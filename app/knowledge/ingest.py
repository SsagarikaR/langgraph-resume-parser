"""Rebuild the Qdrant index after editing data/knowledge_base/*.md:

    python -m app.knowledge.ingest

A file lock (see vector_store._file_lock) keeps this from racing a concurrent search in the API
process, but in local mode only one process can open ./qdrant_data at all, so stop the API server
first regardless.

Trust note: generate_questions puts this index's search results straight into the question-
generation prompt as "company_interview_guidelines", without the extra <document>/UNTRUSTED_FIELDS
wrapping given to resume and job-description text. That is safe only because these .md files are
first-party, human-reviewed content versioned with the app. If this knowledge base is ever made
editable by end users or synced from an external source (e.g. a wiki), treat its content as
untrusted input in the prompt, the same as resume/JD text.
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
