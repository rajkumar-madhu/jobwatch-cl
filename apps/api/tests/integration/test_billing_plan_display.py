"""R38 — /api/v1/billing must report the org's real plan even with no `subscriptions` row.

Found during a UI/UX audit: the billing page showed "Current plan: Free" while the SAME page's
usage bar correctly used the Team plan's 500-job limit for the same org. Root cause: `status()`
INNER JOINed organizations to subscriptions — an org with no subscriptions row (this codebase's
own shared `org` fixture makes exactly this shape; no existing test had ever pointed the billing
endpoint at it) got `subscription: None` back, and the frontend's `s?.effective_plan ?? "free"`
silently rendered "Free" while `limits`, joined straight from organizations.plan in a separate
query, kept reporting the org's true plan. A paying org would have been told it was on Free.
"""
import os

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from cronsentinel.main import app
    return TestClient(app)


@pytest.fixture
def api_key(org):
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'it',:p,:h,'admin')"),
                  {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    return raw


def test_billing_reports_the_orgs_real_plan_with_no_subscriptions_row(client, org, api_key):
    # the shared `org` fixture (conftest.py) never inserts into subscriptions — this is already
    # the exact shape that broke, not a specially-contrived edge case.
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='team' WHERE id=:o"), {"o": org["id"]})
        assert s.execute(text("SELECT count(*) FROM subscriptions WHERE org_id=:o"), {"o": org["id"]}).scalar() == 0

    r = client.get("/api/v1/billing", headers={"X-API-Key": api_key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subscription"]["effective_plan"] == "team", body["subscription"]
    assert body["limits"]["max_jobs"] == 500, body["limits"]  # Team's real limit, not Free's 5


def test_billing_still_prefers_a_real_subscriptions_row_when_one_exists(client, org, api_key):
    # the fix must not make a genuine downgrade-in-progress (subscriptions.plan diverging from
    # organizations.plan mid-transition) invisible — COALESCE(s.plan, o.plan) means a present
    # subscriptions.plan still wins.
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='team' WHERE id=:o"), {"o": org["id"]})
        s.execute(text("INSERT INTO subscriptions (org_id, plan, status) VALUES (:o, 'business', 'active')"), {"o": org["id"]})

    r = client.get("/api/v1/billing", headers={"X-API-Key": api_key})
    assert r.status_code == 200, r.text
    assert r.json()["subscription"]["effective_plan"] == "business"
