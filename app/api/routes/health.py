"""Liveness and readiness: no API key needed, so neither leaks why a check failed, only that one did."""

import asyncio
import logging
from collections.abc import Callable

from fastapi import APIRouter, Response

from app.api.deps import get_service
from app.knowledge import vector_store

logger = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 3.0

router = APIRouter(tags=["health"])


async def passes(check: Callable[[], object]) -> bool:
    """Whether `check` (blocking I/O, so run off the event loop) succeeds within the time limit.
    Failures are logged, not returned: /ready needs no key, so it says what is down, never why."""
    try:
        await asyncio.wait_for(asyncio.to_thread(check), READY_TIMEOUT_SECONDS)
    except Exception:
        logger.warning("Readiness check %s failed", getattr(check, "__qualname__", check), exc_info=True)
        return False
    return True


def label(ok: bool) -> str:
    return "ok" if ok else "unavailable"


# async: answered on the event loop, so it still responds while every worker thread is busy.
# Liveness only: the process is up. /ready says whether it can do its work.
@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response):
    """Readiness: the checkpoint database answers (required: every screening lives there) and
    the guideline store answers (reported, not required: screenings run without guidelines)."""
    database = await passes(lambda: get_service().repository.ping())
    guidelines = await passes(vector_store.ping)
    if not database:
        response.status_code = 503
    return {
        "status": "ok" if database else "unavailable",
        "checks": {"database": label(database), "guidelines": label(guidelines)},
    }
