#!/usr/bin/env python3
"""Record nonauthorizing root-turn metadata for semantic commit requests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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
_load_skill_support('runtime', __file__, 'commit/assets/hooks/commit_intent.py')

from agent_runtime import agent_home  # noqa: E402


RECEIPT_SCHEMA = "commit-transaction.intent.v1"
REPOSITORY_SHAPING_GIT_ENV = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_ATTR_NOSYSTEM",
        "GIT_ATTR_SOURCE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_SYSTEM",
        "GIT_DEFAULT_HASH",
        "GIT_DIR",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_EXEC_PATH",
        "GIT_GRAFT_FILE",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_OPTIONAL_LOCKS",
        "GIT_PREFIX",
        "GIT_QUARANTINE_PATH",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)
REPOSITORY_SHAPING_GIT_ENV_PREFIXES = ("GIT_ATTR_", "GIT_CONFIG_")


class IntentError(RuntimeError):
    """The explicit commit intent could not be bound safely."""


def _digest(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _excluded(payload: dict[str, Any]) -> bool:
    if payload.get("stop_hook_active") or payload.get("is_subagent"):
        return True
    origins = {
        str(payload.get("prompt_source") or "").casefold(),
        str(payload.get("source") or "").casefold(),
    }
    if origins & {"stop", "continuation", "compaction", "subagent", "system"}:
        return True
    agent_type = str(payload.get("agent_type") or "").casefold()
    return bool(agent_type and agent_type not in {"root", "primary"})


def _git_environment() -> dict[str, str]:
    shaped = sorted(
        name
        for name in os.environ
        if (
            name in REPOSITORY_SHAPING_GIT_ENV
            or name.startswith(REPOSITORY_SHAPING_GIT_ENV_PREFIXES)
        )
    )
    if shaped:
        raise IntentError(
            "repository-shaping Git environment must be unset: " + ", ".join(shaped)
        )
    return os.environ.copy()


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_environment(),
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise IntentError("Git identity could not be resolved") from error
    if completed.returncode != 0:
        raise IntentError(
            completed.stderr.strip() or "Git identity could not be resolved"
        )
    return completed.stdout.strip()


def _identity(cwd: object) -> dict[str, str]:
    candidate = Path(str(cwd or ".")).expanduser().resolve(strict=True)
    root = Path(_git(candidate, "rev-parse", "--show-toplevel")).resolve(strict=True)
    common = Path(_git(root, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = root / common
    common = common.resolve(strict=True)
    reference = _git(root, "symbolic-ref", "-q", "HEAD")
    head = _git(root, "rev-parse", "HEAD")
    return {
        "repo_root": str(root),
        "worktree": str(root),
        "common_dir": str(common),
        "ref": reference,
        "base_head": head,
    }


def _codex_home() -> Path:
    value = agent_home()
    if not value.is_absolute():
        raise IntentError("CODEX_HOME must be absolute")
    return Path(os.path.abspath(value)).resolve(strict=False)


def _authorization_path(identity: dict[str, str], session_id: object) -> Path:
    repo_key = _digest(identity["common_dir"])[:24]
    session_key = _digest(session_id)[:24]
    return (
        _codex_home()
        / "commit-transactions"
        / repo_key
        / "sessions"
        / session_key
        / "authorization.json"
    )


def _claim_path(identity: dict[str, str]) -> Path:
    repo_key = _digest(identity["common_dir"])[:24]
    ref_key = _digest(identity["ref"])[:24]
    return (
        _codex_home() / "commit-transactions" / repo_key / "claims" / f"{ref_key}.json"
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _secure_directory(path: Path) -> None:
    if path.is_symlink():
        raise IntentError(
            f"private commit state must not use symlink directories: {path}"
        )
    if not path.exists():
        if not path.parent.exists():
            _secure_directory(path.parent)
        path.mkdir(mode=0o700)
        _fsync_directory(path.parent)
    if not path.is_dir() or path.resolve(strict=True) != path:
        raise IntentError(f"private commit state directory is invalid: {path}")
    path.chmod(0o700)


def _write(path: Path, value: dict[str, object]) -> None:
    _secure_directory(path.parent)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        _fsync_directory(path.parent)
    except OSError as error:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        raise IntentError("commit authorization could not be persisted") from error


def _unavailable(reason: str) -> dict[str, Any]:
    """Report capture failure without granting authority or exposing input."""
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"Commit intent receipt unavailable ({reason}). "
                "No current receipt context was produced. Do not prepare a commit "
                "from an earlier receipt or modify authorization/claim state. "
                "Ordinary work may continue; diagnose native hook capture before publication."
            ),
        },
    }


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("hook_event_name") != "UserPromptSubmit" or _excluded(payload):
        return {}
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _unavailable("PROMPT_UNAVAILABLE")
    session_id = payload.get("session_id")
    turn_id = payload.get("turn_id")
    if any(not isinstance(value, str) or not value.strip() for value in (session_id, turn_id)):
        return _unavailable("NATIVE_IDENTITY_UNAVAILABLE")
    try:
        identity = _identity(payload.get("cwd"))
    except (IntentError, OSError):
        return _unavailable("REPOSITORY_UNAVAILABLE")
    path = _authorization_path(identity, session_id)
    claim_path = _claim_path(identity)
    receipt: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        **identity,
        "session_sha256": _digest(session_id),
        "turn_sha256": _digest(turn_id),
        "prompt_sha256": _digest(prompt),
    }
    try:
        _write(path.with_name("intent.json"), receipt)
    except (IntentError, OSError):
        return _unavailable("RECEIPT_WRITE_FAILED")
    receipt_digest = hashlib.sha256(
        (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    ).hexdigest()
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "Commit intent receipt only; no Git action is authorized by this hook. "
                "Interpret the root user's request semantically: commit, commit-push or create-pr may "
                "be requested in natural language or with a skill name anywhere. Discussion, "
                "help, quotations, negation and skill repair are not action requests. "
                "Only for an authorized action, use the canonical commit transaction prepare "
                f"with --requested-action commit|commit-push|create-pr and --intent-sha256 {receipt_digest}. "
                f"Canonical authorization path: {path}. Canonical claim path: {claim_path}. "
                "Never ask for a repeated magic phrase or create authorization files manually."
            ),
        },
    }


def main() -> int:
    try:
        payload: Any = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise IntentError("hook payload must be an object")
        output = evaluate(payload)
    except Exception:
        output = _unavailable("CAPTURE_FAILED")
    if output:
        print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
