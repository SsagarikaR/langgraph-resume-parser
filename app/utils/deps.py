"""Shared FastAPI dependencies and request/response helpers for the screenings routes."""

import json
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Path, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.config.settings import get_settings
from app.exceptions.domain import InvalidScreeningInputError, ScreeningError
from app.exceptions.handlers import status_code_for
from app.schemas.api_schemas import ScreeningResponse
from app.services.inputs import THREAD_ID_PATTERN
from app.services.screening_service import ScreeningService, create_service

ThreadId = Annotated[str, Path(pattern=THREAD_ID_PATTERN)]
Stream = Annotated[
    bool,
    Query(description="Return progress as NDJSON events (one JSON object per line) instead of one JSON response"),
]


@lru_cache
def get_service() -> ScreeningService:
    # One shared service; FastAPI injects it into each route via Depends
    return create_service()


ServiceDep = Annotated[ScreeningService, Depends(get_service)]


def read_upload(upload: UploadFile) -> bytes:
    """Read an upload, refusing anything over the configured size limit."""
    limit = get_settings().max_upload_bytes
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise InvalidScreeningInputError(f"'{upload.filename}' is larger than {get_settings().max_upload_mb} MB")
    return data


def ndjson(events: Iterator[dict]) -> Iterator[str]:
    """Progress events as JSON lines. Once streaming has started the status code is already
    sent, so a failure after that becomes a final {"type": "error"} event instead."""
    try:
        for event in events:
            if event["type"] == "result":
                screening = ScreeningResponse.model_validate(event["screening"]).model_dump(mode="json")
                event = {**event, "screening": screening}
            yield json.dumps(event) + "\n"
    except ScreeningError as e:
        yield json.dumps({"type": "error", "status": status_code_for(e), "detail": str(e)}) + "\n"
    finally:
        # Releases the screening at once when the client goes away mid-run
        close = getattr(events, "close", None)
        if close:
            close()


def respond(events: Iterator[dict], stream: bool):
    """Stream the events, or run them to the end and return the screening."""
    if stream:
        return StreamingResponse(ndjson(events), media_type="application/x-ndjson")
    return ScreeningService.final_view(events)
