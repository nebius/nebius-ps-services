#!/usr/bin/env python3
"""Focused tests for explicit whole-repository commit transactions."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


COMMIT_ROOT = Path(__file__).resolve().parents[1]
HELPER = COMMIT_ROOT / "scripts" / "commit_transaction.py"
INTENT = COMMIT_ROOT / "assets" / "hooks" / "commit_intent.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


intent = load_module("commit_intent", INTENT)
transaction = load_module("commit_transaction", HELPER)
WORKTREE_SCRIPTS = COMMIT_ROOT.parent / "worktree" / "scripts"
sys.path.insert(0, str(WORKTREE_SCRIPTS))
import worktree_interop  # noqa: E402
import worktree_state  # noqa: E402


def git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


class CommitTransactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.codex_home = self.base / "codex"
        self.root = self.base / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "Test User")
        (self.root / "project-a").mkdir()
        (self.root / "project-b").mkdir()
        (self.root / "project-a" / "tracked.txt").write_text(
            "baseline\n", encoding="utf-8"
        )
        (self.root / "project-b" / "tracked.txt").write_text(
            "baseline\n", encoding="utf-8"
        )
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "baseline")
        git(self.root, "switch", "-qc", "feature/test")
        self.previous_home = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(self.codex_home)

    def tearDown(self) -> None:
        if self.previous_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.previous_home
        self.temporary.cleanup()

    def authorize(
        self,
        prompt: str = "$commit Test complete repository change",
        *,
        session_id: str = "session-1",
        turn_id: str = "turn-1",
    ) -> Path:
        result = intent.evaluate(
            {
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(self.root),
                "session_id": session_id,
                "turn_id": turn_id,
                "prompt": prompt,
                "agent_type": "root",
            }
        )
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Explicit commit transaction authorization", context)
        return transaction.expected_authorization_path(self.root, session_id)

    def run_helper(self, *arguments: str, expected: int = 0) -> dict[str, object]:
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(self.codex_home)
        completed = subprocess.run(
            ["python3", str(HELPER), *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=30,
        )
        self.assertEqual(
            completed.returncode, expected, completed.stderr or completed.stdout
        )
        return json.loads(completed.stdout)

    def prepare(self) -> dict[str, object]:
        authorization = self.authorize()
        claim = transaction.expected_claim_path(self.root)
        return self.run_helper(
            "prepare",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--authorization",
            str(authorization),
            "--claim",
            str(claim),
        )

    def seed_multi_project_diff(self) -> None:
        (self.root / "project-a" / "tracked.txt").write_text(
            "staged\n", encoding="utf-8"
        )
        git(self.root, "add", "project-a/tracked.txt")
        (self.root / "project-b" / "tracked.txt").write_text(
            "unstaged\n", encoding="utf-8"
        )
        (self.root / "project-b" / "new.txt").write_text(
            "untracked\n", encoding="utf-8"
        )

    def test_only_bounded_root_user_commit_turn_mints_authorization(self) -> None:
        base = {
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(self.root),
            "session_id": "session-1",
            "turn_id": "turn-1",
            "agent_type": "root",
        }
        for prompt in (
            "$commit",
            "$commit Fix docs",
            "run $commit",
            "run $commit Fix docs",
            "apply $commit Fix docs",
            "execute $commit Fix docs",
            "invoke $commit Fix docs",
            "Use $commit Fix docs",
            "please run $commit Fix docs",
            "please $commit Fix docs",
            "$commit-push",
            "$commit-push Fix docs",
            "run $commit-push",
            "please use $commit-push Fix docs",
        ):
            with self.subTest(prompt=prompt):
                result = intent.evaluate({**base, "prompt": prompt})
                self.assertIn("hookSpecificOutput", result)
        primary_without_agent_type = {**base, "prompt": "run $commit"}
        primary_without_agent_type.pop("agent_type")
        self.assertIn("hookSpecificOutput", intent.evaluate(primary_without_agent_type))
        for prompt in (
            "please use commit",
            "Can you discuss `$commit`?",
            "Can you run $commit?",
            "Example: run $commit",
            "If needed, run $commit",
            "Do not run $commit",
            'run "$commit"',
            "run `$commit`",
            "$commit --help",
            "run $commit --help",
            "apply $commit -h",
            "run $commit-push --help",
            "$commitment",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(intent.evaluate({**base, "prompt": prompt}), {})
        excluded_payloads = (
            {"is_subagent": True},
            {"stop_hook_active": True},
            {"prompt_source": "stop"},
            {"prompt_source": "continuation"},
            {"prompt_source": "compaction"},
            {"prompt_source": "subagent"},
            {"prompt_source": "system"},
            {"prompt_source": "user", "source": "subagent"},
            {"agent_type": "worker"},
            {"hook_event_name": "PreToolUse"},
        )
        for excluded in excluded_payloads:
            with self.subTest(excluded=excluded):
                self.assertEqual(
                    intent.evaluate({**base, "prompt": "run $commit now", **excluded}),
                    {},
                )
        self.assertEqual(intent.evaluate({**base, "prompt": None}), {})
        for missing_identity in (
            {"session_id": None},
            {"session_id": ""},
            {"turn_id": None},
            {"turn_id": ""},
        ):
            with self.subTest(missing_identity=missing_identity):
                with self.assertRaises(intent.IntentError):
                    intent.evaluate(
                        {
                            **base,
                            "prompt": "run $commit now",
                            **missing_identity,
                        }
                    )
        self.assertFalse(
            intent._default_branch_authorized("$commit Fix docs", "refs/heads/main")
        )
        self.assertTrue(
            intent._default_branch_authorized(
                "$commit on main Fix docs", "refs/heads/main"
            )
        )
        self.assertTrue(
            intent._default_branch_authorized(
                "run $commit on main Fix docs", "refs/heads/main"
            )
        )
        self.assertTrue(
            intent._default_branch_authorized(
                "please execute $commit on the default branch Fix docs",
                "refs/heads/main",
            )
        )
        self.assertFalse(
            intent._default_branch_authorized(
                "$commit on mainframe Fix docs", "refs/heads/main"
            )
        )
        self.assertFalse(
            intent._default_branch_authorized(
                "$commit-push on main Fix docs", "refs/heads/main"
            )
        )

    def test_default_branch_requires_explicit_prompt_binding(self) -> None:
        self.seed_multi_project_diff()
        git(
            self.root,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/feature/test",
        )
        authorization = self.authorize("$commit Ordinary message")
        claim = transaction.expected_claim_path(self.root)
        blocked = self.run_helper(
            "prepare",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--authorization",
            str(authorization),
            "--claim",
            str(claim),
            expected=2,
        )
        self.assertIn("default branch", str(blocked["reason"]))
        self.authorize("$commit on feature/test Commit this default branch")
        prepared = self.run_helper(
            "prepare",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--authorization",
            str(authorization),
            "--claim",
            str(claim),
            "--allow-default-branch",
        )
        self.assertEqual(prepared["status"], "prepared")

    def test_prepare_uses_temp_index_and_preserves_real_staging(self) -> None:
        self.seed_multi_project_diff()
        before = git(self.root, "write-tree")
        prepared = self.prepare()
        self.assertEqual(prepared["status"], "prepared")
        self.assertEqual(git(self.root, "write-tree"), before)
        self.assertNotEqual(prepared["candidate_tree"], before)
        self.assertTrue((self.root / "project-b" / "new.txt").is_file())

    def test_repository_shaping_git_environment_blocks_before_index_mutation(
        self,
    ) -> None:
        self.seed_multi_project_diff()
        authorization = self.authorize()
        claim = transaction.expected_claim_path(self.root)
        real_index = git(self.root, "write-tree")
        alternate_index = self.base / "alternate-index"
        previous_index = os.environ.get("GIT_INDEX_FILE")
        os.environ["GIT_INDEX_FILE"] = str(alternate_index)
        try:
            with self.assertRaises(intent.IntentError):
                intent.evaluate(
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "cwd": str(self.root),
                        "session_id": "session-1",
                        "turn_id": "turn-env",
                        "agent_type": "root",
                        "prompt": "run $commit",
                    }
                )
            blocked = self.run_helper(
                "prepare",
                "--repo-root",
                str(self.root),
                "--session-id",
                "session-1",
                "--authorization",
                str(authorization),
                "--claim",
                str(claim),
                expected=2,
            )
        finally:
            if previous_index is None:
                os.environ.pop("GIT_INDEX_FILE", None)
            else:
                os.environ["GIT_INDEX_FILE"] = previous_index
        self.assertIn("repository-shaping Git environment", str(blocked["reason"]))
        self.assertEqual(git(self.root, "write-tree"), real_index)
        self.assertFalse(alternate_index.exists())

    def test_repository_shaping_git_environment_rejects_config_and_attributes(
        self,
    ) -> None:
        for name, value in (
            ("GIT_COMMON_DIR", ""),
            ("GIT_CONFIG_GLOBAL", "attacker-controlled"),
            ("GIT_CONFIG_KEY_0", "attacker-controlled"),
            ("GIT_ATTR_SOURCE", "attacker-controlled"),
            ("GIT_OBJECT_DIRECTORY", "attacker-controlled"),
        ):
            previous = os.environ.get(name)
            os.environ[name] = value
            try:
                with self.subTest(name=name):
                    with self.assertRaises(intent.IntentError):
                        intent._git_environment()
                    with self.assertRaises(transaction.TransactionError):
                        transaction._git_environment()
            finally:
                if previous is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous

    def test_execute_commits_all_projects_as_one_exact_direct_child(self) -> None:
        self.seed_multi_project_diff()
        base_head = git(self.root, "rev-parse", "HEAD")
        prepared = self.prepare()
        result = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Commit complete repository change",
        )
        self.assertEqual(result["status"], "committed")
        self.assertEqual(git(self.root, "rev-parse", "HEAD^"), base_head)
        self.assertEqual(
            git(self.root, "rev-parse", "HEAD^{tree}"), prepared["candidate_tree"]
        )
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        replayed = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Commit complete repository change",
        )
        self.assertEqual(replayed["commit"], result["commit"])
        self.assertEqual(
            git(self.root, "rev-list", "--count", f"{base_head}..HEAD"), "1"
        )

    def test_drift_stales_claim_before_real_index_mutation(self) -> None:
        self.seed_multi_project_diff()
        prepared = self.prepare()
        real_index = git(self.root, "write-tree")
        (self.root / "project-b" / "new.txt").write_text("drift\n", encoding="utf-8")
        blocked = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Should not commit",
            expected=2,
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("stale", str(blocked["reason"]))
        self.assertEqual(git(self.root, "write-tree"), real_index)

        self.authorize("$commit Reprepare changed candidate")
        refreshed = self.run_helper(
            "prepare",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--authorization",
            str(transaction.expected_authorization_path(self.root, "session-1")),
            "--claim",
            str(prepared["claim"]),
        )
        self.assertEqual(refreshed["status"], "prepared")
        self.assertNotEqual(refreshed["candidate_tree"], prepared["candidate_tree"])

    def test_unsafe_private_claim_mode_blocks_before_index_mutation(self) -> None:
        self.seed_multi_project_diff()
        prepared = self.prepare()
        real_index = git(self.root, "write-tree")
        Path(str(prepared["claim"])).chmod(0o644)
        blocked = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Must not use unsafe state",
            expected=2,
        )
        self.assertIn("claim path is unsafe", str(blocked["reason"]))
        self.assertEqual(git(self.root, "write-tree"), real_index)

    def test_authorization_tamper_blocks_before_index_mutation(self) -> None:
        self.seed_multi_project_diff()
        prepared = self.prepare()
        real_index = git(self.root, "write-tree")
        authorization_path = transaction.expected_authorization_path(
            self.root, "session-1"
        )
        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        authorization["prompt_sha256"] = "f" * 64
        authorization_path.write_text(
            json.dumps(authorization, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        authorization_path.chmod(0o600)
        blocked = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Must not use modified authorization",
            expected=2,
        )
        self.assertIn("authorization digest", str(blocked["reason"]))
        self.assertEqual(git(self.root, "write-tree"), real_index)

    def test_hook_modified_commit_tree_requires_review(self) -> None:
        self.seed_multi_project_diff()
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text(
            "#!/bin/sh\nprintf 'hooked\\n' > project-a/hook.txt\ngit add project-a/hook.txt\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        prepared = self.prepare()
        blocked = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Hook changes tree",
            expected=2,
        )
        self.assertEqual(blocked["status"], "blocked")
        claim = json.loads(Path(str(prepared["claim"])).read_text(encoding="utf-8"))
        self.assertEqual(claim["state"], "REVIEW_REQUIRED")
        reviewed_commit = git(self.root, "rev-parse", "HEAD")
        reviewed_tree = git(self.root, "rev-parse", "HEAD^{tree}")
        self.assertEqual(claim["commit_head"], reviewed_commit)
        self.assertEqual(claim["commit_tree"], reviewed_tree)
        rejected = self.run_helper(
            "review",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-commit",
            reviewed_commit,
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            expected=2,
        )
        self.assertIn("reviewed commit", str(rejected["reason"]))
        completed = self.run_helper(
            "review",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-commit",
            reviewed_commit,
            "--reviewed-tree",
            reviewed_tree,
        )
        self.assertEqual(completed["status"], "committed")
        claim = json.loads(Path(str(prepared["claim"])).read_text(encoding="utf-8"))
        self.assertEqual(claim["state"], "COMMITTED")

    def test_hook_modified_post_commit_rebinds_review_to_fresh_session(self) -> None:
        self.seed_multi_project_diff()
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text(
            "#!/bin/sh\nprintf 'hooked\\n' > project-a/hook.txt\ngit add project-a/hook.txt\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        prepared = self.prepare()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "hook changed tree before claim persistence")
        actual_commit = git(self.root, "rev-parse", "HEAD")
        actual_tree = git(self.root, "rev-parse", "HEAD^{tree}")
        self.assertNotEqual(actual_tree, prepared["candidate_tree"])

        authorization = self.authorize(
            "$commit Recover hook-modified transaction",
            session_id="session-2",
            turn_id="turn-2",
        )
        rebound = self.run_helper(
            "prepare",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-2",
            "--authorization",
            str(authorization),
            "--claim",
            str(prepared["claim"]),
        )
        self.assertEqual(rebound["status"], "review-required")
        self.assertEqual(rebound["commit"], actual_commit)
        self.assertEqual(rebound["tree"], actual_tree)

        completed = self.run_helper(
            "review",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-2",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(rebound["token"]),
            "--reviewed-commit",
            actual_commit,
            "--reviewed-tree",
            actual_tree,
        )
        self.assertEqual(completed["status"], "committed")

    def test_failed_commit_hook_stales_for_fresh_explicit_retry(self) -> None:
        self.seed_multi_project_diff()
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        prepared = self.prepare()
        blocked = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Hook rejects commit",
            expected=2,
        )
        self.assertIn("fresh explicit $commit", str(blocked["reason"]))
        claim = json.loads(Path(str(prepared["claim"])).read_text(encoding="utf-8"))
        self.assertEqual(claim["state"], "STALE")

    def test_exact_staged_kill_window_recovers_without_duplicate_commit(self) -> None:
        self.seed_multi_project_diff()
        base_head = git(self.root, "rev-parse", "HEAD")
        prepared = self.prepare()
        git(self.root, "add", "-A")
        self.assertEqual(git(self.root, "write-tree"), prepared["candidate_tree"])
        authorization = self.authorize(
            "$commit Recover exact staged transaction",
            session_id="session-2",
            turn_id="turn-2",
        )
        rebound = self.run_helper(
            "prepare",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-2",
            "--authorization",
            str(authorization),
            "--claim",
            str(prepared["claim"]),
        )
        self.assertEqual(rebound["candidate_tree"], prepared["candidate_tree"])
        self.assertEqual(
            json.loads(Path(str(prepared["claim"])).read_text(encoding="utf-8"))[
                "state"
            ],
            "STAGED",
        )
        rejected = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-2",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Old token must not commit",
            expected=2,
        )
        self.assertIn("token does not match", str(rejected["reason"]))
        committed = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-2",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(rebound["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Recover exact staged transaction",
        )
        self.assertEqual(committed["status"], "committed")
        self.assertEqual(
            git(self.root, "rev-list", "--count", f"{base_head}..HEAD"), "1"
        )

    def test_post_commit_kill_window_reconciles_from_fresh_session(self) -> None:
        self.seed_multi_project_diff()
        base_head = git(self.root, "rev-parse", "HEAD")
        prepared = self.prepare()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "commit completed before claim persistence")
        committed_head = git(self.root, "rev-parse", "HEAD")
        self.assertEqual(git(self.root, "status", "--porcelain"), "")

        authorization = self.authorize(
            "$commit Recover completed transaction",
            session_id="session-2",
            turn_id="turn-2",
        )
        recovered = self.run_helper(
            "prepare",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-2",
            "--authorization",
            str(authorization),
            "--claim",
            str(prepared["claim"]),
        )

        self.assertEqual(recovered["status"], "committed")
        self.assertEqual(recovered["commit"], committed_head)
        self.assertEqual(recovered["tree"], prepared["candidate_tree"])
        self.assertEqual(
            git(self.root, "rev-list", "--count", f"{base_head}..HEAD"), "1"
        )
        claim = json.loads(Path(str(prepared["claim"])).read_text(encoding="utf-8"))
        self.assertEqual(claim["state"], "COMMITTED")

    def test_unrelated_history_movement_stales_instead_of_trapping_review(self) -> None:
        self.seed_multi_project_diff()
        prepared = self.prepare()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "outside transaction")
        git(self.root, "commit", "--allow-empty", "-qm", "second outside commit")
        blocked = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Must not duplicate outside history",
            expected=2,
        )
        self.assertIn("fresh explicit $commit", str(blocked["reason"]))
        claim = json.loads(Path(str(prepared["claim"])).read_text(encoding="utf-8"))
        self.assertEqual(claim["state"], "STALE")

    def test_merge_commit_is_not_an_exact_direct_child(self) -> None:
        self.seed_multi_project_diff()
        prepared = self.prepare()
        git(self.root, "restore", "--staged", "--worktree", ".")
        (self.root / "project-b" / "new.txt").unlink()
        git(self.root, "switch", "-qc", "side")
        (self.root / "side.txt").write_text("side\n", encoding="utf-8")
        git(self.root, "add", "side.txt")
        git(self.root, "commit", "-qm", "side parent")
        git(self.root, "switch", "-q", "feature/test")
        git(self.root, "merge", "--no-ff", "-qm", "merge side", "side")
        self.assertEqual(
            len(git(self.root, "rev-list", "--parents", "-n", "1", "HEAD").split()),
            3,
        )

        blocked = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Must reject merge history",
            expected=2,
        )
        self.assertIn("fresh explicit $commit", str(blocked["reason"]))
        claim = json.loads(Path(str(prepared["claim"])).read_text(encoding="utf-8"))
        self.assertEqual(claim["state"], "STALE")

    def test_active_worktree_preparation_blocks_direct_execute(self) -> None:
        self.seed_multi_project_diff()
        prepared = self.prepare()
        state = (
            self.root.parent
            / f"{self.root.name}-worktrees"
            / ".worktree-skill"
            / "integration-preparations"
        )
        state.mkdir(parents=True)
        (state / "project-managed.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "kind": "integration-commit-preparation",
                    "name": "project-managed",
                    "branch": "feature/managed",
                    "worktree": str(self.root.parent / "managed"),
                    "source_branch": "feature/test",
                    "source_ref": "refs/heads/feature/test",
                    "source_head": git(self.root, "rev-parse", "HEAD"),
                    "child_head": git(self.root, "rev-parse", "HEAD"),
                    "commit_order": ["source"],
                    "commits": [],
                    "token": "a" * 32,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        blocked = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Do not compete with Worktree",
            expected=2,
        )
        self.assertIn("Worktree owns this source ref", str(blocked["reason"]))
        self.assertNotEqual(git(self.root, "status", "--porcelain"), "")

    def test_corrupt_worktree_coordination_records_fail_closed(self) -> None:
        self.seed_multi_project_diff()
        prepared = self.prepare()
        real_index = git(self.root, "write-tree")
        state = self.root.parent / f"{self.root.name}-worktrees" / ".worktree-skill"
        for directory in ("integration-preparations", "reservations"):
            with self.subTest(directory=directory):
                record_root = state / directory
                record_root.mkdir(parents=True, exist_ok=True)
                record = record_root / "project-corrupt.json"
                record.write_text('{"schema": 1}\n', encoding="utf-8")
                blocked = self.run_helper(
                    "execute",
                    "--repo-root",
                    str(self.root),
                    "--session-id",
                    "session-1",
                    "--claim",
                    str(prepared["claim"]),
                    "--token",
                    str(prepared["token"]),
                    "--reviewed-tree",
                    str(prepared["candidate_tree"]),
                    "--message",
                    "Malformed Worktree state must block",
                    expected=2,
                )
                self.assertIn("Worktree", str(blocked["reason"]))
                self.assertEqual(git(self.root, "write-tree"), real_index)
                record.unlink()

    def test_direct_authorization_cannot_commit_a_managed_worktree_child(self) -> None:
        child = self.base / "repo-worktrees" / "managed-child"
        child.parent.mkdir()
        git(self.root, "worktree", "add", "-qb", "feature/managed", str(child))
        (child / "project-a" / "tracked.txt").write_text("managed\n", encoding="utf-8")
        child_head = git(child, "rev-parse", "HEAD")
        worktree_state.write_manifest(
            self.root,
            worktree_state.Manifest(
                schema=worktree_state.SCHEMA,
                status="active",
                name="project-managed",
                branch="feature/managed",
                primary=str(self.root),
                worktree=str(child.resolve()),
                scope="project-a",
                base=child_head,
                task_slug="managed",
                source_branch="feature/test",
                source_ref="refs/heads/feature/test",
                expected_head=child_head,
            ),
        )
        result = intent.evaluate(
            {
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(child),
                "session_id": "session-1",
                "turn_id": "turn-1",
                "prompt": "$commit Managed child must stay delegated",
                "agent_type": "root",
            }
        )
        self.assertIn("Explicit commit transaction authorization", str(result))
        authorization = transaction.expected_authorization_path(child, "session-1")
        claim = transaction.expected_claim_path(child)
        blocked = self.run_helper(
            "prepare",
            "--repo-root",
            str(child),
            "--session-id",
            "session-1",
            "--authorization",
            str(authorization),
            "--claim",
            str(claim),
            expected=2,
        )
        self.assertIn("delegated integration flow", str(blocked["reason"]))

    def test_corrupt_worktree_manifest_blocks_direct_prepare(self) -> None:
        self.seed_multi_project_diff()
        real_index = git(self.root, "write-tree")
        state = self.root.parent / f"{self.root.name}-worktrees" / ".worktree-skill"
        state.mkdir(parents=True)
        (state / "project-corrupt.json").write_text('{"schema": 4}\n', encoding="utf-8")
        authorization = self.authorize()
        blocked = self.run_helper(
            "prepare",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--authorization",
            str(authorization),
            "--claim",
            str(transaction.expected_claim_path(self.root)),
            expected=2,
        )
        self.assertIn("Worktree ownership manifest", str(blocked["reason"]))
        self.assertEqual(git(self.root, "write-tree"), real_index)

    def test_worktree_and_direct_execute_share_repository_lock(self) -> None:
        self.seed_multi_project_diff()
        prepared = self.prepare()
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(self.codex_home)
        command = [
            "python3",
            str(HELPER),
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(prepared["claim"]),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Serialize Worktree and direct commit",
        ]
        with worktree_interop.commit_repository_lock(self.root):
            process = subprocess.Popen(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            time.sleep(0.2)
            self.assertIsNone(process.poll())
        stdout, stderr = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 0, stderr or stdout)
        self.assertEqual(json.loads(stdout)["status"], "committed")

    def test_task_implementer_worker_evidence_authorizes_exact_worker_commit(
        self,
    ) -> None:
        self.seed_multi_project_diff()
        assignment_sha256 = "a" * 64
        plane = self.codex_home / "task-implementer" / "runs" / "task-1.json"
        plane.parent.mkdir(parents=True, mode=0o700)
        plane.write_text(
            json.dumps(
                {
                    "state": "running",
                    "base_commit": git(self.root, "rev-parse", "HEAD"),
                    "worker_session_sha256": transaction._digest_text("session-1"),
                    "assignment_sha256": assignment_sha256,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        plane.chmod(0o600)
        identity = transaction._identity(self.root)
        authorization = transaction.expected_authorization_path(self.root, "session-1")
        authorization.parent.mkdir(parents=True, mode=0o700)
        authorization.write_text(
            json.dumps(
                {
                    "schema": transaction.AUTH_SCHEMA,
                    "state": "AUTHORIZED",
                    "repo_root": identity["repo_root"],
                    "worktree": identity["worktree"],
                    "common_dir": identity["common_dir"],
                    "ref": identity["ref"],
                    "base_head": identity["head"],
                    "session_sha256": transaction._digest_text("session-1"),
                    "turn_sha256": assignment_sha256,
                    "prompt_sha256": assignment_sha256,
                    "owner": "task-implementer",
                    "owner_evidence_path": str(plane),
                    "owner_evidence_sha256": assignment_sha256,
                    "allow_default_branch": False,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        authorization.chmod(0o600)
        claim = transaction.expected_claim_path(self.root)
        prepared = self.run_helper(
            "prepare",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--authorization",
            str(authorization),
            "--claim",
            str(claim),
        )
        self.assertEqual(
            json.loads(authorization.read_text(encoding="utf-8"))["state"],
            "AUTHORIZED",
        )
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "commit completed before worker recovery")
        worker_evidence = json.loads(plane.read_text(encoding="utf-8"))
        worker_evidence["state"] = "completed"
        plane.write_text(
            json.dumps(worker_evidence) + "\n",
            encoding="utf-8",
        )
        plane.chmod(0o600)
        blocked = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(claim),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Do not adopt a commit after worker ownership ended",
            expected=2,
        )
        self.assertIn("worker commit ownership is stale", str(blocked["reason"]))
        worker_evidence["state"] = "running"
        plane.write_text(
            json.dumps(worker_evidence) + "\n",
            encoding="utf-8",
        )
        plane.chmod(0o600)
        committed = self.run_helper(
            "execute",
            "--repo-root",
            str(self.root),
            "--session-id",
            "session-1",
            "--claim",
            str(claim),
            "--token",
            str(prepared["token"]),
            "--reviewed-tree",
            str(prepared["candidate_tree"]),
            "--message",
            "Commit exact Task Implementer worker change",
        )
        self.assertEqual(committed["status"], "committed")


if __name__ == "__main__":
    unittest.main()
