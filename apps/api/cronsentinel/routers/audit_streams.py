"""R57 — owner-only management of audit-log streams (delivery: cronsentinel/auditstream.py)."""
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from .. import auditstream, netguard
from ..auth import Principal, audit, require_role
from ..config import settings
from ..crypto import encrypt_json
from ..db import tenant_session

router = APIRouter(prefix="/api/v1/audit-streams", tags=["audit"])


class StreamIn(BaseModel):
    kind: str = Field(pattern="^(webhook|splunk_hec|datadog_logs)$")
    name: str = Field(min_length=1, max_length=80)
    url: str | None = Field(default=None, max_length=500, description="webhook and splunk_hec")
    api_key: str | None = Field(default=None, max_length=64, description="datadog_logs: Datadog API key")
    site: str | None = Field(default=None, max_length=40, description="datadog_logs: e.g. datadoghq.com, datadoghq.eu")
    secret: str | None = Field(default=None, min_length=16, max_length=200, description="webhook: HMAC signing secret")
    token: str | None = Field(default=None, min_length=8, max_length=200, description="splunk_hec: HEC token")
    index: str | None = Field(default=None, max_length=80)
    sourcetype: str | None = Field(default=None, max_length=80)
    start: str = Field(default="now", pattern="^(now|30d)$", description="'now', or '30d' to also send the last 30 days")


class StreamOut(BaseModel):
    id: UUID
    kind: str
    name: str
    host: str
    enabled: bool
    delivered_total: int
    pending: int
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    next_attempt_at: datetime
    created_at: datetime


class EnabledIn(BaseModel):
    enabled: bool


def _gate(s, org_id) -> None:
    feats = s.execute(text("SELECT pl.features FROM organizations o JOIN plan_limits pl ON pl.plan=o.plan WHERE o.id=:o"), {"o": str(org_id)}).scalar() or []
    if "audit_stream" not in feats and "all" not in feats:
        raise HTTPException(402, "audit log streaming is available on the Business and Enterprise plans")


def _check_url(url: str) -> None:
    if not url.startswith("https://") and not settings.outbound_allow_private:
        raise HTTPException(400, "url must be https://")
    try:
        netguard.check_url(url)
    except netguard.BlockedDestination as e:
        raise HTTPException(400, f"url: {e}") from None


def _list(s) -> list[StreamOut]:
    top = s.execute(text("SELECT COALESCE(max(id), 0) FROM audit_logs")).scalar()
    out = []
    for r in s.execute(text("""SELECT id, kind, name, config_enc, enabled, delivered_total, cursor, last_success_at, last_error,
                                      last_error_at, next_attempt_at, created_at FROM audit_streams ORDER BY created_at""")).all():
        from ..crypto import decrypt_json
        cfg = decrypt_json(r.config_enc)
        pending = s.execute(text("SELECT count(*) FROM audit_logs WHERE id > :c AND id <= :t"), {"c": r.cursor, "t": top}).scalar()
        out.append(StreamOut(id=r.id, kind=r.kind, name=r.name, host=urlsplit(cfg["url"]).hostname or "", enabled=r.enabled,
                             delivered_total=r.delivered_total, pending=pending, last_success_at=r.last_success_at,
                             last_error=r.last_error, last_error_at=r.last_error_at, next_attempt_at=r.next_attempt_at, created_at=r.created_at))
    return out


@router.get("", response_model=list[StreamOut])
def list_streams(p: Principal = Depends(require_role("owner"))):
    with tenant_session(p.org_id) as s:
        return _list(s)


@router.post("", response_model=StreamOut, status_code=201)
def create_stream(body: StreamIn, request: Request, p: Principal = Depends(require_role("owner"))):
    if body.kind == "datadog_logs":
        from .. import datadog
        cfg = {"api_key": body.api_key or "", "site": body.site or "datadoghq.com"}
        if (err := datadog.validate(cfg)):
            raise HTTPException(422, err)
        cfg["url"] = f"https://http-intake.logs.{cfg['site']}/api/v2/logs"     # shown as the host; Datadog's fixed intake
        return _create(body, cfg, request, p)
    if not body.url:
        raise HTTPException(422, "url is required")
    _check_url(body.url)
    if body.kind == "splunk_hec" and not body.token:
        raise HTTPException(422, "token is required for Splunk HEC")
    if body.kind == "webhook" and not body.secret:
        raise HTTPException(422, "secret is required for webhooks (receivers verify the signature with it)")
    cfg = {"url": body.url}
    if body.kind == "webhook":
        cfg["secret"] = body.secret
    else:
        cfg.update(token=body.token, index=body.index, sourcetype=body.sourcetype)
    return _create(body, cfg, request, p)


def _create(body: StreamIn, cfg: dict, request: Request, p: Principal) -> StreamOut:
    with tenant_session(p.org_id) as s:
        _gate(s, p.org_id)
        q = ("SELECT COALESCE(max(id), 0) FROM audit_logs" if body.start == "now" else
             "SELECT COALESCE(min(id) - 1, (SELECT COALESCE(max(id), 0) FROM audit_logs)) FROM audit_logs WHERE ts >= now() - interval '30 days'")
        cursor = s.execute(text(q)).scalar()
        sid = s.execute(text("""INSERT INTO audit_streams (org_id, kind, name, config_enc, cursor) VALUES (:o, :k, :n, :c, :cur) RETURNING id"""),
                        {"o": str(p.org_id), "k": body.kind, "n": body.name, "c": encrypt_json(cfg), "cur": cursor}).scalar()
        audit(s, p, "audit_stream.create", "audit_stream", str(sid), {"kind": body.kind, "name": body.name, "host": urlsplit(cfg["url"]).hostname,
              "start": body.start}, request.client.host if request.client else None)
        return next(x for x in _list(s) if x.id == sid)


@router.patch("/{stream_id}", response_model=StreamOut)
def set_enabled(stream_id: UUID, body: EnabledIn, request: Request, p: Principal = Depends(require_role("owner"))):
    with tenant_session(p.org_id) as s:
        n = s.execute(text("UPDATE audit_streams SET enabled=:e, next_attempt_at=now(), failures=0 WHERE id=:i"), {"e": body.enabled, "i": str(stream_id)}).rowcount
        if not n: raise HTTPException(404)
        audit(s, p, "audit_stream.enable" if body.enabled else "audit_stream.disable", "audit_stream", str(stream_id),
              ip=request.client.host if request.client else None)
        return next(x for x in _list(s) if x.id == stream_id)


@router.delete("/{stream_id}", status_code=204)
def delete_stream(stream_id: UUID, request: Request, p: Principal = Depends(require_role("owner"))):
    with tenant_session(p.org_id) as s:
        n = s.execute(text("DELETE FROM audit_streams WHERE id=:i"), {"i": str(stream_id)}).rowcount
        if not n: raise HTTPException(404)
        audit(s, p, "audit_stream.delete", "audit_stream", str(stream_id), ip=request.client.host if request.client else None)


@router.post("/{stream_id}/test")
def test_stream(stream_id: UUID, p: Principal = Depends(require_role("owner"))):
    """Send one synthetic event now and report the result; the cursor is not touched."""
    from ..crypto import decrypt_json
    with tenant_session(p.org_id) as s:
        r = s.execute(text("SELECT kind, config_enc FROM audit_streams WHERE id=:i"), {"i": str(stream_id)}).first()
        if not r: raise HTTPException(404)
    ev = {"id": 0, "time": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "org_id": str(p.org_id), "action": "audit_stream.test",
          "actor": {"type": "user" if p.user_id else "api_key", "id": str(p.user_id or p.api_key_id)}, "target": {"type": "audit_stream", "id": str(stream_id)},
          "ip": None, "details": {"message": "Test event from JobWatch"}, "source": "jobwatch"}
    try:
        auditstream.send(r.kind, decrypt_json(r.config_enc), [ev])
    except (auditstream.DeliveryError, netguard.BlockedDestination) as e:
        return {"ok": False, "error": str(e)}
    except Exception:
        return {"ok": False, "error": "could not reach the destination"}
    return {"ok": True}
