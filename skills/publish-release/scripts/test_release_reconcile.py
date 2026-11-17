from __future__ import annotations

import copy
import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

import release_reconcile as rr
from test_release_session import HEAD, SCOPE, state

NEW = "d" * 40
BASE = "e" * 40


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.value = state()
        self.value["waits"] = {"phase:pr": 3600}
        self.pr = {
            "number": 12,
            "state": "open",
            "draft": False,
            "head": {
                "ref": "feature/demo",
                "sha": NEW,
                "repo": {"full_name": SCOPE["repo"], "id": 1},
            },
            "base": {"ref": "main", "sha": BASE, "repo": {"full_name": SCOPE["repo"], "id": 1}},
        }
        self.evidence = {
            "schema": "skills-review/v2",
            "base_repository_id": 1,
            "head_repository_id": 1,
            "head_repository": SCOPE["repo"],
            "head_ref": "feature/demo",
            "contribution": {"verdict": "passed", "compatibility": "preserved", "evidence": ["Accepted behavior preserved"]},
            "specs": [{"project": ".", "outcome": "unchanged", "evidence": "Contract unchanged"}],
            "repository": SCOPE["repo"],
            "pr": 12,
            "head": NEW,
            "base": "main",
            "base_sha": BASE,
            "verdict": "passed",
            "unresolved_findings": 0,
            "validation": ["Focused regressions passed"],
        }
        self.review = {
            "id": 42,
            "user": {"id": 7},
            "state": "COMMENTED",
            "commit_id": NEW,
        }
        self.github = Mock()
        self.github.tag.return_value = (None, None)
        self.github.api.side_effect = self.api
        self.git = Mock(side_effect=self.local)
        self.private_root = "/private/review/repo"
        proof = patch.object(rr, "execution_proof", return_value={"repo_root": self.private_root, "head": NEW})
        self.proof = proof.start()
        self.addCleanup(proof.stop)

    def local(self, root, *args):
        return {
            ("branch", "--show-current"): "feature/demo",
            ("rev-parse", "HEAD"): NEW,
            ("status", "--porcelain"): "",
            ("merge-base", "--is-ancestor", HEAD, NEW): "",
        }[args]

    def api(self, route):
        return {
            "": {"default_branch": "main"},
            "pulls/12": self.pr,
            f"compare/{BASE}...{NEW}": {"status": "ahead"},
            "actions/variables/MERGE_OPERATOR_IDS": {"value": "[7]"},
            "pulls/12/reviews/42": {**self.review, "body": json.dumps(self.evidence)},
        }[route]

    def reconcile(self, **overrides):
        return rr.reconcile_pr(
            self.value,
            self.github,
            self.git,
            **{"expected_head": HEAD, "head": NEW, "review_id": 42, "review_task": "/private/review/task.json", **overrides},
        )

    def test_repaired_head_never_requires_source_checkout(self):
        result = self.reconcile()
        self.assertEqual(result["scope"], self.value["scope"])
        self.git.assert_not_called()
        self.assertEqual(self.proof.call_args.kwargs["ancestor"], HEAD)
        self.assertGreaterEqual(self.proof.call_count, 2)

    def test_invalid_execution_proof_blocks_reconciliation(self):
        self.proof.side_effect = rr.ReleaseError("stale execution")
        with self.assertRaises(rr.ReleaseError):
            self.reconcile()
        self.git.assert_not_called()

    def test_reviewed_descendant_preserves_identity_deadlines_and_history(self):
        before = copy.deepcopy(self.value)
        result = self.reconcile()
        self.assertEqual(self.value, before)
        self.assertEqual(result["head"], NEW)
        self.assertEqual(result["waits"], before["waits"])
        self.assertEqual(
            result["head_reconciliations"],
            [{"previous_head": HEAD, "head": NEW, "base": BASE, "review_id": 42}],
        )
        self.assertEqual(result["tag"], before["tag"])
        self.assertEqual(result["pr"], before["pr"])
        self.assertFalse(result["complete"])

    def test_unselected_or_stale_heads_and_missing_review_rejected(self):
        for args in (
            {"expected_head": NEW},
            {"head": HEAD},
            {"head": None},
            {"review_id": 0},
            {"review_id": None},
            {"review_id": True},
        ):
            with self.subTest(args=args), self.assertRaises(rr.ReleaseError):
                self.reconcile(**args)

    def test_post_proof_drift_is_rechecked_by_owner_without_private_git(self):
        self.proof.side_effect = [
            {"repo_root": self.private_root, "head": NEW},
            {"repo_root": self.private_root, "head": NEW},
            rr.ReleaseError("LFS content is not supported by private reviews yet"),
        ]
        with self.assertRaises(rr.ReleaseError):
            self.reconcile()
        self.git.assert_not_called()

    def test_recorded_or_remote_publication_blocks_reconciliation(self):
        for key in ("merge", "tag_object", "run", "complete"):
            self.value[key] = True
            with self.subTest(key=key), self.assertRaises(rr.ReleaseError):
                self.reconcile()
            self.value[key] = None
        self.github.tag.return_value = ("f" * 40, HEAD)
        with self.assertRaises(rr.ReleaseError):
            self.reconcile()

    def test_pr_closed_draft_fork_or_target_drift_rejected(self):
        original = copy.deepcopy(self.pr)
        for changes in (
            {"state": "closed"},
            {"draft": True},
            {"number": 13},
            {"head": {**original["head"], "ref": "other"}},
            {"head": {**original["head"], "repo": {"full_name": "other/repo"}}},
            {"base": {**original["base"], "ref": "other"}},
        ):
            self.pr = {**original, **changes}
            with self.subTest(changes=changes), self.assertRaises(rr.ReleaseError):
                self.reconcile()

    def test_stale_failed_or_unvalidated_review_rejected(self):
        original = self.evidence.copy()
        for changes in (
            {"head": HEAD},
            {"base_sha": HEAD},
            {"verdict": "failed"},
            {"unresolved_findings": 1},
            {"validation": []},
            {"pr": 13},
        ):
            self.evidence = {**original, **changes}
            with self.subTest(changes=changes), self.assertRaises(rr.ReleaseError):
                self.reconcile()

    def test_wrong_reviewer_commit_or_review_identity_rejected(self):
        original = self.review.copy()
        for changes in (
            {"user": {"id": 8}},
            {"state": "APPROVED"},
            {"commit_id": HEAD},
            {"id": 41},
        ):
            self.review = {**original, **changes}
            with self.subTest(changes=changes), self.assertRaises(rr.ReleaseError):
                self.reconcile()

    def test_base_advancement_or_missing_evidence_preserves_checkpoint(self):
        before = copy.deepcopy(self.value)
        for changed_route, response in (
            ("", {"default_branch": "other"}),
            (f"compare/{BASE}...{NEW}", {"status": "diverged"}),
            ("actions/variables/MERGE_OPERATOR_IDS", {"value": "[]"}),
            ("actions/variables/MERGE_OPERATOR_IDS", {"value": "unavailable"}),
        ):
            self.github.api.side_effect = (
                lambda route: response if route == changed_route else self.api(route)
            )
            with self.subTest(route=changed_route), self.assertRaises(rr.ReleaseError):
                self.reconcile()
            self.assertEqual(self.value, before)

    def test_second_remote_read_catches_racing_base_change(self):
        reads = [0]

        def api(route):
            if route == "pulls/12":
                reads[0] += 1
                if reads[0] == 2:
                    return {**self.pr, "base": {**self.pr["base"], "sha": HEAD}}
            return self.api(route)

        self.github.api.side_effect = api
        with self.assertRaises(rr.ReleaseError):
            self.reconcile()


class MergedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.value = state()
        self.value["waits"] = {"phase:verification": 12345}
        self.merged = "b" * 40
        self.tip = "e" * 40
        self.repo = {"id": 1, "full_name": SCOPE["repo"], "default_branch": "main"}
        self.pr = {
            "id": 91, "number": 12, "state": "closed", "merged": True,
            "merged_at": "2026-11-16T12:00:00Z",
            "head": {"ref": "feature/demo", "sha": NEW, "repo": self.repo.copy()},
            "base": {"ref": "main", "repo": self.repo.copy()},
        }
        self.comparison = {"status": "ahead", "merge_base_commit": {"sha": self.merged}}
        self.github = Mock()
        self.github.api.side_effect = self.api
        self.github.tag.return_value = (None, None)
        self.owner = Mock(Blocked=RuntimeError, ApiFailure=OSError)
        self.github.release_exists.return_value = False
        self.owner.GitHub.return_value.merged_commit.return_value = self.merged
        self.enterContext(patch.object(rr, "review_owner", return_value=self.owner))
        self.proof = self.enterContext(patch.object(rr, "execution_proof", return_value={
            "repo_root": "/private/review", "head": NEW, "task_sha256": "f" * 64,
        }))

    def api(self, route):
        if route == "":
            return self.repo
        if route == "pulls/12":
            return self.pr
        if route == "git/ref/heads/main":
            return {"object": {"sha": self.tip}}
        if route == f"compare/{self.merged}...{self.tip}":
            return self.comparison
        raise AssertionError(route)

    def recover(self, **overrides):
        return rr.reconcile_merged(self.value, self.github, **{
            "expected_head": HEAD, "head": NEW, "review_task": "/private/task.json", **overrides,
        })

    def test_recovery_preserves_checkpoint_and_deadline_without_review_or_merge(self):
        before = copy.deepcopy(self.value)
        result = self.recover()
        self.assertEqual(self.value, before)
        self.assertEqual(result["head"], NEW)
        self.assertEqual(result["merge"], self.merged)
        self.assertEqual(result["waits"], before["waits"])
        self.assertEqual(result["scope"], before["scope"])
        self.assertEqual(result["merged_reconciliation"]["previous_head"], HEAD)
        self.assertEqual(self.proof.call_args.kwargs["ancestor"], HEAD)
        self.assertEqual(self.proof.call_count, 2)
        self.assertEqual({call[0] for call in self.owner.GitHub.mock_calls}, {"", "().merged_commit"})

    def test_exact_repeat_is_idempotent_and_conflicting_retry_fails(self):
        self.value = self.recover()
        before = copy.deepcopy(self.value)
        self.assertEqual(self.recover(), before)
        self.value["merged_reconciliation"]["pr_id"] = 92
        with self.assertRaises(rr.ReleaseError):
            self.recover()

    def test_invalid_old_or_new_head_and_unrecorded_merge_fail(self):
        for args in ({"head": HEAD}, {"head": None}, {"head": "bad"},
                     {"expected_head": NEW}, {"expected_head": False}):
            with self.subTest(args=args), self.assertRaises(rr.ReleaseError):
                self.recover(**args)
        self.value["merge"] = self.merged
        with self.assertRaises(rr.ReleaseError):
            self.recover()

    def test_published_or_remote_tag_blocks_even_an_exact_repeat(self):
        for key in ("tag_object", "tag_payload", "run", "complete"):
            self.value[key] = True
            with self.subTest(key=key), self.assertRaises(rr.ReleaseError):
                self.recover()
            self.value[key] = None
        self.github.tag.return_value = ("c" * 40, self.merged)
        with self.assertRaises(rr.ReleaseError):
            self.recover()

    def test_tag_race_and_stale_execution_fail(self):
        self.github.tag.side_effect = [(None, None), ("c" * 40, self.merged)]
        with self.assertRaises(rr.ReleaseError):
            self.recover()

        self.github.tag.side_effect = None
        self.proof.side_effect = rr.ReleaseError("rewritten or stale execution")
        with self.assertRaises(rr.ReleaseError):
            self.recover()

    def test_existing_draft_release_lookup_failure_and_creation_race_block(self):
        for outcome in (True, rr.ReleaseError("lookup unavailable"), [False, True]):
            self.github.release_exists.side_effect = None
            self.github.release_exists.return_value = False
            if isinstance(outcome, bool):
                self.github.release_exists.return_value = outcome
            else:
                self.github.release_exists.side_effect = outcome
            with self.subTest(outcome=type(outcome).__name__), self.assertRaises(rr.ReleaseError):
                self.recover()

    def test_execution_changes_after_remote_reads_fail(self):
        self.proof.side_effect = [self.proof.return_value, {**self.proof.return_value, "task_sha256": "0" * 64}]
        with self.assertRaises(rr.ReleaseError):
            self.recover()

    def test_unmerged_or_missing_timestamp_and_wrong_pr_fail(self):
        original = copy.deepcopy(self.pr)
        for changes in ({"merged": False}, {"merged_at": None}, {"state": "open"},
                        {"number": 13}, {"number": True}, {"id": None}):
            self.pr = {**original, **changes}
            with self.subTest(changes=changes), self.assertRaises(rr.ReleaseError):
                self.recover()

    def test_repository_and_ref_drift_fail(self):
        original = copy.deepcopy(self.pr)
        for side, changes in (
            ("head", {"sha": HEAD}), ("head", {"ref": "other"}),
            ("base", {"ref": "other"}), ("head", {"repo": None}),
            ("base", {"repo": {"id": 2, "full_name": SCOPE["repo"]}}),
            ("head", {"repo": {"id": 1, "full_name": "other/repo"}}),
        ):
            self.pr = copy.deepcopy(original)
            self.pr[side].update(changes)
            with self.subTest(side=side, changes=changes), self.assertRaises(rr.ReleaseError):
                self.recover()
        self.pr = original
        self.repo["default_branch"] = "other"
        with self.assertRaises(rr.ReleaseError):
            self.recover()

    def test_result_must_be_authoritative_and_contained_at_immutable_tip(self):
        self.owner.GitHub.return_value.merged_commit.side_effect = RuntimeError("not merged")
        with self.assertRaises(rr.ReleaseError):
            self.recover()
        self.owner.GitHub.return_value.merged_commit.side_effect = None
        for comparison in ({"status": "diverged", "merge_base_commit": {"sha": self.merged}},
                           {"status": "ahead", "merge_base_commit": {"sha": HEAD}}, {}):
            self.comparison = comparison
            with self.subTest(comparison=comparison), self.assertRaises(rr.ReleaseError):
                self.recover()

    def test_identity_race_rejects_second_observation(self):
        original_api = self.api
        count = 0
        def changed_api(route):
            nonlocal count
            if route == "pulls/12":
                count += 1
                if count == 2:
                    return {**self.pr, "id": 92}
            return original_api(route)
        self.github.api.side_effect = changed_api
        with self.assertRaises(rr.ReleaseError):
            self.recover()


class ReleaseVisibilityTests(unittest.TestCase):
    def test_complete_listing_finds_draft_on_later_page(self):
        from release_session import GitHub
        github = GitHub(SCOPE)
        with patch.object(github, "api", side_effect=[
            {"permissions": {"push": True}},
            [{"tag_name": "other"}] * 100,
            [{"tag_name": "demo-v1.2.3", "draft": True}],
        ]) as api:
            self.assertTrue(github.release_exists("demo-v1.2.3"))
            self.assertEqual(api.call_args.args, ("releases?per_page=100&page=2",))

    def test_only_complete_visible_listing_establishes_absence(self):
        from release_session import GitHub
        github = GitHub(SCOPE)
        with patch.object(github, "api", side_effect=[{"permissions": {"push": True}}, []]):
            self.assertFalse(github.release_exists("demo-v1.2.3"))
        for visibility in ({}, {"permissions": None}, {"permissions": {"push": False}}):
            with self.subTest(visibility=visibility), patch.object(github, "api", return_value=visibility):
                with self.assertRaises(rr.ReleaseError):
                    github.release_exists("demo-v1.2.3")

    def test_errors_malformed_and_incomplete_listing_fail_closed(self):
        from release_session import GitHub
        github = GitHub(SCOPE)
        for response in (rr.ReleaseError("API unavailable"), {}, [{}]):
            with self.subTest(response=type(response).__name__), patch.object(github, "api", side_effect=[
                {"permissions": {"push": True}}, response,
            ]), self.assertRaises(rr.ReleaseError):
                github.release_exists("demo-v1.2.3")
        with patch.object(github, "api", side_effect=lambda route:
                          {"permissions": {"push": True}} if not route else [{"tag_name": "other"}] * 100):
            with self.assertRaisesRegex(rr.ReleaseError, "incomplete"):
                github.release_exists("demo-v1.2.3")


class MergedRecoveryCommandTests(unittest.TestCase):
    def test_cli_saves_only_verified_recovery_and_preserves_waits(self):
        import release_session as rs
        for rejected in (False, True):
            with self.subTest(rejected=rejected):
                value = state()
                value["waits"] = {"phase:verification": 12345}
                recovered = {**value, "head": NEW, "merge": "b" * 40}
                store = Mock()
                store.locked.return_value = contextlib.nullcontext()
                store.select.return_value = value
                argv = ["release_session.py", "reconcile-merged", "--project-dir", "/fixture/app",
                        "--tag", value["tag"], "--expected-head", HEAD, "--head", NEW,
                        "--review-task", "/private/task.json"]
                with patch("sys.argv", argv), patch.object(rs, "identity", return_value=SCOPE), \
                     patch.object(rs, "state_home"), patch.object(rs, "Store", return_value=store), \
                     patch.object(rs, "GitHub"), contextlib.redirect_stdout(io.StringIO()), \
                     patch.object(rs, "reconcile_merged", return_value=recovered,
                                  side_effect=rs.ReleaseError("rejected") if rejected else None) as recover:
                    self.assertEqual(rs.main(), 2 if rejected else 0)
                if rejected:
                    store.save.assert_not_called()
                else:
                    store.save.assert_called_once_with(recovered)
                    self.assertEqual(recovered["waits"]["phase:verification"], 12345)
                    self.assertEqual(recover.call_args.kwargs["expected_head"], HEAD)


class SuccessorTests(unittest.TestCase):
    def setUp(self):
        self.value = state()
        self.merged = "b" * 40
        self.value.update(
            merge=self.merged,
            qualification={"profile_digest": "f" * 64, "operations": {}},
            waits={"phase:pr": 123, "phase:verification": 456},
        )
        self.repo = {"id": 1, "full_name": SCOPE["repo"], "default_branch": "main"}
        self.original = {
            "id": 91,
            "number": 12,
            "state": "closed",
            "merged": True,
            "merged_at": "2026-11-17T01:00:00Z",
            "head": {"ref": "feature/demo", "sha": HEAD, "repo": self.repo},
            "base": {"ref": "main", "sha": BASE, "repo": self.repo},
        }
        self.successor = {
            "id": 92,
            "number": 13,
            "state": "open",
            "draft": False,
            "head": {"ref": "feature/fix", "sha": NEW, "repo": self.repo},
            "base": {"ref": "main", "sha": BASE, "repo": self.repo},
        }
        self.evidence = {
            "schema": "skills-review/v2",
            "repository": SCOPE["repo"],
            "pr": 13,
            "head": NEW,
            "base": "main",
            "base_sha": BASE,
            "base_repository_id": 1,
            "head_repository_id": 1,
            "head_repository": SCOPE["repo"],
            "head_ref": "feature/fix",
            "verdict": "passed",
            "unresolved_findings": 0,
            "validation": ["Tests passed"],
            "contribution": {
                "verdict": "passed",
                "compatibility": "preserved",
                "evidence": ["Repair"],
            },
            "specs": [{"project": ".", "outcome": "aligned", "evidence": "Owner validation"}],
        }
        self.review = {
            "id": 42,
            "state": "COMMENTED",
            "user": {"id": 7},
            "commit_id": NEW,
            "body": json.dumps(self.evidence),
        }
        self.github = Mock()
        self.github.api.side_effect = self.api
        self.github.tag.return_value = (None, None)
        self.github.release_exists.return_value = False
        self.owner = rr.review_owner()
        self.enterContext(patch.object(rr, "review_owner", return_value=self.owner))
        api = self.enterContext(patch.object(self.owner, "GitHub"))
        self.merge_read = api.return_value.merged_commit
        self.merge_read.return_value = self.merged
        self.proof = self.enterContext(
            patch.object(
                rr,
                "execution_proof",
                return_value={
                    "repo_root": "/private/followup",
                    "head": NEW,
                    "task_sha256": "f" * 64,
                },
            )
        )

    def api(self, route):
        rows = {
            "": self.repo,
            "pulls/12": self.original,
            "pulls/13": self.successor,
            "git/ref/heads/main": {"object": {"sha": BASE}},
            f"compare/{self.merged}...{BASE}": {
                "status": "ahead",
                "merge_base_commit": {"sha": self.merged},
            },
            f"compare/{BASE}...{NEW}": {"status": "ahead", "merge_base_commit": {"sha": BASE}},
            "actions/variables/MERGE_OPERATOR_IDS": {"value": "[7]"},
            "pulls/13/reviews/42": self.review,
        }
        return copy.deepcopy(rows[route])

    def reconcile(self, **changes):
        return rr.reconcile_successor(
            self.value,
            self.github,
            **{
                "expected_merge": self.merged,
                "pr": 13,
                "branch": "feature/fix",
                "head": NEW,
                "review_id": 42,
                "review_task": "/private/new-task.json",
                **changes,
            },
        )

    def test_success_archives_selection_preserves_deadlines_and_uses_new_owner(self):
        before = copy.deepcopy(self.value)
        result = self.reconcile()
        self.assertEqual(self.value, before)
        for key in ("scope", "tag", "workflow", "assets", "base", "waits"):
            self.assertEqual(result[key], before[key])
        self.assertEqual(
            (result["pr"], result["branch"], result["head"], result["merge"]),
            (13, "feature/fix", NEW, None),
        )
        self.assertNotIn("qualification", result)
        old = result["successor_reconciliations"][0]["previous"]
        for key in ("pr", "branch", "head", "merge", "qualification"):
            self.assertEqual(old[key], before[key])
        self.assertEqual(self.proof.call_args.args[0]["pr"], 13)
        self.assertEqual(self.proof.call_args.kwargs["ancestor"], self.merged)
        self.assertEqual(self.proof.call_count, 2)

    def test_exact_retry_is_idempotent_and_conflicting_retry_fails(self):
        self.value = self.reconcile()
        before = copy.deepcopy(self.value)
        self.assertEqual(self.reconcile(), before)
        with self.assertRaises(rr.ReleaseError):
            self.reconcile(review_id=43)
        self.value["successor_reconciliations"][-1]["evidence"]["pr_id"] = 999
        with self.assertRaises(rr.ReleaseError):
            self.reconcile()

    def test_missing_or_wrong_selection_rejected(self):
        for changes in (
            {"expected_merge": HEAD},
            {"expected_merge": False},
            {"pr": 12},
            {"pr": True},
            {"head": "bad"},
            {"branch": ""},
            {"review_id": 0},
        ):
            with self.subTest(changes=changes), self.assertRaises(rr.ReleaseError):
                self.reconcile(**changes)
        self.value["merge"] = None
        with self.assertRaises(rr.ReleaseError):
            self.reconcile()

    def test_publication_or_uncertain_qualification_rejected(self):
        for key in ("tag_object", "tag_payload", "run", "complete"):
            with (
                self.subTest(key=key),
                patch.dict(self.value, {key: True}),
                self.assertRaises(rr.ReleaseError),
            ):
                self.reconcile()

    def test_malformed_history_and_changed_default_rejected(self):
        for history in ("bad", [None], [{"next": {}}]):
            with (
                patch.dict(self.value, {"successor_reconciliations": history}),
                self.assertRaises(rr.ReleaseError),
            ):
                self.reconcile()
        with patch.dict(self.repo, {"default_branch": "other"}), self.assertRaises(rr.ReleaseError):
            self.reconcile()
        for qualification in (
            {"operations": {"ci.yml": {"state": "prepared"}}},
            {"operations": {}, "proof": {}},
            {"operations": []},
            "bad",
        ):
            with (
                self.subTest(qualification=qualification),
                patch.dict(self.value, {"qualification": qualification}),
                self.assertRaises(rr.ReleaseError),
            ):
                self.reconcile()

    def test_remote_tag_and_draft_release_creation_races_rejected(self):
        for target, outcomes in (
            (self.github.tag, [(None, None), ("c" * 40, self.merged)]),
            (self.github.release_exists, [False, True]),
        ):
            target.side_effect = outcomes
            with self.assertRaises(rr.ReleaseError):
                self.reconcile()
            target.side_effect = None
        self.github.release_exists.side_effect = rr.ReleaseError("visibility unavailable")
        with self.assertRaises(rr.ReleaseError):
            self.reconcile()

    def test_stale_review_execution_original_merge_and_fork_rejected(self):
        with patch.dict(self.review, {"commit_id": HEAD}), self.assertRaises(rr.ReleaseError):
            self.reconcile()
        self.merge_read.return_value = HEAD
        with self.assertRaises(rr.ReleaseError):
            self.reconcile()
        self.merge_read.return_value = self.merged
        self.proof.side_effect = rr.ReleaseError("new grant unavailable")
        with self.assertRaises(rr.ReleaseError):
            self.reconcile()
        self.proof.side_effect = None
        with (
            patch.dict(self.successor["head"], {"repo": {"id": 2, "full_name": "fork/demo"}}),
            self.assertRaises(rr.ReleaseError),
        ):
            self.reconcile()
        for changes in ({"state": "closed"}, {"draft": True}):
            with patch.dict(self.successor, changes), self.assertRaises(rr.ReleaseError):
                self.reconcile()

    def test_ancestry_and_observation_races_rejected(self):
        def diverged(route):
            result = self.api(route)
            if route.startswith("compare/"):
                result["merge_base_commit"]["sha"] = NEW
            return result

        self.github.api.side_effect = diverged
        with self.assertRaises(rr.ReleaseError):
            self.reconcile()
        reads = [0]

        def changed(route):
            result = self.api(route)
            if route == "pulls/13":
                reads[0] += 1
                if reads[0] == 2:
                    result["id"] = 999
            return result

        self.github.api.side_effect = changed
        with self.assertRaises(rr.ReleaseError):
            self.reconcile()

    def test_cli_saves_only_verified_successor_without_renewing_waits(self):
        import release_session as rs

        for rejected in (False, True):
            result = {**self.value, "pr": 13, "head": NEW, "branch": "feature/fix", "merge": None}
            store = Mock()
            store.locked.return_value = contextlib.nullcontext()
            store.select.return_value = self.value
            argv = [
                "release_session.py",
                "reconcile-successor",
                "--project-dir",
                "/fixture/app",
                "--tag",
                self.value["tag"],
                "--expected-merge",
                self.merged,
                "--head",
                NEW,
                "--pr",
                "13",
                "--branch",
                "feature/fix",
                "--review-id",
                "42",
                "--review-task",
                "/private/task.json",
            ]
            with (
                patch("sys.argv", argv),
                patch.object(rs, "identity", return_value=SCOPE),
                patch.object(rs, "state_home"),
                patch.object(rs, "Store", return_value=store),
                patch.object(rs, "GitHub"),
                contextlib.redirect_stdout(io.StringIO()),
                patch.object(
                    rs,
                    "reconcile_successor",
                    return_value=result,
                    side_effect=rr.ReleaseError("rejected") if rejected else None,
                ) as transition,
            ):
                self.assertEqual(rs.main(), 2 if rejected else 0)
            if rejected:
                store.save.assert_not_called()
            else:
                store.save.assert_called_once_with(result)
                self.assertEqual(result["waits"], self.value["waits"])
                self.assertEqual(transition.call_args.kwargs["expected_merge"], self.merged)

if __name__ == "__main__":
    unittest.main()
