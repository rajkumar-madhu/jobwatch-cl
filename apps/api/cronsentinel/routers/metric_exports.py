"""R58 — job-metrics exports (Datadog). Delivery: cronsentinel/metricsexport.py."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from .. import datadog
from ..auth import Principal, audit, require_role
from ..crypto import decrypt_json, encrypt_json
from ..db import tenant_session

router = APIRouter(prefix="/api/v1/metric-exports", tags=["integrations"])


class ExportIn(BaseModel):
    name: str = Field(default="Datadog", min_length=1, max_length=80)
    api_key: str = Field(max_length=64)
    site: str = Field(default="datadoghq.com", max_length=40)
    tags: str | None = Field(default=None, max_length=300, description="extra tags for every series, comma separated")


class ExportOut(BaseModel):
    id: UUID
    kind: str
    name: str
    site: str
    enabled: bool
    points_total: int
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    next_attempt_at: datetime


class EnabledIn(BaseModel):
    enabled: bool


def _gate(s, org_id) -> None:
    feats = s.execute(text("SELECT pl.features FROM organizations o JOIN plan_limits pl ON pl.plan=o.plan WHERE o.id=:o"), {"o": str(org_id)}).scalar() or []
    if "metrics_export" not in feats and "all" not in feats:
        raise HTTPException(402, "metrics export is available on the Team, Business and Enterprise plans")


def _list(s) -> list[ExportOut]:
    return [ExportOut(id=r.id, kind=r.kind, name=r.name, site=decrypt_json(r.config_enc).get("site", "datadoghq.com"), enabled=r.enabled,
                      points_total=r.points_total, last_success_at=r.last_success_at, last_error=r.last_error,
                      last_error_at=r.last_error_at, next_attempt_at=r.next_attempt_at)
            for r in s.execute(text("""SELECT id, kind, name, config_enc, enabled, points_total, last_success_at, last_error,
                                              last_error_at, next_attempt_at FROM metric_exports ORDER BY created_at""")).all()]


@router.get("", response_model=list[ExportOut])
def list_exports(p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        return _list(s)


@router.post("", response_model=ExportOut, status_code=201)
def create_export(body: ExportIn, request: Request, p: Principal = Depends(require_role("admin"))):
    cfg = {"api_key": body.api_key, "site": body.site, "tags": body.tags}
    if (err := datadog.validate(cfg)):
        raise HTTPException(422, err)
    with tenant_session(p.org_id) as s:
        _gate(s, p.org_id)
        eid = s.execute(text("INSERT INTO metric_exports (org_id, kind, name, config_enc) VALUES (:o, 'datadog', :n, :c) RETURNING id"),
                        {"o": str(p.org_id), "n": body.name, "c": encrypt_json(cfg)}).scalar()
        audit(s, p, "metric_export.create", "metric_export", str(eid), {"kind": "datadog", "site": body.site},
              request.client.host if request.client else None)
        return next(x for x in _list(s) if x.id == eid)


@router.patch("/{export_id}", response_model=ExportOut)
def set_enabled(export_id: UUID, body: EnabledIn, request: Request, p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        # resuming after a long pause starts from now: a backlog of stale gauges is not useful
        n = s.execute(text("""UPDATE metric_exports SET enabled=:e, failures=0, next_attempt_at=now(),
                              cursor = CASE WHEN :e AND NOT enabled THEN now() ELSE cursor END WHERE id=:i"""),
                      {"e": body.enabled, "i": str(export_id)}).rowcount
        if not n: raise HTTPException(404)
        audit(s, p, "metric_export.enable" if body.enabled else "metric_export.disable", "metric_export", str(export_id),
              ip=request.client.host if request.client else None)
        return next(x for x in _list(s) if x.id == export_id)


@router.delete("/{export_id}", status_code=204)
def delete_export(export_id: UUID, request: Request, p: Principal = Depends(require_role("admin"))):
    with tenant_session(p.org_id) as s:
        if not s.execute(text("DELETE FROM metric_exports WHERE id=:i"), {"i": str(export_id)}).rowcount: raise HTTPException(404)
        audit(s, p, "metric_export.delete", "metric_export", str(export_id), ip=request.client.host if request.client else None)


@router.post("/{export_id}/test")
def test_export(export_id: UUID, p: Principal = Depends(require_role("admin"))):
    """Checks the API key with Datadog's /api/v1/validate."""
    with tenant_session(p.org_id) as s:
        r = s.execute(text("SELECT config_enc FROM metric_exports WHERE id=:i"), {"i": str(export_id)}).first()
        if not r: raise HTTPException(404)
    try:
        datadog.check_key(decrypt_json(r.config_enc))
    except datadog.DatadogError as e:
        return {"ok": False, "error": str(e)}
    except Exception:
        return {"ok": False, "error": "could not reach Datadog"}
    return {"ok": True}
