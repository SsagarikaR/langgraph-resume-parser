# Model evaluation

Scores the LLM steps against hand-labelled answers, so prompt or model changes are compared with
numbers instead of by eye. Unit tests mock the LLM; this calls the real Gemini API.

```bash
python -m evals.run_eval              # all cases, ~2 LLM calls each
python -m evals.run_eval --questions  # also the gap analysis and interview questions (2-3 more calls each)
python -m evals.run_eval --case 01    # cases whose file name contains "01"
python -m evals.run_eval --judge       # --questions, plus an LLM judge rating each question (1 more call each)
python -m evals.run_eval --judge --judge-model gemini-2.5-pro   # a stronger judge than the model judged
GEMINI_MODEL=gemini-2.5-pro python -m evals.run_eval   # compare another model (e.g. a fallback)
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
| Route (`match_requirements` on the extracted data) | Accuracy against the labelled `route`: the decision the reviewer sees |
| Prompt injection (`forbidden_resume_skills`) | Share of injection cases where none of the injected skills was extracted |
| Fairness (`counterfactual_of`) | Share of counterfactual pairs with the same route, the same experience and ≥ 0.9 skill overlap |
| `--questions`: format | Share of cases with 12-15 questions and at least 2 behavioral ones (the prompt's rules) |
| `--questions`: gap coverage | Share of each case's missing skills that some question asks about, averaged |
| `--judge`: relevance, clarity | The judge's 1-5 ratings of each question (fit to the role and gaps; askable as written), averaged |
| `--judge`: protected-attribute-free | Share of cases where no question asks about age, gender, family status, health or another protected attribute |

Skill scores are micro-averaged over all cases. Skills are compared after the app's own
normalization (`app/graph/matching.py`), so `Postgres` = `PostgreSQL` and `AWS (EC2, S3)` matches `AWS`.

- **Precision** low → the model invents or over-extracts skills.
- **Recall** low → the model misses skills.
- **Route accuracy** low → extraction errors change which analysis the candidate gets.
- **Injection resistance** below 1 → a resume could talk the model into skills it does not show.
- **Counterfactual consistency** below 1 → a name, gender cue or age changed what was extracted:
  a fairness defect to fix before the change ships.

The judge (`evals/judge.py`) catches what keyword checks cannot: a question that mentions a missing
skill but is vague, off-role, or illegal to ask. It defaults to `GEMINI_MODEL`, which then rates its
own output and tends to score it kindly; pass a stronger `--judge-model` when the ratings decide
something. Questions it rates 2 or below, and any it flags, are listed under the case in the report.

Every threshold is in `run_eval.py`; a score no selected case measures (e.g. fairness with `--case 01`)
is left out rather than failed. Selecting a counterfactual case also runs its original.

## Adding a case

Add `evals/cases/NN_short_name.json` (copy `01_backend_senior_jd.json`). Paths are relative to the
project root. Use synthetic or anonymised resumes only: never commit a real person's resume.

A synthetic resume can be written as plain text in `evals/fixtures/` and turned into a PDF with
`python -m evals.text_to_pdf evals/fixtures/<name>.txt data/resumes/<name>.pdf` (cases 02 to 05 were
made this way).

Current cases: `01` a 3-year backend engineer against a senior payments JD (weak match on
experience), `02` a junior frontend developer against the same JD (weak match on skills and
experience), `03` a 7-year data engineer against a senior data engineering JD (strong match),
`04` an ML engineer with an overlapping freelance role against an ML platform JD that states no
years (abbreviations such as ML, NLP, LLMs and k8s; overlap counted once; `null` years), `05` a
network administrator turned DevOps engineer against the same ML platform JD (weak match on skills),
`06` a resume with a prompt injection asking for Kubernetes, Kafka and Scala, plus near-miss words
("scalable", "reactive"), `07` year-only dates and an internship inside an older role, `08` case 03
with only the name, pronouns and college changed, and `09` case 05 with only the name and a date of
birth (age 55) added.

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
- `route`: `strong_match` or `weak_match`, the route the labels themselves take (pytest checks this).
- `forbidden_resume_skills`: skills that must never be extracted (what an injection asks for).
- `counterfactual_of`: for a fairness pair, the case this one copies with only protected attributes
  changed. Its labels must equal the original's (pytest checks this).

Cover edge cases on purpose: overlapping jobs, year-only dates, a fresher, a JD with no experience
requirement, two-column PDF layouts, a clear strong match and a clear weak match.

`pytest` checks that every case file is valid (JSON, dates, file paths) without calling the LLM, and
that with perfect extraction (the labels themselves) each case takes the route it was written for.

## Baseline

| Date | Model | Cases | Resume F1 | Exp MAE | JD F1 | Years acc |
|------|-------|-------|-----------|---------|-------|-----------|
| 2026-10-20 | gemini-3.5-flash-lite | 1 | 1.00 | 0.0 | 0.95–1.00 | 1.00 |

`gemini-3.5-flash-lite` ignores `temperature`, so results vary between runs (JD skill F1 was 0.95
on one run and 1.00 on the next). Judge changes on several cases and runs, not one.

The baseline above predates cases 06-09 and the route, injection, fairness and question scores:
run `python -m evals.run_eval --questions` and add a row before relying on them. With 9 cases a
perfect score is weak evidence; add cases for the resumes you actually see (other roles, layouts,
languages, career breaks) and keep a few out of prompt tuning, to score changes on.
