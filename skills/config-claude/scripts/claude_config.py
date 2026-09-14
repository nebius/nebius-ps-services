"""Data-only inspection of the config-claude convergence contract."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat


SKILL = Path(__file__).resolve().parents[1]
BEGIN = "<!-- BEGIN config-claude managed context -->"
END = "<!-- END config-claude managed context -->"
ROLES = ("repo-mapper", "test-strategist", "risk-reviewer")
OWNERS = (
    "agent-nebius-auth-setup", "commit", "config-codex", "maintain-project-specs",
    "prompt-session-intake", "sdlc-start", "troubleshoot",
)
MCP_NAMES = (
    "context7", "playwright", "terraform", "markitdown", "microsoftdocs",
    "github", "openaiDeveloperDocs",
)
PLUGIN = "skills@nebius-ps-services"
MAX_BYTES = 2 * 1024 * 1024


def native_home(value: Path | None = None) -> Path:
    path = value or Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    path = path.expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Claude home must be absolute without parent traversal")
    return path


def safe_path(path: Path) -> None:
    """Reject symlink components before inspecting user-selected paths."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("path must be absolute without parent traversal")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("symlink paths are unsupported")


def safe_read(path: Path, *, private: bool = False) -> bytes:
    safe_path(path)
    before = path.lstat()
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o022
                or (private and stat.S_IMODE(info.st_mode) != 0o600)
                or info.st_size > MAX_BYTES
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError("unsafe file type, ownership, size or permissions")
        data = bytearray()
        while len(data) <= MAX_BYTES:
            chunk = os.read(descriptor, min(65_536, MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        rebound = path.lstat()
        if (len(data) > MAX_BYTES
                or (info.st_mtime_ns, info.st_ctime_ns, info.st_size)
                != (after.st_mtime_ns, after.st_ctime_ns, after.st_size)
                or (rebound.st_dev, rebound.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError("file changed during inspection")
        return bytes(data)
    finally:
        os.close(descriptor)


def object_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def json_object(data: bytes) -> dict:
    def invalid_constant(_value: str):
        raise ValueError("non-finite JSON number")
    value = json.loads(data, object_pairs_hook=object_pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def managed_block(text: str) -> str:
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise ValueError("one managed instruction block is required")
    start = text.index(BEGIN) + len(BEGIN)
    end = text.index(END)
    if end < start:
        raise ValueError("managed markers are out of order")
    return text[start:end]


def normalized(text: str) -> str:
    return " ".join(text.split())


def check_instructions(data: bytes) -> None:
    text = data.decode("utf-8")
    template = safe_read(SKILL / "assets/CLAUDE.md.template").decode("utf-8")
    if normalized(managed_block(text)) != normalized(managed_block(template)):
        raise ValueError("managed guidance is missing or stale")
    # User sections are never rewritten to repair duplicate policy headings.
    outside = text.split(BEGIN)[0] + text.split(END)[1]
    visible = re.sub(r"(?ms)^(```|~~~).*?^\1[^\n]*$", "", outside)
    if re.search(r"(?im)^\s{0,3}#{1,6}\s+Live Product Validation\s*#*\s*$", visible):
        raise ValueError("conflicting user-owned live validation heading")
    if re.search(r"(?im)^Live Product Validation\s*\n[=-]+\s*$", visible):
        raise ValueError("conflicting user-owned live validation heading")


def check_role(data: bytes, name: str) -> None:
    expected = safe_read(SKILL / f"assets/agents/{name}.md.template").decode("utf-8")
    lines, target = data.decode("utf-8").splitlines(), expected.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("role requires exact frontmatter delimiter lines")
    end, target_end = lines.index("---", 1), target.index("---", 1)
    if (not "\n".join(lines[end + 1:]).strip()
            or "\n".join(lines[1:end]).strip() != "\n".join(target[1:target_end]).strip()):
        raise ValueError("role differs from the reviewed native tool profile")


def private_tree(path: Path) -> None:
    safe_path(path)
    pending = [path]
    count = 0
    while pending:
        item = pending.pop()
        count += 1
        if count > 10_000:
            raise ValueError("private tree inspection limit reached")
        info = item.lstat()
        directory = stat.S_ISDIR(info.st_mode)
        if item == path and not directory:
            raise ValueError("private state root must be a directory")
        if (info.st_uid != os.getuid() or stat.S_ISLNK(info.st_mode)
                or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))
                or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)):
            raise ValueError("unsafe private state tree")
        if directory:
            pending.extend(item.iterdir())


def source_bundle(root: Path) -> tuple[dict[str, bytes], dict[str, list]]:
    """Read reviewed source as data; never import a selected source module."""
    safe_path(root)
    payloads: dict[str, bytes] = {}
    hooks: dict[str, list] = {}
    for owner in OWNERS:
        source = root / owner / "assets/hooks"
        safe_path(source)
        try:
            manifest = json_object(safe_read(source.parent / "hooks.json.template"))
        except FileNotFoundError as exc:
            raise ValueError(f"missing reviewed hook dependency: {owner}") from exc
        if not isinstance(manifest.get("hooks"), dict):
            raise ValueError("invalid owner hook manifest")
        for event, entries in manifest["hooks"].items():
            if not isinstance(entries, list):
                raise ValueError("invalid owner hook entries")
            for entry in entries:
                if entry not in hooks.setdefault(event, []):
                    hooks[event].append(entry)
        paths = list(source.rglob("*"))
        if len(paths) > 1000:
            raise ValueError("source bundle inspection limit reached")
        for path in paths:
            relative = path.relative_to(source)
            if any(part in {"tests", "__pycache__"} or part.startswith(".") for part in relative.parts):
                continue
            safe_path(path)
            if path.is_dir() or path.name in {"README.md", "hooks.json", "hooks.json.template"}:
                continue
            name = str(relative).removesuffix(".template")
            content = safe_read(path)
            if name in payloads and payloads[name] != content:
                raise ValueError("conflicting source payload")
            payloads[name] = content
    for name in ("agent_runtime.py", "hook_runtime.py", "trusted_runtime.py", "task_state_permissions.py"):
        payloads[name] = safe_read(root / "global-context-management/scripts" / name)
    if len(hooks.get("Stop", [])) != 1:
        raise ValueError("source must have one Stop arbiter")
    return payloads, hooks


def projected(hooks: dict, home: Path, *, plugin: bool = False) -> dict:
    result = json.loads(json.dumps(hooks))
    for entries in result.values():
        for entry in entries:
            for handler in entry["hooks"]:
                command = shlex.split(handler["command"])
                if command and command[0] == "SKILLS_AGENT=codex":
                    command = command[1:]
                if len(command) != 2 or command[0] != "python3":
                    raise ValueError("unrecognized source hook command")
                name = Path(command[1]).name
                prefix = ('python3 "${CLAUDE_PLUGIN_ROOT}/global-context-management/scripts/hook_runtime.py"'
                          if plugin else f"python3 {shlex.quote(str(home / 'hooks/hook_runtime.py'))}")
                handler["command"] = f"{prefix} --agent claude --hook {name}"
                if plugin:
                    handler["command"] += ' --plugin-root "${CLAUDE_PLUGIN_ROOT}"'
                handler.pop("statusMessage", None)
    return result


def local_handlers(settings: dict, payloads: dict) -> dict[str, list]:
    """Validate shapes and identify managed scripts even in custom commands."""
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    seen: set[tuple[str, str]] = set()
    found: dict[str, list] = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            raise ValueError("hook entries must be lists")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                raise ValueError("invalid hook entry")
            for handler in entry["hooks"]:
                if not isinstance(handler, dict):
                    raise ValueError("invalid hook handler")
                if handler.get("type") != "command":
                    continue
                if not isinstance(handler.get("command"), str):
                    raise ValueError("invalid command hook")
                names = set(re.findall(r"([A-Za-z0-9_-]+\.py)", handler["command"])) & payloads.keys()
                names.discard("hook_runtime.py")
                for name in names:
                    key = (event, name)
                    if key in seen:
                        raise ValueError("duplicate managed hook registration")
                    seen.add(key)
                    found.setdefault(event, []).append(entry)
    return found


def check_hooks(home: Path, source: Path, settings: dict, route: str) -> None:
    payloads, hooks = source_bundle(source)
    found = local_handlers(settings, payloads)
    if settings.get("disableAllHooks") is True or settings.get("allowManagedHooksOnly") is True:
        raise ValueError("user hooks are disabled or restricted")
    plugins = settings.get("enabledPlugins", {})
    if not isinstance(plugins, dict):
        raise ValueError("enabledPlugins must be an object")
    plugin_enabled = plugins.get(PLUGIN) is True
    if route == "plugin":
        if found or not plugin_enabled:
            raise ValueError("plugin ownership is inactive or conflicts with local hooks")
        manifest = json_object(safe_read(source / ".claude-plugin/plugin.json"))
        if manifest.get("hooks") != projected(hooks, home, plugin=True):
            raise ValueError("plugin hooks differ from source owners")
        return
    if plugin_enabled:
        raise ValueError("plugin and local hook routes must not overlap")
    expected = projected(hooks, home)
    for event, entries in expected.items():
        for entry in entries:
            if entry not in found.get(event, []):
                raise ValueError("managed hook registration is missing or differs")
    expected_count = sum(len(entries) for entries in expected.values())
    if sum(len(entries) for entries in found.values()) != expected_count:
        raise ValueError("unexpected managed hook registration")
    for name, data in payloads.items():
        if name == "global_context_policy.json":
            json_object(safe_read(home / "hooks" / name))
            continue
        if safe_read(home / "hooks" / name) != data:
            raise ValueError("installed hook payload differs")


def inspect(home: Path, source: Path, *, route: str = "local", native_only: bool = False,
            trusted: bool = False, delegation: bool = False, workspace: bool = False,
            mcp_names: tuple[str, ...] = (), mcp_config: Path | None = None) -> dict:
    checks: list[dict] = []
    fingerprints: dict[str, str] = {}

    def check(surface: str, operation) -> object:
        try:
            value = operation()
            checks.append({"surface": surface, "state": "Aligned"})
            return value
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            # Do not echo decoder errors, paths, keys, commands or user values.
            reason = str(exc) if type(exc) is ValueError else "missing, malformed, unsafe or concurrently changed data"
            checks.append({"surface": surface, "state": "Not aligned", "reason": reason})
            return None

    def read(relative: str) -> bytes:
        data = safe_read(home / relative)
        fingerprints[relative] = hashlib.sha256(data).hexdigest()
        return data

    settings = check("settings JSON", lambda: json_object(read("settings.json")))
    check("managed instructions", lambda: check_instructions(read("CLAUDE.md")))
    for name in ROLES:
        check(f"role {name}", lambda n=name: check_role(read(f"agents/{n}.md"), n))
    check("private task state", lambda: private_tree(home / "task-state"))
    if settings is not None and not native_only:
        check(f"{route} hook ownership and source parity", lambda: check_hooks(home, source, settings, route))
    if trusted:
        def trusted_profile():
            if (not isinstance(settings, dict)
                    or not isinstance(settings.get("permissions"), dict)
                    or not isinstance(settings.get("sandbox"), dict)
                    or settings["permissions"].get("defaultMode") != "bypassPermissions"
                    or settings["permissions"].get("disableBypassPermissionsMode") == "disable"
                    or settings["sandbox"].get("enabled") is not False):
                raise ValueError("trusted-local profile differs")
        check("requested trusted-local profile", trusted_profile)
    if delegation:
        def delegation_policy():
            policy = json_object(read("hooks/global_context_policy.json"))
            if policy.get("auto_read_only_subagents") is not True:
                raise ValueError("delegation policy is disabled")
        check("requested delegation policy", delegation_policy)
    if workspace:
        def task_workspace():
            private_tree(home / "task-implementer")
            # Git metadata can live in any ancestor of the private workspace.
            if any((part / ".git").exists() for part in (home, *home.parents)):
                raise ValueError("private workspace is inside a Git worktree")
            if (home / "task-implementer/.git").exists():
                raise ValueError("private workspace contains Git metadata")
        check("requested private Task Implementer workspace", task_workspace)
    if mcp_names:
        def selected_mcp():
            if mcp_config is None:
                raise ValueError("explicit native MCP configuration path is required")
            servers = json_object(safe_read(mcp_config)).get("mcpServers", {})
            if not isinstance(servers, dict):
                raise ValueError("invalid MCP server collection")
            for name in mcp_names:
                spec = servers.get(name)
                if not isinstance(spec, dict):
                    raise ValueError("requested MCP integration is missing")
                kind = spec.get("type", "stdio")
                if kind == "stdio":
                    if not isinstance(spec.get("command"), str) or not spec["command"]:
                        raise ValueError("invalid stdio MCP configuration")
                elif kind in {"http", "sse", "streamable-http"}:
                    if not isinstance(spec.get("url"), str) or not spec["url"]:
                        raise ValueError("invalid HTTP MCP configuration")
                else:
                    raise ValueError("unsupported MCP transport")
        check("requested MCP registrations (presence and shape only)", selected_mcp)
    success = all(item["state"] == "Aligned" for item in checks)
    return {"status": "STATIC_PASS" if success else "NOT_ALIGNED", "checks": checks,
            "fingerprints": fingerprints,
            "scope": "native-only" if native_only else route,
            "runtime": "NOT_RUN", "quality": "NOT_RUN"}
