"""Validate Jev answers and turn them into anchor scores, the Cross-EIP bonus, total and tier.

No answer is ever substituted: a missing, malformed or out-of-range answer fails the EIP.
"""

from __future__ import annotations

import math

from eip_complexity.eips import Candidate
from eip_complexity.errors import ComplexityError
from eip_complexity.questions import helper_question_id
from eip_complexity.template import AssessmentTemplate, UncappedRule

_PROBABILITY_SUM_TOLERANCE = 0.02
#: Jev reports probabilities rounded to two decimals; its `choice` is the unrounded argmax, so a
#: near-tie may show another option 0.01 higher. Larger gaps are real inconsistencies.
_ARGMAX_TOLERANCE = 0.011


def compute_bonus(qualifying_interactions: int, rule: UncappedRule) -> int:
    """``increment * floor(max(0, qualifying - beyond_first) / per_additional)``."""
    return rule.increment * (max(0, qualifying_interactions - rule.beyond_first) // rule.per_additional)


def validate_choice_answer(question_id: str, answer: object, criteria: dict[str, object], *, where: str) -> dict:
    """Return ``{"choice", "probabilities", "confidence"}`` or raise ``ComplexityError``."""
    prefix = f"{where}: answer '{question_id}'"
    if not isinstance(answer, dict):
        raise ComplexityError(f"{prefix} is not an object")
    if answer.get("type") != "choice":
        raise ComplexityError(f"{prefix} has type {answer.get('type')!r}, expected 'choice'")
    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in criteria:
        raise ComplexityError(f"{prefix} chose {choice!r}, not one of {sorted(criteria)}")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(criteria):
        raise ComplexityError(f"{prefix} probabilities do not cover exactly the options {sorted(criteria)}")
    total = 0.0
    for option, value in probabilities.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ComplexityError(f"{prefix} probability for {option!r} is not a number in [0, 1]: {value!r}")
        total += value
    if abs(total - 1.0) > _PROBABILITY_SUM_TOLERANCE:
        raise ComplexityError(f"{prefix} probabilities sum to {total:.4f}, not 1")
    confidence = answer.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ComplexityError(f"{prefix} confidence is not a number in [0, 1]: {confidence!r}")
    if probabilities[choice] < max(probabilities.values()) - _ARGMAX_TOLERANCE:
        raise ComplexityError(f"{prefix} choice {choice!r} is not the highest-probability option")
    return {"choice": choice, "probabilities": {k: float(v) for k, v in probabilities.items()}, "confidence": float(confidence)}


def score_response(
    template: AssessmentTemplate,
    questions: dict[str, dict],
    candidates: list[Candidate],
    raw_response: dict,
    *,
    where: str,
) -> dict:
    """Build the ``scores`` block of an evaluation from the verbatim Jev response."""
    answers = raw_response.get("answers")
    if not isinstance(answers, dict):
        raise ComplexityError(f"{where}: Jev response has no 'answers' object")
    unexpected = set(answers) - set(questions)
    if unexpected:
        raise ComplexityError(f"{where}: Jev response contains answers to unknown questions {sorted(unexpected)}")

    anchors: list[dict] = []
    cross_eip: dict | None = None
    for anchor in template.anchors:
        if anchor.id not in answers:
            raise ComplexityError(f"{where}: Jev response has no answer for anchor '{anchor.id}'")
        criteria = questions[anchor.id]["criteria"]
        judged = validate_choice_answer(anchor.id, answers[anchor.id], criteria, where=where)
        score = int(judged["choice"])
        entry = {
            "id": anchor.id,
            "name": anchor.name,
            "checklist_label": anchor.checklist_label,
            "score": score,
            "choice": judged["choice"],
            "probabilities": judged["probabilities"],
            "confidence": judged["confidence"],
            "criteria": criteria,
        }
        if anchor.uncapped_rule is not None:
            helpers = []
            for candidate in candidates:
                question_id = helper_question_id(candidate.number)
                if question_id not in answers:
                    raise ComplexityError(f"{where}: Jev response has no answer for helper '{question_id}'")
                helper = validate_choice_answer(question_id, answers[question_id], questions[question_id]["criteria"], where=where)
                helpers.append(
                    {
                        "eip": candidate.number,
                        "question_id": question_id,
                        "referenced_via": list(candidate.referenced_via),
                        "requires_coordinated_tests": helper["choice"] == "yes",
                        **helper,
                    }
                )
            qualifying = sum(1 for helper in helpers if helper["requires_coordinated_tests"])
            bonus = compute_bonus(qualifying, anchor.uncapped_rule)
            entry["base_score"] = score
            entry["bonus"] = bonus
            entry["score"] = score + bonus
            cross_eip = {
                "anchor_id": anchor.id,
                "rule": anchor.uncapped_rule.to_dict(),
                "candidates": helpers,
                "qualifying_interactions": qualifying,
                "base_score": score,
                "bonus": bonus,
                "final_score": score + bonus,
            }
        anchors.append(entry)

    total = sum(entry["score"] for entry in anchors)
    tier = template.tier_for(total)
    return {"anchors": anchors, "cross_eip": cross_eip, "total_score": total, "tier": tier.to_dict()}
