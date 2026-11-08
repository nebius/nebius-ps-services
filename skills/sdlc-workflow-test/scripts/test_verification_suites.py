"""Suite timeout, cancellation, safe diagnostics and prerequisite attribution."""

from dataclasses import replace
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import verification_suites as suites
import verify_agentic_sdlc as verifier


class SuiteExecutionTests(unittest.TestCase):
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

    def spec(self, code, timeout=5):
        return suites.SuiteSpec(
            "probe", [sys.executable, "-B", "-c", code], Path.cwd(), timeout
        )

    def test_timeout_retains_completed_test_identifiers(self):
        result = suites.execute(
            self.spec(
                "import time; print('test_completed ... ok', flush=True); time.sleep(10)",
                0.15,
            ),
            os.environ.copy(),
        )
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.passed, frozenset({"test_completed"}))
        self.assertEqual(result.outcome, "TIMEOUT")
        self.assertLess(result.elapsed, 5)

    def test_start_failure_is_structured(self):
        spec = replace(
            self.spec("pass"), command=["/nonexistent-sdlc-suite-executable"]
        )
        self.assertEqual(suites.execute(spec, os.environ.copy()).returncode, 127)

    def test_normal_exit_cleans_owned_descendants(self):
        code = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print('test_done ... ok')"
        result = suites.execute(self.spec(code), os.environ.copy())
        self.assertEqual(result.returncode, 0)
        self.assertLess(result.elapsed, 5)

    def test_failure_details_exclude_exception_bodies(self):
        completed = subprocess.CompletedProcess(
            [],
            1,
            stdout="secret test value",
            stderr="ERROR: test_owner (module.Case.test_owner)\nprivate traceback",
        )
        with mock.patch.object(suites, "run_command", return_value=completed):
            result = suites.execute(self.spec("pass"), {})
        self.assertEqual(result.failed, ("test_owner",))
        self.assertNotIn("secret", result.detail)
        self.assertNotIn("private", result.detail)

    def test_cleanup_failure_is_not_success(self):
        with mock.patch.object(
            suites, "run_command", side_effect=RuntimeError("private failure")
        ):
            result = suites.execute(self.spec("pass"), {})
        self.assertEqual(result.returncode, 125)
        self.assertNotIn("private", result.detail)

    def test_cleanup_drain_timeout_is_unproven_and_verifies_group_absence(self):
        group = mock.Mock(id=12345)
        owner = SimpleNamespace(OwnedProcessGroup=lambda: group)
        child = mock.Mock(returncode=0)
        child.communicate.side_effect = [
            ("test_completed ... ok", ""),
            subprocess.TimeoutExpired("private command", 5, output="private output"),
        ]
        with (
            mock.patch.object(
                suites.importlib.util, "spec_from_file_location",
                return_value=SimpleNamespace(loader=mock.Mock()),
            ),
            mock.patch.object(suites.importlib.util, "module_from_spec", return_value=owner),
            mock.patch.object(suites.subprocess, "Popen", return_value=child),
        ):
            result = suites.execute(self.spec("pass"), {})
        self.assertEqual(result.outcome, "EXECUTION_UNPROVEN")
        self.assertFalse(result.passed)
        self.assertNotIn("private", result.detail)
        group.close.assert_called_once_with(grace=0.05)
        group.verify_absent.assert_called_once_with()

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
                mock.patch.object(suites, "run_command", side_effect=[
                    subprocess.CompletedProcess([], 0, "test_ok ... ok", ""),
                    subprocess.TimeoutExpired("private command", 5),
                ]) as run,
            ):
                verifier.check_capability_regressions(ctx)
            self.assertEqual(run.call_count, 2)
            self.assertEqual([r.outcome for r in ctx.suite_results], ["PASS", "EXECUTION_UNPROVEN"])
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
                    "first", 0, 0.1, frozenset({"test_ok"}), frozenset(), ()
                ),
                suites.SuiteResult("second", 130, 0.2, frozenset(), frozenset(), ()),
            ]
            with (
                mock.patch.object(suites, "suite_catalog", return_value=specs),
                mock.patch.object(
                    suites, "capability_catalog", return_value=capabilities
                ),
                mock.patch.object(suites, "execute", side_effect=outcomes) as run,
            ):
                verifier.check_capability_regressions(ctx)
            self.assertEqual(run.call_count, 2)
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
                mock.patch.object(suites, "execute", return_value=outcome),
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

    def test_catalog_has_bounded_commands_and_measured_dispatch_budget(self):
        catalog = suites.suite_catalog(Path(__file__).resolve().parents[2])
        self.assertEqual(catalog["dispatch"].timeout, 600)
        for spec in catalog.values():
            self.assertGreater(spec.timeout, 0)
            self.assertLessEqual(spec.timeout, 900)
            self.assertTrue(spec.command)

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
