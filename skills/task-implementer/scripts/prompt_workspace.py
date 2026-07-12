#!/usr/bin/env python3
"""CLI for the private task-implementer prompt workspace."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from prompt_workspace_core import (  # noqa: E402
    MAX_PROMPT_BYTES,
    PROMPT_SCHEMA,
    RUN_SCHEMA,
    WORKSPACE_SCHEMA,
    PromptWorkspaceError,
    create_prompt,
    init_workspace,
    prompt_slug,
    verify_workspace,
)
from prompt_workspace_runs import (  # noqa: E402
    prompt_rows,
    snapshot_prompt,
    verify_command,
    verify_run,
)


__all__ = [
    "MAX_PROMPT_BYTES",
    "PROMPT_SCHEMA",
    "RUN_SCHEMA",
    "WORKSPACE_SCHEMA",
    "PromptWorkspaceError",
    "create_prompt",
    "init_workspace",
    "main",
    "prompt_rows",
    "prompt_slug",
    "snapshot_prompt",
    "verify_command",
    "verify_run",
    "verify_workspace",
]


def open_in_editor(editor: str, target: Path, *, workspace: bool) -> None:
    arguments = [editor]
    if workspace:
        arguments.extend(("--new-window", str(target)))
    else:
        arguments.extend(("--reuse-window", "--goto", str(target)))
    try:
        result = subprocess.run(arguments, check=False, timeout=15)
    except FileNotFoundError:
        print(f"WARN editor executable is unavailable; open manually: {target}", file=sys.stderr)
        return
    except subprocess.TimeoutExpired:
        print(f"WARN editor did not return promptly; target: {target}", file=sys.stderr)
        return
    if result.returncode != 0:
        print(f"WARN editor exited with status {result.returncode}; open manually: {target}", file=sys.stderr)


def add_common_workspace(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--json", action="store_true")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage private task-implementer prompt workspaces."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or verify a private workspace.")
    init_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    init_parser.add_argument("--scope", default=".")
    init_parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
    )
    init_parser.add_argument("--open", action="store_true")
    init_parser.add_argument(
        "--editor", default=os.environ.get("TASK_IMPLEMENTER_EDITOR", "code")
    )
    init_parser.add_argument("--json", action="store_true")

    new_parser = subparsers.add_parser("new", help="Create one prompt file for one ask.")
    add_common_workspace(new_parser)
    new_parser.add_argument("--ask", required=True)
    new_parser.add_argument("--open", action="store_true")
    new_parser.add_argument(
        "--editor", default=os.environ.get("TASK_IMPLEMENTER_EDITOR", "code")
    )

    list_parser = subparsers.add_parser("list", help="List prompt metadata without bodies.")
    add_common_workspace(list_parser)
    list_parser.add_argument("--query")
    list_parser.add_argument("--date")

    snapshot_parser = subparsers.add_parser(
        "snapshot", help="Create an immutable submitted prompt revision."
    )
    add_common_workspace(snapshot_parser)
    snapshot_parser.add_argument("--prompt", required=True, type=Path)
    snapshot_parser.add_argument("--run-id")
    snapshot_parser.add_argument("--new-run", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify", help="Validate workspace, prompt, snapshot, and drift state."
    )
    add_common_workspace(verify_parser)
    verify_parser.add_argument("--prompt", type=Path)
    verify_parser.add_argument("--run-id")
    return parser.parse_args(argv)


def emit(value: object, json_output: bool) -> None:
    if json_output:
        print(json.dumps(value, sort_keys=True))
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                print(
                    "\t".join(
                        re.sub(
                            r"[\x00-\x1f\x7f]",
                            " ",
                            str(item.get(key) or "-"),
                        )
                        for key in (
                            "created_at",
                            "modified_at",
                            "last_submitted_at",
                            "status",
                            "prompt_id",
                            "title",
                            "latest_run_id",
                            "path",
                        )
                    )
                )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict):
                print(f"{key}: {json.dumps(item, sort_keys=True)}")
            else:
                print(f"{key}: {item}")
        return
    print(value)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.command == "init":
            result = init_workspace(args.repo_root, args.scope, args.codex_home)
            if args.open:
                open_in_editor(args.editor, Path(str(result["vscode_workspace"])), workspace=True)
        elif args.command == "new":
            result = create_prompt(args.workspace, args.ask)
            if args.open:
                open_in_editor(args.editor, Path(str(result["path"])), workspace=False)
        elif args.command == "list":
            result = prompt_rows(args.workspace, args.query, args.date)
        elif args.command == "snapshot":
            result = snapshot_prompt(
                args.workspace,
                args.prompt,
                run_id=args.run_id,
                force_new_run=args.new_run,
            )
        elif args.command == "verify":
            if args.run_id is not None and args.prompt is None:
                result = verify_command(args.workspace, None, args.run_id)
            else:
                result = verify_command(args.workspace, args.prompt, args.run_id)
        else:  # pragma: no cover - argparse enforces the command set.
            raise AssertionError(args.command)
    except PromptWorkspaceError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2
    emit(result, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
