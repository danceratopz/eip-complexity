from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from eip_complexity.template import AssessmentTemplate, load_template

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "templates" / "EIP-Complexity-Assessment.md"

FAKE_EIP_MARKDOWN = """---
eip: 9001
title: Fake Frame Widget
description: A fixture EIP for tests.
author: Test Author (@test)
discussions-to: https://example.invalid/t/1
status: Draft
type: Standards Track
category: Core
created: 2026-01-01
requires: 1559, 7702
---

## Abstract

This EIP introduces a widget. It builds on EIP-7702 and interacts with [EIP-4844](./eip-4844.md).
It mentions EIP-9001 itself and eip-1559 again.

## Specification

A new opcode `WIDGET` is introduced.

## Copyright

Copyright and related rights waived via [CC0](../LICENSE.md).
"""


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="session")
def template() -> AssessmentTemplate:
    return load_template(TEMPLATE_PATH)[0]


@pytest.fixture(scope="session")
def template_text() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def eips_clone(tmp_path: Path) -> tuple[Path, str]:
    """A local 'origin' repository with one fake EIP, plus a clone of it. Returns (clone, commit)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "--quiet", "--initial-branch=trunk", cwd=origin)
    git("config", "user.email", "t@example.invalid", cwd=origin)
    git("config", "user.name", "Test", cwd=origin)
    (origin / "EIPS").mkdir()
    (origin / "EIPS" / "eip-9001.md").write_text(FAKE_EIP_MARKDOWN, encoding="utf-8")
    (origin / "EIPS" / "eip-9003.md").write_text(FAKE_EIP_MARKDOWN.replace("eip: 9001", "eip: 9003").replace("Fake Frame Widget", "Consensus Fixture"), encoding="utf-8")
    git("add", ".", cwd=origin)
    git("commit", "--quiet", "-m", "Add EIP-9001", cwd=origin)
    commit = git("rev-parse", "HEAD", cwd=origin)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(clone)], check=True, capture_output=True)
    # Dirty the clone's working tree: the tool must ignore it.
    (clone / "EIPS" / "eip-9001.md").write_text("garbage", encoding="utf-8")
    return clone, commit
