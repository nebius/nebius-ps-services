#!/usr/bin/env python3
"""Offline publication failure, identity and retry regressions."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from urllib.error import HTTPError
from unittest.mock import Mock, patch

import publication as pub

SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
OTHER = "sha256:" + "c" * 64
IDENTITY = {"tag": "example-v1.2.3", "tag_object": "d" * 40, "sha": SHA}


class PublicationTests(unittest.TestCase):
    def test_tag_version_is_strict_semver(self):
        self.assertEqual(pub.version("example-v0.2.3", "example"), "0.2.3")
        for tag in [
            "example-v01.2.3",
            "example-v1.2",
            "example-v1.2.3-rc1",
            "other-v1.2.3",
        ]:
            with self.subTest(tag=tag), self.assertRaises(pub.Unverifiable):
                pub.version(tag, "example")

    def test_notes_require_one_nonempty_section(self):
        self.assertEqual(
            pub.release_notes(
                "## [1.2.3]\n\n- Fixed.\n## [1.2.2]\nold", IDENTITY["tag"], "1.2.3"
            ),
            "- Fixed.\n",
        )
        for text in [
            "## [Unreleased]\nnotes",
            "## [1.2.3]\n",
            "## [1.2.3]\na\n## [1.2.3]\nb",
        ]:
            with self.assertRaises(pub.Unverifiable):
                pub.release_notes(text, IDENTITY["tag"], "1.2.3")

    def test_registry_errors_never_mean_absent(self):
        self.assertTrue(pub.registry_absent("manifest unknown: manifest unknown"))
        for message in [
            "404 not found",
            "unauthorized: manifest unknown",
            "manifest unknown TLS error",
            "connection refused",
            "503 manifest unknown",
            "forbidden",
        ]:
            self.assertFalse(pub.registry_absent(message), message)
        with patch.object(
            pub.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, "", "401 Unauthorized"),
        ):
            with self.assertRaises(pub.Unverifiable):
                pub.image_digest("registry/example:1.2.3")

    def test_empty_repository_requires_structured_evidence(self):
        for value in ({"tags": []}, {"tags": None}):
            self.assertTrue(pub.empty_repository(200, value))
        self.assertTrue(
            pub.empty_repository(404, {"errors": [{"code": "NAME_UNKNOWN"}]})
        )
        for status, value in [
            (200, {"tags": ["1.2.3"]}),
            (404, {}),
            (401, {"errors": [{"code": "UNAUTHORIZED"}]}),
            (403, {"errors": [{"code": "DENIED"}]}),
            (503, {}),
            (200, {}),
        ]:
            with self.assertRaises(pub.Unverifiable):
                pub.empty_repository(status, value)

    def test_registry_redirects_never_forward_authority(self):
        with self.assertRaises(pub.Unverifiable):
            pub.NoRedirect().redirect_request(
                None, None, 302, "", {}, "https://another.example/token"
            )

    def test_public_registry_observation_supports_anonymous_token(self):
        class Response(io.BytesIO):
            status = 200
            headers = {}

        challenge = HTTPError(
            "https://registry.example/v2/chart/tags/list",
            401,
            "Unauthorized",
            {
                "WWW-Authenticate": 'Bearer realm="https://registry.example/token",service="registry",scope="repository:chart:pull"'
            },
            None,
        )
        with patch.object(
            pub,
            "public_open",
            side_effect=[
                challenge,
                Response(b'{"token":"synthetic-anonymous"}'),
                Response(b'{"tags":[]}'),
            ],
        ) as opened:
            status, value = pub.registry_get(
                "oci://registry.example/chart", "tags/list?n=100"
            )
            self.assertTrue(pub.empty_repository(status, value))
            self.assertIn(
                "scope=repository%3Achart%3Apull", opened.call_args_list[1].args[0]
            )
            self.assertEqual(
                opened.call_args_list[2].args[0].get_header("Authorization"),
                "Bearer synthetic-anonymous",
            )

    def test_remote_tag_must_match_frozen_object(self):
        with patch.object(
            pub,
            "command",
            side_effect=[
                IDENTITY["tag_object"],
                SHA,
                "f" * 40 + "\trefs/tags/" + IDENTITY["tag"],
            ],
        ):
            with self.assertRaisesRegex(pub.Unverifiable, "Remote tag"):
                pub.tag_identity(IDENTITY["tag"])

    def test_image_alias_set_requires_complete_original_digest(self):
        tags = ["registry/example:1.2.3", "registry/example:1.2.3-gaaaaaaa"]
        manifest = {"digest": DIGEST, "tags": tags}
        self.assertEqual(
            pub.immutable_state(dict.fromkeys(tags), [], tags), ("absent", "")
        )
        self.assertEqual(
            pub.immutable_state(dict.fromkeys(tags, DIGEST), [manifest], tags),
            ("verified-existing", DIGEST),
        )
        for observed, manifests in [
            (dict(zip(tags, [DIGEST, None])), [manifest]),
            (dict.fromkeys(tags, OTHER), [manifest]),
            (dict.fromkeys(tags, DIGEST), []),
            (dict.fromkeys(tags, DIGEST), [manifest, {**manifest, "digest": OTHER}]),
        ]:
            with self.assertRaises(pub.Unverifiable):
                pub.immutable_state(observed, manifests, tags)

    def test_asset_hashes_detect_changed_bytes_and_extra_names(self):
        with tempfile.TemporaryDirectory() as folder:
            asset = Path(folder) / "package.whl"
            asset.write_bytes(b"original")
            expected = pub.asset_records([asset])
            pub.verify_assets(expected, pub.asset_records([asset]))
            asset.write_bytes(b"modified")
            with self.assertRaises(pub.Conflict):
                pub.verify_assets(expected, pub.asset_records([asset]))
            with self.assertRaises(pub.Unverifiable):
                pub.asset_records([asset, asset])

    def test_release_absence_requires_complete_successful_listing(self):
        api = Mock()
        with patch.object(pub, "tag_identity", return_value=IDENTITY):
            api.pages.return_value = []
            self.assertEqual(
                pub.release_state(api, "release.yml", IDENTITY["tag"]), "absent"
            )
            api.pages.side_effect = pub.Unverifiable("Authentication failed")
            with self.assertRaises(pub.Unverifiable):
                pub.release_state(api, "release.yml", IDENTITY["tag"])

    def test_existing_release_without_original_hashes_blocks_before_download(self):
        api = Mock()
        api.pages.return_value = [
            {"tag_name": IDENTITY["tag"], "id": 1, "draft": False}
        ]
        with (
            patch.object(pub, "tag_identity", return_value=IDENTITY),
            patch.object(pub, "trusted_manifests", return_value=[]),
        ):
            with self.assertRaisesRegex(pub.Unverifiable, "original hashes"):
                pub.release_state(api, "release.yml", IDENTITY["tag"])
        api.download.assert_not_called()

    def test_interrupted_prepublication_cannot_replace_original_hashes(self):
        runtime = {
            "event": "push",
            "ref": "refs/tags/" + IDENTITY["tag"],
            "workflow_sha": SHA,
        }
        original = {
            **IDENTITY,
            "assets": [{"name": "package.whl", "size": 3, "sha256": "e" * 64}],
        }
        with tempfile.TemporaryDirectory() as folder:
            asset, output = (
                Path(folder) / "package.whl",
                Path(folder) / "publication.json",
            )
            asset.write_bytes(b"new")
            argv = [
                "publication.py",
                "release-manifest",
                "--workflow",
                "release.yml",
                "--tag",
                IDENTITY["tag"],
                "--asset",
                str(asset),
                "--output",
                str(output),
            ]
            with (
                patch("sys.argv", argv),
                patch.object(pub, "runtime", return_value=runtime),
                patch.object(pub, "tag_identity", return_value=IDENTITY),
                patch.object(pub, "GitHub"),
                patch.object(pub, "trusted_manifests", return_value=[original]),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(pub.main(), 1)
                self.assertFalse(
                    output.exists(),
                    "A conflicting new manifest must never reach the upload step",
                )

    def test_remote_digest_must_equal_build_digest(self):
        with (
            patch("sys.argv", ["publication.py", "image-verify"]),
            patch.dict(
                os.environ,
                {
                    "TAGS": "registry/image:1.2.3\nregistry/image:1.2.3-gaaaaaaa",
                    "IMAGE_DIGEST": DIGEST,
                },
            ),
            patch.object(pub, "image_digest", side_effect=[DIGEST, OTHER]),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(pub.main(), 1)

    def test_image_reuse_rejects_changed_annotated_tag_object(self):
        manifest = {**IDENTITY, "digest": DIGEST, "tags": ["registry/image:1.2.3"]}
        with (
            patch(
                "sys.argv",
                [
                    "publication.py",
                    "image-state",
                    "--workflow",
                    "image.yml",
                    "--commit",
                    SHA,
                    "--tag",
                    IDENTITY["tag"],
                ],
            ),
            patch.dict(os.environ, {"TAGS": "registry/image:1.2.3"}),
            patch.object(pub, "image_digest", return_value=DIGEST),
            patch.object(pub, "GitHub"),
            patch.object(
                pub, "tag_identity", return_value={**IDENTITY, "tag_object": "f" * 40}
            ),
            patch.object(pub, "trusted_manifests", return_value=[manifest]),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(pub.main(), 1)

    def test_release_postcondition_never_accepts_absence(self):
        with (
            patch(
                "sys.argv",
                [
                    "publication.py",
                    "release-verify",
                    "--workflow",
                    "release.yml",
                    "--tag",
                    IDENTITY["tag"],
                ],
            ),
            patch.object(pub, "release_state", return_value="absent"),
            patch.object(pub, "GitHub"),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(pub.main(), 1)

    def test_manual_retry_ignores_release_lane_at_same_source(self):
        manual = {
            "id": 1,
            "path": ".github/workflows/image.yml",
            "head_repository": {"full_name": "example/project"},
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": SHA,
        }
        release = {**manual, "id": 2, "event": "push", "head_branch": IDENTITY["tag"]}
        value = {
            "schema": pub.IMAGE_SCHEMA,
            "repository": "example/project",
            "workflow": "image.yml",
            "run_id": 1,
            "run_attempt": 1,
            "workflow_sha": SHA,
            "event": "workflow_dispatch",
            "ref": "refs/heads/main",
            "sha": SHA,
            "digest": DIGEST,
            "tags": ["registry/image:sha-aaaaaaa"],
        }
        for runs in [[manual, release], [release, manual]]:
            api = Mock(repo="example/project")

            def pages(path, key=None):
                if path == "actions/workflows/image.yml/runs":
                    return runs
                self.assertEqual(
                    path,
                    "actions/runs/1/artifacts",
                    "Release lane must be excluded before artifact reads",
                )
                return [{"id": 3, "name": "publication-image-1-1", "expired": False}]

            api.pages.side_effect = pages
            api.api.return_value = manual
            api.artifact.return_value = value
            with (
                patch.object(pub, "command", return_value=""),
                patch.dict(os.environ, {"MAIN_BRANCH": "main"}),
            ):
                self.assertEqual(
                    pub.trusted_manifests(api, "image.yml", pub.IMAGE_SCHEMA, SHA),
                    [value],
                )

    def test_explicit_latest_reuses_verified_digest_without_rebuilding(self):
        tags = ["registry/image:sha-aaaaaaa", "registry/image:latest"]
        manifest = {"tags": tags[:1], "digest": DIGEST}
        for latest_digest, needed in [
            (None, "true"),
            (OTHER, "true"),
            (DIGEST, "false"),
        ]:
            with tempfile.TemporaryDirectory() as folder:
                destination = Path(folder) / "output"
                with (
                    patch(
                        "sys.argv",
                        [
                            "publication.py",
                            "image-state",
                            "--workflow",
                            "image.yml",
                            "--commit",
                            SHA,
                        ],
                    ),
                    patch.dict(
                        os.environ,
                        {
                            "TAGS": "\n".join(tags),
                            "PUBLISH_LATEST": "true",
                            "GITHUB_EVENT_NAME": "workflow_dispatch",
                            "GITHUB_REF": "refs/heads/main",
                            "GITHUB_OUTPUT": str(destination),
                        },
                    ),
                    patch.object(pub, "GitHub"),
                    patch.object(pub, "trusted_manifests", return_value=[manifest]),
                    patch.object(
                        pub, "image_digest", side_effect=[DIGEST, latest_digest]
                    ),
                ):
                    self.assertEqual(pub.main(), 0)
                values = dict(
                    line.split("=", 1) for line in destination.read_text().splitlines()
                )
                self.assertEqual(values["publish_needed"], "false")
                self.assertEqual(values["latest_needed"], needed)
                self.assertEqual(values["verified_source"], "registry/image@" + DIGEST)

    def test_manifest_provenance_rejects_wrong_run(self):
        api = Mock(repo="example/project")
        run = {
            "id": 1,
            "path": ".github/workflows/release.yml",
            "head_repository": {"full_name": api.repo},
            "event": "push",
            "head_branch": IDENTITY["tag"],
            "head_sha": SHA,
        }
        api.pages.side_effect = [
            [run],
            [{"id": 2, "name": "publication-release-1-1", "expired": False}],
        ]
        api.artifact.return_value = {
            **IDENTITY,
            "schema": pub.RELEASE_SCHEMA,
            "repository": api.repo,
            "workflow": "release.yml",
            "run_id": 999,
            "run_attempt": 1,
            "workflow_sha": SHA,
            "event": "push",
        }
        with self.assertRaises(pub.Unverifiable):
            pub.trusted_manifests(
                api, "release.yml", pub.RELEASE_SCHEMA, SHA, IDENTITY["tag"]
            )


if __name__ == "__main__":
    unittest.main()
