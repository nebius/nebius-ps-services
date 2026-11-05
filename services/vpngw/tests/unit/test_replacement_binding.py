from types import SimpleNamespace

import pytest

from nebius_vpngw.deploy.replacement_binding import admit, admit_absence


def instance(role, *, physical="gateway-0"):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            id="compute-exact",
            name=physical,
            labels={
                "nebius-vpngw-member": "gateway-0",
                "nebius-vpngw-replacement": "a" * 64,
                "nebius-vpngw-resource-role": role,
            },
        )
    )


@pytest.mark.parametrize("role", ["source", "candidate", "committed", "retired"])
def test_pending_replacement_blocks_normal_member_operations(role):
    with pytest.raises(RuntimeError, match="resume-replacement"):
        admit(instance(role), "gateway-0")


def test_completed_binding_is_admitted_only_for_its_logical_member():
    admit(instance("active"), "gateway-0")
    with pytest.raises(RuntimeError, match="pending"):
        admit(instance("active"), "other-gateway-0")


def test_rename_gap_is_not_treated_as_absence(monkeypatch):
    from nebius.api.nebius.compute import v1

    request = SimpleNamespace(
        wait=lambda: SimpleNamespace(
            items=[instance("candidate", physical="gateway-next")], next_page_token=""
        )
    )
    monkeypatch.setattr(
        v1, "InstanceServiceClient", lambda _: SimpleNamespace(list=lambda _request: request)
    )
    with pytest.raises(RuntimeError, match="logical binding exists"):
        admit_absence(object(), "project-test", "gateway-0")
    admit_absence(object(), "project-test", "different-0")


def test_tunnel_admission_resolves_every_member_without_yaml_fallback(monkeypatch):
    from unittest.mock import Mock

    from nebius_vpngw.deploy import vm_manager
    from nebius_vpngw.deploy.replacement_binding import ReplacementPending, require_idle

    members = [SimpleNamespace(hostname=f"gateway-{i}", external_ip="192.0.2.99") for i in range(2)]
    manager = Mock()
    manager.__enter__ = Mock(return_value=manager)
    manager.__exit__ = Mock(return_value=False)
    manager._get_vm_by_name.side_effect = [instance("active"), ReplacementPending("pending")]
    manager._vm_public_ip_from_object.return_value = "192.0.2.10"
    monkeypatch.setattr(vm_manager, "VMManager", lambda **kw: manager)
    plan = SimpleNamespace(
        gateway_group=SimpleNamespace(region="region"), iter_instance_configs=lambda: iter(members)
    )
    with pytest.raises(ReplacementPending):
        require_idle({"project_id": "project-test"}, plan)
    assert manager._get_vm_by_name.call_count == 2
    assert members[0].external_ip == "192.0.2.10"
    manager._get_vm_by_name.side_effect = [None]
    with pytest.raises(RuntimeError, match="no SSH address fallback"):
        require_idle({"project_id": "project-test"}, plan)
