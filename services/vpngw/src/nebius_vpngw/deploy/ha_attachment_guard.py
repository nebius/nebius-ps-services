"""Exact HA inhibition and ownership proof for retiring attachment work."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from ..apply_approval import digest
from ..schema import VMHARuntimeBinding
from . import security_group_apply as attachments
from .recovery import ReplanRejected
from .ssh_push import SSHPush, VMHAApplyLockReceipt
from .vm_ha_cloud import allocation_observation


def native_receipts(ssh, target, instance, local, binding) -> dict:
    """Read exact release/continuity identities without publishing guest state."""
    identity = {
        "cluster_id": binding.cluster_id,
        "node_id": instance.vm_ha_node.node_id,
        "generation_id": binding.generation_id,
    }
    script = f"""
import json
from pathlib import Path
from nebius_vpngw.agent.vm_ha.inhibition import (
    standby_replacement_inhibition_operation_id,
    standby_replacement_release_operation_id,
)
from nebius_vpngw.agent.vm_ha.attachment_continuity import _load, FILENAME
identity=json.loads({json.dumps(identity, sort_keys=True)!r})
root=Path('/var/lib/nebius-vpngw/vm-ha')
print(json.dumps({{
    'inhibition':standby_replacement_inhibition_operation_id(root, **identity),
    'release':standby_replacement_release_operation_id(root, **identity),
    'continuity':_load(root / FILENAME),
}}))
"""
    result = attachments._guest_observation(ssh, target, instance, local, script)
    if set(result) != {"inhibition", "release", "continuity"}:
        raise ReplanRejected("HA attachment native receipts are incomplete")
    return result


@dataclass
class Guard:
    manager: Any
    ssh: Any
    instances: tuple
    local: dict
    targets: dict
    transaction: dict

    def authority(self):
        from ..agent.vm_ha.inhibition import STANDBY_REPLACEMENT_INHIBITION_SCHEMA

        transaction = self.transaction
        configs = {item.hostname: item for item in self.instances}
        guests = transaction.get("guests", {})
        if set(configs) != set(guests) or len(configs) != 2:
            raise ReplanRejected("HA attachment guest inventory changed")
        bindings = [VMHARuntimeBinding.model_validate(g["binding"]) for g in guests.values()]
        binding = bindings[0]
        nodes = {node.compute_id: node for node in binding.nodes}
        if (
            any(item != binding for item in bindings)
            or self.manager.inspect_ordinary_targets(tuple(configs)) != self.targets
            or set(nodes) != {compute for compute, _ in self.targets.values()}
            or any(
                config.vm_ha_node is None
                or config.vm_ha_node.node_id != nodes[self.targets[name][0]].node_id
                or config.vm_ha_generation.generation_id != binding.generation_id
                for name, config in configs.items()
            )
        ):
            raise ReplanRejected("HA attachment settlement requires the retained generation")
        owner = allocation_observation(
            binding.shared_allocation_id,
            self.manager.get_ha_allocation(binding.shared_allocation_id),
        ).owner
        if (
            owner is None
            or asdict(owner) != transaction.get("owner")
            or owner.instance_id not in nodes
            or owner.network_interface_name != nodes[owner.instance_id].network_interface_name
        ):
            raise ReplanRejected("HA attachment shared allocation owner changed")
        name = next(name for name, target in self.targets.items() if target[0] == owner.instance_id)
        operation = digest(
            [
                "security-group-apply",
                transaction["operation_id"],
                transaction["approval"],
                transaction["plan"],
            ]
        )
        expected_gate = {
            "schema": STANDBY_REPLACEMENT_INHIBITION_SCHEMA,
            "cluster_id": binding.cluster_id,
            "node_id": configs[name].vm_ha_node.node_id,
            "generation_id": binding.generation_id,
            "operation_id": operation,
        }
        expected_locks = {
            member: asdict(
                SSHPush._vm_ha_apply_lock_payload(
                    cluster_id=binding.cluster_id,
                    node_id=config.vm_ha_node.node_id,
                    generation_id=binding.generation_id,
                    operation_id=operation,
                )[1]
            )
            for member, config in configs.items()
            if member != name
        }
        if (
            transaction.get("owner_inhibition") != expected_gate
            or transaction["locks"] != expected_locks
        ):
            raise ReplanRejected("HA attachment lacks its original complete inhibition authority")
        return binding, configs, name, operation

    def observe(self, *, releasing: bool) -> None:
        binding, configs, owner, operation = self.authority()
        receipts = {}
        for name, config in configs.items():
            target = self.targets[name][1]
            if (
                attachments.guest_identity(self.ssh, target, config, self.local)
                != self.transaction["guests"][name]
            ):
                raise ReplanRejected("HA attachment guest identity changed")
            receipts[name] = native_receipts(self.ssh, target, config, self.local, binding)
        gate = receipts[owner]
        released = gate["inhibition"] is None and gate["release"] == operation
        if gate["inhibition"] == operation:
            self.ssh.verify_vm_ha_standby_replacement_quiescent(
                self.targets[owner][1],
                owner,
                self.local,
                inhibition=self.transaction["owner_inhibition"],
            )
        elif not (releasing and released):
            raise ReplanRejected("HA attachment owner gate disappeared without its release receipt")
        continuity = self.transaction.get("owner_continuity")
        if continuity is not None:
            observed = [receipt["continuity"] for receipt in receipts.values()]
            if (
                any(
                    value is None
                    or {key: v for key, v in value.items() if key != "ownership_epoch"}
                    != continuity
                    for value in observed
                )
                or observed[0] != observed[1]
            ):
                raise ReplanRejected("HA attachment guests lost exact ownership continuity")
        for name, config in configs.items():
            if name == owner:
                continue
            present = attachments.guest_lock_present(
                self.ssh,
                self.targets[name][1],
                config,
                self.local,
                self.transaction["locks"][name],
            )
            if present:
                self.ssh.verify_vm_ha_apply_inhibition_quiescent(
                    self.targets[name][1],
                    name,
                    self.local,
                    node_id=config.vm_ha_node.node_id,
                    operation_id=operation,
                )
            elif not (releasing and released):
                raise ReplanRejected("HA attachment passive gate disappeared before owner release")

    def release(self, admit) -> None:
        self.observe(releasing=True)
        _binding, configs, owner, _operation = self.authority()
        admit()
        self.ssh.release_vm_ha_standby_replacement_inhibition(
            self.targets[owner][1],
            owner,
            self.local,
            inhibition=self.transaction["owner_inhibition"],
        )
        for name, config in configs.items():
            if name != owner and attachments.guest_lock_present(
                self.ssh,
                self.targets[name][1],
                config,
                self.local,
                self.transaction["locks"][name],
            ):
                admit()
                self.ssh.clear_vm_ha_apply_lock(
                    self.targets[name][1],
                    config,
                    self.local,
                    receipt=VMHAApplyLockReceipt(**self.transaction["locks"][name]),
                )
        self.observe(releasing=True)
