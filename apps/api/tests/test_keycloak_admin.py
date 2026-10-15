"""R43: Keycloak Admin API client against a mock transport."""
import json

import httpx

from cronsentinel import keycloak_admin as kc
from cronsentinel.config import settings


def test_upsert_creates_then_updates_and_maps_saml_attributes(monkeypatch):
    monkeypatch.setattr(settings, "keycloak_issuer", "https://kc.test/realms/cronsentinel")
    monkeypatch.setattr(settings, "keycloak_admin_client_secret", "x")
    kc._token.clear()
    seen, exists = [], {"v": False}
    def h(req):
        seen.append((req.method, req.url.path))
        if req.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 60})
        if req.method == "GET" and req.url.path.endswith("/mappers"):
            return httpx.Response(200, json=[])
        if req.method == "GET":
            return httpx.Response(200 if exists["v"] else 404, json={})
        return httpx.Response(201 if req.method == "POST" else 204)
    c = httpx.Client(transport=httpx.MockTransport(h))
    cfg = {"entity_id": "urn:idp", "sso_url": "https://idp.test/sso", "signing_cert": "MIIB"}
    kc.upsert_idp("org-1", "saml", "ADFS", cfg, client=c)
    assert ("POST", "/admin/realms/cronsentinel/identity-provider/instances") in seen
    assert sum(1 for m, p in seen if m == "POST" and p.endswith("/mappers")) == 3
    exists["v"] = True; seen.clear()
    kc.upsert_idp("org-1", "saml", "ADFS", cfg, client=c)
    assert ("PUT", "/admin/realms/cronsentinel/identity-provider/instances/org-1") in seen
    assert kc.broker_redirect_uri("org-1") == "https://kc.test/realms/cronsentinel/broker/org-1/endpoint"


def test_oidc_representation_uses_discovered_endpoints_and_pkce():
    rep = kc.idp_representation("org-1", "oidc", "Okta", {"issuer": "https://acme.okta.test", "client_id": "c", "client_secret": "s",
                                 "authorization_url": "https://a", "token_url": "https://t", "jwks_url": "https://k"})
    cfg = rep["config"]
    assert (cfg["authorizationUrl"], cfg["tokenUrl"], cfg["jwksUrl"], cfg["useJwksUrl"]) == ("https://a", "https://t", "https://k", "true")
    assert cfg["pkceEnabled"] == "true" and cfg["validateSignature"] == "true" and rep["trustEmail"] is False  # SSO takeover fix: never auto-link by email
    json.dumps(rep)


def test_disabled_representation_keeps_the_idp():
    rep = kc.idp_representation("org-1", "oidc", "Okta", {"issuer": "https://i", "client_id": "c", "client_secret": "s"}, enabled=False)
    assert rep["enabled"] is False and rep["alias"] == "org-1"
