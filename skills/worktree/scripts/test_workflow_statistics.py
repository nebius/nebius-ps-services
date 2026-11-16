"""Statistics are observations, not acceptance, scheduling or duration predictions."""
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import workflow_statistics as statistics
from workflow_statistics_report import chat, elapsed, history, project
from workflow_statistics_inventory import summarize
from workflow_state import read, write, lock


def stamp(ns, identity="boot-a", utc="2026-11-13T00:00:00.000000+00:00"):
    return {"utc": utc, "monotonic_ns": ns, "clock_id": identity, "scope": "machine-boot"}


def state(tasks=()):
    return {"run_id": "run-test", "owner": "agentic-sdlc", "scope_basis": "application",
            "delivery": {"aggregation": "not_applicable"}, "objective": {"revision": "r0001", "sha256": "a" * 64,
            "outcome": "Deliver the service", "scope": ["service"]}, "phase": "implementation", "status": "running",
            "architecture": None, "tasks": list(tasks), "executions": [], "limits": {},
            "undecomposed_slices": ["SLICE-002"], "gaps": []}


def task(identity="TASK-001", **updates):
    return {"id": "FEAT-001/" + identity, "name": "Create API", "component": "COMP-api",
            "touched_components": ["COMP-db"], "definition_sha256": "d" * 64, "status": "planned",
            "accepted": False, "integrated": False, "attempt": 0, "dispatched_at": None,
            "worker_started_at": None, **updates}


def event(action, ns, tasks=(), **updates):
    return {"event": action, "clock": stamp(ns), "state": {**state(tasks), **updates}}


class ProjectionTests(unittest.TestCase):
    def test_ownerless_history_is_unavailable_without_conversion(self):
        current = state()
        prior = {key: value for key, value in current.items() if key != "owner"}
        with self.assertRaisesRegex(ValueError, "owner history"):
            project([], prior, stamp(10))
        with self.assertRaisesRegex(ValueError, "owner history"):
            project([{"state": prior}], current, stamp(10))

    def test_retirement_summary_preserves_results_without_counting_replacements_twice(self):
        old = task(status="finished", accepted=True, disposition="superseded")
        other = task("TASK-002", status="superseded", disposition="superseded")
        current = task(definition_sha256="e" * 64)
        report = project([], {**state([current]), "retired_tasks": [old, other]}, stamp(20))
        self.assertEqual(report["logical_task_count"], 2)
        self.assertEqual(report["task_counts"]["known"], 1)
        self.assertEqual(report["task_counts"]["accepted"], 0)
        self.assertEqual(report["retired_task_counts"], {"known": 2, "accepted": 1, "integrated": 0})
        self.assertIn("2 known logical tasks", chat(report))
        self.assertIn("2 retired", chat(report))
        self.assertIn("1 accepted before retirement", chat(report))

    def test_component_counts_and_submission_are_not_acceptance(self):
        report = project([], state([task(status="done"), task("TASK-002", accepted=True)]), stamp(20))
        self.assertEqual(report["task_counts"]["known"], 2)
        self.assertEqual(report["task_counts"]["accepted"], 1)
        self.assertEqual(report["task_counts"]["integrated"], 0)
        self.assertEqual(report["component_tasks"], {"COMP-api": ["FEAT-001/TASK-001", "FEAT-001/TASK-002"]})
        self.assertIsNone(report["timing"]["workflow_elapsed_ns"])

    def test_prestart_retry_worker_recovery_and_observed_overlap(self):
        first = task(status="assigned", dispatched_at="first")
        second = task("TASK-002", status="assigned", dispatched_at="second")
        events = [event("arm_task", 0, [first]), event("requeue_task", 100, [task(status="assigned")]),
                  event("arm_task", 200, [first]),
                  event("start_task", 300, [{**first, "attempt": 1, "status": "running", "worker_started_at": "a"}]),
                  event("arm_task", 350, [{**first, "attempt": 1, "status": "running", "worker_started_at": "a"}, second]),
                  event("start_task", 400, [{**first, "attempt": 1, "status": "running", "worker_started_at": "a"},
                                            {**second, "attempt": 1, "status": "running", "worker_started_at": "b"}]),
                  event("finish_task", 700, [{**first, "attempt": 1, "status": "done", "worker_started_at": "a"},
                                             {**second, "attempt": 1, "status": "running", "worker_started_at": "b"}]),
                  event("finish_task", 800, [{**first, "attempt": 1, "status": "done", "worker_started_at": "a"},
                                             {**second, "attempt": 1, "status": "done", "worker_started_at": "b"}])]
        result = project(events, events[-1]["state"], stamp(800))
        self.assertEqual(result["timing"]["recorded_overlap_ns"], 300)
        self.assertEqual(result["timing"]["recorded_task_time_ns"], 800)
        self.assertEqual(result["timing"]["recorded_union_ns"], 500)
        self.assertEqual(result["timing"]["peak_observed_workers"], 2)
        self.assertEqual([r["outcome"] for r in result["timing"]["launch_attempts"]], ["prestart_failed", "started", "started"])
        self.assertEqual(result["timing"]["worker_attempts"][-1]["outcome"], "done")

    def test_recovered_attempt_has_a_start_but_old_termination_is_unknown(self):
        first = task(status="running", attempt=1, worker_started_at="a", dispatched_at="a")
        second = {**first, "attempt": 2, "worker_started_at": "b", "dispatched_at": "b"}
        events = [event("start_task", 10, [first]), event("recover_task", 30, [second]),
                  event("finish_task", 50, [{**second, "status": "done"}])]
        attempts = history(events, stamp(50))["worker_attempts"]
        self.assertIsNone(attempts[0]["elapsed_ns"])
        self.assertEqual(attempts[1]["elapsed_ns"], 20)

    def test_observed_peak_survives_process_clocks_and_zero_length_recovery(self):
        first = task(status="running", attempt=1, worker_started_at="a")
        peer = task("TASK-002", status="running", attempt=1, worker_started_at="b")
        blocked = {**first, "status": "blocked"}
        blocked_peer = {**peer, "status": "blocked"}
        recovered = {**first, "attempt": 2, "worker_started_at": "c"}
        events = [event("start_task", 10, [first]),
                  event("start_task", 20, [first, peer]),
                  event("worker_result", 30, [blocked, peer]),
                  event("worker_result", 40, [blocked, blocked_peer]),
                  event("recover_task", 50, [recovered, blocked_peer])]
        for index, row in enumerate(events):
            row["clock"] = {**row["clock"], "clock_id": f"process-{index}", "scope": "process-only"}
        for observed in (events[-1]["clock"], stamp(60, identity="status-process")):
            with self.subTest(clock=observed["clock_id"]):
                report = project(events, events[-1]["state"], observed)
                self.assertEqual(report["timing"]["peak_observed_workers"], 2)
                self.assertFalse(report["timing"]["complete"])
                for name in ("recorded_task_time_ns", "recorded_union_ns", "recorded_overlap_ns"):
                    self.assertIsNone(report["timing"][name])
                self.assertEqual(len(report["timing"]["worker_attempts"]), 3)

    def test_complete_zero_length_attempt_keeps_measured_zero_and_observed_worker(self):
        running = task(status="running", attempt=1, worker_started_at="a")
        events = [event("start_task", 10, [running]),
                  event("worker_result", 10, [{**running, "status": "submitted"}])]
        report = project(events, events[-1]["state"], stamp(10))
        self.assertEqual(report["timing"]["peak_observed_workers"], 1)
        self.assertTrue(report["timing"]["complete"])
        for name in ("recorded_task_time_ns", "recorded_union_ns", "recorded_overlap_ns"):
            self.assertEqual(report["timing"][name], 0)

    def test_clocks_restart_wall_clock_change_and_subsecond(self):
        self.assertEqual(elapsed(stamp(10), stamp(20, utc="2000-01-01T00:00:00Z")), 10)
        self.assertIsNone(elapsed(stamp(10), stamp(20, identity="reboot")))
        self.assertIsNone(elapsed(stamp(20), stamp(10)))
        running = task(status="running", attempt=1, worker_started_at="a")
        events = [event("start_task", 10, [running]), event("finish_task", 500_000, [{**running, "status": "done"}])]
        self.assertEqual(history(events, stamp(500_000))["worker_attempts"][0]["elapsed_ns"], 499_990)
        events[-1]["clock"]["clock_id"] = "reboot"
        self.assertIsNone(history(events, stamp(500_000))["worker_attempts"][0]["elapsed_ns"])

    def test_pause_phase_scope_change_and_interrupted_intervals(self):
        initial = state([task()])
        running = task(status="running", attempt=1, worker_started_at="a")
        events = [event("start_task", 10, [running]), event("pause", 20, [running], status="paused"),
                  event("resume", 50, [running]), event("phase", 60, [running], phase="validation"),
                  event("phase", 100, [running, task("TASK-002")], status="blocked")]
        report = project(events, events[-1]["state"], stamp(100), initial=initial)
        self.assertEqual(report["timing"]["recorded_pause_ns"], 30)
        self.assertEqual(report["scope_changes"]["added_tasks"], ["FEAT-001/TASK-002"])
        self.assertEqual(report["report_kind"], "terminal")
        self.assertEqual(report["completeness"], "partial")
        self.assertIsNone(report["timing"]["worker_attempts"][0]["elapsed_ns"])

    def test_inventory_survives_no_plans_and_preserves_disagreements(self):
        inventory = {"layers": [{"id": "LAYER-service", "name": "Service", "origin": "declared"}],
                     "components": [{"id": "COMP-api", "name": "New API", "layer": "LAYER-service", "origin": "declared",
                                     "features": ["FEAT-002", "FEAT-001"]},
                                    {"id": "COMP-api", "name": "Existing API", "layer": "LAYER-service", "origin": "observed",
                                     "features": ["FEAT-003", "FEAT-002"]}],
                     "gaps": []}
        result = summarize(inventory)
        self.assertEqual(result["component_count"], 1)
        self.assertEqual(result["disagreements"], ["COMP-api"])
        for rows in (inventory["components"], list(reversed(inventory["components"]))):
            with self.subTest(origins=[row["origin"] for row in rows]):
                report = project([], {**state(), "architecture": {**inventory, "components": rows}}, stamp(10))
                self.assertEqual(report["component_features"], {"COMP-api": ["FEAT-001", "FEAT-002", "FEAT-003"]})
                self.assertEqual(report["task_counts"]["known"], 0)
                self.assertEqual(report["architecture"]["component_count"], 1)
                self.assertEqual(report["architecture"]["components"], rows)
                self.assertEqual(report["architecture"]["disagreements"], ["COMP-api"])
                self.assertEqual(report["undecomposed_slices"], ["SLICE-002"])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "project/run-test"
        from workflow_objectives import _revision, RUN_SCHEMA, OBJECTIVE_SCHEMA
        write(self.run / "run.json", {"schema": RUN_SCHEMA, "owner": "agentic-sdlc", "run_id": self.run.name, "status": "accepted"})
        objective = {"schema": OBJECTIVE_SCHEMA, "owner": "agentic-sdlc", "run_id": self.run.name, "revisions": []}
        _revision(self.run, objective, {"outcome": "Deliver service", "scope": ["service"], "constraints": [],
                  "acceptance_criteria": ["Works"], "clarifications": [],
                  "provenance": {"kind": "direct_request", "source_sha256": "a" * 64}})
        with lock(self.run.parent):
            pass

    def tree(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*") if p.is_file()}

    def test_initial_immutable_dedup_and_status_never_writes(self):
        first = statistics.record(self.run, "initialize", initial=True)
        baseline = (self.run / "statistics/initial.json").read_bytes()
        self.assertEqual(statistics.record(self.run, "initialize", initial=True)["status"], "UNCHANGED")
        before = self.tree()
        self.assertEqual(statistics.status(self.run)["task_counts"]["known"], 0)
        self.assertEqual(before, self.tree())
        write(self.run / "run.json", {**read(self.run / "run.json"), "status": "cancelled"})
        statistics.record(self.run, "cancel")
        self.assertEqual(baseline, (self.run / "statistics/initial.json").read_bytes())
        self.assertEqual(len(list((self.run / "statistics/events").glob("*.json"))), 2)
        self.assertEqual(len(list((self.run / "statistics/terminal").glob("*.json"))), 1)
        self.assertEqual(first["status"], "RECORDED")

    def test_concurrent_snapshot_and_missing_lock_are_unavailable_without_writes(self):
        fd = os.open(self.run.parent / "transition.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            before = self.tree()
            self.assertEqual(statistics.status(self.run)["status"], "unavailable")
            self.assertEqual(before, self.tree())
        finally:
            os.close(fd)
        (self.run.parent / "transition.lock").unlink()
        self.assertEqual(statistics.status(self.run)["status"], "unavailable")
        self.assertFalse((self.run.parent / "transition.lock").exists())

    def test_reporting_failure_recovers_without_duplicate_history(self):
        with mock.patch.object(statistics, "_report", side_effect=OSError("disk error")):
            self.assertEqual(statistics.safe_record(self.run, "accepted")["status"], "UNAVAILABLE")
        self.assertEqual(statistics.record(self.run, "accepted")["status"], "RECORDED")
        self.assertEqual(len(list((self.run / "statistics/events").glob("*.json"))), 1)

    def test_interrupted_initial_report_uses_original_observation(self):
        with mock.patch.object(statistics, "_report", side_effect=OSError("disk error")):
            statistics.safe_record(self.run, "initialize", initial=True)
        write(self.run / "run.json", {**read(self.run / "run.json"), "status": "cancelled"})
        statistics.record(self.run, "cancel", initial=True)
        self.assertEqual(read(self.run / "statistics/initial.json")["status"], "accepted")
        self.assertEqual(read(self.run / "statistics/current.json")["status"], "cancelled")

    def test_initial_binding_crash_recovers_from_event_intent(self):
        original = statistics.write

        def interrupted(path, *args, **kwargs):
            if path.name == "initial-state.json":
                raise OSError("interrupted pointer publication")
            return original(path, *args, **kwargs)

        with mock.patch.object(statistics, "write", side_effect=interrupted):
            statistics.safe_record(self.run, "initialize", initial=True)
        write(self.run / "run.json", {**read(self.run / "run.json"), "status": "cancelled"})
        statistics.record(self.run, "cancel")
        self.assertEqual(read(self.run / "statistics/initial.json")["status"], "accepted")

    def test_malformed_supplemental_counters_cannot_leak_extra_fields(self):
        write(self.run / "workflow-timing.json", {"schema": "workflow/timing-v2", "stages": {},
                                                 "private": "https://private.internal/secret"})
        statistics.record(self.run, "accepted")
        self.assertNotIn("private.internal", (self.run / "statistics/current.json").read_text())

    def test_unsafe_or_downstream_statistics_cannot_initialize(self):
        statistics.record(self.run, "accepted")
        statistics.admit_initial_storage(self.run)
        write(self.run / "statistics/unknown.json", {})
        with self.assertRaises(Exception):
            statistics.admit_initial_storage(self.run)

    def test_source_inventory_is_bound_and_rejects_stale_or_private_values(self):
        import workflow_program  # noqa: F401 -- canonical source reader setup
        from workflow_statistics_inventory import validate
        source = self.root / "design.md"
        source.write_text("An API service\n")
        reference = {"kind": "project", "path": "design.md", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "anchor": "API"}
        candidate = {"schema": "workflow/architecture-inventory-v1", "layers": [
            {"id": "LAYER-service", "name": "Service", "origin": "declared", "sources": [reference]}],
            "components": [{"id": "COMP-api", "name": "API", "origin": "declared", "layer": "LAYER-service",
                            "features": ["FEAT-001"], "sources": [reference]}], "gaps": []}
        self.assertEqual(validate(candidate, self.root, self.run)["components"][0]["name"], "API")
        invalid = deepcopy(candidate)
        invalid["components"][0]["name"] = "https://private.internal/api"
        with self.assertRaises(Exception):
            validate(invalid, self.root, self.run)
        source.write_text("Changed service\n")
        with self.assertRaises(Exception):
            validate(candidate, self.root, self.run)


import test_workflow_execution as fixture  # noqa: E402


class OwnerTests(unittest.TestCase):
    tearDown = fixture.GitLifecycleTests.tearDown
    prepare = fixture.GitLifecycleTests.prepare
    start_assignment = fixture.GitLifecycleTests.start_assignment
    result_fixture = fixture.WorkerResultContractCases.result_fixture
    result_start = fixture.WorkerResultContractCases.result_start
    result_session = fixture.WorkerResultContractCases.result_session
    result_observation = fixture.WorkerResultContractCases.result_observation
    result_payload = fixture.WorkerResultContractCases.result_payload
    result_ingest = fixture.WorkerResultContractCases.result_ingest
    result_classify_blocked = fixture.WorkerResultContractCases.result_classify_blocked

    def setUp(self):
        fixture.GitLifecycleTests.setUp(self)
        self.plan.chmod(0o600)
        self.plan.with_suffix(".md.lock").chmod(0o600)

    def test_unchanged_locked_plan_retains_task_identity_through_preparation(self):
        statistics.record(self.run_dir, "plan_locked", initial=True)
        baseline_path = self.run_dir / "statistics/initial.json"
        baseline_bytes = baseline_path.read_bytes()
        before = read(baseline_path)
        self.assertEqual(before["task_counts"]["known"], 3)

        self.prepare()

        after = read(self.run_dir / "statistics/current.json")
        self.assertEqual(after["task_counts"]["known"], 3)
        self.assertEqual({row["id"]: row["definition_sha256"] for row in before["tasks"]},
                         {row["id"]: row["definition_sha256"] for row in after["tasks"]})
        for key in ("added_tasks", "removed_tasks", "changed_tasks"):
            self.assertEqual(after["scope_changes"][key], [])
        self.assertEqual(baseline_path.read_bytes(), baseline_bytes)

    def test_standalone_cli_loads_runtime_and_records_objective_inventory(self):
        revision = read(self.run_dir / "objective.json")["revisions"][-1]
        reference = {"kind": "objective", "path": revision["snapshot"],
                     "sha256": revision["sha256"], "anchor": "accepted objective"}
        candidate = {"schema": "workflow/architecture-inventory-v1", "layers": [
            {"id": "LAYER-service", "name": "Service", "origin": "declared", "sources": [reference]}],
            "components": [{"id": "COMP-api", "name": "API", "origin": "declared", "layer": "LAYER-service",
                            "features": [], "sources": [reference]}], "gaps": []}
        path = self.root / "architecture.json"
        write(path, candidate)
        environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        environment.update(SKILLS_AGENT="codex", CODEX_THREAD_ID="deterministic-coordinator")
        script = Path(__file__).with_name("workflow_statistics.py").resolve()
        owners = {p: p.read_bytes() for p in (self.run_dir / "run.json", self.run_dir / "objective.json")}
        for action in ("inventory", "checkpoint"):
            arguments = [sys.executable, "-E", "-B", str(script), action, "--run-dir", str(self.run_dir)]
            if action == "inventory":
                arguments += ["--candidate", str(path)]
            result = subprocess.run(arguments, cwd=self.root, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            outcome = json.loads(result.stdout)
            self.assertIn(outcome["status"], {"RECORDED", "UNCHANGED"})
        stored = read(self.run_dir / "statistics/inventory.json")
        self.assertEqual({key: stored[key] for key in candidate}, candidate)
        self.assertRegex(stored["source_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(read(self.run_dir / "statistics/current.json")["architecture"]["component_count"], 1)
        self.assertEqual(owners, {p: p.read_bytes() for p in owners})

    def test_owner_overlap_acceptance_and_serial_barrier(self):
        self.prepare()
        fixture.seal_tdd_base(self.run_dir, "FEAT-001", "test: base")
        assignments = fixture.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        for assignment in assignments:
            self.start_assignment(assignment, "WAVE-001")
        running = statistics.status(self.run_dir)
        self.assertEqual(running["task_counts"]["running"], 2)
        self.assertEqual(running["timing"]["peak_observed_workers"], 2)
        self.assertGreater(running["timing"]["recorded_overlap_ns"], 0)
        with self.assertRaises(fixture.ExecutionError):
            fixture.prepare_wave(self.run_dir, "FEAT-001", "WAVE-002")
        for assignment in assignments:
            fixture.finish_task(self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"],
                                "already satisfied", "review passed", "", summary="No change needed",
                                status="verified_no_change")
        events = statistics._events(self.run_dir / "statistics")
        submitted = next(r for r in events if r["event"] == "finish_task")
        submitted_task = next(r for r in submitted["state"]["tasks"] if r["task_id"] == assignments[0]["task_id"])
        self.assertFalse(submitted_task["accepted"])
        current = statistics.status(self.run_dir)
        self.assertEqual(current["task_counts"]["accepted"], 2)
        self.assertEqual(current["task_counts"]["integrated"], 0)
        fixture.integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(statistics.status(self.run_dir)["task_counts"]["integrated"], 2)

    def test_blocked_result_is_not_a_running_worker_or_an_accepted_task(self):
        first, _ = self.result_fixture()
        receipt, _, _ = self.result_ingest(first, "blocked")
        report = read(self.run_dir / "statistics/current.json")
        self.assertEqual(report["task_counts"]["running"], 0)
        self.assertEqual(report["task_counts"]["blocked"], 1)
        self.assertEqual(report["task_counts"]["accepted"], 0)
        row = next(t for t in report["tasks"] if t["task_id"] == first["task_id"])
        self.assertEqual(row["owner_status"], "running")
        self.assertEqual(row["worker_result"]["receipt_sha256"], read(receipt)["receipt_sha256"])
        self.assertEqual(report["executions"][0]["active_slots"], 2)
        attempt = report["timing"]["worker_attempts"][0]
        self.assertEqual(attempt["outcome"], "blocked")
        self.assertIsNotNone(attempt["end"])
        self.assertIsNotNone(attempt["elapsed_ns"])

    def test_counted_repair_dispatch_is_visible_before_worker_recovery(self):
        first, _ = self.result_fixture()
        receipt, _, _ = self.result_ingest(first, "blocked")
        attempt = self.result_classify_blocked(receipt)
        before = {p: p.read_bytes() for p in (self.run_dir / "repairs").rglob("*.json")}
        report = statistics.status(self.run_dir)
        self.assertEqual(report["repair_dispatches"][0]["dispatch_id"], attempt["dispatch_id"])
        self.assertEqual(report["repair_controls"][0]["feature_dispatches"], 1)
        self.assertEqual(report["timing"]["recovery_operations"], [])
        self.assertIn("1 counted repair dispatch", chat(report))
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        dispatch = self.run_dir / "repairs/FEAT-001/dispatches" / (attempt["dispatch_id"] + ".json")
        tampered = read(dispatch)
        tampered["ordinal"] += 1
        write(dispatch, tampered)
        self.assertEqual(statistics.status(self.run_dir)["status"], "unavailable")

    def test_submitted_result_is_distinct_from_acceptance_and_integration(self):
        first, _ = self.result_fixture()
        self.result_ingest(first)
        report = read(self.run_dir / "statistics/current.json")
        self.assertEqual(report["task_counts"]["running"], 0)
        self.assertEqual(report["task_counts"]["submitted"], 1)
        self.assertEqual(report["task_counts"]["accepted"], 0)
        self.assertEqual(report["task_counts"]["integrated"], 0)

    def test_result_reporting_failure_cannot_reverse_ingestion(self):
        from workflow_worker_results import read_receipt
        first, _ = self.result_fixture()
        path = fixture.task_path(self.run_dir, "FEAT-001", "WAVE-001", first["task_id"])
        before = path.read_bytes()
        with mock.patch.object(statistics, "record", side_effect=OSError("disk full")):
            _, _, result = self.result_ingest(first, "blocked")
        _, receipt = read_receipt(first, read(path))
        self.assertEqual(receipt["receipt_sha256"], result["receipt_sha256"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(statistics.status(self.run_dir)["task_counts"]["blocked"], 1)

    def test_rejected_replacement_plan_does_not_inflate_scope(self):
        self.prepare()
        before = statistics.snapshot(self.run_dir)
        candidate = self.plan.with_name("FEAT-001.plan.v2.md")
        candidate.write_text(fixture.PLAN.replace("Plan v1", "Plan v2") + "\n### TASK-004\n\n" +
                             fixture.PLAN.split("### TASK-001", 1)[1].split("### TASK-002", 1)[0])
        candidate.chmod(0o600)
        candidate.with_suffix(".md.lock").write_text("locked\n")
        candidate.with_suffix(".md.lock").chmod(0o600)
        self.assertEqual(statistics.snapshot(self.run_dir)["tasks"], before["tasks"])
        self.assertIsNotNone(before["executions"][0]["basis_sha256"])

    def test_superseded_wave_is_historical_not_remaining_scope(self):
        from workflow_objectives import fence
        from workflow_reconcile import retire_fenced_waves
        self.prepare()
        fixture.seal_tdd_base(self.run_dir, "FEAT-001", "test: base")
        for assignment in fixture.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001"):
            self.start_assignment(assignment, "WAVE-001")
            fixture.finish_task(self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"],
                                "already satisfied", "review passed", "", summary="No change needed",
                                status="verified_no_change")
        fence(self.run_dir, "deterministic-coordinator", "steering")
        retire_fenced_waves(self.run_dir, "deterministic-coordinator")
        current = statistics.snapshot(self.run_dir)
        self.assertEqual(len(current["retired_tasks"]), 2)
        self.assertEqual([r["task_id"] for r in current["tasks"]], ["TASK-003"])
        self.assertEqual(current["executions"][0]["available_slots"], 0)
        self.assertIn("workflow_dispatch_fenced", current["executions"][0]["idle_reasons"])

    def test_reporting_storage_failure_preserves_readiness_and_owner_outcome(self):
        from workflow_readiness import require
        self.prepare()
        readiness = require(self.run_dir, "FEAT-001", self.plan)
        with mock.patch.object(statistics, "record", side_effect=OSError("disk full")):
            fixture.seal_tdd_base(self.run_dir, "FEAT-001", "test: base")
        self.assertEqual(require(self.run_dir, "FEAT-001", self.plan), readiness)
        self.assertEqual(fixture.execution_core.read_json(fixture.coordinator_path(self.run_dir, "FEAT-001"))["status"], "tdd_sealed")


if __name__ == "__main__":
    unittest.main()
