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
import html
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tomllib


REQUIRED_AGENT_NAMES = ("repo_mapper", "test_strategist", "risk_reviewer")
REQUIRED_MAX_CONCURRENT_THREADS_PER_SESSION = 16
MANAGED_BEGIN = "<!-- BEGIN config-codex managed context -->"
MANAGED_END = "<!-- END config-codex managed context -->"
FORBIDDEN_MANAGED_CONTEXT_SNIPPETS = (
    "After one remediation fails against the same blocker",
    "When evidence establishes a causally independent blocker",
    "remediation attempts or 60 active minutes",
    "remediation attempts or 120 active minutes",
)
REQUIRED_MANAGED_CONTEXT_SNIPPETS = (
    "Agents may clean up temporary trees they created during the current task",
    'find "$task_temp_dir" -depth -delete',
    "never target the temporary root or an unresolved variable",
    "Live Product Validation",
    "define and freeze the expected product-owned behavior",
    "Observation is non-intervening only when it cannot alter criterion-relevant state or execution",
    "classify nominally read-only actions by their effect",
    "Changing the declaration starts a new trial and never cleans earlier evidence",
    "mutation outside the declared product workflow that performs, bypasses, or pre-satisfies",
    "marks the affected trial and dependent evidence as intervened",
    "Recovery authorization never makes that evidence valid proof",
    "Production and unconfirmed targets remain read-only without exact action authorization",
    "actions require action-specific approval in every environment",
    "Fix the proven causal owner at its authoritative boundary",
    "before the earliest product divergence or first contaminated boundary, whichever came first",
    "Prove prior writers are quiescent",
    "Nested project instructions",
    "read every applicable instruction file from the repository root",
    "Root and ancestor instructions remain applicable",
    "Nested instructions must not weaken higher-level security",
    "If applicable instructions are irreconcilable, stop before mutation",
    "When a workflow creates or refreshes an `AGENTS.md`, read",
    "Treat `AGENTS.override.md` as the active file for its directory",
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
    except tomllib.TOMLDecodeError:
        fail(f"{label} is not valid TOML", failures)
    except (OSError, UnicodeError):
        fail(f"{label} could not be read safely", failures)
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


def markdown_inline_search_text(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\[[^]]*\]", r"\1", text)
    text = re.sub(r"<[^>]*>", "", text)
    text = text.translate(str.maketrans("", "", "*_`~[]\\"))
    return " ".join(text.split())


def markdown_section(value: str, heading: str) -> str:
    lines = value.splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def markdown_atx_heading_text(line: str) -> str | None:
    candidate = line.lstrip(" ")
    if len(line) - len(candidate) > 3:
        return None
    marker_count = len(candidate) - len(candidate.lstrip("#"))
    if marker_count < 1 or marker_count > 6:
        return None
    if len(candidate) > marker_count and candidate[marker_count] not in " \t":
        return None
    return candidate[marker_count:].strip()


def markdown_list_content(line: str) -> tuple[str, int] | None:
    candidate = line.lstrip(" ")
    leading_spaces = len(line) - len(candidate)
    if leading_spaces > 3 or not candidate:
        return None
    marker_end = 0
    if candidate[0] in {"-", "+", "*"}:
        marker_end = 1
    else:
        while marker_end < len(candidate) and candidate[marker_end].isdigit():
            marker_end += 1
        if (
            marker_end < 1
            or marker_end > 9
            or marker_end >= len(candidate)
            or candidate[marker_end] not in {".", ")"}
        ):
            return None
        marker_end += 1
    whitespace_end = marker_end
    while (
        whitespace_end < len(candidate)
        and candidate[whitespace_end] in " \t"
    ):
        whitespace_end += 1
    padding = whitespace_end - marker_end
    if padding < 1 or padding > 4:
        return None
    return candidate[whitespace_end:], leading_spaces + whitespace_end


def markdown_fence_opening(line: str) -> tuple[str, int, int] | None:
    candidate = line.lstrip(" ")
    leading_spaces = len(line) - len(candidate)
    candidates = [(candidate, 3)] if leading_spaces <= 3 else []
    if (list_content := markdown_list_content(line)) is not None:
        content, content_indent = list_content
        candidates.insert(0, (content, content_indent + 3))
    for fence_candidate, close_indent in candidates:
        marker_character = fence_candidate[:1]
        if marker_character not in {"`", "~"}:
            continue
        marker_length = len(fence_candidate) - len(
            fence_candidate.lstrip(marker_character)
        )
        if marker_length < 3:
            continue
        fence_info = fence_candidate[marker_length:]
        if marker_character == "`" and "`" in fence_info:
            continue
        return marker_character, marker_length, close_indent
    return None


def markdown_headings(value: str) -> list[tuple[str, str]]:
    lines = value.splitlines()
    headings: list[tuple[str, str]] = []
    fence_character: str | None = None
    fence_length = 0
    fence_close_indent = 3
    for index, line in enumerate(lines):
        candidate = line.lstrip(" ")
        leading_spaces = len(line) - len(candidate)
        marker_character = candidate[:1]
        marker_length = 0
        if marker_character in {"`", "~"}:
            marker_length = len(candidate) - len(
                candidate.lstrip(marker_character)
            )
        if fence_character is not None:
            if (
                leading_spaces <= fence_close_indent
                and marker_character == fence_character
                and marker_length >= fence_length
                and not candidate[marker_length:].strip()
            ):
                fence_character = None
                fence_length = 0
            continue
        if leading_spaces > 3:
            candidate = ""
            marker_character = ""
        if (fence_opening := markdown_fence_opening(line)) is not None:
            fence_character, fence_length, fence_close_indent = fence_opening
            continue

        if (heading_text := markdown_atx_heading_text(line)) is not None:
            headings.append((heading_text, line))
            continue
        if marker_character not in {"=", "-"}:
            continue
        underline = candidate.rstrip(" \t")
        if not underline or any(char != marker_character for char in underline):
            continue
        if index == 0:
            continue
        paragraph_start = index - 1
        while paragraph_start > 0:
            previous = lines[paragraph_start - 1]
            previous_candidate = previous.lstrip(" ")
            if (
                not previous_candidate.strip()
                or len(previous) - len(previous_candidate) > 3
                or markdown_atx_heading_text(previous) is not None
                or markdown_fence_opening(previous) is not None
            ):
                break
            paragraph_start -= 1
        paragraph_lines = lines[paragraph_start:index]
        heading_text = " ".join(
            paragraph_line.strip() for paragraph_line in paragraph_lines
        )
        if heading_text:
            source = "\n".join((*paragraph_lines, line))
            headings.append((heading_text, source))
    return headings


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
    begin_count = actual.count(MANAGED_BEGIN)
    end_count = actual.count(MANAGED_END)
    if begin_count == 0 and end_count == 0:
        fail(
            "AGENTS.md has neither exact template content nor managed markers",
            failures,
        )
        return
    if begin_count != 1 or end_count != 1:
        fail("AGENTS.md managed block is stale or incomplete", failures)
        return
    begin = actual.find(MANAGED_BEGIN)
    end = actual.find(MANAGED_END)
    if begin != -1 and end != -1 and begin < end:
        managed_block = actual[begin + len(MANAGED_BEGIN) : end]
        managed_text = compact_markdown_text(managed_block)
        live_heading = "## Live Product Validation"
        live_policy_headings = [
            source
            for heading_text, source in markdown_headings(actual)
            if "live product validation"
            in markdown_inline_search_text(heading_text).casefold()
        ]
        if live_policy_headings != [live_heading]:
            fail(
                "AGENTS.md managed block is stale or incomplete",
                failures,
            )
            return
        required_live_section = compact_markdown_text(
            markdown_section(template, live_heading)
        )
        actual_live_section = compact_markdown_text(
            markdown_section(managed_block, live_heading)
        )
        if actual_live_section != required_live_section:
            fail(
                "AGENTS.md managed block is stale or incomplete",
                failures,
            )
            return
        forbidden = [
            snippet
            for snippet in FORBIDDEN_MANAGED_CONTEXT_SNIPPETS
            if compact_markdown_text(snippet) in managed_text
        ]
        if forbidden:
            fail(
                "AGENTS.md managed block contains troubleshoot-owned "
                "remediation policy",
                failures,
            )
            return
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
    fail("AGENTS.md managed block is stale or incomplete", failures)


def check_config_toml(
    codex_home: Path,
    require_template_mcp_servers: bool,
    failures: list[str],
) -> dict:
    config_path = codex_home / "config.toml"
    try:
        config_stat = config_path.lstat()
    except FileNotFoundError:
        fail("config.toml is missing", failures)
        return {}
    if stat.S_ISLNK(config_stat.st_mode):
        fail("config.toml must not be a symbolic link", failures)
        return {}
    if not stat.S_ISREG(config_stat.st_mode):
        fail("config.toml must be a regular file", failures)
        return {}
    config_mode = stat.S_IMODE(config_stat.st_mode)
    if config_mode != 0o600:
        fail(
            f"config.toml mode is {oct(config_mode)}, expected 0o600",
            failures,
        )
        return {}
    ok("config.toml is a regular non-symlink file with mode 0o600")

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
    if not isinstance(agents, dict):
        fail("agents must be a TOML table", failures)
        return config
    key = "max_concurrent_threads_per_session"
    required_threads = REQUIRED_MAX_CONCURRENT_THREADS_PER_SESSION
    configured_threads = agents.get(key)
    if type(configured_threads) is int and configured_threads == required_threads:
        ok(f"agents.{key}={required_threads}")
    else:
        fail(f"agents.{key} is not {required_threads}", failures)
    if "max_threads" in agents:
        fail("agents.max_threads is a legacy alias and must be removed", failures)
    if "max_depth" in agents:
        fail("agents.max_depth is undocumented and must be removed", failures)

    configured_agents = [(name, f"agents.{name}") for name in REQUIRED_AGENT_NAMES]
    additional_agent_names = sorted(
        name
        for name, value in agents.items()
        if name not in REQUIRED_AGENT_NAMES
        and isinstance(value, dict)
        and "config_file" in value
    )
    configured_agents.extend(
        (name, f"additional configured agent #{index}")
        for index, name in enumerate(additional_agent_names, start=1)
    )
    for name, agent_label in configured_agents:
        agent = agents.get(name, {})
        if not isinstance(agent, dict):
            fail(f"{agent_label} must be a TOML table", failures)
            continue
        role_failures = len(failures)
        declared_description = agent.get("description")
        if not (
            isinstance(declared_description, str)
            and declared_description.strip()
        ):
            fail(
                f"{agent_label}.description must be a non-empty string",
                failures,
            )
        config_file = agent.get("config_file")
        if not isinstance(config_file, str) or not config_file:
            fail(f"{agent_label}.config_file is missing", failures)
            continue
        if Path(config_file).is_absolute() or ".." in Path(config_file).parts:
            fail(
                f"{agent_label}.config_file must stay inside Codex home",
                failures,
            )
            continue
        agent_config_path = codex_home / config_file
        try:
            agent_config_stat = agent_config_path.lstat()
        except FileNotFoundError:
            fail(f"{agent_label}.config_file target is missing", failures)
            continue
        except OSError:
            fail(
                f"{agent_label}.config_file target could not be inspected safely",
                failures,
            )
            continue
        if not stat.S_ISREG(agent_config_stat.st_mode):
            fail(
                f"{agent_label}.config_file target must be a regular "
                "non-symlink file",
                failures,
            )
            continue
        try:
            agent_config_path.resolve(strict=True).relative_to(
                codex_home.resolve(strict=True)
            )
        except ValueError:
            fail(
                f"{agent_label}.config_file must stay inside Codex home",
                failures,
            )
            continue
        except OSError:
            fail(
                f"{agent_label}.config_file target could not be inspected safely",
                failures,
            )
            continue
        agent_config = load_toml(
            agent_config_path,
            f"{agent_label}.config_file target",
            failures,
        )
        role_name = agent_config.get("name")
        if not (isinstance(role_name, str) and role_name.strip()):
            fail(
                f"{agent_label}.config_file name must be a non-empty string",
                failures,
            )
        elif role_name != name:
            fail(
                f"{agent_label}.config_file name does not match its role",
                failures,
            )

        role_description = agent_config.get("description")
        if not (
            isinstance(role_description, str) and role_description.strip()
        ):
            fail(
                f"{agent_label}.config_file description must be a non-empty string",
                failures,
            )
        elif (
            isinstance(declared_description, str)
            and declared_description.strip()
            and role_description != declared_description
        ):
            fail(
                f"{agent_label}.config_file description does not match its role",
                failures,
            )

        developer_instructions = agent_config.get("developer_instructions")
        if not (
            isinstance(developer_instructions, str)
            and developer_instructions.strip()
        ):
            fail(
                f"{agent_label}.config_file developer_instructions must be a "
                "non-empty string",
                failures,
            )

        if agent_config.get("sandbox_mode") != "read-only":
            fail(
                f"{agent_label}.config_file sandbox_mode is not read-only",
                failures,
            )

        if len(failures) == role_failures:
            ok(f"{agent_label} has aligned required metadata and is read-only")

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
                'chmod 700 "${CODEX_HOME:-$HOME/.codex}/task-implementer"'
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


def check_required_mcp_servers(
    config: dict, template: dict, failures: list[str]
) -> None:
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


def check_template_asset(
    relative: str, template_relative: str, codex_home: Path, failures: list[str]
) -> None:
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
            fail(
                f"hooks.json missing required {event_name} hook registration", failures
            )
            continue
        for expected_entry in expected_entries:
            if expected_entry in actual_entries:
                ok(f"hooks.json includes required {event_name} hook registration")
            else:
                fail(
                    f"hooks.json missing required {event_name} hook registration",
                    failures,
                )


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
    print(
        "Idempotency preflight passed: no local changes required for checked surfaces"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
