from langgraph.graph import StateGraph, START, END
from app.graph.state import ResumeState
from app.graph.nodes import validate_input, parse_resume

builder = StateGraph(ResumeState)

builder.add_node("validate_input", validate_input)
builder.add_node("parse_resume", parse_resume)

builder.add_edge(START, "validate_input")
builder.add_edge("validate_input", "parse_resume")
builder.add_edge("parse_resume", END)

graph = builder.compile()