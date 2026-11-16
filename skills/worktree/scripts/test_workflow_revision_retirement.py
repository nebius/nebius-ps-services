"""Recover failed revision candidates without retaining execution capacity.

Git operations are real; native identity and termination are explicit fixtures.
"""

from pathlib import Path
import base64
import json
import os
import subprocess
import unittest
from unittest import mock

import workflow_execution as engine
from workflow_state import read, write
from test_workflow_design_revision import RevisionFixture, fixture_replan_future
import test_workflow_test_revision as revisions
import test_workflow_execution as fixtures
from test_workflow_fixture import complete_wave


class RetirementTests(RevisionFixture):
    classify = revisions.TestRevisionTests.classify
    prepare_revision = revisions.TestRevisionTests.prepare_revision
    review = revisions.TestRevisionTests.review
    seal = revisions.TestRevisionTests.seal

    def shared_capacity(self):
        import workflow_projects as projects
        run = read(self.run_dir / "run.json")
        write(self.run_dir / "run.json", {**run, "shared_admission": True})
        coordinator = engine._load_coordinator(self.run_dir, "FEAT-001")
        projects.reserve(self.run_dir, {"owner": run["owner"], "project_root": str(self.project),
                                       "git_root": coordinator["git_root"],
                                       "git_common_dir": run["git_common_dir"]},
                         self.run_dir.parents[2], run["coordinator_session_sha256"])
        self.assertTrue(projects.admit(self.run_dir))
        projects.update(self.run_dir, expected="requested", state="admitted")
        projects.update(self.run_dir, expected="admitted", state="allocated")
        identity = "FEAT-001/WAVE-001/TASK-001"
        projects.reserve_resources(self.run_dir, "worktrees", [identity])
        projects.reserve_resources(self.run_dir, "workers", [identity], ceiling=1)

    def replacement(self, *, dispatch=True):
        prepared = self.prepare_revision()
        self.review(prepared)
        self.seal()
        original = self.fixture.plan.read_text()
        replacement = self.fixture.plan.with_name("FEAT-001.plan.v2.md")
        replacement.write_text(original.replace("Plan v1", "Plan v2") +
                               "### TASK-002" + original.split("### TASK-001", 1)[1])
        replacement.with_suffix(".md.lock").write_text("locked\n")
        fixture_replan_future(self.run_dir, "FEAT-001", replacement, 1)
        return self.dispatch_replacement() if dispatch else None

    def dispatch_replacement(self):
        return engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-002")

    def archive(self):
        return self.run_dir / "execution/FEAT-001/retired-workers" / self.assignment["assignment_digest"]

    def test_revision_releases_stopped_shared_worker_before_replacement(self):
        self.shared_capacity()
        prepared = self.prepare_revision()
        reservation = read(self.run_dir.parents[2] / "workflow-capacity/reservations.json")
        row = reservation["runs"][str(self.run_dir)]
        self.assertEqual(row["workers"], [])
        self.assertIsNone(row["active_wave"])
        self.assertEqual(row["worktrees"], ["FEAT-001/WAVE-001/TASK-001"])
        self.assertEqual(self.candidate.read_bytes(), self.candidate_bytes)
        self.assertEqual(self.prepare_revision(), prepared)
        self.assertEqual(len(self.replacement()), 1)
        row = read(self.run_dir.parents[2] / "workflow-capacity/reservations.json")["runs"][str(self.run_dir)]
        self.assertEqual(row["workers"], ["FEAT-001/WAVE-002/TASK-002"])
        self.assertEqual(row["worktrees"], ["FEAT-001/WAVE-002/TASK-002"])

    def test_archive_recovers_staged_binary_untracked_ignored_modes_links_and_history(self):
        worker = Path(self.assignment["worktree"])
        staged = b"staged\x00binary\xff\n"
        self.candidate.write_bytes(staged)
        fixtures.git(worker, "add", "src/a.py")
        self.candidate.write_bytes(b"unstaged\x00different\xfe\n")
        self.candidate.chmod(0o755)
        (worker / ".gitignore").write_text("ignored.data\n")
        (worker / "ignored.data").write_bytes(b"ignored candidate bytes\x00")
        (worker / "empty-directory").mkdir()
        (worker / "candidate-link").symlink_to("missing-relative-target")
        original_assignment = read(engine.assignment_path(self.run_dir, "FEAT-001", "WAVE-001", "TASK-001"))
        self.replacement()
        self.assertFalse(worker.exists())
        self.assertFalse(engine.local_branch_exists(self.project, self.assignment["branch"]))
        archive = json.loads((self.archive() / "snapshot.json").read_bytes())
        entries = archive["candidate"]["files"]
        self.assertEqual(base64.b64decode(entries["src/a.py"]["content"]), b"unstaged\x00different\xfe\n")
        self.assertEqual(entries["src/a.py"]["mode"], 0o755)
        self.assertEqual(base64.b64decode(entries["ignored.data"]["content"]), b"ignored candidate bytes\x00")
        self.assertEqual(entries["empty-directory"]["type"], "directory")
        self.assertEqual(entries["candidate-link"]["target"], "missing-relative-target")
        bundle = self.root / "candidate.bundle"
        bundle.write_bytes(base64.b64decode(archive["history"]))
        recovered = self.root / "recovered"
        fixtures.git(self.root, "clone", "--branch", self.assignment["branch"], str(bundle), str(recovered))
        patch = self.root / "staged.patch"
        patch.write_bytes(base64.b64decode(archive["candidate"]["staged_patch"]))
        fixtures.git(recovered, "apply", "--cached", "--binary", str(patch))
        self.assertEqual(engine.git(recovered, ["rev-parse", "HEAD"], "restored archive"), self.tdd)
        self.assertEqual(subprocess.check_output(["git", "show", ":src/a.py"], cwd=recovered), staged)
        self.assertEqual(read(engine.assignment_path(self.run_dir, "FEAT-001", "WAVE-001", "TASK-001")), original_assignment)
        self.assertEqual((self.archive() / "snapshot.json").stat().st_mode & 0o777, 0o600)

    def test_interrupted_removal_resumes_from_verified_archive(self):
        self.replacement(dispatch=False)
        original_git = engine.git
        def interrupted(repo, arguments, action, **kwargs):
            result = original_git(repo, arguments, action, **kwargs)
            if arguments[:2] == ["worktree", "remove"]:
                raise engine.ExecutionError("TEST_INTERRUPTION", "fixture interruption after actual removal")
            return result
        with mock.patch.object(engine, "git", side_effect=interrupted), self.assertRaisesRegex(
                engine.ExecutionError, "fixture interruption"):
            self.dispatch_replacement()
        original = (self.archive() / "snapshot.json").read_bytes()
        self.assertFalse(self.candidate.exists())
        self.assertFalse((self.archive() / "retired.json").exists())
        self.assertEqual(len(self.dispatch_replacement()), 1)
        self.assertEqual((self.archive() / "snapshot.json").read_bytes(), original)
        self.assertTrue((self.archive() / "retired.json").is_file())

    def test_archive_recovers_index_resolve_undo_objects(self):
        worker = Path(self.assignment["worktree"])
        identities = []
        for content in (b"original conflict base\n", b"left conflict version\n", b"right conflict version\n"):
            identities.append(subprocess.check_output(["git", "hash-object", "-w", "--stdin"],
                                                      cwd=worker, input=content).decode().strip())
        rows = "".join(f"100644 {oid} {stage}\tsrc/conflict.txt\n"
                       for stage, oid in enumerate(identities, 1))
        subprocess.run(["git", "update-index", "--index-info"], cwd=worker,
                       input=rows.encode(), check=True, capture_output=True)
        (worker / "src/conflict.txt").write_text("resolved candidate\n")
        fixtures.git(worker, "add", "src/conflict.txt")
        self.assertTrue(fixtures.git(worker, "ls-files", "--resolve-undo"))
        self.replacement()
        archive = json.loads((self.archive() / "snapshot.json").read_bytes())
        bundle = self.root / "undo.bundle"
        bundle.write_bytes(base64.b64decode(archive["history"]))
        recovered = self.root / "undo-recovery"
        fixtures.git(self.root, "clone", "--branch", self.assignment["branch"], str(bundle), str(recovered))
        patch = self.root / "undo-staged.patch"
        patch.write_bytes(base64.b64decode(archive["candidate"]["staged_patch"]))
        fixtures.git(recovered, "apply", "--cached", "--binary", str(patch))
        for oid, encoded in archive["candidate"].get("index_objects", {}).items():
            actual = subprocess.check_output(["git", "hash-object", "-w", "--stdin"], cwd=recovered,
                                             input=base64.b64decode(encoded)).decode().strip()
            self.assertEqual(actual, oid)
        (recovered / ".git/index").write_bytes(base64.b64decode(archive["candidate"]["index"]))
        restored = subprocess.run(["git", "checkout", "-m", "--", "src/conflict.txt"],
                                  cwd=recovered, capture_output=True)
        self.assertEqual(restored.returncode, 0, "Archived raw index must retain its resolve-undo objects independently of the original repository.")
        content = (recovered / "src/conflict.txt").read_text()
        self.assertIn("left conflict version", content)
        self.assertIn("right conflict version", content)

    def test_archive_tampering_prevents_removal(self):
        self.replacement(dispatch=False)
        original_git = engine.git
        def interrupted(repo, arguments, action, **kwargs):
            if arguments[:2] == ["worktree", "remove"]:
                raise engine.ExecutionError("TEST_INTERRUPTION", "fixture interruption before removal")
            return original_git(repo, arguments, action, **kwargs)
        with mock.patch.object(engine, "git", side_effect=interrupted), self.assertRaises(engine.ExecutionError):
            self.dispatch_replacement()
        archive = self.archive() / "snapshot.json"
        value = json.loads(archive.read_bytes())
        value["candidate"]["files"]["src/a.py"]["content"] = base64.b64encode(b"tampered").decode()
        archive.write_text(json.dumps(value))
        with self.assertRaisesRegex(engine.ExecutionError, "archive receipt changed"):
            self.dispatch_replacement()
        self.assertEqual(self.candidate.read_bytes(), self.candidate_bytes)

    def test_interrupted_archive_publication_reuses_exact_snapshot(self):
        import workflow_revision_retirement as owner
        self.replacement(dispatch=False)
        original = owner._save_snapshot
        def interrupted(path, value):
            original(path, value)
            raise RuntimeError("fixture interruption after snapshot publication")
        with mock.patch.object(owner, "_save_snapshot", side_effect=interrupted), self.assertRaises(RuntimeError):
            self.dispatch_replacement()
        snapshot = (self.archive() / "snapshot.json").read_bytes()
        self.assertFalse((self.archive() / "archived.json").exists())
        self.assertEqual(self.candidate.read_bytes(), self.candidate_bytes)
        self.assertEqual(len(self.dispatch_replacement()), 1)
        self.assertEqual((self.archive() / "snapshot.json").read_bytes(), snapshot)

    def test_interrupted_release_resumes_after_exact_absence(self):
        self.shared_capacity()
        self.replacement(dispatch=False)
        original = engine._interop
        def interrupted(action, *args, **kwargs):
            if action == "archived worker release":
                raise RuntimeError("fixture interruption before resource release")
            return original(action, *args, **kwargs)
        with mock.patch.object(engine, "_interop", side_effect=interrupted), self.assertRaises(RuntimeError):
            self.dispatch_replacement()
        self.assertFalse(self.candidate.exists())
        self.assertFalse(engine.local_branch_exists(self.project, self.assignment["branch"]))
        row = read(self.run_dir.parents[2] / "workflow-capacity/reservations.json")["runs"][str(self.run_dir)]
        self.assertEqual(row["worktrees"], ["FEAT-001/WAVE-001/TASK-001"])
        self.assertEqual(len(self.dispatch_replacement()), 1)
        row = read(self.run_dir.parents[2] / "workflow-capacity/reservations.json")["runs"][str(self.run_dir)]
        self.assertEqual(row["worktrees"], ["FEAT-001/WAVE-002/TASK-002"])

    def test_live_writer_and_archive_bounds_preserve_candidate(self):
        import workflow_revision_retirement as owner
        self.replacement(dispatch=False)
        path = engine.task_path(self.run_dir, "FEAT-001", "WAVE-001", "TASK-001")
        stopped = read(path)
        write(path, {**stopped, "status": "running"})
        with self.assertRaises(engine.ExecutionError):
            owner.retire_sealed_revision_workers(self.run_dir, "FEAT-001")
        self.assertFalse((self.archive() / "snapshot.json").exists())
        write(path, stopped)
        with mock.patch.object(owner, "MAX_BYTES", 1), self.assertRaises(engine.ExecutionError):
            self.dispatch_replacement()
        self.assertEqual(self.candidate.read_bytes(), self.candidate_bytes)

    def test_terminal_replay_verifies_archive_without_mutating_capacity(self):
        import workflow_revision_retirement as owner
        self.shared_capacity()
        self.replacement()
        capacity = self.run_dir.parents[2] / "workflow-capacity/reservations.json"
        before = capacity.read_bytes()
        owner.retire_sealed_revision_workers(self.run_dir, "FEAT-001", verify_only=True)
        self.assertEqual(capacity.read_bytes(), before)
        (self.archive() / "snapshot.json").unlink()
        with self.assertRaisesRegex(engine.ExecutionError, "archive is missing"):
            owner.retire_sealed_revision_workers(self.run_dir, "FEAT-001", verify_only=True)
        self.assertEqual(capacity.read_bytes(), before)

    def test_split_index_is_retained_without_losing_external_index_state(self):
        worker = Path(self.assignment["worktree"])
        fixtures.git(worker, "update-index", "--split-index")
        shared = Path(fixtures.git(worker, "rev-parse", "--shared-index-path"))
        if not shared.is_absolute():
            shared = worker / shared
        original = shared.read_bytes()
        self.replacement(dispatch=False)
        with self.assertRaisesRegex(engine.ExecutionError, "split index"):
            self.dispatch_replacement()
        self.assertEqual(shared.read_bytes(), original)
        self.assertEqual(self.candidate.read_bytes(), self.candidate_bytes)
        self.assertFalse((self.archive() / "archived.json").exists())

    def test_retirement_rejects_an_unbound_coordinator(self):
        import workflow_revision_retirement as owner
        self.replacement(dispatch=False)
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "unbound-fixture-observer"}), self.assertRaises(
                engine.ExecutionError):
            owner.retire_sealed_revision_workers(self.run_dir, "FEAT-001")
        self.assertEqual(self.candidate.read_bytes(), self.candidate_bytes)
        self.assertFalse(self.archive().exists())

    def test_unsafe_candidate_files_are_retained(self):
        self.replacement(dispatch=False)
        worker = Path(self.assignment["worktree"])
        unsafe = worker / "unsafe"
        for kind in ("hardlink", "fifo", "nested-git"):
            with self.subTest(kind=kind):
                if kind == "hardlink":
                    os.link(self.candidate, unsafe)
                elif kind == "fifo":
                    os.mkfifo(unsafe)
                else:
                    unsafe.mkdir()
                    (unsafe / ".git").write_text("unowned nested repository")
                try:
                    with self.assertRaises(engine.ExecutionError):
                        self.dispatch_replacement()
                    self.assertEqual(self.candidate.read_bytes(), self.candidate_bytes)
                    self.assertFalse((self.archive() / "archived.json").exists())
                finally:
                    if kind == "nested-git":
                        (unsafe / ".git").unlink()
                        unsafe.rmdir()
                    else:
                        unsafe.unlink()

    def test_replacement_promotion_has_no_retained_worker_blocker(self):
        assignment = self.replacement()[0]
        self.fixture.start_assignment(assignment, "WAVE-002")
        target = Path(assignment["worktree"]) / "src/a.py"
        target.parent.mkdir(exist_ok=True)
        target.write_text("VALUE = 'fresh replacement'\n")
        fixtures.finish_task(self.run_dir, "FEAT-001", "WAVE-002", "TASK-002",
                             "deterministic fixture checks", "fixture review",
                             "feat: fresh replacement", summary="Synthetic fixture, not live evidence")
        engine.integrate_wave(self.run_dir, "FEAT-001", "WAVE-002")
        complete_wave(self.run_dir, "FEAT-001", "WAVE-002", "combined checks", session=self.session)
        engine.seal_feature(self.run_dir, "FEAT-001", "fixture checks", "feat: seal replacement")
        promoted = engine.promote_feature(self.run_dir, "FEAT-001", "fixture checks")
        self.assertEqual(promoted["status"], "done")
        self.assertEqual(promoted["cleanup_retained"], [])
        self.assertEqual(self.repair._current_promoted_head(self.run_dir, promoted), promoted["promoted_head"])


if __name__ == "__main__":
    unittest.main()
