#!/usr/bin/env python3
"""Static smoke test for the dependency-wave cross-file contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_README = ROOT.parent / "README.md"
CHANGELOG = ROOT.parent / "CHANGELOG.md"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    source_checkout = REPO_README.is_file() and CHANGELOG.is_file()
    surfaces = {
        "SKILL.md": read("SKILL.md"),
        "README.md": read("README.md"),
        "prompt workspace": read("references/prompt-workspace.md"),
        "implementation loop": read("references/implementation-loop.md"),
        "handoff": read("assets/handoff-template.md"),
        "metadata": read("agents/openai.yaml"),
        "evals": read("evals/trigger-prompts.md"),
        "repo README": REPO_README.read_text(encoding="utf-8")
        if source_checkout
        else "",
        "changelog": CHANGELOG.read_text(encoding="utf-8") if source_checkout else "",
    }
    common = (
        "dependency wave",
        "full-repository",
        "fast-forward",
    )
    required = {
        "SKILL.md": common
        + (
            "Requires explicit invocation",
            "$task-implementer workspace init [project-folder]",
            "$task-implementer run <prompt-path-or-unique-filename>",
            "Expose exactly these two actions",
            "coordinator never implements a worker task",
            "WORKFLOW_UPGRADE_REQUIRED",
            "REPLAN_REQUIRED",
            "UNSUPPORTED_SUBMODULE_SCOPE",
            "git merge --no-ff --no-edit",
            "git merge --ff-only",
            "git worktree remove",
            "git branch -d",
            "task-watch",
            "every 30 seconds",
            "300 seconds",
            "420 seconds",
            "self-contained",
            "assignment and incoming handoff",
            "first private transition",
            "autonomous heartbeat loop",
            "READ_ONLY_DEADLINE_NEAR",
            "WORKER_SCOPE_VIOLATION",
            "task-start` is single-use",
            "replacement worker invokes private `task-recover",
            "isolated correction tail",
            "coordinator-v1/v2/v3 runs are unsupported",
        ),
        "README.md": common
        + (
            "capacity-sized batches",
            "exactly one direct-child commit",
            "coordinator-owned",
            "WORKFLOW_UPGRADE_REQUIRED",
            "every 30 seconds",
            "300 seconds",
            "420 seconds",
            "assignment-only fresh context",
            "first private transition",
            "background or autonomous heartbeat loops",
            "WORKER_SCOPE_VIOLATION",
        ),
        "prompt workspace": (
            "coordinator.json",
            "worker-assignment-v7",
            "embedded assignment digest unchanged",
            "must not guess or recompute",
            "wave-plan",
            "wave-replan",
            "task-arm",
            "task-recover",
            "replacement worker",
            "isolated correction tail",
            "task-heartbeat",
            "task-watch",
            "wave-promote",
            "STEERING_QUEUED_AFTER_WAVE",
            "WORKFLOW_UPGRADE_REQUIRED",
        ),
        "implementation loop": common
        + (
            "exact: repo/path",
            "prefix: repo/directory",
            "class:stable-key",
            "git merge --no-ff --no-edit",
            "git merge --ff-only",
            "Never cherry-pick",
            "first private transition",
        ),
        "handoff": (
            "## Dependency Waves",
            "Write claims",
            "Conflict domains",
            "Worker assignment: private immutable record",
            "Tasks become done only after",
        ),
        "metadata": (
            "allow_implicit_invocation: false",
            "isolated full-repository worktrees",
            "ff-only merge",
        ),
        "evals": (
            "five completely disjoint tasks",
            "capacity must not change logical waves",
            "STEERING_QUEUED_AFTER_WAVE",
            "$task-implementer parallel",
            "generic parallel requests do not trigger",
        ),
        "repo README": (
            "deterministic dependency waves",
            "full-repository linked",
            "git merge --ff-only",
            "WORKFLOW_UPGRADE_REQUIRED",
        ),
        "changelog": (
            "coordinator state to v4",
            "capacity-sized batches",
            "WORKFLOW_UPGRADE_REQUIRED",
        ),
    }
    if not source_checkout:
        for label in ("repo README", "changelog"):
            surfaces.pop(label)
            required.pop(label)
    failures: list[str] = []
    for label, needles in required.items():
        folded = surfaces[label].casefold()
        for needle in needles:
            if needle.casefold() not in folded:
                failures.append(f"{label}: missing {needle!r}")

    stale = (
        "private `plane-claim`",
        "plane-authorize",
        "plane-checkpoint",
        "STEERING_QUEUED_AFTER_TASK",
        "Do not run parallel write-capable",
        "implements exactly one dependency-ready task per fresh session",
    )
    for label, text in surfaces.items():
        for needle in stale:
            if needle in text:
                failures.append(f"{label}: stale v1 contract {needle!r}")

    if failures:
        print("task-implementer contract smoke failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("task-implementer contract smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
