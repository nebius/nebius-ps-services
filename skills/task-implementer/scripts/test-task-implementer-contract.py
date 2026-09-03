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
        "execution helper": read("scripts/prompt_workspace_execution.py"),
        "interop helper": read("scripts/prompt_workspace_interop.py"),
        "reporting helper": read("scripts/prompt_workspace_reporting.py"),
        "resume helper": read("scripts/prompt_workspace_resume.py"),
        "metadata": read("agents/openai.yaml"),
        "evals": read("evals/workflow-cases.md")
        + "\n"
        + read("evals/trigger-prompts.csv"),
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
            "Expose exactly these actions",
            "two-parent",
            "persistent lane",
            "The coordinator independently verifies",
            "REPLAN_REQUIRED",
            "git merge --no-ff --no-edit",
            "Fast-forward",
            "WORKER_SCOPE_VIOLATION",
            "Coordinator v1 through v6 remain unsupported",
            "durable project-intent projection",
            "resume-control-v1",
            "Spec status and receipts never",
            "Do not expose or call a lifecycle authorization bridge",
            "never requires lifecycle seal evidence",
            "project-instruction workflow remains separately responsible",
        ),
        "README.md": common
        + (
            "coordinator-owned",
            "WORKER_SCOPE_VIOLATION",
            "project-agent-instructions",
            "workspace init",
            "Help is report-only",
            "expected-old",
            "workspace remove",
            "workspace reuse",
            "Canonical Project Specs",
            "does not require terminal lifecycle seal evidence",
            "owns its prompt-impact claim/receipt schemas",
            "not a dispatch gate",
        ),
        "prompt workspace": (
            "coordinator.json",
            "task-implementer/prompt-impact-claim-v1",
            "task-implementer/prompt-impact-receipt-v1",
            "Task Implementer owns",
            "WORKFLOW_UPGRADE_REQUIRED",
            "coordinator-v7",
            "resume-control-v1",
            "No lifecycle authorization command exists",
            "never waits for a Stop hook or lifecycle seal",
        ),
        "implementation loop": common
        + (
            "exact: repo/path",
            "prefix: repo/directory",
            "git merge --no-ff --no-edit",
            "fast-forward",
            "Never use reset, stash",
            "two-parent candidate",
            "Canonical specs are root-owned project truth",
            "No lifecycle receipt",
            "Historical lifecycle artifacts are ignored",
        ),
        "handoff": (
            "## Dependency Waves",
            "Write claims",
            "Conflict domains",
            "Worker assignment: private immutable record",
            "Tasks become done only after",
            "## Effective Instructions",
            "Canonical project specs",
            "machine-readable v2 receipt",
            "## Completion Report",
            "task-implementer/run-summary-v1",
        ),
        "wave helper": (
            '"path": f"{prefix}AGENTS.md"',
            "coordinator_write_claims",
            "verify_requirements_refinement_contract",
            "def dispatch_wave",
            "def cleanup_wave",
            "def finalize_run",
        ),
        "execution helper": (
            'COORDINATOR_SCHEMA = "task-implementer/coordinator-v7"',
            'WAVE_SCHEMA = "task-implementer/wave-v4"',
            'TASK_PLANE_SCHEMA = "task-implementer/task-plane-v5"',
            'ASSIGNMENT_SCHEMA = "task-implementer/worker-assignment-v8"',
            'RESULT_SCHEMA = "task-implementer/worker-result-v4"',
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
            'LANE_REPORT_SCHEMA = "task-implementer/lane-report-v2"',
            '"--no-ext-diff"',
            '"--no-textconv"',
            "def diff_statistics",
            "def record_source_head_at_open",
            "def lane_report",
            "def render_lane_report",
            "def _lane_state_digest",
            '"WORKSPACE_BUSY"',
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
            "Concise zero-write Task Implementer lane status",
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

    helper_source = read("scripts/prompt_workspace.py")
    wave_source = read("scripts/prompt_workspace_waves.py")
    resume_source = read("scripts/prompt_workspace_resume.py")
    for retired in ("lifecycle-authorize", "contract-delta-adopt"):
        if retired in helper_source:
            failures.append(f"prompt workspace source: retained {retired!r}")
    for retired in (
        "verify_project_agent_contract(",
        "terminal_lifecycle_seal_promoted(",
        "_promote_terminal_lifecycle_seal(",
        '"LIFECYCLE_SEAL_REQUIRED"',
    ):
        if retired in wave_source:
            failures.append(f"wave helper: retained lifecycle gate {retired!r}")
    for label, source in (("wave helper", wave_source), ("resume helper", resume_source)):
        if "prompt_workspace_contract_delta" in source:
            failures.append(f"{label}: retained project-lifecycle overlay dependency")
    if (ROOT / "scripts/validate_project_specs.py").exists():
        failures.append("retained duplicate project-spec validator")
    if "lifecycle_cwd" in (ROOT / "scripts/prompt_workspace_waves.py").read_text(
        encoding="utf-8"
    ):
        failures.append("retained lifecycle-bound worker commit cwd")

    if failures:
        print("task-implementer contract smoke failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("task-implementer contract smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
