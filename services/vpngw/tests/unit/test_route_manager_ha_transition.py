from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from grpc import StatusCode
from nebius.aio.service_error import RequestError

from nebius_vpngw.agent.vm_ha.models import StateValidationError
from nebius_vpngw.agent.vm_ha.runtime import RuntimeStateStore
from nebius_vpngw.agent.vm_ha.store import AtomicGenerationStore, CorruptStateError
from nebius_vpngw.deploy.route_manager import NebiusSDKRouteBackend, RouteManager
from nebius_vpngw.deploy.vm_ha_cloud import AmbiguousHACloudError, vm_ha_request_kwargs
from nebius_vpngw.deploy.vm_ha_routes import (
    AcceptedRouteOperation,
    BGPRouteReadiness,
    LogicalStaticRouteManifest,
    ManagedRouteKind,
    ManagedRouteOwnership,
    ManagedRouteSnapshot,
    RouteMutation,
    RouteMutationKind,
    RouteMutationPhase,
    RouteOccupancySnapshot,
    RouteReconciliationContext,
    RouteReplacementCompensated,
    RouteRollbackSnapshot,
    RouteTransitionState,
    VerifiedAllocationOwnership,
    VMHARouteReconciler,
    execute_route_plan,
)
from nebius_vpngw.schema import VMHAMigrationRouteBinding, VMHARouteTarget


def _target(suffix: str = "a") -> VMHARouteTarget:
    return VMHARouteTarget(
        project_id="project-a",
        network_id="network-a",
        workload_subnet_id=f"subnet-{suffix}",
        route_table_id=f"route-table-{suffix}",
    )


def _static_manifest(*prefixes: str) -> LogicalStaticRouteManifest:
    payload = json.dumps(
        [{"connection": "site-a", "remote_prefixes": list(prefixes)}],
        sort_keys=True,
        separators=(",", ":"),
    )
    return LogicalStaticRouteManifest.from_committed_json(
        payload,
        expected_digest=hashlib.sha256(payload.encode()).hexdigest(),
    )


def _bgp(
    *,
    learned: tuple[str, ...] = ("10.20.0.0/16",),
    usable: tuple[str, ...] = ("10.20.0.0/16",),
    required: tuple[str, ...] = ("10.20.0.0/16",),
    observed_policy: str = "policy-a",
) -> BGPRouteReadiness:
    return BGPRouteReadiness.normalize(
        configured_sessions=("169.254.10.2",),
        established_sessions=("169.254.10.2",),
        required_prefixes=required,
        optional_prefixes=("10.21.0.0/16",),
        learned_prefixes=learned,
        usable_xfrm_prefixes=usable,
        observed_import_policy_digest=observed_policy,
        committed_import_policy_digest="policy-a",
    )


def _owner(node_id: str = "node-b") -> VerifiedAllocationOwnership:
    return VerifiedAllocationOwnership(
        cluster_id="cluster-a",
        candidate_node_id="node-b",
        observed_owner_node_id=node_id,
        allocation_id="shared-allocation",
        ownership_epoch="7",
    )


def _context(
    *,
    operation_id: str = "boot-a:7:reconcile-routes:node-b",
    ownership_epoch: str = "7",
) -> RouteReconciliationContext:
    return RouteReconciliationContext(
        operation_id=operation_id,
        cluster_id="cluster-a",
        owner_node_id="node-b",
        allocation_id="shared-allocation",
        ownership_epoch=ownership_epoch,
        generation_id="generation-a",
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
        ownership_incarnation=3,
    )


def _observations(*plans):
    queued = deque(plans)
    return lambda: queued.popleft()


def _route(
    prefix: str,
    *,
    kind: ManagedRouteKind,
    allocation_id: str = "shared-allocation",
    route_id: str | None = None,
    cluster_id: str = "cluster-a",
) -> ManagedRouteSnapshot:
    return ManagedRouteSnapshot(
        route_id=route_id or f"route-{prefix}",
        prefix=prefix,
        allocation_id=allocation_id,
        ownership=ManagedRouteOwnership(cluster_id=cluster_id, kind=kind, route_target=_target()),
    )


def _reconciler(*, hold_down: float = 30, observations: int = 2) -> VMHARouteReconciler:
    return VMHARouteReconciler(
        cluster_id="cluster-a",
        node_id="node-b",
        takeover_hold_down_seconds=hold_down,
        withdrawal_stability_observations=observations,
        route_targets=(_target(),),
    )


def test_sdk_reproves_every_approval_bound_migration_route_field(monkeypatch) -> None:
    target = _target()
    route = SimpleNamespace(
        metadata=SimpleNamespace(
            id="route-old",
            name="vpngw-10.20.0.0-16",
            resource_version="7",
        ),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr="10.20.0.0/16"),
            next_hop=SimpleNamespace(allocation=SimpleNamespace(id="primary-allocation")),
        ),
    )
    binding = VMHAMigrationRouteBinding(
        route_id="route-old",
        name="vpngw-10.20.0.0-16",
        prefix="10.20.0.0/16",
        allocation_id="primary-allocation",
        resource_revision="7",
        route_target=target,
    )
    backend = NebiusSDKRouteBackend(object())
    verify_target = MagicMock()
    monkeypatch.setattr(backend, "verify_target", verify_target)
    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: (route,))

    assert backend.verify_migration_route(binding) is True
    verify_target.assert_called_once_with(target)

    route.metadata.resource_version = "8"
    assert backend.verify_migration_route(binding) is False


def _sdk_route_with_labels(labels: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(
            id="route-shared",
            name="vpngw-ha-managed",
            labels=labels,
            parent_id="route-table-a",
            resource_version="7",
        ),
        spec=SimpleNamespace(
            description="",
            destination=SimpleNamespace(cidr="10.20.0.0/16"),
            next_hop=SimpleNamespace(
                allocation=SimpleNamespace(id="shared-allocation")
            ),
        ),
    )


def test_sdk_cloud_authority_labels_are_a_shared_two_node_ledger(monkeypatch) -> None:
    target = _target()
    backend = NebiusSDKRouteBackend(object())
    backend.bind_route_authority(
        cluster_id="cluster-a",
        allocation_id="shared-allocation",
        route_targets=(target,),
    )
    labels = backend._authority_labels(
        cluster_id="cluster-a",
        allocation_id="shared-allocation",
        route_target=target,
        route_kind=ManagedRouteKind.STATIC,
    )
    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: (_sdk_route_with_labels(labels),))

    observed = backend.list_routes(target, {})

    assert observed == (
        ManagedRouteSnapshot(
            route_id="route-shared",
            prefix="10.20.0.0/16",
            allocation_id="shared-allocation",
            ownership=ManagedRouteOwnership(
                cluster_id="cluster-a",
                kind=ManagedRouteKind.STATIC,
                route_target=target,
            ),
            rollback=RouteRollbackSnapshot(
                route_id="route-shared",
                resource_version="7",
                name="vpngw-ha-managed",
                labels=tuple(sorted(labels.items())),
                description="",
                prefix="10.20.0.0/16",
                allocation_id="shared-allocation",
                route_target=target,
            ),
        ),
    )


def test_sdk_cloud_authority_supersedes_only_the_exact_migration_revision(
    monkeypatch,
) -> None:
    target = _target()
    backend = NebiusSDKRouteBackend(object())
    backend.bind_route_authority(
        cluster_id="cluster-a",
        allocation_id="shared-allocation",
        route_targets=(target,),
    )
    ownership = ManagedRouteOwnership(
        cluster_id="cluster-a",
        kind=ManagedRouteKind.STATIC,
        route_target=target,
    )
    labels = backend._authority_labels(
        cluster_id="cluster-a",
        allocation_id="shared-allocation",
        route_target=target,
        route_kind=ManagedRouteKind.STATIC,
    )
    route = _sdk_route_with_labels(labels)
    route.metadata.name = "vpngw-10.20.0.0-16"
    binding = VMHAMigrationRouteBinding(
        route_id="route-shared",
        name="vpngw-10.20.0.0-16",
        prefix="10.20.0.0/16",
        allocation_id="shared-allocation",
        resource_revision="6",
        route_target=target,
    )
    monkeypatch.setattr(backend, "verify_target", MagicMock())
    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: (route,))

    assert backend.verify_migration_successor(binding, ownership) is True

    route.metadata.labels = {backend._AUTHORITY_MANAGED_LABEL: "vm-ha-v1"}
    assert backend.verify_migration_successor(binding, ownership) is False


def test_sdk_foreign_or_partial_cloud_authority_labels_remain_read_only(monkeypatch) -> None:
    target = _target()
    backend = NebiusSDKRouteBackend(object())
    backend.bind_route_authority(
        cluster_id="cluster-a",
        allocation_id="shared-allocation",
        route_targets=(target,),
    )
    route = _sdk_route_with_labels(
        {
            backend._AUTHORITY_MANAGED_LABEL: "vm-ha-v1",
            backend._AUTHORITY_CLUSTER_LABEL: "foreign",
        }
    )
    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: (route,))

    observed = backend.list_routes(target, {})

    assert observed == (
        RouteOccupancySnapshot(
            route_id="route-shared",
            prefix="10.20.0.0/16",
            next_hop="allocation:shared-allocation",
            route_target=target,
        ),
    )


def test_sdk_owner_publishes_legacy_local_ledger_without_overwriting_conflicts(
    monkeypatch,
) -> None:
    target = _target()
    backend = NebiusSDKRouteBackend(object())
    backend.bind_route_authority(
        cluster_id="cluster-a",
        allocation_id="shared-allocation",
        route_targets=(target,),
    )
    route = _sdk_route_with_labels({"owner": "customer"})
    monkeypatch.setattr(backend, "verify_target", MagicMock())
    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: (route,))
    publish = MagicMock()
    monkeypatch.setattr(backend, "_write_authority_labels", publish)
    ledger = {
        "route-shared": ManagedRouteOwnership(
            cluster_id="cluster-a",
            kind=ManagedRouteKind.STATIC,
            route_target=target,
        )
    }

    backend.synchronize_authority_labels(ledger)

    expected = backend._authority_labels(
        cluster_id="cluster-a",
        allocation_id="shared-allocation",
        route_target=target,
        route_kind=ManagedRouteKind.STATIC,
    )
    publish.assert_called_once_with(route, target, {"owner": "customer", **expected})

    route.metadata.labels = {backend._AUTHORITY_MANAGED_LABEL: "foreign"}
    with pytest.raises(RuntimeError, match="conflicting cloud authority labels"):
        backend.synchronize_authority_labels(ledger)


def test_sdk_identifies_only_stably_absent_ledger_routes(monkeypatch) -> None:
    target = _target()
    backend = NebiusSDKRouteBackend(object())
    backend.bind_route_authority(
        cluster_id="cluster-a",
        allocation_id="shared-allocation",
        route_targets=(target,),
    )
    route = _sdk_route_with_labels({})
    route.metadata.id = "route-present"
    verify_target = MagicMock()
    monkeypatch.setattr(backend, "verify_target", verify_target)
    observations = iter(((route,), (route,)))
    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: next(observations))
    ownership = ManagedRouteOwnership(
        cluster_id="cluster-a",
        kind=ManagedRouteKind.STATIC,
        route_target=target,
    )

    absent = backend.stably_absent_ledger_route_ids(
        {
            "route-present": ownership,
            "route-obsolete": ownership,
        }
    )

    assert absent == frozenset({"route-obsolete"})
    assert verify_target.call_args_list == [((target,),), ((target,),)]


def test_sdk_refuses_to_retire_ledger_routes_across_an_unstable_reread(
    monkeypatch,
) -> None:
    target = _target()
    backend = NebiusSDKRouteBackend(object())
    backend.bind_route_authority(
        cluster_id="cluster-a",
        allocation_id="shared-allocation",
        route_targets=(target,),
    )
    route = _sdk_route_with_labels({})
    route.metadata.id = "route-reappeared"
    monkeypatch.setattr(backend, "verify_target", MagicMock())
    observations = iter(((), (route,)))
    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: next(observations))
    ownership = ManagedRouteOwnership(
        cluster_id="cluster-a",
        kind=ManagedRouteKind.STATIC,
        route_target=target,
    )

    with pytest.raises(RuntimeError, match="changed during stable reread"):
        backend.stably_absent_ledger_route_ids({"route-obsolete": ownership})


def test_sdk_authority_label_update_preserves_the_complete_route_spec() -> None:
    request = NebiusSDKRouteBackend._authority_label_update_request(
        route_id="route-shared",
        parent_id="route-table-a",
        name="vpngw-ha-managed",
        resource_version=7,
        labels={"owner": "customer", "nebius-vpngw-managed": "vm-ha-v1"},
        description="customer description",
        prefix="10.20.0.0/16",
        allocation_id="shared-allocation",
    )

    assert request.metadata.id == "route-shared"
    assert request.metadata.parent_id == "route-table-a"
    assert request.metadata.name == "vpngw-ha-managed"
    assert request.metadata.resource_version == 7
    assert dict(request.metadata.labels) == {
        "owner": "customer",
        "nebius-vpngw-managed": "vm-ha-v1",
    }
    assert request.spec.description == "customer description"
    assert request.spec.destination.cidr == "10.20.0.0/16"
    assert request.spec.next_hop.allocation.id == "shared-allocation"


def test_sdk_authority_label_update_rejects_an_incomplete_route_spec() -> None:
    with pytest.raises(RuntimeError, match="lacks the exact route spec"):
        NebiusSDKRouteBackend._authority_label_update_request(
            route_id="route-shared",
            parent_id="route-table-a",
            name="vpngw-ha-managed",
            resource_version=7,
            labels={},
            description="",
            prefix="",
            allocation_id="shared-allocation",
        )


def test_sdk_route_replace_restores_original_route_when_new_create_fails(monkeypatch) -> None:
    original = SimpleNamespace(
        metadata=SimpleNamespace(
            id="route-old",
            name="customer-route",
            labels={"owner": "customer"},
            resource_version="11",
        ),
        spec=SimpleNamespace(
            description="customer description",
            destination=SimpleNamespace(cidr="10.20.0.0/16"),
            next_hop=SimpleNamespace(allocation=SimpleNamespace(id="old-allocation")),
        ),
    )

    class Operation:
        resource_id = ""

        def sync_wait(self, **_kwargs) -> None:
            return None

        def successful(self) -> bool:
            return True

    class Pending:
        def wait(self) -> Operation:
            return Operation()

    class Client:
        def delete(self, request, **_kwargs) -> Pending:
            assert request == {"id": "route-old"}
            return Pending()

    backend = NebiusSDKRouteBackend(object())
    observations = deque(((original,), (original,), ()))
    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: observations.popleft())
    monkeypatch.setattr(
        backend,
        "_client_types",
        lambda: (lambda _sdk: Client(), object, object, lambda **kwargs: kwargs, *([object] * 5)),
    )

    class FailedOperation:
        def sync_wait(self, **_kwargs) -> None:
            return None

        def successful(self) -> bool:
            return False

    monkeypatch.setattr(
        backend,
        "_create_operation",
        lambda _mutation: (FailedOperation(), None),
    )
    monkeypatch.setattr(backend, "_observe_desired_route", lambda _mutation: None)
    restored: list[tuple[RouteRollbackSnapshot, RouteMutation]] = []

    def restore(
        rollback: RouteRollbackSnapshot,
        *,
        mutation: RouteMutation,
    ) -> str:
        restored.append((rollback, mutation))
        return "route-restored"

    monkeypatch.setattr(
        backend,
        "_restore_raw_route",
        restore,
    )
    mutation = RouteMutation(
        kind=RouteMutationKind.REPLACE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
        route_id="route-old",
    )

    with pytest.raises(RouteReplacementCompensated):
        backend.apply_mutation(mutation)

    assert len(restored) == 1
    rollback, compensated_mutation = restored[0]
    assert rollback == RouteRollbackSnapshot(
        route_id="route-old",
        resource_version="11",
        name="customer-route",
        labels=(("owner", "customer"),),
        description="customer description",
        prefix="10.20.0.0/16",
        allocation_id="old-allocation",
        route_target=_target(),
    )
    assert compensated_mutation.rollback == rollback


def test_sdk_route_create_timeout_accepts_one_authoritative_desired_outcome(
    monkeypatch,
) -> None:
    backend = NebiusSDKRouteBackend(object())
    mutation = RouteMutation(
        kind=RouteMutationKind.CREATE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
    )
    class TimedOutOperation:
        def sync_wait(self, **_kwargs) -> None:
            raise TimeoutError("accepted but timed out")

    monkeypatch.setattr(
        backend,
        "_create_operation",
        lambda _mutation: (TimedOutOperation(), None),
    )
    monkeypatch.setattr(
        backend,
        "_observe_desired_route",
        lambda _mutation: "route-created",
    )

    assert backend.apply_mutation(mutation) == "route-created"


def test_sdk_route_timeout_rejects_foreign_same_signature_outcome(monkeypatch) -> None:
    backend = NebiusSDKRouteBackend(object())
    mutation = RouteMutation(
        kind=RouteMutationKind.CREATE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
    )
    foreign = SimpleNamespace(
        metadata=SimpleNamespace(id="route-foreign", name="customer-managed-route"),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr=mutation.prefix),
            next_hop=SimpleNamespace(
                allocation=SimpleNamespace(id=mutation.allocation_id)
            ),
        ),
    )
    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: (foreign,))

    with pytest.raises(RuntimeError, match="conflicting outcome"):
        backend.recover_created_route(mutation)


def test_sdk_route_deleted_recovery_is_observation_only(monkeypatch) -> None:
    backend = NebiusSDKRouteBackend(object())
    mutation = RouteMutation(
        kind=RouteMutationKind.DELETE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
        route_id="route-old",
    )
    original = SimpleNamespace(metadata=SimpleNamespace(id="route-old"))
    delete = MagicMock()
    monkeypatch.setattr(backend, "_delete_exact_route", delete)
    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: (original,))

    assert backend.recover_deleted_route(mutation) is False
    delete.assert_not_called()

    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: ())
    assert backend.recover_deleted_route(mutation) is True
    delete.assert_not_called()


def test_sdk_route_duplicate_timeout_outcome_blocks_without_compensation(
    monkeypatch,
) -> None:
    backend = NebiusSDKRouteBackend(object())
    mutation = RouteMutation(
        kind=RouteMutationKind.CREATE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
    )
    class TimedOutOperation:
        def sync_wait(self, **_kwargs) -> None:
            raise TimeoutError("accepted but timed out")

    monkeypatch.setattr(
        backend,
        "_create_operation",
        lambda _mutation: (TimedOutOperation(), None),
    )
    monkeypatch.setattr(
        backend,
        "_observe_desired_route",
        lambda _mutation: (_ for _ in ()).throw(
            RuntimeError("duplicate desired outcomes")
        ),
    )
    restore = MagicMock()
    monkeypatch.setattr(backend, "_restore_raw_route", restore)

    with pytest.raises(RuntimeError, match="duplicate desired outcomes"):
        backend.apply_mutation(mutation)
    restore.assert_not_called()


def test_sdk_route_idempotency_keys_are_stable_and_domain_separated() -> None:
    backend = NebiusSDKRouteBackend(object())
    backend.set_reconciliation_operation_id("controller-operation")

    first = backend._action_operation_id("create", "mutation-a")
    replay = backend._action_operation_id("create", "mutation-a")
    compensation = backend._action_operation_id("restore", "mutation-a")

    assert first == replay
    assert first != compensation


def test_sdk_route_resumes_accepted_create_without_resubmit(
    tmp_path,
    monkeypatch,
) -> None:
    store = RuntimeStateStore(tmp_path / "state")
    backend = NebiusSDKRouteBackend(object())
    context = _context(operation_id="route-op")
    mutation = RouteMutation(
        kind=RouteMutationKind.CREATE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
    )
    backend.set_reconciliation_operation_id(context.operation_id)
    store.save_pending_mutation(mutation, context)
    initial = store.load_pending_mutation()
    assert initial is not None
    accepted = AcceptedRouteOperation(
        action_operation_id=backend._action_operation_id("create", mutation.operation_id),
        action="create",
        cloud_operation_id="cloud-operation-a",
    )
    store.checkpoint_pending_mutation(
        initial,
        phase=RouteMutationPhase.CREATE_ACCEPTED,
        rollback=None,
        accepted_operation=accepted,
    )
    backend.set_mutation_checkpoint(store)

    class Operation:
        def sync_wait(self, **_kwargs) -> None:
            return None

        def successful(self) -> bool:
            return True

    class Client:
        def create(self, *_args, **_kwargs):
            raise AssertionError("an accepted route operation must not be resubmitted")

    monkeypatch.setattr(
        backend,
        "_client_types",
        lambda: (lambda _sdk: Client(), object, *([lambda **kwargs: kwargs] * 7)),
    )
    monkeypatch.setattr(backend, "_resume_operation", lambda operation_id: Operation())
    observations = iter((None, "route-created"))
    monkeypatch.setattr(
        backend,
        "_observe_desired_route",
        lambda _mutation: next(observations),
    )

    assert backend.apply_mutation(mutation) == "route-created"
    pending = store.load_pending_mutation()
    assert pending is not None
    assert pending.phase is RouteMutationPhase.DESIRED_PRESENT
    assert pending.accepted_operation is None


def test_sdk_route_replays_same_idempotent_create_when_lookup_is_unsupported(
    tmp_path,
    monkeypatch,
) -> None:
    store = RuntimeStateStore(tmp_path / "state")
    backend = NebiusSDKRouteBackend(object())
    context = _context(operation_id="route-op")
    mutation = RouteMutation(
        kind=RouteMutationKind.CREATE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
    )
    backend.set_reconciliation_operation_id(context.operation_id)
    store.save_pending_mutation(mutation, context)
    initial = store.load_pending_mutation()
    assert initial is not None
    accepted = AcceptedRouteOperation(
        action_operation_id=backend._action_operation_id("create", mutation.operation_id),
        action="create",
        cloud_operation_id="cloud-operation-a",
    )
    store.checkpoint_pending_mutation(
        initial,
        phase=RouteMutationPhase.CREATE_ACCEPTED,
        rollback=None,
        accepted_operation=accepted,
    )
    backend.set_mutation_checkpoint(store)

    class Operation:
        id = accepted.cloud_operation_id

        def sync_wait(self, **_kwargs) -> None:
            return None

        def successful(self) -> bool:
            return True

    create = MagicMock(return_value=SimpleNamespace(wait=lambda: Operation()))
    client = SimpleNamespace(create=create)
    monkeypatch.setattr(
        backend,
        "_client_types",
        lambda: (lambda _sdk: client, object, *([lambda **kwargs: kwargs] * 7)),
    )
    monkeypatch.setattr(
        backend,
        "_resume_operation",
        lambda _operation_id: (_ for _ in ()).throw(
            RequestError(SimpleNamespace(code=StatusCode.UNIMPLEMENTED))  # type: ignore[arg-type]
        ),
    )
    observations = iter((None, "route-created"))
    monkeypatch.setattr(
        backend,
        "_observe_desired_route",
        lambda _mutation: next(observations),
    )

    assert backend.apply_mutation(mutation) == "route-created"
    create.assert_called_once()
    assert create.call_args.kwargs["metadata"] == (
        ("x-idempotency-key", accepted.action_operation_id),
    )


def test_sdk_route_retains_accepted_create_when_wait_and_observation_are_ambiguous(
    tmp_path,
    monkeypatch,
) -> None:
    store = RuntimeStateStore(tmp_path / "state")
    backend = NebiusSDKRouteBackend(object())
    context = _context(operation_id="route-op")
    mutation = RouteMutation(
        kind=RouteMutationKind.CREATE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
    )
    backend.set_reconciliation_operation_id(context.operation_id)
    store.save_pending_mutation(mutation, context)
    initial = store.load_pending_mutation()
    assert initial is not None
    accepted = AcceptedRouteOperation(
        action_operation_id=backend._action_operation_id("create", mutation.operation_id),
        action="create",
        cloud_operation_id="cloud-operation-a",
    )
    retained = store.checkpoint_pending_mutation(
        initial,
        phase=RouteMutationPhase.CREATE_ACCEPTED,
        rollback=None,
        accepted_operation=accepted,
    )
    backend.set_mutation_checkpoint(store)

    class Operation:
        def sync_wait(self, **_kwargs) -> None:
            raise TimeoutError("poll interrupted")

    monkeypatch.setattr(
        backend,
        "_client_types",
        lambda: (lambda _sdk: object(), object, *([lambda **kwargs: kwargs] * 7)),
    )
    monkeypatch.setattr(backend, "_resume_operation", lambda operation_id: Operation())
    monkeypatch.setattr(backend, "_observe_desired_route", lambda _mutation: None)

    with pytest.raises(TimeoutError, match="poll interrupted"):
        backend.apply_mutation(mutation)

    assert store.load_pending_mutation() == retained


def test_sdk_route_rejects_mismatched_accepted_operation(tmp_path, monkeypatch) -> None:
    store = RuntimeStateStore(tmp_path / "state")
    backend = NebiusSDKRouteBackend(object())
    context = _context(operation_id="route-op")
    mutation = RouteMutation(
        kind=RouteMutationKind.CREATE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
    )
    backend.set_reconciliation_operation_id(context.operation_id)
    store.save_pending_mutation(mutation, context)
    initial = store.load_pending_mutation()
    assert initial is not None
    store.checkpoint_pending_mutation(
        initial,
        phase=RouteMutationPhase.CREATE_ACCEPTED,
        rollback=None,
        accepted_operation=AcceptedRouteOperation(
            action_operation_id="different-action-operation",
            action="create",
            cloud_operation_id="cloud-operation-a",
        ),
    )
    backend.set_mutation_checkpoint(store)
    monkeypatch.setattr(
        backend,
        "_client_types",
        lambda: (lambda _sdk: object(), object, *([lambda **kwargs: kwargs] * 7)),
    )
    monkeypatch.setattr(backend, "_observe_desired_route", lambda _mutation: None)

    with pytest.raises(RuntimeError, match="different accepted"):
        backend.apply_mutation(mutation)


def test_sdk_route_legacy_replace_fails_closed_when_both_outcomes_are_absent(
    monkeypatch,
) -> None:
    backend = NebiusSDKRouteBackend(object())
    mutation = RouteMutation(
        kind=RouteMutationKind.REPLACE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
        route_id="route-old",
    )
    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: ())
    monkeypatch.setattr(backend, "_observe_desired_route", lambda _mutation: None)

    with pytest.raises(RuntimeError, match="lost both original and desired"):
        backend.apply_mutation(mutation)


def test_sdk_route_restart_advances_accepted_delete_from_observed_absence(
    tmp_path,
    monkeypatch,
) -> None:
    store = RuntimeStateStore(tmp_path / "state")
    backend = NebiusSDKRouteBackend(object())
    context = _context(operation_id="route-op")
    rollback = RouteRollbackSnapshot(
        route_id="route-old",
        resource_version="11",
        name="customer-route",
        labels=(),
        description="",
        prefix="10.20.0.0/16",
        allocation_id="old-allocation",
        route_target=_target(),
    )
    mutation = RouteMutation(
        kind=RouteMutationKind.REPLACE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
        route_id="route-old",
        rollback=rollback,
    )
    backend.set_reconciliation_operation_id(context.operation_id)
    store.save_pending_mutation(mutation, context)
    initial = store.load_pending_mutation()
    assert initial is not None
    store.checkpoint_pending_mutation(
        initial,
        phase=RouteMutationPhase.DELETE_ACCEPTED,
        rollback=rollback,
        accepted_operation=AcceptedRouteOperation(
            backend._action_operation_id("delete", mutation.operation_id),
            "delete",
            "cloud-delete-operation",
        ),
    )
    backend.set_mutation_checkpoint(store)

    class Operation:
        def sync_wait(self, **_kwargs) -> None:
            return None

        def successful(self) -> bool:
            return True

    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: ())
    observations = iter((None, "route-created"))
    monkeypatch.setattr(
        backend,
        "_observe_desired_route",
        lambda _mutation: next(observations),
    )
    monkeypatch.setattr(
        backend,
        "_resume_operation",
        lambda _operation_id: (_ for _ in ()).throw(
            AssertionError("observed delete completion must not be polled again")
        ),
    )
    monkeypatch.setattr(
        backend,
        "_create_operation",
        lambda _mutation: (Operation(), None),
    )

    assert backend.apply_mutation(mutation) == "route-created"
    pending = store.load_pending_mutation()
    assert pending is not None
    assert pending.phase is RouteMutationPhase.DESIRED_PRESENT
    assert pending.accepted_operation is None


def test_sdk_route_restart_resumes_accepted_restore_without_creating_desired_route(
    tmp_path,
    monkeypatch,
) -> None:
    store = RuntimeStateStore(tmp_path / "state")
    backend = NebiusSDKRouteBackend(object())
    context = _context(operation_id="route-op")
    rollback = RouteRollbackSnapshot(
        route_id="route-old",
        resource_version="11",
        name="customer-route",
        labels=(("owner", "customer"),),
        description="customer description",
        prefix="10.20.0.0/16",
        allocation_id="old-allocation",
        route_target=_target(),
    )
    mutation = RouteMutation(
        kind=RouteMutationKind.REPLACE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
        route_id="route-old",
        rollback=rollback,
    )
    backend.set_reconciliation_operation_id(context.operation_id)
    store.save_pending_mutation(mutation, context)
    initial = store.load_pending_mutation()
    assert initial is not None
    store.checkpoint_pending_mutation(
        initial,
        phase=RouteMutationPhase.RESTORE_ACCEPTED,
        rollback=rollback,
        accepted_operation=AcceptedRouteOperation(
            backend._action_operation_id("restore", mutation.operation_id),
            "restore",
            "cloud-restore-operation",
        ),
    )
    backend.set_mutation_checkpoint(store)

    class Operation:
        def sync_wait(self, **_kwargs) -> None:
            return None

        def successful(self) -> bool:
            return True

    class Client:
        def create(self, *_args, **_kwargs):
            raise AssertionError("an accepted rollback must not be resubmitted")

    monkeypatch.setattr(
        backend,
        "_client_types",
        lambda: (lambda _sdk: Client(), object, *([lambda **kwargs: kwargs] * 7)),
    )
    monkeypatch.setattr(backend, "_resume_operation", lambda _operation_id: Operation())
    monkeypatch.setattr(backend, "_observe_rollback_route", lambda _rollback: "restored-route")

    with pytest.raises(RouteReplacementCompensated):
        backend.apply_mutation(mutation)

    pending = store.load_pending_mutation()
    assert pending is not None
    assert pending.phase is RouteMutationPhase.RESTORED
    assert pending.accepted_operation is None


def test_sdk_route_rollback_observation_requires_exact_metadata(monkeypatch) -> None:
    backend = NebiusSDKRouteBackend(object())
    rollback = RouteRollbackSnapshot(
        route_id="route-old",
        resource_version="11",
        name="customer-route",
        labels=(("owner", "customer"),),
        description="customer description",
        prefix="10.20.0.0/16",
        allocation_id="old-allocation",
        route_target=_target(),
    )
    changed = SimpleNamespace(
        metadata=SimpleNamespace(
            id="restored-route",
            name=rollback.name,
            labels={"owner": "different"},
        ),
        spec=SimpleNamespace(
            description=rollback.description,
            destination=SimpleNamespace(cidr=rollback.prefix),
            next_hop=SimpleNamespace(
                allocation=SimpleNamespace(id=rollback.allocation_id)
            ),
        ),
    )
    monkeypatch.setattr(backend, "_raw_routes", lambda _route_table_id: (changed,))

    with pytest.raises(RuntimeError, match="conflicting outcome"):
        backend._observe_rollback_route(rollback)


def test_sdk_route_create_uses_canonical_bounded_request_kwargs(monkeypatch) -> None:
    backend = NebiusSDKRouteBackend(object())
    backend.set_reconciliation_operation_id("route-op")
    mutation = RouteMutation(
        kind=RouteMutationKind.CREATE,
        prefix="10.20.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="shared-allocation",
        cluster_id="cluster-a",
        route_target=_target(),
    )
    observed: dict[str, object] = {}

    class Request:
        def wait(self):
            return SimpleNamespace(id="cloud-operation-a")

    class Client:
        def create(self, request, **kwargs):
            observed["request"] = request
            observed["kwargs"] = kwargs
            return Request()

    def constructor(**kwargs):
        return kwargs
    monkeypatch.setattr(
        backend,
        "_client_types",
        lambda: (lambda _sdk: Client(), object, *([constructor] * 7)),
    )

    operation, accepted = backend._create_operation(mutation)

    action_id = backend._action_operation_id("create", mutation.operation_id)
    assert operation.id == "cloud-operation-a"
    assert accepted is None
    assert observed["kwargs"] == vm_ha_request_kwargs(action_id)


def test_committed_static_manifest_rejects_digest_mismatch() -> None:
    with pytest.raises(ValueError, match="digest mismatch"):
        LogicalStaticRouteManifest.from_committed_json("[]", expected_digest="wrong")


def test_non_owner_never_plans_route_mutations() -> None:
    state = RouteTransitionState(takeover_started_at=10)
    plan = _reconciler().plan(
        ownership=_owner(node_id="node-a"),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(),
        existing_routes=(),
        state=state,
        now=20,
    )

    assert not plan.authorized
    assert plan.mutations == ()
    assert plan.next_state is state


@pytest.mark.parametrize("ownership_epoch", ["", "0", "-1", "local-hash"])
def test_route_ownership_rejects_non_compute_epoch(ownership_epoch: str) -> None:
    with pytest.raises(ValueError, match="Compute resource revision"):
        replace(_owner(), ownership_epoch=ownership_epoch)


def test_non_owner_cannot_execute_even_a_forged_authorized_plan(tmp_path) -> None:
    plan = _reconciler().plan(
        ownership=_owner(node_id="node-a"),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=10,
    )
    calls: list[str] = []
    result = execute_route_plan(
        plan,
        lambda mutation: calls.append(mutation.operation_id),
        context=_context(),
        reobserve_ownership=_owner,
        reobserve_plan=lambda: plan,
        persist_receipt=AtomicGenerationStore(tmp_path / "ha").save_route_reconciliation_receipt,
        observe_receipt=lambda: None,
    )
    assert result.committed_state is None
    assert calls == []


def test_ambiguous_owner_observation_fails_before_route_mutation(tmp_path) -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=10,
    )
    calls: list[str] = []

    def ambiguous_owner() -> VerifiedAllocationOwnership:
        raise AmbiguousHACloudError("ambiguous owner")

    with pytest.raises(AmbiguousHACloudError, match="ambiguous owner"):
        RouteManager.execute_vm_ha_route_plan(
            plan,
            context=_context(),
            apply_mutation=lambda mutation: calls.append(mutation.operation_id),
            reobserve_ownership=ambiguous_owner,
            reobserve_plan=lambda: plan,
            receipt_store=AtomicGenerationStore(tmp_path / "ha"),
        )

    assert calls == []


def test_owner_reconciles_static_and_local_frr_routes_to_shared_allocation() -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=10),
        now=20,
    )

    assert {(item.kind, item.prefix, item.route_kind) for item in plan.mutations} == {
        (RouteMutationKind.CREATE, "10.10.0.0/16", ManagedRouteKind.STATIC),
        (RouteMutationKind.CREATE, "10.20.0.0/16", ManagedRouteKind.BGP),
    }
    assert {item.allocation_id for item in plan.mutations} == {"shared-allocation"}


def test_multi_table_desire_and_foreign_conflict_are_target_aware() -> None:
    targets = (_target("a"), _target("b"))
    reconciler = VMHARouteReconciler(
        cluster_id="cluster-a",
        node_id="node-b",
        takeover_hold_down_seconds=30,
        withdrawal_stability_observations=2,
        route_targets=targets,
    )
    arguments = dict(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )
    plan = reconciler.plan(existing_routes=(), **arguments)
    assert [(item.route_target.route_table_id, item.prefix) for item in plan.mutations] == [
        ("route-table-a", "10.10.0.0/16"),
        ("route-table-b", "10.10.0.0/16"),
    ]
    foreign = RouteOccupancySnapshot(
        route_id="foreign-b",
        prefix="10.10.0.0/16",
        next_hop="allocation:foreign",
        route_target=targets[1],
    )
    blocked = reconciler.plan(existing_routes=(foreign,), **arguments)
    assert blocked.mutations == ()
    assert blocked.blocked_reasons == ("foreign-route-conflict:route-table-b:10.10.0.0/16",)
    undeclared = replace(foreign, route_target=_target("c"))
    target_drift = reconciler.plan(existing_routes=(undeclared,), **arguments)
    assert target_drift.mutations == ()
    assert target_drift.blocked_reasons == ("undeclared-route-target:route-table-c",)


def test_takeover_preserves_existing_bgp_route_but_allows_new_local_route() -> None:
    plan = _reconciler(hold_down=60).plan(
        ownership=_owner(),
        static_manifest=_static_manifest(),
        bgp=_bgp(learned=("10.21.0.0/16",), usable=("10.21.0.0/16",), required=()),
        existing_routes=(_route("10.20.0.0/16", kind=ManagedRouteKind.BGP),),
        state=RouteTransitionState(takeover_started_at=100),
        now=120,
    )

    assert plan.held_bgp_prefixes == {"10.20.0.0/16"}
    assert [(item.kind, item.prefix) for item in plan.mutations] == [
        (RouteMutationKind.CREATE, "10.21.0.0/16")
    ]


def test_bgp_withdrawal_requires_bounded_stable_absence_observations() -> None:
    reconciler = _reconciler(hold_down=10, observations=2)
    existing = (_route("10.20.0.0/16", kind=ManagedRouteKind.BGP),)
    first = reconciler.plan(
        ownership=_owner(),
        static_manifest=_static_manifest(),
        bgp=_bgp(learned=(), usable=(), required=()),
        existing_routes=existing,
        state=RouteTransitionState(takeover_started_at=100),
        now=111,
    )

    assert first.mutations == ()
    assert first.held_bgp_prefixes == {"10.20.0.0/16"}
    assert first.next_state.absence_counts() == {"10.20.0.0/16": 1}

    second = reconciler.plan(
        ownership=_owner(),
        static_manifest=_static_manifest(),
        bgp=_bgp(learned=(), usable=(), required=()),
        existing_routes=existing,
        state=first.next_state,
        now=112,
    )

    assert [(item.kind, item.prefix) for item in second.mutations] == [
        (RouteMutationKind.DELETE, "10.20.0.0/16")
    ]


def test_route_reappearance_resets_withdrawal_stability() -> None:
    existing = (_route("10.20.0.0/16", kind=ManagedRouteKind.BGP),)
    plan = _reconciler(hold_down=0).plan(
        ownership=_owner(),
        static_manifest=_static_manifest(),
        bgp=_bgp(),
        existing_routes=existing,
        state=RouteTransitionState(
            takeover_started_at=100,
            absent_bgp_observations=(("10.20.0.0/16", 1),),
        ),
        now=110,
    )

    assert plan.mutations == ()
    assert plan.next_state.absence_counts() == {}


def test_bgp_readiness_requires_sessions_policy_prefixes_and_xfrm() -> None:
    readiness = BGPRouteReadiness.normalize(
        configured_sessions=("peer-a", "peer-b"),
        established_sessions=("peer-a",),
        required_prefixes=("10.20.0.0/16", "10.21.0.0/16"),
        optional_prefixes=("10.22.0.0/16",),
        learned_prefixes=("10.20.0.0/16", "10.22.0.0/16"),
        usable_xfrm_prefixes=("10.22.0.0/16",),
        observed_import_policy_digest="stale",
        committed_import_policy_digest="current",
    )

    assert not readiness.promotion_ready
    assert readiness.blocked_reasons == (
        "bgp-import-policy-mismatch",
        "required-bgp-prefixes-not-learned",
        "required-bgp-prefixes-lack-usable-xfrm-next-hop",
    )
    assert readiness.missing_optional_prefixes == {"10.22.0.0/16"}


def test_bgp_readiness_accepts_redundant_peer_loss_with_complete_prefix_coverage() -> None:
    readiness = BGPRouteReadiness.normalize(
        configured_sessions=("peer-a", "peer-b"),
        established_sessions=("peer-a",),
        required_prefixes=("10.20.0.0/16",),
        learned_prefixes=("10.20.0.0/16",),
        usable_xfrm_prefixes=("10.20.0.0/16",),
        observed_import_policy_digest="current",
        committed_import_policy_digest="current",
    )

    assert readiness.promotion_ready
    assert readiness.eligible_prefixes == {"10.20.0.0/16"}


def test_stale_import_policy_cannot_make_learned_route_eligible() -> None:
    readiness = _bgp(observed_policy="stale-policy")

    assert readiness.eligible_prefixes == frozenset()


def test_empty_import_policy_digests_are_not_promotion_ready() -> None:
    readiness = BGPRouteReadiness.normalize(
        configured_sessions=("peer-a",),
        established_sessions=("peer-a",),
        required_prefixes=(),
        learned_prefixes=("10.20.0.0/16",),
        usable_xfrm_prefixes=("10.20.0.0/16",),
        observed_import_policy_digest="",
        committed_import_policy_digest="",
    )

    assert not readiness.promotion_ready
    assert readiness.eligible_prefixes == frozenset()


@pytest.mark.parametrize(
    "state",
    [
        RouteTransitionState(takeover_started_at=float("nan")),
        RouteTransitionState(
            takeover_started_at=0,
            absent_bgp_observations=(("10.20.0.0/16", -1),),
        ),
    ],
)
def test_invalid_transition_state_fails_closed(state: RouteTransitionState) -> None:
    with pytest.raises(ValueError):
        _reconciler().plan(
            ownership=_owner(),
            static_manifest=_static_manifest(),
            bgp=_bgp(required=(), learned=(), usable=()),
            existing_routes=(_route("10.20.0.0/16", kind=ManagedRouteKind.BGP),),
            state=state,
            now=100,
        )


def test_non_finite_observation_time_fails_closed() -> None:
    with pytest.raises(ValueError):
        _reconciler().plan(
            ownership=_owner(),
            static_manifest=_static_manifest(),
            bgp=_bgp(required=(), learned=(), usable=()),
            existing_routes=(_route("10.20.0.0/16", kind=ManagedRouteKind.BGP),),
            state=RouteTransitionState(takeover_started_at=0),
            now=float("nan"),
        )


def test_non_finite_takeover_hold_down_is_rejected() -> None:
    with pytest.raises(ValueError):
        _reconciler(hold_down=float("nan"))


def test_duplicate_owned_route_is_deleted_after_one_exact_route_is_retained() -> None:
    exact = _route(
        "10.10.0.0/16",
        kind=ManagedRouteKind.STATIC,
        route_id="route-exact",
    )
    duplicate = _route(
        "10.10.0.0/16",
        kind=ManagedRouteKind.STATIC,
        route_id="route-duplicate",
    )
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(exact, duplicate),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )

    assert [(item.kind, item.route_id) for item in plan.mutations] == [
        (RouteMutationKind.DELETE, "route-duplicate")
    ]


def test_explicit_route_ownership_blocks_name_based_takeover_and_foreign_conflict() -> None:
    foreign = _route(
        "10.10.0.0/16",
        kind=ManagedRouteKind.STATIC,
        route_id="vpngw-name-is-not-authority",
        cluster_id="other-cluster",
    )
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(foreign,),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )

    assert plan.mutations == ()
    assert plan.blocked_reasons == ("foreign-route-conflict:route-table-a:10.10.0.0/16",)


def test_unledgered_occupied_prefix_flows_from_route_manager_to_conflict_plan() -> None:
    route_manager = RouteManager(project_id="project-test")
    foreign = SimpleNamespace(
        metadata=SimpleNamespace(id="route-foreign", name="unmanaged"),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr="10.10.0.0/16"),
            next_hop=SimpleNamespace(
                allocation=SimpleNamespace(id="foreign-allocation"),
                default_egress_gateway=False,
            ),
        ),
    )

    observed = route_manager._vm_ha_route_snapshots(
        (foreign,), ownership_by_route_id={}, route_target=_target()
    )
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16", "10.11.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=observed,
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )

    assert plan.mutations == ()
    assert plan.blocked_reasons == ("foreign-route-conflict:route-table-a:10.10.0.0/16",)


@pytest.mark.parametrize(
    "next_hop",
    [
        SimpleNamespace(
            allocation=SimpleNamespace(id="foreign-allocation"),
            default_egress_gateway=True,
        ),
        SimpleNamespace(
            allocation=SimpleNamespace(id=""),
            default_egress_gateway=False,
        ),
    ],
)
def test_vm_ha_route_observation_rejects_ambiguous_or_missing_next_hop(
    next_hop: SimpleNamespace,
) -> None:
    route = SimpleNamespace(
        metadata=SimpleNamespace(id="route-foreign", name="unmanaged"),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr="10.10.0.0/16"),
            next_hop=next_hop,
        ),
    )

    with pytest.raises(ValueError, match="next hop"):
        RouteManager(project_id="project-test")._vm_ha_route_snapshots(
            (route,), ownership_by_route_id={}, route_target=_target()
        )


def test_existing_route_with_old_allocation_is_replaced_with_shared_allocation() -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(
            _route(
                "10.10.0.0/16",
                kind=ManagedRouteKind.STATIC,
                allocation_id="old-node-allocation",
                route_id="route-a",
            ),
        ),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )

    assert len(plan.mutations) == 1
    assert plan.mutations[0].kind is RouteMutationKind.REPLACE
    assert plan.mutations[0].route_id == "route-a"
    assert plan.mutations[0].allocation_id == "shared-allocation"


def test_partial_failure_does_not_commit_observation_state_and_retry_is_idempotent(
    tmp_path,
) -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16", "10.11.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )
    calls: list[str] = []

    def fail_second(mutation) -> None:
        calls.append(mutation.operation_id)
        if len(calls) == 2:
            raise RuntimeError("injected")

    store = AtomicGenerationStore(tmp_path / "ha")
    result = execute_route_plan(
        plan,
        fail_second,
        context=_context(),
        reobserve_ownership=_owner,
        reobserve_plan=lambda: plan,
        persist_receipt=store.save_route_reconciliation_receipt,
        observe_receipt=store.load_route_reconciliation_receipt,
    )

    assert len(result.applied) == 1
    assert result.failed == plan.mutations[1]
    assert result.remaining == plan.mutations[1:]
    assert result.committed_state is None

    installed = {
        mutation.prefix: _route(
            mutation.prefix,
            kind=mutation.route_kind,
            route_id=f"installed-{mutation.prefix}",
        )
        for mutation in result.applied
    }
    retry = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16", "10.11.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=installed.values(),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )

    assert [item.prefix for item in retry.mutations] == [result.failed.prefix]
    assert (
        execute_route_plan(
            retry,
            lambda mutation: None,
            context=_context(),
            reobserve_ownership=_owner,
            reobserve_plan=_observations(retry, replace(retry, mutations=())),
            persist_receipt=store.save_route_reconciliation_receipt,
            observe_receipt=store.load_route_reconciliation_receipt,
        ).committed_state
        == retry.next_state
    )


def test_ownership_epoch_change_mid_plan_stops_before_next_mutation(tmp_path) -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16", "10.11.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )
    owners = deque((_owner(), _owner(), replace(_owner(), ownership_epoch="8")))
    calls: list[str] = []
    store = AtomicGenerationStore(tmp_path / "ha")

    result = execute_route_plan(
        plan,
        lambda mutation: calls.append(mutation.operation_id),
        context=_context(),
        reobserve_ownership=lambda: owners.popleft(),
        reobserve_plan=lambda: plan,
        persist_receipt=store.save_route_reconciliation_receipt,
        observe_receipt=store.load_route_reconciliation_receipt,
    )

    assert result.applied == plan.mutations[:1]
    assert result.failed == plan.mutations[1]
    assert result.remaining == plan.mutations[1:]
    assert result.receipt is None
    assert len(calls) == 1


def test_route_receipt_is_exact_operation_bound_and_restart_durable(tmp_path) -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )
    store = AtomicGenerationStore(tmp_path / "ha")
    calls: list[str] = []
    first = RouteManager.execute_vm_ha_route_plan(
        plan,
        context=_context(),
        apply_mutation=lambda mutation: calls.append(mutation.operation_id),
        reobserve_ownership=_owner,
        reobserve_plan=_observations(plan, replace(plan, mutations=())),
        receipt_store=store,
    )

    assert first.receipt is not None
    assert first.receipt.context.operation_id == _context().operation_id
    assert len(calls) == 1
    replay = RouteManager.execute_vm_ha_route_plan(
        plan,
        context=_context(),
        apply_mutation=lambda mutation: calls.append(mutation.operation_id),
        reobserve_ownership=_owner,
        reobserve_plan=lambda: replace(plan, mutations=()),
        receipt_store=AtomicGenerationStore(store.root),
    )
    assert replay.receipt == first.receipt
    assert len(calls) == 1

    foreign_current = replace(
        plan,
        blocked_reasons=("foreign-route-conflict:10.10.0.0/16",),
        mutations=(),
    )
    with pytest.raises(RuntimeError, match="does not satisfy"):
        RouteManager.execute_vm_ha_route_plan(
            plan,
            context=_context(),
            apply_mutation=lambda mutation: calls.append(mutation.operation_id),
            reobserve_ownership=_owner,
            reobserve_plan=lambda: foreign_current,
            receipt_store=store,
        )
    assert len(calls) == 1

    settled_plan = replace(plan, mutations=())
    changed_operation = RouteManager.execute_vm_ha_route_plan(
        settled_plan,
        context=_context(operation_id="boot-a:8:reconcile-routes:node-b"),
        apply_mutation=lambda mutation: calls.append(mutation.operation_id),
        reobserve_ownership=_owner,
        reobserve_plan=_observations(settled_plan, settled_plan),
        receipt_store=store,
    )
    assert changed_operation.receipt != first.receipt
    assert len(calls) == 1


def test_route_receipt_store_rejects_fields_outside_secret_free_contract(tmp_path) -> None:
    store = AtomicGenerationStore(tmp_path / "ha")
    context = {**_context().to_dict(), "unexpected": "not-persisted"}

    with pytest.raises(StateValidationError, match="context has an invalid shape"):
        store.save_route_reconciliation_receipt(
            {
                "schema": "nebius-vpngw/vm-ha-route-reconciliation-receipt-v1",
                "context": context,
                "plan_digest": "d" * 64,
            }
        )

    assert not store.route_receipt_path.exists()


def test_route_executor_rejects_stale_ownership_epoch_without_mutation(tmp_path) -> None:
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )
    calls: list[str] = []
    result = RouteManager.execute_vm_ha_route_plan(
        plan,
        context=_context(ownership_epoch="8"),
        apply_mutation=lambda mutation: calls.append(mutation.operation_id),
        reobserve_ownership=_owner,
        reobserve_plan=lambda: plan,
        receipt_store=AtomicGenerationStore(tmp_path / "ha"),
    )
    assert result.committed_state is None
    assert result.receipt is None
    assert calls == []


def test_corrupt_route_receipt_fails_closed_before_mutation(tmp_path) -> None:
    store = AtomicGenerationStore(tmp_path / "ha")
    store.route_receipt_path.write_text("{truncated", encoding="utf-8")
    plan = _reconciler().plan(
        ownership=_owner(),
        static_manifest=_static_manifest("10.10.0.0/16"),
        bgp=_bgp(required=(), learned=(), usable=()),
        existing_routes=(),
        state=RouteTransitionState(takeover_started_at=0),
        now=100,
    )
    with pytest.raises(CorruptStateError, match="route reconciliation receipt"):
        RouteManager.execute_vm_ha_route_plan(
            plan,
            context=_context(),
            apply_mutation=lambda mutation: pytest.fail("must not mutate"),
            reobserve_ownership=_owner,
            reobserve_plan=lambda: plan,
            receipt_store=store,
        )


def test_generation_adapter_consumes_task_one_logical_manifest_shape() -> None:
    payload = '[{"connection":"site-a","remote_prefixes":["10.10.0.0/16"]}]'
    generation = SimpleNamespace(
        logical_manifests=SimpleNamespace(static_routes_json=payload),
        digests=SimpleNamespace(static_routes=hashlib.sha256(payload.encode()).hexdigest()),
    )

    manifest = LogicalStaticRouteManifest.from_generation(generation)

    assert manifest.prefixes == {"10.10.0.0/16"}
