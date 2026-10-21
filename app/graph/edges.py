from typing import Literal

from app.core.config import get_settings
from app.graph.state import ResumeState


def route_after_matching(state: ResumeState) -> Literal["strong_match", "weak_match"]:
    if state["match_percentage"] >= get_settings().match_threshold and state["experience_gap_years"] <= 0.5:
        return "strong_match"
    return "weak_match"


def route_after_review(state: ResumeState) -> Literal["approved", "revise"]:
    if state.get("approved") or state.get("revision_count", 0) >= get_settings().max_revisions:
        return "approved"
    return "revise"
