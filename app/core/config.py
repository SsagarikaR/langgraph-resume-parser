"""Application settings, read once from environment variables and .env.

Every tunable (models, paths, limits) lives here so no other module calls os.getenv.
Relative paths resolve against the project root, so the app works from any CWD.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # ---- Limits ----
    max_upload_mb: int = Field(10, gt=0)

    log_level: str = "INFO"

    @field_validator("qdrant_path", "checkpoint_db", "reports_dir", "uploads_dir", "knowledge_base_dir")
    @classmethod
    def _resolve_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else BASE_DIR / value

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
