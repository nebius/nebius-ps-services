"""Real Git automatic-outer boundaries with a synthetic completed inner run.

These tests do not qualify native agents. The existing execution suites own
inner worker completion; this fixture isolates aggregation and crash recovery.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import workflow_automatic as automatic
import workflow_objectives as objectives
import workflow_projects as projects
import workflow_interop as interop
import worktree_manager as manager
from workflow_state import WorkflowError, read, write, digest


class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo, self.home = self.root / "repo", self.root / "home"
        self.repo.mkdir()
        self.git(self.repo, "init", "-b", "main")
        self.git(self.repo, "config", "user.email", "test@example.com")
        self.git(self.repo, "config", "user.name", "Fixture")
        (self.repo / "README.md").write_text("baseline\n")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-m", "baseline")
        self.git(self.repo, "remote", "add", "origin", str(self.root / "origin.git"))
        self.git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.git(self.repo, "checkout", "-b", "feature/parallel-projects")
        env = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home), "CODEX_THREAD_ID": "automatic-test"})
        env.start()
        self.addCleanup(env.stop)
        accepted = objectives.accept(self.repo, self.home, getattr(self, "workflow_owner", "implement"), "automatic-test", {
            "outcome": "Update the fixture", "scope": ["README.md"], "constraints": [],
            "acceptance_criteria": ["README contains the change"], "clarifications": [],
            "provenance": {"kind": "direct_request", "source_sha256": "a" * 64}})
        self.run = Path(accepted["run_dir"])
        self.outer = Path(accepted["project_root"])
        (self.outer / "README.md").write_text("implemented\n")
        self.git(self.outer, "add", "-A")
        self.git(self.outer, "commit", "-m", "fixture inner result")
        self.child = self.git(self.outer, "rev-parse", "HEAD")
        interop.record_promotion(self.run, self.outer, self.child)
        write(self.run / "execution/FEAT-001/coordinator.json", {"status": "done"})
        journal = self.run / "execution/FEAT-001/journals/coordinator.jsonl"
        journal.parent.mkdir(parents=True)
        journal.write_text('{"event":"fixture_done"}\n')
        check = {"id": "readme", "argv": ["{python}", "-I", "-c",
                 "from pathlib import Path; assert Path('README.md').read_text() == 'implemented\\n'"],
                 "execution_policy": {"observation_interval_seconds": 30, "deadline": None}, "environment_keys": []}
        self.plan = {"schema": "workflow/validation-plan-v2", "wave_checks": [check], "task_checks": {}}
        plans = mock.patch("workflow_validation.load_plan", return_value=self.plan)
        plans.start()
        self.addCleanup(plans.stop)
        proofs = mock.patch("workflow_automatic._execution_proofs", return_value={"fixture": "synthetic completed inner run"})
        proofs.start()
        self.addCleanup(proofs.stop)

    @staticmethod
    def git(path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True, stderr=subprocess.DEVNULL).strip()

    def freeze_basis(self):
        import workflow_impact as impact
        basis = {"execution_id": "FEAT-001", "validation_plan_sha256": impact.canonical_digest(self.plan),
                 "identities": impact._identity_files(self.outer, owner=getattr(self, "workflow_owner", "implement"))}
        if getattr(self, "workflow_owner", "implement") == "agentic-sdlc":
            policy = {"schema": "workflow/task-acceptance-policy-v3", "tasks": {}}
            basis.update(owner="agentic-sdlc", plan_digest="a" * 64,
                         task_acceptance_policy=policy, task_acceptance_policy_sha256=impact.canonical_digest(policy))
        fingerprint = impact.canonical_digest(basis)
        relative = f"bases/FEAT-001/{fingerprint}.json"
        write(self.run / relative, basis)
        write(self.run / "objective-impact/execution/FEAT-001.json", {
            "schema": "workflow/execution-basis-pointer-v2", "path": relative, "sha256": fingerprint})

    def prepare(self):
        self.freeze_basis()
        candidate = automatic.prepare_aggregation(self.run, "automatic-test")
        self.assertEqual(candidate["status"], "validation-required")
        return candidate

    def record_gates(self, *, outcome="passed"):
        import workflow_aggregation_gates as gates
        identity = automatic._candidate(self.run)[0]
        value = {"target": identity, "inner": {}, "aggregate": {}, "qualifications": []}
        for stage, head in (("inner", identity["child_head"]), ("aggregate", identity["head"])):
            for gate in gates.GATES:
                relative = "aggregation/reports/" + stage + "-" + gate + ("-failed" if stage == "aggregate" and outcome == "failed" else "") + ".json"
                report = {"schema": "workflow/gate-review-v2", "gate": gate, "head": head,
                          "outcome": "not_requested" if gate == "uat" else outcome if stage == "aggregate" and gate == "alignment" else "passed",
                          "source_read_only": True, "evidence": ["synthetic offline review fixture"]}
                write(self.run / relative, report)
                value[stage][gate] = {"path": relative, "sha256": digest(report)}
        return gates.record(self.run, "automatic-test", value)

    def validate_final(self):
        value = automatic.validate_aggregation(self.run, "automatic-test")
        self.record_gates()
        return value

    def test_missing_or_changed_final_gates_block_delivery(self):
        self.prepare()
        automatic.validate_aggregation(self.run, "automatic-test")
        before = self.git(self.repo, "rev-parse", "HEAD")
        with self.assertRaises((WorkflowError, FileNotFoundError)):
            automatic.finish_aggregation(self.run, "automatic-test")
        gates = self.record_gates()
        path = self.run / gates["aggregate"]["uat"]["path"]
        value = read(path)
        write(path, {**value, "head": self.child})
        with self.assertRaises(WorkflowError):
            automatic.finish_aggregation(self.run, "automatic-test")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), before)

    def test_failed_read_only_review_starts_fresh_corrective_execution(self):
        candidate = self.prepare()
        automatic.validate_aggregation(self.run, "automatic-test")
        gates = self.record_gates(outcome="failed")
        with self.assertRaises(WorkflowError):
            automatic.finish_aggregation(self.run, "automatic-test")
        correction = automatic.begin_correction(self.run, "automatic-test", "FEAT-002")
        self.assertEqual(correction["failure"]["gates_sha256"], digest(gates))
        self.assertEqual(correction["target"]["head"], candidate["candidate_head"])
        self.assertEqual(automatic.begin_correction(self.run, "automatic-test", "FEAT-002"), correction)

    def test_release_uses_inner_review_references_and_retains_aggregate_proof(self):
        self.prepare()
        self.validate_final()
        original = interop.release
        with mock.patch.object(interop, "release", wraps=original) as release:
            automatic.finish_aggregation(self.run, "automatic-test")
        refs = release.call_args.kwargs
        self.assertEqual(set(refs), {"final_alignment", "uat", "docs"})
        self.assertTrue(all(value.startswith("aggregation/reports/inner-") for value in refs.values()))
        self.assertEqual(automatic.verify_delivery(self.run)["cleanup"], "verified")

    def test_later_failed_observation_preserves_pass_and_admits_correction(self):
        import workflow_aggregation_gates as gates
        self.prepare()
        self.validate_final()
        target = automatic._candidate(self.run)[0]
        path = self.run / "aggregation/gates" / (digest(target) + ".json")
        prior = path.read_bytes()
        original = gates.write
        def interrupted(destination, value, **kwargs):
            if destination.name.endswith(".failure.produced.json"):
                raise OSError("simulated producer-index interruption")
            return original(destination, value, **kwargs)
        with mock.patch.object(gates, "write", side_effect=interrupted), self.assertRaises(OSError):
            self.record_gates(outcome="failed")
        with self.assertRaises(FileNotFoundError):
            gates.load(self.run, target)
        failure = self.record_gates(outcome="failed")
        self.assertEqual(path.read_bytes(), prior)
        self.assertEqual(gates.load(self.run, target), failure)
        self.assertEqual(self.record_gates(outcome="failed"), failure)
        with self.assertRaises(WorkflowError):
            self.record_gates()
        with self.assertRaises(WorkflowError):
            automatic.finish_aggregation(self.run, "automatic-test")
        correction = automatic.begin_correction(self.run, "automatic-test", "FEAT-002")
        self.assertEqual(correction["failure"]["gates_sha256"], digest(failure))
        self.assertEqual(path.read_bytes(), prior)

    def test_malformed_review_map_fails_before_recording(self):
        import workflow_aggregation_gates as gates
        self.prepare()
        with self.assertRaises(WorkflowError):
            gates.record(self.run, "automatic-test", {"target": automatic._candidate(self.run)[0],
                "inner": {}, "aggregate": [], "qualifications": []})
        self.assertFalse((self.run / "aggregation/gates").exists())

    def test_aggregate_then_continue_after_removal(self):
        (self.repo / "independent.txt").write_text("source advances\n")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-m", "independent")
        candidate = self.prepare()
        self.validate_final()
        result = automatic.finish_aggregation(self.run, "automatic-test")
        self.assertEqual(result["delivered_head"], candidate["candidate_head"])
        self.assertFalse(self.outer.exists())
        self.assertEqual((self.repo / "independent.txt").read_text(), "source advances\n")
        continued = objectives.continue_run(self.repo, self.home, "implement", "automatic-test")
        self.assertEqual(continued["run_dir"], str(self.run))
        self.assertEqual(automatic.finish_aggregation(self.run, "automatic-test"), result)
        from workflow_program import completion_project
        self.assertEqual(completion_project(self.run), self.repo)
        self.assertEqual(automatic.prepare_aggregation(self.run, "automatic-test")["status"], "DELIVERED")
        (self.repo / "README.md").write_text("unverified change\n")
        with self.assertRaisesRegex(WorkflowError, "verified aggregate snapshot"):
            completion_project(self.run)

    def test_missing_future_program_slice_blocks_before_aggregate_state_change(self):
        before = projects.get(self.run)
        with mock.patch("workflow_program.require_complete", side_effect=WorkflowError("DELIVERY_PENDING", "future slice")):
            with self.assertRaisesRegex(WorkflowError, "future slice"):
                automatic.prepare_aggregation(self.run, "automatic-test")
        self.assertEqual(projects.get(self.run), before)
        self.assertFalse((self.run / "aggregation/candidate.json").exists())

    def test_manual_integration_cannot_use_automatic_ready_lease(self):
        candidate = self.prepare()
        with self.assertRaises(manager.WorktreeError):
            manager.integrate_worktree(cwd=self.repo, name=candidate["name"], validated_head=candidate["candidate_head"],
                                       restart=False, expected_source_head=candidate["source_head"], expected_child_head=self.child)

    def test_missing_or_modified_check_evidence_blocks_promotion(self):
        self.prepare()
        receipt = self.validate_final()
        path = next((self.run / "aggregation/operations").glob("*.json"))
        write(path, {**receipt, "outcomes": []})
        source = self.git(self.repo, "rev-parse", "HEAD")
        with self.assertRaises(WorkflowError):
            automatic.finish_aggregation(self.run, "automatic-test")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), source)

    def test_resume_after_outer_removed_before_registry_update(self):
        self.prepare()
        self.validate_final()
        original = projects.update
        def interrupted(*args, **kwargs):
            if kwargs.get("state") == "cleaned":
                raise RuntimeError("injected after removal")
            return original(*args, **kwargs)
        with mock.patch.object(projects, "update", side_effect=interrupted), self.assertRaises(RuntimeError):
            automatic.finish_aggregation(self.run, "automatic-test")
        self.assertFalse(self.outer.exists())
        objectives.continue_run(self.repo, self.home, "implement", "automatic-test")
        self.assertEqual(automatic.finish_aggregation(self.run, "automatic-test")["status"], "COMPLETE")

    def test_resume_releases_capacity_after_cleanup_checkpoint(self):
        self.prepare()
        self.validate_final()
        with mock.patch.object(projects, "release", side_effect=RuntimeError("injected before capacity release")), self.assertRaises(RuntimeError):
            automatic.finish_aggregation(self.run, "automatic-test")
        self.assertEqual(projects.get(self.run)["state"], "cleaned")
        automatic.finish_aggregation(self.run, "automatic-test")
        self.assertNotIn(str(self.run), read(self.home / "workflow-capacity/reservations.json")["runs"])

    def test_source_drift_rebuild_budget_is_durable(self):
        self.prepare()
        for count in range(3):
            self.git(self.repo, "commit", "--allow-empty", "-m", "advance " + str(count))
            if count < 2:
                self.prepare()
            else:
                with self.assertRaisesRegex(WorkflowError, "two source-drift"):
                    automatic.prepare_aggregation(self.run, "automatic-test")
        self.assertEqual(projects.get(self.run)["drift_rebuilds"], 2)

    def test_failed_candidate_starts_fresh_correction_without_reopening(self):
        prior = read(self.run / "execution/FEAT-001/coordinator.json")
        self.plan["wave_checks"][0]["argv"][-1] = "raise SystemExit(1)"
        candidate = self.prepare()
        with self.assertRaisesRegex(WorkflowError, "combined acceptance failed"):
            self.validate_final()
        failed = next((self.run / "aggregation/operations").glob("*.json")).read_bytes()
        correction = automatic.begin_correction(self.run, "automatic-test", "FEAT-002")
        self.assertEqual(correction["target"]["head"], candidate["candidate_head"])
        self.assertEqual(self.git(self.outer, "rev-parse", "HEAD"), candidate["candidate_head"])
        self.assertEqual(read(self.run / "execution/FEAT-001/coordinator.json"), prior)
        self.assertEqual(next((self.run / "aggregation/operations").glob("*.json")).read_bytes(), failed)
        self.assertEqual(projects.get(self.run)["state"], "allocated")
        self.assertEqual(automatic.begin_correction(self.run, "automatic-test", "FEAT-002"), correction)
        objectives.continue_run(self.repo, self.home, "implement", "automatic-test")
        self.assertEqual(read(self.run / "run.json")["source_head"], candidate["candidate_head"])
        with self.assertRaisesRegex(WorkflowError, "fresh corrective"):
            automatic.prepare_aggregation(self.run, "automatic-test")

    def test_correction_checks_ownership_before_changing_head(self):
        import worktree_interop as outer_interop
        self.plan["wave_checks"][0]["argv"][-1] = "raise SystemExit(1)"
        self.prepare()
        with self.assertRaises(WorkflowError):
            self.validate_final()
        before = self.git(self.outer, "rev-parse", "HEAD")
        git = manager._git
        def interrupted(path, *args, **kwargs):
            if args[:2] == ("merge", "--ff-only"):
                raise RuntimeError("interrupted before correction merge")
            return git(path, *args, **kwargs)
        with mock.patch.object(manager, "_git", side_effect=interrupted), self.assertRaises(RuntimeError):
            automatic.begin_correction(self.run, "automatic-test", "FEAT-002")
        # Inject at the live read under the owner lock; no fixture mutation can
        # accidentally pre-satisfy the corrective transition.
        original = outer_interop.lifecycle_lease
        def replaced(*args, **kwargs):
            lease = original(*args, **kwargs)
            return {**lease, "workspace": str(self.root / "foreign")}
        with mock.patch.object(outer_interop, "lifecycle_lease", side_effect=replaced):
            with self.assertRaises(WorkflowError):
                automatic.begin_correction(self.run, "automatic-test", "FEAT-002")
        self.assertEqual(self.git(self.outer, "rev-parse", "HEAD"), before)
        original_reservation = outer_interop.load_reservation
        def changed(*args, **kwargs):
            value = original_reservation(*args, **kwargs)
            return {**value, "token": "changed"}
        with mock.patch.object(outer_interop, "load_reservation", side_effect=changed):
            with self.assertRaises(WorkflowError):
                automatic.begin_correction(self.run, "automatic-test", "FEAT-002")
        self.assertEqual(self.git(self.outer, "rev-parse", "HEAD"), before)

    def test_frozen_helper_drift_blocks_new_evaluation_and_promotion(self):
        import workflow_impact as impact
        self.prepare()
        with mock.patch.object(impact, "_identity_files", return_value={"changed": "a" * 64}):
            with self.assertRaisesRegex(WorkflowError, "closure changed"):
                self.validate_final()
        self.validate_final()
        before = self.git(self.repo, "rev-parse", "HEAD")
        with mock.patch.object(impact, "_identity_files", return_value={"changed": "a" * 64}):
            with self.assertRaisesRegex(WorkflowError, "closure changed"):
                automatic.finish_aggregation(self.run, "automatic-test")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), before)

    def test_candidate_restart_waits_for_running_evaluator(self):
        import threading
        import workflow_validation as validation
        self.prepare()
        evaluating, release, transition = threading.Event(), threading.Event(), threading.Event()
        errors = []
        check = validation._run_check
        integrate = manager.integrate_worktree
        def blocked(*args):
            evaluating.set()
            if not release.wait(15):
                raise RuntimeError("test evaluator was not released")
            return check(*args)
        def observed(**kwargs):
            transition.set()
            return integrate(**kwargs)
        def run(function):
            try:
                function(self.run, "automatic-test")
            except BaseException as error:
                errors.append(error)
        with mock.patch.object(validation, "_run_check", side_effect=blocked), mock.patch.object(manager, "integrate_worktree", side_effect=observed):
            evaluator = threading.Thread(target=run, args=(automatic.validate_aggregation,))
            evaluator.start()
            try:
                self.assertTrue(evaluating.wait(10))
                self.git(self.repo, "commit", "--allow-empty", "-m", "aggregate advances")
                restart = threading.Thread(target=run, args=(automatic.prepare_aggregation,))
                restart.start()
                self.assertFalse(transition.wait(.2))
            finally:
                release.set()
                evaluator.join(20)
            restart.join(20)
        self.assertFalse(evaluator.is_alive() or restart.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(transition.is_set())

    def test_resume_after_source_promotion_before_delivery_checkpoint(self):
        self.prepare()
        self.validate_final()
        original = projects.update
        def interrupted(*args, **kwargs):
            if kwargs.get("state") == "delivered":
                raise RuntimeError("injected after source promotion")
            return original(*args, **kwargs)
        with mock.patch.object(projects, "update", side_effect=interrupted), self.assertRaises(RuntimeError):
            automatic.finish_aggregation(self.run, "automatic-test")
        self.assertEqual(automatic.finish_aggregation(self.run, "automatic-test")["status"], "COMPLETE")


if __name__ == "__main__":
    unittest.main()
