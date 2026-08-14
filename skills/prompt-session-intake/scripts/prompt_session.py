#!/usr/bin/env python3
"""Coordinator-only CLI for accepted prompt-session transitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import os
import sys
from typing import Any


HOOK_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "hooks"
if str(HOOK_ASSETS) not in sys.path:
    sys.path.insert(0, str(HOOK_ASSETS))

from prompt_session_state import (  # noqa: E402
    DISPOSITIONS,
    MATERIAL_CLASSIFICATIONS,
    NOOP_REASONS,
    WORKFLOWS,
    PromptSessionError,
    accept_event,
    codex_home,
    consume_event,
    register_objective,
    state_root,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Internal coordinator for one capture-only prompt-session turn."
    )
    subparsers = value.add_subparsers(dest="action", required=True)
    accept = subparsers.add_parser("accept")
    accept.add_argument("--event", type=Path, required=True)
    accept.add_argument("--token", required=True)
    accept.add_argument(
        "--disposition", choices=sorted(DISPOSITIONS), required=True
    )
    accept.add_argument("--classification", choices=sorted(MATERIAL_CLASSIFICATIONS))
    accept.add_argument("--reason", choices=sorted(NOOP_REASONS | {"sensitive"}))
    accept.add_argument("--projection-file", type=Path)
    accept.add_argument("--prompt-path", type=Path)
    accept.add_argument("--base-sha256")
    accept.add_argument("--new-objective", action="store_true")
    consume = subparsers.add_parser("consume")
    consume.add_argument("--event", type=Path, required=True)
    consume.add_argument("--token", required=True)
    consume.add_argument("--workflow", choices=sorted(WORKFLOWS), required=True)
    consume.add_argument("--prompt-id")
    consume.add_argument("--prompt-ref")
    consume.add_argument("--prompt-path", type=Path)
    consume.add_argument("--prompt-sha256")
    consume.add_argument("--duplicate", action="store_true")
    objective = subparsers.add_parser("objective")
    objective.add_argument("--workflow", choices=sorted(WORKFLOWS), required=True)
    objective.add_argument("--project", type=Path, required=True)
    objective.add_argument("--prompt-id", required=True)
    objective.add_argument("--prompt-ref", required=True)
    objective.add_argument("--prompt-path", type=Path, required=True)
    objective.add_argument("--prompt-sha256", required=True)
    objective.add_argument("--terminal", action="store_true")
    status = subparsers.add_parser("status")
    status.add_argument("--codex-home", type=Path)
    return value


def execute(args: argparse.Namespace) -> dict[str, Any]:
    home = codex_home()
    if args.action == "accept":
        session_id = os.environ.get("CODEX_THREAD_ID")
        if not session_id:
            raise PromptSessionError(
                "IDENTITY_REQUIRED", "current Codex session identity is unavailable"
            )
        return accept_event(
            home,
            args.event,
            args.token,
            args.disposition,
            session_id=session_id,
            classification=args.classification,
            reason=args.reason,
            projection_file=args.projection_file,
            prompt_path=args.prompt_path,
            base_sha256=args.base_sha256,
            new_objective=args.new_objective,
        )
    if args.action == "consume":
        session_id = os.environ.get("CODEX_THREAD_ID")
        if not session_id:
            raise PromptSessionError(
                "IDENTITY_REQUIRED", "current Codex session identity is unavailable"
            )
        return consume_event(
            home,
            args.event,
            args.token,
            session_id=session_id,
            workflow=args.workflow,
            prompt_id=args.prompt_id,
            prompt_ref=args.prompt_ref,
            prompt_path=args.prompt_path,
            prompt_sha256=args.prompt_sha256,
            duplicate=args.duplicate,
        )
    if args.action == "objective":
        session_id = os.environ.get("CODEX_THREAD_ID")
        if not session_id:
            raise PromptSessionError(
                "IDENTITY_REQUIRED", "current Codex session identity is unavailable"
            )
        return register_objective(
            home,
            session_id,
            args.workflow,
            args.project,
            prompt_id=args.prompt_id,
            prompt_ref=args.prompt_ref,
            prompt_path=args.prompt_path,
            prompt_sha256=args.prompt_sha256,
            terminal=args.terminal,
        )
    selected = (args.codex_home or home).expanduser().resolve()
    root = state_root(selected)
    return {
        "status": "initialized" if root.is_dir() else "absent",
        "root": str(root),
    }


def main(argv: list[str] | None = None) -> int:
    try:
        result = execute(parser().parse_args(argv))
    except PromptSessionError as error:
        print(
            json.dumps(
                {"status": "blocked", "code": error.code, "error": error.message},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
