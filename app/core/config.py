"""Application settings, read once from environment variables and .env.

Every tunable (models, paths, limits) lives here so no other module calls os.getenv.
Relative paths resolve against the project root, so the app works from any CWD.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # ---- LLM ----
    gemini_api_key: SecretStr
    gemini_model: str = "gemini-2.5-flash"
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
    match_threshold: float = Field(70, ge=0, le=100)  # skill match % for the strong-match route
    max_revisions: int = Field(3, ge=0)  # reviewer revisions before the report is written anyway

    # ---- Guideline search ----
    guideline_top_k: int = Field(4, gt=0)  # chunks returned per search
    guideline_min_score: float = Field(0.6, ge=0, le=1)  # cosine similarity cut-off

    # ---- Limits ----
    max_upload_mb: int = Field(10, gt=0)
    # Text extraction from an uploaded PDF runs in a child process killed after this long, so a
    # crafted PDF cannot hold a worker for good
    pdf_timeout_seconds: float = Field(30, gt=0)

    # ---- HTTP API ----
    # Browser origins allowed to call the API, comma-separated in the env. Empty = no CORS.
    # The Streamlit UI calls the API server-side, so it does not need an entry here.
    cors_origins: Annotated[list[str], NoDecode] = []
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = Field(60, gt=0)  # any /screenings request, per client IP
    workflow_rate_limit_per_minute: int = Field(5, gt=0)  # requests that run the LLM workflow

    log_level: str = "INFO"

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

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_request_bytes(self) -> int:
        # A resume and a JD file at the upload limit, plus room for the form fields
        return 2 * self.max_upload_bytes + 64 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
