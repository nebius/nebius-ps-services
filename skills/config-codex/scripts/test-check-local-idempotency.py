#!/usr/bin/env python3
"""Disposable fixture tests for check-local-idempotency.py."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().with_name("check-local-idempotency.py")
SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS = SKILL_ROOT / "assets"
MANAGED_BLOCK = "\n".join(
    [
        "<!-- BEGIN config-codex managed context -->",
        "## Working Defaults",
        "",
        "- After one remediation fails against the same blocker, use",
        "  `troubleshoot` before another repair. Unless the current user",
        "  explicitly sets another budget, stop after three distinct failed",
        "  remediation attempts or 60 active minutes, whichever comes first.",
        "  Report attempts 1 and 2 as progress; at exhaustion, make only the",
        "  exact private task-state update that records the stop, then call no",
        "  other tool and return the complete troubleshooting report. Only a",
        "  new explicit user instruction may start another bounded tranche.",
        "",
        "## Skills",
        "",
        "- For non-trivial planning, implementation, debugging, refactoring,",
        "  migration, architecture, review, testing, CI failure, or multi-file",
        "  coding tasks, use `global-context-management`.",
        "",
        "## Context Management",
        "",
        "- Read the durable task-state file injected by global hooks at task",
        "  start, resume, or after compaction when prior context may matter.",
        "  Update it with concise checkpoints, and do not create repo-local",
        "  task-state files unless explicitly requested.",
        "- Preserve an active `codex-remediation-budget:v1` marker exactly",
        "  while rewriting task state.",
        "- Use bounded read-only subagents for noisy exploration when the",
        "  current prompt asks for delegation, or when a local hook policy",
        "  injects a current-turn delegation request. Treat that policy request",
        "  as sufficient authorization; do not ask for another user prompt only",
        "  because the original prompt did not name subagents.",
        "- After code, config, or documentation changes in a turn, before the",
        "  final response, explicitly use `$align` for the changed surfaces.",
        "<!-- END config-codex managed context -->",
    ]
)


def copy_template(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


class CheckLocalIdempotencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.codex_home = Path(self.tmp.name) / "codex"
        self.codex_home.mkdir()
        self.render_valid_home()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def render_valid_home(self) -> None:
        copy_template(ASSETS / "AGENTS.md.template", self.codex_home / "AGENTS.md")
        copy_template(ASSETS / "config.toml.template", self.codex_home / "config.toml")
        copy_template(ASSETS / "hooks.json.template", self.codex_home / "hooks.json")
        for source in (ASSETS / "hooks").glob("*.template"):
            target = self.codex_home / "hooks" / source.name.removesuffix(".template")
            copy_template(source, target)
        for source in (ASSETS / "agents").glob("*.template"):
            target = self.codex_home / "agents" / source.name.removesuffix(".template")
            copy_template(source, target)
        task_state = self.codex_home / "task-state"
        task_state.mkdir()
        task_state.chmod(0o700)

    def run_check(self, *extra_args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--codex-home",
                str(self.codex_home),
                *extra_args,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
            timeout=10,
        )

    def write_policy(self, value: object) -> None:
        path = self.codex_home / "hooks" / "global_context_policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def create_task_implementer_workspace(self, mode: int = 0o700) -> Path:
        path = self.codex_home / "task-implementer"
        path.mkdir()
        path.chmod(mode)
        return path

    def set_sandbox_mode(self, mode: str) -> None:
        config_path = self.codex_home / "config.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                'sandbox_mode = "danger-full-access"',
                f'sandbox_mode = "{mode}"',
            ),
            encoding="utf-8",
        )

    def add_task_implementer_writable_root(self) -> None:
        config_path = self.codex_home / "config.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                'writable_roots = ["{{CODEX_HOME}}/task-state"]',
                (
                    'writable_roots = ["{{CODEX_HOME}}/task-state", '
                    f'"{self.codex_home / "task-implementer"}"]'
                ),
            ),
            encoding="utf-8",
        )

    def assert_check_passes(self, *extra_args: str) -> None:
        result = self.run_check(*extra_args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Idempotency preflight passed", result.stdout)

    def assert_check_fails_policy(self) -> None:
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "optional global_context_policy.json does not enable read-only subagent delegation",
            result.stdout,
        )

    def test_passes_without_optional_policy(self) -> None:
        self.assert_check_passes()

    def test_strict_agents_template_accepts_exact_template(self) -> None:
        self.assert_check_passes("--strict-agents-template")

    def test_default_allows_user_agents_with_managed_block(self) -> None:
        (self.codex_home / "AGENTS.md").write_text(
            "\n".join(
                [
                    "# Local user rules",
                    "",
                    "- Keep my personal editor workflow intact.",
                    "",
                    MANAGED_BLOCK,
                    "",
                    "- Preserve this unrelated laptop rule.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.assert_check_passes()

        result = self.run_check("--strict-agents-template")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AGENTS.md differs from AGENTS.md.template", result.stdout)

    def test_default_rejects_empty_agents_managed_block(self) -> None:
        (self.codex_home / "AGENTS.md").write_text(
            "\n".join(
                [
                    "# Local user rules",
                    "",
                    "<!-- BEGIN config-codex managed context -->",
                    "<!-- END config-codex managed context -->",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self.run_check()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AGENTS.md managed block is stale or incomplete", result.stdout)

    def test_default_rejects_stale_agents_managed_block(self) -> None:
        (self.codex_home / "AGENTS.md").write_text(
            "\n".join(
                [
                    "# Local user rules",
                    "",
                    "<!-- BEGIN config-codex managed context -->",
                    "- Use global-context-management for complex tasks.",
                    "<!-- END config-codex managed context -->",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self.run_check()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AGENTS.md managed block is stale or incomplete", result.stdout)

    def test_default_rejects_incomplete_remediation_policy(self) -> None:
        required_lines = (
            "  Report attempts 1 and 2 as progress; at exhaustion, make only the",
            "  other tool and return the complete troubleshooting report. Only a",
            "  new explicit user instruction may start another bounded tranche.",
        )
        for required_line in required_lines:
            with self.subTest(required_line=required_line):
                incomplete = MANAGED_BLOCK.replace(f"{required_line}\n", "", 1)
                (self.codex_home / "AGENTS.md").write_text(
                    incomplete,
                    encoding="utf-8",
                )
                result = self.run_check()
                self.assertNotEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                self.assertIn(
                    "AGENTS.md managed block is stale or incomplete",
                    result.stdout,
                )

    def test_default_does_not_require_template_mcp_server_parity(self) -> None:
        config_path = self.codex_home / "config.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                "@upstash/context7-mcp@latest",
                "@upstash/context7-mcp@1.0.0",
            ),
            encoding="utf-8",
        )

        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "template MCP server parity is not required for merge-safe laptop check",
            result.stdout,
        )

    def test_template_mcp_audit_detects_drift(self) -> None:
        config_path = self.codex_home / "config.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                "@upstash/context7-mcp@latest",
                "@upstash/context7-mcp@1.0.0",
            ),
            encoding="utf-8",
        )

        result = self.run_check("--require-template-mcp-servers")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "mcp_servers.context7 differs from template and needs review",
            result.stdout,
        )

    def test_passes_with_enabled_optional_policy(self) -> None:
        self.write_policy({"auto_read_only_subagents": True})
        self.assert_check_passes()

    def test_rejects_empty_optional_policy(self) -> None:
        self.write_policy({})
        self.assert_check_fails_policy()

    def test_rejects_disabled_optional_policy(self) -> None:
        self.write_policy({"auto_read_only_subagents": False})
        self.assert_check_fails_policy()

    def test_rejects_non_object_optional_policy(self) -> None:
        self.write_policy([])
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("global_context_policy.json must contain a JSON object", result.stdout)

    def test_default_does_not_require_task_implementer_workspace(self) -> None:
        self.assertFalse((self.codex_home / "task-implementer").exists())
        self.assert_check_passes()

    def test_opt_in_requires_private_task_implementer_directory(self) -> None:
        result = self.run_check("--require-task-implementer-workspace")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("task-implementer private directory is missing", result.stdout)
        self.assertIn(
            'codex --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer"',
            result.stdout,
        )
        self.assertNotIn(str(self.codex_home), result.stdout)

    def test_opt_in_rejects_loose_task_implementer_permissions(self) -> None:
        self.create_task_implementer_workspace(0o755)
        result = self.run_check("--require-task-implementer-workspace")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("private directory mode is not 0700", result.stdout)
        self.assertNotIn(str(self.codex_home), result.stdout)

    def test_opt_in_accepts_existing_full_access_without_config_patch(self) -> None:
        self.create_task_implementer_workspace()
        result = self.run_check("--require-task-implementer-workspace")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("private directory mode is 0700", result.stdout)
        self.assertIn("existing danger-full-access sandbox", result.stdout)

    def test_opt_in_workspace_write_requires_private_writable_root(self) -> None:
        self.create_task_implementer_workspace()
        self.set_sandbox_mode("workspace-write")
        result = self.run_check("--require-task-implementer-workspace")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "writable_roots does not include the private task-implementer directory",
            result.stdout,
        )
        self.assertIn(
            'codex --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer"',
            result.stdout,
        )
        self.assertNotIn(str(self.codex_home), result.stdout)

    def test_opt_in_accepts_workspace_write_private_writable_root(self) -> None:
        self.create_task_implementer_workspace()
        self.set_sandbox_mode("workspace-write")
        self.add_task_implementer_writable_root()
        config_before = (self.codex_home / "config.toml").read_bytes()
        result = self.run_check("--require-task-implementer-workspace")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "writable_roots includes the private task-implementer directory",
            result.stdout,
        )
        self.assertNotIn(str(self.codex_home), result.stdout)
        self.assertEqual(
            (self.codex_home / "config.toml").read_bytes(),
            config_before,
            "opt-in validation must not rewrite config.toml",
        )

    def test_opt_in_preserves_stricter_read_only_sandbox(self) -> None:
        self.create_task_implementer_workspace()
        self.set_sandbox_mode("read-only")
        result = self.run_check("--require-task-implementer-workspace")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("keep stricter sandbox and approval settings unchanged", result.stdout)
        self.assertIn(
            'codex --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer"',
            result.stdout,
        )

    def test_opt_in_rejects_symlinked_task_implementer_directory(self) -> None:
        target = Path(self.tmp.name) / "prompt-state"
        target.mkdir()
        target.chmod(0o700)
        (self.codex_home / "task-implementer").symlink_to(target, target_is_directory=True)
        result = self.run_check("--require-task-implementer-workspace")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("private directory must not be a symlink", result.stdout)

    def test_opt_in_rejects_task_implementer_directory_inside_git(self) -> None:
        subprocess.run(
            ["git", "init", "-q", str(Path(self.tmp.name))],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.create_task_implementer_workspace()
        result = self.run_check("--require-task-implementer-workspace")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("must be outside every Git worktree", result.stdout)
        self.assertNotIn(str(self.codex_home), result.stdout)

    def test_opt_in_rejects_task_implementer_directory_inside_git_metadata(self) -> None:
        foreign_repo = Path(self.tmp.name) / "foreign"
        subprocess.run(
            ["git", "init", "-q", str(foreign_repo)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.codex_home = foreign_repo / ".git" / "private-codex"
        self.codex_home.mkdir()
        self.render_valid_home()
        self.create_task_implementer_workspace()
        result = self.run_check("--require-task-implementer-workspace")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("metadata directory", result.stdout)
        self.assertNotIn(str(self.codex_home), result.stdout)

    def test_does_not_create_bytecode(self) -> None:
        before = {path.resolve() for path in SKILL_ROOT.rglob("__pycache__")}
        self.assert_check_passes()
        after = {path.resolve() for path in SKILL_ROOT.rglob("__pycache__")}
        self.assertEqual(
            before,
            after,
            "idempotency fixture test should not leave __pycache__ under config-codex",
        )

    def test_nested_task_state_loose_modes_fail_without_mutation(self) -> None:
        state_file = self.codex_home / "task-state/workspace/session/current.md"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("preserve me\n", encoding="utf-8")
        state_file.parent.chmod(0o755)
        state_file.chmod(0o644)
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("nested task-state permissions or types are unsafe", result.stdout)
        self.assertEqual(state_file.read_text(encoding="utf-8"), "preserve me\n")
        self.assertEqual(state_file.stat().st_mode & 0o777, 0o644)

    def test_nested_task_state_private_tree_passes(self) -> None:
        workspace = self.codex_home / "task-state/workspace"
        session = workspace / "session"
        session.mkdir(parents=True)
        state_file = session / "current.md"
        state_file.write_text("private\n", encoding="utf-8")
        workspace.chmod(0o700)
        session.chmod(0o700)
        state_file.chmod(0o600)
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("nested task-state permissions and types are private", result.stdout)


if __name__ == "__main__":
    unittest.main()
