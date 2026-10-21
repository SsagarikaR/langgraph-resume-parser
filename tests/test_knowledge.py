from app.knowledge.vector_store import chunk_markdown
from app.tools import knowledge_tools


def test_chunk_markdown_splits_bullets_and_red_flags(tmp_path):
    md = tmp_path / "kafka.md"
    md.write_text(
        "# Kafka Interview Guidelines\n\nInterviewers should focus on:\n"
        "- Partitioning\n- Consumer groups\n\nRed flags: Cannot explain offsets\n"
    )
    chunks = chunk_markdown(md)
    assert [(c["kind"], c["text"]) for c in chunks] == [
        ("Focus area", "Partitioning"),
        ("Focus area", "Consumer groups"),
        ("Red flag", "Cannot explain offsets"),
    ]
    assert chunks[0]["topic"] == "Kafka"


def test_lookup_degrades_gracefully_when_search_fails(monkeypatch):
    def boom(_):
        raise ConnectionError("qdrant down")

    monkeypatch.setattr(knowledge_tools, "search", boom)
    out = knowledge_tools.lookup_interview_guideline.invoke({"topic": "Kafka"})
    assert out.startswith(knowledge_tools.SEARCH_FAILED)


def test_lookup_reports_no_results(monkeypatch):
    monkeypatch.setattr(knowledge_tools, "search", lambda _: [])
    out = knowledge_tools.lookup_interview_guideline.invoke({"topic": "Cobol"})
    assert out.startswith(knowledge_tools.NO_RESULTS)
