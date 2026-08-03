#!/usr/bin/env python3
"""Private CLI for Agentic SDLC execution-plane transitions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from sdlc_execution_core import (
    ExecutionError,
    advance_batch,
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
    complete_source_integration,
    ExecutionInteropError,
    release as release_outer_lease,
)


PRIVATE_OUTPUT_KEYS = {
    "combined_evidence",
    "done_criteria",
    "decisions",
    "final_evidence",
    "goal",
    "open_risks",
    "promotion_evidence",
    "regression_oracle_evidence",
    "review",
    "validation",
}


def runtime_session_identity() -> str:
    value = os.environ.get("CODEX_THREAD_ID")
    if not isinstance(value, str) or not value.strip():
        raise ExecutionError(
            "SESSION_ID_UNAVAILABLE", "CODEX_THREAD_ID is required for worker commands"
        )
    return value


def string_list_json(value: str, label: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", f"{label} must be a JSON string array"
        ) from exc
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) or not item.strip() for item in parsed
    ):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", f"{label} must be a JSON string array"
        )
    return parsed


def optional_object_json(value: str, label: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", f"{label} must be a JSON object or null"
        ) from exc
    if parsed is not None and not isinstance(parsed, dict):
        raise ExecutionError(
            "EXECUTION_STATE_INVALID", f"{label} must be a JSON object or null"
        )
    return parsed


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

    batch = sub.add_parser("batch-advance")
    batch.add_argument("--run-dir", type=Path, required=True)
    batch.add_argument("--feature", required=True)
    batch.add_argument("--wave", required=True)

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
    task_start.add_argument("--scope-cwd", type=Path, required=True)

    task_recover = sub.add_parser("task-recover")
    task_recover.add_argument("--run-dir", type=Path, required=True)
    task_recover.add_argument("--feature", required=True)
    task_recover.add_argument("--wave", required=True)
    task_recover.add_argument("--task", required=True)
    task_recover.add_argument("--scope-cwd", type=Path, required=True)
    task_recover.add_argument("--expected-attempt", type=int, required=True)
    task_recover.add_argument("--confirmed-stopped", action="store_true")

    task_finish = sub.add_parser("task-finish")
    task_finish.add_argument("--run-dir", type=Path, required=True)
    task_finish.add_argument("--feature", required=True)
    task_finish.add_argument("--wave", required=True)
    task_finish.add_argument("--task", required=True)
    task_finish.add_argument("--validation", required=True)
    task_finish.add_argument("--review", required=True)
    task_finish.add_argument("--message", required=True)
    task_finish.add_argument("--summary", required=True)
    task_finish.add_argument("--decisions-json", required=True)
    task_finish.add_argument("--open-risks-json", required=True)
    task_finish.add_argument("--oracle-evidence-json", default="null")

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

    complete_outer = sub.add_parser("complete-outer-integration")
    complete_outer.add_argument("--run-dir", type=Path, required=True)
    complete_outer.add_argument("--project-root", type=Path, required=True)

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
    if args.command == "batch-advance":
        return advance_batch(args.run_dir, args.feature, args.wave)
    if args.command == "replan-future":
        return replan_future(args.run_dir, args.feature, args.plan, args.capacity)
    if args.command == "task-start":
        return start_task(
            args.run_dir,
            args.feature,
            args.wave,
            args.task,
            args.assignment_digest,
            runtime_session_identity(),
            args.scope_cwd,
        )
    if args.command == "task-recover":
        return recover_task(
            args.run_dir,
            args.feature,
            args.wave,
            args.task,
            runtime_session_identity(),
            args.scope_cwd,
            expected_attempt=args.expected_attempt,
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
            summary=args.summary,
            decisions=string_list_json(args.decisions_json, "decisions-json"),
            open_risks=string_list_json(args.open_risks_json, "open-risks-json"),
            regression_oracle_evidence=optional_object_json(
                args.oracle_evidence_json, "oracle-evidence-json"
            ),
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
    if args.command == "complete-outer-integration":
        return complete_source_integration(args.run_dir, args.project_root)
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
