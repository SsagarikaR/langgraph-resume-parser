# Model evaluation

Scores the LLM steps against hand-labelled answers, so prompt or model changes are compared with
numbers instead of by eye. Unit tests mock the LLM; this calls the real Gemini API.

```bash
python -m evals.run_eval              # all cases, ~2 LLM calls each
python -m evals.run_eval --case 01    # cases whose file name contains "01"
GEMINI_MODEL=gemini-2.5-pro python -m evals.run_eval   # compare another model
```

Prints a report, saves it as JSON in `evals/results/` (git-ignored, includes the raw LLM output)
and exits with code 1 if an overall score misses its threshold in `run_eval.py`.

## Metrics

| Step | Metric |
|------|--------|
| `extract_resume` skills (skills section + project tech) | Precision, recall, F1 |
| `extract_resume` experience | MAE in months (dates → months computed by the app) |
| `analyze_job` required skills | Precision, recall, F1 |
| `analyze_job` required years | Accuracy (exact value, or `null` when the JD does not state it) |

Skill scores are micro-averaged over all cases. Skills are compared after the app's own
normalization (`app/graph/matching.py`), so `Postgres` = `PostgreSQL` and `AWS (EC2, S3)` matches `AWS`.

- **Precision** low → the model invents or over-extracts skills.
- **Recall** low → the model misses skills.

## Adding a case

Add `evals/cases/NN_short_name.json` (copy `01_backend_senior_jd.json`). Paths are relative to the
project root. Use synthetic or anonymised resumes only: never commit a real person's resume.

Labelling rules:

- `resume_skills`: every technical skill in the resume, from the skills section and from project
  tech stacks, split as written (`AWS (EC2, S3)` → `AWS`, `EC2`, `S3`).
- `jd_required_skills`: must-have technical skills only, not "nice to have". Keep real alternatives
  as one entry: `Kafka or RabbitMQ`.
- `X or Y` in a label accepts either spelling: `Lambda or AWS Lambda`.
- `*_optional_skills`: judgement calls (soft skills, techniques, sub-services). Extracting them is
  neither rewarded nor penalised.
- `experience`: one entry per role, dates as `YYYY-MM`, `end: null` for a current role. Year-only
  dates use `YYYY-01`, the same rule the extraction prompt follows.
- `jd_required_years`: the minimum (`5` for "5+ years", `3` for "3–5 years"), `null` if not stated.

Cover edge cases on purpose: overlapping jobs, year-only dates, a fresher, a JD with no experience
requirement, two-column PDF layouts, a clear strong match and a clear weak match.

`pytest` checks that every case file is valid (JSON, dates, file paths) without calling the LLM.

## Baseline

| Date | Model | Cases | Resume F1 | Exp MAE | JD F1 | Years acc |
|------|-------|-------|-----------|---------|-------|-----------|
| 2026-10-20 | gemini-3.5-flash-lite | 1 | 1.00 | 0.0 | 0.95–1.00 | 1.00 |

`gemini-3.5-flash-lite` ignores `temperature`, so results vary between runs (JD skill F1 was 0.95
on one run and 1.00 on the next). Judge changes on several cases and runs, not one.
