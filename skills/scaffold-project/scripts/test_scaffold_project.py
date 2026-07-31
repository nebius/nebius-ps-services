#!/usr/bin/env python3
"""Unit tests for the scaffold-project bundle executor."""

from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock


SCRIPT_PATH = Path(__file__).with_name("scaffold_project.py")
SPEC = importlib.util.spec_from_file_location("scaffold_project", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ScaffoldProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.target = self.root / "project"
        self.bundle.mkdir(mode=0o700)
        (self.bundle / "candidates").mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _draft(
        self,
        operations: list[dict[str, Any]],
        *,
        architecture_sources: list[dict[str, str]] | None = None,
        capabilities: list[dict[str, Any]] | None = None,
        materialization_units: list[dict[str, Any]] | None = None,
        runtime_units: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        candidate_sets: list[dict[str, Any]] = []
        validations: list[dict[str, Any]] = []
        for operation in operations:
            candidate_set_id = operation["candidate_set_id"]
            manifest_relative = (
                f"candidates/{operation['owner']}/{candidate_set_id}/manifest.json"
            )
            manifest_path = self.bundle / manifest_relative
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            current = manifest_path.parent
            while current != self.bundle:
                os.chmod(current, 0o700)
                current = current.parent
            validation = {
                "id": f"{candidate_set_id}:static",
                "owner": operation["owner"],
                "materialization_unit_id": operation["materialization_unit_id"],
                "candidate_set_id": candidate_set_id,
                "phase": "candidate",
                "command": "fixture static validation",
                "network_required": False,
                "status": "passed",
            }
            manifest_parent = Path(manifest_relative).parent
            candidate_relative = Path(operation["candidate"]).relative_to(
                manifest_parent
            )
            candidate_bytes = (self.bundle / operation["candidate"]).read_bytes()
            inputs = {
                "owner": operation["owner"],
                "path": operation["path"],
                "unit": operation["materialization_unit_id"],
            }
            input_sha256 = hashlib.sha256(MODULE._canonical_bytes(inputs)).hexdigest()
            manifest = {
                "schema_version": 1,
                "candidate_set_id": candidate_set_id,
                "owner": operation["owner"],
                "materialization_unit_id": operation["materialization_unit_id"],
                "profile": (
                    "react-vite"
                    if operation["owner"] == "frontend-project"
                    else "test-fixture"
                ),
                "input_sha256": input_sha256,
                "inputs": inputs,
                "files": [
                    {
                        "path": operation["path"],
                        "candidate": candidate_relative.as_posix(),
                        "mode": operation["mode"],
                        "sha256": hashlib.sha256(candidate_bytes).hexdigest(),
                    }
                ],
                "validations": [validation],
            }
            manifest_bytes = (
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            manifest_path.write_bytes(manifest_bytes)
            os.chmod(manifest_path, 0o600)
            candidate_sets.append(
                {
                    "id": candidate_set_id,
                    "owner": operation["owner"],
                    "materialization_unit_id": operation["materialization_unit_id"],
                    "profile": manifest["profile"],
                    "input_sha256": input_sha256,
                    "manifest": manifest_relative,
                    "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "operation_paths": [operation["path"]],
                    "validation_ids": [validation["id"]],
                }
            )
            validations.append(validation)
        return {
            "schema_version": 2,
            "project": {
                "name": "sample-project",
                "repository_shape": "single-component",
                "architecture": {
                    "approval": (
                        "approved-artifact" if architecture_sources else "direct-user"
                    ),
                    "approval_reference": "approved-design",
                    "handoff": None,
                    "sources": architecture_sources or [],
                },
            },
            "capabilities": capabilities
            or [
                {
                    "id": "application",
                    "kind": "application",
                    "status": "required",
                    "materialization_unit_ids": ["application-source"],
                    "trigger": None,
                },
                {
                    "id": "future-cache",
                    "kind": "cache",
                    "status": "conditional",
                    "materialization_unit_ids": [],
                    "trigger": "Add only after measured cache pressure.",
                },
            ],
            "materialization_units": materialization_units
            or [
                {
                    "id": "application-source",
                    "kind": "application-source",
                    "path": "src",
                    "language": "python",
                    "framework": None,
                    "owner": "python-project",
                    "invocation_scope": "coordinated-candidate",
                }
            ],
            "runtime_units": runtime_units
            or [
                {
                    "id": "application-runtime",
                    "kind": "process",
                    "capability_id": "application",
                    "materialization_unit_id": "application-source",
                    "runtime": "local-process",
                }
            ],
            "external_services": [
                {
                    "id": "database",
                    "kind": "relational-database",
                    "technology": "postgresql",
                    "status": "required",
                    "materialization": "configuration-only",
                    "trigger": None,
                }
            ],
            "candidate_sets": candidate_sets,
            "operations": operations,
            "validations": validations,
            "execution": {
                "allow_apply": True,
                "initialize_git": False,
                "install_dependencies": False,
                "network_access": False,
                "provision_services": False,
                "deploy": False,
            },
            "safety": {"reserved_paths": ["CUSTOM_INSTRUCTIONS.md"]},
        }

    def _candidate(self, relative: str, content: str) -> str:
        path = self.bundle / "candidates" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        current = path.parent
        while current != self.bundle:
            os.chmod(current, 0o700)
            current = current.parent
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o600)
        return path.relative_to(self.bundle).as_posix()

    def _operation(
        self,
        path: str,
        content: str,
        *,
        action: str = "create",
        owner: str = "python-project",
        materialization_unit_id: str | None = None,
        mode: str = "0644",
    ) -> dict[str, Any]:
        if materialization_unit_id is None and owner == "python-project":
            materialization_unit_id = "application-source"
        candidate_set_id = f"{owner}-{hashlib.sha256(path.encode()).hexdigest()[:12]}"
        candidate = self._candidate(
            f"{owner}/{candidate_set_id}/files/{path.replace('/', '__')}",
            content,
        )
        return {
            "path": path,
            "action": action,
            "owner": owner,
            "materialization_unit_id": materialization_unit_id,
            "candidate": candidate,
            "mode": mode,
            "candidate_set_id": candidate_set_id,
        }

    def _write_draft(self, draft: dict[str, Any]) -> None:
        (self.bundle / "manifest.draft.json").write_text(
            json.dumps(draft, indent=2) + "\n", encoding="utf-8"
        )
        os.chmod(self.bundle / "manifest.draft.json", 0o600)

    def _finalize(self, draft: dict[str, Any]) -> dict[str, Any]:
        self._write_draft(draft)
        return MODULE.finalize_bundle(self.target, self.bundle)

    def _manifest(self) -> dict[str, Any]:
        return json.loads((self.bundle / "manifest.json").read_text(encoding="utf-8"))

    def test_greenfield_apply_and_idempotent_resume(self) -> None:
        result = self._finalize(
            self._draft(
                [
                    self._operation(
                        "README.md", "# Sample\n", owner="scaffold-project"
                    ),
                    self._operation("src/sample/__init__.py", '"""Sample."""\n'),
                ]
            )
        )

        applied = MODULE.apply_bundle(self.target, self.bundle, result["bundle_digest"])
        self.assertEqual(applied["applied"], 2)
        self.assertEqual(
            (self.target / "src/sample/__init__.py").read_text(encoding="utf-8"),
            '"""Sample."""\n',
        )
        before_mtime = (self.target / "README.md").stat().st_mtime_ns

        repeated = MODULE.apply_bundle(
            self.target, self.bundle, result["bundle_digest"]
        )
        self.assertEqual(repeated["applied"], 0)
        self.assertEqual(repeated["unchanged"], 2)
        self.assertEqual((self.target / "README.md").stat().st_mtime_ns, before_mtime)

    def test_cli_finalize_status_and_apply_wiring(self) -> None:
        self._write_draft(
            self._draft(
                [
                    self._operation(
                        "README.md", "# Sample\n", owner="scaffold-project"
                    ),
                    self._operation("src/sample.py", "value = 1\n"),
                ]
            )
        )
        finalize = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "finalize",
                "--target",
                str(self.target),
                "--bundle",
                str(self.bundle),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        finalized = json.loads(finalize.stdout)
        self.assertEqual(finalized["action"], "finalize")

        before = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "status",
                "--target",
                str(self.target),
                "--bundle",
                str(self.bundle),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(before.stdout)["counts"]["before"], 2)

        applied = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "apply",
                "--target",
                str(self.target),
                "--bundle",
                str(self.bundle),
                "--expected-digest",
                finalized["bundle_digest"],
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(applied.stdout)["status"], "complete")
        self.assertEqual((self.target / "README.md").read_text(), "# Sample\n")

    def test_status_reports_before_and_after(self) -> None:
        self.target.mkdir()
        result = self._finalize(
            self._draft([self._operation("src/sample.py", "value = 1\n")])
        )
        before, before_code = MODULE.status_bundle(self.target, self.bundle)
        self.assertEqual(before_code, 0)
        self.assertEqual(before["counts"]["before"], 1)

        MODULE.apply_bundle(self.target, self.bundle, result["bundle_digest"])
        after, after_code = MODULE.status_bundle(self.target, self.bundle)
        self.assertEqual(after_code, 0)
        self.assertEqual(after["counts"]["after"], 1)

    def test_finalize_rejects_existing_create_collision(self) -> None:
        self.target.mkdir()
        (self.target / "README.md").write_text("human\n", encoding="utf-8")
        draft = self._draft(
            [
                self._operation("README.md", "candidate\n", owner="scaffold-project"),
                self._operation("src/sample.py", "value = 1\n"),
            ]
        )
        with self.assertRaisesRegex(MODULE.ScaffoldError, "collides"):
            self._finalize(draft)
        self.assertEqual(
            (self.target / "README.md").read_text(encoding="utf-8"), "human\n"
        )

    def test_preflight_conflict_causes_zero_writes(self) -> None:
        self.target.mkdir()
        result = self._finalize(
            self._draft(
                [
                    self._operation("Makefile", "a\n", owner="scaffold-project"),
                    self._operation("README.md", "b\n", owner="scaffold-project"),
                    self._operation("src/sample.py", "value = 1\n"),
                ]
            )
        )
        (self.target / "README.md").write_text("human\n", encoding="utf-8")

        with self.assertRaisesRegex(MODULE.ScaffoldError, "block all writes"):
            MODULE.apply_bundle(self.target, self.bundle, result["bundle_digest"])
        self.assertFalse((self.target / "Makefile").exists())
        self.assertEqual(
            (self.target / "README.md").read_text(encoding="utf-8"), "human\n"
        )

    def test_greenfield_target_appearance_blocks_first_apply(self) -> None:
        result = self._finalize(
            self._draft(
                [
                    self._operation(
                        "README.md", "# Sample\n", owner="scaffold-project"
                    ),
                    self._operation("src/sample.py", "value = 1\n"),
                ]
            )
        )
        self.target.mkdir()

        with self.assertRaisesRegex(
            MODULE.ScaffoldError, "appeared after finalization"
        ):
            MODULE.apply_bundle(self.target, self.bundle, result["bundle_digest"])
        self.assertFalse((self.target / "README.md").exists())

    def test_existing_target_replacement_after_preflight_is_rejected(self) -> None:
        self.target.mkdir()
        result = self._finalize(
            self._draft([self._operation("src/sample.py", "value = 1\n")])
        )
        original_write = MODULE._write_private_json
        replaced = False

        def replace_after_preflight(path: Path, value: dict[str, Any]) -> None:
            nonlocal replaced
            original_write(path, value)
            if path.name == "journal.json" and not replaced:
                self.target.rmdir()
                self.target.mkdir()
                replaced = True

        with (
            mock.patch.object(
                MODULE,
                "_write_private_json",
                side_effect=replace_after_preflight,
            ),
            self.assertRaisesRegex(
                MODULE.ScaffoldError, "target directory identity changed"
            ),
        ):
            MODULE.apply_bundle(
                self.target,
                self.bundle,
                result["bundle_digest"],
            )
        self.assertFalse((self.target / "src/sample.py").exists())

    def test_greenfield_target_substitution_after_creation_gets_no_write(
        self,
    ) -> None:
        result = self._finalize(
            self._draft([self._operation("src/sample.py", "value = 1\n")])
        )
        original_fsync = MODULE.os.fsync
        original_mkdir = MODULE.os.mkdir
        original_rmdir = MODULE.os.rmdir
        replaced = False

        def replace_after_publication(descriptor: int) -> None:
            nonlocal replaced
            original_fsync(descriptor)
            if self.target.exists() and not replaced:
                original_rmdir(self.target)
                original_mkdir(self.target)
                replaced = True

        with (
            mock.patch.object(
                MODULE.os,
                "fsync",
                side_effect=replace_after_publication,
            ),
            self.assertRaises(MODULE.ScaffoldError),
        ):
            MODULE.apply_bundle(
                self.target,
                self.bundle,
                result["bundle_digest"],
            )
        self.assertTrue(replaced)
        self.assertFalse((self.target / "src/sample.py").exists())

    def test_greenfield_target_appearance_during_publication_fails_closed(
        self,
    ) -> None:
        result = self._finalize(
            self._draft([self._operation("src/sample.py", "value = 1\n")])
        )
        original_publish = MODULE._rename_directory_noreplace

        def appear_before_publish(
            parent_descriptor: int,
            source_name: str,
            target_name: str,
        ) -> None:
            self.target.mkdir()
            original_publish(
                parent_descriptor,
                source_name,
                target_name,
            )

        with (
            mock.patch.object(
                MODULE,
                "_rename_directory_noreplace",
                side_effect=appear_before_publish,
            ),
            self.assertRaisesRegex(
                MODULE.ScaffoldError,
                "target appeared after preflight",
            ),
        ):
            MODULE.apply_bundle(
                self.target,
                self.bundle,
                result["bundle_digest"],
            )
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual(
            list(self.root.glob(".scaffold-project-*.directory")),
            [],
        )

    def test_directory_appearance_blocks_first_apply_before_writes(self) -> None:
        self.target.mkdir()
        result = self._finalize(
            self._draft(
                [
                    self._operation("README.md", "a\n", owner="scaffold-project"),
                    self._operation("src/b.txt", "b\n"),
                ]
            )
        )
        (self.target / "src").mkdir()

        with self.assertRaisesRegex(MODULE.ScaffoldError, "planned directory appeared"):
            MODULE.apply_bundle(self.target, self.bundle, result["bundle_digest"])
        self.assertFalse((self.target / "README.md").exists())
        self.assertFalse((self.target / "src/b.txt").exists())

    def test_partial_resume_accepts_only_journaled_created_directories(self) -> None:
        self.target.mkdir()
        result = self._finalize(
            self._draft(
                [
                    self._operation("src/a.txt", "a\n"),
                    self._operation("src/b.txt", "b\n"),
                ]
            )
        )

        def interrupt_second(index: int, operation: dict[str, Any]) -> None:
            del operation
            if index == 1:
                raise RuntimeError("simulated interruption")

        with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
            MODULE.apply_bundle(
                self.target,
                self.bundle,
                result["bundle_digest"],
                before_operation=interrupt_second,
            )

        resumed = MODULE.apply_bundle(
            self.target,
            self.bundle,
            result["bundle_digest"],
        )
        self.assertEqual(resumed["applied"], 1)
        self.assertEqual(resumed["unchanged"], 1)
        self.assertEqual((self.target / "src/b.txt").read_text(encoding="utf-8"), "b\n")

    def test_semantic_merge_is_digest_bound_and_backed_up(self) -> None:
        self.target.mkdir()
        readme = self.target / "README.md"
        readme.write_text("# Existing\n", encoding="utf-8")
        original_digest = MODULE._sha256_path(readme)
        result = self._finalize(
            self._draft(
                [
                    self._operation(
                        "README.md",
                        "# Existing\n\n"
                        "<!-- scaffold-project:begin:commands -->\n"
                        "## Generated commands\n\n- `make check`\n"
                        "<!-- scaffold-project:end:commands -->\n",
                        action="semantic_merge",
                        owner="scaffold-project",
                    ),
                    self._operation("src/sample.py", "value = 1\n"),
                ]
            )
        )

        MODULE.apply_bundle(self.target, self.bundle, result["bundle_digest"])
        self.assertIn("Generated commands", readme.read_text(encoding="utf-8"))
        backup = self.bundle / "backups" / original_digest
        self.assertEqual(backup.read_text(encoding="utf-8"), "# Existing\n")
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

    def test_stale_semantic_merge_blocks_without_change(self) -> None:
        self.target.mkdir()
        readme = self.target / "README.md"
        readme.write_text("# Existing\n", encoding="utf-8")
        result = self._finalize(
            self._draft(
                [
                    self._operation(
                        "README.md",
                        "# Existing\n\n"
                        "<!-- scaffold-project:begin:integration -->\n"
                        "Generated\n"
                        "<!-- scaffold-project:end:integration -->\n",
                        action="semantic_merge",
                        owner="scaffold-project",
                    ),
                    self._operation("src/sample.py", "value = 1\n"),
                ]
            )
        )
        readme.write_text("# Human update\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ScaffoldError, "block all writes"):
            MODULE.apply_bundle(self.target, self.bundle, result["bundle_digest"])
        self.assertEqual(readme.read_text(encoding="utf-8"), "# Human update\n")

    def test_semantic_merge_rejects_unsafe_backup_path(self) -> None:
        self.target.mkdir()
        readme = self.target / "README.md"
        readme.write_text("# Existing\n", encoding="utf-8")
        result = self._finalize(
            self._draft(
                [
                    self._operation(
                        "README.md",
                        "# Existing\n\n"
                        "<!-- scaffold-project:begin:integration -->\n"
                        "Generated\n"
                        "<!-- scaffold-project:end:integration -->\n",
                        action="semantic_merge",
                        owner="scaffold-project",
                    ),
                    self._operation("src/sample.py", "value = 1\n"),
                ]
            )
        )
        backup_directory = self.bundle / "backups"
        backup_directory.mkdir()
        os.chmod(backup_directory, 0o700)
        backup_path = backup_directory / MODULE._sha256_path(readme)
        backup_path.symlink_to(self.root / "outside-backup")

        with self.assertRaisesRegex(MODULE.ScaffoldError, "regular file"):
            MODULE.apply_bundle(self.target, self.bundle, result["bundle_digest"])
        self.assertEqual(readme.read_text(encoding="utf-8"), "# Existing\n")

    def test_semantic_merge_rejects_source_and_non_additive_readme(self) -> None:
        self.target.mkdir()
        source = self.target / "src/app.py"
        source.parent.mkdir()
        source.write_text("SAFE = True\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ScaffoldError, "supported only"):
            self._finalize(
                self._draft(
                    [
                        self._operation(
                            "src/app.py",
                            "SAFE = False\n",
                            action="semantic_merge",
                        )
                    ]
                )
            )
        self.assertEqual(source.read_text(encoding="utf-8"), "SAFE = True\n")

        other_bundle = self.root / "non-additive"
        other_bundle.mkdir(mode=0o700)
        (other_bundle / "candidates").mkdir(mode=0o700)
        self.bundle = other_bundle
        readme = self.target / "README.md"
        readme.write_text("# Human\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ScaffoldError, "preserve"):
            self._finalize(
                self._draft(
                    [
                        self._operation(
                            "README.md",
                            "# Replaced\n",
                            action="semantic_merge",
                            owner="scaffold-project",
                        ),
                        self._operation("src/other.py", "value = 1\n"),
                    ]
                )
            )
        self.assertEqual(readme.read_text(encoding="utf-8"), "# Human\n")

    def test_semantic_merge_rejects_broad_gitignore_rule(self) -> None:
        self.target.mkdir()
        gitignore = self.target / ".gitignore"
        gitignore.write_text(".venv/\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ScaffoldError, "unsupported .gitignore"):
            self._finalize(
                self._draft(
                    [
                        self._operation(
                            ".gitignore",
                            ".venv/\n*\n",
                            action="semantic_merge",
                            owner="gitignore",
                        ),
                        self._operation("src/sample.py", "value = 1\n"),
                    ]
                )
            )
        self.assertEqual(gitignore.read_text(encoding="utf-8"), ".venv/\n")

    def test_semantic_merge_rejects_duplicate_marker_identity(self) -> None:
        self.target.mkdir()
        readme = self.target / "README.md"
        existing = (
            "# Existing\n\n"
            "<!-- scaffold-project:begin:commands -->\n"
            "Existing commands\n"
            "<!-- scaffold-project:end:commands -->\n"
        )
        readme.write_text(existing, encoding="utf-8")
        with self.assertRaisesRegex(
            MODULE.ScaffoldError, "marker identity already exists"
        ):
            self._finalize(
                self._draft(
                    [
                        self._operation(
                            "README.md",
                            existing + "\n<!-- scaffold-project:begin:commands -->\n"
                            "New commands\n"
                            "<!-- scaffold-project:end:commands -->\n",
                            action="semantic_merge",
                            owner="scaffold-project",
                        ),
                        self._operation("src/sample.py", "value = 1\n"),
                    ]
                )
            )

    def test_mid_apply_race_stops_and_journals_partial_state(self) -> None:
        self.target.mkdir()
        result = self._finalize(
            self._draft(
                [
                    self._operation("Makefile", "a\n", owner="scaffold-project"),
                    self._operation("README.md", "b\n", owner="scaffold-project"),
                    self._operation("src/sample.py", "value = 1\n"),
                ]
            )
        )

        def mutate_second(index: int, operation: dict[str, Any]) -> None:
            if index == 1:
                (self.target / operation["path"]).write_text(
                    "human\n", encoding="utf-8"
                )

        with self.assertRaisesRegex(MODULE.ScaffoldError, "drifted during apply"):
            MODULE.apply_bundle(
                self.target,
                self.bundle,
                result["bundle_digest"],
                before_operation=mutate_second,
            )
        self.assertEqual((self.target / "Makefile").read_text(encoding="utf-8"), "a\n")
        self.assertEqual(
            (self.target / "README.md").read_text(encoding="utf-8"), "human\n"
        )
        journal = json.loads((self.bundle / "journal.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["status"], "partial")
        self.assertEqual(journal["operations"][0]["state"], "applied")
        self.assertEqual(journal["operations"][1]["state"], "conflict")

    def test_resume_rejects_replaced_created_target_and_nested_directory(self) -> None:
        result = self._finalize(
            self._draft([self._operation("src/a.py", "value = 1\n")])
        )

        def interrupt_first(index: int, operation: dict[str, Any]) -> None:
            del operation
            if index == 0:
                raise RuntimeError("interrupt before first operation")

        with self.assertRaisesRegex(RuntimeError, "interrupt before first"):
            MODULE.apply_bundle(
                self.target,
                self.bundle,
                result["bundle_digest"],
                before_operation=interrupt_first,
            )
        self.target.rmdir()
        self.target.mkdir()
        with self.assertRaisesRegex(MODULE.ScaffoldError, "target directory identity"):
            MODULE.apply_bundle(self.target, self.bundle, result["bundle_digest"])

        nested_bundle = self.root / "nested-bundle"
        nested_bundle.mkdir(mode=0o700)
        (nested_bundle / "candidates").mkdir(mode=0o700)
        self.bundle = nested_bundle
        nested_target = self.root / "nested-target"
        nested_target.mkdir()
        self.target = nested_target
        nested_result = self._finalize(
            self._draft(
                [
                    self._operation("src/a.py", "a = 1\n"),
                    self._operation("src/b.py", "b = 1\n"),
                ]
            )
        )

        def interrupt_second(index: int, operation: dict[str, Any]) -> None:
            del operation
            if index == 1:
                raise RuntimeError("interrupt before second operation")

        with self.assertRaisesRegex(RuntimeError, "interrupt before second"):
            MODULE.apply_bundle(
                self.target,
                self.bundle,
                nested_result["bundle_digest"],
                before_operation=interrupt_second,
            )
        (self.target / "src/a.py").unlink()
        (self.target / "src").rmdir()
        (self.target / "src").mkdir()
        with self.assertRaisesRegex(MODULE.ScaffoldError, "directory identity changed"):
            MODULE.apply_bundle(
                self.target,
                self.bundle,
                nested_result["bundle_digest"],
            )

    def test_rejects_traversal_reserved_and_case_collisions(self) -> None:
        cases = [
            [self._operation("../escape.txt", "x\n", owner="scaffold-project")],
            [self._operation("src//escape.txt", "x\n")],
            [self._operation("AGENTS.md", "x\n", owner="scaffold-project")],
            [
                self._operation("src/Source.txt", "a\n"),
                self._operation("src/source.txt", "b\n"),
            ],
            [
                self._operation("src/Straße.py", "a = 1\n"),
                self._operation("src/STRASSE.py", "b = 1\n"),
            ],
            [
                self._operation(
                    "CUSTOM_INSTRUCTIONS.md", "x\n", owner="scaffold-project"
                )
            ],
        ]
        for operations in cases:
            with self.subTest(paths=[item["path"] for item in operations]):
                bundle = self.bundle
                self._write_draft(self._draft(operations))
                with self.assertRaises(MODULE.ScaffoldError):
                    MODULE.finalize_bundle(self.target, bundle)

    def test_rejects_repository_control_paths_case_insensitively(self) -> None:
        for path in (
            ".git/hooks/post-checkout.sh",
            ".GIT/hooks/post-merge.sh",
            ".hg/hooks/update.sh",
            ".svn/hooks/update.sh",
        ):
            with self.subTest(path=path):
                bundle = self.root / re.sub(r"[^a-z0-9]+", "-", path.casefold()).strip(
                    "-"
                )
                bundle.mkdir(mode=0o700)
                (bundle / "candidates").mkdir(mode=0o700)
                self.bundle = bundle
                with self.assertRaisesRegex(
                    MODULE.ScaffoldError, "repository control paths are reserved"
                ):
                    self._finalize(
                        self._draft(
                            [
                                self._operation(
                                    path,
                                    "#!/bin/sh\nexit 0\n",
                                    owner="shell-scripting",
                                    materialization_unit_id=None,
                                    mode="0755",
                                ),
                                self._operation("src/sample.py", "value = 1\n"),
                            ]
                        )
                    )

    def test_rejects_symlink_parent_and_special_target(self) -> None:
        self.target.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "file.py").write_text("outside = True\n", encoding="utf-8")
        (self.target / "src").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(MODULE.ScaffoldError, "real directory"):
            self._finalize(self._draft([self._operation("src/file.py", "value = 1\n")]))
        (self.target / "src").unlink()
        (self.target / "src").mkdir()

        special_target = self.target / ".gitignore"
        os.mkfifo(special_target)
        draft = self._draft(
            [
                self._operation(
                    ".gitignore",
                    "replacement\n",
                    action="semantic_merge",
                    owner="gitignore",
                ),
                self._operation("src/sample.py", "value = 1\n"),
            ]
        )
        with self.assertRaisesRegex(MODULE.ScaffoldError, "regular file"):
            self._finalize(draft)

    def test_private_bundle_must_be_real_private_and_outside_git(self) -> None:
        real_bundle = self.root / "real-bundle"
        real_bundle.mkdir(mode=0o700)
        linked_bundle = self.root / "linked-bundle"
        linked_bundle.symlink_to(real_bundle, target_is_directory=True)
        with self.assertRaisesRegex(MODULE.ScaffoldError, "not a symlink"):
            MODULE.finalize_bundle(self.target, linked_bundle)

        checkout = self.root / "checkout"
        (checkout / ".git").mkdir(parents=True)
        repository_bundle = checkout / "private"
        repository_bundle.mkdir()
        with self.assertRaisesRegex(MODULE.ScaffoldError, "outside a Git worktree"):
            MODULE.finalize_bundle(self.target, repository_bundle)

    def test_private_bundle_rejects_internal_directory_symlinks(self) -> None:
        outside = self.root / "outside-private"
        outside.mkdir()
        operation = self._operation("src/file.py", "ignored\n")
        candidate_path = self.bundle / operation["candidate"]
        outside_candidate = outside / candidate_path.name
        outside_candidate.write_text("private = True\n", encoding="utf-8")
        os.chmod(outside_candidate, 0o600)
        candidate_path.unlink()
        candidate_path.parent.rmdir()
        candidate_path.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(MODULE.ScaffoldError, "real directory"):
            self._finalize(self._draft([operation]))

        payload_bundle = self.root / "payload-bundle"
        payload_bundle.mkdir(mode=0o700)
        (payload_bundle / "candidates").mkdir(mode=0o700)
        self.bundle = payload_bundle
        payload_operation = self._operation("src/file.py", "value = 1\n")
        (self.bundle / "payloads").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(MODULE.ScaffoldError, "real directory"):
            self._finalize(self._draft([payload_operation]))

    def test_semantic_merge_rejects_symlinked_backup_directory(self) -> None:
        self.target.mkdir()
        readme = self.target / "README.md"
        readme.write_text("# Existing\n", encoding="utf-8")
        result = self._finalize(
            self._draft(
                [
                    self._operation(
                        "README.md",
                        "# Existing\n\n"
                        "<!-- scaffold-project:begin:integration -->\n"
                        "Generated\n"
                        "<!-- scaffold-project:end:integration -->\n",
                        action="semantic_merge",
                        owner="scaffold-project",
                    ),
                    self._operation("src/sample.py", "value = 1\n"),
                ]
            )
        )
        outside = self.root / "outside-backups"
        outside.mkdir()
        (self.bundle / "backups").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(MODULE.ScaffoldError, "real directory"):
            MODULE.apply_bundle(self.target, self.bundle, result["bundle_digest"])
        self.assertEqual(readme.read_text(encoding="utf-8"), "# Existing\n")
        self.assertEqual(list(outside.iterdir()), [])

    def test_manifest_permissions_are_enforced(self) -> None:
        self.target.mkdir()
        self._finalize(self._draft([self._operation("src/file.py", "value = 1\n")]))
        os.chmod(self.bundle / "manifest.json", 0o644)

        with self.assertRaisesRegex(MODULE.ScaffoldError, "permissions must be 0600"):
            MODULE.validate_bundle(self.target, self.bundle)

    def test_payload_tamper_is_rejected(self) -> None:
        self.target.mkdir()
        self._finalize(self._draft([self._operation("src/file.py", "value = 1\n")]))
        manifest = self._manifest()
        payload = self.bundle / "payloads" / manifest["operations"][0]["payload_sha256"]
        payload.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ScaffoldError, "payload digest mismatch"):
            MODULE.validate_bundle(self.target, self.bundle)

    def test_manifest_tamper_is_rejected(self) -> None:
        self.target.mkdir()
        self._finalize(self._draft([self._operation("src/file.py", "value = 1\n")]))
        manifest = self._manifest()
        manifest["operations"][0]["owner"] = "scaffold-project"
        (self.bundle / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(MODULE.ScaffoldError, "bundle digest mismatch"):
            MODULE.validate_bundle(self.target, self.bundle)

    def test_recomputed_manifest_cannot_bypass_semantic_merge_contract(self) -> None:
        self.target.mkdir()
        readme = self.target / "README.md"
        readme.write_text("# Human content\n", encoding="utf-8")
        result = self._finalize(
            self._draft(
                [
                    self._operation(
                        "README.md",
                        "# Human content\n\n"
                        "<!-- scaffold-project:begin:commands -->\n"
                        "Generated commands\n"
                        "<!-- scaffold-project:end:commands -->\n",
                        action="semantic_merge",
                        owner="scaffold-project",
                    ),
                    self._operation("src/sample.py", "value = 1\n"),
                ]
            )
        )
        manifest = self._manifest()
        operation = manifest["operations"][0]
        replacement = b"# Replaced human content\n"
        replacement_digest = MODULE._sha256_bytes(replacement)
        replacement_path = self.bundle / "payloads" / replacement_digest
        replacement_path.write_bytes(replacement)
        os.chmod(replacement_path, 0o600)
        operation["payload_sha256"] = replacement_digest
        operation["after"]["sha256"] = replacement_digest
        digest_input = dict(manifest)
        digest_input.pop("bundle_digest")
        manifest["bundle_digest"] = MODULE._sha256_bytes(
            MODULE._canonical_bytes(digest_input)
        )
        MODULE._write_private_json(self.bundle / "manifest.json", manifest)

        bypass_error = "finalized file binding mismatch|semantic_merge"
        with self.assertRaisesRegex(MODULE.ScaffoldError, bypass_error):
            MODULE.validate_bundle(self.target, self.bundle)
        with self.assertRaisesRegex(MODULE.ScaffoldError, bypass_error):
            MODULE.apply_bundle(
                self.target,
                self.bundle,
                manifest["bundle_digest"],
            )
        self.assertEqual(readme.read_text(encoding="utf-8"), "# Human content\n")
        self.assertNotEqual(result["bundle_digest"], manifest["bundle_digest"])

    def test_apply_uses_the_exact_validated_manifest(self) -> None:
        self.target.mkdir()
        approved_bundle = self.bundle
        approved = self._finalize(
            self._draft([self._operation("src/sample.py", "approved = True\n")])
        )

        replacement_bundle = self.root / "replacement-bundle"
        replacement_bundle.mkdir(mode=0o700)
        (replacement_bundle / "candidates").mkdir(mode=0o700)
        self.bundle = replacement_bundle
        self._finalize(
            self._draft([self._operation("src/sample.py", "approved = False\n")])
        )
        replacement_manifest = self._manifest()
        replacement_payload_digest = replacement_manifest["operations"][0][
            "payload_sha256"
        ]
        replacement_payload = (
            replacement_bundle / "payloads" / replacement_payload_digest
        )
        copied_payload = approved_bundle / "payloads" / replacement_payload_digest
        copied_payload.write_bytes(replacement_payload.read_bytes())
        os.chmod(copied_payload, 0o600)
        self.bundle = approved_bundle

        load_validated_bundle = MODULE._load_validated_bundle

        def replace_manifest_after_validation(
            target: Path, bundle: Path
        ) -> tuple[dict[str, Any], dict[str, Any], Path]:
            validation, manifest, canonical_bundle = load_validated_bundle(
                target, bundle
            )
            MODULE._write_private_json(
                canonical_bundle / "manifest.json",
                replacement_manifest,
            )
            return validation, manifest, canonical_bundle

        with mock.patch.object(
            MODULE,
            "_load_validated_bundle",
            side_effect=replace_manifest_after_validation,
        ):
            applied = MODULE.apply_bundle(
                self.target,
                approved_bundle,
                approved["bundle_digest"],
            )

        self.assertEqual(applied["bundle_digest"], approved["bundle_digest"])
        self.assertEqual(
            (self.target / "src/sample.py").read_text(encoding="utf-8"),
            "approved = True\n",
        )

    def test_concurrent_apply_cannot_corrupt_completed_journal(self) -> None:
        self.target.mkdir()
        finalized = self._finalize(
            self._draft([self._operation("src/sample.py", "value = 1\n")])
        )
        entered_operation = threading.Event()
        release_operation = threading.Event()
        worker_result: list[dict[str, Any]] = []
        worker_error: list[BaseException] = []

        def hold_first_operation(_index: int, _operation: dict[str, Any]) -> None:
            entered_operation.set()
            if not release_operation.wait(timeout=5):
                raise RuntimeError("test apply lock wait timed out")

        def run_first_apply() -> None:
            try:
                worker_result.append(
                    MODULE.apply_bundle(
                        self.target,
                        self.bundle,
                        finalized["bundle_digest"],
                        before_operation=hold_first_operation,
                    )
                )
            except BaseException as error:
                worker_error.append(error)

        worker = threading.Thread(target=run_first_apply)
        worker.start()
        self.assertTrue(entered_operation.wait(timeout=5))
        try:
            with self.assertRaisesRegex(MODULE.ScaffoldError, "already in progress"):
                MODULE.apply_bundle(
                    self.target,
                    self.bundle,
                    finalized["bundle_digest"],
                )
        finally:
            release_operation.set()
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(worker_error, [])
        self.assertEqual(worker_result[0]["status"], "complete")
        journal = json.loads((self.bundle / "journal.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["status"], "complete")
        self.assertEqual(journal["operations"][0]["state"], "applied")
        self.assertEqual(
            (self.target / "src/sample.py").read_text(encoding="utf-8"),
            "value = 1\n",
        )

    def test_architecture_source_drift_is_rejected(self) -> None:
        self.target.mkdir()
        architecture = self.root / "architecture.json"
        architecture.write_text('{"approved": true}\n', encoding="utf-8")
        sources = [
            {
                "path": str(architecture),
                "sha256": MODULE._sha256_path(architecture),
            }
        ]
        self._finalize(
            self._draft(
                [self._operation("src/file.py", "value = 1\n")],
                architecture_sources=sources,
            )
        )
        architecture.write_text('{"approved": false}\n', encoding="utf-8")
        with self.assertRaisesRegex(
            MODULE.ScaffoldError, "architecture source changed"
        ):
            MODULE.validate_bundle(self.target, self.bundle)

    def test_architecture_source_drift_during_apply_is_rejected(self) -> None:
        self.target.mkdir()
        architecture = self.root / "architecture.json"
        architecture.write_text('{"approved": true}\n', encoding="utf-8")
        result = self._finalize(
            self._draft(
                [self._operation("src/file.py", "value = 1\n")],
                architecture_sources=[
                    {
                        "path": str(architecture),
                        "sha256": MODULE._sha256_path(architecture),
                    }
                ],
            )
        )

        def mutate_architecture(
            index: int,
            operation: dict[str, Any],
        ) -> None:
            del index, operation
            architecture.write_text('{"approved": false}\n', encoding="utf-8")

        with self.assertRaisesRegex(
            MODULE.ScaffoldError, "architecture source changed"
        ):
            MODULE.apply_bundle(
                self.target,
                self.bundle,
                result["bundle_digest"],
                before_operation=mutate_architecture,
            )
        self.assertFalse((self.target / "src/file.py").exists())

    def test_architecture_source_cannot_overlap_operation_target(self) -> None:
        self.target.mkdir()
        readme = self.target / "README.md"
        readme.write_text("# Approved architecture\n", encoding="utf-8")
        with self.assertRaisesRegex(
            MODULE.ScaffoldError, "architecture source overlaps an operation target"
        ):
            self._finalize(
                self._draft(
                    [
                        self._operation(
                            "README.md",
                            "# Approved architecture\n\n"
                            "<!-- scaffold-project:begin:commands -->\n"
                            "Generated commands\n"
                            "<!-- scaffold-project:end:commands -->\n",
                            action="semantic_merge",
                            owner="scaffold-project",
                        ),
                        self._operation("src/sample.py", "value = 1\n"),
                    ],
                    architecture_sources=[
                        {
                            "path": str(readme),
                            "sha256": MODULE._sha256_path(readme),
                        }
                    ],
                )
            )

    def test_cli_converts_filesystem_errors_to_json(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(
                MODULE,
                "apply_bundle",
                side_effect=PermissionError("permission denied"),
            ),
            redirect_stdout(output),
        ):
            exit_code = MODULE.main(
                [
                    "apply",
                    "--target",
                    str(self.target),
                    "--bundle",
                    str(self.bundle),
                    "--expected-digest",
                    "0" * 64,
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"ok": False, "error": "filesystem error: permission denied"},
        )

    def test_unknown_fields_and_unsupported_owner_fail_closed(self) -> None:
        draft = self._draft([self._operation("src/file.py", "value = 1\n")])
        draft["legacy_mode"] = True
        with self.assertRaisesRegex(MODULE.ScaffoldError, "unknown fields"):
            self._finalize(draft)

        other_bundle = self.root / "other-bundle"
        other_bundle.mkdir()
        (other_bundle / "candidates").mkdir()
        self.bundle = other_bundle
        operation = self._operation("src/main.go", "package main\n", owner="go-project")
        with self.assertRaisesRegex(MODULE.ScaffoldError, "unsupported"):
            self._finalize(self._draft([operation]))

        legacy_bundle = self.root / "legacy-owner-bundle"
        legacy_bundle.mkdir()
        (legacy_bundle / "candidates").mkdir()
        self.bundle = legacy_bundle
        operation = self._operation(
            "Dockerfile",
            "FROM example.invalid/runtime:1.0\n",
            owner="container" + "-project",
        )
        with self.assertRaisesRegex(MODULE.ScaffoldError, "unsupported"):
            self._finalize(self._draft([operation]))

    def test_external_json_rejects_duplicate_object_keys(self) -> None:
        duplicate = self.bundle / "duplicate.json"
        duplicate.write_text(
            '{"schema_version": 2, "schema_version": 2}\n',
            encoding="utf-8",
        )
        os.chmod(duplicate, 0o600)

        with self.assertRaisesRegex(MODULE.ScaffoldError, "duplicate object key"):
            MODULE._load_json(duplicate, "duplicate fixture")

    def test_candidate_manifest_rejects_non_standard_numeric_literals(self) -> None:
        operation = self._operation("src/file.py", "value = 1\n")
        draft = self._draft([operation])
        candidate_set = draft["candidate_sets"][0]
        manifest_path = self.bundle / candidate_set["manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["inputs"] = {"non_finite": float("nan")}
        manifest_bytes = (
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode()
        manifest_path.write_bytes(manifest_bytes)
        os.chmod(manifest_path, 0o600)
        candidate_set["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()

        with self.assertRaisesRegex(
            MODULE.ScaffoldError,
            "non-standard numeric literal",
        ):
            self._finalize(draft)

    def test_unresolved_candidate_placeholder_is_rejected(self) -> None:
        operation = self._operation("src/main.py", 'name = "{{DISPLAY_NAME}}"\n')
        with self.assertRaisesRegex(MODULE.ScaffoldError, "unresolved placeholder"):
            self._finalize(self._draft([operation]))

    def test_required_units_reject_disguised_owner_and_empty_operations(self) -> None:
        unsupported_unit = [
            {
                "id": "go-app",
                "kind": "application-source",
                "path": "src",
                "language": "go",
                "framework": None,
                "owner": "scaffold-project",
                "invocation_scope": "coordinated-candidate",
            }
        ]
        capabilities = [
            {
                "id": "application",
                "kind": "application",
                "status": "required",
                "materialization_unit_ids": ["go-app"],
                "trigger": None,
            }
        ]
        operation = self._operation(
            "src/main.go",
            "package main\n",
            owner="scaffold-project",
            materialization_unit_id="go-app",
        )
        with self.assertRaisesRegex(MODULE.ScaffoldError, "unsupported"):
            self._finalize(
                self._draft(
                    [operation],
                    capabilities=capabilities,
                    materialization_units=unsupported_unit,
                    runtime_units=[],
                )
            )

        with self.assertRaisesRegex(MODULE.ScaffoldError, "no file operations"):
            self._finalize(self._draft([]))

        for index, (path, content) in enumerate(
            (
                ("src/main.go", "package main\n"),
                ("src/main.rb", 'puts "hello"\n'),
                ("src/main.php", "<?php echo 'hello';\n"),
                ("src/runner", "#!/usr/bin/env ruby\n"),
            )
        ):
            with self.subTest(path=path):
                foreign_bundle = self.root / f"foreign-bundle-{index}"
                foreign_bundle.mkdir(mode=0o700)
                (foreign_bundle / "candidates").mkdir(mode=0o700)
                self.bundle = foreign_bundle
                with self.assertRaisesRegex(
                    MODULE.ScaffoldError, "positive artifact contract"
                ):
                    self._finalize(self._draft([self._operation(path, content)]))

        nested_compose_bundle = self.root / "nested-compose-bundle"
        nested_compose_bundle.mkdir(mode=0o700)
        (nested_compose_bundle / "candidates").mkdir(mode=0o700)
        self.bundle = nested_compose_bundle
        with self.assertRaisesRegex(MODULE.ScaffoldError, "positive artifact contract"):
            self._finalize(
                self._draft(
                    [
                        self._operation(
                            "src/nested/compose.yaml",
                            "services: {}\n",
                            owner="container",
                            materialization_unit_id="application-source",
                        )
                    ]
                )
            )

        for index, alias in enumerate(
            ("compose.yml", "docker-compose.yaml", "docker-compose.yml")
        ):
            with self.subTest(alias=alias):
                alias_bundle = self.root / f"compose-alias-bundle-{index}"
                alias_bundle.mkdir(mode=0o700)
                (alias_bundle / "candidates").mkdir(mode=0o700)
                self.bundle = alias_bundle
                with self.assertRaisesRegex(
                    MODULE.ScaffoldError, "positive artifact contract"
                ):
                    self._finalize(
                        self._draft(
                            [
                                self._operation(
                                    alias,
                                    "services: {}\n",
                                    owner="container",
                                )
                            ]
                        )
                    )

    def test_positive_artifact_contract_supports_python_auxiliary_files(self) -> None:
        precommit = self._operation(
            ".pre-commit-config.yaml",
            "repos: []\n",
        )
        precommit["materialization_unit_id"] = None
        self._finalize(
            self._draft(
                [
                    precommit,
                    self._operation(
                        "src/sample/systemd/sample.service",
                        "[Service]\nExecStart=/usr/bin/sample\n",
                    ),
                    self._operation(
                        "src/sample/systemd/sample.timer",
                        "[Timer]\nOnCalendar=hourly\n",
                    ),
                ]
            )
        )
        self.assertEqual(
            {operation["path"] for operation in self._manifest()["operations"]},
            {
                ".pre-commit-config.yaml",
                "src/sample/systemd/sample.service",
                "src/sample/systemd/sample.timer",
            },
        )

    def test_positive_artifact_contract_supports_scoped_owner_files(self) -> None:
        capabilities = [
            {
                "id": "application",
                "kind": "application",
                "status": "required",
                "materialization_unit_ids": ["application-source"],
                "trigger": None,
            },
            {
                "id": "infrastructure",
                "kind": "infrastructure",
                "status": "required",
                "materialization_unit_ids": ["terraform"],
                "trigger": None,
            },
        ]
        units = [
            {
                "id": "application-source",
                "kind": "application-source",
                "path": "src",
                "language": "python",
                "framework": None,
                "owner": "python-project",
                "invocation_scope": "coordinated-candidate",
            },
            {
                "id": "terraform",
                "kind": "infrastructure",
                "path": "infra/terraform",
                "language": "hcl",
                "framework": "terraform",
                "owner": "terraform",
                "invocation_scope": "coordinated-candidate",
            },
        ]
        operations = [
            self._operation("src/Makefile", "all:\n\t@true\n"),
            self._operation("src/sample.py", "value = 1\n"),
            self._operation(
                "infra/terraform/.gitignore",
                ".terraform/\n",
                owner="terraform",
                materialization_unit_id="terraform",
            ),
            self._operation(
                "infra/terraform/Makefile",
                "all:\n\t@true\n",
                owner="terraform",
                materialization_unit_id="terraform",
            ),
            self._operation(
                "infra/terraform/envs/dev/terraform.tfvars.example",
                'region = "example"\n',
                owner="terraform",
                materialization_unit_id="terraform",
            ),
        ]
        self._finalize(
            self._draft(
                operations,
                capabilities=capabilities,
                materialization_units=units,
            )
        )
        self.assertEqual(
            {operation["path"] for operation in self._manifest()["operations"]},
            {operation["path"] for operation in operations},
        )

        unscoped_bundle = self.root / "unscoped-makefile-bundle"
        unscoped_bundle.mkdir(mode=0o700)
        (unscoped_bundle / "candidates").mkdir(mode=0o700)
        self.bundle = unscoped_bundle
        root_makefile = self._operation(
            "Makefile",
            "all:\n\t@true\n",
        )
        root_makefile["materialization_unit_id"] = None
        with self.assertRaisesRegex(MODULE.ScaffoldError, "positive artifact contract"):
            self._finalize(
                self._draft(
                    [
                        root_makefile,
                        self._operation("src/sample.py", "value = 1\n"),
                    ]
                )
            )

    def test_container_contract_accepts_exact_root_and_component_files(self) -> None:
        operations = [
            self._operation(
                name,
                "services: {}\n"
                if name.startswith("compose.")
                else 'group "default" {}\n',
                owner="container",
            )
            for name in (
                "compose.yaml",
                "compose.override.yaml",
                "compose.production.yaml",
                "compose.test.yaml",
                "docker-bake.hcl",
            )
        ]
        operations.extend(
            [
                self._operation(
                    "src/sample.py",
                    "value = 1\n",
                ),
                self._operation(
                    "src/Containerfile.runtime",
                    "FROM example.invalid/runtime:1.0\nUSER 10001\n",
                    owner="container",
                    materialization_unit_id="application-source",
                ),
                self._operation(
                    "src/compose.test.yaml",
                    "services: {}\n",
                    owner="container",
                    materialization_unit_id="application-source",
                ),
                self._operation(
                    "src/docker-bake.json",
                    "{}\n",
                    owner="container",
                    materialization_unit_id="application-source",
                ),
            ]
        )
        self._finalize(self._draft(operations))
        self.assertEqual(
            {operation["path"] for operation in self._manifest()["operations"]},
            {operation["path"] for operation in operations},
        )

    def test_container_contract_rejects_unapproved_compose_and_bake_locations(
        self,
    ) -> None:
        for index, path in enumerate(
            (
                "compose.prod.yaml",
                "docker-compose.yaml",
                "src/nested/compose.test.yaml",
                "src/nested/docker-bake.hcl",
            )
        ):
            with self.subTest(path=path):
                bundle = self.root / f"container-path-bundle-{index}"
                bundle.mkdir()
                (bundle / "candidates").mkdir()
                self.bundle = bundle
                with self.assertRaisesRegex(
                    MODULE.ScaffoldError, "positive artifact contract"
                ):
                    self._finalize(
                        self._draft(
                            [
                                self._operation(
                                    path,
                                    "services: {}\n",
                                    owner="container",
                                    materialization_unit_id=(
                                        "application-source"
                                        if path.startswith("src/")
                                        else None
                                    ),
                                )
                            ]
                        )
                    )

    def test_schema_structure_and_runtime_only_nfc_path_contract(self) -> None:
        schema = json.loads(
            (
                SCRIPT_PATH.parent.parent / "references/scaffold-plan.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], 2)
        self.assertIn("candidate_sets", schema["required"])
        operation_schema = schema["properties"]["operations"]["items"]
        self.assertIn("candidate_set_id", operation_schema["required"])
        self.assertNotIn("provenance", operation_schema["properties"])
        validation_schema = schema["properties"]["validations"]["items"]
        self.assertTrue(
            {
                "id",
                "owner",
                "materialization_unit_id",
                "candidate_set_id",
                "phase",
            }.issubset(validation_schema["required"])
        )
        candidate_schema = json.loads(
            (
                SCRIPT_PATH.parent.parent / "references/candidate-manifest.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIn("inputs", candidate_schema["required"])
        self.assertEqual(
            candidate_schema["properties"]["inputs"]["type"],
            "object",
        )
        path_contract = schema["$defs"]["relativePath"]
        self.assertEqual(
            set(schema["$defs"]["owner"]["enum"]),
            MODULE.SUPPORTED_OWNERS,
        )
        self.assertNotIn("container" + "-project", schema["$defs"]["owner"]["enum"])
        pattern = re.compile(path_contract["pattern"])
        vectors = {
            "src/main.py": (True, True),
            "apps/web/package.json": (True, True),
            "src/café.py": (True, True),
            "src//main.py": (False, False),
            "src/../main.py": (False, False),
            "/src/main.py": (False, False),
            "src/main.py/": (False, False),
            r"src\\main.py": (False, False),
            "src/main\n.py": (False, False),
            "src/main\t.py": (False, False),
            "src/main\x7f.py": (False, False),
            "src/main\x00.py": (False, False),
            "src/cafe\u0301.py": (True, False),
        }
        self.assertIn(
            "NFC is enforced by the Python validator",
            path_contract["$comment"],
        )
        for value, (schema_expected, runtime_expected) in vectors.items():
            with self.subTest(value=value):
                schema_accepts = pattern.fullmatch(value) is not None
                try:
                    MODULE._normalize_relative_path(value, "test")
                except MODULE.ScaffoldError:
                    runtime_accepts = False
                else:
                    runtime_accepts = True
                self.assertEqual(schema_accepts, schema_expected)
                self.assertEqual(runtime_accepts, runtime_expected)

    def test_schema_v2_rejects_v1_and_legacy_provenance(self) -> None:
        operation = self._operation("src/sample.py", "value = 1\n")
        draft = self._draft([operation])
        draft["schema_version"] = 1
        with self.assertRaisesRegex(MODULE.ScaffoldError, "unsupported schema_version"):
            self._finalize(draft)

        draft["schema_version"] = 2
        operation["provenance"] = "legacy-free-text"
        with self.assertRaisesRegex(MODULE.ScaffoldError, "unknown fields"):
            self._finalize(draft)

    def test_frontend_operations_must_be_contained_by_owned_root(self) -> None:
        capabilities = [
            {
                "id": "web",
                "kind": "web-ui",
                "status": "required",
                "materialization_unit_ids": ["web"],
                "trigger": None,
            }
        ]
        units = [
            {
                "id": "web",
                "kind": "application-source",
                "path": "apps/web",
                "language": "typescript",
                "framework": "react-vite",
                "owner": "frontend-project",
                "invocation_scope": "coordinated-candidate",
            }
        ]
        runtimes = [
            {
                "id": "web",
                "kind": "static-web",
                "capability_id": "web",
                "materialization_unit_id": "web",
                "runtime": "static-assets",
            }
        ]

        root_operation = self._operation(
            "package.json",
            '{"private":true}\n',
            owner="frontend-project",
        )
        with self.assertRaisesRegex(
            MODULE.ScaffoldError,
            "must bind to a frontend-project materialization unit",
        ):
            self._finalize(
                self._draft(
                    [root_operation],
                    capabilities=capabilities,
                    materialization_units=units,
                    runtime_units=runtimes,
                )
            )

        foreign_operation = self._operation(
            "src/App.tsx",
            "export function App() { return null; }\n",
            owner="frontend-project",
            materialization_unit_id="application-source",
        )
        with self.assertRaisesRegex(
            MODULE.ScaffoldError,
            "must bind to a frontend-project materialization unit",
        ):
            self._finalize(self._draft([foreign_operation]))

        accepted_operation = self._operation(
            "apps/web/package.json",
            '{"private":true}\n',
            owner="frontend-project",
            materialization_unit_id="web",
        )
        result = self._finalize(
            self._draft(
                [accepted_operation],
                capabilities=capabilities,
                materialization_units=units,
                runtime_units=runtimes,
            )
        )
        self.assertTrue(result["ok"])

    def test_candidate_sets_bind_operations_validations_and_manifest_digests(
        self,
    ) -> None:
        operation = self._operation("src/sample.py", "value = 1\n")
        draft = self._draft([operation])
        original_candidate_set_id = operation["candidate_set_id"]
        operation["candidate_set_id"] = "missing-set"
        with self.assertRaisesRegex(MODULE.ScaffoldError, "unknown candidate set"):
            self._finalize(draft)

        operation["candidate_set_id"] = original_candidate_set_id
        draft["validations"][0]["status"] = "pending"
        with self.assertRaisesRegex(
            MODULE.ScaffoldError, "candidate-phase validations must pass"
        ):
            self._finalize(draft)

        draft["validations"][0]["status"] = "passed"
        draft["candidate_sets"][0]["manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.ScaffoldError, "manifest digest mismatch"):
            self._finalize(draft)

        draft = self._draft([self._operation("src/other.py", "value = 2\n")])
        candidate_set = draft["candidate_sets"][0]
        manifest_path = self.bundle / candidate_set["manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["inputs"]["path"] = "src/tampered.py"
        manifest_bytes = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode()
        manifest_path.write_bytes(manifest_bytes)
        os.chmod(manifest_path, 0o600)
        candidate_set["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        with self.assertRaisesRegex(MODULE.ScaffoldError, "input digest mismatch"):
            self._finalize(draft)

    def test_frontend_candidate_profile_must_match_materialization_profile(
        self,
    ) -> None:
        capabilities = [
            {
                "id": "web",
                "kind": "web-ui",
                "status": "required",
                "materialization_unit_ids": ["web"],
                "trigger": None,
            }
        ]
        units = [
            {
                "id": "web",
                "kind": "application-source",
                "path": "apps/web",
                "language": "typescript",
                "framework": "react-vite",
                "owner": "frontend-project",
                "invocation_scope": "coordinated-candidate",
            }
        ]
        operation = self._operation(
            "apps/web/package.json",
            '{"private":true}\n',
            owner="frontend-project",
            materialization_unit_id="web",
        )
        draft = self._draft(
            [operation],
            capabilities=capabilities,
            materialization_units=units,
            runtime_units=[
                {
                    "id": "web",
                    "kind": "static-web",
                    "capability_id": "web",
                    "materialization_unit_id": "web",
                    "runtime": "browser",
                }
            ],
        )
        candidate_set = draft["candidate_sets"][0]
        candidate_set["profile"] = "not-react-vite"
        manifest_path = self.bundle / candidate_set["manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["profile"] = "not-react-vite"
        manifest_bytes = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode()
        manifest_path.write_bytes(manifest_bytes)
        os.chmod(manifest_path, 0o600)
        candidate_set["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()

        with self.assertRaisesRegex(
            MODULE.ScaffoldError,
            "frontend-project candidate sets must use react-vite",
        ):
            self._finalize(draft)

    def test_post_apply_validation_may_remain_pending(self) -> None:
        operation = self._operation("src/sample.py", "value = 1\n")
        draft = self._draft([operation])
        candidate_set = draft["candidate_sets"][0]
        pending = {
            "id": f"{candidate_set['id']}:tests",
            "owner": operation["owner"],
            "materialization_unit_id": operation["materialization_unit_id"],
            "candidate_set_id": candidate_set["id"],
            "phase": "post-apply",
            "command": "python -m pytest",
            "network_required": False,
            "status": "pending",
        }
        draft["validations"].append(pending)
        candidate_set["validation_ids"].append(pending["id"])
        manifest_path = self.bundle / candidate_set["manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["validations"].append(pending)
        manifest_bytes = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode()
        manifest_path.write_bytes(manifest_bytes)
        os.chmod(manifest_path, 0o600)
        candidate_set["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()

        result = self._finalize(draft)
        self.assertTrue(result["ok"])

    def test_full_stack_fixture_preserves_distinct_content_owners(self) -> None:
        capabilities = [
            {
                "id": "api",
                "kind": "api",
                "status": "required",
                "materialization_unit_ids": ["backend"],
                "trigger": None,
            },
            {
                "id": "worker",
                "kind": "worker",
                "status": "required",
                "materialization_unit_ids": ["backend"],
                "trigger": None,
            },
            {
                "id": "web",
                "kind": "web-ui",
                "status": "required",
                "materialization_unit_ids": ["web"],
                "trigger": None,
            },
            {
                "id": "infrastructure",
                "kind": "infrastructure",
                "status": "required",
                "materialization_unit_ids": ["terraform"],
                "trigger": None,
            },
            {
                "id": "deployment",
                "kind": "deployment-package",
                "status": "required",
                "materialization_unit_ids": ["helm"],
                "trigger": None,
            },
        ]
        units = [
            {
                "id": "backend",
                "kind": "application-source",
                "path": "apps/backend",
                "language": "python",
                "framework": "fastapi",
                "owner": "python-project",
                "invocation_scope": "coordinated-candidate",
            },
            {
                "id": "web",
                "kind": "application-source",
                "path": "apps/web",
                "language": "typescript",
                "framework": "react-vite",
                "owner": "frontend-project",
                "invocation_scope": "coordinated-candidate",
            },
            {
                "id": "terraform",
                "kind": "infrastructure",
                "path": "infra/terraform",
                "language": "hcl",
                "framework": "terraform",
                "owner": "terraform",
                "invocation_scope": "coordinated-candidate",
            },
            {
                "id": "helm",
                "kind": "deployment-package",
                "path": "deploy/helm",
                "language": "yaml",
                "framework": "helm",
                "owner": "helmchart",
                "invocation_scope": "coordinated-candidate",
            },
        ]
        runtimes = [
            {
                "id": "api",
                "kind": "process",
                "capability_id": "api",
                "materialization_unit_id": "backend",
                "runtime": "container",
            },
            {
                "id": "worker",
                "kind": "process",
                "capability_id": "worker",
                "materialization_unit_id": "backend",
                "runtime": "container",
            },
            {
                "id": "web",
                "kind": "static-web",
                "capability_id": "web",
                "materialization_unit_id": "web",
                "runtime": "container",
            },
        ]
        operations = [
            self._operation(".gitignore", ".venv/\nnode_modules/\n", owner="gitignore"),
            self._operation(
                ".github/workflows/ci.yml",
                "name: sample-ci\n",
                owner="github-workflows",
            ),
            self._operation("README.md", "# Sample\n", owner="scaffold-project"),
            self._operation(
                "apps/backend/Dockerfile",
                "FROM example/python@sha256:digest\n",
                owner="container",
                materialization_unit_id="backend",
            ),
            self._operation(
                "apps/backend/pyproject.toml",
                '[project]\nname = "sample"\n',
                owner="python-project",
                materialization_unit_id="backend",
            ),
            self._operation(
                "apps/web/Dockerfile",
                "FROM example/node@sha256:digest\n",
                owner="container",
                materialization_unit_id="web",
            ),
            self._operation(
                "apps/web/package.json",
                '{"name":"@example/web","private":true}\n',
                owner="frontend-project",
                materialization_unit_id="web",
            ),
            self._operation(
                "compose.yaml",
                "services:\n  backend:\n    build: apps/backend\n",
                owner="container",
            ),
            self._operation(
                "deploy/helm/Chart.yaml",
                "apiVersion: v2\nname: sample\nversion: 0.1.0\n",
                owner="helmchart",
                materialization_unit_id="helm",
            ),
            self._operation(
                "infra/terraform/main.tf",
                'terraform { required_version = ">= 1.10.0, < 2.0.0" }\n',
                owner="terraform",
                materialization_unit_id="terraform",
            ),
        ]
        result = self._finalize(
            self._draft(
                operations,
                capabilities=capabilities,
                materialization_units=units,
                runtime_units=runtimes,
            )
        )
        MODULE.apply_bundle(self.target, self.bundle, result["bundle_digest"])

        manifest = self._manifest()
        owners = {item["path"]: item["owner"] for item in manifest["operations"]}
        self.assertEqual(owners["apps/backend/pyproject.toml"], "python-project")
        self.assertEqual(owners["apps/backend/Dockerfile"], "container")
        self.assertEqual(owners["apps/web/package.json"], "frontend-project")
        self.assertEqual(len(owners), 10)
        self.assertTrue((self.target / "deploy/helm/Chart.yaml").is_file())
        self.assertTrue((self.target / "infra/terraform/main.tf").is_file())


if __name__ == "__main__":
    unittest.main()
