"""Real-Git design recovery; native termination observations are synthetic fixtures."""

import hashlib
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

import workflow_execution as engine
from workflow_state import read
from workflow_publication import _basis
import test_workflow_execution as fixtures

git = fixtures.git


class DesignRevisionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GitLifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.run_dir = self.root / "home/workflow-fixture/run-1"
        self.run_dir.parent.mkdir(parents=True)
        self.fixture.run_dir.rename(self.run_dir)
        self.fixture.run_dir = self.run_dir
        self.fixture.plan = self.run_dir / "plans/FEAT-001.plan.v1.md"
        self.project = self.fixture.project
        self.session = "deterministic-coordinator"
        environment = mock.patch.dict(os.environ, {
            "CODEX_HOME": str(self.root / "home"),
            "CODEX_THREAD_ID": self.session,
            "SKILLS_AGENT": "codex",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.fixture.plan.write_text(
            self.fixture.plan.read_text().split("### TASK-002", 1)[0].replace(
                "- Conflict domains: code:a", "- Conflict domains: code:a; runtime:design-test")
        )
        coordinator = self.fixture.prepare()
        self.integration = Path(coordinator["integration_worktree"])
        (self.integration / "tests").mkdir()
        (self.integration / "tests/contract.txt").write_text("original red contract\n")
        self.tdd = engine.seal_tdd_base(
            self.run_dir, "FEAT-001", "test: original contract before implementation"
        )["integration_head"]
        self.assignment = engine.prepare_wave(
            self.run_dir, "FEAT-001", "WAVE-001"
        )[0]
        _, self.worker_session = self.fixture.result_start(self.assignment)
        self.fixture.result_write(self.assignment)
        self.result, self.termination, _ = self.fixture.result_ingest(
            self.assignment, "blocked"
        )
        self.candidate = Path(self.assignment["worktree"]) / "src/a.py"
        self.candidate_bytes = self.candidate.read_bytes()
        self.original_assignment = read(engine.assignment_path(
            self.run_dir, "FEAT-001", "WAVE-001", "TASK-001"
        ))
        self.old_basis = _basis(self.run_dir, "FEAT-001")
        self.before = self.spec_digests()
        scripts = Path(__file__).resolve().parents[2] / "sdlc-classify-failure/scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import repair_control
        from test_repair_control import failure_payload, design_diagnosis

        self.repair = repair_control
        payload = failure_payload(
            proposed="UNKNOWN_DEFECT", cause_status="unknown",
            commit=self.tdd, lifecycle="active_task",
        )
        payload["phase"] = "sdlc-implement-plan"
        payload["fingerprints"] = {
            **{name: "sha256:" + value for name, value in self.before.items()},
            "plan": "sha256:" + coordinator["plan_digest"],
            "basis": "sha256:" + self.old_basis,
        }
        event = repair_control.record_failure_event(self.run_dir, payload)["event"]
        diagnosis = repair_control.record_diagnosis(
            self.run_dir, design_diagnosis(event)
        )["diagnosis"]
        self.classification = repair_control.classify_failure(
            self.run_dir, "FEAT-001", event["event_id"], diagnosis["diagnosis_id"]
        )["classification"]
        self.dispatch = repair_control.begin_remediation(
            self.run_dir, "FEAT-001", self.classification["classification_id"],
            "design", "Correct the proven contract at its semantic owner.",
            "evidence/proven-design-cause.json",
        )["attempt"]

    def spec_digests(self):
        return {
            name: hashlib.sha256((self.integration / "docs" / f"{name}.md").read_bytes()).hexdigest()
            for name in ("requirements", "design")
        }

    def prepare_revision(self):
        owner = importlib.import_module("workflow_design_revision")
        return owner.prepare_design_revision(
            self.run_dir, "FEAT-001", self.classification["classification_id"],
            self.dispatch["dispatch_id"], [self.termination], self.session,
        )

    def publish_correction(self):
        from project_specs_lib.transaction import publish_spec_pair
        from workflow_objectives import validate_run

        requirements = (self.integration / "docs/requirements.md").read_bytes()
        design = (self.integration / "docs/design.md").read_bytes().replace(
            b"Use coordinator-owned dependency waves and isolated workers.",
            b"Use coordinator-owned dependency waves and revised isolated worker contracts.",
        )
        return publish_spec_pair(
            self.integration,
            requirements_candidate=requirements,
            design_candidate=design,
            expected_git_head=self.tdd,
            expected_requirements_sha256=self.before["requirements"],
            expected_design_sha256=self.before["design"],
            operation_id="test-design-correction",
            expected_objective_sha256=validate_run(self.run_dir)[1]["revisions"][-1]["sha256"],
            expected_basis_sha256=self.old_basis,
        )

    def seal(self):
        owner = importlib.import_module("workflow_design_revision")
        return owner.seal_design_revision(
            self.run_dir, "FEAT-001", self.dispatch["dispatch_id"],
            "test-design-correction", "fix: admitted design contract", self.session,
        )

    def test_fresh_disjoint_task_dispatches_after_real_impact_reconciliation(self):
        from workflow_refinement import refinement, settle
        from project_specs_lib.impact import statement_inventory
        import workflow_impact as impact
        self.prepare_revision()
        self.publish_correction()
        self.seal()
        value = refinement(self.run_dir)
        claim = {key: value[key] for key in ("objective_id", "revision", "intent_sha256")}
        claim.update(schema=impact.CLAIM_SCHEMA, declared_effects=["design"],
                     declared_plan_action="replan_required", dispositions=[{
                         "statement": item["key"], "disposition": "changed_contract",
                         "requirements": ["REQ-001"], "design": ["FEAT-001"],
                         "effects": ["design"], "reason": None,
                     } for item in statement_inventory(value["extracted"])])
        settle(self.run_dir, claim)
        original = self.fixture.plan.read_text()
        replacement = self.fixture.plan.with_name("FEAT-001.plan.v2.md")
        replacement.write_text(original.replace("Plan v1", "Plan v2") + "### TASK-002" +
                               original.split("### TASK-001", 1)[1].replace("src/a.py", "src/new.py").replace("code:a", "code:new"))
        replacement.with_suffix(".md.lock").write_text("locked\n")
        engine.replan_future(self.run_dir, "FEAT-001", replacement, 1)
        fresh = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-002")[0]
        self.assertEqual(fresh["base_head"], git(self.integration, "rev-parse", "HEAD"))
        self.assertNotEqual(fresh["basis_sha256"], self.old_basis)
        self.assertNotEqual(fresh["assignment_digest"], self.assignment["assignment_digest"])
        with self.assertRaises(engine.ExecutionError):
            engine._assert_current_assignment(self.run_dir, "FEAT-001",
                engine._load_coordinator(self.run_dir, "FEAT-001"), self.assignment)
        self.assertEqual(self.candidate.read_bytes(), self.candidate_bytes)

    def test_prepare_rejects_missing_wrong_or_tampered_authority(self):
        owner = importlib.import_module("workflow_design_revision")
        original = read(engine.coordinator_path(self.run_dir, "FEAT-001"))
        for classification, dispatch in (("0" * 64, self.dispatch["dispatch_id"]),
                                          (self.classification["classification_id"], "0" * 64)):
            with self.subTest(classification=classification, dispatch=dispatch):
                with self.assertRaises((engine.ExecutionError, self.repair.RepairControlError)):
                    owner.prepare_design_revision(self.run_dir, "FEAT-001", classification,
                                                  dispatch, [self.termination], self.session)
                self.assertEqual(read(engine.coordinator_path(self.run_dir, "FEAT-001")), original)

    def test_prepare_rejects_wrong_head_dirty_checkout_live_writer_and_partial_merge(self):
        from workflow_state import write
        owner = importlib.import_module("workflow_design_revision")
        stray = self.integration / "unrelated.txt"
        stray.write_text("preserve\n")
        with self.assertRaises(engine.ExecutionError):
            self.prepare_revision()
        self.assertEqual(stray.read_text(), "preserve\n")
        stray.unlink()
        with self.assertRaises(engine.ExecutionError):
            owner.prepare_design_revision(self.run_dir, "FEAT-001", self.classification["classification_id"],
                                          self.dispatch["dispatch_id"], [], self.session)
        wave_path = engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001")
        wave = read(wave_path)
        write(wave_path, {**wave, "merged_task_ids": ["TASK-001"]})
        with self.assertRaises(engine.ExecutionError):
            self.prepare_revision()
        write(wave_path, wave)
        git(self.integration, "commit", "--allow-empty", "-qm", "unauthorized drift")
        with self.assertRaises(engine.ExecutionError):
            self.prepare_revision()

    def test_prepare_rejects_unreleased_resources_before_retention(self):
        import workflow_resources as resources
        assignment_path = engine.assignment_path(self.run_dir, "FEAT-001", "WAVE-001", "TASK-001")
        resources.acquire(self.run_dir.parents[2], assignment_path, "design-test", self.worker_session)
        with self.assertRaises(engine.ExecutionError):
            self.prepare_revision()
        self.assertFalse((engine.execution_dir(self.run_dir, "FEAT-001") / "retentions").exists())
        self.assertEqual(read(engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001"))["status"], "running")
        resources.release(self.run_dir.parents[2], assignment_path, "design-test", self.session,
                          termination=self.termination)
        self.prepare_revision()

    def test_inactive_dispatch_and_changed_classification_are_rejected(self):
        from workflow_state import write
        path = self.repair._control_path(self.run_dir, "FEAT-001")
        original = read(path)
        write(path, {**original, "status": "blocked_missing_evidence"})
        with self.assertRaises(engine.ExecutionError):
            self.prepare_revision()
        write(path, original)
        path = self.repair._classification_path(self.run_dir, "FEAT-001", self.classification["classification_id"])
        write(path, {**read(path), "classification": "UNKNOWN_DEFECT"})
        with self.assertRaises(engine.ExecutionError):
            self.prepare_revision()

    def test_wrong_native_termination_and_tampered_retention_cannot_seal(self):
        from workflow_state import write
        original = read(self.termination)
        write(self.termination, {**original, "worker_session_hash": "f" * 64})
        with self.assertRaises(engine.ExecutionError):
            self.prepare_revision()
        write(self.termination, original)
        prepared = self.prepare_revision()
        self.publish_correction()
        proof = self.run_dir / prepared["writers"][0]["proof_path"]
        write(proof, {**read(proof), "prior_status": "admitted"})
        with self.assertRaises(engine.ExecutionError):
            self.seal()
        self.assertEqual(git(self.integration, "rev-parse", "HEAD"), self.tdd)

    def test_prepare_replays_interrupted_retention_without_changing_original_failure(self):
        owner = importlib.import_module("workflow_design_revision")
        save = owner.write
        wave_path = engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001")
        def interrupt(path, value, **kwargs):
            if path == wave_path and value["status"] == "superseded":
                raise RuntimeError("synthetic interruption before wave projection")
            return save(path, value, **kwargs)
        with mock.patch.object(owner, "write", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                self.prepare_revision()
        intent = read(engine.execution_dir(self.run_dir, "FEAT-001") / "design-revisions" /
                      self.dispatch["dispatch_id"] / "prepare.json")
        self.assertEqual(self.prepare_revision(), intent)
        self.assertEqual(self.prepare_revision(), intent)
        self.assertEqual(self.candidate.read_bytes(), self.candidate_bytes)

    def test_seal_rejects_unrelated_staged_paths_and_tampered_pair(self):
        self.prepare_revision()
        self.publish_correction()
        stray = self.integration / "unrelated.txt"
        stray.write_text("preserve\n")
        git(self.integration, "add", "-A")
        with self.assertRaises(engine.ExecutionError):
            self.seal()
        self.assertEqual(git(self.integration, "rev-parse", "HEAD"), self.tdd)
        self.assertEqual(stray.read_text(), "preserve\n")
        self.assertIn("unrelated.txt", git(self.integration, "diff", "--cached", "--name-only"))
        git(self.integration, "restore", "--staged", "unrelated.txt")
        stray.unlink()
        design = self.integration / "docs/design.md"
        design.write_bytes(design.read_bytes() + b"\nUnpublished change.\n")
        with self.assertRaises(engine.ExecutionError):
            self.seal()
        self.assertEqual(git(self.integration, "rev-parse", "HEAD"), self.tdd)

    def test_seal_preserves_staged_change_even_when_working_file_matches_head(self):
        self.prepare_revision()
        self.publish_correction()
        path = self.integration / "README.md"
        original = path.read_bytes()
        path.write_text("staged unrelated change\n")
        git(self.integration, "add", "-A")
        path.write_bytes(original)
        index = git(self.integration, "write-tree")
        with self.assertRaises(engine.ExecutionError):
            self.seal()
        self.assertEqual(git(self.integration, "write-tree"), index)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(git(self.integration, "rev-parse", "HEAD"), self.tdd)

    def test_seal_recovers_exact_commit_after_coordinator_write_interruption(self):
        self.prepare_revision()
        self.publish_correction()
        with mock.patch.object(engine, "_save_coordinator", side_effect=RuntimeError("synthetic state interruption")):
            with self.assertRaises(RuntimeError):
                self.seal()
        committed = git(self.integration, "rev-parse", "HEAD")
        self.assertNotEqual(committed, self.tdd)
        self.assertEqual(self.seal()["head"], committed)
        self.assertEqual(git(self.integration, "rev-parse", "HEAD"), committed)

    def test_seal_recovers_commit_before_receipt_and_rejects_changed_replay(self):
        owner = importlib.import_module("workflow_design_revision")
        self.prepare_revision()
        self.publish_correction()
        save = owner._save
        def interrupt(path, value):
            if path.name == "sealed.json":
                raise RuntimeError("synthetic receipt interruption")
            return save(path, value)
        with mock.patch.object(owner, "_save", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                self.seal()
        committed = git(self.integration, "rev-parse", "HEAD")
        self.assertEqual(self.seal()["head"], committed)
        with self.assertRaises(engine.ExecutionError):
            owner.seal_design_revision(self.run_dir, "FEAT-001", self.dispatch["dispatch_id"],
                                       "test-design-correction", "different subject", self.session)

    def test_pending_revision_blocks_replan_and_old_seal_cannot_clear_new_pending(self):
        from workflow_state import write
        self.prepare_revision()
        with self.assertRaises(engine.ExecutionError):
            engine.replan_future(self.run_dir, "FEAT-001", self.fixture.plan, 1)
        self.publish_correction()
        self.seal()
        path = engine.coordinator_path(self.run_dir, "FEAT-001")
        later = {**read(path), "status": "design_revision_pending", "design_revision_id": "e" * 64}
        write(path, later)
        with self.assertRaises(engine.ExecutionError):
            self.seal()
        self.assertEqual(read(path), later)

    def test_prepared_design_revision_preserves_failure_and_seals_exact_pair(self):
        # The old owners cannot bridge this real boundary.
        with self.assertRaises(engine.ExecutionError):
            engine.seal_tdd_base(self.run_dir, "FEAT-001", "cannot reopen TDD")
        with self.assertRaises(engine.ExecutionError):
            engine.seal_feature(self.run_dir, "FEAT-001", "not integrated", "cannot promote")

        prepared = self.prepare_revision()
        self.assertEqual(prepared["revision_id"], self.dispatch["dispatch_id"])
        self.assertEqual(read(engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001"))["status"], "superseded")
        publication = self.publish_correction()
        owner = importlib.import_module("workflow_design_revision")
        sealed = owner.seal_design_revision(
            self.run_dir, "FEAT-001", prepared["revision_id"],
            publication["operation_id"], "fix: admitted design contract", self.session,
        )
        final = git(self.integration, "rev-parse", "HEAD")
        self.assertEqual(sealed["head"], final)
        self.assertEqual(git(self.integration, "rev-parse", "HEAD^"), self.tdd)
        self.assertEqual(git(self.integration, "diff-tree", "--no-commit-id", "--name-only", "-r", final), "docs/design.md")
        self.assertEqual(git(self.integration, "status", "--porcelain"), "")
        self.assertEqual(self.candidate.read_bytes(), self.candidate_bytes)
        self.assertEqual(read(engine.assignment_path(self.run_dir, "FEAT-001", "WAVE-001", "TASK-001")), self.original_assignment)
        self.assertEqual(read(engine.coordinator_path(self.run_dir, "FEAT-001"))["integration_head"], final)
        self.assertEqual(owner.seal_design_revision(
            self.run_dir, "FEAT-001", prepared["revision_id"],
            publication["operation_id"], "fix: admitted design contract", self.session,
        ), sealed)
        self.assertEqual(git(self.integration, "rev-parse", "HEAD"), final)


if __name__ == "__main__":
    unittest.main()
