"""Independent reporting qualification and sanitized cleanup retention."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worktree/scripts"))
from workflow_state import read, write, lock  # noqa: E402
from workflow_objectives import _revision, RUN_SCHEMA, OBJECTIVE_SCHEMA  # noqa: E402
import workflow_statistics as product  # noqa: E402
import three_tier_statistics as observer  # noqa: E402


class StatisticsEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.run = self.root / "owned/private/native/sdlc-runs/project/run-test"
        self.state = {"verification_id": "a" * 32, "run_root": str(self.root / "owned"),
                      "evidence_root": str(self.root / "owned/evidence"),
                      "report_path": str(self.root / "reports/trial/report.md")}
        write(self.run / "run.json", {"schema": RUN_SCHEMA, "owner": "agentic-sdlc", "run_id": self.run.name, "status": "accepted"})
        objective = {"schema": OBJECTIVE_SCHEMA, "owner": "agentic-sdlc", "run_id": self.run.name, "revisions": []}
        _revision(self.run, objective, {"outcome": "Deliver service", "scope": ["service"], "constraints": [],
                  "acceptance_criteria": ["Works"], "clarifications": [],
                  "provenance": {"kind": "direct_request", "source_sha256": "a" * 64}})
        with lock(self.run.parent):
            pass
        patch = mock.patch.object(observer.three_tier_target, "cleanup_registration", return_value=(None, None, None, None, self.run))
        patch.start()
        self.addCleanup(patch.stop)
        product.record(self.run, "accepted", started=product.clock(), initial=True)

    def test_missing_architecture_and_terminal_evidence_never_qualify(self):
        before = {p: p.read_bytes() for p in self.run.rglob("*") if p.is_file()}
        receipt = observer.observe(self.state, "sdlc-start")
        self.assertEqual(receipt["status"], "UNAVAILABLE")
        self.assertEqual(before, {p: p.read_bytes() for p in self.run.rglob("*") if p.is_file()})
        with self.assertRaises(observer.StatisticsEvidenceError):
            observer.require_complete(self.state)

    def test_retention_preserves_reports_outside_disposable_home(self):
        summary = observer.archive(self.state)
        self.assertEqual(summary["status"], "PASS")
        saved = Path(self.state["report_path"]).parent / "statistics/initial.json"
        self.assertEqual(read(saved), read(self.run / "statistics/initial.json"))
        self.assertFalse(saved.is_relative_to(Path(self.state["run_root"])))
        self.assertTrue(saved.with_suffix(".md").is_file())
        self.assertEqual(observer.archive(self.state)["retained"], summary["retained"])

    def test_unsafe_reports_are_not_archived_and_preserve_source_for_review(self):
        report = read(self.run / "statistics/initial.json")
        report["private"] = "https://private.internal/secret"
        write(self.run / "statistics/initial.json", report)
        summary = observer.archive(self.state)
        self.assertEqual(summary["status"], "PARTIAL")
        self.assertTrue(summary["retention_pending"])
        self.assertFalse((Path(self.state["report_path"]).parent / "statistics/initial.json").exists())

    def test_handwritten_success_without_owner_history_is_rejected(self):
        for category in ("before", "during", "after"):
            receipt = {"schema": "agentic-sdlc/statistics-observation-v1", "verification_id": self.state["verification_id"],
                       "phase": "complete", "category": category, "status": "PASS", "run_id": self.run.name,
                       "initial_sha256": "b" * 64, "last_observation_sha256": "c" * 64, "report": {}}
            identity = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
            write(Path(self.state["evidence_root"]) / "statistics" / (identity + ".json"), receipt)
        with self.assertRaises(observer.StatisticsEvidenceError):
            observer.require_complete(self.state)

    def test_copy_failure_retains_source_reports_for_cleanup_retry(self):
        before = (self.run / "statistics/initial.json").read_bytes()
        with mock.patch.object(product, "_markdown_write", side_effect=OSError("disk full")):
            summary = observer.archive(self.state)
        self.assertTrue(summary["retention_pending"])
        self.assertEqual((self.run / "statistics/initial.json").read_bytes(), before)
        self.assertFalse(observer.archive(self.state)["retention_pending"])

    def complete_history(self):
        """Synthetic owner history exercises receipt integrity, not native delivery."""
        from workflow_statistics_report import project
        baseline = product.snapshot(self.run)
        baseline["architecture"] = {"layers": [], "components": [], "gaps": ["Synthetic test inventory"]}
        # This test needs a fresh initial event containing architecture.
        history_root = self.run / "statistics"
        import shutil
        shutil.rmtree(history_root)
        with mock.patch.object(product, "snapshot", return_value=baseline):
            product.record(self.run, "accepted", started=product.clock(), initial=True)
        running = deepcopy(baseline)
        running.update(phase="implementation", status="running", tasks=[{
            "id": "FEAT-001/TASK-001", "name": "Implement API", "component": "COMP-api",
            "touched_components": [], "definition_sha256": "d" * 64, "status": "running",
            "accepted": False, "integrated": False, "attempt": 1, "dispatched_at": "dispatch",
            "worker_started_at": "worker"}])
        with mock.patch.object(product, "snapshot", return_value=running):
            product.record(self.run, "start_task")
        finished = deepcopy(running)
        finished.update(phase="complete", status="done")
        finished["tasks"][0].update(status="done", accepted=True, integrated=True)
        with mock.patch.object(product, "snapshot", return_value=finished):
            product.record(self.run, "finish_task")
        events = product._events(history_root)
        self.state["phases"] = [{"phase": phase, "status": "PASS", "artifact_sha256": "f" * 64}
                                for phase in ("sdlc-start", "implementation")]
        receipts = []
        for index, (category, phase) in enumerate((("before", "sdlc-start"), ("during", "implementation"), ("after", "complete"))):
            observed = events[index]
            report = project(events[:index + 1], observed["state"], observed["clock"], initial=baseline)
            report["observation_sha256"] = observed["sha256"]
            receipts.append({"schema": "agentic-sdlc/statistics-observation-v1", "verification_id": self.state["verification_id"],
                             "phase": phase, "category": category, "status": "PASS", "run_id": self.run.name,
                             "initial_sha256": events[0]["sha256"], "last_observation_sha256": observed["sha256"],
                             "phase_attempt_sha256": "f" * 64 if category != "after" else None, "report": report})
        return receipts

    def save_receipts(self, receipts):
        destination = Path(self.state["evidence_root"]) / "statistics"
        for path in destination.glob("*.json"):
            path.unlink()
        for receipt in receipts:
            identity = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
            write(destination / (identity + ".json"), receipt)

    def test_valid_event_hashes_cannot_authenticate_forged_timing_or_category(self):
        receipts = self.complete_history()
        self.save_receipts(receipts)
        self.assertEqual(observer.require_complete(self.state)["status"], "PASS")
        changed = deepcopy(receipts)
        changed[-1]["report"]["timing"]["workflow_elapsed_ns"] += 1
        self.save_receipts(changed)
        with self.assertRaisesRegex(observer.StatisticsEvidenceError, "content differs"):
            observer.require_complete(self.state)
        changed = deepcopy(receipts)
        changed[1]["phase"] = "complete"
        self.save_receipts(changed)
        with self.assertRaisesRegex(observer.StatisticsEvidenceError, "category differs"):
            observer.require_complete(self.state)
        self.save_receipts(receipts)
        self.state["phases"] = []
        with self.assertRaisesRegex(observer.StatisticsEvidenceError, "phase observation"):
            observer.require_complete(self.state)

    def test_canonical_admission_rechecks_statistics_without_writes(self):
        import three_tier_lifecycle as lifecycle
        receipts = self.complete_history()
        self.state.update(environment={"headless_browser": "PASS"}, validations=[{"status": "PASS"}],
                          git={"baseline_sha": "a" * 40, "promoted_sha": "b" * 40, "aggregate_sha": "c" * 40},
                          project_root=str(self.root / "owned/project"))
        active = lifecycle.paths(self.root)["active"]
        canonical = Path(self.state["evidence_root"]) / "three-tier-results.json"
        write(active, self.state)
        write(canonical, {"fixture": "unrelated application evidence is isolated"})
        # Exercise real statistics admission/history while isolating the already
        # covered application, Git and catalog prerequisites.
        with mock.patch.object(lifecycle, "load_active", return_value=(None, self.state)), \
                mock.patch.object(lifecycle.owned_git_origin, "validate"), \
                mock.patch.object(lifecycle.owned_git_origin, "_git", return_value="c" * 40), \
                mock.patch.object(lifecycle, "validate_semantic_results", return_value={}), \
                mock.patch.object(lifecycle.runtime_catalog, "anchor", return_value=self.root):
            for case in ("valid", "missing", "forged"):
                with self.subTest(case=case):
                    selected = deepcopy(receipts)
                    if case == "missing":
                        selected = selected[1:]
                    elif case == "forged":
                        selected[-1]["report"]["timing"]["workflow_elapsed_ns"] += 1
                    self.save_receipts(selected)
                    before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
                    if case == "valid":
                        result = lifecycle.validate_results(self.root, self.state["verification_id"])
                        self.assertEqual(result["status"], "PASS")
                    else:
                        with self.assertRaises(lifecycle.LifecycleError):
                            lifecycle.validate_results(self.root, self.state["verification_id"])
                    self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
