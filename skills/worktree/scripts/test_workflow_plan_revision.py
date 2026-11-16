#!/usr/bin/env python3
"""Real owner transitions for integrated validation-command recovery."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_workflow_execution as fixture
import workflow_execution as engine
import workflow_validation as validation
import workflow_objectives as objectives
import workflow_reconcile as reconcile
from workflow_state import read, write
from workflow_validation import digest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdlc-classify-failure/scripts"))
import repair_control
from test_repair_control import failure_payload, localized_diagnosis


class IntegratedPlanRevisionTests(unittest.TestCase):
    setUp = fixture.GitLifecycleTests.setUp
    tearDown = fixture.GitLifecycleTests.tearDown
    start_assignment = fixture.GitLifecycleTests.start_assignment
    prepare = fixture.GitLifecycleTests.prepare

    def failed_wave(self):
        app = self.project / "app"
        app.mkdir()
        (app / "contract.txt").write_text("accepted\n")
        fixture.git(self.project, "add", "-A")
        fixture.git(self.project, "commit", "-m", "test: nested contract fixture")
        fixture.seed_objective_impact(self.run_dir, self.project)
        fixture.seed_validation(self.plan)
        self.old_plan = read(validation.plan_path(self.plan))
        self.old_plan["wave_checks"] = [{"id": "nested-contract", "argv": [
            "{python}", "-I", "-c",
            "from pathlib import Path; assert Path('contract.txt').read_text() == 'accepted\\n'",
        ], "execution_policy": {"observation_interval_seconds": 30, "deadline": None}, "environment_keys": []}]
        self.good_command = "import os; os.chdir('app'); " + self.old_plan["wave_checks"][0]["argv"][-1]
        write(validation.plan_path(self.plan), self.old_plan)
        self.prepare()
        engine.seal_tdd_base(self.run_dir, "FEAT-001", "test: base")
        for assignment in engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001"):
            self.start_assignment(assignment, "WAVE-001")
            fixture.finish_task(self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"],
                                "unchanged contract", "review passed", "", summary="Existing contract",
                                status="verified_no_change")
        self.integrated = engine.integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")
        result = validation.produce(self.run_dir, "FEAT-001", "WAVE-001", session="deterministic-coordinator")
        self.assertEqual(result["outcome"], "failed")
        self.failed_receipt = read(Path(result["receipt_path"]))
        self.failed_bytes = Path(result["receipt_path"]).read_bytes()
        self.failed_path = Path(result["receipt_path"])

    def authorize(self, wave_id="WAVE-001"):
        payload = failure_payload(
            proposed="PLAN_DEFECT", commit=self.integrated["integration_head"], lifecycle="integrated_wave",
        )
        payload["evidence_digests"]["evaluate"] = "sha256:" + digest(self.failed_receipt)
        event = repair_control.record_failure_event(self.run_dir, payload)["event"]
        payload = localized_diagnosis(event)
        payload.update(result="plan_defect", bounded_repair_target="Frozen validation command cwd only.",
                       affected_files=["plans/FEAT-001.plan.v1.md.validation.json"])
        self.diagnosis = repair_control.record_diagnosis(self.run_dir, payload)["diagnosis"]
        self.classification = repair_control.classify_failure(self.run_dir, "FEAT-001", event["event_id"],
                                                             self.diagnosis["diagnosis_id"])["classification"]
        self.assertEqual(self.classification["classification"], "PLAN_DEFECT")
        self.attempt = repair_control.begin_remediation(self.run_dir, "FEAT-001",
            self.classification["classification_id"], "localized",
            "Select the nested project cwd." if wave_id == "WAVE-001" else "Remove the extra nonexistent directory prefix before selecting app.",
            "evidence/cwd-counterfactual.md" if wave_id == "WAVE-001" else "evidence/absent-directory-control.md")["attempt"]
        self.failed = engine.fail_wave(self.run_dir, "FEAT-001", wave_id,
            self.classification["classification_id"], self.attempt["dispatch_id"], "nested contract failed")

    def replacement(self, version=2, task_id="TASK-004"):
        self.next_plan = self.plan.with_name(f"FEAT-001.plan.v{version}.md")
        self.next_plan.write_text(self.plan.read_text().replace(f"Plan v{version - 1}", f"Plan v{version}") + f"""

### {task_id}

- Requirements: REQ-001
- Goal: verify the original contract with corrected command scope
- Depends on: TASK-001, TASK-002
- Write claims: exact: app/contract.txt
- Conflict domains: contract:app
- Validation: original nested contract
- Done criteria: unchanged contract passes
- Rollback or stop conditions: stop if acceptance changes
- Diagnosis: {self.diagnosis['diagnosis_id']}
- Regression oracle: {self.diagnosis['regression_oracle']}
""")
        self.next_plan.with_suffix(".md.lock").write_text("locked\n")
        self.new_plan = copy.deepcopy(self.old_plan)
        self.new_plan["wave_checks"][0]["argv"][-1] = self.good_command
        self.new_plan["task_checks"][task_id] = copy.deepcopy(self.new_plan["wave_checks"])
        write(validation.plan_path(self.next_plan), self.new_plan)
        impact, _ = engine.verify_current_objective_impact(self.run_dir, self.project)
        objective = read(self.run_dir / "objective.json")["revisions"][-1]["sha256"]
        self.review = {
            "schema": "workflow/validation-plan-review-v1",
            "classification_id": self.classification["classification_id"],
            "dispatch_id": self.attempt["dispatch_id"],
            "failed_receipt_sha256": digest(self.failed_receipt),
            "prior_validation_plan_sha256": digest(self.old_plan),
            "replacement_validation_plan_sha256": digest(self.new_plan),
            "replacement_plan_sha256": engine.sha256_bytes(self.next_plan.read_bytes()),
            "objective_sha256": objective,
            "requirements_sha256": impact["requirements_sha256"],
            "design_sha256": impact["design_sha256"],
            "criteria_unchanged": True, "source_read_only": True,
            "summary": "Only select the nested cwd; identical contract and assertion, no product changes.",
        }
        self.review_path = self.next_plan.with_suffix(".md.validation-review.json")
        write(self.review_path, self.review)

    def setup_revision(self):
        self.failed_wave()
        self.authorize()
        self.replacement()

    def test_plan_recovery_runs_before_future_tasks_without_product_commit(self):
        self.setup_revision()
        fixture.replan_future(self.run_dir, "FEAT-001", self.next_plan, 1)
        coordinator = read(engine.coordinator_path(self.run_dir, "FEAT-001"))
        self.assertEqual(coordinator["task_waves"]["TASK-004"], "WAVE-002")
        self.assertEqual(coordinator["task_waves"]["TASK-003"], "WAVE-003")
        correction = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-002")[0]
        self.start_assignment(correction, "WAVE-002")
        result = fixture.finish_task(self.run_dir, "FEAT-001", "WAVE-002", "TASK-004",
            "original contract passed", "review passed", "", summary="Command correction verified",
            status="verified_no_change", regression_oracle_evidence={
                "oracle": self.diagnosis["regression_oracle"], "outcome": "passed",
                "evidence_reference": "evidence/corrected-command.md"})
        self.assertIsNone(result["commit"])
        engine.integrate_wave(self.run_dir, "FEAT-001", "WAVE-002")
        fixture.complete_wave(self.run_dir, "FEAT-001", "WAVE-002", "corrected command", session="deterministic-coordinator")
        old = read(engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001"))
        self.assertEqual(old["status"], "failed")
        self.assertEqual(old["combined_failure"], self.failed["combined_failure"])
        self.assertEqual(self.failed_path.read_bytes(), self.failed_bytes)
        self.assertEqual(old["failure_resolution"]["integration_head"], self.integrated["integration_head"])
        self.assertEqual(engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-003")[0]["task_id"], "TASK-003")

    def test_missing_review_and_changed_criteria_block_replan(self):
        self.setup_revision()
        before = engine.coordinator_path(self.run_dir, "FEAT-001").read_bytes()
        self.review_path.unlink()
        with self.assertRaisesRegex(Exception, "validation plan review is required"):
            fixture.replan_future(self.run_dir, "FEAT-001", self.next_plan, 1)
        self.review["criteria_unchanged"] = False
        write(self.review_path, self.review)
        with self.assertRaisesRegex(Exception, "changed authority or acceptance"):
            fixture.replan_future(self.run_dir, "FEAT-001", self.next_plan, 1)
        self.assertEqual(engine.coordinator_path(self.run_dir, "FEAT-001").read_bytes(), before)

    def test_corrective_wave_completes_after_interposed_worker_repair(self):
        self.setup_revision()
        fixture.replan_future(self.run_dir, "FEAT-001", self.next_plan, 1)
        assignment = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-002")[0]
        self.start_assignment(assignment, "WAVE-002")
        fixture.finish_task(self.run_dir, "FEAT-001", "WAVE-002", "TASK-004",
            "original contract passed", "review passed", "", summary="Command correction verified",
            status="verified_no_change", regression_oracle_evidence={
                "oracle": self.diagnosis["regression_oracle"], "outcome": "passed",
                "evidence_reference": "evidence/corrected-command.md"})
        payload = failure_payload(proposed="VALIDATION_DEFECT", lifecycle="active_task",
            commit=self.integrated["integration_head"], component="auxiliary review",
            independent="Optional-path search failed after frozen product checks passed.")
        payload["phase"] = "sdlc-validate-codes"
        event = repair_control.record_failure_event(self.run_dir, payload)["event"]
        child = repair_control.classify_failure(self.run_dir, "FEAT-001", event["event_id"])["classification"]
        attempt = repair_control.begin_remediation(self.run_dir, "FEAT-001", child["classification_id"],
            "localized", "Search only existing review paths.", "evidence/optional-path-control.json")["attempt"]
        engine.integrate_wave(self.run_dir, "FEAT-001", "WAVE-002")
        combined = validation.produce(self.run_dir, "FEAT-001", "WAVE-002", session="deterministic-coordinator")
        self.assertEqual(combined["outcome"], "passed")
        with self.assertRaisesRegex(Exception, "repair authority is invalid"):
            engine.complete_wave(self.run_dir, "FEAT-001", "WAVE-002", combined["receipt_path"])
        resumed = repair_control.complete_remediation(self.run_dir, "FEAT-001", attempt["dispatch_id"],
            "succeeded", "evidence/fresh-review-and-task-verification.json")
        self.assertEqual(resumed["resumed_dispatch_id"], self.attempt["dispatch_id"])
        engine.complete_wave(self.run_dir, "FEAT-001", "WAVE-002", combined["receipt_path"])
        self.assertEqual(self.failed_path.read_bytes(), self.failed_bytes)
        old = read(engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001"))
        self.assertEqual(old["status"], "failed")
        self.assertEqual(old["failure_resolution"]["integration_head"], self.integrated["integration_head"])
        with self.assertRaisesRegex(engine.ExecutionError, "steering readiness is pending"):
            engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-003")
        from test_workflow_fixture import seed_readiness
        seed_readiness(self.run_dir, "FEAT-001", self.next_plan)
        self.assertEqual(engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-003")[0]["task_id"], "TASK-003")
        self.assertEqual(repair_control._load_control(self.run_dir, "FEAT-001")["feature_dispatches"], 2)

    def test_prior_task_checks_cannot_be_replaced(self):
        self.setup_revision()
        self.new_plan["task_checks"]["TASK-001"] = self.new_plan["wave_checks"]
        write(validation.plan_path(self.next_plan), self.new_plan)
        self.review["replacement_validation_plan_sha256"] = digest(self.new_plan)
        write(self.review_path, self.review)
        with self.assertRaisesRegex(Exception, "previously frozen task acceptance"):
            fixture.replan_future(self.run_dir, "FEAT-001", self.next_plan, 1)

    def test_tampered_failed_receipt_blocks_plan_retirement(self):
        self.failed_wave()
        value = read(self.failed_path)
        value["outcomes"][0]["exit_code"] = 0
        write(self.failed_path, value)
        with self.assertRaisesRegex(Exception, "failed producer evidence"):
            self.authorize()
        self.assertEqual(read(engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001"))["status"], "integrated")

    def test_review_drift_blocks_corrective_dispatch(self):
        self.setup_revision()
        fixture.replan_future(self.run_dir, "FEAT-001", self.next_plan, 1)
        archive = self.run_dir / "execution/FEAT-001/validation/revisions" / (digest(self.review) + ".json")
        altered = read(archive)
        altered["source_read_only"] = False
        write(archive, altered)
        with self.assertRaisesRegex(Exception, "frozen validation review changed"):
            engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-002")

    def test_terminal_cancellation_retains_integrated_wave_unchanged(self):
        self.failed_wave()
        wave_path = engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001")
        before = wave_path.read_bytes()
        objectives.fence(self.run_dir, "deterministic-coordinator", "cancelled")
        result = reconcile.cancel_complete(self.run_dir, "deterministic-coordinator")
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(wave_path.read_bytes(), before)
        self.assertTrue(any(row.get("kind") == "unresolved-integration" for row in result["retained_effects"]))
        self.assertEqual(reconcile.cancel_complete(self.run_dir, "deterministic-coordinator"), result)

    def test_nonterminal_replan_still_rejects_unresolved_integration(self):
        self.failed_wave()
        objectives.fence(self.run_dir, "deterministic-coordinator", "steering")
        with self.assertRaisesRegex(Exception, "existing integration journal"):
            reconcile.retire_fenced_waves(self.run_dir, "deterministic-coordinator")

    def test_review_foreign_identity_and_removed_checks_are_rejected(self):
        self.setup_revision()
        before = engine.coordinator_path(self.run_dir, "FEAT-001").read_bytes()
        for key in ("failed_receipt_sha256", "classification_id", "dispatch_id", "objective_sha256",
                    "replacement_plan_sha256", "requirements_sha256", "design_sha256"):
            with self.subTest(key=key):
                wrong = {**self.review, key: "f" * 64}
                write(self.review_path, wrong)
                with self.assertRaisesRegex(Exception, "changed authority or acceptance"):
                    fixture.replan_future(self.run_dir, "FEAT-001", self.next_plan, 1)
                self.assertEqual(engine.coordinator_path(self.run_dir, "FEAT-001").read_bytes(), before)
        write(self.review_path, self.review)
        altered = copy.deepcopy(self.new_plan)
        altered["wave_checks"][0]["id"] = "different-contract"
        write(validation.plan_path(self.next_plan), altered)
        with self.assertRaisesRegex(Exception, "preserve check identities"):
            fixture.replan_future(self.run_dir, "FEAT-001", self.next_plan, 1)

    def test_replan_replay_preserves_one_review_and_failed_receipt(self):
        self.setup_revision()
        first = fixture.replan_future(self.run_dir, "FEAT-001", self.next_plan, 1)
        self.assertEqual(fixture.replan_future(self.run_dir, "FEAT-001", self.next_plan, 1), first)
        self.assertEqual(len(first["validation_revisions"]), 1)
        self.assertEqual(self.failed_path.read_bytes(), self.failed_bytes)

    def test_second_counted_plan_repair_preserves_both_failed_waves(self):
        self.setup_revision()
        first_receipt = self.failed_path
        first_bytes = self.failed_bytes
        self.new_plan["wave_checks"][0]["argv"][-1] = "import os; os.chdir('absent'); " + self.good_command
        write(validation.plan_path(self.next_plan), self.new_plan)
        self.review["replacement_validation_plan_sha256"] = digest(self.new_plan)
        write(self.review_path, self.review)
        fixture.replan_future(self.run_dir, "FEAT-001", self.next_plan, 1)
        assignment = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-002")[0]
        self.start_assignment(assignment, "WAVE-002")
        fixture.finish_task(self.run_dir, "FEAT-001", "WAVE-002", "TASK-004",
            "task contract passed", "review passed", "", summary="Task contract verified",
            status="verified_no_change", regression_oracle_evidence={
                "oracle": self.diagnosis["regression_oracle"], "outcome": "passed",
                "evidence_reference": "evidence/task-contract.md"})
        self.integrated = engine.integrate_wave(self.run_dir, "FEAT-001", "WAVE-002")
        result = validation.produce(self.run_dir, "FEAT-001", "WAVE-002", session="deterministic-coordinator")
        self.assertEqual(result["outcome"], "failed")
        self.failed_receipt = read(Path(result["receipt_path"]))
        repair_control.complete_remediation(self.run_dir, "FEAT-001", self.attempt["dispatch_id"],
                                            "failed_same_blocker", "evidence/still-wrong-directory.md")
        self.authorize("WAVE-002")
        self.plan, self.old_plan = self.next_plan, self.new_plan
        self.replacement(version=3, task_id="TASK-005")
        fixture.replan_future(self.run_dir, "FEAT-001", self.next_plan, 1)
        assignment = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-003")[0]
        self.start_assignment(assignment, "WAVE-003")
        fixture.finish_task(self.run_dir, "FEAT-001", "WAVE-003", "TASK-005",
            "original contract passed", "review passed", "", summary="Command correction verified",
            status="verified_no_change", regression_oracle_evidence={
                "oracle": self.diagnosis["regression_oracle"], "outcome": "passed",
                "evidence_reference": "evidence/corrected-command.md"})
        engine.integrate_wave(self.run_dir, "FEAT-001", "WAVE-003")
        fixture.complete_wave(self.run_dir, "FEAT-001", "WAVE-003", "corrected", session="deterministic-coordinator")
        for name in ("WAVE-001", "WAVE-002"):
            wave = read(engine.wave_path(self.run_dir, "FEAT-001", name))
            self.assertEqual(wave["status"], "failed")
            self.assertEqual(wave["failure_resolution"]["corrective_wave"], "WAVE-003")
        self.assertEqual(first_receipt.read_bytes(), first_bytes)


if __name__ == "__main__":
    unittest.main()
