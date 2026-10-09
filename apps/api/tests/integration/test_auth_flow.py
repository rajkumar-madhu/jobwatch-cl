"""R7 — the OIDC callback end-to-end against a fake provider (real HTTP, real Redis, real DB).

A real Keycloak is not required to prove the thing that matters: that the callback verifies the
id_token it is handed, binds it to the login attempt, and only then mints a session cookie.
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("REDIS_URL"), reason="needs redis")

PORT = 18077
ISS = f"http://127.0.0.1:{PORT}/realms/jobwatch"
CLIENT = "jobwatch-web"
_state = {"id_token": None}


def _keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    pub_pem = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                             serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    d = jwk.construct(pub_pem, algorithm="RS256").to_dict()
    d = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in d.items()}
    d["kid"] = "k1"
    return pem, {"keys": [d]}


PEM, JWKS = _keypair()


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path.endswith("/.well-known/openid-configuration"):
            return self._json({"issuer": ISS, "authorization_endpoint": f"{ISS}/protocol/openid-connect/auth",
                               "token_endpoint": f"{ISS}/protocol/openid-connect/token",
                               "jwks_uri": f"{ISS}/protocol/openid-connect/certs"})
        if self.path.endswith("/certs"):
            return self._json(JWKS)
        self.send_response(404); self.end_headers()

    def do_POST(self):
        _state["token_form"] = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
        return self._json({"access_token": "at", "id_token": _state["id_token"], "token_type": "Bearer"})

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module", autouse=True)
def provider():
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    yield
    srv.shutdown()


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from cronsentinel import oidc
    from cronsentinel.config import settings
    from cronsentinel.routers import auth as authr
    monkeypatch.setattr(settings, "keycloak_issuer", ISS, raising=False)
    monkeypatch.setattr(settings, "keycloak_client_id", CLIENT, raising=False)
    monkeypatch.setattr(settings, "keycloak_client_secret", "shh", raising=False)
    authr._oidc.clear(); oidc._jwks_cache.clear()
    from cronsentinel.main import app
    return TestClient(app, follow_redirects=False)


def _login(client):
    r = client.get("/auth/login", params={"next": "/jobs"})
    assert r.status_code in (302, 307)
    q = parse_qs(urlparse(r.headers["location"]).query)
    return q["state"][0], q["nonce"][0]


def _id_token(nonce, *, access_token="at", **over):
    # R35: access_token defaults to "at" — the fixed value Handler.do_POST always returns — so
    # every token minted here carries a real, matching at_hash, the way a real Keycloak always
    # does. Before this round the fixture minted at_hash-free tokens, so this whole file passed
    # against the exact code path that 401ed on every real login; pass access_token=None to mint
    # the old at_hash-free shape for a specific test.
    now = int(time.time())
    claims = {"iss": ISS, "aud": CLIENT, "sub": "kc-sub-1", "email": "u@example.test", "email_verified": True, "name": "U",
              "iat": now, "exp": now + 300, "nonce": nonce, **over}
    return jwt.encode(claims, PEM, algorithm="RS256", headers={"kid": "k1"}, access_token=access_token)


def test_login_issues_state_and_nonce(client):
    state, nonce = _login(client)
    assert state and nonce and state != nonce


def test_callback_verifies_token_and_sets_session(client):
    from cronsentinel.db import system_session
    state, nonce = _login(client)
    _state["id_token"] = _id_token(nonce)
    r = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r.status_code in (302, 307), r.text
    assert "cs_session" in r.cookies or any("cs_session" in h for h in r.headers.get_list("set-cookie"))
    with system_session() as s:
        row = s.execute(text("SELECT keycloak_sub FROM users WHERE email='u@example.test'")).first()
        assert row and row.keycloak_sub == "kc-sub-1"
        s.execute(text("DELETE FROM users WHERE email='u@example.test'"))


def test_login_uses_pkce_and_callback_sends_the_matching_verifier(client):
    """A Keycloak client with PKCE required answered every login with
    error=invalid_request "Missing parameter: code_challenge_method"."""
    import base64
    import hashlib
    r = client.get("/auth/login", params={"next": "/jobs"})
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"]
    _state["id_token"] = _id_token(q["nonce"][0])
    assert client.get("/auth/callback", params={"code": "c", "state": q["state"][0]}).status_code in (302, 307)
    verifier = _state["token_form"]["code_verifier"][0]
    assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() == q["code_challenge"][0]
    _cleanup_user()


def test_replayed_state_is_rejected(client):
    """State is single-use: the same callback twice must not mint a second session."""
    state, nonce = _login(client)
    _state["id_token"] = _id_token(nonce)
    assert client.get("/auth/callback", params={"code": "c", "state": state}).status_code in (302, 307)
    r2 = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r2.status_code == 400
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("DELETE FROM users WHERE email='u@example.test'"))


def test_token_with_wrong_nonce_is_rejected(client):
    state, _ = _login(client)
    _state["id_token"] = _id_token("attacker-chosen")
    r = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r.status_code == 401 and "id_token" in r.text


def test_token_signed_by_another_key_is_rejected(client):
    other_pem, _ = _keypair()
    state, nonce = _login(client)
    now = int(time.time())
    _state["id_token"] = jwt.encode({"iss": ISS, "aud": CLIENT, "sub": "x", "email": "e@e.test",
                                     "iat": now, "exp": now + 300, "nonce": nonce}, other_pem,
                                    algorithm="RS256", headers={"kid": "k1"})
    assert client.get("/auth/callback", params={"code": "c", "state": state}).status_code == 401


def test_token_without_email_is_rejected_not_500(client):
    state, nonce = _login(client)
    _state["id_token"] = _id_token(nonce, email=None)
    r = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r.status_code == 401 and "email" in r.text


def test_unknown_state_is_rejected(client):
    assert client.get("/auth/callback", params={"code": "c", "state": "never-issued"}).status_code == 400


def test_callback_rejects_id_token_whose_at_hash_does_not_match_the_returned_access_token(client):
    """R35: this is the actual production bug, exercised through the real route. Before the fix,
    routers/auth.py never passed access_token to verify_id_token at all, so *every* token here
    (which all carry at_hash by default now — see _id_token) would 401 regardless of whether
    at_hash was even correct. This test specifically proves the wiring catches a genuine mismatch
    — the token's at_hash was computed for "not-the-token-we-got-back", but Handler.do_POST always
    hands back access_token="at" — rather than merely proving the happy path still 302s."""
    state, nonce = _login(client)
    _state["id_token"] = _id_token(nonce, access_token="not-the-token-we-got-back")
    r = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r.status_code == 401 and "at_hash" in r.text


def test_callback_still_accepts_an_id_token_with_no_at_hash_at_all(client):
    """Not every IdP (or grant type) returns access_token alongside id_token; a token that never
    had at_hash must keep working exactly as it did before this round."""
    state, nonce = _login(client)
    _state["id_token"] = _id_token(nonce, access_token=None)
    r = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r.status_code in (302, 307), r.text
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("DELETE FROM users WHERE email='u@example.test'"))


# ---- R39: post-login redirect and account linking --------------------------------------------

def _cleanup_user(email="u@example.test"):
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("DELETE FROM users WHERE lower(email)=lower(:e)"), {"e": email})


def test_safe_next_rejects_offsite_targets():
    from cronsentinel.routers.auth import safe_next
    for bad in ("@evil.com", ".evil.com", "//evil.com", "/\\evil.com", "https://evil.com", "evil", "/x\r\nSet-Cookie: a=b", "", None):
        assert safe_next(bad) == "/", bad
    assert safe_next("/jobs?status=failed#x") == "/jobs?status=failed#x"


def test_login_next_cannot_redirect_offsite(client):
    """`next=@evil.com` used to become https://app.example@evil.com after the callback."""
    r = client.get("/auth/login", params={"next": "@evil.com"})
    q = parse_qs(urlparse(r.headers["location"]).query)
    state, nonce = q["state"][0], q["nonce"][0]
    _state["id_token"] = _id_token(nonce)
    from cronsentinel.db import system_session
    with system_session() as s:   # give the user an org so the callback honours `next`
        oid = s.execute(text("INSERT INTO organizations (name, slug) VALUES ('r39', :s) RETURNING id"), {"s": f"r39-{state[:8].lower()}"}).scalar()
    try:
        r = client.get("/auth/callback", params={"code": "c", "state": state})
        assert r.status_code in (302, 307)
        # first login creates the user; no membership → onboarding. Either way the host must be ours.
        from cronsentinel.config import settings
        assert r.headers["location"].startswith(settings.web_public_url + "/"), r.headers["location"]
        assert "evil.com" not in r.headers["location"]
    finally:
        with system_session() as s:
            s.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": oid})
        _cleanup_user()


def test_unverified_email_cannot_sign_in(client):
    state, nonce = _login(client)
    _state["id_token"] = _id_token(nonce, email_verified=False)
    r = client.get("/auth/callback", params={"code": "c", "state": state})
    assert r.status_code == 403
    from cronsentinel.db import system_session
    with system_session() as s:
        assert s.execute(text("SELECT 1 FROM users WHERE email='u@example.test'")).first() is None


def test_same_email_different_identity_is_not_silently_relinked(client):
    """An attacker-controlled IdP identity carrying the victim's email must not take over the row."""
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("INSERT INTO users (keycloak_sub, email, name) VALUES ('victim-sub', 'u@example.test', 'V')"))
    try:
        state, nonce = _login(client)
        _state["id_token"] = _id_token(nonce, sub="attacker-sub")
        r = client.get("/auth/callback", params={"code": "c", "state": state})
        assert r.status_code == 409
        with system_session() as s:
            assert s.execute(text("SELECT keycloak_sub FROM users WHERE email='u@example.test'")).scalar() == "victim-sub"
    finally:
        _cleanup_user()


def test_logout_requires_csrf_when_signed_in(client):
    from cronsentinel.routers.auth import COOKIE, sign_session
    tok = sign_session({"sub": "s1", "uid": "00000000-0000-0000-0000-000000000001", "email": "x@y", "name": None, "org_id": None})
    client.cookies.set(COOKIE, tok)
    assert client.post("/auth/logout").status_code == 403
    from cronsentinel.csrf import issue
    assert client.post("/auth/logout", headers={"X-CSRF-Token": issue("s1")}).status_code == 204


# ---- R43: single sign-on through an org's identity provider ----------------------------------

import uuid as _uuid  # noqa: E402


@pytest.fixture
def sso_org():
    """An org with an enabled IdP and a verified domain sso.example.test; cleaned up after."""
    from cronsentinel.db import system_session
    oid = _uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:i, 'sso', :s)"), {"i": oid, "s": f"sso-{oid.hex[:8]}"})
        s.execute(text("INSERT INTO org_sso (org_id, protocol, idp_alias, display_name, config_enc, enabled, default_role) VALUES (:o,'oidc',:a,'Okta','x',true,'developer')"),
                  {"o": oid, "a": f"org-{oid}"})
        s.execute(text("INSERT INTO org_domains (org_id, domain, verification_token, verified_at) VALUES (:o,'sso.example.test','t',now())"), {"o": oid})
    yield {"id": oid, "alias": f"org-{oid}"}
    with system_session() as s:
        s.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": oid})
        s.execute(text("DELETE FROM users WHERE email LIKE '%@sso.example.test' OR email IN ('u@example.test', 'victim@another-company.test')"))


def _sso_login(client, alias, email, sub="sso-sub-1"):
    r = client.get("/auth/login", params={"idp": alias})
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["kc_idp_hint"] == [alias]
    _state["id_token"] = _id_token(q["nonce"][0], email=email, sub=sub, idp=alias)
    return client.get("/auth/callback", params={"code": "c", "state": q["state"][0]})


def test_sso_entry_routes_by_verified_domain(client, sso_org):
    r = client.get("/auth/sso", params={"email": "alice@sso.example.test"})
    assert r.status_code in (302, 307)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["kc_idp_hint"] == [sso_org["alias"]] and q["login_hint"] == ["alice@sso.example.test"]
    assert client.get("/auth/sso", params={"email": "bob@unknown.example"}).status_code == 404
    assert client.get("/auth/login", params={"idp": "../../evil"}).status_code == 400


def test_sso_login_provisions_membership_just_in_time(client, sso_org):
    r = _sso_login(client, sso_org["alias"], "alice@sso.example.test")
    assert r.status_code in (302, 307), r.text
    from cronsentinel.db import system_session
    with system_session() as s:
        role = s.execute(text("""SELECT m.role FROM memberships m JOIN users u ON u.id=m.user_id
            WHERE u.email='alice@sso.example.test' AND m.org_id=:o"""), {"o": sso_org["id"]}).scalar()
    assert role == "developer"
    sess = client.get("/auth/session").json()
    assert sess["org_id"] == str(sso_org["id"])


def test_org_idp_cannot_vouch_for_addresses_outside_its_verified_domains(client, sso_org):
    """An org admin's IdP asserting someone else's address must not sign them in."""
    r = _sso_login(client, sso_org["alias"], "victim@another-company.test", sub="attacker-sub")
    assert r.status_code == 403
    from cronsentinel.db import system_session
    with system_session() as s:
        assert s.execute(text("SELECT 1 FROM users WHERE email='victim@another-company.test'")).first() is None


def test_enforced_sso_blocks_password_sessions_except_owners(client, sso_org):
    from cronsentinel.db import system_session
    from cronsentinel.routers.auth import COOKIE, sign_session
    with system_session() as s:
        s.execute(text("UPDATE org_sso SET enforce=true WHERE org_id=:o"), {"o": sso_org["id"]})
        uid = s.execute(text("INSERT INTO users (keycloak_sub, email, name) VALUES ('pw-sub','pw@sso.example.test','P') RETURNING id")).scalar()
        s.execute(text("INSERT INTO memberships (user_id, org_id, role) VALUES (:u, :o, 'admin')"), {"u": uid, "o": sso_org["id"]})
    base = {"sub": "pw-sub", "uid": str(uid), "email": "pw@sso.example.test", "name": None, "org_id": str(sso_org["id"])}
    client.cookies.set(COOKIE, sign_session({**base, "idp": None}))
    r = client.get("/api/v1/jobs")
    assert r.status_code == 403 and "single sign-on" in r.json()["detail"]
    client.cookies.set(COOKIE, sign_session({**base, "idp": sso_org["alias"]}))
    assert client.get("/api/v1/jobs").status_code == 200
    with system_session() as s:   # owners keep password access (break-glass)
        s.execute(text("UPDATE memberships SET role='owner' WHERE user_id=:u"), {"u": uid})
    client.cookies.set(COOKIE, sign_session({**base, "idp": None}))
    assert client.get("/api/v1/jobs").status_code == 200


def test_scim_deactivated_user_cannot_rejoin_through_sso(client, sso_org):
    """R44: JIT provisioning must not undo a directory deactivation."""
    from cronsentinel.db import system_session
    with system_session() as s:
        uid = s.execute(text("INSERT INTO users (email) VALUES ('gone@sso.example.test') RETURNING id")).scalar()
        s.execute(text("INSERT INTO scim_users (org_id, user_id, active, role) VALUES (:o, :u, false, 'sre')"), {"o": sso_org["id"], "u": uid})
    r = _sso_login(client, sso_org["alias"], "gone@sso.example.test", sub="gone-sub")
    assert r.status_code == 403
    with system_session() as s:
        assert not s.execute(text("SELECT 1 FROM memberships WHERE org_id=:o AND user_id=:u"), {"o": sso_org["id"], "u": uid}).first()



# ---- R51: sign-up and social providers --------------------------------------------------------

def test_providers_and_social_hints(client, monkeypatch):
    from cronsentinel.config import settings
    monkeypatch.setattr(settings, "auth_social_providers", "google, github,bad alias!")
    p = client.get("/auth/providers").json()
    assert [x["alias"] for x in p["social"]] == ["google", "github"] and p["social"][1]["label"] == "GitHub"
    r = client.get("/auth/login", params={"idp": "github"})
    assert r.status_code in (302, 307) and parse_qs(urlparse(r.headers["location"]).query)["kc_idp_hint"] == ["github"]
    assert client.get("/auth/login", params={"idp": "facebook"}).status_code == 400      # not configured
    monkeypatch.setattr(settings, "auth_social_providers", "")
    assert client.get("/auth/providers").json()["social"] == []
    assert client.get("/auth/login", params={"idp": "google"}).status_code == 400


def test_signup_goes_to_registration_and_lands_on_onboarding(client):
    r = client.get("/auth/signup", params={"email": "new@example.test"})
    assert r.status_code in (302, 307)
    loc = urlparse(r.headers["location"])
    assert loc.path.endswith("/protocol/openid-connect/registrations")
    q = parse_qs(loc.query)
    assert q["login_hint"] == ["new@example.test"] and q["client_id"] and q["nonce"] and q["state"]
    import json as _json
    from cronsentinel.routers.auth import _r as redis_
    assert _json.loads(redis_.get(f"oauth:{q['state'][0]}"))["next"] == "/onboarding"
