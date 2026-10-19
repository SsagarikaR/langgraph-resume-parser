from app.graph.edges import MAX_REVISIONS, route_after_matching, route_after_review
from app.graph.matching import is_met, month_index, normalize, role_months, skill_variants
from app.graph.nodes import match_requirements
from app.utils.files import safe_filename


def test_normalize_applies_aliases_and_whitespace():
    assert normalize("  Postgres ") == "postgresql"
    assert normalize("K8s") == "kubernetes"
    assert normalize("FastAPI") == "fastapi"


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
    state = {"resume": {"skills": [], "projects": []}, "job": {"required_skills": []}}
    assert match_requirements(state)["match_percentage"] == 100.0


def test_routing():
    assert route_after_matching({"match_percentage": 80, "experience_gap_years": 0}) == "strong_match"
    assert route_after_matching({"match_percentage": 80, "experience_gap_years": 2}) == "weak_match"
    assert route_after_review({"approved": True}) == "approved"
    assert route_after_review({"revision_count": MAX_REVISIONS}) == "approved"
    assert route_after_review({"revision_count": 0}) == "revise"


def test_safe_filename_blocks_path_traversal():
    assert safe_filename("Jane Doe") == "jane_doe"
    assert safe_filename("../../etc/passwd") == "etc_passwd"
    assert "/" not in safe_filename("a/b\\c")
    assert safe_filename("???") == "candidate"
