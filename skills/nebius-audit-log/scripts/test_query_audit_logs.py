#!/usr/bin/env python3
"""Offline behavioral and transport regressions; the real CLI is never used."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_cli
import query_audit_logs as audit
from audit_filters import parse_extra

TENANT = "tenant-example"
RESOURCE = "mk8scluster-example"
USER = "tenantuseraccount-example"
OTHER = "tenantuseraccount-other"
START = "2026-09-10T12:00:00Z"
END = "2026-09-11T12:00:00Z"
EVENT = {
    "id": "event-1",
    "time": START,
    "type": "ai.nebius.mk8s.cluster.delete",
    "source": "nebius.mk8s.v1.ClusterService/Delete",
    "action": "DELETE",
    "status": "DONE",
    "service": {"name": "MK8S"},
    "authentication": {
        "subject": {"tenant_user_id": OTHER, "name": "actor@example.invalid"},
        "token_credential": {"masked_token": "SECRET_FIXTURE_TOKEN"},
    },
    "authorization": {"authorized": True},
    "resource": {
        "metadata": {"id": RESOURCE, "type": "mk8scluster", "name": "PRIVATE_NAME"}
    },
    "request": {"request_id": "request-1", "parameters": {"secret": "REQUEST_SECRET"}},
    "response": {"status_code": "OK", "payload": {"secret": "RESPONSE_SECRET"}},
}
FAKE = r"""
import json, os, pathlib, subprocess, sys, time
args = sys.argv[1:]
root = pathlib.Path(os.environ["AUDIT_TEST_ROOT"])
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\n")
scenario = json.loads((root / "scenario.json").read_text())
if args[:2] == ["profile", "current"]:
    key = "profile"
elif args[:2] == ["config", "get"]:
    key = args[2]
elif args[:2] == ["iam", "whoami"]:
    key = "identity"
elif args[:4] == ["iam", "v2", "project", "get"]:
    key = "project"
elif args[:4] == ["audit", "v2", "audit-event", "list"]:
    counter = root / "page-number"
    n = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(n + 1))
    key = "pages"
else:
    raise SystemExit(2)
response = scenario[key][n] if key == "pages" else scenario[key]
if "child" in response:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    (root / "child.pid").write_text(str(child.pid))
    raise SystemExit(0)
if "close_sleep" in response:
    os.close(1); os.close(2)
    time.sleep(response["close_sleep"])
time.sleep(response.get("sleep", 0))
if "stdout" in response:
    sys.stdout.write(response["stdout"])
elif "bytes" in response:
    sys.stdout.write("X" * response["bytes"])
else:
    sys.stdout.write(json.dumps(response.get("payload", {})))
sys.stderr.write(response.get("stderr", ""))
raise SystemExit(response.get("exit", 0))
"""


class AuditTests(unittest.TestCase):
    def test_invalid_arguments_have_typed_safe_diagnostics(self):
        code, _ = self.run_query(self.base + ["--unknown", "SECRET_ARGUMENT"])
        self.assertEqual(code, 2)
        diagnostic = json.loads(self.error)["errors"][0]
        self.assertEqual(diagnostic["stage"], "arguments")
        self.assertEqual(diagnostic["code"], "invalid_arguments")
        self.assertNotIn("SECRET_ARGUMENT", self.error)

    def test_only_full_documented_flags_are_accepted(self):
        for arguments in (
            ["--current-subject", "--raw", "action='DELETE'"],
            ["--tenant-w"],
        ):
            with self.subTest(arguments=arguments):
                code, _ = self.run_query(arguments + ["--dry-run"])
                self.assertEqual(code, 2)
                diagnostic = json.loads(self.error)["errors"][0]
                self.assertEqual(diagnostic["code"], "invalid_arguments")
                self.assertEqual(self.calls, [])
        code, report = self.run_query(
            [
                "--tenant-wide",
                "--raw-filter",
                "action='DELETE'",
                "--dry-run",
                "--format",
                "json",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(report["scope"]["selector"]["kind"], "tenant_wide")
        self.assertEqual(self.calls, [])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="audit-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fake = self.root / "nebius"
        self.fake.write_text("#!" + sys.executable + "\n" + FAKE)
        self.fake.chmod(0o700)
        self.scenario = {
            "profile": {"stdout": "example\n"},
            "tenant-id": {"stdout": TENANT},
            "parent-id": {"stdout": "project-example"},
            "identity": {
                "payload": {
                    "user_profile": {
                        "id": "useraccount-example",
                        "tenants": [
                            {
                                "tenant_id": "tenant-other",
                                "tenant_user_account_id": OTHER,
                            },
                            {"tenant_id": TENANT, "tenant_user_account_id": USER},
                        ],
                    }
                }
            },
            "project": {
                "payload": {
                    "metadata": {"id": "project-example", "parent_id": TENANT},
                    "spec": {"region": "eu-north1"},
                }
            },
            "pages": [{"payload": {"items": [copy.deepcopy(EVENT)]}}],
        }
        self.env = mock.patch.dict(
            os.environ,
            {
                "NEBIUS_BIN": str(self.fake),
                "AUDIT_TEST_ROOT": str(self.root),
                "NEBIUS_AUDIT_LOG_NOW": END,
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.base = [
            "--resource-id",
            RESOURCE,
            "--tenant-id",
            TENANT,
            "--region",
            "eu-north1",
            "--format",
            "json",
        ]

    def run_query(self, arguments=None):
        (self.root / "scenario.json").write_text(json.dumps(self.scenario))
        for name in ("calls.jsonl", "page-number"):
            (self.root / name).unlink(missing_ok=True)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = audit.main(self.base if arguments is None else arguments)
        self.output, self.error = stdout.getvalue(), stderr.getvalue()
        self.calls = (
            [
                json.loads(line)
                for line in (self.root / "calls.jsonl").read_text().splitlines()
            ]
            if (self.root / "calls.jsonl").exists()
            else []
        )
        return code, json.loads(self.output) if self.output.startswith("{") else None

    def audit_calls(self):
        return [
            c for c in self.calls if c[:4] == ["audit", "v2", "audit-event", "list"]
        ]

    def test_identity_precedes_resource_query_and_reuses_page(self):
        code, report = self.run_query()
        self.assertEqual(
            [c[:2] for c in self.calls],
            [["profile", "current"], ["iam", "whoami"], ["audit", "v2"]],
        )
        self.assertEqual(code, 0)
        self.assertTrue(report["complete"])
        self.assertEqual(report["caller"]["id"], USER)
        self.assertEqual(report["access"], {"status": "verified", "verified_pages": 1})
        self.assertEqual(len(report["events"]), 1)
        self.assertEqual(len(self.audit_calls()), 1)
        for call in self.calls[1:]:
            self.assertEqual(call[call.index("--profile") + 1], "example")
            self.assertIn("--no-browser", call)
            self.assertEqual(call[call.index("--retries") + 1], "1")

    def test_profile_override_is_used_for_every_call(self):
        self.scenario["profile"]["stdout"] = "chosen"
        code, _ = self.run_query(self.base + ["--profile", "chosen"])
        self.assertEqual(code, 0)
        self.assertTrue(
            all(c[c.index("--profile") + 1] == "chosen" for c in self.calls)
        )

    def test_profile_mismatch_stops_before_identity(self):
        code, report = self.run_query(self.base + ["--profile", "chosen"])
        self.assertEqual(code, 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(report["errors"][0]["code"], "configuration_error")

    def test_service_account(self):
        self.scenario["identity"] = {
            "payload": {
                "service_account_profile": {
                    "info": {"metadata": {"id": "serviceaccount-example"}}
                }
            }
        }
        code, report = self.run_query()
        self.assertEqual(code, 0)
        self.assertEqual(
            report["caller"],
            {"kind": "service_account", "id": "serviceaccount-example"},
        )

    def test_unusable_identities_stop_before_audit(self):
        for payload in (
            {"anonymous_profile": {}},
            {},
            {"user_profile": {"tenants": []}},
            {"user_profile": {"tenants": "wrong"}},
            {"user_profile": {}, "service_account_profile": {}},
            {"service_account_profile": {"info": {"metadata": {"id": USER}}}},
            {
                "user_profile": {
                    "tenants": [{"tenant_id": TENANT, "tenant_user_account_id": USER}]
                    * 2
                }
            },
        ):
            with self.subTest(payload=payload):
                self.scenario["identity"] = {"payload": payload}
                code, report = self.run_query()
                self.assertEqual(code, 1)
                self.assertFalse(self.audit_calls())
                self.assertEqual(report["access"]["status"], "not_checked")

    def test_identity_authentication_and_permission_denials(self):
        for status, expected in (
            (7, "authentication_failed"),
            (15, "permission_denied"),
        ):
            self.scenario["identity"] = {"exit": status, "stderr": "SECRET_STDERR"}
            code, report = self.run_query()
            self.assertEqual(code, 1)
            self.assertEqual(report["errors"][0]["code"], expected)
            self.assertEqual(report["errors"][0]["stage"], "identity")
            self.assertFalse(self.audit_calls())
            self.assertNotIn("SECRET_STDERR", self.output + self.error)

    def test_audit_exit_codes_are_safe_and_distinct(self):
        for status, expected in (
            (7, "authentication_failed"),
            (15, "permission_denied"),
            (55, "permission_denied"),
            (12, "deadline_exceeded"),
            (52, "deadline_exceeded"),
            (20, "unavailable"),
            (60, "unavailable"),
            (4, "configuration_error"),
            (99, "cli_failed"),
        ):
            with self.subTest(status=status):
                self.scenario["pages"] = [{"exit": status, "stderr": "SECRET_STDERR"}]
                code, report = self.run_query()
                self.assertEqual(code, 1)
                self.assertEqual(report["errors"][0]["code"], expected)
                self.assertFalse(report["complete"])
                self.assertNotIn("SECRET_STDERR", self.output + self.error)
                self.assertEqual(report["events"], [])

    def test_canonical_empty_results(self):
        for payload in ({}, {"items": []}, {"items": [], "next_page_token": ""}):
            self.scenario["pages"] = [{"payload": payload}]
            code, report = self.run_query()
            self.assertEqual(code, 0)
            self.assertTrue(report["complete"])
            self.assertEqual(report["access"]["status"], "verified")
            self.assertEqual(report["events"], [])

    def test_invalid_responses_never_become_empty_success(self):
        invalid = [
            "",
            "not json",
            "[]",
            '{"items":null}',
            '{"events":[]}',
            '{"items":[1]}',
            '{"items":[{}]}',
            '{"next_page_token":null}',
            '{"items":[],"items":[]}',
            '{"items":NaN}',
        ]
        for stdout in invalid:
            with self.subTest(stdout=stdout):
                self.scenario["pages"] = [{"stdout": stdout}]
                code, report = self.run_query()
                self.assertEqual(code, 1)
                self.assertFalse(report["complete"])
                self.assertEqual(report["access"]["status"], "not_checked")
                self.assertEqual(report["errors"][0]["code"], "invalid_response")

    def test_region_discovery_checks_project_and_tenant(self):
        args = ["--resource-id", RESOURCE, "--format", "json"]
        code, report = self.run_query(args)
        self.assertEqual(code, 0)
        self.assertEqual(report["scope"]["region"], "eu-north1")
        for change in ("tenant", "project", "region", "denied"):
            with self.subTest(change=change):
                original = copy.deepcopy(self.scenario["project"])
                if change == "tenant":
                    self.scenario["project"]["payload"]["metadata"]["parent_id"] = (
                        "tenant-other"
                    )
                elif change == "project":
                    self.scenario["project"]["payload"]["metadata"]["id"] = (
                        "project-other"
                    )
                elif change == "region":
                    self.scenario["project"]["payload"]["spec"] = {}
                else:
                    self.scenario["project"] = {"exit": 15}
                code, report = self.run_query(args)
                self.assertEqual(code, 1)
                self.assertFalse(self.audit_calls())
                self.assertFalse(report["complete"])
                self.scenario["project"] = original

    def test_explicit_region_skips_project_discovery(self):
        self.scenario["project"] = {"exit": 15}
        code, _ = self.run_query()
        self.assertEqual(code, 0)
        self.assertFalse(
            any(c[:4] == ["iam", "v2", "project", "get"] for c in self.calls)
        )

    def test_missing_config_scope_stops(self):
        for key in ("tenant-id", "parent-id"):
            original = self.scenario[key]
            self.scenario[key] = {"stdout": ""}
            code, _ = self.run_query(["--tenant-wide", "--format", "json"])
            self.assertEqual(code, 1)
            self.assertFalse(self.audit_calls())
            self.scenario[key] = original

    def test_missing_selector_fails_before_cli(self):
        code, _ = self.run_query(["--tenant-id", TENANT, "--region", "eu-north1"])
        self.assertEqual(code, 2)
        self.assertEqual(self.calls, [])

    def test_empty_selectors_cannot_broaden_scope(self):
        for flag in ("--resource-id", "--subject-id"):
            code, _ = self.run_query([flag, "", *self.base[2:]])
            self.assertEqual(code, 2)
            self.assertEqual(self.calls, [])

    def test_empty_explicit_timestamps_are_invalid(self):
        for flag in ("--start", "--end"):
            code, _ = self.run_query([*self.base, flag, ""])
            self.assertEqual(code, 2)
            self.assertEqual(self.calls, [])

    def test_summary_shows_safe_scope_but_no_raw_literals(self):
        code, _ = self.run_query(
            self.base[:-2]
            + [
                "--dry-run",
                "--action",
                "DELETE",
                "--status",
                "DONE",
                "--raw-filter",
                "resource.metadata.name='SECRET_FILTER'",
            ]
        )
        self.assertEqual(code, 0)
        for value in (RESOURCE, "DELETE", "DONE", "resource.metadata.name"):
            self.assertIn(value, self.output)
        self.assertNotIn("SECRET_FILTER", self.output)

    def test_each_selector_has_exact_actor_behavior(self):
        for selector, expected in (
            (["--tenant-wide"], None),
            (["--current-subject"], f"authentication.subject.tenant_user_id='{USER}'"),
            (
                ["--subject-id", "serviceaccount-example"],
                "authentication.subject.service_account_id='serviceaccount-example'",
            ),
        ):
            code, _ = self.run_query(
                selector + self.base[2:] + ["--action", "DELETE", "--service", "MK8S"]
            )
            self.assertEqual(code, 0)
            call = self.audit_calls()[0]
            expression = call[call.index("--filter") + 1]
            self.assertIn("service.name='MK8S'", expression)
            if expected:
                self.assertIn(expected, expression)
            else:
                self.assertNotIn("authentication.subject", expression)

    def test_dry_run_is_offline_with_unresolved_configuration(self):
        with mock.patch.object(
            audit_cli.subprocess, "Popen", side_effect=AssertionError("must be offline")
        ):
            code, report = self.run_query(
                ["--current-subject", "--dry-run", "--format", "json"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(report["mode"], "dry_run")
        self.assertIsNone(report["scope"]["tenant_id"])
        self.assertIsNone(report["scope"]["region"])
        self.assertIsNone(report["caller"])
        self.assertEqual(report["access"]["status"], "not_checked")

    def test_filter_literals_never_render(self):
        expression = "authentication.subject.name='sensitive@example.invalid'"
        for suffix in (["--dry-run"], [], ["--include-pii"]):
            code, report = self.run_query(
                self.base + ["--raw-filter", expression] + suffix
            )
            self.assertEqual(code, 0)
            self.assertNotIn("sensitive@example.invalid", self.output + self.error)
            self.assertEqual(
                report["scope"]["extra_predicates"],
                [{"field": "authentication.subject.name", "operator": "="}],
            )
        self.scenario["pages"] = [{"exit": 3, "stderr": expression}]
        self.run_query(self.base + ["--raw-filter", expression])
        self.assertNotIn("sensitive@example.invalid", self.output + self.error)
        self.run_query(self.base + ["--raw-filter", expression + " OR action='DELETE'"])
        self.assertNotIn("sensitive@example.invalid", self.output + self.error)
        self.assertEqual(self.calls, [])

    def test_unsafe_or_unsupported_filters_fail_locally(self):
        for expression in (
            "action='DELETE' OR status='DONE'",
            "(action='DELETE')",
            "action='DELETE' --comment",
            "authentication.token_credential.masked_token='SECRET'",
            "action='DELETE' AND",
            "regex(action,'x') garbage",
            "action='DELETE';",
            "",
            "action='x'\nAND action='y'",
        ):
            code, _ = self.run_query(self.base + ["--raw-filter", expression])
            self.assertEqual(code, 2)
            self.assertEqual(self.calls, [])

    def test_quoted_and_regex_filters_preserve_conjunction(self):
        expression, fields = parse_extra(
            "regex(resource.metadata.name, '^a OR b$') AND status!='ERROR'"
        )
        self.assertIn("^a OR b$", expression)
        self.assertEqual(
            fields,
            [
                {"field": "resource.metadata.name", "operator": "regex"},
                {"field": "status", "operator": "!="},
            ],
        )

    def test_names_are_explicit_and_payloads_never_render(self):
        self.run_query()
        for value in (
            "SECRET_FIXTURE_TOKEN",
            "REQUEST_SECRET",
            "RESPONSE_SECRET",
            "PRIVATE_NAME",
            "actor@example.invalid",
        ):
            self.assertNotIn(value, self.output)
        _, report = self.run_query(self.base + ["--include-pii"])
        self.assertIn("actor@example.invalid", self.output)
        self.assertIn("PRIVATE_NAME", self.output)
        for value in ("SECRET_FIXTURE_TOKEN", "REQUEST_SECRET", "RESPONSE_SECRET"):
            self.assertNotIn(value, self.output)
        self.assertEqual(report["events"][0]["request_id"], "request-1")
        self.assertEqual(report["events"][0]["response_status_code"], "OK")

    def test_historical_actor_authorization_is_not_reader_permission(self):
        self.scenario["pages"][0]["payload"]["items"][0]["authorization"][
            "authorized"
        ] = False
        _, report = self.run_query()
        self.assertEqual(report["access"]["status"], "verified")
        self.assertFalse(report["events"][0]["event_authorized"])

    def test_page_limit_reports_resumable_partial(self):
        self.scenario["pages"][0]["payload"]["next_page_token"] = "page-2"
        code, report = self.run_query()
        self.assertEqual(code, 1)
        self.assertFalse(report["complete"])
        self.assertEqual(report["next_page_token"], "page-2")
        self.assertEqual(len(report["events"]), 1)
        self.assertEqual(report["scope"]["start"], START)
        self.assertEqual(report["errors"][0]["code"], "page_limit")

    def test_second_page_and_empty_page_with_token(self):
        self.scenario["pages"] = [
            {"payload": {"next_page_token": "page-2"}},
            {"payload": {"items": [EVENT]}},
        ]
        code, report = self.run_query(self.base + ["--max-pages", "2"])
        self.assertEqual(code, 0)
        self.assertTrue(report["complete"])
        self.assertEqual(len(report["events"]), 1)
        self.assertEqual(report["access"]["verified_pages"], 2)
        self.assertEqual(len(self.audit_calls()), 2)
        for call in self.audit_calls():
            self.assertEqual(call[call.index("--start") + 1], START)
            self.assertEqual(call[call.index("--end") + 1], END)

    def test_later_page_failure_retains_committed_pages_and_token(self):
        for response in ({"exit": 15}, {"stdout": "invalid"}):
            self.scenario["pages"] = [
                {"payload": {"items": [EVENT], "next_page_token": "page-2"}},
                response,
            ]
            code, report = self.run_query(self.base + ["--max-pages", "2"])
            self.assertEqual(code, 1)
            self.assertFalse(report["complete"])
            self.assertEqual(len(report["events"]), 1)
            self.assertEqual(report["next_page_token"], "page-2")
            self.assertEqual(report["access"]["verified_pages"], 1)

    def test_repeated_token_stops(self):
        self.scenario["pages"] = [
            {"payload": {"next_page_token": "loop"}},
            {"payload": {"next_page_token": "loop"}},
        ]
        code, report = self.run_query(self.base + ["--max-pages", "3"])
        self.assertEqual(code, 1)
        self.assertIsNone(report["next_page_token"])
        self.assertEqual(len(self.audit_calls()), 2)
        self.assertEqual(report["errors"][0]["code"], "pagination_cycle")

    def test_resume_requires_original_absolute_window(self):
        for suffix in (
            ["--page-token", "page-2"],
            ["--page-token", "page-2", "--start", START, "--end", END, "--hours", "24"],
        ):
            code, _ = self.run_query(self.base + suffix)
            self.assertEqual(code, 2)
            self.assertEqual(self.calls, [])
        code, _ = self.run_query(
            self.base + ["--page-token", "page-2", "--start", START, "--end", END]
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            self.audit_calls()[0][self.audit_calls()[0].index("--page-token") + 1],
            "page-2",
        )

    def test_invalid_arguments_fail_before_cli_without_values(self):
        for suffix in (
            ["--page-size", "501"],
            ["--max-pages", "0"],
            ["--max-pages", "101"],
            ["--timeout", "nan"],
            ["--timeout", "601"],
            ["--hours", "inf"],
            ["--hours", "1e300"],
            ["--action", "DELETE", "--action", "UPDATE"],
            ["--raw"],
            ["--all"],
            ["--format", "yaml"],
            ["--tenant-wide"],
            ["--start", "SECRET_INVALID_DATE"],
            ["--unknown", "SECRET_INVALID_ARG"],
        ):
            with self.subTest(suffix=suffix):
                code, _ = self.run_query(self.base + suffix)
                self.assertEqual(code, 2)
                self.assertEqual(self.calls, [])
                self.assertNotIn("SECRET_", self.output + self.error)

    def test_summary_exposes_status_and_safe_correlation(self):
        code, _ = self.run_query(self.base[:-2])
        self.assertEqual(code, 0)
        self.assertIn("MK8S DELETE DONE", self.output)
        self.assertIn("request=request-1 response=OK", self.output)
        self.assertNotIn("PRIVATE_NAME", self.output)

    def test_transport_timeout_and_closed_pipes(self):
        for response in ({"sleep": 1}, {"close_sleep": 1}, {"child": True}):
            self.scenario["profile"] = response
            before = time.monotonic()
            with mock.patch.object(audit_cli, "CALL_SECONDS", 0.15):
                code, report = self.run_query()
            self.assertEqual(code, 1)
            self.assertLess(time.monotonic() - before, 2)
            self.assertEqual(report["errors"][0]["code"], "deadline_exceeded")

    def test_transport_limits_count_stderr_and_total_bytes(self):
        for response in ({"bytes": 2048}, {"stderr": "X" * 2048}):
            self.scenario["profile"] = response
            with mock.patch.object(audit_cli, "CALL_BYTES", 1024):
                code, report = self.run_query()
            self.assertEqual(code, 1)
            self.assertEqual(report["errors"][0]["code"], "output_limit")
            self.assertNotIn("X" * 100, self.output)
        self.scenario["profile"] = {"stdout": "example"}
        with mock.patch.object(audit_cli, "TOTAL_BYTES", 32):
            code, report = self.run_query()
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "output_limit")

    def test_overall_budget_and_missing_executable(self):
        with mock.patch.object(audit_cli.time, "monotonic", side_effect=[0, 2]):
            cli = audit_cli.Cli(1)
            with self.assertRaises(audit_cli.AuditError) as ctx:
                cli.run(["profile", "current"], "configuration")
            self.assertEqual(ctx.exception.code, "deadline_exceeded")
        with mock.patch.dict(os.environ, {"NEBIUS_BIN": str(self.root / "missing")}):
            code, report = self.run_query()
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "cli_unavailable")


if __name__ == "__main__":
    unittest.main()
