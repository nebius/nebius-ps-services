#!/usr/bin/env python3
"""Focused tests for canonical paired publication."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from project_specs_lib.contracts import canonical_document, digest
from project_specs_lib import transaction


SCRIPT = Path(__file__).with_name("project_specs.py")


REQUIREMENTS = """# Requirements

<!-- REQUIREMENT: REQ-001 status=active priority=P0 type=feature -->
### REQ-001: Publish one pair

#### User Story

As a maintainer, I need atomic project contracts.

#### Acceptance Criteria

- AC-001: Both documents publish together.

#### Negative Criteria

- NC-001: Stale bytes are rejected.

#### Validation Method

Run the transaction tests.

#### Test Method

Inject a second-write failure.

#### Evaluation Method

Compare both final byte streams.

<!-- /REQUIREMENT: REQ-001 -->
"""

DESIGN = """# Design

<!-- FEATURE: FEAT-001 reqs=REQ-001 status=ready delivery=not-started priority=P0 version=1 -->
### FEAT-001: Publish one pair

#### Requirements Covered

- REQ-001

#### Context Evidence

The canonical transaction owns both document writes.

#### Design Details

Validate then compare and swap both files under one scope lock.

#### Selected Option

Use paired publication with exact rollback.

#### Alternatives Considered

Separate writes permit split project truth.

#### Implementation Boundaries

Only canonical requirements and design documents are published.

#### Test-First Success Criteria

- TDD-001: A second-write failure restores both originals.

#### Validation Plan

Run focused transaction tests.

#### Test Plan

Exercise success, stale bytes, and rollback.

#### Evaluation Plan

Compare exact bytes and receipt digests.

#### Rollout And Rollback

Restore exact prior bytes on partial failure.

#### Done Definition

Both documents reflect one validated candidate pair.

#### Implementation Evidence

Not implemented yet.

#### Verification Evidence

Not independently verified yet.

<!-- /FEATURE: FEAT-001 -->
"""


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class ProjectSpecTransactionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        git(self.project, "init", "-q")
        git(self.project, "config", "user.email", "test@example.com")
        git(self.project, "config", "user.name", "Test User")
        (self.project / "README.md").write_text("# Project\n", encoding="utf-8")
        git(self.project, "add", "README.md")
        git(self.project, "commit", "-qm", "baseline")
        self.docs = self.project / "docs"
        self.docs.mkdir()
        self.old_requirements = canonical_document("requirements", REQUIREMENTS)
        self.old_design = canonical_document("design", DESIGN)
        (self.docs / "requirements.md").write_bytes(self.old_requirements)
        (self.docs / "design.md").write_bytes(self.old_design)
        self.candidate_requirements = self.old_requirements.replace(
            b"Publish one pair", b"Publish the canonical pair"
        )
        self.candidate_design = self.old_design.replace(
            b"Publish one pair", b"Publish the canonical pair"
        )
        self.previous_home = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(self.root / "codex")

    def tearDown(self) -> None:
        if self.previous_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.previous_home
        self.temporary.cleanup()

    def publish(self, operation_id: str = "op-1"):
        return transaction.publish_spec_pair(
            self.project,
            requirements_candidate=self.candidate_requirements,
            design_candidate=self.candidate_design,
            expected_git_head=git(self.project, "rev-parse", "HEAD"),
            expected_requirements_sha256=digest(self.old_requirements),
            expected_design_sha256=digest(self.old_design),
            operation_id=operation_id,
        )

    def test_publish_compares_and_swaps_both_documents(self) -> None:
        receipt = self.publish()
        self.assertEqual(receipt["contract_status"], "current")
        self.assertEqual(
            (self.docs / "requirements.md").read_bytes(), self.candidate_requirements
        )
        self.assertEqual((self.docs / "design.md").read_bytes(), self.candidate_design)

    def test_successful_publication_replays_by_operation_identity(self) -> None:
        first = self.publish("op-replay")
        second = self.publish("op-replay")
        self.assertEqual(second, first)

    def test_cli_publish_uses_the_same_paired_transaction(self) -> None:
        requirements_candidate = self.root / "requirements.candidate.md"
        design_candidate = self.root / "design.candidate.md"
        requirements_candidate.write_bytes(self.candidate_requirements)
        design_candidate.write_bytes(self.candidate_design)
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "publish",
                "--project-root",
                str(self.project),
                "--requirements-candidate",
                str(requirements_candidate),
                "--design-candidate",
                str(design_candidate),
                "--expected-head",
                git(self.project, "rev-parse", "HEAD"),
                "--expected-requirements-sha256",
                digest(self.old_requirements),
                "--expected-design-sha256",
                digest(self.old_design),
                "--operation-id",
                "op-cli",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ.copy(),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["operation_id"], "op-cli")
        self.assertEqual(
            (self.docs / "requirements.md").read_bytes(), self.candidate_requirements
        )
        self.assertEqual((self.docs / "design.md").read_bytes(), self.candidate_design)

    def test_stale_document_digest_rejects_without_writes(self) -> None:
        with self.assertRaisesRegex(transaction.ProjectSpecError, "bytes changed"):
            transaction.publish_spec_pair(
                self.project,
                requirements_candidate=self.candidate_requirements,
                design_candidate=self.candidate_design,
                expected_git_head=git(self.project, "rev-parse", "HEAD"),
                expected_requirements_sha256="0" * 64,
                expected_design_sha256=digest(self.old_design),
                operation_id="op-stale",
            )
        self.assertEqual(
            (self.docs / "requirements.md").read_bytes(), self.old_requirements
        )
        self.assertEqual((self.docs / "design.md").read_bytes(), self.old_design)

    def test_second_write_failure_restores_exact_original_pair(self) -> None:
        original_replace = transaction._replace_at
        calls = 0

        def fail_second(
            directory: int, name: str, value: bytes, expected: bytes | None
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second write failure")
            original_replace(directory, name, value, expected)

        with mock.patch.object(transaction, "_replace_at", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "injected"):
                self.publish("op-rollback")
        self.assertEqual(
            (self.docs / "requirements.md").read_bytes(), self.old_requirements
        )
        self.assertEqual((self.docs / "design.md").read_bytes(), self.old_design)

    def test_retry_completes_an_interrupted_known_pair(self) -> None:
        original_replace = transaction._replace_at
        calls = 0

        def interrupt_second(
            directory: int, name: str, value: bytes, expected: bytes | None
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt
            original_replace(directory, name, value, expected)

        with mock.patch.object(transaction, "_replace_at", side_effect=interrupt_second):
            with self.assertRaises(KeyboardInterrupt):
                self.publish("op-interrupted")
        self.assertEqual(
            (self.docs / "requirements.md").read_bytes(), self.candidate_requirements
        )
        self.assertEqual((self.docs / "design.md").read_bytes(), self.old_design)

        receipt = self.publish("op-interrupted")
        self.assertEqual(receipt["operation_id"], "op-interrupted")
        self.assertEqual(
            (self.docs / "requirements.md").read_bytes(), self.candidate_requirements
        )
        self.assertEqual((self.docs / "design.md").read_bytes(), self.candidate_design)

    def test_git_head_change_during_publication_rolls_back_pair(self) -> None:
        original_replace = transaction._replace_at
        calls = 0

        def commit_after_first(
            directory: int, name: str, value: bytes, expected: bytes | None
        ) -> None:
            nonlocal calls
            original_replace(directory, name, value, expected)
            calls += 1
            if calls == 1:
                (self.project / "README.md").write_text(
                    "# Project\n\nConcurrent commit.\n", encoding="utf-8"
                )
                git(self.project, "add", "README.md")
                git(self.project, "commit", "-qm", "concurrent commit")

        with mock.patch.object(
            transaction, "_replace_at", side_effect=commit_after_first
        ):
            with self.assertRaises(transaction.ProjectSpecError) as raised:
                self.publish("op-head-race")

        self.assertEqual(raised.exception.code, "CONCURRENT_MODIFICATION")
        self.assertEqual(
            (self.docs / "requirements.md").read_bytes(), self.old_requirements
        )
        self.assertEqual((self.docs / "design.md").read_bytes(), self.old_design)

    def test_document_change_at_replace_boundary_is_preserved(self) -> None:
        original_replace = transaction._replace_at
        concurrent = self.old_requirements + b"\nconcurrent writer\n"
        injected = False

        def edit_before_first(
            directory: int, name: str, value: bytes, expected: bytes | None
        ) -> None:
            nonlocal injected
            if not injected:
                injected = True
                (self.docs / "requirements.md").write_bytes(concurrent)
            original_replace(directory, name, value, expected)

        with mock.patch.object(transaction, "_replace_at", side_effect=edit_before_first):
            with self.assertRaises(transaction.ProjectSpecError) as raised:
                self.publish("op-document-race")

        self.assertEqual(raised.exception.code, "CONCURRENT_MODIFICATION")
        self.assertEqual((self.docs / "requirements.md").read_bytes(), concurrent)
        self.assertEqual((self.docs / "design.md").read_bytes(), self.old_design)

    def test_docs_directory_swap_cannot_redirect_publication(self) -> None:
        original_replace = transaction._replace_at
        retained = self.project / "docs-retained"
        outside = self.root / "outside"
        outside.mkdir()
        injected = False

        def swap_before_first(
            directory: int, name: str, value: bytes, expected: bytes | None
        ) -> None:
            nonlocal injected
            if not injected:
                injected = True
                self.docs.rename(retained)
                self.docs.symlink_to(outside, target_is_directory=True)
            original_replace(directory, name, value, expected)

        with mock.patch.object(transaction, "_replace_at", side_effect=swap_before_first):
            with self.assertRaises(transaction.ProjectSpecError) as raised:
                self.publish("op-directory-race")

        self.assertIn(raised.exception.code, {"CONCURRENT_MODIFICATION", "UNSAFE_SPEC"})
        self.assertFalse((outside / "requirements.md").exists())
        self.assertFalse((outside / "design.md").exists())
        self.assertEqual(
            (retained / "requirements.md").read_bytes(), self.old_requirements
        )
        self.assertEqual((retained / "design.md").read_bytes(), self.old_design)

    def test_canonical_reader_waits_for_complete_pair(self) -> None:
        original_replace = transaction._replace_at
        first_written = threading.Event()
        release_writer = threading.Event()
        reader_done = threading.Event()
        writer_error: list[BaseException] = []
        reader_receipt: list[dict[str, object]] = []
        calls = 0

        def pause_after_first(
            directory: int, name: str, value: bytes, expected: bytes | None
        ) -> None:
            nonlocal calls
            original_replace(directory, name, value, expected)
            calls += 1
            if calls == 1:
                first_written.set()
                if not release_writer.wait(2):
                    raise TimeoutError("reader did not attempt the shared lock")

        def publish() -> None:
            try:
                self.publish("op-reader-lock")
            except BaseException as error:
                writer_error.append(error)

        # The public reader takes the same shared lock; use its implementation
        # here rather than direct, intentionally unsynchronized file reads.
        from project_specs_lib.contracts import inspect_project

        def inspect_locked() -> None:
            try:
                reader_receipt.append(
                    inspect_project(self.project, require_tracked=False)
                )
            finally:
                reader_done.set()

        with mock.patch.object(transaction, "_replace_at", side_effect=pause_after_first):
            writer = threading.Thread(target=publish)
            writer.start()
            self.assertTrue(first_written.wait(2))
            reader = threading.Thread(target=inspect_locked)
            reader.start()
            self.assertFalse(reader_done.wait(0.1))
            release_writer.set()
            writer.join(2)
            reader.join(2)

        self.assertFalse(writer_error)
        self.assertTrue(reader_done.is_set())
        self.assertEqual(reader_receipt[0]["status"], "current")
        self.assertEqual(
            reader_receipt[0]["requirements"]["sha256"],
            digest(self.candidate_requirements),
        )
        self.assertEqual(
            reader_receipt[0]["design"]["sha256"], digest(self.candidate_design)
        )


if __name__ == "__main__":
    unittest.main()
