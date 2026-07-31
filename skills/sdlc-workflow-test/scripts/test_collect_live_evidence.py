#!/usr/bin/env python3
"""Focused tests for private live-evidence collection."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "collect_live_evidence.py"
SPEC = importlib.util.spec_from_file_location("collect_live_evidence", MODULE_PATH)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


class LiveEvidenceCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "verification"
        self.root.mkdir(mode=0o700)
        marker = self.root / collector.verifier.VERIFICATION_ROOT_MARKER
        marker.write_text(
            json.dumps(
                {"schema": collector.verifier.VERIFICATION_ROOT_MARKER_SCHEMA}
            ),
            encoding="utf-8",
        )
        os.chmod(marker, 0o600)
        self.source = Path(self.temporary.name) / "source.json"
        self.source.write_text('{"result":"pass"}\n', encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_collects_private_owner_local_artifact(self) -> None:
        result = collector.collect(
            self.root,
            owner_kind="lane",
            owner="idempotency",
            source=self.source,
            name="rerun.json",
        )
        artifact = self.root / result["path"]
        self.assertEqual(artifact.read_bytes(), self.source.read_bytes())
        self.assertEqual(collector.verifier.file_sha256(artifact), result["sha256"])
        if os.name == "posix":
            self.assertEqual(artifact.stat().st_mode & 0o077, 0)

    def test_same_bytes_are_idempotent(self) -> None:
        first = collector.collect(
            self.root,
            owner_kind="profile",
            owner="lightweight",
            source=self.source,
            name="checkpoint.json",
        )
        second = collector.collect(
            self.root,
            owner_kind="profile",
            owner="lightweight",
            source=self.source,
            name="checkpoint.json",
        )
        self.assertEqual(first, second)

    def test_different_bytes_do_not_overwrite(self) -> None:
        collector.collect(
            self.root,
            owner_kind="skill",
            owner="sdlc-start",
            source=self.source,
            name="intake.json",
        )
        self.source.write_text('{"result":"changed"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different bytes"):
            collector.collect(
                self.root,
                owner_kind="skill",
                owner="sdlc-start",
                source=self.source,
                name="intake.json",
            )

    def test_unknown_owner_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown lane owner"):
            collector.collect(
                self.root,
                owner_kind="lane",
                owner="invented",
                source=self.source,
                name="result.json",
            )

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX")
    def test_symlink_source_is_rejected(self) -> None:
        linked = Path(self.temporary.name) / "linked.json"
        linked.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            collector.collect(
                self.root,
                owner_kind="lane",
                owner="golden-path",
                source=linked,
                name="source.json",
            )

    def test_unsafe_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "safe filename"):
            collector.collect(
                self.root,
                owner_kind="lane",
                owner="golden-path",
                source=self.source,
                name="../escape.json",
            )

    @unittest.skipUnless(os.name == "posix", "private modes require POSIX")
    def test_permissive_marker_is_rejected(self) -> None:
        marker = self.root / collector.verifier.VERIFICATION_ROOT_MARKER
        marker.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "marker must be private"):
            collector.collect(
                self.root,
                owner_kind="lane",
                owner="golden-path",
                source=self.source,
                name="source.json",
            )

    @unittest.skipUnless(os.name == "posix", "hard-link safety requires POSIX")
    def test_hard_link_destination_is_rejected(self) -> None:
        destination = self.root / "evidence/golden-path/artifacts/source.json"
        destination.parent.mkdir(parents=True)
        os.link(self.source, destination)
        with self.assertRaisesRegex(ValueError, "destination is unsafe"):
            collector.collect(
                self.root,
                owner_kind="lane",
                owner="golden-path",
                source=self.source,
                name="source.json",
            )


if __name__ == "__main__":
    unittest.main()
