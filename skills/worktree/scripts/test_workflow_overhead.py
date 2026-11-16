"""Behavior and bounded-work oracles for workflow overhead changes."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import workflow_automatic as automatic
import workflow_intake as intake
import workflow_readiness as readiness
import workflow_statistics as statistics
from workflow_statistics_report import history
from workflow_state import WorkflowError
from test_workflow_statistics import event, stamp, task


class AttributedTimingTests(unittest.TestCase):
    def setUp(self):
        import workflow_timing
        self.timing = workflow_timing
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name).resolve() / "run"
        self.run.mkdir()

    def clock(self, ns, identity="a" * 64):
        return {"utc": "2026-11-15T00:00:00+00:00", "monotonic_ns": ns,
                "clock_id": identity, "scope": "machine-boot"}

    def read(self):
        return self.timing._load(self.run / "workflow-timing.json")["stages"]

    def test_malformed_telemetry_shapes_raise_only_the_declared_observation_error(self):
        from workflow_state import write
        path = self.run / "workflow-timing.json"
        malformed_clock = {**self.clock(0), "scope": []}
        row = {"count": 1, "failed": 0, "total_ms": 0, "max_ms": 0, "unknown": 1,
               "first_clock": malformed_clock, "last_clock": None}
        for value in ([], {"schema": self.timing.SCHEMA, "stages": {"tools": row}}):
            write(path, value)
            with self.assertRaises(WorkflowError):
                self.timing._load(path)

    def test_inclusive_totals_preserve_clock_identity_and_batch_writes(self):
        with mock.patch.object(self.timing, "_clock", side_effect=[self.clock(n) for n in (0, 1_000_000, 3_000_000, 5_000_000)]), \
                mock.patch.object(self.timing, "write", wraps=self.timing.write) as save:
            with self.timing.measure(self.run, "validation"):
                with self.timing.observed("tools"):
                    pass
            self.assertEqual(save.call_count, 1)
        rows = self.read()
        self.assertEqual(rows["validation"]["total_ms"], 5)
        self.assertEqual(rows["tools"]["total_ms"], 2)
        self.assertEqual(rows["tools"]["last_clock"]["clock_id"], "a" * 64)

    def test_changed_or_missing_clocks_count_unknown_instead_of_zero_duration(self):
        for ending in (None, self.clock(9_000_000, "b" * 64)):
            with mock.patch.object(self.timing, "_clock", side_effect=[self.clock(0), ending]):
                with self.timing.measure(self.run, "tools"):
                    pass
        row = self.read()["tools"]
        self.assertEqual(row["unknown"], 2)
        self.assertEqual(row["count"], 2)
        self.assertEqual(row["total_ms"], 0)  # No measured intervals, not a zero elapsed claim.

    def test_tool_failure_is_attributed_and_telemetry_failure_preserves_product_error(self):
        import sys
        import subprocess
        import worktree_operation
        with self.timing.scope(self.run):
            result = worktree_operation.run([sys.executable, "-I", "-c", "raise SystemExit(7)"])
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.read()["tools"]["failed"], 1)
        with mock.patch.object(self.timing, "write", side_effect=OSError("unavailable")):
            with self.assertRaises(subprocess.CalledProcessError) as error:
                with self.timing.scope(self.run):
                    worktree_operation.run([sys.executable, "-I", "-c", "raise SystemExit(9)"], check=True)
        self.assertEqual(error.exception.returncode, 9)
        with mock.patch.object(statistics, "safe_clock", side_effect=RuntimeError("unavailable")):
            with self.timing.measure(self.run, "tools"):
                pass
        self.assertEqual(self.read()["tools"]["unknown"], 1)

    def test_coordination_wait_measures_acquisition_not_lock_body(self):
        import workflow_state
        moment = [0]
        def tick(_seconds):
            moment[0] += 20_000_000
        with mock.patch.object(self.timing, "_clock", side_effect=lambda: self.clock(moment[0])):
            with self.timing.scope(self.run):
                with mock.patch.object(workflow_state.fcntl, "flock", side_effect=[BlockingIOError(), None, None]), \
                        mock.patch.object(workflow_state.time, "sleep", side_effect=tick):
                    with workflow_state.lock(self.run):
                        moment[0] += 100_000_000
        row = self.read()["coordination_wait"]
        self.assertEqual(row["count"], 1)
        self.assertEqual(row["total_ms"], 20)


class ProjectionWorkTests(unittest.TestCase):
    def test_history_visits_events_once_and_preserves_first_equal_clock(self):
        class Events(list):
            visits = 0

            def __iter__(self):
                for row in super().__iter__():
                    self.visits += 1
                    yield row

        events = Events()
        for index in range(40):
            running = task(f"TASK-{index:03d}", status="running", attempt=1,
                           worker_started_at=str(index), definition_sha256="first")
            events.extend([event("start_task", index * 2, [running]),
                           event("finish_task", index * 2 + 1, [{**running, "status": "done"}])])
        first = deepcopy(events[0])
        first["state"]["tasks"][0]["definition_sha256"] = "earliest"
        events.insert(0, first)
        result = history(events, stamp(100))
        self.assertEqual(result["worker_attempts"][0]["definition_sha256"], "earliest")
        self.assertLessEqual(events.visits, len(events) * 2)


class ObservationWorkTests(unittest.TestCase):
    def test_intake_does_not_duplicate_owned_aggregate_observation(self):
        @automatic._aggregation_transition
        def aggregate(_run, _session):
            return {"status": "done"}

        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(intake, "authorize_coordinator"), \
                mock.patch.object(intake, "native_session_id", return_value="test"), \
                mock.patch.object(automatic, "prepare_aggregation", aggregate), \
                mock.patch("workflow_delivery_guidance.attach", side_effect=lambda _r, v, *_a: v), \
                mock.patch.object(statistics, "safe_record") as observe, \
                mock.patch("builtins.print"):
            self.assertEqual(intake.main(["--owner", "implement", "aggregate-prepare",
                                          "--run-dir", str(Path(root).resolve())]), 0)
            self.assertEqual(observe.call_count, 1)

    def test_partial_effect_observation_survives_error_and_reporting_failure(self):
        @automatic._aggregation_transition
        def aggregate(_run):
            raise RuntimeError("cleanup failed after promotion")

        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(statistics, "safe_record", side_effect=OSError("report unavailable")) as observe:
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                aggregate(Path(root).resolve())
            observe.assert_called_once()


class ReadinessTraversalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def collect(self):
        return readiness._input_files(self.root, "FEAT-001", [], None)

    def test_directories_do_not_spend_file_allowance(self):
        evidence = self.root / "evidence/FEAT-001"
        for index in range(300):
            (evidence / str(index)).mkdir(parents=True)
        (evidence / "one.md").write_text("evidence")
        self.assertEqual(self.collect(), [evidence / "one.md"])

    def test_file_limit_and_deterministic_order(self):
        evidence = self.root / "evidence/FEAT-001"
        evidence.mkdir(parents=True)
        for index in range(256):
            (evidence / f"{index:03d}.md").write_text("evidence")
        self.assertEqual(self.collect(), sorted(evidence.iterdir()))
        (evidence / "overflow.md").touch()
        with self.assertRaisesRegex(WorkflowError, "file"):
            self.collect()

    def test_traversal_limit_counts_empty_directories_before_materialization(self):
        evidence = self.root / "evidence/FEAT-001"
        evidence.mkdir(parents=True)
        for index in range(4096):
            (evidence / str(index)).mkdir()
        self.assertEqual(self.collect(), [])
        (evidence / "overflow").mkdir()
        with self.assertRaisesRegex(WorkflowError, "traversal"):
            self.collect()

    def test_links_and_nonregular_inputs_fail_without_following(self):
        evidence = self.root / "evidence/FEAT-001"
        evidence.mkdir(parents=True)
        (evidence / "linked").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(WorkflowError):
            self.collect()
        (evidence / "linked").unlink()
        os.mkfifo(evidence / "fifo")
        with self.assertRaises(WorkflowError):
            self.collect()

    def test_plan_directory_entries_share_the_traversal_bound(self):
        plan = self.root / "plans/plan.md"
        plan.parent.mkdir()
        plan.touch()
        for index in range(4095):
            (plan.parent / f"other-{index}").touch()
        self.assertEqual(readiness._input_files(self.root, "FEAT-001", [], plan), [plan])
        (plan.parent / "overflow").touch()
        with self.assertRaisesRegex(WorkflowError, "traversal"):
            readiness._input_files(self.root, "FEAT-001", [], plan)

    def test_foreign_execution_is_not_inventoried(self):
        foreign = self.root / "evidence/FEAT-002"
        foreign.mkdir(parents=True)
        (foreign / "linked").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.collect(), [])


class InventoryWorkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "design.md"
        self.source.write_text("Service architecture\n")
        reference = {"kind": "project", "path": "design.md", "anchor": "Service",
                     "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()}
        self.candidate = {"schema": "workflow/architecture-inventory-v1", "layers": [
            {"id": "LAYER-service", "name": "Service", "origin": "observed",
             "sources": [dict(reference) for _ in range(16)]}], "components": [], "gaps": []}

    def validate(self):
        from workflow_statistics_inventory import validate
        return validate(self.candidate, self.root, self.root / "run")

    def test_duplicate_sources_read_once_plus_coherence_check(self):
        import workflow_program  # noqa: F401
        from project_specs_lib import source_bundle
        with mock.patch.object(source_bundle, "read_source", wraps=source_bundle.read_source) as read_source:
            self.validate()
            self.assertEqual(read_source.call_count, 2)

    def test_conflicting_reference_digest_is_not_hidden_by_reuse(self):
        self.candidate["layers"][0]["sources"][-1]["sha256"] = "0" * 64
        with self.assertRaises(WorkflowError):
            self.validate()

    def test_concurrent_change_and_next_call_are_not_cached(self):
        import workflow_program  # noqa: F401
        from project_specs_lib import source_bundle
        original = source_bundle.read_source

        def changing(root, relative):
            data = original(root, relative)
            self.source.write_text("Changed architecture\n")
            return data

        with mock.patch.object(source_bundle, "read_source", side_effect=changing):
            with self.assertRaises(WorkflowError):
                self.validate()
        with self.assertRaises(WorkflowError):
            self.validate()

    def test_every_reference_still_validates_its_path_and_anchor(self):
        self.candidate["layers"][0]["sources"][-1]["anchor"] = ""
        with self.assertRaises(WorkflowError):
            self.validate()


if __name__ == "__main__":
    unittest.main()
