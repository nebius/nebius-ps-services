"""Pinned replacement SSH trust from exact journal-bound Compute identities."""

from __future__ import annotations

from typing import Any

from .replacement import Journal
from .replacement_cloud import Cloud, message_digest
from .ssh_policy import (
    VM_HA_SSH_HOST_KEY_PATH,
    SSHHostKeyRecovery,
    publish_vm_ha_ssh_trust,
    require_vm_ha_ssh_policy,
)
from .vm_ha_identity import recover_product_host_key


def resolve(manager: Any, journal: Journal, scope: Any) -> Any:
    cloud = Cloud(manager._get_client(), journal)
    cloud.settle()
    transaction = journal.state["transaction"]
    observed = {}
    for index, member in enumerate(transaction["plan"]["members"]):
        identifier = (
            transaction["results"].get(f"{index}:prepare", {}).get("compute_id")
            or member["compute_id"]
        )
        instance = cloud.get_instance(identifier)
        address = manager._vm_public_ip_from_object(instance)
        if not address:
            raise RuntimeError("Replacement recovery cannot establish a pinned SSH transport")
        observed[member["logical_name"]] = (instance, address)

    return _resolve_observed(manager, scope, observed, cloud.get_instance, persist=True)


def preview(manager: Any, instances: tuple, scope: Any) -> Any:
    observed = {}
    sdk = manager._get_client()
    from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceServiceClient

    from .vm_ha_cloud import vm_ha_request_kwargs

    def get(identifier):
        instance = (
            InstanceServiceClient(sdk)
            .get(GetInstanceRequest(id=identifier), **vm_ha_request_kwargs())
            .wait()
        )
        if instance.metadata.id != identifier or instance.metadata.parent_id != manager.project_id:
            raise RuntimeError("Replacement host identity belongs to a different deployment")
        return instance

    for item in instances:
        instance = manager._get_vm_by_name(sdk, item.hostname)
        if instance is None:
            raise RuntimeError("Replacement preview requires every retained member")
        address = manager._vm_public_ip_from_object(instance)
        if not address:
            raise RuntimeError("Replacement source has no public management address")
        observed[item.hostname] = (instance, address)
    return _resolve_observed(manager, scope, observed, get, persist=False)


def _resolve_observed(manager: Any, scope: Any, observed: dict, get: Any, *, persist: bool) -> Any:
    def recover(names):
        result = {}
        for name in names:
            instance, _address = observed[name]
            signature = message_digest(instance.spec)

            def unchanged(identifier=instance.metadata.id, signature=signature):
                if message_digest(get(identifier).spec) != signature:
                    raise RuntimeError("Replacement SSH recovery evidence changed")

            result[name] = SSHHostKeyRecovery(
                name, recover_product_host_key(instance, path=VM_HA_SSH_HOST_KEY_PATH), unchanged
            )
        return result

    hosts = tuple((name, address) for name, (_instance, address) in observed.items())
    policy = require_vm_ha_ssh_policy(
        hosts,
        enrollment_hosts=set(observed),
        retained_hosts=set(observed),
        trust_scope=scope,
        allow_managed_repair=True,
        persist_default_host_keys=persist,
        host_identity_recovery=recover,
    )
    if not persist:
        return policy
    publish_vm_ha_ssh_trust(policy)
    # The private identity and public pin now have durable independent ownership.
    # Later authorized Compute mutations must not invalidate their initial import
    # evidence; every SSH target is still rebound by exact Compute ID in the ports.
    return require_vm_ha_ssh_policy(
        hosts,
        enrollment_hosts=set(observed),
        retained_hosts=set(observed),
        trust_scope=scope,
        allow_managed_repair=True,
        persist_default_host_keys=True,
    )
