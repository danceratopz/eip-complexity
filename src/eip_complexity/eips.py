"""Read EIP Markdown from the local ``ethereum/EIPs`` clone at an exact commit.

The working tree of the clone is never consulted or modified: refs are resolved
against ``origin`` with ``git ls-remote``, ``git fetch`` runs only when the resolved
commit is not present locally, and file contents come from git object access
(``git show <sha>:EIPS/eip-N.md``).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from eip_complexity.errors import ComplexityError
from eip_complexity.hashing import sha256_bytes

EIP_REFERENCE_RE = re.compile(r"\b[Ee][Ii][Pp]-(\d{1,5})\b")
_COMMITISH_RE = re.compile(r"[0-9a-fA-F]{7,40}")


@dataclass(frozen=True)
class ResolvedRef:
    requested_ref: str | None
    remote_ref: str | None  # e.g. "refs/heads/master"; None when a raw commit was requested
    commit: str

    def to_dict(self) -> dict:
        return {"requested_ref": self.requested_ref, "remote_ref": self.remote_ref, "resolved_commit": self.commit}


@dataclass(frozen=True)
class EipDocument:
    number: int
    path: str  # repository-relative, e.g. "EIPS/eip-8141.md"
    markdown: str
    sha256: str
    blob_sha: str
    title: str
    requires: tuple[int, ...]


@dataclass(frozen=True)
class Candidate:
    """Another EIP that the target EIP's own source explicitly points at."""

    number: int
    referenced_via: tuple[str, ...]  # subset of ("requires", "body")


def parse_frontmatter(markdown: str, *, where: str) -> dict[str, str]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ComplexityError(f"{where}: Markdown does not start with a '---' frontmatter block")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        key, sep, value = line.partition(":")
        if sep and key.strip():
            fields[key.strip()] = value.strip()
    raise ComplexityError(f"{where}: frontmatter block is not closed")


def _parse_requires(value: str, *, where: str) -> tuple[int, ...]:
    if not value:
        return ()
    numbers = []
    for part in value.split(","):
        part = part.strip()
        if not part.isdigit():
            raise ComplexityError(f"{where}: unexpected 'requires' entry '{part}'")
        numbers.append(int(part))
    return tuple(numbers)


def build_document(number: int, path: str, data: bytes, blob_sha: str) -> EipDocument:
    where = f"EIP-{number} ({path})"
    try:
        markdown = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ComplexityError(f"{where}: not UTF-8: {error}") from error
    frontmatter = parse_frontmatter(markdown, where=where)
    if frontmatter.get("eip") != str(number):
        raise ComplexityError(f"{where}: frontmatter 'eip' is {frontmatter.get('eip')!r}, expected {number}")
    title = frontmatter.get("title", "").strip()
    if not title:
        raise ComplexityError(f"{where}: frontmatter has no 'title'")
    return EipDocument(
        number=number,
        path=path,
        markdown=markdown,
        sha256=sha256_bytes(data),
        blob_sha=blob_sha,
        title=title,
        requires=_parse_requires(frontmatter.get("requires", ""), where=where),
    )


def extract_candidates(document: EipDocument) -> list[Candidate]:
    """EIPs named in ``requires`` or as explicit ``EIP-NNNN`` text, excluding the EIP itself."""
    via: dict[int, set[str]] = {}
    for number in document.requires:
        via.setdefault(number, set()).add("requires")
    for match in EIP_REFERENCE_RE.finditer(document.markdown):
        via.setdefault(int(match.group(1)), set()).add("body")
    via.pop(document.number, None)
    return [Candidate(number, tuple(sorted(via[number]))) for number in sorted(via)]


class EipsRepo:
    """A read-only view of the local ethereum/EIPs clone."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_dir():
            raise ComplexityError(f"EIPs repository {self.path} does not exist or is not a directory")
        try:
            self._git("rev-parse", "--git-dir")
        except ComplexityError as error:
            raise ComplexityError(f"EIPs repository {self.path} is not a git repository: {error}") from error

    def _git(self, *args: str, binary: bool = False) -> str | bytes:
        completed = subprocess.run(
            ["git", "-C", str(self.path), *args], capture_output=True, check=False
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ComplexityError(f"git {' '.join(args)} failed in {self.path}: {stderr or completed.returncode}")
        return completed.stdout if binary else completed.stdout.decode("utf-8").strip()

    def origin_url(self) -> str:
        return str(self._git("remote", "get-url", "origin"))

    def _has_commit(self, commit: str) -> bool:
        try:
            self._git("cat-file", "-e", f"{commit}^{{commit}}")
        except ComplexityError:
            return False
        return True

    def _ensure_local(self, commit: str) -> None:
        if self._has_commit(commit):
            return
        self._git("fetch", "--quiet", "origin")
        if not self._has_commit(commit):
            raise ComplexityError(f"commit {commit} is still not available in {self.path} after 'git fetch origin'")

    def resolve(self, requested_ref: str | None) -> ResolvedRef:
        """Resolve ``requested_ref`` (or the remote default branch) to an exact commit."""
        if requested_ref is None:
            return self._resolve_default_branch()
        listing = str(self._git("ls-remote", "origin", requested_ref))
        heads: dict[str, str] = {}
        peeled: dict[str, str] = {}
        for line in listing.splitlines():
            sha, _, ref = line.partition("\t")
            if ref.endswith("^{}"):
                peeled[ref[:-3]] = sha
            elif ref:
                heads[ref] = sha
        if len(heads) > 1:
            raise ComplexityError(f"ref '{requested_ref}' is ambiguous on origin: {sorted(heads)}")
        if len(heads) == 1:
            (ref, sha), = heads.items()
            commit = peeled.get(ref, sha)
            self._ensure_local(commit)
            return ResolvedRef(requested_ref, ref, commit)
        if not _COMMITISH_RE.fullmatch(requested_ref):
            raise ComplexityError(f"ref '{requested_ref}' was not found on origin and is not a commit hash")
        try:
            commit = str(self._git("rev-parse", "--verify", "--quiet", f"{requested_ref}^{{commit}}"))
        except ComplexityError:
            self._git("fetch", "--quiet", "origin")
            try:
                commit = str(self._git("rev-parse", "--verify", "--quiet", f"{requested_ref}^{{commit}}"))
            except ComplexityError as error:
                raise ComplexityError(f"commit '{requested_ref}' not found locally or on origin") from error
        return ResolvedRef(requested_ref, None, commit)

    def _resolve_default_branch(self) -> ResolvedRef:
        listing = str(self._git("ls-remote", "--symref", "origin", "HEAD"))
        remote_ref = commit = None
        for line in listing.splitlines():
            if line.startswith("ref: ") and line.endswith("\tHEAD"):
                remote_ref = line[len("ref: ") : -len("\tHEAD")]
            elif line.endswith("\tHEAD"):
                commit = line.split("\t", 1)[0]
        if not remote_ref or not commit:
            raise ComplexityError(f"could not determine the default branch of origin from: {listing!r}")
        self._ensure_local(commit)
        return ResolvedRef(None, remote_ref, commit)

    def read_eip(self, commit: str, number: int) -> EipDocument:
        path = f"EIPS/eip-{number}.md"
        try:
            blob_sha = str(self._git("rev-parse", "--verify", "--quiet", f"{commit}:{path}"))
        except ComplexityError as error:
            raise ComplexityError(f"EIP-{number}: {path} does not exist at commit {commit}") from error
        data = self._git("show", f"{commit}:{path}", binary=True)
        assert isinstance(data, bytes)
        return build_document(number, path, data, blob_sha)
