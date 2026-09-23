from __future__ import annotations

from pathlib import Path

import pytest

from eip_complexity.eips import Candidate, EipsRepo, build_document, extract_candidates, parse_frontmatter
from eip_complexity.errors import ComplexityError
from eip_complexity.hashing import git_blob_sha1, sha256_text
from tests.conftest import FAKE_EIP_MARKDOWN, git


def test_frontmatter_title_and_requires():
    fm = parse_frontmatter(FAKE_EIP_MARKDOWN, where="test")
    assert fm["title"] == "Fake Frame Widget"
    assert fm["requires"] == "1559, 7702"
    doc = build_document(9001, "EIPS/eip-9001.md", FAKE_EIP_MARKDOWN.encode(), "blob")
    assert doc.title == "Fake Frame Widget"
    assert doc.requires == (1559, 7702)
    assert doc.sha256 == sha256_text(FAKE_EIP_MARKDOWN)


def test_frontmatter_eip_number_must_match():
    with pytest.raises(ComplexityError, match="frontmatter 'eip' is '9001', expected 9002"):
        build_document(9002, "EIPS/eip-9002.md", FAKE_EIP_MARKDOWN.encode(), "blob")


def test_candidate_extraction_dedupes_and_excludes_self():
    doc = build_document(9001, "EIPS/eip-9001.md", FAKE_EIP_MARKDOWN.encode(), "blob")
    assert extract_candidates(doc) == [
        Candidate(1559, ("body", "requires")),
        Candidate(4844, ("body",)),
        Candidate(7702, ("body", "requires")),
    ]


def test_repo_resolves_default_branch_and_reads_at_commit_not_working_tree(eips_clone):
    clone, commit = eips_clone
    repo = EipsRepo(clone)
    resolved = repo.resolve(None)
    assert resolved.commit == commit
    assert resolved.remote_ref == "refs/heads/trunk"
    assert resolved.requested_ref is None
    doc = repo.read_eip(commit, 9001)
    assert doc.markdown == FAKE_EIP_MARKDOWN  # the dirty working tree copy says "garbage"
    assert doc.blob_sha == git_blob_sha1(FAKE_EIP_MARKDOWN.encode())
    assert (clone / "EIPS" / "eip-9001.md").read_text() == "garbage"  # untouched


def test_repo_resolves_branch_name_and_sha(eips_clone):
    clone, commit = eips_clone
    repo = EipsRepo(clone)
    assert repo.resolve("trunk").commit == commit
    assert repo.resolve("trunk").remote_ref == "refs/heads/trunk"
    by_sha = repo.resolve(commit[:10])
    assert by_sha.commit == commit and by_sha.remote_ref is None


def test_repo_fetches_when_remote_moved_ahead(eips_clone, tmp_path):
    clone, old_commit = eips_clone
    origin = tmp_path / "origin"
    (origin / "EIPS" / "eip-9002.md").write_text(FAKE_EIP_MARKDOWN.replace("eip: 9001", "eip: 9002"), encoding="utf-8")
    git("add", ".", cwd=origin)
    git("commit", "--quiet", "-m", "Add EIP-9002", cwd=origin)
    new_commit = git("rev-parse", "HEAD", cwd=origin)
    repo = EipsRepo(clone)
    resolved = repo.resolve(None)
    assert resolved.commit == new_commit != old_commit
    assert repo.read_eip(new_commit, 9002).number == 9002
    assert git("rev-parse", "HEAD", cwd=clone) == old_commit  # checkout not moved


def test_missing_eip_and_bad_refs_fail_clearly(eips_clone):
    clone, commit = eips_clone
    repo = EipsRepo(clone)
    with pytest.raises(ComplexityError, match="EIP-4242: EIPS/eip-4242.md does not exist at commit"):
        repo.read_eip(commit, 4242)
    with pytest.raises(ComplexityError, match="not found on origin and is not a commit hash"):
        repo.resolve("no-such-branch")
    with pytest.raises(ComplexityError, match="not found locally or on origin"):
        repo.resolve("deadbeefcafe")


def test_not_a_repo_fails(tmp_path: Path):
    with pytest.raises(ComplexityError, match="does not exist"):
        EipsRepo(tmp_path / "missing")
    (tmp_path / "plain").mkdir()
    with pytest.raises(ComplexityError, match="not a git repository"):
        EipsRepo(tmp_path / "plain")
