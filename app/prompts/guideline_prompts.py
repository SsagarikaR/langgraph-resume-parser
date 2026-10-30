"""The GUIDELINE_RESEARCH prompt used by the interview-guideline lookup tool (app/tools/guidelines.py)."""

from app.prompts.screening_prompts import UNTRUSTED_FIELDS

GUIDELINE_RESEARCH = (
    "You are preparing to write interview questions. Search the company interview guidelines "
    "for the skills and areas that matter most for this role, one search per skill or area "
    "(including the candidate's missing skills). When finished, reply with a short summary of "
    "the focus areas and red flags you found, grouped by topic." + UNTRUSTED_FIELDS
)
