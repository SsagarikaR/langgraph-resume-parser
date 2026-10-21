import pytest

from evals.cases import Expected, load_cases
from evals.metrics import SkillScore, accuracy, mean_absolute_error, score_skills


def test_perfect_match_with_aliases_and_groups():
    score = score_skills(["Postgres", "AWS (EC2, S3)", "K8s"], ["PostgreSQL", "AWS", "Kubernetes"])
    assert (score.precision, score.recall, score.f1) == (1.0, 1.0, 1.0)


def test_missed_and_invented_skills():
    score = score_skills(["Python", "Rust"], ["Python", "Docker"])
    assert score.true_positives == ["Python"]
    assert score.false_negatives == ["Docker"]  # missed
    assert score.false_positives == ["Rust"]  # invented
    assert (score.precision, score.recall) == (0.5, 0.5)


def test_or_label_accepts_either_spelling():
    assert score_skills(["AWS Lambda"], ["Lambda or AWS Lambda"]).f1 == 1.0
    assert score_skills(["Kafka"], ["Kafka or RabbitMQ"]).f1 == 1.0


def test_one_prediction_matches_only_one_expected_skill():
    # "FastAPI or Django" split into two required skills: one matches, the other is extra
    score = score_skills(["FastAPI", "Django"], ["FastAPI or Django"])
    assert score.true_positives == ["FastAPI or Django"]
    assert score.false_positives == ["Django"]


def test_optional_skills_are_not_errors():
    score = score_skills(["Python", "Agile"], ["Python"], optional=["Agile"])
    assert score.false_positives == []


def test_extra_spelling_of_a_found_skill():
    # Resume: harmless second spelling. JD: an extra must-have, so still an error by default
    assert score_skills(["Lambda", "AWS Lambda"], ["Lambda or AWS Lambda"], extra_spellings_ok=True).precision == 1.0
    assert score_skills(["Lambda", "AWS Lambda"], ["Lambda or AWS Lambda"]).false_positives == ["AWS Lambda"]


def test_duplicates_count_once():
    assert score_skills(["Python", "python ", "Python"], ["Python"]).precision == 1.0


def test_empty_lists():
    assert score_skills([], []).f1 == 1.0
    assert score_skills([], ["Python"]).f1 == 0.0
    assert score_skills(["Python"], []).precision == 0.0


def test_scores_add_up_for_micro_average():
    total = score_skills(["A"], ["A", "B"]) + score_skills(["C", "D"], ["C"])
    assert (len(total.true_positives), len(total.false_positives), len(total.false_negatives)) == (2, 1, 1)
    assert sum([score_skills(["A"], ["A"])], SkillScore()).f1 == 1.0


def test_mae_and_accuracy():
    assert mean_absolute_error([2, -4, 0]) == 2.0
    assert accuracy([True, False, True, True]) == 0.75


def test_expected_experience_counts_like_the_app():
    expected = Expected(
        resume_skills=[],
        experience=[{"start": "2023-01", "end": None}, {"start": "2022-06", "end": "2023-03"}],
        jd_required_skills=[],
        jd_required_years=None,
    )
    today = 2023 * 12 + 11  # Dec 2023
    assert expected.experience_months(today) == 19  # Jun 2022 – Dec 2023, overlap counted once


def test_bad_label_date_is_rejected():
    with pytest.raises(ValueError, match="YYYY-MM"):
        Expected(resume_skills=[], experience=[{"start": "July 2023"}], jd_required_skills=[], jd_required_years=None)


def test_every_case_file_is_valid():
    # Catches labelling mistakes (bad JSON, wrong dates, missing files) without calling the LLM
    assert load_cases()
