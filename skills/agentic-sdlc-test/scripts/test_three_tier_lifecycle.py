#!/usr/bin/env python3
"""Focused tests for the private three-tier lifecycle helper."""

from __future__ import annotations

from contextlib import redirect_stderr
import importlib.util
from io import StringIO
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib


MODULE_PATH = Path(__file__).with_name("three_tier_lifecycle.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("three_tier_lifecycle", MODULE_PATH)
assert SPEC and SPEC.loader
lifecycle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lifecycle
SPEC.loader.exec_module(lifecycle)


def png_bytes(red: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body))
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00" + bytes((red, 0, 0, 255)))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


class ThreeTierLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        # macOS maps /var to /private/var through a system symlink. Use the
        # home directory so lifecycle symlink rejection is exercised without
        # weakening production path checks for that platform alias.
        self.temporary = tempfile.TemporaryDirectory(dir=Path.home())
        self.root = Path(self.temporary.name) / "verification"
        self.preflight = mock.patch.multiple(
            lifecycle,
            require_command=mock.DEFAULT,
            detect_browser=mock.DEFAULT,
        )
        values = self.preflight.start()
        self.require_command = values["require_command"]
        self.detect_browser = values["detect_browser"]
        self.require_command.side_effect = ["29.0", "5.0", "git version 2.50"]
        self.detect_browser.return_value = ("edge", "Microsoft Edge")

    def tearDown(self) -> None:
        self.preflight.stop()
        self.temporary.cleanup()

    def prepare(self) -> dict[str, object]:
        return lifecycle.prepare(self.root, "edge")

    def write_valid_results(self, state: dict[str, object]) -> None:
        run_root = Path(state["run_root"])
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
                "baseline_sha": "a" * 40,
                "promoted_sha": "b" * 40,
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
                "harness": "computer-use",
                "browser": "edge",
                "steps": list(lifecycle.REQUIRED_GUI_STEPS[:-1]) + ["retain-test-tab"],
                "api_db_correlated": True,
                "restart_persistence": True,
                "screenshots": screenshots,
            },
        }
        state["git"] = {
            "baseline_sha": value["git"]["baseline_sha"],
            "promoted_sha": value["git"]["promoted_sha"],
        }
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
            relative = f"evidence/phases/{phase}.txt"
            artifact = run_root / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("passed\n", encoding="utf-8")
            state["phases"].append(
                {
                    "phase": phase,
                    "status": "PASS",
                    "summary": "Phase passed.",
                    "evidence": [relative],
                    "recorded_at": lifecycle.utc_now(),
                }
            )
        state["environment"]["computer_use"] = "PASS"
        lifecycle.update_state(self.root, state)
        (run_root / "evidence" / "three-tier-results.json").write_text(
            json.dumps(value), encoding="utf-8"
        )

    def test_prepare_creates_one_owned_private_run_and_report(self) -> None:
        state = self.prepare()
        self.assertEqual(state["status"], "PREPARED")
        self.assertEqual(state["scenario"], lifecycle.SCENARIO)
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

    def test_second_prepare_fails_without_replacing_active_run(self) -> None:
        first = self.prepare()
        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "ACTIVE_THREE_TIER_APPLICATION"
        ):
            lifecycle.prepare(self.root, "edge")
        _, current = lifecycle.load_active(self.root)
        self.assertEqual(current["verification_id"], first["verification_id"])

    def test_prepare_refuses_orphaned_owned_run(self) -> None:
        state = self.prepare()
        (self.root / "three-tier-live" / "active.json").unlink()
        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "ORPHANED_THREE_TIER_RUN"
        ):
            lifecycle.prepare(self.root, "edge")
        self.assertTrue(Path(state["run_root"]).is_dir())

    def test_destroy_missing_root_is_idempotent(self) -> None:
        result, state = lifecycle.destroy(self.root)
        self.assertEqual(result, "ALREADY_DESTROYED")
        self.assertIsNone(state)
        self.assertFalse(self.root.exists())

    def test_existing_unowned_root_fails_closed(self) -> None:
        self.root.mkdir()
        (self.root / "preserve.txt").write_text("keep\n", encoding="utf-8")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "not owned"):
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

    def test_record_runtime_requires_exact_labelled_inventory(self) -> None:
        state = self.prepare()

        def labels(kind: str, identifier: str) -> dict[str, str]:
            result = {
                lifecycle.OWNERSHIP_LABEL: state["verification_id"],
                lifecycle.COMPOSE_LABEL: state["compose_project"],
            }
            if kind == "containers":
                result["com.docker.compose.service"] = {
                    "web-id": "web",
                    "db-id": "db",
                }[identifier]
            return result

        with (
            mock.patch.object(lifecycle, "inspect_labels", side_effect=labels),
            mock.patch.object(lifecycle, "assert_port_isolation"),
        ):
            updated = lifecycle.record_runtime(
                self.root,
                web_url="http://127.0.0.1:49152/",
                api_url="http://127.0.0.1:49152/api/v1/tasks",
                health_url="http://127.0.0.1:49152/healthz",
                database_endpoint="db:5432/taskboard",
                web_container="web-id",
                database_container="db-id",
                networks=["network-id"],
                volumes=["volume-id"],
                images=["image-id"],
            )
        self.assertEqual(updated["endpoints"]["web"], "http://127.0.0.1:49152/")
        self.assertEqual(updated["resources"]["containers"], ["web-id", "db-id"])

    def test_record_git_can_capture_clean_baseline_before_promotion(self) -> None:
        state = self.prepare()
        Path(state["project_root"]).joinpath(".git").mkdir()
        baseline_sha = "a" * 40
        self.require_command.side_effect = [baseline_sha, "", ""]
        updated = lifecycle.record_git(self.root, baseline_sha, None)
        self.assertEqual(
            updated["git"],
            {"baseline_sha": baseline_sha, "promoted_sha": None},
        )

    def test_record_runtime_rejects_non_loopback_web_endpoint(self) -> None:
        self.prepare()
        with self.assertRaisesRegex(lifecycle.LifecycleError, "loopback"):
            lifecycle.record_runtime(
                self.root,
                web_url="http://0.0.0.0:8000/",
                api_url="http://127.0.0.1:8000/api/v1/tasks",
                health_url="http://127.0.0.1:8000/healthz",
                database_endpoint="db:5432/taskboard",
                web_container="web-id",
                database_container="db-id",
                networks=["network-id"],
                volumes=["volume-id"],
                images=["image-id"],
            )

    def test_record_runtime_rejects_option_like_resource_identifier(self) -> None:
        self.prepare()
        with self.assertRaisesRegex(lifecycle.LifecycleError, "Invalid containers"):
            lifecycle.record_runtime(
                self.root,
                web_url="http://127.0.0.1:8000/",
                api_url="http://127.0.0.1:8000/api/v1/tasks",
                health_url="http://127.0.0.1:8000/healthz",
                database_endpoint="db:5432/taskboard",
                web_container="--force",
                database_container="db-id",
                networks=["network-id"],
                volumes=["volume-id"],
                images=["image-id"],
            )

    def test_record_runtime_rejects_swapped_container_roles(self) -> None:
        state = self.prepare()

        def labels(kind: str, identifier: str) -> dict[str, str]:
            result = {
                lifecycle.OWNERSHIP_LABEL: state["verification_id"],
                lifecycle.COMPOSE_LABEL: state["compose_project"],
            }
            if kind == "containers":
                result["com.docker.compose.service"] = {
                    "web-id": "web",
                    "db-id": "db",
                }[identifier]
            return result

        with (
            mock.patch.object(lifecycle, "inspect_labels", side_effect=labels),
            self.assertRaisesRegex(lifecycle.LifecycleError, "Compose web service"),
        ):
            lifecycle.record_runtime(
                self.root,
                web_url="http://127.0.0.1:49152/",
                api_url="http://127.0.0.1:49152/api/v1/tasks",
                health_url="http://127.0.0.1:49152/healthz",
                database_endpoint="db:5432/taskboard",
                web_container="db-id",
                database_container="web-id",
                networks=["network-id"],
                volumes=["volume-id"],
                images=["image-id"],
            )

    def test_prepare_public_images_uses_private_config_and_fixed_images(self) -> None:
        state = self.prepare()
        inspect_missing = mock.Mock(returncode=1, stdout="", stderr="missing")
        pull_ok = mock.Mock(returncode=0, stdout="pulled", stderr="")
        with (
            mock.patch.object(lifecycle, "require_command", return_value="desktop-linux"),
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

    def test_uat_phase_failure_updates_computer_use_report_status(self) -> None:
        self.prepare()
        summary = (
            "ENVIRONMENT_DEFECT: JIT Computer Use readiness failed at "
            "pre-navigation-window-capture; no GUI navigation or action was attempted."
        )
        updated = lifecycle.record_phase(
            self.root,
            "sdlc-uat-tests",
            "FAIL",
            summary,
            [],
        )
        self.assertEqual(updated["environment"]["computer_use"], "FAIL")
        report = Path(updated["report_path"]).read_text(encoding="utf-8")
        self.assertIn("## Top issues and recommended fixes", report)
        self.assertIn(summary, report)
        self.assertIn("Keep the owned runtime unchanged.", report)
        self.assertIn("make no further Computer Use calls", report)

    def test_browser_record_rejects_url_credentials(self) -> None:
        self.prepare()
        with self.assertRaisesRegex(lifecycle.LifecycleError, "loopback URL"):
            lifecycle.record_browser(
                self.root,
                "SDLC Task Board",
                "http://user:password@127.0.0.1:8000/",
                closed=False,
            )

    def test_browser_record_does_not_override_failed_computer_use_uat(self) -> None:
        self.prepare()
        lifecycle.record_phase(
            self.root,
            "sdlc-uat-tests",
            "FAIL",
            "ENVIRONMENT_DEFECT: JIT Computer Use readiness failed at "
            "pre-navigation-window-capture; no GUI navigation or action was attempted.",
            [],
        )
        updated = lifecycle.record_browser(
            self.root,
            "SDLC Task Board",
            "http://127.0.0.1:49152/",
            closed=True,
        )
        self.assertEqual(updated["environment"]["computer_use"], "FAIL")

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
        lifecycle.record_browser(
            self.root,
            "SDLC Task Board",
            "http://127.0.0.1:49152/",
            closed=False,
        )
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

    def test_pass_requires_recorded_passing_validations(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        lifecycle.record_browser(
            self.root,
            "SDLC Task Board",
            "http://127.0.0.1:49152/",
            closed=False,
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
        results["gui_uat"]["steps"][0:2] = reversed(
            results["gui_uat"]["steps"][0:2]
        )
        results_path.write_text(json.dumps(results), encoding="utf-8")
        with self.assertRaisesRegex(lifecycle.SemanticEvidenceError, "required order"):
            lifecycle.validate_semantic_results(state, keep=True)

    def test_semantic_rejects_duplicate_test_evidence_content(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        run_root = Path(state["run_root"])
        (run_root / "evidence/tests/api.txt").write_bytes(
            (run_root / "evidence/tests/unit.txt").read_bytes()
        )
        with self.assertRaisesRegex(
            lifecycle.SemanticEvidenceError, "Identical generic evidence"
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
            "evidence/gui-uat/checkpoint-0.png"
        ).write_bytes(b"not an image")
        with self.assertRaisesRegex(
            lifecycle.SemanticEvidenceError, "not a recognized PNG or JPEG"
        ):
            lifecycle.validate_semantic_results(state, keep=True)

    def test_keep_rejects_browser_tab_recorded_closed(self) -> None:
        self.prepare()
        lifecycle.record_browser(
            self.root,
            "SDLC Task Board",
            "http://127.0.0.1:49152/",
            closed=True,
        )
        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "must retain its dedicated browser tab"
        ):
            lifecycle.finish(self.root, "FAIL", keep=True)

    def test_record_validation_populates_report_and_rejects_secrets(self) -> None:
        state = self.prepare()
        command = (
            "python3 -m unittest "
            "agentic-sdlc-test/scripts/test_three_tier_lifecycle.py"
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

    def test_destroy_checks_every_existing_resource_before_removal(self) -> None:
        state = self.prepare()
        state["status"] = "READY_FOR_CLEANUP"
        state["resources"] = {
            "containers": ["web-id", "db-id"],
            "networks": ["network-id"],
            "volumes": ["volume-id"],
            "images": ["image-id"],
        }
        state["browser_tab"] = {
            "title": "SDLC Task Board",
            "url": "http://127.0.0.1:49152/",
            "closed": True,
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

        def labels(kind: str, identifier: str) -> dict[str, str] | None:
            return owned_labels if (kind, identifier) in present else None

        removed: list[tuple[str, str]] = []

        def remove(kind: str, identifier: str) -> None:
            removed.append((kind, identifier))
            present.remove((kind, identifier))

        with (
            mock.patch.object(lifecycle, "inspect_labels", side_effect=labels),
            mock.patch.object(lifecycle, "remove_resource", side_effect=remove),
        ):
            result, destroyed = lifecycle.destroy(self.root)
        self.assertEqual(result, "DESTROYED")
        self.assertIsNotNone(destroyed)
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
            mock.patch.object(lifecycle, "inspect_labels", return_value=foreign),
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

    def test_destroy_requires_dedicated_browser_tab_to_be_closed(self) -> None:
        state = self.prepare()
        state["browser_tab"] = {
            "title": "SDLC Task Board",
            "url": "http://127.0.0.1:49152/",
            "closed": False,
        }
        state["resources"] = {
            "containers": ["web-id", "db-id"],
            "networks": ["network-id"],
            "volumes": ["volume-id"],
            "images": ["image-id"],
        }
        lifecycle.update_state(self.root, state)
        with (
            mock.patch.object(lifecycle, "inspect_labels") as inspect,
            self.assertRaisesRegex(lifecycle.LifecycleError, "still open"),
        ):
            lifecycle.destroy(self.root)
        inspect.assert_not_called()
        self.assertTrue(Path(state["run_root"]).is_dir())
        _, retained = lifecycle.load_active(self.root)
        self.assertEqual(retained["status"], "CLEANUP_FAILED")
        self.assertEqual(retained["cleanup"]["status"], "FAIL")
        self.assertEqual(retained["cleanup"]["removed"], [])
        self.assertEqual(
            retained["cleanup"]["remaining"],
            [
                "containers:web-id",
                "containers:db-id",
                "networks:network-id",
                "volumes:volume-id",
                "images:image-id",
            ],
        )

    def test_kept_project_with_remote_fails_destroy_before_docker(self) -> None:
        state = self.prepare()
        Path(state["project_root"]).joinpath(".git").mkdir()
        state["status"] = "KEPT"
        lifecycle.update_state(self.root, state)
        self.require_command.side_effect = [str(state["project_root"]), "origin"]
        with (
            mock.patch.object(lifecycle, "inspect_labels") as inspect,
            self.assertRaisesRegex(lifecycle.LifecycleError, "gained a Git remote"),
        ):
            lifecycle.destroy(self.root)
        inspect.assert_not_called()

    def test_cli_parser_requires_one_private_action(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            lifecycle.parser().parse_args([])
        parsed = lifecycle.parser().parse_args(
            ["--verification-root", str(self.root), "prepare", "--browser", "edge"]
        )
        self.assertEqual(parsed.command, "prepare")


if __name__ == "__main__":
    unittest.main()
