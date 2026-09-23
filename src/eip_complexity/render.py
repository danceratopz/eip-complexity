"""Render ``index.html`` from a completed run's canonical JSON.

This module knows nothing about Jev, git or the template. It reads ``run.json`` and the
per-EIP evaluation files it lists, verifies their hashes, and writes one self-contained
HTML page: an overview with one stacked anchor bar per EIP, a details view per EIP, a
legend and the run provenance.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from eip_complexity.cache import read_json
from eip_complexity.errors import ComplexityError
from eip_complexity.hashing import sha256_bytes

#: Presentation only. Unknown criteria fall back to initials and a neutral color.
ABBREVIATIONS = {
    "evm_gas_rule_changes": "GAS",
    "state_access_ordering_within_opcode_execution": "SAO",
    "blob_gas_accounting_changes": "BLOB",
    "state_gas_accounting_changes": "SGAS",
    "new_evm_gas_refund": "RFND",
    "patterns_affecting_pre_existing_tests": "PAT",
    "new_invariant_on_pre_existing_tests": "INV",
    "transition_tool_interface_changes": "T8N",
    "new_test_framework_primitives": "FWK",
    "cryptography": "CRYP",
    "edge_boundary_conditions": "EDGE",
    "block_syncing_changes": "SYNC",
    "engine_api_changes": "ENG",
    "added_system_contracts": "+SC",
    "modified_system_contracts": "~SC",
    "added_opcodes": "+OP",
    "modified_opcodes": "~OP",
    "added_precompiles": "+PC",
    "modified_precompiles": "~PC",
    "encoding_changes_rlp_ssz": "ENC",
    "new_transaction_types": "TX",
    "new_or_modified_transaction_validity_mechanisms": "TXV",
    "new_block_header_fields": "HDR",
    "new_fork_activation_mechanism": "FORK",
    "performance_risks": "PERF",
    "security_risks": "SEC",
    "unspecified_behavior_requiring_cross_client_consensus": "UNSP",
    "cross_eip_interactions": "XEIP",
}

#: Criteria families share a hue; members are distinguished by lightness and by their label.
FAMILIES: list[tuple[str, str, list[str]]] = [
    ("Gas", "#2a78d6", ["evm_gas_rule_changes", "state_access_ordering_within_opcode_execution",
                        "blob_gas_accounting_changes", "state_gas_accounting_changes", "new_evm_gas_refund"]),
    ("Tests & tooling", "#eb6834", ["patterns_affecting_pre_existing_tests", "new_invariant_on_pre_existing_tests",
                                    "transition_tool_interface_changes", "new_test_framework_primitives"]),
    ("Crypto, edges, sync, engine", "#1baf7a", ["cryptography", "edge_boundary_conditions",
                                                "block_syncing_changes", "engine_api_changes"]),
    ("System contracts", "#eda100", ["added_system_contracts", "modified_system_contracts"]),
    ("Opcodes", "#e87ba4", ["added_opcodes", "modified_opcodes"]),
    ("Precompiles", "#008300", ["added_precompiles", "modified_precompiles"]),
    ("Transactions, blocks, forks", "#4a3aa7", ["encoding_changes_rlp_ssz", "new_transaction_types",
                                                "new_or_modified_transaction_validity_mechanisms",
                                                "new_block_header_fields", "new_fork_activation_mechanism"]),
    ("Risks & interactions", "#e34948", ["performance_risks", "security_risks",
                                         "unspecified_behavior_requiring_cross_client_consensus",
                                         "cross_eip_interactions"]),
]
_FALLBACK_COLOR = "#8a8a86"
JEV_DOCS_URL = "https://docs.typesafe.ai/introduction"
#: Jev 1.13's window for the state plus the longest question, quoted on the page next to the largest EIP.
JEV_STATE_TOKEN_LIMIT = 32_000
JEV_MODELS_URL = "https://docs.typesafe.ai/models"  # source for the window size and the price
#: Jev's published price per million input tokens (output tokens are free), used only for the reproduction note.
JEV_USD_PER_MILLION_INPUT_TOKENS = 0.042
JEV_CHOICE_URL = "https://docs.typesafe.ai/primitives/choice"
SYSTEM_ONE_URL = "https://docs.typesafe.ai/concepts/system-one"
_BAR_WIDTH_PX = 720
_MIN_LABEL_PX = 24


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _mix_with_white(color: str, amount: float) -> str:
    r, g, b = _hex_to_rgb(color)
    mix = lambda c: round(c + (255 - c) * amount)  # noqa: E731
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


def _text_color_for(background: str) -> str:
    r, g, b = _hex_to_rgb(background)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#0b0b0b" if luminance > 150 else "#ffffff"


def _shade_steps(count: int) -> list[float]:
    if count <= 1:
        return [0.0]
    return [i * 0.6 / (count - 1) for i in range(count)]


def anchor_styles(anchor_ids: list[str]) -> dict[str, dict]:
    """``{anchor_id: {"abbr", "family", "color", "text"}}`` in a fixed, entity-bound order."""
    styles: dict[str, dict] = {}
    for family, base, members in FAMILIES:
        present = [m for m in members if m in anchor_ids]
        for member, step in zip(present, _shade_steps(len(present))):
            color = _mix_with_white(base, step)
            styles[member] = {"family": family, "color": color, "text": _text_color_for(color)}
    unknown = [a for a in anchor_ids if a not in styles]
    for member, step in zip(unknown, _shade_steps(len(unknown))):
        color = _mix_with_white(_FALLBACK_COLOR, step)
        styles[member] = {"family": "Other", "color": color, "text": _text_color_for(color)}
    for anchor_id in anchor_ids:
        styles[anchor_id]["abbr"] = ABBREVIATIONS.get(anchor_id) or "".join(w[0] for w in anchor_id.split("_")).upper()[:4]
    return styles


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _probabilities_text(probabilities: dict[str, float], choice: str) -> str:
    parts = []
    for option in sorted(probabilities, key=lambda o: (len(o), o)):
        p = probabilities[option]
        text = f"{option}: {p:.2f}"
        parts.append(f"<b>{_esc(text)}</b>" if option == choice else _esc(text))
    return " · ".join(parts)


def load_run(run_path: Path) -> tuple[Path, dict, list[dict]]:
    """Read ``run.json`` and every evaluation it lists, verifying the recorded hashes."""
    run_path = Path(run_path)
    if run_path.is_dir():
        run_path = run_path / "run.json"
    manifest = read_json(run_path, where="run manifest")
    if manifest.get("kind") != "eip-complexity-run":
        raise ComplexityError(f"{run_path} is not an eip-complexity run manifest")
    run_dir = run_path.parent
    evaluations = []
    for entry in manifest.get("evaluations", []):
        path = run_dir / entry["path"]
        data = path.read_bytes() if path.exists() else None
        if data is None:
            raise ComplexityError(f"evaluation file {path} listed in run.json is missing")
        if sha256_bytes(data) != entry["sha256"]:
            raise ComplexityError(f"evaluation file {path} does not match the sha256 recorded in run.json")
        document = json.loads(data)
        expected_kind = "eip-complexity-not-applicable" if entry.get("status") == "not_applicable" else "eip-complexity-evaluation"
        if document.get("kind") != expected_kind or document.get("eip_number") != entry["number"]:
            raise ComplexityError(f"file {path} does not describe EIP-{entry['number']} as {expected_kind}")
        evaluations.append({"manifest": entry, "document": document})
    return run_path, manifest, evaluations


def is_evaluated(item: dict) -> bool:
    return item["manifest"].get("status", "evaluated") == "evaluated"


def _anchor_order(items: list[dict]) -> list[dict]:
    evaluations = [i for i in items if is_evaluated(i)]
    if not evaluations:
        raise ComplexityError("run.json lists no evaluated EIPs; nothing to render")
    first = evaluations[0]["document"]["evaluation"]["scores"]["anchors"]
    order = [(a["id"], a["name"]) for a in first]
    for item in evaluations[1:]:
        anchors = item["document"]["evaluation"]["scores"]["anchors"]
        if [(a["id"], a["name"]) for a in anchors] != order:
            raise ComplexityError(f"EIP-{item['manifest']['number']} has a different anchor set than the first evaluation")
    return [{"id": i, "name": n} for i, n in order]


_STAGE_RANK = {"SFI": 0, "CFI": 1, "PFI": 2, "DFI": 3}
_TIER_CLASS = {"Low Complexity": "low", "Medium Complexity": "medium", "High Complexity": "high"}


def expected_score(anchor: dict) -> float:
    """Probability-weighted score of one criterion: sum of option value x probability (options are numeric strings)."""
    return sum(int(option) * probability for option, probability in anchor["probabilities"].items())


def score_variance(anchor: dict) -> float:
    """Variance of one criterion's score under Jev's distribution."""
    mean = expected_score(anchor)
    return max(0.0, sum(int(o) ** 2 * p for o, p in anchor["probabilities"].items()) - mean ** 2)


def expected_total(scores: dict) -> float:
    """Sum of expected criterion scores plus the recorded Cross-EIP bonus, which is a deterministic template rule."""
    total = sum(expected_score(anchor) for anchor in scores["anchors"])
    cross = scores.get("cross_eip")
    return total + (cross["bonus"] if cross else 0)


def total_sd(scores: dict) -> float:
    """Standard deviation of the total, treating the criteria as independent (square root of the summed variances)."""
    return sum(score_variance(anchor) for anchor in scores["anchors"]) ** 0.5


EXPECTED_TITLE = (
    "Probability-weighted total ± standard deviation: for each criterion the option values weighted by Jev\'s "
    "probabilities, summed over all criteria, plus the recorded Cross-EIP bonus; the ± is the square root of the summed "
    "per-criterion variances, treating criteria as independent. Derived on this page from the stored probabilities; not a Jev output."
)


def _distribution_bar(probabilities: dict[str, float], choice: str) -> str:
    """A tiny stacked bar of the option probabilities; the chosen option is emphasised."""
    parts = []
    for option in sorted(probabilities, key=lambda o: (len(o), o)):
        p = probabilities[option]
        cls = "dist-seg chosen" if option == choice else "dist-seg"
        parts.append(f'<i class="{cls}" style="flex-grow:{max(p, 0.0) * 1000:.0f}"></i>')
    return f'<span class="dist" aria-hidden="true">{"".join(parts)}</span>'


def _render_row(item: dict, styles: dict[str, dict], max_total: int, manifest: dict | None = None) -> str:
    entry, document = item["manifest"], item["document"]
    evaluation = document["evaluation"]
    scores = evaluation["scores"]
    number, title = entry["number"], evaluation["eip"]["title"]
    total = scores["total_score"]
    bar_width = 100.0 * total / max_total if max_total else 0.0
    segments = []
    for anchor in scores["anchors"]:
        score = anchor["score"]
        if score <= 0:
            continue
        style = styles[anchor["id"]]
        px = _BAR_WIDTH_PX * score / max_total if max_total else 0
        label = style["abbr"] if px >= _MIN_LABEL_PX else ""
        tooltip = (
            f"{anchor['name']}: {score} (confidence {anchor['confidence']:.2f}; "
            + ", ".join(f"{o}={p:.2f}" for o, p in sorted(anchor["probabilities"].items(), key=lambda kv: (len(kv[0]), kv[0])))
            + ")"
        )
        if "bonus" in anchor:
            tooltip += f" — base {anchor['base_score']} + uncapped bonus {anchor['bonus']}"
        segments.append(
            f'<span class="seg" data-criterion="{_esc(anchor["id"])}" style="flex-grow:{score};background:{style["color"]};color:{style["text"]}" '
            f'title="{_esc(tooltip)}" aria-label="{_esc(tooltip)}">{_esc(label)}</span>'
        )
    stage = entry.get("stage")
    stage_html = f'<span class="badge stage-{_esc(stage.lower())}">{_esc(stage)}</span>' if stage else '<span class="muted">–</span>'
    tier = scores["tier"]
    tier_class = _TIER_CLASS.get(tier["name"], "other")
    rows = []
    for anchor in scores["anchors"]:
        style = styles[anchor["id"]]
        extra = f' <span class="muted small">(base {anchor["base_score"]} + bonus {anchor["bonus"]})</span>' if "bonus" in anchor else ""
        score_cls = "num score-zero" if anchor["score"] == 0 else "num"
        rows.append(
            "<tr>"
            f'<td><span class="swatch" style="background:{style["color"]}"></span>{_esc(anchor["name"])}</td>'
            f'<td class="mono">{_esc(style["abbr"])}</td>'
            f'<td class="{score_cls}">{anchor["score"]}{extra}</td>'
            f'<td class="num muted">{expected_score(anchor):.2f} <span class="small">± {score_variance(anchor) ** 0.5:.2f}</span></td>'
            f'<td class="num">{anchor["confidence"]:.2f}</td>'
            f'<td class="distcell">{_distribution_bar(anchor["probabilities"], anchor["choice"])}'
            f'<span class="mono small">{_probabilities_text(anchor["probabilities"], anchor["choice"])}</span></td>'
            "</tr>"
        )
    cross = scores.get("cross_eip")
    cross_html = ""
    if cross:
        helper_rows = "".join(
            "<tr>"
            f'<td><a href="https://eips.ethereum.org/EIPS/eip-{c["eip"]}">EIP-{c["eip"]}</a></td>'
            f'<td>{_esc(", ".join(c["referenced_via"]))}</td>'
            f'<td>{"<b>yes</b>" if c["requires_coordinated_tests"] else "no"}</td>'
            f'<td class="num">{c["probabilities"].get("yes", 0.0):.2f}</td>'
            f'<td class="num">{c["confidence"]:.2f}</td>'
            "</tr>"
            for c in cross["candidates"]
        ) or '<tr><td colspan="5" class="muted">No candidate EIPs are referenced by this EIP\'s source.</td></tr>'
        rule = cross["rule"]
        cross_html = (
            "<h4>Cross-EIP interactions</h4>"
            f'<p class="small">Base score {cross["base_score"]}, {cross["qualifying_interactions"]} qualifying '
            f'interaction(s) out of {len(cross["candidates"])} candidate(s), bonus +{cross["bonus"]} '
            f'(+{rule["increment"]} per {rule["per_additional"]} beyond the first {rule["beyond_first"]}), '
            f'final score {cross["final_score"]}.</p>'
            '<table class="detail"><thead><tr><th>Candidate</th><th>Referenced via</th>'
            '<th>Needs coordinated tests</th><th class="num">p(yes)</th><th class="num" title="Jev\'s own measure of how concentrated the probabilities are: 1 means all probability on one option, lower means it was split. Returned with each answer; not the chance that the score is right.">Confidence</th></tr></thead>'
            f"<tbody>{helper_rows}</tbody></table>"
        )
    jev = evaluation["jev"]
    usage = jev.get("usage") or {}
    payload = evaluation.get("request", {}).get("payload", {})
    state = payload.get("state", {})
    markdown_chars = len(state.get("eip_markdown", "")) if isinstance(state, dict) else 0
    question_count = len(payload.get("questions", {}))
    helper_count = sum(1 for q in payload.get("questions", {}) if str(q).startswith("cross_eip_"))
    meta = (
        f'model {_esc(jev["resolved_model"])} · input tokens {_esc(usage.get("input_tokens", "n/a"))} · '
        f'evaluated {_esc(evaluation["evaluated_at"])} · '
        f'source sha256 {_esc(evaluation["eip"]["content_sha256"][:12])}… · '
        f'{"cache hit" if document["cache"]["hit"] else "live Jev call"}'
    )
    json_link = f' <a href="{_esc(entry["path"])}">Exact request and response (JSON)</a>.' if entry.get("path") else ""
    md_link = source_permalink(manifest, (document.get("provenance", {}).get("eip_source") or {}).get("path"))
    md_text = f'<a href="{_esc(md_link)}">this EIP\'s Markdown at the assessed commit</a>' if md_link else "this EIP\'s Markdown"
    sent_line = (
        f'<p class="small">Sent to Jev: {md_text} ({markdown_chars:,} characters) as the state, and {question_count} questions '
        f'({question_count - helper_count} criteria, {helper_count} cross-EIP candidates).{json_link}</p>'
        if payload else ""
    )
    links = human_links(manifest, number) if manifest else {}
    human_line = (
        f'<p class="small muted">Human assessment in {_esc(links["repo"])}: '
        f'<a href="{_esc(links["eip_pulls"])}">pull requests mentioning EIP-{number}</a> · '
        f'<a href="{_esc(links["eip_file"])}">committed file, if merged</a>.</p>'
        if links else ""
    )
    source_path = (document.get("provenance", {}).get("eip_source") or {}).get("path")
    expected = expected_total(scores)
    sd = total_sd(scores)
    return (
        f'<details class="row" id="eip-{number}" data-number="{number}" data-title="{_esc(title.lower())}" '
        f'data-stage-rank="{_STAGE_RANK.get((stage or "").upper(), 9)}" data-total="{total}" data-expected="{expected:.3f}">'
        '<summary class="overview">'
        f"{_eip_cell(number, f'eip-{number}', manifest, source_path)}"
        f'<span class="title" title="{_esc(title)}">{_esc(title)}</span>'
        f'<span class="badges">{stage_html}</span>'
        f'<span class="barwrap"><span class="bar" style="width:{bar_width:.2f}%">{"".join(segments)}</span></span>'
        f'<span class="total"><span class="tier tier-{tier_class}" title="{_esc(tier["name"])}">{_esc(tier["emoji"])}</span>{total}</span>'
        f'<span class="expected" title="{EXPECTED_TITLE}">{expected:.1f}<span class="sd"> ± {sd:.1f}</span></span>'
        "</summary>"
        '<div class="detail-body">'
        f'<p class="small muted">{meta}</p>'
        f"{sent_line}"
        f"{human_line}"
        '<table class="detail"><thead><tr><th>Criterion</th><th>Abbr</th><th class="num">Score</th>'
        f'<th class="num" title="{EXPECTED_TITLE}">Expected</th>'
        '<th class="num" title="Jev\'s own measure of how concentrated the probabilities are: 1 means all probability on one option, lower means it was split. Returned with each answer; not the chance that the score is right.">Confidence</th>'
        "<th>Probability by option</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table>'
        f"{cross_html}"
        '<p class="small muted">Rationale, special considerations and notes are not generated by this tool.</p>'
        "</div></details>"
    )


def _tier_ranges(evaluations: list[dict]) -> dict[str, str]:
    """Tier name -> range text (e.g. ``>=23``), taken from the evaluations' own tier records."""
    ranges: dict[str, str] = {}
    for item in evaluations:
        tier = item["document"]["evaluation"]["scores"]["tier"]
        ranges.setdefault(tier["name"], tier.get("range", ""))
    return ranges


def _render_na_row(item: dict, manifest: dict | None = None) -> str:
    entry, document = item["manifest"], item["document"]
    number, title = entry["number"], entry["title"]
    stage = entry.get("stage")
    badges = f'<span class="badge stage-{_esc(stage.lower())}">{_esc(stage)}</span>' if stage else ""
    layer = entry.get("layer") or "unknown"
    layer_text = {"consensus": "consensus layer", "informational": "informational EIP"}.get(layer, f"layer: {layer}")
    na_text = f"not applicable to the execution-layer checklist ({layer_text})"
    return (
        f'<details class="row na" id="eip-{number}" data-number="{number}" data-title="{_esc(title.lower())}" '
        f'data-stage-rank="{_STAGE_RANK.get((stage or "").upper(), 9)}" data-total="-1">'
        '<summary class="overview">'
        f"{_eip_cell(number, f'eip-{number}', manifest, (document.get('eip') or {}).get('source_path'))}"
        f'<span class="title" title="{_esc(title)}">{_esc(title)}</span>'
        f'<span class="badges">{badges}</span>'
        f'<span class="barwrap na-text" title="{_esc(document.get("reason", na_text))}">{_esc(na_text)}</span>'
        '<span class="total muted">n/a</span>'
        '<span class="expected muted">–</span>'
        "</summary>"
        f'<div class="detail-body"><p class="small muted">{_esc(document.get("reason", ""))}</p>'
        '<p class="small muted">The layer is configuration, not a model judgment. No Jev request was made for this EIP.</p></div></details>'
    )


def _render_summary(manifest: dict, evaluations: list[dict], na_count: int = 0) -> str:
    counts: dict[str, int] = {}
    for item in evaluations:
        counts[item["manifest"]["tier"]["name"]] = counts.get(item["manifest"]["tier"]["name"], 0) + 1
    ranges = _tier_ranges(evaluations)
    tiles = []
    for name, emoji in (("High Complexity", "🔴"), ("Medium Complexity", "🟡"), ("Low Complexity", "🟢")):
        range_text = f' <span class="mono">{_esc(ranges[name])}</span>' if ranges.get(name) else ""
        tiles.append(
            f'<div class="tile tier-{_TIER_CLASS[name]}"><div class="tile-value">{counts.get(name, 0)}</div>'
            f'<div class="tile-label">{emoji} {_esc(name)}{range_text}</div></div>'
        )
    totals = [item["manifest"]["total_score"] for item in evaluations]
    label = f"EIPs assessed · median total {sorted(totals)[len(totals) // 2] if totals else 0}"
    if na_count:
        label += f" · {na_count} not applicable"
    tiles.append(f'<div class="tile"><div class="tile-value">{len(evaluations)}</div><div class="tile-label">{label}</div></div>')
    return f'<div class="tiles">{"".join(tiles)}</div>'


def _render_legend(anchors: list[dict], styles: dict[str, dict]) -> str:
    groups: dict[str, list[str]] = {}
    for anchor in anchors:
        groups.setdefault(styles[anchor["id"]]["family"], []).append(anchor["id"])
    names = {anchor["id"]: anchor["name"] for anchor in anchors}
    blocks = []
    for family, ids in groups.items():
        items = "".join(
            f'<li class="legend-item" data-criterion="{_esc(i)}" role="button" tabindex="0" title="Highlight this criterion in every bar">'
            f'<span class="swatch" style="background:{styles[i]["color"]}"></span>'
            f'<span class="mono abbr">{_esc(styles[i]["abbr"])}</span> {_esc(names[i])}</li>'
            for i in ids
        )
        blocks.append(f'<div class="legend-group"><h4>{_esc(family)}</h4><ul>{items}</ul></div>')
    return f'<div class="legend">{"".join(blocks)}</div>'


def template_url(manifest: dict) -> str:
    """A link to the exact template used: its GitHub blob when the remote is known, else the provenance section."""
    git = manifest["template"].get("git") or {}
    remote, commit, relative = git.get("remote_url"), git.get("commit"), git.get("relative_path")
    if remote and commit and relative and _github_base(remote):
        return f"{_github_base(remote)}/blob/{commit}/{relative}"
    return "#provenance"


def _github_base(remote_url: str) -> str | None:
    base = remote_url.removesuffix(".git")
    if base.startswith("git@github.com:"):
        base = "https://github.com/" + base[len("git@github.com:"):]
    return base if base.startswith("https://github.com/") else None


def _render_provenance(manifest: dict, evaluations: list[dict]) -> str:
    repo, template, evaluator, jev = manifest["eips_repo"], manifest["template"], manifest["evaluator"], manifest["jev"]
    live = sum(1 for item in evaluations if not item["document"]["cache"]["hit"])
    template_git = template.get("git") or {}
    evaluator_git = evaluator.get("git") or {}

    def commit_link(url: str | None, sha: str | None) -> str:
        if not sha:
            return "n/a"
        base = _github_base(url) if url else None
        if base:
            return f'<a href="{_esc(base)}/commit/{_esc(sha)}" class="mono">{_esc(sha)}</a>'
        return f'<span class="mono">{_esc(sha)}</span>'

    template_link = template_url(manifest)
    template_label = _esc(template["path"])
    if template_link != "#provenance":
        template_label = f'<a href="{_esc(template_link)}">{template_label}</a>'
    rows = [
        ("Fork", _esc(manifest["fork"])),
        ("Run created", _esc(manifest["created_at"])),
        ("Config", f'{_esc(manifest["config"]["path"])} <span class="mono small">sha256 {_esc(manifest["config"]["sha256"])}</span>'),
        ("EIPs repository", f'{_esc(repo["origin_url"])} (local clone {_esc(repo["path"])})'),
        ("EIPs requested ref", _esc(repo.get("requested_ref") or f"remote default branch ({repo.get('remote_ref')})")),
        ("EIPs resolved commit", commit_link(repo["origin_url"], repo["resolved_commit"])),
        ("Template", f'{template_label}, checklist revision {template["revision"]}, {template["anchor_count"]} criteria'),
        ("Template sha256", f'<span class="mono">{_esc(template["sha256"])}</span>'),
        ("Template git", (
            f'blob {_esc(template.get("git_blob_sha1"))}; repository commit '
            f'{commit_link(template_git.get("remote_url"), template_git.get("commit"))}; '
            f'dirty tree: {_esc(template_git.get("dirty"))}; matches HEAD: {_esc(template_git.get("matches_head"))}'
        ) if template_git else f'blob {_esc(template.get("git_blob_sha1"))} (not in a git repository)'),
        ("Evaluator", (
            f'{_esc(evaluator["name"])} {_esc(evaluator["version"])}; commit '
            f'{commit_link(evaluator_git.get("remote_url"), evaluator_git.get("commit"))}; dirty tree: {_esc(evaluator_git.get("dirty"))}'
            + (f'; repository {_esc(evaluator_git["remote_url"])}' if evaluator_git.get("remote_url") else "")
        )),
        ("Question set", f'version {evaluator["question_set_version"]}, sha256 <span class="mono">{_esc(evaluator["question_set_sha256"])}</span>'),
        ("Jev model", f'requested {_esc(jev["requested_model"])}, resolved {_esc(jev["resolved_model"])}; typesafe-sdk {_esc(jev["sdk_version"])}'),
        ("Jev calls", f'{live} live request(s), {len(evaluations) - live} evaluation(s) reused from cache in this run'),
    ]
    if links := human_links(manifest):
        rows.append(("Human assessments", f'<a href="{_esc(links["directory"])}">{_esc(links["repo"])}/{_esc(manifest["human_assessments"]["path"])}</a> '
                                          f'and <a href="{_esc(links["pulls"])}">pull requests</a> ({_esc(manifest["human_assessments"]["pr_query"])})'))
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    return f'<div class="card prov-card"><dl class="prov">{dl}</dl></div>'


_CSS = """
:root {
  color-scheme: light dark;
  --surface: #f6f6f4; --card: #ffffff; --card-2: #f3f2ef; --ink: #14140f; --ink-2: #55544f; --ink-3: #8a8a86;
  --line: #e4e3df; --line-2: #d3d2cd; --accent: #2a78d6; --track: #ebeae6; --hover: #f7f7f4; --open: #f1f0ec;
  --focus: #2a78d6; --dist: #cfd8e6; --dist-chosen: #2a78d6; --notice-bg: #fff6df; --notice-line: #d9a224;
  --tier-high-bg: #fbe3e3; --tier-high-ink: #8c1f1f; --tier-medium-bg: #fff1cc; --tier-medium-ink: #7a5300; --tier-low-bg: #dff4e6; --tier-low-ink: #12603a;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #161614; --card: #1e1e1b; --card-2: #262622; --ink: #f2f2ee; --ink-2: #bdbcb4; --ink-3: #8a8a86;
    --line: #30302c; --line-2: #3d3d38; --accent: #6ea8f0; --track: #2b2b27; --hover: #232320; --open: #292926;
    --dist: #3a4352; --dist-chosen: #6ea8f0; --notice-bg: #3a3116; --notice-line: #c99a2e;
    --tier-high-bg: #4a1d1d; --tier-high-ink: #ffb3b3; --tier-medium-bg: #4a3a0e; --tier-medium-ink: #ffd97a; --tier-low-bg: #17402a; --tier-low-ink: #9fe3bb;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; padding: 28px 16px 56px; background: var(--surface); color: var(--ink); font: 14px/1.5 -apple-system, "Segoe UI", Inter, Roboto, "Helvetica Neue", sans-serif; }
main { max-width: 1320px; margin: 0 auto; }
a { color: var(--accent); } a:hover { text-decoration: underline; }
h1 { font-size: 24px; letter-spacing: -0.01em; margin: 0 0 6px; } h2 { font-size: 17px; margin: 36px 0 10px; } h3 { font-size: 14px; margin: 22px 0 6px; } h4 { font-size: 12.5px; margin: 18px 0 6px; color: var(--ink-2); text-transform: uppercase; letter-spacing: .05em; }
.subtitle { margin: 0 0 14px; color: var(--ink-2); }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }
.chips span { font-size: 12.5px; padding: 4px 10px; border: 1px solid var(--line-2); border-radius: 999px; background: var(--card); color: var(--ink-2); }
.chips a { text-decoration: none; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.small { font-size: 12px; } .muted { color: var(--ink-3); } .num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin: 0 0 22px; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.tile-value { font-size: 26px; font-weight: 700; line-height: 1.1; font-variant-numeric: tabular-nums; }
.tile-label { color: var(--ink-2); font-size: 12.5px; margin-top: 2px; }
.tile.tier-high .tile-value { color: var(--tier-high-ink); } .tile.tier-medium .tile-value { color: var(--tier-medium-ink); } .tile.tier-low .tile-value { color: var(--tier-low-ink); }
.table { overflow: hidden; }
.header-row, .overview { display: grid; grid-template-columns: 150px minmax(180px, 1.1fr) 64px minmax(260px, 3fr) 84px 104px; gap: 12px; align-items: center; padding: 0 14px; }
.expected { text-align: right; font-variant-numeric: tabular-nums; color: var(--ink-2); white-space: nowrap; } .expected .sd { color: var(--ink-3); font-size: 11.5px; }
.header-row { position: sticky; top: 0; z-index: 2; background: var(--card-2); border-bottom: 1px solid var(--line); color: var(--ink-2); font-size: 11.5px; text-transform: uppercase; letter-spacing: .05em; min-height: 38px; }
.header-row .num { justify-self: end; }
button.sort { all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 4px; padding: 10px 0; color: inherit; font: inherit; text-transform: inherit; letter-spacing: inherit; border-radius: 4px; }
button.sort:hover, button.sort.active { color: var(--ink); } button.sort:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
button.sort .arrow { display: inline-block; width: 1em; font-size: 10px; color: var(--accent); }
.hint { font-weight: 400; text-transform: none; letter-spacing: 0; color: var(--ink-3); }
details.row { border-bottom: 1px solid var(--line); } details.row:last-child { border-bottom: 0; }
summary.overview { list-style: none; cursor: pointer; min-height: 44px; padding-top: 8px; padding-bottom: 8px; transition: background .12s; }
summary.overview::-webkit-details-marker { display: none; }
summary.overview:hover { background: var(--hover); } summary.overview:focus-visible { outline: 2px solid var(--focus); outline-offset: -2px; }
details.row[open] > summary.overview { background: var(--open); }
.eip { white-space: nowrap; } .eip a { text-decoration: none; font-weight: 600; font-variant-numeric: tabular-nums; }
.title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.badges { display: flex; gap: 4px; }
.badge { display: inline-block; font-size: 11px; font-weight: 600; line-height: 1; padding: 5px 7px; border-radius: 5px; background: var(--card-2); color: var(--ink-2); white-space: nowrap; letter-spacing: .02em; }
.badge.stage-sfi { background: #dff4ea; color: #146b47; } .badge.stage-cfi { background: #dfeafa; color: #1c4f96; } .badge.stage-pfi { background: #fbe9dd; color: #8a3a15; } .badge.stage-dfi { background: #f6e0e0; color: #8c2626; }
.barwrap.na-text { background: none; color: var(--ink-3); font-size: 12px; line-height: 22px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.barwrap { display: block; width: 100%; background: var(--track); border-radius: 5px; height: 22px; }
.bar { display: flex; gap: 2px; height: 22px; }
.seg { display: flex; align-items: center; justify-content: center; min-width: 3px; font: 600 10.5px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: .02em; white-space: nowrap; }
.seg:first-child { border-radius: 5px 0 0 5px; } .seg:last-child { border-radius: 0 5px 5px 0; } .seg:only-child { border-radius: 5px; }
.total { display: flex; justify-content: flex-end; align-items: center; gap: 8px; font-weight: 700; font-size: 15px; font-variant-numeric: tabular-nums; }
.tier { font-size: 12px; line-height: 1; }
.detail-body { padding: 8px 14px 20px 176px; background: var(--card); border-top: 1px dashed var(--line); }
table.detail { border-collapse: collapse; width: 100%; margin-top: 6px; }
table.detail th { text-align: left; font-weight: 600; color: var(--ink-2); font-size: 12px; padding: 6px 8px; border-bottom: 1px solid var(--line); }
table.detail td { padding: 5px 8px; border-bottom: 1px solid var(--line); vertical-align: middle; }
table.detail tr:last-child td { border-bottom: 0; }
td.score-zero { color: var(--ink-3); }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 7px; vertical-align: -1px; }
.distcell { min-width: 240px; }
.dist { display: flex; gap: 1px; height: 8px; width: 160px; border-radius: 3px; overflow: hidden; margin-bottom: 3px; background: var(--track); }
.dist-seg { display: block; flex-basis: 0; background: var(--dist); min-width: 0; } .dist-seg.chosen { background: var(--dist-chosen); }
.legend { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 6px 22px; }
.legend-group h4 { margin: 10px 0 4px; }
.legend ul { list-style: none; padding: 0; margin: 0; } .legend li { padding: 2px 0; } .legend .abbr { display: inline-block; min-width: 3.4em; }
.prov-card { padding: 12px 16px; }
dl.prov { display: grid; grid-template-columns: 200px 1fr; gap: 6px 14px; margin: 0; }
dl.prov dt { color: var(--ink-2); } dl.prov dd { margin: 0; overflow-wrap: anywhere; }
ul.limits { padding-left: 20px; margin: 10px 0 4px; } ul.limits li { margin: 6px 0; }
.intro { margin: 0 0 8px; max-width: 900px; color: var(--ink-2); } .intro + .intro { margin-bottom: 14px; }
.notice { display: flex; gap: 12px; align-items: flex-start; margin: 8px 0 16px; padding: 10px 14px; max-width: 900px; background: var(--notice-bg); border: 1px solid var(--notice-line); border-left: 4px solid var(--notice-line); border-radius: 8px; color: var(--ink); }
.notice-mark { flex: none; width: 20px; height: 20px; border-radius: 50%; background: var(--notice-line); color: #fff; font-weight: 700; font-size: 12px; text-align: center; line-height: 20px; margin-top: 1px; }
.notice a { font-weight: 600; }
.toolbar { display: flex; align-items: center; gap: 10px; margin: 0 0 10px; flex-wrap: wrap; }
.toolbar input[type="search"] { flex: 0 1 320px; min-width: 200px; padding: 7px 10px; font: inherit; color: var(--ink); background: var(--card); border: 1px solid var(--line-2); border-radius: 7px; }
.toolbar input[type="search"]:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.toolbar .spacer { flex: 1; }
.btn { font: inherit; font-size: 12.5px; padding: 6px 10px; color: var(--ink-2); background: var(--card); border: 1px solid var(--line-2); border-radius: 7px; cursor: pointer; }
.btn:hover { color: var(--ink); background: var(--card-2); } .btn:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
details.row[hidden] { display: none; }
.permalink { margin-left: 6px; text-decoration: none; color: var(--ink-3); opacity: 0; font-weight: 400; }
.src { margin-left: 6px; text-decoration: none; color: var(--ink-3); font-weight: 500; font-size: 11px; padding: 1px 5px; border: 1px solid var(--line-2); border-radius: 4px; vertical-align: 1px; }
.src:hover { color: var(--accent); border-color: var(--accent); text-decoration: none; }
summary.overview:hover .permalink, .permalink:focus-visible { opacity: 1; }
details.row:target > summary.overview { box-shadow: inset 3px 0 0 var(--accent); }
.legend-item { cursor: pointer; border-radius: 4px; padding: 2px 4px !important; margin-left: -4px; }
.legend-item:hover { background: var(--hover); } .legend-item:focus-visible { outline: 2px solid var(--focus); }
.legend-item.active { background: var(--open); font-weight: 600; }
.legend-hint { margin: 10px 0 0; }
.highlighting .seg { opacity: .18; } .highlighting .seg.hl { opacity: 1; box-shadow: 0 0 0 1px var(--ink); }
details.how { margin: 0 0 18px; background: var(--card); border: 1px solid var(--line); border-radius: 10px; }
details.how > summary { cursor: pointer; padding: 10px 14px; list-style: none; } details.how > summary::-webkit-details-marker { display: none; }
details.how > summary::before { content: "▸ "; color: var(--ink-3); } details.how[open] > summary::before { content: "▾ "; }
.how-body { padding: 0 14px 14px; border-top: 1px solid var(--line); }
ol.steps { padding-left: 20px; margin: 10px 0; } ol.steps li { margin: 6px 0; }
.example { background: var(--card-2); border-radius: 8px; padding: 10px 14px; margin-top: 6px; } .example p { margin: 6px 0; }
pre.cmd { background: var(--card-2); border-radius: 8px; padding: 10px 14px; overflow-x: auto; font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
details.how + details.how { margin-top: -10px; }
.defs { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 6px 22px; }
details.crit { border-bottom: 1px solid var(--line); padding: 4px 0; } details.crit > summary { cursor: pointer; list-style: none; padding: 4px 0; }
details.crit > summary::-webkit-details-marker { display: none; } details.crit p { margin: 6px 0; color: var(--ink-2); }
ul.opts { list-style: none; padding: 0; margin: 4px 0 8px; } ul.opts li { padding: 2px 0; } ul.opts .opt { display: inline-block; min-width: 1.6em; text-align: right; margin-right: 8px; color: var(--ink-2); }
@media (max-width: 800px) {
  .header-row { display: none; }
  .overview { grid-template-columns: 1fr auto auto auto; grid-template-areas: "eip badges total expected" "title title title title" "bar bar bar bar"; row-gap: 6px; }
  .eip { grid-area: eip; } .badges { grid-area: badges; } .total { grid-area: total; } .expected { grid-area: expected; } .title { grid-area: title; white-space: normal; } .barwrap { grid-area: bar; }
  .detail-body { padding-left: 14px; }
  dl.prov { grid-template-columns: 1fr; }
  .distcell { min-width: 0; } .dist { width: 100%; }
}
@media print { body { padding: 0; } .header-row { position: static; } details.row { break-inside: avoid; } }
"""

_JS = """
(function () {
  var list = document.getElementById('rows');
  var buttons = Array.prototype.slice.call(document.querySelectorAll('button.sort'));
  if (!list || !buttons.length) return;
  var numeric = { number: true, total: true, stageRank: true, expected: true };
  var state = { key: 'total', dir: -1 };
  function value(row, key) { var v = row.dataset[key]; return numeric[key] ? Number(v) : v; }
  function apply() {
    var rows = Array.prototype.slice.call(list.children);
    rows.sort(function (a, b) {
      var va = value(a, state.key), vb = value(b, state.key);
      if (va < vb) return -state.dir;
      if (va > vb) return state.dir;
      return Number(a.dataset.number) - Number(b.dataset.number);
    });
    rows.forEach(function (row) { list.appendChild(row); });
    buttons.forEach(function (button) {
      var active = button.dataset.key === state.key;
      button.classList.toggle('active', active);
      button.parentNode.setAttribute('aria-sort', active ? (state.dir === 1 ? 'ascending' : 'descending') : 'none');
      button.querySelector('.arrow').textContent = active ? (state.dir === 1 ? '\\u25B2' : '\\u25BC') : '';
    });
  }
  buttons.forEach(function (button) {
    button.addEventListener('click', function () {
      var key = button.dataset.key;
      if (key === state.key) { state.dir = -state.dir; }
      else { state.key = key; state.dir = (key === 'total' || key === 'expected') ? -1 : 1; }
      apply();
    });
  });
  apply();

  var allRows = Array.prototype.slice.call(list.children);
  var filter = document.getElementById('filter');
  var count = document.getElementById('count');
  function applyFilter() {
    var q = (filter.value || '').trim().toLowerCase();
    var shown = 0;
    allRows.forEach(function (row) {
      var hit = !q || row.dataset.number.indexOf(q.replace(/^eip-?/, '')) === 0 || row.dataset.title.indexOf(q) !== -1;
      row.hidden = !hit;
      if (hit) shown++;
    });
    count.textContent = shown + ' of ' + allRows.length + ' shown';
  }
  if (filter) { filter.addEventListener('input', applyFilter); }
  var expand = document.getElementById('expand'), collapse = document.getElementById('collapse');
  if (expand) expand.addEventListener('click', function () { allRows.forEach(function (r) { if (!r.hidden) r.open = true; }); });
  if (collapse) collapse.addEventListener('click', function () { allRows.forEach(function (r) { r.open = false; }); });

  var legendItems = Array.prototype.slice.call(document.querySelectorAll('.legend-item'));
  var highlighted = null;
  function highlight(id) {
    highlighted = (highlighted === id) ? null : id;
    document.body.classList.toggle('highlighting', highlighted !== null);
    legendItems.forEach(function (li) { li.classList.toggle('active', li.dataset.criterion === highlighted); });
    Array.prototype.forEach.call(document.querySelectorAll('.seg'), function (seg) {
      seg.classList.toggle('hl', seg.dataset.criterion === highlighted);
    });
  }
  legendItems.forEach(function (li) {
    li.addEventListener('click', function () { highlight(li.dataset.criterion); });
    li.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); highlight(li.dataset.criterion); } });
  });

  function openHash() {
    var target = location.hash ? document.getElementById(location.hash.slice(1)) : null;
    if (target && target.tagName === 'DETAILS') { target.open = true; target.scrollIntoView({ block: 'start' }); }
  }
  window.addEventListener('hashchange', openHash);
  openHash();
})();
"""


def _eip_count_chip(manifest: dict, evaluations: list[dict], eips_base: str | None, commit: str) -> str:
    label = f"{len(evaluations)} EIPs"
    meta = manifest.get("meta_eip")
    if meta and eips_base:
        return (
            f'<a href="{_esc(eips_base)}/blob/{_esc(commit)}/EIPS/eip-{meta}.md" '
            f'title="EIP-{meta}, the meta EIP this list was taken from, at the assessed commit">{label} · EIP-{meta}</a>'
        )
    return label


def human_links(manifest: dict, eip_number: int | None = None) -> dict[str, str]:
    """GitHub links to the human checklists: the committed directory, the PR filter and, per EIP, a PR search."""
    human = manifest.get("human_assessments")
    if not human:
        return {}
    from urllib.parse import quote_plus

    base = f"https://github.com/{human['repo']}"
    links = {
        "repo": human["repo"],
        "directory": f"{base}/tree/{human['branch']}/{human['path']}" if human.get("path") else base,
        "pulls": f"{base}/pulls?q={quote_plus(human['pr_query'])}",
    }
    if eip_number is not None:
        links["eip_file"] = f"{base}/blob/{human['branch']}/{human['path']}/EIP-{eip_number}.md" if human.get("path") else base
        links["eip_pulls"] = f"{base}/pulls?q={quote_plus(f'is:pr EIP-{eip_number}')}"
    return links


def source_permalink(manifest: dict | None, source_path: str | None) -> str | None:
    """GitHub blob URL of an EIP's Markdown at the assessed commit, or None when the origin is not GitHub."""
    if not manifest or not source_path:
        return None
    base = _github_base(manifest["eips_repo"]["origin_url"])
    commit = manifest["eips_repo"]["resolved_commit"]
    return f"{base}/blob/{commit}/{source_path}" if base else None


def _eip_cell(number: int, row_id: str, manifest: dict | None, source_path: str | None) -> str:
    permalink = source_permalink(manifest, source_path)
    src = (
        f'<a class="src" href="{_esc(permalink)}" title="Markdown as assessed, at ethereum/EIPs commit '
        f'{_esc(manifest["eips_repo"]["resolved_commit"][:10])}">src</a>'
        if permalink else ""
    )
    return (
        f'<span class="eip"><a href="https://eips.ethereum.org/EIPS/eip-{number}" title="EIP-{number} on eips.ethereum.org (latest)">EIP-{number}</a>'
        f'{src}<a class="permalink" href="#{row_id}" title="Link to this row">#</a></span>'
    )


def largest_state(evaluations: list[dict]) -> dict | None:
    """The EIP whose state used the most tokens, estimated from its own request's measured chars-per-token ratio."""
    best = None
    for item in evaluations:
        ev = item["document"]["evaluation"]
        payload = ev.get("request", {}).get("payload")
        usage = (ev.get("jev") or {}).get("usage") or {}
        if not payload or not usage.get("input_tokens"):
            continue
        ratio = len(json.dumps(payload, ensure_ascii=False)) / usage["input_tokens"]
        tokens = int(len(json.dumps(payload.get("state"), ensure_ascii=False)) / ratio)
        if best is None or tokens > best["tokens"]:
            state = payload.get("state") or {}
            best = {
                "number": item["manifest"]["number"],
                "tokens": tokens,
                "share": tokens / JEV_STATE_TOKEN_LIMIT,
                "chars": len(state.get("eip_markdown", "")) if isinstance(state, dict) else 0,
                "source_path": (item["document"].get("provenance", {}).get("eip_source") or {}).get("path"),
            }
    return best


SITE_FILE = "site.json"
GITHUB_MARK = (
    '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 '
    "5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 "
    "1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 "
    "0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 "
    '3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>'
)

NAV_CSS = """
.site-nav { display: flex; align-items: center; gap: 4px 18px; flex-wrap: wrap; margin: 0 0 22px; padding: 0 0 12px; border-bottom: 1px solid var(--line); font-size: 13.5px; }
.site-nav .brand { font-weight: 700; color: var(--ink); text-decoration: none; margin-right: 6px; }
.site-nav a.nav-link { color: var(--ink-2); text-decoration: none; padding: 4px 0; border-bottom: 2px solid transparent; }
.site-nav a.nav-link:hover { color: var(--ink); } .site-nav a.nav-link.active { color: var(--ink); border-bottom-color: var(--accent); }
.site-nav .spacer { flex: 1; }
.site-nav a.github { display: inline-flex; align-items: center; gap: 6px; color: var(--ink-2); text-decoration: none; }
.site-nav a.github:hover { color: var(--ink); } .site-nav a.github svg { display: block; }
"""


def load_site(docs_dir: Path) -> dict | None:
    """The optional site description (title, repository, nav) kept at the docs root, shared by every page."""
    path = Path(docs_dir) / SITE_FILE
    if not path.exists():
        return None
    site = read_json(path, where="site description")
    if not isinstance(site.get("nav"), list):
        raise ComplexityError(f"{path}: 'nav' must be a list of {{label, href}} entries")
    return site


def render_nav(site: dict | None, prefix: str, active_href: str | None) -> str:
    """The shared navigation bar. ``prefix`` is the relative path from the current page to the docs root."""
    if not site:
        return ""
    links = "".join(
        f'<a class="nav-link{" active" if item.get("href") == active_href else ""}" href="{_esc(prefix + item["href"])}">{_esc(item["label"])}</a>'
        for item in site["nav"]
    )
    github = (
        f'<a class="github" href="{_esc(site["repository"])}" title="Source code, configuration and canonical JSON results on GitHub">'
        f"{GITHUB_MARK}<span>GitHub</span></a>"
        if site.get("repository") else ""
    )
    return (
        f'<nav class="site-nav" aria-label="Site"><a class="brand" href="{_esc(prefix + "index.html")}">{_esc(site.get("title", "EIP complexity"))}</a>'
        f'{links}<span class="spacer"></span>{github}</nav>'
    )


def _question_for(evaluation: dict, anchor_id: str) -> dict | None:
    return (evaluation.get("request", {}).get("payload", {}).get("questions", {}) or {}).get(anchor_id)


def _render_how(manifest: dict, example: dict, largest: dict | None = None) -> str:
    """A collapsed explainer with one real question and its real answer from this run."""
    evaluation = example["document"]["evaluation"]
    entry = example["manifest"]
    payload = evaluation.get("request", {}).get("payload", {})
    state = payload.get("state", {})
    markdown = state.get("eip_markdown", "") if isinstance(state, dict) else str(state)
    questions = payload.get("questions", {})
    anchors = evaluation["scores"]["anchors"]
    # Prefer a criterion with a non-zero score and a full 0-3 option set so the example is informative.
    chosen = next((a for a in anchors if a["score"] > 0 and len(a["criteria"]) >= 4), anchors[0])
    question = questions.get(chosen["id"], {})
    if not question:
        return ""  # a manifest without request payloads (e.g. hand-built fixtures) gets no explainer
    instructions = question.get("instructions", {}) if isinstance(question, dict) else {}
    criterion = instructions.get("anchor") or instructions.get("criterion") or {}
    options = "".join(
        f'<tr><td class="mono num">{_esc(option)}</td><td>{_esc(text if isinstance(text, str) else json.dumps(text))}</td>'
        f'<td class="num">{chosen["probabilities"].get(option, 0.0):.2f}{" ← chosen" if option == chosen["choice"] else ""}</td></tr>'
        for option, text in question.get("criteria", {}).items()
    )
    helpers = sum(1 for q in questions if q.startswith("cross_eip_"))
    commit = manifest["eips_repo"]["resolved_commit"]
    origin = manifest["eips_repo"]["origin_url"]
    eips_base = _github_base(origin)
    repo_name = eips_base.removeprefix("https://github.com/") if eips_base else origin
    repo_link = f'<a href="{_esc(eips_base)}">{_esc(repo_name)}</a>' if eips_base else _esc(repo_name)
    commit_link = (
        f'<a href="{_esc(eips_base)}/tree/{_esc(commit)}" class="mono" title="Browse the repository at this commit">{_esc(commit[:10])}</a>'
        if eips_base else f'<span class="mono">{_esc(commit[:10])}</span>'
    )
    source_path = (example["document"].get("provenance", {}).get("eip_source") or {}).get("path")
    markdown_link = source_permalink(manifest, source_path)
    template_link = template_url(manifest)
    template_text = f'<a href="{_esc(template_link)}">checklist template</a>'
    confidence_url = "https://docs.typesafe.ai/confidence"
    largest_link = ""
    if largest:
        largest_permalink = source_permalink(manifest, largest.get("source_path"))
        largest_link = (
            f'<a href="{_esc(largest_permalink)}">EIP-{largest["number"]}</a>' if largest_permalink else f"EIP-{largest['number']}"
        )
    return (
        '<details class="how"><summary><b>How a score is produced</b> <span class="muted">· input, questions, output · with a real example from this run</span></summary>'
        '<div class="how-body">'
        '<ol class="steps">'
        f'<li><b>Input (the "state").</b> The complete Markdown of the EIP as it stood in {repo_link} at commit {commit_link}. '
        "Jev has no other knowledge of the EIP: not its dependencies, its discussion thread, nor any implementation."
        + (
            f' Jev accepts at most <a href="{JEV_MODELS_URL}">{JEV_STATE_TOKEN_LIMIT // 1000}k tokens</a> of state plus the longest '
            f"question in one request; the largest EIP in this run, {largest_link} ({largest['chars']:,} characters), is about "
            f"{largest['tokens'] / 1000:.0f}k tokens ({largest['share']:.0%} of the window), which leaves no room to send its "
            "dependencies with it. The same rule applies to every EIP so that all are judged the same way."
            if largest else ""
        )
        + "</li>"
        f'<li><b>Questions.</b> One <a href="{JEV_CHOICE_URL}">Choice</a> question per criterion of the {template_text} '
        f'(<a href="#criteria">{len(anchors)} criteria</a>, listed below), plus {helpers} yes/no question(s) for the uncapped Cross-EIP rule, '
        "one per EIP that this EIP\'s text references. Each criterion question names the criterion, quotes its description from the "
        "template, and offers the template\'s score definitions verbatim as the options (0 to 3, plus 4 for exceptional cases). "
        "An evaluation policy tells Jev to judge only what the text supports.</li>"
        f'<li><b>Output.</b> For every question <a href="{JEV_DOCS_URL}">Jev</a> returns a probability for each option and a '
        f'<a href="{confidence_url}">confidence</a>. The score is the most probable option; the total, the Cross-EIP bonus and the tier '
        f'are computed in code from the {template_text}\'s rules. Nothing is generated as prose. The confidence is Jev\'s own '
        "measure of how concentrated the probabilities are, 1 meaning all on one option; it is returned with each answer, it "
        "is not the chance that the score is right, and no score is adjusted by it. The Expected column is derived on this page "
        "from those probabilities: each criterion\'s options weighted by their probability and summed, plus the recorded "
        "Cross-EIP bonus, with a standard deviation from the summed per-criterion variances that shows how much of the total "
        "rests on split judgments. It is not a Jev output and it does not affect the tier.</li>"
        "</ol>"
        f'<p class="small">Example, exactly as sent and answered for <a href="#eip-{entry["number"]}">EIP-{entry["number"]}</a>'
        + (f' (<a href="{_esc(markdown_link)}">source Markdown</a>, <a href="{_esc(entry["path"])}">full request and response</a>)' if markdown_link and entry.get("path") else "")
        + ":</p>"
        '<div class="example">'
        f'<p><b>Question</b> <span class="muted small">(id <span class="mono">{_esc(chosen["id"])}</span>)</span><br>{_esc(instructions.get("task", ""))}</p>'
        f'<p><b>Criterion</b> <a href="#criterion-{_esc(chosen["id"])}">{_esc(criterion.get("name", chosen["name"]))}</a>: {_esc(criterion.get("description", ""))}</p>'
        '<table class="detail"><thead><tr><th class="num">Option</th><th>Definition from the template</th><th class="num">Jev probability</th></tr></thead>'
        f"<tbody>{options}</tbody></table>"
        f'<p class="small muted">Confidence {chosen["confidence"]:.2f}. Score recorded: {chosen["score"]}.'
        + (f' The full request and response are in <a href="{_esc(entry["path"])}">{_esc(entry["path"])}</a>.' if entry.get("path") else "")
        + "</p>"
        "</div></div></details>"
    )


def _render_reproduce(manifest: dict, evaluations: list[dict]) -> str:
    """A collapsed block with the exact commands, request count, token total and cost of this run."""
    requests = len(evaluations)
    tokens = 0
    for item in evaluations:
        usage = (item["document"]["evaluation"].get("jev") or {}).get("usage") or {}
        tokens += usage.get("input_tokens") or 0
    cost = tokens / 1_000_000 * JEV_USD_PER_MILLION_INPUT_TOKENS
    remote = (manifest.get("evaluator", {}).get("git") or {}).get("remote_url")
    repo_base = _github_base(remote) if remote else None
    repo_name = repo_base.rsplit("/", 1)[-1] if repo_base else "eip-complexity"
    eips_origin = manifest["eips_repo"]["origin_url"]
    config_path = manifest["config"]["path"]
    commands = (
        (f"git clone --filter=blob:none {eips_origin}\n" if _github_base(eips_origin) else f"# clone or link {eips_origin} as ../EIPs\n")
        + (f"git clone {repo_base} && cd {repo_name} && uv sync\n" if repo_base else "uv sync\n")
        + f"TYPESAFE_API_KEY=… uv run eip-complexity run {config_path}"
    )
    summary_cost = f"about ${cost:.2f}" if tokens else "a few cents"
    code_commit = (manifest.get("evaluator", {}).get("git") or {}).get("commit")
    config_link = (
        f'<a href="{_esc(repo_base)}/blob/{_esc(code_commit)}/{_esc(config_path)}">configuration file</a>'
        if repo_base and code_commit else "configuration file"
    )
    return (
        '<details class="how"><summary><b>How to reproduce</b> '
        '<span class="muted">· one Python command · one API request per EIP</span></summary>'
        '<div class="how-body">'
        "<p>These results can be reproduced by running a Python command, which sends one Jev API request per scored EIP. "
        f"This evaluation required ~{round(tokens / 10_000) * 10}k input tokens "
        f'(<a href="{JEV_MODELS_URL}" title="Jev\'s published price: ${JEV_USD_PER_MILLION_INPUT_TOKENS} per million input tokens">~${cost:.2f}</a>) '
        f"and completes in under a minute. The {config_link} specifies the ethereum/EIPs commit used for the evaluations, "
        "the checklist template and the Jev model version.</p>"
        f'<pre class="cmd"><code>{_esc(commands)}</code></pre>'
        f'<p class="small">An API key can be requested from <a href="https://console.typesafe.ai/">console.typesafe.ai</a>. Jev returns '
        "probability distributions, and they are not bit-for-bit identical between calls: where two options are nearly tied, "
        "the most probable option can flip, so a rerun may differ from these numbers by a point or two per EIP. Completed "
        "evaluations are cached locally, so repeating the run in the same clone sends no further requests.</p>"
        "</div></details>"
    )


def _render_limitations(manifest: dict, largest: dict | None) -> str:
    """A collapsed list: the dependency caveat, the window limit, and the run's recorded limitations."""
    fork = manifest["fork"]
    items = [
        f"If an EIP builds on other EIPs that are themselves new to {_esc(fork)}, its text restates their mechanisms and the "
        "evaluation cannot separate those from the EIP\'s own additions, so such EIPs may score higher than they would if their "
        "dependencies were already live on mainnet."
        + (
            f" Supplying the dependencies alongside the EIP could help remove that bias, but Jev\'s "
            f'<a href="{JEV_MODELS_URL}">input window</a> is {JEV_STATE_TOKEN_LIMIT // 1000}k tokens per request and the largest EIP '
            f"here, EIP-{largest['number']}, already fills about {largest['tokens'] / 1000:.0f}k of it on its own, so dependencies "
            "are not included."
            if largest else ""
        )
    ]
    items += [_esc(text) for text in manifest.get("methodology", {}).get("limitations", [])]
    return (
        '<details class="how" id="limitations"><summary><b>Limitations</b> '
        f'<span class="muted">· EIPs that build on other EIPs new to {_esc(fork)} may score higher than if those were already live</span></summary>'
        f'<div class="how-body"><ul class="limits">{"".join(f"<li>{item}</li>" for item in items)}</ul></div></details>'
    )


def _render_definitions(example: dict, anchors: list[dict], styles: dict[str, dict]) -> str:
    """Every criterion's description and option texts, as they appeared in the questions."""
    evaluation = example["document"]["evaluation"]
    by_id = {a["id"]: a for a in evaluation["scores"]["anchors"]}
    blocks = []
    for anchor in anchors:
        question = _question_for(evaluation, anchor["id"]) or {}
        instructions = question.get("instructions", {}) if isinstance(question, dict) else {}
        criterion = instructions.get("anchor") or instructions.get("criterion") or {}
        options = by_id[anchor["id"]].get("criteria", {})
        items = "".join(
            f'<li><span class="mono num opt">{_esc(o)}</span> {_esc(t if isinstance(t, str) else json.dumps(t))}</li>' for o, t in options.items()
        )
        notes = "".join(f'<p class="small muted">{_esc(n)}</p>' for n in criterion.get("notes", []))
        style = styles[anchor["id"]]
        blocks.append(
            f'<details class="crit" id="criterion-{_esc(anchor["id"])}"><summary><span class="swatch" style="background:{style["color"]}"></span>'
            f'<span class="mono abbr">{_esc(style["abbr"])}</span> {_esc(anchor["name"])}</summary>'
            f'<p>{_esc(criterion.get("description", ""))}</p><ul class="opts">{items}</ul>{notes}</details>'
        )
    return f'<div class="defs">{"".join(blocks)}</div>'


def _render_notice(notice: dict | None) -> str:
    """An admonition under the heading, from the run's ``[notice]`` config (e.g. marking a run as secondary)."""
    if not notice or not notice.get("text"):
        return ""
    link = ""
    if notice.get("link"):
        label = notice.get("link_label") or notice["link"]
        link = f' <a href="{_esc(notice["link"])}">{_esc(label)}</a>'
    return f'<div class="notice" role="note"><span class="notice-mark" aria-hidden="true">!</span><div>{_esc(notice["text"])}{link}</div></div>'


def _sort_header(label: str, key: str, *, cls: str = "", title: str | None = None) -> str:
    tooltip = _esc(title) if title else f"Sort by {_esc(label.lower())}"
    return (
        f'<span role="columnheader" aria-sort="none" class="{cls}">'
        f'<button type="button" class="sort" data-key="{key}" title="{tooltip}">{_esc(label)}<span class="arrow"></span></button></span>'
    )


def render_html(manifest: dict, items: list[dict], *, site: dict | None = None, nav_prefix: str = "../", nav_active: str | None = None) -> str:
    anchors = _anchor_order(items)
    styles = anchor_styles([a["id"] for a in anchors])
    evaluations = [i for i in items if is_evaluated(i)]
    not_applicable = sorted((i for i in items if not is_evaluated(i)), key=lambda i: i["manifest"]["number"])
    ordered = sorted(evaluations, key=lambda item: (-item["manifest"]["total_score"], item["manifest"]["number"]))
    max_total = max((item["manifest"]["total_score"] for item in ordered), default=0)
    revision = manifest["template"]["revision"]
    rows = "".join(_render_row(item, styles, max_total, manifest) for item in ordered) + "".join(_render_na_row(i, manifest) for i in not_applicable)
    commit = manifest["eips_repo"]["resolved_commit"]
    eips_base = _github_base(manifest["eips_repo"]["origin_url"])
    eips_label = _esc(f"ethereum/EIPs @ {commit[:10]}" if eips_base and eips_base.endswith("/ethereum/EIPs") else f"EIPs @ {commit[:10]}")
    chips = [
        f'<a href="{_esc(template_url(manifest))}">checklist revision {revision} ({manifest["template"]["anchor_count"]} criteria)</a>',
        f'<a href="{JEV_DOCS_URL}">Jev {_esc(manifest["jev"]["resolved_model"])}</a>',
        f'<a href="{_esc(eips_base)}/tree/{_esc(commit)}" title="ethereum/EIPs at the exact commit assessed">{eips_label}</a>' if eips_base else eips_label,
        _eip_count_chip(manifest, items, eips_base, commit),
        f"generated {_esc(manifest['created_at'])}",
    ]
    fork = manifest["fork"]
    largest = largest_state(evaluations)
    title = f"An Automated \u201cSystem One\u201d Evaluation of {fork} EIP Testing Complexity"
    links = human_links(manifest)
    human_intro = (
        (
            f'<p class="intro">Human assessments against the same checklist are maintained in '
            f'<a href="https://github.com/{_esc(links["repo"])}">{_esc(links["repo"])}</a>: '
            f'<a href="{_esc(links["directory"])}">merged evaluations</a> and '
            f'<a href="{_esc(links["pulls"])}">evaluations in progress or under review</a> (pull requests). '
            "Each row\'s details link to the pull requests for that EIP."
        )
        if links
        else ""
    )
    comparison = manifest.get("comparison")
    if comparison:
        source = (
            f'the <a href="{_esc(comparison["source_url"])}">{_esc(comparison["source_label"])}</a>'
            if comparison.get("source_url") else _esc(comparison["source_label"])
        )
        rerun = (
            f', together with <a href="{_esc(comparison["rerun_page"])}">{_esc(comparison["rerun_label"])}</a>'
            if comparison.get("rerun_page") else ""
        )
        sentence = (
            f' A <a href="{_esc(comparison["page"])}">comparison</a> of these scores with the human assessments '
            f"and with the LLM evaluations of {source} is kept on a separate page{rerun}."
        )
        human_intro = (human_intro + sentence) if human_intro else f'<p class="intro">{sentence.strip()}'
    if human_intro:
        human_intro += "</p>"
    header = (
        '<div class="header-row" role="row">'
        + _sort_header("EIP", "number")
        + _sort_header("Title", "title")
        + _sort_header("Stage", "stageRank")
        + '<span role="columnheader">Criteria scores <span class="hint">· segment width ∝ score · click a row for details</span></span>'
        + _sort_header("Total", "total", cls="num")
        + _sort_header("Expected ± sd", "expected", cls="num", title=EXPECTED_TITLE)
        + "</div>"
    )
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{_esc(title)}</title>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<style>{_CSS}{NAV_CSS}</style></head><body><main>"
        f"{render_nav(site, nav_prefix, nav_active)}"
        f"<h1>{_esc(title)}</h1>"
        f"{_render_notice(manifest.get('notice'))}"
        f'<p class="intro"><b>This is an experimental study of {_esc(fork)} EIP testing complexity</b>, scored by '
        f'<a href="{JEV_DOCS_URL}">Jev</a> from each EIP\'s markdown and the '
        f'<a href="{_esc(template_url(manifest))}">ethspecs EIP complexity evaluation template</a>.</p>'
        f'<p class="intro">Each EIP is scored using one Jev request consisting of a <a href="{JEV_CHOICE_URL}">Choice</a> '
        f'question per checklist criterion, with the template\'s score definitions as the options. Jev is a '
        f'<a href="{SYSTEM_ONE_URL}">System One</a> '
        "model: every answer is a calibrated selection with a probability distribution and a confidence, and the model "
        "cannot provide a written rationale for it.</p>"
        f"{human_intro}"
        '<p class="intro">Expand a row to see every criterion\'s probabilities and confidence, and to open the exact request and response.</p>'
        f'<div class="chips">{"".join(f"<span>{c}</span>" for c in chips)}</div>'
        f"{_render_how(manifest, ordered[0], largest)}"
        f"{_render_reproduce(manifest, evaluations)}"
        f"{_render_limitations(manifest, largest)}"
        f"{_render_summary(manifest, evaluations, len(not_applicable))}"
        '<div class="toolbar">'
        '<input type="search" id="filter" placeholder="Filter by EIP number or title" aria-label="Filter rows by EIP number or title" autocomplete="off">'
        f'<span id="count" class="small muted">{len(evaluations)} of {len(evaluations)} shown</span>'
        '<span class="spacer"></span>'
        '<button type="button" class="btn" id="expand">Expand all</button>'
        '<button type="button" class="btn" id="collapse">Collapse all</button>'
        "</div>"
        f'<div class="card table" role="table" aria-label="EIP complexity scores">{header}<div id="rows">{rows}</div></div>'
        '<p class="small muted legend-hint">Click a criterion in the legend to highlight it in every bar; click again to clear.</p>'
        "<h2>Legend</h2>"
        f"{_render_legend(anchors, styles)}"
        '<h2 id="criteria">Checklist criteria as sent to Jev</h2>'
        '<p class="small muted">The description and the score definitions of every criterion, exactly as they appeared in the questions. Click a criterion to expand it.</p>'
        f"{_render_definitions(ordered[0], anchors, styles)}"
        '<h2 id="provenance">Provenance</h2>'
        f"{_render_provenance(manifest, evaluations)}"  # evaluated items only: the cache/live count
        f"<script>{_JS}</script>"
        "</main></body></html>\n"
    )


def render_run(run_path: Path) -> Path:
    run_path, manifest, evaluations = load_run(Path(run_path))
    run_dir = run_path.parent
    site = load_site(run_dir.parent)
    output = run_dir / "index.html"
    output.write_text(
        render_html(manifest, evaluations, site=site, nav_prefix="../", nav_active=f"{run_dir.name}/"),
        encoding="utf-8",
    )
    return output
