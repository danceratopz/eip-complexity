from __future__ import annotations

import pytest

from eip_complexity.eips import Candidate
from eip_complexity.errors import ComplexityError
from eip_complexity.questions import build_questions
from eip_complexity.scoring import compute_bonus, score_response, validate_choice_answer
from eip_complexity.template import UncappedRule
from tests.fake_jev import FakeJev

RULE = UncappedRule(increment=1, per_additional=3, beyond_first=3, text="+1 per 3 beyond 3")


@pytest.mark.parametrize("qualifying,bonus", [(0, 0), (1, 0), (3, 0), (4, 0), (5, 0), (6, 1), (8, 1), (9, 2), (12, 3), (13, 3)])
def test_bonus_arithmetic(qualifying, bonus):
    assert compute_bonus(qualifying, RULE) == bonus


def test_bonus_uses_rule_parameters():
    assert compute_bonus(10, UncappedRule(2, 2, 4, "")) == 6


def _scores(template, fake: FakeJev, candidates):
    questions = build_questions(template, candidates)
    payload = {"state": {"eip_markdown": "x"}, "model": "jev-1.13.0", "questions": questions}
    response = fake.evaluate(payload)
    return score_response(template, questions, candidates, response.raw, where="test")


def test_all_zero_scores_low_tier(template):
    scores = _scores(template, FakeJev(anchor_score="0"), [])
    assert scores["total_score"] == 0
    assert scores["tier"]["name"] == "Low Complexity"
    assert len(scores["anchors"]) == 28
    assert scores["cross_eip"]["candidates"] == [] and scores["cross_eip"]["bonus"] == 0


def test_exceptional_score_4_is_accepted_everywhere(template):
    scores = _scores(template, FakeJev(anchor_score="4"), [])
    assert all(a["score"] == 4 for a in scores["anchors"])
    assert scores["total_score"] == 28 * 4
    assert scores["tier"]["name"] == "High Complexity"


def test_sparse_anchor_falls_back_to_defined_option(template):
    # FakeJev picks option "2" where it exists and the first option otherwise.
    scores = _scores(template, FakeJev(anchor_score="2"), [])
    by_id = {a["id"]: a for a in scores["anchors"]}
    assert by_id["evm_gas_rule_changes"]["score"] == 2
    assert by_id["modified_opcodes"]["score"] == 0
    assert by_id["modified_opcodes"]["criteria"].keys() == {"0", "3", "4"}


def test_cross_eip_bonus_is_added_to_base(template):
    candidates = [Candidate(n, ("body",)) for n in range(1, 8)]  # 7 candidates
    overrides = {"cross_eip_interactions": "3"} | {f"cross_eip_{n}": "yes" for n in range(1, 7)}  # 6 yes, 1 no
    scores = _scores(template, FakeJev(anchor_score="0", overrides=overrides), candidates)
    cross = scores["cross_eip"]
    assert cross["qualifying_interactions"] == 6
    assert (cross["base_score"], cross["bonus"], cross["final_score"]) == (3, 1, 4)
    anchor = next(a for a in scores["anchors"] if a["id"] == "cross_eip_interactions")
    assert (anchor["base_score"], anchor["bonus"], anchor["score"]) == (3, 1, 4)
    assert scores["total_score"] == 4
    assert [c["requires_coordinated_tests"] for c in cross["candidates"]] == [True] * 6 + [False]
    assert all("probabilities" in c and "confidence" in c for c in cross["candidates"])


def test_tier_boundaries(template):
    overrides = {a.id: "0" for a in template.anchors}
    threes = ["evm_gas_rule_changes", "new_evm_gas_refund", "edge_boundary_conditions", "security_risks"]
    for anchor_id in threes:
        overrides[anchor_id] = "3"  # 12 -> Medium
    scores = _scores(template, FakeJev(overrides=overrides), [])
    assert scores["total_score"] == 12 and scores["tier"]["name"] == "Medium Complexity"
    overrides["performance_risks"] = "3"
    overrides["cryptography"] = "3"
    overrides["added_opcodes"] = "3"
    overrides["blob_gas_accounting_changes"] = "2"  # 23 -> High
    scores = _scores(template, FakeJev(overrides=overrides), [])
    assert scores["total_score"] == 23 and scores["tier"]["name"] == "High Complexity"


def test_missing_anchor_answer_fails(template):
    fake = FakeJev()
    questions = build_questions(template, [])
    raw = fake.evaluate({"state": {}, "model": "m", "questions": questions}).raw
    del raw["answers"]["security_risks"]
    with pytest.raises(ComplexityError, match="no answer for anchor 'security_risks'"):
        score_response(template, questions, [], raw, where="t")


def test_missing_helper_answer_fails(template):
    candidates = [Candidate(1559, ("requires",))]
    fake = FakeJev()
    questions = build_questions(template, candidates)
    raw = fake.evaluate({"state": {}, "model": "m", "questions": questions}).raw
    del raw["answers"]["cross_eip_1559"]
    with pytest.raises(ComplexityError, match="no answer for helper 'cross_eip_1559'"):
        score_response(template, questions, candidates, raw, where="t")


def test_unexpected_answer_fails(template):
    questions = build_questions(template, [])
    raw = FakeJev().evaluate({"state": {}, "model": "m", "questions": questions}).raw
    raw["answers"]["surprise"] = raw["answers"]["security_risks"]
    with pytest.raises(ComplexityError, match="unknown questions \\['surprise'\\]"):
        score_response(template, questions, [], raw, where="t")


@pytest.mark.parametrize(
    "mutation,message",
    [
        (lambda a: a.update(choice="7"), "chose '7'"),
        (lambda a: a.update(type="score"), "expected 'choice'"),
        (lambda a: a["probabilities"].pop("4"), "do not cover exactly"),
        (lambda a: a["probabilities"].update({"0": 0.9}), "sum to"),
        (lambda a: a.update(confidence=1.5), "confidence is not a number"),
        (lambda a: a.update(choice="1"), "not the highest-probability option"),
    ],
)
def test_invalid_choice_answers_fail(mutation, message):
    options = ["0", "1", "2", "3", "4"]
    answer = {"type": "choice", "choice": "2", "probabilities": {"0": 0.05, "1": 0.05, "2": 0.8, "3": 0.05, "4": 0.05}, "confidence": 0.8}
    mutation(answer)
    with pytest.raises(ComplexityError, match=message):
        validate_choice_answer("q", answer, dict.fromkeys(options), where="t")


def test_rounding_ties_are_accepted_but_real_mismatches_are_not():
    options = dict.fromkeys(["0", "1", "2", "3", "4"])
    tie = {"type": "choice", "choice": "3", "probabilities": {"0": 0.01, "1": 0.41, "2": 0.16, "3": 0.40, "4": 0.02}, "confidence": 0.25}
    assert validate_choice_answer("q", tie, options, where="t")["choice"] == "3"
    wrong = {**tie, "probabilities": {"0": 0.01, "1": 0.45, "2": 0.12, "3": 0.40, "4": 0.02}}
    with pytest.raises(ComplexityError, match="not the highest-probability option"):
        validate_choice_answer("q", wrong, options, where="t")
