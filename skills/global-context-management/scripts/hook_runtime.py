#!/usr/bin/env python3
"""Run reviewed hook payloads from a flat installation or native plugin."""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile



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
_load_skill_support('runtime', __file__, 'global-context-management/scripts/hook_runtime.py')

from agent_runtime import agent_home, claude_session_identity, normalize_payload  # noqa: E402 — verified bootstrap precedes runtime imports


OWNERS = (
    "agent-nebius-auth-setup", "commit", "config-codex",
    "maintain-project-specs", "prompt-session-intake", "sdlc-start", "troubleshoot",
)
HOOKS = (
    "pre_tool_use_nebius_auth.py", "commit_intent.py", "session_start_context.py",
    "user_prompt_context.py", "project_specs_lifecycle.py", "prompt_session_intake.py",
    "pre_tool_use_sdlc_policy.py", "remediation_attempt_guard.py", "stop_lifecycle_arbiter.py",
)


def project_hook_entries(hooks: dict, home: Path, agent: str) -> dict:
    """Bind reviewed source commands to a host; keep unrelated Codex hooks intact."""
    projected = json.loads(json.dumps(hooks))
    for entries in projected.values():
        for entry in entries:
            for handler in entry["hooks"]:
                tokens = shlex.split(handler.get("command", ""))
                if tokens and tokens[0] == "SKILLS_AGENT=codex":
                    tokens = tokens[1:]
                if (len(tokens) == 6 and tokens[:2] == ["python3", str(home / "hooks/hook_runtime.py")]
                        and tokens[2:5] == ["--agent", agent, "--hook"] and tokens[5] in HOOKS):
                    if agent == "claude":
                        handler.pop("statusMessage", None)
                    continue
                name = Path(tokens[1]).name if len(tokens) == 2 and tokens[0] == "python3" else ""
                managed_paths = {str(home / "hooks" / name), "${CODEX_HOME:-$HOME/.codex}/hooks/" + name}
                if name not in HOOKS or tokens[1] not in managed_paths:
                    if agent == "claude":
                        raise ValueError("Claude hook source has no reviewed host adapter")
                    continue
                handler["command"] = ("python3 " + shlex.quote(str(home / "hooks/hook_runtime.py"))
                                      + " --agent " + agent + " --hook " + shlex.quote(name))
                if agent == "claude":
                    handler.pop("statusMessage", None)
    return projected


def payload_files(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for owner in OWNERS:
        source = root / owner / "assets/hooks"
        if not source.is_dir():
            raise ValueError(f"missing reviewed hook owner: {owner}")
        for path in sorted(source.rglob("*")):
            rel = path.relative_to(source)
            if any(part in {"tests", "__pycache__"} or part.startswith(".") for part in rel.parts):
                continue
            if path.is_symlink():
                raise ValueError("hook payload must not contain symlinks")
            if not path.is_file() or path.name in {"README.md", "hooks.json", "hooks.json.template"}:
                continue
            target = str(rel).removesuffix(".template")
            data = path.read_bytes()
            if target in files and files[target] != data:
                raise ValueError(f"conflicting hook payload: {target}")
            files[target] = data
    support = root / "global-context-management/scripts"
    for name in ("agent_runtime.py", "hook_runtime.py", "trusted_runtime.py", "task_state_permissions.py"):
        files[name] = (support / name).read_bytes()
    return files


def private_directory(path: Path) -> None:
    for ancestor in (path, *path.parents):
        try:
            metadata = ancestor.lstat()
        except FileNotFoundError:
            continue
        # System-owned aliases such as macOS /var cannot be changed by a
        # peer user. User-controlled aliases are never runtime boundaries.
        if stat.S_ISLNK(metadata.st_mode):
            if metadata.st_uid != 0:
                raise ValueError("runtime data must not use symlinks")
            metadata = ancestor.stat()
        trusted_temp = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
        if (not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in {0, os.getuid()}
                or metadata.st_mode & 0o022 and not trusted_temp):
            raise ValueError("unsafe runtime directory ancestry")
    if not path.exists():
        if not path.parent.exists():
            private_directory(path.parent)
        path.mkdir(mode=0o700, exist_ok=True)
    if (path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid()
            or path.stat().st_mode & 0o022):
        raise ValueError("runtime directory must be owned by the current user")


def plugin_runtime(root: Path, agent: str) -> Path:
    settings = agent_home(agent) / ("hooks.json" if agent == "codex" else "settings.json")
    if settings.is_file():
        value = json.loads(settings.read_text())
        for entries in value.get("hooks", {}).values():
            for entry in entries:
                for handler in entry.get("hooks", []):
                    command = handler.get("command", "")
                    native_home = agent_home(agent)
                    legacy = any(
                        str(native_home / "hooks" / name) in command
                        or "${CODEX_HOME:-$HOME/.codex}/hooks/" + name in command
                        for name in HOOKS
                    )
                    if str(native_home / "hooks/hook_runtime.py") in command or legacy:
                        raise ValueError("local script hooks are registered; choose one installation route")
    files = payload_files(root)
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        digest.update(name.encode() + b"\0" + data + b"\0")
    key = "PLUGIN_DATA" if agent == "codex" else "CLAUDE_PLUGIN_DATA"
    raw = os.environ.get(key)
    if not raw or not Path(raw).is_absolute():
        raise ValueError(f"native plugin runtime requires {key}")
    base = Path(raw) / "hook-runtimes"
    private_directory(base)
    target = base / digest.hexdigest()
    if target.exists():
        private_directory(target)
        for name, data in files.items():
            path = target / name
            private_directory(path.parent)
            metadata = path.lstat()
            if (any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(target))
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.getuid() or metadata.st_nlink != 1
                    or metadata.st_mode & 0o022 or path.read_bytes() != data):
                raise ValueError("rendered hook runtime has drifted")
        return target
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=base) as temp:
        staging = Path(temp) / "hooks"
        staging.mkdir(mode=0o700)
        for name, data in files.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(data)
            path.chmod(0o600)
        try:
            staging.rename(target)
        except OSError as error:
            if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise
            return plugin_runtime(root, agent)
    return target


def persist_claude_context(payload: dict, env: dict[str, str]) -> None:
    if payload.get("hook_event_name") != "SessionStart":
        return
    raw = os.environ.get("CLAUDE_ENV_FILE")
    session = payload.get("session_id")
    if not raw or not isinstance(session, str) or not session:
        return
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("invalid native session environment file")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        if metadata.st_uid != os.getuid() or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("foreign native session environment file")
        handle.write("\nexport SKILLS_AGENT=claude\nexport SKILLS_SESSION_ID="
                     + shlex.quote(session) + "\nunset CODEX_THREAD_ID\n")
    env["SKILLS_SESSION_ID"] = session


def bound_claude_stop(payload: dict, output: dict) -> dict:
    """Stop before the host's eighth consecutive continuation; retain workflow state."""
    if output.get("decision") != "block" or output.get("continue") is False:
        return output
    session, prompt = payload.get("session_id"), payload.get("turn_id")
    if not isinstance(session, str) or not session or not isinstance(prompt, str) or not prompt:
        return {"continue": False, "stopReason": "Cannot bind continuation to a native prompt; retain state and resume explicitly."}
    root = agent_home("claude") / "hook-runtime" / hashlib.sha256(session.encode()).hexdigest()
    private_directory(root)
    path = root / "stop-count.json"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1 or metadata.st_mode & 0o022):
            raise ValueError("unsafe continuation counter")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        raw = handle.read(4097)
        if len(raw) > 4096:
            raise ValueError("invalid continuation counter")
        previous = json.loads(raw) if raw else {}
        key = hashlib.sha256(prompt.encode()).hexdigest()
        count = previous.get("count", 0) if previous.get("prompt") == key else 0
        if not isinstance(count, int) or count < 0:
            raise ValueError("invalid continuation counter")
        count += 1
        handle.seek(0)
        json.dump({"prompt": key, "count": count}, handle)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    if count >= 8:
        return {"continue": False, "stopReason": "Claude continuation limit reached. Unfinished workflow state is retained; resume explicitly in a new user turn.",
                "systemMessage": "Workflow remains unfinished and resumable; no completion is claimed."}
    return output


def bind_claude_bash(payload: dict, output: dict) -> dict:
    """The auth adapter alone owns command rewrites, including native identity."""
    specific = output.get("hookSpecificOutput", {})
    if output.get("continue") is False or specific.get("permissionDecision") == "deny":
        return output
    data = {**payload["tool_input"], **specific.get("updatedInput", {})}
    command = data.get("command")
    if not isinstance(command, str):
        raise ValueError("native Bash input must contain a command")
    identity = claude_session_identity(payload)
    data["command"] = ("export SKILLS_AGENT=claude SKILLS_SESSION_ID=" + shlex.quote(identity)
                       + "\nunset CODEX_THREAD_ID\n" + command)
    return {**output, "hookSpecificOutput": {**specific, "hookEventName": "PreToolUse", "updatedInput": data}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, choices=("codex", "claude"))
    parser.add_argument("--hook", required=True, choices=HOOKS)
    parser.add_argument("--plugin-root", type=Path)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("hook input exceeds limit")
        payload = normalize_payload(json.loads(raw), args.agent)
        directory = (plugin_runtime(args.plugin_root.resolve(strict=True), args.agent)
                     if args.plugin_root else Path(__file__).resolve().parent)
        env = os.environ.copy()
        env["SKILLS_AGENT"] = args.agent
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if args.agent == "claude":
            env.pop("CODEX_THREAD_ID", None)
            persist_claude_context(payload, env)
            env["SKILLS_SESSION_ID"] = claude_session_identity(payload)
        completed = subprocess.run(
            [sys.executable, "-B", str(directory / args.hook)],
            input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=35,
        )
        if completed.returncode:
            raise ValueError("reviewed hook subprocess failed")
        output = json.loads(completed.stdout) if completed.stdout.strip() else {}
        if not isinstance(output, dict):
            raise ValueError("reviewed hook returned invalid output")
        if args.agent == "claude" and args.hook == "stop_lifecycle_arbiter.py":
            output = bound_claude_stop(payload, output)
        if (args.agent == "claude" and args.hook == "pre_tool_use_nebius_auth.py"
                and payload.get("hook_event_name") == "PreToolUse" and payload.get("tool_name") == "Bash"):
            output = bind_claude_bash(payload, output)
        print(json.dumps(output))
        return 0
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        # Advisory lifecycle/context hooks must never become blocking hooks.
        advisory = args.hook in {"project_specs_lifecycle.py", "session_start_context.py",
                                 "user_prompt_context.py", "prompt_session_intake.py"}
        print(json.dumps({"systemMessage": "Skills hook runtime unavailable; review installation."}
                         if advisory else {"continue": False, "stopReason":
                                           "Skills hook runtime unavailable; review installation."}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
