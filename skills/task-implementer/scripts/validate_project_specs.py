#!/usr/bin/env python3
"""Validate Task Implementer project specs and emit the owner receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from prompt_workspace_core import PromptWorkspaceError  # noqa: E402
from prompt_workspace_specs import inspect_spec_documents  # noqa: E402


def validate(project_root: Path) -> dict[str, object]:
    """Recompute the Task-owned receipt from one exact selected project."""

    selected = project_root.expanduser().resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(selected), "rev-parse", "--show-toplevel"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "selected project is not in a Git worktree"
        ) from error
    git_root = Path(result.stdout.strip()).resolve()
    try:
        selected.relative_to(git_root)
    except ValueError as error:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "selected project escaped its Git worktree"
        ) from error
    inspected = inspect_spec_documents(
        {"repo_root": str(git_root), "source_root": str(selected)}
    )
    receipt = inspected["project_agent_spec_receipt"]
    if not isinstance(receipt, dict):
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "complete managed Task specifications are required"
        )
    return receipt


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Task Implementer specs and emit a v2 private receipt."
    )
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = validate(args.project_root)
    except PromptWorkspaceError as error:
        print(
            json.dumps(
                {"status": "blocked", "code": error.code, "error": error.message},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
