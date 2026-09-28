"""Recovery locates its exact local owner without reviving unrelated attempts."""

import base64
import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli, nsight_recover_command, nsight_upgrade_recovery
from nebius_cxcli.deployment_cli import DeployOptions
from nebius_cxcli.deployment_local import LocalObjectStore
from nebius_cxcli.deployment_state import DeploymentGeneration, DeploymentState, digest
from nebius_cxcli.deployment_workflow import _semantic_controls
from test_deployment_campaign import paths
from test_deployment_state import settings
from test_nsight_profiling import profiling_config
from test_nsight_recovery import failed_attempt


@pytest.fixture
def interrupted_upgrade(tmp_path, monkeypatch):
    local = paths(tmp_path / "project")
    store = LocalObjectStore(tmp_path / "journals")
    generation = DeploymentGeneration({"runtime_config": profiling_config()}, {})
    identity = {"cluster_id": "cluster-id", "kubernetes_uid": "kube-uid"}
    controls = DeployOptions(target_ref="cluster").controls()
    attempt = digest({"generation": generation.identity, "controls": _semantic_controls(controls)})[
        7:
    ]
    state = DeploymentState(store, settings(), attempt=attempt, assert_held=lambda: None)
    chain, *_ = failed_attempt()
    operation = digest("operation")
    prefix = f"{local.path_tenant_folder}/{local.path_project_folder}/generated/reports/"
    application = {
        "schema": "nebius-cxcli.deployment-applications.v1",
        "generation": generation.identity,
        "selected": ["cluster"],
        "targets": {
            "cluster": {
                "identity": identity,
                "desiredBundle": digest("bundle"),
                "status": "executing",
            }
        },
    }
    mirror = {
        "schema": "nebius-cxcli.soperator-recovery-journal.v4",
        "operationId": operation,
        "clusterId": identity["cluster_id"],
        "kubernetesUid": identity["kubernetes_uid"],
        "status": "active",
        "stages": {"rootfs-nsight-admit": {"nsight": chain}},
    }
    recovery = {
        prefix + "deployment-applications.json": base64.b64encode(
            json.dumps(application).encode()
        ).decode(),
        prefix + f"soperator-recovery-{operation[7:27]}.json": base64.b64encode(
            json.dumps(mirror).encode()
        ).decode(),
    }
    record = state.begin(
        generation,
        plan={"controls": controls, "semanticPlan": {"selectedTargets": ["cluster"]}},
        recovery=recovery,
    )
    target = {"target_ref": "cluster"}
    monkeypatch.setattr(
        cli, "_load_deploy_context_readonly", lambda _: ({}, local, generation.manifest)
    )
    monkeypatch.setattr(cli, "_resolve_selected_deploy_targets", lambda *a, **kw: [target])
    monkeypatch.setattr(
        "nebius_cxcli.terraform_backend.backend_settings_from_config", lambda _: settings()
    )
    monkeypatch.setattr(LocalObjectStore, "for_project", lambda _: store)
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *a, **kw: {cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx"},
    )
    monkeypatch.setattr(
        cli, "_read_kube_system_namespace_uid", lambda **kw: identity["kubernetes_uid"]
    )
    calls = []

    def recover(*args, **kwargs):
        kwargs["fence"]()
        calls.append(kwargs)

    monkeypatch.setattr(nsight_upgrade_recovery, "recover_upgrade", recover)
    return local, state, record, calls


@pytest.mark.parametrize("dry_run", [True, False])
def test_explicit_recovery_reaches_generic_owner_and_preserves_checkpoint(
    interrupted_upgrade, monkeypatch, dry_run
):
    from nebius_cxcli import deployment_cli

    local, state, record, calls = interrupted_upgrade
    monkeypatch.setattr(
        deployment_cli,
        "deployment_execution",
        lambda **kw: nullcontext(SimpleNamespace(assert_held=lambda: None)),
    )
    monkeypatch.setattr(
        nsight_recover_command, "SoperatorOperationLocalLock", lambda _: nullcontext()
    )
    nsight_recover_command.recover_profiling(
        local.config_path,
        target_ref="cluster",
        stage="admit",
        job_uid="failed-uid",
        dry_run=dry_run,
    )
    assert len(calls) == 1
    assert calls[0]["state"].record_key == state.record_key
    assert calls[0]["record"] == record
    assert state.read() == record


@pytest.mark.parametrize("case", ["unrelated", "duplicate", "wrong-identity", "wrong-name"])
def test_recovery_filters_history_and_rejects_ambiguous_or_changed_owner(interrupted_upgrade, case):
    local, state, record, calls = interrupted_upgrade
    if case in {"unrelated", "duplicate"}:
        payload = copy.deepcopy(record.value)
        payload["active"]["plan"]["controls"]["skipValidations"] = True
        if case == "unrelated":
            payload["active"]["recovery"] = {}
        controls = payload["active"]["plan"]["controls"]
        attempt = digest(
            {
                "generation": payload["active"]["generation"],
                "controls": _semantic_controls(controls),
            }
        )[7:]
        other = DeploymentState(state.store, settings(), attempt=attempt, assert_held=lambda: None)
        state.store.write(other.record_key, payload, etag=None)
    elif case == "wrong-name":
        path = state.store.root / state.record_key
        path.rename(path.with_name("f" * 64 + ".json"))
    else:
        payload = copy.deepcopy(record.value)
        archive = payload["active"]["recovery"]
        key = next(k for k in archive if "soperator-recovery-" in k)
        mirror = json.loads(base64.b64decode(archive[key]))
        mirror["kubernetesUid"] = "another-cluster"
        archive[key] = base64.b64encode(json.dumps(mirror).encode()).decode()
        state.store.write(state.record_key, payload, etag=record.etag)
    if case == "unrelated":
        nsight_recover_command.recover_profiling(
            local.config_path,
            target_ref="cluster",
            stage="admit",
            job_uid="failed-uid",
            dry_run=True,
        )
        assert len(calls) == 1
    else:
        with pytest.raises(RuntimeError, match="ambiguous|identity|attempt"):
            nsight_recover_command.recover_profiling(
                local.config_path,
                target_ref="cluster",
                stage="admit",
                job_uid="failed-uid",
                dry_run=True,
            )
        assert not calls


def test_recovery_rechecks_selected_record_after_acquiring_local_owner(
    interrupted_upgrade, monkeypatch
):
    from contextlib import contextmanager

    from nebius_cxcli import deployment_cli

    local, state, record, calls = interrupted_upgrade

    @contextmanager
    def acquire(**kwargs):
        state.checkpoint(record, stage="changed", evidence={"verified": True})
        yield SimpleNamespace(assert_held=lambda: None)

    monkeypatch.setattr(deployment_cli, "deployment_execution", acquire)
    monkeypatch.setattr(
        nsight_recover_command, "SoperatorOperationLocalLock", lambda _: nullcontext()
    )
    with pytest.raises(RuntimeError, match="changed during recovery admission"):
        nsight_recover_command.recover_profiling(
            local.config_path, target_ref="cluster", stage="admit", job_uid="failed-uid"
        )
    assert not calls


def test_missing_predecessor_never_selects_an_unrelated_attempt(interrupted_upgrade):
    local, state, record, calls = interrupted_upgrade
    with pytest.raises(RuntimeError, match="No active frozen deployment attempt"):
        nsight_recover_command.recover_profiling(
            local.config_path,
            target_ref="cluster",
            stage="admit",
            job_uid="unrelated-uid",
            dry_run=True,
        )
    assert state.read() == record and not calls


@pytest.mark.parametrize(
    "invalid",
    [b"interrupted JSON", b'{"schema":"nebius-cxcli.deployment.v2","active":{},"accepted":null}'],
)
def test_unrelated_corrupt_attempt_does_not_hide_exact_recovery_owner(interrupted_upgrade, invalid):
    local, state, record, calls = interrupted_upgrade
    other = state.store.root / state.prefix / "attempts" / ("0" * 64 + ".json")
    other.write_bytes(invalid)
    nsight_recover_command.recover_profiling(
        local.config_path, target_ref="cluster", stage="admit", job_uid="failed-uid", dry_run=True
    )
    assert len(calls) == 1 and state.read() == record and other.read_bytes() == invalid
