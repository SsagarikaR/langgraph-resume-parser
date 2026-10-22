import operator
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig


def config_for(thread_id: str) -> RunnableConfig:
    """The config that addresses one screening's state in the checkpointer."""
    return {"configurable": {"thread_id": thread_id}}


class ResumeState(TypedDict, total=False):
    resume_path: str
    job_description: str
    screened_on: str  # UTC date (YYYY-MM-DD) experience is counted up to
    resume_text: str

    # ---- Structured extraction stored as dictionaries ----
    resume: dict
    job: dict

    matching_skills: list[str]
    missing_skills: list[str]
    match_percentage: float
    candidate_experience_years: float
    experience_gap_years: float

    gap_analysis: dict
    interview_guidelines: str
    interview_questions: list[dict]

    approved: bool
    review_feedback: str
    revision_count: int
    # One entry per review decision (action, reviewer, client, time): the approval audit trail
    reviews: Annotated[list[dict], operator.add]

    final_report: dict

    # Static breakpoints chosen for this run (node names to pause after)
    breakpoints: list[str]

    steps: Annotated[list[str], operator.add]
