"""R43 — single sign-on per organization, brokered through Keycloak.

org_sso      one identity provider per org (OIDC or SAML). The IdP itself lives in Keycloak under
             `idp_alias`; config_enc keeps what the admin entered (encrypted) so the form can be
             re-shown and Keycloak can be re-synced. Secrets are never returned by the API.
org_domains  email domains an org has proven it owns (DNS TXT). Only users whose email is in a
             verified domain of the org may sign in through that org's IdP — otherwise any org admin
             could point an IdP at someone else's addresses. A domain can be verified by one org only.
"""
from alembic import op

revision = "0018"; down_revision = "0017"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE org_sso (
      org_id UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
      protocol TEXT NOT NULL CHECK (protocol IN ('oidc', 'saml')),
      idp_alias TEXT NOT NULL UNIQUE,
      display_name TEXT NOT NULL,
      config_enc BYTEA NOT NULL,                  -- encrypted (crypto.encrypt_json), never returned
      enabled BOOLEAN NOT NULL DEFAULT false,
      enforce BOOLEAN NOT NULL DEFAULT false,
      default_role TEXT NOT NULL DEFAULT 'viewer' CHECK (default_role IN ('viewer', 'developer', 'sre', 'devops')),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE org_domains (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      domain TEXT NOT NULL CHECK (domain = lower(domain)),
      verification_token TEXT NOT NULL,
      verified_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE (org_id, domain)
    );
    CREATE UNIQUE INDEX org_domains_verified_once ON org_domains (domain) WHERE verified_at IS NOT NULL;

    ALTER TABLE org_sso ENABLE ROW LEVEL SECURITY;      ALTER TABLE org_sso FORCE ROW LEVEL SECURITY;
    ALTER TABLE org_domains ENABLE ROW LEVEL SECURITY;  ALTER TABLE org_domains FORCE ROW LEVEL SECURITY;
    CREATE POLICY org_sso_tenant ON org_sso USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id());
    CREATE POLICY org_sso_system ON org_sso TO jobwatch_system USING (true) WITH CHECK (true);
    CREATE POLICY org_domains_tenant ON org_domains USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id());
    CREATE POLICY org_domains_system ON org_domains TO jobwatch_system USING (true) WITH CHECK (true);
    GRANT SELECT, INSERT, UPDATE, DELETE ON org_sso, org_domains TO jobwatch_app, jobwatch_system;

    -- 0015 removed 'sso' from Business because nothing implemented it; it ships now.
    UPDATE plan_limits SET features = array_append(features, 'sso') WHERE plan = 'business' AND NOT ('sso' = ANY(features));
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS org_domains; DROP TABLE IF EXISTS org_sso;")
    op.execute("UPDATE plan_limits SET features = array_remove(features, 'sso') WHERE plan = 'business'")
