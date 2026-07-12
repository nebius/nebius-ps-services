#!/usr/bin/env python3
"""Static smoke test for the task-implementer cross-file workflow contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def require(text: str, needle: str, label: str) -> list[str]:
    if needle not in text:
        return [f"{label}: missing {needle!r}"]
    return []


def reject(text: str, needle: str, label: str) -> list[str]:
    if needle in text:
        return [f"{label}: forbidden {needle!r}"]
    return []


def main() -> int:
    skill = read("SKILL.md")
    workspace = read("references/prompt-workspace.md")
    loop = read("references/implementation-loop.md")
    handoff = read("assets/handoff-template.md")
    prompt = read("assets/prompt-template.md")
    metadata = read("agents/openai.yaml")
    evals = read("evals/trigger-prompts.md")

    checks: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "SKILL.md",
            skill,
            (
                "Requires explicit invocation",
                "workspace init",
                "workspace new",
                "prepare [--new-run] <prompt-path>",
                "run <run-id>",
                "continue <run-id>",
                "reconcile <run-id> <prompt-path>",
                "without product edits",
                "immutable bound snapshot",
                "Inspect the target code before ordering tasks",
                "vertical end-to-end slices",
                "per-task implementation plan",
                "Invoke `code-review`",
                "Invoke `$commit`",
                "fresh session",
                "ACTIVE_RUN_EXISTS",
                "NO_CHANGES",
                "PROMPT_DRIFT",
                "WORKSPACE_BUSY",
                "RUN_STATE_INVALID",
                "WORKTREE_CONFLICT",
                "global-context-management",
                "one active task",
                "Never renumber",
                "Do not write prompts into a Git worktree",
            ),
        ),
        (
            "references/prompt-workspace.md",
            workspace,
            (
                "CODE`: the canonical source scope",
                "PROMPTS`: the private flat prompt directory",
                "one editable Markdown file per independent ask",
                "YYYY-MM-DD_HHmm--<slug>.md",
                "task-implementer/prompt-v1",
                "256 KiB",
                "scripts/prompt_workspace.py",
                "process` task",
                "promptString",
                "There is no prompt-to-task 1:1 mapping",
                "The handoff is the execution truth",
                "### `prepare <prompt-path>`",
                "### `run <run-id>`",
                "### `continue <run-id>`",
                "### `reconcile <run-id> <prompt-path>`",
                "reconciliation_pending",
                "parse the binding only from `## Run`",
                "codex --add-dir",
            ),
        ),
        (
            "references/implementation-loop.md",
            loop,
            (
                "Per-Task Context, Design, And Plan",
                "vertical tasks",
                "Use `brainstorm`",
                "Route to `design`",
                "Create a short implementation plan",
                "Use `code-review`",
                "Use `$commit`",
                "Reconciliation is a planning-only transition",
                "Never use the editable prompt as execution truth",
                "Final Alignment",
            ),
        ),
        (
            "assets/handoff-template.md",
            handoff,
            (
                "Workspace manifest:",
                "Run manifest:",
                "Prompt ID:",
                "Bound revision:",
                "Bound SHA-256:",
                "Reconciliation",
                "Source prompt sections:",
                "Brainstorm/context result:",
                "Design result:",
                "Vertical slice or layers:",
                "End-to-end validation:",
                "Plan:",
                "Plan followed:",
                "Do not continue in current session: yes",
                "commit through",
            ),
        ),
        (
            "assets/prompt-template.md",
            prompt,
            (
                "schema: task-implementer/prompt-v1",
                "prompt_id:",
                "## Ask",
                "## Outcome",
                "## Acceptance criteria",
                "## Verification",
                "## Non-goals",
                "## References",
            ),
        ),
        (
            "agents/openai.yaml",
            metadata,
            (
                "allow_implicit_invocation: false",
                "prepare <prompt-path>",
                "stop without product edits",
                "brainstorm/design/plan",
                "code-review",
                "$commit",
                "reconcile",
            ),
        ),
        (
            "evals/trigger-prompts.md",
            evals,
            (
                "Should Trigger",
                "Should Not Trigger",
                "$task-implementer workspace init",
                "$task-implementer workspace new",
                "$task-implementer prepare",
                "$task-implementer run",
                "$task-implementer continue",
                "$task-implementer reconcile",
                "without product edits",
                "exactly the next pending task",
                "global-context-management",
            ),
        ),
    ]

    failures: list[str] = []
    for label, text, needles in checks:
        for needle in needles:
            failures.extend(require(text, needle, label))

    failures.extend(
        reject(skill, "codex exec /new", "SKILL.md")
    )
    failures.extend(
        reject(workspace, "runOn", "references/prompt-workspace.md")
    )

    helper_files = (
        "scripts/prompt_workspace.py",
        "scripts/prompt_workspace_core.py",
        "scripts/prompt_workspace_runs.py",
        "scripts/test-prompt-workspace.py",
    )
    for relative in helper_files:
        if not (ROOT / relative).is_file():
            failures.append(f"missing helper file: {relative}")

    if failures:
        print("FAIL task-implementer contract smoke")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("OK task-implementer contract smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
