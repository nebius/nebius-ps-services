#!/usr/bin/env python3
"""Read-only idempotency preflight for a local Codex home.

By default, this validates the minimal merge-safe config-codex contract instead
of diffing user-owned files against public templates. Existing local
AGENTS.md/config.toml files often contain app, plugin, project, hook-trust,
desktop, MCP, or personal policy settings that must be preserved and are not
part of this skill's convergence target.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tomllib


REQUIRED_AGENT_NAMES = ("repo_mapper", "test_strategist", "risk_reviewer")
MANAGED_BEGIN = "<!-- BEGIN config-codex managed context -->"
MANAGED_END = "<!-- END config-codex managed context -->"
REQUIRED_MANAGED_CONTEXT_SNIPPETS = (
    (
        "After one remediation fails against the same blocker, use "
        "`troubleshoot` before another repair."
    ),
    "stop after three distinct failed remediation attempts or 60 active minutes",
    "Report attempts 1 and 2 as progress",
    (
        "at exhaustion, make only the exact private task-state update that "
        "records the stop, then call no other tool and return the complete "
        "troubleshooting report"
    ),
    "Only a new explicit user instruction may start another bounded tranche",
    (
        "For non-trivial planning, implementation, debugging, refactoring, "
        "migration, architecture, review, testing, CI failure, or multi-file "
        "coding tasks, use `global-context-management`."
    ),
    (
        "Read the durable task-state file injected by global hooks at task "
        "start, resume, or after compaction when prior context may matter."
    ),
    "Update it with concise checkpoints",
    "Preserve an active `codex-remediation-budget:v1` marker exactly",
    "Use bounded read-only subagents",
    "Treat that policy request as sufficient",
    "do not ask for another user prompt only because the original",
    (
        "After code, config, or documentation changes in a turn, before the "
        "final response, explicitly use `$align` for the changed surfaces"
    ),
)
TEMPLATE_ASSETS = {
    "hooks/global_context_state.py": "hooks/global_context_state.py.template",
    "hooks/session_start_context.py": "hooks/session_start_context.py.template",
    "hooks/user_prompt_context.py": "hooks/user_prompt_context.py.template",
    "agents/repo_mapper.toml": "agents/repo_mapper.toml.template",
    "agents/test_strategist.toml": "agents/test_strategist.toml.template",
    "agents/risk_reviewer.toml": "agents/risk_reviewer.toml.template",
}
TASK_IMPLEMENTER_ADD_DIR = (
    'codex --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer"'
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check whether a local Codex home already satisfies the "
            "config-codex idempotency contract. Performs no writes and prints "
            "only redacted structural results."
        )
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
        help="Codex home to inspect. Defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--strict-agents-template",
        action="store_true",
        help=(
            "Require AGENTS.md to exactly match assets/AGENTS.md.template. "
            "Use only for canonical template/install-copy audits, not normal "
            "laptop setup."
        ),
    )
    parser.add_argument(
        "--require-template-mcp-servers",
        action="store_true",
        help=(
            "Require every MCP server from assets/config.toml.template to be "
            "present with exact template values. Use only for explicit "
            "template-baseline audits; normal laptop setup preserves existing "
            "MCP config and patches requested integrations separately."
        ),
    )
    parser.add_argument(
        "--require-task-implementer-workspace",
        action="store_true",
        help=(
            "Opt in to validating the private task-implementer directory and "
            "its workspace-write access. This check never changes sandbox or "
            "approval settings."
        ),
    )
    return parser.parse_args(argv)


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ok(message: str) -> None:
    print(f"OK {message}")


def fail(message: str, failures: list[str]) -> None:
    print(f"FAIL {message}")
    failures.append(message)


def load_toml(path: Path, label: str, failures: list[str]) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"{label} is missing", failures)
    except tomllib.TOMLDecodeError as exc:
        fail(f"{label} is not valid TOML: {exc}", failures)
    return {}


def load_json(path: Path, label: str, failures: list[str]) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"{label} is missing", failures)
        return {}
    except json.JSONDecodeError as exc:
        fail(f"{label} is not valid JSON: {exc}", failures)
        return {}
    if isinstance(data, dict):
        return data
    fail(f"{label} must contain a JSON object", failures)
    return {}


def truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def compact_markdown_text(value: str) -> str:
    return " ".join(line.strip() for line in value.splitlines() if line.strip())


def check_agents_md(codex_home: Path, strict: bool, failures: list[str]) -> None:
    agents_path = codex_home / "AGENTS.md"
    template_path = skill_root() / "assets" / "AGENTS.md.template"
    try:
        actual = agents_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("AGENTS.md is missing", failures)
        return
    template = template_path.read_text(encoding="utf-8")
    if actual == template:
        ok("AGENTS.md matches AGENTS.md.template")
        return
    if strict:
        fail("AGENTS.md differs from AGENTS.md.template", failures)
        return
    begin = actual.find(MANAGED_BEGIN)
    end = actual.find(MANAGED_END)
    if begin != -1 and end != -1 and begin < end:
        managed_block = actual[begin + len(MANAGED_BEGIN) : end]
        managed_text = compact_markdown_text(managed_block)
        missing = [
            snippet
            for snippet in REQUIRED_MANAGED_CONTEXT_SNIPPETS
            if compact_markdown_text(snippet) not in managed_text
        ]
        if missing:
            fail("AGENTS.md managed block is stale or incomplete", failures)
            return
        ok("AGENTS.md has current config-codex managed block")
        return
    fail("AGENTS.md has neither exact template content nor managed markers", failures)


def check_config_toml(
    codex_home: Path,
    require_template_mcp_servers: bool,
    failures: list[str],
) -> dict:
    config_path = codex_home / "config.toml"
    config = load_toml(config_path, "config.toml", failures)
    if not config:
        return {}

    features = config.get("features", {})
    for key in ("hooks", "multi_agent"):
        if features.get(key) is True:
            ok(f"features.{key}=true")
        else:
            fail(f"features.{key} is not true", failures)

    agents = config.get("agents", {})
    if agents.get("max_threads") == 4:
        ok("agents.max_threads=4")
    else:
        fail("agents.max_threads is not 4", failures)
    if agents.get("max_depth") == 1:
        ok("agents.max_depth=1")
    else:
        fail("agents.max_depth is not 1", failures)

    for name in REQUIRED_AGENT_NAMES:
        agent = agents.get(name, {})
        config_file = agent.get("config_file")
        if not isinstance(config_file, str) or not config_file:
            fail(f"agents.{name}.config_file is missing", failures)
            continue
        if Path(config_file).is_absolute() or ".." in Path(config_file).parts:
            fail(f"agents.{name}.config_file must stay inside Codex home", failures)
            continue
        agent_config_path = codex_home / config_file
        if not agent_config_path.exists():
            fail(f"agents.{name}.config_file target is missing", failures)
            continue
        agent_config = load_toml(
            agent_config_path,
            f"agents.{name}.config_file target",
            failures,
        )
        if agent_config.get("sandbox_mode") == "read-only":
            ok(f"agents.{name} is read-only")
        else:
            fail(f"agents.{name} is not read-only", failures)

    if require_template_mcp_servers:
        template = load_toml(
            skill_root() / "assets" / "config.toml.template",
            "config.toml.template",
            failures,
        )
        check_required_mcp_servers(config, template, failures)
    else:
        ok("template MCP server parity is not required for merge-safe laptop check")
    return config


def fail_task_implementer_workspace(
    message: str,
    failures: list[str],
) -> None:
    fail(
        f"{message}; runtime remediation: {TASK_IMPLEMENTER_ADD_DIR}",
        failures,
    )


def configured_writable_root_matches(value: object, expected: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    configured = Path(value).expanduser()
    if not configured.is_absolute():
        return False
    return configured.resolve(strict=False) == expected.resolve(strict=False)


def is_inside_git_storage(path: Path, failures: list[str]) -> bool | None:
    try:
        worktree = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        if worktree.returncode == 0 and worktree.stdout.strip() == "true":
            return True
        git_dir = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--absolute-git-dir"],
            check=False,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        fail(
            "task-implementer storage location could not be checked for Git safety",
            failures,
        )
        return None
    return git_dir.returncode == 0


def check_task_implementer_workspace(
    codex_home: Path,
    config: dict,
    failures: list[str],
) -> None:
    workspace = codex_home / "task-implementer"
    if workspace.is_symlink():
        fail_task_implementer_workspace(
            "task-implementer private directory must not be a symlink",
            failures,
        )
        return
    if not workspace.is_dir():
        fail_task_implementer_workspace(
            (
                "task-implementer private directory is missing; create "
                "${CODEX_HOME:-$HOME/.codex}/task-implementer with mode 0700"
            ),
            failures,
        )
        return

    mode = stat.S_IMODE(workspace.stat().st_mode)
    if mode != 0o700:
        fail_task_implementer_workspace(
            (
                "task-implementer private directory mode is not 0700; run "
                "chmod 700 \"${CODEX_HOME:-$HOME/.codex}/task-implementer\""
            ),
            failures,
        )
        return
    ok("task-implementer private directory mode is 0700")

    inside_git = is_inside_git_storage(workspace, failures)
    if inside_git is None:
        return
    if inside_git:
        fail(
            (
                "task-implementer private directory must be outside every Git "
                "worktree and metadata directory"
            ),
            failures,
        )
        return
    ok("task-implementer private directory is outside Git storage")

    if not config:
        return
    sandbox_mode = config.get("sandbox_mode")
    if sandbox_mode == "danger-full-access":
        ok(
            "task-implementer private directory is writable under the "
            "existing danger-full-access sandbox"
        )
        return
    if sandbox_mode != "workspace-write":
        fail_task_implementer_workspace(
            (
                "task-implementer private directory is not writable under "
                "the existing sandbox; keep stricter sandbox and approval "
                "settings unchanged"
            ),
            failures,
        )
        return

    workspace_write = config.get("sandbox_workspace_write")
    if not isinstance(workspace_write, dict):
        fail_task_implementer_workspace(
            "sandbox_workspace_write is missing from config.toml",
            failures,
        )
        return
    roots = workspace_write.get("writable_roots")
    if not isinstance(roots, list):
        fail_task_implementer_workspace(
            "sandbox_workspace_write.writable_roots is missing from config.toml",
            failures,
        )
        return
    if any(configured_writable_root_matches(value, workspace) for value in roots):
        ok(
            "sandbox_workspace_write.writable_roots includes the private "
            "task-implementer directory"
        )
        return
    fail_task_implementer_workspace(
        (
            "sandbox_workspace_write.writable_roots does not include the "
            "private task-implementer directory"
        ),
        failures,
    )


def check_required_mcp_servers(config: dict, template: dict, failures: list[str]) -> None:
    required = template.get("mcp_servers", {})
    actual = config.get("mcp_servers", {})
    if not isinstance(required, dict) or not required:
        fail("config.toml.template has no required MCP server definitions", failures)
        return
    if not isinstance(actual, dict):
        fail("config.toml mcp_servers table is missing", failures)
        return

    for name, expected_spec in sorted(required.items()):
        actual_spec = actual.get(name)
        if not isinstance(actual_spec, dict):
            fail(f"mcp_servers.{name} is missing", failures)
            continue
        if actual_spec == expected_spec:
            ok(f"mcp_servers.{name} matches template")
        else:
            fail(f"mcp_servers.{name} differs from template and needs review", failures)


def check_template_asset(relative: str, template_relative: str, codex_home: Path, failures: list[str]) -> None:
    actual_path = codex_home / relative
    template_path = skill_root() / "assets" / template_relative
    try:
        actual = actual_path.read_bytes()
    except FileNotFoundError:
        fail(f"{relative} is missing", failures)
        return
    try:
        expected = template_path.read_bytes()
    except FileNotFoundError:
        fail(f"template asset for {relative} is missing", failures)
        return
    if actual == expected:
        ok(f"{relative} matches template")
    else:
        fail(f"{relative} differs from template and needs review", failures)


def check_runtime_files(codex_home: Path, failures: list[str]) -> None:
    for relative, template_relative in TEMPLATE_ASSETS.items():
        check_template_asset(relative, template_relative, codex_home, failures)

    check_hooks_json(codex_home, failures)

    task_state = codex_home / "task-state"
    if not task_state.is_dir():
        fail("task-state directory is missing", failures)
        return
    mode = stat.S_IMODE(task_state.stat().st_mode)
    if mode == 0o700:
        ok("task-state directory mode is 0700")
    else:
        fail(f"task-state directory mode is {oct(mode)}, expected 0o700", failures)

    helper = codex_home / "hooks/global_context_state.py"
    if helper.is_file():
        audit = subprocess.run(
            [
                sys.executable,
                str(helper),
                "--codex-home",
                str(codex_home),
                "audit-permissions",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if audit.returncode == 0:
            ok("nested task-state permissions and types are private")
        else:
            fail("nested task-state permissions or types are unsafe", failures)

    policy = codex_home / "hooks/global_context_policy.json"
    if policy.exists():
        failure_count = len(failures)
        policy_data = load_json(policy, "global_context_policy.json", failures)
        if len(failures) == failure_count:
            ok("optional global_context_policy.json is valid JSON")
            if truthy(policy_data.get("auto_read_only_subagents")) or truthy(
                policy_data.get("enabled")
            ):
                ok(
                    "optional global_context_policy.json enables read-only "
                    "subagent delegation"
                )
            else:
                fail(
                    "optional global_context_policy.json does not enable "
                    "read-only subagent delegation",
                    failures,
                )


def check_hooks_json(codex_home: Path, failures: list[str]) -> None:
    actual = load_json(codex_home / "hooks.json", "hooks.json", failures)
    if not actual:
        return
    expected = load_json(
        skill_root() / "assets" / "hooks.json.template",
        "hooks.json.template",
        failures,
    )
    if not expected:
        return
    ok("hooks.json is valid JSON")

    actual_hooks = actual.get("hooks")
    expected_hooks = expected.get("hooks")
    if not isinstance(actual_hooks, dict):
        fail("hooks.json hooks table is missing", failures)
        return
    if not isinstance(expected_hooks, dict):
        fail("hooks.json.template hooks table is missing", failures)
        return

    for event_name, expected_entries in sorted(expected_hooks.items()):
        actual_entries = actual_hooks.get(event_name)
        if not isinstance(expected_entries, list) or not expected_entries:
            fail(f"hooks.json.template {event_name} entries are invalid", failures)
            continue
        if not isinstance(actual_entries, list):
            fail(f"hooks.json missing required {event_name} hook registration", failures)
            continue
        for expected_entry in expected_entries:
            if expected_entry in actual_entries:
                ok(f"hooks.json includes required {event_name} hook registration")
            else:
                fail(f"hooks.json missing required {event_name} hook registration", failures)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    codex_home = args.codex_home.expanduser()
    failures: list[str] = []
    print("Checking Codex home: <codex-home>")
    check_agents_md(codex_home, args.strict_agents_template, failures)
    config = check_config_toml(
        codex_home,
        args.require_template_mcp_servers,
        failures,
    )
    check_runtime_files(codex_home, failures)
    if args.require_task_implementer_workspace:
        check_task_implementer_workspace(codex_home, config, failures)
    if failures:
        print(f"Idempotency preflight failed: {len(failures)} issue(s)")
        return 1
    print("Idempotency preflight passed: no local changes required for checked surfaces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
