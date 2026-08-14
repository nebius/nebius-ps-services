#!/usr/bin/env python3
"""Coordinate canonical project specs, migration, and lifecycle receipts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from project_specs_lib.contracts import (
    ProjectSpecError,
    inspect_project,
    validate_project,
)
from project_specs_lib.lifecycle import (
    open_implementation,
    plan,
    seal,
    start_prompt,
    waive,
    write_validation_receipt,
)
from project_specs_lib.migration import migrate_project, recover_migration


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Maintain one canonical requirements/design contract per selected project."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "migrate", "recover"):
        command = subparsers.add_parser(name)
        command.add_argument("--project-root", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--project-root", type=Path, required=True)
    validate_parser.add_argument("--output", type=Path)
    validate_parser.add_argument("--session-id")
    validate_parser.add_argument("--task-implementer-workspace", type=Path)
    validate_parser.add_argument("--task-implementer-run-id")
    start_parser = subparsers.add_parser("start-prompt")
    start_parser.add_argument("--project-root", type=Path, required=True)
    start_parser.add_argument("--session-id", required=True)
    start_parser.add_argument("--turn-id", required=True)
    for name in ("plan", "open", "seal", "waive"):
        command = subparsers.add_parser(name)
        command.add_argument("--project-root", type=Path, required=True)
        command.add_argument("--session-id", required=True)
        turn = command.add_mutually_exclusive_group(required=True)
        turn.add_argument("--turn-id")
        turn.add_argument("--turn-token")
    subparsers.choices["plan"].add_argument("--rules-file", type=Path, required=True)
    subparsers.choices["plan"].add_argument(
        "--render-state-file", type=Path, required=True
    )
    subparsers.choices["plan"].add_argument(
        "--project-instructions-private-root", type=Path, required=True
    )
    subparsers.choices["seal"].add_argument(
        "--project-instructions-state", type=Path, required=True
    )
    subparsers.choices["seal"].add_argument(
        "--project-instructions-private-root", type=Path, required=True
    )
    subparsers.choices["waive"].add_argument(
        "--reason",
        choices=("documentation-only", "read-only", "non-project", "project-policy"),
        required=True,
    )
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            output = inspect_project(args.project_root, require_tracked=False)
        elif args.command == "validate":
            output = (
                write_validation_receipt(
                    args.project_root,
                    args.output,
                    args.session_id,
                    task_implementer_workspace=args.task_implementer_workspace,
                    task_implementer_run_id=args.task_implementer_run_id,
                )
                if args.output is not None
                else validate_project(args.project_root)
            )
        elif args.command == "migrate":
            output = migrate_project(args.project_root)
        elif args.command == "recover":
            output = recover_migration(args.project_root)
        elif args.command == "start-prompt":
            output = start_prompt(args.project_root, args.session_id, args.turn_id)
        elif args.command == "plan":
            output = plan(
                args.project_root,
                args.session_id,
                args.turn_id,
                turn_token=args.turn_token,
                rules_file=args.rules_file,
                render_state_file=args.render_state_file,
                project_instructions_private_root=(
                    args.project_instructions_private_root
                ),
            )
        elif args.command == "open":
            output = open_implementation(
                args.project_root,
                args.session_id,
                args.turn_id,
                turn_token=args.turn_token,
            )
        elif args.command == "seal":
            output = seal(
                args.project_root,
                args.session_id,
                args.turn_id,
                turn_token=args.turn_token,
                project_instructions_state=args.project_instructions_state,
                project_instructions_private_root=(
                    args.project_instructions_private_root
                ),
            )
        else:
            output = waive(
                args.project_root,
                args.session_id,
                args.turn_id,
                args.reason,
                turn_token=args.turn_token,
            )
    except (ProjectSpecError, OSError) as error:
        code = (
            error.code if isinstance(error, ProjectSpecError) else "ENVIRONMENT_BLOCKER"
        )
        message = error.message if isinstance(error, ProjectSpecError) else str(error)
        print(
            json.dumps(
                {"status": "blocked", "code": code, "error": message},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
