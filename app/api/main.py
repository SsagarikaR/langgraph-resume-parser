import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.errors import handle_screening_error
from app.api.routes.screenings import router as screenings_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.exceptions import ScreeningError

logger = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

CallNext = Callable[[Request], Awaitable[Response]]


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    logger.info(
        "Starting API with model %s (CORS origins: %s, rate limit: %s)",
        settings.gemini_model,
        settings.cors_origins or "none",
        f"{settings.rate_limit_per_minute}/min, workflow {settings.workflow_rate_limit_per_minute}/min"
        if settings.rate_limit_enabled
        else "off",
    )
    yield


async def limit_request_size(request: Request, call_next: CallNext) -> Response:
    """Reject oversized bodies from Content-Length, before the multipart parser spools them to disk."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > get_settings().max_request_bytes:
        return JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={"detail": f"Request body exceeds {get_settings().max_request_bytes} bytes"},
        )
    return await call_next(request)


async def add_security_headers(request: Request, call_next: CallNext) -> Response:
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def create_app() -> FastAPI:
    # Reading settings here also fails fast at startup if required ones (API key) are missing
    settings = get_settings()
    app = FastAPI(title="Resume Screening API", version="0.1.0", lifespan=lifespan)
    app.include_router(screenings_router)
    app.add_exception_handler(ScreeningError, handle_screening_error)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # Middleware added last runs first: CORS is outermost so 413/429 errors also carry
    # CORS headers and the browser can show them.
    app.middleware("http")(add_security_headers)
    app.middleware("http")(limit_request_size)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
            expose_headers=["Retry-After"],
            allow_credentials=False,  # the API uses no cookies or auth headers
        )
    return app


app = create_app()
