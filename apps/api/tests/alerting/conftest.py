"""Shared fakes for the alerting tests. No Postgres, Redis or Celery broker needed.

- Settings that keys.py would otherwise refuse are set before any cronsentinel import.
- `cronsentinel.schedule` is stubbed only when the module is absent (it is missing from the
  partial project copy; alerting/context.py imports it at module load).
- FakeSession answers `s.execute(text(...))` by matching SQL fragments, and records every
  statement so tests can assert what the engine wrote.
- Both module-level Redis clients (engine._r, tasks._r) are swapped for fakeredis.

Known bugs from reviews/jobwatch-code-review-2026-10-10.md are pinned with
`pytest.mark.xfail(strict=True, reason="review #N ...")`: they pass today by failing, and turn
red (XPASS) once the fix lands, so the marker gets removed together with the bug.
"""
import importlib
import os
import sys
import types
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[2]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("ALLOW_INSECURE_SECRET", "true")
os.environ.setdefault("SECRET_ENCRYPTION_KEY", "test-only-" + "x" * 48)

try:
    importlib.import_module("cronsentinel.schedule")
except ModuleNotFoundError:
    stub = types.ModuleType("cronsentinel.schedule")
    stub.human = lambda expr: f"schedule {expr}"
    sys.modules["cronsentinel.schedule"] = stub


class Row:
    """Attribute + mapping access, like a SQLAlchemy Row."""

    def __init__(self, **kw):
        self.__dict__.update(kw)
        self._mapping = kw

    def __getitem__(self, i):
        return list(self._mapping.values())[i]


class Result:
    def __init__(self, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    @property
    def rowcount(self):
        return len(self._rows)

    def scalar(self):
        if self._scalar is not None:
            return self._scalar
        return self._rows[0][0] if self._rows else None


class FakeSession:
    """`responders` is a list of (sql_fragment, fn(params) -> Result | rows | scalar).
    The first responder whose fragment occurs in the statement wins; unmatched statements
    return an empty Result. Every call is kept in `calls` as (sql, params)."""

    def __init__(self, responders=()):
        self.responders = list(responders)
        self.calls: list[tuple[str, dict]] = []

    def on(self, fragment, value):
        self.responders.insert(0, (fragment, value))
        return self

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        self.calls.append((sql, params))
        for frag, val in self.responders:
            if frag in sql:
                v = val(params) if callable(val) else val
                if isinstance(v, Result):
                    return v
                if isinstance(v, list):
                    return Result(rows=v)
                return Result(scalar=v)
        return Result()

    def begin_nested(self):
        """Savepoint stand-in: a no-op context manager (the engine runs each rule in one)."""
        from contextlib import nullcontext
        return nullcontext()

    def statements(self, fragment):
        return [(sql, p) for sql, p in self.calls if fragment in sql]


@pytest.fixture
def fake_redis(monkeypatch):
    import fakeredis

    r = fakeredis.FakeRedis(decode_responses=True)
    from cronsentinel.alerting import engine, tasks

    monkeypatch.setattr(engine, "_r", r)
    monkeypatch.setattr(tasks, "_r", r)
    return r


@pytest.fixture
def use_session(monkeypatch):
    """Point system_session() in engine and tasks at a given FakeSession."""
    from contextlib import contextmanager

    from cronsentinel.alerting import engine, tasks

    def install(sess):
        @contextmanager
        def _cm():
            yield sess

        monkeypatch.setattr(engine, "system_session", _cm)
        monkeypatch.setattr(tasks, "system_session", _cm)
        return sess

    return install
