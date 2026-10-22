# Compliance

This service uses AI to help screen job candidates. Under the EU AI Act, AI used to evaluate or
filter job applicants is a **high-risk system** (Annex III, point 4). GDPR Article 22 restricts
decisions about people based solely on automated processing, and India's Digital Personal Data
Protection Act 2023 applies to candidates' data processed in India. This page records how the
service is designed to meet those obligations and what the deploying organisation must still do.
It is an engineering record, not legal advice: have it reviewed by your data protection officer or
counsel before screening real candidates.

## Roles

- **Controller**: the hiring organisation that deploys the service and decides to use it.
- **Processors**: the hosting provider; Google (Gemini API), which receives resume and JD text;
  LangSmith, only if tracing is turned on (keep it off for real candidates).

## Lawful basis and purpose

- Purpose: preparing a human interview for a role the candidate applied to. Nothing else (no
  profiling across roles, no marketing, no model training).
- Basis: steps at the candidate's request before entering a contract, or the controller's legitimate
  interest (record the balancing test). Not consent: an applicant cannot freely refuse.

## Human oversight (GDPR Art. 22, AI Act Art. 14)

The service never makes a decision about a candidate:

- Every screening pauses for a person (`human_review`) before a report exists. The reviewer can send
  questions back with feedback, and correct the skill match at the `match_requirements` breakpoint.
- The match percentage and route only choose which analysis runs; they reject no one. The report
  says whether a human approved it (`approved_by_human`) and who did.
- Skills the model extracted but the resume never mentions are shown to the reviewer
  (`unverified_skills`) and left out of the score.

The controller must make sure reviewers have the authority and training to disagree with the tool,
and must not use the match percentage as an automatic cut-off.

## Fairness and bias

- Matching is deterministic code on skill names and dates: it never sees the name, gender, age,
  photo or address.
- The LLM extraction does see the whole resume. The evals include **counterfactual pairs**: the same
  resume with only the name, gender cues or age changed (`evals/cases/08_*`, `09_*`). The run fails
  unless each pair gets the same route, the same experience and the same skills (≥ 0.9 overlap).
  Run them on every model or prompt change, and add pairs for the groups relevant to your hiring.
- Known gaps, to monitor: experience is counted in calendar months, so career breaks (often parental
  or caring leave) lower the experience total; non-English resumes and scanned PDFs (no OCR) are not
  supported; the skill alias table is English-centric.
- Review outcomes by group periodically (where you lawfully hold that data) and record the results.

## Transparency: notice to candidates

Tell candidates before their resume is screened, for example in the job posting or the application
confirmation:

> We use an AI-assisted tool to compare your resume with the job's requirements and to prepare
> interview questions. A member of our hiring team reviews every result; no decision is made
> automatically. Your resume text is processed by Google's Gemini service for this purpose and is
> deleted within 30 days of the last activity on your application. You can ask us for a copy of the
> analysis, to correct it, or to delete it at any time: contact <privacy contact>.

## Data subject rights

| Right | How |
|-------|-----|
| Access / copy | An audit key (`AUDIT_KEYS`) reads the screening: `GET /screenings/{id}`; the report is in `final_report` |
| Rectification | Reviewer corrects the skill match at the breakpoint, or deletes and re-screens |
| Erasure | `DELETE /screenings/{id}`; backups expire within `KEEP_DAYS` (see [operations.md](operations.md)) |
| Objection / human review | Every result is already human-reviewed; on objection, delete the screening and assess manually |

Find a candidate's screening by the screening id recorded in your applicant system; record that id
when you start the screening.

## Retention and minimisation

`RETENTION_DAYS` (default 30) erases idle screenings, backups older than `KEEP_DAYS` (default 30) are
deleted, the uploaded PDF is deleted once its text is extracted, and logs carry no resume content.
Set both to the period stated in the candidate notice. Details: README, "Data privacy".

## DPIA outline

A data protection impact assessment is required before use (systematic evaluation of people with new
technology). The sections and where the material is:

1. Description of processing: README (workflow), this page (purpose, roles).
2. Necessity and proportionality: purpose limitation and minimisation above.
3. Risks to candidates: [threat-model.md](threat-model.md) (security), "Fairness and bias" above.
4. Measures: human oversight, counterfactual evals, access control (per-reviewer ownership, audit
   keys, key expiry), retention, encryption of backups, processor terms.
5. Sign-off: DPO / counsel, with a review date (at least yearly, and on any model change).

## Before going live: checklist

- [ ] DPIA completed and signed off.
- [ ] Gemini used under terms that do not train on prompts (paid tier) and cover personal data;
      transfer mechanism recorded if data leaves the candidate's jurisdiction.
- [ ] Candidate notice published; privacy contact set.
- [ ] `UI_USERS` (one login per reviewer) rather than a shared login, so reviews are attributable.
- [ ] `RETENTION_DAYS` and `KEEP_DAYS` match the notice.
- [ ] Evals, including the counterfactual pairs, pass on the configured model.
- [ ] Reviewers briefed: the tool suggests, they decide ([SECURITY.md](../SECURITY.md#security-awareness)).
- [ ] Record of processing activities updated.
