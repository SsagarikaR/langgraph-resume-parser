"""The workflow steps. Each node takes the state and returns only the keys it changes."""

import json
import logging
from datetime import date, datetime
from pathlib import Path

from langgraph.types import interrupt

from app.core.config import get_settings
from app.core.llm import structured_call
from app.graph import prompts
from app.graph.exceptions import InvalidInputError
from app.graph.guidelines import gather_guidelines
from app.graph.matching import is_met, role_months, skill_variants
from app.graph.state import ResumeState
from app.schemas.analysis import GapAnalysis, InterviewQuestionSet
from app.schemas.job import JobRequirements
from app.schemas.resume import ResumeProfile
from app.utils.files import safe_filename
from app.utils.pdf import extract_text_from_pdf

logger = logging.getLogger(__name__)

MIN_JD_LENGTH = 50


def validate_input(state: ResumeState) -> dict:
    path = state.get("resume_path", "")
    if not path or not Path(path).exists():
        raise InvalidInputError(f"Resume file not found: {path}")
    if not path.lower().endswith(".pdf"):
        raise InvalidInputError("Resume must be a PDF")
    if len(state.get("job_description", "").strip()) < MIN_JD_LENGTH:
        raise InvalidInputError("Job description is missing or too short")
    return {"steps": ["validate_input"]}


def parse_resume(state: ResumeState) -> dict:
    text = extract_text_from_pdf(state["resume_path"])
    if not text:
        raise InvalidInputError("No text extracted. The PDF may be scanned and need OCR")
    return {"resume_text": text, "steps": ["parse_resume"]}


def extract_resume(state: ResumeState) -> dict:
    profile = structured_call(ResumeProfile, prompts.RESUME_EXTRACTION, state["resume_text"])
    resume = profile.model_dump()

    # Durations are computed here, not by the LLM, so they are correct and repeatable
    today = date.today()
    current_month = today.year * 12 + today.month - 1
    all_months: set[int] = set()
    for role in resume["experience"]:
        months = role_months(role, current_month)
        role["duration_months"] = len(months) or None
        all_months |= months
    resume["total_experience_months"] = len(all_months)  # overlapping roles counted once
    return {"resume": resume, "steps": ["extract_resume"]}


def analyze_job(state: ResumeState) -> dict:
    job = structured_call(JobRequirements, prompts.JOB_EXTRACTION, state["job_description"])
    return {"job": job.model_dump(), "steps": ["analyze_job"]}


def match_requirements(state: ResumeState) -> dict:
    resume, job = state["resume"], state["job"]

    # Skills from the skills section AND from project tech stacks
    candidate_skills: set[str] = set()
    for s in resume["skills"]:
        candidate_skills |= skill_variants(s)
    for project in resume["projects"]:
        for t in project["technologies"]:
            candidate_skills |= skill_variants(t)

    required = job["required_skills"]
    matching = [s for s in required if is_met(s, candidate_skills)]
    missing = [s for s in required if not is_met(s, candidate_skills)]
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


def analyze_gaps(state: ResumeState) -> dict:
    facts = {
        "job_title": state["job"]["job_title"],
        "required_skills": state["job"]["required_skills"],
        "missing_skills": state["missing_skills"],
        "matching_skills": state["matching_skills"],
        "required_years": state["job"].get("required_experience_years"),
        "candidate_years": state["candidate_experience_years"],
        "projects": state["resume"]["projects"],
    }
    analysis = structured_call(GapAnalysis, prompts.GAP_ANALYSIS, json.dumps(facts, indent=2))
    return {"gap_analysis": analysis.model_dump(), "steps": ["analyze_gaps"]}


def generate_questions(state: ResumeState) -> dict:
    context = {
        "job_title": state["job"]["job_title"],
        "matching_skills": state["matching_skills"],
        "missing_skills": state["missing_skills"],
        "projects": state["resume"]["projects"],
        "gap_analysis": state.get("gap_analysis"),  # may be None on strong-match path
    }

    # Look up company guidelines once; revisions reuse them from state
    guidelines = state.get("interview_guidelines")
    if guidelines is None:
        topics = [state["job"]["job_title"], *state["matching_skills"], *state["missing_skills"]]
        guidelines = gather_guidelines(topics)
    context["company_interview_guidelines"] = guidelines or "None found"

    instructions = prompts.QUESTION_GENERATION
    if state.get("review_feedback"):
        instructions += prompts.QUESTION_REVISION.format(feedback=state["review_feedback"])

    result = structured_call(InterviewQuestionSet, instructions, json.dumps(context, indent=2))
    return {
        "interview_questions": [q.model_dump() for q in result.questions],
        "interview_guidelines": guidelines,
        "steps": ["generate_questions"],
    }


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

    if decision.get("action") == "approve":
        return {"approved": True, "steps": ["human_review:approved"]}

    return {
        "approved": False,
        "review_feedback": decision.get("feedback", ""),
        "revision_count": state.get("revision_count", 0) + 1,
        "steps": ["human_review:revise"],
    }


def generate_report(state: ResumeState) -> dict:
    now = datetime.now()
    report = {
        "candidate": {"name": state["resume"]["name"], "email": state["resume"]["email"]},
        "job_title": state["job"]["job_title"],
        "match_percentage": state["match_percentage"],
        "matching_skills": state["matching_skills"],
        "missing_skills": state["missing_skills"],
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
        "revisions": state.get("revision_count", 0),
        "generated_at": now.isoformat(timespec="seconds"),
    }
    reports_dir = get_settings().reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{safe_filename(state['resume']['name'])}_{now:%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(report, indent=2))
    logger.info("Report written to %s", path)
    return {"final_report": report, "report_path": str(path), "steps": ["generate_report"]}
