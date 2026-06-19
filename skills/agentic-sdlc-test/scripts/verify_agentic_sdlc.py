#!/usr/bin/env python3
"""Safe preflight verifier for the Agentic SDLC workflow.

The script writes only under ~/.codex/sdlc-verification by default. It inspects
installed global skills and hook config read-only, runs hook source fixtures
with disposable state, and writes a Markdown report.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_SDLC_SKILLS = (
    "sdlc-align-specs",
    "sdlc-classify-failure",
    "sdlc-commit",
    "sdlc-create-design",
    "sdlc-create-plan",
    "sdlc-create-requirements",
    "sdlc-evaluate",
    "sdlc-gather-context",
    "sdlc-gui-test",
    "sdlc-implement-plan",
    "sdlc-merge-pr",
    "sdlc-start",
    "sdlc-tdd",
    "sdlc-tui-test",
    "sdlc-uat-tests",
    "sdlc-unit-tests",
    "sdlc-validate-codes",
)

DESCRIPTION_PREFIX = "Use only as part of the Agentic SDLC workflow;"
DEFAULT_PROJECT_ID = "sdlc-verification-project"
DEFAULT_RUN_ID = "active"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    section: str


@dataclass
class Context:
    skills_root: Path
    repo_root: Path
    design_path: Path
    global_skills_dir: Path
    codex_home: Path
    verification_root: Path
    disposable_project: Path
    fixture_codex_home: Path
    checks: list[Check] = field(default_factory=list)

    def add(self, section: str, name: str, status: str, detail: str) -> None:
        self.checks.append(Check(name=name, status=status, detail=detail, section=section))


def parse_args(argv: list[str]) -> argparse.Namespace:
    skill_dir = Path(__file__).resolve().parents[1]
    skills_root = skill_dir.parents[0]
    repo_root = skills_root.parent
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    parser = argparse.ArgumentParser(
        description="Run safe Agentic SDLC static and hook preflight verification.",
    )
    parser.add_argument("--skills-root", type=Path, default=skills_root)
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    parser.add_argument("--design", type=Path, default=skills_root / "docs" / "agentic-sdlc-design.md")
    parser.add_argument("--global-skills-dir", type=Path, default=Path.home() / ".agents" / "skills")
    parser.add_argument("--codex-home", type=Path, default=codex_home)
    parser.add_argument("--verification-root", type=Path, default=codex_home / "sdlc-verification")
    parser.add_argument("--report", type=Path, default=None)
    return parser.parse_args(argv)


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 30,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def frontmatter(skill_md: Path) -> dict[str, str]:
    text = read_text(skill_md)
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line.startswith((" ", "\t")):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("\"'")
    return data


def setup_context(ns: argparse.Namespace) -> Context:
    verification_root = ns.verification_root.expanduser().resolve(strict=False)
    disposable_project = verification_root / "disposable-project"
    fixture_codex_home = verification_root / "fixture-codex-home"
    return Context(
        skills_root=ns.skills_root.expanduser().resolve(strict=False),
        repo_root=ns.repo_root.expanduser().resolve(strict=False),
        design_path=ns.design.expanduser().resolve(strict=False),
        global_skills_dir=ns.global_skills_dir.expanduser().resolve(strict=False),
        codex_home=ns.codex_home.expanduser().resolve(strict=False),
        verification_root=verification_root,
        disposable_project=disposable_project,
        fixture_codex_home=fixture_codex_home,
    )


def check_design(ctx: Context) -> None:
    text = read_text(ctx.design_path)
    if not text:
        ctx.add("Environment checked", "Design document", "FAIL", f"Missing or unreadable: {ctx.design_path}")
        return
    required_terms = [
        "There is no workflow CLI",
        "sdlc-start",
        "PreToolUse",
        "Stop",
        "Private local run state",
        "Resume And Idempotency",
        "Workflow Verification",
        "Quick preflight test",
        "Full workflow test",
        "$agentic-sdlc-test",
        "~/.codex/sdlc-verification/report.md",
    ]
    missing = [term for term in required_terms if term not in text]
    status = "PASS" if not missing else "FAIL"
    detail = "Design document contains core SDLC and verification contract terms."
    if missing:
        detail = "Missing expected design terms: " + ", ".join(missing)
    ctx.add("Environment checked", "Design contract", status, detail)


def check_skill_discovery(ctx: Context) -> None:
    base = ctx.global_skills_dir
    ctx.add("Skill discovery results", "Global skills directory", "PASS" if base.is_dir() else "FAIL", str(base))
    names: dict[str, list[Path]] = {}
    for folder in sorted(base.iterdir()) if base.is_dir() else []:
        skill_md = folder / "SKILL.md"
        if not skill_md.is_file():
            continue
        meta = frontmatter(skill_md)
        name = meta.get("name", "")
        if name:
            names.setdefault(name, []).append(folder)

    for required in REQUIRED_SDLC_SKILLS:
        folder = base / required
        skill_md = folder / "SKILL.md"
        if not folder.is_dir():
            ctx.add("Skill discovery results", required, "FAIL", f"Missing folder: {folder}")
            continue
        if not skill_md.is_file():
            ctx.add("Skill discovery results", required, "FAIL", f"Missing SKILL.md: {skill_md}")
            continue
        meta = frontmatter(skill_md)
        name = meta.get("name", "")
        description = meta.get("description", "")
        problems: list[str] = []
        if name != required:
            problems.append(f"name is {name!r}, expected {required!r}")
        if not description:
            problems.append("missing description")
        elif not description.startswith(DESCRIPTION_PREFIX):
            problems.append("description does not start with SDLC-only prefix")
        status = "PASS" if not problems else "FAIL"
        detail = "SKILL.md name and SDLC trigger description are valid."
        if problems:
            detail = "; ".join(problems)
        ctx.add("Skill discovery results", required, status, detail)

    duplicate_sdlc = {
        name: paths for name, paths in names.items() if name.startswith("sdlc-") and len(paths) > 1
    }
    if duplicate_sdlc:
        detail = "; ".join(f"{name}: {', '.join(str(p) for p in paths)}" for name, paths in duplicate_sdlc.items())
        ctx.add("Skill discovery results", "Duplicate SDLC names", "FAIL", detail)
    else:
        ctx.add("Skill discovery results", "Duplicate SDLC names", "PASS", "No duplicate sdlc-* names found.")


def load_hooks_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_toml_hooks(path: Path) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:
        return {}
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    hooks = value.get("hooks", {})
    return hooks if isinstance(hooks, dict) else {}


def flatten_hook_commands(hooks: Any) -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    if not isinstance(hooks, dict):
        return commands
    for event, groups in hooks.items():
        groups_iter: list[Any]
        if isinstance(groups, dict):
            groups_iter = [groups]
        elif isinstance(groups, list):
            groups_iter = groups
        else:
            continue
        for group in groups_iter:
            entries = group.get("hooks", []) if isinstance(group, dict) else []
            if isinstance(entries, dict):
                entries = [entries]
            for entry in entries if isinstance(entries, list) else []:
                if isinstance(entry, dict):
                    command = entry.get("command")
                    if isinstance(command, str):
                        commands.append((str(event), command))
    return commands


def check_hook_config(ctx: Context) -> None:
    hooks_sources: list[tuple[Path, dict[str, Any]]] = []
    hooks_json = ctx.codex_home / "hooks.json"
    config_toml = ctx.codex_home / "config.toml"
    if hooks_json.exists():
        hooks_sources.append((hooks_json, load_hooks_json(hooks_json).get("hooks", {})))
    if config_toml.exists():
        hooks_sources.append((config_toml, load_toml_hooks(config_toml)))
    if not hooks_sources:
        ctx.add("Hook configuration results", "Hook config source", "FAIL", f"No hook config found under {ctx.codex_home}")
        return

    all_commands: list[tuple[str, str]] = []
    for source, hooks in hooks_sources:
        source_commands = flatten_hook_commands(hooks)
        all_commands.extend(source_commands)
        ctx.add(
            "Hook configuration results",
            f"Hook source {source.name}",
            "PASS",
            f"{len(source_commands)} command hook(s) discovered in {source}",
        )

    def has(event: str, token: str) -> bool:
        return any(hook_event == event and token in command for hook_event, command in all_commands)

    pre_found = has("PreToolUse", "pre_tool_use_sdlc_policy.py")
    stop_found = has("Stop", "stop_sdlc_continue.py")
    ctx.add("Hook configuration results", "PreToolUse SDLC hook configured", "PASS" if pre_found else "FAIL", "Found SDLC PreToolUse safety hook." if pre_found else "Missing SDLC PreToolUse safety hook.")
    ctx.add("Hook configuration results", "Stop SDLC hook configured", "PASS" if stop_found else "FAIL", "Found SDLC Stop continuation hook." if stop_found else "Missing SDLC Stop continuation hook.")
    session_found = any(event == "SessionStart" for event, _ in all_commands)
    ctx.add("Hook configuration results", "SessionStart preserved", "PASS" if session_found else "WARN", "SessionStart hook exists." if session_found else "No SessionStart hook command discovered.")
    user_prompt_commands = [command for event, command in all_commands if event == "UserPromptSubmit"]
    if user_prompt_commands and any("sdlc" in command.lower() for command in user_prompt_commands):
        ctx.add("Hook configuration results", "UserPromptSubmit SDLC routing", "FAIL", "UserPromptSubmit command appears to mention SDLC routing.")
    else:
        detail = "UserPromptSubmit hooks do not mention SDLC routing."
        if not user_prompt_commands:
            detail = "No UserPromptSubmit hook command discovered."
        ctx.add("Hook configuration results", "UserPromptSubmit SDLC routing", "PASS", detail)


def git(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=project, timeout=15)


def setup_disposable_project(ctx: Context) -> None:
    ctx.verification_root.mkdir(parents=True, exist_ok=True)
    project = ctx.disposable_project
    project.mkdir(parents=True, exist_ok=True)
    files = {
        "README.md": "# Disposable SDLC Verification Project\n",
        "pyproject.toml": "[project]\nname = \"sdlc-verification-project\"\nversion = \"0.0.0\"\n",
        "src/resource_name.py": "\"\"\"Disposable verification module.\"\"\"\n",
        "tests/test_resource_name.py": "def test_placeholder():\n    assert True\n",
        "docs/.gitkeep": "",
    }
    for rel, content in files.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    if not (project / ".git").exists():
        init = git(project, "init", "-b", "main")
        if init.returncode != 0:
            git(project, "init")
            git(project, "branch", "-m", "main")
        git(project, "config", "user.email", "sdlc-verification@example.invalid")
        git(project, "config", "user.name", "SDLC Verification")
        git(project, "add", ".")
        commit = git(project, "commit", "-m", "initial disposable verification project")
        if commit.returncode not in {0, 1}:
            ctx.add("Disposable SDLC golden-path run results", "Disposable project commit", "WARN", commit.stderr.strip())
    status = git(project, "status", "--short")
    private_staged = any(".codex" in line or "evidence/" in line or "plans/" in line for line in status.stdout.splitlines())
    ctx.add("Disposable SDLC golden-path run results", "Disposable project", "PASS" if project.is_dir() and not private_staged else "FAIL", f"Project exists at {project}; private state staged: {private_staged}")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def setup_fixture_state(ctx: Context, *, record: bool = True) -> Path:
    run_dir = ctx.fixture_codex_home / "sdlc-runs" / DEFAULT_PROJECT_ID / DEFAULT_RUN_ID
    write_json(run_dir.parent / "active.lock", {"project_id": DEFAULT_PROJECT_ID, "project_root": str(ctx.disposable_project), "run_id": DEFAULT_RUN_ID, "status": "running"})
    write_json(run_dir.parent / "active-run.json", {"run_id": DEFAULT_RUN_ID})
    write_json(run_dir / "run.json", {"status": "running"})
    write_json(
        run_dir / "current-state.json",
        {
            "project_id": DEFAULT_PROJECT_ID,
            "run_id": DEFAULT_RUN_ID,
            "status": "running",
            "current_feature": "FEAT-001",
            "current_phase": "implementation",
            "next_recommended_skill": "sdlc-validate-codes",
            "retry_counts": {"implementation": 0},
            "iteration_count": 1,
            "max_iterations": 200,
            "needs_human": False,
        },
    )
    write_json(run_dir / "feature-queue.json", {"features": [{"id": "FEAT-001", "status": "implementation"}]})
    write_json(run_dir / "fingerprints.json", {})
    for rel in ("context", "plans", "evidence/FEAT-001", "history", "permissions"):
        (run_dir / rel).mkdir(parents=True, exist_ok=True)
    plan = run_dir / "plans" / "FEAT-001.plan.v1.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    (run_dir / "plans" / "FEAT-001.plan.v1.md.lock").write_text("locked\n", encoding="utf-8")
    (run_dir / "STEERING.md").write_text("", encoding="utf-8")
    for rel in ("history/continuation-state.json", "history/hook-events.jsonl"):
        try:
            (run_dir / rel).unlink()
        except FileNotFoundError:
            pass
    if record:
        ctx.add("Environment checked", "Disposable SDLC state", "PASS", f"Fixture state created at {run_dir}")
    return run_dir


def run_hook(
    script: Path,
    payload: dict[str, Any],
    ctx: Context,
    *,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home or ctx.fixture_codex_home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = run(["python3", str(script)], input_text=json.dumps(payload), env=env, timeout=10)
    if result.returncode != 0:
        return {"_error": result.stderr.strip() or result.stdout.strip()}
    if not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"_error": result.stdout.strip()}


def pre_payload(ctx: Context, tool_name: str, command: str) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "cwd": str(ctx.disposable_project),
        "turn_id": "verification-turn",
        "tool_name": tool_name,
        "tool_use_id": "verification-tool",
        "tool_input": {"command": command},
    }


def stop_payload(ctx: Context, active: bool = False) -> dict[str, Any]:
    return {
        "hook_event_name": "Stop",
        "cwd": str(ctx.disposable_project),
        "turn_id": "verification-turn",
        "stop_hook_active": active,
        "last_assistant_message": "verification",
    }


def denied(result: dict[str, Any]) -> str | None:
    output = result.get("hookSpecificOutput", {})
    if output.get("permissionDecision") == "deny":
        return str(output.get("permissionDecisionReason") or "")
    return None


def check_hooks_with_fixtures(ctx: Context) -> None:
    hook_dir = ctx.skills_root / "sdlc-start" / "assets" / "hooks"
    pre_tool = hook_dir / "pre_tool_use_sdlc_policy.py"
    stop_hook = hook_dir / "stop_sdlc_continue.py"
    tests = hook_dir / "tests" / "test_sdlc_hooks.py"
    missing = [path for path in (pre_tool, stop_hook, tests) if not path.exists()]
    if missing:
        ctx.add("PreToolUse safety test results", "Hook source files", "FAIL", "Missing: " + ", ".join(str(p) for p in missing))
        return

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    unit = run(["python3", str(tests)], cwd=ctx.skills_root, env=env, timeout=60)
    unit_detail = (unit.stderr + "\n" + unit.stdout).strip().splitlines()[-3:]
    ctx.add("PreToolUse safety test results", "Hook unit tests", "PASS" if unit.returncode == 0 else "FAIL", " | ".join(unit_detail) if unit_detail else "No output.")

    def reset_stop_state(
        *,
        status: str = "running",
        run_status: str | None = None,
        current_phase: str = "implementation",
        next_skill: str = "sdlc-validate-codes",
        retry_counts: dict[str, int] | None = None,
        iteration_count: int = 1,
        max_iterations: int = 200,
        max_retries: int = 3,
        needs_human: bool = False,
        blocked_reason: str = "",
        feature_queue: dict[str, Any] | None = None,
        steering: str = "",
    ) -> Path:
        run_dir = setup_fixture_state(ctx, record=False)
        current_state = {
            "project_id": DEFAULT_PROJECT_ID,
            "run_id": DEFAULT_RUN_ID,
            "status": status,
            "current_feature": "FEAT-001",
            "current_phase": current_phase,
            "next_recommended_skill": next_skill,
            "retry_counts": retry_counts or {current_phase: 0},
            "iteration_count": iteration_count,
            "max_iterations": max_iterations,
            "max_retries": max_retries,
            "needs_human": needs_human,
            "blocked_reason": blocked_reason,
        }
        write_json(run_dir / "run.json", {"status": run_status or status, "blocked_reason": blocked_reason})
        write_json(run_dir / "current-state.json", current_state)
        write_json(
            run_dir / "feature-queue.json",
            feature_queue or {"features": [{"id": "FEAT-001", "status": current_phase}]},
        )
        (run_dir / "STEERING.md").write_text(steering, encoding="utf-8")
        return run_dir

    setup_fixture_state(ctx)
    allow_status = run_hook(pre_tool, pre_payload(ctx, "Bash", "git status --short"), ctx)
    ctx.add("PreToolUse safety test results", "Allow read-only git status", "PASS" if allow_status == {} else "FAIL", json.dumps(allow_status, sort_keys=True))
    delete_cmd = "rm " + "-rf " + "/"
    deny_delete = run_hook(pre_tool, pre_payload(ctx, "Bash", delete_cmd), ctx)
    reason = denied(deny_delete)
    ctx.add("PreToolUse safety test results", "Deny destructive recursive delete", "PASS" if reason and "recursive removal" in reason else "FAIL", reason or json.dumps(deny_delete, sort_keys=True))
    plan = ctx.fixture_codex_home / "sdlc-runs" / DEFAULT_PROJECT_ID / DEFAULT_RUN_ID / "plans" / "FEAT-001.plan.v1.md"
    patch = f"*** Begin Patch\n*** Update File: {plan}\n@@\n-# Plan\n+# Changed\n*** End Patch\n"
    deny_plan = run_hook(pre_tool, pre_payload(ctx, "apply_patch", patch), ctx)
    reason = denied(deny_plan)
    ctx.add("PreToolUse safety test results", "Deny locked plan edit", "PASS" if reason and "locked SDLC plan" in reason else "FAIL", reason or json.dumps(deny_plan, sort_keys=True))

    empty_codex_home = ctx.verification_root / "empty-fixture-codex-home"
    empty_codex_home.mkdir(parents=True, exist_ok=True)
    no_active = run_hook(stop_hook, stop_payload(ctx), ctx, codex_home=empty_codex_home)
    ctx.add("Stop continuation test results", "No active run", "PASS" if no_active == {"continue": True} else "FAIL", json.dumps(no_active, sort_keys=True))

    reset_stop_state(status="complete")
    complete = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add("Stop continuation test results", "Complete run stops", "PASS" if complete.get("continue") is False and "complete" in str(complete.get("stopReason")) else "FAIL", str(complete.get("stopReason") or complete))

    reset_stop_state(status="paused")
    paused = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add("Stop continuation test results", "Paused run stops", "PASS" if paused.get("continue") is False and "paused" in str(paused.get("stopReason")) else "FAIL", str(paused.get("stopReason") or paused))

    reset_stop_state(status="blocked", blocked_reason="verification blocker")
    blocked = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add("Stop continuation test results", "Blocked run stops", "PASS" if blocked.get("continue") is False and "verification blocker" in str(blocked.get("stopReason")) else "FAIL", str(blocked.get("stopReason") or blocked))

    reset_stop_state(needs_human=True, blocked_reason="verification approval")
    human = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add("Stop continuation test results", "Human input stops", "PASS" if human.get("continue") is False and "Human input required" in str(human.get("stopReason")) else "FAIL", str(human.get("stopReason") or human))

    reset_stop_state(iteration_count=200, max_iterations=200)
    max_iter = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add("Stop continuation test results", "Max iteration stops", "PASS" if max_iter.get("continue") is False and "Max SDLC iterations" in str(max_iter.get("stopReason")) else "FAIL", str(max_iter.get("stopReason") or max_iter))

    reset_stop_state(retry_counts={"implementation": 3}, max_retries=3)
    retry = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add("Stop continuation test results", "Retry budget stops", "PASS" if retry.get("continue") is False and "Retry budget exceeded" in str(retry.get("stopReason")) else "FAIL", str(retry.get("stopReason") or retry))

    reset_stop_state()
    run_hook(stop_hook, stop_payload(ctx, active=True), ctx)
    run_hook(stop_hook, stop_payload(ctx, active=True), ctx)
    no_progress = run_hook(stop_hook, stop_payload(ctx, active=True), ctx)
    ctx.add("Stop continuation test results", "No-progress guard stops", "PASS" if no_progress.get("continue") is False and "No progress" in str(no_progress.get("stopReason")) else "FAIL", str(no_progress.get("stopReason") or no_progress))

    reset_stop_state(next_skill="sdlc-validate-codes")
    stop_continue = run_hook(stop_hook, stop_payload(ctx), ctx)
    prompt = str(stop_continue.get("reason") or "")
    ctx.add("Stop continuation test results", "Continue through sdlc-start", "PASS" if stop_continue.get("decision") == "block" and "Use skill sdlc-start" in prompt else "FAIL", prompt.splitlines()[0] if prompt else json.dumps(stop_continue, sort_keys=True))

    reset_stop_state(
        next_skill="",
        feature_queue={"features": [{"id": "FEAT-001", "status": "committed"}]},
    )
    uat = run_hook(stop_hook, stop_payload(ctx), ctx)
    uat_prompt = str(uat.get("reason") or "")
    ctx.add("Stop continuation test results", "Continue to UAT", "PASS" if uat.get("decision") == "block" and "sdlc-uat-tests" in uat_prompt else "FAIL", uat_prompt.splitlines()[0] if uat_prompt else json.dumps(uat, sort_keys=True))

    reset_stop_state(steering="Pause after the current feature. Do not create a PR.\n")
    steering = run_hook(stop_hook, stop_payload(ctx), ctx)
    steering_prompt = str(steering.get("reason") or "")
    ctx.add("Steering behavior results", "Pause/no-PR steering continues through coordinator", "PASS" if steering.get("decision") == "block" and "STEERING.md" in steering_prompt else "FAIL", steering_prompt.splitlines()[0] if steering_prompt else json.dumps(steering, sort_keys=True))

    reset_stop_state(
        current_phase="review",
        next_skill="sdlc-merge-pr",
        retry_counts={"review": 0},
        feature_queue={
            "features": [{"id": "FEAT-001", "status": "committed"}],
            "uat": {"status": "passed"},
        },
    )
    stop_merge = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add("Stop continuation test results", "Do not auto-continue merge", "PASS" if stop_merge.get("continue") is False and "explicit user request" in str(stop_merge.get("stopReason")) else "FAIL", str(stop_merge.get("stopReason") or stop_merge))


def add_agent_required_sections(ctx: Context) -> None:
    pending_checks = [
        (
            "Disposable SDLC golden-path run results",
            "Golden-path agent execution",
            "PENDING: agent must run the disposable golden-path SDLC skills after preflight.",
        ),
        (
            "Idempotency results",
            "Idempotency rerun",
            "PENDING: agent must rerun the disposable workflow and check duplicates.",
        ),
        (
            "Idempotency results",
            "Change-request handling",
            "PENDING: agent must apply the safe change request and verify scoped rerun behavior.",
        ),
        (
            "Failure-loop results",
            "Failure-loop routing",
            "PENDING: agent must inject controlled failures one at a time.",
        ),
        (
            "Steering behavior results",
            "Steering and continuation",
            "PENDING: agent must verify STEERING.md pause/no-PR handling and continuation guards.",
        ),
    ]
    for section, name, detail in pending_checks:
        if not any(check.section == section and check.name == name for check in ctx.checks):
            ctx.add(section, name, "WARN", detail)
    ctx.add("Disposable SDLC golden-path run results", "Private state not committed", "PASS", "Preflight fixture keeps private state outside the disposable repo.")


def final_status(ctx: Context) -> str:
    fail_sections = {
        "Skill discovery results",
        "Hook configuration results",
        "PreToolUse safety test results",
        "Stop continuation test results",
    }
    if any(check.status == "FAIL" and check.section in fail_sections for check in ctx.checks):
        return "FAIL"
    if any(check.status in {"FAIL", "WARN"} for check in ctx.checks):
        return "PARTIAL"
    return "PASS"


def summarize_matrix(ctx: Context) -> list[tuple[str, str]]:
    labels = [
        "Skill discovery",
        "Hook config",
        "PreToolUse allow cases",
        "PreToolUse deny cases",
        "Stop terminal cases",
        "Stop continuation cases",
        "Golden-path SDLC run",
        "Idempotency",
        "Change request handling",
        "Failure-loop routing",
        "Steering",
        "Long-running continuation",
        "GUI smoke check",
        "TUI smoke check",
        "Private state not committed",
        "Merge guard",
    ]
    mapping = {
        "Skill discovery": "Skill discovery results",
        "Hook config": "Hook configuration results",
        "PreToolUse allow cases": "PreToolUse safety test results",
        "PreToolUse deny cases": "PreToolUse safety test results",
        "Stop terminal cases": "Stop continuation test results",
        "Stop continuation cases": "Stop continuation test results",
        "Golden-path SDLC run": "Disposable SDLC golden-path run results",
        "Idempotency": "Idempotency results",
        "Change request handling": "Idempotency results",
        "Failure-loop routing": "Failure-loop results",
        "Steering": "Steering behavior results",
        "Long-running continuation": "Steering behavior results",
        "GUI smoke check": "Disposable SDLC golden-path run results",
        "TUI smoke check": "Disposable SDLC golden-path run results",
        "Private state not committed": "Disposable SDLC golden-path run results",
        "Merge guard": "Stop continuation test results",
    }
    rows: list[tuple[str, str]] = []
    for label in labels:
        section = mapping[label]
        if label == "Private state not committed":
            checks = [check for check in ctx.checks if check.name == "Private state not committed"]
        else:
            checks = [check for check in ctx.checks if check.section == section]
        if not checks:
            status = "NOT APPLICABLE" if "smoke" in label else "WARN"
        elif any(check.status == "FAIL" for check in checks):
            status = "FAIL"
        elif any(check.status == "WARN" for check in checks):
            status = "PARTIAL"
        else:
            status = "PASS"
        if "smoke" in label and status == "PARTIAL":
            status = "NOT APPLICABLE"
        rows.append((label, status))
    return rows


def report(ctx: Context) -> str:
    status = final_status(ctx)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    by_section: dict[str, list[Check]] = {}
    for check in ctx.checks:
        by_section.setdefault(check.section, []).append(check)
    lines = [
        "# Agentic SDLC Verification Report",
        "",
        "## Summary",
        "",
        f"- Final readiness status: {status}",
        f"- Generated at: {now}",
        f"- Verification root: `{ctx.verification_root}`",
        f"- Disposable project: `{ctx.disposable_project}`",
        "- Mode: safe preflight plus hook fixture checks. Full golden-path phase execution must be completed by the agent using the SDLC skills.",
        "",
        "## Readiness Matrix",
        "",
        "| Area | Status |",
        "| --- | --- |",
    ]
    for label, value in summarize_matrix(ctx):
        lines.append(f"| {label} | {value} |")
    lines.append("")
    for section in [
        "Environment checked",
        "Skill discovery results",
        "Hook configuration results",
        "PreToolUse safety test results",
        "Stop continuation test results",
        "Disposable SDLC golden-path run results",
        "Idempotency results",
        "Failure-loop results",
        "Steering behavior results",
    ]:
        lines.extend([f"## {section}", ""])
        for check in by_section.get(section, []):
            lines.append(f"- {check.status}: {check.name} - {check.detail}")
        if section not in by_section:
            lines.append("- WARN: Not run.")
        lines.append("")
    gaps = [check for check in ctx.checks if check.status in {"FAIL", "WARN"}]
    lines.extend(["## Gaps found", ""])
    if gaps:
        for check in gaps:
            lines.append(f"- {check.section}: {check.name} - {check.detail}")
    else:
        lines.append("- None found in preflight.")
    lines.extend(["", "## Recommended fixes", ""])
    if gaps:
        for check in gaps[:10]:
            lines.append(f"- Address `{check.name}` in `{check.section}` and rerun `$agentic-sdlc-test`.")
    else:
        lines.append("- Complete the disposable golden-path run and rerun final verification.")
    lines.extend(["", f"## Final readiness status: {status}", ""])
    return "\n".join(lines)


def write_report(ctx: Context, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = report(ctx)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.write("\n")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def main(argv: list[str]) -> int:
    ns = parse_args(argv)
    ctx = setup_context(ns)
    report_path = (ns.report or (ctx.verification_root / "report.md")).expanduser().resolve(strict=False)
    ctx.verification_root.mkdir(parents=True, exist_ok=True)
    check_design(ctx)
    check_skill_discovery(ctx)
    check_hook_config(ctx)
    setup_disposable_project(ctx)
    check_hooks_with_fixtures(ctx)
    add_agent_required_sections(ctx)
    write_report(ctx, report_path)
    print(f"Report path: {report_path}")
    print(f"Final readiness status: {final_status(ctx)}")
    failures = [check for check in ctx.checks if check.status == "FAIL"]
    warnings = [check for check in ctx.checks if check.status == "WARN"]
    print(f"Failures: {len(failures)}")
    print(f"Warnings: {len(warnings)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
