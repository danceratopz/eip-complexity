from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from eip_complexity.errors import ComplexityError
from eip_complexity.render import anchor_styles, load_run, render_html, render_run
from eip_complexity.run import run
from tests.fake_jev import FakeJev
from tests.test_run_mock import write_config


@pytest.fixture
def completed_run(eips_clone, tmp_path) -> Path:
    clone, _ = eips_clone
    fake = FakeJev(anchor_score="2", overrides={"modified_opcodes": "3", "cross_eip_interactions": "1"})
    return run(write_config(tmp_path, clone, render=False), client_factory=lambda: fake)


def test_render_from_json_only(completed_run, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("renderer must not open sockets")

    monkeypatch.setattr(socket, "socket", no_network)
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("renderer must not run git")))
    output = render_run(completed_run)
    assert output == completed_run.parent / "index.html"
    html = output.read_text()
    assert "<title>An Automated \u201cSystem One\u201d Evaluation of Testfork EIP Testing Complexity</title>" in html
    assert '<nav class="site-nav"' not in html, "no site.json beside the run directory, so no navigation bar"
    assert '<div class="notice" role="note"><span class="notice-mark" aria-hidden="true">!</span><div>Secondary run for comparison only. See <a href="../out/">the main run.</a></div></div>' in html
    assert "<b>This is an experimental study of Testfork EIP testing complexity</b>" in html
    assert '<p class="intro">If an EIP builds' not in html, "the caveat moved into the Limitations block"
    assert '<details class="how" id="limitations"><summary><b>Limitations</b>' in html
    assert "If an EIP builds on other EIPs that are themselves new to Testfork" in html and "already live on mainnet" in html
    assert "<h3>Methodology</h3>" not in html and "Limitations of this MVP" not in html
    assert "Jev does not produce prose." in html  # a recorded limitation from run.json, now inside the block
    assert "ethspecs EIP complexity evaluation template</a>.</p>" in html and ", and nothing else" not in html
    assert 'could help remove that bias, but Jev\'s <a href="https://docs.typesafe.ai/models">input window</a> is 32k tokens per request and the largest EIP here, EIP-9001, already fills about' in html
    assert "and the model cannot provide a written rationale for it.</p>" in html
    assert "<b>How to reproduce</b> <span class=\"muted\">· one Python command · one API request per EIP</span>" in html
    assert "by running a Python command, which sends one Jev API request per scored EIP" in html
    assert "An API key can be requested from" in html and "repeating the run in the same clone sends no further requests" in html
    assert "uv run eip-complexity run" in html and "not bit-for-bit identical between calls" in html
    assert "of the window), which leaves no room to send its dependencies with it" in html
    assert 'title="Jev\'s own measure of how concentrated the probabilities are' in html
    assert "The confidence is Jev's own measure of how concentrated the probabilities are" in html
    assert "Each EIP is scored using one Jev request consisting of a" in html
    assert '<a href="https://github.com/example/pm/tree/main/assessments/EIPs">merged evaluations</a>' in html
    assert '<a href="https://github.com/example/pm/pulls?q=is%3Apr+complexity">evaluations in progress or under review</a>' in html
    assert '<a href="https://github.com/example/pm/pulls?q=is%3Apr+EIP-9001">pull requests mentioning EIP-9001</a>' in html
    assert '<a href="https://github.com/example/pm/blob/main/assessments/EIPs/EIP-9001.md">committed file, if merged</a>' in html
    assert "<dt>Human assessments</dt>" in html
    assert ('for that EIP. A <a href="comparison.html">comparison</a> of these scores with the human assessments and with the LLM evaluations of the '
            '<a href="https://example.invalid/study">example study</a> is kept on a separate page, together with <a href="../out-old/">a rerun on old text</a>.</p>') in html
    import re as _re

    assert (_re.search(r'<a href="https://github\.com/[^"]+" title="Source code, configuration and canonical JSON results">source &amp; data: [^<]+</a>', html)
            or 'source &amp; data' not in html), "repo chip appears only when a GitHub remote is recorded"
    assert "<b>How a score is produced</b>" in html and "Definition from the template" in html and "← chosen" in html
    assert 'Exact request and response (JSON)</a>' in html and 'href="evaluations/eip-9001.json"' in html
    assert '<h2 id="criteria">Checklist criteria as sent to Jev</h2>' in html and 'id="criterion-modified_opcodes"' in html
    assert "No pre-existing opcode modifications are introduced." in html  # option text from the template, via the payload
    assert 'class="src"' not in html, "the fixture origin is a local path, so no GitHub permalink is offered"
    assert 'href="https://eips.ethereum.org/EIPS/eip-9001"' in html
    assert ">~OP<" in html  # modified opcodes segment label
    assert 'class="badge rev"' not in html, "no per-row revision badge"
    # Without a git remote the chip points at the in-page provenance; with one (CI, or a clone with origin) at the template blob.
    import re

    assert re.search(
        r'<a href="(#provenance|https://github\.com/[^"]+/blob/[0-9a-f]{40}/templates/EIP-Complexity-Assessment\.md)">'
        r"checklist revision 2 \(28 criteria\)</a>",
        html,
    ), "template chip must link to the provenance section or to the template blob at the assessed commit"
    assert '<a href="https://docs.typesafe.ai/introduction">Jev jev-1.13.0</a>' in html
    assert "live Jev calls" not in html.split("<h2 id=\"provenance\">")[0], "call counts belong in provenance, not the header"
    assert "1 live request(s), 0 evaluation(s) reused from cache" in html
    assert '<h2 id="provenance">' in html
    assert "Cross-EIP interactions" in html and "Provenance" in html
    assert "jev-1.13.0" in html
    assert "Rationale, special considerations and notes are not generated" in html


def test_render_accepts_directory_and_verifies_hashes(completed_run):
    run_dir = completed_run.parent
    assert render_run(run_dir).exists()
    evaluation = run_dir / "evaluations" / "eip-9001.json"
    evaluation.write_text(evaluation.read_text() + "\n")
    with pytest.raises(ComplexityError, match="does not match the sha256"):
        render_run(run_dir)


def test_rows_sorted_by_total_descending():
    manifest = {
        "kind": "eip-complexity-run", "fork": "F", "created_at": "t",
        "config": {"path": "c", "sha256": "s", "eips": []},
        "eips_repo": {"path": "p", "origin_url": "https://github.com/ethereum/EIPs", "requested_ref": None, "remote_ref": "refs/heads/master", "resolved_commit": "abc123def456"},
        "template": {"path": "T", "revision": 2, "anchor_count": 2, "sha256": "t" * 64, "git_blob_sha1": "b", "git": None},
        "evaluator": {"name": "eip-complexity", "version": "0.1.0", "git": None, "question_set_version": 1, "question_set_sha256": "q" * 64},
        "jev": {"requested_model": "jev-latest", "resolved_model": "jev-1.13.0", "sdk_version": "0.7.1"},
        "methodology": {"summary": "s", "limitations": ["l"]},
    }

    def evaluation(number, scores):
        anchors = [
            {"id": "added_opcodes", "name": "Added opcodes", "checklist_label": "Added opcodes", "score": scores[0], "choice": str(scores[0]),
             "probabilities": {"0": 0.1, "1": 0.9}, "confidence": 0.8, "criteria": {"0": "a", "1": "b"}},
            {"id": "security_risks", "name": "Security risks", "checklist_label": "Security risks", "score": scores[1], "choice": str(scores[1]),
             "probabilities": {"0": 0.2, "3": 0.8}, "confidence": 0.7, "criteria": {"0": "a", "3": "b"}},
        ]
        return {
            "manifest": {"number": number, "stage": "CFI", "total_score": sum(scores), "tier": {"name": "Low Complexity", "emoji": "🟢"}},
            "document": {"cache": {"hit": False}, "provenance": {"eip_source": {"path": f"EIPS/eip-{number}.md"}}, "evaluation": {
                "eip": {"title": f"Title {number}", "content_sha256": "c" * 64},
                "jev": {"resolved_model": "jev-1.13.0", "usage": {"input_tokens": 1}},
                "evaluated_at": "t",
                "scores": {"anchors": anchors, "cross_eip": None, "total_score": sum(scores), "tier": {"name": "Low Complexity", "emoji": "🟢"}},
            }},
        }

    html = render_html(manifest, [evaluation(1, [1, 0]), evaluation(2, [1, 3]), evaluation(3, [0, 3])])
    assert html.index("EIP-2") < html.index("EIP-3") < html.index("EIP-1")
    assert "Human assessment" not in html, "no human-assessment links without the config table"
    assert 'class="notice"' not in html, "no callout without a [notice] table"
    assert "CFI" in html and 'commit/abc123def456' in html
    assert '<a href="https://github.com/ethereum/EIPs/tree/abc123def456" title="ethereum/EIPs at the exact commit assessed">ethereum/EIPs @ abc123def4</a>' in html
    assert '<a class="src" href="https://github.com/ethereum/EIPs/blob/abc123def456/EIPS/eip-2.md" title="Markdown as assessed, at ethereum/EIPs commit abc123def4">src</a>' in html
    assert 'title="EIP-2 on eips.ethereum.org (latest)">EIP-2</a>' in html
    assert "<span>3 EIPs</span>" in html  # no meta_eip in this manifest: plain count
    manifest["meta_eip"] = 8081
    html = render_html(manifest, [evaluation(1, [1, 0])])
    assert '<a href="https://github.com/ethereum/EIPs/blob/abc123def456/EIPS/eip-8081.md" title="EIP-8081, the meta EIP this list was taken from, at the assessed commit">1 EIPs · EIP-8081</a>' in html


def test_expected_total_is_probability_weighted():
    from eip_complexity.render import expected_score, expected_total

    anchor = {"probabilities": {"0": 0.1, "1": 0.2, "2": 0.3, "3": 0.4, "4": 0.0}}
    assert abs(expected_score(anchor) - (0.2 + 0.6 + 1.2)) < 1e-9
    scores = {"anchors": [anchor, {"probabilities": {"0": 0.5, "3": 0.5}}], "cross_eip": {"bonus": 1}}
    assert abs(expected_total(scores) - (2.0 + 1.5 + 1)) < 1e-9
    assert expected_total({"anchors": [anchor], "cross_eip": None}) == expected_score(anchor)
    from eip_complexity.render import score_variance, total_sd

    certain = {"probabilities": {"0": 0.0, "3": 1.0}}
    assert score_variance(certain) == 0.0
    split = {"probabilities": {"1": 0.5, "3": 0.5}}
    assert abs(score_variance(split) - 1.0) < 1e-9  # mean 2, values ±1
    assert abs(total_sd({"anchors": [split, split, certain], "cross_eip": None}) - 2 ** 0.5) < 1e-9


def test_expected_column_is_rendered_and_sortable(completed_run):
    html = render_run(completed_run).read_text()
    assert '<button type="button" class="sort" data-key="expected"' in html
    assert 'data-expected="' in html and '<span class="expected" title="Probability-weighted total' in html
    assert '<span class="sd"> ± ' in html
    assert "<th class=\"num\" title=\"Probability-weighted total" in html  # per-criterion column in the details table
    assert "The Expected column is derived on this page" in html


def test_site_nav_is_rendered_from_site_json(completed_run):
    import json as _json

    docs_dir = completed_run.parent.parent
    (docs_dir / "site.json").write_text(_json.dumps({
        "title": "Test site", "repository": "https://github.com/example/site",
        "nav": [{"label": "Home", "href": "index.html"}, {"label": "Out", "href": "out/"}, {"label": "Other", "href": "other/"}],
    }))
    html = render_run(completed_run).read_text()
    assert '<nav class="site-nav" aria-label="Site"><a class="brand" href="../index.html">Test site</a>' in html
    assert '<a class="nav-link active" href="../out/">Out</a>' in html and '<a class="nav-link" href="../other/">Other</a>' in html
    assert '<a class="github" href="https://github.com/example/site"' in html and "<span>GitHub</span>" in html


def test_anchor_styles_are_stable_and_labelled(template):
    ids = [a.id for a in template.anchors]
    styles = anchor_styles(ids)
    assert styles["added_opcodes"]["abbr"] == "+OP" and styles["cross_eip_interactions"]["abbr"] == "XEIP"
    assert len({s["color"] for s in styles.values()}) == 28, "every anchor gets its own color"
    assert anchor_styles(ids[::-1])["security_risks"]["color"] == styles["security_risks"]["color"], "color follows the anchor, not its position"
    unknown = anchor_styles(["some_new_anchor"])["some_new_anchor"]
    assert unknown["abbr"] == "SNA" and unknown["family"] == "Other"


def test_rows_carry_sort_keys_and_headers_are_sort_buttons(completed_run):
    html = render_run(completed_run).read_text()
    assert 'data-number="9001"' in html and 'data-total="' in html and 'data-title="fake frame widget"' in html
    assert 'data-stage-rank="2"' in html  # PFI
    for key in ("number", "title", "stageRank", "total"):
        assert f'<button type="button" class="sort" data-key="{key}"' in html
    assert '<div id="rows">' in html and "<script>" in html
    assert 'class="tiles"' in html and "EIPs assessed" in html
    assert 'class="dist"' in html  # probability distribution mini-bars in the details
    assert '<a href="https://docs.typesafe.ai/concepts/system-one">System One</a> model' in html
    assert 'scored by <a href="https://docs.typesafe.ai/introduction">Jev</a> from each EIP' in html
    assert 'href="https://docs.typesafe.ai/primitives/choice"' in html
    assert "<p class=\"intro\">Expand a row to see every criterion's probabilities and confidence, and to open the exact request and response.</p>" in html
    assert 'id="filter"' in html and 'id="expand"' in html and 'id="collapse"' in html
    assert 'id="eip-9001"' in html and 'href="#eip-9001"' in html
    assert 'class="legend-item" data-criterion="cross_eip_interactions"' in html
    assert 'data-criterion="modified_opcodes"' in html.split('<div id="rows">')[1]  # segments carry their criterion
    assert "High Complexity <span class=\"mono\">&gt;=23</span>" in html


def test_stage_rank_orders_sfi_before_cfi_before_pfi():
    from eip_complexity.render import _STAGE_RANK

    assert _STAGE_RANK["SFI"] < _STAGE_RANK["CFI"] < _STAGE_RANK["PFI"] < _STAGE_RANK["DFI"]


def test_template_link_uses_github_blob_when_remote_is_known():
    from eip_complexity.render import template_url

    git = {"commit": "abc123", "relative_path": "templates/T.md", "dirty": False}
    assert template_url({"template": {"git": None}}) == "#provenance"
    assert template_url({"template": {"git": {**git, "remote_url": None}}}) == "#provenance"
    assert template_url({"template": {"git": {**git, "remote_url": "https://github.com/o/r.git"}}}) == "https://github.com/o/r/blob/abc123/templates/T.md"
    assert template_url({"template": {"git": {**git, "remote_url": "git@github.com:o/r.git"}}}) == "https://github.com/o/r/blob/abc123/templates/T.md"
    assert template_url({"template": {"git": {**git, "remote_url": "https://gitlab.example/o/r"}}}) == "#provenance"


def test_load_run_rejects_foreign_manifest(tmp_path):
    (tmp_path / "run.json").write_text(json.dumps({"kind": "other"}))
    with pytest.raises(ComplexityError, match="not an eip-complexity run manifest"):
        load_run(tmp_path)
