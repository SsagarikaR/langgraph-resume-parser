"""Checks on what clients send before a screening runs: uploads, the job description, breakpoints,
thread ids and the reviewer's skill correction. Each raises InvalidScreeningInputError (400)."""

import io
import re

from app.core.config import get_settings
from app.services.exceptions import InvalidScreeningInputError
from app.utils.pdf import UnreadablePdfError, extract_text_from_pdf

# Nodes a run may pause after (static breakpoints, i.e. LangGraph's interrupt_after)
BREAKPOINT_NODES = ("match_requirements", "analyze_gaps", "generate_questions")
PDF_MAGIC = b"%PDF-"
# New ids are uuid4 hex; older CLI runs used names the user picked, so allow a safe superset.
# Checked by the service, not only by the API, since ids become file names (uploads, reports).
THREAD_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
THREAD_ID = re.compile(THREAD_ID_PATTERN)


def check_resume(filename: str, data: bytes) -> None:
    if not filename.lower().endswith(".pdf") or not data.startswith(PDF_MAGIC):
        raise InvalidScreeningInputError("Resume must be a PDF")


def check_breakpoints(breakpoints: list[str] | None) -> list[str]:
    """The breakpoints without duplicates, in the order given."""
    unique = list(dict.fromkeys(breakpoints or []))
    unknown = set(unique) - set(BREAKPOINT_NODES)
    if unknown:
        raise InvalidScreeningInputError(
            f"Unknown breakpoint(s) {sorted(unknown)}; choose from {list(BREAKPOINT_NODES)}"
        )
    return unique


def is_pdf_name(filename: str | None) -> bool:
    return (filename or "").lower().endswith(".pdf")


def check_length(job_description: str) -> str:
    limit = get_settings().max_document_chars
    if len(job_description.strip()) > limit:
        raise InvalidScreeningInputError(f"Job description is longer than {limit} characters")
    return job_description


def read_job_description(jd_text: str | None, jd_filename: str | None, jd_bytes: bytes | None) -> str:
    if jd_text and jd_text.strip():
        return jd_text
    if jd_bytes is None:
        raise InvalidScreeningInputError("Provide the job description as text or as a .txt/.pdf file")
    if is_pdf_name(jd_filename):
        if not jd_bytes.startswith(PDF_MAGIC):
            raise InvalidScreeningInputError("Job description file is not a valid PDF")
        try:  # read in memory: the JD file is never written to disk
            return extract_text_from_pdf(io.BytesIO(jd_bytes))
        except UnreadablePdfError as e:
            raise InvalidScreeningInputError(f"Job description: {e}") from e
    try:
        return jd_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise InvalidScreeningInputError("Job description file must be UTF-8 text or a PDF") from e


def skill_match_update(values: dict, matching_skills: list[str]) -> dict:
    """The corrected match_requirements output for the reviewer's list of matching skills."""
    if "match_requirements" not in values["steps"]:
        raise InvalidScreeningInputError("Skills can only be edited at the match_requirements breakpoint")
    required = values["job"]["required_skills"]
    unknown = set(matching_skills) - set(required)
    if unknown:
        raise InvalidScreeningInputError(f"Not required skills for this job: {sorted(unknown)}")

    matching = [s for s in required if s in matching_skills]
    missing = [s for s in required if s not in matching_skills]
    pct = round(100 * len(matching) / len(required), 1) if required else 100.0
    return {
        "matching_skills": matching,
        "missing_skills": missing,
        "match_percentage": pct,
        "steps": ["breakpoint:skills_edited"],
    }
