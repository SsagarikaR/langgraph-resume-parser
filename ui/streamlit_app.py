"""Streamlit UI for the screening API: start a screening, follow it live, review the questions."""

import json
import re
from typing import cast

import streamlit as st

from ui.api_client import API_URL, api, api_is_up, md, run_with_progress
from ui.login import LOGIN_REQUIRED, require_login, sign_out

BREAKPOINT_NODES = ["match_requirements", "analyze_gaps", "generate_questions"]
THREAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # same rule as the API

st.set_page_config(page_title="Resume Screening", page_icon="📄", layout="wide")

# The signed-in reviewer, sent with each review for the audit trail (None without a login)
USER = require_login()


def reload(thread_id: str) -> None:
    if data := api("GET", f"/screenings/{thread_id}"):
        st.session_state.screening = data
        st.rerun()


# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("Resume Screening")
    if api_is_up():
        st.success(f"API connected: {API_URL}")
    else:
        st.error(f"API not reachable: {API_URL}")
        st.caption("Start it with: `uvicorn app.main:app --reload`")
    if not LOGIN_REQUIRED:
        st.caption("⚠️ UI_USERS / UI_PASSWORD is not set: anyone who can open this page can use it.")
    elif USER:
        st.caption(f"Signed in as **{md(USER)}**")
        if st.button("Sign out", use_container_width=True):
            sign_out()
            st.rerun()

    st.divider()
    st.subheader("Open existing screening")
    existing = st.text_input("Screening ID", placeholder="e.g. 3f9a1c2b7d10").strip()
    if st.button("Load", use_container_width=True) and existing:
        if THREAD_ID.match(existing):
            reload(existing)
        else:
            st.warning("A screening ID has only letters, digits, '-' and '_'.")

    with st.expander("My recent screenings"):
        listed = api("GET", "/screenings", params={"limit": 20}) if api_is_up() else None
        recent = cast(list[dict], listed or [])  # this endpoint answers with a list
        for item in recent:
            label = f"{item['candidate'] or 'Candidate'} → {item['job_title'] or 'Role'} · {item['status']}"
            if st.button(label, key=f"open-{item['thread_id']}", use_container_width=True):
                reload(item["thread_id"])
        if not recent:
            st.caption("None yet.")

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

    # The resume check is for the type checker; the button is disabled without one
    if st.button("Run screening", type="primary", disabled=resume is None) and resume is not None:
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
    st.title(f"{md(s['candidate'] or 'Candidate')} → {md(s['job_title'] or 'Role')}")
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
        st.markdown(", ".join(md(skill) for skill in s["matching_skills"]) or "None")
    with right:
        st.subheader("❌ Missing skills")
        st.markdown(", ".join(md(skill) for skill in s["missing_skills"]) or "None")
    if s.get("unverified_skills"):
        st.caption(
            "⚠️ Listed by the model but not found in the resume text, so left out of the match: "
            + ", ".join(md(skill) for skill in s["unverified_skills"])
        )

    gap = s.get("gap_analysis")
    if gap:
        with st.expander(f"Gap analysis — severity: **{md(gap['severity'])}**", expanded=True):
            st.markdown(md(gap["summary"]))
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
                        st.markdown(f"- {md(item)}")
            st.markdown("**Improvement suggestions**")
            for item in gap.get("improvement_suggestions", []):
                st.markdown(f"- {md(item)}")
    elif "generate_questions" in s["steps"]:
        # Questions without a gap analysis: the run took the strong-match route
        st.info("Strong match: gap analysis was skipped.")


def show_questions(questions: list[dict]):
    st.subheader(f"Interview questions ({len(questions)})")
    badge = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
    for i, q in enumerate(questions, 1):
        with st.container(border=True):
            st.markdown(f"**{i}. {md(q['question'])}**")
            st.caption(f"{badge.get(q['difficulty'], '')} {md(q['difficulty'])} · {md(q['type'])} · {md(q['topic'])}")
            st.markdown(f"*Why asked:* {md(q['why_asked'])}")


def review_form(s: dict):
    st.subheader("🧑‍⚖️ Your review")
    feedback = st.text_area(
        "Feedback (required to request a revision)", placeholder="e.g. Add more questions on Kafka and system design"
    )
    can_revise = s["revisions_remaining"] > 0
    if not can_revise:
        st.info("Revisions are turned off on this server: approve the questions to write the report.")
    elif s["revisions_remaining"] == 1:
        st.info(
            "This is the last revision: if you request one, the questions are revised with your "
            "feedback and go straight into the report, without another review."
        )
    approve_col, revise_col = st.columns(2)
    decision = None
    if approve_col.button("Approve questions", type="primary", use_container_width=True):
        decision = {"action": "approve"}
    if revise_col.button("Request revision", use_container_width=True, disabled=not can_revise):
        if not feedback.strip():
            st.warning("Write some feedback so the questions can be improved.")
        else:
            decision = {"action": "revise", "feedback": feedback}
    if decision:
        if USER:
            decision["reviewer"] = USER  # recorded in the screening's audit trail
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

    if s["status"] == "running":
        st.info("⏳ This screening is still running on the server. Refresh to see its progress.")
        if st.button("Refresh", type="primary"):
            reload(s["thread_id"])

    elif s["status"] == "paused":
        breakpoint_form(s)

    elif s["status"] == "awaiting_review":
        st.warning("⏸ Waiting for your review. Approve the questions or ask for a revision.")
        show_questions(s["interview_questions"])
        st.divider()
        review_form(s)

    elif s["status"] == "stopped":
        st.warning(
            "⏹ The run stopped before finishing (the connection was lost or the server restarted). "
            "Continue it from the last completed step."
        )
        if st.button("Continue ▶", type="primary"):
            data = run_with_progress("Continuing…", f"/screenings/{s['thread_id']}/continue", json={})
            if data:
                st.session_state.screening = data
                st.rerun()

    elif s["status"] == "completed":
        if s["approved"]:
            approver = s["reviews"][-1].get("reviewer") if s["reviews"] else None
            by = f" by {md(approver)}" if approver else ""
            st.success(f"✅ Approved{by} after {s['revision_count']} revision(s).")
        else:
            st.warning(
                f"Finished without approval: the revision limit was reached after {s['revision_count']} "
                "revisions. The last feedback was applied to the questions below."
            )
        st.download_button(
            "Download report (JSON)",
            json.dumps(s["final_report"], indent=2),
            file_name=f"{s['thread_id']}_report.json",
            mime="application/json",
        )
        show_questions(s["interview_questions"])

    else:
        st.error(
            "A step failed (for example, the LLM provider was unavailable; see the API logs). "
            "Retry it: the completed steps are kept."
        )
        if st.button("Retry ▶", type="primary"):
            data = run_with_progress("Retrying…", f"/screenings/{s['thread_id']}/continue", json={})
            if data:
                st.session_state.screening = data
                st.rerun()

    with st.expander("Workflow steps"):
        st.write(" → ".join(s["steps"]))

    if s["reviews"]:
        with st.expander("Review history"):
            for r in s["reviews"]:
                who = md(r.get("reviewer") or "unnamed reviewer")
                if r.get("reviewer") and not r.get("reviewer_verified"):
                    who += " (name not verified)"
                via = f" via {md(r['client'])}" if r.get("client") and r.get("client") != r.get("reviewer") else ""
                line = f"- **{md(r['action'])}** by {who}{via} at {md(r['at'])}"
                if r.get("feedback"):
                    line += f": {md(r['feedback'])}"
                st.markdown(line)

    with st.expander("🗑 Delete this screening"):
        st.caption("Erases the stored resume text, report and workflow state. This cannot be undone.")
        if (
            st.button("Delete permanently", type="primary")
            and api("DELETE", f"/screenings/{s['thread_id']}") is not None
        ):
            del st.session_state.screening
            st.rerun()


if "screening" in st.session_state:
    show_screening(st.session_state.screening)
else:
    new_screening_form()
