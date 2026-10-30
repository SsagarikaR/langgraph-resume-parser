from typing import Literal

from app.config.settings import get_settings
from app.graph.state import ResumeState


def route_after_matching(state: ResumeState) -> Literal["strong_match", "weak_match"]:
    settings = get_settings()
    if (
        state["match_percentage"] >= settings.match_threshold
        and state.get("experience_gap_years", 0) <= settings.experience_tolerance_years
    ):
        return "strong_match"
    return "weak_match"


def route_after_questions(state: ResumeState) -> Literal["review", "finalize"]:
    # The last revision is still applied before forcing finalize (no further review after the limit)
    revisions = state.get("revision_count", 0)
    max_rev = get_settings().max_revisions
    if revisions > 0 and (max_rev == 0 or revisions >= max_rev):
        return "finalize"
    return "review"


def route_after_review(state: ResumeState) -> Literal["approved", "revise"]:
    return "approved" if state.get("approved") else "revise"
