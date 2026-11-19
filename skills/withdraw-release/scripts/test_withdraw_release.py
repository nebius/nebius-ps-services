"""Offline provider fixtures; no live release or credential use."""

import ast
import contextlib
import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import withdraw_release as wr


class FakeGitHub:
    repo = "example/widgets"

    def __init__(self, *, immutable=False, private=False, count=2):
        self.release = dict(
            id=10,
            tag_name="widget-v1.2.3",
            name="Widget 1.2.3",
            body="Original notes.\n\nKeep these bytes.\n",
            draft=False,
            prerelease=False,
            immutable=immutable,
        )
        self.repository = dict(id=1, private=private, permissions={"push": True})
        self.assets = {
            str(i): dict(
                id=i,
                name=f"asset-{i}.zip",
                size=12,
                digest="sha256:abc",
                state="uploaded",
                created_at="a",
                updated_at="b",
            )
            for i in range(1, count + 1)
        }
        self.tag_value = dict(object="a" * 40, commit="b" * 40)
        self.effects = []
        self.fault = None
        self.latest_stale = 0
        self.latest_reads = 0

    def tag(self, _):
        return copy.deepcopy(self.tag_value)

    def pages(self, route):
        if route == "releases":
            return [copy.deepcopy(self.release)]
        assert route == "releases/10/assets", route
        return copy.deepcopy(list(self.assets.values()))

    def api(self, route="", method="GET", payload=None, *, absent=False):
        if method != "GET":
            self.effects.append((method, route, copy.deepcopy(payload)))
            if self.fault:
                self.fault(self, route, method, payload)
            if method == "PATCH":
                assert route == "releases/10"
                assert set(payload) == {"name", "body", "prerelease", "make_latest"}
                self.release.update(
                    {k: v for k, v in payload.items() if k != "make_latest"}
                )
            elif method == "DELETE":
                assert not self.release["immutable"], "immutable deletion attempted"
                assert route.startswith("releases/assets/"), (
                    "release/tag deletion attempted"
                )
                self.assets.pop(route.rsplit("/", 1)[-1])
            else:
                raise AssertionError(method)
            return copy.deepcopy(self.release) if method == "PATCH" else None
        if not route:
            return copy.deepcopy(self.repository)
        if route == "releases/10":
            return copy.deepcopy(self.release)
        if route == "releases/latest":
            self.latest_reads += 1
            return {"id": 10} if self.latest_reads <= self.latest_stale else {"id": 9}
        if route.startswith("releases/assets/"):
            return copy.deepcopy(self.assets.get(route.rsplit("/", 1)[-1]))
        raise AssertionError(route)


class WithdrawalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "private"
        self.api = FakeGitHub()
        self.probe = Mock(return_value=True)
        self.sleep = Mock()

    def run_withdraw(self, **kwargs):
        return wr.withdraw(
            self.api,
            copy.deepcopy(self.api.release),
            state_root=self.root,
            probe=self.probe,
            sleep=self.sleep,
            **kwargs,
        )

    def test_mutable_preserves_notes_tag_and_repeats_without_effects(self):
        original = self.api.release["body"]
        tag = self.api.tag_value.copy()
        result = self.run_withdraw()
        self.assertEqual(result["status"], "WITHDRAWN")
        self.assertFalse(self.api.assets)
        self.assertEqual(self.api.tag_value, tag)
        self.assertTrue(self.api.release["prerelease"])
        self.assertEqual(wr.parse_notice(self.api.release["body"])[0], original)
        self.assertEqual(self.api.release["name"], "WITHDRAWN — Widget 1.2.3")
        self.assertEqual(
            [e[0] for e in self.api.effects], ["PATCH", "DELETE", "DELETE", "PATCH"]
        )
        self.assertEqual(self.probe.call_count, 2)
        before = copy.deepcopy(self.api.effects)
        self.run_withdraw()
        self.assertEqual(self.api.effects, before)
        data = next(self.root.glob("*.json")).read_text()
        self.assertNotIn(original, data)
        self.assertNotIn(wr.DEFAULT_REASON, data)

    def test_immutable_changes_only_metadata_and_reports_remaining(self):
        self.api = FakeGitHub(immutable=True)
        assets = copy.deepcopy(self.api.assets)
        result = self.run_withdraw()
        self.assertEqual(result["status"], "WITHDRAWN_DOWNLOADS_REMAIN")
        self.assertEqual(self.api.assets, assets)
        self.assertIn("downloads remain available", self.api.release["body"])
        self.assertEqual([e[0] for e in self.api.effects], ["PATCH"])
        self.probe.assert_not_called()
        self.run_withdraw()
        self.assertEqual(len(self.api.effects), 1)

    def test_dry_run_does_not_create_state_or_effects(self):
        self.assertEqual(self.run_withdraw(dry_run=True)["status"], "DRY_RUN")
        self.assertFalse(self.root.exists())
        self.assertFalse(self.api.effects)

    def test_private_uses_authenticated_absence_without_anonymous_probe(self):
        self.api = FakeGitHub(private=True)
        self.assertEqual(self.run_withdraw()["status"], "WITHDRAWN")
        self.probe.assert_not_called()

    def test_denied_permission_no_effect_or_checkpoint(self):
        self.api.repository["permissions"]["push"] = False
        with self.assertRaisesRegex(wr.WithdrawalError, "write access"):
            self.run_withdraw()
        self.assertFalse(self.root.exists())
        self.assertFalse(self.api.effects)

    def test_unknown_protection_and_draft_fail_before_effects(self):
        for field, value in [("immutable", None), ("draft", True)]:
            self.api = FakeGitHub()
            self.api.release[field] = value
            with self.assertRaises(wr.WithdrawalError):
                self.run_withdraw()
            self.assertFalse(self.api.effects)

    def test_empty_mutable_release(self):
        self.api = FakeGitHub(count=0)
        self.assertEqual(self.run_withdraw()["status"], "WITHDRAWN")
        self.assertFalse(any(e[0] == "DELETE" for e in self.api.effects))

    def test_lost_patch_response_reconciles_without_duplicate_write(self):
        def fault(api, route, method, payload):
            if method == "PATCH":
                api.fault = None
                api.release.update(
                    {k: v for k, v in payload.items() if k != "make_latest"}
                )
                raise wr.WithdrawalError("lost response")

        self.api.fault = fault
        self.assertEqual(self.run_withdraw()["status"], "WITHDRAWN")
        self.assertEqual(sum(e[0] == "PATCH" for e in self.api.effects), 2)

    def test_interrupted_patch_narrows_accepted_metadata_before_deletion(self):
        original = copy.deepcopy(self.api.release)

        def interrupt(api, route, method, payload):
            if method == "PATCH":
                api.release.update(
                    {k: v for k, v in payload.items() if k != "make_latest"}
                )
                raise KeyboardInterrupt

        self.api.fault = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_withdraw()
        self.api.fault = None
        base = self.api.api
        reads = 0

        def revert(route="", method="GET", *args, **kwargs):
            nonlocal reads
            if route == "releases/10" and method == "GET":
                reads += 1
                if reads == 4:
                    self.api.release = copy.deepcopy(original)
            return base(route, method, *args, **kwargs)

        with patch.object(self.api, "api", side_effect=revert):
            with self.assertRaisesRegex(wr.WithdrawalError, "metadata edit"):
                self.run_withdraw()
        self.assertFalse(any(e[0] == "DELETE" for e in self.api.effects))

    def test_lost_delete_response_is_reconciled(self):
        def fault(api, route, method, payload):
            if method == "DELETE":
                api.fault = None
                api.assets.pop(route.rsplit("/", 1)[-1])
                raise wr.WithdrawalError("lost response")

        self.api.fault = fault
        self.assertEqual(self.run_withdraw()["status"], "WITHDRAWN")
        self.assertEqual(sum(e[0] == "DELETE" for e in self.api.effects), 2)

    def test_interrupted_second_delete_resumes_after_prior_removal(self):
        def fault(api, route, method, payload):
            if method == "DELETE" and route.endswith("/2"):
                api.assets.pop("2")
                raise KeyboardInterrupt

        self.api.fault = fault
        with self.assertRaises(KeyboardInterrupt):
            self.run_withdraw()
        self.api.fault = None
        self.assertEqual(self.run_withdraw()["status"], "WITHDRAWN")
        self.assertEqual(sum(e[0] == "DELETE" for e in self.api.effects), 2)

    def test_delayed_pending_delete_survives_previously_removed_assets(self):
        def timeout(api, route, method, payload):
            if method == "DELETE" and route.endswith("/2"):
                raise wr.WithdrawalError("response timed out")

        self.api.fault = timeout
        with self.assertRaisesRegex(wr.WithdrawalError, "removal not confirmed"):
            self.run_withdraw()
        self.assertNotIn("1", self.api.assets)
        self.api.fault = None
        base_pages = self.api.pages
        reads = 0

        def late_completion(route):
            nonlocal reads
            if route.endswith("/assets"):
                reads += 1
                # Earlier observations still see the pending asset. The original
                # request completes during later inventory reconciliation.
                if reads == 5:
                    self.api.assets.pop("2", None)
            return base_pages(route)

        with patch.object(self.api, "pages", side_effect=late_completion):
            self.assertEqual(self.run_withdraw()["status"], "WITHDRAWN")
        deletes = [e[1] for e in self.api.effects if e[0] == "DELETE"]
        self.assertTrue(all(route == "releases/assets/2" for route in deletes[2:]))
        self.assertEqual(self.run_withdraw()["status"], "WITHDRAWN")

    def test_resume_reconciles_pending_before_lexicographically_sorted_assets(self):
        self.api = FakeGitHub(count=10)

        def timeout(api, route, method, payload):
            if method == "DELETE" and route.endswith("/2"):
                raise wr.WithdrawalError("response timed out")

        self.api.fault = timeout
        with self.assertRaisesRegex(wr.WithdrawalError, "removal not confirmed"):
            self.run_withdraw()

        def late_completion(api, route, method, payload):
            if method == "DELETE" and route.endswith("/10"):
                api.assets.pop("2", None)

        self.api.fault = late_completion
        self.assertEqual(self.run_withdraw()["status"], "WITHDRAWN")
        deletes = [e[1] for e in self.api.effects if e[0] == "DELETE"]
        self.assertEqual(
            deletes[:3], ["releases/assets/1", "releases/assets/2", "releases/assets/2"]
        )
        self.assertEqual(self.run_withdraw()["status"], "WITHDRAWN")

    def test_denied_delete_keeps_warning_and_resumes(self):
        def fault(api, route, method, payload):
            if method == "DELETE":
                raise wr.WithdrawalError("denied")

        self.api.fault = fault
        with self.assertRaisesRegex(wr.WithdrawalError, "removal not confirmed"):
            self.run_withdraw()
        self.assertEqual(wr.parse_notice(self.api.release["body"])[1], "WITHDRAWING")
        self.api.fault = None
        self.assertEqual(self.run_withdraw()["status"], "WITHDRAWN")

    def test_tag_metadata_asset_and_repository_drift_stop_resume(self):
        for kind in ("tag", "metadata", "asset", "repo", "protection"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                self.root = Path(td).resolve().resolve() / "state"
                self.api = FakeGitHub(immutable=True)
                self.run_withdraw()
                if kind == "tag":
                    self.api.tag_value["commit"] = "c" * 40
                elif kind == "metadata":
                    self.api.release["body"] += "concurrent edit"
                elif kind == "asset":
                    self.api.assets["1"]["digest"] = "changed"
                elif kind == "repo":
                    # Same selected name now resolves a different repository.
                    self.api.repository["id"] = 2
                else:
                    self.api.release["immutable"] = False
                before = copy.deepcopy(self.api.effects)
                with self.assertRaises(wr.WithdrawalError):
                    self.run_withdraw()
                self.assertEqual(before, self.api.effects)

    def test_replaced_release_at_same_tag_is_rejected(self):
        self.run_withdraw()
        self.api = FakeGitHub()
        self.api.release["id"] = 11
        base_api = self.api.api

        def replaced(route="", *args, **kwargs):
            return base_api(
                route.replace("releases/11", "releases/10"), *args, **kwargs
            )

        base_pages = self.api.pages
        with (
            patch.object(self.api, "api", side_effect=replaced),
            patch.object(
                self.api,
                "pages",
                side_effect=lambda route: base_pages(
                    route.replace("releases/11", "releases/10")
                ),
            ),
        ):
            with self.assertRaisesRegex(wr.WithdrawalError, "identity"):
                self.run_withdraw()
        self.assertFalse(self.api.effects)

    def test_concurrent_new_asset_stops_before_delete(self):
        def fault(api, route, method, payload):
            if method == "PATCH":
                api.assets["99"] = {**api.assets["1"], "id": 99}

        self.api.fault = fault
        with self.assertRaisesRegex(wr.WithdrawalError, "Unexpected added"):
            self.run_withdraw()
        self.assertFalse(any(e[0] == "DELETE" for e in self.api.effects))

    def test_reason_change_and_unknown_marker_rejected(self):
        self.run_withdraw(reason="Do not use this version.")
        with self.assertRaisesRegex(wr.WithdrawalError, "reason differs"):
            self.run_withdraw(reason="Another reason.")
        self.api = FakeGitHub()
        self.api.release["body"] = "<!-- withdraw-release:v9 -->\ntext"
        with self.assertRaisesRegex(wr.WithdrawalError, "marker"):
            self.run_withdraw()

    def test_stale_latest_retried_but_bounded(self):
        self.api.latest_stale = 2
        self.run_withdraw()
        self.assertEqual(self.sleep.call_count, 2)
        self.api.latest_reads = 0
        self.api.latest_stale = 100
        with self.assertRaisesRegex(wr.WithdrawalError, "verification pending"):
            self.run_withdraw()
        self.assertEqual(self.api.latest_reads, 4)

    def test_download_endpoint_still_available_is_incomplete(self):
        self.probe.return_value = False
        with self.assertRaisesRegex(wr.WithdrawalError, "verification pending"):
            self.run_withdraw()
        self.assertFalse(
            json.loads(next(self.root.glob("*.json")).read_text())["complete"]
        )

    def test_symlink_state_and_checkpoint_rejected(self):
        self.root.symlink_to(Path(self.temp.name), target_is_directory=True)
        with self.assertRaisesRegex(wr.WithdrawalError, "symlinks"):
            self.run_withdraw()
        self.assertFalse(self.api.effects)
        self.root.unlink()
        store = wr.Store(self.root, self.api.repo, self.api.release["tag_name"])
        target = Path(self.temp.name) / "data"
        target.write_text("{}")
        (self.root / (store.key + ".json")).symlink_to(target)
        with self.assertRaises(OSError):
            self.run_withdraw()
        self.assertFalse(self.api.effects)

    def test_fifo_checkpoint_rejected_without_blocking(self):
        store = wr.Store(self.root, self.api.repo, self.api.release["tag_name"])
        os.mkfifo(self.root / (store.key + ".json"), 0o600)
        with self.assertRaisesRegex(wr.WithdrawalError, "Unsafe checkpoint"):
            self.run_withdraw()
        self.assertFalse(self.api.effects)

    def test_directory_swap_does_not_redirect_checkpoint(self):
        store = wr.Store(self.root, self.api.repo, self.api.release["tag_name"])
        other = self.root.parent / "other"
        other.mkdir(mode=0o700)
        with store.locked():
            self.root.rename(self.root.parent / "original")
            self.root.symlink_to(other, target_is_directory=True)
            with self.assertRaisesRegex(wr.WithdrawalError, "directory changed"):
                store.save({"schema": wr.SCHEMA})
        self.assertEqual(list(other.iterdir()), [])

    def test_checkpoint_lock_rejects_second_writer(self):
        store = wr.Store(self.root, self.api.repo, self.api.release["tag_name"])
        with store.locked(), self.assertRaisesRegex(wr.WithdrawalError, "active"):
            self.run_withdraw()
        self.assertFalse(self.api.effects)

    def test_help_is_inert_without_checkout(self):
        script = Path(wr.__file__)
        result = subprocess.run(
            ["python3", "-B", str(script), "--help"],
            cwd=self.temp.name,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--release", result.stdout)
        self.assertFalse(self.root.exists())


class SelectionAndTransportTests(unittest.TestCase):
    def test_marker_wire_contract_matches_both_publication_consumers(self):
        skills = Path(wr.__file__).resolve().parents[2]
        consumers = [
            skills / "github-workflows/scripts/publication.py",
            skills / "publish-release/scripts/release_session.py",
        ]
        if not all(p.exists() for p in consumers):
            self.skipTest("Cross-skill contract runs in the source catalog")
        for path in consumers:
            constants = [
                n.value
                for n in ast.walk(ast.parse(path.read_text()))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            self.assertIn(wr.MARKER, constants)
        self.assertTrue(wr.START.startswith(wr.MARKER))

    def test_exact_tag_needs_no_project_metadata(self):
        api = FakeGitHub()
        self.assertEqual(
            wr.resolve_release(api, "widget-v1.2.3", Path("/"), Path("/"), "x/y")["id"],
            10,
        )

    def test_short_version_requires_matching_unique_project_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            project = root / "apps/tool"
            project.mkdir(parents=True)
            wf = root / ".github/workflows"
            wf.mkdir(parents=True)
            source = "env:\n  APP_DIR: apps/tool\n  TAG_PREFIX: widget\n\njobs:\n  release:\n"
            (wf / "release.yml").write_text(source)
            api = FakeGitHub()
            self.assertEqual(
                wr.resolve_release(api, "1.2.3", root, project, api.repo)["id"], 10
            )
            with self.assertRaisesRegex(wr.WithdrawalError, "Cross-repository"):
                wr.resolve_release(api, "1.2.3", root, project, "other/repo")
            (wf / "other.yml").write_text(source.replace("widget", "another"))
            with self.assertRaisesRegex(wr.WithdrawalError, "ambiguous"):
                wr.resolve_release(api, "1.2.3", root, project, api.repo)

    def test_ambiguous_or_malformed_workflow_metadata_requires_exact_tag(self):
        sources = [
            "env:\n  APP_DIR: app\n  TAG_PREFIX: wrong\n  TAG_PREFIX: correct\n",
            "env:\n  APP_DIR: app\n  APP_DIR: other\n  TAG_PREFIX: widget\n",
            "env:\n  APP_DIR: app\n  TAG_PREFIX: wrong\nenv:\n  APP_DIR: app\n  TAG_PREFIX: correct\n",
            "env:\n  APP_DIR: app\n  TAG_PREFIX: 'widget\n",
        ]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            project = root / "app"
            project.mkdir()
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            for source in sources:
                with self.subTest(source=source):
                    (workflows / "release.yml").write_text(
                        source + "jobs:\n  release: {}\n"
                    )
                    with self.assertRaisesRegex(wr.WithdrawalError, "exact.*tag"):
                        wr.workflow_prefix(root, project)
            for value in ("widget", "'widget'", '"widget"'):
                (workflows / "release.yml").write_text(
                    f"env:\n  APP_DIR: app\n  TAG_PREFIX: {value}\njobs:\n  release: {{}}\n"
                )
                self.assertEqual(wr.workflow_prefix(root, project), "widget")

    def test_private_not_found_and_duplicate_selection_are_errors(self):
        api = FakeGitHub()
        with (
            patch.object(api, "pages", return_value=[]),
            self.assertRaises(wr.WithdrawalError),
        ):
            wr.resolve_release(api, "missing", Path("/"), Path("/"), api.repo)
        with (
            patch.object(api, "pages", return_value=[api.release] * 2),
            self.assertRaises(wr.WithdrawalError),
        ):
            wr.resolve_release(
                api, api.release["tag_name"], Path("/"), Path("/"), api.repo
            )

    def test_pagination_complete_and_bounded(self):
        api = wr.GitHub("example/demo", Path("/"))
        with patch.object(api, "api", side_effect=[[{}] * 100, [{}] * 3]) as call:
            self.assertEqual(len(api.pages("releases")), 103)
            self.assertIn("page=2", call.call_args[0][0])
        with patch.object(api, "api", return_value=[{}] * 100) as call:
            with self.assertRaisesRegex(wr.WithdrawalError, "limit"):
                api.pages("releases")
            self.assertEqual(call.call_count, 100)

    def test_transport_explicit_methods_json_stdin_and_status(self):
        api = wr.GitHub("example/demo", Path("/"))
        response = subprocess.CompletedProcess(
            [], 0, 'HTTP/2.0 200 OK\nContent-Type: application/json\n\n{"id": 1}', ""
        )
        with patch.object(wr, "command", return_value=response) as run:
            self.assertEqual(api.api("releases/1", "PATCH", {"name": "title"})["id"], 1)
            self.assertIn("--input", run.call_args[0][0])
            self.assertEqual(
                json.loads(run.call_args.kwargs["data"]), {"name": "title"}
            )
            self.assertNotIn("title", run.call_args[0][0])
        for status in (403, 404, 500):
            response = subprocess.CompletedProcess(
                [], 1, f"HTTP/2.0 {status} Error\n\n{{}}", "secret"
            )
            with patch.object(wr, "command", return_value=response):
                if status == 404:
                    self.assertIsNone(api.api("releases/latest", absent=True))
                else:
                    with self.assertRaises(wr.WithdrawalError) as error:
                        api.api("releases/latest", absent=True)
                    self.assertNotIn("secret", str(error.exception))

    def test_credential_bearing_origins_rejected(self):
        for remote in (
            "https://token@github.com/example/demo",
            "https://github.com/../repo",
            "https://other.example/example/demo",
            "https://github.com/example/demo?token=x",
        ):
            with self.assertRaises(wr.WithdrawalError):
                wr.remote_repo(remote)
        self.assertEqual(
            wr.remote_repo("git@github.com:example/demo.git"), "example/demo"
        )

    def test_dry_run_main_is_read_only(self):
        api = FakeGitHub()
        with (
            patch.object(
                wr, "scope", return_value=(Path("/"), Path("/"), api.repo, api.repo)
            ),
            patch.object(wr, "GitHub", return_value=api),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(wr.main(["--release", "widget-v1.2.3", "--dry-run"]), 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "DRY_RUN")
        self.assertFalse(api.effects)


if __name__ == "__main__":
    unittest.main()
