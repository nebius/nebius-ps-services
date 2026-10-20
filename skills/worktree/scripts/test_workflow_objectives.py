#!/usr/bin/env python3
"""Direct intake and binding tests using disposable committed repositories."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import workflow_objectives as objectives
from workflow_state import WorkflowError, read, write


class ObjectiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "repo"
        self.project.mkdir()
        self.home = self.root / "agent"
        self.home.mkdir(mode=0o700)
        for args in (("init", "-b", "main"), ("config", "user.email", "fixture@example.com"),
                     ("config", "user.name", "Fixture")):
            self.git(*args)
        (self.project / "README.md").write_text("fixture\n")
        self.git("add", "-A")
        self.git("commit", "-m", "fixture")
        self.value = {"outcome": "Add a counter", "scope": ["counter.py"],
                      "constraints": ["No remote writes"], "acceptance_criteria": ["Counter increments"],
                      "clarifications": [], "provenance": {"kind": "direct_request", "source_sha256": "a" * 64}}

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.project), *args], stderr=subprocess.DEVNULL, text=True).strip()

    def accept(self, **kwargs):
        return objectives.accept(self.project, self.home, "implement", "session-a", self.value, **kwargs)

    def test_accept_persists_only_normalized_objective(self):
        result = self.accept()
        run = Path(result["run_dir"])
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(read(run / "objectives/r0001.json"), self.value)
        self.assertFalse((run / "prompt.json").exists())
        self.assertEqual(os.stat(run / "objective.json").st_mode & 0o777, 0o600)

    def test_same_session_continues_without_new_revision(self):
        first = self.accept()
        second = self.accept()
        self.assertEqual(second["run_dir"], first["run_dir"])
        self.assertEqual(second["revision"], "r0001")
        self.assertEqual(second["status"], "CONTINUE")

    def test_new_session_does_not_infer_sole_active_run(self):
        self.accept()
        self.assertIsNone(objectives.observe_binding(self.home, "implement", "session-b", self.project))
        with self.assertRaisesRegex(WorkflowError, "explicitly continue"):
            objectives.accept(self.project, self.home, "implement", "session-b", self.value)

    def test_cross_session_requires_explicit_continue(self):
        first = self.accept()
        second = objectives.continue_run(self.project, self.home, "implement", "session-b")
        self.assertEqual(second["run_dir"], first["run_dir"])
        with self.assertRaisesRegex(WorkflowError, "does not own"):
            objectives.observe_binding(self.home, "implement", "session-a")

    def test_restore_binding_is_read_only(self):
        first = self.accept()
        files = {p: p.read_bytes() for p in self.home.rglob("*.json")}
        self.assertEqual(objectives.observe_binding(self.home, "implement", "session-a")[0], Path(first["run_dir"]))
        self.assertEqual(files, {p: p.read_bytes() for p in self.home.rglob("*.json")})

    def test_steering_fences_dispatch_before_replanning(self):
        first = self.accept()
        self.value["acceptance_criteria"].append("Counter persists")
        steered = self.accept(revise=True)
        self.assertEqual(steered["revision"], "r0002")
        self.assertEqual(steered["fence"], "steering")
        with self.assertRaisesRegex(WorkflowError, "fenced"):
            objectives.check_dispatch(Path(first["run_dir"]))

    def test_other_objectives_are_not_queued(self):
        self.accept()
        self.value["outcome"] = "Replace unrelated service"
        with self.assertRaisesRegex(WorkflowError, "not queued"):
            self.accept()
        self.assertFalse(list(self.home.rglob("*queue*")))

    def test_cross_session_rejects_active_writers(self):
        first = self.accept()
        write(Path(first["run_dir"]) / "execution/FEAT-001/waves/WAVE-001.json", {"active_task_ids": ["TASK-001"]})
        with self.assertRaisesRegex(WorkflowError, "outstanding workers"):
            objectives.continue_run(self.project, self.home, "implement", "session-b")

    def test_cancellation_fences_without_git_rollback(self):
        first = self.accept()
        head = self.git("rev-parse", "HEAD")
        objectives.fence(Path(first["run_dir"]), "session-a", "cancelled")
        with self.assertRaises(WorkflowError):
            objectives.check_dispatch(Path(first["run_dir"]))
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_old_run_schema_is_rejected_without_conversion(self):
        first = self.accept()
        path = Path(first["run_dir"]) / "run.json"
        write(path, {"schema": "task-implementer/run-manifest-v1"})
        before = path.read_bytes()
        with self.assertRaisesRegex(WorkflowError, "obsolete"):
            objectives.validate_run(path.parent)
        self.assertEqual(path.read_bytes(), before)

    def test_snapshot_tampering_is_rejected(self):
        first = self.accept()
        path = Path(first["run_dir"]) / "objectives/r0001.json"
        value = read(path)
        value["outcome"] = "Tampered"
        write(path, value)
        with self.assertRaisesRegex(WorkflowError, "bytes changed"):
            objectives.validate_run(path.parent.parent)

    def test_raw_history_keys_are_rejected(self):
        self.value["conversation"] = ["raw turn"]
        with self.assertRaisesRegex(WorkflowError, "only normalized"):
            self.accept()

    def test_current_plan_has_explicit_provenance(self):
        self.value["provenance"]["kind"] = "current_plan"
        first = self.accept()
        self.assertEqual(read(Path(first["run_dir"]) / "objectives/r0001.json")["provenance"]["kind"], "current_plan")

    def test_detached_and_unborn_git_are_rejected(self):
        self.git("checkout", "--detach")
        with self.assertRaises(WorkflowError):
            self.accept()
        self.git("checkout", "--orphan", "empty")
        with self.assertRaises(WorkflowError):
            self.accept()

    def test_symlinked_private_root_is_rejected(self):
        target = self.root / "foreign"
        target.mkdir()
        (self.home / "implement").symlink_to(target)
        with self.assertRaises(WorkflowError):
            self.accept()
        self.assertEqual(list(target.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
