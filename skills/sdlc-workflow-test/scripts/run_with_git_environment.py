#!/usr/bin/env python3
"""Launch a native agent with an owned Git profile and minimal environment."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import stat
import tomllib

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
_load_skill_support("git-environment", __file__, "sdlc-workflow-test/scripts/run_with_git_environment.py")
from git_environment import environment  # noqa: E402


NATIVE_VARIABLES = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "COLORTERM", "TMPDIR", "SKILLS_GIT_PROFILE", "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM", "GIT_ATTR_NOSYSTEM", "GIT_TERMINAL_PROMPT",
})


def native_environment(cwd: Path, profile: Path) -> dict[str, str]:
    # Validate before filtering so prohibited Git overrides still fail closed.
    inherited = environment(cwd, profile=profile)
    selected = inherited.get("SKILLS_AGENT", "codex")
    if selected not in {"codex", "claude"}:
        raise ValueError("NATIVE_ENVIRONMENT_INVALID: unsupported agent")
    key = "CODEX_HOME" if selected == "codex" else "CLAUDE_CONFIG_DIR"
    home = profile.parent.parent / "private" / (selected + "-home")
    if (inherited.get(key) != str(home) or home.resolve() != home or not home.is_dir()
            or home.stat().st_uid != os.getuid() or home.stat().st_mode & 0o077):
        raise ValueError("NATIVE_ENVIRONMENT_INVALID: require the exact private native home")
    # Zsh reads ZDOTDIR even for non-login shells. No startup files may be
    # supplied by this fixture; Bash/POSIX initialization is separately disabled.
    if any((home / name).exists() or (home / name).is_symlink()
           for name in (".zshenv", ".zprofile", ".zshrc", ".zlogin")):
        raise ValueError("NATIVE_ENVIRONMENT_INVALID: shell startup files are forbidden")
    if selected == "codex":
        # Git scope and metadata have already been validated by environment().
        # Native project layers end at this checkout's .git, including a linked
        # worktree's file marker; ordinary user configuration above it is separate.
        project_directories = []
        parent = cwd
        while profile.parent.parent in parent.parents:
            project_directories.append(parent)
            if (parent / ".git").exists():
                break
            parent = parent.parent
        else:
            raise ValueError("NATIVE_ENVIRONMENT_INVALID: require a Git checkout root")
        if any((parent / ".codex/config.toml").exists()
               or (parent / ".codex/config.toml").is_symlink()
               for parent in project_directories):
            raise ValueError("NATIVE_ENVIRONMENT_INVALID: project configuration overrides are forbidden")
        config = home / "config.toml"
        try:
            info = config.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 1048576):
                raise ValueError
            settings = tomllib.loads(config.read_text())
            policy = settings.get("shell_environment_policy", {})
            if (settings.get("allow_login_shell") is not False
                    or settings.get("project_root_markers") != [".git"]
                    or "profile" in settings or "profiles" in settings
                    or settings.get("features", {}).get("shell_snapshot") is not False
                    or policy != {"inherit": "all", "ignore_default_excludes": False}):
                raise ValueError
        except (OSError, ValueError, TypeError, AttributeError):
            raise ValueError("NATIVE_ENVIRONMENT_INVALID: require reviewed shell isolation settings") from None
    result = {key: value for key, value in inherited.items() if key in NATIVE_VARIABLES}
    result.update({key: str(home), "SKILLS_AGENT": selected, "ZDOTDIR": str(home),
                   "BASH_ENV": "/dev/null", "ENV": "/dev/null",
                   "PYTHONDONTWRITEBYTECODE": "1"})
    return result


def validate_native_command(command: list[str], selected: str) -> None:
    if Path(command[0]).name != selected:
        raise ValueError("NATIVE_COMMAND_INVALID: require the selected native executable")
    if selected != "codex":
        return
    forbidden = {"--config", "--profile", "--enable", "--disable", "--remote",
                 "--cd", "--add-dir", "--worktree",
                 "--remote-auth-token-env", "--oss", "--local-provider",
                 "--dangerously-bypass-approvals-and-sandbox", "--yolo",
                 "--dangerously-bypass-hook-trust"}
    if any(arg.split("=", 1)[0] in forbidden or arg.startswith(("-c", "-p", "-C"))
           for arg in command[1:]):
        raise ValueError("NATIVE_COMMAND_INVALID: runtime overrides are forbidden")
    option_end = command.index("--") if "--" in command else len(command)
    if "--no-daemon" not in command[1:option_end]:
        raise ValueError("NATIVE_COMMAND_INVALID: require a private native process")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a native command is required")
    try:
        env = native_environment(args.cwd, args.profile)
        validate_native_command(command, env["SKILLS_AGENT"])
        import three_tier_lifecycle as lifecycle
        import verification_catalog
        root = args.profile.parent.parent.parent.parent.parent
        _, state = lifecycle.load_active(root)
        verification_catalog.require_native_installation(state, env["SKILLS_AGENT"])
        # Record the process that exec will preserve; cleanup never signals an unowned PID.
        import verification_workspace as workspace
        workspace.register_native(root)
        os.chdir(args.cwd)
        os.umask(0o077)
        os.execvpe(command[0], command, env)
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
