#!/usr/bin/env python3
"""Focused tests for the private three-tier lifecycle helper."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zlib


MODULE_PATH = Path(__file__).with_name("three_tier_lifecycle.py")
sys.path.insert(0, str(MODULE_PATH.parent))
import three_tier_lifecycle as lifecycle  # noqa: E402


def png_bytes(red: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00" + bytes((red, 0, 0, 255)))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


class ThreeTierLifecycleTests(unittest.TestCase):
    def test_fixed_runner_captures_real_unittest_outcomes_without_product_output(self):
        owner = lifecycle.three_tier_tests
        for failing in (False, True):
            runner_module = types.ModuleType("django.test.runner")

            class DiscoverRunner:
                def run_suite(self, suite, **kwargs):
                    return unittest.TextTestRunner(stream=StringIO()).run(suite)

            runner_module.DiscoverRunner = DiscoverRunner
            django = types.ModuleType("django")
            django.test = types.ModuleType("django.test")
            django.test.runner = runner_module

            class ProductCase(unittest.TestCase):
                def runTest(self):
                    print("product output must be discarded")
                    self.assertFalse(failing)

            def manage(*args, **kwargs):
                result = runner_module.EvidenceRunner().run_suite(unittest.TestSuite([ProductCase()]))
                raise SystemExit(0 if result.wasSuccessful() else 1)

            output = StringIO()
            with mock.patch.dict(sys.modules, {"django": django, "django.test": django.test,
                                              "django.test.runner": runner_module}), mock.patch(
                "runpy.run_path", side_effect=manage
            ), mock.patch.object(sys, "argv", ["runner", "tests.unit"]), redirect_stdout(output), self.assertRaises(SystemExit) as exit_info:
                exec(owner.RUNNER, {})
            self.assertEqual(exit_info.exception.code, int(failing))
            self.assertNotIn("product output", output.getvalue())
            counts = json.loads(output.getvalue().removeprefix(owner.PREFIX))
            self.assertEqual(counts["tests"], 1)
            self.assertEqual(counts["failures"], int(failing))

    def test_owned_test_execution_is_immutable_and_identity_bound(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        owner = lifecycle.three_tier_tests
        target = owner.read_receipt(state, state["test_runs"][0])["target"]
        state["test_runs"] = []
        state["browser_stages"] = []
        lifecycle.update_state(self.root, state)
        counts = dict.fromkeys(owner.COUNTERS, 0)
        counts["tests"] = 3
        result = subprocess.CompletedProcess([], 0, owner.PREFIX + json.dumps(counts), "ignored")
        with mock.patch.object(lifecycle, "browser_target", return_value=target), mock.patch.object(
            lifecycle, "command", return_value=result
        ) as command:
            first = lifecycle.run_test_class(self.root, "unit", "tests.test_unit")
            second = lifecycle.run_test_class(self.root, "unit", "tests.test_unit")
            self.assertEqual(first["test_runs"], second["test_runs"])
            command.assert_called_once()
            self.assertEqual(command.call_args.args[0][-4:], ["python", "-c", owner.RUNNER, "tests.test_unit"])
            self.assertEqual(owner.read_receipt(first, first["test_runs"][0])["counts"]["tests"], 3)
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.run_test_class(self.root, "unit", "tests.other")

    def test_owned_test_failure_cannot_be_retried_as_passing(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        owner = lifecycle.three_tier_tests
        target = owner.read_receipt(state, state["test_runs"][0])["target"]
        state["test_runs"] = []
        state["browser_stages"] = []
        lifecycle.update_state(self.root, state)
        with mock.patch.object(lifecycle, "browser_target", return_value=target), mock.patch.object(
            lifecycle, "command", return_value=subprocess.CompletedProcess([], 1, "private output", "private error")
        ) as command:
            for _ in range(2):
                with self.assertRaises(lifecycle.LifecycleError):
                    lifecycle.run_test_class(self.root, "unit", "tests.test_unit")
            command.assert_called_once()
        _, saved = lifecycle.load_active(self.root)
        self.assertEqual(saved["test_runs"][0]["outcome"], "FAIL")
        receipt = (Path(saved["run_root"]) / saved["test_runs"][0]["path"]).read_text()
        self.assertNotIn("private output", receipt)
        self.assertNotIn("private error", receipt)

    def test_owned_test_execution_rejects_source_drift(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        owner = lifecycle.three_tier_tests
        target = owner.read_receipt(state, state["test_runs"][0])["target"]
        state["test_runs"] = []
        state["browser_stages"] = []
        lifecycle.update_state(self.root, state)
        counts = dict.fromkeys(owner.COUNTERS, 0)
        counts["tests"] = 1
        with mock.patch.object(lifecycle, "browser_target", side_effect=[target, {**target, "head": "a" * 40}]), mock.patch.object(
            lifecycle, "command", return_value=subprocess.CompletedProcess([], 0, owner.PREFIX + json.dumps(counts), "")
        ), self.assertRaises(lifecycle.LifecycleError):
            lifecycle.run_test_class(self.root, "unit", "tests.test_unit")
        _, saved = lifecycle.load_active(self.root)
        self.assertEqual(saved["test_runs"][0]["outcome"], "FAIL")

    def test_owned_test_counts_reject_failure_skips_and_empty_execution(self) -> None:
        owner = lifecycle.three_tier_tests
        counts = dict.fromkeys(owner.COUNTERS, 0)
        counts["tests"] = 2
        self.assertEqual(owner.observed_counts(owner.PREFIX + json.dumps(counts)), counts)
        for field in owner.COUNTERS:
            invalid = {**counts, field: 0 if field == "tests" else 1}
            with self.subTest(field=field), self.assertRaises(owner.TestEvidenceError):
                owner.observed_counts(owner.PREFIX + json.dumps(invalid))
        for output in ("all tests passed", "FAILED", owner.PREFIX + "{}", owner.PREFIX + "{}\n" + owner.PREFIX + "{}"):
            with self.subTest(output=output), self.assertRaises(owner.TestEvidenceError):
                owner.observed_counts(output)

    def test_semantic_test_evidence_rejects_wrong_class_revision_and_image(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        binding = state["test_runs"][0]
        path = Path(state["run_root"]) / binding["path"]
        original = json.loads(path.read_text())
        variants = [{**original, "test_class": "api"}, {**original, "verification_id": "foreign"},
                    {**original, "target": {**original["target"], "head": "0" * 40}},
                    {**original, "target": {**original["target"], "deployment": {
                        **original["target"]["deployment"], "image_id": "sha256:" + "b" * 64}}}]
        for value in variants:
            lifecycle.private_json(path, value)
            binding["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.subTest(value=value), self.assertRaises(lifecycle.SemanticEvidenceError):
                lifecycle.validate_semantic_results(state, keep=True)

    def test_partial_summary_does_not_repeat_unverified_test_pass(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        path = Path(state["run_root"]) / state["test_runs"][0]["path"]
        path.write_text("FAILED unit")
        summary = lifecycle.summarize_semantic_results(state)
        self.assertEqual(summary["tests"]["unit"]["status"], "PARTIAL")
        self.assertEqual(summary["tests"]["unit"]["assertions"], 0)

    def test_semantic_rejects_failed_test_artifacts(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        root = Path(state["run_root"])
        value = json.loads((root / "evidence/three-tier-results.json").read_text())
        for name in ("unit", "api", "database", "migration", "vertical"):
            for relative in value["tests"][name]["evidence"]:
                (root / relative).write_text(f"FAILED {name}: 1 test failed, 0 passed\n")
        with self.assertRaises(lifecycle.SemanticEvidenceError):
            lifecycle.validate_semantic_results(state, keep=True)

    def test_semantic_rejects_inflated_test_counts(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        artifact = Path(state["run_root"]) / "evidence/three-tier-results.json"
        value = json.loads(artifact.read_text())
        value["tests"]["unit"]["assertions"] += 100
        artifact.write_text(json.dumps(value))
        with self.assertRaises(lifecycle.SemanticEvidenceError):
            lifecycle.validate_semantic_results(state, keep=True)

    def test_claude_generation_uses_native_home_and_rejects_cross_host_resume(self):
        with mock.patch.dict(os.environ, {"SKILLS_AGENT": "claude"}):
            os.environ.pop("CODEX_THREAD_ID", None)
            state = lifecycle.prepare(self.root)
            self.assertTrue((Path(state["private_root"]) / "claude-home").is_dir())
            lifecycle.validate_state(self.root, state)
            with mock.patch.dict(os.environ, {"SKILLS_AGENT": "codex"}):
                with self.assertRaises(lifecycle.LifecycleError):
                    lifecycle.validate_state(self.root, state)

    def setUp(self) -> None:
        from git_fixture_environment import isolated_git_configuration

        self.enterContext(isolated_git_configuration())
        # macOS maps /var to /private/var through a system symlink. Use the
        # home directory so lifecycle symlink rejection is exercised without
        # weakening production path checks for that platform alias.
        self.temporary = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "verification"
        # Synthetic identities for offline lifecycle tests; real registration is
        # exercised separately by test_three_tier_target with actual Git state.
        self.phase_identity = mock.patch.object(lifecycle, "phase_identity", side_effect=lambda state, phase, *, coordinator_session=None:
            ("a"*64, "d"*64 if phase in lifecycle.EXECUTION_PHASES else None))
        self.phase_identity.start()
        self.addCleanup(self.phase_identity.stop)
        self.preflight = mock.patch.multiple(
            lifecycle,
            require_command=mock.DEFAULT,
            detect_browser=mock.DEFAULT,
            command=mock.DEFAULT,
        )
        values = self.preflight.start()
        self.require_command = values["require_command"]
        self.detect_browser = values["detect_browser"]
        self.command = values["command"]
        self.require_command.side_effect = ["29.0", "5.0", "git version 2.50"]
        self.detect_browser.return_value = ("chrome", "Google Chrome")
        self.command.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        self.browser_close = mock.patch.object(lifecycle.three_tier_browser, "close")
        self.close_browser = self.browser_close.start()
        self.close_browser.side_effect = lambda root, identity, value: {**value, "status": "CLOSED", "active": None}


    def tearDown(self) -> None:
        self.browser_close.stop()
        self.preflight.stop()
        self.temporary.cleanup()

    def prepare(self) -> dict[str, object]:
        state = lifecycle.prepare(self.root)
        lifecycle.update_state(self.root, state)
        return state

    def test_historical_tdd_contract_rejects_previous_active_lifecycle(self):
        state = self.prepare()
        state["schema"] = "agentic-sdlc/three-tier-lifecycle-v6"
        lifecycle.private_json(lifecycle.paths(self.root)["active"], state)
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.load_active(self.root)

    def test_preparation_isolates_inherited_git_filters(self):
        sentinel = Path(self.temporary.name) / "inherited-filter-executed"
        attributes = Path.home() / "attributes"
        attributes.write_text("* filter=ambient\n")
        executable = Path.home() / "filter.sh"
        executable.write_text(f'#!/bin/sh\ntouch "{sentinel}"\ncat\n')
        executable.chmod(0o700)
        (Path.home() / ".gitconfig").write_text(
            f'[core]\n attributesFile = {attributes}\n'
            f'[filter "ambient"]\n clean = {executable}\n'
        )
        state = self.prepare()
        self.assertRegex(state["git"]["baseline_sha"], r"^[0-9a-f]{40}$")
        self.assertFalse(sentinel.exists())

    def reset_prepare_preflight(self) -> None:
        self.require_command.side_effect = ["29.0", "5.0", "git version 2.50"]

    def browser_url(self) -> str:
        _, state = lifecycle.load_active(self.root)
        return (
            "http://127.0.0.1:49152/"
            f"?verification_id={state['verification_id']}"
        )

    def mark_browser_closed(self) -> None:
        _, state = lifecycle.load_active(self.root)
        state["browser_instance"] = self.close_browser(
            Path(state["run_root"]),
            state["verification_id"],
            state["browser_instance"],
        )
        lifecycle.update_state(self.root, state)

    def write_startup_fixture(self, state):
        """Synthetic receipt for semantic unit tests; real startup is tested separately."""
        from contextlib import nullcontext
        # These Docker/receipt tests have no native product execution. The
        # workspace suite tests admission against real product ownership.
        self.enterContext(mock.patch.object(lifecycle.workspace, "product_quiescence", side_effect=lambda *_: nullcontext()))
        relative = "evidence/workflow-startup.json"
        artifact = Path(state["run_root"]) / relative
        lifecycle.private_json(artifact, {
            "schema": "agentic-sdlc/workflow-startup-v3",
            "objective_sha256": "a" * 64,
            "verification_id": state["verification_id"],
            "baseline_sha": state["git"]["baseline_sha"],
            "project_id": "test-project", "run_id": "run-test", "agent": "codex",
            "current_phase": "sdlc-start", "source_state_discovery": "MATCH",
            "owner_session_hash": hashlib.sha256(b"native-fixture-session").hexdigest(), "lock_sha256": "d" * 64,
            "observed_at": lifecycle.utc_now(),
        })
        state["workflow_startup"] = {
            "path": relative, "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()
        }
        from native_activation_fixture import seed
        seed(state, json.loads(artifact.read_text()), Path(__file__).resolve().parents[2])

    def write_valid_results(self, state: dict[str, object]) -> None:
        run_root = Path(state["run_root"])
        project = Path(state["project_root"])
        fixture = project / "test-implementation.txt"
        fixture.write_text((fixture.read_text() if fixture.exists() else "") + "fixture commit\n")
        lifecycle.owned_git_origin._git(project, "add", "-A")
        lifecycle.owned_git_origin._git(project, "commit", "-m", "semantic test fixture")
        promoted = lifecycle.owned_git_origin._git(project, "rev-parse", "HEAD")
        evidence_paths = []
        for test_name in (
            "unit",
            "api",
            "database",
            "migration",
            "vertical",
            "gui",
        ):
            relative = f"evidence/tests/{test_name}.txt"
            artifact = run_root / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(f"{test_name} passed\n", encoding="utf-8")
            evidence_paths.append(relative)
        screenshots = []
        for index in range(5):
            relative = f"evidence/gui-uat/checkpoint-{index}.png"
            artifact = run_root / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(png_bytes(index))
            screenshots.append(relative)
        value = {
            "schema": lifecycle.RESULTS_SCHEMA,
            "scenario": lifecycle.SCENARIO,
            "verification_id": state["verification_id"],
            "git": {
                "baseline_sha": state["git"]["baseline_sha"],
                "promoted_sha": promoted,
                "clean": True,
            },
            "layers": {"frontend": "PASS", "web": "PASS", "database": "PASS"},
            "tests": {
                test_name: {
                    "status": "PASS",
                    "assertions": 1,
                    "evidence": [evidence_paths[index]],
                }
                for index, test_name in enumerate(
                    ("unit", "api", "database", "migration", "vertical", "gui")
                )
            },
            "sdlc_phases": {phase: "PASS" for phase in lifecycle.REQUIRED_SDLC_PHASES},
            "gui_uat": {
                "harness": "playwright-test",
                "headless": True,
                "browser": "chrome",
                "steps": list(lifecycle.REQUIRED_GUI_STEPS),
                "api_db_correlated": True,
                "restart_persistence": True,
                "screenshots": screenshots,
            },
        }
        state["git"] = {
            "baseline_sha": value["git"]["baseline_sha"],
            "promoted_sha": value["git"]["promoted_sha"],
        }
        self.write_startup_fixture(state)
        state["endpoints"] = {
            "web": "http://127.0.0.1:49152/",
            "api": "http://127.0.0.1:49152/api/v1/tasks",
            "health": "http://127.0.0.1:49152/healthz",
            "database": "db:5432/taskboard",
        }
        state["resources"] = {
            "containers": ["web-id", "db-id"],
            "networks": ["network-id"],
            "volumes": ["volume-id"],
            "images": ["image-id"],
        }
        state["phases"] = []
        for phase in lifecycle.REQUIRED_SDLC_PHASES:
            relative = f"evidence/phases/{phase}.json"
            artifact = run_root / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(
                json.dumps(
                    {
                        "schema": lifecycle.PHASE_RESULT_SCHEMA,
                        "phase": phase,
                        "status": "PASS",
                        "verification_id": state["verification_id"],
                        "baseline_sha": state["git"]["baseline_sha"],
                        "recorded_head": state["git"]["promoted_sha"],
                        "objective_sha256": "a"*64,
                        "basis_sha256": "d"*64 if phase in lifecycle.EXECUTION_PHASES else None,
                        "assertions": lifecycle.PHASE_REQUIRED_ASSERTIONS[phase],
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(artifact, 0o600)
            fingerprint = hashlib.sha256(artifact.read_bytes()).hexdigest()
            relative = f"evidence/phases/attempts/{phase}/{fingerprint}.json"
            immutable = run_root / relative
            immutable.parent.mkdir(parents=True, exist_ok=True)
            immutable.write_bytes(artifact.read_bytes())
            immutable.chmod(0o600)
            state["phases"].append(
                {
                    "attempt": 1,
                    "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    "phase": phase,
                    "status": "PASS",
                    "summary": "Phase passed.",
                    "evidence": [relative],
                    "recorded_at": lifecycle.utc_now(),
                }
            )
        self.write_browser_receipts(state, screenshots)
        self.write_test_receipts(state, value)
        observation = {
            "schema": lifecycle.three_tier_runtime.OBSERVATION_SCHEMA,
            "verification_id": state["verification_id"],
            "mode": "acceptance",
            "target": {"path": state["project_root"], "head": promoted},
            "worker_task": None,
            "assignment_digest": None,
            "observed_at": lifecycle.utc_now(),
            "source_operation": "record-runtime",
            "outcome": "PASS",
            "category": None,
            "operation": "inspect_volume",
            "predicates": [
                {
                    "name": "fixture.valid",
                    "expected": True,
                    "observed": True,
                    "passed": True,
                }
            ],
            "resources": state["resources"],
            "endpoints": state["endpoints"],
        }
        relative = "evidence/runtime/observation-" + "e" * 32 + ".json"
        artifact = run_root / relative
        lifecycle.private_json(artifact, observation)
        binding = {
            "path": relative,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
        state["runtime_observations"] = [
            lifecycle.three_tier_runtime.observation_summary(observation, binding)
        ]
        state["runtime_inventory"] = binding
        lifecycle.update_state(self.root, state)
        (run_root / "evidence" / "three-tier-results.json").write_text(
            json.dumps(value), encoding="utf-8"
        )
        (run_root / "evidence" / "three-tier-results.json").chmod(0o600)

    def write_test_receipts(self, state, results):
        """Synthetic owner receipts for offline tests, never runtime proof."""
        owner = lifecycle.three_tier_tests
        state["test_runs"] = []
        target = {"path": state["project_root"], "head": state["git"]["promoted_sha"],
                  "deployment": {"build": state["runtime_build"],
                                 "image_id": "sha256:" + "a" * 64, "web_container": "web-id"}}
        for index, name in enumerate(owner.TEST_CLASSES):
            relative = f"evidence/tests/{index:032x}.json"
            path = Path(state["run_root"]) / relative
            counts = dict.fromkeys(owner.COUNTERS, 0)
            counts["tests"] = 1
            lifecycle.private_json(path, {"schema": owner.SCHEMA,
                "verification_id": state["verification_id"], "test_class": name,
                "label": "tests.test_" + name, "runner_sha256": owner.RUNNER_SHA256,
                "target": target, "exit_code": 0, "counts": counts, "outcome": "PASS"})
            state["test_runs"].append({"test_class": name, "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "outcome": "PASS"})
            results["tests"][name]["evidence"] = [relative]
        results["tests"]["gui"].update(assertions=2, evidence=[
            item["path"] for item in state["browser_stages"] if item["stage"].startswith("uat-")])

    def write_browser_receipts(self, state, screenshots):
        """Synthetic owned receipts for offline tests; never live evidence."""
        browser = lifecycle.three_tier_browser
        root = Path(state["run_root"])
        state["browser_instance"] = {**browser.initial_state(state["verification_id"]), "status": "CLOSED"}
        state["browser_stages"] = []
        state["environment"]["headless_browser"] = "PASS"
        target = {"path": state["project_root"], "head": state["git"]["promoted_sha"]}
        binding = self.write_build_receipt(state, target)
        target["deployment"] = {"build": binding, "image_id": "sha256:" + "a" * 64, "web_container": "web-id"}
        all_screenshots = []
        for index, stage in enumerate(browser.STAGES):
            attempt_id = f"{index:032x}"
            directory = root / "evidence/gui-uat" / attempt_id
            directory.mkdir(exist_ok=True)
            artifacts = {}
            record = {"id": "1", "title": "test", "completed": True}
            images = []
            for name in screenshots[:4] if index == 2 else screenshots[4:] if index == 3 else []:
                item = directory / Path(name).name
                item.write_bytes((root / name).read_bytes())
                images.append(item.name)
                all_screenshots.append(str(item.relative_to(root)))
                artifacts[str(item.relative_to(root))] = browser.digest(item)
            payloads = {
                "trace.zip": "synthetic trace",
                "test-results.json": json.dumps({"stats": {"expected": 1, "unexpected": 0, "flaky": 0, "skipped": 0}}),
                "observations.json": json.dumps({"attempt_id": attempt_id, "stage": stage, "verification_id": state["verification_id"],
                    "headless": True, "browser": "chrome", "browser_version": "synthetic", "record": record,
                    "actions": [{"action": "close-browser"}], "screenshots": images})}
            for name, content in payloads.items():
                item = directory / name
                item.write_text(content)
                artifacts[str(item.relative_to(root))] = browser.digest(item)
            checks = [] if index == 0 else ["post-restart"] if index == 3 else ["blank", "created", "completed"]
            receipt = {"schema": browser.RECEIPT_SCHEMA, "verification_id": state["verification_id"],
                "stage": stage, "attempt_id": attempt_id, "headless": True, "browser": "chrome",
                "outcome": "PASS", "exit_code": 0, "cleanup": "PASS", "artifacts": artifacts,
                "target": target, "record": record, "checks": [{"name": n} for n in checks]}
            item = directory / "receipt.json"
            item.write_text(json.dumps(receipt))
            state["browser_stages"].append({"stage": stage, "outcome": "PASS", "path": str(item.relative_to(root)), "sha256": browser.digest(item)})
        screenshots[:] = all_screenshots
        state["browser_restart"] = {"after_attempt": f"{2:032x}", "target": target, "volumes": state["resources"]["volumes"]}

    def write_build_receipt(self, state, target):
        relative = "evidence/runtime/build-test.json"
        artifact = Path(state["run_root"]) / relative
        artifact.parent.mkdir(exist_ok=True)
        lifecycle.private_json(artifact, {
            "schema": "agentic-sdlc/runtime-build-v1", "verification_id": state["verification_id"],
            "target": dict(target), "image_id": "sha256:" + "a" * 64,
            "compose_sha256": "b" * 64, "observed_at": lifecycle.utc_now(),
        })
        binding = {"path": relative, "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
        state["runtime_build"] = binding
        return binding

    def test_fresh_post_restart_capture_may_match_persisted_pixels(self):
        state = self.prepare()
        self.write_valid_results(state)
        browser = lifecycle.three_tier_browser
        root = Path(state["run_root"])
        bindings = state["browser_stages"]
        before = json.loads((root / bindings[2]["path"]).read_text())
        path = root / bindings[3]["path"]
        after = json.loads(path.read_text())
        completed = sorted(name for name in before["artifacts"] if name.endswith(".png"))[-1]
        post = next(name for name in after["artifacts"] if name.endswith(".png"))
        (root / post).write_bytes((root / completed).read_bytes())
        after["artifacts"][post] = browser.digest(root / post)
        path.write_text(json.dumps(after))
        bindings[3]["sha256"] = browser.digest(path)
        self.assertEqual(lifecycle.validate_semantic_results(state, keep=True)["gui_uat"]["status"], "PASS")

    def test_browser_receipts_reject_tampering_and_volume_replacement(self):
        state = self.prepare()
        self.write_valid_results(state)
        browser = lifecycle.three_tier_browser
        browser.validate_receipts(state)
        original = list(state["resources"]["volumes"])
        state["resources"]["volumes"] = ["replacement-volume"]
        with self.assertRaisesRegex(browser.BrowserOwnershipError, "restart receipt"):
            browser.validate_receipts(state)
        state["resources"]["volumes"] = original
        entry = state["browser_stages"][-1]
        path = Path(state["run_root"]) / entry["path"]
        value = json.loads(path.read_text())
        value["headless"] = False
        path.write_text(json.dumps(value))
        entry["sha256"] = browser.digest(path)
        with self.assertRaisesRegex(browser.BrowserOwnershipError, "identity"):
            browser.validate_receipts(state)

    def test_browser_receipts_reject_success_boolean_without_assertions(self):
        state = self.prepare()
        self.write_valid_results(state)
        browser = lifecycle.three_tier_browser
        entry = state["browser_stages"][-1]
        path = Path(state["run_root"]) / entry["path"]
        value = json.loads(path.read_text())
        report = path.parent / "test-results.json"
        report.write_text(json.dumps({"stats": {"expected": 0, "unexpected": 1, "flaky": 0, "skipped": 0}}))
        value["artifacts"][str(report.relative_to(Path(state["run_root"])))] = browser.digest(report)
        path.write_text(json.dumps(value))
        entry["sha256"] = browser.digest(path)
        with self.assertRaisesRegex(browser.BrowserOwnershipError, "exactly one test"):
            browser.validate_receipts(state)

    def test_browser_checkpoint_correlates_api_and_database(self):
        state = self.prepare()
        state["resources"]["containers"] = ["web-id", "db-id"]
        state["endpoints"] = {"api": "http://127.0.0.1:49152/api/v1/tasks"}
        target = {"head": "b" * 40}
        record = {"id": "1", "title": "owned test", "completed": True}
        rows = [{**record, "id": 1}]
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(rows).encode()
        response.__enter__.return_value.url = state["endpoints"]["api"]
        self.command.return_value = mock.Mock(returncode=0, stdout=json.dumps(rows), stderr="")
        with mock.patch.object(lifecycle, "execution_target", return_value=target), mock.patch.object(lifecycle, "browser_target", return_value=target), mock.patch.object(lifecycle, "assert_resource_owned"), mock.patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = response
            lifecycle.browser_checkpoint(state, target, "completed", record)
        self.assertIn("SELECT id, title, completed FROM tasks_task", self.command.call_args.args[0][-1])

    def test_browser_checkpoint_rejects_changed_target_before_observation(self):
        state = self.prepare()
        with mock.patch.object(lifecycle, "execution_target", return_value={"head": "changed"}), mock.patch("urllib.request.build_opener") as opener:
            with self.assertRaisesRegex(lifecycle.LifecycleError, "target changed"):
                lifecycle.browser_checkpoint(state, {"head": "expected"}, "blank", None)
            opener.assert_not_called()

    def test_browser_stages_reject_out_of_order_and_failed_trial(self):
        state = self.prepare()
        with self.assertRaisesRegex(lifecycle.LifecycleError, "ordered"):
            lifecycle.run_browser_stage(self.root, "evaluate")
        state["browser_stages"] = [{"stage": "capability-discovery", "outcome": "FAIL", "path": "evidence/failure.json", "sha256": "a" * 64}]
        lifecycle.update_state(self.root, state)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "failed browser trial"):
            lifecycle.run_browser_stage(self.root, "evaluate")

    def test_browser_stage_rejects_stale_deployment_before_launch(self):
        state = self.prepare()
        state["browser_stages"] = [{"stage": "capability-discovery", "outcome": "PASS", "path": "evidence/capability.json", "sha256": "c" * 64}]
        state["endpoints"] = {
            "web": "http://127.0.0.1:49152/",
            "api": "http://127.0.0.1:49152/api/v1/tasks",
        }
        self.write_build_receipt(state, {"path": state["project_root"], "head": "a" * 40})
        lifecycle.update_state(self.root, state)
        target = {"path": state["project_root"], "head": "b" * 40, "phase": "sdlc-evaluate"}
        with (
            mock.patch.object(lifecycle, "execution_target", return_value=target),
            mock.patch.object(lifecycle.three_tier_browser, "run_stage") as launch,
            mock.patch.object(
                lifecycle.three_tier_runtime, "validate_acceptance_inventory"
            ),
        ):
            with self.assertRaisesRegex(lifecycle.LifecycleError, "deployment"):
                lifecycle.run_browser_stage(self.root, "evaluate")
            launch.assert_not_called()

    def test_browser_stage_rejects_wrong_api_path_before_attempt(self):
        state = self.prepare()
        state["browser_stages"] = [{"stage": "capability-discovery", "outcome": "PASS", "path": "evidence/capability.json", "sha256": "c" * 64}]
        state["endpoints"] = {
            "web": "http://127.0.0.1:49152/",
            "api": "http://127.0.0.1:49152/api/tasks",
            "health": "http://127.0.0.1:49152/healthz",
        }
        lifecycle.update_state(self.root, state)
        active = self.root / "three-tier-live/active.json"
        before = active.read_bytes()
        target = {"path": state["project_root"], "head": "a" * 40, "phase": "sdlc-evaluate"}
        with mock.patch.object(lifecycle, "browser_target", return_value=target), mock.patch.object(
            lifecycle.three_tier_browser, "run_stage"
        ) as launch:
            with self.assertRaisesRegex(lifecycle.LifecycleError, "API URL"):
                lifecycle.run_browser_stage(self.root, "evaluate")
            launch.assert_not_called()
        self.assertEqual(active.read_bytes(), before)

    def test_deployment_rechecks_running_image_mounts_and_endpoint(self):
        state = self.prepare()
        target = {"path": state["project_root"], "head": "a" * 40}
        self.write_build_receipt(state, target)
        image = "sha256:" + "a" * 64
        state["resources"].update(containers=["web-id", "db-id"], images=[image])
        state["endpoints"] = {name: "http://127.0.0.1:49152/" for name in ("web", "api", "health")}
        observed = {"Id": "web-id", "Image": image, "Mounts": [], "State": {"Running": True},
            "NetworkSettings": {"Ports": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49152"}]}}}
        owner = mock.Mock(return_value={"labels": {"com.docker.compose.service": "web"}})
        self.command.return_value = mock.Mock(returncode=0, stdout=json.dumps(observed))
        self.assertEqual(lifecycle.three_tier_runtime.validate_deployment(state, target, self.command, owner)["image_id"], image)
        for changed in ({"Image": "sha256:" + "c" * 64}, {"Mounts": [{"Destination": "/app"}]}, {"State": {"Running": False}}):
            with self.subTest(changed=changed):
                self.command.return_value.stdout = json.dumps({**observed, **changed})
                with self.assertRaises(lifecycle.three_tier_runtime.RuntimeEvidenceError):
                    lifecycle.three_tier_runtime.validate_deployment(state, target, self.command, owner)
        self.command.return_value.stdout = json.dumps(observed)
        state["endpoints"]["api"] = "http://127.0.0.1:49153/api/v1/tasks"
        with self.assertRaisesRegex(lifecycle.three_tier_runtime.RuntimeEvidenceError, "endpoint"):
            lifecycle.three_tier_runtime.validate_deployment(state, target, self.command, owner)

    def test_owned_build_records_image_and_rejects_failed_rebuild(self):
        state = self.prepare()
        target = {"path": state["project_root"], "head": "a" * 40}
        image = "sha256:" + "a" * 64
        self.command.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(lifecycle, "execution_target", return_value=target), mock.patch.object(
            lifecycle.three_tier_runtime, "compose_build_model", return_value=("owned-web", "b" * 64)
        ), mock.patch.object(lifecycle, "assert_resource_owned", return_value={"canonical_id": image}):
            lifecycle.run_owned_compose(state, ["build", "web"])
            proof = lifecycle.three_tier_runtime.load_build(state, state["runtime_build"], target)
            self.assertEqual(proof["image_id"], image)
            self.command.side_effect = [mock.Mock(returncode=0, stdout=""), mock.Mock(returncode=1, stdout="", stderr="failed")]
            with self.assertRaisesRegex(lifecycle.LifecycleError, "Compose action failed"):
                lifecycle.run_owned_compose(state, ["build", "web"])
            self.assertIsNone(lifecycle.load_active(self.root)[1]["runtime_build"])

    def test_failed_compose_retains_safe_diagnostics_and_nonzero_outcome(self):
        state = self.prepare()
        target = {"path": state["project_root"], "head": "a" * 40}
        self.command.return_value = mock.Mock(
            returncode=1, stdout="Ran 1 test\n", stderr="AssertionError: 500 != 200\n"
        )
        with mock.patch.object(lifecycle, "execution_target", return_value=target):
            with self.assertRaises(lifecycle.LifecycleError) as failure:
                lifecycle.run_owned_compose(state, ["exec", "-T", "web", "test"])
        self.assertIn("exit 1", str(failure.exception))
        self.assertIn("AssertionError: 500 != 200", str(failure.exception))

    def test_failed_compose_diagnostics_redact_before_bounding(self):
        state = self.prepare()
        target = {"path": state["project_root"], "head": "a" * 40}
        private_value = "synthetic-private-runtime-value"
        self.command.return_value = mock.Mock(
            returncode=7, stdout="x" * 20000,
            stderr="\x1b[31mAssertionError: " + private_value + "\x1b[0m\n",
        )
        with mock.patch.object(lifecycle, "execution_target", return_value=target), mock.patch.dict(
            os.environ, {"POSTGRES_PASSWORD": private_value, "DJANGO_SECRET_KEY": private_value}
        ):
            with self.assertRaises(lifecycle.LifecycleError) as failure:
                lifecycle.run_owned_compose(state, ["exec", "-T", "web", "test"])
        message = str(failure.exception)
        self.assertNotIn(private_value, message)
        self.assertNotIn("\x1b", message)
        self.assertIn("[REDACTED]", message)
        self.assertIn("AssertionError", message)
        self.assertLess(len(message), 8500)

    def test_failed_compose_withholds_credential_like_output(self):
        state = self.prepare()
        target = {"path": state["project_root"], "head": "a" * 40}
        private_value = "synthetic-unexpected-secret"
        self.command.return_value = mock.Mock(
            returncode=1, stdout="token=" + private_value + "\n" + "x" * 20000,
            stderr="AssertionError: failure\n",
        )
        with mock.patch.object(lifecycle, "execution_target", return_value=target):
            with self.assertRaises(lifecycle.LifecycleError) as failure:
                lifecycle.run_owned_compose(state, ["exec", "-T", "web", "test"])
        self.assertIn("withheld", str(failure.exception))
        self.assertNotIn(private_value, str(failure.exception))

    def test_compose_diagnostics_withhold_serialized_credentials(self):
        private_value = "synthetic-sensitive-value"
        for output in (
            json.dumps({"password": private_value}),
            json.dumps({"message": json.dumps({"password": private_value})}),
            json.dumps({"message": json.dumps({"AWS_SECRET_ACCESS_KEY": private_value})}),
            '{"passw\\u006frd": "' + private_value + '"}',
            "https://example.com/?%74oken=" + private_value,
            repr({"Authorization": "Bearer " + private_value}),
            "AWS_SECRET_ACCESS_KEY=" + private_value,
            "https://example.com/file?X-Amz-Signature=" + private_value,
            "https://example.com/file?sig=" + private_value,
            "Set-Cookie: session=" + private_value,
            json.dumps({"clientSecret": private_value}),
            "-----BEGIN PRIVATE KEY-----\n" + private_value,
        ):
            with self.subTest(format=output.partition(private_value)[0]):
                result = subprocess.CompletedProcess([], 1, stdout=output, stderr="failure")
                diagnostic = lifecycle.compose_failure_diagnostic(result)
                self.assertIn("withheld", diagnostic)
                self.assertNotIn(private_value, diagnostic)

    def test_owned_restart_refreshes_dynamic_port_before_deployment_check(self):
        state = self.prepare()
        target = {"path": state["project_root"], "head": "a" * 40}
        self.write_build_receipt(state, target)
        image = "sha256:" + "a" * 64
        state["resources"].update(containers=["web-id", "db-id"], images=[image], volumes=["owned-data"])
        state["endpoints"] = {
            "web": "http://127.0.0.1:49152/",
            "api": "http://127.0.0.1:49152/api/v1/tasks",
            "health": "http://127.0.0.1:49152/health",
            "database": "db:5432",
        }
        lifecycle.update_state(self.root, state)
        ports = {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49153"}]}
        observed = {"Id": "web-id", "Image": image, "Mounts": [], "State": {"Running": True},
                    "NetworkSettings": {"Ports": ports}}

        def command(argv, **kwargs):
            value = observed if argv[-1] == "{{json .}}" else ports if "web-id" in argv else {}
            return mock.Mock(returncode=0, stdout=json.dumps(value), stderr="")

        def owned(kind, identifier, current):
            return {"canonical_id": identifier, "labels": {"com.docker.compose.service": "web" if identifier == "web-id" else "db"}}

        receipts = [{"stage": "uat-before-restart", "attempt_id": "before"}]
        with mock.patch.object(lifecycle, "execution_target", return_value=target), mock.patch.object(
            lifecycle, "assert_resource_owned", side_effect=owned
        ), mock.patch.object(lifecycle, "command", side_effect=command), mock.patch.object(
            lifecycle.three_tier_browser, "validate_receipts", return_value=receipts
        ):
            lifecycle.run_owned_compose(state, ["restart"])
        updated = lifecycle.load_active(self.root)[1]
        self.assertEqual(updated["endpoints"]["api"], "http://127.0.0.1:49153/api/v1/tasks")
        self.assertEqual(updated["endpoints"]["database"], "db:5432")
        self.assertEqual(updated["browser_restart"]["after_attempt"], "before")
        self.assertEqual(updated["browser_restart"]["volumes"], ["owned-data"])
        observed["Image"] = "sha256:" + "c" * 64
        with mock.patch.object(lifecycle, "execution_target", return_value=target), mock.patch.object(
            lifecycle, "assert_resource_owned", side_effect=owned
        ), mock.patch.object(lifecycle, "command", side_effect=command), mock.patch.object(
            lifecycle.three_tier_browser, "validate_receipts", return_value=receipts
        ):
            with self.assertRaisesRegex(lifecycle.LifecycleError, "image changed"):
                lifecycle.run_owned_compose(state, ["restart"])
        rejected = lifecycle.load_active(self.root)[1]
        self.assertEqual(rejected["endpoints"], updated["endpoints"])
        self.assertNotIn("browser_restart", rejected)

    def test_restart_endpoint_refresh_rejects_unowned_or_public_binding(self):
        state = self.prepare()
        state["resources"]["containers"] = ["web-id", "db-id"]
        state["endpoints"] = {name: "http://127.0.0.1:49152/" for name in ("web", "api", "health")}
        original = dict(state["endpoints"])
        with mock.patch.object(lifecycle, "assert_resource_owned", side_effect=lifecycle.LifecycleError("ownership mismatch")):
            with self.assertRaisesRegex(lifecycle.LifecycleError, "ownership"):
                lifecycle.refreshed_restart_state(state)
        def owned(kind, identifier, current):
            return {"labels": {"com.docker.compose.service": "web" if identifier == "web-id" else "db"}}

        with mock.patch.object(lifecycle, "assert_resource_owned", side_effect=owned), mock.patch.object(
            lifecycle, "container_ports", return_value={"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49153"}]}
        ):
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.refreshed_restart_state(state)
        self.assertEqual(state["endpoints"], original)
        self.assertNotIn("browser_restart", state)

    def test_build_model_rejects_external_context_and_web_mounts(self):
        state = self.prepare()
        target = {"path": state["project_root"], "head": "a" * 40}
        (Path(target["path"]) / "Dockerfile").write_text("FROM scratch\n")
        web = {"build": {"context": target["path"]}}
        self.command.return_value = mock.Mock(returncode=0, stdout=json.dumps({"services": {"web": web}}))
        self.assertEqual(lifecycle.three_tier_runtime.compose_build_model(state, target, self.command)[0], state["compose_project"] + "-web")
        for invalid in ({"build": {"context": "/tmp"}}, {**web, "volumes": [".:/app"]}):
            self.command.return_value.stdout = json.dumps({"services": {"web": invalid}})
            with self.assertRaises(lifecycle.three_tier_runtime.RuntimeEvidenceError):
                lifecycle.three_tier_runtime.compose_build_model(state, target, self.command)

    def test_keep_retains_application_with_closed_browser(self):
        self.prepare()
        self.mark_browser_closed()
        result = lifecycle.finish(self.root, "FAIL", keep=True)
        self.assertEqual(result["status"], "KEPT")
        self.assertEqual(result["browser_instance"]["status"], "CLOSED")

    def test_phase_record_cannot_overwrite_browser_failure(self):
        state = self.prepare()
        state["environment"]["headless_browser"] = "FAIL"
        lifecycle.update_state(self.root, state)
        result = lifecycle.record_phase(self.root, "sdlc-uat-tests", "FAIL", "Browser assertion failed", [])
        self.assertEqual(result["environment"]["headless_browser"], "FAIL")

    def test_aggregate_profile_resolves_its_owned_lifecycle_and_independent_identity(self):
        import verify_agentic_sdlc as verifier

        state = self.prepare()
        self.write_valid_results(state)
        state["cleanup"]["status"] = "KEPT"
        lifecycle.update_state(self.root, state)
        canonical = Path(state["evidence_root"]) / "three-tier-results.json"
        copied = self.root / "collected-source.json"
        copied.write_bytes(canonical.read_bytes())
        copied.chmod(0o600)
        lifecycle.record_validation(self.root, "product acceptance", "PASS", "Fixture validation passed.")
        _, state = lifecycle.load_active(self.root)
        catalog = Path(__file__).resolve().parents[2]
        ctx = verifier.setup_context(verifier.parse_args([
            "--verification-root", str(self.root), "--global-skills-dir", str(catalog),
        ]))

        # This fixture has synthetic phase identities. Keep those semantic
        # controls in this test while exercising the subprocess handoff contract;
        # test_verification_catalog uses real isolated owners in both catalogs.
        def owner_call(arguments, **kwargs):
            self.assertEqual(arguments[:3], [sys.executable, "-B", str(catalog / "sdlc-workflow-test/scripts/three_tier_lifecycle.py")])
            self.assertEqual(arguments[3:], ["--verification-root", str(self.root),
                                            "--expected-verification-id", state["verification_id"], "validate-results"])
            try:
                value = lifecycle.validate_results(self.root, state["verification_id"])
            except (lifecycle.LifecycleError, lifecycle.SemanticEvidenceError,
                    lifecycle.owned_git_origin.OriginError, OSError, ValueError):
                return subprocess.CompletedProcess(arguments, 2, "", "Rejected synthetic evidence")
            return subprocess.CompletedProcess(arguments, 0, json.dumps(value), "")

        self.enterContext(mock.patch.object(verifier, "run", side_effect=owner_call))

        def claims(identity=state["verification_id"]):
            return verifier.validated_profile_claims(
                ctx, "three-tier", [copied], verification_id_value="f" * 64,
                source_identity=identity, baseline="a" * 40, final="b" * 40,
            )

        self.assertIsNotNone(claims())
        self.assertEqual(lifecycle.validate_results(self.root, state["verification_id"])["canonical_sha256"], hashlib.sha256(canonical.read_bytes()).hexdigest())
        original_attempts = json.loads(json.dumps(state["browser_stages"]))
        for stage in ("capability-discovery", "evaluate", "uat-before-restart", "uat-after-restart"):
            with self.subTest(failed_readiness=stage):
                state["browser_stages"] = json.loads(json.dumps(original_attempts))
                next(item for item in state["browser_stages"] if item["stage"] == stage)["outcome"] = "FAIL"
                lifecycle.update_state(self.root, state)
                self.assertIsNone(claims())
        state["browser_stages"] = original_attempts[:-1]
        lifecycle.update_state(self.root, state)
        self.assertIsNone(claims())
        state["browser_stages"] = original_attempts
        state["environment"]["headless_browser"] = "FAIL"
        lifecycle.update_state(self.root, state)
        self.assertIsNone(claims())
        state["environment"]["headless_browser"] = "PASS"
        lifecycle.update_state(self.root, state)
        self.assertIsNotNone(claims())
        self.assertIsNone(claims("e" * 32))
        copied.write_bytes(canonical.read_bytes() + b"\n")
        self.assertIsNone(claims())
        copied.write_bytes(canonical.read_bytes())
        canonical.chmod(0o644)
        self.assertIsNone(claims())
        canonical.chmod(0o600)
        alias = canonical.with_name("alias.json")
        os.link(canonical, alias)
        self.assertIsNone(claims())
        alias.unlink()
        canonical.rename(alias)
        canonical.symlink_to(alias)
        self.assertIsNone(claims())
        canonical.unlink()
        alias.rename(canonical)
        active = self.root / "three-tier-live" / "active.json"
        active.chmod(0o644)
        self.assertIsNone(claims())
        active.chmod(0o600)
        configuration = Path(state["project_root"]) / ".git/config"
        original_configuration = configuration.read_bytes()
        lifecycle.owned_git_origin._git(Path(state["project_root"]), "remote", "add", "foreign", "/unowned.git")
        self.assertIsNone(claims())
        # Fixture recovery cannot use a product command that now rejects the
        # intentionally corrupted repository before invoking Git.
        configuration.write_bytes(original_configuration)
        active.unlink()
        self.assertIsNone(claims())

    def test_catalog_binding_missing_tampered_and_wrong_native_installation_rejected(self):
        state = self.prepare()
        owner = lifecycle.runtime_catalog
        receipt = Path(state["run_root"]) / owner.RELATIVE
        raw = receipt.read_bytes()
        receipt.write_bytes(raw + b"\n")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "binding changed"):
            lifecycle.load_active(self.root)
        receipt.write_bytes(raw)
        missing = dict(state)
        missing.pop("runtime_catalog")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "binding is missing"):
            lifecycle.validate_state(self.root, missing)
        with mock.patch("agent_runtime.installed_skills_dir", return_value=self.root / "foreign-catalog"):
            with self.assertRaisesRegex(owner.CatalogError, "installed catalog"):
                owner.require_native_installation(state, "codex")
        with self.assertRaisesRegex(owner.CatalogError, "Claude catalog binding remains unqualified"):
            owner.require_native_installation(state, "claude")
        self.assertFalse((self.root / lifecycle.workspace.NATIVE).exists())
        self.assertEqual(receipt.read_bytes(), raw)

    def test_catalog_failure_precedes_new_run_effects_and_replacement_cleanup(self):
        with mock.patch.object(lifecycle.runtime_catalog, "snapshot", side_effect=lifecycle.runtime_catalog.CatalogError("catalog unavailable")):
            with self.assertRaisesRegex(lifecycle.LifecycleError, "catalog unavailable"):
                lifecycle.prepare(self.root)
        self.assertFalse(lifecycle.paths(self.root)["active"].exists())
        self.assertFalse(lifecycle.paths(self.root)["runs"].exists())
        self.reset_prepare_preflight()
        state = self.prepare()
        before = lifecycle.paths(self.root)["active"].read_bytes()
        self.reset_prepare_preflight()
        with mock.patch.object(lifecycle.runtime_catalog, "snapshot", side_effect=lifecycle.runtime_catalog.CatalogError("catalog unavailable")):
            with self.assertRaisesRegex(lifecycle.LifecycleError, "catalog unavailable"):
                lifecycle.prepare(self.root)
        self.assertEqual(lifecycle.paths(self.root)["active"].read_bytes(), before)
        self.assertEqual(list(lifecycle.paths(self.root)["runs"].iterdir()), [Path(state["run_root"])])

    def test_validate_results_is_read_only_and_rejects_failed_validations(self):
        state = self.prepare()
        self.write_valid_results(state)
        lifecycle.record_validation(self.root, "product acceptance", "PASS", "Executed fixture acceptance.")
        _, state = lifecycle.load_active(self.root)
        paths = [lifecycle.paths(self.root)["active"], Path(state["run_root"]) / "evidence/three-tier-results.json",
                 Path(state["project_root"]) / ".git/index"]
        before = {path: path.read_bytes() for path in paths}
        result = lifecycle.validate_results(self.root, state["verification_id"])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["catalog_root"], str(lifecycle.runtime_catalog.anchor()))
        self.assertEqual(before, {path: path.read_bytes() for path in paths})
        original = lifecycle.validate_semantic_results

        def concurrent_writer(value, *, keep):
            result = original(value, keep=keep)
            lifecycle.record_validation(self.root, "concurrent acceptance", "PASS", "Concurrent fixture writer.")
            return result

        with mock.patch.object(lifecycle, "validate_semantic_results", side_effect=concurrent_writer):
            with self.assertRaisesRegex(lifecycle.LifecycleError, "Lifecycle changed"):
                lifecycle.validate_results(self.root, state["verification_id"])
        lifecycle.record_validation(self.root, "another acceptance", "FAIL", "Retained fixture failure.")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "every recorded validation"):
            lifecycle.validate_results(self.root, state["verification_id"])

    def test_result_validation_subprocess_reads_under_parent_workspace_lock(self):
        state = self.prepare()
        before = lifecycle.paths(self.root)["active"].read_bytes()
        with lifecycle.workspace.operation(self.root):
            result = subprocess.run([
                sys.executable, "-B", str(MODULE_PATH), "--verification-root", str(self.root),
                "--expected-verification-id", state["verification_id"], "validate-results",
            ], text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Headless readiness is not passing", result.stderr)
        self.assertNotIn("WORKSPACE_BUSY", result.stderr)
        self.assertEqual(lifecycle.paths(self.root)["active"].read_bytes(), before)
        with lifecycle.workspace.operation(self.root):
            result = subprocess.run([
                sys.executable, "-B", str(MODULE_PATH), "--verification-root", str(self.root),
                "--expected-verification-id", "f" * 32, "validate-results",
            ], text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertIn("STALE_THREE_TIER_GENERATION", result.stderr)
        self.assertEqual(lifecycle.paths(self.root)["active"].read_bytes(), before)

    def test_prepare_creates_one_owned_private_run_and_report(self) -> None:
        state = self.prepare()
        self.assertEqual(state["status"], "PREPARED")
        self.assertEqual(state["scenario"], lifecycle.SCENARIO)
        lifecycle.runtime_catalog.validate(state)
        self.assertTrue((self.root / lifecycle.ROOT_MARKER).is_file())
        self.assertTrue(
            Path(state["run_root"]).joinpath(lifecycle.RUN_MARKER).is_file()
        )
        self.assertTrue(
            Path(state["project_root"]).joinpath(lifecycle.PROJECT_MARKER).is_file()
        )
        report = Path(state["report_path"]).read_text(encoding="utf-8")
        self.assertIn("Frontend GUI", report)
        self.assertIn("PostgreSQL database", report)
        self.assertIn("Project retention state: pending or cleanup incomplete", report)
        self.assertIn(f"--verification-root {self.root} --destroy", report)

    def test_second_prepare_destroys_and_replaces_active_run(self) -> None:
        first = self.prepare()
        first_report = Path(first["report_path"])
        self.reset_prepare_preflight()
        second = lifecycle.prepare(self.root)
        _, current = lifecycle.load_active(self.root)
        self.assertEqual(current["verification_id"], second["verification_id"])
        self.assertNotEqual(second["verification_id"], first["verification_id"])
        self.assertFalse(Path(first["run_root"]).exists())
        self.assertTrue(first_report.is_file())
        self.assertTrue(
            self.root.joinpath(
                "three-tier-live",
                "lifecycle",
                f"{first['verification_id']}.json",
            ).is_file()
        )

    def test_second_prepare_removes_every_recorded_owned_resource(self) -> None:
        first = self.prepare()
        first["status"] = "READY_FOR_CLEANUP"
        first["resources"] = {
            "containers": ["web-id", "db-id"],
            "networks": ["network-id"],
            "volumes": ["volume-id"],
            "images": ["image-id"],
        }
        lifecycle.update_state(self.root, first)
        owned_labels = {
            lifecycle.OWNERSHIP_LABEL: first["verification_id"],
            lifecycle.COMPOSE_LABEL: first["compose_project"],
        }
        present = {
            (kind, identifier)
            for kind, identifiers in first["resources"].items()
            for identifier in identifiers
        }
        removed: list[tuple[str, str]] = []

        def resource(kind: str, identifier: str) -> dict[str, object] | None:
            if (kind, identifier) not in present:
                return None
            return {"canonical_id": identifier, "labels": owned_labels}

        def remove(kind: str, identifier: str) -> str:
            removed.append((kind, identifier))
            present.remove((kind, identifier))
            return "REMOVED"

        self.reset_prepare_preflight()
        with (
            mock.patch.object(lifecycle, "inspect_resource", side_effect=resource),
            mock.patch.object(lifecycle, "remove_resource", side_effect=remove),
        ):
            second = lifecycle.prepare(self.root)

        self.assertNotEqual(second["verification_id"], first["verification_id"])
        self.assertEqual(
            removed,
            [
                ("containers", "web-id"),
                ("containers", "db-id"),
                ("networks", "network-id"),
                ("volumes", "volume-id"),
                ("images", "image-id"),
            ],
        )
        self.assertFalse(present)

    def test_second_prepare_removes_owned_resources_created_before_inventory(
        self,
    ) -> None:
        first = self.prepare()
        unrecorded = {
            "containers": ["web-id", "db-id"],
            "networks": ["network-id"],
            "volumes": ["volume-id"],
            "images": ["image-id"],
        }
        owned_labels = {
            lifecycle.OWNERSHIP_LABEL: first["verification_id"],
            lifecycle.COMPOSE_LABEL: first["compose_project"],
        }
        present = {
            (kind, identifier)
            for kind, identifiers in unrecorded.items()
            for identifier in identifiers
        }

        def resource(kind: str, identifier: str) -> dict[str, object] | None:
            if (kind, identifier) not in present:
                return None
            return {"canonical_id": identifier, "labels": owned_labels}

        def remove(kind: str, identifier: str) -> str:
            present.remove((kind, identifier))
            return "REMOVED"

        self.reset_prepare_preflight()
        with (
            mock.patch.object(
                lifecycle, "discover_owned_resources", return_value=unrecorded
            ),
            mock.patch.object(lifecycle, "inspect_resource", side_effect=resource),
            mock.patch.object(lifecycle, "remove_resource", side_effect=remove),
        ):
            second = lifecycle.prepare(self.root)

        self.assertNotEqual(second["verification_id"], first["verification_id"])
        self.assertFalse(present)

    def test_discovery_uses_both_exact_ownership_labels(self) -> None:
        state = self.prepare()
        outputs = ["web-id\ndb-id\n", "network-id\n", "volume-id\n", "image-id\n"]
        self.command.side_effect = [
            mock.Mock(returncode=0, stdout=output, stderr="") for output in outputs
        ]
        discovered = lifecycle.discover_owned_resources(state)
        self.assertEqual(discovered["containers"], ["web-id", "db-id"])
        for call in self.command.call_args_list:
            arguments = call.args[0]
            self.assertIn(
                f"label={lifecycle.OWNERSHIP_LABEL}={state['verification_id']}",
                arguments,
            )
            self.assertIn(
                f"label={lifecycle.COMPOSE_LABEL}={state['compose_project']}",
                arguments,
            )

    def test_second_prepare_preserves_active_run_when_preflight_fails(self) -> None:
        first = self.prepare()
        self.require_command.side_effect = lifecycle.LifecycleError(
            "Docker Engine preflight failed"
        )
        with self.assertRaisesRegex(lifecycle.LifecycleError, "preflight failed"):
            lifecycle.prepare(self.root)
        _, current = lifecycle.load_active(self.root)
        self.assertEqual(current["verification_id"], first["verification_id"])
        self.assertTrue(Path(first["run_root"]).is_dir())

    def test_second_prepare_blocks_when_owned_cleanup_cannot_be_proven(self) -> None:
        first = self.prepare()
        first["status"] = "READY_FOR_CLEANUP"
        first["resources"]["containers"] = ["foreign-id"]
        lifecycle.update_state(self.root, first)
        foreign = {
            lifecycle.OWNERSHIP_LABEL: "different-run",
            lifecycle.COMPOSE_LABEL: first["compose_project"],
        }
        self.reset_prepare_preflight()
        with (
            mock.patch.object(
                lifecycle,
                "inspect_resource",
                return_value={"canonical_id": "foreign-id", "labels": foreign},
            ),
            mock.patch.object(lifecycle, "remove_resource") as remove,
            self.assertRaisesRegex(
                lifecycle.LifecycleError, "Ownership label mismatch"
            ),
        ):
            lifecycle.prepare(self.root)
        remove.assert_not_called()
        _, failed = lifecycle.load_active(self.root)
        self.assertEqual(failed["verification_id"], first["verification_id"])
        self.assertEqual(failed["status"], "CLEANUP_FAILED")

    def test_prepare_refuses_orphaned_owned_run(self) -> None:
        state = self.prepare()
        (self.root / "three-tier-live" / "active.json").unlink()
        self.reset_prepare_preflight()
        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "ORPHANED_THREE_TIER_RUN"
        ):
            lifecycle.prepare(self.root)
        self.assertTrue(Path(state["run_root"]).is_dir())

    def test_destroy_missing_root_is_idempotent(self) -> None:
        result, state = lifecycle.destroy(self.root)
        self.assertEqual(result, "ALREADY_DESTROYED")
        self.assertIsNone(state)
        self.assertFalse(self.root.exists())

    def test_existing_unowned_root_fails_closed(self) -> None:
        self.root.mkdir()
        (self.root / "preserve.txt").write_text("keep\n", encoding="utf-8")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "private directory|not owned|invalid or unsafe"):
            lifecycle.destroy(self.root)
        self.assertTrue((self.root / "preserve.txt").is_file())

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlinks are required")
    def test_destroy_rejects_symlinked_active_pointer(self) -> None:
        state = self.prepare()
        active = self.root / "three-tier-live" / "active.json"
        target = self.root / "three-tier-live" / "active-target.json"
        active.replace(target)
        active.symlink_to(target)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "Symlinked"):
            lifecycle.destroy(self.root)
        self.assertTrue(Path(state["run_root"]).is_dir())

    def inventory_fixture(self, state):
        from test_three_tier_runtime import InventoryFixture

        fixture = InventoryFixture()
        fixture.state = state
        fixture.target = {
            "path": state["project_root"],
            "head": state["git"]["baseline_sha"],
        }
        for value in fixture.records.values():
            labels = value.get("Config", value).get("Labels", {})
            labels.update(
                {
                    lifecycle.OWNERSHIP_LABEL: state["verification_id"],
                    lifecycle.COMPOSE_LABEL: state["compose_project"],
                }
            )
        old = fixture.image
        fixture.image = "sha256:" + "a" * 64
        fixture.records[("image", fixture.image)] = fixture.records.pop(("image", old))
        fixture.records[("image", fixture.image)]["Id"] = fixture.image
        fixture.records[("container", "web")]["Image"] = fixture.image
        self.write_build_receipt(state, fixture.target)
        lifecycle.update_state(self.root, state)
        return fixture

    def test_record_runtime_discovers_and_persists_bound_acceptance(self):
        state = self.prepare()
        fixture = self.inventory_fixture(state)
        with (
            mock.patch.object(
                lifecycle, "execution_target", return_value=fixture.target
            ),
            mock.patch.object(lifecycle, "command", side_effect=fixture.command),
        ):
            updated = lifecycle.record_runtime(
                self.root, web_path="/", health_path="/healthz"
            )
        self.assertEqual(updated["resources"]["containers"], ["web", "db"])
        self.assertEqual(
            updated["endpoints"]["api"], "http://127.0.0.1:49152/api/v1/tasks"
        )
        lifecycle.three_tier_runtime.validate_acceptance_inventory(
            updated, fixture.target
        )
        self.assertEqual(updated["runtime_observations"][0]["mode"], "acceptance")

    def test_runtime_routes_reject_before_inspection_or_state_write(self):
        self.prepare()
        active = self.root / "three-tier-live/active.json"
        before = active.read_bytes()
        for path in (
            "http://127.0.0.1:1234/",
            "//other",
            "/?query",
            "/#fragment",
            "/\n",
        ):
            with (
                self.subTest(path=path),
                mock.patch.object(lifecycle, "execution_target") as target,
            ):
                with self.assertRaises(lifecycle.LifecycleError):
                    lifecycle.record_runtime(
                        self.root, web_path=path, health_path="/healthz"
                    )
                target.assert_not_called()
                self.assertEqual(active.read_bytes(), before)

    def test_failed_observation_is_immutable_and_does_not_publish_inventory(self):
        state = self.prepare()
        fixture = self.inventory_fixture(state)
        fixture.records[("container", "web")]["Mounts"] = [
            {"Type": "bind", "Source": "password=secret"}
        ]
        with (
            mock.patch.object(
                lifecycle, "execution_target", return_value=fixture.target
            ),
            mock.patch.object(lifecycle, "command", side_effect=fixture.command),
        ):
            with self.assertRaisesRegex(lifecycle.LifecycleError, "web.mounts_absent"):
                lifecycle.record_runtime(
                    self.root, web_path="/", health_path="/healthz"
                )
            failed = lifecycle.load_active(self.root)[1]
            observation = failed["runtime_observations"][0]
            raw = (Path(state["run_root"]) / observation["path"]).read_bytes()
            fixture.records[("container", "web")]["Mounts"] = []
            with self.assertRaisesRegex(lifecycle.LifecycleError, "ends this trial"):
                lifecycle.record_runtime(
                    self.root, web_path="/", health_path="/healthz"
                )
        self.assertEqual(failed["resources"], state["resources"])
        self.assertEqual(failed["endpoints"], {})
        self.assertIsNone(failed["runtime_inventory"])
        self.assertEqual(
            raw, (Path(state["run_root"]) / observation["path"]).read_bytes()
        )
        self.assertNotIn(b"secret", raw)
        self.assertIn("web.mounts_absent", Path(state["report_path"]).read_text())
        self.assertNotIn("Playwright report", Path(state["report_path"]).read_text())

    def test_failed_inventory_diagnostics_survive_interrupted_and_successful_cleanup(
        self,
    ):
        state = self.prepare()
        with mock.patch.object(
            lifecycle,
            "execution_target",
            side_effect=lifecycle.LifecycleError("password=secret"),
        ):
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.record_runtime(
                    self.root, web_path="/", health_path="/healthz"
                )
        summary = lifecycle.load_active(self.root)[1]["runtime_observations"]
        with mock.patch.object(
            lifecycle,
            "discover_owned_resources",
            side_effect=lifecycle.LifecycleError("daemon unavailable"),
        ):
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.destroy(self.root)
        self.assertEqual(
            lifecycle.load_active(self.root)[1]["runtime_observations"], summary
        )
        with mock.patch.object(
            lifecycle,
            "discover_owned_resources",
            return_value={kind: [] for kind in lifecycle.RESOURCE_KINDS},
        ):
            result, archived = lifecycle.destroy(self.root)
        self.assertEqual(result, "DESTROYED")
        self.assertFalse(Path(state["run_root"]).exists())
        self.assertEqual(archived["runtime_observations"], summary)
        report = Path(state["report_path"]).read_text()
        self.assertIn("target.admitted", report)
        self.assertIn(summary[0]["sha256"], report)
        self.assertNotIn("secret", report)

    def test_worker_observations_are_development_only_and_unknown_failure_stays_blocked(
        self,
    ):
        from contextlib import nullcontext

        state = self.prepare()
        fixture = self.inventory_fixture(state)
        with (
            mock.patch.object(
                lifecycle, "runtime_access", return_value=nullcontext()
            ) as access,
            mock.patch.object(
                lifecycle, "execution_target", return_value=fixture.target
            ),
            mock.patch.object(lifecycle, "command", side_effect=fixture.command),
        ):
            updated = lifecycle.record_runtime(
                self.root,
                web_path="/",
                health_path="/healthz",
                worker_task="TASK-001",
                assignment_digest="d" * 64,
            )
        access.assert_called_once_with(
            mock.ANY, worker_task="TASK-001", assignment_digest="d" * 64
        )
        self.assertEqual(updated["resources"], state["resources"])
        self.assertEqual(updated["endpoints"], {})
        self.assertIsNone(updated["runtime_inventory"])
        with self.assertRaises(lifecycle.three_tier_runtime.RuntimeEvidenceError):
            lifecycle.three_tier_runtime.validate_acceptance_inventory(updated)
        with mock.patch.object(
            lifecycle,
            "runtime_access",
            side_effect=lifecycle.LifecycleError("missing lease"),
        ):
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.record_runtime(
                    self.root,
                    web_path="/",
                    health_path="/healthz",
                    worker_task="TASK-001",
                    assignment_digest="d" * 64,
                )
        failed = lifecycle.load_active(self.root)[1]
        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "blocked_missing_evidence"
        ):
            lifecycle.validate_inventory_recovery(failed)

    def test_failed_receipt_orphan_is_reconciled_before_retry_and_cleanup(self):
        state = self.prepare()
        fixture = self.inventory_fixture(state)
        fixture.records[("container", "web")]["Mounts"] = [{"Type": "bind"}]
        original_update = lifecycle.update_state

        def interrupted(root, value):
            if value["runtime_observations"]:
                raise OSError("simulated interruption after receipt publication")
            original_update(root, value)

        with (
            mock.patch.object(
                lifecycle, "execution_target", return_value=fixture.target
            ),
            mock.patch.object(lifecycle, "command", side_effect=fixture.command),
            mock.patch.object(lifecycle, "update_state", side_effect=interrupted),
        ):
            with self.assertRaises(OSError):
                lifecycle.record_runtime(
                    self.root, web_path="/", health_path="/healthz"
                )
        saved = lifecycle.load_active(self.root)[1]
        self.assertEqual(saved["runtime_observations"], [])
        with self.assertRaisesRegex(
            lifecycle.three_tier_runtime.RuntimeEvidenceError, "reconciliation"
        ):
            lifecycle.three_tier_runtime.validate_observations(saved)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "ends this trial"):
            lifecycle.record_runtime(self.root, web_path="/", health_path="/healthz")
        reconciled = lifecycle.load_active(self.root)[1]
        self.assertEqual(
            reconciled["runtime_observations"][0]["predicates"][-1]["name"],
            "web.mounts_absent",
        )
        result, archived = lifecycle.destroy(self.root)
        self.assertEqual(result, "DESTROYED")
        self.assertEqual(
            archived["runtime_observations"], reconciled["runtime_observations"]
        )

    def test_cleanup_resumes_after_raw_observation_was_deleted(self):
        state = self.prepare()
        with mock.patch.object(
            lifecycle,
            "execution_target",
            side_effect=lifecycle.LifecycleError("target unavailable"),
        ):
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.record_runtime(
                    self.root, web_path="/", health_path="/healthz"
                )
        saved = lifecycle.load_active(self.root)[1]
        receipt = Path(saved["run_root"]) / saved["runtime_observations"][0]["path"]

        def partial_delete(path):
            receipt.unlink()
            (Path(state["run_root"]) / lifecycle.runtime_catalog.RELATIVE).unlink()
            raise OSError("simulated interrupted deletion")

        with mock.patch.object(lifecycle.shutil, "rmtree", side_effect=partial_delete):
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.destroy(self.root)
        interrupted = lifecycle.load_active(self.root)[1]
        self.assertEqual(
            interrupted["runtime_observations"], saved["runtime_observations"]
        )
        self.assertIn("target.admitted", Path(saved["report_path"]).read_text())
        with self.assertRaisesRegex(lifecycle.LifecycleError, "only status/destroy"):
            lifecycle.assert_active_generation(self.root, state["verification_id"])
        result, archived = lifecycle.destroy(self.root)
        self.assertEqual(result, "DESTROYED")
        self.assertEqual(
            archived["runtime_observations"], saved["runtime_observations"]
        )
        self.assertFalse(Path(state["run_root"]).exists())

    def test_cleanup_checkpoint_rejects_changed_diagnostics_and_reappeared_runtime(
        self,
    ):
        state = self.prepare()
        with mock.patch.object(
            lifecycle.shutil, "rmtree", side_effect=OSError("interrupted")
        ):
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.destroy(self.root)
        saved = lifecycle.load_active(self.root)[1]
        with mock.patch.object(lifecycle, "agent_name", return_value="claude"):
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.validate_filesystem_cleanup(self.root, saved)
        tampered = {**saved, "runtime_observations": [{"injected": True}]}
        with self.assertRaisesRegex(lifecycle.LifecycleError, "changed"):
            lifecycle.validate_filesystem_cleanup(self.root, tampered)
        with mock.patch.object(
            lifecycle,
            "discover_owned_resources",
            return_value={"containers": ["new-writer"]},
        ):
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.destroy(self.root)
        self.assertTrue(Path(state["run_root"]).exists())

    def test_cleanup_rejects_replacement_directory(self):
        state = self.prepare()
        with mock.patch.object(
            lifecycle.shutil, "rmtree", side_effect=OSError("interrupted")
        ):
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.destroy(self.root)
        run = Path(state["run_root"])
        run.rename(run.with_name("preserved-original"))
        run.mkdir(mode=0o700)
        sentinel = run / "unrelated.txt"
        sentinel.write_text("preserve")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "replaced"):
            lifecycle.destroy(self.root)
        self.assertEqual(sentinel.read_text(), "preserve")

    def test_cleanup_reconciles_exact_unbound_checkpoint(self):
        self.prepare()
        original_update = lifecycle.update_state

        def interrupt(root, state):
            if state.get("filesystem_cleanup"):
                raise OSError("interrupted binding publication")
            original_update(root, state)

        with mock.patch.object(lifecycle, "update_state", side_effect=interrupt):
            with self.assertRaises(OSError):
                lifecycle.destroy(self.root)
        self.assertNotIn("filesystem_cleanup", lifecycle.load_active(self.root)[1])
        self.assertEqual(lifecycle.destroy(self.root)[0], "DESTROYED")

    def test_product_failure_is_not_overwritten_by_a_later_target_failure(self):
        state = self.prepare()
        fixture = self.inventory_fixture(state)
        fixture.records[("container", "web")]["Mounts"] = [{"Type": "bind"}]
        with (
            mock.patch.object(
                lifecycle,
                "execution_target",
                side_effect=[fixture.target, lifecycle.LifecycleError("late drift")],
            ) as target,
            mock.patch.object(lifecycle, "command", side_effect=fixture.command),
        ):
            with self.assertRaisesRegex(lifecycle.LifecycleError, "web.mounts_absent"):
                lifecycle.record_runtime(
                    self.root, web_path="/", health_path="/healthz"
                )
        target.assert_called_once()

    def test_tampered_observation_blocks_admission_and_cleanup(self):
        state = self.prepare()
        fixture = self.inventory_fixture(state)
        with (
            mock.patch.object(
                lifecycle, "execution_target", return_value=fixture.target
            ),
            mock.patch.object(lifecycle, "command", side_effect=fixture.command),
        ):
            saved = lifecycle.record_runtime(
                self.root, web_path="/", health_path="/healthz"
            )
        receipt = Path(state["run_root"]) / saved["runtime_observations"][0]["path"]
        receipt.write_text(receipt.read_text().replace('"PASS"', '"FAIL"'))
        with self.assertRaisesRegex(lifecycle.LifecycleError, "digest"):
            lifecycle.destroy(self.root)
        self.assertTrue(Path(state["run_root"]).exists())

    def test_record_git_can_capture_clean_baseline_before_promotion(self) -> None:
        state = self.prepare()
        baseline_sha = state["git"]["baseline_sha"]
        self.require_command.side_effect = [baseline_sha, ""]
        updated = lifecycle.record_git(self.root, baseline_sha, None)
        self.assertEqual(
            updated["git"],
            {"baseline_sha": baseline_sha, "promoted_sha": None},
        )

    def test_prepare_public_images_uses_private_config_and_fixed_images(self) -> None:
        state = self.prepare()
        inspect_missing = mock.Mock(returncode=1, stdout="", stderr="missing")
        pull_ok = mock.Mock(returncode=0, stdout="pulled", stderr="")
        with (
            mock.patch.object(
                lifecycle, "require_command", return_value="desktop-linux"
            ),
            mock.patch.object(
                lifecycle,
                "command",
                side_effect=[inspect_missing, pull_ok, inspect_missing, pull_ok],
            ) as run,
        ):
            updated = lifecycle.prepare_public_images(self.root)
        config = Path(state["private_root"]) / "docker-config" / "config.json"
        self.assertEqual(json.loads(config.read_text(encoding="utf-8")), {})
        if sys.platform != "win32":
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        pull_commands = [call.args[0] for call in run.call_args_list[1::2]]
        self.assertEqual(
            [command[-1] for command in pull_commands],
            list(lifecycle.PUBLIC_BASE_IMAGES),
        )
        self.assertTrue(all("--config" in command for command in pull_commands))
        self.assertEqual(
            updated["environment"]["public_base_images"],
            list(lifecycle.PUBLIC_BASE_IMAGES),
        )


    def test_passing_phase_requires_canonical_semantic_result(self) -> None:
        state = self.prepare()
        state["git"] = {
            "baseline_sha": "a" * 40,
            "promoted_sha": "b" * 40,
        }
        self.write_startup_fixture(state)
        lifecycle.update_state(self.root, state)
        run_root = Path(state["run_root"])
        relative = "evidence/phases/sdlc-create-requirements.json"
        artifact = run_root / relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            json.dumps(
                {
                    "schema": lifecycle.PHASE_RESULT_SCHEMA,
                    "phase": "sdlc-create-requirements",
                    "status": "PASS",
                    "verification_id": state["verification_id"],
                    "baseline_sha": state["git"]["baseline_sha"],
                    "recorded_head": state["git"]["promoted_sha"],
                    "objective_sha256": "a"*64,
                    "basis_sha256": None,
                    "assertions": lifecycle.PHASE_REQUIRED_ASSERTIONS[
                        "sdlc-create-requirements"
                    ],
                }
            ),
            encoding="utf-8",
        )
        os.chmod(artifact, 0o600)
        updated = lifecycle.record_phase(
            self.root,
            "sdlc-create-requirements",
            "PASS",
            "Requirements passed.",
            [relative],
        )
        self.assertEqual(updated["phases"][0]["status"], "PASS")
        artifact.write_text('{"result":"pass"}\n', encoding="utf-8")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "semantic result"):
            lifecycle.record_phase(
                self.root,
                "sdlc-create-requirements",
                "PASS",
                "Requirements passed.",
                [relative],
            )








    def test_phase_rejects_changed_basis_with_unchanged_git_head(self):
        state = self.prepare()
        self.write_valid_results(state)
        phase = "sdlc-implement-plan"
        with mock.patch.object(lifecycle, 'phase_identity', return_value=('a'*64,'e'*64)):
            with self.assertRaisesRegex(lifecycle.LifecycleError, 'basis is stale'):
                lifecycle.validate_phase_pass_artifact(state, phase, [f'evidence/phases/{phase}.json'])

    def test_phase_ledger_preserves_failed_and_superseded_attempts(self):
        state = self.prepare()
        for status in ('FAIL', 'PARTIAL'):
            state = lifecycle.record_phase(self.root, 'sdlc-tdd', status, 'retained offline attempt', [])
        self.assertEqual([item['status'] for item in state['phases']], ['FAIL','PARTIAL'])
        self.assertEqual([item['attempt'] for item in state['phases']], [1,2])
        self.assertEqual(state['phases'][-1]['supersedes_attempt'], 1)

    def test_pass_rejects_placeholder_semantic_evidence(self) -> None:
        state = self.prepare()
        evidence = Path(state["evidence_root"]) / "three-tier-results.json"
        evidence.write_text('{"result":"pass"}\n', encoding="utf-8")
        with self.assertRaisesRegex(
            lifecycle.SemanticEvidenceError, "fields are invalid"
        ):
            lifecycle.validate_semantic_results(state, keep=True)

    def test_semantic_pass_can_finish_as_kept(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        lifecycle.record_validation(
            self.root,
            "python3 manage.py test",
            "PASS",
            "All required application tests passed.",
        )
        self.mark_browser_closed()
        with (
            mock.patch.object(lifecycle, "assert_resource_owned"),
            mock.patch.object(lifecycle, "assert_port_isolation"),
        ):
            finished = lifecycle.finish(self.root, "PASS", keep=True)
        self.assertEqual(finished["status"], "KEPT")
        self.assertEqual(finished["result"], "PASS")
        report = Path(finished["report_path"]).read_text(encoding="utf-8")
        self.assertIn("## Test results", report)
        self.assertIn("## GUI UAT", report)
        self.assertIn("API/database correlation: PASS", report)
        self.assertIn("Retained owned resources: 5", report)

    def test_semantic_pass_rejects_missing_startup_registration_proof(self):
        state = self.prepare()
        self.write_valid_results(state)
        state.pop("workflow_startup", None)
        with self.assertRaisesRegex(lifecycle.SemanticEvidenceError, "startup"):
            lifecycle.validate_semantic_results(state, keep=True)

    def test_semantic_pass_rejects_modified_startup_registration_proof(self):
        state = self.prepare()
        self.write_valid_results(state)
        artifact = Path(state["run_root"]) / state["workflow_startup"]["path"]
        artifact.write_bytes(artifact.read_bytes() + b"\n")
        with self.assertRaisesRegex(lifecycle.SemanticEvidenceError, "startup receipt digest"):
            lifecycle.validate_semantic_results(state, keep=True)

    def test_final_pass_revalidates_canonical_phase_artifact(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        artifact = Path(state["run_root"]) / state["phases"][0]["evidence"][0]
        artifact.write_text('{"result":"pass"}\n', encoding="utf-8")
        lifecycle.record_validation(
            self.root,
            "python3 manage.py test",
            "PASS",
            "Tests passed.",
        )
        self.mark_browser_closed()
        with (
            mock.patch.object(lifecycle, "assert_resource_owned"),
            mock.patch.object(lifecycle, "assert_port_isolation"),
            self.assertRaisesRegex(lifecycle.LifecycleError, "semantic result"),
        ):
            lifecycle.finish(self.root, "PASS", keep=True)

    @unittest.skipUnless(os.name == "posix", "hard-link safety requires POSIX")
    def test_phase_pass_rejects_hard_linked_artifact(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        phase = lifecycle.REQUIRED_SDLC_PHASES[0]
        relative = f"evidence/phases/{phase}.json"
        artifact = Path(state["run_root"]) / relative
        os.link(artifact, Path(state["run_root"]) / "linked-phase.json")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "unsafe"):
            lifecycle.validate_phase_pass_artifact(state, phase, [relative])

    def test_phase_pass_rejects_stale_git_identity(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        phase = lifecycle.REQUIRED_SDLC_PHASES[0]
        relative = f"evidence/phases/{phase}.json"
        artifact = Path(state["run_root"]) / relative
        value = json.loads(artifact.read_text(encoding="utf-8"))
        value["recorded_head"] = "c" * 40
        artifact.write_text(json.dumps(value), encoding="utf-8")
        self.command.return_value = mock.Mock(returncode=1, stdout="", stderr="")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "not an ancestor"):
            lifecycle.validate_phase_pass_artifact(state, phase, [relative])

    def test_failed_finish_reports_partial_semantic_progress(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        results_path = Path(state["evidence_root"]) / "three-tier-results.json"
        results = json.loads(results_path.read_text(encoding="utf-8"))
        results["layers"]["frontend"] = "FAIL"
        results["tests"]["gui"] = {
            "status": "FAIL",
            "assertions": 0,
            "evidence": [],
        }
        results["sdlc_phases"]["sdlc-evaluate"] = "FAIL"
        results["gui_uat"] = {
            "harness": "playwright-test",
                "headless": True,
            "browser": "chrome",
            "steps": [],
            "api_db_correlated": False,
            "restart_persistence": False,
            "screenshots": [],
        }
        results_path.write_text(json.dumps(results), encoding="utf-8")
        finished = lifecycle.finish(self.root, "FAIL", keep=True)
        self.assertEqual(
            finished["semantic_summary"]["tests"]["unit"]["status"], "PASS"
        )
        self.assertEqual(finished["semantic_summary"]["tests"]["gui"]["status"], "FAIL")
        report = Path(finished["report_path"]).read_text(encoding="utf-8")
        self.assertIn("| unit | PASS |", report)
        self.assertIn("| gui | FAIL |", report)

    def test_pre_promotion_partial_semantics_allow_missing_git_shas(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        results_path = Path(state["evidence_root"]) / "three-tier-results.json"
        results = json.loads(results_path.read_text(encoding="utf-8"))
        results["git"] = {"baseline_sha": None, "promoted_sha": None, "clean": True}
        results["layers"] = {
            "frontend": "NOT_RUN",
            "web": "NOT_RUN",
            "database": "NOT_RUN",
        }
        results["tests"] = {
            name: {"status": "NOT_RUN", "assertions": 0, "evidence": []}
            for name in results["tests"]
        }
        results["sdlc_phases"] = {
            phase: "NOT_RUN" for phase in lifecycle.REQUIRED_SDLC_PHASES
        }
        results["gui_uat"] = {
            "harness": "playwright-test",
                "headless": True,
            "browser": "chrome",
            "steps": [],
            "api_db_correlated": False,
            "restart_persistence": False,
            "screenshots": [],
        }
        results_path.write_text(json.dumps(results), encoding="utf-8")
        _, current = lifecycle.load_active(self.root)
        current["git"] = {"baseline_sha": None, "promoted_sha": None}
        lifecycle.update_state(self.root, current)
        finished = lifecycle.finish(self.root, "PARTIAL", keep=True)
        self.assertIsNotNone(finished["semantic_summary"])

    def test_partial_summary_rejects_unasserted_test_pass(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        results_path = Path(state["evidence_root"]) / "three-tier-results.json"
        results = json.loads(results_path.read_text(encoding="utf-8"))
        results["tests"]["unit"]["assertions"] = 0
        results_path.write_text(json.dumps(results), encoding="utf-8")
        finished = lifecycle.finish(self.root, "PARTIAL", keep=True)
        self.assertIsNone(finished["semantic_summary"])

    def test_resume_reopens_only_kept_failed_or_partial_run(self) -> None:
        self.prepare()
        finished = lifecycle.finish(self.root, "PARTIAL", keep=True)
        self.assertIn("finished_at", finished)
        resumed = lifecycle.resume(self.root)
        self.assertEqual(resumed["status"], "RUNNING")
        self.assertEqual(resumed["result"], "PARTIAL")
        self.assertNotIn("finished_at", resumed)

    def test_resume_rejects_kept_pass(self) -> None:
        state = self.prepare()
        state["status"] = "KEPT"
        state["result"] = "PASS"
        lifecycle.update_state(self.root, state)
        with self.assertRaisesRegex(
            lifecycle.LifecycleError,
            "RESUME_REQUIRES_KEPT_FAILED_OR_PARTIAL_RUN",
        ):
            lifecycle.resume(self.root)

    def test_resume_rejects_missing_recorded_runtime_resource(self) -> None:
        state = self.prepare()
        state["status"] = "KEPT"
        state["result"] = "PARTIAL"
        state["resources"]["containers"] = ["missing-web"]
        lifecycle.update_state(self.root, state)
        with (
            mock.patch.object(lifecycle, "inspect_labels", return_value=None),
            self.assertRaisesRegex(lifecycle.LifecycleError, "resource is missing"),
        ):
            lifecycle.resume(self.root)

    def test_pass_requires_recorded_passing_validations(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        self.mark_browser_closed()
        with (
            mock.patch.object(lifecycle, "assert_resource_owned"),
            mock.patch.object(lifecycle, "assert_port_isolation"),
            self.assertRaisesRegex(
                lifecycle.LifecycleError,
                "every recorded validation to pass",
            ),
        ):
            lifecycle.finish(self.root, "PASS", keep=True)

        lifecycle.record_validation(
            self.root,
            "python3 manage.py test",
            "FAIL",
            "A required application test failed.",
        )
        with (
            mock.patch.object(lifecycle, "assert_resource_owned"),
            mock.patch.object(lifecycle, "assert_port_isolation"),
            self.assertRaisesRegex(
                lifecycle.LifecycleError,
                "every recorded validation to pass",
            ),
        ):
            lifecycle.finish(self.root, "PASS", keep=True)

    def test_semantic_rejects_out_of_order_gui_steps(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        results_path = Path(state["evidence_root"]) / "three-tier-results.json"
        results = json.loads(results_path.read_text(encoding="utf-8"))
        results["gui_uat"]["steps"][0:2] = reversed(results["gui_uat"]["steps"][0:2])
        results_path.write_text(json.dumps(results), encoding="utf-8")
        with self.assertRaisesRegex(lifecycle.SemanticEvidenceError, "required order"):
            lifecycle.validate_semantic_results(state, keep=True)

    def test_semantic_rejects_duplicate_test_evidence_content(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        run_root = Path(state["run_root"])
        (run_root / state["test_runs"][1]["path"]).write_bytes(
            (run_root / state["test_runs"][0]["path"]).read_bytes())
        with self.assertRaisesRegex(
            lifecycle.SemanticEvidenceError, "receipt changed"
        ):
            lifecycle.validate_semantic_results(state, keep=True)

    def test_semantic_requires_migration_test_evidence(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        results_path = Path(state["evidence_root"]) / "three-tier-results.json"
        results = json.loads(results_path.read_text(encoding="utf-8"))
        del results["tests"]["migration"]
        results_path.write_text(json.dumps(results), encoding="utf-8")
        with self.assertRaisesRegex(
            lifecycle.SemanticEvidenceError, "every required test class"
        ):
            lifecycle.validate_semantic_results(state, keep=True)

    def test_semantic_rejects_non_image_screenshot(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        Path(state["run_root"]).joinpath(
            "evidence/gui-uat/00000000000000000000000000000002/checkpoint-0.png"
        ).write_bytes(b"not an image")
        with self.assertRaisesRegex(
            lifecycle.SemanticEvidenceError, "artifact changed or is missing"
        ):
            lifecycle.validate_semantic_results(state, keep=True)


    def test_failed_validation_cannot_be_replaced_or_rewritten(self) -> None:
        state = self.prepare()
        command = "python3 manage.py check"
        summary = "Canonical acceptance check failed."
        lifecycle.record_validation(self.root, command, "FAIL", summary)
        state_path, failed = lifecycle.load_active(self.root)
        original_state = state_path.read_bytes()
        report_path = Path(state["report_path"])
        original_report = report_path.read_bytes()

        for status, replacement in (("PASS", "Repaired."), ("FAIL", "Changed.")):
            with self.subTest(status=status), self.assertRaisesRegex(
                lifecycle.LifecycleError, "Failed validation is immutable"
            ):
                lifecycle.record_validation(self.root, command, status, replacement)
            self.assertEqual(state_path.read_bytes(), original_state)
            self.assertEqual(report_path.read_bytes(), original_report)

        replayed = lifecycle.record_validation(self.root, command, "FAIL", summary)
        self.assertEqual(replayed, failed)
        self.assertEqual(state_path.read_bytes(), original_state)
        self.assertEqual(report_path.read_bytes(), original_report)

        lifecycle.record_validation(self.root, "cleanup-check", "PASS", "Clean.")
        _, final = lifecycle.load_active(self.root)
        self.assertEqual(final["validations"][0], failed["validations"][0])
        self.assertEqual(len(final["validations"]), 2)

    def test_record_validation_populates_report_and_rejects_secrets(self) -> None:
        state = self.prepare()
        command = (
            "python3 -m unittest sdlc-workflow-test/scripts/test_three_tier_lifecycle.py"
        )
        lifecycle.record_validation(
            self.root,
            command,
            "PASS",
            "Focused lifecycle tests passed.",
        )
        lifecycle.record_validation(
            self.root,
            command,
            "PASS",
            "Focused lifecycle tests passed again.",
        )
        report = Path(state["report_path"]).read_text(encoding="utf-8")
        self.assertIn("## Validation commands", report)
        self.assertIn("Focused lifecycle tests passed again.", report)
        self.assertEqual(report.count(command), 1)
        sensitive_value = "blocked" + "-value"
        secret_flag = "to" + "ken"
        authorization = "Authori" + "zation"
        unsafe_commands = (
            f"tool --{secret_flag} {sensitive_value}",
            f"curl -H '{authorization}: Bearer {sensitive_value}' http://localhost",
            f"curl -u user:{sensitive_value} http://localhost",
            f"curl http://user:{sensitive_value}@localhost",
        )
        for unsafe_command in unsafe_commands:
            with (
                self.subTest(unsafe_command=unsafe_command),
                self.assertRaisesRegex(lifecycle.LifecycleError, "secret material"),
            ):
                lifecycle.record_validation(
                    self.root,
                    unsafe_command,
                    "PASS",
                    "Must not be persisted.",
                )

    def test_destroy_leaves_open_tab_and_removes_every_resource(self) -> None:
        state = self.prepare()
        state["status"] = "READY_FOR_CLEANUP"
        state["resources"] = {
            "containers": ["web-id", "db-id"],
            "networks": ["network-id"],
            "volumes": ["volume-id"],
            "images": ["image-id"],
        }
        lifecycle.update_state(self.root, state)
        owned_labels = {
            lifecycle.OWNERSHIP_LABEL: state["verification_id"],
            lifecycle.COMPOSE_LABEL: state["compose_project"],
        }
        present = set(
            (kind, identifier)
            for kind, identifiers in state["resources"].items()
            for identifier in identifiers
        )

        def resource(kind: str, identifier: str) -> dict[str, object] | None:
            if (kind, identifier) not in present:
                return None
            return {"canonical_id": identifier, "labels": owned_labels}

        removed: list[tuple[str, str]] = []

        def remove(kind: str, identifier: str) -> str:
            removed.append((kind, identifier))
            present.remove((kind, identifier))
            return "REMOVED"

        with (
            mock.patch.object(lifecycle, "inspect_resource", side_effect=resource),
            mock.patch.object(lifecycle, "remove_resource", side_effect=remove),
        ):
            result, destroyed = lifecycle.destroy(self.root)
        self.assertEqual(result, "DESTROYED")
        self.assertIsNotNone(destroyed)
        self.assertEqual(destroyed["browser_instance"]["status"], "CLOSED")
        self.assertEqual(
            removed,
            [
                ("containers", "web-id"),
                ("containers", "db-id"),
                ("networks", "network-id"),
                ("volumes", "volume-id"),
                ("images", "image-id"),
            ],
        )
        self.assertFalse(Path(state["run_root"]).exists())
        self.assertFalse((self.root / "three-tier-live" / "active.json").exists())
        self.assertTrue(Path(state["report_path"]).is_file())
        report = Path(state["report_path"]).read_text(encoding="utf-8")
        self.assertIn("Project retention state: destroyed", report)
        archive = (
            self.root
            / "three-tier-live"
            / "lifecycle"
            / f"{state['verification_id']}.json"
        )
        self.assertTrue(archive.is_file())

    def test_destroy_refuses_mismatched_resource_without_partial_cleanup(self) -> None:
        state = self.prepare()
        state["status"] = "READY_FOR_CLEANUP"
        state["resources"]["containers"] = ["foreign-id"]
        lifecycle.update_state(self.root, state)
        foreign = {
            lifecycle.OWNERSHIP_LABEL: "different-run",
            lifecycle.COMPOSE_LABEL: state["compose_project"],
        }
        with (
            mock.patch.object(
                lifecycle,
                "inspect_resource",
                return_value={"canonical_id": "foreign-id", "labels": foreign},
            ),
            mock.patch.object(lifecycle, "remove_resource") as remove,
            self.assertRaisesRegex(
                lifecycle.LifecycleError, "Ownership label mismatch"
            ),
        ):
            lifecycle.destroy(self.root)
        remove.assert_not_called()
        self.assertTrue(Path(state["run_root"]).is_dir())
        _, failed = lifecycle.load_active(self.root)
        self.assertEqual(failed["status"], "CLEANUP_FAILED")

    def test_destroy_deduplicates_name_and_id_aliases_by_canonical_identity(self) -> None:
        state = self.prepare()
        state["status"] = "READY_FOR_CLEANUP"
        state["resources"]["networks"] = ["network-name"]
        lifecycle.update_state(self.root, state)
        owned_labels = {
            lifecycle.OWNERSHIP_LABEL: state["verification_id"],
            lifecycle.COMPOSE_LABEL: state["compose_project"],
        }
        present = True

        def inspect(kind: str, identifier: str) -> dict[str, object] | None:
            if kind != "networks" or not present:
                return None
            return {"canonical_id": "network-id", "labels": owned_labels}

        removed: list[tuple[str, str]] = []

        def remove(kind: str, identifier: str) -> str:
            nonlocal present
            removed.append((kind, identifier))
            present = False
            return "REMOVED"

        discovered = {kind: [] for kind in lifecycle.RESOURCE_KINDS}
        discovered["networks"] = ["network-id"]
        with (
            mock.patch.object(
                lifecycle, "discover_owned_resources", return_value=discovered
            ),
            mock.patch.object(lifecycle, "inspect_resource", side_effect=inspect),
            mock.patch.object(lifecycle, "remove_resource", side_effect=remove),
        ):
            result, destroyed = lifecycle.destroy(self.root)
        self.assertEqual(result, "DESTROYED")
        self.assertEqual(removed, [("networks", "network-id")])
        self.assertEqual(destroyed["cleanup"]["removed"], ["networks:network-id"])

    def test_inspect_resource_does_not_treat_daemon_failure_as_absence(self) -> None:
        self.command.return_value = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Cannot connect to the Docker daemon",
        )
        with self.assertRaisesRegex(lifecycle.LifecycleError, "Could not inspect"):
            lifecycle.inspect_resource("containers", "container-id")

    def test_inspect_resource_rejects_malformed_config_fail_closed(self) -> None:
        self.command.return_value = mock.Mock(
            returncode=0,
            stdout=json.dumps([{"Id": "container-id", "Config": None}]),
            stderr="",
        )
        with self.assertRaisesRegex(lifecycle.LifecycleError, "configuration"):
            lifecycle.inspect_resource("containers", "container-id")

    def test_remove_resource_accepts_race_only_after_proven_absence(self) -> None:
        self.command.return_value = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Error: No such container: container-id",
        )
        with mock.patch.object(lifecycle, "inspect_resource", return_value=None):
            outcome = lifecycle.remove_resource("containers", "container-id")
        self.assertEqual(outcome, "ALREADY_ABSENT")

    def test_cleanup_retry_preserves_cumulative_removed_ledger(self) -> None:
        state = self.prepare()
        state["status"] = "READY_FOR_CLEANUP"
        state["resources"]["containers"] = ["container-id"]
        state["resources"]["networks"] = ["network-id"]
        lifecycle.update_state(self.root, state)
        owned_labels = {
            lifecycle.OWNERSHIP_LABEL: state["verification_id"],
            lifecycle.COMPOSE_LABEL: state["compose_project"],
        }
        present = {("containers", "container-id"), ("networks", "network-id")}

        def inspect(kind: str, identifier: str) -> dict[str, object] | None:
            if (kind, identifier) not in present:
                return None
            return {"canonical_id": identifier, "labels": owned_labels}

        def first_remove(kind: str, identifier: str) -> str:
            if kind == "networks":
                raise lifecycle.LifecycleError("simulated network removal failure")
            present.remove((kind, identifier))
            return "REMOVED"

        discovered = {kind: [] for kind in lifecycle.RESOURCE_KINDS}
        with (
            mock.patch.object(
                lifecycle, "discover_owned_resources", return_value=discovered
            ),
            mock.patch.object(lifecycle, "inspect_resource", side_effect=inspect),
            mock.patch.object(lifecycle, "remove_resource", side_effect=first_remove),
            self.assertRaisesRegex(lifecycle.LifecycleError, "simulated"),
        ):
            lifecycle.destroy(self.root)
        _, failed = lifecycle.load_active(self.root)
        self.assertEqual(failed["cleanup"]["removed"], ["containers:container-id"])
        self.assertEqual(failed["cleanup"]["remaining"], ["networks:network-id"])

        def retry_remove(kind: str, identifier: str) -> str:
            present.remove((kind, identifier))
            return "REMOVED"

        with (
            mock.patch.object(
                lifecycle, "discover_owned_resources", return_value=discovered
            ),
            mock.patch.object(lifecycle, "inspect_resource", side_effect=inspect),
            mock.patch.object(lifecycle, "remove_resource", side_effect=retry_remove),
        ):
            result, destroyed = lifecycle.destroy(self.root)
        self.assertEqual(result, "DESTROYED")
        self.assertEqual(
            destroyed["cleanup"]["removed"],
            ["containers:container-id", "networks:network-id"],
        )


    def test_kept_project_with_remote_fails_destroy_before_docker(self) -> None:
        state = self.prepare()
        lifecycle.owned_git_origin._git(
            Path(state["project_root"]), "remote", "add", "extra", str(self.root / "foreign.git")
        )
        state["status"] = "KEPT"
        lifecycle.update_state(self.root, state)
        with (
            mock.patch.object(lifecycle, "inspect_labels") as inspect,
            self.assertRaisesRegex(lifecycle.LifecycleError, "UNSAFE_GIT_ENVIRONMENT"),
        ):
            lifecycle.destroy(self.root)
        inspect.assert_not_called()

    def test_cli_parser_requires_one_private_action(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            lifecycle.parser().parse_args([])
        parsed = lifecycle.parser().parse_args(
            ["--verification-root", str(self.root), "prepare"]
        )
        self.assertEqual(parsed.command, "prepare")

    def test_cli_mutation_requires_expected_verification_id(self) -> None:
        self.prepare()
        with redirect_stderr(StringIO()) as error:
            result = lifecycle.main(
                [
                    "--verification-root",
                    str(self.root),
                    "record-validation",
                    "--validation-command",
                    "python3 -m unittest",
                    "--status",
                    "PASS",
                    "--summary",
                    "Passed.",
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("requires --expected-verification-id", error.getvalue())

    def test_superseded_cli_generation_cannot_mutate_replacement(self) -> None:
        first = self.prepare()
        self.reset_prepare_preflight()
        second = lifecycle.prepare(self.root)
        with redirect_stderr(StringIO()) as error:
            result = lifecycle.main(
                [
                    "--verification-root",
                    str(self.root),
                    "--expected-verification-id",
                    str(first["verification_id"]),
                    "record-validation",
                    "--validation-command",
                    "python3 -m unittest",
                    "--status",
                    "PASS",
                    "--summary",
                    "Stale worker result.",
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("STALE_THREE_TIER_GENERATION", error.getvalue())
        _, current = lifecycle.load_active(self.root)
        self.assertEqual(current["verification_id"], second["verification_id"])
        self.assertEqual(current["validations"], [])

    def test_active_native_coordinator_can_validate_its_generation(self):
        state = lifecycle.prepare(self.root)
        lifecycle.workspace.register_native(self.root)
        lifecycle.assert_active_generation(self.root, state["verification_id"])
        with self.assertRaisesRegex(lifecycle.LifecycleError, "WORKSPACE_BUSY"):
            lifecycle.destroy(self.root)

    def test_native_receipt_retry_revalidates_orphan_before_admission(self):
        state = lifecycle.prepare(self.root)
        self.write_startup_fixture(state)
        expected = state.pop("native_activation")
        lifecycle.update_state(self.root, state)
        lifecycle.record_native_activation(self.root)
        _, admitted = lifecycle.load_active(self.root)
        self.assertEqual(admitted["native_activation"], expected)

    def test_unsafe_native_orphan_is_rejected_before_unbounded_read(self):
        state = lifecycle.prepare(self.root)
        self.write_startup_fixture(state)
        state.pop("native_activation")
        lifecycle.update_state(self.root, state)
        artifact = Path(state["run_root"]) / lifecycle.native_evidence.RELATIVE
        outside = self.root.parent / "unrelated.txt"
        outside.write_text("unrelated fixture data")
        original = Path.read_bytes
        def guarded_read(path):
            if path == artifact:
                self.fail("Orphan receipt reached an unbounded read")
            return original(path)
        for kind in ("symlink", "oversized"):
            with self.subTest(kind=kind):
                artifact.unlink()
                if kind == "symlink":
                    artifact.symlink_to(outside)
                else:
                    with artifact.open("wb") as stream:
                        stream.truncate(lifecycle.native_evidence.MAX_PREFIX + 1)
                    artifact.chmod(0o600)
                with mock.patch.object(Path, "read_bytes", guarded_read):
                    with self.assertRaises(lifecycle.LifecycleError):
                        lifecycle.record_native_activation(self.root)

    def test_current_cli_generation_can_assert_active(self) -> None:
        state = self.prepare()
        with redirect_stdout(StringIO()):
            result = lifecycle.main(
                [
                    "--verification-root",
                    str(self.root),
                    "--expected-verification-id",
                    str(state["verification_id"]),
                    "assert-active",
                ]
            )
        self.assertEqual(result, 0)

    def test_owned_compose_action_is_generation_locked_and_identity_bound(self) -> None:
        state = self.prepare()
        compose = mock.Mock(returncode=0, stdout="service output\n", stderr="")
        self.command.return_value = compose
        with redirect_stdout(StringIO()), mock.patch.object(
            lifecycle, "execution_target", return_value={"path": state["project_root"]}
        ):
            result = lifecycle.main(
                [
                    "--verification-root",
                    str(self.root),
                    "--expected-verification-id",
                    str(state["verification_id"]),
                    "run-compose",
                    "--",
                    "up",
                    "--force-recreate",
                    "--detach",
                    "--wait",
                    "web",
                    "db",
                ]
            )
        self.assertEqual(result, 0)
        self.command.assert_called_once_with(
            [
                "docker",
                "compose",
                "--project-name",
                state["compose_project"],
                "--project-directory",
                state["project_root"],
                "up",
                "--force-recreate",
                "--detach",
                "--wait",
                "web",
                "db",
            ],
            timeout=1800,
        )

    def test_owned_compose_action_rejects_scale_override(self) -> None:
        state = self.prepare()
        with redirect_stderr(StringIO()) as error:
            result = lifecycle.main(
                [
                    "--verification-root",
                    str(self.root),
                    "--expected-verification-id",
                    str(state["verification_id"]),
                    "run-compose",
                    "--",
                    "up",
                    "--scale",
                    "web=2",
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("cannot override", error.getvalue())
        self.command.assert_not_called()


class OwnedComposeStartupTests(unittest.TestCase):
    def test_incomplete_or_unsafe_startup_is_rejected_before_target_or_docker(self):
        valid = ["up", "--force-recreate", "--detach", "--wait", "web", "db"]
        invalid = [
            ["up", "--detach", "--wait"],
            *[valid[:index] + valid[index + 1:] for index in range(1, len(valid))],
            valid + ["--force-recreate"],
            valid + ["--renew-anon-volumes"],
            valid + ["--no-recreate"],
            valid + ["--no-deps"],
            valid + ["--build"],
            valid[:-1] + ["web"],
            valid[:-1] + ["other"],
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments), mock.patch.object(
                lifecycle, "execution_target"
            ) as target, mock.patch.object(lifecycle, "command") as command:
                with self.assertRaisesRegex(lifecycle.LifecycleError, "startup requires"):
                    lifecycle._run_owned_compose({}, arguments)
                target.assert_not_called()
                command.assert_not_called()

    def test_explicit_two_service_startup_is_forwarded_without_rewriting(self):
        state = {"compose_project": "owned-fixture"}
        for arguments in (
            ["up", "--force-recreate", "--detach", "--wait", "web", "db"],
            ["up", "--wait", "--detach", "--force-recreate", "db", "web"],
        ):
            with self.subTest(arguments=arguments), mock.patch.object(
                lifecycle, "execution_target", return_value={"path": "/tmp/owned-fixture"}
            ) as target, mock.patch.object(
                lifecycle, "command",
                return_value=subprocess.CompletedProcess([], 0, "started", ""),
            ) as command:
                self.assertEqual(lifecycle._run_owned_compose(state, arguments), "started")
                target.assert_called_once_with(
                    state, require_clean=False, worker_task=None, assignment_digest=None
                )
                command.assert_called_once_with(
                    ["docker", "compose", "--project-name", "owned-fixture",
                     "--project-directory", "/tmp/owned-fixture", *arguments],
                    timeout=1800,
                )


class ClassifierPhaseIdentityTests(unittest.TestCase):
    def setUp(self):
        self.state = {"verification_id": "a" * 32}
        self.current = {"current_execution": None}
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.run_dir = Path(directory).resolve()
        registration = (None, None, None, None, self.run_dir, self.current, None)
        self.enterContext(mock.patch.object(lifecycle.three_tier_target, "_registration", return_value=registration))
        self.enterContext(mock.patch.object(lifecycle.three_tier_target, "current_objective", return_value="a" * 64))
        self.target = self.enterContext(mock.patch.object(lifecycle, "execution_target", return_value={"basis_sha256": "b" * 64}))

    def test_classifier_before_preparation_has_no_execution_basis(self):
        self.assertEqual(lifecycle.phase_identity(self.state, "sdlc-classify-failure"), ("a" * 64, None))
        self.target.assert_not_called()

    def historical_classifier_fixture(self):
        self.current['current_execution'] = 'FEAT-001'
        self.state.update(run_root=str(self.run_dir), project_root=str(self.run_dir / 'source'),
                          git={'baseline_sha': 'd' * 40, 'promoted_sha': None})
        phase = 'sdlc-classify-failure'
        relative = f'evidence/phases/{phase}.json'
        result = {'schema': lifecycle.PHASE_RESULT_SCHEMA, 'phase': phase, 'status': 'PASS',
                  'verification_id': self.state['verification_id'], 'baseline_sha': 'd' * 40,
                  'recorded_head': 'd' * 40, 'objective_sha256': 'a' * 64,
                  'basis_sha256': None, 'assertions': lifecycle.PHASE_REQUIRED_ASSERTIONS[phase]}
        lifecycle.private_json(self.run_dir / relative, result)
        self.enterContext(mock.patch.object(lifecycle, 'command', return_value=mock.Mock(returncode=0)))
        self.enterContext(mock.patch.object(lifecycle, 'require_command', return_value='d' * 40))
        lifecycle.validate_phase_pass_artifact(self.state, phase, [relative])
        digest = hashlib.sha256((self.run_dir / relative).read_bytes()).hexdigest()
        archive = f'evidence/phases/attempts/{phase}/{digest}.json'
        lifecycle.private_json(self.run_dir / archive, result)
        entry = {'phase': phase, 'status': 'PASS', 'evidence': [archive],
                 'artifact_sha256': digest, 'attempt': 1, 'supersedes_attempt': None}
        self.state['phases'] = [entry, {'phase': 'sdlc-prepare-execution', 'status': 'PASS'}]
        self.prepare()
        self.target.return_value = {'basis_sha256': 'b' * 64, 'head': 'c' * 40}
        return phase, archive, entry, result

    def test_preexecution_classifier_archive_survives_preparation(self):
        phase, archive, entry, _ = self.historical_classifier_fixture()
        with mock.patch.object(lifecycle.three_tier_target, 'validate_historical_basis') as basis:
            lifecycle.validate_phase_pass_artifact(self.state, phase, [archive], historical_entry=entry)
            basis.assert_not_called()

    def test_null_classifier_archive_requires_prior_admission_and_later_execution(self):
        phase, archive, entry, _ = self.historical_classifier_fixture()
        prepared = self.state['phases'][1]
        for history in ([prepared, entry], [entry]):
            with self.subTest(history=history):
                self.state['phases'] = history
                with self.assertRaisesRegex(lifecycle.LifecycleError, 'basis digest is invalid'):
                    lifecycle.validate_phase_pass_artifact(self.state, phase, [archive], historical_entry=entry)

    def test_preexecution_classifier_archive_still_rejects_tampering(self):
        phase, archive, entry, result = self.historical_classifier_fixture()
        path = self.run_dir / archive
        raw = path.read_bytes()
        path.write_bytes(raw + b'\n')
        with self.assertRaisesRegex(lifecycle.LifecycleError, 'Immutable phase attempt changed'):
            lifecycle.validate_phase_pass_artifact(self.state, phase, [archive], historical_entry=entry)
        lifecycle.private_json(path, {**result, 'objective_sha256': 'e' * 64})
        with self.assertRaisesRegex(lifecycle.LifecycleError, 'basis is stale'):
            lifecycle.validate_phase_pass_artifact(self.state, phase, [archive], historical_entry=entry)

    def prepare(self):
        self.current["current_execution"] = "FEAT-001"
        (self.run_dir / "execution/FEAT-001").mkdir(parents=True)

    def test_selected_locked_plan_before_execution_has_no_basis(self):
        self.current["current_execution"] = "FEAT-001"
        (self.run_dir / "plans").mkdir()
        (self.run_dir / "plans/FEAT-001.plan.v1.md.lock").write_text("locked")
        self.assertEqual(lifecycle.phase_identity(self.state, "sdlc-classify-failure"), ("a" * 64, None))
        self.target.assert_not_called()

    def test_unprepared_selected_feature_receipt_uses_source_head(self):
        self.current["current_execution"] = "FEAT-001"
        self.state.update(run_root=str(self.run_dir), project_root=str(self.run_dir / "source"),
                          git={"baseline_sha": "d" * 40, "promoted_sha": None})
        phase = "sdlc-classify-failure"
        relative = "evidence/phases/" + phase + ".json"
        result = {"schema": lifecycle.PHASE_RESULT_SCHEMA, "phase": phase, "status": "PASS",
                  "verification_id": self.state["verification_id"], "baseline_sha": "d" * 40,
                  "recorded_head": "d" * 40, "objective_sha256": "a" * 64,
                  "basis_sha256": None, "assertions": lifecycle.PHASE_REQUIRED_ASSERTIONS[phase]}
        lifecycle.private_json(self.run_dir / relative, result)
        with (mock.patch.object(lifecycle, "command", return_value=mock.Mock(returncode=0)),
              mock.patch.object(lifecycle, "require_command", return_value="d" * 40) as source):
            lifecycle.validate_phase_pass_artifact(self.state, phase, [relative])
        source.assert_called_once()
        self.target.assert_not_called()

    def test_partial_preparation_never_falls_back_to_null_basis(self):
        self.current["current_execution"] = "FEAT-001"
        self.target.side_effect = lifecycle.three_tier_target.TargetError("Partial execution")
        for relative in ("execution/FEAT-001", "objective-impact/execution/FEAT-001.json",
                         "worktrees/FEAT-001"):
            path = self.run_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("incomplete")
            with self.subTest(relative=relative), self.assertRaisesRegex(
                lifecycle.three_tier_target.TargetError, "Partial execution"
            ):
                lifecycle.phase_identity(self.state, "sdlc-classify-failure")
            path.unlink()

    def test_missing_previously_observed_execution_is_not_unprepared(self):
        self.state["phases"] = [{"phase": "sdlc-prepare-execution", "status": "PASS"}]
        for feature in (None, "FEAT-001"):
            self.current["current_execution"] = feature
            with self.subTest(feature=feature), self.assertRaisesRegex(
                lifecycle.LifecycleError, "Previously observed execution"
            ):
                lifecycle.phase_identity(self.state, "sdlc-classify-failure")

    def test_dangling_preparation_symlink_is_rejected(self):
        self.current["current_execution"] = "FEAT-001"
        (self.run_dir / "execution").symlink_to(self.run_dir / "missing")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "Symlinked lifecycle"):
            lifecycle.phase_identity(self.state, "sdlc-classify-failure")
        self.target.assert_not_called()

    def test_invalid_preparation_parent_is_rejected(self):
        self.current["current_execution"] = "FEAT-001"
        (self.run_dir / "execution").write_text("not a directory")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "preparation path is invalid"):
            lifecycle.phase_identity(self.state, "sdlc-classify-failure")

    def test_invalid_selected_execution_is_rejected(self):
        self.current["current_execution"] = "../other"
        with self.assertRaisesRegex(lifecycle.LifecycleError, "execution selection is invalid"):
            lifecycle.phase_identity(self.state, "sdlc-classify-failure")

    def test_classifier_after_preparation_uses_validated_execution_basis(self):
        self.prepare()
        self.assertEqual(lifecycle.phase_identity(self.state, "sdlc-classify-failure"), ("a" * 64, "b" * 64))
        self.target.assert_called_once_with(
            self.state, require_clean=False, phase_observation="sdlc-classify-failure"
        )

    def test_classifier_invalid_execution_does_not_fall_back_to_null_basis(self):
        self.prepare()
        self.target.side_effect = lifecycle.three_tier_target.TargetError("Invalid execution identity")
        with self.assertRaisesRegex(lifecycle.three_tier_target.TargetError, "Invalid execution"):
            lifecycle.phase_identity(self.state, "sdlc-classify-failure")

    def test_classifier_receipt_uses_integration_head_and_rejects_null_or_stale_basis(self):
        self.prepare()
        self.target.return_value = {"basis_sha256": "b" * 64, "head": "c" * 40}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.state.update(run_root=str(root), project_root=str(root / "source"), git={"baseline_sha": "d" * 40, "promoted_sha": None})
            phase = "sdlc-classify-failure"
            relative = "evidence/phases/" + phase + ".json"
            result = {"schema": lifecycle.PHASE_RESULT_SCHEMA, "phase": phase, "status": "PASS",
                      "verification_id": self.state["verification_id"], "baseline_sha": "d" * 40,
                      "recorded_head": "c" * 40, "objective_sha256": "a" * 64,
                      "basis_sha256": "b" * 64, "assertions": lifecycle.PHASE_REQUIRED_ASSERTIONS[phase]}
            lifecycle.private_json(root / relative, result)
            with (mock.patch.object(lifecycle, "command", return_value=mock.Mock(returncode=0)),
                  mock.patch.object(lifecycle, "require_command", side_effect=AssertionError("Must use integration head"))):
                lifecycle.validate_phase_pass_artifact(self.state, phase, [relative])
                for basis in (None, "e" * 64):
                    lifecycle.private_json(root / relative, {**result, "basis_sha256": basis})
                    with self.assertRaisesRegex(lifecycle.LifecycleError, "basis is stale"):
                        lifecycle.validate_phase_pass_artifact(self.state, phase, [relative])


class LoopbackContractTests(unittest.TestCase):
    def state(self, host='127.0.0.1'):
        return {'resources': {'containers': ['web-id', 'db-id']},
                'endpoints': {'web': f'http://{host}:49152/',
                              'api': f'http://{host}:49152/api/v1/tasks',
                              'health': f'http://{host}:49152/healthz',
                              'database': 'db:5432'}}

    @staticmethod
    def owned(kind, identity, state):
        return {'labels': {'com.docker.compose.service': 'web' if identity == 'web-id' else 'db'}}

    def refresh(self, state, ports):
        with mock.patch.object(lifecycle, 'assert_resource_owned', side_effect=self.owned), mock.patch.object(
                lifecycle, 'container_ports', side_effect=lambda identity: ports if identity == 'web-id' else {}):
            return lifecycle.refreshed_restart_state(state)

    def test_restart_uses_actual_internal_port(self):
        value = self.refresh(self.state(), {'9000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '49153'}]})
        self.assertEqual(value['endpoints']['api'], 'http://127.0.0.1:49153/api/v1/tasks')

    def test_ipv6_registration_and_restart(self):
        state = self.state('[::1]')
        lifecycle.validate_runtime_endpoints(state['endpoints'])
        value = self.refresh(state, {'9000/tcp': [{'HostIp': '::1', 'HostPort': '49153'}]})
        self.assertEqual(value['endpoints']['health'], 'http://[::1]:49153/healthz')

    def test_ipv6_deployment_and_cross_family_rejection(self):
        state = self.state('[::1]')
        image = 'sha256:' + 'a' * 64
        state['resources']['images'] = [image]
        observed = {'Id': 'web-id', 'Image': image, 'Mounts': [], 'State': {'Running': True},
                    'NetworkSettings': {'Ports': {'9000/tcp': [{'HostIp': '::1', 'HostPort': '49152'}]}}}
        command = mock.Mock(return_value=mock.Mock(returncode=0, stdout=json.dumps(observed)))
        runtime = lifecycle.three_tier_runtime
        with mock.patch.object(runtime, 'load_build', return_value={'image_id': image}):
            runtime.validate_deployment(state, {}, command, self.owned)
            state['endpoints']['api'] = 'http://127.0.0.1:49152/api/v1/tasks'
            with self.assertRaises(runtime.RuntimeEvidenceError):
                runtime.validate_deployment(state, {}, command, self.owned)

    def test_extra_published_binding_is_rejected(self):
        ports = {'8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '49153'}],
                 '9000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '49154'}]}
        with self.assertRaises(lifecycle.LifecycleError):
            self.refresh(self.state(), ports)

    def test_binding_parser_rejects_malformed_and_public_observations(self):
        owner = lifecycle.three_tier_runtime
        for ports in (None, [], {"9000/tcp": {}}, {"9000/tcp": [None]},
                      {"9000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49152"}]},
                      {"9000/tcp": [{"HostIp": "::1", "HostPort": "0"}]},
                      {"9000/tcp": [{"HostIp": "::1", "HostPort": "invalid"}]}):
            with self.subTest(ports=ports), self.assertRaises(owner.RuntimeEvidenceError):
                owner.loopback_binding(ports)


if __name__ == "__main__":
    unittest.main()
