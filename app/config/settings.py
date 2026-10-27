"""Application settings, read once from environment variables, .env and Docker secrets.

Every tunable (models, paths, limits) lives here so no other module calls os.getenv.
"""

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
KEY_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
# An API key stored as its SHA-256 instead of in plain text: sha256:<64 hex digits>
KEY_HASH_PREFIX = "sha256:"
KEY_HASH = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
SECRETS_DIR = Path("/run/secrets")
MIN_PLAINTEXT_KEY_LENGTH = 32  # matches secrets.token_urlsafe(32), the documented way to generate one


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        extra="ignore",
        secrets_dir=SECRETS_DIR if SECRETS_DIR.is_dir() else None,  # reads from /run/secrets when present
    )

    # ---- LLM ----
    gemini_api_key: SecretStr
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_fallback_model: str | None = None  # optional backup model used only when the main model fails
    embedding_model: str = "gemini-embedding-001"
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 3

    # ---- Vector store (local mode in qdrant_path unless qdrant_url is set) ----
    qdrant_url: str | None = None
    qdrant_api_key: SecretStr | None = None
    qdrant_path: Path = Path("qdrant_data")

    # ---- Storage ----
    checkpoint_db: Path = Path("checkpoints.db")
    reports_dir: Path = Path("reports")
    uploads_dir: Path = Path("data/uploads")
    knowledge_base_dir: Path = Path("data/knowledge_base")

    # ---- Screening rules ----
    match_threshold: float = Field(70, ge=0, le=100)  # skill match % that counts as a strong match
    experience_tolerance_years: float = Field(0.5, ge=0)  # years of shortfall still accepted (4.6 yrs meets "5+")
    max_revisions: int = Field(3, ge=0)  # max reviewer rounds before the report is written anyway
    llm_node_attempts: int = Field(2, ge=1)  # retries per LLM step, including bad schema responses

    # ---- Guideline search ----
    guideline_top_k: int = Field(4, gt=0)  # chunks returned per search
    guideline_min_score: float = Field(0.6, ge=0, le=1)  # cosine similarity cut-off
    guideline_relative_margin: float = Field(0.15, ge=0, le=1)  # drop hits this far below the best match

    # ---- Data retention (resumes are personal data) ----
    retention_days: int = Field(30, ge=0)  # erase screenings idle this long; 0 = keep forever
    purge_interval_minutes: int = Field(60, gt=0)  # how often the API checks for expired screenings

    # ---- Limits ----
    max_upload_mb: int = Field(10, gt=0)
    max_document_chars: int = Field(50_000, gt=0)  # max resume/JD text length sent to the LLM
    pdf_timeout_seconds: float = Field(30, gt=0)  # seconds before the PDF reader child process is killed

    # ---- HTTP API ----
    api_keys: Annotated[dict[str, SecretStr], NoDecode] = {}  # name:key pairs sent in X-API-Key header
    api_key: SecretStr | None = None  # single legacy key, treated as name "default"
    allow_open_api: bool = False  # skip auth entirely (local dev only)
    api_key_expires: Annotated[dict[str, date], NoDecode] = {}  # name:YYYY-MM-DD expiry per key
    audit_keys: Annotated[list[str], NoDecode] = []  # read-only keys that see all screenings
    acting_user_keys: Annotated[list[str], NoDecode] = ["default"]  # keys allowed to send X-Acting-User
    api_prefix: str = "/api/v1"  # version prefix for all /screenings routes
    api_docs: bool | None = None  # serve /docs; None = auto (on when no keys configured)
    cors_origins: Annotated[list[str], NoDecode] = []  # browser origins allowed to call the API
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = Field(60, gt=0)  # per-person request cap
    workflow_rate_limit_per_minute: int = Field(5, gt=0)  # per-person workflow cap
    key_rate_limit_per_minute: int = Field(600, gt=0)  # per-API-key request cap (all its users combined)
    key_workflow_rate_limit_per_minute: int = Field(30, gt=0)  # per-API-key workflow cap
    key_daily_workflow_limit: int = Field(500, ge=0)  # max workflow runs per key per day; 0 = no limit
    daily_token_budget: int = Field(5_000_000, ge=0)  # server-wide LLM token cap per 24 h; 0 = no limit
    max_concurrent_runs: int = Field(8, gt=0)  # max workflows running at once server-wide
    metrics_enabled: bool = True  # expose GET /metrics for Prometheus

    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"  # json = structured logs for log collectors

    @field_validator("qdrant_path", "checkpoint_db", "reports_dir", "uploads_dir", "knowledge_base_dir")
    @classmethod
    def _resolve_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else BASE_DIR / value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            value = value.split(",")
        return [origin.strip().rstrip("/") for origin in value if origin.strip()]

    @field_validator("acting_user_keys", "audit_keys", mode="before")
    @classmethod
    def _split_names(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            value = value.split(",")
        return [name.strip() for name in value if name.strip()]

    @field_validator("api_docs", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return None if value == "" else value  # API_DOCS= in .env means "decide automatically"

    @field_validator("api_keys", mode="before")
    @classmethod
    def _parse_api_keys(cls, value: str | dict) -> dict:
        if not isinstance(value, str):
            return value
        keys: dict[str, str] = {}
        for pair in filter(None, (p.strip() for p in value.split(","))):
            name, sep, key = pair.partition(":")
            name, key = name.strip(), key.strip()
            if not sep or not key or not KEY_NAME.match(name):
                raise ValueError("API_KEYS must be comma-separated name:key pairs; names use letters, digits, . _ -")
            if name in keys or key in keys.values():
                raise ValueError(f"API_KEYS repeats the name or the key of '{name}': each must be unique")
            if key.startswith(KEY_HASH_PREFIX):
                if not KEY_HASH.match(key):
                    raise ValueError(f"API_KEYS: the hashed key of '{name}' must be sha256: and 64 hex digits")
            elif len(key) < MIN_PLAINTEXT_KEY_LENGTH:
                raise ValueError(
                    f"API_KEYS: the key for '{name}' is too short ({len(key)} chars); "
                    f'use at least {MIN_PLAINTEXT_KEY_LENGTH}, e.g. python -c "import secrets; '
                    f'print(secrets.token_urlsafe(32))"'
                )
            keys[name] = key
        return keys

    @field_validator("api_key_expires", mode="before")
    @classmethod
    def _parse_expiry(cls, value: str | dict) -> dict:
        if not isinstance(value, str):
            return value
        expires: dict[str, str] = {}
        for pair in filter(None, (p.strip() for p in value.split(","))):
            name, sep, day = (part.strip() for part in pair.partition(":"))
            if not sep or not KEY_NAME.match(name) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                raise ValueError("API_KEY_EXPIRES must be comma-separated name:YYYY-MM-DD pairs")
            expires[name] = day
        return expires

    @model_validator(mode="after")
    def _roles_name_known_keys(self):
        # Catch typos: a name in AUDIT_KEYS or API_KEY_EXPIRES that doesn't match any configured key
        known = set(self.credentials)
        for setting, names in (("API_KEY_EXPIRES", set(self.api_key_expires)), ("AUDIT_KEYS", set(self.audit_keys))):
            if known and names - known:
                raise ValueError(f"{setting} names keys that are not configured: {sorted(names - known)}")
        if set(self.audit_keys) & set(self.acting_user_keys):
            raise ValueError("A key cannot be in both AUDIT_KEYS and ACTING_USER_KEYS: audit keys act for nobody")
        return self

    @property
    def credentials(self) -> dict[str, str]:
        """Every accepted API key, by the name that identifies its caller."""
        keys = {name: key.get_secret_value() for name, key in self.api_keys.items()}
        if self.api_key and self.api_key.get_secret_value():
            keys.setdefault("default", self.api_key.get_secret_value())
        return keys

    @property
    def docs_enabled(self) -> bool:
        return self.api_docs if self.api_docs is not None else not self.credentials

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_request_bytes(self) -> int:
        # resume + JD at the upload limit, plus form field overhead
        return 2 * self.max_upload_bytes + 64 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
