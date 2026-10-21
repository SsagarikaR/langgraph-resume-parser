from qdrant_client import QdrantClient

from app.knowledge import vector_store
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


class FakeEmbeddings:
    def embed_documents(self, texts):
        return [[1.0, float(i), 0.5] for i, _ in enumerate(texts)]


def test_reindex_updates_in_place_and_drops_stale_chunks(tmp_path, monkeypatch):
    client = QdrantClient(":memory:")
    monkeypatch.setattr(vector_store, "get_client", lambda: client)
    monkeypatch.setattr(vector_store, "get_embeddings", FakeEmbeddings)
    md = tmp_path / "kafka.md"
    md.write_text("# Kafka Interview Guidelines\n- Partitioning\n- Consumer groups\n")
    assert vector_store.ingest(tmp_path) == 2

    dropped = []
    monkeypatch.setattr(client, "delete_collection", lambda *args, **kwargs: dropped.append(args))
    md.write_text("# Kafka Interview Guidelines\n- Partitioning\n")
    assert vector_store.ingest(tmp_path) == 1
    assert client.count(vector_store.COLLECTION).count == 1  # the removed bullet is gone
    assert dropped == []  # never dropped, so searches kept working during the re-index


class FakeQueryEmbeddings(FakeEmbeddings):
    def embed_query(self, text):
        return [1.0, 0.0, 0.5]


def test_search_builds_the_index_on_first_use_and_applies_the_cut_off(tmp_path, monkeypatch, test_settings):
    client = QdrantClient(":memory:")
    (tmp_path / "kafka.md").write_text("# Kafka Interview Guidelines\n- Partitioning\n- Consumer groups\n")
    monkeypatch.setenv("KNOWLEDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("GUIDELINE_TOP_K", "1")
    monkeypatch.setenv("GUIDELINE_MIN_SCORE", "0")
    vector_store.get_settings.cache_clear()
    monkeypatch.setattr(vector_store, "get_client", lambda: client)
    monkeypatch.setattr(vector_store, "get_embeddings", FakeQueryEmbeddings)
    vector_store.ensure_index.cache_clear()
    try:
        hits = vector_store.search("Kafka")
    finally:
        vector_store.ensure_index.cache_clear()
    assert client.count(vector_store.COLLECTION).count == 2  # indexed without a manual ingest
    assert [h["text"] for h in hits] == ["Partitioning"]  # top_k=1, closest vector first
    assert 0 <= hits[0]["score"] <= 1


def test_ingest_command_reports_the_count(monkeypatch, capsys):
    from app.knowledge import ingest

    monkeypatch.setattr(ingest, "ingest", lambda: 7)
    ingest.main()
    assert "Indexed 7 guideline chunks" in capsys.readouterr().out
