"""R64: job create/rotate responses carry the full ping URL (Terraform's sensitive ping_url)."""
import pytest
from sqlalchemy import text


@pytest.fixture
def api(org, monkeypatch):
    from fastapi.testclient import TestClient

    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.config import settings
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    monkeypatch.setattr(settings, "ingest_public_url", "https://ingest.acme.io/")
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'d',:p,:h,'devops')"), {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    return TestClient(app), {"X-API-Key": raw}


def test_ping_url_only_alongside_the_token(api, org):
    c, h = api
    j = c.post("/api/v1/jobs", headers=h, json={"workspace_id": str(org["ws"]), "name": "tf-job"}).json()
    assert j["ping_url"] == f"https://ingest.acme.io/ping/{j['heartbeat_token']}"
    again = c.get(f"/api/v1/jobs/{j['id']}", headers=h).json()
    assert again["heartbeat_token"] is None and again["ping_url"] is None          # never shown again
    rot = c.post(f"/api/v1/jobs/{j['id']}/rotate-token", headers=h).json()
    assert rot["ping_url"].endswith(rot["heartbeat_token"]) and rot["ping_url"] != j["ping_url"]
