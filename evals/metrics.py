"""Scores for comparing LLM output with hand-labelled answers. Plain Python, no LLM.

Skill lists are scored like multi-label classification: each expected skill found is a true
positive, each expected skill missed a false negative, and each extra skill a false positive.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.graph.matching import skill_variants


def variants(skill: str) -> frozenset[str]:
    """Normalized names a skill can match by: 'Kafka or RabbitMQ' -> {'kafka', 'rabbitmq'}."""
    alternatives = re.split(r"\s+or\s+", skill, flags=re.IGNORECASE)
    return frozenset().union(*(skill_variants(alt) for alt in alternatives))


@dataclass
class SkillScore:
    true_positives: list[str] = field(default_factory=list)  # expected and found
    false_positives: list[str] = field(default_factory=list)  # found but not expected (invented / over-extracted)
    false_negatives: list[str] = field(default_factory=list)  # expected but missed

    @property
    def precision(self) -> float:
        found = len(self.true_positives) + len(self.false_positives)
        if not found:
            return 0.0 if self.false_negatives else 1.0
        return len(self.true_positives) / found

    @property
    def recall(self) -> float:
        expected = len(self.true_positives) + len(self.false_negatives)
        return len(self.true_positives) / expected if expected else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def __add__(self, other: "SkillScore") -> "SkillScore":
        # Summing counts over cases gives micro-averaged scores
        return SkillScore(
            self.true_positives + other.true_positives,
            self.false_positives + other.false_positives,
            self.false_negatives + other.false_negatives,
        )


def score_skills(
    predicted: Iterable[str],
    expected: Iterable[str],
    optional: Iterable[str] = (),
    extra_spellings_ok: bool = False,
) -> SkillScore:
    """Match predicted skills to expected ones; each predicted skill can match one expected skill.

    Skills match when they share a normalized name, so 'Postgres' matches 'PostgreSQL' and
    'AWS (EC2, S3)' matches 'AWS'. Extra skills that match `optional` are not counted as errors.
    With `extra_spellings_ok`, a second spelling of a skill already found ('Lambda' and
    'AWS Lambda') is not an error either: true for resume skills, where it only adds a match
    variant, but not for JD requirements, where every extra entry is another must-have.
    """
    remaining, seen = [], set()
    for skill in predicted:  # the same skill listed twice counts once
        if (key := variants(skill)) and key not in seen:
            seen.add(key)
            remaining.append(skill)

    score = SkillScore()
    for skill in expected:
        hit = next((p for p in remaining if variants(p) & variants(skill)), None)
        if hit is None:
            score.false_negatives.append(skill)
        else:
            score.true_positives.append(skill)
            remaining.remove(hit)

    allowed = frozenset().union(*(variants(o) for o in optional))
    if extra_spellings_ok:
        allowed |= frozenset().union(*(variants(s) for s in score.true_positives))
    score.false_positives = [p for p in remaining if not variants(p) & allowed]
    return score


def mean_absolute_error(errors: Iterable[float]) -> float:
    errors = [abs(e) for e in errors]
    return sum(errors) / len(errors) if errors else 0.0


def accuracy(results: Iterable[bool]) -> float:
    results = list(results)
    return sum(results) / len(results) if results else 0.0
