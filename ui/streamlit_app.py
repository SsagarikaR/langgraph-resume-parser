import json
import os

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")
TIMEOUT = 300  # LLM steps can take a while
BREAKPOINT_NODES = ["match_requirements", "analyze_gaps", "generate_questions"]
# What each workflow step is doing, shown while it runs
STEP_LABELS = {
    "validate_input": "Checking the inputs",
    "parse_resume": "Reading the resume PDF",
    "extract_resume": "Extracting resume details",
    "analyze_job": "Analysing the job description",
    "match_requirements": "Matching skills and experience",
    "analyze_gaps": "Analysing gaps",
    "generate_questions": "Looking up interview guidelines and drafting questions",
    "human_review": "Recording your review",
    "generate_report": "Writing the report",
}

st.set_page_config(page_title="Resume Screening", page_icon="📄", layout="wide")


# ---------------- API helpers ----------------
def show_api_error(r: requests.Response) -> None:
    detail = r.json().get("detail", r.text) if "json" in r.headers.get("content-type", "") else r.text
    st.error(f"API error {r.status_code}: {detail}")


def api(method: str, path: str, **kwargs) -> dict | None:
    try:
        r = requests.request(method, f"{API_URL}{path}", timeout=TIMEOUT, **kwargs)
    except requests.ConnectionError:
        st.error(f"Cannot reach the API at {API_URL}. Is the server running?")
        return None
    if not r.ok:
        show_api_error(r)
        return None
    return r.json()


def run_with_progress(title: str, path: str, **kwargs) -> dict | None:
    """POST to a workflow endpoint with ?stream=true and show each step live as the graph runs.

    The API sends one JSON event per line: {"type": "step", ...} as each node starts and ends,
    then {"type": "result", "screening": ...}, or {"type": "error", ...} if a step fails.
    """
    with st.status(title, expanded=True) as status:
        try:
            r = requests.post(f"{API_URL}{path}", params={"stream": "true"}, stream=True, timeout=TIMEOUT, **kwargs)
        except requests.ConnectionError:
            status.update(label="Cannot reach the API", state="error")
            st.error(f"Cannot reach the API at {API_URL}. Is the server running?")
            return None
        with r:
            if not r.ok:  # rejected before the workflow started (bad input, rate limit, …)
                status.update(label="Request rejected", state="error")
                show_api_error(r)
                return None
            for line in r.iter_lines():
                if not line:
                    continue
                event = json.loads(line)
                if event["type"] == "step":
                    label = STEP_LABELS.get(event["node"], event["node"])
                    if event["event"] == "started":
                        status.update(label=f"{label}…")
                    elif event["event"] == "finished":
                        status.write(f"✅ {label}")
                    else:  # interrupted: the graph paused for the reviewer
                        status.write("⏸ Questions ready for your review")
                elif event["type"] == "error":
                    status.update(label="Workflow failed", state="error")
                    st.error(f"API error {event['status']}: {event['detail']}")
                    return None
                elif event["type"] == "result":
                    status.update(label="Done", state="complete", expanded=False)
                    return event["screening"]
        status.update(label="Connection closed before the workflow finished", state="error")
        return None


def api_is_up() -> bool:
    try:
        return requests.get(f"{API_URL}/health", timeout=3).ok
    except requests.RequestException:
        return False


# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("Resume Screening")
    if api_is_up():
        st.success(f"API connected: {API_URL}")
    else:
        st.error(f"API not reachable: {API_URL}")
        st.caption("Start it with: `uvicorn app.api:app --reload`")

    st.divider()
    st.subheader("Open existing screening")
    existing = st.text_input("Screening ID", placeholder="e.g. 3f9a1c2b7d10")
    if st.button("Load", use_container_width=True) and existing.strip():
        if data := api("GET", f"/screenings/{existing.strip()}"):
            st.session_state.screening = data

    if "screening" in st.session_state and st.button("Start a new screening", use_container_width=True):
        del st.session_state.screening
        st.rerun()


# ---------------- New screening form ----------------
def new_screening_form():
    st.title("📄 New screening")
    st.caption(
        "Upload a resume and a job description. The workflow extracts details, matches skills, "
        "analyses gaps and drafts interview questions, then pauses for your review."
    )

    resume = st.file_uploader("Resume (PDF)", type=["pdf"])
    jd_tab_text, jd_tab_file = st.tabs(["Paste job description", "Upload job description"])
    with jd_tab_text:
        jd_text = st.text_area(
            "Job description", height=220, label_visibility="collapsed", placeholder="Paste the job description here…"
        )
    with jd_tab_file:
        jd_file = st.file_uploader("Job description (.txt or .pdf)", type=["txt", "pdf"])

    with st.expander("⏸ Breakpoints (optional)"):
        st.caption(
            "Pause the workflow after these steps so you can inspect the state, "
            "and after matching, correct the skill match before it continues."
        )
        breakpoints = st.multiselect("Pause after", BREAKPOINT_NODES)

    if st.button("Run screening", type="primary", disabled=resume is None):
        if not jd_text.strip() and jd_file is None:
            st.warning("Add a job description, either pasted or uploaded.")
            return
        files = {"resume": (resume.name, resume.getvalue(), "application/pdf")}
        if jd_file is not None and not jd_text.strip():
            files["jd_file"] = (jd_file.name, jd_file.getvalue(), jd_file.type or "application/octet-stream")
        data = run_with_progress(
            "Running the screening…", "/screenings", files=files, data={"jd_text": jd_text, "breakpoints": breakpoints}
        )
        if data:
            st.session_state.screening = data
            st.rerun()


# ---------------- Results ----------------
def show_overview(s: dict):
    st.title(f"{s['candidate'] or 'Candidate'} → {s['job_title'] or 'Role'}")
    st.caption(f"Screening ID: `{s['thread_id']}`")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Skill match", f"{s['match_percentage'] or 0:.0f}%")
    c2.metric(
        "Experience",
        f"{s['candidate_experience_years'] or 0} yrs",
        help=f"Required: {s['required_experience_years'] or 'not stated'} yrs",
    )
    c3.metric("Experience gap", f"{s['experience_gap_years'] or 0} yrs")
    c4.metric("Revisions", s["revision_count"])

    left, right = st.columns(2)
    with left:
        st.subheader("✅ Matching skills")
        st.write(", ".join(s["matching_skills"]) or "None")
    with right:
        st.subheader("❌ Missing skills")
        st.write(", ".join(s["missing_skills"]) or "None")

    gap = s.get("gap_analysis")
    if gap:
        with st.expander(f"Gap analysis — severity: **{gap['severity']}**", expanded=True):
            st.write(gap["summary"])
            cols = st.columns(2)
            sections = [
                ("Technical gaps", "technical_gaps"),
                ("Experience gaps", "experience_gaps"),
                ("Project gaps", "project_gaps"),
                ("Transferable strengths", "transferable_strengths"),
            ]
            for i, (title, key) in enumerate(sections):
                with cols[i % 2]:
                    st.markdown(f"**{title}**")
                    for item in gap.get(key, []):
                        st.markdown(f"- {item}")
            st.markdown("**Improvement suggestions**")
            for item in gap.get("improvement_suggestions", []):
                st.markdown(f"- {item}")
    elif "analyze_gaps" in s["steps"] or s["status"] != "paused":
        st.info("Strong match: gap analysis was skipped.")


def show_questions(questions: list[dict]):
    st.subheader(f"Interview questions ({len(questions)})")
    badge = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
    for i, q in enumerate(questions, 1):
        with st.container(border=True):
            st.markdown(f"**{i}. {q['question']}**")
            st.caption(f"{badge.get(q['difficulty'], '')} {q['difficulty']} · {q['type']} · {q['topic']}")
            st.markdown(f"*Why asked:* {q['why_asked']}")


def review_form(s: dict):
    st.subheader("🧑‍⚖️ Your review")
    feedback = st.text_area(
        "Feedback (required to request a revision)", placeholder="e.g. Add more questions on Kafka and system design"
    )
    approve_col, revise_col = st.columns(2)
    decision = None
    if approve_col.button("Approve questions", type="primary", use_container_width=True):
        decision = {"action": "approve"}
    if revise_col.button("Request revision", use_container_width=True):
        if not feedback.strip():
            st.warning("Write some feedback so the questions can be improved.")
        else:
            decision = {"action": "revise", "feedback": feedback}
    if decision:
        data = run_with_progress("Updating…", f"/screenings/{s['thread_id']}/review", json=decision)
        if data:
            st.session_state.screening = data
            st.rerun()


def breakpoint_form(s: dict):
    last_step = s["steps"][-1] if s["steps"] else "start"
    st.warning(f"⏸ Paused at a breakpoint after **{last_step}**. Next step: **{', '.join(s['next_nodes'])}**.")

    matching = None
    if last_step == "match_requirements":
        st.markdown(
            "Correct the skill match if the automatic matching missed something. "
            "Changing it can change the route (gap analysis is skipped for a strong match)."
        )
        matching = st.multiselect(
            "Required skills the candidate has", s["required_skills"], default=s["matching_skills"]
        )
    elif s["interview_questions"]:
        show_questions(s["interview_questions"])

    if st.button("Continue ▶", type="primary"):
        edited = matching is not None and set(matching) != set(s["matching_skills"])
        body = {"matching_skills": matching} if edited else {}
        data = run_with_progress("Continuing…", f"/screenings/{s['thread_id']}/continue", json=body)
        if data:
            st.session_state.screening = data
            st.rerun()

    with st.expander("Raw state at this breakpoint"):
        st.json(s)


def show_screening(s: dict):
    show_overview(s)
    st.divider()

    if s["status"] == "paused":
        breakpoint_form(s)

    elif s["status"] == "awaiting_review":
        st.warning("⏸ Waiting for your review. Approve the questions or ask for a revision.")
        show_questions(s["interview_questions"])
        st.divider()
        review_form(s)

    elif s["status"] == "completed":
        if s["approved"]:
            st.success(f"✅ Approved after {s['revision_count']} revision(s). Report saved to `{s['report_path']}`.")
        else:
            st.warning(
                f"Finished without approval after {s['revision_count']} revisions "
                f"(revision limit reached). Report saved to `{s['report_path']}`."
            )
        st.download_button(
            "Download report (JSON)",
            json.dumps(s["final_report"], indent=2),
            file_name=f"{s['thread_id']}_report.json",
            mime="application/json",
        )
        show_questions(s["interview_questions"])

    else:
        st.error("This screening stopped before finishing. Start a new one.")

    with st.expander("Workflow steps"):
        st.write(" → ".join(s["steps"]))


if "screening" in st.session_state:
    show_screening(st.session_state.screening)
else:
    new_screening_form()
