"""Interview guidelines stored in Qdrant and searched by meaning (vector search).

Each bullet in data/knowledge_base/*.md becomes one small chunk, embedded with Gemini
and stored in a Qdrant collection. A search embeds the query the same way and returns
the closest chunks, so "Kafka" also finds the system-design guideline about event streams.

Qdrant runs in local mode (data in ./qdrant_data) unless QDRANT_URL points at a server.
"""
import atexit
import os
import uuid
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

load_dotenv()

KB_DIR = Path("data/knowledge_base")
COLLECTION = "interview_guidelines"
TOP_K = 4
MIN_SCORE = 0.6         

@lru_cache
def get_client() -> QdrantClient:
    if url := os.getenv("QDRANT_URL"):
        client = QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY"))
    else:
        client = QdrantClient(path=os.getenv("QDRANT_PATH", "qdrant_data"))
    atexit.register(client.close)     
    return client


@lru_cache
def get_embeddings() -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(
        model=os.getenv("EMBEDDING_MODEL", "gemini-embedding-001"),
        google_api_key=os.getenv("GEMINI_API_KEY"),
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
        if line.endswith(":"):                        # e.g. "Interviewers should focus on:"
            section = "Focus area"
            continue
        if line.lower().startswith("red flags:"):
            kind, text = "Red flag", line.split(":", 1)[1].strip()
        else:
            kind, text = section, line.removeprefix("- ").strip()
        chunks.append({
            "topic": title,
            "kind": kind,
            "text": text,
            "source": path.name,
            # Embed the topic and kind with the text so the vector carries that context
            "content": f"{title} interview guideline. {kind}: {text}",
        })
    return chunks


def ingest(kb_dir: Path = KB_DIR) -> int:
    """(Re)build the collection from the markdown files. Returns the number of chunks stored."""
    chunks = [c for path in sorted(kb_dir.glob("*.md")) for c in chunk_markdown(path)]
    if not chunks:
        raise ValueError(f"No guideline files found in {kb_dir}")

    vectors = get_embeddings().embed_documents([c["content"] for c in chunks])
    client = get_client()
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    client.create_collection(
        COLLECTION, vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE)
    )
    client.upsert(COLLECTION, points=[
        PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{c['source']}#{i}")),
            vector=vector,
            payload=c,
        )
        for i, (c, vector) in enumerate(zip(chunks, vectors))
    ])
    return len(chunks)


def ensure_index() -> None:
    """Build the collection on first use so the app works without a manual ingest step."""
    client = get_client()
    if not client.collection_exists(COLLECTION) or client.count(COLLECTION).count == 0:
        ingest()


def search(query: str, top_k: int = TOP_K, min_score: float = MIN_SCORE) -> list[dict]:
    ensure_index()
    vector = get_embeddings().embed_query(query)
    hits = get_client().query_points(
        COLLECTION, query=vector, limit=top_k, score_threshold=min_score
    ).points
    return [{**hit.payload, "score": round(hit.score, 3)} for hit in hits]
