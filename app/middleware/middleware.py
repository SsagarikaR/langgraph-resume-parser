"""HTTP and ASGI middleware for the API.

ASGI middleware (BodySizeLimitMiddleware, ScreeningsGuardMiddleware) runs before FastAPI reads the
request body, which is necessary because FastAPI parses uploads before route dependencies run.

FastAPI HTTP middleware (add_security_headers, track_request) runs as a standard middleware stack
around every route, adding security headers, request IDs, timing, and access logging.
"""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config.logging import request_id
from app.core.metrics import metrics
from app.middleware.auth import ACTING_USER_HEADER, API_KEY_HEADER, check_acting_user, check_api_key, check_permission
from app.middleware.rate_limit import api_rate_limit, budgets_for, workflow_rate_limit

logger = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Browsers apply this only over HTTPS (TLS is the reverse proxy's job), so it is safe on plain HTTP
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    # Responses carry resume data: browsers and proxies must not keep copies
    "Cache-Control": "no-store",
}

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Metric labels must come from a fixed set: any token is a valid HTTP method, so labelling by the
# raw method would let a client create a new series (kept in memory forever) per request
METHOD_LABELS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

CallNext = Callable[[Request], Awaitable[Response]]


async def add_security_headers(request: Request, call_next: CallNext) -> Response:
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


async def track_request(request: Request, call_next: CallNext) -> Response:
    """Give the request an id: on every log line it causes, in the X-Request-ID response header,
    and on one summary line, which also names the caller (the audit trail of who read or deleted
    what). A client may send its own id to correlate with its logs."""
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    rid = incoming if REQUEST_ID.match(incoming) else uuid.uuid4().hex
    token = request_id.set(rid)
    started = time.monotonic()
    status_code = 500
    try:
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        status_code = response.status_code
        return response
    finally:
        seconds = time.monotonic() - started
        method = request.method if request.method in METHOD_LABELS else "other"
        metrics.inc("http_requests_total", method=method, status=str(status_code))
        metrics.observe("http_request_duration_seconds", seconds, method=method)
        logger.info(
            "%s %s -> %d in %.0f ms (caller: %s)",
            request.method,
            request.url.path,
            status_code,
            seconds * 1000,
            getattr(request.state, "caller", None) or "-",
        )
        request_id.reset(token)


def error_response(exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


class BodySizeLimitMiddleware:
    """Reject request bodies over `max_bytes()` with 413.

    Content-Length is checked up front; bodies without it (chunked transfer) are counted as they
    arrive, and reading stops at the limit, so nothing larger is ever buffered or spooled.
    """

    def __init__(self, app: ASGIApp, max_bytes: Callable[[], int]):
        self.app = app
        # Called per request: settings are cached, so this only matters when that cache is cleared
        # (tests change limits between requests); a running server reads them once
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.max_bytes()
        too_large = HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail=f"Request body exceeds {limit} bytes")

        length = Request(scope).headers.get("content-length")
        if length and length.isdigit() and int(length) > limit:
            await error_response(too_large)(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # Raised inside the body parser: FastAPI passes HTTPException through and its
                    # exception handler turns it into the 413 response
                    raise too_large
            return message

        await self.app(scope, limited_receive, send)


class ScreeningsGuardMiddleware:
    """Apply the API key and the rate limits to /screenings before the body is read.

    A request with a valid key counts against its person's and its key's budgets (see rate_limit).
    One without (or an open API) counts against its address, before the 401, so repeated guesses
    at the API key are throttled. Every POST runs the workflow (LLM calls), so it also counts
    against the stricter workflow limits: an over-limit client gets its 429 before uploading.
    """

    def __init__(self, app: ASGIApp, prefix: str):
        self.app = app
        self.prefix = prefix

    def _protects(self, path: str) -> bool:
        return path == self.prefix or path.startswith(self.prefix + "/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self._protects(scope["path"]):
            request = Request(scope)
            try:
                try:
                    client = check_api_key(request.headers.get(API_KEY_HEADER))
                except HTTPException:
                    api_rate_limit.hit(budgets_for(request, None))  # 429 once guesses use up the budget
                    raise
                check_acting_user(client, request.headers.get(ACTING_USER_HEADER))
                check_permission(client, scope["method"])
                budgets = budgets_for(request, client)
                api_rate_limit.hit(budgets)
                if scope["method"] == "POST":
                    workflow_rate_limit.hit(budgets)
            except HTTPException as e:
                await error_response(e)(scope, receive, send)
                return
        await self.app(scope, receive, send)
