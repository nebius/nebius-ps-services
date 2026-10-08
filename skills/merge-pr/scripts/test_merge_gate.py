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
    "excluded_checks": [],
    "dependabot_workflow": "dependabot-auto-merge.yml",
}
ENV = {
    "MERGE_OPERATOR_IDS": "[123]",
    "MERGE_AUTOMATION_MODE": "enabled",
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
            "head": {"sha": HEAD, "ref": "feature", "repo": {"full_name": REPO}},
            "created_at": "2026-01-01T00:00:00Z",
            "base": {"sha": BASE, "ref": "main", "repo": {"full_name": REPO}},
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
        self.producers = []
        self.artifacts = []
        self.admission = {}
        self.intent = None
        self.open_prs = [self.pr]
        self.suite = {
            "app": {"id": 15368},
            "repository": {"full_name": REPO},
            "head_sha": HEAD,
            "head_branch": "feature",
        }
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
        if path == "pulls/1/reviews/10":
            return copy.deepcopy(self.review)
        if path == "actions/runs/7":
            return self.producers[0]
        if path.startswith("compare/"):
            return {"status": self.comparison}
        raise AssertionError(path)

    def pages(self, path, key=None):
        if path.startswith("pulls?state=open&head="):
            return self.open_prs
        if path == "actions/runs/100/artifacts":
            return self.intents
        if path == "pulls/1/files":
            return self.files
        if path == "pulls/1/reviews":
            return self.reviews
        if path == "pulls/1/commits":
            return [
                {
                    "author": {"id": gate.BOT_ID},
                    "commit": {"verification": {"verified": True}},
                }
            ]
        if path.startswith("actions/workflows/"):
            return self.producers
        if path.startswith("actions/artifacts?"):
            return self.artifacts
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

    def artifact(self, artifact_id, filename="admission.json"):
        return self.intent if filename == "intent.json" else self.admission


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

    def test_activation_never_inherits_authority(self):
        for settings in (
            {"MERGE_AUTOMATION_MODE": "disabled"},
            {"MERGE_AUTOMATION_MODE": "canary", "MERGE_CANARY_PR_NUMBERS": "[2]"},
        ):
            with self.subTest(settings=settings), patch.dict(os.environ, settings):
                with self.assertRaises(gate.Blocked):
                    self.execute()
                self.assertFalse(self.api.effects)
        with patch.dict(
            os.environ,
            {"MERGE_AUTOMATION_MODE": "canary", "MERGE_CANARY_PR_NUMBERS": "[1]"},
        ):
            self.assertEqual(self.execute()["outcome"], "requested")

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
        os.environ["MERGE_AUTOMATION_MODE"] = "disabled"
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

    def test_fork_blocks(self):
        self.api.pr["head"]["repo"]["full_name"] = "fork/project"
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
        self.api.pr.update(merged=True, merge_commit_sha=BASE, state="closed")
        self.assertEqual(self.execute()["result_sha"], BASE)
        self.assertFalse(self.api.effects)
        self.api.pr["head"]["sha"] = BASE
        with self.assertRaises(gate.Blocked):
            self.execute()

    def test_dependabot_policy_preserves_supported_updates(self):
        for eco, path in [
            ("pip", "pkg/requirements.txt"),
            ("uv", "pkg/uv.lock"),
            ("github_actions", ".github/workflows/ci.yml"),
        ]:
            for kind in ["major", "minor", "patch"]:
                gate.dependency_policy(
                    {
                        "schema": gate.ADMISSION_SCHEMA,
                        "ecosystem": eco,
                        "update_type": "version-update:semver-" + kind,
                    },
                    [{"filename": path, "status": "modified"}],
                )

    def test_dependabot_docker_and_source_edits_block(self):
        for eco, path in [
            ("docker", "Dockerfile"),
            ("pip", "src/main.py"),
            ("github_actions", ".github/merge-policy.json"),
        ]:
            with self.assertRaises(gate.Blocked):
                gate.dependency_policy(
                    {
                        "schema": gate.ADMISSION_SCHEMA,
                        "ecosystem": eco,
                        "update_type": "version-update:semver-patch",
                    },
                    [{"filename": path, "status": "modified"}],
                )

    def test_dependabot_artifact_identity(self):
        self.api.pr["user"] = {"id": gate.BOT_ID, "login": "dependabot[bot]"}
        self.api.files = [{"filename": "uv.lock", "status": "modified"}]
        self.api.producers = [
            {
                "id": 7,
                "status": "completed",
                "conclusion": "success",
                "path": ".github/workflows/dependabot-auto-merge.yml",
                "actor": {"id": gate.BOT_ID},
                "head_sha": BASE,
                "event": "pull_request_target",
                "repository": {"full_name": REPO},
            }
        ]
        self.api.artifacts = [
            {
                "id": 9,
                "name": "dependabot-1-" + HEAD,
                "expired": False,
                "workflow_run": {"id": 7},
            }
        ]
        self.api.admission = {
            "schema": gate.ADMISSION_SCHEMA,
            "repository": REPO,
            "pr": 1,
            "head": HEAD,
            "base": "main",
            "base_sha": BASE,
            "run_id": 7,
            "ecosystem": "uv",
            "update_type": "version-update:semver-major",
        }
        gate.snapshot(self.api, 1, HEAD, POLICY)
        self.api.admission["head"] = BASE
        with self.assertRaises(gate.Blocked):
            gate.snapshot(self.api, 1, HEAD, POLICY)

    def test_workflow_has_no_personal_token_fallback_or_pr_checkout(self):
        root = Path(__file__).resolve().parents[3]
        if not (root / ".github/workflows/skills-merge-pr.yml").exists():
            self.skipTest("Installed skill without repository deployment")
        broker = (root / ".github/workflows/skills-merge-pr.yml").read_text()
        producer = (root / ".github/workflows/dependabot-auto-merge.yml").read_text()
        self.assertNotIn("DEPENDABOT_AUTOMERGE_TOKEN", broker + producer)
        self.assertNotIn("pull_request.head", broker)
        self.assertNotIn("secrets.", producer)
        self.assertNotIn("actions/checkout", producer)
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
        for name in ("skills-merge-pr", "merge-completion", "dependabot-auto-merge"):
            value = (root / f".github/workflows/{name}.yml").read_text()
            value = value.replace("skills/merge-pr/scripts/", ".github/scripts/")
            producer = (
                "Protected merge"
                if name == "merge-completion"
                else "dependabot-auto-merge"
            )
            value = re.sub(
                r"^    workflows:.*$",
                f'    workflows: ["{producer}", "CI"]',
                value,
                flags=re.M,
            )
            self.assertEqual(value, (templates / f"{name}.yml.template").read_text())


if __name__ == "__main__":
    unittest.main()
