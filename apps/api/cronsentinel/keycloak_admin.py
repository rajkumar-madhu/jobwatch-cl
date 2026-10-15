"""R43 — the slice of the Keycloak Admin REST API that single sign-on needs.

JobWatch authenticates with a dedicated confidential client (`jobwatch-admin`, service account with
realm-management roles manage-identity-providers + view-identity-providers only — see the realm
export). It never uses an admin user's password.

Each organization's IdP is a Keycloak identity provider with alias `org-<org uuid>`; users reach it
through `kc_idp_hint`, and the `idp` claim on our client (a user-session-note mapper for
`identity_provider`) tells the callback which IdP a login came through.
"""
import time
from urllib.parse import urlsplit

import httpx

from .config import settings

_token: dict = {}


class KeycloakAdminError(RuntimeError):
    pass


def _base() -> tuple[str, str]:
    """(server base URL, realm) derived from the issuer: https://kc/realms/<realm>."""
    iss = settings.keycloak_issuer.rstrip("/")
    root, _, realm = iss.rpartition("/realms/")
    if not realm:
        raise KeycloakAdminError("KEYCLOAK_ISSUER must look like https://host/realms/<realm>")
    return root, realm


def _client() -> httpx.Client:
    return httpx.Client(timeout=15)


def _access_token(c: httpx.Client) -> str:
    if _token.get("exp", 0) > time.monotonic() + 30:
        return _token["v"]
    if not settings.keycloak_admin_client_secret:
        raise KeycloakAdminError("single sign-on is not configured on this server (KEYCLOAK_ADMIN_CLIENT_SECRET)")
    root, realm = _base()
    r = c.post(f"{root}/realms/{realm}/protocol/openid-connect/token",
               data={"grant_type": "client_credentials", "client_id": settings.keycloak_admin_client_id,
                     "client_secret": settings.keycloak_admin_client_secret})
    if r.status_code != 200:
        raise KeycloakAdminError("could not authenticate to Keycloak as the admin client")
    j = r.json()
    _token.update(v=j["access_token"], exp=time.monotonic() + int(j.get("expires_in", 60)))
    return _token["v"]


def alias_for(org_id) -> str:
    return f"org-{org_id}"


def idp_representation(alias: str, protocol: str, display_name: str, cfg: dict, enabled: bool = True) -> dict:
    """Keycloak IdentityProviderRepresentation.

    trustEmail must stay off. Org admins control their own IdP, so it can assert any address, e.g.
    victim@othercorp.com. With trustEmail on, the first-broker-login flow auto-links that assertion to
    the victim's existing realm account before routers/auth.py gets to check the org's verified
    domains — a cross-tenant account takeover. With it off, linking to an existing account requires
    that account's password or a link sent to its mailbox, which the attacker has neither of."""
    common = {"alias": alias, "displayName": display_name, "enabled": enabled, "trustEmail": False,
              "storeToken": False, "linkOnly": False,
              "firstBrokerLoginFlowAlias": settings.keycloak_sso_first_login_flow or "first broker login"}
    if protocol == "oidc":
        iss = cfg["issuer"].rstrip("/")
        return {**common, "providerId": "oidc", "config": {
            "issuer": iss,
            "authorizationUrl": cfg.get("authorization_url") or f"{iss}/protocol/openid-connect/auth",
            "tokenUrl": cfg.get("token_url") or f"{iss}/protocol/openid-connect/token",
            "jwksUrl": cfg.get("jwks_url") or "", "useJwksUrl": "true" if cfg.get("jwks_url") else "false",
            "validateSignature": "true", "clientId": cfg["client_id"], "clientSecret": cfg["client_secret"],
            "clientAuthMethod": "client_secret_post", "defaultScope": "openid email profile",
            "syncMode": "FORCE", "pkceEnabled": "true", "pkceMethod": "S256"}}
    return {**common, "providerId": "saml", "config": {
        "entityId": f"{settings.keycloak_issuer.rstrip('/')}", "idpEntityId": cfg["entity_id"],
        "singleSignOnServiceUrl": cfg["sso_url"], "signingCertificate": cfg["signing_cert"],
        "validateSignature": "true", "wantAssertionsSigned": "true", "postBindingResponse": "true",
        "postBindingAuthnRequest": "true", "nameIDPolicyFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        "principalType": "SUBJECT", "syncMode": "FORCE"}}


def _mappers(alias: str, protocol: str) -> list[dict]:
    if protocol != "saml":
        return []   # OIDC brokers map email/name from standard claims by default
    attr = lambda name, attribute, user_attr: {  # noqa: E731
        "name": name, "identityProviderAlias": alias, "identityProviderMapper": "saml-user-attribute-idp-mapper",
        "config": {"syncMode": "FORCE", "attribute.name": attribute, "user.attribute": user_attr}}
    return [attr("email", "email", "email"), attr("first name", "firstName", "firstName"), attr("last name", "lastName", "lastName")]


def upsert_idp(alias: str, protocol: str, display_name: str, cfg: dict, *, enabled: bool = True,
               client: httpx.Client | None = None) -> None:
    """Create or update. Disabling goes through here with enabled=False — never delete_idp(): deleting
    an identity provider also deletes every user's federated link to it, and after re-enabling,
    returning SSO-only users would hit Keycloak's "confirm account link" page with no password to
    confirm with (found by tools/sso-e2e against a live Keycloak, R45)."""
    root, realm = _base()
    c = client or _client()
    h = {"Authorization": f"Bearer {_access_token(c)}"}
    base = f"{root}/admin/realms/{realm}/identity-provider/instances"
    rep = idp_representation(alias, protocol, display_name, cfg, enabled)
    r = c.get(f"{base}/{alias}", headers=h)
    if r.status_code == 200:
        r = c.put(f"{base}/{alias}", json=rep, headers=h)
    elif r.status_code == 404:
        r = c.post(base, json=rep, headers=h)
    if r.status_code not in (200, 201, 204):
        raise KeycloakAdminError(f"Keycloak rejected the identity provider ({r.status_code})")
    existing = {m["name"] for m in (c.get(f"{base}/{alias}/mappers", headers=h).json() or [])}
    for m in _mappers(alias, protocol):
        if m["name"] not in existing:
            c.post(f"{base}/{alias}/mappers", json=m, headers=h)


def delete_idp(alias: str, *, client: httpx.Client | None = None) -> None:
    root, realm = _base()
    c = client or _client()
    r = c.delete(f"{root}/admin/realms/{realm}/identity-provider/instances/{alias}",
                 headers={"Authorization": f"Bearer {_access_token(c)}"})
    if r.status_code not in (204, 404):
        raise KeycloakAdminError(f"Keycloak could not remove the identity provider ({r.status_code})")


def broker_redirect_uri(alias: str) -> str:
    """What the customer registers in their IdP (OIDC redirect URI / SAML ACS URL)."""
    root, realm = _base()
    return f"{root}/realms/{realm}/broker/{alias}/endpoint"


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""
