import os
import sqlite3
from functools import lru_cache
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy
from pydantic import ValidationError

from app.config.settings import get_settings
from app.exceptions.domain import InvalidInputError, LlmUnavailableError
from app.graph.edges import route_after_matching, route_after_questions, route_after_review
from app.graph.nodes import (
    analyze_gaps,
    analyze_job,
    extract_resume,
    generate_questions,
    generate_report,
    human_review,
    match_requirements,
    parse_resume,
    research_guidelines,
    validate_input,
)
from app.graph.state import ResumeState

# Structural failures that a retry cannot fix, plus the circuit breaker's own fail-fast signal
# (retrying it just re-raises immediately until the cooldown elapses); everything else (timeouts,
# 5xx, 429) retries
NON_RETRYABLE = (InvalidInputError, ValidationError, KeyError, TypeError, LlmUnavailableError)


def build_graph(db_path: Path | str) -> CompiledStateGraph:
    builder = StateGraph(ResumeState)

    llm_retry = RetryPolicy(
        max_attempts=get_settings().llm_node_attempts, retry_on=lambda e: not isinstance(e, NON_RETRYABLE)
    )
    builder.add_node("validate_input", validate_input)
    builder.add_node("parse_resume", parse_resume)
    builder.add_node("extract_resume", extract_resume, retry_policy=llm_retry)
    builder.add_node("analyze_job", analyze_job, retry_policy=llm_retry)
    builder.add_node("match_requirements", match_requirements)
    builder.add_node("analyze_gaps", analyze_gaps, retry_policy=llm_retry)
    builder.add_node("research_guidelines", research_guidelines, retry_policy=llm_retry)
    builder.add_node("generate_questions", generate_questions, retry_policy=llm_retry)
    builder.add_node("human_review", human_review)
    builder.add_node("generate_report", generate_report)

    builder.add_edge(START, "validate_input")
    builder.add_edge("validate_input", "parse_resume")
    # extract_resume and analyze_job run in parallel (both fan out from parse_resume, fan in at match_requirements)
    builder.add_edge("parse_resume", "extract_resume")
    builder.add_edge("parse_resume", "analyze_job")
    builder.add_edge(["extract_resume", "analyze_job"], "match_requirements")

    builder.add_conditional_edges(
        "match_requirements",
        route_after_matching,
        {"strong_match": "research_guidelines", "weak_match": "analyze_gaps"},
    )
    builder.add_edge("analyze_gaps", "research_guidelines")
    # Revisions loop back to generate_questions only: the guidelines found here are reused
    builder.add_edge("research_guidelines", "generate_questions")
    builder.add_conditional_edges(
        "generate_questions",
        route_after_questions,
        {"review": "human_review", "finalize": "generate_report"},
    )

    builder.add_conditional_edges(
        "human_review",
        route_after_review,
        {"approved": "generate_report", "revise": "generate_questions"},
    )
    builder.add_edge("generate_report", END)

    # Create the checkpoint DB with 0600 permissions (holds resume text); chmod WAL/SHM files too
    os.close(os.open(db_path, os.O_WRONLY | os.O_CREAT, 0o600))
    for path in (Path(db_path), Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if path.exists():
            path.chmod(0o600)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA secure_delete = ON")  # overwrite deleted rows with zeros (no data recovery)
    serde = JsonPlusSerializer(allowed_msgpack_modules=None)  # strict mode: no arbitrary class loading
    return builder.compile(checkpointer=SqliteSaver(conn, serde=serde))


@lru_cache
def get_graph() -> CompiledStateGraph:
    """The compiled graph, built on first use (not at import) with the configured checkpoint DB."""
    return build_graph(get_settings().checkpoint_db)
