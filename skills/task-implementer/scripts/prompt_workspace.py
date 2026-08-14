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
    required_string,
    repo_and_scope,
    resolve_prompt_reference,
    verify_workspace,
    verify_workspace_for_removal,
)
from prompt_workspace_contract_delta import adopt_contract_delta  # noqa: E402
from prompt_workspace_lanes import integrate_lane, remove_lane  # noqa: E402
from prompt_workspace_intake import route_project_prompt  # noqa: E402
from prompt_workspace_interop import verify_workspace_anchor  # noqa: E402
from prompt_workspace_recovery import (  # noqa: E402
    recover_handoff_projection,
    recover_replanned_plan_digest,
)
from prompt_workspace_resume import (  # noqa: E402
    CONTROLLED_TRANSITIONS,
    abort_resume_transition_if_unchanged,
    begin_resume_transition,
    complete_resume_transition,
    resume_execution_lock,
    resume_run,
)
from prompt_workspace_waves import (  # noqa: E402
    accept_task_result,
    advance_batch,
    authorize_lifecycle_impact,
    authorize_project_agent_lifecycle,
    authorize_task_commit_lifecycle,
    arm_task,
    cleanup_wave,
    commit_coordinator_delta,
    dispatch_wave,
    finalize_run,
    heartbeat_task,
    integrate_wave,
    plan_waves,
    publish_task_result,
    prepare_run_checkpoint,
    prepare_wave,
    promote_wave,
    rearm_task,
    recover_task,
    recover_wave_resources,
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
    merge_session_projection,
    prompt_rows,
    queue_rows,
    scope_lock,
    snapshot_prompt,
    verify_command,
    verify_run,
)
from prompt_workspace_specs import (  # noqa: E402
    inspect_spec_documents,
    load_current_prompt_impact,
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
    "authorize_lifecycle_impact",
    "authorize_project_agent_lifecycle",
    "authorize_task_commit_lifecycle",
    "cleanup_wave",
    "commit_coordinator_delta",
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
    "merge_session_projection",
    "plan_waves",
    "prepare_run_checkpoint",
    "prepare_wave",
    "publish_task_result",
    "promote_wave",
    "rearm_task",
    "recover_task",
    "recover_handoff_projection",
    "recover_replanned_plan_digest",
    "recover_wave_resources",
    "replan_waves",
    "remove_lane",
    "reuse_project_workspace",
    "prompt_rows",
    "queue_rows",
    "prompt_slug",
    "resolve_prompt_reference",
    "resolve_steering_revision",
    "route_project_prompt",
    "resume_run",
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
    except OSError:
        print(
            f"WARN editor could not be launched; open manually: {target}",
            file=sys.stderr,
        )
        return
    if result.returncode != 0:
        print(
            f"WARN editor exited with status {result.returncode}; open manually: {target}",
            file=sys.stderr,
        )


def reuse_project_workspace(
    project_path: Path,
    codex_home: Path,
) -> dict[str, object]:
    """Validate and return one existing workspace without changing its lane."""

    requested = project_path.expanduser().resolve()
    workspace_path = project_workspace_manifest(requested, codex_home)
    if not workspace_path.exists() and not workspace_path.is_symlink():
        raise PromptWorkspaceError(
            "WORKSPACE_NOT_FOUND",
            "Task Implementer workspace does not exist; run workspace init for "
            "this project folder",
        )
    workspace = verify_workspace(workspace_path)
    requested_root, _, requested_scope = repo_and_scope(requested, str(requested))
    allowed_roots = {
        Path(required_string(workspace, "primary_root", "workspace manifest")),
        Path(required_string(workspace, "repo_root", "workspace manifest")),
    }
    if requested_root not in allowed_roots or requested_scope != required_string(
        workspace, "scope", "workspace manifest"
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH",
            "workspace reuse requires the primary project or its owning lane",
        )
    anchor = verify_workspace_anchor(workspace)
    return {
        "status": "reused",
        "workspace": str(workspace_path),
        "vscode_workspace": required_string(
            workspace, "vscode_workspace", "workspace manifest"
        ),
        "prompt_root": required_string(workspace, "prompt_root", "workspace manifest"),
        "project_id": required_string(workspace, "project_id", "workspace manifest"),
        "scope_id": required_string(workspace, "scope_id", "workspace manifest"),
        "lane_id": required_string(workspace, "lane_id", "workspace manifest"),
        "lane_state": anchor["lane_state"],
        "lane_status": anchor["status"],
        "lane_branch": anchor["branch"],
        "lane_worktree": anchor["worktree"],
        "scope_cwd": required_string(workspace, "source_root", "workspace manifest"),
    }


def add_common_workspace(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--json", action="store_true")


def parse_lifecycle_authorization(argv: list[str]) -> argparse.Namespace:
    """Parse the hook-only adapter without adding it to discoverable CLI help."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.set_defaults(command="lifecycle-authorize")
    add_common_workspace(parser)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--kind",
        required=True,
        choices=("project-instructions", "impact", "commit"),
    )
    return parser.parse_args(argv)


def parse_plan_digest_recovery(argv: list[str]) -> argparse.Namespace:
    """Parse the owner-only repair without adding it to discoverable CLI help."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.set_defaults(command="plan-digest-recover")
    add_common_workspace(parser)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--expected-index-sha256", required=True)
    return parser.parse_args(argv)


def parse_contract_delta_adoption(argv: list[str]) -> argparse.Namespace:
    """Parse the owner-only adoption without adding it to discoverable help."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.set_defaults(command="contract-delta-adopt")
    add_common_workspace(parser)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--lifecycle-state", required=True, type=Path)
    return parser.parse_args(argv)


def parse_handoff_projection_recovery(argv: list[str]) -> argparse.Namespace:
    """Parse the owner-only repair without adding it to discoverable help."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.set_defaults(command="handoff-projection-recover")
    add_common_workspace(parser)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-handoff-sha256", required=True)
    return parser.parse_args(argv)


def parse_args(argv: list[str]) -> argparse.Namespace:
    if argv[:1] == ["lifecycle-authorize"]:
        return parse_lifecycle_authorization(argv[1:])
    if argv[:1] == ["plan-digest-recover"]:
        return parse_plan_digest_recovery(argv[1:])
    if argv[:1] == ["contract-delta-adopt"]:
        return parse_contract_delta_adoption(argv[1:])
    if argv[:1] == ["handoff-projection-recover"]:
        return parse_handoff_projection_recovery(argv[1:])

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

    reuse_parser = subparsers.add_parser(
        "reuse", help="Internal: validate and reopen one existing project workspace."
    )
    reuse_parser.add_argument("project_path", nargs="?", type=Path, default=Path.cwd())
    reuse_parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
    )
    reuse_parser.add_argument("--no-open", action="store_true")
    reuse_parser.add_argument(
        "--editor", default=os.environ.get("TASK_IMPLEMENTER_EDITOR", "code")
    )
    reuse_parser.add_argument("--json", action="store_true")

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
        "session-merge", help="Internal: merge one accepted project-intent projection."
    )
    add_common_workspace(session_merge)
    session_merge.add_argument("--projection-file", type=Path, required=True)
    session_merge.add_argument("--projection-sha256", required=True)
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

    checkpoint_prepare = subparsers.add_parser(
        "checkpoint-prepare",
        help="Internal: reserve the first-generation lane candidate for review.",
    )
    add_common_workspace(checkpoint_prepare)
    checkpoint_prepare.add_argument("--run-id", required=True)

    wave_replan = subparsers.add_parser(
        "wave-replan",
        help=(
            "Internal: replace a resource-free plan or append a sealed "
            "promotion-review correction round."
        ),
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

    task_result_publish = subparsers.add_parser(
        "task-result-publish",
        help="Internal: validate and publish one private worker result.",
    )
    task_result_publish.add_argument("--assignment", required=True, type=Path)
    task_result_publish.add_argument("--draft", required=True, type=Path)
    task_result_publish.add_argument("--result", required=True, type=Path)

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

    wave_resource_recover = subparsers.add_parser(
        "wave-resource-recover",
        help="Internal: rehydrate exact missing active-wave worktrees.",
    )
    add_common_workspace(wave_resource_recover)
    wave_resource_recover.add_argument("--run-id", required=True)
    wave_resource_recover.add_argument("--confirmed-stopped", action="store_true")

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

    coordinator_commit = subparsers.add_parser(
        "coordinator-commit",
        help="Internal: commit the exact post-integration documentation delta.",
    )
    add_common_workspace(coordinator_commit)
    coordinator_commit.add_argument("--run-id", required=True)

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

    run_resume = subparsers.add_parser(
        "run-resume", help="Internal: plan one authoritative resume transition."
    )
    add_common_workspace(run_resume)
    run_resume.add_argument("--run-id", required=True)
    run_resume.add_argument("--capacity", type=int)
    run_resume.add_argument("--alignment")

    for transition_parser in (
        checkpoint_prepare,
        wave_plan,
        wave_replan,
        wave_prepare,
        wave_dispatch,
        batch_advance,
        task_arm,
        task_rearm,
        task_recover,
        wave_resource_recover,
        task_finish,
        wave_integrate,
        wave_promote,
        wave_cleanup,
        run_finalize,
    ):
        transition_parser.add_argument("--resume-token", help=argparse.SUPPRESS)

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


def _resume_transition_arguments(args: argparse.Namespace) -> dict[str, object]:
    if args.command in {"wave-plan", "wave-replan"}:
        return {"capacity": args.capacity}
    if args.command == "wave-dispatch":
        return {"contract_commit": args.contract_commit}
    if args.command in {"task-arm", "task-finish"}:
        return {"task_id": args.task_id}
    if args.command == "task-rearm":
        return {
            "task_id": args.task_id,
            "expected_start_lease": args.expected_start_lease,
            "confirmed_stopped": args.confirmed_stopped,
        }
    if args.command == "task-recover":
        return {
            "task_id": args.task_id,
            "confirmed_stopped": args.confirmed_stopped,
        }
    if args.command == "wave-resource-recover":
        return {"confirmed_stopped": args.confirmed_stopped}
    if args.command == "wave-promote":
        return {"evidence": str(args.evidence)}
    if args.command == "run-finalize":
        return {"alignment": args.alignment.strip()}
    return {}


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    resume_context: tuple[dict[str, object], str, str, str | None] | None = None
    resume_guard = None
    resume_effect_complete = False
    try:
        if args.command in CONTROLLED_TRANSITIONS:
            resume_workspace = verify_workspace(args.workspace)
            resume_guard = resume_execution_lock(resume_workspace, args.run_id)
            resume_guard.__enter__()
            begun = begin_resume_transition(
                resume_workspace,
                args.run_id,
                args.command,
                args.resume_token,
                arguments=_resume_transition_arguments(args),
            )
            if begun is not None:
                resume_effect_complete = bool(begun.get("_effect_complete"))
                resume_context = (
                    resume_workspace,
                    args.run_id,
                    args.command,
                    str(begun["resume_token"]),
                )
        if resume_effect_complete:
            result = {
                "status": "reconciled",
                "run_id": args.run_id,
                "transition": args.command,
            }
        elif args.command == "init":
            result = initialize_project_workspace(
                args.project_path,
                args.codex_home,
            )
            if not args.no_open:
                open_in_editor(
                    args.editor, Path(str(result["vscode_workspace"])), workspace=True
                )
        elif args.command == "reuse":
            result = reuse_project_workspace(args.project_path, args.codex_home)
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
            result = merge_session_projection(
                args.workspace,
                args.projection_file,
                prompt_reference=args.prompt,
                expected_sha256=args.expected_sha256,
                new_objective=args.new_objective,
                operation_id=args.operation_id,
                projection_sha256=args.projection_sha256,
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
        elif args.command == "checkpoint-prepare":
            result = prepare_run_checkpoint(args.workspace, args.run_id)
        elif args.command == "wave-plan":
            result = plan_waves(args.workspace, args.run_id, args.capacity)
        elif args.command == "lifecycle-authorize":
            command = sys.stdin.read(MAX_PROMPT_BYTES + 1)
            if len(command.encode("utf-8")) > MAX_PROMPT_BYTES:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "lifecycle command is too large"
                )
            command = command.rstrip("\n")
            if args.kind == "project-instructions":
                result = authorize_project_agent_lifecycle(
                    args.workspace, args.run_id, command
                )
            elif args.kind == "impact":
                result = authorize_lifecycle_impact(
                    args.workspace, args.run_id, command
                )
            else:
                result = authorize_task_commit_lifecycle(
                    args.workspace, args.run_id, command
                )
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
        elif args.command == "task-result-publish":
            result = publish_task_result(args.assignment, args.draft, args.result)
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
        elif args.command == "wave-resource-recover":
            result = recover_wave_resources(
                args.workspace,
                args.run_id,
                confirmed_stopped=args.confirmed_stopped,
            )
        elif args.command == "task-finish":
            result = accept_task_result(args.workspace, args.run_id, args.task_id)
        elif args.command == "wave-integrate":
            result = integrate_wave(args.workspace, args.run_id)
        elif args.command == "coordinator-commit":
            result = commit_coordinator_delta(args.workspace, args.run_id)
        elif args.command == "wave-promote":
            result = promote_wave(args.workspace, args.run_id, args.evidence)
        elif args.command == "wave-cleanup":
            result = cleanup_wave(args.workspace, args.run_id)
        elif args.command == "run-finalize":
            result = finalize_run(args.workspace, args.run_id, args.alignment)
        elif args.command == "run-resume":
            result = resume_run(
                args.workspace,
                args.run_id,
                capacity=args.capacity,
                alignment=args.alignment,
            )
        elif args.command == "plan-digest-recover":
            result = recover_replanned_plan_digest(
                args.workspace,
                args.run_id,
                args.expected_plan_sha256,
                args.expected_index_sha256,
            )
        elif args.command == "contract-delta-adopt":
            result = adopt_contract_delta(
                args.workspace,
                args.run_id,
                args.lifecycle_state,
            )
        elif args.command == "handoff-projection-recover":
            result = recover_handoff_projection(
                args.workspace,
                args.run_id,
                args.expected_handoff_sha256,
            )
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
                if args.disposition in {"applied", "no_effect"}:
                    impact = load_current_prompt_impact(run_dir, required=True)
                    assert impact is not None
                    receipt, _receipt_sha256 = impact
                    if receipt.get("revision") != args.revision:
                        raise PromptWorkspaceError(
                            "PROMPT_IMPACT_REQUIRED",
                            "steering cannot resolve without impact evidence for its revision",
                        )
                    has_effect = bool(receipt.get("effects"))
                    if (args.disposition == "no_effect" and has_effect) or (
                        args.disposition == "applied" and not has_effect
                    ):
                        raise PromptWorkspaceError(
                            "PROMPT_IMPACT_REQUIRED",
                            "steering disposition does not match the validated prompt impact",
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
        if resume_context is not None:
            complete_resume_transition(*resume_context)
            next_resume = resume_run(args.workspace, args.run_id)
            if not isinstance(result, dict):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "resume-controlled transition returned an invalid result",
                )
            result = {**result, "resume": next_resume}
    except PromptWorkspaceError as exc:
        if resume_context is not None:
            try:
                abort_resume_transition_if_unchanged(*resume_context)
            except PromptWorkspaceError:
                pass
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2
    finally:
        if resume_guard is not None:
            resume_guard.__exit__(None, None, None)
    if args.command in {"init", "reuse"}:
        result = {
            key: value
            for key, value in result.items()
            if key not in {"project_id", "scope_id", "lane_id"}
        }
    json_output = bool(
        getattr(args, "json", False) or getattr(args, "internal_json", False)
    )
    if args.command == "intake" and getattr(args, "json", False):
        result = {key: value for key, value in result.items() if key != "_internal"}
    emit(result, json_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
