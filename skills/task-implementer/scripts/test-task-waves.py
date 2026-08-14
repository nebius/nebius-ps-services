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
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import prompt_workspace as pw
import prompt_workspace_contract_delta as contract_delta
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

## Requirement Records

### TI-REQ-001: Execute dependency waves safely

- Status: active
- Requirement: Bind execution to accepted project intent.
- Constraints: Preserve the managed lane.
- Non-goals: Runtime installation.

#### Acceptance criteria

- Validated waves progress.

#### Verification

- Run the focused wave tests.

## Task Implementer Open Questions

- None.

## Task Implementer Requirements Change Log

- 2026-07-14: Established the test contract.
"""

DESIGN_BODY = """# Task Implementer Designs

## Design Records

### TI-DES-001: Bind wave execution

- Status: planned
- Requirements: TI-REQ-001
- Selected approach: Validate intent before wave progression.
- Boundaries and interfaces: Prompt impact and wave state.
- Validation: Focused owner and wave tests.
- Rollback: Retain the managed lane.

#### Alternatives considered

- Unbound execution was rejected.

#### Implementation evidence

- Prompt impact settlement tests.

## Task Implementer Design Change Log

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
        (self.scope / "AGENTS.md").write_text(
            "# Project instructions\n\nStable rules.\n", encoding="utf-8"
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
        self.project_agent_gate = mock.patch.object(
            waves,
            "verify_project_agent_contract",
            return_value={"status": "ok", "outcome": "not-needed"},
        )
        self.project_agent_gate.start()
        self.addCleanup(self.project_agent_gate.stop)
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

    def _lifecycle_inspect_command(self, integration: Path) -> tuple[Path, str]:
        project = integration / "services" / "example"
        orchestration = self.run_dir / "orchestration"
        private_root = orchestration / "project-agent-instructions"
        helper = (
            Path(__file__).resolve().parents[2]
            / "project-agent-instructions"
            / "scripts"
            / "project_agent_instructions.py"
        )
        command = shlex.join(
            [
                sys.executable,
                str(helper),
                "inspect",
                "--project-root",
                str(project),
                "--spec-owner",
                "maintain-project-specs",
                "--requirements",
                "docs/requirements.md",
                "--design",
                "docs/design.md",
                "--spec-receipt",
                str(orchestration / "project-agent-spec-receipt.json"),
                "--runtime-config",
                str(orchestration / "project-agent-runtime.json"),
                "--codex-home",
                str(self.codex_home),
                "--private-root",
                str(private_root),
                "--output",
                str(private_root / "manifest.json"),
            ]
        )
        return project, command

    def _prepare_lifecycle_inspect(self) -> tuple[Path, Path, str]:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration = Path(str(prepared["integration_worktree"]))
        project, command = self._lifecycle_inspect_command(integration)
        return integration, project, command

    def _seal_terminal_lifecycle(self) -> dict[str, object]:
        coordinator = waves.load_coordinator_state(self.run_dir)
        assert coordinator is not None and isinstance(coordinator["active_wave"], str)
        wave_path = (
            self.run_dir
            / "orchestration"
            / "waves"
            / f"{coordinator['active_wave']}.json"
        )
        wave = json.loads(wave_path.read_text(encoding="utf-8"))
        lifecycle_root = (
            self.codex_home
            / "project-specs"
            / "example"
            / f"terminal-{wave['wave_id']}"
        )
        instruction_root = lifecycle_root / "project-instructions"
        instruction_root.mkdir(parents=True, exist_ok=True)
        agents = self.scope / "AGENTS.md"
        agents_sha256 = (
            hashlib.sha256(agents.read_bytes()).hexdigest()
            if agents.is_file()
            else None
        )
        instruction_state = instruction_root / "state.json"
        instruction_state.write_text(
            json.dumps(
                {
                    "schema": "project-agent-instructions.state.v3",
                    "project_root": str(self.scope.resolve()),
                    "project_scope": "services/example",
                    "target_path": str(agents.resolve()),
                    "target_sha256": agents_sha256,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        requirements = self.scope / "docs" / "requirements.md"
        design = self.scope / "docs" / "design.md"
        lifecycle_path = lifecycle_root / "lifecycle.json"
        lifecycle_path.write_text(
            json.dumps(
                {
                    "schema": "maintain-project-specs.lifecycle.v1",
                    "phase": "sealed",
                    "project_scope": "services/example",
                    "git_head_at_prompt": wave["base_commit"],
                    "requirements_sha256": hashlib.sha256(
                        requirements.read_bytes()
                    ).hexdigest(),
                    "design_sha256": hashlib.sha256(design.read_bytes()).hexdigest(),
                    "receipt_sha256": "a" * 64,
                    "project_instructions_state_sha256": hashlib.sha256(
                        instruction_state.read_bytes()
                    ).hexdigest(),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return contract_delta.adopt_contract_delta(
            self.workspace,
            self.run_id,
            lifecycle_path,
            clock=lambda: FIXED + timedelta(seconds=20),
        )

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

    def test_trusted_worker_python_accepts_only_exact_path_canonical_family(
        self,
    ) -> None:
        hook_python = self.root / "hook-bin" / "python3.12"
        canonical_python = self.root / "path-bin" / "python3.14"
        alternate_python = self.root / "alternate-bin" / "python3.14"
        helper = self.root / "helper.py"
        helper.write_text("# helper\n", encoding="utf-8")
        for executable in (hook_python, canonical_python, alternate_python):
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)

        def which(value: str) -> str | None:
            return str(canonical_python) if value == "python3.14" else None

        with (
            mock.patch.object(waves.sys, "executable", str(hook_python)),
            mock.patch.object(waves.shutil, "which", side_effect=which),
        ):
            self.assertTrue(
                waves._trusted_python_command(
                    [str(canonical_python), str(helper), "prepare"], helper
                )
            )
            self.assertTrue(
                waves._trusted_python_command(
                    ["python3.14", str(helper), "prepare"], helper
                )
            )
            self.assertFalse(
                waves._trusted_python_command(
                    [str(alternate_python), str(helper), "prepare"], helper
                )
            )
            self.assertFalse(
                waves._trusted_python_command(
                    ["python2", str(helper), "prepare"], helper
                )
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
        self.assertEqual(context["schema"], "task-implementer/worker-commit-context-v1")
        self.assertEqual(context["session_id"], "raw-worker-session")
        self.assertEqual(context["session_id_source"], "CODEX_THREAD_ID")
        self.assertEqual(Path(context["repo_root"]), Path(assignment["worktree"]))
        self.assertEqual(Path(context["lifecycle_cwd"]), self.scope)
        self.assertEqual(context["prepare_argv"][0], context["python_executable"])
        self.assertEqual(context["prepare_argv"][1], context["helper_path"])
        self.assertEqual(
            waves.authorize_task_commit_lifecycle(
                self.workspace, self.run_id, shlex.join(context["prepare_argv"])
            )["status"],
            "authorized",
        )
        bad_argv = list(context["prepare_argv"])
        bad_argv[bad_argv.index("--session-id") + 1] = started[
            "worker_session_fingerprint_sha256"
        ]
        with self.assertRaises(PromptWorkspaceError):
            waves.authorize_task_commit_lifecycle(
                self.workspace, self.run_id, shlex.join(bad_argv)
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

    def test_dispatch_revalidates_project_agent_state_after_preparation(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        waves.verify_project_agent_contract.side_effect = pw.PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent state became stale"
        )
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        with self.assertRaises(pw.PromptWorkspaceError) as caught:
            pw.dispatch_wave(
                self.workspace, self.run_id, self.initial, clock=lambda: FIXED
            )
        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")
        waves.verify_project_agent_contract.assert_called_once()

    def test_lifecycle_bridge_authorizes_only_active_run_project_instructions(
        self,
    ) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration = Path(str(prepared["integration_worktree"]))
        project = integration / "services" / "example"
        orchestration = self.run_dir / "orchestration"
        private_root = orchestration / "project-agent-instructions"
        receipt = orchestration / "project-agent-spec-receipt.json"
        runtime = orchestration / "project-agent-runtime.json"
        helper = (
            Path(__file__).resolve().parents[2]
            / "project-agent-instructions"
            / "scripts"
            / "project_agent_instructions.py"
        )
        command = shlex.join(
            [
                sys.executable,
                str(helper),
                "inspect",
                "--project-root",
                str(project),
                "--spec-owner",
                "maintain-project-specs",
                "--requirements",
                "docs/requirements.md",
                "--design",
                "docs/design.md",
                "--spec-receipt",
                str(receipt),
                "--runtime-config",
                str(runtime),
                "--codex-home",
                str(self.codex_home),
                "--private-root",
                str(private_root),
                "--output",
                str(private_root / "manifest.json"),
            ]
        )

        authorized = pw.authorize_project_agent_lifecycle(
            self.workspace, self.run_id, command
        )

        self.assertEqual(authorized["status"], "authorized")
        self.assertEqual(authorized["action"], "inspect")
        self.assertEqual(authorized["outer_project_root"], str(self.scope))
        self.assertEqual(authorized["project_root"], str(project))
        self.assertEqual(
            authorized["command_sha256"], hashlib.sha256(command.encode()).hexdigest()
        )
        spec_helper = (
            Path(__file__).resolve().parents[2]
            / "maintain-project-specs"
            / "scripts"
            / "project_specs.py"
        )
        validation_session = "019ff65c-3e02-7780-8f24-448c391b5f66"
        validate_command = shlex.join(
            [
                sys.executable,
                str(spec_helper),
                "validate",
                "--project-root",
                str(project),
                "--output",
                str(receipt),
                "--session-id",
                validation_session,
                "--task-implementer-workspace",
                str(self.workspace),
                "--task-implementer-run-id",
                self.run_id,
            ]
        )
        validated = pw.authorize_project_agent_lifecycle(
            self.workspace, self.run_id, validate_command
        )
        self.assertEqual(validated["action"], "validate")
        self.assertEqual(validated["project_root"], str(project))
        for replacement in (
            validate_command.replace(str(receipt), str(orchestration / "other.json")),
            validate_command.replace(validation_session, "not-a-session"),
        ):
            with self.subTest(validate_replacement=replacement):
                with self.assertRaises(pw.PromptWorkspaceError) as invalid_validate:
                    pw.authorize_project_agent_lifecycle(
                        self.workspace, self.run_id, replacement
                    )
                self.assertEqual(
                    invalid_validate.exception.code, "EXECUTION_STATE_INVALID"
                )
        bridged = subprocess.run(
            [
                sys.executable,
                str(Path(pw.__file__).resolve()),
                "lifecycle-authorize",
                "--workspace",
                str(self.workspace),
                "--run-id",
                self.run_id,
                "--kind",
                "project-instructions",
                "--json",
            ],
            input=command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        self.assertEqual(bridged.returncode, 0, bridged.stdout + bridged.stderr)
        self.assertEqual(json.loads(bridged.stdout), authorized)
        render_command = shlex.join(
            [
                sys.executable,
                str(helper),
                "render",
                "--private-root",
                str(private_root),
                "--manifest",
                str(private_root / "manifest.json"),
                "--decision",
                str(private_root / "decision.json"),
                "--output",
                str(private_root / "rules.md"),
                "--state",
                str(private_root / "state.json"),
            ]
        )
        rendered = pw.authorize_project_agent_lifecycle(
            self.workspace, self.run_id, render_command
        )
        self.assertEqual(rendered["action"], "render")
        for name in ("manifest.json", "decision.json", "rules.md", "state.json"):
            expected = private_root / name
            alternate = private_root / f"other-{name}"
            with self.subTest(render_path=name):
                with self.assertRaises(pw.PromptWorkspaceError) as invalid_render:
                    pw.authorize_project_agent_lifecycle(
                        self.workspace,
                        self.run_id,
                        render_command.replace(str(expected), str(alternate)),
                    )
                self.assertEqual(
                    invalid_render.exception.code, "EXECUTION_STATE_INVALID"
                )
        bridged_render = subprocess.run(
            [
                sys.executable,
                str(Path(pw.__file__).resolve()),
                "lifecycle-authorize",
                "--workspace",
                str(self.workspace),
                "--run-id",
                self.run_id,
                "--kind",
                "project-instructions",
                "--json",
            ],
            input=render_command,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        self.assertEqual(json.loads(bridged_render.stdout), rendered)
        for action, arguments in (
            (
                "apply",
                [
                    "--private-root",
                    str(private_root),
                    "--manifest",
                    str(private_root / "manifest.json"),
                    "--decision",
                    str(private_root / "decision.json"),
                    "--ownership",
                    str(private_root / "ownership.json"),
                    "--state",
                    str(private_root / "state.json"),
                ],
            ),
            (
                "verify",
                [
                    "--private-root",
                    str(private_root),
                    "--state",
                    str(private_root / "state.json"),
                ],
            ),
        ):
            terminal_command = shlex.join(
                [sys.executable, str(helper), action, *arguments]
            )
            with self.subTest(action=action):
                with self.assertRaises(pw.PromptWorkspaceError) as terminal:
                    pw.authorize_project_agent_lifecycle(
                        self.workspace, self.run_id, terminal_command
                    )
                self.assertEqual(terminal.exception.code, "EXECUTION_STATE_INVALID")
        with self.assertRaises(pw.PromptWorkspaceError) as caught:
            pw.authorize_project_agent_lifecycle(
                self.workspace,
                self.run_id,
                command.replace(str(project), str(integration / "services" / "other")),
            )
        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")

    def test_lifecycle_bridge_accepts_only_promotion_pending_coordinator_delta(
        self,
    ) -> None:
        _integrated, integration, _evidence = self._integrated_first_wave()
        project = integration / "services" / "example"
        requirements = project / "docs" / "requirements.md"
        requirements.write_text(
            requirements.read_text(encoding="utf-8")
            + "\n<!-- reconciled after worker integration -->\n",
            encoding="utf-8",
        )
        orchestration = self.run_dir / "orchestration"
        spec_helper = (
            Path(__file__).resolve().parents[2]
            / "maintain-project-specs"
            / "scripts"
            / "project_specs.py"
        )
        command = shlex.join(
            [
                sys.executable,
                str(spec_helper),
                "validate",
                "--project-root",
                str(project),
                "--output",
                str(orchestration / "project-agent-spec-receipt.json"),
                "--session-id",
                "019ff65c-3e02-7780-8f24-448c391b5f66",
                "--task-implementer-workspace",
                str(self.workspace),
                "--task-implementer-run-id",
                self.run_id,
            ]
        )

        authorized = pw.authorize_project_agent_lifecycle(
            self.workspace, self.run_id, command
        )
        self.assertEqual(authorized["status"], "authorized")
        self.assertEqual(authorized["action"], "validate")

        (project / "one.txt").write_text("unsafe product edit\n", encoding="utf-8")
        with self.assertRaises(PromptWorkspaceError) as caught:
            pw.authorize_project_agent_lifecycle(self.workspace, self.run_id, command)
        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")

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
        command = shlex.join(
            [
                sys.executable,
                str(Path(pw.__file__).resolve()),
                "coordinator-commit",
                "--workspace",
                str(self.workspace),
                "--run-id",
                self.run_id,
                "--json",
            ]
        )
        authorized = pw.authorize_project_agent_lifecycle(
            self.workspace, self.run_id, command
        )
        self.assertEqual(authorized["action"], "coordinator-commit")
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

    def test_lifecycle_impact_attests_before_and_after_wave_plan(self) -> None:
        command = shlex.join(
            [
                sys.executable,
                str(Path(pw.__file__).resolve()),
                "wave-plan",
                "--workspace",
                str(self.workspace),
                "--run-id",
                self.run_id,
                "--capacity",
                "1",
                "--json",
            ]
        )

        before = pw.authorize_lifecycle_impact(self.workspace, self.run_id, command)
        self.assertIsNone(before["checkpoint_head"])
        self.assertFalse(before["review_correction"])
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        after = pw.authorize_lifecycle_impact(self.workspace, self.run_id, command)

        self.assertEqual(after["status"], "authorized")
        self.assertEqual(after["action"], "wave-plan")
        self.assertEqual(after["outer_project_root"], str(self.scope))
        self.assertRegex(str(after["checkpoint_head"]), r"^[0-9a-f]{40,64}$")
        self.assertFalse(after["review_correction"])
        self.assertEqual(before["command_sha256"], after["command_sha256"])

        self._write_resume_intent("wave-plan", {"capacity": 1})
        resumed_command = shlex.join(
            [
                sys.executable,
                str(Path(pw.__file__).resolve()),
                "wave-plan",
                "--workspace",
                str(self.workspace),
                "--run-id",
                self.run_id,
                "--capacity",
                "1",
                "--resume-token",
                "2" * 64,
                "--json",
            ]
        )
        resumed = pw.authorize_lifecycle_impact(
            self.workspace, self.run_id, resumed_command
        )
        self.assertEqual(resumed["status"], "authorized")
        with self.assertRaisesRegex(
            PromptWorkspaceError, "resume-controlled wave-plan binding is invalid"
        ):
            pw.authorize_lifecycle_impact(
                self.workspace,
                self.run_id,
                resumed_command.replace("2" * 64, "3" * 64),
            )
        control_path = self.run_dir / "orchestration" / "resume-control.json"
        control = json.loads(control_path.read_text(encoding="utf-8"))
        control.update(
            {
                "phase": "idle",
                "transition": None,
                "arguments": None,
                "arguments_sha256": None,
                "resume_token": None,
            }
        )
        control_path.write_text(json.dumps(control), encoding="utf-8")
        control_path.chmod(0o600)
        idle = pw.authorize_lifecycle_impact(
            self.workspace,
            self.run_id,
            resumed_command.replace("2" * 64, "3" * 64),
        )
        self.assertEqual(idle["status"], "authorized")

    def test_worker_commit_crosses_coordinator_bound_lifecycle_hook(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        assignment_path = (
            self.run_dir / "orchestration/assignments/wave-001/task-1.json"
        )
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        armed = pw.arm_task(self.workspace, self.run_id, "task-1", clock=lambda: FIXED)
        worker_session = "cross-worktree-worker"
        previous = Path.cwd()
        os.chdir(Path(assignment["scope_cwd"]))
        try:
            started = pw.start_task(
                self.workspace,
                self.run_id,
                "task-1",
                assignment["assignment_sha256"],
                str(armed["start_lease"]),
                session_id=worker_session,
                clock=lambda: FIXED,
            )
        finally:
            os.chdir(previous)
        helper = (
            Path(__file__).resolve().parents[2]
            / "commit"
            / "scripts"
            / "commit_transaction.py"
        )
        command = shlex.join(
            [
                sys.executable,
                str(helper),
                "prepare",
                "--repo-root",
                assignment["worktree"],
                "--session-id",
                worker_session,
                "--authorization",
                started["commit_authorization"],
                "--claim",
                started["commit_claim"],
            ]
        )

        attested = pw.authorize_task_commit_lifecycle(
            self.workspace, self.run_id, command
        )
        self.assertEqual(attested["worker_root"], assignment["worktree"])
        self.assertEqual(attested["worker_session_id"], worker_session)
        lifecycle = load_lifecycle_hook()
        payload = {
            "cwd": str(self.scope),
            "session_id": "outer-lifecycle-session",
            "turn_id": "outer-turn",
        }
        lifecycle.evaluate({**payload, "hook_event_name": "UserPromptSubmit"})
        self.assertEqual(
            lifecycle.evaluate(
                {
                    **payload,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": command},
                }
            ),
            {},
        )
        with self.assertRaises(pw.PromptWorkspaceError) as wrong_root:
            pw.authorize_task_commit_lifecycle(
                self.workspace,
                self.run_id,
                command.replace(assignment["worktree"], str(self.repo), 1),
            )
        self.assertEqual(wrong_root.exception.code, "EXECUTION_STATE_INVALID")

    def test_lifecycle_bridge_accepts_only_staged_managed_specs(self) -> None:
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration = Path(str(prepared["integration_worktree"]))
        project = integration / "services" / "example"
        docs = project / "docs"
        for name in ("requirements.md", "design.md"):
            (docs / name).write_text(f"# {name}\n", encoding="utf-8")
        git(
            "add",
            "services/example/docs/requirements.md",
            "services/example/docs/design.md",
            cwd=integration,
        )
        orchestration = self.run_dir / "orchestration"
        private_root = orchestration / "project-agent-instructions"
        helper = (
            Path(__file__).resolve().parents[2]
            / "project-agent-instructions"
            / "scripts"
            / "project_agent_instructions.py"
        )
        command = shlex.join(
            [
                sys.executable,
                str(helper),
                "inspect",
                "--project-root",
                str(project),
                "--spec-owner",
                "maintain-project-specs",
                "--requirements",
                "docs/requirements.md",
                "--design",
                "docs/design.md",
                "--spec-receipt",
                str(orchestration / "project-agent-spec-receipt.json"),
                "--runtime-config",
                str(orchestration / "project-agent-runtime.json"),
                "--codex-home",
                str(self.codex_home),
                "--private-root",
                str(private_root),
                "--output",
                str(private_root / "manifest.json"),
            ]
        )

        allowed = pw.authorize_project_agent_lifecycle(
            self.workspace, self.run_id, command
        )
        self.assertEqual(allowed["action"], "inspect")
        (project / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
        git("add", "services/example/unexpected.txt", cwd=integration)
        with self.assertRaises(pw.PromptWorkspaceError) as caught:
            pw.authorize_project_agent_lifecycle(self.workspace, self.run_id, command)
        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")

    def test_prepared_contract_stage_and_commit_are_owner_bound(self) -> None:
        integration, project, _inspect_command = self._prepare_lifecycle_inspect()
        for name in ("requirements.md", "design.md"):
            path = project / "docs" / name
            path.write_bytes(path.read_bytes() + b"\n<!-- correction contract -->\n")
        helper = Path(pw.__file__).resolve()
        stage_command = shlex.join(
            [
                sys.executable,
                str(helper),
                "coordinator-stage",
                "--workspace",
                str(self.workspace),
                "--run-id",
                self.run_id,
                "--json",
            ]
        )
        authorized_stage = pw.authorize_project_agent_lifecycle(
            self.workspace, self.run_id, stage_command
        )
        self.assertEqual(authorized_stage["action"], "coordinator-stage")
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
        commit_command = stage_command.replace(
            "coordinator-stage", "coordinator-commit", 1
        )
        authorized_commit = pw.authorize_project_agent_lifecycle(
            self.workspace, self.run_id, commit_command
        )
        self.assertEqual(authorized_commit["action"], "coordinator-commit")
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
        _integration, project, _inspect_command = self._prepare_lifecycle_inspect()
        requirements = project / "docs" / "requirements.md"
        requirements.write_bytes(requirements.read_bytes() + b"\npartial\n")
        with self.assertRaises(pw.PromptWorkspaceError) as caught:
            pw.stage_coordinator_contract(
                self.workspace, self.run_id, clock=lambda: FIXED
            )
        self.assertEqual(caught.exception.code, "WORKTREE_CONFLICT")

    def test_lifecycle_bridge_rejects_partial_staged_contract(self) -> None:
        integration, project, command = self._prepare_lifecycle_inspect()
        docs = project / "docs"
        (docs / "requirements.md").write_text("# requirements\n", encoding="utf-8")
        git("add", "services/example/docs/requirements.md", cwd=integration)

        with self.assertRaises(pw.PromptWorkspaceError) as caught:
            pw.authorize_project_agent_lifecycle(self.workspace, self.run_id, command)

        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")

    def test_lifecycle_bridge_rejects_unstaged_and_untracked_deltas(self) -> None:
        _integration, project, command = self._prepare_lifecycle_inspect()
        tracked = project / "one.txt"
        tracked.write_text("unstaged\n", encoding="utf-8")
        with self.assertRaises(pw.PromptWorkspaceError) as unstaged:
            pw.authorize_project_agent_lifecycle(self.workspace, self.run_id, command)
        self.assertEqual(unstaged.exception.code, "EXECUTION_STATE_INVALID")

        tracked.write_text("base\n", encoding="utf-8")
        unexpected = project / "unexpected.txt"
        unexpected.write_text("untracked\n", encoding="utf-8")
        with self.assertRaises(pw.PromptWorkspaceError) as untracked:
            pw.authorize_project_agent_lifecycle(self.workspace, self.run_id, command)
        self.assertEqual(untracked.exception.code, "EXECUTION_STATE_INVALID")

    def test_lifecycle_bridge_rejects_deleted_and_symlinked_paths(self) -> None:
        integration, project, command = self._prepare_lifecycle_inspect()
        git("rm", "services/example/one.txt", cwd=integration)
        with self.assertRaises(pw.PromptWorkspaceError) as deleted:
            pw.authorize_project_agent_lifecycle(self.workspace, self.run_id, command)
        self.assertEqual(deleted.exception.code, "EXECUTION_STATE_INVALID")

        (project / "one.txt").write_text("base\n", encoding="utf-8")
        git("add", "services/example/one.txt", cwd=integration)
        docs = project / "docs"
        (docs / "requirements.md").unlink()
        (docs / "requirements.md").symlink_to("../one.txt")
        (docs / "design.md").write_text("# design\n", encoding="utf-8")
        git(
            "add",
            "services/example/docs/requirements.md",
            "services/example/docs/design.md",
            cwd=integration,
        )
        with self.assertRaises(pw.PromptWorkspaceError) as symlinked:
            pw.authorize_project_agent_lifecycle(self.workspace, self.run_id, command)
        self.assertEqual(symlinked.exception.code, "EXECUTION_STATE_INVALID")

    def test_lifecycle_bridge_rejects_stale_run_and_wave(self) -> None:
        _integration, _project, command = self._prepare_lifecycle_inspect()
        with self.assertRaises(pw.PromptWorkspaceError) as stale_run:
            pw.authorize_project_agent_lifecycle(
                self.workspace, "run-20000101t000000z-missing", command
            )
        self.assertEqual(stale_run.exception.code, "RUN_STATE_INVALID")

        pw.dispatch_wave(self.workspace, self.run_id, self.initial, clock=lambda: FIXED)
        with self.assertRaises(pw.PromptWorkspaceError) as stale_wave:
            pw.authorize_project_agent_lifecycle(self.workspace, self.run_id, command)
        self.assertEqual(stale_wave.exception.code, "EXECUTION_STATE_INVALID")

    def test_lifecycle_bridge_rejects_branch_and_head_drift(self) -> None:
        integration, _project, command = self._prepare_lifecycle_inspect()
        expected_branch = git("branch", "--show-current", cwd=integration)
        git("switch", "-qc", "unexpected-integration-branch", cwd=integration)
        with self.assertRaises(pw.PromptWorkspaceError) as branch_drift:
            pw.authorize_project_agent_lifecycle(self.workspace, self.run_id, command)
        self.assertEqual(branch_drift.exception.code, "EXECUTION_STATE_INVALID")

        git("switch", "-q", expected_branch, cwd=integration)
        git("commit", "--allow-empty", "-qm", "unexpected head drift", cwd=integration)
        with self.assertRaises(pw.PromptWorkspaceError) as head_drift:
            pw.authorize_project_agent_lifecycle(self.workspace, self.run_id, command)
        self.assertEqual(head_drift.exception.code, "EXECUTION_STATE_INVALID")

    def test_lifecycle_bridge_rejects_untrusted_inputs_and_unsafe_mode(self) -> None:
        _integration, _project, command = self._prepare_lifecycle_inspect()
        orchestration = self.run_dir / "orchestration"
        private_root = orchestration / "project-agent-instructions"
        helper = (
            Path(__file__).resolve().parents[2]
            / "project-agent-instructions"
            / "scripts"
            / "project_agent_instructions.py"
        )
        replacements = {
            "helper": (str(helper), str(self.root / "untrusted-helper.py")),
            "requirements": (
                "--requirements docs/requirements.md",
                "--requirements docs/other.md",
            ),
            "receipt": (
                str(orchestration / "project-agent-spec-receipt.json"),
                str(orchestration / "other-receipt.json"),
            ),
            "runtime": (
                str(orchestration / "project-agent-runtime.json"),
                str(orchestration / "other-runtime.json"),
            ),
            "output": (
                str(private_root / "manifest.json"),
                str(private_root / "other-manifest.json"),
            ),
            "private-root": (
                str(private_root),
                str(orchestration / "other-private-root"),
            ),
            "codex-home": (str(self.codex_home), str(self.root / "other-home")),
        }
        for label, (expected, alternate) in replacements.items():
            with self.subTest(label=label):
                with self.assertRaises(pw.PromptWorkspaceError) as caught:
                    pw.authorize_project_agent_lifecycle(
                        self.workspace,
                        self.run_id,
                        command.replace(expected, alternate),
                    )
                self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")

        self.workspace.chmod(0o644)
        with self.assertRaises(pw.PromptWorkspaceError) as unsafe_mode:
            pw.authorize_project_agent_lifecycle(self.workspace, self.run_id, command)
        self.assertEqual(unsafe_mode.exception.code, "WORKSPACE_PERMISSION_INVALID")

    def test_real_lifecycle_hook_crosses_wave_plan_and_run_bundle(self) -> None:
        lifecycle = load_lifecycle_hook()
        payload = {
            "cwd": str(self.scope),
            "session_id": "task-wave-lifecycle-session",
            "turn_id": "task-wave-lifecycle-turn",
        }
        lifecycle.evaluate({**payload, "hook_event_name": "UserPromptSubmit"})
        project, git_root, _scope = lifecycle._project(str(self.scope))
        state_path = lifecycle._state_path(git_root, payload["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        rules = state_path.parent / lifecycle.RULES_NAME
        rules.write_bytes(b"")
        rules.chmod(0o600)
        state.update(
            {
                "phase": "implementation-open",
                "receipt_sha256": "a" * 64,
                "rules_path": lifecycle.RULES_NAME,
                "rules_sha256": hashlib.sha256(b"").hexdigest(),
                "planned_write_epoch": 0,
            }
        )
        lifecycle._write(state_path, state)
        wave_command = shlex.join(
            [
                sys.executable,
                str(Path(pw.__file__).resolve()),
                "wave-plan",
                "--workspace",
                str(self.workspace),
                "--run-id",
                self.run_id,
                "--capacity",
                "1",
                "--json",
            ]
        )
        pre = {
            **payload,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": wave_command},
        }
        self.assertEqual(lifecycle.evaluate(pre), {})
        lifecycle.evaluate(
            {
                **pre,
                "hook_event_name": "PostToolUse",
                "tool_response": {"exit_code": 2},
            }
        )
        failed_state = lifecycle._load(state_path)
        assert failed_state is not None
        self.assertEqual(failed_state["phase"], "implementation-open")
        self.assertEqual(failed_state["write_epoch"], 0)
        self.assertEqual(lifecycle.evaluate(pre), {})
        pw.plan_waves(self.workspace, self.run_id, 1, clock=lambda: FIXED)
        post = {
            **pre,
            "hook_event_name": "PostToolUse",
            "tool_response": {"exit_code": 0},
        }
        lifecycle.evaluate(post)
        lifecycle.evaluate(post)
        current = lifecycle._load(state_path)
        assert current is not None
        self.assertEqual(current["phase"], "reconciliation-required")
        self.assertEqual(current["write_epoch"], 1)

        prepared = pw.prepare_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        integration = Path(str(prepared["integration_worktree"]))
        integration_project = integration / "services" / "example"
        orchestration = self.run_dir / "orchestration"
        private_root = orchestration / "project-agent-instructions"
        helper = (
            Path(__file__).resolve().parents[2]
            / "project-agent-instructions"
            / "scripts"
            / "project_agent_instructions.py"
        )
        inspect_command = shlex.join(
            [
                sys.executable,
                str(helper),
                "inspect",
                "--project-root",
                str(integration_project),
                "--spec-owner",
                "maintain-project-specs",
                "--requirements",
                "docs/requirements.md",
                "--design",
                "docs/design.md",
                "--spec-receipt",
                str(orchestration / "project-agent-spec-receipt.json"),
                "--runtime-config",
                str(orchestration / "project-agent-runtime.json"),
                "--codex-home",
                str(self.codex_home),
                "--private-root",
                str(private_root),
                "--output",
                str(private_root / "manifest.json"),
            ]
        )
        self.assertEqual(
            lifecycle.evaluate(
                {
                    **payload,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": inspect_command},
                }
            ),
            {},
        )
        self.assertEqual(project, self.scope.resolve())

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
        waves.verify_project_agent_contract.assert_called_once()
        self.assertEqual(
            waves.verify_project_agent_contract.call_args.args[2],
            integration / "services" / "example",
        )
        self.assertEqual(
            waves.verify_project_agent_contract.call_args.args[3], self.initial
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
        self._seal_terminal_lifecycle()
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

    def test_final_wave_cleanup_requires_terminal_lifecycle_seal(self) -> None:
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

        with self.assertRaises(PromptWorkspaceError) as missing:
            pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(missing.exception.code, "LIFECYCLE_SEAL_REQUIRED")
        self.assertTrue(integration.is_dir())

        sealed = self._seal_terminal_lifecycle()
        self.assertEqual(sealed["status"], "terminal-sealed")
        self.assertEqual(sealed["paths"], [])
        cleaned = pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        self.assertEqual(cleaned["status"], "done")

    def test_provenance_only_terminal_seal_is_promoted_before_cleanup(self) -> None:
        handoff = self.run_dir / "handoff.md"
        handoff.write_text(
            handoff.read_text(encoding="utf-8").replace(
                "### task-3\n\n- Status: pending",
                "### task-3\n\n- Status: done",
            ),
            encoding="utf-8",
        )
        handoff.chmod(0o600)
        _, _, evidence = self._integrated_first_wave()
        promoted = pw.promote_wave(
            self.workspace, self.run_id, evidence, clock=lambda: FIXED
        )
        agents = self.scope / "AGENTS.md"
        agents.write_text(
            agents.read_text(encoding="utf-8")
            + "\n<!-- refreshed terminal provenance -->\n",
            encoding="utf-8",
        )
        sealed = self._seal_terminal_lifecycle()
        self.assertEqual(sealed["paths"], ["services/example/AGENTS.md"])
        self.assertNotEqual(sealed["contract_head"], promoted["promoted_head"])

        cleaned = pw.cleanup_wave(
            self.workspace,
            self.run_id,
            clock=lambda: FIXED + timedelta(seconds=21),
        )
        self.assertEqual(cleaned["status"], "done")
        self.assertEqual(git("status", "--short", cwd=self.repo), "")
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=self.repo), sealed["contract_head"]
        )
        self.assertEqual(
            git("show", "-s", "--format=%s", "HEAD", cwd=self.repo),
            contract_delta.TERMINAL_SEAL_MESSAGE,
        )
        self.assertIn(
            "refreshed terminal provenance", agents.read_text(encoding="utf-8")
        )

    def test_terminal_seal_spec_reconciliation_is_settled_before_cleanup(self) -> None:
        handoff = self.run_dir / "handoff.md"
        handoff.write_text(
            handoff.read_text(encoding="utf-8").replace(
                "### task-3\n\n- Status: pending",
                "### task-3\n\n- Status: done",
            ),
            encoding="utf-8",
        )
        handoff.chmod(0o600)
        _, _, evidence = self._integrated_first_wave()
        promoted = pw.promote_wave(
            self.workspace, self.run_id, evidence, clock=lambda: FIXED
        )
        design = self.scope / "docs" / "design.md"
        design.write_text(
            design.read_text(encoding="utf-8")
            + "\nTerminal implementation status reconciliation.\n",
            encoding="utf-8",
        )
        sealed = self._seal_terminal_lifecycle()
        self.assertEqual(sealed["paths"], ["services/example/docs/design.md"])
        self.assertNotEqual(sealed["contract_head"], promoted["promoted_head"])

        current_impact = specs.load_current_prompt_impact(
            self.run_dir, required=True
        )
        assert current_impact is not None
        terminal_impact = dict(current_impact[0])
        terminal_impact["plan_action"] = "replan_required"
        terminal_impact_sha256 = hashlib.sha256(
            specs.stable_json(terminal_impact)
        ).hexdigest()
        drift = PromptWorkspaceError(
            "REPLAN_REQUIRED",
            "prompt impact plan basis is stale",
        )
        with (
            mock.patch.object(
                waves,
                "verify_prompt_impact_plan",
                side_effect=[
                    drift,
                    {"status": "settled"},
                    {"status": "settled"},
                ],
            ),
            mock.patch.object(
                waves,
                "verify_requirements_refinement_contract",
                return_value={
                    "impact": terminal_impact,
                    "impact_sha256": terminal_impact_sha256,
                },
            ),
        ):
            cleaned = pw.cleanup_wave(
                self.workspace,
                self.run_id,
                clock=lambda: FIXED + timedelta(seconds=21),
            )
        self.assertEqual(cleaned["status"], "done")
        self.assertEqual(git("status", "--short", cwd=self.repo), "")
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=self.repo), sealed["contract_head"]
        )
        plan_basis = json.loads(
            (self.run_dir / "prompt-impact" / "plan-basis.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(plan_basis["plan_action"], "replan_required")

    def test_digest_recovery_binds_receipt_only_impact_refresh(self) -> None:
        handoff = self.run_dir / "handoff.md"
        text = handoff.read_text(encoding="utf-8").replace(
            "### task-3\n\n- Status: pending",
            "### task-3\n\n- Status: done",
        )
        handoff.write_text(text, encoding="utf-8")
        handoff.chmod(0o600)

        _, _, evidence = self._integrated_first_wave()
        pw.promote_wave(self.workspace, self.run_id, evidence, clock=lambda: FIXED)
        self._seal_terminal_lifecycle()
        pw.cleanup_wave(self.workspace, self.run_id, clock=lambda: FIXED)
        handoff.write_text(
            handoff.read_text(encoding="utf-8")
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
        expected_index_sha256 = sha256_json(
            [entry["tasks"] for entry in replanned["waves"]]
        )
        replacement_sha256 = sha256_json(
            [entry["tasks"] for entry in replanned["waves"][1:]]
        )
        coordinator_path = self.run_dir / "orchestration" / "coordinator.json"
        coordinator = json.loads(coordinator_path.read_text(encoding="utf-8"))
        coordinator["plan_sha256"] = replacement_sha256
        waves._save_coordinator(self.run_dir, coordinator)
        basis_path = self.run_dir / "prompt-impact" / "plan-basis.json"
        basis = json.loads(basis_path.read_text(encoding="utf-8"))
        basis["plan_sha256"] = replacement_sha256
        basis_path.write_text(
            json.dumps(basis, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        prior_impact = specs.load_current_prompt_impact(self.run_dir, required=True)
        assert prior_impact is not None
        design_path = self.scope / "docs" / "design.md"
        design_path.write_bytes(
            design_path.read_bytes() + b"\n<!-- receipt-only refresh -->\n"
        )
        recovery_path = self.run_dir / "orchestration" / "plan-digest-recovery.json"
        with mock.patch.object(
            recovery, "write_exclusive", side_effect=OSError("injected journal failure")
        ):
            with self.assertRaisesRegex(OSError, "injected journal failure"):
                recovery.recover_replanned_plan_digest(
                    self.workspace,
                    self.run_id,
                    replacement_sha256,
                    expected_index_sha256,
                    clock=lambda: FIXED + timedelta(seconds=1),
                )
        self.assertFalse(recovery_path.exists())
        refreshed_impact = specs.load_current_prompt_impact(self.run_dir, required=True)
        assert refreshed_impact is not None
        self.assertNotEqual(refreshed_impact[1], prior_impact[1])
        self.assertEqual(refreshed_impact[0]["plan_action"], "retain_plan")
        self.assertEqual(
            json.loads(basis_path.read_text(encoding="utf-8"))["plan_sha256"],
            replacement_sha256,
        )

        recovered = recovery.recover_replanned_plan_digest(
            self.workspace,
            self.run_id,
            replacement_sha256,
            expected_index_sha256,
            clock=lambda: FIXED + timedelta(seconds=2),
        )
        self.assertEqual(recovered["status"], "recovered")
        recovery_state = json.loads(recovery_path.read_text(encoding="utf-8"))
        recovered_basis = json.loads(basis_path.read_text(encoding="utf-8"))
        self.assertEqual(recovery_state["impact_sha256"], refreshed_impact[1])
        self.assertEqual(recovered_basis["impact_sha256"], refreshed_impact[1])
        self.assertEqual(recovered_basis["plan_sha256"], expected_index_sha256)
        self.assertEqual(
            recovered_basis["spec_receipt_sha256"],
            refreshed_impact[0]["spec_receipt_sha256"],
        )
        specs.verify_prompt_impact_plan(
            self.run_dir,
            waves.load_coordinator_state(self.run_dir),
            self.scope,
        )
        current_impact_before_change = specs.load_current_prompt_impact(
            self.run_dir, required=True
        )
        coordinator_after_recovery = coordinator_path.read_bytes()
        basis_after_recovery = basis_path.read_bytes()
        design_path.write_bytes(
            design_path.read_bytes() + b"\n<!-- changed after recovery intent -->\n"
        )
        with self.assertRaisesRegex(
            PromptWorkspaceError, "plan digest recovery impact changed"
        ):
            recovery.recover_replanned_plan_digest(
                self.workspace,
                self.run_id,
                replacement_sha256,
                expected_index_sha256,
                clock=lambda: FIXED + timedelta(seconds=3),
            )
        self.assertEqual(
            specs.load_current_prompt_impact(self.run_dir, required=True),
            current_impact_before_change,
        )
        self.assertEqual(coordinator_path.read_bytes(), coordinator_after_recovery)
        self.assertEqual(basis_path.read_bytes(), basis_after_recovery)

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
        _project, inspect_command = self._lifecycle_inspect_command(integration)
        retained = self._substitute_counterfeit_integration(integration)
        with self.assertRaises(PromptWorkspaceError) as lifecycle:
            pw.authorize_project_agent_lifecycle(
                self.workspace, self.run_id, inspect_command
            )
        self.assertEqual(lifecycle.exception.code, "EXECUTION_STATE_INVALID")
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

    def test_sealed_contract_delta_is_adopted_without_cleaning_the_lane(self) -> None:
        integrated, integration, evidence = self._integrated_first_wave()
        integration_base = str(integrated["integrated_head"])
        requirements = self.scope / "docs" / "requirements.md"
        design = self.scope / "docs" / "design.md"
        requirements.write_bytes(requirements.read_bytes() + b"\n<!-- reconciled -->\n")
        design.write_bytes(design.read_bytes() + b"\n<!-- reconciled -->\n")
        paths = [
            "services/example/docs/design.md",
            "services/example/docs/requirements.md",
        ]
        lane_status = git("status", "--porcelain=v1", cwd=self.repo)
        lifecycle_root = self.codex_home / "project-specs" / "example" / "session"
        lifecycle_root.mkdir(parents=True)
        instruction_root = lifecycle_root / "project-instructions"
        instruction_root.mkdir()
        instruction_state = instruction_root / "state.json"
        instruction_state.write_text(
            json.dumps(
                {
                    "schema": "project-agent-instructions.state.v3",
                    "project_root": str(self.scope.resolve()),
                    "project_scope": "services/example",
                    "target_path": str((self.scope / "AGENTS.md").resolve()),
                    "target_sha256": hashlib.sha256(
                        (self.scope / "AGENTS.md").read_bytes()
                    ).hexdigest(),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        lifecycle_path = lifecycle_root / "lifecycle.json"
        lifecycle_path.write_text(
            json.dumps(
                {
                    "schema": "maintain-project-specs.lifecycle.v1",
                    "phase": "sealed",
                    "project_scope": "services/example",
                    "git_head_at_prompt": self.initial,
                    "requirements_sha256": hashlib.sha256(
                        requirements.read_bytes()
                    ).hexdigest(),
                    "design_sha256": hashlib.sha256(design.read_bytes()).hexdigest(),
                    "receipt_sha256": "a" * 64,
                    "project_instructions_state_sha256": hashlib.sha256(
                        instruction_state.read_bytes()
                    ).hexdigest(),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        original_write_atomic = contract_delta.write_atomic
        interrupted = False

        def interrupt_after_contract_commit(path: Path, data: bytes) -> None:
            nonlocal interrupted
            if path.name == "contract-delta-adoption.json" and not interrupted:
                interrupted = True
                raise RuntimeError("injected contract journal interruption")
            original_write_atomic(path, data)

        with mock.patch.object(
            contract_delta, "verify_prompt_impact_plan", return_value={"status": "ok"}
        ):
            with mock.patch.object(
                contract_delta,
                "write_atomic",
                side_effect=interrupt_after_contract_commit,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "contract journal interruption"
                ):
                    contract_delta.adopt_contract_delta(
                        self.workspace,
                        self.run_id,
                        lifecycle_path,
                        clock=lambda: FIXED + timedelta(seconds=1),
                    )
            adopted = contract_delta.adopt_contract_delta(
                self.workspace,
                self.run_id,
                lifecycle_path,
                clock=lambda: FIXED + timedelta(seconds=1),
            )
        self.assertEqual(adopted["paths"], paths)
        self.assertEqual(git("rev-parse", "HEAD^", cwd=integration), integration_base)
        self.assertEqual(
            git("show", "-s", "--format=%s", "HEAD", cwd=integration),
            contract_delta.CONTRACT_DELTA_MESSAGE,
        )
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)
        self.assertEqual(git("status", "--porcelain=v1", cwd=self.repo), lane_status)
        journal = contract_delta.contract_delta_journal(self.run_dir)
        assert journal is not None
        self.assertEqual(journal["phase"], "integration-committed")
        coordinator = waves.load_coordinator_state(self.run_dir)
        assert coordinator is not None
        active_wave = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "waves"
                / f"{coordinator['active_wave']}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(
            contract_delta.contract_delta_active(
                pw.verify_workspace(self.workspace),
                self.run_dir,
                coordinator,
                active_wave,
            )
        )
        handoff = self.run_dir / "handoff.md"
        handoff.write_text(
            handoff.read_text(encoding="utf-8")
            + """

### task-4

- Status: pending
- Depends on: task-1
- Write claims: exact: services/example/one.txt
- Conflict domains: files:one.txt
- Implementation steps: correct the retained integration
- Validation: inspect the correction
- End-to-end validation: verify the adopted contract remains in integration
- Done criteria: the correction is merged after contract adoption
""",
            encoding="utf-8",
        )
        handoff.chmod(0o600)
        adopted_head = str(adopted["contract_head"])
        journal_path = self.run_dir / "orchestration" / "contract-delta-adoption.json"
        journal_bytes = journal_path.read_bytes()
        unrelated = integration / "services" / "example" / "one.txt"
        unrelated.write_text("unattested descendant\n", encoding="utf-8")
        git("add", "services/example/one.txt", cwd=integration)
        git("commit", "-m", "unrelated integration descendant", cwd=integration)
        self.assertTrue(
            contract_delta.contract_delta_active(
                pw.verify_workspace(self.workspace),
                self.run_dir,
                coordinator,
                active_wave,
            )
        )
        with self.assertRaises(PromptWorkspaceError) as descendant_replan:
            pw.replan_waves(
                self.workspace,
                self.run_id,
                2,
                clock=lambda: FIXED + timedelta(seconds=2),
            )
        self.assertEqual(descendant_replan.exception.code, "WORKTREE_CONFLICT")

        forward_journal = json.loads(journal_bytes)
        forward_journal["contract_head"] = git("rev-parse", "HEAD", cwd=integration)
        journal_path.write_text(
            json.dumps(forward_journal, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        self.assertFalse(
            contract_delta.contract_delta_active(
                pw.verify_workspace(self.workspace),
                self.run_dir,
                coordinator,
                active_wave,
            )
        )
        with self.assertRaises(PromptWorkspaceError) as forward_journal_replan:
            pw.replan_waves(
                self.workspace,
                self.run_id,
                2,
                clock=lambda: FIXED + timedelta(seconds=2),
            )
        self.assertEqual(forward_journal_replan.exception.code, "WORKTREE_CONFLICT")
        journal_path.write_bytes(journal_bytes)
        git("reset", "--hard", adopted_head, cwd=integration)

        replanned = pw.replan_waves(
            self.workspace, self.run_id, 2, clock=lambda: FIXED + timedelta(seconds=2)
        )
        self.assertEqual(replanned["waves"][0]["tasks"][-1]["task_id"], "task-4")
        prepared = pw.prepare_wave(
            self.workspace, self.run_id, clock=lambda: FIXED + timedelta(seconds=3)
        )
        self.assertEqual(prepared["contract_commit"], adopted["contract_head"])
        self.assertNotEqual(prepared["contract_commit"], prepared["base_commit"])
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=integration), prepared["contract_commit"]
        )
        project, inspect_command = self._lifecycle_inspect_command(integration)
        authorized = pw.authorize_project_agent_lifecycle(
            self.workspace, self.run_id, inspect_command
        )
        self.assertEqual(authorized["status"], "authorized")
        self.assertEqual(authorized["action"], "inspect")
        self.assertEqual(authorized["project_root"], str(project))

        unrelated.write_text("unattested descendant\n", encoding="utf-8")
        git("add", "services/example/one.txt", cwd=integration)
        git("commit", "-m", "unrelated prepared descendant", cwd=integration)
        with self.assertRaises(PromptWorkspaceError) as descendant_authorization:
            pw.authorize_project_agent_lifecycle(
                self.workspace, self.run_id, inspect_command
            )
        self.assertEqual(
            descendant_authorization.exception.code, "EXECUTION_STATE_INVALID"
        )
        git("reset", "--hard", adopted_head, cwd=integration)

        stale_journal = json.loads(journal_bytes)
        stale_journal["contract_head"] = stale_journal["integration_base"]
        journal_path.write_text(
            json.dumps(stale_journal, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(PromptWorkspaceError) as stale_authorization:
            pw.authorize_project_agent_lifecycle(
                self.workspace, self.run_id, inspect_command
            )
        self.assertEqual(stale_authorization.exception.code, "EXECUTION_STATE_INVALID")
        journal_path.write_bytes(journal_bytes)

        git("reset", "--hard", prepared["base_commit"], cwd=integration)
        with self.assertRaises(PromptWorkspaceError) as base_authorization:
            pw.authorize_project_agent_lifecycle(
                self.workspace, self.run_id, inspect_command
            )
        self.assertEqual(base_authorization.exception.code, "EXECUTION_STATE_INVALID")
        git("reset", "--hard", adopted_head, cwd=integration)

        pw.dispatch_wave(
            self.workspace,
            self.run_id,
            str(adopted["contract_head"]),
            clock=lambda: FIXED + timedelta(seconds=4),
        )
        self._complete_worker("task-4", "one.txt")
        corrected = pw.integrate_wave(
            self.workspace, self.run_id, clock=lambda: FIXED + timedelta(seconds=5)
        )
        evidence_value = json.loads(evidence.read_text(encoding="utf-8"))
        evidence_value["integration_head"] = str(corrected["integrated_head"])
        evidence.write_text(json.dumps(evidence_value), encoding="utf-8")
        evidence.chmod(0o600)
        with mock.patch.object(
            waves,
            "promote_ff_only",
            side_effect=waves.GitPromotionError("injected promotion failure"),
        ):
            with self.assertRaises(PromptWorkspaceError) as failed:
                pw.promote_wave(
                    self.workspace,
                    self.run_id,
                    evidence,
                    clock=lambda: FIXED + timedelta(seconds=6),
                )
        self.assertEqual(failed.exception.code, "PROMOTION_BLOCKED")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.initial)
        self.assertEqual(git("status", "--porcelain=v1", cwd=self.repo), lane_status)
        restored_journal = contract_delta.contract_delta_journal(self.run_dir)
        assert restored_journal is not None
        self.assertEqual(restored_journal["phase"], "integration-committed")
        self.assertIsNone(restored_journal["promotion_target"])

        reconciled_requirements = integration / "services/example/docs/requirements.md"
        reconciled_requirements.write_text(
            reconciled_requirements.read_text(encoding="utf-8").replace(
                "- Validated waves progress.\n",
                "- Validated waves progress.\n"
                "- Retained corrections are reflected before promotion.\n",
            ),
            encoding="utf-8",
        )
        final = pw.commit_coordinator_delta(
            self.workspace,
            self.run_id,
            clock=lambda: FIXED + timedelta(seconds=7),
        )
        evidence_value["integration_head"] = final["commit"]
        evidence.write_text(json.dumps(evidence_value), encoding="utf-8")
        evidence.chmod(0o600)

        coordinator = waves.load_coordinator_state(self.run_dir)
        assert coordinator is not None
        active_wave = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "waves"
                / f"{coordinator['active_wave']}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(
            contract_delta.contract_delta_active(
                pw.verify_workspace(self.workspace),
                self.run_dir,
                coordinator,
                active_wave,
            )
        )
        resumed = resume.plan_run_resume(
            pw.verify_workspace(self.workspace),
            self.run_id,
            clock=lambda: FIXED + timedelta(seconds=8),
        )
        self.assertIn(resumed["outcome"], {"execute", "requires_confirmation"})
        self.assertIn(
            resumed["next_transition"],
            {"wave-promote", "wave-resource-recover"},
        )

        with mock.patch.object(
            waves,
            "promote_ff_only",
            side_effect=waves.GitPromotionError(
                "injected post-reconciliation promotion failure"
            ),
        ):
            with self.assertRaises(PromptWorkspaceError) as reconciled_failure:
                pw.promote_wave(
                    self.workspace,
                    self.run_id,
                    evidence,
                    clock=lambda: FIXED + timedelta(seconds=9),
                )
        self.assertEqual(reconciled_failure.exception.code, "PROMOTION_BLOCKED")
        self.assertEqual(git("status", "--porcelain=v1", cwd=self.repo), lane_status)
        restored_after_reconciliation = contract_delta.contract_delta_journal(
            self.run_dir
        )
        assert restored_after_reconciliation is not None
        self.assertEqual(
            restored_after_reconciliation["phase"], "integration-committed"
        )

        with mock.patch.object(
            waves,
            "record_promotion",
            side_effect=RuntimeError("injected post-fast-forward interruption"),
        ):
            with self.assertRaisesRegex(RuntimeError, "post-fast-forward interruption"):
                pw.promote_wave(
                    self.workspace,
                    self.run_id,
                    evidence,
                    clock=lambda: FIXED + timedelta(seconds=10),
                )
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), final["commit"])
        interrupted_wave = json.loads(
            (
                self.run_dir
                / "orchestration"
                / "waves"
                / f"{coordinator['active_wave']}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(interrupted_wave["status"], "promotion_pending")

        promoted = pw.promote_wave(
            self.workspace,
            self.run_id,
            evidence,
            clock=lambda: FIXED + timedelta(seconds=11),
        )
        self.assertEqual(promoted["promoted_head"], final["commit"])
        self.assertEqual(git("status", "--short", cwd=self.repo), "")
        promoted_journal = contract_delta.contract_delta_journal(self.run_dir)
        assert promoted_journal is not None
        self.assertEqual(promoted_journal["phase"], "promoted")

        self.impact_plan_gate.stop()
        self.refinement_gate.stop()
        lane_requirements = self.scope / "docs/requirements.md"
        lane_requirements.write_bytes(
            lane_requirements.read_bytes() + b"\n<!-- unrelated drift -->\n"
        )
        with self.assertRaises(PromptWorkspaceError) as unrelated_drift:
            pw.cleanup_wave(
                self.workspace,
                self.run_id,
                clock=lambda: FIXED + timedelta(seconds=12),
            )
        self.assertEqual(unrelated_drift.exception.code, "REPLAN_REQUIRED")
        git(
            "restore",
            "--worktree",
            "--",
            "services/example/docs/requirements.md",
            cwd=self.repo,
        )
        cleaned = pw.cleanup_wave(
            self.workspace,
            self.run_id,
            clock=lambda: FIXED + timedelta(seconds=13),
        )
        self.assertEqual(cleaned["status"], "done")
        current_impact = specs.load_current_prompt_impact(self.run_dir, required=True)
        assert current_impact is not None
        self.assertGreater(int(current_impact[0]["generation"]), 1)
        current_coordinator = waves.load_coordinator_state(self.run_dir)
        assert current_coordinator is not None
        specs.verify_prompt_impact_plan(
            self.run_dir,
            current_coordinator,
            self.scope,
        )

        next_wave = {
            "wave_id": "wave-002",
            "base_commit": final["commit"],
        }
        current_coordinator["waves"].append({"wave_id": "wave-002"})
        current_coordinator["active_wave"] = "wave-002"
        self.assertFalse(
            contract_delta.recover_contract_delta_promotion(
                pw.verify_workspace(self.workspace),
                self.run_dir,
                current_coordinator,
                next_wave,
            )
        )
        contract_delta.complete_contract_delta_promotion(
            pw.verify_workspace(self.workspace),
            self.run_dir,
            current_coordinator,
            next_wave,
            final["commit"],
        )
        self.assertEqual(
            contract_delta.contract_delta_journal(self.run_dir),
            promoted_journal,
        )

        tampered_promoted_journal = dict(promoted_journal)
        tampered_promoted_journal["promotion_target"] = self.initial
        journal_path.write_text(
            json.dumps(
                tampered_promoted_journal,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(PromptWorkspaceError) as stale_promoted_journal:
            contract_delta.recover_contract_delta_promotion(
                pw.verify_workspace(self.workspace),
                self.run_dir,
                current_coordinator,
                next_wave,
            )
        self.assertEqual(
            stale_promoted_journal.exception.code,
            "EXECUTION_STATE_INVALID",
        )
        journal_path.write_text(
            json.dumps(promoted_journal, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

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
            git("diff", "--name-only", assignment["base_commit"], archive_commit, cwd=self.repo),
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
        self.assertEqual(expanded.read_text(encoding="utf-8"), "changed after archive\n")
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
