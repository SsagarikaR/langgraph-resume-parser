"""Interview guidelines stored in Qdrant and searched by meaning (vector search).

Each bullet in data/knowledge_base/*.md becomes one small chunk, embedded with Gemini
and stored in a Qdrant collection. A search embeds the query the same way and returns
the closest chunks, so "Kafka" also finds the system-design guideline about event streams.

Qdrant runs in local mode (data in ./qdrant_data) unless QDRANT_URL points at a server.
"""

import atexit
import logging
import threading
import uuid
from functools import lru_cache
from pathlib import Path

from langchain_google_genai import GoogleGenerativeAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointIdsList, PointStruct, VectorParams

from app.core.config import get_settings

logger = logging.getLogger(__name__)

COLLECTION = "interview_guidelines"


@lru_cache
def get_client() -> QdrantClient:
    settings = get_settings()
    if settings.qdrant_url:
        api_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
        client = QdrantClient(url=settings.qdrant_url, api_key=api_key)
    else:
        client = QdrantClient(path=str(settings.qdrant_path))
    atexit.register(client.close)
    return client


@lru_cache
def get_embeddings() -> GoogleGenerativeAIEmbeddings:
    settings = get_settings()
    return GoogleGenerativeAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.gemini_api_key.get_secret_value(),
    )


def chunk_markdown(path: Path) -> list[dict]:
    """Split one guideline file into one chunk per bullet / red-flag line."""
    lines = path.read_text().splitlines()
    title = lines[0].lstrip("# ").replace(" Interview Guidelines", "").strip() if lines else path.stem
    chunks, section = [], "Notes"
    for raw in lines[1:]:
        line = raw.strip()
        if not line:
            continue
        if line.endswith(":"):  # e.g. "Interviewers should focus on:"
            section = "Focus area"
            continue
        if line.lower().startswith("red flags:"):
            kind, text = "Red flag", line.split(":", 1)[1].strip()
        else:
            kind, text = section, line.removeprefix("- ").strip()
        chunks.append(
            {
                "topic": title,
                "kind": kind,
                "text": text,
                "source": path.name,
                # Embed the topic and kind with the text so the vector carries that context
                "content": f"{title} interview guideline. {kind}: {text}",
            }
        )
    return chunks


def ingest(kb_dir: Path | None = None) -> int:
    """(Re)build the collection from the markdown files. Returns the number of chunks stored."""
    kb_dir = kb_dir or get_settings().knowledge_base_dir
    chunks = [c for path in sorted(kb_dir.glob("*.md")) for c in chunk_markdown(path)]
    if not chunks:
        raise ValueError(f"No guideline files found in {kb_dir}")

    vectors = get_embeddings().embed_documents([c["content"] for c in chunks])
    points = [
        PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{c['source']}#{i}")), vector=vector, payload=c)
        for i, (c, vector) in enumerate(zip(chunks, vectors, strict=True))
    ]
    client = get_client()
    size = len(vectors[0])
    if client.collection_exists(COLLECTION) and _vector_size(client) != size:
        client.delete_collection(COLLECTION)  # embedding model changed: old vectors are not comparable
    if not client.collection_exists(COLLECTION):
        client.create_collection(COLLECTION, vectors_config=VectorParams(size=size, distance=Distance.COSINE))

    # Update in place, then drop chunks that no longer exist, so searches keep working during a re-index
    client.upsert(COLLECTION, points=points)
    keep = {str(p.id) for p in points}
    stale = [point_id for point_id in _all_ids(client) if str(point_id) not in keep]
    if stale:
        client.delete(COLLECTION, points_selector=PointIdsList(points=stale))
    logger.info("Indexed %d guideline chunks from %s (%d stale removed)", len(chunks), kb_dir, len(stale))
    return len(chunks)


def _vector_size(client: QdrantClient) -> int | None:
    vectors = client.get_collection(COLLECTION).config.params.vectors
    return vectors.size if isinstance(vectors, VectorParams) else None


def _all_ids(client: QdrantClient) -> list:
    ids: list = []
    offset = None
    while True:
        records, offset = client.scroll(COLLECTION, limit=256, offset=offset, with_payload=False, with_vectors=False)
        ids.extend(r.id for r in records)
        if offset is None:
            return ids


_index_lock = threading.Lock()


@lru_cache
def ensure_index() -> None:
    """Build the collection on first use so the app works without a manual ingest step.

    Cached, so the existence check runs once per process instead of on every search. The lock
    stops concurrent first searches (sync routes run in a thread pool) from both rebuilding it.
    """
    with _index_lock:
        client = get_client()
        if not client.collection_exists(COLLECTION) or client.count(COLLECTION).count == 0:
            ingest()


def ping() -> None:
    """Raise if Qdrant cannot be reached (the collection itself may not exist yet)."""
    get_client().collection_exists(COLLECTION)


def search(query: str) -> list[dict]:
    """Closest guideline chunks to `query`, using the configured count and score cut-off."""
    settings = get_settings()
    ensure_index()
    vector = get_embeddings().embed_query(query)
    result = get_client().query_points(
        COLLECTION, query=vector, limit=settings.guideline_top_k, score_threshold=settings.guideline_min_score
    )
    return [{**(hit.payload or {}), "score": round(hit.score, 3)} for hit in result.points]
