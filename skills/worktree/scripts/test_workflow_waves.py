"""Focused policy, no-change and independent acceptance regressions (synthetic host)."""
import json
import hashlib
import os
import sys
import tempfile
import subprocess
from pathlib import Path
import unittest
import threading
import venv
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from datetime import datetime, timedelta, timezone

import workflow_execution as engine
import workflow_capacity as capacity
import workflow_validation as validation
import workflow_process
import test_workflow_execution as fixture_module
from test_workflow_execution import finish_task, write_private, git  # noqa: E402
from test_workflow_fixture import seed_validation  # noqa: E402


class ValidationInvocationTests(unittest.TestCase):
    def test_incomplete_stream_overrides_nominal_group_cleanup(self):
        observation = workflow_process.run_owned([sys.executable, "-c", "pass"])
        observation["stdout"]["complete"] = False
        check = {"id": "check", "argv": ["{python}", "-c", "pass"],
                 "execution_policy": {"observation_interval_seconds": 30, "deadline": None}, "environment_keys": []}
        with mock.patch.object(validation, "run_owned", return_value=observation):
            with self.assertRaises(workflow_process.ProcessCleanupError) as error:
                validation._run_check(check, Path.cwd())
        self.assertFalse(error.exception.observation["local_group_quiescent"])

    def test_virtualenv_interpreter_keeps_its_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = root / "environment"
            venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
            python = environment / "bin/python"
            self.assertTrue(python.is_symlink())
            discovered = subprocess.run(
                [str(python), "-B", "-c",
                 "import sysconfig; print(sysconfig.get_path('purelib'))"],
                check=True, capture_output=True, text=True,
            )
            (Path(discovered.stdout.strip()) / "validation_venv_probe.py").write_text("VALUE = 41\n")
            probe = ("import sys; from validation_venv_probe import VALUE; "
                     "assert VALUE == 41; assert sys.prefix != sys.base_prefix")
            for executable in (str(python), "environment/bin/python", "{python}", "python"):
                with self.subTest(executable=executable), \
                        mock.patch.object(validation.sys, "executable", str(python)), \
                        mock.patch.dict(os.environ, {"PATH": str(python.parent)}):
                    result = validation._run_check(
                        {"id": "venv-import", "argv": [executable, "-c", probe],
                         "execution_policy": {"observation_interval_seconds": 30, "deadline": None}, "environment_keys": ["PATH"]}, root,
                    )
                    self.assertEqual(result["outcome"], "passed")
                    self.assertEqual(result["executable_sha256"],
                                     hashlib.sha256(python.read_bytes()).hexdigest())


class CandidateCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.GitLifecycleTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.run = self.fixture.run_dir
        # One real completed wave is sufficient for checkpoint ownership; the
        # independent verifier composition test covers multiple wave lineage.
        self.fixture.plan.write_text(self.fixture.plan.read_text().split("### TASK-002")[0])
        self.fixture.complete_first_wave()
        self.coordinator = engine._load_coordinator(self.run, "FEAT-001")
        self.integration = Path(self.coordinator["integration_worktree"])
        self.parent = engine.head(self.integration)
        self.waves = {p: p.read_bytes() for p in (self.run / "execution/FEAT-001/waves").glob("*.json")}
        self.publish()

    def publish(self):
        from project_specs_lib.transaction import publish_spec_pair
        from project_specs_lib.impact import statement_inventory
        from workflow_publication import _basis
        from workflow_refinement import refinement, settle
        from workflow_impact import CLAIM_SCHEMA, settle_execution
        from workflow_state import read
        original = {k: (self.integration / "docs" / f"{k}.md").read_bytes()
                    for k in ("requirements", "design")}
        objective = read(self.run / "objective.json")["revisions"][-1]
        self.operation = "candidate-authoring-fixture"
        with mock.patch("agent_runtime.native_session_id", return_value="deterministic-coordinator"):
            publish_spec_pair(self.integration, requirements_candidate=original["requirements"],
                design_candidate=original["design"].replace(b"delivery=not-started", b"delivery=implemented"),
                expected_git_head=self.parent,
                expected_requirements_sha256=hashlib.sha256(original["requirements"]).hexdigest(),
                expected_design_sha256=hashlib.sha256(original["design"]).hexdigest(),
                expected_objective_sha256=objective["sha256"], expected_basis_sha256=_basis(self.run, "FEAT-001"),
                operation_id=self.operation)
        refined = refinement(self.run)
        claim = {"schema": CLAIM_SCHEMA, "objective_id": refined["objective_id"],
                 "revision": refined["revision"], "intent_sha256": refined["intent_sha256"],
                 "declared_effects": [], "declared_plan_action": "retain_plan",
                 "dispositions": [{"statement": row["key"], "disposition": "existing_contract",
                    "requirements": ["REQ-001"], "design": ["FEAT-001"], "effects": [],
                    "reason": None}
                    for row in statement_inventory(refined["extracted"])]}
        settle(self.run, claim)
        settle_execution(self.run, "FEAT-001", self.coordinator["plan_digest"], self.fixture.project)

    def checkpoint(self):
        from workflow_candidate import checkpoint
        return checkpoint(self.run, "FEAT-001", self.operation, "Document implemented delivery",
                          session="deterministic-coordinator")

    def test_checkpoint_validation_preserves_waves_and_blocks_early_seal(self):
        result = self.checkpoint()
        self.assertNotEqual(result["head"], self.parent)
        self.assertEqual(self.checkpoint(), result)
        self.assertEqual(engine._load_coordinator(self.run, "FEAT-001")["status"], "integrated")
        with self.assertRaises(Exception):
            engine.seal_feature(self.run, "FEAT-001", "checks", "Final seal")
        with self.assertRaisesRegex(Exception, "combined wave target drifted"):
            validation.produce(self.run, "FEAT-001", "WAVE-001", session="deterministic-coordinator")
        receipt = validation.produce(self.run, "FEAT-001", None, session="deterministic-coordinator")
        self.assertEqual(receipt["outcome"], "passed")
        self.assertEqual(validation.produce(self.run, "FEAT-001", None,
                                          session="deterministic-coordinator"), receipt)
        sealed = engine.seal_feature(self.run, "FEAT-001", "checks", "Final seal")
        self.assertEqual(sealed["integration_head"], result["head"])
        self.assertEqual({p: p.read_bytes() for p in self.waves}, self.waves)

    def test_unrelated_dirt_and_wrong_coordinator_are_rejected(self):
        from workflow_candidate import checkpoint
        before = engine.head(self.integration)
        (self.integration / "README.md").write_text("unrelated change\n")
        with self.assertRaisesRegex(Exception, "unrelated dirty"):
            self.checkpoint()
        with self.assertRaises(Exception):
            checkpoint(self.run, "FEAT-001", self.operation, "Wrong caller", session="other")
        self.assertEqual(engine.head(self.integration), before)
        self.assertEqual((self.integration / "README.md").read_text(), "unrelated change\n")

    def test_interrupted_commit_recovers_only_exact_journaled_child(self):
        import workflow_candidate as candidate
        save = candidate._save
        def interrupt(path, value):
            if path.name == "sealed.json":
                raise KeyboardInterrupt("synthetic crash after Git commit")
            return save(path, value)
        with mock.patch.object(candidate, "_save", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.checkpoint()
        committed = engine.head(self.integration)
        self.assertNotEqual(committed, self.parent)
        self.assertEqual(engine._load_coordinator(self.run, "FEAT-001")["integration_head"], self.parent)
        self.assertEqual(self.checkpoint()["head"], committed)
        self.assertEqual(engine.head(self.integration), committed)

    def test_unjournaled_commit_and_tampered_history_are_rejected(self):
        git(self.integration, "add", "-A")
        git(self.integration, "commit", "-m", "Unjournaled authoring")
        with self.assertRaises(Exception):
            self.checkpoint()
        self.assertEqual(engine._load_coordinator(self.run, "FEAT-001")["integration_head"], self.parent)

    def test_candidate_rejects_receipt_tampering_and_dirty_bytes(self):
        self.checkpoint()
        sealed = next((self.run / "execution/FEAT-001/authoring-checkpoints").glob("*/sealed.json"))
        original = sealed.read_bytes()
        value = json.loads(original)
        value["tree"] = "a" * 40
        write_private(sealed, value)
        with self.assertRaisesRegex(Exception, "receipt is invalid"):
            validation.produce(self.run, "FEAT-001", None, session="deterministic-coordinator")
        sealed.write_bytes(original)
        (self.integration / "README.md").write_text("dirty\n")
        with self.assertRaisesRegex(Exception, "clean recorded"):
            validation.produce(self.run, "FEAT-001", None, session="deterministic-coordinator")

    def test_checkpoint_rejects_active_owners_and_material_contract_review(self):
        from workflow_state import read
        from workflow_refinement import settle
        from workflow_impact import settle_execution
        operation = self.run / "execution/FEAT-001/validation/operations/active-fixture.json"
        write_private(operation, {"status": "running", "target": {}})
        with self.assertRaisesRegex(Exception, "not quiescent"):
            self.checkpoint()
        operation.unlink()
        wave_path = engine.wave_path(self.run, "FEAT-001", "WAVE-001")
        wave = read(wave_path)
        write_private(wave_path, {**wave, "active_task_ids": ["TASK-001"]})
        with self.assertRaisesRegex(Exception, "outstanding workers"):
            self.checkpoint()
        write_private(wave_path, wave)
        claim = read(self.run / "objective-impact-claim.json")
        claim.update(declared_effects=["design"], declared_plan_action="replan_required")
        claim["dispositions"][0].update(disposition="changed_contract", effects=["design"])
        settle(self.run, claim)
        settle_execution(self.run, "FEAT-001", self.coordinator["plan_digest"], self.fixture.project)
        with self.assertRaisesRegex(Exception, "nonmaterial update"):
            self.checkpoint()
        self.assertEqual(engine.head(self.integration), self.parent)

    def test_cli_preserves_structured_publication_rejection_without_effects(self):
        import contextlib
        import io
        import sys
        import workflow_transition
        output = io.StringIO()
        args = ["workflow_transition.py", "checkpoint-authoring", "--run-dir", str(self.run),
                "--execution", "FEAT-001", "--operation-id", "../unsafe", "--message", "Checkpoint docs"]
        with (mock.patch.object(sys, "argv", args),
              mock.patch.object(workflow_transition, "runtime_session_identity", return_value="deterministic-coordinator"),
              contextlib.redirect_stdout(output)):
            code = workflow_transition.main()
        result = json.loads(output.getvalue())
        self.assertNotEqual(code, 0)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "SPEC_TRANSACTION_INVALID")
        self.assertEqual(engine.head(self.integration), self.parent)
        self.assertNotIn("Traceback", output.getvalue())


class WaveContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.GitLifecycleTests()
        self.addCleanup(self.fixture.doCleanups)
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
                 "execution_policy": {"observation_interval_seconds": 30, "deadline": None}, "environment_keys": []}
        with mock.patch.object(workflow_process, "_Stream", side_effect=MemoryError("fixture")), \
                mock.patch.object(workflow_process, "OwnedProcessGroup") as owner:
            with self.assertRaises(MemoryError):
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
                        "execution_policy": {"observation_interval_seconds": 30, "deadline": None}, "environment_keys": ["PATH"]}, target)
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
        def delayed(check, cwd, **options):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test evaluator release timed out")
            return original(check, cwd, **options)
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

        def delayed(check, cwd, **options):
            entered.set()
            if not release.wait(20):
                raise RuntimeError("test evaluator release timed out")
            return original(check, cwd, **options)

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
        failure = workflow_process.ProcessCleanupError("termination unproven")
        failure.observation = {"local_group_quiescent": False}
        with mock.patch.object(validation, "run_owned", side_effect=failure):
            with self.assertRaises(workflow_process.ProcessCleanupError):
                validation.produce(self.run, "FEAT-001", "WAVE-001", session="deterministic-coordinator")
        with self.assertRaisesRegex(Exception, "validation.*quiescent"):
            validation.require_quiescent(self.run)

    def test_incomplete_evaluator_stream_retains_ownership_without_receipt(self):
        self.finish_wave()
        receipts = self.run / "execution/FEAT-001/validation/receipts"
        before = set(receipts.glob("*.json"))
        observation = workflow_process.run_owned([sys.executable, "-c", "pass"])
        observation["local_group_quiescent"] = False
        observation["stdout"]["complete"] = False
        with mock.patch.object(validation, "run_owned", return_value=observation):
            with self.assertRaises(workflow_process.ProcessCleanupError):
                validation.produce(self.run, "FEAT-001", "WAVE-001", session="deterministic-coordinator")
        with self.assertRaisesRegex(Exception, "validation.*quiescent"):
            validation.require_quiescent(self.run)
        self.assertEqual(set(receipts.glob("*.json")), before)

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

        def changed(check, cwd, **options):
            result = original(check, cwd, **options)
            run = engine.read_json(self.run / "run.json")
            write_private(self.run / "run.json", {**run, "coordinator_session_sha256": "b" * 64})
            return result

        with mock.patch.object(validation, "_run_check", side_effect=changed):
            with self.assertRaises(engine.ExecutionError) as changed_owner:
                validation.produce(self.run, "FEAT-001", "WAVE-001", session="deterministic-coordinator")
            self.assertEqual(changed_owner.exception.code, "OWNERSHIP_INVALID")
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
