"""The TOML run configuration. Every meaningful input to a run lives here."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from eip_complexity.errors import ComplexityError
from eip_complexity.hashing import sha256_bytes

DEFAULT_JEV_MODEL = "jev-latest"
DEFAULT_EIPS_REPO = "../EIPs"
DEFAULT_TEMPLATE = "templates/EIP-Complexity-Assessment.md"

_ALLOWED_TOP_LEVEL = {
    "fork",
    "eips_repo",
    "eips_ref",
    "complexity_template",
    "jev_model",
    "meta_eip",
    "output_dir",
    "cache_dir",
    "render_html",
    "force",
    "eips",
    "human_assessments",
    "comparison",
}
_ALLOWED_HUMAN_KEYS = {"repo", "path", "branch", "pr_query"}
_ALLOWED_COMPARISON_KEYS = {"page", "source_url", "source_label"}
_ALLOWED_EIP_KEYS = {"number", "stage", "layer"}

#: Values accepted for the per-EIP ``layer`` field.
LAYERS = ("execution", "consensus", "cross-layer", "networking", "informational")
#: EIPs whose layer is one of these are recorded as not applicable to the checklist (an
#: execution-layer rubric) instead of being sent to Jev. The layer is configuration, not a judgment.
NOT_APPLICABLE_LAYERS = ("consensus", "informational")


@dataclass(frozen=True)
class EipEntry:
    number: int
    stage: str | None  # display-only process metadata such as "CFI" or "PFI"
    layer: str | None = None  # one of LAYERS; consensus/informational EIPs are recorded as not applicable

    @property
    def applicable(self) -> bool:
        return self.layer not in NOT_APPLICABLE_LAYERS


@dataclass(frozen=True)
class HumanAssessments:
    """Display-only pointer to where human checklists for the same EIPs are maintained on GitHub."""

    repo: str  # "owner/name"
    path: str  # directory of committed evaluations
    branch: str
    pr_query: str  # GitHub pull-request search filter

    def to_dict(self) -> dict:
        return {"repo": self.repo, "path": self.path, "branch": self.branch, "pr_query": self.pr_query}


@dataclass(frozen=True)
class Comparison:
    """Display-only pointer to a comparison page kept next to the run, and to the external study it draws on."""

    page: str  # file name relative to output_dir, e.g. "comparison.html"
    source_url: str | None
    source_label: str

    def to_dict(self) -> dict:
        return {"page": self.page, "source_url": self.source_url, "source_label": self.source_label}


@dataclass(frozen=True)
class RunConfig:
    path: Path
    sha256: str
    fork: str
    meta_eip: int | None  # display-only: the hardfork meta EIP the list was taken from
    human_assessments: HumanAssessments | None
    comparison: Comparison | None
    eips_repo: Path
    eips_ref: str | None
    complexity_template: Path
    jev_model: str
    output_dir: Path
    cache_dir: Path
    render_html: bool
    force: bool
    eips: tuple[EipEntry, ...]


def _expect(mapping: dict, key: str, kind: type, *, default, where: str):
    if key not in mapping:
        return default
    value = mapping[key]
    if kind is int and isinstance(value, bool):  # bool is an int subclass; reject it
        raise ComplexityError(f"{where}: '{key}' must be {kind.__name__}, got bool")
    if not isinstance(value, kind):
        raise ComplexityError(f"{where}: '{key}' must be {kind.__name__}, got {type(value).__name__}")
    return value


def load_config(path: Path) -> RunConfig:
    """Read, validate and normalize the run configuration.

    Relative paths are resolved against the current working directory, so a run is
    normally started from the repository root.
    """
    path = Path(path)
    where = f"config {path}"
    try:
        raw_bytes = path.read_bytes()
    except OSError as error:
        raise ComplexityError(f"{where}: cannot read: {error}") from error
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ComplexityError(f"{where}: invalid TOML: {error}") from error

    unknown = set(data) - _ALLOWED_TOP_LEVEL
    if unknown:
        raise ComplexityError(f"{where}: unknown keys {sorted(unknown)}")

    fork = _expect(data, "fork", str, default=None, where=where)
    if not fork or not fork.strip():
        raise ComplexityError(f"{where}: 'fork' is required and must be a non-empty string")

    meta_eip = _expect(data, "meta_eip", int, default=None, where=where)
    if meta_eip is not None and meta_eip <= 0:
        raise ComplexityError(f"{where}: 'meta_eip' must be a positive integer")

    human_assessments = None
    human_raw = _expect(data, "human_assessments", dict, default=None, where=where)
    if human_raw is not None:
        human_where = f"{where}: [human_assessments]"
        unknown = set(human_raw) - _ALLOWED_HUMAN_KEYS
        if unknown:
            raise ComplexityError(f"{human_where}: unknown keys {sorted(unknown)}")
        repo = _expect(human_raw, "repo", str, default="", where=human_where).strip()
        if repo.count("/") != 1 or not all(repo.split("/")):
            raise ComplexityError(f"{human_where}: 'repo' must be a GitHub 'owner/name'")
        human_assessments = HumanAssessments(
            repo=repo,
            path=_expect(human_raw, "path", str, default="", where=human_where).strip().strip("/"),
            branch=_expect(human_raw, "branch", str, default="main", where=human_where).strip() or "main",
            pr_query=_expect(human_raw, "pr_query", str, default="is:pr complexity", where=human_where).strip(),
        )

    comparison = None
    comparison_raw = _expect(data, "comparison", dict, default=None, where=where)
    if comparison_raw is not None:
        comparison_where = f"{where}: [comparison]"
        unknown = set(comparison_raw) - _ALLOWED_COMPARISON_KEYS
        if unknown:
            raise ComplexityError(f"{comparison_where}: unknown keys {sorted(unknown)}")
        page = _expect(comparison_raw, "page", str, default="", where=comparison_where).strip()
        if not page or "/" in page or page.startswith("."):
            raise ComplexityError(f"{comparison_where}: 'page' must be a file name inside output_dir")
        comparison = Comparison(
            page=page,
            source_url=_expect(comparison_raw, "source_url", str, default="", where=comparison_where).strip() or None,
            source_label=_expect(comparison_raw, "source_label", str, default="an external study", where=comparison_where).strip(),
        )

    eips_ref = _expect(data, "eips_ref", str, default="", where=where).strip() or None
    jev_model = _expect(data, "jev_model", str, default=DEFAULT_JEV_MODEL, where=where).strip()
    if not jev_model:
        raise ComplexityError(f"{where}: 'jev_model' must not be empty (omit it for {DEFAULT_JEV_MODEL})")

    output_dir = _expect(data, "output_dir", str, default=None, where=where)
    if not output_dir:
        raise ComplexityError(f"{where}: 'output_dir' is required")
    cache_dir = _expect(data, "cache_dir", str, default=None, where=where)

    eips_raw = _expect(data, "eips", list, default=None, where=where)
    if not eips_raw:
        raise ComplexityError(f"{where}: at least one [[eips]] entry is required")
    entries: list[EipEntry] = []
    for index, item in enumerate(eips_raw):
        item_where = f"{where}: [[eips]] entry {index + 1}"
        if not isinstance(item, dict):
            raise ComplexityError(f"{item_where}: must be a table")
        unknown = set(item) - _ALLOWED_EIP_KEYS
        if unknown:
            raise ComplexityError(f"{item_where}: unknown keys {sorted(unknown)}")
        number = _expect(item, "number", int, default=None, where=item_where)
        if number is None or number <= 0:
            raise ComplexityError(f"{item_where}: 'number' must be a positive integer")
        stage = _expect(item, "stage", str, default=None, where=item_where)
        layer = _expect(item, "layer", str, default=None, where=item_where)
        if layer is not None and layer not in LAYERS:
            raise ComplexityError(f"{item_where}: 'layer' must be one of {list(LAYERS)}, got {layer!r}")
        entries.append(EipEntry(number=number, stage=stage.strip() if stage else None, layer=layer))
    numbers = [entry.number for entry in entries]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicates:
        raise ComplexityError(f"{where}: duplicate EIP numbers {duplicates}")

    output_path = Path(output_dir)
    return RunConfig(
        path=path,
        sha256=sha256_bytes(raw_bytes),
        fork=fork.strip(),
        meta_eip=meta_eip,
        human_assessments=human_assessments,
        comparison=comparison,
        eips_repo=Path(_expect(data, "eips_repo", str, default=DEFAULT_EIPS_REPO, where=where)),
        eips_ref=eips_ref,
        complexity_template=Path(_expect(data, "complexity_template", str, default=DEFAULT_TEMPLATE, where=where)),
        jev_model=jev_model,
        output_dir=output_path,
        cache_dir=Path(cache_dir) if cache_dir else output_path / "cache",
        render_html=_expect(data, "render_html", bool, default=True, where=where),
        force=_expect(data, "force", bool, default=False, where=where),
        eips=tuple(entries),
    )
