from copy import deepcopy
from types import SimpleNamespace

import pytest

from nebius_vpngw.deploy import repair_ssh


@pytest.fixture
def retained(monkeypatch):
    current = {
        "signature": ("vm-a", "gateway-0", "project-a", "26", "192.0.2.1", "bootstrap-hash"),
        "binding": {
            "compute": "vm-a",
            "project": "project-a",
            "disk": "disk-a",
            "nics": [
                {"name": "eth0", "subnet": "subnet-a", "private": "private-a", "public": "public-a"}
            ],
        },
    }
    monkeypatch.setattr(repair_ssh, "retained_binding", lambda value: value["binding"])
    manager = SimpleNamespace(
        project_id="project-a",
        _build_sdk_client=lambda _: object(),
        _vm_ha_ssh_member_signature=lambda value: value["signature"],
        _get_vm_by_id_for_vm_ha_preflight=lambda *_: current,
    )
    check = repair_ssh.bindings(
        manager, SimpleNamespace(region="region-a"), {"gateway-0": (deepcopy(current), "192.0.2.1")}
    )["gateway-0"]
    return current, check


def test_repair_keeps_identity_proof_across_its_own_compute_revision_updates(retained):
    current, check = retained
    values = list(current["signature"])
    values[3] = "28"
    current["signature"] = tuple(values)
    check()


@pytest.mark.parametrize("field", [0, 1, 2, 4, 5])
def test_revision_tolerance_does_not_allow_vm_transport_or_bootstrap_rebinding(retained, field):
    current, check = retained
    values = list(current["signature"])
    values[field] = "foreign"
    current["signature"] = tuple(values)
    with pytest.raises(RuntimeError, match="retained SSH identity changed"):
        check()


@pytest.mark.parametrize("field", ["disk", "subnet", "private", "public"])
def test_same_vm_and_key_cannot_hide_disk_or_nic_replacement(retained, field):
    current, check = retained
    if field == "disk":
        current["binding"][field] = "foreign"
    else:
        current["binding"]["nics"][0][field] = "foreign"
    with pytest.raises(RuntimeError, match="retained SSH identity changed"):
        check()
