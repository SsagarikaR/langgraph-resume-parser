import uuid

from langgraph.types import Command, StateSnapshot

from app.graph.graph import graph
from app.repositories.screening_repository import ScreeningRepository, config_for
from app.services.exceptions import (
    InvalidScreeningInput, NotAwaitingReview, ScreeningNotFound, WorkflowFailed,
)
from app.tools.pdf_tools import extract_text_from_pdf


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
    ) -> dict:
        """Run the workflow until it pauses for human review (or fails)."""
        if not resume_filename.lower().endswith(".pdf"):
            raise InvalidScreeningInput("Resume must be a PDF")

        thread_id = uuid.uuid4().hex[:12]
        resume_path = self.repository.save_upload(thread_id, "resume", resume_bytes, ".pdf")
        job_description = self._read_job_description(thread_id, jd_text, jd_filename, jd_bytes)

        return self._run(
            {"resume_path": str(resume_path), "job_description": job_description},
            thread_id,
        )

    def get(self, thread_id: str) -> dict:
        return self._to_view(thread_id, self.repository.get_state(thread_id))

    def review(self, thread_id: str, action: str, feedback: str = "") -> dict:
        """Resume the paused workflow with the reviewer's decision."""
        if self.get(thread_id)["status"] != "awaiting_review":
            raise NotAwaitingReview("This screening is not waiting for review")
        return self._run(Command(resume={"action": action, "feedback": feedback}), thread_id)

    # ---------------- helpers ----------------
    def _run(self, graph_input, thread_id: str) -> dict:
        try:
            graph.invoke(graph_input, config_for(thread_id))
        except ValueError as e:          # input problems raised by validate_input / parse_resume
            raise InvalidScreeningInput(str(e)) from e
        except Exception as e:
            raise WorkflowFailed(f"{type(e).__name__}: {e}") from e
        return self.get(thread_id)

    def _read_job_description(
        self, thread_id: str, jd_text: str | None, jd_filename: str | None, jd_bytes: bytes | None
    ) -> str:
        if jd_text and jd_text.strip():
            return jd_text
        if jd_bytes is None:
            raise InvalidScreeningInput("Provide the job description as text or as a .txt/.pdf file")
        if (jd_filename or "").lower().endswith(".pdf"):
            path = self.repository.save_upload(thread_id, "jd", jd_bytes, ".pdf")
            return extract_text_from_pdf(path)
        return jd_bytes.decode("utf-8", errors="ignore")

    @staticmethod
    def _to_view(thread_id: str, snapshot: StateSnapshot) -> dict:
        """Turn the raw graph state into the shape the UI needs."""
        values = snapshot.values
        if not values:
            raise ScreeningNotFound(f"Screening '{thread_id}' not found")

        interrupts = [i.value for task in snapshot.tasks for i in task.interrupts]
        if interrupts:
            status = "awaiting_review"
        elif not snapshot.next and values.get("final_report"):
            status = "completed"
        else:
            status = "failed"          # stopped before finishing, e.g. an error in a node

        resume, job = values.get("resume") or {}, values.get("job") or {}
        return {
            "thread_id": thread_id,
            "status": status,
            "candidate": resume.get("name"),
            "job_title": job.get("job_title"),
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
        }
