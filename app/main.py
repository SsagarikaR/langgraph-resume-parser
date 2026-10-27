import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.config.logging import setup_logging
from app.config.settings import get_settings
from app.core.metrics import metrics
from app.exceptions.domain import ScreeningError
from app.exceptions.handlers import handle_screening_error
from app.middleware.auth import ACTING_USER_HEADER, API_KEY_HEADER, CLIENT_SESSION_HEADER, require_api_key
from app.middleware.middleware import (
    REQUEST_ID_HEADER,
    BodySizeLimitMiddleware,
    ScreeningsGuardMiddleware,
    add_security_headers,
    track_request,
)
from app.routes.health_routes import router as health_router
from app.routes.screening_routes import router as screenings_router
from app.utils.deps import get_service

logger = logging.getLogger(__name__)


async def purge_periodically(retention_days: int, interval_minutes: int) -> None:
    """Erase expired screenings now and then every interval, until the app shuts down."""
    while True:
        try:
            # Blocking SQLite and file work, so off the event loop
            await asyncio.to_thread(get_service().purge_expired, retention_days)
        except Exception:
            logger.exception("Retention purge failed; retrying at the next interval")
        await asyncio.sleep(interval_minutes * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    logger.info(
        "Starting API with model %s (CORS origins: %s, rate limit: %s)",
        settings.gemini_model,
        settings.cors_origins or "none",
        f"{settings.rate_limit_per_minute}/min, workflow {settings.workflow_rate_limit_per_minute}/min per person; "
        f"{settings.key_rate_limit_per_minute}/min, workflow {settings.key_workflow_rate_limit_per_minute}/min per key"
        if settings.rate_limit_enabled
        else "off",
    )
    if settings.credentials:
        logger.info("API keys: %s", ", ".join(sorted(settings.credentials)))  # names only, never keys
    else:
        logger.warning(
            "ALLOW_OPEN_API=true and no API key: %s/screenings is open to anyone who can reach this server",
            settings.api_prefix,
        )

    purge = None
    if settings.retention_days:
        logger.info("Screenings idle for %d days are erased automatically", settings.retention_days)
        purge = asyncio.create_task(purge_periodically(settings.retention_days, settings.purge_interval_minutes))
    else:
        logger.warning("RETENTION_DAYS=0: screenings (resume data) are kept forever")
    yield
    if purge:
        purge.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await purge  # let it finish unwinding before the app (and its DB) closes


def create_app() -> FastAPI:
    # Reading settings here also fails fast at startup if required ones (API key) are missing
    settings = get_settings()
    if not settings.credentials and not settings.allow_open_api:
        # Fail closed: a server started without keys would let anyone read every resume
        raise RuntimeError("No API_KEYS or API_KEY is set. Set one, or ALLOW_OPEN_API=true for local development only.")
    # The schema lists every route and field: served only for local development unless API_DOCS=true
    docs = settings.docs_enabled
    app = FastAPI(
        title="Resume Screening API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    api_prefix = settings.api_prefix
    app.include_router(health_router)
    app.include_router(screenings_router, prefix=api_prefix)
    app.add_exception_handler(ScreeningError, handle_screening_error)  # type: ignore[arg-type]  # Starlette types handlers as taking Exception

    if settings.metrics_enabled:
        # Exposes request-rate/latency and LLM token usage: same key requirement as /screenings,
        # so it is not open reconnaissance surface whenever an API key is configured.
        metrics_deps = [Depends(require_api_key)] if settings.credentials else []

        @app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False, dependencies=metrics_deps)
        async def prometheus_metrics():
            return metrics.render()

    # Middleware added last runs first: CORS is outermost so 401/413/429 errors also carry
    # CORS headers and the browser can show them, and every response gets the security headers
    # and a request id. The guard (rate limit + API key) and the body limit run before any body is read.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=lambda: get_settings().max_request_bytes)
    app.add_middleware(ScreeningsGuardMiddleware, prefix=api_prefix + screenings_router.prefix)
    app.middleware("http")(add_security_headers)
    app.middleware("http")(track_request)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=[
                "Content-Type",
                API_KEY_HEADER,
                ACTING_USER_HEADER,
                CLIENT_SESSION_HEADER,
                REQUEST_ID_HEADER,
            ],
            expose_headers=["Retry-After", REQUEST_ID_HEADER],
            allow_credentials=False,  # the API key is a plain header; no cookies
        )
    return app


app = create_app()
