from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_replacement_guard import projection
from test_replacement_ordinary import instance, journal

from nebius_vpngw.deploy import replacement_ha, replacement_ordinary, vm_ha_identity
from nebius_vpngw.deploy.replacement_ha import HAReplacement
from nebius_vpngw.deploy.replacement_ordinary import OrdinaryReplacement
from nebius_vpngw.deploy.security_groups import NICSecurityGroups
from nebius_vpngw.deploy.vm_ha_cloud import InstanceCloudState


def ports(kind=OrdinaryReplacement):
    value = kind.__new__(kind)
    value.sdk = object()
    value.local = {}
    value.manager = SimpleNamespace(
        project_id="project-test",
        _build_cloud_init=lambda **_: "#cloud-config\nwrite_files: []\n",
        _render_gateway_ssh_enrollment_cloud_init=lambda data, _: data,
        _resolve_boot_image_id=lambda *_: "image-test",
        _get_vm_by_name=lambda *_: instance(),
        _instance_boot_disk_id=lambda _: "disk-source",
        _get_disk_by_id=lambda *_: SimpleNamespace(metadata=SimpleNamespace(labels={})),
    )
    value.deployment = SimpleNamespace(
        gateway={},
        gateway_group=SimpleNamespace(
            security_group_ids=(), name="gateway", vm_spec={}, ingress_policies=[None, None]
        ),
    )
    value.ssh = SimpleNamespace(_ssh_policy=SimpleNamespace(identity_for=lambda _: object()))
    value.instances = {
        f"gateway-{index}": SimpleNamespace(
            hostname=f"gateway-{index}", instance_index=index, config_yaml="gateway: {}\n"
        )
        for index in range(2)
    }
    value.snapshots = {
        logical: {"projection": projection(), "digest": "a" * 64} for logical in value.instances
    }
    return value


def oversize(snapshot):
    # Valid SSH authorization can exceed cloud-init's smaller transport limit.
    snapshot["projection"]["files"]["/home/operator/.ssh/authorized_keys"]["content"] += (
        "#" + "x" * 40000 + "\n"
    )


def test_inspection_checks_later_member_before_admitting_group(monkeypatch, tmp_path):
    value = ports()
    value.wheel = tmp_path / "agent.whl"
    value.wheel.write_bytes(b"offline artifact")
    value.access = lambda _identifier, logical: (
        SimpleNamespace(inspect=lambda _: value.snapshots[logical]),
        SimpleNamespace(address="192.0.2.1", compute_id="compute-source"),
    )
    monkeypatch.setattr(
        replacement_ordinary, "instance_cloud_state", lambda _: InstanceCloudState.RUNNING
    )
    monkeypatch.setattr(
        replacement_ordinary.ordinary_apply,
        "inspect_plan",
        lambda _ssh, _address, config, *_args, **_kwargs: SimpleNamespace(
            observation={
                "config_sha256": replacement_ordinary.ordinary_apply.sha(
                    config.config_yaml.encode()
                ),
                "verified": True,
            },
            close=lambda: None,
        ),
    )
    monkeypatch.setattr(
        replacement_ordinary.security_groups,
        "capture",
        lambda *_: [NICSecurityGroups("eth0", "subnet-test", "network-test", (), ())],
    )
    monkeypatch.setattr(replacement_ordinary.replacement_routes, "inventory", lambda *_, **_kw: ())
    monkeypatch.setattr(
        replacement_ordinary.replacement_runtime_routes, "inspect", lambda *_: (None, None)
    )
    assert len(value.inspect().members) == 2
    oversize(value.snapshots["gateway-1"])
    with pytest.raises(ValueError, match="cloud-init exceeds"):
        value.inspect()


@pytest.mark.parametrize("kind", [OrdinaryReplacement, HAReplacement])
def test_prepare_revalidates_final_cloud_init_before_any_effect(monkeypatch, kind):
    value = ports(kind)
    oversize(value.snapshots["gateway-0"])
    monkeypatch.setattr(vm_ha_identity, "render_provisioning_marker", lambda *_: "test-marker")
    value.access = Mock(return_value=(None, None))
    value.request = Mock()
    state = journal()
    plan = state.state["transaction"]["plan"]
    plan.update(image_id="image-test", desired={})
    plan["members"][0].update(
        public_allocations=["allocation-public"],
        authorization_sha256="a" * 64,
        candidate_name="candidate",
        candidate_disk_name="candidate-boot",
        security_groups=[{"subnet_id": "subnet-test"}],
    )
    cloud = Mock()
    cloud.create_candidate.side_effect = lambda **values: vm_ha_identity.require_cloud_init_size(
        values["cloud_init"]
    )
    monkeypatch.setattr(replacement_ordinary, "Cloud", lambda *_: cloud)
    monkeypatch.setattr(replacement_ha, "Cloud", lambda *_: cloud)
    with pytest.raises(ValueError, match="cloud-init exceeds"):
        value.execute(state, step="prepare", effect_id="effect")
    assert cloud.mock_calls == []
    value.request.assert_not_called()
