"""The raw graph state of a screening turned into what clients see."""

from langgraph.types import StateSnapshot

from app.config.settings import get_settings
from app.exceptions.domain import ScreeningNotFoundError

# The fields of a view that GET /screenings lists
SUMMARY_FIELDS = ("status", "candidate", "job_title", "match_percentage")


def screening_view(thread_id: str, snapshot: StateSnapshot) -> dict:
    """Turn the raw graph state into the shape the UI needs."""
    values = snapshot.values
    if not values:
        raise ScreeningNotFoundError(f"Screening '{thread_id}' not found")

    interrupts = [i.value for task in snapshot.tasks for i in task.interrupts]
    errored = any(task.error for task in snapshot.tasks)
    steps = values.get("steps", [])
    if interrupts:
        status = "awaiting_review"
    elif not snapshot.next and values.get("final_report"):
        status = "completed"
    elif snapshot.next and not errored:
        # A static breakpoint stops right after one of the chosen nodes; anything else with work
        # left was cut off part-way (the client went away or the server restarted)
        at_breakpoint = bool(steps) and steps[-1] in values.get("breakpoints", [])
        status = "paused" if at_breakpoint else "stopped"
    else:
        status = "failed"  # stopped before finishing, e.g. an error in a node

    revision_count = values.get("revision_count", 0)
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
        "unverified_skills": resume.get("unverified_skills", []),
        "candidate_experience_years": values.get("candidate_experience_years"),
        "required_experience_years": job.get("required_experience_years"),
        "experience_gap_years": values.get("experience_gap_years"),
        "gap_analysis": values.get("gap_analysis"),
        "interview_questions": values.get("interview_questions", []),
        "interview_guidelines": values.get("interview_guidelines"),
        "approved": values.get("approved", False),
        "revision_count": revision_count,
        # Revisions a reviewer may still ask for. A revision asked for at 1 (or 0) is the last:
        # it is applied, then the report is written without another review
        "revisions_remaining": max(0, get_settings().max_revisions - revision_count),
        "reviews": values.get("reviews", []),
        "final_report": values.get("final_report"),
        "steps": steps,
        "breakpoints": values.get("breakpoints", []),
        "next_nodes": list(snapshot.next),
    }


def summary_of(view: dict) -> dict:
    return {field: view[field] for field in SUMMARY_FIELDS}
