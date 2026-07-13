#!/usr/bin/env python3
"""Focused tests for task planning, execution-plane, and session gates."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().with_name("prompt_workspace.py")
SPEC = importlib.util.spec_from_file_location("prompt_workspace_execution_test", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import invariant.
    raise RuntimeError("could not load prompt_workspace.py")
pw = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pw
SPEC.loader.exec_module(pw)


FIXED = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TaskExecutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git("init", "-q", cwd=self.repo)
        (self.repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        git("add", "tracked.txt", cwd=self.repo)
        git(
            "-c",
            "user.name=Execution Test",
            "-c",
            "user.email=execution@example.invalid",
            "commit",
            "-qm",
            "initial",
            cwd=self.repo,
        )
        self.scope = self.repo / "services" / "example"
        self.scope.mkdir(parents=True)
        self.codex_home = self.root / "codex"
        result = pw.init_workspace(
            self.repo,
            "services/example",
            self.codex_home,
            clock=lambda: FIXED,
        )
        self.workspace = Path(result["workspace"])
        prompt_result = pw.create_prompt(
            self.workspace,
            "Implement three tasks",
            clock=lambda: FIXED,
            id_factory=lambda: "a" * 32,
        )
        self.prompt = Path(prompt_result["path"])
        self.complete_prompt()
        snapshot = pw.snapshot_prompt(
            self.workspace,
            self.prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED,
        )
        self.run_id = str(snapshot["run_id"])
        self.run_dir = Path(snapshot["manifest"]).parent
        self.write_handoff()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def complete_prompt(self) -> None:
        text = self.prompt.read_text(encoding="utf-8")
        text = text.replace(
            "<!-- Required: describe what must be true when the work is complete. -->",
            "Both tasks are implemented.",
        )
        text = text.replace(
            "- [ ] <!-- Required: add an observable, testable completion criterion. -->",
            "- [ ] Both task checkpoints pass.",
        )
        text = text.replace(
            "<!-- Required: name expected checks or ask Codex to derive them from the repo. -->",
            "Run focused execution-plane tests.",
        )
        self.prompt.write_text(text, encoding="utf-8")
        self.prompt.chmod(0o600)

    def edit_prompt(self, replacement: str) -> None:
        text = self.prompt.read_text(encoding="utf-8")
        text = text.replace("Both tasks are implemented.", replacement)
        self.prompt.write_text(text, encoding="utf-8")
        self.prompt.chmod(0o600)

    def resolve_steering(
        self, revision: str, disposition: str = "applied"
    ) -> None:
        manifest = json.loads(
            (self.run_dir / "manifest.json").read_text(encoding="utf-8")
        )
        pw.resolve_steering_revision(
            self.run_dir,
            manifest["revisions"],
            revision,
            disposition,
            clock=lambda: FIXED.replace(minute=1),
        )

    def write_handoff(self) -> None:
        manifest_path = self.run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bound = manifest["revisions"][-1]
        handoff = f"""# Task Implementer Handoff

## Run

- Run ID: {self.run_id}
- Run manifest: {manifest_path}
- Prompt ID: {manifest['prompt_id']}
- Bound revision: {bound['revision']}
- Bound SHA-256: {bound['sha256']}
- Bound snapshot path: {self.run_dir / bound['snapshot']}
- Current task: none
- Last completed task: none
- Last commit: none
- Last invoked at: 2026-07-13T12:00:00+00:00
- Overall status: prepared

## Reconciliation

- State: none
- Previous bound revision: none
- Current bound revision: {bound['revision']}
- Summary: none
- Next-task overrides: none

## Task Queue

### task-1

- Status: pending
- Source revision: r0001
- Source prompt sections: Ask, Outcome, Acceptance criteria, Verification
- Depends on: none
- Goal:
- Plan:
- Likely files:
- Implementation steps:
- Validation:
- End-to-end validation:
- Code-review:
- Review fixes:
- Commit:
- Done criteria:
- Rollback notes:
- Stop conditions:
- Changed files:
- Evidence:
- Blocker:

### task-2

- Status: pending
- Source revision: r0001
- Source prompt sections: Ask, Outcome, Acceptance criteria, Verification
- Depends on: task-1
- Goal:
- Plan:
- Likely files:
- Implementation steps:
- Validation:
- End-to-end validation:
- Code-review:
- Review fixes:
- Commit:
- Done criteria:
- Rollback notes:
- Stop conditions:
- Changed files:
- Evidence:
- Blocker:

### task-3

- Status: pending
- Source revision: r0001
- Source prompt sections: Ask, Outcome, Acceptance criteria, Verification
- Depends on: task-2
- Goal:
- Plan:
- Likely files:
- Implementation steps:
- Validation:
- End-to-end validation:
- Code-review:
- Review fixes:
- Commit:
- Done criteria:
- Rollback notes:
- Stop conditions:
- Changed files:
- Evidence:
- Blocker:

## Checkpoints

### checkpoint-1

- Completed task:
- Bound revision:
- Summary:
- Plan followed:
- Files changed:
- Validation:
- End-to-end validation:
- Code-review:
- Commit hash:
- Commit message:
- Next task:

### checkpoint-2

- Completed task:
- Bound revision:
- Summary:
- Plan followed:
- Files changed:
- Validation:
- End-to-end validation:
- Code-review:
- Commit hash:
- Commit message:
- Next task:

### checkpoint-3

- Completed task:
- Bound revision:
- Summary:
- Plan followed:
- Files changed:
- Validation:
- End-to-end validation:
- Code-review:
- Commit hash:
- Commit message:
- Next task:

## Session Handoff

- Current session action: stop after saving this handoff
- Next session mechanism: new Codex session
- Next task: none
- Do not continue in current session: yes

## Next Session Prompt

```text
Use $task-implementer run <same-prompt-path-or-unique-filename>.
```
"""
        (self.run_dir / "handoff.md").write_text(handoff, encoding="utf-8")
        (self.run_dir / "handoff.md").chmod(0o600)

    def update_task_fields(self, task_id: str, fields: dict[str, str]) -> None:
        path = self.run_dir / "handoff.md"
        text = path.read_text(encoding="utf-8")
        match = re.search(
            rf"(?ms)^### {re.escape(task_id)}\s*\n(.*?)(?=^### task-|^## |\Z)",
            text,
        )
        if match is None:
            raise AssertionError(task_id)
        section = match.group(1)
        for label, value in fields.items():
            section, count = re.subn(
                rf"(?m)^- {re.escape(label)}:.*$",
                f"- {label}: {value}",
                section,
                count=1,
            )
            self.assertEqual(count, 1, label)
        text = text[: match.start(1)] + section + text[match.end(1) :]
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)

    def insert_task_fields(self, task_id: str, fields: dict[str, str]) -> None:
        path = self.run_dir / "handoff.md"
        text = path.read_text(encoding="utf-8")
        match = re.search(
            rf"(?ms)^### {re.escape(task_id)}\s*\n(.*?)(?=^### task-|^## |\Z)",
            text,
        )
        if match is None:
            raise AssertionError(task_id)
        section = match.group(1)
        insertion = "".join(f"- {label}: {value}\n" for label, value in fields.items())
        section, count = re.subn(
            r"(?m)^- Depends on:", insertion + "- Depends on:", section, count=1
        )
        self.assertEqual(count, 1)
        text = text[: match.start(1)] + section + text[match.end(1) :]
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)

    def insert_checkpoint_fields(
        self, checkpoint_id: str, fields: dict[str, str]
    ) -> None:
        path = self.run_dir / "handoff.md"
        text = path.read_text(encoding="utf-8")
        match = re.search(
            rf"(?ms)^### {re.escape(checkpoint_id)}\s*\n"
            r"(.*?)(?=^### checkpoint-|^## |\Z)",
            text,
        )
        if match is None:
            raise AssertionError(checkpoint_id)
        section = match.group(1)
        insertion = "".join(f"- {label}: {value}\n" for label, value in fields.items())
        section, count = re.subn(
            r"(?m)^- Next task:", insertion + "- Next task:", section, count=1
        )
        self.assertEqual(count, 1)
        text = text[: match.start(1)] + section + text[match.end(1) :]
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)

    def populate_plan(self, task_id: str) -> None:
        self.update_task_fields(
            task_id,
            {
                "Goal": f"Implement {task_id} safely",
                "Plan": f"Complete the scoped {task_id} behavior",
                "Likely files": "tracked.txt",
                "Implementation steps": "Edit, test, review, and checkpoint",
                "Validation": "Run the focused unit tests",
                "End-to-end validation": "Exercise the complete task behavior",
                "Done criteria": "The task behavior and tests pass",
                "Rollback notes": "Revert the task commit",
                "Stop conditions": "Stop on ambiguous scope or failed validation",
            },
        )

    def commit_task(self, message: str = "Implement task") -> str:
        if not git("status", "--porcelain", cwd=self.repo):
            current = (self.repo / "tracked.txt").read_text(encoding="utf-8")
            (self.repo / "tracked.txt").write_text(
                current + message + "\n",
                encoding="utf-8",
            )
        git("add", "-A", cwd=self.repo)
        git(
            "-c",
            "user.name=Execution Test",
            "-c",
            "user.email=execution@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            message,
            cwd=self.repo,
        )
        return git("rev-parse", "HEAD", cwd=self.repo)

    def plane_path(self, task_id: str = "task-1") -> Path:
        return self.run_dir / "execution" / f"{task_id}.json"

    def recovery_digest(self) -> str:
        execution_module = sys.modules[pw.claim_execution_plane.__module__]
        workspace = pw.verify_workspace(self.workspace)
        return str(execution_module.worktree_state(workspace)[0])

    def complete_queued_task(
        self,
        *,
        task_id: str,
        session_id: str,
        checkpoint_id: str,
        next_task: str,
        clock: datetime,
    ) -> str:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id=session_id,
            clock=lambda: clock,
        )
        self.populate_plan(task_id)
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id=session_id,
            clock=lambda: clock,
        )
        commit_hash = self.commit_task(f"Implement {task_id}")
        self.populate_checkpoint(
            commit_hash=commit_hash,
            task_id=task_id,
            checkpoint_id=checkpoint_id,
            next_task=next_task,
        )
        pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id=session_id,
            clock=lambda: clock,
        )
        return commit_hash

    def populate_checkpoint(
        self,
        *,
        commit_hash: str,
        task_id: str = "task-1",
        checkpoint_id: str = "checkpoint-1",
        next_task: str = "task-2",
    ) -> None:
        path = self.run_dir / "handoff.md"
        text = path.read_text(encoding="utf-8")
        match = re.search(
            rf"(?ms)^### {re.escape(checkpoint_id)}\s*\n"
            r"(.*?)(?=^### checkpoint-|^## |\Z)",
            text,
        )
        if match is None:
            raise AssertionError(checkpoint_id)
        section = match.group(1)
        values = {
            "Completed task": task_id,
            "Bound revision": "r0001",
            "Summary": f"Implemented {task_id}",
            "Plan followed": "yes",
            "Files changed": "tracked.txt",
            "Validation": "focused tests passed",
            "End-to-end validation": "task behavior passed",
            "Code-review": "no blocking findings",
            "Commit hash": commit_hash,
            "Commit message": f"Implement {task_id}",
            "Next task": next_task,
        }
        for label, value in values.items():
            section, count = re.subn(
                rf"(?m)^- {re.escape(label)}:.*$",
                f"- {label}: {value}",
                section,
                count=1,
            )
            self.assertEqual(count, 1, label)
        text = text[: match.start(1)] + section + text[match.end(1) :]
        session_match = re.search(
            r"(?ms)^## Session Handoff\s*\n(.*?)(?=^## |\Z)", text
        )
        if session_match is None:
            raise AssertionError("Session Handoff")
        session = session_match.group(1)
        session, count = re.subn(
            r"(?m)^- Next task:.*$",
            f"- Next task: {next_task}",
            session,
            count=1,
        )
        self.assertEqual(count, 1)
        text = (
            text[: session_match.start(1)]
            + session
            + text[session_match.end(1) :]
        )
        text = text.replace(
            "$task-implementer run <same-prompt-path-or-unique-filename>",
            f"$task-implementer run {self.prompt.name}",
        )
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)

    def assert_error(self, code: str, function: object, **kwargs: object) -> None:
        with self.assertRaises(pw.PromptWorkspaceError) as context:
            function(self.workspace, self.run_id, **kwargs)
        self.assertEqual(context.exception.code, code)

    def test_active_implementation_edit_is_queued_without_rebinding(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        plane_before = self.plane_path().read_bytes()
        handoff_before = (self.run_dir / "handoff.md").read_text(encoding="utf-8")
        self.edit_prompt("Both tasks and new steering are implemented.")
        edited_mtime = self.prompt.stat().st_mtime_ns

        routed = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            self.prompt.name,
            clock=lambda: FIXED.replace(second=1),
        )

        self.assertEqual(routed["action"], "steering_queued")
        self.assertEqual(routed["outcome"], "STEERING_QUEUED_AFTER_TASK")
        self.assertEqual(routed["status"], "steering_pending")
        self.assertEqual(routed["_internal"]["revision"], "r0002")
        self.assertEqual(self.plane_path().read_bytes(), plane_before)
        handoff_after = (self.run_dir / "handoff.md").read_text(encoding="utf-8")
        for field in ("Bound revision", "Bound SHA-256", "Current task"):
            pattern = rf"(?m)^- {re.escape(field)}:.*$"
            self.assertEqual(
                re.findall(pattern, handoff_after),
                re.findall(pattern, handoff_before),
            )
        self.assertEqual(self.prompt.stat().st_mtime_ns, edited_mtime)
        repeated = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            self.prompt.name,
            clock=lambda: FIXED.replace(second=2),
        )
        self.assertEqual(repeated["action"], "steering_queued")
        manifest = json.loads(
            (self.run_dir / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(manifest["revisions"]), 2)

    def test_queued_steering_applies_after_checkpoint_before_fresh_claim(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        self.edit_prompt("Both tasks and boundary steering are implemented.")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session-b"}):
            queued = pw.route_project_prompt(
                self.scope,
                self.codex_home,
                self.prompt.name,
                clock=lambda: FIXED.replace(second=2),
            )
        self.assertEqual(queued["outcome"], "STEERING_QUEUED_AFTER_TASK")
        self.assertEqual(
            json.loads(self.plane_path().read_text(encoding="utf-8"))[
                "bound_revision"
            ],
            "r0001",
        )

        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        stopped = pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=3),
        )
        self.assertEqual(stopped["phase"], "stopped")

        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session-b"}):
            reconcile = pw.route_project_prompt(
                self.scope,
                self.codex_home,
                self.prompt.name,
                clock=lambda: FIXED.replace(second=4),
            )
        self.assertEqual(reconcile["action"], "reconcile")
        internal = dict(reconcile["_internal"])
        execution_module = sys.modules[pw.claim_execution_plane.__module__]
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8")
        text = execution_module.replace_section_field(
            text, "Run", "Bound revision", "r0002"
        )
        text = execution_module.replace_section_field(
            text, "Run", "Bound SHA-256", str(internal["sha256"])
        )
        text = execution_module.replace_section_field(
            text, "Run", "Bound snapshot path", str(internal["snapshot"])
        )
        text = execution_module.replace_section_field(
            text, "Reconciliation", "State", "applied"
        )
        text = execution_module.replace_section_field(
            text, "Reconciliation", "Previous bound revision", "r0001"
        )
        text = execution_module.replace_section_field(
            text, "Reconciliation", "Current bound revision", "r0002"
        )
        text = execution_module.replace_section_field(
            text,
            "Reconciliation",
            "Summary",
            "queued steering applied at the task boundary",
        )
        text = execution_module.replace_task_field_block(
            text, "task-2", "Source revision", "r0002"
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        self.resolve_steering("r0002")

        claimed = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            clock=lambda: FIXED.replace(second=5),
        )
        self.assertEqual(claimed["task"], "task-2")
        self.assertEqual(claimed["phase"], "planning")

    def test_same_owner_clean_planning_edit_rebinds_existing_plane(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.edit_prompt("Both tasks and planning steering are implemented.")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session-a"}):
            routed = pw.route_project_prompt(
                self.scope,
                self.codex_home,
                self.prompt.name,
                clock=lambda: FIXED.replace(second=1),
            )
        self.assertEqual(routed["action"], "reconcile_planning")
        self.assertEqual(
            json.loads(self.plane_path().read_text(encoding="utf-8"))[
                "bound_revision"
            ],
            "r0001",
        )

        rebound = pw.rebind_planning_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
        )
        self.assertEqual(rebound["bound_revision"], "r0002")
        plane = json.loads(self.plane_path().read_text(encoding="utf-8"))
        self.assertEqual(plane["bound_revision"], "r0002")
        self.assertEqual(plane["phase"], "planning")
        handoff = (self.run_dir / "handoff.md").read_text(encoding="utf-8")
        self.assertIn("- Bound revision: r0002", handoff)
        task = re.search(
            r"(?ms)^### task-1\s*\n(.*?)(?=^### task-|^## |\Z)", handoff
        )
        self.assertIsNotNone(task)
        execution_module = sys.modules[pw.rebind_planning_execution_plane.__module__]
        self.assertEqual(execution_module.field_block(task.group(1), "Plan"), "")
        self.resolve_steering("r0002")
        self.populate_plan("task-1")
        authorized = pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=2),
        )
        self.assertEqual(authorized["phase"], "implementation")

    def test_spec_planning_rebind_clears_the_complete_contract(self) -> None:
        self.insert_task_fields(
            "task-1",
            {
                "Requirement IDs": "TI-REQ-001",
                "Design ID": "TI-DES-001",
                "Requirements proposal": "TI-REQ-001 active stale proposal",
                "Design record": "TI-DES-001 maps TI-REQ-001",
                "Requirements envelope SHA-256": "1" * 64,
                "Design envelope SHA-256": "2" * 64,
            },
        )
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.edit_prompt("Both tasks and spec-aware planning steering are implemented.")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session-a"}):
            routed = pw.route_project_prompt(
                self.scope,
                self.codex_home,
                self.prompt.name,
                clock=lambda: FIXED.replace(second=1),
            )
        self.assertEqual(routed["action"], "reconcile_planning")
        pw.rebind_planning_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
        )
        handoff = (self.run_dir / "handoff.md").read_text(encoding="utf-8")
        task = re.search(
            r"(?ms)^### task-1\s*\n(.*?)(?=^### task-|^## |\Z)", handoff
        )
        self.assertIsNotNone(task)
        execution_module = sys.modules[pw.rebind_planning_execution_plane.__module__]
        for label in execution_module.PLAN_FIELDS + execution_module.SPEC_PLAN_FIELDS:
            self.assertEqual(execution_module.field_block(task.group(1), label), "")
        self.resolve_steering("r0002")
        self.populate_plan("task-1")
        self.assert_error(
            "PLAN_REQUIRED",
            pw.authorize_execution_plane,
            session_id="session-a",
        )

    def test_planning_rebind_recovers_a_handoff_only_partial_write(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.edit_prompt("Both tasks and retry-safe planning steering are implemented.")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session-a"}):
            pw.route_project_prompt(
                self.scope,
                self.codex_home,
                self.prompt.name,
                clock=lambda: FIXED.replace(second=1),
            )
        execution_module = sys.modules[pw.rebind_planning_execution_plane.__module__]
        with mock.patch.object(
            execution_module,
            "write_plane",
            side_effect=OSError("injected plane write failure"),
        ):
            with self.assertRaises(OSError):
                pw.rebind_planning_execution_plane(
                    self.workspace,
                    self.run_id,
                    session_id="session-a",
                )
        self.assertEqual(
            json.loads(self.plane_path().read_text(encoding="utf-8"))[
                "bound_revision"
            ],
            "r0001",
        )
        self.assertIn(
            "- Bound revision: r0002",
            (self.run_dir / "handoff.md").read_text(encoding="utf-8"),
        )
        repaired = pw.rebind_planning_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
        )
        self.assertEqual(repaired["bound_revision"], "r0002")

    def test_planning_rebind_retries_after_prewrite_failure(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.edit_prompt("Both tasks and prewrite retry steering are implemented.")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session-a"}):
            pw.route_project_prompt(
                self.scope,
                self.codex_home,
                self.prompt.name,
                clock=lambda: FIXED.replace(second=1),
            )
        execution_module = sys.modules[pw.rebind_planning_execution_plane.__module__]
        with mock.patch.object(
            execution_module,
            "write_atomic",
            side_effect=OSError("injected handoff write failure"),
        ):
            with self.assertRaises(OSError):
                pw.rebind_planning_execution_plane(
                    self.workspace,
                    self.run_id,
                    session_id="session-a",
                )
        self.assertNotIn(
            "- Bound revision: r0002",
            (self.run_dir / "handoff.md").read_text(encoding="utf-8"),
        )
        repaired = pw.rebind_planning_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
        )
        self.assertEqual(repaired["bound_revision"], "r0002")

    def test_foreign_owner_planning_edit_is_queued(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        plane_before = self.plane_path().read_bytes()
        self.edit_prompt("Both tasks and queued planning steering are implemented.")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session-b"}):
            routed = pw.route_project_prompt(
                self.scope,
                self.codex_home,
                self.prompt.name,
                clock=lambda: FIXED.replace(second=1),
            )
        self.assertEqual(routed["action"], "steering_queued")
        self.assertEqual(routed["outcome"], "STEERING_QUEUED_AFTER_TASK")
        self.assertEqual(self.plane_path().read_bytes(), plane_before)

    def test_plan_gate_checkpoint_and_fresh_session(self) -> None:
        claimed = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.assertEqual(claimed["task"], "task-1")
        self.assertEqual(claimed["phase"], "planning")
        self.assertNotIn("plane", claimed)
        self.assert_error(
            "PLAN_REQUIRED",
            pw.authorize_execution_plane,
            session_id="session-a",
        )

        self.populate_plan("task-1")
        authorized = pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        self.assertEqual(authorized["phase"], "implementation")
        (self.repo / "tracked.txt").write_text("task-1\n", encoding="utf-8")
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        checkpoint = pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=2),
        )
        self.assertEqual(checkpoint["task"], "task-1")
        self.assertEqual(checkpoint["phase"], "stopped")
        self.assertTrue(checkpoint["next_session_required"])
        self.assert_error(
            "FRESH_SESSION_REQUIRED",
            pw.claim_execution_plane,
            session_id="session-a",
        )

        next_claim = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            clock=lambda: FIXED.replace(second=3),
        )
        self.assertEqual(next_claim["task"], "task-2")
        self.assertEqual(next_claim["phase"], "planning")

    def test_spec_aware_plan_and_checkpoint_lock_managed_documents(self) -> None:
        specs_module = sys.modules[pw.inspect_spec_documents.__module__]
        before = pw.inspect_spec_documents(pw.verify_workspace(self.workspace))
        self.insert_task_fields(
            "task-1",
            {
                "Requirement IDs": "TI-REQ-001",
                "Design ID": "TI-DES-001",
                "Requirements proposal": "TI-REQ-001 active safe steering",
                "Design record": "TI-DES-001 maps TI-REQ-001",
                "Requirements envelope SHA-256": str(
                    before["requirements"]["rendered_surrounding_sha256"]
                ),
                "Design envelope SHA-256": str(
                    before["design"]["rendered_surrounding_sha256"]
                ),
            },
        )
        self.insert_task_fields(
            "task-2",
            {
                "Requirement IDs": "TI-REQ-002",
                "Requirements proposal": "TI-REQ-002 active second task",
            },
        )
        self.insert_task_fields(
            "task-3",
            {
                "Requirement IDs": "TI-REQ-003",
                "Requirements proposal": "TI-REQ-003 active third task",
            },
        )
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        self.update_task_fields(
            "task-1",
            {
                "Likely files": "\n".join(
                    (
                        "tracked.txt",
                        "services/example/docs/requirements.md",
                        "services/example/docs/design.md",
                    )
                )
            },
        )
        for task_id in ("task-1", "task-2", "task-3"):
            self.update_task_fields(
                task_id,
                {
                    "Requirement IDs": "TI-REQ-999",
                    "Requirements proposal": "TI-REQ-999 active skipped allocation",
                },
            )
        self.update_task_fields(
            "task-1",
            {"Design record": "TI-DES-001 maps TI-REQ-999"},
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.authorize_execution_plane,
            session_id="session-a",
        )
        for number, task_id in enumerate(("task-1", "task-2", "task-3"), 1):
            self.update_task_fields(
                task_id,
                {
                    "Requirement IDs": f"TI-REQ-{number:03d}",
                    "Requirements proposal": (
                        f"TI-REQ-{number:03d} active task {number}"
                    ),
                },
            )
        self.update_task_fields(
            "task-1",
            {"Design record": "TI-DES-001 maps TI-REQ-001"},
        )
        self.update_task_fields(
            "task-1",
            {
                "Design ID": "TI-DES-002",
                "Design record": "TI-DES-002 maps TI-REQ-001",
            },
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.authorize_execution_plane,
            session_id="session-a",
        )
        self.update_task_fields(
            "task-1",
            {
                "Design ID": "TI-DES-001",
                "Design record": "TI-DES-001 maps TI-REQ-001",
            },
        )
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        docs = self.scope / "docs"
        docs.mkdir()
        requirements = docs / "requirements.md"
        design = docs / "design.md"
        requirements.write_bytes(
            specs_module.new_spec_document(
                "requirements",
                """## Task Implementer Requirements

### TI-REQ-001: Safe steering

- Status: active
- Requirement: Apply steering safely.
- Constraints: Preserve completed work.
- Non-goals: Live interruption.

#### Acceptance criteria

- Steering is applied once at a safe boundary.

#### Verification

- Run focused steering tests.

### TI-REQ-002: Second task

- Status: active
- Requirement: Complete task two.
- Constraints: Preserve task one.
- Non-goals: Unrelated cleanup.

#### Acceptance criteria

- Task two completes.

#### Verification

- Run task-two tests.

### TI-REQ-003: Third task

- Status: active
- Requirement: Complete task three.
- Constraints: Preserve prior tasks.
- Non-goals: Unrelated cleanup.

#### Acceptance criteria

- Task three completes.

#### Verification

- Run task-three tests.

## Task Implementer Open Questions

- None.

## Task Implementer Requirements Change Log

- 2026-07-13: Added three requirements.
""",
            )
        )
        design.write_bytes(
            specs_module.new_spec_document(
                "design",
                """## Task Implementer Designs

### TI-DES-001: Safe steering

- Status: implemented
- Requirements: TI-REQ-001
- Selected approach: Queue during implementation.
- Boundaries and interfaces: Private intake and task boundaries.
- Validation: Run focused steering tests.
- Rollback: Revert the task commit.

#### Alternatives considered

- Live interruption was rejected.

#### Implementation evidence

- Checkpoint commit records the implementation.

## Task Implementer Design Change Log

- 2026-07-13: Added TI-DES-001.
""",
            )
        )
        requirements.chmod(0o644)
        design.chmod(0o644)
        commit_hash = self.commit_task("Implement task-1")
        inspected = pw.inspect_spec_documents(
            pw.verify_workspace(self.workspace), commit=commit_hash
        )
        self.insert_checkpoint_fields(
            "checkpoint-1",
            {
                "Requirements SHA-256": str(
                    inspected["requirements"]["managed_sha256"]
                ),
                "Design SHA-256": str(inspected["design"]["managed_sha256"]),
                "Spec validation": "managed IDs and envelopes verified",
            },
        )
        self.populate_checkpoint(commit_hash=commit_hash)
        self.update_task_fields(
            "task-1",
            {
                "Changed files": "requirements.md, design.md",
            },
        )
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8")
        text = text.replace(
            "- Files changed: tracked.txt",
            "- Files changed: services/example/docs/requirements.md\n"
            "  services/example/docs/design.md",
            1,
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)

        checkpoint = pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        self.assertEqual(checkpoint["phase"], "stopped")

        drifted_design = design.read_text(encoding="utf-8").replace(
            "Queue during implementation.",
            "Manually changed outside the managed workflow.",
        )
        drifted_design = drifted_design.replace(
            "## Task Implementer Design Change Log",
            """### TI-DES-002: Second task design

- Status: implemented
- Requirements: TI-REQ-002
- Selected approach: Implement the second task independently.
- Boundaries and interfaces: The second task only.
- Validation: Run focused task-two tests.
- Rollback: Revert the task-two commit.

#### Alternatives considered

- Reusing the first design was rejected.

#### Implementation evidence

- A task-two checkpoint would record the implementation.

## Task Implementer Design Change Log""",
        )
        design.write_text(drifted_design, encoding="utf-8")
        design.chmod(0o644)
        drift_commit = self.commit_task("Change managed design outside the workflow")
        drifted = pw.inspect_spec_documents(
            pw.verify_workspace(self.workspace), commit=drift_commit
        )
        execution_module = sys.modules[pw.claim_execution_plane.__module__]
        with self.assertRaises(pw.PromptWorkspaceError) as context:
            execution_module.validate_spec_checkpoint(
                pw.verify_workspace(self.workspace),
                {"claim_head": commit_hash},
                {
                    "Requirement IDs": "TI-REQ-002",
                    "Design ID": "TI-DES-002",
                },
                {
                    "Commit hash": drift_commit,
                    "Requirements SHA-256": str(
                        drifted["requirements"]["managed_sha256"]
                    ),
                    "Design SHA-256": str(drifted["design"]["managed_sha256"]),
                    "Files changed": "services/example/docs/design.md",
                },
            )
        self.assertEqual(context.exception.code, "SPEC_CONFLICT")
        self.assert_error(
            "SPEC_CONFLICT",
            pw.claim_execution_plane,
            session_id="session-b",
        )

    def test_execution_claim_is_exclusive_and_recoverable(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        plane = self.plane_path()
        handoff_before = (self.run_dir / "handoff.md").read_bytes()
        plane_before = plane.read_bytes()
        self.assertNotIn(b"session-a", plane_before)
        self.assertIn(b"owner_session_sha256", plane_before)
        self.assert_error(
            "WORKSPACE_BUSY",
            pw.claim_execution_plane,
            session_id="session-b",
        )
        self.assertEqual(plane.read_bytes(), plane_before)
        self.assertEqual((self.run_dir / "handoff.md").read_bytes(), handoff_before)

        self.assert_error(
            "HUMAN_INPUT_REQUIRED",
            pw.claim_execution_plane,
            session_id="session-b",
            recover=True,
        )
        self.assertEqual(plane.read_bytes(), plane_before)

        recovered = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            recover=True,
            confirmed_recovery_worktree_sha256=self.recovery_digest(),
            clock=lambda: FIXED.replace(second=1),
        )
        self.assertTrue(recovered["recovered"])
        self.assertEqual(recovered["task"], "task-1")
        self.assert_error(
            "WORKSPACE_BUSY",
            pw.authorize_execution_plane,
            session_id="session-a",
        )

    def test_implementation_recovery_preserves_the_locked_plan(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        authorized = pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )

        recovered = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            recover=True,
            confirmed_recovery_worktree_sha256=self.recovery_digest(),
            clock=lambda: FIXED.replace(second=2),
        )
        self.assertEqual(recovered["phase"], "implementation")
        plane = json.loads(self.plane_path().read_text(encoding="utf-8"))
        self.assertEqual(plane["plan_sha256"], authorized["plan_sha256"])
        self.assertEqual(
            plane["recovery_confirmation"],
            "prior-session-stopped-and-worktree-reviewed",
        )
        self.assertEqual(plane["recovery_count"], 1)

    def test_same_task_can_recover_a_to_b_to_a_without_corrupting_history(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            recover=True,
            confirmed_recovery_worktree_sha256=self.recovery_digest(),
            clock=lambda: FIXED.replace(second=1),
        )
        recovered = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            recover=True,
            confirmed_recovery_worktree_sha256=self.recovery_digest(),
            clock=lambda: FIXED.replace(second=2),
        )
        self.assertTrue(recovered["recovered"])
        plane = json.loads(self.plane_path().read_text(encoding="utf-8"))
        self.assertEqual(plane["recovery_count"], 2)
        self.assertEqual(len(plane["session_history_sha256"]), 2)
        self.assertEqual(
            plane["session_history_sha256"][-1],
            plane["owner_session_sha256"],
        )
        retried = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
        )
        self.assertEqual(retried["task"], "task-1")

    def test_every_session_participating_in_a_task_is_retired(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            recover=True,
            confirmed_recovery_worktree_sha256=self.recovery_digest(),
            clock=lambda: FIXED.replace(second=1),
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            clock=lambda: FIXED.replace(second=2),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            clock=lambda: FIXED.replace(second=3),
        )

        self.assert_error(
            "FRESH_SESSION_REQUIRED",
            pw.claim_execution_plane,
            session_id="session-a",
        )

    def test_committed_interrupted_task_is_recovered_without_a_new_commit(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")

        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            recover=True,
            confirmed_recovery_worktree_sha256=self.recovery_digest(),
            clock=lambda: FIXED.replace(second=2),
        )
        self.populate_checkpoint(commit_hash=commit_hash)
        checkpoint = pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            clock=lambda: FIXED.replace(second=3),
        )
        self.assertEqual(checkpoint["task"], "task-1")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), commit_hash)

        next_claim = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-c",
            clock=lambda: FIXED.replace(second=4),
        )
        self.assertEqual(next_claim["task"], "task-2")

    def test_multi_recovery_history_retires_every_participant(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        for offset, session_id in enumerate(("session-b", "session-c"), start=1):
            pw.claim_execution_plane(
                self.workspace,
                self.run_id,
                session_id=session_id,
                recover=True,
                confirmed_recovery_worktree_sha256=self.recovery_digest(),
                clock=lambda offset=offset: FIXED.replace(second=offset),
            )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-c",
            clock=lambda: FIXED.replace(second=3),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-c",
            clock=lambda: FIXED.replace(second=4),
        )

        for session_id in ("session-a", "session-b", "session-c"):
            self.assert_error(
                "FRESH_SESSION_REQUIRED",
                pw.claim_execution_plane,
                session_id=session_id,
            )
        next_claim = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-d",
            clock=lambda: FIXED.replace(second=5),
        )
        self.assertEqual(next_claim["task"], "task-2")

    def test_dirty_worktree_cannot_become_a_new_execution_baseline(self) -> None:
        handoff_before = (self.run_dir / "handoff.md").read_bytes()
        (self.repo / "tracked.txt").write_text("preexisting change\n", encoding="utf-8")
        (self.repo / "untracked.txt").write_text("preexisting file\n", encoding="utf-8")

        self.assert_error(
            "WORKTREE_CONFLICT",
            pw.claim_execution_plane,
            session_id="session-a",
        )
        self.assertFalse((self.run_dir / "execution").exists())
        self.assertEqual((self.run_dir / "handoff.md").read_bytes(), handoff_before)

    def test_recovery_rejects_dirty_paths_outside_the_locked_plan(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        (self.repo / "unexpected.txt").write_text("unreviewed\n", encoding="utf-8")

        self.assert_error(
            "WORKTREE_CONFLICT",
            pw.claim_execution_plane,
            session_id="session-b",
            recover=True,
            confirmed_recovery_worktree_sha256=self.recovery_digest(),
        )

    def test_superseded_task_does_not_satisfy_a_dependency(self) -> None:
        self.update_task_fields("task-1", {"Status": "superseded"})
        self.assert_error(
            "HUMAN_INPUT_REQUIRED",
            pw.claim_execution_plane,
            session_id="session-a",
        )

    def test_session_cannot_return_after_an_intervening_task(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        first_commit = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=first_commit)
        pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=2),
        )

        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            clock=lambda: FIXED.replace(second=3),
        )
        self.populate_plan("task-2")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            clock=lambda: FIXED.replace(second=4),
        )
        second_commit = self.commit_task("Implement task-2")
        self.populate_checkpoint(
            commit_hash=second_commit,
            task_id="task-2",
            checkpoint_id="checkpoint-2",
            next_task="task-3",
        )
        pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            clock=lambda: FIXED.replace(second=5),
        )

        self.assert_error(
            "FRESH_SESSION_REQUIRED",
            pw.claim_execution_plane,
            session_id="session-a",
        )

    def test_same_second_checkpoints_select_run_last_completed_task(self) -> None:
        same_clock = FIXED.replace(second=7)
        self.complete_queued_task(
            task_id="task-1",
            session_id="session-a",
            checkpoint_id="checkpoint-1",
            next_task="task-2",
            clock=same_clock,
        )
        self.complete_queued_task(
            task_id="task-2",
            session_id="session-b",
            checkpoint_id="checkpoint-2",
            next_task="task-3",
            clock=same_clock,
        )

        claimed = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-c",
            clock=lambda: same_clock,
        )
        self.assertEqual(claimed["task"], "task-3")

    def test_next_claim_revalidates_every_completed_task(self) -> None:
        self.complete_queued_task(
            task_id="task-1",
            session_id="session-a",
            checkpoint_id="checkpoint-1",
            next_task="task-2",
            clock=FIXED.replace(second=1),
        )
        self.complete_queued_task(
            task_id="task-2",
            session_id="session-b",
            checkpoint_id="checkpoint-2",
            next_task="task-3",
            clock=FIXED.replace(second=2),
        )
        self.update_task_fields("task-1", {"Plan": "Tampered completed plan"})

        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.claim_execution_plane,
            session_id="session-c",
        )

    def test_active_checkpoint_revalidates_every_prior_completed_task(self) -> None:
        self.complete_queued_task(
            task_id="task-1",
            session_id="session-a",
            checkpoint_id="checkpoint-1",
            next_task="task-2",
            clock=FIXED.replace(second=1),
        )
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            clock=lambda: FIXED.replace(second=2),
        )
        self.populate_plan("task-2")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            clock=lambda: FIXED.replace(second=3),
        )
        self.update_task_fields("task-1", {"Plan": "Tampered completed plan"})
        commit_hash = self.commit_task("Implement task-2")
        self.populate_checkpoint(
            commit_hash=commit_hash,
            task_id="task-2",
            checkpoint_id="checkpoint-2",
            next_task="task-3",
        )

        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.checkpoint_execution_plane,
            session_id="session-b",
        )

    def test_session_fingerprint_is_retired_across_scope_runs(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.update_task_fields("task-2", {"Status": "superseded"})
        self.update_task_fields("task-3", {"Status": "superseded"})
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash, next_task="none")
        pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=2),
        )

        prompt_result = pw.create_prompt(
            self.workspace,
            "Implement another prompt",
            clock=lambda: FIXED.replace(minute=1),
            id_factory=lambda: "b" * 32,
        )
        self.prompt = Path(prompt_result["path"])
        self.complete_prompt()
        snapshot = pw.snapshot_prompt(
            self.workspace,
            self.prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED.replace(minute=1),
        )
        self.run_id = str(snapshot["run_id"])
        self.run_dir = Path(snapshot["manifest"]).parent
        self.write_handoff()

        self.assert_error(
            "FRESH_SESSION_REQUIRED",
            pw.claim_execution_plane,
            session_id="session-a",
        )

    def test_worktree_must_not_change_before_plan_authorization(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        (self.repo / "tracked.txt").write_text("too early\n", encoding="utf-8")
        self.assert_error(
            "WORKTREE_CONFLICT",
            pw.authorize_execution_plane,
            session_id="session-a",
        )

    def test_git_head_must_not_advance_during_planning(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        (self.repo / "tracked.txt").write_text("concurrent commit\n", encoding="utf-8")
        self.commit_task("Concurrent planning change")
        self.assert_error(
            "WORKTREE_CONFLICT",
            pw.authorize_execution_plane,
            session_id="session-a",
        )

    def test_locked_plan_cannot_change_before_checkpoint(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        self.update_task_fields("task-1", {"Plan": "A changed implementation plan"})
        self.populate_checkpoint(commit_hash=git("rev-parse", "HEAD", cwd=self.repo))
        self.assert_error(
            "PLAN_LOCKED",
            pw.checkpoint_execution_plane,
            session_id="session-a",
        )

    def test_plane_revision_and_handoff_binding_must_match(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        plane_path = self.plane_path()
        plane = json.loads(plane_path.read_text(encoding="utf-8"))
        plane["bound_revision"] = "r9999"
        plane_path.write_text(
            json.dumps(plane, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        plane_path.chmod(0o600)
        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.authorize_execution_plane,
            session_id="session-a",
        )

    def test_duplicate_plan_field_is_rejected(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "- Plan: Complete the scoped task-1 behavior",
            "- Plan: Complete the scoped task-1 behavior\n- Plan: Duplicate plan",
            1,
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.authorize_execution_plane,
            session_id="session-a",
        )

    def test_authorized_queue_contract_cannot_change(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        self.update_task_fields("task-2", {"Depends on": "none"})
        self.assert_error(
            "PLAN_LOCKED",
            pw.checkpoint_execution_plane,
            session_id="session-a",
        )

    def test_post_implementation_task_evidence_does_not_change_locked_queue(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        self.update_task_fields(
            "task-1",
            {
                "Code-review": "no blocking findings",
                "Review fixes": "none required",
                "Commit": "local checkpoint created",
                "Changed files": "tracked.txt",
                "Evidence": "focused execution test passed",
                "Blocker": "none",
            },
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        result = pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=2),
        )
        self.assertEqual(result["phase"], "stopped")

    def test_checkpoint_is_idempotent_for_one_task(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        first = pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=2),
        )
        handoff = (self.run_dir / "handoff.md").read_bytes()
        second = pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=3),
        )
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual((self.run_dir / "handoff.md").read_bytes(), handoff)

    def test_checkpoint_retry_revalidates_stopped_handoff(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=2),
        )
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            f"- Last commit: {commit_hash}",
            "- Last commit: " + "0" * 40,
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)

        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.checkpoint_execution_plane,
            session_id="session-a",
        )

    def test_next_session_revalidates_stopped_predecessor(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=2),
        )
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            f"- Last commit: {commit_hash}",
            "- Last commit: " + "0" * 40,
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)

        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.claim_execution_plane,
            session_id="session-b",
        )

    def test_reconciled_pending_queue_can_advance_from_historical_checkpoint(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=2),
        )

        self.prompt.write_text(
            self.prompt.read_text(encoding="utf-8").replace(
                "Both tasks are implemented.",
                "All reconciled tasks are implemented.",
            ),
            encoding="utf-8",
        )
        self.prompt.chmod(0o600)
        revision = pw.snapshot_prompt(
            self.workspace,
            self.prompt,
            run_id=self.run_id,
            force_new_run=False,
            clock=lambda: FIXED.replace(second=3),
        )
        self.assertEqual(revision["revision"], "r0002")

        execution_module = sys.modules[pw.claim_execution_plane.__module__]
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8")
        text = execution_module.replace_section_field(
            text, "Run", "Bound revision", "r0002"
        )
        text = execution_module.replace_section_field(
            text, "Run", "Bound SHA-256", str(revision["sha256"])
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        self.update_task_fields(
            "task-2",
            {"Goal": "Implement the reconciled second task"},
        )

        claimed = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            clock=lambda: FIXED.replace(second=4),
        )
        self.assertEqual(claimed["task"], "task-2")

    def test_changed_next_task_requires_revision_bound_override(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=2),
        )
        self.edit_prompt("All reprioritized tasks are implemented.")
        revision = pw.snapshot_prompt(
            self.workspace,
            self.prompt,
            run_id=self.run_id,
            force_new_run=False,
            clock=lambda: FIXED.replace(second=3),
        )
        execution_module = sys.modules[pw.claim_execution_plane.__module__]
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8")
        text = execution_module.replace_section_field(
            text, "Run", "Bound revision", "r0002"
        )
        text = execution_module.replace_section_field(
            text, "Run", "Bound SHA-256", str(revision["sha256"])
        )
        text = execution_module.replace_section_field(
            text, "Session Handoff", "Next task", "task-3"
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        self.update_task_fields("task-2", {"Status": "superseded"})
        self.update_task_fields("task-3", {"Depends on": "task-1"})

        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.claim_execution_plane,
            session_id="session-b",
        )
        text = handoff.read_text(encoding="utf-8")
        override = (
            f"task-1 | task-2 -> task-3 | r0002 | {revision['sha256']}"
        )
        text = execution_module.replace_section_field(
            text, "Reconciliation", "Next-task overrides", f"\n- {override}"
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        claimed = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-b",
            clock=lambda: FIXED.replace(second=4),
        )
        self.assertEqual(claimed["task"], "task-3")

    def test_checkpoint_retry_converges_after_handoff_only_write(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        execution_module = sys.modules[pw.checkpoint_execution_plane.__module__]
        with mock.patch.object(
            execution_module,
            "write_plane",
            side_effect=OSError("simulated checkpoint interruption"),
        ):
            with self.assertRaises(OSError):
                pw.checkpoint_execution_plane(
                    self.workspace,
                    self.run_id,
                    session_id="session-a",
                    clock=lambda: FIXED.replace(second=2),
                )

        recovered = pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=3),
        )
        self.assertEqual(recovered["phase"], "stopped")
        self.assertFalse(recovered["idempotent"])

    def test_claim_retry_repairs_a_plane_only_write(self) -> None:
        execution_module = sys.modules[pw.claim_execution_plane.__module__]
        with mock.patch.object(
            execution_module,
            "write_atomic",
            side_effect=OSError("simulated claim interruption"),
        ):
            with self.assertRaises(OSError):
                pw.claim_execution_plane(
                    self.workspace,
                    self.run_id,
                    session_id="session-a",
                    clock=lambda: FIXED,
                )
        self.assertTrue(self.plane_path().is_file())

        repaired = pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        self.assertEqual(repaired["task"], "task-1")
        handoff = (self.run_dir / "handoff.md").read_text(encoding="utf-8")
        self.assertIn("- Current task: task-1", handoff)
        self.assertIn("- Status: in_progress", handoff)

    def test_authorize_retry_repairs_a_handoff_only_write(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        execution_module = sys.modules[pw.authorize_execution_plane.__module__]
        with mock.patch.object(
            execution_module,
            "write_plane",
            side_effect=OSError("simulated authorization interruption"),
        ):
            with self.assertRaises(OSError):
                pw.authorize_execution_plane(
                    self.workspace,
                    self.run_id,
                    session_id="session-a",
                    clock=lambda: FIXED.replace(second=1),
                )

        repaired = pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=2),
        )
        self.assertEqual(repaired["phase"], "implementation")

    def test_intake_resumes_when_handoff_precedes_plane_checkpoint(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        pw.checkpoint_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=2),
        )

        plane_path = self.run_dir / "execution" / "task-1.json"
        plane = json.loads(plane_path.read_text(encoding="utf-8"))
        plane["phase"] = "implementation"
        plane["completed_at"] = None
        plane["next_session_required"] = "no"
        plane["checkpoint_sha256"] = None
        plane_path.write_text(
            json.dumps(plane, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        plane_path.chmod(0o600)

        routed = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            self.prompt.name,
            clock=lambda: FIXED.replace(second=3),
        )
        self.assertEqual(routed["action"], "continue")

    def test_checkpoint_requires_explicit_handoff_and_stop_boundary(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "- Do not continue in current session: yes",
            "- Do not continue in current session: no",
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        self.assert_error(
            "CHECKPOINT_REQUIRED",
            pw.checkpoint_execution_plane,
            session_id="session-a",
        )

    def test_checkpoint_rejects_a_preclaim_commit(self) -> None:
        original_head = git("rev-parse", "HEAD", cwd=self.repo)
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        self.populate_checkpoint(commit_hash=original_head)
        self.assert_error(
            "CHECKPOINT_REQUIRED",
            pw.checkpoint_execution_plane,
            session_id="session-a",
        )

    def test_checkpoint_rejects_more_than_one_post_claim_commit(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        self.commit_task("Implement task-1")
        second_commit = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=second_commit)
        self.assert_error(
            "CHECKPOINT_REQUIRED",
            pw.checkpoint_execution_plane,
            session_id="session-a",
        )

    def test_checkpoint_commit_message_and_paths_are_exact_evidence(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "- Commit message: Implement task-1",
            "- Commit message: Different message",
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        self.assert_error(
            "CHECKPOINT_REQUIRED",
            pw.checkpoint_execution_plane,
            session_id="session-a",
        )
        text = handoff.read_text(encoding="utf-8").replace(
            "- Commit message: Different message",
            "- Commit message: Implement task-1",
        ).replace(
            "- Files changed: tracked.txt",
            "- Files changed: unexpected.txt",
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        self.assert_error(
            "CHECKPOINT_REQUIRED",
            pw.checkpoint_execution_plane,
            session_id="session-a",
        )

    def test_duplicate_checkpoint_for_one_task_is_rejected(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^### checkpoint-1\s*\n(.*?)(?=^### checkpoint-|^## |\Z)",
            text,
        )
        self.assertIsNotNone(match)
        assert match is not None
        duplicate = "### checkpoint-9\n" + match.group(1)
        text = text.replace("## Session Handoff", duplicate + "## Session Handoff")
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.checkpoint_execution_plane,
            session_id="session-a",
        )

    def test_duplicate_checkpoint_id_is_rejected_globally(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "### checkpoint-2",
            "### checkpoint-1",
            1,
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.checkpoint_execution_plane,
            session_id="session-a",
        )

    def test_orphan_checkpoint_task_is_rejected(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "### checkpoint-3\n\n- Completed task:",
            "### checkpoint-3\n\n- Completed task: task-999",
            1,
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.checkpoint_execution_plane,
            session_id="session-a",
        )

    def test_done_task_cannot_be_fabricated_without_a_stopped_plane(self) -> None:
        commit_hash = self.complete_queued_task(
            task_id="task-1",
            session_id="session-a",
            checkpoint_id="checkpoint-1",
            next_task="task-2",
            clock=FIXED.replace(second=1),
        )
        self.update_task_fields("task-2", {"Status": "done"})
        self.populate_checkpoint(
            commit_hash=commit_hash,
            task_id="task-2",
            checkpoint_id="checkpoint-2",
            next_task="task-3",
        )

        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.claim_execution_plane,
            session_id="session-b",
        )

    def test_runtime_session_identifier_is_required_without_state_changes(self) -> None:
        handoff_before = (self.run_dir / "handoff.md").read_bytes()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": ""}):
            self.assert_error(
                "SESSION_ID_UNAVAILABLE",
                pw.claim_execution_plane,
            )
        self.assertFalse((self.run_dir / "execution").exists())
        self.assertEqual((self.run_dir / "handoff.md").read_bytes(), handoff_before)

    def test_internal_cli_uses_only_runtime_session_fingerprint(self) -> None:
        environment = dict(os.environ)
        environment["CODEX_THREAD_ID"] = "runtime-session"
        claim = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "plane-claim",
                "--workspace",
                str(self.workspace),
                "--run-id",
                self.run_id,
                "--json",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        self.assertEqual(claim.returncode, 0, claim.stderr)
        output = json.loads(claim.stdout)
        self.assertEqual(output["task"], "task-1")
        self.assertNotIn("plane", output)
        plane = json.loads(self.plane_path().read_text(encoding="utf-8"))
        self.assertEqual(
            plane["owner_session_sha256"],
            hashlib.sha256(b"runtime-session").hexdigest(),
        )
        self.assertNotIn("runtime-session", self.plane_path().read_text(encoding="utf-8"))

        self.populate_plan("task-1")
        authorize = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "plane-authorize",
                "--workspace",
                str(self.workspace),
                "--run-id",
                self.run_id,
                "--json",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        self.assertEqual(authorize.returncode, 0, authorize.stderr)
        self.assertEqual(json.loads(authorize.stdout)["phase"], "implementation")

    def test_malformed_execution_plane_matrix_fails_closed(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        plane_path = self.plane_path()
        original = json.loads(plane_path.read_text(encoding="utf-8"))
        mutations = {
            "schema": {"schema": "task-implementer/execution-plane-v999"},
            "history": {"session_history_sha256": ["0" * 64]},
            "recovery": {"recovery_count": 1},
            "phase": {"phase": "implementation"},
            "timestamp": {"claimed_at": "2026-07-13T12:00:00"},
            "stop": {"next_session_required": "yes"},
        }
        for label, changes in mutations.items():
            with self.subTest(label=label):
                mutated = dict(original)
                mutated.update(changes)
                plane_path.write_text(
                    json.dumps(mutated, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                plane_path.chmod(0o600)
                self.assert_error(
                    "EXECUTION_STATE_INVALID",
                    pw.claim_execution_plane,
                    session_id="session-a",
                )
        plane_path.write_text(
            json.dumps(original, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        plane_path.chmod(0o600)
        second = dict(original)
        second["task_id"] = "task-2"
        second_path = plane_path.with_name("task-2.json")
        second_path.write_text(
            json.dumps(second, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        second_path.chmod(0o600)
        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.claim_execution_plane,
            session_id="session-a",
        )

    def test_checkpoint_rejects_dirty_worktree_after_commit(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        self.populate_plan("task-1")
        pw.authorize_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED.replace(second=1),
        )
        commit_hash = self.commit_task("Implement task-1")
        self.populate_checkpoint(commit_hash=commit_hash)
        (self.repo / "uncheckpointed.txt").write_text("dirty\n", encoding="utf-8")
        self.assert_error(
            "CHECKPOINT_REQUIRED",
            pw.checkpoint_execution_plane,
            session_id="session-a",
        )

    @unittest.skipUnless(os.name == "posix", "POSIX execution-plane safety")
    def test_execution_plane_rejects_unsafe_permissions_and_symlinks(self) -> None:
        pw.claim_execution_plane(
            self.workspace,
            self.run_id,
            session_id="session-a",
            clock=lambda: FIXED,
        )
        plane = self.plane_path()
        self.assertEqual(mode(plane), 0o600)
        self.assertEqual(mode(plane.parent), 0o700)
        plane.chmod(0o644)
        self.assert_error(
            "WORKSPACE_PERMISSION_INVALID",
            pw.claim_execution_plane,
            session_id="session-a",
        )

        plane.chmod(0o600)
        outside = self.root / "outside-execution"
        plane.parent.rename(outside)
        plane.parent.symlink_to(outside, target_is_directory=True)
        self.assert_error(
            "EXECUTION_STATE_INVALID",
            pw.claim_execution_plane,
            session_id="session-a",
        )


if __name__ == "__main__":
    unittest.main()
