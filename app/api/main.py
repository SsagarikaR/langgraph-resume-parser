from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes.screenings import router as screenings_router
from app.services.exceptions import (
    InvalidScreeningInput, NotAwaitingReview, NotPaused, ScreeningError, ScreeningNotFound, WorkflowFailed,
)

app = FastAPI(title="Resume Screening API")
app.include_router(screenings_router)

# Service errors → HTTP status codes, in one place
STATUS_CODES = {
    InvalidScreeningInput: 400,
    ScreeningNotFound: 404,
    NotAwaitingReview: 409,
    NotPaused: 409,
    WorkflowFailed: 500,
}


@app.exception_handler(ScreeningError)
def handle_screening_error(request: Request, exc: ScreeningError):
    return JSONResponse(status_code=STATUS_CODES.get(type(exc), 500), content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok"}
