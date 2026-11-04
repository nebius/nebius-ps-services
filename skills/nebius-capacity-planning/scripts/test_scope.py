#!/usr/bin/env python3
"""Offline selector behavior and tenant-inspector argument regressions."""

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

from resolve_scope import resolve_scope


class ScopeTests(unittest.TestCase):
    def test_no_scope_is_public_docs_in_all_regions(self):
        result = resolve_scope([])
        self.assertEqual(
            (result["region"], result["mode"], result["inspection"]),
            ("all", "design", "public-docs"),
        )
        self.assertEqual(result["quota_args"], [])
        self.assertEqual(result["capacity_args"], [])
        self.assertFalse(result["request_scope_ready"])

    def test_profile_does_not_select_tenant(self):
        result = resolve_scope(["--profile", "selected"])
        self.assertIsNone(result["tenant"])
        self.assertEqual(result["inspection"], "public-docs")

    def test_region_only_keeps_offline_assessment(self):
        result = resolve_scope(["--region", "region-a"])
        self.assertEqual(result["region"], "region-a")
        self.assertEqual(result["quota_args"], [])

    def test_tenant_implies_check_and_default_all_matches_explicit(self):
        base = ["--tenant", "tenant-example", "--profile", "human"]
        implicit = resolve_scope(base)
        self.assertEqual(implicit, resolve_scope([*base, "--region", "all"]))
        self.assertEqual(implicit, resolve_scope([*base, "--quota-check"]))
        self.assertEqual(implicit["mode"], "check")
        self.assertEqual(
            implicit["quota_args"],
            ["--tenant-id", "tenant-example", "--profile", "human", "--raw", "--json"],
        )
        self.assertNotIn("--region", implicit["capacity_args"])
        self.assertNotIn("--project-id", implicit["quota_args"])

    def test_exact_region_is_forwarded_to_both_sdk_inspectors(self):
        result = resolve_scope(["--tenant", "tenant-example", "--region", "region-a"])
        for key in ("quota_args", "capacity_args"):
            args = result[key]
            self.assertEqual(args[args.index("--region") + 1], "region-a")

    def test_request_scope_is_not_authority(self):
        base = ["--tenant", "tenant-example", "--quota-request"]
        self.assertFalse(resolve_scope(base)["request_scope_ready"])
        self.assertFalse(
            resolve_scope([*base, "--region", "all"])["request_scope_ready"]
        )
        exact = resolve_scope([*base, "--region", "region-a"])
        self.assertTrue(exact["request_scope_ready"])
        self.assertIn("not authorization", exact["boundary"])

    def test_invalid_invocations_fail_before_any_inspection(self):
        cases = [
            ["--project", "project-example"],
            ["--project=project-example"],
            ["--ten", "tenant-example"],
            ["--unknown"],
            ["--quota-check"],
            ["--profile", "human", "--quota-request"],
            ["--tenant", "tenant-example", "--quota-check", "--quota-request"],
            ["--tenant", "a", "--tenant", "a"],
            ["--region", "all", "--region", "region-a"],
            ["--profile", "a", "--profile", "b"],
            ["--tenant", "a", "--quota-check", "--quota-check"],
            ["--quota-multiplier", "2", "--quota-multiplier", "2"],
            ["--tenant", ""],
            ["--tenant", " x"],
            ["--tenant", "x\ny"],
            ["--region", "ALL"],
            ["--region", "a,b"],
            ["--region", "../../x"],
        ]
        for value in (
            "0",
            "-1",
            "NaN",
            "Infinity",
            "1e9999999999999999999999",
            "1e99",
            "x",
            "1" * 129,
        ):
            cases.append(["--quota-multiplier", value])
        for args in cases:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    resolve_scope(args)
                self.assertEqual(caught.exception.code, 2)

    def test_decimal_multiplier_is_not_rounded(self):
        self.assertEqual(
            resolve_scope(["--quota-multiplier", "1.0000000000000001"])["multiplier"],
            "1.0000000000000001",
        )

    def test_cli_needs_no_site_packages_or_credentials(self):
        script = Path(__file__).with_name("resolve_scope.py")
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(script),
                "--tenant",
                "tenant-example",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["region"], "all")


if __name__ == "__main__":
    unittest.main()
