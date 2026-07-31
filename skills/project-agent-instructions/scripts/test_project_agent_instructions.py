#!/usr/bin/env python3
"""Disposable tests for project_agent_instructions.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from typing import Optional
import unittest
from unittest import mock

import project_agent_instructions as cli_module
from project_agent_instructions_lib import contracts
from project_agent_instructions_lib import discovery
from project_agent_instructions_lib import private_state
from project_agent_instructions_lib import target_io
from project_agent_instructions_lib import workflow

SCRIPT = Path(__file__).resolve().with_name("project_agent_instructions.py")


class ProjectAgentInstructionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="project-agent-instructions-test-"
        )
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.private = self.root / "private"
        self.codex_home = self.root / "codex-home"
        self.repo.mkdir()
        self.private.mkdir()
        self.codex_home.mkdir()
        os.chmod(self.private, 0o700)
        private_state._ensure_private_root(self.private, self.repo)
        subprocess.run(
            ["git", "init", "-q", "-b", "feature/test"],
            cwd=self.repo,
            check=True,
        )
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "requirements.md").write_text(
            "---\nschema: agentic-sdlc.requirements.v1\n---\n# Requirements\n",
            encoding="utf-8",
        )
        (self.repo / "docs" / "design.md").write_text(
            "---\nschema: agentic-sdlc.design.v1\n---\n# Design\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def inspect(
        self,
        *,
        project: Optional[Path] = None,
        owner: str = "agentic-sdlc",
    ) -> dict[str, object]:
        return discovery._manifest(
            project or self.repo,
            owner,
            "docs/requirements.md",
            "docs/design.md",
            self.codex_home,
        )

    def write_json(self, name: str, value: object) -> Path:
        path = self.private / name
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(path, 0o600)
        return path

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def evidence(self, project: Optional[Path] = None) -> list[dict[str, str]]:
        selected = project or self.repo
        path = selected / "docs" / "design.md"
        return [
            {
                "path": "docs/design.md",
                "sha256": contracts._sha256_bytes(path.read_bytes()),
            }
        ]

    def body(self, *, scope: str = ".", purpose: Optional[str] = None) -> str:
        lines = [
            "# Example Project Agent Instructions",
            "",
            "## Scope",
            "",
            "These instructions apply to this directory and all descendants.",
            "",
            f"Project root: `{scope}`",
            "",
        ]
        if purpose is not None:
            lines.extend(
                [
                    "## Project purpose",
                    "",
                    f"- {purpose}",
                    "",
                ]
            )
        lines.extend(
            [
                "## Read before changing",
                "",
                "- Requirements: `docs/requirements.md`",
                "- Design: `docs/design.md`",
                "",
            ]
        )
        return "\n".join(lines)

    def decision(
        self,
        manifest: dict[str, object],
        disposition: str,
        *,
        body: Optional[str] = None,
        project: Optional[Path] = None,
    ) -> dict[str, object]:
        return {
            "schema": contracts.DECISION_SCHEMA,
            "manifest_sha256": manifest["manifest_sha256"],
            "disposition": disposition,
            "rationale": "Current project evidence supports this result.",
            "evidence": self.evidence(project),
            "body": body,
        }

    def apply(
        self,
        manifest: dict[str, object],
        decision: dict[str, object],
    ) -> dict[str, object]:
        manifest_path = self.write_json("manifest.json", manifest)
        decision_path = self.write_json("decision.json", decision)
        return workflow.apply_decision(
            manifest_path,
            decision_path,
            self.private / "state.json",
            self.private,
        )

    def test_missing_target_can_be_created_and_verified(self) -> None:
        manifest = self.inspect()
        self.assertEqual(manifest["target"]["file_status"], "missing")
        state = self.apply(
            manifest,
            self.decision(manifest, "needed", body=self.body()),
        )
        target = self.repo / "AGENTS.md"
        self.assertEqual(state["outcome"], "created")
        self.assertTrue(
            target.read_text(encoding="utf-8").startswith(
                "<!-- project-agent-instructions:generated-v1 "
            )
        )
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        self.assertEqual(
            stat.S_IMODE((self.private / "state.json").stat().st_mode),
            0o600,
        )
        verified = workflow.verify_state(
            self.private / "state.json",
            self.private,
        )
        self.assertEqual(verified["status"], "ok")
        self.assertEqual(verified["outcome"], "created")

    def test_exclusive_create_cleans_up_io_failure(self) -> None:
        target = self.repo / "AGENTS.md"
        with mock.patch.object(
            target_io.os,
            "fchmod",
            side_effect=PermissionError("simulated"),
        ):
            with self.assertRaises(contracts.ProjectInstructionsError) as context:
                target_io._exclusive_create(target, b"content\n")
        self.assertEqual(context.exception.code, "UNSAFE_TARGET")
        self.assertFalse(target.exists())

    def test_create_race_cannot_record_or_verify_a_human_file(self) -> None:
        manifest = self.inspect()
        target = self.repo / "AGENTS.md"
        real_create = target_io._exclusive_create

        def replace_after_create(path: Path, content: bytes) -> None:
            real_create(path, content)
            path.write_text(
                "# Human Instructions\n\n- Concurrent replacement.\n",
                encoding="utf-8",
            )

        with mock.patch.object(
            workflow,
            "_exclusive_create",
            side_effect=replace_after_create,
        ):
            with self.assertRaises(contracts.ProjectInstructionsError) as context:
                self.apply(
                    manifest,
                    self.decision(manifest, "needed", body=self.body()),
                )
        self.assertEqual(context.exception.code, "CONCURRENT_MODIFICATION")
        self.assertTrue(target.read_text(encoding="utf-8").startswith("# Human"))
        self.assertFalse((self.private / "state.json").exists())

    def test_same_generated_body_preserves_bytes_mode_and_mtime(self) -> None:
        manifest = self.inspect()
        body = self.body()
        self.apply(manifest, self.decision(manifest, "needed", body=body))
        target = self.repo / "AGENTS.md"
        before = (
            target.read_bytes(),
            target.stat().st_mtime_ns,
            stat.S_IMODE(target.stat().st_mode),
        )
        time.sleep(0.01)
        current = self.inspect()
        state = self.apply(
            current,
            self.decision(current, "needed", body=body),
        )
        after = (
            target.read_bytes(),
            target.stat().st_mtime_ns,
            stat.S_IMODE(target.stat().st_mode),
        )
        self.assertEqual(state["outcome"], "existing-sufficient")
        self.assertEqual(after, before)

    def test_unchanged_generated_file_can_be_refreshed(self) -> None:
        manifest = self.inspect()
        self.apply(
            manifest,
            self.decision(manifest, "needed", body=self.body()),
        )
        current = self.inspect()
        state = self.apply(
            current,
            self.decision(
                current,
                "needed",
                body=self.body(purpose="Owns the example service."),
            ),
        )
        self.assertEqual(state["outcome"], "refreshed")
        self.assertIn(
            "Owns the example service.",
            (self.repo / "AGENTS.md").read_text(encoding="utf-8"),
        )

    def test_refresh_install_preserves_competing_writer(self) -> None:
        manifest = self.inspect()
        self.apply(
            manifest,
            self.decision(manifest, "needed", body=self.body()),
        )
        target = self.repo / "AGENTS.md"
        generated_before = target.read_bytes()
        current = self.inspect()
        decision = self.decision(
            current,
            "needed",
            body=self.body(purpose="Changed purpose."),
        )
        manifest_path = self.write_json("manifest.json", current)
        decision_path = self.write_json("decision.json", decision)
        real_link = os.link

        def competing_link(source: Path, destination: Path) -> None:
            if Path(destination) == target and Path(source).name.startswith(
                ".AGENTS.md."
            ):
                target.write_text(
                    "# Human Instructions\n\n- Preserve the competing write.\n",
                    encoding="utf-8",
                )
                raise FileExistsError
            real_link(source, destination)

        with mock.patch.object(target_io.os, "link", side_effect=competing_link):
            with self.assertRaises(contracts.ProjectInstructionsError) as context:
                workflow.apply_decision(
                    manifest_path,
                    decision_path,
                    self.private / "state.json",
                    self.private,
                )
        self.assertEqual(context.exception.code, "CONCURRENT_MODIFICATION")
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "# Human Instructions\n\n- Preserve the competing write.\n",
        )
        backup = self.repo / ".AGENTS.md.project-agent-instructions.backup"
        self.assertEqual(backup.read_bytes(), generated_before)
        self.assertFalse(
            (self.repo / ".AGENTS.md.project-agent-instructions.lock").exists()
        )

    def test_refresh_install_failure_restores_prior_file(self) -> None:
        manifest = self.inspect()
        self.apply(
            manifest,
            self.decision(manifest, "needed", body=self.body()),
        )
        target = self.repo / "AGENTS.md"
        original = target.read_bytes()
        current = self.inspect()
        backup = self.repo / ".AGENTS.md.project-agent-instructions.backup"
        real_link = os.link

        def fail_install_once(source: Path, destination: Path) -> None:
            if Path(destination) == target and Path(source) != backup:
                raise PermissionError("simulated install failure")
            real_link(source, destination)

        with mock.patch.object(target_io.os, "link", side_effect=fail_install_once):
            with self.assertRaises(contracts.ProjectInstructionsError) as context:
                self.apply(
                    current,
                    self.decision(
                        current,
                        "needed",
                        body=self.body(purpose="Changed purpose."),
                    ),
                )
        self.assertEqual(context.exception.code, "CONCURRENT_MODIFICATION")
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(backup.exists())

    def test_refresh_race_cannot_record_or_verify_a_human_file(self) -> None:
        manifest = self.inspect()
        self.apply(
            manifest,
            self.decision(manifest, "needed", body=self.body()),
        )
        state_path = self.private / "state.json"
        prior_state = state_path.read_bytes()
        target = self.repo / "AGENTS.md"
        current = self.inspect()
        real_replace = target_io._guarded_replace

        def replace_after_refresh(
            path: Path, expected_sha256: str, content: bytes
        ) -> None:
            real_replace(path, expected_sha256, content)
            path.write_text(
                "# Human Instructions\n\n- Concurrent replacement.\n",
                encoding="utf-8",
            )

        with mock.patch.object(
            workflow,
            "_guarded_replace",
            side_effect=replace_after_refresh,
        ):
            with self.assertRaises(contracts.ProjectInstructionsError) as context:
                self.apply(
                    current,
                    self.decision(
                        current,
                        "needed",
                        body=self.body(purpose="Changed purpose."),
                    ),
                )
        self.assertEqual(context.exception.code, "CONCURRENT_MODIFICATION")
        self.assertTrue(target.read_text(encoding="utf-8").startswith("# Human"))
        self.assertEqual(state_path.read_bytes(), prior_state)

    def test_human_edit_transfers_ownership_and_blocks_refresh(self) -> None:
        manifest = self.inspect()
        self.apply(
            manifest,
            self.decision(manifest, "needed", body=self.body()),
        )
        target = self.repo / "AGENTS.md"
        target.write_text(
            target.read_text(encoding="utf-8") + "\nHuman note.\n",
            encoding="utf-8",
        )
        current = self.inspect()
        self.assertEqual(current["target"]["file_status"], "human-edited")
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            self.apply(
                current,
                self.decision(
                    current,
                    "needed",
                    body=self.body(purpose="Changed purpose."),
                ),
            )
        self.assertEqual(context.exception.code, "STALE_GENERATED_FILE")

    def test_human_owned_file_is_preserved_when_sufficient(self) -> None:
        target = self.repo / "AGENTS.md"
        target.write_text("# Human Instructions\n\n- Preserve me.\n", encoding="utf-8")
        before = target.read_bytes()
        manifest = self.inspect()
        state = self.apply(
            manifest,
            self.decision(manifest, "existing-sufficient"),
        )
        self.assertEqual(state["outcome"], "existing-sufficient")
        self.assertEqual(target.read_bytes(), before)

    def test_generated_file_cannot_claim_existing_sufficient(self) -> None:
        manifest = self.inspect()
        self.apply(
            manifest,
            self.decision(manifest, "needed", body=self.body()),
        )
        current = self.inspect()
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            self.apply(
                current,
                self.decision(current, "existing-sufficient"),
            )
        self.assertEqual(context.exception.code, "EXISTING_INSTRUCTIONS_GAP")

    def test_not_needed_writes_private_state_only(self) -> None:
        manifest = self.inspect()
        state = self.apply(
            manifest,
            self.decision(manifest, "not-needed"),
        )
        self.assertEqual(state["outcome"], "not-needed")
        self.assertFalse((self.repo / "AGENTS.md").exists())
        self.assertTrue((self.private / "state.json").is_file())

    def test_no_write_race_cannot_record_a_changed_target(self) -> None:
        manifest = self.inspect()
        target = self.repo / "AGENTS.md"
        real_fresh_manifest = workflow._fresh_manifest
        calls = 0

        def mutate_before_final(recorded: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls == 2:
                target.write_text(
                    "# Human Instructions\n\n- Concurrent creation.\n",
                    encoding="utf-8",
                )
            return real_fresh_manifest(recorded)

        with mock.patch.object(
            workflow,
            "_fresh_manifest",
            side_effect=mutate_before_final,
        ):
            with self.assertRaises(contracts.ProjectInstructionsError) as context:
                self.apply(
                    manifest,
                    self.decision(manifest, "not-needed"),
                )
        self.assertEqual(context.exception.code, "CONCURRENT_MODIFICATION")
        self.assertFalse((self.private / "state.json").exists())

    def test_existing_private_state_with_loose_mode_is_rejected(self) -> None:
        manifest = self.inspect()
        state_path = self.private / "state.json"
        self.apply(
            manifest,
            self.decision(manifest, "not-needed"),
        )
        os.chmod(state_path, 0o644)
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            self.apply(
                manifest,
                self.decision(manifest, "not-needed"),
            )
        self.assertEqual(context.exception.code, "UNSAFE_TARGET")

    def test_verify_rejects_loose_private_evidence_modes(self) -> None:
        manifest = self.inspect()
        self.apply(
            manifest,
            self.decision(manifest, "not-needed"),
        )
        for name in ("manifest.json", "decision.json", "state.json"):
            with self.subTest(name=name):
                path = self.private / name
                os.chmod(path, 0o644)
                try:
                    with self.assertRaises(
                        contracts.ProjectInstructionsError
                    ) as context:
                        workflow.verify_state(
                            self.private / "state.json",
                            self.private,
                        )
                    self.assertEqual(context.exception.code, "UNSAFE_TARGET")
                finally:
                    os.chmod(path, 0o600)
        self.assertEqual(
            workflow.verify_state(
                self.private / "state.json",
                self.private,
            )["status"],
            "ok",
        )

    def test_state_provenance_fields_cannot_be_tampered(self) -> None:
        manifest = self.inspect()
        self.apply(
            manifest,
            self.decision(manifest, "needed", body=self.body()),
        )
        state_path = self.private / "state.json"
        original = json.loads(state_path.read_text(encoding="utf-8"))
        tampered_values = {
            "outcome": "refreshed",
            "decision_sha256": "0" * 64,
            "target_path": str(self.repo / "OTHER.md"),
            "target_sha256": "0" * 64,
            "active_instruction_path": None,
        }
        for field, value in tampered_values.items():
            with self.subTest(field=field):
                tampered = dict(original)
                tampered[field] = value
                state_path.write_text(
                    json.dumps(tampered, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.chmod(state_path, 0o600)
                with self.assertRaises(contracts.ProjectInstructionsError) as context:
                    workflow.verify_state(state_path, self.private)
                self.assertEqual(
                    context.exception.code,
                    "CONCURRENT_MODIFICATION",
                )
        state_path.write_text(
            json.dumps(original, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(state_path, 0o600)

    def test_private_outputs_cannot_escape_or_replace_unowned_files(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text('{"schema": "other"}\n', encoding="utf-8")
        os.chmod(outside, 0o600)
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            private_state._write_private_json(
                outside,
                {"schema": contracts.STATE_SCHEMA},
                self.repo,
                self.private,
            )
        self.assertEqual(context.exception.code, "UNSAFE_TARGET")
        self.assertEqual(outside.read_text(encoding="utf-8"), '{"schema": "other"}\n')

        unowned = self.private / "unowned.json"
        unowned.write_text('{"schema": "other"}\n', encoding="utf-8")
        os.chmod(unowned, 0o600)
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            private_state._write_private_json(
                unowned,
                {"schema": contracts.STATE_SCHEMA},
                self.repo,
                self.private,
            )
        self.assertEqual(context.exception.code, "UNSAFE_TARGET")
        self.assertEqual(unowned.read_text(encoding="utf-8"), '{"schema": "other"}\n')

    def test_malformed_manifest_returns_structured_cli_blocker(self) -> None:
        manifest_path = self.write_json(
            "manifest.json",
            {"schema": contracts.MANIFEST_SCHEMA},
        )
        decision_path = self.write_json("decision.json", {})
        completed = self.run_cli(
            "apply",
            "--private-root",
            str(self.private),
            "--manifest",
            str(manifest_path),
            "--decision",
            str(decision_path),
            "--state",
            str(self.private / "state.json"),
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)["code"], "UNSAFE_TARGET")
        self.assertNotIn("Traceback", completed.stderr)

    def test_cli_has_no_helper_reexports(self) -> None:
        completed = self.run_cli("--help")
        self.assertEqual(completed.returncode, 0)
        self.assertIn("{inspect,apply,verify}", completed.stdout)
        self.assertEqual(completed.stderr, "")
        for name in (
            "_manifest",
            "_ensure_private_root",
            "_exclusive_create",
            "_guarded_replace",
            "_write_private_json",
            "apply_decision",
            "verify_state",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(cli_module, name))

    def test_cli_round_trip_preserves_contract(self) -> None:
        manifest_path = self.private / "manifest.json"
        inspect_result = self.run_cli(
            "inspect",
            "--project-root",
            str(self.repo),
            "--spec-owner",
            "agentic-sdlc",
            "--codex-home",
            str(self.codex_home),
            "--private-root",
            str(self.private),
            "--output",
            str(manifest_path),
        )
        self.assertEqual(inspect_result.returncode, 0)
        self.assertEqual(json.loads(inspect_result.stdout)["status"], "ok")
        self.assertEqual(inspect_result.stderr, "")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        decision_path = self.write_json(
            "decision.json",
            self.decision(manifest, "needed", body=self.body()),
        )
        state_path = self.private / "state.json"
        apply_result = self.run_cli(
            "apply",
            "--private-root",
            str(self.private),
            "--manifest",
            str(manifest_path),
            "--decision",
            str(decision_path),
            "--state",
            str(state_path),
        )
        self.assertEqual(apply_result.returncode, 0)
        self.assertEqual(json.loads(apply_result.stdout)["outcome"], "created")
        self.assertEqual(apply_result.stderr, "")
        verify_result = self.run_cli(
            "verify",
            "--private-root",
            str(self.private),
            "--state",
            str(state_path),
        )
        self.assertEqual(verify_result.returncode, 0)
        self.assertEqual(json.loads(verify_result.stdout)["status"], "ok")
        self.assertEqual(verify_result.stderr, "")

    def test_private_root_rejects_symlinks_and_git_worktrees(self) -> None:
        symlink = self.root / "private-link"
        symlink.symlink_to(self.private, target_is_directory=True)
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            private_state._ensure_private_root(symlink, self.repo)
        self.assertEqual(context.exception.code, "UNSAFE_TARGET")

        other_repo = self.root / "other-repo"
        other_repo.mkdir()
        os.chmod(other_repo, 0o700)
        subprocess.run(["git", "init", "-q"], cwd=other_repo, check=True)
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            private_state._ensure_private_root(other_repo, self.repo)
        self.assertEqual(context.exception.code, "UNSAFE_TARGET")

    def test_missing_toml_parser_is_a_structured_prerequisite(self) -> None:
        with mock.patch.object(
            discovery.importlib,
            "import_module",
            side_effect=ModuleNotFoundError,
        ):
            with self.assertRaises(contracts.ProjectInstructionsError) as context:
                discovery._toml_parser()
        self.assertEqual(context.exception.code, "PREREQUISITE_MISSING")

    def test_codex_config_is_parsed_or_reports_parser_prerequisite(self) -> None:
        (self.codex_home / "config.toml").write_text(
            'project_doc_fallback_filenames = ["PROJECT_AGENTS.md"]\n'
            "project_doc_max_bytes = 4096\n",
            encoding="utf-8",
        )
        try:
            fallbacks, maximum = discovery._codex_settings(self.codex_home)
        except contracts.ProjectInstructionsError as error:
            self.assertEqual(error.code, "PREREQUISITE_MISSING")
        else:
            self.assertEqual(fallbacks, ["PROJECT_AGENTS.md"])
            self.assertEqual(maximum, 4096)

    def test_body_limit_uses_remaining_project_capacity(self) -> None:
        inherited_size = 25 * 1024
        prefix = b"# Repository instructions\n"
        (self.repo / "AGENTS.md").write_bytes(
            prefix + b"x" * (inherited_size - len(prefix))
        )
        nested = self.repo / "services" / "example"
        (nested / "docs").mkdir(parents=True)
        (nested / "docs" / "requirements.md").write_text(
            "---\nschema: agentic-sdlc.requirements.v1\n---\n",
            encoding="utf-8",
        )
        (nested / "docs" / "design.md").write_text(
            "---\nschema: agentic-sdlc.design.v1\n---\n",
            encoding="utf-8",
        )
        manifest = self.inspect(project=nested)
        marker_size = len(target_io._generated_content(b""))
        expected = 32 * 1024 - inherited_size - marker_size
        self.assertEqual(manifest["generated_body_max_bytes"], expected)
        self.assertLess(expected, 7 * 1024)
        self.assertEqual(
            inherited_size + marker_size + expected,
            manifest["configured_project_doc_max_bytes"],
        )

    def test_exhausted_capacity_still_allows_not_needed(self) -> None:
        (self.codex_home / "config.toml").write_text(
            "project_doc_max_bytes = 100\n",
            encoding="utf-8",
        )
        prefix = b"# Repository instructions\n"
        (self.repo / "AGENTS.md").write_bytes(prefix + b"x" * (100 - len(prefix)))
        nested = self.repo / "services" / "example"
        (nested / "docs").mkdir(parents=True)
        (nested / "docs" / "requirements.md").write_text(
            "---\nschema: agentic-sdlc.requirements.v1\n---\n",
            encoding="utf-8",
        )
        (nested / "docs" / "design.md").write_text(
            "---\nschema: agentic-sdlc.design.v1\n---\n",
            encoding="utf-8",
        )
        manifest = self.inspect(project=nested)
        self.assertEqual(manifest["generated_body_max_bytes"], 0)
        state = self.apply(
            manifest,
            self.decision(manifest, "not-needed", project=nested),
        )
        self.assertEqual(state["outcome"], "not-needed")

    def test_global_instructions_do_not_consume_project_budget(self) -> None:
        prefix = b"# Global instructions\n"
        (self.codex_home / "AGENTS.md").write_bytes(
            prefix + b"x" * (31 * 1024 - len(prefix))
        )
        manifest = self.inspect()
        self.assertEqual(manifest["generated_body_max_bytes"], 7 * 1024)
        self.assertEqual(
            manifest["generated_body_max_bytes"],
            contracts.MAX_BODY_BYTES,
        )

    def test_ignored_decision_evidence_is_rejected(self) -> None:
        evidence_path = self.repo / "local-evidence.txt"
        evidence_path.write_text("local-only evidence\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text(
            "local-evidence.txt\n",
            encoding="utf-8",
        )
        manifest = self.inspect()
        decision = self.decision(manifest, "not-needed")
        decision["evidence"] = [
            {
                "path": "local-evidence.txt",
                "sha256": contracts._sha256_bytes(evidence_path.read_bytes()),
            }
        ]
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            self.apply(manifest, decision)
        self.assertEqual(context.exception.code, "UNSAFE_TARGET")
        self.assertIn("ignored", context.exception.message)

    def test_tracked_evidence_matching_ignore_pattern_is_allowed(self) -> None:
        evidence_path = self.repo / "tracked-evidence.txt"
        evidence_path.write_text("durable evidence\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text(
            "tracked-evidence.txt\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "add", "-f", "tracked-evidence.txt"],
            cwd=self.repo,
            check=True,
        )
        manifest = self.inspect()
        decision = self.decision(manifest, "not-needed")
        decision["evidence"] = [
            {
                "path": "tracked-evidence.txt",
                "sha256": contracts._sha256_bytes(evidence_path.read_bytes()),
            }
        ]
        state = self.apply(manifest, decision)
        self.assertEqual(state["outcome"], "not-needed")

    def test_override_is_active_and_blocks_generated_file(self) -> None:
        override = self.repo / "AGENTS.override.md"
        override.write_text("# Human Override\n\n- Preserve me.\n", encoding="utf-8")
        manifest = self.inspect()
        self.assertEqual(
            manifest["active_project_instruction"]["kind"],
            "project-override",
        )
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            self.apply(
                manifest,
                self.decision(manifest, "needed", body=self.body()),
            )
        self.assertEqual(context.exception.code, "EXISTING_INSTRUCTIONS_GAP")
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_owner_mismatch_fails_closed(self) -> None:
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            self.inspect(owner="task-implementer")
        self.assertEqual(context.exception.code, "SPEC_OWNER_CONFLICT")

    def test_foreign_owner_markers_are_rejected(self) -> None:
        cases = (
            (
                "task-implementer",
                (
                    "<!-- task-implementer:requirements:start "
                    "schema=task-implementer/requirements-v1 -->\n"
                    f"{contracts.AGENTIC_DESIGN_SCHEMA}\n"
                ),
                (
                    "<!-- task-implementer:design:start "
                    "schema=task-implementer/design-v1 -->\n"
                    f"{contracts.AGENTIC_REQUIREMENTS_SCHEMA}\n"
                ),
            ),
            (
                "agentic-sdlc",
                (
                    "---\nschema: agentic-sdlc.requirements.v1\n---\n"
                    f"{contracts.TASK_DESIGN_MARKER}\n"
                ),
                (
                    "---\nschema: agentic-sdlc.design.v1\n---\n"
                    f"{contracts.TASK_REQUIREMENTS_MARKER}\n"
                ),
            ),
        )
        for owner, requirements, design in cases:
            with self.subTest(owner=owner):
                (self.repo / "docs" / "requirements.md").write_text(
                    requirements,
                    encoding="utf-8",
                )
                (self.repo / "docs" / "design.md").write_text(
                    design,
                    encoding="utf-8",
                )
                with self.assertRaises(contracts.ProjectInstructionsError) as context:
                    self.inspect(owner=owner)
                self.assertEqual(
                    context.exception.code,
                    "SPEC_OWNER_CONFLICT",
                )

    def test_task_implementer_owned_specs_are_accepted(self) -> None:
        (self.repo / "docs" / "requirements.md").write_text(
            "<!-- task-implementer:requirements:start "
            "schema=task-implementer/requirements-v1 -->\n"
            "# Requirements\n"
            "<!-- task-implementer:requirements:end -->\n",
            encoding="utf-8",
        )
        (self.repo / "docs" / "design.md").write_text(
            "<!-- task-implementer:design:start "
            "schema=task-implementer/design-v1 -->\n"
            "# Design\n"
            "<!-- task-implementer:design:end -->\n",
            encoding="utf-8",
        )
        manifest = self.inspect(owner="task-implementer")
        self.assertEqual(manifest["spec_owner"], "task-implementer")

    def test_empty_human_file_is_not_sufficient(self) -> None:
        (self.repo / "AGENTS.md").write_text("", encoding="utf-8")
        manifest = self.inspect()
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            self.apply(
                manifest,
                self.decision(manifest, "existing-sufficient"),
            )
        self.assertEqual(context.exception.code, "EXISTING_INSTRUCTIONS_GAP")

    def test_symlink_target_is_rejected_without_touching_referent(self) -> None:
        referent = self.root / "outside.md"
        referent.write_text("outside\n", encoding="utf-8")
        (self.repo / "AGENTS.md").symlink_to(referent)
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            self.inspect()
        self.assertEqual(context.exception.code, "UNSAFE_TARGET")
        self.assertEqual(referent.read_text(encoding="utf-8"), "outside\n")

    def test_nested_selected_project_gets_nested_scope(self) -> None:
        nested = self.repo / "services" / "example"
        (nested / "docs").mkdir(parents=True)
        (nested / "docs" / "requirements.md").write_text(
            "---\nschema: agentic-sdlc.requirements.v1\n---\n",
            encoding="utf-8",
        )
        (nested / "docs" / "design.md").write_text(
            "---\nschema: agentic-sdlc.design.v1\n---\n",
            encoding="utf-8",
        )
        manifest = self.inspect(project=nested)
        self.assertEqual(manifest["project_scope"], "services/example")
        state = self.apply(
            manifest,
            self.decision(
                manifest,
                "needed",
                body=self.body(scope="services/example"),
                project=nested,
            ),
        )
        self.assertEqual(state["outcome"], "created")
        self.assertTrue((nested / "AGENTS.md").is_file())
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_placeholder_body_is_rejected(self) -> None:
        manifest = self.inspect()
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            contracts._validate_body(
                self.body().replace("Example Project", "<Project>"),
                manifest,
            )
        self.assertEqual(context.exception.code, "UNSAFE_TARGET")

    def test_private_ipv6_and_secret_body_is_rejected(self) -> None:
        manifest = self.inspect()
        cases = (
            "See https://[::1]:8443/status.",
            "See https://[fd00::1]/status.",
            "See https://[fe80::1%25en0]/status.",
            "Use " + "Bearer " + "a" * 24 + ".",
        )
        for purpose in cases:
            with (
                self.subTest(purpose=purpose),
                self.assertRaises(contracts.ProjectInstructionsError) as context,
            ):
                contracts._validate_body(self.body(purpose=purpose), manifest)
            self.assertEqual(context.exception.code, "UNSAFE_TARGET")

    def test_public_ipv6_endpoint_body_is_allowed(self) -> None:
        manifest = self.inspect()
        body = self.body(purpose="See https://[2606:4700::1111]/status.")
        self.assertEqual(contracts._validate_body(body, manifest), body.encode())

    def test_generated_file_is_not_deleted_when_no_longer_needed(self) -> None:
        manifest = self.inspect()
        self.apply(
            manifest,
            self.decision(manifest, "needed", body=self.body()),
        )
        current = self.inspect()
        with self.assertRaises(contracts.ProjectInstructionsError) as context:
            self.apply(
                current,
                self.decision(current, "not-needed"),
            )
        self.assertEqual(context.exception.code, "STALE_GENERATED_FILE")
        self.assertTrue((self.repo / "AGENTS.md").is_file())


if __name__ == "__main__":
    unittest.main()
