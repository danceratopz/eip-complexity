#!/usr/bin/env python3
"""Generate docs/hegota/comparison.html: Jev totals vs the retrospective study's LLM and human assessments.

Inputs are canonical JSON only: the two Jev runs (current commit and the LLM study's snapshot commit)
and docs/hegota/comparison-data.json (vendored LLM and human totals with their source commit). With
--eips-repo pointing at a local ethereum/EIPs clone, the per-EIP text diffs between the two commits
are embedded; without it the diff links to GitHub remain.

    uv run scripts/compare_hegota.py --eips-repo ../EIPs
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import statistics
import subprocess
from pathlib import Path

from eip_complexity.render import NAV_CSS, load_site, render_nav

EIPS_GITHUB = "https://github.com/ethereum/EIPs"


def esc(value: object) -> str:
    return html.escape(str(value))


def load_run(run_dir: Path) -> tuple[dict, dict[int, dict], dict[int, dict]]:
    manifest = json.loads((run_dir / "run.json").read_text())
    entries = {e["number"]: e for e in manifest["evaluations"]}
    documents = {n: json.loads((run_dir / e["path"]).read_text()) for n, e in entries.items() if e["status"] == "evaluated"}
    return manifest, entries, documents


def pearson(x: list[float], y: list[float]) -> float:
    mx, my = statistics.mean(x), statistics.mean(y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))


def spearman(x: list[float], y: list[float]) -> float:
    def rank(values: list[float]) -> list[float]:
        ordered = sorted(values)
        return [(ordered.index(v) + 1 + len(ordered) - ordered[::-1].index(v)) / 2 for v in values]

    return pearson(rank(x), rank(y))


def tier(total: float) -> str:
    return "H" if total >= 23 else ("M" if total >= 12 else "L")


def stats(pairs: list[tuple[int, int]]) -> dict:
    x = [a for a, _ in pairs]
    y = [b for _, b in pairs]
    return {
        "n": len(pairs), "r": pearson(x, y), "rho": spearman(x, y),
        "mad": statistics.mean(abs(a - b) for a, b in pairs), "bias": statistics.mean(a - b for a, b in pairs),
        "tier": sum(1 for a, b in pairs if tier(a) == tier(b)),
    }


def tree(sha: str) -> str:
    return f'<a class="mono" href="{EIPS_GITHUB}/tree/{sha}" title="ethereum/EIPs at commit {sha}">{sha[:7]}</a>'


def blob(sha: str, number: int) -> str:
    return f"{EIPS_GITHUB}/blob/{sha}/EIPS/eip-{number}.md"


def file_diff_url(base: str, head: str, number: int) -> str:
    anchor = hashlib.sha256(f"EIPS/eip-{number}.md".encode()).hexdigest()
    return f"{EIPS_GITHUB}/compare/{base}...{head}#diff-{anchor}"


def git_diff(clone: Path, number: int, base: str, head: str) -> tuple[int, int, str]:
    path = f"EIPS/eip-{number}.md"
    numstat = subprocess.run(["git", "-C", str(clone), "diff", "--numstat", base, head, "--", path], capture_output=True, text=True).stdout.split()
    ins, dele = (int(numstat[0]), int(numstat[1])) if len(numstat) >= 2 else (0, 0)
    text = subprocess.run(["git", "-C", str(clone), "diff", "-U3", "--no-color", base, head, "--", path], capture_output=True, text=True).stdout
    return ins, dele, text


def render_diff(text: str) -> str:
    out = []
    for line in text.splitlines():
        if line.startswith(("diff ", "index ", "--- ", "+++ ")):
            cls = "meta"
        elif line.startswith("@@"):
            cls = "hunk"
        elif line.startswith("+"):
            cls = "add"
        elif line.startswith("-"):
            cls = "del"
        else:
            cls = ""
        out.append(f'<span class="{cls}">{esc(line)}</span>' if cls else esc(line))
    return "\n".join(out)


CSS = """
:root{color-scheme:light dark;--surface:#f6f6f4;--card:#fff;--ink:#14140f;--ink-2:#55544f;--ink-3:#8a8a86;--line:#e4e3df;--accent:#2a78d6;--warn:#8a3a15}
@media (prefers-color-scheme:dark){:root{--surface:#161614;--card:#1e1e1b;--ink:#f2f2ee;--ink-2:#bdbcb4;--ink-3:#8a8a86;--line:#30302c;--accent:#6ea8f0;--warn:#f0a070}}
body{margin:0;padding:28px 16px 56px;background:var(--surface);color:var(--ink);font:14px/1.5 -apple-system,"Segoe UI",Inter,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto} a{color:var(--accent)} h1{font-size:22px;margin:0 0 6px} h2{font-size:16px;margin:28px 0 8px}
.lead{color:var(--ink-2);max-width:940px} .mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px} .small{font-size:11.5px} .muted{color:var(--ink-3)} .warn{color:var(--warn);font-weight:700}
table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th{text-align:left;font-size:12px;color:var(--ink-2);padding:8px 10px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--card)}
td{padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top} tr:last-child td{border-bottom:0}
.num,th.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums} .delta{color:var(--ink-2)} .pair,.nowrap{white-space:nowrap}
.badge{font-size:11px;font-weight:600;padding:3px 6px;border-radius:5px;background:var(--line);color:var(--ink-2)} .tier{font-size:11px}
.versions{margin-top:2px;white-space:nowrap} .versions a{color:var(--ink-3)} .versions a:hover{color:var(--accent)}
tr.diffrow td{padding:0 10px 8px;background:var(--surface)} tr.diffrow summary{cursor:pointer;color:var(--ink-2);padding:6px 0}
pre.diff{font:12px/1.45 ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px;max-height:480px;overflow:auto;margin:0}
pre.diff .add{color:#12603a;background:rgba(18,96,58,.08)} pre.diff .del{color:#8c1f1f;background:rgba(140,31,31,.08)} pre.diff .hunk{color:var(--accent)} pre.diff .meta{color:var(--ink-3)}
@media (prefers-color-scheme:dark){pre.diff .add{color:#9fe3bb;background:rgba(159,227,187,.12)} pre.diff .del{color:#ffb3b3;background:rgba(255,179,179,.12)}}
ul.notes{padding-left:20px;color:var(--ink-2)} ul.notes li{margin:4px 0}
"""

SCRIPT = """<script>
(function () {
  function toggleDiff(hash, force) {
    var row = document.getElementById(hash.slice(1));
    if (!row) return false;
    var details = row.querySelector('details');
    if (details) details.open = (force === undefined) ? !details.open : force;
    return true;
  }
  Array.prototype.forEach.call(document.querySelectorAll('a[href^="#diff-"]'), function (a) {
    a.addEventListener('click', function (e) { e.preventDefault(); toggleDiff(a.getAttribute('href')); });
  });
  if (location.hash.indexOf('#diff-') === 0 && toggleDiff(location.hash, true)) {
    document.getElementById(location.hash.slice(1)).scrollIntoView({ block: 'start' });
  }
})();
</script>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--current", type=Path, default=Path("docs/hegota"), help="run directory of the published Hegotá assessment")
    parser.add_argument("--snapshot", type=Path, default=Path("docs/hegota-2026-08-26"), help="run directory of the assessment at the LLM study's commit")
    parser.add_argument("--data", type=Path, default=Path("docs/hegota/comparison-data.json"))
    parser.add_argument("--eips-repo", type=Path, default=None, help="local ethereum/EIPs clone; enables embedded diffs")
    parser.add_argument("--out", type=Path, default=Path("docs/hegota/comparison.html"))
    args = parser.parse_args()

    data = json.loads(args.data.read_text())
    m_new, new_entries, new_docs = load_run(args.current)
    m_old, old_entries, old_docs = load_run(args.snapshot)
    NEW_SHA, OLD_SHA = m_new["eips_repo"]["resolved_commit"], m_old["eips_repo"]["resolved_commit"]
    if OLD_SHA != data["llm"]["eips_commit"]:
        raise SystemExit(f"snapshot run is at {OLD_SHA[:7]} but the LLM study used {data['llm']['eips_commit'][:7]}")
    llm = {int(k): v for k, v in data["llm"]["scores"].items()}
    llm_na = {int(k): v for k, v in data["llm"]["not_applicable"].items()}
    human = {int(k): v for k, v in data["human"]["entries"].items()}
    new_scored = {n: e for n, e in new_entries.items() if e["status"] == "evaluated"}
    old_scored = {n: e for n, e in old_entries.items() if e["status"] == "evaluated"}
    h2 = {n: h for n, h in human.items() if h["checklist_revision"] == 2}
    unchanged = {n for n in new_scored if n in old_scored and new_docs[n]["evaluation"]["eip"]["content_sha256"] == old_docs[n]["evaluation"]["eip"]["content_sha256"]}
    clone = args.eips_repo if args.eips_repo and (args.eips_repo / ".git").exists() else None

    S = {
        "OLD_VS_LLM": stats([(old_scored[n]["total_score"], llm[n]) for n in old_scored if n in llm]),
        "NEW_VS_LLM": stats([(new_scored[n]["total_score"], llm[n]) for n in new_scored if n in llm]),
        # Only EIPs whose text changed: identical text hits the same cache entry, so comparing it with itself says nothing.
        "DRIFT": stats([(new_scored[n]["total_score"], old_scored[n]["total_score"]) for n in new_scored if n in old_scored and n not in unchanged]),
        "OLD_VS_HUMAN": stats([(old_scored[n]["total_score"], h2[n]["total"]) for n in old_scored if n in h2]),
        "NEW_VS_HUMAN": stats([(new_scored[n]["total_score"], h2[n]["total"]) for n in new_scored if n in h2]),
        "LLM_VS_HUMAN": stats([(llm[n], h2[n]["total"]) for n in h2 if n in llm]),
    }
    rev1_n = sum(1 for h in human.values() if h["checklist_revision"] != 2)
    rev2_n = len(h2)
    ck = data["checklist"]
    rev_link = {k: f'https://github.com/{v["repo"]}/blob/{v["commit"]}/{v["path"]}' for k, v in ck.items()}
    OLD_L, NEW_L = tree(OLD_SHA), tree(NEW_SHA)
    H_JEV_OLD, H_JEV_NEW, H_LLM_OLD = f"Jev @ {OLD_L}", f"Jev @ {NEW_L}", f"LLM @ {OLD_L}"
    HUMAN_MIXED = (f'<span title="Human checklists are mixed: {rev2_n} on revision 2 (compared) and {rev1_n} on revision 1 (excluded, marked ! below)">'
                   f'<a href="{rev_link["revision_2"]}">2</a> vs <b>mixed</b>, rev 2 only</span>')
    TWO_VS_TWO = f'<a href="{rev_link["revision_2"]}">2</a> vs <a href="{rev_link["revision_2"]}">2</a>'
    TEXT_SAME = f"identical, {OLD_L}"
    TEXT_DRIFT = f'<span title="Jev scored the text at {NEW_SHA[:7]}, the other side at {OLD_SHA[:7]}">{NEW_L} vs {OLD_L}</span>'
    changed_n = len([n for n in new_scored if n in old_scored and n not in unchanged])
    TEXT_CHANGED_ONLY = (f'<span title="Only the {changed_n} EIPs whose Markdown differs between the two commits; the other {len(unchanged)} '
                         f'have identical text and therefore the identical cached evaluation">{NEW_L} vs {OLD_L}, changed text only</span>')
    HUMAN_TEXT = '<span title="Human checklists were written against whatever EIP text their pull request had">mixed PR heads</span>'
    LABELS = {
        "OLD_VS_LLM": (f"{H_JEV_OLD} vs {H_LLM_OLD}", TWO_VS_TWO, TEXT_SAME),
        "NEW_VS_LLM": (f"{H_JEV_NEW} vs {H_LLM_OLD}", TWO_VS_TWO, TEXT_DRIFT),
        "DRIFT": (f"{H_JEV_NEW} vs {H_JEV_OLD}", TWO_VS_TWO, TEXT_CHANGED_ONLY),
        "OLD_VS_HUMAN": (f"{H_JEV_OLD} vs human", HUMAN_MIXED, f"{OLD_L} vs {HUMAN_TEXT}"),
        "NEW_VS_HUMAN": (f"{H_JEV_NEW} vs human", HUMAN_MIXED, f"{NEW_L} vs {HUMAN_TEXT}"),
        "LLM_VS_HUMAN": (f"{H_LLM_OLD} vs human", HUMAN_MIXED, f"{OLD_L} vs {HUMAN_TEXT}"),
    }
    stat_rows = "".join(
        f'<tr><td class="pair">{LABELS[k][0]}</td><td class="nowrap">{LABELS[k][1]}</td><td class="nowrap">{LABELS[k][2]}</td>'
        f'<td class="num">{v["n"]}</td><td class="num">{v["r"]:.2f}</td><td class="num">{v["rho"]:.2f}</td>'
        f'<td class="num">{v["mad"]:.1f}</td><td class="num">{v["bias"]:+.1f}</td><td class="num">{v["tier"]}/{v["n"]}</td></tr>'
        for k, v in S.items()
    )

    rows = []
    numbers = set(new_entries) | set(old_scored)

    def sort_key(n: int) -> tuple:
        e = new_entries.get(n)
        o = old_scored.get(n)
        return (-(e["total_score"] if e and e["status"] == "evaluated" else (o["total_score"] if o else -1)), n)

    for n in sorted(numbers, key=sort_key):
        e, o, h, l = new_entries.get(n), old_scored.get(n), human.get(n), llm.get(n)
        title = (e or o)["title"]
        stage = (e or o).get("stage") or ""
        if e and e["status"] == "evaluated":
            jev_cell = f'<a href="index.html#eip-{n}">{e["total_score"]}</a> <span class="tier">{e["tier"]["emoji"]}</span>'
            jv: int | None = e["total_score"]
        elif e:
            jev_cell, jv = '<span class="muted">n/a</span>', None
        else:
            jev_cell, jv = '<span class="muted" title="Not in the current Hegotá list (declined since the snapshot)">–</span>', None
        if o:
            same = n in unchanged
            old_cell = f'<a href="../{args.snapshot.name}/index.html#eip-{n}">{o["total_score"]}</a> <span class="tier">{o["tier"]["emoji"]}</span>' + (
                ' <span class="small muted" title="EIP text identical at both commits">=</span>' if same else "")
            ov: int | None = o["total_score"]
        else:
            old_cell, ov = '<span class="muted" title="Not scored by the LLM study, so not rerun">–</span>', None
        if l is not None:
            llm_cell = str(l)
        elif n in llm_na:
            llm_cell = f'<span class="muted" title="{esc(llm_na[n])}">n/a</span>'
        else:
            llm_cell = '<span class="muted" title="Not in the 2026-08-26 PFI snapshot">–</span>'
        if h:
            label = f'{h["total"]} <span class="small muted">rev {h["checklist_revision"]} · {esc(h["status"].replace("_", " "))}</span>'
            human_cell = f'<a href="{esc(h["url"])}">{label}</a>' if h.get("url") else label
            if h["checklist_revision"] != 2:
                human_cell += f' <a class="small warn" href="{rev_link["revision_1"]}" title="Revision-1 checklist (24 anchors, 0–72): not comparable">!</a>'
        else:
            human_cell = '<span class="muted">–</span>'
        versions = []
        diff_row = ""
        if o:
            versions.append(f'<a href="{blob(OLD_SHA, n)}" title="Markdown as scored by Jev and the LLM, at {OLD_SHA[:7]}">{OLD_SHA[:7]}</a>')
        if e:
            versions.append(f'<a href="{blob(NEW_SHA, n)}" title="Markdown as scored by Jev, at {NEW_SHA[:7]}">{NEW_SHA[:7]}</a>')
        if o and jv is not None and n not in unchanged:
            versions.append(f'<a href="{file_diff_url(OLD_SHA, NEW_SHA, n)}" title="GitHub compare view between the two commits, anchored on this file">diff on GitHub</a>')
            if clone:
                ins, dele, text = git_diff(clone, n, OLD_SHA, NEW_SHA)
                versions.append(f'<a href="#diff-{n}" title="Show or hide the unified diff under this row">+{ins}/−{dele}</a>')
                diff_row = (
                    f'<tr class="diffrow" id="diff-{n}"><td colspan="10"><details><summary class="small">EIPS/eip-{n}.md: '
                    f'{OLD_SHA[:7]} → {NEW_SHA[:7]}, +{ins} −{dele} lines · Jev {ov} → {jv}</summary>'
                    f'<pre class="diff">{render_diff(text)}</pre></details></td></tr>'
                )
        version_html = f'<div class="small mono versions">{" · ".join(versions)}</div>' if versions else ""
        d_llm = f"{ov - l:+d}" if (ov is not None and l is not None) else ""
        d_drift = f"{jv - ov:+d}" if (jv is not None and ov is not None) else ""
        d_h = f"{jv - h['total']:+d}" if (jv is not None and h and h["checklist_revision"] == 2) else ""
        rows.append(
            f'<tr><td class="mono"><a href="https://eips.ethereum.org/EIPS/eip-{n}">EIP-{n}</a>{version_html}</td><td>{esc(title)}</td>'
            f'<td><span class="badge">{esc(stage)}</span></td><td class="num">{old_cell}</td><td class="num">{llm_cell}</td>'
            f'<td class="num delta">{d_llm}</td><td class="num">{jev_cell}</td><td class="num delta">{d_drift}</td>'
            f'<td class="num">{human_cell}</td><td class="num delta">{d_h}</td></tr>' + diff_row
        )

    src = data["source"]
    llm_meta = data["llm"]
    site = load_site(args.out.parent.parent)
    nav = render_nav(site, "../", f"{args.out.parent.name}/{args.out.name}")
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Hegota EIP testing complexity: Jev vs LLM vs human</title>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<style>{CSS}{NAV_CSS}</style></head><body><main>"
        f"{nav}"
        "<h1>Hegota EIP testing complexity: Jev vs reasoning LLM vs human</h1>"
        f'<p class="lead">This page compares the <a href="index.html">Jev scores</a> of the Hegota EIPs with the reasoning-LLM and human '
        f'assessments collected by the <a href="{esc(llm_meta["site"])}">retrospective EIP complexity study</a>. '
        f"Because that study scored the EIP versions at ethereum/EIPs {OLD_L} (2026-08-26) and the published Jev run scored {NEW_L} (2026-09-21), "
        f'Jev was <a href="../{args.snapshot.name}/index.html">also run on the study\'s versions</a>: the {H_JEV_OLD} column sees exactly the text the LLM saw, '
        f"and the difference between the two Jev columns shows what a month of EIP edits, plus Jev\\'s own call-to-call variation, did to the totals.</p>"
        f'<p class="lead">LLM totals: {esc(llm_meta["model"])} at reasoning effort {esc(llm_meta["reasoning_effort"])}, checklist revision {llm_meta["checklist_revision"]}, '
        f"one isolated session per EIP, snapshot {esc(llm_meta['snapshot_id'])}. Human totals: the study\\'s snapshot of ethspecs/pm checklists "
        f"({esc(data['human']['captured_at'][:10])}), mostly open or draft pull requests, on mixed checklist revisions. "
        f'Both were taken from <a href="{esc(src["repository"])}/tree/{esc(src["commit"])}">{esc(src["repository"].removeprefix("https://github.com/"))} @ {esc(src["commit"][:7])}</a> '
        f'and are vendored with that provenance in <a href="comparison-data.json">comparison-data.json</a>.</p>'
        "<h2>Agreement</h2>"
        '<table><thead><tr><th>Pair</th><th title="Checklist revision each side was scored against">Checklist rev.</th>'
        '<th title="Whether both sides saw the same EIP Markdown">EIP text</th><th class="num">n</th><th class="num">Pearson r</th>'
        '<th class="num">Spearman ρ</th><th class="num">mean |Δ|</th><th class="num">bias (first − second)</th><th class="num">same tier</th></tr></thead>'
        f"<tbody>{stat_rows}</tbody></table>"
        f'<p class="small muted">Jev and the LLM scored every EIP against <a href="{rev_link["revision_2"]}">checklist revision 2</a>. '
        f"The human checklists are mixed: {rev2_n} were written against revision 2 and enter the statistics; {rev1_n} against "
        f'<a href="{rev_link["revision_1"]}">revision 1</a> ({ck["revision_1"]["anchors"]} anchors, {ck["revision_1"]["scale"]} scale, tiers {ck["revision_1"]["tiers"]}) '
        'are shown in the table with a "!" but excluded, because totals are not comparable across revisions.</p>'
        "<h2>Per EIP</h2>"
        '<table><thead><tr><th>EIP</th><th>Title</th><th>Stage</th>'
        f'<th class="num" title="Jev on the EIP text at the LLM study\'s commit">{H_JEV_OLD}</th><th class="num">{H_LLM_OLD}</th>'
        '<th class="num" title="Same EIP text for both">Jev − LLM</th>'
        f'<th class="num" title="Jev on the current EIP text">{H_JEV_NEW}</th><th class="num" title="Effect of a month of EIP edits and Jev\'s call-to-call variation">drift</th>'
        f'<th class="num">Human</th><th class="num">Jev@{NEW_SHA[:7]} − human</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        "<h2>Notes</h2>"
        '<ul class="notes">'
        f"<li>The three sources scored different EIP snapshots: Jev at {NEW_L} (published run) and at {OLD_L} (this comparison\\'s rerun), the LLM at {OLD_L}, "
        "humans at the head of whichever pull request held their checklist.</li>"
        '<li>"n/a" under LLM means the retrospective study marked the EIP not applicable to the execution-layer rubric; "–" means it entered PFI after the '
        '2026-08-26 snapshot. "n/a" under Jev means the EIP is configured as consensus-only or informational and was not scored. "–" under Jev @ '
        f"{NEW_SHA[:7]} means the EIP has since been declined for Hegotá and is not in the current list.</li>"
        f'<li>Under each EIP number: permalinks to its Markdown at the commit(s) it was scored at and, where the text changed between them, a GitHub compare '
        f"link anchored on that file{' plus the change size, which opens the unified diff embedded under the row' if clone else ''}. "
        f'{len(unchanged)} of the {len([n for n in new_scored if n in old_scored])} EIPs in both runs had identical text and were served from cache in the rerun.</li>'
        f"<li>\"drift\" is Jev at the newer commit minus Jev at the older one, and its agreement row uses only the {changed_n} EIPs whose text changed: "
        f"the other {len(unchanged)} were not re-evaluated, since identical input maps to the same cached evaluation. Drift mixes real text edits with "
        "Jev\\'s own call-to-call variation: EIP-8141 changed by three lines and still moved by four points, about one standard deviation of its expected total.</li>"
        f'<li>Tiers: Low &lt;12, Medium 12–22, High ≥23 (<a href="{rev_link["revision_2"]}">revision 2</a>).</li>'
        "</ul>"
        f"{SCRIPT}</main></body></html>\n"
    )
    args.out.write_text(page, encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size:,} bytes); diffs embedded: {bool(clone)}")
    for key, value in S.items():
        print(f"  {key:13s} n={value['n']:2d} r={value['r']:.2f} rho={value['rho']:.2f} mad={value['mad']:.1f} bias={value['bias']:+.1f} tier={value['tier']}/{value['n']}")


if __name__ == "__main__":
    main()
