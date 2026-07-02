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


NEBIUS_CLI = "nebius"
AGENT_PROFILE_PREFIX = "codex-agent-"
CREDENTIAL_FILE_PREFIX = "codex-agent-authkey."
CREDENTIAL_FILE_SUFFIX = ".json"
NEBIUS_CONFIG_DIR = ".nebius"
DEFAULT_PROJECT_FILE_NAME = "codex-agent-default-project-id"
PROJECT_ID_ENV = "CODEX_NEBIUS_PROJECT_ID"
NEBIUS_PROFILE_ENV = "NEBIUS_PROFILE"
NEBIUS_PROJECT_ID_ENV = "NEBIUS_PROJECT_ID"
NEBIUS_CREDENTIALS_FILE_ENV = "NEBIUS_AUTH_CREDENTIALS_FILE"
NEBIUS_TOKEN_ENV = "NEBIUS_IAM_TOKEN"
TOKEN_ENV = "TOKEN"
MAX_TERRAFORM_FILES_TO_SCAN = 20
MAX_TERRAFORM_FILE_BYTES = 524288

PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
NEBIUS_CLI_WORD = re.escape(NEBIUS_CLI)
SENSITIVE_TOKEN_ENV_PATTERN = rf"(?:{re.escape(TOKEN_ENV)}|{re.escape(NEBIUS_TOKEN_ENV)})"
NEBIUS_CLI_RE = re.compile(
    rf"(?m)(^|[;&|][ \t]*){NEBIUS_CLI_WORD}(?=$|[ \t])"
    r"(?P<tail>(?:[ \t][^\n;&|]*)?)"
)
NEBIUS_TOKEN_COMMAND_RE = re.compile(
    rf"\b{NEBIUS_CLI_WORD}"
    r"(?:\s+(?:--profile(?:=|\s+)\S+|-p\s+\S+))*"
    r"\s+iam\s+get-access-token\b"
    rf"|\b{NEBIUS_CLI_WORD}\s+iam\s+get-access-token\b"
)
NEBIUS_REFRESH_COMMAND_RE = re.compile(
    r"(?m)(^|[;&|][ \t]*)nebius_refresh_token(?=$|[ \t;&|])"
)
TOKEN_DISCLOSURE_RE = re.compile(
    r"(?m)(^|[;&|][ \t]*)(echo|printf|printenv|env|export|set)([ \t]|$)"
)
NESTED_ENV_DUMP_RE = re.compile(
    r"(?m)(^|[;&|][ \t]*)[ \t]*(printenv|env|export|set)([ \t]|$)"
)
NESTED_TOKEN_PRINT_RE = re.compile(
    r"(?m)(^|[;&|][ \t]*)[ \t]*(echo|printf)([ \t]|$).*"
    rf"\$\{{?{SENSITIVE_TOKEN_ENV_PATTERN}\}}?"
)
PYTHON_ENV_PRINT_RE = re.compile(
    r"(?s)\b(print|pprint(?:\.pprint)?|json\.dumps)\s*\([^)]*"
    rf"(os\.environ|os\.getenv\([\"']{SENSITIVE_TOKEN_ENV_PATTERN}[\"'])"
)
SHELL_COMMAND_NAMES = {"bash", "sh", "zsh", "ksh"}
PYTHON_COMMAND_RE = re.compile(r"^python(?:3(?:\.\d+)?)?$")


def is_plain_nebius_token_command(command: str) -> bool:
    stripped = command.strip()
    if not stripped or "\n" in stripped or re.search(r"[;&|]", stripped):
        return False

    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return False

    if not tokens or tokens[0] != NEBIUS_CLI:
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


def shell_command_words(command: str) -> list[str]:
    normalized = re.sub(r"([;&|]+)", r" \1 ", command)
    try:
        return shlex.split(normalized)
    except ValueError:
        return []


def nested_command_scripts(command: str) -> list[tuple[str, str]]:
    tokens = shell_command_words(command)
    scripts: list[tuple[str, str]] = []
    for index, token in enumerate(tokens):
        name = Path(token).name
        if name not in SHELL_COMMAND_NAMES and not PYTHON_COMMAND_RE.fullmatch(name):
            continue

        cursor = index + 1
        while cursor < len(tokens):
            current = tokens[cursor]
            if current in {";", "&&", "||", "|"}:
                break
            if current == "-c" and cursor + 1 < len(tokens):
                scripts.append((name, tokens[cursor + 1]))
                break
            if (
                name in SHELL_COMMAND_NAMES
                and current.startswith("-")
                and "c" in current[1:]
            ):
                if cursor + 1 < len(tokens):
                    scripts.append((name, tokens[cursor + 1]))
                break
            cursor += 1
    return scripts


def nested_command_may_disclose_injected_env(command: str) -> bool:
    for name, script in nested_command_scripts(command):
        if name in SHELL_COMMAND_NAMES and (
            NESTED_ENV_DUMP_RE.search(script) or NESTED_TOKEN_PRINT_RE.search(script)
        ):
            return True
        if PYTHON_COMMAND_RE.fullmatch(name) and PYTHON_ENV_PRINT_RE.search(script):
            return True
    return False


def command_may_disclose_injected_env(command: str) -> bool:
    return (
        "set -x" in command
        or bool(TOKEN_DISCLOSURE_RE.search(command))
        or nested_command_may_disclose_injected_env(command)
    )


def refresh_helper_shell_definition() -> str:
    profile_var = f"${{{NEBIUS_PROFILE_ENV}}}"
    token_var = f"${{{TOKEN_ENV}}}"
    return (
        "nebius_refresh_token() {\n"
        f"  unset {TOKEN_ENV} {NEBIUS_TOKEN_ENV}\n"
        f'  {TOKEN_ENV}="$({NEBIUS_CLI} iam get-access-token --profile "{profile_var}")"\n'
        f"  export {TOKEN_ENV}\n"
        f'  export {NEBIUS_TOKEN_ENV}="{token_var}"\n'
        "}\n"
    )


def terraform_command_needs_nebius(command: str) -> bool:
    if not re.search(r"\bterraform\b", command, re.IGNORECASE):
        return False
    if re.search(r"\bnebius\b|NEBIUS_|api\.nebius\.cloud", command, re.IGNORECASE):
        return True

    try:
        for index, path in enumerate(Path.cwd().glob("*.tf")):
            if index >= MAX_TERRAFORM_FILES_TO_SCAN:
                break
            content = path.read_text(encoding="utf-8", errors="ignore")[
                :MAX_TERRAFORM_FILE_BYTES
            ]
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
    if NEBIUS_REFRESH_COMMAND_RE.search(command):
        return True

    patterns = [
        rf"\b{re.escape(NEBIUS_TOKEN_ENV)}\b",
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
    files = sorted(
        Path.home().glob(
            f"{NEBIUS_CONFIG_DIR}/{CREDENTIAL_FILE_PREFIX}*{CREDENTIAL_FILE_SUFFIX}"
        )
    )
    if len(files) != 1:
        return ""

    name = files[0].name
    if not name.startswith(CREDENTIAL_FILE_PREFIX) or not name.endswith(
        CREDENTIAL_FILE_SUFFIX
    ):
        return ""
    return name[len(CREDENTIAL_FILE_PREFIX) : -len(CREDENTIAL_FILE_SUFFIX)]


def infer_project_id_from_default_file() -> str:
    default_file = Path.home() / NEBIUS_CONFIG_DIR / DEFAULT_PROJECT_FILE_NAME
    try:
        return default_file.read_text(encoding="utf-8").strip().splitlines()[0]
    except (IndexError, OSError):
        return ""


def resolve_project_id() -> str:
    project_id = os.environ.get(PROJECT_ID_ENV, "").strip()
    if project_id:
        return project_id
    project_id = infer_project_id_from_default_file()
    if project_id:
        return project_id
    return infer_project_id_from_single_credential()


def validate_project_id(project_id: str) -> bool:
    return bool(PROJECT_ID_RE.fullmatch(project_id))


def credential_file_for_project(project_id: str) -> Path:
    return (
        Path.home()
        / NEBIUS_CONFIG_DIR
        / f"{CREDENTIAL_FILE_PREFIX}{project_id}{CREDENTIAL_FILE_SUFFIX}"
    )


def credential_file_exists(project_id: str) -> bool:
    return credential_file_for_project(project_id).is_file()


def token_injection_command(
    profile: str,
    project_id: str,
    credential_file: Path,
    command: str,
) -> str:
    return f"""\
set -euo pipefail
PROFILE={shlex.quote(profile)}
export {NEBIUS_PROFILE_ENV}="$PROFILE"
export {NEBIUS_PROJECT_ID_ENV}={shlex.quote(project_id)}
export {NEBIUS_CREDENTIALS_FILE_ENV}={shlex.quote(str(credential_file))}

NEBIUS_REFRESH_BASH_ENV="$(umask 077 && mktemp "${{TMPDIR:-/tmp}}/nebius-refresh.XXXXXX")"
export NEBIUS_REFRESH_BASH_ENV
cat > "$NEBIUS_REFRESH_BASH_ENV" <<'NEBIUS_REFRESH_HELPER'
{refresh_helper_shell_definition()}\
NEBIUS_REFRESH_HELPER
export BASH_ENV="$NEBIUS_REFRESH_BASH_ENV"
trap 'rm -f "$NEBIUS_REFRESH_BASH_ENV"' EXIT HUP INT TERM

{refresh_helper_shell_definition()}\
if [ -n "${{BASH_VERSION:-}}" ]; then
  export -f nebius_refresh_token
fi

nebius_refresh_token

{command}
"""


def nebius_cli_profile_command(profile: str, command: str) -> str:
    def add_profile(match: re.Match[str]) -> str:
        tail = match.group("tail")
        if nebius_segment_has_profile(tail):
            return match.group(0)
        return f"{match.group(1)}{NEBIUS_CLI} --profile {shlex.quote(profile)}{tail}"

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
            "Nebius auth required, but no project selector is available. Set "
            f"{PROJECT_ID_ENV}, run $agent-nebius-auth setup to write the default "
            "selector, or keep exactly one local agent credential file."
        )
    if not validate_project_id(project_id):
        return deny(
            "Nebius auth required, but the resolved Nebius project selector is invalid."
        )
    if not credential_file_exists(project_id):
        return deny(
            "Nebius auth required, but the local agent credential file is missing. Run $agent-nebius-auth setup first."
        )

    profile = f"{AGENT_PROFILE_PREFIX}{project_id}"

    if needs_token:
        if invokes_nebius_cli:
            command = nebius_cli_profile_command(profile, command)
        return allow_rewrite(
            token_injection_command(
                profile,
                project_id,
                credential_file_for_project(project_id),
                command,
            )
        )

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
