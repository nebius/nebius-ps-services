#!/usr/bin/env python3
"""Private CLI for Agentic SDLC execution-plane transitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any



# BEGIN shared runtime bootstrap
def _load_skill_support(group, anchor_file, declared_path, *, source_only=False):
    import hashlib as _hashlib
    import os as _os
    from pathlib import Path as _Path
    import stat as _stat
    import sys as _sys
    from types import ModuleType as _ModuleType

    def read_source(path):
        path = _Path(_os.path.abspath(path))
        for part in (*reversed(path.parents), path):
            metadata = part.lstat()
            if _stat.S_ISLNK(metadata.st_mode):
                if metadata.st_uid != 0 or part == path:
                    raise ImportError("shared runtime path contains an unsafe symlink")
                metadata = part.stat()
            if part != path:
                sticky = metadata.st_uid == 0 and metadata.st_mode & _stat.S_ISVTX
                if (not _stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in {0, _os.getuid()}
                        or metadata.st_mode & 0o022 and not sticky):
                    raise ImportError("unsafe shared runtime ancestry")
        path = path.resolve(strict=True)
        descriptor = _os.open(path.anchor, _os.O_RDONLY | _os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                child = _os.open(part, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW, dir_fd=descriptor)
                _os.close(descriptor)
                descriptor = child
                metadata = _os.fstat(descriptor)
                sticky = metadata.st_uid == 0 and metadata.st_mode & _stat.S_ISVTX
                if metadata.st_uid not in {0, _os.getuid()} or metadata.st_mode & 0o022 and not sticky:
                    raise ImportError("unsafe shared runtime ancestry")
            child = _os.open(path.name, _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_NONBLOCK, dir_fd=descriptor)
            try:
                before = _os.fstat(child)
                if (not _stat.S_ISREG(before.st_mode) or before.st_uid != _os.getuid()
                        or before.st_mode & 0o022 or before.st_nlink != 1 or before.st_size > 1048576):
                    raise ImportError("unsafe shared runtime source")
                data = bytearray()
                while chunk := _os.read(child, min(65536, 1048577 - len(data))):
                    data.extend(chunk)
                    if len(data) > 1048576:
                        raise ImportError("shared runtime source exceeds size limit")
                after = _os.fstat(child)
                bound = _os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                def identity(value):
                    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                if identity(before) != identity(after) or identity(after) != identity(bound):
                    raise ImportError("shared runtime source changed while reading")
                return bytes(data), identity(after)
            finally:
                _os.close(child)
        finally:
            _os.close(descriptor)

    anchor = _Path(_os.path.abspath(anchor_file))
    declared_paths = (declared_path,) if isinstance(declared_path, str) else declared_path
    candidates = []
    for declared in declared_paths:
        relative = _Path(declared)
        if tuple(anchor.parts[-len(relative.parts):]) == relative.parts:
            catalog = anchor.parents[len(relative.parts) - 1]
            candidates.append(("catalog", catalog, catalog / "global-context-management/scripts"))
    if not source_only:
        flat = anchor.parent.parent if anchor.parent.name == "lib" else anchor.parent
        candidates.append(("flat", flat, flat))
        agent = "codex" if _os.environ.get("CODEX_THREAD_ID") else _os.environ.get("SKILLS_AGENT", "codex")
        if agent not in {"codex", "claude"}:
            raise ImportError("SKILLS_AGENT must be codex or claude")
        key, default = ("CODEX_HOME", ".codex") if agent == "codex" else ("CLAUDE_CONFIG_DIR", ".claude")
        home = _Path(_os.environ.get(key, str(_Path.home() / default))).expanduser()
        if not home.is_absolute():
            raise ImportError("shared runtime home must be absolute")
        candidates.append(("flat", home / "hooks", home / "hooks"))
    for kind, root, support in candidates:
        loader_path = support / "trusted_runtime.py"
        if not loader_path.exists() and not loader_path.is_symlink():
            if ((kind == "catalog" and (support.exists() or support.is_symlink()))
                    or any((support / name).exists() or (support / name).is_symlink()
                           for name in ("agent_runtime.py", "hook_runtime.py", "task_state_permissions.py"))):
                raise ImportError("incomplete shared runtime bundle; reinstall current support")
            continue
        data, identity = read_source(loader_path)
        digest = _hashlib.sha256(data).hexdigest()
        cache_name = "_skills_trusted_runtime"
        loader = _sys.modules.get(cache_name)
        provenance = (str(loader_path), identity, digest)
        if cache_name in _sys.modules:
            if (type(loader) is not _ModuleType or getattr(loader, "_bootstrap_provenance", None) != provenance):
                raise ImportError("conflicting shared runtime loader")
        else:
            loader = _ModuleType(cache_name)
            loader.__file__ = str(loader_path)
            exec(compile(data, str(loader_path), "exec"), loader.__dict__)
            loader._bootstrap_provenance = provenance
            loader._read_source = read_source
            _sys.modules[cache_name] = loader
        return loader.load_support(group, anchor=(kind, root), source_only=source_only)
    raise ImportError("Shared skill runtime unavailable; install the complete current skill support")
# END shared runtime bootstrap
_load_skill_support('runtime', __file__, 'sdlc-prepare-execution/scripts/sdlc_execution.py')

from sdlc_execution_core import (  # noqa: E402 — verified bootstrap precedes runtime imports
    ExecutionError,
    advance_batch,
    arm_task,
    complete_wave,
    describe_status,
    finish_task,
    heartbeat_task,
    integrate_wave,
    prepare_execution,
    prepare_wave,
    promote_feature,
    replan_future,
    requeue_task,
    recover_task,
    seal_feature,
    seal_tdd_base,
    start_task,
    watch_task,
)
from sdlc_execution_interop import (  # noqa: E402 — verified bootstrap precedes runtime imports
    complete_source_integration,
    ExecutionInteropError,
    release as release_outer_lease,
)

from agent_runtime import native_session_id  # noqa: E402


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
    value = native_session_id()
    if not isinstance(value, str) or not value.strip():
        raise ExecutionError(
            "SESSION_ID_UNAVAILABLE", "native session identity is required for worker commands"
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

    task_arm = sub.add_parser("task-arm")
    task_arm.add_argument("--run-dir", type=Path, required=True)
    task_arm.add_argument("--feature", required=True)
    task_arm.add_argument("--wave", required=True)
    task_arm.add_argument("--task", required=True)
    task_arm.add_argument("--assignment-digest", required=True)

    task_requeue = sub.add_parser("task-requeue")
    task_requeue.add_argument("--run-dir", type=Path, required=True)
    task_requeue.add_argument("--feature", required=True)
    task_requeue.add_argument("--wave", required=True)
    task_requeue.add_argument("--task", required=True)
    task_requeue.add_argument("--assignment-digest", required=True)
    task_requeue.add_argument("--expected-dispatched-at", required=True)
    task_requeue.add_argument("--confirmed-stopped", action="store_true")

    task_heartbeat = sub.add_parser("task-heartbeat")
    task_heartbeat.add_argument("--run-dir", type=Path, required=True)
    task_heartbeat.add_argument("--feature", required=True)
    task_heartbeat.add_argument("--wave", required=True)
    task_heartbeat.add_argument("--task", required=True)
    task_heartbeat.add_argument("--assignment-digest", required=True)
    task_heartbeat.add_argument("--phase", required=True)

    task_watch = sub.add_parser("task-watch")
    task_watch.add_argument("--run-dir", type=Path, required=True)
    task_watch.add_argument("--feature", required=True)
    task_watch.add_argument("--wave", required=True)
    task_watch.add_argument("--task", required=True)
    task_watch.add_argument("--assignment-digest", required=True)

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
        if args.scope_cwd.expanduser().resolve() != Path.cwd().resolve():
            raise ExecutionError(
                "WORKTREE_CONFLICT", "task-start must run from the assigned scope cwd"
            )
        return start_task(
            args.run_dir,
            args.feature,
            args.wave,
            args.task,
            args.assignment_digest,
            runtime_session_identity(),
            args.scope_cwd,
        )
    if args.command == "task-arm":
        return arm_task(
            args.run_dir,
            args.feature,
            args.wave,
            args.task,
            args.assignment_digest,
        )
    if args.command == "task-requeue":
        return requeue_task(
            args.run_dir,
            args.feature,
            args.wave,
            args.task,
            args.assignment_digest,
            args.expected_dispatched_at,
            confirmed_stopped=args.confirmed_stopped,
        )
    if args.command == "task-heartbeat":
        return heartbeat_task(
            args.run_dir,
            args.feature,
            args.wave,
            args.task,
            args.assignment_digest,
            args.phase,
            runtime_session_identity(),
            Path.cwd(),
        )
    if args.command == "task-watch":
        return watch_task(
            args.run_dir,
            args.feature,
            args.wave,
            args.task,
            args.assignment_digest,
        )
    if args.command == "task-recover":
        if args.scope_cwd.expanduser().resolve() != Path.cwd().resolve():
            raise ExecutionError(
                "WORKTREE_CONFLICT", "task-recover must run from the assigned scope cwd"
            )
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
