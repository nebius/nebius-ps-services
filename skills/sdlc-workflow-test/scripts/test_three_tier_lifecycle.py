#!/usr/bin/env python3
"""Focused tests for the private three-tier lifecycle helper."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import hashlib
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
        relative = "evidence/workflow-startup.json"
        artifact = Path(state["run_root"]) / relative
        lifecycle.private_json(artifact, {
            "schema": "agentic-sdlc/workflow-startup-v1",
            "verification_id": state["verification_id"],
            "baseline_sha": state["git"]["baseline_sha"],
            "project_id": "test-project", "run_id": "run-test", "agent": "codex",
            "current_phase": "sdlc-start", "hook_discovery": "MATCH",
            "owner_session_hash": "c" * 64, "lock_sha256": "d" * 64,
            "observed_at": lifecycle.utc_now(),
        })
        state["workflow_startup"] = {
            "path": relative, "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()
        }

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
        self.write_browser_receipts(state, screenshots)
        lifecycle.update_state(self.root, state)
        (run_root / "evidence" / "three-tier-results.json").write_text(
            json.dumps(value), encoding="utf-8"
        )
        (run_root / "evidence" / "three-tier-results.json").chmod(0o600)

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
        with mock.patch.object(lifecycle, "execution_target", return_value=target), mock.patch.object(
            lifecycle.three_tier_browser, "run_stage"
        ) as launch:
            with self.assertRaisesRegex(lifecycle.LifecycleError, "deployment"):
                lifecycle.run_browser_stage(self.root, "evaluate")
            launch.assert_not_called()

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
        ctx = verifier.setup_context(verifier.parse_args(["--verification-root", str(self.root)]))

        def claims(identity=state["verification_id"]):
            return verifier.validated_profile_claims(
                ctx, "three-tier", [copied], verification_id_value="f" * 64,
                source_identity=identity, baseline="a" * 40, final="b" * 40,
            )

        self.assertIsNotNone(claims())
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
        lifecycle.owned_git_origin._git(Path(state["project_root"]), "remote", "add", "foreign", "/unowned.git")
        self.assertIsNone(claims())
        lifecycle.owned_git_origin._git(Path(state["project_root"]), "remote", "remove", "foreign")
        active.unlink()
        self.assertIsNone(claims())

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
        baseline_sha = state["git"]["baseline_sha"]
        self.require_command.side_effect = [baseline_sha, ""]
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
        phase = lifecycle.REQUIRED_SDLC_PHASES[0]
        artifact = Path(state["run_root"]) / f"evidence/phases/{phase}.json"
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
            "evidence/gui-uat/00000000000000000000000000000002/checkpoint-0.png"
        ).write_bytes(b"not an image")
        with self.assertRaisesRegex(
            lifecycle.SemanticEvidenceError, "artifact changed or is missing"
        ):
            lifecycle.validate_semantic_results(state, keep=True)


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
            self.assertRaisesRegex(lifecycle.LifecycleError, "exactly its one approved origin"),
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
