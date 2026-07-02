#!/usr/bin/env python3
"""Unit tests for query_audit_logs.py using a fake Nebius CLI."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import query_audit_logs as audit  # noqa: E402


FAKE_NEBIUS = r"""#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
log_path = os.environ.get("FAKE_NEBIUS_LOG")
if log_path:
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(args) + "\n")

def output(value):
    sys.stdout.write(value)
    if value and not value.endswith("\n"):
        sys.stdout.write("\n")

if args[:2] == ["config", "get"]:
    prop = args[2]
    values = {
        "tenant-id": os.environ.get("FAKE_TENANT_ID"),
        "parent-id": os.environ.get("FAKE_PARENT_ID"),
        "region": os.environ.get("FAKE_CONFIG_REGION"),
        "default-region": os.environ.get("FAKE_DEFAULT_REGION"),
    }
    value = values.get(prop)
    if value:
        output(value)
        raise SystemExit(0)
    raise SystemExit(1)

if args[:4] == ["iam", "v2", "project", "get"]:
    region = os.environ.get("FAKE_PROJECT_REGION", "")
    output(json.dumps({"metadata": {"id": "project-fake"}, "spec": {"region": region}}))
    raise SystemExit(0)

if args[:2] == ["iam", "whoami"]:
    kind = os.environ.get("FAKE_WHOAMI_KIND", "user")
    if kind == "service":
        payload = {
            "subject": {
                "service_account_id": "serviceaccount-current",
                "name": "ci-service-account@example.invalid",
            }
        }
    else:
        payload = {
            "subject": {
                "tenant_user_id": "tenantuseraccount-current",
                "name": "human@example.invalid",
            }
        }
    output(json.dumps(payload))
    raise SystemExit(0)

if args[:4] == ["audit", "v2", "audit-event", "list"]:
    payload = {
        "items": [
            {
                "id": "event-1",
                "time": "2026-07-02T11:59:00Z",
                "type": "ai.nebius.compute.computeinstance.delete",
                "service": {"name": "COMPUTE"},
                "action": "DELETE",
                "status": "DONE",
                "project_region": {"name": "eu-west1"},
                "authentication": {
                    "subject": {
                        "tenant_user_id": "tenantuseraccount-current",
                        "name": "human@example.invalid",
                    },
                    "token_credential": {"masked_token": "masked-token-placeholder"},
                },
                "authorization": {"authorized": True},
                "resource": {
                    "metadata": {
                        "id": "computeinstance-1",
                        "name": "instance-a",
                        "type": "computeinstance",
                    },
                    "state": {"response": {"body": "raw-response"}},
                },
            }
        ],
        "next_page_token": "next-token",
    }
    output(json.dumps(payload))
    raise SystemExit(0)

raise SystemExit(1)
"""


class QueryAuditLogsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self.temp_dir.name)
        fake_cli = temp_path / "nebius"
        fake_cli.write_text(FAKE_NEBIUS, encoding="utf-8")
        fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IXUSR)
        self.log_path = temp_path / "nebius-args.jsonl"
        self.base_env = {
            "PATH": f"{temp_path}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_NEBIUS_LOG": str(self.log_path),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def invoke(self, argv: list[str], extra_env: dict[str, str] | None = None) -> tuple[int, str, str]:
        env = dict(self.base_env)
        if extra_env:
            env.update(extra_env)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = audit.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def dry_run_query(self, argv: list[str], extra_env: dict[str, str] | None = None) -> dict[str, object]:
        code, stdout, stderr = self.invoke([*argv, "--dry-run", "--format", "json"], extra_env)
        self.assertEqual(code, 0, stderr)
        return json.loads(stdout)

    def test_explicit_resource_id_builds_resource_filter(self) -> None:
        payload = self.dry_run_query(
            [
                "--tenant-id",
                "tenant-1",
                "--region",
                "eu-west1",
                "--resource-id",
                "computeinstance-1",
                "--start",
                "2026-07-01T00:00:00Z",
                "--end",
                "2026-07-02T00:00:00Z",
            ]
        )
        self.assertEqual(payload["query"]["filter"], "resource.metadata.id='computeinstance-1'")

    def test_missing_resource_uses_current_tenant_user_subject(self) -> None:
        payload = self.dry_run_query(
            [
                "--tenant-id",
                "tenant-1",
                "--region",
                "eu-west1",
                "--start",
                "2026-07-01T00:00:00Z",
                "--end",
                "2026-07-02T00:00:00Z",
            ],
            {"FAKE_WHOAMI_KIND": "user"},
        )
        self.assertEqual(
            payload["query"]["filter"],
            "authentication.subject.tenant_user_id='tenantuseraccount-current'",
        )

    def test_missing_resource_uses_current_service_account_subject(self) -> None:
        payload = self.dry_run_query(
            [
                "--tenant-id",
                "tenant-1",
                "--region",
                "eu-west1",
                "--start",
                "2026-07-01T00:00:00Z",
                "--end",
                "2026-07-02T00:00:00Z",
            ],
            {"FAKE_WHOAMI_KIND": "service"},
        )
        self.assertEqual(
            payload["query"]["filter"],
            "authentication.subject.service_account_id='serviceaccount-current'",
        )

    def test_tenant_and_region_resolution_precedence(self) -> None:
        explicit = self.dry_run_query(
            [
                "--tenant-id",
                "tenant-explicit",
                "--region",
                "us-central1",
                "--resource-id",
                "computeinstance-1",
                "--start",
                "2026-07-01T00:00:00Z",
                "--end",
                "2026-07-02T00:00:00Z",
            ],
            {"FAKE_TENANT_ID": "tenant-config", "FAKE_PROJECT_REGION": "eu-west1"},
        )
        self.assertEqual(explicit["query"]["tenant_id"], "tenant-explicit")
        self.assertEqual(explicit["query"]["region"], "us-central1")

        discovered = self.dry_run_query(
            [
                "--resource-id",
                "computeinstance-1",
                "--start",
                "2026-07-01T00:00:00Z",
                "--end",
                "2026-07-02T00:00:00Z",
            ],
            {
                "FAKE_TENANT_ID": "tenant-config",
                "FAKE_PARENT_ID": "project-1",
                "FAKE_PROJECT_REGION": "eu-west1",
            },
        )
        self.assertEqual(discovered["query"]["tenant_id"], "tenant-config")
        self.assertEqual(discovered["query"]["region"], "eu-west1")

        fallback = self.dry_run_query(
            [
                "--resource-id",
                "computeinstance-1",
                "--start",
                "2026-07-01T00:00:00Z",
                "--end",
                "2026-07-02T00:00:00Z",
            ],
            {"FAKE_TENANT_ID": "tenant-config"},
        )
        self.assertEqual(fallback["query"]["region"], "eu-north1")

    def test_default_last_24_hours_utc_timestamps(self) -> None:
        payload = self.dry_run_query(
            [
                "--tenant-id",
                "tenant-1",
                "--region",
                "eu-west1",
                "--resource-id",
                "computeinstance-1",
            ],
            {"NEBIUS_AUDIT_LOG_NOW": "2026-07-02T12:00:00Z"},
        )
        self.assertEqual(payload["query"]["start"], "2026-07-01T12:00:00Z")
        self.assertEqual(payload["query"]["end"], "2026-07-02T12:00:00Z")

    def test_bounded_page_size_default(self) -> None:
        payload = self.dry_run_query(
            [
                "--tenant-id",
                "tenant-1",
                "--region",
                "eu-west1",
                "--resource-id",
                "computeinstance-1",
                "--start",
                "2026-07-01T00:00:00Z",
                "--end",
                "2026-07-02T00:00:00Z",
            ]
        )
        command = payload["command"]
        page_size_index = command.index("--page-size")
        self.assertEqual(command[page_size_index + 1], "100")

    def test_raw_payload_requires_raw_flag(self) -> None:
        code, stdout, stderr = self.invoke(
            [
                "--tenant-id",
                "tenant-1",
                "--region",
                "eu-west1",
                "--resource-id",
                "computeinstance-1",
                "--start",
                "2026-07-01T00:00:00Z",
                "--end",
                "2026-07-02T00:00:00Z",
                "--format",
                "json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertNotIn("token_credential", stdout)
        self.assertNotIn("raw-response", stdout)
        self.assertNotIn("instance-a", stdout)
        self.assertEqual(payload["events"][0]["resource"]["id"], "computeinstance-1")

        code, _stdout, stderr = self.invoke(
            [
                "--tenant-id",
                "tenant-1",
                "--region",
                "eu-west1",
                "--resource-id",
                "computeinstance-1",
                "--start",
                "2026-07-01T00:00:00Z",
                "--end",
                "2026-07-02T00:00:00Z",
                "--format",
                "yaml",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("requires --raw", stderr)

    def test_filter_composition_preserves_quoting_and_uses_and(self) -> None:
        payload = self.dry_run_query(
            [
                "--tenant-id",
                "tenant-1",
                "--region",
                "eu-west1",
                "--resource-id",
                "computeinstance-1",
                "--action",
                "DELETE",
                "--service",
                "COMPUTE",
                "--resource-type",
                "computeinstance",
                "--status",
                "DONE",
                "--raw-filter",
                "project_region.name='eu-west1'",
                "--start",
                "2026-07-01T00:00:00Z",
                "--end",
                "2026-07-02T00:00:00Z",
            ]
        )
        self.assertEqual(
            payload["query"]["filter"],
            "resource.metadata.id='computeinstance-1' AND "
            "action='DELETE' AND "
            "service.name='COMPUTE' AND "
            "resource.metadata.type='computeinstance' AND "
            "status='DONE' AND "
            "project_region.name='eu-west1'",
        )


if __name__ == "__main__":
    unittest.main()
