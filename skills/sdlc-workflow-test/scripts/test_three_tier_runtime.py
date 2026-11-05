#!/usr/bin/env python3
"""Regression oracles for verifier-owned discovery; Docker responses stay synthetic."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import three_tier_runtime as runtime


class InventoryFixture:
    def __init__(self):
        self.state = {"verification_id": "a" * 32, "compose_project": "owned-project"}
        self.target = {"path": "/synthetic/checkout", "head": "b" * 40}
        self.image = "sha256:" + "c" * 64
        self.labels = {
            "sdlc-workflow-test.verification-id": "a" * 32,
            "com.docker.compose.project": "owned-project",
        }
        self.records = {}
        for role in ("web", "db"):
            self.records[("container", role)] = {
                "Id": role,
                "Config": {
                    "Labels": {**self.labels, "com.docker.compose.service": role}
                },
                "Image": self.image if role == "web" else "postgres-image",
                "State": {"Running": True},
                "Mounts": []
                if role == "web"
                else [
                    {
                        "Type": "volume",
                        "Name": "data",
                        "Destination": "/var/lib/postgresql/data",
                    }
                ],
                "NetworkSettings": {
                    "Networks": {"shared": {"NetworkID": "network"}},
                    "Ports": {
                        "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49152"}]
                    }
                    if role == "web"
                    else {"5432/tcp": None},
                },
            }
        self.records[("network", "network")] = {
            "Id": "network",
            "Labels": dict(self.labels),
        }
        self.records[("volume", "data")] = {"Name": "data", "Labels": dict(self.labels)}
        self.records[("image", self.image)] = {
            "Id": self.image,
            "Config": {"Labels": dict(self.labels)},
        }
        self.candidates = ["db", "web"]
        self.calls = []

    def command(self, argv):
        self.calls.append(argv)
        if argv[2] == "ls":
            return subprocess.CompletedProcess(argv, 0, "\n".join(self.candidates), "")
        return subprocess.CompletedProcess(
            argv, 0, json.dumps([self.records[(argv[1], argv[3])]]), ""
        )

    def observe(self, **kwargs):
        with mock.patch.object(
            runtime, "load_build", return_value={"image_id": self.image}
        ):
            return runtime.observe_inventory(
                self.state,
                self.target,
                self.command,
                web_path=kwargs.pop("web_path", "/"),
                health_path=kwargs.pop("health_path", "/healthz"),
                mode=kwargs.pop("mode", "acceptance"),
                **kwargs,
            )


class RuntimeInventoryTests(unittest.TestCase):
    def test_relationships_select_current_image_without_listing_historical_images(self):
        fixture = InventoryFixture()
        result = fixture.observe()
        self.assertEqual(result["outcome"], "PASS")
        self.assertEqual(
            result["resources"],
            {
                "containers": ["web", "db"],
                "networks": ["network"],
                "volumes": ["data"],
                "images": [fixture.image],
            },
        )
        self.assertEqual(
            result["endpoints"],
            {
                "web": "http://127.0.0.1:49152/",
                "health": "http://127.0.0.1:49152/healthz",
                "api": "http://127.0.0.1:49152/api/v1/tasks",
                "database": "db:5432",
            },
        )
        self.assertFalse(any(a[1:3] == ["image", "ls"] for a in fixture.calls))

    def test_named_product_failures(self):
        cases = [
            ("web.candidate_count", lambda f: f.candidates.remove("web")),
            (
                "web.candidate_count",
                lambda f: (
                    f.candidates.append("extra"),
                    f.records.update(
                        {
                            ("container", "extra"): {
                                **copy.deepcopy(f.records[("container", "web")]),
                                "Id": "extra",
                            }
                        }
                    ),
                ),
            ),
            (
                "container.ownership",
                lambda f: f.records[("container", "web")]["Config"]["Labels"].pop(
                    "sdlc-workflow-test.verification-id"
                ),
            ),
            (
                "container.ownership",
                lambda f: f.records[("container", "web")]["Config"]["Labels"].update(
                    {"com.docker.compose.project": "foreign"}
                ),
            ),
            (
                "web.mounts_absent",
                lambda f: f.records[("container", "web")]["Mounts"].append(
                    {"Type": "bind", "Source": "/private/secret"}
                ),
            ),
            (
                "db.named_volume",
                lambda f: f.records[("container", "db")]["Mounts"].append(
                    {"Type": "bind"}
                ),
            ),
            (
                "db.named_volume",
                lambda f: f.records[("container", "db")]["Mounts"][0].update(
                    {"Type": "bind"}
                ),
            ),
            (
                "web.network_count",
                lambda f: f.records[("container", "web")]["NetworkSettings"][
                    "Networks"
                ].update({"extra": {"NetworkID": "foreign"}}),
            ),
            (
                "shared.network",
                lambda f: f.records[("container", "db")]["NetworkSettings"]["Networks"][
                    "shared"
                ].update({"NetworkID": "foreign"}),
            ),
            (
                "web.loopback_binding",
                lambda f: f.records[("container", "web")]["NetworkSettings"].update(
                    {"Ports": {}}
                ),
            ),
            (
                "web.loopback_binding",
                lambda f: f.records[("container", "web")]["NetworkSettings"]["Ports"][
                    "8000/tcp"
                ][0].update({"HostIp": "0.0.0.0"}),
            ),
            (
                "db.ports_unpublished",
                lambda f: f.records[("container", "db")]["NetworkSettings"].update(
                    {
                        "Ports": {
                            "5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}]
                        }
                    }
                ),
            ),
            (
                "web.build_match",
                lambda f: f.records[("container", "web")].update(
                    {"Image": "sha256:" + "d" * 64}
                ),
            ),
            (
                "db.running",
                lambda f: f.records[("container", "db")]["State"].update(
                    {"Running": False}
                ),
            ),
        ]
        for name, mutate in cases:
            with self.subTest(predicate=name):
                f = InventoryFixture()
                mutate(f)
                result = f.observe()
                self.assertEqual(result["outcome"], "FAIL")
                self.assertEqual(result["category"], "product_predicate")
                self.assertEqual(result["predicates"][-1]["name"], name)
                self.assertNotIn("resources", result)
                self.assertNotIn("secret", json.dumps(result))

    def test_route_validation_rejects_origin_query_fragment_controls_and_encoded_escapes(
        self,
    ):
        for path in (
            "http://localhost/",
            "//other/x",
            "/x//y",
            "/?q=x",
            "/#x",
            "/\n",
            "/\\other",
            "/%2f%2fother",
            "/%0a",
            "/%3fsecret",
            "",
            None,
        ):
            with self.subTest(path=path):
                with self.assertRaises(runtime.RuntimeEvidenceError):
                    runtime.validate_route_path(path)
        self.assertEqual(runtime.validate_route_path("/tasks/"), "/tasks/")

    def test_inspection_failures_are_distinct_and_bounded(self):
        for output, code in (
            ("password=secret" * 100000, 1),
            ("broken JSON", 0),
            ("[]", 0),
            ("null", 0),
            ("{}", 0),
        ):
            f = InventoryFixture()
            original = f.command
            f.command = (
                lambda argv: original(argv)
                if argv[2] == "ls"
                else subprocess.CompletedProcess(argv, code, output, "token=hidden")
            )
            result = f.observe()
            self.assertEqual(result["category"], "inspection_failure")
            self.assertLess(len(json.dumps(result)), 4096)
            self.assertNotIn("secret", json.dumps(result))
            self.assertNotIn("hidden", json.dumps(result))

    def test_unexpected_observer_exception_never_leaks_exception_text(self):
        f = InventoryFixture()
        f.command = mock.Mock(side_effect=RuntimeError("password=secret"))
        result = f.observe()
        self.assertEqual(result["category"], "observer_exception")
        self.assertNotIn("secret", json.dumps(result))

    def test_malformed_nested_shapes_are_inspection_failures(self):
        for field in ("Config", "State", "NetworkSettings", "Mounts"):
            f = InventoryFixture()
            f.records[("container", "web")][field] = "password=secret"
            result = f.observe()
            self.assertEqual(result["category"], "inspection_failure")
            self.assertNotIn("secret", json.dumps(result))

    def test_report_recommendations_follow_the_observed_owner(self):
        from three_tier_reporting import recommended_fix

        self.assertIn("Docker", recommended_fix("run-compose", "daemon unavailable"))
        self.assertIn("inventory", recommended_fix("inventory", "product_predicate"))
        self.assertIn(
            "Playwright", recommended_fix("sdlc-evaluate", "browser assertion")
        )
        self.assertNotIn(
            "Playwright", recommended_fix("sdlc-create-design", "missing mapping")
        )

    def test_generic_environment_evidence_does_not_imply_a_docker_failure(self):
        from three_tier_reporting import recommended_fix

        for source in ("sdlc-implement-plan", "sdlc-classify-failure", "startup"):
            with self.subTest(source=source):
                advice = recommended_fix(
                    source, "missing actual native worker environment evidence"
                )
                self.assertNotIn("Docker", advice)
                self.assertNotIn("Playwright", advice)
                self.assertIn("owner", advice)

    def test_browser_environment_evidence_retains_browser_investigation(self):
        from three_tier_reporting import recommended_fix

        advice = recommended_fix("sdlc-evaluate", "browser environment unavailable")
        self.assertIn("Playwright", advice)
        self.assertNotIn("Docker", advice)

    def test_capacity_rejection_precedes_unreadable_state(self):
        for state in (
            {"runtime_observations": [{}] * 128},
            {"runtime_observations": [], "other_evidence": "x" * 950000},
        ):
            before = json.dumps(state)
            with self.assertRaises(runtime.RuntimeEvidenceError):
                runtime.require_observation_capacity(state)
            self.assertEqual(json.dumps(state), before)

    def test_actual_command_transport_failures_keep_inspection_category(self):
        from three_tier_lifecycle import command

        for error in (
            FileNotFoundError("secret executable"),
            subprocess.TimeoutExpired("private command", 30),
        ):
            fixture = InventoryFixture()
            fixture.command = command
            with mock.patch("subprocess.run", side_effect=error):
                result = fixture.observe()
            self.assertEqual(result["category"], "inspection_failure")
            self.assertEqual(result["predicates"][-1]["name"], "inspection.available")
            self.assertNotIn("secret", json.dumps(result))

    def test_worker_observation_does_not_require_or_certify_a_build(self):
        f = InventoryFixture()
        with mock.patch.object(
            runtime,
            "load_build",
            side_effect=AssertionError("acceptance build consulted"),
        ):
            result = runtime.observe_inventory(
                f.state,
                f.target,
                f.command,
                web_path="/",
                health_path="/healthz",
                mode="development",
            )
        self.assertEqual(result["outcome"], "PASS")
        self.assertFalse(
            any(p["name"] == "web.build_match" for p in result["predicates"])
        )


if __name__ == "__main__":
    unittest.main()
