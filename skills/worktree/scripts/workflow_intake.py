#!/usr/bin/env python3
"""Private direct-objective transitions; public syntax is defined by each skill."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

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
_load_skill_support('runtime', __file__, 'worktree/scripts/workflow_intake.py')
from agent_runtime import agent_home, native_session_id  # noqa: E402 — verified runtime support precedes imports
from workflow_state import WorkflowError, read  # noqa: E402 — verified runtime support precedes imports
from workflow_objectives import accept, continue_run, observe_binding, result, fence, authorize_coordinator, complete_run  # noqa: E402 — verified runtime support precedes imports


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--owner', choices=('implement', 'agentic-sdlc'), required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('accept', 'continue', 'observe'):
        command = sub.add_parser(action, allow_abbrev=False)
        command.add_argument('--project-root', type=Path, required=True)
        if action == 'continue':
            command.add_argument('--takeover', type=Path)
        if action == 'accept':
            command.add_argument('--objective', type=Path, required=True)
            command.add_argument('--revise', action='store_true')
    command = sub.add_parser('fence', allow_abbrev=False)
    command.add_argument('--run-dir', type=Path, required=True)
    command.add_argument('--reason', choices=('steering', 'cancelled'), required=True)
    for action in ('refinement-ready', 'settle-objective', 'complete', 'cancel-complete', 'retire-fenced-waves', 'resume-replanned'):
        command = sub.add_parser(action, allow_abbrev=False)
        command.add_argument('--run-dir', type=Path, required=True)
        if action == 'settle-objective':
            command.add_argument('--claim', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        home = agent_home()
        session = native_session_id()
        if hasattr(args, 'run_dir'):
            authorize_coordinator(args.run_dir, session, owner=args.owner)
        if args.action == 'complete':
            value = complete_run(args.run_dir, session)
        elif args.action in {'cancel-complete', 'retire-fenced-waves', 'resume-replanned'}:
            import workflow_reconcile
            value = getattr(workflow_reconcile, args.action.replace('-', '_'))(args.run_dir, session)
        elif args.action in {'refinement-ready', 'settle-objective'}:
            from workflow_refinement import ready, settle
            value = ready(args.run_dir) if args.action == 'refinement-ready' else settle(args.run_dir, read(args.claim))
        elif args.action == 'accept':
            value = accept(args.project_root, home, args.owner, session, read(args.objective), revise=args.revise)
        elif args.action == 'continue':
            value = continue_run(args.project_root, home, args.owner, session, takeover=args.takeover)
        elif args.action == 'fence':
            value = fence(args.run_dir, session, args.reason)
        else:
            binding = observe_binding(home, args.owner, session, args.project_root)
            value = {'status': 'UNBOUND'} if binding is None else result(*binding, 'BOUND')
    except (WorkflowError, OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'status': 'blocked', 'code': getattr(error, 'code', 'STATE_INVALID'), 'error': str(error)}))
        return 2
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
