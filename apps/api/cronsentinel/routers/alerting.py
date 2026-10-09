from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..alerting.rules import CONDITIONS
from ..auth import Principal, audit, current_principal, require_role
from ..crypto import encrypt_json
from ..db import tenant_session
from ..schemas import AlertRuleOut, ChannelOut

router = APIRouter(prefix="/api/v1/alerts", tags=["alerting"])

_KINDS = {"email": ["host", "from", "to"], "slack": ["webhook_url"], "teams": ["webhook_url"], "discord": ["webhook_url"],
          "telegram": ["bot_token", "chat_id"], "webhook": ["url"],
          "pagerduty": ["routing_key"], "opsgenie": ["api_key"],   # R42
          "datadog": ["api_key"],                                  # R58
          "escalation": ["policy_id"]}                             # R66


class ChannelIn(BaseModel):
    kind: str
    name: str
    config: dict
    rate_per_min: int = Field(30, ge=1, le=600)


class RuleIn(BaseModel):
    name: str
    condition: str
    scope: dict = {}
    params: dict = {}
    severity: str = "medium"
    channel_ids: list[UUID] = []
    business_hours: dict | None = None
    repeat_interval_s: int | None = Field(None, ge=60)
    enabled: bool = True


class MaintenanceScope(BaseModel):
    job_ids: list[UUID] = []
    tags: list[str] = []
    environment_ids: list[UUID] = []


class MaintenanceIn(BaseModel):
    """R63: a named window; `repeat` daily or weekly expands in `tz` (cronsentinel/maintenance.py)."""
    name: str = Field(default="Maintenance", min_length=1, max_length=80)
    scope: MaintenanceScope = MaintenanceScope()
    starts_at: datetime
    ends_at: datetime
    tz: str = "UTC"
    repeat: str = Field(default="none", pattern="^(none|daily|weekly)$")
    weekdays: list[str] = []
    until: datetime | None = None
    rrule: str | None = Field(default=None, description="advanced: an RFC 5545 RRULE (DAILY or WEEKLY); overrides repeat")


class MaintenanceOut(BaseModel):
    id: UUID
    name: str
    scope: dict
    starts_at: datetime
    ends_at: datetime
    tz: str
    rrule: str | None = None
    active_now: bool
    current_end: datetime | None = None
    next_start: datetime | None = None
    next_end: datetime | None = None


@router.get("/channels", response_model=list[ChannelOut])
def list_channels(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text("SELECT id, kind, name, rate_per_min, enabled, created_at FROM notification_channels ORDER BY name")).all()]


@router.post("/channels", status_code=201)
def create_channel(body: ChannelIn, request: Request, p: Principal = Depends(require_role("devops"))):
    if body.kind not in _KINDS: raise HTTPException(400, f"kind must be one of {list(_KINDS)}")
    missing = [k for k in _KINDS[body.kind] if k not in body.config]
    if missing: raise HTTPException(400, f"config missing {missing}")
    from .. import netguard
    for key in ("url", "webhook_url"):  # R18: webhook, slack, teams, discord
        if key in body.config:
            try:
                netguard.check_url(str(body.config[key]))
            except netguard.BlockedDestination as e:
                raise HTTPException(400, f"{key}: {e}")
    if body.kind == "email":   # R39: SMTP host gets the same SSRF policy as webhook URLs
        try:
            port = int(body.config.get("port", 587))
        except (TypeError, ValueError):
            raise HTTPException(400, "port must be a number")
        if not 1 <= port <= 65535:
            raise HTTPException(400, "port out of range")
        try:
            netguard.check_host(str(body.config["host"]), port)
        except netguard.BlockedDestination as e:
            raise HTTPException(400, f"host: {e}")
    if body.kind == "telegram":   # the token is spliced into the API path; keep it to the real format
        import re
        if not re.fullmatch(r"\d{3,20}:[A-Za-z0-9_-]{20,64}", str(body.config["bot_token"])):
            raise HTTPException(400, "bot_token: not a Telegram bot token")
    if body.kind == "escalation":
        with tenant_session(p.org_id) as s2:
            if not s2.execute(text("SELECT 1 FROM escalation_policies WHERE id=CAST(:i AS uuid)"), {"i": str(body.config.get("policy_id"))}).first():
                raise HTTPException(400, "policy_id: no such escalation policy")
    if body.kind == "datadog":
        from ..datadog import validate as dd_validate
        if (err := dd_validate(body.config)):
            raise HTTPException(400, err)
    if body.kind in ("pagerduty", "opsgenie"):
        from ..alerting import oncall
        if (err := oncall.validate(body.kind, body.config)):
            raise HTTPException(400, err)
    with tenant_session(p.org_id) as s:
        feats = s.execute(text("SELECT pl.features FROM organizations o JOIN plan_limits pl ON pl.plan=o.plan WHERE o.id=:o"), {"o": str(p.org_id)}).scalar()
        if feats and "all" not in feats and body.kind not in feats:
            raise HTTPException(402, f"{body.kind} channel not available on current plan")
        r = s.execute(text("INSERT INTO notification_channels (org_id, kind, name, config_enc, rate_per_min) VALUES (:o, :k, :n, :c, :rl) RETURNING id"),
                      {"o": str(p.org_id), "k": body.kind, "n": body.name, "c": encrypt_json(body.config), "rl": body.rate_per_min}).first()
        audit(s, p, "channel.create", "notification_channel", str(r.id), {"kind": body.kind, "name": body.name}, request.client.host)
        return {"id": r.id, "kind": body.kind, "name": body.name}


@router.post("/channels/{channel_id}/test")
def test_channel(channel_id: UUID, p: Principal = Depends(require_role("devops"))):
    from ..alerting.tasks import send_notification
    payload = {"job_id": "test", "job_name": "test-job", "prev_status": "healthy", "new_status": "failed", "condition": "failed",
               "severity": "low", "incident_id": None, "execution_id": None, "ts": datetime.utcnow().isoformat(),
               "test": True, "test_id": str(channel_id)}   # on-call tools: trigger then resolve, never leave a page open
    send_notification.delay(str(p.org_id), str(channel_id), f"test:{channel_id}", None, payload)
    return {"queued": True}


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        # R66: escalation policies deliver pages through a channel (FK RESTRICT); say which one instead of a 500
        used = s.execute(text("SELECT name FROM escalation_policies WHERE delivery_channel_id=:c LIMIT 1"), {"c": str(channel_id)}).scalar()
        if used:
            raise HTTPException(409, f"escalation policy '{used}' delivers pages through this channel; change the policy first")
        if not s.execute(text("DELETE FROM notification_channels WHERE id=:id"), {"id": str(channel_id)}).rowcount: raise HTTPException(404)
        audit(s, p, "channel.delete", "notification_channel", str(channel_id), ip=request.client.host)


@router.get("/rules", response_model=list[AlertRuleOut])
def list_rules(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text("SELECT * FROM alert_rules ORDER BY created_at")).all()]


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, request: Request, p: Principal = Depends(require_role("devops"))):
    if body.condition not in CONDITIONS: raise HTTPException(400, f"condition must be one of {sorted(CONDITIONS)}")
    with tenant_session(p.org_id) as s:
        r = s.execute(text(
            "INSERT INTO alert_rules (org_id, name, condition, scope, params, severity, channel_ids, business_hours, repeat_interval_s, enabled) "
            "VALUES (:o, :n, :c, CAST(:sc AS jsonb), CAST(:pa AS jsonb), :sev, CAST(:ch AS uuid[]), CAST(:bh AS jsonb), :ri, :en) RETURNING id"),
            {"o": str(p.org_id), "n": body.name, "c": body.condition, "sc": __import__("json").dumps(body.scope), "pa": __import__("json").dumps(body.params),
             "sev": body.severity, "ch": [str(c) for c in body.channel_ids], "bh": __import__("json").dumps(body.business_hours) if body.business_hours else None,
             "ri": body.repeat_interval_s, "en": body.enabled}).first()
        audit(s, p, "rule.create", "alert_rule", str(r.id), {"name": body.name}, request.client.host)
        return {"id": r.id}


@router.patch("/rules/{rule_id}")
def toggle_rule(rule_id: UUID, enabled: bool, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("UPDATE alert_rules SET enabled=:e WHERE id=:id"), {"e": enabled, "id": str(rule_id)}).rowcount: raise HTTPException(404)
        audit(s, p, "rule.toggle", "alert_rule", str(rule_id), {"enabled": enabled}, request.client.host)
        return {"id": rule_id, "enabled": enabled}


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("DELETE FROM alert_rules WHERE id=:id"), {"id": str(rule_id)}).rowcount: raise HTTPException(404)
        audit(s, p, "rule.delete", "alert_rule", str(rule_id), ip=request.client.host)


def _mw_out(w) -> MaintenanceOut:
    from datetime import UTC
    from datetime import datetime as _dt
    now = _dt.now(UTC)
    cur = w.active_at(now)
    nxt = w.next_occurrence(now if not cur else cur[1])
    return MaintenanceOut(id=w.id, name=w.name, scope=w.scope, starts_at=w.starts_at, ends_at=w.ends_at, tz=w.tz, rrule=w.rrule,
                          active_now=bool(cur), current_end=cur[1] if cur else None,
                          next_start=nxt[0] if nxt else None, next_end=nxt[1] if nxt else None)


@router.get("/maintenance", response_model=list[MaintenanceOut])
def list_maintenance(p: Principal = Depends(current_principal)):
    """Windows that are active, upcoming, or ended in the last 7 days. Recurring windows stay listed
    while they still have occurrences (R63: they used to vanish a week after their first one)."""
    from datetime import UTC, timedelta
    from datetime import datetime as _dt

    from .. import maintenance
    now = _dt.now(UTC)
    with tenant_session(p.org_id) as s:
        ws = maintenance.load(s, p.org_id)
    keep = [w for w in ws if w.occurrences(now - timedelta(days=7), now + timedelta(days=400))]
    return [_mw_out(w) for w in sorted(keep, key=lambda w: (w.next_occurrence(now - timedelta(days=7)) or (w.starts_at,))[0])]


@router.get("/maintenance/{mw_id}", response_model=MaintenanceOut)
def get_maintenance(mw_id: UUID, p: Principal = Depends(current_principal)):
    """R64: one window by id, whatever its dates (Terraform reads; the list hides long-finished ones)."""
    from .. import maintenance
    with tenant_session(p.org_id) as s:
        w = maintenance.get(s, p.org_id, mw_id)
    if not w: raise HTTPException(404)
    return _mw_out(w)


@router.post("/maintenance", status_code=201, response_model=MaintenanceOut)
def create_maintenance(body: MaintenanceIn, request: Request, p: Principal = Depends(require_role("devops"))):
    from .. import maintenance
    try:
        rr = body.rrule or maintenance.build_rrule(body.repeat, body.weekdays, body.until, body.tz)
        maintenance.validate(body.starts_at, body.ends_at, body.tz, rr)
    except maintenance.InvalidWindow as e:
        raise HTTPException(422, str(e)) from None
    scope = {k: [str(x) for x in v] for k, v in body.scope.model_dump().items() if v}
    with tenant_session(p.org_id) as s:
        r = s.execute(text("""INSERT INTO maintenance_windows (org_id, name, scope, starts_at, ends_at, rrule, tz, created_by)
                              VALUES (:o, :n, CAST(:sc AS jsonb), :st, :en, :rr, :tz, :by) RETURNING id"""),
                      {"o": str(p.org_id), "n": body.name, "sc": __import__("json").dumps(scope), "st": body.starts_at,
                       "en": body.ends_at, "rr": rr, "tz": body.tz, "by": str(p.user_id) if p.user_id else None}).first()
        audit(s, p, "maintenance.create", "maintenance_window", str(r.id),
              {"name": body.name, "repeat": rr or "none", "scope": scope}, request.client.host if request.client else None)
        w = maintenance.get(s, p.org_id, r.id)
    return _mw_out(w)


@router.post("/maintenance/{mw_id}/end", response_model=MaintenanceOut)
def end_maintenance(mw_id: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    """End a one-off window now (work finished early). Recurring windows are deleted instead."""
    from .. import maintenance
    with tenant_session(p.org_id) as s:
        row = s.execute(text("SELECT rrule, starts_at FROM maintenance_windows WHERE id=:id"), {"id": str(mw_id)}).first()
        if not row: raise HTTPException(404)
        if row.rrule: raise HTTPException(409, "recurring windows cannot be ended early; delete the window instead")
        n = s.execute(text("""UPDATE maintenance_windows SET ends_at = GREATEST(now(), starts_at + interval '1 second')
                              WHERE id=:id AND ends_at > now()"""), {"id": str(mw_id)}).rowcount
        if not n: raise HTTPException(409, "this window is not active")
        audit(s, p, "maintenance.end", "maintenance_window", str(mw_id), ip=request.client.host if request.client else None)
        w = maintenance.get(s, p.org_id, mw_id)
    return _mw_out(w)


@router.delete("/maintenance/{mw_id}", status_code=204)
def delete_maintenance(mw_id: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("DELETE FROM maintenance_windows WHERE id=:id"), {"id": str(mw_id)}).rowcount: raise HTTPException(404)
        audit(s, p, "maintenance.delete", "maintenance_window", str(mw_id), ip=request.client.host if request.client else None)


@router.get("/ledger")
def ledger(p: Principal = Depends(current_principal), limit: int = 100):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text(
            "SELECT l.id, l.incident_id, l.channel_id, c.kind, c.name, l.dedup_key, l.sent_at, l.status, l.error FROM notification_ledger l "
            "LEFT JOIN notification_channels c ON c.id=l.channel_id ORDER BY l.sent_at DESC LIMIT :l"), {"l": min(limit, 500)}).all()]
