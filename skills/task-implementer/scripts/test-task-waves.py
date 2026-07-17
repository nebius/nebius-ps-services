#!/usr/bin/env python3
"""Disposable real-Git tests for dependency-wave worktree lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import prompt_workspace as pw
import prompt_workspace_waves as waves
from prompt_workspace_core import PromptWorkspaceError
from prompt_workspace_execution import RESULT_SCHEMA, sha256_json


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
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git("init", "-q", cwd=self.repo)
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
        self.codex_home = self.root / "codex home"
        initialized = pw.init_workspace(
            self.repo, "services/example", self.codex_home, clock=lambda: FIXED
        )
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
- Validation: inspect {name}
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

    def _complete_worker(self, task_id: str, filename: str) -> dict[str, object]:
        assignment_path = (
            self.run_dir
            / "orchestration"
            / "assignments"
            / "wave-001"
            / f"{task_id}.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        worktree = Path(assignment["worktree"])
        scope_cwd = Path(assignment["scope_cwd"])
        self.assertEqual(scope_cwd, worktree / "services" / "example")
        self.assertFalse((worktree / "ignored.env").exists())
        previous = Path.cwd()
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
        self.assertEqual(len(dispatched["assignments"]), 2)
        self._complete_worker("task-2", "two.txt")
        task_one_result = self._complete_worker("task-1", "one.txt")
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
        retained = pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(retained["status"], "cleanup")
        self.assertTrue(retained["cleanup_retained"])
        cleanup_dirty.unlink()
        cleaned = pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(cleaned["status"], "done")
        worktrees = git("worktree", "list", "--porcelain", cwd=self.repo)
        self.assertEqual(worktrees.count("worktree "), 1)
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
        journal = self.run_dir / "orchestration" / "journals" / "wave-001.jsonl"
        for line in journal.read_text(encoding="utf-8").splitlines():
            self.assertIsInstance(json.loads(line), dict)

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
        self.assertEqual(raised.exception.code, "REPLAN_REQUIRED")
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
        wave_path = self.run_dir / "orchestration" / "waves" / "wave-001.json"
        wave = json.loads(wave_path.read_text(encoding="utf-8"))
        wave["task_states"]["task-2"] = "running"
        wave_path.write_text(
            json.dumps(wave, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        wave_path.chmod(0o600)
        os.chdir(Path(task_two["scope_cwd"]))
        try:
            replayed = pw.start_task(
                self.workspace,
                self.run_id,
                "task-2",
                task_two["assignment_sha256"],
                session_id="authorized-before-block",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)
        self.assertIsNotNone(replayed["worker_session_sha256"])
        with self.assertRaises(PromptWorkspaceError) as replan_raised:
            pw.replan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.assertEqual(replan_raised.exception.code, "STEERING_QUEUED_AFTER_WAVE")

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
        active = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(active["base_commit"], self.initial)

    def test_interrupted_worker_can_transfer_declared_dirty_state(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration" / "assignments" / "wave-001" / "task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        scope_cwd = Path(assignment["scope_cwd"])
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
            cwd=self.repo,
        )
        git("commit", "-qm", "add gitlink fixture", cwd=self.repo)
        base = git("rev-parse", "HEAD", cwd=self.repo)
        (self.repo / "services" / "example" / "vendor").mkdir()
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
        link = self.scope / "linked"
        link.symlink_to(outside, target_is_directory=True)
        git("add", "services/example/linked", cwd=self.repo)
        git("commit", "-qm", "add symlink fixture", cwd=self.repo)
        base = git("rev-parse", "HEAD", cwd=self.repo)
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
        interop["released"] = False
        interop_path.write_text(
            json.dumps(interop, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        interop_path.chmod(0o600)
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")
        worktrees = git("worktree", "list", "--porcelain", cwd=self.repo)
        self.assertEqual(worktrees.count("worktree "), 1)

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
