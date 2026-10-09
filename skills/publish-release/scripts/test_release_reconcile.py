from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import Mock

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
                "repo": {"full_name": SCOPE["repo"]},
            },
            "base": {"ref": "main", "sha": BASE, "repo": {"full_name": SCOPE["repo"]}},
        }
        self.evidence = {
            "schema": "skills-review/v1",
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
            **{"expected_head": HEAD, "head": NEW, "review_id": 42, **overrides},
        )

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

    def test_dirty_wrong_checkout_or_rewritten_history_rejected(self):
        for command, result in (
            (("status", "--porcelain"), "?? unrelated.txt"),
            (("branch", "--show-current"), "other"),
            (("rev-parse", "HEAD"), HEAD),
        ):
            self.git.side_effect = (
                lambda root, *args: result
                if args == command
                else self.local(root, *args)
            )
            with self.subTest(command=command), self.assertRaises(rr.ReleaseError):
                self.reconcile()
        self.git.side_effect = rr.ReleaseError("Old head is not an ancestor")
        with self.assertRaises(rr.ReleaseError):
            self.reconcile()

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


if __name__ == "__main__":
    unittest.main()
