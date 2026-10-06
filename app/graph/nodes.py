import json
import re
from pathlib import Path
from datetime import date, datetime
from app.llm import llm
from langgraph.types import interrupt
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from app.schemas.resume import ResumeProfile
from app.schemas.job import JobRequirements
from app.graph.state import ResumeState
from app.tools.pdf_tools import extract_text_from_pdf
from app.schemas.analysis import GapAnalysis
from app.schemas.analysis import InterviewQuestionSet
from app.tools.knowledge_tools import lookup_interview_guideline, available_topics

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

def _month_index(value: str | None) -> int | None:
    # "2023-07" -> months since year 0; tolerates "2023" or "2023-7"
    m = re.match(r"\s*(\d{4})(?:-(\d{1,2}))?", value or "")
    if not m:
        return None
    return int(m.group(1)) * 12 + int(m.group(2) or 1) - 1


def _role_months(role: dict, today: int) -> set[int]:
    start = _month_index(role.get("start_date"))
    end = today if role.get("is_current") else _month_index(role.get("end_date"))
    if start is None or end is None or end < start:
        return set()
    return set(range(start, end + 1))          # inclusive: Jul-Jul counts as 1 month


def extract_resume(state: ResumeState) -> dict:
  profile = resume_extractor.invoke([
    ("system", "Extract structured data from the resume. Use only information present "
                   "in the text. Never invent skills, dates or projects. Copy dates exactly "
                   "as YYYY-MM; do not calculate durations. List each skill as a short "
                   "canonical name, splitting groups like 'AWS (EC2, S3)' into separate skills."),
    ("human", state["resume_text"])
  ])
  resume = profile.model_dump()

  # Durations are computed here, not by the LLM, so they are correct and repeatable
  today = date.today().year * 12 + date.today().month - 1
  all_months: set[int] = set()
  for role in resume["experience"]:
    months = _role_months(role, today)
    role["duration_months"] = len(months) or None
    all_months |= months
  resume["total_experience_months"] = len(all_months)   # overlapping roles counted once
  return {"resume": resume, "steps": ["extract_resume"]}

def analyze_job(state: ResumeState) -> dict:
  job = job_extractor.invoke([
    ("system", "Extract structured requirements from the job description. Use only "
                   "information present in the text. Never invent skills or requirements. "
                   "Use short canonical skill names, and keep alternatives such as "
                   "'Kafka or RabbitMQ' as a single required skill."),
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
    "amazon web services": "aws",
    "golang": "go",
    "rest": "rest api",
    "rest apis": "rest api",
    "restful apis": "rest api",
    "rest api design": "rest api",
    "github actions": "ci/cd",
    "gitlab ci": "ci/cd",
    "jenkins": "ci/cd",
    "ci/cd pipelines": "ci/cd",
}


def normalize(skill: str) -> str:
    s = " ".join(skill.lower().split())
    return ALIASES.get(s, s)


def skill_variants(skill: str) -> set[str]:
    main, _, inner = skill.partition("(")
    parts = [main, *inner.rstrip(")").split(",")]
    return {normalize(p) for p in parts if p.strip()}


def is_met(required: str, candidate_skills: set[str]) -> bool:
    # "Kafka or RabbitMQ" is met by either one
    alternatives = re.split(r"\s+or\s+", required, flags=re.IGNORECASE)
    return any(skill_variants(alt) & candidate_skills for alt in alternatives)


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

    # Look up company guidelines once; revisions reuse them from state
    guidelines = state.get("interview_guidelines")
    if guidelines is None:
        topics = [state["job"]["job_title"], *state["matching_skills"], *state["missing_skills"]]
        guidelines = gather_guidelines(topics)
    context["company_interview_guidelines"] = guidelines or "None found"

    instructions = (
        "Generate 12-15 interview questions for this candidate. Mix: questions that verify "
        "claimed strengths, questions that probe gaps, questions about their specific "
        "projects, and 2 behavioral questions. Follow the company interview guidelines: "
        "focus on the areas they list and use their red flags to shape follow-up questions."
    )
    if state.get("review_feedback"):
        instructions += f"\n\nA reviewer rejected the previous set. Their feedback: {state['review_feedback']}"

    result = question_generator.invoke([
        ("system", instructions),
        ("human", json.dumps(context, indent=2)),
    ])
    return {
        "interview_questions": [q.model_dump() for q in result.questions],
        "interview_guidelines": guidelines,
        "steps": ["generate_questions"],
    }

def _text(content) -> str:
    # Some providers return a list of content blocks instead of a string
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def gather_guidelines(topics: list[str]) -> str:
    """Let the LLM look up company guidelines with the knowledge base tool and summarise them."""
    llm_with_tools = llm.bind_tools(list(TOOLS.values()))
    messages = [
        SystemMessage("You are preparing to write interview questions. Look up company "
                      "guidelines for every available topic relevant to this role. "
                      f"Available topics: {', '.join(available_topics())}. When finished, reply "
                      "with a short summary of the focus areas and red flags you found, "
                      "grouped by topic."),
        HumanMessage(f"Role and candidate skills: {', '.join(topics)}"),
    ]
    found = []                                 # raw guideline texts, used if no summary comes back
    for _ in range(5):                         # safety limit on loop iterations
        ai = llm_with_tools.invoke(messages)
        messages.append(ai)
        if not ai.tool_calls:                  # LLM is done using tools
            return _text(ai.content) or "\n\n".join(found)
        for call in ai.tool_calls:
            tool_fn = TOOLS.get(call["name"])
            output = tool_fn.invoke(call["args"]) if tool_fn else f"Unknown tool: {call['name']}"
            if not output.startswith(("No guideline", "Unknown tool")):
                found.append(output)
            messages.append(ToolMessage(content=output, tool_call_id=call["id"]))
    return "\n\n".join(found)

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
        "interview_guidelines": state.get("interview_guidelines"),
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