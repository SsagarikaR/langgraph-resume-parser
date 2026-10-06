import operator
from typing import TypedDict, Annotated


class ResumeState(TypedDict, total=False):
    resume_path: str
    job_description: str
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

    final_report: dict
    report_path: str

    steps: Annotated[list[str], operator.add]
