#!/usr/bin/env python3
"""Inspect, apply, and verify conditional project-root AGENTS.md decisions."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import stat
import sys

from project_agent_instructions_lib import contracts
from project_agent_instructions_lib import discovery
from project_agent_instructions_lib import private_state
from project_agent_instructions_lib import workflow


def _maintenance_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "maintain-project-specs/assets/hooks/project_specs_maintenance.py"
    )
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o022
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise OSError("project spec maintenance helper is unsafe")
    specification = importlib.util.spec_from_file_location(
        "_project_instructions_maintenance", path
    )
    if specification is None or specification.loader is None:
        raise OSError("project spec maintenance helper could not be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@contextmanager
def _operation_lock(args: argparse.Namespace):
    maintenance = _maintenance_module()
    try:
        with maintenance.workspace_operation_lock(
            args.private_root,
            workspace_lock_fd=getattr(args, "workspace_lock_fd", None),
            session_lock_fd=getattr(args, "session_lock_fd", None),
        ):
            yield
    except maintenance.MaintenanceError as error:
        raise OSError(str(error)) from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely manage conditional project-root AGENTS.md decisions."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect", help="Inspect specs, instruction chain, and target ownership."
    )
    inspect_parser.add_argument("--project-root", type=Path, required=True)
    inspect_parser.add_argument(
        "--spec-owner",
        choices=("maintain-project-specs",),
        required=True,
    )
    inspect_parser.add_argument("--requirements", default="docs/requirements.md")
    inspect_parser.add_argument("--design", default="docs/design.md")
    inspect_parser.add_argument("--spec-receipt", type=Path, required=True)
    inspect_parser.add_argument("--runtime-config", type=Path, required=True)
    inspect_parser.add_argument("--codex-home", type=Path, required=True)
    inspect_parser.add_argument("--private-root", type=Path, required=True)
    inspect_parser.add_argument("--output", type=Path, required=True)
    apply_parser = subparsers.add_parser(
        "apply", help="Apply one fingerprint-bound private decision."
    )
    apply_parser.add_argument("--manifest", type=Path, required=True)
    apply_parser.add_argument("--decision", type=Path, required=True)
    apply_parser.add_argument("--ownership", type=Path, required=True)
    apply_parser.add_argument("--state", type=Path, required=True)
    apply_parser.add_argument("--private-root", type=Path, required=True)
    render_parser = subparsers.add_parser(
        "render",
        help="Render a receipt-bound decision without mutating the repository.",
    )
    render_parser.add_argument("--manifest", type=Path, required=True)
    render_parser.add_argument("--decision", type=Path, required=True)
    render_parser.add_argument("--private-root", type=Path, required=True)
    render_parser.add_argument("--output", type=Path, required=True)
    render_parser.add_argument("--state", type=Path, required=True)
    verify_parser = subparsers.add_parser(
        "verify", help="Verify one final private decision state."
    )
    verify_parser.add_argument("--state", type=Path, required=True)
    verify_parser.add_argument("--private-root", type=Path, required=True)
    for command in (render_parser, verify_parser):
        command.add_argument(
            "--workspace-lock-fd", type=int, help=argparse.SUPPRESS
        )
        command.add_argument("--session-lock-fd", type=int, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        with _operation_lock(args):
            if args.command == "inspect":
                _, git_root, _ = discovery._project_identity(args.project_root)
                private_root = private_state._ensure_private_root(
                    args.private_root, git_root
                )
                spec_receipt = args.spec_receipt.expanduser().resolve()
                runtime_config = args.runtime_config.expanduser().resolve()
                result = discovery._manifest(
                    args.project_root,
                    args.spec_owner,
                    args.requirements,
                    args.design,
                    args.codex_home,
                    spec_receipt,
                    runtime_config,
                )
                output_path = private_state._private_member(
                    private_root, args.output, "manifest"
                )
                private_state._write_private_json(
                    output_path,
                    result,
                    Path(str(result["git_root"])),
                    private_root,
                )
                ownership_continuity = workflow.carry_forward_ownership(
                    result, private_root
                )
                output: object = {
                    "status": "ok",
                    "manifest": str(output_path),
                    "manifest_sha256": result["manifest_sha256"],
                    "project_root": result["project_root"],
                    "target": result["target"],
                    "ownership_continuity": ownership_continuity,
                }
            elif args.command == "render":
                output = workflow.render_decision(
                    args.manifest,
                    args.decision,
                    args.output,
                    args.state,
                    args.private_root,
                )
            elif args.command == "apply":
                output = workflow.apply_decision(
                    args.manifest,
                    args.decision,
                    args.ownership,
                    args.state,
                    args.private_root,
                )
            else:
                output = workflow.verify_state(args.state, args.private_root)
    except (contracts.ProjectInstructionsError, OSError) as error:
        code = (
            error.code
            if isinstance(error, contracts.ProjectInstructionsError)
            else "ENVIRONMENT_BLOCKER"
        )
        message = (
            error.message
            if isinstance(error, contracts.ProjectInstructionsError)
            else str(error)
        )
        print(
            contracts._stable_json(
                {
                    "status": "blocked",
                    "code": code,
                    "error": message,
                }
            ).decode("utf-8"),
            end="",
        )
        return 2
    print(contracts._stable_json(output).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
