#!/usr/bin/env python3
"""Offline regressions for asynchronous effects and exact-result evidence."""

import copy
from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch

import merge_completion as completion
import merge_gate as gate

REPO = "example/project"
HEAD, BASE, RESULT, TIP = (c * 40 for c in "abcd")
POLICY = {
    "merge_workflow": "skills-merge-pr.yml",
    "completion_workflow": "merge-completion.yml",
    "ci_workflows": [{"file": "ci.yml", "paths": ["**"]}],
    "pages_enabled": True,
}
ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": REPO,
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": BASE,
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_ACTOR_ID": str(gate.ACTIONS_BOT_ID),
    "GITHUB_RUN_ID": "300",
    "GITHUB_RUN_ATTEMPT": "1",
    "MERGE_OPERATOR_IDS": "[123]",
    "DISPATCH_ARTIFACT_ID": "903",
    "PAGES_ARTIFACT_ID": "904",
}


class API:
    repo = REPO

    def __init__(self):
        self.intent = {
            "schema": gate.INTENT_SCHEMA,
            "repository": REPO,
            "pr": 1,
            "head": HEAD,
            "base": "main",
            "base_sha": BASE,
            "review_id": 10,
            "method": "squash",
            "run_id": 100,
            "run_attempt": 1,
            "source_sha": BASE,
            "ci_workflows": ["ci.yml"],
        }
        self.pr = {
            "merged": True,
            "state": "closed",
            "number": 1,
            "merge_commit_sha": RESULT,
            "head": {"sha": HEAD, "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO}},
        }
        self.broker = self.run(100, "skills-merge-pr.yml", 123)
        self.consumer = self.run(300, "merge-completion.yml", gate.ACTIONS_BOT_ID)
        self.ci = self.run(200, "ci.yml", gate.ACTIONS_BOT_ID)
        self.corr = completion.correlation(REPO, 1, RESULT, "ci.yml")
        self.ci["display_title"] = completion.ci_name(self.corr)
        self.runs = []
        self.artifacts = []
        self.builds = []
        self.effects = []
        self.comparisons = {}
        self.values = {900: self.intent, 901: self.evidence()}
        self.producers = {100: self.broker, 200: self.ci, 300: self.consumer}
        self.jobs = [{"name": "verify-pages-1", "conclusion": "success"}]
        self.expired = False
        self.fail_dispatch = False

    @staticmethod
    def run(number, filename, actor):
        return {
            "id": number,
            "run_number": number,
            "run_attempt": 1,
            "head_sha": BASE,
            "head_branch": "main",
            "head_repository": {"full_name": REPO},
            "path": f".github/workflows/{filename}",
            "event": "workflow_dispatch",
            "actor": {"id": actor},
            "status": "completed",
            "conclusion": "success",
            "display_title": "ordinary",
        }

    @staticmethod
    def artifact_meta(number, name, producer):
        return {
            "id": number,
            "name": name,
            "expired": False,
            "workflow_run": {"id": producer},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def evidence(self):
        return {
            "schema": completion.CI_SCHEMA,
            "repository": REPO,
            "result_sha": RESULT,
            "checkout_sha": RESULT,
            "merge_pr": 1,
            "workflow": "ci.yml",
            "run_id": 200,
            "run_attempt": 1,
            "correlation_id": self.corr,
            "broker_run_id": 100,
            "broker_run_attempt": 1,
        }

    def repo_info(self):
        return {"default_branch": "main"}

    def api(self, path, method="GET", data=None):
        if method != "GET":
            self.effects.append((path, method, copy.deepcopy(data)))
            if path.endswith("/dispatches"):
                if self.fail_dispatch:
                    raise gate.Blocked("Ambiguous network failure")
                self.ci["status"] = "queued"
                self.ci["conclusion"] = None
                self.runs = [self.ci]
                return {"workflow_run_id": 200}
            return {"status": "queued"}
        if path == "pulls/1":
            return copy.deepcopy(self.pr)
        if path == "branches/main":
            return {"commit": {"sha": TIP}}
        if path.startswith("compare/"):
            return {"status": self.comparisons.get(path, "ahead")}
        if path.startswith("actions/runs/"):
            return self.producers[int(path.split("/")[2])]
        if path == "pages":
            return {"build_type": "legacy", "source": {"branch": "main", "path": "/"}}
        raise AssertionError(path)

    def pages(self, path, key=None):
        if path == "actions/runs/100/artifacts":
            item = self.artifact_meta(900, f"merge-intent-1-{HEAD}-1", 100)
            item["expired"] = self.expired
            return [item]
        if path == "actions/runs/200/artifacts":
            return [self.artifact_meta(901, "result-ci-200-1", 200)]
        if path.endswith("/jobs"):
            return self.jobs
        if path.startswith("actions/workflows/"):
            return self.runs
        if path == "actions/artifacts":
            return self.artifacts
        if path == "pages/builds":
            return self.builds
        raise AssertionError(path)

    def artifact(self, number, filename):
        return copy.deepcopy(self.values[number])


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.api = API()

    def inputs(self):
        return {
            "inputs": {
                "result_sha": RESULT,
                "merge_pr": "1",
                "broker_run_id": "100",
                "broker_run_attempt": "1",
                "correlation_id": self.api.corr,
            }
        }

    def journal(self):
        journal = completion.prepare_dispatch(self.api, self.api.intent, POLICY)
        self.api.values[903] = journal
        self.api.artifacts.append(
            self.api.artifact_meta(903, f"merge-dispatch-1-{RESULT}-300-1", 300)
        )
        return journal

    def test_dispatch_pins_result_and_captures_run_id(self):
        result = completion.verify_or_dispatch(
            self.api, self.api.intent, POLICY, True, self.journal()
        )
        self.assertEqual(result["pending"][0]["run_id"], 200)
        payload = self.api.effects[0][2]
        self.assertEqual(payload["ref"], "main")
        self.assertEqual(payload["inputs"]["result_sha"], RESULT)
        self.assertNotIn("return_run_details", payload)

    def test_uncertain_dispatch_is_not_repeated(self):
        journal = self.journal()
        self.api.fail_dispatch = True
        with self.assertRaises(gate.Blocked):
            completion.verify_or_dispatch(
                self.api, self.api.intent, POLICY, True, journal
            )
        with self.assertRaisesRegex(gate.Blocked, "uncertain"):
            completion.prepare_dispatch(self.api, self.api.intent, POLICY)
        self.assertEqual(len(self.api.effects), 1)

    def test_existing_run_reconciles_without_dispatch(self):
        self.api.runs = [self.api.ci]
        result = completion.verify_or_dispatch(self.api, self.api.intent, POLICY, True)
        self.assertEqual(result["outcome"], "ci-verified")
        self.assertFalse(self.api.effects)

    def test_workflow_revision_is_not_mistaken_for_tested_sha(self):
        self.api.runs = [self.api.ci]
        self.assertNotEqual(self.api.ci["head_sha"], RESULT)
        self.assertEqual(
            completion.verify_or_dispatch(self.api, self.api.intent, POLICY)[
                "result_sha"
            ],
            RESULT,
        )
        self.api.values[901]["checkout_sha"] = TIP
        with self.assertRaisesRegex(gate.Blocked, "receipt mismatch"):
            completion.verify_or_dispatch(self.api, self.api.intent, POLICY)

    def test_newer_failed_run_cannot_hide_behind_old_success(self):
        failed = {**self.api.ci, "id": 201, "run_number": 201, "conclusion": "failure"}
        self.api.runs = [self.api.ci, failed]
        with self.assertRaisesRegex(gate.Blocked, "failed"):
            completion.verify_or_dispatch(self.api, self.api.intent, POLICY, True)
        self.assertFalse(self.api.effects)

    def test_unmerged_pr_has_no_completion_effects(self):
        self.api.pr.update(merged=False, state="open")
        with self.assertRaises(gate.Pending):
            completion.verify_or_dispatch(self.api, self.api.intent, POLICY, True)
        self.assertFalse(self.api.effects)

    def test_result_outside_default_history_stops(self):
        self.api.comparisons[f"compare/{RESULT}...{TIP}"] = "diverged"
        with self.assertRaises(gate.Blocked):
            completion.prepare_dispatch(self.api, self.api.intent, POLICY)
        self.assertFalse(self.api.effects)

    def test_expired_intent_is_not_replayed(self):
        self.api.expired = True
        with self.assertRaisesRegex(gate.Blocked, "expired"):
            completion.read_intent(self.api, 100, 1, 1, HEAD, POLICY)

    def test_operator_and_source_revision_bound(self):
        self.api.broker["actor"]["id"] = 321
        with self.assertRaises(gate.Blocked):
            completion.read_intent(self.api, 100, 1, 1, HEAD, POLICY)
        self.api.broker["actor"]["id"] = 123
        self.api.intent["source_sha"] = HEAD
        with self.assertRaises(gate.Blocked):
            completion.read_intent(self.api, 100, 1, 1, HEAD, POLICY)

    def test_partial_post_merge_inputs_rejected(self):
        with self.assertRaises(gate.Blocked):
            completion.ci_inputs(
                self.api, POLICY, {"inputs": {"result_sha": RESULT}}, "ci.yml"
            )
        self.assertEqual(
            completion.ci_inputs(self.api, POLICY, {}, "ci.yml")["post_merge"], "false"
        )

    def test_post_merge_checkout_and_bot_identity(self):
        self.assertEqual(
            completion.ci_inputs(self.api, POLICY, self.inputs(), "ci.yml")[
                "checkout_sha"
            ],
            RESULT,
        )
        os.environ["GITHUB_ACTOR_ID"] = "123"
        with self.assertRaises(gate.Blocked):
            completion.ci_inputs(self.api, POLICY, self.inputs(), "ci.yml")

    def test_required_job_failure_cannot_emit_success_receipt(self):
        with self.assertRaisesRegex(gate.Blocked, "did not succeed"):
            completion.emit_ci_evidence(
                self.api,
                POLICY,
                self.inputs(),
                "ci.yml",
                {"tests": {"result": "skipped"}},
                ["tests"],
            )
        result = completion.emit_ci_evidence(
            self.api,
            POLICY,
            self.inputs(),
            "ci.yml",
            {"tests": {"result": "success"}, "manual": {"result": "skipped"}},
            ["tests"],
        )
        self.assertEqual(result["checkout_sha"], RESULT)

    def test_pages_accepts_descendant_build(self):
        self.api.builds = [{"status": "built", "commit": TIP, "url": "build/1"}]
        self.assertEqual(
            completion.verify_pages(self.api, self.api.intent)["build_commit"], TIP
        )
        self.assertFalse(self.api.effects)

    def test_failed_pages_build_is_not_retried(self):
        self.api.builds = [{"status": "errored", "commit": RESULT}]
        with self.assertRaisesRegex(gate.Blocked, "Pages build failed"):
            completion.verify_pages(self.api, self.api.intent, True)
        self.assertFalse(self.api.effects)

    def test_active_pages_build_is_not_duplicated(self):
        self.api.builds = [{"status": "building", "commit": RESULT}]
        self.assertEqual(
            completion.verify_pages(self.api, self.api.intent, True)["outcome"],
            "pending",
        )
        self.assertFalse(self.api.effects)

    def test_pages_effect_requires_durable_journal(self):
        self.api.runs = [self.api.ci]
        journal = completion.prepare_pages(self.api, self.api.intent, POLICY)
        self.api.values[904] = journal
        self.api.artifacts = [
            self.api.artifact_meta(904, f"merge-pages-1-{RESULT}-300-1", 300)
        ]
        completion.verify_pages(self.api, self.api.intent, True, journal)
        with self.assertRaisesRegex(gate.Blocked, "uncertain"):
            completion.prepare_pages(self.api, self.api.intent, POLICY)
        self.assertEqual(len(self.api.effects), 1)

    def test_pages_disabled_needs_no_pages_access(self):
        self.api.runs = [self.api.ci]
        self.assertFalse(
            completion.prepare_pages(
                self.api, self.api.intent, {**POLICY, "pages_enabled": False}
            )["request"]
        )

    def test_feature_branch_artifacts_do_not_block_trusted_intent(self):
        foreign = self.api.run(400, "merge-completion.yml", gate.ACTIONS_BOT_ID)
        foreign["head_branch"] = "untrusted"
        self.api.producers[400] = foreign
        self.api.artifacts = [
            self.api.artifact_meta(910, f"merge-complete-1-{HEAD}-400-1", 400),
            self.api.artifact_meta(900, f"merge-intent-1-{HEAD}-1", 100),
        ]
        result = completion.targets(self.api, POLICY, {})
        self.assertEqual(result[0]["run_id"], 100)
        self.api.artifacts = [
            self.api.artifact_meta(910, f"merge-dispatch-1-{RESULT}-400-1", 400)
        ]
        self.assertEqual(
            completion.prepare_dispatch(self.api, self.api.intent, POLICY)["workflows"],
            ["ci.yml"],
        )

    def test_successful_target_survives_failed_sibling_matrix(self):
        self.api.consumer["conclusion"] = "failure"
        self.api.values[905] = {
            "schema": "merge-completion/v1",
            "repository": REPO,
            "pr": 1,
            "head": HEAD,
            "result_sha": RESULT,
            "run_id": 300,
            "run_attempt": 1,
            "source_sha": BASE,
        }
        self.api.artifacts = [
            self.api.artifact_meta(905, f"merge-complete-1-{HEAD}-300-1", 300),
            self.api.artifact_meta(900, f"merge-intent-1-{HEAD}-1", 100),
        ]
        self.assertEqual(completion.targets(self.api, POLICY, {}), [])
        self.api.jobs[0]["conclusion"] = "failure"
        self.assertEqual(len(completion.targets(self.api, POLICY, {})), 1)


if __name__ == "__main__":
    unittest.main()
