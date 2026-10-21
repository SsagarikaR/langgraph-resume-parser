import os

import pytest

# app.api builds the FastAPI app at import time, which reads settings, so a key must exist
# before any test module imports it (e.g. in CI, where there is no .env)
os.environ.setdefault("GEMINI_API_KEY", "test-key")
# ...and the app refuses to start without API keys unless told this is local development
os.environ.setdefault("ALLOW_OPEN_API", "true")
# The UI page tests must never reach a real API, e.g. one running locally on the default port
os.environ["API_URL"] = "http://127.0.0.1:9"

from app.api.rate_limit import api_rate_limit, workflow_rate_limit
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def process_limits_unchanged():
    """Fail the test that lowers this process's resource limits (e.g. by running the PDF reader's
    child code in-process): they cannot be raised again, and later tests would break at random."""
    resource = pytest.importorskip("resource")
    before = resource.getrlimit(resource.RLIMIT_AS)
    yield
    assert resource.getrlimit(resource.RLIMIT_AS) == before, "a test lowered the process's address-space limit"


@pytest.fixture(autouse=True)
def test_settings(tmp_path, monkeypatch):
    """Isolated settings for every test: dummy key, all storage under tmp_path."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("ALLOW_OPEN_API", "true")
    # The tests name the UI's key "ui" (API_KEYS=ui:...): trust it to name the person it acts for
    monkeypatch.setenv("ACTING_USER_KEYS", "default,ui")
    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("QDRANT_PATH", str(tmp_path / "qdrant"))
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    monkeypatch.setenv("RETENTION_DAYS", "0")  # no background purge task in API tests
    get_settings.cache_clear()
    api_rate_limit.reset()
    workflow_rate_limit.reset()
    yield get_settings()
    get_settings.cache_clear()
