"""Semantic cache of completed evaluations, one JSON file per cache key.

The key covers everything that can change a Jev judgment: the exact EIP Markdown, the
exact template, the question definitions and the concrete Jev model. Display metadata,
run provenance and HTML are deliberately outside the key.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from eip_complexity import RESULT_SCHEMA_VERSION
from eip_complexity.errors import ComplexityError
from eip_complexity.hashing import sha256_json


def cache_key(*, eip_sha256: str, template_sha256: str, question_set_sha256: str, model: str) -> str:
    return sha256_json(
        {
            "schema_version": RESULT_SCHEMA_VERSION,
            "eip_sha256": eip_sha256,
            "template_sha256": template_sha256,
            "question_set_sha256": question_set_sha256,
            "model": model,
        }
    )


def write_json_atomic(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path, *, where: str) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ComplexityError(f"{where}: cannot read JSON from {path}: {error}") from error
    if not isinstance(document, dict):
        raise ComplexityError(f"{where}: {path} does not contain a JSON object")
    return document


class EvaluationCache:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def load(self, key: str) -> dict | None:
        """The cached ``evaluation`` block for ``key``, or ``None``. Malformed entries fail."""
        path = self.path_for(key)
        if not path.exists():
            return None
        where = f"cache entry {path.name}"
        entry = read_json(path, where=where)
        if entry.get("schema_version") != RESULT_SCHEMA_VERSION:
            raise ComplexityError(f"{where}: schema_version {entry.get('schema_version')!r} != {RESULT_SCHEMA_VERSION}")
        if entry.get("cache_key") != key:
            raise ComplexityError(f"{where}: stored cache_key does not match its file name")
        evaluation = entry.get("evaluation")
        if not isinstance(evaluation, dict):
            raise ComplexityError(f"{where}: no 'evaluation' object")
        return evaluation

    def store(self, key: str, evaluation: dict) -> Path:
        path = self.path_for(key)
        write_json_atomic(
            path,
            {
                "schema_version": RESULT_SCHEMA_VERSION,
                "kind": "eip-complexity-cache-entry",
                "cache_key": key,
                "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "evaluation": evaluation,
            },
        )
        return path
