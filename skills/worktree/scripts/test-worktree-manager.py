#!/usr/bin/env python3
"""Offline real-Git tests for the local-source worktree lifecycle."""

from __future__ import annotations

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
        self.temporary.cleanup()

    def add(
        self, task_slug: str = "fix-triggers", project: str | None = None
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

        result = self.add(task_slug="normalized-scope", project="services/../skills")
        self.assertEqual(result["scope"], "skills")

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
            "integrate",
            "remove",
            "anchor-inspect",
            "publication-guard",
            "task-lease-acquire",
            "task-lease-resource",
            "task-lease-promote",
            "task-lease-release",
            "task-lease-inspect",
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
        prepared = wm.integrate_worktree(
            cwd=self.repo,
            name=str(result["name"]),
            validated_head=None,
            restart=False,
        )
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
        prepared = wm.integrate_worktree(
            cwd=self.repo,
            name=str(result["name"]),
            validated_head=None,
            restart=False,
        )
        self.assertEqual(prepared["status"], "validation-required")
        candidate = str(prepared["candidate_head"])
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.source)
        integrated = wm.integrate_worktree(
            cwd=self.repo,
            name=str(result["name"]),
            validated_head=candidate,
            restart=False,
        )
        self.assertEqual(integrated["status"], "integrated")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), candidate)
        self.assertEqual(
            git("rev-list", "--parents", "-n", "1", candidate, cwd=self.repo).split(),
            [candidate, self.source, child],
        )
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")

    def test_integrate_requires_clean_committed_child(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        with self.assertRaisesRegex(wm.WorktreeError, "no committed work"):
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                validated_head=None,
                restart=False,
            )
        (worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(wm.WorktreeError, "completely clean"):
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                validated_head=None,
                restart=False,
            )

    def test_conflict_is_retained_and_resumable(self) -> None:
        result = self.add()
        child = self.commit_child(result, "skills/skill.txt", "child\n")
        (self.repo / "skills/skill.txt").write_text("source\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "source overlap", cwd=self.repo)
        source_start = git("rev-parse", "HEAD", cwd=self.repo)
        conflicted = wm.integrate_worktree(
            cwd=self.repo,
            name=str(result["name"]),
            validated_head=None,
            restart=False,
        )
        self.assertEqual(conflicted["status"], "conflict")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), source_start)
        recovery = Path(str(conflicted["recovery_worktree"]))
        (recovery / "skills/skill.txt").write_text("resolved\n", encoding="utf-8")
        git("add", "-A", cwd=recovery)
        ready = wm.integrate_worktree(
            cwd=self.repo,
            name=str(result["name"]),
            validated_head=None,
            restart=False,
        )
        self.assertEqual(ready["status"], "validation-required")
        candidate = str(ready["candidate_head"])
        self.assertEqual(
            git("rev-list", "--parents", "-n", "1", candidate, cwd=self.repo).split(),
            [candidate, source_start, child],
        )

    def test_source_movement_requires_explicit_restart(self) -> None:
        result = self.add()
        self.commit_child(result, "skills/skill.txt", "child\n")
        prepared = wm.integrate_worktree(
            cwd=self.repo,
            name=str(result["name"]),
            validated_head=None,
            restart=False,
        )
        (self.repo / "later.txt").write_text("later\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "source moved", cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "--restart"):
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                validated_head=str(prepared["candidate_head"]),
                restart=False,
            )
        restarted = wm.integrate_worktree(
            cwd=self.repo,
            name=str(result["name"]),
            validated_head=None,
            restart=True,
        )
        self.assertEqual(restarted["status"], "validation-required")
        self.assertNotEqual(restarted["candidate_head"], prepared["candidate_head"])

    def test_restart_retains_unexpected_candidate_branch_advance(self) -> None:
        result = self.add()
        self.commit_child(result, "skills/skill.txt", "child\n")
        prepared = wm.integrate_worktree(
            cwd=self.repo,
            name=str(result["name"]),
            validated_head=None,
            restart=False,
        )
        candidate_path = Path(str(prepared["candidate_worktree"]))
        (candidate_path / "unexpected.txt").write_text(
            "preserve me\n", encoding="utf-8"
        )
        git("add", "-A", cwd=candidate_path)
        git("commit", "-qm", "unexpected candidate advance", cwd=candidate_path)
        unexpected_head = git("rev-parse", "HEAD", cwd=candidate_path)

        with self.assertRaisesRegex(wm.WorktreeError, "advanced"):
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                validated_head=None,
                restart=True,
            )

        self.assertTrue(candidate_path.is_dir())
        self.assertEqual(git("rev-parse", "HEAD", cwd=candidate_path), unexpected_head)

    def test_integrate_rejects_clean_candidate_advance_after_validation(self) -> None:
        result = self.add()
        self.commit_child(result, "skills/skill.txt", "child\n")
        prepared = wm.integrate_worktree(
            cwd=self.repo,
            name=str(result["name"]),
            validated_head=None,
            restart=False,
        )
        candidate = str(prepared["candidate_head"])
        candidate_path = Path(str(prepared["candidate_worktree"]))
        (candidate_path / "unvalidated.txt").write_text(
            "not validated\n", encoding="utf-8"
        )
        git("add", "-A", cwd=candidate_path)
        git("commit", "-qm", "unvalidated candidate advance", cwd=candidate_path)

        with self.assertRaisesRegex(wm.WorktreeError, "exact verification"):
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                validated_head=candidate,
                restart=False,
            )

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
        wm.integrate_worktree(
            cwd=self.repo,
            name=str(first["name"]),
            validated_head=None,
            restart=False,
        )
        with self.assertRaisesRegex(wm.WorktreeError, "active integration"):
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(second["name"]),
                validated_head=None,
                restart=False,
            )

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
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                validated_head=None,
                restart=False,
            )
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
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                validated_head=None,
                restart=False,
            )
        with self.assertRaisesRegex(wm.WorktreeError, "outer identity"):
            wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        lease_path.write_text(
            json.dumps(exact_receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        git("branch", internal_branch, child, cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "resources reappeared"):
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                validated_head=None,
                restart=False,
            )
        with self.assertRaisesRegex(wm.WorktreeError, "resources reappeared"):
            wm.task_lease_inspect(
                cwd=worktree,
                name=str(anchor["name"]),
                lease_id=str(lease["token"]),
                owner_kind="task-implementer",
            )
        git("branch", "-D", internal_branch, cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "terminal released"):
            wm.task_lease_acquire(
                cwd=worktree / "skills",
                workspace=self.root / "workspace",
                run_id="run-1",
                task_scope="skills",
                initial_head=child,
                owner_kind="task-implementer",
            )
        ready = wm.integrate_worktree(
            cwd=self.repo,
            name=str(result["name"]),
            validated_head=None,
            restart=False,
        )
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
            wm.integrate_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                validated_head=None,
                restart=False,
            )
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
        ready = wm.integrate_worktree(
            cwd=self.repo,
            name=str(used["name"]),
            validated_head=None,
            restart=False,
        )
        wm.integrate_worktree(
            cwd=self.repo,
            name=str(used["name"]),
            validated_head=str(ready["candidate_head"]),
            restart=False,
        )
        removed = wm.remove_worktree(cwd=self.repo, name=str(used["name"]))
        self.assertEqual(removed["status"], "removed")

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
