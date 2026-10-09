"""R43: SSO administration — config, secrets never returned, domain verification, enforcement rules."""
import uuid

import pytest
from sqlalchemy import text


@pytest.fixture
def api(org, monkeypatch):
    from fastapi.testclient import TestClient

    from cronsentinel import keycloak_admin as kc
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    from cronsentinel.routers import sso
    calls = []
    monkeypatch.setattr(kc, "upsert_idp", lambda *a, **k: calls.append(("upsert",) + a + (k.get("enabled", True),)))
    monkeypatch.setattr(kc, "delete_idp", lambda *a, **k: calls.append(("delete",) + a))
    monkeypatch.setattr(sso, "_discover", lambda issuer: {"authorization_url": "https://idp.test/a", "token_url": "https://idp.test/t", "jwks_url": "https://idp.test/k"})
    dns = {}
    monkeypatch.setattr(sso, "_txt_records", lambda name: dns.get(name, []))
    keys = {}
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='business' WHERE id=:o"), {"o": org["id"]})
        for role in ("admin", "owner"):
            prefix, raw = generate_api_key()
            s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,:n,:p,:h,:r)"),
                      {"o": org["id"], "n": role, "p": prefix, "h": hash_secret(raw), "r": role})
            keys[role] = {"X-API-Key": raw}
    yield TestClient(app, raise_server_exceptions=False), keys, calls, dns


OIDC = {"protocol": "oidc", "display_name": "Okta", "oidc": {"issuer": "https://acme.okta.test", "client_id": "cid", "client_secret": "s3cret"}}


def test_configure_oidc_hides_secret_and_keeps_it_on_update(api, org):
    c, k, calls, _ = api
    r = c.put("/api/v1/sso", headers=k["admin"], json=OIDC)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "s3cret" not in r.text and body["has_client_secret"] and body["redirect_uri"].endswith(f"/broker/org-{org['id']}/endpoint")
    assert calls[-1][0] == "upsert" and calls[-1][1] == f"org-{org['id']}" and calls[-1][4]["client_secret"] == "s3cret"
    r = c.put("/api/v1/sso", headers=k["admin"], json={**OIDC, "display_name": "Okta 2", "oidc": {**OIDC["oidc"], "client_secret": None}})
    assert r.status_code == 200 and calls[-1][4]["client_secret"] == "s3cret"


def test_domains_public_mail_rejected_and_verified_by_one_org_only(api, org):
    c, k, _, dns = api
    assert c.post("/api/v1/sso/domains", headers=k["admin"], json={"domain": "gmail.com"}).status_code == 400
    assert c.post("/api/v1/sso/domains", headers=k["admin"], json={"domain": "not a domain"}).status_code == 400
    d = f"acme-{uuid.uuid4().hex[:6]}.test"
    r = c.post("/api/v1/sso/domains", headers=k["admin"], json={"domain": d.upper()})
    dom = [x for x in r.json()["domains"] if x["domain"] == d][0]
    assert dom["txt_name"] == f"_jobwatch-verify.{d}"
    assert c.post(f"/api/v1/sso/domains/{dom['id']}/verify", headers=k["admin"]).status_code == 409   # no record yet
    dns[dom["txt_name"]] = ["unrelated", dom["txt_value"]]
    assert c.post(f"/api/v1/sso/domains/{dom['id']}/verify", headers=k["admin"]).json()["domains"][0]["verified_at"]
    # another org cannot verify the same domain
    from cronsentinel.db import system_session
    other = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:i,'o',:s)"), {"i": other, "s": f"o-{other.hex[:8]}"})
        s.execute(text("INSERT INTO org_domains (org_id, domain, verification_token) VALUES (:o, :d, 't')"), {"o": other, "d": d})
        with pytest.raises(Exception):
            s.execute(text("UPDATE org_domains SET verified_at=now() WHERE org_id=:o"), {"o": other})
    with system_session() as s:
        s.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": other})


def test_enforce_needs_owner_enabled_idp_and_verified_domain(api, org):
    c, k, _, dns = api
    assert c.put("/api/v1/sso/enforce", headers=k["owner"], json={"enforce": True}).status_code == 409   # nothing configured
    c.put("/api/v1/sso", headers=k["admin"], json=OIDC)
    assert c.put("/api/v1/sso/enforce", headers=k["admin"], json={"enforce": True}).status_code == 403   # owner only
    assert c.put("/api/v1/sso/enforce", headers=k["owner"], json={"enforce": True}).status_code == 409   # no verified domain
    d = f"e-{uuid.uuid4().hex[:6]}.test"
    dom = [x for x in c.post("/api/v1/sso/domains", headers=k["admin"], json={"domain": d}).json()["domains"] if x["domain"] == d][0]
    dns[dom["txt_name"]] = [dom["txt_value"]]
    c.post(f"/api/v1/sso/domains/{dom['id']}/verify", headers=k["admin"])
    assert c.put("/api/v1/sso/enforce", headers=k["owner"], json={"enforce": True}).json()["enforce"] is True
    # disabling the IdP switches enforcement off with it, so nobody is locked out
    assert c.put("/api/v1/sso", headers=k["admin"], json={**OIDC, "enabled": False}).json()["enforce"] is False
    # R45: and disabling keeps the IdP in Keycloak (switched off), so users' links survive re-enabling
    _, _, calls, _ = api
    assert calls[-1][0] == "upsert" and calls[-1][-1] is False and not any(c[0] == "delete" for c in calls)


def test_saml_requires_https_and_a_certificate(api, monkeypatch):
    c, k, calls, _ = api
    from cronsentinel.routers import sso
    monkeypatch.setattr(sso.netguard, "check_url", lambda url, **kw: None)   # test hostnames do not resolve
    bad = {"protocol": "saml", "display_name": "ADFS", "saml": {"entity_id": "urn:x", "sso_url": "http://idp/sso", "signing_cert": "A" * 200}}
    assert c.put("/api/v1/sso", headers=k["admin"], json=bad).status_code == 400
    ok = {**bad, "saml": {**bad["saml"], "sso_url": "https://adfs.acme.test/adfs/ls"}}
    r = c.put("/api/v1/sso", headers=k["admin"], json=ok)
    assert r.status_code == 200 and calls[-1][2] == "saml"


def test_sso_needs_business_plan(api, org):
    c, k, _, _ = api
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='team' WHERE id=:o"), {"o": org["id"]})
    assert c.put("/api/v1/sso", headers=k["admin"], json=OIDC).status_code == 402
