"""Implement observations use real owners without an SDLC checkpoint."""
from pathlib import Path
import fcntl
import os
import unittest
from unittest import mock

import test_workflow_contracts as fixtures
import workflow_statistics as statistics
import workflow_execution as engine
from workflow_state import read, write, digest, lock


class ImplementStatisticsTests(unittest.TestCase):
    tearDown = fixtures.DirectExecutionTests.tearDown
    settle = fixtures.DirectExecutionTests.settle
    prepare = fixtures.DirectExecutionTests.prepare
    start = fixtures.DirectExecutionTests.start
    finish = fixtures.DirectExecutionTests.finish
    observation = fixtures.DirectExecutionTests.observation

    def setUp(self):
        fixtures.DirectExecutionTests.setUp(self)
        self.plan.chmod(0o600)
        self.plan.with_suffix(".md.lock").chmod(0o600)

    def inventory(self):
        revision = read(self.run_dir / "objective.json")["revisions"][-1]
        source = {"kind": "objective", "path": revision["snapshot"],
                  "sha256": revision["sha256"], "anchor": "accepted objective"}
        candidate = {"schema": "workflow/architecture-inventory-v1", "layers": [
            {"id": "LAYER-service", "name": "Service", "origin": "declared", "sources": [source]}],
            "components": [{"id": "COMP-files", "name": "Files", "origin": "declared",
                            "layer": "LAYER-service", "features": ["FEAT-001"], "sources": [source]}], "gaps": []}
        return statistics.inventory(self.run_dir, candidate, self.session)

    def test_initial_architecture_without_plan_and_bootstrap_observation(self):
        plan = self.plan.read_bytes()
        self.plan.unlink()
        self.plan.with_suffix(".md.lock").unlink()
        self.inventory()
        initial = self.run_dir / "statistics/initial.json"
        before = initial.read_bytes()
        report = read(initial)
        self.assertEqual(report["owner"], "implement")
        self.assertEqual(report["architecture"]["component_count"], 1)
        self.assertEqual(report["task_counts"]["known"], 0)
        self.assertEqual(report["scope_basis"], "accepted_objective")
        self.assertTrue(any("unplanned" in gap.lower() for gap in report["measurement_gaps"]))
        engine.prepare_specifications(self.run_dir, self.project, "FEAT-001")
        self.assertEqual(read(self.run_dir / "statistics/current.json")["phase"], "specification")
        self.plan.write_bytes(plan)
        self.plan.chmod(0o600)
        self.plan.with_suffix(".md.lock").write_text("locked\n")
        self.plan.with_suffix(".md.lock").chmod(0o600)
        self.prepare()
        self.inventory()
        self.assertEqual(initial.read_bytes(), before)
        report = statistics.status(self.run_dir)
        self.assertEqual(report["task_counts"]["known"], 3)
        self.assertFalse(any("application decomposition" in gap for gap in report["measurement_gaps"]))
        self.assertEqual(report["delivery"]["aggregation"], "not_applicable")

    def test_late_inventory_cannot_backdate_initial_report(self):
        engine.prepare_specifications(self.run_dir, self.project, "FEAT-001")
        self.inventory()
        self.assertFalse((self.run_dir / "statistics/initial.json").exists())
        report = statistics.status(self.run_dir)
        self.assertTrue(any("initial" in gap.lower() for gap in report["measurement_gaps"]))

    def test_bootstrap_survives_reporting_failure(self):
        with mock.patch.object(statistics, "record", side_effect=OSError("unavailable reporting")):
            result = engine.prepare_specifications(self.run_dir, self.project, "FEAT-001")
        self.assertEqual(result["status"], "specs_prepared")
        self.assertTrue(Path(result["integration_worktree"]).is_dir())

    def test_preparation_keeps_task_identities_and_status_is_read_only(self):
        self.plan.chmod(0o600)
        self.plan.with_suffix(".md.lock").chmod(0o600)
        self.inventory()
        before = read(self.run_dir / "statistics/initial.json")
        self.prepare()
        after = statistics.status(self.run_dir)
        self.assertEqual({r["id"]: r["definition_sha256"] for r in before["tasks"]},
                         {r["id"]: r["definition_sha256"] for r in after["tasks"]})
        files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.run_dir.rglob("*") if p.is_file()}
        statistics.status(self.run_dir)
        self.assertEqual(files, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files})

    def test_arm_reverifies_next_transition_and_heartbeat_does_not_scan(self):
        self.prepare()
        assignment = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        args = (self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"], assignment["assignment_digest"])
        with mock.patch.object(engine, "verify_objective_impact_execution", wraps=engine.verify_objective_impact_execution) as verify:
            engine.arm_task(*args)
            self.assertEqual(verify.call_count, 1)
            engine.arm_task(*args)
            self.assertEqual(verify.call_count, 2)
        engine.start_task(*args, "statistics-worker", Path(assignment["scope_cwd"]))
        before = len(statistics._events(self.run_dir / "statistics"))
        with mock.patch.object(statistics, "snapshot", wraps=statistics.snapshot) as snapshot:
            heartbeat = engine.heartbeat_task(*args, "implementing", "statistics-worker", Path(assignment["scope_cwd"]))
            self.assertEqual(heartbeat["status"], "ACTIVE")
            snapshot.assert_not_called()
        self.assertEqual(len(statistics._events(self.run_dir / "statistics")), before)
        # A relevant change after lock release is rejected by the next arm.
        self.plan.write_text(self.plan.read_text() + "\nchanged plan\n")
        with self.assertRaises(engine.ExecutionError):
            engine.arm_task(*args)

    def test_admission_and_refill_reuse_only_the_current_transition_basis(self):
        self.prepare()
        first, peer = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.start(first)
        self.start(peer)
        self.finish(first, admit=False)
        with mock.patch.object(engine, "verify_objective_impact_execution", wraps=engine.verify_objective_impact_execution) as verify:
            engine.admit_task(self.run_dir, "FEAT-001", "WAVE-001", first["task_id"], self.observation(first))
            self.assertEqual(verify.call_count, 1)
        with mock.patch.object(engine, "verify_objective_impact_execution", wraps=engine.verify_objective_impact_execution) as verify:
            replacement = engine.refill_wave(self.run_dir, "FEAT-001", "WAVE-001")
            # One entry check plus a fresh verification after Git allocation.
            self.assertEqual(verify.call_count, 2)
        self.assertEqual([r["task_id"] for r in replacement], ["TASK-003"])
        self.assertEqual(read(engine.task_path(self.run_dir, "FEAT-001", "WAVE-001", peer["task_id"]))["status"], "running")

    def test_terminated_worker_keeps_reserved_slot_without_counting_as_running(self):
        self.prepare()
        assignment = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        args = (self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"], assignment["assignment_digest"])
        engine.arm_task(*args)
        engine.start_task(*args, "statistics-worker", Path(assignment["scope_cwd"]))
        report = statistics.status(self.run_dir)
        self.assertEqual(report["task_counts"]["running"], 1)
        write(self.run_dir / "execution/FEAT-001/terminations/observed.json", {
            "schema": "workflow/worker-termination-v1", "status": "terminated", "assignment_digest": assignment["assignment_digest"],
            "worker_session_hash": engine._session_hash("statistics-worker"),
            "observer_session_hash": read(self.run_dir / "run.json")["coordinator_session_sha256"],
            "native_handle": "test-worker", "observation": "native exit observed"})
        report = statistics.status(self.run_dir)
        self.assertEqual(report["task_counts"]["running"], 0)
        self.assertEqual(sum(r["active_slots"] for r in report["executions"]), 2)
        self.assertEqual(report["task_counts"]["accepted"], 0)
        receipt = self.run_dir / "execution/FEAT-001/terminations/observed.json"
        valid = read(receipt)
        for field in ("schema", "native_handle", "observation", "observer_session_hash"):
            write(receipt, {key: value for key, value in valid.items() if key != field})
            self.assertEqual(statistics.status(self.run_dir)["task_counts"]["running"], 1)

    def test_basis_reuses_context_only_before_effects_and_standalone_calls_revalidate(self):
        import workflow_program as program
        import workflow_impact as impact
        self.prepare()
        coordinator = engine._load_coordinator(self.run_dir, "FEAT-001")
        project = impact.spec_checkout(self.run_dir, self.project, execution_id="FEAT-001")
        args = (self.run_dir, "FEAT-001", coordinator["plan_digest"], self.project)
        with mock.patch.object(program, "accepted", wraps=program.accepted) as accepted:
            before = impact.verify_execution(*args)
            self.assertEqual(accepted.call_count, 0)
            self.assertEqual(impact.settle_execution(*args), before)
            self.assertEqual(accepted.call_count, 1)  # input archival can write
            self.assertIsNone(program.slice_basis(self.run_dir, project, "FEAT-001"))
            self.assertEqual(accepted.call_count, 2)

    def test_shared_invariants_are_part_of_immutable_basis_identity(self):
        import workflow_impact as impact
        self.prepare()
        coordinator = engine._load_coordinator(self.run_dir, "FEAT-001")
        args = (self.run_dir, "FEAT-001", coordinator["plan_digest"], self.project)
        basis = impact.verify_execution(*args)
        self.assertFalse(any("benchmark_workflow_overhead.py" in path for path in basis["identities"]))
        original = Path.read_bytes
        def changed(path):
            data = original(path)
            return data + b"\nchanged rule\n" if path.name == "workflow-invariants.md" else data
        with mock.patch.object(Path, "read_bytes", changed), self.assertRaises(impact.ObjectiveImpactError):
            impact.verify_execution(*args)

    def test_status_is_unavailable_during_aggregation_without_writes(self):
        root = self.run_dir / "aggregation/validation-lock"
        with lock(root):
            pass
        fd = os.open(root / "transition.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            before = {p: p.read_bytes() for p in self.run_dir.rglob("*") if p.is_file()}
            self.assertEqual(statistics.status(self.run_dir)["status"], "unavailable")
            self.assertEqual(before, {p: p.read_bytes() for p in before})
        finally:
            os.close(fd)


class DeliveryProjectionTests(unittest.TestCase):
    def test_correction_replaces_old_candidate_phase_and_preserves_failure(self):
        from workflow_statistics_delivery import delivery, implement_phase
        run = {"run_id": "run-fixture", "automatic_outer": True, "corrective_execution": "FEAT-002"}
        candidate = {"candidate_head": "old", "source_head": "source", "child_head": "child"}
        target = {"head": "old", "source_head": "source", "child_head": "child"}
        values = {"run.json": run, "aggregation/candidate.json": candidate,
                  "aggregation/corrections/FEAT-002.json": {"schema": "workflow/aggregation-correction-v1",
                      "run_id": run["run_id"], "execution_id": "FEAT-002", "status": "ready", "target": target}}
        old = {"id": "FEAT-001", "status": "done", "promoted": True, "cleanup": "complete"}
        self.assertEqual(implement_phase(values, [old], delivery(run, values, [old]), True), "recovery-planning")
        fresh = {"id": "FEAT-002", "status": "tdd_sealed", "promoted": False, "cleanup": "pending",
                 "waves": [], "active_wave": None}
        values["execution/FEAT-002/coordinator.json"] = {"status": "tdd_sealed"}
        self.assertEqual(implement_phase(values, [old, fresh], delivery(run, values, [old, fresh]), True), "execution")
        fresh["status"] = "done"
        values["execution/FEAT-002/coordinator.json"] = {"status": "done", "promoted_head": "new-child"}
        candidate.update(candidate_head="new", child_head="new-child")
        values["aggregation/operations/test.json"] = {"target": {"head": "new", "source_head": "source", "child_head": "new-child"},
                                                       "status": "verified", "outcome": "failed"}
        report = delivery(run, values, [old, fresh])
        self.assertEqual(report["validation"], "failed")
        self.assertEqual(implement_phase(values, [old, fresh], report, True), "aggregation-validation")

    def test_reporting_import_failure_preserves_product_result_and_exception(self):
        import builtins
        from workflow_automatic import _aggregation_transition
        import tempfile
        original = builtins.__import__

        def failing(name, *args, **kwargs):
            if name == "workflow_statistics":
                raise ImportError("unavailable observer")
            return original(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(builtins, "__import__", side_effect=failing):
            self.assertEqual(_aggregation_transition(lambda _run: "delivered", include_delivery=False)(Path(folder).resolve()), "delivered")
            self.assertEqual(engine._serialized_transition(lambda _run: "prepared")(Path(folder).resolve()), "prepared")
            def failed(_run):
                raise ValueError("product failure")
            with self.assertRaisesRegex(ValueError, "product failure"):
                _aggregation_transition(failed)(Path(folder).resolve())

    def test_aggregation_requires_delivery_receipt_and_separate_cleanup(self):
        from workflow_statistics_delivery import delivery
        run = {"run_id": "run-fixture", "automatic_outer": True}
        executions = [{"promoted": True, "cleanup": "complete"}]
        self.assertEqual(delivery(run, {}, executions)["aggregation"], "pending")
        validation = {"target": {"head": "a" * 40}, "status": "verified", "outcome": "passed"}
        receipt = {"schema": "workflow/automatic-delivery-v1", "run_id": run["run_id"],
                   "target": validation["target"], "validation_sha256": digest(validation), "cleanup": None}
        values = {"aggregation/delivery.json": receipt, "aggregation/operations/test.json": validation}
        self.assertEqual(delivery(run, values, executions)["cleanup"], "pending")
        receipt["cleanup"] = "REMOVED"
        self.assertEqual(delivery(run, values, executions)["cleanup"], "recorded")
        validation["outcome"] = "failed"
        with self.assertRaises(Exception):
            delivery(run, values, executions)


if __name__ == "__main__":
    unittest.main()
