# Security

This service processes resumes, which are personal data. This page says how to report a
vulnerability and what everyone who works on the project or runs it is expected to do. The
technical design is in [docs/threat-model.md](docs/threat-model.md); recovery and incident runbooks
are in [docs/operations.md](docs/operations.md).

## Reporting a vulnerability

Report it privately, never in a public issue or pull request: use GitHub's **Report a vulnerability**
(Security tab → Advisories) on this repository, or email the maintainer listed on the repository
profile. Include what you found, how to reproduce it and what it exposes.

- Acknowledgement within 3 working days; an assessment and a fix plan within 10.
- A fix for a critical or high issue is released before the details are made public.
- If candidate data may have been exposed, the incident steps in
  [docs/operations.md](docs/operations.md#incident-a-leaked-key-or-a-suspected-breach) start at once.

Only the latest commit on `main` (and the image CI published from it) is supported.

## Secure development

- **No secrets in the repository.** `.env` is git-ignored; `.env.example` holds placeholders only.
  The pre-commit hooks run gitleaks and detect-private-key on every commit: install them with
  `pre-commit install`; CI scans the whole git history with gitleaks too, since hooks can be
  skipped. Only CI's placeholder credentials are allowlisted, in `.gitleaks.toml`. A secret that reaches a commit is treated as leaked and rotated, even if the
  commit was never pushed.
- **In production, prefer a cloud secrets manager to `.env` on the host.** `scripts/fetch_secrets.sh`
  fetches from AWS Secrets Manager, GCP Secret Manager or Vault into `/run/secrets/<name>`, which
  the app already reads ahead of the environment (see `app/core/config.py`). Details:
  [docs/operations.md](docs/operations.md#secrets).
- **No real personal data in the repository, tests, issues or chats.** Test with the synthetic
  resumes in `data/resumes/` and `evals/fixtures/` (`example.com` addresses). A bug report that needs
  a real resume to reproduce gets an anonymised one.
- **Dependencies** are hash-locked (`requirements/`); add or upgrade them only through
  `scripts/lock.sh`, and read the changelog of anything security-relevant. CI fails on known fixable
  vulnerabilities; an accepted one goes in `.trivyignore` with a reason and an expiry.
- **Review** every change before it reaches `main`, and treat any change to `app/api/auth.py`,
  `app/api/middleware.py`, `app/core/prompts.py`, `app/utils/pdf.py`, the Dockerfile, compose files
  or CI as security-relevant: it needs a second reader and a test of the protection it touches.
- **Uploads and LLM output are untrusted.** New prompts wrap uploaded text with `as_document()` and
  carry the untrusted-data rule; new UI text from uploads or the LLM goes through `md()`.

## Developer and test machines

Anyone who runs the app with resumes on their own machine:

- Uses full-disk encryption, a screen lock of 5 minutes or less, and a supported, updated OS.
- Keeps `.env` (it holds the Gemini key) readable by their user only (`chmod 600 .env`), and never
  copies it into chat, tickets or screenshots.
- Uses a Gemini key from a development project with a low quota, never the production key.
- Uses synthetic resumes. If a real one was needed (a bug only it shows), runs
  `scripts/wipe_local_data.sh` with the API stopped as soon as the work is done, and deletes the
  original from Downloads and any chat it arrived through.
- Does not run the local API with `ALLOW_OPEN_API=true` on a network others can reach: bind it to
  localhost (`uvicorn app.api:app --host 127.0.0.1`, the default).
- Runs `scripts/wipe_local_data.sh` before the machine is handed over, sent for repair or retired.

Shared test machines and CI runners hold no personal data and no production secrets: CI uses
placeholder keys, its runners are discarded after each job, and the live evals use a separate
`GEMINI_EVAL_API_KEY` in their own GitHub environment.

## Secure remote work

- Reach production only over SSH with keys (no passwords) protected by a passphrase or a hardware
  key, and only from your own managed machine, never a shared or public computer.
- Turn on MFA for GitHub, Google (Gemini / AI Studio) and the hosting provider.
- Do not work with candidate data on public Wi-Fi without a VPN, or where others can see the screen.
- Share candidate data only through the application itself (the reviewer's own login): never by
  email, chat or a file-sharing link, and never export a report to a personal device.
- Lost or stolen device: report it the same day, so its keys and sessions can be revoked
  ([runbook](docs/operations.md#incident-a-leaked-key-or-a-suspected-breach)).

## Security awareness

Everyone who joins the project, as a developer or as a reviewer using the UI, reads this page and the
threat model before they get access, and again once a year or after an incident. The points that
matter most for this service:

- A resume can contain instructions aimed at the AI ("ignore previous instructions…"). The workflow
  is built to ignore them, but a reviewer who sees a strange report reports it.
- Reviewers decide; the tool only suggests. Do not approve questions or reject a candidate on the
  match percentage alone (see [docs/compliance.md](docs/compliance.md)).
- Phishing for the UI login or an API key is the most likely way in: the app never asks for a
  password by email or chat.
