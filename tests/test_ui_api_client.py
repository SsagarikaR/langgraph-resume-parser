"""The UI's HTTP client: headers, error display and the streamed progress, with Streamlit and
the network faked."""

import json

import pytest
import requests

from ui import api_client


class SessionState(dict):
    """Streamlit's session state: a dict that also takes attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def __setattr__(self, name, value):
        self[name] = value


class FakeStatus:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, label=None, state=None, expanded=None):
        self.log.append(("label", label, state))

    def write(self, text):
        self.log.append(("write", text))


class FakeStreamlit:
    def __init__(self):
        self.session_state = SessionState()
        self.errors: list[str] = []
        self.log: list[tuple] = []

    def error(self, text):
        self.errors.append(text)

    def status(self, title, expanded=False):
        return FakeStatus(self.log)


class FakeResponse:
    def __init__(self, status=200, body=b"", lines=()):
        self.status_code = status
        self.content = body
        self.text = body.decode()
        self.lines = lines

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return json.loads(self.content)

    def iter_lines(self):
        for line in self.lines:
            if isinstance(line, Exception):
                raise line
            yield json.dumps(line).encode() if isinstance(line, dict) else line

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def st(monkeypatch):
    fake = FakeStreamlit()
    monkeypatch.setattr(api_client, "st", fake)
    monkeypatch.setattr(api_client, "API_KEY", "key-ui")
    return fake


def respond_with(monkeypatch, response=None, error=None):
    sent = []

    def request(*args, **kwargs):
        sent.append((args, kwargs))
        if error:
            raise error
        return response

    monkeypatch.setattr(api_client.requests, "request", request)
    monkeypatch.setattr(api_client.requests, "post", lambda *a, **k: request("POST", *a, **k))
    return sent


def test_headers_carry_the_key_the_reviewer_and_a_stable_session(st):
    st.session_state.acting_user = "alice"
    first, second = api_client.headers(), api_client.headers()
    assert first["X-API-Key"] == "key-ui"
    assert first["X-Acting-User"] == "alice"
    assert first["X-Client-Session"] == second["X-Client-Session"]  # one session, one budget


def test_the_api_key_can_come_from_a_file(tmp_path, monkeypatch):
    secret = tmp_path / "api_key"
    secret.write_text("from-file\n")
    monkeypatch.setenv("API_KEY_FILE", str(secret))
    assert api_client.read_api_key() == "from-file"


def test_markdown_from_uploads_is_escaped():
    assert api_client.md("![x](http://evil/a.png)") == r"\!\[x\]\(http\://evil/a\.png\)"


def test_an_empty_success_is_an_empty_dict(st, monkeypatch):
    respond_with(monkeypatch, FakeResponse(204))
    assert api_client.api("DELETE", "/screenings/abc") == {}


def test_api_errors_are_shown_escaped(st, monkeypatch):
    respond_with(monkeypatch, FakeResponse(400, json.dumps({"detail": "Bad *input*"}).encode()))
    assert api_client.api("GET", "/screenings/abc") is None
    assert st.errors == [r"API error 400: Bad \*input\*"]


def test_a_non_json_error_page_is_shown_as_text(st, monkeypatch):
    respond_with(monkeypatch, FakeResponse(502, b"Bad Gateway"))
    assert api_client.api("GET", "/screenings") is None
    assert st.errors == ["API error 502: Bad Gateway"]


@pytest.mark.parametrize(
    ("error", "message"),
    [(requests.ConnectionError(), "Cannot reach the API"), (requests.Timeout(), "request failed (Timeout)")],
)
def test_network_failures_are_shown(st, monkeypatch, error, message):
    respond_with(monkeypatch, error=error)
    assert api_client.api("GET", "/screenings") is None
    assert message in st.errors[0]


def test_a_garbled_success_is_reported(st, monkeypatch):
    respond_with(monkeypatch, FakeResponse(200, b"<html>"))
    assert api_client.api("GET", "/screenings") is None
    assert "not valid JSON" in st.errors[0]


def test_progress_is_shown_step_by_step_then_the_result_returned(st, monkeypatch):
    events = [
        {"type": "step", "thread_id": "t1", "node": "parse_resume", "event": "started"},
        {"type": "step", "thread_id": "t1", "node": "parse_resume", "event": "finished"},
        b"",  # keep-alive blank line
        {"type": "step", "thread_id": "t1", "node": "human_review", "event": "interrupted"},
        {"type": "result", "screening": {"thread_id": "t1", "status": "awaiting_review"}},
    ]
    sent = respond_with(monkeypatch, FakeResponse(200, lines=events))
    assert api_client.run_with_progress("Screening…", "/screenings")["status"] == "awaiting_review"
    assert sent[0][1]["params"] == {"stream": "true"}
    assert ("label", "Reading the resume PDF…", None) in st.log
    assert ("write", "✅ Reading the resume PDF") in st.log
    assert ("write", "⏸ Questions ready for your review") in st.log
    assert st.log[-1] == ("label", "Done", "complete")


def test_a_failed_step_is_shown_as_an_error(st, monkeypatch):
    events = [{"type": "error", "status": 500, "detail": "Screening t1 failed (RuntimeError)."}]
    respond_with(monkeypatch, FakeResponse(200, lines=events))
    assert api_client.run_with_progress("Screening…", "/screenings") is None
    assert st.errors == [r"API error 500: Screening t1 failed \(RuntimeError\)\."]


def test_a_rejected_run_shows_why(st, monkeypatch):
    respond_with(monkeypatch, FakeResponse(429, json.dumps({"detail": "Rate limit exceeded"}).encode()))
    assert api_client.run_with_progress("Screening…", "/screenings") is None
    assert st.errors == ["API error 429: Rate limit exceeded"]


def test_a_dropped_stream_loads_the_screening_by_its_id(st, monkeypatch):
    events = [
        {"type": "step", "thread_id": "t1", "node": "extract_resume", "event": "started"},
        requests.ConnectionError("connection reset"),
    ]
    respond_with(monkeypatch, FakeResponse(200, lines=events))
    loaded = []
    monkeypatch.setattr(api_client, "api", lambda method, path: loaded.append((method, path)) or {"status": "running"})
    assert api_client.run_with_progress("Screening…", "/screenings") == {"status": "running"}
    assert loaded == [("GET", "/screenings/t1")]


def test_a_stream_dropped_before_any_step_says_the_run_did_not_start(st, monkeypatch):
    respond_with(monkeypatch, FakeResponse(200, lines=[b"{not json"]))
    assert api_client.run_with_progress("Screening…", "/screenings") is None
    assert st.errors == ["The screening did not start. Try again."]


def test_an_unreachable_api_is_reported_before_streaming(st, monkeypatch):
    respond_with(monkeypatch, error=requests.ConnectionError())
    assert api_client.run_with_progress("Screening…", "/screenings") is None
    assert "Cannot reach the API" in st.errors[0]
