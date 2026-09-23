"""End-to-end runs against a temporary EIPs clone with Jev replaced by a fake."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eip_complexity import RESULT_SCHEMA_VERSION
from eip_complexity.errors import ComplexityError
from eip_complexity.run import run
from tests.conftest import TEMPLATE_PATH
from tests.fake_jev import FakeJev


def write_config(tmp_path: Path, clone: Path, *, model: str = "jev-1.13.0", force: bool = False,
                 render: bool = True, eips: str = '[[eips]]\nnumber = 9001\nstage = "PFI"\n', ref: str = "") -> Path:
    config = tmp_path / "run.toml"
    config.write_text(
        f'fork = "Testfork"\n'
        f'meta_eip = 9000\n'
        f'eips_repo = "{clone}"\n'
        f'eips_ref = "{ref}"\n'
        f'complexity_template = "{TEMPLATE_PATH}"\n'
        f'jev_model = "{model}"\n'
        f'output_dir = "{tmp_path / "out"}"\n'
        f"render_html = {str(render).lower()}\n"
        f"force = {str(force).lower()}\n\n{eips}\n[human_assessments]\nrepo = \"example/pm\"\npath = \"assessments/EIPs\"\n",
        encoding="utf-8",
    )
    return config


def test_full_run_writes_manifest_evaluation_cache_and_html(eips_clone, tmp_path, monkeypatch):
    clone, commit = eips_clone
    monkeypatch.setenv("TYPESAFE_API_KEY", "not-a-real-key-and-must-never-appear")
    fake = FakeJev(anchor_score="1", overrides={"cross_eip_interactions": "2", "cross_eip_7702": "yes"})
    run_path = run(write_config(tmp_path, clone), client_factory=lambda: fake)

    assert len(fake.calls) == 1, "exactly one Jev request per EIP"
    payload = fake.calls[0]
    assert payload["model"] == "jev-1.13.0"
    assert list(payload["questions"])[-3:] == ["cross_eip_1559", "cross_eip_4844", "cross_eip_7702"]
    assert len(payload["questions"]) == 28 + 3
    assert "PFI" not in json.dumps(payload), "stage metadata must not reach Jev"
    assert fake.closed

    manifest = json.loads(run_path.read_text())
    assert manifest["schema_version"] == RESULT_SCHEMA_VERSION and manifest["kind"] == "eip-complexity-run"
    assert manifest["fork"] == "Testfork" and manifest["meta_eip"] == 9000
    assert manifest["human_assessments"] == {"repo": "example/pm", "path": "assessments/EIPs", "branch": "main", "pr_query": "is:pr complexity"}
    assert manifest["eips_repo"]["resolved_commit"] == commit
    assert manifest["eips_repo"]["remote_ref"] == "refs/heads/trunk"
    assert manifest["template"]["revision"] == 2 and manifest["template"]["anchor_count"] == 28
    assert manifest["jev"] == {"requested_model": "jev-1.13.0", "resolved_model": "jev-1.13.0", "sdk_version": manifest["jev"]["sdk_version"]}
    assert manifest["evaluator"]["question_set_sha256"]
    assert manifest["config"]["eips"] == [{"number": 9001, "stage": "PFI", "layer": None}]
    assert manifest["not_applicable_layers"] == ["consensus", "informational"]
    assert manifest["evaluations"][0]["status"] == "evaluated"
    [entry] = manifest["evaluations"]
    assert entry["path"] == "evaluations/eip-9001.json" and entry["cache_hit"] is False
    # 23 anchors define option "1"; the fake picks it for 22 of them, "2" for Cross-EIP, and the 5 sparse 0/3 anchors fall to 0.
    assert entry["total_score"] == 22 + 2
    evaluation_file = run_path.parent / entry["path"]
    document = json.loads(evaluation_file.read_text())
    assert document["kind"] == "eip-complexity-evaluation" and document["eip_number"] == 9001
    ev = document["evaluation"]
    assert ev["eip"]["title"] == "Fake Frame Widget" and ev["eip"]["requires"] == [1559, 7702]
    assert ev["template"]["sha256"] == manifest["template"]["sha256"]
    assert ev["jev"]["resolved_model"] == "jev-1.13.0" and ev["jev"]["usage"] == {"input_tokens": 1234, "output_tokens": 56}
    assert ev["request"]["payload"] == payload and len(ev["request"]["sha256"]) == 64
    assert ev["response"]["raw"]["answers"]["cross_eip_7702"]["choice"] == "yes"
    assert ev["rationale"] is None and ev["special_considerations"] is None and ev["notes"] is None
    cross = ev["scores"]["cross_eip"]
    assert cross["qualifying_interactions"] == 1 and cross["bonus"] == 0 and cross["final_score"] == 2
    assert [c["eip"] for c in cross["candidates"]] == [1559, 4844, 7702]
    assert document["provenance"]["eip_source"]["content_sha256"] == ev["eip"]["content_sha256"]
    assert document["provenance"]["template"]["git"]["relative_path"] == "templates/EIP-Complexity-Assessment.md"
    assert (run_path.parent / "cache" / f"{document['cache']['key']}.json").exists()
    html = (run_path.parent / "index.html").read_text()
    assert "EIP-9001" in html and "Fake Frame Widget" in html and "PFI" in html
    assert "not-a-real-key" not in html and "TYPESAFE_API_KEY=…" in html, "the variable name is shown, its value never"
    for path in (run_path.parent / "evaluations").glob("*.json"):
        assert "not-a-real-key" not in path.read_text()


def test_rerun_reuses_cache_and_force_bypasses_it(eips_clone, tmp_path):
    clone, _ = eips_clone
    first = FakeJev(anchor_score="1")
    run(write_config(tmp_path, clone), client_factory=lambda: first)
    second = FakeJev(anchor_score="3")
    run_path = run(write_config(tmp_path, clone), client_factory=lambda: second)
    assert second.calls == [], "cached evaluation must be reused"
    manifest = json.loads(run_path.read_text())
    assert manifest["evaluations"][0]["cache_hit"] is True
    document = json.loads((run_path.parent / "evaluations" / "eip-9001.json").read_text())
    assert document["cache"]["hit"] is True
    assert document["evaluation"]["scores"]["anchors"][0]["score"] == 1

    third = FakeJev(anchor_score="3")
    run(write_config(tmp_path, clone, force=True), client_factory=lambda: third)
    assert len(third.calls) == 1
    document = json.loads((run_path.parent / "evaluations" / "eip-9001.json").read_text())
    assert document["evaluation"]["scores"]["anchors"][0]["score"] == 3 and document["cache"]["hit"] is False


def test_cache_is_not_reused_across_models(eips_clone, tmp_path):
    clone, _ = eips_clone
    run(write_config(tmp_path, clone, model="jev-1.13.0"), client_factory=lambda: FakeJev(model="jev-1.13.0"))
    other = FakeJev(model="jev-1.14.0")
    run(write_config(tmp_path, clone, model="jev-1.14.0"), client_factory=lambda: other)
    assert len(other.calls) == 1


def test_alias_is_pinned_from_first_response_and_enforced(eips_clone, tmp_path):
    clone, _ = eips_clone
    eips = '[[eips]]\nnumber = 9001\n[[eips]]\nnumber = 9002\n'
    origin = tmp_path / "origin"
    (origin / "EIPS" / "eip-9002.md").write_text((origin / "EIPS" / "eip-9001.md").read_text().replace("eip: 9001", "eip: 9002"))
    from tests.conftest import git
    git("add", ".", cwd=origin)
    git("commit", "--quiet", "-m", "Add EIP-9002", cwd=origin)

    class Drifting(FakeJev):
        def evaluate(self, payload):
            self.model = "jev-1.13.0" if not self.calls else "jev-1.14.0"
            return super().evaluate(payload)

    with pytest.raises(ComplexityError, match="answered with model 'jev-1.14.0' but this run is pinned to 'jev-1.13.0'"):
        run(write_config(tmp_path, clone, model="jev-latest", eips=eips, render=False), client_factory=Drifting)
    # The first EIP was persisted before the failure.
    assert (tmp_path / "out" / "evaluations" / "eip-9001.json").exists()
    assert not (tmp_path / "out" / "run.json").exists()

    steady = FakeJev(model="jev-1.13.0")
    run_path = run(write_config(tmp_path, clone, model="jev-latest", eips=eips, render=False), client_factory=lambda: steady)
    # The alias is sent once, then the pinned concrete model. The drifting answer for 9002 was never cached.
    assert [call["model"] for call in steady.calls] == ["jev-latest", "jev-1.13.0"]
    manifest = json.loads(run_path.read_text())
    assert manifest["jev"]["requested_model"] == "jev-latest" and manifest["jev"]["resolved_model"] == "jev-1.13.0"
    assert [e["cache_hit"] for e in manifest["evaluations"]] == [False, False]

    # With an alias, the first EIP is always evaluated live to pin the model; the rest can come from cache.
    again = FakeJev(model="jev-1.13.0")
    run_path = run(write_config(tmp_path, clone, model="jev-latest", eips=eips, render=False), client_factory=lambda: again)
    assert [call["model"] for call in again.calls] == ["jev-latest"]
    assert [e["cache_hit"] for e in json.loads(run_path.read_text())["evaluations"]] == [False, True]

    # A pinned concrete model serves everything from cache.
    pinned = FakeJev(model="jev-1.13.0")
    run_path = run(write_config(tmp_path, clone, model="jev-1.13.0", eips=eips, render=False), client_factory=lambda: pinned)
    assert pinned.calls == []
    assert [e["cache_hit"] for e in json.loads(run_path.read_text())["evaluations"]] == [True, True]


def test_missing_eip_fails_before_any_jev_call(eips_clone, tmp_path):
    clone, _ = eips_clone
    fake = FakeJev()
    eips = '[[eips]]\nnumber = 9001\n[[eips]]\nnumber = 4242\n'
    with pytest.raises(ComplexityError, match="EIP-4242: EIPS/eip-4242.md does not exist"):
        run(write_config(tmp_path, clone, eips=eips), client_factory=lambda: fake)
    assert fake.calls == []


def test_duplicate_eips_in_config_fail(eips_clone, tmp_path):
    clone, _ = eips_clone
    eips = '[[eips]]\nnumber = 9001\n[[eips]]\nnumber = 9001\n'
    with pytest.raises(ComplexityError, match="duplicate EIP numbers \\[9001\\]"):
        run(write_config(tmp_path, clone, eips=eips), client_factory=FakeJev)


def test_not_applicable_layer_skips_jev(eips_clone, tmp_path):
    clone, _ = eips_clone
    eips = '[[eips]]\nnumber = 9001\nstage = "PFI"\nlayer = "execution"\n[[eips]]\nnumber = 9003\nstage = "PFI"\nlayer = "consensus"\n'
    fake = FakeJev()
    run_path = run(write_config(tmp_path, clone, eips=eips), client_factory=lambda: fake)
    assert len(fake.calls) == 1, "no Jev request for the consensus EIP"
    manifest = json.loads(run_path.read_text())
    na = next(e for e in manifest["evaluations"] if e["number"] == 9003)
    assert na["status"] == "not_applicable" and na["layer"] == "consensus" and na["path"] == "evaluations/eip-9003.not-applicable.json"
    assert "total_score" not in na
    document = json.loads((run_path.parent / na["path"]).read_text())
    assert document["kind"] == "eip-complexity-not-applicable" and document["eip"]["title"] == "Consensus Fixture"
    html = (run_path.parent / "index.html").read_text()
    assert "not applicable to the execution-layer checklist (consensus layer)" in html and 'class="badge layer"' not in html
    assert "1 not applicable" in html
    rows = html.split('<details class="row')[1:]
    assert 'id="eip-9001"' in rows[0] and 'id="eip-9003"' in rows[1], "scored rows first, not-applicable rows last"


def test_bad_layer_fails(tmp_path):
    config = tmp_path / "bad.toml"
    config.write_text('fork = "x"\noutput_dir = "o"\n[[eips]]\nnumber = 1\nlayer = "sidechain"\n')
    with pytest.raises(ComplexityError, match="'layer' must be one of"):
        run(config, client_factory=FakeJev)


def test_meta_eip_must_be_positive(tmp_path):
    config = tmp_path / "bad.toml"
    config.write_text('fork = "x"\noutput_dir = "o"\nmeta_eip = 0\n[[eips]]\nnumber = 1\n')
    with pytest.raises(ComplexityError, match="'meta_eip' must be a positive integer"):
        run(config, client_factory=FakeJev)


def test_human_assessments_repo_must_be_owner_name(tmp_path):
    config = tmp_path / "bad.toml"
    config.write_text('fork = "x"\noutput_dir = "o"\n[human_assessments]\nrepo = "pm"\n[[eips]]\nnumber = 1\n')
    with pytest.raises(ComplexityError, match="'repo' must be a GitHub 'owner/name'"):
        run(config, client_factory=FakeJev)


def test_unknown_config_key_fails(tmp_path):
    config = tmp_path / "bad.toml"
    config.write_text('fork = "x"\noutput_dir = "o"\nmodel = "typo"\n[[eips]]\nnumber = 1\n')
    with pytest.raises(ComplexityError, match="unknown keys \\['model'\\]"):
        run(config, client_factory=FakeJev)
