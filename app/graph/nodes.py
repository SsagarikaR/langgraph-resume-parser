"""The workflow steps. Each node takes the state and returns only the keys it changes."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

from langgraph.types import interrupt

from app.core import prompts
from app.core.config import get_settings
from app.core.llm import structured_call
from app.graph.exceptions import InvalidInputError
from app.graph.matching import candidate_skills, is_met, mentioned, role_months, unverified_skills
from app.graph.state import ResumeState
from app.schemas.analysis import GapAnalysis, InterviewQuestionSet
from app.schemas.job import JobRequirements
from app.schemas.resume import ResumeProfile
from app.tools.guidelines import gather_guidelines
from app.utils.pdf import UnreadablePdfError, extract_text_from_pdf

MIN_JD_LENGTH = 50


def validate_input(state: ResumeState) -> dict:
    path = state.get("resume_path", "")
    if not path or not Path(path).exists():
        raise InvalidInputError(f"Resume file not found: {path}")
    if not path.lower().endswith(".pdf"):
        raise InvalidInputError("Resume must be a PDF")
    jd_length = len(state.get("job_description", "").strip())
    if jd_length < MIN_JD_LENGTH:
        raise InvalidInputError("Job description is missing or too short")
    if jd_length > get_settings().max_document_chars:
        raise InvalidInputError(f"Job description is longer than {get_settings().max_document_chars} characters")
    # Experience is counted up to this day (UTC), so a run resumed or retried later gets the same numbers
    return {"screened_on": datetime.now(timezone.utc).date().isoformat(), "steps": ["validate_input"]}


def parse_resume(state: ResumeState) -> dict:
    path = Path(state["resume_path"])
    if not path.exists():
        # Not a workflow failure that a retry could fix: the screening cannot go on without it
        raise InvalidInputError("The uploaded resume is no longer stored; start a new screening")
    try:
        text = extract_text_from_pdf(path)
    except UnreadablePdfError as e:
        path.unlink(missing_ok=True)  # an input error ends (and erases) the screening
        raise InvalidInputError(f"Resume: {e}") from e
    if not text:
        raise InvalidInputError("No text extracted. The PDF may be scanned and need OCR")
    limit = get_settings().max_document_chars
    if len(text) > limit:
        raise InvalidInputError(f"Resume text is longer than {limit} characters")
    return {"resume_text": text, "steps": ["parse_resume"]}


def extract_resume(state: ResumeState) -> dict:
    # Data minimisation: the text is all later steps need, so the uploaded PDF goes now. Not in
    # parse_resume: its text is saved (checkpointed) only once it returns, and a run cut off before
    # that re-runs parse_resume, which needs the file. No path when called on text alone (the evals).
    if resume_path := state.get("resume_path"):
        Path(resume_path).unlink(missing_ok=True)
    profile = structured_call(ResumeProfile, prompts.RESUME_EXTRACTION, prompts.as_document(state["resume_text"]))
    resume = profile.model_dump()
    resume["unverified_skills"] = unverified_skills(resume, state["resume_text"])

    # Durations are computed here, not by the LLM, so they are correct and repeatable
    screened_on = state.get("screened_on")
    today = date.fromisoformat(screened_on) if screened_on else datetime.now(timezone.utc).date()
    current_month = today.year * 12 + today.month - 1
    all_months: set[int] = set()
    for role in resume["experience"]:
        months = role_months(role, current_month)
        role["duration_months"] = len(months) or None
        all_months |= months
    resume["total_experience_months"] = len(all_months)  # overlapping roles counted once
    return {"resume": resume, "steps": ["extract_resume"]}


def analyze_job(state: ResumeState) -> dict:
    job = structured_call(JobRequirements, prompts.JOB_EXTRACTION, prompts.as_document(state["job_description"]))
    if not job.required_skills:
        # Nothing to match against would score 100% and skip the gap analysis: a misleading strong match
        raise InvalidInputError("No required skills found in the job description")
    return {"job": job.model_dump(), "steps": ["analyze_job"]}


def match_requirements(state: ResumeState) -> dict:
    resume, job = state["resume"], state["job"]
    # Skills from the skills section AND from project tech stacks, if the resume text mentions them
    skills = candidate_skills(resume)

    required = job["required_skills"]
    matching: list[str] = []
    missing: list[str] = []
    for skill in required:
        (matching if is_met(skill, skills) else missing).append(skill)
    pct = round(100 * len(matching) / len(required), 1) if required else 100.0

    years = round(resume.get("total_experience_months", 0) / 12, 1)
    gap = max(0.0, (job.get("required_experience_years") or 0) - years)

    return {
        "matching_skills": matching,
        "missing_skills": missing,
        "match_percentage": pct,
        "candidate_experience_years": years,
        "experience_gap_years": round(gap, 1),
        "steps": ["match_requirements"],
    }


def preferred_skills(state: ResumeState) -> dict:
    """The job's nice-to-have skills the candidate has and lacks: informational, never scored."""
    skills = candidate_skills(state["resume"])
    preferred = state["job"].get("preferred_skills", [])
    return {
        "matching": [s for s in preferred if is_met(s, skills)],
        "missing": [s for s in preferred if not is_met(s, skills)],
    }


def analyze_gaps(state: ResumeState) -> dict:
    facts = {
        "job_title": state["job"]["job_title"],
        "required_skills": state["job"]["required_skills"],
        "missing_skills": state["missing_skills"],
        "matching_skills": state["matching_skills"],
        "preferred_skills": preferred_skills(state),
        "required_years": state["job"].get("required_experience_years"),
        "candidate_years": state["candidate_experience_years"],
        "projects": state["resume"]["projects"],
    }
    analysis = structured_call(GapAnalysis, prompts.GAP_ANALYSIS, json.dumps(facts, indent=2))
    return {"gap_analysis": analysis.model_dump(), "steps": ["analyze_gaps"]}


def research_guidelines(state: ResumeState) -> dict:
    """Company interview guidelines for the role and the candidate's skills, looked up once per
    screening. A step of its own, so a retried or revised question step reuses them instead of
    running the search loop (several LLM and embedding calls) again."""
    topics = [state["job"]["job_title"], *state["matching_skills"], *state["missing_skills"]]
    return {"interview_guidelines": gather_guidelines(topics), "steps": ["research_guidelines"]}


def generate_questions(state: ResumeState) -> dict:
    context = {
        "job_title": state["job"]["job_title"],
        "matching_skills": state["matching_skills"],
        "missing_skills": state["missing_skills"],
        "projects": state["resume"]["projects"],
        "gap_analysis": state.get("gap_analysis"),  # may be None on strong-match path
        "company_interview_guidelines": state.get("interview_guidelines") or "None found",
    }

    instructions = prompts.QUESTION_GENERATION
    if state.get("review_feedback"):
        instructions += prompts.QUESTION_REVISION
        context["reviewer_feedback"] = state["review_feedback"]

    result = structured_call(InterviewQuestionSet, instructions, json.dumps(context, indent=2))

    # Same spirit as unverified_skills: a question whose topic names a skill/project that never
    # appears in the resume or job text is probably invented, so flag it instead of trusting it.
    known_text = " ".join(
        [
            state["job"]["job_title"],
            state.get("job_description", ""),
            state.get("resume_text", ""),
            *state["matching_skills"],
            *state["missing_skills"],
            *(p.get("name", "") for p in state["resume"]["projects"]),
            *(t for p in state["resume"]["projects"] for t in p["technologies"]),
        ]
    ).lower()
    questions = []
    for q in result.questions:
        data = q.model_dump()
        # Behavioral questions legitimately ask about topics (e.g. "teamwork") that never appear
        # verbatim in the resume or job text, so only cross-check topics tied to a specific skill
        topic = data["topic"]
        data["topic_verified"] = data["type"] == "behavioral" or (bool(topic) and mentioned(topic, known_text))
        questions.append(data)
    return {"interview_questions": questions, "steps": ["generate_questions"]}


def human_review(state: ResumeState) -> dict:
    decision = interrupt(
        {
            "candidate": state["resume"]["name"],
            "job_title": state["job"]["job_title"],
            "match_percentage": state["match_percentage"],
            "matching_skills": state["matching_skills"],
            "missing_skills": state["missing_skills"],
            "experience_gap_years": state["experience_gap_years"],
            "gap_summary": (state.get("gap_analysis") or {}).get("summary"),
            "num_questions": len(state["interview_questions"]),
            "question_preview": [q["question"] for q in state["interview_questions"][:5]],
        }
    )

    # Audit trail: who decided what, and when. "client" is the API key's name; "reviewer" is the
    # person, as named by that client (the UI sends its signed-in user), or the client itself.
    review = {
        "action": decision.get("action"),
        "reviewer": decision.get("reviewer"),
        # Whether an API key vouched for the name; a name typed at a shared login is only a claim
        "reviewer_verified": bool(decision.get("reviewer_verified")),
        "client": decision.get("client"),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if decision.get("action") == "approve":
        return {"approved": True, "reviews": [review], "steps": ["human_review:approved"]}

    return {
        "approved": False,
        "review_feedback": decision.get("feedback", ""),
        "revision_count": state.get("revision_count", 0) + 1,
        "reviews": [{**review, "feedback": decision.get("feedback", "")}],
        "steps": ["human_review:revise"],
    }


def generate_report(state: ResumeState) -> dict:
    """The final report, kept in the workflow state. Clients get it from there; it is written to a
    file only when someone exports it (the CLI does), so personal data is not copied by default."""
    reviews = state.get("reviews", [])
    report = {
        "candidate": {
            "name": state["resume"]["name"],
            "email": state["resume"]["email"],
            "education": state["resume"].get("education", []),
        },
        "job_title": state["job"]["job_title"],
        "match_percentage": state["match_percentage"],
        "matching_skills": state["matching_skills"],
        "missing_skills": state["missing_skills"],
        "preferred_skills": preferred_skills(state),
        "unverified_skills": state["resume"].get("unverified_skills", []),
        "experience": {
            "candidate_years": state["candidate_experience_years"],
            "required_years": state["job"].get("required_experience_years"),
            "gap_years": state["experience_gap_years"],
        },
        "projects": state["resume"]["projects"],
        "gap_analysis": state.get("gap_analysis"),
        "interview_guidelines": state.get("interview_guidelines"),
        "interview_questions": state["interview_questions"],
        "approved_by_human": state.get("approved", False),
        "approved_by": reviews[-1]["reviewer"] if state.get("approved") and reviews else None,
        "reviews": reviews,
        "revisions": state.get("revision_count", 0),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return {"final_report": report, "steps": ["generate_report"]}
