"""Read-only git queries used for provenance. Nothing here mutates a repository."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path


def git_output(args: list[str], cwd: Path) -> str | None:
    """Run ``git`` in ``cwd`` and return stdout, or ``None`` if git fails or is missing."""
    try:
        completed = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def repo_toplevel(path: Path) -> Path | None:
    """The working-tree root of the repository containing ``path``, if any."""
    directory = path if path.is_dir() else path.parent
    out = git_output(["rev-parse", "--show-toplevel"], directory)
    return Path(out) if out else None


def describe_repo(path: Path, *, exclude: Iterable[Path] = ()) -> dict | None:
    """``{"toplevel", "commit", "dirty", "remote_url"}`` for the repository containing ``path``.

    ``dirty`` considers tracked files only; untracked files do not make a tree dirty, and
    neither do changes under any of the ``exclude`` directories (a run's own output and
    cache, which are rewritten while the run is in progress). Returns ``None`` when
    ``path`` is not inside a git repository.
    """
    top = repo_toplevel(path)
    if top is None:
        return None
    commit = git_output(["rev-parse", "HEAD"], top)
    pathspecs = ["--", "."]
    for directory in exclude:
        try:
            relative = Path(directory).resolve().relative_to(top.resolve())
        except ValueError:
            continue  # outside this repository; nothing to exclude
        pathspecs.append(f":(exclude){relative.as_posix()}")
    status = git_output(["status", "--porcelain", "--untracked-files=no", *pathspecs], top)
    return {
        "toplevel": str(top),
        "commit": commit,
        "dirty": None if status is None else bool(status),
        "remote_url": git_output(["remote", "get-url", "origin"], top),
    }


def public_git_info(info: dict | None) -> dict | None:
    """The part of ``describe_repo`` worth publishing: everything except the local path."""
    if info is None:
        return None
    return {key: value for key, value in info.items() if key != "toplevel"}


def head_blob_sha(toplevel: Path, relative_path: Path) -> str | None:
    """Blob SHA of ``relative_path`` at ``HEAD``, or ``None`` if untracked."""
    return git_output(["rev-parse", "--verify", "--quiet", f"HEAD:{relative_path.as_posix()}"], toplevel)
