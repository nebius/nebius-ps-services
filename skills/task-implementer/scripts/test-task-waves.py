#!/usr/bin/env python3
"""Disposable real-Git tests for dependency-wave worktree lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import prompt_workspace as pw
import prompt_workspace_lanes as lanes
import prompt_workspace_waves as waves
from prompt_workspace_core import PromptWorkspaceError
from prompt_workspace_execution import (
    RESULT_SCHEMA,
    WORKER_GUARDRAILS,
    WORKER_HEARTBEAT_SECONDS,
    WORKER_INTEGRATION_READ_ONLY_SECONDS,
    WORKER_INTEGRATION_WARNING_SECONDS,
    WORKER_MAX_SECONDS,
    WORKER_START_SECONDS,
    WORKER_STALL_SECONDS,
    WORKER_STANDARD_READ_ONLY_SECONDS,
    WORKER_STANDARD_WARNING_SECONDS,
    sha256_json,
    worker_liveness_profile,
)


FIXED = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def git(*arguments: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class WorktreeWaveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspace with spaces"
        self.root.mkdir()
        self.origin = self.root / "origin.git"
        git("init", "--bare", "-q", str(self.origin), cwd=self.root)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git("init", "-q", "-b", "trunk", cwd=self.repo)
        git("config", "user.name", "Wave Test", cwd=self.repo)
        git("config", "user.email", "wave@example.invalid", cwd=self.repo)
        self.scope = self.repo / "services" / "example"
        self.scope.mkdir(parents=True)
        for name in ("one.txt", "two.txt", "three.txt"):
            (self.scope / name).write_text("base\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("ignored.env\n", encoding="utf-8")
        (self.repo / "ignored.env").write_text("must not be copied\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "initial", cwd=self.repo)
        self.initial = git("rev-parse", "HEAD", cwd=self.repo)
        default_branch = git("branch", "--show-current", cwd=self.repo)
        self.default_branch = default_branch
        git("remote", "add", "origin", str(self.origin), cwd=self.repo)
        git("push", "-qu", "origin", default_branch, cwd=self.repo)
        git(
            "symbolic-ref",
            "HEAD",
            f"refs/heads/{default_branch}",
            cwd=self.origin,
        )
        git("fetch", "-q", "origin", cwd=self.repo)
        git(
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            f"refs/remotes/origin/{default_branch}",
            cwd=self.repo,
        )
        git("switch", "-qc", "wave-feature", cwd=self.repo)
        self.codex_home = self.root / "codex home"
        lane = lanes.ensure_project_lane(self.scope)
        lane_root = Path(str(lane["worktree"]))
        initialized = pw.init_workspace(
            lane_root,
            "services/example",
            self.codex_home,
            lane=lane,
            clock=lambda: FIXED,
        )
        self.primary = self.repo
        self.repo = lane_root
        self.scope = Path(str(lane["scope_cwd"]))
        self.workspace = Path(initialized["workspace"])
        prompt = pw.create_prompt(
            self.workspace,
            "Implement two independent tasks then one dependent task",
            clock=lambda: FIXED,
            id_factory=lambda: "b" * 32,
        )
        self.prompt = Path(prompt["path"])
        text = self.prompt.read_text(encoding="utf-8")
        text = (
            text.replace(
                "<!-- Required: describe what must be true when the work is complete. -->",
                "All three files are updated.",
            )
            .replace(
                "- [ ] <!-- Required: add an observable, testable completion criterion. -->",
                "- [ ] All wave commits are promoted.",
            )
            .replace(
                "<!-- Required: name expected checks or ask Codex to derive them from the repo. -->",
                "Inspect the final files.",
            )
        )
        self.prompt.write_text(text, encoding="utf-8")
        self.prompt.chmod(0o600)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            self.prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED,
        )
        self.run_id = str(snapshot["run_id"])
        self.run_dir = Path(snapshot["manifest"]).parent
        self._write_handoff()

    def tearDown(self) -> None:
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "list"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.temporary.cleanup()

    def _write_handoff(self) -> None:
        manifest_path = self.run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bound = manifest["revisions"][0]
        tasks = []
        for number, name, dependency in (
            (1, "one.txt", "none"),
            (2, "two.txt", "none"),
            (3, "three.txt", "task-1, task-2"),
        ):
            tasks.append(
                f"""### task-{number}

- Status: pending
- Depends on: {dependency}
- Write claims: exact: services/example/{name}
- Conflict domains: files:{name}
- Implementation steps: update only services/example/{name}
- Validation: inspect {name}
- End-to-end validation: verify the dependency wave result
- Done criteria: {name} contains the task update
"""
            )
        handoff = f"""# Task Implementer Handoff

## Run

- Run ID: {self.run_id}
- Run manifest: {manifest_path}
- Prompt ID: {manifest["prompt_id"]}
- Bound revision: {bound["revision"]}
- Bound SHA-256: {bound["sha256"]}
- Bound snapshot path: {self.run_dir / bound["snapshot"]}
- Last invoked at: 2026-07-14T12:00:00+00:00
- Overall status: running

## Reconciliation

- State: none

## Task Queue

{"".join(tasks)}
"""
        path = self.run_dir / "handoff.md"
        path.write_text(handoff, encoding="utf-8")
        path.chmod(0o600)

    def test_replan_without_coordinator_reports_current_schema(self) -> None:
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.replan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")
        self.assertEqual(str(raised.exception), "run has no v6 coordinator")

    def _complete_worker(self, task_id: str, filename: str) -> dict[str, object]:
        assignment_path = (
            self.run_dir
            / "orchestration"
            / "assignments"
            / "wave-001"
            / f"{task_id}.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        self.assertEqual(assignment["worker_guardrails"], WORKER_GUARDRAILS)
        self.assertEqual(Path(assignment["workspace_manifest"]), self.workspace)
        self.assertEqual(
            Path(assignment["helper_path"]).resolve(),
            Path(pw.__file__).resolve(),
        )
        self.assertIn(
            "pass the embedded assignment_sha256 unchanged",
            assignment["worker_guardrails"],
        )
        self.assertIn(
            "never recompute it with ad hoc JSON", assignment["worker_guardrails"]
        )
        self.assertEqual(assignment["start_seconds"], WORKER_START_SECONDS)
        self.assertEqual(assignment["heartbeat_seconds"], WORKER_HEARTBEAT_SECONDS)
        liveness = worker_liveness_profile(assignment["dependencies"])
        self.assertEqual(assignment["worker_profile"], liveness["worker_profile"])
        self.assertEqual(
            assignment["read_only_warning_seconds"],
            liveness["read_only_warning_seconds"],
        )
        self.assertEqual(assignment["read_only_seconds"], liveness["read_only_seconds"])
        self.assertEqual(assignment["stall_seconds"], WORKER_STALL_SECONDS)
        self.assertEqual(assignment["max_worker_seconds"], WORKER_MAX_SECONDS)
        worktree = Path(assignment["worktree"])
        scope_cwd = Path(assignment["scope_cwd"])
        self.assertEqual(scope_cwd, worktree / "services" / "example")
        self.assertFalse((worktree / "ignored.env").exists())
        previous = Path.cwd()
        pw.arm_task(self.workspace, self.run_id, task_id, clock=lambda: FIXED)
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                task_id,
                assignment["assignment_sha256"],
                session_id=f"session-{task_id}",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)
        (scope_cwd / filename).write_text(f"{task_id}\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", f"Implement {task_id}", cwd=worktree)
        commit = git("rev-parse", "HEAD", cwd=worktree)
        result = {
            "schema": RESULT_SCHEMA,
            "run_id": self.run_id,
            "wave_id": "wave-001",
            "task_id": task_id,
            "assignment_sha256": assignment["assignment_sha256"],
            "status": "committed",
            "commit": commit,
            "changed_paths": [f"services/example/{filename}"],
            "summary": f"Implemented {task_id}",
            "decisions": [],
            "open_risks": [],
            "validation": "focused validation passed",
            "end_to_end_validation": "task behavior observed",
            "code_review": "code-review completed with no findings",
            "completed_at": "2026-07-14T12:01:00+00:00",
        }
        result["result_sha256"] = sha256_json(result)
        result_path = (
            self.run_dir / "orchestration" / "results" / "wave-001" / f"{task_id}.json"
        )
        result_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result_path.chmod(0o600)
        return pw.accept_task_result(
            self.workspace, self.run_id, task_id, clock=lambda: FIXED
        )

    def _integrated_first_wave(self) -> tuple[dict[str, object], Path, Path]:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        self._complete_worker("task-1", "one.txt")
        self._complete_worker("task-2", "two.txt")
        integrated = pw.integrate_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration = Path(str(prepared["integration_worktree"]))
        evidence = self.run_dir / "orchestration" / "evidence" / "wave-001.json"
        evidence.parent.mkdir(mode=0o700)
        evidence.write_text(
            json.dumps(
                {
                    "integration_head": git("rev-parse", "HEAD", cwd=integration),
                    "bound_revision": "r0001",
                    "steering_sha256": "none",
                    "validation": "combined tests passed",
                    "code_review": "integration review completed",
                    "steering_reconciled": True,
                }
            ),
            encoding="utf-8",
        )
        evidence.chmod(0o600)
        return integrated, integration, evidence

    def test_full_repository_worktrees_ordered_merge_ff_promotion_and_cleanup(
        self,
    ) -> None:
        plan = pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        self.assertNotEqual(plan["base_branch"], plan["default_branch"])
        self.assertEqual(plan["promotion_source"], "managed-local")
        self.assertEqual(
            git("branch", "--show-current", cwd=self.repo), plan["base_branch"]
        )
        self.assertEqual(
            [item["batches"] for item in plan["waves"]],
            [[["task-1"], ["task-2"]], [["task-3"]]],
        )
        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration = Path(str(prepared["integration_worktree"]))
        self.assertEqual(git("rev-parse", "HEAD", cwd=integration), self.initial)
        dispatched = pw.dispatch_wave(
            self.workspace, self.run_id, self.initial, clock=lambda: FIXED
        )
        self.assertEqual(len(dispatched["assignments"]), 1)
        first_handoff = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "incoming-handoffs"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(first_handoff["dependencies"], [])
        self.assertEqual(first_handoff["predecessors"], [])
        task_one_result = self._complete_worker("task-1", "one.txt")
        next_batch = pw.advance_batch(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(len(next_batch["assignments"]), 1)
        second_handoff = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "incoming-handoffs"
                / "wave-001"
                / "task-2.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            [item["task_id"] for item in second_handoff["predecessors"]],
            ["task-1"],
        )
        second_assignment = json.loads(
            Path(next_batch["assignments"][0]).read_text(encoding="utf-8")
        )
        pw.arm_task(self.workspace, self.run_id, "task-2", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(Path(second_assignment["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as reused:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-2",
                    second_assignment["assignment_sha256"],
                    session_id="session-task-1",
                    clock=lambda: FIXED,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(reused.exception.code, "FRESH_SESSION_REQUIRED")
        self._complete_worker("task-2", "two.txt")
        self.assertEqual(
            pw.accept_task_result(
                self.workspace, self.run_id, "task-1", clock=lambda: FIXED
            ),
            task_one_result,
        )
        integrated = pw.integrate_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration_tip = git("rev-parse", "HEAD", cwd=integration)
        self.assertEqual(integrated["status"], "promotion_pending")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)
        first_parent = git(
            "rev-list",
            "--first-parent",
            "--reverse",
            f"{self.initial}..{integration_tip}",
            cwd=integration,
        ).splitlines()
        merge_commits = [
            commit
            for commit in first_parent
            if len(
                git("rev-list", "--parents", "-n", "1", commit, cwd=integration).split()
            )
            == 3
        ]
        task_commits = [
            json.loads(
                (
                    self.run_dir
                    / "orchestration"
                    / "tasks"
                    / "wave-001"
                    / f"task-{number}.json"
                ).read_text(encoding="utf-8")
            )["commit"]
            for number in (1, 2)
        ]
        self.assertEqual(
            [
                git("rev-parse", f"{commit}^2", cwd=integration)
                for commit in merge_commits
            ],
            task_commits,
        )
        evidence = self.run_dir / "orchestration" / "evidence" / "wave-001.json"
        evidence.parent.mkdir(mode=0o700)
        evidence.write_text(
            json.dumps(
                {
                    "integration_head": integration_tip,
                    "bound_revision": "r0001",
                    "steering_sha256": "none",
                    "validation": "combined tests passed",
                    "code_review": "integration review completed",
                    "steering_reconciled": True,
                }
            ),
            encoding="utf-8",
        )
        evidence.chmod(0o600)
        git("merge", "--ff-only", str(integrated["integration_branch"]), cwd=self.repo)
        dirty = self.repo / "dirty.txt"
        dirty.write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(PromptWorkspaceError) as dirty_promotion:
            pw.promote_wave(self.workspace, self.run_id, evidence, clock=lambda: FIXED)
        self.assertEqual(dirty_promotion.exception.code, "PROMOTION_BLOCKED")
        dirty.unlink()
        task_one_assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        cleanup_dirty = Path(task_one_assignment["worktree"]) / "cleanup-dirty.txt"
        cleanup_dirty.write_text("retain\n", encoding="utf-8")
        with self.assertRaises(PromptWorkspaceError) as retained_worker:
            pw.promote_wave(self.workspace, self.run_id, evidence, clock=lambda: FIXED)
        self.assertEqual(retained_worker.exception.code, "CLEANUP_BLOCKED")
        cleanup_dirty.unlink()
        promoted = pw.promote_wave(
            self.workspace, self.run_id, evidence, clock=lambda: FIXED
        )
        self.assertEqual(promoted["promoted_head"], integration_tip)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), integration_tip)
        self.assertEqual(
            (self.scope / "one.txt").read_text(encoding="utf-8"), "task-1\n"
        )
        self.assertEqual(
            (self.scope / "two.txt").read_text(encoding="utf-8"), "task-2\n"
        )
        cleaned = pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(cleaned["status"], "done")
        worktrees = git("worktree", "list", "--porcelain", cwd=self.repo)
        self.assertEqual(worktrees.count("worktree "), 2)
        self.assertNotIn(
            "codex/ti-", git("branch", "--format=%(refname:short)", cwd=self.repo)
        )
        coordinator = json.loads(
            (self.run_dir / "orchestration" / "coordinator.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(coordinator["active_wave"], "wave-002")
        coordinator["active_wave"] = "wave-001"
        coordinator_path = self.run_dir / "orchestration" / "coordinator.json"
        coordinator_path.write_text(
            json.dumps(coordinator, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        coordinator_path.chmod(0o600)
        pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        repaired = json.loads(coordinator_path.read_text(encoding="utf-8"))
        self.assertEqual(repaired["active_wave"], "wave-002")
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(
            self.workspace, self.run_id, integration_tip, clock=lambda: FIXED
        )
        dependent_handoff = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "incoming-handoffs"
                / "wave-002"
                / "task-3.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(dependent_handoff["dependencies"], ["task-1", "task-2"])
        self.assertEqual(
            [item["task_id"] for item in dependent_handoff["predecessors"]],
            ["task-1", "task-2"],
        )
        integration_assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-002"
                / "task-3.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(integration_assignment["worker_profile"], "integration")
        self.assertEqual(
            integration_assignment["read_only_warning_seconds"],
            WORKER_INTEGRATION_WARNING_SECONDS,
        )
        self.assertEqual(
            integration_assignment["read_only_seconds"],
            WORKER_INTEGRATION_READ_ONLY_SECONDS,
        )
        integration_scope = Path(integration_assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-3", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(integration_scope)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-3",
                integration_assignment["assignment_sha256"],
                session_id="integration-boundary-worker",
                clock=lambda: FIXED,
            )

            for elapsed in range(30, 331, 30):
                pw.heartbeat_task(
                    self.workspace,
                    self.run_id,
                    "task-3",
                    integration_assignment["assignment_sha256"],
                    "implementing",
                    session_id="integration-boundary-worker",
                    clock=lambda elapsed=elapsed: FIXED + timedelta(seconds=elapsed),
                )
        finally:
            os.chdir(previous)
        integration_warning = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-3",
            clock=lambda: FIXED + timedelta(seconds=360),
        )
        self.assertEqual(integration_warning["status"], "ACTIVE")
        self.assertEqual(integration_warning["warning"], "READ_ONLY_DEADLINE_NEAR")
        integration_timeout = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-3",
            clock=lambda: FIXED + timedelta(seconds=420),
        )
        self.assertEqual(integration_timeout["status"], "WORKER_READ_ONLY_TIMEOUT")
        self.assertFalse(integration_timeout["progress_observed"])
        journal = self.run_dir / "orchestration" / "journals" / "wave-001.jsonl"
        for line in journal.read_text(encoding="utf-8").splitlines():
            self.assertIsInstance(json.loads(line), dict)

    def test_remote_default_head_drift_does_not_affect_lane_promotion(
        self,
    ) -> None:
        _integrated, integration, evidence = self._integrated_first_wave()
        integration_tip = git("rev-parse", "HEAD", cwd=integration)
        tree = git("rev-parse", "HEAD^{tree}", cwd=self.repo)
        default_head = git("rev-parse", "HEAD", cwd=self.origin)
        advanced = git(
            "commit-tree",
            tree,
            "-p",
            default_head,
            "-m",
            "advance remote default",
            cwd=self.repo,
        )
        git(
            "push",
            "-q",
            "origin",
            f"{advanced}:refs/heads/{self.default_branch}",
            cwd=self.repo,
        )
        promoted = pw.promote_wave(
            self.workspace, self.run_id, evidence, clock=lambda: FIXED
        )
        self.assertEqual(promoted["promoted_head"], integration_tip)

    def test_remote_default_advance_during_cleanup_does_not_affect_lane(self) -> None:
        _integrated, integration, evidence = self._integrated_first_wave()
        integration_tip = git("rev-parse", "HEAD", cwd=integration)
        original_cleanup = waves._cleanup_resource
        advanced = False

        def cleanup_then_advance(**kwargs: object) -> bool:
            nonlocal advanced
            cleaned = original_cleanup(**kwargs)
            if not advanced:
                tree = git("rev-parse", "HEAD^{tree}", cwd=self.repo)
                default_head = git("rev-parse", "HEAD", cwd=self.origin)
                remote_tip = git(
                    "commit-tree",
                    tree,
                    "-p",
                    default_head,
                    "-m",
                    "advance default during cleanup",
                    cwd=self.repo,
                )
                git(
                    "push",
                    "-q",
                    "origin",
                    f"{remote_tip}:refs/heads/{self.default_branch}",
                    cwd=self.repo,
                )
                advanced = True
            return cleaned

        with mock.patch.object(
            waves, "_cleanup_resource", side_effect=cleanup_then_advance
        ):
            promoted = pw.promote_wave(
                self.workspace, self.run_id, evidence, clock=lambda: FIXED
            )
        self.assertTrue(advanced)
        self.assertEqual(promoted["promoted_head"], integration_tip)

    def test_branch_advance_race_retains_worker_ref_and_blocks_promotion(self) -> None:
        _integrated, _integration, evidence = self._integrated_first_wave()
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        branch = str(assignment["branch"])
        ref = f"refs/heads/{branch}"
        expected_tip = git("rev-parse", ref, cwd=self.repo)
        original_journaled_git = waves._journaled_git
        advanced_tip: str | None = None

        def race_before_delete(
            journal: Path,
            repo: Path,
            arguments: list[str],
            description: str,
            clock: Callable[[], datetime],
            *,
            check: bool = True,
        ) -> subprocess.CompletedProcess[bytes]:
            nonlocal advanced_tip
            if arguments[:3] == ["update-ref", "-d", ref] and advanced_tip is None:
                tree = git("rev-parse", f"{expected_tip}^{{tree}}", cwd=self.repo)
                advanced_tip = git(
                    "commit-tree",
                    tree,
                    "-p",
                    expected_tip,
                    "-m",
                    "advance worker during cleanup",
                    cwd=self.repo,
                )
                git("update-ref", ref, advanced_tip, expected_tip, cwd=self.repo)
            return original_journaled_git(
                journal,
                repo,
                arguments,
                description,
                clock,
                check=check,
            )

        with mock.patch.object(waves, "_journaled_git", side_effect=race_before_delete):
            with self.assertRaises(PromptWorkspaceError) as raised:
                pw.promote_wave(
                    self.workspace, self.run_id, evidence, clock=lambda: FIXED
                )
        self.assertEqual(raised.exception.code, "CLEANUP_BLOCKED")
        self.assertIsNotNone(advanced_tip)
        self.assertEqual(git("rev-parse", ref, cwd=self.repo), advanced_tip)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)

    def test_scope_expansion_fails_replan_and_retains_recovery_resources(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration" / "assignments" / "wave-001" / "task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        worktree = Path(assignment["worktree"])
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                session_id="scope-expansion-worker",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)
        (scope_cwd / "one.txt").write_text("task-1\n", encoding="utf-8")
        (scope_cwd / "two.txt").write_text("undeclared\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "Expand task-1 scope", cwd=worktree)
        commit = git("rev-parse", "HEAD", cwd=worktree)
        result = {
            "schema": RESULT_SCHEMA,
            "run_id": self.run_id,
            "wave_id": "wave-001",
            "task_id": "task-1",
            "assignment_sha256": assignment["assignment_sha256"],
            "status": "committed",
            "commit": commit,
            "changed_paths": [
                "services/example/one.txt",
                "services/example/two.txt",
            ],
            "summary": "Expanded task scope",
            "decisions": [],
            "open_risks": ["scope exceeded"],
            "validation": "focused validation passed",
            "end_to_end_validation": "behavior observed",
            "code_review": "review completed",
            "completed_at": "2026-07-14T12:01:00+00:00",
        }
        result["result_sha256"] = sha256_json(result)
        result_path = Path(assignment["result_path"])
        result_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result_path.chmod(0o600)
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.accept_task_result(
                self.workspace, self.run_id, "task-1", clock=lambda: FIXED
            )
        self.assertEqual(raised.exception.code, "WORKER_SCOPE_VIOLATION")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)
        self.assertTrue(worktree.is_dir())
        task_two = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-2.json"
            ).read_text(encoding="utf-8")
        )
        previous = Path.cwd()
        os.chdir(Path(task_two["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as blocked_start:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-2",
                    task_two["assignment_sha256"],
                    session_id="late-batch-worker",
                    clock=lambda: FIXED,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(blocked_start.exception.code, "EXECUTION_STATE_INVALID")
        with self.assertRaises(PromptWorkspaceError) as replan_raised:
            pw.replan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.assertEqual(replan_raised.exception.code, "STEERING_QUEUED_AFTER_WAVE")

    def test_worker_heartbeat_and_watch_enforce_no_progress_budgets(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration" / "assignments" / "wave-001" / "task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        self.assertEqual(assignment["worker_profile"], "standard")
        self.assertEqual(
            assignment["read_only_warning_seconds"],
            WORKER_STANDARD_WARNING_SECONDS,
        )
        self.assertEqual(
            assignment["read_only_seconds"], WORKER_STANDARD_READ_ONLY_SECONDS
        )
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                session_id="heartbeat-worker",
                clock=lambda: FIXED,
            )
            heartbeat = pw.heartbeat_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                "preflight",
                session_id="heartbeat-worker",
                clock=lambda: FIXED + timedelta(seconds=30),
            )
            self.assertEqual(heartbeat["status"], "ACTIVE")
            self.assertEqual(heartbeat["heartbeat_sequence"], 2)
            for elapsed in range(60, 211, 30):
                pw.heartbeat_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    "preflight",
                    session_id="heartbeat-worker",
                    clock=lambda elapsed=elapsed: FIXED + timedelta(seconds=elapsed),
                )
            plane_path = (
                self.run_dir / "orchestration" / "tasks" / "wave-001" / "task-1.json"
            )
            before_replay = json.loads(plane_path.read_text(encoding="utf-8"))
            with self.assertRaises(PromptWorkspaceError) as replay:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    session_id="heartbeat-worker",
                    clock=lambda: FIXED + timedelta(seconds=45),
                )
            self.assertEqual(replay.exception.code, "EXECUTION_STATE_INVALID")
            self.assertEqual(
                json.loads(plane_path.read_text(encoding="utf-8")), before_replay
            )
        finally:
            os.chdir(previous)
        warning = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=240),
        )
        self.assertEqual(warning["status"], "ACTIVE")
        self.assertEqual(warning["warning"], "READ_ONLY_DEADLINE_NEAR")
        watched = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=300),
        )
        self.assertEqual(watched["status"], "WORKER_READ_ONLY_TIMEOUT")
        self.assertFalse(watched["progress_observed"])

    def test_worker_watch_bounds_dispatch_to_task_start(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        previous = Path.cwd()
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as not_armed:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    session_id="premature-worker",
                    clock=lambda: FIXED,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(not_armed.exception.code, "TASK_NOT_ARMED")
        queued = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=600),
        )
        self.assertEqual(queued["status"], "QUEUED")
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        pending = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=59),
        )
        self.assertEqual(pending["status"], "PENDING_START")
        timed_out = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=60),
        )
        self.assertEqual(timed_out["status"], "WORKER_PRESTART_TIMEOUT")
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as expired:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    session_id="expired-worker",
                    clock=lambda: FIXED + timedelta(seconds=60),
                )
        finally:
            os.chdir(previous)
        self.assertEqual(expired.exception.code, "WORKER_PRESTART_TIMEOUT")

    def test_worker_watch_detects_stale_heartbeat_after_file_progress(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                session_id="stale-worker",
                clock=lambda: FIXED,
            )
            (scope_cwd / "one.txt").write_text("progress\n", encoding="utf-8")
        finally:
            os.chdir(previous)
        watched = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=240),
        )
        self.assertEqual(watched["status"], "WORKER_STALLED")
        self.assertTrue(watched["progress_observed"])

    def test_out_of_claim_mutation_is_not_worker_progress(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                session_id="scope-violation-worker",
                clock=lambda: FIXED,
            )
            (scope_cwd / "two.txt").write_text("undeclared\n", encoding="utf-8")
            watched = pw.watch_task(
                self.workspace,
                self.run_id,
                "task-1",
                clock=lambda: FIXED + timedelta(seconds=30),
            )
            self.assertEqual(watched["status"], "WORKER_SCOPE_VIOLATION")
            self.assertFalse(watched["progress_observed"])
            self.assertTrue(watched["scope_violation"])
            with self.assertRaises(PromptWorkspaceError) as heartbeat:
                pw.heartbeat_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    "implementing",
                    session_id="scope-violation-worker",
                    clock=lambda: FIXED + timedelta(seconds=30),
                )
            self.assertEqual(heartbeat.exception.code, "WORKER_SCOPE_VIOLATION")
        finally:
            os.chdir(previous)

    def test_planned_steering_rebuilds_waves_without_head_drift(self) -> None:
        original = pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.assertEqual(original["active_wave"], "wave-001")
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "### task-2\n\n- Status: pending\n- Depends on: none",
            "### task-2\n\n- Status: pending\n- Depends on: task-1",
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        replanned = pw.replan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.assertTrue(str(replanned["active_wave"]).startswith("wave-r"))
        self.assertTrue(
            all(
                str(item["wave_id"]).startswith("wave-r") for item in replanned["waves"]
            )
        )
        self.assertEqual(
            json.loads(
                (self.run_dir / "orchestration" / "waves" / "wave-001.json").read_text(
                    encoding="utf-8"
                )
            )["status"],
            "blocked",
        )
        active = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(active["base_commit"], self.initial)

    def test_repository_claims_add_every_exclusive_class_sentinel(self) -> None:
        handoff = (self.run_dir / "handoff.md").read_text(encoding="utf-8")
        domains = "\n  - ".join(
            f"{domain_class}:separate-key"
            for domain_class in sorted(waves.EXCLUSIVE_CONFLICT_CLASSES)
        )
        tasks = waves.parse_task_plans(
            handoff.replace("files:one.txt", domains, 1)
        )
        workspace = json.loads(self.workspace.read_text(encoding="utf-8"))
        claims = waves._repository_claims(workspace, tasks)
        actual_domains = {
            claim["path"] for claim in claims if claim["kind"] == "domain"
        }
        expected_sentinels = {
            f"{waves.EXCLUSIVE_DOMAIN_CLAIM_PREFIX}{domain_class}"
            for domain_class in waves.EXCLUSIVE_CONFLICT_CLASSES
        }
        self.assertTrue(expected_sentinels <= actual_domains)

    def test_cleaned_final_wave_can_append_correction_tail(self) -> None:
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "### task-3\n\n- Status: pending",
            "### task-3\n\n- Status: done",
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)

        _, _, evidence = self._integrated_first_wave()
        promoted = pw.promote_wave(
            self.workspace, self.run_id, evidence, clock=lambda: FIXED
        )
        cleaned = pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(cleaned["status"], "done")

        text = handoff.read_text(encoding="utf-8")
        handoff.write_text(
            text
            + """

### task-4

- Status: pending
- Depends on: task-1, task-2
- Write claims: exact: services/example/three.txt
- Conflict domains: files:three.txt
- Implementation steps: correct only services/example/three.txt
- Validation: inspect three.txt
- End-to-end validation: verify the corrected integrated behavior
- Done criteria: three.txt contains the correction
""",
            encoding="utf-8",
        )
        handoff.chmod(0o600)

        replanned = pw.replan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.assertEqual(replanned["status"], "running")
        self.assertTrue(str(replanned["active_wave"]).startswith("wave-r"))
        self.assertEqual(len(replanned["waves"]), 2)
        self.assertEqual(replanned["waves"][0]["wave_id"], "wave-001")
        self.assertEqual(
            replanned["waves"][1]["tasks"][0]["task_id"],
            "task-4",
        )
        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(prepared["base_commit"], promoted["promoted_head"])

    def test_interrupted_worker_can_transfer_declared_dirty_state(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration" / "assignments" / "wave-001" / "task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                session_id="interrupted-worker",
                clock=lambda: FIXED,
            )
            pw.dispatch_wave(
                self.workspace, self.run_id, self.initial, clock=lambda: FIXED
            )
            plane = json.loads(
                (
                    self.run_dir
                    / "orchestration"
                    / "tasks"
                    / "wave-001"
                    / "task-1.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(plane["state"], "running")
            self.assertIsNotNone(plane["worker_session_sha256"])
            (scope_cwd / "one.txt").write_text("recoverable\n", encoding="utf-8")
            with self.assertRaises(PromptWorkspaceError) as unconfirmed:
                pw.recover_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    confirmed_stopped=False,
                    session_id="replacement-worker",
                    clock=lambda: FIXED,
                )
            self.assertEqual(
                unconfirmed.exception.code, "RECOVERY_CONFIRMATION_REQUIRED"
            )
            recovered = pw.recover_task(
                self.workspace,
                self.run_id,
                "task-1",
                confirmed_stopped=True,
                session_id="replacement-worker",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)
        self.assertEqual(recovered["observed_head"], self.initial)
        self.assertEqual(recovered["changed_paths"], ["services/example/one.txt"])
        plane = json.loads(
            (
                self.run_dir / "orchestration" / "tasks" / "wave-001" / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(len(plane["worker_session_sha256_history"]), 2)
        task_two = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-2.json"
            ).read_text(encoding="utf-8")
        )
        pw.arm_task(self.workspace, self.run_id, "task-2", clock=lambda: FIXED)
        os.chdir(Path(task_two["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as reused:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-2",
                    task_two["assignment_sha256"],
                    session_id="interrupted-worker",
                    clock=lambda: FIXED,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(reused.exception.code, "FRESH_SESSION_REQUIRED")

    def test_tampered_incoming_handoff_blocks_worker_start(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        handoff_path = Path(assignment["incoming_handoff_path"])
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        handoff["dependencies"] = ["task-999"]
        handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
        handoff_path.chmod(0o600)
        previous = Path.cwd()
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as raised:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    session_id="fresh-worker",
                    clock=lambda: FIXED,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")

    def test_accepted_branch_drift_blocks_integration_without_promotion(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        self._complete_worker("task-1", "one.txt")
        self._complete_worker("task-2", "two.txt")
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        worktree = Path(assignment["worktree"])
        (worktree / "services" / "example" / "one.txt").write_text(
            "drifted\n", encoding="utf-8"
        )
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "unexpected drift", cwd=worktree)
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.integrate_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(raised.exception.code, "WORKTREE_CONFLICT")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)

    def test_submodule_claim_is_rejected_without_initialization(self) -> None:
        git(
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.initial},services/example/vendor",
            cwd=self.primary,
        )
        git("commit", "-qm", "add gitlink fixture", cwd=self.primary)
        base = git("rev-parse", "HEAD", cwd=self.primary)
        lanes.ensure_project_lane(self.primary / "services" / "example")
        (self.repo / "services" / "example" / "vendor").mkdir(exist_ok=True)
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "exact: services/example/one.txt",
            "exact: services/example/vendor/file.txt",
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.dispatch_wave(self.workspace, self.run_id, base, clock=lambda: FIXED)
        self.assertEqual(raised.exception.code, "UNSUPPORTED_SUBMODULE_SCOPE")
        self.assertFalse(
            (self.repo / "services" / "example" / "vendor" / ".git").exists()
        )
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), base)

    def test_tracked_symlink_claim_is_rejected(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        link = self.primary / "services" / "example" / "linked"
        link.symlink_to(outside, target_is_directory=True)
        git("add", "services/example/linked", cwd=self.primary)
        git("commit", "-qm", "add symlink fixture", cwd=self.primary)
        base = git("rev-parse", "HEAD", cwd=self.primary)
        lanes.ensure_project_lane(self.primary / "services" / "example")
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "exact: services/example/one.txt",
            "exact: services/example/linked/file.txt",
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.dispatch_wave(self.workspace, self.run_id, base, clock=lambda: FIXED)
        self.assertEqual(raised.exception.code, "UNSUPPORTED_SYMLINK_SCOPE")
        self.assertFalse((outside / "file.txt").exists())

    def test_internal_ids_cannot_traverse_private_state(self) -> None:
        with self.assertRaises(PromptWorkspaceError) as run_error:
            pw.plan_waves(self.workspace, "../foreign", 2, clock=lambda: FIXED)
        self.assertEqual(run_error.exception.code, "RUN_STATE_INVALID")

    def test_malformed_interop_state_blocks_resource_creation(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        interop_path = self.run_dir / "orchestration" / "interop.json"
        interop = json.loads(interop_path.read_text(encoding="utf-8"))
        interop["generation"] = 0
        interop_path.write_text(
            json.dumps(interop, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        interop_path.chmod(0o600)
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")
        worktrees = git("worktree", "list", "--porcelain", cwd=self.repo)
        self.assertEqual(worktrees.count("worktree "), 2)

    def test_legacy_wave_and_interop_schemas_are_rejected(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        wave_path = self.run_dir / "orchestration" / "waves" / "wave-001.json"
        wave = json.loads(wave_path.read_text(encoding="utf-8"))
        wave["schema"] = "task-implementer/wave-v3"
        wave_path.write_text(
            json.dumps(wave, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        with self.assertRaises(PromptWorkspaceError) as wave_error:
            pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(wave_error.exception.code, "EXECUTION_STATE_INVALID")

        wave["schema"] = "task-implementer/wave-v4"
        wave_path.write_text(
            json.dumps(wave, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        interop_path = self.run_dir / "orchestration" / "interop.json"
        interop = json.loads(interop_path.read_text(encoding="utf-8"))
        interop["schema"] = 1
        interop_path.write_text(
            json.dumps(interop, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        with self.assertRaises(PromptWorkspaceError) as interop_error:
            pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(interop_error.exception.code, "WORKFLOW_UPGRADE_REQUIRED")

    def test_broken_symlink_resource_is_not_recorded_absent(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        wave = json.loads(
            (self.run_dir / "orchestration" / "waves" / "wave-001.json").read_text(
                encoding="utf-8"
            )
        )
        broken = self.root / "broken-worker"
        broken.symlink_to(self.root / "missing-worker-target", target_is_directory=True)
        workspace = pw.verify_workspace(self.workspace)

        cleaned = waves._cleanup_resource(
            workspace=workspace,
            run_dir=self.run_dir,
            wave=wave,
            repo=self.repo,
            kind="worker",
            worktree=broken,
            branch="codex/task/missing/worker",
            expected_tip=self.initial,
            reachable_tip=self.initial,
            clock=lambda: FIXED,
        )
        self.assertFalse(cleaned)
        self.assertTrue(os.path.lexists(broken))

    def test_journal_completes_partial_writes_as_one_json_line(self) -> None:
        journal = self.run_dir / "orchestration" / "journals" / "partial.jsonl"
        real_write = os.write

        def partial_write(descriptor: int, data: bytes | memoryview) -> int:
            payload = bytes(data)
            return real_write(descriptor, payload[: max(1, len(payload) // 2)])

        with mock.patch.object(waves.os, "write", side_effect=partial_write):
            waves._append_journal(journal, {"phase": "intent", "operation": "test"})
        lines = journal.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            [json.loads(line) for line in lines],
            [{"operation": "test", "phase": "intent"}],
        )

    def test_worktree_path_collision_and_moved_primary_fail_closed(self) -> None:
        plan = pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        wave_path = (
            self.run_dir / "orchestration" / "waves" / f"{plan['active_wave']}.json"
        )
        wave = json.loads(wave_path.read_text(encoding="utf-8"))
        collision = Path(wave["integration_worktree"])
        collision.mkdir(parents=True)
        with self.assertRaises(PromptWorkspaceError) as path_error:
            pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(path_error.exception.code, "WORKTREE_COLLISION")
        collision.rmdir()
        (self.scope / "moved.txt").write_text("moved\n", encoding="utf-8")
        git("add", "services/example/moved.txt", cwd=self.repo)
        git("commit", "-qm", "move primary head", cwd=self.repo)
        with self.assertRaises(PromptWorkspaceError) as head_error:
            pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(head_error.exception.code, "WORKTREE_CONFLICT")

    def test_invalid_integration_evidence_and_queued_steering_block_promotion(
        self,
    ) -> None:
        _, _, evidence = self._integrated_first_wave()
        invalid = json.loads(evidence.read_text(encoding="utf-8"))
        invalid["validation"] = ""
        evidence.write_text(json.dumps(invalid), encoding="utf-8")
        evidence.chmod(0o600)
        with self.assertRaises(PromptWorkspaceError) as invalid_error:
            pw.promote_wave(self.workspace, self.run_id, evidence, clock=lambda: FIXED)
        self.assertEqual(invalid_error.exception.code, "INTEGRATION_VALIDATION_FAILED")
        self.prompt.write_text(
            self.prompt.read_text(encoding="utf-8").replace(
                "All three files are updated.",
                "All three files are updated with queued steering.",
            ),
            encoding="utf-8",
        )
        self.prompt.chmod(0o600)
        routed = pw.route_project_prompt(
            self.scope, self.codex_home, self.prompt.name, clock=lambda: FIXED
        )
        self.assertEqual(routed["action"], "steering_queued_after_wave")
        with self.assertRaises(PromptWorkspaceError) as steering_error:
            pw.promote_wave(self.workspace, self.run_id, evidence, clock=lambda: FIXED)
        self.assertEqual(steering_error.exception.code, "STEERING_QUEUED_AFTER_WAVE")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)

    def test_post_merge_hook_dirty_state_is_classified_and_resumable(self) -> None:
        integrated, integration, evidence = self._integrated_first_wave()
        hook_path = Path(
            git("rev-parse", "--git-path", "hooks/post-merge", cwd=self.repo)
        )
        if not hook_path.is_absolute():
            hook_path = self.repo / hook_path
        hook_path.write_text("#!/bin/sh\n: > hook-dirty.txt\n", encoding="utf-8")
        hook_path.chmod(0o755)
        with self.assertRaises(PromptWorkspaceError) as hook_error:
            pw.promote_wave(self.workspace, self.run_id, evidence, clock=lambda: FIXED)
        self.assertEqual(hook_error.exception.code, "PROMOTION_FAILED")
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=self.repo),
            git("rev-parse", "HEAD", cwd=integration),
        )
        (self.repo / "hook-dirty.txt").unlink()
        promoted = pw.promote_wave(
            self.workspace, self.run_id, evidence, clock=lambda: FIXED
        )
        self.assertEqual(promoted["promoted_head"], integrated["integrated_head"])


if __name__ == "__main__":
    unittest.main()
