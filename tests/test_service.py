import gc
import logging
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.config.settings import get_settings
from app.exceptions.domain import (
    BudgetExceededError,
    InvalidInputError,
    InvalidScreeningInputError,
    NotAwaitingReviewError,
    ScreeningBusyError,
    ScreeningNotFoundError,
    ServerBusyError,
    WorkflowFailedError,
)
from app.services import inputs
from app.services.screening_service import ScreeningService
from tests.test_pdf import pdf_with_pages

PDF = b"%PDF-1.7 fake"
JD = "Senior Python engineer. " * 5


class FakeRepository:
    def __init__(self, run_error: Exception | None = None, values: dict | None = None):
        self.run_error = run_error
        self.values = values if values is not None else {"steps": ["validate_input"]}
        self.saved = []
        self.deleted = []
        self.inactive: list[str] = []
        self.owners: dict[str, str | None] = {}
        self.summaries: dict[str, dict | None] = {}
        self.state_reads = 0
        self.thread_id = None
        self.steps = [("started", "validate_input"), ("finished", "validate_input")]

    def list_screenings(self, owner, limit):
        return [
            (t, "2026-01-01T00:00:00+00:00", self.summaries.get(t))
            for t, o in self.owners.items()
            if owner is None or o == owner
        ]

    def save_summary(self, thread_id, summary):
        self.summaries[thread_id] = summary

    def upload_path(self, thread_id, kind, suffix):
        return f"/tmp/{thread_id}_{kind}{suffix}"

    def save_upload(self, thread_id, kind, data, suffix):
        self.thread_id = thread_id
        self.saved.append((kind, suffix))
        return f"/tmp/{thread_id}_{kind}{suffix}"

    def register(self, thread_id, owner):
        self.owners[thread_id] = owner

    def owner_of(self, thread_id):
        return self.owners.get(thread_id)

    def delete(self, thread_id):
        self.deleted.append(thread_id)

    def inactive_since(self, cutoff):
        return self.inactive

    def purge_stray_files(self, cutoff):
        pass

    def stream(self, graph_input, thread_id, interrupt_after=None, callbacks=None):
        if self.run_error:
            raise self.run_error
        yield from self.steps

    def get_state(self, thread_id):
        self.state_reads += 1
        return SimpleNamespace(values=self.values, tasks=[], next=())


def make_service(repo: FakeRepository) -> ScreeningService:
    return ScreeningService(repo, workflow=repo)  # type: ignore[arg-type]  # the fake also runs the "workflow"


def test_start_rejects_non_pdf_extension():
    with pytest.raises(InvalidScreeningInputError):
        make_service(FakeRepository()).start("cv.docx", PDF, jd_text=JD)


def test_start_rejects_file_that_is_not_really_a_pdf():
    with pytest.raises(InvalidScreeningInputError):
        make_service(FakeRepository()).start("cv.pdf", b"MZ\x90 not a pdf", jd_text=JD)


def test_start_rejects_unknown_breakpoint():
    with pytest.raises(InvalidScreeningInputError, match="Unknown breakpoint"):
        make_service(FakeRepository()).start("cv.pdf", PDF, jd_text=JD, breakpoints=["nope"])


def test_start_requires_job_description():
    with pytest.raises(InvalidScreeningInputError):
        make_service(FakeRepository()).start("cv.pdf", PDF)


def test_jd_file_must_be_utf8():
    with pytest.raises(InvalidScreeningInputError, match="UTF-8"):
        make_service(FakeRepository()).start("cv.pdf", PDF, jd_filename="jd.txt", jd_bytes=b"\xff\xfe\x00")


def test_jd_pdf_is_read_in_memory_and_never_saved():
    repo = FakeRepository()
    make_service(repo).start("cv.pdf", PDF, jd_filename="jd.pdf", jd_bytes=pdf_with_pages(1))
    assert repo.saved == [("resume", ".pdf")]  # only the resume, which the graph parses from disk


def test_unreadable_jd_pdf_is_rejected_before_the_resume_is_saved():
    repo = FakeRepository()
    with pytest.raises(InvalidScreeningInputError, match="Could not read the PDF"):
        make_service(repo).start("cv.pdf", PDF, jd_filename="jd.pdf", jd_bytes=b"%PDF-1.7 broken")
    assert repo.saved == []


def test_failure_log_has_no_exception_message(caplog):
    # LLM and validation errors can quote resume text; only the type reaches the error log
    repo = FakeRepository(run_error=RuntimeError("Ada Lovelace, ada@example.com"))
    with caplog.at_level(logging.INFO), pytest.raises(WorkflowFailedError):
        make_service(repo).start("cv.pdf", PDF, jd_text=JD)
    assert "RuntimeError" in caplog.text
    assert "Ada Lovelace" not in caplog.text


def test_node_input_error_maps_to_invalid_input():
    repo = FakeRepository(run_error=InvalidInputError("Job description is missing or too short"))
    with pytest.raises(InvalidScreeningInputError, match="too short"):
        make_service(repo).start("cv.pdf", PDF, jd_text=JD)


def test_plain_value_error_is_a_workflow_failure_not_user_error():
    # pydantic ValidationError (LLM output) subclasses ValueError; it must not become a 400
    repo = FakeRepository(run_error=ValueError("1 validation error for ResumeProfile"))
    with pytest.raises(WorkflowFailedError) as exc:
        make_service(repo).start("cv.pdf", PDF, jd_text=JD)
    assert "validation error" not in str(exc.value)  # internals stay in the logs


def test_get_unknown_thread_is_not_found():
    with pytest.raises(ScreeningNotFoundError):
        make_service(FakeRepository(values={})).get("missing")


def test_review_requires_awaiting_review_status():
    repo = FakeRepository(values={"steps": ["generate_report"], "final_report": {}})
    with pytest.raises(NotAwaitingReviewError):
        make_service(repo).review("t1", "approve")


def test_start_stream_yields_steps_then_the_result():
    repo = FakeRepository()
    events = list(make_service(repo).start_stream("cv.pdf", PDF, jd_text=JD))
    assert events[:2] == [
        {"type": "step", "thread_id": repo.thread_id, "node": "validate_input", "event": "started"},
        {"type": "step", "thread_id": repo.thread_id, "node": "validate_input", "event": "finished"},
    ]
    assert events[-1]["type"] == "result"
    assert events[-1]["screening"]["steps"] == ["validate_input"]


def test_start_stream_checks_input_before_streaming():
    # Raised by the call itself, before any event, so the API can still answer with a plain 400
    with pytest.raises(InvalidScreeningInputError):
        make_service(FakeRepository()).start_stream("cv.docx", PDF, jd_text=JD)


def test_failure_mid_stream_is_mapped():
    events = make_service(FakeRepository(run_error=RuntimeError("provider down"))).start_stream(
        "cv.pdf", PDF, jd_text=JD
    )
    with pytest.raises(WorkflowFailedError):
        list(events)


def test_jd_longer_than_the_limit_is_rejected_before_the_resume_is_saved(monkeypatch):
    monkeypatch.setenv("MAX_DOCUMENT_CHARS", "100")
    get_settings.cache_clear()
    repo = FakeRepository()
    with pytest.raises(InvalidScreeningInputError, match="longer than 100"):
        make_service(repo).start("cv.pdf", PDF, jd_text="x" * 101)
    assert repo.saved == []


def test_a_running_screening_cannot_be_deleted_until_the_run_ends():
    repo = FakeRepository()
    service = make_service(repo)
    events = service.start_stream("cv.pdf", PDF, jd_text=JD)
    next(events)  # run in progress: it holds the screening
    with pytest.raises(ScreeningBusyError):
        service.delete(repo.thread_id)
    list(events)
    service.delete(repo.thread_id)
    assert repo.deleted == [repo.thread_id]


def test_screening_is_released_when_the_caller_stops_at_the_result():
    repo = FakeRepository()
    service = make_service(repo)
    service.start("cv.pdf", PDF, jd_text=JD)  # final_view stops reading at the result event
    service.delete(repo.thread_id)
    assert repo.deleted == [repo.thread_id]


def test_purge_skips_a_screening_that_is_running():
    repo = FakeRepository()
    service = make_service(repo)
    events = service.start_stream("cv.pdf", PDF, jd_text=JD)
    next(events)
    repo.inactive = [repo.thread_id]
    assert service.purge_inactive_since(datetime.now(timezone.utc)) == 0
    assert repo.deleted == []
    list(events)


def test_a_screening_reads_as_running_while_a_request_executes_it():
    repo = FakeRepository()
    service = make_service(repo)
    events = service.start_stream("cv.pdf", PDF, jd_text=JD)
    assert next(events)["thread_id"] == repo.thread_id  # a client that loses the stream can find it
    assert service.get(repo.thread_id)["status"] == "running"
    result = list(events)[-1]
    assert result["screening"]["status"] == "failed"  # the run's own result shows the stored state
    assert service.get(repo.thread_id)["status"] == "failed"  # released: no longer running


def test_runs_over_the_server_limit_are_refused(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "1")
    get_settings.cache_clear()
    service = make_service(FakeRepository())
    running = service.start_stream("cv.pdf", PDF, jd_text=JD)
    next(running)  # holds the only slot
    with pytest.raises(ServerBusyError):
        service.start("cv.pdf", PDF, jd_text=JD)
    list(running)
    service.start("cv.pdf", PDF, jd_text=JD)  # the slot was released


def test_only_one_of_two_simultaneous_requests_gets_a_screening():
    service = make_service(FakeRepository())
    start = threading.Barrier(2)
    outcomes: list[str] = []

    def hold():
        start.wait()
        try:
            with service.guard.exclusive("t1"):
                outcomes.append("held")
                start_released.wait(timeout=2)
        except ScreeningBusyError:
            outcomes.append("busy")
            start_released.set()

    start_released = threading.Event()
    threads = [threading.Thread(target=hold) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert sorted(outcomes) == ["busy", "held"]
    with service.guard.exclusive("t1"):  # released afterwards
        pass


def test_a_review_names_the_person_the_client_acts_for():
    repo = FakeRepository()
    repo.owners["t1"] = "ui/alice"
    calls = []
    service = make_service(repo)
    service._require_status = lambda *a: {"breakpoints": []}  # type: ignore[method-assign]
    service._stream = lambda graph_input, *a, **kw: calls.append(graph_input.resume)  # type: ignore[method-assign]
    service.review_stream("t1", "approve", caller="ui/alice")
    assert calls == [
        {"action": "approve", "feedback": "", "reviewer": "alice", "reviewer_verified": True, "client": "ui"}
    ]


def test_a_run_the_server_refuses_raises_at_once_and_stores_nothing(monkeypatch):
    # Refused before the stream is returned, so a streaming client gets a 503 status, and the
    # resume is never written to disk for a run that does not happen
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "1")
    get_settings.cache_clear()
    repo = FakeRepository()
    service = make_service(repo)
    running = service.start_stream("cv.pdf", PDF, jd_text=JD)
    saved, owners = list(repo.saved), dict(repo.owners)
    with pytest.raises(ServerBusyError):
        service.start_stream("cv.pdf", PDF, jd_text=JD)  # no next(): raising needs no reading
    assert (repo.saved, repo.owners) == (saved, owners)
    list(running)


def test_a_busy_screening_is_refused_before_the_stream_is_returned():
    repo = FakeRepository()
    service = make_service(repo)
    running = service.start_stream("cv.pdf", PDF, jd_text=JD)
    repo.values = {"steps": ["match_requirements"], "breakpoints": ["match_requirements"]}
    service._require_status = lambda *a: {"breakpoints": []}  # type: ignore[method-assign]
    with pytest.raises(ScreeningBusyError):
        service.continue_stream(repo.thread_id, caller=None)
    list(running)


def test_a_stream_dropped_without_being_read_releases_the_screening(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "1")
    get_settings.cache_clear()
    repo = FakeRepository()
    service = make_service(repo)
    events = service.start_stream("cv.pdf", PDF, jd_text=JD)
    del events  # e.g. a client gone before the response started
    gc.collect()
    service.start("cv.pdf", PDF, jd_text=JD)  # the slot is free again
    service.delete(repo.thread_id)


def test_closing_a_stream_mid_run_releases_the_screening():
    repo = FakeRepository()
    service = make_service(repo)
    events = service.start_stream("cv.pdf", PDF, jd_text=JD)
    next(events)
    events.close()  # what the API does when the client goes away
    service.delete(repo.thread_id)
    assert repo.deleted == [repo.thread_id]


def test_an_authenticated_person_cannot_review_as_someone_else():
    repo = FakeRepository()
    calls = []
    service = make_service(repo)
    service._require_status = lambda *a: {"breakpoints": []}  # type: ignore[method-assign]
    service._stream = lambda graph_input, *a, **kw: calls.append(graph_input.resume)  # type: ignore[method-assign]
    service.review_stream("t1", "approve", reviewer="bob", caller="ui/alice")
    service.review_stream("t1", "approve", reviewer="Bob Smith", caller="ui")  # shared login: typed name
    # The key vouches for alice; a name typed at the shared login is recorded as a claim only
    assert [(c["reviewer"], c["reviewer_verified"]) for c in calls] == [("alice", True), ("Bob Smith", False)]


def test_listing_uses_the_summary_each_run_stores():
    done = {"steps": ["generate_report"], "final_report": {"ok": True}, "job": {"job_title": "SRE"}}
    repo = FakeRepository(values=done)
    service = make_service(repo)
    service.start("cv.pdf", PDF, jd_text=JD)
    assert repo.summaries[repo.thread_id]["status"] == "completed"
    reads = repo.state_reads
    listed = service.list_screenings()
    assert repo.state_reads == reads  # no state loaded per row
    assert listed[0]["job_title"] == "SRE"
    assert listed[0]["status"] == "completed"


def test_a_screening_without_a_summary_is_listed_from_its_state():
    repo = FakeRepository(values={"steps": ["generate_report"], "final_report": {"ok": True}})
    repo.owners["legacy"] = None
    assert make_service(repo).list_screenings()[0]["status"] == "completed"


def test_a_listed_screening_reads_as_running_while_it_runs():
    repo = FakeRepository(values={"steps": ["generate_report"], "final_report": {"ok": True}})
    service = make_service(repo)
    service.start("cv.pdf", PDF, jd_text=JD)
    with service.guard.exclusive(repo.thread_id):  # as a run in progress holds it
        assert service.list_screenings()[0]["status"] == "running"


def test_a_jd_pdf_is_read_only_once_the_run_has_a_slot(monkeypatch):
    # Reading a PDF is real work: a server with no free slot must refuse before doing it
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "1")
    get_settings.cache_clear()
    reads = []
    monkeypatch.setattr(inputs, "extract_text_from_pdf", lambda f: reads.append(f) or JD)
    service = make_service(FakeRepository())
    running = service.start_stream("cv.pdf", PDF, jd_text=JD)
    with pytest.raises(ServerBusyError):
        service.start_stream("cv.pdf", PDF, jd_filename="jd.pdf", jd_bytes=b"%PDF-1.7 x")
    assert reads == []
    list(running)
    service.start("cv.pdf", PDF, jd_filename="jd.pdf", jd_bytes=b"%PDF-1.7 x")
    assert len(reads) == 1


def test_a_jd_that_is_not_a_pdf_is_refused_without_waiting_for_a_slot():
    with pytest.raises(InvalidScreeningInputError, match="not a valid PDF"):
        make_service(FakeRepository()).start_stream("cv.pdf", PDF, jd_filename="jd.pdf", jd_bytes=b"MZ")


def test_new_runs_wait_once_the_daily_token_budget_is_used(monkeypatch):
    monkeypatch.setenv("DAILY_TOKEN_BUDGET", "100")
    get_settings.cache_clear()
    service = make_service(FakeRepository())
    service.token_budget.charge(100)
    with pytest.raises(BudgetExceededError) as refused:
        service.start("cv.pdf", PDF, jd_text=JD)
    assert refused.value.retry_after > 0


def test_a_zero_token_budget_means_no_ceiling(monkeypatch):
    monkeypatch.setenv("DAILY_TOKEN_BUDGET", "0")
    get_settings.cache_clear()
    service = make_service(FakeRepository())
    service.token_budget.charge(10**9)
    service.start("cv.pdf", PDF, jd_text=JD)


def test_final_view_releases_the_screening_at_once():
    repo = FakeRepository()
    service = make_service(repo)
    events = service.start_stream("cv.pdf", PDF, jd_text=JD)
    ScreeningService.final_view(events)
    with service.guard.exclusive(repo.thread_id):  # not waiting for the stream to be collected
        pass
