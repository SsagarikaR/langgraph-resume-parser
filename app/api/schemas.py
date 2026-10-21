from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ReviewRequest(BaseModel):
    action: Literal["approve", "revise"]
    feedback: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def _revise_needs_feedback(self):
        if self.action == "revise" and not self.feedback.strip():
            raise ValueError("feedback is required when action is 'revise'")
        return self


class ContinueRequest(BaseModel):
    # Optional correction at the match_requirements breakpoint: the required skills
    # the candidate actually has. Omit to continue without changes.
    matching_skills: list[str] | None = None


class ScreeningResponse(BaseModel):
    thread_id: str
    status: Literal["paused", "awaiting_review", "completed", "failed"]
    candidate: str | None = None
    job_title: str | None = None
    required_skills: list[str] = []
    match_percentage: float | None = None
    matching_skills: list[str] = []
    missing_skills: list[str] = []
    candidate_experience_years: float | None = None
    required_experience_years: float | None = None
    experience_gap_years: float | None = None
    gap_analysis: dict | None = None
    interview_questions: list[dict] = []
    interview_guidelines: str | None = None
    approved: bool = False
    revision_count: int = 0
    report_path: str | None = None
    final_report: dict | None = None
    steps: list[str] = []
    breakpoints: list[str] = []
    next_nodes: list[str] = []
