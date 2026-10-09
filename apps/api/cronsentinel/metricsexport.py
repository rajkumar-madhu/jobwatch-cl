"""R58 — export job metrics to Datadog every minute.

Series (all tagged source:jobwatch, job, env, team, host where known, plus the export's own tags):
  jobwatch.run.duration    gauge, seconds, one point per finished run          + status:success|failed
  jobwatch.run.completed   count, 1 per finished run                           + status:success|failed
  jobwatch.run.missed      count, 1 per expected run that never started
  jobwatch.job.failing     gauge per job at export time: 1 if failing/missed/timed out, else 0
  jobwatch.job.reliability gauge per job: reliability score 0-100, when known

The window is (cursor, now − 30 s]; runs settle a few seconds after they finish. On failure the cursor
stays put and the export backs off, so an outage delays metrics instead of dropping them.
"""
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text

from . import datadog
from .crypto import decrypt_json

log = structlog.get_logger()
INTERVAL = timedelta(seconds=60)
SETTLE = timedelta(seconds=30)
MAX_WINDOW = timedelta(hours=6)          # after a long outage, catch up in 6-hour slices
MAX_JOBS = 5000
_FAILING = ("failed", "missed", "timeout")


def _tags(r, extra: list[str]) -> list[str]:
    t = ["source:jobwatch", datadog.tag("job", r.job)]
    if r.env: t.append(datadog.tag("env", r.env))
    if r.team: t.append(datadog.tag("team", r.team))
    if getattr(r, "host", None): t.append(datadog.tag("host", r.host))
    return t + extra


def build(s, org_id, start: datetime, end: datetime, extra_tags: list[str]) -> list[dict]:
    series: list[dict] = []
    runs = s.execute(text("""
        SELECT j.name AS job, e.name AS env, t.name AS team, COALESCE(x.host, srv.hostname) AS host,
               x.status::text AS status, x.duration_ms, x.agent_ts_end
        FROM executions x JOIN jobs j ON j.id = x.job_id
        LEFT JOIN environments e ON e.id = j.environment_id LEFT JOIN teams t ON t.id = j.team_id
        LEFT JOIN servers srv ON srv.id = j.server_id
        WHERE x.org_id = :o AND x.agent_ts_end > :a AND x.agent_ts_end <= :b AND x.status IN ('success', 'failed')"""),
        {"o": org_id, "a": start, "b": end}).all()
    for r in runs:
        tags = _tags(r, extra_tags) + [datadog.tag("status", r.status)]
        series.append(datadog.point("jobwatch.run.completed", 1, r.agent_ts_end, tags, datadog.COUNT))
        if r.duration_ms is not None:
            series.append(datadog.point("jobwatch.run.duration", r.duration_ms / 1000, r.agent_ts_end, tags))
    missed = s.execute(text("""
        SELECT j.name AS job, e.name AS env, t.name AS team, srv.hostname AS host, er.settled_at
        FROM expected_runs er JOIN jobs j ON j.id = er.job_id
        LEFT JOIN environments e ON e.id = j.environment_id LEFT JOIN teams t ON t.id = j.team_id
        LEFT JOIN servers srv ON srv.id = j.server_id
        WHERE er.org_id = :o AND er.state = 'missed' AND er.settled_at > :a AND er.settled_at <= :b"""),
        {"o": org_id, "a": start, "b": end}).all()
    for r in missed:
        series.append(datadog.point("jobwatch.run.missed", 1, r.settled_at, _tags(r, extra_tags), datadog.COUNT))
    jobs = s.execute(text("""
        SELECT j.name AS job, e.name AS env, t.name AS team, srv.hostname AS host, j.status::text AS status, j.reliability_score
        FROM jobs j LEFT JOIN environments e ON e.id = j.environment_id LEFT JOIN teams t ON t.id = j.team_id
        LEFT JOIN servers srv ON srv.id = j.server_id
        WHERE j.org_id = :o AND NOT j.paused ORDER BY j.name LIMIT :n"""), {"o": org_id, "n": MAX_JOBS}).all()
    for r in jobs:
        tags = _tags(r, extra_tags)
        series.append(datadog.point("jobwatch.job.failing", 1 if r.status in _FAILING else 0, end, tags))
        if r.reliability_score is not None:
            series.append(datadog.point("jobwatch.job.reliability", r.reliability_score, end, tags))
    return series


def tick(s, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    sent = 0
    for ex in s.execute(text("""SELECT id, org_id, config_enc, cursor, failures FROM metric_exports
            WHERE enabled AND next_attempt_at <= :now ORDER BY next_attempt_at LIMIT 50 FOR UPDATE SKIP LOCKED"""), {"now": now}).all():
        cfg = decrypt_json(ex.config_enc)
        end = min(now - SETTLE, ex.cursor + MAX_WINDOW)
        if end <= ex.cursor:
            continue
        extra = [t.strip() for t in (cfg.get("tags") or "").split(",") if t.strip()][:10]
        try:
            series = build(s, ex.org_id, ex.cursor, end, extra)
            datadog.send_series(cfg, series)
        except Exception as e:
            msg = str(e) if isinstance(e, datadog.DatadogError) else "could not reach Datadog"
            s.execute(text("""UPDATE metric_exports SET failures = failures + 1, last_error = :m, last_error_at = :now,
                              next_attempt_at = :next WHERE id = :id"""),
                      {"m": msg[:300], "now": now, "next": now + timedelta(seconds=min(30 * 2 ** min(ex.failures, 6), 900)), "id": ex.id})
            log.warning("metrics export failed", export=str(ex.id), error=msg)
            continue
        caught_up = end >= now - SETTLE - timedelta(seconds=1)
        s.execute(text("""UPDATE metric_exports SET cursor = :c, failures = 0, last_error = NULL, last_success_at = :now,
                          points_total = points_total + :n, next_attempt_at = :next WHERE id = :id"""),
                  {"c": end, "now": now, "n": len(series), "id": ex.id, "next": now + (INTERVAL if caught_up else timedelta(0))})
        sent += len(series)
    return sent
