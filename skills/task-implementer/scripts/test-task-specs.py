#!/usr/bin/env python3
"""Focused tests for steering ledgers and managed specification documents."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import prompt_workspace_lanes as lanes


SCRIPT = Path(__file__).resolve().with_name("prompt_workspace.py")
SPEC = importlib.util.spec_from_file_location("prompt_workspace_specs_test", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import invariant.
    raise RuntimeError("could not load prompt_workspace.py")
pw = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pw
SPEC.loader.exec_module(pw)
specs = sys.modules[pw.inspect_spec_documents.__module__]


FIXED = datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc)


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def requirement_body(identifier: str = "TI-REQ-001") -> str:
    return f"""## Task Implementer Requirements

### {identifier}: Safe steering

- Status: active
- Requirement: Apply steering at a safe boundary.
- Constraints: Preserve completed work.
- Non-goals: Live interruption.

#### Acceptance criteria

- Queued steering is applied once.

#### Verification

- Run focused tests.

## Task Implementer Open Questions

- None.

## Task Implementer Requirements Change Log

- 2026-07-13: Added {identifier}.
"""


def design_body(
    identifier: str = "TI-DES-001",
    requirement: str = "TI-REQ-001",
) -> str:
    return f"""## Task Implementer Designs

### {identifier}: Boundary steering

- Status: planned
- Requirements: {requirement}
- Selected approach: Queue edits during implementation.
- Boundaries and interfaces: Private intake state only.
- Validation: Focused tests.
- Rollback: Revert the task commit.

#### Alternatives considered

- Live interruption was rejected.

#### Implementation evidence

- Pending checkpoint.

## Task Implementer Design Change Log

- 2026-07-13: Added {identifier}.
"""


class TaskSpecificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.origin = self.root / "origin.git"
        git("init", "--bare", "-q", str(self.origin), cwd=self.root)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        self.scope = self.repo / "services" / "example"
        self.scope.mkdir(parents=True)
        (self.repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        (self.scope / "scope.txt").write_text("scope\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git(
            "-c",
            "user.name=Spec Test",
            "-c",
            "user.email=spec@example.invalid",
            "commit",
            "-qm",
            "initial",
            cwd=self.repo,
        )
        git("remote", "add", "origin", str(self.origin), cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.origin)
        git("fetch", "-q", "origin", cwd=self.repo)
        git(
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
            cwd=self.repo,
        )
        git("switch", "-qc", "spec-feature", cwd=self.repo)
        self.codex_home = self.root / "codex"
        lane = lanes.ensure_project_lane(self.scope)
        lane_root = Path(str(lane["worktree"]))
        result = pw.init_workspace(
            lane_root,
            "services/example",
            self.codex_home,
            lane=lane,
            clock=lambda: FIXED,
        )
        self.scope = Path(str(lane["scope_cwd"]))
        self.workspace = Path(result["workspace"])
        self.workspace_value = pw.verify_workspace(self.workspace)
        self.docs = self.scope / "docs"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_specs(self) -> tuple[Path, Path]:
        self.docs.mkdir(exist_ok=True)
        requirements = self.docs / "requirements.md"
        design = self.docs / "design.md"
        requirements.write_bytes(
            specs.new_spec_document("requirements", requirement_body())
        )
        design.write_bytes(specs.new_spec_document("design", design_body()))
        requirements.chmod(0o644)
        design.chmod(0o644)
        return requirements, design

    def assert_error(
        self, code: str, function: object, *args: object, **kwargs: object
    ) -> None:
        with self.assertRaises(pw.PromptWorkspaceError) as context:
            function(*args, **kwargs)
        self.assertEqual(context.exception.code, code)

    def test_missing_and_created_specs_have_stable_ids(self) -> None:
        missing = pw.inspect_spec_documents(self.workspace_value)
        self.assertEqual(missing["next_requirement_id"], "TI-REQ-001")
        self.assertEqual(missing["next_design_id"], "TI-DES-001")
        requirements, design = self.write_specs()

        inspected = pw.inspect_spec_documents(self.workspace_value)

        self.assertEqual(inspected["requirements"]["ids"], ["TI-REQ-001"])
        self.assertEqual(inspected["design"]["ids"], ["TI-DES-001"])
        self.assertEqual(
            inspected["design"]["requirements"]["TI-DES-001"],
            ["TI-REQ-001"],
        )
        self.assertEqual(inspected["next_requirement_id"], "TI-REQ-002")
        self.assertEqual(inspected["next_design_id"], "TI-DES-002")
        before = (requirements.read_bytes(), design.read_bytes())
        repeated = pw.inspect_spec_documents(self.workspace_value)
        self.assertEqual(repeated, inspected)
        self.assertEqual(before, (requirements.read_bytes(), design.read_bytes()))

    def test_generic_unicode_crlf_envelope_is_byte_preserved(self) -> None:
        self.docs.mkdir()
        requirements = self.docs / "requirements.md"
        original = "# Existing\r\n\r\nHuman-owned café.\r\n".encode("utf-8")
        requirements.write_bytes(original)
        requirements.chmod(0o644)
        generic = pw.inspect_spec_documents(self.workspace_value)["requirements"]

        merged = specs.append_managed_region(
            original, "requirements", requirement_body()
        )
        requirements.write_bytes(merged)
        requirements.chmod(0o644)
        inspected = pw.inspect_spec_documents(self.workspace_value)["requirements"]

        self.assertTrue(merged.startswith(original))
        self.assertEqual(
            inspected["surrounding_sha256"],
            generic["rendered_surrounding_sha256"],
        )
        replaced = specs.replace_managed_region(
            merged, "requirements", requirement_body()
        )
        self.assertEqual(replaced, merged)

    def test_owner_markers_and_path_conflicts_fail_closed(self) -> None:
        self.docs.mkdir()
        requirements = self.docs / "requirements.md"
        requirements.write_text(
            "---\nschema: agentic-sdlc.requirements.v1\n---\n# Requirements\n",
            encoding="utf-8",
        )
        requirements.chmod(0o644)
        self.assert_error(
            "SPEC_OWNER_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )
        requirements.write_text(
            "---\nschema: 'agentic-sdlc.requirements.v1'\n---\n# Requirements\n",
            encoding="utf-8",
        )
        self.assert_error(
            "SPEC_OWNER_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )

        requirements.write_text(
            "<!-- task-implementer:requirements:start "
            "schema=task-implementer/requirements-v1 -->\n",
            encoding="utf-8",
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )
        if os.name == "posix":
            requirements.unlink()
            outside = self.root / "outside.md"
            outside.write_text("outside\n", encoding="utf-8")
            requirements.symlink_to(outside)
            self.assert_error(
                "SPEC_CONFLICT",
                pw.inspect_spec_documents,
                self.workspace_value,
            )

    def test_duplicate_ids_fail_closed(self) -> None:
        requirements, design = self.write_specs()
        requirements.write_bytes(
            specs.new_spec_document(
                "requirements",
                requirement_body() + "\n" + requirement_body(),
            )
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )
        requirements.write_bytes(
            specs.new_spec_document(
                "requirements", requirement_body(identifier="TI-REQ-00")
            )
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )

    def test_required_structure_unknown_and_private_mappings_fail_closed(self) -> None:
        requirements, design = self.write_specs()
        requirements.write_bytes(
            specs.new_spec_document(
                "requirements",
                requirement_body().replace(
                    "#### Acceptance criteria",
                    "#### Missing acceptance criteria",
                ),
            )
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )

        requirements.write_bytes(
            specs.new_spec_document("requirements", requirement_body())
        )
        design.write_bytes(
            specs.new_spec_document(
                "design",
                design_body().replace(
                    "- Requirements: TI-REQ-001",
                    "- Related requirement: TI-REQ-001",
                ),
            )
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )

        requirements.write_bytes(
            specs.new_spec_document("requirements", requirement_body())
        )
        design.write_bytes(
            specs.new_spec_document("design", design_body(requirement="TI-REQ-999"))
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )

        for leaked_state in (
            "run-20260713t150000z-deadbeef",
            "task-2",
            "a" * 64,
        ):
            with self.subTest(leaked_state=leaked_state):
                design.write_bytes(
                    specs.new_spec_document(
                        "design", design_body() + f"\n{leaked_state}\n"
                    )
                )
                self.assert_error(
                    "SPEC_CONFLICT",
                    pw.inspect_spec_documents,
                    self.workspace_value,
                )

    def test_steering_ledger_orders_aba_and_resolves_idempotently(self) -> None:
        prompt = Path(
            pw.create_prompt(
                self.workspace,
                "Steer one prompt",
                clock=lambda: FIXED,
                id_factory=lambda: "b" * 32,
            )["path"]
        )
        text = prompt.read_text(encoding="utf-8")
        text = (
            text.replace(
                "<!-- Required: describe what must be true when the work is complete. -->",
                "Outcome A.",
            )
            .replace(
                "- [ ] <!-- Required: add an observable, testable completion criterion. -->",
                "- [ ] Acceptance A.",
            )
            .replace(
                "<!-- Required: name expected checks or ask Codex to derive them from the repo. -->",
                "Verification A.",
            )
        )
        prompt.write_text(text, encoding="utf-8")
        prompt.chmod(0o600)
        first = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED,
        )
        run_dir = Path(first["manifest"]).parent
        prompt.write_text(text.replace("Outcome A.", "Outcome B."), encoding="utf-8")
        prompt.chmod(0o600)
        second = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=str(first["run_id"]),
            force_new_run=False,
            clock=lambda: FIXED.replace(second=1),
        )
        prompt.write_text(text, encoding="utf-8")
        prompt.chmod(0o600)
        third = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=str(first["run_id"]),
            force_new_run=False,
            clock=lambda: FIXED.replace(second=2),
        )
        manifest = json.loads(Path(first["manifest"]).read_text(encoding="utf-8"))
        specs.record_steering_revision(
            run_dir, manifest["revisions"], str(second["revision"]), FIXED
        )
        specs.record_steering_revision(
            run_dir, manifest["revisions"], str(third["revision"]), FIXED
        )
        specs.record_steering_revision(
            run_dir, manifest["revisions"], str(third["revision"]), FIXED
        )
        ledger = specs.load_steering_ledger(run_dir, manifest["revisions"])
        self.assertEqual(
            [event["revision"] for event in ledger["events"]],
            ["r0002", "r0003"],
        )
        self.assert_error(
            "RUN_STATE_INVALID",
            specs.resolve_steering_revision,
            run_dir,
            manifest["revisions"],
            "r0003",
            "applied",
            clock=lambda: FIXED.replace(second=30),
        )
        resolved = specs.resolve_steering_revision(
            run_dir,
            manifest["revisions"],
            "r0002",
            "no_effect",
            clock=lambda: FIXED.replace(minute=1),
        )
        repeated = specs.resolve_steering_revision(
            run_dir,
            manifest["revisions"],
            "r0002",
            "no_effect",
            clock=lambda: FIXED.replace(minute=2),
        )
        self.assertEqual(resolved, repeated)
        self.assertEqual(resolved["disposition"], "no_effect")


if __name__ == "__main__":
    unittest.main()
