"""Per-client rate limiting, used as FastAPI dependencies.

Counts live in process memory (a sliding one-minute window per client IP), so each worker
process limits on its own; use a shared store such as Redis, or a gateway, when running
several workers. Behind a reverse proxy, start uvicorn with --proxy-headers so
request.client is the real client rather than the proxy.
"""

import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request, status

from app.core.config import get_settings

WINDOW_SECONDS = 60.0


class RateLimiter:
    """Allows `limit()` requests per client per minute; raises 429 beyond that."""

    def __init__(self, name: str, limit: Callable[[], int]):
        self.name = name
        self._limit = limit  # read on every call so settings changes apply without a restart
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()  # sync routes and dependencies run in a thread pool
        self._last_sweep = 0.0

    def __call__(self, request: Request) -> None:
        if not get_settings().rate_limit_enabled:
            return
        client = request.client.host if request.client else "unknown"
        limit = self._limit()
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            hits = self._hits[client]
            while hits and hits[0] <= now - WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = max(1, math.ceil(hits[0] + WINDOW_SECONDS - now))
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded: {limit} {self.name} requests per minute. Retry in {retry_after}s.",
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _sweep(self, now: float) -> None:
        """Forget clients idle for a whole window, so memory doesn't grow with every IP seen."""
        if now - self._last_sweep < WINDOW_SECONDS:
            return
        self._last_sweep = now
        idle = [client for client, hits in self._hits.items() if not hits or hits[-1] <= now - WINDOW_SECONDS]
        for client in idle:
            del self._hits[client]


api_rate_limit = RateLimiter("API", lambda: get_settings().rate_limit_per_minute)
# Stricter limit for requests that run LLM calls (slow and billed per token)
workflow_rate_limit = RateLimiter("workflow", lambda: get_settings().workflow_rate_limit_per_minute)
