#!/usr/bin/env python3
"""Inject per-command Nebius auth for Codex Bash tool calls."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any


PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
NEBIUS_CLI_RE = re.compile(
    r"(?m)(^|[;&|][ \t]*)nebius(?=$|[ \t])(?P<tail>(?:[ \t][^\n;&|]*)?)"
)
NEBIUS_TOKEN_COMMAND_RE = re.compile(
    r"\bnebius(?:\s+(?:--profile(?:=|\s+)\S+|-p\s+\S+))*\s+iam\s+get-access-token\b"
    r"|\bnebius\s+iam\s+get-access-token\b"
)
TOKEN_DISCLOSURE_RE = re.compile(
    r"(?m)(^|[;&|][ \t]*)(echo|printf|printenv|env|export|set)([ \t]|$)"
)


def is_plain_nebius_token_command(command: str) -> bool:
    stripped = command.strip()
    if not stripped or "\n" in stripped or re.search(r"[;&|]", stripped):
        return False

    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return False

    if not tokens or tokens[0] != "nebius":
        return False

    index = 1
    while index < len(tokens):
        if tokens[index] in {"--profile", "-p"} and index + 1 < len(tokens):
            index += 2
            continue
        if tokens[index].startswith("--profile="):
            index += 1
            continue
        break

    return tokens[index : index + 2] == ["iam", "get-access-token"]


def command_contains_token_mint(command: str) -> bool:
    return bool(NEBIUS_TOKEN_COMMAND_RE.search(command))


def command_discards_stdout(command: str) -> bool:
    return bool(re.search(r"(?:^|[ \t])(?:1?>|1>>)[ \t]*/dev/null\b", command))


def command_may_disclose_injected_env(command: str) -> bool:
    return "set -x" in command or bool(TOKEN_DISCLOSURE_RE.search(command))


def terraform_command_needs_nebius(command: str) -> bool:
    if not re.search(r"\bterraform\b", command, re.IGNORECASE):
        return False
    if re.search(r"\bnebius\b|NEBIUS_|api\.nebius\.cloud", command, re.IGNORECASE):
        return True

    try:
        for index, path in enumerate(Path.cwd().glob("*.tf")):
            if index >= 20:
                break
            content = path.read_text(encoding="utf-8", errors="ignore")[:524288]
            if "nebius" in content.lower():
                return True
    except OSError:
        return False

    return False


def deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def allow_rewrite(command: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {"command": command},
        }
    }


def command_needs_nebius_token(command: str) -> bool:
    if is_plain_nebius_token_command(command) or command_contains_token_mint(command):
        return False

    patterns = [
        r"\bNEBIUS_IAM_TOKEN\b",
        r"\bAuthorization:\s*Bearer\b",
        r"\bgrpcurl\b.*\bnebius\b",
        r"\bcurl\b.*\bnebius\b",
        r"\bapi\.nebius\.cloud\b",
        r"\b[a-z0-9.-]+\.api\.nebius\.cloud\b",
        r"\bcpl\.iam\.api\.nebius\.cloud\b",
        r"\bpytest\b.*\bnebius\b",
    ]
    return terraform_command_needs_nebius(command) or any(
        re.search(pattern, command, re.IGNORECASE | re.DOTALL)
        for pattern in patterns
    )


def command_invokes_nebius_cli(command: str) -> bool:
    if is_plain_nebius_token_command(command):
        return False
    return bool(NEBIUS_CLI_RE.search(command))


def nebius_segment_has_profile(tail: str) -> bool:
    return bool(re.search(r"(^|\s)(--profile(?:\s|=|$)|-p(?:\s|$))", tail))


def infer_project_id_from_single_credential() -> str:
    prefix = "codex-agent-authkey."
    suffix = ".json"
    files = sorted(Path.home().glob(f".nebius/{prefix}*{suffix}"))
    if len(files) != 1:
        return ""

    name = files[0].name
    if not name.startswith(prefix) or not name.endswith(suffix):
        return ""
    return name[len(prefix) : -len(suffix)]


def resolve_project_id() -> str:
    project_id = os.environ.get("CODEX_NEBIUS_PROJECT_ID", "").strip()
    if project_id:
        return project_id
    return infer_project_id_from_single_credential()


def validate_project_id(project_id: str) -> bool:
    return bool(PROJECT_ID_RE.fullmatch(project_id))


def credential_file_exists(project_id: str) -> bool:
    credential_file = (
        Path.home() / ".nebius" / f"codex-agent-authkey.{project_id}.json"
    )
    return credential_file.is_file()


def token_injection_command(profile: str, project_id: str, command: str) -> str:
    return f"""\
set -euo pipefail
PROFILE={shlex.quote(profile)}
TOKEN="$(nebius iam get-access-token --profile "$PROFILE")"
export TOKEN
export NEBIUS_IAM_TOKEN="$TOKEN"
export NEBIUS_PROFILE="$PROFILE"
export NEBIUS_PROJECT_ID={shlex.quote(project_id)}

{command}
"""


def nebius_cli_profile_command(profile: str, command: str) -> str:
    def add_profile(match: re.Match[str]) -> str:
        tail = match.group("tail")
        if nebius_segment_has_profile(tail):
            return match.group(0)
        return f"{match.group(1)}nebius --profile {shlex.quote(profile)}{tail}"

    return NEBIUS_CLI_RE.sub(add_profile, command)


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("hook_event_name") != "PreToolUse":
        return {}
    if payload.get("tool_name") != "Bash":
        return {}

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return {}

    command = tool_input.get("command") or ""
    if not isinstance(command, str) or not command.strip():
        return {}

    if command_contains_token_mint(command):
        if is_plain_nebius_token_command(command) and command_discards_stdout(command):
            return {}
        return deny(
            "Nebius access-token commands can expose token material to Codex output. Use the agent profile through $agent-nebius-auth or redirect verification output to /dev/null outside model-visible logs."
        )

    needs_token = command_needs_nebius_token(command)
    invokes_nebius_cli = command_invokes_nebius_cli(command)
    if not needs_token and not invokes_nebius_cli:
        return {}

    if needs_token and command_may_disclose_injected_env(command):
        return deny(
            "Nebius auth hook refused to inject credentials into a command that may print or dump the injected environment."
        )

    project_id = resolve_project_id()
    if not project_id:
        return deny(
            "Nebius auth required, but CODEX_NEBIUS_PROJECT_ID is not set and no single local agent credential file can be inferred."
        )
    if not validate_project_id(project_id):
        return deny(
            "Nebius auth required, but CODEX_NEBIUS_PROJECT_ID is invalid."
        )
    if not credential_file_exists(project_id):
        return deny(
            "Nebius auth required, but the local agent credential file is missing. Run $agent-nebius-auth setup first."
        )

    profile = f"codex-agent-{project_id}"

    if needs_token:
        if invokes_nebius_cli:
            command = nebius_cli_profile_command(profile, command)
        return allow_rewrite(token_injection_command(profile, project_id, command))

    if invokes_nebius_cli:
        rewritten_command = nebius_cli_profile_command(profile, command)
        if rewritten_command != command:
            return allow_rewrite(rewritten_command)

    return {}


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        result = evaluate(payload)
        if result:
            print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:  # pragma: no cover - hook runtime guard
        print(json.dumps(deny(f"Nebius auth hook failed closed: {exc}"), sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
