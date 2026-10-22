import pytest

from app.graph.edges import route_after_matching
from app.graph.nodes import match_requirements
from evals import judge, run_eval
from evals.cases import Expected, load_cases
from evals.judge import QuestionVerdict, QuestionVerdicts, score_verdicts
from evals.metrics import SkillScore, accuracy, forbidden_found, jaccard, mean_absolute_error, score_skills, skill_names


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
    predicted, expected = ["Lambda", "Serverless Lambda"], ["Lambda or Serverless Lambda"]
    assert score_skills(predicted, expected, extra_spellings_ok=True).precision == 1.0
    assert score_skills(predicted, expected).false_positives == ["Serverless Lambda"]


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
        route="weak_match",
    )
    today = 2023 * 12 + 11  # Dec 2023
    assert expected.experience_months(today) == 19  # Jun 2022 – Dec 2023, overlap counted once


def test_bad_label_date_is_rejected():
    with pytest.raises(ValueError, match="YYYY-MM"):
        Expected(
            resume_skills=[],
            experience=[{"start": "July 2023"}],
            jd_required_skills=[],
            jd_required_years=None,
            route="weak_match",
        )


def test_every_case_file_is_valid():
    # Catches labelling mistakes (bad JSON, wrong dates, missing files) without calling the LLM
    assert load_cases()


@pytest.mark.parametrize("case", sorted(load_cases()))
def test_case_labels_take_the_intended_route(case):
    # With perfect extraction (the labels), each case takes the route it is labelled with
    expected = load_cases(case)[case].expected
    today = 2026 * 12 + 9  # Oct 2026, fixed so the test does not drift
    resume = {
        "skills": [alt for s in expected.resume_skills for alt in s.split(" or ")[:1]],
        "projects": [],
        "total_experience_months": expected.experience_months(today),
    }
    job = {"required_skills": expected.jd_required_skills, "required_experience_years": expected.jd_required_years}
    assert route_after_matching(match_requirements({"resume": resume, "job": job})) == expected.route


def test_both_route_outcomes_and_the_adversarial_cases_are_covered():
    cases = load_cases()
    assert {c.expected.route for c in cases.values()} == {"strong_match", "weak_match"}
    assert any(c.expected.forbidden_resume_skills for c in cases.values())  # a prompt injection
    assert sum(c.counterfactual_of is not None for c in cases.values()) >= 2  # fairness pairs


def test_a_counterfactual_case_is_loaded_with_its_original():
    assert set(load_cases("08_")) == {"08_counterfactual_of_03_name_gender", "03_data_engineer_senior_jd"}


def test_counterfactual_cases_differ_from_their_original_only_in_protected_attributes():
    # Same labels: only the name, pronouns, college or birth date differ in the resume
    cases = load_cases()
    for case in cases.values():
        if case.counterfactual_of:
            original = cases[case.counterfactual_of].expected.model_dump()
            assert case.expected.model_dump() == original


def test_forbidden_skills_are_found_under_any_spelling():
    assert forbidden_found(["Python", "k8s", "Apache Kafka"], ["Kubernetes", "Kafka", "Scala"]) == [
        "Kubernetes",
        "Kafka",
    ]
    assert forbidden_found(["Python"], ["Scala"]) == []


def test_skill_overlap():
    assert jaccard(skill_names(["Postgres", "Python"]), skill_names(["PostgreSQL", "python"])) == 1.0
    assert jaccard(skill_names(["Python", "Go"]), skill_names(["Python"])) == 0.5
    assert jaccard(frozenset(), frozenset()) == 1.0


def result(**overrides) -> run_eval.CaseResult:
    fields = {
        "resume_skills_predicted": ["Python", "SQL"],
        "jd_skills_predicted": [],
        "resume_skills": SkillScore(),
        "jd_skills": SkillScore(),
        "experience_expected": 24,
        "experience_predicted": 24,
        "years_expected": None,
        "years_predicted": None,
        "route_expected": "weak_match",
        "route_predicted": "weak_match",
    }
    return run_eval.CaseResult(**(fields | overrides))


def test_counterfactual_pairs_must_match_on_route_experience_and_skills():
    cases = load_cases()
    pair = ("03_data_engineer_senior_jd", "08_counterfactual_of_03_name_gender")
    same = {pair[0]: result(), pair[1]: result()}
    assert run_eval.counterfactual_gaps(cases, same) == {pair[1]: []}

    differs = {pair[0]: result(), pair[1]: result(route_predicted="strong_match", experience_predicted=12)}
    gaps = run_eval.counterfactual_gaps(cases, differs)[pair[1]]
    assert gaps == ["route weak_match → strong_match", "experience 24 → 12 months"]
    overall = run_eval.overall_scores(differs, {pair[1]: gaps})
    assert overall["counterfactual_consistency"] == 0.0
    assert "Counterfactual consistency" in run_eval.check_thresholds(overall)


def test_an_injected_skill_fails_the_run():
    injected = result(forbidden_expected=["Kubernetes"], forbidden_extracted=["Kubernetes"])
    overall = run_eval.overall_scores({"06": injected, "01": result()}, {})
    assert overall["injection_resistance"] == 0.0
    assert overall["route_accuracy"] == 1.0
    assert "Injection resistance" in run_eval.check_thresholds(overall)


def test_scores_no_case_measured_are_left_out():
    overall = run_eval.overall_scores({"01": result()}, {})
    assert "injection_resistance" not in overall
    assert "counterfactual_consistency" not in overall
    assert "question_format_pass_rate" not in overall


def test_question_scores():
    good = run_eval.QuestionScore(count=13, behavioral=2, missing_skills=["Kafka", "Go"], uncovered=["Go"])
    assert good.format_ok
    assert good.gap_coverage == 0.5
    assert not run_eval.QuestionScore(count=5, behavioral=2, missing_skills=[], uncovered=[]).format_ok
    assert run_eval.QuestionScore(count=12, behavioral=0, missing_skills=[], uncovered=[]).gap_coverage == 1.0


QUESTIONS = [
    {"type": "gap", "topic": "Kafka", "question": "How would you size a Kafka cluster?"},
    {"type": "behavioral", "topic": "Teamwork", "question": "Do you plan to have children soon?"},
]


def verdict(index: int, relevance: int = 5, clarity: int = 4, protected: bool = False) -> QuestionVerdict:
    return QuestionVerdict(index=index, relevance=relevance, clarity=clarity, protected_attribute=protected, reason="r")


def test_judge_verdicts_are_averaged_and_flag_protected_questions():
    score = score_verdicts(QUESTIONS, [verdict(1, relevance=1, protected=True), verdict(0)])
    assert (score.relevance, score.clarity) == (3.0, 4.0)
    assert score.flagged == ["Do you plan to have children soon? (r)"]
    assert score.low_relevance == ["Do you plan to have children soon? (r)"]

    judged = run_eval.QuestionScore(count=13, behavioral=2, missing_skills=[], uncovered=[], judge=score)
    overall = run_eval.overall_scores({"01": result(questions=judged)}, {})
    assert overall["protected_attribute_free"] == 0.0
    failures = run_eval.check_thresholds(overall)
    assert "Judge: protected-attribute-free" in failures
    assert "Judge: question relevance" in failures


@pytest.mark.parametrize("indexes", [[0], [0, 0], [0, 2], [0, 1, 1]])
def test_judge_must_rate_every_question_once(indexes):
    with pytest.raises(ValueError, match="judge rated"):
        score_verdicts(QUESTIONS, [verdict(i) for i in indexes])


def test_judge_questions_calls_the_judge_model(monkeypatch):
    calls = []

    class FakeJudge:
        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            calls.append(messages)
            return QuestionVerdicts(verdicts=[verdict(0), verdict(1)])

    monkeypatch.setattr(judge, "chat_model", lambda name: calls.append(name) or FakeJudge())
    score = judge.judge_questions(QUESTIONS, {"title": "Data engineer"}, ["Kafka"], model="judge-model")
    assert score is not None and score.relevance == 5.0 and not score.flagged
    assert calls[0] == "judge-model"
    system, human = calls[1]
    assert "protected_attribute" in system[1]
    assert '"candidate_gaps": [\n    "Kafka"' in human[1]
    assert judge.judge_questions([], {}, []) is None
