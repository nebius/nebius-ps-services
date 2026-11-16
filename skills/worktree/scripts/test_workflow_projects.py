"""Cross-owner admission and atomic shared capacity regressions."""
import concurrent.futures
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

import workflow_projects as p
from workflow_state import WorkflowError, write


class ProjectAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "home"
        self.repo = self.base / "repo"
        self.common = self.repo / ".git"
        self.common.mkdir(parents=True)

    def run_context(self, folder, owner="implement", name="one"):
        project = self.repo / folder
        project.mkdir(parents=True, exist_ok=True)
        root = "implement" if owner == "implement" else "sdlc-runs"
        run = self.home / root / p.project_id(self.common, folder) / ("run-" + p.project_id(self.common, folder) + "-" + name)
        context = {"owner": owner, "project_root": str(project), "git_root": str(self.repo),
                   "git_common_dir": str(self.common)}
        write(run / "run.json", context)
        return run, context

    def reserve(self, folder, owner="implement", name="one"):
        run, context = self.run_context(folder, owner, name)
        return run, p.reserve(run, context, self.home, "a" * 64)

    def test_siblings_share_repository_across_owners(self):
        a, _ = self.reserve("project-a")
        b, _ = self.reserve("project-b", "agentic-sdlc")
        self.assertEqual(len(p.records(self.common)), 2)
        self.assertTrue(p.admit(a))
        self.assertTrue(p.admit(b))

    def test_equal_prefix_and_root_scopes_conflict(self):
        self.reserve("project-a")
        for folder in ("project-a", "project-a/nested", "."):
            with self.subTest(folder=folder), self.assertRaises(WorkflowError):
                self.reserve(folder, "agentic-sdlc", "two")
        self.reserve("project-ab")

    def test_shared_claims_are_exclusive_until_cleanup(self):
        a, _ = self.reserve("project-a")
        b, _ = self.reserve("project-b")
        p.claim(a, ["shared/config.json"])
        with self.assertRaises(WorkflowError):
            p.claim(b, ["shared"])
        p.update(a, expected="requested", state="admitted")
        p.update(a, expected="admitted", state="allocated", outer={"name": "owned"})
        p.update(a, expected="allocated", state="aggregation-ready")
        p.update(a, expected="aggregation-ready", state="delivered", delivery={"head": "a" * 40})
        with self.assertRaises(WorkflowError):
            p.claim(b, ["shared/config.json"])
        p.update(a, expected="delivered", state="cleaned")
        p.claim(b, ["shared/config.json"])

    def test_waiting_objectives_allocate_no_worker_capacity(self):
        runs = [self.reserve(f"project-{i}")[0] for i in range(6)]
        self.assertEqual([p.admit(run) for run in runs], [True] * 4 + [False] * 2)
        with self.assertRaises(WorkflowError):
            p.reserve_resources(runs[4], "workers", ["task"])
        p.release(runs[0])
        self.assertFalse(p.admit(runs[5]))
        self.assertTrue(p.admit(runs[4]))

    def test_retained_worktrees_and_unknown_capacity(self):
        a, _ = self.reserve("a")
        b, _ = self.reserve("b")
        p.admit(a)
        p.admit(b)
        p.reserve_resources(a, "worktrees", [str(i) for i in range(12)])
        with self.assertRaises(WorkflowError):
            p.reserve_resources(b, "worktrees", [str(i) for i in range(5)])
        self.assertEqual(p.reserve_resources(a, "workers", ["a", "b"], ceiling=1), ["a"])
        self.assertEqual(p.reserve_resources(b, "workers", ["c"], ceiling=1), [])
        with self.assertRaises(WorkflowError):
            p.release(a)
        p.release_resources(a, "workers", ["a"])
        self.assertEqual(p.reserve_resources(b, "workers", ["c"], ceiling=1), ["c"])

    def test_diagnostics_share_native_pool_without_wave_fairness(self):
        a, _ = self.reserve("a")
        b, _ = self.reserve("b")
        p.admit(a)
        p.admit(b)
        self.assertEqual(p.reserve_resources(a, "workers", ["wave/task"], ceiling=4), ["wave/task"])
        ids = ["diagnostic/event/one", "diagnostic/event/two", "diagnostic/event/three"]
        self.assertEqual(p.reserve_resources(b, "diagnostics", ids, ceiling=4), ids)
        self.assertEqual(p.reserve_resources(a, "workers", ["wave/another"], ceiling=4), [])
        _, state = p._capacity(self.home)
        self.assertIsNone(state["runs"][str(b)]["active_wave"])
        self.assertFalse(state["runs"][str(b)]["waiting"])
        with self.assertRaises(WorkflowError):
            p.release(b)
        p.release_resources(b, "diagnostics", ids)
        self.assertEqual(p.reserve_resources(a, "workers", ["wave/another"], ceiling=4), ["wave/another"])

    def test_processes_cannot_overbook_worker_slots(self):
        runs = [self.reserve(f"project-{i}")[0] for i in range(2)]
        for run in runs:
            p.admit(run)
        source = ("import sys; from pathlib import Path; import workflow_projects as p; "
                  "print(len(p.reserve_resources(Path(sys.argv[1]), 'workers', ['1','2','3','4'])))")
        def invoke(run):
            result = subprocess.run([sys.executable, "-c", source, str(run)],
                                    cwd=Path(__file__).parent, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return int(result.stdout)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            totals = list(pool.map(invoke, runs))
        self.assertEqual(sum(totals), 4)

    def test_transition_replay_cannot_release_early(self):
        run, _ = self.reserve("a")
        with self.assertRaises(WorkflowError):
            p.update(run, expected="requested", state="cleaned")
        first = p.update(run, expected="requested", state="admitted")
        self.assertEqual(first, p.update(run, expected="requested", state="admitted"))

    def test_shared_claims_preserve_all_canonical_pair_ownership(self):
        import workflow_execution as execution
        run, _ = self.reserve("project-a")
        p.update(run, expected="requested", state="admitted")
        p.update(run, expected="admitted", state="allocated")
        with mock.patch.object(execution, "validate_run", return_value=({"shared_admission": True}, {})), mock.patch.object(execution, "_reject_special_claims"), mock.patch.object(execution, "git", return_value="100644"):
            for name in ("requirements.md", "design.md"):
                path = "project-b/docs/" + name
                claim = execution.WriteClaim("exact", path)
                task = SimpleNamespace(task_id="TASK-001", write_claims=(claim,))
                with self.assertRaises(execution.ExecutionError):
                    execution._verify_claim_paths(self.repo, "project-a", [task], run)
                p.claim(run, ["project-b"])
                with self.assertRaises(execution.ExecutionError):
                    execution._verify_worker_paths(self.repo, [path], (claim,), "project-a", run)
            ordinary = execution.WriteClaim("exact", "shared/config.json")
            task = SimpleNamespace(task_id="TASK-001", write_claims=(ordinary,))
            execution._verify_claim_paths(self.repo, "project-a", [task], run)
            execution._verify_worker_paths(self.repo, [ordinary.path], (ordinary,), "project-a", run)

    def test_retired_wave_releases_absent_trees_but_retains_uncertain_paths(self):
        import workflow_capacity as capacity
        import workflow_execution as execution
        run, _ = self.reserve("project-a")
        p.admit(run)
        tasks = [f"TASK-{index:03d}" for index in range(1, 9)]
        identities = [f"FEAT-001/WAVE-001/{task}" for task in tasks]
        p.reserve_resources(run, "worktrees", identities)
        write(run / "execution/FEAT-001/coordinator.json", {"owner": "implement", "git_root": str(self.repo)})
        retained = run / "worktrees/FEAT-001/waves/WAVE-001/TASK-001"
        retained.mkdir(parents=True)
        wave = {"wave_id": "WAVE-001", "task_ids": tasks, "status": "superseded"}
        with mock.patch.object(execution, "worktrees", return_value={}), mock.patch.object(execution, "local_branch_exists", return_value=False), mock.patch("workflow_objectives.validate_run", return_value=({"shared_admission": True}, {})):
            capacity.reconcile_retired_wave(run, "FEAT-001", wave)
            from workflow_state import read
            ledger = self.home / "workflow-capacity/reservations.json"
            self.assertEqual(read(ledger)["runs"][str(run)]["worktrees"], identities[:1])
            retained.rmdir()
            capacity.reconcile_retired_wave(run, "FEAT-001", wave)
            self.assertEqual(read(ledger)["runs"][str(run)]["worktrees"], [])
            before = ledger.read_bytes()
            capacity.reconcile_retired_wave(run, "FEAT-001", wave)
            self.assertEqual(ledger.read_bytes(), before)

    def test_another_home_cannot_bypass_repository_claims(self):
        self.reserve("a")
        run, context = self.run_context("b")
        with self.assertRaises(WorkflowError):
            p.reserve(run, context, self.base / "another-home", "b" * 64)


if __name__ == "__main__":
    unittest.main()
