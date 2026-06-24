#!/usr/bin/env python3
"""Fixture tests for installer-managed agent-nebius-auth hook migration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


HOOK_SOURCE = "agent-nebius-auth/assets/hooks"
PROJECT = "project-test"


def find_repo_root() -> Path | None:
    override = os.environ.get("AGENT_NEBIUS_AUTH_REPO_ROOT")
    if override:
        return Path(override).expanduser()

    for parent in Path(__file__).resolve().parents:
        if (parent / "install-skills.sh").is_file():
            return parent
    return None


class AgentNebiusAuthHookInstallMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = find_repo_root()
        if self.repo_root is None:
            self.skipTest("install-skills.sh is not available next to this installed skill copy")
        self.installer = self.repo_root / "install-skills.sh"
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.codex_home = self.root / "codex"
        self.home.mkdir()
        self.codex_home.mkdir()
        (self.home / ".nebius").mkdir()
        self.env = os.environ.copy()
        self.env.update(
            {
                "CODEX_HOME": str(self.codex_home),
                "HOME": str(self.home),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        self.env.pop("CODEX_NEBIUS_PROJECT_ID", None)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def config_path(self) -> Path:
        return self.codex_home / "config.toml"

    def selector_path(self) -> Path:
        return self.home / ".nebius" / "codex-agent-default-project-id"

    def installed_hook_path(self) -> Path:
        return self.codex_home / "hooks" / "pre_tool_use_nebius_auth.py"

    def hook_registrations(self) -> list[dict[str, str]]:
        hooks = json.loads((self.codex_home / "hooks.json").read_text(encoding="utf-8"))
        return [
            hook
            for group in hooks["hooks"]["PreToolUse"]
            for hook in group["hooks"]
            if "pre_tool_use_nebius_auth.py" in hook["command"]
        ]

    def run_installer(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(self.installer),
                "--install-hooks",
                HOOK_SOURCE,
                "--register-hooks",
            ],
            cwd=str(self.repo_root),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_all_hooks_installer(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(self.installer),
                "--install-all-hooks",
                "--register-hooks",
            ],
            cwd=str(self.repo_root),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def write_legacy_config_block(self) -> None:
        self.config_path().write_text(
            textwrap.dedent(
                f"""\
                model = "gpt-test"

                # agent-nebius-auth managed block begin
                [[hooks.PreToolUse]]
                matcher = "^Bash$"

                [[hooks.PreToolUse.hooks]]
                type = "command"
                command = 'CODEX_NEBIUS_PROJECT_ID={PROJECT} python3 /tmp/pre_tool_use_nebius_auth.py'
                timeout = 30
                statusMessage = "Preparing Nebius auth"
                # agent-nebius-auth managed block end
                """
            ),
            encoding="utf-8",
        )

    def test_register_hooks_removes_legacy_inline_config_block(self) -> None:
        self.write_legacy_config_block()

        result = self.run_installer()

        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        config_text = self.config_path().read_text(encoding="utf-8")
        self.assertIn('model = "gpt-test"', config_text)
        self.assertNotIn("agent-nebius-auth managed block", config_text)
        self.assertNotIn("pre_tool_use_nebius_auth.py", config_text)
        self.assertEqual(
            self.selector_path().read_text(encoding="utf-8").strip(),
            PROJECT,
        )
        self.assertEqual(oct(self.selector_path().stat().st_mode & 0o777), "0o600")
        backups = list(self.codex_home.glob("config.toml.bak.agent-nebius-auth.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(self.hook_registrations()), 1)
        self.assertIn(
            "Removed legacy agent-nebius-auth inline config hook block(s): 1",
            result.stdout,
        )
        self.assertNotIn(PROJECT, result.stdout)
        self.assertNotIn(PROJECT, result.stderr)

    def test_install_all_hooks_registers_and_migrates_legacy_inline_config(self) -> None:
        self.write_legacy_config_block()

        result = self.run_all_hooks_installer()

        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        config_text = self.config_path().read_text(encoding="utf-8")
        self.assertIn('model = "gpt-test"', config_text)
        self.assertNotIn("agent-nebius-auth managed block", config_text)
        self.assertNotIn("pre_tool_use_nebius_auth.py", config_text)
        self.assertEqual(
            self.selector_path().read_text(encoding="utf-8").strip(),
            PROJECT,
        )
        self.assertEqual(oct(self.selector_path().stat().st_mode & 0o777), "0o600")
        self.assertEqual(len(self.hook_registrations()), 1)
        self.assertIn(
            "Removed legacy agent-nebius-auth inline config hook block(s): 1",
            result.stdout,
        )
        self.assertNotIn(PROJECT, result.stdout)
        self.assertNotIn(PROJECT, result.stderr)

    def test_selector_conflict_fails_before_registering_hooks(self) -> None:
        self.write_legacy_config_block()
        self.selector_path().write_text("project-other\n", encoding="utf-8")
        self.selector_path().chmod(0o600)

        result = self.run_installer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already selects a different project", result.stderr)
        self.assertNotIn(PROJECT, result.stdout)
        self.assertNotIn(PROJECT, result.stderr)
        self.assertIn(
            "agent-nebius-auth managed block",
            self.config_path().read_text(encoding="utf-8"),
        )
        self.assertEqual(
            self.selector_path().read_text(encoding="utf-8").strip(),
            "project-other",
        )
        self.assertFalse((self.codex_home / "hooks.json").exists())
        self.assertFalse(self.installed_hook_path().exists())
        self.assertEqual(
            list(self.codex_home.glob("config.toml.bak.agent-nebius-auth.*")),
            [],
        )

    def test_repeated_register_hooks_is_idempotent_and_hook_runs(self) -> None:
        self.write_legacy_config_block()

        first = self.run_installer()
        second = self.run_installer()

        for result in (first, second):
            self.assertEqual(
                result.returncode,
                0,
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            self.assertNotIn(PROJECT, result.stdout)
            self.assertNotIn(PROJECT, result.stderr)

        config_text = self.config_path().read_text(encoding="utf-8")
        self.assertIn('model = "gpt-test"', config_text)
        self.assertNotIn("agent-nebius-auth managed block", config_text)
        self.assertNotIn("pre_tool_use_nebius_auth.py", config_text)
        self.assertEqual(
            self.selector_path().read_text(encoding="utf-8").strip(),
            PROJECT,
        )
        self.assertEqual(oct(self.selector_path().stat().st_mode & 0o777), "0o600")
        self.assertEqual(
            len(list(self.codex_home.glob("config.toml.bak.agent-nebius-auth.*"))),
            1,
        )
        self.assertEqual(len(self.hook_registrations()), 1)
        self.assertTrue(self.installed_hook_path().is_file())
        self.assertIn("Registered hook entries added: 0", second.stdout)
        self.assertNotIn("Removed legacy agent-nebius-auth inline config hook", second.stdout)

        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.write_text("{}", encoding="utf-8")
        call_log = self.root / "nebius-calls.log"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        fake_nebius = bin_dir / "nebius"
        fake_nebius.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                printf '%s\n' "$*" >> "$FAKE_NEBIUS_CALL_LOG"
                if [ "$1" = "iam" ] && [ "$2" = "get-access-token" ]; then
                  printf '%s\n' fake-token
                  exit 0
                fi
                exit 64
                """
            ),
            encoding="utf-8",
        )
        fake_nebius.chmod(0o700)
        exec_env = self.env.copy()
        exec_env["PATH"] = f"{bin_dir}{os.pathsep}{exec_env['PATH']}"
        exec_env["EXPECTED_TEST_TOKEN"] = "fake-token"
        exec_env["FAKE_NEBIUS_CALL_LOG"] = str(call_log)

        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "python3 -c 'import os; "
                    "expected = os.environ[\"EXPECTED_TEST_TOKEN\"]; "
                    "assert os.environ[\"NEBIUS_IAM_TOKEN\"] == expected; "
                    "assert os.environ[\"TOKEN\"] == expected; "
                    f"assert os.environ[\"NEBIUS_PROFILE\"] == \"codex-agent-{PROJECT}\"; "
                    f"assert os.environ[\"NEBIUS_PROJECT_ID\"] == \"{PROJECT}\"' "
                    "# api.nebius.cloud"
                ),
            },
        }
        hook_result = subprocess.run(
            [sys.executable, str(self.installed_hook_path())],
            input=json.dumps(payload),
            cwd=str(self.repo_root),
            env=exec_env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(
            hook_result.returncode,
            0,
            f"stdout:\n{hook_result.stdout}\nstderr:\n{hook_result.stderr}",
        )
        self.assertEqual(hook_result.stderr, "")
        hook_output = json.loads(hook_result.stdout)
        updated_command = hook_output["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertIn("nebius iam get-access-token", updated_command)
        self.assertNotIn("fake-token", hook_result.stdout)
        self.assertNotIn("fake-token", hook_result.stderr)
        self.assertNotIn("fake-token", updated_command)
        self.assertFalse(call_log.exists())

        command_result = subprocess.run(
            ["bash", "-c", updated_command],
            cwd=str(self.repo_root),
            env=exec_env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(
            call_log.read_text(encoding="utf-8").splitlines(),
            [f"iam get-access-token --profile codex-agent-{PROJECT}"],
        )

        self.assertEqual(
            command_result.returncode,
            0,
            f"stdout:\n{command_result.stdout}\nstderr:\n{command_result.stderr}",
        )
        self.assertEqual(command_result.stdout, "")
        self.assertEqual(command_result.stderr, "")


if __name__ == "__main__":
    unittest.main()
