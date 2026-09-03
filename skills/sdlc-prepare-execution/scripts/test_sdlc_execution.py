#!/usr/bin/env python3
"""Deterministic and real-Git tests for the Agentic SDLC execution plane."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import sdlc_execution_core as execution_core  # noqa: E402
import sdlc_execution_interop as execution_interop  # noqa: E402
from sdlc_execution_core import (  # noqa: E402
    ExecutionError,
    _claim_worker_session,
    advance_batch,
    arm_task,
    assignment_path,
    append_journal,
    build_dependency_waves,
    complete_wave,
    coordinator_path,
    finish_task,
    heartbeat_task,
    integrate_wave,
    journal_path,
    local_branch_exists,
    parse_locked_plan,
    prepare_execution,
    prepare_wave,
    promote_feature,
    recover_task,
    replan_future,
    requeue_task,
    seal_feature,
    seal_tdd_base,
    start_task,
    task_path,
    watch_task,
    wave_path,
    worktrees,
)
from sdlc_execution_interop import (  # noqa: E402
    ExecutionInteropError,
    acquire as acquire_outer_interop,
    complete_source_integration,
    load as load_outer_interop,
    release,
)


PLAN = """# FEAT-001 Plan v1

## Task Graph

### TASK-001

- Requirements: REQ-001
- Goal: implement the first independent behavior
- Depends on: none
- Write claims: exact: src/a.py
- Conflict domains: code:a
- Validation: python3 -m unittest tests.test_feature
- Done criteria: first behavior passes
- Rollback or stop conditions: stop on contract drift

### TASK-002

- Requirements: REQ-001
- Goal: implement the second independent behavior
- Depends on: none
- Write claims: exact: src/b.py
- Conflict domains: code:b
- Validation: python3 -m unittest tests.test_feature
- Done criteria: second behavior passes
- Rollback or stop conditions: stop on contract drift

### TASK-003

- Requirements: REQ-001
- Goal: implement the dependent behavior
- Depends on: TASK-001, TASK-002
- Write claims: exact: src/c.py
- Conflict domains: code:c
- Validation: python3 -m unittest tests.test_feature
- Done criteria: dependent behavior passes
- Rollback or stop conditions: stop on contract drift
"""

REQUIREMENTS = """# Requirements

<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v2 -->
<!-- REQUIREMENT: REQ-001 status=active priority=P0 type=feature -->
### REQ-001: Implement the selected behavior

#### User Story

As a maintainer, I need the planned behavior to be implemented safely.

#### Acceptance Criteria

- AC-001: The planned behavior passes focused validation.

#### Negative Criteria

- NC-001: Workers do not exceed their declared scope.

#### Validation Method

Run the combined execution validation.

#### Test Method

Run the focused execution tests.

#### Evaluation Method

Inspect the promoted result evidence.

<!-- /REQUIREMENT: REQ-001 -->
<!-- maintain-project-specs:requirements:end -->
"""

DESIGN = """# Design

<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v2 -->
<!-- FEATURE: FEAT-001 reqs=REQ-001 status=ready delivery=not-started priority=P0 version=1 -->
### FEAT-001: Execute isolated task waves

#### Requirements Covered

- REQ-001

#### Context Evidence

The execution fixture models one isolated feature.

#### Design Details

Use coordinator-owned dependency waves and isolated workers.

#### Selected Option

Bind immutable assignments to one integration base.

#### Alternatives Considered

Shared mutable workers were rejected because ownership would be ambiguous.

#### Implementation Boundaries

Workers may change only their declared paths.

#### Test-First Success Criteria

- TDD-001: Out-of-scope changes fail closed.

#### Validation Plan

Run focused execution-plane checks.

#### Test Plan

Run the execution unit suite.

#### Evaluation Plan

Inspect assignment, result, merge, and cleanup evidence.

#### Rollout And Rollback

Promote only a clean verified integration head.

#### Done Definition

All waves are integrated and validated.

#### Implementation Evidence

Not implemented yet.

#### Verification Evidence

Not verified yet.

<!-- /FEATURE: FEAT-001 -->
<!-- maintain-project-specs:design:end -->
"""

MANAGED_PLAN = """# FEAT-001 Plan v1

## Task Graph

### TASK-001

- Requirements: REQ-001
- Goal: implement one selected-scope behavior
- Depends on: none
- Write claims: exact: services/example/src/value.py
- Conflict domains: code:value
- Validation: focused test passes
- Done criteria: selected-scope behavior is committed
- Rollback or stop conditions: stop on contract drift
"""


class WorktreeInteropBoundaryTests(unittest.TestCase):
    def test_private_interop_rejects_public_lifecycle_before_subprocess(self) -> None:
        for action in ("add", "integrate", "remove"):
            with (
                self.subTest(action=action),
                mock.patch.object(execution_interop.subprocess, "run") as run,
                self.assertRaisesRegex(
                    ExecutionInteropError, "rejects public lifecycle actions"
                ),
            ):
                execution_interop._call(Path.cwd(), [action])
            run.assert_not_called()

    def test_private_interop_rejects_task_lane_before_state_write(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(
                execution_interop,
                "_call",
                return_value={"status": "task-lane"},
            ),
            mock.patch.object(execution_interop, "_write") as write,
            self.assertRaisesRegex(
                ExecutionInteropError, "cannot use a Task Implementer persistent lane"
            ),
        ):
            acquire_outer_interop(
                Path(temporary) / "run",
                Path(temporary) / "project",
                ".",
                "a" * 40,
            )
        write.assert_not_called()


def git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def write_private(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def seed_prompt_impact(run_dir: Path, selected_project: Path) -> None:
    prompt_id = "prompt-" + "a" * 32
    intent_sha256 = "b" * 64
    write_private(
        run_dir / "prompt.json",
        {
            "prompt_id": prompt_id,
            "revisions": [
                {
                    "revision": "r0001",
                    "sha256": "c" * 64,
                    "intent_sha256": intent_sha256,
                }
            ],
        },
    )
    requirements_bytes = (selected_project / "docs" / "requirements.md").read_bytes()
    design_bytes = (selected_project / "docs" / "design.md").read_bytes()
    impact_receipt = {
        "schema": "agentic-sdlc/prompt-impact-receipt-v1",
        "workflow": "agentic-sdlc",
        "generation": 1,
        "prompt_id": prompt_id,
        "revision": "r0001",
        "intent_sha256": intent_sha256,
        "spec_receipt_sha256": "d" * 64,
        "requirements_sha256": hashlib.sha256(requirements_bytes).hexdigest(),
        "design_sha256": hashlib.sha256(design_bytes).hexdigest(),
        "effects": [],
        "plan_action": "retain_plan",
    }
    impact_sha256 = hashlib.sha256(
        json.dumps(
            impact_receipt,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    write_private(
        run_dir / "prompt-impact" / "attempt-0001.json",
        impact_receipt,
    )
    write_private(
        run_dir / "prompt-impact" / "ledger.json",
        {
            "schema": "agentic-sdlc/prompt-impact-ledger-v1",
            "workflow": "agentic-sdlc",
            "current": {
                "generation": 1,
                "revision": "r0001",
                "path": "attempt-0001.json",
                "sha256": impact_sha256,
            },
        },
    )


class SchedulerTests(unittest.TestCase):
    def test_independent_tasks_share_wave_and_dependencies_follow(self) -> None:
        tasks = parse_locked_plan(PLAN)
        waves = build_dependency_waves(tasks)
        self.assertEqual(
            [[task.task_id for task in wave] for wave in waves],
            [["TASK-001", "TASK-002"], ["TASK-003"]],
        )

    def test_overlapping_write_claims_serialize(self) -> None:
        text = PLAN.replace("exact: src/b.py", "prefix: src")
        waves = build_dependency_waves(parse_locked_plan(text))
        self.assertEqual(
            [[task.task_id for task in wave] for wave in waves],
            [["TASK-001"], ["TASK-002"], ["TASK-003"]],
        )

    def test_unknown_ownership_serializes(self) -> None:
        text = PLAN.replace("exact: src/b.py", "unknown")
        waves = build_dependency_waves(parse_locked_plan(text))
        self.assertEqual([task.task_id for task in waves[0]], ["TASK-001"])
        self.assertEqual([task.task_id for task in waves[1]], ["TASK-002"])

    def test_dependency_cycle_fails_closed(self) -> None:
        text = PLAN.replace(
            "### TASK-001\n\n- Requirements: REQ-001\n- Goal: implement the first independent behavior\n- Depends on: none",
            "### TASK-001\n\n- Requirements: REQ-001\n- Goal: implement the first independent behavior\n- Depends on: TASK-003",
        )
        with self.assertRaisesRegex(ExecutionError, "cycle") as raised:
            parse_locked_plan(text)
        self.assertEqual(raised.exception.code, "DEPENDENCY_CYCLE")

    def test_task_ids_must_be_contiguous(self) -> None:
        with self.assertRaises(ExecutionError) as raised:
            parse_locked_plan(PLAN.replace("TASK-002", "TASK-004"))
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_requirement_ids_must_be_canonical(self) -> None:
        with self.assertRaises(ExecutionError) as raised:
            parse_locked_plan(
                PLAN.replace("Requirements: REQ-001", "Requirements: story-1", 1)
            )
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_corrective_task_binds_diagnosis_and_regression_oracle(self) -> None:
        corrective = PLAN.replace(
            "- Rollback or stop conditions: stop on contract drift",
            "- Rollback or stop conditions: stop on contract drift\n"
            f"- Diagnosis: {'d' * 64}\n"
            "- Regression oracle: python3 -m unittest tests.test_regression",
            1,
        )
        task = parse_locked_plan(corrective)[0]
        self.assertEqual(task.diagnosis_id, "d" * 64)
        self.assertEqual(
            task.regression_oracle,
            "python3 -m unittest tests.test_regression",
        )

    def test_corrective_task_requires_diagnosis_and_oracle_as_a_pair(self) -> None:
        malformed = PLAN.replace(
            "- Rollback or stop conditions: stop on contract drift",
            "- Rollback or stop conditions: stop on contract drift\n"
            f"- Diagnosis: {'d' * 64}",
            1,
        )
        with self.assertRaises(ExecutionError) as raised:
            parse_locked_plan(malformed)
        self.assertEqual(raised.exception.code, "PLAN_INVALID")


class GitLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.origin = self.root / "origin.git"
        git(self.root, "init", "--bare", "-q", str(self.origin))
        self.project = self.root / "project"
        self.run_dir = self.root / "private" / "run-1"
        self.plan = self.run_dir / "plans" / "FEAT-001.plan.v1.md"
        self.project.mkdir(parents=True)
        docs = self.project / "docs"
        docs.mkdir()
        (docs / "requirements.md").write_text(REQUIREMENTS, encoding="utf-8")
        (docs / "design.md").write_text(DESIGN, encoding="utf-8")
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text(PLAN, encoding="utf-8")
        self.plan.with_suffix(self.plan.suffix + ".lock").write_text(
            "locked\n", encoding="utf-8"
        )
        seed_prompt_impact(self.run_dir, self.project)
        try:
            git(self.project, "init", "-b", "main")
        except AssertionError:
            git(self.project, "init")
            git(self.project, "branch", "-m", "main")
        git(self.project, "config", "user.name", "SDLC Test")
        git(self.project, "config", "user.email", "sdlc-test@example.com")
        (self.project / "README.md").write_text("fixture\n", encoding="utf-8")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-m", "initial")
        git(self.project, "remote", "add", "origin", str(self.origin))
        git(self.project, "push", "-u", "origin", "main")
        git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        git(self.project, "switch", "-c", "feature/test")
        self.base_head = git(self.project, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def prepare(self) -> dict:
        return prepare_execution(
            self.run_dir, self.project, "FEAT-001", self.plan, capacity=2
        )

    def install_scoped_specs(self, selected: Path) -> None:
        docs = selected / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "requirements.md").write_bytes(
            (self.project / "docs" / "requirements.md").read_bytes()
        )
        (docs / "design.md").write_bytes(
            (self.project / "docs" / "design.md").read_bytes()
        )

    def start_assignment(
        self, assignment: dict, wave_id: str, session: str | None = None
    ) -> dict:
        arm_task(
            self.run_dir,
            "FEAT-001",
            wave_id,
            assignment["task_id"],
            assignment["assignment_digest"],
        )
        return start_task(
            self.run_dir,
            "FEAT-001",
            wave_id,
            assignment["task_id"],
            assignment["assignment_digest"],
            session or f"test-session-{wave_id}-{assignment['task_id']}",
            Path(assignment["scope_cwd"]),
        )

    def complete_first_wave(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignments = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        for assignment in assignments:
            self.start_assignment(assignment, "WAVE-001")
            worker = Path(assignment["worktree"])
            (worker / "src").mkdir(exist_ok=True)
            filename = "a.py" if assignment["task_id"] == "TASK-001" else "b.py"
            (worker / "src" / filename).write_text("VALUE = 1\n", encoding="utf-8")
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "focused test passed",
                "review passed",
                f"feat: {assignment['task_id']}",
                summary=f"completed {assignment['task_id']}",
            )
        integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")
        complete_wave(self.run_dir, "FEAT-001", "WAVE-001", "combined tests passed")

    def append_prompt_revision(self) -> None:
        binding_path = self.run_dir / "prompt.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["revisions"].append(
            {
                "revision": "r0002",
                "sha256": "e" * 64,
                "intent_sha256": "f" * 64,
            }
        )
        write_private(binding_path, binding)

    def test_assignment_binds_root_intent_and_canonical_spec_receipt(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): bind specs")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.assertEqual(assignment["schema"], "agentic-sdlc/worker-assignment-v4")
        self.assertEqual(assignment["root_intent_sha256"], "b" * 64)
        self.assertEqual(
            assignment["project_spec_receipt"],
            {
                "schema": "maintain-project-specs.worker-receipt.v1",
                "requirements_sha256": hashlib.sha256(
                    REQUIREMENTS.encode("utf-8")
                ).hexdigest(),
                "design_sha256": hashlib.sha256(
                    DESIGN.encode("utf-8")
                ).hexdigest(),
            },
        )

    def test_new_prompt_revision_freezes_dispatch_until_impact_reconciliation(
        self,
    ) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): impact gate")
        binding_path = self.run_dir / "prompt.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["revisions"].append(
            {
                "revision": "r0002",
                "sha256": "e" * 64,
                "intent_sha256": "f" * 64,
            }
        )
        binding_path.write_text(
            json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        binding_path.chmod(0o600)

        with self.assertRaises(ExecutionError) as caught:
            prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(caught.exception.code, "PROMPT_IMPACT_REQUIRED")
        self.assertFalse(
            execution_core.assignment_path(
                self.run_dir, "FEAT-001", "WAVE-001", "TASK-001"
            ).exists()
        )

    def test_new_prompt_revision_freezes_arm_and_start_before_mutation(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): impact gate")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.append_prompt_revision()

        for action in (
            lambda: arm_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                assignment["assignment_digest"],
            ),
            lambda: start_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                assignment["assignment_digest"],
                "stale-impact-worker",
                Path(assignment["scope_cwd"]),
            ),
        ):
            with self.assertRaises(ExecutionError) as caught:
                action()
            self.assertEqual(caught.exception.code, "PROMPT_IMPACT_REQUIRED")
        task = json.loads(
            task_path(
                self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"]
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(task["status"], "assigned")
        self.assertIsNone(task["dispatched_at"])

    def test_new_prompt_revision_freezes_wave_completion_and_feature_seal(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): impact gate")
        assignments = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        for assignment in assignments:
            self.start_assignment(assignment, "WAVE-001")
            worker = Path(assignment["worktree"])
            (worker / "src").mkdir(exist_ok=True)
            filename = "a.py" if assignment["task_id"] == "TASK-001" else "b.py"
            (worker / "src" / filename).write_text("VALUE = 1\n", encoding="utf-8")
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "focused test passed",
                "review passed",
                f"feat: {assignment['task_id']}",
                summary=f"completed {assignment['task_id']}",
            )
        integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.append_prompt_revision()

        with self.assertRaises(ExecutionError) as completion:
            complete_wave(
                self.run_dir, "FEAT-001", "WAVE-001", "combined tests passed"
            )
        self.assertEqual(completion.exception.code, "PROMPT_IMPACT_REQUIRED")
        wave = json.loads(
            wave_path(self.run_dir, "FEAT-001", "WAVE-001").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(wave["status"], "integrated")

        state_path = coordinator_path(self.run_dir, "FEAT-001")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "integrated"
        expected_integration_head = state["integration_head"]
        write_private(state_path, state)

        with self.assertRaises(ExecutionError) as caught:
            seal_feature(
                self.run_dir,
                "FEAT-001",
                "final evidence passed",
                "feat(FEAT-001): stale impact must not seal",
            )
        self.assertEqual(caught.exception.code, "PROMPT_IMPACT_REQUIRED")
        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["status"], "integrated")
        self.assertEqual(persisted["integration_head"], expected_integration_head)

    @unittest.skipUnless(hasattr(os, "link"), "hard links are unavailable")
    def test_hardlinked_canonical_spec_freezes_progression(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): impact gate")
        design = self.project / "docs" / "design.md"
        backing = design.with_name("design.backing")
        design.rename(backing)
        os.link(backing, design)
        with self.assertRaises(ExecutionError) as caught:
            prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(caught.exception.code, "SPEC_CONFLICT")

    def test_existing_execution_without_impact_basis_requires_explicit_replan(self) -> None:
        self.prepare()
        binding_path = self.run_dir / "prompt.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["revisions"].append(
            {
                "revision": "r0002",
                "sha256": "e" * 64,
                "intent_sha256": "f" * 64,
            }
        )
        write_private(binding_path, binding)
        prior = json.loads(
            (self.run_dir / "prompt-impact" / "attempt-0001.json").read_text(
                encoding="utf-8"
            )
        )
        material = {
            **prior,
            "generation": 2,
            "revision": "r0002",
            "intent_sha256": "f" * 64,
            "effects": ["execution"],
            "plan_action": "replan_required",
        }
        material_sha256 = hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        write_private(
            self.run_dir / "prompt-impact" / "attempt-0002.json", material
        )
        write_private(
            self.run_dir / "prompt-impact" / "ledger.json",
            {
                "schema": "agentic-sdlc/prompt-impact-ledger-v1",
                "workflow": "agentic-sdlc",
                "current": {
                    "generation": 2,
                    "revision": "r0002",
                    "path": "attempt-0002.json",
                    "sha256": material_sha256,
                },
            },
        )
        (self.run_dir / "prompt-impact" / "execution" / "FEAT-001.json").unlink()
        with self.assertRaises(ExecutionError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, "REPLAN_REQUIRED")

    def test_spec_drift_after_impact_settlement_freezes_dispatch(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): impact gate")
        design = self.project / "docs" / "design.md"
        design.write_text("drifted design\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as caught:
            prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(caught.exception.code, "REPLAN_REQUIRED")

    def test_later_no_effect_retains_plan_but_material_effect_requires_new_plan(
        self,
    ) -> None:
        coordinator = self.prepare()
        binding_path = self.run_dir / "prompt.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))

        def publish_revision(
            generation: int, revision: str, intent: str, effects: list[str]
        ) -> None:
            binding["revisions"].append(
                {
                    "revision": revision,
                    "sha256": str(generation) * 64,
                    "intent_sha256": intent,
                }
            )
            write_private(binding_path, binding)
            receipt = {
                "schema": "agentic-sdlc/prompt-impact-receipt-v1",
                "workflow": "agentic-sdlc",
                "generation": generation,
                "prompt_id": binding["prompt_id"],
                "revision": revision,
                "intent_sha256": intent,
                "spec_receipt_sha256": "d" * 64,
                "spec_transition_sha256": None,
                "requirements_sha256": hashlib.sha256(
                    (self.project / "docs" / "requirements.md").read_bytes()
                ).hexdigest(),
                "design_sha256": hashlib.sha256(
                    (self.project / "docs" / "design.md").read_bytes()
                ).hexdigest(),
                "effects": effects,
                "plan_action": "retain_plan" if not effects else "replan_required",
            }
            receipt_sha256 = hashlib.sha256(
                json.dumps(
                    receipt,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            write_private(
                self.run_dir
                / "prompt-impact"
                / f"attempt-{generation:04d}.json",
                receipt,
            )
            write_private(
                self.run_dir / "prompt-impact" / "ledger.json",
                {
                    "schema": "agentic-sdlc/prompt-impact-ledger-v1",
                    "workflow": "agentic-sdlc",
                    "current": {
                        "generation": generation,
                        "revision": revision,
                        "path": f"attempt-{generation:04d}.json",
                        "sha256": receipt_sha256,
                    },
                },
            )

        publish_revision(2, "r0002", "e" * 64, [])
        retained = execution_core.settle_prompt_impact_execution(
            self.run_dir,
            "FEAT-001",
            coordinator["plan_digest"],
            self.project,
        )
        self.assertEqual(retained["plan_basis_revision"], "r0001")
        self.assertEqual(retained["latest_settled_revision"], "r0002")

        publish_revision(3, "r0003", "f" * 64, ["execution"])
        with self.assertRaises(execution_core.PromptImpactError) as caught:
            execution_core.settle_prompt_impact_execution(
                self.run_dir,
                "FEAT-001",
                coordinator["plan_digest"],
                self.project,
            )
        self.assertEqual(caught.exception.code, "REPLAN_REQUIRED")

    def test_parallel_session_claim_is_atomic(self) -> None:
        def claim(task_id: str) -> str:
            try:
                _claim_worker_session(
                    self.run_dir,
                    "FEAT-001",
                    "WAVE-001",
                    task_id,
                    "a" * 64,
                )
            except ExecutionError as exc:
                return exc.code
            return "claimed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(claim, ("TASK-001", "TASK-002")))
        self.assertEqual(outcomes, ["FRESH_SESSION_REQUIRED", "claimed"])

    def test_session_claim_is_complete_before_atomic_publication(self) -> None:
        ready = threading.Event()
        publish = threading.Event()
        original_link = execution_core.os.link
        session_hash = "b" * 64
        claim_path = (
            execution_core.execution_dir(self.run_dir, "FEAT-001")
            / "sessions"
            / f"{session_hash}.json"
        )

        def paused_link(source: Path, target: Path, **kwargs: object) -> None:
            ready.set()
            if not publish.wait(timeout=5):
                raise OSError("timed out waiting to publish session claim")
            original_link(source, target, **kwargs)

        with mock.patch.object(execution_core.os, "link", side_effect=paused_link):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    _claim_worker_session,
                    self.run_dir,
                    "FEAT-001",
                    "WAVE-001",
                    "TASK-001",
                    session_hash,
                )
                self.assertTrue(ready.wait(timeout=5))
                self.assertFalse(claim_path.exists())
                publish.set()
                future.result(timeout=5)

        self.assertEqual(
            execution_core.read_json(claim_path),
            {
                "feature_id": "FEAT-001",
                "wave_id": "WAVE-001",
                "task_id": "TASK-001",
                "worker_session_hash": session_hash,
            },
        )

    def test_failed_session_claim_publication_leaves_no_partial_claim(self) -> None:
        session_hash = "c" * 64
        claim_path = (
            execution_core.execution_dir(self.run_dir, "FEAT-001")
            / "sessions"
            / f"{session_hash}.json"
        )
        with (
            mock.patch.object(
                execution_core.os,
                "link",
                side_effect=OSError("simulated publication crash"),
            ),
            self.assertRaises(ExecutionError) as raised,
        ):
            _claim_worker_session(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                "TASK-001",
                session_hash,
            )
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")
        self.assertFalse(claim_path.exists())
        self.assertEqual(list(claim_path.parent.glob(f".{claim_path.name}.*")), [])

    def test_parallel_task_start_has_one_owner(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        arm_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
        )

        def start(session: str) -> str:
            try:
                start_task(
                    self.run_dir,
                    "FEAT-001",
                    "WAVE-001",
                    assignment["task_id"],
                    assignment["assignment_digest"],
                    session,
                    Path(assignment["scope_cwd"]),
                )
            except ExecutionError as exc:
                return exc.code
            return "started"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(start, ("worker-a", "worker-b")))
        self.assertEqual(outcomes, ["WORKSPACE_BUSY", "started"])

    def test_parallel_recovery_transfers_once(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001", "old-worker")

        def recover(session: str) -> str:
            try:
                recover_task(
                    self.run_dir,
                    "FEAT-001",
                    "WAVE-001",
                    assignment["task_id"],
                    session,
                    Path(assignment["scope_cwd"]),
                    expected_attempt=1,
                    confirmed_stopped=True,
                )
            except ExecutionError as exc:
                return exc.code
            return "recovered"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(recover, ("worker-a", "worker-b")))
        self.assertEqual(outcomes, ["WORKSPACE_BUSY", "recovered"])

    def test_corrective_finish_requires_exact_oracle_evidence(self) -> None:
        oracle = "python3 -m unittest tests.test_regression"
        self.plan.write_text(
            PLAN.replace(
                "- Rollback or stop conditions: stop on contract drift",
                "- Rollback or stop conditions: stop on contract drift\n"
                f"- Diagnosis: {'d' * 64}\n"
                f"- Regression oracle: {oracle}",
                1,
            ),
            encoding="utf-8",
        )
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): corrective TDD")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001")
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir(exist_ok=True)
        (worker / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        common = (
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            "focused test passed",
            "review passed",
            "fix(FEAT-001): repair diagnosed regression",
        )
        with self.assertRaises(ExecutionError) as missing:
            finish_task(*common, summary="corrective handoff")
        self.assertEqual(missing.exception.code, "INTEGRATION_VALIDATION_FAILED")
        with self.assertRaises(ExecutionError) as wrong:
            finish_task(
                *common,
                summary="corrective handoff",
                regression_oracle_evidence={
                    "oracle": "a different oracle",
                    "outcome": "passed",
                    "evidence_reference": "evidence/wrong-oracle.txt",
                },
            )
        self.assertEqual(wrong.exception.code, "INTEGRATION_VALIDATION_FAILED")
        result = finish_task(
            *common,
            summary="corrective handoff",
            regression_oracle_evidence={
                "oracle": oracle,
                "outcome": "passed",
                "evidence_reference": "evidence/FEAT-001/original-oracle.txt",
            },
        )
        evidence = result["regression_oracle_evidence"]
        self.assertEqual(evidence["oracle"], oracle)
        self.assertEqual(evidence["outcome"], "passed")
        self.assertEqual(evidence["commit"], result["commit"])
        self.assertEqual(len(evidence["evidence_digest"]), 64)

    def test_nested_project_scope_is_persisted_and_enforced(self) -> None:
        selected = self.project / "services" / "a"
        sibling = self.project / "services" / "b"
        selected.mkdir(parents=True)
        sibling.mkdir(parents=True)
        self.install_scoped_specs(selected)
        (selected / ".keep").write_text("a\n", encoding="utf-8")
        (sibling / ".keep").write_text("b\n", encoding="utf-8")
        git(self.project, "add", "services")
        git(self.project, "commit", "-m", "add monorepo scopes")
        self.base_head = git(self.project, "rev-parse", "HEAD")
        scoped_plan = PLAN.replace("exact: src/", "exact: services/a/src/")
        self.plan.write_text(scoped_plan, encoding="utf-8")
        coordinator = prepare_execution(
            self.run_dir, selected, "FEAT-001", self.plan, capacity=2
        )
        self.assertEqual(coordinator["git_root"], str(self.project.resolve()))
        self.assertEqual(coordinator["project_scope"], "services/a")
        integration = Path(coordinator["integration_worktree"])
        (integration / "services" / "a" / "tests").mkdir()
        (integration / "services" / "a" / "tests" / "test_feature.py").write_text(
            "# red contract\n", encoding="utf-8"
        )
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): scoped TDD")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.assertEqual(
            assignment["scope_cwd"],
            str(Path(assignment["worktree"]) / "services" / "a"),
        )
        self.start_assignment(assignment, "WAVE-001")
        outside = Path(assignment["worktree"]) / "services" / "b" / "outside.py"
        outside.write_text("OUTSIDE = True\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as caught:
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "focused test passed",
                "review passed",
                "feat(FEAT-001): escaped change",
                summary="escaped-change handoff",
            )
        self.assertEqual(caught.exception.code, "WORKER_SCOPE_VIOLATION")

    def test_tdd_seal_rejects_changes_outside_nested_project_scope(self) -> None:
        selected = self.project / "services" / "a"
        sibling = self.project / "services" / "b"
        selected.mkdir(parents=True)
        sibling.mkdir(parents=True)
        self.install_scoped_specs(selected)
        (selected / ".keep").write_text("a\n", encoding="utf-8")
        (sibling / ".keep").write_text("b\n", encoding="utf-8")
        git(self.project, "add", "services")
        git(self.project, "commit", "-m", "add monorepo scopes")
        scoped_plan = PLAN.replace("exact: src/", "exact: services/a/src/")
        self.plan.write_text(scoped_plan, encoding="utf-8")
        coordinator = prepare_execution(
            self.run_dir, selected, "FEAT-001", self.plan, capacity=2
        )
        integration = Path(coordinator["integration_worktree"])
        outside = integration / "services" / "b" / "test_outside.py"
        outside.write_text("# outside scope\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as caught:
            seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): scoped TDD")
        self.assertEqual(caught.exception.code, "REPLAN_REQUIRED")
        self.assertEqual(git(integration, "diff", "--cached", "--name-only"), "")

    def test_claim_outside_nested_project_scope_fails_before_resources(self) -> None:
        selected = self.project / "services" / "a"
        selected.mkdir(parents=True)
        self.install_scoped_specs(selected)
        (selected / ".keep").write_text("a\n", encoding="utf-8")
        git(self.project, "add", "services")
        git(self.project, "commit", "-m", "add selected scope")
        with self.assertRaises(ExecutionError) as caught:
            prepare_execution(self.run_dir, selected, "FEAT-001", self.plan, capacity=2)
        self.assertEqual(caught.exception.code, "REPLAN_REQUIRED")
        self.assertFalse(coordinator_path(self.run_dir, "FEAT-001").exists())

    def test_claim_crossing_gitlink_fails_before_resources(self) -> None:
        git(
            self.project,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.base_head},src/vendor",
        )
        git(self.project, "commit", "-m", "add uninitialized gitlink")
        self.plan.write_text(
            PLAN.replace("exact: src/a.py", "exact: src/vendor/a.py"),
            encoding="utf-8",
        )

        with self.assertRaises(ExecutionError) as caught:
            self.prepare()

        self.assertEqual(caught.exception.code, "UNSUPPORTED_SUBMODULE_SCOPE")
        self.assertFalse(coordinator_path(self.run_dir, "FEAT-001").exists())
        self.assertFalse((self.project / "src" / "vendor" / ".git").exists())

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX semantics")
    def test_prefix_claim_containing_tracked_symlink_fails_before_resources(
        self,
    ) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (self.project / "src").mkdir()
        (self.project / "src" / "linked").symlink_to(outside, target_is_directory=True)
        git(self.project, "add", "src/linked")
        git(self.project, "commit", "-m", "add tracked symlink")
        self.plan.write_text(
            PLAN.replace("exact: src/a.py", "prefix: src"), encoding="utf-8"
        )

        with self.assertRaises(ExecutionError) as caught:
            self.prepare()

        self.assertEqual(caught.exception.code, "UNSUPPORTED_SYMLINK_SCOPE")
        self.assertFalse(coordinator_path(self.run_dir, "FEAT-001").exists())

    def test_worker_must_be_armed_before_start(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        queued = watch_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
        )
        self.assertEqual(queued["status"], "QUEUED")
        with self.assertRaises(ExecutionError) as caught:
            start_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                assignment["assignment_digest"],
                "unarmed-session",
                Path(assignment["scope_cwd"]),
            )
        self.assertEqual(caught.exception.code, "TASK_NOT_ARMED")

    def test_arm_start_heartbeat_and_watch_liveness(self) -> None:
        fixed = datetime(2026, 8, 5, tzinfo=timezone.utc)
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        armed = arm_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed,
        )
        repeated = arm_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed + timedelta(seconds=10),
        )
        self.assertEqual(repeated["dispatched_at"], armed["dispatched_at"])
        pending = watch_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed + timedelta(seconds=30),
        )
        self.assertEqual(pending["status"], "PENDING_START")
        start_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            "liveness-session",
            Path(assignment["scope_cwd"]),
            clock=lambda: fixed + timedelta(seconds=30),
        )
        heartbeat = heartbeat_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            "implementing",
            "liveness-session",
            Path(assignment["scope_cwd"]),
            clock=lambda: fixed + timedelta(seconds=60),
        )
        self.assertEqual(heartbeat["heartbeat_sequence"], 2)
        warning = watch_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed + timedelta(seconds=270),
        )
        self.assertEqual(warning["status"], "ACTIVE")
        self.assertEqual(warning["warning"], "READ_ONLY_DEADLINE_NEAR")
        stalled = watch_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed + timedelta(seconds=301),
        )
        self.assertEqual(stalled["status"], "WORKER_STALLED")
        read_only = watch_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed + timedelta(seconds=330),
        )
        self.assertEqual(read_only["status"], "WORKER_READ_ONLY_TIMEOUT")

    def test_prestart_mutation_and_timeout_fail_closed(self) -> None:
        fixed = datetime(2026, 8, 5, tzinfo=timezone.utc)
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        arm_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed,
        )
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir()
        changed = worker / "src" / "a.py"
        changed.write_text("VALUE = 1\n", encoding="utf-8")
        mutated = watch_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed + timedelta(seconds=1),
        )
        self.assertEqual(mutated["status"], "WORKER_PRESTART_MUTATION")
        changed.unlink()
        with self.assertRaises(ExecutionError) as caught:
            start_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                assignment["assignment_digest"],
                "late-session",
                Path(assignment["scope_cwd"]),
                clock=lambda: fixed + timedelta(seconds=60),
            )
        self.assertEqual(caught.exception.code, "WORKER_PRESTART_TIMEOUT")

    def test_confirmed_prestart_requeue_allows_a_fresh_arm(self) -> None:
        fixed = datetime(2026, 8, 5, tzinfo=timezone.utc)
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        armed = arm_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed,
        )
        dispatched_at = str(armed["dispatched_at"])
        timed_out = watch_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed + timedelta(seconds=60),
        )
        self.assertEqual(timed_out["status"], "WORKER_PRESTART_TIMEOUT")
        with self.assertRaises(ExecutionError) as unconfirmed:
            requeue_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                assignment["assignment_digest"],
                dispatched_at,
                confirmed_stopped=False,
            )
        self.assertEqual(unconfirmed.exception.code, "WORKSPACE_BUSY")
        requeued = requeue_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            dispatched_at,
            confirmed_stopped=True,
        )
        self.assertIsNone(requeued["dispatched_at"])
        with self.assertRaises(ExecutionError) as stale:
            requeue_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                assignment["assignment_digest"],
                dispatched_at,
                confirmed_stopped=True,
            )
        self.assertEqual(stale.exception.code, "WORKSPACE_BUSY")
        arm_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed + timedelta(seconds=61),
        )
        started = start_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            "requeued-session",
            Path(assignment["scope_cwd"]),
            clock=lambda: fixed + timedelta(seconds=62),
        )
        self.assertEqual(started["task_id"], assignment["task_id"])

    def test_prestart_scope_violations_use_scope_error(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        arm_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
        )
        worker = Path(assignment["worktree"])
        outside = worker / "outside.py"
        outside.write_text("VALUE = 1\n", encoding="utf-8")
        watched = watch_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
        )
        self.assertEqual(watched["status"], "WORKER_SCOPE_VIOLATION")
        with self.assertRaises(ExecutionError) as caught:
            start_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                assignment["assignment_digest"],
                "scope-violation-session",
                Path(assignment["scope_cwd"]),
            )
        self.assertEqual(caught.exception.code, "WORKER_SCOPE_VIOLATION")
        outside.unlink()

        (worker / "src").mkdir()
        special = worker / "src" / "a.py"
        special.symlink_to(self.root / "outside")
        git(worker, "add", "src/a.py")
        special_watch = watch_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
        )
        self.assertEqual(special_watch["status"], "WORKER_SCOPE_VIOLATION")
        git(worker, "restore", "--staged", ":/")
        special.unlink()
        git(worker, "commit", "--allow-empty", "-m", "worker moved HEAD")
        moved = watch_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
        )
        self.assertEqual(moved["status"], "WORKER_SCOPE_VIOLATION")

    def test_progress_does_not_bypass_maximum_runtime(self) -> None:
        fixed = datetime(2026, 8, 5, tzinfo=timezone.utc)
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        arm_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed,
        )
        start_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            "long-session",
            Path(assignment["scope_cwd"]),
            clock=lambda: fixed + timedelta(seconds=1),
        )
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir()
        (worker / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        for seconds in range(200, 1800, 200):
            heartbeat_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                assignment["assignment_digest"],
                "implementing",
                "long-session",
                Path(assignment["scope_cwd"]),
                clock=lambda seconds=seconds: fixed + timedelta(seconds=seconds),
            )
        watched = watch_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            assignment["assignment_digest"],
            clock=lambda: fixed + timedelta(seconds=1801),
        )
        self.assertEqual(watched["status"], "WORKER_TIMEOUT")

    def test_interrupted_worker_recovery_transfers_claimed_dirty_state(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "tests").mkdir()
        (integration / "tests" / "test_feature.py").write_text(
            "# red contract\n", encoding="utf-8"
        )
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): recovery TDD")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001", "old-worker-session")
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir(exist_ok=True)
        (worker / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as unconfirmed:
            recover_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "new-worker-session",
                Path(assignment["scope_cwd"]),
                expected_attempt=1,
                confirmed_stopped=False,
            )
        self.assertEqual(unconfirmed.exception.code, "WORKSPACE_BUSY")
        recover_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            "new-worker-session",
            Path(assignment["scope_cwd"]),
            expected_attempt=1,
            confirmed_stopped=True,
        )
        state = json.loads(
            task_path(
                self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"]
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(state["attempt"], 2)
        self.assertEqual(len(state["worker_session_hash_history"]), 2)
        self.assertNotIn("old-worker-session", json.dumps(state))
        self.assertNotIn("new-worker-session", json.dumps(state))
        result = finish_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            "focused test passed",
            "review passed",
            "feat(FEAT-001): recovered task",
            summary="recovered-task handoff",
        )
        self.assertEqual(result["attempt"], 2)

    def test_task_finish_recovers_commit_before_result_write(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001")
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir()
        (worker / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        result_file = execution_core.result_path(
            self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"]
        )
        original_write = execution_core.write_json_atomic
        failed = False

        def fail_result_once(path: Path, value: dict) -> None:
            nonlocal failed
            if path == result_file and not failed:
                failed = True
                raise OSError("simulated result publication crash")
            original_write(path, value)

        with (
            mock.patch.object(
                execution_core, "write_json_atomic", side_effect=fail_result_once
            ),
            self.assertRaisesRegex(OSError, "result publication crash"),
        ):
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "focused test passed",
                "review passed",
                "feat: recover task finish",
                summary="recoverable finish",
            )

        committed = git(worker, "rev-parse", "HEAD")
        persisted = json.loads(
            task_path(
                self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"]
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["status"], "running")
        self.assertIsNotNone(persisted["finish_intent"])
        recovered = finish_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            "focused test passed",
            "review passed",
            "feat: recover task finish",
            summary="recoverable finish",
        )
        self.assertEqual(recovered["commit"], committed)
        self.assertEqual(
            git(worker, "rev-list", "--count", f"{assignment['base_head']}..HEAD"),
            "1",
        )

    def test_task_finish_recovers_result_before_task_state_write(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001")
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir()
        (worker / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        task_file = task_path(
            self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"]
        )
        original_write = execution_core.write_json_atomic
        failed = False

        def fail_committed_state_once(path: Path, value: dict) -> None:
            nonlocal failed
            if path == task_file and value.get("status") == "committed" and not failed:
                failed = True
                raise OSError("simulated task state publication crash")
            original_write(path, value)

        with (
            mock.patch.object(
                execution_core,
                "write_json_atomic",
                side_effect=fail_committed_state_once,
            ),
            self.assertRaisesRegex(OSError, "task state publication crash"),
        ):
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "focused test passed",
                "review passed",
                "feat: recover task state",
                summary="recoverable task state",
            )

        result_file = execution_core.result_path(
            self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"]
        )
        before = result_file.read_bytes()
        recovered = finish_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            "focused test passed",
            "review passed",
            "feat: recover task state",
            summary="recoverable task state",
        )
        self.assertEqual(result_file.read_bytes(), before)
        self.assertEqual(recovered["commit"], git(worker, "rev-parse", "HEAD"))
        persisted = json.loads(task_file.read_text(encoding="utf-8"))
        self.assertEqual(persisted["status"], "committed")
        self.assertEqual(persisted["result_digest"], recovered["result_digest"])

    def test_interrupted_worker_recovery_accepts_clean_base(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "tests").mkdir()
        (integration / "tests" / "test_feature.py").write_text(
            "# red contract\n", encoding="utf-8"
        )
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): recovery TDD")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001", "old-clean-session")
        recovered = recover_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            assignment["task_id"],
            "fresh-clean-session",
            Path(assignment["scope_cwd"]),
            expected_attempt=1,
            confirmed_stopped=True,
        )
        self.assertEqual(
            recovered["assignment_digest"], assignment["assignment_digest"]
        )

    def test_interrupted_worker_recovery_rejects_worker_created_commit(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "tests").mkdir()
        (integration / "tests" / "test_feature.py").write_text(
            "# red contract\n", encoding="utf-8"
        )
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): recovery TDD")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001", "old-commit-session")
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir()
        (worker / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(worker, "add", "src/a.py")
        git(worker, "commit", "-m", "feat: interrupted worker commit")
        with self.assertRaises(ExecutionError) as caught:
            recover_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "fresh-commit-session",
                Path(assignment["scope_cwd"]),
                expected_attempt=1,
                confirmed_stopped=True,
            )
        self.assertEqual(caught.exception.code, "WORKER_SCOPE_VIOLATION")

    def test_one_worker_session_cannot_own_two_tasks(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        first, second = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.start_assignment(first, "WAVE-001", "single-worker-session")
        with self.assertRaises(ExecutionError) as caught:
            self.start_assignment(second, "WAVE-001", "single-worker-session")
        self.assertEqual(caught.exception.code, "FRESH_SESSION_REQUIRED")

    def test_malformed_session_history_fails_closed(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        first, second = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        second_path = task_path(self.run_dir, "FEAT-001", "WAVE-001", second["task_id"])
        state = json.loads(second_path.read_text(encoding="utf-8"))
        state["worker_session_hash_history"] = ["malformed"]
        second_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(ExecutionError) as caught:
            self.start_assignment(first, "WAVE-001", "fresh-session")
        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")

    def test_committed_worker_session_cannot_start_another_task(self) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        first, second = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.start_assignment(first, "WAVE-001", "burned-worker-session")
        worker = Path(first["worktree"])
        (worker / "src").mkdir(exist_ok=True)
        (worker / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        finish_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            "TASK-001",
            "focused test passed",
            "review passed",
            "feat(FEAT-001): first task",
            summary="first-task handoff",
        )
        with self.assertRaises(ExecutionError) as caught:
            self.start_assignment(second, "WAVE-001", "burned-worker-session")
        self.assertEqual(caught.exception.code, "FRESH_SESSION_REQUIRED")
        self.assertEqual(Path(coordinator["integration_worktree"]).name, "integration")

    def test_capacity_batches_create_only_active_assignments(self) -> None:
        prepare_execution(self.run_dir, self.project, "FEAT-001", self.plan, capacity=1)
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        first_batch = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual([item["task_id"] for item in first_batch], ["TASK-001"])
        self.assertFalse(
            assignment_path(self.run_dir, "FEAT-001", "WAVE-001", "TASK-002").exists()
        )
        with self.assertRaises(ExecutionError) as incomplete:
            advance_batch(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(incomplete.exception.code, "EXECUTION_STATE_INVALID")
        self.start_assignment(first_batch[0], "WAVE-001")
        worker = Path(first_batch[0]["worktree"])
        (worker / "src").mkdir(exist_ok=True)
        (worker / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        finish_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            "TASK-001",
            "focused test passed",
            "review passed",
            "feat(FEAT-001): first batch",
            summary="first-batch handoff",
        )
        second_batch = advance_batch(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual([item["task_id"] for item in second_batch], ["TASK-002"])
        later_handoff = json.loads(
            Path(second_batch[0]["incoming_handoff_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            [item["summary"] for item in later_handoff["predecessors"]],
            ["first-batch handoff"],
        )

    def test_finish_rejects_sensitive_staged_content_without_leaking(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "tests").mkdir()
        (integration / "tests" / "test_feature.py").write_text(
            "# red contract\n", encoding="utf-8"
        )
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): security TDD")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001")
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir(exist_ok=True)
        sensitive = "sk-" + "x" * 24
        (worker / "src" / "a.py").write_text(
            "OPENAI_API_KEY = " + sensitive + "\n", encoding="utf-8"
        )
        with self.assertRaises(ExecutionError) as caught:
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "focused test passed",
                "review passed",
                "feat(FEAT-001): unsafe task",
                summary="unsafe-task handoff",
            )
        self.assertEqual(caught.exception.code, "SECURITY_BLOCKER")
        self.assertNotIn(sensitive, str(caught.exception))
        self.assertEqual(git(worker, "diff", "--cached", "--name-only"), "")
        self.assertEqual(git(worker, "rev-parse", "HEAD"), assignment["base_head"])

    def test_future_replan_replaces_only_resource_free_planned_waves(self) -> None:
        original = self.prepare()
        addition = """

### TASK-004

- Requirements: REQ-001
- Goal: implement the final follow-up behavior
- Depends on: TASK-003
- Write claims: exact: src/d.py
- Conflict domains: code:d
- Validation: python3 -m unittest tests.test_feature
- Done criteria: final follow-up passes
- Rollback or stop conditions: stop on contract drift
"""
        replacement = self.run_dir / "plans" / "FEAT-001.plan.v2.md"
        replacement.write_text(
            PLAN.replace("# FEAT-001 Plan v1", "# FEAT-001 Plan v2").replace(
                "implement the second independent behavior",
                "implement the revised second independent behavior",
            )
            + addition,
            encoding="utf-8",
        )
        replacement.with_suffix(replacement.suffix + ".lock").write_text(
            "locked\n", encoding="utf-8"
        )
        replanned = replan_future(self.run_dir, "FEAT-001", replacement, capacity=2)
        self.assertNotEqual(replanned["plan_digest"], original["plan_digest"])
        self.assertEqual(replanned["wave_ids"], ["WAVE-001", "WAVE-002", "WAVE-003"])
        revised = json.loads(
            task_path(self.run_dir, "FEAT-001", "WAVE-001", "TASK-002").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            revised["task"]["goal"],
            "implement the revised second independent behavior",
        )
        repeated = replan_future(self.run_dir, "FEAT-001", replacement, capacity=2)
        self.assertEqual(repeated["plan_digest"], replanned["plan_digest"])
        future_assignment = assignment_path(
            self.run_dir, "FEAT-001", "WAVE-003", "TASK-004"
        )
        future_assignment.parent.mkdir(parents=True)
        future_assignment.write_text("{}\n", encoding="utf-8")
        third = self.run_dir / "plans" / "FEAT-001.plan.v3.md"
        third.write_text(
            replacement.read_text(encoding="utf-8").replace(
                "# FEAT-001 Plan v2", "# FEAT-001 Plan v3"
            ),
            encoding="utf-8",
        )
        third.with_suffix(third.suffix + ".lock").write_text(
            "locked\n", encoding="utf-8"
        )
        with self.assertRaises(ExecutionError) as blocked:
            replan_future(self.run_dir, "FEAT-001", third, capacity=2)
        self.assertEqual(blocked.exception.code, "REPLAN_REQUIRED")

    def test_corrective_replan_preserves_completed_task_definitions_and_digests(
        self,
    ) -> None:
        self.complete_first_wave()
        original_task_records = {
            task_id: json.loads(
                task_path(self.run_dir, "FEAT-001", "WAVE-001", task_id).read_text(
                    encoding="utf-8"
                )
            )
            for task_id in ("TASK-001", "TASK-002")
        }
        addition = """

### TASK-004

- Requirements: REQ-001
- Goal: implement the diagnosis-bound corrective behavior
- Depends on: TASK-003
- Write claims: exact: src/d.py
- Conflict domains: code:d
- Validation: run the original evaluator regression oracle
- Done criteria: original evaluator oracle and affected checks pass
- Rollback or stop conditions: stop on diagnosis or contract drift
- Diagnosis: dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
- Regression oracle: original AC-001 evaluator oracle
"""
        replacement = self.run_dir / "plans" / "FEAT-001.plan.v2.md"
        replacement.write_text(
            PLAN.replace("# FEAT-001 Plan v1", "# FEAT-001 Plan v2") + addition,
            encoding="utf-8",
        )
        replacement.with_suffix(replacement.suffix + ".lock").write_text(
            "locked\n", encoding="utf-8"
        )
        replanned = replan_future(self.run_dir, "FEAT-001", replacement, capacity=2)
        self.assertEqual(replanned["wave_ids"], ["WAVE-001", "WAVE-002", "WAVE-003"])
        corrective = json.loads(
            task_path(self.run_dir, "FEAT-001", "WAVE-003", "TASK-004").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(corrective["task"]["diagnosis_id"], "d" * 64)
        self.assertEqual(
            corrective["task"]["regression_oracle"],
            "original AC-001 evaluator oracle",
        )
        for task_id, original in original_task_records.items():
            preserved = json.loads(
                task_path(self.run_dir, "FEAT-001", "WAVE-001", task_id).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(preserved["task"], original["task"])
            self.assertEqual(
                preserved["task_definition_digest"],
                original["task_definition_digest"],
            )

    def test_corrective_replan_rejects_completed_task_definition_drift(self) -> None:
        self.complete_first_wave()
        replacement = self.run_dir / "plans" / "FEAT-001.plan.v2.md"
        replacement.write_text(
            PLAN.replace("# FEAT-001 Plan v1", "# FEAT-001 Plan v2").replace(
                "implement the first independent behavior",
                "reinterpret the completed first behavior",
            )
            + """

### TASK-004

- Requirements: REQ-001
- Goal: implement the diagnosis-bound corrective behavior
- Depends on: TASK-003
- Write claims: exact: src/d.py
- Conflict domains: code:d
- Validation: run the original evaluator regression oracle
- Done criteria: original evaluator oracle and affected checks pass
- Rollback or stop conditions: stop on diagnosis or contract drift
""",
            encoding="utf-8",
        )
        replacement.with_suffix(replacement.suffix + ".lock").write_text(
            "locked\n", encoding="utf-8"
        )
        with self.assertRaises(ExecutionError) as blocked:
            replan_future(self.run_dir, "FEAT-001", replacement, capacity=2)
        self.assertEqual(blocked.exception.code, "REPLAN_REQUIRED")
        self.assertIn("task definition", str(blocked.exception))

    def test_three_task_golden_path_and_exact_promotion(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base_head)
        (integration / "tests").mkdir()
        (integration / "tests" / "test_feature.py").write_text(
            "# red-first contract\n", encoding="utf-8"
        )
        sealed_tdd = seal_tdd_base(
            self.run_dir, "FEAT-001", "test(FEAT-001): define behavior"
        )
        self.assertNotEqual(sealed_tdd["integration_head"], self.base_head)

        first_wave = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(
            [item["task_id"] for item in first_wave], ["TASK-001", "TASK-002"]
        )
        first_handoff = json.loads(
            Path(first_wave[0]["incoming_handoff_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(first_handoff["dependencies"], [])
        self.assertEqual(first_handoff["predecessors"], [])
        for assignment in reversed(first_wave):
            self.start_assignment(assignment, "WAVE-001")
            worker = Path(assignment["worktree"])
            target = (
                worker
                / "src"
                / ("a.py" if assignment["task_id"] == "TASK-001" else "b.py")
            )
            target.parent.mkdir(exist_ok=True)
            target.write_text(f"VALUE = '{assignment['task_id']}'\n", encoding="utf-8")
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "focused test passed",
                "code review passed",
                f"feat(FEAT-001): {assignment['task_id']}",
                summary=f"completed {assignment['task_id']}",
            )
        integrated = integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(integrated["merged_task_ids"], ["TASK-001", "TASK-002"])
        complete_wave(self.run_dir, "FEAT-001", "WAVE-001", "combined tests passed")

        second_wave = prepare_wave(self.run_dir, "FEAT-001", "WAVE-002")
        assignment = second_wave[0]
        dependent_handoff = json.loads(
            Path(assignment["incoming_handoff_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            [item["task_id"] for item in dependent_handoff["predecessors"]],
            ["TASK-001", "TASK-002"],
        )
        self.assertEqual(
            [item["summary"] for item in dependent_handoff["predecessors"]],
            ["completed TASK-001", "completed TASK-002"],
        )
        self.start_assignment(assignment, "WAVE-002")
        worker = Path(assignment["worktree"])
        (worker / "src" / "c.py").write_text("VALUE = 'TASK-003'\n", encoding="utf-8")
        finish_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-002",
            "TASK-003",
            "focused test passed",
            "code review passed",
            "feat(FEAT-001): TASK-003",
            summary="completed TASK-003",
        )
        integrate_wave(self.run_dir, "FEAT-001", "WAVE-002")
        complete_wave(self.run_dir, "FEAT-001", "WAVE-002", "combined tests passed")

        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base_head)
        (integration / "docs").mkdir(exist_ok=True)
        (integration / "docs" / "release.md").write_text("done\n", encoding="utf-8")
        sealed = seal_feature(
            self.run_dir,
            "FEAT-001",
            "validation, tests, evaluation, docs, and spec alignment passed",
            "feat(FEAT-001): seal feature",
        )
        promoted = promote_feature(self.run_dir, "FEAT-001", "final evidence passed")
        self.assertEqual(promoted["status"], "done")
        self.assertEqual(
            git(self.project, "rev-parse", "HEAD"), sealed["integration_head"]
        )
        self.assertEqual(
            (self.project / "src" / "a.py").read_text(), "VALUE = 'TASK-001'\n"
        )
        self.assertEqual(
            (self.project / "src" / "b.py").read_text(), "VALUE = 'TASK-002'\n"
        )
        self.assertEqual(
            (self.project / "src" / "c.py").read_text(), "VALUE = 'TASK-003'\n"
        )
        self.assertNotIn(
            "codex/sdlc/", git(self.project, "branch", "--format=%(refname:short)")
        )
        listed = git(self.project, "worktree", "list", "--porcelain")
        self.assertEqual(listed.count("worktree "), 1)

    def test_dirty_project_is_rejected(self) -> None:
        (self.project / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "WORKTREE_CONFLICT")

    def test_prepare_on_default_creates_and_uses_promotion_branch(self) -> None:
        git(self.project, "switch", "main")
        coordinator = self.prepare()
        self.assertTrue(coordinator["base_branch"].startswith("feature/sdlc-"))
        self.assertNotEqual(coordinator["base_branch"], coordinator["default_branch"])
        self.assertEqual(coordinator["promotion_source"], "auto-created")
        self.assertEqual(
            git(self.project, "branch", "--show-current"),
            coordinator["base_branch"],
        )

    def test_tdd_seal_recovers_commit_completed_before_state_write(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        message = "test(FEAT-001): interrupted TDD seal"
        append_journal(
            journal_path(self.run_dir, "FEAT-001", "coordinator"),
            {
                "event": "tdd_seal_intent",
                "base": coordinator["integration_head"],
                "message": message,
            },
        )
        (integration / "test_contract.py").write_text("# red first\n", encoding="utf-8")
        git(integration, "add", "test_contract.py")
        git(integration, "commit", "-m", message)
        recovered = seal_tdd_base(self.run_dir, "FEAT-001", message)
        self.assertEqual(recovered["status"], "tdd_sealed")
        self.assertEqual(
            recovered["integration_head"], git(integration, "rev-parse", "HEAD")
        )

    def test_tdd_seal_rejects_unjournaled_head_drift(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "unexpected.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(integration, "add", "unexpected.py")
        git(integration, "commit", "-m", "test(FEAT-001): expected message")
        with self.assertRaises(ExecutionError) as raised:
            seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): expected message")
        self.assertEqual(raised.exception.code, "WORKTREE_CONFLICT")

    def test_missing_plan_lock_is_rejected(self) -> None:
        self.plan.with_suffix(self.plan.suffix + ".lock").unlink()
        with self.assertRaises(ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_symlinked_plan_lock_is_rejected(self) -> None:
        lock = self.plan.with_suffix(self.plan.suffix + ".lock")
        lock.unlink()
        target = self.run_dir / "lock-target"
        target.write_text("locked\n", encoding="utf-8")
        lock.symlink_to(target)
        with self.assertRaises(ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_plan_outside_private_run_is_rejected(self) -> None:
        outside = self.root / "outside-plan.md"
        outside.write_text(PLAN, encoding="utf-8")
        outside.with_suffix(outside.suffix + ".lock").write_text(
            "locked\n", encoding="utf-8"
        )
        with self.assertRaises(ExecutionError) as raised:
            prepare_execution(
                self.run_dir, self.project, "FEAT-001", outside, capacity=2
            )
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_feature_id_path_traversal_is_rejected(self) -> None:
        with self.assertRaises(ExecutionError) as raised:
            prepare_execution(
                self.run_dir, self.project, "../FEAT-001", self.plan, capacity=2
            )
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")

    def test_plan_heading_must_match_feature(self) -> None:
        self.plan.write_text(PLAN.replace("# FEAT-001", "# FEAT-002"), encoding="utf-8")
        with self.assertRaises(ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX semantics")
    def test_symlinked_write_claim_is_rejected(self) -> None:
        outside = self.root / "outside-src"
        outside.mkdir()
        (self.project / "src").symlink_to(outside, target_is_directory=True)
        git(self.project, "add", "src")
        git(self.project, "commit", "-m", "add symlink fixture")
        with self.assertRaises(ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "UNSUPPORTED_SYMLINK_SCOPE")

    def test_existing_internal_branch_is_rejected(self) -> None:
        git(
            self.project,
            "branch",
            "codex/sdlc/run-1/feat-001/integration",
            self.base_head,
        )
        with self.assertRaises(ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "WORKTREE_COLLISION")

    def test_worker_outside_write_claim_requires_replan(self) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001")
        worker = Path(assignment["worktree"])
        (worker / "outside.py").write_text("outside = True\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as raised:
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "validation passed",
                "review passed",
                "feat: invalid",
                summary="invalid-path handoff",
            )
        self.assertEqual(raised.exception.code, "WORKER_SCOPE_VIOLATION")
        self.assertEqual(git(worker, "diff", "--cached", "--name-only"), "")
        self.assertEqual(
            git(self.project, "rev-parse", "HEAD"), coordinator["base_head"]
        )

    def test_worker_cannot_change_project_specs_through_a_broad_claim(self) -> None:
        self.plan.write_text(
            PLAN.replace("exact: src/a.py", "prefix: docs", 1), encoding="utf-8"
        )
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001")
        worker = Path(assignment["worktree"])
        requirements = worker / "docs/requirements.md"
        requirements.write_text(REQUIREMENTS + "\nworker edit\n", encoding="utf-8")

        with self.assertRaises(ExecutionError) as raised:
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "validation passed",
                "review passed",
                "feat: invalid project spec write",
                summary="invalid project-spec handoff",
            )

        self.assertEqual(raised.exception.code, "WORKER_SCOPE_VIOLATION")
        self.assertEqual(git(worker, "diff", "--cached", "--name-only"), "")

    def test_worker_cannot_hide_project_spec_rename_in_a_broad_claim(self) -> None:
        self.plan.write_text(
            PLAN.replace("exact: src/a.py", "prefix: src", 1), encoding="utf-8"
        )
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001")
        worker = Path(assignment["worktree"])
        (worker / "src").mkdir(exist_ok=True)
        (worker / "docs/requirements.md").rename(worker / "src/moved-requirements.md")

        with self.assertRaises(ExecutionError) as raised:
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "validation passed",
                "review passed",
                "feat: invalid project spec rename",
                summary="invalid project-spec rename handoff",
            )

        self.assertEqual(raised.exception.code, "WORKER_SCOPE_VIOLATION")
        self.assertEqual(git(worker, "diff", "--cached", "--name-only"), "")

    def test_wave_prepare_adopts_exact_interrupted_worker_resource(self) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        integration = Path(coordinator["integration_worktree"])
        current_head = git(integration, "rev-parse", "HEAD")
        branch_name = "codex/sdlc/run-1/feat-001/wave-001/task-001"
        worker = (
            self.run_dir / "worktrees" / "FEAT-001" / "waves" / "WAVE-001" / "TASK-001"
        )
        git(integration, "branch", branch_name, current_head)
        worker.parent.mkdir(parents=True)
        git(
            integration,
            "worktree",
            "add",
            "--lock",
            "--reason",
            "interrupted fixture",
            str(worker),
            branch_name,
        )
        assignments = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(assignments[0]["worktree"], str(worker.resolve()))
        self.assertTrue(
            self.run_dir.joinpath(
                "execution", "FEAT-001", "assignments", "WAVE-001", "TASK-001.json"
            ).is_file()
        )

    def test_wave_prepare_recovers_branch_created_before_worktree(self) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        integration = Path(coordinator["integration_worktree"])
        branch_name = "codex/sdlc/run-1/feat-001/wave-001/task-001"
        git(integration, "branch", branch_name, "HEAD")
        assignments = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        worker = Path(assignments[0]["worktree"])
        self.assertTrue(worker.is_dir())
        self.assertEqual(git(worker, "branch", "--show-current"), branch_name)

    def test_prepare_recovers_branch_created_before_integration_worktree(self) -> None:
        common_raw = git(self.project, "rev-parse", "--git-common-dir")
        common = Path(common_raw)
        if not common.is_absolute():
            common = (self.project / common).resolve()
        branch_name = "codex/sdlc/run-1/feat-001/integration"
        integration = self.run_dir / "worktrees" / "FEAT-001" / "integration"
        git(self.project, "branch", branch_name, self.base_head)
        state_path = coordinator_path(self.run_dir, "FEAT-001")
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps(
                {
                    "schema": "agentic-sdlc/execution-coordinator-v7",
                    "state_version": 7,
                    "feature_id": "FEAT-001",
                    "run_id": "run-1",
                    "project_root": str(self.project.resolve()),
                    "git_root": str(self.project.resolve()),
                    "selected_project_root": str(self.project.resolve()),
                    "project_scope": ".",
                    "git_common_dir": str(common),
                    "base_branch": "feature/test",
                    "base_head": self.base_head,
                    "default_remote": "origin",
                    "default_branch": "main",
                    "default_ref": "origin/main",
                    "default_head": self.base_head,
                    "promotion_source": "existing",
                    "plan_path": str(self.plan),
                    "plan_digest": hashlib.sha256(self.plan.read_bytes()).hexdigest(),
                    "capacity": 2,
                    "status": "blocked",
                    "integration_branch": branch_name,
                    "integration_worktree": str(integration),
                    "integration_head": self.base_head,
                    "wave_ids": ["WAVE-001", "WAVE-002"],
                    "active_wave": None,
                    "promoted_head": None,
                    "cleanup_retained": [],
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        recovered = self.prepare()
        self.assertEqual(recovered["status"], "prepared")
        self.assertTrue(integration.is_dir())

    def test_integrate_recovers_merge_completed_before_state_write(self) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignments = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        for assignment in assignments:
            self.start_assignment(assignment, "WAVE-001")
            worker = Path(assignment["worktree"])
            filename = "a.py" if assignment["task_id"] == "TASK-001" else "b.py"
            (worker / "src").mkdir(exist_ok=True)
            (worker / "src" / filename).write_text("VALUE = 1\n", encoding="utf-8")
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "validation passed",
                "review passed",
                f"feat: {assignment['task_id']}",
                summary=f"completed {assignment['task_id']}",
            )
        integration = Path(coordinator["integration_worktree"])
        git(integration, "merge", "--no-ff", "--no-edit", assignments[0]["branch"])
        integrated = integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(integrated["merged_task_ids"], ["TASK-001", "TASK-002"])

    def test_promote_recovers_fast_forward_completed_before_state_write(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(integration, "add", "feature.py")
        git(integration, "commit", "-m", "feat: interrupted promotion fixture")
        promoted_tip = git(integration, "rev-parse", "HEAD")
        state_path = coordinator_path(self.run_dir, "FEAT-001")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "sealed"
        state["integration_head"] = promoted_tip
        state_path.write_text(json.dumps(state), encoding="utf-8")
        git(self.project, "merge", "--ff-only", promoted_tip)
        recovered = promote_feature(self.run_dir, "FEAT-001", "evidence passed")
        self.assertEqual(recovered["status"], "done")
        self.assertEqual(recovered["promoted_head"], promoted_tip)

    def test_final_seal_recovers_commit_completed_before_state_write(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        state_path = coordinator_path(self.run_dir, "FEAT-001")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "integrated"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        message = "feat(FEAT-001): interrupted final seal"
        append_journal(
            journal_path(self.run_dir, "FEAT-001", "coordinator"),
            {
                "event": "feature_seal_intent",
                "base": coordinator["integration_head"],
                "message": message,
            },
        )
        (integration / "release.md").write_text("ready\n", encoding="utf-8")
        git(integration, "add", "release.md")
        git(integration, "commit", "-m", message)
        recovered = seal_feature(
            self.run_dir, "FEAT-001", "final evidence passed", message
        )
        self.assertEqual(recovered["status"], "sealed")
        self.assertEqual(
            recovered["integration_head"], git(integration, "rev-parse", "HEAD")
        )

    def test_promotion_rejects_dirty_project_checkout(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        for wave_id in ("WAVE-001", "WAVE-002"):
            for assignment in prepare_wave(self.run_dir, "FEAT-001", wave_id):
                self.start_assignment(assignment, wave_id)
                worker = Path(assignment["worktree"])
                filename = {"TASK-001": "a.py", "TASK-002": "b.py", "TASK-003": "c.py"}[
                    assignment["task_id"]
                ]
                (worker / "src").mkdir(exist_ok=True)
                (worker / "src" / filename).write_text("VALUE = 1\n", encoding="utf-8")
                finish_task(
                    self.run_dir,
                    "FEAT-001",
                    wave_id,
                    assignment["task_id"],
                    "validation passed",
                    "review passed",
                    f"feat: {assignment['task_id']}",
                    summary=f"completed {assignment['task_id']}",
                )
            integrate_wave(self.run_dir, "FEAT-001", wave_id)
            complete_wave(self.run_dir, "FEAT-001", wave_id, "combined tests passed")
        seal_feature(self.run_dir, "FEAT-001", "all evidence passed", "feat: seal")
        (self.project / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(ExecutionError) as raised:
            promote_feature(self.run_dir, "FEAT-001", "all evidence passed")
        self.assertEqual(raised.exception.code, "PROMOTION_BLOCKED")

    def test_promotion_rejects_recorded_remote_default_head_drift(self) -> None:
        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        (integration / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(integration, "add", "feature.py")
        git(integration, "commit", "-m", "feat: ready for promotion")
        state_path = coordinator_path(self.run_dir, "FEAT-001")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "sealed"
        state["integration_head"] = git(integration, "rev-parse", "HEAD")
        state_path.write_text(json.dumps(state), encoding="utf-8")
        tree = git(self.project, "rev-parse", "HEAD^{tree}")
        advanced = git(
            self.project,
            "commit-tree",
            tree,
            "-p",
            coordinator["default_head"],
            "-m",
            "advance remote default",
        )
        git(self.project, "push", "-q", "origin", f"{advanced}:refs/heads/main")

        with self.assertRaises(ExecutionError) as raised:
            promote_feature(self.run_dir, "FEAT-001", "all evidence passed")
        self.assertEqual(raised.exception.code, "PROMOTION_BLOCKED")
        self.assertTrue(integration.is_dir())

    def test_cleanup_retry_accepts_already_removed_reachable_worker(self) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignments = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        for assignment in assignments:
            self.start_assignment(assignment, "WAVE-001")
            worker = Path(assignment["worktree"])
            filename = "a.py" if assignment["task_id"] == "TASK-001" else "b.py"
            (worker / "src").mkdir(exist_ok=True)
            (worker / "src" / filename).write_text("VALUE = 1\n", encoding="utf-8")
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "validation passed",
                "review passed",
                f"feat: {assignment['task_id']}",
                summary=f"completed {assignment['task_id']}",
            )
        integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")
        first = assignments[0]
        worker = Path(first["worktree"])
        integration = Path(coordinator["integration_worktree"])
        git(integration, "worktree", "unlock", str(worker))
        git(integration, "worktree", "remove", str(worker))
        git(integration, "branch", "-d", first["branch"])
        state_path = wave_path(self.run_dir, "FEAT-001", "WAVE-001")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "blocked"
        state["blocker"] = "CLEANUP_BLOCKED"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        completed = complete_wave(
            self.run_dir, "FEAT-001", "WAVE-001", "combined tests passed"
        )
        self.assertEqual(completed["status"], "done")

    def test_cleanup_retains_branch_when_registered_worker_path_is_missing(
        self,
    ) -> None:
        coordinator = self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignments = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        for assignment in assignments:
            self.start_assignment(assignment, "WAVE-001")
            worker = Path(assignment["worktree"])
            filename = "a.py" if assignment["task_id"] == "TASK-001" else "b.py"
            (worker / "src").mkdir(exist_ok=True)
            (worker / "src" / filename).write_text("VALUE = 1\n", encoding="utf-8")
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "validation passed",
                "review passed",
                f"feat: {assignment['task_id']}",
                summary=f"completed {assignment['task_id']}",
            )
        integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")
        missing = Path(assignments[0]["worktree"])
        missing.rename(missing.with_name(f"{missing.name}-moved"))

        with self.assertRaises(ExecutionError) as raised:
            complete_wave(self.run_dir, "FEAT-001", "WAVE-001", "combined tests passed")
        self.assertEqual(raised.exception.code, "CLEANUP_BLOCKED")
        self.assertTrue(local_branch_exists(self.project, assignments[0]["branch"]))
        self.assertIn(missing.resolve(), worktrees(Path(coordinator["project_root"])))

    def test_cleanup_branch_advance_race_retains_exact_ref(self) -> None:
        coordinator = self.prepare()
        expected_tip = git(self.project, "rev-parse", "HEAD")
        branch_name = f"codex/sdlc/{self.run_dir.name}/feat-001/worker-race"
        ref = f"refs/heads/{branch_name}"
        worker = self.root / "worker-race"
        git(
            self.project,
            "worktree",
            "add",
            "--no-track",
            "-b",
            branch_name,
            str(worker),
            expected_tip,
        )
        original_run = execution_core._run
        advanced_tip: str | None = None

        def race_before_delete(
            argv: list[str], cwd: Path, action: str, *, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            nonlocal advanced_tip
            if argv[:4] == ["git", "update-ref", "-d", ref] and advanced_tip is None:
                tree = git(self.project, "rev-parse", f"{expected_tip}^{{tree}}")
                advanced_tip = git(
                    self.project,
                    "commit-tree",
                    tree,
                    "-p",
                    expected_tip,
                    "-m",
                    "advance worker during cleanup",
                )
                git(self.project, "update-ref", ref, advanced_tip, expected_tip)
            return original_run(argv, cwd, action, check=check)

        with mock.patch.object(execution_core, "_run", side_effect=race_before_delete):
            cleaned = execution_core._cleanup_internal_resource(
                run_dir=self.run_dir,
                coordinator=coordinator,
                repo=self.project,
                kind="worker",
                worktree=worker,
                branch_name=branch_name,
                expected_tip=expected_tip,
                reachable_tip=expected_tip,
            )

        self.assertFalse(cleaned)
        self.assertFalse(os.path.lexists(worker))
        self.assertIsNotNone(advanced_tip)
        self.assertEqual(git(self.project, "rev-parse", ref), advanced_tip)

    def test_wrong_assignment_digest_is_rejected(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        with self.assertRaises(ExecutionError) as raised:
            start_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "bad",
                "test-session-bad-digest",
                Path(assignment["scope_cwd"]),
            )
        self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX semantics")
    def test_worker_cannot_commit_symlink_inside_write_claim(self) -> None:
        self.prepare()
        seal_tdd_base(self.run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
        assignment = prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start_assignment(assignment, "WAVE-001")
        worker = Path(assignment["worktree"])
        outside = self.root / "outside.py"
        outside.write_text("outside = True\n", encoding="utf-8")
        (worker / "src").mkdir()
        (worker / "src" / "a.py").symlink_to(outside)
        with self.assertRaises(ExecutionError) as raised:
            finish_task(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                "validation passed",
                "review passed",
                "feat: invalid symlink",
                summary="invalid-symlink handoff",
            )
        self.assertEqual(raised.exception.code, "REPLAN_REQUIRED")

    def test_legacy_coordinators_always_require_workflow_upgrade(self) -> None:
        state = coordinator_path(self.run_dir, "FEAT-001")
        state.parent.mkdir(parents=True, exist_ok=True)
        for version in (1, 2, 3, 4, 5, 6):
            for status in ("running", "done"):
                with self.subTest(version=version, status=status):
                    state.write_text(
                        json.dumps(
                            {
                                "schema": (
                                    f"agentic-sdlc/execution-coordinator-v{version}"
                                ),
                                "status": status,
                            }
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ExecutionError) as raised:
                        self.prepare()
                    self.assertEqual(raised.exception.code, "WORKFLOW_UPGRADE_REQUIRED")


class ManagedOuterLifecycleTests(unittest.TestCase):
    def test_task_lane_rejection_leaves_next_task_generation_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin = root / "origin.git"
            git(root, "init", "--bare", "-q", str(origin))
            repository = root / "repo"
            try:
                git(root, "init", "-q", "-b", "main", str(repository))
            except AssertionError:
                git(root, "init", "-q", str(repository))
                git(repository, "branch", "-m", "main")
            git(repository, "config", "user.name", "Task Lane Test")
            git(repository, "config", "user.email", "task-lane@example.invalid")
            selected = repository / "services" / "example"
            selected.mkdir(parents=True)
            (selected / "service.txt").write_text("base\n", encoding="utf-8")
            (selected / "docs").mkdir()
            (selected / "docs" / "requirements.md").write_text(
                REQUIREMENTS, encoding="utf-8"
            )
            (selected / "docs" / "design.md").write_text(
                DESIGN, encoding="utf-8"
            )
            git(repository, "add", "-A")
            git(repository, "commit", "-qm", "initial")
            git(repository, "remote", "add", "origin", str(origin))
            git(repository, "push", "-qu", "origin", "main")
            git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
            git(repository, "fetch", "-q", "origin")
            git(
                repository,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/main",
            )
            git(repository, "switch", "-qc", "task-lane-source")

            manager_path = (
                SCRIPT_DIR.parents[1] / "worktree" / "scripts" / "worktree_manager.py"
            )
            sys.path.insert(0, str(manager_path.parent))
            spec = importlib.util.spec_from_file_location(
                "task_lane_sdlc_worktree_manager", manager_path
            )
            assert spec and spec.loader
            manager = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = manager
            spec.loader.exec_module(manager)
            lane = manager.task_lane_ensure(cwd=selected, project=None)
            run_dir = root / "private" / "agentic-run"
            plan = run_dir / "plans" / "FEAT-001.plan.v1.md"
            plan.parent.mkdir(parents=True)
            plan.write_text(MANAGED_PLAN, encoding="utf-8")
            plan.with_suffix(plan.suffix + ".lock").write_text(
                "locked\n", encoding="utf-8"
            )
            seed_prompt_impact(run_dir, Path(str(lane["scope_cwd"])))

            with self.assertRaises(ExecutionError) as caught:
                prepare_execution(
                    run_dir,
                    Path(str(lane["scope_cwd"])),
                    "FEAT-001",
                    plan,
                    capacity=1,
                )

            self.assertEqual(caught.exception.code, "WORKTREE_CONFLICT")
            self.assertFalse(coordinator_path(run_dir, "FEAT-001").exists())
            self.assertFalse((run_dir / "execution" / "interop.json").exists())
            generation_arguments = {
                "cwd": Path(str(lane["scope_cwd"])),
                "workspace": root / "task-workspace.json",
                "run_id": "task-run-after-agentic-rejection",
                "task_scope": "services/example",
                "expected_head": str(lane["lane_head"]),
                "claims": [],
            }
            prepared = manager.task_lane_generation_prepare(**generation_arguments)
            acquired = manager.task_lane_generation_open(
                **generation_arguments,
                review_token=str(prepared["review_token"]),
                reviewed_tree=str(prepared["candidate_tree"]),
                reviewed_paths_sha256=str(prepared["paths_sha256"]),
            )
            self.assertEqual(acquired["generation"], 1)
            manager.task_lease_promote(
                cwd=Path(str(lane["worktree"])),
                name=str(lane["name"]),
                lease_id=str(acquired["token"]),
                promoted_head=str(lane["lane_head"]),
                expected_head=str(lane["lane_head"]),
                owner_kind="task-implementer",
            )
            released = manager.task_lane_generation_release(
                cwd=Path(str(lane["worktree"])),
                name=str(lane["name"]),
                generation=1,
                lease_id=str(acquired["token"]),
                promoted_head=str(lane["lane_head"]),
            )
            self.assertEqual(released["state"], "released")

    def test_managed_outer_execution_releases_to_local_source_integration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace with spaces"
            root.mkdir()
            origin = root / "origin.git"
            git(root, "init", "--bare", "-q", str(origin))
            repository = root / "example-monorepo"
            git(root, "init", "-q", "-b", "main", str(repository))
            git(repository, "config", "user.name", "Managed SDLC Test")
            git(repository, "config", "user.email", "sdlc@example.invalid")
            selected = repository / "services" / "example"
            selected.mkdir(parents=True)
            (selected / "service.txt").write_text("base\n", encoding="utf-8")
            (selected / "docs").mkdir()
            (selected / "docs" / "requirements.md").write_text(
                REQUIREMENTS, encoding="utf-8"
            )
            (selected / "docs" / "design.md").write_text(
                DESIGN, encoding="utf-8"
            )
            git(repository, "add", "-A")
            git(repository, "commit", "-qm", "initial")
            git(repository, "remote", "add", "origin", str(origin))
            git(repository, "push", "-qu", "origin", "main")
            git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
            git(repository, "fetch", "-q", "origin")
            git(
                repository,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/main",
            )
            git(repository, "switch", "-qc", "local-source")

            manager_path = (
                SCRIPT_DIR.parents[1] / "worktree" / "scripts" / "worktree_manager.py"
            )
            sys.path.insert(0, str(manager_path.parent))
            spec = importlib.util.spec_from_file_location(
                "managed_sdlc_worktree_manager", manager_path
            )
            assert spec and spec.loader
            manager = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = manager
            spec.loader.exec_module(manager)
            with mock.patch.object(manager.secrets, "token_hex", return_value="a7c2f9"):
                outer = manager.add_worktree(
                    cwd=selected, project=None, task_slug="agentic-sdlc-composed"
                )
            outer_root = Path(str(outer["worktree"]))
            outer_selected = Path(str(outer["scope_cwd"]))
            run_dir = root / "private" / "run-managed"
            plan = run_dir / "plans" / "FEAT-001.plan.v1.md"
            plan.parent.mkdir(parents=True)
            plan.write_text(MANAGED_PLAN, encoding="utf-8")
            plan.with_suffix(plan.suffix + ".lock").write_text(
                "locked\n", encoding="utf-8"
            )
            seed_prompt_impact(run_dir, outer_selected)

            prepare_execution(run_dir, outer_selected, "FEAT-001", plan, capacity=1)
            with self.assertRaisesRegex(manager.WorktreeError, "still owns"):
                manager.integrate_worktree(
                    cwd=repository,
                    name=str(outer["name"]),
                    validated_head=None,
                    restart=False,
                )

            seal_tdd_base(run_dir, "FEAT-001", "test(FEAT-001): no-op TDD base")
            assignment = prepare_wave(run_dir, "FEAT-001", "WAVE-001")[0]
            with self.assertRaisesRegex(manager.WorktreeError, "must not push"):
                manager.publication_guard(
                    cwd=Path(str(assignment["worktree"])), action="push"
                )
            arm_task(
                run_dir,
                "FEAT-001",
                "WAVE-001",
                "TASK-001",
                assignment["assignment_digest"],
            )
            start_task(
                run_dir,
                "FEAT-001",
                "WAVE-001",
                "TASK-001",
                assignment["assignment_digest"],
                "managed-worker-session",
                Path(assignment["scope_cwd"]),
            )
            value = Path(assignment["scope_cwd"]) / "src" / "value.py"
            value.parent.mkdir(parents=True)
            value.write_text("VALUE = 1\n", encoding="utf-8")
            finish_task(
                run_dir,
                "FEAT-001",
                "WAVE-001",
                "TASK-001",
                "focused test passed",
                "review passed",
                "feat(FEAT-001): add selected-scope value",
                summary="selected-scope handoff",
            )
            integrate_wave(run_dir, "FEAT-001", "WAVE-001")
            coordinator = json.loads(
                coordinator_path(run_dir, "FEAT-001").read_text(encoding="utf-8")
            )
            with self.assertRaisesRegex(manager.WorktreeError, "must not create-pr"):
                manager.publication_guard(
                    cwd=Path(str(coordinator["integration_worktree"])),
                    action="create-pr",
                )
            complete_wave(run_dir, "FEAT-001", "WAVE-001", "combined tests passed")
            seal_feature(
                run_dir,
                "FEAT-001",
                "final alignment passed",
                "feat(FEAT-001): seal selected scope",
            )
            with (
                mock.patch.object(
                    execution_core,
                    "record_outer_promotion",
                    side_effect=ExecutionInteropError(
                        "simulated crash before lease promotion"
                    ),
                ),
                self.assertRaises(ExecutionError) as git_only_crash,
            ):
                promote_feature(run_dir, "FEAT-001", "promotion checks passed")
            self.assertEqual(git_only_crash.exception.code, "WORKTREE_CONFLICT")
            git_only_interop = load_outer_interop(run_dir)
            assert git_only_interop is not None
            git_only_lease = manager.task_lease_inspect(
                cwd=outer_selected,
                name=str(git_only_interop["name"]),
                lease_id=str(git_only_interop["lease_id"]),
                owner_kind="agentic-sdlc",
            )
            self.assertIsNone(git_only_lease["promoted_head"])
            self.assertEqual(
                git_only_lease["outer_head"], git(outer_root, "rev-parse", "HEAD")
            )

            with (
                mock.patch.object(
                    execution_interop,
                    "_write",
                    side_effect=ExecutionInteropError(
                        "simulated crash after lease promotion"
                    ),
                ),
                self.assertRaises(ExecutionError) as lease_only_crash,
            ):
                promote_feature(run_dir, "FEAT-001", "promotion checks passed")
            self.assertEqual(lease_only_crash.exception.code, "WORKTREE_CONFLICT")
            lease_only_interop = load_outer_interop(run_dir)
            assert lease_only_interop is not None
            self.assertIsNone(lease_only_interop["promoted_head"])
            lease_only_lease = manager.task_lease_inspect(
                cwd=outer_selected,
                name=str(lease_only_interop["name"]),
                lease_id=str(lease_only_interop["lease_id"]),
                owner_kind="agentic-sdlc",
            )
            self.assertEqual(
                lease_only_lease["promoted_head"],
                git(outer_root, "rev-parse", "HEAD"),
            )

            original_save = execution_core._save_coordinator

            def interrupt_promoted_save(
                target_run_dir: Path,
                target_feature: str,
                coordinator: dict[str, object],
            ) -> None:
                if coordinator.get("status") == "promoted":
                    raise RuntimeError(
                        "simulated crash before coordinator promotion save"
                    )
                original_save(target_run_dir, target_feature, coordinator)

            with (
                mock.patch.object(
                    execution_core,
                    "_save_coordinator",
                    side_effect=interrupt_promoted_save,
                ),
                self.assertRaisesRegex(RuntimeError, "simulated crash"),
            ):
                promote_feature(run_dir, "FEAT-001", "promotion checks passed")
            crash_interop = load_outer_interop(run_dir)
            assert crash_interop is not None
            crash_lease = manager.task_lease_inspect(
                cwd=outer_selected,
                name=str(crash_interop["name"]),
                lease_id=str(crash_interop["lease_id"]),
                owner_kind="agentic-sdlc",
            )
            self.assertEqual(
                crash_lease["promoted_head"], git(outer_root, "rev-parse", "HEAD")
            )
            self.assertEqual(
                json.loads(
                    coordinator_path(run_dir, "FEAT-001").read_text(encoding="utf-8")
                )["status"],
                "sealed",
            )

            promoted = promote_feature(run_dir, "FEAT-001", "promotion checks passed")
            promoted_head = str(promoted["promoted_head"])
            self.assertEqual(git(outer_root, "rev-parse", "HEAD"), promoted_head)

            second_plan = run_dir / "plans" / "FEAT-002.plan.v1.md"
            second_plan.write_text(
                MANAGED_PLAN.replace("FEAT-001", "FEAT-002").replace(
                    "services/example/src/value.py",
                    "services/example/src/value-two.py",
                ),
                encoding="utf-8",
            )
            second_plan.with_suffix(second_plan.suffix + ".lock").write_text(
                "locked\n", encoding="utf-8"
            )
            prepare_execution(
                run_dir, outer_selected, "FEAT-002", second_plan, capacity=1
            )
            seal_tdd_base(run_dir, "FEAT-002", "test(FEAT-002): no-op TDD base")
            second_assignment = prepare_wave(run_dir, "FEAT-002", "WAVE-001")[0]
            arm_task(
                run_dir,
                "FEAT-002",
                "WAVE-001",
                "TASK-001",
                second_assignment["assignment_digest"],
            )
            start_task(
                run_dir,
                "FEAT-002",
                "WAVE-001",
                "TASK-001",
                second_assignment["assignment_digest"],
                "managed-worker-session-two",
                Path(second_assignment["scope_cwd"]),
            )
            second_value = Path(second_assignment["scope_cwd"]) / "src" / "value-two.py"
            second_value.parent.mkdir(parents=True, exist_ok=True)
            second_value.write_text("VALUE = 2\n", encoding="utf-8")
            finish_task(
                run_dir,
                "FEAT-002",
                "WAVE-001",
                "TASK-001",
                "focused test passed",
                "review passed",
                "feat(FEAT-002): add second selected-scope value",
                summary="second selected-scope handoff",
            )
            integrate_wave(run_dir, "FEAT-002", "WAVE-001")
            complete_wave(run_dir, "FEAT-002", "WAVE-001", "combined tests passed")
            seal_feature(
                run_dir,
                "FEAT-002",
                "final alignment passed",
                "feat(FEAT-002): seal selected scope",
            )

            with (
                mock.patch.object(
                    execution_interop,
                    "_write",
                    side_effect=ExecutionInteropError(
                        "simulated second promotion crash after lease CAS"
                    ),
                ),
                self.assertRaises(ExecutionError) as second_lease_only_crash,
            ):
                promote_feature(run_dir, "FEAT-002", "promotion checks passed")
            self.assertEqual(
                second_lease_only_crash.exception.code, "WORKTREE_CONFLICT"
            )
            second_crash_interop = load_outer_interop(run_dir)
            assert second_crash_interop is not None
            self.assertEqual(second_crash_interop["promoted_head"], promoted_head)
            second_crash_lease = manager.task_lease_inspect(
                cwd=outer_selected,
                name=str(second_crash_interop["name"]),
                lease_id=str(second_crash_interop["lease_id"]),
                owner_kind="agentic-sdlc",
            )
            self.assertEqual(
                second_crash_lease["promotion_heads"][-2:],
                [promoted_head, git(outer_root, "rev-parse", "HEAD")],
            )

            promoted = promote_feature(run_dir, "FEAT-002", "promotion checks passed")
            promoted_head = str(promoted["promoted_head"])
            reconciled_interop = load_outer_interop(run_dir)
            assert reconciled_interop is not None
            self.assertEqual(reconciled_interop["promoted_head"], promoted_head)
            reconciled_lease = manager.task_lease_inspect(
                cwd=outer_selected,
                name=str(reconciled_interop["name"]),
                lease_id=str(reconciled_interop["lease_id"]),
                owner_kind="agentic-sdlc",
            )
            self.assertEqual(reconciled_lease["promotion_heads"][-1], promoted_head)

            complete_evidence = {
                "final_alignment": "passed",
                "uat": "passed",
                "docs": "passed",
            }
            for missing in complete_evidence:
                incomplete = dict(complete_evidence)
                incomplete[missing] = ""
                with self.subTest(missing=missing):
                    with self.assertRaises(ExecutionInteropError):
                        release(
                            run_dir,
                            outer_selected,
                            promoted_head,
                            **incomplete,
                        )
                    with self.assertRaisesRegex(manager.WorktreeError, "still owns"):
                        manager.integrate_worktree(
                            cwd=repository,
                            name=str(outer["name"]),
                            validated_head=None,
                            restart=False,
                        )

            released = release(
                run_dir,
                outer_selected,
                promoted_head,
                **complete_evidence,
            )
            self.assertEqual(released["status"], "released")
            self.assertEqual(released["primary"], str(repository.resolve()))
            self.assertEqual(released["source_branch"], "local-source")
            pending = load_outer_interop(run_dir)
            assert pending is not None
            self.assertEqual(pending["outer_integration_status"], "pending")
            interop_path = run_dir / "execution" / "interop.json"
            stale = dict(pending)
            stale["promoted_head"] = "f" * 40
            interop_path.write_text(json.dumps(stale), encoding="utf-8")
            with self.assertRaisesRegex(ExecutionInteropError, "promoted head changed"):
                acquire_outer_interop(
                    run_dir,
                    outer_selected,
                    str(pending["project_scope"]),
                    promoted_head,
                )
            interop_path.write_text(json.dumps(pending), encoding="utf-8")
            resumed = acquire_outer_interop(
                run_dir,
                outer_selected,
                str(pending["project_scope"]),
                promoted_head,
            )
            self.assertTrue(resumed["released"])
            candidate = manager.integrate_worktree(
                cwd=repository,
                name=str(outer["name"]),
                validated_head=None,
                restart=False,
                expected_source_head=git(repository, "rev-parse", "HEAD"),
                expected_child_head=git(outer_root, "rev-parse", "HEAD"),
            )
            self.assertEqual(candidate["status"], "validation-required")
            integrated = manager.integrate_worktree(
                cwd=repository,
                name=str(outer["name"]),
                validated_head=str(candidate["candidate_head"]),
                restart=False,
                expected_source_head=str(candidate["source_head"]),
                expected_child_head=str(candidate["child_head"]),
            )
            git(repository, "reset", "--hard", str(candidate["source_head"]))
            with self.assertRaises(ExecutionInteropError):
                complete_source_integration(run_dir, outer_selected)
            git(repository, "reset", "--hard", str(integrated["source_head"]))
            proof = complete_source_integration(run_dir, outer_selected)
            self.assertEqual(proof["status"], "integrated")
            self.assertEqual(
                proof["source_integration_head"], integrated["source_head"]
            )


if __name__ == "__main__":
    unittest.main()
