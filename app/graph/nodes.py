from pathlib import Path
from app.graph.state import ResumeState
from app.tools.pdf_tools import extract_text_from_pdf

def validate_input(state -> ResumeState) -> dict
  path = state.get("resume path", "")
  if not path or not Path(path).exists():
    raise ValueError(f"Resume file not found: {path}")
  if not path.lower().endswith(".pdf"):
    raise ValueError(f"Resume must be a pdf")
  if len(state.get("job_description", "").strip()) < 50
    raise ValueError(f"Job description is missing or too short")
  return {"steps": ["validate_input"]}

def parse_resume(state: ResumeState) -> dict:
    text = extract_text_from_pdf(state["resume_path"])
    if not text:
        raise ValueError("No text extracted. The PDF may be scanned and need OCR")
    return {"resume_text": text, "steps": ["parse_resume"]}