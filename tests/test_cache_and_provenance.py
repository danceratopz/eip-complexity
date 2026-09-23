from __future__ import annotations

import json
from pathlib import Path

import pytest

from eip_complexity import RESULT_SCHEMA_VERSION
from eip_complexity.cache import EvaluationCache, cache_key
from eip_complexity.errors import ComplexityError
from eip_complexity.hashing import canonical_json, git_blob_sha1, sha256_json, sha256_text
from eip_complexity.template import load_template
from tests.conftest import TEMPLATE_PATH, git

BASE = dict(eip_sha256="e" * 64, template_sha256="t" * 64, question_set_sha256="q" * 64, model="jev-1.13.0")


def test_cache_key_changes_with_every_semantic_input():
    base = cache_key(**BASE)
    assert base == cache_key(**BASE)
    for field, value in [("eip_sha256", "f" * 64), ("template_sha256", "u" * 64), ("question_set_sha256", "r" * 64), ("model", "jev-1.14.0")]:
        assert cache_key(**{**BASE, field: value}) != base, field


def test_cache_store_and_load_round_trip(tmp_path: Path):
    cache = EvaluationCache(tmp_path / "cache")
    key = cache_key(**BASE)
    assert cache.load(key) is None
    evaluation = {"eip": {"number": 1}, "scores": {"total_score": 3}}
    path = cache.store(key, evaluation)
    assert path.name == f"{key}.json"
    assert cache.load(key) == evaluation
    entry = json.loads(path.read_text())
    assert entry["schema_version"] == RESULT_SCHEMA_VERSION and entry["cache_key"] == key


def test_malformed_cache_entries_fail(tmp_path: Path):
    cache = EvaluationCache(tmp_path)
    key = cache_key(**BASE)
    cache.path_for(key).write_text("{not json")
    with pytest.raises(ComplexityError, match="cannot read JSON"):
        cache.load(key)
    cache.path_for(key).write_text(json.dumps({"schema_version": RESULT_SCHEMA_VERSION, "cache_key": "other", "evaluation": {}}))
    with pytest.raises(ComplexityError, match="does not match its file name"):
        cache.load(key)
    cache.path_for(key).write_text(json.dumps({"schema_version": 99, "cache_key": key, "evaluation": {}}))
    with pytest.raises(ComplexityError, match="schema_version"):
        cache.load(key)


def test_canonical_json_and_hashes_are_order_independent():
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
    assert sha256_json({"b": 1, "a": 2}) == sha256_json({"a": 2, "b": 1})
    assert sha256_text("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_template_provenance_matches_git(tmp_path: Path):
    template, source = load_template(TEMPLATE_PATH)
    assert source.sha256 == sha256_text(TEMPLATE_PATH.read_text(encoding="utf-8"))
    assert source.blob_sha1 == git("hash-object", str(TEMPLATE_PATH), cwd=TEMPLATE_PATH.parent)
    assert source.git is not None
    assert source.git["relative_path"] == "templates/EIP-Complexity-Assessment.md"
    assert source.git["commit"] == git("rev-parse", "HEAD", cwd=TEMPLATE_PATH.parent)
    assert source.git["matches_head"] == (source.git["head_blob_sha"] == source.blob_sha1)
    as_dict = source.to_dict()
    assert as_dict["sha256"] == source.sha256 and as_dict["git_blob_sha1"] == source.blob_sha1


def test_template_outside_git_still_has_content_hash(tmp_path: Path):
    copy = tmp_path / "T.md"
    copy.write_text(TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    _, source = load_template(copy)
    assert source.git is None
    assert source.sha256 == sha256_text(copy.read_text(encoding="utf-8"))


def test_dirty_state_ignores_run_owned_directories(tmp_path: Path):
    from eip_complexity.gitinfo import describe_repo, public_git_info

    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "--quiet", "--initial-branch=main", cwd=repo)
    git("config", "user.email", "t@example.invalid", cwd=repo)
    git("config", "user.name", "Test", cwd=repo)
    (repo / "code.py").write_text("x = 1\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "result.json").write_text("{}\n")
    git("add", ".", cwd=repo)
    git("commit", "--quiet", "-m", "init", cwd=repo)

    assert describe_repo(repo)["dirty"] is False
    (repo / "docs" / "result.json").write_text('{"rewritten": true}\n')  # the run rewriting its own output
    assert describe_repo(repo)["dirty"] is True
    assert describe_repo(repo, exclude=[repo / "docs", tmp_path / "elsewhere"])["dirty"] is False
    (repo / "code.py").write_text("x = 2\n")  # a real code change still counts
    assert describe_repo(repo, exclude=[repo / "docs"])["dirty"] is True
    info = public_git_info(describe_repo(repo))
    assert set(info) == {"commit", "dirty", "remote_url"} and info["remote_url"] is None
    git("remote", "add", "origin", "https://github.com/o/r.git", cwd=repo)
    assert describe_repo(repo)["remote_url"] == "https://github.com/o/r.git"
    assert public_git_info(None) is None


def test_run_provenance_is_captured_once_and_has_no_local_paths(eips_clone, tmp_path: Path):
    import json

    from eip_complexity.run import run
    from tests.fake_jev import FakeJev
    from tests.test_run_mock import write_config

    clone, _ = eips_clone
    eips = '[[eips]]\nnumber = 9001\n[[eips]]\nnumber = 9002\n'
    origin = tmp_path / "origin"
    (origin / "EIPS" / "eip-9002.md").write_text((origin / "EIPS" / "eip-9001.md").read_text().replace("eip: 9001", "eip: 9002"))
    git("add", ".", cwd=origin)
    git("commit", "--quiet", "-m", "Add EIP-9002", cwd=origin)
    run_path = run(write_config(tmp_path, clone, eips=eips, render=False), client_factory=FakeJev)
    manifest = json.loads(run_path.read_text())
    states = []
    for entry in manifest["evaluations"]:
        document = json.loads((run_path.parent / entry["path"]).read_text())
        provenance = document["provenance"]
        assert "toplevel" not in (provenance["evaluator"]["git"] or {})
        assert "toplevel" not in (provenance["template"]["git"] or {})
        states.append((provenance["evaluator"]["git"], provenance["template"]["git"]))
    assert states[0] == states[1], "provenance is captured once per run, not per EIP"
    assert manifest["evaluator"]["git"] == states[0][0]
