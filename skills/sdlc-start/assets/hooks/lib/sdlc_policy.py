#!/usr/bin/env python3
"""Policy helpers for Agentic SDLC Codex hooks."""

from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sdlc_state import (
    ActiveRun,
    CODEX_HOME,
    SDLC_RUNS,
    CODEX_TASK_STATE,
    detect_current_branch,
    detect_default_branch,
    is_inside,
    resolve_path,
    staged_diff,
    staged_files,
)


DEFAULT_BRANCHES = {"main", "master", "trunk", "develop", "default"}
WRITE_TOOL_KEYWORDS = ("write", "create", "update", "delete", "remove", "move", "rename", "patch", "edit")
READ_TOOL_KEYWORDS = ("read", "get", "list", "search", "status", "view", "inspect")
READ_ONLY_SHELL_COMMANDS = {
    "cat",
    "cmp",
    "diff",
    "file",
    "grep",
    "head",
    "less",
    "ls",
    "md5sum",
    "more",
    "rg",
    "sed",
    "shasum",
    "sha1sum",
    "sha256sum",
    "stat",
    "tail",
    "wc",
}
PRIVATE_STATE_PARTS = {
    ".agent-state",
    "evidence",
    "history",
    "screenshots",
    "transcripts",
}
PRIVATE_STATE_FILES = {
    "continuation-state.json",
    "hook-events.jsonl",
}
CREDENTIAL_DIRS = {
    ".ssh",
    ".aws",
    ".config",
    ".kube",
    ".gnupg",
    ".gpg",
    ".docker",
    ".azure",
}


def allow() -> dict[str, Any]:
    return {}


def deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def warn_context(message: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        }
    }


def stop(reason: str) -> dict[str, Any]:
    return {"continue": False, "stopReason": reason}


def continue_with(reason: str) -> dict[str, Any]:
    return {"decision": "block", "reason": reason}


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return str(value)


def contains_secret(text: str) -> bool:
    if not text:
        return False
    placeholder_markers = (
        "example",
        "dummy",
        "placeholder",
        "redacted",
        "<token>",
        "<secret>",
        "<password>",
        "changeme",
        "not-a-secret",
    )
    secret_patterns = [
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\bAWS_ACCESS_KEY_ID\b\s*[:=]\s*[A-Z0-9]{16,}",
        r"\bAWS_SECRET_ACCESS_KEY\b\s*[:=]\s*[A-Za-z0-9/+=]{30,}",
        r"\bGITHUB_TOKEN\b\s*[:=]\s*[A-Za-z0-9_ghopsu-]{20,}",
        r"\bOPENAI_API_KEY\b\s*[:=]\s*sk-[A-Za-z0-9_-]{16,}",
        r"\bNEBIUS_[A-Z0-9_]*\b\s*[:=]\s*[A-Za-z0-9_./+=:-]{12,}",
        r"\bYC_TOKEN\b\s*[:=]\s*[A-Za-z0-9_./+=:-]{12,}",
        r"\bKUBECONFIG\b.*(certificate-authority-data|client-key-data|token:)",
        r"(?i)\b(password|secret|token)\b\s*[:=]\s*[\"']?[A-Za-z0-9_./+=:-]{12,}",
    ]
    for line in text.splitlines() or [text]:
        lowered = line.lower()
        if any(marker in lowered for marker in placeholder_markers):
            continue
        if any(re.search(pattern, line) for pattern in secret_patterns):
            return True
    return False


def dangerous_shell_reason(command: str) -> str | None:
    normalized = re.sub(r"\s+", " ", command.strip())
    patterns = [
        (r"\b(curl|wget)\b.+\|\s*(sh|bash|zsh)\b", "Blocked: piping downloaded content into a shell."),
        (r"(^|[;&|]\s*)sudo\b", "Blocked: sudo is outside the SDLC hook policy."),
        (r"\bchmod\s+-R\s+777\b", "Blocked: recursive chmod 777 is unsafe."),
        (r"\bchown\s+-R\b", "Blocked: recursive chown is unsafe."),
        (r"\brm\s+-[^\n;]*r[^\n;]*f[^\n;]*(?:/\s*$|/\s|~(?:/|\s|$)|\.\.(?:/|\s|$))", "Blocked: destructive recursive removal outside a known temp path."),
        (r"\bfind\s+/\s+.*-delete\b", "Blocked: deleting from filesystem root is unsafe."),
        (r"\bdd\s+if=", "Blocked: dd is outside the SDLC hook policy."),
        (r"\bmkfs(\.|\s|$)", "Blocked: filesystem formatting is unsafe."),
        (r"\bdiskutil\s+erase", "Blocked: disk erase is unsafe."),
        (r"\bkillall\b", "Blocked: killall is outside the SDLC hook policy."),
        (r"\bpkill\s+-9\b", "Blocked: pkill -9 is outside the SDLC hook policy."),
    ]
    for pattern, reason in patterns:
        if re.search(pattern, normalized, re.IGNORECASE):
            return reason
    return None


def extract_apply_patch_targets(command: str, cwd: Path) -> list[Path]:
    targets: list[Path] = []
    for line in command.splitlines():
        match = re.match(r"\*\*\* (?:Add|Update|Delete) File: (.+)$", line.strip())
        if match:
            targets.append(resolve_path(match.group(1).strip(), cwd))
        move_match = re.match(r"\*\*\* Move to: (.+)$", line.strip())
        if move_match:
            targets.append(resolve_path(move_match.group(1).strip(), cwd))
    return targets


def patch_deletes_codex_global_agents(command: str, cwd: Path) -> bool:
    for line in command.splitlines():
        match = re.match(r"\*\*\* Delete File: (.+)$", line.strip())
        if match and is_codex_global_agents_path(resolve_path(match.group(1).strip(), cwd)):
            return True
    return False


def patch_moves_codex_global_agents(command: str, cwd: Path) -> bool:
    source: Path | None = None
    for line in command.splitlines():
        stripped = line.strip()
        update_match = re.match(r"\*\*\* Update File: (.+)$", stripped)
        if update_match:
            source = resolve_path(update_match.group(1).strip(), cwd)
            continue
        if re.match(r"\*\*\* (?:Add|Delete) File: .+$", stripped):
            source = None
            continue
        move_match = re.match(r"\*\*\* Move to: (.+)$", stripped)
        if not move_match:
            continue
        destination = resolve_path(move_match.group(1).strip(), cwd)
        if is_codex_global_agents_path(destination):
            return True
        if source and is_codex_global_agents_path(source):
            return True
    return False


def command_references_codex_global_agents(command: str, cwd: Path) -> bool:
    agents_path = resolve_path(CODEX_HOME / "AGENTS.md")
    candidates = {
        str(agents_path),
        str(CODEX_HOME / "AGENTS.md"),
        "$CODEX_HOME/AGENTS.md",
        "${CODEX_HOME}/AGENTS.md",
        "${CODEX_HOME:-$HOME/.codex}/AGENTS.md",
        "$HOME/.codex/AGENTS.md",
        "${HOME}/.codex/AGENTS.md",
        "~/.codex/AGENTS.md",
    }
    if any(candidate and candidate in command for candidate in candidates):
        return True
    for token in command_words(command):
        cleaned = token.strip("\"'(),;:")
        if is_codex_global_agents_path(resolve_path(cleaned, cwd)):
            return True
    return False


def is_simple_read_only_shell(command: str) -> bool:
    words = command_words(command)
    if not words:
        return False
    first = Path(words[0]).name
    if first not in READ_ONLY_SHELL_COMMANDS:
        return False
    if any(marker in command for marker in (">", ">>", "|", ";", "&&", "||", "`", "$(")):
        return False
    if first in {"sed", "awk", "perl"} and any(word == "-i" or word.startswith("-i") for word in words[1:]):
        return False
    return True


def extract_obvious_command_paths(command: str, cwd: Path) -> list[Path]:
    paths: list[Path] = []
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in {"-m", "--message", "-c", "--config"}:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        if token.startswith(("~", "/", "./", "../")) or "/" in token:
            paths.append(resolve_path(token, cwd))
    return paths


def extract_mcp_paths(args: Any, cwd: Path) -> list[Path]:
    paths: list[Path] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"path", "file", "filepath", "filename", "dest", "destination", "target"} and isinstance(item, str):
                    paths.append(resolve_path(item, cwd))
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(args)
    return paths


def is_temp_path(path: Path) -> bool:
    resolved = resolve_path(path)
    temp_roots = [Path("/tmp"), Path("/private/tmp"), Path("/var/tmp")]
    return any(is_inside(resolved, root) for root in temp_roots)


def is_credential_path(path: Path) -> bool:
    resolved = resolve_path(path)
    home = resolve_path(Path.home())
    if not is_inside(resolved, home):
        return False
    rel_parts = resolved.relative_to(home).parts
    return bool(rel_parts and rel_parts[0] in CREDENTIAL_DIRS)


def is_codex_global_agents_path(path: Path) -> bool:
    return resolve_path(path) == resolve_path(CODEX_HOME / "AGENTS.md")


def is_sdlc_private_path(path: Path, active: ActiveRun | None = None) -> bool:
    resolved = resolve_path(path)
    home_sdlc = resolve_path(Path.home() / ".codex" / "sdlc-runs")
    if is_inside(resolved, home_sdlc):
        return True
    if active and is_inside(resolved, active.run_dir):
        return True
    parts = set(resolved.parts)
    if ".agent-state" in parts:
        return True
    if any(part in PRIVATE_STATE_PARTS for part in resolved.parts):
        return True
    if resolved.name in PRIVATE_STATE_FILES or resolved.name.endswith(".lock"):
        return True
    return False


def is_plan_locked(path: Path, active: ActiveRun | None = None) -> bool:
    resolved = resolve_path(path)
    if resolved.name.endswith(".lock"):
        return True
    if not re.match(r"FEAT-\d+\.plan\.v\d+\.md$", resolved.name):
        return False
    lock = resolved.with_suffix(resolved.suffix + ".lock")
    if lock.exists():
        return True
    if active:
        active_lock = active.plans_dir / (resolved.name + ".lock")
        if active_lock.exists():
            return True
    return False


def validate_write_targets(
    paths: list[Path],
    project_root: Path,
    active: ActiveRun | None,
    *,
    allow_global_agents: bool = False,
) -> str | None:
    for path in paths:
        if is_credential_path(path):
            return f"Blocked: writing credential path {path}."
        if is_plan_locked(path, active):
            return f"Blocked: locked SDLC plan cannot be edited or deleted: {path}."
        allowed = (
            is_inside(path, project_root)
            or is_inside(path, SDLC_RUNS)
            or is_inside(path, CODEX_TASK_STATE)
            or is_temp_path(path)
            or (allow_global_agents and is_codex_global_agents_path(path))
        )
        if active:
            allowed = allowed or is_inside(path, active.run_dir) or is_inside(path, active.project_dir)
        if not allowed:
            return f"Blocked: write target is outside the project root, SDLC state, global task state, or temp directory: {path}."
    return None


def command_has_private_state_leak(command: str) -> bool:
    patterns = [
        r"\bgit\s+add\b.*(\.codex/sdlc-runs|~/.codex/sdlc-runs|\.agent-state|evidence|screenshots|transcripts)",
        r"\bcp\s+(-[A-Za-z]*R[A-Za-z]*\s+|\s+).*(\.codex/sdlc-runs|~/.codex/sdlc-runs|\.agent-state).*\s+\.",
        r"\brsync\b.*(\.codex/sdlc-runs|~/.codex/sdlc-runs|\.agent-state).*\s+\.",
    ]
    return any(re.search(pattern, command) for pattern in patterns)


def staged_private_paths(project_root: Path, active: ActiveRun | None = None) -> list[str]:
    bad: list[str] = []
    for name in staged_files(project_root):
        path = resolve_path(project_root / name)
        if is_sdlc_private_path(path, active):
            bad.append(name)
    return bad


def auth_valid(active: ActiveRun | None, filename: str, expected_branch: str | None = None) -> tuple[bool, str]:
    if not active:
        return False, "no active SDLC run"
    auth_path = active.permissions_dir / filename
    try:
        with auth_path.open("r", encoding="utf-8") as handle:
            auth = json.load(handle)
    except FileNotFoundError:
        return False, f"missing {filename}"
    except json.JSONDecodeError:
        return False, f"corrupt {filename}"
    if not auth.get("allowed"):
        return False, f"{filename} does not allow this action"
    expires_at = auth.get("expires_at")
    if expires_at:
        try:
            parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if parsed < datetime.now(timezone.utc):
                return False, f"{filename} expired"
        except ValueError:
            return False, f"{filename} has invalid expires_at"
    if expected_branch and auth.get("branch") and auth.get("branch") != expected_branch:
        return False, f"{filename} is for branch {auth.get('branch')}, not {expected_branch}"
    return True, "authorized"


def command_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def is_git_command(words: list[str], subcommand: str) -> bool:
    return len(words) >= 2 and words[0] == "git" and words[1] == subcommand


def is_gh_pr_merge(words: list[str]) -> bool:
    return len(words) >= 3 and words[0] == "gh" and words[1] == "pr" and words[2] == "merge"


def git_policy_reason(command: str, project_root: Path, active: ActiveRun | None) -> str | None:
    words = command_words(command)
    if not words:
        return None
    branch = detect_current_branch(project_root)
    default_branch = detect_default_branch(project_root)
    protected = DEFAULT_BRANCHES | {default_branch}
    if is_git_command(words, "commit"):
        if branch in protected:
            return f"Blocked: git commit on protected branch {branch or '<detached>'}."
        if active:
            ok, reason = auth_valid(active, "commit-authorization.json", branch)
            if not ok:
                return f"Blocked: git commit requires valid SDLC commit authorization: {reason}."
            private = staged_private_paths(project_root, active)
            if private:
                return "Blocked: staged files include private SDLC state: " + ", ".join(private[:5])
            diff = staged_diff(project_root)
            if contains_secret(diff):
                return "Blocked: staged diff appears to contain a secret."
        return None
    if is_git_command(words, "push"):
        if any(word in {"-f", "--force", "--force-with-lease"} or word.startswith("--force") for word in words):
            return "Blocked: force push is outside the SDLC hook policy."
        if branch in protected:
            return f"Blocked: git push from protected branch {branch or '<detached>'}."
        if active:
            ok, reason = auth_valid(active, "pr-authorization.json", branch)
            if not ok:
                return f"Blocked: git push requires valid SDLC PR authorization: {reason}."
        return None
    if is_git_command(words, "reset") and "--hard" in words:
        return "Blocked: git reset --hard is destructive."
    if is_git_command(words, "clean") and any(re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*d[A-Za-z]*x[A-Za-z]*", word) for word in words[2:]):
        return "Blocked: git clean -fdx is destructive."
    if is_git_command(words, "rebase") and branch in protected:
        return f"Blocked: rebase on protected branch {branch}."
    if is_git_command(words, "merge") and branch in protected:
        return f"Blocked: merge into protected branch {branch}."
    if is_git_command(words, "branch") and any(word in {"-D", "--delete", "-d"} for word in words):
        return "Blocked: branch deletion is outside the SDLC hook policy."
    if is_git_command(words, "push") and "--tags" in words:
        return "Blocked: pushing tags requires explicit authorization."
    if is_gh_pr_merge(words):
        ok, reason = auth_valid(active, "merge-authorization.json", branch)
        if not ok:
            return f"Blocked: PR merge requires valid SDLC merge authorization: {reason}."
    return None


def mcp_policy_reason(tool_name: str, tool_input: Any, cwd: Path, project_root: Path, active: ActiveRun | None) -> str | None:
    lower = tool_name.lower()
    payload = _json_text(tool_input)
    if contains_secret(payload):
        return "Blocked: MCP arguments appear to contain a secret."
    if "github" in lower and "merge" in lower:
        ok, reason = auth_valid(active, "merge-authorization.json", detect_current_branch(project_root))
        if not ok:
            return f"Blocked: GitHub merge requires valid SDLC merge authorization: {reason}."
    if "github" in lower and ("create_pull_request" in lower or "create_pr" in lower or "pull_request" in lower):
        ok, reason = auth_valid(active, "pr-authorization.json", detect_current_branch(project_root))
        if active and not ok:
            return f"Blocked: GitHub PR creation requires valid SDLC PR authorization: {reason}."
    if ("slack" in lower or "confluence" in lower) and any(word in lower for word in WRITE_TOOL_KEYWORDS):
        if not active:
            return "Blocked: external write MCP call requires active SDLC authorization."
    if any(word in lower for word in WRITE_TOOL_KEYWORDS):
        paths = extract_mcp_paths(tool_input, cwd)
        target_reason = validate_write_targets(paths, project_root, active)
        if target_reason:
            return target_reason
    return None


def spec_warning_or_denial(command: str, paths: list[Path], project_root: Path, current_state: dict[str, Any]) -> dict[str, Any] | None:
    spec_paths = {project_root / "docs" / "requirements.md": "requirements_update", project_root / "docs" / "design.md": "design_update"}
    touched = [path for path in paths if any(resolve_path(path) == resolve_path(spec) for spec in spec_paths)]
    if not touched:
        return None
    if re.search(r"^-.*\b(REQ|FEAT)-\d+\b", command, re.MULTILINE) and "CHANGELOG" not in command and "Change Log" not in command:
        return deny("Blocked: spec edit appears to delete REQ/FEAT IDs without a changelog entry.")
    phase = str(current_state.get("current_phase") or "")
    for path in touched:
        expected = spec_paths[resolve_path(path)]
        if phase and phase != expected:
            return warn_context(f"SDLC warning: {path.name} is normally updated only during {expected}; current phase is {phase}.")
    return None
