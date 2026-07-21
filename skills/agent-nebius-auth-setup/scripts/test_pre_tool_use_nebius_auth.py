#!/usr/bin/env python3
"""Contract tests for the project-selected Nebius authentication hook."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
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
PROJECT_ENV = "CODEX_NEBIUS_PROJECT_ID"
PROFILE = f"codex-agent-{PROJECT}"


def credential_json(service_account_id: str) -> str:
    return json.dumps(
        {
            "subject-credentials": {
                "type": "JWT",
                "alg": "RS256",
                "iss": service_account_id,
                "sub": service_account_id,
            }
        }
    )


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
        os.environ["HOME"] = str(self.home)
        credential_dir = self.home / ".nebius"
        credential_dir.mkdir()
        self.credential_file = (
            credential_dir / f"codex-agent-authkey.{PROJECT}.json"
        )
        self.write_credential()

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home
        self.tmp.cleanup()

    def write_credential(self, account_id: str = "serviceaccount-test") -> None:
        self.credential_file.write_text(
            credential_json(account_id), encoding="utf-8"
        )
        self.credential_file.chmod(0o600)

    @staticmethod
    def payload(command: str) -> dict[str, object]:
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }

    def evaluate(
        self, command: str, *, selector: bool = True
    ) -> dict[str, object]:
        raw = f"{PROJECT_ENV}={PROJECT} {command}" if selector else command
        return self.hook.evaluate(self.payload(raw))

    def decision(self, command: str) -> str:
        return self.evaluate(command)["hookSpecificOutput"]["permissionDecision"]

    def updated(self, command: str) -> str:
        return self.evaluate(command)["hookSpecificOutput"]["updatedInput"][
            "command"
        ]

    def test_selector_injects_same_renewable_context_for_any_executable(self) -> None:
        commands = (
            "true",
            "bash ./script.sh",
            "python3 ./script.py",
            "./custom-client",
            "nebius iam service-account list",
            "nebius-cxcli status",
            "terraform plan",
        )
        for original in commands:
            with self.subTest(command=original):
                rewritten = self.updated(original)
                self.assertIn(f"PROFILE={PROFILE}", rewritten)
                self.assertIn(f"PROJECT_ID_VALUE={PROJECT}", rewritten)
                self.assertIn(
                    f"CREDENTIALS_FILE_VALUE={self.credential_file}", rewritten
                )
                self.assertIn(
                    "TOKEN_HELPER_VALUE="
                    + str(HOOK.parent / "nebius_auth_token_helper.py"),
                    rewritten,
                )
                self.assertIn('export NEBIUS_PROFILE="$PROFILE"', rewritten)
                self.assertIn(
                    'export NEBIUS_PROJECT_ID="$PROJECT_ID_VALUE"', rewritten
                )
                self.assertIn(
                    'export NEBIUS_AUTH_CREDENTIALS_FILE="$CREDENTIALS_FILE_VALUE"',
                    rewritten,
                )
                self.assertIn(
                    'export CODEX_NEBIUS_TOKEN_HELPER="$TOKEN_HELPER_VALUE"',
                    rewritten,
                )
                self.assertIn(f"\n{original}\n", rewritten)

    def test_context_clears_selector_and_static_bearer_tokens(self) -> None:
        rewritten = self.updated("./custom-client")

        self.assertIn(
            "unset CODEX_NEBIUS_PROJECT_ID TOKEN NEBIUS_IAM_TOKEN ", rewritten
        )
        self.assertNotIn("get-access-token", rewritten)
        self.assertNotIn("set -euo pipefail", rewritten)
        self.assertNotIn("export TOKEN=", rewritten)
        self.assertNotIn("export NEBIUS_IAM_TOKEN=", rewritten)

    def test_rewritten_context_executes_for_shell_and_python(self) -> None:
        checks = (
            "test \"$NEBIUS_PROFILE\" = codex-agent-project-test",
            "python3 -c 'import os; assert os.environ[\"NEBIUS_PROJECT_ID\"] == \"project-test\"'",
        )
        for check in checks:
            with self.subTest(command=check):
                result = subprocess.run(
                    ["bash", "-c", self.updated(check)],
                    cwd=self.home,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_selector_is_inert_for_unrelated_commands(self) -> None:
        for command in ("true", "python3 ./script.py", "terraform plan"):
            with self.subTest(command=command):
                self.assertEqual(self.evaluate(command, selector=False), {})

    def test_missing_or_malformed_selector_denies_nebius_commands(self) -> None:
        commands = (
            "nebius iam group list",
            "curl https://api.nebius.cloud/",
            "env CODEX_NEBIUS_PROJECT_ID=project-test nebius iam group list",
        )
        for command in commands:
            with self.subTest(command=command):
                result = self.evaluate(command, selector=False)
                self.assertEqual(
                    result["hookSpecificOutput"]["permissionDecision"], "deny"
                )
                self.assertIn(
                    "Start the Bash command exactly",
                    result["hookSpecificOutput"]["permissionDecisionReason"],
                )

    def test_managed_auth_mutation_is_denied(self) -> None:
        commands = (
            "TOKEN=wrong true",
            "NEBIUS_PROFILE=human ./custom-client",
            "env -u NEBIUS_PROJECT_ID ./custom-client",
            "unset CODEX_NEBIUS_TOKEN_HELPER; true",
            "nebius --profile human iam group list",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), "deny")

    def test_environment_clearing_or_splitting_is_denied_through_wrappers(self) -> None:
        commands = (
            "env -i ./custom-client",
            "env --ignore-environment ./custom-client",
            "env -S './custom-client --status'",
            "timeout 5 env -i ./custom-client",
            "bash -c 'env -i ./custom-client'",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), "deny")

    def test_environment_and_secret_disclosure_is_denied(self) -> None:
        commands = (
            "env",
            "printenv",
            "export -p",
            "python3 -c 'import os; print(os.environ)'",
            "set -x; ./custom-client",
            "curl -v https://api.nebius.cloud/",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), "deny")

    def test_token_helper_child_is_subject_to_disclosure_policy(self) -> None:
        helper = 'python3 "$CODEX_NEBIUS_TOKEN_HELPER" exec-token --'
        denied = (
            f"{helper} env",
            f"{helper} printenv NEBIUS_IAM_TOKEN",
            f"{helper} bash -xc 'true'",
            f"{helper} bash -c 'env'",
            f"{helper} curl -v https://api.nebius.cloud/",
            f"{helper} TOKEN=wrong ./custom-client",
            f"{helper} env -i ./custom-client",
        )
        for command in denied:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), "deny")

        allowed = f"{helper} ./custom-client"
        self.assertIn(f"\n{allowed}\n", self.updated(allowed))

        missing_selector = self.evaluate(allowed, selector=False)
        self.assertEqual(
            missing_selector["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_credential_must_be_safe_and_canonical(self) -> None:
        cases = {
            "missing": None,
            "mode": credential_json("serviceaccount-test"),
            "legacy": '{"service_account_id":"serviceaccount-test"}',
            "conflict": credential_json("serviceaccount-test").replace(
                '"sub": "serviceaccount-test"',
                '"sub": "serviceaccount-other"',
            ),
        }
        for case, content in cases.items():
            with self.subTest(case=case):
                self.credential_file.unlink(missing_ok=True)
                if content is not None:
                    self.credential_file.write_text(content, encoding="utf-8")
                    self.credential_file.chmod(0o644 if case == "mode" else 0o600)
                self.assertEqual(self.decision("./custom-client"), "deny")

    def test_symlink_and_oversized_credentials_are_denied(self) -> None:
        self.credential_file.unlink()
        target = self.credential_file.with_name("target.json")
        target.write_text(credential_json("serviceaccount-test"), encoding="utf-8")
        target.chmod(0o600)
        self.credential_file.symlink_to(target)
        self.assertEqual(self.decision("./custom-client"), "deny")

        self.credential_file.unlink()
        self.credential_file.write_text(
            credential_json("serviceaccount-test") + " " * (1024 * 1024),
            encoding="utf-8",
        )
        self.credential_file.chmod(0o600)
        self.assertEqual(self.decision("./custom-client"), "deny")

    def test_direct_token_mint_is_denied_except_exact_quiet_verification(self) -> None:
        mint = "nebius iam get-access-token"
        self.assertEqual(self.decision(mint), "deny")
        self.assertEqual(self.decision(mint + " >/tmp/token"), "deny")

        safe = f"{mint} --profile {PROFILE} >/dev/null"
        rewritten = self.updated(safe)
        self.assertIn(f"\n{safe}\n", rewritten)

    def test_non_bash_and_non_pretooluse_payloads_are_ignored(self) -> None:
        payload = self.payload("true")
        payload["tool_name"] = "Read"
        self.assertEqual(self.hook.evaluate(payload), {})
        payload = self.payload("true")
        payload["hook_event_name"] = "PostToolUse"
        self.assertEqual(self.hook.evaluate(payload), {})


if __name__ == "__main__":
    unittest.main()
