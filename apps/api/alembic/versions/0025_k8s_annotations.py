"""R65 — warnings from jobwatch.io/* annotations, shown next to the CronJob on the Kubernetes page."""
from alembic import op

revision = "0025"; down_revision = "0024"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("ALTER TABLE k8s_cronjobs ADD COLUMN IF NOT EXISTS annotation_warnings TEXT[] NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE k8s_cronjobs ADD COLUMN IF NOT EXISTS time_zone TEXT")


def downgrade():
    op.execute("ALTER TABLE k8s_cronjobs DROP COLUMN IF EXISTS annotation_warnings, DROP COLUMN IF EXISTS time_zone")
