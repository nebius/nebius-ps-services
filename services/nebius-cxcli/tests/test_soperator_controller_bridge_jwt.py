from __future__ import annotations

import base64
import copy
import hashlib
import inspect
import json
import subprocess
from collections.abc import Sequence
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.soperator_controller_bridge import (
    BridgePlacementDomain,
    BridgePlan,
    BridgeSourceBinding,
    new_bridge_journal,
    validate_bridge_journal,
)
from nebius_cxcli.soperator_migration import SoperatorMigrationCommandResult

_IMAGE = "registry.example/slurm@sha256:" + "a" * 64
_MATERIAL_NAME = "controller-material"
_MATERIAL_KEY = "fixture.material"
_SOURCE_BYTES = b"value-a"
_CHANGED_BYTES = b"value-b"
_SOURCE_VALUE = base64.b64encode(_SOURCE_BYTES).decode("ascii")
_CHANGED_VALUE = base64.b64encode(_CHANGED_BYTES).decode("ascii")
_LIVE_KEY_SHA256 = hashlib.sha256(_SOURCE_BYTES).hexdigest()
_JWT_KEY_PATH = "/run/material/fixture.material"
_SLURM_CONFIG = (
    "AuthAltTypes=auth/jwt\n"
    f"AuthAltParameters=jwt_key={_JWT_KEY_PATH}\n"
    "StateSaveLocation=/mnt/controller-spool/current\n"
)


class _JwtRunner:
    def __init__(
        self,
        *,
        pod: dict[str, Any],
        material: dict[str, Any],
        workload: dict[str, Any] | None = None,
    ) -> None:
        self.pod = pod
        self.material = material
        self.workload = workload or _target_workload(pod, gated=True)
        self.calls: list[tuple[str, ...]] = []
        self.token_smoke_calls = 0
        self.fail_token_smoke = False
        self.live_key_sha256 = _LIVE_KEY_SHA256
        self.slurm_config = _SLURM_CONFIG

    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(args)
        self.calls.append(command)
        if "pod/controller-0" in command:
            payload = self.pod
        elif "statefulset.apps.kruise.io/controller" in command:
            payload = self.workload
        elif "secret" in command and _MATERIAL_NAME in command:
            payload = self.material
        elif command[-3:] == ("scontrol", "show", "config"):
            return SoperatorMigrationCommandResult(
                args=command,
                returncode=0,
                stdout=self.slurm_config,
            )
        elif "cxcli-jwt-proof" in command:
            token_smoke = any("scontrol token lifespan=60" in item for item in command)
            if token_smoke:
                self.token_smoke_calls += 1
            return SoperatorMigrationCommandResult(
                args=command,
                returncode=97 if token_smoke and self.fail_token_smoke else 0,
                stdout=(
                    "" if token_smoke and self.fail_token_smoke else f"{self.live_key_sha256}\n"
                ),
            )
        else:  # pragma: no cover - a new command is a contract change
            raise AssertionError(command)
        return SoperatorMigrationCommandResult(
            args=command,
            returncode=0,
            stdout=json.dumps(payload),
        )


def _source_material(*, value: str = _SOURCE_VALUE) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "namespace": "soperator",
            "name": _MATERIAL_NAME,
            "uid": "controller-material-uid",
            "resourceVersion": "17",
        },
        "data": {_MATERIAL_KEY: value},
    }


def _target_pod(*, gated: bool = True) -> dict[str, Any]:
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "namespace": "soperator",
            "name": "controller-0",
            "uid": "target-controller-pod-uid",
            "ownerReferences": [{"uid": "target-controller-workload-uid"}],
        },
        "spec": {
            "nodeName": "controller-node",
            "volumes": [{"name": "material", "secret": {"secretName": _MATERIAL_NAME}}],
            "containers": [
                {
                    "name": "slurmctld",
                    "image": _IMAGE,
                    "volumeMounts": [{"name": "material", "mountPath": "/run/material"}],
                }
            ],
        },
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [
                {
                    "name": "slurmctld",
                    "imageID": "registry.example/slurm@sha256:" + "a" * 64,
                    "ready": True,
                    "restartCount": 0,
                }
            ],
        },
    }
    if gated:
        gate = migration.target_controller_gate_values({})["slurmNodes"]["controller"]["slurmctld"]
        container = pod["spec"]["containers"][0]
        container["command"] = copy.deepcopy(gate["command"])
        container["args"] = copy.deepcopy(gate["args"])
    return pod


def _target_workload(pod: dict[str, Any], *, gated: bool) -> dict[str, Any]:
    pod_spec = copy.deepcopy(pod["spec"])
    pod_spec["containers"][0].pop("command", None)
    pod_spec["containers"][0].pop("args", None)
    if gated:
        gate = migration.target_controller_gate_values({})["slurmNodes"]["controller"]["slurmctld"]
        pod_spec["containers"][0]["command"] = copy.deepcopy(gate["command"])
        pod_spec["containers"][0]["args"] = copy.deepcopy(gate["args"])
    return {
        "apiVersion": "apps.kruise.io/v1beta1",
        "kind": "StatefulSet",
        "metadata": {
            "namespace": "soperator",
            "name": "controller",
            "uid": "target-controller-workload-uid",
            "resourceVersion": "23",
        },
        "spec": {"replicas": 1, "template": {"spec": pod_spec}},
    }


def _journal_and_source() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    material = _source_material()
    fingerprint = migration._controller_bridge_resource_fingerprint(  # noqa: SLF001
        (material,),
        names=(_MATERIAL_NAME,),
        data_keys=(_MATERIAL_KEY,),
    )
    source_binding = BridgeSourceBinding(
        namespace="soperator",
        slurmcluster_name="old-cluster",
        slurmcluster_uid="source-cluster-uid",
        controller_workload_name="controller",
        controller_workload_uid="source-controller-workload-uid",
        controller_pod_name="controller-0",
        controller_pod_uid="source-controller-pod-uid",
        controller_pvc_name="controller-state",
        controller_pvc_uid="controller-state-pvc-uid",
        controller_pv_name="controller-state-pv",
        controller_pv_uid="controller-state-pv-uid",
        jail_pvc_name="jail-pvc",
        jail_pvc_uid="jail-pvc-uid",
        jail_pv_name="jail-pv",
        jail_pv_uid="jail-pv-uid",
        jail_filesystem_id="filesystem-jail",
        slurm_image_digest=_IMAGE,
        slurm_version="24.11.4",
        configuration_fingerprint="b" * 64,
        munge_fingerprint="c" * 64,
        jwt_fingerprint=fingerprint,
    )
    plan = BridgePlan(
        campaign_fingerprint="d" * 64,
        cluster_id="cluster-id",
        cluster_name="old-cluster",
        source_kubernetes_version="1.31",
        source_slurm_image=_IMAGE,
        target_slurm_image="registry.example/slurm@sha256:" + "e" * 64,
        source_slurm_version="24.11.4",
        target_slurm_version="25.11.0",
        state_save_location="/mnt/controller-spool/current",
        controller_spool_attachment={
            "existing_filesystem": {"id": "filesystem-state"},
            "mount_tag": "controller-spool",
        },
        jail_attachment={
            "existing_filesystem": {"id": "filesystem-jail"},
            "mount_tag": "jail-rootfs",
        },
        placement_domains=(
            BridgePlacementDomain.external(
                name="bridge-a", role="external-a", template={"fixed_node_count": 1}
            ),
            BridgePlacementDomain.external(
                name="bridge-b", role="external-b", template={"fixed_node_count": 1}
            ),
        ),
    )
    journal = new_bridge_journal(
        source=source_binding,
        plan=plan,
        authority_epoch="source-epoch",
    )
    source = {
        "slurmcluster": {"namespace": "soperator"},
        "jwt": {
            "object_names": [_MATERIAL_NAME],
            "data_keys": [_MATERIAL_KEY],
            "fingerprint": fingerprint,
        },
    }
    migration._capture_controller_bridge_jwt_material_contract(  # noqa: SLF001
        journal=journal,
        source=source,
        source_resources=(material,),
    )
    takeover = journal["target_singleton_takeover"]
    takeover.update(
        {
            "target_ref": "target-cluster",
            "controller_pod_uid": "target-controller-pod-uid",
            "controller_workload_uid": "target-controller-workload-uid",
        }
    )
    journal["authority"].update({"epoch": "bridge-target-epoch", "owner": "bridge-target"})
    journal["stage"] = migration.BridgeStage.TARGET_HA_ACTIVE.value
    return journal, source, material


def _preflight(journal: dict[str, Any], runner: _JwtRunner) -> None:
    migration._preflight_target_singleton_jwt_material(  # noqa: SLF001
        journal=journal,
        target_ref="target-cluster",
        slurm_config=_SLURM_CONFIG,
        kube_context="external-context",
        command_runner=runner,
        checkpoint_writer=None,
    )


def _activate_target(journal: dict[str, Any], runner: _JwtRunner) -> None:
    journal["stage"] = migration.BridgeStage.PLANNED.value
    journal["authority"].update({"epoch": "target-singleton-epoch", "owner": "target-singleton"})
    runner.pod = _target_pod(gated=False)
    runner.workload = _target_workload(runner.pod, gated=False)


def test_target_singleton_jwt_material_is_bound_without_secret_bytes() -> None:
    journal, _source, material = _journal_and_source()
    runner = _JwtRunner(pod=_target_pod(), material=material)
    writes: list[str] = []

    migration._preflight_target_singleton_jwt_material(  # noqa: SLF001
        journal=journal,
        target_ref="target-cluster",
        slurm_config=_SLURM_CONFIG,
        kube_context="external-context",
        command_runner=runner,
        checkpoint_writer=lambda: writes.append("write"),
    )
    _activate_target(journal, runner)
    migration._revalidate_target_singleton_jwt_material(  # noqa: SLF001
        journal=journal,
        target_ref="target-cluster",
        target_pod=runner.pod,
        kube_context="external-context",
        command_runner=runner,
        checkpoint_writer=lambda: writes.append("write"),
    )

    validate_bridge_journal(journal)
    serialized = json.dumps(journal, sort_keys=True)
    assert _SOURCE_VALUE not in serialized
    assert journal["target_singleton_takeover"]["jwt_material_proof"]["status"] == "verified"
    assert writes == ["write", "write", "write", "write"]
    probe_scripts = [
        call[call.index("-ec") + 1] for call in runner.calls if "cxcli-jwt-proof" in call
    ]
    assert len(probe_scripts) == 2
    for script in probe_scripts:
        subprocess.run(["/bin/sh", "-n", "-c", script], check=True)


def test_target_singleton_jwt_material_allows_runtime_image_id_resolution_change() -> None:
    journal, _source, material = _journal_and_source()
    runner = _JwtRunner(pod=_target_pod(), material=material)
    _preflight(journal, runner)
    preflight = journal["target_singleton_takeover"]["jwt_material_preflight"]

    _activate_target(journal, runner)
    resolved_image_id = "registry.example/slurm@sha256:" + "b" * 64
    runner.pod["status"]["containerStatuses"][0]["imageID"] = resolved_image_id
    migration._revalidate_target_singleton_jwt_material(  # noqa: SLF001
        journal=journal,
        target_ref="target-cluster",
        target_pod=runner.pod,
        kube_context="external-context",
        command_runner=runner,
        checkpoint_writer=None,
    )

    proof = journal["target_singleton_takeover"]["jwt_material_proof"]
    assert proof["status"] == "verified"
    assert proof["controller_container_image"] == preflight["controller_container_image"]
    assert proof["controller_container_image_id"] == resolved_image_id
    assert proof["controller_container_image_id"] != preflight["controller_container_image_id"]
    validate_bridge_journal(journal)


def test_target_singleton_jwt_material_mismatch_fails_without_disclosure() -> None:
    journal, _source, material = _journal_and_source()
    runner = _JwtRunner(pod=_target_pod(), material=material)
    _preflight(journal, runner)
    _activate_target(journal, runner)
    changed = copy.deepcopy(material)
    changed["data"][_MATERIAL_KEY] = _CHANGED_VALUE
    runner.material = changed

    with pytest.raises(RuntimeError, match="JWT Secret identity or content differs") as exc_info:
        migration._revalidate_target_singleton_jwt_material(  # noqa: SLF001
            journal=journal,
            target_ref="target-cluster",
            target_pod=runner.pod,
            kube_context="external-context",
            command_runner=runner,
            checkpoint_writer=None,
        )

    assert _SOURCE_VALUE not in str(exc_info.value)
    assert _CHANGED_VALUE not in str(exc_info.value)
    assert journal["target_singleton_takeover"]["jwt_material_proof"] == {}


def test_target_singleton_jwt_material_resume_revalidates_live_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _source, material = _journal_and_source()
    runner = _JwtRunner(pod=_target_pod(), material=material)
    times = iter(
        (
            "2026-07-13T09:59:00Z",
            "2026-07-13T10:00:00Z",
            "2026-07-13T10:01:00Z",
            "2026-07-13T10:02:00Z",
            "2026-07-13T10:03:00Z",
        )
    )
    monkeypatch.setattr(migration, "_utc_now", lambda: next(times))

    _preflight(journal, runner)
    _activate_target(journal, runner)
    migration._revalidate_target_singleton_jwt_material(  # noqa: SLF001
        journal=journal,
        target_ref="target-cluster",
        target_pod=runner.pod,
        kube_context="external-context",
        command_runner=runner,
        checkpoint_writer=None,
    )
    migration._revalidate_target_singleton_jwt_material(  # noqa: SLF001
        journal=journal,
        target_ref="target-cluster",
        kube_context="external-context",
        command_runner=runner,
        checkpoint_writer=None,
    )

    proof = journal["target_singleton_takeover"]["jwt_material_proof"]
    assert proof["verified_at"] == "2026-07-13T10:02:00Z"
    assert proof["revalidated_at"] == "2026-07-13T10:03:00Z"
    assert runner.token_smoke_calls == 2
    assert any("pod/controller-0" in call for call in runner.calls)


def test_target_singleton_jwt_material_adopts_fully_reproven_pod_successor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _source, material = _journal_and_source()
    runner = _JwtRunner(pod=_target_pod(), material=material)
    _preflight(journal, runner)
    _activate_target(journal, runner)
    migration._revalidate_target_singleton_jwt_material(  # noqa: SLF001
        journal=journal,
        target_ref="target-cluster",
        target_pod=runner.pod,
        kube_context="external-context",
        command_runner=runner,
        checkpoint_writer=None,
    )
    original_uid = runner.pod["metadata"]["uid"]
    locked_index_image_id = "registry.example/slurm@sha256:" + "b" * 64
    journal["target_image_lock"]["index_digest"] = "sha256:" + "b" * 64
    runner.pod["metadata"]["uid"] = "target-controller-successor-uid"
    runner.pod["status"]["containerStatuses"][0]["imageID"] = locked_index_image_id
    proofs: list[str] = []
    monkeypatch.setattr(
        migration,
        "_prove_replacement_target_singleton_adoption",
        lambda **kwargs: proofs.append(str(kwargs["pod_uid"])),
    )

    migration._revalidate_target_singleton_jwt_material(  # noqa: SLF001
        journal=journal,
        target_ref="target-cluster",
        target_pod=runner.pod,
        kube_context="external-context",
        command_runner=runner,
        checkpoint_writer=None,
    )

    takeover = journal["target_singleton_takeover"]
    assert takeover["controller_pod_uid"] == "target-controller-successor-uid"
    assert takeover["jwt_material_proof"]["controller_pod_uid"] == (
        "target-controller-successor-uid"
    )
    assert takeover["controller_pod_successors"][-1]["prior_pod_uid"] == original_uid
    assert (
        takeover["controller_pod_successors"][-1]["controller_container_image_id"]
        == locked_index_image_id
    )
    assert proofs == ["target-controller-successor-uid"]
    validate_bridge_journal(journal)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda pod: pod["metadata"].update({"ownerReferences": [{"uid": "other"}]}), "workload"),
        (
            lambda pod: pod["status"]["containerStatuses"][0].update(
                {"imageID": "registry.example/slurm@sha256:" + "f" * 64}
            ),
            "immutable image",
        ),
        (
            lambda pod: pod["status"]["containerStatuses"][0].update({"restartCount": 1}),
            "restart",
        ),
    ),
)
def test_target_singleton_jwt_material_rejects_unproven_pod_successor(
    mutation: Any,
    message: str,
) -> None:
    journal, _source, material = _journal_and_source()
    runner = _JwtRunner(pod=_target_pod(), material=material)
    _preflight(journal, runner)
    _activate_target(journal, runner)
    migration._revalidate_target_singleton_jwt_material(  # noqa: SLF001
        journal=journal,
        target_ref="target-cluster",
        target_pod=runner.pod,
        kube_context="external-context",
        command_runner=runner,
        checkpoint_writer=None,
    )
    original_uid = runner.pod["metadata"]["uid"]
    runner.pod["metadata"]["uid"] = "target-controller-successor-uid"
    mutation(runner.pod)

    with pytest.raises(RuntimeError, match=message):
        migration._revalidate_target_singleton_jwt_material(  # noqa: SLF001
            journal=journal,
            target_ref="target-cluster",
            target_pod=runner.pod,
            kube_context="external-context",
            command_runner=runner,
            checkpoint_writer=None,
        )

    takeover = journal["target_singleton_takeover"]
    assert takeover["controller_pod_uid"] == original_uid
    assert "controller_pod_successors" not in takeover


def test_target_jwt_preflight_rejects_an_unmounted_or_sidecar_only_secret() -> None:
    journal, _source, material = _journal_and_source()
    pod = _target_pod()
    pod["spec"]["volumes"].append(
        {"name": "copied-key", "configMap": {"name": "untrusted-key-copy"}}
    )
    pod["spec"]["containers"][0]["volumeMounts"] = [
        {"name": "copied-key", "mountPath": "/run/material"}
    ]
    pod["spec"]["containers"].append(
        {
            "name": "sidecar",
            "image": _IMAGE,
            "volumeMounts": [{"name": "material", "mountPath": "/run/source-secret"}],
        }
    )
    runner = _JwtRunner(
        pod=pod,
        material=material,
        workload=_target_workload(pod, gated=True),
    )

    with pytest.raises(RuntimeError, match="mount exactly one Secret source"):
        _preflight(journal, runner)


def test_target_jwt_preflight_rejects_an_ungated_template() -> None:
    journal, _source, material = _journal_and_source()
    pod = _target_pod()
    runner = _JwtRunner(
        pod=pod,
        material=material,
        workload=_target_workload(pod, gated=False),
    )

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="canonical.*command gate"):
        _preflight(journal, runner)


def test_target_jwt_preflight_rejects_a_secret_subpath_mount() -> None:
    journal, _source, material = _journal_and_source()
    pod = _target_pod()
    pod["spec"]["containers"][0]["volumeMounts"][0]["subPath"] = _MATERIAL_KEY
    runner = _JwtRunner(
        pod=pod,
        material=material,
        workload=_target_workload(pod, gated=True),
    )

    with pytest.raises(RuntimeError, match="cannot use subPath"):
        _preflight(journal, runner)


def test_jwt_preflight_cannot_be_rewritten_after_bridge_fencing() -> None:
    journal, _source, material = _journal_and_source()
    runner = _JwtRunner(pod=_target_pod(), material=material)
    _preflight(journal, runner)
    frozen = copy.deepcopy(journal["target_singleton_takeover"]["jwt_material_preflight"])
    journal["stage"] = migration.BridgeStage.BRIDGE_FENCED.value
    runner.workload = _target_workload(runner.pod, gated=False)

    with pytest.raises(RuntimeError, match="only while the target-version bridge"):
        _preflight(journal, runner)

    assert journal["target_singleton_takeover"]["jwt_material_preflight"] == frozen


def test_target_jwt_proof_requires_functional_token_smoke() -> None:
    journal, _source, material = _journal_and_source()
    runner = _JwtRunner(pod=_target_pod(), material=material)
    _preflight(journal, runner)
    _activate_target(journal, runner)
    migration._revalidate_target_singleton_jwt_material(  # noqa: SLF001
        journal=journal,
        target_ref="target-cluster",
        target_pod=runner.pod,
        kube_context="external-context",
        command_runner=runner,
        checkpoint_writer=None,
    )
    assert runner.token_smoke_calls == 1
    runner.fail_token_smoke = True

    with pytest.raises(RuntimeError, match="hash and token smoke failed"):
        migration._revalidate_target_singleton_jwt_material(  # noqa: SLF001
            journal=journal,
            target_ref="target-cluster",
            kube_context="external-context",
            command_runner=runner,
            checkpoint_writer=None,
        )

    assert runner.token_smoke_calls == 2


def test_target_jwt_preflight_rejects_secret_mount_bytes_that_do_not_match() -> None:
    journal, _source, material = _journal_and_source()
    pod = _target_pod()
    pod["spec"]["volumes"].append({"name": "other", "emptyDir": {}})
    pod["spec"]["containers"][0]["volumeMounts"].append(
        {"name": "other", "mountPath": "/run/other"}
    )
    runner = _JwtRunner(
        pod=pod,
        material=material,
        workload=_target_workload(pod, gated=True),
    )
    runner.live_key_sha256 = "f" * 64
    config = (
        "AuthAltTypes=auth/jwt\n"
        "AuthAltParameters=jwt_key=/run/other/active.key\n"
        "StateSaveLocation=/mnt/controller-spool/current\n"
    )

    with pytest.raises(RuntimeError, match="mounted JWT Secret file differs"):
        migration._preflight_target_singleton_jwt_material(  # noqa: SLF001
            journal=journal,
            target_ref="target-cluster",
            slurm_config=config,
            kube_context="external-context",
            command_runner=runner,
            checkpoint_writer=None,
        )


def test_target_jwt_preflight_binds_upstream_secret_and_entrypoint_paths() -> None:
    journal, _source, material = _journal_and_source()
    pod = _target_pod()
    pod["spec"]["volumes"][0]["secret"]["items"] = [{"key": _MATERIAL_KEY, "path": "rest_jwt.key"}]
    pod["spec"]["volumes"].append({"name": "spool", "emptyDir": {}})
    pod["spec"]["containers"][0]["volumeMounts"] = [
        {"name": "material", "mountPath": "/mnt/rest-jwt-key", "readOnly": True},
        {"name": "spool", "mountPath": "/var/spool/slurmctld"},
    ]
    runner = _JwtRunner(
        pod=pod,
        material=material,
        workload=_target_workload(pod, gated=True),
    )
    config = (
        "AuthAltTypes=auth/jwt\n"
        "AuthAltParameters=jwt_key=/var/spool/slurmctld/jwt_hs256.key\n"
        "StateSaveLocation=/var/spool/slurmctld\n"
    )

    migration._preflight_target_singleton_jwt_material(  # noqa: SLF001
        journal=journal,
        target_ref="target-cluster",
        slurm_config=config,
        kube_context="external-context",
        command_runner=runner,
        checkpoint_writer=None,
    )

    proof = journal["target_singleton_takeover"]["jwt_material_preflight"]
    assert proof["jwt_secret_file_path"] == "/mnt/rest-jwt-key/rest_jwt.key"
    assert proof["jwt_key_path"] == "/var/spool/slurmctld/jwt_hs256.key"
    journal["stage"] = migration.BridgeStage.PLANNED.value
    journal["authority"].update({"epoch": "target-singleton-epoch", "owner": "target-singleton"})
    active_pod = copy.deepcopy(pod)
    active_container = active_pod["spec"]["containers"][0]
    active_container.pop("command", None)
    active_container.pop("args", None)
    runner.pod = active_pod
    runner.workload = _target_workload(active_pod, gated=False)
    runner.slurm_config = config

    migration._revalidate_target_singleton_jwt_material(  # noqa: SLF001
        journal=journal,
        target_ref="target-cluster",
        target_pod=active_pod,
        kube_context="external-context",
        command_runner=runner,
        checkpoint_writer=None,
    )

    active_probe = next(
        call[call.index("-ec") + 1] for call in reversed(runner.calls) if "cxcli-jwt-proof" in call
    )
    assert "/proc/self/mountinfo" in active_probe


def test_jwt_key_path_supports_schedmd_fallback_and_rejects_jwks_only() -> None:
    assert (
        migration._controller_jwt_key_path(  # noqa: SLF001
            "AuthAltTypes=auth/jwt\nStateSaveLocation=/var/spool/slurmctld\n"
        )
        == "/var/spool/slurmctld/jwt_hs256.key"
    )

    with pytest.raises(RuntimeError, match="JWKS-only"):
        migration._controller_jwt_key_path(  # noqa: SLF001
            "AuthAltTypes=auth/jwt\n"
            "AuthAltParameters=jwks=/etc/slurm/jwks.json\n"
            "StateSaveLocation=/var/spool/slurmctld\n"
        )


def test_jwt_checks_bracket_target_takeover_and_mismatch_uses_rollback() -> None:
    source = inspect.getsource(  # noqa: SLF001
        migration._handoff_controller_bridge_to_target_singleton
    )
    fence_source = inspect.getsource(  # noqa: SLF001
        migration._fence_target_version_bridge_for_takeover
    )

    preflight = source.index("_preflight_target_singleton_jwt_material")
    fence = source.index("_fence_target_version_bridge_for_takeover")
    try_block = source.index("    try:", fence)
    live_proof = source.index("_revalidate_target_singleton_jwt_material", try_block)
    rollback = source.index("_restart_target_version_bridge_after_failed_takeover", live_proof)
    lease_transfer = fence_source.index("_transition_controller_authority_lease")
    journal_authority = fence_source.index("record_bridge_authority", lease_transfer)
    helper_return = fence_source.rindex("return stopped_pod_uids")
    assert preflight < fence < try_block < live_proof < rollback
    assert lease_transfer < journal_authority < helper_return


def test_gated_target_replica_is_explicitly_ungated_before_startup_proof() -> None:
    source = inspect.getsource(  # noqa: SLF001
        migration._handoff_controller_bridge_to_target_singleton
    )

    gate_observation = source.index("target_gated_before")
    ungate_branch = source.index("if ungate_required:", gate_observation)
    controller_ungate = source.index(
        "_ungate_in_place_target_controller_for_takeover", ungate_branch
    )
    reject_live_gate = source.index("_is_exact_target_controller_command_gate", controller_ungate)
    startup_proof = source.index("scontrol ping", reject_live_gate)
    assert gate_observation < ungate_branch < controller_ungate
    assert controller_ungate < reject_live_gate < startup_proof


def test_failed_takeover_clears_jwt_proof_only_after_target_runtime_fence() -> None:
    source = inspect.getsource(  # noqa: SLF001
        migration._restart_target_version_bridge_after_failed_takeover
    )

    runtime_fence = source.index("_prove_controller_runtime_fence")
    clear_proof = source.index('takeover["jwt_material_proof"] = {}', runtime_fence)
    bridge_authority = source.index("record_bridge_authority", clear_proof)
    assert runtime_fence < clear_proof < bridge_authority


def test_target_takeover_smokes_jwt_without_printing_the_token() -> None:
    source = inspect.getsource(  # noqa: SLF001
        migration._probe_target_controller_jwt_key
    )

    assert "unset SLURM_JWT" in source
    assert "scontrol token lifespan=60" in source
    assert 'case "$jwt_token" in SLURM_JWT=*' in source
    assert "unset jwt_token" in source
    assert "echo $jwt_token" not in source
    assert 'printf "%s\\\\n" "$jwt_token"' not in source
