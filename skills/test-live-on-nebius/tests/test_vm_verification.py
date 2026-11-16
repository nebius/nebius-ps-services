"""Offline acceptance boundaries; these tests provide no live guest evidence."""

import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))
import vm_trial
from live_state import Journal, StateError


class VMVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        )
        self.addCleanup(self.temp.cleanup)
        self.j = Journal.create(
            self.temp.name,
            {
                "project": "project-example",
                "inputs": {"platform": "cpu-d3", "preset": "4vcpu-16gb"},
            },
        )
        with self.j.lock():
            for kind in ("disk", "vm"):
                self.j.own(
                    kind,
                    resource_id=kind,
                    kind=kind,
                    parent="project-example",
                    owner="sdk",
                )
        self.vm = NS(
            spec=NS(resources=NS(platform="cpu-d3", preset="4vcpu-16gb")),
            status=NS(state=NS(name="RUNNING")),
        )
        self.disk = NS(spec=NS(size_gibibytes=40, type=NS(name="NETWORK_SSD")))

    def evidence(self):
        return {
            "run_id": self.j.data["run_id"],
            "instance_id": "vm",
            "source": "authenticated-serial-logs",
            "facts": {
                "cpu": 4,
                "memory_kib": 15 * 2**20,
                "disk_bytes": 40 * 2**30,
                "checksum": True,
            },
        }

    def verify(self, evidence=None):
        if evidence is not None:
            path = self.j.directory / "guest-evidence.json"
            path.write_text(json.dumps(evidence))
            path.chmod(0o600)
        with (
            self.j.lock(),
            patch.object(vm_trial, "context", return_value=contextlib.nullcontext()),
            patch.object(
                vm_trial,
                "clients",
                return_value={
                    "vm": (Mock(), Mock()),
                    "disk": (Mock(), Mock()),
                },
            ),
            patch("sdk.runtime.wait_ready", return_value=self.vm),
            patch("sdk.runtime.rpc", return_value=self.disk),
            patch.object(vm_trial, "remaining", return_value=10),
            patch.object(
                vm_trial.time,
                "monotonic",
                side_effect=[0, 0] if evidence is not None else [0, 121],
            ),
        ):
            return vm_trial.verify(self.j, 300)

    def test_exact_configuration_and_scoped_guest_facts_pass(self):
        self.assertTrue(self.verify(self.evidence()))
        self.assertTrue(self.j.data["evidence"]["control_plane"]["passed"])
        self.assertTrue(self.j.data["evidence"]["guest"]["passed"])

    def test_running_vm_without_guest_evidence_is_not_a_pass(self):
        self.assertFalse(self.verify())
        self.assertEqual(self.j.data["evidence"]["guest"]["status"], "unavailable")

    def test_wrong_run_instance_or_collector_source_is_rejected(self):
        for key in ("run_id", "instance_id", "source"):
            evidence = self.evidence()
            evidence[key] = "foreign"
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(StateError, "GUEST_EVIDENCE_SCOPE"),
            ):
                self.verify(evidence)

    def test_wrong_guest_facts_fail(self):
        for key, value in (
            ("cpu", 2),
            ("memory_kib", 8 * 2**20),
            ("disk_bytes", 20 * 2**30),
            ("checksum", False),
        ):
            evidence = self.evidence()
            evidence["facts"][key] = value
            with self.subTest(key=key):
                self.assertFalse(self.verify(evidence))

    def test_wrong_control_plane_shape_fails_before_guest_acceptance(self):
        self.disk.spec.size_gibibytes = 20
        self.assertFalse(self.verify(self.evidence()))
        self.assertFalse(self.j.data["evidence"]["control_plane"]["passed"])
        self.assertNotIn("guest", self.j.data["evidence"])
