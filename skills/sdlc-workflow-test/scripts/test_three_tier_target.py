#!/usr/bin/env python3
"""Real-Git composition of fresh spec admission and harness execution targeting."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for directory in (
    Path(__file__).parent,
    ROOT / "worktree/scripts",
    ROOT / "maintain-project-specs/scripts",
):
    sys.path.insert(0, str(directory))

import workflow_objectives as objectives  # noqa: E402 — verified runtime support precedes imports
import workflow_refinement as refinement  # noqa: E402 — verified runtime support precedes imports
import workflow_resources as resources  # noqa: E402 — verified runtime support precedes imports
import workflow_impact as impact  # noqa: E402
import workflow_execution as execution  # noqa: E402
from project_specs_lib.transaction import publish_spec_pair  # noqa: E402
from test_workflow_execution import REQUIREMENTS, DESIGN, PLAN, finish_task as fixture_finish  # noqa: E402
import three_tier_lifecycle as lifecycle  # noqa: E402
import three_tier_target as target  # noqa: E402


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value))
    path.chmod(0o600)


class ThreeTierTargetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "verification"
        with (
            mock.patch.object(
                lifecycle,
                "require_command",
                side_effect=["29.0", "5.0", "git version 2.50"],
            ),
            mock.patch.object(
                lifecycle, "detect_browser", return_value=("chrome", "Google Chrome")
            ),
        ):
            self.state = lifecycle.prepare(self.root)
        self.project = Path(self.state["project_root"])
        self.home = Path(self.state["private_root"]) / "codex-home"
        self.environment = mock.patch.dict(
            os.environ, {"CODEX_HOME": str(self.home), "SKILLS_AGENT": "codex", "CODEX_THREAD_ID": "target-root"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        initial = objectives.accept(self.project, self.home, 'agentic-sdlc', 'target-root', {
            'outcome':'Implement the planned behavior', 'scope':['fixture project'], 'constraints':[],
            'acceptance_criteria':['observable behavior passes'], 'clarifications':[],
            'provenance':{'kind':'direct_request','source_sha256':'a'*64}})
        self.run = Path(initial['run_dir'])
        self.run_id = self.run.name
        self.manifest = self.run.parent / 'project.json'
        project_id = json.loads(self.manifest.read_text())['project_id']
        private_json(self.run / 'current-state.json', {'run_id': self.run_id, 'project_id': project_id,
                     'current_feature':'FEAT-001','status':'running','current_phase':'sdlc-start'})
        self.base = self.git("rev-parse", "HEAD")
        self.startup_observation = target.observe_startup(
            self.state, skills_root=ROOT, agent="codex"
        )
        relative = "evidence/phases/sdlc-start.json"
        private_json(
            Path(self.state["run_root"]) / relative,
            {
                "schema": lifecycle.PHASE_RESULT_SCHEMA,
                "phase": "sdlc-start",
                "status": "PASS",
                "verification_id": self.state["verification_id"],
                "baseline_sha": self.base,
                "recorded_head": self.base,
                "objective_sha256": self.startup_observation["objective_sha256"],
                "basis_sha256": None,
                "assertions": lifecycle.PHASE_REQUIRED_ASSERTIONS["sdlc-start"],
            },
        )
        self.state = lifecycle.record_phase(
            self.root, "sdlc-start", "PASS", "Exact hook registration observed.", [relative]
        )
        publish_spec_pair(
            self.project,
            requirements_candidate=REQUIREMENTS.encode(),
            design_candidate=DESIGN.encode(),
            expected_git_head=self.base,
            expected_requirements_sha256="absent",
            expected_design_sha256="absent",
            operation_id="initial-spec-admission",
        )
        value = refinement.refinement(self.run)
        from project_specs_lib.impact import statement_inventory
        claim = {key: value[key] for key in ('objective_id','revision','intent_sha256')}
        claim.update(schema=impact.CLAIM_SCHEMA, declared_effects=['design','requirements'],
                     declared_plan_action='replan_required', dispositions=[{
                         'statement': item['key'], 'disposition':'changed_contract',
                         'requirements':['REQ-001'],'design':['FEAT-001'],
                         'effects':['design','requirements'],'reason':None}
                         for item in statement_inventory(value['extracted'])])
        self.claim = claim

    def git(self, *arguments, path=None):
        return subprocess.run(
            ["git", "-C", str(path or self.project), *arguments],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def test_execution_rejects_empty_hook_ownership_record(self):
        self.admit_and_prepare()
        lock = self.manifest.parent / "active.lock"
        lock.write_text("")
        lock.chmod(0o600)
        with self.assertRaises(target.TargetError):
            self.resolve()

    def test_explicit_new_session_continuation_preserves_startup_and_phase_evidence(self):
        coordinator = self.admit_and_prepare()
        startup = dict(self.state['workflow_startup'])
        continued = objectives.continue_run(
            self.project, self.home, 'agentic-sdlc', 'continued-root'
        )
        self.assertEqual(continued['run_dir'], str(self.run))
        # The original native session may bind another objective after handoff.
        old_binding = self.home / 'sdlc-runs/bindings' / (objectives.session_hash('target-root') + '.json')
        private_json(old_binding, {'schema': 'foreign-binding'})
        with mock.patch.dict(os.environ, {'CODEX_THREAD_ID': 'continued-root'}):
            self.assertEqual(self.resolve()['path'], coordinator['integration_worktree'])
            objective_sha, basis_sha = lifecycle.phase_identity(self.state, 'sdlc-implement-plan')
            self.assertEqual(objective_sha, self.startup_observation['objective_sha256'])
            self.assertEqual(len(basis_sha), 64)
        self.assertEqual(self.state['workflow_startup'], startup)

    def test_startup_receipt_is_bound_and_cannot_be_retrofitted(self):
        observed = target.validate_startup_receipt(self.state)
        self.assertEqual(observed["run_id"], self.run_id)
        self.assertEqual(observed["hook_discovery"], "MATCH")
        self.state.pop("workflow_startup")
        lifecycle.update_state(self.root, self.state)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "after other phase"):
            lifecycle.record_phase(
                self.root, "sdlc-start", "PASS", "Late observation.",
                ["evidence/phases/sdlc-start.json"],
            )
        self.admit_and_prepare()
        with self.assertRaisesRegex(target.TargetError, "too late"):
            target.observe_startup(self.state, skills_root=ROOT, agent="codex")

    def test_execution_rejects_mismatched_hook_owner(self):
        self.admit_and_prepare()
        lock_path = self.manifest.parent / "active.lock"
        lock = json.loads(lock_path.read_text())
        for field, value in (("run_id", "run-foreign"), ("project_root", "/foreign"), ("owner_session_hash", "")):
            with self.subTest(field=field):
                private_json(lock_path, {**lock, field: value})
                with self.assertRaises(target.TargetError):
                    self.resolve()
        private_json(lock_path, lock)
        self.resolve()

    def admit_and_prepare(self):
        refinement.ready(self.run)
        # Execute the documented initial admission: only the new canonical pair is dirty.
        canonical = {"docs/requirements.md", "docs/design.md"}
        self.assertEqual(
            set(self.git("ls-files", "--others", "--exclude-standard").splitlines()),
            canonical,
        )
        self.assertEqual(self.git("diff", "--name-only"), "")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.git("add", "-A")
        self.assertEqual(
            set(self.git("diff", "--cached", "--name-only").splitlines()), canonical
        )
        for name in canonical:
            self.assertEqual(
                self.git("show", ":" + name), (self.project / name).read_text().strip()
            )
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)
        plans = self.run / "plans"
        plans.mkdir(mode=0o700, exist_ok=True)
        plan = plans / "FEAT-001-v1.md"
        plan.write_text(PLAN.split("### TASK-002")[0].replace('- Conflict domains: code:a',
            '- Conflict domains: runtime:sdlc-three-tier-' + self.state['verification_id']))
        plan.chmod(0o600)
        plan.with_suffix(".md.lock").write_text("locked\n")
        plan.with_suffix(".md.lock").chmod(0o600)
        # The prepare phase owns this commit; design admission above did not commit.
        self.git("commit", "-qm", "Record canonical contract")
        refinement.settle(self.run, self.claim)
        return execution.prepare_execution(self.run, self.project, "FEAT-001", plan, 1)

    def resolve(self, **kwargs):
        return target.resolve_target(
            self.state, skills_root=ROOT, agent="codex", **kwargs
        )

    def admit_phase_then_reconcile_delivery(self, *, material=False):
        """Real owner publication/settlement; no fabricated execution basis."""
        coordinator = self.admit_and_prepare()
        integration = Path(coordinator["integration_worktree"])
        phase = "sdlc-prepare-execution"
        prior_basis = self.resolve()["basis_sha256"]
        relative = f"evidence/phases/{phase}.json"
        private_json(Path(self.state["run_root"]) / relative, {
            "schema": lifecycle.PHASE_RESULT_SCHEMA, "phase": phase, "status": "PASS",
            "verification_id": self.state["verification_id"], "baseline_sha": self.base,
            "recorded_head": coordinator["integration_head"],
            "objective_sha256": self.startup_observation["objective_sha256"],
            "basis_sha256": prior_basis, "assertions": lifecycle.PHASE_REQUIRED_ASSERTIONS[phase],
        })
        self.state = lifecycle.record_phase(self.root, phase, "PASS", "Offline preparation fixture.", [relative])
        entry = self.state["phases"][-1]
        requirements = (integration / "docs/requirements.md").read_bytes()
        design = (integration / "docs/design.md").read_bytes()
        publish_spec_pair(
            integration, requirements_candidate=requirements,
            design_candidate=design.replace(b"delivery=not-started", b"delivery=implemented").replace(
                b"Not implemented yet.", b"Offline fixture delivery evidence."),
            expected_git_head=coordinator["integration_head"],
            expected_requirements_sha256=hashlib.sha256(requirements).hexdigest(),
            expected_design_sha256=hashlib.sha256(design).hexdigest(),
            expected_objective_sha256=self.startup_observation["objective_sha256"],
            expected_basis_sha256=prior_basis, operation_id="delivery-reconciliation",
        )
        claim = json.loads(json.dumps(self.claim))
        if not material:
            claim.update(declared_effects=[], declared_plan_action="retain_plan")
            for row in claim["dispositions"]:
                row.update(disposition="existing_contract", effects=[])
        refinement.settle(self.run, claim)
        execution.replan_future(self.run, "FEAT-001", Path(coordinator["plan_path"]), 1)
        current = json.loads((self.run / "current-state.json").read_text())
        private_json(self.run / "current-state.json", {**current, "current_phase": "sdlc-update-documents"})
        self.assertNotEqual(prior_basis, self.resolve(require_clean=False)["basis_sha256"])
        return phase, entry, prior_basis, coordinator

    def test_admitted_history_survives_owner_delivery_settlement(self):
        phase, entry, _, _ = self.admit_phase_then_reconcile_delivery()
        lifecycle.validate_phase_pass_artifact(
            self.state, phase, entry["evidence"], historical_entry=entry,
        )
        # The same old bytes cannot be submitted as a new phase result.
        with self.assertRaisesRegex(lifecycle.LifecycleError, "basis is stale"):
            lifecycle.validate_phase_pass_artifact(self.state, phase, entry["evidence"])

    def test_historical_basis_requires_latest_admitted_receipt(self):
        phase, entry, _, _ = self.admit_phase_then_reconcile_delivery()
        self.state["phases"].remove(entry)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "latest admitted"):
            lifecycle.validate_phase_pass_artifact(
                self.state, phase, entry["evidence"], historical_entry=entry,
            )

    def test_historical_basis_rejects_material_impact(self):
        phase, entry, _, _ = self.admit_phase_then_reconcile_delivery(material=True)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "succession is missing or material"):
            lifecycle.validate_phase_pass_artifact(
                self.state, phase, entry["evidence"], historical_entry=entry,
            )

    def test_historical_basis_rejects_material_intermediate_settlement(self):
        phase, entry, _, coordinator = self.admit_phase_then_reconcile_delivery(material=True)
        claim = json.loads(json.dumps(self.claim))
        claim.update(declared_effects=[], declared_plan_action="retain_plan")
        for row in claim["dispositions"]:
            row.update(disposition="existing_contract", effects=[])
        refinement.settle(self.run, claim)
        execution.replan_future(self.run, "FEAT-001", Path(coordinator["plan_path"]), 1)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "succession is missing or material"):
            lifecycle.validate_phase_pass_artifact(
                self.state, phase, entry["evidence"], historical_entry=entry,
            )

    def test_historical_basis_rejects_changed_plan(self):
        phase, entry, _, coordinator = self.admit_phase_then_reconcile_delivery()
        old = Path(coordinator["plan_path"])
        new = self.run / "plans/FEAT-001-v2.md"
        new.write_text(old.read_text().replace("Plan v1", "Plan v2"))
        new.with_suffix(".md.lock").write_text("locked\n")
        execution.replan_future(self.run, "FEAT-001", new, 1)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "material identity"):
            lifecycle.validate_phase_pass_artifact(
                self.state, phase, entry["evidence"], historical_entry=entry,
            )

    def test_historical_basis_rejects_changed_immutable_record(self):
        phase, entry, prior, _ = self.admit_phase_then_reconcile_delivery()
        path = self.run / "bases/FEAT-001" / (prior + ".json")
        value = json.loads(path.read_text())
        value["ownership"]["run_id"] = "foreign-run"
        private_json(path, value)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "basis changed"):
            lifecycle.validate_phase_pass_artifact(
                self.state, phase, entry["evidence"], historical_entry=entry,
            )

    def test_historical_basis_requires_impact_predecessor(self):
        phase, entry, prior, _ = self.admit_phase_then_reconcile_delivery()
        value = json.loads((self.run / "bases/FEAT-001" / (prior + ".json")).read_text())
        for path in (self.run / "objective-impact").glob("attempt-*.json"):
            if impact.canonical_digest(json.loads(path.read_text())) == value["impact_sha256"]:
                path.unlink()
                break
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.validate_phase_pass_artifact(
                self.state, phase, entry["evidence"], historical_entry=entry,
            )

    def test_fresh_specs_admit_without_early_commit_and_target_real_integration(self):
        coordinator = self.admit_and_prepare()
        integration = Path(coordinator["integration_worktree"])
        before = {
            str(p.relative_to(self.run)): p.read_bytes()
            for p in self.run.rglob("*")
            if p.is_file() and "worktrees" not in p.relative_to(self.run).parts
        }
        self.assertEqual(self.resolve()["path"], str(integration))
        self.assertEqual(
            before,
            {
                str(p.relative_to(self.run)): p.read_bytes()
                for p in self.run.rglob("*")
                if p.is_file() and "worktrees" not in p.relative_to(self.run).parts
            },
        )
        current_path = self.run / "current-state.json"
        current = json.loads(current_path.read_text())
        (integration / "test_contract.py").write_text("assert True\n")
        with mock.patch.object(lifecycle, "command") as command:
            with self.assertRaisesRegex(lifecycle.LifecycleError, "cleanliness"):
                lifecycle.run_owned_compose(
                    self.state, ["exec", "-T", "web", "python", "test_contract.py"]
                )
            command.assert_not_called()
        private_json(current_path, {**current, "current_phase": "sdlc-tdd"})
        with mock.patch.object(
            lifecycle,
            "command",
            return_value=mock.Mock(returncode=0, stdout="", stderr=""),
        ) as command:
            lifecycle.run_owned_compose(
                self.state, ["exec", "-T", "web", "python", "test_contract.py"]
            )
        self.assertIn(str(integration), command.call_args.args[0])
        with self.assertRaises(target.TargetError):
            self.resolve()
        self.assertEqual(
            self.resolve(require_clean=False)["head"], coordinator["integration_head"]
        )
        sealed = execution.seal_tdd_base(
            self.run, "FEAT-001", "Record test-first contract"
        )
        self.assertNotEqual(sealed["integration_head"], self.git("rev-parse", "HEAD"))
        self.assertEqual(self.resolve()["head"], sealed["integration_head"])
        phase = "sdlc-implement-plan"
        relative = f"evidence/phases/{phase}.json"
        private_json(
            Path(self.state["run_root"]) / relative,
            {
                "schema": lifecycle.PHASE_RESULT_SCHEMA,
                "phase": phase,
                "status": "PASS",
                "verification_id": self.state["verification_id"],
                "baseline_sha": self.state["git"]["baseline_sha"],
                "recorded_head": sealed["integration_head"],
                "objective_sha256": self.startup_observation["objective_sha256"],
                "basis_sha256": self.resolve()["basis_sha256"],
                "assertions": lifecycle.PHASE_REQUIRED_ASSERTIONS[phase],
            },
        )
        lifecycle.validate_phase_pass_artifact(self.state, phase, [relative])
        with mock.patch.object(
            lifecycle,
            "command",
            return_value=mock.Mock(returncode=0, stdout="", stderr=""),
        ) as command:
            lifecycle.run_owned_compose(self.state, ["up", "--detach"])
        argv = command.call_args.args[0]
        self.assertEqual(argv[argv.index("--project-directory") + 1], str(integration))
        self.git(
            "commit",
            "--allow-empty",
            "-qm",
            "Unexpected integration writer",
            path=integration,
        )
        with self.assertRaisesRegex(target.TargetError, "stale"):
            self.resolve()

    def test_completed_execution_targets_exact_promoted_checkout(self):
        coordinator = self.admit_and_prepare()
        execution.seal_tdd_base(self.run, "FEAT-001", "Test fixture TDD boundary")
        assignment = execution.prepare_wave(self.run, "FEAT-001", "WAVE-001")[0]
        execution.arm_task(
            self.run,
            "FEAT-001",
            "WAVE-001",
            "TASK-001",
            assignment["assignment_digest"],
        )
        execution.start_task(
            self.run,
            "FEAT-001",
            "WAVE-001",
            "TASK-001",
            assignment["assignment_digest"],
            "unit-fixture-worker",
            Path(assignment["scope_cwd"]),
        )
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir()
        (worker / "src/a.py").write_text("VALUE = 1\n")
        fixture_finish(
            self.run,
            "FEAT-001",
            "WAVE-001",
            "TASK-001",
            "fixture validation",
            "fixture review",
            "Implement fixture",
            summary="fixture complete",
        )
        execution.integrate_wave(self.run, "FEAT-001", "WAVE-001")
        execution.complete_wave(self.run, "FEAT-001", "WAVE-001", "fixture validation")
        execution.seal_feature(
            self.run, "FEAT-001", "fixture validation", "Seal fixture"
        )
        promoted = execution.promote_feature(self.run, "FEAT-001", "fixture validation")
        self.assertFalse(Path(coordinator["integration_worktree"]).exists())
        self.assertEqual(self.resolve()["path"], str(self.project))
        self.assertEqual(self.resolve()["head"], promoted["promoted_head"])
        self.state["git"]["promoted_sha"] = promoted["promoted_head"]
        self.assertEqual(self.resolve()["path"], str(self.project))
        (self.project / "unsealed.txt").write_text("dirty")
        with self.assertRaises(target.TargetError):
            self.resolve(require_clean=False)

    def test_worker_runtime_is_bound_to_one_live_scoped_assignment(self):
        self.admit_and_prepare()
        execution.seal_tdd_base(self.run, "FEAT-001", "Test fixture TDD boundary")
        assignment = execution.prepare_wave(self.run, "FEAT-001", "WAVE-001")[0]
        identity = {
            "worker_task": "TASK-001",
            "assignment_digest": assignment["assignment_digest"],
        }
        current_path = self.run / "current-state.json"
        current = json.loads(current_path.read_text())
        private_json(current_path, {**current, "current_phase": "sdlc-implement-plan"})
        with self.assertRaisesRegex(target.TargetError, "scope guard"):
            self.resolve(**identity)
        execution.arm_task(
            self.run, "FEAT-001", "WAVE-001", "TASK-001",
            assignment["assignment_digest"],
        )
        execution.start_task(
            self.run, "FEAT-001", "WAVE-001", "TASK-001",
            assignment["assignment_digest"], "runtime-fixture-worker",
            Path(assignment["scope_cwd"]),
        )
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir()
        (worker / "src/a.py").write_text("VALUE = 1\n")
        with self.assertRaises(target.TargetError):
            self.resolve(**identity)
        assignment_path = execution.assignment_path(self.run, 'FEAT-001', 'WAVE-001', 'TASK-001')
        resource = 'sdlc-three-tier-' + self.state['verification_id']
        with mock.patch.object(lifecycle, "command") as command:
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.run_owned_compose(self.state, ["up", "--detach"], **identity)
            command.assert_not_called()
        resources.acquire(self.home, assignment_path, resource, 'runtime-fixture-worker')
        self.assertEqual(os.environ['CODEX_THREAD_ID'], 'target-root')
        with mock.patch.object(
            lifecycle, "command",
            return_value=mock.Mock(returncode=0, stdout="passed", stderr=""),
        ) as command:
            lifecycle.run_owned_compose(self.state, ["up", "--detach"], **identity)
        self.assertEqual(resources.verify(self.home, assignment_path, resource)['status'], 'active')
        argv = command.call_args.args[0]
        self.assertEqual(argv[argv.index("--project-directory") + 1], str(worker))
        self.assertNotEqual(self.resolve()["path"], str(worker))
        for invalid in (
            {"worker_task": "TASK-001"},
            {**identity, "assignment_digest": "0" * 64},
            {**identity, "worker_task": "../TASK-001"},
            {**identity, "worker_task": "TASK-002"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(target.TargetError):
                self.resolve(**invalid)
        private_json(current_path, {**current, "current_phase": "sdlc-uat-tests"})
        with self.assertRaisesRegex(target.TargetError, "implementation wave"):
            self.resolve(**identity)
        private_json(current_path, {**current, "current_phase": "sdlc-implement-plan"})
        # A lease permits this worker even when independent slots exist.
        assignment_path = execution.assignment_path(self.run, 'FEAT-001', 'WAVE-001', 'TASK-001')
        resource = 'sdlc-three-tier-' + self.state['verification_id']
        resources.acquire(self.home, assignment_path, resource, 'runtime-fixture-worker')
        self.assertEqual(self.resolve(**identity)['path'], str(worker))
        resources.release(self.home, assignment_path, resource, 'runtime-fixture-worker')
        (worker / "unclaimed.txt").write_text("outside claims\n")
        with mock.patch.object(lifecycle, "command") as command:
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.run_owned_compose(self.state, ["up", "--detach"], **identity)
            command.assert_not_called()
        (worker / "unclaimed.txt").unlink()
        self.git("commit", "--allow-empty", "-qm", "Unexpected worker commit", path=worker)
        with self.assertRaises(target.TargetError):
            self.resolve(**identity)

    def test_target_rejects_foreign_checkpoint_and_unregistered_worktree(self):
        coordinator = self.admit_and_prepare()
        current_path = self.run / "current-state.json"
        current = json.loads(current_path.read_text())
        private_json(current_path, {**current, "project_id": "foreign"})
        with self.assertRaises(target.TargetError):
            self.resolve()
        private_json(current_path, current)
        self.git("worktree", "unlock", coordinator["integration_worktree"])
        self.git("worktree", "remove", coordinator["integration_worktree"])
        with self.assertRaises(target.TargetError):
            self.resolve()


if __name__ == "__main__":
    unittest.main()
