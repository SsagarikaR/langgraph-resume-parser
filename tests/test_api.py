import asyncio
import hashlib
import json
import logging
import subprocess
import sys
from datetime import date, timedelta
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from app.api import main
from app.api.main import app, create_app
from app.api.routes.screenings import get_service
from app.core.config import get_settings
from app.core.metrics import metrics
from app.services.exceptions import (
    ScreeningBusyError,
    ScreeningNotFoundError,
    ServerBusyError,
    WorkflowFailedError,
)

PDF_UPLOAD = {"resume": ("cv.pdf", b"%PDF-1.7", "application/pdf")}
JD_FORM = {"jd_text": "x" * 100}
SCREENING = {"thread_id": "abc", "status": "awaiting_review"}


class StubService:
    def start_stream(self, **kwargs):
        raise AssertionError("upload checks should reject the request before the service runs")

    def get(self, thread_id, caller=None):
        raise ScreeningNotFoundError(f"Screening '{thread_id}' not found")

    def review_stream(self, thread_id, action, feedback, reviewer=None, caller=None):
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
    headers = client.get("/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"  # responses carry resume data


def test_delete_screening(client):
    deleted = []

    class DeletingService(StubService):
        def delete(self, thread_id, caller=None):
            deleted.append(thread_id)

    app.dependency_overrides[get_service] = DeletingService
    assert client.delete("/screenings/abc").status_code == 204
    assert deleted == ["abc"]


def test_busy_screening_is_409(client):
    class BusyService(StubService):
        def delete(self, thread_id, caller=None):
            raise ScreeningBusyError(f"Screening {thread_id} is busy with another request; try again shortly")

    app.dependency_overrides[get_service] = BusyService
    assert client.delete("/screenings/abc").status_code == 409


def test_delete_unknown_screening_is_404(client):
    StubService.delete = lambda self, thread_id, caller=None: self.get(thread_id)  # type: ignore[attr-defined]
    assert client.delete("/screenings/missing").status_code == 404


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


def test_api_key_is_required_when_configured(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    assert client.get("/screenings/abc").status_code == 401
    assert client.get("/screenings/abc", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/screenings/abc", headers={"X-API-Key": "s3cret"}).status_code == 404  # past auth
    assert client.get("/health").status_code == 200  # health checks never need the key


def test_api_is_open_when_no_key_is_configured(client):
    assert client.get("/screenings/abc").status_code == 404


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


MULTIPART = {"content-type": "multipart/form-data; boundary=x"}
PART_HEAD = b'--x\r\nContent-Disposition: form-data; name="resume"; filename="cv.pdf"\r\n\r\n%PDF-'


def test_chunked_body_over_the_limit_is_rejected_while_it_streams(client):
    # No Content-Length (chunked), so only counting the bytes as they arrive can catch it
    def chunks():
        yield PART_HEAD
        for _ in range(40):  # 2.5 MB; the limit is 2 MB + 64 KB with MAX_UPLOAD_MB=1
            yield b"0" * 65536

    r = client.post("/screenings", content=chunks(), headers=MULTIPART)
    assert r.status_code == 413
    assert r.headers["X-Content-Type-Options"] == "nosniff"  # error responses get the security headers too


def test_unauthenticated_upload_is_rejected_before_its_body_is_read(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    read = []

    def chunks():
        read.append(True)
        yield PART_HEAD

    r = client.post("/screenings", content=chunks(), headers=MULTIPART)
    assert r.status_code == 401
    assert read == []  # FastAPI would have parsed the upload before running a dependency


def test_bad_api_keys_are_rate_limited(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    get_settings.cache_clear()
    wrong = {"X-API-Key": "wrong"}
    assert [client.get("/screenings/abc", headers=wrong).status_code for _ in range(3)] == [401, 401, 429]


def test_responses_never_include_server_paths(client):
    class CompletedService(StubService):
        def get(self, thread_id, caller=None):
            return {"thread_id": thread_id, "status": "completed", "report_path": "/var/lib/screener/reports/abc.json"}

    app.dependency_overrides[get_service] = CompletedService
    assert "report_path" not in client.get("/screenings/abc").json()


class RecordingService(StubService):
    """Remembers who each call came from."""

    calls: ClassVar[list[tuple]] = []  # shared: FastAPI builds a new instance per request

    def get(self, thread_id, caller=None):
        self.calls.append(("get", caller))
        return SCREENING

    def list_screenings(self, caller=None, limit=50):
        self.calls.append(("list", caller, limit))
        return [{**SCREENING, "created_at": "2026-01-01T00:00:00+00:00"}]

    def review_stream(self, thread_id, action, feedback, reviewer=None, caller=None):
        self.calls.append(("review", reviewer, caller))
        yield {"type": "result", "screening": SCREENING}


@pytest.fixture
def recording(client):
    RecordingService.calls = []
    app.dependency_overrides[get_service] = RecordingService
    return RecordingService.calls


def test_each_named_key_identifies_its_caller(client, recording, monkeypatch):
    monkeypatch.setenv("API_KEYS", "alice:key-a, bob:key-b")
    get_settings.cache_clear()
    assert client.get("/screenings/abc", headers={"X-API-Key": "key-b"}).status_code == 200
    assert client.get("/screenings/abc", headers={"X-API-Key": "nope"}).status_code == 401
    assert recording == [("get", "bob")]


def test_the_single_api_key_is_named_default(client, recording, monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    client.get("/screenings/abc", headers={"X-API-Key": "s3cret"})
    assert recording == [("get", "default")]


def test_open_api_has_no_caller(client, recording):
    client.get("/screenings/abc")
    assert recording == [("get", None)]


def test_review_passes_the_reviewer_and_the_caller(client, recording, monkeypatch):
    monkeypatch.setenv("API_KEYS", "ui:key-ui")
    get_settings.cache_clear()
    body = {"action": "approve", "reviewer": "alice"}
    assert client.post("/screenings/abc/review", json=body, headers={"X-API-Key": "key-ui"}).status_code == 200
    assert recording == [("review", "alice", "ui")]
    unnamed = {"action": "approve", "reviewer": ""}
    assert client.post("/screenings/abc/review", json=unnamed, headers={"X-API-Key": "key-ui"}).status_code == 422


@pytest.mark.parametrize("value", ["alice", "alice:", ":key", "a b:key", "alice:k1,alice:k2", "alice:k,bob:k"])
def test_malformed_api_keys_fail_at_startup(monkeypatch, value):
    monkeypatch.setenv("API_KEYS", value)
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="API_KEYS"):
        get_settings()


def test_docs_are_served_only_without_keys_unless_enabled(monkeypatch):
    assert TestClient(create_app()).get("/openapi.json").status_code == 200  # local dev: no keys

    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    protected = TestClient(create_app())
    assert protected.get("/openapi.json").status_code == 404
    assert protected.get("/docs").status_code == 404

    monkeypatch.setenv("API_DOCS", "true")
    get_settings.cache_clear()
    assert TestClient(create_app()).get("/openapi.json").status_code == 200


def test_every_response_gets_a_request_id(client, monkeypatch):
    generated = client.get("/health").headers["X-Request-ID"]
    assert len(generated) == 32
    assert client.get("/health", headers={"X-Request-ID": "trace-42"}).headers["X-Request-ID"] == "trace-42"
    unsafe = client.get("/health", headers={"X-Request-ID": "bad id\r\n"}).headers["X-Request-ID"]
    assert unsafe != "bad id\r\n"

    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    assert "X-Request-ID" in client.get("/screenings/abc").headers  # rejected by the guard, still tagged


def test_hsts_header(client):
    assert client.get("/health").headers["Strict-Transport-Security"].startswith("max-age=")


def test_empty_api_docs_in_env_means_automatic(monkeypatch):
    monkeypatch.setenv("API_DOCS", "")  # as copied from .env.example
    get_settings.cache_clear()
    assert get_settings().docs_enabled is True


def test_the_client_can_name_the_person_it_acts_for(client, recording, monkeypatch):
    monkeypatch.setenv("API_KEYS", "ui:key-ui")
    get_settings.cache_clear()
    headers = {"X-API-Key": "key-ui", "X-Acting-User": "alice"}
    assert client.get("/screenings/abc", headers=headers).status_code == 200
    assert recording == [("get", "ui/alice")]
    bad = {"X-API-Key": "key-ui", "X-Acting-User": "../bob"}
    assert client.get("/screenings/abc", headers=bad).status_code == 400


def test_the_acting_user_is_ignored_on_an_open_api(client, recording):
    client.get("/screenings/abc", headers={"X-Acting-User": "alice"})
    assert recording == [("get", None)]  # no keys: no access control to scope


def test_list_screenings(client, recording):
    r = client.get("/screenings?limit=5")
    assert r.status_code == 200
    assert r.json()[0]["thread_id"] == "abc"
    assert recording == [("list", None, 5)]
    assert client.get("/screenings?limit=500").status_code == 422


def test_server_busy_is_503_with_retry_after(client):
    class FullService(StubService):
        def get(self, thread_id, caller=None):
            raise ServerBusyError("The server is running as many screenings as it allows")

    app.dependency_overrides[get_service] = FullService
    r = client.get("/screenings/abc")
    assert r.status_code == 503
    assert int(r.headers["Retry-After"]) > 0


def test_metrics_are_exposed_in_prometheus_format(client):
    metrics.reset()
    client.get("/screenings/abc")
    body = client.get("/metrics").text
    assert "# TYPE http_requests_total counter" in body
    assert 'http_requests_total{method="GET",status="404"} 1' in body
    assert "screening_runs_in_progress 0" in body


def test_metrics_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "false")
    get_settings.cache_clear()
    assert TestClient(create_app()).get("/metrics").status_code == 404


def test_the_request_log_names_the_caller(client, recording, monkeypatch, caplog):
    monkeypatch.setenv("API_KEYS", "ui:key-ui")
    get_settings.cache_clear()
    with caplog.at_level(logging.INFO, logger="app.api.main"):
        client.delete("/screenings/abc", headers={"X-API-Key": "key-ui", "X-Acting-User": "alice"})
    assert "(caller: ui/alice)" in caplog.text


def test_the_purge_loop_survives_a_failed_purge(monkeypatch):
    purges = []

    class Service:
        def purge_expired(self, days):
            purges.append(days)
            if len(purges) == 1:
                raise RuntimeError("database locked")

    async def stop_after_two(seconds):
        if len(purges) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(main, "get_service", Service)
    monkeypatch.setattr(main.asyncio, "sleep", stop_after_two)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.purge_periodically(retention_days=30, interval_minutes=60))
    assert purges == [30, 30]  # the failure was logged and the next interval purged again


def test_unknown_http_methods_share_one_metric_label(client):
    # Any token is a valid method: labelling by it would grow the metrics without bound
    metrics.reset()
    for i in range(3):
        client.request(f"PROBE{i}", "/health")
    body = client.get("/metrics").text
    assert "PROBE" not in body
    assert 'http_requests_total{method="other",status="405"} 3' in body


def test_a_busy_server_refuses_a_streamed_run_with_503_not_an_error_event(client):
    class FullService(StubService):
        def start_stream(self, **kwargs):
            # The real service takes its run slot before returning the stream, so it raises here
            raise ServerBusyError("The server is running as many screenings as it allows")

    app.dependency_overrides[get_service] = FullService
    r = client.post("/screenings?stream=true", files=PDF_UPLOAD, data=JD_FORM)
    assert r.status_code == 503
    assert int(r.headers["Retry-After"]) > 0


def test_each_person_behind_one_key_has_their_own_rate_limit(client, recording, monkeypatch):
    # Every UI user reaches the API from the UI's address: an address budget would be shared by all
    monkeypatch.setenv("API_KEYS", "ui:key-ui")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    get_settings.cache_clear()

    def get(user):
        return client.get("/screenings/abc", headers={"X-API-Key": "key-ui", "X-Acting-User": user}).status_code

    assert [get("alice") for _ in range(3)] == [200, 200, 429]
    assert get("bob") == 200


def test_the_workflow_limit_is_per_person_too(client, recording, monkeypatch):
    monkeypatch.setenv("API_KEYS", "ui:key-ui")
    monkeypatch.setenv("WORKFLOW_RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()

    def review(user):
        headers = {"X-API-Key": "key-ui", "X-Acting-User": user}
        return client.post("/screenings/abc/review", json={"action": "approve"}, headers=headers).status_code

    assert [review("alice"), review("alice"), review("bob")] == [200, 429, 200]


@pytest.mark.parametrize("name", ["Ada\nINFO forged log line", "Ada\r", "<script>", "a" * 101])
def test_reviewer_names_that_could_forge_log_lines_are_rejected(client, name):
    r = client.post("/screenings/abc/review", json={"action": "approve", "reviewer": name})
    assert r.status_code == 422


def test_ordinary_reviewer_names_are_accepted(client, recording):
    r = client.post("/screenings/abc/review", json={"action": "approve", "reviewer": "Ada O'Brien-Smith"})
    assert r.status_code == 200


def test_importing_the_api_package_does_not_build_the_app():
    # Without GEMINI_API_KEY, building the app fails: importing a module must not build it
    env = {"PATH": "/usr/bin:/bin", "GEMINI_API_KEY": ""}
    code = "import app.api.auth, app.api.rate_limit; import sys; sys.exit('app.api.main' in sys.modules)"
    run = subprocess.run([sys.executable, "-c", code], env=env, check=False)  # noqa: S603  # fixed command
    assert run.returncode == 0


def test_each_session_of_a_shared_login_has_its_own_budget(client, recording, monkeypatch):
    # The shared UI login names no person, so it names the browser session instead
    monkeypatch.setenv("API_KEYS", "ui:key-ui")
    monkeypatch.setenv("WORKFLOW_RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()

    def review(session):
        headers = {"X-API-Key": "key-ui", "X-Client-Session": session}
        return client.post("/screenings/abc/review", json={"action": "approve"}, headers=headers).status_code

    assert [review("session-aaaa"), review("session-aaaa"), review("session-bbbb")] == [200, 429, 200]


def test_a_session_does_not_split_a_named_persons_budget(client, recording, monkeypatch):
    monkeypatch.setenv("API_KEYS", "ui:key-ui")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()

    def get(session):
        headers = {"X-API-Key": "key-ui", "X-Acting-User": "alice", "X-Client-Session": session}
        return client.get("/screenings/abc", headers=headers).status_code

    assert [get("session-aaaa"), get("session-bbbb")] == [200, 429]


def test_the_key_ceiling_stops_rotating_names_for_more_budget(client, recording, monkeypatch):
    # Acting users and sessions are names the client picks: the key's total is capped regardless
    monkeypatch.setenv("API_KEYS", "ui:key-ui")
    monkeypatch.setenv("KEY_WORKFLOW_RATE_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()

    def review(user):
        headers = {"X-API-Key": "key-ui", "X-Acting-User": user}
        return client.post("/screenings/abc/review", json={"action": "approve"}, headers=headers).status_code

    statuses = [review(f"user{i}") for i in range(5)]
    assert statuses == [200, 200, 200, 429, 429]
    r = client.post(
        "/screenings/abc/review", json={"action": "approve"}, headers={"X-API-Key": "key-ui", "X-Acting-User": "zed"}
    )
    assert "for this API key" in r.json()["detail"]


def test_a_refused_request_uses_no_budget(client, recording, monkeypatch):
    # Over the key's ceiling, the person's budget is not spent as well
    monkeypatch.setenv("API_KEYS", "ui:key-ui")
    monkeypatch.setenv("KEY_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    get_settings.cache_clear()
    headers = {"X-API-Key": "key-ui", "X-Acting-User": "alice"}
    assert [client.get("/screenings/abc", headers=headers).status_code for _ in range(2)] == [200, 429]
    monkeypatch.setenv("KEY_RATE_LIMIT_PER_MINUTE", "100")
    get_settings.cache_clear()
    assert client.get("/screenings/abc", headers=headers).status_code == 200  # alice had 1 of 2 left


def test_ready_reports_each_dependency(client, monkeypatch):
    class Repository:
        def ping(self):
            pass

    class Service(StubService):
        repository = Repository()

    monkeypatch.setattr(main, "get_service", Service)
    monkeypatch.setattr(main.vector_store, "ping", lambda: None)
    r = client.get("/ready")
    assert (r.status_code, r.json()) == (200, {"status": "ok", "checks": {"database": "ok", "guidelines": "ok"}})


def test_ready_is_503_without_the_database_but_not_without_guidelines(client, monkeypatch, caplog):
    def down():
        raise OSError("disk I/O error at /var/lib/screener/checkpoints.db")

    class Repository:
        ping = staticmethod(down)

    class Service(StubService):
        repository = Repository()

    monkeypatch.setattr(main.vector_store, "ping", down)
    monkeypatch.setattr(main, "get_service", lambda: Service())
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["checks"] == {"database": "unavailable", "guidelines": "unavailable"}
    assert "/var/lib" not in r.text  # what is down, never why (it needs no key)

    class Healthy:
        def ping(self):
            pass

    Service.repository = Healthy()  # type: ignore[assignment]
    r = client.get("/ready")
    assert (r.status_code, r.json()["status"]) == (200, "ok")  # screenings run without guidelines


def test_the_api_refuses_to_start_without_keys_unless_told_it_is_local(monkeypatch):
    monkeypatch.setenv("ALLOW_OPEN_API", "false")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="ALLOW_OPEN_API"):
        create_app()
    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    create_app()  # a key is enough


def test_only_trusted_keys_may_name_the_person_they_act_for(client, recording, monkeypatch):
    monkeypatch.setenv("API_KEYS", "ui:key-ui, alice:key-a")
    monkeypatch.setenv("ACTING_USER_KEYS", "ui")
    get_settings.cache_clear()
    posing = {"X-API-Key": "key-a", "X-Acting-User": "bob"}
    assert client.get("/screenings/abc", headers=posing).status_code == 403
    # Refused before the body is read, like a wrong key
    assert client.post("/screenings", files=PDF_UPLOAD, data=JD_FORM, headers=posing).status_code == 403
    assert client.get("/screenings/abc", headers={"X-API-Key": "key-a"}).status_code == 200
    assert recording == [("get", "alice")]


def test_the_workflow_limit_is_applied_before_the_upload_is_read(client, monkeypatch):
    monkeypatch.setenv("WORKFLOW_RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()
    app.dependency_overrides[get_service] = StreamingStubService
    assert client.post("/screenings", files=PDF_UPLOAD, data=JD_FORM).status_code == 200
    seen = []
    original = main.ScreeningsGuardMiddleware.__call__

    async def guard(self, scope, receive, send):
        async def counting_receive():
            message = await receive()
            seen.append(message)
            return message

        await original(self, scope, counting_receive, send)

    monkeypatch.setattr(main.ScreeningsGuardMiddleware, "__call__", guard)
    r = client.post("/screenings", files=PDF_UPLOAD, data=JD_FORM)
    assert r.status_code == 429
    assert seen == []  # not one body chunk was read


def test_each_key_has_a_daily_workflow_quota(client, recording, monkeypatch):
    monkeypatch.setenv("API_KEYS", "ui:key-ui")
    monkeypatch.setenv("KEY_DAILY_WORKFLOW_LIMIT", "2")
    get_settings.cache_clear()
    body = {"action": "approve"}
    for user in ("alice", "bob"):
        headers = {"X-API-Key": "key-ui", "X-Acting-User": user}
        assert client.post("/screenings/abc/review", json=body, headers=headers).status_code == 200
    # A new person behind the same key does not get a new quota
    r = client.post("/screenings/abc/review", json=body, headers={"X-API-Key": "key-ui", "X-Acting-User": "carol"})
    assert r.status_code == 429
    assert "per day" in r.json()["detail"]


def test_an_unhandled_error_is_counted_and_logged_as_500(monkeypatch, caplog):
    class BrokenService(StubService):
        def get(self, thread_id, caller=None):
            raise RuntimeError("bug")

    metrics.reset()
    app.dependency_overrides[get_service] = BrokenService
    try:
        with TestClient(app, raise_server_exceptions=False) as c, caplog.at_level(logging.INFO, "app.api.main"):
            assert c.get("/screenings/abc").status_code == 500
            body = c.get("/metrics").text
    finally:
        app.dependency_overrides.clear()
    assert 'http_requests_total{method="GET",status="500"} 1' in body
    assert "/screenings/abc -> 500" in caplog.text


def test_metric_values_are_written_exactly_and_latency_is_a_histogram(client):
    metrics.reset()
    metrics.inc("llm_tokens_total", 1234567, direction="input")
    client.get("/health")
    body = client.get("/metrics").text
    assert 'llm_tokens_total{direction="input"} 1234567' in body  # not 1.23457e+06
    assert "# TYPE http_request_duration_seconds histogram" in body
    assert 'http_request_duration_seconds_bucket{method="GET",le="+Inf"} 1' in body
    assert 'http_request_duration_seconds_count{method="GET"} 1' in body


def test_a_key_can_be_stored_as_its_sha256(client, recording, monkeypatch):
    digest = hashlib.sha256(b"key-a").hexdigest()
    monkeypatch.setenv("API_KEYS", f"alice:sha256:{digest}")
    get_settings.cache_clear()
    assert client.get("/screenings/abc", headers={"X-API-Key": "key-a"}).status_code == 200
    # The hash itself is not a key: someone who reads the configuration cannot use it
    assert client.get("/screenings/abc", headers={"X-API-Key": f"sha256:{digest}"}).status_code == 401
    assert recording == [("get", "alice")]


def test_an_expired_key_is_refused(client, recording, monkeypatch):
    yesterday = (date.today() - timedelta(days=2)).isoformat()
    tomorrow = (date.today() + timedelta(days=2)).isoformat()
    monkeypatch.setenv("API_KEYS", "old:key-old, new:key-new")
    monkeypatch.setenv("API_KEY_EXPIRES", f"old:{yesterday}, new:{tomorrow}")
    get_settings.cache_clear()
    r = client.get("/screenings/abc", headers={"X-API-Key": "key-old"})
    assert r.status_code == 401
    assert "expired" in r.json()["detail"]
    assert client.get("/screenings/abc", headers={"X-API-Key": "key-new"}).status_code == 200
    assert recording == [("get", "new")]


def test_an_audit_key_reads_every_screening_but_changes_none(client, recording, monkeypatch):
    monkeypatch.setenv("API_KEYS", "ui:key-ui, audit:key-audit")
    monkeypatch.setenv("AUDIT_KEYS", "audit")
    get_settings.cache_clear()
    auditor = {"X-API-Key": "key-audit"}
    assert client.get("/screenings/abc", headers=auditor).status_code == 200
    assert client.get("/screenings", headers=auditor).status_code == 200
    assert recording == [("get", None), ("list", None, 50)]  # None: every caller's screenings
    review = client.post("/screenings/abc/review", json={"action": "approve"}, headers=auditor)
    assert review.status_code == 403
    # Refused before the upload is read, like a wrong key
    assert client.post("/screenings", files=PDF_UPLOAD, data=JD_FORM, headers=auditor).status_code == 403
    assert client.delete("/screenings/abc", headers=auditor).status_code == 403
    assert client.get("/screenings/abc", headers={**auditor, "X-Acting-User": "bob"}).status_code == 403
    # Other keys still see only their own
    client.get("/screenings/abc", headers={"X-API-Key": "key-ui"})
    assert recording[-1] == ("get", "ui")


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"API_KEYS": "alice:sha256:abc"}, "64 hex"),
        ({"API_KEYS": "alice:k", "API_KEY_EXPIRES": "alice:31-01-2027"}, "API_KEY_EXPIRES"),
        ({"API_KEYS": "alice:k", "API_KEY_EXPIRES": "bob:2027-01-31"}, "not configured"),
        ({"API_KEYS": "alice:k", "AUDIT_KEYS": "bob"}, "not configured"),
        ({"API_KEYS": "ui:k", "AUDIT_KEYS": "ui", "ACTING_USER_KEYS": "ui"}, "AUDIT_KEYS"),
    ],
)
def test_key_roles_and_expiry_are_checked_at_startup(monkeypatch, env, message):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    with pytest.raises(ValueError, match=message):
        get_settings()
