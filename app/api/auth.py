"""Optional API-key authentication for the /screenings routes.

When API_KEYS (or API_KEY) is set, every request must send one of the keys in the X-API-Key
header, and the key's name identifies the caller. When none is set the API is open, which is
meant for local development only. /health never needs a key.

Keys may be stored hashed (API_KEYS=name:sha256:<hex>), may expire (API_KEY_EXPIRES), and an audit
key (AUDIT_KEYS) is read-only but sees every screening.

A client acting for several people (the UI) names the person in the X-Acting-User header. The
caller is then "key/user": each person owns the screenings they start, and the key vouches for
the name. Only the keys in ACTING_USER_KEYS may do that (403 for any other), so a key given to
one person or script cannot pose as someone else. Without the header the caller is the key's name.

The check runs twice on purpose: in ScreeningsGuardMiddleware, before the request body is read
(FastAPI parses uploads before it runs dependencies), and as a router dependency, which also
gives routes the caller's name and documents the header in OpenAPI.
"""

import hashlib
import re
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import KEY_HASH_PREFIX, get_settings

API_KEY_HEADER = "X-API-Key"
ACTING_USER_HEADER = "X-Acting-User"
# Names a browser session of a client with no per-person login, for rate limits only (see rate_limit)
CLIENT_SESSION_HEADER = "X-Client-Session"
# No "/" (it separates key and user in the caller) and nothing a log line or header could abuse
USER_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
READ_METHODS = frozenset({"GET", "HEAD"})

# auto_error=False so a missing header gets our 401 (not FastAPI's 403) and an open API still works
api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def require_api_key(
    request: Request,
    key: Annotated[str | None, Security(api_key_header)],
    acting_user: Annotated[str | None, Header(alias=ACTING_USER_HEADER)] = None,
) -> str | None:
    """The caller: the key's name, or "key/user" when the client names the person it acts for.
    None when no keys are configured (the header is then ignored: there is no access control)."""
    client = check_api_key(key)
    check_acting_user(client, acting_user)
    check_permission(client, request.method)
    caller = caller_name(client, acting_user)
    request.state.caller = caller  # for the request's log line and the per-caller rate limit
    return caller


def is_audit_key(client: str | None) -> bool:
    return client is not None and client in get_settings().audit_keys


def check_permission(client: str | None, method: str) -> None:
    """403 if `client` is a read-only audit key and the request would change anything."""
    if is_audit_key(client) and method not in READ_METHODS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This API key is read-only (AUDIT_KEYS)")


def read_scope(caller: Annotated[str | None, Depends(require_api_key)]) -> str | None:
    """Whose screenings a read may return: the caller's own, or (None) every one for an audit key."""
    return None if is_audit_key(caller) else caller


def check_acting_user(client: str | None, acting_user: str | None) -> None:
    """403 if `client` may not name the person it acts for; 400 if the name is malformed."""
    if client is None or acting_user is None:
        return  # no header, or an open API (no access control for it to scope)
    if client not in get_settings().acting_user_keys:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"This API key may not send {ACTING_USER_HEADER} (see ACTING_USER_KEYS)",
        )
    if not USER_NAME.match(acting_user):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"{ACTING_USER_HEADER} must be 1-64 letters, digits or . _ @ -",
        )


def caller_name(client: str | None, acting_user: str | None) -> str | None:
    """The caller's name: "key/user" for a valid acting user, else the key's (None if the API is open)."""
    if client is not None and acting_user is not None and USER_NAME.match(acting_user):
        return f"{client}/{acting_user}"
    return client


def key_matches(key: str, expected: str) -> bool:
    """Whether `key` is the configured key `expected`, stored in plain text or as sha256:<hex>.
    compare_digest takes the same time whatever the input, so a key cannot be found by timing."""
    if expected.startswith(KEY_HASH_PREFIX):
        digest = hashlib.sha256(key.encode()).hexdigest()
        return secrets.compare_digest(digest, expected.removeprefix(KEY_HASH_PREFIX).lower())
    return secrets.compare_digest(key.encode(), expected.encode())


def check_api_key(key: str | None) -> str | None:
    """The name of the key that matches `key`, or None when no keys are configured.

    Raises 401 when keys are configured and `key` matches none of them, or has expired.
    """
    settings = get_settings()
    credentials = settings.credentials
    if not credentials:
        return None
    caller = None
    for name, expected in credentials.items():
        # Every key is compared (no early exit), so which key matched cannot be found by timing
        if key is not None and key_matches(key, expected):
            caller = name
    if caller is None:
        raise unauthorized(f"Missing or invalid {API_KEY_HEADER} header")
    expires = settings.api_key_expires.get(caller)
    if expires and datetime.now(timezone.utc).date() > expires:
        raise unauthorized(f"This API key expired on {expires.isoformat()}; ask for a new one")
    return caller


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "ApiKey"})


# The authenticated caller (None when the API is open), for routes to pass on
Caller = Annotated[str | None, Depends(require_api_key)]
# For read routes: the caller, or None (every screening) for an audit key
ReadCaller = Annotated[str | None, Depends(read_scope)]
