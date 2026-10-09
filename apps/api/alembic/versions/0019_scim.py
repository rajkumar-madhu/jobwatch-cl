"""R44 — SCIM 2.0 provisioning.

org_scim_tokens  bearer tokens an identity provider uses to call /scim/v2 for one org (hashed).
scim_users       SCIM's view of a person in an org: externalId, active flag, and the role to restore
                 on reactivation. Access is still decided only by `memberships`: deactivating a SCIM
                 user deletes the membership (so every session and token loses the org immediately),
                 and reactivating re-creates it. No access check anywhere had to learn about SCIM.
"""
from alembic import op

revision = "0019"; down_revision = "0018"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE org_scim_tokens (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      name TEXT NOT NULL,
      prefix TEXT NOT NULL,
      token_hash TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      last_used_at TIMESTAMPTZ,
      revoked_at TIMESTAMPTZ
    );
    CREATE INDEX org_scim_tokens_prefix ON org_scim_tokens (prefix) WHERE revoked_at IS NULL;
    CREATE TABLE scim_users (
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      external_id TEXT,
      active BOOLEAN NOT NULL DEFAULT true,
      role member_role NOT NULL DEFAULT 'viewer',
      given_name TEXT, family_name TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (org_id, user_id)
    );
    CREATE UNIQUE INDEX scim_users_external ON scim_users (org_id, external_id) WHERE external_id IS NOT NULL;

    ALTER TABLE org_scim_tokens ENABLE ROW LEVEL SECURITY; ALTER TABLE org_scim_tokens FORCE ROW LEVEL SECURITY;
    ALTER TABLE scim_users ENABLE ROW LEVEL SECURITY;      ALTER TABLE scim_users FORCE ROW LEVEL SECURITY;
    CREATE POLICY org_scim_tokens_tenant ON org_scim_tokens USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id());
    CREATE POLICY org_scim_tokens_system ON org_scim_tokens TO jobwatch_system USING (true) WITH CHECK (true);
    CREATE POLICY scim_users_tenant ON scim_users USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id());
    CREATE POLICY scim_users_system ON scim_users TO jobwatch_system USING (true) WITH CHECK (true);
    GRANT SELECT, INSERT, UPDATE, DELETE ON org_scim_tokens, scim_users TO jobwatch_app, jobwatch_system;
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS scim_users; DROP TABLE IF EXISTS org_scim_tokens;")
