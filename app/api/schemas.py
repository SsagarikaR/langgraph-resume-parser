"""HTTP request and response bodies: the API's contract with its clients.

The resume, job and analysis data the workflow works with is in app/schemas/; the service
turns it into ScreeningResponse.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.analysis import GapAnalysis, InterviewQuestion
from app.schemas.resume import Education, Project

# running = another request is executing the workflow right now (the state shown is its last step)
# paused = stopped at a breakpoint; stopped = cut off part-way (client gone, server restart);
# failed = a step raised an error (e.g. the LLM provider was down). POST /continue resumes all three.
Status = Literal["running", "paused", "stopped", "awaiting_review", "completed", "failed"]


class ReviewRequest(BaseModel):
    action: Literal["approve", "revise"]
    feedback: str = Field("", max_length=2000)
    # The person deciding, for the audit trail, when the client acts for several people and has no
    # per-person login (the UI's shared login sends the typed name). Recorded with the API key's
    # name, which vouches for it; an X-Acting-User takes precedence. Letters, digits, spaces and
    # . , ' @ - only: it is logged, so a newline must not be able to forge a log line.
    reviewer: str | None = Field(None, min_length=1, max_length=100, pattern=r"^[\w .,'@-]+$")

    @model_validator(mode="after")
    def _revise_needs_feedback(self):
        if self.action == "revise" and not self.feedback.strip():
            raise ValueError("feedback is required when action is 'revise'")
        return self


class ContinueRequest(BaseModel):
    # Optional correction at the match_requirements breakpoint: the required skills
    # the candidate actually has. Omit to continue without changes.
    matching_skills: list[str] | None = None


class Review(BaseModel):
    """One review decision: the approval audit trail."""

    action: Literal["approve", "revise"] | None = None
    reviewer: str | None = None
    # True when an API key vouched for the name; False for a name typed at a shared login
    reviewer_verified: bool = False
    client: str | None = None  # the API key's name
    at: str
    feedback: str | None = None


class PreferredSkills(BaseModel):
    """The job's nice-to-have skills: informational, not part of the match percentage."""

    matching: list[str] = []
    missing: list[str] = []


class CandidateContact(BaseModel):
    name: str
    email: str | None = None
    education: list[Education] = []


class ExperienceSummary(BaseModel):
    candidate_years: float | None = None
    required_years: float | None = None
    gap_years: float | None = None


class FinalReport(BaseModel):
    candidate: CandidateContact
    job_title: str
    match_percentage: float
    matching_skills: list[str] = []
    missing_skills: list[str] = []
    preferred_skills: PreferredSkills = PreferredSkills()
    # Skills the LLM extracted that the resume text never mentions: left out of the match
    unverified_skills: list[str] = []
    experience: ExperienceSummary
    projects: list[Project] = []
    gap_analysis: GapAnalysis | None = None
    interview_guidelines: str | None = None
    interview_questions: list[InterviewQuestion] = []
    approved_by_human: bool = False
    approved_by: str | None = None
    reviews: list[Review] = []
    revisions: int = 0
    generated_at: str


class ScreeningResponse(BaseModel):
    thread_id: str
    status: Status
    candidate: str | None = None
    job_title: str | None = None
    required_skills: list[str] = []
    match_percentage: float | None = None
    matching_skills: list[str] = []
    missing_skills: list[str] = []
    unverified_skills: list[str] = []
    candidate_experience_years: float | None = None
    required_experience_years: float | None = None
    experience_gap_years: float | None = None
    gap_analysis: GapAnalysis | None = None
    interview_questions: list[InterviewQuestion] = []
    interview_guidelines: str | None = None
    approved: bool = False
    revision_count: int = 0
    # Revisions still allowed. Revising at 1 (or 0) applies the feedback, then writes the report
    # without another review.
    revisions_remaining: int = 0
    # Audit trail: one entry per review decision (action, reviewer, client key name, time)
    reviews: list[Review] = []
    final_report: FinalReport | None = None
    steps: list[str] = []
    breakpoints: list[str] = []
    next_nodes: list[str] = []


class ScreeningSummary(BaseModel):
    """One row of GET /screenings."""

    thread_id: str
    status: Status
    candidate: str | None = None
    job_title: str | None = None
    match_percentage: float | None = None
    created_at: str
