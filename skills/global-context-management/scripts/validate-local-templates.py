#!/usr/bin/env python3
"""Validate global-context-management local runtime templates.

This check is local-only and uses disposable directories. It does not read or
write the user's real Codex home.
"""

from __future__ import annotations

import hashlib
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
    json.loads(
        (root / "assets" / "global_context_policy.json.template").read_text(
            encoding="utf-8"
        )
    )

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


def write_agent_fixture(codex_home: Path, *, enable_policy: bool) -> None:
    agents_dir = codex_home / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text(
        """
[agents]
max_threads = 4
max_depth = 1

[agents.alpha_mapper]
description = "Read-only repository mapper."
config_file = "agents/alpha_mapper.toml"

[agents.beta_test_planner]
description = "Read-only verification planner."
config_file = "agents/beta_test_planner.toml"

[agents.gamma_risk_reviewer]
description = "Read-only risk reviewer."
config_file = "agents/gamma_risk_reviewer.toml"

[agents.write_worker]
description = "Write-capable worker that must not be injected."
config_file = "agents/write_worker.toml"
""",
        encoding="utf-8",
    )
    for name in ("alpha_mapper", "beta_test_planner", "gamma_risk_reviewer"):
        (agents_dir / f"{name}.toml").write_text(
            'sandbox_mode = "read-only"\n',
            encoding="utf-8",
        )
    (agents_dir / "write_worker.toml").write_text(
        'sandbox_mode = "workspace-write"\n',
        encoding="utf-8",
    )
    policy_path = codex_home / "hooks" / "global_context_policy.json"
    if enable_policy:
        policy_path.write_text(
            json.dumps(
                {
                    "auto_read_only_subagents": True,
                    "include_agent_descriptions": False,
                }
            ),
            encoding="utf-8",
        )
    elif policy_path.exists():
        policy_path.unlink()


def assert_agent_delegation_context(context: str) -> None:
    expected = ("alpha_mapper", "beta_test_planner", "gamma_risk_reviewer")
    for name in expected:
        if f"`{name}`" not in context:
            raise AssertionError(f"configured read-only agent missing from context: {name}")

    if "write_worker" in context:
        raise AssertionError("non-read-only agent leaked into context")
    if "agents/alpha_mapper.toml" in context:
        raise AssertionError("agent config path leaked into context")
    if "Hook-assisted read-only subagent delegation is enabled" not in context:
        raise AssertionError("delegation policy context missing")
    if "close the completed subagent thread" not in context:
        raise AssertionError("subagent cleanup guidance missing")
    if "continue waiting on the remaining handles" not in context:
        raise AssertionError("multi-subagent cleanup guidance missing")
    if "`wait_agent` completions" not in context:
        raise AssertionError("wait_agent cleanup guidance missing")
    if "asynchronous subagent completion notifications" not in context:
        raise AssertionError("async subagent cleanup guidance missing")


def assert_no_default_agent_names(context: str) -> None:
    for name in ("repo_mapper", "test_strategist", "risk_reviewer"):
        if name in context:
            raise AssertionError(f"default agent name leaked into context: {name}")


def expected_workspace_segment(root: Path) -> str:
    workspace_name = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name or "workspace").strip(
        "-"
    )
    if not workspace_name:
        workspace_name = "workspace"
    workspace_hash = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return f"{workspace_name}-{workspace_hash}"


def resolve_root_for_cwd(cwd: str) -> Path:
    cwd = os.path.abspath(cwd)
    try:
        root = subprocess.check_output(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        root = ""
    return Path(os.path.abspath(root or cwd))


def assert_duplicate_templates(root: Path) -> None:
    config_assets = root.parent / "config-codex" / "assets"
    pairs = (
        (
            root / "assets" / "hooks.json.template",
            config_assets / "hooks.json.template",
        ),
        (
            root / "assets" / "session_start_context.py.template",
            config_assets / "hooks" / "session_start_context.py.template",
        ),
        (
            root / "assets" / "user_prompt_context.py.template",
            config_assets / "hooks" / "user_prompt_context.py.template",
        ),
        (
            root / "assets" / "global_context_policy.json.template",
            config_assets / "hooks" / "global_context_policy.json.template",
        ),
        (
            root / "assets" / "repo_mapper.toml.template",
            config_assets / "agents" / "repo_mapper.toml.template",
        ),
        (
            root / "assets" / "test_strategist.toml.template",
            config_assets / "agents" / "test_strategist.toml.template",
        ),
        (
            root / "assets" / "risk_reviewer.toml.template",
            config_assets / "agents" / "risk_reviewer.toml.template",
        ),
        (
            root / "assets" / "task-state-template.md",
            config_assets / "task-state-template.md",
        ),
    )
    for left, right in pairs:
        if left.read_bytes() != right.read_bytes():
            raise AssertionError(f"duplicate template drift: {left} != {right}")


def validate_path_contract_helpers(temp_dir: Path) -> None:
    dot_root = temp_dir / ".dotrepo"
    dot_root.mkdir()
    if not expected_workspace_segment(dot_root).startswith(".dotrepo-"):
        raise AssertionError("workspace segment should preserve leading dots")

    actual = temp_dir / "actual"
    link = temp_dir / "linkroot"
    actual.mkdir()
    try:
        os.symlink(actual, link, target_is_directory=True)
    except OSError:
        return
    if resolve_root_for_cwd(str(link)).name != "linkroot":
        raise AssertionError("resolve_root_for_cwd should match hook abspath semantics")


def assert_manual_state_path(state_file: Path, codex_home: Path, root: Path) -> None:
    if state_file.parent.name != "manual":
        raise AssertionError(f"manual fallback used unexpected directory: {state_file}")
    if state_file.name != "current.md":
        raise AssertionError(f"manual fallback used unexpected file name: {state_file}")
    if not str(state_file).startswith(str(codex_home / "task-state") + os.sep):
        raise AssertionError(f"manual state file is outside CODEX_HOME: {state_file}")
    expected_relative = Path(expected_workspace_segment(root)) / "manual" / "current.md"
    actual_relative = state_file.relative_to(codex_home / "task-state")
    if actual_relative != expected_relative:
        raise AssertionError(
            "manual fallback is not workspace-scoped: "
            f"{actual_relative} != {expected_relative}"
        )


def assert_doc_contracts(root: Path) -> None:
    gcm_readme = (root / "README.md").read_text(encoding="utf-8")
    gcm_skill = (root / "SKILL.md").read_text(encoding="utf-8")
    config_readme = (root.parent / "config-codex" / "README.md").read_text(
        encoding="utf-8"
    )

    required_skill = (
        "$CODEX_HOME/task-state/<workspace>-<hash>/manual/current.md",
    )
    for needle in required_skill:
        if needle not in gcm_skill:
            raise AssertionError(f"global-context-management SKILL missing: {needle}")
    if "$CODEX_HOME/task-state/manual/current.md" in gcm_skill:
        raise AssertionError(
            "global-context-management SKILL still documents flat manual fallback"
        )

    required_gcm = (
        "$CODEX_HOME/task-state/<workspace>-<hash>/manual/current.md",
        "Task-state files are useful only when Codex reads and updates them.",
        "hidden state automatically active in the model forever.",
        "continuity note",
        "Synthetic hook probes that pass a made-up `session_id`",
    )
    for needle in required_gcm:
        if needle not in gcm_readme:
            raise AssertionError(f"global-context-management README missing: {needle}")

    required_config = (
        "Treat runtime activation as unverified",
        "task-state path under",
        "Direct hook unit probes against a live `$CODEX_HOME`",
    )
    for needle in required_config:
        if needle not in config_readme:
            raise AssertionError(f"config-codex README missing: {needle}")


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
    resolved_root = resolve_root_for_cwd(cwd)
    session_payload = {
        "session_id": "session one/with spaces",
        "cwd": cwd,
        "hook_event_name": "SessionStart",
        "source": "startup",
        "model": "test-model",
    }
    session_result = run_hook(session_script, session_payload, env)
    session_context = json.loads(session_result.stdout)["hookSpecificOutput"][
        "additionalContext"
    ]
    assert_no_default_agent_names(session_context)
    state_file = extract_state_path(session_result.stdout)
    if not state_file.exists():
        raise AssertionError(f"state file was not created: {state_file}")
    if not str(state_file).startswith(str(codex_home / "task-state") + os.sep):
        raise AssertionError(f"state file is outside CODEX_HOME: {state_file}")
    assert_private_file(state_file)

    manual_session_payload = {
        "cwd": cwd,
        "hook_event_name": "SessionStart",
        "source": "startup",
        "model": "test-model",
    }
    manual_session_result = run_hook(session_script, manual_session_payload, env)
    manual_session_state = extract_state_path(manual_session_result.stdout)
    assert_manual_state_path(manual_session_state, codex_home, resolved_root)
    assert_private_file(manual_session_state)

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
    if "Hook-assisted read-only subagent delegation is enabled" in context:
        raise AssertionError("delegation context appeared before policy opt-in")
    if SENTINEL_MARKER in context:
        raise AssertionError("hook echoed prompt content into model context")
    assert_no_prompt_leak(state_file)

    manual_prompt_payload = {
        "cwd": cwd,
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "turn-manual",
        "prompt": "Please review and test hooks end to end.",
    }
    manual_prompt_result = run_hook(user_script, manual_prompt_payload, env)
    manual_prompt_state = extract_state_path(manual_prompt_result.stdout)
    assert_manual_state_path(manual_prompt_state, codex_home, resolved_root)
    if manual_prompt_state != manual_session_state:
        raise AssertionError(
            "SessionStart and UserPromptSubmit manual fallback paths differ"
        )

    write_agent_fixture(codex_home, enable_policy=False)
    env_override = {**env, "CODEX_GCM_AUTO_SUBAGENTS": "1"}
    env_override_result = run_hook(user_script, complex_payload, env_override)
    env_override_context = json.loads(env_override_result.stdout)["hookSpecificOutput"][
        "additionalContext"
    ]
    if "Hook-assisted read-only subagent delegation is enabled" in env_override_context:
        raise AssertionError("environment override unexpectedly enabled delegation")

    write_agent_fixture(codex_home, enable_policy=True)
    delegated_session_result = run_hook(session_script, session_payload, env)
    delegated_session_context = json.loads(delegated_session_result.stdout)[
        "hookSpecificOutput"
    ]["additionalContext"]
    assert_no_default_agent_names(delegated_session_context)

    delegated_result = run_hook(user_script, complex_payload, env)
    delegated_context = json.loads(delegated_result.stdout)["hookSpecificOutput"][
        "additionalContext"
    ]
    assert_agent_delegation_context(delegated_context)
    if SENTINEL_MARKER in delegated_context:
        raise AssertionError("hook echoed prompt content into delegated model context")
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
    assert_doc_contracts(root)
    assert_duplicate_templates(root)

    with tempfile.TemporaryDirectory(prefix="gcm-template-test-") as temp:
        temp_dir = Path(temp)
        validate_path_contract_helpers(temp_dir)
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
