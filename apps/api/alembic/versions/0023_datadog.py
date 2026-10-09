"""R58 — Datadog: alert channel, audit-log stream kind, and job-metrics export.

- notification channels may be kind 'datadog' (plan feature 'datadog' on Team and Business)
- audit_streams may be kind 'datadog_logs'
- metric_exports: per-org job-metrics export (kind 'datadog'); `cursor` is the end of the last window
  sent, so an outage delays metrics instead of losing them (feature 'metrics_export', Team and Business)
"""
from alembic import op

revision = "0023"; down_revision = "0022"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE audit_streams DROP CONSTRAINT IF EXISTS audit_streams_kind_check;
    ALTER TABLE audit_streams ADD CONSTRAINT audit_streams_kind_check CHECK (kind IN ('webhook', 'splunk_hec', 'datadog_logs'));
    CREATE TABLE metric_exports (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK (kind IN ('datadog')),
      name TEXT NOT NULL,
      config_enc BYTEA NOT NULL,
      enabled BOOLEAN NOT NULL DEFAULT true,
      cursor TIMESTAMPTZ NOT NULL DEFAULT now(),
      failures INTEGER NOT NULL DEFAULT 0,
      next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      last_success_at TIMESTAMPTZ,
      last_error TEXT,
      last_error_at TIMESTAMPTZ,
      points_total BIGINT NOT NULL DEFAULT 0,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX metric_exports_due ON metric_exports (next_attempt_at) WHERE enabled;
    ALTER TABLE metric_exports ENABLE ROW LEVEL SECURITY; ALTER TABLE metric_exports FORCE ROW LEVEL SECURITY;
    CREATE POLICY metric_exports_tenant ON metric_exports USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id());
    CREATE POLICY metric_exports_system ON metric_exports TO jobwatch_system USING (true) WITH CHECK (true);
    GRANT SELECT, INSERT, UPDATE, DELETE ON metric_exports TO jobwatch_app, jobwatch_system;
    """)
    for feat in ("datadog", "metrics_export"):
        op.execute(f"""UPDATE plan_limits SET features = array_append(features, '{feat}')
                       WHERE plan IN ('team', 'business') AND NOT ('{feat}' = ANY(features))""")


def downgrade():
    op.execute("DROP TABLE IF EXISTS metric_exports")
    op.execute("""ALTER TABLE audit_streams DROP CONSTRAINT IF EXISTS audit_streams_kind_check;
                  ALTER TABLE audit_streams ADD CONSTRAINT audit_streams_kind_check CHECK (kind IN ('webhook', 'splunk_hec'))""")
    for feat in ("datadog", "metrics_export"):
        op.execute(f"UPDATE plan_limits SET features = array_remove(features, '{feat}') WHERE plan IN ('team', 'business')")
