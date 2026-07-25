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
STATE_REUSE_MARKER = "TASK_STATE_REUSE_SENTINEL_KEEP_FOR_AGENT_READ"
RELATED_STATE_CONTENT_MARKER = "RELATED_STATE_CONTENT_SENTINEL_DO_NOT_INJECT"


def skill_dir() -> Path:
    path = Path(__file__).resolve().parent.parent
    missing = [marker for marker in ROOT_MARKERS if not (path / marker).exists()]
    if missing:
        raise AssertionError(f"cannot locate skill root from {path}: missing {missing}")
    return path


def parse_templates(root: Path) -> dict:
    hooks_template = root / "assets" / "hooks.json.template"
    hooks = json.loads(hooks_template.read_text(encoding="utf-8"))
    matcher = hooks["hooks"]["SessionStart"][0].get("matcher")
    if matcher != "startup|resume|clear|compact":
        raise AssertionError("SessionStart matcher must include compact exactly once")
    policy = json.loads(
        (root / "assets" / "global_context_policy.json.template").read_text(
            encoding="utf-8"
        )
    )
    if policy.get("auto_read_only_subagents") is not True:
        raise AssertionError(
            "optional global_context_policy template must enable read-only "
            "subagent delegation"
        )

    for path in sorted((root / "assets").glob("*.toml.template")):
        tomllib.loads(path.read_text(encoding="utf-8"))

    for path in (
        root / "assets" / "global_context_state.py.template",
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


def extract_context(output: str) -> str:
    payload = json.loads(output)
    return payload["hookSpecificOutput"]["additionalContext"]


def assert_private_file(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise AssertionError(f"{path} mode is {oct(mode)}, expected 0o600")


def assert_private_state_tree(state_file: Path, codex_home: Path) -> None:
    assert_private_file(state_file)
    for directory in (
        codex_home / "task-state",
        state_file.parent.parent,
        state_file.parent,
    ):
        mode = stat.S_IMODE(directory.stat().st_mode)
        if mode != 0o700:
            raise AssertionError(f"{directory} mode is {oct(mode)}, expected 0o700")


def assert_no_prompt_leak(state_file: Path) -> None:
    text = state_file.read_text(encoding="utf-8")
    if SENTINEL_MARKER in text:
        raise AssertionError("hook persisted prompt content into task state")


def assert_missing_state_path(state_file: Path, codex_home: Path) -> None:
    if state_file.exists():
        raise AssertionError(f"hook unexpectedly created task-state file: {state_file}")
    if state_file.parent.exists():
        raise AssertionError(
            f"hook unexpectedly created task-state directory: {state_file.parent}"
        )
    try:
        state_file.relative_to(codex_home)
    except ValueError as exc:
        raise AssertionError(f"state path is outside CODEX_HOME: {state_file}") from exc


def assert_existing_state_preserved(
    *,
    result: subprocess.CompletedProcess[str],
    state_file: Path,
    expected_text: str,
) -> str:
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    actual_state = extract_state_path(result.stdout)
    if actual_state != state_file:
        raise AssertionError(
            "hook did not reuse existing task-state path: "
            f"{actual_state} != {state_file}"
        )
    if state_file.read_text(encoding="utf-8") != expected_text:
        raise AssertionError("hook overwrote existing task-state contents")
    if STATE_REUSE_MARKER in context:
        raise AssertionError("hook injected task-state contents into model context")
    assert_private_file(state_file)
    return context


def write_agent_fixture(codex_home: Path, *, enable_policy: bool) -> None:
    agents_dir = codex_home / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text(
        """
[agents]
max_concurrent_threads_per_session = 16

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
    if len(context) > 1800:
        raise AssertionError("delegation context is too large for a lightweight hint")
    expected = ("alpha_mapper", "beta_test_planner", "gamma_risk_reviewer")
    for name in expected:
        if f"`{name}`" not in context:
            raise AssertionError(f"configured read-only agent missing from context: {name}")

    if "write_worker" in context:
        raise AssertionError("non-read-only agent leaked into context")
    if "agents/alpha_mapper.toml" in context:
        raise AssertionError("agent config path leaked into context")
    if "Local policy asks the main Codex agent to dynamically spawn bounded" not in context:
        raise AssertionError("delegation policy context missing")
    if "Available read-only roles:" not in context:
        raise AssertionError("read-only role list missing")
    if "Suggested role timing:" not in context:
        raise AssertionError("role timing hint missing")
    if "Choose the smallest useful set of targeted roles" not in context:
        raise AssertionError("targeted subagent selection guidance missing")
    if "do not spawn every role" not in context:
        raise AssertionError("bounded subagent selection guidance missing")
    forbidden = (
        "For every subagent you spawn",
        "close the completed subagent thread",
        "continue waiting on the remaining handles",
        "`wait_agent` completions",
        "asynchronous subagent completion notifications",
        "`tool_search` is available",
    )
    for needle in forbidden:
        if needle in context:
            raise AssertionError(f"delegation context repeats workflow detail: {needle}")


def assert_no_default_agent_names(context: str) -> None:
    for name in ("repo_mapper", "test_strategist", "risk_reviewer"):
        if name in context:
            raise AssertionError(f"default agent name leaked into context: {name}")


def assert_tool_search_discovery_guidance(context: str, label: str) -> None:
    if "`tool_search` is available" not in context:
        raise AssertionError(f"{label} tool_search discovery guard missing")
    if (
        "multi-agent/subagent tools" not in context
        or "before reporting delegation unavailable" not in context
    ):
        raise AssertionError(
            f"{label} deferred subagent tool discovery guidance missing"
        )


def expected_workspace_segment(root: Path) -> str:
    workspace_name = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name or "workspace").strip(
        "-"
    )
    if not workspace_name:
        workspace_name = "workspace"
    workspace_hash = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return f"{workspace_name}-{workspace_hash}"


def expected_session_segment(session_id: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", session_id) and session_id not in {
        ".",
        "..",
    }:
        return session_id
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id).strip("-")[:48]
    prefix = safe or "session"
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def expected_state_file(codex_home: Path, root: Path, session_id: str) -> Path:
    return (
        codex_home
        / "task-state"
        / expected_workspace_segment(root)
        / expected_session_segment(session_id)
        / "current.md"
    )


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
            root / "assets" / "global_context_state.py.template",
            config_assets / "hooks" / "global_context_state.py.template",
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

    if expected_session_segment("a/b") == expected_session_segment("a b"):
        raise AssertionError("unsafe session IDs must not collide after normalization")
    for session_id in (".", ".."):
        if expected_session_segment(session_id) == session_id:
            raise AssertionError("dot session IDs must be hashed")


def assert_doc_contracts(root: Path) -> None:
    gcm_readme = (root / "README.md").read_text(encoding="utf-8")
    gcm_skill = (root / "SKILL.md").read_text(encoding="utf-8")
    state_template = (root / "assets" / "task-state-template.md").read_text(
        encoding="utf-8"
    )
    session_hook = (root / "assets" / "session_start_context.py.template").read_text(
        encoding="utf-8"
    )
    prompt_hook = (root / "assets" / "user_prompt_context.py.template").read_text(
        encoding="utf-8"
    )
    config_root = root.parent / "config-codex"
    config_skill = (config_root / "SKILL.md").read_text(encoding="utf-8")
    config_readme = (config_root / "README.md").read_text(encoding="utf-8")
    config_reference = (config_root / "references" / "local-setup.md").read_text(
        encoding="utf-8"
    )
    agents_template = (config_root / "assets" / "AGENTS.md.template").read_text(
        encoding="utf-8"
    )

    forbidden_needles = (
        "$CODEX_HOME/task-state/<workspace>-<hash>/manual/current.md",
        "$CODEX_HOME/task-state/manual/current.md",
        "manual fallback",
    )
    for needle in forbidden_needles:
        if needle in gcm_skill:
            raise AssertionError(
                f"global-context-management SKILL still documents legacy path: {needle}"
            )

    required_skill = (
        "No legacy task-state",
        "it must allow writes under\n`$CODEX_HOME/task-state`",
        "same-workspace prior task-state candidate paths",
        "must not inject\nhistorical task-state contents",
        "rolling summary, not an append-only log",
        "preserve exactly one valid marker",
        "close every spawned subagent handle",
        "Completed agents remain open",
        "final lifecycle sweep",
    )
    for needle in required_skill:
        if needle not in gcm_skill:
            raise AssertionError(f"global-context-management SKILL missing: {needle}")

    required_gcm = (
        "Task-state files are useful only when Codex reads and updates them.",
        "Normal\n`SessionStart` startup advertises a missing path without creating it",
        "`SessionStart` with `source=compact` and the first\ncomplex `UserPromptSubmit` create an empty scaffold",
        "manual or legacy fallback path",
        "same-workspace prior `current.md` candidate paths",
        "must not inject historical task-state contents",
        "current session's advertised `current.md` remains the only write target",
        "continuity note",
        "Any local PreToolUse write guard must explicitly allow\n`$CODEX_HOME/task-state` writes",
        "rolling summary, not an append-only transcript",
        "codex-remediation-budget:v1",
        "summarize any older task-state file",
        "bounded\nsame-workspace related task-state candidate discovery",
        "close every spawned subagent handle",
        "Completed agents can remain open",
        "final lifecycle sweep",
        "subagent was spawned and closed",
    )
    for needle in required_gcm:
        if needle not in gcm_readme:
            raise AssertionError(f"global-context-management README missing: {needle}")

    required_config = (
        "Treat runtime activation as unverified",
        "task-state path under",
        "Do not run complex synthetic hook probes against a live `$CODEX_HOME`",
        "same-workspace prior `current.md` candidate paths",
        "compaction and complex-prompt empty scaffolds",
        "No manual or legacy",
        "rolling summary, not an append-only transcript",
        "summarize oversized historical files",
        "close every spawned subagent",
        "final lifecycle sweep",
    )
    for needle in required_config:
        if needle not in config_readme:
            raise AssertionError(f"config-codex README missing: {needle}")

    required_config_reference = (
        "bounded same-workspace prior task-state\n  candidate paths",
        "hooks must not inject historical\n  task-state contents",
        "bounded related prior task-state candidate discovery",
        "close every spawned subagent handle",
        "residual open or running handle",
        "subagent was spawned and closed",
    )
    for needle in required_config_reference:
        if needle not in config_reference:
            raise AssertionError(f"config-codex local setup reference missing: {needle}")

    forbidden_probe = "Wait for it, then report whether the subagent was spawned"
    for label, text in (
        ("global-context-management README", gcm_readme),
        ("config-codex local setup reference", config_reference),
    ):
        if forbidden_probe in text:
            raise AssertionError(f"{label} still has stale subagent probe")

    required_config_skill = (
        "Prompt-time hooks may list bounded same-workspace prior task-state candidate",
        "must not inject historical task-state\n   contents",
        "current.md` as a compact rolling\n   summary, not an append-only transcript",
        "summarize oversized\n   historical task-state files before relying on them",
        "close every spawned helper handle",
        "unavailable or failed cleanup",
    )
    for needle in required_config_skill:
        if needle not in config_skill:
            raise AssertionError(f"config-codex SKILL missing: {needle}")

    required_summary_template = (
        "## Summary hygiene",
        "compact rolling summary, not an append-only transcript",
        "Replace stale or superseded details",
        "raw logs, broad command output, full prompts",
        "## Active remediation budget",
        "codex-remediation-budget:v1",
    )
    for needle in required_summary_template:
        if needle not in state_template:
            raise AssertionError(f"task-state template missing: {needle}")

    required_session_hook_summary = (
        "current.md as a rolling summary, not an append-only",
        "Replace stale task-state details instead of appending transcripts.",
    )
    for needle in required_session_hook_summary:
        if needle not in session_hook:
            raise AssertionError(f"SessionStart template missing: {needle}")

    required_prompt_hook_summary = (
        "Keep current.md summarized; replace stale details instead of appending logs.",
        "Related same-workspace task-state candidates (not loaded):",
        "verify against current repo/runtime evidence",
        "the prompt or local policy request authorizes delegation",
        "Local policy asks the main Codex agent to dynamically spawn bounded",
        "Choose the smallest useful set of targeted roles",
    )
    for needle in required_prompt_hook_summary:
        if needle not in prompt_hook:
            raise AssertionError(f"UserPromptSubmit template missing: {needle}")

    required_agents_template = (
        "Read the durable task-state file injected by global hooks",
        "Keep the parent thread focused on objective, constraints, decisions",
        "Treat that policy request as sufficient\n  authorization",
        "do not ask for another user prompt only because the original",
        "close completed helpers when close controls are available",
        "stop after three distinct failed remediation attempts or 60 active",
        "When evidence establishes a causally independent blocker",
        "lower or higher limits that apply to the new\n  blocker",
        "Permission denials and marker validation\n  or repair consume no attempt",
        "Agents may clean up temporary trees they created during the current task",
        'find "$task_temp_dir" -depth -delete',
        "Preserve an active `codex-remediation-budget:v1` marker exactly",
    )
    for needle in required_agents_template:
        if needle not in agents_template:
            raise AssertionError(f"AGENTS.md template missing: {needle}")


def validate_direct_hooks(root: Path, codex_home: Path, home: Path) -> None:
    hooks_dir = codex_home / "hooks"
    hooks_dir.mkdir(parents=True)

    session_script = hooks_dir / "session_start_context.py"
    user_script = hooks_dir / "user_prompt_context.py"
    helper_script = hooks_dir / "global_context_state.py"
    shutil.copyfile(root / "assets" / "global_context_state.py.template", helper_script)
    shutil.copyfile(root / "assets" / "session_start_context.py.template", session_script)
    shutil.copyfile(root / "assets" / "user_prompt_context.py.template", user_script)

    env = {
        **os.environ,
        "CODEX_HOME": str(codex_home),
        "HOME": str(home),
    }
    cwd = str(root)
    resolved_root = resolve_root_for_cwd(cwd)
    missing_session_result = run_hook(
        session_script,
        {
            "cwd": cwd,
            "hook_event_name": "SessionStart",
            "source": "startup",
            "model": "test-model",
        },
        env,
    )
    missing_session_context = extract_context(missing_session_result.stdout)
    if "unavailable (hook payload missing session_id)" not in missing_session_context:
        raise AssertionError("missing-session SessionStart did not report unavailable state")
    legacy_state = (
        codex_home
        / "task-state"
        / expected_workspace_segment(resolved_root)
        / "manual"
        / "current.md"
    )
    assert_missing_state_path(legacy_state, codex_home)

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
    if not str(state_file).startswith(str(codex_home / "task-state") + os.sep):
        raise AssertionError(f"state file is outside CODEX_HOME: {state_file}")
    if len(session_context) > 800:
        raise AssertionError("SessionStart context is too large")
    if "Apply the `global-context-management` skill" in session_context:
        raise AssertionError("SessionStart should not directly select skills")
    if "subagent" in session_context.lower():
        raise AssertionError("SessionStart should not inject subagent workflow detail")
    if "sdlc-start" in session_context:
        raise AssertionError("SessionStart should not route SDLC")
    assert_missing_state_path(state_file, codex_home)

    compact_payload = {
        **session_payload,
        "session_id": "compact session",
        "source": "compact",
    }
    compact_result = run_hook(session_script, compact_payload, env)
    compact_state = extract_state_path(compact_result.stdout)
    if compact_state.read_bytes() != b"":
        raise AssertionError("compact SessionStart scaffold must be empty")
    assert_private_state_tree(compact_state, codex_home)
    if "Compaction initialized" not in extract_context(compact_result.stdout):
        raise AssertionError("compact SessionStart did not report initialization")
    preserved_state = (
        "# Current Codex task state\n\n"
        "## Workspace\n\n"
        f"- Root: `{resolved_root}`\n\n"
        "## Objective\n\n"
        f"- Preserve existing state for the agent to read: {STATE_REUSE_MARKER}\n"
    )
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(preserved_state, encoding="utf-8")
    state_file.chmod(0o644)
    preserved_session_result = run_hook(session_script, session_payload, env)
    assert_existing_state_preserved(
        result=preserved_session_result,
        state_file=state_file,
        expected_text=preserved_state,
    )

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
    fresh_simple_session = "fresh simple session"
    fresh_simple_payload = {
        "session_id": fresh_simple_session,
        "cwd": cwd,
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "turn-simple-fresh",
        "prompt": "hello",
    }
    fresh_simple_result = run_hook(user_script, fresh_simple_payload, env)
    if fresh_simple_result.stdout:
        raise AssertionError("fresh simple prompt unexpectedly produced hook context")
    fresh_simple_state = expected_state_file(
        codex_home, resolved_root, fresh_simple_session
    )
    assert_missing_state_path(fresh_simple_state, codex_home)
    missing_session_simple_result = run_hook(
        user_script,
        {
            "cwd": cwd,
            "hook_event_name": "UserPromptSubmit",
            "turn_id": "turn-simple-no-session",
            "prompt": "hello",
        },
        env,
    )
    if missing_session_simple_result.stdout:
        raise AssertionError("missing-session simple prompt unexpectedly produced context")
    assert_missing_state_path(legacy_state, codex_home)

    related_state_one = expected_state_file(
        codex_home, resolved_root, "older related task-state"
    )
    related_state_one.parent.mkdir(parents=True, exist_ok=True)
    related_state_one.write_text(
        (
            "# Current Codex task state\n\n"
            "## Objective\n\n"
            "- Review hooks and task-state discovery.\n"
            f"- Related content marker: {RELATED_STATE_CONTENT_MARKER}\n"
        ),
        encoding="utf-8",
    )
    related_state_two = expected_state_file(
        codex_home, resolved_root, "second related task-state"
    )
    related_state_two.parent.mkdir(parents=True, exist_ok=True)
    related_state_two.write_text(
        (
            "# Current Codex task state\n\n"
            "## Objective\n\n"
            "- Review hooks candidate ranking.\n"
        ),
        encoding="utf-8",
    )
    unrelated_same_workspace = expected_state_file(
        codex_home, resolved_root, "unrelated same-workspace task-state"
    )
    unrelated_same_workspace.parent.mkdir(parents=True, exist_ok=True)
    unrelated_same_workspace.write_text(
        "# Current Codex task state\n\n## Objective\n\n- Banana orange kiwi.\n",
        encoding="utf-8",
    )
    continuation_only_state = expected_state_file(
        codex_home, resolved_root, "continuation words only task-state"
    )
    continuation_only_state.parent.mkdir(parents=True, exist_ok=True)
    continuation_only_state.write_text(
        (
            "# Current Codex task state\n\n"
            "## Objective\n\n"
            "- Continue previous prior resume same.\n"
        ),
        encoding="utf-8",
    )
    heading_only_state = expected_state_file(
        codex_home, resolved_root, "heading only task-state"
    )
    heading_only_state.parent.mkdir(parents=True, exist_ok=True)
    heading_only_state.write_text(
        (
            "# Current Codex task state\n\n"
            "## Workspace\n\n"
            "## Objective\n\n"
            "## Constraints\n\n"
            "## Current plan\n\n"
            "## Decisions made\n\n"
            "## Relevant files and symbols\n\n"
            "## Commands run\n\n"
            "## Test status\n\n"
            "## Risks\n\n"
            "## Next action\n\n"
            "## Summary hygiene\n"
        ),
        encoding="utf-8",
    )
    unrelated_workspace = (
        codex_home
        / "task-state"
        / "other-workspace-000000000000"
        / "related-looking-session"
        / "current.md"
    )
    unrelated_workspace.parent.mkdir(parents=True, exist_ok=True)
    unrelated_workspace.write_text(
        "# Current Codex task state\n\n## Objective\n\n- Review hooks elsewhere.\n",
        encoding="utf-8",
    )
    outside_related = codex_home.parent / "outside-related-session"
    outside_related.mkdir()
    (outside_related / "current.md").write_text(
        "# Current Codex task state\n\n## Objective\n\n- Review hooks outside.\n",
        encoding="utf-8",
    )
    linked_session = related_state_one.parent.parent / "linked-session"
    linked_session.symlink_to(outside_related, target_is_directory=True)

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
    state_file.chmod(0o644)
    complex_result = run_hook(user_script, complex_payload, env)
    context = assert_existing_state_preserved(
        result=complex_result,
        state_file=state_file,
        expected_text=preserved_state,
    )
    if "Global context hint for a complex prompt." not in context:
        raise AssertionError("complex prompt did not provide global context hint")
    if (
        "Keep current.md summarized; replace stale details instead of appending logs."
        not in context
    ):
        raise AssertionError("complex prompt missing task-state summary guidance")
    if "Related same-workspace task-state candidates (not loaded):" not in context:
        raise AssertionError("complex prompt missing related task-state candidates")
    for related_state in (related_state_one, related_state_two):
        if str(related_state) not in context:
            raise AssertionError(f"related task-state path missing: {related_state}")
    for unrelated_state in (unrelated_same_workspace, unrelated_workspace):
        if str(unrelated_state) in context:
            raise AssertionError(f"unrelated task-state path leaked: {unrelated_state}")
    if str(outside_related) in context:
        raise AssertionError("symlinked outside task-state candidate leaked")
    if RELATED_STATE_CONTENT_MARKER in context:
        raise AssertionError("hook injected related task-state contents")
    if len(context) > 1400:
        raise AssertionError("non-delegated UserPromptSubmit context is too large")
    if "sdlc-start" in context:
        raise AssertionError("UserPromptSubmit should not route sdlc-start")
    if "Apply the `global-context-management` skill" in context:
        raise AssertionError("UserPromptSubmit should not directly select skills")
    if "Local policy asks the main Codex agent" in context:
        raise AssertionError("delegation context appeared before policy opt-in")
    if "For every subagent you spawn" in context:
        raise AssertionError("UserPromptSubmit repeated subagent workflow detail")
    if SENTINEL_MARKER in context:
        raise AssertionError("hook echoed prompt content into model context")
    assert_no_prompt_leak(state_file)

    continuation_payload = {
        "session_id": "continuation prompt session",
        "cwd": cwd,
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "turn-continuation",
        "prompt": "Please continue the previous review.",
    }
    continuation_context = extract_context(
        run_hook(user_script, continuation_payload, env).stdout
    )
    if str(related_state_one) not in continuation_context:
        raise AssertionError("continuation prompt did not include overlapping state")
    if str(unrelated_same_workspace) in continuation_context:
        raise AssertionError(
            "continuation prompt listed unrelated same-workspace task state"
        )
    if str(continuation_only_state) in continuation_context:
        raise AssertionError(
            "continuation prompt listed task state matched only by continuation words"
        )
    heading_only_payload = {
        "session_id": "heading-only continuation prompt session",
        "cwd": cwd,
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "turn-heading-only-continuation",
        "prompt": "Please continue the previous plan.",
    }
    heading_only_context = extract_context(
        run_hook(user_script, heading_only_payload, env).stdout
    )
    if str(heading_only_state) in heading_only_context:
        raise AssertionError(
            "continuation prompt listed task state matched only by template headings"
        )

    fresh_complex_payload = {
        "session_id": "fresh complex session",
        "cwd": cwd,
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "turn-complex-fresh",
        "prompt": (
            "Please review and test hooks end to end for a fresh complex "
            f"session. Do not persist {SENTINEL_MARKER} in task state."
        ),
    }
    fresh_complex_result = run_hook(user_script, fresh_complex_payload, env)
    fresh_complex_state = extract_state_path(fresh_complex_result.stdout)
    expected_fresh_complex_state = expected_state_file(
        codex_home, resolved_root, "fresh complex session"
    )
    if fresh_complex_state != expected_fresh_complex_state:
        raise AssertionError(
            "fresh complex prompt used unexpected state path: "
            f"{fresh_complex_state} != {expected_fresh_complex_state}"
        )
    if fresh_complex_state.read_bytes() != b"":
        raise AssertionError("fresh complex prompt scaffold must be empty")
    assert_private_state_tree(fresh_complex_state, codex_home)
    if SENTINEL_MARKER in fresh_complex_state.read_text(encoding="utf-8"):
        raise AssertionError("fresh scaffold persisted prompt content")
    if "empty private task-state scaffold" not in extract_context(
        fresh_complex_result.stdout
    ):
        raise AssertionError("fresh complex prompt did not report initialization")

    missing_session_complex_payload = {
        "cwd": cwd,
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "turn-complex-no-session",
        "prompt": "Please review and test hooks end to end.",
    }
    missing_session_complex_result = run_hook(
        user_script, missing_session_complex_payload, env
    )
    missing_session_complex_context = extract_context(
        missing_session_complex_result.stdout
    )
    if (
        "unavailable (hook payload missing session_id)"
        not in missing_session_complex_context
    ):
        raise AssertionError(
            "missing-session complex prompt did not report unavailable state"
        )
    if SENTINEL_MARKER in missing_session_complex_context:
        raise AssertionError("missing-session complex hook echoed prompt content")
    assert_missing_state_path(legacy_state, codex_home)

    write_agent_fixture(codex_home, enable_policy=False)
    env_override = {**env, "CODEX_GCM_AUTO_SUBAGENTS": "1"}
    env_override_result = run_hook(user_script, complex_payload, env_override)
    env_override_context = json.loads(env_override_result.stdout)["hookSpecificOutput"][
        "additionalContext"
    ]
    if (
        "Local policy asks the main Codex agent"
        in env_override_context
    ):
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
    if RELATED_STATE_CONTENT_MARKER in delegated_context:
        raise AssertionError("hook injected related task-state contents with delegation")
    assert_no_prompt_leak(state_file)


def validate_hooks_json_command(root: Path, hooks: dict, temp_dir: Path) -> None:
    custom_home = temp_dir / "custom-codex-home"
    normal_home = temp_dir / "normal-home"
    hooks_dir = custom_home / "hooks"
    hooks_dir.mkdir(parents=True)
    normal_home.mkdir()
    shutil.copyfile(
        root / "assets" / "global_context_state.py.template",
        hooks_dir / "global_context_state.py",
    )
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


def validate_security_and_permission_helper(root: Path, temp_dir: Path) -> None:
    codex_home = temp_dir / "security-codex"
    hooks_dir = codex_home / "hooks"
    hooks_dir.mkdir(parents=True)
    helper = hooks_dir / "global_context_state.py"
    user_hook = hooks_dir / "user_prompt_context.py"
    shutil.copyfile(root / "assets/global_context_state.py.template", helper)
    shutil.copyfile(root / "assets/user_prompt_context.py.template", user_hook)
    env = {**os.environ, "CODEX_HOME": str(codex_home)}
    payload = {
        "session_id": "concurrent session",
        "cwd": str(root),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "Please review, implement, and validate this complex task.",
    }

    processes = []
    for _ in range(8):
        processes.append(
            subprocess.Popen(
                [sys.executable, str(user_hook)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                preexec_fn=lambda: os.umask(0),
            )
        )
    for process in processes:
        stdout, stderr = process.communicate(json.dumps(payload), timeout=10)
        if process.returncode != 0 or stderr:
            raise AssertionError(f"concurrent hook failed: {stdout} {stderr}")
    state_file = expected_state_file(
        codex_home, resolve_root_for_cwd(str(root)), "concurrent session"
    )
    if state_file.read_bytes() != b"":
        raise AssertionError("concurrent initialization changed scaffold content")
    assert_private_state_tree(state_file, codex_home)
    audit_command = [
        sys.executable,
        str(helper),
        "--codex-home",
        str(codex_home),
    ]

    task_root = codex_home / "task-state"
    bad_workspace = task_root / "workspace-is-file"
    bad_workspace.write_text("unexpected node\n", encoding="utf-8")
    bad_audit = subprocess.run(
        [*audit_command, "audit-permissions"],
        text=True,
        capture_output=True,
        check=False,
    )
    if bad_audit.returncode == 0 or json.loads(bad_audit.stdout)["unsafe"] == 0:
        raise AssertionError("permission audit ignored non-directory workspace")
    bad_workspace.unlink()

    bad_session = state_file.parent.parent / "session-is-file"
    bad_session.write_text("unexpected node\n", encoding="utf-8")
    bad_audit = subprocess.run(
        [*audit_command, "audit-permissions"],
        text=True,
        capture_output=True,
        check=False,
    )
    if bad_audit.returncode == 0 or json.loads(bad_audit.stdout)["unsafe"] == 0:
        raise AssertionError("permission audit ignored non-directory session")
    bad_session.unlink()

    bad_current = state_file.parent.parent / "bad-current-session" / "current.md"
    bad_current.mkdir(parents=True)
    bad_audit = subprocess.run(
        [*audit_command, "audit-permissions"],
        text=True,
        capture_output=True,
        check=False,
    )
    if bad_audit.returncode == 0 or json.loads(bad_audit.stdout)["unsafe"] == 0:
        raise AssertionError("permission audit ignored non-regular current.md")
    bad_current.rmdir()
    bad_current.parent.rmdir()

    state_file.write_text("content-preservation-sentinel\n", encoding="utf-8")
    original_hash = hashlib.sha256(state_file.read_bytes()).hexdigest()
    state_file.chmod(0o644)
    state_file.parent.chmod(0o755)
    audit = subprocess.run(
        [*audit_command, "audit-permissions"],
        text=True,
        capture_output=True,
        check=False,
    )
    if audit.returncode == 0 or json.loads(audit.stdout)["unsafe"] == 0:
        raise AssertionError("permission audit did not report loose modes")
    if stat.S_IMODE(state_file.stat().st_mode) != 0o644:
        raise AssertionError("read-only permission audit changed state")
    repair = subprocess.run(
        [*audit_command, "repair-permissions", "--execute"],
        text=True,
        capture_output=True,
        check=False,
    )
    if repair.returncode != 0 or json.loads(repair.stdout)["repaired"] == 0:
        raise AssertionError(f"permission repair failed: {repair.stderr}")
    if hashlib.sha256(state_file.read_bytes()).hexdigest() != original_hash:
        raise AssertionError("permission repair changed task-state content")
    assert_private_state_tree(state_file, codex_home)

    outside = temp_dir / "outside"
    outside.mkdir()
    unsafe_home = temp_dir / "unsafe-codex"
    unsafe_home.symlink_to(outside, target_is_directory=True)
    unsafe_env = {**env, "CODEX_HOME": str(unsafe_home)}
    unsafe_result = run_hook(user_hook, {**payload, "session_id": "unsafe"}, unsafe_env)
    if "private initialization failed" not in extract_context(unsafe_result.stdout):
        raise AssertionError("symlinked CODEX_HOME did not fail closed")
    if (outside / "task-state").exists():
        raise AssertionError("symlinked CODEX_HOME was followed")

    unsafe_root_home = temp_dir / "unsafe-task-root-home"
    unsafe_root_home.mkdir()
    (unsafe_root_home / "task-state").symlink_to(outside, target_is_directory=True)
    unsafe_root_env = {**env, "CODEX_HOME": str(unsafe_root_home)}
    unsafe_root_result = run_hook(
        user_hook, {**payload, "session_id": "unsafe-root"}, unsafe_root_env
    )
    if "private initialization failed" not in extract_context(
        unsafe_root_result.stdout
    ):
        raise AssertionError("symlinked task-state root did not fail closed")
    if any(outside.iterdir()):
        raise AssertionError("symlinked task-state root was followed")


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
        validate_security_and_permission_helper(root=root, temp_dir=temp_dir)

    print("global-context-management local templates validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
