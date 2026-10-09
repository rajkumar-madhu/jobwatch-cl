"""R48 — bring jobs over from another monitor. Healthchecks.io first (cronsentinel/importers)."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from .. import pingtokens, schedule
from ..auth import Principal, audit, require_role
from ..config import settings
from ..db import system_session, tenant_session
from ..importers import cronitor as cr
from ..importers import healthchecks as hc
from .heartbeat import invalidate_token

router = APIRouter(prefix="/api/v1/import", tags=["import"])


class HealthchecksIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=200)
    base_url: str = Field(default="https://healthchecks.io", max_length=300)
    workspace_id: UUID
    dry_run: bool = True


class CronitorIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=200, description="Cronitor API key with the monitor:read scope")
    telemetry_key: str | None = Field(default=None, max_length=100,
                                      description="The key your scripts use in cronitor.link URLs; keeps those URLs working")
    base_url: str = Field(default="https://cronitor.io", max_length=300)
    workspace_id: UUID
    dry_run: bool = True


class ImportedCheck(BaseModel):
    name: str
    status: str                      # imported | converted | needs_schedule | skipped | exists
    note: str | None = None
    job_id: UUID | None = None
    schedule_expr: str | None = None
    kept_ping_url: bool = False      # the Healthchecks UUID still works under /hc/
    new_ping_url: str | None = None  # shown once, when the UUID could not be kept


class ImportOut(BaseModel):
    dry_run: bool
    results: list[ImportedCheck]
    ping_host_change: str


def _apply(p: Principal, workspace_id: UUID, items: list[tuple[str, dict]], dry_run: bool, ingest: str) -> list[ImportedCheck]:
    """Shared by every importer (R50). `items` = (fallback name, mapped) where mapped is
    {'job': dict | None, 'token': str | None, 'status': str, 'note': str | None} from an importer's
    map function. The token, when given, is the source tool's ping credential: kept so existing
    ping URLs keep working through a compatible ingest path."""
    out: list[ImportedCheck] = []
    with tenant_session(p.org_id) as s:
        if not s.execute(text("SELECT 1 FROM workspaces WHERE id=:w"), {"w": str(workspace_id)}).first():
            raise HTTPException(422, "workspace_id not found in this organization")
        lim = s.execute(text("SELECT pl.max_jobs, (SELECT count(*) FROM jobs WHERE org_id=:o) AS n FROM organizations o "
                             "JOIN plan_limits pl ON pl.plan=o.plan WHERE o.id=:o"), {"o": str(p.org_id)}).first()
        room = None if not lim or lim.max_jobs is None else max(lim.max_jobs - lim.n, 0)
        existing = {r.name for r in s.execute(text("SELECT name FROM jobs WHERE workspace_id=:w"), {"w": str(workspace_id)})}
        for fallback, m in items:
            job = m["job"]
            res = ImportedCheck(name=(job or {}).get("name") or fallback or "unnamed check", status=m["status"], note=m["note"],
                                schedule_expr=(job or {}).get("schedule_expr"))
            if job is None:
                out.append(res); continue
            if job["name"] in existing:
                res.status, res.note = "exists", "a job with this name already exists in the workspace; left unchanged"
                out.append(res); continue
            if room is not None and room <= 0:
                res.status, res.note = "skipped", "plan job limit reached"
                out.append(res); continue
            raw = None
            if m["token"]:
                h = pingtokens.hash_token(m["token"])
                # Token hashes are unique across all orgs; RLS hides other orgs' jobs, so ask as the system
                # role. Only a yes/no leaves this check.
                with system_session() as sys_s:
                    taken = bool(sys_s.execute(text("SELECT 1 FROM jobs WHERE heartbeat_token_hash=:h"), {"h": h}).first())
                if not taken:
                    tok_hash, hint = h, pingtokens.hint(m["token"])
                    res.kept_ping_url = True
            if not res.kept_ping_url:
                raw, tok_hash, hint = pingtokens.new_token()
            if dry_run:
                res.new_ping_url = None if res.kept_ping_url else "(issued on import)"
                out.append(res)
                if room is not None: room -= 1
                existing.add(job["name"]); continue
            nxt = schedule.next_run(job["schedule_expr"], job["tz"]) if job["schedule_expr"] else None
            jid = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, description, kind, heartbeat_token_hash, heartbeat_token_hint,
                    schedule_expr, tz, grace_s, expected_runtime_s, tags, paused, status, next_expected_at, owner_id, assertions)
                VALUES (:o, :w, :n, :d, 'heartbeat', :h, :hint, :e, :tz, :g, :rt, :tags, :paused, :st, :nxt, :owner, :rules) RETURNING id"""),
                {"o": str(p.org_id), "w": str(workspace_id), "n": job["name"], "d": job["description"], "h": tok_hash, "hint": hint,
                 "e": job["schedule_expr"], "tz": job["tz"], "g": job["grace_s"], "rt": job.get("expected_runtime_s"), "tags": job["tags"],
                 "paused": job["paused"], "st": "healthy" if job["schedule_expr"] else "unknown", "nxt": nxt,
                 "owner": str(p.user_id) if p.user_id else None, "rules": job.get("assertions") or []}).scalar()
            res.job_id = jid
            invalidate_token(tok_hash)   # drop any cached "unknown token" answer from pings sent before the import
            if raw:
                res.new_ping_url = f"{ingest}/ping/{raw}"
            existing.add(job["name"])
            if room is not None: room -= 1
            out.append(res)
    return out


def _ingest() -> str:
    return (settings.ingest_public_url or settings.api_public_url).rstrip("/")


def _audit(p: Principal, request: Request, action: str, n: int, out: list[ImportedCheck]) -> None:
    with tenant_session(p.org_id) as s:
        audit(s, p, action, "org", str(p.org_id), {"checks": n, "created": sum(1 for r in out if r.job_id)},
              request.client.host if request.client else None)


@router.post("/healthchecks", response_model=ImportOut)
def import_healthchecks(body: HealthchecksIn, request: Request, p: Principal = Depends(require_role("admin"))):
    try:
        checks = hc.fetch(body.base_url, body.api_key)
    except hc.ImportError_ as e:
        raise HTTPException(400, str(e)) from None
    out = _apply(p, body.workspace_id, [(c.get("name"), hc.map_check(c)) for c in checks], body.dry_run, _ingest())
    if not body.dry_run:
        _audit(p, request, "import.healthchecks", len(checks), out)
    return ImportOut(dry_run=body.dry_run, results=out,
                     ping_host_change=f"In your scripts, replace https://hc-ping.com/ with {_ingest()}/hc/ (paths like /<uuid>/start, /fail and /<exit code> keep working).")


@router.post("/cronitor", response_model=ImportOut)
def import_cronitor(body: CronitorIn, request: Request, p: Principal = Depends(require_role("admin"))):
    """R50. Neither key is stored: the API key is used for this request only; the telemetry key exists
    afterwards only inside each job's hashed ping token."""
    if body.telemetry_key is not None and not cr.KEY_RE.fullmatch(body.telemetry_key):
        raise HTTPException(422, "telemetry_key has unexpected characters")
    try:
        monitors = cr.fetch(body.api_key, body.base_url)
    except cr.ImportError_ as e:
        raise HTTPException(400, str(e)) from None
    out = _apply(p, body.workspace_id, [(m.get("name") or m.get("key"), cr.map_monitor(m, body.telemetry_key)) for m in monitors],
                 body.dry_run, _ingest())
    if not body.dry_run:
        _audit(p, request, "import.cronitor", len(monitors), out)
    hint = (f"In your scripts, replace https://cronitor.link/ with {_ingest()}/cronitor/ (the /p/<key>/<monitor> paths, state=, series= "
            "and metric= keep working)." if body.telemetry_key else
            "No telemetry key was given, so every job has a new ping URL (listed below, shown once).")
    return ImportOut(dry_run=body.dry_run, results=out, ping_host_change=hint)
