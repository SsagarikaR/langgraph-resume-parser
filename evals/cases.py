"""Labelled evaluation cases: one JSON file per resume + JD pair in evals/cases/."""

from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from app.core.config import BASE_DIR
from app.graph.matching import month_index, role_months

CASES_DIR = Path(__file__).parent / "cases"


class Role(BaseModel):
    start: str = Field(description="YYYY-MM")
    end: str | None = Field(None, description="YYYY-MM, or null for a current role")

    @field_validator("start", "end")
    @classmethod
    def _month(cls, value: str | None) -> str | None:
        if value is not None and month_index(value) is None:
            raise ValueError(f"expected YYYY-MM, got {value!r}")
        return value


class Expected(BaseModel):
    # "X or Y" accepts either spelling, e.g. "Lambda or AWS Lambda"
    resume_skills: list[str]
    # Judgement calls (soft skills, techniques): extracting them is neither right nor wrong
    resume_optional_skills: list[str] = []
    experience: list[Role]
    jd_required_skills: list[str]
    jd_optional_skills: list[str] = []
    jd_required_years: float | None = Field(description="null when the JD does not state it")

    def experience_months(self, today: int) -> int:
        """Total months, counted the same way as the app: inclusive, overlaps once."""
        months: set[int] = set()
        for role in self.experience:
            role_dict = {"start_date": role.start, "end_date": role.end, "is_current": role.end is None}
            months |= role_months(role_dict, today)
        return len(months)


class Case(BaseModel):
    name: str
    resume: Path  # relative to the project root
    jd: Path
    expected: Expected

    @field_validator("resume", "jd")
    @classmethod
    def _exists(cls, value: Path) -> Path:
        path = value if value.is_absolute() else BASE_DIR / value
        if not path.is_file():
            raise ValueError(f"file not found: {path}")
        return path


def load_cases(name_filter: str | None = None) -> dict[str, Case]:
    """All cases by file stem, optionally only those whose stem contains `name_filter`."""
    return {
        path.stem: Case.model_validate_json(path.read_text())
        for path in sorted(CASES_DIR.glob("*.json"))
        if not name_filter or name_filter in path.stem
    }
