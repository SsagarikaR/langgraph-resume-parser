import pytest

from app import cli


def test_start_applies_service_validation(tmp_path):
    # A renamed non-PDF is rejected by the service's magic-byte check, as it is in the API
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"MZ\x90 not a pdf")
    jd = tmp_path / "jd.txt"
    jd.write_text("Senior Python engineer. " * 5)

    with pytest.raises(SystemExit, match="Resume must be a PDF"):
        cli.main(["start", str(resume), str(jd)])


def test_missing_file_is_a_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["start", str(tmp_path / "nope.pdf"), str(tmp_path / "jd.txt")])
    assert exc.value.code == 2  # argparse usage error
    assert "nope.pdf" in capsys.readouterr().err


def test_review_unknown_thread_exits_with_message():
    with pytest.raises(SystemExit, match="not found"):
        cli.main(["review", "missing"])


class FakeService:
    """Records what the CLI asks of the service."""

    def __init__(self, view):
        self.view = view
        self.calls = []

    def get(self, thread_id):
        return self.view

    def review(self, thread_id, action, feedback, reviewer=None):
        self.calls.append(("review", action, feedback, reviewer))
        return {**self.view, "status": "completed"}

    def export_report(self, thread_id):
        return "/r.json"

    def continue_run(self, thread_id):
        self.calls.append(("continue", thread_id))
        return {**self.view, "status": "awaiting_review"}

    def delete(self, thread_id):
        self.calls.append(("delete", thread_id))

    def purge_expired(self, days):
        self.calls.append(("purge", days))
        return 2


VIEW = {
    "thread_id": "t1",
    "status": "awaiting_review",
    "candidate": "Ada",
    "job_title": "Engineer",
    "match_percentage": 80.0,
    "matching_skills": ["Python"],
    "missing_skills": [],
    "interview_questions": [{"difficulty": "easy", "question": "Why Python?"}],
    "revisions_remaining": 3,
}


@pytest.fixture
def fake(monkeypatch):
    service = FakeService(dict(VIEW))
    monkeypatch.setattr(cli, "create_service", lambda: service)
    return service


def test_review_records_the_os_user_as_reviewer(fake, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "a")
    monkeypatch.setattr(cli.getpass, "getuser", lambda: "ada")
    cli.main(["review", "t1"])
    assert fake.calls == [("review", "approve", "", "ada")]
    assert "Report saved to: /r.json" in capsys.readouterr().out


def test_review_warns_before_the_last_revision(fake, monkeypatch, capsys):
    fake.view["revisions_remaining"] = 1
    answers = iter(["r", "", "More on Kafka"])  # blank feedback is asked again
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    cli.main(["review", "t1"])
    assert fake.calls[0][:3] == ("review", "revise", "More on Kafka")
    assert "Last revision" in capsys.readouterr().out


def test_review_of_a_screening_not_awaiting_review_only_shows_it(fake, capsys):
    fake.view["status"] = "stopped"
    cli.main(["review", "t1"])
    assert fake.calls == []
    assert "python -m app.cli continue t1" in capsys.readouterr().out


def test_a_failed_screening_shows_how_to_retry_it(fake, capsys):
    fake.view["status"] = "failed"
    cli.main(["review", "t1"])
    assert "python -m app.cli continue t1" in capsys.readouterr().out


def test_continue_delete_and_purge(fake, capsys):
    cli.main(["continue", "t1"])
    cli.main(["delete", "t1"])
    cli.main(["purge", "--days", "7"])
    assert fake.calls == [("continue", "t1"), ("delete", "t1"), ("purge", 7)]
    out = capsys.readouterr().out
    assert "Deleted screening t1" in out
    assert "Erased 2 screening(s) idle for over 7 days" in out


def test_purge_refuses_when_retention_is_off(fake):
    with pytest.raises(SystemExit) as exc:  # RETENTION_DAYS=0 in tests
        cli.main(["purge"])
    assert exc.value.code == 2
