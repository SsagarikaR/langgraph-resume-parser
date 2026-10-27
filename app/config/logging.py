import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone

from app.config.settings import get_settings

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s"

# Id of the HTTP request being handled ("-" outside one), set by the API's request middleware.
# Context variables follow the request into the thread pool and LangGraph's node threads.
request_id: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for log collectors."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def setup_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else logging.Formatter(LOG_FORMAT))
    logging.basicConfig(level=settings.log_level.upper(), handlers=[handler])
    # Third-party clients are chatty at INFO (one line per HTTP request)
    for noisy in ("httpx", "httpcore", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
