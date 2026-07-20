#!/usr/bin/env python3
"""Focused tests for the private three-tier lifecycle helper."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from io import StringIO
import json
import os
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
            command=mock.DEFAULT,
        )
        values = self.preflight.start()
        self.require_command = values["require_command"]
        self.detect_browser = values["detect_browser"]
        self.command = values["command"]
        self.require_command.side_effect = ["29.0", "5.0", "git version 2.50"]
        self.detect_browser.return_value = ("chrome", "Google Chrome")
        self.command.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        self.browser_assert = mock.patch.object(
            lifecycle.three_tier_browser, "assert_owned_running"
        )
        self.browser_close = mock.patch.object(
            lifecycle.three_tier_browser, "close"
        )
        self.browser_assert.start()
        self.close_browser = self.browser_close.start()

        def close_owned(run_root, verification_id, browser_state):
            return {
                **browser_state,
                "status": "CLOSED",
                "pid": None,
                "process_group": None,
                "launched_at": browser_state.get("launched_at") or lifecycle.utc_now(),
                "closed_at": lifecycle.utc_now(),
            }

        self.close_browser.side_effect = close_owned

    def tearDown(self) -> None:
        self.browser_close.stop()
        self.browser_assert.stop()
        self.preflight.stop()
        self.temporary.cleanup()

    def prepare(self) -> dict[str, object]:
        state = lifecycle.prepare(self.root)
        state["browser_instance"].update(
            {
                "status": "RUNNING",
                "pid": 12345,
                "process_group": 12345,
                "launched_at": lifecycle.utc_now(),
            }
        )
        lifecycle.update_state(self.root, state)
        return state

    def reset_prepare_preflight(self) -> None:
        self.require_command.side_effect = ["29.0", "5.0", "git version 2.50"]

    def browser_marker(self) -> str:
        _, state = lifecycle.load_active(self.root)
        return state["browser_instance"]["window_marker"]

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
                "browser": "chrome",
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
                        "assertions": lifecycle.PHASE_REQUIRED_ASSERTIONS[phase],
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(artifact, 0o600)
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
        state["computer_use_attempts"] = [
            {
                "stage": stage,
                "outcome": "PASS",
                "action_attempted": False,
                "response": "success",
                "lock_state": "no",
                "window_visible": "yes",
                "window_frontmost": "yes",
                "current_space": "yes",
                "dedicated_instance": "yes",
                "recorded_at": lifecycle.utc_now(),
            }
            for stage in (
                "capability-discovery",
                "evaluate-readiness",
                "uat-readiness",
            )
        ]
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

        def resource(kind: str, identifier: str) -> dict[str, object]:
            result = {
                lifecycle.OWNERSHIP_LABEL: state["verification_id"],
                lifecycle.COMPOSE_LABEL: state["compose_project"],
            }
            if kind == "containers":
                result["com.docker.compose.service"] = {
                    "web-id": "web",
                    "db-id": "db",
                }[identifier]
            return {"canonical_id": identifier, "labels": result}

        with (
            mock.patch.object(lifecycle, "inspect_resource", side_effect=resource),
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

        def resource(kind: str, identifier: str) -> dict[str, object]:
            result = {
                lifecycle.OWNERSHIP_LABEL: state["verification_id"],
                lifecycle.COMPOSE_LABEL: state["compose_project"],
            }
            if kind == "containers":
                result["com.docker.compose.service"] = {
                    "web-id": "web",
                    "db-id": "db",
                }[identifier]
            return {"canonical_id": identifier, "labels": result}

        with (
            mock.patch.object(lifecycle, "inspect_resource", side_effect=resource),
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

    def test_passing_phase_requires_canonical_semantic_result(self) -> None:
        state = self.prepare()
        state["git"] = {
            "baseline_sha": "a" * 40,
            "promoted_sha": "b" * 40,
        }
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

    def test_computer_use_attempts_preserve_discovery_and_jit_failure(self) -> None:
        state = self.prepare()
        lifecycle.record_computer_use(
            self.root,
            stage="capability-discovery",
            outcome="PASS",
            action_attempted=False,
            response="success",
            lock_state="no",
            window_visible="yes",
            window_frontmost="yes",
            current_space="yes",
            window_marker=self.browser_marker(),
        )
        updated = lifecycle.record_computer_use(
            self.root,
            stage="evaluate-readiness",
            outcome="ENVIRONMENT_DEFECT",
            action_attempted=False,
            response="error",
            lock_state="unknown",
            window_visible="unknown",
            window_frontmost="unknown",
            current_space="unknown",
            window_marker=self.browser_marker(),
        )
        self.assertEqual(updated["environment"]["computer_use"], "FAIL")
        self.assertEqual(len(updated["computer_use_attempts"]), 2)
        report = Path(state["report_path"]).read_text(encoding="utf-8")
        self.assertIn("capability-discovery", report)
        self.assertIn("evaluate-readiness", report)
        self.assertIn("ENVIRONMENT_DEFECT", report)

    def test_later_pass_does_not_hide_computer_use_environment_defect(self) -> None:
        self.prepare()
        lifecycle.record_computer_use(
            self.root,
            stage="capability-discovery",
            outcome="PASS",
            action_attempted=False,
            response="success",
            lock_state="no",
            window_visible="yes",
            window_frontmost="yes",
            current_space="yes",
            window_marker=self.browser_marker(),
        )
        lifecycle.record_computer_use(
            self.root,
            stage="evaluate-readiness",
            outcome="ENVIRONMENT_DEFECT",
            action_attempted=False,
            response="error",
            lock_state="unknown",
            window_visible="unknown",
            window_frontmost="unknown",
            current_space="unknown",
            window_marker=self.browser_marker(),
        )
        updated = lifecycle.record_computer_use(
            self.root,
            stage="uat-readiness",
            outcome="PASS",
            action_attempted=False,
            response="success",
            lock_state="no",
            window_visible="yes",
            window_frontmost="yes",
            current_space="yes",
            window_marker=self.browser_marker(),
        )
        self.assertEqual(updated["environment"]["computer_use"], "FAIL")

    def test_computer_use_timeout_blocks_later_attempts(self) -> None:
        self.prepare()
        lifecycle.record_computer_use(
            self.root,
            stage="capability-discovery",
            outcome="ENVIRONMENT_DEFECT",
            action_attempted=False,
            response="timeout",
            lock_state="unknown",
            window_visible="unknown",
            window_frontmost="unknown",
            current_space="unknown",
            window_marker=self.browser_marker(),
        )
        with self.assertRaisesRegex(lifecycle.LifecycleError, "unhealthy"):
            lifecycle.record_computer_use(
                self.root,
                stage="evaluate-readiness",
                outcome="PASS",
                action_attempted=False,
                response="success",
                lock_state="no",
                window_visible="yes",
                window_frontmost="yes",
                current_space="yes",
                window_marker=self.browser_marker(),
            )

    def test_computer_use_pass_requires_successful_visible_capture(self) -> None:
        self.prepare()
        with self.assertRaisesRegex(lifecycle.LifecycleError, "PASS requires"):
            lifecycle.record_computer_use(
                self.root,
                stage="capability-discovery",
                outcome="PASS",
                action_attempted=False,
                response="error",
                lock_state="unknown",
                window_visible="unknown",
                window_frontmost="unknown",
                current_space="unknown",
                window_marker=self.browser_marker(),
            )

    def test_computer_use_action_rejects_non_dedicated_window_marker(self) -> None:
        self.prepare()
        with self.assertRaisesRegex(lifecycle.LifecycleError, "exact dedicated"):
            lifecycle.record_computer_use(
                self.root,
                stage="capability-discovery",
                outcome="FAIL",
                action_attempted=True,
                response="error",
                lock_state="no",
                window_visible="yes",
                window_frontmost="yes",
                current_space="yes",
                window_marker="an existing Chrome window",
            )

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
        self.mark_browser_closed()
        updated = lifecycle.record_browser(
            self.root,
            "SDLC Task Board",
            self.browser_url(),
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
            self.browser_url(),
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
        self.assertIn("Retained owned resources: 5", report)

    def test_final_pass_revalidates_canonical_phase_artifact(self) -> None:
        state = self.prepare()
        self.write_valid_results(state)
        phase = lifecycle.REQUIRED_SDLC_PHASES[0]
        artifact = Path(state["run_root"]) / f"evidence/phases/{phase}.json"
        artifact.write_text('{"result":"pass"}\n', encoding="utf-8")
        lifecycle.record_validation(
            self.root,
            "python3 manage.py test",
            "PASS",
            "Tests passed.",
        )
        lifecycle.record_browser(
            self.root,
            "SDLC Task Board",
            self.browser_url(),
            closed=False,
        )
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
            "harness": "computer-use",
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
            "harness": "computer-use",
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
        lifecycle.record_browser(
            self.root,
            "SDLC Task Board",
            self.browser_url(),
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
        results["gui_uat"]["steps"][0:2] = reversed(results["gui_uat"]["steps"][0:2])
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
        self.mark_browser_closed()
        lifecycle.record_browser(
            self.root,
            "SDLC Task Board",
            self.browser_url(),
            closed=True,
        )
        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "must retain its dedicated browser tab"
        ):
            lifecycle.finish(self.root, "FAIL", keep=True)

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
        state["browser_tab"] = {
            "title": "SDLC Task Board",
            "url": "http://127.0.0.1:49152/",
            "closed": False,
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
        self.assertFalse(destroyed["browser_tab"]["closed"])
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

    def test_destroy_leaves_recorded_browser_tab_open(self) -> None:
        state = self.prepare()
        state["browser_tab"] = {
            "title": "SDLC Task Board",
            "url": "http://127.0.0.1:49152/",
            "closed": False,
        }
        lifecycle.update_state(self.root, state)
        result, destroyed = lifecycle.destroy(self.root)

        self.assertEqual(result, "DESTROYED")
        self.assertIsNotNone(destroyed)
        self.assertEqual(
            destroyed["browser_tab"],
            {
                "title": "SDLC Task Board",
                "url": "http://127.0.0.1:49152/",
                "closed": False,
            },
        )
        self.assertFalse(Path(state["run_root"]).exists())
        self.assertFalse((self.root / "three-tier-live" / "active.json").exists())

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
        with redirect_stdout(StringIO()):
            result = lifecycle.main(
                [
                    "--verification-root",
                    str(self.root),
                    "--expected-verification-id",
                    str(state["verification_id"]),
                    "run-compose",
                    "--",
                    "up",
                    "--detach",
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
                "--detach",
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


if __name__ == "__main__":
    unittest.main()
