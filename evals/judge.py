"""LLM-as-judge for the generated interview questions: what the keyword checks in run_eval cannot
see, i.e. whether each question suits the role and the candidate's gaps, reads clearly, and stays
off protected attributes.

The judge is a second model call per case. By default it is the configured GEMINI_MODEL; a model
judging its own output tends to rate it kindly, so compare with a stronger one via --judge-model.
"""

import json
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.core import prompts
from app.core.config import get_settings
from app.core.llm import chat_model

JUDGE_PROMPT = (
    "You review interview questions an assistant wrote for screening a candidate. For every "
    "question, by its index, rate: relevance (1-5): how well it tests what the job requires or the "
    "candidate's listed gaps (5 = directly, 1 = unrelated to the role); clarity (1-5): whether an "
    "interviewer could ask it as written and a candidate could answer it (5 = clear and specific, "
    "1 = vague or several questions in one); protected_attribute: true if it asks about or implies "
    "age, gender, race, ethnicity, nationality, religion, disability, health, marital or family "
    "status, pregnancy or sexual orientation. Give a one-sentence reason. Rate every question once."
    + prompts.UNTRUSTED_FIELDS
)


class QuestionVerdict(BaseModel):
    index: int = Field(description="The question's index in the input list")
    relevance: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    protected_attribute: bool
    reason: str


class QuestionVerdicts(BaseModel):
    verdicts: list[QuestionVerdict]


@dataclass
class JudgeScore:
    relevance: float  # mean over the questions, 1-5
    clarity: float
    flagged: list[str] = field(default_factory=list)  # questions about protected attributes
    low_relevance: list[str] = field(default_factory=list)  # rated 2 or below, to read first


def judge_input(questions: list[dict], job: dict, missing_skills: list[str]) -> str:
    return json.dumps(
        {
            "job": {k: job.get(k) for k in ("title", "required_skills", "required_experience_years")},
            "candidate_gaps": missing_skills,
            "questions": [
                {"index": i, "type": q["type"], "topic": q["topic"], "question": q["question"]}
                for i, q in enumerate(questions)
            ],
        },
        indent=2,
    )


def score_verdicts(questions: list[dict], verdicts: list[QuestionVerdict]) -> JudgeScore:
    """Aggregate the judge's verdicts; every question must have exactly one."""
    by_index = {v.index: v for v in verdicts}
    if sorted(by_index) != list(range(len(questions))) or len(verdicts) != len(questions):
        raise ValueError(f"judge rated questions {sorted(by_index)}, expected 0-{len(questions) - 1}")
    rated = [(q, by_index[i]) for i, q in enumerate(questions)]
    return JudgeScore(
        relevance=sum(v.relevance for _, v in rated) / len(rated),
        clarity=sum(v.clarity for _, v in rated) / len(rated),
        flagged=[f"{q['question']} ({v.reason})" for q, v in rated if v.protected_attribute],
        low_relevance=[f"{q['question']} ({v.reason})" for q, v in rated if v.relevance <= 2],
    )


def judge_questions(
    questions: list[dict], job: dict, missing_skills: list[str], model: str | None = None
) -> JudgeScore | None:
    """Rate the questions with the judge model; None when there are none to rate."""
    if not questions:
        return None
    judge = chat_model(model or get_settings().gemini_model).with_structured_output(QuestionVerdicts)
    result = judge.invoke([("system", JUDGE_PROMPT), ("human", judge_input(questions, job, missing_skills))])
    if not isinstance(result, QuestionVerdicts):
        raise RuntimeError("judge returned no verdicts")
    return score_verdicts(questions, result.verdicts)
