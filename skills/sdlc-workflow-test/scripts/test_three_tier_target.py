#!/usr/bin/env python3
"""Real-Git composition of fresh spec admission and harness execution targeting."""

from __future__ import annotations

import hashlib
import json
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
    ROOT / "sdlc-start/scripts",
    ROOT / "sdlc-prepare-execution/scripts",
    ROOT / "maintain-project-specs/scripts",
):
    sys.path.insert(0, str(directory))

import prompt_workspace as workspace  # noqa: E402
import sdlc_execution_core as execution  # noqa: E402
from project_specs_lib.transaction import publish_spec_pair  # noqa: E402
from test_sdlc_execution import REQUIREMENTS, DESIGN, PLAN  # noqa: E402
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
            os.environ, {"CODEX_HOME": str(self.home), "SKILLS_AGENT": "codex"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        initial = workspace.initialize(self.project, self.home, False, "code")
        self.manifest = Path(initial["workspace"])
        prompt = Path(initial["starter_prompt"])
        prompt.write_text(
            prompt.read_text().replace(
                "<!-- Required: replace this comment with your Ask. -->",
                "Implement the planned behavior and verify its observable acceptance criteria.",
            )
        )
        prompt.chmod(0o600)
        intake = workspace.intake(str(prompt), self.project, self.home)
        self.run_id = intake["run_id"]
        self.run = Path(intake["snapshot"]).parents[2]
        project_id = json.loads(self.manifest.read_text())["project_id"]
        private_json(
            self.manifest.parent / "active.lock",
            {
                "project_id": project_id,
                "project_root": str(self.project),
                "run_id": self.run_id,
                "status": "running",
                "owner_session_hash": "c" * 64,
                "created_at": lifecycle.utc_now(),
            },
        )
        private_json(
            self.run / "current-state.json",
            {
                "run_id": self.run_id,
                "project_id": project_id,
                "current_feature": "FEAT-001",
                "status": "running",
                "current_phase": "sdlc-start",
            },
        )
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
        refinement = workspace.load_requirements_refinement(self.run, required=True)
        refinement["status"] = "ready"
        refinement["extracted"]["outcomes"] = ["Implement the planned behavior."]
        refinement["compiled_requirements_sha256"] = hashlib.sha256(
            REQUIREMENTS.encode()
        ).hexdigest()
        workspace.save_requirements_refinement(self.run, refinement)
        private_json(
            self.run / "prompt-impact-claim.json",
            {
                "schema": "agentic-sdlc/prompt-impact-claim-v1",
                "prompt_id": refinement["prompt_id"],
                "revision": refinement["revision"],
                "intent_sha256": refinement["intent_sha256"],
                "dispositions": [
                    {
                        "statement": "outcomes:0001",
                        "disposition": "changed_contract",
                        "requirements": ["REQ-001"],
                        "design": ["FEAT-001"],
                        "effects": ["design", "requirements"],
                        "reason": None,
                    }
                ],
                "declared_effects": ["design", "requirements"],
                "declared_plan_action": "replan_required",
            },
        )

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
        workspace.verify_requirements_refinement_ready(self.manifest, self.run_id)
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.verify_requirements_refinement_contract(
                self.manifest, self.run_id
            )
        self.assertIn("tracked", caught.exception.message)
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
        workspace.verify_requirements_refinement_contract(self.manifest, self.run_id)
        plans = self.run / "plans"
        plans.mkdir(mode=0o700, exist_ok=True)
        plan = plans / "FEAT-001-v1.md"
        plan.write_text(PLAN.split("### TASK-002")[0])
        plan.chmod(0o600)
        plan.with_suffix(".md.lock").write_text("locked\n")
        plan.with_suffix(".md.lock").chmod(0o600)
        # The prepare phase owns this commit; design admission above did not commit.
        self.git("commit", "-qm", "Record canonical contract")
        return execution.prepare_execution(self.run, self.project, "FEAT-001", plan, 1)

    def resolve(self, **kwargs):
        return target.resolve_target(
            self.state, skills_root=ROOT, agent="codex", **kwargs
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
        execution.finish_task(
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
        with mock.patch.object(
            lifecycle, "command",
            return_value=mock.Mock(returncode=0, stdout="passed", stderr=""),
        ) as command:
            lifecycle.run_owned_compose(self.state, ["up", "--detach"], **identity)
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
        wave_path = execution.wave_path(self.run, "FEAT-001", "WAVE-001")
        wave = json.loads(wave_path.read_text())
        changed_wave = {**wave, "batches": [["TASK-001", "TASK-002"]]}
        private_json(wave_path, changed_wave)
        with self.assertRaisesRegex(target.TargetError, "one active worker"):
            self.resolve(**identity)
        private_json(wave_path, wave)
        (worker / "unclaimed.txt").write_text("outside claims\n")
        with mock.patch.object(lifecycle, "command") as command:
            with self.assertRaisesRegex(lifecycle.LifecycleError, "scope guard"):
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
        with self.assertRaisesRegex(target.TargetError, "checkpoint"):
            self.resolve()
        private_json(current_path, current)
        self.git("worktree", "unlock", coordinator["integration_worktree"])
        self.git("worktree", "remove", coordinator["integration_worktree"])
        with self.assertRaises(target.TargetError):
            self.resolve()


if __name__ == "__main__":
    unittest.main()
