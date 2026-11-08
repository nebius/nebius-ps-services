"""Focused policy, no-change and independent acceptance regressions (synthetic host)."""
import json
import hashlib
import os
import tempfile
import subprocess
from pathlib import Path
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from datetime import datetime, timedelta, timezone

import workflow_execution as engine
import workflow_capacity as capacity
import workflow_validation as validation
import test_workflow_execution as fixture_module
from test_workflow_execution import finish_task, write_private, git  # noqa: E402
from test_workflow_fixture import seed_validation  # noqa: E402


class WaveContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.GitLifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.run = self.fixture.run_dir

    def test_invalid_validation_identity_rejected_before_lock_creation(self):
        with mock.patch.object(validation, "lock") as lock:
            for identity in (("../outside", "WAVE-001", None),
                             ("FEAT-001", "../outside", None),
                             ("FEAT-001", "WAVE-001", "../outside")):
                with self.subTest(identity=identity), self.assertRaises(engine.ExecutionError):
                    validation.produce(self.run, *identity, session="deterministic-coordinator")
            lock.assert_not_called()

    def test_output_allocation_failure_does_not_launch_process_group(self):
        check = {"id": "fixture", "argv": ["{python}", "-c", "pass"],
                 "timeout_seconds": 10, "environment_keys": []}
        with mock.patch.object(validation.tempfile, "TemporaryFile", side_effect=OSError("fixture")), \
                mock.patch.object(validation, "OwnedProcessGroup") as owner:
            with self.assertRaises(OSError):
                validation._run_check(check, self.fixture.project)
            owner.assert_not_called()

    def test_relative_path_uses_and_hashes_target_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, target = root / "coordinator", root / "target"
            for directory, code in ((coordinator, 9), (target, 0)):
                (directory / "bin").mkdir(parents=True)
                executable = directory / "bin/check"
                executable.write_text(f"#!/bin/sh\nexit {code}\n")
                executable.chmod(0o700)
            previous = Path.cwd()
            try:
                os.chdir(coordinator)
                with mock.patch.dict(os.environ, {"PATH": "bin"}):
                    result = validation._run_check({"id": "target-check", "argv": ["check"],
                        "timeout_seconds": 5, "environment_keys": ["PATH"]}, target)
            finally:
                os.chdir(previous)
            self.assertEqual(result["outcome"], "passed")
            self.assertEqual(result["executable_sha256"],
                             hashlib.sha256((target / "bin/check").read_bytes()).hexdigest())

    def test_missing_frozen_policy_is_not_recreated(self):
        self.fixture.prepare()
        path = self.run / "execution-policy.json"
        path.unlink()
        with self.assertRaisesRegex(Exception, "policy is missing"):
            capacity.policy(self.run)
        self.assertFalse(path.exists())

    def test_out_of_order_wave_does_not_reserve_resources(self):
        self.fixture.prepare()
        engine.seal_tdd_base(self.run, "FEAT-001", "test: base")
        with self.assertRaisesRegex(engine.ExecutionError, "prior wave is incomplete"):
            engine.prepare_wave(self.run, "FEAT-001", "WAVE-002")
        self.assertFalse((self.run / "resource-reservations" / "FEAT-001-WAVE-002.json").exists())

    def test_evaluator_does_not_block_peer_heartbeat(self):
        self.fixture.prepare()
        engine.seal_tdd_base(self.run, "FEAT-001", "test: base")
        first, peer = engine.prepare_wave(self.run, "FEAT-001", "WAVE-001")
        self.fixture.start_assignment(first, "WAVE-001")
        self.fixture.start_assignment(peer, "WAVE-001")
        entered, release = threading.Event(), threading.Event()
        original = validation._run_check
        def delayed(check, cwd):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test evaluator release timed out")
            return original(check, cwd)
        with mock.patch.object(validation, "_run_check", side_effect=delayed), ThreadPoolExecutor(max_workers=2) as pool:
            finishing = pool.submit(finish_task, self.run, "FEAT-001", "WAVE-001", first["task_id"],
                                    "fixture acceptance", "fixture review", "", summary="Already accepted",
                                    status="verified_no_change")
            try:
                self.assertTrue(entered.wait(5))
                heartbeat = pool.submit(engine.heartbeat_task, self.run, "FEAT-001", "WAVE-001", peer["task_id"],
                    peer["assignment_digest"], "reviewing", f"test-session-WAVE-001-{peer['task_id']}", Path(peer["scope_cwd"]))
                self.assertEqual(heartbeat.result(timeout=5)["status"], "ACTIVE")
            finally:
                release.set()
            self.assertEqual(finishing.result(timeout=10)["status"], "verified_no_change")
        before = engine.read_json(engine.result_path(self.run, "FEAT-001", "WAVE-001", first["task_id"]))
        replay = validation.produce(self.run, "FEAT-001", "WAVE-001", first["task_id"], session="deterministic-coordinator")
        self.assertEqual(Path(replay["receipt_path"]).stem, before["acceptance_receipt_sha256"])
        engine._validate_result_record(before, first)

    def test_parallel_policy_exceeds_four_without_cpu_count_cap(self):
        write_private(self.run / "execution-policy.json", {"schema": capacity.SCHEMA,
                      "worker_limit": 8, "wave_task_limit": 16, "worker_worktree_limit": 32})
        capacity.observe(self.run, native_slots=12, evidence="synthetic native capacity fixture",
                         session="deterministic-coordinator")
        with mock.patch("os.cpu_count", return_value=1):
            self.assertEqual(capacity.effective(self.run, 8), 8)
        self.assertEqual(capacity.policy(self.run)["wave_task_limit"], 16)

    def test_combined_evaluator_blocks_handoff_replan_and_retirement(self):
        import workflow_objectives as objectives
        self.finish_wave()
        write_private(self.run.parent / "active-run.json", {"run_id": self.run.name})
        entered, release = threading.Event(), threading.Event()
        original = validation._run_check

        def delayed(check, cwd):
            entered.set()
            if not release.wait(20):
                raise RuntimeError("test evaluator release timed out")
            return original(check, cwd)

        with mock.patch.object(validation, "_run_check", side_effect=delayed), ThreadPoolExecutor(max_workers=1) as pool:
            producing = pool.submit(validation.produce, self.run, "FEAT-001", "WAVE-001", session="deterministic-coordinator")
            try:
                self.assertTrue(entered.wait(5))
                with mock.patch.object(objectives, "project_context", return_value=(self.run.parent, None, None, None)), \
                        mock.patch.object(objectives, "_bind"), \
                        mock.patch("workflow_reconcile.validate_takeover", return_value="fixture-proof"):
                    for takeover in (None, self.run / "takeovers/fixture.json"):
                        with self.subTest(takeover=takeover), self.assertRaisesRegex(Exception, "validation.*quiescent"):
                            objectives.continue_run(self.fixture.project, self.run.parent, "agentic-sdlc",
                                                    "replacement-coordinator", takeover=takeover)
                with self.assertRaisesRegex(Exception, "validation.*quiescent"):
                    engine.replan_future(self.run, "FEAT-001", self.fixture.plan, 2)
                with self.assertRaisesRegex(Exception, "validation.*quiescent"):
                    with engine._execution_transition_lock(self.run, "FEAT-001"):
                        engine._complete_wave_locked(self.run, "FEAT-001", "WAVE-001", "fixture", failure={})
            finally:
                release.set()
            self.assertEqual(producing.result(timeout=10)["outcome"], "passed")
        objectives._writers_quiescent(self.run)

    def test_unproven_evaluator_cleanup_retains_ownership(self):
        self.finish_wave()
        original = validation._run_check

        def failed_start(check, cwd):
            with mock.patch("workflow_process.selectors.DefaultSelector") as selector, \
                    mock.patch("workflow_process.subprocess.Popen") as spawn, \
                    mock.patch.object(validation.OwnedProcessGroup, "close", side_effect=subprocess.TimeoutExpired("anchor", 5)):
                selector.return_value.__enter__.return_value.select.return_value = []
                try:
                    return original(check, cwd)
                finally:
                    spawn.assert_called_once()

        with mock.patch.object(validation, "_run_check", side_effect=failed_start):
            with self.assertRaises(validation.ProcessCleanupError):
                validation.produce(self.run, "FEAT-001", "WAVE-001", session="deterministic-coordinator")
        with self.assertRaisesRegex(Exception, "validation.*quiescent"):
            validation.require_quiescent(self.run)

    def test_old_coordinator_cannot_start_or_replay_validation(self):
        self.finish_wave()
        for completed in (False, True):
            if completed:
                validation.produce(self.run, "FEAT-001", "WAVE-001", session="deterministic-coordinator")
            with self.subTest(completed=completed), self.assertRaisesRegex(Exception, "bound native coordinator"):
                validation.produce(self.run, "FEAT-001", "WAVE-001", session="old-coordinator")

    def test_coordinator_change_during_evaluation_cannot_seal_receipt(self):
        self.finish_wave()
        original = validation._run_check
        receipt_root = self.run / "execution/FEAT-001/validation/receipts"
        before = set(receipt_root.glob("*.json"))

        def changed(check, cwd):
            result = original(check, cwd)
            run = engine.read_json(self.run / "run.json")
            write_private(self.run / "run.json", {**run, "coordinator_session_sha256": "b" * 64})
            return result

        with mock.patch.object(validation, "_run_check", side_effect=changed):
            with self.assertRaisesRegex(Exception, "coordinator.*changed"):
                validation.produce(self.run, "FEAT-001", "WAVE-001", session="deterministic-coordinator")
        self.assertEqual(set(receipt_root.glob("*.json")), before)

    def test_zero_unknown_stale_and_other_session_capacity(self):
        capacity.observe(self.run, native_slots=0, evidence="synthetic zero slots",
                         session="deterministic-coordinator")
        self.assertEqual(capacity.effective(self.run, 8), 0)
        capacity.observe(self.run, native_slots=None, evidence="no native capacity query",
                         session="deterministic-coordinator")
        self.assertEqual(capacity.effective(self.run, 8), 1)
        observed = capacity.observe(self.run, native_slots=8, evidence="synthetic slots",
                                    session="deterministic-coordinator")
        for changed in ({"observed_at": (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()},
                        {"coordinator_session_sha256": "b" * 64}, {"host_sha256": "b" * 64}):
            write_private(self.run / "host-capacity.json", {**observed, **changed})
            self.assertEqual(capacity.effective(self.run, 8), 1)

    def test_whole_wave_reservation_blocks_before_allocating(self):
        coordinator = self.fixture.prepare()
        engine.seal_tdd_base(self.run, "FEAT-001", "test: base")
        limits = capacity.policy(self.run)
        other = self.run / "execution" / "FEAT-002"
        write_private(other / "coordinator.json", {**coordinator, "execution_id": "FEAT-002", "status": "done"})
        write_private(other / "waves" / "WAVE-001.json", {"status": "planned", "task_ids": [f"TASK-{i:03}" for i in range(1, limits["worker_worktree_limit"])]})
        write_private(self.run / "resource-reservations" / "FEAT-002-WAVE-001.json", {
            "schema": "workflow/wave-reservation-v1", "execution_id": "FEAT-002", "wave_id": "WAVE-001",
            "task_ids": [f"TASK-{i:03}" for i in range(1, limits["worker_worktree_limit"])],
            "policy_sha256": capacity.digest(limits)})
        with self.assertRaisesRegex(engine.ExecutionError, "whole wave"):
            engine.prepare_wave(self.run, "FEAT-001", "WAVE-001")
        self.assertFalse((self.run / "worktrees" / "FEAT-001" / "waves").exists())

    def finish_wave(self, *, mixed=False):
        self.fixture.prepare()
        engine.seal_tdd_base(self.run, "FEAT-001", "test: base")
        assignments = engine.prepare_wave(self.run, "FEAT-001", "WAVE-001")
        for index, assignment in enumerate(assignments):
            self.fixture.start_assignment(assignment, "WAVE-001")
            status = "verified_no_change"
            if mixed and index == 0:
                path = Path(assignment["worktree"]) / "src/a.py"
                path.parent.mkdir(exist_ok=True)
                path.write_text("VALUE = 1\n")
                status = "implemented"
            finish_task(self.run, "FEAT-001", "WAVE-001", assignment["task_id"],
                        "fixture acceptance", "fixture review", "feat: change" if status == "implemented" else "",
                        summary="fixture result", status=status)
        return engine.integrate_wave(self.run, "FEAT-001", "WAVE-001")

    def test_mixed_wave_merges_only_changed_task_and_requires_receipt(self):
        wave = self.finish_wave(mixed=True)
        coordinator = engine._load_coordinator(self.run, "FEAT-001")
        self.assertEqual(git(Path(coordinator["integration_worktree"]), "rev-list", "--count", "--merges",
                             f"{wave['base_head']}..{wave['integration_head']}"), "1")
        produced = validation.produce(self.run, "FEAT-001", "WAVE-001", session="deterministic-coordinator")
        receipt = Path(produced["receipt_path"])
        original = json.loads(receipt.read_text())
        write_private(receipt, {**original, "target": {**original["target"], "head": "f" * 40}})
        with self.assertRaises(engine.ExecutionError):
            engine.complete_wave(self.run, "FEAT-001", "WAVE-001", receipt)
        write_private(receipt, original)
        self.assertEqual(engine.complete_wave(self.run, "FEAT-001", "WAVE-001", receipt)["status"], "done")

    def test_failed_combined_check_cannot_complete_wave(self):
        seed_validation(self.fixture.plan)
        path = validation.plan_path(self.fixture.plan)
        plan = json.loads(path.read_text())
        plan["wave_checks"][0]["argv"] = ["{python}", "-c", "raise SystemExit(13)"]
        write_private(path, plan)
        self.finish_wave()
        produced = validation.produce(self.run, "FEAT-001", "WAVE-001", session="deterministic-coordinator")
        self.assertEqual(produced["outcome"], "failed")
        with self.assertRaisesRegex(engine.ExecutionError, "did not pass"):
            engine.complete_wave(self.run, "FEAT-001", "WAVE-001", Path(produced["receipt_path"]))
        self.assertTrue((self.run / "worktrees" / "FEAT-001" / "waves" / "WAVE-001" / "TASK-001").exists())


if __name__ == "__main__":
    unittest.main()
