#!/usr/bin/env python3
"""Static smoke test for the task-implementer cross-file workflow contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_README = ROOT.parent / "README.md"


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
    readme = read("README.md")
    repo_surface_present = REPO_README.is_file()
    repo_readme = (
        REPO_README.read_text(encoding="utf-8") if repo_surface_present else ""
    )

    checks: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "SKILL.md",
            skill,
            (
                "Requires explicit invocation",
                "$task-implementer workspace init [project-folder]",
                "$task-implementer run <prompt-path-or-unique-filename>",
                "Expose exactly these two actions",
                "Never require the user to supply a prompt ID, run ID",
                "create exactly one starter prompt",
                "private `plane-claim`",
                "private `plane-authorize`",
                "Product edits are",
                "`plane-checkpoint`",
                "FRESH_SESSION_REQUIRED",
                "A completed edited prompt starts a new internal run",
                "ALREADY_COMPLETE",
                "Last invoked at",
                "HUMAN_INPUT_REQUIRED",
                "STEERING_QUEUED_AFTER_TASK",
                "SPEC_OWNER_CONFLICT",
                "TI-REQ-nnn",
                "TI-DES-nnn",
                "Do not expose a separate `steer` action",
                "Invoke `code-review`",
                "Invoke `$commit`",
                "fresh session",
                "same-prompt-path-or-unique-filename",
                "WORKSPACE_BUSY",
                "RUN_STATE_INVALID",
                "WORKTREE_CONFLICT",
                "one active task",
                "Never renumber task IDs",
                "Do not expose or require internal prompt IDs",
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
                "filename date is creation",
                "Never rename a prompt, rewrite it, or deliberately change its",
                "workspace init [project-folder]",
                "run <prompt-path-or-unique-filename>",
                "private intake router",
                "plane-claim",
                "plane-authorize",
                "plane-checkpoint",
                "runtime-provided `CODEX_THREAD_ID`",
                "confirmed-recovery-worktree-sha256",
                "FRESH_SESSION_REQUIRED",
                "last_invoked_at",
                "Validation failures and lock-busy calls do not reorder prompts",
                "last invocation",
                "status",
                "title",
                "prompt path",
                "ALREADY_COMPLETE",
                "HUMAN_INPUT_REQUIRED",
                "reconcile_planning",
                "steering_queued",
                "steering-resolve",
                "spec-inspect",
                "Managed Specification Documents",
                "SPEC_OWNER_CONFLICT",
                "codex --add-dir",
            ),
        ),
        (
            "references/implementation-loop.md",
            loop,
            (
                "claims `task-1` for planning",
                "Execution Plane State Machine",
                "No product edit is",
                "plane-checkpoint",
                "distinct runtime session",
                "A-B-A",
                "Per-Task Context, Design, And Plan",
                "vertical tasks",
                "Use `brainstorm`",
                "Route to `design`",
                "Use `code-review`",
                "Use `$commit`",
                "Automatic Reconciliation",
                "Incremental Requirements And Design",
                "STEERING_QUEUED_AFTER_TASK",
                "TI-REQ-nnn",
                "TI-DES-nnn",
                "Never reuse",
                "Interruption Recovery",
                "Never create duplicate revisions",
                "run <same-prompt-path-or-unique-filename>",
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
                "Last invoked at:",
                "Execution Plane",
                "Phase: unclaimed | planning | implementation | stopped",
                "Plan SHA-256:",
                "Queue SHA-256:",
                "Checkpoint SHA-256:",
                "Reconciliation",
                "Specification State",
                "Next-task overrides:",
                "Requirement IDs:",
                "Design ID:",
                "Requirements SHA-256:",
                "Source prompt sections:",
                "Brainstorm/context result:",
                "Design result:",
                "End-to-end validation:",
                "Stop conditions:",
                "Do not continue in current session: yes",
                "run <same-prompt-path-or-unique-filename>",
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
                "## Steering",
            ),
        ),
        (
            "agents/openai.yaml",
            metadata,
            (
                "allow_implicit_invocation: false",
                "workspace init [project-folder]",
                "run <prompt-path-or-unique-filename>",
                "claim exactly one dependency-ready task",
                "exact file allowlist before product edits",
                "stop the session",
                "Reuse the same edited prompt for steering",
            ),
        ),
        (
            "evals/trigger-prompts.md",
            evals,
            (
                "Should Trigger",
                "Retired Public Actions",
                "Should Not Trigger",
                "$task-implementer workspace init",
                "$task-implementer run",
                "ALREADY_COMPLETE",
                "FRESH_SESSION_REQUIRED",
                "HUMAN_INPUT_REQUIRED",
                "STEERING_QUEUED_AFTER_TASK",
                "SPEC_OWNER_CONFLICT",
                "Never require an internal",
                "global-context-management",
            ),
        ),
        (
            "README.md",
            readme,
            (
                "Two-Command Workflow",
                "$task-implementer workspace init",
                "$task-implementer run <prompt-path-or-unique-filename>",
                "last_invoked_at",
                "ALREADY_COMPLETE",
                "execution plane",
                "## Steering",
                "docs/requirements.md",
            ),
        ),
        (
            "../README.md",
            repo_readme,
            (
                "$task-implementer workspace init services/nebius-cxcli",
                "$task-implementer run <prompt-path-or-unique-filename>",
                "Prompt IDs, run IDs,",
                "mechanically required and locked plan",
                "STEERING_QUEUED_AFTER_TASK",
                "TI-REQ-nnn",
            ),
        ),
    ]

    failures: list[str] = []
    for label, text, needles in checks:
        if label == "../README.md" and not repo_surface_present:
            continue
        for needle in needles:
            failures.extend(require(text, needle, label))

    public_docs = {
        "SKILL.md": skill,
        "README.md": readme,
        "agents/openai.yaml": metadata,
    }
    if repo_surface_present:
        public_docs["../README.md"] = repo_readme
    retired_invocations = (
        "$task-implementer workspace new",
        "$task-implementer workspace list",
        "$task-implementer prepare",
        "$task-implementer continue",
        "$task-implementer reconcile",
        "$task-implementer run <run-id>",
        "$task-implementer run --new-run",
        "$task-implementer steer",
    )
    for label, text in public_docs.items():
        for invocation in retired_invocations:
            failures.extend(reject(text, invocation, label))

    failures.extend(reject(skill, "codex exec /new", "SKILL.md"))
    failures.extend(reject(workspace, "runOn", "references/prompt-workspace.md"))
    failures.extend(
        reject(
            read("scripts/prompt_workspace.py"),
            '"--session-id"',
            "scripts/prompt_workspace.py",
        )
    )

    helper_files = (
        "scripts/prompt_workspace.py",
        "scripts/prompt_workspace_core.py",
        "scripts/prompt_workspace_intake.py",
        "scripts/prompt_workspace_execution.py",
        "scripts/prompt_workspace_runs.py",
        "scripts/prompt_workspace_specs.py",
        "scripts/test-prompt-workspace.py",
        "scripts/test-task-execution.py",
        "scripts/test-task-specs.py",
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
