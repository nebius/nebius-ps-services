#!/usr/bin/env python3
"""Private sequential native-agent worker fallback for Agentic SDLC assignments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys


SDLC_EXECUTION_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "sdlc-prepare-execution" / "scripts"
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
_load_skill_support('runtime', __file__, 'sdlc-implement-plan/scripts/worker_dispatch.py')

from sdlc_evidence_security import contains_sensitive  # noqa: E402


from agent_runtime import agent_name, runtime_environment  # noqa: E402


class DispatchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
REQUIREMENT_ID_RE = re.compile(r"REQ-[0-9]{3,}\Z")
DESIGN_ID_RE = re.compile(r"FEAT-[0-9]{3,}\Z")
SPEC_GAP_KINDS = {"requirement", "design", "traceability"}


def _valid_spec_gaps(value: object) -> bool:
    if not isinstance(value, list):
        return False
    for gap in value:
        if (
            not isinstance(gap, dict)
            or set(gap)
            != {"kind", "summary", "evidence", "requirement_ids", "design_ids"}
            or gap.get("kind") not in SPEC_GAP_KINDS
            or not isinstance(gap.get("summary"), str)
            or not str(gap["summary"]).strip()
            or contains_sensitive(str(gap["summary"]))
            or not isinstance(gap.get("evidence"), list)
            or not gap["evidence"]
            or any(
                not isinstance(item, str)
                or not item.strip()
                or contains_sensitive(item)
                for item in gap["evidence"]
            )
            or not isinstance(gap.get("requirement_ids"), list)
            or any(
                REQUIREMENT_ID_RE.fullmatch(str(item)) is None
                for item in gap["requirement_ids"]
            )
            or not isinstance(gap.get("design_ids"), list)
            or any(
                DESIGN_ID_RE.fullmatch(str(item)) is None
                for item in gap["design_ids"]
            )
        ):
            return False
    return True


def stable_digest(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def canonical_execution_helper() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "sdlc-prepare-execution"
        / "scripts"
        / "sdlc_execution.py"
    ).resolve()


def load_assignment(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "worker assignment is unreadable"
        ) from exc
    if isinstance(value, dict) and value.get("schema") in {
        "agentic-sdlc/worker-assignment-v1",
        "agentic-sdlc/worker-assignment-v2",
        "agentic-sdlc/worker-assignment-v3",
    }:
        raise DispatchError(
            "WORKFLOW_UPGRADE_REQUIRED", "legacy worker assignment is unsupported"
        )
    if not isinstance(value, dict) or value.get("schema") != (
        "agentic-sdlc/worker-assignment-v4"
    ):
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "worker assignment schema is invalid"
        )
    digest = value.get("assignment_digest")
    unsigned = dict(value)
    unsigned.pop("assignment_digest", None)
    if not isinstance(digest, str) or stable_digest(unsigned) != digest:
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "worker assignment digest is invalid"
        )
    scope_cwd = Path(str(value.get("scope_cwd") or ""))
    if not scope_cwd.is_absolute() or scope_cwd.is_symlink() or not scope_cwd.is_dir():
        raise DispatchError("WORKTREE_CONFLICT", "worker scope cwd is invalid")
    helper = Path(str(value.get("execution_helper") or ""))
    expected_helper = canonical_execution_helper()
    run_dir = Path(str(value.get("run_dir") or ""))
    profile = value.get("worker_profile")
    expected_read_only = (240, 300) if profile == "standard" else (360, 420)
    spec_receipt = value.get("project_spec_receipt")
    if (
        not helper.is_absolute()
        or helper.is_symlink()
        or not helper.is_file()
        or helper.resolve() != expected_helper
        or not expected_helper.is_file()
        or not run_dir.is_absolute()
        or run_dir.is_symlink()
        or value.get("heartbeat_seconds") != 30
        or value.get("start_seconds") != 60
        or value.get("stall_seconds") != 240
        or value.get("max_seconds") != 1800
        or profile not in {"standard", "integration"}
        or value.get("read_only_warning_seconds") != expected_read_only[0]
        or value.get("read_only_seconds") != expected_read_only[1]
        or value.get("worker_phases")
        != ["preflight", "implementing", "validating", "reviewing", "reporting"]
        or SHA256_RE.fullmatch(str(value.get("root_intent_sha256") or "")) is None
        or not isinstance(spec_receipt, dict)
        or set(spec_receipt)
        != {"schema", "requirements_sha256", "design_sha256"}
        or spec_receipt.get("schema")
        != "maintain-project-specs.worker-receipt.v1"
        or any(
            SHA256_RE.fullmatch(str(spec_receipt.get(field) or "")) is None
            for field in ("requirements_sha256", "design_sha256")
        )
    ):
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "worker assignment liveness is invalid"
        )
    handoff_path = Path(str(value.get("incoming_handoff_path") or ""))
    if (
        not handoff_path.is_absolute()
        or handoff_path.is_symlink()
        or not handoff_path.is_file()
        or (handoff_path.stat().st_mode & 0o777) != 0o600
    ):
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "incoming handoff path or mode is invalid"
        )
    try:
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "incoming handoff is unreadable"
        ) from exc
    unsigned_handoff = dict(handoff) if isinstance(handoff, dict) else {}
    handoff_digest = unsigned_handoff.pop("handoff_digest", None)
    if (
        not isinstance(handoff, dict)
        or handoff.get("schema") != "agentic-sdlc/incoming-handoff-v1"
        or handoff.get("feature_id") != value.get("feature_id")
        or handoff.get("wave_id") != value.get("wave_id")
        or handoff.get("task_id") != value.get("task_id")
        or handoff_digest != value.get("incoming_handoff_digest")
        or handoff_digest != stable_digest(unsigned_handoff)
    ):
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "incoming handoff context is invalid"
        )
    return value


def worker_prompt(assignment_path: Path, assignment: dict[str, object]) -> str:
    run_dir = Path(str(assignment["run_dir"]))
    helper = Path(str(assignment["execution_helper"]))
    start = [
        sys.executable,
        str(helper),
        "task-start",
        "--run-dir",
        str(run_dir),
        "--feature",
        str(assignment["feature_id"]),
        "--wave",
        str(assignment["wave_id"]),
        "--task",
        str(assignment["task_id"]),
        "--assignment-digest",
        str(assignment["assignment_digest"]),
        "--scope-cwd",
        str(assignment["scope_cwd"]),
    ]
    instructions = [
        "Implement exactly one immutable Agentic SDLC worker assignment.",
        "Run the task-start command represented by this JSON argv before editing:",
        json.dumps(start),
        "Run direct task-heartbeat commands at least every "
        f"{assignment['heartbeat_seconds']} seconds while working. Use one of "
        "the assignment's worker_phases and this JSON argv prefix:",
        json.dumps(
            [
                sys.executable,
                str(helper),
                "task-heartbeat",
                "--run-dir",
                str(run_dir),
                "--feature",
                str(assignment["feature_id"]),
                "--wave",
                str(assignment["wave_id"]),
                "--task",
                str(assignment["task_id"]),
                "--assignment-digest",
                str(assignment["assignment_digest"]),
                "--phase",
            ]
        ),
        "Invoke heartbeats directly; never create a background heartbeat loop.",
        "Read the assignment JSON at:",
        str(assignment_path.resolve()),
        "Work only from its scope_cwd and write claims.",
        "Inherit root_intent_sha256 and project_spec_receipt exactly. Do not "
        "reclassify user intent or edit canonical project specs.",
        "Report any requirement, design, or traceability discovery through the "
        "typed spec_gaps result field for root-coordinator reconciliation.",
        "Do not commit, merge, replan, edit coordinator state, or start another task.",
    ]
    if assignment.get("diagnosis_id") is not None:
        instructions.extend(
            [
                "This is a corrective task bound to the assignment's diagnosis_id.",
                "After the smallest bounded repair, run the assignment's original "
                "regression_oracle first, then its affected-boundary validation.",
                "Do not reinterpret or modify any completed task definition.",
            ]
        )
    instructions.extend(
        [
            "Validate the change and perform a focused code review.",
            "Return only the schema-conforming worker result.",
        ]
    )
    return "\n".join(instructions)


def _transition(
    assignment: dict[str, object], action: str, *extra: str
) -> dict[str, object]:
    command = [
        sys.executable,
        str(assignment["execution_helper"]),
        action,
        "--run-dir",
        str(assignment["run_dir"]),
        "--feature",
        str(assignment["feature_id"]),
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


def _stop_worker(
    process: subprocess.Popen[str], *, terminate_grace: float
) -> tuple[str, str]:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        elif process.poll() is None:
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=terminate_grace)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.poll() is None:
                process.kill()
        except ProcessLookupError:
            pass
        return process.communicate()


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
        assignment = load_assignment(assignment_path)
        if not _agent_available(agent_binary):
            raise DispatchError(
                "ENVIRONMENT_BLOCKER", "selected agent executable is unavailable"
            )
        command = [
            agent_binary,
            "exec",
            "--cd",
            str(assignment["scope_cwd"]),
            "--sandbox",
            "workspace-write",
            "--ephemeral",
            "--output-schema",
            str(output_schema.resolve()),
            "-",
        ]
        if selected == "claude":
            command = [agent_binary, "--print", "--output-format", "json",
                       "--no-session-persistence", "--json-schema", output_schema.read_text()]
        try:
            process = subprocess.Popen(
                command,
                cwd=str(assignment["scope_cwd"]),
                env=runtime_environment(selected, fresh_session=True),
                stdin=subprocess.PIPE,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            raise DispatchError(
                "ENVIRONMENT_BLOCKER", "selected agent executable is unavailable"
            ) from exc
        try:
            _transition(assignment, "task-arm")
            prompt: str | None = worker_prompt(assignment_path, assignment)
            while True:
                try:
                    stdout, _stderr = process.communicate(
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
                raise DispatchError(
                    "WORKER_FAILED", "sequential worker exited unsuccessfully"
                )
            final_watch = _transition(assignment, "task-watch")
            final_status = str(final_watch.get("status") or "")
            if final_status in TERMINAL_WORKER_STATUSES:
                raise DispatchError(
                    final_status, "sequential worker exceeded its budget"
                )
            if final_status != "ACTIVE":
                raise DispatchError(
                    "WORKER_FAILED", "sequential worker exited before task-start"
                )
            try:
                result = json.loads(stdout)
                if selected == "claude":
                    if (not isinstance(result, dict) or result.get("type") != "result"
                            or result.get("subtype") != "success" or result.get("is_error") is not False):
                        raise DispatchError("WORKER_FAILED", "native worker result is unsuccessful")
                    result = result.get("structured_output")
            except json.JSONDecodeError as exc:
                raise DispatchError(
                    "WORKER_FAILED", "sequential worker returned invalid JSON"
                ) from exc
            if (
                not isinstance(result, dict)
                or result.get("task_id") != assignment["task_id"]
                or result.get("assignment_digest")
                != assignment["assignment_digest"]
                or result.get("status") not in {"implemented", "replan_required"}
                or not isinstance(result.get("validation"), str)
                or not isinstance(result.get("review"), str)
                or not isinstance(result.get("summary"), str)
                or not isinstance(result.get("decisions"), list)
                or not isinstance(result.get("open_risks"), list)
                or not _valid_spec_gaps(result.get("spec_gaps"))
                or (
                    result.get("status") == "implemented"
                    and bool(result.get("spec_gaps"))
                )
                or (
                    result.get("status") == "replan_required"
                    and not bool(result.get("spec_gaps"))
                )
            ):
                raise DispatchError(
                    "WORKER_FAILED", "sequential worker result is invalid"
                )
            results.append(result)
        except BaseException:
            _stop_worker(process, terminate_grace=terminate_grace)
            raise
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
    except DispatchError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "message": str(exc)}))
        return 2
    print(json.dumps({"status": "ok", "results": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
