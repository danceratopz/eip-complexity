"""Parse ``templates/EIP-Complexity-Assessment.md`` into an assessment definition.

The template is an input, not documentation to duplicate in code. Everything the
evaluation needs (anchors, score definitions, notes, the exceptional score, tier
thresholds and the uncapped Cross-EIP rule) is read from it here, and parsing fails
loudly whenever the document does not have the structure this parser expects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from eip_complexity.errors import ComplexityError
from eip_complexity.gitinfo import describe_repo, head_blob_sha, public_git_info
from eip_complexity.hashing import git_blob_sha1, sha256_bytes

#: Anchor counts the tool insists on for checklist revisions it knows.
EXPECTED_ANCHOR_COUNTS = {2: 28}

_REVISION_RE = re.compile(r"Checklist revision:\s*\*\*(\d+)\*\*\s*\((\d+) anchors\)")
_SCALE_RE = re.compile(r"scored on a (\d+)\s*[–-]\s*(\d+) scale")
_EXCEPTIONAL_RE = re.compile(r"A score of (\d+) may be used in exceptional circumstances[^.\n]*\.")
_SCORE_LINE_RE = re.compile(r"^- (\d+)\. (.+)$")
_UNCAPPED_RULE_RE = re.compile(
    r"^- \*\*\+(\d+) for every (\d+) additional interacting EIPs beyond the first (\d+)\*\*,?\s*(.*)$"
)
_CHECKLIST_ROW_RE = re.compile(r"^\|\s*\*\*(.+?)\*\*([^|]*)\|", re.M)
_TIER_ROW_RE = re.compile(r"^\|\s*(\S+)\s+\*\*(.+?)\*\*\s*\|\s*\*\*(.+?)\*\*\s*\|", re.M)


@dataclass(frozen=True)
class UncappedRule:
    """``+increment`` for every ``per_additional`` interacting EIPs beyond ``beyond_first``."""

    increment: int
    per_additional: int
    beyond_first: int
    text: str

    def to_dict(self) -> dict:
        return {
            "increment": self.increment,
            "per_additional": self.per_additional,
            "beyond_first": self.beyond_first,
            "text": self.text,
        }


@dataclass(frozen=True)
class Anchor:
    id: str
    name: str
    checklist_label: str
    description: str
    notes: tuple[str, ...]
    criteria: dict[int, str]  # only the scores the template defines; may be sparse
    uncapped_rule: UncappedRule | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "checklist_label": self.checklist_label,
            "description": self.description,
            "notes": list(self.notes),
            "criteria": {str(score): text for score, text in self.criteria.items()},
            "uncapped_rule": self.uncapped_rule.to_dict() if self.uncapped_rule else None,
        }


@dataclass(frozen=True)
class Tier:
    name: str
    emoji: str
    min_inclusive: int
    max_exclusive: int | None
    range_text: str

    def contains(self, total: int) -> bool:
        return total >= self.min_inclusive and (self.max_exclusive is None or total < self.max_exclusive)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "emoji": self.emoji,
            "min_inclusive": self.min_inclusive,
            "max_exclusive": self.max_exclusive,
            "range": self.range_text,
        }


@dataclass(frozen=True)
class AssessmentTemplate:
    revision: int
    anchors: tuple[Anchor, ...]
    tiers: tuple[Tier, ...]
    max_defined_score: int
    exceptional_score: int
    exceptional_score_text: str

    @property
    def cross_eip_anchor(self) -> Anchor | None:
        for anchor in self.anchors:
            if anchor.uncapped_rule is not None:
                return anchor
        return None

    def anchor(self, anchor_id: str) -> Anchor:
        for anchor in self.anchors:
            if anchor.id == anchor_id:
                return anchor
        raise KeyError(anchor_id)

    def tier_for(self, total: int) -> Tier:
        for tier in self.tiers:
            if tier.contains(total):
                return tier
        raise ComplexityError(f"total score {total} falls outside every tier range")

    def to_dict(self) -> dict:
        return {
            "revision": self.revision,
            "anchor_count": len(self.anchors),
            "max_defined_score": self.max_defined_score,
            "exceptional_score": self.exceptional_score,
            "exceptional_score_text": self.exceptional_score_text,
            "anchors": [anchor.to_dict() for anchor in self.anchors],
            "tiers": [tier.to_dict() for tier in self.tiers],
        }


def anchor_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _section(text: str, start_heading: str, end_heading: str, *, where: str) -> str:
    start = text.find(f"\n{start_heading}\n")
    if start < 0:
        raise ComplexityError(f"{where}: heading '{start_heading}' not found")
    end = text.find(f"\n{end_heading}", start + 1)
    if end < 0:
        raise ComplexityError(f"{where}: heading '{end_heading}' not found after '{start_heading}'")
    return text[start + len(start_heading) + 2 : end]


def _parse_anchor_block(block: str, *, exceptional_score: int, where: str) -> tuple[str, str, tuple[str, ...], dict[int, str], UncappedRule | None]:
    heading, _, body = block.partition("\n")
    name = heading.strip()
    if not name:
        raise ComplexityError(f"{where}: anchor with an empty heading")
    description: list[str] = []
    notes: list[str] = []
    criteria: dict[int, str] = {}
    rule: UncappedRule | None = None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if match := _SCORE_LINE_RE.match(line):
            score = int(match.group(1))
            if score in criteria:
                raise ComplexityError(f"{where}: anchor '{name}' defines score {score} twice")
            criteria[score] = match.group(2).strip()
        elif match := _UNCAPPED_RULE_RE.match(line):
            if rule is not None:
                raise ComplexityError(f"{where}: anchor '{name}' has two uncapped rules")
            rule = UncappedRule(
                increment=int(match.group(1)),
                per_additional=int(match.group(2)),
                beyond_first=int(match.group(3)),
                text=line[2:].strip(),
            )
        elif not criteria and rule is None:
            description.append(line)
        else:
            notes.append(re.sub(r"^\*(?!\*)", "", line).strip())
    if not criteria:
        raise ComplexityError(f"{where}: anchor '{name}' defines no '- N. ...' score lines")
    scores = sorted(criteria)
    if scores[0] != 0:
        raise ComplexityError(f"{where}: anchor '{name}' does not define score 0")
    if scores[-1] > exceptional_score:
        raise ComplexityError(f"{where}: anchor '{name}' defines score {scores[-1]} above the exceptional score")
    if scores != list(criteria):
        raise ComplexityError(f"{where}: anchor '{name}' lists scores out of order")
    if rule is not None and rule.per_additional <= 0:
        raise ComplexityError(f"{where}: anchor '{name}' has an uncapped rule with a zero divisor")
    return name, "\n".join(description), tuple(notes), criteria, rule


def _parse_tier_range(range_text: str, *, where: str) -> tuple[int, int | None]:
    compact = range_text.replace(" ", "")
    if match := re.fullmatch(r"<(\d+)", compact):
        return 0, int(match.group(1))
    if match := re.fullmatch(r">=(\d+)<(\d+)", compact):
        return int(match.group(1)), int(match.group(2))
    if match := re.fullmatch(r">=(\d+)", compact):
        return int(match.group(1)), None
    raise ComplexityError(f"{where}: unrecognized tier range '{range_text}'")


def parse_template(text: str, *, where: str = "template") -> AssessmentTemplate:
    """Parse the Markdown template. Raises ``ComplexityError`` on any structural surprise."""
    revision_match = _REVISION_RE.search(text)
    if not revision_match:
        raise ComplexityError(f"{where}: 'Checklist revision: **N** (M anchors)' header not found")
    revision = int(revision_match.group(1))
    declared_count = int(revision_match.group(2))

    anchors_section = _section(text, "#### Anchors", "### Checklist", where=where)
    blocks = re.split(r"^##### ", anchors_section, flags=re.M)
    intro, anchor_blocks = blocks[0], blocks[1:]

    scale_match = _SCALE_RE.search(intro)
    if not scale_match or int(scale_match.group(1)) != 0:
        raise ComplexityError(f"{where}: 'scored on a 0–N scale' sentence not found in the anchors introduction")
    max_defined_score = int(scale_match.group(2))
    exceptional_match = _EXCEPTIONAL_RE.search(intro)
    if not exceptional_match:
        raise ComplexityError(f"{where}: 'A score of N may be used in exceptional circumstances ...' sentence not found")
    exceptional_score = int(exceptional_match.group(1))
    if exceptional_score <= max_defined_score:
        raise ComplexityError(f"{where}: exceptional score {exceptional_score} is not above the defined scale")

    checklist_section = _section(text, "### Checklist", "**Total", where=where)
    labels = [f"{m.group(1).strip()} {m.group(2).strip()}".strip() for m in _CHECKLIST_ROW_RE.finditer(checklist_section)]
    labels = [label for label in labels if _normalize(label) != "anchor"]

    if len(anchor_blocks) != declared_count:
        raise ComplexityError(
            f"{where}: header declares {declared_count} anchors but {len(anchor_blocks)} '#####' anchor sections were found"
        )
    if len(labels) != declared_count:
        raise ComplexityError(f"{where}: checklist table has {len(labels)} rows but {declared_count} anchors are declared")
    expected = EXPECTED_ANCHOR_COUNTS.get(revision)
    if expected is not None and declared_count != expected:
        raise ComplexityError(f"{where}: revision {revision} must have {expected} anchors, found {declared_count}")

    anchors: list[Anchor] = []
    seen_ids: set[str] = set()
    for block, label in zip(anchor_blocks, labels, strict=True):
        name, description, notes, criteria, rule = _parse_anchor_block(block, exceptional_score=exceptional_score, where=where)
        if not _normalize(label).startswith(_normalize(name)):
            raise ComplexityError(f"{where}: anchor '{name}' does not match checklist row '{label}' at the same position")
        anchor_id = anchor_slug(name)
        if anchor_id in seen_ids:
            raise ComplexityError(f"{where}: two anchors share the id '{anchor_id}'")
        seen_ids.add(anchor_id)
        anchors.append(Anchor(anchor_id, name, label, description, notes, criteria, rule))
    if sum(1 for anchor in anchors if anchor.uncapped_rule) > 1:
        raise ComplexityError(f"{where}: more than one anchor carries an uncapped rule")

    tier_section = _section(text, "##### Tier Interpretation", "#####", where=where)
    tiers: list[Tier] = []
    for match in _TIER_ROW_RE.finditer(tier_section):
        emoji, name, range_text = match.group(1), match.group(2).strip(), match.group(3).strip()
        low, high = _parse_tier_range(range_text, where=where)
        tiers.append(Tier(name=name, emoji=emoji, min_inclusive=low, max_exclusive=high, range_text=range_text))
    if not tiers:
        raise ComplexityError(f"{where}: no tier rows found under 'Tier Interpretation'")
    tiers.sort(key=lambda tier: tier.min_inclusive)
    if tiers[0].min_inclusive != 0 or tiers[-1].max_exclusive is not None:
        raise ComplexityError(f"{where}: tier ranges do not start at 0 and end open-ended")
    for lower, upper in zip(tiers, tiers[1:]):
        if lower.max_exclusive != upper.min_inclusive:
            raise ComplexityError(f"{where}: tier ranges '{lower.range_text}' and '{upper.range_text}' are not contiguous")

    return AssessmentTemplate(
        revision=revision,
        anchors=tuple(anchors),
        tiers=tuple(tiers),
        max_defined_score=max_defined_score,
        exceptional_score=exceptional_score,
        exceptional_score_text=exceptional_match.group(0).strip(),
    )


@dataclass(frozen=True)
class TemplateSource:
    """Exactly which template bytes were used, with git provenance when available."""

    path: Path
    sha256: str
    blob_sha1: str
    git: dict | None  # {"commit", "dirty", "remote_url", "relative_path", "head_blob_sha", "matches_head"}

    def to_dict(self) -> dict:
        return {"path": str(self.path), "sha256": self.sha256, "git_blob_sha1": self.blob_sha1, "git": self.git}


def load_template(path: Path, *, exclude_from_dirty: tuple[Path, ...] = ()) -> tuple[AssessmentTemplate, TemplateSource]:
    """Parse the template at ``path`` and record exactly which bytes were used.

    ``exclude_from_dirty`` names directories (a run's output and cache) whose tracked
    changes must not count towards the enclosing repository's dirty state.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ComplexityError(f"template {path}: cannot read: {error}") from error
    template = parse_template(data.decode("utf-8"), where=f"template {path}")
    blob = git_blob_sha1(data)
    git = describe_repo(path.resolve(), exclude=exclude_from_dirty)
    if git is not None:
        toplevel = Path(git["toplevel"])
        relative = path.resolve().relative_to(toplevel)
        head_blob = head_blob_sha(toplevel, relative)
        git = {**public_git_info(git), "relative_path": relative.as_posix(), "head_blob_sha": head_blob, "matches_head": head_blob == blob}
    return template, TemplateSource(path=path, sha256=sha256_bytes(data), blob_sha1=blob, git=git)
