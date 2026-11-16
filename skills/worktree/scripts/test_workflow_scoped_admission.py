"""Real-Git admission and immutable-input regressions; no native-agent claims."""
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import workflow_inputs as inputs
import workflow_objectives as objectives
import workflow_projects as projects
import worktree_manager as manager
from workflow_state import WorkflowError, read, write


class ScopedAdmissionTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.repo, self.home = root / "repo", root / "home"
            self.repo.mkdir()
            self.git("init", "-b", "main")
            self.git("config", "user.name", "Fixture")
            self.git("config", "user.email", "fixture@example.com")
            for name in ("clean/a.txt", "busy/b.txt", "shared/config.txt", "AGENTS.md", ".gitignore"):
                path = self.repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("baseline\n" if name != ".gitignore" else "ignored\n")
            self.git("add", "-A")
            self.git("commit", "-m", "baseline")
            self.git("remote", "add", "origin", str(root / "origin.git"))
            self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
            self.git("checkout", "-b", "feature/example-branch1")
            self.base = self.git("rev-parse", "HEAD")
            self.declaration = {"schema": inputs.SCHEMA, "base_head": self.base, "complete": True,
                                "claims": [{"kind": "prefix", "path": "shared"}]}
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home), "CODEX_THREAD_ID": "scoped-test"}), \
                    mock.patch("workflow_statistics.safe_record"), mock.patch("workflow_statistics.safe_clock"):
                yield

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True,
                                       stderr=subprocess.DEVNULL).strip()

    def accept(self, owner="implement", declaration=True, project="clean"):
        return objectives.accept(self.repo / project, self.home, owner, "scoped-test", {
            "outcome": "Update selected project", "scope": [project], "constraints": [],
            "acceptance_criteria": ["Selected project passes"], "clarifications": [],
            "provenance": {"kind": "direct_request", "source_sha256": "a" * 64}},
            input_dependencies=self.declaration if declaration else None)

    def add_gitlink(self):
        nested = self.repo / "vendor"
        nested.mkdir()
        def nested_git(*args):
            return subprocess.check_output(["git", "-C", str(nested), *args],
                                           stderr=subprocess.DEVNULL)
        nested_git("init", "-b", "main")
        nested_git("config", "user.name", "Fixture")
        nested_git("config", "user.email", "fixture@example.com")
        data = nested / "data/input.txt"
        data.parent.mkdir()
        data.write_text("baseline\n")
        nested_git("add", "-A")
        nested_git("commit", "-m", "nested baseline")
        (self.repo / ".gitmodules").write_text('[submodule "vendor"]\n\tpath = vendor\n\turl = ../vendor.git\n')
        self.git("add", "vendor", ".gitmodules")
        self.git("commit", "-m", "track shared gitlink")
        self.base = self.git("rev-parse", "HEAD")
        self.declaration["base_head"] = self.base
        return data

    def test_dirty_gitlink_blocks_nested_shared_inputs_for_both_owners(self):
        for owner in ("implement", "agentic-sdlc"):
            for claim in ({"kind": "exact", "path": "vendor/data/input.txt"},
                          {"kind": "prefix", "path": "vendor/data"}):
                with self.subTest(owner=owner, claim=claim), self.fixture():
                    data = self.add_gitlink()
                    data.write_text("uncommitted dependency\n")
                    self.assertEqual(manager.status_paths(self.repo), ["vendor"])
                    self.declaration["claims"] = [claim]
                    with self.assertRaises(WorkflowError):
                        self.accept(owner)
                    self.assertEqual(len(manager.list_worktrees(self.repo)), 1)

    def test_dirty_gitlink_blocks_new_read_and_write_claims(self):
        with self.fixture():
            data = self.add_gitlink()
            accepted = self.accept()
            run = Path(accepted["run_dir"])
            data.write_text("uncommitted dependency\n")
            added = {"kind": "exact", "path": "vendor/data/input.txt"}
            with self.assertRaises(WorkflowError):
                inputs.declare(run, {**self.declaration, "claims": [*self.declaration["claims"], added]})
            with self.assertRaises(WorkflowError):
                inputs.check_expansion(run, [added])

    def test_dependency_check_does_not_hide_configured_submodule_dirt(self):
        for key in ("submodule.vendor.ignore", "diff.ignoreSubmodules"):
            with self.subTest(key=key), self.fixture():
                data = self.add_gitlink()
                data.write_text("uncommitted dependency\n")
                self.git("config", key, "all")
                self.assertEqual(manager.status_paths(self.repo), [])
                # Observe hidden dirt even when declarations do not cross the
                # gitlink, so the unsupported-input guard cannot mask this check.
                self.assertIn("vendor", inputs.dirty_inputs(self.repo, self.declaration["claims"], base=self.base))
                inputs.check_source(self.repo, "clean", self.declaration, base=self.base)
                self.declaration["claims"] = [{"kind": "exact", "path": "vendor/data/input.txt"}]
                with self.assertRaises(WorkflowError):
                    self.accept()

    def test_gitlink_input_needs_supported_snapshot_even_when_status_is_clean(self):
        for ignored in (False, True):
            with self.subTest(ignored=ignored), self.fixture():
                data = self.add_gitlink()
                if ignored:
                    (self.repo / "vendor/.git/info/exclude").write_text("ignored-input\n")
                    data = self.repo / "vendor/ignored-input"
                    data.write_text("ignored dependency\n")
                self.assertEqual(manager.status_paths(self.repo, include_submodule_dirt=True), [])
                self.declaration["claims"] = [{"kind": "exact", "path": data.relative_to(self.repo).as_posix()}]
                with self.assertRaises(WorkflowError) as raised:
                    self.accept()
                self.assertEqual(raised.exception.code, "UNSUPPORTED_SUBMODULE_INPUT")

    def test_expanded_parent_write_is_not_covered_by_owned_child(self):
        with self.fixture():
            accepted = self.accept()
            run = Path(accepted["run_dir"])
            projects.claim(run, ["shared/owned"])
            (self.repo / "shared/config.txt").write_text("uncommitted sibling\n")
            with self.assertRaises(WorkflowError):
                inputs.check_expansion(run, [{"kind": "prefix", "path": "shared"}])

    def test_both_owners_exclude_sibling_dirt_without_importing_or_changing_it(self):
        for owner in ("implement", "agentic-sdlc"):
            with self.subTest(owner=owner), self.fixture():
                busy = self.repo / "busy/b.txt"
                busy.write_text("staged\n")
                self.git("add", "busy/b.txt")
                busy.write_text("unstaged\n")
                (self.repo / "busy/untracked.txt").write_text("untracked\n")
                before = (self.git("rev-parse", "HEAD"), self.git("ls-files", "--stage"),
                          self.git("status", "--porcelain"), busy.read_bytes())
                accepted = self.accept(owner)
                outer = Path(accepted["project_root"]).parent
                self.assertEqual((outer / "busy/b.txt").read_text(), "baseline\n")
                self.assertFalse((outer / "busy/untracked.txt").exists())
                self.assertEqual(before, (self.git("rev-parse", "HEAD"), self.git("ls-files", "--stage"),
                                         self.git("status", "--porcelain"), busy.read_bytes()))

    def test_protected_inputs_block_both_owners_before_outer_creation(self):
        for owner in ("implement", "agentic-sdlc"):
            for relative in ("clean/a.txt", "clean/new.txt", "shared/config.txt", "AGENTS.md",
                             "AGENTS.override.md", ".gitignore", "clean/docs/requirements.md"):
                with self.subTest(owner=owner, path=relative), self.fixture():
                    path = self.repo / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("dirty\n")
                    with self.assertRaisesRegex(WorkflowError, "uncommitted"):
                        self.accept(owner)
                    self.assertEqual(len(manager.list_worktrees(self.repo)), 1)

    def test_missing_incomplete_and_stale_declarations_fail(self):
        for bad in (None, "incomplete", "stale"):
            with self.subTest(case=bad), self.fixture():
                (self.repo / "busy/b.txt").write_text("dirty\n")
                if bad == "incomplete":
                    self.declaration["complete"] = False
                elif bad == "stale":
                    self.declaration["base_head"] = "0" * 40
                with self.assertRaises(WorkflowError):
                    self.accept(declaration=bad is not None)
                self.assertEqual(len(manager.list_worktrees(self.repo)), 1)

    def test_ignored_explicit_inputs_cannot_be_excluded(self):
        for claim in ({"kind": "exact", "path": "ignored"}, {"kind": "prefix", "path": "shared"}):
            with self.subTest(claim=claim), self.fixture():
                path = self.repo / ("shared/ignored" if claim["kind"] == "prefix" else "ignored")
                path.write_text("uncommitted dependency\n")
                self.declaration["claims"] = [claim]
                with self.assertRaises(WorkflowError):
                    self.accept()
                self.assertEqual(len(manager.list_worktrees(self.repo)), 1)

    def test_creation_crash_cannot_reuse_an_outer_from_another_declared_base(self):
        with self.fixture():
            original = projects.update
            def crash(*args, **kwargs):
                if kwargs.get("state") == "allocated":
                    raise RuntimeError("creation crash")
                return original(*args, **kwargs)
            with mock.patch.object(projects, "update", side_effect=crash), self.assertRaises(RuntimeError):
                self.accept()
            self.assertEqual(len(manager.list_worktrees(self.repo)), 2)
            self.git("commit", "--allow-empty", "-m", "advance original")
            self.declaration["base_head"] = self.git("rev-parse", "HEAD")
            with self.assertRaisesRegex(WorkflowError, "recovery base differs"):
                objectives.continue_run(self.repo / "clean", self.home, "implement", "scoped-test",
                                        input_dependencies=self.declaration)
            self.assertEqual(len(manager.list_worktrees(self.repo)), 2)

    def test_whole_repository_and_cross_scope_rename_remain_blocked(self):
        with self.fixture():
            self.git("mv", "busy/b.txt", "clean/b.txt")
            with self.assertRaises(WorkflowError):
                self.accept()
        with self.fixture():
            (self.repo / "busy/b.txt").write_text("dirty\n")
            with self.assertRaises(WorkflowError):
                self.accept(project=".")

    def test_second_preflight_catches_new_protected_dirt(self):
        with self.fixture():
            original = manager._add_worktree_unlocked
            def race(**kwargs):
                (self.repo / "clean/a.txt").write_text("racing writer\n")
                return original(**kwargs)
            with mock.patch.object(manager, "_add_worktree_unlocked", side_effect=race), self.assertRaises(WorkflowError):
                self.accept()
            self.assertEqual(len(manager.list_worktrees(self.repo)), 1)

    def test_second_preflight_rejects_changed_declaration(self):
        with self.fixture():
            original = manager._add_worktree_unlocked
            def race(**kwargs):
                changed = {**self.declaration, "claims": []}
                write(kwargs["automatic_run"] / "input-dependencies.json", changed)
                return original(**kwargs)
            with mock.patch.object(manager, "_add_worktree_unlocked", side_effect=race), \
                    self.assertRaisesRegex(WorkflowError, "declaration changed"):
                self.accept()

    def test_queued_source_advance_requires_fresh_declaration(self):
        with self.fixture():
            with mock.patch.object(projects, "admit", return_value=False):
                accepted = self.accept()
            self.git("commit", "--allow-empty", "-m", "source advance")
            with self.assertRaisesRegex(WorkflowError, "captured source"):
                objectives.continue_run(self.repo / "clean", self.home, "implement", "scoped-test")
            self.declaration["base_head"] = self.git("rev-parse", "HEAD")
            result = objectives.continue_run(self.repo / "clean", self.home, "implement", "scoped-test",
                                             input_dependencies=self.declaration)
            self.assertEqual(result["run_dir"], accepted["run_dir"])
            self.assertNotEqual(result["project_root"], str(self.repo / "clean"))

    def test_expanded_write_claims_recheck_original_dirt(self):
        with self.fixture():
            accepted = self.accept()
            (self.repo / "busy/b.txt").write_text("dirty\n")
            with self.assertRaisesRegex(WorkflowError, "expanded shared"):
                inputs.check_expansion(Path(accepted["run_dir"]), [{"kind": "exact", "path": "busy/b.txt"}])
            inputs.check_expansion(Path(accepted["run_dir"]), [{"kind": "prefix", "path": "clean"}])

    def test_archive_preserves_earlier_declaration_and_detects_tampering(self):
        with self.fixture():
            accepted = self.accept()
            run = Path(accepted["run_dir"])
            key = inputs.current_digest(run)
            replacement = {**self.declaration, "claims": []}
            inputs.declare(run, replacement)
            self.assertEqual(inputs.frozen(run, key), self.declaration)
            self.assertNotEqual(inputs.current_digest(run), key)
            write(run / "input-declarations" / (key + ".json"), replacement)
            with self.assertRaisesRegex(WorkflowError, "digest changed"):
                inputs.frozen(run, key)

    def test_frozen_run_requires_fenced_replanning_for_new_inputs(self):
        with self.fixture():
            accepted = self.accept()
            run = Path(accepted["run_dir"])
            write(run / "bases/FEAT-001/example.json", {"frozen": True})
            with self.assertRaisesRegex(WorkflowError, "steering"):
                inputs.declare(run, {**self.declaration, "claims": []})
            self.assertEqual(read(run / "input-dependencies.json"), self.declaration)


if __name__ == "__main__":
    unittest.main()
