"""Agent event ingest hardening (code review 2026-10-10, findings #3 and #4)."""
import pytest

from cronsentinel.processor import _int_or_none
from cronsentinel.routers.agents import _MAX_TAIL, AgentEvent, _parse_events, _token_allowed

GOOD = {"kind": "success", "fingerprint": "fp", "execution_id": "e1", "sequence": 1,
        "agent_ts": "2026-10-13T06:00:00Z", "duration_ms": 1200, "exit_code": 0, "host": "h1",
        "meta": {"command": "backup.sh"}, "env_var_names": ["PATH"], "metrics": {"rows": 3}}


@pytest.mark.parametrize("bad", [
    {**GOOD, "meta": ["not", "a", "dict"]},
    {**GOOD, "meta": {"nested": {"x": 1}}},
    {**GOOD, "duration_ms": "fast"},
    {**GOOD, "duration_ms": -5},
    {**GOOD, "sequence": "first"},
    {**GOOD, "sequence": 2**40},
    {**GOOD, "exit_code": "boom"},
    {**GOOD, "exit_code": 1.5},
    {**GOOD, "execution_id": "x" * 500},
    {**GOOD, "kind": None},
    {**GOOD, "env_var_names": "PATH"},
    "not an object",
    None,
])
def test_one_bad_event_is_dropped_alone(bad):
    good, invalid = _parse_events([GOOD, bad, {**GOOD, "execution_id": "e2"}])
    assert invalid == 1
    assert [e.execution_id for e in good] == ["e1", "e2"]


def test_go_agent_event_shape_is_accepted():
    good, invalid = _parse_events([GOOD, {"kind": "start", "job_token": "t", "execution_id": "e", "sequence": 0,
                                          "agent_ts": "2026-10-13T06:00:00.123456789Z", "monotonic_ns": 5, "host": "h"}])
    assert invalid == 0 and len(good) == 2


def test_oversized_tails_are_trimmed_to_their_end():
    e = AgentEvent.model_validate({**GOOD, "stderr_tail": "a" * (3 << 20) + "END", "stdout_tail": None})
    assert len(e.stderr_tail) == _MAX_TAIL and e.stderr_tail.endswith("END")


def _ev(**meta):
    return AgentEvent.model_validate({**GOOD, "job_token": "tok", "meta": meta})


def test_token_from_root_or_buffer_is_trusted():
    assert _token_allowed(_ev(), "root")             # written to the root-only buffer
    assert _token_allowed(_ev(spool_uid="0", spool_user="root"), "root")


def test_token_from_other_users_spool_only_for_their_own_jobs():
    assert not _token_allowed(_ev(spool_uid="1001", spool_user="mallory"), "root")
    assert not _token_allowed(_ev(spool_uid="1001", spool_user="mallory"), None)
    assert _token_allowed(_ev(spool_uid="1001", spool_user="alice"), "alice")


@pytest.mark.parametrize("v,want", [(5, 5), ("7", 7), ("x", None), (None, None), (True, None), (2**40, None), (1.9, 1)])
def test_int_or_none(v, want):
    assert _int_or_none(v) == want
