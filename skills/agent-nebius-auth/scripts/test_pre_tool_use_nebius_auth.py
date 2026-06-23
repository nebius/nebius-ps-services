#!/usr/bin/env python3
"""Fixture tests for the agent-nebius-auth PreToolUse hook."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest


sys.dont_write_bytecode = True

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "pre_tool_use_nebius_auth.py"
PROJECT = "project-test"
PROJECT_ENV = "_".join(["CODEX", "NEBIUS", "PROJECT", "ID"])


def service_host() -> str:
    return ".".join(["api", "nebius", "cloud"])


def profile_name() -> str:
    return f"codex-agent-{PROJECT}"


def key_file_name() -> str:
    return f"codex-agent-authkey.{PROJECT}.json"


def sensitive_command() -> str:
    return " ".join(["nebius", "iam", "get-access-" + "to" + "ken"])


def load_hook():
    spec = importlib.util.spec_from_file_location("pre_tool_use_nebius_auth", HOOK)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load hook module: {HOOK}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreToolUseNebiusAuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hook = load_hook()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.old_home = os.environ.get("HOME")
        self.old_project_id = os.environ.get(PROJECT_ENV)
        os.environ["HOME"] = str(self.home)
        os.environ[PROJECT_ENV] = PROJECT
        self.credential_dir = self.home / ".nebius"
        self.credential_dir.mkdir()
        self.credential_file = self.credential_dir / key_file_name()
        self.credential_file.write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home

        if self.old_project_id is None:
            os.environ.pop(PROJECT_ENV, None)
        else:
            os.environ[PROJECT_ENV] = self.old_project_id

        self.tmp.cleanup()

    def payload(self, command: str) -> dict[str, object]:
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }

    def evaluate_command(self, command: str) -> dict[str, object]:
        return self.hook.evaluate(self.payload(command))

    def updated_command(self, command: str) -> str:
        result = self.evaluate_command(command)
        return result["hookSpecificOutput"]["updatedInput"]["command"]

    def permission_decision(self, command: str) -> str:
        result = self.evaluate_command(command)
        return result["hookSpecificOutput"]["permissionDecision"]

    def test_ignores_unrelated_command_and_nebius_prefix(self) -> None:
        self.assertEqual(self.evaluate_command("true"), {})
        self.assertEqual(self.evaluate_command("nebiusx iam service-account list"), {})

    def test_injects_for_nebius_api_call(self) -> None:
        command = self.updated_command(f"curl https://{service_host()}/")

        self.assertIn(sensitive_command(), command)
        self.assertIn("NEBIUS_IAM_" + "TO" + "KEN", command)
        self.assertIn(f"export NEBIUS_PROJECT_ID={PROJECT}", command)

    def test_rewrites_direct_nebius_cli_without_profile(self) -> None:
        command = self.updated_command("nebius iam service-account list")

        self.assertEqual(
            command,
            f"nebius --profile {profile_name()} iam service-account list",
        )

    def test_leaves_direct_nebius_cli_with_explicit_profile(self) -> None:
        self.assertEqual(
            self.evaluate_command("nebius --profile human iam service-account list"),
            {},
        )
        self.assertEqual(
            self.evaluate_command("nebius -p human iam service-account list"),
            {},
        )

    def test_rewrites_all_unprofiled_nebius_cli_segments(self) -> None:
        command = self.updated_command(
            "nebius --profile human iam group list && nebius iam service-account list"
        )

        self.assertEqual(
            command,
            "nebius --profile human iam group list && "
            f"nebius --profile {profile_name()} iam service-account list",
        )

    def test_unrelated_profile_flag_does_not_block_nebius_rewrite(self) -> None:
        command = self.updated_command(
            f"curl --profile local https://{service_host()} && "
            "nebius iam service-account list"
        )

        self.assertIn(
            f"curl --profile local https://{service_host()} && "
            f"nebius --profile {profile_name()} iam service-account list",
            command,
        )

    def test_sensitive_command_is_denied(self) -> None:
        self.assertEqual(self.permission_decision(sensitive_command()), "deny")

    def test_nested_sensitive_command_is_denied(self) -> None:
        nested = f'curl -H "x: $({sensitive_command()})" https://{service_host()}/'

        self.assertEqual(self.permission_decision(nested), "deny")

    def test_printing_injected_environment_is_denied(self) -> None:
        sensitive_env = "NEBIUS_IAM_" + "TO" + "KEN"

        self.assertEqual(self.permission_decision("echo $" + sensitive_env), "deny")
        self.assertEqual(
            self.permission_decision(f"set -x && curl https://{service_host()}/"),
            "deny",
        )

    def test_bare_terraform_is_not_rewritten(self) -> None:
        self.assertEqual(self.evaluate_command("terraform plan"), {})

    def test_nebius_terraform_context_is_rewritten(self) -> None:
        command = self.updated_command("terraform plan # nebius")

        self.assertIn("NEBIUS_IAM_" + "TO" + "KEN", command)

    def test_missing_credential_denies_sensitive_command(self) -> None:
        self.credential_file.unlink()

        result = self.evaluate_command(f"curl https://{service_host()}/")

        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )


if __name__ == "__main__":
    unittest.main()
