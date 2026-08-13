"""Inert fail-closed SDK/cloud facade for the future VM-HA controller."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nebius_vpngw.agent.vm_ha_controller import (
    CloudObservation,
    ComputeState,
)
from nebius_vpngw.deploy.vm_ha_cloud import (
    AllocationOwner,
    ClusterCloudObservation,
    InstanceCloudState,
    NebiusSDKCloudClient,
    VMHACloudAdapter,
)
from nebius_vpngw.schema import VMHARuntimeBinding, VMHARuntimeNodeBinding


def _default_sdk_factory(*, credentials_file_name: str) -> Any:
    from nebius.sdk import SDK

    return SDK(credentials_file_name=credentials_file_name)


def _credential_file(path: str, name: str) -> Path:
    candidate = Path(path)
    try:
        stat = candidate.lstat()
        if candidate.is_symlink() or not candidate.is_file() or stat.st_mode & 0o077:
            raise ValueError
        with candidate.open("rb"):
            pass
    except (OSError, ValueError):
        raise ValueError(f"installed VM-HA {name} is not a private readable regular file") from None
    return candidate


def _compute_state(value: InstanceCloudState) -> ComputeState:
    return ComputeState(value.value)


class RenewableNebiusSDK:
    """Own exactly one renewable SDK and close its background resources."""

    def __init__(
        self,
        credentials_file_name: str,
        *,
        factory: Callable[..., Any] = _default_sdk_factory,
    ) -> None:
        credentials = _credential_file(credentials_file_name, "Nebius credentials")
        self.client = factory(credentials_file_name=str(credentials))
        if self.client is None or not callable(getattr(self.client, "sync_close", None)):
            raise RuntimeError("Nebius SDK does not provide the required synchronous lifecycle")
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.client.sync_close()


class BoundCloudRuntime:
    """Map the strict SDK adapter to controller observations and individual effects."""

    def __init__(
        self,
        binding: VMHARuntimeBinding,
        local: VMHARuntimeNodeBinding,
        peer: VMHARuntimeNodeBinding,
        adapter: VMHACloudAdapter,
    ) -> None:
        self.binding = binding
        self.local = local
        self.peer = peer
        self.adapter = adapter

    @property
    def allocation_id(self) -> str:
        return self.binding.shared_allocation_id

    @staticmethod
    def _owner(node: VMHARuntimeNodeBinding) -> AllocationOwner:
        return AllocationOwner(node.compute_id, node.network_interface_name)

    def _cluster(self) -> ClusterCloudObservation:
        return self.adapter.observe_cluster(
            allocation_id=self.allocation_id,
            former_owner=self._owner(self.peer),
            candidate=self._owner(self.local),
        )

    def observe(self) -> CloudObservation:
        observed = self._cluster()
        owner = observed.allocation.owner
        owner_id = None
        for node in self.binding.nodes:
            if owner == self._owner(node):
                owner_id = node.node_id
        if owner is not None and owner_id is None:
            raise RuntimeError("shared allocation has an unexpected Compute owner")
        epoch = ""
        if owner == self._owner(self.local):
            epoch = observed.candidate.resource_version
        elif owner == self._owner(self.peer):
            epoch = observed.former.resource_version
        else:
            epoch = observed.former.resource_version
        exact = observed.candidate_attachment_exact
        return CloudObservation(
            authoritative=True,
            allocation_id=self.allocation_id,
            observed_owner_node_id=owner_id,
            former_owner_node_id=self.peer.node_id,
            former_owner_compute_state=_compute_state(observed.former.state),
            former_attachment_absent=observed.former_attachment_absent,
            candidate_attachment_exact=exact,
            ownership_re_read_exact=exact,
            ownership_epoch=epoch,
        )

    def stop_former(self) -> None:
        self.adapter.require_stopped(self.peer.compute_id)

    def detach_former(self) -> None:
        if (
            self.adapter.require_stopped(self.peer.compute_id).state
            is not InstanceCloudState.STOPPED
        ):
            raise RuntimeError("former owner is not STOPPED")
        self.adapter.require_former_attachment_absent(
            self.allocation_id, self._owner(self.peer), self._owner(self.local)
        )
        self.adapter.require_compute_attachment(
            self.allocation_id, self._owner(self.peer), present=False
        )

    def attach_candidate(self) -> None:
        self.detach_former()
        self.adapter.require_candidate_attachment(self.allocation_id, self._owner(self.local))
        self.adapter.require_compute_attachment(
            self.allocation_id, self._owner(self.local), present=True
        )

    def confirm_candidate(self) -> None:
        observation = self.observe()
        if not observation.transfer_complete(self.local.node_id):
            raise RuntimeError("candidate ownership is not exact after former-owner fencing")


def build_cloud_runtime(
    binding: VMHARuntimeBinding,
    local_node_id: str,
    sdk: Any,
    *,
    attempts: int = 6,
    poll_interval: float = 1.0,
) -> BoundCloudRuntime:
    nodes = {node.node_id: node for node in binding.nodes}
    if local_node_id not in nodes or len(nodes) != 2:
        raise ValueError("VM-HA runtime binding does not contain the local node")
    local = nodes[local_node_id]
    peer = next(node for node in binding.nodes if node.node_id != local_node_id)
    calls = NebiusSDKCloudClient(sdk)
    adapter = VMHACloudAdapter(
        instance_reader=calls.get_instance,
        instance_stopper=calls.stop_instance,
        allocation_reader=calls.get_allocation,
        allocation_setter=calls.set_allocation,
        attempts=attempts,
        poll_interval=poll_interval,
    )
    return BoundCloudRuntime(binding, local, peer, adapter)
