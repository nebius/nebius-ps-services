"""Implementation admission executes core checks without requiring runtime UAT."""
from pathlib import Path
import os
import subprocess
import tempfile
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import workflow_delivery_admission as admission
import workflow_qualification as qualification
from workflow_state import WorkflowError, lock, read, write
import workflow_validation as validation
import workflow_process
from test_workflow_fixture import seed


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.project = root / "project"
        self.project.mkdir()
        def git(*args):
            return subprocess.check_output(["git", "-C", str(self.project), *args], text=True, stderr=subprocess.DEVNULL).strip()
        git("init", "-b", "feature/test")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        (self.project / "README.md").write_text("core fixture\n")
        git("add", "-A")
        git("commit", "-m", "fixture")
        self.head = git("rev-parse", "HEAD")
        self.run = root / "home/sdlc-runs/project/run-test"
        seed(self.run, self.project, "project", "admission-test")
        native = mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "admission-test"})
        native.start()
        self.addCleanup(native.stop)
        self.expected = {"project": self.project, "head": self.head, "criteria": ["CORE-1"],
                         "objective_sha256": read(self.run / "objective.json")["revisions"][-1]["sha256"],
                         "basis_sha256": None, "inputs": None, "obligation_basis": {"CORE-1": "c" * 64}}
        target = mock.patch.object(admission, "target", return_value=self.expected)
        target.start()
        self.addCleanup(target.stop)
        self.check = {"id": "CORE-1", "argv": ["{python}", "-I", "-c", "from pathlib import Path; assert Path('README.md').read_text() == 'core fixture\\n'"],
                      "execution_policy": {"observation_interval_seconds": 30, "deadline": None}, "environment_keys": []}

    def test_core_phase_is_admitted_without_uat_plan_environment_or_runner(self):
        with mock.patch.object(qualification, "command") as runtime:
            ref = admission.admit(self.run, "PHASE:foundation", [self.check])
            value = admission.verify_receipt(self.run, ref)
        runtime.assert_not_called()
        self.assertEqual(value["uat"]["status"], "not_requested")
        self.assertEqual(value["validation"]["outcomes"][0]["exit_code"], 0)
        self.assertNotIn("artifact_sha256", value)
        self.assertNotIn("environment", value)

    def test_failed_core_check_cannot_be_admitted_as_pending_uat(self):
        check = {**self.check, "argv": ["{python}", "-I", "-c", "raise SystemExit(1)"]}
        with self.assertRaisesRegex(WorkflowError, "checks failed"):
            admission.admit(self.run, "PHASE:foundation", [check])
        self.assertFalse(list((self.run / "delivery-admission").glob("*/*.produced.json")))

    def test_missing_core_coverage_is_rejected_before_execution(self):
        with self.assertRaisesRegex(WorkflowError, "executable checks"):
            admission.admit(self.run, "PHASE:foundation", [])

    def test_requesting_additional_uat_does_not_remove_core_coverage(self):
        import workflow_uat as uat
        policy = uat.normalize({"requested": True, "backend": "existing",
            "scope": {"kind": "objective", "executions": []}, "criteria": ["CORE-1"]})
        with mock.patch.object(uat, "context", return_value=({}, {}, policy)):
            self.assertEqual(admission.implementation_criteria(self.run, self.expected), ["CORE-1"])

    def test_other_tasks_optional_id_cannot_erase_source_implementation_criterion(self):
        write(self.run / "bases/SLICE-OTHER/untrusted.json", {"task_acceptance_policy": {
            "tasks": {"TASK-001": {"criteria": [{"id": "CORE-1", "purpose": "uat"}]}}}})
        self.assertEqual(admission.implementation_criteria(self.run, self.expected), ["CORE-1"])

    def test_slice_rechecks_final_candidate_after_previously_passed_waves(self):
        self.expected["definition"] = {"implementation_coverage": {"CORE-1": ["CORE-1"]}}
        check = {**self.check, "argv": ["{python}", "-I", "-c", "raise SystemExit(1)"]}
        with mock.patch.object(admission, "_proof", return_value={"promoted_head": self.head, "waves": {"WAVE-001": "f" * 64}}), \
                mock.patch("workflow_validation.load_plan", return_value={"wave_checks": [check]}), \
                mock.patch("workflow_task_acceptance.final_snapshot", return_value=None):
            with self.assertRaisesRegex(WorkflowError, "final implementation checks failed"):
                admission.admit(self.run, "SLICE-001")
        self.assertFalse(list((self.run / "delivery-admission/SLICE-001").glob("*.produced.json")))

    def test_tampered_admission_and_candidate_drift_are_rejected(self):
        ref = admission.admit(self.run, "PHASE:foundation", [self.check])
        self.expected["head"] = "d" * 40
        with self.assertRaises(WorkflowError):
            admission.verify_receipt(self.run, ref)
        self.expected["head"] = self.head
        value = read(self.run / ref["receipt"])
        value["criteria"] = []
        write(self.run / ref["receipt"], value)
        with self.assertRaises(WorkflowError):
            admission.verify_receipt(self.run, ref, current=False)

    def test_incomplete_delivery_evaluator_retains_retry_and_successor_fence(self):
        observation = workflow_process.run_owned([sys.executable, "-c", "pass"])
        observation["local_group_quiescent"] = False
        observation["stdout"]["complete"] = False
        with mock.patch.object(validation, "run_owned", return_value=observation):
            with self.assertRaises(workflow_process.ProcessCleanupError):
                admission.admit(self.run, "PHASE:foundation", [self.check])
        with self.assertRaisesRegex(WorkflowError, "quiescent"):
            validation.require_quiescent(self.run)
        with mock.patch.object(validation, "run_owned") as runner:
            with self.assertRaisesRegex(WorkflowError, "quiescent"):
                admission.admit(self.run, "PHASE:foundation", [self.check])
            runner.assert_not_called()
        from workflow_objectives import _writers_quiescent
        with self.assertRaisesRegex(WorkflowError, "quiescent"):
            _writers_quiescent(self.run)
        self.assertFalse(list((self.run / "delivery-admission").glob("*/*.produced.json")))

    def test_delivery_releases_coordinator_lock_during_evaluation(self):
        entered, release = threading.Event(), threading.Event()
        original = validation._run_check
        def delayed(*args, **options):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test evaluator was not released")
            return original(*args, **options)
        def observe():
            with lock(self.run.parent):
                return "observed"
        with mock.patch.object(validation, "_run_check", side_effect=delayed), ThreadPoolExecutor(max_workers=2) as pool:
            admitted = pool.submit(admission.admit, self.run, "PHASE:foundation", [self.check])
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(pool.submit(observe).result(timeout=2), "observed")
                with self.assertRaisesRegex(WorkflowError, "quiescent"):
                    validation.require_quiescent(self.run)
            finally:
                release.set()
            admission.verify_receipt(self.run, admitted.result(timeout=10))

    def test_changed_coordinator_cannot_publish_delivery_admission(self):
        original = validation._run_check
        def changed(*args, **options):
            result = original(*args, **options)
            with lock(self.run.parent):
                value = read(self.run / "run.json")
                write(self.run / "run.json", {**value, "coordinator_session_sha256": "b" * 64})
            return result
        with mock.patch.object(validation, "_run_check", side_effect=changed):
            with self.assertRaises(WorkflowError) as error:
                admission.admit(self.run, "PHASE:foundation", [self.check])
        self.assertEqual(error.exception.code, "OWNERSHIP_INVALID")
        self.assertFalse(list((self.run / "delivery-admission").glob("*/*.produced.json")))


if __name__ == "__main__":
    unittest.main()
