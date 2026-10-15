"""Secret redaction applied to every log chunk / payload string at ingest (D12, Security §7).

What survives redaction is stored, shown to every user in the tenant, put in alert text sent to
Slack/PagerDuty, and sent to the copilot LLM, so this errs on the side of redacting too much.
Regression corpus: tests/test_redaction.py.
"""
import math
import re

_R = "[REDACTED]"

# Key names whose value is a secret. Matched inside longer names too (DB_PASSWORD, x-api-key,
# AWS_SECRET_ACCESS_KEY, client_secret).
_KEY = r"(?:api[_-]?key|access[_-]?key|secret|token|password|passwd|passphrase|pwd|credentials?)"

# `Authorization: Basic dXNlcjpwYXNz` — keep the scheme, drop the credential. Runs first so the
# generic key=value rule below never sees "authorization" and leaves the credential behind.
_AUTH = re.compile(
    r"(?i)(\bauthorization[\"']?[ \t]*[:=][ \t]*[\"']?)"
    r"(?:(basic|bearer|digest|token|negotiate|ntlm|apikey|sharedkey|aws4-hmac-sha256)[ \t]+)?"
    r"([^\s\"',]+(?:,[ \t]*[^\s\"',]+)*)")

# key = value / key: value, with optional quotes around the key (JSON, YAML, env, logfmt, headers).
_KV = re.compile(
    rf"(?i)([\"']?[\w.-]*{_KEY}[\w.-]*[\"']?[ \t]*[:=][ \t]*)(\"[^\"\n]*\"|'[^'\n]*'|\S+)")

# --password x, --api-key x, --token x (the --flag=value form is handled by _KV).
_FLAG = re.compile(rf"(?i)((?<![\w-])--?[\w-]*{_KEY}[\w-]*[ \t]+)((?!-)(?:\"[^\"\n]*\"|'[^'\n]*'|\S+))")

# Credentials in any URL: mongodb://, redis://, amqp://, https://user:pass@, postgres://, ...
_URL_USERPASS = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^:/@\s]*):([^@\s/]+)@")
# https://ghp_xxx@github.com — a long user part with no password is a token in practice.
_URL_USERTOKEN = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^:/@\s]{16,})@")

_PATTERNS = [
    re.compile(r"(?i)\bbearer\s+[a-z0-9\-_.=+/~]+"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),                  # OpenAI / Anthropic style
    re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}"),     # Stripe
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),                   # Google API key
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    # 32+ hex chars: API keys (Datadog and many others) are hex, and hex can never reach the entropy
    # threshold below (at most 4 bits/char). Also hides commit SHAs and checksums; that is the trade-off.
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
]
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


def _value(v: str) -> str:
    """Replacement for a matched value, keeping its quotes so JSON/YAML stays parseable."""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return f"{v[0]}{_R}{v[0]}"
    return _R


def redact(text: str) -> str:
    if not text:
        return text
    text = _AUTH.sub(lambda m: f"{m.group(1)}{m.group(2) + ' ' if m.group(2) else ''}{_R}", text)
    text = _KV.sub(lambda m: m.group(1) + _value(m.group(2)), text)
    text = _FLAG.sub(lambda m: m.group(1) + _value(m.group(2)), text)
    text = _URL_USERPASS.sub(lambda m: f"{m.group(1)}{m.group(2)}:{_R}@", text)
    text = _URL_USERTOKEN.sub(lambda m: f"{m.group(1)}{_R}@", text)
    for p in _PATTERNS:
        text = p.sub(_R, text)
    # high-entropy blobs (likely tokens)
    return _TOKEN_RE.sub(lambda m: _R if _entropy(m.group(0)) > 4.2 else m.group(0), text)
