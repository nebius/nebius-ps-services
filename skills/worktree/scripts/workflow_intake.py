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
    for action in ('accept', 'continue', 'observe', 'status'):
        command = sub.add_parser(action, allow_abbrev=False)
        command.add_argument('--project-root', type=Path, required=True)
        if action in {'accept', 'continue'}:
            command.add_argument('--input-dependencies', type=Path)
        if action == 'continue':
            command.add_argument('--takeover', type=Path)
        if action == 'accept':
            command.add_argument('--objective', type=Path, required=True)
            command.add_argument('--revise', action='store_true')
    command = sub.add_parser('fence', allow_abbrev=False)
    command.add_argument('--run-dir', type=Path, required=True)
    command.add_argument('--reason', choices=('steering', 'cancelled'), required=True)
    command = sub.add_parser('admit-program', allow_abbrev=False)
    command.add_argument('--run-dir', type=Path, required=True)
    command.add_argument('--candidate', type=Path, required=True)
    command = sub.add_parser('aggregate-correct', allow_abbrev=False)
    command.add_argument('--run-dir', type=Path, required=True)
    command.add_argument('--execution', required=True)
    command = sub.add_parser('aggregate-record-gates', allow_abbrev=False)
    command.add_argument('--run-dir', type=Path, required=True)
    command.add_argument('--candidate', type=Path, required=True)
    for action in ('uat-readiness', 'uat-recover-cleanup', 'admit-phase', 'admit-delivery', 'qualify', 'attest-delivery', 'submit-delivery', 'reconcile-delivery', 'phase-exit'):
        command = sub.add_parser(action, allow_abbrev=False)
        command.add_argument('--run-dir', type=Path, required=True)
        if action != 'uat-recover-cleanup':
            command.add_argument('--execution', required=True)
        if action == 'qualify':
            command.add_argument('--stage', choices=('promoted', 'aggregate'), default='promoted')
        if action in {'uat-readiness', 'uat-recover-cleanup', 'admit-phase', 'qualify', 'attest-delivery', 'phase-exit'}:
            command.add_argument('--candidate', type=Path, required=True)
    for action in ('admit-inputs', 'refinement-ready', 'settle-objective', 'complete', 'cancel-complete', 'retire-fenced-waves', 'resume-replanned', 'aggregate-prepare', 'aggregate-validate', 'aggregate-finish'):
        command = sub.add_parser(action, allow_abbrev=False)
        command.add_argument('--run-dir', type=Path, required=True)
        if action == 'settle-objective':
            command.add_argument('--claim', type=Path, required=True)
    args = parser.parse_args(argv)
    authorized_run = None
    try:
        home = agent_home()
        session = native_session_id()
        if hasattr(args, 'run_dir'):
            authorize_coordinator(args.run_dir, session, owner=args.owner)
            authorized_run = args.run_dir
        if args.action == 'status':
            from workflow_automatic import status
            value = status(args.project_root.absolute(), home)
        elif args.action == 'aggregate-correct':
            from workflow_automatic import begin_correction
            value = begin_correction(args.run_dir, session, args.execution)
        elif args.action == 'aggregate-record-gates':
            from workflow_aggregation_gates import record
            value = record(args.run_dir, session, read(args.candidate))
        elif args.action == 'uat-readiness':
            from workflow_uat import declare_readiness
            value = declare_readiness(args.run_dir, read(args.candidate))
        elif args.action == 'uat-recover-cleanup':
            from workflow_qualification import recover_cleanup
            value = recover_cleanup(args.run_dir, read(args.candidate))
        elif args.action == 'admit-phase':
            from workflow_delivery_admission import admit
            value = admit(args.run_dir, args.execution, checks=read(args.candidate)['checks'])
        elif args.action == 'admit-delivery':
            from workflow_delivery_admission import admit
            value = admit(args.run_dir, args.execution)
        elif args.action == 'qualify':
            from workflow_qualification import qualify
            value = qualify(args.run_dir, args.execution, read(args.candidate), stage=args.stage)
        elif args.action in {'attest-delivery', 'submit-delivery', 'reconcile-delivery', 'phase-exit'}:
            import workflow_delivery
            if args.action == 'attest-delivery':
                candidate = read(args.candidate)
                value = workflow_delivery.attest(args.run_dir, args.execution, candidate['review'], candidate['admission'])
            elif args.action == 'phase-exit':
                value = workflow_delivery.phase_exit(args.run_dir, args.execution, read(args.candidate)['admissions'])
            else:
                value = getattr(workflow_delivery, 'submit' if args.action == 'submit-delivery' else 'reconcile')(args.run_dir, args.execution)
        elif args.action == 'admit-inputs':
            from workflow_program import admit_inputs
            value = admit_inputs(args.run_dir)
        elif args.action == 'admit-program':
            from workflow_program import admit
            value = admit(args.run_dir, read(args.candidate))
        elif args.action.startswith('aggregate-'):
            from workflow_automatic import prepare_aggregation, validate_aggregation, finish_aggregation
            action = {'aggregate-prepare': prepare_aggregation, 'aggregate-validate': validate_aggregation,
                      'aggregate-finish': finish_aggregation}[args.action]
            value = action(args.run_dir, session)
        elif args.action == 'complete':
            value = complete_run(args.run_dir, session)
        elif args.action in {'cancel-complete', 'retire-fenced-waves', 'resume-replanned'}:
            import workflow_reconcile
            value = getattr(workflow_reconcile, args.action.replace('-', '_'))(args.run_dir, session)
        elif args.action in {'refinement-ready', 'settle-objective'}:
            from workflow_refinement import ready, settle
            value = ready(args.run_dir) if args.action == 'refinement-ready' else settle(args.run_dir, read(args.claim))
        elif args.action == 'accept':
            value = accept(args.project_root, home, args.owner, session, read(args.objective), revise=args.revise,
                           input_dependencies=read(args.input_dependencies) if args.input_dependencies else None)
        elif args.action == 'continue':
            value = continue_run(args.project_root, home, args.owner, session, takeover=args.takeover,
                                 input_dependencies=read(args.input_dependencies) if args.input_dependencies else None)
        elif args.action == 'fence':
            value = fence(args.run_dir, session, args.reason)
        else:
            binding = observe_binding(home, args.owner, session, args.project_root)
            value = {'status': 'UNBOUND'} if binding is None else result(*binding, 'BOUND')
    except (WorkflowError, OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        value = {'status': 'blocked', 'code': getattr(error, 'code', 'STATE_INVALID'), 'error': str(error)}
        if authorized_run is not None:
            from workflow_delivery_guidance import attach
            value = attach(authorized_run, value, [str(error)])
        print(json.dumps(value))
        return 2
    # Aggregation observes its own finally boundary, including partial effects.
    # A second CLI observation repeats a full snapshot even when no event is added.
    observed_by_owner = {'aggregate-prepare', 'aggregate-validate', 'aggregate-finish', 'aggregate-correct'}
    if (args.owner in {'agentic-sdlc', 'implement'} and hasattr(args, 'run_dir')
            and args.action not in {'status', 'observe'} | observed_by_owner):
        try:
            from workflow_statistics import safe_record
            safe_record(args.run_dir, args.action)
        except Exception:
            pass  # Preserve the successful product response.
    if authorized_run is not None:
        from workflow_delivery_guidance import attach
        value = attach(authorized_run, value, value.get('reasons', ()))
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
