import json
from pathlib import Path
from datetime import datetime
from app.llm import llm
from langgraph.types import interrupt
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from app.schemas.resume import ResumeProfile
from app.schemas.job import JobRequirements
from app.graph.state import ResumeState
from app.tools.pdf_tools import extract_text_from_pdf
from app.schemas.analysis import GapAnalysis
from app.schemas.analysis import InterviewQuestionSet
from app.tools.knowledge_tools import lookup_interview_guideline


TOOLS = {"lookup_interview_guideline": lookup_interview_guideline}
resume_extractor = llm.with_structured_output(ResumeProfile)
job_extractor = llm.with_structured_output(JobRequirements)
gap_analyzer = llm.with_structured_output(GapAnalysis)
question_generator = llm.with_structured_output(InterviewQuestionSet)
REPORTS_DIR = Path("reports")

def validate_input(state: ResumeState) -> dict:
  path = state.get("resume_path", "")
  if not path or not Path(path).exists():
    raise ValueError(f"Resume file not found: {path}")
  if not path.lower().endswith(".pdf"):
    raise ValueError(f"Resume must be a pdf")
  if len(state.get("job_description", "").strip()) < 50:
    raise ValueError(f"Job description is missing or too short")
  return {"steps": ["validate_input"]}

def parse_resume(state: ResumeState) -> dict:
    text = extract_text_from_pdf(state["resume_path"])
    if not text:
        raise ValueError("No text extracted. The PDF may be scanned and need OCR")
    return {"resume_text": text, "steps": ["parse_resume"]}

def extract_resume(state: ResumeState) -> dict:
  profile = resume_extractor.invoke([
    ("system", "Extract structured data from the resume. Use only information present "
                   "in the text. Never invent skills, dates or projects."),
    ("human", state["resume_text"])
  ])
  return {"resume": profile.model_dump(), "steps": ["extract_resume"]}

def analyze_job(state: ResumeState) -> dict:
  job = job_extractor.invoke([
    ("system", "Extract structured requirements from the job description. Use only "
                   "information present in the text. Never invent skills or requirements."),
    ("human", state["job_description"])
  ])
  return {"job": job.model_dump(), "steps": ["analyze_job"]}

# Simple normalization so "Postgres" == "PostgreSQL", "NodeJS" == "Node.js"
ALIASES = {
    "postgres": "postgresql",
    "nodejs": "node.js",
    "node": "node.js",
    "js": "javascript",
    "ts": "typescript",
    "k8s": "kubernetes",
    "retrieval augmented generation": "rag",
}


def normalize(skill: str) -> str:
    s = skill.strip().lower()
    return ALIASES.get(s, s)


def match_requirements(state: ResumeState) -> dict:
    resume, job = state["resume"], state["job"]

    # Skills from the skills section AND from project tech stacks
    candidate_skills = {normalize(s) for s in resume["skills"]}
    for project in resume["projects"]:
        candidate_skills |= {normalize(t) for t in project["technologies"]}

    required = job["required_skills"]
    matching = [s for s in required if normalize(s) in candidate_skills]
    missing = [s for s in required if normalize(s) not in candidate_skills]
    pct = round(100 * len(matching) / len(required), 1) if required else 100.0

    months = sum(e["duration_months"] or 0 for e in resume["experience"])
    years = round(months / 12, 1)
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
    analysis = gap_analyzer.invoke([
        ("system", "You are a technical recruiter. The numbers below were computed by code "
                   "and are correct; do not recalculate them. Interpret the gaps, note "
                   "transferable skills, and suggest concrete improvements."),
        ("human", json.dumps(facts, indent=2)),
    ])
    return {"gap_analysis": analysis.model_dump(), "steps": ["analyze_gaps"]}


def generate_questions(state: ResumeState) -> dict:
    context = {
        "job_title": state["job"]["job_title"],
        "matching_skills": state["matching_skills"],
        "missing_skills": state["missing_skills"],
        "projects": state["resume"]["projects"],
        "gap_analysis": state.get("gap_analysis"),   # may be None on strong-match path
    }

    instructions = (
        "Generate 12-15 interview questions for this candidate. Mix: questions that verify "
        "claimed strengths, questions that probe gaps, questions about their specific "
        "projects, and 2 behavioral questions."
    )
    if state.get("review_feedback"):
        instructions += f"\n\nA reviewer rejected the previous set. Their feedback: {state['review_feedback']}"

    result = question_generator.invoke([
        ("system", instructions),
        ("human", json.dumps(context, indent=2)),
    ])
    return {
        "interview_questions": [q.model_dump() for q in result.questions],
        "steps": ["generate_questions"],
    }

def _text(content) -> str:
    # Some providers return a list of content blocks instead of a string
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def gather_guidelines(topics: list[str]) -> str:
    llm_with_tools = llm.bind_tools(list(TOOLS.values()))
    messages = [
        SystemMessage("You are preparing to write interview questions. Look up company "
                      "guidelines for the topics that matter most. When finished, reply "
                      "with a short summary of the focus areas you found."),
        HumanMessage(f"Topics: {', '.join(topics)}"),
    ]
    for _ in range(5):                         # safety limit on loop iterations
        ai = llm_with_tools.invoke(messages)
        messages.append(ai)
        if not ai.tool_calls:                  # LLM is done using tools
            return _text(ai.content)
        for call in ai.tool_calls:
            output = TOOLS[call["name"]].invoke(call["args"])
            messages.append(ToolMessage(content=output, tool_call_id=call["id"]))
    return ""

def human_review(state: ResumeState) -> dict:
    decision = interrupt({
        "candidate": state["resume"]["name"],
        "job_title": state["job"]["job_title"],
        "match_percentage": state["match_percentage"],
        "matching_skills": state["matching_skills"],
        "missing_skills": state["missing_skills"],
        "experience_gap_years": state["experience_gap_years"],
        "gap_summary": (state.get("gap_analysis") or {}).get("summary"),
        "num_questions": len(state["interview_questions"]),
        "question_preview": [q["question"] for q in state["interview_questions"][:5]],
    })

    if decision.get("action") == "approve":
        return {"approved": True, "steps": ["human_review:approved"]}

    return {
        "approved": False,
        "review_feedback": decision.get("feedback", ""),
        "revision_count": state.get("revision_count", 0) + 1,
        "steps": ["human_review:revise"],
    }

def generate_report(state: ResumeState) -> dict:
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
        "interview_questions": state["interview_questions"],
        "approved_by_human": state.get("approved", False),
        "revisions": state.get("revision_count", 0),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    REPORTS_DIR.mkdir(exist_ok=True)
    safe_name = state["resume"]["name"].replace(" ", "_").lower()
    path = REPORTS_DIR / f"{safe_name}_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(report, indent=2))
    return {"final_report": report, "report_path": str(path), "steps": ["generate_report"]}