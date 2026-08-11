#!/usr/bin/env python3
"""Run Task Implementer's lightweight, local-only verification profile."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from task_implementer_reporting import build_report, write_private
from task_implementer_lifecycle import _ensure_root


TEST_SCRIPTS = (
    "test-task-implementer-contract.py",
    "test-prompt-workspace.py",
    "test-task-specs.py",
    "test-task-execution.py",
    "test-task-waves.py",
    "test-worktree-interoperability.py",
)
HARNESS_TEST_SCRIPTS = (
    "test_app_prompt.py",
    "test_collect_live_evidence.py",
    "test_task_implementer_lifecycle.py",
    "test_task_implementer_reporting.py",
    "test_task_implementer_semantics.py",
    "test_task_implementer_test_contract.py",
    "test_verify_task_implementer.py",
)
IGNORED_NAMES = {"__pycache__", ".install-source-id"}


def default_private_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    return (
        Path(codex_home).expanduser() / "task-implementer-test"
        if codex_home
        else Path.home() / ".codex" / "task-implementer-test"
    )


def _files(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in IGNORED_NAMES for part in path.parts):
            continue
        found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def parity(source: Path, installed: Path) -> tuple[str, str]:
    if not installed.is_dir():
        return "PARTIAL", "installed Task Implementer was not found"
    return (
        ("PASS", "source and installed Task Implementer copies match")
        if _files(source) == _files(installed)
        else ("FAIL", "source and installed Task Implementer copies differ")
    )


def dependency_parity(source: Path, installed: Path) -> tuple[str, str]:
    if not source.is_dir():
        return "FAIL", "project-agent-instructions source was not found"
    if not installed.is_dir():
        return "PARTIAL", "installed project-agent-instructions was not found"
    return (
        ("PASS", "source and installed project-agent-instructions copies match")
        if _files(source) == _files(installed)
        else (
            "FAIL",
            "source and installed project-agent-instructions copies differ",
        )
    )


def contract(source: Path) -> tuple[str, str]:
    skill = (source / "SKILL.md").read_text(encoding="utf-8")
    metadata = (source / "agents" / "openai.yaml").read_text(encoding="utf-8")
    required_commands = (
        "$task-implementer workspace init [project-folder]",
        "$task-implementer run <prompt-ref-or-file>",
        "$task-implementer integrate [project-folder]",
        "$task-implementer workspace remove [project-folder]",
    )
    frontmatter = re.match(r"\A---\n(?P<body>.*?)\n---\n", skill, re.DOTALL)
    if source.name != "task-implementer" or frontmatter is None:
        return "FAIL", "the source folder or frontmatter is invalid"
    name = re.search(r"(?m)^name:\s*([^\s]+)\s*$", frontmatter.group("body"))
    if name is None or name.group(1) != "task-implementer":
        return "FAIL", "the source folder and frontmatter name do not match"
    observed_commands = tuple(re.findall(r"(?m)^\$task-implementer[^\n]*$", skill))
    if observed_commands != required_commands:
        return "FAIL", "the public interface is not exactly the four canonical actions"
    policy_block = re.search(
        r"(?m)^policy:\s*(?:#.*)?\n(?P<body>(?:(?:[ \t]+[^\n]*|[ \t]*)\n?)*)",
        metadata,
    )
    implicit_policy = (
        []
        if policy_block is None
        else re.findall(
            r"(?m)^[ \t]+allow_implicit_invocation:\s*(true|false)\s*(?:#.*)?$",
            policy_block.group("body"),
        )
    )
    if implicit_policy != ["false"]:
        return "FAIL", "Task Implementer is not explicit-only"
    if any(
        f"$task-implementer {name}" in skill
        for name in ("parallel", "merge", "cleanup", "upgrade")
    ):
        return "FAIL", "an unsupported public compatibility command is present"
    return (
        "PASS",
        "explicit-only metadata and the exact four-action surface are present",
    )


def run_tests(source: Path, timeout: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in TEST_SCRIPTS:
        script = source / "scripts" / name
        if not script.is_file():
            results.append(
                {"name": name, "status": "FAIL", "detail": "test script is missing"}
            )
            continue
        try:
            completed = subprocess.run(
                [sys.executable, str(script)],
                cwd=source.parent,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            results.append({"name": name, "status": "FAIL", "detail": "test timed out"})
            continue
        detail = (
            "test suite passed"
            if completed.returncode == 0
            else f"test suite exited {completed.returncode}"
        )
        results.append(
            {
                "name": name,
                "status": "PASS" if completed.returncode == 0 else "FAIL",
                "detail": detail,
            }
        )
    return results


def run_harness_tests(harness: Path, timeout: int) -> dict[str, str]:
    scripts = harness / "scripts"
    missing = [name for name in HARNESS_TEST_SCRIPTS if not (scripts / name).is_file()]
    if missing:
        return {
            "name": "task-implementer-test helper suites",
            "status": "FAIL",
            "detail": "required helper test script is missing",
        }
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(scripts),
                "-p",
                "test_*.py",
            ],
            cwd=harness.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "name": "task-implementer-test helper suites",
            "status": "FAIL",
            "detail": "helper test suite timed out",
        }
    return {
        "name": "task-implementer-test helper suites",
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "detail": (
            "helper test suite passed"
            if completed.returncode == 0
            else f"helper test suite exited {completed.returncode}"
        ),
    }


def verify(
    source: Path,
    installed: Path,
    timeout: int,
    harness: Path | None = None,
    dependency_source: Path | None = None,
    dependency_installed: Path | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    if not source.is_dir():
        checks.append(
            {
                "name": "source",
                "status": "FAIL",
                "detail": "Task Implementer source is missing",
            }
        )
    else:
        status, detail = contract(source)
        checks.append({"name": "public contract", "status": status, "detail": detail})
        status, detail = parity(source, installed)
        checks.append({"name": "installed parity", "status": status, "detail": detail})
        if dependency_source is not None and dependency_installed is not None:
            status, detail = dependency_parity(
                dependency_source, dependency_installed
            )
            checks.append(
                {
                    "name": "project-agent-instructions parity",
                    "status": status,
                    "detail": detail,
                }
            )
        checks.extend(run_tests(source, timeout))
        if harness is not None:
            checks.append(run_harness_tests(harness, timeout))
    deterministic = (
        "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"
    )
    if deterministic == "PASS" and any(item["status"] == "PARTIAL" for item in checks):
        deterministic = "PARTIAL"
    return {
        "mode": "lightweight",
        "overall": "FAIL" if deterministic == "FAIL" else "PARTIAL",
        "deterministic": deterministic,
        "live": "NOT_RUN",
        "lifecycle": "UNCHANGED",
        "stages": [
            {
                "id": re.sub(r"[^a-z0-9]+", "-", check["name"].lower()).strip("-"),
                **check,
            }
            for check in checks
        ]
        + [
            {
                "id": "live-application",
                "name": "live application",
                "status": "NOT_RUN",
                "detail": "no real application, Docker runtime, or workers were started",
            }
        ],
        "next_action": (
            "Resolve the first deterministic failure and rerun $task-implementer-test."
            if deterministic == "FAIL"
            else "Run $task-implementer-test --create only when live disposable verification is required."
        ),
    }


def main() -> int:
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root", type=Path, default=skill_root.parent / "task-implementer"
    )
    parser.add_argument(
        "--installed-root",
        type=Path,
        default=Path.home() / ".agents" / "skills" / "task-implementer",
    )
    parser.add_argument(
        "--project-agent-instructions-source-root",
        type=Path,
        default=skill_root.parent / "project-agent-instructions",
    )
    parser.add_argument(
        "--project-agent-instructions-installed-root",
        type=Path,
        default=Path.home() / ".agents" / "skills" / "project-agent-instructions",
    )
    parser.add_argument(
        "--report", type=Path, default=default_private_root() / "report.md"
    )
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    summary = verify(
        args.source_root.resolve(),
        args.installed_root.expanduser().resolve(),
        args.timeout,
        skill_root,
        args.project_agent_instructions_source_root.resolve(),
        args.project_agent_instructions_installed_root.expanduser().resolve(),
    )
    report = args.report.expanduser().absolute()
    summary["report_path"] = str(report.resolve())
    if report.parent == default_private_root().expanduser().absolute():
        _ensure_root(report.parent)
    write_private(report, build_report(summary))
    print(
        json.dumps(
            {"summary": summary, "report": str(report.resolve())}, sort_keys=True
        )
    )
    return 1 if summary["deterministic"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
