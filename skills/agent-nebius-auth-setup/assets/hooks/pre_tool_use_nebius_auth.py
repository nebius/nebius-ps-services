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


HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from nebius_auth_shared import (  # noqa: E402
    credential_file_safety_issue as shared_credential_file_safety_issue,
)


NEBIUS_CLI = "nebius"
AGENT_PROFILE_PREFIX = "codex-agent-"
CREDENTIAL_FILE_PREFIX = "codex-agent-authkey."
CREDENTIAL_FILE_SUFFIX = ".json"
NEBIUS_CONFIG_DIR = ".nebius"
PROJECT_ID_ENV = "CODEX_NEBIUS_PROJECT_ID"
NEBIUS_PROFILE_ENV = "NEBIUS_PROFILE"
NEBIUS_PROJECT_ID_ENV = "NEBIUS_PROJECT_ID"
NEBIUS_CREDENTIALS_FILE_ENV = "NEBIUS_AUTH_CREDENTIALS_FILE"
NEBIUS_TOKEN_ENV = "NEBIUS_IAM_TOKEN"
TOKEN_ENV = "TOKEN"
TOKEN_HELPER_ENV = "CODEX_NEBIUS_TOKEN_HELPER"
TOKEN_HELPER_BASENAME = "nebius_auth_token_helper.py"
TOKEN_HELPER_OPERATIONS = {"exec-token", "retry-idempotent"}
MAX_TERRAFORM_FILES_TO_SCAN = 20
MAX_TERRAFORM_FILE_BYTES = 524288

PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
PROJECT_SELECTOR_RE = re.compile(
    rf"^{PROJECT_ID_ENV}=([^\s;&|]+)[ \t]+(.+)$", re.DOTALL
)
NEBIUS_CLI_WORD = re.escape(NEBIUS_CLI)
SENSITIVE_TOKEN_ENV_PATTERN = rf"(?:{re.escape(TOKEN_ENV)}|{re.escape(NEBIUS_TOKEN_ENV)})"
NEBIUS_TOKEN_PHRASE_RE = re.compile(
    rf"\b{NEBIUS_CLI_WORD}\b.*\biam\s+get-access-token\b", re.DOTALL
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
STATIC_COMMAND_LAUNCHERS = {"gtimeout", "nice", "nohup", "stdbuf", "timeout"}
NEBIUS_ENDPOINT_RE = re.compile(
    r"(?:^|[/:.@])(?:[a-z0-9-]+\.)*api\.nebius\.cloud"
    r"(?::[0-9]+)?(?:[/?#]|$)",
    re.IGNORECASE,
)


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


def quote_aware_shell_command_segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return []

    segments: list[list[str]] = []
    current: list[str] = []
    for word in words:
        if word and all(character in ";&|" for character in word):
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
            if word == "--":
                index += 1
                continue
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
            if word.startswith(("--unset=", "--chdir=")) or word in {
                "-i",
                "--ignore-environment",
            }:
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


def static_launcher_command_words(words: list[str]) -> list[str]:
    """Return argv launched by a supported literal process wrapper."""
    executable = command_words_after_wrappers(words)
    if not executable:
        return []
    launcher = Path(executable[0]).name
    if launcher not in STATIC_COMMAND_LAUNCHERS:
        return []

    arguments = executable[1:]
    index = 0
    options_with_values: set[str] = set()
    if launcher in {"timeout", "gtimeout"}:
        options_with_values = {"-k", "--kill-after", "-s", "--signal"}
    elif launcher == "stdbuf":
        options_with_values = {
            "-e",
            "--error",
            "-i",
            "--input",
            "-o",
            "--output",
        }
    elif launcher == "nice":
        options_with_values = {"-n", "--adjustment"}

    while index < len(arguments):
        word = arguments[index]
        if word == "--":
            index += 1
            break
        if word in {"--help", "--version"}:
            return []
        if word in options_with_values:
            index += 2
            continue
        if word.startswith("-"):
            index += 1
            continue
        break

    if launcher in {"timeout", "gtimeout"}:
        if index >= len(arguments):
            return []
        index += 1
    return arguments[index:]


def token_helper_command_words(words: list[str]) -> list[str]:
    """Return the generic token helper's child argv, if statically visible."""

    executable = command_words_after_wrappers(words, unwrap_env=False)
    if not executable:
        return []
    index = 0
    command_name = Path(executable[index]).name
    if PYTHON_COMMAND_RE.fullmatch(command_name):
        index += 1
        if index >= len(executable):
            return []
        helper = executable[index]
        if (
            Path(helper).name != TOKEN_HELPER_BASENAME
            and helper
            not in {f"${TOKEN_HELPER_ENV}", f"${{{TOKEN_HELPER_ENV}}}"}
        ):
            return []
    elif command_name != TOKEN_HELPER_BASENAME:
        return []

    index += 1
    if (
        index >= len(executable)
        or executable[index] not in TOKEN_HELPER_OPERATIONS
    ):
        return []
    index += 1
    if index >= len(executable) or executable[index] != "--":
        return []
    return executable[index + 1 :]


def policy_nested_command_words(words: list[str]) -> list[list[str]]:
    nested: list[list[str]] = []
    helper_command = token_helper_command_words(words)
    if helper_command:
        nested.append(helper_command)
    launcher_command = static_launcher_command_words(words)
    if launcher_command:
        nested.append(launcher_command)
    return nested


def env_split_string_may_mint_token(words: list[str]) -> bool:
    for index, word in enumerate(words):
        if word in {"-S", "--split-string"} and index + 1 < len(words):
            return bool(NEBIUS_TOKEN_PHRASE_RE.search(words[index + 1]))
        if word.startswith(("-S", "--split-string=")):
            return bool(NEBIUS_TOKEN_PHRASE_RE.search(word.split("=", 1)[-1]))
    return False


def command_words_invoke_token_mint(words: list[str]) -> bool:
    if any(
        command_words_invoke_token_mint(nested)
        for nested in policy_nested_command_words(words)
    ):
        return True
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


def is_safe_manual_token_verification(command: str, expected_profile: str) -> bool:
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
        normalized
        in (
            [NEBIUS_CLI, "iam", "get-access-token"],
            [NEBIUS_CLI, "iam", "get-access-token", "--no-browser"],
        )
        and len(profile_values) == 1
        and profile_values[0] == expected_profile
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


def command_words_may_disclose_injected_env(
    words: list[str], *, depth: int = 0
) -> bool:
    if depth < 4 and any(
        command_may_disclose_injected_env(shlex.join(nested), depth=depth + 1)
        for nested in policy_nested_command_words(words)
    ):
        return True
    executable = command_words_after_wrappers(words, unwrap_env=False)
    if not executable:
        return False

    command_name = Path(executable[0]).name
    arguments = executable[1:]
    command_text = " ".join(executable)

    if (
        command_name
        not in {"[", "[[", "curl", "grpcurl", "test", *SHELL_COMMAND_NAMES}
        and SENSITIVE_TOKEN_REFERENCE_RE.search(command_text)
    ):
        return True

    if command_name in SHELL_COMMAND_NAMES:
        option_words = [word for word in arguments if word.startswith("-")]
        if any("x" in word[1:] for word in option_words):
            return True
        if any(word == "xtrace" for word in arguments):
            return True

    if command_name == "curl" and any(
        word == "--verbose"
        or word.startswith("--trace")
        or (word.startswith("-") and not word.startswith("--") and "v" in word[1:])
        for word in arguments
    ):
        return True

    if command_name == "grpcurl" and any(
        word in {"-v", "-vv", "-very-verbose"} for word in arguments
    ):
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
            or command_words_may_disclose_injected_env(
                nested_command, depth=depth + 1
            )
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
        command_words_may_disclose_injected_env(segment, depth=depth)
        for segment in shell_command_segments(command)
    ):
        return True
    if depth >= 4:
        return False
    return nested_command_may_disclose_injected_env(command, depth) or any(
        command_may_disclose_injected_env(script, depth + 1)
        for script in command_substitution_scripts(command)
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


def shell_position_is_single_quoted(command: str, position: int) -> bool:
    quote = ""
    index = 0
    while index < position:
        character = command[index]
        if character == "\\" and quote != "'":
            index += 2
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
        index += 1
    return quote == "'"


def command_has_active_token_reference(command: str) -> bool:
    return any(
        not shell_position_is_single_quoted(command, match.start())
        for match in SENSITIVE_TOKEN_REFERENCE_RE.finditer(command)
    )


def command_words_need_nebius_token(words: list[str]) -> bool:
    if any(
        command_words_need_nebius_token(nested)
        for nested in policy_nested_command_words(words)
    ):
        return True
    executable = command_words_after_wrappers(words)
    if not executable:
        return False
    command_name = Path(executable[0]).name
    if command_name not in {"curl", "grpcurl"}:
        return False
    return any(NEBIUS_ENDPOINT_RE.search(word) for word in executable[1:])


def command_needs_nebius_token(command: str, depth: int = 0) -> bool:
    if command_contains_token_mint(command):
        return False
    if command_has_active_token_reference(command):
        return True
    if terraform_command_needs_nebius(command) or any(
        command_words_need_nebius_token(segment)
        for segment in quote_aware_shell_command_segments(command)
    ):
        return True
    if depth >= 4:
        return False
    nested_scripts = [script for _, script in nested_command_scripts(command)]
    nested_scripts.extend(command_substitution_scripts(command))
    return any(
        command_needs_nebius_token(script, depth + 1)
        for script in nested_scripts
    )


def command_segment_invokes_unprofiled_nebius_cli(words: list[str]) -> bool:
    if any(
        command_segment_invokes_unprofiled_nebius_cli(nested)
        for nested in policy_nested_command_words(words)
    ):
        return True
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


UNSUPPORTED_NEBIUS_LAUNCHERS = {
    "find",
    "parallel",
    "sudo",
    "watch",
    "xargs",
}


def segment_has_unsupported_nebius_launcher(words: list[str]) -> bool:
    executable = command_words_after_wrappers(words, unwrap_env=False)
    if not executable:
        return False
    launcher = Path(executable[0]).name
    return launcher in UNSUPPORTED_NEBIUS_LAUNCHERS and any(
        Path(word).name == NEBIUS_CLI for word in executable[1:]
    )


def command_has_unsupported_nebius_launcher(
    command: str, depth: int = 0
) -> bool:
    if any(
        segment_has_unsupported_nebius_launcher(segment)
        for segment in shell_command_segments(command)
    ):
        return True
    if depth >= 4:
        return False
    policy_commands = [
        nested
        for segment in shell_command_segments(command)
        for nested in policy_nested_command_words(segment)
    ]
    nested_scripts = [script for _, script in nested_command_scripts(command)]
    nested_scripts.extend(command_substitution_scripts(command))
    nested_scripts.extend(shlex.join(words) for words in policy_commands)
    return any(
        command_has_unsupported_nebius_launcher(script, depth + 1)
        for script in nested_scripts
    )


def segment_has_project_selector_assignment(words: list[str]) -> bool:
    assignment_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    index = 0
    wrappers = {
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
    while index < len(words) and words[index] in wrappers:
        index += 1
    while index < len(words) and assignment_re.match(words[index]):
        if words[index].startswith(f"{PROJECT_ID_ENV}="):
            return True
        index += 1
    if index >= len(words) or Path(words[index]).name != "env":
        return False
    index += 1
    while index < len(words):
        word = words[index]
        if word == "--":
            index += 1
            continue
        if word.startswith(f"{PROJECT_ID_ENV}="):
            return True
        if assignment_re.match(word):
            index += 1
            continue
        if word in {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}:
            index += 2
            continue
        if word.startswith(("--unset=", "--chdir=", "--split-string=")):
            index += 1
            continue
        if word in {"-i", "--ignore-environment"}:
            index += 1
            continue
        break
    return False


def command_has_project_selector_assignment(command: str, depth: int = 0) -> bool:
    if any(
        segment_has_project_selector_assignment(segment)
        for segment in shell_command_segments(command)
    ):
        return True
    if depth >= 4:
        return False
    policy_commands = [
        nested
        for segment in shell_command_segments(command)
        for nested in policy_nested_command_words(segment)
    ]
    nested_scripts = [script for _, script in nested_command_scripts(command)]
    nested_scripts.extend(command_substitution_scripts(command))
    nested_scripts.extend(shlex.join(words) for words in policy_commands)
    return any(
        command_has_project_selector_assignment(script, depth + 1)
        for script in nested_scripts
    )


MANAGED_AUTH_ENV_NAMES = {
    PROJECT_ID_ENV,
    NEBIUS_PROFILE_ENV,
    NEBIUS_PROJECT_ID_ENV,
    NEBIUS_CREDENTIALS_FILE_ENV,
    NEBIUS_TOKEN_ENV,
    TOKEN_ENV,
    TOKEN_HELPER_ENV,
}


def segment_mutates_managed_auth_env(words: list[str]) -> bool:
    assignment_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
    wrappers = {
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
    index = 0
    while index < len(words):
        word = words[index]
        if word in wrappers:
            index += 1
            continue
        match = assignment_re.match(word)
        if match:
            if match.group(1) in MANAGED_AUTH_ENV_NAMES:
                return True
            index += 1
            continue
        break
    if index >= len(words):
        return False
    executable = Path(words[index]).name
    if executable == "env":
        env_words = words[index + 1 :]
        env_index = 0
        while env_index < len(env_words):
            word = env_words[env_index]
            match = assignment_re.match(word)
            if match and match.group(1) in MANAGED_AUTH_ENV_NAMES:
                return True
            if word in {"-u", "--unset"}:
                env_index += 1
                if (
                    env_index < len(env_words)
                    and env_words[env_index] in MANAGED_AUTH_ENV_NAMES
                ):
                    return True
            elif word.startswith("--unset="):
                if word.split("=", 1)[1] in MANAGED_AUTH_ENV_NAMES:
                    return True
            elif word.startswith("-") and not word.startswith("--"):
                short_options = word[1:]
                if "u" in short_options:
                    unset_name = short_options.split("u", 1)[1]
                    if not unset_name:
                        env_index += 1
                        if env_index < len(env_words):
                            unset_name = env_words[env_index]
                    if unset_name in MANAGED_AUTH_ENV_NAMES:
                        return True
            env_index += 1
        return False
    if executable in {"declare", "export", "local", "readonly", "typeset"}:
        return any(
            (match := assignment_re.match(word))
            and match.group(1) in MANAGED_AUTH_ENV_NAMES
            for word in words[index + 1 :]
        )
    return executable == "unset" and any(
        word in MANAGED_AUTH_ENV_NAMES for word in words[index + 1 :]
    )


def command_mutates_managed_auth_env(command: str, depth: int = 0) -> bool:
    if any(
        segment_mutates_managed_auth_env(segment)
        for segment in shell_command_segments(command)
    ):
        return True
    if depth >= 4:
        return False
    policy_commands = [
        nested
        for segment in shell_command_segments(command)
        for nested in policy_nested_command_words(segment)
    ]
    nested_scripts = [script for _, script in nested_command_scripts(command)]
    nested_scripts.extend(command_substitution_scripts(command))
    nested_scripts.extend(shlex.join(words) for words in policy_commands)
    return any(
        command_mutates_managed_auth_env(script, depth + 1)
        for script in nested_scripts
    )


def parse_project_selector(command: str) -> tuple[str, str, str]:
    """Return project ID, stripped command, and a fail-closed error string."""
    if not command.startswith(f"{PROJECT_ID_ENV}="):
        if command_has_project_selector_assignment(command):
            return "", command, "the project selector is not the first shell token"
        return "", command, "the required leading project selector is missing"

    match = PROJECT_SELECTOR_RE.fullmatch(command)
    if match is None:
        return "", command, "the leading project selector is malformed"

    project_id, stripped_command = match.groups()
    if not validate_project_id(project_id):
        return "", stripped_command, "the leading project selector is invalid"
    if command_has_project_selector_assignment(stripped_command):
        return "", stripped_command, "multiple or nested project selectors are not allowed"
    if not stripped_command.strip():
        return "", stripped_command, "the selected command is empty"
    return project_id, stripped_command, ""


def validate_project_id(project_id: str) -> bool:
    return bool(PROJECT_ID_RE.fullmatch(project_id))


def command_segment_nebius_profile(words: list[str]) -> str | None:
    for nested in policy_nested_command_words(words):
        profile = command_segment_nebius_profile(nested)
        if profile is not None:
            return profile
    executable = command_words_after_wrappers(words)
    if not executable or Path(executable[0]).name != NEBIUS_CLI:
        return None
    for index, word in enumerate(executable[1:], start=1):
        if word in {"--profile", "-p"}:
            return executable[index + 1] if index + 1 < len(executable) else ""
        if word.startswith("--profile="):
            return word.split("=", 1)[1]
    return ""


def command_nebius_profiles(command: str, depth: int = 0) -> list[str]:
    profiles = [
        profile
        for segment in shell_command_segments(command)
        if (profile := command_segment_nebius_profile(segment)) is not None
    ]
    if depth >= 4:
        return profiles
    nested_scripts = [script for _, script in nested_command_scripts(command)]
    nested_scripts.extend(command_substitution_scripts(command))
    for script in nested_scripts:
        profiles.extend(command_nebius_profiles(script, depth + 1))
    return profiles


def command_text_requires_nebius_auth(command: str) -> bool:
    return (
        command_contains_token_mint(command)
        or command_needs_nebius_token(command)
        or bool(command_nebius_profiles(command))
    )


def env_segment_has_unsupported_nebius(words: list[str]) -> bool:
    executable = command_words_after_wrappers(words, unwrap_env=False)
    if not executable or Path(executable[0]).name != "env":
        return False

    index = 1
    clears_environment = False
    split_scripts: list[str] = []
    assignment_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    while index < len(executable):
        word = executable[index]
        if word == "--":
            index += 1
            continue
        if word in {"-i", "--ignore-environment"}:
            clears_environment = True
            index += 1
            continue
        if word in {"-S", "--split-string"}:
            if index + 1 < len(executable):
                split_scripts.append(executable[index + 1])
            index += 2
            continue
        if word.startswith("-") and not word.startswith("--") and "S" in word[1:]:
            option_flags, attached = word[1:].split("S", 1)
            if "i" in option_flags:
                clears_environment = True
            if attached:
                split_scripts.append(attached)
            elif index + 1 < len(executable):
                split_scripts.append(executable[index + 1])
                index += 1
            index += 1
            continue
        if word.startswith("--split-string="):
            split_scripts.append(word.split("=", 1)[1])
            index += 1
            continue
        if word in {"-u", "--unset", "-C", "--chdir"}:
            index += 2
            continue
        if word.startswith(("--unset=", "--chdir=")):
            index += 1
            continue
        if assignment_re.match(word):
            index += 1
            continue
        break

    if any(command_text_requires_nebius_auth(script) for script in split_scripts):
        return True
    if not clears_environment or index >= len(executable):
        return False
    remaining = " ".join(shlex.quote(word) for word in executable[index:])
    return command_text_requires_nebius_auth(remaining)


def command_has_unsupported_env_nebius(command: str, depth: int = 0) -> bool:
    if any(
        env_segment_has_unsupported_nebius(segment)
        for segment in shell_command_segments(command)
    ):
        return True
    if depth >= 4:
        return False
    policy_commands = [
        nested
        for segment in shell_command_segments(command)
        for nested in policy_nested_command_words(segment)
    ]
    nested_scripts = [script for _, script in nested_command_scripts(command)]
    nested_scripts.extend(command_substitution_scripts(command))
    nested_scripts.extend(shlex.join(words) for words in policy_commands)
    return any(
        command_has_unsupported_env_nebius(script, depth + 1)
        for script in nested_scripts
    )


def env_segment_discards_or_splits_environment(words: list[str]) -> bool:
    executable = command_words_after_wrappers(words, unwrap_env=False)
    if not executable or Path(executable[0]).name != "env":
        return False
    return any(
        word in {"-i", "--ignore-environment", "-S", "--split-string"}
        or word.startswith("--split-string=")
        or (
            word.startswith("-")
            and not word.startswith("--")
            and any(flag in word[1:] for flag in ("i", "S"))
        )
        for word in executable[1:]
    )


def command_discards_or_splits_environment(command: str, depth: int = 0) -> bool:
    if any(
        env_segment_discards_or_splits_environment(segment)
        for segment in shell_command_segments(command)
    ):
        return True
    if depth >= 4:
        return False
    policy_commands = [
        nested
        for segment in shell_command_segments(command)
        for nested in policy_nested_command_words(segment)
    ]
    nested_scripts = [script for _, script in nested_command_scripts(command)]
    nested_scripts.extend(command_substitution_scripts(command))
    nested_scripts.extend(shlex.join(words) for words in policy_commands)
    return any(
        command_discards_or_splits_environment(script, depth + 1)
        for script in nested_scripts
    )


def credential_file_for_project(project_id: str) -> Path:
    return (
        Path.home()
        / NEBIUS_CONFIG_DIR
        / f"{CREDENTIAL_FILE_PREFIX}{project_id}{CREDENTIAL_FILE_SUFFIX}"
    )


def credential_file_safety_issue(project_id: str) -> str:
    return shared_credential_file_safety_issue(
        credential_file_for_project(project_id), expected_uid=os.getuid()
    )


def credential_file_is_safe(project_id: str) -> bool:
    return not credential_file_safety_issue(project_id)


def renewable_context_command(
    profile: str,
    project_id: str,
    credential_file: Path,
    command: str,
) -> str:
    helper = HOOK_DIR / "nebius_auth_token_helper.py"
    return f"""\
unset {PROJECT_ID_ENV} {TOKEN_ENV} {NEBIUS_TOKEN_ENV} {NEBIUS_PROFILE_ENV} {NEBIUS_PROJECT_ID_ENV} {NEBIUS_CREDENTIALS_FILE_ENV} {TOKEN_HELPER_ENV}
PROFILE={shlex.quote(profile)}
PROJECT_ID_VALUE={shlex.quote(project_id)}
CREDENTIALS_FILE_VALUE={shlex.quote(str(credential_file))}
TOKEN_HELPER_VALUE={shlex.quote(str(helper))}
export {NEBIUS_PROFILE_ENV}="$PROFILE"
export {NEBIUS_PROJECT_ID_ENV}="$PROJECT_ID_VALUE"
export {NEBIUS_CREDENTIALS_FILE_ENV}="$CREDENTIALS_FILE_VALUE"
export {TOKEN_HELPER_ENV}="$TOKEN_HELPER_VALUE"

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

    raw_command = tool_input.get("command") or ""
    if not isinstance(raw_command, str) or not raw_command.strip():
        return {}

    project_id, command, selector_error = parse_project_selector(raw_command)
    has_selector_assignment = command_has_project_selector_assignment(raw_command)
    token_mint = command_contains_token_mint(command)
    needs_token = command_needs_nebius_token(command)
    profiles = command_nebius_profiles(command)
    unsupported_env_nebius = command_has_unsupported_env_nebius(command)
    discards_environment = command_discards_or_splits_environment(command)
    unsupported_launcher = command_has_unsupported_nebius_launcher(command)
    invokes_token_helper = any(
        token_helper_command_words(segment)
        for segment in shell_command_segments(command)
    )
    mutates_auth_env = command_mutates_managed_auth_env(command)
    invokes_any_nebius_cli = bool(profiles)
    requires_auth = (
        token_mint
        or needs_token
        or invokes_any_nebius_cli
        or invokes_token_helper
        or unsupported_env_nebius
        or unsupported_launcher
    )

    if selector_error:
        if not requires_auth and not has_selector_assignment:
            return {}
        return deny(
            f"Nebius auth required, but {selector_error}. Start the Bash command "
            f"exactly with '{PROJECT_ID_ENV}=<project-id> <command>'. Run "
            "$agent-nebius-auth-diagnose to discover or verify the current-session "
            "project. Correct the command and retry; no setup or user confirmation "
            "is required."
        )

    profile = f"{AGENT_PROFILE_PREFIX}{project_id}"
    if mutates_auth_env:
        return deny(
            "Nebius auth required, but the command assigns or unsets a managed "
            "authentication variable. Remove that assignment or unset, use only "
            "selector-derived auth state. Correct the command and retry; no setup "
            "or user confirmation is required."
        )
    if discards_environment:
        return deny(
            "Nebius auth required, but env environment-clearing or split-string "
            "execution cannot preserve the selector-derived renewable credential "
            "context. Run the command without clearing or splitting its environment."
        )
    conflicting_profiles = [
        value
        for value in profiles
        if value and value != profile
    ]
    if conflicting_profiles:
        return deny(
            "Nebius auth required, but an explicit profile conflicts "
            f"with the leading {PROJECT_ID_ENV} selector. Remove the explicit "
            "profile and run $agent-nebius-auth-diagnose if the project is uncertain. "
            "Correct the command and retry; no setup or user confirmation is required."
        )

    if token_mint:
        if is_safe_manual_token_verification(command, profile):
            if not credential_file_is_safe(project_id):
                return deny(
                    "Nebius auth verification requires the selected project's "
                    "owned regular credential file at mode 0600. Run "
                    "$agent-nebius-auth-diagnose before retrying."
                )
            return allow_rewrite(
                renewable_context_command(
                    profile,
                    project_id,
                    credential_file_for_project(project_id),
                    command,
                )
            )
        return deny(
            "Nebius access-token commands can expose token material to Codex output. "
            "Replace this with the normal Nebius command through the selected agent "
            "profile, or use only the exact matching agent-profile token verification "
            "with stdout redirected to /dev/null. Correct the command and retry; no "
            "setup or user confirmation is required."
        )

    if command_may_disclose_injected_env(command):
        return deny(
            "Nebius auth hook refused to inject renewable context into a command "
            "that may print or dump authentication environment or headers."
        )

    credential_issue = credential_file_safety_issue(project_id)
    if credential_issue:
        if credential_issue == "mode":
            return deny(
                "Nebius auth required, but the selected project's canonical local "
                "agent credential has unsafe permissions. Run "
                "$agent-nebius-auth-diagnose. If a still-valid matching repair "
                "lease exists, $agent-nebius-auth-setup repair-local may restore "
                "mode 0600; otherwise explicitly invoke "
                "$agent-nebius-auth-setup for bounded repair."
            )
        return deny(
            "Nebius auth required, but the selected project's local agent credential "
            "file is missing or unsafe. Run $agent-nebius-auth-diagnose, then use "
            "explicitly invoke $agent-nebius-auth-setup for bounded repair."
        )

    return allow_rewrite(
        renewable_context_command(
            profile,
            project_id,
            credential_file_for_project(project_id),
            command,
        )
    )


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
