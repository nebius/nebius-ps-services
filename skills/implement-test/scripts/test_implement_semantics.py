#!/usr/bin/env python3

import hashlib
import copy
import json
import tempfile
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import implement_semantics as semantics

from implement_semantics import (
    APPLICATION_KEYS,
    ORCHESTRATION_KEYS,
    _assignment_context_errors,
    _dependency_contract_errors,
    validate_results,
)


def manifest(generation: str, digest: str, status: str = "PARTIAL") -> dict:
    return {
        "schema": "implement-test/live-results-v2",
        "generation_id": generation,
        "status": status,
        "project_head": "a" * 40,
        "orchestration": {key: True for key in ORCHESTRATION_KEYS},
        "application": {key: True for key in APPLICATION_KEYS},
        "artifacts": {
            "browser": None,
            "application": {
                "path": "evidence/application.json",
                "sha256": digest,
            }
        },
    }


def application(generation: str) -> dict:
    task = {"id": 7, "title": "Verifier task", "completed": True}
    return {
        "schema": "implement-test/application-evidence-v1",
        "generation_id": generation,
        "services": ["api", "db", "frontend"],
        "web_port": 49152,
        "frontend_body_sha256": "b" * 64,
        "created_task": task,
        "database_task": task,
        "persisted_after_restart": True,
    }


class SemanticsTests(unittest.TestCase):
    def test_completed_assignment_survives_real_owner_spec_reconciliation(self):
        # Real Git, objective, spec transaction, basis and admission helpers.
        # The shared fixture labels its native termination as synthetic.
        scripts = Path(__file__).resolve().parents[2] / "worktree/scripts"
        sys.path.insert(0, str(scripts))
        from test_workflow_contracts import DirectExecutionTests
        from test_workflow_execution import git, complete_wave
        import workflow_execution as execution
        import workflow_impact as impact
        import workflow_objectives as objectives
        import workflow_state as state
        from workflow_publication import _basis

        fixture = DirectExecutionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        coordinator = fixture.prepare()
        pending = execution.prepare_wave(fixture.run_dir, "FEAT-001", "WAVE-001")
        first = pending[0]
        while pending:
            for assignment in pending:
                fixture.start(assignment)
                fixture.finish(assignment)
            pending = execution.refill_wave(fixture.run_dir, "FEAT-001", "WAVE-001")
        execution.integrate_wave(fixture.run_dir, "FEAT-001", "WAVE-001")
        complete_wave(fixture.run_dir, "FEAT-001", "WAVE-001", "fixture checks passed", session=fixture.session)
        integration = Path(coordinator["integration_worktree"])
        objective = objectives.validate_run(fixture.run_dir)[1]["revisions"][-1]["sha256"]
        fixture.publication(integration, objective=objective, basis=_basis(fixture.run_dir, "FEAT-001"))
        git(integration, "add", "-A")
        # Initial planning has effects; final owner publication fulfills the
        # same statements without changing the locked execution contract.
        from workflow_refinement import refinement, settle
        from project_specs_lib.impact import statement_inventory
        refined = refinement(fixture.run_dir)
        claim = {key: refined[key] for key in ("objective_id", "revision", "intent_sha256")}
        claim.update(
            schema=impact.CLAIM_SCHEMA, declared_effects=[], declared_plan_action="retain_plan",
            dispositions=[{
                "statement": item["key"], "disposition": "existing_contract",
                "requirements": ["REQ-001"], "design": ["FEAT-001"], "effects": [], "reason": None,
            } for item in statement_inventory(refined["extracted"])],
        )
        settle(fixture.run_dir, claim)
        basis = impact.settle_execution(fixture.run_dir, "FEAT-001", coordinator["plan_digest"], fixture.project)
        self.assertNotEqual(first["basis_sha256"], impact.canonical_digest(basis))
        execution.seal_feature(fixture.run_dir, "FEAT-001", "validation and alignment passed", "test: final specs")
        execution.promote_feature(fixture.run_dir, "FEAT-001", "delivery checked")
        objectives.complete_run(fixture.run_dir, fixture.session)
        basis = impact.verify_execution(fixture.run_dir, "FEAT-001", coordinator["plan_digest"], fixture.project)
        admission = state.read(fixture.run_dir / "execution/FEAT-001/admissions" / (first["task_id"] + ".json"))
        semantics._validate_admission_basis(fixture.run_dir, first, admission, basis, impact, state)
        for field in ("objective_sha256", "plan_digest", "identities", "base_commit",
                      "input_dependencies_sha256", "ownership", "plan_action"):
            with self.subTest(changed_contract=field):
                changed = {**basis, field: "different"}
                with self.assertRaisesRegex(ValueError, "different execution contract"):
                    semantics._validate_admission_basis(fixture.run_dir, first, admission, changed, impact, state)
        with self.assertRaisesRegex(ValueError, "admission does not bind"):
            semantics._validate_admission_basis(fixture.run_dir, first, {**admission, "basis_sha256": "0" * 64}, basis, impact, state)

        historical_path = fixture.run_dir / "bases/FEAT-001" / (first["basis_sha256"] + ".json")
        historical = state.read(historical_path)
        historical_path.unlink()
        with self.assertRaises(FileNotFoundError):
            semantics._validate_admission_basis(fixture.run_dir, first, admission, basis, impact, state)
        state.write(historical_path, {**historical, "owner": "different"})
        with self.assertRaisesRegex(ValueError, "historical assignment basis was modified"):
            semantics._validate_admission_basis(fixture.run_dir, first, admission, basis, impact, state)
        state.write(historical_path, historical)

        receipt, _ = impact.load_current(fixture.run_dir, required=True)
        receipt_path = fixture.run_dir / "objective-impact" / f"attempt-{receipt['generation']:04d}.json"
        for field, value in (("reason", "untrusted"), ("prior_impact_sha256", "0" * 64),
                             ("prior_spec_receipt_sha256", "0" * 64),
                             ("next_spec_receipt_sha256", "0" * 64)):
            with self.subTest(broken_transition=field):
                changed = copy.deepcopy(receipt)
                changed["spec_transition"][field] = value
                changed["spec_transition_sha256"] = impact.canonical_digest(changed["spec_transition"])
                state.write(receipt_path, changed)
                mutated_basis = {**basis, "impact_sha256": impact.canonical_digest(changed),
                                 "spec_transition_sha256": changed["spec_transition_sha256"]}
                with self.assertRaisesRegex(ValueError, "valid owner reconciliation"):
                    semantics._validate_admission_basis(fixture.run_dir, first, admission, mutated_basis, impact, state)
        state.write(receipt_path, receipt)
        semantics._validate_admission_basis(fixture.run_dir, first, admission, basis, impact, state)
        for field in ("statement", "category", "statement_sha256", "effects", "disposition"):
            with self.subTest(changed_coverage=field):
                changed = copy.deepcopy(receipt)
                changed["coverage"][0][field] = ["execution"] if field == "effects" else "different"
                state.write(receipt_path, changed)
                mutated_basis = {**basis, "impact_sha256": impact.canonical_digest(changed)}
                with self.assertRaisesRegex(ValueError, "valid owner reconciliation"):
                    semantics._validate_admission_basis(fixture.run_dir, first, admission, mutated_basis, impact, state)
        state.write(receipt_path, receipt)
        with self.assertRaisesRegex(ValueError, "hash-bound impact receipt"):
            semantics._validate_admission_basis(
                fixture.run_dir, first, admission, {**basis, "plan_action": "replan_required"}, impact, state,
            )

    def _recovered_worker(self, *, blocked):
        scripts = Path(__file__).resolve().parents[2] / "worktree/scripts"
        sys.path.insert(0, str(scripts))
        from test_workflow_contracts import DirectExecutionTests
        import workflow_execution as execution
        import workflow_reconcile as reconcile
        import workflow_state as state
        from workflow_objectives import session_hash

        fixture = DirectExecutionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        assignment, _ = fixture.result_fixture()
        if blocked:
            _, observation, _ = fixture.result_ingest(assignment, "blocked")
        else:
            observation = fixture.result_observation(assignment)
        native = "fresh-recovery-verifier-test"
        reconcile.authorize_recovery(
            fixture.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"],
            observation, fixture.session, session_hash(native),
        )
        execution.recover_task(
            fixture.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"], native,
            Path(assignment["scope_cwd"]), expected_attempt=1, confirmed_stopped=True,
        )
        fixture.result_write(assignment)
        receipt, observation, _ = fixture.result_ingest(assignment)
        result = fixture.result_finish(assignment, receipt, observation)
        admission = execution.admit_task(
            fixture.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"], observation,
        )
        task = state.read(execution.task_path(fixture.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"]))
        return fixture, assignment, task, result, admission, execution, state

    def test_completed_worker_preserves_authorized_recovery_history(self):
        fixture, assignment, task, result, admission, execution, state = self._recovered_worker(blocked=True)

        def verify(task_value=task, result_value=result, admission_value=admission):
            return semantics._validate_worker_history(
                fixture.run_dir, assignment, task_value, result_value, admission_value, execution, state,
            )

        self.assertEqual(len(verify()), 2)
        with self.assertRaisesRegex(ValueError, "session history"):
            verify({**task, "attempt": 1})
        with self.assertRaises(execution.ExecutionError):
            verify({**task, "worker_session_hash_history": [task["worker_session_hash"]] * 2})
        with self.assertRaisesRegex(ValueError, "session history"):
            verify(result_value={**result, "worker_session_hash": "0" * 64})
        with self.assertRaisesRegex(ValueError, "termination evidence"):
            verify(admission_value={**admission, "termination_sha256": "0" * 64})

        root = fixture.run_dir / "execution/FEAT-001"
        authorization_path = next((root / "recoveries").glob("*.json"))
        authorization = state.read(authorization_path)
        authorization_path.unlink()
        with self.assertRaisesRegex(ValueError, "history is missing"):
            verify()
        state.write(authorization_path, authorization)
        for field, value in (
            ("assignment_digest", "0" * 64), ("prior_worker_session_hash", "0" * 64),
            ("next_worker_session_hash", "0" * 64), ("termination_sha256", "0" * 64),
            ("worker_result_receipt_sha256", None), ("repair_authority", {}),
        ):
            with self.subTest(changed_recovery=field):
                state.write(authorization_path, {**authorization, field: value})
                with self.assertRaises(ValueError):
                    verify()
        state.write(authorization_path, authorization)
        from workflow_worker_results import receipt_path
        failed_path = receipt_path(assignment, 1)
        failed = state.read(failed_path)
        failed_path.unlink()
        with self.assertRaisesRegex(ValueError, "authority changed"):
            verify()
        state.write(failed_path, failed)
        self.assertEqual(len(verify()), 2)

    def test_completed_worker_preserves_interruption_recovery(self):
        fixture, assignment, task, result, admission, execution, state = self._recovered_worker(blocked=False)
        history = semantics._validate_worker_history(
            fixture.run_dir, assignment, task, result, admission, execution, state,
        )
        self.assertEqual(len(history), 2)

    def test_assignment_context_is_bound_to_the_canonical_task(self) -> None:
        task = {
            "dependencies": ["task-1"],
            "goal": "Integrate the tiers",
            "plan": "Wire the runtime",
            "implementation_steps": ["write compose.yaml", "add runtime checks"],
            "validation": ["validate strict JSON"],
            "end_to_end_validation": ["exercise the complete task flow"],
            "done_criteria": ["all tiers are connected"],
        }
        liveness = {
            "worker_profile": "integration",
            "health_policy": __import__("workflow_liveness").health_policy("integration"),
        }
        assignment = {
            **task,
            **liveness,
        }
        self.assertEqual(_assignment_context_errors(task, assignment, liveness), [])
        assignment["dependencies"] = []
        assignment["validation"] = ["reread coordinator state"]
        errors = _assignment_context_errors(task, assignment, liveness)
        self.assertIn(
            "assignment dependencies does not match its canonical task", errors
        )
        self.assertIn(
            "assignment validation does not match its canonical task",
            errors,
        )

    def test_dependency_contract_requires_disjoint_tiers_and_integration_dependencies(
        self,
    ) -> None:
        tiers = [
            {
                "task_id": f"task-{index}",
                "dependencies": [],
                "write_claims": [{"kind": "prefix", "path": path}],
            }
            for index, path in enumerate(
                ("project-a/app/frontend", "project-a/app/api", "project-a/app/database"), start=1
            )
        ]
        integration = {
            "task_id": "task-4",
            "dependencies": ["task-1", "task-2", "task-3"],
            "write_claims": [{"kind": "exact", "path": "project-a/compose.yaml"}],
        }
        waves = [{"tasks": tiers}, {"tasks": [integration]}]
        self.assertEqual(_dependency_contract_errors(waves), [])
        integration["dependencies"] = ["task-1"]
        self.assertIn(
            "integration task must depend on every first-wave tier task",
            _dependency_contract_errors(waves),
        )
        integration["dependencies"] = ["task-1", "task-2", "task-3"]
        tiers[1]["write_claims"] = [{"kind": "prefix", "path": "project-a/app/frontend/api"}]
        self.assertIn(
            "first-wave task write claims overlap",
            _dependency_contract_errors(waves),
        )
        tiers[0]["write_claims"] = [{"kind": "prefix", "path": "project-a/app/frontend/nested"}]
        tiers[1]["write_claims"] = [{"kind": "prefix", "path": "project-a/app/frontend"}]
        self.assertIn(
            "first-wave task write claims overlap",
            _dependency_contract_errors(waves),
        )

    def test_dependency_contract_requires_three_distinct_tier_owners(self) -> None:
        combined = {
            "task_id": "task-1",
            "dependencies": [],
            "write_claims": [
                {"kind": "prefix", "path": path}
                for path in ("project-a/app/frontend", "project-a/app/api", "project-a/app/database")
            ],
        }
        integration = {
            "task_id": "task-2",
            "dependencies": ["task-1"],
            "write_claims": [{"kind": "exact", "path": "project-a/compose.yaml"}],
        }
        self.assertIn(
            "first wave must have three distinct tier task owners",
            _dependency_contract_errors(
                [{"tasks": [combined]}, {"tasks": [integration]}]
            ),
        )

    def write_evidence(self, run: Path, generation: str) -> tuple[Path, str]:
        evidence = run / "evidence"
        evidence.mkdir()
        artifact = evidence / "application.json"
        artifact.write_text(
            json.dumps(application(generation), sort_keys=True), encoding="utf-8"
        )
        return evidence, hashlib.sha256(artifact.read_bytes()).hexdigest()

    def test_valid_partial_requires_structured_digest_bound_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            generation = str(uuid.uuid4())
            evidence, digest = self.write_evidence(Path(temp), generation)
            self.assertEqual(
                validate_results(manifest(generation, digest), evidence, generation), []
            )

    def test_pass_requires_direct_orchestration_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            generation = str(uuid.uuid4())
            evidence, digest = self.write_evidence(Path(temp), generation)
            errors = validate_results(
                manifest(generation, digest, "PASS"), evidence, generation
            )
            self.assertIn(
                "PASS requires direct project and orchestration state validation",
                errors,
            )

    def test_rejects_self_attested_empty_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            generation = str(uuid.uuid4())
            evidence = Path(temp) / "evidence"
            evidence.mkdir()
            artifact = evidence / "application.json"
            artifact.write_text("{}", encoding="utf-8")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            errors = validate_results(
                manifest(generation, digest), evidence, generation
            )
            self.assertIn("application evidence has an invalid shape", errors)

    def test_rejects_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            generation = str(uuid.uuid4())
            evidence, _digest = self.write_evidence(Path(temp), generation)
            errors = validate_results(
                manifest(generation, "0" * 64), evidence, generation
            )
            self.assertIn("application artifact digest does not match", errors)

    def test_pass_accepts_helper_bound_application_and_canonical_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generation = str(uuid.uuid4())
            evidence, digest = self.write_evidence(root, generation)
            project = root / "project"
            run_dir = root / "run"
            scripts = root / "scripts"
            for directory in (project, run_dir, scripts):
                directory.mkdir()
            prompt = root / "prompt.md"
            prompt.write_text("managed\n", encoding="utf-8")
            lifecycle = root / "lifecycle.json"
            lifecycle.write_text(
                json.dumps(
                    {
                        "owner": "implement-test",
                        "generation_id": generation,
                        "live_started": True,
                        "web_port": 49152,
                        "application_evidence_sha256": digest,
                        "browser_evidence_sha256": "b" * 64,
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(semantics, "_validate_orchestration", return_value=[]), patch(
                "implement_catalog.validate"
            ), patch("implement_browser.validate") as browser_check:
                data = manifest(generation, digest, "PASS")
                data["artifacts"]["browser"] = {"path": "evidence/browser.json", "sha256": "b" * 64}
                errors = validate_results(
                    data,
                    evidence,
                    generation,
                    project_root=project,
                    run_dir=run_dir,
                    workflow_scripts=scripts,
                    invocation_receipt=prompt,
                    lifecycle_state=lifecycle,
                )
            self.assertEqual(errors, [])
            browser_check.assert_called_once()


if __name__ == "__main__":
    unittest.main()
