from pathlib import Path
from app.llm import llm
from schemas.resume import ResumeProfile
from schemas.job import JobRequirements
from app.graph.state import ResumeState
from app.tools.pdf_tools import extract_text_from_pdf


resume_extractor = llm.with_structured_output(ResumeProfile)
job_extractor = llm.with_structured_output(JobRequirements)

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
    ("system", "Extract structured data from the resume. Use only information present "
                   "in the text. Never invent skills, dates or projects."),
    ("human", state["job_description"])
  ])
  return {"resume": job.model_dump(), "steps": ["analyze_job"]}

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