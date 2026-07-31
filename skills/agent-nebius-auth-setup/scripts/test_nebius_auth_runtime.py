#!/usr/bin/env python3
"""Tests for canonical credential parsing and just-in-time token execution."""

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

HOOK_DIR = Path(__file__).resolve().parent.parent / "assets" / "hooks"
SHARED = HOOK_DIR / "nebius_auth_shared.py"
HELPER = HOOK_DIR / "nebius_auth_token_helper.py"
PROJECT = "project-test"
PROFILE = f"codex-agent-{PROJECT}"
IAM_ENV = "_".join(("NEBIUS", "IAM", "TOKEN"))
GENERIC_ENV = "".join(("TO", "KEN"))


def load_shared():
    spec = importlib.util.spec_from_file_location("nebius_auth_shared", SHARED)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load shared auth module: {SHARED}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def credential_json(
    issuer: str = "serviceaccount-test", subject: str | None = None
) -> str:
    return json.dumps(
        {
            "subject-credentials": {
                "type": "JWT",
                "alg": "RS256",
                "iss": issuer,
                "sub": issuer if subject is None else subject,
            }
        }
    )


class SharedCredentialTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.shared = load_shared()

    def test_only_current_matching_issuer_and_subject_are_accepted(self) -> None:
        self.assertEqual(
            self.shared.credential_service_account_id(
                credential_json().encode()
            ),
            "serviceaccount-test",
        )
        invalid = (
            credential_json(subject="serviceaccount-other"),
            '{"service_account_id":"serviceaccount-test"}',
            '{"subject":{"service_account":{"id":"serviceaccount-test"}}}',
            '{"nested":{"account_id":"serviceaccount-test"},'
            '"subject-credentials":{"type":"JWT","alg":"RS256",'
            '"iss":"serviceaccount-test","sub":"serviceaccount-test"}}',
            '{"subject-credentials":{"type":"OTHER","alg":"RS256",'
            '"iss":"serviceaccount-test","sub":"serviceaccount-test"}}',
            '{"subject-credentials":{"type":"JWT","alg":"OTHER",'
            '"iss":"serviceaccount-test","sub":"serviceaccount-test"}}',
            '{"subject-credentials":{"iss":"not-an-account","sub":"not-an-account"}}',
            '{"subject-credentials":{"iss":"serviceaccount-test",'
            '"iss":"serviceaccount-test","sub":"serviceaccount-test"}}',
            "[]",
            "not-json",
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(self.shared.CredentialError):
                    self.shared.credential_service_account_id(raw.encode())

    def test_file_reader_rejects_mode_symlink_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            credential = root / "credential.json"
            credential.write_text(credential_json(), encoding="utf-8")
            credential.chmod(0o600)
            self.assertEqual(
                self.shared.credential_identity_from_file(
                    credential, expected_uid=os.getuid()
                ),
                "serviceaccount-test",
            )

            credential.chmod(0o644)
            with self.assertRaises(self.shared.CredentialError):
                self.shared.credential_identity_from_file(
                    credential, expected_uid=os.getuid()
                )

            credential.unlink()
            target = root / "target.json"
            target.write_text(credential_json(), encoding="utf-8")
            target.chmod(0o600)
            credential.symlink_to(target)
            with self.assertRaises(self.shared.CredentialError):
                self.shared.credential_identity_from_file(
                    credential, expected_uid=os.getuid()
                )

            credential.unlink()
            credential.write_bytes(b" " * (1024 * 1024 + 1))
            credential.chmod(0o600)
            with self.assertRaises(self.shared.CredentialError):
                self.shared.credential_identity_from_file(
                    credential, expected_uid=os.getuid()
                )


class TokenHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.bin_dir = self.home / "bin"
        self.bin_dir.mkdir()
        credential_dir = self.home / ".nebius"
        credential_dir.mkdir()
        self.credential = credential_dir / f"codex-agent-authkey.{PROJECT}.json"
        self.credential.write_text(credential_json(), encoding="utf-8")
        self.credential.chmod(0o600)
        self.mint_log = self.home / "mint.log"
        self.operation_log = self.home / "operation.log"
        self._write_fake_nebius()
        self._write_operation()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_fake_nebius(self) -> None:
        executable = self.bin_dir / "nebius"
        executable.write_text(
            """#!/usr/bin/env bash
set -eu
printf 'mint\\n' >> "$MINT_LOG"
count="$(wc -l < "$MINT_LOG" | tr -d ' ')"
case "${MINT_MODE:-ok}" in
  fail) exit 4 ;;
  empty) exit 0 ;;
esac
printf 'opaque-credential-%s\\n' "$count"
""",
            encoding="utf-8",
        )
        executable.chmod(0o700)

    def _write_operation(self) -> None:
        executable = self.bin_dir / "operation"
        executable.write_text(
            """#!/usr/bin/env bash
set -eu
test -n "${NEBIUS_IAM_TOKEN:-}"
test -z "${TOKEN:-}"
printf 'operation\\n' >> "$OPERATION_LOG"
count="$(wc -l < "$OPERATION_LOG" | tr -d ' ')"
case "${OPERATION_MODE:-success}" in
  non-auth) exit 42 ;;
  always-auth) exit 77 ;;
  auth-then-success) [ "$count" -gt 1 ] || exit 77 ;;
esac
""",
            encoding="utf-8",
        )
        executable.chmod(0o700)

    def environment(self, **extra: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.bin_dir}{os.pathsep}{environment['PATH']}",
                "NEBIUS_PROFILE": PROFILE,
                "NEBIUS_PROJECT_ID": PROJECT,
                "NEBIUS_AUTH_CREDENTIALS_FILE": str(self.credential),
                "MINT_LOG": str(self.mint_log),
                "OPERATION_LOG": str(self.operation_log),
                IAM_ENV: "stale",
                GENERIC_ENV: "stale",
            }
        )
        environment.update(extra)
        return environment

    def run_helper(
        self, operation: str, **extra: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(HELPER), operation, "--", str(self.bin_dir / "operation")],
            env=self.environment(**extra),
            text=True,
            capture_output=True,
            check=False,
        )

    def call_count(self, path: Path) -> int:
        return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0

    def test_exec_token_mints_once_and_never_outputs_the_credential(self) -> None:
        result = self.run_helper("exec-token")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.call_count(self.mint_log), 1)
        self.assertEqual(self.call_count(self.operation_log), 1)
        self.assertNotIn("opaque-credential", result.stdout)
        self.assertNotIn("opaque-credential", result.stderr)

    def test_retry_is_bounded_and_only_retries_status_77(self) -> None:
        cases = (
            ("non-auth", 42, 1, 1),
            ("always-auth", 77, 2, 2),
            ("auth-then-success", 0, 2, 2),
        )
        for mode, status, mints, operations in cases:
            with self.subTest(mode=mode):
                self.mint_log.unlink(missing_ok=True)
                self.operation_log.unlink(missing_ok=True)
                result = self.run_helper("retry-idempotent", OPERATION_MODE=mode)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(self.call_count(self.mint_log), mints)
                self.assertEqual(self.call_count(self.operation_log), operations)
                self.assertNotIn("opaque-credential", result.stdout)
                self.assertNotIn("opaque-credential", result.stderr)

    def test_mint_failure_and_invalid_context_fail_closed(self) -> None:
        for mode in ("fail", "empty"):
            with self.subTest(mode=mode):
                result = self.run_helper("retry-idempotent", MINT_MODE=mode)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.call_count(self.operation_log), 0)
                self.assertNotIn("opaque-credential", result.stderr)

        wrong_path = self.home / "wrong.json"
        result = subprocess.run(
            [str(HELPER), "exec-token", "--", str(self.bin_dir / "operation")],
            env=self.environment(NEBIUS_AUTH_CREDENTIALS_FILE=str(wrong_path)),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not canonical", result.stderr)

    def test_missing_child_returns_bounded_execution_error(self) -> None:
        result = subprocess.run(
            [str(HELPER), "exec-token", "--", "missing-child-command"],
            env=self.environment(),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 126)
        self.assertIn("cannot execute credential-scoped child", result.stderr)
        self.assertNotIn("opaque-credential", result.stderr)


if __name__ == "__main__":
    unittest.main()
