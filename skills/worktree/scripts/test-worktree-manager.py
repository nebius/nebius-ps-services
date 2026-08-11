#!/usr/bin/env python3
"""Offline real-Git tests for the local-source worktree lifecycle."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor


MODULE_PATH = Path(__file__).with_name("worktree_manager.py")
SPEC = importlib.util.spec_from_file_location("worktree_manager", MODULE_PATH)
assert SPEC and SPEC.loader
wm = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wm
SPEC.loader.exec_module(wm)
interop_state = sys.modules["worktree_interop"]
ownership_state = sys.modules["worktree_state"]
lane_state = sys.modules["task_lane_state"]


def git(*arguments: str, cwd: Path, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class WorktreeManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspace with spaces"
        self.root.mkdir()
        self.previous_codex_home = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(self.root / "codex")
        self.origin = self.root / "origin.git"
        git("init", "--bare", "-q", str(self.origin), cwd=self.root)
        self.repo = self.root / "example-monorepo"
        git("init", "-q", "-b", "main", str(self.repo), cwd=self.root)
        git("config", "user.name", "Worktree Test", cwd=self.repo)
        git("config", "user.email", "worktree@example.invalid", cwd=self.repo)
        (self.repo / "skills").mkdir()
        (self.repo / "skills" / "skill.txt").write_text("base\n", encoding="utf-8")
        (self.repo / "services" / "example").mkdir(parents=True)
        (self.repo / "services" / "example" / "service.txt").write_text(
            "base\n", encoding="utf-8"
        )
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "initial", cwd=self.repo)
        git("remote", "add", "origin", str(self.origin), cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.origin)
        git("fetch", "-q", "origin", cwd=self.repo)
        git(
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
            cwd=self.repo,
        )
        git("switch", "-qc", "abc-feature", cwd=self.repo)
        (self.repo / "source.txt").write_text("local source\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "local source commit", cwd=self.repo)
        self.source = git("rev-parse", "HEAD", cwd=self.repo)

    def tearDown(self) -> None:
        if self.previous_codex_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.previous_codex_home
        self.temporary.cleanup()

    def add(
        self, task_slug: str | None = "fix-triggers", project: str | None = None
    ) -> dict[str, object]:
        with mock.patch.object(wm.secrets, "token_hex", return_value="a7c2f9"):
            return wm.add_worktree(
                cwd=self.repo / "skills",
                project=project,
                task_slug=task_slug,
            )

    def commit_child(
        self, result: dict[str, object], relative: str, content: str
    ) -> str:
        worktree = Path(str(result["worktree"]))
        target = worktree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", f"change {relative}", cwd=worktree)
        return git("rev-parse", "HEAD", cwd=worktree)

    def integrate(
        self,
        result: dict[str, object],
        *,
        validated_head: str | None = None,
        restart: bool = False,
        cwd: Path | None = None,
        preparation_token: str | None = None,
    ) -> dict[str, object]:
        worktree = Path(str(result["worktree"]))
        return wm.integrate_worktree(
            cwd=cwd or self.repo,
            name=str(result["name"]),
            validated_head=validated_head,
            restart=restart,
            expected_source_head=git("rev-parse", "HEAD", cwd=self.repo),
            expected_child_head=git("rev-parse", "HEAD", cwd=worktree),
            preparation_token=preparation_token,
        )

    def test_parse_worktree_porcelain_z(self) -> None:
        data = (
            b"worktree /tmp/repo with spaces\0"
            b"HEAD aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\0"
            b"branch refs/heads/main\0\0"
            b"worktree /tmp/other\0"
            b"HEAD bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\0"
            b"detached\0prunable missing\0\0"
        )
        records = wm.parse_worktree_porcelain(data)
        self.assertEqual(records[0].path, "/tmp/repo with spaces")
        self.assertEqual(records[0].branch, "main")
        self.assertTrue(records[1].detached)

    def test_atomic_interop_write_fsyncs_file_then_directory(self) -> None:
        events: list[str] = []
        original_fsync = interop_state.os.fsync
        original_replace = interop_state.os.replace

        def observed_fsync(descriptor: int) -> None:
            kind = (
                "fsync-directory"
                if stat.S_ISDIR(os.fstat(descriptor).st_mode)
                else "fsync-file"
            )
            events.append(kind)
            original_fsync(descriptor)

        def observed_replace(source: str, destination: str) -> None:
            events.append("replace")
            original_replace(source, destination)

        target = (self.root / "durable-state" / "record.json").resolve()
        with (
            mock.patch.object(interop_state.os, "fsync", side_effect=observed_fsync),
            mock.patch.object(
                interop_state.os, "replace", side_effect=observed_replace
            ),
        ):
            interop_state._atomic_json(target, {"schema": 1})

        self.assertEqual(events[-3:], ["fsync-file", "replace", "fsync-directory"])

    def test_add_uses_clean_local_source_branch_and_exact_head(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        self.assertEqual(result["source_branch"], "abc-feature")
        self.assertEqual(result["source_ref"], "refs/heads/abc-feature")
        self.assertEqual(result["base_sha"], self.source)
        self.assertEqual(result["scope"], "skills")
        self.assertEqual(Path(str(result["scope_cwd"])), worktree / "skills")
        self.assertTrue((worktree / "services/example/service.txt").is_file())
        self.assertEqual(git("rev-parse", "HEAD", cwd=worktree), self.source)
        upstream = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "@{upstream}"],
            cwd=worktree,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(upstream.returncode, 0)

    def test_add_defaults_task_slug_to_current_project_basename(self) -> None:
        result = self.add(task_slug=None)
        worktree = Path(str(result["worktree"]))

        self.assertEqual(result["scope"], "skills")
        self.assertEqual(result["task_slug"], "skills")
        self.assertEqual(result["name"], "project-skills-a7c2f9")
        self.assertEqual(result["branch"], "feature/skills-a7c2f9")
        self.assertEqual(Path(str(result["scope_cwd"])), worktree / "skills")
        manifest = ownership_state.load_manifest(self.repo, "project-skills-a7c2f9")
        self.assertIsNotNone(manifest)
        assert manifest is not None
        self.assertEqual(manifest.task_slug, "skills")

    def test_add_defaults_task_slug_to_selected_project_basename(self) -> None:
        result = self.add(task_slug=None, project="services/example")
        worktree = Path(str(result["worktree"]))

        self.assertEqual(result["scope"], "services/example")
        self.assertEqual(result["task_slug"], "example")
        self.assertEqual(result["name"], "project-example-a7c2f9")
        self.assertEqual(result["branch"], "feature/example-a7c2f9")
        self.assertEqual(Path(str(result["scope_cwd"])), worktree / "services/example")

    def test_add_defaults_task_slug_to_repository_basename_at_root(self) -> None:
        with mock.patch.object(wm.secrets, "token_hex", return_value="a7c2f9"):
            result = wm.add_worktree(
                cwd=self.repo,
                project=None,
                task_slug=None,
            )

        self.assertEqual(result["scope"], ".")
        self.assertEqual(result["task_slug"], "example-monorepo")
        self.assertEqual(result["name"], "project-example-monorepo-a7c2f9")

    def test_default_task_slug_normalizes_project_basename(self) -> None:
        self.assertEqual(
            wm._resolve_task_slug(Path("Example_API.v2"), None),
            "example-api-v2",
        )
        self.assertEqual(wm._resolve_task_slug(Path("部署"), None), "work")
        self.assertEqual(
            wm._resolve_task_slug(Path("ignored"), "explicit-task"),
            "explicit-task",
        )

    def test_default_task_slug_requires_exact_reuse_for_existing_lifecycle(
        self,
    ) -> None:
        created = self.add(task_slug=None)

        with self.assertRaisesRegex(wm.WorktreeError, "already exists"):
            self.add(task_slug=None)

        reused = wm.add_worktree(
            cwd=self.repo / "skills",
            project=None,
            task_slug=None,
            reuse=str(created["name"]),
        )
        self.assertEqual(reused["status"], "reused")
        self.assertEqual(reused["task_slug"], "skills")
        self.assertEqual(
            Path(str(reused["scope_cwd"])),
            Path(str(created["worktree"])) / "skills",
        )

        with self.assertRaisesRegex(wm.WorktreeError, "does not match"):
            wm.add_worktree(
                cwd=self.repo / "skills",
                project=None,
                task_slug="different-task",
                reuse=str(created["name"]),
            )

    def test_exact_reuse_rejects_scope_replaced_by_file(self) -> None:
        created = self.add(task_slug=None, project="services/example")
        scope_cwd = Path(str(created["scope_cwd"]))
        (scope_cwd / "service.txt").unlink()
        scope_cwd.rmdir()
        scope_cwd.write_text("not a directory\n", encoding="utf-8")

        with self.assertRaisesRegex(
            wm.WorktreeError, "reused project scope is not a directory"
        ):
            wm.add_worktree(
                cwd=self.repo / "skills",
                project="services/example",
                task_slug=None,
                reuse=str(created["name"]),
            )

    def test_previous_generic_work_slug_requires_explicit_reuse_identity(self) -> None:
        created = self.add(task_slug="work")

        with self.assertRaisesRegex(wm.WorktreeError, "does not match"):
            wm.add_worktree(
                cwd=self.repo / "skills",
                project=None,
                task_slug=None,
                reuse=str(created["name"]),
            )

        reused = wm.add_worktree(
            cwd=self.repo / "skills",
            project=None,
            task_slug="work",
            reuse=str(created["name"]),
        )
        self.assertEqual(reused["status"], "reused")
        self.assertEqual(reused["task_slug"], "work")

    def test_add_requires_entire_primary_to_be_clean(self) -> None:
        cases = {
            "unstaged": lambda: (self.repo / "source.txt").write_text(
                "dirty\n", encoding="utf-8"
            ),
            "untracked": lambda: (self.repo / "untracked.txt").write_text(
                "dirty\n", encoding="utf-8"
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                mutate()
                with self.assertRaisesRegex(wm.WorktreeError, "completely clean"):
                    self.add()
                self.assertFalse(wm.state_directory(self.repo).exists())
                git("reset", "--hard", "-q", "HEAD", cwd=self.repo)
                subprocess.run(["git", "clean", "-fdq"], cwd=self.repo, check=True)
        (self.repo / "source.txt").write_text("staged\n", encoding="utf-8")
        git("add", "source.txt", cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "completely clean"):
            self.add()
        self.assertFalse(wm.state_directory(self.repo).exists())

    @unittest.skipUnless(os.name == "posix", "POSIX symlink safety")
    def test_add_rejects_symlinked_parent_without_redirected_state(self) -> None:
        parent = self.repo.parent / f"{self.repo.name}-worktrees"
        redirected = self.root / "redirected-state"
        redirected.mkdir()
        parent.symlink_to(redirected, target_is_directory=True)
        with self.assertRaisesRegex(wm.WorktreeError, "must not be a symlink"):
            self.add()
        self.assertEqual(list(redirected.iterdir()), [])

    def test_add_serializes_lifecycle_selection_and_activation(self) -> None:
        original = wm.matching_manifests
        start = threading.Barrier(2)
        counter_lock = threading.Lock()
        active = 0
        maximum_active = 0

        def observed(*args: object, **kwargs: object) -> object:
            nonlocal active, maximum_active
            with counter_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                time.sleep(0.1)
                return original(*args, **kwargs)
            finally:
                with counter_lock:
                    active -= 1

        def attempt() -> dict[str, object] | wm.WorktreeError:
            start.wait()
            try:
                return wm.add_worktree(
                    cwd=self.repo / "skills",
                    project=None,
                    task_slug="contended",
                )
            except wm.WorktreeError as error:
                return error

        with mock.patch.object(wm, "matching_manifests", side_effect=observed):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: attempt(), range(2)))

        self.assertEqual(maximum_active, 1)
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        errors = [item for item in results if isinstance(item, wm.WorktreeError)]
        self.assertEqual(len(errors), 1)
        self.assertIn("already exists", str(errors[0]))
        self.assertEqual(
            len(
                wm.matching_manifests(self.repo, scope="skills", task_slug="contended")
            ),
            1,
        )

    def test_add_rejects_default_and_detached_head(self) -> None:
        git("switch", "-q", "main", cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "non-default source branch"):
            self.add()
        self.assertFalse(wm.state_directory(self.repo).exists())
        git("switch", "-q", "abc-feature", cwd=self.repo)
        git("switch", "-q", "--detach", cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "detached HEAD"):
            self.add()
        self.assertFalse(wm.state_directory(self.repo).exists())

    def test_add_rejects_in_progress_git_operations_without_state(self) -> None:
        cases = (("MERGE_HEAD", False), ("rebase-merge", True))
        for marker, directory in cases:
            with self.subTest(marker=marker):
                marker_path = Path(
                    git(
                        "rev-parse",
                        "--path-format=absolute",
                        "--git-path",
                        marker,
                        cwd=self.repo,
                    )
                )
                if directory:
                    marker_path.mkdir()
                else:
                    marker_path.write_text(self.source + "\n", encoding="ascii")
                try:
                    with self.assertRaisesRegex(
                        wm.WorktreeError, "operation is in progress"
                    ):
                        self.add()
                    self.assertFalse(wm.state_directory(self.repo).exists())
                finally:
                    if directory:
                        marker_path.rmdir()
                    else:
                        marker_path.unlink()

    def test_project_scope_is_canonical_and_repository_relative(self) -> None:
        with self.assertRaisesRegex(wm.WorktreeError, "repository-relative"):
            self.add(project=str(self.repo / "skills"))
        self.assertFalse(wm.state_directory(self.repo).exists())

        result = self.add(task_slug=None, project="services/../skills")
        self.assertEqual(result["scope"], "skills")
        self.assertEqual(result["task_slug"], "skills")
        self.assertEqual(result["name"], "project-skills-a7c2f9")

    @unittest.skipUnless(os.name == "posix", "POSIX symlink safety")
    def test_project_scope_rejects_symlink_escape_without_state(self) -> None:
        outside = self.root / "outside-project"
        outside.mkdir()
        link = self.repo / "external-project"
        link.symlink_to(outside, target_is_directory=True)
        git("add", "external-project", cwd=self.repo)
        git("commit", "-qm", "add external project link", cwd=self.repo)

        with self.assertRaisesRegex(wm.WorktreeError, "outside the repository"):
            self.add(project="external-project")
        self.assertFalse(wm.state_directory(self.repo).exists())

    def test_cli_help_renders_for_public_and_interop_actions(self) -> None:
        actions = (
            "add",
            "inspect",
            "integration-preflight",
            "integration-commit",
            "integration-commit-review",
            "integration-preparation-abort",
            "integrate",
            "remove",
            "anchor-inspect",
            "publication-guard",
            "task-lease-acquire",
            "task-lease-resource",
            "task-lease-promote",
            "task-lease-release",
            "task-lease-inspect",
            "task-lane-ensure",
            "task-lane-generation-acquire",
            "task-lane-generation-inspect",
            "task-lane-generation-claims",
            "task-lane-generation-release",
            "task-lane-integrate",
            "task-lane-remove",
        )
        for action in actions:
            with self.subTest(action=action):
                result = subprocess.run(
                    [sys.executable, str(MODULE_PATH), action, "--help"],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)

        missing_heads = subprocess.run(
            [sys.executable, str(MODULE_PATH), "integrate", "--name", "example"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(missing_heads.returncode, 2)
        self.assertIn("--expected-source-head", missing_heads.stderr)
        self.assertIn("--expected-child-head", missing_heads.stderr)

    def test_internal_coordinators_do_not_call_public_lifecycle_actions(self) -> None:
        skills_root = Path(__file__).resolve().parents[2]
        expected_callers = {
            skills_root
            / "task-implementer"
            / "scripts"
            / "prompt_workspace_interop.py",
            skills_root
            / "sdlc-prepare-execution"
            / "scripts"
            / "sdlc_execution_interop.py",
            skills_root
            / "task-implementer"
            / "scripts"
            / "prompt_workspace_lanes.py",
        }
        callers = {
            path
            for path in skills_root.glob("*/scripts/**/*.py")
            if not path.name.startswith("test")
            and "tests" not in path.parts
            and "worktree_manager.py" in path.read_text(encoding="utf-8")
        }
        self.assertEqual(callers, expected_callers)
        public_actions = {
            "add",
            "integration-preflight",
            "integration-commit",
            "integration-commit-review",
            "integration-preparation-abort",
            "integrate",
            "remove",
        }
        allowed_actions = {
            "anchor-inspect",
            "inspect",
            "task-lease-acquire",
            "task-lease-inspect",
            "task-lease-promote",
            "task-lease-release",
            "task-lease-resource",
            "task-lane-ensure",
            "task-lane-generation-acquire",
            "task-lane-generation-inspect",
            "task-lane-generation-claims",
            "task-lane-generation-release",
            "task-lane-integrate",
            "task-lane-remove",
        }
        for caller in sorted(callers):
            tree = ast.parse(caller.read_text(encoding="utf-8"), filename=str(caller))
            actions: set[str] = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(
                    node.func, ast.Name
                ):
                    continue
                if node.func.id not in {"_call", "_managed_call"}:
                    continue
                for argument in node.args:
                    if (
                        isinstance(argument, ast.List)
                        and argument.elts
                        and isinstance(argument.elts[0], ast.Constant)
                        and isinstance(argument.elts[0].value, str)
                    ):
                        actions.add(argument.elts[0].value)
                        break
            with self.subTest(caller=caller.name):
                self.assertTrue(actions, "expected private worktree interop actions")
                self.assertTrue(actions.isdisjoint(public_actions), actions)
                self.assertEqual(actions - allowed_actions, set())

    def test_project_is_a_starting_directory_not_a_path_restriction(self) -> None:
        result = self.add()
        self.commit_child(result, "services/example/service.txt", "cross project\n")
        inspected = wm.inspect_worktree(
            cwd=Path(str(result["worktree"])),
            name=str(result["name"]),
            require_clean=True,
        )
        self.assertIn("services/example/service.txt", inspected["branch_changed_paths"])

    def test_publication_guard_blocks_managed_child_only(self) -> None:
        result = self.add()
        with self.assertRaisesRegex(wm.WorktreeError, "must not push"):
            wm.publication_guard(cwd=Path(str(result["worktree"])), action="push")
        self.commit_child(result, "skills/skill.txt", "candidate\n")
        prepared = self.integrate(result)
        with self.assertRaisesRegex(wm.WorktreeError, "must not create-pr"):
            wm.publication_guard(
                cwd=Path(str(prepared["candidate_worktree"])), action="create-pr"
            )
        allowed = wm.publication_guard(cwd=self.repo, action="create-pr")
        self.assertEqual(allowed["mode"], "source")
        git(
            "worktree",
            "remove",
            str(prepared["candidate_worktree"]),
            cwd=self.repo,
        )
        reservation = wm.load_reservation(self.repo, str(result["name"]))
        assert reservation is not None
        git("switch", "-q", str(reservation["integration_branch"]), cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "publication is unsafe"):
            wm.publication_guard(cwd=self.repo, action="push")

    def test_publication_guard_allows_only_truly_unmanaged_manual_worktree(
        self,
    ) -> None:
        manual = self.root / "manual-checkout"
        git(
            "worktree",
            "add",
            "-q",
            "-b",
            "manual-publication",
            str(manual),
            self.source,
            cwd=self.repo,
        )
        allowed = wm.publication_guard(cwd=manual, action="push")
        self.assertEqual(allowed["mode"], "unmanaged")
        self.assertFalse(wm.state_directory(self.repo).exists())

        private_parent = self.repo.parent / f"{self.repo.name}-worktrees"
        private_parent.mkdir()
        unclaimed = private_parent / "manual-private"
        git(
            "worktree",
            "add",
            "-q",
            "-b",
            "manual-private",
            str(unclaimed),
            self.source,
            cwd=self.repo,
        )
        with self.assertRaisesRegex(wm.WorktreeError, "unclaimed checkout"):
            wm.publication_guard(cwd=unclaimed, action="push")

    def test_integrate_records_two_parent_merge_and_promotes_source(self) -> None:
        result = self.add()
        child = self.commit_child(result, "skills/skill.txt", "child\n")
        prepared = self.integrate(result)
        self.assertEqual(prepared["status"], "validation-required")
        candidate = str(prepared["candidate_head"])
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.source)
        integrated = self.integrate(result, validated_head=candidate)
        self.assertEqual(integrated["status"], "integrated")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), candidate)
        self.assertEqual(
            git("rev-list", "--parents", "-n", "1", candidate, cwd=self.repo).split(),
            [candidate, self.source, child],
        )
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")

    def test_integration_preflight_classifies_child_then_source_commits(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        (worktree / "skills/skill.txt").write_text("unstaged child\n", encoding="utf-8")
        (worktree / "staged.txt").write_text("staged child\n", encoding="utf-8")
        git("add", "staged.txt", cwd=worktree)
        git("mv", "source.txt", "renamed-source.txt", cwd=worktree)
        (worktree / "services/example/service.txt").unlink()
        (self.repo / "source-dirty.txt").write_text("dirty\n", encoding="utf-8")
        (self.repo / "source-staged.txt").write_text(
            "staged source\n", encoding="utf-8"
        )
        git("add", "source-staged.txt", cwd=self.repo)
        preflight = wm.integration_preflight(
            cwd=self.repo / "skills", name=str(result["name"])
        )
        self.assertEqual(preflight["status"], "commit-required")
        self.assertEqual(preflight["commit_order"], ["child", "source"])
        self.assertTrue(
            {
                "dirty.txt",
                "skills/skill.txt",
                "staged.txt",
                "source.txt",
                "renamed-source.txt",
                "services/example/service.txt",
            }.issubset(set(preflight["child_dirty_paths"]))
        )
        self.assertEqual(
            set(preflight["source_dirty_paths"]),
            {"source-dirty.txt", "source-staged.txt"},
        )
        self.assertIsNone(wm.load_reservation(self.repo, str(result["name"])))

        git("add", "-A", cwd=worktree)
        child_tree = git("write-tree", cwd=worktree)
        child_commit = wm.integration_commit(
            cwd=self.repo,
            name=str(result["name"]),
            target="child",
            expected_head=str(preflight["child_head"]),
            expected_tree=child_tree,
            message="Prepare child integration",
        )
        self.assertEqual(child_commit["status"], "committed")
        child = str(child_commit["commit_head"])
        git("add", "-A", cwd=self.repo)
        source_tree = git("write-tree", cwd=self.repo)
        source_commit = wm.integration_commit(
            cwd=self.repo,
            name=str(result["name"]),
            target="source",
            expected_head=str(preflight["source_head"]),
            expected_tree=source_tree,
            message="Prepare source integration",
            preparation_token=str(child_commit["preparation_token"]),
        )
        self.assertEqual(source_commit["status"], "committed")
        source = str(source_commit["commit_head"])
        ready = wm.integration_preflight(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(ready["status"], "ready-clean")
        self.assertEqual(ready["source_head"], source)
        self.assertEqual(ready["child_head"], child)
        candidate = self.integrate(
            result, preparation_token=str(ready["preparation_token"])
        )
        self.assertEqual(candidate["source_head"], source)
        self.assertEqual(candidate["child_head"], child)
        self.assertIsNone(wm.load_preparation(self.repo, str(result["name"])))

    def test_low_level_integrate_remains_clean_only(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        with self.assertRaisesRegex(wm.WorktreeError, "no committed work"):
            self.integrate(result)
        (worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(wm.WorktreeError, "completely clean"):
            self.integrate(result)

    def test_preparation_claim_blocks_lease_publication_and_other_integration(
        self,
    ) -> None:
        first = self.add("first")
        with mock.patch.object(wm.secrets, "token_hex", return_value="b8d3e0"):
            second = wm.add_worktree(
                cwd=self.repo / "skills", project=None, task_slug="second"
            )
        first_worktree = Path(str(first["worktree"]))
        (first_worktree / "first.txt").write_text("first\n", encoding="utf-8")
        preflight = wm.integration_preflight(
            cwd=self.repo, name=str(first["name"])
        )
        git("add", "-A", cwd=first_worktree)
        committed = wm.integration_commit(
            cwd=self.repo,
            name=str(first["name"]),
            target="child",
            expected_head=str(preflight["child_head"]),
            expected_tree=git("write-tree", cwd=first_worktree),
            message="Prepare first child",
        )
        with self.assertRaisesRegex(wm.WorktreeError, "commit preparation"):
            wm.task_lease_acquire(
                cwd=first_worktree / "skills",
                workspace=self.root / "workspace",
                run_id="run-preparation-race",
                task_scope="skills",
                initial_head=str(committed["commit_head"]),
                owner_kind="task-implementer",
            )
        with self.assertRaisesRegex(wm.WorktreeError, "must not create-pr"):
            wm.publication_guard(cwd=self.repo, action="create-pr")

        self.commit_child(second, "second.txt", "second\n")
        with self.assertRaisesRegex(wm.WorktreeError, "preparing commits"):
            self.integrate(second)
        aborted = wm.integration_preparation_abort(
            cwd=self.repo,
            name=str(first["name"]),
            preparation_token=str(committed["preparation_token"]),
        )
        self.assertEqual(aborted["status"], "aborted")
        self.assertEqual(
            wm.publication_guard(cwd=self.repo, action="create-pr")["status"],
            "allowed",
        )

    def test_preparation_blocks_publication_after_source_branch_relocation(
        self,
    ) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        preflight = wm.integration_preflight(cwd=self.repo, name=str(result["name"]))
        git("add", "-A", cwd=worktree)
        committed = wm.integration_commit(
            cwd=self.repo,
            name=str(result["name"]),
            target="child",
            expected_head=str(preflight["child_head"]),
            expected_tree=git("write-tree", cwd=worktree),
            message="Prepare relocated source",
        )
        self.assertEqual(committed["status"], "committed")
        git("switch", "-qc", "other-primary", cwd=self.repo)
        relocated = self.root / "relocated-source"
        git(
            "worktree",
            "add",
            "-q",
            str(relocated),
            str(result["source_branch"]),
            cwd=self.repo,
        )
        with self.assertRaisesRegex(wm.WorktreeError, "partially matches"):
            wm.publication_guard(cwd=relocated, action="push")

    def test_reservation_write_preparation_delete_failure_reconciles(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        preflight = wm.integration_preflight(cwd=self.repo, name=str(result["name"]))
        git("add", "-A", cwd=worktree)
        committed = wm.integration_commit(
            cwd=self.repo,
            name=str(result["name"]),
            target="child",
            expected_head=str(preflight["child_head"]),
            expected_tree=git("write-tree", cwd=worktree),
            message="Prepare crash recovery",
        )
        token = str(committed["preparation_token"])
        with mock.patch.object(
            interop_state,
            "_delete_preparation",
            side_effect=wm.InteropError("injected preparation delete failure"),
        ):
            with self.assertRaisesRegex(
                wm.WorktreeError, "injected preparation delete failure"
            ):
                self.integrate(result, preparation_token=token)
        self.assertIsNotNone(wm.load_reservation(self.repo, str(result["name"])))
        self.assertIsNotNone(wm.load_preparation(self.repo, str(result["name"])))
        resumed = self.integrate(result, preparation_token=token)
        self.assertEqual(resumed["status"], "validation-required")
        self.assertIsNone(wm.load_preparation(self.repo, str(result["name"])))
        integrated = self.integrate(
            result,
            validated_head=str(resumed["candidate_head"]),
        )
        self.assertEqual(integrated["status"], "integrated")
        self.assertEqual(
            wm.publication_guard(cwd=self.repo, action="create-pr")["status"],
            "allowed",
        )

    def test_task_lease_rolls_back_when_outer_changes_during_acquisition(self) -> None:
        result = self.add()
        child = self.commit_child(result, "child.txt", "child\n")
        worktree = Path(str(result["worktree"]))
        original_acquire = wm.acquire_task_lease

        def racing_acquire(*args: object, **kwargs: object) -> dict[str, object]:
            (worktree / "racing.txt").write_text("racing\n", encoding="utf-8")
            return original_acquire(*args, **kwargs)

        with mock.patch.object(wm, "acquire_task_lease", side_effect=racing_acquire):
            with self.assertRaisesRegex(wm.WorktreeError, "changed during"):
                wm.task_lease_acquire(
                    cwd=worktree / "skills",
                    workspace=self.root / "workspace-race",
                    run_id="run-lease-race",
                    task_scope="skills",
                    initial_head=child,
                    owner_kind="task-implementer",
                )
        self.assertIsNone(wm.load_lease(self.repo, str(result["name"])))
        manifest = wm.load_manifest(self.repo, str(result["name"]))
        assert manifest is not None
        self.assertEqual(manifest.lease_state, "none")

    def test_other_reservation_wins_race_before_preparatory_commit(self) -> None:
        first = self.add("first")
        with mock.patch.object(wm.secrets, "token_hex", return_value="b8d3e0"):
            second = wm.add_worktree(
                cwd=self.repo / "skills", project=None, task_slug="second"
            )
        first_worktree = Path(str(first["worktree"]))
        (first_worktree / "first.txt").write_text("first\n", encoding="utf-8")
        preflight = wm.integration_preflight(
            cwd=self.repo, name=str(first["name"])
        )
        self.assertEqual(preflight["status"], "commit-required")
        self.commit_child(second, "second.txt", "second\n")
        self.integrate(second)
        git("add", "-A", cwd=first_worktree)
        before = git("rev-parse", "HEAD", cwd=first_worktree)
        with self.assertRaisesRegex(wm.WorktreeError, "active integration"):
            wm.integration_commit(
                cwd=self.repo,
                name=str(first["name"]),
                target="child",
                expected_head=str(preflight["child_head"]),
                expected_tree=git("write-tree", cwd=first_worktree),
                message="Prepare first child",
            )
        self.assertEqual(git("rev-parse", "HEAD", cwd=first_worktree), before)
        self.assertTrue(wm.status_paths(first_worktree))

    def test_preflight_blocks_orphan_candidate_resources_before_commit(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        branch = wm._integration_branch(str(result["name"]))
        git("branch", branch, self.source, cwd=self.repo)
        branch_blocked = wm.integration_preflight(
            cwd=self.repo, name=str(result["name"])
        )
        self.assertEqual(branch_blocked["status"], "blocked")
        self.assertIn(
            "an orphan integration candidate branch already exists",
            branch_blocked["blockers"],
        )
        git("branch", "-D", branch, cwd=self.repo)

        candidate_path = wm._integration_path(self.repo, str(result["name"]))
        candidate_path.mkdir(parents=True)
        path_blocked = wm.integration_preflight(
            cwd=self.repo, name=str(result["name"])
        )
        self.assertEqual(path_blocked["status"], "blocked")
        self.assertIn(
            "an orphan integration candidate path already exists",
            path_blocked["blockers"],
        )

    def test_hook_modified_commit_requires_actual_tree_review(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "reviewed.txt").write_text("reviewed\n", encoding="utf-8")
        preflight = wm.integration_preflight(
            cwd=self.repo, name=str(result["name"])
        )
        git("add", "-A", cwd=worktree)
        reviewed_tree = git("write-tree", cwd=worktree)
        hook_value = git("rev-parse", "--git-path", "hooks/pre-commit", cwd=worktree)
        hook = Path(hook_value)
        if not hook.is_absolute():
            hook = worktree / hook
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "#!/bin/sh\nprintf 'hook-added\\n' > hook-added.txt\ngit add hook-added.txt\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        committed = wm.integration_commit(
            cwd=self.repo,
            name=str(result["name"]),
            target="child",
            expected_head=str(preflight["child_head"]),
            expected_tree=reviewed_tree,
            message="Prepare reviewed child",
        )
        self.assertEqual(committed["status"], "review-required")
        self.assertFalse(committed["tree_verified"])
        blocked = wm.integration_preflight(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn(
            "preparatory child commit requires actual-commit review",
            blocked["blockers"],
        )
        reviewed = wm.integration_commit_review(
            cwd=self.repo,
            name=str(result["name"]),
            target="child",
            preparation_token=str(committed["preparation_token"]),
            commit_head=str(committed["commit_head"]),
            commit_tree=str(committed["commit_tree"]),
        )
        self.assertEqual(reviewed["status"], "verified")
        ready = wm.integration_preflight(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(ready["status"], "ready-clean")

    def test_preflight_rejects_missing_participating_lease_before_source_commit(
        self,
    ) -> None:
        result = self.add()
        child = self.commit_child(result, "child.txt", "child\n")
        worktree = Path(str(result["worktree"]))
        lease = wm.task_lease_acquire(
            cwd=worktree / "skills",
            workspace=self.root / "workspace",
            run_id="run-missing-receipt",
            task_scope="skills",
            initial_head=child,
            owner_kind="task-implementer",
        )
        self.assertEqual(lease["status"], "acquired")
        lease_path = (
            wm.state_directory(self.repo)
            / "leases"
            / f"{result['name']}.json"
        )
        lease_path.unlink()
        (self.repo / "source-dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(wm.WorktreeError, "participating task lease is missing"):
            wm.integration_preflight(cwd=self.repo, name=str(result["name"]))

    def test_integration_requires_primary_checkout_before_mutation(self) -> None:
        result = self.add()
        child = self.commit_child(result, "skills/skill.txt", "child\n")
        worktree = Path(str(result["worktree"]))
        with self.assertRaisesRegex(wm.WorktreeError, "primary checkout"):
            wm.integration_preflight(cwd=worktree, name=str(result["name"]))
        with self.assertRaisesRegex(wm.WorktreeError, "primary checkout"):
            self.integrate(result, cwd=worktree)
        self.assertEqual(git("rev-parse", "HEAD", cwd=worktree), child)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.source)
        self.assertIsNone(wm.load_reservation(self.repo, str(result["name"])))

    def test_expected_heads_reject_post_preflight_source_movement(self) -> None:
        result = self.add()
        child = self.commit_child(result, "skills/skill.txt", "child\n")
        preflight = wm.integration_preflight(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(preflight["status"], "ready-clean")
        with self.assertRaisesRegex(wm.WorktreeError, "integration-preflight"):
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                validated_head=None,
                restart=False,
            )
        self.assertIsNone(wm.load_reservation(self.repo, str(result["name"])))
        (self.repo / "later.txt").write_text("later\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "move source after preflight", cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "changed after"):
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                validated_head=None,
                restart=False,
                expected_source_head=str(preflight["source_head"]),
                expected_child_head=child,
            )
        self.assertIsNone(wm.load_reservation(self.repo, str(result["name"])))

    def test_expected_heads_reject_post_preflight_child_movement(self) -> None:
        result = self.add()
        child = self.commit_child(result, "skills/skill.txt", "child\n")
        preflight = wm.integration_preflight(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(preflight["status"], "ready-clean")
        self.commit_child(result, "later-child.txt", "later\n")
        with self.assertRaisesRegex(wm.WorktreeError, "child HEAD changed after"):
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                validated_head=None,
                restart=False,
                expected_source_head=str(preflight["source_head"]),
                expected_child_head=child,
            )
        self.assertIsNone(wm.load_reservation(self.repo, str(result["name"])))

    def test_conflict_is_retained_and_resumable(self) -> None:
        result = self.add()
        child = self.commit_child(result, "skills/skill.txt", "child\n")
        (self.repo / "skills/skill.txt").write_text("source\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "source overlap", cwd=self.repo)
        source_start = git("rev-parse", "HEAD", cwd=self.repo)
        conflicted = self.integrate(result)
        self.assertEqual(conflicted["status"], "conflict")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), source_start)
        recovery = Path(str(conflicted["recovery_worktree"]))
        (recovery / "skills/skill.txt").write_text("resolved\n", encoding="utf-8")
        git("add", "-A", cwd=recovery)
        ready = self.integrate(result)
        self.assertEqual(ready["status"], "validation-required")
        candidate = str(ready["candidate_head"])
        self.assertEqual(
            git("rev-list", "--parents", "-n", "1", candidate, cwd=self.repo).split(),
            [candidate, source_start, child],
        )

    def test_source_movement_requires_explicit_restart(self) -> None:
        result = self.add()
        self.commit_child(result, "skills/skill.txt", "child\n")
        prepared = self.integrate(result)
        (self.repo / "later.txt").write_text("later\n", encoding="utf-8")
        blocked = wm.integration_preflight(
            cwd=self.repo, name=str(result["name"]), restart=True
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn(
            "source is dirty during an active integration attempt",
            blocked["blockers"],
        )
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "source moved", cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "--restart"):
            self.integrate(result, validated_head=str(prepared["candidate_head"]))
        restarted = self.integrate(
            result,
            restart=True,
        )
        self.assertEqual(restarted["status"], "validation-required")
        self.assertNotEqual(restarted["candidate_head"], prepared["candidate_head"])

    def test_restart_retains_unexpected_candidate_branch_advance(self) -> None:
        result = self.add()
        self.commit_child(result, "skills/skill.txt", "child\n")
        prepared = self.integrate(result)
        candidate_path = Path(str(prepared["candidate_worktree"]))
        (candidate_path / "unexpected.txt").write_text(
            "preserve me\n", encoding="utf-8"
        )
        git("add", "-A", cwd=candidate_path)
        git("commit", "-qm", "unexpected candidate advance", cwd=candidate_path)
        unexpected_head = git("rev-parse", "HEAD", cwd=candidate_path)

        with self.assertRaisesRegex(wm.WorktreeError, "advanced"):
            self.integrate(result, restart=True)

        self.assertTrue(candidate_path.is_dir())
        self.assertEqual(git("rev-parse", "HEAD", cwd=candidate_path), unexpected_head)

    def test_integrate_rejects_clean_candidate_advance_after_validation(self) -> None:
        result = self.add()
        self.commit_child(result, "skills/skill.txt", "child\n")
        prepared = self.integrate(result)
        candidate = str(prepared["candidate_head"])
        candidate_path = Path(str(prepared["candidate_worktree"]))
        (candidate_path / "unvalidated.txt").write_text(
            "not validated\n", encoding="utf-8"
        )
        git("add", "-A", cwd=candidate_path)
        git("commit", "-qm", "unvalidated candidate advance", cwd=candidate_path)

        with self.assertRaisesRegex(wm.WorktreeError, "exact verification"):
            self.integrate(result, validated_head=candidate)

        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.source)

    def test_one_source_branch_has_one_active_integration(self) -> None:
        first = self.add("first")
        with mock.patch.object(wm.secrets, "token_hex", return_value="b8d3e0"):
            second = wm.add_worktree(
                cwd=self.repo / "services/example",
                project=None,
                task_slug="second",
            )
        self.commit_child(first, "skills/skill.txt", "first\n")
        self.commit_child(second, "services/example/service.txt", "second\n")
        self.integrate(first)
        with self.assertRaisesRegex(wm.WorktreeError, "active integration"):
            self.integrate(second)

    def test_task_lease_blocks_integration_until_release(self) -> None:
        result = self.add()
        child = self.commit_child(result, "skills/skill.txt", "child\n")
        worktree = Path(str(result["worktree"]))
        anchor = wm.inspect_managed_anchor(cwd=worktree / "skills")
        lease = wm.task_lease_acquire(
            cwd=worktree / "skills",
            workspace=self.root / "workspace",
            run_id="run-1",
            task_scope="skills",
            initial_head=child,
            owner_kind="task-implementer",
        )
        with self.assertRaisesRegex(wm.WorktreeError, "still owns"):
            self.integrate(result)
        wm.task_lease_promote(
            cwd=worktree,
            name=str(anchor["name"]),
            lease_id=str(lease["token"]),
            promoted_head=child,
            expected_head=child,
            owner_kind="task-implementer",
        )
        with self.assertRaisesRegex(wm.InteropError, "compare-and-set"):
            wm.update_task_lease(
                self.repo,
                name=str(anchor["name"]),
                token=str(lease["token"]),
                promoted_head="f" * 40,
                expected_previous_head="e" * 40,
                owner_kind="task-implementer",
            )
        inspected = wm.task_lease_inspect(
            cwd=worktree,
            name=str(anchor["name"]),
            lease_id=str(lease["token"]),
            owner_kind="task-implementer",
        )
        self.assertEqual(inspected["state"], "active")
        self.assertEqual(inspected["promoted_head"], child)
        internal_path = self.root / "retired-task-resource"
        internal_branch = "codex/ti-run-1/worker"
        wm.task_lease_resource(
            cwd=worktree,
            name=str(anchor["name"]),
            lease_id=str(lease["token"]),
            kind="worker",
            path=internal_path,
            branch=internal_branch,
            state="planned",
            owner_kind="task-implementer",
        )
        wm.task_lease_resource(
            cwd=worktree,
            name=str(anchor["name"]),
            lease_id=str(lease["token"]),
            kind="worker",
            path=internal_path,
            branch=internal_branch,
            state="absent",
            owner_kind="task-implementer",
        )
        released = wm.task_lease_release(
            cwd=worktree,
            name=str(anchor["name"]),
            lease_id=str(lease["token"]),
            promoted_head=child,
            owner_kind="task-implementer",
        )
        self.assertEqual(released["state"], "released")
        receipt = wm.task_lease_inspect(
            cwd=worktree,
            name=str(anchor["name"]),
            lease_id=str(lease["token"]),
            owner_kind="task-implementer",
        )
        self.assertEqual(receipt["state"], "released")
        replay = wm.task_lease_release(
            cwd=worktree,
            name=str(anchor["name"]),
            lease_id=str(lease["token"]),
            promoted_head=child,
            owner_kind="task-implementer",
        )
        self.assertEqual(replay["status"], "already-released")
        lease_path = wm.state_directory(self.repo) / "leases" / f"{anchor['name']}.json"
        exact_receipt = json.loads(lease_path.read_text(encoding="utf-8"))
        tampered_receipt = dict(exact_receipt)
        tampered_receipt["worktree"] = str(self.root / "different-outer")
        lease_path.write_text(
            json.dumps(tampered_receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(wm.WorktreeError, "outer identity"):
            self.integrate(result)
        with self.assertRaisesRegex(wm.WorktreeError, "outer identity"):
            wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        lease_path.write_text(
            json.dumps(exact_receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        git("branch", internal_branch, child, cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "resources reappeared"):
            self.integrate(result)
        with self.assertRaisesRegex(wm.WorktreeError, "resources reappeared"):
            wm.task_lease_inspect(
                cwd=worktree,
                name=str(anchor["name"]),
                lease_id=str(lease["token"]),
                owner_kind="task-implementer",
            )
        git("branch", "-D", internal_branch, cwd=self.repo)
        (worktree / "after-release.txt").write_text("dirty\n", encoding="utf-8")
        blocked = wm.integration_preflight(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn(
            "nested workflow ownership binds the child to an exact head",
            blocked["blockers"],
        )
        (worktree / "after-release.txt").unlink()
        with self.assertRaisesRegex(wm.WorktreeError, "terminal released"):
            wm.task_lease_acquire(
                cwd=worktree / "skills",
                workspace=self.root / "workspace",
                run_id="run-1",
                task_scope="skills",
                initial_head=child,
                owner_kind="task-implementer",
            )
        ready = self.integrate(result)
        self.assertEqual(ready["status"], "validation-required")

    def test_remove_revalidates_released_resources_before_cleanup(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        lease = wm.task_lease_acquire(
            cwd=worktree / "skills",
            workspace=self.root / "workspace",
            run_id="run-resurrection",
            task_scope="skills",
            initial_head=self.source,
            owner_kind="task-implementer",
        )
        resource = self.root / "retired-resource"
        branch = "codex/ti-run-resurrection/worker"
        for state in ("planned", "absent"):
            wm.task_lease_resource(
                cwd=worktree,
                name=str(result["name"]),
                lease_id=str(lease["token"]),
                kind="worker",
                path=resource,
                branch=branch,
                state=state,
                owner_kind="task-implementer",
            )
        wm.task_lease_promote(
            cwd=worktree,
            name=str(result["name"]),
            lease_id=str(lease["token"]),
            promoted_head=self.source,
            expected_head=self.source,
            owner_kind="task-implementer",
        )
        wm.task_lease_release(
            cwd=worktree,
            name=str(result["name"]),
            lease_id=str(lease["token"]),
            promoted_head=self.source,
            owner_kind="task-implementer",
        )
        resource.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(wm.WorktreeError, "resources reappeared"):
            wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertTrue(worktree.is_dir())
        resource.unlink()
        removed = wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(removed["status"], "removed")

    def test_remove_deletes_terminal_lease_receipt(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        lease = wm.task_lease_acquire(
            cwd=worktree / "skills",
            workspace=self.root / "workspace",
            run_id="run-unused",
            task_scope="skills",
            initial_head=self.source,
            owner_kind="task-implementer",
        )
        wm.task_lease_promote(
            cwd=worktree,
            name=str(result["name"]),
            lease_id=str(lease["token"]),
            promoted_head=self.source,
            expected_head=self.source,
            owner_kind="task-implementer",
        )
        wm.task_lease_release(
            cwd=worktree,
            name=str(result["name"]),
            lease_id=str(lease["token"]),
            promoted_head=self.source,
            owner_kind="task-implementer",
        )
        lease_path = wm.state_directory(self.repo) / "leases" / f"{result['name']}.json"
        self.assertTrue(lease_path.is_file())
        with (
            mock.patch.object(
                interop_state,
                "fsync_directory",
                wraps=interop_state.fsync_directory,
            ) as interop_sync,
            mock.patch.object(
                ownership_state,
                "fsync_directory",
                wraps=ownership_state.fsync_directory,
            ) as ownership_sync,
        ):
            wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertFalse(lease_path.exists())
        interop_paths = {call.args[0] for call in interop_sync.call_args_list}
        state = wm.state_directory(self.repo).resolve()
        self.assertIn(state / "lease-removals", interop_paths)
        self.assertIn(state / "leases", interop_paths)
        ownership_sync.assert_any_call(state)

    def test_remove_retry_deletes_terminal_receipt_after_git_cleanup(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        lease = wm.task_lease_acquire(
            cwd=worktree / "skills",
            workspace=self.root / "workspace",
            run_id="run-cleanup-crash",
            task_scope="skills",
            initial_head=self.source,
            owner_kind="task-implementer",
        )
        wm.task_lease_promote(
            cwd=worktree,
            name=str(result["name"]),
            lease_id=str(lease["token"]),
            promoted_head=self.source,
            expected_head=self.source,
            owner_kind="task-implementer",
        )
        wm.task_lease_release(
            cwd=worktree,
            name=str(result["name"]),
            lease_id=str(lease["token"]),
            promoted_head=self.source,
            owner_kind="task-implementer",
        )
        lease_path = wm.state_directory(self.repo) / "leases" / f"{result['name']}.json"
        intent_path = (
            wm.state_directory(self.repo) / "lease-removals" / f"{result['name']}.json"
        )
        exact_receipt = json.loads(lease_path.read_text(encoding="utf-8"))
        with (
            mock.patch.object(
                wm,
                "delete_released_lease",
                side_effect=wm.InteropError("simulated receipt deletion crash"),
            ),
            self.assertRaisesRegex(wm.WorktreeError, "simulated receipt deletion"),
        ):
            wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertFalse(worktree.exists())
        self.assertTrue(lease_path.is_file())
        self.assertTrue(intent_path.is_file())

        changed_receipt = dict(exact_receipt)
        changed_receipt["promoted_head"] = "f" * 40
        changed_receipt["promotion_heads"] = [self.source, "f" * 40]
        lease_path.write_text(
            json.dumps(changed_receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(wm.WorktreeError, "removal intent"):
            wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        lease_path.write_text(
            json.dumps(exact_receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with (
            mock.patch.object(
                wm,
                "delete_manifest",
                side_effect=wm.StateError("simulated manifest deletion crash"),
            ),
            self.assertRaisesRegex(wm.WorktreeError, "simulated manifest deletion"),
        ):
            wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertFalse(lease_path.exists())
        self.assertTrue(intent_path.is_file())
        removed = wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(removed["status"], "already-removed")
        self.assertFalse(lease_path.exists())
        self.assertFalse(intent_path.exists())

    def test_missing_active_lease_blocks_integration_and_removal(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        wm.task_lease_acquire(
            cwd=worktree / "skills",
            workspace=self.root / "workspace",
            run_id="run-missing",
            task_scope="skills",
            initial_head=self.source,
            owner_kind="task-implementer",
        )
        lease_path = wm.state_directory(self.repo) / "leases" / f"{result['name']}.json"
        lease_path.unlink()
        with self.assertRaisesRegex(wm.WorktreeError, "lease is missing"):
            self.integrate(result)
        with self.assertRaisesRegex(wm.WorktreeError, "lease is missing"):
            wm.remove_worktree(cwd=self.repo, name=str(result["name"]))

    def test_remove_accepts_unused_or_integrated_child(self) -> None:
        unused = self.add("unused")
        removed = wm.remove_worktree(cwd=self.repo, name=str(unused["name"]))
        self.assertEqual(removed["status"], "removed")

        with mock.patch.object(wm.secrets, "token_hex", return_value="b8d3e0"):
            used = wm.add_worktree(
                cwd=self.repo / "skills", project=None, task_slug="used"
            )
        self.commit_child(used, "skills/skill.txt", "used\n")
        ready = self.integrate(used)
        self.integrate(used, validated_head=str(ready["candidate_head"]))
        removed = wm.remove_worktree(cwd=self.repo, name=str(used["name"]))
        self.assertEqual(removed["status"], "removed")

    def test_task_lane_is_idempotent_and_excludes_dirty_source_state(self) -> None:
        dirty = self.repo / "uncommitted.txt"
        dirty.write_text("source-only\n", encoding="utf-8")

        created = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        reused = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        reused_from_lane = wm.task_lane_ensure(
            cwd=Path(str(created["scope_cwd"])), project=None
        )

        self.assertEqual(created["status"], "created")
        self.assertEqual(reused["status"], "reused")
        self.assertEqual(created["lane_id"], reused["lane_id"])
        self.assertEqual(created["branch"], reused["branch"])
        self.assertEqual(created["worktree"], reused["worktree"])
        self.assertEqual(created["lane_id"], reused_from_lane["lane_id"])
        self.assertEqual(created["lane_head"], self.source)
        self.assertFalse(Path(str(created["worktree"]), "uncommitted.txt").exists())
        self.assertTrue(dirty.is_file())

    def test_task_lane_rejects_ordinary_task_lease_without_state_mutation(self) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        manifest_path = wm.manifest_path(self.repo, str(lane["name"]))
        manifest_before = manifest_path.read_bytes()
        lease_path = (
            wm.state_directory(self.repo) / "leases" / f"{lane['name']}.json"
        )

        with self.assertRaisesRegex(
            wm.WorktreeError, "cannot be acquired from a Task Implementer"
        ):
            wm.task_lease_acquire(
                cwd=Path(str(lane["scope_cwd"])),
                workspace=self.root / "agentic-run",
                run_id="agentic-run",
                task_scope="skills",
                initial_head=str(lane["lane_head"]),
                owner_kind="agentic-sdlc",
            )

        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertFalse(lease_path.exists())

    def test_task_lane_missing_branch_identity_fails_closed_without_lease(self) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        manifest_path = wm.manifest_path(self.repo, str(lane["name"]))
        manifest_before = manifest_path.read_bytes()
        lease_path = (
            wm.state_directory(self.repo) / "leases" / f"{lane['name']}.json"
        )
        git(
            "config",
            "--local",
            "--unset-all",
            wm._task_lane_config_key(str(lane["branch"]), "lane_id"),
            cwd=self.repo,
        )

        with self.assertRaisesRegex(
            wm.WorktreeError, "task lane branch metadata is incomplete"
        ):
            wm.task_lease_acquire(
                cwd=Path(str(lane["scope_cwd"])),
                workspace=self.root / "agentic-run",
                run_id="agentic-run",
                task_scope="skills",
                initial_head=str(lane["lane_head"]),
                owner_kind="agentic-sdlc",
            )

        for field in ("source_ref", "incarnation"):
            git(
                "config",
                "--local",
                "--unset-all",
                wm._task_lane_config_key(str(lane["branch"]), field),
                cwd=self.repo,
            )
        with self.assertRaisesRegex(
            wm.WorktreeError, "metadata is missing from a live lane"
        ):
            wm.inspect_managed_anchor(cwd=Path(str(lane["scope_cwd"])))

        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertFalse(lease_path.exists())

    def test_task_lane_creation_recovery_cleans_and_reincarnates(self) -> None:
        original_write = wm.write_lane
        failed = False

        def fail_first_idle(primary: Path, value: dict[str, object]) -> Path:
            nonlocal failed
            if value["state"] == "idle" and not failed:
                failed = True
                raise wm.TaskLaneStateError("simulated lane checkpoint interruption")
            return original_write(primary, value)

        with (
            mock.patch.object(wm, "write_lane", side_effect=fail_first_idle),
            self.assertRaisesRegex(
                wm.WorktreeError, "simulated lane checkpoint interruption"
            ),
        ):
            wm.task_lane_ensure(cwd=self.repo / "skills", project=None)

        recovered = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        self.assertEqual(recovered["status"], "created")
        self.assertEqual(recovered["incarnation"], 2)
        self.assertEqual(recovered["lane_head"], self.source)

    def test_task_lane_generation_stays_monotonic_after_reincarnation(self) -> None:
        def complete_no_change_generation(
            lane: dict[str, object], *, run_id: str, generation: int
        ) -> None:
            lane_root = Path(str(lane["worktree"]))
            initial = str(lane["lane_head"])
            acquired = wm.task_lane_generation_acquire(
                cwd=Path(str(lane["scope_cwd"])),
                workspace=self.root / f"{run_id}.json",
                run_id=run_id,
                task_scope="skills",
                initial_head=initial,
            )
            self.assertEqual(acquired["generation"], generation)
            wm.task_lease_promote(
                cwd=lane_root,
                name=str(lane["name"]),
                lease_id=str(acquired["token"]),
                promoted_head=initial,
                expected_head=initial,
                owner_kind="task-implementer",
            )
            wm.task_lane_generation_release(
                cwd=lane_root,
                name=str(lane["name"]),
                generation=generation,
                lease_id=str(acquired["token"]),
                promoted_head=initial,
            )
            integrated = wm.task_lane_integrate(
                cwd=self.repo,
                lane_id=str(lane["lane_id"]),
                validated_head=None,
                restart=False,
            )
            self.assertEqual(integrated["integrated_generations"], [generation])

        first = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        complete_no_change_generation(first, run_id="run-first-life", generation=1)
        wm.task_lane_remove(cwd=self.repo, lane_id=str(first["lane_id"]))

        second = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        self.assertEqual(second["incarnation"], int(first["incarnation"]) + 1)
        complete_no_change_generation(second, run_id="run-second-life", generation=2)

    def test_task_lane_state_rejects_inconsistent_lifecycle_shape(self) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        path = lane_state.lane_path(self.repo, str(lane["lane_id"]))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["latest_generation"] = 1
        payload["pending_generations"] = [1]
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(
            wm.TaskLaneStateError, "idle task lane retains lifecycle work"
        ):
            wm.load_lane(self.repo, str(lane["lane_id"]))

    def test_task_lane_rejects_default_source_and_dirty_lane_run(self) -> None:
        git("switch", "main", cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "non-default source"):
            wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        git("switch", "abc-feature", cwd=self.repo)

        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        lane_root = Path(str(lane["worktree"]))
        (lane_root / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(wm.WorktreeError, "completely clean"):
            wm.task_lane_generation_acquire(
                cwd=Path(str(lane["scope_cwd"])),
                workspace=self.root / "workspace.json",
                run_id="run-dirty",
                task_scope="skills",
                initial_head=str(lane["lane_head"]),
            )

    def test_task_lane_generation_acquire_recovers_external_first_checkpoint(
        self,
    ) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        original_write = wm.write_lane
        failed = False

        def fail_first_active(primary: Path, value: dict[str, object]) -> Path:
            nonlocal failed
            if value["state"] == "active" and not failed:
                failed = True
                raise wm.TaskLaneStateError("simulated generation checkpoint loss")
            return original_write(primary, value)

        arguments = {
            "cwd": Path(str(lane["scope_cwd"])),
            "workspace": self.root / "workspace.json",
            "run_id": "run-recover-acquire",
            "task_scope": "skills",
            "initial_head": str(lane["lane_head"]),
        }
        with (
            mock.patch.object(wm, "write_lane", side_effect=fail_first_active),
            self.assertRaisesRegex(wm.WorktreeError, "generation checkpoint loss"),
        ):
            wm.task_lane_generation_acquire(**arguments)

        recovered = wm.task_lane_generation_acquire(**arguments)
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(recovered["generation"], 1)
        state = wm.load_lane(self.repo, str(lane["lane_id"]))
        assert state is not None
        self.assertEqual(state["active_generation"]["token"], recovered["token"])

    def test_task_lane_idle_refresh_recovers_after_fast_forward(self) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        lane_root = Path(str(lane["worktree"]))
        (self.repo / "source-refresh.txt").write_text("advance\n", encoding="utf-8")
        git("add", "source-refresh.txt", cwd=self.repo)
        git("commit", "-qm", "advance source for lane refresh", cwd=self.repo)
        source_head = git("rev-parse", "HEAD", cwd=self.repo)
        original_write_config = wm._write_config
        failed = False

        def fail_after_fast_forward(
            repository: Path, branch: str, field: str, value: str
        ) -> None:
            nonlocal failed
            if field == "base" and value == source_head and not failed:
                failed = True
                raise wm.WorktreeError("simulated refresh checkpoint loss")
            original_write_config(repository, branch, field, value)

        with (
            mock.patch.object(wm, "_write_config", side_effect=fail_after_fast_forward),
            self.assertRaisesRegex(wm.WorktreeError, "refresh checkpoint loss"),
        ):
            wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        self.assertEqual(git("rev-parse", "HEAD", cwd=lane_root), source_head)

        recovered = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        self.assertEqual(recovered["lane_head"], source_head)
        manifest = wm.load_manifest(self.repo, str(lane["name"]))
        assert manifest is not None
        self.assertEqual(manifest.base, source_head)
        self.assertEqual(manifest.expected_head, source_head)
        self.assertEqual(
            wm._read_config(self.repo, str(lane["branch"]), "base"), source_head
        )
        removed = wm.task_lane_remove(
            cwd=self.repo, lane_id=str(lane["lane_id"])
        )
        self.assertEqual(removed["status"], "removed")

    def test_task_lane_idle_refresh_serializes_generation_acquire(self) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        old_head = str(lane["lane_head"])
        (self.repo / "source-refresh-race.txt").write_text(
            "advance\n", encoding="utf-8"
        )
        git("add", "source-refresh-race.txt", cwd=self.repo)
        git("commit", "-qm", "advance source for refresh race", cwd=self.repo)
        source_head = git("rev-parse", "HEAD", cwd=self.repo)
        refresh_entered = threading.Event()
        allow_refresh = threading.Event()
        acquire_entered = threading.Event()
        original_refresh = wm._task_lane_refresh_idle
        original_lane_by_name = wm._task_lane_by_name

        def paused_refresh(
            primary: Path, value: dict[str, object]
        ) -> dict[str, object]:
            refresh_entered.set()
            if not allow_refresh.wait(timeout=5):
                raise AssertionError("refresh barrier timed out")
            return original_refresh(primary, value)

        def observed_lane_by_name(
            primary: Path, name: str
        ) -> tuple[dict[str, object], object, object]:
            acquire_entered.set()
            return original_lane_by_name(primary, name)

        with (
            mock.patch.object(
                wm, "_task_lane_refresh_idle", side_effect=paused_refresh
            ),
            mock.patch.object(
                wm, "_task_lane_by_name", side_effect=observed_lane_by_name
            ),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                refresh_future = executor.submit(
                    wm.task_lane_ensure, cwd=self.repo / "skills", project=None
                )
                self.assertTrue(refresh_entered.wait(timeout=5))
                acquire_future = executor.submit(
                    wm.task_lane_generation_acquire,
                    cwd=Path(str(lane["scope_cwd"])),
                    workspace=self.root / "refresh-race.json",
                    run_id="run-refresh-race",
                    task_scope="skills",
                    initial_head=old_head,
                )
                try:
                    self.assertFalse(acquire_entered.wait(timeout=0.2))
                finally:
                    allow_refresh.set()
                refreshed = refresh_future.result(timeout=5)
                self.assertEqual(refreshed["lane_head"], source_head)
                with self.assertRaisesRegex(wm.WorktreeError, "baseline does not match"):
                    acquire_future.result(timeout=5)

        state = wm.load_lane(self.repo, str(lane["lane_id"]))
        assert state is not None
        self.assertEqual(state["state"], "idle")
        self.assertIsNone(state["active_generation"])
        self.assertEqual(state["lane_head"], source_head)

    def test_task_lane_idle_refresh_serializes_removal(self) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        (self.repo / "source-refresh-remove.txt").write_text(
            "advance\n", encoding="utf-8"
        )
        git("add", "source-refresh-remove.txt", cwd=self.repo)
        git("commit", "-qm", "advance source for refresh removal", cwd=self.repo)
        refresh_entered = threading.Event()
        allow_refresh = threading.Event()
        remove_entered = threading.Event()
        original_refresh = wm._task_lane_refresh_idle
        original_remove = wm._remove_worktree_unlocked

        def paused_refresh(
            primary: Path, value: dict[str, object]
        ) -> dict[str, object]:
            refresh_entered.set()
            if not allow_refresh.wait(timeout=5):
                raise AssertionError("refresh barrier timed out")
            return original_refresh(primary, value)

        def observed_remove(**kwargs: object) -> dict[str, object]:
            remove_entered.set()
            return original_remove(**kwargs)

        with (
            mock.patch.object(
                wm, "_task_lane_refresh_idle", side_effect=paused_refresh
            ),
            mock.patch.object(
                wm, "_remove_worktree_unlocked", side_effect=observed_remove
            ),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                refresh_future = executor.submit(
                    wm.task_lane_ensure, cwd=self.repo / "skills", project=None
                )
                self.assertTrue(refresh_entered.wait(timeout=5))
                remove_future = executor.submit(
                    wm.task_lane_remove,
                    cwd=self.repo,
                    lane_id=str(lane["lane_id"]),
                )
                try:
                    self.assertFalse(remove_entered.wait(timeout=0.2))
                finally:
                    allow_refresh.set()
                self.assertEqual(refresh_future.result(timeout=5)["status"], "reused")
                self.assertEqual(remove_future.result(timeout=5)["status"], "removed")

    def test_task_lane_no_change_integration_recovers_after_fast_forward(self) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        lane_root = Path(str(lane["worktree"]))
        initial = str(lane["lane_head"])
        acquired = wm.task_lane_generation_acquire(
            cwd=lane_root / "skills",
            workspace=self.root / "workspace.json",
            run_id="run-no-change",
            task_scope="skills",
            initial_head=initial,
        )
        wm.task_lease_promote(
            cwd=lane_root,
            name=str(lane["name"]),
            lease_id=str(acquired["token"]),
            promoted_head=initial,
            expected_head=initial,
            owner_kind="task-implementer",
        )
        wm.task_lane_generation_release(
            cwd=lane_root,
            name=str(lane["name"]),
            generation=1,
            lease_id=str(acquired["token"]),
            promoted_head=initial,
        )
        (self.repo / "source-no-change.txt").write_text("advance\n", encoding="utf-8")
        git("add", "source-no-change.txt", cwd=self.repo)
        git("commit", "-qm", "advance source around no-change lane", cwd=self.repo)
        source_head = git("rev-parse", "HEAD", cwd=self.repo)
        original_write_config = wm._write_config
        failed = False

        def fail_after_fast_forward(
            repository: Path, branch: str, field: str, value: str
        ) -> None:
            nonlocal failed
            if field == "base" and value == source_head and not failed:
                failed = True
                raise wm.WorktreeError("simulated no-change checkpoint loss")
            original_write_config(repository, branch, field, value)

        with (
            mock.patch.object(wm, "_write_config", side_effect=fail_after_fast_forward),
            self.assertRaisesRegex(wm.WorktreeError, "no-change checkpoint loss"),
        ):
            wm.task_lane_integrate(
                cwd=self.repo,
                lane_id=str(lane["lane_id"]),
                validated_head=None,
                restart=False,
            )
        interrupted = wm.load_lane(self.repo, str(lane["lane_id"]))
        assert interrupted is not None
        self.assertEqual(interrupted["state"], "integrating")
        self.assertEqual(interrupted["integration"]["phase"], "no-change")
        self.assertEqual(git("rev-parse", "HEAD", cwd=lane_root), source_head)

        recovered = wm.task_lane_integrate(
            cwd=self.repo,
            lane_id=str(lane["lane_id"]),
            validated_head=None,
            restart=False,
        )
        self.assertEqual(recovered["status"], "integrated-no-change")
        self.assertEqual(recovered["integrated_generations"], [1])
        state = wm.load_lane(self.repo, str(lane["lane_id"]))
        assert state is not None
        self.assertEqual(state["state"], "idle")
        self.assertEqual(state["lane_head"], source_head)

    def test_task_lane_claim_and_release_are_serialized(self) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        lane_root = Path(str(lane["worktree"]))
        initial = str(lane["lane_head"])
        acquired = wm.task_lane_generation_acquire(
            cwd=lane_root / "skills",
            workspace=self.root / "workspace.json",
            run_id="run-claim-release",
            task_scope="skills",
            initial_head=initial,
        )
        first_claim = {"kind": "exact", "path": "skills/skill.txt"}
        wm.task_lane_generation_claims(
            cwd=lane_root,
            name=str(lane["name"]),
            generation=1,
            lease_id=str(acquired["token"]),
            claims=[first_claim],
        )
        wm.task_lease_promote(
            cwd=lane_root,
            name=str(lane["name"]),
            lease_id=str(acquired["token"]),
            promoted_head=initial,
            expected_head=initial,
            owner_kind="task-implementer",
        )
        release_entered = threading.Event()
        allow_release = threading.Event()
        claim_started = threading.Event()
        original_release = wm.task_lease_release

        def paused_release(**kwargs: object) -> dict[str, object]:
            release_entered.set()
            if not allow_release.wait(timeout=5):
                raise AssertionError("release barrier timed out")
            return original_release(**kwargs)

        def late_claim() -> dict[str, object]:
            claim_started.set()
            return wm.task_lane_generation_claims(
                cwd=lane_root,
                name=str(lane["name"]),
                generation=1,
                lease_id=str(acquired["token"]),
                claims=[{"kind": "exact", "path": "README.md"}],
            )

        with mock.patch.object(wm, "task_lease_release", side_effect=paused_release):
            with ThreadPoolExecutor(max_workers=2) as executor:
                release_future = executor.submit(
                    wm.task_lane_generation_release,
                    cwd=lane_root,
                    name=str(lane["name"]),
                    generation=1,
                    lease_id=str(acquired["token"]),
                    promoted_head=initial,
                )
                self.assertTrue(release_entered.wait(timeout=5))
                claim_future = executor.submit(late_claim)
                self.assertTrue(claim_started.wait(timeout=5))
                with self.assertRaises(TimeoutError):
                    claim_future.result(timeout=0.1)
                allow_release.set()
                self.assertEqual(release_future.result(timeout=5)["status"], "released")
                with self.assertRaisesRegex(wm.WorktreeError, "not active"):
                    claim_future.result(timeout=5)

        state = wm.load_lane(self.repo, str(lane["lane_id"]))
        receipt = wm.load_generation(self.repo, str(lane["lane_id"]), 1)
        assert state is not None and receipt is not None
        expected_claim = {**first_claim, "generation": 1}
        self.assertEqual(state["claims"], [expected_claim])
        self.assertEqual(receipt["claims"], [expected_claim])

    def test_task_lane_generations_integrate_and_rearm_same_lane(self) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        lane_root = Path(str(lane["worktree"]))
        workspace = self.root / "workspace.json"
        expected_heads: list[str] = []

        for generation, content in ((1, "first\n"), (2, "second\n")):
            initial = git("rev-parse", "HEAD", cwd=lane_root)
            acquired = wm.task_lane_generation_acquire(
                cwd=Path(str(lane["scope_cwd"])),
                workspace=workspace,
                run_id=f"run-{generation}",
                task_scope="skills",
                initial_head=initial,
            )
            self.assertEqual(acquired["generation"], generation)
            wm.task_lane_generation_claims(
                cwd=lane_root,
                name=str(lane["name"]),
                generation=generation,
                lease_id=str(acquired["token"]),
                claims=[{"kind": "exact", "path": "skills/skill.txt"}],
            )
            (lane_root / "skills" / "skill.txt").write_text(
                content, encoding="utf-8"
            )
            git("add", "skills/skill.txt", cwd=lane_root)
            git("commit", "-qm", f"generation {generation}", cwd=lane_root)
            promoted = git("rev-parse", "HEAD", cwd=lane_root)
            expected_heads.append(promoted)
            wm.task_lease_promote(
                cwd=lane_root,
                name=str(lane["name"]),
                lease_id=str(acquired["token"]),
                promoted_head=promoted,
                expected_head=initial,
                owner_kind="task-implementer",
            )
            released = wm.task_lane_generation_release(
                cwd=lane_root,
                name=str(lane["name"]),
                generation=generation,
                lease_id=str(acquired["token"]),
                promoted_head=promoted,
            )
            self.assertEqual(released["status"], "released")

        state = wm.load_lane(self.repo, str(lane["lane_id"]))
        assert state is not None
        self.assertEqual(state["pending_generations"], [1, 2])
        self.assertEqual(state["lane_head"], expected_heads[-1])
        with self.assertRaisesRegex(wm.WorktreeError, "fully integrated"):
            wm.task_lane_remove(cwd=self.repo, lane_id=str(lane["lane_id"]))
        with self.assertRaisesRegex(wm.WorktreeError, "workspace remove"):
            wm.remove_worktree(cwd=self.repo, name=str(lane["name"]))
        source_dirty = self.repo / "source-dirty-before-integrate.txt"
        source_dirty.write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(wm.WorktreeError, "source checkout"):
            wm.task_lane_integrate(
                cwd=self.repo,
                lane_id=str(lane["lane_id"]),
                validated_head=None,
                restart=False,
            )
        source_dirty.unlink()

        ready = wm.task_lane_integrate(
            cwd=self.repo,
            lane_id=str(lane["lane_id"]),
            validated_head=None,
            restart=False,
        )
        self.assertEqual(ready["status"], "validation-required")
        original_write = wm.write_lane
        failed = False

        def fail_first_rearm(primary: Path, value: dict[str, object]) -> Path:
            nonlocal failed
            if (
                value["state"] == "idle"
                and value["last_integrated_generation"] == 2
                and not failed
            ):
                failed = True
                raise wm.TaskLaneStateError("simulated rearm checkpoint loss")
            return original_write(primary, value)

        with (
            mock.patch.object(wm, "write_lane", side_effect=fail_first_rearm),
            self.assertRaisesRegex(wm.WorktreeError, "rearm checkpoint loss"),
        ):
            wm.task_lane_integrate(
                cwd=self.repo,
                lane_id=str(lane["lane_id"]),
                validated_head=str(ready["candidate_head"]),
                restart=False,
            )
        integrated = wm.task_lane_integrate(
            cwd=self.repo,
            lane_id=str(lane["lane_id"]),
            validated_head=None,
            restart=False,
        )
        self.assertEqual(integrated["status"], "integrated")
        self.assertEqual(integrated["integrated_generations"], [1, 2])
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), integrated["source_head"])
        self.assertEqual(git("rev-parse", "HEAD", cwd=lane_root), integrated["lane_head"])
        self.assertEqual(integrated["worktree"] if "worktree" in integrated else str(lane_root), str(lane_root))

        next_generation = wm.task_lane_generation_acquire(
            cwd=lane_root / "skills",
            workspace=workspace,
            run_id="run-3",
            task_scope="skills",
            initial_head=str(integrated["lane_head"]),
        )
        self.assertEqual(next_generation["generation"], 3)

    def test_task_lane_claims_conflict_across_project_scopes(self) -> None:
        first = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        second = wm.task_lane_ensure(
            cwd=self.repo / "services" / "example", project=None
        )
        first_lease = wm.task_lane_generation_acquire(
            cwd=Path(str(first["scope_cwd"])),
            workspace=self.root / "first.json",
            run_id="run-first",
            task_scope="skills",
            initial_head=str(first["lane_head"]),
        )
        second_lease = wm.task_lane_generation_acquire(
            cwd=Path(str(second["scope_cwd"])),
            workspace=self.root / "second.json",
            run_id="run-second",
            task_scope="services/example",
            initial_head=str(second["lane_head"]),
        )
        claim = [{"kind": "domain", "path": "repository-docs"}]
        wm.task_lane_generation_claims(
            cwd=Path(str(first["worktree"])),
            name=str(first["name"]),
            generation=1,
            lease_id=str(first_lease["token"]),
            claims=claim,
        )
        with self.assertRaisesRegex(wm.WorktreeError, "claim conflicts"):
            wm.task_lane_generation_claims(
                cwd=Path(str(second["worktree"])),
                name=str(second["name"]),
                generation=1,
                lease_id=str(second_lease["token"]),
                claims=claim,
            )

    def test_task_lane_claim_scan_failure_does_not_publish_unchecked_claim(
        self,
    ) -> None:
        first = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        second = wm.task_lane_ensure(
            cwd=self.repo / "services" / "example", project=None
        )
        first_lease = wm.task_lane_generation_acquire(
            cwd=Path(str(first["scope_cwd"])),
            workspace=self.root / "claim-first.json",
            run_id="run-claim-first",
            task_scope="skills",
            initial_head=str(first["lane_head"]),
        )
        second_lease = wm.task_lane_generation_acquire(
            cwd=Path(str(second["scope_cwd"])),
            workspace=self.root / "claim-second.json",
            run_id="run-claim-second",
            task_scope="services/example",
            initial_head=str(second["lane_head"]),
        )
        claim = [{"kind": "domain", "path": "repository-docs"}]
        wm.task_lane_generation_claims(
            cwd=Path(str(second["worktree"])),
            name=str(second["name"]),
            generation=1,
            lease_id=str(second_lease["token"]),
            claims=claim,
        )

        with (
            mock.patch.object(
                wm,
                "all_lanes",
                side_effect=wm.TaskLaneStateError("simulated peer scan failure"),
            ),
            self.assertRaisesRegex(wm.WorktreeError, "simulated peer scan failure"),
        ):
            wm.task_lane_generation_claims(
                cwd=Path(str(first["worktree"])),
                name=str(first["name"]),
                generation=1,
                lease_id=str(first_lease["token"]),
                claims=claim,
            )

        with self.assertRaisesRegex(wm.WorktreeError, "claim conflicts"):
            wm.task_lane_generation_claims(
                cwd=Path(str(first["worktree"])),
                name=str(first["name"]),
                generation=1,
                lease_id=str(first_lease["token"]),
                claims=claim,
            )

    def test_task_lane_integration_requires_explicit_restart_after_source_move(
        self,
    ) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        lane_root = Path(str(lane["worktree"]))
        acquired = wm.task_lane_generation_acquire(
            cwd=lane_root / "skills",
            workspace=self.root / "workspace.json",
            run_id="run-restart",
            task_scope="skills",
            initial_head=str(lane["lane_head"]),
        )
        initial = str(lane["lane_head"])
        (lane_root / "skills" / "skill.txt").write_text(
            "lane change\n", encoding="utf-8"
        )
        git("add", "skills/skill.txt", cwd=lane_root)
        git("commit", "-qm", "lane generation", cwd=lane_root)
        promoted = git("rev-parse", "HEAD", cwd=lane_root)
        wm.task_lease_promote(
            cwd=lane_root,
            name=str(lane["name"]),
            lease_id=str(acquired["token"]),
            promoted_head=promoted,
            expected_head=initial,
            owner_kind="task-implementer",
        )
        wm.task_lane_generation_release(
            cwd=lane_root,
            name=str(lane["name"]),
            generation=1,
            lease_id=str(acquired["token"]),
            promoted_head=promoted,
        )
        first = wm.task_lane_integrate(
            cwd=self.repo,
            lane_id=str(lane["lane_id"]),
            validated_head=None,
            restart=False,
        )
        self.assertEqual(first["status"], "validation-required")

        (self.repo / "source-moved.txt").write_text("moved\n", encoding="utf-8")
        git("add", "source-moved.txt", cwd=self.repo)
        git("commit", "-qm", "advance source during lane integration", cwd=self.repo)
        moved_source = git("rev-parse", "HEAD", cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "source moved"):
            wm.task_lane_integrate(
                cwd=self.repo,
                lane_id=str(lane["lane_id"]),
                validated_head=None,
                restart=False,
            )

        restarted = wm.task_lane_integrate(
            cwd=self.repo,
            lane_id=str(lane["lane_id"]),
            validated_head=None,
            restart=True,
        )
        self.assertEqual(restarted["status"], "validation-required")
        self.assertEqual(restarted["source_head"], moved_source)
        integrated = wm.task_lane_integrate(
            cwd=self.repo,
            lane_id=str(lane["lane_id"]),
            validated_head=str(restarted["candidate_head"]),
            restart=False,
        )
        self.assertEqual(integrated["integrated_generations"], [1])

    def test_task_lane_remove_is_idempotent_after_full_integration(self) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        removed = wm.task_lane_remove(
            cwd=self.repo, lane_id=str(lane["lane_id"])
        )
        repeated = wm.task_lane_remove(
            cwd=self.repo, lane_id=str(lane["lane_id"])
        )
        self.assertEqual(removed["status"], "removed")
        self.assertEqual(repeated["status"], "already-removed")
        self.assertFalse(Path(str(lane["worktree"])).exists())

    def test_task_lane_remove_resumes_after_post_cleanup_interruption(self) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        original_remove = wm._remove_worktree_unlocked

        def remove_then_interrupt(**kwargs: object) -> dict[str, object]:
            original_remove(**kwargs)
            raise wm.WorktreeError("simulated removal checkpoint interruption")

        with (
            mock.patch.object(
                wm, "_remove_worktree_unlocked", side_effect=remove_then_interrupt
            ),
            self.assertRaisesRegex(wm.WorktreeError, "simulated removal"),
        ):
            wm.task_lane_remove(cwd=self.repo, lane_id=str(lane["lane_id"]))

        resumed = wm.task_lane_remove(
            cwd=self.repo, lane_id=str(lane["lane_id"])
        )
        self.assertEqual(resumed["status"], "removed")
        final = wm.load_lane(self.repo, str(lane["lane_id"]))
        assert final is not None
        self.assertEqual(final["state"], "removed")

    def test_task_lane_remove_retains_branch_when_source_ref_moves(self) -> None:
        lane = wm.task_lane_ensure(cwd=self.repo / "skills", project=None)
        lane_head = str(lane["lane_head"])
        moved_source = git("rev-parse", "HEAD^", cwd=self.repo)
        original_remove = wm._remove_worktree_unlocked
        moved = False

        def move_source_then_remove(**kwargs: object) -> dict[str, object]:
            nonlocal moved
            if not moved:
                moved = True
                git(
                    "update-ref",
                    str(lane["source_ref"]),
                    moved_source,
                    lane_head,
                    cwd=self.repo,
                )
            return original_remove(**kwargs)

        with (
            mock.patch.object(
                wm, "_remove_worktree_unlocked", side_effect=move_source_then_remove
            ),
            self.assertRaisesRegex(wm.WorktreeError, "source ref moved"),
        ):
            wm.task_lane_remove(cwd=self.repo, lane_id=str(lane["lane_id"]))

        self.assertEqual(
            git(
                "rev-parse",
                "--verify",
                f"refs/heads/{lane['branch']}",
                cwd=self.repo,
            ),
            lane_head,
        )

    def test_remove_rejects_unintegrated_child_commits(self) -> None:
        result = self.add()
        self.commit_child(result, "skills/skill.txt", "child\n")
        with self.assertRaisesRegex(wm.WorktreeError, "must be integrated"):
            wm.remove_worktree(cwd=self.repo, name=str(result["name"]))

    def test_old_manifest_and_reservation_schemas_fail_closed(self) -> None:
        result = self.add()
        manifest_path = wm.manifest_path(self.repo, str(result["name"]))
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["schema"] = 2
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(wm.StateError, "WORKFLOW_UPGRADE_REQUIRED"):
            wm.load_manifest(self.repo, str(result["name"]))

        payload["schema"] = wm.MANIFEST_SCHEMA
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        worktree = Path(str(result["worktree"]))
        lease = wm.task_lease_acquire(
            cwd=worktree / "skills",
            workspace=self.root / "workspace",
            run_id="run-old",
            task_scope="skills",
            initial_head=self.source,
            owner_kind="task-implementer",
        )
        lease_path = wm.state_directory(self.repo) / "leases" / f"{result['name']}.json"
        lease_payload = json.loads(lease_path.read_text(encoding="utf-8"))
        lease_payload["schema"] = 3
        lease_path.write_text(json.dumps(lease_payload), encoding="utf-8")
        with self.assertRaisesRegex(wm.WorktreeError, "WORKFLOW_UPGRADE_REQUIRED"):
            wm.task_lease_inspect(
                cwd=worktree,
                name=str(result["name"]),
                lease_id=str(lease["token"]),
                owner_kind="task-implementer",
            )


if __name__ == "__main__":
    unittest.main()
