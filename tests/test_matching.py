import pytest

from app.config.settings import get_settings
from app.graph.edges import route_after_matching, route_after_questions, route_after_review
from app.graph.matching import (
    candidate_skill_names,
    candidate_skills,
    is_met,
    mentioned,
    month_index,
    normalize,
    role_months,
    skill_variants,
    unverified_skills,
)
from app.graph.nodes import match_requirements
from app.utils.files import safe_filename


def test_normalize_applies_aliases_and_whitespace():
    assert normalize("  Postgres ") == "postgresql"
    assert normalize("K8s") == "kubernetes"
    assert normalize("FastAPI") == "fastapi"


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [
        ("React.js", "react"),
        ("ReactJS", "react"),
        ("Node.js", "node"),
        ("NodeJS", "node"),
        ("Python 3.11", "python"),
        ("Java 17+", "java"),
        ("Vue 3.x", "vue"),
        ("Amazon S3", "s3"),
        ("AWS Lambda", "lambda"),
        ("Apache Kafka", "kafka"),
        ("AWS", "aws"),  # a vendor name alone is kept
        ("JS", "javascript"),  # not stripped to nothing
        ("Google Cloud Platform", "gcp"),
        ("HTML5", "html"),
    ],
)
def test_normalize_handles_versions_suffixes_and_vendors(spelling, canonical):
    assert normalize(spelling) == canonical


def test_broad_requirements_are_met_by_a_specific_tool():
    assert is_met("SQL", {"postgresql"})
    assert is_met("Cloud", {"gcp"})
    assert is_met("Message queues", {"rabbitmq"})
    assert not is_met("PostgreSQL", {"sql"})  # the reverse never holds


def test_skill_variants_splits_groups():
    assert skill_variants("AWS (EC2, S3)") == {"aws", "ec2", "s3"}


def test_is_met_accepts_any_alternative():
    assert is_met("Kafka or RabbitMQ", {"rabbitmq"})
    assert not is_met("Kafka or RabbitMQ", {"redis"})


def testmonth_index_tolerates_formats():
    assert month_index("2023-07") == 2023 * 12 + 6
    assert month_index("2023") == 2023 * 12
    assert month_index(None) is None
    assert month_index("Present") is None


def testrole_months_inclusive_and_current():
    today = month_index("2024-01")
    assert len(role_months({"start_date": "2023-07", "end_date": "2023-07"}, today)) == 1
    assert len(role_months({"start_date": "2023-01", "is_current": True}, today)) == 13
    assert role_months({"start_date": "2024-05", "end_date": "2024-01"}, today) == set()


def test_match_requirements_uses_skills_and_project_tech():
    state = {
        "resume": {
            "skills": ["Python", "Postgres"],
            "projects": [{"technologies": ["Docker"]}],
            "total_experience_months": 30,
        },
        "job": {"required_skills": ["Python", "PostgreSQL", "Docker", "Kafka"], "required_experience_years": 3},
    }
    out = match_requirements(state)
    assert out["matching_skills"] == ["Python", "PostgreSQL", "Docker"]
    assert out["missing_skills"] == ["Kafka"]
    assert out["match_percentage"] == 75.0
    assert out["candidate_experience_years"] == 2.5
    assert out["experience_gap_years"] == 0.5


def test_match_requirements_with_no_required_skills():
    # Unreachable from the graph (analyze_job rejects such a job), kept safe for direct calls
    state = {"resume": {"skills": [], "projects": []}, "job": {"required_skills": []}}
    assert match_requirements(state)["match_percentage"] == 100.0


def test_routing():
    assert route_after_matching({"match_percentage": 80, "experience_gap_years": 0}) == "strong_match"
    assert route_after_matching({"match_percentage": 80, "experience_gap_years": 2}) == "weak_match"
    assert route_after_review({"approved": True}) == "approved"
    assert route_after_review({"approved": False}) == "revise"
    assert route_after_questions({}) == "review"  # the first set is always reviewed
    assert route_after_questions({"revision_count": get_settings().max_revisions}) == "finalize"


def test_routing_thresholds_come_from_settings(monkeypatch):
    monkeypatch.setenv("MATCH_THRESHOLD", "90")
    monkeypatch.setenv("MAX_REVISIONS", "1")
    monkeypatch.setenv("EXPERIENCE_TOLERANCE_YEARS", "1")
    get_settings.cache_clear()
    assert route_after_matching({"match_percentage": 80, "experience_gap_years": 0}) == "weak_match"
    assert route_after_matching({"match_percentage": 95, "experience_gap_years": 1}) == "strong_match"
    assert route_after_questions({"revision_count": 1}) == "finalize"


def test_first_questions_are_reviewed_even_with_no_revisions_allowed(monkeypatch):
    monkeypatch.setenv("MAX_REVISIONS", "0")
    get_settings.cache_clear()
    assert route_after_questions({"revision_count": 0}) == "review"
    assert route_after_questions({"revision_count": 1}) == "finalize"


def test_safe_filename_blocks_path_traversal():
    assert safe_filename("Jane Doe") == "jane_doe"
    assert safe_filename("../../etc/passwd") == "etc_passwd"
    assert "/" not in safe_filename("a/b\\c")
    assert safe_filename("???") == "candidate"


def test_a_category_is_met_by_its_tools_but_one_tool_never_meets_another():
    assert is_met("CI/CD", {"jenkins"})
    assert is_met("CI/CD pipelines", {"github actions"})
    assert not is_met("Jenkins", {"github actions"})
    assert not is_met("Jenkins", {"ci/cd"})  # knowing "CI/CD" in general is not knowing Jenkins


def test_a_grouped_requirement_needs_the_main_skill_or_every_part():
    assert is_met("AWS (EC2, S3)", {"aws"})
    assert is_met("AWS (EC2, S3)", {"ec2", "s3"})
    assert not is_met("AWS (EC2, S3)", {"s3"})  # one service is not AWS experience
    assert is_met("unit testing (pytest)", {"pytest"})


def test_tools_in_a_category_do_not_inflate_the_match():
    state = {
        "resume": {"skills": ["GitHub Actions"], "projects": [], "total_experience_months": 0},
        "job": {"required_skills": ["Jenkins", "CI/CD"]},
    }
    out = match_requirements(state)
    assert out["matching_skills"] == ["CI/CD"]
    assert out["missing_skills"] == ["Jenkins"]


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [
        ("Python programming", "python"),
        ("Go language", "go"),
        ("Rust programming language", "rust"),
        ("Spring Framework", "spring"),
        ("framework", "framework"),  # a generic word alone is kept
    ],
)
def test_normalize_drops_generic_words_after_a_name(spelling, canonical):
    assert normalize(spelling) == canonical


def test_a_cloud_product_on_the_resume_meets_its_platform():
    assert candidate_skill_names("AWS Lambda") == {"lambda", "aws"}
    assert is_met("AWS", candidate_skill_names("Amazon S3"))
    assert is_met("Azure", candidate_skill_names("Azure Functions"))
    assert is_met("GCP", candidate_skill_names("Google Cloud Run"))
    # The other way round, the platform does not mean every product
    assert not is_met("Lambda", candidate_skill_names("AWS"))
    # Not for vendors that are not platforms: Apache Kafka is no "Apache" skill
    assert candidate_skill_names("Apache Kafka") == {"kafka"}


@pytest.mark.parametrize(
    ("short", "long"), [("ML", "Machine Learning"), ("LLMs", "Large Language Model"), ("NLP", "nlp")]
)
def test_common_abbreviations_match_their_full_names(short, long):
    assert normalize(short) == normalize(long)


@pytest.mark.parametrize(
    ("skill", "text", "found"),
    [
        ("PostgreSQL", "built on Postgres and Redis", True),  # an alias spelling
        ("React", "frontends in ReactJS", True),
        ("Node.js", "NodeJS services", True),
        ("Machine Learning", "applied ML at scale", True),
        ("Spring Boot", "SpringBoot microservices", True),
        ("scikit-learn", "models in scikit learn", True),
        ("Spring Boot", "Spring-Boot services", True),
        ("Go", "going to good lengths", False),  # not inside other words
        # Nor inside a longer word once spaces and punctuation are dropped: an invented skill
        # must not pass as mentioned because of a common word that starts with it
        ("Scala", "built scalable APIs in Python", False),
        ("React", "reactive programming with RxJava", False),
        ("Spring", "lived in Springfield", False),
        ("Kafka", "kafkaesque bureaucracy", False),
        ("Kubernetes", "deployed with Docker", False),
    ],
)
def test_a_skill_is_found_in_the_resume_text_under_any_spelling(skill, text, found):
    assert mentioned(skill, " ".join(text.lower().split())) is found


def test_skills_the_resume_never_mentions_are_left_out_of_the_match():
    resume = {
        "skills": ["Python", "Kubernetes"],
        "projects": [{"name": "Ledger", "description": "", "technologies": ["Postgres", "Kafka"]}],
    }
    resume["unverified_skills"] = unverified_skills(resume, "Python developer. Ledger: billing on Postgres.")
    assert resume["unverified_skills"] == ["Kubernetes", "Kafka"]
    assert candidate_skills(resume) == {"python", "postgresql"}
