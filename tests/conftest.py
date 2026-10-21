import os

import pytest

# app.api builds the FastAPI app at import time, which reads settings, so a key must exist
# before any test module imports it (e.g. in CI, where there is no .env)
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from app.api.rate_limit import api_rate_limit, workflow_rate_limit  # noqa: E402
from app.core.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def test_settings(tmp_path, monkeypatch):
    """Isolated settings for every test: dummy key, all storage under tmp_path."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("QDRANT_PATH", str(tmp_path / "qdrant"))
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    api_rate_limit.reset()
    workflow_rate_limit.reset()
    yield get_settings()
    get_settings.cache_clear()
