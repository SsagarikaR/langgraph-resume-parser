import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes.screenings import router as screenings_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.exceptions import (
    InvalidScreeningInput, NotAwaitingReview, NotPaused, ScreeningError, ScreeningNotFound, WorkflowFailed,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()      # fail fast at startup if required settings (API key) are missing
    logger.info("Starting API with model %s", settings.gemini_model)
    yield


app = FastAPI(title="Resume Screening API", version="0.1.0", lifespan=lifespan)
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
