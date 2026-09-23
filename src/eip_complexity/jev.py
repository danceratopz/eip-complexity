"""The only module that talks to Jev, through the official TypeSafe Python SDK."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

from eip_complexity.errors import ComplexityError

API_KEY_ENV = "TYPESAFE_API_KEY"

#: One EIP with ~30-45 questions is a large request; the SDK default of 10s is too short.
REQUEST_TIMEOUT_SECONDS = 180.0
#: Retries use the SDK's own transient-error rules (408, 429, 5xx, connection errors, timeouts).
MAX_RETRIES = 3

_CONCRETE_MODEL_RE = re.compile(r"jev-\d+\.\d+\.\d+")


def is_concrete_model(model: str) -> bool:
    """``jev-1.13.0`` is concrete; ``jev-latest`` and other aliases are not."""
    return _CONCRETE_MODEL_RE.fullmatch(model) is not None


def sdk_version() -> str:
    return importlib.metadata.version("typesafe-sdk")


@dataclass(frozen=True)
class JevResponse:
    raw: dict  # the response body exactly as decoded from the wire
    model: str
    usage: dict | None
    request_id: str | None


class JevClient(Protocol):
    def evaluate(self, payload: dict) -> JevResponse: ...

    def close(self) -> None: ...


class TypeSafeJevClient:
    """Sends one prepared payload per call and returns the verbatim response."""

    def __init__(self) -> None:
        if not os.environ.get(API_KEY_ENV, "").strip():
            raise ComplexityError(f"{API_KEY_ENV} is not set; it is the only way to supply the Jev API key")
        from typesafe_sdk import RetryPolicy, TypeSafeClient

        self._client = TypeSafeClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            retry=RetryPolicy(max_retries=MAX_RETRIES, timeout=None),
        )

    def evaluate(self, payload: dict) -> JevResponse:
        from typesafe_sdk import TypeSafeError

        try:
            response = self._client.system_one(payload["state"], payload["questions"], model=payload["model"])
        except TypeSafeError as error:
            raise ComplexityError(f"Jev request failed: {error}") from error
        raw = json.loads(response.raw_http_response.content)
        if not isinstance(raw, dict):
            raise ComplexityError("Jev returned a non-object JSON body")
        try:
            request_id: str | None = response.request_id
        except Exception:  # the SDK raises when the header is absent
            request_id = None
        usage = raw.get("usage")
        return JevResponse(raw=raw, model=response.model, usage=usage if isinstance(usage, dict) else None, request_id=request_id)

    def close(self) -> None:
        self._client.close()
