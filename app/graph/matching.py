"""Deterministic scoring of a resume against a job: skill matching and experience months.

Plain Python, no LLM, so results are exact, repeatable and unit-tested.
"""

import re
from collections import defaultdict

# Exact spellings mapped to one canonical name, so "Postgres" == "PostgreSQL"
ALIASES = {
    "postgres": "postgresql",
    "nodejs": "node",
    "node.js": "node",
    "js": "javascript",
    "ts": "typescript",
    "k8s": "kubernetes",
    "retrieval augmented generation": "rag",
    "amazon web services": "aws",
    "google cloud platform": "gcp",
    "google cloud": "gcp",
    "microsoft azure": "azure",
    "golang": "go",
    "html5": "html",
    "css3": "css",
    "mongo": "mongodb",
    "sklearn": "scikit-learn",
    "rest": "rest api",
    "rest apis": "rest api",
    "restful apis": "rest api",
    "restful api": "rest api",
    "rest api design": "rest api",
    "ci/cd pipelines": "ci/cd",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "genai": "generative ai",
    "gen ai": "generative ai",
    "nlp": "natural language processing",
    "llm": "large language models",
    "llms": "large language models",
    "large language model": "large language models",
    "oop": "object-oriented programming",
    "object oriented programming": "object-oriented programming",
    "cpp": "c++",
    "c sharp": "c#",
    "dotnet": ".net",
    "mssql": "sql server",
    "ms sql": "sql server",
    "microsoft sql server": "sql server",
    "elastic search": "elasticsearch",
    "tensor flow": "tensorflow",
    "py torch": "pytorch",
    "gh actions": "github actions",
    "github action": "github actions",
    "gitlab ci/cd": "gitlab ci",
    "rabbit mq": "rabbitmq",
    "dynamo db": "dynamodb",
}
# Canonical name → every spelling that maps to it, to find a skill in the resume text
SPELLINGS: dict[str, set[str]] = defaultdict(set)
for _spelling, _canonical in ALIASES.items():
    SPELLINGS[_canonical].add(_spelling)

# A trailing version: "Python 3", "Java 17+", "Vue 3.x", "Angular v15"
VERSION = re.compile(r"\s+v?\d+(?:\.\d+)*(?:\.x)?\+?$")
# A ".js"/"js" suffix on a library name: "React.js", "ReactJS", "D3.js" (but not "js" itself)
JS_SUFFIX = re.compile(r"(?<=[a-z0-9])\.?js$")
# Vendor names before a product: "Amazon S3" is S3, "Apache Kafka" is Kafka
VENDOR_PREFIXES = ("amazon ", "aws ", "apache ")
# Cloud vendors whose products show the platform itself: "AWS Lambda" experience meets "AWS"
CLOUD_VENDORS = {"aws ": "aws", "amazon ": "aws", "azure ": "azure", "google cloud ": "gcp", "gcp ": "gcp"}
# Generic words after a name: "Python programming", "Go language", "Spring framework"
GENERIC_SUFFIX = re.compile(r"\s+(?:programming language|programming|language|framework)$")

# A required category is met by any of its tools, but one tool never meets another:
# "CI/CD" is met by Jenkins, while "Jenkins" is not met by GitHub Actions
CATEGORIES = {
    "ci/cd": {"github actions", "gitlab ci", "jenkins", "circleci", "azure devops", "travis ci", "argo cd"},
    "sql": {"postgresql", "mysql", "sqlite", "sql server", "oracle", "mariadb"},
    "nosql": {"mongodb", "dynamodb", "cassandra", "couchdb", "redis"},
    "cloud": {"aws", "gcp", "azure"},
    "containers": {"docker", "podman", "kubernetes"},
    "message queues": {"kafka", "rabbitmq", "sqs", "nats", "pulsar"},
}


def normalize(skill: str) -> str:
    s = " ".join(skill.lower().split())
    s = ALIASES.get(s, s)
    s = GENERIC_SUFFIX.sub("", s)
    s = VERSION.sub("", s)
    s = JS_SUFFIX.sub("", s)
    for prefix in VENDOR_PREFIXES:
        if s.startswith(prefix) and len(s) > len(prefix):
            s = s[len(prefix) :]
            break
    return ALIASES.get(s, s)


def skill_variants(skill: str) -> set[str]:
    main, _, inner = skill.partition("(")
    parts = [main, *inner.rstrip(")").split(",")]
    return {normalize(p) for p in parts if p.strip()}


def candidate_skill_names(skill: str) -> set[str]:
    """The normalized names a skill on the resume counts as: its variants, plus the cloud platform
    of a vendor product ("AWS Lambda" also counts as AWS). Only for the candidate's side: on the
    job's side "AWS Lambda" does not ask for all of AWS."""
    names = skill_variants(skill)
    lowered = " ".join(skill.lower().split())
    for prefix, platform in CLOUD_VENDORS.items():
        if lowered.startswith(prefix) and len(lowered) > len(prefix):
            names.add(platform)
    return names


def has_skill(skill: str, candidate_skills: set[str]) -> bool:
    return skill in candidate_skills or bool(CATEGORIES.get(skill, set()) & candidate_skills)


def alternative_met(required: str, candidate_skills: set[str]) -> bool:
    # "AWS (EC2, S3)" needs AWS itself or every listed part, so S3 alone does not meet it
    main, _, inner = required.partition("(")
    if main.strip() and has_skill(normalize(main), candidate_skills):
        return True
    parts = [normalize(p) for p in inner.rstrip(")").split(",") if p.strip()]
    return bool(parts) and all(has_skill(p, candidate_skills) for p in parts)


def is_met(required: str, candidate_skills: set[str]) -> bool:
    # "Kafka or RabbitMQ" is met by either one
    alternatives = re.split(r"\s+or\s+", required, flags=re.IGNORECASE)
    return any(alternative_met(alt, candidate_skills) for alt in alternatives)


def candidate_skills(resume: dict) -> set[str]:
    """Normalized names of the candidate's skills: the skills section and project tech stacks,
    minus any the resume text does not mention (see unverified_skills)."""
    unverified = set(resume.get("unverified_skills", []))
    names: set[str] = set()
    for skill in [*resume["skills"], *(t for p in resume["projects"] for t in p["technologies"])]:
        if skill not in unverified:
            names |= candidate_skill_names(skill)
    return names


COMPACT_MAX_WORDS = 3  # "scikit learn" (2 words) and "ci / cd" (2) fit; longer names match as written


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", text)


def _compact_phrases(text: str) -> set[str]:
    """Every run of up to COMPACT_MAX_WORDS adjacent words, joined without spaces or punctuation.
    Whole words only, so "scalable" never yields "scala", nor "reactive" "react"."""
    words = re.findall(r"[a-z0-9+#]+", text)
    return {"".join(words[i : i + n]) for n in range(1, COMPACT_MAX_WORDS + 1) for i in range(len(words) - n + 1)}


def mentioned(skill: str, text: str) -> bool:
    """Whether `text` (lowercased, single-spaced) names `skill` under any of its spellings:
    "Postgres" for PostgreSQL, "ReactJS" for React, "ML" for Machine Learning."""
    spellings = {" ".join(skill.lower().split())}
    for name in skill_variants(skill):
        spellings |= {name, f"{name}js", f"{name}.js", *SPELLINGS.get(name, ())}
    for spelling in filter(None, spellings):
        if re.search(rf"(?<![a-z0-9]){re.escape(spelling)}(?![a-z0-9])", text):
            return True
    # "Spring Boot" for "SpringBoot", "scikit learn" for "scikit-learn": compared without spaces
    # and punctuation, as whole words, and only for names long enough not to be a common word
    phrases = _compact_phrases(text)
    return any(len(c) >= 5 and c in phrases for c in map(_compact, spellings))


def unverified_skills(resume: dict, resume_text: str) -> list[str]:
    """Skills the LLM extracted (skills section and project tech) that the resume text never
    mentions: possibly invented, so they are left out of the match and shown to the reviewer."""
    text = " ".join(resume_text.lower().split())
    listed = [*resume["skills"], *(t for p in resume["projects"] for t in p["technologies"])]
    return list(dict.fromkeys(skill for skill in listed if not mentioned(skill, text)))


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
