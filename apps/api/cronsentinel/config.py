from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://cronsentinel:cronsentinel@localhost:5432/cronsentinel"
    redis_url: str = "redis://localhost:6379/0"
    nats_url: str = "nats://localhost:4222"
    nats_stream: str = "CS_EXEC"
    keycloak_issuer: str = "http://localhost:8080/realms/cronsentinel"
    keycloak_audience: str = "cronsentinel-web"
    keycloak_client_id: str = "cronsentinel-web"
    keycloak_client_secret: str = ""
    # R43: service-account client for the Keycloak Admin API (identity providers only).
    keycloak_admin_client_id: str = "jobwatch-admin"
    keycloak_admin_client_secret: str = ""
    # R51: Keycloak identity-provider aliases offered as "Continue with …" buttons (comma separated,
    # e.g. "google,github,microsoft"). Each must exist in the realm; empty = email and SSO only.
    auth_social_providers: str = ""
    api_public_url: str = "http://localhost:8000"
    web_public_url: str = "http://localhost:3000"
    # R48: public base URL of the ingest service (ping URLs). Empty = same host as the API.
    ingest_public_url: str = ""
    # R62: the Linux agent installer served at <ingest>/agent/install.sh fetches binaries from
    # <agent_download_base>/<agent_version>/… (e.g. https://github.com/<owner>/<repo>/releases/download, v0.2.0)
    agent_download_base: str = ""
    agent_version: str = ""
    agent_install_script: str = ""   # path to install.sh; default: the repository copy
    session_ttl_s: int = 12 * 3600
    cookie_secure: bool = False
    copilot_provider: str = "openai"          # openai-compatible (vLLM/Ollama) | anthropic
    copilot_base_url: str = ""                # e.g. http://vllm:8000 or http://ollama:11434
    copilot_model: str = "qwen2.5:14b"
    copilot_api_key: str = "none"
    copilot_timeout_s: int = 60
    copilot_max_context_chars: int = 60_000
    copilot_rate_per_min: int = 20
    # R16 data boundary: an endpoint outside the private network is refused unless this is set, and
    # when it is set, hostnames/pods/nodes/IPs are pseudonymised before leaving. See copilot/egress.py.
    copilot_allow_external: bool = False
    # R18 SSRF guard: false = tenant webhooks may only reach public addresses (multi-tenant SaaS).
    # Set true for single-tenant self-hosted installs that post to internal services. netguard.py.
    outbound_allow_private: bool = False
    copilot_internal_suffixes: str = ".svc,.cluster.local,.internal,.local,.lan"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # No default on purpose: every session cookie, CSRF token and encrypted channel config is keyed off
    # this. A known default ("change-me") meant an install that forgot to set it would accept forged
    # session cookies for any user in any org. keys.py refuses empty/known/short values at first use.
    secret_encryption_key: str = ""
    # Escape hatch for unit tests and throwaway local runs ONLY. Never set in a deployed environment.
    allow_insecure_secret: bool = False
    ingest_rate_per_min: int = 600
    api_rate_per_min: int = 1200
    reconciler_interval_s: int = 30
    max_log_bytes_per_execution: int = 256 * 1024
    log_level: str = "INFO"


settings = Settings()
