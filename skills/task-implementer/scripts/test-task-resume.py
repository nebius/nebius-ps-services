#!/usr/bin/env python3
"""Focused tests for digest-bound Task Implementer resume control."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from prompt_workspace_core import PromptWorkspaceError, stable_json
import prompt_workspace_core as core
import prompt_workspace_intake as intake
import prompt_workspace as cli
import prompt_workspace_recovery as recovery
import prompt_workspace_resume as resume


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


def observation(
    *,
    coordinator_status: str = "running",
    wave_status: str = "planned",
    task_state: str = "planned",
    dispatched_at: str | None = None,
    heartbeat_at: str | None = None,
    released: bool = False,
    terminal_seal_active: bool = False,
    terminal_seal_promoted: bool = False,
    terminal_recovery_promoted: bool = False,
) -> dict[str, object]:
    active_wave = None if coordinator_status == "done" else "wave-001"
    coordinator = {
        "status": coordinator_status,
        "active_wave": active_wave,
        "waves": [{"wave_id": "wave-001"}],
    }
    wave = {
        "wave_id": "wave-001",
        "status": wave_status,
        "integration_worktree": "/tmp/integration",
        "task_ids": ["task-1"],
        "task_states": {"task-1": task_state},
        "batches": [["task-1"]],
        "batch_states": ["active" if wave_status == "running" else "planned"],
        "active_batch_index": 0 if wave_status == "running" else None,
        "promoted_head": None,
    }
    plane = {
        "state": task_state,
        "dispatched_at": dispatched_at,
        "last_heartbeat_at": heartbeat_at,
    }
    return {
        "coordinator": coordinator,
        "waves": [wave],
        "tasks": [{"wave_id": "wave-001", "task_id": "task-1", "plane": plane}],
        "interop": {"released": released},
        "lease": None,
        "interop_repairs": {},
        "terminal_lifecycle_seal": None,
        "terminal_lifecycle_seal_active": terminal_seal_active,
        "terminal_lifecycle_seal_promoted": terminal_seal_promoted,
        "terminal_lifecycle_recovery": None,
        "terminal_lifecycle_recovery_promoted": terminal_recovery_promoted,
        "git": {
            "lane": {"present": True},
            "resources": [
                {"path": "/tmp/integration", "present": True, "head": "a" * 40}
            ],
        },
        "journals": [],
        "pending_unindexed_tasks": [],
        "files": [],
        "state_sha256": "1" * 64,
        "handoff_sha256": "2" * 64,
    }


class ResumeDecisionTest(unittest.TestCase):
    def test_atomic_codex_worker_launch_uses_exact_start_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scope_cwd = root / "worker" / "services" / "example"
            scope_cwd.mkdir(parents=True)
            assignment_path = root / "private" / "task-1.json"
            assignment_path.parent.mkdir()
            result_path = root / "private" / "task-1-result.json"
            result_path.write_text("{}\n", encoding="utf-8")
            assignment_path.write_text(
                json.dumps({"result_path": str(result_path)}) + "\n",
                encoding="utf-8",
            )
            start_argv = [
                "/usr/bin/python3",
                "/installed/prompt_workspace.py",
                "task-start",
                "--workspace",
                "/private/workspace.json",
                "--run-id",
                "run-test",
                "--task-id",
                "task-1",
                "--assignment-sha256",
                "1" * 64,
                "--start-lease",
                "2026-08-14T00:00:00+00:00",
                "--json",
            ]
            completed = subprocess.CompletedProcess([], 0)
            with (
                mock.patch.object(cli.shutil, "which", return_value="/usr/bin/codex"),
                mock.patch.object(
                    cli.subprocess, "run", return_value=completed
                ) as launched,
            ):
                result = cli._launch_codex_worker(
                    {
                        "start_context": {
                            "scope_cwd": str(scope_cwd),
                            "assignment_path": str(assignment_path),
                            "start_argv": start_argv,
                        }
                    }
                )

        self.assertEqual(result, {"mode": "codex-exec", "returncode": 0})
        call = launched.call_args
        self.assertEqual(
            call.args[0][0:5],
            [
                "/usr/bin/codex",
                "exec",
                "--ephemeral",
                "-C",
                str(scope_cwd),
            ],
        )
        self.assertIn(" ".join(start_argv), call.args[0][-1])
        self.assertIn(str(assignment_path), call.args[0][-1])
        self.assertIn('model_reasoning_effort="medium"', call.args[0])
        self.assertIs(call.kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(call.kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(call.kwargs["stderr"], subprocess.DEVNULL)

    def test_atomic_codex_worker_launch_rejects_success_without_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scope_cwd = root / "worker"
            scope_cwd.mkdir()
            assignment_path = root / "task-1.json"
            assignment_path.write_text(
                json.dumps({"result_path": str(root / "missing-result.json")})
                + "\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(cli.shutil, "which", return_value="/usr/bin/codex"),
                mock.patch.object(
                    cli.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0),
                ),
                self.assertRaises(PromptWorkspaceError) as raised,
            ):
                cli._launch_codex_worker(
                    {
                        "start_context": {
                            "scope_cwd": str(scope_cwd),
                            "assignment_path": str(assignment_path),
                            "start_argv": ["/usr/bin/python3", "task-start"],
                        }
                    }
                )
        self.assertEqual(raised.exception.code, "WORKER_EXEC_INCOMPLETE")

    def test_atomic_codex_worker_launch_reports_child_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scope_cwd = root / "worker"
            scope_cwd.mkdir()
            assignment_path = root / "task-1.json"
            assignment_path.write_text("{}\n", encoding="utf-8")
            with (
                mock.patch.object(cli.shutil, "which", return_value="/usr/bin/codex"),
                mock.patch.object(
                    cli.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 17),
                ),
                self.assertRaises(PromptWorkspaceError) as raised,
            ):
                cli._launch_codex_worker(
                    {
                        "start_context": {
                            "scope_cwd": str(scope_cwd),
                            "assignment_path": str(assignment_path),
                            "start_argv": ["/usr/bin/python3", "task-start"],
                        }
                    }
                )
        self.assertEqual(raised.exception.code, "WORKER_EXEC_FAILED")

    def test_atomic_recovery_worker_launch_uses_exact_resume_context(self) -> None:
        context = {
            "scope_cwd": "/private/task-1/services/example",
            "assignment_path": "/private/task-1.json",
            "recover_argv": ["/usr/bin/python3", "task-recover", "--json"],
        }
        with mock.patch.object(
            cli,
            "_launch_codex_worker",
            return_value={"mode": "codex-exec", "returncode": 0},
        ) as launched:
            result = cli._launch_codex_recovery_worker(
                {"worker_context": context}
            )
        self.assertEqual(result, {"mode": "codex-exec", "returncode": 0})
        launched.assert_called_once_with(
            {
                "start_context": {
                    "scope_cwd": context["scope_cwd"],
                    "assignment_path": context["assignment_path"],
                    "start_argv": context["recover_argv"],
                }
            },
            reasoning_effort="low",
        )

    def test_planned_wave_executes_prepare(self) -> None:
        decision = resume._choose_transition(
            Path("/tmp/run"), observation(), clock=lambda: NOW
        )
        self.assertEqual(
            (decision["outcome"], decision["next_transition"]),
            ("execute", "wave-prepare"),
        )

    def test_planned_wave_with_newer_prompt_impact_routes_to_replan(self) -> None:
        value = observation()
        value["prompt_impact_replan_required"] = True
        decision = resume._choose_transition(
            Path("/tmp/run"),
            value,
            clock=lambda: NOW,
            requested_arguments={"capacity": 3},
        )
        self.assertEqual(decision["next_transition"], "wave-replan")
        self.assertEqual(decision["arguments"], {"capacity": 3})
        self.assertIn("prompt impact", decision["reason"])

    def test_planned_wave_handoff_contract_drift_routes_to_replan(self) -> None:
        value = observation()
        value["coordinator"]["plan_sha256"] = "0" * 64
        value["coordinator"]["waves"][0]["tasks"] = [{"stale": True}]
        handoff = """# Task Implementer Handoff

## Task Queue

### task-1

- Status: pending
- Depends on: none
- Write claims:
  - prefix: services/example/agent
  - exact: services/example/cli.py
- Conflict domains: example:safety
- Implementation steps: implement the complete safety correction
- Validation: run the focused safety regressions
- End-to-end validation: prove the corrected offline workflow
- Done criteria: all correction oracles pass
- Stop conditions: stop before unclaimed effects
"""
        with mock.patch.object(resume, "read_handoff_text", return_value=handoff):
            decision = resume._choose_transition(
                Path("/tmp/run"),
                value,
                clock=lambda: NOW,
                requested_arguments={"capacity": 3},
            )
        self.assertEqual(decision["next_transition"], "wave-replan")
        self.assertEqual(decision["arguments"], {"capacity": 3})
        self.assertIn("task contract", decision["reason"])

    def test_fresh_armed_and_running_workers_wait(self) -> None:
        for state, dispatched, heartbeat in (
            ("assigned", (NOW - timedelta(seconds=10)).isoformat(), None),
            ("running", None, (NOW - timedelta(seconds=10)).isoformat()),
        ):
            with self.subTest(state=state):
                decision = resume._choose_transition(
                    Path("/tmp/run"),
                    observation(
                        wave_status="running",
                        task_state=state,
                        dispatched_at=dispatched,
                        heartbeat_at=heartbeat,
                    ),
                    clock=lambda: NOW,
                )
                self.assertEqual(decision["outcome"], "wait")

    def test_stale_workers_require_confirmation(self) -> None:
        for state, dispatched, heartbeat, transition in (
            (
                "assigned",
                (NOW - timedelta(seconds=61)).isoformat(),
                None,
                "task-rearm",
            ),
            (
                "running",
                None,
                (NOW - timedelta(seconds=241)).isoformat(),
                "task-recover",
            ),
        ):
            with self.subTest(state=state):
                decision = resume._choose_transition(
                    Path("/tmp/run"),
                    observation(
                        wave_status="running",
                        task_state=state,
                        dispatched_at=dispatched,
                        heartbeat_at=heartbeat,
                    ),
                    clock=lambda: NOW,
                )
                self.assertEqual(decision["outcome"], "requires_confirmation")
                self.assertEqual(decision["next_transition"], transition)

    def test_hard_worker_guard_routes_immediate_confirmed_recovery(self) -> None:
        value = observation(
            wave_status="running",
            task_state="running",
            heartbeat_at=(NOW - timedelta(seconds=1)).isoformat(),
        )
        value["tasks"][0]["assignment"] = {
            "task_id": "task-1",
            "worktree": "/tmp/task-1",
        }
        value["git"]["resources"].append(
            {"path": "/tmp/task-1", "present": True, "head": "a" * 40}
        )
        with mock.patch.object(
            resume,
            "_worker_guard_status",
            return_value={"status": "WORKER_READ_ONLY_TIMEOUT"},
        ):
            decision = resume._choose_transition(
                Path("/tmp/run"), value, clock=lambda: NOW
            )
        self.assertEqual(decision["outcome"], "requires_confirmation")
        self.assertEqual(decision["next_transition"], "task-recover")
        self.assertIn("WORKER_READ_ONLY_TIMEOUT", decision["reason"])

    def test_blocked_and_complete_are_distinct(self) -> None:
        blocked = observation(coordinator_status="blocked")
        self.assertEqual(
            resume._choose_transition(Path("/tmp/run"), blocked, clock=lambda: NOW)[
                "outcome"
            ],
            "blocked",
        )
        complete = observation(
            coordinator_status="done",
            released=True,
            terminal_seal_promoted=True,
        )
        with mock.patch.object(
            resume, "read_handoff_text", return_value="- Overall status: done\n"
        ):
            decision = resume._choose_transition(
                Path("/tmp/run"), complete, clock=lambda: NOW
            )
        self.assertEqual(decision["outcome"], "complete")

        missing_seal = observation(coordinator_status="done", released=True)
        with mock.patch.object(
            resume, "read_handoff_text", return_value="- Overall status: done\n"
        ):
            blocked_release = resume._choose_transition(
                Path("/tmp/run"), missing_seal, clock=lambda: NOW
            )
        self.assertEqual(blocked_release["outcome"], "blocked")
        self.assertIn("terminal lifecycle seal", blocked_release["reason"])

        recovered = observation(
            coordinator_status="done",
            released=True,
            terminal_recovery_promoted=True,
        )
        with mock.patch.object(
            resume, "read_handoff_text", return_value="- Overall status: done\n"
        ):
            recovered_decision = resume._choose_transition(
                Path("/tmp/run"), recovered, clock=lambda: NOW
            )
        self.assertEqual(recovered_decision["outcome"], "complete")

    def test_blocked_prefixed_completed_result_routes_to_strict_finish(self) -> None:
        value = observation(wave_status="blocked", task_state="failed")
        value["tasks"][0]["result"] = {"status": "COMPLETED"}
        decision = resume._choose_transition(Path("/tmp/run"), value, clock=lambda: NOW)
        self.assertEqual(decision["outcome"], "execute")
        self.assertEqual(decision["next_transition"], "task-finish")
        self.assertEqual(decision["arguments"], {"task_id": "task-1"})

    def test_final_promoted_wave_waits_for_terminal_lifecycle_seal(self) -> None:
        value = observation(wave_status="promoted", task_state="merged")
        blocked = resume._choose_transition(Path("/tmp/run"), value, clock=lambda: NOW)
        self.assertEqual(blocked["outcome"], "blocked")
        self.assertIn("terminal lifecycle seal", blocked["reason"])

        value["terminal_lifecycle_seal_promoted"] = True
        cleanup = resume._choose_transition(Path("/tmp/run"), value, clock=lambda: NOW)
        self.assertEqual(cleanup["next_transition"], "wave-cleanup")

    def test_finalization_requires_alignment_before_token_issue(self) -> None:
        value = observation(coordinator_status="done", released=False)
        with mock.patch.object(resume, "read_handoff_text", return_value=""):
            missing = resume._choose_transition(
                Path("/tmp/run"), value, clock=lambda: NOW
            )
            supplied = resume._choose_transition(
                Path("/tmp/run"),
                value,
                clock=lambda: NOW,
                requested_arguments={"alignment": "changed-surface align passed"},
            )
        self.assertEqual(missing["next_transition"], "run-finalize")
        self.assertEqual(missing["required_arguments"], ["alignment"])
        self.assertEqual(
            supplied["arguments"],
            {"alignment": "changed-surface align passed"},
        )
        with (
            mock.patch.object(resume, "read_handoff_text", return_value=""),
            self.assertRaises(PromptWorkspaceError) as multiline,
        ):
            resume._choose_transition(
                Path("/tmp/run"),
                value,
                clock=lambda: NOW,
                requested_arguments={"alignment": "passed\n## injected"},
            )
        self.assertEqual(multiline.exception.code, "EXECUTION_STATE_INVALID")

    def test_unindexed_review_correction_routes_to_replan(self) -> None:
        value = observation(wave_status="promotion_pending", task_state="merged")
        value["coordinator"]["waves"][0]["batches"] = [["task-1"]]
        value["pending_unindexed_tasks"] = ["task-2"]
        decision = resume._choose_transition(Path("/tmp/run"), value, clock=lambda: NOW)
        self.assertEqual(decision["next_transition"], "wave-replan")
        self.assertEqual(decision["arguments"], {"capacity": 1})

    def test_missing_active_resource_requires_stop_confirmation(self) -> None:
        value = observation(wave_status="running", task_state="assigned")
        value["git"]["resources"][0]["present"] = False
        decision = resume._choose_transition(Path("/tmp/run"), value, clock=lambda: NOW)
        self.assertEqual(decision["outcome"], "requires_confirmation")
        self.assertEqual(decision["next_transition"], "wave-resource-recover")

    def test_missing_cleaned_historical_resource_does_not_block_active_wave(
        self,
    ) -> None:
        value = observation(
            wave_status="running",
            task_state="assigned",
            dispatched_at=(NOW - timedelta(seconds=10)).isoformat(),
        )
        value["git"]["resources"].append(
            {"path": "/tmp/cleaned-prior-wave", "present": False}
        )
        decision = resume._choose_transition(Path("/tmp/run"), value, clock=lambda: NOW)
        self.assertEqual(decision["outcome"], "wait")

    def test_intake_maps_every_resume_outcome_without_a_new_run(self) -> None:
        expected = {
            "execute": ("continue", "running", "RESUME_EXECUTE"),
            "wait": ("wait", "running", "WORKER_ACTIVE"),
            "requires_confirmation": (
                "blocked",
                "blocked",
                "RECOVERY_CONFIRMATION_REQUIRED",
            ),
            "blocked": ("blocked", "blocked", "RESUME_BLOCKED"),
            "complete": ("done", "done", "ALREADY_COMPLETE"),
        }
        for outcome, public in expected.items():
            with self.subTest(outcome=outcome):
                plan = {
                    "outcome": outcome,
                    "next_transition": "wave-prepare" if outcome == "execute" else None,
                    "handoff_sha256": "2" * 64,
                }
                with (
                    mock.patch.object(intake, "reconcile_committed_resume"),
                    mock.patch.object(intake, "plan_run_resume", return_value=plan),
                    mock.patch.object(intake, "adopt_resume_plan", return_value=plan),
                    mock.patch.object(
                        intake, "reconcile_handoff_projection"
                    ) as projection,
                ):
                    action, status, code, internal = intake._resume_route(
                        {},
                        Path("/private/runs/run-existing"),
                        {"run_id": "run-existing"},
                        clock=lambda: NOW,
                    )
                self.assertEqual((action, status, code), public)
                self.assertEqual(internal["run_id"], "run-existing")
                self.assertIs(internal["resume"], plan)
                projection.assert_called_once()

    def test_intake_preserves_finalization_pending_public_route(self) -> None:
        plan = {
            "outcome": "execute",
            "next_transition": "run-finalize",
            "handoff_sha256": "2" * 64,
        }
        with (
            mock.patch.object(intake, "reconcile_committed_resume"),
            mock.patch.object(intake, "plan_run_resume", return_value=plan),
            mock.patch.object(intake, "adopt_resume_plan", return_value=plan),
            mock.patch.object(intake, "reconcile_handoff_projection"),
        ):
            action, status, code, _internal = intake._resume_route(
                {}, Path("/private/runs/run-existing"), {}, clock=lambda: NOW
            )
        self.assertEqual(
            (action, status, code),
            (
                "finalize",
                "finalization_pending",
                "TASK_LEASE_RELEASE_REQUIRED",
            ),
        )


class ResumeControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.run_dir = root / "runs" / "run-test"
        self.run_dir.mkdir(parents=True)
        self.run_dir.chmod(0o700)
        self.workspace = {"runs_root": str(root / "runs")}

    def _projection_recovery_fixture(
        self,
    ) -> tuple[dict[str, object], dict[str, object]]:
        handoff = """# Task Implementer Handoff

## Task Queue

### task-1

- Status: committed
- Narrative: preserve this exact text

### task-9

- Status: pending
- Narrative: unindexed correction input
"""
        handoff_path = self.run_dir / "handoff.md"
        handoff_path.write_text(handoff, encoding="utf-8")
        handoff_path.chmod(0o600)
        expected = hashlib.sha256(handoff.encode("utf-8")).hexdigest()
        control = {
            "schema": resume.RESUME_CONTROL_SCHEMA,
            "run_id": "run-test",
            "epoch": 1,
            "adopted": True,
            "phase": "idle",
            "pre_state_sha256": "1" * 64,
            "transition": None,
            "arguments": None,
            "arguments_sha256": None,
            "resume_token": None,
            "terminal_state_sha256": "1" * 64,
            "projection_sha256": expected,
            "updated_at": NOW.isoformat(),
        }
        core.write_atomic(
            self.run_dir / "orchestration" / "resume-control.json",
            stable_json(control),
        )
        tasks = [{"task_id": "task-1"}]
        coordinator = {
            "status": "running",
            "plan_sha256": recovery.sha256_json([tasks]),
            "waves": [{"wave_id": "wave-001", "tasks": tasks, "batches": [["task-1"]]}],
        }
        wave = {
            "wave_id": "wave-001",
            "status": "promotion_pending",
            "task_ids": ["task-1"],
            "task_states": {"task-1": "merged"},
            "batches": [["task-1"]],
        }
        return coordinator, wave

    def _recover_projection(
        self, coordinator: dict[str, object], wave: dict[str, object]
    ) -> dict[str, object]:
        control = resume.load_resume_control(self.run_dir, required=True)
        expected = str(control["projection_sha256"])
        with (
            mock.patch.object(
                recovery, "verify_workspace", return_value=self.workspace
            ),
            mock.patch.object(
                recovery, "load_coordinator_state", return_value=coordinator
            ),
            mock.patch.object(recovery, "_load_wave", return_value=wave),
            mock.patch.object(
                recovery, "_load_task_plane", return_value={"state": "merged"}
            ),
        ):
            return recovery.recover_handoff_projection(
                Path("workspace.json"), "run-test", expected, clock=lambda: NOW
            )

    def test_adoption_is_digest_bound_and_mode_0600(self) -> None:
        plan = {
            "outcome": "execute",
            "next_transition": "wave-prepare",
            "reason": "test",
            "arguments": {},
            "resume_token": None,
            "state_sha256": "1" * 64,
            "handoff_sha256": "2" * 64,
            "epoch": 0,
            "replay": False,
        }
        with mock.patch.object(resume, "plan_run_resume", return_value=plan):
            adopted = resume.adopt_resume_plan(
                self.workspace, "run-test", plan, clock=lambda: NOW
            )
        self.assertRegex(str(adopted["resume_token"]), r"^[0-9a-f]{64}$")
        control = resume.load_resume_control(self.run_dir, required=True)
        self.assertEqual(control["schema"], resume.RESUME_CONTROL_SCHEMA)
        self.assertEqual(control["phase"], "idle")
        self.assertEqual(
            (self.run_dir / "orchestration" / "resume-control.json").stat().st_mode
            & 0o777,
            0o600,
        )

    def test_begin_rejects_stale_token_and_records_one_intent(self) -> None:
        plan = {
            "outcome": "execute",
            "next_transition": "wave-prepare",
            "reason": "test",
            "arguments": {},
            "resume_token": None,
            "state_sha256": "1" * 64,
            "handoff_sha256": "2" * 64,
            "epoch": 0,
            "replay": False,
        }
        with mock.patch.object(resume, "plan_run_resume", return_value=plan):
            adopted = resume.adopt_resume_plan(
                self.workspace, "run-test", plan, clock=lambda: NOW
            )
        with mock.patch.object(resume, "plan_run_resume", return_value=plan):
            with self.assertRaises(PromptWorkspaceError) as stale:
                resume.begin_resume_transition(
                    self.workspace, "run-test", "wave-prepare", "0" * 64
                )
        self.assertEqual(stale.exception.code, "RESUME_STALE")
        with mock.patch.object(resume, "plan_run_resume", return_value=plan):
            begun = resume.begin_resume_transition(
                self.workspace,
                "run-test",
                "wave-prepare",
                str(adopted["resume_token"]),
                clock=lambda: NOW,
            )
        self.assertEqual(begun["phase"], "intent")
        replayed = resume.begin_resume_transition(
            self.workspace,
            "run-test",
            "wave-prepare",
            str(adopted["resume_token"]),
            clock=lambda: NOW,
        )
        self.assertEqual(replayed["epoch"], begun["epoch"])

    def test_active_resource_free_prepare_intent_retires_for_replan(self) -> None:
        token = "4" * 64
        control = {
            "schema": resume.RESUME_CONTROL_SCHEMA,
            "run_id": "run-test",
            "epoch": 3,
            "adopted": True,
            "phase": "intent",
            "pre_state_sha256": "1" * 64,
            "transition": "wave-prepare",
            "arguments": {},
            "arguments_sha256": hashlib.sha256(stable_json({})).hexdigest(),
            "resume_token": token,
            "terminal_state_sha256": None,
            "projection_sha256": None,
            "updated_at": NOW.isoformat(),
        }
        core.write_atomic(
            self.run_dir / "orchestration" / "resume-control.json",
            stable_json(control),
        )
        changed = observation()
        changed["state_sha256"] = "5" * 64
        changed["handoff_sha256"] = "6" * 64
        changed["prompt_impact_replan_required"] = True
        changed["git"]["resources"][0]["present"] = False
        with (
            mock.patch.object(resume, "_machine_observation", return_value=changed),
            self.assertRaises(PromptWorkspaceError) as raised,
        ):
            resume.begin_resume_transition(
                self.workspace,
                "run-test",
                "wave-prepare",
                token,
                clock=lambda: NOW,
            )
        self.assertEqual(raised.exception.code, "REPLAN_REQUIRED")
        retired = resume.load_resume_control(self.run_dir, required=True)
        self.assertEqual(retired["phase"], "idle")
        self.assertIsNone(retired["transition"])
        self.assertEqual(retired["terminal_state_sha256"], "5" * 64)

    def test_failed_prepare_abort_accepts_only_resource_free_replan(self) -> None:
        token = "4" * 64
        control = {
            "schema": resume.RESUME_CONTROL_SCHEMA,
            "run_id": "run-test",
            "epoch": 3,
            "adopted": True,
            "phase": "intent",
            "pre_state_sha256": "1" * 64,
            "transition": "wave-prepare",
            "arguments": {},
            "arguments_sha256": hashlib.sha256(stable_json({})).hexdigest(),
            "resume_token": token,
            "terminal_state_sha256": None,
            "projection_sha256": None,
            "updated_at": NOW.isoformat(),
        }
        control_path = self.run_dir / "orchestration" / "resume-control.json"
        changed = observation()
        changed["state_sha256"] = "5" * 64
        changed["handoff_sha256"] = "6" * 64
        changed["prompt_impact_replan_required"] = True
        changed["git"]["resources"][0]["present"] = False

        core.write_atomic(control_path, stable_json(control))
        with mock.patch.object(
            resume, "_machine_observation", return_value=changed
        ):
            resume.abort_resume_transition_if_unchanged(
                self.workspace,
                "run-test",
                "wave-prepare",
                token,
                clock=lambda: NOW,
            )
        retired = resume.load_resume_control(self.run_dir, required=True)
        self.assertEqual(retired["phase"], "idle")

        blocked = dict(changed)
        blocked["git"] = {
            **changed["git"],
            "resources": [
                {"path": "/tmp/integration", "present": True, "head": "a" * 40}
            ],
        }
        core.write_atomic(control_path, stable_json(control))
        with mock.patch.object(
            resume, "_machine_observation", return_value=blocked
        ):
            resume.abort_resume_transition_if_unchanged(
                self.workspace,
                "run-test",
                "wave-prepare",
                token,
                clock=lambda: NOW,
            )
        retained = resume.load_resume_control(self.run_dir, required=True)
        self.assertEqual(retained["phase"], "intent")
        self.assertEqual(retained["resume_token"], token)

    def test_first_controlled_transition_bootstraps_resume_state(self) -> None:
        plan = {
            "outcome": "execute",
            "next_transition": "wave-prepare",
            "reason": "test",
            "arguments": {},
            "required_arguments": [],
            "resume_token": None,
            "state_sha256": "1" * 64,
            "handoff_sha256": "2" * 64,
            "epoch": 0,
            "replay": False,
        }
        with mock.patch.object(resume, "plan_run_resume", return_value=plan):
            begun = resume.begin_resume_transition(
                self.workspace,
                "run-test",
                "wave-prepare",
                None,
                clock=lambda: NOW,
            )
        self.assertEqual(begun["phase"], "intent")
        self.assertEqual(begun["epoch"], 1)
        self.assertRegex(str(begun["resume_token"]), r"^[0-9a-f]{64}$")
        persisted = resume.load_resume_control(self.run_dir, required=True)
        self.assertEqual(persisted["resume_token"], begun["resume_token"])

    def test_successful_controlled_cli_returns_next_resume_plan(self) -> None:
        current_token = "4" * 64
        next_plan = {
            "outcome": "execute",
            "next_transition": "wave-dispatch",
            "resume_token": "5" * 64,
        }
        with (
            mock.patch.object(cli, "verify_workspace", return_value=self.workspace),
            mock.patch.object(cli, "resume_execution_lock", return_value=nullcontext()),
            mock.patch.object(
                cli,
                "begin_resume_transition",
                return_value={"resume_token": current_token},
            ),
            mock.patch.object(cli, "prepare_wave", return_value={"status": "prepared"}),
            mock.patch.object(cli, "complete_resume_transition") as complete,
            mock.patch.object(cli, "resume_run", return_value=next_plan) as routed,
            mock.patch.object(cli, "emit") as emitted,
        ):
            return_code = cli.main(
                [
                    "wave-prepare",
                    "--workspace",
                    "/private/workspace.json",
                    "--run-id",
                    "run-test",
                    "--resume-token",
                    current_token,
                    "--json",
                ]
            )
        self.assertEqual(return_code, 0)
        complete.assert_called_once_with(
            self.workspace, "run-test", "wave-prepare", current_token
        )
        routed.assert_called_once_with(Path("/private/workspace.json"), "run-test")
        emitted.assert_called_once_with(
            {"status": "prepared", "resume": next_plan}, True
        )

    def test_controlled_task_arm_can_launch_worker_atomically(self) -> None:
        current_token = "4" * 64
        events: list[str] = []
        guard = mock.MagicMock()
        guard.__enter__.return_value = None
        guard.__exit__.side_effect = lambda *_args: events.append("lock-released")
        armed = {
            "status": "armed",
            "start_context": {
                "scope_cwd": "/private/task-1/services/example",
                "assignment_path": "/private/task-1.json",
                "start_argv": ["/usr/bin/python3", "task-start"],
            },
        }
        next_plan = {"outcome": "wait", "next_transition": None}

        def launch_worker(result: dict[str, object]) -> dict[str, object]:
            self.assertEqual(events, ["transition-complete", "lock-released"])
            self.assertEqual(result, {**armed, "resume": next_plan})
            events.append("worker-launched")
            return {"mode": "codex-exec", "returncode": 0}

        with (
            mock.patch.object(cli, "verify_workspace", return_value=self.workspace),
            mock.patch.object(cli, "resume_execution_lock", return_value=guard),
            mock.patch.object(
                cli,
                "begin_resume_transition",
                return_value={"resume_token": current_token},
            ),
            mock.patch.object(cli, "arm_task", return_value=armed),
            mock.patch.object(
                cli,
                "complete_resume_transition",
                side_effect=lambda *_args: events.append("transition-complete"),
            ) as complete,
            mock.patch.object(cli, "resume_run", return_value=next_plan),
            mock.patch.object(
                cli,
                "_launch_codex_worker",
                side_effect=launch_worker,
            ) as launched,
            mock.patch.object(cli, "emit") as emitted,
        ):
            return_code = cli.main(
                [
                    "task-arm",
                    "--workspace",
                    "/private/workspace.json",
                    "--run-id",
                    "run-test",
                    "--task-id",
                    "task-1",
                    "--resume-token",
                    current_token,
                    "--launch-codex-worker",
                    "--json",
                ]
            )
        self.assertEqual(return_code, 0)
        self.assertEqual(
            events, ["transition-complete", "lock-released", "worker-launched"]
        )
        complete.assert_called_once_with(
            self.workspace, "run-test", "task-arm", current_token
        )
        launched.assert_called_once_with({**armed, "resume": next_plan})
        emitted.assert_called_once_with(
            {
                **armed,
                "resume": next_plan,
                "worker_launch": {"mode": "codex-exec", "returncode": 0},
            },
            True,
        )

    def test_run_resume_can_launch_fresh_recovery_worker_atomically(self) -> None:
        plan = {
            "outcome": "requires_confirmation",
            "next_transition": "task-recover",
            "worker_context": {
                "scope_cwd": "/private/task-1/services/example",
                "assignment_path": "/private/task-1.json",
                "recover_argv": ["/usr/bin/python3", "task-recover", "--json"],
            },
        }
        launched_result = {"mode": "codex-exec", "returncode": 0}
        with (
            mock.patch.object(cli, "resume_run", return_value=plan),
            mock.patch.object(
                cli,
                "_launch_codex_recovery_worker",
                return_value=launched_result,
            ) as launched,
            mock.patch.object(cli, "emit") as emitted,
        ):
            return_code = cli.main(
                [
                    "run-resume",
                    "--workspace",
                    "/private/workspace.json",
                    "--run-id",
                    "run-test",
                    "--launch-codex-recovery-worker",
                    "--json",
                ]
            )
        self.assertEqual(return_code, 0)
        launched.assert_called_once_with(plan)
        emitted.assert_called_once_with(
            {**plan, "worker_launch": launched_result}, True
        )

    def test_begin_rejects_arguments_not_bound_to_token(self) -> None:
        plan = {
            "outcome": "execute",
            "next_transition": "task-arm",
            "reason": "test",
            "arguments": {"task_id": "task-1"},
            "resume_token": None,
            "state_sha256": "1" * 64,
            "handoff_sha256": "2" * 64,
            "epoch": 0,
            "replay": False,
        }
        with mock.patch.object(resume, "plan_run_resume", return_value=plan):
            adopted = resume.adopt_resume_plan(
                self.workspace, "run-test", plan, clock=lambda: NOW
            )
        with (
            mock.patch.object(resume, "plan_run_resume", return_value=plan),
            self.assertRaises(PromptWorkspaceError) as raised,
        ):
            resume.begin_resume_transition(
                self.workspace,
                "run-test",
                "task-arm",
                str(adopted["resume_token"]),
                arguments={"task_id": "task-2"},
                clock=lambda: NOW,
            )
        self.assertEqual(raised.exception.code, "RESUME_STALE")

    def test_active_intent_routes_exact_replay(self) -> None:
        arguments = {"capacity": 3}
        control = {
            "schema": resume.RESUME_CONTROL_SCHEMA,
            "run_id": "run-test",
            "epoch": 3,
            "adopted": True,
            "phase": "intent",
            "pre_state_sha256": "1" * 64,
            "transition": "wave-replan",
            "arguments": arguments,
            "arguments_sha256": hashlib.sha256(stable_json(arguments)).hexdigest(),
            "resume_token": "4" * 64,
            "terminal_state_sha256": None,
            "projection_sha256": None,
            "updated_at": NOW.isoformat(),
        }
        path = self.run_dir / "orchestration" / "resume-control.json"
        core.write_atomic(path, stable_json(control))
        with mock.patch.object(resume, "_machine_observation") as machine:
            plan = resume.plan_run_resume(self.workspace, "run-test", clock=lambda: NOW)
        machine.assert_not_called()
        self.assertTrue(plan["replay"])
        self.assertEqual(plan["resume_token"], "4" * 64)
        self.assertEqual(plan["next_transition"], "wave-replan")
        self.assertEqual(plan["arguments"], arguments)

    def test_confirmed_recovery_creates_digest_bound_intent_without_token(
        self,
    ) -> None:
        idle = {
            "schema": resume.RESUME_CONTROL_SCHEMA,
            "run_id": "run-test",
            "epoch": 2,
            "adopted": True,
            "phase": "idle",
            "pre_state_sha256": "1" * 64,
            "transition": None,
            "arguments": None,
            "arguments_sha256": None,
            "resume_token": None,
            "terminal_state_sha256": "1" * 64,
            "projection_sha256": "2" * 64,
            "updated_at": NOW.isoformat(),
        }
        core.write_atomic(
            self.run_dir / "orchestration" / "resume-control.json",
            stable_json(idle),
        )
        plan = {
            "outcome": "requires_confirmation",
            "next_transition": "task-recover",
            "reason": "stale worker",
            "arguments": {"task_id": "task-1"},
            "required_arguments": [],
            "resume_token": None,
            "state_sha256": "1" * 64,
            "handoff_sha256": "2" * 64,
            "epoch": 2,
            "replay": False,
        }
        actual = {"task_id": "task-1", "confirmed_stopped": True}
        with mock.patch.object(resume, "plan_run_resume", return_value=plan):
            begun = resume.begin_resume_transition(
                self.workspace,
                "run-test",
                "task-recover",
                None,
                arguments=actual,
                clock=lambda: NOW,
            )
        self.assertEqual(begun["phase"], "intent")
        self.assertEqual(begun["arguments"], actual)
        self.assertRegex(str(begun["resume_token"]), r"^[0-9a-f]{64}$")
        with mock.patch.object(resume, "_machine_observation") as machine:
            replay = resume.begin_resume_transition(
                self.workspace,
                "run-test",
                "task-recover",
                None,
                arguments=actual,
                clock=lambda: NOW,
            )
        machine.assert_not_called()
        self.assertNotIn("_effect_complete", replay)

    def test_run_path_traversal_is_rejected_before_observation(self) -> None:
        with self.assertRaises(PromptWorkspaceError) as raised:
            resume.plan_run_resume(self.workspace, "../run-test", clock=lambda: NOW)
        self.assertEqual(raised.exception.code, "RUN_STATE_INVALID")

    def test_cli_binds_every_behavior_argument(self) -> None:
        cases = (
            (
                "wave-replan",
                {"capacity": 3},
                {"capacity": 3},
            ),
            (
                "task-rearm",
                {
                    "task_id": "task-1",
                    "expected_start_lease": "lease-1",
                    "confirmed_stopped": True,
                },
                {
                    "task_id": "task-1",
                    "expected_start_lease": "lease-1",
                    "confirmed_stopped": True,
                },
            ),
            (
                "run-finalize",
                {"alignment": "  align passed  "},
                {"alignment": "align passed"},
            ),
        )
        for command, fields, expected in cases:
            with self.subTest(command=command):
                namespace = type("Arguments", (), {"command": command, **fields})()
                self.assertEqual(cli._resume_transition_arguments(namespace), expected)

    def test_projection_crash_retains_exact_transition_for_replay(self) -> None:
        control = {
            "schema": resume.RESUME_CONTROL_SCHEMA,
            "run_id": "run-test",
            "epoch": 3,
            "adopted": True,
            "phase": "intent",
            "pre_state_sha256": "1" * 64,
            "transition": "wave-prepare",
            "arguments": {},
            "arguments_sha256": hashlib.sha256(stable_json({})).hexdigest(),
            "resume_token": "4" * 64,
            "terminal_state_sha256": None,
            "projection_sha256": None,
            "updated_at": NOW.isoformat(),
        }
        path = self.run_dir / "orchestration" / "resume-control.json"
        core.write_atomic(path, stable_json(control))
        after = observation()
        after["state_sha256"] = "5" * 64
        after["handoff_sha256"] = "6" * 64
        next_decision = {
            "outcome": "execute",
            "next_transition": "wave-dispatch",
            "reason": "next",
            "arguments": {},
        }
        with (
            mock.patch.object(resume, "_machine_observation", return_value=after),
            mock.patch.object(resume, "_choose_transition", return_value=next_decision),
            mock.patch.object(
                resume,
                "reconcile_handoff_projection",
                side_effect=PromptWorkspaceError("RESUME_STALE", "crash boundary"),
            ),
            self.assertRaises(PromptWorkspaceError),
        ):
            resume.complete_resume_transition(
                self.workspace,
                "run-test",
                "wave-prepare",
                "4" * 64,
                clock=lambda: NOW,
            )
        retained = resume.load_resume_control(self.run_dir, required=True)
        self.assertEqual(retained["phase"], "state-committed")
        self.assertEqual(retained["transition"], "wave-prepare")
        with mock.patch.object(resume, "_machine_observation", return_value=after):
            replay = resume.plan_run_resume(
                self.workspace, "run-test", clock=lambda: NOW
            )
        self.assertTrue(replay["replay"])
        self.assertEqual(replay["next_transition"], "resume-reconcile")

        with (
            mock.patch.object(resume, "_machine_observation", return_value=after),
            mock.patch.object(resume, "_choose_transition", return_value=next_decision),
            mock.patch.object(
                resume,
                "reconcile_handoff_projection",
                return_value=hashlib.sha256(b"").hexdigest(),
            ),
        ):
            resume.reconcile_committed_resume(
                self.workspace, "run-test", clock=lambda: NOW
            )
        completed = resume.load_resume_control(self.run_dir, required=True)
        self.assertEqual(completed["phase"], "idle")
        self.assertIsNone(completed["transition"])
        self.assertEqual(completed["terminal_state_sha256"], "5" * 64)

    def test_projection_retirement_preserves_later_handoff_metadata(self) -> None:
        handoff = self.run_dir / "handoff.md"
        handoff.write_text("later invocation metadata\n", encoding="utf-8")
        handoff.chmod(0o600)
        current_sha = hashlib.sha256(handoff.read_bytes()).hexdigest()
        control = {
            "schema": resume.RESUME_CONTROL_SCHEMA,
            "run_id": "run-test",
            "epoch": 3,
            "adopted": True,
            "phase": "projection-committed",
            "pre_state_sha256": "1" * 64,
            "transition": "wave-prepare",
            "arguments": {},
            "arguments_sha256": hashlib.sha256(stable_json({})).hexdigest(),
            "resume_token": "4" * 64,
            "terminal_state_sha256": "5" * 64,
            "projection_sha256": "6" * 64,
            "updated_at": NOW.isoformat(),
        }
        core.write_atomic(
            self.run_dir / "orchestration" / "resume-control.json",
            stable_json(control),
        )
        after = observation()
        after["state_sha256"] = "5" * 64
        after["handoff_sha256"] = current_sha
        decision = {
            "outcome": "execute",
            "next_transition": "wave-dispatch",
            "reason": "next",
            "arguments": {"contract_commit": "a" * 40},
        }
        with (
            mock.patch.object(resume, "_machine_observation", return_value=after),
            mock.patch.object(resume, "_choose_transition", return_value=decision),
            mock.patch.object(
                resume,
                "reconcile_handoff_projection",
                return_value=current_sha,
            ) as projection,
        ):
            resume.reconcile_committed_resume(
                self.workspace, "run-test", clock=lambda: NOW
            )
        projection.assert_called_once()
        completed = resume.load_resume_control(self.run_dir, required=True)
        self.assertEqual(completed["phase"], "idle")

    def test_resume_execution_lock_serializes_adopted_transitions(self) -> None:
        control = {
            "schema": resume.RESUME_CONTROL_SCHEMA,
            "run_id": "run-test",
            "epoch": 1,
            "adopted": True,
            "phase": "idle",
            "pre_state_sha256": "1" * 64,
            "transition": None,
            "arguments": None,
            "arguments_sha256": None,
            "resume_token": None,
            "terminal_state_sha256": "1" * 64,
            "projection_sha256": "2" * 64,
            "updated_at": NOW.isoformat(),
        }
        core.write_atomic(
            self.run_dir / "orchestration" / "resume-control.json",
            stable_json(control),
        )
        first_acquired = threading.Event()
        release_first = threading.Event()
        second_acquired = threading.Event()

        def hold_first() -> None:
            with resume.resume_execution_lock(self.workspace, "run-test"):
                with resume.scope_lock(self.run_dir.parent.parent):
                    first_acquired.set()
                    release_first.wait(timeout=2)

        def take_second() -> None:
            first_acquired.wait(timeout=2)
            with resume.resume_execution_lock(self.workspace, "run-test"):
                second_acquired.set()

        first = threading.Thread(target=hold_first)
        second = threading.Thread(target=take_second)
        first.start()
        second.start()
        self.assertTrue(first_acquired.wait(timeout=2))
        self.assertFalse(second_acquired.wait(timeout=0.1))
        release_first.set()
        first.join(timeout=2)
        second.join(timeout=2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertTrue(second_acquired.is_set())

    def test_resume_execution_lock_bootstraps_missing_orchestration_dir(self) -> None:
        orchestration = self.run_dir / "orchestration"
        self.assertFalse(orchestration.exists())
        with resume.resume_execution_lock(self.workspace, "run-test"):
            self.assertTrue(orchestration.is_dir())
            self.assertEqual(orchestration.stat().st_mode & 0o777, 0o700)
            self.assertTrue((orchestration / ".workspace.lock").is_file())

    @unittest.skipUnless(os.name == "posix", "process lock replay requires POSIX")
    def test_fresh_process_crash_releases_lock_and_retains_replay_intent(self) -> None:
        control = {
            "schema": resume.RESUME_CONTROL_SCHEMA,
            "run_id": "run-test",
            "epoch": 4,
            "adopted": True,
            "phase": "intent",
            "pre_state_sha256": "1" * 64,
            "transition": "wave-prepare",
            "arguments": {},
            "arguments_sha256": hashlib.sha256(stable_json({})).hexdigest(),
            "resume_token": "4" * 64,
            "terminal_state_sha256": None,
            "projection_sha256": None,
            "updated_at": NOW.isoformat(),
        }
        core.write_atomic(
            self.run_dir / "orchestration" / "resume-control.json",
            stable_json(control),
        )
        script_dir = Path(__file__).resolve().parent
        child = f"""
import os
import sys
sys.path.insert(0, {str(script_dir)!r})
import prompt_workspace_resume as resume
workspace = {{"runs_root": {str(self.run_dir.parent)!r}}}
with resume.resume_execution_lock(workspace, "run-test"):
    os._exit(73)
"""
        crashed = subprocess.run(
            [sys.executable, "-c", child],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(crashed.returncode, 73, crashed.stderr)
        with resume.resume_execution_lock(self.workspace, "run-test"):
            retained = resume.load_resume_control(self.run_dir, required=True)
        self.assertEqual(retained["phase"], "intent")
        with mock.patch.object(
            resume, "_machine_observation", return_value=observation()
        ):
            replay = resume.plan_run_resume(
                self.workspace, "run-test", clock=lambda: NOW
            )
        self.assertTrue(replay["replay"])
        self.assertEqual(replay["next_transition"], "wave-prepare")

    def test_parent_sync_failure_is_reported(self) -> None:
        target = self.run_dir / "state.json"
        with mock.patch.object(
            core,
            "_fsync_directory",
            side_effect=PromptWorkspaceError("WORKSPACE_PATH_INVALID", "sync failed"),
        ):
            with self.assertRaises(PromptWorkspaceError) as raised:
                core.write_atomic(target, b"{}\n")
        self.assertEqual(raised.exception.code, "WORKSPACE_PATH_INVALID")

    def test_projection_updates_indexed_machine_fields_and_preserves_narrative(
        self,
    ) -> None:
        template = (
            Path(__file__).resolve().parents[1] / "assets" / "handoff-template.md"
        ).read_text(encoding="utf-8")
        narrative = "Summarize the bound prompt revision and constraints"
        handoff = self.run_dir / "handoff.md"
        handoff.write_text(template, encoding="utf-8")
        handoff.chmod(0o600)
        expected = hashlib.sha256(template.encode("utf-8")).hexdigest()
        coordinator = {
            "status": "running",
            "active_wave": "wave-001",
            "waves": [{"wave_id": "wave-001"}],
        }
        wave = {
            "wave_id": "wave-001",
            "status": "running",
            "promoted_head": None,
            "task_ids": ["task-1"],
        }
        with (
            mock.patch.object(
                resume, "load_coordinator_state", return_value=coordinator
            ),
            mock.patch.object(resume, "_load_wave", return_value=wave),
            mock.patch.object(
                resume, "_load_task_plane", return_value={"state": "running"}
            ),
        ):
            actual = resume.reconcile_handoff_projection(
                self.workspace,
                "run-test",
                {"outcome": "wait", "next_transition": None},
                expected_sha256=expected,
            )
        updated = handoff.read_text(encoding="utf-8")
        self.assertEqual(actual, hashlib.sha256(updated.encode("utf-8")).hexdigest())
        self.assertIn("- Overall status: running", updated)
        self.assertIn("### wave-001\n\n- Status: running", updated)
        self.assertIn("### task-1\n\n- Status: in_progress", updated)
        self.assertIn(narrative, updated)
        self.assertIn("### task-2\n\n- Status: pending | in_progress", updated)

    def test_projection_maps_every_unpromoted_active_state_to_canonical_status(
        self,
    ) -> None:
        expected = {
            "planned": "pending",
            "assigned": "in_progress",
            "running": "in_progress",
            "committed": "in_progress",
            "merged": "in_progress",
            "failed": "blocked",
        }
        for plane_state, status in expected.items():
            with self.subTest(plane_state=plane_state):
                self.assertEqual(
                    resume._projected_task_status(plane_state, "promotion_pending"),
                    status,
                )
        self.assertEqual(resume._projected_task_status("merged", "done"), "done")

    def test_projection_recovery_replays_interrupted_write_and_preserves_input(
        self,
    ) -> None:
        coordinator, wave = self._projection_recovery_fixture()
        original_write_atomic = recovery.write_atomic
        interrupted = False

        def fail_handoff_once(path: Path, data: bytes) -> None:
            nonlocal interrupted
            if path.name == "handoff.md" and not interrupted:
                interrupted = True
                raise OSError("injected handoff write failure")
            original_write_atomic(path, data)

        with (
            mock.patch.object(
                recovery, "verify_workspace", return_value=self.workspace
            ),
            mock.patch.object(
                recovery, "load_coordinator_state", return_value=coordinator
            ),
            mock.patch.object(recovery, "_load_wave", return_value=wave),
            mock.patch.object(
                recovery, "_load_task_plane", return_value={"state": "merged"}
            ),
            mock.patch.object(recovery, "write_atomic", side_effect=fail_handoff_once),
            self.assertRaisesRegex(OSError, "injected handoff write failure"),
        ):
            expected = str(
                resume.load_resume_control(self.run_dir, required=True)[
                    "projection_sha256"
                ]
            )
            recovery.recover_handoff_projection(
                Path("workspace.json"), "run-test", expected, clock=lambda: NOW
            )
        journal = core.load_json_object(
            self.run_dir / "orchestration" / "handoff-projection-recovery.json",
            "projection recovery",
        )
        self.assertEqual(journal["phase"], "intent")
        recovered = self._recover_projection(coordinator, wave)
        text = (self.run_dir / "handoff.md").read_text(encoding="utf-8")
        self.assertEqual(recovered["task_ids"], ["task-1"])
        self.assertIn("### task-1\n\n- Status: in_progress", text)
        self.assertIn("unindexed correction input", text)
        self.assertFalse(recovered["machine_state_changed"])
        self.assertEqual(self._recover_projection(coordinator, wave), recovered)

    def test_projection_recovery_rejects_changed_handoff_and_active_control(
        self,
    ) -> None:
        coordinator, wave = self._projection_recovery_fixture()
        handoff_path = self.run_dir / "handoff.md"
        handoff_path.write_text(
            handoff_path.read_text(encoding="utf-8") + "\nchanged\n",
            encoding="utf-8",
        )
        with self.assertRaises(PromptWorkspaceError) as changed:
            self._recover_projection(coordinator, wave)
        self.assertEqual(changed.exception.code, "EXECUTION_STATE_INVALID")

        coordinator, wave = self._projection_recovery_fixture()
        control_path = self.run_dir / "orchestration" / "resume-control.json"
        control = core.load_json_object(control_path, "resume control")
        control.update(
            {
                "phase": "intent",
                "transition": "wave-replan",
                "arguments": {},
                "arguments_sha256": hashlib.sha256(stable_json({})).hexdigest(),
                "resume_token": "2" * 64,
            }
        )
        core.write_atomic(control_path, stable_json(control))
        with self.assertRaises(PromptWorkspaceError) as active:
            self._recover_projection(coordinator, wave)
        self.assertEqual(active.exception.code, "EXECUTION_STATE_INVALID")

    def test_hidden_projection_recovery_parser_binds_expected_preimage(self) -> None:
        parsed = cli.parse_args(
            [
                "handoff-projection-recover",
                "--workspace",
                "workspace.json",
                "--run-id",
                "run-test",
                "--expected-handoff-sha256",
                "1" * 64,
                "--json",
            ]
        )
        self.assertEqual(parsed.command, "handoff-projection-recover")
        self.assertEqual(parsed.expected_handoff_sha256, "1" * 64)

    def test_hidden_projection_recovery_routes_only_bound_inputs(self) -> None:
        expected = "1" * 64
        recovered = {
            "status": "recovered",
            "run_id": "run-test",
            "handoff_sha256": "2" * 64,
            "task_ids": ["task-1"],
            "machine_state_changed": False,
        }
        with (
            mock.patch.object(
                cli, "recover_handoff_projection", return_value=recovered
            ) as routed,
            mock.patch.object(cli, "emit") as emitted,
        ):
            return_code = cli.main(
                [
                    "handoff-projection-recover",
                    "--workspace",
                    "workspace.json",
                    "--run-id",
                    "run-test",
                    "--expected-handoff-sha256",
                    expected,
                    "--json",
                ]
            )
        self.assertEqual(return_code, 0)
        routed.assert_called_once_with(Path("workspace.json"), "run-test", expected)
        emitted.assert_called_once_with(recovered, True)

    def test_projection_recovery_rejects_unsafe_journal_and_inconsistent_plane(
        self,
    ) -> None:
        coordinator, wave = self._projection_recovery_fixture()
        journal = self.run_dir / "orchestration" / "handoff-projection-recovery.json"
        journal.symlink_to(self.run_dir / "missing-recovery.json")
        with self.assertRaises(PromptWorkspaceError) as unsafe:
            self._recover_projection(coordinator, wave)
        self.assertEqual(unsafe.exception.code, "EXECUTION_STATE_INVALID")
        journal.unlink()

        journal.write_text("{}\n", encoding="utf-8")
        journal.chmod(0o644)
        with self.assertRaises(PromptWorkspaceError) as mode:
            self._recover_projection(coordinator, wave)
        self.assertEqual(mode.exception.code, "WORKSPACE_PERMISSION_INVALID")
        journal.unlink()

        with (
            mock.patch.object(
                recovery, "verify_workspace", return_value=self.workspace
            ),
            mock.patch.object(
                recovery, "load_coordinator_state", return_value=coordinator
            ),
            mock.patch.object(recovery, "_load_wave", return_value=wave),
            mock.patch.object(
                recovery, "_load_task_plane", return_value={"state": "committed"}
            ),
            self.assertRaises(PromptWorkspaceError) as inconsistent,
        ):
            expected = str(
                resume.load_resume_control(self.run_dir, required=True)[
                    "projection_sha256"
                ]
            )
            recovery.recover_handoff_projection(
                Path("workspace.json"), "run-test", expected, clock=lambda: NOW
            )
        self.assertEqual(inconsistent.exception.code, "EXECUTION_STATE_INVALID")

    def test_projection_rejects_stale_handoff_digest(self) -> None:
        handoff = self.run_dir / "handoff.md"
        handoff.write_text("# changed\n", encoding="utf-8")
        handoff.chmod(0o600)
        with self.assertRaises(PromptWorkspaceError) as raised:
            resume.reconcile_handoff_projection(
                self.workspace,
                "run-test",
                {"outcome": "wait", "next_transition": None},
                expected_sha256="0" * 64,
            )
        self.assertEqual(raised.exception.code, "RESUME_STALE")


if __name__ == "__main__":
    unittest.main()
