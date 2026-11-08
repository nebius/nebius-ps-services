"""Behavioral regressions for the offline planner and evidence boundary."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tsc_support.contracts import (
    MAX_JSON_BYTES,
    PLAN,
    RESULTS,
    fingerprint,
    read_json,
    validate_plan,
    write_new,
)
from plan_matrix import expand
from check_evidence import markdown, summarize

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"


def profile(name="package-service"):
    return json.loads((ASSETS / (name + ".json")).read_text())


def receipt(suite, target="source"):
    inventory = suite["inventory"]
    return {
        "schema": "test-software-in-container.suite-result.v2",
        **{
            k: inventory[k]
            for k in ("suite_sha256", "input_sha256", "selection_sha256", "runner")
        },
        "inventory_sha256": inventory["sha256"],
        "completed": True,
        "exit_code": 0,
        "interrupted": False,
        "errors": [],
        "collected": inventory["selected"],
        "process_completed": True,
        "process_exit_code": 0,
        "collection_skips": [],
        "deselected": inventory["deselected"],
        "phase_seconds": dict(setup=0, call=0, teardown=0),
        "observed": [
            {
                "id": tid,
                "origin": {"kind": target, "reference": "frozen/test"},
                "phases": {
                    p: {"outcome": "passed", "seconds": 0}
                    for p in ("setup", "call", "teardown")
                },
            }
            for tid in inventory["selected"]
        ],
    }


def observations(plan):
    cases = []
    for case in plan["cases"]:
        checks = []
        for name, kind in case["checks"].items():
            obs = {
                "entrypoint_observed": True,
                "intervened": False,
                "probe_origin": "installed-artifact",
                "assertions_verified": True,
                "delta_verified": True,
                "invariants_preserved": True,
                "target_state_verified": True,
                "mutations": 0,
                "real_manager": True,
                "stable_window_observed": True,
                "boundary_observed": True,
                "product_owned": True,
            }
            if kind == "source":
                obs.update(
                    probe_origin="frozen-source",
                    restricted_access_verified=True,
                    source_sha256=case["inputs"]["source_sha256"],
                )
            if name.startswith("suite."):
                obs["receipt"] = receipt(
                    case["suites"][name[6:]], case["test_target"] or "installed"
                )
                obs["receipt"].update(case_id=case["id"], plan_sha256=plan["sha256"])
            checks.append(
                {
                    "id": name,
                    "status": "PASS",
                    "evidence": ["case/observations.json"],
                    "observation": obs,
                }
            )
        cases.append(
            {
                "id": case["id"],
                "baseline_key": case["baseline_key"],
                "platform": case["platform"],
                "image_digest": case["image_digest"],
                "inputs_sha256": fingerprint(case["inputs"]),
                "execution_mode": "native",
                "elapsed_seconds": 1,
                "phase_seconds": {
                    "preparation": 0.2,
                    "operation": 0.4,
                    "verification": 0.4,
                },
                "checks": checks,
            }
        )
    return {"schema": RESULTS, "plan_sha256": plan["sha256"], "cases": cases}


def reseal(plan):
    for case in plan["cases"]:
        case["id"] = fingerprint({k: v for k, v in case.items() if k != "id"})[:24]
    plan["sha256"] = fingerprint({k: v for k, v in plan.items() if k != "sha256"})
    return plan


class PlanningTests(unittest.TestCase):
    def test_explicit_install_needs_no_history(self):
        p = profile("install")
        result = expand(p, operations=["install"])
        self.assertEqual(result["selected_operations"], ["install"])
        self.assertEqual(len(result["cases"]), 1)
        self.assertEqual(result["cases"][0]["blockers"], [])

    def test_test_only_does_not_install(self):
        result = expand(
            profile("image-test"), operations=["test"], test_target="installed"
        )
        self.assertEqual(result["selected_operations"], ["test"])
        self.assertEqual(result["cases"][0]["path"], [])
        self.assertNotIn("artifact_sha256", result["cases"][0]["inputs"])

    def test_combined_modes_are_independent_and_deterministic(self):
        p = profile()
        a = expand(p, operations=["patch", "install", "upgrade", "install"])
        self.assertEqual(
            a, expand(copy.deepcopy(p), operations=["upgrade", "install", "patch"])
        )
        self.assertEqual(len(a["cases"]), 6)
        self.assertEqual(a["selected_operations"], ["install", "upgrade", "patch"])

    def test_all_tests_checks_operation_results_without_image_test(self):
        result = expand(profile(), all_tests=True)
        self.assertNotIn("test", result["selected_operations"])
        self.assertEqual(len(result["cases"]), 6)
        self.assertEqual(result["not_applicable"][0]["operation"], "migration")
        self.assertTrue(all("smoke" in case["checks"] for case in result["cases"]))

    def test_all_tests_includes_only_declared_image_targets(self):
        p = profile()
        other = profile("image-test")["environments"][0]
        for op in ["install", "upgrade", "patch", "migration"]:
            other["operations"][op] = {
                "not_applicable": "Only a preinstalled target in this environment."
            }
        p["environments"].append(other)
        plan = expand(p, all_tests=True)
        self.assertEqual(len([c for c in plan["cases"] if c["operation"] == "test"]), 1)
        self.assertEqual(len(plan["not_applicable"]), 6)
        validate_plan(plan)

    def test_missing_operation_not_silently_excluded(self):
        p = profile()
        del p["environments"][0]["operations"]["migration"]
        plan = expand(p, all_tests=True)
        case = next(c for c in plan["cases"] if c["operation"] == "migration")
        self.assertIn("UNDECLARED_OPERATION", case["blockers"])

    def test_explicit_non_applicable_operation_is_blocked(self):
        case = expand(profile(), operations=["migration"])["cases"][0]
        self.assertIn("EXPLICIT_OPERATION_NOT_APPLICABLE", case["blockers"])

    def test_all_na_has_no_vacuous_pass(self):
        p = profile("saved-state")
        p["environments"][0]["operations"]["migration"] = {
            "not_applicable": "No migration in this fixture."
        }
        plan = expand(p, all_tests=True)
        self.assertEqual(plan["cases"], [])
        self.assertEqual(
            summarize(plan, observations(plan))["claim_status"], "INCOMPLETE"
        )

    def test_conflicting_modes(self):
        with self.assertRaises(ValueError):
            expand(profile(), operations=["install"], all_tests=True)

    def test_irrelevant_upgrade_selectors(self):
        for kwargs in [
            {"past_releases": 1},
            {"from_releases": ["1.0.0"]},
            {"upgrade_path": "direct"},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                expand(profile("install"), **kwargs)

    def test_release_count_positive_and_selector_exclusive(self):
        for kwargs in [
            {"past_releases": 0},
            {"past_releases": -1},
            {"past_releases": True},
            {"past_releases": 1, "from_releases": ["1.0.0"]},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                expand(profile(), operations=["upgrade"], **kwargs)

    def test_upgrade_has_no_automatic_fresh_case(self):
        plan = expand(profile(), operations=["upgrade"])
        self.assertEqual(len(plan["cases"]), 4)
        self.assertTrue(all(c["operation"] == "upgrade" for c in plan["cases"]))

    def test_no_predecessors_remains_blocked(self):
        p = profile("install")
        plan = expand(p, operations=["upgrade"])
        self.assertIn("UNRESOLVED_UPGRADE_PATH", plan["cases"][0]["blockers"])
        self.assertEqual(plan["cases"][0]["path"], [])

    def test_missing_target_keeps_declared_upgrade_paths_blocked(self):
        for selection in [{"operations": ["upgrade"]}, {"all_tests": True}]:
            p = profile()
            p.pop("artifact")
            plan = expand(p, **selection)
            self.assertEqual(plan["selected_releases"], ["1.0.0", "1.1.0"])
            upgrades = [c for c in plan["cases"] if c["operation"] == "upgrade"]
            self.assertTrue(upgrades)
            for case in upgrades:
                self.assertIn("MISSING_INPUT", case["blockers"])
                self.assertIn("UNRESOLVED_UPGRADE_PATH", case["blockers"])
            if selection.get("all_tests"):
                patches = [c for c in plan["cases"] if c["operation"] == "patch"]
                self.assertEqual(len(patches), 1)
                self.assertEqual(patches[0]["blockers"], [])
            self.assertEqual(
                summarize(
                    plan,
                    {"schema": RESULTS, "plan_sha256": plan["sha256"], "cases": []},
                )["claim_status"],
                "INCOMPLETE",
            )

    def test_missing_target_does_not_hide_malformed_supported_paths(self):
        for paths in [[[]], [["1.0.0"]], [["1.0.0", 2]], [["1.0.0", "1.0.0"]]]:
            p = profile()
            p.pop("artifact")
            p["supported_paths"] = paths
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                expand(p, operations=["upgrade"])

    def test_authoritative_release_order_and_prerelease(self):
        p = profile()
        p["releases"][0]["stable"] = False
        self.assertEqual(
            expand(p, operations=["upgrade"])["selected_releases"], ["1.1.0"]
        )
        self.assertEqual(
            expand(p, operations=["upgrade"], from_releases=["1.1.0", "1.0.0"])[
                "selected_releases"
            ],
            ["1.0.0", "1.1.0"],
        )

    def test_unknown_or_duplicate_explicit_release(self):
        for versions in [["absent"], ["1.0.0", "1.0.0"], []]:
            with self.subTest(versions=versions), self.assertRaises(ValueError):
                expand(profile(), operations=["upgrade"], from_releases=versions)

    def test_default_four_predecessors(self):
        p = profile()
        prototype = p["releases"][0]
        p["releases"] = [
            dict(copy.deepcopy(prototype), version=str(i)) for i in range(6)
        ]
        p["supported_paths"] = [[str(i), "2.0.0"] for i in range(6)]
        self.assertEqual(
            expand(p, operations=["upgrade"])["selected_releases"], ["2", "3", "4", "5"]
        )

    def test_missing_artifact_retains_selected_release(self):
        p = profile()
        p["releases"][0]["artifacts"] = {}
        plan = expand(p, operations=["upgrade"])
        self.assertIn("1.0.0", plan["selected_releases"])
        self.assertIn("MISSING_INPUT", plan["cases"][0]["blockers"])

    def test_missing_baseline_blocks(self):
        p = profile()
        p["baselines"] = {}
        self.assertIn(
            "MISSING_VERIFIED_BASELINE",
            expand(p, operations=["upgrade"])["cases"][0]["blockers"],
        )

    def test_baseline_contamination_and_drift(self):
        for key, value, reason in [
            ("target_present", True, "TARGET_CONTAMINATION"),
            ("verified", False, "UNVERIFIED_BASELINE"),
            ("environment_sha256", "0" * 64, "BASELINE_ENVIRONMENT_MISMATCH"),
            ("installed_artifact_sha256", "0" * 64, "BASELINE_ARTIFACT_MISMATCH"),
        ]:
            p = profile()
            p["baselines"]["package-service"]["1.0.0"][key] = value
            with self.subTest(key=key):
                self.assertIn(
                    reason, expand(p, operations=["upgrade"])["cases"][0]["blockers"]
                )

    def test_dependency_change_changes_baseline_key(self):
        p = profile()
        a = expand(p, operations=["upgrade"])["cases"][0]["baseline_key"]
        p["baselines"]["package-service"]["1.0.0"]["dependencies_sha256"] = "0" * 64
        self.assertNotEqual(
            a, expand(p, operations=["upgrade"])["cases"][0]["baseline_key"]
        )

    def test_target_and_bundle_changes_preserve_baseline_but_change_plan(self):
        p = profile()
        a = expand(p, operations=["upgrade"])
        p["artifact"]["artifacts"]["linux/amd64"]["sha256"] = "0" * 64
        p["bundle_sha256"] = "1" * 64
        b = expand(p, operations=["upgrade"])
        self.assertEqual(a["cases"][0]["baseline_key"], b["cases"][0]["baseline_key"])
        self.assertNotEqual(a["sha256"], b["sha256"])

    def test_fixture_change_invalidates_cached_environment(self):
        p = profile()
        p["environments"][0]["fixture_sha256"] = "0" * 64
        self.assertIn(
            "BASELINE_ENVIRONMENT_MISMATCH",
            expand(p, operations=["upgrade"])["cases"][0]["blockers"],
        )

    def test_sequential_support_not_direct_support(self):
        p = profile()
        p["supported_paths"].remove(["1.0.0", "2.0.0"])
        self.assertIn(
            "UNSUPPORTED_DIRECT_PATH",
            expand(p, operations=["upgrade"])["cases"][0]["blockers"],
        )
        self.assertEqual(
            expand(p, operations=["upgrade"], upgrade_path="sequential")["cases"][0][
                "blockers"
            ],
            [],
        )

    def test_both_deduplicates_terminal_path(self):
        plan = expand(profile(), operations=["upgrade"], upgrade_path="both")
        self.assertEqual(len(plan["cases"]), 6)
        terminals = [c for c in plan["cases"] if c["path"][0] == "1.1.0"]
        self.assertEqual(len(terminals), 2)  # normal + fault, not two identical paths

    def test_backward_path_rejected(self):
        p = profile()
        p["supported_paths"].append(["1.1.0", "1.0.0", "2.0.0"])
        with self.assertRaises(ValueError):
            expand(p, operations=["upgrade"])

    def test_test_mode_requires_explicit_image(self):
        p = profile("image-test")
        del p["environments"][0]["image"]
        self.assertIn(
            "MISSING_EXPLICIT_IMAGE",
            expand(p, operations=["test"], test_target="installed")["cases"][0][
                "blockers"
            ],
        )

    def test_defaults_and_frozen_override_conflicts(self):
        p = profile("install")
        env = p["environments"][0]
        p["defaults"] = {"image": env.pop("image"), "platform": env.pop("platform")}
        self.assertEqual(
            expand(
                p, operations=["install"], image="ubuntu:24.04", platform="linux/amd64"
            )["cases"][0]["blockers"],
            [],
        )
        for kwargs in [{"image": "debian:bookworm"}, {"platform": "linux/arm64"}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                expand(p, operations=["install"], **kwargs)

    def test_pure_migration_has_no_fake_package_inputs(self):
        case = expand(profile("saved-state"), operations=["migration"])["cases"][0]
        self.assertEqual(case["blockers"], [])
        self.assertNotIn("installed", case["checks"])
        self.assertNotIn("artifact_sha256", case["inputs"])

    def test_patch_does_not_require_version_change(self):
        p = profile()
        del p["artifact"]
        del p["releases"]
        case = expand(p, operations=["patch"])["cases"][0]
        self.assertEqual(case["blockers"], [])
        self.assertIn("patch", case["checks"])
        self.assertEqual(case["path"], [])

    def test_missing_patch_payload_blocks(self):
        p = profile()
        del p["environments"][0]["operations"]["patch"]["inputs"]["payload_sha256"]
        self.assertIn(
            "MISSING_INPUT", expand(p, operations=["patch"])["cases"][0]["blockers"]
        )

    def test_state_baseline_mismatch_blocks(self):
        p = profile("saved-state")
        p["environments"][0]["operations"]["migration"]["baseline"]["state_sha256"] = (
            "0" * 64
        )
        self.assertIn(
            "BASELINE_STATE_MISMATCH",
            expand(p, operations=["migration"])["cases"][0]["blockers"],
        )

    def test_suites_opt_in_and_bound_to_digest(self):
        p = profile("image-test")
        self.assertEqual(
            expand(p, operations=["test"], test_target="installed")["cases"][0][
                "suites"
            ],
            {},
        )
        case = expand(p, operations=["test"], test_target="installed", suites=["api"])[
            "cases"
        ][0]
        self.assertEqual(case["checks"]["suite.api"], "suite")
        self.assertEqual(case["suites"]["api"]["sha256"], p["suites"]["api"]["sha256"])

    def test_unknown_or_incompatible_suite_rejected(self):
        for name in ["missing", "cli"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                expand(profile(), operations=["migration"], suites=[name])

    def test_reserved_ids_and_applicability_conflicts_rejected(self):
        for name in ["execution", "smoke", "interruption", "suite.fake"]:
            p = profile("install")
            p["environments"][0]["operations"]["install"]["checks"][name] = "packages"
            with self.subTest(name=name), self.assertRaises(ValueError):
                expand(p, operations=["install"])
        p = profile("install")
        p["environments"][0]["operations"]["install"]["exclusions"]["services"] = (
            "excluded"
        )
        with self.assertRaises(ValueError):
            expand(p, operations=["install"])

    def test_old_schema_rejected(self):
        p = profile("install")
        p["schema"] = "software-upgrade-test.profile.v1"
        with self.assertRaisesRegex(ValueError, "old skill"):
            expand(p, operations=["install"])

    def test_commands_are_not_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "must-not-exist"
            p = profile("install")
            p["environments"][0]["operations"]["install"]["entrypoint"] = [
                sys.executable,
                "-c",
                f"open({str(marker)!r},'w').close()",
            ]
            expand(p, operations=["install"])
            self.assertFalse(marker.exists())

    def test_generated_plan_size_bounded(self):
        p = profile("install")
        template = p["environments"][0]
        p["environments"] = [
            dict(copy.deepcopy(template), id=f"env-{i}") for i in range(5000)
        ]
        with self.assertRaisesRegex(ValueError, "4 MiB"):
            expand(p, operations=["install"])

    def test_case_count_bounded(self):
        p = profile("install")
        op = p["environments"][0]["operations"]["install"]
        del op["exclusions"]["recovery"]
        op["fault_points"] = [f"point-{i}" for i in range(10000)]
        with self.assertRaisesRegex(ValueError, "10000"):
            expand(p, operations=["install"])


class EvidenceTests(unittest.TestCase):
    def test_all_modes_summarize_supplied_observations(self):
        for name, modes in [
            ("install", ["install"]),
            ("image-test", ["test"]),
            ("package-service", ["upgrade", "patch"]),
            ("saved-state", ["migration"]),
        ]:
            with self.subTest(name=name):
                plan = expand(
                    profile(name),
                    operations=modes,
                    **({"test_target": "installed"} if modes == ["test"] else {}),
                )
                report = summarize(plan, observations(plan))
                self.assertEqual(report["claim_status"], "PASS")
                self.assertNotIn("functional qualification", report["excludes"])

    def test_missing_checks_and_cases_stay_incomplete(self):
        plan = expand(profile("install"), operations=["install"])
        for result in [
            {"schema": RESULTS, "plan_sha256": plan["sha256"], "cases": []},
            observations(plan),
        ]:
            if result["cases"]:
                result["cases"][0]["checks"].pop()
            self.assertEqual(summarize(plan, result)["claim_status"], "INCOMPLETE")

    def test_fail_propagates_across_operations(self):
        plan = expand(profile(), operations=["install", "patch"])
        result = observations(plan)
        result["cases"][1]["checks"][0]["status"] = "FAIL"
        report = summarize(plan, result)
        self.assertEqual(report["claim_status"], "FAIL")
        self.assertEqual(report["operations"]["install"]["status"], "PASS")

    def test_unknown_duplicate_results_and_checks_rejected(self):
        plan = expand(profile("install"), operations=["install"])
        for mutate in [
            lambda r: r["cases"].append(copy.deepcopy(r["cases"][0])),
            lambda r: r["cases"][0].update(id="unknown"),
            lambda r: r["cases"][0]["checks"].append(
                copy.deepcopy(r["cases"][0]["checks"][0])
            ),
            lambda r: r["cases"][0]["checks"][0].update(id="unknown"),
        ]:
            result = observations(plan)
            mutate(result)
            with self.assertRaises(ValueError):
                summarize(plan, result)

    def test_identity_drift_rejected(self):
        plan = expand(profile("install"), operations=["install"])
        for field in ["inputs_sha256", "baseline_key", "image_digest", "platform"]:
            result = observations(plan)
            result["cases"][0][field] = "different"
            with self.subTest(field=field), self.assertRaises(ValueError):
                summarize(plan, result)
        result = observations(plan)
        result["plan_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            summarize(plan, result)

    def test_required_observation_negative_controls(self):
        plan = expand(profile(), operations=["upgrade", "patch"])
        controls = {
            "execution": ("intervened", True),
            "installed": ("probe_origin", "source-tree"),
            "smoke": ("assertions_verified", False),
            "services": ("real_manager", False),
            "no_op": ("mutations", 1),
            "interruption": ("boundary_observed", False),
            "recovery": ("product_owned", False),
            "patch": ("delta_verified", False),
        }
        for kind, (field, value) in controls.items():
            result = observations(plan)
            index = next(
                i for i, c in enumerate(plan["cases"]) if kind in c["checks"].values()
            )
            name = next(
                n for n, k in plan["cases"][index]["checks"].items() if k == kind
            )
            next(c for c in result["cases"][index]["checks"] if c["id"] == name)[
                "observation"
            ][field] = value
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                summarize(plan, result)

    def test_no_op_boolean_is_not_zero(self):
        plan = expand(profile("install"), operations=["install"])
        result = observations(plan)
        next(c for c in result["cases"][0]["checks"] if c["id"] == "unchanged")[
            "observation"
        ]["mutations"] = False
        with self.assertRaises(ValueError):
            summarize(plan, result)

    def test_migration_and_suite_assertions_required(self):
        plan = expand(profile("saved-state"), operations=["migration"])
        result = observations(plan)
        next(c for c in result["cases"][0]["checks"] if c["id"] == "migration")[
            "observation"
        ]["invariants_preserved"] = False
        with self.assertRaises(ValueError):
            summarize(plan, result)
        plan = expand(
            profile("image-test"),
            operations=["test"],
            test_target="installed",
            suites=["api"],
        )
        result = observations(plan)
        next(c for c in result["cases"][0]["checks"] if c["id"] == "suite.api")[
            "observation"
        ]["receipt"]["suite_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            summarize(plan, result)

    def test_blocked_case_cannot_claim_pass(self):
        p = profile("install")
        del p["artifact"]
        plan = expand(p, operations=["install"])
        with self.assertRaises(ValueError):
            summarize(plan, observations(plan))

    def test_not_run_cannot_have_executed_assertions(self):
        plan = expand(profile("install"), operations=["install"])
        result = observations(plan)
        result["cases"][0]["execution_mode"] = "not-run"
        with self.assertRaises(ValueError):
            summarize(plan, result)

    def test_bad_timing_and_phase_rejected(self):
        plan = expand(profile("install"), operations=["install"])
        for value in [-1, True, float("inf"), 10**1000]:
            result = observations(plan)
            result["cases"][0]["elapsed_seconds"] = value
            with (
                self.subTest(value_type=type(value).__name__),
                self.assertRaises(ValueError),
            ):
                summarize(plan, result)
        result = observations(plan)
        result["cases"][0]["phase_seconds"] = {"secret-phase": 1}
        with self.assertRaises(ValueError):
            summarize(plan, result)

    def test_evidence_paths_bounded_and_contained(self):
        plan = expand(profile("install"), operations=["install"])
        for value in [
            [],
            ["../escape"],
            ["/absolute"],
            ["https://example.invalid/"],
            ["a\\b"],
        ]:
            result = observations(plan)
            result["cases"][0]["checks"][0]["evidence"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                summarize(plan, result)

    def test_resealed_plan_cannot_drop_required_checks(self):
        for operation, asset in [
            ("install", "install"),
            ("test", "image-test"),
            ("patch", "package-service"),
            ("migration", "saved-state"),
        ]:
            plan = expand(
                profile(asset),
                operations=[operation],
                **({"test_target": "installed"} if operation == "test" else {}),
            )
            plan["cases"][0]["checks"].pop("smoke")
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                validate_plan(reseal(plan))

    def test_resealed_operation_change_rejected(self):
        plan = expand(profile("saved-state"), operations=["migration"])
        plan["cases"][0]["operation"] = "patch"
        plan["selected_operations"] = ["patch"]
        with self.assertRaises(ValueError):
            validate_plan(reseal(plan))

    def test_resealed_plan_cannot_hide_mode_or_suite(self):
        plan = expand(profile(), operations=["install", "patch"])
        plan["cases"].pop()
        with self.assertRaises(ValueError):
            validate_plan(reseal(plan))
        plan = expand(
            profile("image-test"),
            operations=["test"],
            test_target="installed",
            suites=["api"],
        )
        plan["cases"][0]["suites"] = {}
        plan["cases"][0]["checks"].pop("suite.api")
        with self.assertRaises(ValueError):
            validate_plan(reseal(plan))

    def test_plan_hash_and_path_order(self):
        plan = expand(profile(), operations=["upgrade"])
        plan["cases"][0]["image"] = "tampered"
        with self.assertRaises(ValueError):
            validate_plan(plan)
        plan = expand(profile(), operations=["upgrade"])
        plan["release_order"].reverse()
        with self.assertRaises(ValueError):
            validate_plan(reseal(plan))

    def test_resealed_plan_cannot_drop_predecessor_path_or_trials(self):
        selectors = [
            lambda c: c["path"][0] != "1.0.0",
            lambda c: c["fault_point"] is None,
            lambda c: c["fault_point"] is not None,
            lambda c: c["path_kind"] != "sequential",
        ]
        for keep in selectors:
            plan = expand(profile(), operations=["upgrade"], upgrade_path="both")
            plan["cases"] = [c for c in plan["cases"] if keep(c)]
            with self.subTest(keep=keep), self.assertRaises(ValueError):
                summarize(reseal(plan), observations(plan))

    def test_resealed_plan_cannot_drop_declared_predecessor(self):
        plan = expand(profile(), operations=["upgrade"])
        plan["cases"] = [c for c in plan["cases"] if c["path"][0] != "1.0.0"]
        plan["upgrade_paths"] = [
            p for p in plan["upgrade_paths"] if p["path"][0] != "1.0.0"
        ]
        with self.assertRaises(ValueError):
            validate_plan(reseal(plan))

    def test_markdown_all_summary_rows_precede_details(self):
        plan = expand(profile(), operations=["upgrade"])
        report = summarize(plan, observations(plan))
        text = markdown(report)
        first_detail = text.index("### Case ")
        for case in report["cases"]:
            self.assertLess(text.index("| " + case["id"] + " |"), first_detail)

    def test_markdown_injection_is_literal(self):
        p = profile("install")
        p["environments"][0]["operations"]["install"]["exclusions"]["recovery"] = (
            "![x](https://example.invalid/track)<img>"
        )
        plan = expand(p, operations=["install"])
        text = markdown(summarize(plan, observations(plan)))
        self.assertNotIn("![x](", text)
        self.assertNotIn("<img>", text)
        self.assertIn("&lt;img&gt;", text)
        self.assertIn("preparation=0.2", text)


class IOTests(unittest.TestCase):
    def test_new_private_output_no_overwrite_or_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report"
            write_new(path, "first")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                write_new(path, "second")
            link = Path(tmp) / "link"
            link.symlink_to(path)
            with self.assertRaises(FileExistsError):
                write_new(link, "second")
            self.assertEqual(path.read_text(), "first")

    def test_bounded_json_duplicate_and_nonfinite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            for content in ['{"a":1,"a":2}', '{"a":NaN}', " " * (MAX_JSON_BYTES + 1)]:
                path.write_text(content)
                with self.assertRaises(ValueError):
                    read_json(path)

    def call(self, script, *args):
        return subprocess.run(
            [sys.executable, "-B", str(HERE / script), *map(str, args)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_cli_help_modes_and_old_flags(self):
        result = self.call("plan_matrix.py", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--all-tests", result.stdout)
        for flag in ["--base-os", "--candidate", "--matrix", "--runtime"]:
            result = self.call(
                "plan_matrix.py", "--profile", ASSETS / "install.json", flag, "x"
            )
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)

    def test_cli_expansion_and_incomplete_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_path, result_path = Path(tmp) / "plan.json", Path(tmp) / "result.json"
            result = self.call(
                "plan_matrix.py",
                "--profile",
                ASSETS / "install.json",
                "--install",
                "--output",
                plan_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = read_json(plan_path)
            result_path.write_text(
                json.dumps(
                    {"schema": RESULTS, "plan_sha256": plan["sha256"], "cases": []}
                )
            )
            result = self.call(
                "check_evidence.py", plan_path, result_path, "--format", "markdown"
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("INCOMPLETE", result.stdout)

    def test_cli_bad_selector_and_malformed_json_no_traceback(self):
        result = self.call(
            "plan_matrix.py",
            "--profile",
            ASSETS / "install.json",
            "--past-releases",
            "1",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("require upgrade", result.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps({"schema": PLAN, "sha256": "0" * 64}))
            result = self.call("check_evidence.py", path, path)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)

    def test_cli_huge_integer_timing_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = expand(profile("install"), operations=["install"])
            result = observations(plan)
            result["cases"][0]["elapsed_seconds"] = 10**1000
            pp, rp = Path(tmp) / "plan.json", Path(tmp) / "results.json"
            pp.write_text(json.dumps(plan))
            rp.write_text(json.dumps(result))
            output = self.call("check_evidence.py", pp, rp)
            self.assertEqual(output.returncode, 2)
            self.assertNotIn("Traceback", output.stderr)


if __name__ == "__main__":
    unittest.main()
