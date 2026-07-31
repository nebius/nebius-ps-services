#!/usr/bin/env python3
"""Offline mocked tests for the bounded Docker smoke helper."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("container_smoke_test.py")
sys.path.insert(0, str(SCRIPT.parent))
COMMON = importlib.import_module("container_runtime_common")
SPEC = importlib.util.spec_from_file_location("container_smoke_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

CONTAINER_ID = "a" * 64
TOKEN = "b" * 24


def arguments(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "image": "example/service:1.2.3",
        "name_prefix": "codex-container-smoke",
        "timeout": 120.0,
        "shutdown_timeout": 10.0,
        "memory": "1g",
        "cpus": 2.0,
        "pids_limit": 256,
        "read_only": False,
        "tmpfs": [],
        "env": [],
        "command_json": None,
        "health_command_json": None,
        "health_path": None,
        "container_port": None,
        "network": "none",
        "external_network": False,
        "format": "json",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class SmokeSafetyTest(unittest.TestCase):
    def test_default_create_and_shutdown_are_bounded(self) -> None:
        calls: list[list[str]] = []
        states = iter(
            (
                {"Running": True, "ExitCode": 0},
                {"Running": True, "ExitCode": 0},
                {"Running": False, "ExitCode": 0},
            )
        )

        def fake_run(argv: list[str], **_: object) -> object:
            calls.append(argv)
            if argv[:2] == ["docker", "create"]:
                return COMMON.CommandResult([], 0, CONTAINER_ID + "\n", "")
            if argv[:3] == ["docker", "start", CONTAINER_ID]:
                return COMMON.CommandResult([], 0, CONTAINER_ID + "\n", "")
            if argv[:3] == ["docker", "inspect", "--format"]:
                if "json .State" in argv[3]:
                    import json

                    return COMMON.CommandResult([], 0, json.dumps(next(states)), "")
                return COMMON.CommandResult([], 0, TOKEN + "\n", "")
            return COMMON.CommandResult([], 0, "", "")

        with (
            mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/docker"),
            mock.patch.object(MODULE.secrets, "token_hex", return_value=TOKEN),
            mock.patch.object(MODULE, "run_command", side_effect=fake_run),
            mock.patch.object(MODULE.time, "sleep"),
        ):
            report = MODULE.smoke(arguments())

        self.assertEqual(report["status"], "pass")
        create = calls[0]
        self.assertIn("never", create)
        self.assertIn("none", create)
        self.assertIn("ALL", create)
        self.assertIn("no-new-privileges", create)
        self.assertIn("--memory", create)
        self.assertIn("--pids-limit", create)
        self.assertNotIn("--privileged", create)
        self.assertNotIn("/var/run/docker.sock", create)
        self.assertIn(["docker", "kill", "--signal", "TERM", CONTAINER_ID], calls)
        self.assertIn(["docker", "rm", "--force", CONTAINER_ID], calls)

    def test_environment_value_never_enters_command_or_report(self) -> None:
        calls: list[list[str]] = []

        def fake_run(argv: list[str], **_: object) -> object:
            calls.append(argv)
            if argv[:2] == ["docker", "create"]:
                return COMMON.CommandResult([], 1, "", "")
            return COMMON.CommandResult([], 0, "", "")

        with (
            mock.patch.dict(os.environ, {"API_TOKEN": "very-secret"}, clear=False),
            mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/docker"),
            mock.patch.object(MODULE.secrets, "token_hex", return_value=TOKEN),
            mock.patch.object(MODULE, "run_command", side_effect=fake_run),
        ):
            report = MODULE.smoke(arguments(env=["API_TOKEN"]))

        self.assertEqual(report["environment_names"], ["API_TOKEN"])
        self.assertNotIn("very-secret", repr(calls))
        self.assertNotIn("very-secret", repr(report))
        self.assertIn("--env", calls[0])
        self.assertIn("API_TOKEN", calls[0])

    def test_create_timeout_cleans_a_late_container_by_owned_name(self) -> None:
        calls: list[list[str]] = []

        def fake_run(argv: list[str], **_: object) -> object:
            calls.append(argv)
            if argv[:2] == ["docker", "create"]:
                return COMMON.CommandResult([], 124, "", "")
            if argv[:3] == ["docker", "inspect", "--format"]:
                return COMMON.CommandResult([], 0, TOKEN + "\n", "")
            if argv[:3] == ["docker", "rm", "--force"]:
                return COMMON.CommandResult([], 0, "", "")
            return COMMON.CommandResult([], 1, "", "")

        with (
            mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/docker"),
            mock.patch.object(MODULE.secrets, "token_hex", return_value=TOKEN),
            mock.patch.object(MODULE, "run_command", side_effect=fake_run),
        ):
            report = MODULE.smoke(arguments())

        name = f"codex-container-smoke-{TOKEN}"
        self.assertEqual(report["status"], "fail")
        self.assertTrue(report["evidence"]["cleanup_verified"])
        self.assertIn(["docker", "rm", "--force", name], calls)

    def test_non_isolated_network_requires_explicit_authorization(self) -> None:
        with (
            mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/docker"),
            self.assertRaisesRegex(MODULE.SmokeError, "--external-network"),
        ):
            MODULE.smoke(arguments(network="bridge"))

    def test_unset_environment_name_fails_before_create(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/docker"),
            self.assertRaisesRegex(MODULE.SmokeError, "not set"),
        ):
            MODULE.smoke(arguments(env=["API_TOKEN"]))

    def test_cleanup_rejects_label_mismatch_without_removal(self) -> None:
        mismatch = COMMON.CommandResult([], 0, "not-ours\n", "")
        with mock.patch.object(MODULE, "run_command", return_value=mismatch) as runner:
            self.assertFalse(MODULE._cleanup(CONTAINER_ID, TOKEN))
        self.assertEqual(runner.call_count, 1)

    def test_graceful_timeout_uses_kill_then_owned_cleanup(self) -> None:
        calls: list[list[str]] = []
        kill_timeouts: list[float | None] = []
        state = {"Running": True, "ExitCode": 0}

        def fake_run(argv: list[str], **kwargs: object) -> object:
            calls.append(argv)
            if argv[:4] == ["docker", "kill", "--signal", "KILL"]:
                kill_timeouts.append(kwargs.get("timeout"))
            if argv[:2] == ["docker", "create"]:
                return COMMON.CommandResult([], 0, CONTAINER_ID + "\n", "")
            if argv[:3] == ["docker", "inspect", "--format"]:
                if "json .State" in argv[3]:
                    import json

                    return COMMON.CommandResult([], 0, json.dumps(state), "")
                return COMMON.CommandResult([], 0, TOKEN + "\n", "")
            return COMMON.CommandResult([], 0, "", "")

        with (
            mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/docker"),
            mock.patch.object(MODULE.secrets, "token_hex", return_value=TOKEN),
            mock.patch.object(MODULE, "run_command", side_effect=fake_run),
            mock.patch.object(MODULE, "_wait_for_stop", return_value=(False, state)),
        ):
            report = MODULE.smoke(arguments())

        self.assertEqual(report["status"], "fail")
        self.assertTrue(report["evidence"]["sigkill_after_timeout"])
        self.assertIn(["docker", "kill", "--signal", "KILL", CONTAINER_ID], calls)
        self.assertIn(["docker", "rm", "--force", CONTAINER_ID], calls)
        self.assertEqual(kill_timeouts, [5.0])


class InputValidationTest(unittest.TestCase):
    def test_json_command_requires_string_array(self) -> None:
        with self.assertRaisesRegex(MODULE.SmokeError, "JSON string array"):
            MODULE._json_array('{"command": "id"}', "--command-json")

    def test_tmpfs_parent_segment_is_rejected(self) -> None:
        with (
            mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/docker"),
            self.assertRaisesRegex(MODULE.SmokeError, "tmpfs"),
        ):
            MODULE.smoke(arguments(tmpfs=["/tmp/../secret"]))

    def test_health_endpoint_and_command_are_mutually_exclusive(self) -> None:
        with (
            mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/docker"),
            self.assertRaisesRegex(MODULE.SmokeError, "choose one"),
        ):
            MODULE.smoke(
                arguments(
                    health_path="/ready",
                    container_port=8080,
                    health_command_json='["true"]',
                )
            )

    def test_health_path_rejects_control_characters(self) -> None:
        with (
            mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/docker"),
            self.assertRaisesRegex(MODULE.SmokeError, "origin-form"),
        ):
            MODULE.smoke(
                arguments(
                    health_path="/ready\r\nHost: external.invalid",
                    container_port=8080,
                )
            )

    def test_unbounded_memory_value_is_rejected(self) -> None:
        with (
            mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/docker"),
            self.assertRaisesRegex(MODULE.SmokeError, "bounded values"),
        ):
            MODULE.smoke(arguments(memory="0"))

    def test_total_timeout_stops_before_container_creation(self) -> None:
        with (
            mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/docker"),
            mock.patch.object(MODULE.time, "monotonic", side_effect=(0.0, 2.0)),
            mock.patch.object(MODULE, "run_command") as runner,
        ):
            report = MODULE.smoke(arguments(timeout=1.0))

        self.assertEqual(report["status"], "fail")
        self.assertIn("total timeout", report["error"])
        runner.assert_not_called()

    def test_health_command_polling_honors_absolute_deadline(self) -> None:
        failed = COMMON.CommandResult([], 1, "", "")
        with (
            mock.patch.object(
                MODULE.time,
                "monotonic",
                side_effect=(0.0, 0.8, 1.1),
            ),
            mock.patch.object(
                MODULE,
                "run_command",
                return_value=failed,
            ) as runner,
            mock.patch.object(MODULE, "_inspect_state") as inspect_state,
        ):
            ready = MODULE._command_ready(CONTAINER_ID, ["check"], 1.0)

        self.assertFalse(ready)
        self.assertAlmostEqual(runner.call_args.kwargs["timeout"], 0.2)
        inspect_state.assert_not_called()

    def test_shutdown_polling_honors_absolute_deadline(self) -> None:
        with (
            mock.patch.object(
                MODULE.time,
                "monotonic",
                side_effect=(0.0, 0.9, 1.1, 1.2),
            ),
            mock.patch.object(
                MODULE,
                "_inspect_state",
                return_value={"Running": True},
            ) as inspect_state,
            mock.patch.object(MODULE.time, "sleep") as sleep,
        ):
            stopped, _ = MODULE._wait_for_stop(CONTAINER_ID, 1.0)

        self.assertFalse(stopped)
        self.assertAlmostEqual(inspect_state.call_args.kwargs["timeout"], 0.1)
        sleep.assert_not_called()

    def test_health_redirects_are_not_followed(self) -> None:
        handler = MODULE.NoRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(
                None,
                None,
                302,
                "redirect",
                {},
                "https://external.example.invalid/",
            )
        )
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError(
            "http://127.0.0.1:1234/ready",
            302,
            "redirect",
            {},
            None,
        )
        with (
            mock.patch.object(
                MODULE.urllib.request, "build_opener", return_value=opener
            ) as build_opener,
            mock.patch.object(
                MODULE.time,
                "monotonic",
                side_effect=(0.0, 0.1, 1.1, 1.2),
            ),
            mock.patch.object(MODULE.time, "sleep"),
        ):
            self.assertFalse(MODULE._endpoint_ready(1234, "/ready", 1.0))

        self.assertEqual(
            opener.open.call_args_list,
            [mock.call("http://127.0.0.1:1234/ready", timeout=0.9)],
        )
        handlers = build_opener.call_args.args
        proxy = next(
            item
            for item in handlers
            if isinstance(item, MODULE.urllib.request.ProxyHandler)
        )
        self.assertEqual(proxy.proxies, {})
        self.assertTrue(
            any(isinstance(item, MODULE.NoRedirectHandler) for item in handlers)
        )


if __name__ == "__main__":
    unittest.main()
