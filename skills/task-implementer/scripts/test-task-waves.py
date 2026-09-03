#!/usr/bin/env python3
"""Disposable real-Git tests for dependency-wave worktree lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import prompt_workspace as pw
import prompt_workspace_lanes as lanes
import prompt_workspace_recovery as recovery
import prompt_workspace_resume as resume
import prompt_workspace_specs as specs
import prompt_workspace_waves as waves
from prompt_workspace_core import PromptWorkspaceError
from prompt_workspace_execution import (
    RESULT_SCHEMA,
    WORKER_GUARDRAILS,
    WORKER_HEARTBEAT_SECONDS,
    WORKER_INTEGRATION_READ_ONLY_SECONDS,
    WORKER_INTEGRATION_WARNING_SECONDS,
    WORKER_MAX_SECONDS,
    WORKER_START_SECONDS,
    WORKER_STALL_SECONDS,
    WORKER_STANDARD_READ_ONLY_SECONDS,
    WORKER_STANDARD_WARNING_SECONDS,
    parse_task_plans,
    sha256_json,
    task_sections,
    task_statuses,
    worker_liveness_profile,
)


FIXED = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
FIXED_TEXT = FIXED.isoformat(timespec="seconds")


REQUIREMENT_BODY = """# Task Implementer Requirements

<!-- REQUIREMENT: TI-REQ-001 status=active priority=P1 type=feature -->
### TI-REQ-001: Execute dependency waves safely

#### User Story

Bind execution to accepted project intent while preserving the managed lane;
runtime installation is excluded.

#### Acceptance Criteria

- AC-001: Validated waves progress.

#### Negative Criteria

- NC-001: Unvalidated waves do not progress.

#### Validation Method

Run focused validation.

#### Test Method

Run the focused wave tests.

#### Evaluation Method

Inspect wave progression evidence.

<!-- /REQUIREMENT: TI-REQ-001 -->

## Open Questions

- None.

## Change Log

- 2026-07-14: Established the test contract.
"""

DESIGN_BODY = """# Task Implementer Designs

<!-- FEATURE: TI-DES-001 reqs=TI-REQ-001 status=ready delivery=not-started priority=P1 version=1 -->
### TI-DES-001: Bind wave execution

#### Requirements Covered

- TI-REQ-001: Execute dependency waves safely.

#### Context Evidence

Focused owner and dependency-wave tests.

#### Design Details

Validate intent before wave progression through prompt-impact and wave state.

#### Selected Option

Validate intent before wave progression.

#### Alternatives Considered

- Unbound execution was rejected.

#### Implementation Boundaries

Prompt impact and wave state.

#### Test-First Success Criteria

- TDD-001: Unbound execution fails before wave progression.

#### Validation Plan

Run focused owner and wave tests.

#### Test Plan

Run focused dependency-wave tests.

#### Evaluation Plan

Inspect wave progression evidence.

#### Rollout And Rollback

Retain the managed lane on failure.

#### Done Definition

The mapped requirement and focused checks pass.

#### Implementation Evidence

- Prompt impact settlement tests.

#### Verification Evidence

- Independent verification is pending.

<!-- /FEATURE: TI-DES-001 -->

## Change Log

- 2026-07-14: Established the test design.
"""


def git(*arguments: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def load_lifecycle_hook():
    path = (
        Path(__file__).resolve().parents[2]
        / "maintain-project-specs"
        / "assets"
        / "hooks"
        / "project_specs_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location(
        f"task_wave_lifecycle_{hashlib.sha256(str(path).encode()).hexdigest()[:12]}",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorktreeWaveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspace with spaces"
        self.root.mkdir()
        self.previous_codex_home = os.environ.get("CODEX_HOME")
        self.codex_home = self.root / "codex home"
        os.environ["CODEX_HOME"] = str(self.codex_home.resolve(strict=False))
        self.origin = self.root / "origin.git"
        git("init", "--bare", "-q", str(self.origin), cwd=self.root)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git("init", "-q", "-b", "trunk", cwd=self.repo)
        git("config", "user.name", "Wave Test", cwd=self.repo)
        git("config", "user.email", "wave@example.invalid", cwd=self.repo)
        self.scope = self.repo / "services" / "example"
        self.scope.mkdir(parents=True)
        for name in ("one.txt", "two.txt", "three.txt"):
            (self.scope / name).write_text("base\n", encoding="utf-8")
        docs = self.scope / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "requirements.md").write_bytes(
            specs.new_spec_document("requirements", REQUIREMENT_BODY)
        )
        (docs / "design.md").write_bytes(specs.new_spec_document("design", DESIGN_BODY))
        instruction_body = b"# Project instructions\n\nStable rules.\n"
        (self.scope / "AGENTS.md").write_bytes(
            b"<!-- project-agent-instructions:managed-v3 manifest-sha256="
            + b"a" * 64
            + b" decision-sha256="
            + b"b" * 64
            + b" body-sha256="
            + hashlib.sha256(instruction_body).hexdigest().encode()
            + b" -->\n\n"
            + instruction_body
        )
        (self.repo / ".gitignore").write_text("ignored.env\n", encoding="utf-8")
        (self.repo / "ignored.env").write_text("must not be copied\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "initial", cwd=self.repo)
        self.initial = git("rev-parse", "HEAD", cwd=self.repo)
        default_branch = git("branch", "--show-current", cwd=self.repo)
        self.default_branch = default_branch
        git("remote", "add", "origin", str(self.origin), cwd=self.repo)
        git("push", "-qu", "origin", default_branch, cwd=self.repo)
        git(
            "symbolic-ref",
            "HEAD",
            f"refs/heads/{default_branch}",
            cwd=self.origin,
        )
        git("fetch", "-q", "origin", cwd=self.repo)
        git(
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            f"refs/remotes/origin/{default_branch}",
            cwd=self.repo,
        )
        git("switch", "-qc", "wave-feature", cwd=self.repo)
        lane = lanes.ensure_project_lane(self.scope)
        lane_root = Path(str(lane["worktree"]))
        initialized = pw.init_workspace(
            lane_root,
            "services/example",
            self.codex_home,
            lane=lane,
            clock=lambda: FIXED,
        )
        self.primary = self.repo
        self.repo = lane_root
        self.scope = Path(str(lane["scope_cwd"]))
        self.workspace = Path(initialized["workspace"])
        self.refinement_gate = mock.patch.object(
            waves,
            "verify_requirements_refinement_contract",
            side_effect=lambda _workspace, run_dir, _run_state: {
                "impact": specs.load_current_prompt_impact(run_dir, required=True)[0],
                "impact_sha256": specs.load_current_prompt_impact(
                    run_dir, required=True
                )[1],
            },
        )
        self.refinement_gate.start()
        self.addCleanup(self.refinement_gate.stop)
        self.impact_plan_gate = mock.patch.object(
            waves,
            "verify_prompt_impact_plan",
            return_value={"status": "settled"},
        )
        self.impact_plan_gate.start()
        self.addCleanup(self.impact_plan_gate.stop)
        prompt = pw.create_prompt(
            self.workspace,
            "Implement two independent tasks then one dependent task",
            clock=lambda: FIXED,
            id_factory=lambda: "b" * 32,
        )
        self.prompt = Path(prompt["path"])
        text = self.prompt.read_text(encoding="utf-8").rstrip() + (
            "\n\n## Outcome\n\nAll three files are updated.\n"
            "\n## Acceptance criteria\n\n- [ ] All wave commits are promoted.\n"
            "\n## Verification\n\nInspect the final files.\n"
        )
        self.prompt.write_text(text, encoding="utf-8")
        self.prompt.chmod(0o600)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            self.prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED,
        )
        self.run_id = str(snapshot["run_id"])
        self.run_dir = Path(snapshot["manifest"]).parent
        refinement = specs.load_requirements_refinement(self.run_dir, required=True)
        assert refinement is not None
        refinement["status"] = "ready"
        refinement["extracted"]["constraints"] = [
            "Bind dependency-wave execution to accepted intent."
        ]
        refinement["compiled_requirements_sha256"] = specs.inspect_spec_documents(
            pw.verify_workspace(self.workspace)
        )["requirements"]["managed_sha256"]
        specs.save_requirements_refinement(self.run_dir, refinement)
        specs.write_atomic(
            specs.prompt_impact_claim_path(self.run_dir),
            specs.stable_json(
                {
                    "schema": specs.IMPACT_CLAIM_SCHEMA,
                    "prompt_id": refinement["prompt_id"],
                    "revision": refinement["revision"],
                    "intent_sha256": refinement["intent_sha256"],
                    "dispositions": [
                        {
                            "statement": "constraints:0001",
                            "disposition": "existing_contract",
                            "requirements": ["TI-REQ-001"],
                            "design": ["TI-DES-001"],
                            "effects": [],
                            "reason": None,
                        }
                    ],
                    "declared_effects": [],
                    "declared_plan_action": "retain_plan",
                }
            ),
        )
        run_state = pw.verify_run(
            pw.verify_workspace(self.workspace), self.run_id, None
        )
        specs.verify_requirements_refinement_contract(
            pw.verify_workspace(self.workspace), self.run_dir, run_state
        )
        self._write_handoff()

    def tearDown(self) -> None:
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "list"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if self.previous_codex_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.previous_codex_home
        self.temporary.cleanup()

    def test_write_claims_reject_inline_multiple_claims(self) -> None:
        handoff = """# Handoff

## Task Queue

### task-1

- Status: pending
- Depends on: none
- Write claims: exact: services/example/one.txt; exact: services/example/two.txt
- Conflict domains: files:one
- Implementation steps: update the claimed files
- Validation: inspect the exact paths
- End-to-end validation: verify the correction
- Done criteria: both files contain the correction
"""

        with self.assertRaisesRegex(
            PromptWorkspaceError, "each write claim must appear on its own line"
        ):
            parse_task_plans(handoff)

    def test_blocked_replan_accepts_prior_superseded_task(self) -> None:
        wave = {
            "active_batch_index": 3,
            "batches": [["task-1"], ["task-2"], ["task-3"], ["task-4"]],
            "task_states": {
                "task-1": "merged",
                "task-2": "superseded",
                "task-3": "merged",
                "task-4": "failed",
            },
        }

        self.assertEqual(waves._blocked_replan_failed_ids(wave), ["task-4"])

    def _write_handoff(self) -> None:
        manifest_path = self.run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bound = manifest["revisions"][0]
        tasks = []
        for number, name, dependency in (
            (1, "one.txt", "none"),
            (2, "two.txt", "none"),
            (3, "three.txt", "task-1, task-2"),
        ):
            tasks.append(
                f"""### task-{number}

- Status: pending
- Depends on: {dependency}
- Write claims: exact: services/example/{name}
- Conflict domains: files:{name}
- Implementation steps: update only services/example/{name}
- Validation: inspect {name}
- End-to-end validation: verify the dependency wave result
- Done criteria: {name} contains the task update
"""
            )
        handoff = f"""# Task Implementer Handoff

## Run

- Run ID: {self.run_id}
- Run manifest: {manifest_path}
- Prompt ID: {manifest["prompt_id"]}
- Bound revision: {bound["revision"]}
- Bound SHA-256: {bound["sha256"]}
- Bound snapshot path: {self.run_dir / bound["snapshot"]}
- Last invoked at: 2026-07-14T12:00:00+00:00
- Overall status: running

## Reconciliation

- State: none

## Task Queue

{"".join(tasks)}
"""
        path = self.run_dir / "handoff.md"
        path.write_text(handoff, encoding="utf-8")
        path.chmod(0o600)

    def _write_resume_intent(
        self, transition: str, arguments: dict[str, object]
    ) -> Path:
        path = self.run_dir / "orchestration" / "resume-control.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": resume.RESUME_CONTROL_SCHEMA,
                    "run_id": self.run_id,
                    "epoch": 1,
                    "adopted": True,
                    "phase": "intent",
                    "pre_state_sha256": "1" * 64,
                    "transition": transition,
                    "arguments": arguments,
                    "arguments_sha256": hashlib.sha256(
                        specs.stable_json(arguments)
                    ).hexdigest(),
                    "resume_token": "2" * 64,
                    "terminal_state_sha256": None,
                    "projection_sha256": None,
                    "updated_at": FIXED_TEXT,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def _prepare_contract_edit(self) -> tuple[Path, Path]:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration = Path(str(prepared["integration_worktree"]))
        return integration, integration / "services" / "example"

    def _substitute_counterfeit_integration(self, integration: Path) -> Path:
        branch = git("branch", "--show-current", cwd=integration)
        expected_head = git("rev-parse", "HEAD", cwd=integration)
        retained = integration.with_name(f"{integration.name}-linked")
        integration.rename(retained)
        git(
            "clone",
            "-q",
            "--no-local",
            "--branch",
            branch,
            str(self.repo),
            str(integration),
            cwd=integration.parent,
        )
        self.assertEqual(git("branch", "--show-current", cwd=integration), branch)
        self.assertEqual(git("rev-parse", "HEAD", cwd=integration), expected_head)
        return retained

    def _restore_linked_integration(self, integration: Path, retained: Path) -> None:
        shutil.rmtree(integration)
        retained.rename(integration)

    def test_replan_without_coordinator_reports_current_schema(self) -> None:
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.replan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")
        self.assertEqual(str(raised.exception), "run has no v7 coordinator")

    def test_journal_less_v7_plan_adopts_exact_stable_boundary(self) -> None:
        planned = pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        workspace = pw.verify_workspace(self.workspace)
        first = resume.plan_run_resume(workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(first["outcome"], "execute")
        self.assertEqual(first["next_transition"], "wave-prepare")
        adopted = resume.adopt_resume_plan(
            workspace, self.run_id, first, clock=lambda: FIXED
        )
        self.assertEqual(adopted["epoch"], 1)
        self.assertIsNotNone(adopted["resume_token"])
        repeated = resume.plan_run_resume(workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(repeated["state_sha256"], first["state_sha256"])
        self.assertEqual(repeated["next_transition"], "wave-prepare")
        self.assertEqual(planned["active_wave"], "wave-001")

    def test_prepare_rejects_plan_stale_after_prompt_revision(self) -> None:
        planned = pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.prompt.write_text(
            self.prompt.read_text(encoding="utf-8").rstrip()
            + "\n\n## Steering\n\nChange the planned behavior before resources exist.\n",
            encoding="utf-8",
        )

        self.prompt.chmod(0o600)
        routed = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            self.prompt.name,
            clock=lambda: FIXED + timedelta(seconds=1),
        )
        self.assertEqual(routed["action"], "reconcile")
        with self.assertRaises(PromptWorkspaceError) as plan_error:
            pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.assertEqual(plan_error.exception.code, "REPLAN_REQUIRED")
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(raised.exception.code, "REPLAN_REQUIRED")
        active = next(
            item
            for item in planned["waves"]
            if item["wave_id"] == planned["active_wave"]
        )
        wave = json.loads(
            (
                self.run_dir / "orchestration" / "waves" / f"{active['wave_id']}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(wave["status"], "planned")
        self.assertFalse(Path(str(wave["integration_worktree"])).exists())

    def test_commit_authorization_rejects_private_symlink_escape(self) -> None:
        root = self.root / "authorization-root"
        root.mkdir(mode=0o700)
        escape = self.root / "authorization-escape"
        escape.mkdir()
        (root / "commit-transactions").symlink_to(escape, target_is_directory=True)
        with self.assertRaisesRegex(pw.PromptWorkspaceError, "must not be a symlink"):
            waves._ensure_commit_authorization_parent(
                root,
                root / "commit-transactions" / "repo" / "session",
            )

    def test_task_start_returns_unambiguous_transient_commit_context(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration/assignments/wave-001/task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            started = pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="raw-worker-session",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)

        self.assertNotIn("worker_session_sha256", started)
        self.assertEqual(
            started["worker_session_fingerprint_sha256"],
            hashlib.sha256(b"raw-worker-session").hexdigest(),
        )
        context = started["commit_context"]
        self.assertEqual(context["schema"], "task-implementer/worker-commit-context-v2")
        self.assertEqual(context["session_id"], "raw-worker-session")
        self.assertEqual(context["session_id_source"], "CODEX_THREAD_ID")
        self.assertEqual(Path(context["repo_root"]), Path(assignment["worktree"]))
        self.assertEqual(Path(context["scope_cwd"]), self.scope)
        self.assertEqual(context["prepare_argv"][0], context["python_executable"])
        self.assertEqual(context["prepare_argv"][1], context["helper_path"])
        self.assertEqual(
            context["prepare_argv"][context["prepare_argv"].index("--session-id") + 1],
            "raw-worker-session",
        )
        self.assertFalse(Path(context["claim"]).exists())
        for persisted in (
            assignment_path,
            self.run_dir / "orchestration/tasks/wave-001/task-1.json",
            self.run_dir / "orchestration/waves/wave-001.json",
            self.run_dir / "orchestration/coordinator.json",
            Path(context["authorization"]),
        ):
            self.assertNotIn(
                "raw-worker-session", persisted.read_text(encoding="utf-8")
            )
        self.assertEqual(
            git("diff", "--cached", "--name-only", cwd=Path(assignment["worktree"])), ""
        )
        result_context = started["result_context"]
        self.assertEqual(
            result_context["schema"], "task-implementer/worker-result-context-v1"
        )
        self.assertEqual(result_context["result_path"], assignment["result_path"])
        self.assertEqual(
            Path(result_context["publication_cwd"]),
            Path(assignment["result_path"]).parent,
        )
        self.assertTrue(Path(result_context["publication_cwd"]).is_dir())
        self.assertFalse(
            Path(result_context["publication_cwd"]).is_relative_to(
                Path(assignment["worktree"])
            )
        )

    def test_result_publication_directory_failure_precedes_worker_ownership(
        self,
    ) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration/assignments/wave-001/task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            with (
                mock.patch.object(
                    waves, "ensure_private_dir", side_effect=OSError("denied")
                ),
                self.assertRaisesRegex(OSError, "denied"),
            ):
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    FIXED_TEXT,
                    session_id="raw-worker-session",
                    clock=lambda: FIXED,
                )
        finally:
            os.chdir(previous)
        plane = json.loads(
            (self.run_dir / "orchestration/tasks/wave-001/task-1.json").read_text(
                encoding="utf-8"
            )
        )
        wave = json.loads(
            (self.run_dir / "orchestration/waves/wave-001.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(plane["state"], "assigned")
        self.assertIsNone(plane["worker_session_sha256"])
        self.assertEqual(wave["task_states"]["task-1"], "assigned")

    def test_worker_result_publisher_computes_final_digest_atomically(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration/assignments/wave-001/task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        armed = pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            started = pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                str(armed["start_lease"]),
                session_id="publisher-session",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)
        context = started["result_context"]
        draft = {
            "schema": RESULT_SCHEMA,
            "run_id": self.run_id,
            "wave_id": "wave-001",
            "task_id": "task-1",
            "assignment_sha256": assignment["assignment_sha256"],
            "status": "committed",
            "commit": "a" * 40,
            "changed_paths": [
                "services/example/z-last.txt",
                "services/example/a-first.txt",
            ],
            "summary": "Implemented task one",
            "decisions": ["Kept one canonical path."],
            "open_risks": ["Offline proof only."],
            "spec_gaps": [],
            "validation": "Focused validation passed.",
            "end_to_end_validation": "Observed deterministic behavior.",
            "code_review": "No unresolved finding.",
            "completed_at": FIXED_TEXT,
        }
        draft_path = Path(context["draft_path"])
        draft_path.write_bytes(specs.stable_json({**draft, "status": "COMPLETED"}))
        os.chdir(Path(context["publication_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as invalid_status:
                pw.publish_task_result(
                    assignment_path,
                    draft_path,
                    Path(context["result_path"]),
                )
            self.assertEqual(invalid_status.exception.code, "EXECUTION_STATE_INVALID")
            self.assertFalse(Path(context["result_path"]).exists())
            draft_path.write_bytes(specs.stable_json(draft))
            published = pw.publish_task_result(
                assignment_path,
                draft_path,
                Path(context["result_path"]),
            )
            replayed = pw.publish_task_result(
                assignment_path,
                draft_path,
                Path(context["result_path"]),
            )
        finally:
            os.chdir(previous)
        result = json.loads(Path(context["result_path"]).read_text(encoding="utf-8"))
        canonical = {
            **draft,
            "changed_paths": sorted(draft["changed_paths"]),
        }
        self.assertEqual(result["changed_paths"], canonical["changed_paths"])
        self.assertEqual(result["result_sha256"], sha256_json(canonical))
        self.assertEqual(published, replayed)
        self.assertEqual(Path(context["result_path"]).stat().st_mode & 0o777, 0o600)

    def test_terminal_replan_result_replays_and_resumes_as_blocked(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration/assignments/wave-001/task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        armed = pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            started = pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                str(armed["start_lease"]),
                session_id="terminal-replan-session",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)
        draft = {
            "schema": RESULT_SCHEMA,
            "run_id": self.run_id,
            "wave_id": "wave-001",
            "task_id": "task-1",
            "assignment_sha256": assignment["assignment_sha256"],
            "status": "REPLAN_REQUIRED",
            "commit": assignment["base_commit"],
            "changed_paths": [],
            "summary": "Stopped before editing because a required path was unclaimed.",
            "decisions": ["Preserved the clean task base."],
            "open_risks": ["A corrected immutable claim is required."],
            "spec_gaps": [
                {
                    "kind": "design",
                    "summary": "The immutable task lacks one required path boundary.",
                    "evidence": ["Preflight found an unclaimed required path."],
                    "requirement_ids": ["TI-REQ-001"],
                    "design_ids": ["TI-DES-001"],
                }
            ],
            "validation": "Preflight only; no product edits were made.",
            "end_to_end_validation": "Not run because preflight required replanning.",
            "code_review": "The clean stop preserves every activation blocker.",
            "completed_at": FIXED_TEXT,
        }
        context = started["result_context"]
        draft_path = Path(context["draft_path"])
        draft_path.write_bytes(specs.stable_json(draft))
        os.chdir(Path(context["publication_cwd"]))
        try:
            secret_draft = {
                **draft,
                "spec_gaps": [
                    {
                        "kind": "design",
                        "summary": "token=abcdefghijklmnopqrstuvwxyz123456",
                        "evidence": ["Preflight found a missing design boundary."],
                        "requirement_ids": ["TI-REQ-001"],
                        "design_ids": ["TI-DES-001"],
                    }
                ],
            }
            draft_path.write_bytes(specs.stable_json(secret_draft))
            with self.assertRaises(PromptWorkspaceError) as sensitive:
                pw.publish_task_result(
                    assignment_path,
                    draft_path,
                    Path(context["result_path"]),
                )
            self.assertEqual(sensitive.exception.code, "EXECUTION_STATE_INVALID")
            self.assertFalse(Path(context["result_path"]).exists())
            draft_path.write_bytes(specs.stable_json(draft))
            published = pw.publish_task_result(
                assignment_path,
                draft_path,
                Path(context["result_path"]),
            )
        finally:
            os.chdir(previous)

        expected = json.loads(Path(context["result_path"]).read_text(encoding="utf-8"))
        first = pw.accept_task_result(
            self.workspace, self.run_id, "task-1", clock=lambda: FIXED
        )
        replayed = pw.accept_task_result(
            self.workspace, self.run_id, "task-1", clock=lambda: FIXED
        )
        self.assertEqual(published["result_sha256"], expected["result_sha256"])
        self.assertEqual(first, expected)
        self.assertEqual(replayed, expected)
        decision = resume.plan_run_resume(
            pw.verify_workspace(self.workspace),
            self.run_id,
            clock=lambda: FIXED,
            observe_external=False,
        )
        self.assertEqual(decision["outcome"], "blocked")
        self.assertIsNone(decision["next_transition"])

    def test_unordered_result_paths_recover_exact_false_rejection(self) -> None:
        handoff = self.run_dir / "handoff.md"
        handoff.write_text(
            handoff.read_text(encoding="utf-8").replace(
                "- Write claims: exact: services/example/one.txt",
                "- Write claims:\n"
                "  - exact: services/example/one.txt\n"
                "  - exact: services/example/one-extra.txt",
            ),
            encoding="utf-8",
        )
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration/assignments/wave-001/task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        armed = pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        worktree = Path(assignment["worktree"])
        scope_cwd = Path(assignment["scope_cwd"])
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                str(armed["start_lease"]),
                session_id="unordered-result-session",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)
        (scope_cwd / "one.txt").write_text("one\n", encoding="utf-8")
        (scope_cwd / "one-extra.txt").write_text("extra\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "Write two claimed files", cwd=worktree)
        commit = git("rev-parse", "HEAD", cwd=worktree)
        result = {
            "schema": RESULT_SCHEMA,
            "run_id": self.run_id,
            "wave_id": "wave-001",
            "task_id": "task-1",
            "assignment_sha256": assignment["assignment_sha256"],
            "status": "committed",
            "commit": commit,
            "changed_paths": [
                "services/example/one.txt",
                "services/example/one-extra.txt",
            ],
            "summary": "Changed two claimed files.",
            "decisions": [],
            "open_risks": [],
            "spec_gaps": [],
            "validation": "Focused validation passed.",
            "end_to_end_validation": "Both files were observed.",
            "code_review": "No findings.",
            "completed_at": FIXED_TEXT,
        }
        result["result_sha256"] = sha256_json(result)
        result_path = Path(assignment["result_path"])
        result_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result_path.chmod(0o600)

        plane_path = self.run_dir / "orchestration/tasks/wave-001/task-1.json"
        plane = json.loads(plane_path.read_text(encoding="utf-8"))
        plane.update(
            state="failed",
            result_sha256=result["result_sha256"],
            commit=commit,
        )
        plane_path.write_text(
            json.dumps(plane, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        wave_path = self.run_dir / "orchestration/waves/wave-001.json"
        wave = json.loads(wave_path.read_text(encoding="utf-8"))
        wave["status"] = "blocked"
        wave["task_states"]["task-1"] = "failed"
        wave_path.write_text(
            json.dumps(wave, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        accepted = pw.accept_task_result(
            self.workspace, self.run_id, "task-1", clock=lambda: FIXED
        )
        self.assertEqual(accepted, result)
        recovered_wave = json.loads(wave_path.read_text(encoding="utf-8"))
        self.assertEqual(recovered_wave["status"], "running")
        self.assertEqual(recovered_wave["task_states"]["task-1"], "committed")

    def test_prefixed_completed_result_migrates_only_after_blocked_revalidation(
        self,
    ) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration/assignments/wave-001/task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        armed = pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        worktree = Path(assignment["worktree"])
        scope_cwd = Path(assignment["scope_cwd"])
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                str(armed["start_lease"]),
                session_id="legacy-completed-session",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)
        (scope_cwd / "one.txt").write_text("one\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "Write one claimed file", cwd=worktree)
        commit = git("rev-parse", "HEAD", cwd=worktree)
        result = {
            "schema": RESULT_SCHEMA,
            "run_id": self.run_id,
            "wave_id": "wave-001",
            "task_id": "task-1",
            "assignment_sha256": assignment["assignment_sha256"],
            "status": "COMPLETED",
            "commit": commit,
            "changed_paths": ["services/example/one.txt"],
            "summary": "Changed one claimed file.",
            "decisions": [],
            "open_risks": [],
            "spec_gaps": [],
            "validation": "Focused validation passed.",
            "end_to_end_validation": "The file was observed.",
            "code_review": "No findings.",
            "completed_at": FIXED_TEXT,
        }
        result["result_sha256"] = sha256_json(result)
        result_path = Path(assignment["result_path"])
        result_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result_path.chmod(0o600)

        first = pw.accept_task_result(
            self.workspace, self.run_id, "task-1", clock=lambda: FIXED
        )
        blocked_wave = json.loads(
            (self.run_dir / "orchestration/waves/wave-001.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(blocked_wave["status"], "blocked")
        self.assertEqual(blocked_wave["task_states"]["task-1"], "failed")

        migrated = pw.accept_task_result(
            self.workspace, self.run_id, "task-1", clock=lambda: FIXED
        )
        self.assertEqual(first, result)
        self.assertEqual(migrated, result)
        recovered_wave = json.loads(
            (self.run_dir / "orchestration/waves/wave-001.json").read_text(
                encoding="utf-8"
            )
        )
        recovered_plane = json.loads(
            (self.run_dir / "orchestration/tasks/wave-001/task-1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(recovered_wave["status"], "running")
        self.assertEqual(recovered_wave["task_states"]["task-1"], "committed")
        self.assertEqual(recovered_plane["state"], "committed")
        self.assertEqual(recovered_plane["commit"], commit)

    def test_replan_revalidates_current_requirements_contract(self) -> None:
        planned = pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        waves.verify_requirements_refinement_contract.reset_mock()
        replanned = pw.replan_waves(
            self.workspace,
            self.run_id,
            2,
            clock=lambda: FIXED + timedelta(seconds=1),
        )
        waves.verify_requirements_refinement_contract.assert_called_once()
        self.assertEqual(replanned["prompt_revision"], planned["prompt_revision"])
        self.assertEqual(
            replanned["prompt_intent_sha256"], planned["prompt_intent_sha256"]
        )

    def test_material_impact_rejects_unchanged_replan_identity(self) -> None:
        planned = pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        current, current_sha256 = specs.load_current_prompt_impact(
            self.run_dir, required=True
        )
        material = {
            **current,
            "revision": "r0002",
            "intent_sha256": "e" * 64,
            "effects": ["execution"],
            "plan_action": "replan_required",
        }
        with mock.patch.object(
            waves,
            "verify_requirements_refinement_contract",
            return_value={"impact": material, "impact_sha256": current_sha256},
        ):
            with self.assertRaises(PromptWorkspaceError) as caught:
                pw.replan_waves(
                    self.workspace,
                    self.run_id,
                    2,
                    clock=lambda: FIXED + timedelta(seconds=1),
                )
        self.assertEqual(caught.exception.code, "REPLAN_REQUIRED")
        persisted = waves.load_coordinator_state(self.run_dir)
        assert persisted is not None
        self.assertEqual(persisted["plan_sha256"], planned["plan_sha256"])
        self.assertEqual(persisted["prompt_revision"], planned["prompt_revision"])

    def _complete_worker(self, task_id: str, filename: str) -> dict[str, object]:
        assignment_path = (
            self.run_dir
            / "orchestration"
            / "assignments"
            / "wave-001"
            / f"{task_id}.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        self.assertEqual(assignment["worker_guardrails"], WORKER_GUARDRAILS)
        self.assertEqual(Path(assignment["workspace_manifest"]), self.workspace)
        self.assertEqual(
            Path(assignment["helper_path"]).resolve(),
            Path(pw.__file__).resolve(),
        )
        self.assertIn(
            "pass the embedded assignment_sha256 unchanged",
            assignment["worker_guardrails"],
        )
        self.assertIn(
            "never recompute it with ad hoc JSON", assignment["worker_guardrails"]
        )
        self.assertEqual(assignment["start_seconds"], WORKER_START_SECONDS)
        self.assertEqual(assignment["heartbeat_seconds"], WORKER_HEARTBEAT_SECONDS)
        liveness = worker_liveness_profile(assignment["dependencies"])
        self.assertEqual(assignment["worker_profile"], liveness["worker_profile"])
        self.assertEqual(
            assignment["read_only_warning_seconds"],
            liveness["read_only_warning_seconds"],
        )
        self.assertEqual(assignment["read_only_seconds"], liveness["read_only_seconds"])
        self.assertEqual(assignment["stall_seconds"], WORKER_STALL_SECONDS)
        self.assertEqual(assignment["max_worker_seconds"], WORKER_MAX_SECONDS)
        worktree = Path(assignment["worktree"])
        scope_cwd = Path(assignment["scope_cwd"])
        self.assertEqual(scope_cwd, worktree / "services" / "example")
        self.assertFalse((worktree / "ignored.env").exists())
        previous = Path.cwd()
        pw.arm_task(self.workspace, self.run_id, task_id, clock=lambda: FIXED)
        os.chdir(scope_cwd)
        try:
            started = pw.start_task(
                self.workspace,
                self.run_id,
                task_id,
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id=f"session-{task_id}",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)
        authorization_path = Path(str(started["commit_authorization"]))
        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        self.assertEqual(authorization["state"], "AUTHORIZED")
        self.assertEqual(authorization["owner"], "task-implementer")
        self.assertEqual(
            authorization["session_sha256"],
            hashlib.sha256(f"session-{task_id}".encode()).hexdigest(),
        )
        (scope_cwd / filename).write_text(f"{task_id}\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", f"Implement {task_id}", cwd=worktree)
        commit = git("rev-parse", "HEAD", cwd=worktree)
        result = {
            "schema": RESULT_SCHEMA,
            "run_id": self.run_id,
            "wave_id": "wave-001",
            "task_id": task_id,
            "assignment_sha256": assignment["assignment_sha256"],
            "status": "committed",
            "commit": commit,
            "changed_paths": [f"services/example/{filename}"],
            "summary": f"Implemented {task_id}",
            "decisions": [],
            "open_risks": [],
            "spec_gaps": [],
            "validation": "focused validation passed",
            "end_to_end_validation": "task behavior observed",
            "code_review": "code-review completed with no findings",
            "completed_at": "2026-07-14T12:01:00+00:00",
        }
        result["result_sha256"] = sha256_json(result)
        result_path = (
            self.run_dir / "orchestration" / "results" / "wave-001" / f"{task_id}.json"
        )
        result_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result_path.chmod(0o600)
        return pw.accept_task_result(
            self.workspace, self.run_id, task_id, clock=lambda: FIXED
        )

    def _integrated_first_wave(self) -> tuple[dict[str, object], Path, Path]:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        self._complete_worker("task-1", "one.txt")
        self._complete_worker("task-2", "two.txt")
        integrated = pw.integrate_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration = Path(str(prepared["integration_worktree"]))
        evidence = self.run_dir / "orchestration" / "evidence" / "wave-001.json"
        evidence.parent.mkdir(mode=0o700)
        evidence.write_text(
            json.dumps(
                {
                    "integration_head": git("rev-parse", "HEAD", cwd=integration),
                    "bound_revision": "r0001",
                    "steering_sha256": "none",
                    "validation": "combined tests passed",
                    "code_review": "integration review completed",
                    "steering_reconciled": True,
                }
            ),
            encoding="utf-8",
        )
        evidence.chmod(0o600)
        return integrated, integration, evidence

    def test_clean_replan_worker_can_be_superseded_by_correction(self) -> None:
        integrated, _integration, _evidence = self._integrated_first_wave()
        reviewed_head = str(integrated["integrated_head"])
        handoff = self.run_dir / "handoff.md"
        handoff.write_text(
            handoff.read_text(encoding="utf-8")
            + """

### task-4

- Status: pending
- Depends on: task-1
- Write claims: exact: services/example/one.txt
- Conflict domains: files:one.txt
- Implementation steps: stop cleanly when the immutable scope is incomplete
- Validation: prove no product edit was made
- End-to-end validation: preserve the reviewed integration
- Done criteria: publish a truthful terminal replan result
""",
            encoding="utf-8",
        )
        handoff.chmod(0o600)
        pw.replan_waves(
            self.workspace, self.run_id, 2, clock=lambda: FIXED + timedelta(seconds=1)
        )
        pw.prepare_wave(
            self.workspace, self.run_id, clock=lambda: FIXED + timedelta(seconds=2)
        )
        dispatched = pw.dispatch_wave(
            self.workspace,
            self.run_id,
            reviewed_head,
            clock=lambda: FIXED + timedelta(seconds=3),
        )
        assignment_path = Path(dispatched["assignments"][0])
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        self.assertEqual(assignment["task_id"], "task-4")
        armed = pw.arm_task(
            self.workspace,
            self.run_id,
            "task-4",
            clock=lambda: FIXED + timedelta(seconds=4),
        )
        previous = Path.cwd()
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            started = pw.start_task(
                self.workspace,
                self.run_id,
                "task-4",
                assignment["assignment_sha256"],
                str(armed["start_lease"]),
                session_id="clean-replan-session",
                clock=lambda: FIXED + timedelta(seconds=5),
            )
        finally:
            os.chdir(previous)
        draft = {
            "schema": RESULT_SCHEMA,
            "run_id": self.run_id,
            "wave_id": "wave-001",
            "task_id": "task-4",
            "assignment_sha256": assignment["assignment_sha256"],
            "status": "REPLAN_REQUIRED",
            "commit": assignment["base_commit"],
            "changed_paths": [],
            "summary": "Stopped before editing because a required path was unclaimed.",
            "decisions": ["Preserved the reviewed integration exactly."],
            "open_risks": ["A corrected immutable claim is required."],
            "spec_gaps": [],
            "validation": "Preflight only; no product edits were made.",
            "end_to_end_validation": "The reviewed integration stayed unchanged.",
            "code_review": "The clean stop preserved every activation blocker.",
            "completed_at": FIXED_TEXT,
        }
        context = started["result_context"]
        draft_path = Path(context["draft_path"])
        draft_path.write_bytes(specs.stable_json(draft))
        os.chdir(Path(context["publication_cwd"]))
        try:
            pw.publish_task_result(
                assignment_path,
                draft_path,
                Path(context["result_path"]),
            )
        finally:
            os.chdir(previous)
        pw.accept_task_result(
            self.workspace,
            self.run_id,
            "task-4",
            clock=lambda: FIXED + timedelta(seconds=6),
        )

        handoff.write_text(
            handoff.read_text(encoding="utf-8")
            + """

### task-5

- Status: pending
- Depends on: task-1
- Write claims: exact: services/example/one.txt
- Conflict domains: files:one.txt
- Implementation steps: implement the corrected immutable scope
- Validation: inspect the corrected output
- End-to-end validation: verify the corrected retained integration
- Done criteria: one.txt contains the correction
""",
            encoding="utf-8",
        )
        handoff.chmod(0o600)
        replanned = pw.replan_waves(
            self.workspace, self.run_id, 2, clock=lambda: FIXED + timedelta(seconds=7)
        )
        self.assertEqual(replanned["active_wave"], "wave-001")
        plane = json.loads(
            (self.run_dir / "orchestration/tasks/wave-001/task-4.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(plane["state"], "superseded")
        wave = json.loads(
            (self.run_dir / "orchestration/waves/wave-001.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(wave["task_states"]["task-4"], "superseded")
        self.assertEqual(wave["task_states"]["task-5"], "planned")

        prepared = pw.prepare_wave(
            self.workspace, self.run_id, clock=lambda: FIXED + timedelta(seconds=8)
        )
        dispatched = pw.dispatch_wave(
            self.workspace,
            self.run_id,
            str(prepared["contract_commit"]),
            clock=lambda: FIXED + timedelta(seconds=9),
        )
        self.assertFalse(Path(assignment["worktree"]).exists())
        self.assertEqual(len(dispatched["assignments"]), 1)
        correction = json.loads(
            Path(dispatched["assignments"][0]).read_text(encoding="utf-8")
        )
        self.assertEqual(correction["task_id"], "task-5")
        self.assertEqual(correction["base_commit"], reviewed_head)

    def test_repeated_resume_after_merged_tasks_keeps_handoff_parseable(self) -> None:
        self._integrated_first_wave()
        first = resume.resume_run(
            self.workspace, self.run_id, clock=lambda: FIXED + timedelta(seconds=1)
        )
        self.assertEqual(
            (first["outcome"], first["next_transition"]),
            ("execute", "wave-promote"),
        )
        statuses = task_statuses(
            task_sections((self.run_dir / "handoff.md").read_text(encoding="utf-8"))[1]
        )
        self.assertEqual(statuses["task-1"], "in_progress")
        self.assertEqual(statuses["task-2"], "in_progress")
        second = resume.resume_run(
            self.workspace, self.run_id, clock=lambda: FIXED + timedelta(seconds=2)
        )
        self.assertEqual(second["next_transition"], "wave-promote")
        self.assertEqual(first["state_sha256"], second["state_sha256"])

    def test_dispatch_does_not_require_project_agent_state(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        dispatched = pw.dispatch_wave(
            self.workspace, self.run_id, self.initial, clock=lambda: FIXED
        )
        self.assertEqual(len(dispatched["assignments"]), 1)

    def test_coordinator_commit_is_single_claim_bound_and_replay_safe(self) -> None:
        integrated, integration, _evidence = self._integrated_first_wave()
        requirements = integration / "services/example/docs/requirements.md"
        requirements.write_text(
            requirements.read_text(encoding="utf-8")
            + "\n<!-- reconciled after worker integration -->\n",
            encoding="utf-8",
        )

        committed = pw.commit_coordinator_delta(
            self.workspace, self.run_id, clock=lambda: FIXED
        )

        self.assertEqual(committed["status"], "committed")
        self.assertEqual(
            committed["changed_paths"],
            ["services/example/docs/requirements.md"],
        )
        self.assertEqual(
            git("rev-parse", f"{committed['commit']}^", cwd=integration),
            integrated["integrated_head"],
        )
        self.assertEqual(git("status", "--short", cwd=integration), "")
        replayed = pw.commit_coordinator_delta(
            self.workspace, self.run_id, clock=lambda: FIXED
        )
        self.assertEqual(replayed["status"], "reused")
        self.assertEqual(replayed["commit"], committed["commit"])

        (integration / "services/example/one.txt").write_text(
            "unsafe product edit\n", encoding="utf-8"
        )
        with self.assertRaises(PromptWorkspaceError) as caught:
            pw.commit_coordinator_delta(
                self.workspace, self.run_id, clock=lambda: FIXED
            )
        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")

    def test_prepared_contract_stage_and_commit_are_owner_bound(self) -> None:
        integration, project = self._prepare_contract_edit()
        for name in ("requirements.md", "design.md"):
            path = project / "docs" / name
            path.write_bytes(path.read_bytes() + b"\n<!-- correction contract -->\n")
        staged = pw.stage_coordinator_contract(
            self.workspace, self.run_id, clock=lambda: FIXED
        )
        expected = [
            "services/example/docs/design.md",
            "services/example/docs/requirements.md",
        ]
        self.assertEqual(staged, {"status": "staged", "staged_paths": expected})
        self.assertEqual(
            sorted(
                git(
                    "diff",
                    "--cached",
                    "--name-only",
                    cwd=integration,
                ).splitlines()
            ),
            expected,
        )
        committed = pw.commit_coordinator_delta(
            self.workspace, self.run_id, clock=lambda: FIXED
        )
        self.assertEqual(committed["status"], "committed")
        self.assertEqual(committed["changed_paths"], expected)
        self.assertEqual(
            git("rev-parse", f"{committed['commit']}^", cwd=integration),
            self.initial,
        )
        self.assertEqual(git("status", "--short", cwd=integration), "")
        replayed = pw.commit_coordinator_delta(
            self.workspace, self.run_id, clock=lambda: FIXED
        )
        self.assertEqual(replayed["status"], "reused")
        self.assertEqual(replayed["commit"], committed["commit"])

    def test_prepared_contract_stage_rejects_partial_spec_delta(self) -> None:
        _integration, project = self._prepare_contract_edit()
        requirements = project / "docs" / "requirements.md"
        requirements.write_bytes(requirements.read_bytes() + b"\npartial\n")
        with self.assertRaises(pw.PromptWorkspaceError) as caught:
            pw.stage_coordinator_contract(
                self.workspace, self.run_id, clock=lambda: FIXED
            )
        self.assertEqual(caught.exception.code, "WORKTREE_CONFLICT")

    def test_full_repository_worktrees_ordered_merge_ff_promotion_and_cleanup(
        self,
    ) -> None:
        plan = pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        self.assertNotEqual(plan["base_branch"], plan["default_branch"])
        self.assertEqual(plan["promotion_source"], "managed-local")
        self.assertEqual(
            git("branch", "--show-current", cwd=self.repo), plan["base_branch"]
        )
        self.assertEqual(
            [item["batches"] for item in plan["waves"]],
            [[["task-1"], ["task-2"]], [["task-3"]]],
        )
        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration = Path(str(prepared["integration_worktree"]))
        self.assertEqual(git("rev-parse", "HEAD", cwd=integration), self.initial)
        dispatched = pw.dispatch_wave(
            self.workspace, self.run_id, self.initial, clock=lambda: FIXED
        )
        self.assertEqual(len(dispatched["assignments"]), 1)
        first_handoff = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "incoming-handoffs"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(first_handoff["dependencies"], [])
        self.assertEqual(first_handoff["predecessors"], [])
        task_one_result = self._complete_worker("task-1", "one.txt")
        next_batch = pw.advance_batch(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(len(next_batch["assignments"]), 1)
        second_handoff = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "incoming-handoffs"
                / "wave-001"
                / "task-2.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            [item["task_id"] for item in second_handoff["predecessors"]],
            ["task-1"],
        )
        second_assignment = json.loads(
            Path(next_batch["assignments"][0]).read_text(encoding="utf-8")
        )
        pw.arm_task(self.workspace, self.run_id, "task-2", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(Path(second_assignment["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as reused:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-2",
                    second_assignment["assignment_sha256"],
                    FIXED_TEXT,
                    session_id="session-task-1",
                    clock=lambda: FIXED,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(reused.exception.code, "FRESH_SESSION_REQUIRED")
        self._complete_worker("task-2", "two.txt")
        self.assertEqual(
            pw.accept_task_result(
                self.workspace, self.run_id, "task-1", clock=lambda: FIXED
            ),
            task_one_result,
        )
        integrated = pw.integrate_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration_tip = git("rev-parse", "HEAD", cwd=integration)
        self.assertEqual(integrated["status"], "promotion_pending")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)
        first_parent = git(
            "rev-list",
            "--first-parent",
            "--reverse",
            f"{self.initial}..{integration_tip}",
            cwd=integration,
        ).splitlines()
        merge_commits = [
            commit
            for commit in first_parent
            if len(
                git("rev-list", "--parents", "-n", "1", commit, cwd=integration).split()
            )
            == 3
        ]
        task_commits = [
            json.loads(
                (
                    self.run_dir
                    / "orchestration"
                    / "tasks"
                    / "wave-001"
                    / f"task-{number}.json"
                ).read_text(encoding="utf-8")
            )["commit"]
            for number in (1, 2)
        ]
        self.assertEqual(
            [
                git("rev-parse", f"{commit}^2", cwd=integration)
                for commit in merge_commits
            ],
            task_commits,
        )
        evidence = self.run_dir / "orchestration" / "evidence" / "wave-001.json"
        evidence.parent.mkdir(mode=0o700)
        evidence.write_text(
            json.dumps(
                {
                    "integration_head": integration_tip,
                    "bound_revision": "r0001",
                    "steering_sha256": "none",
                    "validation": "combined tests passed",
                    "code_review": "integration review completed",
                    "steering_reconciled": True,
                }
            ),
            encoding="utf-8",
        )
        evidence.chmod(0o600)
        git("merge", "--ff-only", str(integrated["integration_branch"]), cwd=self.repo)
        dirty = self.repo / "dirty.txt"
        dirty.write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(PromptWorkspaceError) as dirty_promotion:
            pw.promote_wave(self.workspace, self.run_id, evidence, clock=lambda: FIXED)
        self.assertEqual(dirty_promotion.exception.code, "PROMOTION_BLOCKED")
        dirty.unlink()
        task_one_assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        cleanup_dirty = Path(task_one_assignment["worktree"]) / "cleanup-dirty.txt"
        cleanup_dirty.write_text("retain\n", encoding="utf-8")
        with self.assertRaises(PromptWorkspaceError) as retained_worker:
            pw.promote_wave(self.workspace, self.run_id, evidence, clock=lambda: FIXED)
        self.assertEqual(retained_worker.exception.code, "CLEANUP_BLOCKED")
        cleanup_dirty.unlink()
        promoted = pw.promote_wave(
            self.workspace, self.run_id, evidence, clock=lambda: FIXED
        )
        self.assertEqual(promoted["promoted_head"], integration_tip)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), integration_tip)
        self.assertEqual(
            (self.scope / "one.txt").read_text(encoding="utf-8"), "task-1\n"
        )
        self.assertEqual(
            (self.scope / "two.txt").read_text(encoding="utf-8"), "task-2\n"
        )
        cleaned = pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(cleaned["status"], "done")
        worktrees = git("worktree", "list", "--porcelain", cwd=self.repo)
        self.assertEqual(worktrees.count("worktree "), 2)
        self.assertNotIn(
            "codex/ti-", git("branch", "--format=%(refname:short)", cwd=self.repo)
        )
        coordinator = json.loads(
            (self.run_dir / "orchestration" / "coordinator.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(coordinator["active_wave"], "wave-002")
        coordinator["active_wave"] = "wave-001"
        coordinator_path = self.run_dir / "orchestration" / "coordinator.json"
        coordinator_path.write_text(
            json.dumps(coordinator, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        coordinator_path.chmod(0o600)
        pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        repaired = json.loads(coordinator_path.read_text(encoding="utf-8"))
        self.assertEqual(repaired["active_wave"], "wave-002")
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(
            self.workspace, self.run_id, integration_tip, clock=lambda: FIXED
        )
        dependent_handoff = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "incoming-handoffs"
                / "wave-002"
                / "task-3.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(dependent_handoff["dependencies"], ["task-1", "task-2"])
        self.assertEqual(
            [item["task_id"] for item in dependent_handoff["predecessors"]],
            ["task-1", "task-2"],
        )
        integration_assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-002"
                / "task-3.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(integration_assignment["worker_profile"], "integration")
        self.assertEqual(
            integration_assignment["read_only_warning_seconds"],
            WORKER_INTEGRATION_WARNING_SECONDS,
        )
        self.assertEqual(
            integration_assignment["read_only_seconds"],
            WORKER_INTEGRATION_READ_ONLY_SECONDS,
        )
        integration_scope = Path(integration_assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-3", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(integration_scope)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-3",
                integration_assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="integration-boundary-worker",
                clock=lambda: FIXED,
            )

            for elapsed in range(30, 331, 30):
                pw.heartbeat_task(
                    self.workspace,
                    self.run_id,
                    "task-3",
                    integration_assignment["assignment_sha256"],
                    "implementing",
                    session_id="integration-boundary-worker",
                    clock=lambda elapsed=elapsed: FIXED + timedelta(seconds=elapsed),
                )
        finally:
            os.chdir(previous)
        integration_warning = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-3",
            clock=lambda: FIXED + timedelta(seconds=360),
        )
        self.assertEqual(integration_warning["status"], "ACTIVE")
        self.assertEqual(integration_warning["warning"], "READ_ONLY_DEADLINE_NEAR")
        integration_timeout = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-3",
            clock=lambda: FIXED + timedelta(seconds=420),
        )
        self.assertEqual(integration_timeout["status"], "WORKER_READ_ONLY_TIMEOUT")
        self.assertFalse(integration_timeout["progress_observed"])
        journal = self.run_dir / "orchestration" / "journals" / "wave-001.jsonl"
        for line in journal.read_text(encoding="utf-8").splitlines():
            self.assertIsInstance(json.loads(line), dict)

    def test_remote_default_head_drift_does_not_affect_lane_promotion(
        self,
    ) -> None:
        _integrated, integration, evidence = self._integrated_first_wave()
        integration_tip = git("rev-parse", "HEAD", cwd=integration)
        tree = git("rev-parse", "HEAD^{tree}", cwd=self.repo)
        default_head = git("rev-parse", "HEAD", cwd=self.origin)
        advanced = git(
            "commit-tree",
            tree,
            "-p",
            default_head,
            "-m",
            "advance remote default",
            cwd=self.repo,
        )
        git(
            "push",
            "-q",
            "origin",
            f"{advanced}:refs/heads/{self.default_branch}",
            cwd=self.repo,
        )
        promoted = pw.promote_wave(
            self.workspace, self.run_id, evidence, clock=lambda: FIXED
        )
        self.assertEqual(promoted["promoted_head"], integration_tip)

    def test_remote_default_advance_during_cleanup_does_not_affect_lane(self) -> None:
        _integrated, integration, evidence = self._integrated_first_wave()
        integration_tip = git("rev-parse", "HEAD", cwd=integration)
        original_cleanup = waves._cleanup_resource
        advanced = False

        def cleanup_then_advance(**kwargs: object) -> bool:
            nonlocal advanced
            cleaned = original_cleanup(**kwargs)
            if not advanced:
                tree = git("rev-parse", "HEAD^{tree}", cwd=self.repo)
                default_head = git("rev-parse", "HEAD", cwd=self.origin)
                remote_tip = git(
                    "commit-tree",
                    tree,
                    "-p",
                    default_head,
                    "-m",
                    "advance default during cleanup",
                    cwd=self.repo,
                )
                git(
                    "push",
                    "-q",
                    "origin",
                    f"{remote_tip}:refs/heads/{self.default_branch}",
                    cwd=self.repo,
                )
                advanced = True
            return cleaned

        with mock.patch.object(
            waves, "_cleanup_resource", side_effect=cleanup_then_advance
        ):
            promoted = pw.promote_wave(
                self.workspace, self.run_id, evidence, clock=lambda: FIXED
            )
        self.assertTrue(advanced)
        self.assertEqual(promoted["promoted_head"], integration_tip)

    def test_branch_advance_race_retains_worker_ref_and_blocks_promotion(self) -> None:
        _integrated, _integration, evidence = self._integrated_first_wave()
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        branch = str(assignment["branch"])
        ref = f"refs/heads/{branch}"
        expected_tip = git("rev-parse", ref, cwd=self.repo)
        original_journaled_git = waves._journaled_git
        advanced_tip: str | None = None

        def race_before_delete(
            journal: Path,
            repo: Path,
            arguments: list[str],
            description: str,
            clock: Callable[[], datetime],
            *,
            check: bool = True,
        ) -> subprocess.CompletedProcess[bytes]:
            nonlocal advanced_tip
            if arguments[:3] == ["update-ref", "-d", ref] and advanced_tip is None:
                tree = git("rev-parse", f"{expected_tip}^{{tree}}", cwd=self.repo)
                advanced_tip = git(
                    "commit-tree",
                    tree,
                    "-p",
                    expected_tip,
                    "-m",
                    "advance worker during cleanup",
                    cwd=self.repo,
                )
                git("update-ref", ref, advanced_tip, expected_tip, cwd=self.repo)
            return original_journaled_git(
                journal,
                repo,
                arguments,
                description,
                clock,
                check=check,
            )

        with mock.patch.object(waves, "_journaled_git", side_effect=race_before_delete):
            with self.assertRaises(PromptWorkspaceError) as raised:
                pw.promote_wave(
                    self.workspace, self.run_id, evidence, clock=lambda: FIXED
                )
        self.assertEqual(raised.exception.code, "CLEANUP_BLOCKED")
        self.assertIsNotNone(advanced_tip)
        self.assertEqual(git("rev-parse", ref, cwd=self.repo), advanced_tip)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)

    def test_scope_expansion_fails_replan_and_retains_recovery_resources(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration" / "assignments" / "wave-001" / "task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        worktree = Path(assignment["worktree"])
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="scope-expansion-worker",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)
        (scope_cwd / "one.txt").write_text("task-1\n", encoding="utf-8")
        (scope_cwd / "two.txt").write_text("undeclared\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "Expand task-1 scope", cwd=worktree)
        commit = git("rev-parse", "HEAD", cwd=worktree)
        result = {
            "schema": RESULT_SCHEMA,
            "run_id": self.run_id,
            "wave_id": "wave-001",
            "task_id": "task-1",
            "assignment_sha256": assignment["assignment_sha256"],
            "status": "committed",
            "commit": commit,
            "changed_paths": [
                "services/example/one.txt",
                "services/example/two.txt",
            ],
            "summary": "Expanded task scope",
            "decisions": [],
            "open_risks": ["scope exceeded"],
            "spec_gaps": [],
            "validation": "focused validation passed",
            "end_to_end_validation": "behavior observed",
            "code_review": "review completed",
            "completed_at": "2026-07-14T12:01:00+00:00",
        }
        result["result_sha256"] = sha256_json(result)
        result_path = Path(assignment["result_path"])
        result_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result_path.chmod(0o600)
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.accept_task_result(
                self.workspace, self.run_id, "task-1", clock=lambda: FIXED
            )
        self.assertEqual(raised.exception.code, "WORKER_SCOPE_VIOLATION")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)
        self.assertTrue(worktree.is_dir())
        task_two = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-2.json"
            ).read_text(encoding="utf-8")
        )
        previous = Path.cwd()
        os.chdir(Path(task_two["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as blocked_start:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-2",
                    task_two["assignment_sha256"],
                    FIXED_TEXT,
                    session_id="late-batch-worker",
                    clock=lambda: FIXED,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(blocked_start.exception.code, "EXECUTION_STATE_INVALID")
        with self.assertRaises(PromptWorkspaceError) as replan_raised:
            pw.replan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.assertEqual(replan_raised.exception.code, "STEERING_QUEUED_AFTER_WAVE")

    def test_worker_heartbeat_and_watch_enforce_no_progress_budgets(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration" / "assignments" / "wave-001" / "task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        self.assertEqual(assignment["worker_profile"], "standard")
        self.assertEqual(
            assignment["read_only_warning_seconds"],
            WORKER_STANDARD_WARNING_SECONDS,
        )
        self.assertEqual(
            assignment["read_only_seconds"], WORKER_STANDARD_READ_ONLY_SECONDS
        )
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="heartbeat-worker",
                clock=lambda: FIXED,
            )
            heartbeat = pw.heartbeat_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                "preflight",
                session_id="heartbeat-worker",
                clock=lambda: FIXED + timedelta(seconds=30),
            )
            self.assertEqual(heartbeat["status"], "ACTIVE")
            self.assertEqual(heartbeat["heartbeat_sequence"], 2)
            for elapsed in range(60, 211, 30):
                pw.heartbeat_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    "preflight",
                    session_id="heartbeat-worker",
                    clock=lambda elapsed=elapsed: FIXED + timedelta(seconds=elapsed),
                )
            plane_path = (
                self.run_dir / "orchestration" / "tasks" / "wave-001" / "task-1.json"
            )
            before_replay = json.loads(plane_path.read_text(encoding="utf-8"))
            with self.assertRaises(PromptWorkspaceError) as replay:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    FIXED_TEXT,
                    session_id="heartbeat-worker",
                    clock=lambda: FIXED + timedelta(seconds=45),
                )
            self.assertEqual(replay.exception.code, "EXECUTION_STATE_INVALID")
            self.assertEqual(
                json.loads(plane_path.read_text(encoding="utf-8")), before_replay
            )
        finally:
            os.chdir(previous)
        warning = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=240),
        )
        self.assertEqual(warning["status"], "ACTIVE")
        self.assertEqual(warning["warning"], "READ_ONLY_DEADLINE_NEAR")
        watched = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=300),
        )
        self.assertEqual(watched["status"], "WORKER_READ_ONLY_TIMEOUT")
        self.assertFalse(watched["progress_observed"])

    def test_started_assignment_keeps_accepted_guardrails_after_source_growth(
        self,
    ) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        expanded_guardrails = (
            f"{WORKER_GUARDRAILS} Use the current transient recovery context."
        )
        with (
            mock.patch.object(waves, "WORKER_GUARDRAILS", expanded_guardrails),
            self.assertRaises(PromptWorkspaceError) as unstarted,
        ):
            pw.watch_task(
                self.workspace,
                self.run_id,
                "task-1",
                clock=lambda: FIXED,
            )
        self.assertEqual(unstarted.exception.code, "EXECUTION_STATE_INVALID")

        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="accepted-guardrails-worker",
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)

        with mock.patch.object(waves, "WORKER_GUARDRAILS", expanded_guardrails):
            watched = pw.watch_task(
                self.workspace,
                self.run_id,
                "task-1",
                clock=lambda: FIXED + timedelta(seconds=30),
            )
        self.assertEqual(watched["status"], "ACTIVE")
        self.assertEqual(watched["heartbeat_age_seconds"], 30)

    def test_terminal_assignment_keeps_recorded_helper_after_source_relocation(
        self,
    ) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        coordinator = waves.load_coordinator_state(self.run_dir)
        assert coordinator is not None
        wave = waves._load_wave(self.run_dir, "wave-001")
        assignment = waves._validated_assignment(
            waves._assignment_path(self.run_dir, "wave-001", "task-1")
        )
        relocated_source = self.root / "relocated-source" / "prompt_workspace_waves.py"

        with (
            mock.patch.object(waves, "__file__", str(relocated_source)),
            self.assertRaises(PromptWorkspaceError) as active,
        ):
            waves._validate_assignment_context(
                assignment,
                json.loads(self.workspace.read_text(encoding="utf-8")),
                coordinator,
                self.run_dir,
                wave,
                "task-1",
            )
        self.assertEqual(active.exception.code, "EXECUTION_STATE_INVALID")

        self._complete_worker("task-1", "one.txt")
        wave = waves._load_wave(self.run_dir, "wave-001")
        with mock.patch.object(waves, "__file__", str(relocated_source)):
            waves._validate_assignment_context(
                assignment,
                json.loads(self.workspace.read_text(encoding="utf-8")),
                coordinator,
                self.run_dir,
                wave,
                "task-1",
            )

    def test_worker_watch_bounds_dispatch_to_task_start(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        previous = Path.cwd()
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as not_armed:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    FIXED_TEXT,
                    session_id="premature-worker",
                    clock=lambda: FIXED,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(not_armed.exception.code, "TASK_NOT_ARMED")
        queued = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=600),
        )
        self.assertEqual(queued["status"], "QUEUED")
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        pending = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=59),
        )
        self.assertEqual(pending["status"], "PENDING_START")
        timed_out = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=60),
        )
        self.assertEqual(timed_out["status"], "WORKER_PRESTART_TIMEOUT")
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as expired:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    FIXED_TEXT,
                    session_id="expired-worker",
                    clock=lambda: FIXED + timedelta(seconds=60),
                )
        finally:
            os.chdir(previous)
        self.assertEqual(expired.exception.code, "WORKER_PRESTART_TIMEOUT")

    def test_expired_clean_retained_v7_assignment_rearms_in_place(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration" / "assignments" / "wave-001" / "task-1.json"
        )
        assignment_bytes = assignment_path.read_bytes()
        assignment = json.loads(assignment_bytes)
        self.assertIn(
            "embedded assignment_sha256 unchanged",
            str(assignment["worker_guardrails"]),
        )
        worktree = Path(assignment["worktree"])
        resource_snapshot = waves._registered_worktrees(self.repo)
        armed = pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        start_context = armed["start_context"]
        self.assertEqual(
            (start_context["assignment_path"], start_context["scope_cwd"]),
            (str(assignment_path), assignment["scope_cwd"]),
        )
        self.assertEqual(
            start_context["start_argv"][-2:],
            [str(armed["start_lease"]), "--json"],
        )
        old_lease = str(armed["start_lease"])

        timed_out = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
        )
        self.assertEqual(timed_out["status"], "WORKER_PRESTART_TIMEOUT")
        self.assertEqual(timed_out["dispatched_at"], old_lease)

        rearmed = pw.rearm_task(
            self.workspace,
            self.run_id,
            "task-1",
            old_lease,
            confirmed_stopped=True,
            clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
        )
        new_lease = str(rearmed["start_lease"])
        self.assertEqual(rearmed["start_context"]["scope_cwd"], assignment["scope_cwd"])
        self.assertEqual(rearmed["start_context"]["start_argv"][-2], new_lease)
        resume_control = self._write_resume_intent(
            "task-rearm",
            {
                "task_id": "task-1",
                "expected_start_lease": old_lease,
                "confirmed_stopped": True,
            },
        )
        replayed = pw.rearm_task(
            self.workspace,
            self.run_id,
            "task-1",
            old_lease,
            confirmed_stopped=True,
            clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS + 1),
        )
        self.assertEqual(replayed["start_lease"], new_lease)
        resume_control.unlink()

        previous = Path.cwd()
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as stale_start:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    old_lease,
                    session_id="expired-worker",
                    clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
                )
            started = pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                new_lease,
                session_id="replacement-prestart-worker",
                clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
            )
            heartbeat = pw.heartbeat_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                "implementing",
                session_id="replacement-prestart-worker",
                clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS + 1),
            )
        finally:
            os.chdir(previous)

        self.assertEqual(rearmed["status"], "REARMED")
        self.assertNotEqual(new_lease, old_lease)
        self.assertEqual(
            started["assignment"]["assignment_sha256"],
            assignment["assignment_sha256"],
        )
        self.assertEqual(heartbeat["status"], "ACTIVE")
        self.assertEqual(heartbeat["heartbeat_sequence"], 2)
        self.assertEqual(heartbeat["heartbeat_phase"], "implementing")
        self.assertEqual(stale_start.exception.code, "WORKER_START_LEASE_INVALID")
        plane = json.loads(
            (
                self.run_dir / "orchestration" / "tasks" / "wave-001" / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        wave = json.loads(
            (self.run_dir / "orchestration" / "waves" / "wave-001.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(plane["state"], "running")
        self.assertEqual(wave["task_states"]["task-1"], "running")
        self.assertEqual(len(plane["worker_session_sha256_history"]), 1)
        self.assertEqual(plane["started_at"], plane["dispatched_at"])
        self.assertNotEqual(plane["last_heartbeat_at"], plane["started_at"])
        self.assertEqual(assignment_path.read_bytes(), assignment_bytes)
        self.assertEqual(waves._registered_worktrees(self.repo), resource_snapshot)
        self.assertEqual(waves._head(worktree), assignment["base_commit"])
        self.assertTrue(waves._clean(worktree))

    def test_prestart_rearm_rejects_active_or_mutated_worker(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        scope_cwd = Path(assignment["scope_cwd"])
        second_assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-2.json"
            ).read_text(encoding="utf-8")
        )
        second_scope_cwd = Path(second_assignment["scope_cwd"])
        armed = pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        lease = str(armed["start_lease"])
        with self.assertRaises(PromptWorkspaceError) as unconfirmed:
            pw.rearm_task(
                self.workspace,
                self.run_id,
                "task-1",
                lease,
                confirmed_stopped=False,
                clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
            )
        self.assertEqual(unconfirmed.exception.code, "RECOVERY_CONFIRMATION_REQUIRED")
        with self.assertRaises(PromptWorkspaceError) as active:
            pw.rearm_task(
                self.workspace,
                self.run_id,
                "task-1",
                lease,
                confirmed_stopped=True,
                clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS - 1),
            )
        self.assertEqual(active.exception.code, "WORKER_PRESTART_ACTIVE")
        second_armed = pw.arm_task(
            self.workspace, self.run_id, "task-2", clock=lambda: FIXED
        )
        waves._git(
            second_scope_cwd,
            ["commit", "--allow-empty", "-m", "empty prestart protocol violation"],
            "commit empty prestart mutation",
        )
        empty_watch = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-2",
            clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
        )
        self.assertEqual(empty_watch["status"], "WORKER_PRESTART_MUTATION")
        self.assertTrue(empty_watch["progress_observed"])
        with self.assertRaises(PromptWorkspaceError) as empty_commit:
            pw.rearm_task(
                self.workspace,
                self.run_id,
                "task-2",
                str(second_armed["start_lease"]),
                confirmed_stopped=True,
                clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
            )
        self.assertEqual(empty_commit.exception.code, "WORKER_PRESTART_MUTATION")
        (scope_cwd / "one.txt").write_text(
            "unauthorized prestart edit\n", encoding="utf-8"
        )
        with self.assertRaises(PromptWorkspaceError) as mutated:
            pw.rearm_task(
                self.workspace,
                self.run_id,
                "task-1",
                lease,
                confirmed_stopped=True,
                clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
            )
        self.assertEqual(mutated.exception.code, "WORKER_PRESTART_MUTATION")
        waves._git(scope_cwd, ["add", "one.txt"], "stage prestart mutation")
        waves._git(
            scope_cwd,
            ["commit", "-m", "prestart protocol violation"],
            "commit prestart mutation",
        )
        with self.assertRaises(PromptWorkspaceError) as committed:
            pw.rearm_task(
                self.workspace,
                self.run_id,
                "task-1",
                lease,
                confirmed_stopped=True,
                clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
            )
        self.assertEqual(committed.exception.code, "WORKER_PRESTART_MUTATION")
        plane = json.loads(
            (
                self.run_dir / "orchestration" / "tasks" / "wave-001" / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(plane["state"], "assigned")
        self.assertIsNone(plane["worker_session_sha256"])

    def test_prestart_rearm_is_strict_compare_and_swap(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        armed = pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        old_lease = str(armed["start_lease"])
        rearmed = pw.rearm_task(
            self.workspace,
            self.run_id,
            "task-1",
            old_lease,
            confirmed_stopped=True,
            clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
        )
        with self.assertRaises(PromptWorkspaceError) as obsolete:
            pw.rearm_task(
                self.workspace,
                self.run_id,
                "task-1",
                old_lease,
                confirmed_stopped=True,
                clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
            )
        with self.assertRaises(PromptWorkspaceError) as conflict:
            pw.rearm_task(
                self.workspace,
                self.run_id,
                "task-1",
                (FIXED + timedelta(seconds=WORKER_START_SECONDS + 1)).isoformat(
                    timespec="seconds"
                ),
                confirmed_stopped=True,
                clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
            )

        self.assertEqual(rearmed["status"], "REARMED")
        self.assertEqual(obsolete.exception.code, "WORKER_START_LEASE_CONFLICT")
        self.assertEqual(conflict.exception.code, "WORKER_START_LEASE_CONFLICT")
        watched = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=WORKER_START_SECONDS),
        )
        self.assertEqual(watched["status"], "PENDING_START")
        self.assertEqual(watched["dispatched_at"], rearmed["start_lease"])
        plane = json.loads(
            (
                self.run_dir / "orchestration" / "tasks" / "wave-001" / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(plane["state"], "assigned")
        self.assertEqual(plane["dispatched_at"], rearmed["start_lease"])
        self.assertEqual(plane["worker_session_sha256_history"], [])

    def test_worker_watch_detects_stale_heartbeat_after_file_progress(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="stale-worker",
                clock=lambda: FIXED,
            )
            (scope_cwd / "one.txt").write_text("progress\n", encoding="utf-8")
        finally:
            os.chdir(previous)
        watched = pw.watch_task(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=240),
        )
        self.assertEqual(watched["status"], "WORKER_STALLED")
        self.assertTrue(watched["progress_observed"])

    def test_out_of_claim_mutation_is_not_worker_progress(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="scope-violation-worker",
                clock=lambda: FIXED,
            )
            (scope_cwd / "two.txt").write_text("undeclared\n", encoding="utf-8")
            watched = pw.watch_task(
                self.workspace,
                self.run_id,
                "task-1",
                clock=lambda: FIXED + timedelta(seconds=30),
            )
            self.assertEqual(watched["status"], "WORKER_SCOPE_VIOLATION")
            self.assertFalse(watched["progress_observed"])
            self.assertTrue(watched["scope_violation"])
            with self.assertRaises(PromptWorkspaceError) as heartbeat:
                pw.heartbeat_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    "implementing",
                    session_id="scope-violation-worker",
                    clock=lambda: FIXED + timedelta(seconds=30),
                )
            self.assertEqual(heartbeat.exception.code, "WORKER_SCOPE_VIOLATION")
        finally:
            os.chdir(previous)

    def test_broad_worker_claim_still_excludes_coordinator_owned_paths(self) -> None:
        handoff_path = self.run_dir / "handoff.md"
        handoff_path.write_text(
            handoff_path.read_text(encoding="utf-8").replace(
                "- Write claims: exact: services/example/one.txt",
                "- Write claims: prefix: services/example",
                1,
            ),
            encoding="utf-8",
        )
        handoff_path.chmod(0o600)
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="coordinator-path-worker",
                clock=lambda: FIXED,
            )
            design = scope_cwd / "docs" / "design.md"
            design.write_text(
                design.read_text(encoding="utf-8") + "\nworker edit\n",
                encoding="utf-8",
            )
            watched = pw.watch_task(
                self.workspace,
                self.run_id,
                "task-1",
                clock=lambda: FIXED + timedelta(seconds=1),
            )
            self.assertEqual(watched["status"], "WORKER_SCOPE_VIOLATION")
            self.assertEqual(
                watched["scope_violation_paths"],
                ["services/example/docs/design.md"],
            )
            with self.assertRaises(PromptWorkspaceError) as heartbeat:
                pw.heartbeat_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    "implementing",
                    session_id="coordinator-path-worker",
                    clock=lambda: FIXED + timedelta(seconds=1),
                )
            self.assertEqual(heartbeat.exception.code, "WORKER_SCOPE_VIOLATION")
        finally:
            os.chdir(previous)

    def test_renamed_project_spec_remains_a_worker_scope_violation(self) -> None:
        handoff_path = self.run_dir / "handoff.md"
        handoff_path.write_text(
            handoff_path.read_text(encoding="utf-8").replace(
                "- Write claims: exact: services/example/one.txt",
                "- Write claims: prefix: services/example",
                1,
            ),
            encoding="utf-8",
        )
        handoff_path.chmod(0o600)
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="renamed-spec-worker",
                clock=lambda: FIXED,
            )
            (scope_cwd / "docs" / "requirements.md").rename(
                scope_cwd / "moved-requirements.md"
            )
            watched = pw.watch_task(
                self.workspace,
                self.run_id,
                "task-1",
                clock=lambda: FIXED + timedelta(seconds=1),
            )
            self.assertEqual(watched["status"], "WORKER_SCOPE_VIOLATION")
            self.assertEqual(
                watched["scope_violation_paths"],
                ["services/example/docs/requirements.md"],
            )
            recovered = pw.recover_task(
                self.workspace,
                self.run_id,
                "task-1",
                confirmed_stopped=True,
                session_id="renamed-spec-recovery",
                clock=lambda: FIXED + timedelta(seconds=2),
            )
            self.assertTrue(recovered["replan_required"])
            self.assertEqual(
                recovered["scope_violation_paths"],
                ["services/example/docs/requirements.md"],
            )
            self.assertIsNone(recovered["commit_authorization"])
        finally:
            os.chdir(previous)

    def test_planned_steering_rebuilds_waves_without_head_drift(self) -> None:
        original = pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.assertEqual(original["active_wave"], "wave-001")
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "### task-2\n\n- Status: pending\n- Depends on: none",
            "### task-2\n\n- Status: pending\n- Depends on: task-1",
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        replanned = pw.replan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.assertTrue(str(replanned["active_wave"]).startswith("wave-r"))
        self.assertTrue(
            all(
                str(item["wave_id"]).startswith("wave-r") for item in replanned["waves"]
            )
        )
        self.assertEqual(
            json.loads(
                (self.run_dir / "orchestration" / "waves" / "wave-001.json").read_text(
                    encoding="utf-8"
                )
            )["status"],
            "blocked",
        )
        active = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(active["base_commit"], self.initial)

    def test_repository_claims_add_every_exclusive_class_sentinel(self) -> None:
        handoff = (self.run_dir / "handoff.md").read_text(encoding="utf-8")
        domains = "\n  - ".join(
            f"{domain_class}:separate-key"
            for domain_class in sorted(waves.EXCLUSIVE_CONFLICT_CLASSES)
        )
        tasks = waves.parse_task_plans(handoff.replace("files:one.txt", domains, 1))
        workspace = json.loads(self.workspace.read_text(encoding="utf-8"))
        claims = waves._repository_claims(workspace, tasks)
        actual_domains = {
            claim["path"] for claim in claims if claim["kind"] == "domain"
        }
        expected_sentinels = {
            f"{waves.EXCLUSIVE_DOMAIN_CLAIM_PREFIX}{domain_class}"
            for domain_class in waves.EXCLUSIVE_CONFLICT_CLASSES
        }
        self.assertTrue(expected_sentinels <= actual_domains)

    def test_cleaned_final_wave_can_append_correction_tail(self) -> None:
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "### task-3\n\n- Status: pending",
            "### task-3\n\n- Status: done",
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)

        _, _, evidence = self._integrated_first_wave()
        promoted = pw.promote_wave(
            self.workspace, self.run_id, evidence, clock=lambda: FIXED
        )
        cleaned = pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(cleaned["status"], "done")

        text = handoff.read_text(encoding="utf-8")
        handoff.write_text(
            text
            + """

### task-4

- Status: pending
- Depends on: task-1, task-2
- Write claims: exact: services/example/three.txt
- Conflict domains: files:three.txt
- Implementation steps: correct only services/example/three.txt
- Validation: inspect three.txt
- End-to-end validation: verify the corrected integrated behavior
- Done criteria: three.txt contains the correction
""",
            encoding="utf-8",
        )
        handoff.chmod(0o600)

        replanned = pw.replan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        self.assertEqual(replanned["status"], "running")
        self.assertTrue(str(replanned["active_wave"]).startswith("wave-r"))
        self.assertEqual(len(replanned["waves"]), 2)
        self.assertEqual(replanned["waves"][0]["wave_id"], "wave-001")
        self.assertEqual(
            replanned["waves"][1]["tasks"][0]["task_id"],
            "task-4",
        )
        expected_index_sha256 = sha256_json(
            [entry["tasks"] for entry in replanned["waves"]]
        )
        self.assertEqual(replanned["plan_sha256"], expected_index_sha256)
        coordinator_path = self.run_dir / "orchestration" / "coordinator.json"
        replacement_wave_id = str(replanned["waves"][1]["wave_id"])
        replacement_wave_path = (
            self.run_dir / "orchestration" / "waves" / f"{replacement_wave_id}.json"
        )
        coordinator_before = coordinator_path.read_bytes()
        replacement_before = replacement_wave_path.read_bytes()
        repeated_replan = pw.replan_waves(
            self.workspace,
            self.run_id,
            2,
            clock=lambda: FIXED + timedelta(seconds=1),
        )
        self.assertEqual(repeated_replan, replanned)
        self.assertEqual(coordinator_path.read_bytes(), coordinator_before)
        self.assertEqual(replacement_wave_path.read_bytes(), replacement_before)
        self.assertEqual(
            waves._load_wave(self.run_dir, replacement_wave_id)["status"], "planned"
        )

        replacement_sha256 = sha256_json(
            [entry["tasks"] for entry in replanned["waves"][1:]]
        )
        coordinator = json.loads(coordinator_path.read_text(encoding="utf-8"))
        coordinator["plan_sha256"] = replacement_sha256
        waves._save_coordinator(self.run_dir, coordinator)
        basis_path = self.run_dir / "prompt-impact" / "plan-basis.json"
        basis = json.loads(basis_path.read_text(encoding="utf-8"))
        basis["plan_sha256"] = replacement_sha256
        basis_path.write_text(
            json.dumps(basis, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(
            PromptWorkspaceError, "coordinator plan digest is invalid"
        ):
            resume.plan_run_resume(
                json.loads(self.workspace.read_text(encoding="utf-8")),
                self.run_id,
                clock=lambda: FIXED + timedelta(seconds=1),
            )

        replacement_wave = waves._load_wave(self.run_dir, replacement_wave_id)
        replacement_plane = waves._load_task_plane(
            self.run_dir, replacement_wave_id, "task-4"
        )
        replacement_wave["task_states"]["task-4"] = "assigned"
        replacement_plane["state"] = "assigned"
        waves._save_wave(self.run_dir, replacement_wave)
        waves._save_task_plane(self.run_dir, replacement_plane)
        with self.assertRaisesRegex(
            PromptWorkspaceError, "cannot change a live worker plan identity"
        ):
            recovery.recover_replanned_plan_digest(
                self.workspace,
                self.run_id,
                replacement_sha256,
                expected_index_sha256,
                clock=lambda: FIXED + timedelta(seconds=2),
            )
        self.assertFalse(
            (self.run_dir / "orchestration" / "plan-digest-recovery.json").exists()
        )
        replacement_wave["task_states"]["task-4"] = "planned"
        replacement_plane["state"] = "planned"
        waves._save_wave(self.run_dir, replacement_wave)
        waves._save_task_plane(self.run_dir, replacement_plane)

        resume_path = self.run_dir / "orchestration" / "resume-control.json"
        resume_path.symlink_to(self.run_dir / "missing-resume-control.json")
        with self.assertRaisesRegex(
            PromptWorkspaceError, "requires a journal-less coordinator"
        ):
            recovery.recover_replanned_plan_digest(
                self.workspace,
                self.run_id,
                replacement_sha256,
                expected_index_sha256,
                clock=lambda: FIXED + timedelta(seconds=2),
            )
        resume_path.unlink()

        recovery_path = self.run_dir / "orchestration" / "plan-digest-recovery.json"
        recovery_path.symlink_to(self.run_dir / "missing-plan-digest-recovery.json")
        with self.assertRaisesRegex(
            PromptWorkspaceError, "plan digest recovery path is invalid"
        ):
            recovery.recover_replanned_plan_digest(
                self.workspace,
                self.run_id,
                replacement_sha256,
                expected_index_sha256,
                clock=lambda: FIXED + timedelta(seconds=2),
            )
        recovery_path.unlink()

        refinement = specs.load_requirements_refinement(self.run_dir, required=True)
        assert refinement is not None
        compiled_requirements_sha256 = refinement["compiled_requirements_sha256"]
        refinement["compiled_requirements_sha256"] = "0" * 64
        specs.save_requirements_refinement(self.run_dir, refinement)
        with self.assertRaisesRegex(
            PromptWorkspaceError, "compiled requirements digest does not match"
        ):
            recovery.recover_replanned_plan_digest(
                self.workspace,
                self.run_id,
                replacement_sha256,
                expected_index_sha256,
                clock=lambda: FIXED + timedelta(seconds=2),
            )
        self.assertFalse(recovery_path.exists())
        refinement["compiled_requirements_sha256"] = compiled_requirements_sha256
        specs.save_requirements_refinement(self.run_dir, refinement)

        with mock.patch.object(
            recovery,
            "verify_requirements_refinement_contract",
            return_value={
                "impact": {"plan_action": "replan_required"},
                "impact_sha256": "f" * 64,
            },
        ):
            with self.assertRaisesRegex(
                PromptWorkspaceError,
                "material prompt impact cannot use plan digest recovery",
            ):
                recovery.recover_replanned_plan_digest(
                    self.workspace,
                    self.run_id,
                    replacement_sha256,
                    expected_index_sha256,
                    clock=lambda: FIXED + timedelta(seconds=2),
                )
        self.assertFalse(recovery_path.exists())

        with (
            mock.patch.object(
                pw,
                "recover_replanned_plan_digest",
                side_effect=lambda *arguments: recovery.recover_replanned_plan_digest(
                    *arguments, clock=lambda: FIXED + timedelta(seconds=2)
                ),
            ) as routed_recovery,
            mock.patch.object(pw, "emit") as emitted,
        ):
            return_code = pw.main(
                [
                    "plan-digest-recover",
                    "--workspace",
                    str(self.workspace),
                    "--run-id",
                    self.run_id,
                    "--expected-plan-sha256",
                    replacement_sha256,
                    "--expected-index-sha256",
                    expected_index_sha256,
                    "--json",
                ]
            )
        self.assertEqual(return_code, 0)
        routed_recovery.assert_called_once()
        recovered = emitted.call_args.args[0]
        self.assertEqual(recovered["status"], "recovered")

        coordinator = json.loads(coordinator_path.read_text(encoding="utf-8"))
        coordinator["plan_sha256"] = replacement_sha256
        waves._save_coordinator(self.run_dir, coordinator)
        recovery_state = json.loads(recovery_path.read_text(encoding="utf-8"))
        recovery_state["phase"] = "intent"
        recovery_path.write_text(
            json.dumps(recovery_state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        repeated = recovery.recover_replanned_plan_digest(
            self.workspace,
            self.run_id,
            replacement_sha256,
            expected_index_sha256,
            clock=lambda: FIXED + timedelta(seconds=3),
        )
        self.assertEqual(repeated, recovered)

        recovery_state = json.loads(recovery_path.read_text(encoding="utf-8"))
        recovery_state["phase"] = "basis-committed"
        recovery_path.write_text(
            json.dumps(recovery_state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        repeated_after_coordinator = recovery.recover_replanned_plan_digest(
            self.workspace,
            self.run_id,
            replacement_sha256,
            expected_index_sha256,
            clock=lambda: FIXED + timedelta(seconds=4),
        )
        self.assertEqual(repeated_after_coordinator, recovered)

        plan = resume.resume_run(
            self.workspace,
            self.run_id,
            clock=lambda: FIXED + timedelta(seconds=5),
        )
        self.assertEqual(
            (plan["outcome"], plan["next_transition"]),
            ("execute", "wave-prepare"),
        )
        self.assertIsNotNone(plan["resume_token"])
        control = resume.load_resume_control(self.run_dir, required=True)
        self.assertEqual((control["phase"], control["epoch"]), ("idle", plan["epoch"]))
        with mock.patch.object(pw, "emit") as emitted:
            return_code = pw.main(
                [
                    "wave-prepare",
                    "--workspace",
                    str(self.workspace),
                    "--run-id",
                    self.run_id,
                    "--resume-token",
                    str(plan["resume_token"]),
                    "--json",
                ]
            )
        self.assertEqual(return_code, 0)
        prepared = emitted.call_args.args[0]
        self.assertEqual(prepared["base_commit"], promoted["promoted_head"])
        self.assertIn("resume", prepared)

    def test_final_wave_cleanup_does_not_require_terminal_lifecycle_seal(self) -> None:
        handoff = self.run_dir / "handoff.md"
        handoff.write_text(
            handoff.read_text(encoding="utf-8").replace(
                "### task-3\n\n- Status: pending",
                "### task-3\n\n- Status: done",
            ),
            encoding="utf-8",
        )
        handoff.chmod(0o600)
        _, integration, evidence = self._integrated_first_wave()
        pw.promote_wave(self.workspace, self.run_id, evidence, clock=lambda: FIXED)

        cleaned = pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(cleaned["status"], "done")
        self.assertFalse(integration.exists())

    def test_promotion_review_can_append_and_integrate_one_correction_round(
        self,
    ) -> None:
        integrated, integration, evidence = self._integrated_first_wave()
        reviewed_head = str(integrated["integrated_head"])
        initial_plan = json.loads(
            (self.run_dir / "orchestration" / "coordinator.json").read_text(
                encoding="utf-8"
            )
        )["plan_sha256"]
        handoff = self.run_dir / "handoff.md"
        handoff.write_text(
            handoff.read_text(encoding="utf-8")
            + """

### task-4

- Status: pending
- Depends on: task-1
- Write claims: exact: services/example/one.txt
- Conflict domains: files:one.txt
- Implementation steps: correct only services/example/one.txt
- Validation: inspect one.txt recovery behavior
- End-to-end validation: verify the corrected retained integration
- Done criteria: one.txt contains the correction
""",
            encoding="utf-8",
        )
        handoff.chmod(0o600)

        replanned = pw.replan_waves(
            self.workspace, self.run_id, 2, clock=lambda: FIXED + timedelta(seconds=1)
        )
        self.assertNotEqual(replanned["plan_sha256"], initial_plan)
        self.assertEqual(replanned["active_wave"], "wave-001")
        retained = json.loads(
            (self.run_dir / "orchestration" / "waves" / "wave-001.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(retained["status"], "preparing")
        self.assertEqual(retained["base_commit"], self.initial)
        self.assertEqual(retained["contract_commit"], reviewed_head)
        self.assertEqual(retained["integrated_head"], reviewed_head)
        self.assertEqual(retained["task_ids"], ["task-1", "task-2", "task-4"])
        self.assertEqual(retained["batch_states"], ["done", "planned"])
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)
        pending_plans = list(
            (self.run_dir / "orchestration" / "pending-plans" / "wave-001").glob(
                "*.json"
            )
        )
        self.assertEqual(len(pending_plans), 1)
        pending_plan = json.loads(pending_plans[0].read_text(encoding="utf-8"))
        self.assertEqual(pending_plan["schema"], waves.PENDING_PLAN_SCHEMA)
        self.assertEqual(
            [task["task_id"] for task in pending_plan["tasks"]], ["task-4"]
        )

        prepared = pw.prepare_wave(
            self.workspace, self.run_id, clock=lambda: FIXED + timedelta(seconds=2)
        )
        self.assertEqual(prepared["integrated_head"], reviewed_head)
        dispatched = pw.dispatch_wave(
            self.workspace,
            self.run_id,
            reviewed_head,
            clock=lambda: FIXED + timedelta(seconds=3),
        )
        self.assertEqual(len(dispatched["assignments"]), 1)
        correction_assignment = json.loads(
            Path(dispatched["assignments"][0]).read_text(encoding="utf-8")
        )
        self.assertEqual(correction_assignment["task_id"], "task-4")
        self.assertEqual(correction_assignment["base_commit"], reviewed_head)

        self._complete_worker("task-4", "one.txt")
        corrected = pw.integrate_wave(
            self.workspace, self.run_id, clock=lambda: FIXED + timedelta(seconds=4)
        )
        corrected_head = str(corrected["integrated_head"])
        self.assertNotEqual(corrected_head, reviewed_head)
        self.assertEqual(
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", reviewed_head, corrected_head],
                cwd=integration,
                check=False,
            ).returncode,
            0,
        )
        self.assertTrue(
            all(state == "merged" for state in corrected["task_states"].values())
        )
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)

        evidence.write_text(
            json.dumps(
                {
                    "integration_head": corrected_head,
                    "bound_revision": "r0001",
                    "steering_sha256": "none",
                    "validation": "combined correction tests passed",
                    "code_review": "corrected integration review passed",
                    "steering_reconciled": True,
                }
            ),
            encoding="utf-8",
        )
        evidence.chmod(0o600)
        promoted = pw.promote_wave(
            self.workspace,
            self.run_id,
            evidence,
            clock=lambda: FIXED + timedelta(seconds=5),
        )
        self.assertEqual(promoted["promoted_head"], corrected_head)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), corrected_head)

    def test_promotion_review_rejects_counterfeit_integration_repository(self) -> None:
        integrated, integration, _evidence = self._integrated_first_wave()
        reviewed_head = str(integrated["integrated_head"])
        handoff = self.run_dir / "handoff.md"
        handoff.write_text(
            handoff.read_text(encoding="utf-8")
            + """

### task-4

- Status: pending
- Depends on: task-1
- Write claims: exact: services/example/one.txt
- Conflict domains: files:one.txt
- Implementation steps: reject a counterfeit integration checkout
- Validation: verify linked-worktree identity
- End-to-end validation: preserve the registered integration
- Done criteria: only the registered integration can dispatch
""",
            encoding="utf-8",
        )
        handoff.chmod(0o600)

        retained = self._substitute_counterfeit_integration(integration)
        with self.assertRaises(PromptWorkspaceError) as replan:
            pw.replan_waves(
                self.workspace,
                self.run_id,
                2,
                clock=lambda: FIXED + timedelta(seconds=1),
            )
        self.assertEqual(replan.exception.code, "WORKTREE_CONFLICT")
        self._restore_linked_integration(integration, retained)

        pw.replan_waves(
            self.workspace,
            self.run_id,
            2,
            clock=lambda: FIXED + timedelta(seconds=2),
        )
        retained = self._substitute_counterfeit_integration(integration)
        with self.assertRaises(PromptWorkspaceError) as prepare:
            pw.prepare_wave(
                self.workspace,
                self.run_id,
                clock=lambda: FIXED + timedelta(seconds=3),
            )
        self.assertEqual(prepare.exception.code, "WORKTREE_CONFLICT")
        self._restore_linked_integration(integration, retained)

        pw.prepare_wave(
            self.workspace,
            self.run_id,
            clock=lambda: FIXED + timedelta(seconds=4),
        )
        retained = self._substitute_counterfeit_integration(integration)
        with self.assertRaises(PromptWorkspaceError) as dispatch:
            pw.dispatch_wave(
                self.workspace,
                self.run_id,
                reviewed_head,
                clock=lambda: FIXED + timedelta(seconds=5),
            )
        self.assertEqual(dispatch.exception.code, "WORKTREE_CONFLICT")
        self._restore_linked_integration(integration, retained)

    def test_promotion_review_appends_dependency_frontiers_one_at_a_time(
        self,
    ) -> None:
        integrated, integration, _evidence = self._integrated_first_wave()
        reviewed_head = str(integrated["integrated_head"])
        handoff = self.run_dir / "handoff.md"
        handoff_text = handoff.read_text(encoding="utf-8").replace(
            "- Depends on: task-1, task-2",
            "- Depends on: task-5",
            1,
        )
        handoff.write_text(
            handoff_text
            + """

### task-4

- Status: pending
- Depends on: task-1
- Write claims: exact: services/example/one.txt
- Conflict domains: files:one.txt
- Implementation steps: apply the first reviewed correction
- Validation: inspect the first correction
- End-to-end validation: verify the retained integration
- Done criteria: the first correction is merged

### task-5

- Status: pending
- Depends on: task-4
- Write claims: exact: services/example/two.txt
- Conflict domains: files:two.txt
- Implementation steps: apply the dependent reviewed correction
- Validation: inspect the dependent correction
- End-to-end validation: verify the retained integration
- Done criteria: the dependent correction is merged
""",
            encoding="utf-8",
        )
        handoff.chmod(0o600)

        first = pw.replan_waves(
            self.workspace, self.run_id, 2, clock=lambda: FIXED + timedelta(seconds=1)
        )
        self.assertEqual(
            [task["task_id"] for task in first["waves"][0]["tasks"]],
            ["task-1", "task-2", "task-4"],
        )
        self.assertEqual(
            first["waves"][1]["tasks"][0]["dependencies"],
            [
                "task-1",
                "task-2",
            ],
        )
        pw.prepare_wave(
            self.workspace, self.run_id, clock=lambda: FIXED + timedelta(seconds=2)
        )
        pw.dispatch_wave(
            self.workspace,
            self.run_id,
            reviewed_head,
            clock=lambda: FIXED + timedelta(seconds=3),
        )
        self._complete_worker("task-4", "one.txt")
        first_integrated = pw.integrate_wave(
            self.workspace, self.run_id, clock=lambda: FIXED + timedelta(seconds=4)
        )
        first_correction_head = str(first_integrated["integrated_head"])

        second = pw.replan_waves(
            self.workspace, self.run_id, 2, clock=lambda: FIXED + timedelta(seconds=5)
        )
        self.assertEqual(
            [task["task_id"] for task in second["waves"][0]["tasks"]],
            ["task-1", "task-2", "task-4", "task-5"],
        )
        self.assertEqual(second["waves"][1]["tasks"][0]["dependencies"], ["task-5"])
        retained = json.loads(
            (self.run_dir / "orchestration" / "waves" / "wave-001.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(retained["contract_commit"], first_correction_head)
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=integration), first_correction_head
        )
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)

    def test_promotion_review_correction_cannot_depend_on_future_work(self) -> None:
        self._integrated_first_wave()
        handoff = self.run_dir / "handoff.md"
        handoff.write_text(
            handoff.read_text(encoding="utf-8")
            + """

### task-4

- Status: pending
- Depends on: task-3
- Write claims: exact: services/example/one.txt
- Conflict domains: files:one.txt
- Implementation steps: correct only services/example/one.txt
- Validation: inspect one.txt
- End-to-end validation: verify the corrected retained integration
- Done criteria: one.txt contains the correction
""",
            encoding="utf-8",
        )
        handoff.chmod(0o600)

        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.replan_waves(
                self.workspace,
                self.run_id,
                2,
                clock=lambda: FIXED + timedelta(seconds=1),
            )
        self.assertEqual(raised.exception.code, "REPLAN_REQUIRED")
        self.assertIn("future work", str(raised.exception))
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)
        self.assertFalse((self.run_dir / "orchestration" / "pending-plans").exists())

    def test_interrupted_correction_publication_blocks_promotion_and_replays(
        self,
    ) -> None:
        _integrated, _integration, evidence = self._integrated_first_wave()
        handoff = self.run_dir / "handoff.md"
        handoff.write_text(
            handoff.read_text(encoding="utf-8")
            + """

### task-4

- Status: pending
- Depends on: task-1
- Write claims: exact: services/example/one.txt
- Conflict domains: files:one.txt
- Implementation steps: correct only services/example/one.txt
- Validation: inspect one.txt
- End-to-end validation: verify the corrected retained integration
- Done criteria: one.txt contains the correction
""",
            encoding="utf-8",
        )
        handoff.chmod(0o600)

        original_save_wave = waves._save_wave
        with mock.patch.object(
            waves, "_save_wave", side_effect=RuntimeError("publication interrupted")
        ):
            with self.assertRaisesRegex(RuntimeError, "publication interrupted"):
                pw.replan_waves(
                    self.workspace,
                    self.run_id,
                    2,
                    clock=lambda: FIXED + timedelta(seconds=1),
                )

        with self.assertRaises(PromptWorkspaceError) as blocked:
            pw.promote_wave(
                self.workspace,
                self.run_id,
                evidence,
                clock=lambda: FIXED + timedelta(seconds=2),
            )
        self.assertEqual(blocked.exception.code, "EXECUTION_STATE_INVALID")
        self.assertIn("task indexes differ", str(blocked.exception))
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)

        handoff.write_text(
            handoff.read_text(encoding="utf-8").replace(
                "correct only services/example/one.txt",
                "changed live text that must not replace staged correction bytes",
            ),
            encoding="utf-8",
        )
        handoff.chmod(0o600)

        with mock.patch.object(waves, "_save_wave", side_effect=original_save_wave):
            replayed = pw.replan_waves(
                self.workspace,
                self.run_id,
                2,
                clock=lambda: FIXED + timedelta(seconds=3),
            )
        self.assertEqual(
            len(
                list(
                    (
                        self.run_dir / "orchestration" / "pending-plans" / "wave-001"
                    ).glob("*.json")
                )
            ),
            1,
        )
        self.assertEqual(replayed["active_wave"], "wave-001")
        wave = json.loads(
            (self.run_dir / "orchestration" / "waves" / "wave-001.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(wave["status"], "preparing")
        self.assertEqual(wave["task_ids"], ["task-1", "task-2", "task-4"])

    def test_staged_correction_survives_pre_coordinator_handoff_drift(self) -> None:
        self._integrated_first_wave()
        handoff = self.run_dir / "handoff.md"
        handoff.write_text(
            handoff.read_text(encoding="utf-8")
            + """

### task-4

- Status: pending
- Depends on: task-1
- Write claims: exact: services/example/one.txt
- Conflict domains: files:one.txt
- Implementation steps: original staged correction
- Validation: inspect one.txt
- End-to-end validation: verify the corrected retained integration
- Done criteria: one.txt contains the correction
""",
            encoding="utf-8",
        )
        handoff.chmod(0o600)

        original_save = waves._save_coordinator
        with mock.patch.object(
            waves,
            "_save_coordinator",
            side_effect=RuntimeError("coordinator publication interrupted"),
        ):
            with self.assertRaisesRegex(RuntimeError, "publication interrupted"):
                pw.replan_waves(
                    self.workspace,
                    self.run_id,
                    2,
                    clock=lambda: FIXED + timedelta(seconds=1),
                )
        coordinator = json.loads(
            (self.run_dir / "orchestration" / "coordinator.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn(
            "task-4",
            [task["task_id"] for task in coordinator["waves"][0]["tasks"]],
        )

        handoff.write_text(
            handoff.read_text(encoding="utf-8").replace(
                "original staged correction", "changed unstaged correction"
            ),
            encoding="utf-8",
        )
        handoff.chmod(0o600)
        with mock.patch.object(waves, "_save_coordinator", side_effect=original_save):
            replayed = pw.replan_waves(
                self.workspace,
                self.run_id,
                2,
                clock=lambda: FIXED + timedelta(seconds=2),
            )
        task = next(
            task
            for task in replayed["waves"][0]["tasks"]
            if task["task_id"] == "task-4"
        )
        self.assertEqual(task["implementation_steps"], "original staged correction")

    def test_interrupted_worker_can_transfer_declared_dirty_state(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration" / "assignments" / "wave-001" / "task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="interrupted-worker",
                clock=lambda: FIXED,
            )
            pw.dispatch_wave(
                self.workspace, self.run_id, self.initial, clock=lambda: FIXED
            )
            plane = json.loads(
                (
                    self.run_dir
                    / "orchestration"
                    / "tasks"
                    / "wave-001"
                    / "task-1.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(plane["state"], "running")
            self.assertIsNotNone(plane["worker_session_sha256"])
            (scope_cwd / "one.txt").write_text("recoverable\n", encoding="utf-8")
            recovery_plan = resume.resume_run(
                self.workspace,
                self.run_id,
                clock=lambda: FIXED + timedelta(seconds=WORKER_STALL_SECONDS),
            )
            self.assertEqual(recovery_plan["next_transition"], "task-recover")
            self.assertEqual(
                recovery_plan["worker_context"]["scope_cwd"],
                assignment["scope_cwd"],
            )
            self.assertEqual(
                recovery_plan["worker_context"]["recover_argv"][-3:],
                ["task-1", "--confirmed-stopped", "--json"],
            )
            with self.assertRaises(PromptWorkspaceError) as unconfirmed:
                pw.recover_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    confirmed_stopped=False,
                    session_id="replacement-worker",
                    clock=lambda: FIXED,
                )
            self.assertEqual(
                unconfirmed.exception.code, "RECOVERY_CONFIRMATION_REQUIRED"
            )
            recovered = pw.recover_task(
                self.workspace,
                self.run_id,
                "task-1",
                confirmed_stopped=True,
                session_id="replacement-worker",
                clock=lambda: FIXED,
            )
            self.assertEqual(
                recovered["commit_context"]["session_id"], "replacement-worker"
            )
            self.assertEqual(
                recovered["worker_session_fingerprint_sha256"],
                hashlib.sha256(b"replacement-worker").hexdigest(),
            )
            self.assertEqual(
                recovered["result_context"]["result_path"], assignment["result_path"]
            )
            self.assertEqual(
                Path(recovered["result_context"]["publication_cwd"]),
                Path(assignment["result_path"]).parent,
            )
            recovered_authorization = Path(str(recovered["commit_authorization"]))
            recovered_authorization.unlink()
            resume_control = self._write_resume_intent(
                "task-recover",
                {"task_id": "task-1", "confirmed_stopped": True},
            )
            replayed = pw.recover_task(
                self.workspace,
                self.run_id,
                "task-1",
                confirmed_stopped=True,
                session_id="replacement-worker",
                clock=lambda: FIXED + timedelta(seconds=1),
            )
            self.assertTrue(Path(str(replayed["commit_authorization"])).is_file())
            self.assertEqual(
                replayed["commit_context"]["session_id"], "replacement-worker"
            )
            resume_control.unlink()
        finally:
            os.chdir(previous)
        self.assertEqual(recovered["observed_head"], self.initial)
        self.assertEqual(recovered["changed_paths"], ["services/example/one.txt"])
        recovered_authorization = Path(str(recovered["commit_authorization"]))
        self.assertEqual(
            json.loads(recovered_authorization.read_text(encoding="utf-8"))[
                "session_sha256"
            ],
            hashlib.sha256(b"replacement-worker").hexdigest(),
        )
        plane = json.loads(
            (
                self.run_dir / "orchestration" / "tasks" / "wave-001" / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(len(plane["worker_session_sha256_history"]), 2)
        task_two = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-2.json"
            ).read_text(encoding="utf-8")
        )
        pw.arm_task(self.workspace, self.run_id, "task-2", clock=lambda: FIXED)
        os.chdir(Path(task_two["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as reused:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-2",
                    task_two["assignment_sha256"],
                    FIXED_TEXT,
                    session_id="interrupted-worker",
                    clock=lambda: FIXED,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(reused.exception.code, "FRESH_SESSION_REQUIRED")

    def test_interrupted_scope_expansion_recovers_for_reporting_only(self) -> None:
        handoff_path = self.run_dir / "handoff.md"
        handoff = handoff_path.read_text(encoding="utf-8")
        for task_id in ("task-2", "task-3"):
            handoff = handoff.replace(
                f"### {task_id}\n\n- Status: pending",
                f"### {task_id}\n\n- Status: done",
            )
        handoff_path.write_text(handoff, encoding="utf-8")
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        scope_cwd = Path(assignment["scope_cwd"])
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="scope-expansion-original",
                clock=lambda: FIXED,
            )
            (scope_cwd / "two.txt").write_text("outside claims\n", encoding="utf-8")
            recovered = pw.recover_task(
                self.workspace,
                self.run_id,
                "task-1",
                confirmed_stopped=True,
                session_id="scope-expansion-reporter",
                clock=lambda: FIXED + timedelta(seconds=1),
            )
        finally:
            os.chdir(previous)

        self.assertTrue(recovered["replan_required"])
        self.assertEqual(
            recovered["scope_violation_paths"], ["services/example/two.txt"]
        )
        self.assertEqual(recovered["changed_paths"], ["services/example/two.txt"])
        self.assertIsNone(recovered["commit_authorization"])
        self.assertIsNone(recovered["commit_claim"])

        draft = {
            "schema": RESULT_SCHEMA,
            "run_id": self.run_id,
            "wave_id": "wave-001",
            "task_id": "task-1",
            "assignment_sha256": assignment["assignment_sha256"],
            "status": "REPLAN_REQUIRED",
            "commit": assignment["base_commit"],
            "changed_paths": recovered["changed_paths"],
            "summary": "Reported exact tracked dirt outside immutable claims.",
            "decisions": ["Preserve it only through the quarantine owner."],
            "open_risks": ["A corrected assignment is required."],
            "spec_gaps": [],
            "validation": "Recovery preflight only.",
            "end_to_end_validation": "No product validation was run.",
            "code_review": "No commit was authorized.",
            "completed_at": FIXED_TEXT,
        }
        result_context = recovered["result_context"]
        draft_path = Path(result_context["draft_path"])
        draft_path.write_bytes(specs.stable_json(draft))
        os.chdir(Path(result_context["publication_cwd"]))
        try:
            pw.publish_task_result(
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json",
                draft_path,
                Path(result_context["result_path"]),
            )
        finally:
            os.chdir(previous)
        pw.accept_task_result(
            self.workspace,
            self.run_id,
            "task-1",
            clock=lambda: FIXED + timedelta(seconds=2),
        )
        handoff_path.write_text(
            handoff_path.read_text(encoding="utf-8")
            + """

### task-4

- Status: pending
- Depends on: none
- Write claims: exact: services/example/two.txt
- Conflict domains: files:two.txt
- Implementation steps: reimplement from the clean integration base
- Validation: inspect the corrected tracked file
- End-to-end validation: verify the correction without archived bytes
- Done criteria: two.txt contains the clean correction
""",
            encoding="utf-8",
        )
        replanned = pw.replan_waves(
            self.workspace,
            self.run_id,
            1,
            clock=lambda: FIXED + timedelta(seconds=3),
        )
        archive_ref = waves._failed_worker_archive_ref(
            self.run_id, "wave-001", "task-1"
        )
        archive_commit = git("rev-parse", "--verify", archive_ref, cwd=self.repo)
        self.assertEqual(
            git("rev-parse", f"{archive_commit}^1", cwd=self.repo),
            assignment["base_commit"],
        )
        self.assertEqual(
            git(
                "diff",
                "--name-only",
                assignment["base_commit"],
                archive_commit,
                cwd=self.repo,
            ),
            "services/example/two.txt",
        )
        self.assertTrue(waves._clean(Path(assignment["worktree"])))
        wave = waves._load_wave(self.run_dir, "wave-001")
        self.assertEqual(wave["task_states"]["task-1"], "superseded")
        self.assertEqual(wave["task_states"]["task-4"], "planned")
        self.assertEqual(replanned["active_wave"], "wave-001")
        worker = Path(assignment["worktree"])
        expanded = worker / "services" / "example" / "two.txt"
        expanded.write_text("changed after archive\n", encoding="utf-8")
        with self.assertRaisesRegex(
            PromptWorkspaceError, "quarantine bytes changed before replay"
        ):
            waves._archive_failed_worker_dirt(
                repo=self.repo,
                run_dir=self.run_dir,
                wave_id="wave-001",
                task_id="task-1",
                worker=worker,
                base=assignment["base_commit"],
                changed_paths=["services/example/two.txt"],
                clock=lambda: FIXED + timedelta(seconds=4),
            )
        self.assertEqual(
            expanded.read_text(encoding="utf-8"), "changed after archive\n"
        )
        git("restore", "--", "services/example/two.txt", cwd=worker)
        removed = waves._cleanup_failed_worker_archives(
            self.repo,
            self.run_dir,
            wave,
            lambda: FIXED + timedelta(seconds=5),
        )
        self.assertEqual(removed, [])
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", str(self.repo), "rev-parse", "--verify", archive_ref],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode,
            0,
        )

    def test_missing_active_wave_worktrees_rehydrate_before_task_transfer(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration = Path(str(prepared["integration_worktree"]))
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        worker = Path(str(assignment["worktree"]))
        scope_cwd = Path(str(assignment["scope_cwd"]))
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="lost-path-worker",
                clock=lambda: FIXED,
            )

            (scope_cwd / "one.txt").write_text("filesystem-only\n", encoding="utf-8")
        finally:
            os.chdir(previous)
        shutil.rmtree(worker)
        shutil.rmtree(integration)

        os.chdir(self.scope)
        try:
            with self.assertRaises(PromptWorkspaceError) as unconfirmed:
                pw.recover_wave_resources(
                    self.workspace,
                    self.run_id,
                    confirmed_stopped=False,
                    clock=lambda: FIXED + timedelta(seconds=299),
                )
            self.assertEqual(
                unconfirmed.exception.code, "RECOVERY_CONFIRMATION_REQUIRED"
            )
            recovered = pw.recover_wave_resources(
                self.workspace,
                self.run_id,
                confirmed_stopped=True,
                clock=lambda: FIXED + timedelta(seconds=300),
            )
        finally:
            os.chdir(previous)

        self.assertEqual(recovered["status"], "RESOURCES_RECOVERED")
        self.assertFalse(recovered["task_state_changed"])
        self.assertFalse(recovered["promotion_inferred"])
        self.assertEqual(
            {item["kind"] for item in recovered["resources"]},
            {"integration", "worker"},
        )
        self.assertTrue(all(item["restored"] for item in recovered["resources"]))
        self.assertTrue(
            all(item["filesystem_only_state_lost"] for item in recovered["resources"])
        )
        self.assertTrue(
            next(item for item in recovered["resources"] if item["kind"] == "worker")[
                "uncommitted_state_lost"
            ]
        )
        self.assertFalse(
            next(
                item for item in recovered["resources"] if item["kind"] == "integration"
            )["uncommitted_state_lost"]
        )
        self.assertEqual((scope_cwd / "one.txt").read_text(encoding="utf-8"), "base\n")
        self.assertEqual(git("rev-parse", "HEAD", cwd=integration), self.initial)
        lease_resources = waves.inspect_active_resources(
            json.loads(self.workspace.read_text(encoding="utf-8")), self.run_dir
        )
        active = {
            (item["kind"], item["path"], item["branch"], item["state"])
            for item in lease_resources
            if item["state"] == "present"
        }
        self.assertIn(
            (
                "integration",
                str(integration.absolute()),
                prepared["integration_branch"],
                "present",
            ),
            active,
        )
        self.assertIn(
            ("worker", str(worker.absolute()), assignment["branch"], "present"),
            active,
        )
        os.chdir(scope_cwd)
        try:
            transferred = pw.recover_task(
                self.workspace,
                self.run_id,
                "task-1",
                confirmed_stopped=True,
                session_id="replacement-after-path-loss",
                clock=lambda: FIXED + timedelta(seconds=301),
            )
        finally:
            os.chdir(previous)
        self.assertEqual(transferred["observed_head"], self.initial)
        self.assertEqual(transferred["changed_paths"], [])
        self.assertIsNotNone(transferred["commit_authorization"])

    def test_missing_promotion_pending_integration_rehydrates_integrated_head(
        self,
    ) -> None:
        integrated, integration, _ = self._integrated_first_wave()
        integrated_head = str(integrated["integrated_head"])
        shutil.rmtree(integration)

        previous = Path.cwd()
        os.chdir(self.scope)
        try:
            recovered = pw.recover_wave_resources(
                self.workspace,
                self.run_id,
                confirmed_stopped=True,
                clock=lambda: FIXED + timedelta(seconds=300),
            )
        finally:
            os.chdir(previous)

        self.assertEqual(recovered["status"], "RESOURCES_RECOVERED")
        self.assertFalse(recovered["task_state_changed"])
        self.assertFalse(recovered["promotion_inferred"])
        self.assertEqual(len(recovered["resources"]), 1)
        resource = recovered["resources"][0]
        self.assertEqual(resource["kind"], "integration")
        self.assertIsNone(resource["task_id"])
        self.assertTrue(resource["restored"])
        self.assertEqual(resource["head"], integrated_head)
        self.assertEqual(git("rev-parse", "HEAD", cwd=integration), integrated_head)
        wave = json.loads(
            (self.run_dir / "orchestration" / "waves" / "wave-001.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(wave["status"], "promotion_pending")
        self.assertTrue(
            all(state == "merged" for state in wave["task_states"].values())
        )

    def test_missing_promotion_pending_integration_accepts_superseded_history(
        self,
    ) -> None:
        integrated, integration, _ = self._integrated_first_wave()
        integrated_head = str(integrated["integrated_head"])
        wave_path = self.run_dir / "orchestration" / "waves" / "wave-001.json"
        wave = json.loads(wave_path.read_text(encoding="utf-8"))
        wave["task_states"]["task-1"] = "superseded"
        wave_path.write_text(json.dumps(wave), encoding="utf-8")
        wave_path.chmod(0o600)
        shutil.rmtree(integration)

        previous = Path.cwd()
        os.chdir(self.scope)
        try:
            recovered = pw.recover_wave_resources(
                self.workspace,
                self.run_id,
                confirmed_stopped=True,
                clock=lambda: FIXED + timedelta(seconds=300),
            )
        finally:
            os.chdir(previous)

        self.assertEqual(recovered["status"], "RESOURCES_RECOVERED")
        self.assertEqual(len(recovered["resources"]), 1)
        self.assertTrue(recovered["resources"][0]["restored"])
        self.assertEqual(git("rev-parse", "HEAD", cwd=integration), integrated_head)
        retained = json.loads(wave_path.read_text(encoding="utf-8"))
        self.assertEqual(
            retained["task_states"],
            {"task-1": "superseded", "task-2": "merged"},
        )
        self.assertEqual(retained["status"], "promotion_pending")

    def test_missing_worker_with_staged_index_is_preserved_for_recovery(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        worker = Path(str(assignment["worktree"]))
        scope_cwd = Path(str(assignment["scope_cwd"]))
        pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        previous = Path.cwd()
        os.chdir(scope_cwd)
        try:
            pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                FIXED_TEXT,
                session_id="staged-lost-path-worker",
                clock=lambda: FIXED,
            )
            (scope_cwd / "one.txt").write_text("staged\n", encoding="utf-8")
            git("add", "services/example/one.txt", cwd=worker)
        finally:
            os.chdir(previous)
        shutil.rmtree(worker)

        os.chdir(self.scope)
        try:
            with self.assertRaises(PromptWorkspaceError) as blocked:
                pw.recover_wave_resources(
                    self.workspace,
                    self.run_id,
                    confirmed_stopped=True,
                    clock=lambda: FIXED + timedelta(seconds=300),
                )
        finally:
            os.chdir(previous)
        self.assertEqual(blocked.exception.code, "WORKTREE_CONFLICT")
        self.assertFalse(worker.exists())
        lease_resources = waves.inspect_active_resources(
            json.loads(self.workspace.read_text(encoding="utf-8")), self.run_dir
        )
        worker_resource = next(
            item for item in lease_resources if item["path"] == str(worker.absolute())
        )
        self.assertEqual(worker_resource["state"], "present")

    def test_missing_worker_broken_symlink_collision_stays_retained(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        worker = Path(str(assignment["worktree"]))
        shutil.rmtree(worker)
        worker.symlink_to(worker.parent / "missing-target", target_is_directory=True)
        previous = Path.cwd()
        os.chdir(self.scope)
        try:
            with self.assertRaises(PromptWorkspaceError) as blocked:
                pw.recover_wave_resources(
                    self.workspace,
                    self.run_id,
                    confirmed_stopped=True,
                    clock=lambda: FIXED + timedelta(seconds=300),
                )
        finally:
            os.chdir(previous)
        self.assertEqual(blocked.exception.code, "WORKTREE_CONFLICT")
        self.assertTrue(worker.is_symlink())

    def test_tampered_incoming_handoff_blocks_worker_start(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        handoff_path = Path(assignment["incoming_handoff_path"])
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        handoff["dependencies"] = ["task-999"]
        handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
        handoff_path.chmod(0o600)
        previous = Path.cwd()
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            with self.assertRaises(PromptWorkspaceError) as raised:
                pw.start_task(
                    self.workspace,
                    self.run_id,
                    "task-1",
                    assignment["assignment_sha256"],
                    FIXED_TEXT,
                    session_id="fresh-worker",
                    clock=lambda: FIXED,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")

    def test_accepted_branch_drift_blocks_integration_without_promotion(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        self._complete_worker("task-1", "one.txt")
        self._complete_worker("task-2", "two.txt")
        assignment = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "assignments"
                / "wave-001"
                / "task-1.json"
            ).read_text(encoding="utf-8")
        )
        worktree = Path(assignment["worktree"])
        (worktree / "services" / "example" / "one.txt").write_text(
            "drifted\n", encoding="utf-8"
        )
        git("add", "-A", cwd=worktree)
        git("commit", "-qm", "unexpected drift", cwd=worktree)
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.integrate_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(raised.exception.code, "WORKTREE_CONFLICT")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)

    def test_submodule_claim_is_rejected_without_initialization(self) -> None:
        git(
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.initial},services/example/vendor",
            cwd=self.primary,
        )
        git("commit", "-qm", "add gitlink fixture", cwd=self.primary)
        base = git("rev-parse", "HEAD", cwd=self.primary)
        lanes.ensure_project_lane(self.primary / "services" / "example")
        (self.repo / "services" / "example" / "vendor").mkdir(exist_ok=True)
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "exact: services/example/one.txt",
            "exact: services/example/vendor/file.txt",
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.dispatch_wave(self.workspace, self.run_id, base, clock=lambda: FIXED)
        self.assertEqual(raised.exception.code, "UNSUPPORTED_SUBMODULE_SCOPE")
        self.assertFalse(
            (self.repo / "services" / "example" / "vendor" / ".git").exists()
        )
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), base)

    def test_tracked_symlink_claim_is_rejected(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        link = self.primary / "services" / "example" / "linked"
        link.symlink_to(outside, target_is_directory=True)
        git("add", "services/example/linked", cwd=self.primary)
        git("commit", "-qm", "add symlink fixture", cwd=self.primary)
        base = git("rev-parse", "HEAD", cwd=self.primary)
        lanes.ensure_project_lane(self.primary / "services" / "example")
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "exact: services/example/one.txt",
            "exact: services/example/linked/file.txt",
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.dispatch_wave(self.workspace, self.run_id, base, clock=lambda: FIXED)
        self.assertEqual(raised.exception.code, "UNSUPPORTED_SYMLINK_SCOPE")
        self.assertFalse((outside / "file.txt").exists())

    def test_internal_ids_cannot_traverse_private_state(self) -> None:
        with self.assertRaises(PromptWorkspaceError) as run_error:
            pw.plan_waves(self.workspace, "../foreign", 2, clock=lambda: FIXED)
        self.assertEqual(run_error.exception.code, "RUN_STATE_INVALID")

    def test_malformed_interop_state_blocks_resource_creation(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        interop_path = self.run_dir / "orchestration" / "interop.json"
        interop = json.loads(interop_path.read_text(encoding="utf-8"))
        interop["generation"] = 0
        interop_path.write_text(
            json.dumps(interop, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        interop_path.chmod(0o600)
        with self.assertRaises(PromptWorkspaceError) as raised:
            pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")
        worktrees = git("worktree", "list", "--porcelain", cwd=self.repo)
        self.assertEqual(worktrees.count("worktree "), 2)

    def test_legacy_wave_and_interop_schemas_are_rejected(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        wave_path = self.run_dir / "orchestration" / "waves" / "wave-001.json"
        wave = json.loads(wave_path.read_text(encoding="utf-8"))
        wave["schema"] = "task-implementer/wave-v3"
        wave_path.write_text(
            json.dumps(wave, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        with self.assertRaises(PromptWorkspaceError) as wave_error:
            pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(wave_error.exception.code, "EXECUTION_STATE_INVALID")

        wave["schema"] = "task-implementer/wave-v4"
        wave_path.write_text(
            json.dumps(wave, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        interop_path = self.run_dir / "orchestration" / "interop.json"
        interop = json.loads(interop_path.read_text(encoding="utf-8"))
        interop["schema"] = 1
        interop_path.write_text(
            json.dumps(interop, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        with self.assertRaises(PromptWorkspaceError) as interop_error:
            pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(interop_error.exception.code, "WORKFLOW_UPGRADE_REQUIRED")

    def test_broken_symlink_resource_is_not_recorded_absent(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        wave = json.loads(
            (self.run_dir / "orchestration" / "waves" / "wave-001.json").read_text(
                encoding="utf-8"
            )
        )
        broken = self.root / "broken-worker"
        broken.symlink_to(self.root / "missing-worker-target", target_is_directory=True)
        workspace = pw.verify_workspace(self.workspace)

        cleaned = waves._cleanup_resource(
            workspace=workspace,
            run_dir=self.run_dir,
            wave=wave,
            repo=self.repo,
            kind="worker",
            worktree=broken,
            branch="codex/task/missing/worker",
            expected_tip=self.initial,
            reachable_tip=self.initial,
            clock=lambda: FIXED,
        )
        self.assertFalse(cleaned)
        self.assertTrue(os.path.lexists(broken))

    def test_journal_completes_partial_writes_as_one_json_line(self) -> None:
        journal = self.run_dir / "orchestration" / "journals" / "partial.jsonl"
        real_write = os.write

        def partial_write(descriptor: int, data: bytes | memoryview) -> int:
            payload = bytes(data)
            return real_write(descriptor, payload[: max(1, len(payload) // 2)])

        with mock.patch.object(waves.os, "write", side_effect=partial_write):
            waves._append_journal(journal, {"phase": "intent", "operation": "test"})
        lines = journal.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            [json.loads(line) for line in lines],
            [{"operation": "test", "phase": "intent"}],
        )

    def test_worktree_path_collision_and_moved_primary_fail_closed(self) -> None:
        plan = pw.plan_waves(self.workspace, self.run_id, 2, clock=lambda: FIXED)
        wave_path = (
            self.run_dir / "orchestration" / "waves" / f"{plan['active_wave']}.json"
        )
        wave = json.loads(wave_path.read_text(encoding="utf-8"))
        collision = Path(wave["integration_worktree"])
        collision.mkdir(parents=True)
        with self.assertRaises(PromptWorkspaceError) as path_error:
            pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(path_error.exception.code, "WORKTREE_COLLISION")
        collision.rmdir()
        (self.scope / "moved.txt").write_text("moved\n", encoding="utf-8")
        git("add", "services/example/moved.txt", cwd=self.repo)
        git("commit", "-qm", "move primary head", cwd=self.repo)
        with self.assertRaises(PromptWorkspaceError) as head_error:
            pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(head_error.exception.code, "WORKTREE_CONFLICT")

    def test_invalid_integration_evidence_and_queued_steering_block_promotion(
        self,
    ) -> None:
        _, _, evidence = self._integrated_first_wave()
        invalid = json.loads(evidence.read_text(encoding="utf-8"))
        invalid["validation"] = ""
        evidence.write_text(json.dumps(invalid), encoding="utf-8")
        evidence.chmod(0o600)
        with self.assertRaises(PromptWorkspaceError) as invalid_error:
            pw.promote_wave(self.workspace, self.run_id, evidence, clock=lambda: FIXED)
        self.assertEqual(invalid_error.exception.code, "INTEGRATION_VALIDATION_FAILED")
        self.prompt.write_text(
            self.prompt.read_text(encoding="utf-8").replace(
                "All three files are updated.",
                "All three files are updated with queued steering.",
            ),
            encoding="utf-8",
        )
        self.prompt.chmod(0o600)
        routed = pw.route_project_prompt(
            self.scope, self.codex_home, self.prompt.name, clock=lambda: FIXED
        )
        self.assertEqual(routed["action"], "steering_queued_after_wave")
        with self.assertRaises(PromptWorkspaceError) as steering_error:
            pw.promote_wave(self.workspace, self.run_id, evidence, clock=lambda: FIXED)
        self.assertEqual(steering_error.exception.code, "STEERING_QUEUED_AFTER_WAVE")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)

    def test_post_merge_hook_dirty_state_is_classified_and_resumable(self) -> None:
        integrated, integration, evidence = self._integrated_first_wave()
        hook_path = Path(
            git("rev-parse", "--git-path", "hooks/post-merge", cwd=self.repo)
        )
        if not hook_path.is_absolute():
            hook_path = self.repo / hook_path
        hook_path.write_text("#!/bin/sh\n: > hook-dirty.txt\n", encoding="utf-8")
        hook_path.chmod(0o755)
        with self.assertRaises(PromptWorkspaceError) as hook_error:
            pw.promote_wave(self.workspace, self.run_id, evidence, clock=lambda: FIXED)
        self.assertEqual(hook_error.exception.code, "PROMOTION_FAILED")
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=self.repo),
            git("rev-parse", "HEAD", cwd=integration),
        )
        (self.repo / "hook-dirty.txt").unlink()
        promoted = pw.promote_wave(
            self.workspace, self.run_id, evidence, clock=lambda: FIXED
        )
        self.assertEqual(promoted["promoted_head"], integrated["integrated_head"])


if __name__ == "__main__":
    unittest.main()
