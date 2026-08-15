#!/usr/bin/env python3
"""Static smoke test for the dependency-wave cross-file contract."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
REPO_README = ROOT.parent / "README.md"
CHANGELOG = ROOT.parent / "CHANGELOG.md"
REQUIREMENTS = ROOT.parent / "docs" / "requirements.md"
DESIGN = ROOT.parent / "docs" / "design.md"


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
        "wave helper": read("scripts/prompt_workspace_waves.py"),
        "contract-delta helper": read("scripts/prompt_workspace_contract_delta.py"),
        "execution helper": read("scripts/prompt_workspace_execution.py"),
        "interop helper": read("scripts/prompt_workspace_interop.py"),
        "reporting helper": read("scripts/prompt_workspace_reporting.py"),
        "resume helper": read("scripts/prompt_workspace_resume.py"),
        "metadata": read("agents/openai.yaml"),
        "evals": read("evals/trigger-prompts.md"),
        "repo README": REPO_README.read_text(encoding="utf-8")
        if source_checkout
        else "",
        "changelog": CHANGELOG.read_text(encoding="utf-8") if source_checkout else "",
        "requirements": REQUIREMENTS.read_text(encoding="utf-8")
        if source_checkout
        else "",
        "design": DESIGN.read_text(encoding="utf-8") if source_checkout else "",
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
            "$task-implementer workspace reuse [project-folder]",
            "$task-implementer run <prompt-ref-or-file>",
            "$task-implementer integrate [project-folder]",
            "$task-implementer workspace remove [project-folder]",
            "$task-implementer --help",
            "Expose exactly these five actions",
            "repository-wide",
            "monotonic",
            "two-parent",
            "same persistent lane",
            "coordinator never implements a worker task",
            "WORKFLOW_UPGRADE_REQUIRED",
            "REPLAN_REQUIRED",
            "UNSUPPORTED_SUBMODULE_SCOPE",
            "git merge --no-ff --no-edit",
            "git merge --ff-only",
            "git worktree remove",
            "git update-ref -d",
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
            "coordinator confirms that it",
            "task-rearm --confirmed-stopped",
            "WORKER_START_LEASE_CONFLICT",
            "WORKER_START_LEASE_INVALID",
            "cannot start the task",
            "isolated correction tail",
            "coordinator-v1/v2/v3/v4/v5/v6 runs are unsupported",
            "$project-agent-instructions",
            "spec owner",
            "public `$worktree` lifecycle actions",
            "never selects or invokes a public Task Implementer action",
            "Direct-prompt capture never supplies a",
            "durable project-intent projection",
            "shell/tool",
            "Capture failures never block the direct",
            "exact duplicates never append",
            "chore(task-implementer): checkpoint managed lane",
            "post-checkpoint",
            "authoritative coordinator-v7 execution state",
            "resume-control-v1",
            "`execute`, `wait`,",
            "`requires_confirmation`, `blocked`, or `complete`",
            "RESUME_STALE",
            "worker_session_fingerprint_sha256",
            "prepare_argv",
            "PATH-canonical",
            "result_context",
            "publication_cwd",
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
            "assignment-scoped fresh context",
            "first private transition",
            "background or autonomous heartbeat loops",
            "WORKER_SCOPE_VIOLATION",
            "project-agent-instructions",
            "workspace init",
            "$task-implementer --help",
            "not a sixth workflow action",
            "five-action",
            "pending generations",
            "expected-old",
            "Five Git Roles Plus Private State",
            "Internal wave integration during `run`",
            "resource-free fresh `run` is the one exception",
            "repo-root `git add -A`",
            "checkpoint managed lane",
            "workspace remove",
            "workspace reuse",
            "direct prompt still runs normally in the current agent",
            "Capture never starts",
            "never stores the submitted body",
            "merge/no-op/sensitive",
            "operation-and-projection marker",
            "artifact-specific current truth",
            "resume-control v1",
            "worker_session_fingerprint_sha256",
            "CODEX_THREAD_ID",
            "PATH-canonical",
            "result_context",
            "publication_cwd",
        ),
        "prompt workspace": (
            "coordinator.json",
            "worker-assignment-v7",
            "start_context",
            "start_argv",
            "worker_context",
            "recover_argv",
            "must not guess or recompute",
            "wave-plan",
            "checkpoint-prepare",
            "lane-checkpoint-preparation.json",
            "wave-replan",
            "task-arm",
            "task-rearm",
            "task-recover",
            "replacement worker",
            "exact observed start lease",
            "isolated correction tail",
            "task-heartbeat",
            "task-watch",
            "wave-promote",
            "STEERING_QUEUED_AFTER_WAVE",
            "WORKFLOW_UPGRADE_REQUIRED",
            "workspace-v2",
            "monotonic generation",
            "no unmanaged execution mode",
            "proceeds normally in the current agent",
            "Capture does not route through run intake",
            "metadata-only event-v2",
            "commands that define",
            "never auto-rebase",
            "lane-checkpoint.json",
            "checkpoint-and-generation-open",
            "coordinator-v7",
            "resume-control-v1",
            "machine-readable state owns execution routing",
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
            "$project-agent-instructions",
            "selected-project root `AGENTS.md` tail",
            "For an expired prestart task",
            "For an interrupted running task",
            "Persistent Lane Integration",
            "$task-implementer integrate",
            "two-parent candidate",
            "pure resume planner",
            "A nonterminal intent replays",
            "worker-commit-context-v1",
            "worker_session_fingerprint_sha256",
            "prepare_argv",
            "worker-result-context-v1",
            "publication_cwd",
            "start_lease",
            "publish_argv",
            "result_sha256",
        ),
        "handoff": (
            "## Dependency Waves",
            "Write claims",
            "Conflict domains",
            "Worker assignment: private immutable record",
            "Tasks become done only after",
            "## Project Agent Instructions",
            "project-agent-instructions.spec-validation.v3",
            "project-agent-instructions.decision.v3",
            "project-agent-instructions.state.v3",
            "attached",
            "Reload required",
            "## Completion Report",
            "task-implementer/run-summary-v1",
        ),
        "wave helper": (
            '"path": f"{prefix}AGENTS.md"',
            "coordinator_write_claims",
            "verify_project_agent_contract",
            "verify_requirements_refinement_contract",
            "terminal_lifecycle_seal_promoted",
        ),
        "contract-delta helper": (
            'TERMINAL_SEAL_SCHEMA = "task-implementer/terminal-lifecycle-seal-v1"',
            "TERMINAL_SEAL_MESSAGE",
            "def terminal_lifecycle_seal_active",
            "def terminal_lifecycle_seal_promoted",
            "def prepare_terminal_lifecycle_promotion",
            "def recover_terminal_lifecycle_promotion",
            "TERMINAL_RECOVERY_SCHEMA",
            "def terminal_lifecycle_recovery_promoted",
            "def _recover_released_terminal_lifecycle",
        ),
        "execution helper": (
            'COORDINATOR_SCHEMA = "task-implementer/coordinator-v7"',
            'WAVE_SCHEMA = "task-implementer/wave-v4"',
            'TASK_PLANE_SCHEMA = "task-implementer/task-plane-v5"',
            'ASSIGNMENT_SCHEMA = "task-implementer/worker-assignment-v7"',
            'RESULT_SCHEMA = "task-implementer/worker-result-v3"',
            'INCOMING_HANDOFF_SCHEMA = "task-implementer/incoming-handoff-v1"',
            "start a new v7 run",
        ),
        "interop helper": (
            "SCHEMA = 4",
            "observe_managed_state",
            "without repairing local interop state",
        ),
        "reporting helper": (
            'SUMMARY_SCHEMA = "task-implementer/run-summary-v1"',
            '"--no-ext-diff"',
            '"--no-textconv"',
            "def diff_statistics",
            "def record_source_head_at_open",
            "def lane_report",
            "summary unavailable for legacy generation",
            "queue_activation_pending",
        ),
        "resume helper": (
            'RESUME_CONTROL_SCHEMA = "task-implementer/resume-control-v1"',
            '"execute", "wait", "requires_confirmation", "blocked", "complete"',
            "def plan_run_resume",
            "def begin_resume_transition",
            "def reconcile_handoff_projection",
            "def effective_run_status",
        ),
        "metadata": (
            "allow_implicit_invocation: false",
            "five-action Task Implementer workflow",
            "persistent Worktree-owned project",
            "repository-wide claims",
            "Never call public $worktree",
            "whole-repository direct-child commit",
        ),
        "evals": (
            "$task-implementer --help",
            "$task-implementer -h",
            "exactly five workflow actions",
            "$task-implementer workspace reuse",
            "$task-implementer integrate",
            "$task-implementer workspace remove",
            "call additional tools",
            "five completely disjoint tasks",
            "capacity must not change logical waves",
            "STEERING_QUEUED_AFTER_WAVE",
            "$task-implementer parallel",
            "generic parallel requests do not trigger",
            "dirty source",
            "pending generation",
        ),
        "repo README": (
            "deterministic dependency waves",
            "full-repository linked",
            "git merge --ff-only",
            "WORKFLOW_UPGRADE_REQUIRED",
            "project-agent-instructions",
            "persistent full-repository lane",
            "repository-wide exact/prefix",
            "workspace remove",
            "Public `$worktree`",
            "every direct prompt still runs normally in the current agent",
            "without starting or resuming the workflow",
            "never stores the submitted body",
            "durable project intent",
            "post-checkpoint clean `HEAD`",
        ),
        "changelog": (
            "coordinator state to v5",
            "capacity-sized batches",
            "WORKFLOW_UPGRADE_REQUIRED",
            "persistent Worktree-owned per-project",
            "monotonic generations",
            "Every direct prompt continues to the",
            "never starts or resumes a",
            "metadata-only event-v2",
            "operation ID and accepted projection digest",
            "built-in Task Implementer pre-run checkpoint",
            "$task-implementer workspace reuse [project-folder]",
            "run-summary-v1",
            "CODE — MANAGED PERSISTENT LANE",
        ),
        "requirements": (
            "REQ-013",
            "$task-implementer workspace reuse [project-folder]",
            "WORKSPACE_NOT_FOUND",
            "creates nothing",
            "REQ-020",
            "run-summary-v1",
        ),
        "design": (
            "FEAT-012",
            "Existing-only workspace reopen",
            "anchor-inspect",
            "exactly five public actions",
            "FEAT-017",
            "resume-control-v1",
            "FEAT-019",
            "Immutable Task Implementer completion reporting",
        ),
    }
    if not source_checkout:
        for label in ("repo README", "changelog", "requirements", "design"):
            surfaces.pop(label)
            required.pop(label)
    failures: list[str] = []
    for label, needles in required.items():
        folded = " ".join(surfaces[label].casefold().split())
        for needle in needles:
            normalized_needle = " ".join(needle.casefold().split())
            if normalized_needle not in folded:
                failures.append(f"{label}: missing {needle!r}")

    stale = (
        "private `plane-claim`",
        "plane-authorize",
        "plane-checkpoint",
        "STEERING_QUEUED_AFTER_TASK",
        "Do not run parallel write-capable",
        "implements exactly one dependency-ready task per fresh session",
        "final v2 state",
        "verified v2 project-agent-instructions state",
        "hook-routed accepted direct-prompt continuation",
        "this same run path executes once",
        "v6 execution state",
        "durable v5 truth",
        "V4 Execution State Ownership",
        "canonical v4 execution state",
        "start a new v5 run",
        "resume recorded v3 transition",
        "v2 coordinator/wave state",
    )
    for label, text in surfaces.items():
        for needle in stale:
            if needle in text:
                failures.append(f"{label}: stale v1 contract {needle!r}")

    helper_help = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "prompt_workspace.py"), "--help"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    ).stdout
    if "lifecycle-authorize" in helper_help:
        failures.append("prompt workspace help: exposes the private lifecycle adapter")
    if "plan-digest-recover" in helper_help:
        failures.append("prompt workspace help: exposes the private plan-digest repair")
    if "contract-delta-adopt" in helper_help:
        failures.append(
            "prompt workspace help: exposes private contract-delta adoption"
        )
    if "handoff-projection-recover" in helper_help:
        failures.append("prompt workspace help: exposes private projection recovery")

    if failures:
        print("task-implementer contract smoke failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("task-implementer contract smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
