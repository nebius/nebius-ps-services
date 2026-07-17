#!/usr/bin/env python3
"""Inject per-command Nebius auth for Codex Bash tool calls."""

from __future__ import annotations

import ast
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
    rf"(?m)(^|[;&|][ \t]*)"
    r"(?P<prefix>(?:(?:command|exec|time|if|then|elif|while|until|do|!)[ \t]+|"
    r"env(?:[ \t]+(?:-[^ \t]+|[A-Za-z_][A-Za-z0-9_]*=[^ \t]+))*[ \t]+)*)"
    rf"{NEBIUS_CLI_WORD}(?=$|[ \t])"
    r"(?P<tail>(?:[ \t][^\n;&|]*)?)"
)
NEBIUS_TOKEN_PHRASE_RE = re.compile(
    rf"\b{NEBIUS_CLI_WORD}\b.*\biam\s+get-access-token\b", re.DOTALL
)
NEBIUS_REFRESH_COMMAND_RE = re.compile(
    r"(?m)(^|[;&|][ \t]*)nebius_refresh_token(?=$|[ \t;&|])"
)
SENSITIVE_TOKEN_REFERENCE_RE = re.compile(
    rf"\$(?:{SENSITIVE_TOKEN_ENV_PATTERN}\b|"
    rf"\{{{SENSITIVE_TOKEN_ENV_PATTERN}(?![A-Za-z0-9_])[^}}]*\}})"
)
INDIRECT_SHELL_EXPANSION_RE = re.compile(r"\$\{!|\beval\b")
PYTHON_ENV_PRINT_RE = re.compile(
    r"(?s)\b(print|pprint(?:\.pprint)?|json\.dumps)\s*\([^)]*"
    rf"(os\.environ|os\.getenv\([\"']{SENSITIVE_TOKEN_ENV_PATTERN}[\"'])"
)
SHELL_COMMAND_NAMES = {"bash", "sh", "zsh", "ksh"}
PYTHON_COMMAND_RE = re.compile(r"^python(?:3(?:\.\d+)?)?$")
PYTHON_PROCESS_CALLS = {
    ("os", "popen"),
    ("os", "system"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("subprocess", "run"),
}


def shell_command_words(command: str) -> list[str]:
    continued = re.sub(r"\\\r?\n", "", command)
    normalized = re.sub(
        r"([;&|<>]+)", r" \1 ", continued.replace("\n", " ; ")
    )
    try:
        return shlex.split(normalized)
    except ValueError:
        return []


def shell_command_segments(command: str) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for word in shell_command_words(command):
        if word in {";", "&&", "||", "|", "&"}:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(word)
    if current:
        segments.append(current)
    return segments


def command_words_after_wrappers(
    words: list[str], *, unwrap_env: bool = True
) -> list[str]:
    index = 0
    shell_wrappers = {
        "!",
        "{",
        "builtin",
        "command",
        "do",
        "elif",
        "exec",
        "if",
        "noglob",
        "then",
        "time",
        "until",
        "while",
    }
    assignment_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

    while index < len(words):
        word = words[index]
        if word in shell_wrappers or assignment_re.match(word):
            index += 1
            continue
        break

    if unwrap_env and index < len(words) and Path(words[index]).name == "env":
        index += 1
        while index < len(words):
            word = words[index]
            if assignment_re.match(word):
                index += 1
                continue
            if word in {
                "-u",
                "--unset",
                "-C",
                "--chdir",
                "-S",
                "--split-string",
            }:
                index += 2
                continue
            if word.startswith(("--unset=", "--chdir=")) or word == "--ignore-environment":
                index += 1
                continue
            if word == "--":
                index += 1
                break
            if word.startswith("-"):
                index += 1
                continue
            break

    return words[index:]


def env_split_string_may_mint_token(words: list[str]) -> bool:
    for index, word in enumerate(words):
        if word in {"-S", "--split-string"} and index + 1 < len(words):
            return bool(NEBIUS_TOKEN_PHRASE_RE.search(words[index + 1]))
        if word.startswith(("-S", "--split-string=")):
            return bool(NEBIUS_TOKEN_PHRASE_RE.search(word.split("=", 1)[-1]))
    return False


def command_words_invoke_token_mint(words: list[str]) -> bool:
    unwrapped = command_words_after_wrappers(words, unwrap_env=False)
    if (
        unwrapped
        and Path(unwrapped[0]).name == "env"
        and env_split_string_may_mint_token(unwrapped[1:])
    ):
        return True
    executable = command_words_after_wrappers(words)
    if not executable:
        return False
    if Path(executable[0]).name == "eval":
        return bool(NEBIUS_TOKEN_PHRASE_RE.search(" ".join(executable[1:])))
    if Path(executable[0]).name != NEBIUS_CLI:
        return False
    return any(
        executable[index : index + 2] == ["iam", "get-access-token"]
        for index in range(1, len(executable) - 1)
    )


def python_script_contains_token_mint(script: str) -> bool:
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name):
            continue
        if (node.func.value.id, node.func.attr) not in PYTHON_PROCESS_CALLS:
            continue

        argument: ast.expr | None = node.args[0] if node.args else None
        if argument is None:
            argument = next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg in {"args", "command"}
                ),
                None,
            )
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            if command_contains_token_mint(argument.value, depth=1):
                return True
        if isinstance(argument, (ast.List, ast.Tuple)):
            literal_words = [
                element.value
                for element in argument.elts
                if isinstance(element, ast.Constant)
                and isinstance(element.value, str)
            ]
            if len(literal_words) == len(argument.elts) and command_words_invoke_token_mint(
                literal_words
            ):
                return True
    return False


def command_substitution_scripts(command: str) -> list[str]:
    scripts: list[str] = []
    index = 0
    quote = ""
    while index < len(command):
        character = command[index]
        if character == "\\" and quote != "'":
            index += 2
            continue
        if character == "'":
            if quote == "'":
                quote = ""
            elif not quote:
                quote = "'"
            index += 1
            continue
        if character == '"':
            if quote == '"':
                quote = ""
            elif not quote:
                quote = '"'
            index += 1
            continue
        if character == "`" and quote != "'":
            end = index + 1
            while end < len(command):
                if command[end] == "\\":
                    end += 2
                    continue
                if command[end] == "`":
                    scripts.append(command[index + 1 : end])
                    index = end + 1
                    break
                end += 1
            else:
                return scripts
            continue
        if (
            character in {"$", "<", ">"}
            and index + 1 < len(command)
            and command[index + 1] == "("
            and quote != "'"
        ):
            start = index + 2
            end = start
            depth = 1
            inner_quote = ""
            while end < len(command):
                inner = command[end]
                if inner == "\\" and inner_quote != "'":
                    end += 2
                    continue
                if inner == "'":
                    if inner_quote == "'":
                        inner_quote = ""
                    elif not inner_quote:
                        inner_quote = "'"
                    end += 1
                    continue
                if inner == '"':
                    if inner_quote == '"':
                        inner_quote = ""
                    elif not inner_quote:
                        inner_quote = '"'
                    end += 1
                    continue
                if not inner_quote and inner == "(":
                    depth += 1
                elif not inner_quote and inner == ")":
                    depth -= 1
                    if depth == 0:
                        scripts.append(command[start:end])
                        index = end + 1
                        break
                end += 1
            else:
                return scripts
            continue
        index += 1
    return scripts


def command_contains_token_mint(command: str, depth: int = 0) -> bool:
    if any(
        command_words_invoke_token_mint(segment)
        for segment in shell_command_segments(command)
    ):
        return True
    if depth >= 4:
        return False
    nested_commands = nested_command_scripts(command)
    if any(
        PYTHON_COMMAND_RE.fullmatch(name)
        and python_script_contains_token_mint(script)
        for name, script in nested_commands
    ):
        return True
    nested_scripts = [script for _, script in nested_commands]
    nested_scripts.extend(command_substitution_scripts(command))
    return any(
        command_contains_token_mint(script, depth + 1)
        for script in nested_scripts
    )


def is_safe_manual_token_verification(command: str) -> bool:
    stripped = command.strip()
    if not stripped or "\n" in stripped or re.search(r"[;&|`()]", stripped):
        return False

    words = shell_command_words(stripped)
    if not words:
        return False

    stdout_redirects: list[str] = []
    command_words: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word in {">", ">>", "1>", "1>>"}:
            if index + 1 >= len(words):
                return False
            stdout_redirects.append(words[index + 1])
            index += 2
            continue
        redirect_match = re.fullmatch(r"(?:1?>|1>>)(.+)", word)
        if redirect_match:
            stdout_redirects.append(redirect_match.group(1))
            index += 1
            continue
        if re.search(r"[<>]", word):
            return False
        command_words.append(word)
        index += 1

    if stdout_redirects != ["/dev/null"]:
        return False

    profile_values: list[str] = []
    normalized: list[str] = []
    index = 0
    while index < len(command_words):
        word = command_words[index]
        if word in {"--profile", "-p"}:
            if index + 1 >= len(command_words):
                return False
            profile_values.append(command_words[index + 1])
            index += 2
            continue
        if word.startswith("--profile="):
            profile_values.append(word.split("=", 1)[1])
            index += 1
            continue
        normalized.append(word)
        index += 1

    return (
        normalized == [NEBIUS_CLI, "iam", "get-access-token"]
        and len(profile_values) == 1
        and profile_values[0].startswith(AGENT_PROFILE_PREFIX)
    )


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


def command_words_may_disclose_injected_env(words: list[str]) -> bool:
    executable = command_words_after_wrappers(words, unwrap_env=False)
    if not executable:
        return False

    command_name = Path(executable[0]).name
    arguments = executable[1:]
    command_text = " ".join(executable)

    if command_name in SHELL_COMMAND_NAMES:
        option_words = [word for word in arguments if word.startswith("-")]
        if any("x" in word[1:] for word in option_words):
            return True
        if any(word == "xtrace" for word in arguments):
            return True

    if command_name in {"echo", "printf"}:
        return bool(
            SENSITIVE_TOKEN_REFERENCE_RE.search(command_text)
            or INDIRECT_SHELL_EXPANSION_RE.search(command_text)
        )

    if command_name == "printenv":
        names = [word for word in arguments if not word.startswith("-")]
        return not names or any(
            name in {TOKEN_ENV, NEBIUS_TOKEN_ENV} for name in names
        )

    if command_name == "env":
        nested_command = command_words_after_wrappers(executable)
        return (
            not nested_command
            or any(
                word == "-S" or word.startswith("--split-string")
                for word in arguments
            )
            or command_words_may_disclose_injected_env(nested_command)
        )

    if command_name in {"export", "readonly"}:
        return not arguments or any(
            "p" in word[1:] for word in arguments if word.startswith("-")
        )

    if command_name == "set":
        return (
            not arguments
            or any(
                word.startswith("-") and "x" in word[1:]
                for word in arguments
            )
            or any(
                arguments[index : index + 2] == ["-o", "xtrace"]
                for index in range(len(arguments) - 1)
            )
        )

    if command_name in {"declare", "typeset"}:
        return not arguments or any(
            "p" in word[1:] for word in arguments if word.startswith("-")
        ) or any(word in {TOKEN_ENV, NEBIUS_TOKEN_ENV} for word in arguments)

    return False


def nested_command_may_disclose_injected_env(command: str, depth: int) -> bool:
    for name, script in nested_command_scripts(command):
        if name in SHELL_COMMAND_NAMES and command_may_disclose_injected_env(
            script, depth + 1
        ):
            return True
        if PYTHON_COMMAND_RE.fullmatch(name) and PYTHON_ENV_PRINT_RE.search(script):
            return True
    return False


def command_may_disclose_injected_env(command: str, depth: int = 0) -> bool:
    if any(
        command_words_may_disclose_injected_env(segment)
        for segment in shell_command_segments(command)
    ):
        return True
    if depth >= 4:
        return False
    return nested_command_may_disclose_injected_env(command, depth) or any(
        command_may_disclose_injected_env(script, depth + 1)
        for script in command_substitution_scripts(command)
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
    if command_contains_token_mint(command):
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


def command_segment_invokes_unprofiled_nebius_cli(words: list[str]) -> bool:
    executable = command_words_after_wrappers(words)
    if not executable or Path(executable[0]).name != NEBIUS_CLI:
        return False
    return not any(
        word in {"--profile", "-p"} or word.startswith("--profile=")
        for word in executable[1:]
    )


def command_invokes_nebius_cli(command: str, depth: int = 0) -> bool:
    if any(
        command_segment_invokes_unprofiled_nebius_cli(segment)
        for segment in shell_command_segments(command)
    ):
        return True
    if depth >= 4:
        return False
    nested_scripts = [script for _, script in nested_command_scripts(command)]
    nested_scripts.extend(command_substitution_scripts(command))
    return any(
        command_invokes_nebius_cli(script, depth + 1)
        for script in nested_scripts
    )


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
PROJECT_ID_VALUE={shlex.quote(project_id)}
CREDENTIALS_FILE_VALUE={shlex.quote(str(credential_file))}
export {NEBIUS_PROFILE_ENV}="$PROFILE"
export {NEBIUS_PROJECT_ID_ENV}="$PROJECT_ID_VALUE"
export {NEBIUS_CREDENTIALS_FILE_ENV}="$CREDENTIALS_FILE_VALUE"

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
        nebius_position = (
            match.start() + len(match.group(1)) + len(match.group("prefix"))
        )
        if shell_position_is_quoted(command, nebius_position):
            return match.group(0)
        tail = match.group("tail")
        if nebius_segment_has_profile(tail):
            return match.group(0)
        return (
            f"{match.group(1)}{match.group('prefix')}{NEBIUS_CLI} "
            f"--profile {shlex.quote(profile)}{tail}"
        )

    return NEBIUS_CLI_RE.sub(add_profile, command)


def shell_position_is_quoted(command: str, position: int) -> bool:
    quote = ""
    backtick = False
    index = 0
    while index < position:
        character = command[index]
        if character == "\\" and quote != "'":
            index += 2
            continue
        if backtick:
            if character == "`":
                backtick = False
            index += 1
            continue
        if character == "'":
            if quote == "'":
                quote = ""
            elif not quote:
                quote = "'"
        elif character == '"':
            if quote == '"':
                quote = ""
            elif not quote:
                quote = '"'
        elif character == "`" and quote != "'":
            backtick = True
        index += 1
    return bool(quote or backtick)


def nebius_cli_profile_environment_command(profile: str, command: str) -> str:
    return f"""\
PROFILE={shlex.quote(profile)}
export {NEBIUS_PROFILE_ENV}="$PROFILE"

{command}
"""


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
        if is_safe_manual_token_verification(command):
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
        if command_invokes_nebius_cli(rewritten_command):
            rewritten_command = nebius_cli_profile_environment_command(
                profile, rewritten_command
            )
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
