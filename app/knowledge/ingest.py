"""Rebuild the Qdrant index after editing data/knowledge_base/*.md:

    python -m app.knowledge.ingest

In local mode, stop the API server first: only one process can open ./qdrant_data at a time.
"""
from app.knowledge.vector_store import COLLECTION, KB_DIR, ingest

if __name__ == "__main__":
    count = ingest()
    print(f"Indexed {count} guideline chunks from {KB_DIR} into Qdrant collection '{COLLECTION}'")
