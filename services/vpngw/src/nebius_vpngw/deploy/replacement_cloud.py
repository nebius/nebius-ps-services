"""Exact Nebius mutations for the staged replacement journal.

Resource observations never substitute for settling an accepted operation.
Requests are replayable only with the same request hash and idempotency key.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from .replacement import Journal, digest
from .vm_ha_cloud import (
    InstanceCloudState,
    clone_nebius_sdk_message,
    instance_cloud_state,
    nebius_request_error_code_is,
    vm_ha_request_kwargs,
    wait_vm_ha_operation,
)

TRANSACTION_LABEL = "nebius-vpngw-replacement"
LOGICAL_LABEL = "nebius-vpngw-member"
ROLE_LABEL = "nebius-vpngw-resource-role"


def effect_key(approval: str, index: int, label: str, scope: str | None = None) -> str:
    """A new execution may reuse approved intent, but never its request key."""
    return digest([approval, index, label] if scope is None else [approval, scope, index, label])


class CloudOperationRejected(RuntimeError):
    """A terminal failure needs a reviewed successor, never blind replay."""


def rejection_code(error: BaseException) -> str | None:
    # Transport/cancellation/deadline errors remain uncertain. A typed server
    # rejection is terminal, but does not itself prove the resource unchanged.
    for code in (
        "INVALID_ARGUMENT",
        "NOT_FOUND",
        "ALREADY_EXISTS",
        "PERMISSION_DENIED",
        "FAILED_PRECONDITION",
        "ABORTED",
        "OUT_OF_RANGE",
        "UNIMPLEMENTED",
        "UNAUTHENTICATED",
    ):
        if nebius_request_error_code_is(error, code):
            return code
    return None


def message_digest(message: Any) -> str:
    protobuf = getattr(message, "__pb2_message__", None)
    if protobuf is None:
        raise TypeError("Replacement requires generated Nebius protobuf messages")
    return hashlib.sha256(protobuf.SerializeToString(deterministic=True)).hexdigest()


def restore_compute_update(record: dict, current: Any) -> Any:
    """Reconstruct the exact approved request without journaling private bytes.

    JSON omits protobuf fields newer than the pinned SDK. Recover opaque
    metadata and specification from the same Compute identity, then restore
    their original known fields. The original binary hash must still match every byte;
    a changed or unavailable field never permits a different request.
    """
    from google.protobuf.json_format import ParseDict  # type: ignore[import-untyped]
    from nebius.api.nebius.compute.v1 import UpdateInstanceRequest

    request = UpdateInstanceRequest()
    ParseDict(record["request_metadata"], request.__pb2_message__)
    identifier = request.metadata.id
    if not identifier or resource_id(current) != identifier:
        raise RuntimeError("Compute request reconstruction requires the same resource identity")
    for name in ("metadata", "spec"):
        restored = clone_nebius_sdk_message(getattr(current, name))
        for field, _ in restored.__pb2_message__.ListFields():
            restored.__pb2_message__.ClearField(field.name)
        restored.__pb2_message__.MergeFrom(getattr(request, name).__pb2_message__)
        setattr(request, name, restored)
    if record.get("user_data_source"):
        if record["user_data_source"] != identifier:
            raise RuntimeError("Compute request reconstruction has a different private-data source")
        request.spec.cloud_init_user_data = current.spec.cloud_init_user_data
    if message_digest(request) != record["request_sha256"]:
        raise RuntimeError("Compute request reconstruction changed; retain the checkpoint")
    return request


def resource_id(resource: Any) -> str:
    return str(getattr(getattr(resource, "metadata", None), "id", "") or "")


def source_spec_digest(instance: Any) -> str:
    """Bind every source spec field except the approved public-address pin.

    Actual public allocation identities are separately bound to authoritative
    NIC status and allocation assignment. Auto-assigned addresses can therefore
    become static before detachment without hiding any other spec change.
    """
    spec = clone_nebius_sdk_message(instance.spec)
    interfaces = list(spec.network_interfaces)
    for nic in interfaces:
        nic.public_ip_address = None
    spec.network_interfaces = interfaces
    return message_digest(spec)


def single_nic_status(instance: Any) -> Any:
    specification, observed = (
        list(instance.spec.network_interfaces),
        list(instance.status.network_interfaces),
    )
    if len(specification) != 1 or len(observed) != 1 or specification[0].name != observed[0].name:
        raise RuntimeError("Replacement requires one exact authoritative NIC status")
    if not observed[0].ip_address.allocation_id:
        raise RuntimeError("Replacement NIC has no authoritative primary allocation")
    return observed[0]


class Cloud:
    def __init__(
        self,
        sdk: Any,
        journal: Journal,
        *,
        member_index: int | None = None,
        operation_scope: str | None = None,
        restore_request: Callable[[dict], Any] | None = None,
        admit_request: Callable[[Any], None] | None = None,
    ) -> None:
        self.sdk, self.journal = sdk, journal
        self.operation_scope = operation_scope
        self.restore_request = restore_request
        self.admit_request = admit_request
        self.member_index = (
            journal.state["transaction"]["member"] if member_index is None else member_index
        )

    @property
    def records(self) -> dict[str, Any]:
        return self.journal.state["transaction"]["operations"]

    def _finish(self, record: dict, operation: Any) -> str:
        if str(getattr(operation, "id", "") or "") != record["cloud_operation_id"]:
            raise RuntimeError("Replacement operation identity changed")
        wait_vm_ha_operation(operation)
        successful = getattr(operation, "successful", None)
        if not callable(successful) or not successful():
            done = getattr(operation, "done", None)
            status = getattr(operation, "status", None)
            code = getattr(
                getattr(status() if callable(status) else None, "code", None), "name", None
            )
            if callable(done) and done() and isinstance(code, str) and code != "OK":
                record.update(status="failed", failure={"kind": "operation", "code": code})
                self.journal.save()
                raise CloudOperationRejected(
                    "Cloud operation failed; independently verify effects and review a successor"
                )
            raise RuntimeError("Replacement cloud operation is not confirmed successful")
        identifier = resource_id(
            getattr(getattr(operation, "result", None), "resource", None)
        ) or str(getattr(operation, "resource_id", "") or "")
        record["status"] = "settled"
        record["resource_id"] = identifier
        self.journal.save()
        return identifier

    def _submit(self, record: dict, key: str, request: Any, submit: Callable[..., Any]) -> Any:
        if self.admit_request is not None:
            self.admit_request(request)
        # Persist submission provenance before transport. A later rejection of
        # an uncertain replay cannot prove the original request had no effects.
        record["submission_count"] = record.get("submission_count", 1) + 1
        record["single_attempt"] = record.get("single_attempt") is True
        self.journal.save()
        request_options = vm_ha_request_kwargs(key)
        request_options["retries"] = 0
        try:
            operation = submit(request, **request_options).wait()
        except Exception as error:
            code = rejection_code(error)
            if code is not None:
                record.update(status="rejected", failure={"kind": "request", "code": code})
                self.journal.save()
                raise CloudOperationRejected(
                    "Cloud request was rejected; independently verify effects and review a successor"
                ) from None
            raise
        identifier = str(getattr(operation, "id", "") or "")
        if not identifier:
            raise RuntimeError("Replacement mutation returned no operation receipt")
        record.update(status="accepted", cloud_operation_id=identifier)
        self.journal.save()
        return operation

    def settle(self, *, require_success: bool = True) -> None:
        import nebius.api.nebius.compute.v1 as compute
        import nebius.api.nebius.iam.v1 as iam
        import nebius.api.nebius.vpc.v1 as vpc
        from google.protobuf.json_format import ParseDict  # type: ignore[import-untyped]
        from nebius.api.nebius.common.v1 import GetOperationRequest, OperationServiceClient

        replayable = {
            "StopInstanceRequest": (
                compute.StopInstanceRequest,
                compute.InstanceServiceClient,
                "stop",
            ),
            "UpdateInstanceRequest": (
                compute.UpdateInstanceRequest,
                compute.InstanceServiceClient,
                "update",
            ),
            "DeleteInstanceRequest": (
                compute.DeleteInstanceRequest,
                compute.InstanceServiceClient,
                "delete",
            ),
            "CreateDiskRequest": (compute.CreateDiskRequest, compute.DiskServiceClient, "create"),
            "DeleteDiskRequest": (compute.DeleteDiskRequest, compute.DiskServiceClient, "delete"),
            "CreateAllocationRequest": (
                vpc.CreateAllocationRequest,
                vpc.AllocationServiceClient,
                "create",
            ),
            "DeleteAllocationRequest": (
                vpc.DeleteAllocationRequest,
                vpc.AllocationServiceClient,
                "delete",
            ),
            "UpdateRouteRequest": (vpc.UpdateRouteRequest, vpc.RouteServiceClient, "update"),
            "CreateRouteRequest": (vpc.CreateRouteRequest, vpc.RouteServiceClient, "create"),
            "DeleteRouteRequest": (vpc.DeleteRouteRequest, vpc.RouteServiceClient, "delete"),
            "CreateRouteTableRequest": (
                vpc.CreateRouteTableRequest,
                vpc.RouteTableServiceClient,
                "create",
            ),
            "UpdateSubnetRequest": (vpc.UpdateSubnetRequest, vpc.SubnetServiceClient, "update"),
            "CreateSubnetRequest": (vpc.CreateSubnetRequest, vpc.SubnetServiceClient, "create"),
            "UpdatePoolRequest": (vpc.UpdatePoolRequest, vpc.PoolServiceClient, "update"),
            "CreateServiceAccountRequest": (
                iam.CreateServiceAccountRequest,
                iam.ServiceAccountServiceClient,
                "create",
            ),
            "CreateGroupRequest": (iam.CreateGroupRequest, iam.GroupServiceClient, "create"),
            "CreateGroupMembershipRequest": (
                iam.CreateGroupMembershipRequest,
                iam.GroupMembershipServiceClient,
                "create",
            ),
            "CreateAccessPermitRequest": (
                iam.CreateAccessPermitRequest,
                iam.AccessPermitServiceClient,
                "create",
            ),
            "CreateAuthPublicKeyRequest": (
                iam.CreateAuthPublicKeyRequest,
                iam.AuthPublicKeyServiceClient,
                "create",
            ),
        }
        if self.restore_request is not None:
            # Only an owner that reconstructs and revalidates the approved
            # bootstrap can replay Compute creation. Never persist user-data.
            replayable["CreateInstanceRequest"] = (
                compute.CreateInstanceRequest,
                compute.InstanceServiceClient,
                "create",
            )
        for key, record in self.records.items():
            if record["status"] == "intent" and (
                self.restore_request is not None or record.get("request_type") in replayable
            ):
                if self.restore_request is not None:
                    request = self.restore_request(record)
                    _, service_type, method = replayable[type(request).__name__]
                else:
                    request_type, service_type, method = replayable[record["request_type"]]
                    request = request_type()
                    ParseDict(record["request_metadata"], request.__pb2_message__)
                    if record["request_type"] == "UpdateInstanceRequest":
                        request = restore_compute_update(
                            record, self.get_instance(request.metadata.id)
                        )
                if message_digest(request) != record["request_sha256"]:
                    raise RuntimeError(
                        "Replacement request reconstruction changed; retain the checkpoint"
                    )
                operation = self._submit(
                    record, key, request, getattr(service_type(self.sdk), method)
                )
                self._finish(record, operation)
            elif record["status"] == "accepted":
                operation = (
                    OperationServiceClient(self.sdk)
                    .get(
                        GetOperationRequest(id=record["cloud_operation_id"]),
                        **vm_ha_request_kwargs(),
                    )
                    .wait()
                )
                self._finish(record, operation)
        if require_success and any(
            record["status"] in {"rejected", "failed"} for record in self.records.values()
        ):
            raise CloudOperationRejected(
                "Cloud effect failed; retain its receipt and review a successor operation"
            )

    def mutate(self, label: str, request: Any, submit: Callable[..., Any]) -> str:
        transaction = self.journal.state["transaction"]
        key = effect_key(transaction["approval"], self.member_index, label, self.operation_scope)
        signature = message_digest(request)
        self.settle()
        record = self.records.get(key)
        if record is not None:
            if record["label"] != label:
                raise RuntimeError("Replacement effect identity changed")
            if record["request_sha256"] != signature:
                raise RuntimeError(
                    "Replacement request changed; preserve resources and recover its original intent"
                )
            if record["status"] == "settled":
                return record["resource_id"]
        else:
            if any(item["status"] != "settled" for item in self.records.values()):
                raise RuntimeError("Another replacement cloud effect is unresolved")
            record = {
                "label": label,
                "request_sha256": signature,
                "status": "intent",
                "cloud_operation_id": None,
                "resource_id": None,
                "submission_count": 0,
                "single_attempt": True,
            }
            # Creation user-data contains the pinned host private key. It is
            # reconstructed by the guarded prepare path, never stored here.
            if type(request).__name__ != "CreateInstanceRequest":
                from google.protobuf.json_format import (
                    MessageToDict,  # type: ignore[import-untyped]
                )

                metadata = MessageToDict(request.__pb2_message__, preserving_proto_field_name=True)
                specification = metadata.get("spec", {})
                if type(request).__name__ == "CreateAuthPublicKeyRequest":
                    public_key = specification.pop("data")
                    record["public_key_sha256"] = hashlib.sha256(public_key.encode()).hexdigest()
                user_data = specification.pop("cloud_init_user_data", None)
                if user_data:
                    record["user_data_source"] = request.metadata.id
                record.update(request_type=type(request).__name__, request_metadata=metadata)
            self.records[key] = record
            self.journal.save()
        operation = self._submit(record, key, request, submit)
        return self._finish(record, operation)

    def completed(self, label: str) -> str | None:
        self.settle()
        transaction = self.journal.state["transaction"]
        key = effect_key(transaction["approval"], self.member_index, label, self.operation_scope)
        record = self.records.get(key)
        return record["resource_id"] if record and record["status"] == "settled" else None

    def get_instance(self, identifier: str, *, absent_ok: bool = False) -> Any:
        from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceServiceClient

        try:
            instance = (
                InstanceServiceClient(self.sdk)
                .get(GetInstanceRequest(id=identifier), **vm_ha_request_kwargs())
                .wait()
            )
        except Exception as error:
            if absent_ok and nebius_request_error_code_is(error, "NOT_FOUND"):
                return None
            raise
        if (
            resource_id(instance) != identifier
            or instance.metadata.parent_id != self.journal.project
        ):
            raise RuntimeError("Replacement Compute identity is foreign")
        return instance

    def require_stopped(self, identifier: str) -> Any:
        instance = self.get_instance(identifier)
        if instance_cloud_state(instance) is not InstanceCloudState.STOPPED:
            raise RuntimeError("Former Compute must be authoritatively Stopped")
        return instance

    def stop(self, identifier: str, label: str) -> None:
        from nebius.api.nebius.compute.v1 import InstanceServiceClient, StopInstanceRequest

        self.mutate(label, StopInstanceRequest(id=identifier), InstanceServiceClient(self.sdk).stop)
        self.require_stopped(identifier)

    def update(self, instance: Any, label: str, edit: Callable[[Any, Any], None]) -> None:
        from nebius.api.nebius.compute.v1 import InstanceServiceClient, UpdateInstanceRequest

        metadata = clone_nebius_sdk_message(instance.metadata)
        spec = clone_nebius_sdk_message(instance.spec)
        if metadata.resource_version <= 0:
            raise RuntimeError("Replacement mutation requires a positive Compute revision")
        edit(metadata, spec)
        self.mutate(
            label,
            UpdateInstanceRequest(metadata=metadata, spec=spec),
            InstanceServiceClient(self.sdk).update,
        )

    def detach_public(self, identifier: str, allocation_ids: tuple[str, ...], label: str) -> None:
        instance = self.require_stopped(identifier)
        if self.completed(label) is not None:
            if any(
                nic.public_ip_address.allocation_id in allocation_ids
                for nic in instance.spec.network_interfaces
            ):
                raise RuntimeError("Settled public allocation detachment no longer holds")
            return

        def edit(metadata: Any, spec: Any) -> None:
            interfaces = list(spec.network_interfaces)
            observed = tuple(str(nic.public_ip_address.allocation_id) for nic in interfaces)
            if observed != allocation_ids:
                raise RuntimeError("Public allocation attachment changed before cutover")
            for nic in interfaces:
                nic.public_ip_address = None
            spec.network_interfaces = interfaces

        self.update(instance, label, edit)
        observed = self.require_stopped(identifier)
        if any(
            nic.public_ip_address.allocation_id in allocation_ids
            for nic in observed.spec.network_interfaces
        ):
            raise RuntimeError("Former public allocation attachment did not detach")

    def pin_public(self, identifier: str, allocation_ids: tuple[str, ...]) -> None:
        from nebius.api.nebius.compute.v1 import PublicIPAddress
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient, GetAllocationRequest

        self.settle()
        instance = self.get_instance(identifier)
        observed = single_nic_status(instance)
        if (observed.public_ip_address.allocation_id,) != allocation_ids:
            raise RuntimeError("Source public allocation changed before retention")
        if self.completed("pin-source-public") is None:

            def edit(metadata: Any, spec: Any) -> None:
                interfaces = list(spec.network_interfaces)
                interfaces[0].public_ip_address = PublicIPAddress(
                    allocation_id=allocation_ids[0], static=True
                )
                spec.network_interfaces = interfaces

            self.update(instance, "pin-source-public", edit)
        allocation = (
            AllocationServiceClient(self.sdk)
            .get(GetAllocationRequest(id=allocation_ids[0]), **vm_ha_request_kwargs())
            .wait()
        )
        assignment = allocation.status.assignment.network_interface
        if (
            resource_id(allocation) != allocation_ids[0]
            or not allocation.status.static
            or getattr(allocation.status.state, "name", "") != "ASSIGNED"
            or assignment.instance_id != identifier
            or assignment.name != observed.name
        ):
            raise RuntimeError("Source public allocation retention is unconfirmed")

    def retire(
        self,
        old_compute: str,
        old_disk: str,
        *,
        owned_disk: bool,
        candidate_verified: Callable[[], Any],
    ) -> None:
        from nebius.api.nebius.compute.v1 import (
            DeleteDiskRequest,
            DeleteInstanceRequest,
            DiskServiceClient,
            GetDiskRequest,
            InstanceServiceClient,
        )

        self.settle()
        candidate_verified()
        instance = self.get_instance(old_compute, absent_ok=True)
        if instance is not None:
            self.require_stopped(old_compute)
            if str(instance.spec.boot_disk.existing_disk.id) != old_disk:
                raise RuntimeError("Retired Compute boot disk attachment changed")
            self.mutate(
                "retire-compute",
                DeleteInstanceRequest(id=old_compute),
                InstanceServiceClient(self.sdk).delete,
            )
        if self.get_instance(old_compute, absent_ok=True) is not None:
            raise RuntimeError("Retired Compute deletion is unconfirmed")
        if not owned_disk:
            return
        candidate_verified()
        service = DiskServiceClient(self.sdk)
        try:
            disk = service.get(GetDiskRequest(id=old_disk), **vm_ha_request_kwargs()).wait()
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return
            raise
        if resource_id(disk) != old_disk or disk.metadata.parent_id != self.journal.project:
            raise RuntimeError("Retired boot disk ownership is foreign")
        member = self.journal.state["transaction"]["plan"]["members"][self.member_index]
        if disk.metadata.labels.get(LOGICAL_LABEL) != member[
            "logical_name"
        ] or disk.metadata.labels.get(ROLE_LABEL) not in {"boot", "candidate-boot"}:
            raise RuntimeError(
                "Retired boot disk ownership evidence changed; the disk was retained"
            )
        self.mutate("retire-boot-disk", DeleteDiskRequest(id=old_disk), service.delete)
        try:
            service.get(GetDiskRequest(id=old_disk), **vm_ha_request_kwargs()).wait()
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return
            raise
        raise RuntimeError("Retired boot disk deletion is unconfirmed")

    def create_disk(
        self, *, name: str, image_id: str, desired: dict[str, Any], logical: str
    ) -> str:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.compute.v1 import CreateDiskRequest, DiskServiceClient, DiskSpec

        request = CreateDiskRequest(
            metadata=ResourceMetadata(
                name=name,
                parent_id=self.journal.project,
                labels=self.labels(logical, "candidate-boot"),
            ),
            spec=DiskSpec(
                source_image_id=image_id,
                size_gibibytes=desired["disk_gb"],
                type=desired["disk_type"],
                block_size_bytes=desired["disk_block_bytes"],
            ),
        )
        identifier = self.mutate(
            "create-candidate-boot", request, DiskServiceClient(self.sdk).create
        )
        if not identifier:
            raise RuntimeError("Candidate disk creation returned no authoritative identity")
        return identifier

    def labels(self, logical: str, role: str) -> dict[str, str]:
        return {
            TRANSACTION_LABEL: self.journal.state["transaction"]["plan"]["operation_id"],
            LOGICAL_LABEL: logical,
            ROLE_LABEL: role,
        }

    def create_management_allocation(self, *, name: str, subnet: str, logical: str) -> str:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.vpc.v1 import (
            AllocationServiceClient,
            AllocationSpec,
            CreateAllocationRequest,
            IPv4PublicAllocationSpec,
        )

        request = CreateAllocationRequest(
            metadata=ResourceMetadata(
                name=name,
                parent_id=self.journal.project,
                labels=self.labels(logical, "temporary-management"),
            ),
            spec=AllocationSpec(ipv4_public=IPv4PublicAllocationSpec(subnet_id=subnet)),
        )
        identifier = self.mutate(
            "create-management-allocation", request, AllocationServiceClient(self.sdk).create
        )
        if not identifier:
            raise RuntimeError("Temporary management allocation returned no identity")
        return identifier

    def create_candidate(
        self,
        *,
        name: str,
        logical: str,
        disk_id: str,
        public_allocation: str,
        desired: dict[str, Any],
        security_groups: list[dict[str, Any]],
        cloud_init: str,
    ) -> str:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.compute.v1 import (
            AttachedDiskSpec,
            CreateInstanceRequest,
            ExistingDisk,
            InstanceServiceClient,
            InstanceSpec,
            IPAddress,
            NetworkInterfaceSpec,
            PublicIPAddress,
            ResourcesSpec,
            SecurityGroup,
        )

        from .vm_ha_identity import require_cloud_init_size

        require_cloud_init_size(cloud_init)
        if len(security_groups) != 1:
            raise RuntimeError("Candidate creation requires the supported single NIC topology")
        source = security_groups[0]
        request = CreateInstanceRequest(
            metadata=ResourceMetadata(
                name=name, parent_id=self.journal.project, labels=self.labels(logical, "candidate")
            ),
            spec=InstanceSpec(
                resources=ResourcesSpec(platform=desired["platform"], preset=desired["preset"]),
                boot_disk=AttachedDiskSpec(
                    attach_mode="READ_WRITE",
                    device_id="boot",
                    existing_disk=ExistingDisk(id=disk_id),
                ),
                network_interfaces=[
                    NetworkInterfaceSpec(
                        name=source["name"],
                        subnet_id=source["subnet_id"],
                        ip_address=IPAddress(),
                        public_ip_address=PublicIPAddress(
                            allocation_id=public_allocation, static=True
                        ),
                        security_groups=[
                            SecurityGroup(id=identifier) for identifier in source["explicit_ids"]
                        ],
                    )
                ],
                cloud_init_user_data=cloud_init,
            ),
        )
        identifier = self.mutate(
            "create-candidate", request, InstanceServiceClient(self.sdk).create
        )
        if not identifier:
            raise RuntimeError("Candidate creation returned no Compute identity")
        candidate = self.get_instance(identifier)
        if (
            dict(candidate.metadata.labels) != self.labels(logical, "candidate")
            or candidate.spec.boot_disk.existing_disk.id != disk_id
        ):
            raise RuntimeError(
                "Candidate ownership or boot attachment differs from its creation receipt"
            )
        from .security_groups import NICSecurityGroups, verify_candidate

        verify_candidate(
            self.sdk,
            candidate,
            tuple(
                NICSecurityGroups(
                    name=item["name"],
                    subnet_id=item["subnet_id"],
                    network_id=item["network_id"],
                    explicit_ids=tuple(item["explicit_ids"]),
                    effective_ids=tuple(item["effective_ids"]),
                )
                for item in security_groups
            ),
        )
        return identifier

    def attach_public(
        self, identifier: str, allocation_ids: tuple[str, ...], *, label: str
    ) -> None:
        from nebius.api.nebius.compute.v1 import PublicIPAddress

        instance = self.get_instance(identifier)
        if self.completed(label) is None:

            def edit(metadata: Any, spec: Any) -> None:
                interfaces = list(spec.network_interfaces)
                if len(interfaces) != len(allocation_ids):
                    raise RuntimeError("Candidate NIC set changed before allocation transfer")
                for nic, allocation in zip(interfaces, allocation_ids, strict=True):
                    nic.public_ip_address = PublicIPAddress(allocation_id=allocation, static=True)
                spec.network_interfaces = interfaces

            self.update(instance, label, edit)
        candidate = self.get_instance(identifier)
        if (
            tuple(nic.public_ip_address.allocation_id for nic in candidate.spec.network_interfaces)
            != allocation_ids
        ):
            raise RuntimeError("Candidate public allocation transfer did not verify")

    def rename(self, identifier: str, name: str, *, logical: str, role: str, label: str) -> None:
        instance = self.get_instance(identifier)
        if self.completed(label) is None:

            def edit(metadata: Any, spec: Any) -> None:
                metadata.name = name
                metadata.labels = {**dict(metadata.labels), **self.labels(logical, role)}

            self.update(instance, label, edit)
        observed = self.get_instance(identifier)
        if observed.metadata.name != name or any(
            observed.metadata.labels.get(key) != value
            for key, value in self.labels(logical, role).items()
        ):
            raise RuntimeError("Replacement logical member publication did not verify")
