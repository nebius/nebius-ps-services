from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.agent.vm_ha.mtls import ManagedMTLSStore
from nebius_vpngw.agent.vm_ha.mtls_actions import execute_mtls_action
from nebius_vpngw.cli import (
    _finalize_vm_ha_managed_mtls,
    _prepare_vm_ha_managed_mtls,
)


def _instance(node_id: str, hostname: str, role: str) -> SimpleNamespace:
    return SimpleNamespace(
        hostname=hostname,
        vm_ha_node=SimpleNamespace(node_id=node_id, role=SimpleNamespace(value=role)),
    )


def _binding(*, passive_compute: str = "compute-b") -> SimpleNamespace:
    return SimpleNamespace(
        cluster_id="cluster-a",
        shared_allocation_id="allocation-a",
        route_runtime_id="route-runtime-a",
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
        nebius_project_id="project-test",
        nebius_service_account_id="service-account-test",
        nebius_authorized_key_id="authorized-key-test",
        nodes=(
            SimpleNamespace(
                node_id="node-a",
                compute_id="compute-a",
                network_interface_name="eth0",
                role=SimpleNamespace(value="active"),
                nebius_credentials_path="/credentials/node-a.json",
                nebius_credentials_sha256="d" * 64,
            ),
            SimpleNamespace(
                node_id="node-b",
                compute_id=passive_compute,
                network_interface_name="eth0",
                role=SimpleNamespace(value="passive"),
                nebius_credentials_path="/credentials/node-b.json",
                nebius_credentials_sha256="d" * 64,
            ),
        ),
    )


class _LocalManagedSSH:
    def __init__(self, roots: dict[str, Path]) -> None:
        self.roots = roots
        self.actions: list[tuple[str, str]] = []
        self.package_preparations: list[str] = []

    def ensure_vm_ha_agent_package(self, target, inst_cfg, local_cfg):
        self.package_preparations.append(target)
        return {
            "schema": "nebius-vpngw/vm-ha-package-v1",
            "package_version": "test",
            "cryptography_version": "test",
            "cffi_version": "test",
        }

    def run_vm_ha_mtls_action(
        self,
        target,
        instance_name,
        local_cfg,
        *,
        action,
        request,
    ):
        self.actions.append((target, action))
        return execute_mtls_action(
            action,
            request,
            state_dir=self.roots[target],
            require_root=False,
        )


def _agent_statuses(transaction) -> dict[str, dict[str, object]]:
    receipts = {
        inst_cfg.vm_ha_node.node_id: receipt for inst_cfg, _target, receipt in transaction.nodes
    }
    statuses: dict[str, dict[str, object]] = {}
    for inst_cfg, _target, receipt in transaction.nodes:
        node_id = inst_cfg.vm_ha_node.node_id
        peer_id = next(candidate for candidate in receipts if candidate != node_id)
        peer = receipts[peer_id]
        statuses[node_id] = {
            "mtls": {
                "epoch": receipt["epoch"],
                "certificate_fingerprint": receipt["certificate_fingerprint"],
                "peer": {
                    "node_id": peer_id,
                    "boot_id": f"boot-{peer_id}",
                    "sequence": 17,
                    "epoch": peer["epoch"],
                    "certificate_fingerprint": peer["certificate_fingerprint"],
                    "fresh": True,
                },
            }
        }
    return statuses


def test_apply_bootstraps_managed_mtls_and_healthy_reapply_is_crypto_noop(
    tmp_path: Path,
) -> None:
    passive = _instance("node-b", "gateway-1", "passive")
    active = _instance("node-a", "gateway-0", "active")
    targets = {"gateway-0": "target-a", "gateway-1": "target-b"}
    ssh = _LocalManagedSSH({"target-a": tmp_path / "member-a", "target-b": tmp_path / "member-b"})
    binding = _binding()

    transaction = _prepare_vm_ha_managed_mtls(
        ssh=ssh,  # type: ignore[arg-type]
        ordered_instances=[passive, active],
        targets=targets,
        local_cfg={},
        runtime_binding=binding,
    )
    assert transaction.operation_kind == "bootstrap"
    _finalize_vm_ha_managed_mtls(
        ssh=ssh,  # type: ignore[arg-type]
        transaction=transaction,
        local_cfg={},
        agent_statuses=_agent_statuses(transaction),
    )
    fingerprints = {
        target: execute_mtls_action("status", {}, state_dir=root, require_root=False)["result"][
            "certificate_fingerprint"
        ]
        for target, root in ssh.roots.items()
    }
    action_count = len(ssh.actions)

    reapply = _prepare_vm_ha_managed_mtls(
        ssh=ssh,  # type: ignore[arg-type]
        ordered_instances=[passive, active],
        targets=targets,
        local_cfg={},
        runtime_binding=binding,
    )

    assert reapply.changed is False
    assert ssh.actions[action_count:] == [("target-b", "status"), ("target-a", "status")]
    assert {
        target: execute_mtls_action("status", {}, state_dir=root, require_root=False)["result"][
            "certificate_fingerprint"
        ]
        for target, root in ssh.roots.items()
    } == fingerprints


def test_apply_rejects_an_inhibition_only_interrupted_rotation(tmp_path: Path) -> None:
    passive = _instance("node-b", "gateway-1", "passive")
    active = _instance("node-a", "gateway-0", "active")
    targets = {"gateway-0": "target-a", "gateway-1": "target-b"}
    ssh = _LocalManagedSSH({"target-a": tmp_path / "member-a", "target-b": tmp_path / "member-b"})
    operation_id = "f" * 64
    ManagedMTLSStore(ssh.roots["target-b"] / "mtls").install_inhibition(
        operation_id=operation_id,
        cluster_id="cluster-a",
        node_id="node-b",
        generation_id="a" * 64,
    )

    with pytest.raises(RuntimeError, match="inhibited by a rotation transaction"):
        _prepare_vm_ha_managed_mtls(
            ssh=ssh,  # type: ignore[arg-type]
            ordered_instances=[passive, active],
            targets=targets,
            local_cfg={},
            runtime_binding=_binding(),
        )

    assert ssh.actions == [("target-b", "status"), ("target-a", "status")]


def test_apply_replacement_preserves_survivor_key_and_prunes_former_leaf(
    tmp_path: Path,
) -> None:
    passive = _instance("node-b", "gateway-1", "passive")
    active = _instance("node-a", "gateway-0", "active")
    targets = {"gateway-0": "target-a", "gateway-1": "target-b"}
    ssh = _LocalManagedSSH({"target-a": tmp_path / "member-a", "target-b": tmp_path / "member-b"})
    first = _prepare_vm_ha_managed_mtls(
        ssh=ssh,  # type: ignore[arg-type]
        ordered_instances=[passive, active],
        targets=targets,
        local_cfg={},
        runtime_binding=_binding(),
    )
    _finalize_vm_ha_managed_mtls(
        ssh=ssh,  # type: ignore[arg-type]
        transaction=first,
        local_cfg={},
        agent_statuses=_agent_statuses(first),
    )
    survivor_before = execute_mtls_action(
        "status", {}, state_dir=ssh.roots["target-a"], require_root=False
    )["result"]
    former_before = execute_mtls_action(
        "status", {}, state_dir=ssh.roots["target-b"], require_root=False
    )["result"]
    ssh.roots["target-b"] = tmp_path / "replacement-b"

    replacement = _prepare_vm_ha_managed_mtls(
        ssh=ssh,  # type: ignore[arg-type]
        ordered_instances=[passive, active],
        targets=targets,
        local_cfg={},
        runtime_binding=_binding(passive_compute="compute-b-new"),
    )
    assert replacement.operation_kind == "replacement"
    _finalize_vm_ha_managed_mtls(
        ssh=ssh,  # type: ignore[arg-type]
        transaction=replacement,
        local_cfg={},
        agent_statuses=_agent_statuses(replacement),
    )
    survivor_after = execute_mtls_action(
        "status", {}, state_dir=ssh.roots["target-a"], require_root=False
    )["result"]
    replacement_after = execute_mtls_action(
        "status", {}, state_dir=ssh.roots["target-b"], require_root=False
    )["result"]

    assert survivor_after["certificate_fingerprint"] == survivor_before["certificate_fingerprint"]
    assert survivor_after["spki_fingerprint"] == survivor_before["spki_fingerprint"]
    assert former_before["certificate_fingerprint"] not in survivor_after["peer_fingerprints"]
    assert survivor_after["peer_fingerprints"] == [replacement_after["certificate_fingerprint"]]
