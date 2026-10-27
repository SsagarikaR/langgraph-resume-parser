import logging

from langchain_core.tools import tool

from app.repository.vector_repository.vector_store import search

logger = logging.getLogger(__name__)

NO_RESULTS = "No relevant guidelines found"
SEARCH_FAILED = "Guideline search is unavailable"


@tool
def lookup_interview_guideline(topic: str) -> str:
    """Search the company's interview guidelines by meaning. Pass one skill, technology or
    area per call (e.g. 'Kafka', 'async Python', 'caching', 'system design').
    Returns the most relevant focus areas and red flags for interviewers."""
    try:
        hits = search(topic)
    except Exception:
        # Guidelines are optional context: a vector-store outage should not fail the screening
        # Not the topic: it comes from the resume (skill names), and error logs carry no resume data
        logger.exception("Guideline search failed")
        return f"{SEARCH_FAILED}; continue without guidelines for '{topic}'."
    if not hits:
        return f"{NO_RESULTS} for '{topic}'."
    return "\n".join(f"[{h['topic']}] {h['kind']}: {h['text']} (relevance {h['score']})" for h in hits)
