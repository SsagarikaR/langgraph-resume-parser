import logging
import uuid

from langgraph.types import Command, StateSnapshot

from app.graph.errors import InvalidInputError
from app.repositories.screening_repository import ScreeningRepository
from app.services.exceptions import (
    InvalidScreeningInput, NotAwaitingReview, NotPaused, ScreeningNotFound, WorkflowFailed,
)
from app.tools.pdf_tools import extract_text_from_pdf

# Nodes a run may pause after (static breakpoints, i.e. LangGraph's interrupt_after)
BREAKPOINT_NODES = ("match_requirements", "analyze_gaps", "generate_questions")
PDF_MAGIC = b"%PDF-"

logger = logging.getLogger(__name__)


class ScreeningService:
    """Business logic for screenings: start, read and review. Knows nothing about HTTP."""

    def __init__(self, repository: ScreeningRepository):
        self.repository = repository

    # ---------------- public operations ----------------
    def start(
        self,
        resume_filename: str,
        resume_bytes: bytes,
        jd_text: str | None = None,
        jd_filename: str | None = None,
        jd_bytes: bytes | None = None,
        breakpoints: list[str] | None = None,
    ) -> dict:
        """Run the workflow until it pauses for human review, hits a breakpoint, or fails."""
        if not resume_filename.lower().endswith(".pdf") or not resume_bytes.startswith(PDF_MAGIC):
            raise InvalidScreeningInput("Resume must be a PDF")
        breakpoints = list(dict.fromkeys(breakpoints or []))
        unknown = set(breakpoints) - set(BREAKPOINT_NODES)
        if unknown:
            raise InvalidScreeningInput(
                f"Unknown breakpoint(s) {sorted(unknown)}; choose from {list(BREAKPOINT_NODES)}"
            )

        thread_id = uuid.uuid4().hex[:12]
        logger.info("Starting screening %s (breakpoints=%s)", thread_id, breakpoints)
        resume_path = self.repository.save_upload(thread_id, "resume", resume_bytes, ".pdf")
        job_description = self._read_job_description(thread_id, jd_text, jd_filename, jd_bytes)

        return self._run(
            {"resume_path": str(resume_path), "job_description": job_description,
             "breakpoints": breakpoints},
            thread_id,
            breakpoints,
        )

    def get(self, thread_id: str) -> dict:
        return self._to_view(thread_id, self.repository.get_state(thread_id))

    def review(self, thread_id: str, action: str, feedback: str = "") -> dict:
        """Resume the paused workflow with the reviewer's decision."""
        view = self.get(thread_id)
        if view["status"] != "awaiting_review":
            raise NotAwaitingReview("This screening is not waiting for review")
        return self._run(
            Command(resume={"action": action, "feedback": feedback}), thread_id, view["breakpoints"]
        )

    def continue_run(self, thread_id: str, matching_skills: list[str] | None = None) -> dict:
        """Resume a run stopped at a breakpoint, optionally correcting the skill match first."""
        snapshot = self.repository.get_state(thread_id)
        view = self._to_view(thread_id, snapshot)
        if view["status"] != "paused":
            raise NotPaused("This screening is not paused at a breakpoint")

        if matching_skills is not None:
            self._override_skill_match(thread_id, snapshot.values, matching_skills)

        # Input None = "carry on from the saved checkpoint"
        return self._run(None, thread_id, view["breakpoints"])

    # ---------------- helpers ----------------
    def _override_skill_match(self, thread_id: str, values: dict, matching_skills: list[str]) -> None:
        if values["steps"][-1] != "match_requirements":
            raise InvalidScreeningInput("Skills can only be edited at the match_requirements breakpoint")
        required = values["job"]["required_skills"]
        unknown = set(matching_skills) - set(required)
        if unknown:
            raise InvalidScreeningInput(f"Not required skills for this job: {sorted(unknown)}")

        matching = [s for s in required if s in matching_skills]
        missing = [s for s in required if s not in matching_skills]
        pct = round(100 * len(matching) / len(required), 1) if required else 100.0
        # as_node makes LangGraph treat this as match_requirements' output,
        # so the strong/weak routing is re-evaluated against the corrected numbers
        self.repository.update_state(
            thread_id,
            {"matching_skills": matching, "missing_skills": missing, "match_percentage": pct,
             "steps": ["breakpoint:skills_edited"]},
            as_node="match_requirements",
        )

    def _run(self, graph_input, thread_id: str, breakpoints: list[str]) -> dict:
        try:
            # Static breakpoints apply per invoke call, so they are passed on every resume
            self.repository.run(graph_input, thread_id, interrupt_after=breakpoints)
        except InvalidInputError as e:   # input problems raised by validate_input / parse_resume
            raise InvalidScreeningInput(str(e)) from e
        except Exception as e:
            # Full details go to the log; the client gets no internals (provider errors, paths)
            logger.exception("Screening %s failed", thread_id)
            raise WorkflowFailed(
                f"Screening {thread_id} failed ({type(e).__name__}). See server logs for details."
            ) from e
        return self.get(thread_id)

    def _read_job_description(
        self, thread_id: str, jd_text: str | None, jd_filename: str | None, jd_bytes: bytes | None
    ) -> str:
        if jd_text and jd_text.strip():
            return jd_text
        if jd_bytes is None:
            raise InvalidScreeningInput("Provide the job description as text or as a .txt/.pdf file")
        if (jd_filename or "").lower().endswith(".pdf"):
            if not jd_bytes.startswith(PDF_MAGIC):
                raise InvalidScreeningInput("Job description file is not a valid PDF")
            path = self.repository.save_upload(thread_id, "jd", jd_bytes, ".pdf")
            return extract_text_from_pdf(path)
        try:
            return jd_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise InvalidScreeningInput("Job description file must be UTF-8 text or a PDF") from e

    @staticmethod
    def _to_view(thread_id: str, snapshot: StateSnapshot) -> dict:
        """Turn the raw graph state into the shape the UI needs."""
        values = snapshot.values
        if not values:
            raise ScreeningNotFound(f"Screening '{thread_id}' not found")

        interrupts = [i.value for task in snapshot.tasks for i in task.interrupts]
        errored = any(task.error for task in snapshot.tasks)
        if interrupts:
            status = "awaiting_review"
        elif not snapshot.next and values.get("final_report"):
            status = "completed"
        elif snapshot.next and not errored:
            status = "paused"          # stopped at a static breakpoint
        else:
            status = "failed"          # stopped before finishing, e.g. an error in a node

        resume, job = values.get("resume") or {}, values.get("job") or {}
        return {
            "thread_id": thread_id,
            "status": status,
            "candidate": resume.get("name"),
            "job_title": job.get("job_title"),
            "required_skills": job.get("required_skills", []),
            "match_percentage": values.get("match_percentage"),
            "matching_skills": values.get("matching_skills", []),
            "missing_skills": values.get("missing_skills", []),
            "candidate_experience_years": values.get("candidate_experience_years"),
            "required_experience_years": job.get("required_experience_years"),
            "experience_gap_years": values.get("experience_gap_years"),
            "gap_analysis": values.get("gap_analysis"),
            "interview_questions": values.get("interview_questions", []),
            "interview_guidelines": values.get("interview_guidelines"),
            "approved": values.get("approved", False),
            "revision_count": values.get("revision_count", 0),
            "report_path": values.get("report_path"),
            "final_report": values.get("final_report"),
            "steps": values.get("steps", []),
            "breakpoints": values.get("breakpoints", []),
            "next_nodes": list(snapshot.next),
        }
