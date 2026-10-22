# syntax=docker/dockerfile:1.7
# Two images from one file:
#   docker build --target api -t resume-screener-api .
#   docker build --target ui  -t resume-screener-ui  .
# The app runs from source in /app (not pip-installed) because settings resolve
# relative paths against the project root.

# Pinned by digest, so a rebuild gets the same base; Dependabot proposes updates
ARG PYTHON_IMAGE=python:3.12-slim@sha256:2259debdc6af85626058a15a7bf44ad6a68b8c98c9ae82a02cf5cdb92f096d47

# ---- Dependency builder: installs a hash-pinned lockfile into a venv, per image ----
FROM ${PYTHON_IMAGE} AS deps-base
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/venv/bin:$PATH
RUN python -m venv /opt/venv
WORKDIR /build
# --require-hashes: every package, dependencies included, must match the lockfile's hashes
# (generated from pyproject.toml by scripts/lock.sh), so a build installs exactly what was locked.

FROM deps-base AS api-deps
COPY requirements/api.txt .
RUN pip install --require-hashes --no-deps -r api.txt

FROM deps-base AS ui-deps
COPY requirements/ui.txt .
RUN pip install --require-hashes --no-deps -r ui.txt

# ---- Shared runtime base: non-root user, no build tools ----
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app

# ---- API image ----
FROM runtime AS api
COPY --from=api-deps /opt/venv /opt/venv
COPY app/ app/
COPY data/knowledge_base/ data/knowledge_base/
# Writable state (checkpoints, reports, uploads, local Qdrant) lives in a volume, not the image
ENV CHECKPOINT_DB=/var/lib/screener/checkpoints.db \
    REPORTS_DIR=/var/lib/screener/reports \
    UPLOADS_DIR=/var/lib/screener/uploads \
    QDRANT_PATH=/var/lib/screener/qdrant_data
RUN mkdir -p /var/lib/screener && chown app:app /var/lib/screener
VOLUME ["/var/lib/screener"]
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"]
# One worker: the SQLite checkpointer and in-memory rate limits are per process.
# Behind a reverse proxy, set FORWARDED_ALLOW_IPS (read by uvicorn) to the proxy's address so
# rate limits see real client IPs; docker-compose.yml does this for its proxy.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]

# ---- UI image (only Streamlit + requests; talks to the API over HTTP) ----
FROM runtime AS ui
COPY --from=ui-deps /opt/venv /opt/venv
COPY ui/ ui/
USER app
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=3)"]
CMD ["streamlit", "run", "ui/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
