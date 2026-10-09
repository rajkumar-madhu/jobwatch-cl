"""R63: the incident poll runs against the real schema (it used a column that does not exist)."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import text


def test_poll_incidents_reads_real_events(org, monkeypatch):
    from cronsentinel.db import system_session
    from cronsentinel.workers import outbound_exporter as ox
    sent = []
    monkeypatch.setattr(ox, "fanout", lambda s, sig: sent.append(sig) or 1)
    since = datetime.now(UTC) - timedelta(seconds=5)
    with system_session() as s:
        inc = s.execute(text("""INSERT INTO incidents (org_id, title, severity, status) VALUES (:o, 'Backups failing', 'high', 'open') RETURNING id"""),
                        {"o": org["id"]}).scalar()
        s.execute(text("INSERT INTO incident_events (org_id, incident_id, kind) VALUES (:o, :i, 'opened')"), {"o": org["id"], "i": inc})
        s.execute(text("INSERT INTO incident_events (org_id, incident_id, kind) VALUES (:o, :i, 'resolved')"), {"o": org["id"], "i": inc})
    with system_session() as s:
        n, last = ox.poll_incidents(s, since)
    assert n == 2 and last > since
    kinds = [getattr(sig, "kind", None) or getattr(sig, "event_type", None) or str(sig) for sig in sent]
    assert len(kinds) == 2



def test_every_exporter_poll_runs_against_the_real_schema(org, monkeypatch):
    """Each poll is executed for real, so a query against a column that does not exist fails here."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import outbound_exporter as ox
    monkeypatch.setattr(ox, "fanout", lambda s, sig: 1)
    since = datetime.now(UTC) - timedelta(days=1)
    with system_session() as s:
        ox.poll_slots(s, since)
        ox.poll_incidents(s, since)
        ox.poll_agents(s)
