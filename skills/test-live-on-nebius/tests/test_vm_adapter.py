"""VM adapter admission and request checks with no cloud/network effects."""

import contextlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))
import vm_trial


class VMAdapterTests(unittest.TestCase):
    def test_sdk_request_is_private_exact_shape_and_owned(self):
        try:
            import nebius.api.nebius.compute.v1  # noqa: F401 - Optional typed SDK qualification.
        except ImportError:
            self.skipTest("pinned SDK environment required")
        j = NS(
            data={
                "run_id": "run-example",
                "plan": {
                    "engine": "sdk",
                    "project": "project-example",
                    "inputs": {
                        "network_id": "network-example",
                        "subnet_id": "subnet-example",
                        "platform": "cpu-d3",
                        "preset": "4vcpu-16gb",
                        "image_id": "image-example",
                        "ssh_public_key": "ssh-ed25519 AAAAtest",
                    },
                },
            }
        )
        recorder = Mock()
        recorder.create.side_effect = ["sg", "disk", "vm"]
        with (
            patch.object(
                vm_trial, "context", return_value=contextlib.nullcontext(Mock())
            ),
            patch.object(vm_trial, "Actions", return_value=recorder),
            patch.object(
                vm_trial,
                "clients",
                return_value={k: (Mock(),) for k in ["group", "disk", "vm", "bucket"]},
            ),
        ):
            vm_trial.provision(j, vm_trial.time.monotonic() + 60)
        disk = recorder.create.call_args_list[1].args[2]
        vm = recorder.create.call_args_list[2].args[2]
        self.assertEqual(disk.spec.size_gibibytes, 40)
        self.assertEqual(vm.spec.resources.preset, "4vcpu-16gb")
        self.assertEqual(vm.metadata.parent_id, "project-example")
        self.assertEqual(vm.metadata.labels["live-test-run"], "run-example")
        self.assertEqual(vm.spec.network_interfaces[0].security_groups[0].id, "sg")
        self.assertNotIn("public_ip_address", str(vm.spec.network_interfaces[0]))
        self.assertIn("NEBIUS_LIVE_run-example", vm.spec.cloud_init_user_data)
        self.assertNotIn("PRIVATE KEY", vm.spec.cloud_init_user_data)
        self.assertNotIn("access-token", vm.spec.cloud_init_user_data)


if __name__ == "__main__":
    unittest.main()
