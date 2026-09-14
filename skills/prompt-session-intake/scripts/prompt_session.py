#!/usr/bin/env python3
"""Coordinator-only CLI for accepted prompt-session transitions."""

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
_load_skill_support('prompt-intake', __file__, 'prompt-session-intake/scripts/prompt_session.py')

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

from agent_runtime import native_session_id  # noqa: E402


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
    status.add_argument("--agent-home", dest="codex_home", type=Path)
    return value


def execute(args: argparse.Namespace) -> dict[str, Any]:
    home = codex_home()
    if args.action == "accept":
        session_id = native_session_id()
        if not session_id:
            raise PromptSessionError(
                "IDENTITY_REQUIRED", "current native session identity is unavailable"
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
        session_id = native_session_id()
        if not session_id:
            raise PromptSessionError(
                "IDENTITY_REQUIRED", "current native session identity is unavailable"
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
        session_id = native_session_id()
        if not session_id:
            raise PromptSessionError(
                "IDENTITY_REQUIRED", "current native session identity is unavailable"
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
