#!/usr/bin/env python3
"""Claude native candidate discovery and hook-boundary regressions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

_support_bootstrap = Path(__file__).resolve().parents[1] / "assets/trusted_runtime_bootstrap.py.template"
exec(compile(_support_bootstrap.read_bytes(), str(_support_bootstrap), "exec"))
_load_skill_support("projector", __file__, 'global-context-management/scripts/test-claude-roles.py', source_only=True)  # noqa: F821 — verified bootstrap precedes runtime imports

from agent_runtime import CLAUDE_READ_ONLY_ROLES, claude_read_only_roles, claude_role_metadata  # noqa: E402 — verified bootstrap precedes runtime imports
from hook_runtime import payload_files  # noqa: E402 — verified bootstrap precedes runtime imports


ROOT = Path(__file__).resolve().parents[2]


class NativeRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="claude-role-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "claude"
        self.project = self.base / "project"
        self.project.mkdir()
        (self.home / "agents").mkdir(parents=True)
        for name in CLAUDE_READ_ONLY_ROLES:
            target = self.home / f"agents/{name}.md"
            target.write_bytes((ROOT / f"config-claude/assets/agents/{name}.md.template").read_bytes())

    def names(self):
        return {role["name"] for role in claude_read_only_roles(self.home, self.project)}

    def test_three_native_roles_are_candidates(self):
        self.assertEqual(self.names(), set(CLAUDE_READ_ONLY_ROLES))

    def test_nested_project_shadow_and_nested_user_duplicate_are_not_candidates(self):
        project_override = self.project / ".claude/agents/nested/override.md"
        project_override.parent.mkdir(parents=True)
        project_override.write_text("---\nname: repo-mapper\ntools: Bash, Write\n---\nCustom writable role.\n")
        self.assertNotIn("repo-mapper", self.names())
        project_override.unlink()
        duplicate = self.home / "agents/nested/duplicate.md"
        duplicate.parent.mkdir()
        duplicate.write_bytes((self.home / "agents/repo-mapper.md").read_bytes())
        self.assertNotIn("repo-mapper", self.names())

    def test_unrestricted_tools_and_side_effect_fields_are_not_candidates(self):
        target = self.home / "agents/repo-mapper.md"
        original = target.read_text()
        variants = [original.replace("tools: Read, Grep, Glob\n", ""),
                    original.replace("Read, Grep, Glob", "Read, Grep, Glob, Bash"),
                    original.replace("Read, Grep, Glob", "Read, Grep, Glob, Agent")]
        for field in ("hooks", "mcpServers", "memory", "skills", "permissionMode"):
            variants.append(original.replace("model: inherit", f"model: inherit\n{field}: value"))
        for value in variants:
            with self.subTest(length=len(value)):
                target.write_text(value)
                self.assertNotIn("repo-mapper", self.names())

    def test_duplicate_keys_unsafe_modes_and_special_files(self):
        target = self.home / "agents/repo-mapper.md"
        original = target.read_text()
        target.write_text(original.replace("model: inherit", "model: inherit\ntools: Bash"))
        self.assertEqual(claude_role_metadata(target), {})
        target.write_text(original)
        target.chmod(0o666)
        self.assertEqual(claude_role_metadata(target), {})
        target.unlink()
        os.mkfifo(target)
        self.assertEqual(claude_role_metadata(target), {})

    def test_project_shadow_by_declared_name_suppresses_user_candidate(self):
        local = self.project / ".claude/agents/unrelated-filename.md"
        local.parent.mkdir(parents=True)
        local.write_bytes((self.home / "agents/repo-mapper.md").read_bytes())
        self.assertEqual(self.names(), {"test-strategist", "risk-reviewer"})
        local.write_text(local.read_text().replace("Read, Grep, Glob", "Bash"))
        self.assertEqual(self.names(), {"test-strategist", "risk-reviewer"})

    def test_symlinked_or_uninspectable_project_definition_is_conservative(self):
        local = self.project / ".claude/agents"
        local.parent.mkdir()
        local.symlink_to(self.home / "agents", target_is_directory=True)
        self.assertEqual(self.names(), set())

    def test_user_symlink_cannot_supply_role(self):
        target = self.home / "agents/repo-mapper.md"
        outside = self.base / "role.md"
        target.rename(outside)
        target.symlink_to(outside)
        self.assertNotIn("repo-mapper", self.names())

    def test_native_hook_requires_policy_and_separates_private_state_effects(self):
        hooks = self.home / "hooks"
        for name, data in payload_files(ROOT).items():
            target = hooks / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        policy = hooks / "global_context_policy.json"
        payload = {"cwd": str(self.project), "session_id": "claude-role-fixture",
                   "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
                   "hook_event_name": "UserPromptSubmit", "prompt": "Plan a multi-file implementation and review its tests."}
        env = {**os.environ, "HOME": str(self.base / "home"), "CLAUDE_CONFIG_DIR": str(self.home),
               "CODEX_HOME": str(self.base / "codex-sentinel"), "SKILLS_AGENT": "claude",
               "PYTHONDONTWRITEBYTECODE": "1", "CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS": "1"}
        env.pop("CODEX_THREAD_ID", None)
        for enabled in (False, True):
            policy.write_text(json.dumps({"auto_read_only_subagents": enabled}))
            before = {str(p.relative_to(self.project)): p.read_bytes() for p in self.project.rglob("*") if p.is_file()}
            result = subprocess.run([sys.executable, "-B", str(hooks / "hook_runtime.py"), "--agent", "claude",
                                     "--hook", "user_prompt_context.py"], input=json.dumps(payload),
                                    text=True, capture_output=True, env=env, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            if enabled:
                for name in CLAUDE_READ_ONLY_ROLES:
                    self.assertIn(name, result.stdout)
                self.assertIn("CLI and managed roles", result.stdout)
                self.assertIn("Inherited hooks may maintain private task state", result.stdout)
            else:
                self.assertNotIn("Local policy asks", result.stdout)
            self.assertEqual(before, {str(p.relative_to(self.project)): p.read_bytes() for p in self.project.rglob("*") if p.is_file()})
            self.assertFalse((self.base / "codex-sentinel").exists())
        states = list((self.home / "task-state").rglob("current.md"))
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].read_bytes(), b"")
        self.assertEqual(states[0].stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
