"""Hashing and canonical JSON helpers shared by every module."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 preserved."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def git_blob_sha1(data: bytes) -> str:
    """The SHA-1 git would assign to ``data`` as a blob (``git hash-object``)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
