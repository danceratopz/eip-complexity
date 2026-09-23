"""One real Jev request. Runs only when EIP_COMPLEXITY_LIVE=1 and TYPESAFE_API_KEY are set."""

from __future__ import annotations

import os

import pytest

from eip_complexity.eips import Candidate
from eip_complexity.jev import TypeSafeJevClient, is_concrete_model
from eip_complexity.questions import build_questions, build_request_payload
from eip_complexity.scoring import score_response
from tests.conftest import FAKE_EIP_MARKDOWN

pytestmark = pytest.mark.skipif(
    os.environ.get("EIP_COMPLEXITY_LIVE") != "1" or not os.environ.get("TYPESAFE_API_KEY"),
    reason="set EIP_COMPLEXITY_LIVE=1 and TYPESAFE_API_KEY to run the live smoke test",
)


def test_one_live_request_yields_a_complete_evaluation(template):
    candidates = [Candidate(1559, ("body", "requires")), Candidate(7702, ("body", "requires"))]
    questions = build_questions(template, candidates)
    payload = build_request_payload(FAKE_EIP_MARKDOWN, "jev-latest", questions)
    client = TypeSafeJevClient()
    try:
        response = client.evaluate(payload)
    finally:
        client.close()
    assert is_concrete_model(response.model)
    scores = score_response(template, questions, candidates, response.raw, where="live")
    assert len(scores["anchors"]) == 28
    assert len(scores["cross_eip"]["candidates"]) == 2
    assert scores["tier"]["name"] in {"Low Complexity", "Medium Complexity", "High Complexity"}
