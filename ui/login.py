"""Sign-in for the UI, which holds the API key and so must not be open to everyone.

UI_USERS gives each reviewer their own login (comma-separated name:password pairs). The UI then
sends the name to the API as the person it acts for, so each reviewer sees only their own
screenings, and the name is recorded on their reviews. UI_PASSWORD is a single shared login
instead: the reviewer types their name (recorded on reviews), and the team shares its screenings.
Neither set = no login (local development only); UI_REQUIRE_LOGIN=true (docker compose sets it)
makes that a configuration error instead, so a deployment cannot end up open by mistake.

Passwords may be stored as scrypt hashes instead of plain text; print one with:
    python ui/login.py hash
"""

import base64
import getpass
import hashlib
import logging
import math
import os
import re
import secrets
import sys
import threading
import time
from collections import deque

import streamlit as st

MAX_FAILURES = 5  # failed sign-ins per client address...
LOCKOUT_SECONDS = 300  # ...within this window lock that address out for the rest of it
# UI_USERS names go to the API in the X-Acting-User header, which accepts only these
USER_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
# A name typed at the shared login goes to the API as the reviewer, which accepts only these
REVIEWER_NAME = re.compile(r"^[\w .,'@-]{1,100}$")
HASH_PREFIX = "scrypt$"
SCRYPT = {"n": 2**14, "r": 8, "p": 1}  # ~16 MB and tens of milliseconds per check


def hash_password(password: str, salt: bytes | None = None) -> str:
    """scrypt$<salt>$<hash>, base64url: no ':' or ',', so it fits in UI_USERS and UI_PASSWORD."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    encode = base64.urlsafe_b64encode
    return f"{HASH_PREFIX}{encode(salt).decode()}${encode(digest).decode()}"


def password_matches(password: str, stored: str) -> bool:
    """Compare in constant time against a stored scrypt hash or plain-text password."""
    if stored.startswith(HASH_PREFIX):
        salt_b64, _, _ = stored.removeprefix(HASH_PREFIX).partition("$")
        try:
            candidate = hash_password(password, base64.urlsafe_b64decode(salt_b64))
        except ValueError:  # a malformed hash in the env matches nothing
            return False
        return secrets.compare_digest(candidate.encode(), stored.encode())
    return secrets.compare_digest(password.encode(), stored.encode())


def parse_users(value: str) -> dict[str, str]:
    users = {}
    for pair in filter(None, (p.strip() for p in value.split(","))):
        name, sep, password = pair.partition(":")
        if sep and USER_NAME.match(name.strip()) and password:
            users[name.strip()] = password
    return users


logger = logging.getLogger(__name__)


def is_hash(stored: str) -> bool:
    """Whether `stored` is a well-formed scrypt$<salt>$<hash>."""
    if not stored.startswith(HASH_PREFIX):
        return False
    salt, sep, digest = stored.removeprefix(HASH_PREFIX).partition("$")
    try:
        return bool(sep and base64.urlsafe_b64decode(salt) and base64.urlsafe_b64decode(digest))
    except ValueError:
        return False


def config_error(stored_passwords: list[str], require_login: bool) -> str | None:
    """Why the configured passwords must not be used, or None if they may."""
    for stored in stored_passwords:
        # A hash that lost its "$" parts, e.g. to docker compose expanding "$..." in an unquoted
        # .env value, must not be compared as a plain-text password: that text would be easy to guess
        if stored.startswith("scrypt") and not is_hash(stored):
            return "A UI password looks like a damaged scrypt hash: put the hash in single quotes in .env."
        if require_login and not is_hash(stored):
            # A deployment: anyone who can read the environment would be able to read the password
            return "UI passwords must be scrypt hashes when UI_REQUIRE_LOGIN is set (python ui/login.py hash)."
    return None


USERS = parse_users(os.getenv("UI_USERS", ""))
SHARED_PASSWORD = "" if USERS else os.getenv("UI_PASSWORD", "")
LOGIN_REQUIRED = bool(USERS or SHARED_PASSWORD)
REQUIRE_LOGIN = os.getenv("UI_REQUIRE_LOGIN", "").lower() in ("1", "true", "yes")
STORED_PASSWORDS = [stored for stored in [*USERS.values(), SHARED_PASSWORD] if stored]
CONFIG_ERROR = config_error(STORED_PASSWORDS, REQUIRE_LOGIN)
if not CONFIG_ERROR and any(not is_hash(stored) for stored in STORED_PASSWORDS):
    # Accepted for local development only (UI_REQUIRE_LOGIN refuses them)
    logger.warning("UI passwords are stored in plain text; use scrypt hashes (python ui/login.py hash)")
# A signed-in session left unused this long is signed out, e.g. on a shared or unattended machine
SESSION_TIMEOUT_SECONDS = 60 * float(os.getenv("UI_SESSION_TIMEOUT_MINUTES") or 30)
# Behind the compose proxy, every request comes from its address: the real client is in the
# X-Forwarded-For header the proxy sets. Trust that header only when told the proxy is there.
BEHIND_PROXY = os.getenv("UI_BEHIND_PROXY", "").lower() in ("1", "true", "yes")
# Compared against when a name is unknown, so a wrong name takes as long as a wrong password
_DUMMY_HASH = hash_password(secrets.token_hex(16))


class FailedSignIns:
    """Recent failed sign-ins per client address, shared by every session of this server, so
    opening more browser sessions does not give an attacker more guesses."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}

    def retry_after(self, client: str, now: float | None = None) -> int:
        """Seconds until `client` may try again; 0 if it is not locked out."""
        now = time.monotonic() if now is None else now
        with self._lock:
            failures = self._recent(client, now)
            if len(failures) < MAX_FAILURES:
                return 0
            return max(1, math.ceil(failures[0] + LOCKOUT_SECONDS - now))

    def record(self, client: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._recent(client, now).append(now)
            # Forget addresses with nothing recent, so memory does not grow with every one seen
            for idle in [c for c, f in self._failures.items() if not f or f[-1] <= now - LOCKOUT_SECONDS]:
                del self._failures[idle]

    def clear(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def _recent(self, client: str, now: float) -> deque[float]:
        failures = self._failures.setdefault(client, deque())
        while failures and failures[0] <= now - LOCKOUT_SECONDS:
            failures.popleft()
        return failures


@st.cache_resource
def failed_sign_ins() -> FailedSignIns:
    return FailedSignIns()  # one per server process, not per session


def password_ok(name: str, password: str) -> bool:
    expected = USERS.get(name, "") if USERS else SHARED_PASSWORD
    # Checked even for an unknown name (against a dummy hash), and in constant time, so neither
    # the password nor which names exist can be found by timing
    matches = password_matches(password, expected or _DUMMY_HASH)
    return matches and bool(expected)


def client_address() -> str:
    if BEHIND_PROXY and (forwarded := st.context.headers.get("X-Forwarded-For")):
        # The proxy appends the address it saw last; earlier entries are whatever the client sent
        return forwarded.split(",")[-1].strip()
    return st.context.ip_address or "unknown"


def require_login() -> str | None:
    """Stop the page until the user signs in. Returns the reviewer's name, or None without a login."""
    if not LOGIN_REQUIRED:
        if REQUIRE_LOGIN:  # fail closed: never serve the page without the login it was meant to have
            st.error("Sign-in is required but not configured: set UI_USERS or UI_PASSWORD.")
            st.stop()
        return None
    if CONFIG_ERROR:  # fail closed: a damaged or plain-text password must not let anyone in
        st.error(f"Sign-in is misconfigured. {CONFIG_ERROR}")
        st.stop()
    if user := st.session_state.get("user"):
        now = time.monotonic()
        if now - st.session_state.get("last_active", now) <= SESSION_TIMEOUT_SECONDS:
            st.session_state.last_active = now
            return user
        sign_out()
        st.info("You were signed out after a period of inactivity.")

    st.title("📄 Resume Screening")
    client = client_address()
    guard = failed_sign_ins()
    with st.form("login"):
        label = "Username" if USERS else "Your name (recorded on your reviews)"
        name = re.sub(r"\s+", " ", st.text_input(label, max_chars=100).strip())
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            if wait := guard.retry_after(client):
                st.error(f"Too many failed sign-ins. Try again in {wait} s.")
            elif not USERS and name and not REVIEWER_NAME.match(name):
                st.error("Your name may use letters, digits, spaces and . , ' @ - only.")
            elif name and password_ok(name, password):
                guard.clear(client)
                st.session_state.user = name
                st.session_state.last_active = time.monotonic()
                # Per-reviewer logins scope the API to the person; a typed name on the shared
                # login cannot be trusted for that, so the team shares its screenings
                st.session_state.acting_user = name if USERS else None
                st.rerun()
            else:
                guard.record(client)
                time.sleep(1)  # slows down guessing within a session too
                st.error("Wrong username or password" if USERS else "Enter your name and the correct password")
    st.stop()
    return None  # not reached: st.stop() ends the script run


def sign_out() -> None:
    for key in ("user", "acting_user", "screening", "last_active"):
        st.session_state.pop(key, None)


if __name__ == "__main__":
    if sys.argv[1:] != ["hash"]:
        sys.exit("usage: python ui/login.py hash")
    print(hash_password(getpass.getpass("Password to hash: ")))
    # docker compose expands "$..." in .env values unless they are single-quoted
    print("In .env, single-quote it: UI_PASSWORD='scrypt$...'", file=sys.stderr)
