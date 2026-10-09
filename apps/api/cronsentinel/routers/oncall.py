"""R66 — on-call schedules, escalation policies and active escalations."""
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from .. import escalation, oncall
from ..auth import Principal, audit, current_principal, require_role
from ..db import tenant_session

router = APIRouter(prefix="/api/v1/oncall", tags=["oncall"])


# ---- models ----------------------------------------------------------------------------------------

class LayerIn(BaseModel):
    name: str = Field(default="Rotation", min_length=1, max_length=80)
    rotation: str = Field(pattern="^(daily|weekly|custom)$")
    shift_hours: int | None = Field(default=None, ge=1, le=720)
    handoff_at: datetime
    members: list[UUID] = Field(min_length=1, max_length=50)
    restrict_days: list[int] | None = None
    restrict_start: str | None = None
    restrict_end: str | None = None


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    tz: str = "UTC"
    description: str | None = Field(default=None, max_length=500)
    layers: list[LayerIn] = Field(min_length=1, max_length=10, description="lowest first; later layers win")


class OverrideIn(BaseModel):
    user_id: UUID
    starts_at: datetime
    ends_at: datetime


class StepIn(BaseModel):
    delay_min: int = Field(default=15, ge=1, le=1440, description="minutes to wait for an acknowledgement before the next step")
    targets: list[dict] = Field(min_length=1, max_length=10, description='[{"type": "schedule"|"user", "id": "…"}]')


class PolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    repeat_count: int = Field(default=0, ge=0, le=9)
    delivery_channel_id: UUID
    steps: list[StepIn] = Field(min_length=1, max_length=10)


class PersonOut(BaseModel):
    id: str
    name: str
    email: str


class MemberOut(BaseModel):
    id: str
    name: str


class LayerOut(BaseModel):
    name: str
    rotation: str
    shift_hours: int | None = None
    handoff_at: datetime | None = None
    members: list[MemberOut]
    restrict_days: list[int] | None = None
    restrict_start: str | None = None
    restrict_end: str | None = None


class OnCallNow(BaseModel):
    user_id: str | None = None
    name: str | None = None
    source: str


class ShiftOut(BaseModel):
    start: datetime
    end: datetime
    user_id: str | None = None
    name: str | None = None
    source: str


class OverrideOut(BaseModel):
    id: UUID | str
    user_id: UUID | str
    starts_at: datetime
    ends_at: datetime


class ScheduleOut(BaseModel):
    id: str
    name: str
    tz: str
    description: str | None = None
    layers: list[LayerOut]
    on_call_now: OnCallNow
    timeline: list[ShiftOut] | None = None
    overrides: list[OverrideOut] | None = None


class StepOut(BaseModel):
    delay_min: int
    targets: list[dict]


class PolicyOut(BaseModel):
    id: str
    name: str
    repeat_count: int
    delivery_channel_id: str
    steps: list[StepOut]


class RunOut(BaseModel):
    id: UUID | str
    status: str
    step: int
    cycle: int
    next_at: datetime
    history: list[dict]
    created_at: datetime
    incident_id: UUID | str | None = None
    job_id: UUID | str | None = None
    job_name: str | None = None
    condition: str | None = None
    policy: str


# ---- helpers ---------------------------------------------------------------------------------------

def _gate(s, org_id) -> None:
    feats = s.execute(text("SELECT pl.features FROM organizations o JOIN plan_limits pl ON pl.plan=o.plan WHERE o.id=:o"), {"o": str(org_id)}).scalar() or []
    if "oncall" not in feats and "all" not in feats:
        raise HTTPException(402, "on-call schedules are available on the Team plan and above")


def _members_ok(s, ids) -> None:
    ids = {str(i) for i in ids}
    found = {str(r.user_id) for r in s.execute(text("SELECT user_id FROM memberships WHERE user_id = ANY(CAST(:ids AS uuid[]))"), {"ids": list(ids)}).all()}
    if ids - found:
        raise HTTPException(422, "every member must belong to this organization")


def _people(s) -> dict:
    return {str(r.id): (r.name or r.email) for r in s.execute(text(
        "SELECT u.id, u.name, u.email FROM users u JOIN memberships m ON m.user_id = u.id")).all()}


def _layer_obj(layer: LayerIn, pos: int) -> oncall.Layer:
    return oncall.Layer(position=pos, rotation=layer.rotation, handoff_at=layer.handoff_at, members=[str(m) for m in layer.members],
                        shift_hours=layer.shift_hours, restrict_days=layer.restrict_days, restrict_start=layer.restrict_start,
                        restrict_end=layer.restrict_end, name=layer.name)


def _write_layers(s, org_id, sid, body: ScheduleIn) -> None:
    for i, ly in enumerate(body.layers):
        try:
            oncall.validate_layer(_layer_obj(ly, i), body.tz)
        except oncall.InvalidSchedule as e:
            raise HTTPException(422, f"layer {i + 1}: {e}") from None
    _members_ok(s, [m for ly in body.layers for m in ly.members])
    s.execute(text("DELETE FROM oncall_layers WHERE schedule_id=:s"), {"s": str(sid)})
    for i, ly in enumerate(body.layers):
        s.execute(text("""INSERT INTO oncall_layers (org_id, schedule_id, position, name, rotation, shift_hours, handoff_at, members,
                restrict_days, restrict_start, restrict_end)
            VALUES (:o, :s, :p, :n, :r, :h, :ho, CAST(:m AS uuid[]), :rd, :rs, :re)"""),
            {"o": str(org_id), "s": str(sid), "p": i, "n": ly.name, "r": ly.rotation, "h": ly.shift_hours, "ho": ly.handoff_at,
             "m": [str(x) for x in ly.members], "rd": ly.restrict_days, "rs": ly.restrict_start, "re": ly.restrict_end})


def _schedule_out(s, org_id, row, people, days: int = 0) -> dict:
    sch = escalation.load_schedule(s, org_id, row.id)
    now = datetime.now(UTC)
    who, why = oncall.on_call(sch, now)
    layers = [dict(r._mapping) for r in s.execute(text(
        "SELECT name, rotation, shift_hours, handoff_at, members, restrict_days, restrict_start, restrict_end FROM oncall_layers WHERE schedule_id=:s ORDER BY position"),
        {"s": row.id}).all()]
    for ly in layers:
        ly["members"] = [{"id": str(m), "name": people.get(str(m), "former member")} for m in ly["members"]]
    out = {"id": str(row.id), "name": row.name, "tz": row.tz, "description": row.description, "layers": layers,
           "on_call_now": {"user_id": who, "name": people.get(who) if who else None, "source": why}}
    if days:
        segs = oncall.timeline(sch, now, now + timedelta(days=days))
        out["timeline"] = [{"start": x["start"], "end": x["end"], "user_id": x["user_id"],
                            "name": people.get(x["user_id"]) if x["user_id"] else None, "source": x["source"]} for x in segs]
        out["overrides"] = [dict(r._mapping) for r in s.execute(text(
            "SELECT id, user_id, starts_at, ends_at FROM oncall_overrides WHERE schedule_id=:s AND ends_at > now() ORDER BY starts_at"), {"s": row.id}).all()]
    return out


# ---- schedules ------------------------------------------------------------------------------------

@router.get("/schedules", response_model=list[ScheduleOut])
def list_schedules(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        people = _people(s)
        return [_schedule_out(s, p.org_id, r, people) for r in s.execute(text("SELECT id, name, tz, description FROM oncall_schedules ORDER BY name")).all()]


@router.get("/schedules/{sid}", response_model=ScheduleOut)
def get_schedule(sid: UUID, days: int = 14, p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        row = s.execute(text("SELECT id, name, tz, description FROM oncall_schedules WHERE id=:i"), {"i": str(sid)}).first()
        if not row: raise HTTPException(404)
        return _schedule_out(s, p.org_id, row, _people(s), days=max(1, min(days, 60)))


@router.post("/schedules", status_code=201)
def create_schedule(body: ScheduleIn, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        _gate(s, p.org_id)
        if s.execute(text("SELECT 1 FROM oncall_schedules WHERE name=:n"), {"n": body.name}).first():
            raise HTTPException(409, "a schedule with this name exists")
        sid = s.execute(text("INSERT INTO oncall_schedules (org_id, name, tz, description) VALUES (:o, :n, :tz, :d) RETURNING id"),
                        {"o": str(p.org_id), "n": body.name, "tz": body.tz, "d": body.description}).scalar()
        _write_layers(s, p.org_id, sid, body)
        audit(s, p, "oncall.schedule_create", "oncall_schedule", str(sid), {"name": body.name}, request.client.host if request.client else None)
        row = s.execute(text("SELECT id, name, tz, description FROM oncall_schedules WHERE id=:i"), {"i": sid}).first()
        return _schedule_out(s, p.org_id, row, _people(s), days=14)


@router.put("/schedules/{sid}")
def replace_schedule(sid: UUID, body: ScheduleIn, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("UPDATE oncall_schedules SET name=:n, tz=:tz, description=:d WHERE id=:i"),
                         {"n": body.name, "tz": body.tz, "d": body.description, "i": str(sid)}).rowcount:
            raise HTTPException(404)
        _write_layers(s, p.org_id, sid, body)
        audit(s, p, "oncall.schedule_update", "oncall_schedule", str(sid), {"name": body.name}, request.client.host if request.client else None)
        row = s.execute(text("SELECT id, name, tz, description FROM oncall_schedules WHERE id=:i"), {"i": str(sid)}).first()
        return _schedule_out(s, p.org_id, row, _people(s), days=14)


@router.delete("/schedules/{sid}", status_code=204)
def delete_schedule(sid: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        used = s.execute(text("""SELECT p.name FROM escalation_steps st JOIN escalation_policies p ON p.id = st.policy_id
            WHERE st.targets @> CAST(:t AS jsonb) LIMIT 1"""), {"t": json.dumps([{"type": "schedule", "id": str(sid)}])}).scalar()
        if used:
            raise HTTPException(409, f"used by escalation policy '{used}'; remove it there first")
        if not s.execute(text("DELETE FROM oncall_schedules WHERE id=:i"), {"i": str(sid)}).rowcount: raise HTTPException(404)
        audit(s, p, "oncall.schedule_delete", "oncall_schedule", str(sid), ip=request.client.host if request.client else None)


@router.post("/schedules/{sid}/overrides", status_code=201)
def add_override(sid: UUID, body: OverrideIn, request: Request, p: Principal = Depends(require_role("devops"))):
    if body.ends_at <= body.starts_at or body.ends_at - body.starts_at > timedelta(days=31):
        raise HTTPException(422, "an override must end after it starts and last at most 31 days")
    with tenant_session(p.org_id) as s:
        if not s.execute(text("SELECT 1 FROM oncall_schedules WHERE id=:i"), {"i": str(sid)}).first(): raise HTTPException(404)
        _members_ok(s, [body.user_id])
        oid = s.execute(text("""INSERT INTO oncall_overrides (org_id, schedule_id, user_id, starts_at, ends_at, created_by)
            VALUES (:o, :s, :u, :st, :en, :by) RETURNING id"""), {"o": str(p.org_id), "s": str(sid), "u": str(body.user_id),
            "st": body.starts_at, "en": body.ends_at, "by": str(p.user_id) if p.user_id else None}).scalar()
        audit(s, p, "oncall.override_create", "oncall_schedule", str(sid), {"user_id": str(body.user_id)}, request.client.host if request.client else None)
    return {"id": str(oid)}


@router.delete("/schedules/{sid}/overrides/{oid}", status_code=204)
def delete_override(sid: UUID, oid: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("DELETE FROM oncall_overrides WHERE id=:o AND schedule_id=:s"), {"o": str(oid), "s": str(sid)}).rowcount:
            raise HTTPException(404)
        audit(s, p, "oncall.override_delete", "oncall_schedule", str(sid), ip=request.client.host if request.client else None)


# ---- escalation policies ---------------------------------------------------------------------------

def _validate_policy(s, body: PolicyIn) -> None:
    ch = s.execute(text("SELECT kind FROM notification_channels WHERE id=:c"), {"c": str(body.delivery_channel_id)}).first()
    if not ch:
        raise HTTPException(422, "delivery_channel_id: no such channel")
    if ch.kind not in escalation.DELIVERY_KINDS:
        raise HTTPException(422, f"pages can be delivered by {', '.join(escalation.DELIVERY_KINDS)} channels, not {ch.kind}")
    for i, st in enumerate(body.steps):
        for t in st.targets:
            kind, tid = t.get("type"), str(t.get("id", ""))
            if kind == "schedule":
                if not s.execute(text("SELECT 1 FROM oncall_schedules WHERE id=CAST(:i AS uuid)"), {"i": tid}).first():
                    raise HTTPException(422, f"step {i + 1}: no such schedule {tid}")
            elif kind == "user":
                _members_ok(s, [tid])
            else:
                raise HTTPException(422, f"step {i + 1}: target type must be schedule or user")


def _write_steps(s, org_id, pid, body: PolicyIn) -> None:
    s.execute(text("DELETE FROM escalation_steps WHERE policy_id=:p"), {"p": str(pid)})
    for i, st in enumerate(body.steps):
        s.execute(text("INSERT INTO escalation_steps (org_id, policy_id, position, delay_min, targets) VALUES (:o, :p, :i, :d, CAST(:t AS jsonb))"),
                  {"o": str(org_id), "p": str(pid), "i": i, "d": st.delay_min,
                   "t": json.dumps([{"type": t["type"], "id": str(t["id"])} for t in st.targets])})


def _policy_out(s, row) -> dict:
    steps = [{"delay_min": r.delay_min, "targets": r.targets} for r in s.execute(text(
        "SELECT delay_min, targets FROM escalation_steps WHERE policy_id=:p ORDER BY position"), {"p": row.id}).all()]
    return {"id": str(row.id), "name": row.name, "repeat_count": row.repeat_count, "delivery_channel_id": str(row.delivery_channel_id), "steps": steps}


@router.get("/policies", response_model=list[PolicyOut])
def list_policies(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        return [_policy_out(s, r) for r in s.execute(text("SELECT id, name, repeat_count, delivery_channel_id FROM escalation_policies ORDER BY name")).all()]


@router.post("/policies", status_code=201)
def create_policy(body: PolicyIn, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        _gate(s, p.org_id)
        _validate_policy(s, body)
        if s.execute(text("SELECT 1 FROM escalation_policies WHERE name=:n"), {"n": body.name}).first():
            raise HTTPException(409, "a policy with this name exists")
        pid = s.execute(text("INSERT INTO escalation_policies (org_id, name, repeat_count, delivery_channel_id) VALUES (:o, :n, :r, :c) RETURNING id"),
                        {"o": str(p.org_id), "n": body.name, "r": body.repeat_count, "c": str(body.delivery_channel_id)}).scalar()
        _write_steps(s, p.org_id, pid, body)
        audit(s, p, "oncall.policy_create", "escalation_policy", str(pid), {"name": body.name}, request.client.host if request.client else None)
        return _policy_out(s, s.execute(text("SELECT id, name, repeat_count, delivery_channel_id FROM escalation_policies WHERE id=:i"), {"i": pid}).first())


@router.put("/policies/{pid}")
def replace_policy(pid: UUID, body: PolicyIn, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        _validate_policy(s, body)
        if not s.execute(text("UPDATE escalation_policies SET name=:n, repeat_count=:r, delivery_channel_id=:c WHERE id=:i"),
                         {"n": body.name, "r": body.repeat_count, "c": str(body.delivery_channel_id), "i": str(pid)}).rowcount:
            raise HTTPException(404)
        _write_steps(s, p.org_id, pid, body)
        audit(s, p, "oncall.policy_update", "escalation_policy", str(pid), {"name": body.name}, request.client.host if request.client else None)
        return _policy_out(s, s.execute(text("SELECT id, name, repeat_count, delivery_channel_id FROM escalation_policies WHERE id=:i"), {"i": str(pid)}).first())


@router.delete("/policies/{pid}", status_code=204)
def delete_policy(pid: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    with tenant_session(p.org_id) as s:
        from ..crypto import decrypt_json      # channel configs are encrypted: check each escalation channel
        for r in s.execute(text("SELECT name, config_enc FROM notification_channels WHERE kind='escalation'")).all():
            if decrypt_json(r.config_enc).get("policy_id") == str(pid):
                raise HTTPException(409, f"used by alert channel '{r.name}'; delete or repoint it first")
        if not s.execute(text("DELETE FROM escalation_policies WHERE id=:i"), {"i": str(pid)}).rowcount: raise HTTPException(404)
        audit(s, p, "oncall.policy_delete", "escalation_policy", str(pid), ip=request.client.host if request.client else None)


# ---- active escalations ---------------------------------------------------------------------------

@router.get("/escalations", response_model=list[RunOut])
def list_escalations(status: str = "active", p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        rows = s.execute(text("""SELECT r.id, r.status, r.step, r.cycle, r.next_at, r.history, r.created_at, r.incident_id, r.job_id,
                r.payload->>'job_name' AS job_name, r.payload->>'condition' AS condition, pol.name AS policy
            FROM escalation_runs r JOIN escalation_policies pol ON pol.id = r.policy_id
            WHERE (:st = 'all' OR r.status = :st) ORDER BY r.created_at DESC LIMIT 100"""), {"st": status}).all()
    return [dict(r._mapping) for r in rows]


@router.post("/escalations/{rid}/ack")
def ack_escalation(rid: UUID, request: Request, p: Principal = Depends(require_role("developer"))):
    with tenant_session(p.org_id) as s:
        ok = escalation.acknowledge(s, p.org_id, rid, str(p.user_id) if p.user_id else None)
        if not ok:
            raise HTTPException(409, "this escalation is not active (already acknowledged, resolved or finished)")
        audit(s, p, "oncall.acknowledge", "escalation_run", str(rid), ip=request.client.host if request.client else None)
    return {"status": "acknowledged"}


@router.get("/people", response_model=list[PersonOut])
def people(p: Principal = Depends(current_principal)):
    """Organization members, for choosing who is on a rotation or paged by a step."""
    with tenant_session(p.org_id) as s:
        return [{"id": str(r.id), "name": r.name or r.email, "email": r.email} for r in s.execute(text(
            "SELECT u.id, u.name, u.email FROM users u JOIN memberships m ON m.user_id = u.id ORDER BY coalesce(u.name, u.email)")).all()]


@router.post("/policies/{pid}/channel", status_code=201)
def policy_channel(pid: UUID, request: Request, p: Principal = Depends(require_role("devops"))):
    """Create the alert channel that routes alerts to this policy, so alert rules can target it."""
    from ..crypto import encrypt_json
    with tenant_session(p.org_id) as s:
        pol = s.execute(text("SELECT name FROM escalation_policies WHERE id=:i"), {"i": str(pid)}).first()
        if not pol: raise HTTPException(404)
        name = f"On call: {pol.name}"
        if s.execute(text("SELECT 1 FROM notification_channels WHERE name=:n"), {"n": name}).first():
            raise HTTPException(409, f"a channel called '{name}' already exists")
        cid = s.execute(text("INSERT INTO notification_channels (org_id, kind, name, config_enc, rate_per_min) VALUES (:o, 'escalation', :n, :c, 60) RETURNING id"),
                        {"o": str(p.org_id), "n": name, "c": encrypt_json({"policy_id": str(pid)})}).scalar()
        audit(s, p, "alert_channel.create", "notification_channel", str(cid), {"kind": "escalation", "policy": str(pid)},
              request.client.host if request.client else None)
    return {"id": str(cid), "name": name}
