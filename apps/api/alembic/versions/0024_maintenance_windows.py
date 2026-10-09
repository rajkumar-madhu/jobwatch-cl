"""R63 — maintenance windows get a name, a time zone (recurrences expand in it) and authorship.

Recurrence (`rrule`) existed since 0001 but was never expanded; cronsentinel/maintenance.py now does,
for the alert engine, the reconciler and status pages alike.
"""
from alembic import op

revision = "0024"; down_revision = "0023"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE maintenance_windows ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT 'Maintenance';
    ALTER TABLE maintenance_windows ADD COLUMN IF NOT EXISTS tz TEXT NOT NULL DEFAULT 'UTC';
    ALTER TABLE maintenance_windows ADD COLUMN IF NOT EXISTS created_by UUID;
    ALTER TABLE maintenance_windows ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
    ALTER TABLE maintenance_windows ADD CONSTRAINT maintenance_windows_order CHECK (ends_at > starts_at) NOT VALID;
    """)


def downgrade():
    op.execute("""
    ALTER TABLE maintenance_windows DROP CONSTRAINT IF EXISTS maintenance_windows_order;
    ALTER TABLE maintenance_windows DROP COLUMN IF EXISTS created_at, DROP COLUMN IF EXISTS created_by,
      DROP COLUMN IF EXISTS tz, DROP COLUMN IF EXISTS name;
    """)
