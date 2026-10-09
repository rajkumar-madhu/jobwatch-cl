"""R44 — SCIM 2.0 (RFC 7643/7644) user provisioning, per organization.

Identity providers (Okta, Microsoft Entra ID, OneLogin, JumpCloud…) call /scim/v2 with an org's
SCIM token to add people, update them, and — the reason SCIM matters — take access away the moment
someone leaves the company.

Rules, all enforced here:
  * Only addresses in the org's **verified domains** can be provisioned (same rule as SSO login).
  * **Owners are never changed by SCIM** — not deactivated, not deleted. A misconfigured directory
    sync must not be able to remove the people who can fix it.
  * Deactivation deletes the membership, so every session, bearer token and SSO login loses the org
    on the very next request; the scim_users row remembers the role for reactivation.
  * Users are global (one person can be in several orgs); SCIM only ever touches this org's
    membership, never the user row's identity, so one org cannot affect another.

Groups are not supported (empty list, 501 on writes); assign roles in JobWatch.
"""
import json
import re
from uuid import UUID

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from .. import ratelimit
from ..auth import Principal, audit, hash_secret, verify_secret
from ..config import settings
from ..db import system_session

# Not part of the web API schema: this is the RFC 7644 protocol surface for identity providers.
router = APIRouter(prefix="/scim/v2", tags=["scim"], include_in_schema=False)

MEDIA = "application/scim+json"
USER = "urn:ietf:params:scim:schemas:core:2.0:User"
LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
MAX_PAGE = 200
TOKEN_RATE_PER_MIN = 600


class ScimError(Exception):
    def __init__(self, status: int, detail: str, scim_type: str | None = None):
        self.status, self.detail, self.scim_type = status, detail, scim_type


def scim_error_response(_req: Request, e: ScimError) -> JSONResponse:
    body = {"schemas": [ERROR], "status": str(e.status), "detail": e.detail}
    if e.scim_type:
        body["scimType"] = e.scim_type
    return JSONResponse(body, status_code=e.status, media_type=MEDIA)


def _ok(body, status: int = 200) -> JSONResponse:
    return JSONResponse(json.loads(json.dumps(body, default=str)), status_code=status, media_type=MEDIA)


# ---- auth -----------------------------------------------------------------------------------------

def generate_token() -> tuple[str, str]:
    import secrets
    prefix = "jwscim_" + secrets.token_hex(4)
    return prefix, f"{prefix}.{secrets.token_urlsafe(32)}"


def _org_for(authorization: str | None, request: Request) -> tuple[UUID, UUID]:
    """(org_id, token_id) for a valid, unrevoked SCIM token on a plan that includes SSO."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ScimError(401, "missing bearer token")
    raw = authorization[7:].strip()
    if len(raw) > 200 or "." not in raw:
        raise ScimError(401, "invalid token")
    with system_session() as s:
        rows = s.execute(text("""SELECT t.id, t.org_id, t.token_hash, pl.features FROM org_scim_tokens t
            JOIN organizations o ON o.id = t.org_id JOIN plan_limits pl ON pl.plan = o.plan
            WHERE t.prefix = :p AND t.revoked_at IS NULL"""), {"p": raw.split(".", 1)[0]}).all()
        row = next((r for r in rows if verify_secret(raw, r.token_hash)), None)
        if not row:
            raise ScimError(401, "invalid token")
        if "sso" not in row.features and "all" not in row.features:
            raise ScimError(403, "SCIM provisioning is available on the Business and Enterprise plans")
        s.execute(text("UPDATE org_scim_tokens SET last_used_at = now() WHERE id = :i"), {"i": row.id})
    try:
        ratelimit.check(f"scim:{row.id}", TOKEN_RATE_PER_MIN, request)
    except Exception:
        raise ScimError(429, "too many requests") from None
    return row.org_id, row.id


def _actor(org_id, token_id) -> Principal:
    return Principal(org_id=org_id, role="admin", api_key_id=token_id)


# ---- representation ---------------------------------------------------------------------------

def _resource(r) -> dict:
    name = {k: v for k, v in (("givenName", r.given_name), ("familyName", r.family_name)) if v}
    if r.name:
        name["formatted"] = r.name
    return {
        "schemas": [USER], "id": str(r.user_id), "externalId": r.external_id, "userName": r.email,
        "name": name or None, "displayName": r.name or r.email, "active": bool(r.active),
        "emails": [{"value": r.email, "type": "work", "primary": True}],
        "meta": {"resourceType": "User", "created": r.created_at, "lastModified": r.updated_at,
                 "location": f"{settings.api_public_url}/scim/v2/Users/{r.user_id}"},
    }


_SELECT = """SELECT su.user_id, su.external_id, su.active, su.role, su.given_name, su.family_name, su.created_at, su.updated_at,
                    u.email, u.name FROM scim_users su JOIN users u ON u.id = su.user_id WHERE su.org_id = :o"""


def _get(s, org_id, uid) -> object:
    try:
        uid = str(UUID(str(uid)))
    except ValueError:
        raise ScimError(404, "user not found") from None
    r = s.execute(text(_SELECT + " AND su.user_id = :u"), {"o": org_id, "u": uid}).first()
    if not r:
        raise ScimError(404, "user not found")
    return r


def _email_of(body: dict) -> str:
    email = body.get("userName")
    if not email:
        emails = body.get("emails") or []
        prim = [e for e in emails if isinstance(e, dict) and e.get("primary")] or emails
        email = prim[0].get("value") if prim and isinstance(prim[0], dict) else None
    if not isinstance(email, str) or not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,253}", email.strip()):
        raise ScimError(400, "userName must be the person's email address", "invalidValue")
    return email.strip().lower()


def _domain_verified(s, org_id, email: str) -> bool:
    return bool(s.execute(text("SELECT 1 FROM org_domains WHERE org_id=:o AND domain=:d AND verified_at IS NOT NULL"),
                          {"o": org_id, "d": email.rsplit("@", 1)[1]}).first())


def _is_owner(s, org_id, uid) -> bool:
    return s.execute(text("SELECT role FROM memberships WHERE org_id=:o AND user_id=:u"), {"o": org_id, "u": uid}).scalar() == "owner"


def _set_active(s, org_id, uid, active: bool) -> None:
    """The only place SCIM changes access. Membership is the access record; scim_users remembers the role."""
    if active:
        role = s.execute(text("SELECT role FROM scim_users WHERE org_id=:o AND user_id=:u"), {"o": org_id, "u": uid}).scalar()
        s.execute(text("INSERT INTO memberships (user_id, org_id, role) VALUES (:u, :o, :r) ON CONFLICT DO NOTHING"),
                  {"u": uid, "o": org_id, "r": role or "viewer"})
    else:
        role = s.execute(text("DELETE FROM memberships WHERE org_id=:o AND user_id=:u RETURNING role"), {"o": org_id, "u": uid}).scalar()
        if role:
            s.execute(text("UPDATE scim_users SET role=:r WHERE org_id=:o AND user_id=:u"), {"r": role, "o": org_id, "u": uid})
    s.execute(text("UPDATE scim_users SET active=:a, updated_at=now() WHERE org_id=:o AND user_id=:u"), {"a": active, "o": org_id, "u": uid})


def _as_bool(v) -> bool:
    """Entra ID sends "False"/"True" strings in PATCH values."""
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return bool(v)


# ---- discovery --------------------------------------------------------------------------------

@router.get("/ServiceProviderConfig")
def service_provider_config():
    return _ok({"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
                "patch": {"supported": True}, "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
                "filter": {"supported": True, "maxResults": MAX_PAGE}, "changePassword": {"supported": False},
                "sort": {"supported": False}, "etag": {"supported": False},
                "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer token", "primary": True,
                                           "description": "Per-organization SCIM token from Settings → Single sign-on"}]})


@router.get("/ResourceTypes")
def resource_types():
    rt = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
    return _ok({"schemas": [LIST], "totalResults": 2, "Resources": [
        {"schemas": [rt], "id": "User", "name": "User", "endpoint": "/Users", "schema": USER},
        {"schemas": [rt], "id": "Group", "name": "Group", "endpoint": "/Groups", "schema": GROUP}]})


@router.get("/Schemas")
def schemas():
    attrs = [{"name": n, "type": t, "multiValued": m, "required": req, "mutability": mut, "returned": "default", "uniqueness": u}
             for n, t, m, req, mut, u in (("userName", "string", False, True, "immutable", "server"),
                                          ("externalId", "string", False, False, "readWrite", "none"),
                                          ("active", "boolean", False, False, "readWrite", "none"),
                                          ("name", "complex", False, False, "readWrite", "none"),
                                          ("emails", "complex", True, False, "readWrite", "none"))]
    gattrs = [{"name": "displayName", "type": "string", "multiValued": False, "required": True, "mutability": "readWrite", "returned": "default", "uniqueness": "server"},
              {"name": "externalId", "type": "string", "multiValued": False, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
              {"name": "members", "type": "complex", "multiValued": True, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"}]
    return _ok({"schemas": [LIST], "totalResults": 2, "Resources": [{"id": USER, "name": "User", "attributes": attrs},
                                                                    {"id": GROUP, "name": "Group", "attributes": gattrs}]})


# ---- Users ------------------------------------------------------------------------------------

_FILTER = re.compile(r'^\s*(userName|externalId|emails(?:\.value)?)\s+eq\s+"([^"]*)"\s*$', re.I)


@router.get("/Users")
def list_users(request: Request, filter: str | None = None, startIndex: int = 1, count: int = 100,  # noqa: A002
               authorization: str | None = Header(None)):
    org_id, _ = _org_for(authorization, request)
    start, count = max(1, startIndex), max(0, min(count, MAX_PAGE))
    where, params = "", {"o": org_id}
    if filter:
        m = _FILTER.match(filter)
        if not m:
            raise ScimError(400, 'only `userName eq "…"`, `externalId eq "…"` and `emails eq "…"` filters are supported', "invalidFilter")
        attr, val = m.group(1).lower(), m.group(2)
        where = " AND su.external_id = :v" if attr == "externalid" else " AND lower(u.email) = lower(:v)"
        params["v"] = val
    with system_session() as s:
        total = s.execute(text(f"SELECT count(*) FROM ({_SELECT}{where}) x"), params).scalar()
        rows = s.execute(text(f"{_SELECT}{where} ORDER BY su.created_at LIMIT :c OFFSET :off"), {**params, "c": count, "off": start - 1}).all()
    return _ok({"schemas": [LIST], "totalResults": total, "startIndex": start, "itemsPerPage": len(rows),
                "Resources": [_resource(r) for r in rows]})


@router.get("/Users/{uid}")
def get_user(uid: str, request: Request, authorization: str | None = Header(None)):
    org_id, _ = _org_for(authorization, request)
    with system_session() as s:
        return _ok(_resource(_get(s, org_id, uid)))


@router.post("/Users", status_code=201)
async def create_user(request: Request, authorization: str | None = Header(None)):
    org_id, tok = _org_for(authorization, request)
    body = await _json(request)
    email = _email_of(body)
    name = body.get("name") or {}
    given, family = (name.get("givenName"), name.get("familyName")) if isinstance(name, dict) else (None, None)
    display = body.get("displayName") or (name.get("formatted") if isinstance(name, dict) else None) or " ".join(x for x in (given, family) if x) or None
    active = _as_bool(body.get("active", True))
    with system_session() as s:
        if not _domain_verified(s, org_id, email):
            raise ScimError(400, "this email's domain is not verified for the organization (Settings → Single sign-on)", "invalidValue")
        uid = s.execute(text("SELECT id FROM users WHERE lower(email)=lower(:e)"), {"e": email}).scalar()
        if uid and s.execute(text("SELECT 1 FROM scim_users WHERE org_id=:o AND user_id=:u"), {"o": org_id, "u": uid}).first():
            raise ScimError(409, "a user with this userName already exists", "uniqueness")
        if not uid:
            # No keycloak_sub yet: the person's first SSO login links to this row by email (routers/auth.py).
            uid = s.execute(text("INSERT INTO users (email, name) VALUES (:e, :n) RETURNING id"), {"e": email, "n": display}).scalar()
        default_role = s.execute(text("SELECT default_role FROM org_sso WHERE org_id=:o"), {"o": org_id}).scalar() or "viewer"
        existing_role = s.execute(text("SELECT role FROM memberships WHERE org_id=:o AND user_id=:u"), {"o": org_id, "u": uid}).scalar()
        try:
            s.execute(text("""INSERT INTO scim_users (org_id, user_id, external_id, active, role, given_name, family_name)
                VALUES (:o, :u, :x, :a, :r, :g, :f)"""), {"o": org_id, "u": uid, "x": body.get("externalId"), "a": active,
                                                         "r": existing_role or default_role, "g": given, "f": family})
            s.flush()
        except Exception:
            raise ScimError(409, "externalId is already used by another user", "uniqueness") from None
        if existing_role != "owner":
            _set_active(s, org_id, uid, active)
        audit(s, _actor(org_id, tok), "scim.user_create", "user", str(uid), {"email": email, "active": active})
        r = _get(s, org_id, uid)
    resp = _ok(_resource(r), 201)
    resp.headers["Location"] = f"{settings.api_public_url}/scim/v2/Users/{uid}"
    return resp


@router.put("/Users/{uid}")
async def replace_user(uid: str, request: Request, authorization: str | None = Header(None)):
    org_id, tok = _org_for(authorization, request)
    body = await _json(request)
    with system_session() as s:
        r = _get(s, org_id, uid)
        if body.get("userName") and body["userName"].strip().lower() != r.email.lower():
            raise ScimError(400, "userName (the email address) cannot be changed; create a new user instead", "mutability")
        name = body.get("name") or {}
        s.execute(text("""UPDATE scim_users SET external_id=:x, given_name=:g, family_name=:f, updated_at=now()
            WHERE org_id=:o AND user_id=:u"""), {"x": body.get("externalId", r.external_id), "g": name.get("givenName"),
                                                 "f": name.get("familyName"), "o": org_id, "u": r.user_id})
        _apply_active(s, org_id, r.user_id, _as_bool(body.get("active", True)), tok)
        return _ok(_resource(_get(s, org_id, r.user_id)))


@router.patch("/Users/{uid}")
async def patch_user(uid: str, request: Request, authorization: str | None = Header(None)):
    org_id, tok = _org_for(authorization, request)
    body = await _json(request)
    ops = body.get("Operations")
    if not isinstance(ops, list):
        raise ScimError(400, "PatchOp with an Operations list expected", "invalidSyntax")
    with system_session() as s:
        r = _get(s, org_id, uid)
        for op in ops:
            kind = str(op.get("op", "")).lower()
            if kind not in ("replace", "add", "remove"):
                raise ScimError(400, f"unsupported op {op.get('op')!r}", "invalidSyntax")
            path, val = op.get("path"), op.get("value")
            # Okta: {"op":"replace","value":{"active":false}}; Entra: {"op":"Replace","path":"active","value":"False"}
            changes = {path: val} if path else (val if isinstance(val, dict) else {})
            for k, v in changes.items():
                key = str(k).lower()
                if key == "active":
                    _apply_active(s, org_id, r.user_id, _as_bool(v), tok)
                elif key == "externalid":
                    s.execute(text("UPDATE scim_users SET external_id=:x, updated_at=now() WHERE org_id=:o AND user_id=:u"),
                              {"x": None if kind == "remove" else v, "o": org_id, "u": r.user_id})
                elif key in ("name.givenname", "name.familyname"):
                    col = "given_name" if key.endswith("givenname") else "family_name"
                    s.execute(text(f"UPDATE scim_users SET {col}=:v, updated_at=now() WHERE org_id=:o AND user_id=:u"),
                              {"v": None if kind == "remove" else v, "o": org_id, "u": r.user_id})
                elif key == "name" and isinstance(v, dict):
                    s.execute(text("UPDATE scim_users SET given_name=:g, family_name=:f, updated_at=now() WHERE org_id=:o AND user_id=:u"),
                              {"g": v.get("givenName"), "f": v.get("familyName"), "o": org_id, "u": r.user_id})
                elif key == "username" and isinstance(v, str) and v.strip().lower() != r.email.lower():
                    raise ScimError(400, "userName (the email address) cannot be changed", "mutability")
                # other attributes (displayName, emails, title, …) are accepted and ignored
        return _ok(_resource(_get(s, org_id, r.user_id)))


@router.delete("/Users/{uid}", status_code=204)
def delete_user(uid: str, request: Request, authorization: str | None = Header(None)):
    org_id, tok = _org_for(authorization, request)
    with system_session() as s:
        r = _get(s, org_id, uid)
        if _is_owner(s, org_id, r.user_id):
            raise ScimError(403, "owners are managed in JobWatch, not by provisioning")
        s.execute(text("DELETE FROM memberships WHERE org_id=:o AND user_id=:u"), {"o": org_id, "u": r.user_id})
        s.execute(text("DELETE FROM scim_users WHERE org_id=:o AND user_id=:u"), {"o": org_id, "u": r.user_id})
        s.execute(text("DELETE FROM scim_group_members WHERE org_id=:o AND user_id=:u"), {"o": org_id, "u": r.user_id})   # R56
        audit(s, _actor(org_id, tok), "scim.user_delete", "user", str(r.user_id), {"email": r.email})
    return Response(status_code=204)


def _apply_active(s, org_id, uid, active: bool, tok) -> None:
    current = s.execute(text("SELECT active FROM scim_users WHERE org_id=:o AND user_id=:u"), {"o": org_id, "u": uid}).scalar()
    if current == active:
        return
    if not active and _is_owner(s, org_id, uid):
        raise ScimError(403, "owners are managed in JobWatch, not by provisioning")
    _set_active(s, org_id, uid, active)
    audit(s, _actor(org_id, tok), "scim.user_activate" if active else "scim.user_deactivate", "user", str(uid))


# ---- Groups (R56) -------------------------------------------------------------------------------
# Groups come from the directory; an org owner maps a group to a JobWatch role in Settings. A person's
# role is the highest role among their mapped groups. Owners are never changed, deactivated people keep
# no access, and a person in no mapped group keeps (or, if a group set it, reverts to) the default role.

def recompute_roles(s, org_id, user_ids, tok=None, principal: Principal | None = None) -> None:
    """Re-derive roles for these SCIM-provisioned people from their mapped groups. The audit actor is the
    SCIM token (directory push) or the owner who changed a mapping (`principal`)."""
    actor = principal or (_actor(org_id, tok) if tok else None)
    from ..auth import ROLE_ORDER
    default = s.execute(text("SELECT default_role FROM org_sso WHERE org_id=:o"), {"o": org_id}).scalar() or "viewer"
    for uid in {str(u) for u in user_ids}:
        su = s.execute(text("SELECT active, role, role_from_group FROM scim_users WHERE org_id=:o AND user_id=:u"), {"o": org_id, "u": uid}).first()
        if not su:
            continue
        current = s.execute(text("SELECT role FROM memberships WHERE org_id=:o AND user_id=:u"), {"o": org_id, "u": uid}).scalar()
        if current == "owner":
            continue                                        # owners are managed in JobWatch only
        mapped = [r for (r,) in s.execute(text("""SELECT g.role FROM scim_group_members m JOIN scim_groups g ON g.id = m.group_id
            WHERE m.org_id=:o AND m.user_id=:u AND g.role IS NOT NULL"""), {"o": org_id, "u": uid}).all()]
        if mapped:
            target, from_group = max(mapped, key=ROLE_ORDER.index), True
        elif su.role_from_group:
            target, from_group = default, False             # left every mapped group
        else:
            continue                                        # role was set by hand or by default: leave it
        before = current or su.role
        s.execute(text("UPDATE scim_users SET role=:r, role_from_group=:g, updated_at=now() WHERE org_id=:o AND user_id=:u"),
                  {"r": target, "g": from_group, "o": org_id, "u": uid})
        if su.active and current and current != target:
            s.execute(text("UPDATE memberships SET role=:r WHERE org_id=:o AND user_id=:u"), {"r": target, "o": org_id, "u": uid})
        if before != target and actor is not None:
            audit(s, actor, "scim.role_sync", "user", uid, {"from": before, "to": target, "active": su.active})


def _group_row(s, org_id, gid):
    try:
        gid = str(UUID(str(gid)))
    except ValueError:
        raise ScimError(404, "group not found") from None
    g = s.execute(text("SELECT id, display_name, external_id, role, created_at, updated_at FROM scim_groups WHERE org_id=:o AND id=:g"),
                  {"o": org_id, "g": gid}).first()
    if not g:
        raise ScimError(404, "group not found")
    return g


def _group_resource(s, org_id, g, with_members=True) -> dict:
    out = {"schemas": [GROUP], "id": str(g.id), "displayName": g.display_name, "externalId": g.external_id,
           "meta": {"resourceType": "Group", "created": g.created_at, "lastModified": g.updated_at,
                    "location": f"{settings.api_public_url}/scim/v2/Groups/{g.id}"}}
    if with_members:
        out["members"] = [{"value": str(r.user_id), "display": r.email} for r in s.execute(text(
            "SELECT m.user_id, u.email FROM scim_group_members m JOIN users u ON u.id = m.user_id WHERE m.org_id=:o AND m.group_id=:g ORDER BY u.email"),
            {"o": org_id, "g": g.id}).all()]
    return out


def _member_ids(s, org_id, value) -> set[str]:
    """SCIM member objects → ids of people provisioned to this org (others are ignored)."""
    ids = set()
    for m in value if isinstance(value, list) else [value]:
        v = m.get("value") if isinstance(m, dict) else m
        try:
            ids.add(str(UUID(str(v))))
        except (ValueError, TypeError):
            continue
    if not ids:
        return set()
    rows = s.execute(text("SELECT user_id FROM scim_users WHERE org_id=:o AND user_id = ANY(CAST(:ids AS uuid[]))"),
                     {"o": org_id, "ids": list(ids)}).all()
    return {str(r.user_id) for r in rows}


def _set_members(s, org_id, gid, add: set[str] = frozenset(), remove: set[str] = frozenset(), replace: set[str] | None = None) -> set[str]:
    """Apply a membership change; returns everyone whose role may change."""
    current = {str(r.user_id) for r in s.execute(text("SELECT user_id FROM scim_group_members WHERE org_id=:o AND group_id=:g"),
                                                    {"o": org_id, "g": gid}).all()}
    target = set(replace) if replace is not None else (current | set(add)) - set(remove)
    for uid in target - current:
        s.execute(text("INSERT INTO scim_group_members (org_id, group_id, user_id) VALUES (:o, :g, :u) ON CONFLICT DO NOTHING"),
                  {"o": org_id, "g": gid, "u": uid})
    for uid in current - target:
        s.execute(text("DELETE FROM scim_group_members WHERE org_id=:o AND group_id=:g AND user_id=:u"), {"o": org_id, "g": gid, "u": uid})
    s.execute(text("UPDATE scim_groups SET updated_at=now() WHERE id=:g"), {"g": gid})
    return target ^ current


_GFILTER = re.compile(r'^\s*(displayName|externalId)\s+eq\s+"([^"]*)"\s*$', re.I)


@router.get("/Groups")
def list_groups(request: Request, filter: str | None = None, startIndex: int = 1, count: int = 100,  # noqa: A002
                excludedAttributes: str | None = None, authorization: str | None = Header(None)):  # noqa: N803
    org_id, _ = _org_for(authorization, request)
    start, count = max(1, startIndex), max(0, min(count, MAX_PAGE))
    where, params = "", {"o": org_id}
    if filter:
        m = _GFILTER.match(filter)
        if not m:
            raise ScimError(400, 'only `displayName eq "…"` and `externalId eq "…"` filters are supported', "invalidFilter")
        where = " AND external_id = :v" if m.group(1).lower() == "externalid" else " AND lower(display_name) = lower(:v)"
        params["v"] = m.group(2)
    members = "members" not in (excludedAttributes or "")
    with system_session() as s:
        total = s.execute(text(f"SELECT count(*) FROM scim_groups WHERE org_id=:o{where}"), params).scalar()
        rows = s.execute(text(f"SELECT id, display_name, external_id, role, created_at, updated_at FROM scim_groups WHERE org_id=:o{where} "
                              "ORDER BY created_at LIMIT :c OFFSET :off"), {**params, "c": count, "off": start - 1}).all()
        res = [_group_resource(s, org_id, g, members) for g in rows]
    return _ok({"schemas": [LIST], "totalResults": total, "startIndex": start, "itemsPerPage": len(res), "Resources": res})


@router.get("/Groups/{gid}")
def get_group(gid: str, request: Request, excludedAttributes: str | None = None, authorization: str | None = Header(None)):  # noqa: N803
    org_id, _ = _org_for(authorization, request)
    with system_session() as s:
        return _ok(_group_resource(s, org_id, _group_row(s, org_id, gid), "members" not in (excludedAttributes or "")))


@router.post("/Groups", status_code=201)
async def create_group(request: Request, authorization: str | None = Header(None)):
    org_id, tok = _org_for(authorization, request)
    body = await _json(request)
    name = str(body.get("displayName") or "").strip()[:200]
    if not name:
        raise ScimError(400, "displayName is required", "invalidValue")
    with system_session() as s:
        if s.execute(text("SELECT 1 FROM scim_groups WHERE org_id=:o AND lower(display_name)=lower(:n)"), {"o": org_id, "n": name}).first():
            raise ScimError(409, "a group with this displayName already exists", "uniqueness")
        gid = s.execute(text("INSERT INTO scim_groups (org_id, display_name, external_id) VALUES (:o, :n, :x) RETURNING id"),
                        {"o": org_id, "n": name, "x": body.get("externalId")}).scalar()
        changed = _set_members(s, org_id, gid, add=_member_ids(s, org_id, body.get("members") or []))
        recompute_roles(s, org_id, changed, tok)
        audit(s, _actor(org_id, tok), "scim.group_create", "scim_group", str(gid), {"name": name})
        out = _group_resource(s, org_id, _group_row(s, org_id, gid))
    resp = _ok(out, 201)
    resp.headers["Location"] = out["meta"]["location"]
    return resp


@router.put("/Groups/{gid}")
async def replace_group(gid: str, request: Request, authorization: str | None = Header(None)):
    org_id, tok = _org_for(authorization, request)
    body = await _json(request)
    with system_session() as s:
        g = _group_row(s, org_id, gid)
        name = str(body.get("displayName") or g.display_name).strip()[:200]
        s.execute(text("UPDATE scim_groups SET display_name=:n, external_id=:x WHERE id=:g"), {"n": name, "x": body.get("externalId", g.external_id), "g": g.id})
        changed = _set_members(s, org_id, g.id, replace=_member_ids(s, org_id, body.get("members") or []))
        recompute_roles(s, org_id, changed, tok)
        return _ok(_group_resource(s, org_id, _group_row(s, org_id, g.id)))


_MEMBER_PATH = re.compile(r'^members\[value eq "([^"]+)"\]$', re.I)


@router.patch("/Groups/{gid}")
async def patch_group(gid: str, request: Request, authorization: str | None = Header(None)):
    """Okta and Entra ID speak different PatchOp dialects; both are handled:
       Okta   {"op":"add","path":"members","value":[{"value":id}]}, {"op":"remove","path":"members[value eq \"id\"]"},
              {"op":"replace","value":{"id":…,"displayName":…}}
       Entra  {"op":"Add","path":"members","value":[{"value":id}]}, {"op":"Remove","path":"members","value":[{"value":id}]},
              {"op":"Replace","path":"displayName","value":"…"}"""
    org_id, tok = _org_for(authorization, request)
    body = await _json(request)
    ops = body.get("Operations")
    if not isinstance(ops, list):
        raise ScimError(400, "PatchOp with an Operations list expected", "invalidSyntax")
    with system_session() as s:
        g = _group_row(s, org_id, gid)
        changed: set[str] = set()
        for op in ops:
            kind = str(op.get("op", "")).lower()
            path = str(op.get("path") or "")
            val = op.get("value")
            m = _MEMBER_PATH.match(path)
            if kind not in ("add", "remove", "replace"):
                raise ScimError(400, f"unsupported op {op.get('op')!r}", "invalidSyntax")
            if path.lower() == "members" or m:
                if kind == "add":
                    changed |= _set_members(s, org_id, g.id, add=_member_ids(s, org_id, val or []))
                elif kind == "remove":
                    who = _member_ids(s, org_id, [m.group(1)]) if m else (_member_ids(s, org_id, val) if val else None)
                    changed |= _set_members(s, org_id, g.id, replace=set()) if who is None else _set_members(s, org_id, g.id, remove=who)
                else:
                    changed |= _set_members(s, org_id, g.id, replace=_member_ids(s, org_id, val or []))
            elif path.lower() in ("displayname", "externalid") or (not path and isinstance(val, dict)):
                vals = {path.lower(): val} if path else {k.lower(): v for k, v in val.items()}
                if "displayname" in vals and str(vals["displayname"]).strip():
                    s.execute(text("UPDATE scim_groups SET display_name=:n, updated_at=now() WHERE id=:g"), {"n": str(vals["displayname"]).strip()[:200], "g": g.id})
                if "externalid" in vals:
                    s.execute(text("UPDATE scim_groups SET external_id=:x, updated_at=now() WHERE id=:g"), {"x": vals["externalid"], "g": g.id})
                if not path and isinstance(val, dict) and "members" in vals:
                    changed |= _set_members(s, org_id, g.id, replace=_member_ids(s, org_id, vals["members"] or []))
            # other attributes are accepted and ignored
        recompute_roles(s, org_id, changed, tok)
        return _ok(_group_resource(s, org_id, _group_row(s, org_id, g.id)))


@router.delete("/Groups/{gid}", status_code=204)
def delete_group(gid: str, request: Request, authorization: str | None = Header(None)):
    org_id, tok = _org_for(authorization, request)
    with system_session() as s:
        g = _group_row(s, org_id, gid)
        members = {str(r.user_id) for r in s.execute(text("SELECT user_id FROM scim_group_members WHERE group_id=:g"), {"g": g.id}).all()}
        s.execute(text("DELETE FROM scim_groups WHERE id=:g"), {"g": g.id})
        recompute_roles(s, org_id, members, tok)
        audit(s, _actor(org_id, tok), "scim.group_delete", "scim_group", str(g.id), {"name": g.display_name})
    return Response(status_code=204)


async def _json(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise ScimError(413, "request too large")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise ScimError(400, "body is not JSON", "invalidSyntax") from None
    if not isinstance(body, dict):
        raise ScimError(400, "body must be a JSON object", "invalidSyntax")
    return body


# ---- token admin (used by routers/sso.py) ---------------------------------------------------------

def create_token(s, org_id, name: str) -> tuple[str, dict]:
    prefix, raw = generate_token()
    r = s.execute(text("INSERT INTO org_scim_tokens (org_id, name, prefix, token_hash) VALUES (:o, :n, :p, :h) RETURNING id, name, prefix, created_at"),
                  {"o": str(org_id), "n": name, "p": prefix, "h": hash_secret(raw)}).first()
    return raw, dict(r._mapping)


def list_tokens(s) -> list[dict]:
    return [dict(r._mapping) for r in s.execute(text(
        "SELECT id, name, prefix, created_at, last_used_at FROM org_scim_tokens WHERE revoked_at IS NULL ORDER BY created_at")).all()]


