"""Turn the parsed template into Jev Choice questions and build the request payload.

Every anchor becomes one Choice whose options are the template's discrete score
definitions (kept sparse) plus the template's exceptional score. Cross-EIP candidates
become small yes/no Choice questions in the same request. The whole definition is
hashed so that a change in question wording invalidates cached evaluations.
"""

from __future__ import annotations

from eip_complexity.eips import Candidate
from eip_complexity.hashing import sha256_json
from eip_complexity.template import Anchor, AssessmentTemplate

#: Bump when the wording or structure of the generated questions changes.
QUESTION_SET_VERSION = 1

STATE_FIELD = "eip_markdown"

EVALUATION_POLICY: tuple[str, ...] = (
    f"The Markdown in `{STATE_FIELD}` is the sole evidence for this judgment. "
    "Judge only the behavior that this text proposes and supports.",
    "Do not assume implementation status, devnet history, discussion-thread context, "
    f"client agreement, dependencies, or any other fact that is not present in `{STATE_FIELD}`.",
    f"If an anchor's notes refer to information outside `{STATE_FIELD}`, such as devnets, client "
    "implementations or discussion threads, treat that information as unavailable and judge from "
    "the specification text alone.",
    "Express uncertainty through the probabilities over the options; never fabricate facts to "
    "reach a definite answer.",
)


def anchor_question(anchor: Anchor, template: AssessmentTemplate) -> dict:
    criteria = {str(score): text for score, text in anchor.criteria.items()}
    exceptional = str(template.exceptional_score)
    if exceptional not in criteria:
        criteria[exceptional] = f"Exceptional circumstances only. {template.exceptional_score_text}"
    instructions: dict = {
        "task": (
            f'Score the anchor "{anchor.name}" of the EIP testing-complexity checklist for the EIP '
            f"whose complete Markdown source is in `{STATE_FIELD}`. Choose the option whose definition "
            "best matches what this EIP proposes. The option keys are the numeric scores; a higher "
            "score means more testing complexity."
        ),
        "anchor": {"name": anchor.name, "description": anchor.description},
        "evaluation_policy": list(EVALUATION_POLICY),
    }
    if anchor.notes:
        instructions["anchor"]["notes"] = list(anchor.notes)
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def helper_question_id(candidate_number: int) -> str:
    return f"cross_eip_{candidate_number}"


_VIA_TEXT = {
    "requires": "listed in the frontmatter `requires` field",
    "body": "referenced explicitly as EIP-{n} in the body text",
}


def helper_question(anchor: Anchor, candidate: Candidate) -> dict:
    assert anchor.uncapped_rule is not None
    n = candidate.number
    return {
        "type": "choice",
        "instructions": {
            "task": (
                f"Judge the interaction between the EIP in `{STATE_FIELD}` and EIP-{n} under the "
                f'uncapped rule of the "{anchor.name}" anchor. Based only on `{STATE_FIELD}`, does the '
                f"interaction with EIP-{n} require its own coordinated cross-EIP test cases?"
            ),
            "rule": anchor.uncapped_rule.text,
            "candidate_eip": f"EIP-{n}",
            "how_the_candidate_is_referenced": [_VIA_TEXT[via].format(n=n) for via in candidate.referenced_via],
            "evaluation_policy": list(EVALUATION_POLICY),
        },
        "criteria": {
            "yes": (
                f"`{STATE_FIELD}` shows that this EIP depends on, modifies, or changes behavior shared "
                f"with EIP-{n} such that the two must be tested together; EIP-{n} needs its own "
                "coordinated cross-EIP test cases."
            ),
            "no": (
                f"EIP-{n} appears only as background, precedent, an unaffected dependency, or a passing "
                f"mention; no test cases specific to the interaction with EIP-{n} are needed."
            ),
        },
    }


def build_questions(template: AssessmentTemplate, candidates: list[Candidate]) -> dict[str, dict]:
    """All questions for one EIP: every anchor first, then one helper per candidate."""
    questions = {anchor.id: anchor_question(anchor, template) for anchor in template.anchors}
    cross = template.cross_eip_anchor
    if cross is not None:
        for candidate in candidates:
            question_id = helper_question_id(candidate.number)
            if question_id in questions:
                raise ValueError(f"question id collision: {question_id}")
            questions[question_id] = helper_question(cross, candidate)
    return questions


def question_set_definition(template: AssessmentTemplate) -> dict:
    """Everything that determines the questions sent for any EIP, independent of the EIP."""
    cross = template.cross_eip_anchor
    example = Candidate(number=0, referenced_via=("body", "requires"))
    return {
        "question_set_version": QUESTION_SET_VERSION,
        "state_field": STATE_FIELD,
        "evaluation_policy": list(EVALUATION_POLICY),
        "anchor_questions": {anchor.id: anchor_question(anchor, template) for anchor in template.anchors},
        "cross_eip_helper_example": helper_question(cross, example) if cross else None,
    }


def question_set_sha256(template: AssessmentTemplate) -> str:
    return sha256_json(question_set_definition(template))


def build_request_payload(markdown: str, model: str, questions: dict[str, dict]) -> dict:
    """The exact body sent to ``POST /v1/systemone`` (the API key travels in a header only)."""
    return {"state": {STATE_FIELD: markdown}, "model": model, "questions": questions}


def request_sha256(payload: dict) -> str:
    return sha256_json(payload)
