from langchain_core.tools import tool

from app.knowledge.vector_store import search

NO_RESULTS = "No relevant guidelines found"


@tool
def lookup_interview_guideline(topic: str) -> str:
    """Search the company's interview guidelines by meaning. Pass one skill, technology or
    area per call (e.g. 'Kafka', 'async Python', 'caching', 'system design').
    Returns the most relevant focus areas and red flags for interviewers."""
    hits = search(topic)
    if not hits:
        return f"{NO_RESULTS} for '{topic}'."
    return "\n".join(f"[{h['topic']}] {h['kind']}: {h['text']} (relevance {h['score']})" for h in hits)
