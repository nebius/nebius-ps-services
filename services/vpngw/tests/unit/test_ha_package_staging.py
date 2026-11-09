"""Repeated staging revalidates guest bytes without retransferring them."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from nebius_vpngw import runtime_migration
from nebius_vpngw.deploy import ordinary_apply, vm_ha_package

pytestmark = pytest.mark.usefixtures("runtime_migration_guest")


@pytest.fixture
def staging(tmp_path, monkeypatch):
    wheel = tmp_path / "product.whl"
    wheel.write_bytes(b"approved product")
    dependency = tmp_path / "dependency.whl"
    dependency.write_bytes(b"approved dependency")
    directory = tmp_path / "guest"
    directory.mkdir()
    monkeypatch.setattr(runtime_migration, "stage", lambda _plan: directory)
    requests = []

    def remote(_ssh, _target, _instance, _local, request):
        requests.append(deepcopy(request))
        return {
            "status": "staged",
            "receipt": runtime_migration.stage_payload(request["manifest"], request),
        }

    monkeypatch.setattr(ordinary_apply, "remote", remote)
    plan = vm_ha_package.VMHAPackagePlan(
        "host",
        SimpleNamespace(hostname="gateway"),
        wheel,
        {
            "wheel_name": wheel.name,
            "wheel_sha256": ordinary_apply.sha(wheel.read_bytes()),
            "dependency_wheels": {dependency.name: ordinary_apply.sha(dependency.read_bytes())},
            "runtime_plan": {"approved": "native packages"},
        },
        {"boot_id": "original-boot"},
        [dependency],
        [],
        "compute-a",
        None,
    )
    artifact = SimpleNamespace(open_verified=lambda: wheel.open("rb"))
    return plan, artifact, directory / "payload", requests


def test_repeated_stage_rechecks_payload_and_publishes_new_parent(staging):
    plan, artifact, directory, requests = staging
    vm_ha_package.stage(object(), plan, {}, artifact=artifact)
    plan.manifest["ha_parent"] = "exact-lifecycle-operation"
    receipt = vm_ha_package.stage(object(), plan, {}, artifact=artifact)
    assert "wheel" in requests[0] and "dependency_wheels" in requests[0]
    assert "wheel" not in requests[1] and "dependency_wheels" not in requests[1]
    assert json.loads((directory / "manifest.json").read_text()) == plan.manifest
    assert receipt["manifest"] == ordinary_apply.digest(plan.manifest)


@pytest.mark.parametrize("name", ["product.whl", "dependency.whl"])
@pytest.mark.parametrize("fault", ["changed", "missing", "symlink"])
def test_repeated_stage_rejects_guest_payload_drift(staging, name, fault):
    plan, artifact, directory, _requests = staging
    vm_ha_package.stage(object(), plan, {}, artifact=artifact)
    path = directory / name
    original = path.read_bytes()
    if fault == "changed":
        path.write_bytes(b"changed after staging")
    else:
        path.unlink()
        if fault == "symlink":
            target = directory / "unexpected"
            target.write_bytes(original)
            path.symlink_to(target)
    with pytest.raises((RuntimeError, FileNotFoundError)):
        vm_ha_package.stage(object(), plan, {}, artifact=artifact)


@pytest.mark.parametrize("identity", ["target", "target_identity", "boot_id"])
def test_payload_upload_hint_does_not_cross_member_identity(staging, identity):
    plan, artifact, _directory, requests = staging
    vm_ha_package.stage(object(), plan, {}, artifact=artifact)
    if identity == "boot_id":
        plan.observation[identity] = "different-boot"
    else:
        setattr(plan, identity, "different-member")
    vm_ha_package.stage(object(), plan, {}, artifact=artifact)
    assert "wheel" in requests[1] and "dependency_wheels" in requests[1]
