"""R57 — stream the audit log to a SIEM (signed HTTPS webhook or Splunk HTTP Event Collector).

audit_streams  per-org destinations. `cursor` is the last audit_logs.id delivered: delivery is
               at-least-once and in order (receivers dedupe on the event id). On failure the cursor
               stays put and `next_attempt_at` backs off; the error is kept for the settings page.
Plan feature 'audit_stream' on Business (Enterprise has 'all').
"""
from alembic import op

revision = "0022"; down_revision = "0021"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE audit_streams (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK (kind IN ('webhook', 'splunk_hec')),
      name TEXT NOT NULL,
      config_enc BYTEA NOT NULL,
      enabled BOOLEAN NOT NULL DEFAULT true,
      cursor BIGINT NOT NULL DEFAULT 0,
      failures INTEGER NOT NULL DEFAULT 0,
      next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      last_success_at TIMESTAMPTZ,
      last_error TEXT,
      last_error_at TIMESTAMPTZ,
      delivered_total BIGINT NOT NULL DEFAULT 0,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX audit_streams_due ON audit_streams (next_attempt_at) WHERE enabled;
    CREATE INDEX IF NOT EXISTS audit_logs_org_id_idx ON audit_logs (org_id, id);
    ALTER TABLE audit_streams ENABLE ROW LEVEL SECURITY; ALTER TABLE audit_streams FORCE ROW LEVEL SECURITY;
    CREATE POLICY audit_streams_tenant ON audit_streams USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id());
    CREATE POLICY audit_streams_system ON audit_streams TO jobwatch_system USING (true) WITH CHECK (true);
    GRANT SELECT, INSERT, UPDATE, DELETE ON audit_streams TO jobwatch_app, jobwatch_system;
    UPDATE plan_limits SET features = array_append(features, 'audit_stream') WHERE plan = 'business' AND NOT ('audit_stream' = ANY(features));
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS audit_streams; DROP INDEX IF EXISTS audit_logs_org_id_idx;")
    op.execute("UPDATE plan_limits SET features = array_remove(features, 'audit_stream') WHERE plan = 'business'")
