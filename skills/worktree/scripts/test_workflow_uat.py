"""Optional UAT must not become a runtime dependency of ordinary delivery."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import workflow_uat as uat
import workflow_qualification as qualification
from workflow_state import WorkflowError, digest, write


class UatTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name).resolve() / "run-test"
        self.policy = uat.normalize({"requested": True, "backend": "existing",
            "scope": {"kind": "objective", "executions": []}, "criteria": ["AC-1", "AC-2"]})
        self.head = "a" * 40
        self.executions = ["SLICE-001", "SLICE-002"]
        self.obligation_basis = {c: digest(c) for c in ("AC-1", "AC-2")}
        for patch in (
            mock.patch.object(uat, "context", side_effect=lambda _: ({}, {"sha256": "f" * 64}, self.policy)),
            mock.patch.object(uat, "software_readiness", side_effect=lambda _: {
                "objective_sha256": "f" * 64, "policy_sha256": digest(self.policy),
                "executions": self.executions, "missing": []}),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def event(self, status, campaign, **kw):
        criteria = kw.pop("criteria", ["AC-1", "AC-2"])
        return uat.record(self.run, status, self.head, reason="fixture outcome",
                          campaign=campaign, criteria=criteria,
                          obligation_basis={c: self.obligation_basis[c] for c in criteria}, **kw)

    def test_default_is_not_requested_even_with_an_environment_elsewhere(self):
        self.policy = uat.normalize()
        write(self.run / "uat/readiness.json", {"environment": "provided"})
        value = uat.require_delivery(self.run, self.head)
        self.assertEqual(value["status"], "not_requested")
        self.assertTrue(value["delivery_eligible"])

    def test_selecting_backend_without_request_is_rejected(self):
        for backend in ("existing", "container", "nebius"):
            with self.subTest(backend=backend), self.assertRaises(WorkflowError):
                uat.normalize({**uat.normalize(), "backend": backend})

    def test_milestone_needs_explicit_execution_scope(self):
        with self.assertRaises(WorkflowError):
            uat.normalize({**self.policy, "scope": {"kind": "milestone", "executions": []}})

    def test_unrequested_campaign_launch_is_refused(self):
        self.policy = uat.normalize()
        with mock.patch.object(uat, "check_stage"), mock.patch.object(qualification, "command") as runner:
            with self.assertRaisesRegex(WorkflowError, "opt-in"):
                qualification.qualify(self.run, "OBJECTIVE", {})
        runner.assert_not_called()

    def test_partial_scope_returns_pending_without_runtime_or_provisioning(self):
        with mock.patch.object(uat, "check_stage"), mock.patch.object(uat, "software_readiness", return_value={"missing": ["implemented_scope"]}), \
                mock.patch.object(qualification, "target") as target, mock.patch.object(qualification, "command") as runner:
            result = qualification.qualify(self.run, "OBJECTIVE", {})
        self.assertEqual(result["status"], "pending")
        self.assertTrue(result["delivery_eligible"])
        target.assert_not_called()
        runner.assert_not_called()

    def test_missing_environment_stays_pending_for_each_explicit_backend(self):
        for backend in ("existing", "container", "nebius"):
            self.policy = {**self.policy, "backend": backend}
            with mock.patch.object(uat, "check_stage"), mock.patch.object(qualification, "target", return_value={"head": self.head}), \
                    mock.patch.object(qualification, "command") as runner:
                value = qualification.qualify(self.run, "OBJECTIVE", {})
            self.assertEqual(value["status"], "pending")
            self.assertIn("environment", value["missing"])
            self.assertTrue(value["delivery_eligible"])
            runner.assert_not_called()

    def test_cleaned_incomplete_trial_does_not_block_delivery(self):
        self.event("running", "trial-1", cleanup_proven=False)
        self.event("incomplete", "trial-1", cleanup_proven=True)
        self.assertEqual(uat.require_delivery(self.run, self.head)["status"], "incomplete")

    def test_missing_prerequisite_does_not_erase_a_prior_assertion_failure(self):
        self.event("failed", "trial-1")
        self.event("running", "trial-2", cleanup_proven=False)
        self.event("incomplete", "trial-2", cleanup_proven=True)
        self.event("pending", None)
        with self.assertRaises(WorkflowError):
            uat.require_delivery(self.run, self.head)
        self.assertEqual(uat.outcome(self.run)["status"], "failed")

    def test_narrow_pass_does_not_clear_larger_failed_scope(self):
        self.event("failed", "trial-1")
        self.executions = ["SLICE-001"]
        receipt = {"head": self.head, "objective_sha256": "f" * 64, "executions": self.executions,
                   "criteria": {"AC-1": {}}, "obligation_basis": {"AC-1": self.obligation_basis["AC-1"]}}
        with mock.patch.object(qualification, "verify_receipt", return_value=receipt):
            self.event("passed", "trial-2", qualification={"fixture": True}, criteria=["AC-1"])
            self.assertFalse(uat.outcome(self.run)["delivery_eligible"])

    def test_passing_other_trial_cannot_erase_cleanup_debt(self):
        self.event("incomplete", "trial-1", cleanup_proven=False)
        receipt = {"head": self.head, "objective_sha256": "f" * 64, "executions": self.executions,
                   "criteria": {"AC-1": {}, "AC-2": {}}, "obligation_basis": self.obligation_basis}
        with mock.patch.object(qualification, "verify_receipt", return_value=receipt):
            self.event("passed", "trial-2", qualification={"fixture": True})
            self.assertFalse(uat.outcome(self.run)["delivery_eligible"])

    def test_verified_covering_pass_resolves_defect_but_not_a_changed_candidate(self):
        self.event("failed", "trial-1")
        receipt = {"head": self.head, "objective_sha256": "f" * 64, "executions": self.executions,
                   "criteria": {"AC-1": {}, "AC-2": {}}, "obligation_basis": self.obligation_basis}
        with mock.patch.object(qualification, "verify_receipt", return_value=receipt):
            self.event("passed", "trial-2", qualification={"fixture": True})
            self.assertEqual(uat.require_delivery(self.run, self.head)["status"], "passed")
            self.assertEqual(uat.require_delivery(self.run, "b" * 40)["status"], "pending")

    def test_redefined_criterion_cannot_clear_original_defect(self):
        self.event("failed", "trial-1")
        receipt = {"head": self.head, "objective_sha256": "f" * 64,
                   "executions": self.executions, "criteria": {"AC-1": {}, "AC-2": {}},
                   "obligation_basis": {"AC-1": "b" * 64, "AC-2": "c" * 64}}
        self.obligation_basis = receipt["obligation_basis"]
        with mock.patch.object(qualification, "verify_receipt", return_value=receipt):
            self.event("passed", "trial-2", qualification={"fixture": True})
            self.assertFalse(uat.outcome(self.run)["delivery_eligible"])
            with self.assertRaises(WorkflowError):
                uat.require_delivery(self.run, self.head)

    def test_late_handoff_preserves_program_criteria_without_runtime(self):
        program = {"slices": [
            {"id": "SLICE-001", "criteria": ["AC-build"], "regression_criteria": ["AC-regression"]},
            {"id": "SLICE-002", "criteria": ["AC-second"], "regression_criteria": []}],
            "phases": [{"id": "acceptance", "exit_criteria": ["AC-journey"]}]}
        program_sha = digest(program)
        write(self.run / "program" / (program_sha + ".json"), program)
        snapshot = {"inputs": {"program_sha256": program_sha}, "acceptance_criteria": ["The application is usable."]}
        self.policy["criteria"] = []
        with mock.patch("workflow_program.accepted", return_value=({}, {}, snapshot)), \
                mock.patch("workflow_objectives._git", return_value="d" * 40), \
                mock.patch.object(qualification, "command", side_effect=AssertionError("no runtime setup")):
            value = uat.handoff(self.run, self.head, repository=self.run)
            self.assertEqual(value["criteria"], ["AC-build", "AC-journey", "AC-regression", "AC-second"])
            self.assertEqual(value["outcome"]["status"], "pending")
            self.assertTrue(value["outcome"]["delivery_eligible"])
            self.policy["scope"] = {"kind": "milestone", "executions": ["SLICE-001"]}
            self.assertEqual(uat.handoff(self.run, self.head, repository=self.run)["criteria"], ["AC-build", "AC-regression"])
            self.policy["criteria"] = ["AC-journey"]
            self.assertEqual(uat.handoff(self.run, self.head, repository=self.run)["criteria"], ["AC-journey"])

    def test_late_handoff_rejects_changed_program(self):
        snapshot = {"inputs": {"program_sha256": "a" * 64}, "acceptance_criteria": ["Summary"]}
        write(self.run / "program" / ("a" * 64 + ".json"), {"changed": True})
        with mock.patch("workflow_program.accepted", return_value=({}, {}, snapshot)), self.assertRaisesRegex(WorkflowError, "program definition changed"):
            uat.handoff(self.run, self.head, repository=self.run)

    def test_unrequested_handoff_keeps_direct_objective_criteria(self):
        self.policy = uat.normalize()
        snapshot = {"inputs": None, "acceptance_criteria": ["The application is usable."]}
        with mock.patch("workflow_program.accepted", return_value=({}, {}, snapshot)), \
                mock.patch("workflow_objectives._git", return_value="d" * 40):
            value = uat.handoff(self.run, self.head, repository=self.run)
        self.assertEqual(value["criteria"], snapshot["acceptance_criteria"])
        self.assertEqual(value["missing_prerequisites"], [])
        self.assertEqual(value["outcome"]["status"], "not_requested")

    def test_pass_receipt_must_match_recorded_criterion_identities(self):
        receipt = {"head": self.head, "objective_sha256": "f" * 64,
                   "executions": self.executions, "criteria": {"AC-1": {}, "AC-2": {}},
                   "obligation_basis": {"AC-1": "b" * 64, "AC-2": "c" * 64}}
        with mock.patch.object(qualification, "verify_receipt", return_value=receipt), self.assertRaises(WorkflowError):
            self.event("passed", "trial-1", qualification={"fixture": True})

    def test_unsupported_history_is_preserved_without_a_compatibility_path(self):
        event = self.event("failed", "trial-1")
        (self.run / "uat/events" / (digest(event) + ".json")).unlink()
        event["schema"] = "workflow/uat-event-v1"
        path = self.run / "uat/events" / (digest(event) + ".json")
        write(path, event)
        before = path.read_bytes()
        with self.assertRaisesRegex(WorkflowError, "unsupported UAT history"):
            uat.outcome(self.run)
        self.assertEqual(path.read_bytes(), before)

    def test_optional_task_check_needs_no_worker_runtime_receipt(self):
        import workflow_task_acceptance as acceptance
        policy = {"criteria": [{"id": "optional-journey", "purpose": "uat", "disposition": "check"}]}
        with mock.patch.object(acceptance, "assignment_policy", return_value=policy), mock.patch.object(acceptance, "_root") as runtime:
            self.assertIsInstance(acceptance.verify_task({}, 1, "f" * 40), str)
        runtime.assert_not_called()

    def test_optional_debt_does_not_remove_required_implementation_debt(self):
        import workflow_task_acceptance as acceptance
        core = {"purpose": "implementation"}
        with mock.patch.object(acceptance, "read", return_value={"owner": "agentic-sdlc"}), \
                mock.patch.object(acceptance, "obligations", return_value={"core": core, "later": {"purpose": "uat"}}), \
                mock.patch.object(acceptance, "require_quiescent"), mock.patch.object(acceptance, "_git", return_value="t" * 40), \
                mock.patch.object(acceptance, "require_resolved") as required:
            acceptance.require_final(self.run, self.run, self.head, phase_without_tasks=True)
        required.assert_called_once_with(self.run, {"core": core}, "t" * 40)

    def test_correctness_consumer_cannot_depend_on_optional_uat(self):
        import workflow_task_acceptance as acceptance
        criterion = {"id": "journey", "purpose": "uat", "disposition": "deferred",
                     "reason": "Whole application acceptance", "prerequisite": "Completed scope",
                     "resolution_stage": "uat", "check": {"kind": "command", "definition": {
                         "id": "journey", "argv": ["{python}", "-I", "-c", "pass"],
                         "execution_policy": {"observation_interval_seconds": 30, "deadline": None}, "environment_keys": []}}}
        policy = {"schema": acceptance.SCHEMA, "tasks": {
            "TASK-001": {"requires": ["journey"], "criteria": [criterion]}}}
        with self.assertRaisesRegex(WorkflowError, "correctness prerequisites"):
            acceptance.validate(policy, ["TASK-001"])

    def test_aggregate_testing_does_not_reopen_worker_dispatch(self):
        with mock.patch("workflow_objectives.validate_run", return_value=({"status": "executing", "shared_admission": True}, {})), \
                mock.patch("workflow_projects.get", return_value={"state": "aggregation-ready"}), \
                mock.patch("workflow_automatic._candidate"), mock.patch("workflow_objectives.check_dispatch") as dispatch:
            uat.check_stage(self.run, "aggregate")
        dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
