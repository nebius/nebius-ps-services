from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import shlex
import time
import typing as t
import uuid
from enum import Enum
from pathlib import Path

from rich import print

from ..config_loader import ResolvedDeploymentPlan, connection_static_remote_prefixes
from ..schema import VMHAMigrationRouteBinding, VMHARouteTarget
from .ssh_policy import SSHTrustPolicy, build_openssh_base_command
from .vm_ha_cloud import (
    operation_status_lookup_unsupported,
    vm_ha_request_kwargs,
    wait_vm_ha_operation,
)
from .vm_ha_routes import (
    AcceptedRouteOperation,
    ManagedRouteKind,
    ManagedRouteOwnership,
    PendingRouteMutation,
    RouteApplyResult,
    RouteMutation,
    RouteMutationKind,
    RouteMutationPhase,
    RouteReconciliationContext,
    RouteReconciliationPlan,
    RouteReplacementCompensated,
    RouteRollbackSnapshot,
    VerifiedAllocationOwnership,
    execute_route_plan,
    owned_route_snapshots,
    route_observation_snapshots,
)


class BGPAdvertisementState(str, Enum):
    """Exact result of a read-only Adj-RIB-Out comparison."""

    MATCH = "MATCH"
    DRIFT = "DRIFT"
    UNKNOWN = "UNKNOWN"


class RouteManagementError(RuntimeError):
    """A public route workflow could not prove its requested postcondition."""


AGENT_CAPABILITIES_SCHEMA = "nebius-vpngw.agent-capabilities.v1"
FORCE_RECONCILE_CAPABILITY = "force-reconcile-v1"
VM_HA_AUTHORITY_FORCE_RECONCILE_CAPABILITY = (
    "vm-ha-authority-bound-force-reconcile-v1"
)


class VMHAAdvertisementAuthority(t.NamedTuple):
    owner_hostname: str
    generation_id: str
    owner_node_id: str
    allocation_id: str
    ownership_epochs_by_hostname: tuple[tuple[str, str], ...]

    def ownership_epoch_for(self, hostname: str) -> str | None:
        return dict(self.ownership_epochs_by_hostname).get(hostname)


class _RouteReceiptStore(t.Protocol):
    def save_route_reconciliation_receipt(self, receipt: t.Mapping[str, object]) -> None: ...

    def load_route_reconciliation_receipt(self) -> t.Mapping[str, object] | None: ...


class _RouteMutationCheckpoint(t.Protocol):
    def load_pending_mutation(self) -> PendingRouteMutation | None: ...

    def checkpoint_pending_mutation(
        self,
        expected: PendingRouteMutation,
        *,
        phase: RouteMutationPhase,
        rollback: RouteRollbackSnapshot | None,
        accepted_operation: AcceptedRouteOperation | None,
    ) -> PendingRouteMutation: ...


class NebiusSDKRouteBackend:
    """Exact target-bound synchronous SDK adapter for on-node HA route effects."""

    _AUTHORITY_MANAGED_LABEL = "nebius-vpngw-managed"
    _AUTHORITY_CLUSTER_LABEL = "nebius-vpngw-cluster"
    _AUTHORITY_ALLOCATION_LABEL = "nebius-vpngw-allocation"
    _AUTHORITY_TARGET_LABEL = "nebius-vpngw-route-target"
    _AUTHORITY_KIND_LABEL = "nebius-vpngw-route-kind"
    _AUTHORITY_LABEL_KEYS = frozenset(
        {
            _AUTHORITY_MANAGED_LABEL,
            _AUTHORITY_CLUSTER_LABEL,
            _AUTHORITY_ALLOCATION_LABEL,
            _AUTHORITY_TARGET_LABEL,
            _AUTHORITY_KIND_LABEL,
        }
    )

    def __init__(self, sdk: t.Any) -> None:
        self.sdk = sdk
        self._reconciliation_operation_id = "unbound-route-reconciliation"
        self._mutation_checkpoint: _RouteMutationCheckpoint | None = None
        self._authority_cluster_id: str | None = None
        self._authority_allocation_id: str | None = None
        self._authority_targets: tuple[VMHARouteTarget, ...] = ()

    @staticmethod
    def _authority_fingerprint(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]

    @classmethod
    def _authority_labels(
        cls,
        *,
        cluster_id: str,
        allocation_id: str,
        route_target: VMHARouteTarget,
        route_kind: t.Any,
    ) -> dict[str, str]:
        target_payload = json.dumps(
            route_target.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        kind = str(getattr(route_kind, "value", route_kind))
        return {
            cls._AUTHORITY_MANAGED_LABEL: "vm-ha-v1",
            cls._AUTHORITY_CLUSTER_LABEL: cls._authority_fingerprint(cluster_id),
            cls._AUTHORITY_ALLOCATION_LABEL: cls._authority_fingerprint(allocation_id),
            cls._AUTHORITY_TARGET_LABEL: cls._authority_fingerprint(target_payload),
            cls._AUTHORITY_KIND_LABEL: kind,
        }

    def bind_route_authority(
        self,
        *,
        cluster_id: str,
        allocation_id: str,
        route_targets: tuple[VMHARouteTarget, ...],
    ) -> None:
        """Bind cloud-resident route authority to one installed HA generation."""

        if not cluster_id or not allocation_id or not route_targets:
            raise ValueError("VM-HA route authority binding is incomplete")
        self._authority_cluster_id = cluster_id
        self._authority_allocation_id = allocation_id
        self._authority_targets = route_targets

    @staticmethod
    def _route_labels(route: object) -> dict[str, str]:
        metadata = getattr(route, "metadata", None)
        labels = getattr(metadata, "labels", {}) or {}
        if not isinstance(labels, t.Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in labels.items()
        ):
            raise RuntimeError("VM-HA route labels are ambiguous")
        return dict(labels)

    def _cloud_authority(
        self,
        route: object,
        target: VMHARouteTarget,
    ) -> tuple[bool, ManagedRouteOwnership | None]:
        labels = self._route_labels(route)
        has_authority_labels = bool(self._AUTHORITY_LABEL_KEYS & labels.keys())
        if not has_authority_labels:
            return False, None
        cluster_id = self._authority_cluster_id
        allocation_id = self._authority_allocation_id
        if (
            cluster_id is None
            or allocation_id is None
            or target not in self._authority_targets
            or RouteManager._route_next_hop_allocation_id(route) != allocation_id
        ):
            return True, None
        kind_value = labels.get(self._AUTHORITY_KIND_LABEL)
        try:
            kind = ManagedRouteKind(str(kind_value))
        except ValueError:
            return True, None
        expected = self._authority_labels(
            cluster_id=cluster_id,
            allocation_id=allocation_id,
            route_target=target,
            route_kind=kind,
        )
        if any(labels.get(key) != value for key, value in expected.items()):
            return True, None
        return True, ManagedRouteOwnership(
            cluster_id=cluster_id,
            kind=kind,
            route_target=target,
        )

    def set_mutation_checkpoint(self, checkpoint: _RouteMutationCheckpoint) -> None:
        self._mutation_checkpoint = checkpoint

    def set_reconciliation_operation_id(self, operation_id: str) -> None:
        if not operation_id:
            raise ValueError("VM-HA route reconciliation operation ID is empty")
        self._reconciliation_operation_id = operation_id

    def _action_operation_id(self, action: str, mutation_identity: str) -> str:
        raw = hashlib.sha256(
            (f"{self._reconciliation_operation_id}\0{action}\0{mutation_identity}").encode()
        ).digest()[:16]
        return str(uuid.UUID(bytes=raw, version=4))

    def _pending(self, mutation: RouteMutation) -> PendingRouteMutation | None:
        checkpoint = self._mutation_checkpoint
        if checkpoint is None:
            return None
        pending = checkpoint.load_pending_mutation()
        if pending is None or pending.mutation != mutation:
            raise RuntimeError("VM-HA route mutation checkpoint changed")
        return pending

    def _checkpoint(
        self,
        mutation: RouteMutation,
        *,
        phase: RouteMutationPhase,
        rollback: RouteRollbackSnapshot | None,
        accepted_operation: AcceptedRouteOperation | None,
    ) -> PendingRouteMutation | None:
        checkpoint = self._mutation_checkpoint
        if checkpoint is None:
            return None
        pending = self._pending(mutation)
        assert pending is not None
        return checkpoint.checkpoint_pending_mutation(
            pending,
            phase=phase,
            rollback=rollback,
            accepted_operation=accepted_operation,
        )

    @staticmethod
    def _client_types():
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.vpc.v1 import (
            AllocationNextHop,
            CreateRouteRequest,
            DeleteRouteRequest,
            DestinationMatch,
            ListRoutesRequest,
            NextHop,
            RouteServiceClient,
            RouteSpec,
        )

        return (
            RouteServiceClient,
            ListRoutesRequest,
            CreateRouteRequest,
            DeleteRouteRequest,
            ResourceMetadata,
            RouteSpec,
            DestinationMatch,
            NextHop,
            AllocationNextHop,
        )

    def _raw_routes(self, route_table_id: str) -> tuple[object, ...]:
        client_type, list_request, *_rest = self._client_types()
        client = client_type(self.sdk)
        routes: list[object] = []
        page_token = ""
        seen_tokens: set[str] = set()
        for _page in range(1000):
            response = client.list(
                list_request(parent_id=route_table_id, page_token=page_token),
                **vm_ha_request_kwargs(),
            ).wait()
            routes.extend(tuple(getattr(response, "items", ()) or ()))
            next_token = str(getattr(response, "next_page_token", "") or "")
            if not next_token:
                return tuple(routes)
            if next_token == page_token or next_token in seen_tokens:
                raise RuntimeError("VM-HA route listing returned a cyclic page token")
            seen_tokens.add(next_token)
            page_token = next_token
        raise RuntimeError("VM-HA route listing exceeded the bounded page limit")

    def verify_target(self, target: VMHARouteTarget) -> None:
        """Freshly prove the full declared parent chain before route authority is used."""

        from nebius.api.nebius.vpc.v1 import (
            GetRouteTableRequest,
            GetSubnetRequest,
            RouteTableServiceClient,
            SubnetServiceClient,
        )

        subnet = (
            SubnetServiceClient(self.sdk)
            .get(
                GetSubnetRequest(id=target.workload_subnet_id),
                **vm_ha_request_kwargs(),
            )
            .wait()
        )
        route_table = (
            RouteTableServiceClient(self.sdk)
            .get(
                GetRouteTableRequest(id=target.route_table_id),
                **vm_ha_request_kwargs(),
            )
            .wait()
        )
        subnet_metadata = getattr(subnet, "metadata", None)
        subnet_spec = getattr(subnet, "spec", None)
        table_metadata = getattr(route_table, "metadata", None)
        table_spec = getattr(route_table, "spec", None)
        observed = VMHARouteTarget(
            project_id=str(getattr(subnet_metadata, "parent_id", "") or ""),
            network_id=str(getattr(subnet_spec, "network_id", "") or ""),
            workload_subnet_id=str(getattr(subnet_metadata, "id", "") or ""),
            route_table_id=str(getattr(table_metadata, "id", "") or ""),
        )
        if (
            observed != target
            or str(getattr(subnet_spec, "route_table_id", "") or "") != target.route_table_id
            or str(getattr(table_metadata, "parent_id", "") or "") != target.project_id
            or str(getattr(table_spec, "network_id", "") or "") != target.network_id
        ):
            raise RuntimeError("VM-HA route target membership changed")

    def verify_migration_route(self, binding: VMHAMigrationRouteBinding) -> bool:
        """Reobserve one exact approval-bound route without adopting by name."""

        self.verify_target(binding.route_target)
        matches = tuple(
            route
            for route in self._raw_routes(binding.route_target.route_table_id)
            if RouteManager._metadata_id(route) == binding.route_id
        )
        if len(matches) > 1:
            raise RuntimeError("VM-HA migration route identity is ambiguous")
        if not matches:
            return False
        route = matches[0]
        metadata = getattr(route, "metadata", None)
        prefix = RouteManager._route_destination_network(route)
        return bool(
            RouteManager._metadata_name(route) == binding.name
            and prefix is not None
            and str(prefix) == binding.prefix
            and RouteManager._route_next_hop_allocation_id(route) == binding.allocation_id
            and str(getattr(metadata, "resource_version", "") or "") == binding.resource_revision
        )

    def verify_migration_successor(
        self,
        binding: VMHAMigrationRouteBinding,
        ownership: ManagedRouteOwnership,
    ) -> bool:
        """Prove that cloud authority has superseded an old revision receipt."""

        self.verify_target(binding.route_target)
        matches = tuple(
            route
            for route in self._raw_routes(binding.route_target.route_table_id)
            if RouteManager._metadata_id(route) == binding.route_id
        )
        if len(matches) > 1:
            raise RuntimeError("VM-HA migration successor identity is ambiguous")
        if not matches:
            return False
        route = matches[0]
        prefix = RouteManager._route_destination_network(route)
        _has_authority, cloud_ownership = self._cloud_authority(
            route,
            binding.route_target,
        )
        return bool(
            RouteManager._metadata_name(route) == binding.name
            and prefix is not None
            and str(prefix) == binding.prefix
            and cloud_ownership == ownership
        )

    def list_routes(
        self,
        target: VMHARouteTarget,
        ownership: t.Mapping[str, ManagedRouteOwnership],
    ):
        routes = self._raw_routes(target.route_table_id)
        combined_ownership = dict(ownership)
        for route in routes:
            route_id = RouteManager._metadata_id(route)
            has_cloud_authority, cloud_ownership = self._cloud_authority(route, target)
            local_ownership = combined_ownership.get(route_id)
            if local_ownership is not None and has_cloud_authority:
                if cloud_ownership != local_ownership:
                    raise RuntimeError(
                        "VM-HA cloud route authority conflicts with the durable ledger"
                    )
            elif local_ownership is None and cloud_ownership is not None:
                combined_ownership[route_id] = cloud_ownership
        return RouteManager(None)._vm_ha_route_snapshots(
            routes,
            ownership_by_route_id=combined_ownership,
            route_target=target,
        )

    def _stable_route_listing(self, target: VMHARouteTarget) -> tuple[tuple[object, ...], ...]:
        """Return one exact target-reverified route-table observation."""

        self.verify_target(target)
        records: list[tuple[object, ...]] = []
        for route in self._raw_routes(target.route_table_id):
            metadata = getattr(route, "metadata", None)
            route_id = RouteManager._metadata_id(route)
            if not route_id:
                raise RuntimeError("VM-HA route listing contains an unidentified route")
            prefix = RouteManager._route_destination_network(route)
            records.append(
                (
                    route_id,
                    RouteManager._metadata_name(route),
                    "" if prefix is None else str(prefix),
                    str(RouteManager._route_next_hop_allocation_id(route) or ""),
                    str(getattr(metadata, "resource_version", "") or ""),
                    tuple(sorted(self._route_labels(route).items())),
                )
            )
        return tuple(sorted(records))

    def stably_absent_ledger_route_ids(
        self,
        ownership: t.Mapping[str, ManagedRouteOwnership],
    ) -> frozenset[str]:
        """Prove which local ledger identities no longer exist in cloud state."""

        cluster_id = self._authority_cluster_id
        if cluster_id is None or self._authority_allocation_id is None:
            raise RuntimeError("VM-HA route authority backend is not bound")
        for route_id, owner in ownership.items():
            if (
                not route_id
                or owner.cluster_id != cluster_id
                or owner.route_target not in self._authority_targets
            ):
                raise RuntimeError("VM-HA route ledger conflicts with the runtime binding")

        absent: set[str] = set()
        for target in self._authority_targets:
            ledger_ids = {
                route_id
                for route_id, owner in ownership.items()
                if owner.route_target == target
            }
            if not ledger_ids:
                continue
            first = self._stable_route_listing(target)
            second = self._stable_route_listing(target)
            if first != second:
                raise RuntimeError("VM-HA route listing changed during stable reread")
            observed_ids = {t.cast(str, record[0]) for record in second}
            absent.update(ledger_ids - observed_ids)
        return frozenset(absent)

    def synchronize_authority_labels(
        self,
        ownership: t.Mapping[str, ManagedRouteOwnership],
    ) -> None:
        """Publish exact local ledger authority for safe standby discovery."""

        cluster_id = self._authority_cluster_id
        allocation_id = self._authority_allocation_id
        if cluster_id is None or allocation_id is None:
            raise RuntimeError("VM-HA route authority backend is not bound")
        for route_id, owner in sorted(ownership.items()):
            if owner.cluster_id != cluster_id or owner.route_target not in self._authority_targets:
                raise RuntimeError("VM-HA route ledger conflicts with the runtime binding")
            self.verify_target(owner.route_target)
            matches = tuple(
                route
                for route in self._raw_routes(owner.route_target.route_table_id)
                if RouteManager._metadata_id(route) == route_id
            )
            if len(matches) != 1:
                raise RuntimeError("VM-HA ledger route identity is not exact in cloud state")
            route = matches[0]
            if RouteManager._route_next_hop_allocation_id(route) != allocation_id:
                raise RuntimeError("VM-HA ledger route no longer uses the shared allocation")
            current = self._route_labels(route)
            expected = self._authority_labels(
                cluster_id=cluster_id,
                allocation_id=allocation_id,
                route_target=owner.route_target,
                route_kind=owner.kind,
            )
            present = self._AUTHORITY_LABEL_KEYS & current.keys()
            if present and any(current.get(key) != value for key, value in expected.items()):
                raise RuntimeError("VM-HA ledger route has conflicting cloud authority labels")
            if all(current.get(key) == value for key, value in expected.items()):
                continue
            self._write_authority_labels(
                route,
                owner.route_target,
                {**current, **expected},
            )

    def _write_authority_labels(
        self,
        route: object,
        target: VMHARouteTarget,
        labels: t.Mapping[str, str],
    ) -> None:
        from nebius.api.nebius.vpc.v1 import RouteServiceClient

        before = self._rollback_snapshot(route, target)
        metadata = getattr(route, "metadata", None)
        spec = getattr(route, "spec", None)
        revision = str(getattr(metadata, "resource_version", "") or "")
        if not revision.isdecimal() or int(revision) <= 0:
            raise RuntimeError("VM-HA route label update lacks an exact resource revision")
        operation_id = self._action_operation_id(
            "publish-authority-labels",
            f"{before.route_id}:{revision}",
        )
        request = self._authority_label_update_request(
            route_id=before.route_id,
            parent_id=str(getattr(metadata, "parent_id", "") or target.route_table_id),
            name=before.name,
            resource_version=int(revision),
            labels=labels,
            description=str(getattr(spec, "description", "") or ""),
            prefix=before.prefix,
            allocation_id=before.allocation_id,
        )
        try:
            operation = (
                RouteServiceClient(self.sdk)
                .update(
                    request,
                    **vm_ha_request_kwargs(operation_id),
                )
                .wait()
            )
        except Exception as error:
            # SDK transport and service errors are not RuntimeError subclasses.
            # Keep the long-running controller behind its current-boot guard so
            # a rejected metadata migration cannot restart its systemd dependents.
            raise RuntimeError("VM-HA route authority label update request failed") from error
        try:
            wait_vm_ha_operation(operation)
        except Exception:
            pass
        else:
            if not self._operation_succeeded(operation):
                raise RuntimeError("VM-HA route authority label update failed")
        expected_labels = tuple(sorted(labels.items()))
        for attempt in range(5):
            matches = tuple(
                candidate
                for candidate in self._raw_routes(target.route_table_id)
                if RouteManager._metadata_id(candidate) == before.route_id
            )
            if len(matches) == 1:
                after = self._rollback_snapshot(matches[0], target)
                if (
                    after.name != before.name
                    or after.description != before.description
                    or after.prefix != before.prefix
                    or after.allocation_id != before.allocation_id
                ):
                    raise RuntimeError("VM-HA route changed while publishing authority labels")
                if after.labels == expected_labels:
                    return
            elif len(matches) > 1:
                raise RuntimeError("VM-HA route label update produced duplicate identities")
            if attempt < 4:
                time.sleep(0.2)
        raise RuntimeError("VM-HA route authority labels were not durably observed")

    @staticmethod
    def _authority_label_update_request(
        *,
        route_id: str,
        parent_id: str,
        name: str,
        resource_version: int,
        labels: t.Mapping[str, str],
        description: str,
        prefix: str,
        allocation_id: str,
    ) -> t.Any:
        """Build the full replacement spec required by Nebius UpdateRoute."""

        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.vpc.v1 import (
            AllocationNextHop,
            DestinationMatch,
            NextHop,
            RouteSpec,
            UpdateRouteRequest,
        )

        if not prefix or not allocation_id:
            raise RuntimeError("VM-HA route authority update lacks the exact route spec")
        return UpdateRouteRequest(
            metadata=ResourceMetadata(
                id=route_id,
                parent_id=parent_id,
                name=name,
                resource_version=resource_version,
                labels=dict(labels),
            ),
            spec=RouteSpec(
                description=description,
                destination=DestinationMatch(cidr=prefix),
                next_hop=NextHop(allocation=AllocationNextHop(id=allocation_id)),
            ),
        )

    @staticmethod
    def _name(mutation: RouteMutation) -> str:
        identity = (
            f"{mutation.cluster_id}:{mutation.route_target.route_table_id}:"
            f"{mutation.prefix}:{mutation.route_kind.value}:{mutation.allocation_id}"
        )
        return f"vpngw-ha-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"

    def _resume_operation(self, cloud_operation_id: str) -> t.Any:
        from nebius.api.nebius.common.v1 import GetOperationRequest, OperationServiceClient

        return (
            OperationServiceClient(self.sdk)
            .get(
                GetOperationRequest(id=cloud_operation_id),
                **vm_ha_request_kwargs(),
            )
            .wait()
        )

    @staticmethod
    def _operation_succeeded(operation: t.Any) -> bool:
        successful = getattr(operation, "successful", None)
        if not callable(successful):
            raise RuntimeError("VM-HA route operation has no terminal success status")
        return bool(successful())

    def _accepted_operation(
        self,
        action: str,
        mutation: RouteMutation,
        submit: t.Callable[[], t.Any],
    ) -> tuple[t.Any, AcceptedRouteOperation | None]:
        action_operation_id = self._action_operation_id(action, mutation.operation_id)
        pending = self._pending(mutation)
        accepted = None if pending is None else pending.accepted_operation
        if accepted is not None:
            if accepted.action != action or accepted.action_operation_id != action_operation_id:
                raise RuntimeError("a different accepted VM-HA route operation is pending")
            try:
                operation = self._resume_operation(accepted.cloud_operation_id)
            except Exception as error:
                if not operation_status_lookup_unsupported(error):
                    raise
                operation = submit().wait()
                replayed_operation_id = str(getattr(operation, "id", "") or "")
                if replayed_operation_id != accepted.cloud_operation_id:
                    raise RuntimeError(
                        "VM-HA idempotent route replay returned a different cloud "
                        "operation identity"
                    ) from error
            return operation, accepted
        operation = submit().wait()
        cloud_operation_id = str(getattr(operation, "id", "") or "")
        if pending is None:
            return operation, None
        if not cloud_operation_id:
            raise RuntimeError("VM-HA route mutation returned no durable operation identity")
        accepted = AcceptedRouteOperation(
            action_operation_id=action_operation_id,
            action=action,
            cloud_operation_id=cloud_operation_id,
        )
        phase = {
            "delete": RouteMutationPhase.DELETE_ACCEPTED,
            "create": RouteMutationPhase.CREATE_ACCEPTED,
            "restore": RouteMutationPhase.RESTORE_ACCEPTED,
        }[action]
        self._checkpoint(
            mutation,
            phase=phase,
            rollback=mutation.rollback,
            accepted_operation=accepted,
        )
        return operation, accepted

    def _create_operation(
        self, mutation: RouteMutation
    ) -> tuple[t.Any, AcceptedRouteOperation | None]:
        (
            client_type,
            _list_request,
            create_request,
            _delete_request,
            metadata_type,
            route_spec,
            destination_match,
            next_hop,
            allocation_next_hop,
        ) = self._client_types()
        action_operation_id = self._action_operation_id("create", mutation.operation_id)
        return self._accepted_operation(
            "create",
            mutation,
            lambda: client_type(self.sdk).create(
                create_request(
                    metadata=metadata_type(
                        parent_id=mutation.route_target.route_table_id,
                        name=self._name(mutation),
                        labels=self._authority_labels(
                            cluster_id=mutation.cluster_id,
                            allocation_id=mutation.allocation_id,
                            route_target=mutation.route_target,
                            route_kind=mutation.route_kind,
                        ),
                    ),
                    spec=route_spec(
                        destination=destination_match(cidr=mutation.prefix),
                        next_hop=next_hop(
                            allocation=allocation_next_hop(id=mutation.allocation_id)
                        ),
                    ),
                ),
                **vm_ha_request_kwargs(action_operation_id),
            ),
        )

    @staticmethod
    def _rollback_snapshot(route: object, target: VMHARouteTarget) -> RouteRollbackSnapshot:
        metadata = getattr(route, "metadata", None)
        spec = getattr(route, "spec", None)
        prefix = RouteManager._route_destination_network(route)
        allocation_id = RouteManager._route_next_hop_allocation_id(route)
        labels = getattr(metadata, "labels", {}) or {}
        if not isinstance(labels, t.Mapping):
            raise RuntimeError("VM-HA route rollback labels are ambiguous")
        try:
            return RouteRollbackSnapshot(
                route_id=RouteManager._metadata_id(route),
                resource_version=str(getattr(metadata, "resource_version", "") or ""),
                name=RouteManager._metadata_name(route),
                labels=tuple(sorted((str(key), str(value)) for key, value in labels.items())),
                description=str(getattr(spec, "description", "") or ""),
                prefix="" if prefix is None else str(prefix),
                allocation_id=str(allocation_id or ""),
                route_target=target,
            )
        except ValueError as error:
            raise RuntimeError("VM-HA route is not exactly restorable") from error

    def _restore_raw_route(
        self,
        rollback: RouteRollbackSnapshot,
        *,
        mutation: RouteMutation,
    ) -> str:
        """Compensate a failed replacement by restoring the exact removed route."""

        (
            client_type,
            _list,
            create_request,
            _delete,
            metadata_type,
            route_spec,
            destination_match,
            next_hop,
            allocation_next_hop,
        ) = self._client_types()
        action_operation_id = self._action_operation_id("restore", mutation.operation_id)
        operation, accepted = self._accepted_operation(
            "restore",
            mutation,
            lambda: client_type(self.sdk).create(
                create_request(
                    metadata=metadata_type(
                        parent_id=rollback.route_target.route_table_id,
                        name=rollback.name,
                        labels=dict(rollback.labels),
                    ),
                    spec=route_spec(
                        description=rollback.description,
                        destination=destination_match(cidr=rollback.prefix),
                        next_hop=next_hop(
                            allocation=allocation_next_hop(id=rollback.allocation_id)
                        ),
                    ),
                ),
                **vm_ha_request_kwargs(action_operation_id),
            ),
        )
        try:
            wait_vm_ha_operation(operation)
        except Exception:
            restored = self._observe_rollback_route(rollback)
            if not restored:
                raise
        else:
            if not self._operation_succeeded(operation):
                raise RuntimeError("VM-HA route rollback operation failed")
            restored = self._observe_rollback_route(rollback)
        for attempt in range(5):
            restored = restored or self._observe_rollback_route(rollback)
            if restored:
                self._checkpoint(
                    mutation,
                    phase=RouteMutationPhase.RESTORED,
                    rollback=rollback,
                    accepted_operation=None,
                )
                return restored
            if attempt < 4:
                time.sleep(0.2)
        raise RuntimeError("VM-HA route rollback postcondition was not observed")

    def _observe_rollback_route(self, rollback: RouteRollbackSnapshot) -> str | None:
        matches: list[str] = []
        conflicts = False
        for route in self._raw_routes(rollback.route_target.route_table_id):
            route_id = RouteManager._metadata_id(route)
            name = RouteManager._metadata_name(route)
            prefix = RouteManager._route_destination_network(route)
            allocation_id = RouteManager._route_next_hop_allocation_id(route)
            metadata = getattr(route, "metadata", None)
            spec = getattr(route, "spec", None)
            raw_labels = getattr(metadata, "labels", {}) or {}
            labels = (
                tuple(sorted((str(key), str(value)) for key, value in raw_labels.items()))
                if isinstance(raw_labels, t.Mapping)
                else None
            )
            description = str(getattr(spec, "description", "") or "")
            if name == rollback.name or (prefix is not None and str(prefix) == rollback.prefix):
                if (
                    name == rollback.name
                    and str(prefix) == rollback.prefix
                    and allocation_id == rollback.allocation_id
                    and labels == rollback.labels
                    and description == rollback.description
                ):
                    if route_id:
                        matches.append(route_id)
                else:
                    conflicts = True
        if len(matches) > 1 or (matches and conflicts):
            raise RuntimeError("VM-HA route rollback produced duplicate or conflicting outcomes")
        if conflicts:
            raise RuntimeError("VM-HA route rollback has a conflicting outcome")
        return matches[0] if matches else None

    def _observe_desired_route(self, mutation: RouteMutation) -> str | None:
        desired: list[str] = []
        conflicting = False
        expected_name = self._name(mutation)
        expected_labels = self._authority_labels(
            cluster_id=mutation.cluster_id,
            allocation_id=mutation.allocation_id,
            route_target=mutation.route_target,
            route_kind=mutation.route_kind,
        )
        for route in self._raw_routes(mutation.route_target.route_table_id):
            prefix = RouteManager._route_destination_network(route)
            allocation_id = RouteManager._route_next_hop_allocation_id(route)
            name = RouteManager._metadata_name(route)
            if str(prefix) == mutation.prefix:
                if allocation_id == mutation.allocation_id:
                    route_id = RouteManager._metadata_id(route)
                    labels = self._route_labels(route)
                    if (
                        route_id
                        and name == expected_name
                        and all(labels.get(key) == value for key, value in expected_labels.items())
                    ):
                        desired.append(route_id)
                    else:
                        conflicting = True
                else:
                    conflicting = True
            elif name == expected_name:
                conflicting = True
        if len(desired) > 1:
            raise RuntimeError("VM-HA route operation has duplicate desired outcomes")
        if desired and conflicting:
            raise RuntimeError("VM-HA route operation has stale or conflicting outcomes")
        if conflicting:
            raise RuntimeError("VM-HA route operation has a conflicting outcome")
        return desired[0] if desired else None

    def _delete_exact_route(self, mutation: RouteMutation) -> None:
        if not mutation.route_id:
            raise ValueError("VM-HA route deletion requires an exact route identity")
        client_type, _list, _create, delete_request, *_rest = self._client_types()

        action_operation_id = self._action_operation_id("delete", mutation.operation_id)
        operation, accepted = self._accepted_operation(
            "delete",
            mutation,
            lambda: client_type(self.sdk).delete(
                delete_request(id=mutation.route_id),
                **vm_ha_request_kwargs(action_operation_id),
            ),
        )
        try:
            wait_vm_ha_operation(operation)
        except Exception:
            if not any(
                RouteManager._metadata_id(route) == mutation.route_id
                for route in self._raw_routes(mutation.route_target.route_table_id)
            ):
                self._checkpoint(
                    mutation,
                    phase=RouteMutationPhase.ORIGINAL_ABSENT,
                    rollback=mutation.rollback,
                    accepted_operation=None,
                )
                return
            raise
        if not self._operation_succeeded(operation):
            raise RuntimeError("VM-HA route deletion operation failed")
        if any(
            RouteManager._metadata_id(route) == mutation.route_id
            for route in self._raw_routes(mutation.route_target.route_table_id)
        ):
            raise RuntimeError("VM-HA route deletion postcondition was not observed")
        self._checkpoint(
            mutation,
            phase=RouteMutationPhase.ORIGINAL_ABSENT,
            rollback=mutation.rollback,
            accepted_operation=None,
        )

    def apply_mutation(self, mutation: RouteMutation) -> str | None:
        pending = self._pending(mutation)
        if pending is not None and pending.phase is RouteMutationPhase.RESTORED:
            if mutation.rollback is None:
                raise RuntimeError("VM-HA compensated replacement lacks rollback authority")
            restored = self._observe_rollback_route(mutation.rollback)
            if not restored:
                raise RuntimeError("VM-HA compensated route replacement changed")
            raise RouteReplacementCompensated(restored)
        if pending is not None and pending.phase is RouteMutationPhase.RESTORE_ACCEPTED:
            if mutation.rollback is None:
                raise RuntimeError("VM-HA accepted rollback lacks exact restore authority")
            restored = self._restore_raw_route(mutation.rollback, mutation=mutation)
            raise RouteReplacementCompensated(restored)
        if pending is not None and pending.phase is RouteMutationPhase.DESIRED_PRESENT:
            desired = self._observe_desired_route(mutation)
            if not desired:
                raise RuntimeError("VM-HA completed route mutation changed")
            return desired

        created_route_id: str | None = None
        if mutation.kind is RouteMutationKind.CREATE:
            created_route_id = self.recover_created_route(mutation)
        elif mutation.kind is RouteMutationKind.REPLACE:
            current_routes = self._raw_routes(mutation.route_target.route_table_id)
            original = [
                route
                for route in current_routes
                if RouteManager._metadata_id(route) == mutation.route_id
            ]
            if len(original) > 1:
                raise RuntimeError("VM-HA route deletion resolved to duplicate identities")
            if original:
                observed_rollback = self._rollback_snapshot(original[0], mutation.route_target)
                if mutation.rollback is not None and mutation.rollback != observed_rollback:
                    raise RuntimeError("VM-HA route changed after rollback snapshot was persisted")
                if mutation.rollback is None:
                    pending = self._checkpoint(
                        mutation,
                        phase=RouteMutationPhase.INTENT,
                        rollback=observed_rollback,
                        accepted_operation=None,
                    )
                    mutation = RouteMutation(
                        kind=mutation.kind,
                        prefix=mutation.prefix,
                        route_kind=mutation.route_kind,
                        allocation_id=mutation.allocation_id,
                        cluster_id=mutation.cluster_id,
                        route_target=mutation.route_target,
                        route_id=mutation.route_id,
                        rollback=observed_rollback,
                    )
            else:
                created_route_id = self.recover_created_route(mutation)
                if not created_route_id and mutation.rollback is None:
                    raise RuntimeError(
                        "legacy VM-HA replacement lost both original and desired route"
                    )
                if pending is not None and pending.phase is RouteMutationPhase.DELETE_ACCEPTED:
                    pending = self._checkpoint(
                        mutation,
                        phase=RouteMutationPhase.ORIGINAL_ABSENT,
                        rollback=mutation.rollback,
                        accepted_operation=None,
                    )
        if mutation.kind in {RouteMutationKind.DELETE, RouteMutationKind.REPLACE}:
            if not mutation.route_id:
                raise ValueError("VM-HA route deletion requires an exact route identity")
            observed = [
                route
                for route in self._raw_routes(mutation.route_target.route_table_id)
                if RouteManager._metadata_id(route) == mutation.route_id
            ]
            if len(observed) > 1:
                raise RuntimeError("VM-HA route deletion resolved to duplicate identities")
            if observed:
                current_prefix = RouteManager._route_destination_network(observed[0])
                if current_prefix is None or str(current_prefix) != mutation.prefix:
                    raise RuntimeError("VM-HA route identity changed before deletion")
                if mutation.kind is RouteMutationKind.DELETE and (
                    RouteManager._route_next_hop_allocation_id(observed[0])
                    != mutation.allocation_id
                ):
                    raise RuntimeError("VM-HA route next hop changed before deletion")
                self._delete_exact_route(mutation)
        if mutation.kind in {RouteMutationKind.CREATE, RouteMutationKind.REPLACE}:
            if created_route_id:
                return created_route_id
            operation, accepted = self._create_operation(mutation)
            try:
                wait_vm_ha_operation(operation)
            except Exception:
                observed_route_id = self._observe_desired_route(mutation)
                if not observed_route_id:
                    raise
            else:
                if self._operation_succeeded(operation):
                    observed_route_id = self._observe_desired_route(mutation)
                    if not observed_route_id:
                        raise RuntimeError("VM-HA route creation postcondition was not observed")
                else:
                    observed_route_id = self._observe_desired_route(mutation)
                    if not observed_route_id:
                        self._checkpoint(
                            mutation,
                            phase=(
                                RouteMutationPhase.ORIGINAL_ABSENT
                                if mutation.kind is RouteMutationKind.REPLACE
                                else RouteMutationPhase.INTENT
                            ),
                            rollback=mutation.rollback,
                            accepted_operation=None,
                        )
                        if mutation.kind is RouteMutationKind.REPLACE:
                            if mutation.rollback is None:
                                raise RuntimeError(
                                    "VM-HA terminal replacement failure has no rollback snapshot"
                                )
                            if self._observe_desired_route(mutation) is not None:
                                raise RuntimeError(
                                    "VM-HA desired route appeared before compensation"
                                )
                            try:
                                restored_route_id = self._restore_raw_route(
                                    mutation.rollback,
                                    mutation=mutation,
                                )
                            except Exception as rollback_error:
                                raise RuntimeError(
                                    "VM-HA route replacement and compensating rollback both failed"
                                ) from rollback_error
                            raise RouteReplacementCompensated(restored_route_id)
                        raise RuntimeError("VM-HA route creation operation failed")
            self._checkpoint(
                mutation,
                phase=RouteMutationPhase.DESIRED_PRESENT,
                rollback=mutation.rollback,
                accepted_operation=None,
            )
            return observed_route_id
        return None

    def recover_created_route(self, mutation: RouteMutation) -> str | None:
        return self._observe_desired_route(mutation)

    def recover_deleted_route(self, mutation: RouteMutation) -> bool:
        if not mutation.route_id:
            raise ValueError("VM-HA route deletion recovery requires an exact route identity")
        return not any(
            RouteManager._metadata_id(route) == mutation.route_id
            for route in self._raw_routes(mutation.route_target.route_table_id)
        )

    def recover_restored_route(self, mutation: RouteMutation) -> str | None:
        if mutation.rollback is None:
            return None
        return self._observe_rollback_route(mutation.rollback)

    @staticmethod
    def execute_verified_plan(
        plan: RouteReconciliationPlan,
        *,
        context: RouteReconciliationContext,
        apply_mutation: t.Callable[[RouteMutation], None],
        reobserve_ownership: t.Callable[[], VerifiedAllocationOwnership],
        reobserve_plan: t.Callable[[], RouteReconciliationPlan],
        receipt_store: _RouteReceiptStore,
    ) -> RouteApplyResult:
        return RouteManager.execute_vm_ha_route_plan(
            plan,
            context=context,
            apply_mutation=apply_mutation,
            reobserve_ownership=reobserve_ownership,
            reobserve_plan=reobserve_plan,
            receipt_store=receipt_store,
        )


class RouteManager:
    @staticmethod
    def _normalize_value(value) -> str:
        if hasattr(value, "value"):
            value = value.value
        return str(value or "").strip().lower()

    @staticmethod
    def _parse_ipv4_network(
        prefix: str,
        *,
        strict: bool = False,
    ) -> ipaddress.IPv4Network | None:
        try:
            network = ipaddress.ip_network(str(prefix), strict=strict)
        except Exception:
            return None
        if isinstance(network, ipaddress.IPv4Network):
            return network
        return None

    @classmethod
    def _parse_ipv4_networks(
        cls,
        prefixes,
        *,
        strict: bool = True,
    ) -> list[ipaddress.IPv4Network]:
        networks: list[ipaddress.IPv4Network] = []
        for prefix in prefixes or []:
            try:
                network = ipaddress.ip_network(str(prefix), strict=strict)
            except Exception as e:
                raise ValueError(f"Invalid IPv4 network prefix: {prefix}") from e
            if not isinstance(network, ipaddress.IPv4Network):
                raise ValueError(f"Expected IPv4 network prefix: {prefix}")
            networks.append(network)
        return networks

    def __init__(
        self,
        project_id: str | None,
        auth_token: str | None = None,
        *,
        ssh_policy: SSHTrustPolicy | None = None,
    ) -> None:
        self.project_id = project_id
        self.auth_token = auth_token
        self.endpoint = "vpc.api.nebius.cloud:443"
        self._ssh_policy = ssh_policy
        self._agent_capabilities_by_host: dict[str, frozenset[str]] = {}

    def _channel(self):
        """Create a synchronous gRPC channel for VPC API."""
        import os

        import grpc  # type: ignore

        token = self.auth_token or os.environ.get("NEBIUS_IAM_TOKEN")
        if not token:
            raise ValueError(
                "No authentication token available. Set NEBIUS_IAM_TOKEN or pass auth_token."
            )

        # Create a metadata callback for authentication
        def auth_metadata_plugin(context, callback):
            callback([("authorization", f"Bearer {token}")], None)

        # Create channel credentials with auth metadata
        auth_creds = grpc.metadata_call_credentials(t.cast(t.Any, auth_metadata_plugin))
        ssl_creds = grpc.ssl_channel_credentials()
        composite_creds = grpc.composite_channel_credentials(ssl_creds, auth_creds)

        # Return channel with composite credentials
        return grpc.secure_channel(self.endpoint, composite_creds)

    def _create_read_sdk(self) -> t.Any:
        """Create the supported synchronous Nebius SDK surface for route reads."""
        import os

        from nebius.sdk import SDK

        token = self.auth_token or os.environ.get("NEBIUS_IAM_TOKEN")
        if not token:
            raise ValueError(
                "No authentication token available. Set NEBIUS_IAM_TOKEN or pass auth_token."
            )
        return SDK(credentials=token, user_agent_prefix="nebius-vpngw")

    @staticmethod
    def _list_sdk_items(
        client: t.Any,
        request_type: t.Any,
        *,
        parent_id: str,
    ) -> tuple[object, ...]:
        """Read every SDK list page while rejecting cyclic or unbounded pagination."""
        items: list[object] = []
        page_token = ""
        seen_tokens: set[str] = set()
        for _page in range(1000):
            response = client.list(
                request_type(
                    parent_id=parent_id,
                    page_size=1000,
                    page_token=page_token,
                )
            ).wait()
            items.extend(tuple(getattr(response, "items", ()) or ()))
            next_token = str(getattr(response, "next_page_token", "") or "")
            if not next_token:
                return tuple(items)
            if next_token == page_token or next_token in seen_tokens:
                raise RuntimeError("VPC route listing returned a cyclic page token")
            seen_tokens.add(next_token)
            page_token = next_token
        raise RuntimeError("VPC route listing exceeded the bounded page limit")

    def _list_allocations_with_sdk(
        self,
        allocation_client: t.Any,
        list_request_type: t.Any,
    ) -> dict[str, str]:
        """Map every project allocation ID to a printable allocated IP address."""
        alloc_to_ip: dict[str, str] = {}
        allocations = self._list_sdk_items(
            allocation_client,
            list_request_type,
            parent_id=self.project_id or "",
        )
        for allocation in allocations:
            status = getattr(allocation, "status", None)
            details = getattr(status, "details", None)
            cidr = getattr(details, "allocated_cidr", None)
            try:
                network = ipaddress.ip_network(str(cidr), strict=False)
            except Exception:
                continue
            allocation_id = self._metadata_id(allocation)
            if not allocation_id:
                continue
            alloc_to_ip[allocation_id] = str(
                network.network_address
                if network.prefixlen == network.max_prefixlen
                else next(network.hosts(), network.network_address)
            )
        return alloc_to_ip

    def _resolve_target_network_id_with_sdk(
        self,
        sdk: t.Any,
        local_cfg: dict,
        *,
        subnet_client: t.Any,
        get_subnet_by_name_request: t.Any,
        network_client_type: t.Any,
        get_network_by_name_request: t.Any,
        list_networks_request: t.Any,
    ) -> str | None:
        """Resolve the configured or uniquely discoverable VPC through SDK clients."""
        gateway_group = local_cfg.get("gateway_group", {}) or {}
        explicit_network_id = str(gateway_group.get("network_id") or "").strip()
        if explicit_network_id:
            return explicit_network_id

        try:
            subnet = subnet_client.get_by_name(
                get_subnet_by_name_request(
                    parent_id=self.project_id or "",
                    name=self._gateway_subnet_name(local_cfg),
                )
            ).wait()
            subnet_network_id = getattr(getattr(subnet, "spec", None), "network_id", None)
            if subnet_network_id:
                return str(subnet_network_id)
        except Exception:
            pass

        network_client = network_client_type(sdk)
        try:
            network = network_client.get_by_name(
                get_network_by_name_request(
                    parent_id=self.project_id or "",
                    name="default-network",
                )
            ).wait()
            network_id = self._metadata_id(network)
            if network_id:
                return network_id
        except Exception:
            pass

        try:
            networks = self._list_sdk_items(
                network_client,
                list_networks_request,
                parent_id=self.project_id or "",
            )
        except Exception:
            return None
        if len(networks) == 1:
            return self._metadata_id(networks[0]) or None
        return None

    def _list_allocations(
        self, channel: t.Any
    ) -> tuple[list[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str]], dict[str, str]]:
        """Return list of (network, allocation_id) and a lookup map for pretty-printing."""
        from nebius.api.nebius.vpc.v1 import (  # type: ignore[attr-defined]
            allocation_service_pb2,
            allocation_service_pb2_grpc,
        )

        nets: list[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str]] = []
        alloc_to_ip: dict[str, str] = {}
        stub = allocation_service_pb2_grpc.AllocationServiceStub(channel)
        req = allocation_service_pb2.ListAllocationsRequest(parent_id=self.project_id or "")
        resp = stub.List(req)
        for alloc in resp.items:
            cidr = alloc.status.details.allocated_cidr
            try:
                net = ipaddress.ip_network(cidr, strict=False)
            except Exception:
                continue
            alloc_id = self._metadata_id(alloc)
            if not alloc_id:
                continue
            nets.append((net, alloc_id))
            # pick first host address to represent this allocation
            ip_str = str(
                net.network_address
                if net.prefixlen == net.max_prefixlen
                else next(net.hosts(), net.network_address)
            )
            alloc_to_ip[alloc_id] = ip_str
        return nets, alloc_to_ip

    def _find_gateway_private_allocations_by_index(
        self, compute_channel: t.Any, plan: ResolvedDeploymentPlan
    ) -> dict[int, str]:
        import ipaddress

        from nebius.api.nebius.compute.v1 import (  # type: ignore[attr-defined]
            instance_service_pb2,
            instance_service_pb2_grpc,
        )

        host_to_index = {
            inst.hostname: inst.instance_index for inst in plan.iter_instance_configs()
        }
        allocations_by_index: dict[int, str] = {}

        # List instances in the project and find the private (static) IP allocation
        # With the VM manager refactoring, private IPs now use static allocations
        istub = instance_service_pb2_grpc.InstanceServiceStub(compute_channel)
        ilist = istub.List(
            instance_service_pb2.ListInstancesRequest(parent_id=self.project_id or "")
        )
        for inst in ilist.items:
            instance_index = host_to_index.get(self._metadata_name(inst))
            if instance_index is None:
                continue
            for ni in inst.status.network_interfaces:
                # Check if this network interface has a private IP with a static allocation
                if ni.ip_address and ni.ip_address.allocation_id:
                    # Extract the IP address string (without CIDR notation)
                    ip_str = ni.ip_address.address.split("/")[0]
                    # Verify it's a private IP
                    if ipaddress.ip_address(ip_str).is_private:
                        allocations_by_index[instance_index] = ni.ip_address.allocation_id
                        break

        return allocations_by_index

    def _connection_instance_indices(self, conn: dict) -> list[int]:
        indices: set[int] = set()
        for tunnel in conn.get("tunnels") or []:
            if self._normalize_value(tunnel.get("ha_role") or "active") == "disable":
                continue
            try:
                indices.add(int(tunnel.get("gateway_instance_index", 0) or 0))
            except (TypeError, ValueError):
                continue
        return sorted(indices)

    def _connection_peer_ips(
        self,
        conn: dict,
        *,
        instance_index: int | None = None,
    ) -> set[str]:
        peer_ips: set[str] = set()
        for tunnel in conn.get("tunnels") or []:
            if self._normalize_value(tunnel.get("ha_role") or "active") == "disable":
                continue
            if instance_index is not None:
                try:
                    tunnel_instance_index = int(tunnel.get("gateway_instance_index", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if tunnel_instance_index != instance_index:
                    continue
            peer_ip = (
                ((tunnel.get("bgp", {}) or {}).get("remote_ip"))
                or tunnel.get("inner_remote_ip")
                or ""
            )
            if peer_ip:
                peer_ips.add(str(peer_ip))
        return peer_ips

    def _collect_remote_prefix_targets(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
        allocations_by_index: dict[int, str],
    ) -> dict[str, str]:
        defaults_mode = (
            self._normalize_value(
                (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode")
            )
            or "bgp"
        )

        prefix_targets: dict[str, str] = {}
        prefix_source_labels: dict[str, set[str]] = {}
        conflict_sources: dict[str, set[str]] = {}

        for conn in local_cfg.get("connections") or []:
            conn_name = str(conn.get("name") or "unnamed")
            mode = self._normalize_value(conn.get("routing_mode") or defaults_mode)
            instance_indices = self._connection_instance_indices(conn)

            if not instance_indices:
                continue
            if len(instance_indices) != 1:
                raise ValueError(
                    f"Connection '{conn_name}' spans multiple gateway VMs "
                    f"({instance_indices}). add-routes-local requires one owning "
                    "gateway_instance_index per connection so each remote prefix has "
                    "an unambiguous next-hop."
                )

            instance_index = instance_indices[0]
            alloc_id = allocations_by_index.get(instance_index)
            if not alloc_id:
                raise ValueError(
                    f"Could not resolve the private IP allocation for connection "
                    f"'{conn_name}' on gateway_instance_index={instance_index}."
                )

            if mode == "bgp":
                conn_prefixes = self._get_bgp_learned_routes(plan, conn, local_cfg)
            else:
                conn_prefixes = connection_static_remote_prefixes(
                    conn,
                    instance_index=instance_index,
                )

            source_label = f"{conn_name}@vm{instance_index}"
            for prefix in conn_prefixes:
                prefix_source_labels.setdefault(prefix, set()).add(source_label)
                previous_alloc = prefix_targets.get(prefix)
                if previous_alloc and previous_alloc != alloc_id:
                    conflict_sources.setdefault(prefix, set()).update(
                        prefix_source_labels.get(prefix, set())
                    )
                    continue
                prefix_targets[prefix] = alloc_id

        if conflict_sources:
            details = "; ".join(
                f"{prefix} via {', '.join(sources)}"
                for prefix, sources in sorted(
                    (pfx, sorted(srcs)) for pfx, srcs in conflict_sources.items()
                )
            )
            raise ValueError(
                "The same remote prefix is present on more than one gateway VM. "
                "add-routes-local cannot choose a single next-hop. Resolve the "
                f"overlap first: {details}"
            )

        return prefix_targets

    @staticmethod
    def _gateway_subnet_name(local_cfg: dict) -> str:
        gateway_group = local_cfg.get("gateway_group", {}) or {}
        subnet_cfg = gateway_group.get("subnet", {}) or {}
        return str(subnet_cfg.get("name") or "vpngw-subnet")

    @staticmethod
    def _subnet_uses_network_pools(subnet_obj) -> bool:
        subnet_spec = getattr(subnet_obj, "spec", None)
        ipv4_private_pools = getattr(subnet_spec, "ipv4_private_pools", None)
        return bool(getattr(ipv4_private_pools, "use_network_pools", False))

    @staticmethod
    def _extract_explicit_subnet_cidrs(subnet_obj) -> list[ipaddress.IPv4Network]:
        subnet_spec = getattr(subnet_obj, "spec", None)
        ipv4_private_pools = getattr(subnet_spec, "ipv4_private_pools", None)
        if not ipv4_private_pools or getattr(ipv4_private_pools, "use_network_pools", False):
            return []

        networks: list[ipaddress.IPv4Network] = []
        for pool in getattr(ipv4_private_pools, "pools", []) or []:
            for cidr_obj in getattr(pool, "cidrs", []) or []:
                cidr = getattr(cidr_obj, "cidr", None)
                if not cidr:
                    continue
                network = RouteManager._parse_ipv4_network(str(cidr))
                if network is not None:
                    networks.append(network)
        return networks

    @staticmethod
    def _extract_status_subnet_cidrs(subnet_obj) -> list[ipaddress.IPv4Network]:
        subnet_status = getattr(subnet_obj, "status", None)
        status_pools = getattr(subnet_status, "ipv4_private_pools", None)
        cidrs = [
            cidr
            for pool in status_pools or ()
            for cidr in (getattr(pool, "cidrs", ()) or ())
        ]
        if not cidrs:
            # Compatibility with older SDK responses that predate status pools.
            # Current SDK objects are handled above and never touch this
            # deprecated property.
            cidrs = list(getattr(subnet_status, "ipv4_private_cidrs", ()) or ())
        networks: list[ipaddress.IPv4Network] = []
        for cidr in cidrs:
            network = RouteManager._parse_ipv4_network(str(cidr))
            if network is not None:
                networks.append(network)
        return networks

    def _effective_subnet_cidrs(self, subnet_obj) -> list[ipaddress.IPv4Network]:
        explicit_cidrs = self._extract_explicit_subnet_cidrs(subnet_obj)
        if explicit_cidrs:
            return explicit_cidrs
        return self._extract_status_subnet_cidrs(subnet_obj)

    def _subtract_subnet_cidrs(
        self,
        source_cidrs: list[ipaddress.IPv4Network],
        blocked_cidrs: list[ipaddress.IPv4Network],
    ) -> list[ipaddress.IPv4Network]:
        remaining = list(source_cidrs)
        for blocked in self._sort_networks(blocked_cidrs):
            updated: list[ipaddress.IPv4Network] = []
            for network in remaining:
                if network.version != blocked.version or not network.overlaps(blocked):
                    updated.append(network)
                    continue
                if network.subnet_of(blocked):
                    continue
                if blocked.subnet_of(network):
                    updated.extend(network.address_exclude(blocked))
                    continue
                updated.append(network)
            remaining = updated

        unique_networks = {str(network): network for network in remaining}
        return self._sort_networks(list(unique_networks.values()))

    def _selection_cidrs_for_subnet(
        self,
        subnet_obj,
        *,
        other_explicit_cidrs: list[ipaddress.IPv4Network],
    ) -> tuple[list[ipaddress.IPv4Network], bool]:
        explicit_cidrs = self._extract_explicit_subnet_cidrs(subnet_obj)
        if explicit_cidrs:
            return self._sort_networks(explicit_cidrs), False

        status_cidrs = self._extract_status_subnet_cidrs(subnet_obj)
        if not self._subnet_uses_network_pools(subnet_obj):
            return self._sort_networks(status_cidrs), False

        sanitized_cidrs = self._subtract_subnet_cidrs(status_cidrs, other_explicit_cidrs)
        raw_status_set = {str(network) for network in status_cidrs}
        sanitized_set = {str(network) for network in sanitized_cidrs}
        return sanitized_cidrs, raw_status_set != sanitized_set

    def _ssh_policy_hostname(self, external_ip: str) -> str | None:
        if self._ssh_policy is None:
            return None
        try:
            return self._ssh_policy.hostname_for_transport(external_ip)
        except ValueError:
            raise RouteManagementError(
                "VM-HA SSH trust does not bind this gateway management address."
            ) from None

    def _ssh_base_command(
        self,
        local_cfg: dict,
        *,
        external_ip: str,
        connect_timeout: int = 10,
    ) -> list[str]:
        import os

        gateway_group = local_cfg.get("gateway_group", {}) or {}
        vm_spec = gateway_group.get("vm_spec", {}) or {}
        ssh_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")

        key_path = Path(str(ssh_key)).expanduser() if ssh_key else None
        return build_openssh_base_command(
            key_path=key_path,
            connect_timeout=connect_timeout,
            policy=self._ssh_policy,
            hostname=self._ssh_policy_hostname(external_ip),
        )

    @staticmethod
    def _ssh_target(local_cfg: dict, external_ip: str) -> str:
        import os

        gateway_group = local_cfg.get("gateway_group", {}) or {}
        vm_spec = gateway_group.get("vm_spec", {}) or {}
        username = str(vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER") or "ubuntu")
        return f"{username}@{external_ip}"

    def _run_ssh(
        self,
        local_cfg: dict,
        external_ip: str,
        remote_cmd: str,
        *,
        timeout: int = 15,
        ssh_connect_timeout: int = 10,
        stdin_text: str | None = None,
    ):
        import subprocess

        try:
            return subprocess.run(
                self._ssh_base_command(
                    local_cfg,
                    external_ip=external_ip,
                    connect_timeout=ssh_connect_timeout,
                )
                + [self._ssh_target(local_cfg, external_ip), remote_cmd],
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except RouteManagementError:
            raise
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            raise RouteManagementError(
                "SSH transport or host-trust validation failed before the gateway "
                "command completed."
            ) from error

    def _query_bgp_summary(self, external_ip: str, local_cfg: dict) -> dict | None:
        import json

        result = self._run_ssh(
            local_cfg,
            external_ip,
            "sudo vtysh -c 'show bgp summary json'",
            timeout=15,
        )
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _query_bgp_advertised_routes(
        self,
        external_ip: str,
        peer_ip: str,
        local_cfg: dict,
    ) -> dict | None:
        import json

        try:
            if ipaddress.ip_address(peer_ip).version != 4:
                return None
        except ValueError:
            return None

        result = self._run_ssh(
            local_cfg,
            external_ip,
            f"sudo vtysh -c 'show bgp ipv4 unicast neighbors {peer_ip} advertised-routes json'",
            timeout=15,
        )
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _query_vm_ha_status(self, external_ip: str, local_cfg: dict) -> dict | None:
        result = self._run_ssh(
            local_cfg,
            external_ip,
            "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-status",
            timeout=15,
        )
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _vm_ha_advertisement_authority(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
    ) -> VMHAAdvertisementAuthority | None:
        """Resolve one exact owner from two current, generation-bound agent projections."""

        if plan.vm_ha is None:
            return None
        instances = tuple(plan.iter_instance_configs())
        if len(instances) != 2:
            return None
        statuses: dict[str, dict] = {}
        node_to_host: dict[str, str] = {}
        allocation_ids: set[str] = set()
        owner_node_ids: set[str] = set()
        generation_ids: set[str] = set()
        ownership_epochs_by_hostname: dict[str, str] = {}
        for inst_cfg in instances:
            node = inst_cfg.vm_ha_node
            generation = inst_cfg.vm_ha_generation
            if node is None or generation is None or not inst_cfg.external_ip:
                return None
            status = self._query_vm_ha_status(inst_cfg.external_ip, local_cfg)
            expected_digests = {
                "configuration": generation.digests.configuration,
                "static_routes": generation.digests.static_routes,
                "bgp_policy": generation.digests.bgp_policy,
            }
            if not (
                isinstance(status, dict)
                and status.get("schema") == "nebius-vpngw/vm-ha-status-v1"
                and status.get("cluster_id") == plan.vm_ha.cluster_id
                and status.get("node_id") == node.node_id
                and status.get("generation_id") == generation.generation_id
                and status.get("digests") == expected_digests
                and status.get("apply_locked") is False
                and status.get("pending_operation_id") is None
                and isinstance(status.get("allocation_id"), str)
                and status.get("allocation_id")
                and isinstance(status.get("observed_owner_node_id"), str)
                and isinstance(status.get("ownership_epoch"), str)
                and status.get("ownership_epoch")
            ):
                return None
            statuses[inst_cfg.hostname] = status
            node_to_host[node.node_id] = inst_cfg.hostname
            allocation_ids.add(t.cast(str, status["allocation_id"]))
            owner_node_ids.add(t.cast(str, status["observed_owner_node_id"]))
            generation_ids.add(generation.generation_id)
            ownership_epochs_by_hostname[inst_cfg.hostname] = t.cast(
                str, status["ownership_epoch"]
            )
        if (
            len(allocation_ids) != 1
            or len(owner_node_ids) != 1
            or len(generation_ids) != 1
        ):
            return None
        owner_node_id = next(iter(owner_node_ids))
        owner_hostname = node_to_host.get(owner_node_id)
        if owner_hostname is None:
            return None
        for hostname, status in statuses.items():
            if hostname == owner_hostname:
                route_receipt = status.get("route_reconciliation")
                if not (
                    status.get("data_plane_mode") == "active"
                    and status.get("candidate_attachment_exact") is True
                    and status.get("ownership_re_read_exact") is True
                    and isinstance(route_receipt, dict)
                    and route_receipt.get("owner_node_id") == owner_node_id
                    and route_receipt.get("allocation_id") in allocation_ids
                    and route_receipt.get("ownership_epoch")
                    == ownership_epochs_by_hostname[owner_hostname]
                    and route_receipt.get("generation_id") in generation_ids
                    and route_receipt.get("digests") == status.get("digests")
                ):
                    return None
            elif not (
                status.get("data_plane_mode") == "passive"
                and status.get("former_attachment_exact") is True
                and status.get("candidate_attachment_absent") is True
            ):
                return None
        return VMHAAdvertisementAuthority(
            owner_hostname=owner_hostname,
            generation_id=next(iter(generation_ids)),
            owner_node_id=owner_node_id,
            allocation_id=next(iter(allocation_ids)),
            ownership_epochs_by_hostname=tuple(
                sorted(ownership_epochs_by_hostname.items())
            ),
        )

    @staticmethod
    def _vm_ha_lifecycle_is_stable(
        plan: ResolvedDeploymentPlan,
        guard: t.Callable[[], bool] | None,
    ) -> bool:
        if plan.vm_ha is None:
            return True
        if guard is None:
            return False
        try:
            return guard() is True
        except (OSError, RuntimeError, TypeError, ValueError):
            return False

    def _expected_advertised_prefixes(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
        *,
        vm_ha_owner_hostname: str | None = None,
    ) -> dict[str, dict[str, set[str]]] | None:
        if plan.vm_ha is not None and vm_ha_owner_hostname is None:
            return None
        defaults_mode = (
            self._normalize_value(
                (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode")
            )
            or "bgp"
        )
        normalized_local_prefixes = {
            str(network)
            for prefix in (local_cfg.get("gateway", {}).get("local_prefixes") or [])
            if (network := self._parse_prefix(prefix)) is not None
        }
        expected_by_host: dict[str, dict[str, set[str]]] = {}
        instances_by_index = {
            inst_cfg.instance_index: inst_cfg for inst_cfg in plan.iter_instance_configs()
        }

        for conn in local_cfg.get("connections", []) or []:
            if self._normalize_value(conn.get("routing_mode") or defaults_mode) != "bgp":
                continue

            conn_bgp = conn.get("bgp", {}) or {}
            advertised_prefixes = (
                normalized_local_prefixes
                if conn_bgp.get("advertise_local_prefixes", True)
                else set()
            )

            for tunnel in conn.get("tunnels", []) or []:
                instance_index = int(tunnel.get("gateway_instance_index", 0) or 0)
                inst_cfg = instances_by_index.get(instance_index)
                if not inst_cfg:
                    continue
                host_expectations = expected_by_host.setdefault(inst_cfg.hostname, {})
                if self._normalize_value(tunnel.get("ha_role") or "active") == "disable":
                    continue

                peer_ip = (
                    ((tunnel.get("bgp", {}) or {}).get("remote_ip"))
                    or tunnel.get("inner_remote_ip")
                    or ""
                )
                if not peer_ip:
                    continue

                host_prefixes = advertised_prefixes
                if plan.vm_ha is not None and inst_cfg.hostname != vm_ha_owner_hostname:
                    host_prefixes = set()
                host_expectations[peer_ip] = set(host_prefixes)

        return expected_by_host

    def _bgp_instance_indices(self, local_cfg: dict) -> set[int]:
        default_mode = (
            self._normalize_value(
                ((local_cfg.get("defaults") or {}).get("routing") or {}).get("mode")
            )
            or "bgp"
        )
        indices: set[int] = set()
        for connection in local_cfg.get("connections") or []:
            mode = self._normalize_value(connection.get("routing_mode") or default_mode)
            if mode == "bgp":
                indices.update(self._connection_instance_indices(connection))
        return indices

    @staticmethod
    def _parse_prefix(prefix: str) -> ipaddress.IPv4Network | None:
        return RouteManager._parse_ipv4_network(prefix)

    @staticmethod
    def _path_nexthops(path: dict) -> set[str]:
        return {
            str(nh_ip)
            for nh in path.get("nexthops", []) or []
            if (nh_ip := nh.get("ip")) and nh_ip != "0.0.0.0"
        }

    @staticmethod
    def _sort_prefix_targets(prefix_targets: dict[str, str]) -> dict[str, str]:
        def _sort_key(item: tuple[str, str]) -> tuple[int, int, str]:
            prefix, _alloc_id = item
            network = ipaddress.ip_network(prefix, strict=False)
            return (int(network.network_address), network.prefixlen, prefix)

        return dict(sorted(prefix_targets.items(), key=_sort_key))

    @staticmethod
    def _sort_networks(
        networks: list[ipaddress.IPv4Network],
    ) -> list[ipaddress.IPv4Network]:
        return sorted(
            networks,
            key=lambda network: (
                int(network.network_address),
                network.prefixlen,
                str(network),
            ),
        )

    @staticmethod
    def _extract_pool_cidrs(pool_obj) -> list[ipaddress.IPv4Network]:
        pool_spec = getattr(pool_obj, "spec", None)
        networks: list[ipaddress.IPv4Network] = []
        for cidr_obj in getattr(pool_spec, "cidrs", []) or []:
            cidr = getattr(cidr_obj, "cidr", None)
            if not cidr:
                continue
            network = RouteManager._parse_ipv4_network(str(cidr))
            if network is not None:
                networks.append(network)
        return networks

    def _get_network_private_pool_cidrs(
        self,
        channel,
        *,
        network_id: str,
    ) -> list[ipaddress.IPv4Network]:
        from nebius.api.nebius.vpc.v1 import (  # type: ignore[attr-defined]
            network_service_pb2,
            network_service_pb2_grpc,
            pool_service_pb2,
            pool_service_pb2_grpc,
        )

        nstub = network_service_pb2_grpc.NetworkServiceStub(channel)
        pstub = pool_service_pb2_grpc.PoolServiceStub(channel)

        try:
            network_obj = nstub.Get(network_service_pb2.GetNetworkRequest(id=network_id))
        except Exception:
            return []

        network_spec = getattr(network_obj, "spec", None)
        private_pools = getattr(network_spec, "ipv4_private_pools", None)
        pool_refs = getattr(private_pools, "pools", []) or []

        networks: list[ipaddress.IPv4Network] = []
        for pool_ref in pool_refs:
            inline_cidrs = self._extract_pool_cidrs(pool_ref)
            if inline_cidrs:
                networks.extend(inline_cidrs)
                continue

            pool_id = getattr(pool_ref, "pool_id", None) or getattr(pool_ref, "id", None)
            if not pool_id:
                continue

            try:
                pool_obj = pstub.Get(pool_service_pb2.GetPoolRequest(id=str(pool_id)))
            except Exception:
                continue

            networks.extend(self._extract_pool_cidrs(pool_obj))

        unique_networks = {str(network): network for network in networks}
        return self._sort_networks(list(unique_networks.values()))

    def _filter_prefix_targets(
        self,
        prefix_targets: dict[str, str],
        *,
        local_networks: list[ipaddress.IPv4Network],
        network_pool_networks: list[ipaddress.IPv4Network],
    ) -> tuple[dict[str, str], list[str], list[tuple[str, str]]]:
        filtered_prefix_targets: dict[str, str] = {}
        skipped_local_prefixes: list[str] = []
        skipped_network_pool_prefixes: list[tuple[str, str]] = []

        for prefix, alloc_id in prefix_targets.items():
            prefix_network = self._parse_ipv4_network(prefix)
            if prefix_network is None:
                continue

            if any(prefix_network.overlaps(local_network) for local_network in local_networks):
                skipped_local_prefixes.append(str(prefix_network))
                continue

            overlapping_pool = next(
                (
                    network_pool
                    for network_pool in network_pool_networks
                    if prefix_network.overlaps(network_pool)
                ),
                None,
            )
            if overlapping_pool is not None:
                skipped_network_pool_prefixes.append((str(prefix_network), str(overlapping_pool)))
                continue

            filtered_prefix_targets[str(prefix_network)] = alloc_id

        return (
            self._sort_prefix_targets(filtered_prefix_targets),
            skipped_local_prefixes,
            skipped_network_pool_prefixes,
        )

    def _summarize_prefix_targets(self, prefix_targets: dict[str, str]) -> dict[str, str]:
        grouped: dict[str, list[ipaddress.IPv4Network]] = {}
        for prefix, alloc_id in prefix_targets.items():
            network = self._parse_ipv4_network(prefix)
            if network is None:
                continue
            grouped.setdefault(alloc_id, []).append(network)

        summarized: dict[str, str] = {}
        for alloc_id, networks in grouped.items():
            for collapsed in ipaddress.collapse_addresses(networks):
                summarized[str(collapsed)] = alloc_id

        return self._sort_prefix_targets(summarized)

    @staticmethod
    def _route_destination_network(route) -> ipaddress.IPv4Network | None:
        spec = getattr(route, "spec", None)
        destination = getattr(spec, "destination", None) if spec else None
        cidr = getattr(destination, "cidr", None) if destination else None
        if not cidr:
            return None
        return RouteManager._parse_ipv4_network(str(cidr))

    @staticmethod
    def _route_next_hop_allocation_id(route) -> str | None:
        spec = getattr(route, "spec", None)
        next_hop = getattr(spec, "next_hop", None) if spec else None
        allocation = getattr(next_hop, "allocation", None) if next_hop else None
        allocation_id = getattr(allocation, "id", None) if allocation else None
        return str(allocation_id) if allocation_id else None

    @staticmethod
    def _route_spec(route):
        return getattr(route, "spec", None)

    @staticmethod
    def _subnet_route_table(subnet_obj):
        status_obj = getattr(subnet_obj, "status", None)
        return getattr(status_obj, "route_table", None)

    @staticmethod
    def _subnet_network_id(subnet_obj) -> str:
        subnet_spec = getattr(subnet_obj, "spec", None)
        return str(getattr(subnet_spec, "network_id", "") or "")

    @staticmethod
    def _metadata_name(resource) -> str:
        metadata = getattr(resource, "metadata", None)
        return str(getattr(metadata, "name", None) or "")

    @staticmethod
    def _metadata_id(resource) -> str:
        metadata = getattr(resource, "metadata", None)
        return str(getattr(metadata, "id", None) or "")

    @staticmethod
    def _route_name(route) -> str:
        return RouteManager._metadata_name(route)

    @staticmethod
    def _route_is_managed(route) -> bool:
        return RouteManager._route_name(route).startswith("vpngw-")

    def _vm_ha_owned_route_snapshots(
        self,
        routes,
        *,
        ownership_by_route_id: t.Mapping[str, ManagedRouteOwnership],
    ):
        """Adapt VPC route objects using an explicit HA management ledger.

        A ``vpngw-`` metadata-name prefix remains a legacy non-HA convention;
        it is deliberately insufficient to establish VM-HA route ownership.
        """

        return owned_route_snapshots(
            routes,
            ownership_by_route_id=ownership_by_route_id,
            route_id=self._metadata_id,
            route_prefix=lambda route: (
                str(network)
                if (network := self._route_destination_network(route)) is not None
                else None
            ),
            route_allocation_id=self._route_next_hop_allocation_id,
        )

    @classmethod
    def _vm_ha_route_next_hop(cls, route) -> str:
        spec = getattr(route, "spec", None)
        next_hop = getattr(spec, "next_hop", None) if spec else None
        allocation_id = cls._route_next_hop_allocation_id(route)
        default_egress = bool(
            getattr(next_hop, "default_egress_gateway", False) if next_hop else False
        )
        if allocation_id and default_egress:
            raise ValueError("Observed route has an ambiguous next hop")
        if allocation_id:
            return f"allocation:{allocation_id}"
        if default_egress:
            return "default-egress-gateway"
        raise ValueError("Observed route has no supported next hop")

    def _vm_ha_route_snapshots(
        self,
        routes,
        *,
        ownership_by_route_id: t.Mapping[str, ManagedRouteOwnership],
        route_target: VMHARouteTarget,
    ):
        """Adapt all VPC routes without granting unledgered routes mutation authority."""

        return route_observation_snapshots(
            routes,
            ownership_by_route_id=ownership_by_route_id,
            route_id=self._metadata_id,
            route_prefix=lambda route: (
                str(network)
                if (network := self._route_destination_network(route)) is not None
                else None
            ),
            route_allocation_id=self._route_next_hop_allocation_id,
            route_next_hop=self._vm_ha_route_next_hop,
            route_rollback=NebiusSDKRouteBackend._rollback_snapshot,
            route_target=route_target,
        )

    @staticmethod
    def execute_vm_ha_route_plan(
        plan: RouteReconciliationPlan,
        *,
        context: RouteReconciliationContext,
        apply_mutation: t.Callable[[RouteMutation], None],
        reobserve_ownership: t.Callable[[], VerifiedAllocationOwnership],
        reobserve_plan: t.Callable[[], RouteReconciliationPlan],
        receipt_store: _RouteReceiptStore,
    ) -> RouteApplyResult:
        """Execute and receipt one owner-verified HA plan through the durable store."""

        return execute_route_plan(
            plan,
            apply_mutation,
            context=context,
            reobserve_ownership=reobserve_ownership,
            reobserve_plan=reobserve_plan,
            persist_receipt=receipt_store.save_route_reconciliation_receipt,
            observe_receipt=receipt_store.load_route_reconciliation_receipt,
        )

    def _routes_with_destination(
        self,
        routes,
        destination_cidr: str,
    ) -> list[object]:
        matching_routes = []
        for route in routes:
            route_network = self._route_destination_network(route)
            if route_network is None:
                continue
            if str(route_network) == destination_cidr:
                matching_routes.append(route)
        return matching_routes

    @staticmethod
    def _route_next_hop_label(route) -> str:
        allocation_id = RouteManager._route_next_hop_allocation_id(route)
        if allocation_id:
            return f"allocation {allocation_id}"

        spec = getattr(route, "spec", None)
        next_hop = getattr(spec, "next_hop", None) if spec else None
        if getattr(next_hop, "default_egress_gateway", False):
            return "default-egress"
        return "non-allocation next-hop"

    def _installed_prefix_targets(
        self,
        existing_routes,
        desired_prefix_targets: dict[str, str],
    ) -> dict[str, str]:
        installed: dict[str, str] = {}
        for prefix, alloc_id in desired_prefix_targets.items():
            matching_routes = self._routes_with_destination(existing_routes, prefix)
            if any(
                self._route_next_hop_allocation_id(route) == alloc_id for route in matching_routes
            ):
                installed[prefix] = alloc_id
        return installed

    def _find_redundant_managed_routes(
        self,
        existing_routes,
        desired_prefix_targets: dict[str, str],
    ) -> list[object]:
        desired_by_alloc: dict[str, list[ipaddress.IPv4Network]] = {}
        desired_prefixes = set(desired_prefix_targets)

        for prefix, alloc_id in desired_prefix_targets.items():
            network = self._parse_ipv4_network(prefix)
            if network is None:
                continue
            desired_by_alloc.setdefault(alloc_id, []).append(network)

        redundant = []
        for route in existing_routes:
            route_name = self._route_name(route)
            if not route_name.startswith("vpngw-"):
                continue

            route_alloc_id = self._route_next_hop_allocation_id(route)
            if not route_alloc_id:
                continue

            route_network = self._route_destination_network(route)
            if route_network is None:
                continue

            route_prefix = str(route_network)
            if route_prefix in desired_prefixes:
                continue

            desired_networks = desired_by_alloc.get(route_alloc_id) or []
            if any(route_network.subnet_of(summary_net) for summary_net in desired_networks):
                redundant.append(route)

        return redundant

    def _find_redundant_managed_covering_routes(
        self,
        existing_routes,
        desired_prefix_targets: dict[str, str],
        effective_prefix_targets: dict[str, str],
    ) -> list[object]:
        desired_by_alloc: dict[str, list[ipaddress.IPv4Network]] = {}
        desired_prefixes = set(desired_prefix_targets)
        effective_prefixes = set(effective_prefix_targets)

        for prefix, alloc_id in desired_prefix_targets.items():
            network = self._parse_ipv4_network(prefix)
            if network is None:
                continue
            desired_by_alloc.setdefault(alloc_id, []).append(network)

        redundant = []
        for route in existing_routes:
            route_name = self._route_name(route)
            if not route_name.startswith("vpngw-"):
                continue

            route_alloc_id = self._route_next_hop_allocation_id(route)
            if not route_alloc_id:
                continue

            route_network = self._route_destination_network(route)
            if route_network is None:
                continue

            route_prefix = str(route_network)
            if route_prefix in desired_prefixes:
                continue

            desired_networks = desired_by_alloc.get(route_alloc_id) or []
            desired_prefixes_within_route = [
                str(desired_network)
                for desired_network in desired_networks
                if desired_network.subnet_of(route_network)
            ]
            if not desired_prefixes_within_route:
                continue

            if all(prefix in effective_prefixes for prefix in desired_prefixes_within_route):
                redundant.append(route)

        return redundant

    def _route_signature(self, route) -> tuple[str, str] | None:
        route_network = self._route_destination_network(route)
        if route_network is None:
            return None
        return str(route_network), self._route_next_hop_label(route)

    def _missing_route_signatures(
        self,
        expected_signatures: set[tuple[str, str]],
        existing_routes,
    ) -> list[tuple[str, str]]:
        existing_signatures = {
            signature
            for route in existing_routes
            if (signature := self._route_signature(route)) is not None
        }
        return sorted(expected_signatures - existing_signatures)

    @staticmethod
    def _copyable_routes(existing_routes) -> list[object]:
        return [route for route in existing_routes if not RouteManager._route_is_managed(route)]

    @staticmethod
    def _sanitize_name_fragment(value: str) -> str:
        sanitized = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower())
        sanitized = re.sub(r"-+", "-", sanitized).strip("-")
        return sanitized or "route-table"

    def _swap_route_table_name(self, subnet_name: str) -> str:
        suffix = f"{int(time.time())}-{int(time.time_ns() % 1_000_000):06d}"
        name = f"{self._sanitize_name_fragment(subnet_name)}-vpngw-rt-swap-{suffix}"
        return name[:63]

    def _route_table_name(self, subnet_name: str) -> str:
        return f"{subnet_name}-vpngw-rt"

    @staticmethod
    def _subnet_pool_group_to_dict(pool_group) -> dict | None:
        if pool_group is None:
            return None

        pools: list[dict[str, object]] = []
        result: dict[str, object] = {
            "use_network_pools": bool(getattr(pool_group, "use_network_pools", False)),
            "pools": pools,
        }
        for pool in getattr(pool_group, "pools", []) or []:
            cidrs: list[dict[str, object]] = []
            pool_entry: dict[str, object] = {"cidrs": cidrs}
            for cidr_obj in getattr(pool, "cidrs", []) or []:
                cidr = getattr(cidr_obj, "cidr", None)
                if not cidr:
                    continue
                cidr_entry: dict[str, object] = {"cidr": str(cidr)}
                max_mask_length = getattr(cidr_obj, "max_mask_length", None)
                if max_mask_length is not None and max_mask_length != 0:
                    cidr_entry["max_mask_length"] = int(max_mask_length)
                state = getattr(cidr_obj, "state", None)
                if state is not None and hasattr(state, "name"):
                    cidr_entry["state"] = str(state.name).lower()
                elif state not in (None, 0, ""):
                    cidr_entry["state"] = str(state)
                cidrs.append(cidr_entry)
            if cidrs:
                pools.append(pool_entry)
        return result

    def _write_swap_rollback_spec(
        self,
        *,
        rollback_dir: Path,
        subnet_obj,
        previous_route_table_id: str,
    ) -> Path:
        rollback_dir.mkdir(parents=True, exist_ok=True)

        subnet_name = self._metadata_name(subnet_obj) or "subnet"
        subnet_id = self._metadata_id(subnet_obj)
        subnet_spec = getattr(subnet_obj, "spec", None)
        network_id = self._subnet_network_id(subnet_obj)

        metadata_payload: dict[str, object] = {
            "id": subnet_id,
            "parent_id": self.project_id or "",
            "name": str(subnet_name),
        }
        spec_payload: dict[str, object] = {
            "network_id": network_id,
            "route_table_id": previous_route_table_id,
        }
        payload: dict[str, object] = {"metadata": metadata_payload, "spec": spec_payload}

        private_pools = self._subnet_pool_group_to_dict(
            getattr(subnet_spec, "ipv4_private_pools", None)
        )
        public_pools = self._subnet_pool_group_to_dict(
            getattr(subnet_spec, "ipv4_public_pools", None)
        )
        if private_pools is not None:
            spec_payload["ipv4_private_pools"] = private_pools
        if public_pools is not None:
            spec_payload["ipv4_public_pools"] = public_pools

        safe_subnet = self._sanitize_name_fragment(str(subnet_name))
        rollback_path = rollback_dir / (
            f"rollback-{safe_subnet}-{int(time.time())}-{int(time.time_ns() % 1_000_000):06d}.json"
        )
        rollback_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return rollback_path

    @staticmethod
    def _subnet_update_spec(subnet_pb2, subnet_obj, *, route_table_id: str):
        subnet_spec = getattr(subnet_obj, "spec", None)
        existing_ipv4_private_pools = getattr(subnet_spec, "ipv4_private_pools", None)
        existing_ipv4_public_pools = getattr(subnet_spec, "ipv4_public_pools", None)
        return subnet_pb2.SubnetSpec(
            network_id=RouteManager._subnet_network_id(subnet_obj),
            route_table_id=route_table_id,
            ipv4_private_pools=existing_ipv4_private_pools,
            ipv4_public_pools=existing_ipv4_public_pools,
        )

    def _attach_route_table_to_subnet(
        self,
        sstub,
        subnet_service_pb2,
        metadata_pb2,
        subnet_pb2,
        *,
        subnet_obj,
        route_table_id: str,
    ) -> None:
        sstub.Update(
            subnet_service_pb2.UpdateSubnetRequest(
                metadata=metadata_pb2.ResourceMetadata(id=self._metadata_id(subnet_obj)),
                spec=self._subnet_update_spec(
                    subnet_pb2,
                    subnet_obj,
                    route_table_id=route_table_id,
                ),
            )
        )

    def _list_route_table_routes(
        self,
        rstub,
        route_service_pb2,
        *,
        route_table_id: str,
    ):
        return rstub.List(route_service_pb2.ListRoutesRequest(parent_id=route_table_id)).items

    def _copy_routes_to_route_table(
        self,
        rstub,
        route_service_pb2,
        metadata_pb2,
        *,
        destination_route_table_id: str,
        routes_to_copy,
    ) -> tuple[int, list[tuple[str, str]]]:
        copied = 0
        failures: list[tuple[str, str]] = []
        for route in routes_to_copy:
            destination = self._route_destination_network(route)
            destination_label = str(destination) if destination else "unknown"
            route_name = self._route_name(route)
            metadata_name = (
                route_name[:63]
                if route_name
                else f"copied-{destination_label.replace('/', '-')}"[:63]
            )
            try:
                rstub.Create(
                    route_service_pb2.CreateRouteRequest(
                        metadata=metadata_pb2.ResourceMetadata(
                            parent_id=destination_route_table_id,
                            name=metadata_name,
                        ),
                        spec=self._route_spec(route),
                    )
                )
                copied += 1
            except Exception as e:
                failures.append((destination_label, str(e)))
        return copied, failures

    def _reconcile_route_table(
        self,
        rstub,
        route_service_pb2,
        route_pb2,
        metadata_pb2,
        *,
        route_table_id: str,
        prefix_targets: dict[str, str],
        summarize: bool,
    ):
        try:
            existing_routes = self._list_route_table_routes(
                rstub,
                route_service_pb2,
                route_table_id=route_table_id,
            )
        except Exception as e:
            print(f"[yellow]Failed to list existing routes on {route_table_id}: {e}[/yellow]")
            existing_routes = []

        for pfx, alloc_id in prefix_targets.items():
            matching_routes = self._routes_with_destination(existing_routes, pfx)
            if matching_routes and any(
                self._route_next_hop_allocation_id(route) == alloc_id for route in matching_routes
            ):
                print(f"[blue]Route {pfx} already exists on {route_table_id}; skipping[/blue]")
                continue
            if matching_routes:
                existing_next_hops = ", ".join(
                    sorted({self._route_next_hop_label(route) for route in matching_routes})
                )
                print(
                    f"[yellow]Route {pfx} already exists on {route_table_id} with {existing_next_hops}; "
                    f"expected allocation {alloc_id}. Leaving the existing route unchanged.[/yellow]"
                )
                continue

            try:
                rstub.Create(
                    route_service_pb2.CreateRouteRequest(
                        metadata=metadata_pb2.ResourceMetadata(
                            parent_id=route_table_id,
                            name=f"vpngw-{pfx.replace('/', '-')}"[:63],
                        ),
                        spec=route_pb2.RouteSpec(
                            destination=route_pb2.DestinationMatch(cidr=pfx),
                            next_hop=route_pb2.NextHop(
                                allocation=route_pb2.AllocationNextHop(id=alloc_id)
                            ),
                        ),
                    )
                )
                print(
                    f"[green]Added route {pfx} -> allocation {alloc_id} on {route_table_id}[/green]"
                )
            except Exception as e:
                err_str = str(e).lower()
                if "already exists" in err_str or "duplicate" in err_str:
                    try:
                        existing_routes = self._list_route_table_routes(
                            rstub,
                            route_service_pb2,
                            route_table_id=route_table_id,
                        )
                    except Exception:
                        existing_routes = []
                    matching_routes = self._routes_with_destination(existing_routes, pfx)
                    if any(
                        self._route_next_hop_allocation_id(route) == alloc_id
                        for route in matching_routes
                    ):
                        print(
                            f"[blue]Route {pfx} already exists on {route_table_id}; skipping[/blue]"
                        )
                    else:
                        existing_next_hops = (
                            ", ".join(
                                sorted(
                                    {self._route_next_hop_label(route) for route in matching_routes}
                                )
                            )
                            or "unknown next-hop"
                        )
                        print(
                            f"[yellow]Route {pfx} already exists on {route_table_id} with "
                            f"{existing_next_hops}; expected allocation {alloc_id}. "
                            "Leaving the existing route unchanged.[/yellow]"
                        )
                elif summarize and ("max-route-count" in err_str or "quota exceeded" in err_str):
                    redundant_routes = self._find_redundant_managed_routes(
                        existing_routes,
                        {pfx: alloc_id},
                    )
                    if not redundant_routes:
                        print(
                            f"[yellow]Failed to add route {pfx} on {route_table_id}: {e}[/yellow]"
                        )
                        continue

                    print(
                        f"[yellow]Route table {route_table_id} hit its route limit while adding {pfx}. "
                        f"Deleting {len(redundant_routes)} covered vpngw-managed route(s) and retrying.[/yellow]"
                    )
                    deleted = self._delete_routes(
                        rstub,
                        route_service_pb2,
                        redundant_routes,
                        route_table_id=route_table_id,
                    )
                    if not deleted:
                        print(
                            f"[yellow]Failed to add route {pfx} on {route_table_id}: {e}[/yellow]"
                        )
                        continue

                    existing_routes = [
                        route
                        for route in existing_routes
                        if getattr(getattr(route, "metadata", None), "id", None)
                        not in {
                            getattr(getattr(dead_route, "metadata", None), "id", None)
                            for dead_route in redundant_routes
                        }
                    ]

                    try:
                        rstub.Create(
                            route_service_pb2.CreateRouteRequest(
                                metadata=metadata_pb2.ResourceMetadata(
                                    parent_id=route_table_id,
                                    name=f"vpngw-{pfx.replace('/', '-')}"[:63],
                                ),
                                spec=route_pb2.RouteSpec(
                                    destination=route_pb2.DestinationMatch(cidr=pfx),
                                    next_hop=route_pb2.NextHop(
                                        allocation=route_pb2.AllocationNextHop(id=alloc_id)
                                    ),
                                ),
                            )
                        )
                        print(
                            f"[green]Added summarized route {pfx} -> allocation {alloc_id} "
                            f"on {route_table_id} after pruning redundant specifics[/green]"
                        )
                    except Exception as retry_err:
                        print(
                            f"[yellow]Failed to add summarized route {pfx} on {route_table_id} "
                            f"after pruning redundant specifics: {retry_err}[/yellow]"
                        )
                else:
                    print(f"[yellow]Failed to add route {pfx} on {route_table_id}: {e}[/yellow]")

            try:
                existing_routes = self._list_route_table_routes(
                    rstub,
                    route_service_pb2,
                    route_table_id=route_table_id,
                )
            except Exception:
                existing_routes = []

        if summarize:
            try:
                existing_routes = self._list_route_table_routes(
                    rstub,
                    route_service_pb2,
                    route_table_id=route_table_id,
                )
            except Exception as e:
                print(
                    f"[yellow]Failed to refresh routes on {route_table_id} before summary "
                    f"reconciliation: {e}[/yellow]"
                )
                existing_routes = []
            effective_prefix_targets = self._installed_prefix_targets(
                existing_routes,
                prefix_targets,
            )
            redundant_routes = self._find_redundant_managed_routes(
                existing_routes,
                effective_prefix_targets,
            )
            if redundant_routes:
                print(
                    f"[cyan]Pruning {len(redundant_routes)} redundant vpngw-managed "
                    f"route(s) from {route_table_id} after summary reconciliation[/cyan]"
                )
                self._delete_routes(
                    rstub,
                    route_service_pb2,
                    redundant_routes,
                    route_table_id=route_table_id,
                )
        else:
            try:
                existing_routes = self._list_route_table_routes(
                    rstub,
                    route_service_pb2,
                    route_table_id=route_table_id,
                )
            except Exception as e:
                print(
                    f"[yellow]Failed to refresh routes on {route_table_id} before exact-route "
                    f"reconciliation: {e}[/yellow]"
                )
                existing_routes = []

            effective_prefix_targets = self._installed_prefix_targets(
                existing_routes,
                prefix_targets,
            )
            redundant_covering_routes = self._find_redundant_managed_covering_routes(
                existing_routes,
                prefix_targets,
                effective_prefix_targets,
            )
            if redundant_covering_routes:
                print(
                    f"[cyan]Pruning {len(redundant_covering_routes)} broader vpngw-managed "
                    f"route(s) from {route_table_id} after exact-route reconciliation[/cyan]"
                )
                self._delete_routes(
                    rstub,
                    route_service_pb2,
                    redundant_covering_routes,
                    route_table_id=route_table_id,
                )

        try:
            return self._list_route_table_routes(
                rstub,
                route_service_pb2,
                route_table_id=route_table_id,
            )
        except Exception:
            return []

    def _delete_routes(
        self,
        rstub,
        route_service_pb2,
        routes_to_delete,
        *,
        route_table_id: str,
    ) -> int:
        deleted = 0
        for route in routes_to_delete:
            route_id = getattr(getattr(route, "metadata", None), "id", None)
            if not route_id:
                continue

            dest_network = self._route_destination_network(route)
            dest_label = str(dest_network) if dest_network else "unknown"
            try:
                rstub.Delete(route_service_pb2.DeleteRouteRequest(id=route_id))
                print(
                    f"[green]Deleted redundant managed route {dest_label} from {route_table_id}[/green]"
                )
                deleted += 1
            except Exception as e:
                print(
                    f"[yellow]Failed to delete redundant managed route {dest_label} "
                    f"from {route_table_id}: {e}[/yellow]"
                )
        return deleted

    @staticmethod
    def _bgp_advertisement_state(
        expected_by_peer: dict[str, set[str]],
        observed_peers: set[str],
        observed_prefixes_by_peer: dict[str, set[str]],
    ) -> BGPAdvertisementState:
        if observed_peers != set(expected_by_peer):
            return BGPAdvertisementState.DRIFT

        for peer_ip, expected_prefixes in expected_by_peer.items():
            observed_prefixes = observed_prefixes_by_peer.get(peer_ip)
            if observed_prefixes is None:
                return BGPAdvertisementState.UNKNOWN
            if observed_prefixes != expected_prefixes:
                return BGPAdvertisementState.DRIFT

        return BGPAdvertisementState.MATCH

    @staticmethod
    def _advertised_bgp_prefixes(adv_data: dict) -> set[str] | None:
        total = adv_data.get("totalPrefixCounter")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            return None
        advertised_routes = adv_data.get("advertisedRoutes")
        if advertised_routes is None and total == 0:
            advertised_routes = {}
        if not isinstance(advertised_routes, dict):
            return None
        normalized: set[str] = set()
        for prefix in advertised_routes:
            network = RouteManager._parse_ipv4_network(str(prefix))
            if network is None:
                return None
            normalized.add(str(network))
        return normalized if len(normalized) == total else None

    def _collect_observed_bgp_advertisements(
        self,
        external_ip: str,
        local_cfg: dict,
    ) -> tuple[set[str], dict[str, set[str]]] | None:
        bgp_summary = self._query_bgp_summary(external_ip, local_cfg)
        if not bgp_summary:
            return None

        ipv4_unicast = bgp_summary.get("ipv4Unicast")
        if not isinstance(ipv4_unicast, dict):
            return None
        peers = ipv4_unicast.get("peers")
        if not isinstance(peers, dict):
            return None
        if any(not isinstance(peer_info, dict) for peer_info in peers.values()):
            return None
        observed_peers = set(peers)
        observed_prefixes_by_peer: dict[str, set[str]] = {}

        for peer_ip, peer_info in peers.items():
            if peer_info.get("state") != "Established":
                continue

            adv_data = self._query_bgp_advertised_routes(external_ip, peer_ip, local_cfg)
            if adv_data is None:
                continue

            advertised_prefixes = self._advertised_bgp_prefixes(adv_data)
            if advertised_prefixes is not None:
                observed_prefixes_by_peer[peer_ip] = advertised_prefixes

        return observed_peers, observed_prefixes_by_peer

    def require_agent_capabilities(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
    ) -> None:
        """Prove every repair target supports the private installed-agent contract."""

        required = {FORCE_RECONCILE_CAPABILITY}
        if plan.vm_ha is not None:
            required.add(VM_HA_AUTHORITY_FORCE_RECONCILE_CAPABILITY)

        target_indices = self._bgp_instance_indices(local_cfg)
        if not target_indices:
            return

        observed: dict[str, frozenset[str]] = {}
        for inst_cfg in plan.iter_instance_configs():
            if inst_cfg.instance_index not in target_indices:
                continue
            cached = self._agent_capabilities_by_host.get(inst_cfg.hostname)
            if cached is not None and required.issubset(cached):
                observed[inst_cfg.hostname] = cached
                continue
            if not inst_cfg.external_ip:
                raise RouteManagementError(
                    f"Cannot verify installed-agent capabilities on {inst_cfg.hostname}: "
                    "the gateway has no external management IP."
                )

            result = self._run_ssh(
                local_cfg,
                inst_cfg.external_ip,
                (
                    "sudo /usr/bin/python3 -m nebius_vpngw.agent.main "
                    "--agent-capabilities"
                ),
                timeout=15,
            )
            if result.returncode != 0:
                raise RouteManagementError(
                    f"Gateway {inst_cfg.hostname} does not expose the required "
                    "installed-agent capability contract. Run 'nebius-vpngw apply' "
                    "with this CLI version, verify the deployment, and retry."
                )
            try:
                payload = json.loads(result.stdout)
            except (TypeError, json.JSONDecodeError) as error:
                raise RouteManagementError(
                    f"Gateway {inst_cfg.hostname} returned malformed installed-agent "
                    "capability evidence. Run 'nebius-vpngw apply' and retry."
                ) from error
            features = payload.get("features") if isinstance(payload, dict) else None
            if (
                not isinstance(payload, dict)
                or payload.get("schema") != AGENT_CAPABILITIES_SCHEMA
                or not isinstance(features, list)
                or any(not isinstance(feature, str) for feature in features)
            ):
                raise RouteManagementError(
                    f"Gateway {inst_cfg.hostname} returned an unsupported installed-agent "
                    "capability document. Run 'nebius-vpngw apply' and retry."
                )
            feature_set = frozenset(features)
            missing = sorted(required - feature_set)
            if missing:
                raise RouteManagementError(
                    f"Gateway {inst_cfg.hostname} is missing required installed-agent "
                    f"capabilities: {', '.join(missing)}. Run 'nebius-vpngw apply' "
                    "and retry."
                )
            observed[inst_cfg.hostname] = feature_set

        self._agent_capabilities_by_host.update(observed)

    def _force_reconcile_runtime_config(
        self,
        inst_cfg,
        local_cfg: dict,
        *,
        vm_ha_authority: VMHAAdvertisementAuthority | None = None,
    ) -> bool:
        reconcile_argv = [
            "sudo",
            "/usr/bin/python3",
            "-m",
            "nebius_vpngw.agent.main",
            "--force-reconcile",
        ]
        if vm_ha_authority is not None:
            target_epoch = vm_ha_authority.ownership_epoch_for(inst_cfg.hostname)
            if target_epoch is None:
                print(
                    f"[yellow]Failed to force-reconcile nebius-vpngw-agent on "
                    f"{inst_cfg.hostname}: current host ownership epoch is unavailable[/yellow]"
                )
                return False
            reconcile_argv.extend(
                [
                    "--expected-vm-ha-owner",
                    vm_ha_authority.owner_node_id,
                    "--expected-vm-ha-generation",
                    vm_ha_authority.generation_id,
                    "--expected-vm-ha-epoch",
                    target_epoch,
                    "--expected-vm-ha-allocation",
                    vm_ha_authority.allocation_id,
                ]
            )
        reconcile_cmd = shlex.join(reconcile_argv)
        reconcile_result = self._run_ssh(
            local_cfg,
            inst_cfg.external_ip,
            reconcile_cmd,
            timeout=30,
        )
        if reconcile_result.returncode != 0:
            print(
                f"[yellow]Failed to force-reconcile nebius-vpngw-agent on {inst_cfg.hostname}: "
                f"{reconcile_result.stderr.strip() or reconcile_result.stdout.strip()}[/yellow]"
            )
            return False

        return True

    def ensure_bgp_advertisements_current(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
        *,
        vm_ha_lifecycle_guard: t.Callable[[], bool] | None = None,
    ) -> None:
        import time

        lifecycle_stable = self._vm_ha_lifecycle_is_stable(plan, vm_ha_lifecycle_guard)
        authority = (
            self._vm_ha_advertisement_authority(plan, local_cfg)
            if plan.vm_ha is not None and lifecycle_stable
            else None
        )
        expected_by_host = self._expected_advertised_prefixes(
            plan,
            local_cfg,
            vm_ha_owner_hostname=(
                authority.owner_hostname if authority is not None else None
            ),
        )
        if expected_by_host is None:
            raise RouteManagementError(
                "VM-HA BGP advertisements are UNKNOWN because exact owner and "
                "generation authority could not be established; no repair was attempted."
            )
        if not expected_by_host:
            return

        self.require_agent_capabilities(plan, local_cfg)

        print("[bold]Checking live BGP advertisements against current YAML...[/bold]")

        for inst_cfg in plan.iter_instance_configs():
            if not inst_cfg.external_ip:
                continue
            if inst_cfg.hostname not in expected_by_host:
                continue

            expected_by_peer = expected_by_host[inst_cfg.hostname]
            print(
                f"[cyan]  • Inspecting gateway {inst_cfg.hostname} ({inst_cfg.external_ip})...[/cyan]"
            )
            print("[dim]    Step 1/3: Querying current BGP advertisements[/dim]")
            observed_state = self._collect_observed_bgp_advertisements(
                inst_cfg.external_ip,
                local_cfg,
            )
            if observed_state is None:
                raise RouteManagementError(
                    f"Could not query live BGP advertisements from {inst_cfg.hostname}; "
                    "no repair was attempted."
                )

            observed_peers, observed_prefixes_by_peer = observed_state
            audit_state = self._bgp_advertisement_state(
                expected_by_peer,
                observed_peers,
                observed_prefixes_by_peer,
            )
            if audit_state is BGPAdvertisementState.MATCH:
                print(
                    f"[green]    ✓ Live BGP advertisements on {inst_cfg.hostname} already match the current YAML.[/green]"
                )
                continue
            if audit_state is BGPAdvertisementState.UNKNOWN:
                raise RouteManagementError(
                    f"Live BGP advertisements on {inst_cfg.hostname} are UNKNOWN; "
                    "no repair was attempted."
                )

            expected_prefixes = sorted(
                {prefix for prefixes in expected_by_peer.values() for prefix in prefixes}
            )
            print(
                f"[yellow]    Detected stale BGP advertisement state on {inst_cfg.hostname}.[/yellow]"
            )
            if expected_prefixes:
                print(
                    "[dim]    Expected advertised prefixes from current YAML: "
                    f"{', '.join(expected_prefixes)}[/dim]"
                )

            if plan.vm_ha is not None and (
                authority is None
                or not self._vm_ha_lifecycle_is_stable(plan, vm_ha_lifecycle_guard)
                or self._vm_ha_advertisement_authority(plan, local_cfg) != authority
            ):
                raise RouteManagementError(
                    "VM-HA authority changed before repair; no repair was attempted."
                )
            print(
                "[dim]    Step 2/3: Force-reconciling the installed config under "
                "current authority[/dim]"
            )
            reconciled = (
                self._force_reconcile_runtime_config(inst_cfg, local_cfg)
                if authority is None
                else self._force_reconcile_runtime_config(
                    inst_cfg,
                    local_cfg,
                    vm_ha_authority=authority,
                )
            )
            if not reconciled:
                raise RouteManagementError(
                    f"Failed to force-reconcile the installed configuration on "
                    f"{inst_cfg.hostname}."
                )

            print("[dim]    Step 3/3: Re-checking live BGP advertisements[/dim]")
            time.sleep(3)
            refreshed_state = self._collect_observed_bgp_advertisements(
                inst_cfg.external_ip,
                local_cfg,
            )
            if refreshed_state is None:
                raise RouteManagementError(
                    f"Reconciled {inst_cfg.hostname}, but could not verify live BGP "
                    "advertisements."
                )

            if plan.vm_ha is not None and (
                authority is None
                or not self._vm_ha_lifecycle_is_stable(plan, vm_ha_lifecycle_guard)
                or self._vm_ha_advertisement_authority(plan, local_cfg) != authority
            ):
                raise RouteManagementError(
                    "VM-HA authority changed during repair; refreshed state is UNKNOWN."
                )
            refreshed_peers, refreshed_prefixes_by_peer = refreshed_state
            refreshed_audit = self._bgp_advertisement_state(
                expected_by_peer,
                refreshed_peers,
                refreshed_prefixes_by_peer,
            )
            if refreshed_audit is BGPAdvertisementState.DRIFT:
                raise RouteManagementError(
                    f"{inst_cfg.hostname} still advertises prefixes that do not match "
                    "the installed configuration. Run 'nebius-vpngw apply' and retry."
                )
            if refreshed_audit is BGPAdvertisementState.UNKNOWN:
                raise RouteManagementError(
                    f"Reconciled {inst_cfg.hostname}, but exact BGP advertisement "
                    "evidence is still UNKNOWN."
                )

            print(
                f"[green]    ✓ Refreshed live BGP advertisements on {inst_cfg.hostname} to match the current YAML.[/green]"
            )

    def audit_bgp_advertisements(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
        *,
        vm_ha_lifecycle_guard: t.Callable[[], bool] | None = None,
    ) -> dict[str, BGPAdvertisementState]:
        """Observe live BGP exports without writing config or reloading a service."""

        lifecycle_stable = self._vm_ha_lifecycle_is_stable(plan, vm_ha_lifecycle_guard)
        authority = (
            self._vm_ha_advertisement_authority(plan, local_cfg)
            if plan.vm_ha is not None and lifecycle_stable
            else None
        )
        expected_by_host = self._expected_advertised_prefixes(
            plan,
            local_cfg,
            vm_ha_owner_hostname=(
                authority.owner_hostname if authority is not None else None
            ),
        )
        results: dict[str, BGPAdvertisementState] = {}
        print("[bold]BGP Advertisement Audit (read-only)[/bold]")
        for inst_cfg in plan.iter_instance_configs():
            if not inst_cfg.external_ip:
                continue
            if expected_by_host is None:
                state = BGPAdvertisementState.UNKNOWN
            elif inst_cfg.hostname not in expected_by_host:
                continue
            else:
                observed = self._collect_observed_bgp_advertisements(
                    inst_cfg.external_ip,
                    local_cfg,
                )
                state = (
                    BGPAdvertisementState.UNKNOWN
                    if observed is None
                    else self._bgp_advertisement_state(
                        expected_by_host.get(inst_cfg.hostname, {}),
                        observed[0],
                        observed[1],
                    )
                )
            results[inst_cfg.hostname] = state

        authority_changed = plan.vm_ha is not None and (
            authority is None
            or not self._vm_ha_lifecycle_is_stable(plan, vm_ha_lifecycle_guard)
            or self._vm_ha_advertisement_authority(plan, local_cfg) != authority
        )
        if authority_changed:
            results = {
                hostname: BGPAdvertisementState.UNKNOWN for hostname in results
            }

        for hostname, state in results.items():
            style = {
                BGPAdvertisementState.MATCH: "green",
                BGPAdvertisementState.DRIFT: "red",
                BGPAdvertisementState.UNKNOWN: "yellow",
            }[state]
            print(f"[{style}]  {hostname}: {state.value}[/{style}]")
        if expected_by_host is None:
            print(
                "[yellow]  Exact VM-HA owner/generation authority is unavailable; "
                "UNKNOWN is not treated as drift and no repair was attempted.[/yellow]"
            )
        elif authority_changed:
            print(
                "[yellow]  VM-HA authority changed during observation; mixed-time "
                "evidence was downgraded to UNKNOWN.[/yellow]"
            )
        return results

    def _resolve_target_network_id(self, channel, local_cfg: dict) -> str | None:
        from nebius.api.nebius.vpc.v1 import (  # type: ignore[attr-defined]
            network_service_pb2,
            network_service_pb2_grpc,
            subnet_service_pb2,
            subnet_service_pb2_grpc,
        )

        gateway_group = local_cfg.get("gateway_group", {}) or {}
        explicit_network_id = str(gateway_group.get("network_id") or "").strip()
        if explicit_network_id:
            return explicit_network_id

        subnet_name = self._gateway_subnet_name(local_cfg)
        sstub = subnet_service_pb2_grpc.SubnetServiceStub(channel)
        try:
            subnet_obj = sstub.GetByName(
                subnet_service_pb2.GetSubnetByNameRequest(
                    parent_id=self.project_id or "",
                    name=subnet_name,
                )
            )
            subnet_spec = getattr(subnet_obj, "spec", None)
            subnet_network_id = getattr(subnet_spec, "network_id", None)
            if subnet_network_id:
                return subnet_network_id
        except Exception:
            pass

        nstub = network_service_pb2_grpc.NetworkServiceStub(channel)
        try:
            network_obj = nstub.GetByName(
                network_service_pb2.GetNetworkByNameRequest(
                    parent_id=self.project_id or "",
                    name="default-network",
                )
            )
            network_id = getattr(network_obj, "id", None) or getattr(
                getattr(network_obj, "metadata", None),
                "id",
                None,
            )
            if network_id:
                return network_id
        except Exception:
            pass

        try:
            networks = nstub.List(
                network_service_pb2.ListNetworksRequest(parent_id=self.project_id or "")
            ).items
        except Exception:
            return None

        if len(networks) == 1:
            network_obj = networks[0]
            return getattr(network_obj, "id", None) or getattr(
                getattr(network_obj, "metadata", None),
                "id",
                None,
            )

        return None

    def _select_local_prefix_subnets(
        self,
        subnets,
        gateway_prefixes: list[ipaddress.IPv4Network],
        *,
        target_network_id: str,
        gateway_subnet_name: str,
    ) -> tuple[list[tuple[object, list[ipaddress.IPv4Network]]], list[str]]:
        selected: list[tuple[object, list[ipaddress.IPv4Network]]] = []
        diagnostics: list[str] = []
        explicit_cidrs_by_subnet_name: dict[str, list[ipaddress.IPv4Network]] = {}

        for subnet_obj in subnets:
            subnet_spec = getattr(subnet_obj, "spec", None)
            subnet_network_id = getattr(subnet_spec, "network_id", None)
            if subnet_network_id != target_network_id:
                continue

            subnet_name = getattr(getattr(subnet_obj, "metadata", None), "name", None) or ""
            explicit_cidrs_by_subnet_name[subnet_name] = self._extract_explicit_subnet_cidrs(
                subnet_obj
            )

        for subnet_obj in subnets:
            subnet_spec = getattr(subnet_obj, "spec", None)
            subnet_network_id = getattr(subnet_spec, "network_id", None)
            if subnet_network_id != target_network_id:
                continue

            subnet_name = getattr(getattr(subnet_obj, "metadata", None), "name", None) or ""
            if subnet_name == gateway_subnet_name:
                continue

            other_explicit_cidrs = [
                cidr
                for other_subnet_name, cidrs in explicit_cidrs_by_subnet_name.items()
                if other_subnet_name != subnet_name
                for cidr in cidrs
            ]
            effective_cidrs, sanitized_inherited_status = self._selection_cidrs_for_subnet(
                subnet_obj,
                other_explicit_cidrs=other_explicit_cidrs,
            )
            matches_gateway_prefixes = any(
                effective_cidr.overlaps(prefix)
                for effective_cidr in effective_cidrs
                for prefix in gateway_prefixes
            )
            if not matches_gateway_prefixes:
                if self._subnet_uses_network_pools(subnet_obj) and sanitized_inherited_status:
                    raw_status_cidrs = self._extract_status_subnet_cidrs(subnet_obj)
                    raw_status_overlaps = any(
                        raw_status_cidr.overlaps(prefix)
                        for raw_status_cidr in raw_status_cidrs
                        for prefix in gateway_prefixes
                    )
                    if raw_status_overlaps:
                        diagnostics.append(
                            f"[dim]Ignoring inherited subnet {subnet_name} for gateway.local_prefixes "
                            "matching because Nebius status CIDRs overlap explicit CIDRs owned by "
                            "other subnets.[/dim]"
                        )
                continue

            selected.append((subnet_obj, effective_cidrs))
            if self._subnet_uses_network_pools(subnet_obj):
                cidr_labels = ", ".join(str(effective_cidr) for effective_cidr in effective_cidrs)
                if sanitized_inherited_status:
                    diagnostics.append(
                        f"[dim]Subnet {subnet_name} inherits parent network pools "
                        "(use_network_pools=true); sanitized status CIDRs to exclude explicit "
                        f"CIDRs owned by other subnets before matching: {cidr_labels}[/dim]"
                    )
                else:
                    diagnostics.append(
                        f"[dim]Subnet {subnet_name} inherits parent network pools "
                        f"(use_network_pools=true); matching via status CIDRs: {cidr_labels}[/dim]"
                    )

        return selected, diagnostics

    def resolve_vm_ha_route_targets(
        self,
        subnets: t.Iterable[object],
        local_prefixes: t.Iterable[str],
        *,
        project_id: str,
        target_network_id: str,
        gateway_subnet_name: str,
    ) -> tuple[VMHARouteTarget, ...]:
        """Resolve the exact immutable workload route-table target set for VM-HA."""

        prefixes = [
            network
            for prefix in local_prefixes
            if (network := self._parse_ipv4_network(str(prefix))) is not None
        ]
        if not project_id or not target_network_id or not prefixes:
            raise ValueError("VM-HA route target selection requires project, network, and prefixes")
        selected, _ = self._select_local_prefix_subnets(
            tuple(subnets),
            prefixes,
            target_network_id=target_network_id,
            gateway_subnet_name=gateway_subnet_name,
        )
        if not selected:
            raise ValueError("VM-HA route target selection matched no workload subnet")
        targets: list[VMHARouteTarget] = []
        for subnet, _ in selected:
            subnet_id = self._metadata_id(subnet)
            network_id = self._subnet_network_id(subnet)
            route_table_id = str(getattr(self._subnet_route_table(subnet), "id", "") or "")
            if not subnet_id or not route_table_id:
                raise ValueError("VM-HA workload subnet has no exact attached route-table ID")
            if network_id != target_network_id:
                raise ValueError("VM-HA route target crossed the selected network")
            targets.append(
                VMHARouteTarget(
                    project_id=project_id,
                    network_id=network_id,
                    workload_subnet_id=subnet_id,
                    route_table_id=route_table_id,
                )
            )
        canonical = tuple(
            sorted(
                targets,
                key=lambda target: (
                    target.project_id,
                    target.network_id,
                    target.workload_subnet_id,
                    target.route_table_id,
                ),
            )
        )
        if len({target.workload_subnet_id for target in canonical}) != len(canonical) or len(
            {target.route_table_id for target in canonical}
        ) != len(canonical):
            raise ValueError("VM-HA route target selection is duplicate or ambiguous")
        return canonical

    def list_routes(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
        *,
        vm_ha_lifecycle_guard: t.Callable[[], bool] | None = None,
    ) -> None:
        """List route tables attached to subnets matching gateway.local_prefixes."""
        from nebius.api.nebius.vpc.v1 import (  # type: ignore[attr-defined]
            AllocationServiceClient,
            GetNetworkByNameRequest,
            GetSubnetByNameRequest,
            ListAllocationsRequest,
            ListNetworksRequest,
            ListRoutesRequest,
            ListSubnetsRequest,
            NetworkServiceClient,
            RouteServiceClient,
            SubnetServiceClient,
        )
        from rich.console import Console
        from rich.table import Table

        gateway_prefixes = self._parse_ipv4_networks(
            local_cfg.get("gateway", {}).get("local_prefixes") or []
        )
        if not gateway_prefixes:
            print("[yellow]No gateway.local_prefixes; nothing to list.[/yellow]")
            return

        sdk = self._create_read_sdk()
        console = Console()
        try:
            allocation_client = AllocationServiceClient(sdk)
            subnet_client = SubnetServiceClient(sdk)
            route_client = RouteServiceClient(sdk)

            alloc_to_ip = self._list_allocations_with_sdk(
                allocation_client,
                ListAllocationsRequest,
            )
            gateway_subnet_name = self._gateway_subnet_name(local_cfg)
            target_network_id = self._resolve_target_network_id_with_sdk(
                sdk,
                local_cfg,
                subnet_client=subnet_client,
                get_subnet_by_name_request=GetSubnetByNameRequest,
                network_client_type=NetworkServiceClient,
                get_network_by_name_request=GetNetworkByNameRequest,
                list_networks_request=ListNetworksRequest,
            )
            if not target_network_id:
                print(
                    "[yellow]Could not resolve the target network for route listing. "
                    "Set gateway_group.network_id explicitly.[/yellow]"
                )
                return

            subnets = self._list_sdk_items(
                subnet_client,
                ListSubnetsRequest,
                parent_id=self.project_id or "",
            )
            selected_subnets, subnet_selection_diagnostics = self._select_local_prefix_subnets(
                subnets,
                gateway_prefixes,
                target_network_id=target_network_id,
                gateway_subnet_name=gateway_subnet_name,
            )

            for diagnostic in subnet_selection_diagnostics:
                print(diagnostic)

            if not selected_subnets:
                print(
                    "[yellow]No workload subnets matched gateway.local_prefixes "
                    f"in network {target_network_id}.[/yellow]"
                )

            for subnet, selected_cidrs in selected_subnets:
                subnet_name = self._metadata_name(subnet)
                subnet_cidrs = [str(network) for network in selected_cidrs]

                route_table = self._subnet_route_table(subnet)
                route_table_id = str(getattr(route_table, "id", "") or "")
                route_table_default = bool(getattr(route_table, "default", False))

                print(f"\n[bold cyan]Subnet: {subnet_name}[/bold cyan] ({', '.join(subnet_cidrs)})")

                if not route_table_id:
                    print("[yellow]  No route table attached[/yellow]")
                    continue

                print(
                    f"[dim]  Route Table ID: {route_table_id} (default={route_table_default})[/dim]"
                )

                routes = self._list_sdk_items(
                    route_client,
                    ListRoutesRequest,
                    parent_id=route_table_id,
                )

                if not routes:
                    print("[dim]  No routes in route table[/dim]")
                    continue

                table = Table(show_header=True, header_style="bold")
                table.add_column("Destination", style="cyan")
                table.add_column("Next Hop", style="green")

                for route in routes:
                    route_spec = getattr(route, "spec", None)
                    destination = getattr(route_spec, "destination", None)
                    next_hop = getattr(route_spec, "next_hop", None)
                    destination_cidr = str(getattr(destination, "cidr", "") or "")
                    allocation = getattr(next_hop, "allocation", None)
                    allocation_id = str(getattr(allocation, "id", "") or "")
                    next_hop_label = "-"
                    if allocation_id:
                        next_hop_label = alloc_to_ip.get(allocation_id, allocation_id)
                    elif getattr(next_hop, "default_egress_gateway", False):
                        next_hop_label = "default-egress"

                    table.add_row(destination_cidr, next_hop_label)

                console.print(table)
        finally:
            sdk.sync_close()

        # Add BGP advertised routes section
        self._list_bgp_advertised_routes(
            plan,
            local_cfg,
            console,
            vm_ha_lifecycle_guard=vm_ha_lifecycle_guard,
        )

    def _list_bgp_advertised_routes(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
        console,
        *,
        vm_ha_lifecycle_guard: t.Callable[[], bool] | None = None,
    ) -> None:
        """List BGP routes being advertised from gateway to peer routers.

        Shows what routes are announced to remote sites, organized by connection and tunnel.
        """
        from rich.table import Table

        print(
            "\n[bold magenta]BGP Routes Advertised to Peer Routers (Local → Remote)[/bold magenta]"
        )

        # Get routing mode and connections
        defaults_mode = (
            self._normalize_value(
                (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode")
            )
            or "bgp"
        )
        connections = local_cfg.get("connections", [])

        # Check if BGP is enabled for any connection
        has_bgp = any(
            self._normalize_value(conn.get("routing_mode") or defaults_mode) == "bgp"
            for conn in connections
        )

        if not has_bgp:
            print("[dim]No BGP connections configured - routes are static[/dim]")
            return

        self.audit_bgp_advertisements(
            plan,
            local_cfg,
            vm_ha_lifecycle_guard=vm_ha_lifecycle_guard,
        )

        # Query each gateway VM
        bgp_instance_indices = self._bgp_instance_indices(local_cfg)
        for inst_cfg in plan.iter_instance_configs():
            if inst_cfg.instance_index not in bgp_instance_indices:
                continue
            hostname = inst_cfg.hostname
            external_ip = inst_cfg.external_ip

            if not external_ip:
                print(f"[yellow]Skipping {hostname}: no external IP[/yellow]")
                continue

            print(f"\n[bold cyan]Gateway VM: {hostname} ({external_ip})[/bold cyan]")

            # Query BGP summary to get peer list
            try:
                bgp_summary = self._query_bgp_summary(external_ip, local_cfg)
                if bgp_summary is None:
                    print(f"[yellow]Failed to query BGP summary from {hostname}[/yellow]")
                    continue

                ipv4_peers = bgp_summary.get("ipv4Unicast", {}).get("peers", {})

                if not ipv4_peers:
                    print("[dim]No BGP peers configured or active[/dim]")
                    continue

                # For each peer, query advertised routes
                for peer_ip, peer_info in sorted(ipv4_peers.items()):
                    peer_state = peer_info.get("state", "Unknown")
                    peer_asn = peer_info.get("remoteAs", "?")

                    # Find connection and tunnel name for this peer
                    conn_name, tunnel_name, inner_local_ip, ha_role = (
                        self._find_connection_for_peer(peer_ip, connections, inst_cfg)
                    )
                    role_label = ha_role or "unknown"

                    if peer_state != "Established":
                        print(
                            f"\n[bold]Connection: {conn_name} | Tunnel: {tunnel_name} ({role_label})[/bold]"
                        )
                        print(
                            f"[yellow]  BGP Peer {peer_ip} (ASN {peer_asn}): {peer_state}[/yellow]"
                        )
                        continue

                    # Query advertised routes to this peer
                    adv_data = self._query_bgp_advertised_routes(external_ip, peer_ip, local_cfg)
                    if adv_data is None:
                        print(
                            f"\n[bold]Connection: {conn_name} | Tunnel: {tunnel_name} ({role_label})[/bold]"
                        )
                        print(f"[yellow]  Failed to query advertised routes to {peer_ip}[/yellow]")
                        continue

                    advertised_routes = adv_data.get("advertisedRoutes", {})

                    if not advertised_routes:
                        print(
                            f"\n[bold]Connection: {conn_name} | Tunnel: {tunnel_name} ({role_label})[/bold]"
                        )
                        print(
                            f"[dim]  BGP Peer {peer_ip} (ASN {peer_asn}): No routes advertised[/dim]"
                        )
                        continue

                    # Build table for this peer
                    print(
                        f"\n[bold]Connection: {conn_name} | Tunnel: {tunnel_name} ({role_label})[/bold]"
                    )

                    # Get local ASN from adv_data
                    local_asn = adv_data.get("localAS", "")

                    table = Table(title=f"→ Peer {peer_ip} (ASN {peer_asn})")
                    table.add_column("Prefix", style="cyan")
                    table.add_column("Next-Hop", style="blue")
                    table.add_column("AS Path", style="yellow")
                    table.add_column("Origin", style="green")

                    for prefix, route_info in sorted(advertised_routes.items()):
                        next_hop = route_info.get("nextHop", "-")
                        # Replace 0.0.0.0 with actual XFRM interface IP
                        if next_hop == "0.0.0.0":
                            next_hop = inner_local_ip

                        # AS path is empty for locally originated routes
                        # When sent on wire, FRR automatically prepends local ASN
                        as_path = route_info.get("path", "")
                        if not as_path and local_asn:
                            as_path = str(local_asn)  # Show local ASN for clarity
                        elif not as_path:
                            as_path = "local"

                        # For user clarity, show "BGP" for locally originated routes (IGP origin code)
                        # since these are routes injected via BGP 'network' statement
                        origin = route_info.get("origin", "?")
                        if origin == "IGP":
                            origin = "BGP"

                        table.add_row(prefix, next_hop, as_path, origin)

                    console.print(table)

            except Exception as e:
                print(f"[yellow]Error querying BGP data: {e}[/yellow]")

    def _find_connection_for_peer(self, peer_ip: str, connections: list[dict], inst_cfg) -> tuple:
        """Find connection and tunnel name for a BGP peer IP.

        Returns (connection_name, tunnel_name, inner_local_ip, ha_role) tuple.
        """
        inst_index = getattr(inst_cfg, "instance_index", None)

        # Peer IP is the tunnel's inner_remote_ip (APIPA address on the tunnel interface)
        for conn in connections:
            conn_name = conn.get("name", "unnamed")
            tunnels = conn.get("tunnels", [])

            for tunnel in tunnels:
                if self._normalize_value(tunnel.get("ha_role") or "active") == "disable":
                    continue
                if inst_index is not None:
                    try:
                        tunnel_inst_index = int(tunnel.get("gateway_instance_index", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    if tunnel_inst_index != inst_index:
                        continue

                # Check inner_remote_ip (the remote peer's BGP IP on the tunnel)
                inner_remote = tunnel.get("inner_remote_ip", "")

                # Also check bgp.remote_ip if present (alternative config format)
                bgp_cfg = tunnel.get("bgp", {})
                bgp_remote = bgp_cfg.get("remote_ip", "")

                if inner_remote == peer_ip or bgp_remote == peer_ip:
                    tunnel_name = tunnel.get("name") or "unnamed-tunnel"
                    inner_local_ip = tunnel.get("inner_local_ip", "0.0.0.0")
                    ha_role = self._normalize_value(tunnel.get("ha_role") or "active") or "active"
                    return (conn_name, tunnel_name, inner_local_ip, ha_role)

        # Fallback if not found
        return ("unknown", "unknown", "0.0.0.0", "unknown")

    def add_routes(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
        *,
        summarize: bool = False,
        swap_route_table: bool = False,
        rollback_dir: Path | None = None,
    ) -> None:
        """Run route management behind one typed public failure boundary."""

        try:
            self._add_routes(
                plan,
                local_cfg,
                summarize=summarize,
                swap_route_table=swap_route_table,
                rollback_dir=rollback_dir,
            )
        except RouteManagementError:
            raise
        except Exception as error:
            raise RouteManagementError(
                "VPC route management failed before its requested postcondition "
                "could be verified."
            ) from error

    def _add_routes(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
        *,
        summarize: bool = False,
        swap_route_table: bool = False,
        rollback_dir: Path | None = None,
    ) -> None:
        """Ensure routes for connection.remote_prefixes and assign custom route tables when needed."""
        try:
            channel = self._channel()
        except Exception as e:
            raise RouteManagementError(f"Failed to open the VPC SDK channel: {e}") from e

        # Create compute channel using same auth pattern as VPC channel
        try:
            import os

            import grpc  # type: ignore

            token = self.auth_token or os.environ.get("NEBIUS_IAM_TOKEN")
            if not token:
                raise ValueError("No authentication token available.")

            def auth_metadata_plugin(context, callback):
                callback([("authorization", f"Bearer {token}")], None)

            auth_creds = grpc.metadata_call_credentials(t.cast(t.Any, auth_metadata_plugin))
            ssl_creds = grpc.ssl_channel_credentials()
            composite_creds = grpc.composite_channel_credentials(ssl_creds, auth_creds)
            compute_channel = grpc.secure_channel("compute.api.nebius.cloud:443", composite_creds)
        except Exception:
            compute_channel = None

        from nebius.api.nebius.common.v1 import metadata_pb2  # type: ignore[attr-defined]
        from nebius.api.nebius.vpc.v1 import (  # type: ignore[attr-defined]
            route_pb2,
            route_service_pb2,
            route_service_pb2_grpc,
            route_table_pb2,
            route_table_service_pb2,
            route_table_service_pb2_grpc,
            subnet_pb2,
            subnet_service_pb2,
            subnet_service_pb2_grpc,
        )

        gateway_prefixes = self._parse_ipv4_networks(
            local_cfg.get("gateway", {}).get("local_prefixes") or []
        )
        if not gateway_prefixes:
            raise RouteManagementError(
                "No gateway.local_prefixes are configured; workload subnets cannot be selected."
            )

        allocations_by_index: dict[int, str] = {}
        if compute_channel:
            allocations_by_index = self._find_gateway_private_allocations_by_index(
                compute_channel, plan
            )
        if not allocations_by_index:
            raise RouteManagementError(
                "Could not resolve private gateway allocations; no routes were changed."
            )

        target_network_id = self._resolve_target_network_id(channel, local_cfg)
        if not target_network_id:
            raise RouteManagementError(
                "Could not resolve the target network for route updates. Set "
                "gateway_group.network_id explicitly."
            )

        network_pool_networks = self._get_network_private_pool_cidrs(
            channel,
            network_id=target_network_id,
        )

        try:
            prefix_targets = self._collect_remote_prefix_targets(
                plan, local_cfg, allocations_by_index
            )
        except ValueError as error:
            raise RouteManagementError(str(error)) from error

        if not prefix_targets:
            raise RouteManagementError(
                "No remote prefixes were found (BGP: no learned routes; static: "
                "no configured connection or tunnel prefixes)."
            )

        # Filter out local prefixes (don't create routes for our own VPC networks)
        local_networks = self._parse_ipv4_networks(
            local_cfg.get("gateway", {}).get("local_prefixes") or []
        )
        (
            prefix_targets,
            skipped_local_prefixes,
            skipped_network_pool_prefixes,
        ) = self._filter_prefix_targets(
            prefix_targets,
            local_networks=local_networks,
            network_pool_networks=network_pool_networks,
        )

        for prefix in skipped_local_prefixes:
            print(f"[dim]Skipping {prefix} (overlaps with local_prefixes)[/dim]")
        for prefix, pool_cidr in skipped_network_pool_prefixes:
            print(
                f"[dim]Skipping {prefix} (overlaps target network private pool {pool_cidr})[/dim]"
            )

        if not prefix_targets:
            print(
                "[yellow]No remote prefixes to add (all learned routes are local networks)[/yellow]"
            )
            return

        original_prefix_count = len(prefix_targets)
        if summarize:
            summarized_targets = self._summarize_prefix_targets(prefix_targets)
            if len(summarized_targets) < original_prefix_count:
                print(
                    f"[cyan]Summarized remote prefixes from {original_prefix_count} "
                    f"to {len(summarized_targets)} exact route(s)[/cyan]"
                )
            else:
                print("[dim]Summarization produced no exact route reduction[/dim]")
            prefix_targets = summarized_targets

        print(f"[cyan]Found {len(prefix_targets)} remote prefix(es) to add as VPC routes[/cyan]")

        sstub = subnet_service_pb2_grpc.SubnetServiceStub(channel)
        rtstub = route_table_service_pb2_grpc.RouteTableServiceStub(channel)
        rstub = route_service_pb2_grpc.RouteServiceStub(channel)
        gateway_subnet_name = self._gateway_subnet_name(local_cfg)

        subnets = sstub.List(
            subnet_service_pb2.ListSubnetsRequest(parent_id=self.project_id or "")
        ).items
        selected_subnets, subnet_selection_diagnostics = self._select_local_prefix_subnets(
            subnets,
            gateway_prefixes,
            target_network_id=target_network_id,
            gateway_subnet_name=gateway_subnet_name,
        )

        for diagnostic in subnet_selection_diagnostics:
            print(diagnostic)

        if not selected_subnets:
            raise RouteManagementError(
                "No workload subnets matched gateway.local_prefixes in network "
                f"{target_network_id}; no routes were changed."
            )

        completed_subnets: set[str] = set()

        for sn, _selected_cidrs in selected_subnets:
            subnet_name = self._metadata_name(sn)
            rt_info = self._subnet_route_table(sn)
            current_route_table_id = str(getattr(rt_info, "id", "") or "")
            current_route_table_default = bool(getattr(rt_info, "default", False))
            current_route_table_label = current_route_table_id or "default route table"
            subnet_network_id = self._subnet_network_id(sn) or target_network_id

            if swap_route_table:
                source_route_table_id = current_route_table_id
                if not source_route_table_id:
                    print(
                        f"[yellow]Cannot swap route table for subnet {subnet_name}: "
                        "the currently attached route table ID is unavailable, so a "
                        "safe rollback target cannot be generated.[/yellow]"
                    )
                    continue

                print(
                    f"[cyan]Subnet {subnet_name} will swap from route table "
                    f"{source_route_table_id} to a fresh custom table...[/cyan]"
                )

                swap_rt_name = self._swap_route_table_name(subnet_name)
                try:
                    op = rtstub.Create(
                        route_table_service_pb2.CreateRouteTableRequest(
                            metadata=metadata_pb2.ResourceMetadata(
                                name=swap_rt_name,
                                parent_id=self.project_id,
                            ),
                            spec=route_table_pb2.RouteTableSpec(network_id=subnet_network_id),
                        )
                    )
                    rt_id = op.resource_id or ""
                except Exception as e:
                    print(
                        f"[yellow]Failed to create swap route table for subnet {subnet_name}: {e}[/yellow]"
                    )
                    continue

                if not rt_id:
                    print(
                        f"[red]Route table create returned no resource_id for subnet {subnet_name}; skipping.[/red]"
                    )
                    continue

                existing_routes = []
                if source_route_table_id:
                    try:
                        existing_routes = self._list_route_table_routes(
                            rstub,
                            route_service_pb2,
                            route_table_id=source_route_table_id,
                        )
                    except Exception as e:
                        print(
                            f"[yellow]Failed to list routes on source route table "
                            f"{source_route_table_id} for subnet {subnet_name}: {e}[/yellow]"
                        )
                        continue

                routes_to_copy = self._copyable_routes(existing_routes)
                copied, copy_failures = self._copy_routes_to_route_table(
                    rstub,
                    route_service_pb2,
                    metadata_pb2,
                    destination_route_table_id=rt_id,
                    routes_to_copy=routes_to_copy,
                )
                if routes_to_copy:
                    print(
                        f"[cyan]Copied {copied}/{len(routes_to_copy)} non-vpngw route(s) "
                        f"from {current_route_table_label} to {rt_id}[/cyan]"
                    )
                else:
                    print(
                        f"[dim]No non-vpngw routes to copy from {current_route_table_label}[/dim]"
                    )

                if copy_failures:
                    print(
                        f"[yellow]Swap route table {rt_id} is incomplete; copy failures prevent cutover.[/yellow]"
                    )
                    for dest_label, error_text in copy_failures:
                        print(
                            f"[yellow]  Could not copy preserved route {dest_label}: {error_text}[/yellow]"
                        )
                    print(
                        f"[yellow]Leaving unattached swap route table {rt_id} for inspection. "
                        "Current subnet attachment is unchanged.[/yellow]"
                    )
                    continue

                existing_routes = self._reconcile_route_table(
                    rstub,
                    route_service_pb2,
                    route_pb2,
                    metadata_pb2,
                    route_table_id=rt_id,
                    prefix_targets=prefix_targets,
                    summarize=summarize,
                )

                preserved_signatures = {
                    signature
                    for route in routes_to_copy
                    if (signature := self._route_signature(route)) is not None
                }
                missing_preserved_routes = self._missing_route_signatures(
                    preserved_signatures,
                    existing_routes,
                )
                installed_prefix_targets = self._installed_prefix_targets(
                    existing_routes,
                    prefix_targets,
                )
                missing_prefix_targets = sorted(set(prefix_targets) - set(installed_prefix_targets))

                if missing_preserved_routes or missing_prefix_targets:
                    print(
                        f"[yellow]Swap route table {rt_id} failed validation; subnet "
                        f"{subnet_name} will stay on {current_route_table_label}.[/yellow]"
                    )
                    for destination, next_hop in missing_preserved_routes:
                        print(
                            f"[yellow]  Missing preserved route after copy: {destination} -> "
                            f"{next_hop}[/yellow]"
                        )
                    for prefix in missing_prefix_targets:
                        print(
                            f"[yellow]  Missing managed route after reconciliation: {prefix}[/yellow]"
                        )
                    print(
                        f"[yellow]Leaving unattached swap route table {rt_id} for inspection. "
                        "Current subnet attachment is unchanged.[/yellow]"
                    )
                    continue

                if rollback_dir is None:
                    rollback_dir = Path.cwd() / ".nebius-vpngw-rollbacks"
                rollback_path = self._write_swap_rollback_spec(
                    rollback_dir=rollback_dir,
                    subnet_obj=sn,
                    previous_route_table_id=current_route_table_id,
                )

                try:
                    self._attach_route_table_to_subnet(
                        sstub,
                        subnet_service_pb2,
                        metadata_pb2,
                        subnet_pb2,
                        subnet_obj=sn,
                        route_table_id=rt_id,
                    )
                except Exception as e:
                    print(
                        f"[yellow]Failed to attach swap route table {rt_id} to subnet "
                        f"{subnet_name}: {e}[/yellow]"
                    )
                    print(
                        f"[yellow]Rollback spec saved to {rollback_path}, but the subnet "
                        "attachment was not changed.[/yellow]"
                    )
                    continue

                print(
                    f"[green]Swapped subnet {subnet_name} from {current_route_table_label} "
                    f"to fresh route table {rt_id}[/green]"
                )
                print(f"[cyan]Rollback spec saved to {rollback_path}[/cyan]")
                print(
                    "[yellow]Rollback command:[/yellow] "
                    f"[bold]nebius vpc subnet update --file {shlex.quote(str(rollback_path))}[/bold]"
                )
                completed_subnets.add(subnet_name)
                continue

            if not current_route_table_default and current_route_table_id:
                print(
                    f"[cyan]Subnet {subnet_name} already uses custom route table {current_route_table_id}; "
                    "adding VPN routes...[/cyan]"
                )
                rt_id = current_route_table_id
            else:
                # Subnet uses default route table - need to create custom RT
                rt_name = self._route_table_name(subnet_name)

                # Check if route table already exists (idempotency)
                existing_rts = rtstub.List(
                    route_table_service_pb2.ListRouteTablesRequest(parent_id=self.project_id)
                ).items
                existing_rt = next(
                    (rt for rt in existing_rts if self._metadata_name(rt) == rt_name),
                    None,
                )

                if existing_rt:
                    rt_id = self._metadata_id(existing_rt)
                    print(
                        f"[green]Using existing route table {rt_id} ({rt_name}) for subnet {subnet_name}[/green]"
                    )
                    # Attach to subnet if not already attached
                    if current_route_table_id != rt_id:
                        try:
                            self._attach_route_table_to_subnet(
                                sstub,
                                subnet_service_pb2,
                                metadata_pb2,
                                subnet_pb2,
                                subnet_obj=sn,
                                route_table_id=rt_id,
                            )
                            print(
                                f"[green]Attached route table {rt_id} to subnet {subnet_name}[/green]"
                            )
                        except Exception as e:
                            print(
                                f"[yellow]Failed to attach route table to subnet {subnet_name}: {e}[/yellow]"
                            )
                            continue
                else:
                    # Create new route table and copy routes from default RT
                    print(f"[yellow]⚠ Subnet {subnet_name} uses default route table[/yellow]")
                    print(
                        f"[yellow]  Creating custom route table '{rt_name}' to add VPN routes[/yellow]"
                    )

                    # Get default route table ID to copy routes from
                    default_rt_id = current_route_table_id or None

                    try:
                        op = rtstub.Create(
                            route_table_service_pb2.CreateRouteTableRequest(
                                metadata=metadata_pb2.ResourceMetadata(
                                    name=rt_name,
                                    parent_id=self.project_id,
                                ),
                                spec=route_table_pb2.RouteTableSpec(network_id=subnet_network_id),
                            )
                        )
                        new_rt_id = op.resource_id or ""
                        if not new_rt_id:
                            print(
                                f"[red]Route table create returned no resource_id for subnet {subnet_name}; skipping.[/red]"
                            )
                            continue

                        # Copy existing routes from default route table
                        if default_rt_id:
                            try:
                                default_routes = rstub.List(
                                    route_service_pb2.ListRoutesRequest(parent_id=default_rt_id)
                                ).items

                                if default_routes:
                                    print(
                                        f"[cyan]  Copying {len(default_routes)} route(s) from default route table...[/cyan]"
                                    )
                                    for dr in default_routes:
                                        try:
                                            rstub.Create(
                                                route_service_pb2.CreateRouteRequest(
                                                    metadata=metadata_pb2.ResourceMetadata(
                                                        parent_id=new_rt_id,
                                                        name=f"{self._metadata_name(dr)}-copy"[:63],
                                                    ),
                                                    spec=self._route_spec(dr),
                                                )
                                            )
                                        except Exception as copy_err:
                                            # Ignore errors for copying (might be system routes that can't be copied)
                                            route_destination = self._route_destination_network(dr)
                                            destination_label = (
                                                str(route_destination)
                                                if route_destination
                                                else "unknown"
                                            )
                                            print(
                                                f"[dim]  Could not copy route {destination_label}: {copy_err}[/dim]"
                                            )
                                else:
                                    print("[dim]  No routes in default route table to copy[/dim]")
                            except Exception as list_err:
                                print(
                                    f"[yellow]  Could not list default route table routes: {list_err}[/yellow]"
                                )

                        # Attach to subnet - preserve IP pool configuration
                        self._attach_route_table_to_subnet(
                            sstub,
                            subnet_service_pb2,
                            metadata_pb2,
                            subnet_pb2,
                            subnet_obj=sn,
                            route_table_id=new_rt_id,
                        )
                        rt_id = new_rt_id
                        print(
                            f"[green]✓ Created custom route table {rt_id} and attached to subnet {subnet_name}[/green]"
                        )
                        print(
                            "[yellow]  NOTE: Future changes to the default route table will NOT apply to this subnet.[/yellow]"
                        )
                        print(
                            f"[yellow]  Add any required routes manually to route table: {rt_id}[/yellow]"
                        )
                    except Exception as e:
                        print(
                            f"[yellow]Failed to create/attach route table for subnet {subnet_name}: {e}[/yellow]"
                        )
                        continue

            reconciled_routes = self._reconcile_route_table(
                rstub,
                route_service_pb2,
                route_pb2,
                metadata_pb2,
                route_table_id=rt_id,
                prefix_targets=prefix_targets,
                summarize=summarize,
            )
            installed_prefix_targets = self._installed_prefix_targets(
                reconciled_routes,
                prefix_targets,
            )
            missing_prefix_targets = sorted(
                set(prefix_targets) - set(installed_prefix_targets)
            )
            if missing_prefix_targets:
                print(
                    f"[yellow]Route reconciliation on subnet {subnet_name} did not "
                    "reach its requested postcondition.[/yellow]"
                )
                for prefix in missing_prefix_targets:
                    print(f"[yellow]  Missing managed route: {prefix}[/yellow]")
                continue
            completed_subnets.add(subnet_name)

        expected_subnets = {self._metadata_name(subnet) for subnet, _ in selected_subnets}
        incomplete_subnets = sorted(expected_subnets - completed_subnets)
        if incomplete_subnets:
            raise RouteManagementError(
                "Route management did not converge on workload subnet(s): "
                + ", ".join(incomplete_subnets)
            )

    def _get_bgp_learned_routes(
        self, plan: ResolvedDeploymentPlan, conn: dict, local_cfg: dict
    ) -> list[str]:
        """Query FRR on gateway VMs to get BGP-learned routes (filtered by whitelist if configured)."""
        import json
        import subprocess

        conn_name = conn.get("name", "unnamed")
        whitelist = (
            conn.get("remote_prefixes") or (conn.get("bgp", {}) or {}).get("remote_prefixes") or []
        )

        # Create whitelist networks for matching
        whitelist_networks: list[ipaddress.IPv4Network] = []
        if whitelist:
            for pfx in whitelist:
                network = self._parse_ipv4_network(str(pfx))
                if network is not None:
                    whitelist_networks.append(network)

        learned_prefixes = []

        target_instance_indices = set(self._connection_instance_indices(conn))

        # Query only the gateway VM(s) that own this connection
        for inst_cfg in plan.iter_instance_configs():
            if target_instance_indices and inst_cfg.instance_index not in target_instance_indices:
                continue
            hostname = inst_cfg.hostname
            external_ip = inst_cfg.external_ip
            peer_ips = self._connection_peer_ips(conn, instance_index=inst_cfg.instance_index)

            if not external_ip:
                print(f"[yellow]Skipping {hostname}: no external IP for BGP route query[/yellow]")
                continue

            try:
                result = self._run_ssh(
                    local_cfg,
                    external_ip,
                    "sudo vtysh -c 'show bgp ipv4 unicast json'",
                    timeout=15,
                )

                if result.returncode != 0:
                    print(
                        f"[yellow]Failed to query BGP routes from {hostname}: {result.stderr}[/yellow]"
                    )
                    continue

                bgp_data = json.loads(result.stdout)
                routes = bgp_data.get("routes", {})

                selected_prefixes_for_host = 0
                for prefix, route_data in routes.items():
                    if isinstance(route_data, dict):
                        paths = [route_data]
                    elif isinstance(route_data, list):
                        paths = route_data
                    else:
                        continue

                    relevant_paths = []
                    for path in paths:
                        path_nexthops = self._path_nexthops(path)
                        if not path_nexthops:
                            continue
                        if peer_ips and not (path_nexthops & peer_ips):
                            continue
                        relevant_paths.append(path)

                    if not relevant_paths:
                        continue

                    # Apply whitelist filter if configured
                    if whitelist_networks:
                        try:
                            prefix_net = self._parse_ipv4_network(str(prefix))
                            if prefix_net is None:
                                continue
                            allowed = any(
                                prefix_net.subnet_of(wl) or prefix_net == wl
                                for wl in whitelist_networks
                            )
                            if not allowed:
                                continue
                        except Exception:
                            continue

                    if prefix not in learned_prefixes:
                        learned_prefixes.append(prefix)
                        selected_prefixes_for_host += 1

                print(
                    f"[cyan]Selected {selected_prefixes_for_host} connection-scoped BGP "
                    f"route(s) from {hostname} (connection: {conn_name}; FRR table: "
                    f"{len(routes)} route(s))[/cyan]"
                )

            except subprocess.TimeoutExpired:
                print(f"[yellow]Timeout querying BGP routes from {hostname}[/yellow]")
            except json.JSONDecodeError:
                print(f"[yellow]Failed to parse BGP JSON from {hostname}[/yellow]")
            except Exception as e:
                print(f"[yellow]Error querying BGP routes from {hostname}: {e}[/yellow]")

        return learned_prefixes

    def list_remote_routes(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
        connection_filter: str | None = None,
    ) -> None:
        """List remote routes learned via BGP or configured as static routes.

        - BGP mode: Query FRR for learned routes and check against remote_prefixes whitelist
        - Static mode: Show static routes configured from remote_prefixes
        """
        from rich.console import Console

        console = Console()

        # Get routing mode and connections
        defaults_mode = (
            self._normalize_value(
                (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode")
            )
            or "bgp"
        )
        connections = local_cfg.get("connections", [])

        if connection_filter:
            connections = [c for c in connections if c.get("name") == connection_filter]
            if not connections:
                print(f"[yellow]No connection found with name '{connection_filter}'[/yellow]")
                return

        # Group by gateway VM and connection
        for inst_cfg in plan.iter_instance_configs():
            hostname = inst_cfg.hostname
            external_ip = inst_cfg.external_ip

            if not external_ip:
                print(f"[yellow]Skipping {hostname}: no external IP[/yellow]")
                continue

            print(f"\n[bold cyan]Gateway VM: {hostname} ({external_ip})[/bold cyan]")

            # Process each connection for this VM
            for conn in connections:
                conn_name = conn.get("name", "unnamed")
                routing_mode = self._normalize_value(conn.get("routing_mode") or defaults_mode)
                owner_indices = self._connection_instance_indices(conn)
                if owner_indices and inst_cfg.instance_index not in owner_indices:
                    continue
                remote_prefixes = (
                    conn.get("remote_prefixes", [])
                    or (conn.get("bgp", {}) or {}).get("remote_prefixes", [])
                    if routing_mode == "bgp"
                    else connection_static_remote_prefixes(
                        conn,
                        instance_index=inst_cfg.instance_index,
                    )
                )

                print(f"\n[bold]Connection: {conn_name}[/bold] (routing_mode: {routing_mode})")

                if routing_mode == "bgp":
                    self._list_bgp_routes(
                        local_cfg,
                        hostname,
                        external_ip,
                        conn_name,
                        conn,
                        inst_cfg.instance_index,
                        remote_prefixes,
                        console,
                    )
                else:  # static mode
                    self._list_static_routes(
                        local_cfg,
                        hostname,
                        external_ip,
                        conn_name,
                        remote_prefixes,
                        console,
                    )

    def _list_bgp_routes(
        self,
        local_cfg: dict,
        hostname: str,
        external_ip: str,
        conn_name: str,
        conn: dict,
        instance_index: int,
        whitelist: list[str],
        console,
    ) -> None:
        """Query FRR BGP routes and check against whitelist."""
        import json
        import re
        import subprocess

        from rich.table import Table

        peer_ips = self._connection_peer_ips(conn, instance_index=instance_index)
        if not peer_ips:
            print("[dim]No BGP tunnel peers configured on this gateway VM for the connection[/dim]")
            return

        # Query BGP routes via SSH
        try:
            result = self._run_ssh(
                local_cfg,
                external_ip,
                "sudo vtysh -c 'show bgp ipv4 unicast json'",
                timeout=15,
            )

            if result.returncode != 0:
                print(f"[yellow]Failed to query BGP routes: {result.stderr}[/yellow]")
                return

            bgp_data = json.loads(result.stdout)
            routes = bgp_data.get("routes", {})

            if not routes:
                print("[dim]No BGP routes learned yet[/dim]")
                return

            # Build a cache of next-hop IP -> interface mappings
            nexthop_to_iface = {}
            unique_nexthops = set()
            for route_data in routes.values():
                paths = [route_data] if isinstance(route_data, dict) else route_data
                for path in paths:
                    path_nexthops = self._path_nexthops(path)
                    if path_nexthops & peer_ips:
                        unique_nexthops.update(path_nexthops & peer_ips)

            # Query interface for each unique next-hop
            for nh_ip in unique_nexthops:
                try:
                    route_result = self._run_ssh(
                        local_cfg,
                        external_ip,
                        f"ip route get {nh_ip}",
                        timeout=5,
                        ssh_connect_timeout=5,
                    )
                    if route_result.returncode == 0:
                        # Parse output like: "169.254.5.153 dev xfrm1 src 169.254.5.154 uid 1000"
                        match = re.search(r"dev\s+(\S+)", route_result.stdout)
                        if match:
                            nexthop_to_iface[nh_ip] = match.group(1)
                except Exception:
                    pass

            # Create whitelist networks for matching
            whitelist_networks: list[ipaddress.IPv4Network] = []
            if whitelist:
                for pfx in whitelist:
                    network = self._parse_ipv4_network(str(pfx))
                    if network is not None:
                        whitelist_networks.append(network)

            # Build table
            table = Table(title=f"Remote Routes (BGP-learned) - {conn_name}")
            table.add_column("Prefix", style="cyan")
            table.add_column("Next-Hop", style="blue")
            table.add_column("Via", style="magenta")
            table.add_column("AS Path", style="yellow")
            table.add_column("Status", style="green")
            row_count = 0

            for prefix, route_data in sorted(routes.items()):
                if isinstance(route_data, dict):
                    # Handle single path
                    paths = [route_data]
                elif isinstance(route_data, list):
                    # Handle multiple paths
                    paths = route_data
                else:
                    continue

                for path in paths:
                    path_nexthops = self._path_nexthops(path)
                    if not path_nexthops or not (path_nexthops & peer_ips):
                        continue

                    nexthops = path.get("nexthops", [])
                    as_path = path.get("path", "")

                    # Determine next-hop and interface
                    nexthop_ip = "-"
                    via_iface = "-"

                    if nexthops:
                        nh = nexthops[0]
                        nexthop_ip = nh.get("ip", "-")
                        # Look up interface from our cache
                        via_iface = nexthop_to_iface.get(nexthop_ip, "-")

                    # Skip locally originated routes (next-hop 0.0.0.0)
                    if nexthop_ip == "0.0.0.0":
                        continue

                    # Check whitelist status
                    status = "allowed"
                    if whitelist_networks:
                        try:
                            prefix_net = self._parse_ipv4_network(str(prefix))
                            if prefix_net is None:
                                status = "unknown"
                                continue
                            allowed = any(
                                prefix_net.subnet_of(wl) or prefix_net == wl
                                for wl in whitelist_networks
                            )
                            status = "allowed" if allowed else "[red]not-allowed[/red]"
                        except Exception:
                            status = "unknown"
                    else:
                        status = "[dim]no-filter[/dim]"

                    table.add_row(prefix, nexthop_ip, via_iface, as_path, status)
                    row_count += 1

            if not row_count:
                print(
                    "[dim]No BGP routes currently learned from this connection's tunnel peer(s)[/dim]"
                )
                return

            console.print(table)

            if whitelist:
                print(f"[dim]Note: remote_prefixes whitelist has {len(whitelist)} entries[/dim]")
            else:
                print(
                    "[dim]Note: No remote_prefixes whitelist configured - all BGP routes accepted[/dim]"
                )

        except subprocess.TimeoutExpired:
            print(f"[yellow]Timeout querying BGP routes from {hostname}[/yellow]")
        except json.JSONDecodeError:
            print(f"[yellow]Failed to parse BGP JSON output from {hostname}[/yellow]")
        except Exception as e:
            print(f"[yellow]Error querying BGP routes: {e}[/yellow]")

    def _list_static_routes(
        self,
        local_cfg: dict,
        hostname: str,
        external_ip: str,
        conn_name: str,
        remote_prefixes: list[str],
        console,
    ) -> None:
        """List static routes configured on gateway VM."""
        import subprocess

        from rich.table import Table

        if not remote_prefixes:
            print("[yellow]No remote_prefixes configured in YAML for this connection[/yellow]")
            return

        # Query kernel routing table via SSH
        try:
            result = self._run_ssh(
                local_cfg,
                external_ip,
                "ip route show",
                timeout=15,
            )

            if result.returncode != 0:
                print(f"[yellow]Failed to query routes: {result.stderr}[/yellow]")
                return

            # Parse routing table
            kernel_routes = {}
            for line in result.stdout.splitlines():
                parts = line.split()
                if not parts:
                    continue
                dest = parts[0]
                # Extract next-hop and interface
                nexthop = "-"
                via_dev = "-"
                if "via" in parts:
                    idx = parts.index("via")
                    if idx + 1 < len(parts):
                        nexthop = parts[idx + 1]
                if "dev" in parts:
                    idx = parts.index("dev")
                    if idx + 1 < len(parts):
                        via_dev = parts[idx + 1]
                kernel_routes[dest] = (nexthop, via_dev)

            # Build table
            table = Table(title=f"Remote Routes (Static) - {conn_name}")
            table.add_column("Prefix (YAML)", style="cyan")
            table.add_column("Status", style="yellow")
            table.add_column("Next-Hop", style="blue")
            table.add_column("Via", style="magenta")

            for pfx in sorted(remote_prefixes):
                if pfx in kernel_routes:
                    nexthop, via_dev = kernel_routes[pfx]
                    table.add_row(pfx, "[green]installed[/green]", nexthop, via_dev)
                else:
                    table.add_row(pfx, "[red]missing[/red]", "-", "-")

            console.print(table)
            print(f"[dim]Showing {len(remote_prefixes)} configured static remote prefixes[/dim]")

        except subprocess.TimeoutExpired:
            print(f"[yellow]Timeout querying routes from {hostname}[/yellow]")
        except Exception as e:
            print(f"[yellow]Error querying routes: {e}[/yellow]")
