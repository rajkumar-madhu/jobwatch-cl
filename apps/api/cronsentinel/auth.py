"""Auth: Keycloak OIDC bearer (users) or X-API-Key (machines). RBAC via role ordering."""
import ipaddress
import secrets
from dataclasses import dataclass
from uuid import UUID

import httpx
import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Header, HTTPException, Request
from jose import jwt
from sqlalchemy import text

from . import ratelimit
from .config import settings
from .db import system_session

ROLE_ORDER = ["viewer", "developer", "sre", "devops", "admin", "owner"]
_ph = PasswordHasher()
_jwks_cache: dict = {}


@dataclass
class Principal:
    org_id: UUID
    role: str
    user_id: UUID | None = None
    api_key_id: UUID | None = None

    def at_least(self, role: str) -> bool:
        return ROLE_ORDER.index(self.role) >= ROLE_ORDER.index(role)


# R21: machine secrets (API keys, agent keys) are hashed with SHA-256, not argon2.
#
# Argon2 exists to make low-entropy *human* passwords expensive to guess. These secrets are
# server-generated with secrets.token_urlsafe(32) — 256 bits — so brute force is infeasible
# whatever the hash, and the ~180 ms argon2 verify bought nothing while running on every request:
# every API-key call, and every agent heartbeat (agent_principal cached the hash, not the result).
# That capped a core at ~5 agent requests/s — the 10k-job load mix needs ~36/s — and let anyone
# who knew a key's (non-secret) prefix burn 180 ms of CPU per request, before the rate limiter ran.
#
# No pepper on purpose: a peppered hash would tie every key to SECRET_ENCRYPTION_KEY, so rotating
# the root secret would silently revoke every API and agent key.
#
# NEVER use these for passwords. There are none today (users authenticate through Keycloak); if a
# password ever appears, it needs argon2, which is why _ph stays for the legacy path below.
_FAST = "sha256$"


def hash_secret(raw: str) -> str:
    return _FAST + hashlib.sha256(raw.encode()).hexdigest()


def verify_secret(raw: str, hashed: str) -> bool:
    if hashed.startswith(_FAST):
        return hmac.compare_digest(hashed, hash_secret(raw))
    try:  # pre-R21 argon2 hash; callers upgrade it via needs_rehash()
        return _ph.verify(hashed, raw)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def needs_rehash(hashed: str) -> bool:
    return not hashed.startswith(_FAST)


def generate_api_key() -> tuple[str, str]:
    prefix = "cs_" + secrets.token_hex(4)
    return prefix, f"{prefix}.{secrets.token_urlsafe(32)}"


JWT_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "ES256"]   # asymmetric only; never HS*/none
JWKS_TTL_S = 3600


def _jwks(force: bool = False):
    """Cached JWKS. Refetched hourly and on demand: the old cache never expired, so after Keycloak
    rotated its signing key every bearer token failed until the process restarted."""
    import time
    if force or "keys" not in _jwks_cache or time.monotonic() - _jwks_cache.get("at", 0) > JWKS_TTL_S:
        oidc = httpx.get(f"{settings.keycloak_issuer}/.well-known/openid-configuration", timeout=5).json()
        _jwks_cache["keys"] = httpx.get(oidc["jwks_uri"], timeout=5).json()
        _jwks_cache["at"] = time.monotonic()
    return _jwks_cache["keys"]


def _decode_bearer(token: str, keys) -> dict:
    # algorithms pinned: python-jose otherwise trusts the token header's own `alg`.
    return jwt.decode(token, keys, algorithms=JWT_ALGORITHMS, audience=settings.keycloak_audience,
                      issuer=settings.keycloak_issuer)


def _principal_from_jwt(token: str, org_id: UUID) -> Principal:
    try:
        try:
            claims = _decode_bearer(token, _jwks())
        except jwt.JWTError:
            claims = _decode_bearer(token, _jwks(force=True))   # one refetch: key may have rotated
    except Exception:
        # The exception text (key ids, claim values, library internals) is not for the caller.
        raise HTTPException(401, "invalid token") from None
    with system_session() as s:
        row = s.execute(text(
            "SELECT u.id, m.role, os.enforce AND os.enabled AS enforce, os.idp_alias FROM users u JOIN memberships m ON m.user_id=u.id "
            "LEFT JOIN org_sso os ON os.org_id = m.org_id "
            "WHERE u.keycloak_sub=:sub AND m.org_id=:org"), {"sub": claims["sub"], "org": str(org_id)}).first()
    if not row:
        raise HTTPException(403, "not a member of this organization")
    from .routers.auth import sso_required   # R43: bearer tokens carry the same `idp` claim
    if sso_required(row.role, row.enforce, row.idp_alias, claims.get("idp")):
        raise HTTPException(403, "this organization requires single sign-on")
    return Principal(org_id=org_id, role=row.role, user_id=row.id)


def _principal_from_api_key(raw: str) -> Principal:
    if len(raw) > 200:
        raise HTTPException(401, "invalid api key")
    prefix = raw.split(".", 1)[0]
    with system_session() as s:
        # The prefix is 32 bits: with enough keys two can share one, and .first() then checked the
        # wrong row and rejected a valid key. Check every live candidate.
        rows = s.execute(text(
            "SELECT id, org_id, key_hash, role FROM api_keys WHERE prefix=:p AND revoked_at IS NULL"), {"p": prefix}).all()
        row = next((r for r in rows if verify_secret(raw, r.key_hash)), None)
        if not row or row.role not in ROLE_ORDER:
            raise HTTPException(401, "invalid api key")
        # Upgrade a pre-R21 argon2 hash in place on first successful use.
        new_hash = hash_secret(raw) if needs_rehash(row.key_hash) else None
        s.execute(text("UPDATE api_keys SET last_used_at=now(), key_hash=COALESCE(:h, key_hash) WHERE id=:id"), {"id": row.id, "h": new_hash})
    return Principal(org_id=row.org_id, role=row.role, api_key_id=row.id)


def current_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
) -> Principal:
    if x_api_key:
        p = _principal_from_api_key(x_api_key)
        ratelimit.check(f"key:{p.api_key_id}", settings.api_rate_per_min, request)
        return p
    sess = request.cookies.get("cs_session")
    if sess:
        from .routers.auth import read_session
        sd = read_session(request)
        if not sd: raise HTTPException(401, "session expired")
        if not sd.get("org_id"): raise HTTPException(403, "no organization yet — complete onboarding")
        with system_session() as s:
            m = s.execute(text("""SELECT m.role, os.enforce AND os.enabled AS enforce, os.idp_alias FROM memberships m
                LEFT JOIN org_sso os ON os.org_id = m.org_id WHERE m.user_id=:u AND m.org_id=:o"""), {"u": sd["uid"], "o": sd["org_id"]}).first()
        if not m: raise HTTPException(403, "not a member")
        role = m.role
        # R43: turning enforcement on also ends existing password sessions for non-owners
        from .routers.auth import sso_required
        if sso_required(m.role, m.enforce, m.idp_alias, sd.get("idp")):
            raise HTTPException(403, "this organization requires single sign-on: sign in with SSO")
        # R8: cookie auth is the only browser-drivable path, so it is the only one that needs CSRF.
        from . import csrf
        csrf.enforce(request, sd["sub"])
        p = Principal(org_id=UUID(sd["org_id"]), role=role, user_id=UUID(sd["uid"]))
        ratelimit.check(f"user:{p.user_id}", settings.api_rate_per_min, request)
        return p
    if authorization and authorization.lower().startswith("bearer "):
        if not x_org_id:
            raise HTTPException(400, "X-Org-Id header required")
        try:
            org = UUID(x_org_id)
        except ValueError:
            raise HTTPException(400, "X-Org-Id must be a UUID") from None
        p = _principal_from_jwt(authorization[7:], org)
        ratelimit.check(f"user:{p.user_id}", settings.api_rate_per_min, request)
        return p
    raise HTTPException(401, "authentication required")


def require_role(role: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.at_least(role):
            raise HTTPException(403, f"requires role >= {role}")
        return p
    return dep


def _inet(ip: str | None) -> str | None:
    """audit_logs.ip is `inet`. Starlette's request.client.host is not always an address — it is
    "testclient" under TestClient, and can be a hostname or a unix-socket path depending on the
    server and proxy in front. Feeding that straight in raised InvalidTextRepresentation and
    failed the *whole write* the audit entry was attached to, so a logging concern could 500 a
    job creation. Store NULL rather than lose the operation."""
    if not ip:
        return None
    try:
        return str(ipaddress.ip_address(ip.strip().strip("[]").split("%")[0]))
    except ValueError:
        return None


def audit(s, p: Principal, action: str, target_type: str, target_id: str, payload: dict | None = None, ip: str | None = None):
    s.execute(text(
        "INSERT INTO audit_logs (org_id, actor_id, actor_type, action, target_type, target_id, ip, payload) "
        "VALUES (:org, :actor, :atype, :action, :tt, :tid, :ip, CAST(:payload AS jsonb))"),
        {"org": str(p.org_id), "actor": str(p.user_id or p.api_key_id), "atype": "user" if p.user_id else "api_key",
         "action": action, "tt": target_type, "tid": target_id, "ip": _inet(ip), "payload": __import__("json").dumps(payload or {})})
