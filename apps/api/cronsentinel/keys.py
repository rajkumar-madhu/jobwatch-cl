"""Purpose-separated key derivation (R8).

One secret was doing three jobs: encrypting channel/destination configs at rest, signing session
cookies, and (via the same string) anything else that needed a key. That is a real weakness —
a signing oracle or a leak in one path compromises the others, and rotating the secret for one
purpose silently invalidates the others.

HKDF-SHA256 over the root secret with a per-purpose `info` label. Deriving is cheap and
deterministic, so nothing needs to be stored: the same root secret yields the same subkeys, but a
subkey reveals nothing about the root or its siblings.
"""
import base64
import functools

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import settings

SALT = b"jobwatch/v1"

# Placeholder values that have shipped in this repo's examples/defaults. A deployment running with one
# of these is running with a public key.
KNOWN_WEAK = {"", "change-me", "change-me-32-bytes-base64", "changeme", "secret", "ci", "dev"}
MIN_LEN = 32


def check_root_secret(value: str, allow_insecure: bool) -> None:
    """Raise unless `value` is plausibly a real secret. Called before any key is derived."""
    if allow_insecure:
        if not value:
            raise RuntimeError("SECRET_ENCRYPTION_KEY is not set")
        return
    if value.strip().lower() in KNOWN_WEAK or "change-me" in value.lower():
        raise RuntimeError("SECRET_ENCRYPTION_KEY is unset or a known placeholder; generate one with "
                           "`openssl rand -base64 48` (ALLOW_INSECURE_SECRET=true is for tests only)")
    if len(value) < MIN_LEN:
        raise RuntimeError(f"SECRET_ENCRYPTION_KEY must be at least {MIN_LEN} characters of random data")


@functools.lru_cache(maxsize=8)
def derive(purpose: str, length: int = 32) -> bytes:
    """Subkey for `purpose`. Purposes must be stable strings — changing one rotates that key and
    invalidates everything it protected (sessions log out; encrypted configs become unreadable)."""
    check_root_secret(settings.secret_encryption_key, settings.allow_insecure_secret)
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=SALT,
                info=f"jobwatch:{purpose}".encode()).derive(settings.secret_encryption_key.encode())


def fernet_key(purpose: str = "config-encryption") -> bytes:
    return base64.urlsafe_b64encode(derive(purpose))


def signing_key(purpose: str) -> str:
    return base64.urlsafe_b64encode(derive(purpose)).decode()
