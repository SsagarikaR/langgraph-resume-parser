"""Run the labelled cases through the real LLM steps and score the output.

    python -m evals.run_eval              # all cases in evals/cases/
    python -m evals.run_eval --case 01    # only cases whose file name contains "01"

Calls the Gemini API (two LLM calls per case), so it needs GEMINI_API_KEY and is not part of
pytest. Prints a report, saves it to evals/results/, and exits with code 1 when an overall
score misses its threshold or a case fails to run.
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.graph.nodes import analyze_job, extract_resume
from app.utils.pdf import extract_text_from_pdf
from evals.cases import CASES_DIR, Case, load_cases
from evals.metrics import SkillScore, accuracy, mean_absolute_error, score_skills

logger = logging.getLogger(__name__)

RESULTS_DIR = CASES_DIR.parent / "results"

# Overall pass marks. Starting guesses: set them from the first baseline run and raise them
# as prompts improve, so a change that makes extraction worse is caught.
MIN_RESUME_SKILL_F1 = 0.85
MIN_JD_SKILL_F1 = 0.85
MIN_JD_YEARS_ACCURACY = 0.9
MAX_EXPERIENCE_MAE_MONTHS = 2.0


@dataclass
class CaseResult:
    resume_skills_predicted: list[str]  # raw LLM output, to see what was returned instead
    jd_skills_predicted: list[str]
    resume_skills: SkillScore
    jd_skills: SkillScore
    experience_expected: int
    experience_predicted: int
    years_expected: float | None
    years_predicted: float | None

    @property
    def experience_error(self) -> int:
        return self.experience_predicted - self.experience_expected

    @property
    def years_correct(self) -> bool:
        if self.years_expected is None or self.years_predicted is None:
            return self.years_expected is None and self.years_predicted is None
        return abs(self.years_expected - self.years_predicted) < 0.01


def read_jd(path: Path) -> str:
    return extract_text_from_pdf(path) if path.suffix.lower() == ".pdf" else path.read_text()


def evaluate_case(case: Case) -> CaseResult:
    """Run the real extraction nodes on one case and score them against its labels."""
    resume = extract_resume({"resume_text": extract_text_from_pdf(case.resume)})["resume"]
    job = analyze_job({"job_description": read_jd(case.jd)})["job"]

    expected = case.expected
    today = date.today()
    # Matching uses skills from the skills section and from project tech stacks, so score both
    candidate_skills = resume["skills"] + [t for p in resume["projects"] for t in p["technologies"]]
    return CaseResult(
        resume_skills_predicted=candidate_skills,
        jd_skills_predicted=job["required_skills"],
        resume_skills=score_skills(
            candidate_skills, expected.resume_skills, expected.resume_optional_skills, extra_spellings_ok=True
        ),
        jd_skills=score_skills(job["required_skills"], expected.jd_required_skills, expected.jd_optional_skills),
        experience_expected=expected.experience_months(today.year * 12 + today.month - 1),
        experience_predicted=resume["total_experience_months"],
        years_expected=expected.jd_required_years,
        years_predicted=job.get("required_experience_years"),
    )


def prf(score: SkillScore) -> str:
    return f"{score.precision:.2f} / {score.recall:.2f} / {score.f1:.2f}"


def print_report(results: dict[str, CaseResult], failed: dict[str, str]) -> None:
    print(f"\n{'Case':<28} {'Resume skills P / R / F1':<26} {'Exp months':<14} {'JD skills P / R / F1':<24} JD years")
    for name, r in results.items():
        months = f"{r.experience_expected} → {r.experience_predicted}"
        years = f"{r.years_expected} → {r.years_predicted} {'✓' if r.years_correct else '✗'}"
        print(f"{name:<28} {prf(r.resume_skills):<26} {months:<14} {prf(r.jd_skills):<24} {years}")
    for name, error in failed.items():
        print(f"{name:<28} FAILED: {error}")

    # The skill lists behind the scores: what to fix in prompts or matching ALIASES
    for name, r in results.items():
        lines = [
            ("Resume: missed", r.resume_skills.false_negatives),
            ("Resume: extra ", r.resume_skills.false_positives),
            ("JD: missed    ", r.jd_skills.false_negatives),
            ("JD: extra     ", r.jd_skills.false_positives),
        ]
        if any(skills for _, skills in lines):
            print(f"\n{name}")
            for label, skills in lines:
                if skills:
                    print(f"  {label} {', '.join(skills)}")


def overall_scores(results: dict[str, CaseResult]) -> dict[str, float]:
    # Skill scores are micro-averaged: true/false positive counts are summed over all cases
    resume_total = sum((r.resume_skills for r in results.values()), SkillScore())
    jd_total = sum((r.jd_skills for r in results.values()), SkillScore())
    return {
        "resume_skill_precision": resume_total.precision,
        "resume_skill_recall": resume_total.recall,
        "resume_skill_f1": resume_total.f1,
        "experience_mae_months": mean_absolute_error(r.experience_error for r in results.values()),
        "jd_skill_precision": jd_total.precision,
        "jd_skill_recall": jd_total.recall,
        "jd_skill_f1": jd_total.f1,
        "jd_years_accuracy": accuracy(r.years_correct for r in results.values()),
    }


def check_thresholds(overall: dict[str, float]) -> list[str]:
    checks = [
        ("Resume skill F1", overall["resume_skill_f1"], MIN_RESUME_SKILL_F1, True),
        ("Experience MAE (months)", overall["experience_mae_months"], MAX_EXPERIENCE_MAE_MONTHS, False),
        ("JD skill F1", overall["jd_skill_f1"], MIN_JD_SKILL_F1, True),
        ("JD years accuracy", overall["jd_years_accuracy"], MIN_JD_YEARS_ACCURACY, True),
    ]
    print("\nOverall")
    failures = []
    for label, value, limit, higher_is_better in checks:
        passed = value >= limit if higher_is_better else value <= limit
        sign = "≥" if higher_is_better else "≤"
        print(f"  {'PASS' if passed else 'FAIL'}  {label:<24} {value:.2f}  ({sign} {limit})")
        if not passed:
            failures.append(label)
    return failures


def save_report(results: dict[str, CaseResult], failed: dict[str, str], overall: dict[str, float]) -> Path:
    model = get_settings().gemini_model
    report = {
        "model": model,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "overall": overall,
        "cases": {
            name: {
                "resume_skills": {"predicted": r.resume_skills_predicted, **vars(r.resume_skills)}
                | {"f1": r.resume_skills.f1},
                "jd_skills": {"predicted": r.jd_skills_predicted, **vars(r.jd_skills)} | {"f1": r.jd_skills.f1},
                "experience_months": {"expected": r.experience_expected, "predicted": r.experience_predicted},
                "jd_required_years": {"expected": r.years_expected, "predicted": r.years_predicted},
            }
            for name, r in results.items()
        },
        "failed": failed,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{model}.json"
    path.write_text(json.dumps(report, indent=2))
    return path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Score LLM extraction against labelled cases")
    parser.add_argument("--case", help="only run cases whose file name contains this text")
    args = parser.parse_args(argv)

    setup_logging()
    cases = load_cases(args.case)
    if not cases:
        sys.exit(f"No cases found in {CASES_DIR}")
    print(f"Evaluating {len(cases)} case(s) with {get_settings().gemini_model}…")

    results: dict[str, CaseResult] = {}
    failed: dict[str, str] = {}
    for name, case in cases.items():
        try:
            results[name] = evaluate_case(case)
        except Exception as e:  # one broken case should not stop the others
            logger.exception("Case %s failed", name)
            failed[name] = f"{type(e).__name__}: {e}"

    print_report(results, failed)
    overall = overall_scores(results)
    failures = check_thresholds(overall) if results else ["no case ran"]
    print(f"\nSaved to {save_report(results, failed, overall)}")
    if failures or failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
