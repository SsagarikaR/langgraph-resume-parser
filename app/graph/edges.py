from typing import Literal
from app.graph.state import ResumeState

MATCH_THRESHOLD = 70
MAX_REVISIONS = 3

def route_after_matching(state: ResumeState) -> Literal["strong_match", "weak_match"]:
    if state["match_percentage"] >= MATCH_THRESHOLD and state["experience_gap_years"] == 0:
        return "strong_match"
    return "weak_match"

def route_after_review(state: ResumeState) -> Literal["approved", "revise"]:
    if state.get("approved") or state.get("revision_count", 0) >= MAX_REVISIONS:
        return "approved"
    return "revise"