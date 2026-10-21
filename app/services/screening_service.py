import logging
import traceback
import uuid
from collections.abc import Callable, Generator, Iterator
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langgraph.types import Command

from app.core.config import get_settings
from app.graph.exceptions import InvalidInputError
from app.graph.graph import get_graph
from app.graph.runner import WorkflowRunner
from app.repositories.screening_repository import ScreeningRepository
from app.services.concurrency import HeldRun, RunGuard
from app.services.exceptions import (
    InvalidScreeningInputError,
    NotAwaitingReviewError,
    NotPausedError,
    ScreeningBusyError,
    ScreeningNotFoundError,
    WorkflowFailedError,
)
from app.services.inputs import (
    BREAKPOINT_NODES,
    PDF_MAGIC,
    THREAD_ID,
    THREAD_ID_PATTERN,
    check_breakpoints,
    check_length,
    check_resume,
    is_pdf_name,
    read_job_description,
    skill_match_update,
)
from app.services.run_stats import RunStats
from app.services.token_budget import TokenBudget
from app.services.views import screening_view, summary_of

__all__ = ["BREAKPOINT_NODES", "RESUMABLE", "THREAD_ID_PATTERN", "ScreeningService", "create_service"]

# Statuses /continue resumes: a breakpoint pause, a run cut off part-way (client gone, restart), or
# one a step failed in (e.g. the LLM provider was down): LangGraph re-runs the failed step. Input
# errors never leave a failed screening behind: the screening is erased (see _stream).
RESUMABLE = ("paused", "stopped", "failed")

logger = logging.getLogger(__name__)


class ScreeningService:
    """Business logic for screenings: start, read and review. Knows nothing about HTTP.

    Each workflow operation has a *_stream form that yields progress events while the graph
    runs, then a final {"type": "result", "screening": ...} event. Input checks, and taking the
    screening and a run slot, happen before the stream is returned, so they raise straight away
    (a streaming client gets a 400, 409 or 503 status, not an error event). The plain forms run
    the same stream to the end, so both share one code path.

    `caller` is the authenticated client: the API key's name, or "key/user" when a client acting
    for several people (the UI) names the person. A screening belongs to the caller that started
    it, and other callers get ScreeningNotFoundError for it, as if it did not exist. None means no
    access control: the CLI, an API with no keys configured, or a read by an audit key (the API
    lets those keys only read). Screenings started that way (owner None) are not visible to
    authenticated callers.

    A screening runs or is deleted by one request at a time: a second request for the same
    screening meanwhile gets ScreeningBusyError (409), instead of resuming the same review twice
    or deleting state a run is still writing. That, and the cap on concurrent runs, is RunGuard's
    job (app/services/concurrency.py); input checks are in app/services/inputs.py.
    """

    def __init__(self, repository: ScreeningRepository, workflow: WorkflowRunner | None = None):
        self.repository = repository
        self.workflow = workflow or WorkflowRunner()
        self.guard = RunGuard(get_settings().max_concurrent_runs)
        self.token_budget = TokenBudget(lambda: get_settings().daily_token_budget)

    # ---------------- public operations ----------------
    def start(
        self,
        resume_filename: str,
        resume_bytes: bytes,
        jd_text: str | None = None,
        jd_filename: str | None = None,
        jd_bytes: bytes | None = None,
        breakpoints: list[str] | None = None,
        caller: str | None = None,
    ) -> dict:
        """Run the workflow until it pauses for human review, hits a breakpoint, or fails."""
        return self.final_view(
            self.start_stream(resume_filename, resume_bytes, jd_text, jd_filename, jd_bytes, breakpoints, caller)
        )

    def start_stream(
        self,
        resume_filename: str,
        resume_bytes: bytes,
        jd_text: str | None = None,
        jd_filename: str | None = None,
        jd_bytes: bytes | None = None,
        breakpoints: list[str] | None = None,
        caller: str | None = None,
    ) -> Iterator[dict]:
        check_resume(resume_filename, resume_bytes)
        breakpoints = check_breakpoints(breakpoints)

        # A JD given as text is checked now. A PDF one is only checked for its signature here, and
        # read under the hold below: reading a PDF is real work, so it waits for a run slot
        jd_pdf = jd_bytes if not (jd_text and jd_text.strip()) and is_pdf_name(jd_filename) else None
        if jd_pdf is not None and not jd_pdf.startswith(PDF_MAGIC):
            raise InvalidScreeningInputError("Job description file is not a valid PDF")
        job_description = None if jd_pdf is not None else read_job_description(jd_text, jd_filename, jd_bytes)
        if job_description is not None:
            check_length(job_description)
        thread_id = uuid.uuid4().hex[:12]
        resume_path = self.repository.upload_path(thread_id, "resume", ".pdf")
        graph_input = {"resume_path": str(resume_path), "job_description": job_description, "breakpoints": breakpoints}

        def before() -> None:
            # Under the hold, JD first: a run the server refuses (all slots busy) or a JD that is
            # rejected never writes the resume to disk
            if jd_pdf is not None:
                graph_input["job_description"] = check_length(read_job_description(None, ".pdf", jd_pdf))
            self.repository.save_upload(thread_id, "resume", resume_bytes, ".pdf")
            self.repository.register(thread_id, owner=caller)
            logger.info("Starting screening %s for %s (breakpoints=%s)", thread_id, caller or "anyone", breakpoints)

        return self._stream(
            graph_input,
            thread_id,
            breakpoints,
            before,
        )

    def list_screenings(self, caller: str | None = None, limit: int = 50) -> list[dict]:
        """The caller's newest screenings, newest first, as summaries.

        Each run stores its screening's summary when it ends, so a listing reads one table instead
        of loading every screening's state. A screening without one (its last run failed or was cut
        off, or it predates summaries) is read from its state.
        """
        summaries = []
        for thread_id, created_at, summary in self.repository.list_screenings(caller, limit):
            if summary is None:
                try:
                    summary = summary_of(self._view(thread_id, caller))
                except ScreeningNotFoundError:
                    continue  # registered, but its run never saved a step
            summaries.append({"thread_id": thread_id, **self._live(thread_id, summary), "created_at": created_at})
        return summaries

    def get(self, thread_id: str, caller: str | None = None) -> dict:
        """The screening as clients see it: "running" while another request is executing it, so a
        client that lost its stream does not mistake the last checkpoint for a pause."""
        return self._live(thread_id, self._view(thread_id, caller))

    def review(
        self, thread_id: str, action: str, feedback: str = "", reviewer: str | None = None, caller: str | None = None
    ) -> dict:
        """Resume the paused workflow with the reviewer's decision."""
        return self.final_view(self.review_stream(thread_id, action, feedback, reviewer, caller))

    def review_stream(
        self, thread_id: str, action: str, feedback: str = "", reviewer: str | None = None, caller: str | None = None
    ) -> Iterator[dict]:
        """The review is recorded under the person the caller acts for ("ui/alice": the key vouches
        for alice), else `reviewer` (a name the client gives, e.g. the one typed at the UI's shared
        login), else the caller's key name. An authenticated person cannot record a review as
        someone else by passing another `reviewer`. `reviewer_verified` says whether an API key
        vouched for the name (False for a typed name, or with no access control at all)."""
        view = self._require_status(thread_id, ("awaiting_review",), caller)
        if action == "revise" and view["revisions_remaining"] == 0:
            # Only when MAX_REVISIONS=0: otherwise the last allowed revision is still applied
            raise InvalidScreeningInputError("Revisions are turned off (MAX_REVISIONS=0): approve the questions")
        client, _, user = (caller or "").partition("/")
        decision = {
            "action": action,
            "feedback": feedback,
            "reviewer": user or reviewer or client or None,
            "reviewer_verified": bool(user or (client and not reviewer)),
            "client": client or None,
        }

        def before() -> None:
            self._require_status(thread_id, ("awaiting_review",), caller)
            logger.info("Screening %s: %s by %s", thread_id, action, decision["reviewer"] or "unnamed reviewer")

        return self._stream(Command(resume=decision), thread_id, view["breakpoints"], before)

    def continue_run(self, thread_id: str, matching_skills: list[str] | None = None, caller: str | None = None) -> dict:
        """Resume a run stopped at a breakpoint, cut off part-way or failed in a step, optionally
        correcting the skill match first."""
        return self.final_view(self.continue_stream(thread_id, matching_skills, caller))

    def continue_stream(
        self, thread_id: str, matching_skills: list[str] | None = None, caller: str | None = None
    ) -> Iterator[dict]:
        view = self._require_status(thread_id, RESUMABLE, caller)
        if matching_skills is not None:
            # Checked now so a bad correction is a plain 400; written once the run holds the screening
            skill_match_update(self.repository.get_state(thread_id).values, matching_skills)

        def before() -> None:
            self._require_status(thread_id, RESUMABLE, caller)
            if matching_skills is not None:
                update = skill_match_update(self.repository.get_state(thread_id).values, matching_skills)
                # as_node makes LangGraph treat this as match_requirements' output,
                # so the strong/weak routing is re-evaluated against the corrected numbers
                self.repository.update_state(thread_id, update, as_node="match_requirements")

        # Input None = "carry on from the saved checkpoint"
        return self._stream(None, thread_id, view["breakpoints"], before)

    def delete(self, thread_id: str, caller: str | None = None) -> None:
        """Erase a screening and everything stored for it (resume text, report, uploads)."""
        with self.guard.exclusive(thread_id):
            self._view(thread_id, caller)  # not found (or not the caller's) → 404, not a silent no-op
            self.repository.delete(thread_id)
        logger.info("Deleted screening %s for %s", thread_id, caller or "anyone")

    def export_report(self, thread_id: str, caller: str | None = None) -> Path:
        """Write a completed screening's report to the reports directory and return its path."""
        view = self._view(thread_id, caller)
        if view["status"] != "completed":
            raise InvalidScreeningInputError("Only a completed screening has a report")
        return self.repository.save_report(thread_id, view["final_report"])

    def purge_expired(self, retention_days: int) -> int:
        """Erase screenings idle for `retention_days` days. Returns how many were erased."""
        count = self.purge_inactive_since(datetime.now(timezone.utc) - timedelta(days=retention_days))
        if count:
            logger.info("Retention: erased %d screening(s) idle for over %d days", count, retention_days)
        return count

    def purge_inactive_since(self, cutoff: datetime) -> int:
        """Erase screenings with no activity since `cutoff`, skipping any that are running now,
        and stray files older than that. Returns how many screenings were erased."""
        count = 0
        for thread_id in self.repository.inactive_since(cutoff):
            try:
                with self.guard.exclusive(thread_id):
                    self.repository.delete(thread_id)
                count += 1
            except ScreeningBusyError:
                pass  # in use, so not idle after all; the next purge looks again
        self.repository.purge_stray_files(cutoff)
        return count

    # ---------------- helpers ----------------
    def _live(self, thread_id: str, view: dict) -> dict:
        """`view` with status "running" while a request is executing the screening, so a client
        that lost its stream does not mistake the last checkpoint for a pause."""
        return {**view, "status": "running"} if self.guard.is_held(thread_id) else view

    def _hold(self, thread_id: str) -> ExitStack:
        """Take a run slot and the screening (BudgetExceededError once the day's LLM token
        budget is used up); close the stack to release both."""
        self.token_budget.check()
        return self.guard.hold(thread_id)

    def _view(self, thread_id: str, caller: str | None = None) -> dict:
        """The stored state of the screening (callers holding it must not see "running")."""
        # Same answer as a missing screening, so other callers cannot tell that it exists. A
        # screening with no owner (CLI or open API) is not shared with authenticated callers either.
        # An id that could not be a screening (e.g. "../x") is not found either, before any lookup.
        if not THREAD_ID.match(thread_id) or (caller is not None and self.repository.owner_of(thread_id) != caller):
            raise ScreeningNotFoundError(f"Screening '{thread_id}' not found")
        return screening_view(thread_id, self.repository.get_state(thread_id))

    def _require_status(self, thread_id: str, expected: tuple[str, ...], caller: str | None) -> dict:
        # Checks the stored state; a screening running elsewhere is refused by RunGuard (409)
        view = self._view(thread_id, caller)
        if view["status"] not in expected:
            if "paused" in expected:
                raise NotPausedError("This screening is not paused, stopped part-way or failed")
            raise NotAwaitingReviewError("This screening is not waiting for review")
        return view

    def _stream(
        self, graph_input, thread_id: str, breakpoints: list[str], before: Callable[[], object] | None = None
    ) -> Iterator[dict]:
        """Take the screening and a run slot, then return the run's events. `before` runs first
        under the hold, to re-check the status the caller saw: another request may have run it in
        between."""
        hold = self._hold(thread_id)
        try:
            if before:
                before()
        except BaseException:
            hold.close()
            raise
        return HeldRun(self._run(graph_input, thread_id, breakpoints, hold), hold)

    def _run(self, graph_input, thread_id: str, breakpoints: list[str], hold: ExitStack) -> Generator[dict, None, None]:
        with hold:
            stats = RunStats(thread_id)
            self.repository.save_summary(thread_id, None)  # stale until this run ends
            outcome = "aborted"  # stays so if the caller stops reading mid-run
            try:
                # Static breakpoints apply per run, so they are passed on every resume
                for event, node in self.workflow.stream(
                    graph_input, thread_id, interrupt_after=breakpoints, callbacks=[stats.usage]
                ):
                    stats.record(event, node)
                    # The thread id lets a client whose stream drops find the screening again
                    yield {"type": "step", "thread_id": thread_id, "node": node, "event": event}
                outcome = "ok"
            except InvalidInputError as e:  # input problems raised by validate_input / parse_resume / analyze_job
                outcome = "invalid_input"
                # Unusable input cannot be resumed into anything: erase what was stored for it
                self.repository.delete(thread_id)
                raise InvalidScreeningInputError(str(e)) from e
            except Exception as e:
                outcome = "failed"
                # The client gets no internals (provider errors, paths). Exception messages can quote
                # resume text (LLM output, validation errors), so the error log has only the type and
                # stack frames; the full messages are logged at DEBUG for troubleshooting.
                stack = "".join(traceback.format_tb(e.__traceback__))
                logger.error("Screening %s failed with %s\n%s", thread_id, type(e).__name__, stack.rstrip())
                logger.debug("Screening %s failure details", thread_id, exc_info=True)
                raise WorkflowFailedError(
                    f"Screening {thread_id} failed ({type(e).__name__}). See server logs for details."
                ) from e
            finally:
                self.token_budget.charge(stats.finish(outcome))
            screening = self._view(thread_id)
            self.repository.save_summary(thread_id, summary_of(screening))
        # Released before the last event: a caller that stops reading at the result
        # must not keep the screening held
        yield {"type": "result", "screening": screening}

    @staticmethod
    def final_view(events: Iterator[dict]) -> dict:
        """Run a stream to the end and return its result, for callers that don't show progress."""
        try:
            for event in events:
                if event["type"] == "result":
                    return event["screening"]
        finally:
            close = getattr(events, "close", None)
            if close:
                close()  # releases the screening now, not whenever the stream is collected
        raise WorkflowFailedError("Workflow ended without a result")


def create_service() -> ScreeningService:
    """The service wired to the compiled graph and its checkpointer (what the API and CLI use)."""
    return ScreeningService(ScreeningRepository(get_graph), WorkflowRunner())
