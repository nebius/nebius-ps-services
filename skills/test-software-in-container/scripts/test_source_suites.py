"""Source defaults, static discovery, metadata and outcome accounting regressions."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from check_evidence import summarize
from tsc_support.contracts import seal, validate_plan
from plan_matrix import expand
from project_context import discover, list_suites, project_path
from source_manifest import snapshot
from tsc_support.suite_contracts import assess_receipt
from test_helpers import observations, profile, receipt, reseal


class SourcePlanningTests(unittest.TestCase):
    def test_all_tests_suites_apply_to_cases_not_na_operations(self):
        plan = expand(profile(), all_tests=True, suites=["cli"])
        self.assertNotIn("migration", {c["operation"] for c in plan["cases"]})
        self.assertTrue(all("suite.cli" in c["checks"] for c in plan["cases"]))

    def test_malformed_operation_object_fails_cleanly(self):
        p = profile("source-test")
        p["environments"][0]["operations"]["test"] = None
        with self.assertRaises(ValueError):
            expand(p, all_tests=True)

    def test_bare_invocation_source_and_auto(self):
        plan = expand(profile("source-test"))
        self.assertEqual(plan["selected_operations"], ["test"])
        self.assertEqual(plan["test_target"], "source")
        self.assertEqual(plan["selected_suites"], ["integration"])
        case = plan["cases"][0]
        self.assertEqual(case["blockers"], [])
        self.assertEqual(
            set(case["checks"]), {"execution", "source", "suite.integration"}
        )
        self.assertEqual(summarize(plan, observations(plan))["claim_status"], "PASS")

    def test_source_and_lifecycle_cannot_mix(self):
        for kwargs in (
            {"operations": ["test", "install"]},
            {"all_tests": True, "test_target": "source"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                expand(profile("source-test"), **kwargs)

    def test_combined_installed_target_is_explicit(self):
        p = profile("install")
        p["environments"][0]["operations"]["test"] = profile("image-test")[
            "environments"
        ][0]["operations"]["test"]
        plan = expand(p, operations=["test", "install"], test_target="installed")
        self.assertEqual(len(plan["cases"]), 2)

    def test_source_caches_bind_dependencies_harness_and_source(self):
        p = profile("source-test")
        before = expand(p)["cases"][0]["baseline_key"]
        for key in p["environments"][0]["operations"]["test"]["inputs"]:
            other = copy.deepcopy(p)
            other["environments"][0]["operations"]["test"]["inputs"][key] = "0" * 64
            self.assertNotEqual(before, expand(other)["cases"][0]["baseline_key"])

    def test_source_missing_image_selector_allowed_frozen_identity_required(self):
        p = profile("source-test")
        del p["environments"][0]["image"]
        self.assertEqual(expand(p)["cases"][0]["image"], "ubuntu:24.04")
        del p["environments"][0]["platform"]
        with self.assertRaises(ValueError):
            expand(p)  # Agent must first observe the engine platform, never guess here.

    def test_source_origin_must_match_snapshot(self):
        p = expand(profile("source-test"))
        result = observations(p)
        source = next(c for c in result["cases"][0]["checks"] if c["id"] == "source")
        source["observation"]["source_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            summarize(p, result)

    def test_empty_ambiguous_invalid_defaults_fail(self):
        for defaults in (
            {},
            {"source": []},
            {"source": ["missing"]},
            {"source": ["auto"]},
        ):
            p = profile("source-test")
            p["default_suites"] = defaults
            with self.subTest(defaults=defaults), self.assertRaises(ValueError):
                expand(p)

    def test_explicit_suites_replace_auto_and_incompatibilities_fail(self):
        p = profile("source-test")
        p["default_suites"]["source"] = []
        self.assertEqual(
            expand(p, suites=["integration"])["selected_suites"], ["integration"]
        )
        for suites in (["auto", "integration"], ["integration", "integration"], []):
            with self.subTest(suites=suites), self.assertRaises(ValueError):
                expand(p, suites=suites)
        p["suites"]["integration"]["test_targets"] = ["installed"]
        with self.assertRaises(ValueError):
            expand(p, suites=["integration"])

    def test_uncollected_inventory_blocks_not_fake_pass(self):
        p = profile("source-test")
        del p["suites"]["integration"]["inventory"]
        plan = expand(p)
        self.assertIn("MISSING_INVENTORY", plan["cases"][0]["blockers"])

    def test_resealed_plan_cannot_drop_source_or_target(self):
        plan = expand(profile("source-test"))
        del plan["cases"][0]["checks"]["source"]
        with self.assertRaises(ValueError):
            validate_plan(reseal(plan))


class DiscoveryTests(unittest.TestCase):
    def test_exact_cwd_no_ancestor_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "child"
            child.mkdir()
            (root / "container-tests.json").write_text(
                json.dumps(profile("source-test"))
            )
            saved = Path.cwd()
            try:
                os.chdir(child)
                selected, config = discover()
                self.assertEqual(selected, child.resolve())
                self.assertIsNone(config)
            finally:
                os.chdir(saved)

    def test_external_profile_keeps_project_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            external = root / "external.json"
            external.write_text(json.dumps(profile("source-test")))
            selected, config = discover(project, external)
            self.assertEqual(selected, project.resolve())
            self.assertEqual(
                project_path(selected, "fixtures/input"), selected / "fixtures/input"
            )
            self.assertEqual(list_suites(config)[0]["default_for"], ["source"])

    def test_bad_present_profile_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "container-tests.json"
            for text in ("bad", '{"schema":"test-software-in-container.profile.v1"}'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    discover(root)
            path.unlink()
            path.symlink_to(root / "missing")
            with self.assertRaises(OSError):
                discover(root)

    def test_listing_never_imports_or_executes_entrypoints(self):
        p = profile("source-test")
        p["suites"]["integration"]["entrypoint"] = ["must-never-execute"]
        self.assertEqual(list_suites(p, "source")[0]["name"], "integration")
        self.assertEqual(list_suites(p, "installed"), [])


class ManifestTests(unittest.TestCase):
    def test_canceled_directory_and_link_chain_modes_are_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "transit").mkdir(mode=0o755)
            (root / "file").write_text("content")
            (root / "middle").symlink_to("transit/../file")
            (root / "entry").symlink_to("middle")
            first = snapshot(root, ["entry"])
            self.assertIn("transit", {e["path"] for e in first["entries"]})
            (root / "transit").chmod(0o700)
            self.assertNotEqual(first["sha256"], snapshot(root, ["entry"])["sha256"])

    def test_symlink_parent_before_dotdot_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "nested").mkdir()
            (root / "nested" / "dir").mkdir()
            (root / "file").write_text("wrong")
            (root / "nested" / "file").write_text("actual")
            (root / "link").symlink_to("nested/dir")
            (root / "entry").symlink_to("link/../file")
            with self.assertRaisesRegex(ValueError, "symlink parent"):
                snapshot(root, ["entry"])

    def test_directory_modes_symlink_targets_and_dirty_bytes_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "src"
            folder.mkdir(mode=0o755)
            file = folder / "module.py"
            file.write_text("x=1")
            (root / "current").symlink_to("src/module.py")
            first = snapshot(root, ["current"])
            self.assertEqual(
                {e["path"] for e in first["entries"]},
                {".", "src", "src/module.py", "current"},
            )
            folder.chmod(0o700)
            second = snapshot(root, ["current"])
            self.assertNotEqual(first["sha256"], second["sha256"])
            file.write_text("x=2")
            self.assertNotEqual(second["sha256"], snapshot(root, ["current"])["sha256"])

    def test_only_reviewed_files_and_contained_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "unreviewed").write_text("do not include")
            self.assertEqual(len(snapshot(root, ["."])["entries"]), 1)
            (root / "escape").symlink_to("../outside")
            with self.assertRaises((ValueError, OSError)):
                snapshot(root, ["escape"])


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.profile = profile("source-test")
        self.suite = self.profile["suites"]["integration"]
        self.result = receipt(self.suite)

    def assess(self):
        return assess_receipt(
            self.suite, self.result, "source", self.profile["bundle_sha256"]
        )

    def test_neutral_non_pytest_receipt(self):
        self.assertEqual(self.assess()["status"], "PASS")
        self.assertEqual(self.assess()["counts"]["passed"], 1)

    @unittest.skipUnless(os.name == "posix", "POSIX signal return-code semantics")
    def test_observed_signal_returncode_retains_interruption_and_failures(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            child.terminate()
            code = child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        self.assertLess(code, 0)
        self.result.update(
            interrupted=True,
            completed=False,
            exit_code=None,
            process_completed=True,
            process_exit_code=code,
        )
        self.assertEqual(self.assess()["status"], "INCOMPLETE")
        self.result["observed"][0]["phases"]["call"].update(
            outcome="failed", reason="earlier assertion"
        )
        self.assertEqual(self.assess()["status"], "FAIL")

    def test_assertion_and_setup_teardown_errors_fail(self):
        for phase in ("setup", "call", "teardown"):
            r = copy.deepcopy(self.result)
            self.result["observed"][0]["phases"][phase].update(
                outcome="failed", reason="assertion"
            )
            self.assertEqual(self.assess()["status"], "FAIL")
            self.result = r

    def test_incomplete_matrix(self):
        for mutation in (
            lambda r: r.update(completed=False, exit_code=None),
            lambda r: r.update(interrupted=True, exit_code=2),
            lambda r: r.update(observed=[]),
            lambda r: r["observed"][0]["phases"].pop("teardown"),
            lambda r: r["observed"][0]["phases"]["call"].update(
                outcome="skipped", reason="missing dependency"
            ),
        ):
            saved = copy.deepcopy(self.result)
            mutation(self.result)
            self.assertEqual(self.assess()["status"], "INCOMPLETE")
            self.result = saved

    def test_duplicates_unexpected_and_errors_fail(self):
        for mutation in (
            lambda r: r["observed"].append(copy.deepcopy(r["observed"][0])),
            lambda r: r["observed"][0].update(id="unknown"),
            lambda r: r["errors"].append(
                {"phase": "collection", "reason": "import error"}
            ),
            lambda r: r.update(exit_code=3),
        ):
            saved = copy.deepcopy(self.result)
            mutation(self.result)
            self.assertEqual(self.assess()["status"], "FAIL")
            self.result = saved

    def test_expected_failure_requires_exact_phase_reason_and_failure(self):
        self.suite["exemptions"] = [
            {
                "id": "integration::works",
                "phase": "call",
                "outcome": "xfail",
                "reason": "known",
                "failure_sha256": "a" * 64,
            }
        ]
        self.result["observed"][0]["phases"]["call"].update(
            outcome="xfail", reason="known", failure_sha256="b" * 64
        )
        self.assertEqual(self.assess()["status"], "FAIL")
        self.result["observed"][0]["phases"]["call"]["failure_sha256"] = "a" * 64
        self.assertEqual(self.assess()["status"], "INCOMPLETE")  # No ordinary pass.
        self.result["observed"][0]["phases"]["call"]["outcome"] = "xpass"
        self.assertEqual(self.assess()["status"], "FAIL")

    def test_empty_collection_never_passes(self):
        inv = self.suite["inventory"]
        inv["selected"] = []
        self.suite["inventory"] = seal({k: v for k, v in inv.items() if k != "sha256"})
        self.result = receipt(self.suite)
        self.assertEqual(self.assess()["status"], "INCOMPLETE")

    def test_runner_selection_bundle_and_origin_mismatches_rejected(self):
        for field in ("runner", "selection_sha256", "input_sha256", "inventory_sha256"):
            saved = copy.deepcopy(self.result)
            self.result[field] = "different"
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.assess()
            self.result = saved
        self.result["observed"][0]["origin"]["kind"] = "installed"
        with self.assertRaises(ValueError):
            self.assess()

    def test_checker_cannot_promote_partial_receipt(self):
        plan = expand(self.profile)
        result = observations(plan)
        check = next(
            c for c in result["cases"][0]["checks"] if c["id"].startswith("suite.")
        )
        check["observation"]["receipt"]["completed"] = False
        with self.assertRaises(ValueError):
            summarize(plan, result)
        check["status"] = "INCOMPLETE"
        self.assertEqual(summarize(plan, result)["claim_status"], "INCOMPLETE")

    def test_receipt_cannot_be_reused_in_another_case(self):
        p = profile("source-test")
        other = copy.deepcopy(p["environments"][0])
        other["id"] = "second"
        p["environments"].append(other)
        plan = expand(p)
        result = observations(plan)
        first = next(
            c for c in result["cases"][0]["checks"] if c["id"].startswith("suite.")
        )
        second = next(
            c for c in result["cases"][1]["checks"] if c["id"].startswith("suite.")
        )
        second["observation"] = copy.deepcopy(first["observation"])
        with self.assertRaisesRegex(ValueError, "another trial"):
            summarize(plan, result)


if __name__ == "__main__":
    unittest.main()
