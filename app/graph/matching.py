"""Deterministic scoring of a resume against a job: skill matching and experience months.

Plain Python, no LLM, so results are exact, repeatable and unit-tested.
"""

import re

# Simple normalization so "Postgres" == "PostgreSQL", "NodeJS" == "Node.js"
ALIASES = {
    "postgres": "postgresql",
    "nodejs": "node.js",
    "node": "node.js",
    "js": "javascript",
    "ts": "typescript",
    "k8s": "kubernetes",
    "retrieval augmented generation": "rag",
    "amazon web services": "aws",
    "golang": "go",
    "rest": "rest api",
    "rest apis": "rest api",
    "restful apis": "rest api",
    "rest api design": "rest api",
    "github actions": "ci/cd",
    "gitlab ci": "ci/cd",
    "jenkins": "ci/cd",
    "ci/cd pipelines": "ci/cd",
}


def normalize(skill: str) -> str:
    s = " ".join(skill.lower().split())
    return ALIASES.get(s, s)


def skill_variants(skill: str) -> set[str]:
    main, _, inner = skill.partition("(")
    parts = [main, *inner.rstrip(")").split(",")]
    return {normalize(p) for p in parts if p.strip()}


def is_met(required: str, candidate_skills: set[str]) -> bool:
    # "Kafka or RabbitMQ" is met by either one
    alternatives = re.split(r"\s+or\s+", required, flags=re.IGNORECASE)
    return any(skill_variants(alt) & candidate_skills for alt in alternatives)


def month_index(value: str | None) -> int | None:
    # "2023-07" -> months since year 0; tolerates "2023" or "2023-7"
    m = re.match(r"\s*(\d{4})(?:-(\d{1,2}))?", value or "")
    if not m:
        return None
    return int(m.group(1)) * 12 + int(m.group(2) or 1) - 1


def role_months(role: dict, today: int) -> set[int]:
    start = month_index(role.get("start_date"))
    end = today if role.get("is_current") else month_index(role.get("end_date"))
    if start is None or end is None or end < start:
        return set()
    return set(range(start, end + 1))  # inclusive: Jul-Jul counts as 1 month
