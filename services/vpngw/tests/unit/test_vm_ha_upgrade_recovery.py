from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest
from test_vm_ha_host_recovery import _policy
from test_vm_ha_lifecycle import _missing_standby_state

from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHALifecycleJournal,
    VMHALifecycleStatus,
    VMHALifecycleStore,
    vm_ha_effective_resource_bindings,
)
from nebius_vpngw.deploy.vm_ha_upgrade_recovery import (
    ARTIFACT,
    AUTHORITY,
    READY,
    UNITS,
    authorize,
    host_digest,
    install_effect,
    publish_packages,
    recovery_authority,
    release_started,
    validate_checkpoint,
    validate_evidence,
)


def checkpoint():
    active = _missing_standby_state(prior_artifact_key=ARTIFACT, start_replacement=False)
    return replace(
        active,
        status=VMHALifecycleStatus.ACTIVATING,
        transaction=replace(
            active.transaction,
            pending_effect="activate-node-1",
            completed_effects=("install-apply-lock-node-0", "install-apply-lock-node-1"),
        ),
    )


def package_record():
    return json.dumps(
        {f"gateway-{index}": {"closure": "1" * 64, "wheels": {}} for index in (0, 1)},
        sort_keys=True,
        separators=(",", ":"),
    )


def authorize_state(state):
    return authorize(
        state,
        approval="b" * 64,
        artifact="c" * 64,
        ssh_trust="d" * 64,
        hosts="e" * 64,
        owner="node-0",
        packages=package_record(),
    )


def test_real_store_recovery_replays_every_durable_boundary_without_losing_history(tmp_path):
    original = checkpoint()
    store = VMHALifecycleStore(tmp_path / "gateway.yaml")
    store.write_verified(original)
    state = authorize_state(original)
    store.write_verified(state, predecessor_sha256=original.record_sha256)

    def reload():
        return store.read(expected_project_id="project-1", expected_gateway_name="gateway")

    assert reload() == state
    assert authorize_state(reload()) == state
    audit = recovery_authority(state)
    assert audit["superseded_effect"] == "activate-node-1"
    assert audit["prior_record"] == original.record_sha256
    assert audit["prior_artifact"] == "a" * 64
    for node in ("node-1", "node-0"):
        journal = VMHALifecycleJournal(store, reload())
        effect = install_effect(node)
        journal.begin(effect)
        # A disconnected client resumes its existing effect, not a new operation.
        resumed = VMHALifecycleJournal(store, reload())
        resumed.begin(effect)
        resumed.complete(effect)
        resumed.complete(effect)
    before = reload()
    final = publish_packages(before)
    store.write_verified(final, predecessor_sha256=before.record_sha256)
    assert publish_packages(reload()) == final
    assert final.has_same_identity(original)
    assert final.transaction.operation_id == original.transaction.operation_id
    assert set(original.transaction.completed_effects).issubset(final.transaction.completed_effects)
    assert dict(final.transaction.resource_bindings)[ARTIFACT] == "c" * 64
    assert dict(final.transaction.resource_bindings)[READY] == "c" * 64
    assert recovery_authority(final) == audit
    assert vm_ha_effective_resource_bindings(
        dict(final.transaction.resource_bindings)
    ) == vm_ha_effective_resource_bindings(dict(original.transaction.resource_bindings))
    assert vm_ha_effective_resource_bindings(
        dict(state.transaction.resource_bindings)
    ) == vm_ha_effective_resource_bindings(dict(original.transaction.resource_bindings))
    # Canonical apply resumes the superseded host effect after package publication.
    journal = VMHALifecycleJournal(store, final)
    journal.begin("activate-node-1")
    journal.complete("activate-node-1")


@pytest.mark.parametrize(
    "mutation",
    [
        "artifact",
        "identity",
        "history",
        "operation",
        "pending",
        "approval",
        "skip-install",
    ],
)
def test_recovery_writer_rejects_unrelated_changes(tmp_path, mutation):
    original = checkpoint()
    store = VMHALifecycleStore(tmp_path / "gateway.yaml")
    store.write_verified(original)
    state = authorize_state(original)
    tx = state.transaction
    if mutation == "artifact":
        bindings = dict(tx.resource_bindings, **{ARTIFACT: "f" * 64})
        state = replace(
            state, transaction=replace(tx, resource_bindings=tuple(sorted(bindings.items())))
        )
    elif mutation == "identity":
        state = replace(state, allocation_id="foreign-allocation")
    elif mutation == "history":
        state = replace(state, transaction=replace(tx, completed_effects=()))
    elif mutation == "operation":
        state = replace(state, transaction=replace(tx, operation_id="foreign-operation"))
    elif mutation == "pending":
        state = replace(state, transaction=replace(tx, pending_effect="activate-node-0"))
    elif mutation == "approval":
        state = replace(state, transaction=replace(tx, approval_digest="f" * 64))
    else:
        bindings = dict(tx.resource_bindings, **{READY: "c" * 64})
        state = replace(
            state, transaction=replace(tx, resource_bindings=tuple(sorted(bindings.items())))
        )
    with pytest.raises(ValueError):
        store.write_verified(state, predecessor_sha256=original.record_sha256)


@pytest.mark.parametrize(
    "frontier",
    [
        "verify-active-forwarding-and-routes",
        "verify-passive-unlocked-non-forwarding",
        "delete-compute",
        "create-boot-disk",
        "release-owner",
    ],
)
def test_recovery_refuses_unknown_or_release_frontier(frontier):
    state = checkpoint()
    with pytest.raises(ValueError):
        validate_checkpoint(
            replace(state, transaction=replace(state.transaction, pending_effect=frontier))
        )


@pytest.mark.parametrize(
    "frontier", ["verify-active-forwarding-and-routes", "verify-passive-unlocked-non-forwarding"]
)
def test_recovery_refuses_completed_release(frontier):
    state = checkpoint()
    with pytest.raises(ValueError):
        validate_checkpoint(
            replace(state, transaction=replace(state.transaction, completed_effects=(frontier,)))
        )


def test_recovery_forbids_owner_first_and_other_effects_until_both_packages(tmp_path):
    original = checkpoint()
    store = VMHALifecycleStore(tmp_path / "gateway.yaml")
    store.write_verified(original)
    state = authorize_state(original)
    store.write_verified(state, predecessor_sha256=original.record_sha256)
    journal = VMHALifecycleJournal(store, state)
    for effect in (install_effect("node-0"), "activate-node-1", "create-compute"):
        with pytest.raises(ValueError):
            journal.begin(effect)
    with pytest.raises(ValueError):
        publish_packages(state)
    assert ARTIFACT in dict(state.transaction.resource_bindings)
    assert READY not in dict(state.transaction.resource_bindings)


def evidence_fixture(tmp_path):
    state = checkpoint()
    evidence, generations = {}, {}
    nodes = {
        m.node_id: {"compute_id": m.compute_id, "nic": m.network_interface_name}
        for m in state.members
    }
    for member in state.members:
        peer = next(n for n in nodes if n != member.node_id)
        _, policy = _policy(tmp_path / member.node_id, member.node_id, peer, depth=1)
        generation = policy.generation_id
        digests = {key: "f" * 64 for key in ("configuration", "static_routes", "bgp_policy")}
        projection = dict(
            cluster_id=state.cluster_id,
            node_id=member.node_id,
            generation_id=generation,
            digests=digests,
            allocation_id=state.allocation_id,
            nodes=nodes,
            apply_operation_id="7" * 64,
            mtls_operation_id="8" * 64,
        )
        generations[member.node_id] = (generation, digests)
        evidence[member.node_id] = dict(
            installed=copy.deepcopy(projection),
            staged=copy.deepcopy(projection),
            policy=policy.to_dict(),
            pending=[],
            inhibition=None,
            apply_lock=dict(
                schema="nebius-vpngw/vm-ha-apply-lock-v2",
                apply_locked=True,
                cluster_id=state.cluster_id,
                node_id=member.node_id,
                generation_id=generation,
                operation_id="7" * 64,
            ),
            mtls=dict(
                state="transitioning",
                operation_kind="bootstrap",
                epoch=1,
                certificate_fingerprint=("a" if member.node_id == "node-0" else "b") * 64,
                spki_fingerprint="c" * 64,
                peer_fingerprints=[("b" if member.node_id == "node-0" else "a") * 64],
                cluster_id=state.cluster_id,
                node_id=member.node_id,
                compute_id=member.compute_id,
                operation_id="8" * 64,
                phase="local-active",
                inhibited=False,
                inhibition_operation_id=None,
            ),
            forwarding="0",
            boot_id="current-boot",
            guard=dict(data_plane_mode="blocked", guard_boot_id="current-boot"),
            units={
                unit: dict(
                    LoadState="loaded", ActiveState="inactive", Job="", MainPID="0", ControlPID="0"
                )
                for unit in UNITS
            },
            source_sha256={},
            released_inhibition=None,
            controller=dict(
                pending_action=None,
                sequence=1,
                ownership_incarnation=0,
                transfer_continuity=None,
                repair_attempt=None,
            ),
        )
    return state, evidence, generations


def test_exact_generation_locks_are_independent_of_lifecycle_operation(tmp_path):
    state, evidence, generations = evidence_fixture(tmp_path)
    assert state.transaction.operation_id != "7" * 64
    validate_evidence(state, evidence, generations, require_stopped=True)
    evidence["node-0"]["apply_lock"]["operation_id"] = state.transaction.operation_id
    with pytest.raises(ValueError, match="exact apply lock"):
        validate_evidence(state, evidence, generations, require_stopped=True)


@pytest.mark.parametrize("unsafe", [None, "pid", "job", "guard"])
def test_historical_failed_unit_requires_all_current_quiescence_proofs(tmp_path, unsafe):
    state, evidence, generations = evidence_fixture(tmp_path)
    item = evidence["node-0"]
    unit = item["units"]["frr.service"]
    unit["ActiveState"] = "failed"
    if unsafe == "pid":
        unit["MainPID"] = "42"
    elif unsafe == "job":
        unit["Job"] = "42"
    elif unsafe == "guard":
        item["guard"]["guard_boot_id"] = "old-boot"
    if unsafe is None:
        validate_evidence(state, evidence, generations, require_stopped=True)
    else:
        with pytest.raises(ValueError):
            validate_evidence(state, evidence, generations, require_stopped=True)


@pytest.mark.parametrize(
    "mutation",
    [
        "installed-identity",
        "peer-identity",
        "generation",
        "no-lock",
        "pending",
        "forwarding",
        "stale-guard",
        "timer",
        "pid",
        "job",
        "missing-unit",
        "mtls",
        "policy",
    ],
)
def test_host_recovery_fails_closed_on_drift_or_live_writers(tmp_path, mutation):
    state, evidence, generations = evidence_fixture(tmp_path)
    item = evidence["node-0"]
    if mutation == "installed-identity":
        item["installed"]["node_id"] = "node-1"
    elif mutation == "peer-identity":
        item["staged"]["nodes"]["node-1"]["compute_id"] = "foreign"
    elif mutation == "generation":
        item["staged"]["generation_id"] = "f" * 64
    elif mutation == "no-lock":
        item["apply_lock"] = None
    elif mutation == "pending":
        item["pending"] = ["accepted-cloud-operation.json"]
    elif mutation == "forwarding":
        item["forwarding"] = "1"
    elif mutation == "stale-guard":
        item["guard"]["guard_boot_id"] = "old-boot"
    elif mutation == "timer":
        item["units"]["nebius-vpngw-fix-routes.timer"]["ActiveState"] = "active"
    elif mutation == "pid":
        item["units"][UNITS[0]]["MainPID"] = "42"
    elif mutation == "job":
        item["units"][UNITS[0]]["Job"] = "42"
    elif mutation == "missing-unit":
        del item["units"][UNITS[-1]]
    elif mutation == "mtls":
        item["mtls"]["phase"] = "prepared"
    elif mutation == "policy":
        item["policy"]["phase"] = "prepared"
    with pytest.raises(ValueError):
        validate_evidence(state, evidence, generations, require_stopped=True)


def test_host_authority_is_pinned_across_package_recovery(tmp_path):
    state, evidence, generations = evidence_fixture(tmp_path)
    state = authorize(
        state,
        approval="b" * 64,
        artifact="c" * 64,
        ssh_trust="d" * 64,
        hosts=host_digest(evidence),
        owner="node-0",
        packages=package_record(),
    )
    evidence["node-0"]["source_sha256"] = {"main.py": "changed-package"}
    validate_evidence(state, evidence, generations, require_stopped=True)
    evidence["node-0"]["policy"]["updated_at"] += 1
    with pytest.raises(ValueError, match="approved host authority"):
        validate_evidence(state, evidence, generations, require_stopped=True)
    assert AUTHORITY in dict(state.transaction.resource_bindings)


def test_completed_prior_inhibition_receipt_is_audited_without_reopening_it(tmp_path):
    state, evidence, generations = evidence_fixture(tmp_path)
    item = evidence["node-0"]
    receipt = dict(
        schema="nebius-vpngw/vm-ha-standby-replacement-release-v1",
        cluster_id=state.cluster_id,
        node_id="node-0",
        generation_id=generations["node-0"][0],
        operation_id="6" * 64,
    )
    item["released_inhibition"] = receipt
    validate_evidence(state, evidence, generations, require_stopped=True)
    assert item["released_inhibition"] == receipt
    for field, foreign in (
        ("operation_id", item["apply_lock"]["operation_id"]),
        ("node_id", "foreign"),
        ("generation_id", "f" * 64),
    ):
        original = receipt[field]
        receipt[field] = foreign
        with pytest.raises(ValueError, match="retained release receipt"):
            validate_evidence(state, evidence, generations, require_stopped=True)
        receipt[field] = original


def test_resume_pins_dependency_closure_and_wheels_but_accepts_completed_downloads():
    from types import SimpleNamespace

    from nebius_vpngw.deploy.vm_ha_upgrade_recovery import package_authority, validate_packages

    plans = tuple(
        (
            f"gateway-{i}",
            SimpleNamespace(
                manifest={
                    "expected_dependencies": {"needed": "2.0"},
                    "expected_requirements": {"needed": []},
                    "dependency_wheels": {"needed-2.0-py3-none-any.whl": "f" * 64},
                }
            ),
        )
        for i in (0, 1)
    )
    artifact = SimpleNamespace(sha256="c" * 64, dependency_plans=plans)
    state = authorize(
        checkpoint(),
        approval="b" * 64,
        artifact=artifact.sha256,
        ssh_trust="d" * 64,
        hosts="e" * 64,
        owner="node-0",
        packages=package_authority(artifact),
    )
    validate_packages(state, artifact)
    manifest = plans[0][1].manifest
    manifest["dependency_wheels"]["needed-2.0-py3-none-any.whl"] = "a" * 64
    with pytest.raises(ValueError, match="dependency closure or wheel"):
        validate_packages(state, artifact)
    manifest["dependency_wheels"] = {}
    # An already completed install no longer needs downloading; its full desired
    # version/requirement closure must nevertheless remain the approved one.
    validate_packages(state, artifact)
    manifest["expected_dependencies"]["needed"] = "2.1"
    with pytest.raises(ValueError, match="dependency closure or wheel"):
        validate_packages(state, artifact)


def test_real_pruned_mtls_store_is_valid_terminal_upgrade_authority(tmp_path):
    from nebius_vpngw.agent.vm_ha.mtls import ManagedMTLSStore, MTLSOperationKind

    state, evidence, generations = evidence_fixture(tmp_path)
    stores = {node: ManagedMTLSStore(tmp_path / (node + "-mtls")) for node in evidence}
    operation = "8" * 64
    receipts = {}
    for member in state.members:
        receipts[member.node_id] = stores[member.node_id].prepare_identity(
            operation_id=operation,
            operation_kind=MTLSOperationKind.BOOTSTRAP,
            cluster_id=state.cluster_id,
            node_id=member.node_id,
            compute_id=member.compute_id,
            target_epoch=1,
        )
    for member in state.members:
        peer = next(m for m in state.members if m != member)
        store = stores[member.node_id]
        store.stage_peer_leaf(
            operation_id=operation,
            peer_node_id=peer.node_id,
            peer_compute_id=peer.compute_id,
            peer_epoch=1,
            certificate_pem=receipts[peer.node_id].certificate_pem,
        )
        store.expand_peer_trust(operation)
        store.activate_identity(operation)
        store.record_fresh_observation(
            operation,
            local_certificate_fingerprint=receipts[member.node_id].certificate_fingerprint,
            peer_certificate_fingerprint=receipts[peer.node_id].certificate_fingerprint,
            local_epoch=1,
            peer_epoch=1,
            observation_id="9" * 64,
        )
        store.commit_transaction(operation)
        store.prune_obsolete(operation)
        evidence[member.node_id]["mtls"] = store.status().to_dict()
        assert evidence[member.node_id]["mtls"]["operation_id"] is None
    validate_evidence(state, evidence, generations, require_stopped=True)
    evidence["node-0"]["mtls"]["peer_fingerprints"] = ["f" * 64]
    with pytest.raises(ValueError, match="cross-pinned"):
        validate_evidence(state, evidence, generations, require_stopped=True)


@pytest.mark.parametrize(
    "kind",
    [
        "enter-passive",
        "stop-former-owner",
        "attach-candidate",
        "enable-active",
        "repair-local-dataplane",
    ],
)
def test_only_exact_pending_local_passive_action_survives_for_controller_replay(tmp_path, kind):
    state, evidence, generations = evidence_fixture(tmp_path)
    item = evidence["node-1"]
    action = dict(
        kind=kind,
        boot_id=item["boot_id"],
        target_node_id="node-1",
        allocation_id=state.allocation_id,
        generation_id=generations["node-1"][0],
        digests=generations["node-1"][1],
        ownership_incarnation=0,
        operation_id=f"{item['boot_id']}:1:{kind}:node-1",
    )
    item["controller"]["pending_action"] = action
    if kind != "enter-passive":
        with pytest.raises(ValueError, match="competing controller"):
            validate_evidence(state, evidence, generations, require_stopped=True)
        return
    validate_evidence(state, evidence, generations, require_stopped=True)
    # Admission preserves intent; only the repaired controller may reconcile it.
    assert item["controller"]["pending_action"] == action
    for key, changed in (
        ("generation_id", "f" * 64),
        ("boot_id", "old"),
        ("operation_id", "foreign"),
    ):
        original = action[key]
        action[key] = changed
        with pytest.raises(ValueError, match="competing controller"):
            validate_evidence(state, evidence, generations, require_stopped=True)
        action[key] = original


@pytest.mark.parametrize("interrupt_write", range(1, 7))
def test_executor_resumes_after_each_durable_write_without_resource_effects(
    tmp_path, monkeypatch, interrupt_write
):
    from types import SimpleNamespace

    from nebius_vpngw import cli
    from nebius_vpngw.deploy import vm_ha_host_recovery, vm_ha_package

    initial, evidence, generations = evidence_fixture(tmp_path)
    store = VMHALifecycleStore(tmp_path / "gateway.yaml")
    store.write_verified(initial)
    instances = tuple(
        SimpleNamespace(
            hostname=m.instance_name,
            vm_ha_node=SimpleNamespace(node_id=m.node_id),
            manifest={
                "expected_dependencies": {},
                "expected_requirements": {},
                "dependency_wheels": {},
            },
        )
        for m in reversed(initial.members)
    )
    trace = []
    expected = {"main.py": "exact-source"}
    artifact = SimpleNamespace(
        sha256="c" * 64, dependency_plans=tuple((i.hostname, i) for i in instances)
    )
    approved = SimpleNamespace(artifact=artifact, digest="b" * 64)
    cloud = {"owner": "node-0"}

    def context(state):
        return cli._VMHAUpgradeRecoveryContext(
            state,
            instances,
            generations,
            copy.deepcopy(evidence),
            {
                "lifecycle": state.record_sha256,
                "hosts": host_digest(evidence),
                "cloud": cli._canonical_digest(cloud),
                "ssh_trust": "d" * 64,
                "artifact": artifact.sha256,
                "owner": "node-0",
            },
            "node-0",
            recovery_authority(state) is not None,
        )

    monkeypatch.setattr(cli, "_plan_vm_ha_upgrade_recovery", lambda **kw: context(kw["state"]))
    shared_journal = None

    def assert_ssh_authority():
        # The public apply SSH policy captures its enclosing lifecycle journal.
        # An executor-private journal leaves that callback at the predecessor
        # and rejects the first SSH read after recovery authorization.
        assert shared_journal is not None
        observed = store.read_hardened(
            expected_project_id="project-1", expected_gateway_name="gateway"
        )
        assert observed.state == shared_journal.state

    def inspect_hosts(*args):
        assert_ssh_authority()
        return copy.deepcopy(evidence)

    monkeypatch.setattr(cli, "_upgrade_recovery_host_evidence", inspect_hosts)
    monkeypatch.setattr(vm_ha_host_recovery, "host_recovery_artifact_sources", lambda _: expected)

    def verify(ssh, instance, local, **kwargs):
        assert_ssh_authority()
        assert evidence[instance.vm_ha_node.node_id]["source_sha256"] == expected

    monkeypatch.setattr(vm_ha_package, "verify_prepared", verify)
    monkeypatch.setattr(
        cli,
        "SSHPush",
        lambda **kw: SimpleNamespace(
            stop_vm_ha_mutation_services=lambda target, name, local: trace.append(("stop", name))
        ),
    )

    def install(ssh, target, instance, local, *, artifact, checkpoint_conversion=None):
        assert_ssh_authority()
        assert {name for action, name in trace if action == "stop"} == {
            i.hostname for i in instances
        }
        persisted = store.read(expected_project_id="project-1", expected_gateway_name="gateway")
        assert recovery_authority(persisted)["artifact"] == artifact.sha256
        assert (
            persisted.transaction.pending_effect == install_effect(instance.vm_ha_node.node_id)
            or install_effect(instance.vm_ha_node.node_id)
            in persisted.transaction.completed_effects
        )
        trace.append(("install", instance.hostname))
        evidence[instance.vm_ha_node.node_id]["source_sha256"] = expected

    monkeypatch.setattr(cli, "_prepare_vm_ha_agent_package", install)
    original_write = store.write_verified
    writes = 0

    def write(value, **kwargs):
        nonlocal writes
        original_write(value, **kwargs)
        writes += 1
        if writes == interrupt_write:
            raise RuntimeError("client-interrupted-after-durable-write")

    monkeypatch.setattr(store, "write_verified", write)

    def execute(state):
        nonlocal shared_journal
        shared_journal = VMHALifecycleJournal(store, state)
        return cli._execute_vm_ha_upgrade_recovery(
            context(state),
            approved=approved,
            journal=shared_journal,
            plan=object(),
            local={},
            targets={i.hostname: i.hostname for i in instances},
            credentials=object(),
            ssh_policy=SimpleNamespace(known_hosts_sha256="d" * 64),
            observe=lambda: cloud,
        )

    with pytest.raises(RuntimeError, match="client-interrupted"):
        execute(initial)
    resumed = store.read(expected_project_id="project-1", expected_gateway_name="gateway")
    if READY not in dict(resumed.transaction.resource_bindings):
        resumed = execute(resumed)
    assert dict(resumed.transaction.resource_bindings)[ARTIFACT] == artifact.sha256
    assert resumed.has_same_identity(initial)
    assert recovery_authority(resumed)["superseded_effect"] == "activate-node-1"
    assert {action for action, _ in trace} == {"stop", "install"}


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "activation-progress",
        "activation-running",
        "activation-mtls-committed",
        "missing-ready",
        "source",
        "forwarding",
        "writer",
        "lock",
        "package",
        "trust",
        "transfer",
        "repair",
        "missing-controller",
    ],
)
def test_prepared_recovery_reproves_stopped_authority_without_live_ack(
    tmp_path, monkeypatch, fault
):
    from types import SimpleNamespace

    from nebius_vpngw import cli
    from nebius_vpngw.deploy import vm_ha_host_recovery, vm_ha_package
    from nebius_vpngw.deploy.vm_ha_upgrade_recovery import package_authority

    state, evidence, generations = evidence_fixture(tmp_path)
    instances = tuple(
        SimpleNamespace(
            hostname=member.instance_name,
            vm_ha_node=SimpleNamespace(node_id=member.node_id),
            vm_ha_generation=SimpleNamespace(
                generation_id=generations[member.node_id][0],
                digests=SimpleNamespace(**generations[member.node_id][1]),
            ),
            manifest={
                "expected_dependencies": {},
                "expected_requirements": {},
                "dependency_wheels": {},
            },
        )
        for member in state.members
    )
    artifact = SimpleNamespace(
        sha256="c" * 64, dependency_plans=tuple((i.hostname, i) for i in instances)
    )
    store = VMHALifecycleStore(tmp_path / "prepared.yaml")
    store.write_verified(state)
    predecessor = state.record_sha256
    state = authorize(
        state,
        approval="b" * 64,
        artifact=artifact.sha256,
        ssh_trust="d" * 64,
        hosts=host_digest(evidence),
        owner="node-0",
        packages=package_authority(artifact),
    )
    store.write_verified(state, predecessor_sha256=predecessor)
    journal = VMHALifecycleJournal(store, state)
    for node in ("node-1", "node-0"):
        journal.begin(install_effect(node))
        journal.complete(install_effect(node))
    state = publish_packages(journal.state)
    activation_progress = isinstance(fault, str) and fault.startswith("activation-")
    if activation_progress:
        store.write_verified(state, predecessor_sha256=journal.state.record_sha256)
        resumed = VMHALifecycleJournal(store, state)
        resumed.begin("activate-node-1")
        resumed.complete("activate-node-1")
        state = resumed.state
        # A restarted controller advances its durable sequence. A later stopped
        # barrier must retain exact authority without requiring that old sequence.
        evidence["node-1"]["controller"]["sequence"] += 1
        if fault == "activation-running":
            evidence["node-1"]["units"][UNITS[0]].update(ActiveState="active", MainPID="42")
            evidence["node-1"]["guard"]["data_plane_mode"] = "passive"
        if fault == "activation-mtls-committed":
            for item in evidence.values():
                item["mtls"].update(
                    state="healthy", operation_id=None, operation_kind=None, phase=None
                )
    expected = {"main.py": "exact-source"}
    for item in evidence.values():
        item["source_sha256"] = expected
    if fault == "missing-ready":
        state = journal.state
    elif fault == "source":
        evidence["node-1"]["source_sha256"] = {}
    elif fault == "forwarding":
        evidence["node-1"]["forwarding"] = "1"
    elif fault == "writer":
        evidence["node-1"]["units"][UNITS[0]]["MainPID"] = "42"
    elif fault == "lock":
        evidence["node-1"]["apply_lock"] = None
    elif fault == "transfer":
        evidence["node-1"]["controller"]["transfer_continuity"] = {"operation_id": "competing"}
    elif fault == "repair":
        evidence["node-1"]["controller"]["repair_attempt"] = {"operation_id": "competing"}
    elif fault == "missing-controller":
        evidence["node-1"]["controller"] = None
    monkeypatch.setattr(cli, "_upgrade_recovery_host_evidence", lambda *args: evidence)
    monkeypatch.setattr(vm_ha_host_recovery, "host_recovery_artifact_sources", lambda _: expected)
    verified = []

    def verify(ssh, package, local, **kwargs):
        if fault == "package":
            raise ValueError("installed package postcondition differs")
        verified.append(package.hostname)

    monkeypatch.setattr(vm_ha_package, "verify_prepared", verify)

    def run(*, require_stopped=True):
        # No controller acknowledgement method exists on this stopped fixture.
        cli._verify_vm_ha_prepared_upgrade_recovery(
            SimpleNamespace(
                _ssh_policy=SimpleNamespace(
                    known_hosts_sha256=("e" if fault == "trust" else "d") * 64
                )
            ),
            state=state,
            artifact=artifact,
            instances=instances,
            targets={i.hostname: i.hostname for i in instances},
            local={},
            require_stopped=require_stopped,
        )

    if fault and not activation_progress:
        with pytest.raises(ValueError):
            run()
    else:
        if fault == "activation-running":
            run(require_stopped=False)
            with pytest.raises(ValueError, match="fence|writer"):
                run()
            evidence["node-1"]["units"][UNITS[0]].update(ActiveState="inactive", MainPID="0")
            evidence["node-1"]["guard"]["data_plane_mode"] = "blocked"
            verified.clear()
        run()
        assert verified == [i.hostname for i in instances]


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize(
    "effect", ["verify-active-forwarding-and-routes", "verify-passive-unlocked-non-forwarding"]
)
def test_release_frontier_resumes_canonical_apply_without_reopening_recovery(pending, effect):
    state = checkpoint()
    assert not release_started(state)
    state = replace(
        state,
        transaction=replace(
            state.transaction,
            pending_effect=effect if pending else None,
            completed_effects=() if pending else (effect,),
        ),
    )
    assert release_started(state)
    with pytest.raises(ValueError, match="frontier"):
        validate_checkpoint(state)


@pytest.mark.parametrize("effect", ["activate-release-node-1", "stage-unlock-node-0"])
def test_release_words_in_node_names_are_not_release_effects(effect):
    state = checkpoint()
    state = replace(
        state,
        transaction=replace(state.transaction, pending_effect=None, completed_effects=(effect,)),
    )
    assert not release_started(state)
