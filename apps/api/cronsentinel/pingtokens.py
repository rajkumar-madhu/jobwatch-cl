"""R41: heartbeat (ping) tokens are stored hashed.

A ping token is a bearer credential: anyone holding it can report a job as succeeded and mask a real
outage. It used to sit in `jobs.heartbeat_token` in plaintext and was returned by every job read,
so a database dump, a backup, or any viewer-role API key exposed every ping URL in the tenant.

Now only SHA-256(token) and a 4-character hint are stored. The full token is shown exactly once —
in the response that created or rotated it. 144 bits of randomness make a fast hash appropriate
(no salt/KDF needed: the input is not guessable, unlike a password).
"""
import hashlib
import secrets


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def hint(raw: str) -> str:
    return raw[-4:]


def new_token() -> tuple[str, str, str]:
    """(raw, sha256 hex, hint). Store the last two; return the first to the caller once."""
    raw = secrets.token_urlsafe(18)
    return raw, hash_token(raw), hint(raw)
