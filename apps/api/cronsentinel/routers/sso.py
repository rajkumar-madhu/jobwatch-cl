"""R43 — single sign-on administration: one identity provider per org, verified email domains,
and the switch that makes SSO mandatory for everyone except owners.

Security model, in order of the checks a login goes through (routers/auth.py):
  1. The IdP is configured by an org admin and brokered by Keycloak (alias org-<org id>).
  2. A login through that IdP is accepted only for an email in a domain this org has proven it owns
     with a DNS TXT record. An admin cannot use their IdP to sign in as someone else's address.
  3. Such users are added to the org on first login with `default_role` (just-in-time provisioning).
  4. With `enforce` on, members other than owners can use the org only from an SSO session. Owners
     keep password sign-in so a broken IdP cannot lock the organization out.
"""
import re
import secrets
from uuid import UUID

import dns.exception
import dns.resolver
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from .. import keycloak_admin as kc
from .. import netguard
from ..auth import Principal, _inet, audit, require_role
from ..crypto import decrypt_json, encrypt_json
from ..db import tenant_session
from ..schemas import ScimGroupOut, ScimTokensOut, SsoOut

router = APIRouter(prefix="/api/v1/sso", tags=["sso"])

DOMAIN_RE = re.compile(r"(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
# Mailbox providers anyone can sign up to. Verifying one would route every user of it to one org's IdP.
PUBLIC_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com",
               "me.com", "proton.me", "protonmail.com", "aol.com", "gmx.com", "zoho.com", "yandex.com", "mail.com"}
TXT_PREFIX = "_jobwatch-verify"


class OidcIn(BaseModel):
    issuer: str = Field(max_length=500)
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str | None = Field(default=None, max_length=1024)   # omit to keep the stored one


class SamlIn(BaseModel):
    entity_id: str = Field(max_length=500)
    sso_url: str = Field(max_length=1000)
    signing_cert: str = Field(min_length=100, max_length=20000)


class SsoIn(BaseModel):
    protocol: str = Field(pattern="^(oidc|saml)$")
    display_name: str = Field(min_length=1, max_length=80)
    oidc: OidcIn | None = None
    saml: SamlIn | None = None
    enabled: bool = True
    default_role: str = Field(default="viewer", pattern="^(viewer|developer|sre|devops)$")


class DomainIn(BaseModel):
    domain: str = Field(max_length=253)


class EnforceIn(BaseModel):
    enforce: bool


def _out(s, org_id) -> dict:
    row = s.execute(text("SELECT protocol, idp_alias, display_name, config_enc, enabled, enforce, default_role, updated_at FROM org_sso")).first()
    doms = [dict(r._mapping) | {"txt_name": f"{TXT_PREFIX}.{r.domain}", "txt_value": f"jobwatch-verify={r.verification_token}"}
            for r in s.execute(text("SELECT id, domain, verification_token, verified_at FROM org_domains ORDER BY domain")).all()]
    for d in doms:
        d.pop("verification_token")
    if not row:
        return {"configured": False, "domains": doms, "redirect_uri": kc.broker_redirect_uri(kc.alias_for(org_id))}
    cfg = decrypt_json(row.config_enc)
    public = {k: v for k, v in cfg.items() if k not in ("client_secret",)}   # secrets never leave the server
    return {"configured": True, "protocol": row.protocol, "display_name": row.display_name, "enabled": row.enabled,
            "enforce": row.enforce, "default_role": row.default_role, "config": public, "has_client_secret": bool(cfg.get("client_secret")),
            "updated_at": row.updated_at, "domains": doms, "redirect_uri": kc.broker_redirect_uri(row.idp_alias)}


def _discover(issuer: str) -> dict:
    """Fetch the IdP's OIDC discovery document through the SSRF guard: Keycloak will call these
    URLs from inside our network, so a tenant must not be able to point them at internal hosts."""
    if not issuer.startswith("https://"):
        raise HTTPException(400, "issuer must be an https:// URL")
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        netguard.check_url(url)
        with netguard.client(timeout=10) as c:
            r = c.get(url)
        r.raise_for_status()
        d = r.json()
    except netguard.BlockedDestination as e:
        raise HTTPException(400, f"issuer: {e}") from None
    except Exception:
        raise HTTPException(400, "could not read the issuer's /.well-known/openid-configuration") from None
    need = ("authorization_endpoint", "token_endpoint", "jwks_uri")
    if not all(isinstance(d.get(k), str) and d[k].startswith("https://") for k in need):
        raise HTTPException(400, "the issuer's discovery document is missing https endpoints")
    for k in need:
        try:
            netguard.check_url(d[k])
        except netguard.BlockedDestination as e:
            raise HTTPException(400, f"{k}: {e}") from None
    return {"authorization_url": d["authorization_endpoint"], "token_url": d["token_endpoint"], "jwks_url": d["jwks_uri"]}


@router.get("", response_model=SsoOut)
def get_sso(p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        return _out(s, p.org_id)


@router.put("", response_model=SsoOut)
def put_sso(body: SsoIn, request: Request, p: Principal = Depends(require_role("admin"))):
    alias = kc.alias_for(p.org_id)
    with tenant_session(p.org_id) as s:
        feats = s.execute(text("SELECT pl.features FROM organizations o JOIN plan_limits pl ON pl.plan=o.plan WHERE o.id=:o"), {"o": str(p.org_id)}).scalar() or []
        if "sso" not in feats and "all" not in feats:
            raise HTTPException(402, "single sign-on is available on the Business and Enterprise plans")
        prev = s.execute(text("SELECT config_enc, protocol FROM org_sso")).first()
        prev_cfg = decrypt_json(prev.config_enc) if prev else {}
        if body.protocol == "oidc":
            if not body.oidc: raise HTTPException(422, "oidc settings are required")
            secret = body.oidc.client_secret or (prev_cfg.get("client_secret") if prev and prev.protocol == "oidc" else None)
            if not secret: raise HTTPException(422, "client_secret is required")
            cfg = {"issuer": body.oidc.issuer.rstrip("/"), "client_id": body.oidc.client_id, "client_secret": secret, **_discover(body.oidc.issuer)}
        else:
            if not body.saml: raise HTTPException(422, "saml settings are required")
            if not body.saml.sso_url.startswith("https://"): raise HTTPException(400, "sso_url must be https://")
            try:
                netguard.check_url(body.saml.sso_url)
            except netguard.BlockedDestination as e:
                raise HTTPException(400, f"sso_url: {e}") from None
            cert = re.sub(r"-----(BEGIN|END) CERTIFICATE-----|\s", "", body.saml.signing_cert)
            if not re.fullmatch(r"[A-Za-z0-9+/=]{100,}", cert): raise HTTPException(400, "signing_cert: expected a PEM or base64 X.509 certificate")
            cfg = {"entity_id": body.saml.entity_id, "sso_url": body.saml.sso_url, "signing_cert": cert}
        try:
            kc.upsert_idp(alias, body.protocol, body.display_name, cfg, enabled=body.enabled)   # R45: disable, never delete
        except kc.KeycloakAdminError as e:
            raise HTTPException(503 if "not configured" in str(e) else 502, str(e)) from None
        s.execute(text("""INSERT INTO org_sso (org_id, protocol, idp_alias, display_name, config_enc, enabled, default_role)
            VALUES (:o, :p, :a, :d, :c, :e, :r)
            ON CONFLICT (org_id) DO UPDATE SET protocol=EXCLUDED.protocol, display_name=EXCLUDED.display_name, config_enc=EXCLUDED.config_enc,
              enabled=EXCLUDED.enabled, default_role=EXCLUDED.default_role, updated_at=now(),
              enforce = org_sso.enforce AND EXCLUDED.enabled"""),
                  {"o": str(p.org_id), "p": body.protocol, "a": alias, "d": body.display_name, "c": encrypt_json(cfg), "e": body.enabled, "r": body.default_role})
        audit(s, p, "sso.configure", "org_sso", str(p.org_id), {"protocol": body.protocol, "enabled": body.enabled}, _inet(request.client.host if request.client else None))
        return _out(s, p.org_id)


@router.delete("", status_code=204)
def delete_sso(request: Request, p: Principal = Depends(require_role("owner"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("SELECT 1 FROM org_sso")).first():
            raise HTTPException(404, "single sign-on is not configured")
    try:
        kc.delete_idp(kc.alias_for(p.org_id))
    except kc.KeycloakAdminError as e:
        raise HTTPException(502, str(e)) from None
    with tenant_session(p.org_id) as s:
        s.execute(text("DELETE FROM org_sso"))
        audit(s, p, "sso.delete", "org_sso", str(p.org_id), ip=request.client.host if request.client else None)


@router.post("/domains", status_code=201, response_model=SsoOut)
def add_domain(body: DomainIn, request: Request, p: Principal = Depends(require_role("admin"))):
    d = body.domain.strip().lower().rstrip(".")
    if not DOMAIN_RE.fullmatch(d): raise HTTPException(400, "not a valid domain name")
    if d in PUBLIC_MAIL: raise HTTPException(400, "public mailbox domains cannot be claimed")
    with tenant_session(p.org_id) as s:
        r = s.execute(text("INSERT INTO org_domains (org_id, domain, verification_token) VALUES (:o, :d, :t) ON CONFLICT (org_id, domain) DO NOTHING RETURNING id"),
                      {"o": str(p.org_id), "d": d, "t": secrets.token_urlsafe(24)}).first()
        if not r: raise HTTPException(409, "domain already added")
        audit(s, p, "sso.domain_add", "org_domain", str(r.id), {"domain": d}, request.client.host if request.client else None)
        return _out(s, p.org_id)


def _txt_records(name: str) -> list[str]:
    try:
        ans = dns.resolver.resolve(name, "TXT", lifetime=5)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return []
    return [b"".join(r.strings).decode(errors="replace") for r in ans]


@router.post("/domains/{domain_id}/verify", response_model=SsoOut)
def verify_domain(domain_id: UUID, request: Request, p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        row = s.execute(text("SELECT domain, verification_token, verified_at FROM org_domains WHERE id=:i"), {"i": str(domain_id)}).first()
        if not row: raise HTTPException(404)
        if row.verified_at: return _out(s, p.org_id)
        want = f"jobwatch-verify={row.verification_token}"
        if want not in _txt_records(f"{TXT_PREFIX}.{row.domain}"):
            raise HTTPException(409, f"TXT record not found yet: add {TXT_PREFIX}.{row.domain} with value {want} (DNS can take a few minutes)")
        try:
            s.execute(text("UPDATE org_domains SET verified_at=now() WHERE id=:i"), {"i": str(domain_id)})
            s.flush()
        except Exception:
            raise HTTPException(409, "this domain is already verified by another organization") from None
        audit(s, p, "sso.domain_verify", "org_domain", str(domain_id), {"domain": row.domain}, request.client.host if request.client else None)
        return _out(s, p.org_id)


@router.delete("/domains/{domain_id}", status_code=204)
def delete_domain(domain_id: UUID, request: Request, p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        n = s.execute(text("DELETE FROM org_domains WHERE id=:i"), {"i": str(domain_id)}).rowcount
        if not n: raise HTTPException(404)
        audit(s, p, "sso.domain_delete", "org_domain", str(domain_id), ip=request.client.host if request.client else None)


@router.put("/enforce", response_model=SsoOut)
def set_enforce(body: EnforceIn, request: Request, p: Principal = Depends(require_role("owner"))):
    with tenant_session(p.org_id) as s:
        row = s.execute(text("SELECT enabled FROM org_sso")).first()
        if body.enforce:
            if not row or not row.enabled: raise HTTPException(409, "set up and enable single sign-on first")
            if not s.execute(text("SELECT 1 FROM org_domains WHERE verified_at IS NOT NULL")).first():
                raise HTTPException(409, "verify at least one email domain first")
        if not row: raise HTTPException(404)
        s.execute(text("UPDATE org_sso SET enforce=:e, updated_at=now()"), {"e": body.enforce})
        audit(s, p, "sso.enforce", "org_sso", str(p.org_id), {"enforce": body.enforce}, request.client.host if request.client else None)
        return _out(s, p.org_id)


# ---- R44: SCIM tokens ----------------------------------------------------------------------------

class ScimTokenIn(BaseModel):
    name: str = Field(default="Directory sync", min_length=1, max_length=80)


@router.get("/scim-tokens", response_model=ScimTokensOut)
def scim_tokens(p: Principal = Depends(require_role("admin"))):
    from . import scim
    from ..config import settings
    with tenant_session(p.org_id) as s:
        return {"base_url": f"{settings.api_public_url}/scim/v2", "tokens": scim.list_tokens(s)}


@router.post("/scim-tokens", status_code=201)
def create_scim_token(body: ScimTokenIn, request: Request, p: Principal = Depends(require_role("owner"))):
    """Owner-only: a SCIM token can remove every non-owner member of the org."""
    from . import scim
    from ..config import settings
    with tenant_session(p.org_id) as s:
        feats = s.execute(text("SELECT pl.features FROM organizations o JOIN plan_limits pl ON pl.plan=o.plan WHERE o.id=:o"), {"o": str(p.org_id)}).scalar() or []
        if "sso" not in feats and "all" not in feats:
            raise HTTPException(402, "SCIM provisioning is available on the Business and Enterprise plans")
        raw, meta = scim.create_token(s, p.org_id, body.name)
        audit(s, p, "scim.token_create", "scim_token", str(meta["id"]), {"name": body.name}, request.client.host if request.client else None)
    # the token is returned exactly once
    return {**meta, "token": raw, "base_url": f"{settings.api_public_url}/scim/v2"}


@router.delete("/scim-tokens/{token_id}", status_code=204)
def revoke_scim_token(token_id: UUID, request: Request, p: Principal = Depends(require_role("owner"))):
    with tenant_session(p.org_id) as s:
        n = s.execute(text("UPDATE org_scim_tokens SET revoked_at=now() WHERE id=:i AND revoked_at IS NULL"), {"i": str(token_id)}).rowcount
        if not n: raise HTTPException(404)
        audit(s, p, "scim.token_revoke", "scim_token", str(token_id), ip=request.client.host if request.client else None)


# ---- R56: SCIM groups → roles -------------------------------------------------------------------

class GroupRoleIn(BaseModel):
    role: str | None = Field(default=None, pattern="^(viewer|developer|sre|devops|admin)$")


@router.get("/scim-groups", response_model=list[ScimGroupOut])
def scim_groups(p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text("""
            SELECT g.id, g.display_name, g.role, count(m.user_id) AS members, g.updated_at
            FROM scim_groups g LEFT JOIN scim_group_members m ON m.group_id = g.id
            GROUP BY g.id ORDER BY g.display_name""")).all()]


@router.put("/scim-groups/{group_id}/role")
def set_group_role(group_id: UUID, body: GroupRoleIn, request: Request, p: Principal = Depends(require_role("owner"))):
    """Owner-only: a mapping can grant admin to everyone in a directory group."""
    from . import scim
    with tenant_session(p.org_id) as s:
        g = s.execute(text("UPDATE scim_groups SET role=CAST(:r AS member_role), updated_at=now() WHERE id=:g RETURNING id, display_name"),
                      {"r": body.role, "g": str(group_id)}).first()
        if not g: raise HTTPException(404)
        members = [r.user_id for r in s.execute(text("SELECT user_id FROM scim_group_members WHERE group_id=:g"), {"g": str(group_id)}).all()]
        audit(s, p, "scim.group_role", "scim_group", str(group_id), {"group": g.display_name, "role": body.role},
              request.client.host if request.client else None)
    from ..db import system_session
    with system_session() as s:          # recompute reads org_sso and memberships the same way SCIM calls do
        scim.recompute_roles(s, p.org_id, members, principal=p)
    return {"id": str(group_id), "role": body.role, "members_updated": len(members)}
