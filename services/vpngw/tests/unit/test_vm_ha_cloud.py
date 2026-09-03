from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from grpc import StatusCode
from nebius.aio.service_error import RequestError

from nebius_vpngw.deploy.vm_ha_cloud import (
    AcceptedCloudOperation,
    AllocationObservation,
    AllocationOwner,
    AmbiguousHACloudError,
    ComputeObservation,
    InstanceCloudState,
    MissingHAComputeError,
    NebiusSDKCloudClient,
    PermanentHACloudError,
    VMHACloudAdapter,
    VMHACloudOperationJournal,
    clone_nebius_sdk_message,
    nebius_request_error_code_is,
    operation_status_lookup_unsupported,
    vm_ha_idempotency_key,
    vm_ha_request_kwargs,
)


class _ModernGeneratedMessage:
    """Small stand-in for the newer Nebius SDK generated-message shape."""

    def __init__(self, source: _ModernGeneratedMessage | None = None) -> None:
        self._values = (
            {"nested": {"name": "eth0"}, "repeated": [{"allocation_id": "shared-a"}]}
            if source is None
            else deepcopy(source._values)
        )


def test_clone_nebius_sdk_message_supports_modern_values_without_aliasing() -> None:
    source = _ModernGeneratedMessage()

    cloned = clone_nebius_sdk_message(source)
    cloned._values["nested"]["name"] = "changed"
    cloned._values["repeated"].clear()

    assert source._values == {
        "nested": {"name": "eth0"},
        "repeated": [{"allocation_id": "shared-a"}],
    }


def test_clone_nebius_sdk_message_rejects_unknown_representation() -> None:
    with pytest.raises(TypeError, match="Unsupported Nebius SDK"):
        clone_nebius_sdk_message(SimpleNamespace(value="ambiguous"))


def test_sdk_alias_update_preserves_live_message_fields_without_mutating_source() -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute.v1 import (
        Instance,
        InstanceSpec,
        IPAddress,
        IPAlias,
        NetworkInterfaceSpec,
    )

    instance = Instance(
        metadata=ResourceMetadata(
            id="instance-a", parent_id="project-a", name="gateway-a", resource_version=7
        ),
        spec=InstanceSpec(
            network_interfaces=[
                NetworkInterfaceSpec(
                    subnet_id="subnet-a",
                    name="eth0",
                    ip_address=IPAddress(allocation_id="primary-a"),
                    aliases=[IPAlias(allocation_id="shared-a")],
                )
            ]
        ),
    )
    request = MagicMock()
    request.wait.return_value = SimpleNamespace(id="cloud-operation-a", successful=lambda: True)
    service = MagicMock()
    service.update.return_value = request
    client = NebiusSDKCloudClient(object())

    with (
        patch.object(client, "get_instance", return_value=instance),
        patch("nebius.api.nebius.compute.v1.InstanceServiceClient", return_value=service),
        patch("nebius_vpngw.deploy.vm_ha_cloud.wait_vm_ha_operation"),
    ):
        client.set_alias_allocation(
            "instance-a", "eth0", "shared-a", False, operation_id="operation-a"
        )

    update = service.update.call_args.args[0]
    assert update.metadata.id == "instance-a"
    assert update.metadata.resource_version == 7
    assert update.spec.network_interfaces[0].name == "eth0"
    assert update.spec.network_interfaces[0].ip_address.allocation_id == "primary-a"
    assert list(update.spec.network_interfaces[0].aliases) == []
    assert [alias.allocation_id for alias in instance.spec.network_interfaces[0].aliases] == [
        "shared-a"
    ]


def test_sdk_start_instance_uses_bounded_idempotent_mutation() -> None:
    operation = SimpleNamespace(id="cloud-operation-a", successful=lambda: True)
    request = MagicMock()
    request.wait.return_value = operation
    service = MagicMock()
    service.start.return_value = request
    client = NebiusSDKCloudClient(object())

    with (
        patch("nebius.api.nebius.compute.v1.InstanceServiceClient", return_value=service),
        patch("nebius_vpngw.deploy.vm_ha_cloud.wait_vm_ha_operation") as wait,
    ):
        client.start_instance("instance-a", "operation-a")

    submitted = service.start.call_args.args[0]
    assert submitted.id == "instance-a"
    assert service.start.call_args.kwargs["metadata"] == (("x-idempotency-key", "operation-a"),)
    wait.assert_called_once_with(operation)


def test_vm_ha_request_key_encodes_only_provider_invalid_action_ids() -> None:
    controller_id = "boot-a:1:stop-former-owner:node-a"
    encoded = hashlib.sha256(controller_id.encode("utf-8")).hexdigest()

    assert vm_ha_idempotency_key("already-valid-key-7") == "already-valid-key-7"
    assert vm_ha_idempotency_key(controller_id) == encoded
    assert vm_ha_idempotency_key(controller_id + "-other") != encoded
    assert "metadata" not in vm_ha_request_kwargs()
    assert vm_ha_request_kwargs(controller_id)["metadata"] == (("x-idempotency-key", encoded),)


def test_sdk_stop_encodes_provider_key_but_retains_action_journal_identity(
    tmp_path: Path,
) -> None:
    controller_id = "boot-a:1:stop-former-owner:node-a"
    cloud_operation_id = "cloud-operation-a"
    operation = SimpleNamespace(id=cloud_operation_id, successful=lambda: True)
    request = MagicMock()
    request.wait.return_value = operation
    service = MagicMock()
    service.stop.return_value = request
    journal = VMHACloudOperationJournal(tmp_path / "accepted.json")
    client = NebiusSDKCloudClient(object(), operation_journal=journal)

    with (
        patch("nebius.api.nebius.compute.v1.InstanceServiceClient", return_value=service),
        patch(
            "nebius_vpngw.deploy.vm_ha_cloud.wait_vm_ha_operation",
            side_effect=RuntimeError("injected post-acceptance interruption"),
        ),
        pytest.raises(RuntimeError, match="post-acceptance interruption"),
    ):
        client.stop_instance("instance-a", controller_id)

    encoded = hashlib.sha256(controller_id.encode("utf-8")).hexdigest()
    assert service.stop.call_args.kwargs["metadata"] == (("x-idempotency-key", encoded),)
    assert journal.load() == AcceptedCloudOperation(
        action_operation_id=controller_id,
        kind="stop-instance",
        cloud_operation_id=cloud_operation_id,
    )


@pytest.mark.parametrize(
    ("mutation", "alias_present"),
    (
        ("stop", None),
        ("start", None),
        ("set-alias", True),
        ("set-alias", False),
    ),
)
def test_sdk_mutations_share_provider_key_encoding(
    mutation: str,
    alias_present: bool | None,
) -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute.v1 import (
        Instance,
        InstanceSpec,
        IPAlias,
        NetworkInterfaceSpec,
    )

    controller_id = "boot-a:1:stop-former-owner:node-a"
    operation = SimpleNamespace(id="cloud-operation-a", successful=lambda: True)
    request = MagicMock()
    request.wait.return_value = operation
    service = MagicMock()
    service.stop.return_value = request
    service.start.return_value = request
    service.update.return_value = request
    client = NebiusSDKCloudClient(object())
    instance = Instance(
        metadata=ResourceMetadata(id="instance-a", resource_version=7),
        spec=InstanceSpec(
            network_interfaces=[
                NetworkInterfaceSpec(
                    name="eth0",
                    aliases=[] if alias_present else [IPAlias(allocation_id="shared-a")],
                )
            ]
        ),
    )

    with (
        patch("nebius.api.nebius.compute.v1.InstanceServiceClient", return_value=service),
        patch.object(client, "get_instance", return_value=instance),
        patch("nebius_vpngw.deploy.vm_ha_cloud.wait_vm_ha_operation"),
    ):
        if mutation == "stop":
            client.stop_instance("instance-a", controller_id)
            submitted = service.stop
        elif mutation == "start":
            client.start_instance("instance-a", controller_id)
            submitted = service.start
        else:
            assert alias_present is not None
            client.set_alias_allocation(
                "instance-a",
                "eth0",
                "shared-a",
                alias_present,
                operation_id=controller_id,
            )
            submitted = service.update

    encoded = hashlib.sha256(controller_id.encode("utf-8")).hexdigest()
    assert submitted.call_args.kwargs["metadata"] == (("x-idempotency-key", encoded),)


def test_sdk_read_uses_remaining_deadline_budget() -> None:
    request = MagicMock()
    request.wait.return_value = SimpleNamespace(metadata=SimpleNamespace(id="instance-a"))
    service = MagicMock()
    service.get.return_value = request
    client = NebiusSDKCloudClient(object(), request_timeout_provider=lambda: 4.5)

    with patch("nebius.api.nebius.compute.v1.InstanceServiceClient", return_value=service):
        client.get_instance("instance-a")

    assert service.get.call_args.kwargs == {
        "auth_timeout": 4.5,
        "per_retry_timeout": 4.5,
        "retries": 3,
        "timeout": 4.5,
    }


def test_sdk_read_rejects_expired_deadline_before_submission() -> None:
    service = MagicMock()
    client = NebiusSDKCloudClient(object(), request_timeout_provider=lambda: 0.0)

    with (
        patch("nebius.api.nebius.compute.v1.InstanceServiceClient", return_value=service),
        pytest.raises(TimeoutError, match="deadline has expired"),
    ):
        client.get_instance("instance-a")

    service.get.assert_not_called()


def _accepted_operation() -> AcceptedCloudOperation:
    return AcceptedCloudOperation(
        action_operation_id="a" * 64,
        kind="stop-instance",
        cloud_operation_id="cloud-operation-1",
    )


def _request_error(code: StatusCode) -> RequestError:
    return RequestError(SimpleNamespace(code=code))  # type: ignore[arg-type]


def test_operation_status_lookup_unsupported_is_exact() -> None:
    assert operation_status_lookup_unsupported(_request_error(StatusCode.UNIMPLEMENTED))
    assert not operation_status_lookup_unsupported(_request_error(StatusCode.UNAVAILABLE))
    assert not operation_status_lookup_unsupported(RuntimeError("UNIMPLEMENTED"))
    assert nebius_request_error_code_is(_request_error(StatusCode.ALREADY_EXISTS), "ALREADY_EXISTS")


def test_cloud_adapter_classifies_not_found_as_exact_missing_compute() -> None:
    adapter = VMHACloudAdapter(
        instance_reader=lambda _instance_id: (_ for _ in ()).throw(
            _request_error(StatusCode.NOT_FOUND)
        ),
        instance_stopper=lambda *_args: None,
        allocation_reader=lambda _allocation_id: None,
        alias_allocation_setter=lambda *_args: None,
    )

    with pytest.raises(MissingHAComputeError):
        adapter._read_instance("retired-compute")


def test_cloud_adapter_allows_missing_former_only_with_exact_candidate_ownership() -> None:
    allocation_id = "shared-a"
    former_owner = AllocationOwner("retired-compute", "eth0")
    candidate_owner = AllocationOwner("owner-compute", "eth0")
    exact_allocation = AllocationObservation(allocation_id, candidate_owner)
    candidate = ComputeObservation(
        instance_id=candidate_owner.instance_id,
        state=InstanceCloudState.RUNNING,
        resource_version="7",
        nic_allocations=(("eth0", "primary-a"),),
        nic_alias_allocations=(("eth0", (allocation_id,)),),
    )
    adapter = VMHACloudAdapter(
        instance_reader=lambda _instance_id: None,
        instance_stopper=lambda *_args: None,
        allocation_reader=lambda _allocation_id: None,
        alias_allocation_setter=lambda *_args: None,
    )
    adapter._read_allocation = MagicMock(side_effect=[exact_allocation, exact_allocation])

    def read_instance(instance_id: str) -> ComputeObservation:
        if instance_id == former_owner.instance_id:
            raise MissingHAComputeError("retired Compute is absent")
        return candidate

    adapter._read_instance = MagicMock(side_effect=read_instance)

    observed = adapter.observe_cluster(
        allocation_id=allocation_id,
        former_owner=former_owner,
        candidate=candidate_owner,
        allow_missing_former=True,
    )

    assert observed.former.state is InstanceCloudState.STOPPED
    assert observed.former_attachment_absent is True
    assert observed.candidate_attachment_exact is True

    adapter._read_allocation = MagicMock(
        side_effect=[
            AllocationObservation(allocation_id, former_owner),
            AllocationObservation(allocation_id, former_owner),
        ]
    )
    with pytest.raises(PermanentHACloudError, match="exact candidate ownership"):
        adapter.observe_cluster(
            allocation_id=allocation_id,
            former_owner=former_owner,
            candidate=candidate_owner,
            allow_missing_former=True,
        )

    adapter._read_allocation = MagicMock(side_effect=[exact_allocation])
    with pytest.raises(MissingHAComputeError):
        adapter.observe_cluster(
            allocation_id=allocation_id,
            former_owner=former_owner,
            candidate=candidate_owner,
        )


def test_cloud_mutation_replay_reuses_encoded_provider_key(
    tmp_path: Path,
) -> None:
    controller_id = "boot-a:1:stop-former-owner:node-a"
    accepted = AcceptedCloudOperation(
        action_operation_id=controller_id,
        kind="stop-instance",
        cloud_operation_id="cloud-operation-a",
    )
    journal = VMHACloudOperationJournal(tmp_path / "accepted.json")
    journal.save(accepted)
    operation = SimpleNamespace(id=accepted.cloud_operation_id, successful=lambda: True)
    request = MagicMock()
    request.wait.return_value = operation
    service = MagicMock()
    service.stop.return_value = request
    client = NebiusSDKCloudClient(object(), operation_journal=journal)

    with (
        patch.object(
            client,
            "_resume_operation",
            side_effect=_request_error(StatusCode.UNIMPLEMENTED),
        ),
        patch("nebius.api.nebius.compute.v1.InstanceServiceClient", return_value=service),
        patch("nebius_vpngw.deploy.vm_ha_cloud.wait_vm_ha_operation"),
    ):
        client.stop_instance("instance-a", controller_id)

    encoded = hashlib.sha256(controller_id.encode("utf-8")).hexdigest()
    assert service.stop.call_args.kwargs["metadata"] == (("x-idempotency-key", encoded),)
    assert journal.load() is None


def test_cloud_operation_journal_is_private_and_compare_and_clear(tmp_path: Path) -> None:
    journal = VMHACloudOperationJournal(tmp_path / "operations" / "accepted.json")
    accepted = _accepted_operation()

    journal.save(accepted)

    assert journal.load() == accepted
    assert journal.path.stat().st_mode & 0o777 == 0o600
    assert journal.path.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(ValueError, match="another VM-HA cloud operation"):
        journal.save(
            AcceptedCloudOperation(
                action_operation_id="b" * 64,
                kind=accepted.kind,
                cloud_operation_id=accepted.cloud_operation_id,
            )
        )
    with pytest.raises(ValueError, match="changed"):
        journal.clear(
            AcceptedCloudOperation(
                action_operation_id=accepted.action_operation_id,
                kind=accepted.kind,
                cloud_operation_id="foreign-operation",
            )
        )

    journal.clear(accepted)

    assert journal.load() is None


def test_cloud_mutation_persists_before_wait_and_resumes_without_resubmit(
    tmp_path: Path,
) -> None:
    journal = VMHACloudOperationJournal(tmp_path / "accepted.json")
    accepted = _accepted_operation()
    client = NebiusSDKCloudClient(object(), operation_journal=journal)
    submitted_operation = SimpleNamespace(
        id=accepted.cloud_operation_id,
        successful=lambda: True,
    )
    request = MagicMock()
    request.wait.return_value = submitted_operation
    submit = MagicMock(return_value=request)

    def fail_after_acceptance(operation: object) -> None:
        assert operation is submitted_operation
        assert journal.load() == accepted
        raise RuntimeError("operation wait interrupted")

    with (
        patch(
            "nebius_vpngw.deploy.vm_ha_cloud.wait_vm_ha_operation",
            side_effect=fail_after_acceptance,
        ),
        pytest.raises(RuntimeError, match="interrupted"),
    ):
        client._mutate(
            action_operation_id=accepted.action_operation_id,
            kind=accepted.kind,
            submit=submit,
        )

    assert journal.load() == accepted
    submit.assert_called_once_with()

    resumed_operation = SimpleNamespace(successful=lambda: True)
    resumed_client = NebiusSDKCloudClient(object(), operation_journal=journal)
    resumed_submit = MagicMock()
    with (
        patch.object(
            resumed_client,
            "_resume_operation",
            return_value=resumed_operation,
        ) as resume,
        patch("nebius_vpngw.deploy.vm_ha_cloud.wait_vm_ha_operation") as wait,
    ):
        resumed_client._mutate(
            action_operation_id=accepted.action_operation_id,
            kind=accepted.kind,
            submit=resumed_submit,
        )

    resume.assert_called_once_with(accepted.cloud_operation_id)
    wait.assert_called_once_with(resumed_operation)
    resumed_submit.assert_not_called()
    assert journal.load() is None


def test_cloud_mutation_replays_same_idempotent_request_when_lookup_is_unsupported(
    tmp_path: Path,
) -> None:
    journal = VMHACloudOperationJournal(tmp_path / "accepted.json")
    accepted = _accepted_operation()
    journal.save(accepted)
    client = NebiusSDKCloudClient(object(), operation_journal=journal)
    replayed_operation = SimpleNamespace(
        id=accepted.cloud_operation_id,
        successful=lambda: True,
    )
    replayed_request = MagicMock()
    replayed_request.wait.return_value = replayed_operation
    submit = MagicMock(return_value=replayed_request)

    with (
        patch.object(
            client,
            "_resume_operation",
            side_effect=_request_error(StatusCode.UNIMPLEMENTED),
        ),
        patch("nebius_vpngw.deploy.vm_ha_cloud.wait_vm_ha_operation") as wait,
    ):
        client._mutate(
            action_operation_id=accepted.action_operation_id,
            kind=accepted.kind,
            submit=submit,
        )

    submit.assert_called_once_with()
    wait.assert_called_once_with(replayed_operation)
    assert journal.load() is None


def test_cloud_mutation_rejects_changed_operation_identity_during_idempotent_replay(
    tmp_path: Path,
) -> None:
    journal = VMHACloudOperationJournal(tmp_path / "accepted.json")
    accepted = _accepted_operation()
    journal.save(accepted)
    client = NebiusSDKCloudClient(object(), operation_journal=journal)
    replayed_request = MagicMock()
    replayed_request.wait.return_value = SimpleNamespace(id="different-operation")

    with (
        patch.object(
            client,
            "_resume_operation",
            side_effect=_request_error(StatusCode.UNIMPLEMENTED),
        ),
        pytest.raises(AmbiguousHACloudError, match="different cloud operation identity"),
    ):
        client._mutate(
            action_operation_id=accepted.action_operation_id,
            kind=accepted.kind,
            submit=MagicMock(return_value=replayed_request),
        )

    assert journal.load() == accepted


@pytest.mark.parametrize(
    ("action_operation_id", "kind"),
    (("b" * 64, "stop-instance"), ("a" * 64, "set-alias-present")),
)
def test_cloud_mutation_rejects_a_different_pending_operation(
    tmp_path: Path,
    action_operation_id: str,
    kind: str,
) -> None:
    journal = VMHACloudOperationJournal(tmp_path / "accepted.json")
    accepted = _accepted_operation()
    journal.save(accepted)
    client = NebiusSDKCloudClient(object(), operation_journal=journal)
    submit = MagicMock()

    with pytest.raises(PermanentHACloudError, match="different accepted"):
        client._mutate(
            action_operation_id=action_operation_id,
            kind=kind,
            submit=submit,
        )

    submit.assert_not_called()
    assert journal.load() == accepted


def test_cloud_mutation_retains_receipt_when_operation_finishes_unsuccessfully(
    tmp_path: Path,
) -> None:
    journal = VMHACloudOperationJournal(tmp_path / "accepted.json")
    accepted = _accepted_operation()
    client = NebiusSDKCloudClient(object(), operation_journal=journal)

    class FailedOperation:
        id = accepted.cloud_operation_id

        def sync_wait(self, **_kwargs: object) -> None:
            return None

        def successful(self) -> bool:
            return False

    request = MagicMock()
    request.wait.return_value = FailedOperation()

    with pytest.raises(PermanentHACloudError, match="finished unsuccessfully"):
        client._mutate(
            action_operation_id=accepted.action_operation_id,
            kind=accepted.kind,
            submit=MagicMock(return_value=request),
        )

    assert journal.load() == accepted


def test_finalize_accepted_operation_uses_exact_lookup_and_compare_clear(
    tmp_path: Path,
) -> None:
    journal = VMHACloudOperationJournal(tmp_path / "accepted.json")
    accepted = _accepted_operation()
    journal.save(accepted)
    client = NebiusSDKCloudClient(object(), operation_journal=journal)
    operation = SimpleNamespace(successful=lambda: True)

    with (
        patch.object(client, "_resume_operation", return_value=operation) as resume,
        patch("nebius_vpngw.deploy.vm_ha_cloud.wait_vm_ha_operation") as wait,
    ):
        client.finalize_accepted_operation(accepted)

    resume.assert_called_once_with(accepted.cloud_operation_id)
    wait.assert_called_once_with(operation)
    assert journal.load() is None


def test_finalize_accepted_operation_never_adopts_changed_identity(tmp_path: Path) -> None:
    journal = VMHACloudOperationJournal(tmp_path / "accepted.json")
    accepted = _accepted_operation()
    journal.save(accepted)
    client = NebiusSDKCloudClient(object(), operation_journal=journal)
    changed = AcceptedCloudOperation(
        action_operation_id=accepted.action_operation_id,
        kind=accepted.kind,
        cloud_operation_id="changed-operation",
    )

    with (
        patch.object(client, "_resume_operation") as resume,
        pytest.raises(PermanentHACloudError, match="identity changed"),
    ):
        client.finalize_accepted_operation(changed)

    resume.assert_not_called()
    assert journal.load() == accepted


def test_finalize_accepted_operation_retains_unavailable_or_failed_operation(
    tmp_path: Path,
) -> None:
    journal = VMHACloudOperationJournal(tmp_path / "accepted.json")
    accepted = _accepted_operation()
    journal.save(accepted)
    client = NebiusSDKCloudClient(object(), operation_journal=journal)

    with (
        patch.object(client, "_resume_operation", side_effect=RuntimeError("unavailable")),
        pytest.raises(RuntimeError, match="unavailable"),
    ):
        client.finalize_accepted_operation(accepted)
    assert journal.load() == accepted

    with (
        patch.object(
            client,
            "_resume_operation",
            return_value=SimpleNamespace(successful=lambda: False),
        ),
        patch("nebius_vpngw.deploy.vm_ha_cloud.wait_vm_ha_operation"),
        pytest.raises(PermanentHACloudError, match="finished unsuccessfully"),
    ):
        client.finalize_accepted_operation(accepted)
    assert journal.load() == accepted
