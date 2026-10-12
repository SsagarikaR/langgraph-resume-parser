from typing import Literal

from pydantic import BaseModel, Field


class GapAnalysis(BaseModel):
    technical_gaps: list[str]
    experience_gaps: list[str]
    project_gaps: list[str]
    transferable_strengths: list[str] = Field(
        description="Existing skills that partially compensate for gaps, e.g. FastAPI experience helps with Flask"
    )
    severity: Literal["low", "medium", "high"]
    improvement_suggestions: list[str]
    summary: str


class InterviewQuestion(BaseModel):
    topic: str
    question: str
    why_asked: str = Field(description="What this question tests about the candidate")
    type: Literal["strength", "gap", "project", "behavioral"]
    difficulty: Literal["easy", "medium", "hard"]


class InterviewQuestionSet(BaseModel):
    questions: list[InterviewQuestion]
