"""HTTP client for the screening API, with errors shown in the page."""

import json
import os
import re
import uuid

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")
API_PREFIX = os.getenv("API_PREFIX", "/api/v1")


def read_api_key() -> str:
    """API_KEY, or the contents of the file API_KEY_FILE names (e.g. a Docker secret, which unlike
    an environment variable does not show in `docker inspect`)."""
    if path := os.getenv("API_KEY_FILE"):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return os.getenv("API_KEY", "")


# Sent on every API call when the API is protected with API keys
API_KEY = read_api_key()
TIMEOUT = 300  # LLM steps can take a while
# What each workflow step is doing, shown while it runs
STEP_LABELS = {
    "validate_input": "Checking the inputs",
    "parse_resume": "Reading the resume PDF",
    "extract_resume": "Extracting resume details",
    "analyze_job": "Analysing the job description",
    "match_requirements": "Matching skills and experience",
    "analyze_gaps": "Analysing gaps",
    "research_guidelines": "Looking up the company's interview guidelines",
    "generate_questions": "Drafting interview questions",
    "human_review": "Recording your review",
    "generate_report": "Writing the report",
}


def md(text: object) -> str:
    """Escape Markdown in text that came from the resume, JD or LLM.

    Streamlit renders Markdown, so a crafted resume could otherwise inject links or images
    (e.g. an image URL that leaks data when the reviewer's browser loads it).
    """
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>~$:])", r"\\\1", str(text))


def headers() -> dict[str, str]:
    """The API key, and the signed-in reviewer as the person the UI acts for: the API then
    shows each reviewer only the screenings they started. With the shared login there is no
    person to vouch for, so the browser session is named instead: the API's rate limit then gives
    each session its own budget (under the key's overall ceiling) rather than one for everyone."""
    sent = {"X-API-Key": API_KEY} if API_KEY else {}
    if user := st.session_state.get("acting_user"):
        sent["X-Acting-User"] = user
    if "client_session" not in st.session_state:
        st.session_state.client_session = uuid.uuid4().hex
    sent["X-Client-Session"] = st.session_state.client_session
    return sent


def show_api_error(r: requests.Response) -> None:
    try:
        detail = r.json().get("detail", r.text)
    except ValueError:  # not JSON, e.g. a proxy error page
        detail = r.text[:500]
    st.error(f"API error {r.status_code}: {md(detail)}")


def api(method: str, path: str, **kwargs) -> dict | None:
    try:
        r = requests.request(method, f"{API_URL}{API_PREFIX}{path}", headers=headers(), timeout=TIMEOUT, **kwargs)
    except requests.ConnectionError:
        st.error(f"Cannot reach the API at {API_URL}. Is the server running?")
        return None
    except requests.RequestException as e:  # timeout, broken response, …
        st.error(f"The API request failed ({type(e).__name__}). Try again.")
        return None
    if not r.ok:
        show_api_error(r)
        return None
    if not r.content:  # DELETE answers 204 with no body
        return {}
    try:
        return r.json()
    except ValueError:
        st.error("The API sent a response that is not valid JSON.")
        return None


def run_with_progress(title: str, path: str, **kwargs) -> dict | None:
    """POST to a workflow endpoint with ?stream=true and show each step live as the graph runs.

    The API sends one JSON event per line: {"type": "step", "thread_id": ...} as each node starts
    and ends, then {"type": "result", "screening": ...}, or {"type": "error", ...} if a step fails.
    If the connection drops mid-run, the screening is loaded by its id instead, so the user sees
    where it got to (or that it is still running) rather than losing it.
    """
    thread_id = None
    with st.status(title, expanded=True) as status:
        try:
            r = requests.post(
                f"{API_URL}{API_PREFIX}{path}",
                params={"stream": "true"},
                headers=headers(),
                stream=True,
                timeout=TIMEOUT,
                **kwargs,
            )
        except requests.RequestException:
            status.update(label="Cannot reach the API", state="error")
            st.error(f"Cannot reach the API at {API_URL}. Is the server running?")
            return None
        with r:
            if not r.ok:  # rejected before the workflow started (bad input, rate limit, …)
                status.update(label="Request rejected", state="error")
                show_api_error(r)
                return None
            try:
                for line in r.iter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if event["type"] == "step":
                        thread_id = event.get("thread_id", thread_id)
                        label = STEP_LABELS.get(event["node"], md(event["node"]))
                        if event["event"] == "started":
                            status.update(label=f"{label}…")
                        elif event["event"] == "finished":
                            status.write(f"✅ {label}")
                        else:  # interrupted: the graph paused for the reviewer
                            status.write("⏸ Questions ready for your review")
                    elif event["type"] == "error":
                        status.update(label="Workflow failed", state="error")
                        st.error(f"API error {event['status']}: {md(event['detail'])}")
                        return None
                    elif event["type"] == "result":
                        status.update(label="Done", state="complete", expanded=False)
                        return event["screening"]
            except (requests.RequestException, ValueError, KeyError):
                pass  # dropped connection or a garbled line: recover below
        status.update(label="Lost the connection before the workflow finished", state="error")
    if thread_id:
        return api("GET", f"/screenings/{thread_id}")
    st.error("The screening did not start. Try again.")
    return None


@st.cache_data(ttl=15, show_spinner=False)
def api_is_up() -> bool:
    try:
        return requests.get(f"{API_URL}/health", timeout=3).ok
    except requests.RequestException:
        return False
