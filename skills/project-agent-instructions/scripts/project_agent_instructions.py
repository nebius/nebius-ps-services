#!/usr/bin/env python3
"""Inspect, apply, and verify conditional project-root AGENTS.md decisions."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from project_agent_instructions_lib import contracts
from project_agent_instructions_lib import discovery
from project_agent_instructions_lib import private_state
from project_agent_instructions_lib import workflow


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
        choices=("task-implementer", "agentic-sdlc"),
        required=True,
    )
    inspect_parser.add_argument("--requirements", default="docs/requirements.md")
    inspect_parser.add_argument("--design", default="docs/design.md")
    inspect_parser.add_argument("--codex-home", type=Path)
    inspect_parser.add_argument("--private-root", type=Path, required=True)
    inspect_parser.add_argument("--output", type=Path, required=True)
    apply_parser = subparsers.add_parser(
        "apply", help="Apply one fingerprint-bound private decision."
    )
    apply_parser.add_argument("--manifest", type=Path, required=True)
    apply_parser.add_argument("--decision", type=Path, required=True)
    apply_parser.add_argument("--state", type=Path, required=True)
    apply_parser.add_argument("--private-root", type=Path, required=True)
    verify_parser = subparsers.add_parser(
        "verify", help="Verify one final private decision state."
    )
    verify_parser.add_argument("--state", type=Path, required=True)
    verify_parser.add_argument("--private-root", type=Path, required=True)
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result = discovery._manifest(
                args.project_root,
                args.spec_owner,
                args.requirements,
                args.design,
                args.codex_home,
            )
            private_root = private_state._ensure_private_root(
                args.private_root, Path(str(result["git_root"]))
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
            output: object = {
                "status": "ok",
                "manifest": str(output_path),
                "manifest_sha256": result["manifest_sha256"],
                "project_root": result["project_root"],
                "target": result["target"],
            }
        elif args.command == "apply":
            output = workflow.apply_decision(
                args.manifest,
                args.decision,
                args.state,
                args.private_root,
            )
        else:
            output = workflow.verify_state(args.state, args.private_root)
    except contracts.ProjectInstructionsError as error:
        print(
            contracts._stable_json(
                {
                    "status": "blocked",
                    "code": error.code,
                    "error": error.message,
                }
            ).decode("utf-8"),
            end="",
        )
        return 2
    print(contracts._stable_json(output).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
