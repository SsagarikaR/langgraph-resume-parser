"""Rebuild the Qdrant index after editing data/knowledge_base/*.md:

    python -m app.knowledge.ingest

In local mode, stop the API server first: only one process can open ./qdrant_data at a time.
"""

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.knowledge.vector_store import COLLECTION, ingest


def main() -> None:
    setup_logging()
    count = ingest()
    print(
        f"Indexed {count} guideline chunks from {get_settings().knowledge_base_dir} "
        f"into Qdrant collection '{COLLECTION}'"
    )


if __name__ == "__main__":
    main()
