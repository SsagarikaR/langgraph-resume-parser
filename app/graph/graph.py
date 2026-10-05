import sqlite3
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

from app.graph.state import ResumeState
from app.graph.nodes import (
    validate_input, parse_resume, extract_resume, analyze_job,
    match_requirements, analyze_gaps, generate_questions,
    human_review, generate_report,
)
from app.graph.edges import route_after_matching, route_after_review


def build_graph(db_path: str = "checkpoints.db"):
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
        "match_requirements", route_after_matching,
        {"strong_match": "generate_questions", "weak_match": "analyze_gaps"},
    )
    builder.add_edge("analyze_gaps", "generate_questions")
    builder.add_edge("generate_questions", "human_review")

    builder.add_conditional_edges(
        "human_review", route_after_review,
        {"approved": "generate_report", "revise": "generate_questions"},
    )
    builder.add_edge("generate_report", END)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    return builder.compile(checkpointer=SqliteSaver(conn))


graph = build_graph()