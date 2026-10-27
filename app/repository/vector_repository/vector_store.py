"""Interview guidelines stored in Qdrant and searched by meaning (vector search).

Each bullet in data/knowledge_base/*.md becomes one small chunk, embedded with Gemini
and stored in a Qdrant collection. A search embeds the query the same way and returns
the closest chunks, so "Kafka" also finds the system-design guideline about event streams.

Qdrant runs in local mode (data in ./qdrant_data) unless QDRANT_URL points at a server.
"""

import atexit
import contextlib
import fcntl
import logging
import threading
import uuid
from functools import lru_cache
from pathlib import Path

from langchain_google_genai import GoogleGenerativeAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointIdsList, PointStruct, VectorParams

from app.config.settings import BASE_DIR, get_settings

logger = logging.getLogger(__name__)

COLLECTION = "interview_guidelines"
# Records which embedding model produced the collection's vectors, so a same-dimension model swap
# (not caught by the vector-size check alone) still triggers a rebuild instead of leaving stale,
# semantically mismatched vectors in place indefinitely.
_MODEL_MARKER_PATH = BASE_DIR / ".qdrant_embedding_model"
# Coordinates `ingest()` (can run as a separate `python -m app.repository.vector_repository.ingest` process) with
# `search()`/`ensure_index()` in the API process, so a search never reads mid-rebuild.
_LOCK_PATH = BASE_DIR / ".qdrant_ingest.lock"


@contextlib.contextmanager
def _file_lock(exclusive: bool):
    _LOCK_PATH.touch(exist_ok=True)
    with open(_LOCK_PATH) as fh:
        fcntl.flock(fh, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


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


def _marked_embedding_model() -> str | None:
    return _MODEL_MARKER_PATH.read_text().strip() if _MODEL_MARKER_PATH.exists() else None


def ingest(kb_dir: Path | None = None) -> int:
    """(Re)build the collection from the markdown files. Returns the number of chunks stored."""
    settings = get_settings()
    kb_dir = kb_dir or settings.knowledge_base_dir
    chunks = [c for path in sorted(kb_dir.glob("*.md")) for c in chunk_markdown(path)]
    if not chunks:
        raise ValueError(f"No guideline files found in {kb_dir}")

    vectors = get_embeddings().embed_documents([c["content"] for c in chunks])
    points = [
        PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{c['source']}#{i}")), vector=vector, payload=c)
        for i, (c, vector) in enumerate(zip(chunks, vectors, strict=True))
    ]
    size = len(vectors[0])
    with _file_lock(exclusive=True):
        client = get_client()
        # Rebuild on a dimension change (incomparable vectors) or a same-dimension model swap
        # (comparable size, different semantic space): either way the old vectors must go.
        stale_model = _marked_embedding_model() not in (None, settings.embedding_model)
        if client.collection_exists(COLLECTION) and (_vector_size(client) != size or stale_model):
            client.delete_collection(COLLECTION)
        if not client.collection_exists(COLLECTION):
            client.create_collection(COLLECTION, vectors_config=VectorParams(size=size, distance=Distance.COSINE))

        # Update in place, then drop chunks that no longer exist, so searches keep working during a re-index
        client.upsert(COLLECTION, points=points)
        keep = {str(p.id) for p in points}
        stale = [point_id for point_id in _all_ids(client) if str(point_id) not in keep]
        if stale:
            client.delete(COLLECTION, points_selector=PointIdsList(points=stale))
        _MODEL_MARKER_PATH.write_text(settings.embedding_model)
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


@lru_cache(maxsize=256)
def _embed_query_cached(query: str) -> tuple[float, ...]:
    """Query embeddings, cached: screenings repeat the same skill names (e.g. "Kafka") often
    enough that re-calling the embedding API for each one is wasted latency and cost."""
    return tuple(get_embeddings().embed_query(query))


def _tokenize(text: str) -> set[str]:
    return {tok for tok in text.lower().split() if len(tok) > 2}


def _lexical_overlap(query_tokens: set[str], text: str) -> float:
    """Fraction of query tokens that literally appear in the chunk's text.

    Vector similarity alone can rank a chunk that is merely topic-adjacent above one that
    actually names the queried skill/term (e.g. "Kafka" vs. a generic "event streams" chunk),
    because embeddings blur exact-term matches into broader semantic neighborhoods. Blending in
    a literal overlap signal rewards chunks that actually mention what was searched for.
    """
    if not query_tokens:
        return 0.0
    return len(query_tokens & _tokenize(text)) / len(query_tokens)


def _rerank(query: str, hits: list[dict]) -> list[dict]:
    """Re-score vector hits with a vector/lexical blend and re-sort by the blended score.

    `score` is kept as the raw cosine similarity (used for the absolute cut-off upstream);
    the blended value only decides final ordering and the relative-margin cut-off, so a
    lexically-matching chunk that's a near-tie on vector score can win the top slot.
    """
    query_tokens = _tokenize(query)
    reranked = [
        {**h, "rerank_score": 0.7 * h["score"] + 0.3 * _lexical_overlap(query_tokens, h.get("text", ""))} for h in hits
    ]
    reranked.sort(key=lambda h: h["rerank_score"], reverse=True)
    return reranked


def search(query: str) -> list[dict]:
    """Closest guideline chunks to `query`, using the configured count and score cut-off.

    Besides the absolute `guideline_min_score` cut-off, a hit is also dropped if it trails the
    best match for this query by more than `guideline_relative_margin`: once one chunk is clearly
    the best answer, a merely-passable tail hit only dilutes the summary the LLM is asked to write.
    Hits are reranked with a vector/lexical blend (see `_rerank`) before that margin is applied,
    so the margin is measured against the best *reranked* match, not just the best raw embedding.
    """
    settings = get_settings()
    ensure_index()
    vector = list(_embed_query_cached(query))
    with _file_lock(exclusive=False):
        result = get_client().query_points(
            COLLECTION, query=vector, limit=settings.guideline_top_k, score_threshold=settings.guideline_min_score
        )
    hits = [{**(hit.payload or {}), "score": round(hit.score, 3)} for hit in result.points]
    if not hits:
        return hits
    hits = _rerank(query, hits)
    best = hits[0]["rerank_score"]
    return [h for h in hits if h["rerank_score"] >= best - settings.guideline_relative_margin]
