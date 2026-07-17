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

HOOK = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "hooks"
    / "pre_tool_use_nebius_auth.py"
)
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

    def clear_project_env(self) -> None:
        os.environ.pop(PROJECT_ENV, None)

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
        self.assertIn("nebius_refresh_token() {", command)
        self.assertIn("unset TOKEN NEBIUS_IAM_" + "TO" + "KEN", command)
        self.assertIn('if [ -n "${BASH_VERSION:-}" ]; then', command)
        self.assertIn("export -f nebius_refresh_token", command)
        self.assertIn("nebius_refresh_token\n", command)
        self.assertIn(f"PROJECT_ID_VALUE={PROJECT}", command)
        self.assertIn('export NEBIUS_PROJECT_ID="$PROJECT_ID_VALUE"', command)
        self.assertIn(
            f"CREDENTIALS_FILE_VALUE={self.credential_file}",
            command,
        )
        self.assertIn(
            'export NEBIUS_AUTH_CREDENTIALS_FILE="$CREDENTIALS_FILE_VALUE"',
            command,
        )

    def test_refresh_helper_is_available_to_long_running_shell_command(self) -> None:
        command = self.updated_command("nebius_refresh_token && ./poll.sh")

        self.assertIn("nebius_refresh_token() {", command)
        self.assertIn("unset TOKEN NEBIUS_IAM_" + "TO" + "KEN", command)
        self.assertIn('if [ -n "${BASH_VERSION:-}" ]; then', command)
        self.assertIn("export -f nebius_refresh_token", command)
        self.assertIn("nebius_refresh_token && ./poll.sh", command)

    def test_refresh_helper_mention_does_not_trigger_token_injection(self) -> None:
        self.assertEqual(
            self.evaluate_command("python3 -c 'print(\"nebius_refresh_token\")'"),
            {},
        )

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

    def test_rewrites_nebius_cli_after_safe_command_wrappers(self) -> None:
        cases = {
            "command nebius iam service-account list": (
                f"command nebius --profile {profile_name()} "
                "iam service-account list"
            ),
            "time nebius iam service-account list": (
                f"time nebius --profile {profile_name()} "
                "iam service-account list"
            ),
            "env RESULT=ready nebius iam service-account list": (
                f"env RESULT=ready nebius --profile {profile_name()} "
                "iam service-account list"
            ),
            "if true; then nebius iam service-account list; fi": (
                "if true; then "
                f"nebius --profile {profile_name()} iam service-account list; fi"
            ),
        }

        for original, expected in cases.items():
            with self.subTest(command=original):
                self.assertEqual(self.updated_command(original), expected)

    def test_nested_nebius_cli_inherits_agent_profile(self) -> None:
        original = "bash -c 'nebius iam service-account list'"

        command = self.updated_command(original)

        self.assertIn(f"PROFILE={profile_name()}", command)
        self.assertIn('export NEBIUS_PROFILE="$PROFILE"', command)
        self.assertIn(original, command)
        self.assertNotIn(
            f"export NEBIUS_PROFILE={profile_name()}",
            command,
        )

    def test_mixed_direct_and_nested_nebius_cli_share_agent_profile(self) -> None:
        original = (
            "nebius iam group list; "
            "bash -c 'nebius iam service-account list'"
        )

        command = self.updated_command(original)

        self.assertIn(
            f"nebius --profile {profile_name()} iam group list",
            command,
        )
        self.assertIn('export NEBIUS_PROFILE="$PROFILE"', command)
        self.assertIn("bash -c 'nebius iam service-account list'", command)

    def test_quoted_nebius_text_is_not_rewritten(self) -> None:
        cases = {
            (
                "nebius iam group list; "
                "printf '%s\\n' '; nebius iam service-account list'"
            ): (
                f"nebius --profile {profile_name()} iam group list; "
                "printf '%s\\n' '; nebius iam service-account list'"
            ),
            (
                "nebius iam group list; "
                'printf \'%s\\n\' "`true`; nebius iam service-account list"'
            ): (
                f"nebius --profile {profile_name()} iam group list; "
                'printf \'%s\\n\' "`true`; nebius iam service-account list"'
            ),
        }

        for original, expected in cases.items():
            with self.subTest(command=original):
                self.assertEqual(self.updated_command(original), expected)

    def test_nested_nebius_cli_with_explicit_profile_is_unchanged(self) -> None:
        self.assertEqual(
            self.evaluate_command(
                "bash -c 'nebius --profile human iam service-account list'"
            ),
            {},
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
        self.assertEqual(
            self.permission_decision(sensitive_command() + ">/tmp/token"),
            "deny",
        )
        continued = "nebius iam \\\nget-access-" + "token>/tmp/token"
        self.assertEqual(self.permission_decision(continued), "deny")

    def test_only_exact_agent_profile_verification_to_dev_null_is_allowed(self) -> None:
        safe = (
            f"{sensitive_command()} --profile {profile_name()} "
            ">/dev/null"
        )
        self.assertEqual(self.evaluate_command(safe), {})
        self.assertEqual(
            self.evaluate_command(
                f"{sensitive_command()} --profile {profile_name()}>/dev/null"
            ),
            {},
        )

        unsafe_commands = [
            f"{sensitive_command()} --profile human >/dev/null",
            f"{sensitive_command()} --profile {profile_name()} --format json >/dev/null",
            f"{sensitive_command()} --profile {profile_name()} >/tmp/token >/dev/null",
            f"{sensitive_command()} --profile {profile_name()} >/dev/null >/tmp/token",
        ]
        for command in unsafe_commands:
            with self.subTest(command=command):
                self.assertEqual(self.permission_decision(command), "deny")

    def test_inert_token_command_text_is_not_treated_as_execution(self) -> None:
        command = self.updated_command(
            f"rg '{sensitive_command()}' docs # {service_host()}"
        )

        self.assertIn(f"rg '{sensitive_command()}' docs", command)
        self.assertEqual(
            self.evaluate_command(
                f"python3 -c 'print(\"{sensitive_command()}\")'"
            ),
            {},
        )

    def test_nested_sensitive_command_is_denied(self) -> None:
        nested = f'curl -H "x: $({sensitive_command()})" https://{service_host()}/'

        self.assertEqual(self.permission_decision(nested), "deny")
        self.assertEqual(
            self.permission_decision(f"echo `{sensitive_command()}`"),
            "deny",
        )
        self.assertEqual(
            self.permission_decision(
                f"bash -c 'eval \"{sensitive_command()}\"'"
            ),
            "deny",
        )
        self.assertEqual(
            self.permission_decision(
                "python3 -c 'import os; "
                f"os.system(\"{sensitive_command()}\")'"
            ),
            "deny",
        )
        list_form = ", ".join(
            f'"{part}"'
            for part in ["nebius", "iam", "get-access-" + "token"]
        )
        self.assertEqual(
            self.permission_decision(
                "python3 -c 'import subprocess; "
                f"subprocess.run([{list_form}])'"
            ),
            "deny",
        )

    def test_printing_injected_environment_is_denied(self) -> None:
        sensitive_env = "NEBIUS_IAM_" + "TO" + "KEN"

        self.assertEqual(self.permission_decision("echo $" + sensitive_env), "deny")
        self.assertEqual(
            self.permission_decision(
                f"printf '%s\\n' \"${sensitive_env}\"; curl https://{service_host()}/"
            ),
            "deny",
        )
        self.assertEqual(
            self.permission_decision(f"set -x && curl https://{service_host()}/"),
            "deny",
        )
        self.assertEqual(
            self.permission_decision(
                f"set -o xtrace && curl https://{service_host()}/"
            ),
            "deny",
        )
        self.assertEqual(
            self.permission_decision(
                f"set -xv && curl https://{service_host()}/"
            ),
            "deny",
        )
        self.assertEqual(
            self.permission_decision(f"bash -xc true # {service_host()}"),
            "deny",
        )
        for dump_command in ("env", "printenv", "export -p", "set", "declare -p"):
            with self.subTest(command=dump_command):
                self.assertEqual(
                    self.permission_decision(
                        f"{dump_command}; curl https://{service_host()}/"
                    ),
                    "deny",
                )
        self.assertEqual(
            self.permission_decision(f"env -0; curl https://{service_host()}/"),
            "deny",
        )
        self.assertEqual(
            self.permission_decision(
                f"cat <(env); curl https://{service_host()}/"
            ),
            "deny",
        )
        self.assertEqual(
            self.permission_decision(
                f"echo \"$(printenv TOKEN)\"; curl https://{service_host()}/"
            ),
            "deny",
        )
        self.assertEqual(
            self.permission_decision(
                f"env RESULT=ready printenv TOKEN; curl https://{service_host()}/"
            ),
            "deny",
        )
        self.assertEqual(
            self.permission_decision(
                f"declare TOKEN; curl https://{service_host()}/"
            ),
            "deny",
        )
        self.assertEqual(
            self.permission_decision("nebius_refresh_token && bash -c env"),
            "deny",
        )
        self.assertEqual(
            self.permission_decision("nebius_refresh_token&&bash -c env"),
            "deny",
        )
        self.assertEqual(
            self.permission_decision("nebius_refresh_token;bash -c env"),
            "deny",
        )
        self.assertEqual(
            self.permission_decision(
                "nebius_refresh_token && bash -c 'printenv NEBIUS_IAM_TOKEN'"
            ),
            "deny",
        )
        self.assertEqual(
            self.permission_decision("nebius_refresh_token && bash -c ' env'"),
            "deny",
        )
        self.assertEqual(
            self.permission_decision(
                "nebius_refresh_token && python3 -c 'import os; print(os.environ)'"
            ),
            "deny",
        )

    def test_safe_status_and_log_output_is_allowed_with_token_injection(self) -> None:
        original = (
            "printf '%s\\n' status; "
            f"curl https://{service_host()}/; "
            "echo logs; tail -n 20 service.log"
        )

        command = self.updated_command(original)

        self.assertIn(original, command)

    def test_safe_nested_status_output_is_allowed_with_token_injection(self) -> None:
        original = (
            "nebius_refresh_token && "
            "bash -c 'printf \"%s\\n\" status; ./read-status-and-logs.sh'"
        )

        command = self.updated_command(original)

        self.assertIn(original, command)

    def test_safe_shell_setup_and_nonsecret_environment_use_are_allowed(self) -> None:
        original = (
            "set -euo pipefail; set +o xtrace >/dev/null; export RESULT=ready; "
            "printenv PATH >/dev/null; echo \"$(date)\"; "
            f"env RESULT=ready ./read-status-and-logs.sh # {service_host()}"
        )

        command = self.updated_command(original)

        self.assertIn(original, command)

    def test_rewritten_bash_env_helper_does_not_chain_ambient_bash_env(self) -> None:
        command = self.updated_command("nebius_refresh_token && ./poll.sh")

        self.assertIn("export BASH_ENV=\"$NEBIUS_REFRESH_BASH_ENV\"", command)
        self.assertNotIn("NEBIUS_REFRESH_PREVIOUS_BASH_ENV", command)

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

    def test_default_project_file_selects_profile_with_multiple_credentials(self) -> None:
        self.clear_project_env()
        other = self.credential_dir / "codex-agent-authkey.project-other.json"
        other.write_text("{}", encoding="utf-8")
        (self.credential_dir / "codex-agent-default-project-id").write_text(
            f"{PROJECT}\n",
            encoding="utf-8",
        )

        command = self.updated_command(f"curl https://{service_host()}/")

        self.assertIn(sensitive_command(), command)
        self.assertIn(f"PROJECT_ID_VALUE={PROJECT}", command)
        self.assertIn('export NEBIUS_PROJECT_ID="$PROJECT_ID_VALUE"', command)

    def test_multiple_credentials_without_default_project_denies(self) -> None:
        self.clear_project_env()
        other = self.credential_dir / "codex-agent-authkey.project-other.json"
        other.write_text("{}", encoding="utf-8")

        result = self.evaluate_command(f"curl https://{service_host()}/")

        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )


if __name__ == "__main__":
    unittest.main()
