"""Live SSO + SCIM checks against a real Keycloak (see README.md). Run from the repo root with the
API's environment (DATABASE_URL, KEYCLOAK_*, API_PUBLIC_URL …) and the API listening on API_PUBLIC_URL.
Exits 1 if any check fails. Creates and then deletes its own test orgs."""
import asyncio
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "apps", "api"))

import httpx  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402
from sqlalchemy import text  # noqa: E402

from cronsentinel import keycloak_admin as kc  # noqa: E402
from cronsentinel.config import settings  # noqa: E402
from cronsentinel.crypto import encrypt_json  # noqa: E402
from cronsentinel.db import system_session  # noqa: E402
from cronsentinel.routers import scim  # noqa: E402

API = settings.api_public_url.rstrip("/")
KC = settings.keycloak_issuer.rsplit("/realms/", 1)[0]
IDP = f"{KC}/realms/acme-idp"
CHROME = os.getenv("CHROMIUM_PATH")  # optional; default is Playwright's own Chromium
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))


def make_org(protocol: str, domain: str, role: str) -> tuple[uuid.UUID, str]:
    oid = uuid.uuid4()
    alias = kc.alias_for(oid)
    if protocol == "oidc":
        d = httpx.get(f"{IDP}/.well-known/openid-configuration").json()
        cfg = {"issuer": d["issuer"], "client_id": "jobwatch-broker", "client_secret": "broker-secret-123",
               "authorization_url": d["authorization_endpoint"], "token_url": d["token_endpoint"], "jwks_url": d["jwks_uri"]}
    else:
        desc = httpx.get(f"{IDP}/protocol/saml/descriptor").text
        cert = re.search(r"<ds:X509Certificate>([^<]+)</ds:X509Certificate>", desc).group(1)
        cfg = {"entity_id": IDP, "sso_url": f"{IDP}/protocol/saml", "signing_cert": cert}
    kc.upsert_idp(alias, protocol, f"SSO e2e {protocol}", cfg)
    with system_session() as s:
        s.execute(text("DELETE FROM org_domains WHERE domain=:d"), {"d": domain})   # leftovers from an aborted run
        s.execute(text("INSERT INTO organizations (id, name, slug, plan) VALUES (:i, :n, :s, 'business')"),
                  {"i": oid, "n": f"SSO e2e {protocol}", "s": f"sso-e2e-{oid.hex[:8]}"})
        s.execute(text("INSERT INTO org_sso (org_id, protocol, idp_alias, display_name, config_enc, enabled, default_role) VALUES (:o,:p,:a,:d,:c,true,:r)"),
                  {"o": oid, "p": protocol, "a": alias, "d": f"SSO e2e {protocol}", "c": encrypt_json(cfg), "r": role})
        s.execute(text("INSERT INTO org_domains (org_id, domain, verification_token, verified_at) VALUES (:o,:d,'t',now())"), {"o": oid, "d": domain})
    return oid, alias


async def login(browser, user: str, pw: str, email: str) -> dict:
    ctx = await browser.new_context()
    pg = await ctx.new_page()
    out: dict = {}

    async def on_resp(r):
        if "/auth/callback" in r.url:
            out["callback"] = r.status
            try:
                out["body"] = (await r.text())[:200]
            except Exception:
                pass
    pg.on("response", on_resp)
    await pg.goto(f"{API}/auth/sso?email={email}", wait_until="domcontentloaded")
    await pg.fill("#username", user)
    await pg.fill("#password", pw)
    await pg.click("#kc-login")
    await pg.wait_for_timeout(2500)
    if await pg.locator("#kc-update-profile-form, form#kc-idp-review-profile-form").count():
        await pg.click("input[type=submit], button[type=submit]")
        await pg.wait_for_timeout(2500)
    r = await ctx.request.get(f"{API}/auth/session")
    out["session"] = await r.json() if r.status == 200 else None
    await ctx.close()
    return out


async def main() -> int:
    created = []
    try:
        oidc_org, _ = make_org("oidc", "acme.test", "developer"); created.append(oidc_org)
        saml_org, _ = make_org("saml", "acme-saml.test", "viewer"); created.append(saml_org)
        check("Keycloak Admin API accepted OIDC and SAML identity providers", True)
        async with async_playwright() as p:
            b = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"]) if CHROME else await p.chromium.launch()

            a = await login(b, "alice", "Alice-pass-1", "alice@acme.test")
            s = a.get("session") or {}
            check("OIDC sign-in lands in the org with the default role",
                  s.get("org_id") == str(oidc_org) and s.get("orgs", [{}])[0].get("role") == "developer", str(a)[:200])

            # R45: disable then re-enable SSO; a returning user must still get in (federated link kept)
            d = httpx.get(f"{IDP}/.well-known/openid-configuration").json()
            cfg = {"issuer": d["issuer"], "client_id": "jobwatch-broker", "client_secret": "broker-secret-123",
                   "authorization_url": d["authorization_endpoint"], "token_url": d["token_endpoint"], "jwks_url": d["jwks_uri"]}
            kc.upsert_idp(kc.alias_for(oidc_org), "oidc", "SSO e2e oidc", cfg, enabled=False)
            kc.upsert_idp(kc.alias_for(oidc_org), "oidc", "SSO e2e oidc", cfg, enabled=True)
            back = await login(b, "alice", "Alice-pass-1", "alice@acme.test")
            check("Returning user signs in after SSO was disabled and re-enabled",
                  (back.get("session") or {}).get("user", {}).get("email") == "alice@acme.test", str(back)[:200])

            m = await login(b, "mallory", "Mallory-pass-1", "alice@acme.test")
            check("IdP asserting an unverified-domain email is refused", m.get("callback") == 403 and not m.get("session"), str(m)[:200])

            with system_session() as db:
                raw, _ = scim.create_token(db, oidc_org, "sso-e2e")
            H = {"Authorization": f"Bearer {raw}"}
            r = httpx.post(f"{API}/scim/v2/Users", headers=H, json={"userName": "bob@acme.test", "externalId": "e2e-bob", "active": True})
            bob = r.json().get("id")
            check("SCIM provisions a user", r.status_code == 201, r.text[:200])
            with system_session() as db:
                db.execute(text("UPDATE memberships SET role='sre' WHERE org_id=:o AND user_id=:u"), {"o": oidc_org, "u": bob})
            first = await login(b, "bob", "Bob-pass-1", "bob@acme.test")
            fs = first.get("session") or {}
            check("First SSO sign-in links to the SCIM-provisioned user and keeps its role",
                  fs.get("user", {}).get("id") == bob and fs.get("orgs", [{}])[0].get("role") == "sre", str(first)[:200])
            r = httpx.patch(f"{API}/scim/v2/Users/{bob}", headers=H,
                            json={"Operations": [{"op": "Replace", "path": "active", "value": "False"}]})
            again = await login(b, "bob", "Bob-pass-1", "bob@acme.test")
            check("After SCIM deactivation, SSO sign-in is refused", r.status_code == 200 and again.get("callback") == 403, str(again)[:200])

            c = await login(b, "carol", "Carol-pass-1", "carol@acme-saml.test")
            cs = c.get("session") or {}
            check("SAML sign-in (signed assertions, mapped attributes) lands in the org",
                  cs.get("org_id") == str(saml_org) and cs.get("user", {}).get("email") == "carol@acme-saml.test", str(c)[:200])
            await b.close()
    finally:
        for oid in created:
            try:
                kc.delete_idp(kc.alias_for(oid))
            except Exception as e:  # leave a note, keep cleaning
                print("cleanup: could not delete IdP", oid, e)
        with system_session() as s:
            for oid in created:
                s.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": oid})
            s.execute(text("DELETE FROM users WHERE email IN ('alice@acme.test','bob@acme.test','carol@acme-saml.test','victim@other-corp.test')"))
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
