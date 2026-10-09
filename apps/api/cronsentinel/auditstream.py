"""R57 — deliver audit-log events to customer SIEMs.

Kinds:
  webhook     POST {"source": "jobwatch", "type": "audit", "events": [...]} as JSON, signed with
              X-JobWatch-Signature: sha256=<hmac of the body with the stream's secret>; batch ids in
              X-JobWatch-Delivery. Works with most log pipelines (Logstash, Vector, Fluent Bit, Sumo, …).
  splunk_hec  Splunk HTTP Event Collector: newline-joined {"time", "source", "sourcetype", "index"?,
              "event"} objects, header "Authorization: Splunk <token>".

Delivery is at-least-once and in order per stream; each event carries its audit id for dedupe.
All destinations go through netguard (no internal addresses) with TLS verification.
"""
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text

from . import netguard
from .crypto import decrypt_json

log = structlog.get_logger()
BATCH = 500
MAX_BACKOFF_S = 900


class DeliveryError(Exception):
    pass


def event(r) -> dict:
    """One audit_logs row → the documented event shape."""
    return {"id": r.id, "time": r.ts.astimezone(UTC).isoformat().replace("+00:00", "Z"), "org_id": str(r.org_id),
            "action": r.action, "actor": {"type": r.actor_type, "id": str(r.actor_id) if r.actor_id else None},
            "target": {"type": r.target_type, "id": r.target_id}, "ip": str(r.ip) if r.ip else None,
            "details": r.payload or {}, "source": "jobwatch"}


def send(kind: str, cfg: dict, events: list[dict], timeout: float = 15) -> None:
    if kind == "webhook":
        body = json.dumps({"source": "jobwatch", "type": "audit", "events": events}, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "JobWatch-AuditStream/1",
                   "X-JobWatch-Delivery": f"{events[0]['id']}-{events[-1]['id']}" if events else "test"}
        if cfg.get("secret"):
            headers["X-JobWatch-Signature"] = "sha256=" + hmac.new(cfg["secret"].encode(), body, hashlib.sha256).hexdigest()
        r = netguard.post(cfg["url"], content=body, headers=headers, timeout=timeout)
    elif kind == "splunk_hec":
        lines = []
        for e in events:
            ev = {"time": datetime.fromisoformat(e["time"].replace("Z", "+00:00")).timestamp(), "host": "jobwatch",
                  "source": "jobwatch", "sourcetype": cfg.get("sourcetype") or "jobwatch:audit", "event": e}
            if cfg.get("index"):
                ev["index"] = cfg["index"]
            lines.append(json.dumps(ev, separators=(",", ":")))
        r = netguard.post(cfg["url"], content="\n".join(lines).encode(), timeout=timeout,
                          headers={"Authorization": f"Splunk {cfg['token']}", "Content-Type": "application/json"})
    elif kind == "datadog_logs":                 # R58
        from . import datadog
        try:
            datadog.send_logs(cfg, events)
        except datadog.DatadogError as e:
            raise DeliveryError(str(e)) from None
        return
    else:
        raise DeliveryError(f"unknown stream kind {kind}")
    if r.status_code >= 300:
        raise DeliveryError(f"destination answered HTTP {r.status_code}")


def _backoff(failures: int) -> timedelta:
    return timedelta(seconds=min(10 * 2 ** min(failures, 10), MAX_BACKOFF_S))


def tick(s, now: datetime | None = None) -> int:
    """Deliver one batch for every due stream. Returns events delivered. Safe with several exporters:
    each stream row is locked (SKIP LOCKED) while it is being worked."""
    now = now or datetime.now(UTC)
    delivered = 0
    streams = s.execute(text("""SELECT id, org_id, kind, config_enc, cursor, failures FROM audit_streams
        WHERE enabled AND next_attempt_at <= :now ORDER BY next_attempt_at LIMIT 50 FOR UPDATE SKIP LOCKED"""), {"now": now}).all()
    for st in streams:
        rows = s.execute(text("""SELECT id, org_id, ts, actor_id, actor_type, action, target_type, target_id, ip, payload
            FROM audit_logs WHERE org_id = :o AND id > :c ORDER BY id LIMIT :n"""), {"o": st.org_id, "c": st.cursor, "n": BATCH}).all()
        if not rows:
            continue
        evs = [event(r) for r in rows]
        try:
            send(st.kind, decrypt_json(st.config_enc), evs)
        except Exception as e:
            msg = str(e) if isinstance(e, (DeliveryError, netguard.BlockedDestination)) else "could not reach the destination"
            s.execute(text("""UPDATE audit_streams SET failures = failures + 1, last_error = :m, last_error_at = :now,
                              next_attempt_at = :next WHERE id = :id"""),
                      {"m": msg[:300], "now": now, "next": now + _backoff(st.failures), "id": st.id})
            log.warning("audit stream delivery failed", stream=str(st.id), error=msg)
            continue
        s.execute(text("""UPDATE audit_streams SET cursor = :c, failures = 0, last_success_at = :now, last_error = NULL,
                          next_attempt_at = :now, delivered_total = delivered_total + :n WHERE id = :id"""),
                  {"c": rows[-1].id, "now": now, "n": len(rows), "id": st.id})
        delivered += len(rows)
    return delivered
