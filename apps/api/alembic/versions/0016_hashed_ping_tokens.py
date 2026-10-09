"""R41 — store heartbeat tokens as SHA-256 hashes (see cronsentinel/pingtokens.py).

Existing tokens keep working: their hash is computed here, so every ping URL already configured in a
crontab, CI job or agent stays valid. What changes is that the plaintext is gone — the UI can no
longer re-display an existing URL; users who need it again rotate the token.
"""
from alembic import op

revision = "0016"; down_revision = "0015"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE jobs ADD COLUMN heartbeat_token_hash TEXT, ADD COLUMN heartbeat_token_hint TEXT;
        UPDATE jobs SET heartbeat_token_hash = encode(sha256(convert_to(heartbeat_token, 'UTF8')), 'hex'),
                        heartbeat_token_hint = right(heartbeat_token, 4);
        ALTER TABLE jobs ALTER COLUMN heartbeat_token_hash SET NOT NULL;
        CREATE UNIQUE INDEX jobs_heartbeat_token_hash_key ON jobs (heartbeat_token_hash);
        ALTER TABLE jobs DROP COLUMN heartbeat_token;
    """)


def downgrade():
    # The plaintext cannot be recovered. Downgrading issues fresh random tokens: every ping URL in use
    # stops working until it is re-copied from the UI. Deliberate — never fabricate a match.
    op.execute("""
        ALTER TABLE jobs ADD COLUMN heartbeat_token TEXT;
        UPDATE jobs SET heartbeat_token = replace(gen_random_uuid()::text, '-', '');
        ALTER TABLE jobs ALTER COLUMN heartbeat_token SET NOT NULL;
        ALTER TABLE jobs ADD CONSTRAINT jobs_heartbeat_token_key UNIQUE (heartbeat_token);
        DROP INDEX IF EXISTS jobs_heartbeat_token_hash_key;
        ALTER TABLE jobs DROP COLUMN heartbeat_token_hash, DROP COLUMN heartbeat_token_hint;
    """)
