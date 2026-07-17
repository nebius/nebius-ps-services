#!/usr/bin/env python3
"""Private CLI for Agentic SDLC execution-plane transitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sdlc_execution_core import (
    ExecutionError,
    complete_wave,
    describe_status,
    finish_task,
    integrate_wave,
    prepare_execution,
    prepare_wave,
    promote_feature,
    replan_future,
    recover_task,
    seal_feature,
    seal_tdd_base,
    start_task,
)
from sdlc_execution_interop import (
    ExecutionInteropError,
    release as release_outer_lease,
)


PRIVATE_OUTPUT_KEYS = {
    "combined_evidence",
    "done_criteria",
    "final_evidence",
    "promotion_evidence",
    "review",
    "validation",
}


def public_result(value: Any) -> Any:
    """Remove task/evidence bodies from command output while preserving identities."""

    if isinstance(value, dict):
        return {
            key: public_result(item)
            for key, item in value.items()
            if key not in PRIVATE_OUTPUT_KEYS
        }
    if isinstance(value, list):
        return [public_result(item) for item in value]
    return value


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    sub = value.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--run-dir", type=Path, required=True)
    prepare.add_argument("--project-root", type=Path, required=True)
    prepare.add_argument("--feature", required=True)
    prepare.add_argument("--plan", type=Path, required=True)
    prepare.add_argument("--capacity", type=int, default=1)

    tdd = sub.add_parser("seal-tdd")
    tdd.add_argument("--run-dir", type=Path, required=True)
    tdd.add_argument("--feature", required=True)
    tdd.add_argument("--message", required=True)

    wave = sub.add_parser("wave-prepare")
    wave.add_argument("--run-dir", type=Path, required=True)
    wave.add_argument("--feature", required=True)
    wave.add_argument("--wave", required=True)

    replan = sub.add_parser("replan-future")
    replan.add_argument("--run-dir", type=Path, required=True)
    replan.add_argument("--feature", required=True)
    replan.add_argument("--plan", type=Path, required=True)
    replan.add_argument("--capacity", type=int, default=1)

    task_start = sub.add_parser("task-start")
    task_start.add_argument("--run-dir", type=Path, required=True)
    task_start.add_argument("--feature", required=True)
    task_start.add_argument("--wave", required=True)
    task_start.add_argument("--task", required=True)
    task_start.add_argument("--assignment-digest", required=True)
    task_start.add_argument("--session-id", required=True)
    task_start.add_argument("--scope-cwd", type=Path, required=True)

    task_recover = sub.add_parser("task-recover")
    task_recover.add_argument("--run-dir", type=Path, required=True)
    task_recover.add_argument("--feature", required=True)
    task_recover.add_argument("--wave", required=True)
    task_recover.add_argument("--task", required=True)
    task_recover.add_argument("--session-id", required=True)
    task_recover.add_argument("--scope-cwd", type=Path, required=True)
    task_recover.add_argument("--confirmed-stopped", action="store_true")

    task_finish = sub.add_parser("task-finish")
    task_finish.add_argument("--run-dir", type=Path, required=True)
    task_finish.add_argument("--feature", required=True)
    task_finish.add_argument("--wave", required=True)
    task_finish.add_argument("--task", required=True)
    task_finish.add_argument("--validation", required=True)
    task_finish.add_argument("--review", required=True)
    task_finish.add_argument("--message", required=True)

    integrate = sub.add_parser("wave-integrate")
    integrate.add_argument("--run-dir", type=Path, required=True)
    integrate.add_argument("--feature", required=True)
    integrate.add_argument("--wave", required=True)

    complete = sub.add_parser("wave-complete")
    complete.add_argument("--run-dir", type=Path, required=True)
    complete.add_argument("--feature", required=True)
    complete.add_argument("--wave", required=True)
    complete.add_argument("--evidence", required=True)

    seal = sub.add_parser("seal-feature")
    seal.add_argument("--run-dir", type=Path, required=True)
    seal.add_argument("--feature", required=True)
    seal.add_argument("--evidence", required=True)
    seal.add_argument("--message", required=True)

    promote = sub.add_parser("promote")
    promote.add_argument("--run-dir", type=Path, required=True)
    promote.add_argument("--feature", required=True)
    promote.add_argument("--evidence", required=True)

    release = sub.add_parser("release-outer-lease")
    release.add_argument("--run-dir", type=Path, required=True)
    release.add_argument("--project-root", type=Path, required=True)
    release.add_argument("--promoted-head", required=True)
    release.add_argument("--final-alignment", required=True)
    release.add_argument("--uat", required=True)
    release.add_argument("--docs", required=True)

    status = sub.add_parser("status")
    status.add_argument("--run-dir", type=Path, required=True)
    status.add_argument("--feature", required=True)
    return value


def execute(args: argparse.Namespace) -> Any:
    if args.command == "prepare":
        return prepare_execution(
            args.run_dir, args.project_root, args.feature, args.plan, args.capacity
        )
    if args.command == "seal-tdd":
        return seal_tdd_base(args.run_dir, args.feature, args.message)
    if args.command == "wave-prepare":
        return prepare_wave(args.run_dir, args.feature, args.wave)
    if args.command == "replan-future":
        return replan_future(args.run_dir, args.feature, args.plan, args.capacity)
    if args.command == "task-start":
        return start_task(
            args.run_dir,
            args.feature,
            args.wave,
            args.task,
            args.assignment_digest,
            args.session_id,
            args.scope_cwd,
        )
    if args.command == "task-recover":
        return recover_task(
            args.run_dir,
            args.feature,
            args.wave,
            args.task,
            args.session_id,
            args.scope_cwd,
            confirmed_stopped=args.confirmed_stopped,
        )
    if args.command == "task-finish":
        return finish_task(
            args.run_dir,
            args.feature,
            args.wave,
            args.task,
            args.validation,
            args.review,
            args.message,
        )
    if args.command == "wave-integrate":
        return integrate_wave(args.run_dir, args.feature, args.wave)
    if args.command == "wave-complete":
        return complete_wave(args.run_dir, args.feature, args.wave, args.evidence)
    if args.command == "seal-feature":
        return seal_feature(args.run_dir, args.feature, args.evidence, args.message)
    if args.command == "promote":
        return promote_feature(args.run_dir, args.feature, args.evidence)
    if args.command == "release-outer-lease":
        return release_outer_lease(
            args.run_dir,
            args.project_root,
            args.promoted_head,
            final_alignment=args.final_alignment,
            uat=args.uat,
            docs=args.docs,
        )
    if args.command == "status":
        return describe_status(args.run_dir, args.feature)
    raise AssertionError(args.command)


def main() -> int:
    try:
        result = execute(parser().parse_args())
    except (ExecutionError, ExecutionInteropError) as exc:
        code = exc.code if isinstance(exc, ExecutionError) else "WORKTREE_CONFLICT"
        print(
            json.dumps(
                {"status": "error", "code": code, "message": str(exc)},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps({"status": "ok", "result": public_result(result)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
