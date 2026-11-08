#!/usr/bin/env python3
"""Private sequential native-agent fallback for frozen workflow assignments."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from workflow_process import OwnedProcessGroup, ProcessCleanupError


SDLC_EXECUTION_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "worktree" / "scripts"
)
if str(SDLC_EXECUTION_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SDLC_EXECUTION_SCRIPTS))


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
_load_skill_support('runtime', __file__, 'worktree/scripts/workflow_dispatch.py')

import workflow_worker_contract as worker_contract  # noqa: E402


from agent_runtime import agent_name, runtime_environment  # noqa: E402


class DispatchError(RuntimeError):
    def __init__(
        self, code: str, message: str, *,
        worker_result: dict[str, object] | None = None,
        prestart_result: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.worker_result = worker_result
        self.prestart_result = prestart_result


TERMINAL_WORKER_STATUSES = frozenset(
    {
        "WORKER_PRESTART_TIMEOUT",
        "WORKER_PRESTART_MUTATION",
        "WORKER_STALLED",
        "WORKER_READ_ONLY_TIMEOUT",
        "WORKER_SCOPE_VIOLATION",
        "WORKER_TIMEOUT",
    }
)
PRESTART_DIAGNOSTIC_BYTES = 65536


def _validated_result(
    stdout: str, selected: str, assignment: dict[str, object]
) -> dict[str, object]:
    try:
        result = json.loads(stdout)
    except (ValueError, RecursionError) as exc:
        raise DispatchError("WORKER_FAILED", "sequential worker returned invalid JSON") from exc
    if selected == "claude":
        if (not isinstance(result, dict) or result.get("type") != "result"
                or result.get("subtype") != "success" or result.get("is_error") is not False):
            raise DispatchError("WORKER_FAILED", "native worker result is unsuccessful")
        result = result.get("structured_output")
    rejection = worker_contract.result_rejection(result, assignment)
    if rejection is not None:
        raise DispatchError(
            "WORKER_FAILED", f"sequential worker result is invalid (reason={rejection})"
        )
    return result


def _prestart_failure(
    stdout: str, selected: str, assignment: dict[str, object]
) -> DispatchError:
    message = "sequential worker exited before task-start"
    if len(stdout.encode("utf-8")) > PRESTART_DIAGNOSTIC_BYTES:
        return DispatchError("WORKER_FAILED", f"{message} (reason=output_size)")
    try:
        result = _validated_result(stdout, selected, assignment)
    except DispatchError as exc:
        return DispatchError("WORKER_FAILED", f"{message}; {exc}")
    if result["status"] == "implemented":
        return DispatchError("WORKER_FAILED", f"{message} (reason=implemented_before_start)")
    if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) > PRESTART_DIAGNOSTIC_BYTES:
        return DispatchError("WORKER_FAILED", f"{message} (reason=result_size)")
    return DispatchError("WORKER_FAILED", message, prestart_result=result)


def _transition(
    assignment: dict[str, object], action: str, *extra: str
) -> dict[str, object]:
    command = [
        sys.executable,
        str(assignment["execution_helper"]),
        action,
        "--run-dir",
        str(assignment["run_dir"]),
        "--execution",
        str(assignment["execution_id"]),
        "--wave",
        str(assignment["wave_id"]),
        "--task",
        str(assignment["task_id"]),
        "--assignment-digest",
        str(assignment["assignment_digest"]),
        *extra,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(assignment["scope_cwd"]),
            env=runtime_environment(cwd=Path(assignment["scope_cwd"])),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
        value = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise DispatchError(
            "EXECUTION_STATE_INVALID", f"{action} transition failed"
        ) from exc
    if completed.returncode != 0 or not isinstance(value, dict):
        code = value.get("code") if isinstance(value, dict) else None
        raise DispatchError(
            str(code or "WORKER_FAILED"), f"{action} transition was rejected"
        )
    result = value.get("result")
    if value.get("status") != "ok" or not isinstance(result, dict):
        raise DispatchError(
            "EXECUTION_STATE_INVALID", f"{action} transition result is invalid"
        )
    return result


def _stop_worker(process: subprocess.Popen[str], *, terminate_grace: float) -> tuple[str, str]:
    owner = getattr(process, "_workflow_group", None)
    if owner is None:
        raise DispatchError("CLEANUP_BLOCKED", "worker group ownership is unavailable")
    try:
        owner.close(grace=terminate_grace)
        output = process.communicate(timeout=max(1, terminate_grace))
        owner.verify_absent()
        return output
    except (ProcessCleanupError, subprocess.TimeoutExpired) as exc:
        raise DispatchError("CLEANUP_BLOCKED", "worker termination is unproven; retain its resources") from exc


def _agent_available(agent_binary: str) -> bool:
    candidate = Path(agent_binary)
    if candidate.parent != Path("."):
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(agent_binary) is not None


def dispatch_sequential(
    assignment_paths: list[Path],
    output_schema: Path,
    *,
    agent_binary: str | None = None,
    watch_interval: float = 30,
    terminate_grace: float = 5,
) -> list[dict[str, object]]:
    if output_schema.is_symlink() or not output_schema.is_file():
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "worker output schema is unavailable"
        )
    selected = agent_name()
    agent_binary = agent_binary or selected
    results: list[dict[str, object]] = []
    for assignment_path in assignment_paths:
        assignment = worker_contract.load_assignment(assignment_path)
        launch = worker_contract.launch_contract(assignment_path, assignment)
        if output_schema.resolve() != Path(launch["output_schema_path"]):
            raise DispatchError("EXECUTION_STATE_INVALID", "worker output schema must be canonical")
        if not _agent_available(agent_binary):
            raise DispatchError(
                "ENVIRONMENT_BLOCKER", "selected agent executable is unavailable"
            )
        admitted = _transition(assignment, "task-arm")
        if admitted.get("launch") != launch:
            raise DispatchError("EXECUTION_STATE_INVALID", "native launch admission differs from its assignment")
        command = [
            agent_binary,
            "exec",
            "--cd",
            str(assignment["scope_cwd"]),
            "--sandbox",
            "workspace-write",
            *[argument for root in launch["support_write_roots"] for argument in ("--add-dir", root)],
            "--ephemeral",
            "--output-schema",
            str(output_schema.resolve()),
            "-",
        ]
        if selected == "claude":
            command = [agent_binary, "--add-dir", *launch["support_write_roots"],
                       "--print", "--output-format", "json",
                       "--no-session-persistence", "--json-schema", output_schema.read_text()]
        environment = runtime_environment(selected, fresh_session=True, cwd=Path(assignment["scope_cwd"]))
        owner = OwnedProcessGroup()
        try:
            process = subprocess.Popen(
                command,
                cwd=str(assignment["scope_cwd"]),
                env=environment,
                stdin=subprocess.PIPE,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                process_group=owner.id,
            )
        except BaseException as exc:
            owner.close(grace=0)
            owner.verify_absent()
            if not isinstance(exc, OSError):
                raise
            raise DispatchError(
                "ENVIRONMENT_BLOCKER", "selected agent executable is unavailable"
            ) from exc
        process._workflow_group = owner
        try:
            prompt: str | None = launch["prompt"]
            while True:
                try:
                    stdout, stderr = process.communicate(
                        input=prompt, timeout=watch_interval
                    )
                    break
                except subprocess.TimeoutExpired:
                    prompt = None
                    watched = _transition(assignment, "task-watch")
                    status = str(watched.get("status") or "")
                    if status in TERMINAL_WORKER_STATUSES:
                        raise DispatchError(
                            status, "sequential worker was interrupted"
                        )
                    if status not in {"PENDING_START", "ACTIVE"}:
                        raise DispatchError(
                            "EXECUTION_STATE_INVALID",
                            "sequential worker liveness state is invalid",
                        )
            if process.returncode != 0:
                # Report only fixed labels, never native stderr, prompts or paths.
                detail = "sequential worker exited unsuccessfully"
                if "invalid_json_schema" in stderr and "codex_output_schema" in stderr:
                    detail = "native worker output schema was rejected"
                raise DispatchError(
                    "WORKER_FAILED", f"{detail} (exit {process.returncode})"
                )
            final_watch = _transition(assignment, "task-watch")
            final_status = str(final_watch.get("status") or "")
            if final_status in TERMINAL_WORKER_STATUSES:
                raise DispatchError(
                    final_status, "sequential worker exceeded its budget"
                )
            if final_status != "ACTIVE":
                if final_status == "PENDING_START":
                    raise _prestart_failure(stdout, selected, assignment)
                raise DispatchError(
                    "WORKER_FAILED", "sequential worker exited before task-start"
                )
            result = _validated_result(stdout, selected, assignment)
            if result["status"] in {"blocked", "replan_required"}:
                raise DispatchError(
                    "WORKER_BLOCKED" if result["status"] == "blocked" else "WORKER_REPLAN_REQUIRED",
                    "sequential worker requires coordinator diagnosis or replanning",
                    worker_result=result,
                )
            results.append(result)
        finally:
            _stop_worker(process, terminate_grace=terminate_grace)
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment", type=Path, action="append", required=True)
    parser.add_argument(
        "--output-schema",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "assets"
        / "worker-result.schema.json",
    )
    parser.add_argument("--agent-binary", default=None)
    parser.add_argument("--watch-interval", type=float, default=30)
    parser.add_argument("--terminate-grace", type=float, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        results = dispatch_sequential(
            args.assignment,
            args.output_schema,
            agent_binary=args.agent_binary,
            watch_interval=args.watch_interval,
            terminate_grace=args.terminate_grace,
        )
    except (DispatchError, worker_contract.WorkerContractError) as exc:
        error: dict[str, object] = {
            "status": "error", "code": exc.code, "message": str(exc),
        }
        if isinstance(exc, DispatchError) and exc.worker_result is not None:
            error["worker_result"] = exc.worker_result
        if isinstance(exc, DispatchError) and exc.prestart_result is not None:
            error["prestart_result"] = exc.prestart_result
        print(json.dumps(error))
        return 2
    print(json.dumps({"status": "ok", "results": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
