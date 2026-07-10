#!/usr/bin/env python3
"""Static smoke test for the task-implementer workflow contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def require(text: str, needle: str, label: str) -> list[str]:
    if needle not in text:
        return [f"{label}: missing {needle!r}"]
    return []


def main() -> int:
    checks: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "SKILL.md",
            read("SKILL.md"),
            (
                "Use only when the user explicitly asks",
                "bigger than one coherent task",
                "vertical",
                "brainstorm",
                "design",
                "per-task implementation plan",
                "code-review",
                "$commit",
                "fresh Codex session",
                "WORKTREE_CONFLICT",
                "global-context-management",
            ),
        ),
        (
            "references/implementation-loop.md",
            read("references/implementation-loop.md"),
            (
                "Per-Task Context, Design, And Plan",
                "vertical tasks",
                "Use `brainstorm`",
                "Route to `design`",
                "Create a short implementation plan",
                "Use `$commit`",
            ),
        ),
        (
            "assets/handoff-template.md",
            read("assets/handoff-template.md"),
            (
                "Brainstorm/context result:",
                "Design result:",
                "Vertical slice or layers:",
                "End-to-end validation:",
                "Plan:",
                "Plan followed:",
                "Do not continue in current session: yes",
                "commit through $commit",
            ),
        ),
        (
            "agents/openai.yaml",
            read("agents/openai.yaml"),
            (
                "allow_implicit_invocation: false",
                "vertical",
                "gather context with brainstorm",
                "route design or contract choices through design",
                "write a short plan",
            ),
        ),
        (
            "evals/trigger-prompts.md",
            read("evals/trigger-prompts.md"),
            (
                "Should Trigger",
                "Should Not Trigger",
                "global-context-management",
                "vertical",
                "per-task implementation and commit loop",
            ),
        ),
    ]

    failures: list[str] = []
    for label, text, needles in checks:
        for needle in needles:
            failures.extend(require(text, needle, label))

    if failures:
        print("FAIL task-implementer contract smoke")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("OK task-implementer contract smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
