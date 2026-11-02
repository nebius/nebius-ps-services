"""Executed harness proof, strict receipts and failure provenance without a live target."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import workflow_qualification as q
from workflow_state import WorkflowError, digest, read, write

RUNNER = '''import json, os, subprocess, sys
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
mode = os.environ.get("QUAL_MODE", "pass")
value = {"head":head, "artifact_sha256":"a"*64, "configuration_sha256":"b"*64}
if sys.argv[1] == "observe":
    value.update(environment="test", trial="trial-1", reset="reset-1", intervened=mode=="intervened", writers_quiescent=True)
    if mode=="wrong-artifact": value["artifact_sha256"]="c"*64
elif sys.argv[1] == "criterion":
    assert sum([1,2,3]) == 6
    value={"criterion":sys.argv[3], "tests":1, "passed":1, "failed":0, "skipped":0}
    if mode=="skip": value.update(passed=0,skipped=1)
    if mode=="labels": value={"status":"passed"}
print(json.dumps(value))
'''


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.run = self.root / "home/sdlc-runs/project/run-test"
        def git(*args):
            return subprocess.check_output(["git", "-C", str(self.project), *args], stderr=subprocess.DEVNULL, text=True).strip()
        git("init", "-b", "candidate")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Fixture")
        (self.project / "runner.py").write_text(RUNNER)
        git("add", "-A")
        git("commit", "-m", "fixture")
        self.expected = {"project": self.project, "head": git("rev-parse", "HEAD"), "criteria": ["AC-001", "AC-002"],
                         "kind": "runtime", "basis_sha256": "e" * 64, "objective_sha256": "f" * 64,
                         "inputs": {"manifest_sha256": "d" * 64}, "completion": {"live_operations": ["test"]}}
        self.expected["obligation_basis"] = {c: {"source_sha256": "d" * 64, "coverage_sha256": "e" * 64}
                                             for c in self.expected["criteria"]}
        self.plan = {"schema": q.SCHEMA, "execution_id": "SLICE-001", "runner": "runner.py",
                     "runner_sha256": hashlib.sha256(RUNNER.encode()).hexdigest(), "criteria": self.expected["criteria"],
                     "environment": {"id": "test", "resource": "fixture", "nonproduction": True},
                     "operations": ["test"], "timeout_seconds": 10, "environment_keys": ["QUAL_MODE"], "live": True}
        self.planpath = self.root / "locked-plan.md"
        self.planpath.write_text("Qualification plan SHA256: " + digest(self.plan) + "\n")
        write(self.run / "execution/SLICE-001/coordinator.json", {"plan_path": str(self.planpath)})
        for patch in (mock.patch.object(q, "check_dispatch"), mock.patch.object(q, "target", return_value=self.expected),
                      mock.patch.object(q, "accepted", return_value=({}, {"sha256": "f" * 64}, {"inputs": self.expected["inputs"]})),
                      mock.patch.dict(os.environ, {"QUAL_MODE": "pass"})):
            patch.start()
            self.addCleanup(patch.stop)

    def qualify(self):
        result = q.qualify(self.run, "SLICE-001", self.plan)
        return {k: result[k] for k in ("receipt", "sha256")}

    def test_executes_every_criterion_and_admits_exact_receipt(self):
        reference = self.qualify()
        receipt = q.verify_receipt(self.run, reference)
        self.assertEqual(len(receipt["commands"]), 5)
        self.assertEqual(set(receipt["criteria"]), {"AC-001", "AC-002"})
        self.assertNotIn("QUAL_MODE", json.dumps(receipt["commands"]))

    def test_labels_skips_wrong_artifacts_and_intervention_cannot_pass(self):
        for mode in ("labels", "skip", "wrong-artifact", "intervened"):
            with self.subTest(mode=mode), mock.patch.dict(os.environ, {"QUAL_MODE": mode}), self.assertRaises(WorkflowError):
                self.qualify()
        receipts = [read(p) for p in (self.run / "qualification/SLICE-001").glob("*.json")]
        self.assertTrue(all(v["status"] == "failed" for v in receipts))

    def test_forged_receipt_recomputed_hash_is_not_producer_admitted(self):
        reference = self.qualify()
        receipt = read(self.run / reference["receipt"])
        receipt["plan"]["runner_sha256"] = "0" * 64
        receipt["plan_sha256"] = digest(receipt["plan"])
        write(self.run / reference["receipt"], receipt)
        reference["sha256"] = digest(receipt)
        with self.assertRaises(WorkflowError):
            q.verify_receipt(self.run, reference)

    def test_locked_plan_drift_and_missing_criteria_block(self):
        self.planpath.write_text("Qualification plan SHA256: " + "0" * 64)
        with self.assertRaises(WorkflowError):
            self.qualify()
        plan = {**self.plan, "criteria": ["AC-001"]}
        with self.assertRaises(WorkflowError):
            q.validate_plan(plan, self.expected)

    def test_revoked_authority_prevents_runner_launch(self):
        with mock.patch.object(q, "check_dispatch", side_effect=[None, WorkflowError("FENCED", "revoked")]), \
                mock.patch.object(q, "command") as runner, self.assertRaises(WorkflowError):
            self.qualify()
        runner.assert_not_called()

    def test_failed_trial_cannot_be_reused_as_clean_evidence(self):
        with mock.patch.dict(os.environ, {"QUAL_MODE": "skip"}), self.assertRaises(WorkflowError):
            self.qualify()
        with self.assertRaisesRegex(WorkflowError, "fresh reset"):
            self.qualify()

    def test_first_criterion_attempt_and_trial_are_durable_before_execution(self):
        original = q.command
        def crash(project, plan, action, criterion=None):
            if action == "criterion":
                receipt = read(next((self.run / "qualification/SLICE-001").glob("*.json")))
                self.assertEqual(receipt["before"]["trial"], "trial-1")
                self.assertEqual(receipt["trial_id"], "trial-1")
                self.assertEqual(receipt["attempted_criterion"], "AC-001")
                self.assertEqual(receipt["status"], "incomplete")
                raise KeyboardInterrupt()
            return original(project, plan, action, criterion)
        with mock.patch.object(q, "command", side_effect=crash), self.assertRaises(KeyboardInterrupt):
            self.qualify()
        with self.assertRaisesRegex(WorkflowError, "fresh reset"):
            self.qualify()

    def test_qualification_acquires_coordinator_before_resource_lease(self):
        from contextlib import contextmanager
        held = []
        @contextmanager
        def ordered(path):
            if "qualification-leases" in path.parts:
                self.assertIn(self.run.parent, held)
            held.append(path)
            try:
                yield
            finally:
                held.pop()
        with mock.patch.object(q, "lock", side_effect=ordered):
            self.qualify()


if __name__ == "__main__":
    unittest.main()
