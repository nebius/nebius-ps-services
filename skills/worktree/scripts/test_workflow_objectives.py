#!/usr/bin/env python3
"""Direct intake and binding tests using disposable committed repositories."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import workflow_objectives as objectives
from workflow_state import WorkflowError, read, write


class ObjectiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "repo"
        self.project.mkdir()
        self.home = self.root / "agent"
        self.home.mkdir(mode=0o700)
        for args in (("init", "-b", "main"), ("config", "user.email", "fixture@example.com"),
                     ("config", "user.name", "Fixture")):
            self.git(*args)
        (self.project / "README.md").write_text("fixture\n")
        self.git("add", "-A")
        self.git("commit", "-m", "fixture")
        self.value = {"outcome": "Add a counter", "scope": ["counter.py"],
                      "constraints": ["No remote writes"], "acceptance_criteria": ["Counter increments"],
                      "clarifications": [], "provenance": {"kind": "direct_request", "source_sha256": "a" * 64}}

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.project), *args], stderr=subprocess.DEVNULL, text=True).strip()

    def accept(self, **kwargs):
        return objectives.accept(self.project, self.home, "implement", "session-a", self.value, **kwargs)

    def test_automatic_sibling_projects_keep_source_independent(self):
        self.automatic_siblings("implement", "agentic-sdlc")

    def test_two_sdlc_siblings_continue_from_original_folders(self):
        self.automatic_siblings("agentic-sdlc", "agentic-sdlc")

    def automatic_siblings(self, first_owner, second_owner):
        for name in ("project-a", "project-b"):
            (self.project / name).mkdir()
            (self.project / name / "README.md").write_text(name)
        self.git("add", "-A")
        self.git("commit", "-m", "project scopes")
        self.git("remote", "add", "origin", str(self.root / "origin.git"))
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.git("checkout", "-b", "feature/parallel-projects")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home), "CODEX_THREAD_ID": "session-a"}):
            first = objectives.accept(self.project / "project-a", self.home, first_owner, "session-a", self.value)
            initial = self.git("rev-parse", "HEAD")
            self.git("commit", "--allow-empty", "-m", "independent source advancement")
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session-b"}):
                second = objectives.accept(self.project / "project-b", self.home, second_owner, "session-b", self.value)
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session-b"}):
                peer = objectives.continue_run(self.project / "project-b", self.home, second_owner, "session-b")
            self.assertEqual(second["run_dir"], peer["run_dir"])
            resumed = objectives.continue_run(self.project / "project-a", self.home, first_owner, "session-a")
        self.assertEqual(first["run_dir"], resumed["run_dir"])
        self.assertNotEqual(first["project_root"], second["project_root"])
        self.assertEqual(first["logical_project_root"], str(self.project / "project-a"))
        self.assertEqual(objectives._git(Path(first["project_root"]), "rev-parse", "HEAD"), initial)
        self.assertEqual(objectives._git(Path(second["project_root"]), "rev-parse", "HEAD"), self.git("rev-parse", "HEAD"))

    def test_accept_persists_only_normalized_objective(self):
        result = self.accept()
        run = Path(result["run_dir"])
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(read(run / "objectives/r0001.json"), objectives.normalize(self.value))
        self.assertFalse((run / "prompt.json").exists())
        self.assertEqual(os.stat(run / "objective.json").st_mode & 0o777, 0o600)

    def test_new_manual_checkout_after_terminal_run_keeps_logical_identity(self):
        import worktree_manager as manager
        import workflow_reconcile as reconcile

        self.git("remote", "add", "origin", str(self.root / "origin.git"))
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home), "CODEX_THREAD_ID": "session-a"}):
            first = self.accept()
            run = Path(first["run_dir"])
            self.git("checkout", "-b", "feature/manual-source")
            outer = manager.add_worktree(cwd=self.project, project=None, task_slug="manual-after-terminal")
            project = Path(outer["scope_cwd"])
            with self.assertRaises(WorkflowError) as blocked:
                objectives.accept(project, self.home, "implement", "session-b", self.value)
            self.assertEqual(blocked.exception.code, "CONTINUE_REQUIRED")
            objectives.fence(run, "session-a", "cancelled")
            self.assertEqual(reconcile.cancel_complete(run, "session-a")["status"], "CANCELLED")
            before = {str(p.relative_to(run)): p.read_bytes() for p in run.rglob("*") if p.is_file()}
            accepted = objectives.accept(project, self.home, "implement", "session-b", self.value)
            current, _ = objectives.validate_run(Path(accepted["run_dir"]))
            previous, _ = objectives.validate_run(run)
            self.assertEqual(current["project_id"], previous["project_id"])
            self.assertNotEqual(current["project_root"], previous["project_root"])
            self.assertEqual(current["logical_project_root"], str(project))
            self.assertFalse(current["automatic_outer"])
            self.assertEqual(before, {str(p.relative_to(run)): p.read_bytes()
                                      for p in run.rglob("*") if p.is_file()})

    def test_same_session_continues_without_new_revision(self):
        first = self.accept()
        second = self.accept()
        self.assertEqual(second["run_dir"], first["run_dir"])
        self.assertEqual(second["revision"], "r0001")
        self.assertEqual(second["status"], "CONTINUE")

    def test_explicit_continue_renews_only_exhausted_iteration_window(self):
        origin = self.root / "origin.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "origin", "main")
        result = objectives.accept(self.project, self.home, "agentic-sdlc", "session-a", self.value)
        run = Path(result["run_dir"])
        checkpoint = {"iteration_count": 200, "checkpoint_interval_iterations": 200,
                      "failure_count": 3, "no_progress_count": 2}
        write(run / "current-state.json", checkpoint)
        objectives.continue_run(self.project, self.home, "agentic-sdlc", "session-a")
        self.assertEqual(read(run / "run.json")["observation_checkpoint_start"], 200)
        self.assertEqual(read(run / "run.json")["observation_checkpoints"], 2)
        self.assertEqual(read(run / "current-state.json"), checkpoint)
        objectives.continue_run(self.project, self.home, "agentic-sdlc", "session-a")
        self.assertEqual(read(run / "run.json")["observation_checkpoints"], 2)

    def test_new_session_does_not_infer_sole_active_run(self):
        self.accept()
        self.assertIsNone(objectives.observe_binding(self.home, "implement", "session-b", self.project))
        with self.assertRaisesRegex(WorkflowError, "explicitly continue"):
            objectives.accept(self.project, self.home, "implement", "session-b", self.value)

    def test_cross_session_requires_explicit_continue(self):
        first = self.accept()
        second = objectives.continue_run(self.project, self.home, "implement", "session-b")
        self.assertEqual(second["run_dir"], first["run_dir"])
        with self.assertRaisesRegex(WorkflowError, "does not own"):
            objectives.observe_binding(self.home, "implement", "session-a")

    def test_restore_binding_is_read_only(self):
        first = self.accept()
        files = {p: p.read_bytes() for p in self.home.rglob("*.json")}
        self.assertEqual(objectives.observe_binding(self.home, "implement", "session-a")[0], Path(first["run_dir"]))
        self.assertEqual(files, {p: p.read_bytes() for p in self.home.rglob("*.json")})

    def test_steering_fences_dispatch_before_replanning(self):
        first = self.accept()
        self.value["acceptance_criteria"].append("Counter persists")
        steered = self.accept(revise=True)
        self.assertEqual(steered["revision"], "r0002")
        self.assertEqual(steered["fence"], "steering")
        with self.assertRaisesRegex(WorkflowError, "fenced"):
            objectives.check_dispatch(Path(first["run_dir"]))

    def test_other_objectives_are_not_queued(self):
        self.accept()
        self.value["outcome"] = "Replace unrelated service"
        with self.assertRaisesRegex(WorkflowError, "not queued"):
            self.accept()
        self.assertFalse(list(self.home.rglob("*queue*")))

    def test_cross_session_rejects_active_writers(self):
        first = self.accept()
        write(Path(first["run_dir"]) / "execution/FEAT-001/waves/WAVE-001.json", {"active_task_ids": ["TASK-001"]})
        with self.assertRaisesRegex(WorkflowError, "outstanding workers"):
            objectives.continue_run(self.project, self.home, "implement", "session-b")

    def test_cancellation_fences_without_git_rollback(self):
        first = self.accept()
        head = self.git("rev-parse", "HEAD")
        objectives.fence(Path(first["run_dir"]), "session-a", "cancelled")
        with self.assertRaises(WorkflowError):
            objectives.check_dispatch(Path(first["run_dir"]))
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_queued_cancellation_releases_fifo_and_project_claim_on_replay(self):
        import workflow_projects as projects
        import workflow_reconcile as reconcile
        for index in range(6):
            folder = self.project / f"project-{index}"
            folder.mkdir()
            (folder / "README.md").write_text("fixture\n")
        self.git("add", "-A")
        self.git("commit", "-m", "siblings")
        runs = []
        for index in range(6):
            accepted = objectives.accept(self.project / f"project-{index}", self.home,
                                         "implement", f"session-{index}", self.value)
            runs.append(Path(accepted["run_dir"]))
        self.assertEqual(projects.get(runs[4])["state"], "requested")
        objectives.fence(runs[4], "session-4", "cancelled")
        first = reconcile.cancel_complete(runs[4], "session-4")
        self.assertEqual(reconcile.cancel_complete(runs[4], "session-4"), first)
        self.assertEqual(projects.get(runs[4])["state"], "cancelled")
        objectives.fence(runs[0], "session-0", "cancelled")
        reconcile.cancel_complete(runs[0], "session-0")
        objectives.continue_run(self.project / "project-5", self.home, "implement", "session-5")
        self.assertEqual(projects.get(runs[5])["state"], "allocated")
        self.assertNotIn(str(runs[4]), read(self.home / "workflow-capacity/reservations.json")["runs"])
        replacement = objectives.accept(self.project / "project-4", self.home, "implement", "replacement", self.value)
        self.assertNotEqual(replacement["run_dir"], str(runs[4]))

    def test_cancelled_unleased_outer_reconciles_only_after_owner_cleanup(self):
        import workflow_projects as projects
        import workflow_reconcile as reconcile
        import worktree_manager as manager
        import worktree_interop as interop
        import worktree_state

        self.git("remote", "add", "origin", str(self.root / "origin.git"))
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.git("checkout", "-b", "feature/cancellation")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home), "CODEX_THREAD_ID": "session-a"}):
            with mock.patch.object(objectives, "_acquire_intake_lease", side_effect=RuntimeError("interrupted before lease")) as acquire:
                with self.assertRaisesRegex(RuntimeError, "interrupted before lease"):
                    self.accept()
            run = acquire.call_args.args[0]
            outer = projects.get(run)["outer"]
            objectives.fence(run, "session-a", "cancelled")
            reconcile.cancel_complete(run, "session-a")
            self.assertEqual(projects.get(run)["state"], "allocated")
            self.assertTrue(Path(outer["worktree"]).exists())
            manager.remove_worktree(cwd=self.project, name=outer["name"])
            # Outstanding owner records retain the claim even after Git cleanup.
            for module, loader in ((worktree_state, "load_manifest"), *(
                (interop, name) for name in ("load_lease", "load_lease_removal",
                                            "load_reservation", "load_preparation")
            )):
                with self.subTest(record=loader), mock.patch.object(module, loader, return_value={}):
                    reconcile.cancel_complete(run, "session-a")
                    self.assertEqual(projects.get(run)["state"], "allocated")
            for relative in ("execution/interop.json", "execution/FEAT-001/coordinator.json"):
                retained = run / relative
                write(retained, {})
                reconcile.cancel_complete(run, "session-a")
                self.assertEqual(projects.get(run)["state"], "allocated")
                retained.unlink()
            # Missing directory alone cannot hide a retained branch or path.
            self.git("branch", outer["branch"], outer["base_sha"])
            reconcile.cancel_complete(run, "session-a")
            self.assertEqual(projects.get(run)["state"], "allocated")
            self.git("branch", "-d", outer["branch"])
            path = Path(outer["worktree"])
            path.symlink_to(self.root / "absent-target")
            reconcile.cancel_complete(run, "session-a")
            self.assertEqual(projects.get(run)["state"], "allocated")
            path.unlink()
            # A crash between capacity release and the registry update must replay.
            with mock.patch.object(projects, "update", side_effect=RuntimeError("checkpoint")):
                with self.assertRaisesRegex(RuntimeError, "checkpoint"):
                    reconcile.cancel_complete(run, "session-a")
            first = reconcile.cancel_complete(run, "session-a")
            self.assertEqual(reconcile.cancel_complete(run, "session-a"), first)
            self.assertEqual(projects.get(run)["state"], "cancelled")
            self.assertNotIn(str(run), read(self.home / "workflow-capacity/reservations.json")["runs"])
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "replacement"}):
                replacement = objectives.accept(self.project, self.home, "implement", "replacement", self.value)
            self.assertNotEqual(replacement["run_dir"], str(run))

    def test_old_run_schema_is_rejected_without_conversion(self):
        first = self.accept()
        path = Path(first["run_dir"]) / "run.json"
        write(path, {"schema": "task-implementer/run-manifest-v1"})
        before = path.read_bytes()
        with self.assertRaisesRegex(WorkflowError, "obsolete"):
            objectives.validate_run(path.parent)
        self.assertEqual(path.read_bytes(), before)

    def test_snapshot_tampering_is_rejected(self):
        first = self.accept()
        path = Path(first["run_dir"]) / "objectives/r0001.json"
        value = read(path)
        value["outcome"] = "Tampered"
        write(path, value)
        with self.assertRaisesRegex(WorkflowError, "bytes changed"):
            objectives.validate_run(path.parent.parent)

    def test_raw_history_keys_are_rejected(self):
        self.value["conversation"] = ["raw turn"]
        with self.assertRaisesRegex(WorkflowError, "only normalized"):
            self.accept()

    def test_current_plan_has_explicit_provenance(self):
        self.value["provenance"]["kind"] = "current_plan"
        first = self.accept()
        self.assertEqual(read(Path(first["run_dir"]) / "objectives/r0001.json")["provenance"]["kind"], "current_plan")

    def test_detached_and_unborn_git_are_rejected(self):
        self.git("checkout", "--detach")
        with self.assertRaises(WorkflowError):
            self.accept()
        self.git("checkout", "--orphan", "empty")
        with self.assertRaises(WorkflowError):
            self.accept()

    def test_symlinked_private_root_is_rejected(self):
        target = self.root / "foreign"
        target.mkdir()
        (self.home / "implement").symlink_to(target)
        with self.assertRaises(WorkflowError):
            self.accept()
        self.assertEqual(list(target.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
