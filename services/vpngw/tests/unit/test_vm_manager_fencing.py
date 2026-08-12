from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import pytest

from nebius_vpngw.deploy.vm_ha_cloud import (
    AllocationOwner,
    AmbiguousHACloudError,
    InstanceCloudState,
    PermanentHACloudError,
    RetryableHACloudError,
    TransferStage,
    VMHACloudAdapter,
    allocation_observation,
    instance_cloud_state,
)


def _instance(state: object) -> SimpleNamespace:
    return SimpleNamespace(status=SimpleNamespace(state=state))


def _allocation(owner: AllocationOwner | None) -> SimpleNamespace:
    network_interface = None
    if owner is not None:
        network_interface = SimpleNamespace(
            instance_id=owner.instance_id,
            name=owner.network_interface_name,
        )
    return SimpleNamespace(
        status=SimpleNamespace(
            state="ASSIGNED" if owner else "ALLOCATED",
            assignment=SimpleNamespace(network_interface=network_interface, load_balancer=None),
        )
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("STOPPED", InstanceCloudState.STOPPED),
        ("RUNNING", InstanceCloudState.RUNNING),
        ("STOPPING", InstanceCloudState.STOPPING),
        ("ERROR", InstanceCloudState.ERROR),
        ("STARTING", InstanceCloudState.TRANSITIONAL),
        ("UPDATING", InstanceCloudState.TRANSITIONAL),
        ("something-new", InstanceCloudState.UNKNOWN),
        (None, InstanceCloudState.UNKNOWN),
    ],
)
def test_instance_cloud_state_is_fail_closed(raw: object, expected: InstanceCloudState) -> None:
    assert instance_cloud_state(_instance(raw)) is expected


def test_transfer_enforces_fencing_and_exact_ownership_order() -> None:
    former = AllocationOwner("old", "eth0")
    candidate = AllocationOwner("new", "eth0")
    instance_states = deque(["RUNNING", "STOPPING", "STOPPED"])
    owner = former
    events: list[str] = []

    def read_instance(instance_id: str) -> SimpleNamespace:
        assert instance_id == "old"
        state = instance_states.popleft() if len(instance_states) > 1 else instance_states[0]
        events.append(f"instance:{state}")
        return _instance(state)

    def stop_instance(instance_id: str) -> None:
        events.append(f"stop:{instance_id}")

    def read_allocation(allocation_id: str) -> SimpleNamespace:
        assert allocation_id == "private-1"
        value = "absent" if owner is None else f"{owner.instance_id}/{owner.network_interface_name}"
        events.append(f"owner:{value}")
        return _allocation(owner)

    def set_allocation(instance_id: str, nic_name: str, allocation_id: str | None) -> None:
        nonlocal owner
        events.append(f"set:{instance_id}/{nic_name}:{allocation_id or 'absent'}")
        owner = AllocationOwner(instance_id, nic_name) if allocation_id else None

    adapter = VMHACloudAdapter(
        instance_reader=read_instance,
        instance_stopper=stop_instance,
        allocation_reader=read_allocation,
        allocation_setter=set_allocation,
        attempts=4,
        poll_interval=0,
        sleeper=lambda _: None,
    )

    proof = adapter.transfer_private_allocation(
        allocation_id="private-1",
        former_owner=former,
        candidate=candidate,
    )

    assert proof.stages == (
        TransferStage.FORMER_OWNER_STOPPED,
        TransferStage.FORMER_ATTACHMENT_ABSENT,
        TransferStage.CANDIDATE_ATTACHMENT_EXACT,
        TransferStage.OWNERSHIP_CONFIRMED,
    )
    assert events.index("instance:STOPPED") < events.index("owner:absent")
    assert events.index("owner:absent") < events.index("owner:new/eth0")
    assert events.count("owner:new/eth0") == 2


@pytest.mark.parametrize("state", ["ERROR", "UNSPECIFIED", "CREATING"])
def test_ambiguous_or_transitional_state_never_reaches_allocation(state: str) -> None:
    allocation_reads = 0

    def read_allocation(_: str) -> SimpleNamespace:
        nonlocal allocation_reads
        allocation_reads += 1
        return _allocation(None)

    adapter = VMHACloudAdapter(
        instance_reader=lambda _: _instance(state),
        instance_stopper=lambda _: None,
        allocation_reader=read_allocation,
        allocation_setter=lambda *_: None,
        attempts=1,
        poll_interval=0,
    )

    expected_error = AmbiguousHACloudError if state != "CREATING" else RetryableHACloudError
    with pytest.raises(expected_error):
        adapter.transfer_private_allocation(
            allocation_id="private-1",
            former_owner=AllocationOwner("old", "eth0"),
            candidate=AllocationOwner("new", "eth0"),
        )
    assert allocation_reads == 0


def test_stopping_timeout_is_retryable_and_never_detaches() -> None:
    setters: list[tuple[object, ...]] = []
    adapter = VMHACloudAdapter(
        instance_reader=lambda _: _instance("STOPPING"),
        instance_stopper=lambda _: None,
        allocation_reader=lambda _: _allocation(AllocationOwner("old", "eth0")),
        allocation_setter=lambda *args: setters.append(args),
        attempts=2,
        poll_interval=0,
        sleeper=lambda _: None,
    )

    with pytest.raises(RetryableHACloudError, match="did not become STOPPED"):
        adapter.transfer_private_allocation(
            allocation_id="private-1",
            former_owner=AllocationOwner("old", "eth0"),
            candidate=AllocationOwner("new", "eth0"),
        )
    assert setters == []


def test_unexpected_allocation_owner_is_permanent_and_not_mutated() -> None:
    setters: list[tuple[object, ...]] = []
    adapter = VMHACloudAdapter(
        instance_reader=lambda _: _instance("STOPPED"),
        instance_stopper=lambda _: None,
        allocation_reader=lambda _: _allocation(AllocationOwner("foreign", "eth9")),
        allocation_setter=lambda *args: setters.append(args),
        attempts=2,
        poll_interval=0,
    )

    with pytest.raises(PermanentHACloudError, match="unexpected owner"):
        adapter.transfer_private_allocation(
            allocation_id="private-1",
            former_owner=AllocationOwner("old", "eth0"),
            candidate=AllocationOwner("new", "eth0"),
        )
    assert setters == []


def test_detached_allocation_skips_detach_and_attaches_candidate() -> None:
    candidate = AllocationOwner("new", "eth0")
    owner: AllocationOwner | None = None
    setters: list[tuple[str, str, str | None]] = []

    def set_allocation(instance_id: str, nic_name: str, allocation_id: str | None) -> None:
        nonlocal owner
        setters.append((instance_id, nic_name, allocation_id))
        owner = AllocationOwner(instance_id, nic_name) if allocation_id else None

    adapter = VMHACloudAdapter(
        instance_reader=lambda _: _instance("STOPPED"),
        instance_stopper=lambda _: None,
        allocation_reader=lambda _: _allocation(owner),
        allocation_setter=set_allocation,
        attempts=2,
        poll_interval=0,
    )

    adapter.transfer_private_allocation(
        allocation_id="private-1",
        former_owner=AllocationOwner("old", "eth0"),
        candidate=candidate,
    )
    assert setters == [("new", "eth0", "private-1")]


def test_replay_with_exact_candidate_owner_is_read_only() -> None:
    candidate = AllocationOwner("new", "eth0")
    setters: list[tuple[object, ...]] = []
    adapter = VMHACloudAdapter(
        instance_reader=lambda _: _instance("STOPPED"),
        instance_stopper=lambda _: None,
        allocation_reader=lambda _: _allocation(candidate),
        allocation_setter=lambda *args: setters.append(args),
        attempts=2,
        poll_interval=0,
    )

    proof = adapter.transfer_private_allocation(
        allocation_id="private-1",
        former_owner=AllocationOwner("old", "eth0"),
        candidate=candidate,
    )
    assert proof.candidate == candidate
    assert setters == []


def test_transfer_rejects_candidate_on_former_compute_instance() -> None:
    former = AllocationOwner("same-instance", "eth0")
    candidate = AllocationOwner("same-instance", "eth1")
    adapter = VMHACloudAdapter(
        instance_reader=lambda _: _instance("STOPPED"),
        instance_stopper=lambda _: None,
        allocation_reader=lambda _: _allocation(candidate),
        allocation_setter=lambda *_: None,
        attempts=1,
        poll_interval=0,
    )

    with pytest.raises(PermanentHACloudError, match="distinct Compute instances"):
        adapter.transfer_private_allocation(
            allocation_id="private-1",
            former_owner=former,
            candidate=candidate,
        )


def test_stale_attachment_read_times_out_without_duplicate_detach() -> None:
    former = AllocationOwner("old", "eth0")
    setters: list[tuple[object, ...]] = []
    adapter = VMHACloudAdapter(
        instance_reader=lambda _: _instance("STOPPED"),
        instance_stopper=lambda _: None,
        allocation_reader=lambda _: _allocation(former),
        allocation_setter=lambda *args: setters.append(args),
        attempts=3,
        poll_interval=0,
        sleeper=lambda _: None,
    )

    with pytest.raises(RetryableHACloudError, match="remained attached"):
        adapter.transfer_private_allocation(
            allocation_id="private-1",
            former_owner=former,
            candidate=AllocationOwner("new", "eth0"),
        )
    assert setters == [("old", "eth0", None)]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("deadline"), RetryableHACloudError),
        (ConnectionError("unavailable"), RetryableHACloudError),
        (PermissionError("denied"), PermanentHACloudError),
        (RuntimeError("malformed response"), AmbiguousHACloudError),
    ],
)
def test_cloud_read_errors_are_typed_and_block_promotion(
    error: Exception,
    expected: type[Exception],
) -> None:
    def failed_read(_: str) -> SimpleNamespace:
        raise error

    adapter = VMHACloudAdapter(
        instance_reader=failed_read,
        instance_stopper=lambda _: None,
        allocation_reader=lambda _: _allocation(None),
        allocation_setter=lambda *_: None,
        attempts=1,
        poll_interval=0,
    )

    with pytest.raises(expected):
        adapter.require_stopped("old")


def test_incomplete_allocation_assignment_is_ambiguous() -> None:
    allocation = SimpleNamespace(
        status=SimpleNamespace(
            state="ASSIGNED",
            assignment=SimpleNamespace(
                network_interface=SimpleNamespace(instance_id="old", name=""),
                load_balancer=None,
            ),
        )
    )
    with pytest.raises(AmbiguousHACloudError, match="incomplete assignment"):
        allocation_observation("private-1", allocation)


@pytest.mark.parametrize("state", ["CREATING", "DELETING", "STATE_UNSPECIFIED", None])
def test_transitional_or_unknown_allocation_state_is_ambiguous(state: object) -> None:
    allocation = SimpleNamespace(
        status=SimpleNamespace(state=state, assignment=None),
    )
    with pytest.raises(AmbiguousHACloudError, match="not stable"):
        allocation_observation("private-1", allocation)
