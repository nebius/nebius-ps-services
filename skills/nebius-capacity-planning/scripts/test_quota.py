#!/usr/bin/env python3
"""Focused synthetic tests for reserve arithmetic and fail-closed candidates."""

import copy
import json
import subprocess
import sys
from pathlib import Path
import unittest
from plan_quota import plan, unique_object


def fixture():
    return {
        "multiplier": "2",
        "collection_complete": True,
        "dimensions": [
            {
                "scope": "tenant",
                "region": "example",
                "quota_name": "cpu",
                "unit": "count",
                "envelope": 176,
                "limit": 400,
                "usage": 80,
                "state": "STATE_ACTIVE",
                "usage_state": "USAGE_STATE_USED",
            }
        ],
    }


class QuotaTests(unittest.TestCase):
    def test_usage_is_not_multiplied(self):
        r = plan(fixture())
        d = r["dimensions"][0]
        self.assertEqual(
            (d["required_free"], d["minimum_total"], d["shortfall"]), (352, 432, 32)
        )
        self.assertEqual(r["request_candidates"][0]["requested_limit"], 432)

    def test_sufficient_never_reduced(self):
        x = fixture()
        x["dimensions"][0].update(limit=1000)
        r = plan(x)
        self.assertEqual(r["dimensions"][0]["increase"], 0)
        self.assertEqual(r["request_candidates"], [])

    def test_exact_fit(self):
        x = fixture()
        x["dimensions"][0]["limit"] = 432
        self.assertEqual(plan(x)["dimensions"][0]["status"], "sufficient")

    def test_decimal_and_increment(self):
        x = fixture()
        x["multiplier"] = "1.1"
        x["dimensions"][0].update(envelope=3, usage=2, limit=4, increment=4)
        d = plan(x)["dimensions"][0]
        self.assertEqual(
            (d["required_free"], d["minimum_total"], d["increase"]), (4, 8, 4)
        )

    def test_byte_conversion(self):
        x = fixture()
        g = 2**30
        x["dimensions"][0].update(
            unit="byte", envelope=1536 * g, usage=512 * g, limit=2048 * g, increment=g
        )
        self.assertEqual(plan(x)["request_candidates"][0]["requested_limit"], 3584 * g)

    def test_unknowns_do_not_request(self):
        for key, value in [
            ("usage", None),
            ("limit", None),
            ("state", "STATE_FROZEN"),
            ("usage_state", "USAGE_STATE_UNKNOWN"),
            ("usage_state", "USAGE_STATE_UNSPECIFIED"),
        ]:
            with self.subTest(key=key, value=value):
                x = fixture()
                x["dimensions"][0][key] = value
                r = plan(x)
                self.assertEqual(r["dimensions"][0]["status"], "unresolved")
                self.assertEqual(r["request_candidates"], [])

    def test_partial_keeps_deficit_without_submission(self):
        x = fixture()
        x["collection_complete"] = False
        r = plan(x)
        self.assertEqual(r["dimensions"][0]["shortfall"], 32)
        self.assertEqual(r["request_candidates"], [])

    def test_zero_limit_is_real(self):
        x = fixture()
        x["dimensions"][0].update(limit=0, usage=0, usage_state="USAGE_STATE_NOT_USED")
        self.assertEqual(plan(x)["dimensions"][0]["shortfall"], 352)

    def test_known_deficit_survives_unknown_dimension(self):
        x = fixture()
        row = copy.deepcopy(x["dimensions"][0])
        row.update(quota_name="storage", usage=None)
        x["dimensions"].append(row)
        self.assertEqual(len(plan(x)["request_candidates"]), 1)

    def test_duplicate_scope_key_rejected_even_with_different_unit(self):
        x = fixture()
        row = copy.deepcopy(x["dimensions"][0])
        row["unit"] = "byte"
        x["dimensions"].append(row)
        with self.assertRaises(ValueError):
            plan(x)

    def test_scopes_remain_separate(self):
        x = fixture()
        row = copy.deepcopy(x["dimensions"][0])
        row["scope"] = "project"
        x["dimensions"].append(row)
        self.assertEqual(len(plan(x)["request_candidates"]), 2)

    def test_invalid_multiplier(self):
        for value in [0, 0.5, -1, True, "NaN", "Infinity", "oops", "1e30"]:
            x = fixture()
            x["multiplier"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                plan(x)

    def test_invalid_provider_quantities(self):
        for key, value in [
            ("usage", -1),
            ("limit", True),
            ("envelope", 1.5),
            ("increment", 0),
            ("envelope", 2**63),
        ]:
            x = fixture()
            x["dimensions"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                plan(x)

    def test_inconsistent_not_used(self):
        x = fixture()
        x["dimensions"][0]["usage_state"] = "USAGE_STATE_NOT_USED"
        with self.assertRaises(ValueError):
            plan(x)

    def test_usage_above_limit(self):
        x = fixture()
        x["dimensions"][0].update(usage=410)
        self.assertEqual(plan(x)["dimensions"][0]["shortfall"], 362)

    def test_raw_json_decimal_preserves_ceiling(self):
        x = fixture()
        x["dimensions"][0].update(limit=176, usage=0)
        raw = json.dumps(x).replace(
            '"multiplier": "2"', '"multiplier": 1.0000000000000000001'
        )
        r = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).with_name("plan_quota.py"))],
            input=raw,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(json.loads(r.stdout)["dimensions"][0]["required_free"], 177)

    def assert_invalid_stdin(self, raw):
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).with_name("plan_quota.py"))],
            input=raw,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertIsInstance(json.loads(result.stderr)["error"], str)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_raw_json_extreme_decimal_exponent(self):
        raw = json.dumps(fixture()).replace(
            '"multiplier": "2"', '"multiplier": 1e999999999999999999999999'
        )
        self.assert_invalid_stdin(raw.encode("utf-8"))

    def test_stdin_limit_counts_utf8_bytes(self):
        raw = json.dumps({**fixture(), "padding": "é" * 600_000}, ensure_ascii=False)
        self.assertLess(len(raw), 1024 * 1024)
        self.assertGreater(len(raw.encode("utf-8")), 1024 * 1024)
        self.assert_invalid_stdin(raw.encode("utf-8"))

    def test_stdin_rejects_invalid_utf8(self):
        raw = json.dumps({**fixture(), "padding": "INVALID"}).encode("utf-8")
        self.assert_invalid_stdin(raw.replace(b"INVALID", b"\xff"))

    def test_stdin_rejects_excessive_nesting(self):
        self.assert_invalid_stdin(b"[" * 2000 + b"0" + b"]" * 2000)

    def test_stdin_accepts_valid_utf8_under_limit(self):
        x = fixture()
        x["dimensions"][0]["scope"] = "équipe"
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).with_name("plan_quota.py"))],
            input=json.dumps(x, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=5,
            check=True,
        )
        self.assertEqual(
            json.loads(result.stdout)["request_candidates"][0]["scope"], "équipe"
        )

    def test_duplicate_json_fields(self):
        with self.assertRaises(ValueError):
            unique_object([("x", 1), ("x", 2)])


if __name__ == "__main__":
    unittest.main()
