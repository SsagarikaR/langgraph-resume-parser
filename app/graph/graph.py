import sqlite3
from functools import lru_cache
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.config import get_settings
from app.graph.edges import route_after_matching, route_after_review
from app.graph.nodes import (
    analyze_gaps,
    analyze_job,
    extract_resume,
    generate_questions,
    generate_report,
    human_review,
    match_requirements,
    parse_resume,
    validate_input,
)
from app.graph.state import ResumeState


def build_graph(db_path: Path | str) -> CompiledStateGraph:
    builder = StateGraph(ResumeState)

    builder.add_node("validate_input", validate_input)
    builder.add_node("parse_resume", parse_resume)
    builder.add_node("extract_resume", extract_resume)
    builder.add_node("analyze_job", analyze_job)
    builder.add_node("match_requirements", match_requirements)
    builder.add_node("analyze_gaps", analyze_gaps)
    builder.add_node("generate_questions", generate_questions)
    builder.add_node("human_review", human_review)
    builder.add_node("generate_report", generate_report)

    builder.add_edge(START, "validate_input")
    builder.add_edge("validate_input", "parse_resume")
    builder.add_edge("parse_resume", "extract_resume")
    builder.add_edge("extract_resume", "analyze_job")
    builder.add_edge("analyze_job", "match_requirements")

    builder.add_conditional_edges(
        "match_requirements",
        route_after_matching,
        {"strong_match": "generate_questions", "weak_match": "analyze_gaps"},
    )
    builder.add_edge("analyze_gaps", "generate_questions")
    builder.add_edge("generate_questions", "human_review")

    builder.add_conditional_edges(
        "human_review",
        route_after_review,
        {"approved": "generate_report", "revise": "generate_questions"},
    )
    builder.add_edge("generate_report", END)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    # Strict mode: checkpoints only load built-in safe types, never arbitrary classes, so an
    # edited checkpoints.db cannot run code. The state is plain dicts, lists and scalars.
    serde = JsonPlusSerializer(allowed_msgpack_modules=None)
    return builder.compile(checkpointer=SqliteSaver(conn, serde=serde))


@lru_cache
def get_graph() -> CompiledStateGraph:
    """The compiled graph, built on first use (not at import) with the configured checkpoint DB."""
    return build_graph(get_settings().checkpoint_db)
