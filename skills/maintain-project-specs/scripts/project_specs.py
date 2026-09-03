#!/usr/bin/env python3
"""Inspect, validate, publish, and explicitly migrate canonical project specs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from project_specs_lib.contracts import (
    ProjectSpecError,
    _read_file,
    inspect_project,
    validate_project,
)
from project_specs_lib.lifecycle import write_validation_receipt
from project_specs_lib.migration import migrate_project, recover_migration
from project_specs_lib.transaction import publish_spec_pair


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
    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--project-root", type=Path, required=True)
    publish_parser.add_argument("--requirements-candidate", type=Path, required=True)
    publish_parser.add_argument("--design-candidate", type=Path, required=True)
    publish_parser.add_argument("--expected-head", required=True)
    publish_parser.add_argument("--expected-requirements-sha256", required=True)
    publish_parser.add_argument("--expected-design-sha256", required=True)
    publish_parser.add_argument("--operation-id", required=True)
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
                )
                if args.output is not None
                else validate_project(args.project_root)
            )
        elif args.command == "migrate":
            output = migrate_project(args.project_root)
        elif args.command == "publish":
            requirements, _requirements_text = _read_file(
                args.requirements_candidate, "requirements candidate"
            )
            design, _design_text = _read_file(
                args.design_candidate, "design candidate"
            )
            output = publish_spec_pair(
                args.project_root,
                requirements_candidate=requirements,
                design_candidate=design,
                expected_git_head=args.expected_head,
                expected_requirements_sha256=args.expected_requirements_sha256,
                expected_design_sha256=args.expected_design_sha256,
                operation_id=args.operation_id,
            )
        else:
            output = recover_migration(args.project_root)
    except (ProjectSpecError, OSError) as error:
        code = (
            error.code if isinstance(error, ProjectSpecError) else "ENVIRONMENT_BLOCKER"
        )
        message = error.message if isinstance(error, ProjectSpecError) else str(error)
        status = "advisory" if args.command in {"inspect", "validate"} else "blocked"
        print(json.dumps({"status": status, "code": code, "error": message}, sort_keys=True))
        return 0 if status == "advisory" else 2
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
