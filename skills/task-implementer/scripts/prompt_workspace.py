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
    REVISION_RE,
    RUN_SCHEMA,
    WORKSPACE_SCHEMA,
    PromptWorkspaceError,
    create_prompt,
    init_workspace,
    project_workspace_manifest,
    prompt_slug,
    resolve_prompt_reference,
    verify_workspace,
    verify_workspace_for_removal,
)
from prompt_workspace_lanes import integrate_lane, remove_lane  # noqa: E402
from prompt_workspace_intake import route_project_prompt  # noqa: E402
from prompt_workspace_waves import (  # noqa: E402
    accept_task_result,
    advance_batch,
    arm_task,
    cleanup_wave,
    dispatch_wave,
    finalize_run,
    heartbeat_task,
    integrate_wave,
    plan_waves,
    prepare_wave,
    promote_wave,
    rearm_task,
    recover_task,
    replan_waves,
    start_task,
    watch_task,
)
from prompt_workspace_runs import (  # noqa: E402
    activate_next_queued_prompt,
    cancel_queued_prompt,
    initialize_project_workspace,
    load_run_manifests,
    manifest_revisions,
    merge_session_refinement,
    prompt_rows,
    queue_rows,
    scope_lock,
    snapshot_prompt,
    verify_command,
    verify_run,
)
from prompt_workspace_specs import (  # noqa: E402
    inspect_spec_documents,
    resolve_steering_revision,
)


__all__ = [
    "MAX_PROMPT_BYTES",
    "PROMPT_SCHEMA",
    "RUN_SCHEMA",
    "WORKSPACE_SCHEMA",
    "PromptWorkspaceError",
    "accept_task_result",
    "activate_next_queued_prompt",
    "advance_batch",
    "arm_task",
    "cleanup_wave",
    "cancel_queued_prompt",
    "create_prompt",
    "init_workspace",
    "initialize_project_workspace",
    "inspect_spec_documents",
    "integrate_lane",
    "main",
    "project_workspace_manifest",
    "dispatch_wave",
    "finalize_run",
    "heartbeat_task",
    "integrate_wave",
    "merge_session_refinement",
    "plan_waves",
    "prepare_wave",
    "promote_wave",
    "rearm_task",
    "recover_task",
    "replan_waves",
    "remove_lane",
    "prompt_rows",
    "queue_rows",
    "prompt_slug",
    "resolve_prompt_reference",
    "resolve_steering_revision",
    "route_project_prompt",
    "snapshot_prompt",
    "verify_command",
    "verify_run",
    "verify_workspace",
    "verify_workspace_for_removal",
    "watch_task",
]


def open_in_editor(editor: str, target: Path, *, workspace: bool) -> None:
    arguments = [editor]
    if workspace:
        arguments.extend(("--reuse-window", str(target)))
    else:
        arguments.extend(("--reuse-window", "--goto", str(target)))
    try:
        result = subprocess.run(arguments, check=False, timeout=15)
    except FileNotFoundError:
        print(
            f"WARN editor executable is unavailable; open manually: {target}",
            file=sys.stderr,
        )
        return
    except subprocess.TimeoutExpired:
        print(f"WARN editor did not return promptly; target: {target}", file=sys.stderr)
        return
    if result.returncode != 0:
        print(
            f"WARN editor exited with status {result.returncode}; open manually: {target}",
            file=sys.stderr,
        )


def add_common_workspace(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--json", action="store_true")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Internal mechanical helper for task-implementer private state."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="Internal: create or verify one project workspace."
    )
    init_parser.add_argument("project_path", nargs="?", type=Path, default=Path.cwd())
    init_parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
    )
    init_parser.add_argument("--no-open", action="store_true")
    init_parser.add_argument(
        "--editor", default=os.environ.get("TASK_IMPLEMENTER_EDITOR", "code")
    )
    init_parser.add_argument("--json", action="store_true")

    integrate_parser = subparsers.add_parser(
        "integrate",
        help="Internal: integrate all pending project-lane generations.",
    )
    integrate_parser.add_argument(
        "project_path", nargs="?", type=Path, default=Path.cwd()
    )
    integrate_parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
    )
    integrate_parser.add_argument("--validated-head", help=argparse.SUPPRESS)
    integrate_parser.add_argument(
        "--restart", action="store_true", help=argparse.SUPPRESS
    )
    integrate_parser.add_argument("--json", action="store_true")

    remove_parser = subparsers.add_parser(
        "remove", help="Internal: retire one idle project lane."
    )
    remove_parser.add_argument("project_path", nargs="?", type=Path, default=Path.cwd())
    remove_parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
    )
    remove_parser.add_argument("--json", action="store_true")

    intake_parser = subparsers.add_parser(
        "intake", help="Internal: resolve one prompt to its next run transition."
    )
    intake_parser.add_argument("prompt")
    intake_parser.add_argument("--project-path", type=Path, default=Path.cwd())
    intake_parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
    )
    intake_parser.add_argument("--json", action="store_true")
    intake_parser.add_argument(
        "--internal-json",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    new_parser = subparsers.add_parser(
        "new", help="Internal: create one prompt file for the editor task."
    )
    add_common_workspace(new_parser)
    new_parser.add_argument("--ask", required=True)
    new_parser.add_argument("--open", action="store_true")
    new_parser.add_argument(
        "--editor", default=os.environ.get("TASK_IMPLEMENTER_EDITOR", "code")
    )
    session_merge = subparsers.add_parser(
        "session-merge", help="Internal: merge one accepted bound-session refinement."
    )
    add_common_workspace(session_merge)
    session_merge.add_argument("--refined-file", type=Path, required=True)
    session_merge.add_argument("--prompt")
    session_merge.add_argument("--expected-sha256")
    session_merge.add_argument("--new-objective", action="store_true")
    session_merge.add_argument("--operation-id", required=True)

    list_parser = subparsers.add_parser(
        "list", help="Internal: list prompt metadata without bodies."
    )
    add_common_workspace(list_parser)
    list_parser.add_argument("--query")
    list_parser.add_argument("--date")

    queue_list_parser = subparsers.add_parser(
        "queue-list", help="Internal: list accepted queued prompts without bodies."
    )
    add_common_workspace(queue_list_parser)

    queue_cancel_parser = subparsers.add_parser(
        "queue-cancel", help="Internal: cancel one accepted queued prompt."
    )
    add_common_workspace(queue_cancel_parser)
    queue_cancel_parser.add_argument("--prompt", required=True)

    queue_next_parser = subparsers.add_parser(
        "queue-next", help="Internal: activate the FIFO queue head when idle."
    )
    add_common_workspace(queue_next_parser)

    snapshot_parser = subparsers.add_parser(
        "snapshot", help="Internal: create an immutable prompt revision."
    )
    add_common_workspace(snapshot_parser)
    snapshot_parser.add_argument("--prompt", required=True, type=Path)
    snapshot_parser.add_argument("--run-id")
    snapshot_parser.add_argument("--new-run", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify", help="Internal: validate private workspace and run state."
    )
    add_common_workspace(verify_parser)
    verify_parser.add_argument("--prompt", type=Path)
    verify_parser.add_argument("--run-id")

    wave_plan = subparsers.add_parser(
        "wave-plan", help="Internal: lock dependency waves."
    )
    add_common_workspace(wave_plan)
    wave_plan.add_argument("--run-id", required=True)
    wave_plan.add_argument("--capacity", required=True, type=int)

    wave_replan = subparsers.add_parser(
        "wave-replan", help="Internal: replace one resource-free planned wave."
    )
    add_common_workspace(wave_replan)
    wave_replan.add_argument("--run-id", required=True)
    wave_replan.add_argument("--capacity", required=True, type=int)

    wave_prepare = subparsers.add_parser(
        "wave-prepare", help="Internal: create integration worktree."
    )
    add_common_workspace(wave_prepare)
    wave_prepare.add_argument("--run-id", required=True)

    wave_dispatch = subparsers.add_parser(
        "wave-dispatch", help="Internal: create immutable worker assignments."
    )
    add_common_workspace(wave_dispatch)
    wave_dispatch.add_argument("--run-id", required=True)
    wave_dispatch.add_argument("--contract-commit", required=True)

    batch_advance = subparsers.add_parser(
        "batch-advance", help="Internal: activate the next capacity batch."
    )
    add_common_workspace(batch_advance)
    batch_advance.add_argument("--run-id", required=True)

    task_start = subparsers.add_parser(
        "task-start", help="Internal: authorize one assigned worker."
    )
    add_common_workspace(task_start)
    task_start.add_argument("--run-id", required=True)
    task_start.add_argument("--task-id", required=True)
    task_start.add_argument("--assignment-sha256", required=True)
    task_start.add_argument("--start-lease", required=True)

    task_arm = subparsers.add_parser(
        "task-arm", help="Internal: arm one available worker slot."
    )
    add_common_workspace(task_arm)
    task_arm.add_argument("--run-id", required=True)
    task_arm.add_argument("--task-id", required=True)

    task_heartbeat = subparsers.add_parser(
        "task-heartbeat", help="Internal: record bounded worker progress."
    )
    add_common_workspace(task_heartbeat)
    task_heartbeat.add_argument("--run-id", required=True)
    task_heartbeat.add_argument("--task-id", required=True)
    task_heartbeat.add_argument("--assignment-sha256", required=True)
    task_heartbeat.add_argument(
        "--phase",
        required=True,
        choices=(
            "preflight",
            "implementing",
            "validating",
            "reviewing",
            "committing",
            "reporting",
        ),
    )

    task_watch = subparsers.add_parser(
        "task-watch", help="Internal: evaluate one worker liveness budget."
    )
    add_common_workspace(task_watch)
    task_watch.add_argument("--run-id", required=True)
    task_watch.add_argument("--task-id", required=True)

    task_rearm = subparsers.add_parser(
        "task-rearm", help="Internal: replace one expired clean prestart lease."
    )
    add_common_workspace(task_rearm)
    task_rearm.add_argument("--run-id", required=True)
    task_rearm.add_argument("--task-id", required=True)
    task_rearm.add_argument("--expected-start-lease", required=True)
    task_rearm.add_argument("--confirmed-stopped", action="store_true")

    task_recover = subparsers.add_parser(
        "task-recover", help="Internal: transfer one interrupted running task."
    )
    add_common_workspace(task_recover)
    task_recover.add_argument("--run-id", required=True)
    task_recover.add_argument("--task-id", required=True)
    task_recover.add_argument("--confirmed-stopped", action="store_true")

    task_finish = subparsers.add_parser(
        "task-finish", help="Internal: verify one worker result."
    )
    add_common_workspace(task_finish)
    task_finish.add_argument("--run-id", required=True)
    task_finish.add_argument("--task-id", required=True)

    wave_integrate = subparsers.add_parser(
        "wave-integrate", help="Internal: merge ready tasks in stable order."
    )
    add_common_workspace(wave_integrate)
    wave_integrate.add_argument("--run-id", required=True)

    wave_promote = subparsers.add_parser(
        "wave-promote", help="Internal: fast-forward the primary branch."
    )
    add_common_workspace(wave_promote)
    wave_promote.add_argument("--run-id", required=True)
    wave_promote.add_argument("--evidence", required=True, type=Path)

    wave_cleanup = subparsers.add_parser(
        "wave-cleanup", help="Internal: remove reachable managed resources."
    )
    add_common_workspace(wave_cleanup)
    wave_cleanup.add_argument("--run-id", required=True)

    run_finalize = subparsers.add_parser(
        "run-finalize",
        help="Internal: seal final alignment and release the lane generation.",
    )
    add_common_workspace(run_finalize)
    run_finalize.add_argument("--run-id", required=True)
    run_finalize.add_argument("--alignment", required=True)

    steering_parser = subparsers.add_parser(
        "steering-resolve", help="Internal: record one steering disposition."
    )
    add_common_workspace(steering_parser)
    steering_parser.add_argument("--run-id", required=True)
    steering_parser.add_argument("--revision", required=True)
    steering_parser.add_argument(
        "--disposition",
        required=True,
        choices=("applied", "blocked", "no_effect"),
    )

    specs_parser = subparsers.add_parser(
        "spec-inspect", help="Internal: validate managed specification documents."
    )
    add_common_workspace(specs_parser)
    specs_parser.add_argument("--commit")
    return parser.parse_args(argv)


def emit(value: object, json_output: bool) -> None:
    if json_output:
        print(json.dumps(value, sort_keys=True))
        return

    def emit_rows(rows: list[object]) -> None:
        for item in rows:
            if isinstance(item, dict):
                print(
                    "\t".join(
                        re.sub(
                            r"[\x00-\x1f\x7f]",
                            " ",
                            str(item.get(key) or "-"),
                        )
                        for key in (
                            "last_invoked_at",
                            "status",
                            "title",
                            "path",
                        )
                    )
                )

    if isinstance(value, list):
        emit_rows(value)
        return
    if isinstance(value, dict):
        prompts = value.get("prompts")
        if isinstance(prompts, list):
            for key in (
                "workspace",
                "vscode_workspace",
                "prompt_root",
                "starter_prompt",
                "starter_created",
                "action",
                "prompt",
                "prompt_ref",
                "last_invoked_at",
                "status",
                "outcome",
                "queue_position",
            ):
                if key in value:
                    print(f"{key}: {value[key]}")
            emit_rows(prompts)
            return
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
            result = initialize_project_workspace(
                args.project_path,
                args.codex_home,
            )
            if not args.no_open:
                open_in_editor(
                    args.editor, Path(str(result["vscode_workspace"])), workspace=True
                )
        elif args.command == "integrate":
            workspace_path = project_workspace_manifest(
                args.project_path, args.codex_home
            )
            workspace = verify_workspace(workspace_path)
            result = integrate_lane(
                workspace,
                validated_head=args.validated_head,
                restart=args.restart,
            )
        elif args.command == "remove":
            workspace_path = project_workspace_manifest(
                args.project_path, args.codex_home
            )
            workspace = verify_workspace_for_removal(workspace_path, args.project_path)
            result = remove_lane(workspace)
        elif args.command == "intake":
            result = route_project_prompt(
                args.project_path,
                args.codex_home,
                args.prompt,
            )
        elif args.command == "new":
            result = create_prompt(args.workspace, args.ask)
            if args.open:
                open_in_editor(args.editor, Path(str(result["path"])), workspace=False)
        elif args.command == "session-merge":
            result = merge_session_refinement(
                args.workspace,
                args.refined_file,
                prompt_reference=args.prompt,
                expected_sha256=args.expected_sha256,
                new_objective=args.new_objective,
                operation_id=args.operation_id,
            )
        elif args.command == "list":
            result = prompt_rows(args.workspace, args.query, args.date)
        elif args.command == "queue-list":
            result = queue_rows(args.workspace)
        elif args.command == "queue-cancel":
            result = cancel_queued_prompt(args.workspace, args.prompt)
        elif args.command == "queue-next":
            result = activate_next_queued_prompt(args.workspace)
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
        elif args.command == "wave-plan":
            result = plan_waves(args.workspace, args.run_id, args.capacity)
        elif args.command == "wave-replan":
            result = replan_waves(args.workspace, args.run_id, args.capacity)
        elif args.command == "wave-prepare":
            result = prepare_wave(args.workspace, args.run_id)
        elif args.command == "wave-dispatch":
            result = dispatch_wave(args.workspace, args.run_id, args.contract_commit)
        elif args.command == "batch-advance":
            result = advance_batch(args.workspace, args.run_id)
        elif args.command == "task-arm":
            result = arm_task(args.workspace, args.run_id, args.task_id)
        elif args.command == "task-start":
            result = start_task(
                args.workspace,
                args.run_id,
                args.task_id,
                args.assignment_sha256,
                args.start_lease,
            )
        elif args.command == "task-heartbeat":
            result = heartbeat_task(
                args.workspace,
                args.run_id,
                args.task_id,
                args.assignment_sha256,
                args.phase,
            )
        elif args.command == "task-watch":
            result = watch_task(args.workspace, args.run_id, args.task_id)
        elif args.command == "task-rearm":
            result = rearm_task(
                args.workspace,
                args.run_id,
                args.task_id,
                args.expected_start_lease,
                confirmed_stopped=args.confirmed_stopped,
            )
        elif args.command == "task-recover":
            result = recover_task(
                args.workspace,
                args.run_id,
                args.task_id,
                confirmed_stopped=args.confirmed_stopped,
            )
        elif args.command == "task-finish":
            result = accept_task_result(args.workspace, args.run_id, args.task_id)
        elif args.command == "wave-integrate":
            result = integrate_wave(args.workspace, args.run_id)
        elif args.command == "wave-promote":
            result = promote_wave(args.workspace, args.run_id, args.evidence)
        elif args.command == "wave-cleanup":
            result = cleanup_wave(args.workspace, args.run_id)
        elif args.command == "run-finalize":
            result = finalize_run(args.workspace, args.run_id, args.alignment)
        elif args.command == "steering-resolve":
            workspace = verify_workspace(args.workspace)
            runs_root = Path(str(workspace["runs_root"]))
            with scope_lock(runs_root.parent):
                verified = verify_run(workspace, args.run_id, None)
                revision_match = REVISION_RE.fullmatch(args.revision)
                if revision_match is None:
                    raise PromptWorkspaceError(
                        "RUN_STATE_INVALID", "steering revision is invalid"
                    )
                run_dir, manifest = next(
                    item
                    for item in load_run_manifests(runs_root)
                    if item[0].name == args.run_id
                )
                if args.disposition in {"applied", "no_effect"} and int(
                    str(verified["revision"])[1:]
                ) < int(revision_match.group(1)):
                    raise PromptWorkspaceError(
                        "RUN_STATE_INVALID",
                        "steering cannot resolve before the handoff binds its revision",
                    )
                result = resolve_steering_revision(
                    run_dir,
                    manifest_revisions(manifest),
                    args.revision,
                    args.disposition,
                )
        elif args.command == "spec-inspect":
            workspace = verify_workspace(args.workspace)
            result = inspect_spec_documents(workspace, commit=args.commit)
        else:  # pragma: no cover - argparse enforces the command set.
            raise AssertionError(args.command)
    except PromptWorkspaceError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2
    if args.command == "init" and args.json:
        result = {
            key: value
            for key, value in result.items()
            if key not in {"project_id", "scope_id"}
        }
    if args.command == "intake" and args.json:
        result = {key: value for key, value in result.items() if key != "_internal"}
    emit(result, args.json or getattr(args, "internal_json", False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
