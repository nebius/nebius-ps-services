#!/usr/bin/env python3
"""Real intake, ownership and crash-recovery checks for initial phase state."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "worktree/scripts"))
sys.path.insert(0, str(Path(__file__).parent))
import workflow_intake  # noqa: E402,F401 — load the trusted runtime first
import workflow_objectives as objectives  # noqa: E402
from workflow_state import WorkflowError, read, write  # noqa: E402

SCRIPT = Path(__file__).with_name("initialize_phase.py")


class InitializePhaseTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temp).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.home = self.root / "agent"
        self.home.mkdir(mode=0o700)
        self.enterContext(mock.patch.dict(os.environ, {
            "CODEX_HOME": str(self.home), "CODEX_THREAD_ID": "startup-root",
            "SKILLS_AGENT": "codex", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1",
        }))
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.com")
        (self.project / "README.md").write_text("fixture\n")
        self.git("add", "-A")
        self.git("commit", "-m", "fixture")
        origin = self.root / "origin.git"
        self.git("init", "--bare", "-b", "main", str(origin))
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "origin", "main")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        accepted = objectives.accept(self.project, self.home, "agentic-sdlc", "startup-root", {
            "outcome": "Implement a counter", "scope": ["counter"],
            "constraints": [], "acceptance_criteria": ["counter increments"],
            "clarifications": [], "provenance": {"kind": "direct_request", "source_sha256": "a" * 64},
        })
        self.run = Path(accepted["run_dir"])

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.project), *args], text=True, stderr=subprocess.DEVNULL,
        ).strip()

    def helper(self):
        return importlib.import_module("initialize_phase")

    def initialize(self):
        return self.helper().initialize(self.project)

    def snapshot(self):
        return {str(p.relative_to(self.run)): p.read_bytes()
                for p in self.run.rglob("*") if p.is_file()}

    def assert_blocked_unchanged(self):
        before = self.snapshot()
        with self.assertRaises((WorkflowError, OSError, ValueError)):
            self.initialize()
        self.assertEqual(before, self.snapshot())

    def test_hook_only_history_is_preserved_by_fresh_cli(self):
        history = self.run / "history"
        history.mkdir(mode=0o700)
        audit = history / "hook-events.jsonl"
        audit.write_text('{"event":"PreToolUse","decision":"allow"}\n')
        audit.chmod(0o600)
        write(history / "continuation-state.json", {"no_progress_count": 1})
        before = self.snapshot()
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--project-root", str(self.project)],
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["status"], "INITIALIZED")
        current = read(self.run / "current-state.json")
        self.assertEqual(current["current_phase"], "sdlc-start")
        self.assertEqual(current["status"], "running")
        self.assertEqual(set(current["retry_counts"].values()), {0})
        self.assertIsNone(current["last_successful_phase"])
        self.assertIsNone(current["current_execution"])
        self.assert_publication(current)
        after = self.snapshot()
        for name, data in before.items():
            self.assertEqual(after[name], data, name)

    def assert_publication(self, current):
        relative = "checkpoints/" + current["checkpoint_id"] + ".json"
        checkpoint = read(self.run / relative)
        self.assertEqual(read(self.run / "checkpoints/latest.json"), {
            "checkpoint_id": current["checkpoint_id"], "path": relative,
            "created_at": checkpoint["created_at"],
        })
        self.assertEqual(checkpoint["created_at"], current["updated_at"])
        self.assertEqual(checkpoint["fingerprint_ids"], [])
        self.assertTrue(checkpoint["evidence"])
        self.assertTrue(all(value is None for value in checkpoint["evidence"].values()))
        self.assertEqual({key: checkpoint[key] for key in current}, current)

    def test_repeated_initialization_is_read_only(self):
        self.initialize()
        before = self.snapshot()
        self.assertEqual(self.initialize()["status"], "ALREADY_INITIALIZED")
        self.assertEqual(before, self.snapshot())

    def test_concurrent_initialization_has_one_authoritative_snapshot(self):
        self.helper()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.initialize(), range(2)))
        self.assertCountEqual([v["status"] for v in results], ["INITIALIZED", "ALREADY_INITIALIZED"])
        self.assertEqual(len(list((self.run / "checkpoints").glob("checkpoint-*.json"))), 1)

    def test_foreign_or_missing_native_session_has_no_effect(self):
        for session in ("foreign-worker", ""):
            with self.subTest(session=bool(session)), mock.patch.dict(os.environ, {"CODEX_THREAD_ID": session}):
                self.assert_blocked_unchanged()

    def test_wrong_selected_home_or_project_has_no_effect(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.root / "foreign")}):
            self.assert_blocked_unchanged()
        with self.assertRaises(WorkflowError):
            self.helper().initialize(self.root)

    def test_active_ownership_disagreement_has_no_effect(self):
        for name, field, value in (
            ("active-run.json", "run_id", "run-foreign"),
            ("active.lock", "owner_session_hash", "f" * 64),
            ("active.lock", "project_id", "foreign"),
            ("active.lock", "status", "done"),
            ("project.json", "project_root", str(self.root)),
        ):
            path = self.run.parent / name
            original = read(path)
            with self.subTest(name=name, field=field):
                write(path, {**original, field: value})
                self.assert_blocked_unchanged()
            write(path, original)

    def test_fenced_or_nonaccepted_run_is_not_reinitialized(self):
        path = self.run / "run.json"
        original = read(path)
        for change in ({"fence": "steering"}, {"status": "done"}):
            write(path, {**original, **change})
            self.assert_blocked_unchanged()
        write(path, original)

    def test_later_or_unknown_evidence_is_not_deleted(self):
        for name in ("history/iteration-0007.md", "history/unknown.json", "plans/plan.md",
                     "execution/FEAT-001/coordinator.json", "evidence/result.json", "unknown.json"):
            path = self.run / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text("retained evidence\n")
            path.chmod(0o600)
            with self.subTest(name=name):
                self.assert_blocked_unchanged()
            path.unlink()
            if path.parent != self.run:
                path.parent.rmdir()
            if path.parent.parent.name == "execution":
                path.parent.parent.rmdir()

    def test_progressed_state_is_never_reset(self):
        self.initialize()
        current = read(self.run / "current-state.json")
        current.update(current_phase="sdlc-tdd", iteration_count=9, last_successful_phase="sdlc-create-plan")
        current["retry_counts"]["test"] = 2
        write(self.run / "current-state.json", current)
        self.assert_blocked_unchanged()

    def test_interrupted_initial_publication_uses_new_snapshot(self):
        helper = self.helper()
        for boundary in ("checkpoint", "latest", "current", "history"):
            with self.subTest(boundary=boundary):
                self.setUp()
                original = helper.write
                history_write = helper._write_history
                def interrupted(path, value, **kwargs):
                    original(path, value, **kwargs)
                    if ((boundary == "checkpoint" and path.name.startswith("checkpoint-"))
                            or boundary == "latest" and path.name == "latest.json"
                            or boundary == "current" and path.name == "current-state.json"):
                        raise OSError("simulated interruption")
                def interrupted_history(*args):
                    history_write(*args)
                    if boundary == "history":
                        raise OSError("simulated interruption")
                with (mock.patch.object(helper, "write", side_effect=interrupted),
                      mock.patch.object(helper, "_write_history", side_effect=interrupted_history)):
                    with self.assertRaises(OSError):
                        self.initialize()
                before = self.snapshot()
                result = self.initialize()
                self.assertEqual(result["status"], "ALREADY_INITIALIZED" if boundary == "history" else "RECOVERED")
                after = self.snapshot()
                for name, data in before.items():
                    if name not in {"current-state.json", "checkpoints/latest.json"}:
                        self.assertEqual(after[name], data, name)
                self.assert_publication(read(self.run / "current-state.json"))

    def test_corrupt_snapshot_or_pointer_is_retained(self):
        self.initialize()
        for name in ("checkpoints/checkpoint-0001.json", "checkpoints/latest.json", "current-state.json"):
            path = self.run / name
            original = path.read_bytes()
            path.write_text('{"truncated":')
            with self.subTest(name=name):
                self.assert_blocked_unchanged()
            path.write_bytes(original)

    def test_unsafe_history_is_rejected_without_touching_target(self):
        target = self.root / "outside.json"
        target.write_text("outside\n")
        history = self.run / "history"
        history.mkdir(mode=0o700)
        (history / "hook-events.jsonl").symlink_to(target)
        self.assert_blocked_unchanged()
        self.assertEqual(target.read_text(), "outside\n")

    def test_no_public_identity_override(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--project-root", str(self.project), "--session", "startup-root"],
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.run / "current-state.json").exists())

    def test_partial_initial_history_is_preserved_and_recovered(self):
        self.initialize()
        current = read(self.run / "current-state.json")
        path = self.run / "history/iteration-0001.md"
        for partial in (b"", b"# Initial phase checkpoint\n"):
            path.write_bytes(partial)
            result = self.initialize()
            self.assertEqual(result["status"], "RECOVERED")
            self.assertEqual(path.read_bytes(), partial)
            next_state = read(self.run / "current-state.json")
            self.assertNotEqual(next_state["checkpoint_id"], current["checkpoint_id"])
            self.assert_publication(next_state)
            # Exercise a torn newest history file on the next iteration.
            current = next_state
            path = self.run / "history" / (current["checkpoint_id"].replace("checkpoint-", "iteration-") + ".md")

    def managed_intake(self):
        import worktree_manager as manager
        value = read(self.run / "objectives/r0001.json")
        outer = manager.add_worktree(cwd=self.project, project=None, task_slug="startup-test")
        self.project = Path(outer["scope_cwd"])
        self.enterContext(mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "startup-managed"}))
        accepted = objectives.accept(self.project, self.home, "agentic-sdlc", "startup-managed", value)
        self.run = Path(accepted["run_dir"])
        return manager

    def test_managed_intake_requires_exact_active_outer_lease(self):
        self.managed_intake()
        path = self.run / "execution/interop.json"
        original = read(path)
        self.assertEqual(self.initialize()["status"], "INITIALIZED")
        path.unlink()
        self.assert_blocked_unchanged()
        path.parent.rmdir()
        self.assert_blocked_unchanged()
        write(path, {**original, "lease_id": "f" * 32})
        self.assert_blocked_unchanged()
        write(path, original)
        self.assertEqual(self.initialize()["status"], "ALREADY_INITIALIZED")

    def test_externally_released_outer_lease_cannot_initialize(self):
        manager = self.managed_intake()
        lease = read(self.run / "execution/interop.json")
        manager.task_lease_promote(cwd=self.project, owner_kind="agentic-sdlc",
                                   name=lease["name"], lease_id=lease["lease_id"],
                                   expected_head=lease["initial_head"], promoted_head=lease["initial_head"])
        manager.task_lease_release(cwd=self.project, owner_kind="agentic-sdlc",
                                   name=lease["name"], lease_id=lease["lease_id"],
                                   promoted_head=lease["initial_head"])
        self.assert_blocked_unchanged()


if __name__ == "__main__":
    unittest.main()
