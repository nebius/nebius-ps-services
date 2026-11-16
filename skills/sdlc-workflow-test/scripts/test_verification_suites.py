"""Suite timeout, cancellation, safe diagnostics and prerequisite attribution."""

from dataclasses import replace
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import verification_suites as suites
import verify_agentic_sdlc as verifier


class SuiteExecutionTests(unittest.TestCase):
    def test_catalog_buffers_summaries_without_hiding_failed_test_outcomes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            path = fixture / "test_probe.py"
            path.write_text("import unittest\nclass Probe(unittest.TestCase):\n def test_passing(self): print('private body')\n def test_failing(self): self.fail('private failure')\n @unittest.skip('control')\n def test_skipped(self): pass\n")
            result = suites.execute(suites.SuiteSpec("probe", [sys.executable, str(path)], fixture), os.environ.copy())
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.passed, frozenset({"test_probe.Probe.test_passing"}))
            self.assertEqual(result.skipped, frozenset({"test_probe.Probe.test_skipped"}))
            self.assertIn("test_probe.Probe.test_failing", result.failed)
            self.assertNotIn("private", result.detail)

    def test_execution_command_collects_its_required_capability_tests(self):
        root = Path(__file__).resolve().parents[2]
        # Use unittest's actual command parser and loader without executing the
        # expensive real-Git cases. A declared capability must be in this suite.
        collect = """import json,sys,unittest
def names(test):
    if isinstance(test, unittest.TestSuite):
        return [name for child in test for name in names(child)]
    return [test.id().rsplit('.', 1)[-1]]
class Collect(unittest.TestProgram):
    def runTests(self):
        print(json.dumps(names(self.test)))
Collect(module=None, argv=['unittest', *sys.argv[1:]], exit=False)
"""
        for suite_id in ("execution", "three-tier"):
            with self.subTest(suite=suite_id):
                spec = suites.suite_catalog(root)[suite_id]
                result = subprocess.run(
                    [spec.command[0], "-B", "-c", collect, *spec.command[3:]],
                    cwd=spec.cwd, capture_output=True, text=True, timeout=30, check=True,
                )
                collected = set(json.loads(result.stdout))
                required = {test for _, dependencies in suites.capability_catalog().values()
                            for suite, test in dependencies if suite == suite_id}
                self.assertFalse(required - collected, sorted(required - collected))

    def test_unproven_process_cleanup_skips_all_later_fixture_execution(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary).resolve() / "verify"
            for name in (
                "check_design",
                "check_source_phase_contract",
                "check_source_catalog_validation",
                "check_spec_ownership_contract",
                "check_vertical_slice_contract",
                "check_execution_plane_contract",
                "check_repair_loop_contract",
                "check_skill_discovery",
                "check_hook_config",
                "check_direct_objectives",
                "add_agent_required_sections",
            ):
                stack.enter_context(mock.patch.object(verifier, name))
            stack.enter_context(
                mock.patch.object(
                    verifier, "setup_disposable_project", return_value=True
                )
            )
            hooks = stack.enter_context(
                mock.patch.object(verifier, "check_hooks_with_fixtures")
            )

            def failed(ctx):
                ctx.suite_results.append(
                    suites.SuiteResult(
                        "failed-cleanup", 125, 0.1, frozenset(), frozenset(), ()
                    )
                )
                ctx.add(
                    "Suite execution results",
                    "failed-cleanup",
                    "FAIL",
                    "Execution unproven",
                )

            stack.enter_context(
                mock.patch.object(
                    verifier, "check_capability_regressions", side_effect=failed
                )
            )
            with redirect_stdout(StringIO()):
                self.assertEqual(verifier.main(["--verification-root", str(root)]), 1)
            hooks.assert_not_called()

    def spec(self, code="pass"):
        return suites.SuiteSpec("probe", [sys.executable, str(Path(__file__).resolve())], Path.cwd())

    def row(self, status, *, passed=(), inventory=("fixture.Case.test_ok",)):
        return {"id": "probe:exclusive", "suite": "probe", "status": status,
                "inventory": list(inventory), "outcomes": {name: "passed" for name in passed},
                "observation": {"elapsed_seconds": .1, "local_group_quiescent": status != "EXECUTION_UNPROVEN"}}

    def test_interruption_retains_completed_test_identifiers(self):
        row = self.row("INTERRUPTED", passed=("fixture.Case.test_ok",),
                       inventory=("fixture.Case.test_ok", "fixture.Case.test_pending"))
        with mock.patch.object(suites.scheduler(), "schedule", return_value=[row]):
            result = suites.execute(self.spec(), {})
        self.assertEqual(result.returncode, 130)
        self.assertEqual(result.passed, frozenset({"fixture.Case.test_ok"}))
        self.assertEqual(result.outcome, "INTERRUPTED")

    def test_start_failure_is_structured(self):
        with mock.patch.object(suites.scheduler(), "schedule", return_value=[self.row("START_FAILED")]):
            result = suites.execute(self.spec(), {})
        self.assertEqual(result.returncode, 127)

    def test_normal_exit_cleans_owned_descendants(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "test_child.py"
            path.write_text("import unittest,subprocess,sys\nclass Child(unittest.TestCase):\n def test_done(self): subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n")
            result = suites.execute(suites.SuiteSpec("probe", [sys.executable, str(path)], root), os.environ.copy())
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.groups[0]["observation"]["local_group_quiescent"])

    def test_failure_details_exclude_exception_bodies(self):
        row = self.row("FAIL")
        row["outcomes"] = {"fixture.Case.test_ok": "failed"}
        with mock.patch.object(suites.scheduler(), "schedule", return_value=[row]):
            result = suites.execute(self.spec(), {})
        self.assertEqual(result.failed, ("fixture.Case.test_ok",))
        self.assertNotIn("private", result.detail)

    def test_cleanup_failure_is_not_success(self):
        with mock.patch.object(suites.scheduler(), "schedule", return_value=[self.row("EXECUTION_UNPROVEN")]):
            result = suites.execute(self.spec(), {})
        self.assertEqual(result.returncode, 125)

    def test_cleanup_uncertainty_preserves_completed_cases_without_admission(self):
        row = self.row("EXECUTION_UNPROVEN", passed=("fixture.Case.test_ok",))
        with mock.patch.object(suites.scheduler(), "schedule", return_value=[row]):
            result = suites.execute(self.spec(), {})
        self.assertEqual(result.outcome, "EXECUTION_UNPROVEN")
        self.assertEqual(result.passed, frozenset({"fixture.Case.test_ok"}))

    def test_cleanup_timeout_preserves_prior_results_and_stops_later_suites(self):
        with tempfile.TemporaryDirectory() as temporary:
            ctx = verifier.setup_context(verifier.parse_args([
                "--verification-root", str(Path(temporary) / "verify"),
            ]))
            specs = {
                name: replace(self.spec("pass"), name=name)
                for name in ("first", "second", "third")
            }
            with (
                mock.patch.object(suites, "suite_catalog", return_value=specs),
                mock.patch.object(suites, "capability_catalog", return_value={
                    name: (name, ((name, "test_ok"),)) for name in specs
                }),
                mock.patch.object(suites, "execute_all", return_value=[
                    suites.SuiteResult("first", 0, .1, frozenset({"fixture.Case.test_ok"}), frozenset(), (), inventory=frozenset({"fixture.Case.test_ok"})),
                    suites.SuiteResult("second", 125, .1, frozenset(), frozenset(), (), inventory=frozenset({"fixture.Case.test_ok"})),
                    suites.SuiteResult("third", 126, 0, frozenset(), frozenset(), (), inventory=frozenset({"fixture.Case.test_ok"})),
                ]) as run,
            ):
                verifier.check_capability_regressions(ctx)
            self.assertEqual(run.call_count, 1)
            self.assertEqual([r.outcome for r in ctx.suite_results], ["PASS", "EXECUTION_UNPROVEN", "NOT_RUN"])
            results = {c.capability_id: c.status for c in ctx.checks if c.capability_id}
            self.assertEqual(results, {"first": "PASS", "second": "FAIL", "third": "WARN"})
            self.assertEqual(verifier.final_status(ctx), "FAIL")

    def test_interruption_stops_later_suites_and_preserves_prior_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            ctx = verifier.setup_context(
                verifier.parse_args(
                    ["--verification-root", str(Path(temporary) / "verify")]
                )
            )
            specs = {
                name: replace(self.spec("pass"), name=name)
                for name in ("first", "second", "third")
            }
            capabilities = {name: (name, ((name, "test_ok"),)) for name in specs}
            outcomes = [
                suites.SuiteResult(
                    "first", 0, 0.1, frozenset({"fixture.Case.test_ok"}), frozenset(), (), inventory=frozenset({"fixture.Case.test_ok"})
                ),
                suites.SuiteResult("second", 130, 0.2, frozenset(), frozenset(), (), inventory=frozenset({"fixture.Case.test_ok"})),
                suites.SuiteResult("third", 126, 0, frozenset(), frozenset(), (), inventory=frozenset({"fixture.Case.test_ok"})),
            ]
            with (
                mock.patch.object(suites, "suite_catalog", return_value=specs),
                mock.patch.object(
                    suites, "capability_catalog", return_value=capabilities
                ),
                mock.patch.object(suites, "execute_all", return_value=outcomes) as run,
            ):
                verifier.check_capability_regressions(ctx)
            self.assertEqual(run.call_count, 1)
            self.assertTrue(ctx.interrupted)
            results = {c.capability_id: c.status for c in ctx.checks if c.capability_id}
            self.assertEqual(
                results, {"first": "PASS", "second": "WARN", "third": "WARN"}
            )

    def test_failed_suite_is_one_finding_with_explicit_dependent_impacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            ctx = verifier.setup_context(
                verifier.parse_args(
                    ["--verification-root", str(Path(temporary) / "verify")]
                )
            )
            outcome = suites.SuiteResult(
                "probe", 1, 0.1, frozenset(), frozenset(), ("test_bad",)
            )
            with (
                mock.patch.object(
                    suites, "suite_catalog", return_value={"probe": self.spec("pass")}
                ),
                mock.patch.object(
                    suites,
                    "capability_catalog",
                    return_value={
                        name: (name, (("probe", "test_bad"),)) for name in ("a", "b")
                    },
                ),
                mock.patch.object(suites, "execute_all", return_value=[outcome]),
            ):
                verifier.check_capability_regressions(ctx)
            failures = [c for c in ctx.checks if c.section == "Suite execution results"]
            self.assertEqual(len(failures), 1)
            self.assertTrue(
                all(
                    "Blocked by failed required suite" in c.detail
                    for c in ctx.checks
                    if c.capability_id
                )
            )
            self.assertEqual(verifier.final_status(ctx), "FAIL")

    def test_catalog_uses_observations_without_suite_expiry(self):
        catalog = suites.suite_catalog(Path(__file__).resolve().parents[2])
        for spec in catalog.values():
            self.assertEqual(spec.observation_interval_seconds, 30)
            self.assertTrue(spec.command)
            self.assertFalse(hasattr(spec, "timeout"))

    def test_in_progress_report_never_claims_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            ctx = verifier.setup_context(
                verifier.parse_args(
                    ["--verification-root", str(Path(temporary) / "verify")]
                )
            )
            ctx.in_progress = True
            self.assertEqual(verifier.final_status(ctx), "PARTIAL")
            ctx.in_progress = False
            self.assertEqual(verifier.final_status(ctx), "PASS")

    def test_prior_report_is_archived_without_overwriting_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "report.md"
            path.write_text("prior failed trial\n")
            path.chmod(0o600)
            verifier.archive_report(path)
            path.write_text("next trial\n")
            verifier.archive_report(path)
            history = sorted(
                p.read_text() for p in (path.parent / "report.md.history").iterdir()
            )
            self.assertEqual(history, ["next trial\n", "prior failed trial\n"])


if __name__ == "__main__":
    unittest.main()
