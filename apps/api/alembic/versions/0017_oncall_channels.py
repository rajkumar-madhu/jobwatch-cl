"""R42 — PagerDuty and Opsgenie channels exist now (cronsentinel/alerting/oncall.py), so the plans
that sell them may list them again. 0015 removed them from Business because nothing could send them;
they return for Team and Business together with a real sender."""
from alembic import op

revision = "0017"; down_revision = "0016"; branch_labels = None; depends_on = None


def upgrade():
    for kind in ("pagerduty", "opsgenie"):
        op.execute(f"""UPDATE plan_limits SET features = array_append(features, '{kind}')
                       WHERE plan IN ('team', 'business') AND NOT ('{kind}' = ANY(features))""")


def downgrade():
    for kind in ("pagerduty", "opsgenie"):
        op.execute(f"UPDATE plan_limits SET features = array_remove(features, '{kind}') WHERE plan IN ('team', 'business')")
