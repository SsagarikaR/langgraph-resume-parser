from types import SimpleNamespace

import pytest

from app.graph.exceptions import InvalidInputError
from app.services.exceptions import (
    InvalidScreeningInputError,
    NotAwaitingReviewError,
    ScreeningNotFoundError,
    WorkflowFailedError,
)
from app.services.screening_service import ScreeningService

PDF = b"%PDF-1.7 fake"
JD = "Senior Python engineer. " * 5


class FakeRepository:
    def __init__(self, run_error: Exception | None = None, values: dict | None = None):
        self.run_error = run_error
        self.values = values if values is not None else {"steps": ["validate_input"]}
        self.saved = []
        self.steps = [("started", "validate_input"), ("finished", "validate_input")]

    def save_upload(self, thread_id, kind, data, suffix):
        self.saved.append((kind, suffix))
        return f"/tmp/{thread_id}_{kind}{suffix}"

    def stream(self, graph_input, thread_id, interrupt_after=None):
        if self.run_error:
            raise self.run_error
        yield from self.steps

    def get_state(self, thread_id):
        return SimpleNamespace(values=self.values, tasks=[], next=())


def test_start_rejects_non_pdf_extension():
    with pytest.raises(InvalidScreeningInputError):
        ScreeningService(FakeRepository()).start("cv.docx", PDF, jd_text=JD)


def test_start_rejects_file_that_is_not_really_a_pdf():
    with pytest.raises(InvalidScreeningInputError):
        ScreeningService(FakeRepository()).start("cv.pdf", b"MZ\x90 not a pdf", jd_text=JD)


def test_start_rejects_unknown_breakpoint():
    with pytest.raises(InvalidScreeningInputError, match="Unknown breakpoint"):
        ScreeningService(FakeRepository()).start("cv.pdf", PDF, jd_text=JD, breakpoints=["nope"])


def test_start_requires_job_description():
    with pytest.raises(InvalidScreeningInputError):
        ScreeningService(FakeRepository()).start("cv.pdf", PDF)


def test_jd_file_must_be_utf8():
    with pytest.raises(InvalidScreeningInputError, match="UTF-8"):
        ScreeningService(FakeRepository()).start("cv.pdf", PDF, jd_filename="jd.txt", jd_bytes=b"\xff\xfe\x00")


def test_node_input_error_maps_to_invalid_input():
    repo = FakeRepository(run_error=InvalidInputError("Job description is missing or too short"))
    with pytest.raises(InvalidScreeningInputError, match="too short"):
        ScreeningService(repo).start("cv.pdf", PDF, jd_text=JD)


def test_plain_value_error_is_a_workflow_failure_not_user_error():
    # pydantic ValidationError (LLM output) subclasses ValueError; it must not become a 400
    repo = FakeRepository(run_error=ValueError("1 validation error for ResumeProfile"))
    with pytest.raises(WorkflowFailedError) as exc:
        ScreeningService(repo).start("cv.pdf", PDF, jd_text=JD)
    assert "validation error" not in str(exc.value)  # internals stay in the logs


def test_get_unknown_thread_is_not_found():
    with pytest.raises(ScreeningNotFoundError):
        ScreeningService(FakeRepository(values={})).get("missing")


def test_review_requires_awaiting_review_status():
    repo = FakeRepository(values={"steps": ["generate_report"], "final_report": {}})
    with pytest.raises(NotAwaitingReviewError):
        ScreeningService(repo).review("t1", "approve")


def test_start_stream_yields_steps_then_the_result():
    events = list(ScreeningService(FakeRepository()).start_stream("cv.pdf", PDF, jd_text=JD))
    assert events[:2] == [
        {"type": "step", "node": "validate_input", "event": "started"},
        {"type": "step", "node": "validate_input", "event": "finished"},
    ]
    assert events[-1]["type"] == "result"
    assert events[-1]["screening"]["steps"] == ["validate_input"]


def test_start_stream_checks_input_before_streaming():
    # Raised by the call itself, before any event, so the API can still answer with a plain 400
    with pytest.raises(InvalidScreeningInputError):
        ScreeningService(FakeRepository()).start_stream("cv.docx", PDF, jd_text=JD)


def test_failure_mid_stream_is_mapped():
    events = ScreeningService(FakeRepository(run_error=RuntimeError("provider down"))).start_stream(
        "cv.pdf", PDF, jd_text=JD
    )
    with pytest.raises(WorkflowFailedError):
        list(events)
