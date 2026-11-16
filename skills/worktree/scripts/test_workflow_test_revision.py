"""Late TDD recovery against real Git; native termination uses explicit fixtures."""

import hashlib
import importlib
import unittest
from unittest import mock

import workflow_execution as engine
from workflow_publication import _basis
from workflow_state import read, write
from test_workflow_design_revision import RevisionFixture, git, fixture_replan_future


class TestRevisionTests(RevisionFixture):
    def classify(self, coordinator):
        from test_repair_control import failure_payload
        payload = failure_payload(proposed="TEST_DEFECT", commit=self.tdd, lifecycle="active_task")
        payload["phase"] = "sdlc-implement-plan"
        payload["fingerprints"] = {
            **{key: "sha256:" + value for key, value in self.before.items()},
            "plan": "sha256:" + coordinator["plan_digest"], "basis": "sha256:" + self.old_basis,
        }
        event = self.repair.record_failure_event(self.run_dir, payload)["event"]
        self.classification = self.repair.classify_failure(
            self.run_dir, "FEAT-001", event["event_id"])["classification"]
        self.assertEqual(self.classification["corrective_mode"], "test_revision")
        self.assertEqual(self.classification["next_recommended_skill"], "sdlc-tdd")
        self.assertTrue({"plan", "execution_preparation", "implementation", "validation", "tests"}
                        <= set(self.classification["invalidates"]))
        self.dispatch = self.repair.begin_remediation(
            self.run_dir, "FEAT-001", self.classification["classification_id"], "localized",
            "Correct the proven test fixture without changing accepted behavior.",
            "evidence/test-fixture-cause.json")["attempt"]

    def prepare_revision(self, files=None):
        owner = importlib.import_module("workflow_revision")
        return owner.prepare_test_revision(
            self.run_dir, "FEAT-001", self.classification["classification_id"],
            self.dispatch["dispatch_id"], [self.termination], files or ["tests/contract.txt"], self.session)

    def review(self, prepared):
        path = self.integration / "tests/contract.txt"
        path.write_text("corrected test-only fixture; accepted behavior unchanged\n")
        self.review_path = self.run_dir / "evidence/test-review.json"
        write(self.review_path, {
            "schema": "workflow/test-revision-review-v1", "prepare_sha256": prepared["sha256"],
            "files": {"tests/contract.txt": {"before": prepared["test_files"]["tests/contract.txt"],
                      "after": {"mode": "100644", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}},
            "criteria_unchanged": True, "reason": "Only fixture setup is corrected; assertions retain the accepted contract.",
        })

    def seal(self):
        return importlib.import_module("workflow_revision").seal_test_revision(
            self.run_dir, "FEAT-001", self.dispatch["dispatch_id"], self.review_path,
            "test: repair the classified fixture", self.session)

    def test_late_test_revision_preserves_failure_and_dispatches_fresh_assignment(self):
        with self.assertRaises(engine.ExecutionError):
            engine.seal_tdd_base(self.run_dir, "FEAT-001", "cannot reopen original seal")
        prepared = self.prepare_revision()
        self.review(prepared)
        sealed = self.seal()
        self.assertEqual(self.seal(), sealed)
        self.assertEqual(git(self.integration, "rev-parse", "HEAD^"), self.tdd)
        self.assertEqual(git(self.integration, "diff-tree", "--no-commit-id", "--name-only", "-r", sealed["head"]), "tests/contract.txt")
        self.assertEqual(self.spec_digests(), self.before)
        self.assertEqual(self.candidate.read_bytes(), self.candidate_bytes)
        self.assertEqual(read(engine.assignment_path(self.run_dir, "FEAT-001", "WAVE-001", "TASK-001")), self.original_assignment)
        original = self.fixture.plan.read_text()
        replacement = self.fixture.plan.with_name("FEAT-001.plan.v2.md")
        replacement.write_text(original.replace("Plan v1", "Plan v2") + "### TASK-002" + original.split("### TASK-001", 1)[1])
        replacement.with_suffix(".md.lock").write_text("locked\n")
        fixture_replan_future(self.run_dir, "FEAT-001", replacement, 1)
        fresh = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-002")[0]
        self.assertEqual(fresh["base_head"], sealed["head"])
        self.assertNotEqual(_basis(self.run_dir, "FEAT-001"), self.old_basis)
        self.assertNotEqual(fresh["assignment_digest"], self.assignment["assignment_digest"])

    def test_preparation_rejects_protected_paths_and_wrong_authority(self):
        owner = importlib.import_module("workflow_revision")
        original = read(engine.coordinator_path(self.run_dir, "FEAT-001"))
        for files in (["docs/design.md"], ["docs/requirements.md"], ["../tests/contract.txt"],
                      ["/tests/contract.txt"], ["tests/contract.txt", "tests/contract.txt"], ["tests"]):
            with self.subTest(files=files), self.assertRaises(engine.ExecutionError):
                self.prepare_revision(files)
        with self.assertRaises(engine.ExecutionError):
            owner.prepare_design_revision(self.run_dir, "FEAT-001", self.classification["classification_id"],
                                          self.dispatch["dispatch_id"], [self.termination], self.session)
        self.assertEqual(read(engine.coordinator_path(self.run_dir, "FEAT-001")), original)

    def test_missing_selected_file_is_a_structured_rejection(self):
        with self.assertRaises(engine.ExecutionError):
            self.prepare_revision(["tests/missing.py"])

    def test_preparation_requires_exact_termination_no_partial_merge_and_released_lease(self):
        import workflow_resources as resources
        owner = importlib.import_module("workflow_revision")
        with self.assertRaises(engine.ExecutionError):
            owner.prepare_test_revision(self.run_dir, "FEAT-001", self.classification["classification_id"],
                                        self.dispatch["dispatch_id"], [], ["tests/contract.txt"], self.session)
        path = engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001")
        original = read(path)
        write(path, {**original, "integrated_task_ids": ["TASK-001"]})
        with self.assertRaises(engine.ExecutionError):
            self.prepare_revision()
        write(path, original)
        assignment_path = engine.assignment_path(self.run_dir, "FEAT-001", "WAVE-001", "TASK-001")
        resources.acquire(self.run_dir.parents[2], assignment_path, "design-test", self.worker_session)
        with self.assertRaises(engine.ExecutionError):
            self.prepare_revision()
        self.assertEqual(read(path), original)
        resources.release(self.run_dir.parents[2], assignment_path, "design-test", self.session,
                          termination=self.termination)
        self.prepare_revision()

    def test_frozen_acceptance_harness_is_never_a_repair_file(self):
        owner = importlib.import_module("workflow_revision")
        original_read = owner.read
        policy = {"tasks": {"TASK-001": {"criteria": [{"check": {
            "harness": {"tests/contract.txt": "a" * 64}}}]}}}
        def frozen_basis(path):
            value = original_read(path)
            if path.parent == self.run_dir / "bases/FEAT-001":
                return {**value, "task_acceptance_policy": policy,
                        "task_acceptance_policy_sha256": engine.canonical_digest(policy)}
            return value
        with mock.patch.object(owner, "read", side_effect=frozen_basis), self.assertRaisesRegex(
                engine.ExecutionError, "protected"):
            owner._test_files(self.run_dir, "FEAT-001", engine._load_coordinator(self.run_dir, "FEAT-001"),
                              ["tests/contract.txt"])

    def test_changed_review_bytes_criteria_and_symlinks_are_rejected_before_staging(self):
        prepared = self.prepare_revision()
        self.review(prepared)
        original = read(self.review_path)
        index = git(self.integration, "write-tree")
        for changes in ({"criteria_unchanged": False}, {"prepare_sha256": "0" * 64}, {"reason": ""}):
            write(self.review_path, {**original, **changes})
            with self.assertRaises(engine.ExecutionError):
                self.seal()
            self.assertEqual(git(self.integration, "write-tree"), index)
        write(self.review_path, original)
        path = self.integration / "tests/contract.txt"
        path.write_text("unreviewed change\n")
        with self.assertRaises(engine.ExecutionError):
            self.seal()
        path.unlink()
        path.symlink_to("../README.md")
        with self.assertRaises(engine.ExecutionError):
            self.seal()
        self.assertEqual(git(self.integration, "rev-parse", "HEAD"), self.tdd)

    def test_unrelated_staged_content_and_canonical_drift_are_preserved(self):
        prepared = self.prepare_revision()
        self.review(prepared)
        path = self.integration / "README.md"
        original = path.read_bytes()
        path.write_text("unrelated staged change\n")
        git(self.integration, "add", "-A")
        path.write_bytes(original)
        index = git(self.integration, "write-tree")
        with self.assertRaises(engine.ExecutionError):
            self.seal()
        self.assertEqual(git(self.integration, "write-tree"), index)
        git(self.integration, "restore", "--staged", "README.md")
        design = self.integration / "docs/design.md"
        design.write_bytes(design.read_bytes() + b"\nUnpublished canonical change.\n")
        with self.assertRaises(engine.ExecutionError):
            self.seal()
        self.assertEqual(git(self.integration, "rev-parse", "HEAD"), self.tdd)

    def test_pending_replay_retains_failure_and_blocks_replan(self):
        owner = importlib.import_module("workflow_revision")
        prepared = self.prepare_revision()
        self.assertEqual(self.prepare_revision(), prepared)
        self.assertEqual(owner.observe_pending_revision(self.run_dir, "FEAT-001"), prepared)
        with self.assertRaises(engine.ExecutionError):
            self.prepare_revision(["README.md"])
        with self.assertRaises(engine.ExecutionError):
            fixture_replan_future(self.run_dir, "FEAT-001", self.fixture.plan, 1)
        self.assertEqual(self.candidate.read_bytes(), self.candidate_bytes)
        (self.integration / "unrelated.txt").write_text("unrelated pending change\n")
        with self.assertRaises(engine.ExecutionError):
            owner.observe_pending_revision(self.run_dir, "FEAT-001")

    def test_interrupted_commit_resumes_without_second_commit(self):
        owner = importlib.import_module("workflow_revision")
        prepared = self.prepare_revision()
        self.review(prepared)
        save = owner._save
        def interrupted(path, value):
            if path.name == "sealed.json":
                raise RuntimeError("synthetic interruption after commit")
            return save(path, value)
        with mock.patch.object(owner, "_save", side_effect=interrupted), self.assertRaises(RuntimeError):
            self.seal()
        committed = git(self.integration, "rev-parse", "HEAD")
        self.assertNotEqual(committed, self.tdd)
        self.assertEqual(self.seal()["head"], committed)
        self.assertEqual(self.seal()["head"], committed)
        self.assertEqual(git(self.integration, "rev-list", "--count", self.tdd + "..HEAD"), "1")

    def test_stale_dispatch_cannot_prepare_or_seal(self):
        control_path = self.repair._control_path(self.run_dir, "FEAT-001")
        original = read(control_path)
        write(control_path, {**original, "status": "blocked_missing_evidence"})
        with self.assertRaises(engine.ExecutionError):
            self.prepare_revision()
        write(control_path, original)
        prepared = self.prepare_revision()
        self.review(prepared)
        write(control_path, {**original, "status": "blocked_missing_evidence"})
        with self.assertRaises(engine.ExecutionError):
            self.seal()
        self.assertEqual(git(self.integration, "rev-parse", "HEAD"), self.tdd)


if __name__ == "__main__":
    unittest.main()
