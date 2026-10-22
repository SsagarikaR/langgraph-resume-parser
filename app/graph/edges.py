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
    # The last revision is still applied before forcing finalize (no further review after the limit)
    revisions = state.get("revision_count", 0)
    if revisions and revisions >= get_settings().max_revisions:
        return "finalize"
    return "review"


def route_after_review(state: ResumeState) -> Literal["approved", "revise"]:
    return "approved" if state.get("approved") else "revise"
