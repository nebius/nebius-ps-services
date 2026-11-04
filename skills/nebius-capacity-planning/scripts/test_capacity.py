#!/usr/bin/env python3
"""Offline observable arithmetic and malformed-input regression tests."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from calculate_capacity import InputError, calculate

ROOT = Path(__file__).resolve().parents[1]


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.model = json.loads((ROOT / "assets/example-capacity.json").read_text())

    def result(self):
        return calculate(self.model)["scenarios"][0]

    def resource(self, name):
        return self.result()["pools"][0]["resources"][name]

    def codes(self):
        return {issue["code"] for issue in self.result()["issues"]}

    def test_steady_totals_include_overhead_once(self):
        cpu = self.resource("cpu_m")
        self.assertEqual(cpu["usable_allocatable"], 8000)
        self.assertEqual(cpu["requests"], 5000)
        self.assertEqual(cpu["request_margin"], {"spare": 3000, "spare_percent": 37.5})
        self.assertEqual(cpu["runtime"], 7500)

    def test_unbounded_cpu_is_not_zero(self):
        self.assertIsNone(self.resource("cpu_m")["limits"])
        self.assertIsNone(self.resource("cpu_m")["limit_margin"]["spare"])

    def test_unassessed_runtime_is_not_zero(self):
        self.model["components"][0]["allocation"]["runtime"] = None
        self.assertIsNone(self.resource("memory_mib")["runtime"])

    def test_known_runtime_overload_survives_unknown_overhead(self):
        self.model["components"][0]["allocation"]["runtime"]["cpu_m"] = 2500
        self.model["pools"][0]["overhead"]["runtime"] = None
        self.assertIn("RUNTIME_EXCEED_CAPACITY", self.codes())
        cpu = self.resource("cpu_m")
        self.assertIsNone(cpu["runtime"])
        self.assertIsNone(cpu["runtime_margin"]["spare"])
        self.assertEqual(cpu["known_subtotals"]["runtime"], 10000)
        self.assertEqual(self.cli(json.dumps(self.model)).returncode, 1)

    def test_known_limits_overload_survives_unbounded_component(self):
        self.model["components"][0]["allocation"]["limits"]["cpu_m"] = 2500
        other = copy.deepcopy(self.model["components"][0])
        other["id"] = "unbounded"
        other["allocation"]["limits"]["cpu_m"] = None
        self.model["components"].append(other)
        self.model["scenarios"][0]["replicas"]["unbounded"] = 1
        self.assertIn("LIMITS_EXCEED_CAPACITY", self.codes())
        cpu = self.resource("cpu_m")
        self.assertIsNone(cpu["limits"])
        self.assertIsNone(cpu["limit_margin"]["spare"])
        self.assertEqual(cpu["known_subtotals"]["limits"], 12000)

    def test_unknown_terms_do_not_prove_overload_or_complete_totals(self):
        self.model["pools"][0]["overhead"]["runtime"] = None
        cpu = self.resource("cpu_m")
        self.assertIsNone(cpu["runtime"])
        self.assertEqual(cpu["known_subtotals"]["runtime"], 6000)
        self.assertNotIn("RUNTIME_EXCEED_CAPACITY", self.codes())
        self.assertNotIn("LIMITS_EXCEED_CAPACITY", self.codes())

    def test_inactive_unknown_runtime_does_not_add_demand(self):
        self.model["components"][0]["allocation"]["runtime"] = None
        self.model["scenarios"][0]["replicas"]["api"] = 0
        cpu = self.resource("cpu_m")
        self.assertEqual(cpu["runtime"], 1500)
        self.assertEqual(cpu["known_subtotals"]["runtime"], 1500)
        self.assertNotIn("RUNTIME_EXCEED_CAPACITY", self.codes())

    def test_inactive_unbounded_component_does_not_poison_total(self):
        self.model["scenarios"][0]["replicas"]["api"] = 0
        self.assertEqual(self.resource("cpu_m")["limits"], 2000)

    def test_node_loss_exposes_deficit(self):
        self.model["scenarios"][0]["nodes"]["core"]["unavailable"] = 1
        self.assertIn("REQUESTS_EXCEED_CAPACITY", self.codes())
        self.assertEqual(self.resource("cpu_m")["request_margin"]["spare"], -500)
        pool = self.result()["pools"][0]
        self.assertEqual((pool["provisioned_nodes"], pool["usable_nodes"]), (2, 1))

    def test_not_ready_surge_still_counts_provisioned(self):
        self.model["scenarios"][0]["nodes"]["core"].update(surge=1, unavailable=1)
        pool = self.result()["pools"][0]
        self.assertEqual((pool["provisioned_nodes"], pool["usable_nodes"]), (3, 2))
        self.assertEqual(self.resource("cpu_m")["requests"], 5000)

    def test_rollout_and_terminating_overlap_counts(self):
        self.model["scenarios"][0]["replicas"]["api"] = 8
        self.assertEqual(self.resource("cpu_m")["requests"], 9000)
        self.assertIn("REQUESTS_EXCEED_CAPACITY", self.codes())

    def test_aggregate_fit_does_not_hide_oversized_pod(self):
        self.model["components"][0]["allocation"]["requests"]["cpu_m"] = 3600
        self.model["scenarios"][0]["replicas"]["api"] = 1
        self.assertGreater(self.resource("cpu_m")["request_margin"]["spare"], 0)
        self.assertIn("POD_EXCEEDS_NODE", self.codes())

    def test_node_pod_density_includes_daemonsets(self):
        self.model["pools"][0]["pod_slots"] = 3
        self.assertIn("POD_SLOTS_EXCEEDED", self.codes())

    def test_memory_limits_can_exceed_capacity_when_requests_fit(self):
        self.model["components"][0]["allocation"]["limits"]["memory_mib"] = 8192
        self.assertGreater(self.resource("memory_mib")["request_margin"]["spare"], 0)
        self.assertIn("LIMITS_EXCEED_CAPACITY", self.codes())

    def test_zero_nodes_are_not_capacity(self):
        self.model["scenarios"][0]["nodes"]["core"]["base"] = 0
        self.assertIn("POD_SLOTS_EXCEEDED", self.codes())
        self.assertIsNone(self.resource("cpu_m")["request_margin"]["spare_percent"])

    def test_gpu_indivisibility(self):
        pool = self.model["pools"][0]
        pool["allocatable"]["gpus"] = 1
        component = self.model["components"][0]["allocation"]
        for kind in ("requests", "limits", "runtime"):
            component[kind]["gpus"] = 2
        self.model["scenarios"][0]["replicas"]["api"] = 1
        self.assertEqual(self.resource("gpus")["request_margin"]["spare"], 0)
        self.assertIn("POD_EXCEEDS_NODE", self.codes())

    def test_prederived_init_sidecar_overhead_budget_preserved(self):
        # Stage maximum already derived: 2500m init + 100m Pod overhead.
        self.model["components"][0]["allocation"]["requests"]["cpu_m"] = 2600
        self.model["scenarios"][0]["replicas"]["api"] = 1
        self.assertEqual(self.resource("cpu_m")["requests"], 3600)

    def test_storage_replication_and_independent_backup(self):
        budget = self.result()["budgets"][0]
        self.assertEqual((budget["demand"], budget["spare"]), (450, 50))

    def test_network_address_shortfall(self):
        self.model["scenarios"][0]["budgets"][1]["items"][0]["copies"] = 5
        self.assertIn("BUDGET_EXCEEDED", self.codes())

    def test_unknown_budget_does_not_mean_infinite(self):
        self.model["scenarios"][0]["budgets"][0]["capacity"] = None
        self.assertIsNone(self.result()["budgets"][0]["spare"])

    def test_arithmetic_never_proves_placement_or_cloud(self):
        output = calculate(self.model)
        self.assertEqual(output["status"], "arithmetic_clear")
        self.assertEqual(output["placement"], "not_evaluated")
        self.assertEqual(output["cloud_feasibility"], "not_evaluated")

    def test_scenarios_do_not_inherit_counts(self):
        self.model["scenarios"].append(copy.deepcopy(self.model["scenarios"][0]))
        self.model["scenarios"][1]["id"] = "incomplete"
        del self.model["scenarios"][1]["replicas"]["api"]
        with self.assertRaises(InputError):
            calculate(self.model)

    def test_bad_quantities(self):
        for value in [True, -1, 1.5, "1Gi", float("inf"), float("nan"), 10**16]:
            with self.subTest(type=type(value).__name__):
                self.setUp()
                self.model["components"][0]["allocation"]["requests"]["cpu_m"] = value
                with self.assertRaises(InputError):
                    calculate(self.model)

    def test_rejects_unknown_missing_duplicate_and_bad_references(self):
        mutations = [
            lambda m: m.update(extra=1),
            lambda m: m["pools"][0]["allocatable"].pop("gpus"),
            lambda m: m["components"].append(copy.deepcopy(m["components"][0])),
            lambda m: m["components"][0].update(pool="absent"),
            lambda m: m["scenarios"][0]["nodes"]["core"].update(unavailable=3),
            lambda m: m["pools"][0]["overhead"]["requests"].update(cpu_m=5000),
            lambda m: m["components"][0]["allocation"]["limits"].update(cpu_m=999),
            lambda m: m["components"][0]["allocation"]["limits"].update(gpus=1),
            lambda m: m["components"][0]["allocation"]["runtime"].update(memory_mib=9999),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                self.setUp()
                mutate(self.model)
                with self.assertRaises(InputError):
                    calculate(self.model)

    def cli(self, data, *args):
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/calculate_capacity.py"), *args],
            input=data, text=True, capture_output=True, timeout=10, check=False,
        )

    def test_cli_exit_codes_and_sanitized_errors(self):
        self.assertEqual(self.cli(json.dumps(self.model)).returncode, 0)
        self.model["scenarios"][0]["replicas"]["api"] = 8
        self.assertEqual(self.cli(json.dumps(self.model)).returncode, 1)
        for data in ['{"private-value": bad}', '{"a": 1, "a": 2}', 'NaN', '[' * 2000]:
            result = self.cli(data)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error"], "INVALID_INPUT")
            self.assertNotIn("private-value", result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_help_needs_no_input(self):
        result = self.cli("", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("JSON stdin", result.stdout)

    def test_input_size_bound(self):
        self.assertEqual(self.cli(" " * (2 * 1024 * 1024 + 1)).returncode, 2)


if __name__ == "__main__":
    unittest.main()
