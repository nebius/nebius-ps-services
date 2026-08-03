#!/usr/bin/env python3
"""Offline real-Git tests for the worktree skill helper."""

from __future__ import annotations

from collections.abc import Iterable
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("worktree_manager.py")
SPEC = importlib.util.spec_from_file_location("worktree_manager", MODULE_PATH)
assert SPEC and SPEC.loader
wm = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wm
SPEC.loader.exec_module(wm)


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
        confidential_scope = self.repo / "customers" / "example-confidential"
        confidential_scope.mkdir(parents=True)
        (confidential_scope / "customer.txt").write_text(
            "base\n", encoding="utf-8"
        )
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "initial", cwd=self.repo)
        git("remote", "add", "origin", str(self.origin), cwd=self.repo)
        git("push", "-qu", "origin", "main", cwd=self.repo)
        git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.origin)
        git("fetch", "-q", "origin", cwd=self.repo)
        self.base = git("rev-parse", "origin/main", cwd=self.repo)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add(self, task_slug: str = "fix-triggers") -> dict[str, object]:
        with mock.patch.object(wm.secrets, "token_hex", return_value="a7c2f9"):
            return wm.add_worktree(
                cwd=self.repo / "skills", project=None, task_slug=task_slug
            )

    @staticmethod
    def merged_pr(branch: str, head: str) -> list[dict[str, object]]:
        return [
            {
                "number": 42,
                "url": "https://github.com/example/repo/pull/42",
                "state": "MERGED",
                "mergedAt": "2026-07-15T12:00:00Z",
                "headRefName": branch,
                "headRefOid": head,
                "baseRefName": "main",
            }
        ]

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
        self.assertEqual(records[1].prunable, "missing")

    def test_error_detail_redacts_credentials_and_tokens(self) -> None:
        credential_url = (
            "https://" + "user:example-credential@" + "example.invalid/repo"
        )
        assignment = "token" + "=example-sensitive-value"
        provider_token = "ghp_" + ("x" * 26)
        detail = f"{credential_url} {assignment} {provider_token}"
        redacted = wm._redact_detail(detail)
        self.assertNotIn("user:example-credential", redacted)
        self.assertNotIn("example-sensitive-value", redacted)
        self.assertNotIn("ghp_", redacted)

    def test_add_creates_full_repo_worktree_from_exact_origin_main(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        self.assertEqual(result["scope"], "skills")
        self.assertEqual(result["base_sha"], self.base)
        self.assertEqual(Path(str(result["scope_cwd"])), worktree / "skills")
        self.assertTrue((worktree / "services" / "example" / "service.txt").is_file())
        self.assertEqual(git("rev-parse", "HEAD", cwd=worktree), self.base)
        self.assertEqual(
            git("symbolic-ref", "--short", "HEAD", cwd=worktree), result["branch"]
        )
        upstream = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "@{upstream}"],
            cwd=worktree,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(upstream.returncode, 0)
        self.assertEqual(
            git(
                "config",
                "--local",
                "--get",
                f"branch.{result['branch']}.worktreeSkillScope",
                cwd=self.repo,
            ),
            "skills",
        )

    def test_add_uses_nonstandard_symbolic_remote_default(self) -> None:
        git("branch", "trunk", self.base, cwd=self.repo)
        git("push", "-qu", "origin", "trunk", cwd=self.repo)
        git("symbolic-ref", "HEAD", "refs/heads/trunk", cwd=self.origin)

        result = self.add()

        self.assertEqual(result["base_ref"], "origin/trunk")
        self.assertEqual(result["base_sha"], self.base)

    def test_add_uses_recorded_sha_when_remote_tracking_ref_advances(self) -> None:
        original_git = wm._git
        advanced: str | None = None

        def advance_before_preflight(
            cwd: Path, *arguments: str, allowed: Iterable[int] = (0,)
        ) -> str:
            nonlocal advanced
            if arguments and arguments[0] == "merge-base" and advanced is None:
                tree = git("rev-parse", f"{self.base}^{{tree}}", cwd=self.repo)
                advanced = git(
                    "commit-tree",
                    tree,
                    "-p",
                    self.base,
                    "-m",
                    "advance remote-tracking ref",
                    cwd=self.repo,
                )
                git(
                    "update-ref",
                    "refs/remotes/origin/main",
                    advanced,
                    self.base,
                    cwd=self.repo,
                )
            return original_git(cwd, *arguments, allowed=allowed)

        with (
            mock.patch.object(wm, "_git", side_effect=advance_before_preflight),
            mock.patch.object(wm.secrets, "token_hex", return_value="a7c2f9"),
        ):
            result = wm.add_worktree(
                cwd=self.repo / "skills",
                project=None,
                task_slug="fix-triggers",
            )

        self.assertIsNotNone(advanced)
        self.assertEqual(git("rev-parse", "origin/main", cwd=self.repo), advanced)
        self.assertEqual(result["base_sha"], self.base)
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=Path(str(result["worktree"]))),
            self.base,
        )

    def test_add_does_not_embed_project_scope_in_generated_identity(self) -> None:
        scope = self.repo / "customers" / "example-confidential"
        with mock.patch.object(wm.secrets, "token_hex", return_value="a7c2f9"):
            result = wm.add_worktree(
                cwd=scope,
                project=None,
                task_slug="fix-triggers",
            )
        self.assertEqual(result["scope"], "customers/example-confidential")
        self.assertEqual(result["name"], "project-fix-triggers-a7c2f9")
        self.assertEqual(result["branch"], "feature/fix-triggers-a7c2f9")
        self.assertEqual(
            Path(str(result["worktree"])).name,
            "project-fix-triggers-a7c2f9",
        )

    def test_add_allows_unrelated_primary_dirt(self) -> None:
        service = self.repo / "services" / "example" / "service.txt"
        service.write_text("unrelated\n", encoding="utf-8")
        result = self.add()
        self.assertEqual(
            result["unrelated_primary_changes"], ["services/example/service.txt"]
        )
        self.assertEqual(service.read_text(encoding="utf-8"), "unrelated\n")

    def test_add_blocks_selected_scope_dirty_or_committed_divergence(self) -> None:
        skill = self.repo / "skills" / "skill.txt"
        skill.write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(wm.WorktreeError, "uncommitted changes"):
            self.add()
        skill.write_text("committed\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "local skill work", cwd=self.repo)
        with self.assertRaisesRegex(wm.WorktreeError, "not contained in origin/main"):
            self.add()

    def test_add_rejects_scope_symlink_escape(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (self.repo / "escape").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(wm.WorktreeError, "outside the repository"):
            wm.add_worktree(
                cwd=self.repo,
                project="escape",
                task_slug="unsafe-scope",
            )

    def test_add_allows_scope_tree_already_squash_merged_to_main(self) -> None:
        git("switch", "-qc", "topic", cwd=self.repo)
        skill = self.repo / "skills" / "skill.txt"
        skill.write_text("squashed result\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "feature commit", cwd=self.repo)

        integrator = self.root / "integrator"
        git("clone", "-q", str(self.origin), str(integrator), cwd=self.root)
        git("config", "user.name", "Integrator", cwd=integrator)
        git("config", "user.email", "integrator@example.invalid", cwd=integrator)
        (integrator / "skills" / "skill.txt").write_text(
            "squashed result\n", encoding="utf-8"
        )
        git("add", "-A", cwd=integrator)
        git("commit", "-qm", "squash feature", cwd=integrator)
        git("push", "-q", "origin", "main", cwd=integrator)

        result = self.add("continue-work")
        self.assertEqual(result["scope"], "skills")
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=Path(str(result["worktree"]))),
            git("rev-parse", "origin/main", cwd=self.repo),
        )

    def test_interrupted_add_rolls_back_resources_and_planned_manifest(self) -> None:
        name = "project-fix-triggers-a7c2f9"
        branch = "feature/fix-triggers-a7c2f9"
        worktree = self.root / "example-monorepo-worktrees" / name
        with (
            mock.patch.object(wm.secrets, "token_hex", return_value="a7c2f9"),
            mock.patch.object(wm, "_write_config", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            wm.add_worktree(
                cwd=self.repo / "skills",
                project=None,
                task_slug="fix-triggers",
            )
        self.assertFalse(worktree.exists())
        self.assertFalse(wm._local_branch_exists(self.repo, branch))
        self.assertFalse(wm.manifest_path(self.repo, name).exists())

    def test_add_reuses_only_the_exact_active_lifecycle_and_preserves_changes(
        self,
    ) -> None:
        created = self.add()
        worktree = Path(str(created["worktree"]))
        changed = worktree / "skills" / "skill.txt"
        changed.write_text("unfinished\n", encoding="utf-8")

        with self.assertRaisesRegex(wm.WorktreeError, "managed lifecycle"):
            self.add()
        reused = wm.add_worktree(
            cwd=self.repo / "skills",
            project=None,
            task_slug="fix-triggers",
            reuse=str(created["name"]),
        )

        self.assertEqual(reused["status"], "reused")
        self.assertEqual(reused["worktree"], created["worktree"])
        self.assertEqual(reused["branch"], created["branch"])
        self.assertEqual(reused["dirty_paths"], ["skills/skill.txt"])
        self.assertFalse(reused["remote_default_head_drift"])
        self.assertEqual(changed.read_text(encoding="utf-8"), "unfinished\n")

    def test_add_reuse_cli_returns_json_without_altering_dirty_changes(self) -> None:
        created = self.add()
        worktree = Path(str(created["worktree"]))
        changed = worktree / "skills" / "skill.txt"
        changed.write_text("unfinished\n", encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(MODULE_PATH),
                "add",
                "--task-slug",
                "fix-triggers",
                "--reuse",
                str(created["name"]),
            ],
            cwd=self.repo / "skills",
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "reused")
        self.assertEqual(payload["dirty_paths"], ["skills/skill.txt"])
        self.assertEqual(changed.read_text(encoding="utf-8"), "unfinished\n")

    def test_interrupted_add_with_failed_rollback_is_recoverable(self) -> None:
        name = "project-fix-triggers-a7c2f9"
        branch = "feature/fix-triggers-a7c2f9"
        worktree = self.root / "example-monorepo-worktrees" / name
        original_write = wm._write_config
        original_run = wm._run
        writes = 0

        def interrupted_write(
            repository: Path, target_branch: str, field: str, value: str
        ) -> None:
            nonlocal writes
            writes += 1
            if writes == 2:
                raise KeyboardInterrupt
            original_write(repository, target_branch, field, value)

        def failed_rollback(
            arguments: list[str], *, cwd: Path, allowed: object = (0,)
        ) -> subprocess.CompletedProcess[str]:
            if arguments[:3] == ["git", "worktree", "remove"]:
                raise wm.WorktreeError("simulated rollback failure")
            return original_run(arguments, cwd=cwd, allowed=allowed)

        with (
            mock.patch.object(wm.secrets, "token_hex", return_value="a7c2f9"),
            mock.patch.object(wm, "_write_config", side_effect=interrupted_write),
            mock.patch.object(wm, "_run", side_effect=failed_rollback),
            self.assertRaises(KeyboardInterrupt),
        ):
            wm.add_worktree(
                cwd=self.repo / "skills",
                project=None,
                task_slug="fix-triggers",
            )

        manifest = wm.load_manifest(self.repo, name)
        assert manifest is not None
        self.assertEqual(manifest.status, "recovery")
        self.assertTrue(worktree.exists())
        self.assertTrue(wm._local_branch_exists(self.repo, branch))
        with mock.patch.object(wm, "_pull_requests", return_value=[]):
            removed = wm.remove_worktree(cwd=self.repo, name=name)
        self.assertEqual(removed["status"], "removed")
        self.assertFalse(worktree.exists())
        self.assertFalse(wm._local_branch_exists(self.repo, branch))
        self.assertFalse(wm.manifest_path(self.repo, name).exists())

    def test_inspect_blocks_changes_outside_recorded_scope(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        outside = worktree / "services" / "example" / "service.txt"
        outside.write_text("escaped\n", encoding="utf-8")
        with self.assertRaisesRegex(wm.WorktreeError, "outside its recorded"):
            wm.inspect_worktree(
                cwd=worktree / "skills",
                name=None,
                require_scope_clean=True,
            )

    def test_inspect_rejects_tampered_scope_metadata(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        git(
            "config",
            "--local",
            f"branch.{result['branch']}.worktreeSkillScope",
            "../outside",
            cwd=self.repo,
        )
        with self.assertRaisesRegex(wm.WorktreeError, "not a safe"):
            wm.inspect_worktree(
                cwd=worktree / "skills",
                name=None,
                require_scope_clean=True,
            )

    def test_inspect_rejects_recorded_remote_default_head_drift(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        tree = git("rev-parse", "HEAD^{tree}", cwd=self.repo)
        advanced = git(
            "commit-tree",
            tree,
            "-p",
            self.base,
            "-m",
            "advance remote default",
            cwd=self.repo,
        )
        git("push", "-q", "origin", f"{advanced}:refs/heads/main", cwd=self.repo)

        reused = wm.add_worktree(
            cwd=self.repo / "skills",
            project=None,
            task_slug="fix-triggers",
            reuse=str(result["name"]),
        )
        self.assertEqual(reused["status"], "reused")
        self.assertTrue(reused["remote_default_head_drift"])

        with self.assertRaisesRegex(wm.WorktreeError, "remote default changed"):
            wm.inspect_worktree(
                cwd=worktree / "skills",
                name=None,
                require_scope_clean=True,
            )

    def test_task_lease_blocks_outer_inspect_and_remove_until_release(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        outer_head = git("rev-parse", "HEAD", cwd=worktree)
        acquired = wm.task_lease_acquire(
            cwd=worktree / "skills",
            workspace=self.root / "private-workspace.json",
            run_id="run-001",
            task_scope="skills",
            initial_head=outer_head,
            owner_kind="task-implementer",
        )
        lease_id = str(acquired["token"])
        with self.assertRaisesRegex(wm.WorktreeError, "still owns"):
            wm.inspect_worktree(
                cwd=worktree / "skills",
                name=None,
                require_scope_clean=True,
            )
        with self.assertRaisesRegex(wm.WorktreeError, "still owns"):
            wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        wm.task_lease_promote(
            cwd=worktree / "skills",
            name=str(result["name"]),
            lease_id=lease_id,
            promoted_head=outer_head,
            owner_kind="task-implementer",
        )
        released = wm.task_lease_release(
            cwd=worktree / "skills",
            name=str(result["name"]),
            lease_id=lease_id,
            promoted_head=outer_head,
            owner_kind="task-implementer",
        )
        self.assertEqual(released["status"], "released")
        inspected = wm.inspect_worktree(
            cwd=worktree / "skills", name=None, require_scope_clean=True
        )
        self.assertEqual(inspected["head"], outer_head)

    def test_agentic_sdlc_owner_uses_v3_lease_and_releases_before_publication(
        self,
    ) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        outer_head = git("rev-parse", "HEAD", cwd=worktree)
        acquired = wm.task_lease_acquire(
            cwd=worktree / "skills",
            workspace=self.root / "agentic-run",
            run_id="run-agentic-001",
            task_scope="skills",
            initial_head=outer_head,
            owner_kind="agentic-sdlc",
        )
        lease_id = str(acquired["token"])
        self.assertEqual(acquired["schema"], 3)
        self.assertEqual(acquired["owner_kind"], "agentic-sdlc")
        resource = self.root / "agentic-integration"
        wm.task_lease_resource(
            cwd=worktree / "skills",
            name=str(result["name"]),
            lease_id=lease_id,
            kind="integration",
            path=resource,
            branch="codex/sdlc/run-agentic-001/feat-001/integration",
            state="planned",
            owner_kind="agentic-sdlc",
        )
        with self.assertRaises(wm.WorktreeError):
            wm.task_lease_promote(
                cwd=worktree / "skills",
                name=str(result["name"]),
                lease_id=lease_id,
                promoted_head=outer_head,
                owner_kind="task-implementer",
            )
        with self.assertRaises(wm.WorktreeError):
            wm.publication_begin(cwd=worktree / "skills", action="create-pr")
        wm.task_lease_promote(
            cwd=worktree / "skills",
            name=str(result["name"]),
            lease_id=lease_id,
            promoted_head=outer_head,
            owner_kind="agentic-sdlc",
        )
        wm.task_lease_release(
            cwd=worktree / "skills",
            name=str(result["name"]),
            lease_id=lease_id,
            promoted_head=outer_head,
            owner_kind="agentic-sdlc",
        )
        publication = wm.publication_begin(cwd=worktree / "skills", action="create-pr")
        self.assertEqual(publication["status"], "acquired")
        self.assertEqual(publication["default_branch"], "main")
        self.assertEqual(publication["default_ref"], "origin/main")
        self.assertEqual(publication["default_head"], self.base)

    def test_publication_begin_cli_returns_recorded_default_identity(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(MODULE_PATH),
                "publication-begin",
                "--publication-action",
                "create-pr",
            ],
            cwd=worktree / "skills",
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["default_branch"], "main")
        self.assertEqual(payload["default_ref"], "origin/main")
        self.assertEqual(payload["default_head"], self.base)

    def test_planned_resource_symlink_does_not_poison_lease_identity(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        outer_head = git("rev-parse", "HEAD", cwd=worktree)
        acquired = wm.task_lease_acquire(
            cwd=worktree / "skills",
            workspace=self.root / "private-workspace.json",
            run_id="run-001",
            task_scope="skills",
            initial_head=outer_head,
            owner_kind="task-implementer",
        )
        lease_id = str(acquired["token"])
        victim = self.root / "foreign-directory"
        victim.mkdir()
        planned = self.root / "task-worktrees" / "worker"
        planned.parent.mkdir()
        planned.symlink_to(victim, target_is_directory=True)
        wm.task_lease_resource(
            cwd=worktree / "skills",
            name=str(result["name"]),
            lease_id=lease_id,
            kind="worker",
            path=planned,
            branch="codex/ti-test-wave-001-task-1",
            state="planned",
            owner_kind="task-implementer",
        )
        planned.unlink()
        wm.task_lease_promote(
            cwd=worktree / "skills",
            name=str(result["name"]),
            lease_id=lease_id,
            promoted_head=outer_head,
            owner_kind="task-implementer",
        )
        released = wm.task_lease_release(
            cwd=worktree / "skills",
            name=str(result["name"]),
            lease_id=lease_id,
            promoted_head=outer_head,
            owner_kind="task-implementer",
        )
        self.assertEqual(released["status"], "released")
        self.assertTrue(victim.is_dir())

    def test_publication_reservation_serializes_task_lease(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        outer_head = git("rev-parse", "HEAD", cwd=worktree)
        first = wm.publication_begin(cwd=worktree / "skills", action="push")
        resumed = wm.publication_begin(cwd=worktree / "skills", action="push")
        self.assertEqual(resumed["status"], "resumed")
        self.assertEqual(first["token"], resumed["token"])
        with self.assertRaisesRegex(wm.WorktreeError, "different"):
            wm.publication_begin(cwd=worktree / "skills", action="create-pr")
        with self.assertRaisesRegex(wm.WorktreeError, "active"):
            wm.task_lease_acquire(
                cwd=worktree / "skills",
                workspace=self.root / "private-workspace.json",
                run_id="run-001",
                task_scope="skills",
                initial_head=outer_head,
                owner_kind="task-implementer",
            )
        ended = wm.publication_end_action(
            cwd=worktree / "skills",
            action="push",
            reservation_id=str(first["token"]),
        )
        self.assertEqual(ended["status"], "released")
        acquired = wm.task_lease_acquire(
            cwd=worktree / "skills",
            workspace=self.root / "private-workspace.json",
            run_id="run-001",
            task_scope="skills",
            initial_head=outer_head,
            owner_kind="task-implementer",
        )
        self.assertEqual(acquired["status"], "acquired")

    def test_task_lease_and_publication_race_has_one_owner(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        outer_head = git("rev-parse", "HEAD", cwd=worktree)
        barrier = threading.Barrier(3)
        outcomes: list[tuple[str, object]] = []

        def reserve_publication() -> None:
            barrier.wait()
            try:
                outcomes.append(
                    (
                        "publication",
                        wm.publication_begin(cwd=worktree / "skills", action="push"),
                    )
                )
            except wm.WorktreeError as error:
                outcomes.append(("publication-error", error))

        def acquire_lease() -> None:
            barrier.wait()
            try:
                outcomes.append(
                    (
                        "lease",
                        wm.task_lease_acquire(
                            cwd=worktree / "skills",
                            workspace=self.root / "private-workspace.json",
                            run_id="run-001",
                            task_scope="skills",
                            initial_head=outer_head,
                            owner_kind="task-implementer",
                        ),
                    )
                )
            except wm.WorktreeError as error:
                outcomes.append(("lease-error", error))

        threads = [
            threading.Thread(target=reserve_publication),
            threading.Thread(target=acquire_lease),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        successes = [item for item in outcomes if not item[0].endswith("-error")]
        failures = [item for item in outcomes if item[0].endswith("-error")]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        owner, payload = successes[0]
        assert isinstance(payload, dict)
        if owner == "publication":
            wm.publication_end_action(
                cwd=worktree / "skills",
                action="push",
                reservation_id=str(payload["token"]),
            )
        else:
            wm.task_lease_promote(
                cwd=worktree / "skills",
                name=str(result["name"]),
                lease_id=str(payload["token"]),
                promoted_head=outer_head,
                owner_kind="task-implementer",
            )
            wm.task_lease_release(
                cwd=worktree / "skills",
                name=str(result["name"]),
                lease_id=str(payload["token"]),
                promoted_head=outer_head,
                owner_kind="task-implementer",
            )

    def test_malformed_interop_state_fails_closed(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        lease = (
            self.root
            / "example-monorepo-worktrees"
            / ".worktree-skill"
            / "leases"
            / f"{result['name']}.json"
        )
        lease.parent.mkdir(parents=True, exist_ok=True)
        lease.write_text("{not-json\n", encoding="utf-8")
        with self.assertRaisesRegex(wm.WorktreeError, "invalid"):
            wm.inspect_worktree(
                cwd=worktree / "skills",
                name=None,
                require_scope_clean=True,
            )

    def test_manifest_v1_is_rejected_without_compatibility_fallback(self) -> None:
        result = self.add()
        manifest_path = wm.manifest_path(self.repo, str(result["name"]))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema"] = 1
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(wm.WorktreeError, "unsupported ownership"):
            wm.inspect_worktree(
                cwd=self.repo,
                name=str(result["name"]),
                require_scope_clean=False,
            )

    def test_unfinished_legacy_leases_require_workflow_upgrade(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        lease = (
            self.root
            / "example-monorepo-worktrees"
            / ".worktree-skill"
            / "leases"
            / f"{result['name']}.json"
        )
        lease.parent.mkdir(parents=True, exist_ok=True)
        for schema in (1, 2):
            with self.subTest(schema=schema):
                lease.write_text(f'{{"schema": {schema}}}\n', encoding="utf-8")
                with self.assertRaisesRegex(
                    wm.WorktreeError, "WORKFLOW_UPGRADE_REQUIRED"
                ):
                    wm.inspect_worktree(
                        cwd=worktree / "skills",
                        name=None,
                        require_scope_clean=True,
                    )

    def test_publication_reservation_v1_requires_workflow_upgrade(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        reservation = (
            self.root
            / "example-monorepo-worktrees"
            / ".worktree-skill"
            / "reservations"
            / f"{result['name']}.json"
        )
        reservation.parent.mkdir(parents=True, exist_ok=True)
        reservation.write_text('{"schema": 1}\n', encoding="utf-8")
        with self.assertRaisesRegex(wm.WorktreeError, "WORKFLOW_UPGRADE_REQUIRED"):
            wm.inspect_worktree(
                cwd=worktree / "skills",
                name=None,
                require_scope_clean=True,
            )

    def test_interop_lock_symlink_fails_closed(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        victim = self.root / "victim.txt"
        victim.write_text("unchanged\n", encoding="utf-8")
        victim.chmod(0o644)
        lock = (
            self.root
            / "example-monorepo-worktrees"
            / ".worktree-skill"
            / ".interop.lock"
        )
        lock.symlink_to(victim)
        with self.assertRaisesRegex(wm.WorktreeError, "interop lock"):
            wm.inspect_worktree(
                cwd=worktree / "skills",
                name=None,
                require_scope_clean=True,
            )
        self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged\n")
        self.assertEqual(victim.stat().st_mode & 0o777, 0o644)

    def test_remove_unused_local_worktree(self) -> None:
        result = self.add()
        with mock.patch.object(wm, "_pull_requests", return_value=[]):
            removed = wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(removed["status"], "removed")
        self.assertFalse(Path(str(result["worktree"])).exists())
        self.assertFalse(
            subprocess.run(
                [
                    "git",
                    "show-ref",
                    "--verify",
                    "--quiet",
                    f"refs/heads/{result['branch']}",
                ],
                cwd=self.repo,
                check=False,
            ).returncode
            == 0
        )

    def test_remove_requires_primary_checkout_and_exact_name(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        with self.assertRaisesRegex(wm.WorktreeError, "primary checkout"):
            wm.remove_worktree(cwd=worktree / "skills", name=None)
        with self.assertRaisesRegex(wm.WorktreeError, "requires --name"):
            wm.remove_worktree(cwd=self.repo, name=None)
        self.assertTrue(worktree.exists())

    def test_remove_merged_pr_uses_guarded_branch_delete_and_remote_lease(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "skills" / "skill.txt").write_text("feature\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "update skill", cwd=worktree)
        head = git("rev-parse", "HEAD", cwd=worktree)
        git("push", "-q", "origin", f"HEAD:refs/heads/{result['branch']}", cwd=worktree)
        with mock.patch.object(
            wm,
            "_pull_requests",
            return_value=self.merged_pr(str(result["branch"]), head),
        ):
            removed = wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(removed["local_branch"], "deleted-with-expected-old-value")
        self.assertEqual(removed["remote_branch"], "deleted-with-exact-lease")
        self.assertEqual(
            git(
                "ls-remote",
                "--heads",
                "origin",
                f"refs/heads/{result['branch']}",
                cwd=self.repo,
            ),
            "",
        )

    def test_remove_retains_dirty_or_unmerged_work(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "skills" / "new.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(wm.WorktreeError, "dirty"):
            wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertTrue(worktree.exists())
        (worktree / "skills" / "new.txt").unlink()
        (worktree / "skills" / "skill.txt").write_text("commit\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "unmerged", cwd=worktree)
        with mock.patch.object(wm, "_pull_requests", return_value=[]):
            with self.assertRaisesRegex(wm.WorktreeError, "exact merged PR"):
                wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertTrue(worktree.exists())

    def test_remove_retains_remote_branch_when_pr_head_does_not_match(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "skills" / "skill.txt").write_text("feature\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "update skill", cwd=worktree)
        head = git("rev-parse", "HEAD", cwd=worktree)
        git("push", "-q", "origin", f"HEAD:refs/heads/{result['branch']}", cwd=worktree)
        mismatched = self.merged_pr(str(result["branch"]), "f" * 40)
        with mock.patch.object(wm, "_pull_requests", return_value=mismatched):
            with self.assertRaisesRegex(wm.WorktreeError, "exact merged PR"):
                wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertTrue(worktree.exists())
        self.assertEqual(
            git(
                "ls-remote",
                "--heads",
                "origin",
                f"refs/heads/{result['branch']}",
                cwd=self.repo,
            ).split()[0],
            head,
        )

    def test_remove_resumes_when_only_remote_branch_remains(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "skills" / "skill.txt").write_text("feature\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "update skill", cwd=worktree)
        head = git("rev-parse", "HEAD", cwd=worktree)
        git("push", "-q", "origin", f"HEAD:refs/heads/{result['branch']}", cwd=worktree)
        git("worktree", "remove", str(worktree), cwd=self.repo)
        git("branch", "-D", str(result["branch"]), cwd=self.repo)
        with mock.patch.object(
            wm,
            "_pull_requests",
            return_value=self.merged_pr(str(result["branch"]), head),
        ):
            removed = wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(removed["local_branch"], "already-removed")
        self.assertEqual(removed["remote_branch"], "deleted-with-exact-lease")
        again = wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(again["status"], "already-removed")

    def test_remote_only_cleanup_requires_durable_ownership_manifest(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "skills" / "skill.txt").write_text("feature\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "update skill", cwd=worktree)
        head = git("rev-parse", "HEAD", cwd=worktree)
        git("push", "-q", "origin", f"HEAD:refs/heads/{result['branch']}", cwd=worktree)
        git("worktree", "remove", str(worktree), cwd=self.repo)
        git("branch", "-D", str(result["branch"]), cwd=self.repo)
        wm.delete_manifest(self.repo, str(result["name"]))
        with mock.patch.object(
            wm,
            "_pull_requests",
            return_value=self.merged_pr(str(result["branch"]), head),
        ):
            with self.assertRaisesRegex(
                wm.WorktreeError, "ownership manifest is missing"
            ):
                wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(
            git(
                "ls-remote",
                "--heads",
                "origin",
                f"refs/heads/{result['branch']}",
                cwd=self.repo,
            ).split()[0],
            head,
        )

    def test_remove_retains_branch_that_advances_during_cleanup(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "skills" / "skill.txt").write_text("feature\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "update skill", cwd=worktree)
        proved_head = git("rev-parse", "HEAD", cwd=worktree)
        git("push", "-q", "origin", f"HEAD:refs/heads/{result['branch']}", cwd=worktree)
        original_git = wm._git
        raced = False

        def racing_git(cwd: Path, *arguments: str, **kwargs: object) -> str:
            nonlocal raced
            if not raced and arguments[:2] == ("worktree", "remove"):
                raced = True
                (worktree / "skills" / "race.txt").write_text(
                    "late commit\n", encoding="utf-8"
                )
                git("add", "-A", cwd=worktree)
                git("commit", "-qm", "late unpushed commit", cwd=worktree)
            return original_git(cwd, *arguments, **kwargs)

        with (
            mock.patch.object(
                wm,
                "_pull_requests",
                return_value=self.merged_pr(str(result["branch"]), proved_head),
            ),
            mock.patch.object(wm, "_git", side_effect=racing_git),
        ):
            with self.assertRaisesRegex(
                wm.WorktreeError, "advanced after cleanup proof"
            ):
                wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertFalse(worktree.exists())
        retained = git("rev-parse", f"refs/heads/{result['branch']}", cwd=self.repo)
        self.assertNotEqual(retained, proved_head)

    def test_expected_old_ref_delete_rejects_final_branch_race(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "skills" / "skill.txt").write_text("feature\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "update skill", cwd=worktree)
        proved_head = git("rev-parse", "HEAD", cwd=worktree)
        git("push", "-q", "origin", f"HEAD:refs/heads/{result['branch']}", cwd=worktree)
        original_run = wm._run
        raced = False

        def racing_run(
            arguments: list[str], *, cwd: Path, allowed: object = (0,)
        ) -> subprocess.CompletedProcess[str]:
            nonlocal raced
            if not raced and arguments[:3] == ["git", "update-ref", "-d"]:
                raced = True
                git(
                    "update-ref",
                    f"refs/heads/{result['branch']}",
                    self.base,
                    cwd=self.repo,
                )
            return original_run(arguments, cwd=cwd, allowed=allowed)

        with (
            mock.patch.object(
                wm,
                "_pull_requests",
                return_value=self.merged_pr(str(result["branch"]), proved_head),
            ),
            mock.patch.object(wm, "_run", side_effect=racing_run),
        ):
            with self.assertRaisesRegex(wm.WorktreeError, "cannot lock ref"):
                wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertFalse(worktree.exists())
        self.assertEqual(
            git("rev-parse", f"refs/heads/{result['branch']}", cwd=self.repo),
            self.base,
        )

    def test_cleanup_resume_rejects_advanced_remote_even_with_merged_pr(self) -> None:
        result = self.add()
        worktree = Path(str(result["worktree"]))
        (worktree / "skills" / "skill.txt").write_text("feature\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "update skill", cwd=worktree)
        proved_head = git("rev-parse", "HEAD", cwd=worktree)
        git("push", "-q", "origin", f"HEAD:refs/heads/{result['branch']}", cwd=worktree)
        manifest = wm.load_manifest(self.repo, str(result["name"]))
        assert manifest is not None
        wm.write_manifest(
            self.repo,
            manifest.updated(status="cleanup-pending", expected_head=proved_head),
        )
        git("worktree", "remove", str(worktree), cwd=self.repo)
        git("branch", "-D", str(result["branch"]), cwd=self.repo)

        advancer = self.root / "advancer"
        git("clone", "-q", str(self.origin), str(advancer), cwd=self.root)
        git("config", "user.name", "Advancer", cwd=advancer)
        git("config", "user.email", "advancer@example.invalid", cwd=advancer)
        git("switch", "-qc", "advanced", f"origin/{result['branch']}", cwd=advancer)
        (advancer / "skills" / "late.txt").write_text("late\n", encoding="utf-8")
        git("add", "-A", cwd=advancer)
        git("commit", "-qm", "late merged work", cwd=advancer)
        advanced_head = git("rev-parse", "HEAD", cwd=advancer)
        git("push", "-q", "origin", f"HEAD:refs/heads/{result['branch']}", cwd=advancer)

        with mock.patch.object(
            wm,
            "_pull_requests",
            return_value=self.merged_pr(str(result["branch"]), advanced_head),
        ):
            with self.assertRaisesRegex(
                wm.WorktreeError, "advanced after cleanup proof"
            ):
                wm.remove_worktree(cwd=self.repo, name=str(result["name"]))
        self.assertEqual(
            git(
                "ls-remote",
                "--heads",
                "origin",
                f"refs/heads/{result['branch']}",
                cwd=self.repo,
            ).split()[0],
            advanced_head,
        )


if __name__ == "__main__":
    unittest.main()
