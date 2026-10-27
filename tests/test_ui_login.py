"""The UI's sign-in rules (the Streamlit page itself is not run here)."""

import pytest

from ui import login
from ui.login import LOCKOUT_SECONDS, MAX_FAILURES, FailedSignIns, hash_password, parse_users


def test_parse_users():
    assert parse_users(" alice:pw1, bob:p:w2 ,broken, :x, carol:") == {"alice": "pw1", "bob": "p:w2"}


def test_user_names_must_be_valid_acting_users():
    # The name goes to the API in X-Acting-User, which allows no spaces or slashes
    assert parse_users("alice:pw, a b:pw, x/y:pw") == {"alice": "pw"}


def test_lockout_after_repeated_failures_is_shared_and_expires():
    guard = FailedSignIns()
    for i in range(MAX_FAILURES):
        assert guard.retry_after("1.2.3.4", now=i) == 0
        guard.record("1.2.3.4", now=i)
    assert guard.retry_after("1.2.3.4", now=MAX_FAILURES) > 0  # every session from that address
    assert guard.retry_after("5.6.7.8", now=MAX_FAILURES) == 0  # other addresses are unaffected
    assert guard.retry_after("1.2.3.4", now=LOCKOUT_SECONDS + MAX_FAILURES) == 0  # the window passed


def test_a_successful_sign_in_clears_the_failures():
    guard = FailedSignIns()
    for i in range(MAX_FAILURES - 1):
        guard.record("ip", now=i)
    guard.clear("ip")
    guard.record("ip", now=10)
    assert guard.retry_after("ip", now=11) == 0


@pytest.mark.parametrize(
    ("users", "shared", "name", "password", "ok"),
    [
        ({"alice": "pw"}, "", "alice", "pw", True),
        ({"alice": "pw"}, "", "alice", "wrong", False),
        ({"alice": "pw"}, "", "mallory", "pw", False),  # unknown name
        ({}, "team-pw", "anyone", "team-pw", True),  # shared login: the name is the reviewer's own
        ({}, "team-pw", "anyone", "", False),
    ],
)
def test_password_check(monkeypatch, users, shared, name, password, ok):
    monkeypatch.setattr(login, "USERS", users)
    monkeypatch.setattr(login, "SHARED_PASSWORD", shared)
    assert login.password_ok(name, password) is ok


def test_hashed_passwords():
    stored = hash_password("s3cret")
    assert stored.startswith("scrypt$")
    assert ":" not in stored and "," not in stored  # fits in UI_USERS
    assert hash_password("s3cret") != stored  # salted
    assert login.password_matches("s3cret", stored)
    assert not login.password_matches("wrong", stored)
    assert not login.password_matches("s3cret", "scrypt$%%%$bad")


@pytest.mark.parametrize(("password", "ok"), [("pw", True), ("wrong", False)])
def test_password_check_with_a_hashed_user(monkeypatch, password, ok):
    monkeypatch.setattr(login, "USERS", {"alice": hash_password("pw")})
    monkeypatch.setattr(login, "SHARED_PASSWORD", "")
    assert login.password_ok("alice", password) is ok


class FakeContext:
    def __init__(self, ip, forwarded=None):
        self.ip_address = ip
        self.headers = {"X-Forwarded-For": forwarded} if forwarded else {}


@pytest.mark.parametrize(
    ("behind_proxy", "forwarded", "expected"),
    [
        (False, "6.6.6.6", "10.0.0.2"),  # no proxy: the header is the client's own claim
        (True, "6.6.6.6, 1.2.3.4", "1.2.3.4"),  # the address the proxy appended, not the spoofable first
        (True, None, "10.0.0.2"),
    ],
)
def test_lockout_address(monkeypatch, behind_proxy, forwarded, expected):
    monkeypatch.setattr(login, "BEHIND_PROXY", behind_proxy)
    monkeypatch.setattr(login.st, "context", FakeContext("10.0.0.2", forwarded))
    assert login.client_address() == expected


@pytest.mark.parametrize(("name", "ok"), [("Ada O'Brien", True), ("José", True), ("a\nb", False), ("<b>", False)])
def test_shared_login_names_follow_the_api_reviewer_rule(name, ok):
    assert bool(login.REVIEWER_NAME.match(name)) is ok


@pytest.mark.parametrize(
    ("stored", "require_login", "refused"),
    [
        ("scrypt==", False, True),  # a hash docker compose expanded "$..." out of: never plain text
        ("scrypt$only-salt", False, True),
        ("team-pw", False, False),  # plain text: local development
        ("team-pw", True, True),  # ...but not in a deployment
        (hash_password("pw"), True, False),
    ],
)
def test_passwords_that_must_not_be_used(stored, require_login, refused):
    assert (login.config_error([stored], require_login) is not None) is refused


def page_signed_in(monkeypatch, idle_seconds: float):
    import time

    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(login, "USERS", {"alice": hash_password("pw")})
    monkeypatch.setattr(login, "LOGIN_REQUIRED", True)
    monkeypatch.setattr(login, "CONFIG_ERROR", None)
    monkeypatch.setattr(login, "SESSION_TIMEOUT_SECONDS", 60)
    at = AppTest.from_file("../ui/streamlit_app.py")  # relative to this file
    at.session_state.user = "alice"
    at.session_state.last_active = time.monotonic() - idle_seconds
    return at.run(timeout=30)


def test_an_idle_session_is_signed_out(monkeypatch):
    at = page_signed_in(monkeypatch, idle_seconds=120)
    assert "user" not in at.session_state
    assert "You were signed out after a period of inactivity." in [i.value for i in at.info]


def test_an_active_session_stays_signed_in(monkeypatch):
    at = page_signed_in(monkeypatch, idle_seconds=10)
    assert at.session_state.user == "alice"


def test_the_ui_reads_the_api_key_from_a_file(tmp_path, monkeypatch):
    from ui import api_client

    secret = tmp_path / "api_key"
    secret.write_text("s3cret\n")
    monkeypatch.setenv("API_KEY_FILE", str(secret))
    monkeypatch.setenv("API_KEY", "from-env")
    assert api_client.read_api_key() == "s3cret"


def test_the_strong_match_message_is_not_shown_for_a_run_that_failed_early():
    from streamlit.testing.v1 import AppTest

    def page(screening):
        at = AppTest.from_file("../ui/streamlit_app.py")  # relative to this file
        at.session_state.screening = screening
        return at.run(timeout=30)

    base = {
        "thread_id": "t1", "candidate": None, "job_title": None, "match_percentage": None,
        "candidate_experience_years": None, "required_experience_years": None, "experience_gap_years": None,
        "revision_count": 0, "matching_skills": [], "missing_skills": [], "gap_analysis": None,
        "interview_questions": [], "reviews": [], "steps": ["validate_input", "parse_resume"], "status": "failed",
    }  # fmt: skip
    strong = "Strong match: gap analysis was skipped."
    assert strong not in [i.value for i in page(base).info]
    done = {**base, "status": "awaiting_review", "steps": [*base["steps"], "generate_questions"],
            "revisions_remaining": 3}  # fmt: skip
    assert strong in [i.value for i in page(done).info]


def test_the_ui_offers_the_breakpoints_the_api_accepts():
    # The UI image does not include app/, so it keeps its own copy of the list
    from app.services.inputs import BREAKPOINT_NODES
    from ui import streamlit_app

    assert tuple(streamlit_app.BREAKPOINT_NODES) == BREAKPOINT_NODES
