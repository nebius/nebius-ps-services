#!/usr/bin/env python3
"""Policy helpers for Agentic SDLC Codex hooks."""

from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sdlc_state import (
    ActiveRun,
    detect_current_branch,
    detect_default_branch,
    git_common_dir,
    git_head,
    is_inside,
    resolve_path,
    staged_diff,
    staged_files,
)


DEFAULT_BRANCHES = {"main", "master", "trunk", "develop", "default"}
WRITE_TOOL_KEYWORDS = (
    "write",
    "create",
    "update",
    "delete",
    "remove",
    "move",
    "rename",
    "patch",
    "edit",
)
READ_TOOL_KEYWORDS = ("read", "get", "list", "search", "status", "view", "inspect")
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
        (
            r"\b(curl|wget)\b.+\|\s*(sh|bash|zsh)\b",
            "Blocked: piping downloaded content into a shell.",
        ),
        (r"(^|[;&|]\s*)sudo\b", "Blocked: sudo is outside the SDLC hook policy."),
        (r"\bchmod\s+-R\s+777\b", "Blocked: recursive chmod 777 is unsafe."),
        (r"\bchown\s+-R\b", "Blocked: recursive chown is unsafe."),
        (
            r"\brm\s+-[^\n;]*r[^\n;]*f[^\n;]*(?:/\s*$|/\s|~(?:/|\s|$)|\.\.(?:/|\s|$))",
            "Blocked: destructive recursive removal outside a known temp path.",
        ),
        (
            r"\bfind\s+/\s+.*-delete\b",
            "Blocked: deleting from filesystem root is unsafe.",
        ),
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
                if key.lower() in {
                    "path",
                    "file",
                    "filepath",
                    "filename",
                    "dest",
                    "destination",
                    "target",
                } and isinstance(item, str):
                    paths.append(resolve_path(item, cwd))
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(args)
    return paths


def is_sdlc_private_path(path: Path, active: ActiveRun | None = None) -> bool:
    resolved = resolve_path(path)
    if (
        active
        and active.execution_role in {"integration", "worker"}
        and is_inside(resolved, active.project_root)
    ):
        relative = resolved.relative_to(active.project_root)
        if ".agent-state" in relative.parts:
            return True
        if any(part in PRIVATE_STATE_PARTS for part in relative.parts):
            return True
        return resolved.name in PRIVATE_STATE_FILES or resolved.name.endswith(".lock")
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


def validate_write_targets(
    _paths: list[Path],
    _project_root: Path,
    _active: ActiveRun | None,
    *,
    allow_global_agents: bool = False,  # Kept for backwards-compatible callers.
) -> str | None:
    return None


def staged_private_paths(
    project_root: Path, active: ActiveRun | None = None
) -> list[str]:
    bad: list[str] = []
    for name in staged_files(project_root):
        path = resolve_path(project_root / name)
        if is_sdlc_private_path(path, active):
            bad.append(name)
    return bad


def auth_valid(
    active: ActiveRun | None, filename: str, expected_branch: str | None = None
) -> tuple[bool, str]:
    if not active:
        return False, "no active SDLC run"
    auth_path = active.permissions_dir / filename
    try:
        if auth_path.is_symlink():
            return False, f"{filename} must not be a symlink"
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
            if parsed.tzinfo is None:
                return False, f"{filename} expires_at must include a timezone"
            if parsed < datetime.now(timezone.utc):
                return False, f"{filename} expired"
        except ValueError:
            return False, f"{filename} has invalid expires_at"
    if expected_branch and auth.get("branch") and auth.get("branch") != expected_branch:
        return (
            False,
            f"{filename} is for branch {auth.get('branch')}, not {expected_branch}",
        )
    return True, "authorized"


def execution_auth_valid(
    active: ActiveRun | None, action: str, command: str
) -> tuple[bool, str]:
    if not active:
        return False, "no active SDLC run"
    if not active.execution_identity_valid:
        return (
            False,
            active.execution_identity_reason or "registered Git identity changed",
        )
    auth_dir = active.permissions_dir / "execution"
    if not auth_dir.is_dir():
        return False, "missing action-scoped execution authorization"
    current_branch = detect_current_branch(active.project_root)
    current_head = git_head(active.project_root)
    current_common = git_common_dir(active.project_root)
    for auth_path in sorted(auth_dir.glob("*.json")):
        try:
            if auth_path.is_symlink():
                continue
            with auth_path.open("r", encoding="utf-8") as handle:
                auth = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not auth.get("allowed") or auth.get("action") != action:
            continue
        try:
            parsed = datetime.fromisoformat(
                str(auth.get("expires_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if parsed.tzinfo is None:
            continue
        if parsed < datetime.now(timezone.utc):
            continue
        if resolve_path(str(auth.get("worktree") or "")) != active.project_root:
            continue
        if auth.get("branch") != current_branch:
            continue
        if auth.get("expected_head") != current_head:
            continue
        expected_common = resolve_path(str(auth.get("git_common_dir") or ""))
        if current_common is None or expected_common != current_common:
            continue
        exact_command = auth.get("exact_command")
        target = str(auth.get("target") or "")
        if not exact_command and not target:
            continue
        if exact_command and exact_command != command:
            continue
        if target and target not in command_words(command):
            continue
        return True, f"authorized by {auth_path.name}"
    return (
        False,
        f"no valid {action} authorization matches worktree, branch, HEAD, and target",
    )


def command_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def is_git_command(words: list[str], subcommand: str) -> bool:
    return len(words) >= 2 and words[0] == "git" and words[1] == subcommand


def is_gh_pr_merge(words: list[str]) -> bool:
    return (
        len(words) >= 3
        and words[0] == "gh"
        and words[1] == "pr"
        and words[2] == "merge"
    )


def git_policy_reason(
    command: str, project_root: Path, active: ActiveRun | None
) -> str | None:
    words = command_words(command)
    if not words:
        return None
    branch = detect_current_branch(project_root)
    default_branch = detect_default_branch(project_root)
    protected = DEFAULT_BRANCHES | {default_branch}
    sensitive_execution_git = (
        is_git_command(words, "commit")
        or is_git_command(words, "merge")
        or (
            is_git_command(words, "branch")
            and any(word in {"-D", "--delete", "-d"} for word in words)
        )
        or (len(words) >= 3 and words[:3] == ["git", "worktree", "remove"])
    )
    if (
        active
        and active.execution_role in {"integration", "worker", "unregistered"}
        and sensitive_execution_git
        and not active.execution_identity_valid
    ):
        return "Blocked: registered SDLC worktree identity changed: " + (
            active.execution_identity_reason or "unknown identity mismatch"
        )
    if is_git_command(words, "commit"):
        if branch in protected:
            return f"Blocked: git commit on protected branch {branch or '<detached>'}."
        if active:
            if active.execution_role == "worker":
                ok, reason = execution_auth_valid(active, "worker-commit", command)
            elif active.execution_role == "integration":
                ok, reason = auth_valid(active, "commit-authorization.json", branch)
            else:
                ok, reason = auth_valid(active, "commit-authorization.json", branch)
            if not ok:
                return f"Blocked: git commit requires valid SDLC commit authorization: {reason}."
            private = staged_private_paths(project_root, active)
            if private:
                return "Blocked: staged files include private SDLC state: " + ", ".join(
                    private[:5]
                )
            diff = staged_diff(project_root)
            if contains_secret(diff):
                return "Blocked: staged diff appears to contain a secret."
        return None
    if is_git_command(words, "push"):
        if any(
            word in {"-f", "--force", "--force-with-lease"}
            or word.startswith("--force")
            for word in words
        ):
            return "Blocked: force push is outside the SDLC hook policy."
        if branch in protected:
            return f"Blocked: git push from protected branch {branch or '<detached>'}."
        if active:
            ok, reason = auth_valid(active, "pr-authorization.json", branch)
            if not ok:
                return (
                    f"Blocked: git push requires valid SDLC PR authorization: {reason}."
                )
        return None
    if is_git_command(words, "reset") and "--hard" in words:
        return "Blocked: git reset --hard is destructive."
    if is_git_command(words, "clean") and any(
        re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*d[A-Za-z]*x[A-Za-z]*", word)
        for word in words[2:]
    ):
        return "Blocked: git clean -fdx is destructive."
    if is_git_command(words, "rebase") and branch in protected:
        return f"Blocked: rebase on protected branch {branch}."
    if is_git_command(words, "merge"):
        if branch in protected:
            return f"Blocked: merge into protected branch {branch}."
        if active and active.execution_role == "integration":
            ok, reason = execution_auth_valid(active, "integration-merge", command)
            if not ok:
                return f"Blocked: integration merge requires valid execution authorization: {reason}."
        elif active and active.execution_role == "project":
            ok, reason = execution_auth_valid(active, "feature-promotion", command)
            if not ok:
                return f"Blocked: feature promotion requires valid execution authorization: {reason}."
    if is_git_command(words, "branch") and any(
        word in {"-D", "--delete", "-d"} for word in words
    ):
        if "-D" in words:
            return "Blocked: force branch deletion is outside the SDLC hook policy."
        ok, reason = execution_auth_valid(active, "resource-cleanup", command)
        if not ok:
            return f"Blocked: branch deletion requires valid execution cleanup authorization: {reason}."
    if len(words) >= 3 and words[:3] == ["git", "worktree", "remove"]:
        if any(word in {"-f", "--force"} for word in words[3:]):
            return "Blocked: force worktree removal is outside the SDLC hook policy."
        ok, reason = execution_auth_valid(active, "resource-cleanup", command)
        if not ok:
            return f"Blocked: worktree removal requires valid execution cleanup authorization: {reason}."
    if is_git_command(words, "push") and "--tags" in words:
        return "Blocked: pushing tags requires explicit authorization."
    if is_gh_pr_merge(words):
        ok, reason = auth_valid(active, "merge-authorization.json", branch)
        if not ok:
            return (
                f"Blocked: PR merge requires valid SDLC merge authorization: {reason}."
            )
    return None


def mcp_policy_reason(
    tool_name: str,
    tool_input: Any,
    cwd: Path,
    project_root: Path,
    active: ActiveRun | None,
) -> str | None:
    lower = tool_name.lower()
    payload = _json_text(tool_input)
    if contains_secret(payload):
        return "Blocked: MCP arguments appear to contain a secret."
    if "github" in lower and "merge" in lower:
        ok, reason = auth_valid(
            active, "merge-authorization.json", detect_current_branch(project_root)
        )
        if not ok:
            return f"Blocked: GitHub merge requires valid SDLC merge authorization: {reason}."
    if "github" in lower and (
        "create_pull_request" in lower
        or "create_pr" in lower
        or "pull_request" in lower
    ):
        ok, reason = auth_valid(
            active, "pr-authorization.json", detect_current_branch(project_root)
        )
        if active and not ok:
            return f"Blocked: GitHub PR creation requires valid SDLC PR authorization: {reason}."
    if ("slack" in lower or "confluence" in lower) and any(
        word in lower for word in WRITE_TOOL_KEYWORDS
    ):
        if not active:
            return (
                "Blocked: external write MCP call requires active SDLC authorization."
            )
    if any(word in lower for word in WRITE_TOOL_KEYWORDS):
        paths = extract_mcp_paths(tool_input, cwd)
        target_reason = validate_write_targets(paths, project_root, active)
        if target_reason:
            return target_reason
    return None


def spec_warning_or_denial(
    command: str, paths: list[Path], project_root: Path, current_state: dict[str, Any]
) -> dict[str, Any] | None:
    spec_paths = {
        project_root / "docs" / "requirements.md": "requirements_update",
        project_root / "docs" / "design.md": "design_update",
    }
    touched = [
        path
        for path in paths
        if any(resolve_path(path) == resolve_path(spec) for spec in spec_paths)
    ]
    if not touched:
        return None
    if (
        re.search(r"^-.*\b(REQ|FEAT)-\d+\b", command, re.MULTILINE)
        and "CHANGELOG" not in command
        and "Change Log" not in command
    ):
        return warn_context(
            "SDLC warning: spec edit appears to delete REQ/FEAT IDs without a changelog entry."
        )
    phase = str(current_state.get("current_phase") or "")
    for path in touched:
        expected = spec_paths[resolve_path(path)]
        if phase and phase != expected:
            return warn_context(
                f"SDLC warning: {path.name} is normally updated only during {expected}; current phase is {phase}."
            )
    return None
