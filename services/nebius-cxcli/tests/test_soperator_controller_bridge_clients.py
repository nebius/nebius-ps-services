from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration


def _result(
    args: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> migration.SoperatorMigrationCommandResult:
    return migration.SoperatorMigrationCommandResult(
        args=tuple(args),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _pod(
    name: str,
    uid: str,
    *,
    container: str,
    node: str,
    instance: str,
    component: str,
) -> dict[str, Any]:
    return {
        "metadata": {
            "namespace": "soperator",
            "name": name,
            "uid": uid,
            "labels": {
                "app.kubernetes.io/instance": instance,
                "app.kubernetes.io/component": component,
            },
        },
        "spec": {
            "nodeName": node,
            "containers": [{"name": container}],
        },
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [
                {
                    "name": container,
                    "ready": True,
                    "restartCount": 0,
                    "containerID": f"containerd://{uid}",
                    "state": {"running": {"startedAt": "2026-07-12T00:00:00Z"}},
                }
            ],
        },
    }


def _source_config() -> str:
    config, _originals = migration._bridge_slurm_config_with_timeouts(  # noqa: SLF001
        "ClusterName=cluster-a\nSlurmctldHost=controller-0(source-controller-svc)\n"
    )
    return config


def _target_handoff_config() -> str:
    return migration.source_controller_config_with_bridge_hosts(
        "ClusterName=cluster-a\nStateSaveLocation=/mnt/controller-spool/current\n"
    )


def _runner(
    *,
    pods: Sequence[Mapping[str, Any]],
    configs: Mapping[str, str],
    nodes: Sequence[Mapping[str, Any]] = (),
) -> tuple[migration.SoperatorMigrationCommandRunner, list[tuple[str, ...]]]:
    calls: list[tuple[str, ...]] = []

    def run(
        args: Sequence[str],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
        input_text: str | None = None,
    ) -> migration.SoperatorMigrationCommandResult:
        del check, timeout_seconds, input_text
        command = tuple(str(item) for item in args)
        calls.append(command)
        if command[-5:] == ("get", "pods", "-o", "json", "--request-timeout=20s"):
            return _result(command, stdout=json.dumps({"items": list(pods)}))
        if command[-5:] == ("get", "nodes", "-o", "json", "--request-timeout=20s"):
            return _result(command, stdout=json.dumps({"items": list(nodes)}))
        if "exec" not in command:
            raise AssertionError(command)
        pod_name = command[command.index("exec") + 1]
        remote = command[command.index("--") + 1 :]
        if remote == ("cat", migration._SOPERATOR_LEGACY_SLURM_CONF):  # noqa: SLF001
            return _result(command, stdout=configs[pod_name])
        if remote[-2:] == ("scontrol", "reconfigure"):
            return _result(command)
        if remote[-2:] == ("scontrol", "ping"):
            return _result(
                command,
                stdout="Slurmctld(primary) at controller-0 is UP\n",
            )
        if remote[-3:] == ("scontrol", "show", "config"):
            return _result(command, stdout="ClusterName = cluster-a\n")
        raise AssertionError(command)

    return run, calls


def _journal(source_login: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "cluster_name": "cluster-a",
        "login_session_handoff": {
            "protected_pods": [
                {
                    "name": source_login["metadata"]["name"],
                    "uid": source_login["metadata"]["uid"],
                    "container_id": source_login["status"]["containerStatuses"][0]["containerID"],
                }
            ]
        },
    }


def test_source_client_propagation_covers_protected_login_and_every_slurmd() -> None:
    source_login = _pod(
        "login-0",
        "source-login-uid",
        container="sshd",
        node="source-login-node",
        instance="source",
        component="login",
    )
    workers = [
        _pod(
            f"worker-{index}",
            f"worker-{index}-uid",
            container="slurmd",
            node=f"worker-node-{index}",
            instance="source",
            component="worker",
        )
        for index in range(2)
    ]
    config = _source_config()
    runner, calls = _runner(
        pods=[source_login, *workers],
        configs={pod["metadata"]["name"]: config for pod in [source_login, *workers]},
    )
    journal = _journal(source_login)
    owner: dict[str, Any] = {}

    lines = migration._prove_controller_bridge_client_configuration(  # noqa: SLF001
        journal=journal,
        proof_owner=owner,
        proof_key="client_propagation",
        proof_stage="before-source-fence",
        expected_contract=migration._controller_bridge_client_config_contract(config),  # noqa: SLF001
        required_hosts=migration._controller_bridge_client_config_contract(config)[  # noqa: SLF001
            "controller_hosts"
        ],
        target_ref="target",
        source_clients=True,
        require_raised_timeouts=True,
        require_live_rpc=True,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=None,
    )

    proof = owner["client_propagation"]
    assert proof["status"] == "verified"
    assert proof["live_rpc_verified"] is True
    assert [(item["role"], item["uid"]) for item in proof["consumers"]] == [
        ("source-login", "source-login-uid"),
        ("source-worker", "worker-0-uid"),
        ("source-worker", "worker-1-uid"),
    ]
    assert all(item["config_sha256"] == proof["config_sha256"] for item in proof["consumers"])
    assert not any(command[-2:] == ("scontrol", "reconfigure") for command in calls)
    assert "1 login and 2 worker" in lines[0]


def test_source_client_propagation_rejects_one_stale_worker_jail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_login = _pod(
        "login-0",
        "source-login-uid",
        container="sshd",
        node="source-login-node",
        instance="source",
        component="login",
    )
    worker = _pod(
        "worker-0",
        "worker-0-uid",
        container="slurmd",
        node="worker-node-0",
        instance="source",
        component="worker",
    )
    config = _source_config()
    stale = config.replace("SlurmctldHost=cxcli-slurm-controller-bridge-1", "# missing=")
    runner, _calls = _runner(
        pods=[source_login, worker],
        configs={"login-0": config, "worker-0": stale},
    )
    monkeypatch.setattr(migration.time, "sleep", lambda _seconds: None)

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="worker-0 has stale or non-canonical",
    ):
        migration._prove_controller_bridge_client_configuration(  # noqa: SLF001
            journal=_journal(source_login),
            proof_owner={},
            proof_key="client_propagation",
            proof_stage="before-source-fence",
            expected_contract=migration._controller_bridge_client_config_contract(config),  # noqa: SLF001
            required_hosts=migration._controller_bridge_client_config_contract(config)[  # noqa: SLF001
                "controller_hosts"
            ],
            target_ref="target",
            source_clients=True,
            require_raised_timeouts=True,
            require_live_rpc=True,
            kube_context="context",
            command_runner=runner,
            checkpoint_writer=None,
        )


def test_target_client_propagation_waits_for_jailed_config_convergence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_login = _pod(
        "login-0",
        "login-uid",
        container="sshd",
        node="login-node",
        instance="cluster-a",
        component="login",
    )
    worker = _pod(
        "worker-0",
        "worker-uid",
        container="slurmd",
        node="worker-node",
        instance="cluster-a",
        component="worker",
    )
    expected = _target_handoff_config()
    stale = expected.replace("SlurmctldHost=cxcli-slurm-controller-bridge-1", "# stale=")
    reads = {"worker-0": 0}
    sleeps: list[int] = []
    pods = [target_login, worker]

    def runner(
        args: Sequence[str],
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        command = tuple(str(item) for item in args)
        if command[-5:] == ("get", "pods", "-o", "json", "--request-timeout=20s"):
            return _result(command, stdout=json.dumps({"items": pods}))
        pod_name = command[command.index("exec") + 1]
        remote = command[command.index("--") + 1 :]
        if remote == ("cat", migration._SOPERATOR_LEGACY_SLURM_CONF):  # noqa: SLF001
            if pod_name == "worker-0":
                reads[pod_name] += 1
                return _result(command, stdout=stale if reads[pod_name] == 1 else expected)
            return _result(command, stdout=expected)
        if remote[-2:] == ("scontrol", "ping"):
            return _result(command, stdout="Slurmctld(primary) at bridge-0 is UP\n")
        if remote[-3:] == ("scontrol", "show", "config"):
            return _result(command, stdout="ClusterName = cluster-a\n")
        raise AssertionError(command)

    monkeypatch.setattr(migration.time, "sleep", sleeps.append)
    contract = migration._controller_bridge_client_config_contract(expected)  # noqa: SLF001

    migration._prove_controller_bridge_client_configuration(  # noqa: SLF001
        journal={"cluster_name": "cluster-a"},
        proof_owner={},
        proof_key="client_propagation",
        proof_stage="before-target-takeover",
        expected_contract=contract,
        required_hosts=contract["controller_hosts"],
        target_ref="cluster-a",
        source_clients=False,
        require_raised_timeouts=False,
        require_live_rpc=True,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=None,
    )

    assert reads == {"worker-0": 2}
    assert sleeps == [5]


def test_target_client_propagation_selects_only_exact_target_login_and_workers() -> None:
    source_login = _pod(
        "source-login-0",
        "source-login-uid",
        container="sshd",
        node="source-login-node",
        instance="source",
        component="login",
    )
    source_worker = _pod(
        "source-worker-0",
        "source-worker-uid",
        container="slurmd",
        node="source-worker-node",
        instance="source",
        component="worker",
    )
    target_login = _pod(
        "target-login-0",
        "target-login-uid",
        container="sshd",
        node="target-login-node",
        instance="target",
        component="login",
    )
    target_worker = _pod(
        "target-worker-0",
        "target-worker-uid",
        container="slurmd",
        node="target-worker-node",
        instance="target",
        component="worker",
    )
    config = _target_handoff_config()
    pods = [source_login, source_worker, target_login, target_worker]
    runner, _calls = _runner(
        pods=pods,
        configs={pod["metadata"]["name"]: config for pod in pods},
    )
    owner: dict[str, Any] = {}

    migration._prove_controller_bridge_client_configuration(  # noqa: SLF001
        journal=_journal(source_login),
        proof_owner=owner,
        proof_key="client_handoff_propagation",
        proof_stage="before-target-takeover",
        expected_contract=migration._controller_bridge_client_config_contract(config),  # noqa: SLF001
        required_hosts=migration._controller_bridge_client_config_contract(config)[  # noqa: SLF001
            "controller_hosts"
        ],
        target_ref="target",
        source_clients=False,
        require_raised_timeouts=False,
        require_live_rpc=True,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=None,
    )

    assert [
        (item["role"], item["uid"]) for item in owner["client_handoff_propagation"]["consumers"]
    ] == [
        ("target-login", "target-login-uid"),
        ("target-worker", "target-worker-uid"),
    ]


def test_checkpointed_target_client_proof_revalidates_identity_after_bridge_fence() -> None:
    source_login = _pod(
        "source-login-0",
        "source-login-uid",
        container="sshd",
        node="source-login-node",
        instance="source",
        component="login",
    )
    target_login = _pod(
        "target-login-0",
        "target-login-uid",
        container="sshd",
        node="target-login-node",
        instance="target",
        component="login",
    )
    target_worker = _pod(
        "target-worker-0",
        "target-worker-uid",
        container="slurmd",
        node="target-worker-node",
        instance="target",
        component="worker",
    )
    config = _target_handoff_config()
    runner, calls = _runner(
        pods=[source_login, target_login, target_worker],
        configs={"target-login-0": config, "target-worker-0": config},
    )
    owner: dict[str, Any] = {}
    kwargs = {
        "journal": _journal(source_login),
        "proof_owner": owner,
        "proof_key": "client_handoff_propagation",
        "proof_stage": "before-target-takeover",
        "expected_contract": migration._controller_bridge_client_config_contract(config),  # noqa: SLF001
        "required_hosts": migration._controller_bridge_client_config_contract(config)[  # noqa: SLF001
            "controller_hosts"
        ],
        "target_ref": "target",
        "source_clients": False,
        "require_raised_timeouts": False,
        "kube_context": "context",
        "command_runner": runner,
        "checkpoint_writer": None,
    }
    migration._prove_controller_bridge_client_configuration(  # noqa: SLF001
        **kwargs,
        require_live_rpc=True,
    )
    rpc_count = sum("scontrol" in command for command in calls)

    migration._prove_controller_bridge_client_configuration(  # noqa: SLF001
        **kwargs,
        require_live_rpc=False,
    )

    assert sum("scontrol" in command for command in calls) == rpc_count
    assert owner["client_handoff_propagation"]["live_rpc_verified"] is True


def test_checkpointed_target_client_proof_accepts_provider_journaled_worker_successor() -> None:
    source_login = _pod(
        "source-login-0",
        "source-login-uid",
        container="sshd",
        node="source-login-node",
        instance="source",
        component="login",
    )
    target_login = _pod(
        "target-login-0",
        "target-login-uid",
        container="sshd",
        node="target-login-node",
        instance="target",
        component="login",
    )
    old_worker = _pod(
        "target-worker-0",
        "old-worker-uid",
        container="slurmd",
        node="old-worker-node",
        instance="target",
        component="worker",
    )
    replacement_worker = _pod(
        "target-worker-0",
        "replacement-worker-uid",
        container="slurmd",
        node="replacement-worker-node",
        instance="target",
        component="worker",
    )
    config = _target_handoff_config()
    owner: dict[str, Any] = {}
    common = {
        "journal": _journal(source_login),
        "proof_owner": owner,
        "proof_key": "client_handoff_propagation",
        "proof_stage": "before-in-place-controller-roll",
        "expected_contract": migration._controller_bridge_client_config_contract(config),  # noqa: SLF001
        "required_hosts": migration._controller_bridge_client_config_contract(config)[  # noqa: SLF001
            "controller_hosts"
        ],
        "target_ref": "target",
        "source_clients": False,
        "require_raised_timeouts": False,
        "require_live_rpc": True,
        "kube_context": "context",
        "checkpoint_writer": None,
    }
    initial_runner, _calls = _runner(
        pods=[source_login, target_login, old_worker],
        configs={"target-login-0": config, "target-worker-0": config},
    )
    migration._prove_controller_bridge_client_configuration(  # noqa: SLF001
        **common,
        command_runner=initial_runner,
    )

    replacement_runner, _calls = _runner(
        pods=[source_login, target_login, replacement_worker],
        configs={"target-login-0": config, "target-worker-0": config},
        nodes=[
            {
                "metadata": {
                    "name": "replacement-worker-node",
                    "uid": "replacement-node-uid",
                }
            }
        ],
    )
    migration._prove_controller_bridge_client_configuration(  # noqa: SLF001
        **common,
        command_runner=replacement_runner,
        allowed_target_worker_replacement_node_uids=("replacement-node-uid",),
    )

    proof = owner["client_handoff_propagation"]
    worker = next(item for item in proof["consumers"] if item["role"] == "target-worker")
    assert worker["uid"] == "replacement-worker-uid"
    assert proof["consumer_successors"] == [
        {
            "kind": "checkpointed-worker-provider-replacement",
            "previous_consumers_sha256": proof["consumer_successors"][0][
                "previous_consumers_sha256"
            ],
            "replacement_consumers_sha256": proof["consumer_successors"][0][
                "replacement_consumers_sha256"
            ],
            "workers": [
                {
                    "name": "target-worker-0",
                    "previous_uid": "old-worker-uid",
                    "replacement_uid": "replacement-worker-uid",
                    "replacement_node_uid": "replacement-node-uid",
                }
            ],
            "accepted_at": proof["consumer_successors"][0]["accepted_at"],
        }
    ]


def test_checkpointed_target_client_proof_rejects_unjournaled_worker_successor() -> None:
    source_login = _pod(
        "source-login-0",
        "source-login-uid",
        container="sshd",
        node="source-login-node",
        instance="source",
        component="login",
    )
    target_login = _pod(
        "target-login-0",
        "target-login-uid",
        container="sshd",
        node="target-login-node",
        instance="target",
        component="login",
    )
    old_worker = _pod(
        "target-worker-0",
        "old-worker-uid",
        container="slurmd",
        node="old-worker-node",
        instance="target",
        component="worker",
    )
    replacement_worker = _pod(
        "target-worker-0",
        "replacement-worker-uid",
        container="slurmd",
        node="replacement-worker-node",
        instance="target",
        component="worker",
    )
    config = _target_handoff_config()
    owner: dict[str, Any] = {}
    common = {
        "journal": _journal(source_login),
        "proof_owner": owner,
        "proof_key": "client_handoff_propagation",
        "proof_stage": "before-in-place-controller-roll",
        "expected_contract": migration._controller_bridge_client_config_contract(config),  # noqa: SLF001
        "required_hosts": migration._controller_bridge_client_config_contract(config)[  # noqa: SLF001
            "controller_hosts"
        ],
        "target_ref": "target",
        "source_clients": False,
        "require_raised_timeouts": False,
        "require_live_rpc": True,
        "kube_context": "context",
        "checkpoint_writer": None,
    }
    initial_runner, _calls = _runner(
        pods=[source_login, target_login, old_worker],
        configs={"target-login-0": config, "target-worker-0": config},
    )
    migration._prove_controller_bridge_client_configuration(  # noqa: SLF001
        **common,
        command_runner=initial_runner,
    )
    replacement_runner, _calls = _runner(
        pods=[source_login, target_login, replacement_worker],
        configs={"target-login-0": config, "target-worker-0": config},
        nodes=[
            {
                "metadata": {
                    "name": "replacement-worker-node",
                    "uid": "replacement-node-uid",
                }
            }
        ],
    )

    with pytest.raises(RuntimeError, match="recovery-required"):
        migration._prove_controller_bridge_client_configuration(  # noqa: SLF001
            **common,
            command_runner=replacement_runner,
            allowed_target_worker_replacement_node_uids=("different-node-uid",),
        )


def test_final_target_clients_prove_exact_single_controller_host() -> None:
    source_login = _pod(
        "source-login-0",
        "source-login-uid",
        container="sshd",
        node="source-login-node",
        instance="source",
        component="login",
    )
    target_login = _pod(
        "target-login-0",
        "target-login-uid",
        container="sshd",
        node="target-login-node",
        instance="target",
        component="login",
    )
    target_worker = _pod(
        "target-worker-0",
        "target-worker-uid",
        container="slurmd",
        node="target-worker-node",
        instance="target",
        component="worker",
    )
    config = "ClusterName=cluster-a\nSlurmctldHost=controller-0\n"
    runner, _calls = _runner(
        pods=[source_login, target_login, target_worker],
        configs={"target-login-0": config, "target-worker-0": config},
    )
    owner: dict[str, Any] = {}

    migration._prove_controller_bridge_client_configuration(  # noqa: SLF001
        journal=_journal(source_login),
        proof_owner=owner,
        proof_key="final_client_propagation",
        proof_stage="final-target-singleton",
        expected_contract=migration._controller_bridge_client_config_contract(config),  # noqa: SLF001
        required_hosts=("controller-0",),
        target_ref="target",
        source_clients=False,
        require_raised_timeouts=False,
        require_live_rpc=True,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=None,
    )

    assert owner["final_client_propagation"]["controller_hosts"] == ["controller-0"]


def test_client_contract_rejects_duplicate_controller_hosts() -> None:
    with pytest.raises(RuntimeError, match="duplicate-free"):
        migration._controller_bridge_client_config_contract(  # noqa: SLF001
            "ClusterName=cluster-a\nSlurmctldHost=controller-0\nSlurmctldHost=controller-0\n"
        )
