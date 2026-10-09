"""R65 — configure monitoring on the CronJob itself with jobwatch.io/* annotations.

    metadata:
      annotations:
        jobwatch.io/name: "Billing report"
        jobwatch.io/grace: "10m"
        jobwatch.io/expected-runtime: "2h"
        jobwatch.io/tags: "billing, prod"
        jobwatch.io/run-checks: "duration < 2h; metric.rows > 0"
        jobwatch.io/description: "Nightly invoice export"
        jobwatch.io/ignore: "true"

An annotation that is present is the source of truth (like a crontab line for the Linux agent); an
absent one leaves edits made in the app alone. Invalid values are not applied: each produces a
warning that the Kubernetes page shows next to the CronJob. The agent sends only jobwatch.io/* keys.
"""
import re
from dataclasses import dataclass, field

from . import assertions

PREFIX = "jobwatch.io/"
KNOWN = {"name", "grace", "expected-runtime", "tags", "run-checks", "description", "ignore"}
_SPAN = re.compile(r"(\d+)\s*(s|m|h|d)?", re.I)
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, None: 1}
_TAG = re.compile(r"[a-z0-9][a-z0-9_.:/-]{0,49}")
IGNORED_TAG = "k8s-ignored"


@dataclass
class Settings:
    ignore: bool = False
    name: str | None = None
    grace_s: int | None = None
    expected_runtime_s: int | None = None
    tags: list[str] | None = None
    run_checks: list[str] | None = None
    description: str | None = None
    warnings: list[str] = field(default_factory=list)


def duration_s(v: str) -> int | None:
    """'90', '10m', '2h', '1h30m', '1d' → seconds; None if not a duration."""
    v = v.strip().lower()
    if not v or not re.fullmatch(r"(\d+\s*[smhd]?\s*)+", v):
        return None
    return sum(int(n) * _UNIT[(u or None) and u.lower()] for n, u in _SPAN.findall(v))


def parse(annotations: dict | None) -> Settings:
    out = Settings()
    for key, raw in (annotations or {}).items():
        if not key.startswith(PREFIX):
            continue
        k, v = key[len(PREFIX):], str(raw).strip()
        if k not in KNOWN:
            out.warnings.append(f"{key}: unknown annotation, ignored")
            continue
        if k == "ignore":
            if v.lower() in ("true", "yes", "1"):
                out.ignore = True
            elif v.lower() not in ("false", "no", "0", ""):
                out.warnings.append(f"{key}: expected true or false")
        elif k == "name":
            if 1 <= len(v) <= 200:
                out.name = v
            else:
                out.warnings.append(f"{key}: must be 1 to 200 characters")
        elif k in ("grace", "expected-runtime"):
            sec = duration_s(v)
            limit = 7 * 86400
            if sec is None:
                out.warnings.append(f"{key}: '{v}' is not a duration (e.g. 90, 10m, 2h, 1h30m)")
            elif not (0 if k == "grace" else 1) <= sec <= limit:
                out.warnings.append(f"{key}: must be between {'0' if k == 'grace' else '1s'} and 7 days")
            elif k == "grace":
                out.grace_s = sec
            else:
                out.expected_runtime_s = sec
        elif k == "tags":
            tags, bad = [], []
            for t in (x.strip().lower() for x in v.split(",")):
                if not t:
                    continue
                (tags if _TAG.fullmatch(t) else bad).append(t)
            if bad:
                out.warnings.append(f"{key}: invalid tags ignored: {', '.join(bad)}")
            out.tags = tags[:20]
        elif k == "run-checks":
            rules = [r.strip() for r in v.split(";") if r.strip()]
            try:
                out.run_checks = assertions.validate(rules)
            except assertions.InvalidAssertion as e:
                out.warnings.append(f"{key}: {e}")
        elif k == "description":
            out.description = v[:1000]
    return out
