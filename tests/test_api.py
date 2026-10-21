import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import app, create_app
from app.api.routes.screenings import get_service
from app.core.config import get_settings
from app.services.exceptions import ScreeningNotFoundError, WorkflowFailedError

PDF_UPLOAD = {"resume": ("cv.pdf", b"%PDF-1.7", "application/pdf")}
JD_FORM = {"jd_text": "x" * 100}
SCREENING = {"thread_id": "abc", "status": "awaiting_review"}


class StubService:
    def start_stream(self, **kwargs):
        raise AssertionError("upload checks should reject the request before the service runs")

    def get(self, thread_id):
        raise ScreeningNotFoundError(f"Screening '{thread_id}' not found")

    def review_stream(self, thread_id, action, feedback):
        return self.get(thread_id)


class StreamingStubService(StubService):
    def __init__(self, fail_mid_stream=False):
        self.fail_mid_stream = fail_mid_stream

    def start_stream(self, **kwargs):
        yield {"type": "step", "node": "parse_resume", "event": "started"}
        if self.fail_mid_stream:
            raise WorkflowFailedError("Screening abc failed (RuntimeError). See server logs for details.")
        yield {"type": "step", "node": "parse_resume", "event": "finished"}
        yield {"type": "result", "screening": SCREENING}


def ndjson_lines(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines()]


@pytest.fixture
def client():
    app.dependency_overrides[get_service] = StubService
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_service_errors_map_to_status_codes(client):
    r = client.get("/screenings/abc")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_revise_without_feedback_is_rejected(client):
    r = client.post("/screenings/abc/review", json={"action": "revise", "feedback": "  "})
    assert r.status_code == 422


def test_oversized_upload_is_rejected(client):
    big = b"%PDF-" + b"0" * (1024 * 1024 + 10)  # MAX_UPLOAD_MB=1 in tests
    r = client.post("/screenings", files={"resume": ("cv.pdf", big, "application/pdf")}, data={"jd_text": "x" * 100})
    assert r.status_code == 400
    assert "larger than" in r.json()["detail"]


def test_invalid_thread_id_is_rejected(client):
    assert client.get("/screenings/bad%20id!").status_code == 422


def test_api_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    get_settings.cache_clear()
    assert [client.get("/screenings/abc").status_code for _ in range(3)] == [404, 404, 429]
    r = client.get("/screenings/abc")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    assert client.get("/health").status_code == 200  # health checks are never limited


def test_workflow_rate_limit_is_stricter(client, monkeypatch):
    monkeypatch.setenv("WORKFLOW_RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()
    body = {"action": "approve"}
    assert client.post("/screenings/abc/review", json=body).status_code == 404
    assert client.post("/screenings/abc/review", json=body).status_code == 429
    assert client.get("/screenings/abc").status_code == 404  # reads still allowed


def test_rate_limit_can_be_disabled(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()
    assert {client.get("/screenings/abc").status_code for _ in range(3)} == {404}


def test_oversized_request_body_is_rejected_early(client):
    r = client.post("/screenings", content=b"x", headers={"content-length": str(10 * 1024 * 1024)})
    assert r.status_code == 413


def test_security_headers(client):
    assert client.get("/health").headers["X-Content-Type-Options"] == "nosniff"


def test_cors_allows_only_configured_origins(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, https://app.example.com/")
    get_settings.cache_clear()
    cors_client = TestClient(create_app())
    preflight = {"Access-Control-Request-Method": "POST"}

    r = cors_client.options("/screenings", headers={"Origin": "https://app.example.com", **preflight})
    assert r.headers["access-control-allow-origin"] == "https://app.example.com"

    r = cors_client.options("/screenings", headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in r.headers


def test_no_cors_headers_by_default(client):
    r = client.get("/health", headers={"Origin": "https://app.example.com"})
    assert "access-control-allow-origin" not in r.headers


def test_stream_returns_progress_then_result(client):
    app.dependency_overrides[get_service] = StreamingStubService
    r = client.post("/screenings?stream=true", files=PDF_UPLOAD, data=JD_FORM)
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = ndjson_lines(r)
    assert [e["type"] for e in events] == ["step", "step", "result"]
    assert events[-1]["screening"]["thread_id"] == "abc"
    assert events[-1]["screening"]["matching_skills"] == []  # validated against ScreeningResponse


def test_without_stream_the_same_run_returns_plain_json(client):
    app.dependency_overrides[get_service] = StreamingStubService
    r = client.post("/screenings", files=PDF_UPLOAD, data=JD_FORM)
    assert r.json()["status"] == "awaiting_review"


def test_failure_after_streaming_started_is_an_error_event(client):
    app.dependency_overrides[get_service] = lambda: StreamingStubService(fail_mid_stream=True)
    r = client.post("/screenings?stream=true", files=PDF_UPLOAD, data=JD_FORM)
    assert r.status_code == 200  # already sent with the first event
    assert ndjson_lines(r)[-1] == {
        "type": "error",
        "status": 500,
        "detail": "Screening abc failed (RuntimeError). See server logs for details.",
    }


def test_stream_errors_before_the_first_event_keep_their_status(client):
    r = client.post("/screenings/abc/review?stream=true", json={"action": "approve"})
    assert r.status_code == 404
