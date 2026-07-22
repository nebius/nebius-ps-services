#!/usr/bin/env python3
"""Fixture tests for installer-managed agent-nebius-auth hook registration."""

from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import pty
import subprocess
import sys
import tempfile
import textwrap
import unittest


HOOK_SOURCE = "agent-nebius-auth-setup/assets/hooks"
PROJECT = "project-test"


def find_repo_root() -> Path | None:
    override = os.environ.get("AGENT_NEBIUS_AUTH_REPO_ROOT")
    if override:
        return Path(override).expanduser()

    for parent in Path(__file__).resolve().parents:
        if (parent / "install-skills.sh").is_file():
            return parent
    return None


class AgentNebiusAuthHookInstallRegistrationTest(unittest.TestCase):
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

    def installed_hook_target(self, relative_path: str) -> Path:
        return self.codex_home / "hooks" / relative_path

    def hook_provenance_path(self) -> Path:
        return self.codex_home / ".install-hooks-state" / "hook-files.tsv"

    def hook_provenance(self) -> dict[str, dict[str, str]]:
        entries: dict[str, dict[str, str]] = {}
        for line in self.hook_provenance_path().read_text(encoding="utf-8").splitlines():
            dest_rel, source_id, source_rel, source_sha, target_sha = line.split("\t")
            entries[dest_rel] = {
                "source_id": source_id,
                "source_rel": source_rel,
                "source_sha": source_sha,
                "target_sha": target_sha,
            }
        return entries

    def file_sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def make_hook_source(self, files: dict[str, str]) -> Path:
        hook_source = self.root / f"hook-source-{len(list(self.root.glob('hook-source-*')))}"
        for relative_path, content in files.items():
            target = hook_source / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return hook_source

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

    def run_installer_copy_only(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(self.installer),
                "--install-hooks",
                HOOK_SOURCE,
            ],
            cwd=str(self.repo_root),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_custom_hook_installer(
        self,
        hook_source: Path,
        *extra_args: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(self.installer),
                "--install-hooks",
                str(hook_source),
                *extra_args,
            ],
            cwd=str(self.repo_root),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_custom_hook_installer_interactive(
        self,
        hook_source: Path,
        *extra_args: str,
    ) -> tuple[int, str]:
        master_fd, slave_fd = pty.openpty()
        env = self.env.copy()
        env["TERM"] = "xterm-256color"
        env.pop("NO_COLOR", None)
        process = subprocess.Popen(
            [
                "bash",
                str(self.installer),
                "--install-hooks",
                str(hook_source),
                *extra_args,
            ],
            cwd=str(self.repo_root),
            env=env,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
        chunks: list[bytes] = []
        try:
            while True:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(master_fd)

        return process.wait(), b"".join(chunks).decode(errors="replace").replace(
            "\r\n", "\n"
        )

    def run_installer_replace_hooks(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(self.installer),
                "--install-hooks",
                HOOK_SOURCE,
                "--register-hooks",
                "--replace-hooks-json",
            ],
            cwd=str(self.repo_root),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_installer_refresh_hooks(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(self.installer),
                "--install-hooks",
                HOOK_SOURCE,
                "--register-hooks",
                "--refresh-hook-registrations",
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

    def run_all_hooks_installer_copy_only(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(self.installer),
                "--install-all-hooks",
            ],
            cwd=str(self.repo_root),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def write_inline_config_block(self) -> None:
        self.config_path().write_text(
            textwrap.dedent(
                rf"""\
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

    def write_unmarked_inline_config_entry(self) -> None:
        self.config_path().write_text(
            textwrap.dedent(
                rf"""\
                model = "gpt-test"

                [[hooks.PreToolUse]]
                matcher = "^Bash$"

                [[hooks.PreToolUse.hooks]]
                type = "command"
                command = 'CODEX_NEBIUS_PROJECT_ID={PROJECT} python3 /tmp/pre_tool_use_nebius_auth.py'
                timeout = 30
                """
            ),
            encoding="utf-8",
        )

    def write_duplicate_hooks_json_registration(self) -> str:
        original_hooks = json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "^Bash$",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 /old/pre_tool_use_nebius_auth.py",
                                }
                            ],
                        }
                    ]
                }
            },
            indent=2,
            sort_keys=True,
        )
        (self.codex_home / "hooks.json").write_text(original_hooks + "\n", encoding="utf-8")
        return original_hooks + "\n"

    def assert_failed_before_mutation(
        self,
        result: subprocess.CompletedProcess[str],
        original_config: str,
    ) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config_path().read_text(encoding="utf-8"), original_config)
        self.assertFalse(self.selector_path().exists())
        self.assertEqual(list(self.codex_home.glob("config.toml.bak.agent-nebius-auth.*")), [])
        self.assertFalse((self.codex_home / "hooks.json").exists())
        self.assertFalse(self.installed_hook_path().exists())
        self.assertIn("inline config.toml hook entry detected", result.stderr)
        self.assertIn("Remove the inline agent-nebius-auth hook entry", result.stdout)
        self.assertNotIn(PROJECT, result.stdout)
        self.assertNotIn(PROJECT, result.stderr)

    def assert_duplicate_registration_failed_before_payload_mutation(
        self,
        result: subprocess.CompletedProcess[str],
        original_hooks_json: str,
        original_hook_payload: str,
    ) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            (self.codex_home / "hooks.json").read_text(encoding="utf-8"),
            original_hooks_json,
        )
        self.assertEqual(
            self.installed_hook_path().read_text(encoding="utf-8"),
            original_hook_payload,
        )
        self.assertFalse(self.hook_provenance_path().exists())
        self.assertFalse((self.codex_home / ".install-hooks-state" / "backups").exists())
        self.assertIn("Refusing to register duplicate", result.stderr)

    def test_register_hooks_duplicate_fails_before_payload_overwrite(self) -> None:
        original_hooks_json = self.write_duplicate_hooks_json_registration()
        self.installed_hook_path().parent.mkdir(parents=True, exist_ok=True)
        original_hook_payload = "# local hook customization\n"
        self.installed_hook_path().write_text(original_hook_payload, encoding="utf-8")

        result = self.run_installer()

        self.assert_duplicate_registration_failed_before_payload_mutation(
            result,
            original_hooks_json,
            original_hook_payload,
        )

    def test_targeted_refresh_replaces_only_same_event_script_entry(self) -> None:
        initial = self.run_installer()
        self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
        hooks_path = self.codex_home / "hooks.json"
        existing = json.loads(hooks_path.read_text(encoding="utf-8"))
        existing["hooks"]["PreToolUse"][0]["matcher"] = "^OldBash$"
        existing["owner"] = "preserve-me"
        existing["hooks"]["Stop"] = [
            {
                "hooks": [
                    {"type": "command", "command": "python3 /other/keep_me.py"}
                ]
            }
        ]
        hooks_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

        first = self.run_installer_refresh_hooks()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        refreshed = json.loads(hooks_path.read_text(encoding="utf-8"))
        self.assertEqual(refreshed["owner"], "preserve-me")
        self.assertEqual(refreshed["hooks"]["Stop"], existing["hooks"]["Stop"])
        registrations = self.hook_registrations()
        self.assertEqual(len(registrations), 1)
        self.assertIn("registrations: refreshed 1", first.stdout)
        backups = list(self.codex_home.glob("hooks.json.bak.*"))
        self.assertEqual(len(backups), 1)

        second = self.run_installer_refresh_hooks()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("registrations: unchanged 1", second.stdout)
        self.assertEqual(len(list(self.codex_home.glob("hooks.json.bak.*"))), 1)

    def test_targeted_refresh_rejects_mixed_handler_group_before_mutation(self) -> None:
        initial = self.run_installer()
        self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
        hooks_path = self.codex_home / "hooks.json"
        existing = json.loads(hooks_path.read_text(encoding="utf-8"))
        existing["hooks"]["PreToolUse"][0]["matcher"] = "^OldBash$"
        existing["hooks"]["PreToolUse"][0]["hooks"].append(
            {"type": "command", "command": "python3 /other/keep_me.py"}
        )
        original = json.dumps(existing, indent=2) + "\n"
        hooks_path.write_text(original, encoding="utf-8")
        payload_before = self.installed_hook_path().read_bytes()

        result = self.run_installer_refresh_hooks()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("differing or mixed handler list", result.stderr)
        self.assertEqual(hooks_path.read_text(encoding="utf-8"), original)
        self.assertEqual(self.installed_hook_path().read_bytes(), payload_before)

    def test_targeted_refresh_cli_guards(self) -> None:
        cases = (
            (
                ["--refresh-hook-registrations"],
                "must be combined with --register-hooks",
            ),
            (
                [
                    "--install-hooks",
                    HOOK_SOURCE,
                    "--register-hooks",
                    "--refresh-hook-registrations",
                    "--replace-hooks-json",
                ],
                "cannot be combined with --replace-hooks-json",
            ),
            (
                [
                    "--install-hooks",
                    HOOK_SOURCE,
                    "--register-hooks",
                    "--refresh-hook-registrations",
                    "--refresh-hook-registrations",
                ],
                "may only be specified once",
            ),
        )
        for args, message in cases:
            with self.subTest(args=args):
                result = subprocess.run(
                    ["bash", str(self.installer), *args],
                    cwd=str(self.repo_root),
                    env=self.env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_install_all_hooks_duplicate_registration_fails_before_payload_overwrite(self) -> None:
        original_hooks_json = self.write_duplicate_hooks_json_registration()
        self.installed_hook_path().parent.mkdir(parents=True, exist_ok=True)
        original_hook_payload = "# local hook customization\n"
        self.installed_hook_path().write_text(original_hook_payload, encoding="utf-8")

        result = self.run_all_hooks_installer()

        self.assert_duplicate_registration_failed_before_payload_mutation(
            result,
            original_hooks_json,
            original_hook_payload,
        )

    def test_register_hooks_fails_when_marked_inline_config_toml_hook_exists(self) -> None:
        self.write_inline_config_block()
        original_config = self.config_path().read_text(encoding="utf-8")

        result = self.run_installer()

        self.assert_failed_before_mutation(result, original_config)

    def test_copy_only_install_fails_when_inline_config_toml_hook_exists(self) -> None:
        self.write_inline_config_block()
        original_config = self.config_path().read_text(encoding="utf-8")

        result = self.run_installer_copy_only()

        self.assert_failed_before_mutation(result, original_config)

    def test_register_hooks_fails_when_unmarked_inline_config_toml_hook_exists(self) -> None:
        self.write_unmarked_inline_config_entry()
        original_config = self.config_path().read_text(encoding="utf-8")

        result = self.run_installer()

        self.assert_failed_before_mutation(result, original_config)

    def test_replace_hooks_fails_when_inline_config_toml_hook_exists(self) -> None:
        self.write_inline_config_block()
        original_config = self.config_path().read_text(encoding="utf-8")

        result = self.run_installer_replace_hooks()

        self.assert_failed_before_mutation(result, original_config)

    def test_install_all_hooks_fails_when_inline_config_toml_hook_exists(self) -> None:
        self.write_inline_config_block()
        original_config = self.config_path().read_text(encoding="utf-8")

        result = self.run_all_hooks_installer()

        self.assert_failed_before_mutation(result, original_config)

    def test_copy_only_install_all_fails_when_inline_config_toml_hook_exists(self) -> None:
        self.write_inline_config_block()
        original_config = self.config_path().read_text(encoding="utf-8")

        result = self.run_all_hooks_installer_copy_only()

        self.assert_failed_before_mutation(result, original_config)

    def test_hook_install_overwrites_customized_hook_file_copy_only(self) -> None:
        self.installed_hook_path().parent.mkdir(parents=True, exist_ok=True)
        custom_hook = "# local hook customization\n"
        self.installed_hook_path().write_text(custom_hook, encoding="utf-8")

        result = self.run_installer_copy_only()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source_hook = self.repo_root / HOOK_SOURCE / "pre_tool_use_nebius_auth.py"
        self.assertEqual(
            self.installed_hook_path().read_text(encoding="utf-8"),
            source_hook.read_text(encoding="utf-8"),
        )
        self.assertFalse((self.codex_home / "hooks.json").exists())
        backups = list(
            (self.codex_home / ".install-hooks-state" / "backups").glob(
                "*/pre_tool_use_nebius_auth.py"
            )
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), custom_hook)
        entry = self.hook_provenance()["pre_tool_use_nebius_auth.py"]
        self.assertEqual(entry["source_sha"], self.file_sha256(source_hook))
        self.assertEqual(entry["target_sha"], self.file_sha256(self.installed_hook_path()))

    def test_hook_install_overwrites_customized_hook_file_and_registers(self) -> None:
        self.installed_hook_path().parent.mkdir(parents=True, exist_ok=True)
        custom_hook = "# local hook customization\n"
        self.installed_hook_path().write_text(custom_hook, encoding="utf-8")

        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source_hook = self.repo_root / HOOK_SOURCE / "pre_tool_use_nebius_auth.py"
        self.assertEqual(
            self.installed_hook_path().read_text(encoding="utf-8"),
            source_hook.read_text(encoding="utf-8"),
        )
        self.assertEqual(len(self.hook_registrations()), 1)
        backups = list(
            (self.codex_home / ".install-hooks-state" / "backups").glob(
                "*/pre_tool_use_nebius_auth.py"
            )
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), custom_hook)

    def test_hook_install_records_file_provenance(self) -> None:
        result = self.run_installer_copy_only()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source_hook = self.repo_root / HOOK_SOURCE / "pre_tool_use_nebius_auth.py"
        entries = self.hook_provenance()
        self.assertIn("pre_tool_use_nebius_auth.py", entries)
        entry = entries["pre_tool_use_nebius_auth.py"]
        self.assertEqual(entry["source_id"], f"local:{(self.repo_root / HOOK_SOURCE).resolve()}")
        self.assertEqual(entry["source_rel"], "pre_tool_use_nebius_auth.py")
        self.assertEqual(entry["source_sha"], self.file_sha256(source_hook))
        self.assertEqual(entry["target_sha"], self.file_sha256(self.installed_hook_path()))

    def test_source_changed_existing_hook_overwrites_with_backup(self) -> None:
        hook_source = self.make_hook_source({"demo.py": "print('v1')\n"})
        first = self.run_custom_hook_installer(hook_source)
        (hook_source / "demo.py").write_text("print('v2')\n", encoding="utf-8")

        second = self.run_custom_hook_installer(hook_source)

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        target = self.installed_hook_target("demo.py")
        self.assertEqual(target.read_text(encoding="utf-8"), "print('v2')\n")
        backups = list((self.codex_home / ".install-hooks-state" / "backups").glob("*/demo.py"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "print('v1')\n")
        entry = self.hook_provenance()["demo.py"]
        self.assertEqual(entry["source_sha"], self.file_sha256(hook_source / "demo.py"))
        self.assertEqual(entry["target_sha"], self.file_sha256(target))
        self.assertIn("files: updated 1", second.stdout)

    def test_updated_file_count_is_bold_green_in_interactive_output(self) -> None:
        hook_source = self.make_hook_source({"demo.py": "print('v1')\n"})
        first = self.run_custom_hook_installer(hook_source)
        (hook_source / "demo.py").write_text("print('v2')\n", encoding="utf-8")

        returncode, output = self.run_custom_hook_installer_interactive(hook_source)

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(returncode, 0, output)
        styled_updated = "\x1b[32m\x1b[1mupdated\x1b[0m"
        self.assertIn(f"files: {styled_updated} 1", output)
        self.assertIn(
            f"Summary: files {styled_updated} 1, unchanged 0; "
            "registrations not requested",
            output,
        )

    def test_local_hook_edit_overwrites_after_provenance(self) -> None:
        hook_source = self.make_hook_source({"demo.py": "print('v1')\n"})
        first = self.run_custom_hook_installer(hook_source)
        target = self.installed_hook_target("demo.py")
        custom_hook = "print('local edit')\n"
        target.write_text(custom_hook, encoding="utf-8")

        second = self.run_custom_hook_installer(hook_source)

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "print('v1')\n")
        backups = list((self.codex_home / ".install-hooks-state" / "backups").glob("*/demo.py"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), custom_hook)
        entry = self.hook_provenance()["demo.py"]
        self.assertEqual(entry["source_sha"], self.file_sha256(hook_source / "demo.py"))
        self.assertEqual(entry["target_sha"], self.file_sha256(target))

    def test_install_all_hooks_overwrites_existing_nested_hook_file(self) -> None:
        target = self.installed_hook_target("tests/test_sdlc_hooks.py")
        target.parent.mkdir(parents=True, exist_ok=True)
        custom_hook = "# local nested hook customization\n"
        target.write_text(custom_hook, encoding="utf-8")

        result = self.run_all_hooks_installer_copy_only()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source_hook = self.repo_root / "sdlc-start" / "assets" / "hooks" / "tests" / "test_sdlc_hooks.py"
        self.assertEqual(target.read_text(encoding="utf-8"), source_hook.read_text(encoding="utf-8"))
        backups = list(
            (self.codex_home / ".install-hooks-state" / "backups").glob(
                "*/tests/test_sdlc_hooks.py"
            )
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), custom_hook)

    def test_overwrite_hook_files_flag_is_removed(self) -> None:
        removed_flag = "--overwrite" "-hook-files"
        help_result = subprocess.run(
            ["bash", str(self.installer), "--help"],
            cwd=str(self.repo_root),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )
        result = subprocess.run(
            [
                "bash",
                str(self.installer),
                removed_flag,
                "demo.py",
            ],
            cwd=str(self.repo_root),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(help_result.returncode, 0, help_result.stdout + help_result.stderr)
        self.assertNotIn(removed_flag, help_result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"unknown option: {removed_flag}", result.stderr)

    def test_install_hooks_requires_source_before_other_options(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(self.installer),
                "--install-hooks",
                "--register-hooks",
            ],
            cwd=str(self.repo_root),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--install-hooks requires a source hook directory", result.stderr)
        self.assertIn("Use --install-all-hooks", result.stdout)

    def test_repeated_install_all_hooks_reports_unchanged_by_source(self) -> None:
        first = self.run_all_hooks_installer()
        second = self.run_all_hooks_installer()

        for result in (first, second):
            self.assertEqual(
                result.returncode,
                0,
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )

        self.assertIn("unchanged agent-nebius-auth-setup/assets/hooks", second.stdout)
        self.assertIn("unchanged config-codex/assets/hooks", second.stdout)
        self.assertIn("unchanged sdlc-start/assets/hooks", second.stdout)
        self.assertIn("unchanged troubleshoot/assets/hooks", second.stdout)
        self.assertIn("files: unchanged 3", second.stdout)
        self.assertIn("files: unchanged 4", second.stdout)
        self.assertIn("files: unchanged 6", second.stdout)
        self.assertIn("files: unchanged 2", second.stdout)
        self.assertIn("registrations: unchanged 1", second.stdout)
        self.assertIn("registrations: unchanged 2", second.stdout)
        self.assertIn("Summary: files updated 0, unchanged 15; registrations unchanged 7", second.stdout)
        self.assertTrue(
            self.installed_hook_target("remediation_attempt_guard.py").is_file()
        )
        hooks = json.loads((self.codex_home / "hooks.json").read_text(encoding="utf-8"))
        remediation_events = {
            event
            for event, groups in hooks["hooks"].items()
            for group in groups
            for hook in group["hooks"]
            if "remediation_attempt_guard.py" in hook["command"]
        }
        self.assertEqual(remediation_events, {"PreToolUse", "Stop"})
        self.assertNotIn("Discovered hook source directories", second.stdout)
        self.assertNotIn("Template suffixes were stripped", second.stdout)
        self.assertNotIn("Hook files:", second.stdout)
        self.assertNotIn("Hook registrations:", second.stdout)
        self.assertNotIn("This did not modify hooks.json", second.stdout)
        self.assertNotIn("Action required: hook files or registrations changed", second.stderr)

    def test_repeated_register_hooks_is_idempotent_and_hook_runs(self) -> None:
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

        self.assertFalse(self.config_path().exists())
        self.assertFalse(self.selector_path().exists())
        self.assertEqual(len(self.hook_registrations()), 1)
        self.assertTrue(self.installed_hook_path().is_file())
        self.assertTrue(
            self.installed_hook_target("nebius_auth_shared.py").is_file()
        )
        self.assertTrue(
            self.installed_hook_target("nebius_auth_token_helper.py").is_file()
        )
        self.assertIn("registrations: unchanged 1", second.stdout)
        self.assertIn("Summary: files updated 0, unchanged 3; registrations unchanged 1", second.stdout)
        self.assertNotIn("Action required: hook files or registrations changed", second.stderr)

        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.write_text(
            '{"subject-credentials":{"type":"JWT","alg":"RS256","iss":"serviceaccount-test","sub":"serviceaccount-test"}}',
            encoding="utf-8",
        )
        credential.chmod(0o600)
        self.selector_path().write_text("project-wrong\n", encoding="utf-8")
        self.selector_path().chmod(0o600)
        call_log = self.root / "nebius-calls.log"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        fake_nebius = bin_dir / "nebius"
        fake_nebius.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                call_count=1
                if [ -f "{call_log}" ]; then
                  call_count=$(($(wc -l < "{call_log}") + 1))
                fi
                printf '%s\\n' "$*" >> "{call_log}"
                if [ "$1" = "iam" ] && [ "$2" = "get-access-token" ]; then
                  if [ -n "${{NEBIUS_IAM_TOKEN:-}}" ] || [ -n "${{TOKEN:-}}" ]; then
                    printf 'stale token env reached token mint\\n' >&2
                    exit 65
                  fi
                  printf 'fake-token-%s\\n' "$call_count"
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
        exec_env["EXPECTED_CHILD_TOKEN"] = "fake-token-2"
        exec_env["EXPECTED_CREDENTIALS_FILE"] = str(credential)
        operation_log = self.root / "idempotent-operation.log"
        exec_env["NEBIUS_IAM_TOKEN"] = "stale-parent-token"
        exec_env["TOKEN"] = "stale-parent-token"
        exec_env["CODEX_NEBIUS_PROJECT_ID"] = "project-wrong"
        operation = bin_dir / "idempotent-read"
        operation.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                set -eu
                printf 'attempt\\n' >> "{operation_log}"
                count=$(wc -l < "{operation_log}" | tr -d ' ')
                if [ "$count" -eq 1 ]; then
                  exit 77
                fi
                test "$NEBIUS_IAM_TOKEN" = "$EXPECTED_CHILD_TOKEN"
                test -z "${{TOKEN:-}}"
                """
            ),
            encoding="utf-8",
        )
        operation.chmod(0o700)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    f"CODEX_NEBIUS_PROJECT_ID={PROJECT} "
                    "python3 \"$CODEX_NEBIUS_TOKEN_HELPER\" retry-idempotent -- "
                    f"{operation} && "
                    "python3 -c 'import os; "
                    "assert \"NEBIUS_IAM_TOKEN\" not in os.environ; "
                    "assert \"TOKEN\" not in os.environ; "
                    f"assert os.environ[\"NEBIUS_PROFILE\"] == \"codex-agent-{PROJECT}\"; "
                    f"assert os.environ[\"NEBIUS_PROJECT_ID\"] == \"{PROJECT}\"; "
                    "assert os.environ[\"NEBIUS_AUTH_CREDENTIALS_FILE\"] == "
                    "os.environ[\"EXPECTED_CREDENTIALS_FILE\"]' "
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
        self.assertNotIn("nebius iam get-access-token", updated_command)
        self.assertNotIn("nebius_refresh_token() {", updated_command)
        self.assertIn(
            'export NEBIUS_AUTH_CREDENTIALS_FILE="$CREDENTIALS_FILE_VALUE"',
            updated_command,
        )
        self.assertIn(
            'export CODEX_NEBIUS_TOKEN_HELPER="$TOKEN_HELPER_VALUE"',
            updated_command,
        )
        self.assertIn(
            str(self.installed_hook_target("nebius_auth_token_helper.py")),
            updated_command,
        )
        self.assertNotIn("fake-token", hook_result.stdout)
        self.assertNotIn("fake-token", hook_result.stderr)
        self.assertNotIn("fake-token", updated_command)
        self.assertFalse(call_log.exists())

        sdlc_hook_dir = self.repo_root / "sdlc-start" / "assets" / "hooks"
        scanner_result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    f"sys.path.insert(0, {str(sdlc_hook_dir)!r}); "
                    "from lib.sdlc_policy import contains_secret; "
                    "raise SystemExit(1 if contains_secret(sys.stdin.read()) else 0)"
                ),
            ],
            input=updated_command,
            cwd=str(self.repo_root),
            env=exec_env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            scanner_result.returncode,
            0,
            "generated auth wrapper must not resemble a literal secret assignment",
        )

        command_result = subprocess.run(
            ["bash", "-c", updated_command],
            cwd=str(self.repo_root),
            env=exec_env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(
            command_result.returncode,
            0,
            f"stdout:\n{command_result.stdout}\nstderr:\n{command_result.stderr}\n"
            f"mint calls: {call_log.read_text(encoding='utf-8') if call_log.exists() else '<none>'}",
        )
        self.assertEqual(command_result.stdout, "")
        self.assertEqual(command_result.stderr, "")
        self.assertEqual(
            call_log.read_text(encoding="utf-8").splitlines(),
            [
                f"iam get-access-token --no-browser --profile codex-agent-{PROJECT}",
                f"iam get-access-token --no-browser --profile codex-agent-{PROJECT}",
            ],
        )
        self.assertEqual(
            operation_log.read_text(encoding="utf-8").splitlines(),
            ["attempt", "attempt"],
        )


if __name__ == "__main__":
    unittest.main()
