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


def test_source_client_propagation_rejects_one_stale_worker_jail() -> None:
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
