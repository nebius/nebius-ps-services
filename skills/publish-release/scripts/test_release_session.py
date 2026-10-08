from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

import release_session as rs

HEAD = "a" * 40
MERGED = "b" * 40
OBJECT = "c" * 40
SCOPE = {
    "root": "/fixture",
    "project": "app",
    "host": "github.com",
    "repo": "example/demo",
    "origin_digest": "d" * 64,
}


def state():
    return dict(
        schema=rs.SCHEMA,
        scope=SCOPE,
        tag="demo-v1.2.3",
        branch="feature/demo",
        base="main",
        workflow="release.yml",
        assets=["*.whl"],
        pr=12,
        head=HEAD,
        merge=None,
        tag_object=None,
        run=None,
        waits={},
        complete=False,
    )


def pr(**changes):
    result = dict(
        number=12,
        state="OPEN",
        isDraft=False,
        headRefName="feature/demo",
        headRefOid=HEAD,
        baseRefName="main",
        headRepositoryOwner={"login": "example"},
        headRepository={"name": "demo"},
        reviewDecision="APPROVED",
        mergeStateStatus="CLEAN",
        mergeable="MERGEABLE",
        mergeCommit={"oid": MERGED},
        statusCheckRollup=[{"status": "COMPLETED", "conclusion": "SUCCESS"}],
        url="https://github.com/example/demo/pull/12",
    )
    result.update(changes)
    return result


class GitHubApiTests(unittest.TestCase):
    def test_repository_and_nested_endpoints(self):
        github = rs.GitHub(SCOPE)
        for endpoint, expected in (
            ("", "repos/example/demo"),
            ("pulls/12", "repos/example/demo/pulls/12"),
        ):
            with (
                self.subTest(endpoint=endpoint),
                patch.object(
                    rs, "command", return_value='{"default_branch": "main"}'
                ) as command,
            ):
                self.assertEqual(github.api(endpoint), {"default_branch": "main"})
                command.assert_called_once_with(
                    ["gh", "api", "--hostname", "github.com", expected],
                    Path(SCOPE["root"]),
                )


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.value = state()
        self.github = Mock()
        self.github.pr.return_value = pr()
        self.github.queue.return_value = {
            "number": 12,
            "state": "OPEN",
            "headRefOid": HEAD,
            "baseRefName": "main",
            "isInMergeQueue": True,
            "mergeQueueEntry": {
                "id": "queue-id",
                "enqueuedAt": "2026-10-02T00:00:00Z",
                "pullRequest": {"number": 12},
            },
        }

    def test_required_approval_is_visible(self):
        self.github.pr.return_value = pr(reviewDecision="REVIEW_REQUIRED")
        result = rs.observation(self.value, self.github, "pr")
        self.assertEqual(result["gate"], "approval:pr:12")
        self.assertIn("/pull/12", result["url"])

    def test_fixed_timeout_and_explicit_new_attempt(self):
        result = {
            "status": "waiting",
            "gate": "approval:pr:12",
            "url": "https://example.invalid/review",
        }
        first = rs.timed_observation(self.value, result, "pr", 100)
        self.assertEqual(first["deadline"], 700)
        self.assertEqual(
            rs.timed_observation(self.value, result, "pr", 699)["status"], "waiting"
        )
        expired = rs.timed_observation(self.value, result, "pr", 700)
        self.assertEqual(expired["status"], "timed_out")
        self.assertIn("--resume --tag demo-v1.2.3", expired["resume"])
        self.value["waits"] = {}
        self.assertEqual(
            rs.timed_observation(self.value, result, "pr", 800)["deadline"], 1400
        )

    def test_phase_budget_does_not_slide_with_different_gates(self):
        first = rs.timed_observation(
            self.value, {"status": "waiting", "gate": "checks:pr"}, "pr", 0
        )
        next_gate = rs.timed_observation(
            self.value, {"status": "waiting", "gate": "approval:pr:12"}, "pr", 3500
        )
        self.assertEqual(first["deadline"], next_gate["deadline"])
        self.assertEqual(next_gate["deadline"], 3600)

    def test_wait_polls_fifteen_seconds_and_prints_minute_progress(self):
        self.github.pr.return_value = pr(reviewDecision="REVIEW_REQUIRED")
        now = [0]
        sleeps = []
        messages = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        result = rs.watch(
            self.value,
            self.github,
            Mock(),
            "pr",
            clock=lambda: now[0],
            sleep=sleep,
            emit=lambda text, **_: messages.append((now[0], text)),
        )
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(sum(sleeps), 600)
        self.assertEqual(set(sleeps), {15})
        self.assertEqual([t for t, _ in messages], list(range(0, 600, 60)))
        self.assertIn("Waiting for approval on GitHub", messages[0][1])

    def test_remote_call_timeout_is_capped_by_wait_deadline(self):
        token = rs.REMOTE_DEADLINE.set(105)
        try:
            with (
                patch.object(rs.time, "monotonic", return_value=100),
                patch.object(rs.subprocess, "run") as run,
            ):
                run.return_value.returncode = 0
                run.return_value.stdout = "{}"
                rs.command(["gh", "api", "test"], Path("."))
                self.assertEqual(run.call_args.kwargs["timeout"], 5)
        finally:
            rs.REMOTE_DEADLINE.reset(token)

    def test_slow_observation_cannot_extend_approval_deadline(self):
        now = [0]
        pending = {
            "status": "waiting",
            "gate": "approval:pr:12",
            "url": "https://example.invalid/review",
        }

        def observe(*_):
            if now[0] == 0:
                return pending
            now[0] = 601
            return {"status": "ready"}

        def sleep(seconds):
            now[0] += seconds

        with patch.object(rs, "observation", side_effect=observe):
            result = rs.watch(
                self.value,
                self.github,
                Mock(),
                "pr",
                clock=lambda: now[0],
                sleep=sleep,
                emit=Mock(),
            )
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(result["deadline"], 600)

    def test_approval_arrives_during_wait(self):
        self.github.pr.side_effect = [pr(reviewDecision="REVIEW_REQUIRED"), pr()]
        now = [0]

        def sleep(seconds):
            now[0] += seconds

        result = rs.watch(
            self.value,
            self.github,
            Mock(),
            "pr",
            clock=lambda: now[0],
            sleep=sleep,
            emit=Mock(),
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(now[0], 15)
        self.assertIn("merge-pr", result["next"])

    def test_drift_and_conflicts_never_merge(self):
        for change in (
            {"headRefOid": "e" * 40},
            {"baseRefName": "other"},
            {"headRepositoryOwner": {"login": "fork"}},
        ):
            self.github.pr.return_value = pr(**change)
            with self.assertRaises(rs.ReleaseError):
                rs.observation(self.value, self.github, "pr")
        for change in (
            {"reviewDecision": "CHANGES_REQUESTED"},
            {"isDraft": True},
            {"mergeable": "CONFLICTING"},
            {"state": "CLOSED"},
        ):
            self.github.pr.return_value = pr(**change)
            self.assertEqual(
                rs.observation(self.value, self.github, "pr")["status"], "blocked"
            )

    def test_pending_failed_and_unknown_checks(self):
        for check in (
            {"status": "IN_PROGRESS"},
            {"status": "COMPLETED", "conclusion": "NEW_UNKNOWN"},
        ):
            self.github.pr.return_value = pr(statusCheckRollup=[check])
            self.assertEqual(
                rs.observation(self.value, self.github, "pr")["status"], "waiting"
            )
        self.github.pr.return_value = pr(
            statusCheckRollup=[{"status": "COMPLETED", "conclusion": "FAILURE"}]
        )
        self.assertEqual(
            rs.observation(self.value, self.github, "pr")["status"], "blocked"
        )

    def test_queue_is_waiting_not_published(self):
        result = rs.observation(self.value, self.github, "merge")
        self.assertEqual(result, {"status": "waiting", "gate": "merge_queue"})

    def test_queue_merges_between_pr_and_queue_reads(self):
        self.github.queue.return_value.update(
            state="MERGED",
            mergeCommit={"oid": MERGED},
            isInMergeQueue=False,
            mergeQueueEntry=None,
        )
        self.github.api.return_value = {"status": "ahead"}
        result = rs.observation(self.value, self.github, "merge")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.value["merge"], MERGED)

    def test_removed_queue_entry_blocks_instead_of_waiting(self):
        self.github.queue.return_value.update(
            isInMergeQueue=False, mergeQueueEntry=None
        )
        result = rs.observation(self.value, self.github, "merge")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("do not re-enqueue", result["reason"])

    def test_squashed_merge_and_advanced_default(self):
        self.github.pr.return_value = pr(state="MERGED")
        self.github.api.return_value = {"status": "ahead"}
        result = rs.observation(self.value, self.github, "merge")
        self.assertEqual(self.value["merge"], MERGED)
        self.assertEqual(result["commit"], MERGED)
        self.assertNotEqual(self.value["head"], MERGED)
        self.github.api.return_value = {"status": "diverged"}
        with self.assertRaises(rs.ReleaseError):
            rs.observation(self.value, self.github, "merge")

    def release(
        self,
        *,
        status="completed",
        conclusion="success",
        deployments=None,
        assets=None,
        draft=False,
    ):
        self.value.update(merge=MERGED, tag_object=OBJECT)
        self.github.pr.return_value = pr(state="MERGED")
        self.github.tag.return_value = (OBJECT, MERGED)
        run = dict(
            id=99,
            head_sha=MERGED,
            head_branch=self.value["tag"],
            event="push",
            status=status,
            conclusion=conclusion,
            html_url="https://github.com/example/demo/actions/runs/99",
        )
        replies = [{"workflow_runs": [run]}, deployments or []]
        if not deployments and status == "completed" and conclusion == "success":
            replies += [
                {
                    "id": 42,
                    "draft": draft,
                    "tag_name": self.value["tag"],
                    "html_url": "https://github.com/example/demo/releases/tag/demo-v1.2.3",
                },
                assets
                if assets is not None
                else [{"id": 1, "name": "demo-1.2.3.whl", "size": 123, "digest": None}],
            ]
        self.github.api.side_effect = replies

    def test_release_ready_still_requires_artifact_verification(self):
        self.release()
        result = rs.observation(self.value, self.github, "release")
        self.assertEqual(result["status"], "ready")
        self.assertFalse(self.value["complete"])
        self.assertEqual(self.value["run"], 99)

    def test_lost_push_response_recovers_exact_remote_tag(self):
        self.release()
        result = rs.observation(self.value, self.github, "release")
        self.assertEqual(result["status"], "ready")
        self.github.tag.assert_called_once_with(self.value["tag"])

    def test_tag_collision_or_lightweight_tag_blocked(self):
        self.release()
        for remote in (
            ("f" * 40, MERGED),
            (OBJECT, HEAD),
            (OBJECT, None),
            (None, None),
        ):
            self.github.tag.return_value = remote
            with self.assertRaises(rs.ReleaseError):
                rs.observation(self.value, self.github, "release")

    def test_environment_approval_gets_its_own_deadline(self):
        self.release(status="waiting", deployments=[{"environment": {"id": 3}}])
        result = rs.timed_observation(
            self.value,
            rs.observation(self.value, self.github, "release"),
            "release",
            800,
        )
        self.assertEqual(result["deadline"], 1400)
        self.assertIn("approval:environment", result["gate"])

    def test_partial_and_reordered_environment_approvals_keep_deadline(self):
        for now, ids in ((0, [1, 2]), (590, [2, 1]), (599, [2]), (600, [2])):
            self.release(
                status="waiting", deployments=[{"environment": {"id": i}} for i in ids]
            )
            result = rs.timed_observation(
                self.value,
                rs.observation(self.value, self.github, "release"),
                "release",
                now,
            )
            self.assertEqual(result["deadline"], 600)
        self.assertEqual(result["status"], "timed_out")

    def test_failed_workflow_draft_and_missing_assets(self):
        self.release(conclusion="failure")
        self.assertEqual(
            rs.observation(self.value, self.github, "release")["status"], "blocked"
        )
        self.release(draft=True)
        with self.assertRaises(rs.ReleaseError):
            rs.observation(self.value, self.github, "release")
        self.release(assets=[])
        self.assertEqual(
            rs.observation(self.value, self.github, "release")["status"], "blocked"
        )

    def test_wrong_tag_workflow_never_selected(self):
        self.release()
        self.github.api.side_effect = [
            {
                "workflow_runs": [
                    {
                        "id": 99,
                        "head_sha": MERGED,
                        "head_branch": "other-v1.2.3",
                        "event": "push",
                    }
                ]
            }
        ]
        self.assertEqual(
            rs.observation(self.value, self.github, "release")["status"], "waiting"
        )


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = rs.Store(self.root, SCOPE)
        self.value = state()
        self.store.save(self.value)

    def test_roundtrip_fresh_session_and_selection(self):
        self.assertEqual(rs.Store(self.root, SCOPE).select(None), self.value)
        other = {**self.value, "tag": "demo-v1.2.4"}
        self.store.save(other)
        with self.assertRaises(rs.ReleaseError):
            self.store.select(None)
        self.assertEqual(self.store.select(other["tag"]), other)
        self.value["complete"] = True
        self.store.save(self.value)
        self.assertEqual(self.store.select(None), other)

    def test_atomic_failure_preserves_previous_checkpoint(self):
        with patch.object(
            rs.os, "replace", side_effect=OSError("simulated interruption")
        ):
            with self.assertRaises(OSError):
                self.store.save({**self.value, "complete": True})
        self.assertEqual(self.store.load(self.value["tag"]), self.value)
        self.assertFalse(list(self.store.directory.glob(".checkpoint-*")))

    def test_rejects_symlinks_permissions_schema_and_path_escape(self):
        path = self.store.path(self.value["tag"])
        path.chmod(0o644)
        with self.assertRaises(rs.ReleaseError):
            self.store.load(self.value["tag"])
        path.chmod(0o600)
        value = copy.deepcopy(self.value)
        value["schema"] = "unknown"
        self.store.save(value)
        with self.assertRaises(rs.ReleaseError):
            self.store.load(self.value["tag"])
        path.unlink()
        path.symlink_to(self.root / "target")
        with self.assertRaises((rs.ReleaseError, OSError)):
            self.store.save(self.value)
        with self.assertRaises(rs.ReleaseError):
            self.store.path("../outside")

    def test_preparation_reservation_spans_projects_in_same_checkout(self):
        self.value["pr"] = None
        self.store.save(self.value)
        sibling = rs.Store(self.root, {**SCOPE, "project": "other"})
        with self.assertRaises(rs.ReleaseError):
            sibling.require_preparation_available("other-v1.0.0")
        self.value["pr"] = 12
        self.store.save(self.value)
        sibling.require_preparation_available("other-v1.0.0")

    def test_concurrent_observers_rejected(self):
        with self.store.locked():
            with self.assertRaises(rs.ReleaseError):
                with rs.Store(self.root, SCOPE).locked():
                    self.fail("acquired concurrent lock")

    def test_remote_urls_do_not_accept_secrets_or_other_destinations(self):
        self.assertEqual(
            rs.remote_target("git@github.com:example/demo.git"),
            ("github.com", "example/demo"),
        )
        self.assertEqual(
            rs.remote_target("https://github.com/example/demo.git"),
            ("github.com", "example/demo"),
        )
        for url in (
            "https://user:secret@github.com/example/demo",
            "https://token@github.com/example/demo",
            "file:///tmp/repo",
            "https://github.com/example/demo?token=secret",
        ):
            with self.assertRaises(rs.ReleaseError):
                rs.remote_target(url)

    def test_artifact_version_digest_and_size(self):
        wheel = self.root / "demo-1.2.3.whl"
        with ZipFile(wheel, "w") as archive:
            archive.writestr(
                "demo-1.2.3.dist-info/METADATA", "Name: demo\nVersion: 1.2.3\n"
            )
        asset = dict(
            name=wheel.name,
            size=wheel.stat().st_size,
            digest="sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest(),
        )
        rs.verify_downloads(self.root, [asset], self.value)
        for wrong in ({**asset, "digest": "sha256:" + "0" * 64}, {**asset, "size": 0}):
            with self.assertRaises(rs.ReleaseError):
                rs.verify_downloads(self.root, [wrong], self.value)
        with self.assertRaises(rs.ReleaseError):
            rs.verify_downloads(
                self.root, [asset], {**self.value, "tag": "demo-v1.2.4"}
            )


class CommandFlowTests(unittest.TestCase):
    """Real local Git, mocked GitHub; fixture commits are not owner-runtime proof."""

    def test_private_cli_preparation_tag_loss_and_resume(self):
        import contextlib
        import io
        import sys

        from test_publish_release_doer import GitFixture

        fixture = GitFixture()
        self.addCleanup(fixture.close)
        private = fixture.root / "private"
        private.mkdir(mode=0o700)
        scope = {**SCOPE, "root": str(fixture.work), "project": "."}

        def identify(project):
            root = fixture.git_output(project, "rev-parse", "--show-toplevel")
            return {**scope, "root": root}

        remote = Mock()
        remote.api.side_effect = (
            lambda endpoint: {"default_branch": "main"}
            if not endpoint
            else {"status": "ahead"}
        )

        def call(*args):
            with (
                patch.object(
                    sys,
                    "argv",
                    ["release_session.py", *args, "--project-dir", str(fixture.work)],
                ),
                patch.object(rs, "identity", side_effect=identify),
                patch.object(rs, "state_home", return_value=private),
                patch.object(rs, "GitHub", return_value=remote),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                code = rs.main()
            self.assertEqual(code, 0, output.getvalue())
            return output.getvalue()

        call(
            "open",
            "--tag",
            "demo-v1.2.3",
            "--branch",
            "feature/demo",
            "--base",
            "main",
            "--workflow",
            "release.yml",
            "--asset",
            "*.whl",
        )
        fixture.git(fixture.work, "switch", "-c", "feature/demo")
        (fixture.work / "user.txt").write_text("existing user change")
        head = fixture.git_output(fixture.work, "rev-parse", "HEAD")
        call("prepare", "--tag", "demo-v1.2.3")
        self.assertEqual(fixture.git_output(fixture.work, "rev-parse", "HEAD"), head)
        # Simulate the external commit/PR owner in the fixture, then bind its result.
        fixture.git(fixture.work, "add", "-A")
        fixture.git(fixture.work, "commit", "-m", "Reviewed complete candidate")
        commit = fixture.git_output(fixture.work, "rev-parse", "HEAD")
        fixture.git(fixture.work, "push", "origin", "HEAD:refs/heads/feature/demo")
        remote.pr.return_value = pr(headRefOid=commit)
        call("bind-pr", "--tag", "demo-v1.2.3", "--pr", "12")
        remote.pr.return_value = pr(
            headRefOid=commit, state="MERGED", mergeCommit={"oid": commit}
        )
        call("observe", "--tag", "demo-v1.2.3", "--phase", "merge")
        fixture.git(fixture.work, "push", "origin", "HEAD:refs/heads/main")
        clone = fixture.root / "clone"
        fixture.git(fixture.root, "clone", str(fixture.origin), str(clone))
        fixture._configure_identity(clone)
        fixture.git(clone, "switch", "--detach", commit)
        fixture.git(clone, "tag", "-a", "demo-v1.2.3", "-m", "Release demo-v1.2.3")
        original = fixture.git_output(clone, "rev-parse", "refs/tags/demo-v1.2.3")
        call("record-tag", "--tag", "demo-v1.2.3", "--checkout", str(clone))
        # A distinct fresh clone has no tag object; restore must produce identical bytes.
        fresh = fixture.root / "fresh"
        fixture.git(fixture.root, "clone", str(fixture.origin), str(fresh))
        fixture.git(fresh, "switch", "--detach", commit)
        call("resume", "--tag", "demo-v1.2.3")
        call("restore-tag", "--tag", "demo-v1.2.3", "--checkout", str(fresh))
        self.assertEqual(
            fixture.git_output(fresh, "rev-parse", "refs/tags/demo-v1.2.3"), original
        )
        self.assertNotEqual(
            fixture.git(
                fixture.work,
                "show-ref",
                "--verify",
                "refs/tags/demo-v1.2.3",
                check=False,
            ).returncode,
            0,
        )
        self.assertEqual(
            fixture.git_output(fixture.work, "branch", "--show-current"), "feature/demo"
        )


if __name__ == "__main__":
    unittest.main()
