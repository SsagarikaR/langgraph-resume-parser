from fastapi import Request
from fastapi.responses import JSONResponse

from app.services.exceptions import (
    InvalidScreeningInputError,
    NotAwaitingReviewError,
    NotPausedError,
    ScreeningError,
    ScreeningNotFoundError,
    WorkflowFailedError,
)

# Service errors → HTTP status codes, in one place
STATUS_CODES = {
    InvalidScreeningInputError: 400,
    ScreeningNotFoundError: 404,
    NotAwaitingReviewError: 409,
    NotPausedError: 409,
    WorkflowFailedError: 500,
}


def status_code_for(exc: ScreeningError) -> int:
    return STATUS_CODES.get(type(exc), 500)


def handle_screening_error(request: Request, exc: ScreeningError) -> JSONResponse:
    return JSONResponse(status_code=status_code_for(exc), content={"detail": str(exc)})
