from typing import Literal

from app.core.config import get_settings
from app.graph.state import ResumeState


def route_after_matching(state: ResumeState) -> Literal["strong_match", "weak_match"]:
    settings = get_settings()
    if (
        state["match_percentage"] >= settings.match_threshold
        and state["experience_gap_years"] <= settings.experience_tolerance_years
    ):
        return "strong_match"
    return "weak_match"


def route_after_questions(state: ResumeState) -> Literal["review", "finalize"]:
    # The revision that reaches the limit is still applied (the questions were just regenerated
    # with its feedback); only then is the report written without another review
    revisions = state.get("revision_count", 0)
    if revisions and revisions >= get_settings().max_revisions:
        return "finalize"
    return "review"


def route_after_review(state: ResumeState) -> Literal["approved", "revise"]:
    return "approved" if state.get("approved") else "revise"
