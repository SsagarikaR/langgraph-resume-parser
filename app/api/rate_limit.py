"""Per-client rate limiting, applied by ScreeningsGuardMiddleware before a request body is read.

Each limit has two budgets, and a request must fit both:

- per person: the authenticated caller ("key" or "key/user"). A key with no acting user may also
  name a browser session in X-Client-Session (the UI's shared login does, since it cannot vouch
  for who is behind it), so each session gets its own budget. Without a valid key (an open API,
  or a wrong key), the client IP: repeated guesses at a key are throttled by address.
- per key: everything sent with one key together. Acting users and sessions are names the
  client picks, so without this ceiling a key holder could rotate them for unlimited budgets.

Per person rather than per address, because one client often stands for many people: every
reviewer using the UI reaches the API from the UI's address.

Requests that run the workflow (every POST) also count against a daily quota per key: the
ceiling on what one key, even a leaked one, can spend on the LLM in a day.

Counts live in process memory (a sliding window per budget), so each worker process limits on its
own and a restart forgets them; use a shared store such as Redis, or a gateway, when running several workers.
Behind a reverse proxy, start uvicorn with --proxy-headers so request.client is the real client.
"""

import math
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.api.auth import ACTING_USER_HEADER, CLIENT_SESSION_HEADER, caller_name
from app.core.config import get_settings

MINUTE = 60.0
DAY = 24 * 60 * 60.0
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass(frozen=True)
class Budgets:
    """Which budgets a request counts against."""

    person: str  # "caller:ui/alice", "caller:ui#<session>", or "ip:<address>"
    key: str | None = None  # "key:ui"; None without a valid key


def address_of(request: Request) -> str:
    return f"ip:{request.client.host if request.client else 'unknown'}"


def budgets_for(request: Request, client: str | None) -> Budgets:
    """The budgets of a request whose API key matched `client` (None: open API or no valid key)."""
    if client is None:
        return Budgets(address_of(request))
    caller = caller_name(client, request.headers.get(ACTING_USER_HEADER))
    person = f"caller:{caller}"
    session = request.headers.get(CLIENT_SESSION_HEADER, "")
    if caller == client and SESSION_ID.match(session):  # an acting user is already one person
        person += f"#{session}"
    return Budgets(person, f"key:{client}")


class RateLimiter:
    """Allows `limit()` requests per client per `window` seconds; a limit of 0 allows any number."""

    def __init__(self, description: str, limit: Callable[[], int], window: float = MINUTE):
        self.description = description  # e.g. "workflow requests per minute"
        self.window = window
        # Called per request: settings are cached, so this only matters when that cache is cleared
        # (tests change limits between requests); a running server reads them once
        self._limit = limit
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()  # sync routes and dependencies run in a thread pool
        self._last_sweep = 0.0

    def check(self, client: str) -> None:
        """429 if `client` has used up its budget; counts nothing."""
        limit = self._limit()
        if not limit:
            return
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            hits = self._hits[client]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = max(1, math.ceil(hits[0] + self.window - now))
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded: {limit} {self.description}. Retry in {retry_after}s.",
                    headers={"Retry-After": str(retry_after)},
                )

    def record(self, client: str) -> None:
        with self._lock:
            self._hits[client].append(time.monotonic())

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _sweep(self, now: float) -> None:
        """Forget clients idle for a whole window, so memory doesn't grow with every client seen."""
        if now - self._last_sweep < min(self.window, MINUTE):
            return
        self._last_sweep = now
        idle = [client for client, hits in self._hits.items() if not hits or hits[-1] <= now - self.window]
        for client in idle:
            del self._hits[client]


class RateLimit:
    """A per-person and a per-key budget for one kind of request; a request must fit both."""

    def __init__(
        self,
        name: str,
        per_person: Callable[[], int],
        per_key: Callable[[], int],
        key_per_day: Callable[[], int] | None = None,
    ):
        self.person = RateLimiter(f"{name} requests per minute", per_person)
        self.key = RateLimiter(f"{name} requests per minute for this API key, all its users together", per_key)
        self.key_daily = (
            RateLimiter(f"{name} requests per day for this API key", key_per_day, window=DAY) if key_per_day else None
        )

    def hit(self, budgets: Budgets) -> None:
        """Count one request against `budgets`; 429 (and nothing counted) if any is used up."""
        if not get_settings().rate_limit_enabled:
            return
        checks = [(self.person, budgets.person)]
        if budgets.key:
            checks.append((self.key, budgets.key))
            if self.key_daily:
                checks.append((self.key_daily, budgets.key))
        for limiter, client in checks:
            limiter.check(client)
        for limiter, client in checks:
            limiter.record(client)

    def reset(self) -> None:
        for limiter in (self.person, self.key, self.key_daily):
            if limiter:
                limiter.reset()


def _settings_value(name: str) -> Callable[[], int]:
    return lambda: getattr(get_settings(), name)


api_rate_limit = RateLimit(
    "API", _settings_value("rate_limit_per_minute"), _settings_value("key_rate_limit_per_minute")
)
# Stricter limit for requests that run LLM calls (slow and billed per token)
workflow_rate_limit = RateLimit(
    "workflow",
    _settings_value("workflow_rate_limit_per_minute"),
    _settings_value("key_workflow_rate_limit_per_minute"),
    _settings_value("key_daily_workflow_limit"),
)
