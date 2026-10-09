"""Unit/integration tests run with a fixed throwaway key. Set before cronsentinel.config is imported.
An empty-but-exported SECRET_ENCRYPTION_KEY (common in shells/CI) counts as unset."""
import os

if not os.environ.get("SECRET_ENCRYPTION_KEY"):
    os.environ["SECRET_ENCRYPTION_KEY"] = "unit-test-only-not-a-real-secret"
os.environ.setdefault("ALLOW_INSECURE_SECRET", "true")
