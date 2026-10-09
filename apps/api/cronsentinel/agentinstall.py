"""R62 — serve the Linux agent installer from the ingest host.

`GET https://<ingest>/agent/install.sh` returns agents/linux-agent/packaging/install.sh with its three
default lines filled in: where the agent binaries live (AGENT_DOWNLOAD_BASE), which release
(AGENT_VERSION) and which server to enroll with (INGEST_PUBLIC_URL). The install command in the app is
then real: `curl -fsSL https://<ingest>/agent/install.sh | sudo bash -s -- --token csb_…`.

The script runs as root, so every value written into it is validated against a strict pattern first;
anything else and the endpoint refuses (503) instead of serving a script. The installer still verifies
the binaries against the release's SHA256SUMS before installing anything.
"""
import re
from functools import lru_cache
from pathlib import Path

from .config import settings

_URL = re.compile(r"https://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_DEFAULTS = re.compile(r'^DEFAULT_(SERVER|VERSION|BASE)="[^"\n]*"$', re.M)


def _script_path() -> Path:
    if settings.agent_install_script:
        return Path(settings.agent_install_script)
    # repository layout: apps/api/cronsentinel → repo root
    return Path(__file__).resolve().parents[3] / "agents" / "linux-agent" / "packaging" / "install.sh"


@lru_cache(maxsize=1)
def _template() -> str:
    t = _script_path().read_text()
    if len(_DEFAULTS.findall(t)) != 3:
        raise RuntimeError("install.sh has no defaults block")
    return t


def problem() -> str | None:
    """Why the installer cannot be served, or None when it can."""
    base, ver, server = settings.agent_download_base.rstrip("/"), settings.agent_version, settings.ingest_public_url.rstrip("/")
    if not base or not ver:
        return "agent downloads are not configured (AGENT_DOWNLOAD_BASE and AGENT_VERSION)"
    if not _URL.fullmatch(base):
        return "AGENT_DOWNLOAD_BASE must be a plain https:// URL"
    if not _VERSION.fullmatch(ver):
        return "AGENT_VERSION must be a release tag such as v0.2.0"
    if not server or not _URL.fullmatch(server):
        return "INGEST_PUBLIC_URL must be the https:// address of the ingest host"
    try:
        _template()
    except (OSError, RuntimeError) as e:
        return f"installer script unavailable: {e}"
    return None


def render() -> str:
    """The installer with defaults filled in. Call problem() first."""
    vals = {"SERVER": settings.ingest_public_url.rstrip("/"), "VERSION": settings.agent_version,
            "BASE": settings.agent_download_base.rstrip("/")}
    return _DEFAULTS.sub(lambda m: f'DEFAULT_{m.group(1)}="{vals[m.group(1)]}"', _template())


def install_command(token: str) -> str | None:
    if problem():
        return None
    return f"curl -fsSL {settings.ingest_public_url.rstrip('/')}/agent/install.sh | sudo bash -s -- --token {token}"
