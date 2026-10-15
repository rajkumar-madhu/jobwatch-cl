"""Regression corpus for cronsentinel.redaction (code review 2026-10-10, finding #2).

Every SECRET case lists the secret string that must not survive redact(); every KEEP case is text
that must come through unchanged so job output stays readable."""
import pytest

from cronsentinel.redaction import redact

SECRET = [
    # (input, secret that must disappear)
    ('{"password": "hunter2"}', "hunter2"),
    ("{'password': 'hunter2'}", "hunter2"),
    ('{"api_key":"k-123456"}', "k-123456"),
    ("password=hunter2", "hunter2"),
    ("DB_PASSWORD=hunter2", "hunter2"),
    ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCY", "wJalrXUtnFEMIK7MDENGbPxRfiCY"),
    ("client_secret: s3cr3tv4lue", "s3cr3tv4lue"),
    ("x-api-key: abcdef123", "abcdef123"),
    ("pg_dump --password hunter2 mydb", "hunter2"),
    ("curl --token 'hunter2' https://x", "hunter2"),
    ("mysql --password=hunter2", "hunter2"),
    ("deploy --api-key=hunter2", "hunter2"),
    ("mongodb://admin:hunter2@db:27017/app", "hunter2"),
    ("mongodb+srv://admin:hunter2@cluster0.example.net", "hunter2"),
    ("redis://:hunter2@cache:6379/0", "hunter2"),
    ("amqp://guest:hunter2@rabbit:5672/", "hunter2"),
    ("git clone https://deploy:hunter2@git.example.com/r.git", "hunter2"),
    ("postgresql://app:hunter2@pg/app", "hunter2"),
    ("mysql://root:hunter2@localhost/x", "hunter2"),
    ("https://x-access-token-0123456789@github.com/o/r", "x-access-token-0123456789"),
    ("Authorization: Basic dXNlcjpodW50ZXIy", "dXNlcjpodW50ZXIy"),
    ("authorization: Bearer abc.def.ghi", "abc.def.ghi"),
    ('"Authorization": "Token tok_123"', "tok_123"),
    ("Authorization: AWS4-HMAC-SHA256 Credential=AKID/x, Signature=abc123", "Signature=abc123"),
    ("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl", "eyJhbGciOiJIUzI1NiJ9"),
    ("datadog key 0123456789abcdef0123456789abcdef in use", "0123456789abcdef0123456789abcdef"),
    ("key=ABCDEF0123456789ABCDEF0123456789AB", "ABCDEF0123456789ABCDEF0123456789AB"),
    ("AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("ghp_" + "a" * 36, "ghp_" + "a" * 36),
    ("github_pat_11ABCDEFG0123456789_abcdefghijklmnop", "github_pat_11ABCDEFG0123456789_abcdefghijklmnop"),
    ("xoxb-1234-5678-abcdef", "xoxb-1234-5678-abcdef"),
    ("sk-ant-api03-abcdefghijklmnopqrstuvwxyz", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz"),
    ("sk_live_abcdefghijklmnop1234", "sk_live_abcdefghijklmnop1234"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----", "MIIabc"),
    ("token: Zx8qP2mN7vR4tY1wK6sD3fG9hJ0lA5cB", "Zx8qP2mN7vR4tY1wK6sD3fG9hJ0lA5cB"),
]

KEEP = [
    "backup finished in 12.3s, 1532 rows",
    "rsync -av /srv/data/ backup:/srv/data/",
    "GET https://api.example.com/v1/jobs?page=2 200",
    "ssh://git@github.com/org/repo.git",
    "job 3f2a9c1e-4b7d-4e8a-9c2f-1a2b3c4d5e6f ok",
    "exit code 1: connection refused (db:5432)",
]


@pytest.mark.parametrize("text,secret", SECRET)
def test_secret_is_removed(text, secret):
    out = redact(text)
    assert secret not in out, out
    assert "[REDACTED]" in out


@pytest.mark.parametrize("text", KEEP)
def test_ordinary_output_is_untouched(text):
    assert redact(text) == text


def test_json_stays_parseable():
    import json
    assert json.loads(redact('{"password": "hunter2", "user": "bob"}')) == {"password": "[REDACTED]", "user": "bob"}


def test_key_and_scheme_stay_visible():
    assert redact("Authorization: Basic dXNlcjpwYXNz") == "Authorization: Basic [REDACTED]"
    assert redact("redis://:pw@cache:6379") == "redis://:[REDACTED]@cache:6379"
    assert redact("--password hunter2 --user bob") == "--password [REDACTED] --user bob"


def test_empty_and_none():
    assert redact("") == ""
    assert redact(None) is None
