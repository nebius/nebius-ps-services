"""Combined maintenance cannot be completed by generic infrastructure recovery."""

import copy

import pytest

from nebius_cxcli.soperator_checks import SoperatorChecksExecution
from nebius_cxcli.soperator_checks_maintenance import SoperatorChecksMaintenance
from test_soperator_checks_execution import Cluster, execution
from test_soperator_checks_execution import policy as policy


def maintenance(tmp_path, policy):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    states = {"gpu": "DOWN", "hidden": "DOWN"}
    original = cluster.slurm

    def slurm(command):
        if command == "scontrol show partition -o":
            return "\n".join(
                f"PartitionName={name} State={state}" for name, state in states.items()
            )
        if command.startswith("scontrol update PartitionName="):
            cluster.writes.append(("partition", command))
            fields = dict(x.split("=", 1) for x in command.split()[2:])
            states[fields["PartitionName"]] = fields["State"]
            return ""
        return original(command)

    runner.slurm = slurm
    calls = []
    composed = SoperatorChecksMaintenance(
        runner,
        True,
        [{"name": "gpu"}, {"name": "hidden", "config": "Hidden=YES State=UP"}],
        lambda: "reserve",
        lambda: calls.append("restore"),
        lambda: calls.append("recover"),
        lambda: calls.append("verify"),
        lambda: calls.append("restored-policy"),
    )
    return runner, cluster, states, calls, composed


@pytest.mark.parametrize("path", ["restore", "recover"])
def test_both_mutation_paths_bind_reservation_restore_partitions_and_verify(tmp_path, policy, path):
    runner, cluster, states, calls, composed = maintenance(tmp_path, policy)
    with pytest.raises(RuntimeError, match="not bound"):
        composed.verify()
    assert not calls
    assert not cluster.writes
    result = getattr(composed, path)()
    assert calls == [path]
    assert states == {"gpu": "UP", "hidden": "UP"}
    assert result["checks"]["reservation"]["fingerprint"] == runner.state["reservationFingerprint"]
    before = copy.deepcopy(cluster.writes)
    composed.verify()
    assert cluster.writes == before
    states["gpu"] = "DOWN"
    with pytest.raises(RuntimeError, match="partition restoration is incomplete"):
        composed.verify()


def test_recovery_finishes_after_reservation_and_one_partition_were_written(tmp_path, policy):
    runner, cluster, states, calls, composed = maintenance(tmp_path, policy)
    original = runner.slurm

    def interrupted(command):
        result = original(command)
        if command == "scontrol update PartitionName=gpu State=UP":
            raise KeyboardInterrupt
        return result

    runner.slurm = interrupted
    with pytest.raises(KeyboardInterrupt):
        composed.restore()
    assert not calls
    assert states == {"gpu": "UP", "hidden": "DOWN"}
    runner.slurm = original
    composed.recover()
    assert calls == ["recover"]
    assert states == {"gpu": "UP", "hidden": "UP"}


def test_final_restoration_verifies_policy_after_owned_reservation_release(tmp_path, policy):
    runner, cluster, states, calls, composed = maintenance(tmp_path, policy)
    runner.state["phase"] = "restored"
    composed.verify()
    assert calls == ["restored-policy", "verify"]
    assert not cluster.writes


@pytest.mark.parametrize("path", ["verify", "recover", "restore"])
def test_release_checkpoint_uses_owner_proof_before_any_topology_or_reservation_reentry(
    tmp_path, policy, path
):
    runner, cluster, states, calls, composed = maintenance(tmp_path, policy)
    runner.state["scheduleRelease"] = {"status": "released"}
    composed.before_checks = lambda _: pytest.fail("released topology cannot be entered again")
    observed = []
    composed.released_checks = lambda mutate: observed.append(mutate)
    getattr(composed, path)()
    assert observed == [path != "verify"]
    assert calls == [path] and not cluster.writes


def test_release_checkpoint_without_owner_verifier_fails_before_infrastructure(tmp_path, policy):
    runner, cluster, states, calls, composed = maintenance(tmp_path, policy)
    runner.state["scheduleRelease"] = {"status": "released"}
    with pytest.raises(RuntimeError, match="no owner verifier"):
        composed.verify()
    assert not calls and not cluster.writes


@pytest.mark.parametrize("mutation", [None, "fingerprint", "policy", "started", "foreign-user"])
def test_sealed_handoff_carries_only_reservation_and_release_obligation(tmp_path, policy, mutation):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    observed = runner._reservation("reserve")
    handoff = {
        "operation": "predecessor",
        "receiptSha256": "sha256:old",
        "policy": policy.sha256,
        "predecessorPolicy": policy.sha256,
        "reservation": "reserve",
        "fingerprint": observed["fingerprint"],
    }
    if mutation == "fingerprint":
        handoff["fingerprint"] = "changed"
    elif mutation == "policy":
        handoff["policy"] = "changed"
    elif mutation == "started":
        runner.state["jobs"] = {"job": {}}
    elif mutation == "foreign-user":
        cluster.users = "root,soperatorchecks"
    if mutation:
        with pytest.raises(RuntimeError):
            runner.adopt_install_reservation(handoff)
        assert not runner.path.exists()
        return
    runner.adopt_install_reservation(handoff)
    resumed = SoperatorChecksExecution(
        policy=policy,
        operation_id="test-operation",
        receipt_path=runner.path,
        kubernetes=cluster.kube,
        slurm=cluster.slurm,
        assert_authority=lambda: None,
    )
    resumed.adopt_install_reservation(handoff)
    assert resumed.state["installReservationIntent"] is True
    assert resumed.state["reservation"] == "reserve"
    assert resumed.state["jobs"] == {}
    assert resumed.state["phase"] == "planned"
    assert "acceptance" not in resumed.state
    assert not cluster.writes


@pytest.mark.parametrize("path", ["restore", "recover", "verify"])
def test_effective_deferral_precedes_install_infrastructure_actions(tmp_path, policy, path):
    runner, cluster, states, calls, composed = maintenance(tmp_path, policy)
    cluster.crons["gpu-fryer"]["spec"]["suspend"] = False
    with pytest.raises(RuntimeError, match="deferral changed"):
        getattr(composed, path)()
    assert not calls and not cluster.writes
    assert states == {"gpu": "DOWN", "hidden": "DOWN"}
