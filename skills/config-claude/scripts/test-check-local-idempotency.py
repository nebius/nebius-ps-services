#!/usr/bin/env python3
"""Isolated configuration, recovery and installation contract tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import claude_config as config


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("claude_recovery", Path(__file__).with_name("create-recovery-config.py"))
assert SPEC and SPEC.loader
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


def put(path: Path, value: bytes | str | dict) -> None:
    for directory in reversed(path.parents):
        directory.mkdir(exist_ok=True, mode=0o700)
    data = value if isinstance(value, bytes) else (value if isinstance(value, str) else json.dumps(value)).encode()
    path.write_bytes(data)
    path.chmod(0o600)


def native_fixture(home: Path) -> None:
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    put(home / "settings.json", {"model": "existing-model", "permissions": {"deny": ["Bash(rm *)"]}})
    put(home / "CLAUDE.md", "User text stays first.\n\n" + (config.SKILL / "assets/CLAUDE.md.template").read_text())
    for name in config.ROLES:
        put(home / f"agents/{name}.md", (config.SKILL / f"assets/agents/{name}.md.template").read_bytes())
    (home / "task-state").mkdir(mode=0o700)


def snapshot(root: Path) -> dict:
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns, stat.S_IMODE(path.stat().st_mode))
            for path in root.rglob("*") if path.is_file()}


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="config-claude-check-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "claude"
        native_fixture(self.home)

    def inspect(self, **options):
        return config.inspect(self.home, ROOT, native_only=True, **options)

    def test_native_preflight_preserves_custom_settings_and_repeated_reads(self):
        put(self.home / "backups/existing.backup", "preserve")
        before = snapshot(self.home)
        first, second = self.inspect(), self.inspect()
        self.assertEqual(first["status"], "STATIC_PASS")
        self.assertEqual(first, second)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(first["runtime"], "NOT_RUN")

    def test_missing_home_is_reported_without_creation(self):
        missing = self.base / "missing"
        self.assertEqual(config.inspect(missing, ROOT)["status"], "NOT_ALIGNED")
        self.assertFalse(missing.exists())

    def test_malformed_duplicate_and_nonobject_json_are_not_rewritten(self):
        for value in ('{"secret":"opaque-fixture",', '{"a":1,"a":2}', '[]', '{"a":NaN}'):
            with self.subTest(value=value):
                put(self.home / "settings.json", value)
                before = snapshot(self.home)
                result = self.inspect()
                self.assertEqual(result["status"], "NOT_ALIGNED")
                self.assertNotIn("opaque-fixture", json.dumps(result))
                self.assertEqual(snapshot(self.home), before)

    def test_stale_empty_duplicate_and_conflicting_instruction_blocks(self):
        original = (self.home / "CLAUDE.md").read_text()
        for value in (
            original.replace("Preserve unrelated changes", "Old guidance"),
            config.BEGIN + config.END,
            original + "\n" + config.BEGIN,
            original + "\n## Live Product Validation\nOverride policy\n",
            original + "\nLive Product Validation\n-----------------------\nOverride policy\n",
        ):
            with self.subTest(length=len(value)):
                put(self.home / "CLAUDE.md", value)
                self.assertEqual(self.inspect()["status"], "NOT_ALIGNED")
        put(self.home / "CLAUDE.md", original + "\n```markdown\n## Live Product Validation\n```\n")
        self.assertEqual(self.inspect()["status"], "STATIC_PASS")

    def test_writable_and_behavior_expanding_role_definitions_fail(self):
        target = self.home / "agents/repo-mapper.md"
        original = target.read_text()
        for value in (original.replace("Read, Grep, Glob", "Read, Bash"),
                      original.replace("model: inherit", "model: inherit\nhooks: {}"),
                      original.replace("tools: Read, Grep, Glob\n", "")):
            put(target, value)
            self.assertEqual(self.inspect()["status"], "NOT_ALIGNED")

    def test_role_frontmatter_requires_exact_delimiter_lines(self):
        target = self.home / "agents/repo-mapper.md"
        original = target.read_text()
        for terminator in ("---invalid", "----"):
            with self.subTest(terminator=terminator):
                put(target, original.replace("\n---\n", f"\n{terminator}\n", 1))
                before = snapshot(self.home)
                self.assertEqual(self.inspect()["status"], "NOT_ALIGNED")
                self.assertEqual(snapshot(self.home), before)
        put(target, original + "\n---\nAdditional role guidance.\n")
        self.assertEqual(self.inspect()["status"], "STATIC_PASS")

    def test_unsafe_paths_do_not_become_aligned(self):
        target = self.home / "settings.json"
        outside = self.base / "outside.json"
        target.rename(outside)
        target.symlink_to(outside)
        self.assertEqual(self.inspect()["status"], "NOT_ALIGNED")
        target.unlink()
        os.link(outside, target)
        self.assertEqual(self.inspect()["status"], "NOT_ALIGNED")

    def test_read_rejects_concurrent_content_or_identity_change(self):
        target = self.home / "settings.json"
        original_read = os.read
        changed = False

        def intervening_read(fd, count):
            nonlocal changed
            result = original_read(fd, count)
            if not changed:
                changed = True
                put(target, {"unrelated": "newer user edit"})
            return result

        with patch.object(config.os, "read", side_effect=intervening_read):
            with self.assertRaisesRegex(ValueError, "changed"):
                config.safe_read(target)
        self.assertEqual(json.loads(target.read_text())["unrelated"], "newer user edit")

    def test_optional_permission_profile_is_not_a_default_requirement(self):
        self.assertEqual(self.inspect()["status"], "STATIC_PASS")
        self.assertEqual(self.inspect(trusted=True)["status"], "NOT_ALIGNED")
        for permissions in ([], None, {"defaultMode": "bypassPermissions", "disableBypassPermissionsMode": "disable"}):
            put(self.home / "settings.json", {"permissions": permissions, "sandbox": {"enabled": False}})
            self.assertEqual(self.inspect(trusted=True)["status"], "NOT_ALIGNED")
        put(self.home / "settings.json", recovery.render("trusted-local"))
        self.assertEqual(self.inspect(trusted=True)["status"], "STATIC_PASS")

    def test_selected_private_state_and_delegation_checks(self):
        self.assertEqual(self.inspect(delegation=True, workspace=True)["status"], "NOT_ALIGNED")
        put(self.home / "hooks/global_context_policy.json", {"auto_read_only_subagents": True})
        (self.home / "task-implementer").mkdir(mode=0o700)
        self.assertEqual(self.inspect(delegation=True, workspace=True)["status"], "STATIC_PASS")
        (self.base / ".git").mkdir()
        self.assertEqual(self.inspect(workspace=True)["status"], "NOT_ALIGNED")

    def test_nested_state_modes_and_links(self):
        state = self.home / "task-state/workspace/session/current.md"
        put(state, "private fixture")
        self.assertEqual(self.inspect()["status"], "STATIC_PASS")
        state.chmod(0o644)
        self.assertEqual(self.inspect()["status"], "NOT_ALIGNED")
        state.unlink()
        state.symlink_to(self.home / "CLAUDE.md")
        self.assertEqual(self.inspect()["status"], "NOT_ALIGNED")

    def test_private_state_roots_must_be_directories(self):
        for name in ("task-state", "task-implementer"):
            with self.subTest(root=name):
                home = self.base / name
                native_fixture(home)
                target = home / name
                if target.exists():
                    target.rmdir()
                put(target, "regular file cannot contain session directories")
                before = snapshot(home)
                result = config.inspect(home, ROOT, native_only=True, workspace=name == "task-implementer")
                self.assertEqual(result["status"], "NOT_ALIGNED")
                self.assertEqual(snapshot(home), before)

    def test_selected_mcp_checks_are_shape_only_and_redacted(self):
        target = self.base / "native-app.json"
        put(target, {"mcpServers": {"github": {"type": "http", "url": "https://example.com/mcp",
                                            "headers": {"Authorization": "opaque-fixture"}}},
                     "oauth": "opaque-fixture"})
        before = target.read_bytes()
        result = self.inspect(mcp_names=("github",), mcp_config=target)
        self.assertEqual(result["status"], "STATIC_PASS")
        self.assertNotIn("opaque-fixture", json.dumps(result))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(self.inspect(mcp_names=("github",))["status"], "NOT_ALIGNED")

    def test_explicit_native_target_wins_over_codex_invoking_host(self):
        with patch.dict(os.environ, {"CODEX_HOME": str(self.base / "codex"), "CLAUDE_CONFIG_DIR": str(self.home)}):
            self.assertEqual(config.native_home(), self.home)
            self.assertEqual(config.native_home(self.base / "explicit"), self.base / "explicit")
        with self.assertRaises(ValueError):
            config.native_home(Path("relative"))

    def test_full_check_requires_complete_hook_dependencies_before_success(self):
        result = config.inspect(self.home, self.base / "standalone")
        self.assertEqual(result["status"], "NOT_ALIGNED")
        self.assertIn("missing reviewed hook dependency", json.dumps(result))
        self.assertEqual(self.inspect()["scope"], "native-only")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="config-claude-recovery-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()

    def test_create_and_repeated_attempt_preserve_existing_bytes_and_mtime(self):
        self.assertFalse(recovery.create_private_file(self.home, recovery.render("minimal")))
        before = snapshot(self.home)
        with self.assertRaises(FileExistsError):
            recovery.create_private_file(self.home, recovery.render("trusted-local"))
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(stat.S_IMODE((self.home / "settings.json").stat().st_mode), 0o600)

    def test_concurrent_creators_publish_exactly_once(self):
        def create(_index):
            try:
                recovery.create_private_file(self.home, recovery.render("minimal"))
                return True
            except FileExistsError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(create, range(4))), 1)
        self.assertEqual(list(self.home.iterdir()), [self.home / "settings.json"])

    def test_existing_symlink_and_nonempty_target_survive(self):
        outside = self.home / "outside"
        put(outside, "untouched")
        (self.home / "settings.json").symlink_to(outside)
        with self.assertRaises(FileExistsError):
            recovery.create_private_file(self.home, recovery.render("minimal"))
        self.assertEqual(outside.read_text(), "untouched")

    def test_post_publication_durability_warning_keeps_completed_file(self):
        original_fsync = os.fsync
        def fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("simulated directory durability failure")
            return original_fsync(fd)
        with patch.object(recovery.os, "fsync", side_effect=fsync):
            self.assertTrue(recovery.create_private_file(self.home, recovery.render("minimal")))
        self.assertEqual((self.home / "settings.json").read_text(), "{}\n")

    def test_recovery_assets_cannot_silently_add_settings(self):
        with patch.object(recovery, "safe_read", return_value=b'{"env":{"PRIVATE":"opaque-fixture"}}'):
            with self.assertRaises(ValueError):
                recovery.render("minimal")

    def test_help_has_no_target_effects_for_both_helpers(self):
        before = snapshot(self.home)
        for name in ("create-recovery-config.py", "check-local-idempotency.py"):
            result = subprocess.run([sys.executable, "-B", str(config.SKILL / "scripts" / name), "--help"],
                                    env={**os.environ, "CLAUDE_CONFIG_DIR": str(self.home)},
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0)
        self.assertEqual(snapshot(self.home), before)


class HookOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="config-claude-hooks-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "claude"
        native_fixture(self.home)
        self.payloads, self.hooks = config.source_bundle(ROOT)

    def local_fixture(self):
        settings = json.loads((self.home / "settings.json").read_text())
        settings["hooks"] = config.projected(self.hooks, self.home)
        for name, data in self.payloads.items():
            put(self.home / "hooks" / name, data)
        put(self.home / "settings.json", settings)
        return settings

    def test_local_parity_and_unrelated_hooks_are_preserved(self):
        settings = self.local_fixture()
        settings["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "true"}]})
        put(self.home / "settings.json", settings)
        before = snapshot(self.home)
        self.assertEqual(config.inspect(self.home, ROOT)["status"], "STATIC_PASS")
        self.assertEqual(snapshot(self.home), before)

    def test_duplicate_custom_and_drifted_registrations_fail(self):
        for variant in ("duplicate", "custom", "payload"):
            with self.subTest(variant=variant):
                settings = self.local_fixture()
                stop = settings["hooks"]["Stop"][0]
                if variant == "duplicate":
                    settings["hooks"]["Stop"].append(stop)
                elif variant == "custom":
                    stop["hooks"][0]["command"] += " --custom"
                else:
                    put(self.home / "hooks/stop_lifecycle_arbiter.py", "drift")
                put(self.home / "settings.json", settings)
                self.assertEqual(config.inspect(self.home, ROOT)["status"], "NOT_ALIGNED")

    def test_plugin_ownership_and_local_conflict(self):
        settings = {"enabledPlugins": {config.PLUGIN: True}, "theme": "preserve"}
        put(self.home / "settings.json", settings)
        self.assertEqual(config.inspect(self.home, ROOT, route="plugin")["status"], "STATIC_PASS")
        self.assertEqual(config.inspect(self.home, ROOT, route="local")["status"], "NOT_ALIGNED")
        settings["hooks"] = config.projected(self.hooks, self.home)
        put(self.home / "settings.json", settings)
        self.assertEqual(config.inspect(self.home, ROOT, route="plugin")["status"], "NOT_ALIGNED")

    def test_source_bundle_is_never_imported(self):
        source = self.home.parent / "source"
        for owner in config.OWNERS:
            shutil.copytree(ROOT / owner / "assets", source / owner / "assets")
        target = source / "global-context-management/scripts"
        target.mkdir(parents=True)
        for name in ("agent_runtime.py", "hook_runtime.py", "trusted_runtime.py", "task_state_permissions.py"):
            put(target / name, "raise RuntimeError('must never execute selected source')\n")
        payloads, _hooks = config.source_bundle(source)
        self.assertIn(b"must never execute", payloads["agent_runtime.py"])


if __name__ == "__main__":
    unittest.main()
