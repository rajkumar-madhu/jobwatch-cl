"""R44: SCIM 2.0 provisioning — Okta- and Entra-shaped requests against the real API and DB."""
import uuid

import pytest
from sqlalchemy import text

USER = "urn:ietf:params:scim:schemas:core:2.0:User"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


@pytest.fixture
def scim(org):
    """Business-plan org with a verified domain, an owner API key, and a SCIM token minted through the API."""
    from fastapi.testclient import TestClient

    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    dom = f"corp-{uuid.uuid4().hex[:6]}.test"
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='business' WHERE id=:o"), {"o": org["id"]})
        s.execute(text("INSERT INTO org_domains (org_id, domain, verification_token, verified_at) VALUES (:o, :d, 't', now())"), {"o": org["id"], "d": dom})
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'owner',:p,:h,'owner')"), {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    c = TestClient(app, raise_server_exceptions=False)
    tok = c.post("/api/v1/sso/scim-tokens", headers={"X-API-Key": raw}, json={"name": "Okta"})
    assert tok.status_code == 201, tok.text
    yield c, {"Authorization": f"Bearer {tok.json()['token']}"}, dom, {"X-API-Key": raw}
    with system_session() as s:
        s.execute(text("DELETE FROM users WHERE email LIKE :d"), {"d": f"%@{dom}"})


def _member_role(org_id, email):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("SELECT m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.org_id=:o AND u.email=:e"),
                         {"o": org_id, "e": email}).scalar()


def test_discovery_and_auth(scim):
    c, h, _, _ = scim
    assert c.get("/scim/v2/ServiceProviderConfig").json()["patch"]["supported"] is True
    r = c.get("/scim/v2/Users", headers={"Authorization": "Bearer jwscim_00000000.nope"})
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/scim+json")
    assert r.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert c.get("/scim/v2/Groups", headers=h).json()["totalResults"] == 0


def test_okta_lifecycle_create_find_deactivate_reactivate(scim, org):
    c, h, dom, _ = scim
    email = f"ana@{dom}"
    r = c.post("/scim/v2/Users", headers=h, json={"schemas": [USER], "userName": email, "externalId": "00u1",
                                                  "name": {"givenName": "Ana", "familyName": "Ray"}, "active": True,
                                                  "emails": [{"value": email, "primary": True, "type": "work"}]})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert r.headers["location"].endswith(f"/scim/v2/Users/{uid}")
    assert _member_role(org["id"], email) == "viewer"
    # Okta looks users up by userName before creating
    found = c.get("/scim/v2/Users", headers=h, params={"filter": f'userName eq "{email.upper()}"'}).json()
    assert found["totalResults"] == 1 and found["Resources"][0]["id"] == uid
    assert c.post("/scim/v2/Users", headers=h, json={"userName": email}).status_code == 409
    # promote in JobWatch, then deactivate (Okta: value object) — membership gone, role remembered
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("UPDATE memberships SET role='sre' WHERE org_id=:o AND user_id=:u"), {"o": org["id"], "u": uid})
    r = c.patch(f"/scim/v2/Users/{uid}", headers=h, json={"schemas": [PATCH], "Operations": [{"op": "replace", "value": {"active": False}}]})
    assert r.status_code == 200 and r.json()["active"] is False
    assert _member_role(org["id"], email) is None
    r = c.patch(f"/scim/v2/Users/{uid}", headers=h, json={"schemas": [PATCH], "Operations": [{"op": "replace", "value": {"active": True}}]})
    assert r.json()["active"] is True and _member_role(org["id"], email) == "sre"


def test_entra_string_booleans_and_path_ops(scim, org):
    c, h, dom, _ = scim
    email = f"raj@{dom}"
    uid = c.post("/scim/v2/Users", headers=h, json={"userName": email, "externalId": "e-1", "active": "True"}).json()["id"]
    r = c.patch(f"/scim/v2/Users/{uid}", headers=h, json={"schemas": [PATCH], "Operations": [
        {"op": "Replace", "path": "active", "value": "False"}, {"op": "Replace", "path": "name.givenName", "value": "Raj"}]})
    assert r.status_code == 200 and r.json()["active"] is False and r.json()["name"]["givenName"] == "Raj"
    assert _member_role(org["id"], email) is None
    assert c.get("/scim/v2/Users", headers=h, params={"filter": 'externalId eq "e-1"'}).json()["totalResults"] == 1


def test_deactivation_ends_access_immediately(scim, org):
    """A deactivated person's existing session loses the org on the next request."""
    c, h, dom, _ = scim
    email = f"leaver@{dom}"
    uid = c.post("/scim/v2/Users", headers=h, json={"userName": email}).json()["id"]
    from cronsentinel.routers.auth import COOKIE, sign_session
    c.cookies.set(COOKIE, sign_session({"sub": "s-leaver", "uid": uid, "email": email, "name": None, "org_id": str(org["id"]), "idp": None}))
    assert c.get("/api/v1/jobs").status_code == 200
    c.patch(f"/scim/v2/Users/{uid}", headers=h, json={"Operations": [{"op": "replace", "path": "active", "value": False}]})
    assert c.get("/api/v1/jobs").status_code == 403
    c.cookies.clear()


def test_unverified_domain_owner_protection_and_delete(scim, org):
    c, h, dom, _ = scim
    assert c.post("/scim/v2/Users", headers=h, json={"userName": "someone@not-their-domain.test"}).status_code == 400
    # an owner known to the directory cannot be deactivated or deleted through SCIM
    from cronsentinel.db import system_session
    email = f"boss@{dom}"
    with system_session() as s:
        boss = s.execute(text("INSERT INTO users (email) VALUES (:e) RETURNING id"), {"e": email}).scalar()
        s.execute(text("INSERT INTO memberships (user_id, org_id, role) VALUES (:u, :o, 'owner')"), {"u": boss, "o": org["id"]})
    uid = c.post("/scim/v2/Users", headers=h, json={"userName": email}).json()["id"]
    assert c.patch(f"/scim/v2/Users/{uid}", headers=h, json={"Operations": [{"op": "replace", "path": "active", "value": False}]}).status_code == 403
    assert c.delete(f"/scim/v2/Users/{uid}", headers=h).status_code == 403
    assert _member_role(org["id"], email) == "owner"
    # a normal user is deleted from this org only
    v = c.post("/scim/v2/Users", headers=h, json={"userName": f"temp@{dom}"}).json()["id"]
    assert c.delete(f"/scim/v2/Users/{v}", headers=h).status_code == 204
    assert c.get(f"/scim/v2/Users/{v}", headers=h).status_code == 404


def test_tokens_are_owner_only_scoped_to_org_and_revocable(scim, org):
    c, h, _, owner = scim
    toks = c.get("/api/v1/sso/scim-tokens", headers=owner).json()
    assert toks["base_url"].endswith("/scim/v2") and all("token" not in t for t in toks["tokens"])
    tid = toks["tokens"][0]["id"]
    assert c.delete(f"/api/v1/sso/scim-tokens/{tid}", headers=owner).status_code == 204
    assert c.get("/scim/v2/Users", headers=h).status_code == 401


def test_scim_needs_business_plan(scim, org):
    c, h, _, _ = scim
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='team' WHERE id=:o"), {"o": org["id"]})
    assert c.get("/scim/v2/Users", headers=h).status_code == 403
