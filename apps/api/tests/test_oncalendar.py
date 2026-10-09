"""R53: systemd timer schedules → cron. Every translatable case is cross-checked against systemd itself
(`systemd-analyze calendar`): the next 15 elapse times must equal the translated cron's next 15 runs."""
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime

import pytest

from cronsentinel import oncalendar, schedule

BASE = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)     # a Saturday
SYSTEMD = shutil.which("systemd-analyze")

TRANSLATABLE = {
    "daily": "0 0 * * *", "hourly": "0 * * * *", "minutely": "* * * * *", "weekly": "0 0 * * 1",
    "monthly": "0 0 1 * *", "yearly": "0 0 1 1 *", "quarterly": "0 0 1 1,4,7,10 *", "semiannually": "0 0 1 1,7 *",
    "*-*-* 03:00:00": "0 3 * * *", "03:30": "30 3 * * *", "Mon..Fri 09:00": "0 9 * * 1,2,3,4,5",
    "Mon,Wed,Fri *-*-* 22:15": "15 22 * * 1,3,5", "Fri..Sun 06:00": "0 6 * * 0,5,6",
    "*:0/15": "0-59/15 * * * *", "*:*:00": "* * * * *", "*-*-01 02:00": "0 2 1 * *",
    "*-01,07-01 00:00:00": "0 0 1 1,7 *", "*-*-1/2 05:00": "0 5 1-31/2 * *", "8..17:00": "0 8-17 * * *",
    "*-*-* 0/2:30": "30 0-23/2 * * *", "*-*-* 00,12:00:00": "0 0,12 * * *",   # certbot.timer
}
REFUSED = {
    "2027-01-01 00:00": "a specific year", "*-*-* 03:00:30": "seconds", "*-02~1 00:00": "end of the month",
    "Mon *-*-01 00:00": "weekday and day of month", "hello": "unknown part", "Sat..Mon 06:00": "wraps",
}


@pytest.mark.parametrize("expr,cron", TRANSLATABLE.items())
def test_translates(expr, cron):
    assert oncalendar.calendar_to_cron(expr) == (cron, None)


@pytest.mark.parametrize("expr,reason", REFUSED.items())
def test_refuses_what_cron_cannot_express(expr, reason):
    with pytest.raises(oncalendar.Untranslatable, match=reason):
        oncalendar.calendar_to_cron(expr)


def _systemd_runs(expr: str, n: int) -> list[datetime]:
    out = subprocess.run([SYSTEMD, "calendar", f"--iterations={n}", "--base-time=2026-10-03 12:00:00 UTC", expr],
                         capture_output=True, text=True, env={**os.environ, "TZ": "UTC"}, check=True).stdout
    stamps = re.findall(r"(?:Next elapse|Iteration #\d+): \w{3} (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC", out)
    return [datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC) for s in stamps]


@pytest.mark.skipif(not SYSTEMD, reason="systemd-analyze not installed")
@pytest.mark.parametrize("expr", list(TRANSLATABLE) + ["Mon..Fri 09:00 Asia/Kolkata", "*-*-* 02:00 America/New_York"])
def test_matches_systemd_exactly(expr):
    cron, tz = oncalendar.calendar_to_cron(expr)
    ours = [r.astimezone(UTC) for r in schedule.next_runs(cron, tz or "UTC", 15, after=BASE)]
    assert ours == _systemd_runs(expr, 15), f"{expr} → {cron} ({tz})"


def test_agent_property_formats():
    t = oncalendar.translate("{ OnCalendar=Mon..Fri *-*-* 09:00:00 ; next_elapse=Mon 2026-10-05 09:00:00 UTC }")
    assert t["cron"] == "0 9 * * 1,2,3,4,5" and t["note"] is None
    twice = oncalendar.translate("{ OnCalendar=*-*-* 03:00:00 ; next_elapse=x } { OnCalendar=*-*-* 15:00:00 ; next_elapse=y }")
    assert twice["cron"] is None and "2 different" in twice["note"]
    m = oncalendar.translate("{ OnUnitActiveSec=15min ; next_elapse=x }")
    assert m["cron"] == "*/15 * * * *" and m["extra_grace_s"] == 900
    assert oncalendar.translate("{ OnUnitActiveSec=1h 30min ; next_elapse=x }")["cron"] is None
    boot = oncalendar.translate("{ OnBootSec=5min ; next_elapse=x }")
    assert boot["cron"] is None and "once" in boot["note"]
    assert oncalendar.translate("Mon..Fri 09:00 Europe/London")["tz"] == "Europe/London"
