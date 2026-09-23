from __future__ import annotations

import re

import pytest

from eip_complexity.errors import ComplexityError
from eip_complexity.template import parse_template


def test_current_template_is_revision_2_with_28_anchors(template):
    assert template.revision == 2
    assert len(template.anchors) == 28
    assert len({a.id for a in template.anchors}) == 28
    assert template.max_defined_score == 3
    assert template.exceptional_score == 4
    assert template.exceptional_score_text.startswith("A score of 4 may be used in exceptional circumstances")


def test_anchor_names_and_labels_are_preserved(template):
    names = [a.name for a in template.anchors]
    assert names[0] == "EVM Gas rule changes"
    assert names[-1] == "Cross-EIP interactions"
    crypto = template.anchor("cryptography")
    assert crypto.checklist_label == "Cryptography-related testing"
    assert template.anchor("cross_eip_interactions").checklist_label == "Cross-EIP interactions (uncapped)"
    assert template.anchor("encoding_changes_rlp_ssz").name == "Encoding changes (RLP/SSZ)"


def test_sparse_anchors_stay_sparse(template):
    assert list(template.anchor("modified_opcodes").criteria) == [0, 3]
    assert list(template.anchor("new_transaction_types").criteria) == [0, 3]
    assert list(template.anchor("new_block_header_fields").criteria) == [0, 3]
    assert list(template.anchor("encoding_changes_rlp_ssz").criteria) == [0, 3]
    assert list(template.anchor("new_fork_activation_mechanism").criteria) == [0, 3]
    assert list(template.anchor("evm_gas_rule_changes").criteria) == [0, 1, 2, 3]
    assert all(4 not in a.criteria for a in template.anchors)


def test_descriptions_and_notes_are_extracted(template):
    ordering = template.anchor("state_access_ordering_within_opcode_execution")
    assert ordering.description.startswith("Changes *where inside an opcode's execution* state is accessed")
    assert len(ordering.notes) == 3
    assert ordering.notes[0].startswith('Distinct from "Modified opcodes"')
    assert template.anchor("added_opcodes").notes == (
        'Cryptography opcodes are not considered complex by default. Refer to the "Cryptography" section for a separate assessment.',
    )
    assert template.anchor("evm_gas_rule_changes").notes == ()


def test_cross_eip_uncapped_rule(template):
    cross = template.cross_eip_anchor
    assert cross is not None and cross.id == "cross_eip_interactions"
    rule = cross.uncapped_rule
    assert (rule.increment, rule.per_additional, rule.beyond_first) == (1, 3, 3)
    assert rule.text.startswith("**+1 for every 3 additional interacting EIPs beyond the first 3**")
    assert list(cross.criteria) == [0, 1, 2, 3]
    assert sum(1 for a in template.anchors if a.uncapped_rule) == 1


def test_tier_thresholds_come_from_template(template):
    tiers = [(t.name, t.min_inclusive, t.max_exclusive) for t in template.tiers]
    assert tiers == [("Low Complexity", 0, 12), ("Medium Complexity", 12, 23), ("High Complexity", 23, None)]
    assert template.tier_for(0).name == "Low Complexity"
    assert template.tier_for(11).name == "Low Complexity"
    assert template.tier_for(12).name == "Medium Complexity"
    assert template.tier_for(22).name == "Medium Complexity"
    assert template.tier_for(23).name == "High Complexity"
    assert template.tier_for(90).emoji == "🔴"


def test_missing_anchor_section_fails_loudly(template_text):
    broken = template_text.replace("##### Modified opcodes\n\nModifies pre-existing opcodes\n\n- 0. No pre-existing opcode modifications are introduced.\n- 3. At least one pre-existing opcode's behavior is modified (not including gas changes) or a pre-existing opcode is deprecated.\n\n", "")
    assert broken != template_text
    with pytest.raises(ComplexityError, match="declares 28 anchors but 27"):
        parse_template(broken)


def test_wrong_declared_count_fails(template_text):
    broken = template_text.replace("Checklist revision: **2** (28 anchors)", "Checklist revision: **2** (27 anchors)")
    with pytest.raises(ComplexityError, match="27 anchors but 28"):
        parse_template(broken)


def test_checklist_row_mismatch_fails(template_text):
    broken = template_text.replace("| **Modified opcodes** |   |   |", "| **Something else** |   |   |")
    with pytest.raises(ComplexityError, match="does not match checklist row"):
        parse_template(broken)


def test_missing_tier_table_fails(template_text):
    broken = re.sub(r"\| 🟡 \*\*Medium Complexity\*\*.*\n", "", template_text)
    with pytest.raises(ComplexityError, match="not contiguous"):
        parse_template(broken)


def test_missing_exceptional_sentence_fails(template_text):
    broken = template_text.replace("A score of 4 may be used in exceptional circumstances", "Nothing to see here")
    with pytest.raises(ComplexityError, match="exceptional circumstances"):
        parse_template(broken)


def test_to_dict_round_trips_sparse_criteria(template):
    as_dict = template.to_dict()
    modified = next(a for a in as_dict["anchors"] if a["id"] == "modified_opcodes")
    assert modified["criteria"] == {"0": "No pre-existing opcode modifications are introduced.",
                                    "3": "At least one pre-existing opcode's behavior is modified (not including gas changes) or a pre-existing opcode is deprecated."}
    assert as_dict["tiers"][1]["range"] == ">=12<23"
