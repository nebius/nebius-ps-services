#!/usr/bin/env python3
"""Offline behavioral tests; never contact GitHub or mint credentials."""

import copy
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import merge_gate as gate

HEAD, BASE = "a" * 40, "b" * 40
REPO = "example/project"
POLICY = {
    "schema": "merge-policy/v1",
    "ci_workflows": [{"file": "ci.yml", "paths": ["**"]}],
    "merge_workflow": "skills-merge-pr.yml",
    "completion_workflow": "merge-completion.yml",
}
ENV = {
    "MERGE_OPERATOR_IDS": "[123]",
    "GITHUB_RUN_ID": "100",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_SHA": BASE,
    "GITHUB_ACTIONS": "true",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": REPO,
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_ACTOR_ID": "123",
}


def evidence():
    return {
        "schema": gate.SCHEMA,
        "verdict": "passed",
        "unresolved_findings": 0,
        "repository": REPO,
        "pr": 1,
        "head": HEAD,
        "base": "main",
        "base_sha": BASE,
        "base_repository_id": 1,
        "head_repository_id": 1,
        "head_repository": REPO,
        "head_ref": "feature",
        "contribution": {"verdict": "passed", "compatibility": "preserved", "evidence": ["Existing consumer regression passed."]},
        "specs": [{"project": ".", "outcome": "unchanged", "evidence": "Accepted contract already covers this repair."}],
        "validation": ["Focused tests passed; full diff reviewed locally."],
    }


class API:
    repo = REPO

    def __init__(self):
        self.pr = {
            "number": 1,
            "state": "open",
            "draft": False,
            "merged": False,
            "head": {"sha": HEAD, "ref": "feature", "repo": {"full_name": REPO, "id": 1}},
            "created_at": "2026-01-01T00:00:00Z",
            "base": {"sha": BASE, "ref": "main", "repo": {"full_name": REPO, "id": 1}},
            "user": {"id": 123},
            "changed_files": 1,
            "commits": 1,
            "mergeable": True,
            "mergeable_state": "blocked",
        }
        self.review = {
            "id": 10,
            "user": {"id": 123},
            "state": "COMMENTED",
            "commit_id": HEAD,
            "body": json.dumps(evidence()),
        }
        self.reviews = [self.review]
        self.files = [{"filename": "code.py", "status": "modified"}]
        self.runs = [
            {
                "id": 42,
                "path": ".github/workflows/ci.yml",
                "event": "pull_request",
                "head_sha": HEAD,
                "head_branch": "feature",
                "repository": {"full_name": REPO},
                "created_at": "2026-01-01T00:01:00Z",
                "check_suite_id": 50,
                "pull_requests": [{"number": 1}],
                "head_repository": {"full_name": REPO},
                "run_number": 1,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "success",
            }
        ]
        self.checks = [
            {"name": "tests", "status": "completed", "conclusion": "success"}
        ]
        self.statuses = [{"id": 1, "context": gate.CI_CONTEXT, "state": "success"}]
        self.rules = []
        self.effects = []
        self.comparison = "ahead"
        self.unresolved = False
        self.on_approve = None
        self.intent = None
        self.open_prs = [self.pr]
        self.suite = {
            "app": {"id": 15368},
            "repository": {"full_name": REPO},
            "head_sha": HEAD,
            "head_branch": "feature",
        }
        self.orchestration_suites = {}
        self.orchestration_runs = {}
        self.suite_reads = []
        self.intents = [
            {
                "id": 900,
                "name": f"merge-intent-1-{HEAD}-1",
                "expired": False,
                "workflow_run": {"id": 100},
            }
        ]

    def repo_info(self):
        return {"default_branch": "main"}

    def merged_commit(self, number, head, base):
        assert (number, head, base) == (1, HEAD, "main")
        assert self.pr["merged"]
        return BASE

    def api(self, path, method="GET", data=None):
        if method != "GET":
            self.effects.append((path, method, copy.deepcopy(data)))
            if path.endswith("/reviews"):
                self.reviews.append(
                    {
                        "id": 11,
                        "user": {"id": gate.ACTIONS_BOT_ID},
                        "state": "APPROVED",
                        "commit_id": HEAD,
                    }
                )
                if self.on_approve:
                    self.on_approve(self)
            return {"id": "request-1"}
        if path == "actions/artifacts/900":
            return self.intents[0]
        if path == "pulls/1":
            return copy.deepcopy(self.pr)
        if path == "check-suites/50":
            return self.suite
        if path.startswith("check-suites/"):
            suite_id = int(path.rsplit("/", 1)[1])
            self.suite_reads.append(suite_id)
            return self.orchestration_suites[suite_id]
        if path == "pulls/1/reviews/10":
            return copy.deepcopy(self.review)
        if path.startswith("compare/"):
            return {"status": self.comparison}
        raise AssertionError(path)

    def pages(self, path, key=None):
        if path.startswith("pulls?state=open&"):
            return self.open_prs
        if path == "actions/runs/100/artifacts":
            return self.intents
        if path == "pulls/1/files":
            return self.files
        if path == "pulls/1/reviews":
            return self.reviews
        if path.startswith("actions/runs?check_suite_id="):
            return self.orchestration_runs[int(path.split("=", 1)[1])]
        if path.startswith("actions/runs?"):
            return self.runs
        if "/check-runs?" in path:
            return self.checks
        if path.endswith("/statuses"):
            return self.statuses
        if path.startswith("rules/branches/"):
            return self.rules
        raise AssertionError(path)

    def threads(self, number):
        gate.require(not self.unresolved, "Unresolved review threads")

    def artifact(self, artifact_id, filename):
        assert artifact_id == 900 and filename == "intent.json"
        return self.intent


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.api = API()
        self.api.intent = gate.admitted_intent(self.api, 1, HEAD, 10, "squash", POLICY)

    def check(self):
        return gate.snapshot(self.api, 1, HEAD, POLICY, 10)

    def execute(self):
        return gate.execute(self.api, 1, HEAD, 10, "squash", POLICY)

    def orchestration(self, *, event="pull_request_target", workflow="merge_workflow"):
        check = {
            "name": f"required-ci-status (1, {HEAD})",
            "head_sha": HEAD,
            "app": {"id": 15368},
            "check_suite": {"id": 60},
            "status": "completed",
            "conclusion": "cancelled",
        }
        suite = copy.deepcopy(self.api.suite)
        suite["id"] = 60
        run = {
            **self.api.runs[0],
            "id": 43,
            "path": ".github/workflows/" + POLICY[workflow],
            "event": event,
            "check_suite_id": 60,
            "pull_requests": [{"number": 1, "base": {"ref": "main"}}],
            "status": "completed",
            "conclusion": "cancelled",
        }
        if event != "pull_request_target":
            suite["head_branch"] = run["head_branch"] = "main"
        self.api.checks.append(check)
        self.api.orchestration_suites[60] = suite
        self.api.orchestration_runs[60] = [run]
        return check, suite, run

    def test_orchestration_status_is_not_its_own_ci_gate(self):
        for status, conclusion in (("completed", "cancelled"), ("in_progress", None)):
            for event in (
                "pull_request_target",
                "workflow_dispatch",
                "workflow_run",
                "schedule",
            ):
                with self.subTest(status=status, event=event):
                    self.api = API()
                    check, _, _ = self.orchestration(event=event)
                    check.update(status=status, conclusion=conclusion)
                    self.check()

    def test_completion_checks_are_also_orchestration(self):
        check, _, _ = self.orchestration(
            event="workflow_run", workflow="completion_workflow"
        )
        check["name"] = "dispatch-result-ci (1, anything)"
        self.check()

    def test_orchestration_provenance_is_cached_only_within_one_evaluation(self):
        check, _, _ = self.orchestration()
        self.api.checks.append({**check, "name": "merge-admission (1, head)"})
        self.check()
        self.assertEqual(self.api.suite_reads, [60])
        self.api.orchestration_runs[60][0]["event"] = "pull_request"
        with self.assertRaises(gate.Blocked):
            self.check()
        self.assertEqual(self.api.suite_reads, [60, 60])

    def test_unknown_or_untrusted_orchestration_origin_still_gates(self):
        mutations = {
            "external app": lambda c, s, r: c.update(app={"id": 99}),
            "missing suite": lambda c, s, r: c.pop("check_suite"),
            "suite app": lambda c, s, r: s.update(app={"id": 99}),
            "suite repository": lambda c, s, r: s.update(
                repository={"full_name": "other/repo"}
            ),
            "suite head": lambda c, s, r: s.update(head_sha=BASE),
            "check head": lambda c, s, r: c.update(head_sha=BASE),
            "suite id": lambda c, s, r: s.update(id=61),
            "run repository": lambda c, s, r: r.update(
                repository={"full_name": "other/repo"}
            ),
            "run suite": lambda c, s, r: r.update(check_suite_id=61),
            "run head": lambda c, s, r: r.update(head_sha=BASE),
            "wrong path": lambda c, s, r: r.update(path=".github/workflows/ci.yml"),
            "PR controlled": lambda c, s, r: r.update(event="pull_request"),
            "unknown event": lambda c, s, r: r.update(event="repository_dispatch"),
            "dispatch branch": lambda c, s, r: r.update(event="workflow_dispatch"),
            "target base": lambda c, s, r: r["pull_requests"][0]["base"].update(
                ref="feature"
            ),
            "target association": lambda c, s, r: r.update(pull_requests=[]),
            "target PR": lambda c, s, r: r["pull_requests"][0].update(number=2),
            "target branch": lambda c, s, r: r.update(head_branch="other"),
            "target repository": lambda c, s, r: r.update(
                head_repository={"full_name": "other/repo"}
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                self.api = API()
                mutate(*self.orchestration())
                with self.assertRaises(gate.Blocked):
                    self.check()

    def test_ambiguous_or_missing_producer_is_not_excluded(self):
        for count in (0, 2):
            with self.subTest(count=count):
                self.api = API()
                _, _, run = self.orchestration()
                self.api.orchestration_runs[60] = [run] * count
                with self.assertRaises(gate.Blocked):
                    self.check()

    def test_name_cannot_exempt_real_validation_or_missing_required_workflow(self):
        self.orchestration()
        self.api.checks[0].update(name="required-ci-status", conclusion="failure")
        with self.assertRaises(gate.Blocked):
            self.check()
        self.api.checks[0]["conclusion"] = "success"
        self.api.runs = []
        with self.assertRaises(gate.Pending):
            self.check()

    def test_orchestration_rerun_remains_excluded(self):
        _, _, run = self.orchestration()
        run["run_attempt"] = 2
        self.check()

    def test_fork_target_without_historical_base_evidence_stays_gated(self):
        self.api.pr["head"]["repo"]["full_name"] = "contributor/project"
        self.api.runs[0]["head_repository"]["full_name"] = "contributor/project"
        check, _, run = self.orchestration()
        run["pull_requests"] = []
        with self.assertRaises(gate.Blocked):
            gate.checks_ready(self.api, self.api.pr, self.api.files, POLICY)
        check.update(status="in_progress", conclusion=None)
        with self.assertRaises(gate.Pending):
            gate.checks_ready(self.api, self.api.pr, self.api.files, POLICY)

    def test_fork_target_with_authoritative_default_base_can_be_excluded(self):
        self.api.pr["head"]["repo"]["full_name"] = "contributor/project"
        self.api.runs[0]["head_repository"]["full_name"] = "contributor/project"
        self.orchestration()
        gate.checks_ready(self.api, self.api.pr, self.api.files, POLICY)

    def test_exact_head_protected_merge(self):
        result = self.execute()
        self.assertEqual(result["outcome"], "requested")
        self.assertEqual(
            self.api.effects[-1],
            (
                "pulls/1/merge-async",
                "PUT",
                {
                    "sha": HEAD,
                    "merge_action": "direct_merge",
                    "bypass_rules": False,
                    "merge_method": "squash",
                },
            ),
        )
        self.assertEqual(self.api.effects[0][2]["commit_id"], HEAD)

    def test_automatic_events_never_select_or_admit_merges(self):
        for event in (
            "pull_request_target",
            "pull_request",
            "workflow_run",
            "schedule",
            "push",
            "",
        ):
            with (
                self.subTest(event=event),
                patch.dict(os.environ, {"GITHUB_EVENT_NAME": event}),
            ):
                self.assertEqual(gate.candidates(self.api, {}), [])
                for review_id in (0, 10):
                    with self.assertRaises(gate.Blocked):
                        gate.admitted_intent(
                            self.api, 1, HEAD, review_id, "squash", POLICY
                        )
                self.assertFalse(self.api.effects)

    def test_dispatch_requires_trusted_operator_and_positive_review(self):
        event = {"inputs": {"pr": "1", "head": HEAD, "review_id": "10"}}
        self.assertEqual(
            gate.candidates(self.api, event),
            [{"pr": 1, "head": HEAD, "review_id": 10, "method": "squash"}],
        )
        for review_id in (None, "", "0", "-1", "bogus", True, 10, "1.0"):
            with self.subTest(review_id=review_id):
                event["inputs"]["review_id"] = review_id
                with self.assertRaises(gate.Blocked):
                    gate.candidates(self.api, event)
        os.environ["GITHUB_ACTOR_ID"] = "456"
        with self.assertRaisesRegex(gate.Blocked, "Unauthorized"):
            self.execute()
        self.assertFalse(self.api.effects)

    def test_missing_malformed_or_forged_review_blocks_before_effects(self):
        for review_id in (None, 0, -1, True, "10", 1.5):
            with self.subTest(review_id=review_id):
                with self.assertRaises(gate.Blocked):
                    gate.snapshot(self.api, 1, HEAD, POLICY, review_id)
                with self.assertRaises(gate.Blocked):
                    gate.execute(self.api, 1, HEAD, review_id, "squash", POLICY)
                self.assertFalse(self.api.effects)
        for field, value in (
            ("id", 11),
            ("state", "APPROVED"),
            ("commit_id", BASE),
            ("body", "{}"),
        ):
            with self.subTest(field=field):
                self.api.review = {**API().review, field: value}
                with self.assertRaises(gate.Blocked):
                    self.execute()
                self.assertFalse(self.api.effects)

    def test_ci_targets_include_ordinary_and_fork_without_review(self):
        fork = copy.deepcopy(self.api.pr)
        fork["number"] = 2
        fork["head"]["repo"]["full_name"] = "contributor/fork"
        self.api.open_prs.append(fork)
        self.api.review = {}
        self.assertEqual(
            gate.ci_targets(self.api, {}, POLICY, recovery=True),
            [{"pr": 1, "head": HEAD}, {"pr": 2, "head": HEAD}],
        )
        self.assertFalse(self.api.effects)

    def test_ci_events_target_only_current_associated_heads(self):
        event = {"workflow_run": {"id": 300}}
        run = {
            "event": "pull_request",
            "path": ".github/workflows/ci.yml",
            "head_sha": HEAD,
            "pull_requests": [{"number": 1}],
        }
        original = self.api.api
        with (
            patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_run"}),
            patch.object(
                self.api,
                "api",
                side_effect=lambda path: run
                if path == "actions/runs/300"
                else original(path),
            ),
            patch.object(
                self.api, "pages", side_effect=AssertionError("No global PR scan")
            ),
        ):
            self.assertEqual(
                gate.ci_targets(self.api, event, POLICY), [{"pr": 1, "head": HEAD}]
            )
            self.api.pr["head"]["sha"] = BASE
            self.assertEqual(gate.ci_targets(self.api, event, POLICY), [])
            for unrelated in ("push", "workflow_dispatch"):
                run["event"] = unrelated
                self.assertEqual(gate.ci_targets(self.api, event, POLICY), [])

    def test_ci_start_clears_previous_success_for_same_sha_rerun(self):
        self.api.runs[0]["run_attempt"] = 2
        self.api.runs[0]["status"] = "in_progress"
        self.api.runs[0]["conclusion"] = None
        with self.assertRaises(gate.Pending):
            gate.checks_ready(self.api, self.api.pr, self.api.files, POLICY)

    def test_missing_or_changed_intent_prevents_effects(self):
        self.api.intents = []
        with self.assertRaises(gate.Pending):
            self.execute()
        self.assertFalse(self.api.effects)
        self.api.intents = API().intents
        self.api.intent["base_sha"] = HEAD
        with self.assertRaisesRegex(gate.Blocked, "receipt differs"):
            self.execute()
        self.assertFalse(self.api.effects)

    def test_actions_authored_pr_cannot_self_approve(self):
        self.api.pr["user"]["id"] = gate.ACTIONS_BOT_ID
        with self.assertRaisesRegex(gate.Blocked, "own PR"):
            self.execute()
        self.assertFalse(self.api.effects)

    def test_ci_aggregate_is_independent_of_merge_admission(self):
        self.api.pr["head"]["repo"]["full_name"] = "contributor/fork"
        self.api.runs[0]["head_repository"]["full_name"] = "contributor/fork"
        self.api.review["body"] = "No agent attestation on an ordinary human PR"
        self.api.statuses[0]["state"] = "pending"
        result = gate.publish_ci(self.api, 1, HEAD, POLICY)
        self.assertEqual(result["state"], "success")
        self.assertEqual(self.api.effects[0][0], f"statuses/{HEAD}")
        self.assertEqual(len(self.api.effects), 1)

    def test_ci_run_must_belong_to_this_pr(self):
        self.api.runs[0]["pull_requests"] = [{"number": 2}]
        result = gate.publish_ci(self.api, 1, HEAD, POLICY)
        self.assertEqual(result["state"], "pending")
        self.assertEqual(self.api.effects[0][2]["state"], "pending")

    def test_fork_run_with_omitted_association_uses_suite_and_unique_pr(self):
        self.api.pr["head"]["repo"]["full_name"] = "contributor/fork"
        self.api.runs[0]["head_repository"]["full_name"] = "contributor/fork"
        self.api.runs[0]["pull_requests"] = []
        self.assertEqual(gate.publish_ci(self.api, 1, HEAD, POLICY)["state"], "success")
        self.api.open_prs.append({"number": 2})
        self.assertEqual(gate.publish_ci(self.api, 1, HEAD, POLICY)["state"], "pending")
        self.api.open_prs.pop()
        self.api.suite["head_sha"] = BASE
        self.assertEqual(gate.publish_ci(self.api, 1, HEAD, POLICY)["state"], "pending")

    def test_running_rerun_invalidates_successful_aggregate(self):
        self.api.runs[0]["run_attempt"] = 2
        self.api.runs[0]["status"] = "in_progress"
        self.api.runs[0]["conclusion"] = None
        self.assertEqual(gate.publish_ci(self.api, 1, HEAD, POLICY)["state"], "pending")
        self.assertEqual(self.api.effects[0][2]["state"], "pending")

    def test_ci_failure_updates_aggregate_without_merge_effects(self):
        self.api.runs[0]["conclusion"] = "failure"
        self.assertEqual(gate.publish_ci(self.api, 1, HEAD, POLICY)["state"], "failure")
        self.assertEqual(self.api.effects[0][0], f"statuses/{HEAD}")
        self.assertEqual(len(self.api.effects), 1)

    def test_queue_stops_without_effects(self):
        self.api.rules = [{"type": "merge_queue"}]
        with self.assertRaisesRegex(gate.Blocked, "cannot enqueue"):
            self.execute()
        self.assertFalse(self.api.effects)

    def test_existing_actions_approval_not_repeated(self):
        self.api.reviews.append(
            {
                "id": 11,
                "user": {"id": gate.ACTIONS_BOT_ID},
                "state": "APPROVED",
                "commit_id": HEAD,
            }
        )
        self.execute()
        self.assertEqual(len(self.api.effects), 1)

    def test_changed_head_blocks(self):
        self.api.pr["head"]["sha"] = "c" * 40
        with self.assertRaises(gate.Blocked):
            self.execute()
        self.assertFalse(self.api.effects)

    def test_default_advance_after_approval_blocks_merge(self):
        self.api.on_approve = lambda api: api.pr["base"].update(sha="c" * 40)
        with self.assertRaises(gate.Blocked):
            self.execute()
        self.assertEqual(len(self.api.effects), 1)

    def test_head_advance_after_approval_blocks_merge(self):
        self.api.on_approve = lambda api: api.pr["head"].update(sha="c" * 40)
        with self.assertRaises(gate.Blocked):
            self.execute()
        self.assertEqual(len(self.api.effects), 1)

    def test_old_base_ci_blocks(self):
        self.api.comparison = "diverged"
        with self.assertRaises(gate.Blocked):
            self.execute()
        self.assertFalse(self.api.effects)

    def test_empty_ci_is_pending(self):
        self.api.runs = []
        with self.assertRaises(gate.Pending):
            self.check()

    def test_failed_ci_blocks(self):
        self.api.runs[0]["conclusion"] = "failure"
        with self.assertRaises(gate.Blocked):
            self.check()

    def test_rerun_in_progress_is_pending(self):
        new = dict(
            self.api.runs[0], run_attempt=2, status="in_progress", conclusion=None
        )
        self.api.runs.append(new)
        with self.assertRaises(gate.Pending):
            self.check()

    def test_skipped_expected_workflow_blocks(self):
        self.api.runs[0]["conclusion"] = "skipped"
        with self.assertRaises(gate.Blocked):
            self.check()

    def test_failing_external_check_blocks(self):
        self.api.checks[0]["conclusion"] = "failure"
        with self.assertRaises(gate.Blocked):
            self.check()

    def test_latest_status_controls(self):
        self.api.statuses = [
            {"id": 2, "context": "external", "state": "pending"},
            {"id": 1, "context": "external", "state": "success"},
        ]
        with self.assertRaises(gate.Pending):
            self.check()

    def test_human_objection_survives_comment(self):
        self.api.reviews.extend(
            [
                {"id": 20, "state": "CHANGES_REQUESTED", "user": {"id": 9}},
                {"id": 21, "state": "COMMENTED", "user": {"id": 9}},
            ]
        )
        with self.assertRaises(gate.Blocked):
            self.execute()
        self.assertFalse(self.api.effects)

    def test_review_submission_time_controls_objections(self):
        self.api.reviews.extend(
            [
                {
                    "id": 20,
                    "state": "CHANGES_REQUESTED",
                    "user": {"id": 9},
                    "submitted_at": "2026-01-02T00:00:00Z",
                },
                {
                    "id": 21,
                    "state": "APPROVED",
                    "user": {"id": 9},
                    "submitted_at": "2026-01-01T00:00:00Z",
                },
            ]
        )
        with self.assertRaises(gate.Blocked):
            self.check()

    def test_workflow_rename_still_requires_workflow_permission(self):
        files = [
            {
                "filename": "archived/ci.yml",
                "previous_filename": ".github/workflows/ci.yml",
                "status": "renamed",
            }
        ]
        self.assertTrue(gate.changes_workflows(files))
        self.assertFalse(gate.changes_workflows([{"filename": "docs/readme.md"}]))

    def test_rename_requires_old_project_checks(self):
        self.api.files = [
            {
                "filename": "docs/new.md",
                "previous_filename": "service/main.py",
                "status": "renamed",
            }
        ]
        policy = copy.deepcopy(POLICY)
        policy["ci_workflows"].append({"file": "service.yml", "paths": ["service/**"]})
        with self.assertRaises(gate.Pending):
            gate.snapshot(self.api, 1, HEAD, policy, 10)

    def test_unresolved_threads_block(self):
        self.api.unresolved = True
        with self.assertRaises(gate.Blocked):
            self.check()

    def test_attestation_fields_are_bound(self):
        for field, value in [
            ("head", BASE),
            ("base_sha", HEAD),
            ("repository", "evil/project"),
            ("pr", 2),
            ("verdict", "failed"),
            ("unresolved_findings", 1),
            ("validation", []),
        ]:
            with self.subTest(field=field):
                record = evidence()
                record[field] = value
                self.api.review["body"] = json.dumps(record)
                with self.assertRaises(gate.Blocked):
                    self.check()

    def test_untrusted_operator_blocks(self):
        self.api.review["user"]["id"] = 456
        with self.assertRaises(gate.Blocked):
            self.check()

    def test_malformed_review_body_fails_closed(self):
        for body in ("[]", "null", "true", "broken"):
            with self.subTest(body=body):
                self.api.review["body"] = body
                with self.assertRaises(gate.Blocked):
                    self.execute()
                self.assertFalse(self.api.effects)

    def test_changed_review_after_approval_stops_merge(self):
        self.api.on_approve = lambda api: api.review.update(state="DISMISSED")
        with self.assertRaises(gate.Blocked):
            self.execute()
        self.assertEqual(len(self.api.effects), 1)
        self.assertEqual(self.api.effects[0][0], "pulls/1/reviews")

    def test_unbound_source_identity_blocks(self):
        self.api.pr["head"]["repo"]["full_name"] = "fork/project"
        with self.assertRaises(gate.Blocked):
            self.check()

    def test_bound_fork_can_be_admitted(self):
        self.api.pr["head"]["repo"] = {"full_name": "contributor/fork", "id": 2}
        self.api.runs[0]["head_repository"]["full_name"] = "contributor/fork"
        record = evidence()
        record.update(head_repository="contributor/fork", head_repository_id=2)
        self.api.review["body"] = json.dumps(record)
        self.check()

    def test_missing_contribution_or_specs_cannot_attest(self):
        for field in ("contribution", "specs", "head_repository_id", "head_ref"):
            with self.subTest(field=field):
                record = evidence()
                del record[field]
                self.api.review["body"] = json.dumps(record)
                with self.assertRaises(gate.Blocked):
                    self.check()

    def test_incomplete_semantic_assessments_block(self):
        for change in ({"specs": []}, {"specs": [{"project": "../other", "outcome": "aligned", "evidence": "ok"}]}, {"contribution": {"verdict": "passed", "compatibility": "unknown", "evidence": ["CI green"]}}):
            with self.subTest(change=change):
                record = evidence()
                record.update(change)
                self.api.review["body"] = json.dumps(record)
                with self.assertRaises(gate.Blocked):
                    self.check()

    def test_incomplete_files_block(self):
        self.api.pr["changed_files"] = 2
        with self.assertRaises(gate.Blocked):
            self.check()

    def test_nondefault_execution_blocks(self):
        os.environ["GITHUB_REF"] = "refs/heads/feature"
        with self.assertRaises(gate.Blocked):
            self.execute()
        self.assertFalse(self.api.effects)

    def test_automatic_event_cannot_admit_operator_review(self):
        os.environ["GITHUB_EVENT_NAME"] = "workflow_run"
        with self.assertRaises(gate.Blocked):
            self.execute()

    def test_already_merged_is_read_only_and_identity_bound(self):
        self.api.pr.update(merged=True, state="closed")
        self.assertEqual(self.execute()["result_sha"], BASE)
        self.assertFalse(self.api.effects)
        self.api.pr["head"]["sha"] = BASE
        with self.assertRaises(gate.Blocked):
            self.execute()

    def test_reviewed_human_and_dependency_updates_share_admission(self):
        for author in (123, 49699333):
            for path in (
                "src/main.py",
                "pkg/requirements.txt",
                "pkg/uv.lock",
                ".github/workflows/ci.yml",
                "Dockerfile",
            ):
                with self.subTest(author=author, path=path):
                    self.api = API()
                    self.api.pr["user"]["id"] = author
                    # Safe source repairs are allowed alongside dependency changes.
                    self.api.files = [
                        {"filename": path, "status": "modified"},
                        {"filename": "src/repair.py", "status": "modified"},
                    ]
                    self.api.pr["changed_files"] = 2
                    self.api.intent = gate.admitted_intent(
                        self.api, 1, HEAD, 10, "squash", POLICY
                    )
                    self.assertEqual(self.execute()["outcome"], "requested")
                    self.assertEqual(len(self.api.effects), 2)
                    self.api.effects.clear()
                    with self.assertRaises(gate.Blocked):
                        gate.execute(self.api, 1, HEAD, 0, "squash", POLICY)
                    self.assertFalse(self.api.effects)

    def test_dependabot_repair_requires_fresh_head_base_review_and_ci(self):
        self.api.pr["user"]["id"] = 49699333
        repaired = "c" * 40
        self.api.pr["head"]["sha"] = repaired
        with self.assertRaises(gate.Blocked):
            gate.snapshot(self.api, 1, repaired, POLICY, 10)
        self.api.review["commit_id"] = repaired
        record = evidence()
        record["head"] = repaired
        self.api.review["body"] = json.dumps(record)
        with self.assertRaises(gate.Pending):
            gate.snapshot(self.api, 1, repaired, POLICY, 10)
        self.api.runs[0]["head_sha"] = repaired
        gate.snapshot(self.api, 1, repaired, POLICY, 10)
        self.api.pr["base"]["sha"] = "d" * 40
        with self.assertRaises(gate.Blocked):
            gate.snapshot(self.api, 1, repaired, POLICY, 10)
        record["base_sha"] = "d" * 40
        self.api.review["body"] = json.dumps(record)
        gate.snapshot(self.api, 1, repaired, POLICY, 10)
        self.assertFalse(self.api.effects)

    def test_workflow_has_no_personal_token_fallback_or_pr_checkout(self):
        root = Path(__file__).resolve().parents[3]
        if not (root / ".github/workflows/skills-merge-pr.yml").exists():
            self.skipTest("Installed skill without repository deployment")
        broker = (root / ".github/workflows/skills-merge-pr.yml").read_text()
        self.assertNotIn("DEPENDABOT_AUTOMERGE_TOKEN", broker)
        self.assertNotIn("pull_request.head", broker)
        self.assertNotIn("secrets.", broker)
        self.assertIn("persist-credentials: false", broker)
        self.assertNotIn("create-github-app-token", broker)
        self.assertIn("types: [in_progress, completed]", broker)

    def test_each_result_ci_preserves_sha_and_push_equivalent_execution(self):
        root = Path(__file__).resolve().parents[3]
        policy_path = root / ".github/merge-policy.json"
        if not policy_path.exists():
            self.skipTest("Installed skill without repository deployment")
        policy = json.loads(policy_path.read_text())
        for workflow in policy["ci_workflows"]:
            with self.subTest(workflow=workflow["file"]):
                value = (root / ".github/workflows" / workflow["file"]).read_text()
                group = re.search(r"^  group: (.+)$", value, re.M)[1]
                self.assertIn("inputs.result_sha", group)
                self.assertIn(
                    "cancel-in-progress: ${{ inputs.result_sha == '' }}", value
                )
                self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', value)
                self.assertNotIn(
                    "ref: ${{ needs.merge-inputs.outputs.checkout_sha }}", value
                )
                checkout_steps = value.count("name: Verify exact checkout identity")
                self.assertEqual(
                    value.count("ci-checkout --workflow " + workflow["file"]),
                    checkout_steps,
                )
                self.assertEqual(
                    value.count("      actions: read"),
                    checkout_steps + 2,  # identity and terminal evidence jobs
                )
                self.assertIn("CI_EXPECTED_JOBS:", value)
                self.assertIn("ci-evidence --workflow " + workflow["file"], value)
                for field in (
                    "result_sha",
                    "merge_pr",
                    "broker_run_id",
                    "broker_run_attempt",
                    "correlation_id",
                ):
                    self.assertIn("      " + field + ":", value)

    def test_reusable_brokers_match_deployment(self):
        root = Path(__file__).resolve().parents[3]
        if not (root / ".github/workflows/skills-merge-pr.yml").exists():
            self.skipTest("Installed skill without repository deployment")
        templates = root / "skills/github-workflows/assets/protected-merge"
        for name in ("skills-merge-pr", "merge-completion"):
            value = (root / f".github/workflows/{name}.yml").read_text()
            value = value.replace("skills/merge-pr/scripts/", ".github/scripts/")
            triggers = (
                '["Protected merge", "CI"]' if name == "merge-completion" else '["CI"]'
            )
            value = re.sub(
                r"^    workflows:.*$", f"    workflows: {triggers}", value, flags=re.M
            )
            self.assertEqual(value, (templates / f"{name}.yml.template").read_text())


class MergedResultTests(unittest.TestCase):
    def setUp(self):
        self.pr = {
            "number": 1,
            "merged": True,
            "mergedAt": "2026-01-01T00:00:00Z",
            "headRefOid": HEAD,
            "baseRefName": "main",
            "baseRepository": {"nameWithOwner": REPO},
            "mergeCommit": {"oid": BASE},
        }

    def result(self, response=None):
        if response is None:
            response = {"data": {"repository": {"pullRequest": self.pr}}}
        with patch.object(gate, "gh", return_value=response) as transport:
            result = gate.GitHub(REPO).merged_commit(1, HEAD, "main")
        args, payload = transport.call_args.args
        self.assertEqual(
            args, ["api", "--hostname", "github.com", "graphql", "--input", "-"]
        )
        self.assertEqual(
            payload["variables"], {"owner": "example", "name": "project", "number": 1}
        )
        return result

    def test_actual_graphql_result_is_canonical(self):
        # Neither a REST result field nor a synthetic test merge is authority.
        self.pr["potentialMergeCommit"] = {"oid": HEAD}
        self.assertEqual(self.result(), BASE)

    def test_result_requires_matching_pr_head_and_target(self):
        for key, value in (
            ("number", 2),
            ("number", True),
            ("headRefOid", BASE),
            ("baseRefName", "other"),
            ("baseRepository", {"nameWithOwner": "other/project"}),
            ("baseRepository", None),
        ):
            with self.subTest(key=key, value=value):
                prior = self.pr[key]
                self.pr[key] = value
                with self.assertRaises(gate.Blocked):
                    self.result()
                self.pr[key] = prior

    def test_unconfirmed_or_missing_result_fails_closed(self):
        for key, value in (
            ("merged", False),
            ("merged", "true"),
            ("mergedAt", None),
            ("mergedAt", ""),
            ("mergeCommit", None),
            ("mergeCommit", {}),
            ("mergeCommit", {"oid": "main"}),
            ("mergeCommit", {"oid": 1}),
        ):
            with self.subTest(key=key, value=value):
                prior = self.pr[key]
                self.pr[key] = value
                with self.assertRaises(gate.Blocked):
                    self.result()
                self.pr[key] = prior

    def test_graphql_errors_or_missing_object_fail_closed(self):
        for response in (
            {},
            [],
            {"data": None},
            {"data": {"repository": None}},
            {"data": {"repository": {"pullRequest": None}}},
            {
                "errors": [{"message": "unavailable"}],
                "data": {"repository": {"pullRequest": self.pr}},
            },
        ):
            with self.subTest(response=response), self.assertRaises(gate.Blocked):
                self.result(response)


if __name__ == "__main__":
    unittest.main()
