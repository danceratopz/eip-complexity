#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Extract the LLM and human Hegotá totals from a local clone of the retrospective study into one JSON file.

The retrospective-eip-complexity repository holds the reasoning-LLM assessment (Task 08) and the
snapshot of human checklists from ethspecs/pm (Task 09) as YAML. This script copies only the totals
and their provenance into docs/<run>/comparison-data.json so the comparison page can be regenerated
without that clone, and records which commit and files the numbers came from.

    uv run scripts/extract_comparison_data.py --retrospective ../retrospective-complexity-eval
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

TASK08 = "research/tasks/08-hegota-prospective-complexity-assessment"
TASK09 = "research/tasks/09-hegota-human-assessment-snapshot"
CHECKLIST = {
    "revision_1": {"repo": "ethspecs/pm", "commit": "51a8e5c15144a96f088e1e1c2261e9cc87c80ac2", "path": "Templates/EIP-Complexity-Assessment.md", "anchors": 24, "scale": "0-72", "tiers": "10/20"},
    "revision_2": {"repo": "ethspecs/pm", "commit": "3d8c0128c5543dd3146341ef395aa344e4abea30", "path": "Templates/EIP-Complexity-Assessment.md", "anchors": 28, "scale": "0-84 nominal", "tiers": "12/23"},
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retrospective", type=Path, required=True, help="local clone of danceratopz/retrospective-eip-complexity")
    parser.add_argument("--out", type=Path, default=Path("docs/hegota/comparison-data.json"))
    args = parser.parse_args()
    root = args.retrospective
    files: dict[str, str] = {}

    def load(relative: str) -> dict:
        path = root / relative
        files[relative] = sha256(path)
        return yaml.safe_load(path.open())

    summary = load(f"{TASK08}/outputs/summary.yaml")
    extension = load(f"{TASK08}/extensions/sfi-cfi-2026-08-26/outputs/summary.yaml")
    config = load(f"{TASK08}/config.yaml")
    scores: dict[str, int] = {}
    not_applicable: dict[str, str] = {}
    for doc in (summary, extension):
        for entry in doc.get("scored_eips", []):
            scores[str(entry["eip"])] = int(entry["score"])
        for key, value in doc.items():
            if "not_applicable" in key and isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict) and "eip" in entry:
                        not_applicable[str(entry["eip"])] = (entry.get("rationale") or "").strip()
    runtime = config.get("runtime") or {}
    rubric = config.get("rubric") or {}

    human: dict[str, dict] = {}
    snapshot_meta = load(f"{TASK09}/outputs/snapshot.yaml")
    for path in sorted(glob.glob(str(root / TASK09 / "outputs/assessments/eip-*.yaml"))):
        relative = str(Path(path).relative_to(root))
        doc = load(relative)
        candidate = next((c for c in doc.get("candidates", []) if c["candidate_id"] == doc.get("preferred_candidate")), None)
        if not candidate or candidate.get("parse_state") != "complete" or candidate.get("published_total") is None:
            continue
        source = candidate["source"]
        human[str(doc["eip"]["number"])] = {
            "total": candidate["published_total"],
            "checklist_revision": candidate.get("rubric_revision"),
            "status": doc["human_assessment_status"],
            "kind": source.get("kind"),
            "url": source.get("pull_request_url") or source.get("immutable_url"),
            "immutable_url": source.get("immutable_url"),
        }

    remote = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if remote.startswith("git@github.com:"):
        remote = "https://github.com/" + remote[len("git@github.com:"):]
    data = {
        "kind": "eip-complexity-comparison-data",
        "schema_version": 1,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"repository": remote.removesuffix(".git"), "commit": commit, "files": files},
        "checklist": CHECKLIST,
        "llm": {
            "model": runtime.get("model"),
            "reasoning_effort": runtime.get("reasoning_effort"),
            "checklist_revision": rubric.get("checklist_revision", 2),
            "eips_commit": summary["snapshot"]["commit"],
            "snapshot_id": summary["snapshot"]["snapshot_id"],
            "information_cutoff_at": summary["snapshot"].get("information_cutoff_at"),
            "site": "https://danceratopz.github.io/retrospective-eip-complexity/prospective/hegota/?evaluator=llm",
            "scores": scores,
            "not_applicable": not_applicable,
        },
        "human": {
            "snapshot_id": snapshot_meta.get("snapshot_id"),
            "captured_at": snapshot_meta.get("captured_at"),
            "upstream": snapshot_meta.get("upstream", {}).get("repository"),
            "site": "https://danceratopz.github.io/retrospective-eip-complexity/prospective/hegota/?evaluator=human",
            "entries": human,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(scores)} LLM totals, {len(not_applicable)} LLM not-applicable, {len(human)} human totals, from {remote} @ {commit[:7]}")


if __name__ == "__main__":
    main()
