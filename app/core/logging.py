import logging

from app.core.config import get_settings

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def setup_logging() -> None:
    logging.basicConfig(level=get_settings().log_level.upper(), format=LOG_FORMAT)
    # Third-party clients are chatty at INFO (one line per HTTP request)
    for noisy in ("httpx", "httpcore", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
