#!/usr/bin/env python3
"""Composed real-Git test for worktree and task-implementer ownership."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import prompt_workspace as pw
import prompt_workspace_contract_delta as contract_delta
import prompt_workspace_interop as task_interop
import prompt_workspace_lanes as task_lanes
import prompt_workspace_waves as waves
from prompt_workspace_core import PromptWorkspaceError
from prompt_workspace_execution import RESULT_SCHEMA, sha256_json


FIXED = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
FIXED_TEXT = FIXED.isoformat(timespec="seconds")
WORKTREE_SCRIPTS = Path(__file__).resolve().parents[2] / "worktree" / "scripts"
sys.path.insert(0, str(WORKTREE_SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "composed_worktree_manager", WORKTREE_SCRIPTS / "worktree_manager.py"
)
assert SPEC and SPEC.loader
wm = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wm
SPEC.loader.exec_module(wm)


def git(*arguments: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class WorktreeInteroperabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "composed workspace"
        self.root.mkdir()
        self.origin = self.root / "origin.git"
        git("init", "--bare", "-q", str(self.origin), cwd=self.root)
        self.primary = self.root / "repo"
        git("init", "-q", "-b", "main", str(self.primary), cwd=self.root)
        git("config", "user.name", "Interop Test", cwd=self.primary)
        git("config", "user.email", "interop@example.invalid", cwd=self.primary)
        scope = self.primary / "services" / "example"
        scope.mkdir(parents=True)
        (scope / "feature.txt").write_text("base\n", encoding="utf-8")
        docs = scope / "docs"
        docs.mkdir()
        (docs / "requirements.md").write_text(
            "# Requirements\n\nComposed Task Implementer test contract.\n",
            encoding="utf-8",
        )
        (docs / "design.md").write_text(
            "# Design\n\nComposed Task Implementer test design.\n",
            encoding="utf-8",
        )
        (scope / "AGENTS.md").write_text(
            "# Project instructions\n\nStable rules.\n", encoding="utf-8"
        )
        peer_scope = self.primary / "services" / "other"
        peer_scope.mkdir(parents=True)
        (peer_scope / "feature.txt").write_text("base\n", encoding="utf-8")
        git("add", "-A", cwd=self.primary)
        git("commit", "-qm", "initial", cwd=self.primary)
        git("remote", "add", "origin", str(self.origin), cwd=self.primary)
        git("push", "-qu", "origin", "main", cwd=self.primary)
        git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.origin)
        git("fetch", "-q", "origin", cwd=self.primary)
        git(
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
            cwd=self.primary,
        )
        git("switch", "-qc", "local-source", cwd=self.primary)
        outer = wm.task_lane_ensure(cwd=scope, project=None)
        self.lane_id = str(outer["lane_id"])
        self.outer_name = str(outer["name"])
        self.outer_branch = str(outer["branch"])
        self.outer = Path(str(outer["worktree"]))
        self.outer_scope = self.outer / "services" / "example"
        git("config", "user.name", "Interop Test", cwd=self.outer)
        git("config", "user.email", "interop@example.invalid", cwd=self.outer)
        self.initial = git("rev-parse", "HEAD", cwd=self.outer)
        self.codex_home = self.root / "codex-home"
        initialized = pw.init_workspace(
            self.outer,
            "services/example",
            self.codex_home,
            lane=outer,
            clock=lambda: FIXED,
        )
        self.workspace = Path(initialized["workspace"])
        self.project_agent_gate = mock.patch.object(
            waves,
            "verify_project_agent_contract",
            return_value={"status": "ok", "outcome": "not-needed"},
        )
        self.project_agent_gate.start()
        self.addCleanup(self.project_agent_gate.stop)
        self.refinement_gate = mock.patch.object(
            waves,
            "verify_requirements_refinement_contract",
            side_effect=lambda _workspace, _run_dir, run_state: {
                "impact": {
                    "revision": run_state["latest_revision"],
                    "intent_sha256": run_state["latest_intent_sha256"],
                    "spec_receipt_sha256": "d" * 64,
                    "effects": [],
                    "plan_action": "retain_plan",
                },
                "impact_sha256": "e" * 64,
            },
        )
        self.refinement_gate.start()
        self.addCleanup(self.refinement_gate.stop)
        self.impact_plan_gate = mock.patch.object(
            waves,
            "verify_prompt_impact_plan",
            return_value={"status": "settled"},
        )
        self.impact_plan_gate.start()
        self.addCleanup(self.impact_plan_gate.stop)
        prompt = pw.create_prompt(
            self.workspace,
            "Implement one composed task",
            clock=lambda: FIXED,
            id_factory=lambda: "c" * 32,
        )
        self.prompt = Path(prompt["path"])
        text = self.prompt.read_text(encoding="utf-8")
        text = (
            text.replace(
                "<!-- Required: describe what must be true when the work is complete. -->",
                "The composed file is updated.",
            )
            .replace(
                "- [ ] <!-- Required: add an observable, testable completion criterion. -->",
                "- [ ] The task commit reaches the outer branch.",
            )
            .replace(
                "<!-- Required: name expected checks or ask Codex to derive them from the repo. -->",
                "Inspect the composed file.",
            )
        )
        self.prompt.write_text(text, encoding="utf-8")
        self.prompt.chmod(0o600)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            self.prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED,
        )
        self.run_id = str(snapshot["run_id"])
        self.run_dir = Path(snapshot["manifest"]).parent
        manifest = json.loads(
            (self.run_dir / "manifest.json").read_text(encoding="utf-8")
        )
        bound = manifest["revisions"][0]
        handoff = f"""# Task Implementer Handoff

## Run

- Run ID: {self.run_id}
- Run manifest: {self.run_dir / "manifest.json"}
- Prompt ID: {manifest["prompt_id"]}
- Bound revision: {bound["revision"]}
- Bound SHA-256: {bound["sha256"]}
- Bound snapshot path: {self.run_dir / bound["snapshot"]}
- Last invoked at: 2026-07-16T12:00:00+00:00
- Overall status: running

## Reconciliation

- State: none

## Task Queue

### task-1

- Status: pending
- Depends on: none
- Write claims: exact: services/example/feature.txt
- Conflict domains: files:feature.txt
- Implementation steps: update only the claimed feature file
- Validation: inspect feature.txt
- End-to-end validation: verify the nested worktree result
- Done criteria: feature.txt contains the composed update
"""
        handoff_path = self.run_dir / "handoff.md"
        handoff_path.write_text(handoff, encoding="utf-8")
        handoff_path.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _open_lane_generation(self, **arguments: object) -> dict[str, object]:
        prepared = wm.task_lane_generation_prepare(**arguments)
        return wm.task_lane_generation_open(
            **arguments,
            review_token=str(prepared["review_token"]),
            reviewed_tree=str(prepared["candidate_tree"]),
            reviewed_paths_sha256=str(prepared["paths_sha256"]),
        )

    def _claim_peer_lane(self, claims: list[dict[str, str]]) -> dict[str, object]:
        peer = wm.task_lane_ensure(
            cwd=self.primary / "services" / "other", project=None
        )
        acquired = self._open_lane_generation(
            cwd=Path(str(peer["scope_cwd"])),
            workspace=self.root / "peer-workspace.json",
            run_id="run-peer",
            task_scope="services/other",
            expected_head=str(peer["lane_head"]),
            claims=[],
        )
        return wm.task_lane_generation_claims(
            cwd=Path(str(peer["worktree"])),
            name=str(peer["name"]),
            generation=int(acquired["generation"]),
            lease_id=str(acquired["token"]),
            claims=claims,
        )

    def _seal_terminal_lifecycle(self) -> dict[str, object]:
        coordinator = waves.load_coordinator_state(self.run_dir)
        assert coordinator is not None
        wave_id = coordinator.get("active_wave")
        if not isinstance(wave_id, str):
            wave_id = str(coordinator["waves"][-1]["wave_id"])
        wave = json.loads(
            (self.run_dir / "orchestration" / "waves" / f"{wave_id}.json").read_text(
                encoding="utf-8"
            )
        )
        root = self.codex_home / "project-specs" / "example" / "terminal"
        instructions = root / "project-instructions"
        instructions.mkdir(parents=True, exist_ok=True)
        agents = self.outer_scope / "AGENTS.md"
        agents_sha256 = (
            hashlib.sha256(agents.read_bytes()).hexdigest()
            if agents.is_file()
            else None
        )
        instruction_state = instructions / "state.json"
        instruction_state.write_text(
            json.dumps(
                {
                    "schema": "project-agent-instructions.state.v3",
                    "project_root": str(self.outer_scope.resolve()),
                    "project_scope": "services/example",
                    "target_path": str(agents.resolve()),
                    "target_sha256": agents_sha256,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        requirements = self.outer_scope / "docs" / "requirements.md"
        design = self.outer_scope / "docs" / "design.md"
        lifecycle = root / "lifecycle.json"
        lifecycle.write_text(
            json.dumps(
                {
                    "schema": "maintain-project-specs.lifecycle.v1",
                    "phase": "sealed",
                    "project_scope": "services/example",
                    "git_head_at_prompt": wave["base_commit"],
                    "requirements_sha256": hashlib.sha256(
                        requirements.read_bytes()
                    ).hexdigest(),
                    "design_sha256": hashlib.sha256(design.read_bytes()).hexdigest(),
                    "receipt_sha256": "a" * 64,
                    "project_instructions_state_sha256": hashlib.sha256(
                        instruction_state.read_bytes()
                    ).hexdigest(),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return contract_delta.adopt_contract_delta(
            self.workspace, self.run_id, lifecycle, clock=lambda: FIXED
        )

    def test_replan_claims_block_a_conflicting_live_lane_before_state_write(
        self,
    ) -> None:
        self._claim_peer_lane([{"kind": "domain", "path": "shared-contract:peer"}])
        original = pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        coordinator_path = self.run_dir / "orchestration" / "coordinator.json"
        before = coordinator_path.read_bytes()
        handoff_path = self.run_dir / "handoff.md"
        handoff_path.write_text(
            handoff_path.read_text(encoding="utf-8").replace(
                "files:feature.txt", "shared-contract:peer"
            ),
            encoding="utf-8",
        )
        handoff_path.chmod(0o600)

        with self.assertRaises(PromptWorkspaceError) as caught:
            pw.replan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)

        self.assertEqual(caught.exception.code, "WORKTREE_CONFLICT")
        self.assertIn("repository claim conflicts", str(caught.exception))
        self.assertEqual(coordinator_path.read_bytes(), before)
        self.assertEqual(json.loads(before)["plan_sha256"], original["plan_sha256"])
        self.assertEqual(
            list((self.run_dir / "orchestration" / "waves").glob("wave-r*.json")),
            [],
        )

    def test_exclusive_domain_classes_conflict_across_lanes_with_distinct_keys(
        self,
    ) -> None:
        handoff_path = self.run_dir / "handoff.md"
        handoff_path.write_text(
            handoff_path.read_text(encoding="utf-8").replace(
                "files:feature.txt", "publication:first"
            ),
            encoding="utf-8",
        )
        handoff_path.chmod(0o600)
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        sentinel = {
            "kind": "domain",
            "path": f"{waves.EXCLUSIVE_DOMAIN_CLAIM_PREFIX}publication",
        }
        lane = wm.load_lane(self.primary, self.lane_id)
        assert lane is not None
        self.assertIn({**sentinel, "generation": 1}, lane["claims"])

        peer_text = (
            handoff_path.read_text(encoding="utf-8")
            .replace("services/example/feature.txt", "services/other/feature.txt")
            .replace("publication:first", "publication:second")
        )
        peer_claims = waves._repository_claims(
            {"scope": "services/other"}, waves.parse_task_plans(peer_text)
        )
        self.assertIn(sentinel, peer_claims)
        with self.assertRaisesRegex(wm.WorktreeError, "claim conflicts"):
            self._claim_peer_lane(peer_claims)

    def test_private_interop_rejects_public_lifecycle_before_subprocess(self) -> None:
        workspace = json.loads(self.workspace.read_text(encoding="utf-8"))
        for action in ("add", "integrate", "remove"):
            with (
                self.subTest(action=action),
                mock.patch.object(task_interop.subprocess, "Popen") as popen,
                self.assertRaisesRegex(
                    PromptWorkspaceError, "rejects public lifecycle actions"
                ),
            ):
                task_interop._call(workspace, [action])
            popen.assert_not_called()

    def test_integration_review_rejection_is_forwarded_to_lane_owner(self) -> None:
        workspace = json.loads(self.workspace.read_text(encoding="utf-8"))
        with mock.patch.object(
            task_lanes,
            "workspace_lane_call",
            return_value={"status": "correction-required"},
        ) as lane_call:
            result = task_lanes.integrate_lane(
                workspace,
                validated_head=None,
                restart=False,
                review_rejected_head="a" * 40,
                review_findings_sha256="b" * 64,
            )
        self.assertEqual(result["status"], "correction-required")
        lane_call.assert_called_once_with(
            workspace,
            [
                "task-lane-integrate",
                "--lane-id",
                self.lane_id,
                "--review-rejected-head",
                "a" * 40,
                "--review-findings-sha256",
                "b" * 64,
            ],
        )

    def test_non_run_adapters_never_checkpoint_an_idle_dirty_lane(self) -> None:
        before = git("rev-parse", "HEAD", cwd=self.outer)
        dirty = self.outer / "ordinary-idle-dirt.txt"
        dirty.write_text("preserve without checkpoint\n", encoding="utf-8")
        workspace = json.loads(self.workspace.read_text(encoding="utf-8"))

        with self.assertRaisesRegex(
            PromptWorkspaceError, "completely clean before reuse"
        ):
            pw.initialize_project_workspace(
                self.outer_scope, self.codex_home, clock=lambda: FIXED
            )
        reopened = pw.reuse_project_workspace(self.outer_scope, self.codex_home)
        self.assertEqual(reopened["status"], "reused")
        self.assertEqual(reopened["lane_state"], "idle")
        self.assertEqual(reopened["lane_worktree"], str(self.outer))
        integrated = pw.integrate_lane(workspace, validated_head=None, restart=False)
        self.assertEqual(integrated["status"], "already-integrated")
        with self.assertRaisesRegex(PromptWorkspaceError, "dirty"):
            pw.remove_lane(workspace)

        self.assertEqual(git("rev-parse", "HEAD", cwd=self.outer), before)
        self.assertEqual(
            git("status", "--porcelain", cwd=self.outer),
            "?? ordinary-idle-dirt.txt",
        )
        self.assertIsNone(
            wm.load_checkpoint(self.primary, self.lane_id, required=False)
        )
        self.assertTrue(dirty.is_file())

    def test_workers_remain_internal_to_the_outer_worktree_branch(self) -> None:
        (self.outer / "checkpoint-root.txt").write_text(
            "root checkpoint\n", encoding="utf-8"
        )
        (self.outer_scope / "checkpoint-selected.txt").write_text(
            "selected checkpoint\n", encoding="utf-8"
        )
        (self.outer / "services" / "other" / "checkpoint-sibling.txt").write_text(
            "sibling checkpoint\n", encoding="utf-8"
        )
        checkpoint_preparation = pw.prepare_run_checkpoint(self.workspace, self.run_id)
        self.assertTrue(checkpoint_preparation["requires_review"])
        self.assertEqual(
            checkpoint_preparation["paths"],
            [
                "checkpoint-root.txt",
                "services/example/checkpoint-selected.txt",
                "services/other/checkpoint-sibling.txt",
            ],
        )
        plan = pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        baseline = str(plan["initial_head"])
        self.assertNotEqual(baseline, self.initial)
        self.assertEqual(git("rev-parse", f"{baseline}^", cwd=self.outer), self.initial)
        self.assertFalse((self.primary / "checkpoint-root.txt").exists())
        interop = json.loads(
            (self.run_dir / "orchestration" / "interop.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(interop["mode"], "lane")
        self.assertEqual(interop["outer_scope"], "services/example")
        self.assertEqual(interop["task_scope"], "services/example")
        wave = json.loads(
            (
                self.run_dir / "orchestration" / "waves" / f"{plan['active_wave']}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIn(
            {"kind": "exact", "path": "services/example/README.md"},
            wave["coordinator_write_claims"],
        )
        with self.assertRaisesRegex(wm.WorktreeError, "Task Implementer lanes"):
            wm.integrate_worktree(
                cwd=self.primary,
                name=self.outer_name,
                validated_head=None,
                restart=False,
            )

        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        dispatched = pw.dispatch_wave(
            self.workspace, self.run_id, baseline, clock=lambda: FIXED
        )
        assignment_path = Path(str(dispatched["assignments"][0]))
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        worker = Path(str(assignment["worktree"]))
        with self.assertRaisesRegex(wm.WorktreeError, "must not push"):
            wm.publication_guard(cwd=worker, action="push")
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(Path(str(assignment["scope_cwd"])))
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                str(assignment["assignment_sha256"]),
                FIXED_TEXT,
                session_id="composed-worker",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)
        (worker / "services" / "example" / "feature.txt").write_text(
            "composed\n", encoding="utf-8"
        )
        git("add", "-A", cwd=worker)
        git("commit", "-qm", "Implement composed task", cwd=worker)
        commit = git("rev-parse", "HEAD", cwd=worker)
        result = {
            "schema": RESULT_SCHEMA,
            "run_id": self.run_id,
            "wave_id": "wave-001",
            "task_id": "task-1",
            "assignment_sha256": assignment["assignment_sha256"],
            "status": "committed",
            "commit": commit,
            "changed_paths": ["services/example/feature.txt"],
            "summary": "Implemented composed task",
            "decisions": [],
            "open_risks": [],
            "validation": "focused validation passed",
            "end_to_end_validation": "composed behavior observed",
            "code_review": "code-review completed with no findings",
            "completed_at": "2026-07-16T12:01:00+00:00",
        }
        result["result_sha256"] = sha256_json(result)
        result_path = Path(str(assignment["result_path"]))
        result_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result_path.chmod(0o600)
        pw.accept_task_result(
            self.workspace, self.run_id, "task-1", clock=lambda: FIXED
        )
        integrated = pw.integrate_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration = Path(str(prepared["integration_worktree"]))
        evidence = self.run_dir / "orchestration" / "evidence" / "wave-001.json"
        evidence.parent.mkdir(mode=0o700)
        evidence.write_text(
            json.dumps(
                {
                    "integration_head": git("rev-parse", "HEAD", cwd=integration),
                    "bound_revision": "r0001",
                    "steering_sha256": "none",
                    "validation": "combined tests passed",
                    "code_review": "integration review completed",
                    "steering_reconciled": True,
                }
            ),
            encoding="utf-8",
        )
        evidence.chmod(0o600)
        promoted = pw.promote_wave(
            self.workspace, self.run_id, evidence, clock=lambda: FIXED
        )
        self.assertEqual(promoted["promoted_head"], integrated["integrated_head"])
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=self.outer), promoted["promoted_head"]
        )
        self._seal_terminal_lifecycle()
        cleaned = pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(cleaned["status"], "done")
        self.assertFalse(worker.exists())
        self.assertFalse(integration.exists())
        with (
            mock.patch.object(
                waves,
                "release_interop",
                side_effect=PromptWorkspaceError(
                    "WORKTREE_CONFLICT", "simulated interrupted release"
                ),
            ),
            self.assertRaises(PromptWorkspaceError),
        ):
            pw.finalize_run(
                self.workspace,
                self.run_id,
                "changed-surface align passed",
                clock=lambda: FIXED,
            )
        self.assertIn(
            "- Overall status: running",
            (self.run_dir / "handoff.md").read_text(encoding="utf-8"),
        )
        with self.assertRaisesRegex(wm.WorktreeError, "still owns"):
            wm.inspect_worktree(cwd=self.outer_scope, name=None, require_clean=True)
        routed = pw.route_project_prompt(
            self.outer_scope,
            self.codex_home,
            self.prompt.name,
            clock=lambda: FIXED,
        )
        self.assertEqual(routed["action"], "finalize")
        self.assertEqual(routed["outcome"], "TASK_LEASE_RELEASE_REQUIRED")
        finalized = pw.finalize_run(
            self.workspace,
            self.run_id,
            "changed-surface align passed",
            clock=lambda: FIXED,
        )
        self.assertEqual(finalized["interop"]["status"], "released")
        self.assertEqual(finalized["interop"]["primary"], str(self.primary.resolve()))
        self.assertEqual(finalized["interop"]["source_branch"], "local-source")
        terminal_receipt = (
            self.run_dir
            / "orchestration"
            / "terminal-lifecycle-seals"
            / "wave-001.json"
        )
        terminal_receipt.unlink()
        agents = self.outer_scope / "AGENTS.md"
        agents.write_text(
            agents.read_text(encoding="utf-8") + "\n<!-- late sealed provenance -->\n",
            encoding="utf-8",
        )
        recovered = self._seal_terminal_lifecycle()
        self.assertEqual(recovered["status"], "terminal-recovered")
        self.assertEqual(recovered["generation"], 2)
        self.assertEqual(recovered["paths"], ["services/example/AGENTS.md"])
        routed_complete = pw.route_project_prompt(
            self.outer_scope,
            self.codex_home,
            self.prompt.name,
            clock=lambda: FIXED,
        )
        self.assertEqual(routed_complete["outcome"], "ALREADY_COMPLETE")
        inspected = wm.inspect_worktree(
            cwd=self.outer_scope, name=None, require_clean=True
        )
        self.assertEqual(inspected["head"], recovered["promoted_head"])
        ready = wm.task_lane_integrate(
            cwd=self.primary,
            lane_id=self.lane_id,
            validated_head=None,
            restart=False,
        )
        self.assertEqual(ready["status"], "validation-required")
        integrated_lane = wm.task_lane_integrate(
            cwd=self.primary,
            lane_id=self.lane_id,
            validated_head=str(ready["candidate_head"]),
            restart=False,
        )
        self.assertEqual(integrated_lane["status"], "integrated")
        self.assertEqual(integrated_lane["integrated_generations"], [1, 2])
        self.assertEqual(
            (self.primary / "checkpoint-root.txt").read_text(encoding="utf-8"),
            "root checkpoint\n",
        )
        self.assertEqual(
            (
                self.primary / "services" / "example" / "checkpoint-selected.txt"
            ).read_text(encoding="utf-8"),
            "selected checkpoint\n",
        )
        self.assertEqual(
            (self.primary / "services" / "other" / "checkpoint-sibling.txt").read_text(
                encoding="utf-8"
            ),
            "sibling checkpoint\n",
        )
        lane_state = wm.load_lane(self.primary, self.lane_id)
        assert lane_state is not None
        self.assertEqual(lane_state["state"], "idle")
        self.assertIsNone(
            wm.load_checkpoint(self.primary, self.lane_id, required=False)
        )
        remote_branches = git("ls-remote", "--heads", "origin", cwd=self.outer)
        self.assertEqual(remote_branches.count("refs/heads/"), 1)
        self.assertIn("refs/heads/main", remote_branches)
        self.assertNotIn("codex/ti-", remote_branches)
        handoff = (self.run_dir / "handoff.md").read_text(encoding="utf-8")
        self.assertIn("- Overall status: done", handoff)
        self.assertIn("## Final Alignment", handoff)

    def test_plan_checkpoints_dirty_lane_and_uses_post_checkpoint_baseline(
        self,
    ) -> None:
        before = self.initial
        primary_head = git("rev-parse", "HEAD", cwd=self.primary)
        (self.outer_scope / "feature.txt").write_text(
            "pre-run lane change\n", encoding="utf-8"
        )
        sibling = self.outer / "services" / "other" / "checkpoint.txt"
        sibling.write_text("related sibling change\n", encoding="utf-8")

        with self.assertRaises(PromptWorkspaceError) as caught:
            pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        self.assertEqual(caught.exception.code, "CHECKPOINT_REVIEW_REQUIRED")
        preparation = task_interop.load_checkpoint_preparation(
            self.run_dir,
            claims=waves._repository_claims(
                {"scope": "services/example"},
                waves.parse_task_plans(
                    (self.run_dir / "handoff.md").read_text(encoding="utf-8")
                ),
            ),
        )
        assert preparation is not None
        self.assertEqual(
            preparation["paths"],
            [
                "services/example/feature.txt",
                "services/other/checkpoint.txt",
            ],
        )

        planned = pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)

        checkpoint_head = git("rev-parse", "HEAD", cwd=self.outer)
        self.assertNotEqual(checkpoint_head, before)
        self.assertEqual(planned["initial_head"], checkpoint_head)
        self.assertEqual(
            git("rev-parse", f"{checkpoint_head}^", cwd=self.outer), before
        )
        self.assertEqual(
            git("show", "-s", "--format=%s", checkpoint_head, cwd=self.outer),
            wm.TASK_LANE_CHECKPOINT_MESSAGE,
        )
        self.assertEqual(git("status", "--porcelain", cwd=self.outer), "")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.primary), primary_head)
        receipt = task_interop.load_checkpoint_receipt(self.run_dir)
        assert receipt is not None
        self.assertEqual(receipt["before_head"], before)
        self.assertEqual(receipt["initial_head"], checkpoint_head)
        self.assertEqual(
            receipt["paths"],
            [
                "services/example/feature.txt",
                "services/other/checkpoint.txt",
            ],
        )
        wave = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "waves"
                / f"{planned['active_wave']}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIsNone(wave["base_commit"])
        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(prepared["base_commit"], checkpoint_head)

    def test_plan_recovers_open_before_local_receipts_without_duplicate_commit(
        self,
    ) -> None:
        before = self.initial
        (self.outer / "pre-receipt.txt").write_text(
            "checkpoint before task receipt\n", encoding="utf-8"
        )
        handoff = (self.run_dir / "handoff.md").read_text(encoding="utf-8")
        claims = waves._repository_claims(
            {"scope": "services/example"}, waves.parse_task_plans(handoff)
        )
        workspace = json.loads(self.workspace.read_text(encoding="utf-8"))
        preparation = task_interop.prepare_checkpoint(
            workspace,
            self.run_dir,
            self.workspace,
            before,
            claims,
        )
        opened = wm.task_lane_generation_open(
            cwd=self.outer_scope,
            workspace=self.workspace,
            run_id=self.run_id,
            task_scope="services/example",
            expected_head=before,
            claims=claims,
            review_token=str(preparation["review_token"]),
            reviewed_tree=str(preparation["candidate_tree"]),
            reviewed_paths_sha256=str(preparation["paths_sha256"]),
        )
        checkpoint_head = str(opened["checkpoint_head"])

        planned = pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)

        self.assertEqual(planned["initial_head"], checkpoint_head)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.outer), checkpoint_head)
        self.assertEqual(
            git("rev-parse", f"{checkpoint_head}^", cwd=self.outer), before
        )
        receipt = task_interop.load_checkpoint_receipt(self.run_dir)
        assert receipt is not None
        self.assertEqual(receipt["before_head"], before)
        self.assertEqual(receipt["initial_head"], checkpoint_head)
        self.assertEqual(receipt["status"], "recovered")

    def test_plan_requires_fresh_review_after_hook_modified_checkpoint(self) -> None:
        before = self.initial
        requested = self.outer / "requested.txt"
        requested.write_text("requested\n", encoding="utf-8")
        hook = self.primary / ".git" / "hooks" / "pre-commit"
        hook.write_text(
            "#!/bin/sh\nprintf 'hook change\\n' > hook-added.txt\n"
            "git add hook-added.txt\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)

        with self.assertRaises(PromptWorkspaceError) as first:
            pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        self.assertEqual(first.exception.code, "CHECKPOINT_REVIEW_REQUIRED")
        initial_preparation = json.loads(
            (
                self.run_dir / "orchestration" / "lane-checkpoint-preparation.json"
            ).read_text(encoding="utf-8")
        )

        with self.assertRaises(PromptWorkspaceError) as hook_changed:
            pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        self.assertEqual(hook_changed.exception.code, "WORKTREE_CONFLICT")
        self.assertIn("requires review", str(hook_changed.exception))
        checkpoint_head = git("rev-parse", "HEAD", cwd=self.outer)
        self.assertNotEqual(checkpoint_head, before)

        with self.assertRaises(PromptWorkspaceError) as refreshed:
            pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        self.assertEqual(refreshed.exception.code, "CHECKPOINT_REVIEW_REQUIRED")
        reviewed_preparation = json.loads(
            (
                self.run_dir / "orchestration" / "lane-checkpoint-preparation.json"
            ).read_text(encoding="utf-8")
        )
        self.assertNotEqual(
            reviewed_preparation["review_token"],
            initial_preparation["review_token"],
        )
        self.assertEqual(
            reviewed_preparation["paths"], ["hook-added.txt", "requested.txt"]
        )

        planned = pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)

        self.assertEqual(planned["initial_head"], checkpoint_head)
        self.assertEqual(git("status", "--porcelain", cwd=self.outer), "")
        receipt = task_interop.load_checkpoint_receipt(self.run_dir)
        assert receipt is not None
        self.assertEqual(receipt["initial_head"], checkpoint_head)
        self.assertEqual(receipt["paths"], ["hook-added.txt", "requested.txt"])

    def test_managed_write_claim_may_span_the_full_checkout(self) -> None:
        handoff_path = self.run_dir / "handoff.md"
        handoff_path.write_text(
            handoff_path.read_text(encoding="utf-8").replace(
                "exact: services/example/feature.txt", "exact: README.md"
            ),
            encoding="utf-8",
        )
        handoff_path.chmod(0o600)
        planned = pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        self.assertEqual(planned["promotion_source"], "managed-local")
        self.assertIn(
            {"kind": "exact", "path": "README.md"},
            planned["waves"][0]["tasks"][0]["write_claims"],
        )

    def test_successive_promotions_advance_the_outer_lease_history(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        workspace = json.loads(self.workspace.read_text(encoding="utf-8"))
        first_path = self.outer_scope / "feature.txt"
        first_path.write_text("first promotion\n", encoding="utf-8")
        git("add", "-A", cwd=self.outer)
        git("commit", "-qm", "first outer promotion", cwd=self.outer)
        first = git("rev-parse", "HEAD", cwd=self.outer)
        task_interop.record_promotion(workspace, self.run_dir, first)

        first_path.write_text("second promotion\n", encoding="utf-8")
        git("add", "-A", cwd=self.outer)
        git("commit", "-qm", "second outer promotion", cwd=self.outer)
        second = git("rev-parse", "HEAD", cwd=self.outer)
        task_interop.record_promotion(workspace, self.run_dir, second)

        local = task_interop.load_interop(self.run_dir)
        assert local is not None
        inspected = wm.task_lane_generation_inspect(
            cwd=self.outer,
            name=self.outer_name,
            generation=int(local["generation"]),
            lease_id=str(local["lease_id"]),
        )
        self.assertEqual(local["promoted_head"], second)
        self.assertEqual(inspected["promotion_heads"], [self.initial, first, second])

    def test_resume_repairs_exact_terminal_receipt_and_rejects_stale_sha(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        interop_path = self.run_dir / "orchestration" / "interop.json"
        local = json.loads(interop_path.read_text(encoding="utf-8"))
        wm.task_lease_promote(
            cwd=self.outer,
            name=self.outer_name,
            lease_id=str(local["lease_id"]),
            promoted_head=self.initial,
            expected_head=self.initial,
            owner_kind="task-implementer",
        )
        with (
            mock.patch.object(
                wm,
                "retire_released_task_lease",
                side_effect=wm.WorktreeError(
                    "simulated crash before terminal lease retirement"
                ),
            ),
            self.assertRaisesRegex(wm.WorktreeError, "simulated crash"),
        ):
            wm.task_lane_generation_release(
                cwd=self.outer,
                name=self.outer_name,
                generation=int(local["generation"]),
                lease_id=str(local["lease_id"]),
                promoted_head=self.initial,
            )
        workspace = json.loads(self.workspace.read_text(encoding="utf-8"))
        before_observation = interop_path.read_bytes()
        observed = task_interop.observe_managed_state(
            workspace,
            self.run_dir,
            local,
            initial_head=self.initial,
            workspace_path=self.workspace,
        )
        self.assertEqual(interop_path.read_bytes(), before_observation)
        self.assertEqual(
            observed["repairs"],
            {"promoted_head": self.initial, "released": True},
        )
        reconciled = task_interop.acquire_interop(
            workspace,
            self.run_dir,
            self.workspace,
            self.initial,
        )
        self.assertEqual(reconciled["promoted_head"], self.initial)
        self.assertTrue(reconciled["released"])
        ownership = wm.load_manifest(self.primary, self.outer_name)
        assert ownership is not None
        self.assertEqual(ownership.lease_state, "released")

        stale = dict(reconciled)
        stale["promoted_head"] = "f" * 40
        interop_path.write_text(json.dumps(stale), encoding="utf-8")
        with self.assertRaisesRegex(PromptWorkspaceError, "promoted head changed"):
            task_interop.acquire_interop(
                workspace,
                self.run_dir,
                self.workspace,
                self.initial,
            )


if __name__ == "__main__":
    unittest.main()
