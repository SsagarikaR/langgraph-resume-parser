from fastapi import Request
from fastapi.responses import JSONResponse

from app.exceptions.domain import (
    BudgetExceededError,
    InvalidScreeningInputError,
    LlmUnavailableError,
    NotAwaitingReviewError,
    NotPausedError,
    ScreeningBusyError,
    ScreeningError,
    ScreeningNotFoundError,
    ServerBusyError,
    WorkflowFailedError,
)

# Service errors → HTTP status codes, in one place
STATUS_CODES = {
    InvalidScreeningInputError: 400,
    ScreeningNotFoundError: 404,
    NotAwaitingReviewError: 409,
    NotPausedError: 409,
    ScreeningBusyError: 409,
    ServerBusyError: 503,
    BudgetExceededError: 503,
    LlmUnavailableError: 503,
    WorkflowFailedError: 500,
}


def status_code_for(exc: ScreeningError) -> int:
    return STATUS_CODES.get(type(exc), 500)


# A run slot frees up when any running screening reaches its next pause, typically within a minute
BUSY_RETRY_AFTER_SECONDS = 30


def handle_screening_error(request: Request, exc: ScreeningError) -> JSONResponse:
    headers = None
    if isinstance(exc, ServerBusyError):
        headers = {"Retry-After": str(getattr(exc, "retry_after", BUSY_RETRY_AFTER_SECONDS))}
    return JSONResponse(status_code=status_code_for(exc), content={"detail": str(exc)}, headers=headers)
