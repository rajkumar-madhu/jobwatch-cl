"""R56 — SCIM groups and group → role mapping.

scim_groups         groups pushed by the directory (Okta, Entra ID …); `role` is set by an org owner in
                    JobWatch (never 'owner'); NULL = the group does not affect roles
scim_group_members  who is in each group (users provisioned to this org over SCIM)
scim_users.role_from_group  whether the person's current role came from a mapped group, so leaving every
                    mapped group reverts it to the default role, while a role set by hand is left alone
"""
from alembic import op

revision = "0021"; down_revision = "0020"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE scim_groups (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      display_name TEXT NOT NULL,
      external_id TEXT,
      role member_role CHECK (role IS NULL OR role <> 'owner'),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE (org_id, display_name)
    );
    CREATE TABLE scim_group_members (
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      group_id UUID NOT NULL REFERENCES scim_groups(id) ON DELETE CASCADE,
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      PRIMARY KEY (group_id, user_id)
    );
    CREATE INDEX scim_group_members_user ON scim_group_members (org_id, user_id);
    ALTER TABLE scim_users ADD COLUMN role_from_group BOOLEAN NOT NULL DEFAULT false;

    ALTER TABLE scim_groups ENABLE ROW LEVEL SECURITY;        ALTER TABLE scim_groups FORCE ROW LEVEL SECURITY;
    ALTER TABLE scim_group_members ENABLE ROW LEVEL SECURITY; ALTER TABLE scim_group_members FORCE ROW LEVEL SECURITY;
    CREATE POLICY scim_groups_tenant ON scim_groups USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id());
    CREATE POLICY scim_groups_system ON scim_groups TO jobwatch_system USING (true) WITH CHECK (true);
    CREATE POLICY scim_group_members_tenant ON scim_group_members USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id());
    CREATE POLICY scim_group_members_system ON scim_group_members TO jobwatch_system USING (true) WITH CHECK (true);
    GRANT SELECT, INSERT, UPDATE, DELETE ON scim_groups, scim_group_members TO jobwatch_app, jobwatch_system;
    """)


def downgrade():
    op.execute("""DROP TABLE IF EXISTS scim_group_members; DROP TABLE IF EXISTS scim_groups;
                  ALTER TABLE scim_users DROP COLUMN IF EXISTS role_from_group;""")
