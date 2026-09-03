"""Load a tracked baseline from the merge base without shell execution."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _git(
    project_root: Path,
    argv: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *argv],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(argv)} failed: {detail}")
    return result


def merge_base_payload(
    project_root: Path,
    current_baseline: Path,
    base_ref: str | None,
) -> dict[str, Any] | None:
    """Return the baseline at merge-base, or None for a proven initial adoption."""
    normalized_ref = str(base_ref or "").strip()
    if not normalized_ref:
        return None
    if set(normalized_ref) == {"0"}:
        raise RuntimeError("baseline comparison ref must not be an all-zero object id")

    git_root = Path(_git(project_root, ["rev-parse", "--show-toplevel"]).stdout.strip())
    try:
        relative = current_baseline.resolve(strict=True).relative_to(git_root.resolve(strict=True))
    except ValueError as exc:
        raise RuntimeError("baseline path must be inside the current Git worktree") from exc
    base_commit = _git(
        project_root,
        ["rev-parse", "--verify", "--end-of-options", f"{normalized_ref}^{{commit}}"],
    ).stdout.strip()
    merge_base = _git(project_root, ["merge-base", base_commit, "HEAD"]).stdout.strip()
    if not merge_base:
        raise RuntimeError(f"git merge-base returned no commit for {normalized_ref}")
    object_name = f"{merge_base}:{relative.as_posix()}"
    exists = _git(project_root, ["cat-file", "-e", object_name], check=False)
    if exists.returncode != 0:
        tracked = _git(
            project_root,
            ["ls-tree", "--name-only", "--full-tree", merge_base, "--", relative.as_posix()],
        )
        if not tracked.stdout.strip():
            return None
        detail = (exists.stderr or exists.stdout).strip()
        raise RuntimeError(f"cannot read merge-base baseline {relative.as_posix()}: {detail}")
    raw = _git(project_root, ["show", object_name]).stdout
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("merge-base baseline is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("merge-base baseline must be a JSON object")
    return payload
