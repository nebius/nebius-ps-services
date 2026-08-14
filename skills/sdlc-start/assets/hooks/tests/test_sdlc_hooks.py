#!/usr/bin/env python3
"""Unit tests for local Agentic SDLC Codex hooks."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


HOOK_DIR = Path(__file__).resolve().parents[1]
PRE_TOOL = HOOK_DIR / "pre_tool_use_sdlc_policy.py"
STOP = HOOK_DIR / "stop_sdlc_continue.py"


def run_hook(script: Path, payload: dict, codex_home: Path) -> dict:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    result = subprocess.run(
        ["python3", str(script)],
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def git(project: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(project),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


class HookTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.codex_home = self.root / "codex"
        self.origin = self.root / "origin.git"
        git(self.root, "init", "--bare", "-q", str(self.origin))
        self.project = self.root / "project"
        self.project.mkdir()
        try:
            git(self.project, "init", "-b", "main")
        except subprocess.CalledProcessError:
            git(self.project, "init")
            git(self.project, "branch", "-m", "main")
        git(self.project, "config", "user.email", "test@example.com")
        git(self.project, "config", "user.name", "Test User")
        (self.project / "src").mkdir()
        (self.project / "src" / "module.py").write_text(
            "print('hello')\n", encoding="utf-8"
        )
        (self.project / "docs").mkdir()
        (self.project / "docs" / "design.md").write_text(
            "# Design\n\nFEAT-001\n", encoding="utf-8"
        )
        git(self.project, "add", ".")
        git(self.project, "commit", "-m", "initial")
        git(self.project, "remote", "add", "origin", str(self.origin))
        git(self.project, "push", "-u", "origin", "main")
        git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        git(
            self.project,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def switch_feature(self) -> None:
        git(self.project, "switch", "-c", "agent/test")

    def active_run(
        self,
        *,
        status: str = "running",
        phase: str = "implementation",
        next_skill: str = "sdlc-validate-codes",
    ) -> Path:
        run_dir = self.codex_home / "sdlc-runs" / "test-project" / "run-1"
        lock = {
            "project_id": "test-project",
            "project_root": str(self.project),
            "run_id": "run-1",
            "status": status,
            "created_at": "2026-06-16T00:00:00Z",
        }
        write_json(run_dir.parent / "active.lock", lock)
        write_json(run_dir.parent / "active-run.json", {"run_id": "run-1"})
        prompt_filename = "20260716T000000Z--test-feature.md"
        write_json(
            run_dir / "run.json",
            {"status": status, "prompt": {"filename": prompt_filename}},
        )
        write_json(
            run_dir / "prompt.json",
            {
                "schema": "agentic-sdlc/prompt-binding-v2",
                "run_id": "run-1",
                "prompt_id": "prompt-" + "1" * 32,
                "prompt_filename": prompt_filename,
                "lineage_root": "run-1",
                "predecessor": None,
                "revisions": [
                    {
                        "revision": "r0001",
                        "sha256": "a" * 64,
                        "intent_sha256": "b" * 64,
                        "kind": "initial",
                        "snapshot": "inputs/r0001/prompt.md",
                        "steering_status": "initial",
                    }
                ],
            },
        )
        write_json(
            run_dir / "current-state.json",
            {
                "project_id": "test-project",
                "run_id": "run-1",
                "status": status,
                "current_feature": "FEAT-001",
                "current_phase": phase,
                "next_recommended_skill": next_skill,
                "retry_counts": {phase: 0},
                "iteration_count": 1,
                "max_iterations": 200,
                "needs_human": False,
            },
        )
        write_json(
            run_dir / "feature-queue.json",
            {"features": [{"id": "FEAT-001", "status": phase}]},
        )
        write_json(run_dir / "fingerprints.json", {})
        (run_dir / "history").mkdir(parents=True, exist_ok=True)
        (run_dir / "evidence" / "FEAT-001").mkdir(parents=True, exist_ok=True)
        return run_dir

    def write_repair_state(
        self,
        run_dir: Path,
        *,
        status: str,
        next_skill: str,
        with_diagnosis: bool = False,
        classification_route: str | None = None,
    ) -> None:
        event_id = "a" * 64
        diagnosis_id = "b" * 64 if with_diagnosis else None
        blocker_key = "component|operation|error-class|source-boundary"
        classification_record = (
            {
                "schema": "agentic-sdlc/failure-classification-v1",
                "feature_id": "FEAT-001",
                "event_id": event_id,
                "blocker_key": blocker_key,
                "next_recommended_skill": classification_route,
            }
            if classification_route
            else None
        )
        classification_id = (
            hashlib.sha256(
                json.dumps(
                    classification_record,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            ).hexdigest()
            if classification_record
            else None
        )
        event_path = run_dir / "repairs" / "FEAT-001" / "events" / f"{event_id}.json"
        control_path = run_dir / "repairs" / "FEAT-001" / "repair-control.json"
        write_json(
            event_path,
            {
                "schema": "agentic-sdlc/failure-event-v1",
                "feature_id": "FEAT-001",
                "event_id": event_id,
                "blocker_key": blocker_key,
            },
        )
        write_json(
            control_path,
            {
                "schema": "agentic-sdlc/repair-control-v1",
                "feature_id": "FEAT-001",
                "current_event_id": event_id,
                "current_diagnosis_id": diagnosis_id,
                "current_classification_id": classification_id,
                "status": status,
                "feature_dispatches": 0,
                "feature_dispatch_limit": 4,
                "route_history": (
                    [
                        {
                            "classification_id": classification_id,
                            "next_recommended_skill": classification_route,
                        }
                    ]
                    if classification_id
                    else []
                ),
                "active_blocker": {
                    "blocker_key": blocker_key,
                    "active_seconds": 0,
                    "time_limit_seconds": 3600,
                },
            },
        )
        pointer = {
            "schema": "agentic-sdlc/repair-state-pointer-v1",
            "failure_event": str(event_path.relative_to(run_dir)),
            "diagnosis": None,
            "control": str(control_path.relative_to(run_dir)),
        }
        if with_diagnosis:
            diagnosis_path = (
                run_dir / "repairs" / "FEAT-001" / "diagnoses" / f"{diagnosis_id}.json"
            )
            write_json(
                diagnosis_path,
                {
                    "schema": "agentic-sdlc/diagnosis-v1",
                    "feature_id": "FEAT-001",
                    "event_id": event_id,
                    "diagnosis_id": diagnosis_id,
                    "blocker_key": blocker_key,
                },
            )
            pointer["diagnosis"] = str(diagnosis_path.relative_to(run_dir))
        if classification_id:
            classification_path = (
                run_dir
                / "repairs"
                / "FEAT-001"
                / "classifications"
                / f"{classification_id}.json"
            )
            assert classification_record is not None
            classification_record["classification_id"] = classification_id
            write_json(classification_path, classification_record)
        state_path = run_dir / "current-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["next_recommended_skill"] = next_skill
        state["repair"] = pointer
        write_json(state_path, state)

    def authorize(self, run_dir: Path, name: str, **overrides: object) -> None:
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        authorization = {
            "allowed": True,
            "branch": "agent/test",
            "base_branch": "main",
            "base_head": git(self.project, "rev-parse", "origin/main"),
            "phase": name.removesuffix("-authorization.json"),
            "expires_at": expires.isoformat().replace("+00:00", "Z"),
        }
        authorization.update(overrides)
        write_json(
            run_dir / "permissions" / name,
            authorization,
        )

    def write_revalidation_cursor(
        self,
        run_dir: Path,
        *,
        complete: bool,
    ) -> None:
        control_path = run_dir / "repairs" / "FEAT-001" / "repair-control.json"
        control = json.loads(control_path.read_text(encoding="utf-8"))
        blocker_key = control["active_blocker"]["blocker_key"]
        surface = "commit" if complete else "validation"
        owner_skill = "sdlc-commit" if complete else "sdlc-validate-codes"
        classification = {
            "schema": "agentic-sdlc/failure-classification-v1",
            "feature_id": "FEAT-001",
            "event_id": control["current_event_id"],
            "blocker_key": blocker_key,
            "invalidates": [surface],
        }
        classification_id = hashlib.sha256(
            json.dumps(
                classification,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        classification["classification_id"] = classification_id
        write_json(
            run_dir
            / "repairs"
            / "FEAT-001"
            / "classifications"
            / f"{classification_id}.json",
            classification,
        )
        required = [
            {
                "surface": surface,
                "next_recommended_skill": owner_skill,
            }
        ]
        cursor_projection = {
            "schema": "agentic-sdlc/revalidation-cursor-v1",
            "classification_id": classification_id,
            "repair_dispatch_id": "dispatch-1",
            "required": required,
        }
        cursor_id = hashlib.sha256(
            json.dumps(
                cursor_projection,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        completed_ids: list[str] = []
        fingerprints = {
            "requirements": "1" * 64,
            "design": "2" * 64,
            "plan": "3" * 64,
        }
        integration_head = None
        if complete:
            integration = run_dir / "worktrees" / "FEAT-001" / "integration"
            branch_name = "codex/test/revalidation"
            git(self.project, "branch", branch_name, "HEAD")
            integration.parent.mkdir(parents=True, exist_ok=True)
            git(self.project, "worktree", "add", str(integration), branch_name)
            integration_head = git(integration, "rev-parse", "HEAD")
            gate = {
                "schema": "agentic-sdlc/gate-evidence-v1",
                "feature_id": "FEAT-001",
                "surface": surface,
                "owner_skill": owner_skill,
                "status": "passed",
                "integration_commit": integration_head,
                "fingerprints": fingerprints,
                "evidence": ["promotion and cleanup passed"],
            }
            content = (json.dumps(gate, sort_keys=True, indent=2) + "\n").encode(
                "utf-8"
            )
            source = run_dir / "evidence" / "FEAT-001" / "commit.json"
            source.write_bytes(content)
            git(self.project, "worktree", "remove", str(integration))
            git(self.project, "branch", "-d", branch_name)
            write_json(
                run_dir / "execution" / "FEAT-001" / "coordinator.json",
                {
                    "schema": "agentic-sdlc/execution-coordinator-v7",
                    "status": "done",
                    "base_branch": "main",
                    "project_root": str(self.project),
                    "selected_project_root": str(self.project),
                    "integration_branch": branch_name,
                    "integration_worktree": str(integration),
                    "integration_head": integration_head,
                    "promoted_head": integration_head,
                    "cleanup_retained": [],
                },
            )
            evidence = {
                "schema": "agentic-sdlc/revalidation-evidence-v1",
                "feature_id": "FEAT-001",
                "event_id": control["current_event_id"],
                "classification_id": classification_id,
                "repair_dispatch_id": "dispatch-1",
                "cursor_id": cursor_id,
                "blocker_key": blocker_key,
                "surface": surface,
                "next_recommended_skill": owner_skill,
                "integration_commit": integration_head,
                "fingerprints": fingerprints,
                "evidence_reference": "evidence/FEAT-001/commit.json",
                "evidence_digest": hashlib.sha256(content).hexdigest(),
                "recorded_at": "2026-07-28T10:20:00Z",
            }
            revalidation_id = hashlib.sha256(
                json.dumps(
                    evidence,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            ).hexdigest()
            evidence["revalidation_id"] = revalidation_id
            write_json(
                run_dir
                / "repairs"
                / "FEAT-001"
                / "revalidations"
                / f"{revalidation_id}.json",
                evidence,
            )
            completed_ids.append(revalidation_id)
        control["current_classification_id"] = classification_id
        control["active_blocker"]["attempts"] = [
            {
                "dispatch_id": "dispatch-1",
                "classification_id": classification_id,
                "status": "completed",
                "result": "succeeded",
            }
        ]
        control["invalidations"] = [
            {
                "event_id": control["current_event_id"],
                "classification_id": classification_id,
                "surface": surface,
            }
        ]
        control["revalidation"] = {
            "schema": "agentic-sdlc/revalidation-cursor-v1",
            "classification_id": classification_id,
            "repair_dispatch_id": "dispatch-1",
            "cursor_id": cursor_id,
            "status": "complete" if complete else "pending",
            "cursor": 1 if complete else 0,
            "required": required,
            "completed_revalidation_ids": completed_ids,
            "integration_commit": integration_head,
        }
        write_json(control_path, control)
        state_path = run_dir / "current-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["fingerprint_ids"] = [
            f"{name}:{value}" for name, value in fingerprints.items()
        ]
        write_json(state_path, state)

    def registered_integration(self) -> tuple[Path, Path]:
        run_dir = self.active_run(phase="execution_prepared", next_skill="sdlc-tdd")
        integration = run_dir / "worktrees" / "FEAT-001" / "integration"
        branch_name = "codex/sdlc/run-1/feat-001/integration"
        git(self.project, "branch", branch_name, "HEAD")
        integration.parent.mkdir(parents=True)
        git(self.project, "worktree", "add", str(integration), branch_name)
        common_raw = git(integration, "rev-parse", "--git-common-dir")
        common = Path(common_raw)
        if not common.is_absolute():
            common = (integration / common).resolve()
        write_json(
            run_dir / "execution" / "FEAT-001" / "coordinator.json",
            {
                "schema": "agentic-sdlc/execution-coordinator-v7",
                "feature_id": "FEAT-001",
                "project_root": str(self.project),
                "git_common_dir": str(common),
                "integration_worktree": str(integration),
                "integration_branch": branch_name,
                "integration_head": git(integration, "rev-parse", "HEAD"),
            },
        )
        return run_dir, integration

    def authorize_execution(
        self, run_dir: Path, worktree: Path, action: str, command: str
    ) -> None:
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        common_raw = git(worktree, "rev-parse", "--git-common-dir")
        common = Path(common_raw)
        if not common.is_absolute():
            common = (worktree / common).resolve()
        write_json(
            run_dir / "permissions" / "execution" / f"{action}.json",
            {
                "allowed": True,
                "action": action,
                "worktree": str(worktree),
                "branch": git(worktree, "branch", "--show-current"),
                "expected_head": git(worktree, "rev-parse", "HEAD"),
                "git_common_dir": str(common),
                "exact_command": command,
                "expires_at": expires.isoformat().replace("+00:00", "Z"),
            },
        )

    def registered_worker(self) -> tuple[Path, Path]:
        run_dir, integration = self.registered_integration()
        worker = run_dir / "worktrees" / "FEAT-001" / "waves" / "WAVE-001" / "TASK-001"
        branch_name = "codex/sdlc/run-1/feat-001/wave-001/task-001"
        git(integration, "branch", branch_name, "HEAD")
        worker.parent.mkdir(parents=True)
        git(integration, "worktree", "add", str(worker), branch_name)
        common_raw = git(worker, "rev-parse", "--git-common-dir")
        common = Path(common_raw)
        if not common.is_absolute():
            common = (worker / common).resolve()
        write_json(
            run_dir
            / "execution"
            / "FEAT-001"
            / "assignments"
            / "WAVE-001"
            / "TASK-001.json",
            {
                "schema": "agentic-sdlc/worker-assignment-v3",
                "feature_id": "FEAT-001",
                "wave_id": "WAVE-001",
                "task_id": "TASK-001",
                "worktree": str(worker),
                "branch": branch_name,
                "base_head": git(worker, "rev-parse", "HEAD"),
                "git_common_dir": str(common),
            },
        )
        return run_dir, worker

    def pre_payload(
        self,
        tool_name: str,
        command: str | None = None,
        tool_input: dict | None = None,
        cwd: Path | None = None,
    ) -> dict:
        return {
            "hook_event_name": "PreToolUse",
            "cwd": str(cwd or self.project),
            "turn_id": "turn-test",
            "tool_name": tool_name,
            "tool_use_id": "tool-test",
            "tool_input": tool_input
            if tool_input is not None
            else {"command": command or ""},
        }

    def stop_payload(self, *, active: bool = False) -> dict:
        return {
            "hook_event_name": "Stop",
            "cwd": str(self.project),
            "turn_id": "turn-test",
            "stop_hook_active": active,
            "last_assistant_message": "done",
        }

    def assert_denied(self, result: dict, text: str) -> None:
        output = result.get("hookSpecificOutput", {})
        self.assertEqual(output.get("permissionDecision"), "deny")
        self.assertIn(text, output.get("permissionDecisionReason", ""))

    def test_pretool_registration_omits_static_status_message(self) -> None:
        registration_path = HOOK_DIR.parent / "hooks.json.template"
        if not registration_path.is_file():
            registration_path = HOOK_DIR.parent / "hooks.json"
        registration = json.loads(registration_path.read_text(encoding="utf-8"))
        matches = [
            (group.get("matcher"), hook)
            for group in registration["hooks"]["PreToolUse"]
            for hook in group.get("hooks", [])
            if "pre_tool_use_sdlc_policy.py" in hook.get("command", "")
        ]

        self.assertEqual(len(matches), 1)
        matcher, hook = matches[0]
        self.assertEqual(matcher, "Bash|apply_patch|Edit|Write|mcp__.*")
        self.assertNotIn("statusMessage", hook)

    def test_pretool_allows_git_status(self) -> None:
        self.active_run()
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", "git status --short"), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_skips_sdlc_policy_without_active_run(self) -> None:
        private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
        private_key_end = "-----END " + "PRIVATE KEY-----"
        cases = (
            ("Bash", {"command": "git commit -m ordinary-task"}),
            ("Bash", {"command": "rm -rf /"}),
            (
                "apply_patch",
                {
                    "command": (
                        "*** Begin Patch\n"
                        "*** Add File: src/secret.txt\n"
                        f"+{private_key_marker}\n"
                        "+abc\n"
                        f"+{private_key_end}\n"
                        "*** End Patch\n"
                    )
                },
            ),
            (
                "mcp__slack__send_message",
                {"channel": "example", "message": "ordinary task"},
            ),
        )

        for tool_name, tool_input in cases:
            with self.subTest(tool_name=tool_name):
                result = run_hook(
                    PRE_TOOL,
                    self.pre_payload(tool_name, tool_input=tool_input),
                    self.codex_home,
                )
                self.assertEqual(result, {})

    def test_pretool_skips_sdlc_policy_for_unrelated_active_run(self) -> None:
        run_dir = self.active_run()
        unrelated_project = self.root / "unrelated-project"
        unrelated_project.mkdir()
        lock_path = run_dir.parent / "active.lock"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["project_root"] = str(unrelated_project)
        write_json(lock_path, lock)

        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "rm -rf /"),
            self.codex_home,
        )

        self.assertEqual(result, {})

    def test_pretool_allows_public_nebius_metadata_assignments(self) -> None:
        self.active_run()
        prefix = "NEBIUS" + "_"
        commands = (
            prefix
            + "PROFILE="
            + "codex-agent-project-1234567890 nebius iam project get",
            prefix + "PROJECT_ID=" + "project-1234567890 command true",
            prefix
            + "AUTH_CREDENTIALS_FILE="
            + "/tmp/codex-agent-authkey.project-1234567890.json command true",
        )

        for command in commands:
            with self.subTest(variable=command.split("=", 1)[0]):
                result = run_hook(
                    PRE_TOOL, self.pre_payload("Bash", command), self.codex_home
                )
                self.assertEqual(result, {})

    def test_pretool_nebius_metadata_cannot_mask_secret_assignment(self) -> None:
        self.active_run()
        prefix = "NEBIUS" + "_"
        command = (
            prefix
            + "PROFILE=codex-agent-project-1234567890 "
            + prefix
            + "IAM_"
            + "TOKEN="
            + "x" * 32
            + " command true"
        )

        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)

        self.assert_denied(result, "secret")

    def test_pretool_denies_unknown_nebius_assignment(self) -> None:
        self.active_run()
        prefix = "NEBIUS" + "_"
        command = prefix + "RUNTIME_VALUE=" + "x" * 32 + " command true"

        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)

        self.assert_denied(result, "secret")

    def test_pretool_allows_project_patch(self) -> None:
        self.active_run()
        patch = (
            "*** Begin Patch\n*** Add File: src/new.py\n+print('ok')\n*** End Patch\n"
        )
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_patch_containing_dockerfile_ownership_text(self) -> None:
        self.active_run()
        ownership_command = "cho" + "wn " + "-R app:app /app"
        patch = (
            "*** Begin Patch\n"
            "*** Add File: Dockerfile\n"
            "+FROM example/app:1.2.3\n"
            f"+RUN {ownership_command}\n"
            "*** End Patch\n"
        )
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_initial_sdlc_state_patch(self) -> None:
        state_path = (
            self.codex_home
            / "sdlc-runs"
            / "test-project"
            / "run-1"
            / "current-state.json"
        )
        patch = f"*** Begin Patch\n*** Add File: {state_path}\n+{{}}\n*** End Patch\n"
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_global_task_state_patch(self) -> None:
        self.active_run()
        state_path = (
            self.codex_home
            / "task-state"
            / "workspace-abc"
            / "session-1"
            / "current.md"
        )
        patch = f"*** Begin Patch\n*** Add File: {state_path}\n+# Current Codex task state\n*** End Patch\n"
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_outside_project_patch(self) -> None:
        self.active_run()
        outside_path = self.root / "outside-project" / "note.md"
        patch = f"*** Begin Patch\n*** Add File: {outside_path}\n+external note\n*** End Patch\n"
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_outside_project_delete_patch(self) -> None:
        self.active_run()
        outside_path = self.root / "outside-project" / "old-note.md"
        patch = f"*** Begin Patch\n*** Delete File: {outside_path}\n*** End Patch\n"
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_outside_project_bash_write(self) -> None:
        self.active_run()
        outside_path = self.root / "outside-project" / "note.md"
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", f"tee {outside_path}"), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_outside_project_bash_delete(self) -> None:
        self.active_run()
        outside_path = self.root / "outside-project" / "old-note.md"
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", f"rm {outside_path}"), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_mcp_outside_project_write(self) -> None:
        self.active_run()
        outside_path = self.root / "outside-project" / "note.md"
        result = run_hook(
            PRE_TOOL,
            self.pre_payload(
                "mcp__filesystem__write_file",
                tool_input={"path": str(outside_path), "content": "external note\n"},
            ),
            self.codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_allows_mcp_outside_project_delete(self) -> None:
        self.active_run()
        outside_path = self.root / "outside-project" / "old-note.md"
        result = run_hook(
            PRE_TOOL,
            self.pre_payload(
                "mcp__filesystem__delete_file", tool_input={"path": str(outside_path)}
            ),
            self.codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_allows_global_agents_apply_patch(self) -> None:
        self.active_run()
        codex_home = self.codex_home
        agents_path = codex_home / "AGENTS.md"
        patch = f"*** Begin Patch\n*** Add File: {agents_path}\n+# Global AGENTS.md\n*** End Patch\n"
        result = run_hook(PRE_TOOL, self.pre_payload("apply_patch", patch), codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_global_agents_delete_patch(self) -> None:
        self.active_run()
        codex_home = self.codex_home
        agents_path = codex_home / "AGENTS.md"
        patch = f"*** Begin Patch\n*** Delete File: {agents_path}\n*** End Patch\n"
        result = run_hook(PRE_TOOL, self.pre_payload("apply_patch", patch), codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_move_from_global_agents(self) -> None:
        self.active_run()
        codex_home = self.codex_home
        agents_path = codex_home / "AGENTS.md"
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {agents_path}\n"
            "*** Move to: docs/moved-agents.md\n"
            "@@\n"
            "-# Global AGENTS.md\n"
            "+# Moved\n"
            "*** End Patch\n"
        )
        result = run_hook(PRE_TOOL, self.pre_payload("apply_patch", patch), codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_move_to_global_agents(self) -> None:
        self.active_run()
        codex_home = self.codex_home
        agents_path = codex_home / "AGENTS.md"
        patch = (
            "*** Begin Patch\n"
            "*** Update File: src/module.py\n"
            f"*** Move to: {agents_path}\n"
            "@@\n"
            "-print('hello')\n"
            "+# Global AGENTS.md\n"
            "*** End Patch\n"
        )
        result = run_hook(PRE_TOOL, self.pre_payload("apply_patch", patch), codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_global_hooks_apply_patch(self) -> None:
        self.active_run()
        codex_home = self.codex_home
        hook_path = codex_home / "hooks" / "user_prompt_context.py"
        patch = f"*** Begin Patch\n*** Add File: {hook_path}\n+print('blocked')\n*** End Patch\n"
        result = run_hook(PRE_TOOL, self.pre_payload("apply_patch", patch), codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_global_config_apply_patch(self) -> None:
        self.active_run()
        codex_home = self.codex_home
        config_path = codex_home / "config.toml"
        patch = f"*** Begin Patch\n*** Add File: {config_path}\n+[features]\n*** End Patch\n"
        result = run_hook(PRE_TOOL, self.pre_payload("apply_patch", patch), codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_bash_global_agents_write(self) -> None:
        self.active_run()
        codex_home = self.codex_home
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", f"tee {codex_home / 'AGENTS.md'}"),
            codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_allows_non_obvious_bash_global_agents_write(self) -> None:
        self.active_run()
        codex_home = self.codex_home
        command = f"python3 -c \"open('{codex_home / 'AGENTS.md'}','w').write('x')\""
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_bash_global_agents_read_only_inspection(self) -> None:
        self.active_run()
        codex_home = self.codex_home
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", f"cat {codex_home / 'AGENTS.md'}"),
            codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_allows_mcp_global_agents_write(self) -> None:
        self.active_run()
        codex_home = self.codex_home
        result = run_hook(
            PRE_TOOL,
            self.pre_payload(
                "mcp__filesystem__write_file",
                tool_input={
                    "path": str(codex_home / "AGENTS.md"),
                    "content": "# Global AGENTS.md\n",
                },
            ),
            codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_allows_mcp_read(self) -> None:
        self.active_run()
        result = run_hook(
            PRE_TOOL,
            self.pre_payload(
                "mcp__filesystem__read_file",
                tool_input={"path": str(self.project / "src" / "module.py")},
            ),
            self.codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_allows_credential_patch(self) -> None:
        self.active_run()
        patch = "*** Begin Patch\n*** Add File: ~/.ssh/config\n+Host example\n*** End Patch\n"
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_locked_plan_patch(self) -> None:
        run_dir = self.active_run()
        plan = run_dir / "plans" / "FEAT-001.plan.v1.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text("# Plan\n", encoding="utf-8")
        plan.with_suffix(plan.suffix + ".lock").write_text("locked\n", encoding="utf-8")
        patch = f"*** Begin Patch\n*** Update File: {plan}\n@@\n-# Plan\n+# Changed\n*** End Patch\n"
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_private_state_copy_command(self) -> None:
        self.active_run()
        command = "cp -R ~/." + "codex/sdlc-runs/demo ."
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_private_state_stage_command(self) -> None:
        self.active_run()
        command = "git " + "add ~/." + "codex/sdlc-runs/demo"
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_external_network_curl(self) -> None:
        self.active_run()
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "curl -I https://example.com"),
            self.codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_allows_external_network_ssh(self) -> None:
        self.active_run()
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", "ssh example.com true"), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_external_network_scp(self) -> None:
        self.active_run()
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "scp local.txt example.com:/tmp/local.txt"),
            self.codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_denies_commit_on_main(self) -> None:
        self.active_run()
        (self.project / "src" / "main_change.py").write_text(
            "print('x')\n", encoding="utf-8"
        )
        git(self.project, "add", ".")
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", "git commit -m test"), self.codex_home
        )
        self.assert_denied(result, "protected branch")

    def test_pretool_denies_commit_without_authorization(self) -> None:
        self.switch_feature()
        self.active_run()
        (self.project / "src" / "feature.py").write_text(
            "print('feature')\n", encoding="utf-8"
        )
        git(self.project, "add", ".")
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", "git commit -m feature"), self.codex_home
        )
        self.assert_denied(result, "commit authorization")

    def test_pretool_denies_direct_commit_transaction_during_active_sdlc(self) -> None:
        self.switch_feature()
        self.active_run()
        for action in ("prepare", "execute", "review"):
            command = (
                "python3 /Users/example/.agents/skills/commit/scripts/"
                f"commit_transaction.py {action} --private canonical"
            )
            with self.subTest(action=action):
                result = run_hook(
                    PRE_TOOL,
                    self.pre_payload("Bash", command),
                    self.codex_home,
                )
                self.assert_denied(result, "use sdlc-commit")

    def test_pretool_detects_registered_integration_outside_project(self) -> None:
        _run_dir, integration = self.registered_integration()
        (integration / "feature.py").write_text("value = 1\n", encoding="utf-8")
        git(integration, "add", "feature.py")
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "git commit -m feature", cwd=integration),
            self.codex_home,
        )
        self.assert_denied(result, "commit authorization")

    def test_pretool_denies_unregistered_private_worktree(self) -> None:
        run_dir, integration = self.registered_integration()
        coordinator = run_dir / "execution" / "FEAT-001" / "coordinator.json"
        coordinator.unlink()
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "git commit -m feature", cwd=integration),
            self.codex_home,
        )
        self.assert_denied(result, "worktree identity changed")

    def test_pretool_allows_registered_integration_merge_with_exact_authorization(
        self,
    ) -> None:
        run_dir, integration = self.registered_integration()
        command = "git merge --no-ff --no-edit worker/test"
        self.authorize_execution(run_dir, integration, "integration-merge", command)
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", command, cwd=integration),
            self.codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_denies_execution_authorization_without_exact_scope(self) -> None:
        run_dir, integration = self.registered_integration()
        command = "git merge --no-ff --no-edit worker/test"
        self.authorize_execution(run_dir, integration, "integration-merge", command)
        auth_path = run_dir / "permissions" / "execution" / "integration-merge.json"
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
        auth.pop("exact_command")
        write_json(auth_path, auth)
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", command, cwd=integration),
            self.codex_home,
        )
        self.assert_denied(result, "execution authorization")

    def test_pretool_allows_registered_worker_commit_with_exact_authorization(
        self,
    ) -> None:
        run_dir, worker = self.registered_worker()
        (worker / "worker.py").write_text("value = 1\n", encoding="utf-8")
        git(worker, "add", "worker.py")
        command = "git commit -m worker"
        self.authorize_execution(run_dir, worker, "worker-commit", command)
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", command, cwd=worker),
            self.codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_denies_registered_integration_head_drift(self) -> None:
        run_dir, integration = self.registered_integration()
        self.authorize(run_dir, "commit-authorization.json")
        (integration / "drift.py").write_text("drift = True\n", encoding="utf-8")
        git(integration, "add", "drift.py")
        git(integration, "commit", "-m", "unrecorded drift")
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "git commit -m next", cwd=integration),
            self.codex_home,
        )
        self.assert_denied(result, "worktree identity changed")

    def test_pretool_denies_force_worktree_removal(self) -> None:
        run_dir, integration = self.registered_integration()
        command = f"git worktree remove --force {integration}"
        self.authorize_execution(run_dir, self.project, "resource-cleanup", command)
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", command),
            self.codex_home,
        )
        self.assert_denied(result, "force worktree removal")

    def test_pretool_denies_staged_private_evidence(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(run_dir, "commit-authorization.json")
        (self.project / "evidence").mkdir()
        (self.project / "evidence" / "note.md").write_text(
            "private\n", encoding="utf-8"
        )
        git(self.project, "add", ".")
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", "git commit -m feature"), self.codex_home
        )
        self.assert_denied(result, "private SDLC state")

    def test_pretool_denies_staged_private_key(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(run_dir, "commit-authorization.json")
        (self.project / "src" / "secret.txt").write_text(
            "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
            encoding="utf-8",
        )
        git(self.project, "add", ".")
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", "git commit -m feature"), self.codex_home
        )
        self.assert_denied(result, "secret")

    def test_pretool_denies_real_secret_even_with_example_line(self) -> None:
        self.active_run()
        patch = (
            "*** Begin Patch\n"
            "*** Add File: src/secret.txt\n"
            "+example placeholder line\n"
            "+-----BEGIN PRIVATE KEY-----\n"
            "+abc\n"
            "+-----END PRIVATE KEY-----\n"
            "*** End Patch\n"
        )
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assert_denied(result, "secret")

    def test_pretool_denies_push_without_pr_authorization(self) -> None:
        self.switch_feature()
        self.active_run()
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", "git push origin HEAD"), self.codex_home
        )
        self.assert_denied(result, "PR authorization")

    def test_pretool_allows_push_with_exact_create_pr_authorization(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
        )
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "git push origin HEAD:agent/test"),
            self.codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_denies_push_when_authorized_head_is_stale(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head="0" * 40,
            uat_status="passed",
        )
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", "git push origin HEAD"), self.codex_home
        )
        self.assert_denied(result, "expected HEAD")

    def test_pretool_denies_push_when_remote_default_head_advanced(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
            base_head="0" * 40,
        )
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "git push origin HEAD:agent/test"),
            self.codex_home,
        )
        self.assert_denied(result, "base HEAD")

    def test_pretool_denies_push_without_passing_uat_authorization(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
        )
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", "git push origin HEAD"), self.codex_home
        )
        self.assert_denied(result, "UAT status")

    def test_pretool_denies_push_with_wrong_authorization_phase(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="review-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
        )
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "git push origin HEAD:agent/test"),
            self.codex_home,
        )
        self.assert_denied(result, "not create-pr")

    def test_pretool_denies_push_without_expiring_authorization(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
            expires_at=None,
        )
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "git push origin HEAD:agent/test"),
            self.codex_home,
        )
        self.assert_denied(result, "missing expires_at")

    def test_pretool_denies_authorized_push_to_a_different_ref(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
        )
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "git push origin HEAD:main"),
            self.codex_home,
        )
        self.assert_denied(result, "exact HEAD:agent/test refspec")

    def test_pretool_denies_authorized_push_with_extra_refs_or_tags(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
        )
        commands = (
            "git push origin HEAD:agent/test other:other",
            "git push origin HEAD:agent/test --tags",
        )
        for command in commands:
            with self.subTest(command=command):
                result = run_hook(
                    PRE_TOOL,
                    self.pre_payload("Bash", command),
                    self.codex_home,
                )
                self.assert_denied(result, "exact HEAD:agent/test refspec")

    def test_pretool_denies_wrapped_authorized_push(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
        )
        command = "env git push origin HEAD:agent/test"
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", command),
            self.codex_home,
        )
        self.assert_denied(result, "without a wrapper or prepended command")

    def test_pretool_denies_gh_pr_create_without_authorization(self) -> None:
        self.switch_feature()
        self.active_run()
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "gh pr create --fill"),
            self.codex_home,
        )
        self.assert_denied(result, "PR authorization")

    def test_pretool_allows_gh_pr_create_with_exact_authorization(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
        )
        result = run_hook(
            PRE_TOOL,
            self.pre_payload(
                "Bash", "gh pr create --base main --head agent/test --fill"
            ),
            self.codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_denies_gh_pr_create_without_explicit_head(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
        )
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "gh pr create --fill"),
            self.codex_home,
        )
        self.assert_denied(result, "head must match")

    def test_pretool_denies_gh_pr_create_without_exact_base(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
        )
        commands = (
            "gh pr create --head agent/test --fill",
            "gh pr create --base other --head agent/test --fill",
            "gh pr create -Bother -Hagent/test --fill",
            "gh pr create -B -Hagent/test --fill",
        )
        for command in commands:
            with self.subTest(command=command):
                result = run_hook(
                    PRE_TOOL,
                    self.pre_payload("Bash", command),
                    self.codex_home,
                )
                self.assert_denied(result, "base must match")

    def test_pretool_denies_gh_pr_create_when_remote_default_head_advanced(
        self,
    ) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
            base_head="0" * 40,
        )
        result = run_hook(
            PRE_TOOL,
            self.pre_payload(
                "Bash", "gh pr create --base main --head agent/test --fill"
            ),
            self.codex_home,
        )
        self.assert_denied(result, "base HEAD")

    def test_pretool_denies_compound_gh_pr_create(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
        )
        command = "gh pr create --head agent/test --fill && git status"
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", command),
            self.codex_home,
        )
        self.assert_denied(result, "one direct shell action")

    def test_pretool_denies_gh_pr_create_for_a_different_head(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
        )
        commands = (
            "gh pr create --head other --fill",
            "gh pr create -H other --fill",
            "gh pr create -Hother --fill",
            "gh pr create -H",
        )
        for command in commands:
            with self.subTest(command=command):
                result = run_hook(
                    PRE_TOOL,
                    self.pre_payload("Bash", command),
                    self.codex_home,
                )
                self.assert_denied(result, "head must match")

    def test_pretool_allows_github_pr_read_without_authorization(self) -> None:
        self.switch_feature()
        self.active_run()
        result = run_hook(
            PRE_TOOL,
            self.pre_payload(
                "mcp__github__pull_request_read",
                tool_input={"owner": "example", "repo": "demo", "pullNumber": 1},
            ),
            self.codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_guards_only_github_pr_creation_writes(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        payload = self.pre_payload(
            "mcp__github__create_pull_request",
            tool_input={
                "owner": "example",
                "repo": "demo",
                "base": "main",
                "head": "agent/test",
            },
        )
        denied = run_hook(PRE_TOOL, payload, self.codex_home)
        self.assert_denied(denied, "PR authorization")

        self.authorize(
            run_dir,
            "pr-authorization.json",
            phase="create-pr",
            expected_head=git(self.project, "rev-parse", "HEAD"),
            uat_status="passed",
        )
        allowed = run_hook(PRE_TOOL, payload, self.codex_home)
        self.assertEqual(allowed, {})

        wrong_head = self.pre_payload(
            "mcp__github__create_pull_request",
            tool_input={
                "owner": "example",
                "repo": "demo",
                "base": "main",
                "head": "other",
            },
        )
        denied = run_hook(PRE_TOOL, wrong_head, self.codex_home)
        self.assert_denied(denied, "head must match")

        wrong_base = self.pre_payload(
            "mcp__github__create_pull_request",
            tool_input={
                "owner": "example",
                "repo": "demo",
                "base": "other",
                "head": "agent/test",
            },
        )
        denied = run_hook(PRE_TOOL, wrong_base, self.codex_home)
        self.assert_denied(denied, "base must match")

    def test_pretool_denies_gh_pr_merge_without_authorization(self) -> None:
        self.switch_feature()
        self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        command = f"gh pr merge 42 --squash --match-head-commit {head}"
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assert_denied(result, "merge authorization")

    def test_pretool_denies_wrapped_or_prepended_gh_pr_merge(self) -> None:
        self.switch_feature()
        self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        commands = (
            f"command gh pr merge 42 --squash --match-head-commit {head}",
            f"env gh pr merge 42 --squash --match-head-commit {head}",
            f"/opt/example/bin/gh pr merge 42 --squash --match-head-commit {head}",
            f"true && gh pr merge 42 --squash --match-head-commit {head}",
        )
        for command in commands:
            with self.subTest(command=command):
                result = run_hook(
                    PRE_TOOL,
                    self.pre_payload("Bash", command),
                    self.codex_home,
                )
                self.assert_denied(result, "without a wrapper or prepended command")

    def test_pretool_denies_nested_shell_gh_pr_merge(self) -> None:
        self.switch_feature()
        self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        nested = f"gh pr merge 42 --squash --match-head-commit {head}"
        command = f"bash -lc {shlex.quote(nested)}"
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assert_denied(result, "must not run through a nested shell")

    def test_pretool_allows_quoted_gh_pr_merge_documentation_search(self) -> None:
        self.switch_feature()
        self.active_run()
        command = "rg 'gh pr merge' docs"
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_exact_authorized_gh_pr_merge(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        command = f"gh pr merge 42 --squash --match-head-commit {head}"
        self.authorize(
            run_dir,
            "merge-authorization.json",
            phase="sdlc-merge-pr",
            pr="42",
            expected_head=head,
            exact_command=command,
            explicit_user_request=True,
            checks_status="passed",
            review_status="passed",
            uat_status="passed",
        )
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assertEqual(result, {})

    def test_pretool_denies_merge_when_remote_default_head_advanced(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        command = f"gh pr merge 42 --squash --match-head-commit {head}"
        self.authorize(
            run_dir,
            "merge-authorization.json",
            phase="sdlc-merge-pr",
            pr="42",
            expected_head=head,
            exact_command=command,
            explicit_user_request=True,
            checks_status="passed",
            review_status="passed",
            uat_status="passed",
            base_head="0" * 40,
        )
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assert_denied(result, "base HEAD")

    def test_pretool_allows_exact_authorized_merge_queue_command(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        command = f"gh pr merge 42 --match-head-commit {head}"
        self.authorize(
            run_dir,
            "merge-authorization.json",
            phase="sdlc-merge-pr",
            pr="42",
            expected_head=head,
            exact_command=command,
            explicit_user_request=True,
            checks_status="passed",
            review_status="passed",
            uat_status="passed",
        )
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_exact_authorized_pr_url_merge(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        pr_url = "https://github.example/example/demo/pull/42"
        command = f"gh pr merge {pr_url} --squash --match-head-commit {head}"
        self.authorize(
            run_dir,
            "merge-authorization.json",
            phase="sdlc-merge-pr",
            pr=pr_url,
            expected_head=head,
            exact_command=command,
            explicit_user_request=True,
            checks_status="passed",
            review_status="passed",
            uat_status="passed",
        )
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assertEqual(result, {})

    def test_pretool_denies_merge_without_exact_head_guard(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        command = "gh pr merge 42 --squash"
        self.authorize(
            run_dir,
            "merge-authorization.json",
            phase="sdlc-merge-pr",
            pr="42",
            expected_head=head,
            exact_command=command,
            explicit_user_request=True,
            checks_status="passed",
            review_status="passed",
            uat_status="passed",
        )
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assert_denied(result, "match the authorized current HEAD")

    def test_pretool_denies_merge_command_outside_exact_authorization(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        authorized = f"gh pr merge 42 --squash --match-head-commit {head}"
        attempted = f"gh pr merge 43 --squash --match-head-commit {head}"
        self.authorize(
            run_dir,
            "merge-authorization.json",
            phase="sdlc-merge-pr",
            pr="42",
            expected_head=head,
            exact_command=authorized,
            explicit_user_request=True,
            checks_status="passed",
            review_status="passed",
            uat_status="passed",
        )
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", attempted), self.codex_home
        )
        self.assert_denied(result, "exact_command")

    def test_pretool_denies_merge_without_explicit_pr_target(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        command = f"gh pr merge --squash --match-head-commit {head}"
        self.authorize(
            run_dir,
            "merge-authorization.json",
            phase="sdlc-merge-pr",
            pr="42",
            expected_head=head,
            exact_command=command,
            explicit_user_request=True,
            checks_status="passed",
            review_status="passed",
            uat_status="passed",
        )
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assert_denied(result, "explicit PR number or URL")

    def test_pretool_denies_merge_admin_bypass(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        command = f"gh pr merge 42 --admin --match-head-commit {head}"
        self.authorize(
            run_dir,
            "merge-authorization.json",
            phase="sdlc-merge-pr",
            pr="42",
            expected_head=head,
            exact_command=command,
            explicit_user_request=True,
            checks_status="passed",
            review_status="passed",
            uat_status="passed",
        )
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assert_denied(result, "unsupported strategy or flag")

    def test_pretool_denies_merge_branch_deletion(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        command = f"gh pr merge 42 --squash --delete-branch --match-head-commit {head}"
        self.authorize(
            run_dir,
            "merge-authorization.json",
            phase="sdlc-merge-pr",
            pr="42",
            expected_head=head,
            exact_command=command,
            explicit_user_request=True,
            checks_status="passed",
            review_status="passed",
            uat_status="passed",
        )
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assert_denied(result, "canonical single-action form")

    def test_pretool_denies_compound_merge_command(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        command = f"gh pr merge 42 --squash --match-head-commit {head} && git status"
        self.authorize(
            run_dir,
            "merge-authorization.json",
            phase="sdlc-merge-pr",
            pr="42",
            expected_head=head,
            exact_command=command,
            explicit_user_request=True,
            checks_status="passed",
            review_status="passed",
            uat_status="passed",
        )
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assert_denied(result, "one direct shell action")

    def test_pretool_denies_merge_pr_scope_mismatch(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        command = f"gh pr merge 42 --squash --match-head-commit {head}"
        self.authorize(
            run_dir,
            "merge-authorization.json",
            phase="sdlc-merge-pr",
            pr="43",
            expected_head=head,
            exact_command=command,
            explicit_user_request=True,
            checks_status="passed",
            review_status="passed",
            uat_status="passed",
        )
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assert_denied(result, "PR does not match")

    def test_pretool_denies_merge_without_passing_readiness(self) -> None:
        self.switch_feature()
        run_dir = self.active_run()
        head = git(self.project, "rev-parse", "HEAD")
        command = f"gh pr merge 42 --squash --match-head-commit {head}"
        self.authorize(
            run_dir,
            "merge-authorization.json",
            phase="sdlc-merge-pr",
            pr="42",
            expected_head=head,
            exact_command=command,
            explicit_user_request=True,
            checks_status="failed",
            review_status="passed",
            uat_status="passed",
        )
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assert_denied(result, "checks status")

    def test_pretool_denies_github_merge_mcp_in_active_sdlc(self) -> None:
        self.switch_feature()
        self.active_run()
        result = run_hook(
            PRE_TOOL,
            self.pre_payload(
                "mcp__github__merge_pull_request",
                tool_input={"owner": "example", "repo": "demo", "pullNumber": 42},
            ),
            self.codex_home,
        )
        self.assert_denied(result, "must use the exact authorized gh pr merge")

    def test_pretool_denies_force_push(self) -> None:
        self.switch_feature()
        self.active_run()
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "git push --force origin HEAD"),
            self.codex_home,
        )
        self.assert_denied(result, "force push")

    def test_pretool_denies_dangerous_rm(self) -> None:
        self.active_run()
        for command, reason in (
            ("rm -rf /", "recursive removal"),
            ("find / -depth -delete", "filesystem root"),
            ("find /./ -depth -delete", "filesystem root"),
            ("find /tmp/.. -depth -delete", "filesystem root"),
            ('find "/" -depth -delete', "filesystem root"),
            ("find // -depth -delete", "filesystem root"),
            ("find / -depth -{delete,print}", "filesystem root"),
            ("find / -depth -{d{elete,ummy},print}", "filesystem root"),
            ("find / -depth -{de,xx}{lete,yy}", "filesystem root"),
            ("find / -depth {-,x}delete", "filesystem root"),
            ("find / -depth -del*", "filesystem root"),
        ):
            with self.subTest(command=command):
                result = run_hook(
                    PRE_TOOL, self.pre_payload("Bash", command), self.codex_home
                )
                self.assert_denied(result, reason)

    def test_pretool_denies_recursive_ownership_shell_command(self) -> None:
        self.active_run()
        ownership_command = "cho" + "wn " + "-R app:app /app"
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", ownership_command),
            self.codex_home,
        )
        self.assert_denied(result, "recursive chown")

    def test_pretool_warns_design_edit_outside_design_phase(self) -> None:
        self.active_run(phase="implementation")
        patch = "*** Begin Patch\n*** Update File: docs/design.md\n@@\n # Design\n+More\n*** End Patch\n"
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertIn("additionalContext", result.get("hookSpecificOutput", {}))

    def test_pretool_warns_spec_id_delete_without_blocking(self) -> None:
        self.active_run(phase="design_update")
        patch = "*** Begin Patch\n*** Update File: docs/design.md\n@@\n-FEAT-001\n+Removed\n*** End Patch\n"
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertIn("additionalContext", result.get("hookSpecificOutput", {}))
        self.assertNotEqual(
            result.get("hookSpecificOutput", {}).get("permissionDecision"), "deny"
        )

    def test_pretool_allows_design_edit_in_design_phase(self) -> None:
        self.active_run(phase="design_update")
        patch = "*** Begin Patch\n*** Update File: docs/design.md\n@@\n # Design\n+More\n*** End Patch\n"
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertEqual(result, {})

    def test_stop_allows_no_active_run(self) -> None:
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertEqual(result, {"continue": True})

    def test_stop_stops_complete_run(self) -> None:
        self.active_run(status="complete")
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("complete", result["stopReason"])

    def test_stop_continues_running_next_skill(self) -> None:
        self.active_run(next_skill="sdlc-validate-codes")
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertEqual(result.get("decision"), "block")
        self.assertIn(
            "Use $sdlc-start run 20260716T000000Z--test-feature.md",
            result.get("reason", ""),
        )
        self.assertNotIn("Use $sdlc-start.", result.get("reason", ""))
        self.assertNotIn("Project ID:", result.get("reason", ""))
        self.assertNotIn("Run ID:", result.get("reason", ""))
        self.assertIn("sdlc-validate-codes", result.get("reason", ""))

    def test_stop_normalizes_short_next_skill_alias(self) -> None:
        cases = {
            "validate-codes": "sdlc-validate-codes",
            "auto-steering": "sdlc-auto-steering",
            "update-documents": "sdlc-update-documents",
        }
        for short_name, canonical_name in cases.items():
            with self.subTest(short_name=short_name):
                self.active_run(next_skill=short_name)
                result = run_hook(STOP, self.stop_payload(), self.codex_home)
                reason = result.get("reason", "")
                self.assertEqual(result.get("decision"), "block")
                self.assertIn(
                    "Use $sdlc-start run 20260716T000000Z--test-feature.md", reason
                )
                self.assertIn(canonical_name, reason)
                self.assertNotIn(f"Next recommended skill: {short_name}", reason)

    def test_stop_allows_one_conditional_troubleshooting_route(self) -> None:
        run_dir = self.active_run(next_skill="troubleshoot")
        self.write_repair_state(
            run_dir,
            status="diagnosis_required",
            next_skill="troubleshoot",
        )
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertEqual(result.get("decision"), "block")
        self.assertIn("Next recommended skill: troubleshoot", result.get("reason", ""))

    def test_stop_rejects_diagnosis_required_route_that_bypasses_troubleshoot(
        self,
    ) -> None:
        run_dir = self.active_run(next_skill="sdlc-implement-plan")
        self.write_repair_state(
            run_dir,
            status="diagnosis_required",
            next_skill="sdlc-implement-plan",
        )
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("must route to troubleshoot", result["stopReason"])

    def test_stop_requires_every_diagnosis_to_return_through_classifier(self) -> None:
        run_dir = self.active_run(next_skill="sdlc-implement-plan")
        self.write_repair_state(
            run_dir,
            status="diagnosed",
            next_skill="sdlc-implement-plan",
            with_diagnosis=True,
        )
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("return through sdlc-classify-failure", result["stopReason"])

        self.write_repair_state(
            run_dir,
            status="diagnosed",
            next_skill="sdlc-classify-failure",
            with_diagnosis=True,
        )
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertEqual(result.get("decision"), "block")
        self.assertIn("sdlc-classify-failure", result.get("reason", ""))

    def test_stop_enforces_repair_budget_status(self) -> None:
        run_dir = self.active_run(next_skill="sdlc-classify-failure")
        self.write_repair_state(
            run_dir,
            status="exhausted",
            next_skill="sdlc-classify-failure",
        )
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("stopped with status exhausted", result["stopReason"])

    def test_stop_rejects_routed_owner_mismatch(self) -> None:
        run_dir = self.active_run(next_skill="sdlc-create-design")
        self.write_repair_state(
            run_dir,
            status="routed",
            next_skill="sdlc-create-design",
            classification_route="sdlc-implement-plan",
        )
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("authoritative owner", result["stopReason"])

        self.write_repair_state(
            run_dir,
            status="routed",
            next_skill="sdlc-implement-plan",
            classification_route="sdlc-implement-plan",
        )
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertEqual(result.get("decision"), "block")
        self.assertIn("sdlc-implement-plan", result.get("reason", ""))

    def test_stop_blocks_publication_until_invalidations_are_revalidated(
        self,
    ) -> None:
        run_dir = self.active_run(next_skill="create-pr")
        self.write_repair_state(
            run_dir,
            status="revalidation_required",
            next_skill="create-pr",
        )
        self.write_revalidation_cursor(run_dir, complete=False)
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("authoritative gate", result["stopReason"])

        state_path = run_dir / "current-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["next_recommended_skill"] = "sdlc-validate-codes"
        write_json(state_path, state)
        control_path = run_dir / "repairs" / "FEAT-001" / "repair-control.json"
        control = json.loads(control_path.read_text(encoding="utf-8"))
        control["revalidation"]["repair_dispatch_id"] = "foreign-dispatch"
        projection = {
            "schema": "agentic-sdlc/revalidation-cursor-v1",
            "classification_id": control["revalidation"]["classification_id"],
            "repair_dispatch_id": "foreign-dispatch",
            "required": control["revalidation"]["required"],
        }
        control["revalidation"]["cursor_id"] = hashlib.sha256(
            json.dumps(
                projection,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        write_json(control_path, control)
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("not repair-authorized", result["stopReason"])

        self.write_revalidation_cursor(run_dir, complete=False)
        control = json.loads(control_path.read_text(encoding="utf-8"))
        control["revalidation"]["required"] = []
        write_json(control_path, control)
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("cursor is inconsistent", result["stopReason"])

        self.write_revalidation_cursor(run_dir, complete=False)
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertEqual(result.get("decision"), "block")
        self.assertIn("sdlc-validate-codes", result.get("reason", ""))

        control = json.loads(control_path.read_text(encoding="utf-8"))
        control["status"] = "resolved"
        write_json(control_path, control)
        state["next_recommended_skill"] = "create-pr"
        write_json(state_path, state)
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("still has invalidated evidence", result["stopReason"])

        self.write_revalidation_cursor(run_dir, complete=True)
        self.assertFalse((run_dir / "worktrees" / "FEAT-001" / "integration").exists())
        self.assertEqual(git(self.project, "status", "--porcelain"), "")
        control = json.loads(control_path.read_text(encoding="utf-8"))
        control["status"] = "resolved"
        write_json(control_path, control)
        state["next_recommended_skill"] = "sdlc-uat-tests"
        write_json(state_path, state)
        git(self.project, "checkout", "--detach", "HEAD")
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("revalidation evidence is stale", result["stopReason"])
        git(self.project, "switch", "main")
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertEqual(result.get("decision"), "block")
        self.assertIn("sdlc-uat-tests", result.get("reason", ""))

    def test_stop_continues_for_pause_steering(self) -> None:
        run_dir = self.active_run(next_skill="sdlc-validate-codes")
        (run_dir / "STEERING.md").write_text(
            "Pause after the current feature. Do not create a PR.\n", encoding="utf-8"
        )
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertEqual(result.get("decision"), "block")
        self.assertIn(
            "Use $sdlc-start run 20260716T000000Z--test-feature.md",
            result.get("reason", ""),
        )
        self.assertIn("STEERING.md", result.get("reason", ""))
        self.assertIn("pause or PR-control", result.get("reason", ""))

    def test_stop_no_progress_guard(self) -> None:
        self.active_run(next_skill="sdlc-validate-codes")
        run_hook(STOP, self.stop_payload(active=True), self.codex_home)
        run_hook(STOP, self.stop_payload(active=True), self.codex_home)
        result = run_hook(STOP, self.stop_payload(active=True), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("No progress", result["stopReason"])

    def test_stop_fails_closed_for_unbound_active_run(self) -> None:
        run_dir = self.active_run(next_skill="sdlc-validate-codes")
        (run_dir / "prompt.json").unlink()
        write_json(
            run_dir / "run.json",
            {
                "status": "running",
                "prompt": {"filename": "20260716T000000Z--test-feature.md"},
            },
        )
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("WORKFLOW_UPGRADE_REQUIRED", result["stopReason"])

    def test_stop_fails_closed_for_prompt_binding_mismatch(self) -> None:
        run_dir = self.active_run(next_skill="sdlc-validate-codes")
        write_json(
            run_dir / "run.json",
            {
                "status": "running",
                "prompt": {"filename": "different-managed-prompt.md"},
            },
        )
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("WORKFLOW_UPGRADE_REQUIRED", result["stopReason"])

    def test_stop_uses_repaired_renamed_prompt_filename(self) -> None:
        run_dir = self.active_run(next_skill="sdlc-validate-codes")
        binding = json.loads((run_dir / "prompt.json").read_text(encoding="utf-8"))
        binding["prompt_filename"] = "renamed-feature-prompt.md"
        write_json(run_dir / "prompt.json", binding)
        run_state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        run_state["prompt"]["filename"] = "renamed-feature-prompt.md"
        run_state["prompt_filename"] = "renamed-feature-prompt.md"
        write_json(run_dir / "run.json", run_state)
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertEqual(result.get("decision"), "block")
        self.assertIn(
            "Use $sdlc-start run renamed-feature-prompt.md",
            result.get("reason", ""),
        )

    def test_stop_does_not_continue_merge_pr(self) -> None:
        self.active_run(next_skill="sdlc-merge-pr")
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("explicit user request", result["stopReason"])

    def test_stop_does_not_continue_worktree_integration(self) -> None:
        self.active_run(
            phase="outer-integration-pending",
            next_skill="sdlc-start",
        )
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("fresh explicit user invocation", result["stopReason"])
        self.assertIn("recorded primary checkout", result["stopReason"])

    def test_stop_does_not_continue_worktree_next_skill(self) -> None:
        self.active_run(next_skill="worktree")
        result = run_hook(STOP, self.stop_payload(), self.codex_home)
        self.assertFalse(result["continue"])
        self.assertIn("fresh explicit user invocation", result["stopReason"])
        self.assertIn("recorded primary checkout", result["stopReason"])


if __name__ == "__main__":
    unittest.main()
