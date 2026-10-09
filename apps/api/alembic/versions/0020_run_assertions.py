"""R54 — run metrics and assertions.

executions.metrics  numbers a run reported (size_bytes, rows, count …), at most 20 per run
jobs.assertions     rules a successful run must satisfy (cronsentinel/assertions.py); a broken rule
                    turns the run into a failure with the reasons in executions.meta.assertion_failures
"""
from alembic import op

revision = "0020"; down_revision = "0019"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("ALTER TABLE executions ADD COLUMN metrics JSONB NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE jobs ADD COLUMN assertions TEXT[] NOT NULL DEFAULT '{}'")


def downgrade():
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS assertions")
    op.execute("ALTER TABLE executions DROP COLUMN IF EXISTS metrics")
