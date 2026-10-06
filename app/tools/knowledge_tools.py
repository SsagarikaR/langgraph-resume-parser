from pathlib import Path
from langchain_core.tools import tool

KB_DIR = Path("data/knowledge_base")


def available_topics() -> list[str]:
    return sorted(p.stem for p in KB_DIR.glob("*.md"))


@tool
def lookup_interview_guideline(topic: str) -> str:
    """Look up the company's interview guidelines for a technical topic
    (e.g. 'python', 'langgraph', 'rag'). Returns what interviewers should focus on."""
    file = KB_DIR / f"{topic.strip().lower().replace(' ', '_')}.md"
    if file.exists():
        return file.read_text()
    return f"No guideline for '{topic}'. Available topics: {', '.join(available_topics())}"