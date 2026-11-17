"""Offline qualification provenance, coverage and concurrency regressions."""

import base64
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import unittest
from unittest.mock import Mock, patch
import zipfile

import publication as pub
import release_qualification as q

SHA = "a" * 40
PROFILE = {
    "publisher": "release.yml",
    "workflows": {"ci.yml": {"jobs": {"test": ["Test 3.12", "Test 3.13"]}}},
}
JOBS = [
    {"name": name, "id": i, "conclusion": "success"}
    for i, name in enumerate(["Test 3.12", "Test 3.13"], 1)
]


class QualificationTests(unittest.TestCase):
    def test_repository_root_lookup_uses_canonical_api_endpoint(self):
        responses = {
            "repos/example/repo": {"id": 123, "default_branch": "main"},
            "repos/example/repo/commits/main": {"sha": SHA},
            f"repos/example/repo/compare/{SHA}...{SHA}": {"status": "identical"},
        }

        def request(args, **kwargs):
            payload = responses.get(args[-1])
            return Mock(
                returncode=0 if payload is not None else 1,
                stdout=json.dumps(payload or {"status": "404"}).encode(),
            )

        with (
            patch.dict(q.os.environ, {"GITHUB_REPOSITORY": "example/repo"}),
            patch.object(pub.subprocess, "run", side_effect=request),
        ):
            self.assertEqual(
                q.main_history(q.GitHub(), SHA), responses["repos/example/repo"]
            )

    def test_checkout_scopes_read_token_to_fetch_and_disables_git_hooks(self):
        identity = {"checkout_sha": SHA}
        with (
            patch.object(q, "qualification_inputs", return_value=identity),
            patch.object(q, "command", side_effect=[SHA, "", SHA]),
            patch.object(q.subprocess, "run", return_value=Mock(returncode=0)) as run,
            patch.dict(
                q.os.environ,
                {
                    "GITHUB_WORKSPACE": str(q.Path.cwd()),
                    "GITHUB_SHA": SHA,
                    "GH_TOKEN": "test-fixture",
                },
            ),
        ):
            self.assertEqual(
                q.checkout(Mock(repo="example/repo"), {}, "ci.yml"), identity
            )
        fetch, checkout = run.call_args_list
        self.assertIn("core.hooksPath=/dev/null", fetch.args[0])
        self.assertIn("credential.helper=", fetch.args[0])
        self.assertIn("GIT_CONFIG_VALUE_0", fetch.kwargs["env"])
        self.assertNotIn("GIT_CONFIG_VALUE_0", checkout.kwargs["env"])
        self.assertNotIn("GH_TOKEN", fetch.kwargs["env"])
        self.assertNotIn("GH_TOKEN", checkout.kwargs["env"])

    def setUp(self):
        self.api = Mock(repo="example/repo")
        self.repo = {"id": 123, "default_branch": "main"}
        self.run = {
            "id": 10,
            "run_attempt": 1,
            "path": ".github/workflows/ci.yml",
            "head_repository": {"full_name": "example/repo"},
            "head_branch": "main",
            "head_sha": SHA,
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-01-01T00:00:00Z",
        }
        self.artifact = {
            "id": 22,
            "name": "release-qualification-services-demo-10-1",
            "digest": "sha256:" + "b" * 64,
            "expired": False,
            "size_in_bytes": 100,
        }
        self.manifest = {
            "schema": q.SCHEMA,
            "repository": "example/repo",
            "repository_id": 123,
            "project": "services/demo",
            "profile_digest": q.digest(PROFILE),
            "sha": SHA,
            "tree": SHA,
            "workflow": "ci.yml",
            "workflow_sha": SHA,
            "run_id": 10,
            "run_attempt": 1,
            "event": "push",
            "required_jobs": PROFILE["workflows"]["ci.yml"]["jobs"],
            "jobs": q.successful_jobs(JOBS, PROFILE["workflows"]["ci.yml"]),
            "reports": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.api.api.side_effect = self.api_call
        self.api.pages.side_effect = lambda path, key: (
            [self.run]
            if key == "workflow_runs"
            else [self.artifact]
            if key == "artifacts"
            else JOBS
        )

    def api_call(self, path):
        if path == "":
            return self.repo
        if path.startswith("commits/"):
            return {"sha": SHA}
        if path.startswith("compare/"):
            return {"status": "identical"}
        if path.startswith("contents/"):
            return {
                "encoding": "base64",
                "type": "file",
                "content": base64.b64encode(
                    json.dumps(
                        {"release_profiles": {"services/demo": PROFILE}}
                    ).encode()
                ).decode(),
            }
        if path.startswith("git/commits/"):
            return {"tree": {"sha": SHA}}
        if path == "actions/runs/10":
            return self.run
        if path.startswith("collaborators/"):
            return {"permission": "write"}
        raise AssertionError(path)

    def verify(self):
        with patch.object(q, "archive", return_value=self.manifest):
            return q.verify(self.api, "services/demo", SHA, publisher="release.yml")

    def test_complete_exact_commit_qualifies(self):
        self.assertEqual(self.verify()["status"], "qualified")

    def test_broker_rerun_keeps_historical_route_for_discovery_only(self):
        later = {
            **self.run, "id": 11, "run_attempt": 2,
            "event": "workflow_dispatch", "display_title": "post-merge-fixture",
            "actor": {"id": 41898282},
            "created_at": "2026-01-02T00:00:00Z",
        }
        route = {"id": 23, "name": "result-route-11-1", "expired": False}
        self.api.pages.side_effect = lambda path, key: (
            [self.run, later] if key == "workflow_runs"
            else JOBS if key == "jobs"
            else [route] if "/11/" in path else [self.artifact]
        )
        original = self.api_call
        self.api.api.side_effect = lambda path: (
            {**later, "run_attempt": 1} if path == "actions/runs/11/attempts/1"
            else original(path)
        )
        # Historical routing authenticates association only. It cannot attest
        # to current-attempt jobs, status or qualification.
        with patch.object(q, "broker_result", return_value=SHA, create=True) as resolve:
            later.update(status="in_progress", conclusion=None)
            self.assertEqual(self.verify()["status"], "waiting")
            self.assertEqual(resolve.call_args.args[1]["run_attempt"], 1)
            later.update(status="completed", conclusion="failure")
            with self.assertRaisesRegex(q.Unverifiable, "Latest qualification failed"):
                self.verify()
            later.update(status="completed", conclusion="success")
            with patch.object(q, "validate_origin", side_effect=q.Unverifiable("Missing current route")):
                with self.assertRaisesRegex(q.Unverifiable, "Missing current route"):
                    self.verify()
            # No routing receipt at all must not hide a trusted producer that
            # might qualify this commit, even before its first identity upload.
            self.api.pages.side_effect = lambda path, key: (
                [self.run, later] if key == "workflow_runs" else []
            )
            for state in ["in_progress", "completed"]:
                later.update(status=state)
                with self.assertRaisesRegex(q.Unverifiable, "source association unresolved"):
                    self.verify()

    def test_broker_owner_errors_are_structured_qualification_blocks(self):
        import sys
        sys.path.insert(0, q.merge_owner_path())
        import merge_completion
        import merge_gate

        with patch.object(
            merge_completion, "route_target",
            side_effect=merge_gate.Blocked("owner failure"),
        ), patch.object(merge_gate, "GitHub"):
            with self.assertRaisesRegex(q.Unverifiable, "Broker routing evidence"):
                q.broker_result(self.api, self.run)

    def test_missing_producer_is_distinct_from_failed(self):
        self.api.pages.return_value = []
        self.api.pages.side_effect = None
        self.assertEqual(self.verify()["missing"], ["ci.yml"])

    def test_failed_latest_run_blocks(self):
        for state, conclusion in [("completed", "failure"), ("completed", "cancelled")]:
            with self.subTest(state=state, conclusion=conclusion):
                self.run.update(status=state, conclusion=conclusion)
                with self.assertRaises(q.Unverifiable):
                    self.verify()

    def test_pending_producer_does_not_hide_missing_work(self):
        self.run.update(status="in_progress", conclusion=None)
        profile = copy.deepcopy(PROFILE)
        profile["workflows"]["missing.yml"] = profile["workflows"]["ci.yml"]
        self.api.pages.side_effect = (
            lambda path, key: [] if "missing.yml" in path else [self.run]
        )
        with patch.object(q, "profile_at", return_value=(profile, q.digest(profile))):
            result = q.verify(self.api, "services/demo", SHA)
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["pending"], ["ci.yml"])
        self.assertEqual(result["missing"], ["missing.yml"])

    def test_latest_attempt_changes_during_verification(self):
        original = self.api_call
        self.api.api.side_effect = lambda path: (
            {**self.run, "run_attempt": 2, "status": "in_progress", "conclusion": None}
            if path == "actions/runs/10"
            else original(path)
        )
        with self.assertRaisesRegex(q.Unverifiable, "superseded"):
            self.verify()

    def test_profile_rechecks_earlier_producer_after_later_evidence(self):
        profile = copy.deepcopy(PROFILE)
        profile["workflows"]["second.yml"] = profile["workflows"]["ci.yml"]
        second = {**self.run, "id": 11, "path": ".github/workflows/second.yml"}
        artifact = {
            **self.artifact,
            "id": 23,
            "name": "release-qualification-services-demo-11-1",
        }
        manifest = {**self.manifest, "profile_digest": q.digest(profile)}
        later = {**manifest, "workflow": "second.yml", "run_id": 11}

        def pages(path, key):
            if key == "workflow_runs":
                return [second if "second.yml" in path else self.run.copy()]
            if key == "artifacts":
                return [artifact if "/11/" in path else self.artifact]
            return JOBS

        def archive(api, item, filename):
            if item["id"] == 23:
                self.run["run_attempt"] = 2
                return later
            return manifest

        original = self.api_call
        self.api.api.side_effect = (
            lambda path: second if path == "actions/runs/11" else original(path)
        )
        self.api.pages.side_effect = pages
        with (
            patch.object(q, "profile_at", return_value=(profile, q.digest(profile))),
            patch.object(q, "archive", side_effect=archive),
        ):
            with self.assertRaisesRegex(q.Unverifiable, "superseded while"):
                q.verify(self.api, "services/demo", SHA)

    def test_provenance_and_profile_drift(self):
        for key, value in [
            ("sha", "c" * 40),
            ("tree", "c" * 40),
            ("repository_id", 4),
            ("run_attempt", 2),
            ("profile_digest", "bad"),
            ("required_jobs", {}),
            ("jobs", {}),
        ]:
            saved = copy.deepcopy(self.manifest)
            with self.subTest(key=key):
                self.manifest[key] = value
                with self.assertRaises(q.Unverifiable):
                    self.verify()
            self.manifest = saved

    def test_expired_manifest_blocks(self):
        self.manifest["created_at"] = "2000-01-01T00:00:00+00:00"
        with self.assertRaisesRegex(q.Unverifiable, "expired"):
            self.verify()

    def test_matrix_member_missing_skipped_failed_or_duplicate(self):
        for jobs in [
            JOBS[:1],
            JOBS + JOBS[:1],
            [{**JOBS[0], "conclusion": "skipped"}, JOBS[1]],
            [{**JOBS[0], "conclusion": "failure"}, JOBS[1]],
        ]:
            with self.subTest(jobs=jobs), self.assertRaises(q.Unverifiable):
                q.successful_jobs(jobs, PROFILE["workflows"]["ci.yml"])

    def test_dispatch_rejects_mixed_incomplete_and_foreign_modes(self):
        valid = {
            "qualification_sha": SHA,
            "qualification_profile": "services/demo",
            "qualification_id": "f" * 32,
        }
        env = {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": "example/repo/.github/workflows/ci.yml@refs/heads/main",
            "GITHUB_ACTOR": "operator",
        }
        with patch.dict(q.os.environ, env):
            self.assertEqual(
                q.qualification_inputs(self.api, {"inputs": valid}, "ci.yml")[
                    "checkout_sha"
                ],
                SHA,
            )
            for values in [
                {**valid, "result_sha": SHA},
                {**valid, "candidate_pr": "2"},
                {**valid, "qualification_id": ""},
                {**valid, "qualification_profile": "unknown"},
            ]:
                with self.subTest(values=values), self.assertRaises(q.Unverifiable):
                    q.qualification_inputs(self.api, {"inputs": values}, "ci.yml")
        with patch.dict(q.os.environ, {**env, "GITHUB_REF": "refs/heads/feature"}):
            with self.assertRaises(q.Unverifiable):
                q.qualification_inputs(self.api, {"inputs": valid}, "ci.yml")

    def test_archive_identity_digest_expiry_and_member_names(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as z:
            z.writestr("qualification.json", "{}")
        data = raw.getvalue()
        item = {**self.artifact, "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
        with patch.object(q, "command", return_value=data):
            self.assertEqual(q.archive(self.api, item, "qualification.json"), {})
            for change in [
                {"expired": True},
                {"digest": "sha256:" + "0" * 64},
                {"size_in_bytes": 0},
            ]:
                with self.subTest(change=change), self.assertRaises(q.Unverifiable):
                    q.archive(self.api, {**item, **change}, "qualification.json")

    def test_historical_matrix_requires_all_cases_and_repeats(self):
        matrix = {
            "status": "passed",
            "coverage": "complete",
            "topology": "all",
            "expected_cases": 2,
            "expected_case_ids": ["one", "two"],
            "expected_repeats": 2,
            "cases": [
                {"case": n, "status": "passed", "repeat": "convergence"}
                for n in ["one", "two"]
            ],
        }
        artifact = {**self.artifact, "name": "vpngw-package-history-1"}
        definition = {
            "reports": [
                {
                    "name": "vpngw-package-history-{attempt}",
                    "contract": "package-history/v1",
                }
            ]
        }
        with patch.object(q, "archive", return_value={"matrix.json": matrix}):
            self.assertEqual(
                len(q.report_evidence(self.api, [artifact], definition, 1)), 1
            )
            for change in [
                {"coverage": "partial"},
                {"topology": "ha"},
                {"cases": matrix["cases"][:1]},
                {"expected_repeats": 1},
                {"cases": [{**matrix["cases"][0], "repeat": None}, matrix["cases"][1]]},
            ]:
                with (
                    self.subTest(change=change),
                    patch.object(
                        q, "archive", return_value={"matrix.json": {**matrix, **change}}
                    ),
                    self.assertRaises(q.Unverifiable),
                ):
                    q.report_evidence(self.api, [artifact], definition, 1)

    def test_native_requires_full_topology_and_every_phase(self):
        native = {
            "expected_nodeids": ["case"],
            "expected_cases": 1,
            "topology": "all",
            "status": "passed",
            "exit_code": 0,
            "outcomes": {
                "case": {"setup": "passed", "call": "passed", "teardown": "passed"}
            },
        }
        inputs = {
            "expected_nodeids": ["case"],
            "image_id": "image",
            "platform": "linux/amd64",
            "dependencies": {},
        }
        artifact = {**self.artifact, "name": "vpngw-native-1"}
        definition = {
            "reports": [
                {"name": "vpngw-native-{attempt}", "contract": "native-cases/v1"}
            ]
        }
        for change in [
            {},
            {"topology": "ordinary"},
            {"outcomes": {"case": {"call": "skipped"}}},
        ]:
            with patch.object(
                q,
                "archive",
                return_value={
                    "native.json": {**native, **change},
                    "inputs.json": inputs,
                },
            ):
                if change:
                    with self.assertRaises(q.Unverifiable):
                        q.report_evidence(self.api, [artifact], definition, 1)
                else:
                    self.assertEqual(
                        len(q.report_evidence(self.api, [artifact], definition, 1)), 1
                    )

    def test_profile_requires_known_publisher(self):
        with self.assertRaises(q.Unverifiable):
            q.verify(self.api, "services/demo", SHA, publisher="unrelated.yml")


if __name__ == "__main__":
    unittest.main()
