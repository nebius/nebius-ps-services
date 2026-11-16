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
import workflow_uat as uat
from workflow_state import WorkflowError, digest, read, write

RUNNER = '''import json, os, subprocess, sys
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
mode = os.environ.get("QUAL_MODE", "pass")
value = {"head":head, "artifact_sha256":"a"*64, "configuration_sha256":"b"*64}
if sys.argv[1] == "observe":
    value.update(environment="test", trial="trial-2" if mode=="fresh" else "trial-1", reset="reset-2" if mode=="fresh" else "reset-1", intervened=mode=="intervened", writers_quiescent=True)
    if mode=="wrong-artifact": value["artifact_sha256"]="c"*64
elif sys.argv[1] == "cleanup":
    value={"head":head, "backend":"existing", "owner_receipt_sha256":"c"*64, "writers_quiescent":True, "owned_resources_absent":mode!="cleanup-fail"}
elif sys.argv[1] == "criterion":
    assert sum([1,2,3]) == 6
    value={"criterion":sys.argv[3], "tests":1, "passed":1, "failed":0, "skipped":0}
    if mode=="skip": value.update(passed=0,skipped=1)
    if mode=="labels": value={"status":"passed"}
    if mode=="exit-one": value.update(passed=0,failed=1)
    if mode=="infra-error": sys.exit(1)
print(json.dumps(value))
if mode == "exit-one" and sys.argv[1] == "criterion": sys.exit(1)
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
        self.expected.update(stage="promoted", target_identity={"head": self.expected["head"]})
        self.expected["obligation_basis"] = {c: {"source_sha256": "d" * 64, "coverage_sha256": "e" * 64}
                                             for c in self.expected["criteria"]}
        self.plan = {"schema": q.SCHEMA, "execution_id": "OBJECTIVE", "runner": "runner.py",
                     "runner_sha256": hashlib.sha256(RUNNER.encode()).hexdigest(), "criteria": self.expected["criteria"],
                     "environment": {"id": "test", "resource": "fixture", "nonproduction": True},
                     "operations": ["test"], "execution_policy": {"observation_interval_seconds": 30, "deadline": None}, "environment_keys": ["QUAL_MODE"], "live": True, "backend": "existing"}
        self.planpath = self.root / "locked-plan.md"
        self.planpath.write_text("Qualification plan SHA256: " + digest(self.plan) + "\n")
        write(self.run / "execution/SLICE-001/coordinator.json", {"plan_path": str(self.planpath)})
        from workflow_task_acceptance import digest as acceptance_digest
        policy = {"schema": "workflow/task-acceptance-policy-v3", "tasks": {}}
        basis = {"owner": "agentic-sdlc", "execution_id": "SLICE-001", "task_acceptance_policy": policy,
                 "task_acceptance_policy_sha256": acceptance_digest(policy), "plan_digest": "f" * 64}
        relative = "bases/SLICE-001/" + acceptance_digest(basis) + ".json"
        write(self.run / relative, basis)
        write(self.run / "objective-impact/execution/SLICE-001.json",
              {"schema": "workflow/execution-basis-pointer-v2", "path": relative, "sha256": acceptance_digest(basis)})
        write(self.run / "run.json", {"owner": "agentic-sdlc", "git_root": str(self.project)})
        journal = self.run / "execution/SLICE-001/journals/coordinator.jsonl"
        journal.parent.mkdir(parents=True)
        journal.write_text('{"event": "prepared"}\n')
        policy = uat.normalize({"requested": True, "backend": "existing", "scope": {"kind": "objective", "executions": []}, "criteria": self.expected["criteria"]})
        readiness = {"objective_sha256": "f" * 64, "policy_sha256": digest(policy), "executions": ["SLICE-001"], "missing": []}
        for patch in (mock.patch.object(uat, "context", return_value=({}, {"sha256": "f" * 64}, policy)),
                      mock.patch.object(uat, "software_readiness", return_value=readiness),
                      mock.patch.object(uat, "readiness", return_value=readiness),
                      mock.patch.object(uat, "check_stage"), mock.patch.object(q, "target", return_value=self.expected),
                      mock.patch.object(q, "accepted", return_value=({}, {"sha256": "f" * 64}, {"inputs": self.expected["inputs"]})),
                      mock.patch.dict(os.environ, {"QUAL_MODE": "pass"})):
            patch.start()
            self.addCleanup(patch.stop)

    def qualify(self):
        result = q.qualify(self.run, "OBJECTIVE", self.plan)
        return {k: result[k] for k in ("receipt", "sha256")}

    def test_executes_every_criterion_and_admits_exact_receipt(self):
        reference = self.qualify()
        receipt = q.verify_receipt(self.run, reference)
        self.assertEqual(len(receipt["commands"]), 6)
        self.assertEqual(set(receipt["criteria"]), {"AC-001", "AC-002"})
        self.assertNotIn("QUAL_MODE", json.dumps(receipt["commands"]))

    def test_unbound_elapsed_deadline_is_rejected_before_harness_launch(self):
        self.plan["execution_policy"]["deadline"] = {
            "seconds": 1, "basis": "backend_constraint", "reference": "fabricated"}
        with mock.patch.object(q, "command") as command, self.assertRaisesRegex(WorkflowError, "independently admitted"):
            self.qualify()
        command.assert_not_called()

    def test_active_campaign_cannot_be_cleaned_by_recovery(self):
        original = q.command
        observed = []
        def command(project, plan, action, criterion=None, **options):
            if action == "criterion" and not observed:
                path = next((self.run / "qualification/OBJECTIVE/promoted").glob("*.json"))
                candidate = {"receipt": str(path.relative_to(self.run)), "sha256": digest(read(path)),
                             "checkout": str(self.project)}
                with mock.patch("workflow_objectives.authorize_coordinator"), self.assertRaisesRegex(WorkflowError, "writers are active"):
                    q.recover_cleanup(self.run, candidate)
                observed.append(True)
            return original(project, plan, action, criterion, **options)
        with mock.patch.object(q, "command", side_effect=command):
            self.qualify()
        self.assertEqual(observed, [True])

    def test_fence_during_cleanup_prevents_acceptance_but_preserves_cleanup(self):
        original = q.command
        fenced = False
        def command(project, plan, action, criterion=None, **options):
            nonlocal fenced
            result = original(project, plan, action, criterion, **options)
            if action == "cleanup":
                fenced = True
            return result
        def authority(*args):
            if fenced:
                raise WorkflowError("QUALIFICATION_BLOCKED", "campaign fenced")
        with mock.patch.object(q, "command", side_effect=command), mock.patch.object(uat, "check_stage", side_effect=authority), self.assertRaisesRegex(WorkflowError, "fenced"):
            self.qualify()
        root = self.run / "qualification/OBJECTIVE/promoted"
        self.assertFalse((root / "produced.json").exists())
        receipt = read(next(root.glob("*.json")))
        self.assertTrue(receipt["cleanup_proven"])
        self.assertEqual(receipt["status"], "incomplete")

    def test_phase_cannot_start_an_early_uat_campaign(self):
        self.expected.update(kind="external", basis_sha256=None)
        self.plan["execution_id"] = "PHASE:foundation"
        with self.assertRaisesRegex(WorkflowError, "accepted objective"), mock.patch.object(q, "command") as runner:
            q.qualify(self.run, "PHASE:foundation", self.plan)
        runner.assert_not_called()

    def test_delivery_qualification_cannot_omit_prepared_execution(self):
        (self.run / "execution/SLICE-001/coordinator.json").unlink()
        with self.assertRaises(WorkflowError), mock.patch.object(q, "command") as command:
            self.qualify()
        command.assert_not_called()

    def test_aggregate_qualification_executes_and_keeps_its_distinct_target(self):
        promoted = self.qualify()
        prior = self.expected["head"]
        (self.project / "combined.txt").write_text("aggregate input")
        for args in (("add", "-A"), ("commit", "-m", "aggregate fixture")):
            subprocess.run(["git", "-C", str(self.project), *args], check=True, capture_output=True)
        head = subprocess.check_output(["git", "-C", str(self.project), "rev-parse", "HEAD"], text=True).strip()
        identity = {"head": head, "child_head": prior, "source_head": prior,
                    "tree": "a" * 40, "reservation": "fixture", "run_id": self.run.name}
        self.expected.update(stage="aggregate", target_identity=identity, head=head)
        result = q.qualify(self.run, "OBJECTIVE", self.plan, stage="aggregate")
        reference = {key: result[key] for key in ("receipt", "sha256")}
        receipt = q.verify_receipt(self.run, reference)
        self.assertEqual(receipt["head"], head)
        self.assertEqual(receipt["target_identity"], identity)
        self.assertIn("/aggregate/", reference["receipt"])
        self.assertNotEqual(q.verify_receipt(self.run, promoted, current=False)["head"], head)
        self.expected["target_identity"] = {**identity, "reservation": "changed"}
        with self.assertRaises(WorkflowError):
            q.verify_receipt(self.run, reference)
        self.assertEqual(q.verify_receipt(self.run, reference, current=False)["head"], head)

    def test_labels_skips_wrong_artifacts_and_intervention_cannot_pass(self):
        for mode in ("labels", "skip", "wrong-artifact", "intervened"):
            with self.subTest(mode=mode), mock.patch.dict(os.environ, {"QUAL_MODE": mode}), self.assertRaises(WorkflowError):
                self.qualify()
        receipts = [read(p) for p in (self.run / "qualification/OBJECTIVE/promoted").glob("*.json")]
        self.assertTrue(all(v["status"] in {"failed", "incomplete"} for v in receipts))

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
        write(self.run / "uat" / ("plan-" + "f" * 64 + ".json"), {**self.plan, "runner_sha256": "0" * 64})
        with self.assertRaises(WorkflowError):
            self.qualify()
        plan = {**self.plan, "criteria": ["AC-001"]}
        with self.assertRaises(WorkflowError):
            q.validate_plan(plan, self.expected)

    def test_revoked_authority_prevents_runner_launch(self):
        with mock.patch.object(uat, "check_stage", side_effect=WorkflowError("FENCED", "revoked")), \
                mock.patch.object(q, "command") as runner, self.assertRaises(WorkflowError):
            self.qualify()
        runner.assert_not_called()

    def test_failed_trial_cannot_be_reused_as_clean_evidence(self):
        with mock.patch.dict(os.environ, {"QUAL_MODE": "skip"}), self.assertRaises(WorkflowError):
            self.qualify()
        with self.assertRaisesRegex(WorkflowError, "fresh reset"):
            self.qualify()

    def test_failed_trial_cannot_be_reused_across_qualification_stages(self):
        with mock.patch.dict(os.environ, {"QUAL_MODE": "skip"}), self.assertRaises(WorkflowError):
            self.qualify()
        self.expected.update(stage="aggregate")
        with self.assertRaisesRegex(WorkflowError, "fresh reset"):
            q.qualify(self.run, "OBJECTIVE", self.plan, stage="aggregate")
        with mock.patch.dict(os.environ, {"QUAL_MODE": "fresh"}):
            result = q.qualify(self.run, "OBJECTIVE", self.plan, stage="aggregate")
            reference = {key: result[key] for key in ("receipt", "sha256")}
            self.assertEqual(q.verify_receipt(self.run, reference)["before"]["trial"], "trial-2")

    def test_first_criterion_attempt_and_trial_are_durable_before_execution(self):
        original = q.command
        def crash(project, plan, action, criterion=None, **options):
            if action == "criterion":
                receipt = read(next((self.run / "qualification/OBJECTIVE/promoted").glob("*.json")))
                self.assertEqual(receipt["before"]["trial"], "trial-1")
                self.assertEqual(receipt["trial_id"], "trial-1")
                self.assertEqual(receipt["attempted_criterion"], "AC-001")
                self.assertEqual(receipt["status"], "incomplete")
                error = KeyboardInterrupt()
                error.execution_evidence = {"local_group_quiescent": True}
                raise error
            return original(project, plan, action, criterion, **options)
        with mock.patch.object(q, "command", side_effect=crash), self.assertRaises(KeyboardInterrupt):
            self.qualify()
        with self.assertRaisesRegex(WorkflowError, "fresh reset"):
            self.qualify()

    def test_nonzero_assertion_exit_blocks_affected_delivery(self):
        with mock.patch.dict(os.environ, {"QUAL_MODE": "exit-one"}), self.assertRaises(WorkflowError):
            self.qualify()
        self.assertEqual(uat.outcome(self.run)["status"], "failed")
        self.assertFalse(uat.outcome(self.run)["delivery_eligible"])

    def test_cleanup_requires_owner_observation_not_just_process_exit(self):
        with mock.patch.dict(os.environ, {"QUAL_MODE": "cleanup-fail"}), self.assertRaises(WorkflowError):
            self.qualify()
        self.assertEqual(uat.outcome(self.run)["status"], "incomplete")
        self.assertFalse(uat.outcome(self.run)["delivery_eligible"])

    def test_nonzero_without_assertion_results_is_incomplete_after_cleanup(self):
        with mock.patch.dict(os.environ, {"QUAL_MODE": "infra-error"}), self.assertRaises(WorkflowError):
            self.qualify()
        result = uat.outcome(self.run)
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(result["delivery_eligible"])

    def test_cleanup_recovery_clears_only_original_trial_and_preserves_history(self):
        with mock.patch.dict(os.environ, {"QUAL_MODE": "cleanup-fail"}), self.assertRaises(WorkflowError):
            self.qualify()
        path = next((self.run / "qualification/OBJECTIVE/promoted").glob("*.json"))
        original = path.read_bytes()
        candidate = {"receipt": str(path.relative_to(self.run)), "sha256": digest(read(path)), "checkout": str(self.project)}
        with mock.patch("workflow_objectives.authorize_coordinator"):
            value = q.recover_cleanup(self.run, candidate)
        self.assertTrue(value["delivery_eligible"])
        self.assertEqual(value["status"], "incomplete")
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(list((self.run / "uat/recovery").glob("*.json")))

    def test_cleanup_recovery_does_not_erase_assertion_failure(self):
        original = q.command
        def fail(project, plan, action, criterion=None, **options):
            if action == "cleanup":
                error = WorkflowError("QUALIFICATION_BLOCKED", "fixture cleanup failure")
                error.execution_evidence = {"local_group_quiescent": True}
                raise error
            return original(project, plan, action, criterion, **options)
        with mock.patch.dict(os.environ, {"QUAL_MODE": "exit-one"}), mock.patch.object(q, "command", side_effect=fail), self.assertRaises(WorkflowError):
            self.qualify()
        path = next((self.run / "qualification/OBJECTIVE/promoted").glob("*.json"))
        candidate = {"receipt": str(path.relative_to(self.run)), "sha256": digest(read(path)), "checkout": str(self.project)}
        with mock.patch("workflow_objectives.authorize_coordinator"):
            value = q.recover_cleanup(self.run, candidate)
        self.assertFalse(value["delivery_eligible"])
        self.assertEqual(value["status"], "failed")

    def test_recovering_old_cleanup_does_not_resurrect_repaired_defect(self):
        original = q.command
        def fail(project, plan, action, criterion=None, **options):
            if action == "cleanup":
                error = WorkflowError("QUALIFICATION_BLOCKED", "fixture cleanup failure")
                error.execution_evidence = {"local_group_quiescent": True}
                raise error
            return original(project, plan, action, criterion, **options)
        with mock.patch.dict(os.environ, {"QUAL_MODE": "exit-one"}), mock.patch.object(q, "command", side_effect=fail), self.assertRaises(WorkflowError):
            self.qualify()
        path = next((self.run / "qualification/OBJECTIVE/promoted").glob("*.json"))
        candidate = {"receipt": str(path.relative_to(self.run)), "sha256": digest(read(path)), "checkout": str(self.project)}
        with mock.patch.dict(os.environ, {"QUAL_MODE": "fresh"}), self.assertRaisesRegex(WorkflowError, "unknown effects"):
            self.qualify()
        with mock.patch("workflow_objectives.authorize_coordinator"):
            q.recover_cleanup(self.run, candidate)
        with mock.patch.dict(os.environ, {"QUAL_MODE": "fresh"}):
            self.qualify()
        with mock.patch("workflow_objectives.authorize_coordinator"):
            value = q.recover_cleanup(self.run, candidate)
        self.assertTrue(value["delivery_eligible"])
        self.assertEqual(value["status"], "passed")

    def test_receipt_verification_does_not_reopen_a_cleaned_environment(self):
        reference = self.qualify()
        with mock.patch.object(q, "command", side_effect=AssertionError("retired environment")):
            self.assertEqual(q.verify_receipt(self.run, reference)["status"], "passed")

    def test_changed_acceptance_identity_does_not_resolve_executed_failure(self):
        with mock.patch.dict(os.environ, {"QUAL_MODE": "exit-one"}), self.assertRaises(WorkflowError):
            self.qualify()
        original = dict(self.expected["obligation_basis"])
        failed = next(event for event in uat._events(self.run) if event["status"] == "failed")
        self.assertEqual(failed["obligation_basis"], original)
        self.expected["obligation_basis"] = {
            key: {**value, "source_sha256": "e" * 64} for key, value in original.items()}
        with mock.patch.dict(os.environ, {"QUAL_MODE": "fresh"}):
            self.qualify()
        self.assertFalse(uat.outcome(self.run)["delivery_eligible"])

    def test_receipt_verification_never_recreates_missing_frozen_plan(self):
        reference = self.qualify()
        path = self.run / "uat" / ("plan-" + self.expected["objective_sha256"] + ".json")
        path.unlink()
        with self.assertRaisesRegex(WorkflowError, "plan is missing"):
            q.verify_receipt(self.run, reference)
        self.assertFalse(path.exists())

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


class ObjectiveTargetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.run = self.root / "run-test"
        self.git("init", "-b", "candidate")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.project / "app.txt").write_text("first capability")
        self.git("add", "-A")
        self.git("commit", "-m", "first capability")
        self.first = self.git("rev-parse", "HEAD")
        (self.project / "app.txt").write_text("complete journey")
        self.git("commit", "-am", "complete journey")
        self.final = self.git("rev-parse", "HEAD")
        self.rows = {"FEAT-002": self.first, "FEAT-001": self.final}
        self.policy = uat.normalize({"requested": True, "backend": "existing",
            "scope": {"kind": "objective", "executions": []}, "criteria": []})
        self.program = None
        self.snapshot = {"acceptance_criteria": ["AC-journey"], "inputs": None,
                         "completion": {"live_operations": ["test"]}}
        for patch in (
            mock.patch.object(q, "accepted", side_effect=lambda _: ({"project_root": str(self.project)}, {"sha256": "f" * 64}, self.snapshot)),
            mock.patch.object(q, "verify_inputs", side_effect=lambda *_: self.program),
            mock.patch.object(uat, "context", side_effect=lambda _: ({}, {}, self.policy)),
            mock.patch.object(uat, "software_readiness", side_effect=lambda _: {"executions": sorted(self.rows), "missing": []}),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.project), *args], text=True, stderr=subprocess.DEVNULL).strip()

    def records(self):
        for name, head in self.rows.items():
            write(self.run / "execution" / name / "coordinator.json",
                  {"execution_id": name, "promoted_head": head, "status": "done"})
            write(self.run / "objective-impact/execution" / (name + ".json"), {"sha256": "b" * 64})

    def test_ready_objective_uses_final_candidate_not_execution_id_order(self):
        self.records()
        value = q.target(self.run, "OBJECTIVE")
        self.assertEqual(value["head"], self.final)
        self.assertEqual(value["project"], self.project)

    def test_feature_delivery_uses_retained_scoped_checkout(self):
        retained = self.root / "retained"
        self.git("worktree", "add", "--detach", str(retained), self.final)
        self.git("checkout", "--detach", self.first)
        # The project can be a directory inside the retained repository.
        (retained / "component").mkdir()
        self.records()
        path = self.run / "execution/FEAT-001/coordinator.json"
        write(path, {**read(path), "delivered_checkout": str(retained), "project_scope": "component"})
        value = q.target(self.run, "OBJECTIVE")
        self.assertEqual(value["head"], self.final)
        self.assertEqual(value["project"], retained / "component")

    def test_aggregate_uses_owner_candidate_without_retired_checkouts(self):
        self.records()
        path = self.run / "execution/FEAT-001/coordinator.json"
        write(path, {**read(path), "delivered_checkout": str(self.root / "retired"), "project_scope": "."})
        identity = {"head": self.final, "reservation": "aggregate-fixture"}
        with mock.patch("workflow_automatic._candidate", return_value=(identity, self.project, {"scope": "."})), \
             mock.patch.object(q, "_promoted_candidate", side_effect=AssertionError("aggregate owner is authoritative")):
            value = q.target(self.run, "OBJECTIVE", stage="aggregate")
        self.assertEqual(value["head"], self.final)
        self.assertEqual(value["target_identity"], identity)

    def test_default_objective_includes_phase_only_acceptance(self):
        self.rows = {"SLICE-001": self.final}
        self.records()
        self.program = {"definition": {"slices": [{"id": "SLICE-001", "phase": "build", "criteria": ["AC-build"], "regression_criteria": []}],
            "phases": [{"id": "build", "exit_criteria": ["AC-build"]}, {"id": "acceptance", "exit_criteria": ["AC-journey"]}]},
            "bundle": {"obligation_basis": {"AC-build": "b" * 64, "AC-journey": "c" * 64}}}
        self.assertEqual(q.target(self.run, "OBJECTIVE")["criteria"], ["AC-build", "AC-journey"])
        self.policy["scope"] = {"kind": "milestone", "executions": ["SLICE-001"]}
        self.assertEqual(q.target(self.run, "OBJECTIVE")["criteria"], ["AC-build"])
        self.policy["criteria"] = ["AC-journey"]
        self.assertEqual(q.target(self.run, "OBJECTIVE")["criteria"], ["AC-journey"])

    def test_uncombined_promoted_branches_are_not_a_ready_candidate(self):
        self.git("checkout", "-b", "independent", self.first)
        (self.project / "other.txt").write_text("independent capability")
        self.git("add", "-A")
        self.git("commit", "-m", "independent capability")
        self.rows["FEAT-002"] = self.git("rev-parse", "HEAD")
        self.records()
        with self.assertRaises(WorkflowError):
            q.target(self.run, "OBJECTIVE")
        self.assertFalse((self.run / "uat/events").exists())

    def squashed_program(self):
        self.git("checkout", "-b", "slice-one", self.first)
        (self.project / "app.txt").write_text("slice one")
        self.git("commit", "-am", "original slice")
        promoted = self.git("rev-parse", "HEAD")
        self.git("checkout", "-b", "delivered", self.first)
        self.git("merge", "--squash", "slice-one")
        self.git("commit", "-m", "squashed delivery")
        merged = self.git("rev-parse", "HEAD")
        self.assertNotEqual(self.git("merge-base", promoted, merged), promoted)
        (self.project / "app.txt").write_text("second slice")
        self.git("commit", "-am", "second slice")
        self.final = self.git("rev-parse", "HEAD")
        self.rows = {"SLICE-001": promoted, "SLICE-002": self.final}
        self.program = {"definition": {"slices": [
            {"id": name, "criteria": ["AC-journey"], "regression_criteria": []} for name in self.rows],
            "phases": [{"id": "build", "exit_criteria": ["AC-journey"]}]},
            "bundle": {"obligation_basis": {"AC-journey": "b" * 64}}}
        self.records()
        review = {"admission": {"receipt": "fixture", "sha256": "b" * 64}}
        intent = {"head": promoted}
        submission = {"status": "merged", "result_sha": merged, "review_sha256": digest(review), **intent}
        root = self.run / "delivery/SLICE-001"
        write(root / "submission.json", submission)
        completion = {"schema": "workflow/delivery-completion-v1", "status": "result-verified",
                      "execution_id": "SLICE-001", "submission_sha256": digest(submission),
                      "result_sha": merged, "review": review, "intent": intent,
                      "ci": {"result_sha": merged, "outcome": "ci-verified"},
                      "pages": {"outcome": "not-applicable"}}
        write(root / "completion.json", completion)
        return root / "completion.json", completion

    def test_program_candidate_contains_verified_squash_delivery(self):
        self.squashed_program()
        # The delivery owner still verifies the immutable completion chain;
        # only the separately tested core-admission verifier is isolated here.
        with mock.patch("workflow_delivery.verify_receipt") as admission:
            value = q.target(self.run, "OBJECTIVE")
        self.assertEqual(value["head"], self.final)
        admission.assert_called_once()

    def test_program_candidate_rejects_changed_merge_completion(self):
        path, completion = self.squashed_program()
        write(path, {**completion, "result_sha": self.final})
        with mock.patch("workflow_delivery.verify_receipt"), self.assertRaises(WorkflowError):
            q.target(self.run, "OBJECTIVE")


if __name__ == "__main__":
    unittest.main()
