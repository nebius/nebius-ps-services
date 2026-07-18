#!/usr/bin/env python3
"""Unit tests for local Agentic SDLC Codex hooks."""

from __future__ import annotations

import json
import os
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
                "schema": "agentic-sdlc/prompt-binding-v1",
                "run_id": "run-1",
                "prompt_id": "prompt-" + "1" * 32,
                "prompt_filename": prompt_filename,
                "revisions": [
                    {
                        "revision": "r0001",
                        "sha256": "a" * 64,
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

    def authorize(self, run_dir: Path, name: str) -> None:
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        write_json(
            run_dir / "permissions" / name,
            {
                "allowed": True,
                "branch": "agent/test",
                "phase": name.removesuffix("-authorization.json"),
                "expires_at": expires.isoformat().replace("+00:00", "Z"),
            },
        )

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
                "schema": "agentic-sdlc/execution-coordinator-v4",
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
                "schema": "agentic-sdlc/worker-assignment-v2",
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

    def test_pretool_allows_git_status(self) -> None:
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", "git status --short"), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_project_patch(self) -> None:
        patch = (
            "*** Begin Patch\n*** Add File: src/new.py\n+print('ok')\n*** End Patch\n"
        )
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_patch_containing_dockerfile_ownership_text(self) -> None:
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
        outside_path = self.root / "outside-project" / "note.md"
        patch = f"*** Begin Patch\n*** Add File: {outside_path}\n+external note\n*** End Patch\n"
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_outside_project_delete_patch(self) -> None:
        outside_path = self.root / "outside-project" / "old-note.md"
        patch = f"*** Begin Patch\n*** Delete File: {outside_path}\n*** End Patch\n"
        result = run_hook(
            PRE_TOOL, self.pre_payload("apply_patch", patch), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_outside_project_bash_write(self) -> None:
        outside_path = self.root / "outside-project" / "note.md"
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", f"tee {outside_path}"), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_outside_project_bash_delete(self) -> None:
        outside_path = self.root / "outside-project" / "old-note.md"
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", f"rm {outside_path}"), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_mcp_outside_project_write(self) -> None:
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
        codex_home = Path("/codex-hook-fixture")
        agents_path = codex_home / "AGENTS.md"
        patch = f"*** Begin Patch\n*** Add File: {agents_path}\n+# Global AGENTS.md\n*** End Patch\n"
        result = run_hook(PRE_TOOL, self.pre_payload("apply_patch", patch), codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_global_agents_delete_patch(self) -> None:
        codex_home = Path("/codex-hook-fixture")
        agents_path = codex_home / "AGENTS.md"
        patch = f"*** Begin Patch\n*** Delete File: {agents_path}\n*** End Patch\n"
        result = run_hook(PRE_TOOL, self.pre_payload("apply_patch", patch), codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_move_from_global_agents(self) -> None:
        codex_home = Path("/codex-hook-fixture")
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
        codex_home = Path("/codex-hook-fixture")
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
        codex_home = Path("/codex-hook-fixture")
        hook_path = codex_home / "hooks" / "user_prompt_context.py"
        patch = f"*** Begin Patch\n*** Add File: {hook_path}\n+print('blocked')\n*** End Patch\n"
        result = run_hook(PRE_TOOL, self.pre_payload("apply_patch", patch), codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_global_config_apply_patch(self) -> None:
        codex_home = Path("/codex-hook-fixture")
        config_path = codex_home / "config.toml"
        patch = f"*** Begin Patch\n*** Add File: {config_path}\n+[features]\n*** End Patch\n"
        result = run_hook(PRE_TOOL, self.pre_payload("apply_patch", patch), codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_bash_global_agents_write(self) -> None:
        codex_home = Path("/codex-hook-fixture")
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", f"tee {codex_home / 'AGENTS.md'}"),
            codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_allows_non_obvious_bash_global_agents_write(self) -> None:
        codex_home = Path("/codex-hook-fixture")
        command = f"python3 -c \"open('{codex_home / 'AGENTS.md'}','w').write('x')\""
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_bash_global_agents_read_only_inspection(self) -> None:
        codex_home = Path("/codex-hook-fixture")
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", f"cat {codex_home / 'AGENTS.md'}"),
            codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_allows_mcp_global_agents_write(self) -> None:
        codex_home = Path("/codex-hook-fixture")
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
        command = "cp -R ~/." + "codex/sdlc-runs/demo ."
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_private_state_stage_command(self) -> None:
        command = "git " + "add ~/." + "codex/sdlc-runs/demo"
        result = run_hook(PRE_TOOL, self.pre_payload("Bash", command), self.codex_home)
        self.assertEqual(result, {})

    def test_pretool_allows_external_network_curl(self) -> None:
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "curl -I https://example.com"),
            self.codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_allows_external_network_ssh(self) -> None:
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", "ssh example.com true"), self.codex_home
        )
        self.assertEqual(result, {})

    def test_pretool_allows_external_network_scp(self) -> None:
        result = run_hook(
            PRE_TOOL,
            self.pre_payload("Bash", "scp local.txt example.com:/tmp/local.txt"),
            self.codex_home,
        )
        self.assertEqual(result, {})

    def test_pretool_denies_commit_on_main(self) -> None:
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
        result = run_hook(
            PRE_TOOL, self.pre_payload("Bash", "rm -rf /"), self.codex_home
        )
        self.assert_denied(result, "recursive removal")

    def test_pretool_denies_recursive_ownership_shell_command(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
