#!/usr/bin/env python3
"""Composed real-Git test for worktree and task-implementer ownership."""

from __future__ import annotations

from datetime import datetime, timezone
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
import prompt_workspace_interop as task_interop
import prompt_workspace_waves as waves
from prompt_workspace_core import PromptWorkspaceError
from prompt_workspace_execution import RESULT_SCHEMA, sha256_json


FIXED = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
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
        with mock.patch.object(wm.secrets, "token_hex", return_value="a7c2f9"):
            outer = wm.add_worktree(
                cwd=scope,
                project=None,
                task_slug="composed-run",
            )
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
            clock=lambda: FIXED,
        )
        self.workspace = Path(initialized["workspace"])
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

    def test_private_interop_rejects_public_lifecycle_before_subprocess(self) -> None:
        workspace = json.loads(self.workspace.read_text(encoding="utf-8"))
        for action in ("add", "integrate", "remove"):
            with (
                self.subTest(action=action),
                mock.patch.object(task_interop.subprocess, "run") as run,
                self.assertRaisesRegex(
                    PromptWorkspaceError, "rejects public lifecycle actions"
                ),
            ):
                task_interop._call(workspace, [action])
            run.assert_not_called()

    def test_workers_remain_internal_to_the_outer_worktree_branch(self) -> None:
        plan = pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        self.assertEqual(plan["initial_head"], self.initial)
        interop = json.loads(
            (self.run_dir / "orchestration" / "interop.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(interop["mode"], "managed")
        self.assertEqual(interop["outer_scope"], "services/example")
        self.assertEqual(interop["task_scope"], "services/example")
        wave = json.loads(
            (
                self.run_dir / "orchestration" / "waves" / f"{plan['active_wave']}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIn(
            {"kind": "exact", "path": "README.md"},
            wave["coordinator_write_claims"],
        )
        with self.assertRaisesRegex(wm.WorktreeError, "still owns"):
            wm.integrate_worktree(
                cwd=self.primary,
                name=self.outer_name,
                validated_head=None,
                restart=False,
            )

        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        dispatched = pw.dispatch_wave(
            self.workspace, self.run_id, self.initial, clock=lambda: FIXED
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
            "- Overall status: done",
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
        inspected = wm.inspect_worktree(
            cwd=self.outer_scope, name=None, require_clean=True
        )
        self.assertEqual(inspected["head"], promoted["promoted_head"])
        ready = wm.integrate_worktree(
            cwd=self.primary,
            name=self.outer_name,
            validated_head=None,
            restart=False,
        )
        self.assertEqual(ready["status"], "validation-required")
        remote_branches = git("ls-remote", "--heads", "origin", cwd=self.outer)
        self.assertEqual(remote_branches.count("refs/heads/"), 1)
        self.assertIn("refs/heads/main", remote_branches)
        self.assertNotIn("codex/ti-", remote_branches)
        handoff = (self.run_dir / "handoff.md").read_text(encoding="utf-8")
        self.assertIn("- Overall status: done", handoff)
        self.assertIn("## Final Alignment", handoff)

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
        inspected = wm.task_lease_inspect(
            cwd=self.outer,
            name=self.outer_name,
            lease_id=str(local["lease_id"]),
            owner_kind="task-implementer",
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
                "_record_manifest_lease",
                side_effect=wm.WorktreeError(
                    "simulated crash before manifest release marker"
                ),
            ),
            self.assertRaisesRegex(wm.WorktreeError, "simulated crash"),
        ):
            wm.task_lease_release(
                cwd=self.outer,
                name=self.outer_name,
                lease_id=str(local["lease_id"]),
                promoted_head=self.initial,
                owner_kind="task-implementer",
            )
        workspace = json.loads(self.workspace.read_text(encoding="utf-8"))
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
