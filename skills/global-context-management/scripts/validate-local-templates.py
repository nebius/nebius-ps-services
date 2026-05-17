#!/usr/bin/env python3
"""Validate global-context-management local runtime templates.

This check is local-only and uses disposable directories. It does not read or
write the user's real Codex home.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib


ROOT_MARKERS = ("SKILL.md", "assets", "references")
SENTINEL_MARKER = "PROMPT_CONTENT_SENTINEL_DO_NOT_PERSIST"


def skill_dir() -> Path:
    path = Path(__file__).resolve().parent.parent
    missing = [marker for marker in ROOT_MARKERS if not (path / marker).exists()]
    if missing:
        raise AssertionError(f"cannot locate skill root from {path}: missing {missing}")
    return path


def parse_templates(root: Path) -> dict:
    hooks_template = root / "assets" / "hooks.json.template"
    hooks = json.loads(hooks_template.read_text(encoding="utf-8"))

    for path in sorted((root / "assets").glob("*.toml.template")):
        tomllib.loads(path.read_text(encoding="utf-8"))

    for path in (
        root / "assets" / "session_start_context.py.template",
        root / "assets" / "user_prompt_context.py.template",
    ):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    return hooks


def run_hook(script: Path, payload: dict, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )


def extract_state_path(output: str) -> Path:
    payload = json.loads(output)
    context = payload["hookSpecificOutput"]["additionalContext"]
    match = re.search(r"Durable task-state file: `([^`]+)`", context)
    if not match:
        raise AssertionError(f"state path missing from context: {context}")
    return Path(match.group(1))


def assert_private_file(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise AssertionError(f"{path} mode is {oct(mode)}, expected 0o600")


def assert_no_prompt_leak(state_file: Path) -> None:
    text = state_file.read_text(encoding="utf-8")
    if SENTINEL_MARKER in text:
        raise AssertionError("hook persisted prompt content into task state")


def validate_direct_hooks(root: Path, codex_home: Path, home: Path) -> None:
    hooks_dir = codex_home / "hooks"
    hooks_dir.mkdir(parents=True)

    session_script = hooks_dir / "session_start_context.py"
    user_script = hooks_dir / "user_prompt_context.py"
    shutil.copyfile(root / "assets" / "session_start_context.py.template", session_script)
    shutil.copyfile(root / "assets" / "user_prompt_context.py.template", user_script)

    env = {
        **os.environ,
        "CODEX_HOME": str(codex_home),
        "HOME": str(home),
    }
    cwd = str(root)
    session_payload = {
        "session_id": "session one/with spaces",
        "cwd": cwd,
        "hook_event_name": "SessionStart",
        "source": "startup",
        "model": "test-model",
    }
    session_result = run_hook(session_script, session_payload, env)
    state_file = extract_state_path(session_result.stdout)
    if not state_file.exists():
        raise AssertionError(f"state file was not created: {state_file}")
    if not str(state_file).startswith(str(codex_home / "task-state") + os.sep):
        raise AssertionError(f"state file is outside CODEX_HOME: {state_file}")
    assert_private_file(state_file)

    simple_payload = {
        "session_id": "session one/with spaces",
        "cwd": cwd,
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "turn-simple",
        "prompt": "hello",
    }
    simple_result = run_hook(user_script, simple_payload, env)
    if simple_result.stdout:
        raise AssertionError("simple prompt unexpectedly produced hook context")

    complex_payload = {
        "session_id": "session one/with spaces",
        "cwd": cwd,
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "turn-complex",
        "prompt": (
            "Please review and test hooks end to end. "
            f"Do not persist {SENTINEL_MARKER} in task state."
        ),
    }
    complex_result = run_hook(user_script, complex_payload, env)
    context = json.loads(complex_result.stdout)["hookSpecificOutput"]["additionalContext"]
    if "Apply the `global-context-management` skill" not in context:
        raise AssertionError("complex prompt did not request the skill")
    if SENTINEL_MARKER in context:
        raise AssertionError("hook echoed prompt content into model context")
    assert_no_prompt_leak(state_file)


def validate_hooks_json_command(root: Path, hooks: dict, temp_dir: Path) -> None:
    custom_home = temp_dir / "custom-codex-home"
    normal_home = temp_dir / "normal-home"
    hooks_dir = custom_home / "hooks"
    hooks_dir.mkdir(parents=True)
    normal_home.mkdir()
    shutil.copyfile(
        root / "assets" / "session_start_context.py.template",
        hooks_dir / "session_start_context.py",
    )

    command = hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    env = {
        **os.environ,
        "CODEX_HOME": str(custom_home),
        "HOME": str(normal_home),
    }
    payload = {
        "session_id": "mismatch",
        "cwd": str(root),
        "hook_event_name": "SessionStart",
        "source": "startup",
    }
    result = subprocess.run(
        command,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        shell=True,
        check=True,
        env=env,
    )
    state_file = extract_state_path(result.stdout)
    if not str(state_file).startswith(str(custom_home / "task-state") + os.sep):
        raise AssertionError(
            "hooks.json command did not respect CODEX_HOME override: "
            f"{state_file}"
        )


def main() -> int:
    root = skill_dir()
    hooks = parse_templates(root)

    with tempfile.TemporaryDirectory(prefix="gcm-template-test-") as temp:
        temp_dir = Path(temp)
        validate_direct_hooks(
            root=root,
            codex_home=temp_dir / ".codex",
            home=temp_dir / "home-default",
        )
        validate_hooks_json_command(root=root, hooks=hooks, temp_dir=temp_dir)

    print("global-context-management local templates validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
