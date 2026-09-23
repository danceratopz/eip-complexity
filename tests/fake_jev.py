"""A deterministic stand-in for Jev used by the offline tests."""

from __future__ import annotations

from eip_complexity.jev import JevResponse


def choice_answer(options: list[str], chosen: str, confidence: float = 0.9) -> dict:
    rest = (1.0 - 0.8) / max(1, len(options) - 1)
    probabilities = {o: (0.8 if o == chosen else rest) for o in options}
    return {"type": "choice", "choice": chosen, "probabilities": probabilities, "confidence": confidence}


class FakeJev:
    """Answers every anchor with ``anchor_score`` and every helper with ``helper_choice``."""

    def __init__(self, *, model: str = "jev-1.13.0", anchor_score: str = "0", helper_choice: str = "no",
                 overrides: dict[str, str] | None = None):
        self.model = model
        self.anchor_score = anchor_score
        self.helper_choice = helper_choice
        self.overrides = overrides or {}
        self.calls: list[dict] = []
        self.closed = False

    def evaluate(self, payload: dict) -> JevResponse:
        self.calls.append(payload)
        answers = {}
        for question_id, question in payload["questions"].items():
            options = list(question["criteria"])
            if question_id in self.overrides:
                chosen = self.overrides[question_id]
            elif set(options) == {"yes", "no"}:  # a Cross-EIP helper question
                chosen = self.helper_choice
            else:
                chosen = self.anchor_score if self.anchor_score in options else options[0]
            answers[question_id] = choice_answer(options, chosen)
        raw = {"model": self.model, "answers": answers, "usage": {"input_tokens": 1234, "output_tokens": 56}}
        return JevResponse(raw=raw, model=self.model, usage=raw["usage"], request_id="req-fake")

    def close(self) -> None:
        self.closed = True
