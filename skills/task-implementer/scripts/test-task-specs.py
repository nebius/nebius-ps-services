#!/usr/bin/env python3
"""Focused tests for steering ledgers and managed specification documents."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
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
        self.root = Path(self.temporary.name).resolve()
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

    def write_specs(self, *, tracked: bool = True) -> tuple[Path, Path]:
        self.docs.mkdir(exist_ok=True)
        requirements = self.docs / "requirements.md"
        design = self.docs / "design.md"
        requirements.write_bytes(
            specs.new_spec_document("requirements", requirement_body())
        )
        design.write_bytes(specs.new_spec_document("design", design_body()))
        requirements.chmod(0o644)
        design.chmod(0o644)
        if tracked:
            git(
                "add",
                "services/example/docs",
                cwd=Path(str(self.workspace_value["repo_root"])),
            )
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
        self.assertIsNone(missing["project_agent_spec_receipt"])
        with self.assertRaises(pw.PromptWorkspaceError) as caught:
            specs.verify_project_agent_contract(
                self.workspace_value,
                self.root / "missing-run",
                self.scope,
                git(
                    "rev-parse",
                    "HEAD",
                    cwd=Path(str(self.workspace_value["repo_root"])),
                ),
            )
        self.assertEqual(caught.exception.code, "SPEC_CONFLICT")
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
        receipt = inspected["project_agent_spec_receipt"]
        self.assertEqual(
            receipt["schema"], "project-agent-instructions.spec-validation.v3"
        )
        self.assertEqual(receipt["owner"], "maintain-project-specs")
        self.assertEqual(receipt["project_scope"], "services/example")
        self.assertEqual(
            receipt["requirements"]["sha256"],
            inspected["requirements"]["file_sha256"],
        )
        self.assertRegex(receipt["traceability_sha256"], r"^[0-9a-f]{64}$")
        before = (requirements.read_bytes(), design.read_bytes())
        repeated = pw.inspect_spec_documents(self.workspace_value)
        self.assertEqual(repeated, inspected)
        self.assertEqual(before, (requirements.read_bytes(), design.read_bytes()))

    def test_untracked_specs_do_not_emit_a_validation_receipt(self) -> None:
        self.write_specs(tracked=False)

        inspected = pw.inspect_spec_documents(self.workspace_value)

        self.assertIsNone(inspected["project_agent_spec_receipt"])

    def test_commit_snapshot_does_not_fabricate_authoritative_receipt(self) -> None:
        self.write_specs()
        repo_root = Path(str(self.workspace_value["repo_root"]))
        git(
            "-c",
            "user.name=Spec Test",
            "-c",
            "user.email=spec@example.invalid",
            "commit",
            "-qm",
            "record specs",
            cwd=repo_root,
        )
        commit = git("rev-parse", "HEAD", cwd=repo_root)

        inspected = pw.inspect_spec_documents(self.workspace_value, commit=commit)

        self.assertIsNone(inspected["project_agent_spec_receipt"])

    def test_every_applicable_requirement_must_be_mapped(self) -> None:
        requirements, _design = self.write_specs()
        second_record = """### TI-REQ-002: Preserve traceability

- Status: active
- Requirement: Map every applicable requirement.
- Constraints: Ignore superseded requirements.
- Non-goals: Duplicate mappings.

#### Acceptance criteria

- Every applicable requirement is covered.

#### Verification

- Run focused tests.

"""
        requirements.write_bytes(
            specs.new_spec_document(
                "requirements",
                requirement_body().replace(
                    "## Task Implementer Open Questions",
                    second_record + "## Task Implementer Open Questions",
                ),
            )
        )

        self.assert_error(
            "SPEC_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )

    def test_superseded_requirement_and_design_are_not_current_coverage(self) -> None:
        requirements, design = self.write_specs()
        requirements.write_bytes(
            specs.new_spec_document(
                "requirements",
                requirement_body().replace("- Status: active", "- Status: superseded"),
            )
        )
        design.write_bytes(
            specs.new_spec_document(
                "design",
                design_body().replace("- Status: planned", "- Status: superseded"),
            )
        )

        inspected = pw.inspect_spec_documents(self.workspace_value)

        self.assertIsInstance(inspected["project_agent_spec_receipt"], dict)

    def test_project_agent_contract_gate_verifies_owner_receipt_and_state(self) -> None:
        self.write_specs()
        (self.scope / "AGENTS.md").write_text(
            "# Human project instructions\n\n- Preserve the validated contract.\n",
            encoding="utf-8",
        )
        repo_root = Path(str(self.workspace_value["repo_root"]))
        git("add", "services/example/docs", "services/example/AGENTS.md", cwd=repo_root)
        git(
            "-c",
            "user.name=Spec Test",
            "-c",
            "user.email=spec@example.invalid",
            "commit",
            "-qm",
            "lock project contract",
            cwd=repo_root,
        )
        contract_commit = git("rev-parse", "HEAD", cwd=repo_root)
        inspected = pw.inspect_spec_documents(self.workspace_value)
        run_dir = self.root / "private-run"
        orchestration = run_dir / "orchestration"
        private_root = orchestration / "project-agent-instructions"
        private_root.mkdir(parents=True)
        private_root.chmod(0o700)
        receipt_path = orchestration / "project-agent-spec-receipt.json"
        receipt_path.write_text(
            json.dumps(
                inspected["project_agent_spec_receipt"], indent=2, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )
        receipt_path.chmod(0o600)
        runtime_path = orchestration / "project-agent-runtime.json"
        runtime_path.write_text(
            json.dumps(
                {
                    "schema": "project-agent-instructions.runtime-config.v1",
                    "profile": None,
                    "overrides": {"project_root_markers": ["scope.txt"]},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_path.chmod(0o600)
        helper = (
            Path(__file__).resolve().parents[2]
            / "project-agent-instructions"
            / "scripts"
            / "project_agent_instructions.py"
        )
        inspect_result = subprocess.run(
            [
                sys.executable,
                str(helper),
                "inspect",
                "--project-root",
                str(self.scope),
                "--spec-owner",
                "maintain-project-specs",
                "--spec-receipt",
                str(receipt_path),
                "--runtime-config",
                str(runtime_path),
                "--codex-home",
                str(self.codex_home),
                "--private-root",
                str(private_root),
                "--output",
                "manifest.json",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(inspect_result.returncode, 0, inspect_result.stdout)
        manifest = json.loads((private_root / "manifest.json").read_text())
        design = self.scope / "docs/design.md"
        decision = {
            "schema": "project-agent-instructions.decision.v3",
            "manifest_sha256": manifest["manifest_sha256"],
            "disposition": "existing-sufficient",
            "rationale": "The tracked human project instructions are sufficient.",
            "evidence": [
                {
                    "path": "docs/design.md",
                    "sha256": hashlib.sha256(design.read_bytes()).hexdigest(),
                    "locator": "Task Implementer Design Change Log",
                }
            ],
            "rules": [],
            "budget_exception": None,
            "ownership_approval": None,
        }
        decision_path = private_root / "decision.json"
        decision_path.write_text(
            json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        decision_path.chmod(0o600)
        render_result = subprocess.run(
            [
                sys.executable,
                str(helper),
                "render",
                "--private-root",
                str(private_root),
                "--manifest",
                "manifest.json",
                "--decision",
                "decision.json",
                "--output",
                "rules.md",
                "--state",
                "state.json",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(render_result.returncode, 0, render_result.stdout)
        verified = specs.verify_project_agent_contract(
            self.workspace_value, run_dir, self.scope, contract_commit
        )
        self.assertEqual(verified["disposition"], "existing-sufficient")
        self.assertFalse(verified["repository_mutated"])

        receipt_path.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(pw.PromptWorkspaceError) as caught:
            specs.verify_project_agent_contract(
                self.workspace_value, run_dir, self.scope, contract_commit
            )
        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")

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

    def test_requirements_refinement_tracks_material_questions_and_ready_digest(
        self,
    ) -> None:
        requirements, _design = self.write_specs()
        created = pw.create_prompt(
            self.workspace,
            "Implement prompt refinement",
            clock=lambda: FIXED,
            id_factory=lambda: "a" * 32,
        )
        snapshot = pw.snapshot_prompt(
            self.workspace,
            Path(str(created["path"])),
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED,
        )
        run_dir = Path(str(snapshot["manifest"])).parent
        state = specs.load_requirements_refinement(run_dir, required=True)
        assert state is not None
        self.assertEqual(state["status"], "extracting")
        self.assertEqual(state["revision"], "r0001")
        state["questions"] = [
            {
                "id": "Q-001",
                "question": "Which external contract is authoritative?",
                "material": True,
                "status": "open",
                "answer": None,
                "source": None,
                "source_revision": None,
                "conflict": None,
            }
        ]
        requirements_digest = specs.inspect_spec_documents(self.workspace_value)[
            "requirements"
        ]["managed_sha256"]
        state["compiled_requirements_sha256"] = requirements_digest
        state["status"] = "ready"
        refinement_path = run_dir / "requirements-refinement.json"
        valid_bytes = refinement_path.read_bytes()
        self.assert_error(
            "RUN_STATE_INVALID",
            specs.save_requirements_refinement,
            run_dir,
            state,
        )
        self.assertEqual(refinement_path.read_bytes(), valid_bytes)
        state["questions"][0].update(
            {
                "status": "answered",
                "answer": "Use the documented v2 contract.",
                "source": "chat",
                "source_revision": "r0001",
            }
        )
        saved = specs.save_requirements_refinement(run_dir, state)
        self.assertEqual(saved["status"], "ready")
        run_state = pw.verify_run(self.workspace_value, str(snapshot["run_id"]), None)
        verified = specs.verify_requirements_refinement_contract(
            self.workspace_value, run_dir, run_state
        )
        self.assertEqual(
            verified["refinement"]["compiled_requirements_sha256"],
            requirements_digest,
        )
        requirements.write_text(
            requirements.read_text(encoding="utf-8").replace(
                "Apply steering at a safe boundary.",
                "Apply revised steering at a safe boundary.",
            ),
            encoding="utf-8",
        )
        self.assert_error(
            "REQUIREMENTS_REFINEMENT_REQUIRED",
            specs.verify_requirements_refinement_contract,
            self.workspace_value,
            run_dir,
            run_state,
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
        text = text.rstrip() + (
            "\n\n## Outcome\n\nOutcome A.\n"
            "\n## Acceptance criteria\n\n- [ ] Acceptance A.\n"
            "\n## Verification\n\nVerification A.\n"
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
