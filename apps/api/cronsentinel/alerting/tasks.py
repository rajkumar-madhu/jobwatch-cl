"""Celery notification tasks. One task per (channel, event). Per-channel rate limit + ledger."""
import hashlib
import hmac
import json
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage

import httpx

from .. import netguard  # R18: tenant-supplied URLs are fetched only through the SSRF guard
import redis
import structlog
from sqlalchemy import text

from ..celery_app import celery
from ..config import settings
from ..crypto import decrypt_json
from .. import datadog
from . import context as alert_context
from . import oncall
from ..db import system_session

log = structlog.get_logger()
_r = redis.Redis.from_url(settings.redis_url, decode_responses=True)

_EMOJI = {"failed": "✕", "missed": "⚠", "late": "⚠", "timeout": "⏱", "recovered": "✓", "flapping": "↕"}


def _fmt(p: dict) -> tuple[str, str]:
    """R55: subject and body come from alert_context (server, environment, reason, run facts)."""
    c = p.get("context") or {}
    return alert_context.subject(p, c), alert_context.body(p, c)


def _send_email(cfg, title, body):
    to = cfg["to"] if isinstance(cfg["to"], list) else [cfg["to"]]
    msg = EmailMessage(); msg["Subject"] = title; msg["From"] = cfg["from"]; msg["To"] = ", ".join(to); msg.set_content(body)
    port = int(cfg.get("port", 587))
    implicit = port == 465 or bool(cfg.get("ssl"))
    # R39: SSRF-guarded connect + verified TLS (see netguard.smtp)
    with netguard.smtp(cfg["host"], port, timeout=15, implicit_tls=implicit) as smtp:
        if not implicit and cfg.get("starttls", True):
            smtp.starttls(context=ssl.create_default_context())
        if cfg.get("username"): smtp.login(cfg["username"], cfg["password"])
        smtp.send_message(msg)


def _send_slack(cfg, title, body, p):
    color = {"critical": "#dc2626", "high": "#ea580c", "medium": "#d97706", "low": "#2563eb"}[p["severity"]]
    if p["condition"] == "recovered": color = "#16a34a"
    rows = alert_context.details(p, p.get("context") or {})
    fields = [{"title": k, "value": v, "short": len(v) < 40 and k not in ("Last error", "Failed checks", "Open in JobWatch")} for k, v in rows]
    netguard.post(cfg["webhook_url"], json={"text": title, "attachments": [{"color": color, "fallback": body, "fields": fields}]},
                  timeout=10).raise_for_status()


def _send_teams(cfg, title, body, p):
    netguard.post(cfg["webhook_url"], json={"@type": "MessageCard", "@context": "http://schema.org/extensions", "summary": title,
                                          "themeColor": "dc2626" if p["new_status"] in ("failed", "missed", "timeout") else "16a34a",
                                          "title": title, "sections": [{"facts": [{"name": k, "value": v} for k, v in
                                                                                 alert_context.details(p, p.get("context") or {})]}]},
                  timeout=10).raise_for_status()


def _send_discord(cfg, title, body, p):
    rows = alert_context.details(p, p.get("context") or {})
    netguard.post(cfg["webhook_url"], json={"content": f"**{title}**"[:2000], "embeds": [{
        "fields": [{"name": k, "value": v[:1024], "inline": len(v) < 40} for k, v in rows][:25]}]}, timeout=10).raise_for_status()


def _send_telegram(cfg, title, body, p):
    httpx.post(f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
               json={"chat_id": cfg["chat_id"], "text": f"{title}\n{body}"}, timeout=10).raise_for_status()


def _send_webhook(cfg, title, body, p):
    raw = json.dumps({"event": "job.status.changed", "title": title, **p}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "JobWatch/0.1"}
    if cfg.get("secret"):
        headers["X-JobWatch-Signature"] = "sha256=" + hmac.new(cfg["secret"].encode(), raw, hashlib.sha256).hexdigest()
    netguard.post(cfg["url"], content=raw, headers=headers, timeout=10).raise_for_status()


def _escalation_is_routed_earlier(cfg, title, body, p):
    """'escalation' channels start an on-call escalation (send_notification handles them before any
    sender is chosen). Reaching this means that routing broke; fail loudly instead of dropping a page."""
    raise RuntimeError("escalation channels must be routed to the escalation engine")


_SENDERS = {"email": lambda c, t, b, p: _send_email(c, t, b), "slack": _send_slack, "teams": _send_teams,
            "discord": _send_discord, "telegram": _send_telegram, "webhook": _send_webhook,
            "pagerduty": oncall.send_pagerduty, "opsgenie": oncall.send_opsgenie,   # R42
            "datadog": lambda c, t, b, p: datadog.send_event(c, t, b, p),           # R58
            "escalation": _escalation_is_routed_earlier}                            # R66
# TODO: sms (provider TBD)


@celery.task(bind=True, max_retries=5, default_retry_delay=30)
def send_notification(self, org_id: str, channel_id: str, dedup_key: str, incident_id: str | None, payload: dict):
    with system_session() as s:
        ch = s.execute(text("SELECT kind, config_enc, rate_per_min, enabled FROM notification_channels WHERE id=:id AND org_id=:o"),
                       {"id": channel_id, "o": org_id}).first()
        if not ch or not ch.enabled:
            return
        # per-channel rate limit (D10)
        k = f"chrl:{channel_id}:{datetime.now(UTC).strftime('%Y%m%d%H%M')}"
        n = _r.incr(k); _r.expire(k, 120)
        if n > ch.rate_per_min:
            s.execute(text("INSERT INTO notification_ledger (org_id, incident_id, channel_id, dedup_key, status, error) VALUES (:o, :i, :c, :k, 'rate_limited', NULL)"),
                      {"o": org_id, "i": incident_id, "c": channel_id, "k": dedup_key})
            return
        if ch.kind == "escalation":                        # R66: page whoever is on call
            from .. import escalation
            pid = decrypt_json(ch.config_enc).get("policy_id")
            if payload.get("test"):
                err = escalation.test_page(s, org_id, pid, payload)
                status = "failed" if err else "sent"
            elif payload.get("condition") == "recovered":
                if payload.get("job_id"):
                    escalation.resolve_for_job(s, org_id, payload["job_id"])
                status, err = "sent", None
            else:
                key = f"incident:{payload['incident_id']}" if payload.get("incident_id") else f"job:{payload.get('job_id')}:{payload.get('condition')}"
                escalation.start(s, org_id, pid, payload, key)
                escalation.tick(s)                         # page the first step now, not on the next tick
                status, err = "sent", None
            s.execute(text("INSERT INTO notification_ledger (org_id, incident_id, channel_id, dedup_key, status, error) VALUES (:o, :i, :c, :k, :st, :e)"),
                      {"o": org_id, "i": incident_id, "c": channel_id, "k": dedup_key, "st": status, "e": err})
            return
        cfg = decrypt_json(ch.config_enc)
        # R55: who/where/why for every channel — looked up once here from ids already in the payload
        try:
            payload = {**payload, "context": alert_context.build(s, org_id, payload)}
        except Exception:
            log.warning("alert context lookup failed; sending the basic alert", exc_info=True)
        title, body = _fmt(payload)
        try:
            _SENDERS[ch.kind](cfg, title, body, payload)
            status, err = "sent", None
            if ch.kind in oncall.KINDS and not payload.get("test") and payload.get("job_id"):
                # remember which on-call tools hold an open alert for this job, so recovery can
                # resolve them even when no "recovered" rule points at the channel (engine.py)
                k = oncall.open_key(org_id, str(payload["job_id"]))
                (_r.srem if payload.get("condition") == "recovered" else _r.sadd)(k, channel_id)
                _r.expire(k, 30 * 86400)
        except Exception as e:
            # R18: the ledger is tenant-visible (/alerts/ledger); store a category, not the raw exception.
            status, err = "failed", netguard.tenant_error(e)
        s.execute(text("INSERT INTO notification_ledger (org_id, incident_id, channel_id, dedup_key, status, error) VALUES (:o, :i, :c, :k, :st, :e)"),
                  {"o": org_id, "i": incident_id, "c": channel_id, "k": dedup_key, "st": status, "e": err})
        if incident_id:
            s.execute(text("UPDATE incidents SET last_notified_at=now() WHERE id=:i"), {"i": incident_id})
            s.execute(text("INSERT INTO incident_events (org_id, incident_id, kind, payload) VALUES (:o, :i, 'notified', CAST(:p AS jsonb))"),
                      {"o": org_id, "i": incident_id, "p": json.dumps({"channel_id": channel_id, "kind": ch.kind, "status": status})})
    if status == "failed":
        raise self.retry(exc=RuntimeError(err))
