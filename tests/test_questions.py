from __future__ import annotations

import json

from eip_complexity.eips import Candidate
from eip_complexity.questions import (
    EVALUATION_POLICY,
    anchor_question,
    build_questions,
    build_request_payload,
    helper_question,
    question_set_sha256,
    request_sha256,
)


def test_anchor_question_uses_template_criteria_plus_exceptional_4(template):
    q = anchor_question(template.anchor("evm_gas_rule_changes"), template)
    assert q["type"] == "choice"
    assert list(q["criteria"]) == ["0", "1", "2", "3", "4"]
    assert q["criteria"]["0"] == "No gas accounting changes."
    assert q["criteria"]["4"].startswith("Exceptional circumstances only. A score of 4 may be used")
    assert q["instructions"]["anchor"]["name"] == "EVM Gas rule changes"
    assert q["instructions"]["evaluation_policy"] == list(EVALUATION_POLICY)
    assert "notes" not in q["instructions"]["anchor"]


def test_sparse_anchor_question_does_not_invent_1_and_2(template):
    q = anchor_question(template.anchor("modified_opcodes"), template)
    assert list(q["criteria"]) == ["0", "3", "4"]


def test_anchor_notes_are_included(template):
    q = anchor_question(template.anchor("added_opcodes"), template)
    assert q["instructions"]["anchor"]["notes"][0].startswith("Cryptography opcodes are not considered complex")


def test_helper_question_is_yes_no_and_names_the_candidate(template):
    q = helper_question(template.cross_eip_anchor, Candidate(8141, ("body", "requires")))
    assert set(q["criteria"]) == {"yes", "no"}
    assert "EIP-8141" in q["instructions"]["task"]
    assert q["instructions"]["rule"].startswith("**+1 for every 3 additional interacting EIPs")
    assert q["instructions"]["how_the_candidate_is_referenced"] == [
        "referenced explicitly as EIP-8141 in the body text",
        "listed in the frontmatter `requires` field",
    ]


def test_build_questions_orders_anchors_then_helpers(template):
    qs = build_questions(template, [Candidate(1559, ("requires",)), Candidate(7702, ("body",))])
    assert len(qs) == 30
    assert list(qs)[:28] == [a.id for a in template.anchors]
    assert list(qs)[28:] == ["cross_eip_1559", "cross_eip_7702"]


def test_question_set_hash_is_deterministic_and_wording_sensitive(template):
    first = question_set_sha256(template)
    assert first == question_set_sha256(template)
    changed = template.anchors[0].__class__(**{**template.anchors[0].__dict__, "description": "different"})
    other = template.__class__(**{**template.__dict__, "anchors": (changed, *template.anchors[1:])})
    assert question_set_sha256(other) != first


def test_payload_contains_no_key_and_hash_is_stable(template, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "not-a-real-key-and-must-not-appear")
    qs = build_questions(template, [])
    payload = build_request_payload("# md", "jev-1.13.0", qs)
    text = json.dumps(payload)
    assert "not-a-real-key" not in text and "TYPESAFE" not in text
    assert payload["state"] == {"eip_markdown": "# md"} and payload["model"] == "jev-1.13.0"
    assert request_sha256(payload) == request_sha256(json.loads(text))
    assert request_sha256(payload) != request_sha256(build_request_payload("# md2", "jev-1.13.0", qs))
