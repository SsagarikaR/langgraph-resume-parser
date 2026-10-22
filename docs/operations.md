# Operations

Environments, recovery objectives, backups and the runbooks for the incidents this service can
have. The deployment is one host running the compose stack (`docker-compose.yml`).

## Environments

| Environment | What runs | Data | Gemini key |
|-------------|-----------|------|------------|
| Development | `uvicorn` + `streamlit` on a developer machine | Synthetic resumes only (`data/resumes/`) | Personal dev project, low quota |
| CI | Tests (fake LLM), image builds, the full compose stack, backup/restore drill | None (placeholders) | None; live evals use `GEMINI_EVAL_API_KEY` in the `eval` environment |
| Staging | The same signed images as production, on a separate host or directory with its own `.env` | Synthetic resumes only | Staging project, low quota |
| Production | Signed images deployed by digest (`scripts/deploy.sh`) | Real candidates | Production project, paid tier |

Every release goes to staging first: deploy the same digests there with `scripts/deploy.sh`, run a
screening with a synthetic resume end to end (start, review, approve), then deploy those digests to
production. Staging and production never share keys, volumes or backups.

## Recovery objectives

| | Target | How it is met |
|---|---|---|
| **RPO** (data that may be lost) | 24 hours | Nightly `scripts/backup.sh` from cron; a screening started since the last backup must be re-run |
| **RTO** (time to restore service) | 4 hours | Rebuild a host from the repository, deploy the signed images, `scripts/restore.sh` the latest backup |
| Backup retention | `KEEP_DAYS` (30) | Matches `RETENTION_DAYS`, so erasure completes within 30 days |

Screenings are short-lived (they are reviewed within days), so a 24-hour RPO loses at most a day of
work that can be redone from the original resumes, which the applicant system still holds.

## Backups

- `BACKUP_AGE_RECIPIENT=age1... scripts/backup.sh /srv/backups/screener` nightly from cron. Copy the
  encrypted files off the host (object storage with versioning). The age private key is held off
  the host by two operators.
- **Restore drill**: CI backs up and restores the database on every run, and checks that a damaged
  backup is refused. Once a quarter, restore the latest real backup on the staging host
  (`AGE_IDENTITY=... scripts/restore.sh <file>`), check `/api/ready` and that a recent screening
  opens, then erase it (`docker compose down -v`). Record the date and the time it took.
- Qdrant needs no backup: the index is rebuilt from `data/knowledge_base/` on first use.

## Runbooks

### Gemini outage or quota exhausted

Symptoms: runs end `failed`; `screening_runs_total{outcome="failed"}` rises; logs show provider
errors.
1. With `GEMINI_FALLBACK_MODEL` set, calls already fall back to it. If not, set it to a model that
   has passed the evals and restart the API.
2. Failed screenings keep their completed steps: once the provider is back, reviewers press
   **Retry** (or `POST /screenings/{id}/continue`). Nothing needs re-uploading.

### Daily token budget reached

Symptoms: new runs get `503` with `Retry-After`. Check `llm_tokens_total`. If the use is legitimate,
raise `DAILY_TOKEN_BUDGET` and restart; if not, find the key in the request log and rotate it
(below). Note that a restart also clears the budget's count.

### Database corrupt or lost

Symptoms: `/ready` reports `database: unavailable`; the API fails to start.
1. `docker compose stop api`; keep the damaged volume for analysis (`docker volume` copy).
2. `AGE_IDENTITY=... scripts/restore.sh <latest backup>`: it refuses a damaged backup, so try the
   previous one if it does.
3. Tell reviewers that screenings started after the backup time must be re-run.

### Host lost

Provision a host, clone the repository at the deployed commit, restore `.env` and secrets from the
secret store, `scripts/deploy.sh` the same digests, then `scripts/restore.sh` the latest backup.

### Incident: a leaked key or a suspected breach

1. **Contain**: remove the key from `API_KEYS` (or the password from `UI_USERS`) and restart; for the
   Gemini key, revoke it in Google AI Studio and deploy a new one. For a lost device, revoke its SSH
   keys and the user's sessions.
2. **Assess**: the request log names the caller of every request (`caller:` on each line); list what
   the key read or deleted in the exposure window.
3. **Notify**: if candidate data was exposed, the controller's DPO decides on notifying the
   authority (GDPR: within 72 hours) and the candidates.
4. **Recover and learn**: issue new keys with an expiry (`API_KEY_EXPIRES`), and record the cause and
   the fix.

### Routine key rotation

Every key has an expiry (`API_KEY_EXPIRES`, at most 12 months). Before it: add the new key under a new
name, move the client to it, then remove the old one. CI keys rotate with each CI secret change.

## Monitoring

`docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d` starts Prometheus
(scraping `api:8000/metrics` on the compose network; config and alert rules in `deploy/prometheus/`)
and Grafana (`deploy/grafana/`, provisioned with a "Resume screener" dashboard and the Prometheus
datasource). See the README's Monitoring section for ports and access.

Alerts already defined (`deploy/prometheus/alerts.yml`): the API unreachable; a high 5xx share;
repeated failed screening runs; run slots saturated at `MAX_CONCURRENT_RUNS` for 10 minutes; the
daily token budget (`DAILY_TOKEN_BUDGET`) 80% spent. None of them notify anyone yet — add an
Alertmanager service with a receiver (email, Slack, PagerDuty) for that.

## Secrets

In production, prefer a cloud secrets manager over `.env` on the host: `scripts/fetch_secrets.sh`
fetches from AWS Secrets Manager, GCP Secret Manager or HashiCorp Vault and writes each one to
`/run/secrets/<setting_name>`, which `app/core/config.py` already reads ahead of the environment.
Run it before the container starts (a host step, an init container, or a systemd `ExecStartPre`),
then mount that directory read-only into the container in place of (or alongside) `env_file: .env`.
Rotate by fetching the new value and restarting the API; nothing caches the old one past that.

## Known limits

- One API worker: the SQLite checkpointer, rate limits, run slots and the token budget are per
  process and in memory, so a restart resets the limits and budget. Scaling out needs LangGraph's
  Postgres checkpointer and a shared store (e.g. Redis) behind the same interfaces.
- No OCR: scanned PDFs are rejected with a clear message.
