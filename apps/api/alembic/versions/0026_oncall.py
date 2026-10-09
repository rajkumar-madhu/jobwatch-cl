"""R66 — on-call schedules and escalation policies.

oncall_schedules   a named schedule in a time zone
oncall_layers      rotations (daily / weekly / every N hours) over ordered members from a handoff time,
                   optionally restricted to weekdays and a time of day; higher position wins
oncall_overrides   someone covers [starts_at, ends_at); beats every layer
escalation_policies / escalation_steps
                   steps page schedules or people, waiting `delay_min` for an acknowledgement before
                   the next step; the whole policy repeats `repeat_count` times; pages are delivered
                   through an existing notification channel
escalation_runs    one per paged alert: where it is in the policy, when it moves next, who was paged
Plan feature 'oncall' on Team and Business (Enterprise has everything).
"""
from alembic import op

revision = "0026"; down_revision = "0025"; branch_labels = None; depends_on = None

TABLES = ["oncall_schedules", "oncall_layers", "oncall_overrides", "escalation_policies", "escalation_steps", "escalation_runs"]


def upgrade():
    op.execute("""
    CREATE TABLE oncall_schedules (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      name TEXT NOT NULL, tz TEXT NOT NULL DEFAULT 'UTC', description TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE (org_id, name));
    CREATE TABLE oncall_layers (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      schedule_id UUID NOT NULL REFERENCES oncall_schedules(id) ON DELETE CASCADE,
      position INTEGER NOT NULL, name TEXT NOT NULL,
      rotation TEXT NOT NULL CHECK (rotation IN ('daily', 'weekly', 'custom')),
      shift_hours INTEGER CHECK (shift_hours IS NULL OR shift_hours BETWEEN 1 AND 720),
      handoff_at TIMESTAMPTZ NOT NULL,
      members UUID[] NOT NULL,
      restrict_days INTEGER[],                 -- 0 = Monday … 6 = Sunday; NULL = every day
      restrict_start TEXT, restrict_end TEXT,  -- 'HH:MM' local; end may be before start (overnight)
      UNIQUE (schedule_id, position));
    CREATE TABLE oncall_overrides (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      schedule_id UUID NOT NULL REFERENCES oncall_schedules(id) ON DELETE CASCADE,
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      starts_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ NOT NULL CHECK (ends_at > starts_at),
      created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
    CREATE TABLE escalation_policies (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      name TEXT NOT NULL, repeat_count INTEGER NOT NULL DEFAULT 0 CHECK (repeat_count BETWEEN 0 AND 9),
      delivery_channel_id UUID NOT NULL REFERENCES notification_channels(id) ON DELETE RESTRICT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE (org_id, name));
    CREATE TABLE escalation_steps (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      policy_id UUID NOT NULL REFERENCES escalation_policies(id) ON DELETE CASCADE,
      position INTEGER NOT NULL, delay_min INTEGER NOT NULL CHECK (delay_min BETWEEN 1 AND 1440),
      targets JSONB NOT NULL,                  -- [{"type": "schedule"|"user", "id": "…"}]
      UNIQUE (policy_id, position));
    CREATE TABLE escalation_runs (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      policy_id UUID NOT NULL REFERENCES escalation_policies(id) ON DELETE CASCADE,
      job_id UUID, incident_id UUID, dedup_key TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'acknowledged', 'resolved', 'exhausted')),
      step INTEGER NOT NULL DEFAULT 0, cycle INTEGER NOT NULL DEFAULT 0,
      next_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      payload JSONB NOT NULL, history JSONB NOT NULL DEFAULT '[]',
      acknowledged_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
    CREATE UNIQUE INDEX escalation_runs_one_active ON escalation_runs (org_id, policy_id, dedup_key) WHERE status = 'active';
    CREATE INDEX escalation_runs_due ON escalation_runs (next_at) WHERE status = 'active';
    """)
    for t in TABLES:
        op.execute(f"""ALTER TABLE {t} ENABLE ROW LEVEL SECURITY; ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
            CREATE POLICY {t}_tenant ON {t} USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id());
            CREATE POLICY {t}_system ON {t} TO jobwatch_system USING (true) WITH CHECK (true);
            GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO jobwatch_app, jobwatch_system;""")
    for feat in ("oncall", "escalation"):   # schedules/policies; the 'escalation' alert-channel kind
        op.execute(f"""UPDATE plan_limits SET features = array_append(features, '{feat}')
                       WHERE plan IN ('team', 'business') AND NOT ('{feat}' = ANY(features))""")


def downgrade():
    for t in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {t}")
    for feat in ("oncall", "escalation"):
        op.execute(f"UPDATE plan_limits SET features = array_remove(features, '{feat}') WHERE plan IN ('team', 'business')")
