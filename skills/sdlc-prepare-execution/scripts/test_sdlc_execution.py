#!/usr/bin/env python3
"""Deterministic and real-Git tests for the Agentic SDLC execution plane."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from sdlc_execution_core import (  # noqa: E402
    ExecutionError,
    _claim_worker_session,
    advance_batch,
    assignment_path,
    append_journal,
    build_dependency_waves,
    complete_wave,
    coordinator_path,
    finish_task,
    integrate_wave,
    journal_path,
    parse_locked_plan,
    prepare_execution,
    prepare_wave,
    promote_feature,
    recover_task,
    replan_future,
    seal_feature,
    seal_tdd_base,
    start_task,
    task_path,
    wave_path,
)
from sdlc_execution_interop import ExecutionInteropError, release  # noqa: E402


PLAN = """# FEAT-001 Plan v1

## Task Graph

### TASK-001

- Requirements: REQ-001
- Goal: implement the first independent behavior
- Depends on: none
- Write claims: exact: src/a.py
- Conflict domains: code:a
- Validation: python3 -m unittest tests.test_feature
- Done criteria: first behavior passes
- Rollback or stop conditions: stop on contract drift

### TASK-002

- Requirements: REQ-001
- Goal: implement the second independent behavior
- Depends on: none
- Write claims: exact: src/b.py
- Conflict domains: code:b
- Validation: python3 -m unittest tests.test_feature
- Done criteria: second behavior passes
- Rollback or stop conditions: stop on contract drift

### TASK-003

- Requirements: REQ-001
- Goal: implement the dependent behavior
- Depends on: TASK-001, TASK-002
- Write claims: exact: src/c.py
- Conflict domains: code:c
- Validation: python3 -m unittest tests.test_feature
- Done criteria: dependent behavior passes
- Rollback or stop conditions: stop on contract drift
"""

MANAGED_PLAN = """# FEAT-001 Plan v1

## Task Graph

### TASK-001

- Requirements: REQ-001
- Goal: implement one selected-scope behavior
- Depends on: none
- Write claims: exact: services/example/src/value.py
- Conflict domains: code:value
- Validation: focused test passes
- Done criteria: selected-scope behavior is committed
- Rollback or stop conditions: stop on contract drift
"""


def git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


class SchedulerTests(unittest.TestCase):
    def test_independent_tasks_share_wave_and_dependencies_follow(self) -> None:
        tasks = parse_locked_plan(PLAN)
        waves = build_dependency_waves(tasks)
        self.assertEqual(
            [[task.task_id for task in wave] for wave in waves],
            [["TASK-001", "TASK-002"], ["TASK-003"]],
        )

    def test_overlapping_write_claims_serialize(self) -> None:
        text = PLAN.replace("exact: src/b.py", "prefix: src")
        waves = build_dependency_waves(parse_locked_plan(text))
        self.assertEqual(
            [[task.task_id for task in wave] for wave in waves],
            [["TASK-001"], ["TASK-002"], ["TASK-003"]],
        )

    def test_unknown_ownership_serializes(self) -> None:
        text = PLAN.replace("exact: src/b.py", "unknown")
        waves = build_dependency_waves(parse_locked_plan(text))
        self.assertEqual([task.task_id for task in waves[0]], ["TASK-001"])
        self.assertEqual([task.task_id for task in waves[1]], ["TASK-002"])

    def test_dependency_cycle_fails_closed(self) -> None:
        text = PLAN.replace(
            "### TASK-001\n\n- Requirements: REQ-001\n- Goal: implement the first independent behavior\n- Depends on: none",
            "### TASK-001\n\n- Requirements: REQ-001\n- Goal: implement the first independent behavior\n- Depends on: TASK-003",
        )
        with self.assertRaisesRegex(ExecutionError, "cycle") as raised:
            parse_locked_plan(text)
        self.assertEqual(raised.exception.code, "DEPENDENCY_CYCLE")

    def test_task_ids_must_be_contiguous(self) -> None:
        with self.assertRaises(ExecutionError) as raised:
            parse_locked_plan(PLAN.replace("TASK-002", "TASK-004"))
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_requirement_ids_must_be_canonical(self) -> None:
        with self.assertRaises(ExecutionError) as raised:
            parse_locked_plan(
                PLAN.replace("Requirements: REQ-001", "Requirements: story-1", 1)
            )
        self.assertEqual(raised.exception.code, "PLAN_INVALID")


class GitLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.run_dir = self.root / "private" / "run-1"
        self.plan = self.run_dir / "plans" / "FEAT-001.plan.v1.md"
        self.project.mkdir(parents=True)
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text(PLAN, encoding="utf-8")
        self.plan.with_suffix(self.plan.suffix + ".lock").write_text(
            "locked\n", encoding="utf-8"
        )
        try:
            git(self.project, "init", "-b", "main")
        except AssertionError:
            git(self.project, "init")
            git(self.project, "branch", "-m", "main")
        git(self.project, "config", "user.name", "SDLC Test")
        git(self.project, "config", "user.email", "sdlc-test@example.com")
        (self.project / "README.md").write_text("fixture\n", encoding="utf-8")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-m", "initial")
        git(self.project, "switch", "-c", "feature/test")
        self.base_head = git(self.project, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def prepare(self) -> dict:
        return prepare_execution(
            self.run_dir, self.project, "FEAT-001", self.plan, capacity=2
        )

    def start_assignment(
        self, assignment: dict, wave_id: str, session: str | None = None
    ) -> dict:
        return start_task(
            self.run_dir,
            "FEAT-001",
            wave_id,
            assignment["task_id"],
            assignment["assignment_digest"],
            session or f"test-session-{wave_id}-{assignment['task_id']}",
            Path(assignment["scope_cwd"]),
        )

    def test_parallel_session_claim_is_atomic(self) -> None:
        def claim(task_id: str) -> str:
            try:
                _claim_worker_session(
                    self.run_dir,
                    "FEAT-001",
                    "WAVE-001",
                    task_id,
                    "a" * 64,
                )
            except ExecutionError as exc:
                return exc.code
            return "claimed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(claim, ("TASK-001", "TASK-002")))
        self.assertEqual(outcomes, ["FRESH_SESSION_REQUIRED", "claimed"])

    def test_parallel_task_start_has_one_owner(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]

        def start(session: str) -> str:
            try:
                self.start_assignment(assignment, "WAVE-001", session)
            except ExecutionError as exc:
                return exc.code
            return "started"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(start, ("worker-a", "worker-b")))
        self.assertEqual(outcomes, ["WORKSPACE_BUSY", "started"])

    def test_parallel_recovery_transfers_once(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001", "old-worker")

        def recover(session: str) -> str:
            try:
                recover_task(
                    self.run_dir,
                    "FEAT-001",
                    "WAVE-001",
                    assignment["task_id"],
                    session,
                    Path(assignment["scope_cwd"]),
                    expected_attempt=1,
                    confirmed_stopped=True,
                )
            except ExecutionError as exc:
                return exc.code
            return "recovered"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(recover, ("worker-a", "worker-b")))
        self.assertEqual(outcomes, ["WORKSPACE_BUSY", "recovered"])

    def test_nested_project_scope_is_persisted_and_enforced(self) -> None:
        selected = self.project / "services" / "a"
        sibling = self.project / "services" / "b"
        selected.mkdir(parents=True)
        sibling.mkdir(parents=True)
        (selected / ".keep").write_text("a\n", encoding="utf-8")
        (sibling / ".keep").write_text("b\n", encoding="utf-8")
        git(self.project, "add", "services")
        git(self.project, "commit", "-m", "add monorepo scopes")
        self.base_head = git(self.project, "rev-parse", "HEAD")
        scoped_plan = PLAN.replace("exact: src/", "exact: services/a/src/")
        self.plan.write_text(scoped_plan, encoding="utf-8")
        coordinator = prepare_execution(
            self.run_dir, selected, "FEAT-001", self.plan, capacity=2
        )
        self.assertEqual(coordinator["git_root"], str(self.project.resolve()))
        self.assertEqual(coordinator["project_scope"], "services/a")
        integration = Path(coordinator["integration_worktree"])
        (integration / "services" / "a" / "tests").mkdir()
        (integration / "services" / "a" / "tests" / "test_feature.py").write_text(
            "# red contract\n", encoding="utf-8"
        )
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): scoped TDD")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.assertEqual(
            assignment["scope_cwd"],
            str(Path(assignment["worktree"]) / "services" / "a"),
        )
        self.start_assignment(assignment, "WAVE-001")
        outside = Path(assignment["worktree"]) / "services" / "b" / "outside.py"
        outside.write_text("OUTSIDE = True\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as caught:
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "focused test passed",
                "review passed",
                "feat(FEAT-001): escaped change",
                summary="escaped-change handoff",
            )
        self.assertEqual(caught.exception.code, "REPLAN_REQUIRED")

    def test_tdd_seal_rejects_changes_outside_nested_project_scope(self) -> None:
        selected = self.project / "services" / "a"
        sibling = self.project / "services" / "b"
        selected.mkdir(parents=True)
        sibling.mkdir(parents=True)
        (selected / ".keep").write_text("a\n", encoding="utf-8")
        (sibling / ".keep").write_text("b\n", encoding="utf-8")
        git(self.project, "add", "services")
        git(self.project, "commit", "-m", "add monorepo scopes")
        scoped_plan = PLAN.replace("exact: src/", "exact: services/a/src/")
        self.plan.write_text(scoped_plan, encoding="utf-8")
        coordinator = prepare_execution(
            self.run_dir, selected, "FEAT-001", self.plan, capacity=2
        )
        integration = Path(coordinator["integration_worktree"])
        outside = integration / "services" / "b" / "test_outside.py"
        outside.write_text("# outside scope\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as caught:
            seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): scoped TDD")
        self.assertEqual(caught.exception.code, "REPLAN_REQUIRED")
        self.assertEqual(git(integration, "diff", "--cached", "--name-only"), "")

    def test_claim_outside_nested_project_scope_fails_before_resources(self) -> None:
        selected = self.project / "services" / "a"
        selected.mkdir(parents=True)
        (selected / ".keep").write_text("a\n", encoding="utf-8")
        git(self.project, "add", "services")
        git(self.project, "commit", "-m", "add selected scope")
        with self.assertRaises(ExecutionError) as caught:
            prepare_execution(self.run_dir, selected, "FEAT-001", self.plan, capacity=2)
        self.assertEqual(caught.exception.code, "REPLAN_REQUIRED")
        self.assertFalse(coordinator_path(self.run_dir, "FEAT-001").exists())

    def test_interrupted_worker_recovery_transfers_claimed_dirty_state(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "tests").mkdir()
        (integration / "tests" / "test_feature.py").write_text(
            "# red contract\n", encoding="utf-8"
        )
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): recovery TDD")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001", "old-worker-session")
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir(exist_ok=True)
        (worker / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as unconfirmed:
            recover_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "new-worker-session",
                Path(assignment["scope_cwd"]),
                expected_attempt=1,
                confirmed_stopped=False,
            )
        self.assertEqual(unconfirmed.exception.code, "WORKSPACE_BUSY")
        recover_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            "new-worker-session",
            Path(assignment["scope_cwd"]),
            expected_attempt=1,
            confirmed_stopped=True,
        )
        state = json.loads(
            task_path(
                self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"]
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(state["attempt"], 2)
        self.assertEqual(len(state["worker_session_hash_history"]), 2)
        self.assertNotIn("old-worker-session", json.dumps(state))
        self.assertNotIn("new-worker-session", json.dumps(state))
        result = finish_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            "focused test passed",
            "review passed",
            "feat(FEAT-001): recovered task",
            summary="recovered-task handoff",
        )
        self.assertEqual(result["attempt"], 2)

    def test_interrupted_worker_recovery_accepts_clean_base(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "tests").mkdir()
        (integration / "tests" / "test_feature.py").write_text(
            "# red contract\n", encoding="utf-8"
        )
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): recovery TDD")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001", "old-clean-session")
        recovered = recover_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            "fresh-clean-session",
            Path(assignment["scope_cwd"]),
            expected_attempt=1,
            confirmed_stopped=True,
        )
        self.assertEqual(
            recovered["assignment_digest"], assignment["assignment_digest"]
        )

    def test_interrupted_worker_recovery_accepts_one_clean_direct_child(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "tests").mkdir()
        (integration / "tests" / "test_feature.py").write_text(
            "# red contract\n", encoding="utf-8"
        )
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): recovery TDD")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001", "old-commit-session")
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir()
        (worker / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(worker, "add", "src/a.py")
        git(worker, "commit", "-m", "feat: interrupted worker commit")
        recovered = recover_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            "fresh-commit-session",
            Path(assignment["scope_cwd"]),
            expected_attempt=1,
            confirmed_stopped=True,
        )
        self.assertEqual(
            recovered["assignment_digest"], assignment["assignment_digest"]
        )

    def test_one_worker_session_cannot_own_two_tasks(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        first, second = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.start_assignment(first, "WAVE-001", "single-worker-session")
        with self.assertRaises(ExecutionError) as caught:
            self.start_assignment(second, "WAVE-001", "single-worker-session")
        self.assertEqual(caught.exception.code, "FRESH_SESSION_REQUIRED")

    def test_malformed_session_history_fails_closed(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        first, second = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        second_path = task_path(
            self.run_dir, "FEAT-001", "WAVE-001", second["task_id"]
        )
        state = json.loads(second_path.read_text(encoding="utf-8"))
        state["worker_session_hash_history"] = ["malformed"]
        second_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(ExecutionError) as caught:
            self.start_assignment(first, "WAVE-001", "fresh-session")
        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")

    def test_committed_worker_session_cannot_start_another_task(self) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        first, second = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.start_assignment(first, "WAVE-001", "burned-worker-session")
        worker = Path(first["worktree"])
        (worker / "src").mkdir(exist_ok=True)
        (worker / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        finish_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            "TASK-001",
            "focused test passed",
            "review passed",
            "feat(FEAT-001): first task",
            summary="first-task handoff",
        )
        with self.assertRaises(ExecutionError) as caught:
            self.start_assignment(second, "WAVE-001", "burned-worker-session")
        self.assertEqual(caught.exception.code, "FRESH_SESSION_REQUIRED")
        self.assertEqual(Path(coordinator["integration_worktree"]).name, "integration")

    def test_capacity_batches_create_only_active_assignments(self) -> None:
        prepare_execution(
            self.run_dir, self.project, "FEAT-001", self.plan, capacity=1
        )
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        first_batch = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual([item["task_id"] for item in first_batch], ["TASK-001"])
        self.assertFalse(
            assignment_path(
                self.run_dir, "FEAT-001", "WAVE-001", "TASK-002"
            ).exists()
        )
        with self.assertRaises(ExecutionError) as incomplete:
            advance_batch(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(incomplete.exception.code, "EXECUTION_STATE_INVALID")
        self.start_assignment(first_batch[0], "WAVE-001")
        worker = Path(first_batch[0]["worktree"])
        (worker / "src").mkdir(exist_ok=True)
        (worker / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        finish_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            "TASK-001",
            "focused test passed",
            "review passed",
            "feat(FEAT-001): first batch",
            summary="first-batch handoff",
        )
        second_batch = advance_batch(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual([item["task_id"] for item in second_batch], ["TASK-002"])
        later_handoff = json.loads(
            Path(second_batch[0]["incoming_handoff_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            [item["summary"] for item in later_handoff["predecessors"]],
            ["first-batch handoff"],
        )

    def test_finish_rejects_sensitive_staged_content_without_leaking(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "tests").mkdir()
        (integration / "tests" / "test_feature.py").write_text(
            "# red contract\n", encoding="utf-8"
        )
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): security TDD")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001")
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir(exist_ok=True)
        sensitive = "sk-" + "x" * 24
        (worker / "src" / "a.py").write_text(
            "OPENAI_API_KEY = " + sensitive + "\n", encoding="utf-8"
        )
        with self.assertRaises(ExecutionError) as caught:
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "focused test passed",
                "review passed",
                "feat(FEAT-001): unsafe task",
                summary="unsafe-task handoff",
            )
        self.assertEqual(caught.exception.code, "SECURITY_BLOCKER")
        self.assertNotIn(sensitive, str(caught.exception))
        self.assertEqual(git(worker, "diff", "--cached", "--name-only"), "")
        self.assertEqual(git(worker, "rev-parse", "HEAD"), assignment["base_head"])

    def test_future_replan_replaces_only_resource_free_planned_waves(self) -> None:
        original = self.prepare()
        addition = """

### TASK-004

- Requirements: REQ-001
- Goal: implement the final follow-up behavior
- Depends on: TASK-003
- Write claims: exact: src/d.py
- Conflict domains: code:d
- Validation: python3 -m unittest tests.test_feature
- Done criteria: final follow-up passes
- Rollback or stop conditions: stop on contract drift
"""
        replacement = self.run_dir / "plans" / "FEAT-001.plan.v2.md"
        replacement.write_text(
            PLAN.replace("# FEAT-001 Plan v1", "# FEAT-001 Plan v2").replace(
                "implement the second independent behavior",
                "implement the revised second independent behavior",
            )
            + addition,
            encoding="utf-8",
        )
        replacement.with_suffix(replacement.suffix + ".lock").write_text(
            "locked\n", encoding="utf-8"
        )
        replanned = replan_future(self.run_dir, "FEAT-001", replacement, capacity=2)
        self.assertNotEqual(replanned["plan_digest"], original["plan_digest"])
        self.assertEqual(replanned["wave_ids"], ["WAVE-001", "WAVE-002", "WAVE-003"])
        revised = json.loads(
            task_path(self.run_dir, "FEAT-001", "WAVE-001", "TASK-002").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            revised["task"]["goal"],
            "implement the revised second independent behavior",
        )
        repeated = replan_future(self.run_dir, "FEAT-001", replacement, capacity=2)
        self.assertEqual(repeated["plan_digest"], replanned["plan_digest"])
        future_assignment = assignment_path(
            self.run_dir, "FEAT-001", "WAVE-003", "TASK-004"
        )
        future_assignment.parent.mkdir(parents=True)
        future_assignment.write_text("{}\n", encoding="utf-8")
        third = self.run_dir / "plans" / "FEAT-001.plan.v3.md"
        third.write_text(
            replacement.read_text(encoding="utf-8").replace(
                "# FEAT-001 Plan v2", "# FEAT-001 Plan v3"
            ),
            encoding="utf-8",
        )
        third.with_suffix(third.suffix + ".lock").write_text(
            "locked\n", encoding="utf-8"
        )
        with self.assertRaises(ExecutionError) as blocked:
            replan_future(self.run_dir, "FEAT-001", third, capacity=2)
        self.assertEqual(blocked.exception.code, "REPLAN_REQUIRED")

    def test_three_task_golden_path_and_exact_promotion(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base_head)
        (integration / "tests").mkdir()
        (integration / "tests" / "test_feature.py").write_text(
            "# red-first contract\n", encoding="utf-8"
        )
        sealed_tdd = seal_tdd_base(
            self.run_dir, "FEAT-001", "test(FEAT-001): define behavior"
        )
        self.assertNotEqual(sealed_tdd["integration_head"], self.base_head)

        first_wave = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(
            [item["task_id"] for item in first_wave], ["TASK-001", "TASK-002"]
        )
        first_handoff = json.loads(
            Path(first_wave[0]["incoming_handoff_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(first_handoff["dependencies"], [])
        self.assertEqual(first_handoff["predecessors"], [])
        for assignment in reversed(first_wave):
            self.start_assignment(assignment, "WAVE-001")
            worker = Path(assignment["worktree"])
            target = (
                worker
                / "src"
                / ("a.py" if assignment["task_id"] == "TASK-001" else "b.py")
            )
            target.parent.mkdir(exist_ok=True)
            target.write_text(f"VALUE = '{assignment['task_id']}'\n", encoding="utf-8")
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "focused test passed",
                "code review passed",
                f"feat(FEAT-001): {assignment['task_id']}",
                summary=f"completed {assignment['task_id']}",
            )
        integrated = integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(integrated["merged_task_ids"], ["TASK-001", "TASK-002"])
        complete_wave(self.run_dir, "FEAT-001", "WAVE-001", "combined tests passed")

        second_wave = prepare_wave(self.run_dir, "FEAT-001", "WAVE-002")
        assignment = second_wave[0]
        dependent_handoff = json.loads(
            Path(assignment["incoming_handoff_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            [item["task_id"] for item in dependent_handoff["predecessors"]],
            ["TASK-001", "TASK-002"],
        )
        self.assertEqual(
            [item["summary"] for item in dependent_handoff["predecessors"]],
            ["completed TASK-001", "completed TASK-002"],
        )
        self.start_assignment(assignment, "WAVE-002")
        worker = Path(assignment["worktree"])
        (worker / "src" / "c.py").write_text("VALUE = 'TASK-003'\n", encoding="utf-8")
        finish_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-002",
            "TASK-003",
            "focused test passed",
            "code review passed",
            "feat(FEAT-001): TASK-003",
            summary="completed TASK-003",
        )
        integrate_wave(self.run_dir, "FEAT-001", "WAVE-002")
        complete_wave(self.run_dir, "FEAT-001", "WAVE-002", "combined tests passed")

        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base_head)
        (integration / "docs").mkdir()
        (integration / "docs" / "release.md").write_text("done\n", encoding="utf-8")
        sealed = seal_feature(
            self.run_dir,
            "FEAT-001",
            "validation, tests, evaluation, docs, and spec alignment passed",
            "feat(FEAT-001): seal feature",
        )
        promoted = promote_feature(self.run_dir, "FEAT-001", "final evidence passed")
        self.assertEqual(promoted["status"], "done")
        self.assertEqual(
            git(self.project, "rev-parse", "HEAD"), sealed["integration_head"]
        )
        self.assertEqual(
            (self.project / "src" / "a.py").read_text(), "VALUE = 'TASK-001'\n"
        )
        self.assertEqual(
            (self.project / "src" / "b.py").read_text(), "VALUE = 'TASK-002'\n"
        )
        self.assertEqual(
            (self.project / "src" / "c.py").read_text(), "VALUE = 'TASK-003'\n"
        )
        self.assertNotIn(
            "codex/sdlc/", git(self.project, "branch", "--format=%(refname:short)")
        )
        listed = git(self.project, "worktree", "list", "--porcelain")
        self.assertEqual(listed.count("worktree "), 1)

    def test_dirty_project_is_rejected(self) -> None:
        (self.project / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "WORKTREE_CONFLICT")

    def test_tdd_seal_recovers_commit_completed_before_state_write(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        message = "test(FEAT-001): interrupted TDD seal"
        append_journal(
            journal_path(self.run_dir, "FEAT-001", "coordinator"),
            {
                "event": "tdd_seal_intent",
                "base": coordinator["integration_head"],
                "message": message,
            },
        )
        (integration / "test_contract.py").write_text("# red first\n", encoding="utf-8")
        git(integration, "add", "test_contract.py")
        git(integration, "commit", "-m", message)
        recovered = seal_tdd_base(self.run_dir, "FEAT-001", message)
        self.assertEqual(recovered["status"], "tdd_sealed")
        self.assertEqual(
            recovered["integration_head"], git(integration, "rev-parse", "HEAD")
        )

    def test_tdd_seal_rejects_unjournaled_head_drift(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "unexpected.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(integration, "add", "unexpected.py")
        git(integration, "commit", "-m", "test(FEAT-001): expected message")
        with self.assertRaises(ExecutionError) as raised:
            seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): expected message")
        self.assertEqual(raised.exception.code, "WORKTREE_CONFLICT")

    def test_missing_plan_lock_is_rejected(self) -> None:
        self.plan.with_suffix(self.plan.suffix + ".lock").unlink()
        with self.assertRaises(ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_symlinked_plan_lock_is_rejected(self) -> None:
        lock = self.plan.with_suffix(self.plan.suffix + ".lock")
        lock.unlink()
        target = self.run_dir / "lock-target"
        target.write_text("locked\n", encoding="utf-8")
        lock.symlink_to(target)
        with self.assertRaises(ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_plan_outside_private_run_is_rejected(self) -> None:
        outside = self.root / "outside-plan.md"
        outside.write_text(PLAN, encoding="utf-8")
        outside.with_suffix(outside.suffix + ".lock").write_text(
            "locked\n", encoding="utf-8"
        )
        with self.assertRaises(ExecutionError) as raised:
            prepare_execution(
                self.run_dir, self.project, "FEAT-001", outside, capacity=2
            )
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_feature_id_path_traversal_is_rejected(self) -> None:
        with self.assertRaises(ExecutionError) as raised:
            prepare_execution(
                self.run_dir, self.project, "../FEAT-001", self.plan, capacity=2
            )
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")

    def test_plan_heading_must_match_feature(self) -> None:
        self.plan.write_text(PLAN.replace("# FEAT-001", "# FEAT-002"), encoding="utf-8")
        with self.assertRaises(ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX semantics")
    def test_symlinked_write_claim_is_rejected(self) -> None:
        outside = self.root / "outside-src"
        outside.mkdir()
        (self.project / "src").symlink_to(outside, target_is_directory=True)
        git(self.project, "add", "src")
        git(self.project, "commit", "-m", "add symlink fixture")
        with self.assertRaises(ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_existing_internal_branch_is_rejected(self) -> None:
        git(
            self.project,
            "branch",
            "codex/sdlc/run-1/feat-001/integration",
            self.base_head,
        )
        with self.assertRaises(ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "WORKTREE_COLLISION")

    def test_worker_outside_write_claim_requires_replan(self) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001")
        worker = Path(assignment["worktree"])
        (worker / "outside.py").write_text("outside = True\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as raised:
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "validation passed",
                "review passed",
                "feat: invalid",
                summary="invalid-path handoff",
            )
        self.assertEqual(raised.exception.code, "REPLAN_REQUIRED")
        self.assertEqual(git(worker, "diff", "--cached", "--name-only"), "")
        self.assertEqual(
            git(self.project, "rev-parse", "HEAD"), coordinator["base_head"]
        )

    def test_wave_prepare_adopts_exact_interrupted_worker_resource(self) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        integration = Path(coordinator["integration_worktree"])
        current_head = git(integration, "rev-parse", "HEAD")
        branch_name = "codex/sdlc/run-1/feat-001/wave-001/task-001"
        worker = (
            self.run_dir / "worktrees" / "FEAT-001" / "waves" / "WAVE-001" / "TASK-001"
        )
        git(integration, "branch", branch_name, current_head)
        worker.parent.mkdir(parents=True)
        git(
            integration,
            "worktree",
            "add",
            "--lock",
            "--reason",
            "interrupted fixture",
            str(worker),
            branch_name,
        )
        assignments = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(assignments[0]["worktree"], str(worker.resolve()))
        self.assertTrue(
            self.run_dir.joinpath(
                "execution", "FEAT-001", "assignments", "WAVE-001", "TASK-001.json"
            ).is_file()
        )

    def test_wave_prepare_recovers_branch_created_before_worktree(self) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        integration = Path(coordinator["integration_worktree"])
        branch_name = "codex/sdlc/run-1/feat-001/wave-001/task-001"
        git(integration, "branch", branch_name, "HEAD")
        assignments = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        worker = Path(assignments[0]["worktree"])
        self.assertTrue(worker.is_dir())
        self.assertEqual(git(worker, "branch", "--show-current"), branch_name)

    def test_prepare_recovers_branch_created_before_integration_worktree(self) -> None:
        common_raw = git(self.project, "rev-parse", "--git-common-dir")
        common = Path(common_raw)
        if not common.is_absolute():
            common = (self.project / common).resolve()
        branch_name = "codex/sdlc/run-1/feat-001/integration"
        integration = self.run_dir / "worktrees" / "FEAT-001" / "integration"
        git(self.project, "branch", branch_name, self.base_head)
        state_path = coordinator_path(self.run_dir, "FEAT-001")
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps(
                {
                    "schema": "agentic-sdlc/execution-coordinator-v4",
                    "state_version": 4,
                    "feature_id": "FEAT-001",
                    "run_id": "run-1",
                    "project_root": str(self.project.resolve()),
                    "git_root": str(self.project.resolve()),
                    "selected_project_root": str(self.project.resolve()),
                    "project_scope": ".",
                    "git_common_dir": str(common),
                    "base_branch": "feature/test",
                    "base_head": self.base_head,
                    "plan_path": str(self.plan),
                    "plan_digest": hashlib.sha256(self.plan.read_bytes()).hexdigest(),
                    "capacity": 2,
                    "status": "blocked",
                    "integration_branch": branch_name,
                    "integration_worktree": str(integration),
                    "integration_head": self.base_head,
                    "wave_ids": ["WAVE-001", "WAVE-002"],
                    "active_wave": None,
                    "promoted_head": None,
                    "cleanup_retained": [],
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        recovered = self.prepare()
        self.assertEqual(recovered["status"], "prepared")
        self.assertTrue(integration.is_dir())

    def test_integrate_recovers_merge_completed_before_state_write(self) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignments = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        for assignment in assignments:
            self.start_assignment(assignment, "WAVE-001")
            worker = Path(assignment["worktree"])
            filename = "a.py" if assignment["task_id"] == "TASK-001" else "b.py"
            (worker / "src").mkdir(exist_ok=True)
            (worker / "src" / filename).write_text("VALUE = 1\n", encoding="utf-8")
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "validation passed",
                "review passed",
                f"feat: {assignment['task_id']}",
                summary=f"completed {assignment['task_id']}",
            )
        integration = Path(coordinator["integration_worktree"])
        git(integration, "merge", "--no-ff", "--no-edit", assignments[0]["branch"])
        integrated = integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(integrated["merged_task_ids"], ["TASK-001", "TASK-002"])

    def test_promote_recovers_fast_forward_completed_before_state_write(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(integration, "add", "feature.py")
        git(integration, "commit", "-m", "feat: interrupted promotion fixture")
        promoted_tip = git(integration, "rev-parse", "HEAD")
        state_path = coordinator_path(self.run_dir, "FEAT-001")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "sealed"
        state["integration_head"] = promoted_tip
        state_path.write_text(json.dumps(state), encoding="utf-8")
        git(self.project, "merge", "--ff-only", promoted_tip)
        recovered = promote_feature(self.run_dir, "FEAT-001", "evidence passed")
        self.assertEqual(recovered["status"], "done")
        self.assertEqual(recovered["promoted_head"], promoted_tip)

    def test_final_seal_recovers_commit_completed_before_state_write(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        state_path = coordinator_path(self.run_dir, "FEAT-001")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "integrated"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        message = "feat(FEAT-001): interrupted final seal"
        append_journal(
            journal_path(self.run_dir, "FEAT-001", "coordinator"),
            {
                "event": "feature_seal_intent",
                "base": coordinator["integration_head"],
                "message": message,
            },
        )
        (integration / "release.md").write_text("ready\n", encoding="utf-8")
        git(integration, "add", "release.md")
        git(integration, "commit", "-m", message)
        recovered = seal_feature(
            self.run_dir, "FEAT-001", "final evidence passed", message
        )
        self.assertEqual(recovered["status"], "sealed")
        self.assertEqual(
            recovered["integration_head"], git(integration, "rev-parse", "HEAD")
        )

    def test_promotion_rejects_dirty_project_checkout(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        for wave_id in ("WAVE-001", "WAVE-002"):
            for assignment in prepare_wave(self.run_dir, "FEAT-001", wave_id):
                self.start_assignment(assignment, wave_id)
                worker = Path(assignment["worktree"])
                filename = {"TASK-001": "a.py", "TASK-002": "b.py", "TASK-003": "c.py"}[
                    assignment["task_id"]
                ]
                (worker / "src").mkdir(exist_ok=True)
                (worker / "src" / filename).write_text("VALUE = 1\n", encoding="utf-8")
                finish_task(
                    self.run_dir,
                    "FEAT-001",
                    wave_id,
                    assignment["task_id"],
                    "validation passed",
                    "review passed",
                    f"feat: {assignment['task_id']}",
                    summary=f"completed {assignment['task_id']}",
                )
            integrate_wave(self.run_dir, "FEAT-001", wave_id)
            complete_wave(self.run_dir, "FEAT-001", wave_id, "combined tests passed")
        seal_feature(self.run_dir, "FEAT-001", "all evidence passed", "feat: seal")
        (self.project / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as raised:
            promote_feature(self.run_dir, "FEAT-001", "all evidence passed")
        self.assertEqual(raised.exception.code, "PROMOTION_BLOCKED")

    def test_cleanup_retry_accepts_already_removed_reachable_worker(self) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignments = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        for assignment in assignments:
            self.start_assignment(assignment, "WAVE-001")
            worker = Path(assignment["worktree"])
            filename = "a.py" if assignment["task_id"] == "TASK-001" else "b.py"
            (worker / "src").mkdir(exist_ok=True)
            (worker / "src" / filename).write_text("VALUE = 1\n", encoding="utf-8")
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "validation passed",
                "review passed",
                f"feat: {assignment['task_id']}",
                summary=f"completed {assignment['task_id']}",
            )
        integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")
        first = assignments[0]
        worker = Path(first["worktree"])
        integration = Path(coordinator["integration_worktree"])
        git(integration, "worktree", "unlock", str(worker))
        git(integration, "worktree", "remove", str(worker))
        git(integration, "branch", "-d", first["branch"])
        state_path = wave_path(self.run_dir, "FEAT-001", "WAVE-001")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "blocked"
        state["blocker"] = "CLEANUP_BLOCKED"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        completed = complete_wave(
            self.run_dir, "FEAT-001", "WAVE-001", "combined tests passed"
        )
        self.assertEqual(completed["status"], "done")

    def test_wrong_assignment_digest_is_rejected(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        with self.assertRaises(ExecutionError) as raised:
            start_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "bad",
                "test-session-bad-digest",
                Path(assignment["scope_cwd"]),
            )
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX semantics")
    def test_worker_cannot_commit_symlink_inside_write_claim(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001")
        worker = Path(assignment["worktree"])
        outside = self.root / "outside.py"
        outside.write_text("outside = True\n", encoding="utf-8")
        (worker / "src").mkdir()
        (worker / "src" / "a.py").symlink_to(outside)
        with self.assertRaises(ExecutionError) as raised:
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "validation passed",
                "review passed",
                "feat: invalid symlink",
                summary="invalid-symlink handoff",
            )
        self.assertEqual(raised.exception.code, "REPLAN_REQUIRED")

    def test_v1_v2_and_v3_always_require_workflow_upgrade(self) -> None:
        state = coordinator_path(self.run_dir, "FEAT-001")
        state.parent.mkdir(parents=True, exist_ok=True)
        for version in (1, 2, 3):
            for status in ("running", "done"):
                with self.subTest(version=version, status=status):
                    state.write_text(
                        json.dumps(
                            {
                                "schema": (
                                    "agentic-sdlc/execution-coordinator-"
                                    f"v{version}"
                                ),
                                "status": status,
                            }
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ExecutionError) as raised:
                        self.prepare()
                    self.assertEqual(
                        raised.exception.code, "WORKFLOW_UPGRADE_REQUIRED"
                    )


class ManagedOuterLifecycleTests(unittest.TestCase):
    def test_managed_outer_execution_releases_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace with spaces"
            root.mkdir()
            origin = root / "origin.git"
            git(root, "init", "--bare", "-q", str(origin))
            repository = root / "example-monorepo"
            git(root, "init", "-q", "-b", "main", str(repository))
            git(repository, "config", "user.name", "Managed SDLC Test")
            git(repository, "config", "user.email", "sdlc@example.invalid")
            selected = repository / "services" / "example"
            selected.mkdir(parents=True)
            (selected / "service.txt").write_text("base\n", encoding="utf-8")
            git(repository, "add", "-A")
            git(repository, "commit", "-qm", "initial")
            git(repository, "remote", "add", "origin", str(origin))
            git(repository, "push", "-qu", "origin", "main")
            git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
            git(repository, "fetch", "-q", "origin")

            manager_path = (
                SCRIPT_DIR.parents[1] / "worktree" / "scripts" / "worktree_manager.py"
            )
            sys.path.insert(0, str(manager_path.parent))
            spec = importlib.util.spec_from_file_location(
                "managed_sdlc_worktree_manager", manager_path
            )
            assert spec and spec.loader
            manager = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = manager
            spec.loader.exec_module(manager)
            with mock.patch.object(manager.secrets, "token_hex", return_value="a7c2f9"):
                outer = manager.add_worktree(
                    cwd=selected, project=None, task_slug="agentic-sdlc-composed"
                )
            outer_root = Path(str(outer["worktree"]))
            outer_selected = Path(str(outer["scope_cwd"]))
            run_dir = root / "private" / "run-managed"
            plan = run_dir / "plans" / "FEAT-001.plan.v1.md"
            plan.parent.mkdir(parents=True)
            plan.write_text(MANAGED_PLAN, encoding="utf-8")
            plan.with_suffix(plan.suffix + ".lock").write_text(
                "locked\n", encoding="utf-8"
            )

            prepare_execution(run_dir, outer_selected, "FEAT-001", plan, capacity=1)
            with self.assertRaisesRegex(manager.WorktreeError, "still owns"):
                manager.publication_begin(cwd=outer_selected, action="create-pr")

            seal_tdd_base(run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
            assignment = prepare_wave(run_dir, "FEAT-001", "WAVE-001")[0]
            start_task(
                run_dir,
                "FEAT-001",
                "WAVE-001",
                "TASK-001",
                assignment["assignment_digest"],
                "managed-worker-session",
                Path(assignment["scope_cwd"]),
            )
            value = Path(assignment["scope_cwd"]) / "src" / "value.py"
            value.parent.mkdir(parents=True)
            value.write_text("VALUE = 1\n", encoding="utf-8")
            finish_task(
                run_dir,
                "FEAT-001",
                "WAVE-001",
                "TASK-001",
                "focused test passed",
                "review passed",
                "feat(FEAT-001): add selected-scope value",
                summary="selected-scope handoff",
            )
            integrate_wave(run_dir, "FEAT-001", "WAVE-001")
            complete_wave(run_dir, "FEAT-001", "WAVE-001", "combined tests passed")
            seal_feature(
                run_dir,
                "FEAT-001",
                "final alignment passed",
                "feat(FEAT-001): seal selected scope",
            )
            promoted = promote_feature(run_dir, "FEAT-001", "promotion checks passed")
            promoted_head = str(promoted["promoted_head"])
            self.assertEqual(git(outer_root, "rev-parse", "HEAD"), promoted_head)

            complete_evidence = {
                "final_alignment": "passed",
                "uat": "passed",
                "docs": "passed",
            }
            for missing in complete_evidence:
                incomplete = dict(complete_evidence)
                incomplete[missing] = ""
                with self.subTest(missing=missing):
                    with self.assertRaises(ExecutionInteropError):
                        release(
                            run_dir,
                            outer_selected,
                            promoted_head,
                            **incomplete,
                        )
                    with self.assertRaisesRegex(manager.WorktreeError, "still owns"):
                        manager.publication_begin(
                            cwd=outer_selected, action="create-pr"
                        )

            released = release(
                run_dir,
                outer_selected,
                promoted_head,
                **complete_evidence,
            )
            self.assertEqual(released["status"], "released")
            reservation = manager.publication_begin(
                cwd=outer_selected, action="create-pr"
            )
            self.assertEqual(reservation["status"], "acquired")


if __name__ == "__main__":
    unittest.main()
