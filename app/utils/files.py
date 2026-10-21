import re


def safe_filename(name: str) -> str:
    """The candidate name comes from the LLM, so strip anything that could form a path."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:60] or "candidate"
