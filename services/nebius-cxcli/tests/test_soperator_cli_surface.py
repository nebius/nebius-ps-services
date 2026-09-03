from __future__ import annotations

import copy
import hashlib
import inspect
import json
import re
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import typer
import yaml
from rich.markdown import Markdown
from typer.testing import CliRunner

from nebius_cxcli import cli, soperator_adapter, soperator_public_discovery
from nebius_cxcli.paths import ProjectPaths
from nebius_cxcli.slurm_jobs import (
    applied_slurm_held_job_records,
    parse_scontrol_show_job_record,
)
from nebius_cxcli.soperator_destroy import (
    build_soperator_destroy_receipt,
    load_soperator_destroy_receipt,
    write_soperator_destroy_receipt,
)
from nebius_cxcli.soperator_failures import SoperatorFailureDisposition
from nebius_cxcli.soperator_release_artifacts import SoperatorArtifactReceipt
from nebius_cxcli.soperator_status import SoperatorOperationStatus
from nebius_cxcli.soperator_telemetry import (
    SoperatorObservabilityReceipt,
    SoperatorObservabilityScope,
)
from soperator_fixtures import sample_infrastructure_receipt

runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def test_public_discovery_provider_observation_preserves_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nebius.api.nebius.iam.v1 as iam_v1

    class _CompletedRequest:
        def __init__(self, value: object) -> None:
            self._value = value

        def wait(self) -> object:
            return self._value

    class _ProjectServiceClient:
        def __init__(self, _sdk: object) -> None:
            pass

        def get(self, _request: object, **_kwargs: object) -> _CompletedRequest:
            return _CompletedRequest(
                SimpleNamespace(metadata=SimpleNamespace(id="project-a", parent_id="tenant-a"))
            )

    raw_group = SimpleNamespace(
        metadata=SimpleNamespace(id="group-a", name="worker-a", resource_version=17),
        spec=SimpleNamespace(
            version="1.35",
            template=SimpleNamespace(
                resources=SimpleNamespace(platform="gpu-h100", preset="16vcpu-200gb"),
                gpu_settings=SimpleNamespace(drivers_preset="cuda13.0"),
                os="ubuntu24.04",
            ),
        ),
        status=SimpleNamespace(
            version="v1.35.6",
            node_count=7,
            target_node_count=9,
            ready_node_count=6,
            outdated_node_count=1,
            state="RUNNING",
            reconciling=True,
        ),
    )
    cluster = SimpleNamespace(
        metadata=SimpleNamespace(id="mk8scluster-a", parent_id="project-a", name="cluster-a"),
        spec=SimpleNamespace(control_plane=SimpleNamespace(version="1.35")),
    )

    class _Executor:
        def __init__(self, _sdk: object) -> None:
            pass

        def get_cluster(self, _cluster_id: str) -> object:
            return cluster

        def list_node_groups(self, _cluster_id: str) -> list[object]:
            return [raw_group]

    sdk = SimpleNamespace(sync_close=lambda: None)
    monkeypatch.setattr(iam_v1, "ProjectServiceClient", _ProjectServiceClient)
    monkeypatch.setattr(soperator_public_discovery, "init_nebius_sdk", lambda **_kwargs: sdk)
    monkeypatch.setattr(
        soperator_public_discovery,
        "Mk8sKubernetesVersionExecutor",
        _Executor,
    )

    _, provider, errors = (
        soperator_public_discovery.SoperatorPublicDiscoveryRuntime.provider_observation(
            tenant_id="tenant-a",
            project_id="project-a",
            cluster_id="mk8scluster-a",
        )
    )

    assert errors == []
    assert provider["node_groups"][0]["actual_node_count"] == 7
    assert provider["node_groups"][0]["target_node_count"] == 9
    assert provider["node_groups"][0]["provider_ready_node_count"] == 6
    assert provider["node_groups"][0]["outdated_node_count"] == 1
    assert provider["collection_lanes"] == [
        {"name": "provider-node-groups", "status": "succeeded", "item_count": 1}
    ]


def test_long_running_upgrade_handoffs_require_renewable_exec_auth() -> None:
    full_stack_source = inspect.getsource(cli.soperator_upgrade_command)
    release_source = inspect.getsource(cli._run_common_soperator_release_upgrade)

    assert re.search(
        r"_prepare_cluster_handoff_kube_env\(.*?require_renewable_auth=True",
        full_stack_source,
        re.DOTALL,
    )
    assert re.search(
        r"_prepare_cluster_handoff_kube_env\(.*?require_renewable_auth=True",
        release_source,
        re.DOTALL,
    )
    assert "upgrade_progress.retry_wait(" in full_stack_source
    assert "retry_wait=_parent_retry_wait" in full_stack_source
    assert "print_plan=print_release_plan_once" in full_stack_source
    assert "single_use_soperator_upgrade_plan_printer(" in full_stack_source
    assert "upgrade_progress.message(message)" in release_source
    assert "print_plan(plan_lines)" in release_source


def test_upgrade_login_command_runs_inside_the_mounted_jail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def _run(namespace: str, args: list[str], **_kwargs: object):
        command = tuple(args)
        calls.append((namespace, command))
        return cli._SoperatorUpgradeCommandResult(command, 0, "idle\n", "")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", _run)

    result = cli._run_soperator_upgrade_login_command(
        "soperator",
        "sinfo -h -o '%t'",
    )

    assert result.returncode == 0
    assert calls == [
        (
            "soperator",
            (
                "exec",
                "login-0",
                "--",
                "chroot",
                "/mnt/jail",
                "bash",
                "-lc",
                "sinfo -h -o '%t'",
            ),
        )
    ]


def test_slurm_worker_readiness_requires_every_static_nodeset_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_login_command",
        lambda *_args, **_kwargs: cli._SoperatorUpgradeCommandResult(
            ("scontrol", "show", "nodes", "-o"),
            0,
            (
                "NodeName=worker-0-0 State=IDLE SlurmdStartTime=2026-08-30T00:00:00\n"
                "NodeName=worker-0-1 State=DRAIN SlurmdStartTime=2026-08-30T00:00:01\n"
            ),
            "",
        ),
    )

    receipt = cli._verify_soperator_upgrade_slurm_worker_registration(
        namespace="soperator",
        values={
            "nodesets": {"overrideValues": {"nodesets": [{"name": "worker-0", "replicas": 2}]}}
        },
    )

    assert receipt["status"] == "ready"
    assert receipt["expectedNodeCount"] == 2
    assert receipt["registeredNodeCount"] == 2


def test_slurm_worker_readiness_rejects_missing_or_unregistered_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter(
        (
            cli._SoperatorUpgradeCommandResult(
                ("scontrol", "show", "nodes", "-o"),
                0,
                ("NodeName=worker-0-0 State=IDLE SlurmdStartTime=2026-08-30T00:00:00\n"),
                "",
            ),
            cli._SoperatorUpgradeCommandResult(
                ("scontrol", "show", "nodes", "-o"),
                0,
                (
                    "NodeName=worker-0-0 State=IDLE "
                    "SlurmdStartTime=2026-08-30T00:00:00\n"
                    "NodeName=worker-0-1 State=UNKNOWN+NOT_RESPONDING "
                    "SlurmdStartTime=2026-08-30T00:00:01\n"
                ),
                "",
            ),
        )
    )
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_login_command",
        lambda *_args, **_kwargs: next(results),
    )
    values = {"nodesets": {"overrideValues": {"nodesets": [{"name": "worker-0", "replicas": 2}]}}}

    with pytest.raises(RuntimeError, match="missing target Slurm worker registration"):
        cli._verify_soperator_upgrade_slurm_worker_registration(
            namespace="soperator",
            values=values,
        )
    with pytest.raises(RuntimeError, match="have not registered successfully"):
        cli._verify_soperator_upgrade_slurm_worker_registration(
            namespace="soperator",
            values=values,
        )


def test_slurm_worker_readiness_rejects_configured_but_unregistered_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_login_command",
        lambda *_args, **_kwargs: cli._SoperatorUpgradeCommandResult(
            ("scontrol", "show", "nodes", "-o"),
            0,
            ("NodeName=worker-0-0 State=IDLE+CLOUD+POWERED_DOWN SlurmdStartTime=None\n"),
            "",
        ),
    )

    with pytest.raises(RuntimeError, match="have not registered successfully"):
        cli._verify_soperator_upgrade_slurm_worker_registration(
            namespace="soperator",
            values={
                "nodesets": {"overrideValues": {"nodesets": [{"name": "worker-0", "replicas": 1}]}}
            },
        )


def _upgrade_payload_with_values(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster-a",
                    "target_ref": "cluster-a",
                    "values": dict(values),
                }
            ]
        }
    }


def test_upgrade_wizard_freezes_defaults_and_preserves_existing_mount_backing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _upgrade_payload_with_values(
        {
            "jailPersistentMounts": [
                {
                    "mountPath": "/opt/customer-data",
                    "localPath": "/mnt/jail/customer/opt-data",
                }
            ]
        }
    )
    prompts: list[tuple[str, object]] = []

    def _prompt(path: str, default: object, **_kwargs: object) -> object:
        prompts.append((path, default))
        return ["/opt/customer-data", "/etc/customer-data/"]

    monkeypatch.setattr(cli, "_prompt_upgrade_scalar", _prompt)

    protected = cli._configure_soperator_upgrade_persistent_paths(
        source_payload=payload,
        target=cli._parse_soperator_upgrade_target("cluster-a"),
        ownership="onboarded",
        interactive=True,
    )

    assert prompts == [
        (
            "soperator.upgrade.additional_persistent_data_paths",
            ["/opt/customer-data"],
        )
    ]
    assert protected == (
        "/data",
        "/etc/customer-data",
        "/home",
        "/models",
        "/opt/customer-data",
        "/scripts",
    )
    values = payload["apps"]["charts"][0]["values"]
    mounts = {item["mountPath"]: item["localPath"] for item in values["jailPersistentMounts"]}
    assert mounts["/opt/customer-data"] == "/mnt/jail/customer/opt-data"
    assert mounts["/etc/customer-data"] == "/mnt/jail/etc/customer-data"
    assert set(mounts) == set(protected)


def test_upgrade_recovery_reuses_frozen_persistent_paths_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _upgrade_payload_with_values({})
    monkeypatch.setattr(
        cli,
        "_prompt_upgrade_scalar",
        lambda *_args, **_kwargs: pytest.fail("recovery must not prompt"),
    )

    protected = cli._configure_soperator_upgrade_persistent_paths(
        source_payload=payload,
        target=cli._parse_soperator_upgrade_target("cluster-a"),
        ownership="onboarded",
        interactive=True,
        frozen_paths=["/home", "/data", "/scripts", "/models", "/srv/customer"],
    )

    assert protected == ("/data", "/home", "/models", "/scripts", "/srv/customer")
    values = payload["apps"]["charts"][0]["values"]
    mounts = {item["mountPath"]: item["localPath"] for item in values["jailPersistentMounts"]}
    assert mounts["/srv/customer"] == "/mnt/jail/srv/customer"


def test_upgrade_rejects_new_zero_copy_path_after_slot_adoption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _upgrade_payload_with_values(
        {
            "jailRootfs": {
                "strategy": "activePassive",
                "activeSlot": "slot-a",
                "passiveSlot": "slot-b",
                "adoption": {"activeSource": "slot", "rollbackSource": "slot"},
            }
        }
    )
    monkeypatch.setattr(
        cli,
        "_prompt_upgrade_scalar",
        lambda *_args, **_kwargs: ["/srv/new-data"],
    )

    with pytest.raises(ValueError, match="only during first rootfs adoption"):
        cli._configure_soperator_upgrade_persistent_paths(
            source_payload=payload,
            target=cli._parse_soperator_upgrade_target("cluster-a"),
            ownership="managed",
            interactive=True,
        )


def test_upgrade_materializes_default_slot_source_during_admission() -> None:
    payload = _upgrade_payload_with_values(
        {
            "jailRootfs": {
                "strategy": "activePassive",
                "activeSlot": "slot-a",
                "passiveSlot": "slot-b",
            }
        }
    )

    protected = cli._configure_soperator_upgrade_persistent_paths(
        source_payload=payload,
        target=cli._parse_soperator_upgrade_target("cluster-a"),
        ownership="managed",
        interactive=False,
    )

    values = payload["apps"]["charts"][0]["values"]
    assert protected == ("/data", "/home", "/models", "/scripts")
    assert values["jailRootfs"]["adoption"] == {
        "activeSource": "slot",
        "rollbackSource": "slot",
    }


def test_upgrade_preserves_explicit_slot_source_when_strategy_was_omitted() -> None:
    payload = _upgrade_payload_with_values(
        {
            "jailRootfs": {
                "activeSlot": "slot-a",
                "passiveSlot": "slot-b",
                "adoption": {
                    "activeSource": "slot",
                    "rollbackSource": "slot",
                },
            }
        }
    )

    cli._configure_soperator_upgrade_persistent_paths(
        source_payload=payload,
        target=cli._parse_soperator_upgrade_target("cluster-a"),
        ownership="managed",
        interactive=False,
    )

    values = payload["apps"]["charts"][0]["values"]
    assert values["jailRootfs"]["strategy"] == "activePassive"
    assert values["jailRootfs"]["adoption"] == {
        "activeSource": "slot",
        "rollbackSource": "slot",
    }


def test_upgrade_preserves_legacy_rollback_authority_after_slot_adoption() -> None:
    payload = _upgrade_payload_with_values(
        {
            "jailRootfs": {
                "strategy": "activePassive",
                "activeSlot": "slot-b",
                "passiveSlot": "slot-a",
                "adoption": {
                    "activeSource": "slot",
                    "rollbackSource": "legacy-rootfs",
                    "legacyPvcName": "jail-pvc",
                },
            }
        }
    )

    cli._configure_soperator_upgrade_persistent_paths(
        source_payload=payload,
        target=cli._parse_soperator_upgrade_target("cluster-a"),
        ownership="onboarded",
        interactive=False,
        frozen_paths=["/home", "/data", "/scripts", "/models"],
    )
    first_reconstruction = copy.deepcopy(payload)
    cli._configure_soperator_upgrade_persistent_paths(
        source_payload=payload,
        target=cli._parse_soperator_upgrade_target("cluster-a"),
        ownership="onboarded",
        interactive=False,
        frozen_paths=["/home", "/data", "/scripts", "/models"],
    )

    assert payload == first_reconstruction
    assert payload["apps"]["charts"][0]["values"]["jailRootfs"]["adoption"] == {
        "activeSource": "slot",
        "rollbackSource": "legacy-rootfs",
        "legacyPvcName": "jail-pvc",
    }


def test_upgrade_rejects_legacy_rollback_authority_without_pvc_identity() -> None:
    payload = _upgrade_payload_with_values(
        {
            "jailRootfs": {
                "strategy": "activePassive",
                "activeSlot": "slot-b",
                "passiveSlot": "slot-a",
                "adoption": {
                    "activeSource": "slot",
                    "rollbackSource": "legacy-rootfs",
                },
            }
        }
    )

    with pytest.raises(ValueError, match="legacy rollback authority requires"):
        cli._configure_soperator_upgrade_persistent_paths(
            source_payload=payload,
            target=cli._parse_soperator_upgrade_target("cluster-a"),
            ownership="onboarded",
            interactive=False,
            frozen_paths=["/home", "/data", "/scripts", "/models"],
        )


def test_release_transition_recycles_slot_when_adoption_source_was_omitted() -> None:
    switched_values, transition = cli.plan_soperator_rootfs_transition(
        {
            "jailRootfs": {
                "strategy": "activePassive",
                "activeSlot": "slot-a",
                "passiveSlot": "slot-b",
            }
        },
        target_ref="apps:soperator@cluster-a",
        layout="managed",
        legacy_pvc_resolver=lambda: pytest.fail(
            "slot-backed transition must not resolve a legacy PVC"
        ),
    )

    assert transition == {
        "currentActiveSource": "slot",
        "currentActiveSlot": "slot-a",
        "currentInactiveSlot": "slot-b",
        "desiredActiveSlot": "slot-b",
        "livePvcName": "jail-rootfs-slot-a-pvc",
        "targetPvcName": "jail-rootfs-slot-b-pvc",
        "recycleInactiveSlot": True,
    }
    assert switched_values["jailRootfs"]["adoption"] == {
        "activeSource": "slot",
        "rollbackSource": "slot",
    }


def _retired_soperator_root_command() -> str:
    return "-".join(("ext", "soperator"))


def _normalized(value: str) -> str:
    return " ".join(_ANSI_ESCAPE_RE.sub("", value).split())


def _normalized_cli_help(value: str) -> str:
    return _normalized(re.sub(r"[\u2500-\u257f]", " ", value))


def _soperator_cli_contract_payload() -> dict[str, Any]:
    payload: Any = json.loads(
        (Path(__file__).resolve().parent / "fixtures" / "cli_contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(payload, dict)
    assert payload["schema"] == "nebius-cxcli.cli-contract.v1"
    contract = payload["soperator"]
    assert contract["schema"] == "nebius-cxcli.soperator-cli-contract.v4"
    return contract


def _soperator_cli_contract() -> dict[str, set[str]]:
    payload = _soperator_cli_contract_payload()
    return {
        str(name): {str(option) for option in definition["options"]}
        for name, definition in payload["commands"].items()
    }


def _soperator_command_metadata(command: Any) -> dict[str, Any]:
    arguments: list[dict[str, Any]] = []
    options: list[str] = []
    required: list[str] = []
    repeatable: list[str] = []
    flags: list[str] = []
    paired: dict[str, list[str]] = {}
    defaults: dict[str, Any] = {}
    option_help: dict[str, str] = {}

    for parameter in command.params:
        primary = next(
            (option for option in getattr(parameter, "opts", ()) if option.startswith("--")),
            None,
        )
        if primary is None:
            arguments.append(
                {
                    "name": parameter.name,
                    "metavar": parameter.metavar,
                    "required": parameter.required,
                    "help": _normalized(str(parameter.help or "")),
                }
            )
            continue
        secondary = sorted(
            option for option in getattr(parameter, "secondary_opts", ()) if option.startswith("--")
        )
        options.extend((primary, *secondary))
        option_help[primary] = _normalized(str(parameter.help or ""))
        if parameter.required:
            required.append(primary)
        if parameter.multiple:
            repeatable.append(primary)
        if parameter.is_flag:
            flags.append(primary)
        if secondary:
            paired[primary] = secondary
        if parameter.default is not None:
            defaults[primary] = parameter.default

    assert len(arguments) == 1
    return {
        "short_help": _normalized(str(command.short_help or "")),
        "argument": arguments[0],
        "option_help": dict(sorted(option_help.items())),
        "options": sorted(options),
        "option_order": options,
        "required": sorted(required),
        "repeatable": sorted(repeatable),
        "flags": sorted(flags),
        "paired": dict(sorted(paired.items())),
        "defaults": dict(sorted(defaults.items())),
        "conditional_requirements": list(
            cli._SOPERATOR_CONDITIONAL_REQUIREMENTS.get(str(command.name), ())
        ),
    }


def _paths(tmp_path: Path) -> ProjectPaths:
    return ProjectPaths(
        config_path=tmp_path / "config.yaml",
        repo_root=tmp_path,
        deployments_dir=tmp_path,
        project_dir=tmp_path,
        generated_dir=tmp_path / "generated",
        infra_dir=tmp_path / "generated" / "infra",
        flux_dir=tmp_path / "generated" / "flux",
        reports_dir=tmp_path / "generated" / "reports",
        path_tenant_folder="tenant",
        path_project_folder="project",
    )


def _write_rendered_soperator_values(
    paths: ProjectPaths,
    *,
    target_ref: str,
    values: Mapping[str, Any],
) -> None:
    target_dir = paths.flux_dir / "targets" / target_ref
    target_dir.mkdir(parents=True, exist_ok=True)
    values_configmap = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "terraform-fluxcd-values", "namespace": "flux-system"},
        "data": {"values.yaml": cli.yaml.safe_dump(dict(values), sort_keys=False)},
    }
    (target_dir / "configmap-terraform-fluxcd-values.yaml").write_text(
        cli.yaml.safe_dump(values_configmap, sort_keys=False),
        encoding="utf-8",
    )


def test_rendered_rootfs_slot_contract_uses_selected_target_flux_dir(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    target_paths = replace(paths, flux_dir=paths.flux_dir / "targets" / "cluster-a")
    target_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    adapter_documents = [
        {
            "apiVersion": "storage.k8s.io/v1",
            "kind": "StorageClass",
            "metadata": {"name": "slurm-local-pv"},
            "provisioner": "kubernetes.io/no-provisioner",
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "jail-rootfs-slot-b"},
            "spec": {
                "storageClassName": "slurm-local-pv",
                "resources": {"requests": {"storage": "128Gi"}},
            },
        },
    ]
    (target_paths.flux_dir / "soperator-nebius-adapter.yaml").write_text(
        cli.yaml.safe_dump_all(adapter_documents, sort_keys=False),
        encoding="utf-8",
    )

    assert cli._rendered_rootfs_slot_contract(
        target_paths,
        pvc_name="jail-rootfs-slot-b",
    ) == (
        "slurm-local-pv",
        "kubernetes.io/no-provisioner",
        "128Gi",
    )


def _rootfs_admission(
    *,
    assert_authority=lambda: cli.SoperatorLeaseAuthority(
        lease_name="lease-a",
        lease_uid="lease-uid-a",
        holder_identity_sha256="sha256:" + "a" * 64,
        fencing_epoch=3,
        operation_fingerprint="sha256:" + "b" * 64,
    ),
) -> cli.SoperatorRootfsAdmissionPreflight:
    return cli._build_soperator_rootfs_admission(
        target_image="registry.example.invalid/target@sha256:" + "2" * 64,
        live_pvc_name="jail-rootfs-source",
        live_pvc_uid="source-uid",
        target_pvc_name="jail-rootfs-slot-b-pvc",
        target_slot="slot-b",
        target_storage_class_name="slurm-local-pv",
        target_provisioner="kubernetes.io/no-provisioner",
        target_capacity="128Gi",
        persistent_paths=("/scripts", "/home", "/models", "/data"),
        assert_authority=assert_authority,
    )


def test_rootfs_admission_is_content_free_and_requires_only_fencing_authority() -> None:
    authority_checks = 0

    def _authority() -> cli.SoperatorLeaseAuthority:
        nonlocal authority_checks
        authority_checks += 1
        return cli.SoperatorLeaseAuthority(
            lease_name="lease-a",
            lease_uid="lease-uid-a",
            holder_identity_sha256="sha256:" + "a" * 64,
            fencing_epoch=3,
            operation_fingerprint="sha256:" + "b" * 64,
        )

    preflight = _rootfs_admission(assert_authority=_authority)
    payload = preflight.as_payload()

    assert authority_checks == 1
    assert payload["mode"] == "target-wins"
    assert payload["persistentPaths"] == ["/data", "/home", "/models", "/scripts"]
    assert payload["decision"] == {
        "targetWinsOutsidePersistentPaths": True,
        "protectedPathCount": 4,
    }
    assert payload["binding"]["targetProvisioner"] == "kubernetes.io/no-provisioner"
    assert set(payload).isdisjoint({"classification", "manifests", "scratch", "jobs"})
    assert cli.SoperatorRootfsAdmissionPreflight.from_payload(payload) == preflight


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"target_image": "registry.example.invalid/target:latest"}, "digest-addressed"),
        ({"persistent_paths": ("/home",)}, "persistent paths"),
        (
            {
                "persistent_paths": (
                    "/data",
                    "/home",
                    "/models",
                    "/scripts",
                    "/data/../etc/customer",
                )
            },
            "persistent data path",
        ),
        ({"target_pvc_name": "jail-rootfs-source"}, "binding"),
        ({"target_provisioner": "compute.csi.nebius.com"}, "storage contract"),
    ],
)
def test_rootfs_admission_rejects_incomplete_or_mutable_authority(
    overrides: dict[str, object],
    message: str,
) -> None:
    kwargs: dict[str, object] = {
        "target_image": "registry.example.invalid/target@sha256:" + "2" * 64,
        "live_pvc_name": "jail-rootfs-source",
        "live_pvc_uid": "source-uid",
        "target_pvc_name": "jail-rootfs-slot-b-pvc",
        "target_slot": "slot-b",
        "target_storage_class_name": "slurm-local-pv",
        "target_provisioner": "kubernetes.io/no-provisioner",
        "target_capacity": "128Gi",
        "persistent_paths": ("/data", "/home", "/models", "/scripts"),
        "assert_authority": None,
    }
    kwargs.update(overrides)

    with pytest.raises(RuntimeError, match=message):
        cli._build_soperator_rootfs_admission(**kwargs)


def _install_replan_receipt(authority: Mapping[str, Any]) -> dict[str, Any]:
    receipt = copy.deepcopy(dict(authority))
    inputs = receipt["inputs"]
    assert isinstance(inputs, dict)
    inputs["terraformPlanSha256"] = "sha256:" + "c" * 64
    receipt["planGeneration"] = "sha256:" + "d" * 64
    receipt["status"] = "planned"
    receipt["approvalFingerprint"] = cli._soperator_install_approval_fingerprint(receipt)
    return receipt


def _install_plan_authority() -> dict[str, Any]:
    return {
        "schema": "nebius-cxcli.soperator-install-plan.v1",
        "operationId": "sha256:" + "a" * 64,
        "target": {
            "ref": "cluster-a",
            "kind": "managed-mk8s",
            "ownership": "managed",
        },
        "inputs": {
            "configSha256": "sha256:" + "b" * 64,
            "generatedManifestSha256": "sha256:" + "c" * 64,
        },
        "release": {
            "selector": "4.1.7",
            "version": "4.1.7",
            "tag": "v4.1.7",
            "commit": "d" * 40,
            "tree": "e" * 40,
            "snapshotSha256": "sha256:" + "f" * 64,
            "sourceManifestSha256": "sha256:" + "1" * 64,
            "umbrellaDigest": "sha256:" + "2" * 64,
        },
    }


def test_rootfs_admission_receipt_round_trips_sealed_target_wins_identity() -> None:
    preflight = _rootfs_admission()

    restored = cli.SoperatorRootfsAdmissionPreflight.from_payload(preflight.as_payload())

    assert restored == preflight
    tampered = copy.deepcopy(preflight.as_payload())
    tampered["binding"]["targetCapacity"] = "256Gi"
    with pytest.raises(RuntimeError, match="identity changed"):
        cli.SoperatorRootfsAdmissionPreflight.from_payload(tampered)

    unsupported = copy.deepcopy(preflight.as_payload())
    unsupported["schema"] = "nebius-cxcli.soperator-rootfs-admission.v0"
    with pytest.raises(RuntimeError, match="incomplete or unsupported"):
        cli.SoperatorRootfsAdmissionPreflight.from_payload(unsupported)


def test_passive_rootfs_prewrite_does_not_reinventory_disposable_live_content() -> None:
    source = inspect.getsource(cli._apply_rendered_flux)

    assert 'purpose="live-prewrite"' not in source
    assert '"sourceManifestSha256"' not in source
    assert '"liveManifestSha256"' not in source
    assert '"rootfs-admission-decision"' in source
    assert "rootfs_admission_preflight.receipt_sha256" in source
    assert re.search(
        r"verify_passive_rootfs_precondition\(.*?"
        r"allowed_operation_id=operation_anchor\.operation_id,.*?\)",
        source,
        re.DOTALL,
    )


def test_passive_rootfs_preparation_reports_copy_and_inventory_subphases() -> None:
    source = inspect.getsource(cli._apply_rendered_flux)

    assert "on_progress=_emit_population_progress" in source
    assert "progress.files_restored" in source
    assert "progress.bytes_restored" in source
    assert "verify-passive-jail-rootfs-inventory" in source


def test_bound_adapter_repair_requires_drift_unless_source_repair_is_authenticated() -> None:
    source = inspect.getsource(cli._soperator_upgrade_bound_adapter_repair_readmission_is_safe)

    assert "source_repair = registration_topology_repair or scheduling_runtime_repair" in source
    assert "not baseline_mismatches" in source
    assert "desired_stage.project_generation_plan" in source
    assert "if not mismatches or not set(mismatches) <= allowed_mismatches:" in source
    assert "mismatches != allowed_mismatches" not in source


def test_recovery_admission_reuses_the_authenticated_slurm_preimage() -> None:
    capture_calls: list[str] = []
    preimage = cli.build_slurm_upgrade_preimage(
        partitions=[],
        jobs=[],
        reservations=[],
    )

    observed = cli._soperator_slurm_preimage_for_admission(
        recovery_admission_receipt={"slurmPreimage": preimage.as_payload()},
        capture_fresh=lambda: capture_calls.append("fresh") or preimage,
    )

    assert observed == preimage
    assert capture_calls == []


def test_new_admission_still_captures_a_fresh_slurm_preimage() -> None:
    preimage = cli.build_slurm_upgrade_preimage(
        partitions=[],
        jobs=[],
        reservations=[],
    )

    assert (
        cli._soperator_slurm_preimage_for_admission(
            recovery_admission_receipt=None,
            capture_fresh=lambda: preimage,
        )
        == preimage
    )


def test_recovery_admission_rejects_a_missing_slurm_preimage() -> None:
    with pytest.raises(cli.SoperatorSafetyPauseError, match="Slurm admission preimage is missing"):
        cli._soperator_slurm_preimage_for_admission(
            recovery_admission_receipt={},
            capture_fresh=lambda: pytest.fail("fresh capture must not run during recovery"),
        )


def test_completed_retention_recovery_uses_the_declared_home_transport_gate() -> None:
    source = inspect.getsource(cli._apply_rendered_flux)

    assert re.search(
        r"def _verify_completed_protected_retention\(\).*?"
        r"admitted_home_mount_sha256=protected_before_receipt\.home_mount_sha256.*?"
        r"allow_home_mount_transport_transition=\(\s*"
        r"recovery_has_applied_declared_home_transition\s*\)",
        source,
        re.DOTALL,
    )
    assert re.search(
        r'"enforce-protected-volume-retention": lambda _evidence: \('
        r"\s*_verify_completed_protected_retention\(\)",
        source,
    )
    assert re.search(
        r"verify_protected_state=\(\s*_verify_post_rootfs_protected_state\s*if ",
        source,
    )


def test_completed_rootfs_population_uses_sealed_evidence_after_target_apply() -> None:
    source = inspect.getsource(cli._apply_rendered_flux)

    assert re.search(
        r"recovery_has_applied_target_release = \(.*?"
        r"operation_source_release is not None.*?"
        r"current_release != soperator_snapshot\.release.*?"
        r"observed_release == soperator_snapshot\.release",
        source,
        re.DOTALL,
    )
    assert re.search(
        r"def _verify_current_rootfs_materialization\(\).*?"
        r"_soperator_upgrade_sealed_rootfs_materialization\(.*?"
        r"_soperator_upgrade_sealed_rootfs_jobs_are_complete\(",
        source,
        re.DOTALL,
    )
    assert re.search(
        r'"populate-passive-jail-rootfs": lambda _evidence: \(.*?'
        r"_verify_repair_rootfs_materialization\(\).*?"
        r"if repair_lineage is not None.*?"
        r"_verify_current_rootfs_materialization\(\).*?"
        r"if recovery_has_applied_target_release.*?"
        r"else _prepare_passive_rootfs\(\)",
        source,
        re.DOTALL,
    )


def test_completed_declarative_release_rechecks_spool_and_refreshes_sources() -> None:
    source = inspect.getsource(cli._apply_rendered_flux)

    assert re.search(
        r"def _wait_flux\(\).*?"
        r"reconcile_strategy\.strategy is SoperatorStrategy\.NOOP.*?"
        r"prepare_soperator_release_sources\(.*?"
        r"return _wait_noop_readiness\(\)",
        source,
        re.DOTALL,
    )
    assert re.search(
        r"def _verify_completed_declarative_release\(\).*?"
        r"controller_spool_migration\.prepare\(\).*?"
        r"controller_spool_migration\.finish\(.*?"
        r"release_opened=True.*?"
        r"prepare_soperator_release_sources\(.*?"
        r"return _wait_flux\(\)",
        source,
        re.DOTALL,
    )
    assert re.search(
        r'"apply-declarative-release": lambda _evidence: \(\s*'
        r"_verify_completed_declarative_release\(\)",
        source,
    )


def test_soperator_upgrade_has_no_observability_auth_query_or_secret_lifecycle() -> None:
    apply_source = inspect.getsource(cli._apply_rendered_flux)
    upgrade_source = inspect.getsource(cli._run_common_soperator_release_upgrade)
    forbidden = (
        "acquire_operator_access_token",
        "verify_soperator_observability",
        "ensure_observability_read_token_secret",
        "read_observability_read_token_secret",
        "cleanup_observability_read_token_secret",
        "wait_pre_resume_telemetry",
        "wait_final_telemetry",
        "authoritative-telemetry",
    )
    assert all(symbol not in apply_source for symbol in forbidden)
    assert all(symbol not in upgrade_source for symbol in forbidden)
    assert "wait_final_product=_wait_complete_product" in apply_source


def test_post_rootfs_handoff_allows_home_transport_change_only_for_legacy_adoption() -> None:
    source = inspect.getsource(cli._apply_rendered_flux)

    assert re.search(
        r"declared_home_mount_transport_transition = \(.*?"
        r'admitted_rootfs_transition_map\.get\("currentActiveSource"\).*?'
        r'== "legacy-rootfs".*?'
        r'"/home" in rootfs_admission_preflight\.persistent_paths.*?'
        r'item\.mount_path == "/home".*?'
        r"recovery_has_applied_target_release = \(.*?"
        r"operation_source_release is not None.*?"
        r"observed_release == soperator_snapshot\.release.*?"
        r"recovery_has_applied_declared_home_transition = \(.*?"
        r"recovery_has_applied_target_release",
        source,
        re.DOTALL,
    )

    upgrade_source = inspect.getsource(cli._run_common_soperator_release_upgrade)
    assert re.search(
        r"recovery_has_applied_declared_home_transition = \(.*?"
        r"active_intent is not None.*?"
        r"live_release == target_version.*?"
        r'stored_rootfs_transition_map\.get\("currentActiveSource"\).*?'
        r'== "legacy-rootfs".*?'
        r'"/home" in protected_paths.*?'
        r"allow_home_mount_transport_transition=\(\s*"
        r"recovery_has_applied_declared_home_transition",
        upgrade_source,
        re.DOTALL,
    )
    assert re.search(
        r"def _verify_post_rootfs_protected_state\(\).*?"
        r"allow_home_mount_transport_transition=\(\s*"
        r"declared_home_mount_transport_transition",
        source,
        re.DOTALL,
    )


def _bound_adapter_repair_receipt(*, generation: int = 2) -> tuple[dict[str, Any], str]:
    operation_spec: dict[str, Any] = {"intervention_generation": generation}
    operation_spec_sha256 = cli.soperator_sha256(operation_spec)
    transitions = [
        {"phase": "resolve-immutable-sources", "status": "complete"},
        {"phase": "capture-protected-data-plane", "status": "complete"},
        {"phase": "enforce-protected-volume-retention", "status": "complete"},
        {"phase": "establish-boot-storage-barrier", "status": "complete"},
        {"phase": "verify-sealed-jail-rootfs-classification", "status": "complete"},
        {
            "phase": "populate-passive-jail-rootfs",
            "status": "failed",
            "failureAttempts": 1,
        },
    ]
    return (
        {
            "status": "failed",
            "operation": {"spec": operation_spec},
            "target": {"ref": "cluster-a"},
            "transitions": transitions,
        },
        operation_spec_sha256,
    )


def _failed_population_journal(operation_spec_sha256: str) -> dict[str, Any]:
    return {
        "schema": "nebius-cxcli.soperator-recovery-journal.v4",
        "status": "active",
        "operationId": operation_spec_sha256,
        "stages": {
            "rootfs-classification": {"status": "complete"},
            "rootfs-passive-target-identity": {
                "status": "complete",
                "evidence": {"pvcUid": "pvc-a"},
            },
            "rootfs-passive-target-preflight": {
                "status": "complete",
                "intent": {"pvcUid": "pvc-a"},
                "evidence": {
                    "status": "empty-and-unconsumed",
                    "consumerCount": 0,
                    "consumerStatus": "unconsumed",
                    "controlMetadataPolicy": "reserved-nebius-cxcli-subtree",
                },
            },
            "rootfs-passive-target-populate": {
                "status": "intent",
                "intent": {
                    "pvcUid": "pvc-a",
                    "workloadSha256": "sha256:" + "b" * 64,
                },
            },
        },
    }


def _failed_population_resources(operation_spec_sha256: str) -> dict[str, Any]:
    operation_id = operation_spec_sha256.removeprefix("sha256:")[:63]
    labels = {
        "nebius-cxcli/operation-id": operation_id,
        "nebius-cxcli/pvc-uid": "pvc-a",
        "soperator.nebius.ai/protected-data-plane": "populate-passive-target",
    }
    return {
        "items": [
            {
                "kind": "Job",
                "metadata": {
                    "uid": "job-a",
                    "labels": labels,
                    "annotations": {"nebius-cxcli/requested-workload-sha256": "sha256:" + "b" * 64},
                },
                "status": {
                    "failed": 1,
                    "conditions": [{"type": "Failed", "status": "True"}],
                },
            },
            {
                "kind": "Pod",
                "metadata": {
                    "labels": labels,
                    "ownerReferences": [{"kind": "Job", "uid": "job-a", "controller": True}],
                },
                "status": {
                    "phase": "Failed",
                    "initContainerStatuses": [
                        {
                            "name": "mount-gate-populate-jail",
                            "state": {"terminated": {"exitCode": 1}},
                        }
                    ],
                    "containerStatuses": [
                        {
                            "name": "populate-jail",
                            "started": False,
                            "imageID": "",
                            "state": {"waiting": {"reason": "PodInitializing"}},
                        }
                    ],
                },
            },
        ]
    }


def test_bound_adapter_repair_binds_current_failed_population_frontier() -> None:
    receipt, operation_spec_sha256 = _bound_adapter_repair_receipt()

    assert (
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=2,
        )
        == "failed-population-init-gate"
    )
    with pytest.raises(cli.SoperatorSafetyPauseError, match="stale intervention"):
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=1,
        )


def test_bound_repair_binds_running_declarative_release_frontier() -> None:
    operation_spec: dict[str, Any] = {"intervention_generation": 2}
    operation_spec_sha256 = cli.soperator_sha256(operation_spec)
    receipt = {
        "status": "running",
        "operation": {"spec": operation_spec},
        "target": {"ref": "cluster-a"},
        "transitions": [
            {"phase": "resolve-immutable-sources", "status": "complete"},
            {"phase": "capture-protected-data-plane", "status": "complete"},
            {"phase": "enforce-protected-volume-retention", "status": "complete"},
            {"phase": "establish-boot-storage-barrier", "status": "complete"},
            {"phase": "verify-sealed-jail-rootfs-classification", "status": "complete"},
            {"phase": "populate-passive-jail-rootfs", "status": "complete"},
            {"phase": "quiesce-legacy-release-owners", "status": "complete"},
            {"phase": "apply-declarative-release", "status": "running"},
        ],
    }

    assert (
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=2,
        )
        == "running-declarative-release"
    )


def test_bound_repair_binds_running_wait_flux_frontier() -> None:
    operation_spec: dict[str, Any] = {"intervention_generation": 2}
    operation_spec_sha256 = cli.soperator_sha256(operation_spec)
    transitions = [
        {"phase": "resolve-immutable-sources", "status": "complete"},
        {"phase": "capture-protected-data-plane", "status": "complete"},
        {"phase": "enforce-protected-volume-retention", "status": "complete"},
        {"phase": "establish-boot-storage-barrier", "status": "complete"},
        {"phase": "verify-sealed-jail-rootfs-classification", "status": "complete"},
        {"phase": "populate-passive-jail-rootfs", "status": "complete"},
        {"phase": "quiesce-legacy-release-owners", "status": "complete"},
        {
            "id": "apply-a",
            "phase": "apply-declarative-release",
            "status": "complete",
            "receiptSha256": "sha256:" + "a" * 64,
        },
        {"phase": "wait-flux-graph", "status": "running", "receiptSha256": None},
    ]
    receipt = {
        "status": "running",
        "operation": {"spec": operation_spec},
        "target": {"ref": "cluster-a"},
        "transitions": transitions,
        "irreversibleFrontier": {
            "phase": "apply-declarative-release",
            "disposition": "forward-only",
            "transitionId": "apply-a",
            "transitionReceiptSha256": "sha256:" + "a" * 64,
        },
    }

    assert (
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=2,
        )
        == "running-wait-flux-graph"
    )
    receipt["irreversibleFrontier"]["transitionId"] = "other"
    with pytest.raises(cli.SoperatorSafetyPauseError, match="Flux-wait frontier"):
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=2,
        )


def test_spool_receipt_writer_repair_refreshes_storage_before_apply() -> None:
    repair = cli.SoperatorReconcileRepairLineage(
        predecessor_receipt={},
        previous_operation_spec_sha256="sha256:" + "a" * 64,
        resume_phase="apply-declarative-release",
        reason="controller-spool-receipt-writer-v1",
    )

    assert cli._soperator_upgrade_refreshes_controller_spool_receipt_writer(repair)
    assert not cli._soperator_upgrade_refreshes_controller_spool_receipt_writer(
        replace(repair, reason="controller-storage-render-contract-v3")
    )
    assert not cli._soperator_upgrade_refreshes_controller_spool_receipt_writer(None)


def test_bound_repair_binds_failed_declarative_release_after_quiescence() -> None:
    operation_spec: dict[str, Any] = {"intervention_generation": 2}
    operation_spec_sha256 = cli.soperator_sha256(operation_spec)
    receipt = {
        "status": "recovery-required",
        "operation": {"spec": operation_spec},
        "target": {"ref": "cluster-a"},
        "transitions": [
            {"phase": "resolve-immutable-sources", "status": "complete"},
            {"phase": "capture-protected-data-plane", "status": "complete"},
            {"phase": "enforce-protected-volume-retention", "status": "complete"},
            {"phase": "establish-boot-storage-barrier", "status": "complete"},
            {"phase": "verify-sealed-jail-rootfs-classification", "status": "complete"},
            {"phase": "populate-passive-jail-rootfs", "status": "complete"},
            {"phase": "quiesce-legacy-release-owners", "status": "complete"},
            {
                "phase": "apply-declarative-release",
                "status": "failed",
                "failureAttempts": 1,
                "failureType": "operation-error",
                "receiptSha256": None,
            },
        ],
        "irreversibleIntent": None,
        "irreversibleFrontier": None,
    }

    assert (
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=2,
        )
        == "failed-declarative-release-after-quiescence"
    )
    receipt["transitions"][-1]["failureAttempts"] = 0
    with pytest.raises(cli.SoperatorSafetyPauseError, match="failure evidence"):
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=2,
        )


def test_bound_repair_binds_failed_protected_adoption_after_apply() -> None:
    operation_spec: dict[str, Any] = {"intervention_generation": 2}
    operation_spec_sha256 = cli.soperator_sha256(operation_spec)
    transitions = [
        {"phase": "resolve-immutable-sources", "status": "complete"},
        {"phase": "capture-protected-data-plane", "status": "complete"},
        {"phase": "enforce-protected-volume-retention", "status": "complete"},
        {"phase": "establish-boot-storage-barrier", "status": "complete"},
        {"phase": "verify-sealed-jail-rootfs-classification", "status": "complete"},
        {"phase": "populate-passive-jail-rootfs", "status": "complete"},
        {"phase": "quiesce-legacy-release-owners", "status": "complete"},
        {
            "id": "apply-a",
            "phase": "apply-declarative-release",
            "status": "complete",
            "receiptSha256": "sha256:" + "a" * 64,
        },
        {"phase": "wait-flux-graph", "status": "complete"},
        {"phase": "apply-post-flux-manifests", "status": "complete"},
        {"phase": "verify-single-writer", "status": "complete"},
        {
            "phase": "adopt-protected-data-plane",
            "status": "failed",
            "failureType": "operation-error",
            "receiptSha256": None,
        },
    ]
    receipt = {
        "status": "recovery-required",
        "operation": {"spec": operation_spec},
        "target": {"ref": "cluster-a"},
        "transitions": transitions,
        "irreversibleFrontier": {
            "phase": "apply-declarative-release",
            "disposition": "forward-only",
            "transitionId": "apply-a",
            "transitionReceiptSha256": "sha256:" + "a" * 64,
        },
    }

    assert (
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=2,
        )
        == "failed-protected-adoption-after-apply"
    )
    receipt["status"] = "running"
    assert (
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=2,
        )
        == "failed-protected-adoption-after-apply"
    )
    receipt["irreversibleFrontier"]["transitionId"] = "other"
    with pytest.raises(cli.SoperatorSafetyPauseError, match="frontier evidence"):
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=2,
        )


def test_bound_repair_binds_failed_preimage_restore_before_any_restore_intent() -> None:
    operation_spec: dict[str, Any] = {"intervention_generation": 2}
    operation_spec_sha256 = cli.soperator_sha256(operation_spec)
    transitions = [
        {"phase": "resolve-immutable-sources", "status": "complete"},
        {"phase": "capture-protected-data-plane", "status": "complete"},
        {"phase": "enforce-protected-volume-retention", "status": "complete"},
        {"phase": "establish-boot-storage-barrier", "status": "complete"},
        {"phase": "verify-sealed-jail-rootfs-classification", "status": "complete"},
        {"phase": "populate-passive-jail-rootfs", "status": "complete"},
        {"phase": "quiesce-legacy-release-owners", "status": "complete"},
        {
            "id": "apply-a",
            "phase": "apply-declarative-release",
            "status": "complete",
            "receiptSha256": "sha256:" + "a" * 64,
        },
        {"phase": "wait-flux-graph", "status": "complete"},
        {"phase": "apply-post-flux-manifests", "status": "complete"},
        {"phase": "verify-single-writer", "status": "complete"},
        {"phase": "adopt-protected-data-plane", "status": "complete"},
        {"phase": "wait-pre-restore-product-readiness", "status": "complete"},
        {
            "phase": "restore-infrastructure-and-scheduling-preimages",
            "status": "failed",
            "failureAttempts": 1,
            "failureType": "operation-error",
            "receiptSha256": None,
            "intentSha256": None,
        },
    ]
    receipt = {
        "status": "recovery-required",
        "operation": {"spec": operation_spec},
        "target": {"ref": "cluster-a"},
        "transitions": transitions,
        "irreversibleIntent": None,
        "irreversibleFrontier": {
            "phase": "apply-declarative-release",
            "disposition": "forward-only",
            "transitionId": "apply-a",
            "transitionReceiptSha256": "sha256:" + "a" * 64,
        },
    }

    assert (
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=2,
        )
        == "failed-preimage-restore-after-apply"
    )
    receipt["transitions"][-1]["intentSha256"] = "sha256:" + "b" * 64
    with pytest.raises(cli.SoperatorSafetyPauseError, match="preimage-restore failure"):
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=2,
        )


def test_bound_repair_binds_failed_pre_restore_readiness_after_apply() -> None:
    operation_spec: dict[str, Any] = {"intervention_generation": 10}
    operation_spec_sha256 = cli.soperator_sha256(operation_spec)
    transitions = [
        {"phase": "resolve-immutable-sources", "status": "complete"},
        {"phase": "capture-protected-data-plane", "status": "complete"},
        {"phase": "enforce-protected-volume-retention", "status": "complete"},
        {"phase": "establish-boot-storage-barrier", "status": "complete"},
        {"phase": "verify-sealed-jail-rootfs-classification", "status": "complete"},
        {"phase": "populate-passive-jail-rootfs", "status": "complete"},
        {"phase": "quiesce-legacy-release-owners", "status": "complete"},
        {
            "id": "apply-a",
            "phase": "apply-declarative-release",
            "status": "complete",
            "receiptSha256": "sha256:" + "a" * 64,
        },
        {"phase": "wait-flux-graph", "status": "complete"},
        {"phase": "apply-post-flux-manifests", "status": "complete"},
        {"phase": "verify-single-writer", "status": "complete"},
        {"phase": "adopt-protected-data-plane", "status": "complete"},
        {
            "phase": "wait-pre-restore-product-readiness",
            "status": "failed",
            "failureType": "operation-error",
            "receiptSha256": None,
        },
    ]
    receipt = {
        "status": "recovery-required",
        "operation": {"spec": operation_spec},
        "target": {"ref": "cluster-a"},
        "transitions": transitions,
        "irreversibleIntent": None,
        "irreversibleFrontier": {
            "phase": "apply-declarative-release",
            "disposition": "forward-only",
            "transitionId": "apply-a",
            "transitionReceiptSha256": "sha256:" + "a" * 64,
        },
    }

    assert (
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=10,
        )
        == "failed-pre-restore-readiness-after-apply"
    )
    receipt["transitions"][-1]["receiptSha256"] = "sha256:" + "b" * 64
    with pytest.raises(cli.SoperatorSafetyPauseError, match="pre-restore readiness"):
        cli._soperator_upgrade_bound_adapter_repair_frontier(
            receipt,
            target_ref="cluster-a",
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_intervention_generation=10,
        )


def test_repair_chain_reuses_authenticated_canonical_rootfs_predecessor(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.reports_dir.mkdir(parents=True, mode=0o700)
    base_spec: dict[str, Any] = {"intervention_generation": 0}
    base_spec_sha256 = cli.soperator_sha256(base_spec)
    transitions = [
        {"phase": "resolve-immutable-sources", "status": "complete"},
        {"phase": "capture-protected-data-plane", "status": "complete"},
        {"phase": "enforce-protected-volume-retention", "status": "complete"},
        {"phase": "establish-boot-storage-barrier", "status": "complete"},
        {"phase": "verify-sealed-jail-rootfs-classification", "status": "complete"},
        {"phase": "populate-passive-jail-rootfs", "status": "complete"},
        {"phase": "quiesce-legacy-release-owners", "status": "complete"},
        {
            "id": "apply-base",
            "phase": "apply-declarative-release",
            "status": "complete",
            "receiptSha256": "sha256:" + "a" * 64,
        },
        {"phase": "wait-flux-graph", "status": "complete"},
        {"phase": "apply-post-flux-manifests", "status": "complete"},
        {"phase": "verify-single-writer", "status": "complete"},
        {
            "phase": "adopt-protected-data-plane",
            "status": "failed",
            "failureType": "operation-error",
            "receiptSha256": None,
        },
    ]
    base_receipt: dict[str, Any] = {
        "operationId": "base-operation",
        "status": "recovery-required",
        "operation": {"spec": base_spec},
        "target": {"ref": "cluster-a"},
        "transitions": transitions,
        "irreversibleFrontier": {
            "phase": "apply-declarative-release",
            "disposition": "forward-only",
            "transitionId": "apply-base",
            "transitionReceiptSha256": "sha256:" + "a" * 64,
        },
    }
    cli.write_owner_only_json(
        paths.reports_dir / "soperator-release-reconcile-base.json",
        base_receipt,
    )
    repair_spec: dict[str, Any] = {"intervention_generation": 1}
    repair_spec_sha256 = cli.soperator_sha256(repair_spec)
    repair_receipt: dict[str, Any] = {
        "operationId": "repair-operation",
        "operation": {"spec": repair_spec},
        "target": {"ref": "cluster-a"},
        "repairLineage": {
            "schema": cli.SOPERATOR_RECONCILE_REPAIR_LINEAGE_SCHEMA,
            "predecessorOperationId": "base-operation",
            "predecessorOperationSpecSha256": base_spec_sha256,
            "predecessorReceiptSha256": cli.soperator_sha256(base_receipt),
            "predecessorFrontier": "failed-protected-adoption-after-apply",
            "resumePhase": "apply-declarative-release",
            "reason": "controller-spool-upstream-default-shadow-v1",
            "interventionGeneration": 1,
        },
    }

    rootfs_operation_sha256, rootfs_receipt = cli._soperator_upgrade_canonical_rootfs_predecessor(
        paths=paths,
        target_ref="cluster-a",
        predecessor_receipt=repair_receipt,
        predecessor_operation_spec_sha256=repair_spec_sha256,
        predecessor_intervention_generation=1,
    )

    assert rootfs_operation_sha256 == base_spec_sha256
    assert rootfs_receipt == base_receipt

    tampered = copy.deepcopy(repair_receipt)
    tampered["repairLineage"]["predecessorReceiptSha256"] = "sha256:" + "b" * 64
    with pytest.raises(cli.SoperatorSafetyPauseError, match="evidence changed"):
        cli._soperator_upgrade_canonical_rootfs_predecessor(
            paths=paths,
            target_ref="cluster-a",
            predecessor_receipt=tampered,
            predecessor_operation_spec_sha256=repair_spec_sha256,
            predecessor_intervention_generation=1,
        )


def test_bound_vm_stack_retry_repair_accepts_only_two_exact_patch_operations() -> None:
    base_operations = [
        {"op": "replace", "path": "/metadata/name", "value": "cxcli-vm-stack"},
        {
            "op": "add",
            "path": "/spec/values/victoria-metrics-operator/crds",
            "value": {"cleanup": {"enabled": False}},
        },
    ]
    expected_operations = [
        {
            "op": "test",
            "path": "/spec/install",
            "value": {"crds": "Skip", "remediation": {"retries": 3}},
        },
        {
            "op": "add",
            "path": "/spec/install/strategy",
            "value": {"name": "RetryOnFailure", "retryInterval": "30s"},
        },
    ]

    def _outer(operations: list[dict[str, Any]]) -> str:
        return yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "soperator-controller"},
                "spec": {
                    "postRenderers": [
                        {
                            "kustomize": {
                                "patches": [
                                    {
                                        "target": {"name": "soperator-fluxcd-vm-stack"},
                                        "patch": yaml.safe_dump(
                                            operations,
                                            sort_keys=False,
                                        ),
                                    }
                                ]
                            }
                        }
                    ]
                },
            },
            sort_keys=False,
        )

    current = _outer(base_operations)
    replacement = _outer([*base_operations, *expected_operations])

    assert cli._soperator_upgrade_vm_stack_retry_patch_is_exact(
        current,
        replacement,
    )
    assert (
        cli._soperator_upgrade_running_release_render_repair_kind(
            current,
            replacement,
        )
        == "vm-stack"
    )
    changed_interval = replacement.replace("retryInterval: 30s", "retryInterval: 5m")
    assert not cli._soperator_upgrade_vm_stack_retry_patch_is_exact(
        current,
        changed_interval,
    )


def test_bound_controller_storage_repair_accepts_only_exact_render_contract() -> None:
    base_operations = [
        {
            "op": "replace",
            "path": "/metadata/name",
            "value": "cxcli-soperator-fluxcd-slurm-cluster",
        }
    ]
    jail_submounts = [
        {
            "name": "jail-persistent-home",
            "mountPath": "/home",
            "volumeSourceName": "jail-persistent-home",
        }
    ]

    def _child_renderer(*, include_unsupported_submounts: bool) -> dict[str, Any]:
        operations: list[dict[str, Any]] = [
            {
                "op": "test",
                "path": "/spec/slurmNodes/controller/volumes/spool/volumeSourceName",
                "value": "controller-spool",
            },
            {
                "op": "remove",
                "path": ("/spec/slurmNodes/controller/volumes/spool/volumeClaimTemplateSpec"),
            },
        ]
        if include_unsupported_submounts:
            operations.extend(
                [
                    {
                        "op": "test",
                        "path": "/spec/slurmNodes/controller/volumes/jail/volumeSourceName",
                        "value": "jail",
                    },
                    {
                        "op": "add",
                        "path": "/spec/slurmNodes/controller/volumes/jailSubMounts",
                        "value": jail_submounts,
                    },
                ]
            )
        return {
            "op": "add",
            "path": "/spec/postRenderers",
            "value": [
                {
                    "kustomize": {
                        "patches": [
                            {
                                "target": {
                                    "group": "slurm.nebius.ai",
                                    "version": "v1",
                                    "kind": "SlurmCluster",
                                    "name": "soperator",
                                },
                                "patch": yaml.safe_dump(operations, sort_keys=False),
                            }
                        ]
                    }
                }
            ],
        }

    def _gate(role: str, name: str) -> dict[str, Any]:
        return {
            "name": f"mount-gate-{role}-{name}",
            "volumeMounts": [{"name": name, "mountPath": "/proof", "readOnly": True}],
        }

    def _outer(
        *,
        include_unsupported_submounts: bool,
        child_renderer: dict[str, Any],
        spool_source: str = "controller-spool",
    ) -> str:
        controller_volumes: dict[str, Any] = {
            "jail": {"volumeSourceName": "jail"},
            "spool": {"volumeSourceName": spool_source},
        }
        exporter_volumes: dict[str, Any] = {"jail": {"volumeSourceName": "jail"}}
        controller_init = [
            _gate("controller", "controller-spool"),
            _gate("controller", "jail"),
        ]
        exporter_init = [_gate("exporter", "jail")]
        if include_unsupported_submounts:
            controller_volumes["jailSubMounts"] = copy.deepcopy(jail_submounts)
            exporter_volumes["jailSubMounts"] = copy.deepcopy(jail_submounts)
            controller_init.append(_gate("controller", "jail-persistent-home"))
            exporter_init.append(_gate("exporter", "jail-persistent-home"))
        return yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "soperator-controller"},
                "spec": {
                    "values": {
                        "slurmCluster": {
                            "overrideValues": {
                                "clusterName": "soperator",
                                "slurmNodes": {
                                    "controller": {
                                        "volumes": controller_volumes,
                                        "customInitContainers": controller_init,
                                    },
                                    "login": {
                                        "volumes": {
                                            "jail": {"volumeSourceName": "jail"},
                                            "jailSubMounts": jail_submounts,
                                        },
                                        "customInitContainers": [
                                            _gate("login", "jail"),
                                            _gate("login", "jail-persistent-home"),
                                        ],
                                    },
                                    "exporter": {
                                        "volumes": exporter_volumes,
                                        "customInitContainers": exporter_init,
                                    },
                                },
                            }
                        }
                    },
                    "postRenderers": [
                        {
                            "kustomize": {
                                "patches": [
                                    {
                                        "target": {"name": "soperator-fluxcd-slurm-cluster"},
                                        "patch": yaml.safe_dump(
                                            [*base_operations, child_renderer],
                                            sort_keys=False,
                                        ),
                                    }
                                ]
                            }
                        }
                    ],
                },
            },
            sort_keys=False,
        )

    current = _outer(
        include_unsupported_submounts=True,
        child_renderer=_child_renderer(include_unsupported_submounts=True),
    )
    replacement = _outer(
        include_unsupported_submounts=False,
        child_renderer=_child_renderer(include_unsupported_submounts=False),
    )

    assert cli._soperator_upgrade_controller_spool_patch_is_exact(
        current,
        replacement,
    )
    assert (
        cli._soperator_upgrade_running_release_render_repair_kind(
            current,
            replacement,
        )
        == "controller-storage"
    )
    assert not cli._soperator_upgrade_controller_spool_patch_is_exact(
        current,
        _outer(
            include_unsupported_submounts=False,
            child_renderer=_child_renderer(include_unsupported_submounts=False),
            spool_source="other-spool",
        ),
    )
    wrong_post_renderer = _child_renderer(include_unsupported_submounts=False)
    wrong_post_renderer["value"][0]["kustomize"]["patches"][0]["patch"] = (
        "- op: remove\n  path: /spec/slurmNodes/controller/volumes/jail\n"
    )
    assert not cli._soperator_upgrade_controller_spool_patch_is_exact(
        current,
        _outer(
            include_unsupported_submounts=False,
            child_renderer=wrong_post_renderer,
        ),
    )


def test_registered_runtime_shape_repair_accepts_only_gpu_and_rest_bootstrap_delta() -> None:
    current_values: dict[str, Any] = {
        "slurmCluster": {
            "overrideValues": {
                "slurmNodes": {
                    "rest": {"enabled": True},
                    "controller": {
                        "customInitContainers": [
                            {
                                "name": "mount-gate-controller-jail",
                                "command": ["/bin/sh", "-ec", "set -eu\nmount-ok\n"],
                            }
                        ]
                    },
                }
            }
        },
        "nodesets": {
            "overrideValues": {
                "nodesets": [
                    {
                        "name": "worker-0",
                        "gpu": {"enabled": True},
                        "nodeConfig": {
                            "static": (
                                "Boards=1 SocketsPerBoard=1 CoresPerSocket=32 ThreadsPerCore=1"
                            ),
                            "gresConfig": ["AutoDetect=off Name=gpu File=/dev/nvidia[0-7]"],
                        },
                        "slurmd": {
                            "resources": {
                                "cpu": "13950m",
                                "memory": "54Gi",
                                "gpu": 8,
                                "nvidia.com/gpu": "1",
                            }
                        },
                    }
                ]
            }
        },
    }
    replacement_values = copy.deepcopy(current_values)
    replacement_gate = replacement_values["slurmCluster"]["overrideValues"]["slurmNodes"][
        "controller"
    ]["customInitContainers"][0]
    replacement_gate["command"][-1] += soperator_adapter._REST_JWT_CONFIG_GATE_SCRIPT
    replacement_nodeset = replacement_values["nodesets"]["overrideValues"]["nodesets"][0]
    replacement_nodeset["slurmd"]["resources"]["gpu"] = 1
    replacement_nodeset["slurmd"]["resources"].pop("nvidia.com/gpu")
    replacement_nodeset["nodeConfig"]["gresConfig"] = [
        "AutoDetect=off Name=gpu File=/dev/nvidia[0]"
    ]
    replacement_nodeset["nodeConfig"]["static"] = (
        "Boards=1 SocketsPerBoard=1 CoresPerSocket=7 ThreadsPerCore=2 Gres=gpu:1"
    )

    def _outer(values: Mapping[str, Any]) -> str:
        return yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "soperator-controller"},
                "spec": {"values": values},
            },
            sort_keys=False,
        )

    def _configmap(values: Mapping[str, Any]) -> str:
        return yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "terraform-fluxcd-values"},
                "data": {"values.yaml": yaml.safe_dump(values, sort_keys=False)},
            },
            sort_keys=False,
        )

    assert cli._soperator_upgrade_registered_runtime_shape_patch_is_exact(
        _outer(current_values),
        _outer(replacement_values),
    )
    assert cli._soperator_upgrade_registered_runtime_shape_patch_is_exact(
        _configmap(current_values),
        _configmap(replacement_values),
    )
    unsafe = copy.deepcopy(replacement_values)
    unsafe["nodesets"]["overrideValues"]["nodesets"][0]["slurmd"]["resources"]["memory"] = "40Gi"
    assert not cli._soperator_upgrade_registered_runtime_shape_patch_is_exact(
        _outer(current_values),
        _outer(unsafe),
    )


def test_registered_topology_disable_repair_accepts_only_explicit_empty_plugin() -> None:
    current_values: dict[str, Any] = {
        "slurmCluster": {
            "overrideValues": {
                "clusterName": "soperator",
                "slurmNodes": {"controller": {"replicas": 1}},
            }
        },
        "nodesets": {"overrideValues": {"nodesets": [{"name": "worker-0"}]}},
    }
    replacement_values = copy.deepcopy(current_values)
    replacement_values["slurmCluster"]["overrideValues"]["slurmConfig"] = {"topologyPlugin": ""}

    def _outer(values: Mapping[str, Any]) -> str:
        return yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "soperator-controller"},
                "spec": {"values": values},
            },
            sort_keys=False,
        )

    def _configmap(values: Mapping[str, Any]) -> str:
        return yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "terraform-fluxcd-values"},
                "data": {"values.yaml": yaml.safe_dump(values, sort_keys=False)},
            },
            sort_keys=False,
        )

    assert cli._soperator_upgrade_registered_topology_disable_patch_is_exact(
        _outer(current_values),
        _outer(replacement_values),
    )
    assert cli._soperator_upgrade_registered_topology_disable_patch_is_exact(
        _configmap(current_values),
        _configmap(replacement_values),
    )
    unsafe = copy.deepcopy(replacement_values)
    unsafe["nodesets"]["overrideValues"]["nodesets"][0]["replicas"] = 2
    assert not cli._soperator_upgrade_registered_topology_disable_patch_is_exact(
        _outer(current_values),
        _outer(unsafe),
    )
    enabled = copy.deepcopy(replacement_values)
    enabled["slurmCluster"]["overrideValues"]["slurmConfig"]["topologyPlugin"] = "topology/tree"
    assert not cli._soperator_upgrade_registered_topology_disable_patch_is_exact(
        _outer(current_values),
        _outer(enabled),
    )


def test_registered_operator_capability_repair_accepts_only_graph_derived_flags() -> None:
    current_values: dict[str, Any] = {
        "soperator": {"overrideValues": {"controllerManager": {"affinity": {"nodeAffinity": {}}}}},
        "securityProfilesOperator": {"enabled": True},
        "mariadbOperator": {"enabled": True},
        "observability": {"enabled": False},
    }
    replacement_values = copy.deepcopy(current_values)
    replacement_values["soperator"]["overrideValues"]["controllerManager"]["manager"] = {
        "env": {
            "isApparmorCrdInstalled": True,
            "isMariadbCrdInstalled": True,
            "isPrometheusCrdInstalled": False,
        }
    }

    def _outer(values: Mapping[str, Any]) -> str:
        return yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "soperator-controller"},
                "spec": {"values": values},
            },
            sort_keys=False,
        )

    def _configmap(values: Mapping[str, Any]) -> str:
        return yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "terraform-fluxcd-values"},
                "data": {"values.yaml": yaml.safe_dump(values, sort_keys=False)},
            },
            sort_keys=False,
        )

    assert cli._soperator_upgrade_registered_operator_capability_patch_is_exact(
        _outer(current_values),
        _outer(replacement_values),
    )
    assert cli._soperator_upgrade_registered_operator_capability_patch_is_exact(
        _configmap(current_values),
        _configmap(replacement_values),
    )
    unsafe = copy.deepcopy(replacement_values)
    unsafe["observability"]["enabled"] = True
    assert not cli._soperator_upgrade_registered_operator_capability_patch_is_exact(
        _outer(current_values),
        _outer(unsafe),
    )
    wrong_flag = copy.deepcopy(replacement_values)
    wrong_flag["soperator"]["overrideValues"]["controllerManager"]["manager"]["env"][
        "isMariadbCrdInstalled"
    ] = False
    assert not cli._soperator_upgrade_registered_operator_capability_patch_is_exact(
        _outer(current_values),
        _outer(wrong_flag),
    )


def test_registered_static_partition_repair_accepts_only_default_equivalence() -> None:
    current_values: dict[str, Any] = {
        "slurmCluster": {
            "overrideValues": {
                "partitionConfiguration": {"configType": "default"},
            }
        },
        "nodesets": {
            "overrideValues": {
                "nodesets": [{"name": "worker-0", "replicas": 2, "slurmd": {}}],
            }
        },
    }
    replacement_values = copy.deepcopy(current_values)
    replacement_values["slurmCluster"]["overrideValues"]["partitionConfiguration"] = {
        "configType": "structured",
        "partitions": [
            {
                "name": "main",
                "isAll": True,
                "config": (
                    "Default=YES State=UP MaxTime=INFINITE PriorityTier=10 OverSubscribe=YES"
                ),
            },
            {
                "name": "hidden",
                "isAll": True,
                "config": (
                    "Default=NO Hidden=YES State=UP MaxTime=INFINITE "
                    "PriorityTier=10 PreemptMode=OFF OverSubscribe=YES"
                ),
            },
        ],
    }
    revision = cli._soperator_registered_static_config_revision(
        {
            "nodesets": replacement_values["nodesets"]["overrideValues"]["nodesets"],
            "partitionConfiguration": replacement_values["slurmCluster"]["overrideValues"][
                "partitionConfiguration"
            ],
        }
    )
    replacement_values["nodesets"]["overrideValues"]["nodesets"][0]["workerAnnotations"] = {
        cli._SOPERATOR_STATIC_CONFIG_REVISION_ANNOTATION: revision
    }
    replacement_values["nodesets"]["overrideValues"]["nodesets"][0].setdefault("slurmd", {})[
        "customEnv"
    ] = [
        {
            "name": cli._SOPERATOR_STATIC_CONFIG_REVISION_ENV,
            "value": revision,
        }
    ]

    def _outer(values: Mapping[str, Any]) -> str:
        return yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "soperator-controller"},
                "spec": {"values": values},
            },
            sort_keys=False,
        )

    def _configmap(values: Mapping[str, Any]) -> str:
        return yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "terraform-fluxcd-values"},
                "data": {"values.yaml": yaml.safe_dump(values, sort_keys=False)},
            },
            sort_keys=False,
        )

    assert cli._soperator_upgrade_registered_static_partition_patch_is_exact(
        _outer(current_values),
        _outer(replacement_values),
    )
    assert cli._soperator_upgrade_registered_static_partition_patch_is_exact(
        _configmap(current_values),
        _configmap(replacement_values),
    )
    unsafe = copy.deepcopy(replacement_values)
    unsafe["slurmCluster"]["overrideValues"]["partitionConfiguration"]["partitions"][0][
        "config"
    ] = "Default=YES State=UP MaxTime=01:00:00 PriorityTier=10 OverSubscribe=YES"
    assert not cli._soperator_upgrade_registered_static_partition_patch_is_exact(
        _outer(current_values),
        _outer(unsafe),
    )

    rollout_current = copy.deepcopy(replacement_values)
    rollout_worker = rollout_current["nodesets"]["overrideValues"]["nodesets"][0]
    rollout_worker["slurmd"].pop("customEnv")
    assert cli._soperator_upgrade_registered_static_worker_rollout_patch_is_exact(
        _outer(rollout_current),
        _outer(replacement_values),
    )
    assert cli._soperator_upgrade_registered_static_worker_rollout_patch_is_exact(
        _configmap(rollout_current),
        _configmap(replacement_values),
    )


def test_registered_scheduling_runtime_repair_requires_frozen_partitions_and_mounts() -> None:
    preimage = cli.build_slurm_upgrade_preimage(
        partitions=cli.parse_scontrol_show_partition_states(
            "\n".join(
                (
                    "PartitionName=background State=UP Nodes=worker-[0-1] NodeSets=ALL "
                    "Hidden=YES PriorityTier=1 DefaultTime=NONE TotalNodes=2 TotalCPUs=32",
                    "PartitionName=main State=UP Nodes=worker-[0-1] NodeSets=ALL "
                    "Default=YES PriorityTier=10 DefaultTime=NONE TotalNodes=2 TotalCPUs=32",
                )
            )
        ),
        jobs=(),
        reservations=(),
    )
    current_values: dict[str, Any] = {
        "slurmCluster": {
            "overrideValues": {
                "partitionConfiguration": {
                    "configType": "structured",
                    "partitions": [
                        {
                            "name": "main",
                            "isAll": True,
                            "config": "Default=YES PriorityTier=10 State=UP",
                        }
                    ],
                }
            }
        },
        "nodesets": {
            "overrideValues": {
                "nodesets": [
                    {
                        "name": "worker-0",
                        "replicas": 2,
                        "slurmd": {
                            "volumes": {
                                "customVolumeMounts": [
                                    {
                                        "name": "gpu-health-sysfs",
                                        "mountPath": "/mnt/jail/sys-host",
                                        "readOnly": True,
                                        "volumeSource": {
                                            "hostPath": {
                                                "path": "/sys",
                                                "type": "Directory",
                                            }
                                        },
                                    }
                                ]
                            }
                        },
                    }
                ]
            }
        },
    }
    current_worker = current_values["nodesets"]["overrideValues"]["nodesets"][0]
    current_revision = cli._soperator_registered_legacy_static_config_revision(
        {
            "nodesets": [current_worker],
            "partitionConfiguration": current_values["slurmCluster"]["overrideValues"][
                "partitionConfiguration"
            ],
        }
    )
    current_worker["workerAnnotations"] = {
        cli._SOPERATOR_STATIC_CONFIG_REVISION_ANNOTATION: current_revision
    }
    current_worker["slurmd"]["customEnv"] = [
        {
            "name": cli._SOPERATOR_STATIC_CONFIG_REVISION_ENV,
            "value": current_revision,
        }
    ]
    replacement_values = copy.deepcopy(current_values)
    replacement_values["slurmCluster"]["overrideValues"]["partitionConfiguration"] = (
        cli.slurm_partition_configuration_from_preimage(
            preimage.partitions,
            desired_state="DOWN",
        )
    )
    replacement_worker = replacement_values["nodesets"]["overrideValues"]["nodesets"][0]
    assert cli._materialize_soperator_registered_runtime_mounts({"nodesets": [replacement_worker]})
    replacement_revision = cli._soperator_registered_static_config_revision(
        {
            "nodesets": [replacement_worker],
            "partitionConfiguration": replacement_values["slurmCluster"]["overrideValues"][
                "partitionConfiguration"
            ],
        }
    )
    replacement_worker["workerAnnotations"] = {
        cli._SOPERATOR_STATIC_CONFIG_REVISION_ANNOTATION: replacement_revision
    }
    replacement_worker["slurmd"]["customEnv"] = [
        {
            "name": cli._SOPERATOR_STATIC_CONFIG_REVISION_ENV,
            "value": replacement_revision,
        }
    ]

    def _outer(values: Mapping[str, Any]) -> str:
        return yaml.safe_dump(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "soperator-controller"},
                "spec": {"values": values},
            },
            sort_keys=False,
        )

    assert cli._soperator_upgrade_scheduling_runtime_patch_is_exact(
        _outer(current_values),
        _outer(replacement_values),
        slurm_preimage=preimage,
    )
    sentinel_current = copy.deepcopy(replacement_values)
    sentinel_current["slurmCluster"]["overrideValues"]["partitionConfiguration"] = (
        cli.slurm_partition_configuration_from_preimage(
            preimage.partitions,
            desired_state="DOWN",
            output_sentinel_policy="legacy-v1",
        )
    )
    sentinel_worker = sentinel_current["nodesets"]["overrideValues"]["nodesets"][0]
    sentinel_revision = cli._soperator_registered_static_config_revision(
        {
            "nodesets": [sentinel_worker],
            "partitionConfiguration": sentinel_current["slurmCluster"]["overrideValues"][
                "partitionConfiguration"
            ],
        }
    )
    sentinel_worker["workerAnnotations"] = {
        cli._SOPERATOR_STATIC_CONFIG_REVISION_ANNOTATION: sentinel_revision
    }
    sentinel_worker["slurmd"]["customEnv"] = [
        {
            "name": cli._SOPERATOR_STATIC_CONFIG_REVISION_ENV,
            "value": sentinel_revision,
        }
    ]
    assert cli._soperator_upgrade_scheduling_runtime_patch_is_exact(
        _outer(sentinel_current),
        _outer(replacement_values),
        slurm_preimage=preimage,
    )
    unsafe = copy.deepcopy(replacement_values)
    unsafe["slurmCluster"]["overrideValues"]["partitionConfiguration"]["partitions"][0][
        "config"
    ] += " PriorityTier=99"
    assert not cli._soperator_upgrade_scheduling_runtime_patch_is_exact(
        _outer(current_values),
        _outer(unsafe),
        slurm_preimage=preimage,
    )


def test_bound_vm_stack_retry_repair_requires_exact_current_stalled_child() -> None:
    payload: dict[str, Any] = {
        "metadata": {
            "name": "cxcli-soperator-fluxcd-vm-stack",
            "namespace": "flux-system",
            "generation": 2,
            "labels": {
                "soperator.nebius.ai/release-graph": "nebius-cxcli",
                "app.kubernetes.io/version": "4.1.7",
            },
        },
        "spec": {
            "install": {"crds": "Skip", "remediation": {"retries": 3}},
        },
        "status": {
            "observedGeneration": 2,
            "conditions": [
                {
                    "type": "Stalled",
                    "status": "True",
                    "reason": "RetriesExceeded",
                },
                {
                    "type": "Ready",
                    "status": "False",
                    "reason": "InstallFailed",
                },
            ],
            "history": [{"chartVersion": "0.39.4"}],
        },
    }

    cli._validate_soperator_upgrade_vm_stack_retry_live_repair(
        payload,
        expected_release="4.1.7",
    )

    payload["metadata"]["generation"] = 3
    with pytest.raises(cli.SoperatorSafetyPauseError, match="exact current stalled child"):
        cli._validate_soperator_upgrade_vm_stack_retry_live_repair(
            payload,
            expected_release="4.1.7",
        )


def test_bound_adapter_repair_accepts_only_prewrite_population_failure() -> None:
    _receipt, operation_spec_sha256 = _bound_adapter_repair_receipt()
    journal = _failed_population_journal(operation_spec_sha256)
    pvc_uid, workload_sha256 = cli._soperator_upgrade_failed_population_journal_evidence(
        journal,
        expected_operation_spec_sha256=operation_spec_sha256,
    )

    resources = _failed_population_resources(operation_spec_sha256)
    foreign_resources = _failed_population_resources("sha256:" + "c" * 64)
    resources["items"].extend(foreign_resources["items"])
    cli._soperator_upgrade_failed_population_workload_is_prewrite(
        resources,
        expected_operation_spec_sha256=operation_spec_sha256,
        expected_pvc_uid=pvc_uid,
        expected_workload_sha256=workload_sha256,
    )

    completed = copy.deepcopy(journal)
    completed["stages"]["rootfs-passive-target-populate"]["status"] = "complete"
    with pytest.raises(cli.SoperatorSafetyPauseError, match="pre-completion intent"):
        cli._soperator_upgrade_failed_population_journal_evidence(
            completed,
            expected_operation_spec_sha256=operation_spec_sha256,
        )

    started = _failed_population_resources(operation_spec_sha256)
    started["items"][1]["status"]["containerStatuses"][0].update(
        {"started": True, "containerID": "containerd://main"}
    )
    with pytest.raises(cli.SoperatorSafetyPauseError, match="may have started"):
        cli._soperator_upgrade_failed_population_workload_is_prewrite(
            started,
            expected_operation_spec_sha256=operation_spec_sha256,
            expected_pvc_uid=pvc_uid,
            expected_workload_sha256=workload_sha256,
        )


def _discarded_inventory_replay(
    *,
    completed: bool = False,
    writable: bool = False,
) -> tuple[
    cli.SoperatorOperationSpec,
    cli.SoperatorRootfsAdmissionPreflight,
    dict[str, Any],
    dict[str, Any],
]:
    preflight = _rootfs_admission()
    operation_spec = cli.SoperatorOperationSpec(
        target_ref="cluster-a",
        ownership="managed",
        strategy="protected-data-plane",
        current_release="1.22.3",
        target_release="4.1.7",
        source_contract="protected-data-plane-v1",
        target_contract="protected-data-plane-v1",
        source_capability_sha256="sha256:" + "1" * 64,
        target_capability_sha256="sha256:" + "2" * 64,
        stage_plan_sha256="sha256:" + "3" * 64,
        release_snapshot_sha256="sha256:" + "4" * 64,
        target_jail_image=preflight.target_image,
        target_jail_image_source="upstream-default",
        nebius_cluster_id="mk8s-a",
        kubernetes_uid="kube-uid-a",
        infrastructure_plan_sha256="sha256:" + "5" * 64,
        desired_values_sha256="sha256:" + "6" * 64,
        adapter_sha256="sha256:" + "7" * 64,
        protected_state_sha256="sha256:" + "8" * 64,
        scheduling_sha256="sha256:" + "9" * 64,
        admission_sha256="sha256:" + "a" * 64,
        intervention_generation=1,
    )
    operation_spec_sha256 = cli.soperator_sha256(asdict(operation_spec))
    fence_epoch = 3
    pvc_uid = "passive-pvc-uid"
    name = (
        f"cxcli-rootfs-{operation_spec_sha256.removeprefix('sha256:')[:12]}-"
        f"passive-preflight-e{fence_epoch}"
    )
    requested_manifest = cli.bind_protected_job_authority(
        cli.rootfs_inventory_job_manifest(
            namespace="soperator",
            name=name,
            image=preflight.target_image,
            pvc_name=preflight.target_pvc_name,
        ),
        operation_id=operation_spec_sha256,
        fence_epoch=fence_epoch,
        pvc_uid=pvc_uid,
    )
    requested = cli.protected_workload_identity(requested_manifest)
    job = cli.bind_protected_workload_identity(
        requested_manifest,
        requested_workload_sha256=requested.workload_sha256,
        admitted_workload_sha256=requested.workload_sha256,
    )
    job["metadata"]["uid"] = "job-uid-a"
    job["metadata"]["resourceVersion"] = "123"
    job["status"] = (
        {
            "succeeded": 1,
            "active": 0,
            "conditions": [{"type": "Complete", "status": "True"}],
        }
        if completed
        else {"active": 1, "succeeded": 0, "failed": 0}
    )
    if writable:
        job["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["readOnly"] = False
    template = copy.deepcopy(job["spec"]["template"])
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            **template["metadata"],
            "ownerReferences": [{"kind": "Job", "uid": "job-uid-a", "controller": True}],
        },
        "spec": template["spec"],
        "status": {"phase": "Running"},
    }
    journal = {
        "schema": "nebius-cxcli.soperator-recovery-journal.v4",
        "status": "active",
        "operationId": operation_spec_sha256,
        "fencingEpoch": fence_epoch,
        "stages": {
            "rootfs-admission-decision": {
                "status": "complete",
                "intent": cli._soperator_rootfs_admission_stage_evidence(preflight),
                "evidence": cli._soperator_rootfs_admission_stage_evidence(preflight),
            },
            "rootfs-passive-target-identity": {
                "status": "complete",
                "intent": {
                    "pvcName": preflight.target_pvc_name,
                    "targetSlot": preflight.target_slot,
                },
                "evidence": {"pvcUid": pvc_uid},
            },
        },
    }
    return operation_spec, preflight, journal, {"items": [job, pod]}


def test_discarded_inventory_replay_accepts_only_active_read_only_job() -> None:
    operation_spec, preflight, journal, resources = _discarded_inventory_replay()

    evidence = cli._soperator_upgrade_discarded_inventory_job_evidence(
        resources,
        operation_spec=operation_spec,
        journal=journal,
        preflight=preflight,
    )

    assert evidence.name.startswith("cxcli-rootfs-")
    assert evidence.uid == "job-uid-a"
    assert evidence.resource_version == "123"
    assert evidence.workload_sha256.startswith("sha256:")
    assert evidence.pod_identity_sha256.startswith("sha256:")


def test_sealed_rootfs_job_verification_rejects_reused_uid_before_cluster_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "a" * 64
    journal = {
        "stages": {
            stage: {
                "intent": {"workloadSha256": digest},
                "evidence": {
                    "jobUid": "reused-job-uid",
                    "admittedWorkloadSha256": digest,
                },
            }
            for stage in (
                "rootfs-passive-target-preflight",
                "rootfs-passive-target-populate",
                "rootfs-passive-target-inventory",
            )
        }
    }
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_kubectl",
        lambda *_args, **_kwargs: pytest.fail("cluster read should not run"),
    )

    with pytest.raises(cli.SoperatorSafetyPauseError, match="reuse a workload UID"):
        cli._soperator_upgrade_sealed_rootfs_jobs_are_complete(
            journal=journal,
            operation_spec_sha256="sha256:" + "b" * 64,
            kube_context="ctx",
            extra_env=None,
        )


def test_post_activation_rootfs_uses_sealed_pre_activation_evidence() -> None:
    manifest_sha256 = "sha256:" + "a" * 64
    materialization: dict[str, Any] = {
        "schema": "nebius-cxcli.soperator-rootfs-materialization.v1",
        "image": "registry.example/rootfs@sha256:" + "b" * 64,
        "slot": "slot-b",
        "pvcName": "jail-rootfs-slot-b",
        "manifestSha256": manifest_sha256,
        "entryCount": 42,
    }
    materialization["receiptSha256"] = cli.soperator_sha256(materialization)
    stage = {
        "status": "complete",
        "evidence": {
            "manifestSha256": manifest_sha256,
            "materialization": materialization,
        },
    }

    evidence = cli._soperator_upgrade_post_activation_rootfs_evidence(
        stage,
        expected_image=materialization["image"],
        expected_slot="slot-b",
        expected_pvc_name="jail-rootfs-slot-b",
    )

    assert evidence == {
        "sealedManifestSha256": manifest_sha256,
        "materializationReceiptSha256": materialization["receiptSha256"],
        "postActivationPolicy": "target-runtime-owned-except-protected-mounts",
    }


@pytest.mark.parametrize("changed_field", ["image", "manifest", "receipt", "entryCount"])
def test_post_activation_rootfs_rejects_changed_sealed_evidence(
    changed_field: str,
) -> None:
    manifest_sha256 = "sha256:" + "a" * 64
    materialization: dict[str, Any] = {
        "schema": "nebius-cxcli.soperator-rootfs-materialization.v1",
        "image": "registry.example/rootfs@sha256:" + "b" * 64,
        "slot": "slot-b",
        "pvcName": "jail-rootfs-slot-b",
        "manifestSha256": manifest_sha256,
        "entryCount": 42,
    }
    materialization["receiptSha256"] = cli.soperator_sha256(materialization)
    stage: dict[str, Any] = {
        "status": "complete",
        "evidence": {
            "manifestSha256": manifest_sha256,
            "materialization": materialization,
        },
    }
    if changed_field == "image":
        materialization["image"] = "registry.example/other@sha256:" + "c" * 64
    elif changed_field == "manifest":
        stage["evidence"]["manifestSha256"] = "sha256:" + "d" * 64
    elif changed_field == "receipt":
        materialization["receiptSha256"] = "sha256:" + "e" * 64
    else:
        materialization["entryCount"] = 0

    with pytest.raises(RuntimeError, match="sealed passive jail/rootfs evidence changed"):
        cli._soperator_upgrade_post_activation_rootfs_evidence(
            stage,
            expected_image="registry.example/rootfs@sha256:" + "b" * 64,
            expected_slot="slot-b",
            expected_pvc_name="jail-rootfs-slot-b",
        )


def test_discarded_inventory_replay_rejects_completed_or_writable_job() -> None:
    operation_spec, preflight, journal, completed = _discarded_inventory_replay(completed=True)
    with pytest.raises(cli.SoperatorSafetyPauseError, match="already completed"):
        cli._soperator_upgrade_discarded_inventory_job_evidence(
            completed,
            operation_spec=operation_spec,
            journal=journal,
            preflight=preflight,
        )

    operation_spec, preflight, journal, writable = _discarded_inventory_replay(writable=True)
    with pytest.raises(cli.SoperatorSafetyPauseError, match="exact active read-only"):
        cli._soperator_upgrade_discarded_inventory_job_evidence(
            writable,
            operation_spec=operation_spec,
            journal=journal,
            preflight=preflight,
        )


def test_discarded_inventory_replay_rejects_malformed_stage_without_attribute_error() -> None:
    operation_spec, preflight, journal, resources = _discarded_inventory_replay()
    journal["stages"]["rootfs-passive-target-identity"] = "malformed"

    with pytest.raises(cli.SoperatorSafetyPauseError, match="read-only inventory boundary"):
        cli._soperator_upgrade_discarded_inventory_job_evidence(
            resources,
            operation_spec=operation_spec,
            journal=journal,
            preflight=preflight,
        )


class _DiscardedReplayJournal:
    def __init__(self, payload: Mapping[str, object]) -> None:
        self.payload = copy.deepcopy(dict(payload))
        self.events: list[str] = []

    def snapshot(self) -> dict[str, object]:
        return copy.deepcopy(self.payload)

    def begin_safe_replay_supersession(self, **evidence: object) -> Mapping[str, object]:
        self.events.append("begin")
        self.payload["supersededReplayIntent"] = {
            "predecessorOperationId": evidence["predecessor_operation_id"],
            "discardedReplayReceiptSha256": evidence["discarded_replay_receipt_sha256"],
            "workloadName": evidence["workload_name"],
            "workloadUid": evidence["workload_uid"],
            "workloadResourceVersion": evidence["workload_resource_version"],
            "workloadSha256": evidence["workload_sha256"],
            "podIdentitySha256": evidence["pod_identity_sha256"],
        }
        return self.payload

    def supersede_safe_replay(self) -> Mapping[str, object]:
        self.events.append("seal")
        self.payload["status"] = "superseded-safe-replay"
        self.payload["supersededReplay"] = self.payload["supersededReplayIntent"]
        return self.payload


def test_supersede_discarded_inventory_replay_uses_write_ahead_uid_delete() -> None:
    operation_spec, preflight, payload, resources = _discarded_inventory_replay()
    journal = _DiscardedReplayJournal(payload)
    commands: list[tuple[tuple[str, ...], str | None]] = []
    remaining = copy.deepcopy(resources)

    def _run(args: list[str], **kwargs: object) -> cli._SoperatorUpgradeCommandResult:
        nonlocal remaining
        command = tuple(args)
        commands.append((command, kwargs.get("input_text")))
        if "get" in command:
            return cli._SoperatorUpgradeCommandResult(command, 0, json.dumps(remaining), "")
        if "--raw" in command:
            remaining = {"items": []}
        return cli._SoperatorUpgradeCommandResult(command, 0, "", "")

    authority_calls: list[str] = []
    discarded_sha256 = "sha256:" + "e" * 64
    result = cli._soperator_upgrade_supersede_discarded_inventory_replay(
        journal=journal,
        operation_spec=operation_spec,
        preflight=preflight,
        predecessor_operation_spec_sha256="sha256:" + "d" * 64,
        discarded_replay_receipt_sha256=discarded_sha256,
        kube_context="ctx",
        runner=_run,
        assert_authority=lambda: authority_calls.append("asserted"),
    )

    raw_calls = [(command, body) for command, body in commands if "--raw" in command]
    assert result == discarded_sha256
    assert journal.events == ["begin", "seal"]
    assert authority_calls == ["asserted"]
    assert len(raw_calls) == 1
    raw_command = raw_calls[0][0]
    raw_index = raw_command.index("--raw")
    assert raw_command[raw_index + 1].endswith("/jobs/" + resources["items"][0]["metadata"]["name"])
    assert raw_command[-2:] == ("-f", "-")
    delete_options = json.loads(raw_calls[0][1] or "{}")
    assert delete_options["apiVersion"] == "v1"
    assert delete_options["kind"] == "DeleteOptions"
    assert delete_options["propagationPolicy"] == "Foreground"
    assert delete_options["preconditions"] == {
        "uid": "job-uid-a",
        "resourceVersion": "123",
    }


def test_supersede_discarded_inventory_replay_never_deletes_completed_job() -> None:
    operation_spec, preflight, payload, resources = _discarded_inventory_replay(completed=True)
    journal = _DiscardedReplayJournal(payload)
    commands: list[tuple[str, ...]] = []

    def _run(args: list[str], **_kwargs: object) -> cli._SoperatorUpgradeCommandResult:
        command = tuple(args)
        commands.append(command)
        return cli._SoperatorUpgradeCommandResult(command, 0, json.dumps(resources), "")

    with pytest.raises(cli.SoperatorSafetyPauseError, match="already completed"):
        cli._soperator_upgrade_supersede_discarded_inventory_replay(
            journal=journal,
            operation_spec=operation_spec,
            preflight=preflight,
            predecessor_operation_spec_sha256="sha256:" + "d" * 64,
            discarded_replay_receipt_sha256="sha256:" + "e" * 64,
            kube_context="ctx",
            runner=_run,
            assert_authority=lambda: pytest.fail("delete authority was requested"),
        )

    assert journal.events == []
    assert all("--raw" not in command for command in commands)


def test_supersede_discarded_inventory_replay_resumes_exact_foreground_delete() -> None:
    operation_spec, preflight, payload, resources = _discarded_inventory_replay()
    evidence = cli._soperator_upgrade_discarded_inventory_job_evidence(
        resources,
        operation_spec=operation_spec,
        journal=payload,
        preflight=preflight,
    )
    discarded_sha256 = "sha256:" + "e" * 64
    payload["supersededReplayIntent"] = {
        "predecessorOperationId": "sha256:" + "d" * 64,
        "discardedReplayReceiptSha256": discarded_sha256,
        "workloadName": evidence.name,
        "workloadUid": evidence.uid,
        "workloadResourceVersion": evidence.resource_version,
        "workloadSha256": evidence.workload_sha256,
        "podIdentitySha256": evidence.pod_identity_sha256,
    }
    resources["items"][0]["metadata"]["deletionTimestamp"] = "2026-08-29T00:00:00Z"
    resources["items"][0]["metadata"]["resourceVersion"] = "124"
    journal = _DiscardedReplayJournal(payload)
    remaining = copy.deepcopy(resources)
    commands: list[tuple[str, ...]] = []

    def _run(args: list[str], **_kwargs: object) -> cli._SoperatorUpgradeCommandResult:
        nonlocal remaining
        command = tuple(args)
        commands.append(command)
        if "wait" in command:
            remaining = {"items": []}
            return cli._SoperatorUpgradeCommandResult(command, 0, "", "")
        return cli._SoperatorUpgradeCommandResult(command, 0, json.dumps(remaining), "")

    authority_calls: list[str] = []
    result = cli._soperator_upgrade_supersede_discarded_inventory_replay(
        journal=journal,
        operation_spec=operation_spec,
        preflight=preflight,
        predecessor_operation_spec_sha256="sha256:" + "d" * 64,
        discarded_replay_receipt_sha256=discarded_sha256,
        kube_context="ctx",
        runner=_run,
        assert_authority=lambda: authority_calls.append("asserted"),
    )

    assert result == discarded_sha256
    assert journal.events == ["seal"]
    assert authority_calls == ["asserted"]
    assert any("wait" in command for command in commands)
    assert all("--raw" not in command for command in commands)


def test_upgrade_non_tty_defaults_to_guarded_requeue_hold_all_and_rejects_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)

    assert cli._soperator_runtime_job_policy(None, upgrade=True) == "requeue-hold-all"
    assert cli._soperator_runtime_job_policy("wait-to-finish", upgrade=True) == "wait-to-finish"
    with pytest.raises(RuntimeError, match="job-policy must be one of"):
        cli._soperator_runtime_job_policy("fail", upgrade=True)


def test_upgrade_wizard_shows_partition_policy_before_structured_job_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda message, *args, **kwargs: events.append(("print", str(message))),
    )

    def _prompt(path_label, current, *, choices, required=True, missing):
        events.append(("prompt", path_label, current, tuple(choice.value for choice in choices)))
        assert required is True
        assert missing == "Slurm job policy"
        return current

    monkeypatch.setattr(cli, "_prompt_upgrade_choice", _prompt)

    resolved = cli._validate_soperator_upgrade_job_controls(
        job_policy=None,
        cancel_job_ids=(),
        requeue_job_ids=(),
        interactive=True,
    )

    assert resolved == "requeue-hold-all"
    assert events == [
        (
            "print",
            "Partition Policy: pause-all-active (required; pause every active Slurm partition)",
        ),
        (
            "prompt",
            "soperator.upgrade.job_policy",
            "requeue-hold-all",
            (
                "requeue-hold-all",
                "wait-to-finish",
                "wait-then-cancel",
                "requeue-all",
                "cancel-all",
                "interactive",
            ),
        ),
    ]


def test_upgrade_chart_freeze_failure_closes_initialized_sdk_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    paths = _paths(tmp_path)
    target = SimpleNamespace(target_ref="cluster-a")
    generated_config = SimpleNamespace(
        client_info=SimpleNamespace(nebius=SimpleNamespace(project_id="project-a"))
    )
    selected_target = {
        "target_ref": "cluster-a",
        "kind": "managed-mk8s",
        "ownership": "managed",
        "component_id": "mk8s",
    }
    close_calls: list[str] = []
    initialized_sdks: list[Any] = []
    real_init_nebius_sdk = cli.init_nebius_sdk

    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "offline-test-token")
    caplog.set_level("ERROR", logger="nebius.aio._runtime")

    def _init_sdk(**kwargs: object) -> Any:
        sdk = real_init_nebius_sdk(**kwargs)
        initialized_sdks.append(sdk)
        original_sync_close = sdk.sync_close

        def _close_once() -> None:
            close_calls.append("close")
            original_sync_close()

        sdk.sync_close = _close_once
        return sdk

    class _Executor:
        def __init__(self, received_sdk: object) -> None:
            assert received_sdk is initialized_sdks[0]

        def get_cluster_by_name(self, *, project_id: str, name: str) -> object:
            assert (project_id, name) == ("project-a", "cluster-a")
            return object()

        def list_node_groups(self, cluster_id: str) -> tuple[()]:
            assert cluster_id == "mk8scluster-a"
            return ()

        def control_plane_versions(self) -> tuple[str, str]:
            return ("1.34", "1.35")

    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: {})
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_command_target",
        lambda *_args, **_kwargs: (target, None, False),
    )
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (generated_config, paths, {}),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_selected_deploy_targets",
        lambda *_args, **_kwargs: [selected_target],
    )
    monkeypatch.setattr(cli, "find_source_mk8s_component", lambda *_args: {})
    monkeypatch.setattr(
        cli,
        "_generated_bundle_mk8s_module_index",
        lambda _manifest: {"cluster": ("mk8s", "cluster-a")},
    )
    monkeypatch.setattr(cli, "load_campaign_receipt", lambda _path: None)
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *_args, **_kwargs: {
            cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "mk8scluster-a",
            cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx-a",
        },
    )
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **_kwargs: "uid-a")
    monkeypatch.setattr(cli, "init_nebius_sdk", _init_sdk)
    monkeypatch.setattr(cli, "Mk8sKubernetesVersionExecutor", _Executor)
    monkeypatch.setattr(cli, "source_mk8s_cluster_name", lambda *_args, **_kwargs: "cluster-a")
    monkeypatch.setattr(cli, "_live_mk8s_cluster_id", lambda *_args, **_kwargs: "mk8scluster-a")
    monkeypatch.setattr(
        cli,
        "_cluster_control_plane_minor_version",
        lambda *_args, **_kwargs: "1.34",
    )
    monkeypatch.setattr(
        cli,
        "_live_soperator_release_for_reconcile",
        lambda **_kwargs: "4.1.6",
    )
    monkeypatch.setattr(
        cli,
        "freeze_soperator_release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("official chart download timed out")
        ),
    )

    with pytest.raises(typer.Exit) as exc_info:
        cli.soperator_upgrade_command(
            paths.config_path,
            target_ref="cluster-a",
            to_chart_version="4.1.7",
            to_k8s_version="1.35",
            to_os="auto",
            to_gpu_stack_preset="auto",
            dry_run=True,
            execute=False,
            interactive=False,
        )

    assert exc_info.value.exit_code == 1
    assert len(initialized_sdks) == 1
    assert close_calls == ["close"]
    assert initialized_sdks[0]._runtime.owned
    assert initialized_sdks[0]._runtime.event_loop.is_closed()
    assert not any(
        "The SDK runtime could not shut down" in record.getMessage() for record in caplog.records
    )
    output = capsys.readouterr().out
    assert "ERROR: official chart download timed out" in output
    assert "SDK runtime could not shut down" not in output


def test_upgrade_raw_recovery_prefix_remains_retryable() -> None:
    disposition = cli._soperator_upgrade_failure_disposition(
        "scheduling-gate",
        RuntimeError("recovery-required: partition postimage is ambiguous"),
    )

    assert disposition is SoperatorFailureDisposition.RETRY


def test_upgrade_supervisor_retry_detail_is_actionable_bounded_and_secret_safe() -> None:
    assert (
        cli._soperator_upgrade_supervisor_failure_detail(
            RuntimeError("project generation preimage changed")
        )
        == "RuntimeError: project generation preimage changed"
    )
    redacted = cli._soperator_upgrade_supervisor_failure_detail(
        RuntimeError("request failed: token=do-not-print password: also-private")
    )
    assert "do-not-print" not in redacted
    assert "also-private" not in redacted
    assert redacted.count("<redacted>") == 2
    assert len(cli._soperator_upgrade_supervisor_failure_detail(RuntimeError("x" * 500))) <= 337


def test_upgrade_supervisor_retry_detail_redacts_pem_material() -> None:
    detail = cli._soperator_upgrade_supervisor_failure_detail(
        RuntimeError("-----BEGIN PRIVATE KEY----- sensitive -----END PRIVATE KEY-----")
    )

    assert detail == "RuntimeError: sensitive detail redacted"


def test_upgrade_project_generation_postimage_requires_exact_safe_files(
    tmp_path: Path,
) -> None:
    target = tmp_path / "generated" / "main.tf"
    target.parent.mkdir()
    target.write_bytes(b"expected\n")
    plan = cli.ProjectGenerationPlan(
        writes={target: b"expected\n"},
        removals=(),
        sha256="sha256:" + "1" * 64,
        expected_preimages={target: "sha256:" + "2" * 64},
        preimage_sha256="sha256:" + "3" * 64,
    )

    assert cli._project_generation_plan_matches_current_postimage(plan, project_dir=tmp_path)
    target.write_bytes(b"changed\n")
    assert not cli._project_generation_plan_matches_current_postimage(plan, project_dir=tmp_path)
    assert cli._project_generation_plan_matches_current_postimage(
        plan, project_dir=tmp_path, semantic_source_paths=(target,)
    )
    assert not cli._project_generation_plan_matches_current_postimage(
        replace(plan, writes={target: b"changed\n"}, removals=(target,)),
        project_dir=tmp_path,
    )


def test_upgrade_lease_acquisition_retries_same_authority_object() -> None:
    class _Lease:
        def __init__(self) -> None:
            self.enter_count = 0

        def __enter__(self):
            self.enter_count += 1
            if self.enter_count < 3:
                raise RuntimeError("Unable to acquire the Soperator operation Lease")
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    lease = _Lease()
    sleeps: list[float] = []
    with cli.ExitStack() as stack:
        acquired = cli._acquire_soperator_upgrade_lease(
            stack=stack,
            lease=lease,
            sleep=sleeps.append,
        )

    assert acquired is lease
    assert lease.enter_count == 3
    assert sleeps == [5.0, 10.0]


def test_nested_upgrade_reuses_parent_cross_command_lease() -> None:
    class _ParentLease:
        cluster_id = "cluster-a"
        kube_context = "context-a"

        def __init__(self) -> None:
            self.assertions = 0

        def assert_held(self) -> None:
            self.assertions += 1

    class _ChildLease:
        cluster_id = "cluster-a"
        kube_context = "context-a"

        def __enter__(self):
            raise AssertionError("nested reconciliation must not acquire another Lease")

    parent = _ParentLease()
    token = cli._SOPERATOR_PARENT_OPERATION_LEASE.set(parent)
    try:
        with cli.ExitStack() as stack:
            acquired = cli._acquire_soperator_upgrade_lease(
                stack=stack,
                lease=_ChildLease(),
                sleep=lambda _seconds: None,
            )
    finally:
        cli._SOPERATOR_PARENT_OPERATION_LEASE.reset(token)

    assert acquired is parent
    assert parent.assertions == 1


def test_upgrade_pause_barrier_covers_every_live_up_partition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _state(name: str, state: str) -> cli.SlurmPartitionState:
        record = f"Nodes={name}-nodes PartitionName={name} State={state}"
        return cli.SlurmPartitionState(
            name=name,
            state=state,
            record=record,
            record_fingerprint=cli.slurm_partition_record_fingerprint(record),
            nodes=f"{name}-nodes",
        )

    initial = (_state("gpu", "UP"), _state("cpu", "UP"), _state("maintenance", "DOWN"))
    live_reads: dict[str, int] = {"gpu": 0, "cpu": 0}
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_partition_state_snapshot",
        lambda **_kwargs: initial,
    )

    def _live_partition(*, partition: str, **_kwargs: Any) -> cli.SlurmPartitionState:
        live_reads[partition] += 1
        return _state(partition, "UP" if live_reads[partition] == 1 else "DOWN")

    monkeypatch.setattr(cli, "_soperator_upgrade_partition_state", _live_partition)
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_login_command",
        lambda *_args, **_kwargs: cli._SoperatorUpgradeCommandResult((), 0, "", ""),
    )
    recorded: list[cli.SlurmPartitionPauseRecord] = []

    result = cli._soperator_upgrade_pause_slurm_partitions(
        namespace="soperator",
        node_names=(),
        all_active_partitions=True,
        restore_on_failure=False,
        record_recorder=recorded.append,
    )

    assert {item.partition for item in result} == {"gpu", "cpu"}
    assert all(item.applied_state == "DOWN" for item in result)
    assert "maintenance" not in {item.partition for item in result}
    assert len(recorded) == 4


def test_upgrade_pause_rechecks_authority_before_each_partition_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _state(name: str, state: str) -> cli.SlurmPartitionState:
        record = f"Nodes={name}-nodes PartitionName={name} State={state}"
        return cli.SlurmPartitionState(
            name=name,
            state=state,
            record=record,
            record_fingerprint=cli.slurm_partition_record_fingerprint(record),
            nodes=f"{name}-nodes",
        )

    initial = (_state("gpu", "UP"), _state("cpu", "UP"))
    live_reads: dict[str, int] = {"gpu": 0, "cpu": 0}
    mutations: list[str] = []
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_partition_state_snapshot",
        lambda **_kwargs: initial,
    )

    def _live_partition(*, partition: str, **_kwargs: Any) -> cli.SlurmPartitionState:
        live_reads[partition] += 1
        return _state(partition, "UP" if live_reads[partition] == 1 else "DOWN")

    def _run(
        _namespace: str,
        command: str,
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        mutations.append(command)
        return cli._SoperatorUpgradeCommandResult((), 0, "", "")

    def _assert_authority() -> None:
        if mutations:
            raise RuntimeError("lease authority was lost")

    monkeypatch.setattr(cli, "_soperator_upgrade_partition_state", _live_partition)
    monkeypatch.setattr(cli, "_run_soperator_upgrade_login_command", _run)
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_restore_slurm_partitions",
        lambda **_kwargs: pytest.fail("lost authority must not trigger compensation"),
    )

    with pytest.raises(RuntimeError, match="lease authority was lost"):
        cli._soperator_upgrade_pause_slurm_partitions(
            namespace="soperator",
            node_names=(),
            all_active_partitions=True,
            restore_on_failure=True,
            record_recorder=lambda _record: None,
            mutation_guard=_assert_authority,
        )

    assert len(mutations) == 1
    assert "PartitionName=gpu" in mutations[0]


def test_upgrade_all_active_barrier_runs_without_discovered_worker_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pause_calls: list[dict[str, object]] = []

    def _pause(**kwargs: object) -> tuple[cli.SlurmPartitionPauseRecord, ...]:
        pause_calls.append(dict(kwargs))
        return ()

    monkeypatch.setattr(cli, "_soperator_upgrade_pause_slurm_partitions", _pause)
    monkeypatch.setattr(cli, "_soperator_upgrade_partition_state_snapshot", lambda **_kwargs: ())
    monkeypatch.setattr(cli, "_soperator_upgrade_affected_jobs", lambda **_kwargs: ())

    result = cli._handle_soperator_upgrade_running_jobs(
        namespace="soperator",
        node_names=(),
        policy="wait-to-finish",
        cancel_job_ids=(),
        requeue_job_ids=(),
        wait_timeout_seconds=0,
        refresh_interval_seconds=1,
        checkpoint_id="a" * 16,
        drain_nodes=False,
        slurm_scheduling_pause=True,
        decision_recorder=lambda _event: None,
        all_active_partitions=True,
        continue_until_clear=True,
    )

    assert result == ()
    assert len(pause_calls) == 1
    assert pause_calls[0]["all_active_partitions"] is True


def test_upgrade_headless_retry_needs_no_tui_when_no_jobs_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli, "_soperator_upgrade_pause_slurm_partitions", lambda **_kwargs: ())
    monkeypatch.setattr(cli, "_soperator_upgrade_partition_state_snapshot", lambda **_kwargs: ())
    monkeypatch.setattr(cli, "_soperator_upgrade_affected_jobs", lambda **_kwargs: ())

    result = cli._handle_soperator_upgrade_running_jobs(
        namespace="soperator",
        node_names=(),
        policy="interactive",
        cancel_job_ids=(),
        requeue_job_ids=(),
        wait_timeout_seconds=0,
        refresh_interval_seconds=1,
        checkpoint_id="b" * 16,
        drain_nodes=False,
        slurm_scheduling_pause=True,
        decision_recorder=lambda _event: None,
        all_active_partitions=True,
        continue_until_clear=True,
    )

    assert result == ()


def test_requeue_hold_all_mutates_only_proven_batch_jobs_and_waits_for_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = parse_scontrol_show_job_record(
        "JobId=41 JobName=train UserId=alice(1001) JobState=RUNNING "
        "BatchFlag=1 SubmitTime=2026-09-01T10:00:00 Priority=100 Reason=None"
    )
    held = parse_scontrol_show_job_record(
        "JobId=41 JobName=train UserId=alice(1001) JobState=PENDING "
        "BatchFlag=2 SubmitTime=2026-09-01T10:00:00 Priority=0 Reason=JobHeldAdmin"
    )
    completing = parse_scontrol_show_job_record(
        "JobId=42 JobName=cleanup UserId=bob(1002) JobState=COMPLETING "
        "BatchFlag=1 SubmitTime=2026-09-01T10:01:00 Priority=90 Reason=None"
    )
    jobs = (
        cli.AffectedSlurmJob(
            "41",
            "alice",
            "RUNNING",
            "main",
            "worker-0",
            "",
            "",
            "",
            "1:00",
            "2:00",
            "1:00",
            "train",
            "cluster-wide",
        ),
        cli.AffectedSlurmJob(
            "42",
            "bob",
            "COMPLETING",
            "main",
            "worker-1",
            "",
            "",
            "",
            "1:00",
            "2:00",
            "1:00",
            "cleanup",
            "cluster-wide",
        ),
    )
    affected_reads = 0

    def _affected(**_kwargs):
        nonlocal affected_reads
        affected_reads += 1
        return jobs if affected_reads == 1 else ()

    control_reads = {"41": 0, "42": 0}

    def _control(*, job_id: str, **_kwargs):
        control_reads[job_id] += 1
        if job_id == "42":
            return completing
        return held if control_reads[job_id] == 3 else running

    actions: list[Mapping[str, Any]] = []
    mutations: list[tuple[tuple[str, ...], bool]] = []
    monkeypatch.setattr(cli, "_soperator_upgrade_affected_jobs", _affected)
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_requeue_jobs",
        lambda _namespace, job_ids, *, hold, **_kwargs: mutations.append((tuple(job_ids), hold)),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_wait_for_requeued_jobs_to_leave_nodes",
        lambda **_kwargs: (),
    )
    monkeypatch.setattr(cli, "_soperator_upgrade_wait_for_jobs", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_cancel_jobs",
        lambda *_args, **_kwargs: pytest.fail("guarded default must never cancel jobs"),
    )

    result = cli._handle_soperator_upgrade_running_jobs(
        namespace="soperator",
        node_names=("worker-0", "worker-1"),
        policy="requeue-hold-all",
        cancel_job_ids=(),
        requeue_job_ids=(),
        wait_timeout_seconds=0,
        refresh_interval_seconds=1,
        checkpoint_id="c" * 16,
        drain_nodes=False,
        decision_recorder=actions.append,
        job_control_reader=lambda job_id: _control(job_id=job_id),
    )

    assert result == ()
    assert mutations == [(("41",), True)]
    assert any(
        action["action"] == "requeue-hold-all-wait-only"
        and action["job_ids"] == ["42"]
        and action["reason"] == "job is completing"
        for action in actions
    )
    applied = next(action for action in actions if action["action"] == "requeue-hold-all-applied")
    assert applied["job_control_postimages"][0]["identity_sha256"] == held.identity_sha256


def test_requeue_hold_revalidation_rejects_job_id_reuse_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = cli.AffectedSlurmJob(
        "41",
        "alice",
        "RUNNING",
        "main",
        "worker-0",
        "",
        "",
        "",
        "1:00",
        "2:00",
        "1:00",
        "train",
        "cluster-wide",
    )
    before = parse_scontrol_show_job_record(
        "JobId=41 JobName=train UserId=alice(1001) JobState=RUNNING "
        "BatchFlag=1 SubmitTime=2026-09-01T10:00:00 Priority=100 Reason=None"
    )
    reused = parse_scontrol_show_job_record(
        "JobId=41 JobName=train UserId=alice(1001) JobState=RUNNING "
        "BatchFlag=1 SubmitTime=2026-09-01T11:00:00 Priority=100 Reason=None"
    )
    reads = iter((before, reused))
    monkeypatch.setattr(cli, "_soperator_upgrade_affected_jobs", lambda **_kwargs: (job,))
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_requeue_jobs",
        lambda *_args, **_kwargs: pytest.fail("identity drift must block mutation"),
    )

    with pytest.raises(RuntimeError, match="identity changed immediately before"):
        cli._handle_soperator_upgrade_running_jobs(
            namespace="soperator",
            node_names=("worker-0",),
            policy="requeue-hold-all",
            cancel_job_ids=(),
            requeue_job_ids=(),
            wait_timeout_seconds=0,
            refresh_interval_seconds=1,
            checkpoint_id="d" * 16,
            drain_nodes=False,
            decision_recorder=lambda _event: None,
            job_control_reader=lambda _job_id: next(reads),
        )


def test_campaign_restoration_uses_only_exact_applied_held_job_postimages() -> None:
    held = parse_scontrol_show_job_record(
        "JobId=41 JobName=train UserId=alice(1001) JobState=PENDING "
        "BatchFlag=2 SubmitTime=2026-09-01T10:00:00 Priority=0 Reason=JobHeldAdmin"
    )
    records = applied_slurm_held_job_records(
        (
            {
                "action": "requeue-hold-all",
                "job_ids": ["99"],
                "job_control_preimages": [],
            },
            {
                "action": "requeue-hold-all-applied",
                "job_ids": ["41"],
                "job_control_postimages": [held.as_payload()],
            },
        )
    )

    assert records == (held,)


def test_campaign_restoration_rejects_legacy_or_reused_held_job_bindings() -> None:
    held = parse_scontrol_show_job_record(
        "JobId=41 JobName=train UserId=alice(1001) JobState=PENDING "
        "BatchFlag=2 SubmitTime=2026-09-01T10:00:00 Priority=0 Reason=JobHeldAdmin"
    )
    reused = parse_scontrol_show_job_record(
        "JobId=41 JobName=train UserId=alice(1001) JobState=PENDING "
        "BatchFlag=2 SubmitTime=2026-09-01T11:00:00 Priority=0 Reason=JobHeldAdmin"
    )
    with pytest.raises(RuntimeError, match="lacks an exact job identity"):
        applied_slurm_held_job_records(
            (
                {
                    "action": "requeue-hold-all-applied",
                    "job_ids": ["41"],
                    "jobs": [{"job_id": "41"}],
                },
            )
        )
    with pytest.raises(RuntimeError, match="reused job IDs"):
        applied_slurm_held_job_records(
            (
                {
                    "action": "requeue-hold-all-applied",
                    "job_control_postimages": [held.as_payload()],
                },
                {
                    "action": "requeue-hold-all-applied",
                    "job_control_postimages": [reused.as_payload()],
                },
            )
        )


def test_upgrade_all_active_barrier_rejects_malformed_partition_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_login_command",
        lambda *_args, **_kwargs: cli._SoperatorUpgradeCommandResult(
            (),
            0,
            "PartitionName=gpu State",
            "",
        ),
    )

    with pytest.raises(ValueError, match="Slurm partition observation line 1"):
        cli._soperator_upgrade_partition_state_snapshot(namespace="soperator")


def test_upgrade_all_active_barrier_rejects_empty_partition_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_login_command",
        lambda *_args, **_kwargs: cli._SoperatorUpgradeCommandResult((), 0, "", ""),
    )

    with pytest.raises(RuntimeError, match="Could not inspect any Slurm partition states"):
        cli._soperator_upgrade_partition_state_snapshot(namespace="soperator")


def test_upgrade_all_active_job_scope_queries_the_whole_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running_queries: list[tuple[str, ...]] = []
    pending_queries: list[tuple[tuple[str, ...], tuple[str, ...], bool]] = []
    running = cli.AffectedSlurmJob(
        job_id="101",
        user="alice",
        state="RUNNING",
        partition="gpu",
        allocated_nodes="worker-0",
        requested_nodes="",
        scheduled_nodes="worker-0",
        reason="",
        elapsed="1:00",
        limit="2:00",
        remaining="1:00",
        name="job",
        impact_scope="allocated-node",
    )

    def _running_jobs(*, node_names: tuple[str, ...], **_kwargs: object):
        running_queries.append(tuple(node_names))
        return (running,)

    def _pending_jobs(
        *,
        node_names: tuple[str, ...],
        partitions: tuple[str, ...],
        all_jobs: bool = False,
        **_kwargs: object,
    ):
        pending_queries.append((tuple(node_names), tuple(partitions), all_jobs))
        return ()

    monkeypatch.setattr(cli, "_soperator_upgrade_running_jobs", _running_jobs)
    monkeypatch.setattr(cli, "_soperator_upgrade_pending_jobs", _pending_jobs)

    jobs = cli._soperator_upgrade_affected_jobs(
        namespace="soperator",
        node_names=("worker-0",),
        include_pending=True,
        all_jobs=True,
    )

    assert jobs == (running,)
    assert running_queries == [()]
    assert pending_queries == [((), (), True)]


def test_upgrade_all_active_pending_scope_keeps_every_cluster_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_login_command",
        lambda *_args, **_kwargs: cli._SoperatorUpgradeCommandResult(
            (),
            0,
            "102|alice|PENDING|gpu||worker-[0-1]||Resources|0:00|2:00|2:00|job\n",
            "",
        ),
    )

    jobs = cli._soperator_upgrade_pending_jobs(
        namespace="soperator",
        node_names=(),
        partitions=(),
        all_jobs=True,
    )

    assert tuple(job.job_id for job in jobs) == ("102",)
    assert jobs[0].impact_scope == "cluster-wide"


def _slurm_recovery_test_context(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[cli.SoperatorLeaseAuthority, dict[str, str]]:
    authority = cli.SoperatorLeaseAuthority(
        lease_name="lease-a",
        lease_uid="lease-uid-a",
        holder_identity_sha256="sha256:" + "a" * 64,
        fencing_epoch=3,
        operation_fingerprint="sha256:" + "b" * 64,
    )
    cluster: dict[str, Any] = {}
    monkeypatch.setattr(
        cli,
        "_read_soperator_slurm_cluster_journal",
        lambda **_kwargs: (copy.deepcopy(cluster), "10") if cluster else None,
    )

    def _write(journal: Mapping[str, Any], **_kwargs: object) -> None:
        cluster.clear()
        cluster.update(copy.deepcopy(dict(journal)))

    monkeypatch.setattr(cli, "_write_soperator_slurm_cluster_journal", _write)
    return authority, {
        cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "mk8s-a",
        cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx-a",
    }


@pytest.mark.parametrize(
    "current_journal",
    [
        {"leaseUid": "new-lease", "fencingEpoch": 4},
        {"leaseUid": "other-lease", "fencingEpoch": 3},
    ],
)
def test_cluster_slurm_journal_rejects_stale_or_foreign_fence(
    monkeypatch: pytest.MonkeyPatch,
    current_journal: dict[str, object],
) -> None:
    monkeypatch.setattr(
        cli,
        "_read_soperator_slurm_cluster_journal",
        lambda **_kwargs: (current_journal, "12"),
    )
    writes: list[tuple[str, ...]] = []

    def _run(args, **_kwargs):
        writes.append(tuple(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl_cluster", _run)

    with pytest.raises(RuntimeError, match="fencing authority"):
        cli._write_soperator_slurm_cluster_journal(
            {"leaseUid": "lease-uid-a", "fencingEpoch": 3},
            target_ref="cluster-a",
            cluster_id="mk8s-a",
            kube_context="ctx-a",
            extra_env=None,
        )

    assert writes == []


def test_cluster_slurm_journal_allows_strictly_newer_fence_with_resource_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "_read_soperator_slurm_cluster_journal",
        lambda **_kwargs: ({"leaseUid": "lease-old", "fencingEpoch": 2}, "12"),
    )
    manifests: list[dict[str, object]] = []

    def _run(args, **kwargs):
        assert "replace" in args
        manifests.append(json.loads(str(kwargs["input_text"])))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl_cluster", _run)

    cli._write_soperator_slurm_cluster_journal(
        {"leaseUid": "lease-new", "fencingEpoch": 3},
        target_ref="cluster-a",
        cluster_id="mk8s-a",
        kube_context="ctx-a",
        extra_env=None,
    )

    assert manifests[0]["metadata"]["resourceVersion"] == "12"


def test_root_exposes_one_soperator_command_group() -> None:
    click_root = typer.main.get_command(cli.app)
    soperator_roots = {
        name
        for name in click_root.commands
        if "soperator" in re.sub(r"[^a-z0-9]", "", name.lower())
    }
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert soperator_roots == {"soperator"}
    output = _normalized(result.output)
    assert "soperator" in output
    assert _retired_soperator_root_command() not in output


@pytest.mark.parametrize("suffix", ((), ("--help",)))
def test_retired_soperator_root_command_is_not_invocable(suffix: tuple[str, ...]) -> None:
    command = _retired_soperator_root_command()
    result = runner.invoke(cli.app, [command, *suffix])

    assert result.exit_code == 2
    assert "No such command" in result.output
    assert command in result.output


def test_soperator_help_exposes_unified_commands_and_release_flag() -> None:
    registered_commands = [
        command.name for command in cli.soperator_app.registered_commands if command.name
    ]
    assert registered_commands == [
        "install",
        "discover",
        "onboard",
        "upgrade",
        "status",
        "destroy",
    ]

    group = runner.invoke(cli.app, ["soperator", "--help"])
    install = runner.invoke(cli.app, ["soperator", "install", "--help"])
    onboard = runner.invoke(cli.app, ["soperator", "onboard", "--help"])
    destroy = runner.invoke(cli.app, ["soperator", "destroy", "--help"])
    discover = runner.invoke(cli.app, ["soperator", "discover", "--help"])
    status = runner.invoke(cli.app, ["soperator", "status", "--help"])
    upgrade = runner.invoke(cli.app, ["soperator", "upgrade", "--help"])
    render = runner.invoke(cli.app, ["render", "--help"])
    flux_destroy = runner.invoke(cli.app, ["flux", "destroy", "--help"])
    deploy = runner.invoke(cli.app, ["deploy", "--help"])

    assert group.exit_code == 0
    assert install.exit_code == 0
    assert onboard.exit_code == 0
    assert destroy.exit_code == 0
    assert discover.exit_code == 0
    assert status.exit_code == 0
    assert upgrade.exit_code == 0
    assert render.exit_code == 0
    assert flux_destroy.exit_code == 0
    assert deploy.exit_code == 0
    group_output = _normalized(group.output)
    for command in (
        "install",
        "onboard",
        "discover",
        "destroy",
        "status",
        "upgrade",
    ):
        assert command in group_output
    install_output = _normalized_cli_help(install.output)
    assert "--profile" in install_output
    assert "--profile mixed --release latest --no-interactive --dry-run" in group_output
    assert "--profile gpu --release latest --no-interactive --dry-run" in install_output
    assert "a fresh --no-interactive install requires this option" in install_output
    assert "--resume rejects it and reuses the frozen release" in install_output
    destroy_output = _normalized(destroy.output)
    assert "--target" in destroy_output
    assert "--dry-run" in destroy_output
    assert "--yes" not in destroy_output
    assert "--approve" not in destroy_output
    discover_output = _normalized(discover.output)
    assert "OUTPUT_ROOT" in discover_output
    assert "CONFIG_YAML" not in discover_output
    for required_option in ("--tenant-id", "--project-id", "--cluster-id"):
        assert required_option in discover_output
    for removed_option in (
        "--target",
        "--output-dir",
        "--namespace",
        "--release-name",
        "--redaction",
        "--interactive",
    ):
        assert removed_option not in discover_output
    for removed_option in (
        "--to-release",
        "--to-k8s-version",
        "--to-os",
        "--to-gpu-stack-preset",
    ):
        assert removed_option not in discover_output
    onboard_output = _normalized(onboard.output)
    assert "or registration" not in onboard_output
    for removed_option in (
        "--no-validate-sources",
        "--source-version",
        "--storage-mode",
        "--compute-mode",
        "--compute-migration-mode",
        "--to-k8s-version",
    ):
        assert removed_option not in onboard_output

    status_output = _normalized(status.output)
    assert "--target" in status_output
    assert "--live" in status_output
    assert "--no-live" in status_output

    render_output = _normalized(render.output)
    for lifecycle_command in ("install", "onboard", "upgrade", "destroy"):
        assert f"soperator {lifecycle_command}" in render_output

    flux_destroy_output = _normalized(flux_destroy.output)
    assert "Bundles containing Soperator are rejected" in flux_destroy_output
    assert "soperator destroy CONFIG --target TARGET" in flux_destroy_output
    assert "Removes Soperator CRs first" not in flux_destroy_output

    upgrade_output = _normalized_cli_help(upgrade.output)
    assert "--to-release" in upgrade_output
    assert "A fresh --no-interactive upgrade requires this option" in upgrade_output
    assert "Recovery may omit it" in upgrade_output
    assert "must match the frozen campaign" in upgrade_output
    assert "Explicit read-only" in upgrade_output
    assert "planning mode" in upgrade_output
    assert "Required read-only mode" not in upgrade_output
    assert "--to-chart-version" not in upgrade_output
    assert _retired_soperator_root_command() not in upgrade_output
    for full_stack_option in (
        "--to-k8s-version",
        "--to-os",
        "--to-gpu-stack-preset",
        "--node-group-os",
        "--node-group-gpu-sta",
        "--node-group-strategy",
        "--strategy-max-surge",
        "--drain-timeout",
    ):
        assert full_stack_option in upgrade_output
    for removed_option in (
        "--zero-size-gpu-validation",
        "--allow-provider-api-upgrade",
    ):
        assert removed_option not in upgrade_output
    for removed_option in (
        "--populate-jail-refresh",
        "--jail-persistent-mount",
        "--jail-sfs-resize-policy",
        "--stop-for-remediation-approval",
    ):
        assert removed_option not in upgrade_output

    deploy_output = _normalized(deploy.output)
    assert "Configs or generated bundles containing Soperator lifecycle state are rejected" in (
        deploy_output
    )
    assert "rerun the same approved `soperator upgrade --execute --approve` command" in (
        deploy_output
    )
    for retired_phrase in (
        "remaining upgrade segment",
        "later registration",
        "reruns/resume",
    ):
        assert retired_phrase not in deploy_output


def test_soperator_commands_expose_exact_canonical_option_sets() -> None:
    click_group = typer.main.get_command(cli.soperator_app)
    contract = _soperator_cli_contract_payload()
    actual = {
        name: _soperator_command_metadata(command) for name, command in click_group.commands.items()
    }
    expected = {
        str(name): {key: value for key, value in definition.items() if key != "help_clauses"}
        for name, definition in contract["commands"].items()
    }

    assert _normalized(str(click_group.help or "")) == contract["group_help"]
    assert list(click_group.commands) == contract["command_order"]
    assert actual == expected


def test_saved_onboard_evidence_command_uses_only_canonical_options(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"

    command = cli._soperator_onboard_evidence_command_args(
        config_path=config_path,
        cluster_id="mk8scluster-a",
        target_ref="cluster-a",
        kube_context="ctx-a",
        access="internal",
    )

    assert command == (
        "nebius-cxcli",
        "soperator",
        "onboard",
        str(config_path),
        "--cluster-id",
        "mk8scluster-a",
        "--target-id",
        "cluster-a",
        "--access",
        "internal",
        "--kube-context",
        "ctx-a",
        "--no-interactive",
    )
    canonical_options = _soperator_cli_contract()["onboard"]
    persisted_options = {item for item in command if item.startswith("--")}
    assert persisted_options <= canonical_options


def _soperator_lifecycle_guard_payload(state: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-a",
                "project_id": "project-a",
                "region_id": "eu-north1",
            },
        },
        "infra": {"components": []},
        "apps": {"charts": []},
    }
    if state == "none":
        pass
    elif state == "disabled-app":
        payload["apps"] = {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster-a",
                    "enabled": False,
                }
            ]
        }
    elif state == "registration-marker":
        payload["deploy"] = {
            "targets": [
                {
                    "instance_id": "cluster-a",
                    "soperator_registration": None,
                }
            ]
        }
    else:  # pragma: no cover - test helper contract
        raise AssertionError(f"unknown guard state: {state}")
    return payload


@pytest.mark.parametrize("state", ["disabled-app", "registration-marker"])
def test_generic_render_rejects_all_soperator_lifecycle_state_before_side_effects(
    state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "deployments" / "tenant-a" / "project-a" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(_soperator_lifecycle_guard_payload(state)),
        encoding="utf-8",
    )
    load_attempts: list[bool] = []

    def _load_config(_path: Path, *, persist_normalized: bool = True) -> dict[str, object]:
        load_attempts.append(persist_normalized)
        pytest.fail("render reached config normalization")

    monkeypatch.setattr(cli, "load_config", _load_config)
    monkeypatch.setattr(
        cli,
        "_ensure_runtime_auth_material",
        lambda *_args, **_kwargs: pytest.fail("render reached authentication"),
    )

    result = runner.invoke(cli.app, ["render", str(config_path), "--force"])

    assert result.exit_code == 1, result.output
    assert "does not manage Soperator clusters" in _normalized(result.output)
    assert load_attempts == []
    assert not (config_path.parent / "generated").exists()


@pytest.mark.parametrize("state", ["disabled-app", "registration-marker"])
@pytest.mark.parametrize("lifecycle_location", ["source", "manifest", "both"])
@pytest.mark.parametrize(
    "argv_template",
    [
        ("deploy", "{config}"),
        ("destroy", "{config}", "--yes"),
        ("terraform", "plan", "{generated}"),
        ("terraform", "apply", "{generated}"),
        ("terraform", "destroy", "{generated}", "--yes"),
        ("terraform", "unlock", "{generated}", "--force"),
        ("flux", "bootstrap", "{generated}"),
        ("flux", "apply", "{generated}"),
        ("flux", "destroy", "{generated}", "--yes"),
    ],
)
def test_generic_generated_commands_reject_all_soperator_lifecycle_state_before_side_effects(
    state: str,
    lifecycle_location: str,
    argv_template: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "deployments" / "tenant-a" / "project-a"
    generated_dir = project_dir / "generated"
    for subtree in ("infra", "flux", "reports"):
        (generated_dir / subtree).mkdir(parents=True, exist_ok=True)
    config_path = project_dir / "config.yaml"
    source_state = state if lifecycle_location in {"source", "both"} else "none"
    manifest_state = state if lifecycle_location in {"manifest", "both"} else "none"
    config_path.write_text(
        json.dumps(_soperator_lifecycle_guard_payload(source_state)),
        encoding="utf-8",
    )
    (generated_dir / "nebius-cxcli-manifest.json").write_text(
        json.dumps(
            {
                "schema": "nebius-cxcli-generated/v1",
                "runtime_config": _soperator_lifecycle_guard_payload(manifest_state),
                "render": {"terraform_tfvars": {"sentinel": "must-not-write"}},
            }
        ),
        encoding="utf-8",
    )
    side_effects: list[str] = []

    def _unexpected(label: str) -> None:
        side_effects.append(label)
        pytest.fail(f"generic command reached {label}")

    monkeypatch.setattr(
        cli,
        "_apply_generated_tool_version_overrides",
        lambda *_args, **_kwargs: _unexpected("tool overrides"),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_runtime_auth_material",
        lambda *_args, **_kwargs: _unexpected("authentication"),
    )
    monkeypatch.setattr(
        cli,
        "_materialize_generated_terraform_tfvars",
        lambda *_args, **_kwargs: _unexpected("terraform tfvars"),
    )
    argv = [token.format(config=config_path, generated=generated_dir) for token in argv_template]

    result = runner.invoke(cli.app, argv)

    assert result.exit_code == 1, result.output
    assert "does not manage Soperator clusters" in _normalized(result.output)
    assert side_effects == []
    assert not (generated_dir / "infra" / "terraform.auto.tfvars.json").exists()


def test_soperator_internal_lifecycle_context_exempts_dedicated_commands() -> None:
    token = cli._SOPERATOR_LIFECYCLE_INTERNAL.set(True)
    try:
        cli._require_soperator_lifecycle_scope(
            _soperator_lifecycle_guard_payload("disabled-app"),
            command="render",
        )
    finally:
        cli._SOPERATOR_LIFECYCLE_INTERNAL.reset(token)


def test_soperator_onboard_forwards_the_complete_public_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_register_existing_soperator_target",
        lambda **kwargs: captured.update(kwargs),
    )
    target_path = tmp_path / "deployments"

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "onboard",
            str(target_path),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-a",
            "--project-id",
            "project-a",
            "--region-id",
            "eu-north1",
            "--email",
            "ops@example.invalid",
            "--cluster-id",
            "mk8scluster-a",
            "--target-id",
            "cluster-a",
            "--kube-context",
            "ctx-a",
            "--access",
            "internal",
            "--no-interactive",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "target_path": target_path,
        "client_name": "client-a",
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "region_id": "eu-north1",
        "email": "ops@example.invalid",
        "cluster_id": "mk8scluster-a",
        "target_id": "cluster-a",
        "kube_context": "ctx-a",
        "access": "internal",
        "interactive": False,
    }


def test_soperator_discover_forwards_the_complete_public_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "support"
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli,
        "_run_soperator_public_discovery_command",
        lambda **kwargs: calls.append(dict(kwargs)) or {"status": "complete"},
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "discover",
            str(output_root),
            "--tenant-id",
            "tenant-a",
            "--project-id",
            "project-a",
            "--cluster-id",
            "mk8scluster-a",
            "--region-id",
            "eu-north1",
            "--kube-context",
            "ctx-a",
            "--access",
            "internal",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "output_root": output_root,
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "cluster_id": "mk8scluster-a",
            "region_id": "eu-north1",
            "kube_context": "ctx-a",
            "access": "internal",
        }
    ]


def test_soperator_discover_requires_raw_scope_before_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli,
        "_run_soperator_public_discovery_command",
        lambda **kwargs: calls.append(dict(kwargs)) or {"status": "complete"},
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "discover",
            str(tmp_path / "support"),
            "--project-id",
            "project-a",
            "--cluster-id",
            "mk8s-a",
        ],
    )

    assert result.exit_code == 2
    assert "--tenant-id" in _normalized(result.output)
    assert calls == []


def test_onboarded_discovery_omits_temporary_context_from_saved_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    manifest_path = tmp_path / "manifest.json"
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli,
        "soperator_registration_target",
        lambda *_args, **_kwargs: {"cluster_id": "mk8s-a", "access": "internal"},
    )

    @contextmanager
    def _context(*_args: object, **_kwargs: object):
        yield "temporary-context"

    monkeypatch.setattr(cli, "_onboarded_soperator_cluster_context", _context)
    monkeypatch.setattr(
        cli,
        "collect_kubectl_soperator_snapshot",
        lambda **_kwargs: {"helm_releases": []},
    )
    monkeypatch.setattr(
        cli,
        "_write_soperator_discovery_bundle_from_snapshot",
        lambda **kwargs: captured.append(dict(kwargs)) or manifest_path,
    )

    result = cli._run_onboarded_soperator_discovery_command(
        config_path=config_path,
        payload={},
        target_ref="cluster-a",
        kube_context=None,
        output_dir=None,
        namespace=None,
        release_name=None,
        redaction="support",
    )

    assert result == manifest_path
    assert captured == [
        {
            "config_path": config_path,
            "target_ref": "cluster-a",
            "snapshot": {"helm_releases": []},
            "source_kind": "onboarded",
            "output_dir": None,
            "namespace": None,
            "release_name": None,
            "kube_context": "temporary-context",
            "durable_kube_context": None,
            "cluster_id": "mk8s-a",
            "cluster_name": "",
            "redaction": "support",
        }
    ]


def test_discovery_bundle_does_not_persist_temporary_collection_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: v1\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_collect_soperator_discovery_helm_values", lambda **_kwargs: {})
    monkeypatch.setattr(
        cli,
        "_collect_soperator_discovery_slurm_snapshot",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        cli,
        "_collect_soperator_discovery_accounting_snapshot",
        lambda **_kwargs: {},
    )

    manifest_path = cli._write_soperator_discovery_bundle_from_snapshot(
        config_path=config_path,
        target_ref="cluster-a",
        snapshot={"helm_releases": []},
        source_kind="onboarded",
        output_dir=tmp_path / "support",
        namespace="soperator",
        release_name="soperator",
        kube_context="temporary-context",
        durable_kube_context=None,
        cluster_id="mk8s-a",
        redaction="support",
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = json.loads((manifest_path.parent / "identity.json").read_text(encoding="utf-8"))
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(manifest_path.parent.iterdir())
        if path.is_file()
    )
    assert manifest["command"] == [
        "nebius-cxcli",
        "soperator",
        "discover",
        str(config_path),
        "--target",
        "cluster-a",
        "--output-dir",
        str(tmp_path / "support"),
        "--namespace",
        "soperator",
        "--release-name",
        "soperator",
        "--redaction",
        "support",
        "--no-interactive",
    ]
    assert identity["kube_context"] == ""
    assert "temporary-context" not in serialized


def test_discovery_uses_helm_storage_and_slurm_workload_namespaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: v1\n", encoding="utf-8")
    calls: dict[str, str] = {}
    snapshot = {
        "helm_releases": [
            {
                "name": "soperator-controller",
                "namespace": "soperator-system",
                "storage_namespace": "flux-system",
                "chart": "helm-soperator-1.22.3",
            }
        ],
        "soperator_resources": [
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "soperator", "namespace": "soperator"},
            }
        ],
    }

    monkeypatch.setattr(
        cli,
        "_collect_soperator_discovery_helm_values",
        lambda **kwargs: calls.__setitem__("helm", kwargs["namespace"]) or {},
    )
    monkeypatch.setattr(
        cli,
        "_collect_soperator_discovery_slurm_snapshot",
        lambda **kwargs: calls.__setitem__("slurm", kwargs["namespace"]) or {},
    )
    monkeypatch.setattr(
        cli,
        "_collect_soperator_discovery_accounting_snapshot",
        lambda **kwargs: calls.__setitem__("accounting", kwargs["namespace"]) or {},
    )

    cli._write_soperator_discovery_bundle_from_snapshot(
        config_path=config_path,
        target_ref="cluster-a",
        snapshot=snapshot,
        source_kind="onboarded",
        output_dir=tmp_path / "support",
        namespace=None,
        release_name=None,
        kube_context="temporary-context",
        durable_kube_context=None,
        cluster_id="mk8s-a",
        redaction="support",
    )

    assert calls == {
        "helm": "flux-system",
        "slurm": "soperator",
        "accounting": "soperator",
    }


def test_discovery_bundle_uses_one_durable_context_for_command_and_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: v1\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_collect_soperator_discovery_helm_values", lambda **_kwargs: {})
    monkeypatch.setattr(
        cli,
        "_collect_soperator_discovery_slurm_snapshot",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        cli,
        "_collect_soperator_discovery_accounting_snapshot",
        lambda **_kwargs: {},
    )

    manifest_path = cli._write_soperator_discovery_bundle_from_snapshot(
        config_path=config_path,
        target_ref="cluster-a",
        snapshot={"helm_releases": []},
        source_kind="onboarded",
        output_dir=tmp_path / "support",
        namespace="soperator",
        release_name="soperator",
        kube_context="durable-context",
        durable_kube_context="durable-context",
        cluster_id="mk8s-a",
        redaction="support",
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = json.loads((manifest_path.parent / "identity.json").read_text(encoding="utf-8"))
    assert manifest["command"] == [
        "nebius-cxcli",
        "soperator",
        "discover",
        str(config_path),
        "--target",
        "cluster-a",
        "--output-dir",
        str(tmp_path / "support"),
        "--namespace",
        "soperator",
        "--release-name",
        "soperator",
        "--kube-context",
        "durable-context",
        "--redaction",
        "support",
        "--no-interactive",
    ]
    assert identity["kube_context"] == "durable-context"


@pytest.mark.parametrize("status", ("partial", "not-detected"))
def test_soperator_discover_writes_incomplete_report_then_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli,
        "_run_soperator_public_discovery_command",
        lambda **kwargs: calls.append(dict(kwargs)) or {"status": status},
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "discover",
            str(tmp_path / "support"),
            "--cluster-id",
            "mk8scluster-a",
            "--tenant-id",
            "tenant-a",
            "--project-id",
            "project-a",
        ],
    )

    assert result.exit_code == 1
    assert calls == [
        {
            "output_root": tmp_path / "support",
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "cluster_id": "mk8scluster-a",
            "region_id": None,
            "kube_context": None,
            "access": "external",
        }
    ]


@pytest.mark.parametrize(
    ("detected", "collection_errors", "expected_status"),
    (
        (True, [], "complete"),
        (
            True,
            [
                {
                    "collector": "slurm-health",
                    "message": "probe unavailable",
                    "severity": "warning",
                }
            ],
            "partial",
        ),
        (False, [], "not-detected"),
    ),
)
def test_public_discovery_prints_saved_markdown_after_identity_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    detected: bool,
    collection_errors: list[dict[str, str]],
    expected_status: str,
) -> None:
    runtime = cli._SOPERATOR_PUBLIC_DISCOVERY_RUNTIME
    cluster = SimpleNamespace(
        metadata=SimpleNamespace(name="training-a", labels={}),
        spec=SimpleNamespace(),
        status=SimpleNamespace(),
    )
    snapshot = {
        "helm_releases": (
            [
                {
                    "name": "soperator",
                    "namespace": "soperator",
                    "chart": "helm-soperator-1.22.3",
                }
            ]
            if detected
            else []
        ),
        "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
        "kubernetes_nodes": [
            {
                "metadata": {
                    "name": "worker-a",
                    "labels": {
                        "topology.kubernetes.io/region": "eu-west1",
                        "nebius.com/node-group-id": "group-a",
                    },
                },
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
        ],
        "slurm_health": {"checked": detected, "healthy": detected},
        "collection_errors": collection_errors,
        "collection_lanes": [
            {
                "name": "kubernetes-crds",
                "status": "succeeded",
                "item_count": int(detected),
            },
            {
                "name": "soperator-resources",
                "status": "succeeded" if detected else "not-applicable",
                "item_count": int(detected),
            },
            {
                "name": "soperator-helm",
                "status": "succeeded",
                "item_count": int(detected),
            },
        ],
    }
    monkeypatch.setattr(
        runtime,
        "provider_observation",
        lambda **_kwargs: (
            cluster,
            {
                "control_plane_version": "1.33",
                "node_groups": [
                    {
                        "id": "group-a",
                        "name": "worker-a",
                        "actual_node_count": 1,
                        "target_node_count": 1,
                    }
                ],
            },
            [],
        ),
    )

    @contextmanager
    def _provider_context(**_kwargs: object):
        yield "provider-context", {"KUBECONFIG": "/temporary/config"}, "https://mk8s"

    monkeypatch.setattr(runtime, "provider_context", _provider_context)
    collection_calls: list[dict[str, object]] = []

    def _collect_snapshot(**kwargs: object) -> dict[str, object]:
        collection_calls.append(dict(kwargs))
        return snapshot

    monkeypatch.setattr(
        runtime,
        "_collect_snapshot",
        _collect_snapshot,
    )
    writes: list[dict[str, object]] = []
    rendered_markdown = (
        "# Soperator Discovery Report\n\n"
        f"- Status: `{expected_status}`\n\n"
        "## Boundaries\n\n"
        "- This report is information-only.\n"
    )
    monkeypatch.setattr(
        runtime,
        "_write_report",
        lambda output_root, **kwargs: (
            writes.append({"output_root": output_root, **kwargs})
            or (tmp_path / "report.json", tmp_path / "report.md", rendered_markdown)
        ),
    )
    console_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _RecordingConsole:
        def print(self, *objects: object, **kwargs: object) -> None:
            console_calls.append((objects, kwargs))

    monkeypatch.setattr(runtime, "_console", _RecordingConsole())

    report = cli._run_soperator_public_discovery_command(
        output_root=tmp_path / "support",
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        region_id=None,
        kube_context=None,
        access="external",
    )

    assert report["status"] == expected_status
    assert report["identity"]["region_id"] == "eu-west1"
    assert collection_calls == [
        {
            "kube_context": "provider-context",
            "extra_env": {"KUBECONFIG": "/temporary/config"},
            "require_complete_identity": False,
        }
    ]
    assert len(writes) == 1
    assert len(console_calls) == 4
    terminal_report = console_calls[0][0][0]
    assert isinstance(terminal_report, Markdown)
    assert terminal_report.markup == rendered_markdown
    assert terminal_report.hyperlinks is False
    assert console_calls[1][0] == (f"Soperator discovery status: {expected_status}",)
    assert console_calls[2][0] == (f"Soperator discovery JSON: {tmp_path / 'report.json'}",)
    assert console_calls[3][0] == (f"Soperator discovery Markdown: {tmp_path / 'report.md'}",)


def test_public_discovery_region_mismatch_writes_no_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = cli._SOPERATOR_PUBLIC_DISCOVERY_RUNTIME
    cluster = SimpleNamespace(
        metadata=SimpleNamespace(name="training-a", labels={}),
        spec=SimpleNamespace(),
        status=SimpleNamespace(),
    )
    snapshot = {
        "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
        "kubernetes_nodes": [
            {
                "metadata": {
                    "name": "worker-a",
                    "labels": {"topology.kubernetes.io/region": "eu-west1"},
                },
                "status": {},
            }
        ],
        "collection_errors": [],
    }
    monkeypatch.setattr(
        runtime,
        "provider_observation",
        lambda **_kwargs: (
            cluster,
            {"control_plane_version": "1.33", "node_groups": []},
            [],
        ),
    )

    @contextmanager
    def _provider_context(**_kwargs: object):
        yield "provider-context", {"KUBECONFIG": "/temporary/config"}, "https://mk8s"

    monkeypatch.setattr(runtime, "provider_context", _provider_context)
    monkeypatch.setattr(
        runtime,
        "_collect_snapshot",
        lambda **_kwargs: snapshot,
    )
    writes: list[str] = []
    monkeypatch.setattr(
        runtime,
        "_write_report",
        lambda *_args, **_kwargs: writes.append("written"),
    )

    with pytest.raises(RuntimeError, match="does not match live cluster region"):
        cli._run_soperator_public_discovery_command(
            output_root=tmp_path / "support",
            tenant_id="tenant-a",
            project_id="project-a",
            cluster_id="mk8scluster-a",
            region_id="eu-north1",
            kube_context=None,
            access="external",
        )

    assert writes == []


def test_soperator_status_forwards_live_context_and_noninteractive_target_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    paths.config_path.write_text("version: v1\n", encoding="utf-8")
    target = SimpleNamespace(target_ref="cluster-a")
    resolver_calls: list[dict[str, object]] = []
    snapshot_calls: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: {"apps": {}})

    def _resolve(*_args: object, **kwargs: object):
        resolver_calls.append(dict(kwargs))
        return (
            target,
            {
                "kube_context": "stored-ctx",
                "inventory": {"cluster_identity": {"kubernetes_uid": "cluster-uid-a"}},
            },
            True,
        )

    monkeypatch.setattr(cli, "_resolve_soperator_command_target", _resolve)
    monkeypatch.setattr(cli, "_source_helm_chart_row", lambda *_args: {"version": "4.1.7"})
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _path: paths)
    monkeypatch.setattr(cli, "read_soperator_operation_status", lambda **_kwargs: None)

    def _snapshot(**kwargs: object) -> dict[str, object]:
        snapshot_calls.append(dict(kwargs))
        return {
            "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
            "helm_releases": [
                {
                    "name": "soperator",
                    "status": "deployed",
                    "chart_version": "4.1.7",
                    "app_version": "4.1.7",
                }
            ],
        }

    monkeypatch.setattr(cli, "collect_kubectl_soperator_snapshot", _snapshot)

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "status",
            str(paths.config_path),
            "--target",
            "cluster-a",
            "--kube-context",
            "ctx-a",
            "--live",
            "--no-interactive",
        ],
    )

    assert result.exit_code == 0, result.output
    assert resolver_calls == [{"target_ref": "cluster-a", "interactive": False}]
    assert snapshot_calls == [{"kube_context": "ctx-a"}]
    assert "Live Soperator status: deployed" in _normalized(result.output)


def _status_observability_workload_inventory() -> dict[str, object]:
    return {
        "items": [
            {
                "kind": "Deployment",
                "metadata": {
                    "namespace": "soperator-system",
                    "name": "soperator-controller-manager",
                    "uid": "deployment-uid",
                    "labels": {"app.kubernetes.io/version": "4.1.7"},
                },
                "spec": {"replicas": 1},
                "status": {"readyReplicas": 1},
            },
            {
                "kind": "ReplicaSet",
                "metadata": {
                    "namespace": "soperator-system",
                    "name": "soperator-controller-manager-abc",
                    "uid": "replicaset-uid",
                    "ownerReferences": [
                        {"kind": "Deployment", "uid": "deployment-uid", "controller": True}
                    ],
                },
            },
            {
                "kind": "Pod",
                "metadata": {
                    "namespace": "soperator-system",
                    "name": "soperator-controller-manager-abc-123",
                    "uid": "pod-uid",
                    "ownerReferences": [
                        {"kind": "ReplicaSet", "uid": "replicaset-uid", "controller": True}
                    ],
                },
                "spec": {
                    "containers": [{"name": "manager", "image": "ghcr.io/nebius/soperator:v4.1.7"}]
                },
                "status": {
                    "startTime": "2026-08-30T12:00:00Z",
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "containerStatuses": [{"name": "manager", "ready": True}],
                },
            },
        ]
    }


def _passed_observability_receipt(verification_id: str) -> SoperatorObservabilityReceipt:
    return SoperatorObservabilityReceipt(
        schema="nebius-cxcli.soperator-observability-verification.v1",
        verification_id=verification_id,
        status="passed",
        failure_code="",
        http_status=None,
        target_ref_sha256="sha256:" + "1" * 64,
        release="4.1.7",
        credential_source="operator-nebius-cli",
        metrics_endpoint_sha256="sha256:" + "2" * 64,
        logs_endpoint_sha256="sha256:" + "3" * 64,
        metric_query_sha256="sha256:" + "4" * 64,
        product_log_query_sha256="sha256:" + "5" * 64,
        workload_identity_sha256="sha256:" + "6" * 64,
        verification_started_at="2026-08-30T12:05:00Z",
        metric_not_before="2026-08-30T12:00:00Z",
        log_not_before="2026-08-30T12:00:00Z",
        observed_at="2026-08-30T12:05:01Z",
        attempts=1,
        metric_series_count=1,
        metric_newest_sample_at="2026-08-30T12:05:00Z",
        product_log_stream_count=1,
        product_log_newest_sample_at="2026-08-30T12:00:01Z",
    )


def test_soperator_status_verify_observability_is_live_by_default_and_writes_separate_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    paths.config_path.write_text("version: v1\n", encoding="utf-8")
    operation_receipt = paths.reports_dir / "soperator-release-reconcile-existing.json"
    operation_receipt.parent.mkdir(parents=True, exist_ok=True)
    operation_receipt.write_text('{"status":"complete"}\n', encoding="utf-8")
    operation_before = operation_receipt.read_bytes()
    target = SimpleNamespace(target_ref="cluster-a")
    payload = {"client_info": {"nebius": {"project_id": "project-a"}}, "apps": {}}
    target_row = {
        "cluster_id": "mk8scluster-a",
        "project_id": "project-a",
        "kube_context": "ctx-a",
        "inventory": {"cluster_identity": {"kubernetes_uid": "cluster-uid-a"}},
    }
    snapshot = {
        "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
        "helm_releases": [
            {
                "name": "soperator",
                "status": "deployed",
                "chart_version": "4.1.7+dfddf7cde96b",
                "app_version": "4.1.7",
            }
        ],
        "soperator_resources": [{"kind": "SlurmCluster", "metadata": {"name": "soperator"}}],
        "collection_errors": [],
    }
    auth_calls: list[dict[str, object]] = []
    verify_calls: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: payload)
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_command_target",
        lambda *_args, **_kwargs: (target, target_row, True),
    )
    monkeypatch.setattr(cli, "_source_helm_chart_row", lambda *_args: {"version": "4.1.7"})
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _path: paths)
    monkeypatch.setattr(cli, "read_soperator_operation_status", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "collect_kubectl_soperator_snapshot", lambda **_kwargs: snapshot)
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_process",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps(_status_observability_workload_inventory())
        ),
    )
    monkeypatch.setattr(
        cli,
        "acquire_operator_access_token",
        lambda **kwargs: auth_calls.append(dict(kwargs)) or "operator-token",
    )

    def verify(scope: object, **kwargs: object) -> SoperatorObservabilityReceipt:
        verify_calls.append({"scope": scope, **kwargs})
        assert isinstance(scope, SoperatorObservabilityScope)
        assert scope.release == "4.1.7"
        assert kwargs["token"] == "operator-token"
        return _passed_observability_receipt(str(kwargs["verification_id"]))

    monkeypatch.setattr(cli, "verify_soperator_observability", verify)

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "status",
            str(paths.config_path),
            "--target",
            "cluster-a",
            "--verify-observability",
            "--no-interactive",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Live Soperator status: deployed" in _normalized(result.output)
    assert "Observability verification: passed" in _normalized(result.output)
    assert len(auth_calls) == 1
    assert auth_calls[0]["interactive"] is False
    assert len(verify_calls) == 1
    assert operation_receipt.read_bytes() == operation_before
    receipts = list(paths.reports_dir.glob("soperator-observability-*.json"))
    assert len(receipts) == 1
    assert receipts[0].stat().st_mode & 0o077 == 0
    persisted = receipts[0].read_text(encoding="utf-8")
    assert "operator-token" not in persisted
    assert "project-a" not in persisted
    assert "mk8scluster-a" not in persisted


def test_soperator_status_verify_observability_rejects_no_live_before_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: v1\n", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "_load_source_payload",
        lambda _path: pytest.fail("invalid flag combination must fail before config or auth"),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "status",
            str(config_path),
            "--verify-observability",
            "--no-live",
        ],
    )

    assert result.exit_code == 2
    normalized = _normalized_cli_help(result.output)
    assert "requires" in normalized
    assert "--no-live" in normalized


def test_soperator_status_observability_auth_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    paths.config_path.write_text("version: v1\n", encoding="utf-8")
    target = SimpleNamespace(target_ref="cluster-a")
    target_row = {
        "cluster_id": "mk8scluster-a",
        "project_id": "project-a",
        "kube_context": "ctx-a",
        "inventory": {"cluster_identity": {"kubernetes_uid": "cluster-uid-a"}},
    }
    snapshot = {
        "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
        "helm_releases": [{"name": "soperator", "status": "deployed", "chart_version": "4.1.7"}],
        "soperator_resources": [{"kind": "SlurmCluster", "metadata": {"name": "soperator"}}],
        "collection_errors": [],
    }
    monkeypatch.setattr(
        cli,
        "_load_source_payload",
        lambda _path: {
            "client_info": {"nebius": {"project_id": "project-a"}},
            "apps": {},
        },
    )
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_command_target",
        lambda *_args, **_kwargs: (target, target_row, True),
    )
    monkeypatch.setattr(cli, "_source_helm_chart_row", lambda *_args: {"version": "4.1.7"})
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _path: paths)
    monkeypatch.setattr(cli, "read_soperator_operation_status", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "collect_kubectl_soperator_snapshot", lambda **_kwargs: snapshot)
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_process",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps(_status_observability_workload_inventory())
        ),
    )
    monkeypatch.setattr(
        cli,
        "acquire_operator_access_token",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("secret provider detail")),
    )
    monkeypatch.setattr(
        cli,
        "verify_soperator_observability",
        lambda *_args, **_kwargs: pytest.fail("query must not run without operator auth"),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "status",
            str(paths.config_path),
            "--target",
            "cluster-a",
            "--verify-observability",
            "--no-interactive",
        ],
    )

    assert result.exit_code == 1
    assert "authentication-unavailable" in _normalized(result.output)
    assert "secret provider detail" not in result.output
    receipt = next(paths.reports_dir.glob("soperator-observability-*.json"))
    persisted = json.loads(receipt.read_text(encoding="utf-8"))
    assert persisted["status"] == "unavailable"
    assert persisted["failure_code"] == "authentication-unavailable"
    assert "secret provider detail" not in str(persisted)


def test_registered_context_identity_rejects_different_cluster_uid() -> None:
    with pytest.raises(RuntimeError, match="different Kubernetes cluster"):
        cli._validate_registered_soperator_kube_context_identity(
            target={"inventory": {"cluster_identity": {"kubernetes_uid": "registered-uid"}}},
            target_ref="cluster-a",
            kube_context="wrong-context",
            explicit_snapshot={"cluster_identity": {"kubernetes_uid": "different-uid"}},
        )


def test_managed_context_identity_compares_provider_generated_uid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (object(), paths, {"deploy": {"targets": []}}),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_managed_mk8s_upgrade_target",
        lambda *_args, **_kwargs: {"target_ref": "cluster-a"},
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *_args, **_kwargs: {
            "KUBECONFIG": "/private/provider-config",
            cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "provider-context",
        },
    )
    monkeypatch.setattr(
        cli,
        "_read_kube_system_namespace_uid",
        lambda **_kwargs: "provider-uid",
    )

    with pytest.raises(RuntimeError, match="different Kubernetes cluster"):
        cli._validate_managed_soperator_kube_context_identity(
            config_path=paths.config_path,
            target_ref="cluster-a",
            kube_context="wrong-context",
            explicit_snapshot={"cluster_identity": {"kubernetes_uid": "different-uid"}},
        )


def test_soperator_status_uses_scoped_context_for_onboarded_cluster_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    paths.config_path.write_text("version: v1\n", encoding="utf-8")
    target = SimpleNamespace(target_ref="cluster-a")
    payload = {"client_info": {"nebius": {"project_id": "project-a"}}, "apps": {}}
    context_calls: list[dict[str, object]] = []
    snapshot_calls: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: payload)
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_command_target",
        lambda *_args, **_kwargs: (
            target,
            {"cluster_id": "mk8scluster-a", "access": "internal"},
            True,
        ),
    )
    monkeypatch.setattr(cli, "_source_helm_chart_row", lambda *_args: {"version": "4.1.7"})
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _path: paths)
    monkeypatch.setattr(cli, "read_soperator_operation_status", lambda **_kwargs: None)

    @contextmanager
    def _context(source_payload: object, **kwargs: object):
        context_calls.append({"payload": source_payload, **kwargs})
        yield "temporary-ctx"

    monkeypatch.setattr(cli, "_onboarded_soperator_cluster_context", _context)

    def _snapshot(**kwargs: object) -> dict[str, object]:
        snapshot_calls.append(dict(kwargs))
        return {
            "helm_releases": [{"name": "soperator", "status": "deployed", "chart_version": "4.1.7"}]
        }

    monkeypatch.setattr(cli, "collect_kubectl_soperator_snapshot", _snapshot)

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "status",
            str(paths.config_path),
            "--target",
            "cluster-a",
            "--live",
            "--no-interactive",
        ],
    )

    assert result.exit_code == 0, result.output
    assert context_calls == [
        {
            "payload": payload,
            "cluster_id": "mk8scluster-a",
            "access": "internal",
        }
    ]
    assert snapshot_calls == [{"kube_context": "temporary-ctx"}]


def test_soperator_status_uses_nonpersistent_managed_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    paths.config_path.write_text("version: v1\n", encoding="utf-8")
    target = SimpleNamespace(target_ref="cluster-a")
    generated_config = SimpleNamespace()
    manifest: dict[str, object] = {"deploy": {"targets": []}}
    selected_target = {"target_ref": "cluster-a"}
    kube_env = {
        "KUBECONFIG": "/private/scoped-kubeconfig",
        cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "managed-ctx",
    }
    handoff_calls: list[dict[str, object]] = []
    snapshot_calls: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: {"apps": {}})
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_command_target",
        lambda *_args, **_kwargs: (target, {}, False),
    )
    monkeypatch.setattr(cli, "_source_helm_chart_row", lambda *_args: {"version": "4.1.7"})
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _path: paths)
    monkeypatch.setattr(cli, "read_soperator_operation_status", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (generated_config, paths, manifest),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_selected_deploy_targets",
        lambda *_args, **_kwargs: [selected_target],
    )

    def _handoff(*args: object, **kwargs: object) -> dict[str, str]:
        handoff_calls.append({"args": args, **kwargs})
        return kube_env

    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", _handoff)

    def _snapshot(**kwargs: object) -> dict[str, object]:
        snapshot_calls.append(dict(kwargs))
        return {
            "helm_releases": [{"name": "soperator", "status": "deployed", "chart_version": "4.1.7"}]
        }

    monkeypatch.setattr(cli, "collect_kubectl_soperator_snapshot", _snapshot)

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "status",
            str(paths.config_path),
            "--target",
            "cluster-a",
            "--live",
            "--no-interactive",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(handoff_calls) == 1
    assert handoff_calls[0]["target"] == selected_target
    assert handoff_calls[0]["persist_local_kubeconfig"] is False
    assert handoff_calls[0]["set_current_context"] is False
    assert handoff_calls[0]["allow_terraform_output"] is False
    assert snapshot_calls == [{"kube_context": "managed-ctx", "extra_env": kube_env}]


def test_soperator_destroy_requires_target_before_project_discovery(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        ["soperator", "destroy", str(tmp_path / "config.yaml"), "--dry-run"],
    )

    assert result.exit_code == 2
    assert "--target" in _normalized(result.output)


def test_soperator_destroy_inventory_includes_cluster_wide_workload_identities() -> None:
    destroy, preserve = cli._soperator_destroy_inventory(
        target_ref="cluster-a",
        cluster_id="mk8scluster-a",
        ownership="managed",
        snapshot={
            "namespaces": ["default", "soperator"],
            "node_groups": {"workers-a": {}},
            "cluster_namespace_resources": [
                {
                    "kind": "Deployment",
                    "metadata": {"namespace": "default", "name": "customer-api"},
                },
                {
                    "kind": "StatefulSet",
                    "metadata": {"namespace": "soperator", "name": "slurm-controller"},
                },
            ],
        },
        infrastructure=sample_infrastructure_receipt(),
    )

    assert "kubernetes:default/Deployment/customer-api" in destroy
    assert "kubernetes:soperator/StatefulSet/slurm-controller" in destroy
    assert "namespace:default" in destroy
    assert "mk8s-node-group:workers-a" in destroy
    assert "sfs:filesystem-jail" in preserve


def test_soperator_destroy_rejects_new_unapproved_csi_storage_bindings() -> None:
    snapshot = {
        "pvcs": [
            {
                "metadata": {"namespace": "soperator", "name": "jail-rootfs"},
                "spec": {"volumeName": "pv-jail"},
            }
        ],
        "pvs": [
            {
                "metadata": {"name": "pv-jail"},
                "spec": {"csi": {"volumeHandle": "filesystem-jail"}},
            }
        ],
    }
    infrastructure = sample_infrastructure_receipt()

    cli._assert_soperator_destroy_storage_bindings(
        snapshot=snapshot,
        infrastructure=infrastructure,
    )
    snapshot["pvcs"].append(
        {
            "metadata": {"namespace": "soperator", "name": "new-data"},
            "spec": {"volumeName": "pv-new-data"},
        }
    )
    snapshot["pvs"].append(
        {
            "metadata": {"name": "pv-new-data"},
            "spec": {"csi": {"volumeHandle": "filesystem-new-data"}},
        }
    )

    with pytest.raises(RuntimeError, match="bindings differ"):
        cli._assert_soperator_destroy_storage_bindings(
            snapshot=snapshot,
            infrastructure=infrastructure,
        )


def test_soperator_destroy_resumes_after_config_write_before_receipt_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.reports_dir.mkdir(parents=True)
    config_bytes = b"version: v1\n"
    paths.config_path.write_bytes(config_bytes)
    infrastructure = sample_infrastructure_receipt()
    receipt = build_soperator_destroy_receipt(
        target_ref="cluster-a",
        ownership="managed",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        kubernetes_uid="uid-a",
        destroy_inventory=("mk8s:mk8scluster-a",),
        preserve_inventory=("sfs:filesystem-jail",),
        protected_storage_sha256=infrastructure.receipt_sha256,
        infrastructure_receipt=infrastructure.as_payload(),
        config_sha256="sha256:" + "a" * 64,
        post_cleanup_config_sha256=("sha256:" + hashlib.sha256(config_bytes).hexdigest()),
    )
    receipt = replace(
        receipt,
        checkpoints=(
            "approved",
            "storage_verified_before_cleanup",
            "cleanup_complete",
            "delete_requested",
            "cluster_absent",
            "storage_verified_after_delete",
        ),
        delete_operation_id="operation-a",
        status="running",
    )
    receipt_path = paths.reports_dir / "soperator-destroy-cluster-a.json"
    write_soperator_destroy_receipt(receipt_path, receipt)
    payload = {
        "client_info": {"nebius": {"project_id": "project-a"}},
        "apps": {"charts": []},
        "deploy": {"targets": []},
    }
    render_calls: list[Path] = []
    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: copy.deepcopy(payload))
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _path: paths)
    monkeypatch.setattr(cli, "validate_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_run_internal_render_command",
        lambda path, **_kwargs: render_calls.append(path),
    )

    result = runner.invoke(
        cli.app,
        ["soperator", "destroy", str(paths.config_path), "--target", "cluster-a"],
    )

    assert result.exit_code == 0, result.output
    assert render_calls == [paths.config_path]
    assert load_soperator_destroy_receipt(receipt_path).status == "complete"


def test_soperator_destroy_blocks_an_active_non_destroy_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.reports_dir.mkdir(parents=True)
    paths.config_path.write_text("version: v1\n", encoding="utf-8")
    payload = {
        "client_info": {"nebius": {"project_id": "project-a"}},
        "apps": {"charts": []},
        "deploy": {"targets": []},
    }
    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: copy.deepcopy(payload))
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _path: paths)
    monkeypatch.setattr(
        cli,
        "read_soperator_operation_status",
        lambda **_kwargs: SoperatorOperationStatus(
            operation="upgrade",
            status="safety-paused",
            phase="wait-flux-graph",
            receipt_path=paths.reports_dir / "upgrade.json",
            resume_command="resume-upgrade",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_command_target",
        lambda *_args, **_kwargs: pytest.fail("target discovery must remain blocked"),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "destroy",
            str(paths.config_path),
            "--target",
            "cluster-a",
            "--dry-run",
        ],
    )

    assert result.exit_code == 1
    assert "blocked by an active foreign operation" in _normalized(result.output)


def test_soperator_destroy_acquires_local_and_cluster_writer_fences() -> None:
    source = inspect.getsource(cli.soperator_destroy_command)

    assert "SoperatorOperationLocalLock(" in source
    assert "SoperatorOperationLease(" in source
    assert source.index("SoperatorOperationLease(") < source.rindex("run_soperator_destroy(")


def test_soperator_destroy_config_cleanup_refuses_post_approval_edits(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: changed\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        cli._commit_soperator_destroy_config_cleanup(
            config_path=config_path,
            source_payload={},
            target_ref="cluster-a",
            ownership="managed",
            expected_config_sha256="sha256:" + "a" * 64,
        )


def test_soperator_destroy_config_cleanup_staging_failure_leaves_preimage_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    expected_bytes = b"# preserve this operator comment\nversion: original\n"
    config_path.write_bytes(expected_bytes)
    monkeypatch.setattr(
        cli,
        "_soperator_destroy_cleanup_payload",
        lambda **_kwargs: {"version": "cleaned"},
    )
    monkeypatch.setattr(
        cli,
        "_render_soperator_upgrade_admission",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("render failed")),
    )

    with pytest.raises(RuntimeError, match="render failed"):
        cli._commit_soperator_destroy_config_cleanup(
            config_path=config_path,
            source_payload={"version": "original"},
            target_ref="cluster-a",
            ownership="managed",
            expected_config_sha256=("sha256:" + hashlib.sha256(expected_bytes).hexdigest()),
            paths=SimpleNamespace(project_dir=tmp_path),
        )

    assert config_path.read_bytes() == expected_bytes


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr"),
    (
        (1, "", "SENTINEL_RAW_OUTPUT"),
        (0, "SENTINEL_RAW_OUTPUT", ""),
    ),
)
def test_soperator_discovery_helm_values_never_persist_raw_output(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    stderr: str,
) -> None:
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_process",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        ),
    )

    result = cli._collect_soperator_discovery_helm_values(
        namespace="soperator",
        release_name="soperator",
        kube_context="ctx-a",
    )

    assert result["status"] == "not_collected"
    assert "SENTINEL_RAW_OUTPUT" not in json.dumps(result, sort_keys=True)


@pytest.mark.parametrize("collector", ("slurm", "accounting"))
def test_soperator_discovery_command_collectors_never_persist_raw_output(
    monkeypatch: pytest.MonkeyPatch,
    collector: str,
) -> None:
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_login_command",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="SENTINEL_CUSTOMER_STDOUT",
            stderr="SENTINEL_CUSTOMER_STDERR",
        ),
    )

    collect = getattr(cli, f"_collect_soperator_discovery_{collector}_snapshot")
    result = collect(namespace="soperator", kube_context="ctx-a")
    encoded = json.dumps(result, sort_keys=True)

    assert "SENTINEL_CUSTOMER" not in encoded
    assert all(entry["status"] == "not_collected" for entry in result["commands"].values())


def test_soperator_status_fails_on_live_collection_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    paths.config_path.write_text("version: v1\n", encoding="utf-8")
    target = SimpleNamespace(target_ref="cluster-a")
    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: {"apps": {}})
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_command_target",
        lambda *_args, **_kwargs: (
            target,
            {
                "kube_context": "stored-ctx",
                "inventory": {"cluster_identity": {"kubernetes_uid": "cluster-uid-a"}},
            },
            True,
        ),
    )
    monkeypatch.setattr(cli, "_source_helm_chart_row", lambda *_args: {"version": "4.1.7"})
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _path: paths)
    monkeypatch.setattr(cli, "read_soperator_operation_status", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "collect_kubectl_soperator_snapshot",
        lambda **_kwargs: {
            "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
            "collection_errors": ["helm list failed"],
        },
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "status",
            str(paths.config_path),
            "--target",
            "cluster-a",
            "--kube-context",
            "ctx-a",
            "--live",
            "--no-interactive",
        ],
    )

    assert result.exit_code == 1
    assert "complete live Kubernetes inventory" in _normalized(result.output)


def test_soperator_status_projects_local_recovery_without_live_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.config_path.write_text("version: v1\n", encoding="utf-8")
    target = SimpleNamespace(target_ref="cluster-a")
    monkeypatch.setattr(cli, "_load_source_payload", lambda _path: {"apps": {}})
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_command_target",
        lambda *_args, **_kwargs: (target, {}, False),
    )
    monkeypatch.setattr(cli, "_source_helm_chart_row", lambda *_args: {"version": "4.1.7"})
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _path: paths)
    monkeypatch.setattr(
        cli,
        "read_soperator_operation_status",
        lambda **_kwargs: SoperatorOperationStatus(
            operation="upgrade",
            status="safety-paused",
            phase="wait-flux-graph",
            receipt_path=paths.reports_dir / "upgrade.json",
            resume_command=(
                "nebius-cxcli soperator upgrade config.yaml --target cluster-a "
                "--to-release 4.1.7 --execute --approve"
            ),
            classification="safety-paused",
            detail="Mutation is safety-paused.",
        ),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "status",
            str(paths.config_path),
            "--target",
            "cluster-a",
            "--no-live",
        ],
    )

    output = _normalized(result.output)
    assert result.exit_code == 0, result.output
    assert "Operation: upgrade" in output
    assert "Operation status: safety-paused" in output
    assert "Operation phase: wait-flux-graph" in output
    assert "Operation classification: safety-paused" in output


def test_managed_soperator_destroy_applies_only_saved_selected_cluster_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        cli,
        "_generated_bundle_mk8s_module_index",
        lambda _manifest: {"cluster_a": ("mk8s", "cluster-a")},
    )
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(
        cli,
        "terraform_init",
        lambda infra_dir, **_kwargs: calls.append(("init", infra_dir)),
    )

    def _plan(infra_dir, **kwargs):
        kwargs["plan_file"].write_bytes(b"selected destroy plan")
        calls.append(("plan", kwargs))

    monkeypatch.setattr(cli, "terraform_plan", _plan)
    monkeypatch.setattr(
        cli,
        "terraform_show_json",
        lambda *_args, **_kwargs: {
            "resource_changes": [
                {
                    "address": "module.cluster_a.nebius_mk8s_v1_cluster.this",
                    "change": {
                        "actions": ["delete"],
                        "before": {"id": "mk8scluster-a"},
                    },
                }
            ]
        },
    )
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda *_args, **kwargs: calls.append(("apply", kwargs["plan_file"])),
    )

    digest = cli._apply_managed_soperator_destroy_plan(
        config={},
        paths=paths,
        manifest={},
        target_ref="cluster-a",
        expected_cluster_id="mk8scluster-a",
    )

    plan_call = next(item[1] for item in calls if item[0] == "plan")
    assert plan_call["destroy"] is True
    assert plan_call["targets"] == (
        "module.cluster_a",
        "nebius_iam_v1_group.cluster_a_soperator_observability",
        "nebius_iam_v1_group_membership.cluster_a_soperator_observability",
        "nebius_iam_v1_access_permit.cluster_a_soperator_observability_metrics",
        "nebius_iam_v1_access_permit.cluster_a_soperator_observability_logs",
    )
    assert calls[-1][0] == "apply"
    assert digest.startswith("sha256:")


def test_documented_soperator_commands_use_only_registered_options() -> None:
    contract = _soperator_cli_contract()
    documents = "\n".join(
        (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
        for path in ("README.md", "docs/requirements.md", "docs/design.md")
    )
    code_blocks = re.findall(r"```(?:bash|text)\n(.*?)```", documents, re.DOTALL)

    for command, registered_options in contract.items():
        command_text = f"soperator {command}"
        fragments = re.findall(
            rf"`([^`\n]*{re.escape(command_text)}[^`\n]*)`",
            documents,
        )
        for block in code_blocks:
            collapsed = block.replace("\\\n", " ")
            fragments.extend(
                line.strip() for line in collapsed.splitlines() if command_text in line
            )

        assert fragments, command
        for fragment in fragments:
            documented_options = set(re.findall(r"--[a-z][a-z0-9-]*", fragment))
            assert documented_options <= registered_options, fragment

    assert "--resume" not in {
        option
        for fragment in re.findall(r"`([^`\n]*soperator upgrade[^`\n]*)`", documents)
        for option in re.findall(r"--[a-z][a-z0-9-]*", fragment)
    }


def test_protected_rootfs_job_identity_binds_full_workload() -> None:
    manifest = cli.bind_protected_job_authority(
        cli.rootfs_cleanup_job_manifest(
            namespace="soperator",
            name="cxcli-rootfs-cleanup",
            image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
            pvc_name="jail-rootfs-slot-b-pvc",
        ),
        operation_id="operation-a",
        fence_epoch=1,
        pvc_uid="pvc-uid-a",
    )
    changed = json.loads(json.dumps(manifest))
    changed["spec"]["template"]["spec"]["containers"][0]["command"][-1] += "\ntrue"

    assert cli._protected_data_plane_job_identity(manifest) != (
        cli._protected_data_plane_job_identity(changed)
    )


def test_protected_rootfs_job_lookup_uses_explicit_context(monkeypatch) -> None:
    manifest = cli.bind_protected_job_authority(
        cli.rootfs_inventory_job_manifest(
            namespace="soperator",
            name="cxcli-rootfs-inventory",
            image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
            pvc_name="jail-rootfs-slot-b-pvc",
        ),
        operation_id="operation-a",
        fence_epoch=1,
        pvc_uid="pvc-uid-a",
    )
    identity = cli._protected_data_plane_job_identity(manifest)
    observed = cli.bind_protected_workload_identity(
        manifest,
        requested_workload_sha256=identity.workload_sha256,
        admitted_workload_sha256=identity.workload_sha256,
    )
    observed["metadata"]["uid"] = "job-uid"
    commands: list[list[str]] = []

    def _run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(observed), stderr="")

    monkeypatch.setattr(cli.subprocess, "run", _run)

    uid, workload_sha256 = cli._ensure_protected_data_plane_job(
        manifest,
        kube_context="customer-cluster",
        extra_env={},
    )

    assert uid == "job-uid"
    assert workload_sha256 == identity.workload_sha256
    assert commands[0][:3] == ["kubectl", "--context", "customer-cluster"]
    assert "--dry-run=server" not in commands[0]
    assert len(commands) == 1


def test_completed_protected_rootfs_job_is_never_recreated(monkeypatch) -> None:
    manifest = cli.bind_protected_job_authority(
        cli.rootfs_cleanup_job_manifest(
            namespace="soperator",
            name="cxcli-rootfs-cleanup",
            image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
            pvc_name="jail-rootfs-slot-b-pvc",
        ),
        operation_id="operation-a",
        fence_epoch=1,
        pvc_uid="pvc-uid-a",
    )
    commands: list[list[str]] = []

    def _run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="NotFound")

    monkeypatch.setattr(cli.subprocess, "run", _run)

    with pytest.raises(RuntimeError, match="completed protected rootfs Job.*is missing"):
        cli._ensure_protected_data_plane_job(
            manifest,
            kube_context="customer-cluster",
            extra_env={},
            allow_create=False,
            expected_job_uid="job-uid-a",
            expected_workload_sha256=(
                cli._protected_data_plane_job_identity(manifest).workload_sha256
            ),
        )

    assert len(commands) == 1
    assert commands[0][5:7] == ["get", "job"]


def test_completed_protected_rootfs_job_requires_checkpoint_uid(monkeypatch) -> None:
    manifest = cli.bind_protected_job_authority(
        cli.rootfs_inventory_job_manifest(
            namespace="soperator",
            name="cxcli-rootfs-inventory",
            image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
            pvc_name="jail-rootfs-slot-b-pvc",
        ),
        operation_id="operation-a",
        fence_epoch=1,
        pvc_uid="pvc-uid-a",
    )
    identity = cli._protected_data_plane_job_identity(manifest)
    observed = cli.bind_protected_workload_identity(
        manifest,
        requested_workload_sha256=identity.workload_sha256,
        admitted_workload_sha256=identity.workload_sha256,
    )
    observed["metadata"]["uid"] = "replacement-job-uid"
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(observed),
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="changed UID"):
        cli._ensure_protected_data_plane_job(
            manifest,
            kube_context="customer-cluster",
            extra_env={},
            allow_create=False,
            expected_job_uid="checkpoint-job-uid",
            expected_workload_sha256=identity.workload_sha256,
        )


def test_protected_rootfs_job_wait_fails_promptly_on_authenticated_failure(
    monkeypatch,
) -> None:
    manifest = cli.bind_protected_job_authority(
        cli.rootfs_inventory_job_manifest(
            namespace="soperator",
            name="cxcli-rootfs-inventory",
            image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
            pvc_name="jail-rootfs-slot-b-pvc",
        ),
        operation_id="operation-a",
        fence_epoch=1,
        pvc_uid="pvc-uid-a",
    )
    identity = cli._protected_data_plane_job_identity(manifest)
    observed = cli.bind_protected_workload_identity(
        manifest,
        requested_workload_sha256=identity.workload_sha256,
        admitted_workload_sha256=identity.workload_sha256,
    )
    observed["metadata"]["uid"] = "job-uid-a"
    observed["status"] = {
        "conditions": [
            {
                "type": "Failed",
                "status": "True",
                "reason": "BackoffLimitExceeded",
                "message": "Job has reached the specified backoff limit",
            }
        ]
    }
    commands: list[list[str]] = []

    def _run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(observed), stderr="")

    monkeypatch.setattr(cli.subprocess, "run", _run)
    monkeypatch.setattr(
        cli.time,
        "sleep",
        lambda _seconds: pytest.fail("terminal Failed condition must not sleep"),
    )

    with pytest.raises(RuntimeError, match="failed: BackoffLimitExceeded"):
        cli._wait_protected_data_plane_job(
            namespace="soperator",
            name="cxcli-rootfs-inventory",
            uid="job-uid-a",
            kube_context="customer-cluster",
            extra_env={},
            include_logs=True,
            expected_workload_sha256=identity.workload_sha256,
        )

    assert len(commands) == 1
    assert commands[0][5:7] == ["get", "job"]


def test_protected_rootfs_job_wait_checks_identity_before_terminal_state(
    monkeypatch,
) -> None:
    manifest = cli.bind_protected_job_authority(
        cli.rootfs_inventory_job_manifest(
            namespace="soperator",
            name="cxcli-rootfs-inventory",
            image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
            pvc_name="jail-rootfs-slot-b-pvc",
        ),
        operation_id="operation-a",
        fence_epoch=1,
        pvc_uid="pvc-uid-a",
    )
    identity = cli._protected_data_plane_job_identity(manifest)
    observed = cli.bind_protected_workload_identity(
        manifest,
        requested_workload_sha256=identity.workload_sha256,
        admitted_workload_sha256=identity.workload_sha256,
    )
    observed["metadata"]["uid"] = "replacement-job-uid"
    observed["status"] = {"conditions": [{"type": "Complete", "status": "True"}]}
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(observed),
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="UID changed"):
        cli._wait_protected_data_plane_job(
            namespace="soperator",
            name="cxcli-rootfs-inventory",
            uid="checkpoint-job-uid",
            kube_context="customer-cluster",
            extra_env={},
            include_logs=False,
            expected_workload_sha256=identity.workload_sha256,
        )


def test_protected_rootfs_job_accepts_only_generated_selector_drift(monkeypatch) -> None:
    manifest = cli.bind_protected_job_authority(
        cli.rootfs_inventory_job_manifest(
            namespace="soperator",
            name="cxcli-rootfs-inventory",
            image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
            pvc_name="jail-rootfs-slot-b-pvc",
        ),
        operation_id="operation-a",
        fence_epoch=1,
        pvc_uid="pvc-uid-a",
    )

    def _server_job(selector_uid: str, *, uid: str = "") -> dict[str, Any]:
        payload = json.loads(json.dumps(manifest))
        selector_labels = {
            "batch.kubernetes.io/controller-uid": selector_uid,
            "controller-uid": selector_uid,
        }
        payload["spec"]["selector"] = {"matchLabels": selector_labels}
        payload["spec"]["template"]["metadata"]["labels"].update(
            {
                **selector_labels,
                "batch.kubernetes.io/job-name": manifest["metadata"]["name"],
                "job-name": manifest["metadata"]["name"],
            }
        )
        payload["spec"].update(
            {
                "parallelism": 1,
                "completions": 1,
                "completionMode": "NonIndexed",
                "suspend": False,
            }
        )
        pod_spec = payload["spec"]["template"]["spec"]
        pod_spec.update(
            {
                "dnsPolicy": "ClusterFirst",
                "schedulerName": "default-scheduler",
                "terminationGracePeriodSeconds": 30,
                "enableServiceLinks": True,
                "securityContext": {},
            }
        )
        for container in pod_spec["containers"]:
            container.update(
                {
                    "resources": {},
                    "terminationMessagePath": "/dev/termination-log",
                    "terminationMessagePolicy": "File",
                }
            )
        if uid:
            payload["metadata"]["uid"] = uid
        return payload

    admitted = _server_job("dry-run-controller")
    requested_identity = cli._protected_data_plane_job_identity(manifest)
    admitted_identity = cli._protected_data_plane_job_identity(admitted)
    persisted = cli.bind_protected_workload_identity(
        _server_job("persisted-controller", uid="job-uid"),
        requested_workload_sha256=requested_identity.workload_sha256,
        admitted_workload_sha256=admitted_identity.workload_sha256,
    )
    responses = iter(
        (
            SimpleNamespace(returncode=1, stdout="", stderr="NotFound"),
            SimpleNamespace(returncode=0, stdout=json.dumps(admitted), stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout=json.dumps(persisted), stderr=""),
        )
    )
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: next(responses))

    assert cli._ensure_protected_data_plane_job(
        manifest,
        kube_context="customer-cluster",
        extra_env={},
    ) == (
        "job-uid",
        admitted_identity.workload_sha256,
    )


def test_downstream_soperator_chart_family_and_lock_are_deleted() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    for chart_name in (
        "soperator",
        "soperator-activechecks",
        "soperator-backup-config",
        "soperator-checks",
        "soperator-dcgm-exporter",
        "soperator-notifier",
    ):
        chart_dir = repository_root / "helm-charts" / chart_name
        assert not chart_dir.exists()
    assert not (
        repository_root / ".github" / "workflows" / "soperator-upstream-verifier.yml"
    ).exists()
    assert not (
        repository_root
        / "services"
        / "nebius-cxcli"
        / "src"
        / "nebius_cxcli"
        / "soperator_release_lock.yaml"
    ).exists()
    publish_catalog = json.loads(
        (repository_root / ".github" / "helm-chart-publish.json").read_text(encoding="utf-8")
    )
    assert all(
        chart.get("chartDir") != "helm-charts/soperator"
        and chart.get("tagPrefix") != "soperator-chart"
        for chart in publish_catalog["charts"]
    )


def test_generic_create_rejects_soperator_before_provider_work(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        cli,
        "_validate_deployments_root_target",
        lambda *_args, **_kwargs: pytest.fail("must fail before provider or filesystem work"),
    )

    result = runner.invoke(
        cli.app,
        ["create", str(tmp_path), "--app", "soperator", "--no-interactive"],
    )

    assert result.exit_code == 1
    assert "soperator install" in _normalized(result.output)


def test_soperator_install_uses_saved_plan_for_execution(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "tenant" / "project" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("version: v1\n", encoding="utf-8")
    paths = cli.resolve_project_paths(config_path)
    paths.infra_dir.mkdir(parents=True)
    paths.reports_dir.mkdir(parents=True)
    terraform_plan_path = paths.infra_dir / ".soperator-install.tfplan"
    terraform_plan_path.write_bytes(b"plan")
    receipt_path = paths.reports_dir / "soperator-install-plan.json"
    plan = {
        "operationId": "sha256:" + "b" * 64,
        "approvalFingerprint": "sha256:" + "a" * 64,
        "release": {"version": "4.1.7"},
        "status": "planned",
    }
    config = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "values": {},
                }
            ]
        }
    }
    manifest: dict[str, Any] = {"deploy": {"targets": []}}
    deployed: list[Path | None] = []

    def _create(**_kwargs: Any) -> None:
        cli._SOPERATOR_INSTALL_CONFIG_RESULT.set(config_path)

    monkeypatch.setattr(cli, "create_command", _create)
    monkeypatch.setattr(cli, "render_command", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: (config, paths, manifest))
    monkeypatch.setattr(cli, "_managed_soperator_install_target_ref", lambda *_args: "mk8s")
    monkeypatch.setattr(
        cli,
        "_soperator_install_operation_id",
        lambda **_kwargs: "sha256:" + "b" * 64,
    )

    class _Lease:
        def assert_held(self) -> None:
            return None

    @contextmanager
    def _lease(**_kwargs: Any):
        yield _Lease()

    monkeypatch.setattr(cli, "_soperator_install_execution_lease", _lease)
    monkeypatch.setattr(
        cli,
        "_load_soperator_install_plan",
        lambda **_kwargs: (terraform_plan_path, receipt_path, plan),
    )
    monkeypatch.setattr(
        cli,
        "_deploy_generated_artifacts",
        lambda *_args, **kwargs: (
            deployed.append(kwargs.get("terraform_plan_file")) or cli.DeployRunSummary()
        ),
    )
    monkeypatch.setattr(cli, "_print_deploy_command_footer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_write_owner_only_json", lambda *_args, **_kwargs: None)

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "install",
            str(config_path),
            "--resume",
            "--no-interactive",
            "--execute",
            "--approve",
            "--approval-fingerprint",
            plan["approvalFingerprint"],
        ],
    )

    assert result.exit_code == 0, result.output
    assert deployed == [terraform_plan_path]


def test_soperator_fresh_install_forwards_every_creation_option(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tenant-a" / "project-a" / "config.yaml"
    paths = _paths(config_path.parent)
    paths = replace(paths, config_path=config_path)
    plan_path = paths.infra_dir / ".soperator-install.tfplan"
    receipt_path = paths.reports_dir / "soperator-install-plan.json"
    config = {
        "apps": {"charts": [{"id": "soperator", "instance_id": "cluster-a", "enabled": True}]}
    }
    manifest: dict[str, object] = {"deploy": {"targets": []}}
    create_calls: list[dict[str, object]] = []

    def _create(**kwargs: object) -> None:
        create_calls.append(dict(kwargs))
        cli._SOPERATOR_INSTALL_CONFIG_RESULT.set(config_path)

    @contextmanager
    def _frozen_context(_release: object):
        yield

    class _Lease:
        def assert_held(self) -> None:
            return None

    @contextmanager
    def _lease(**_kwargs: object):
        yield _Lease()

    monkeypatch.setattr(cli, "create_command", _create)
    monkeypatch.setattr(cli, "render_command", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "freeze_soperator_release",
        lambda _selector: SimpleNamespace(snapshot=SimpleNamespace(release="4.1.7")),
    )
    monkeypatch.setattr(cli, "use_frozen_soperator_release", _frozen_context)
    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: (config, paths, manifest))
    monkeypatch.setattr(cli, "_managed_soperator_install_target_ref", lambda *_args: "cluster-a")
    monkeypatch.setattr(
        cli,
        "_soperator_install_operation_id",
        lambda **_kwargs: "sha256:" + "b" * 64,
    )
    monkeypatch.setattr(cli, "_soperator_install_execution_lease", _lease)
    monkeypatch.setattr(
        cli,
        "_plan_soperator_install",
        lambda **_kwargs: (
            plan_path,
            receipt_path,
            {
                "approvalFingerprint": "sha256:" + "a" * 64,
                "release": {"version": "4.1.7"},
            },
        ),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "install",
            str(tmp_path),
            "--client-name",
            "client-a",
            "--tenant-id",
            "tenant-a",
            "--project-id",
            "project-a",
            "--region-id",
            "eu-north1",
            "--email",
            "ops@example.invalid",
            "--profile",
            "mixed",
            "--release",
            "4.1.7",
            "--network-id",
            "network-a",
            "--network-id",
            "network-b",
            "--subnet-id",
            "subnet-a",
            "--network-ref",
            "network-ref-a",
            "--subnet-ref",
            "subnet-ref-a",
            "--force",
            "--no-interactive",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert create_calls == [
        {
            "target_path": tmp_path,
            "client_name": "client-a",
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "region_id": "eu-north1",
            "email": "ops@example.invalid",
            "infra_components_opt": ["mk8s", "sfs"],
            "apps_components_opt": ["soperator"],
            "app_namespace_opt": None,
            "app_releasename_opt": None,
            "app_version_opt": "4.1.7",
            "network_ids_opt": ["network-a", "network-b"],
            "subnet_ids_opt": ["subnet-a"],
            "network_refs_opt": ["network-ref-a"],
            "subnet_refs_opt": ["subnet-ref-a"],
            "validate_sources": True,
            "validate_config": True,
            "no_interactive": True,
            "force": True,
        }
    ]


def test_soperator_install_replan_replaces_saved_plan_only_in_resume_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tenant" / "project" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("version: v1\n", encoding="utf-8")
    paths = cli.resolve_project_paths(config_path)
    config = {
        "apps": {"charts": [{"id": "soperator", "instance_id": "cluster-a", "enabled": True}]}
    }
    manifest: dict[str, object] = {"deploy": {"targets": []}}
    events: list[str] = []
    saved_receipt = {"status": "planned", "planGeneration": "sha256:" + "d" * 64}

    class _Lease:
        def assert_held(self) -> None:
            return None

    @contextmanager
    def _lease(**_kwargs: object):
        events.append("lease")
        yield _Lease()

    monkeypatch.setattr(cli, "_load_deploy_context", lambda _path: (config, paths, manifest))
    monkeypatch.setattr(cli, "_managed_soperator_install_target_ref", lambda *_args: "cluster-a")
    monkeypatch.setattr(
        cli,
        "_soperator_install_operation_id",
        lambda **_kwargs: "sha256:" + "b" * 64,
    )
    monkeypatch.setattr(cli, "_soperator_install_execution_lease", _lease)

    def _validate(**_kwargs: object):
        events.append("validate-before-lease")
        return (
            paths.infra_dir / ".soperator-install.tfplan",
            paths.reports_dir / "soperator-install-plan.json",
            saved_receipt,
        )

    def _replan(**kwargs: object):
        events.append("replan")
        assert kwargs["expected_receipt"] == saved_receipt
        return (
            paths.infra_dir / ".soperator-install.tfplan",
            paths.reports_dir / "soperator-install-plan.json",
            {
                "approvalFingerprint": "sha256:" + "a" * 64,
                "release": {"version": "4.1.7"},
            },
        )

    monkeypatch.setattr(cli, "_validate_soperator_install_replan_receipt", _validate)
    monkeypatch.setattr(cli, "_replan_soperator_install", _replan)

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "install",
            str(config_path),
            "--resume",
            "--replan",
            "--no-interactive",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert events == ["validate-before-lease", "lease", "replan"]


def test_soperator_install_replan_rejects_non_resume_mode_before_project_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli,
        "create_command",
        lambda **_kwargs: pytest.fail("invalid --replan must fail before project work"),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "install",
            str(tmp_path),
            "--profile",
            "mixed",
            "--release",
            "4.1.7",
            "--replan",
            "--no-interactive",
            "--dry-run",
        ],
    )

    assert result.exit_code == 1
    assert "--replan is valid only with --resume --dry-run" in _normalized(result.output)
    assert cli._SOPERATOR_INSTALL_PROFILE_OVERRIDE.get() is None


def test_soperator_install_replan_accepts_only_an_exact_planned_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    authority = _install_plan_authority()
    receipt = _install_replan_receipt(authority)
    monkeypatch.setattr(
        cli,
        "_soperator_install_plan_authority",
        lambda **_kwargs: copy.deepcopy(authority),
    )
    cli._write_owner_only_json(
        paths.reports_dir / "soperator-install-plan.json",
        receipt,
    )

    _plan_path, _receipt_path, loaded = cli._validate_soperator_install_replan_receipt(
        config={},
        paths=paths,
        manifest={},
        target_ref="cluster-a",
    )

    assert loaded == receipt


@pytest.mark.parametrize(
    ("status", "marker"),
    (
        pytest.param("planned", "startedAt", id="started"),
        pytest.param("planned", "infraCompleteAt", id="infra-complete"),
        pytest.param("planned", "completedAt", id="completed-marker"),
        pytest.param("planned", "failedAt", id="failed-marker"),
        pytest.param("planned", "failureType", id="failure-type"),
        pytest.param("executing", None, id="executing"),
        pytest.param("failed", None, id="failed"),
        pytest.param("complete", None, id="complete"),
    ),
)
def test_soperator_install_replan_rejects_execution_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    marker: str | None,
) -> None:
    paths = _paths(tmp_path)
    authority = _install_plan_authority()
    receipt = _install_replan_receipt(authority)
    receipt["status"] = status
    if marker is not None:
        receipt[marker] = "evidence"
    monkeypatch.setattr(
        cli,
        "_soperator_install_plan_authority",
        lambda **_kwargs: copy.deepcopy(authority),
    )
    cli._write_owner_only_json(
        paths.reports_dir / "soperator-install-plan.json",
        receipt,
    )

    with pytest.raises(RuntimeError, match="never-executed saved plan"):
        cli._validate_soperator_install_replan_receipt(
            config={},
            paths=paths,
            manifest={},
            target_ref="cluster-a",
        )


def test_soperator_install_replan_rejects_missing_or_corrupt_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(
        cli,
        "_soperator_install_plan_authority",
        lambda **_kwargs: _install_plan_authority(),
    )

    with pytest.raises(RuntimeError, match="readable owner-only"):
        cli._validate_soperator_install_replan_receipt(
            config={}, paths=paths, manifest={}, target_ref="cluster-a"
        )

    cli._write_owner_only_json(
        paths.reports_dir / "soperator-install-plan.json",
        {"schema": "wrong", "status": "planned"},
    )
    with pytest.raises(RuntimeError, match="receipt is invalid"):
        cli._validate_soperator_install_replan_receipt(
            config={}, paths=paths, manifest={}, target_ref="cluster-a"
        )


def test_soperator_install_replan_rejects_saved_authority_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    authority = _install_plan_authority()
    receipt = _install_replan_receipt(authority)
    drifted = copy.deepcopy(authority)
    drifted["target"]["ref"] = "cluster-b"
    monkeypatch.setattr(
        cli,
        "_soperator_install_plan_authority",
        lambda **_kwargs: drifted,
    )
    cli._write_owner_only_json(
        paths.reports_dir / "soperator-install-plan.json",
        receipt,
    )

    with pytest.raises(RuntimeError, match="authority no longer matches"):
        cli._validate_soperator_install_replan_receipt(
            config={}, paths=paths, manifest={}, target_ref="cluster-a"
        )


def test_soperator_install_plan_generations_always_change_the_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    plan_path = paths.infra_dir / ".soperator-install.tfplan"
    plan_path.write_bytes(b"same-plan")
    monkeypatch.setattr(
        cli,
        "_soperator_install_plan_authority",
        lambda **_kwargs: _install_plan_authority(),
    )

    first = cli._soperator_install_plan_material(
        config_path=paths.config_path,
        paths=paths,
        manifest={},
        terraform_plan_path=plan_path,
        target_ref="cluster-a",
    )
    second = cli._soperator_install_plan_material(
        config_path=paths.config_path,
        paths=paths,
        manifest={},
        terraform_plan_path=plan_path,
        target_ref="cluster-a",
    )

    assert first["planGeneration"] != second["planGeneration"]
    assert first["approvalFingerprint"] != second["approvalFingerprint"]


def test_soperator_install_replan_publishes_validated_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    terraform_plan_path, receipt_path = cli._soperator_install_plan_paths(paths)
    terraform_plan_path.write_bytes(b"old-plan")
    terraform_plan_path.chmod(0o600)
    old_receipt = {"approvalFingerprint": "sha256:" + "a" * 64}
    cli._write_owner_only_json(receipt_path, old_receipt)
    new_receipt = {"approvalFingerprint": "sha256:" + "b" * 64}
    monkeypatch.setattr(
        cli,
        "_validate_soperator_install_replan_receipt",
        lambda **_kwargs: (terraform_plan_path, receipt_path, old_receipt),
    )
    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})

    def _plan(_infra_dir: Path, **kwargs: object) -> None:
        candidate = kwargs["plan_file"]
        assert isinstance(candidate, Path)
        candidate.write_bytes(b"new-plan")

    monkeypatch.setattr(cli, "terraform_plan", _plan)
    monkeypatch.setattr(cli, "terraform_show_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        cli,
        "_validate_soperator_install_terraform_plan_scope",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cli,
        "_soperator_install_plan_material",
        lambda **_kwargs: new_receipt,
    )

    returned_plan, returned_receipt, receipt = cli._replan_soperator_install(
        config={},
        paths=paths,
        manifest={},
        target_ref="cluster-a",
        expected_receipt=old_receipt,
    )

    assert returned_plan == terraform_plan_path
    assert returned_receipt == receipt_path
    assert receipt == new_receipt
    assert terraform_plan_path.read_bytes() == b"new-plan"
    assert cli.read_owner_only_json(receipt_path, label="test receipt") == new_receipt
    assert not any(path.name.endswith(".replan") for path in paths.infra_dir.iterdir())


@pytest.mark.parametrize("failure_stage", ("plan", "show", "scope", "receipt"))
def test_soperator_install_replan_preserves_saved_pair_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_stage: str,
) -> None:
    paths = _paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    terraform_plan_path, receipt_path = cli._soperator_install_plan_paths(paths)
    terraform_plan_path.write_bytes(b"old-plan")
    terraform_plan_path.chmod(0o600)
    old_receipt = {"approvalFingerprint": "sha256:" + "a" * 64}
    cli._write_owner_only_json(receipt_path, old_receipt)
    old_receipt_bytes = receipt_path.read_bytes()
    monkeypatch.setattr(
        cli,
        "_validate_soperator_install_replan_receipt",
        lambda **_kwargs: (terraform_plan_path, receipt_path, old_receipt),
    )
    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})

    def _plan(_infra_dir: Path, **kwargs: object) -> None:
        candidate = kwargs["plan_file"]
        assert isinstance(candidate, Path)
        candidate.write_bytes(b"new-plan")
        if failure_stage == "plan":
            raise RuntimeError("plan failed")

    def _show(*_args: object, **_kwargs: object) -> dict[str, object]:
        if failure_stage == "show":
            raise RuntimeError("show failed")
        return {}

    def _scope(*_args: object, **_kwargs: object) -> None:
        if failure_stage == "scope":
            raise RuntimeError("scope failed")

    def _receipt(**_kwargs: object) -> dict[str, str]:
        if failure_stage == "receipt":
            raise RuntimeError("receipt failed")
        return {"approvalFingerprint": "sha256:" + "b" * 64}

    monkeypatch.setattr(cli, "terraform_plan", _plan)
    monkeypatch.setattr(cli, "terraform_show_json", _show)
    monkeypatch.setattr(cli, "_validate_soperator_install_terraform_plan_scope", _scope)
    monkeypatch.setattr(cli, "_soperator_install_plan_material", _receipt)

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        cli._replan_soperator_install(
            config={},
            paths=paths,
            manifest={},
            target_ref="cluster-a",
            expected_receipt=old_receipt,
        )

    assert terraform_plan_path.read_bytes() == b"old-plan"
    assert receipt_path.read_bytes() == old_receipt_bytes
    assert not any(path.name.endswith(".replan") for path in paths.infra_dir.iterdir())


def test_soperator_install_replan_restores_saved_plan_when_receipt_publish_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    terraform_plan_path, receipt_path = cli._soperator_install_plan_paths(paths)
    terraform_plan_path.write_bytes(b"old-plan")
    terraform_plan_path.chmod(0o600)
    old_receipt = {"approvalFingerprint": "sha256:" + "a" * 64}
    cli._write_owner_only_json(receipt_path, old_receipt)
    old_receipt_bytes = receipt_path.read_bytes()
    monkeypatch.setattr(
        cli,
        "_validate_soperator_install_replan_receipt",
        lambda **_kwargs: (terraform_plan_path, receipt_path, old_receipt),
    )
    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(
        cli,
        "terraform_plan",
        lambda _infra_dir, **kwargs: kwargs["plan_file"].write_bytes(b"new-plan"),
    )
    monkeypatch.setattr(cli, "terraform_show_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        cli,
        "_validate_soperator_install_terraform_plan_scope",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cli,
        "_soperator_install_plan_material",
        lambda **_kwargs: {"approvalFingerprint": "sha256:" + "b" * 64},
    )
    real_write_owner_only_json = cli._write_owner_only_json

    def _write_receipt_then_fail(path: Path, payload: Mapping[str, Any]) -> None:
        real_write_owner_only_json(path, payload)
        if path == receipt_path:
            raise RuntimeError("receipt publish failed")

    monkeypatch.setattr(cli, "_write_owner_only_json", _write_receipt_then_fail)

    with pytest.raises(RuntimeError, match="receipt publish failed"):
        cli._replan_soperator_install(
            config={},
            paths=paths,
            manifest={},
            target_ref="cluster-a",
            expected_receipt=old_receipt,
        )

    assert terraform_plan_path.read_bytes() == b"old-plan"
    assert receipt_path.read_bytes() == old_receipt_bytes


def test_soperator_install_replan_retains_recovery_copies_when_rollback_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.infra_dir.mkdir(parents=True)
    terraform_plan_path, receipt_path = cli._soperator_install_plan_paths(paths)
    terraform_plan_path.write_bytes(b"old-plan")
    terraform_plan_path.chmod(0o600)
    candidate_plan_path = paths.infra_dir / ".candidate.tfplan"
    candidate_plan_path.write_bytes(b"new-plan")
    candidate_plan_path.chmod(0o600)
    old_receipt = {"approvalFingerprint": "sha256:" + "a" * 64}
    new_receipt = {"approvalFingerprint": "sha256:" + "b" * 64}
    cli._write_owner_only_json(receipt_path, old_receipt)

    real_write_owner_only_json = cli._write_owner_only_json

    def _write_receipt_then_fail(path: Path, payload: Mapping[str, Any]) -> None:
        real_write_owner_only_json(path, payload)
        if path == receipt_path:
            raise RuntimeError("receipt publish failed")

    real_replace = cli.os.replace
    canonical_plan_replacements = 0

    def _replace(src: object, dst: object, *args: object, **kwargs: object) -> None:
        nonlocal canonical_plan_replacements
        if not args and not kwargs and Path(dst) == terraform_plan_path:
            canonical_plan_replacements += 1
            if canonical_plan_replacements == 2:
                raise OSError("plan restore blocked")
        real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(cli, "_write_owner_only_json", _write_receipt_then_fail)
    monkeypatch.setattr(cli.os, "replace", _replace)

    with pytest.raises(RuntimeError, match="rollback was incomplete") as exc_info:
        cli._publish_soperator_install_replan(
            candidate_plan_path=candidate_plan_path,
            terraform_plan_path=terraform_plan_path,
            receipt_path=receipt_path,
            previous_receipt=old_receipt,
            receipt=new_receipt,
        )

    plan_backups = [path for path in paths.infra_dir.iterdir() if path.name.endswith(".backup")]
    receipt_backups = [
        path for path in paths.reports_dir.iterdir() if path.name.endswith(".backup")
    ]
    assert not terraform_plan_path.exists()
    assert len(plan_backups) == 1
    assert plan_backups[0].read_bytes() == b"old-plan"
    assert len(receipt_backups) == 1
    assert cli.read_owner_only_json(receipt_backups[0], label="test receipt backup") == old_receipt
    assert str(plan_backups[0]) in str(exc_info.value)
    assert str(receipt_backups[0]) in str(exc_info.value)


def test_soperator_install_rejects_one_step_automated_apply_before_project_work(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli,
        "create_command",
        lambda **_kwargs: pytest.fail("must require reviewed plan before project work"),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "install",
            str(tmp_path),
            "--profile",
            "mixed",
            "--no-interactive",
            "--execute",
            "--approve",
        ],
    )

    assert result.exit_code == 1
    assert "--resume" in _normalized(result.output)
    assert "--approval-fingerprint" in _normalized(result.output)


def test_soperator_install_noninteractive_requires_release_before_resolver_or_project_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli,
        "resolve_soperator_release",
        lambda *_args, **_kwargs: pytest.fail("missing release must fail before resolution"),
    )
    monkeypatch.setattr(
        cli,
        "create_command",
        lambda **_kwargs: pytest.fail("missing release must fail before project work"),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "install",
            str(tmp_path),
            "--profile",
            "mixed",
            "--no-interactive",
            "--dry-run",
        ],
    )

    assert result.exit_code == 1
    assert "requires --release latest or exact X.Y.Z" in _normalized(result.output)


def test_soperator_install_resume_rejects_release_override_before_plan_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tenant" / "project" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("version: v1\n", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "_load_soperator_install_plan",
        lambda **_kwargs: pytest.fail("conflicting release must fail before plan load"),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "install",
            str(config_path),
            "--resume",
            "--release",
            "4.1.7",
            "--no-interactive",
            "--dry-run",
        ],
    )

    assert result.exit_code == 1
    assert "reuses its frozen release" in _normalized(result.output)


@pytest.mark.parametrize(
    "fresh_args",
    (
        pytest.param(["--client-name", "other"], id="client-name"),
        pytest.param(["--tenant-id", "tenant-b"], id="tenant-id"),
        pytest.param(["--project-id", "project-b"], id="project-id"),
        pytest.param(["--region-id", "eu-west1"], id="region-id"),
        pytest.param(["--email", "operator@example.invalid"], id="email"),
        pytest.param(["--profile", "gpu"], id="profile"),
        pytest.param(["--network-id", "network-b"], id="network-id"),
        pytest.param(["--subnet-id", "subnet-b"], id="subnet-id"),
        pytest.param(["--network-ref", "network-b"], id="network-ref"),
        pytest.param(["--subnet-ref", "subnet-b"], id="subnet-ref"),
        pytest.param(["--force"], id="force"),
    ),
)
def test_soperator_install_resume_rejects_fresh_only_options_before_plan_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fresh_args: list[str],
) -> None:
    config_path = tmp_path / "tenant" / "project" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("version: v1\n", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "_load_soperator_install_plan",
        lambda **_kwargs: pytest.fail("fresh-only options must fail before plan load"),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "install",
            str(config_path),
            "--resume",
            *fresh_args,
            "--no-interactive",
            "--dry-run",
        ],
    )

    output = _normalized(result.output)
    assert result.exit_code == 1
    assert "does not accept fresh-install options" in output
    assert fresh_args[0] in output


def test_soperator_install_plan_scope_accepts_only_mk8s_and_sfs_modules(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "rendered_module_sources",
        lambda *_args, **_kwargs: (
            SimpleNamespace(module_name="cluster", component_id="mk8s"),
            SimpleNamespace(module_name="storage", component_id="sfs"),
        ),
    )
    monkeypatch.setattr(cli, "resolve_component_sources_profile", lambda: "portable")

    cli._validate_soperator_install_terraform_plan_scope(
        {},
        {
            "resource_changes": [
                {
                    "address": "module.cluster.nebius_mk8s_v1_cluster.this",
                    "change": {"actions": ["create"]},
                },
                {
                    "address": "module.storage.nebius_compute_v1_filesystem.jail",
                    "change": {"actions": ["create"]},
                },
            ]
        },
    )


def test_soperator_install_plan_scope_rejects_unrelated_or_destructive_changes(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "rendered_module_sources",
        lambda *_args, **_kwargs: (
            SimpleNamespace(module_name="cluster", component_id="mk8s"),
            SimpleNamespace(module_name="storage", component_id="sfs"),
            SimpleNamespace(module_name="database", component_id="managed-postgresql"),
        ),
    )
    monkeypatch.setattr(cli, "resolve_component_sources_profile", lambda: "portable")

    with pytest.raises(RuntimeError, match="unrelated infrastructure"):
        cli._validate_soperator_install_terraform_plan_scope({}, {"resource_changes": []})

    monkeypatch.setattr(
        cli,
        "rendered_module_sources",
        lambda *_args, **_kwargs: (
            SimpleNamespace(module_name="cluster", component_id="mk8s"),
            SimpleNamespace(module_name="storage", component_id="sfs"),
        ),
    )
    with pytest.raises(RuntimeError, match="delete or replacement"):
        cli._validate_soperator_install_terraform_plan_scope(
            {},
            {
                "resource_changes": [
                    {
                        "address": "module.cluster.nebius_mk8s_v1_cluster.this",
                        "change": {"actions": ["delete", "create"]},
                    }
                ]
            },
        )


@pytest.mark.parametrize(
    ("job_args", "message"),
    (
        (
            ["--job-policy", "requeue-selected", "--cancel-job", "101", "--requeue-job", "201"],
            "--cancel-job is valid only with --job-policy cancel-selected",
        ),
        (
            ["--job-policy", "cancel-selected", "--cancel-job", "101", "--requeue-job", "201"],
            "--requeue-job is valid only with --job-policy requeue-selected",
        ),
        (
            ["--job-policy", "cancel-selected"],
            "--job-policy cancel-selected requires at least one --cancel-job",
        ),
        (
            ["--job-policy", "requeue-hold-selected"],
            "--job-policy requeue-hold-selected requires at least one --requeue-job",
        ),
        (
            ["--cancel-job", "101"],
            "--cancel-job is valid only with --job-policy cancel-selected",
        ),
        (
            ["--job-policy", "interactive"],
            "--job-policy interactive requires --interactive in a prompt-capable terminal",
        ),
    ),
)
def test_upgrade_rejects_incompatible_job_controls_before_config_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    job_args: list[str],
    message: str,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    monkeypatch.setattr(
        cli,
        "_load_source_payload",
        lambda _path: pytest.fail("invalid job controls must fail before config access"),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "upgrade",
            str(tmp_path / "config.yaml"),
            "--target",
            "cluster-a",
            "--to-release",
            "4.1.7",
            *job_args,
            "--dry-run",
            "--no-interactive",
        ],
    )

    assert result.exit_code == 1
    assert message in _normalized(result.output)


def test_upgrade_noninteractive_requires_complete_target_before_config_or_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli,
        "_load_source_payload",
        lambda _path: pytest.fail("missing targets must fail before config discovery"),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "upgrade",
            str(tmp_path / "config.yaml"),
            "--target",
            "cluster-a",
            "--dry-run",
            "--no-interactive",
        ],
    )

    assert result.exit_code == 1
    normalized = _normalized(result.output)
    assert "Non-interactive Soperator upgrade requires" in normalized
    assert "--to-release" in normalized
    assert "--to-k8s-version" in normalized
    assert "--to-os" in normalized
    assert "--to-gpu-stack-preset" in normalized


@pytest.mark.parametrize(
    ("prompted", "expected"),
    [
        ("latest(4.1.7)", "4.1.7"),
        ("latest", "4.1.7"),
        ("4.0.5", "4.0.5"),
    ],
)
def test_interactive_release_selector_shows_exact_latest_and_accepts_supported_inputs(
    monkeypatch: pytest.MonkeyPatch,
    prompted: str,
    expected: str,
) -> None:
    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        cli,
        "resolve_soperator_release",
        lambda selector: (
            SimpleNamespace(release="4.1.7")
            if selector == "latest"
            else pytest.fail("interactive preview must resolve only latest")
        ),
    )

    def _prompt(text: str, *, default: str) -> str:
        prompts.append((text, default))
        return prompted

    monkeypatch.setattr(cli.typer, "prompt", _prompt)

    assert (
        cli._new_soperator_release_selector(
            None,
            interactive=True,
            command_name="upgrade",
            option_name="--to-release",
        )
        == expected
    )
    assert prompts == [("Soperator upgrade release", "latest(4.1.7)")]


def test_full_stack_child_calls_command_neutral_node_template_executor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cli,
        "upgrade_node_template_command",
        lambda *_args, **_kwargs: pytest.fail("parent must not call the Typer handler"),
    )
    monkeypatch.setattr(
        cli,
        "_execute_node_template_upgrade",
        lambda **kwargs: calls.append(kwargs),
    )

    cli._run_node_template_upgrade_suboperation(
        config_path=tmp_path / "config.yaml",
        target_selector="infra:mk8s@cluster-a",
        to_version="1.34",
        to_os="ubuntu24.04",
        to_gpu_stack_preset="cuda13.0",
        node_group="worker",
        disruption_policy="safe-surge",
        drain_timeout="30m",
        strategy_max_surge_count=1,
    )

    assert calls == [
        {
            "config_path": tmp_path / "config.yaml",
            "target_selector": "infra:mk8s@cluster-a",
            "to_version": "1.34",
            "to_os": "ubuntu24.04",
            "to_gpu_stack_preset": "cuda13.0",
            "node_group": "worker",
            "dry_run": False,
            "disruption_policy": "safe-surge",
            "drain_timeout": "30m",
            "strategy_max_surge_count": 1,
            "skip_validations": False,
            "skip_validation": None,
            "interactive": False,
        }
    ]


@pytest.mark.parametrize("disruption_policy", ["zero-surge", "force-delete"])
def test_full_stack_child_translates_frozen_zero_to_non_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    disruption_policy: str,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cli,
        "_execute_node_template_upgrade",
        lambda **kwargs: calls.append(kwargs),
    )

    cli._run_node_template_upgrade_suboperation(
        config_path=tmp_path / "config.yaml",
        target_selector="infra:mk8s@cluster-a",
        to_version="1.34",
        to_os="ubuntu24.04",
        to_gpu_stack_preset="",
        node_group="system",
        disruption_policy=disruption_policy,
        drain_timeout="30m",
        strategy_max_surge_count=0,
    )

    assert calls[0]["strategy_max_surge_count"] is None


def test_full_stack_parent_forwards_fenced_authority_to_release_child() -> None:
    source = inspect.getsource(cli.soperator_upgrade_command)

    assert re.search(
        r"def _assert_campaign_authority\(\) -> SoperatorLeaseAuthority:.*?"
        r"lease_authority = campaign_lease\.assert_held\(\).*?"
        r"return lease_authority.*?"
        r"assert_parent_authority=_assert_campaign_authority",
        source,
        re.DOTALL,
    )


def test_full_stack_surfaces_post_plan_authority_and_maintenance_progress() -> None:
    source = inspect.getsource(cli.soperator_upgrade_command)

    plan = source.index("_print_upgrade_plan_lines(")
    authority = source.index('"operation-authority"', plan)
    lease = source.index("SoperatorOperationLease(", authority)
    maintenance = source.index('"maintenance-entry"', lease)
    run = source.index("return run_campaign(", maintenance)

    assert plan < authority < lease < maintenance < run
    assert "emit=authority_progress.milestone" in source[authority:maintenance]
    assert "maintenance_progress.milestone" in source[maintenance:run]
    assert re.search(
        r"job_prompt_pause_token = _SOPERATOR_UPGRADE_JOB_PROMPT_PAUSE\.set\(\s*"
        r"maintenance_progress\.paused\s*\).*?"
        r"try:.*?_enter_maintenance_impl\(.*?finally:.*?"
        r"_SOPERATOR_UPGRADE_JOB_PROMPT_PAUSE\.reset\(job_prompt_pause_token\)",
        source[maintenance:run],
        re.DOTALL,
    )


def test_full_stack_parent_forwards_campaign_spool_checkpoint_to_release_child() -> None:
    source = inspect.getsource(cli.soperator_upgrade_command)
    common_source = inspect.getsource(cli._run_common_soperator_release_upgrade)

    assert re.search(
        r"campaign_controller_spool_store = CampaignControllerSpoolMigrationStore\(.*?"
        r"external_controller_spool_migration_store=\(\s*"
        r"campaign_controller_spool_store\s*\)",
        source,
        re.DOTALL,
    )
    assert "external_controller_spool_migration_store.read" in common_source
    assert "external_controller_spool_migration_store.write" in common_source


def test_full_stack_final_readiness_refreshes_sources_and_reproves_release_graph() -> None:
    source = inspect.getsource(cli.soperator_upgrade_command)
    boundary_source = inspect.getsource(cli.run_final_runtime_validation_boundary)

    assert re.search(
        r"def _final_readiness\(\).*?"
        r"target_flux_paths = _paths_for_target_flux_dir\(paths, selected_target\).*?"
        r"def _refresh_sources\(\).*?prepare_soperator_release_sources\(.*?"
        r"def _prove_release_graph\(\).*?wait_for_soperator_release_graph\(.*?"
        r"def _validate_runtime\(.*?_run_target_upgrade_validations\(.*?"
        r"run_final_runtime_validation_boundary\(.*?"
        r"refresh_sources=_refresh_sources.*?"
        r"prove_release_graph=_prove_release_graph.*?"
        r"validate_runtime=_validate_runtime",
        source,
        re.DOTALL,
    )
    assert re.search(
        r"source_evidence = refresh_sources\(\).*?"
        r"prove_release_graph\(\).*?before = freeze_capacity\(\).*?"
        r"runtime_evidence = validate_runtime\(before\).*?"
        r"prove_release_graph\(\).*?after = freeze_capacity\(\).*?"
        r"assert_final_capacity_snapshot_unchanged\(before, after\)",
        boundary_source,
        re.DOTALL,
    )


def test_full_stack_derives_provider_authority_and_revalidates_each_frozen_hop() -> None:
    source = inspect.getsource(cli.soperator_upgrade_command)
    compatibility_source = inspect.getsource(cli.assert_frozen_compatibility_row_supported)

    assert "--allow-provider-api-upgrade" not in source
    assert re.search(
        r"provider_api_authorized=bool\(execute and approve and is_onboarded\)",
        source,
    )
    assert re.search(
        r"def _provider_node_template_segment\(version: str\).*?"
        r"_assert_campaign_authority\(\).*?"
        r"_assert_frozen_provider_compatibility\(version\).*?"
        r"infrastructure_backend\.apply_version\(version\)",
        source,
        re.DOTALL,
    )
    assert source.count("apply_frozen_node_group_rows(") == 2
    assert 'code="provider-compatibility-drift"' in compatibility_source


def test_full_stack_recovery_reuses_frozen_resolved_max_surge_count() -> None:
    source = inspect.getsource(cli.soperator_upgrade_command)

    assert re.search(
        r"intent = recovery_intent.*?"
        r"policy = validate_disruption_policy\(intent\.node_group_strategy\).*?"
        r"resolved_max_surge = intent\.strategy_max_surge_count.*?"
        r"else:\s+missing_non_interactive",
        source,
        re.DOTALL,
    )
    assert re.search(
        r"def _terraform_managed_stage\(.*?"
        r"strategy_max_surge_count=resolved_max_surge.*?"
        r"def _onboarded_provider_api_stage\(.*?"
        r"strategy_max_surge_count=resolved_max_surge",
        source,
        re.DOTALL,
    )


def test_common_in_place_upgrade_writes_target_then_uses_exact_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _paths(tmp_path)
    source_payload = {
        "client_info": {"nebius": {"project_id": "project-a"}},
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster-a",
                    "target_ref": "cluster-a",
                    "enabled": True,
                    "namespace": "soperator",
                    "release-name": "soperator",
                    "version": "4.1.6",
                    "values": {
                        "externalNfs": {"enabled": True, "server": "10.0.0.20"},
                        "nodesets": [{"name": "worker"}],
                    },
                }
            ]
        },
    }
    original_source_payload = copy.deepcopy(source_payload)
    target = cli._parse_soperator_upgrade_target("cluster-a")
    generated_config = SimpleNamespace()
    manifest: dict[str, Any] = {"deploy": {"targets": []}}
    writes: list[str] = []
    applies: list[dict[str, Any]] = []
    intents: list[str] = []
    verified_values: list[Mapping[str, Any]] = []
    paths.config_path.write_text("version: v1\n", encoding="utf-8")
    rendered_values = {
        "nodesets": {"enabled": True},
        "observability": {"enabled": True, "overrideValues": {"cluster": "materialized"}},
        "slurmCluster": {"overrideValues": {"nodesets": [{"name": "worker"}]}},
    }
    _write_rendered_soperator_values(
        paths,
        target_ref="cluster-a",
        values=rendered_values,
    )

    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (generated_config, paths, manifest),
    )
    monkeypatch.setattr(
        cli,
        "_load_deploy_context",
        lambda _path: (generated_config, paths, manifest),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_selected_deploy_targets",
        lambda *_args, **_kwargs: [{"target_ref": "cluster-a"}],
    )
    monkeypatch.setattr(cli, "_paths_for_target_flux_dir", lambda path, target_row: path)
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *_args, **_kwargs: {
            "KUBECONFIG": "/private/test-kubeconfig",
            cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "mk8scluster-a",
            cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx-a",
        },
    )
    monkeypatch.setattr(cli, "_live_soperator_release_for_reconcile", lambda **_kwargs: "4.1.6")
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **_kwargs: "uid")
    monkeypatch.setattr(cli, "load_active_soperator_release_intent", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "freeze_soperator_release",
        lambda *_args, **_kwargs: SimpleNamespace(
            snapshot=SimpleNamespace(
                release="4.1.7",
                capability_contract="upstream-flux-v1",
                capability_sha256="sha256:" + "2" * 64,
                snapshot_sha256="sha256:" + "3" * 64,
                source_manifest_sha256="sha256:" + "5" * 64,
                populate_jail_image=("registry.example.invalid/jail@sha256:" + "4" * 64),
            )
        ),
    )
    monkeypatch.setattr(
        cli,
        "inspect_soperator_release_contract",
        lambda *_args, **_kwargs: (None, None, "upstream-flux-v1", "sha256:" + "1" * 64),
    )
    monkeypatch.setattr(
        cli,
        "begin_soperator_release_intent",
        lambda **_kwargs: intents.append("begin"),
    )
    monkeypatch.setattr(
        cli,
        "complete_soperator_release_intent",
        lambda **_kwargs: intents.append("complete"),
    )

    class _Lease:
        def assert_held(self) -> None:
            pass

    @contextmanager
    def _cluster_lease(**_kwargs: Any):
        yield _Lease()

    monkeypatch.setattr(cli, "SoperatorOperationLease", _cluster_lease)
    monkeypatch.setattr(
        cli,
        "discover_soperator_infrastructure_receipt",
        lambda **_kwargs: replace(
            sample_infrastructure_receipt(),
            nebius_cluster_id="mk8scluster-a",
            kubernetes_uid="uid",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_render_soperator_upgrade_admission",
        lambda **kwargs: SimpleNamespace(
            admitted_config=kwargs["source_payload"],
            staged_paths=paths,
            rendered_flux_sha256="sha256:" + "7" * 64,
            project_generation_sha256="sha256:" + "8" * 64,
            project_generation_plan=SimpleNamespace(
                writes={
                    paths.config_path: cli.render_updated_source_payload(kwargs["source_payload"])
                },
                removals=(),
                expected_preimages={
                    paths.config_path: "sha256:"
                    + hashlib.sha256(paths.config_path.read_bytes()).hexdigest()
                },
                preimage_sha256="sha256:" + "9" * 64,
            ),
            proposed_config_text=cli.render_updated_source_payload(kwargs["source_payload"]),
            cleanup=lambda: None,
        ),
    )
    monkeypatch.setattr(cli, "_validate_rendered_flux_manifests", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_flux_bundle_sha256",
        lambda _paths: "sha256:" + "7" * 64,
    )
    monkeypatch.setattr(cli, "validate_config", lambda payload, **_kwargs: payload)
    monkeypatch.setattr(cli, "ensure_soperator_release_source", lambda _snapshot: object())

    def _verify_artifacts(*_args: object, **kwargs: object) -> SoperatorArtifactReceipt:
        values = kwargs.get("values")
        assert isinstance(values, Mapping)
        verified_values.append(values)
        return SoperatorArtifactReceipt(
            release="4.1.7",
            source_manifest_sha256="sha256:" + "5" * 64,
            chart_package_sha256=(),
            umbrella_render_sha256="sha256:" + "6" * 64,
        )

    monkeypatch.setattr(
        cli,
        "verify_soperator_release_artifacts",
        _verify_artifacts,
    )
    monkeypatch.setattr(cli, "_soperator_upgrade_partition_state_snapshot", lambda **_kwargs: ())
    monkeypatch.setattr(
        cli, "_soperator_upgrade_worker_nodeset_pod_candidates", lambda **_kwargs: ()
    )
    monkeypatch.setattr(cli, "_soperator_upgrade_slurm_node_filter", lambda **_kwargs: ())
    monkeypatch.setattr(cli, "_soperator_upgrade_affected_jobs", lambda **_kwargs: ())
    monkeypatch.setattr(cli, "_soperator_upgrade_reservation_preimages", lambda **_kwargs: ())
    monkeypatch.setattr(
        cli,
        "observe_login_service_continuity",
        lambda *_args, **_kwargs: {"status": "unknown"},
    )

    class _Transaction:
        def __init__(self, _project_dir: Path) -> None:
            pass

        def current_generation_sha256(self) -> None:
            return None

        def commit(self, updates: Mapping[Path, str], **_kwargs: object) -> None:
            writes.append(str(updates[paths.config_path]))

    monkeypatch.setattr(cli, "ProjectBundleTransaction", _Transaction)
    monkeypatch.setattr(cli, "_run_generated_bundle_validation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux_with_soperator_job_policy",
        lambda *_args, **kwargs: (
            applies.append(kwargs),
            SimpleNamespace(complete=lambda: None),
        )[1],
    )

    cli._run_common_soperator_release_upgrade(
        config_path=paths.config_path,
        source_payload=source_payload,
        target=target,
        ownership="managed",
        target_selector="4.1.7",
        dry_run=False,
        job_policy="wait-to-finish",
        cancel_job_ids=(),
        requeue_job_ids=(),
        job_wait_timeout="0s",
        job_refresh_interval="30s",
    )

    plan_output = capsys.readouterr().out
    assert "authoritative telemetry credential" not in plan_output
    assert "OBSERVABILITY static key" not in plan_output
    assert "soperator status --verify-observability" in plan_output
    assert "version: 4.1.7" in writes[0]
    assert applies[0]["strategy"].strategy.value == "in-place"
    assert applies[0]["infrastructure_plan_sha256"].startswith("sha256:")
    assert applies[0]["source_capability_sha256"] == "sha256:" + "1" * 64
    assert applies[0]["operation_source_release"] == "4.1.6"
    assert callable(applies[0]["assert_authority"])
    assert intents == ["begin", "complete"]
    assert source_payload == original_source_payload
    assert verified_values == [rendered_values]


def test_interrupted_latest_upgrade_reuses_frozen_target_without_resolving_latest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.config_path.write_text("version: v1\n", encoding="utf-8")
    _write_rendered_soperator_values(
        paths,
        target_ref="cluster-a",
        values={"nodesets": {"enabled": False}},
    )
    source_payload = {
        "client_info": {"nebius": {"project_id": "project-a"}},
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster-a",
                    "target_ref": "cluster-a",
                    "enabled": True,
                    "namespace": "soperator",
                    "release-name": "soperator",
                    "version": "4.1.7",
                    "values": {"externalNfs": {"enabled": True, "server": "10.0.0.20"}},
                }
            ]
        },
    }
    target = cli._parse_soperator_upgrade_target("cluster-a")
    generated_config = SimpleNamespace()
    manifest: dict[str, Any] = {"deploy": {"targets": []}}
    frozen_snapshot = SimpleNamespace(
        release="4.1.7",
        capability_contract="upstream-flux-v1",
        capability_sha256="sha256:" + "2" * 64,
        snapshot_sha256="sha256:" + "3" * 64,
        source_manifest_sha256="sha256:" + "5" * 64,
        populate_jail_image="registry.example.invalid/jail@sha256:" + "4" * 64,
    )
    intent = SimpleNamespace(
        source_release="4.1.6",
        source_contract="upstream-flux-v1",
        source_capability_sha256="sha256:" + "1" * 64,
        strategy="in-place",
        target_jail_image="registry.example.invalid/jail@sha256:" + "4" * 64,
        target_jail_image_source="upstream-default",
    )
    applies: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _path: (generated_config, paths, manifest),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_selected_deploy_targets",
        lambda *_args, **_kwargs: [{"target_ref": "cluster-a"}],
    )
    monkeypatch.setattr(cli, "_paths_for_target_flux_dir", lambda path, _target: path)
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *_args, **_kwargs: {
            cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "mk8scluster-a",
            cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx-a",
        },
    )
    monkeypatch.setattr(cli, "_live_soperator_release_for_reconcile", lambda **_kwargs: "4.1.7")
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **_kwargs: "uid")
    monkeypatch.setattr(
        cli,
        "load_active_soperator_release_intent",
        lambda **_kwargs: (intent, frozen_snapshot),
    )
    monkeypatch.setattr(cli, "soperator_operation_anchor_status", lambda **_kwargs: "active")
    monkeypatch.setattr(
        cli,
        "freeze_soperator_release",
        lambda *_args, **_kwargs: pytest.fail("latest must not be resolved during recovery"),
    )
    monkeypatch.setattr(
        cli,
        "frozen_soperator_release_from_snapshot",
        lambda snapshot: SimpleNamespace(snapshot=snapshot),
    )
    monkeypatch.setattr(
        cli,
        "inspect_soperator_release_contract",
        lambda release: (
            None,
            None,
            "upstream-flux-v1",
            "sha256:" + "1" * 64,
        ),
    )
    monkeypatch.setattr(
        cli,
        "begin_soperator_release_intent",
        lambda **_kwargs: pytest.fail("recovery must not replace the active intent"),
    )
    monkeypatch.setattr(cli, "complete_soperator_release_intent", lambda **_kwargs: None)

    class _Lease:
        def assert_held(self) -> None:
            pass

    @contextmanager
    def _cluster_lease(**_kwargs: Any):
        yield _Lease()

    monkeypatch.setattr(cli, "SoperatorOperationLease", _cluster_lease)
    monkeypatch.setattr(
        cli,
        "discover_soperator_infrastructure_receipt",
        lambda **_kwargs: replace(
            sample_infrastructure_receipt(),
            nebius_cluster_id="mk8scluster-a",
            kubernetes_uid="uid",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_render_soperator_upgrade_admission",
        lambda **_kwargs: SimpleNamespace(
            admitted_config=source_payload,
            staged_paths=paths,
            rendered_flux_sha256="sha256:" + "7" * 64,
            project_generation_sha256="sha256:" + "8" * 64,
            project_generation_plan=SimpleNamespace(
                writes={paths.config_path: cli.render_updated_source_payload(source_payload)},
                removals=(),
                expected_preimages={
                    paths.config_path: "sha256:"
                    + hashlib.sha256(paths.config_path.read_bytes()).hexdigest()
                },
                preimage_sha256="sha256:" + "9" * 64,
            ),
            proposed_config_text=cli.render_updated_source_payload(source_payload),
            cleanup=lambda: None,
        ),
    )
    monkeypatch.setattr(cli, "_validate_rendered_flux_manifests", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_flux_bundle_sha256",
        lambda _paths: "sha256:" + "7" * 64,
    )
    monkeypatch.setattr(cli, "validate_config", lambda payload, **_kwargs: payload)
    monkeypatch.setattr(cli, "ensure_soperator_release_source", lambda _snapshot: object())
    artifact_receipt = SoperatorArtifactReceipt(
        release="4.1.7",
        source_manifest_sha256="sha256:" + "5" * 64,
        chart_package_sha256=(),
        umbrella_render_sha256="sha256:" + "6" * 64,
    )
    monkeypatch.setattr(
        cli,
        "verify_soperator_release_artifacts",
        lambda *_args, **_kwargs: artifact_receipt,
    )
    monkeypatch.setattr(cli, "_soperator_upgrade_partition_state_snapshot", lambda **_kwargs: ())
    monkeypatch.setattr(
        cli, "_soperator_upgrade_worker_nodeset_pod_candidates", lambda **_kwargs: ()
    )
    monkeypatch.setattr(cli, "_soperator_upgrade_slurm_node_filter", lambda **_kwargs: ())
    monkeypatch.setattr(cli, "_soperator_upgrade_affected_jobs", lambda **_kwargs: ())
    monkeypatch.setattr(cli, "_soperator_upgrade_reservation_preimages", lambda **_kwargs: ())
    monkeypatch.setattr(
        cli,
        "observe_login_service_continuity",
        lambda *_args, **_kwargs: {"status": "unknown"},
    )
    admission_material = {
        "schema": cli._SOPERATOR_UPGRADE_ADMISSION_SCHEMA,
        "targetRef": "cluster-a",
        "ownership": "managed",
        "clusterId": "mk8scluster-a",
        "kubernetesUid": "uid",
        "sourceRelease": "4.1.6",
        "targetRelease": "4.1.7",
        "strategy": "in-place",
        "releaseSnapshotSha256": "sha256:" + "3" * 64,
        "sourceManifestSha256": "sha256:" + "5" * 64,
        "artifactReceiptSha256": cli.soperator_sha256(cli.asdict(artifact_receipt)),
        "desiredConfigSha256": cli.soperator_sha256(cli.to_plain_data(source_payload)),
        "renderedFluxSha256": "sha256:" + "7" * 64,
        "projectGenerationSha256": "sha256:" + "8" * 64,
        "projectPreimageSha256": "sha256:" + "9" * 64,
        "targetJailImage": "registry.example.invalid/jail@sha256:" + "4" * 64,
        "targetJailImageSource": "upstream-default",
        "persistentPaths": [],
        "rootfsTransition": {"mode": "not-required"},
        "infrastructure": replace(
            sample_infrastructure_receipt(),
            nebius_cluster_id="mk8scluster-a",
            kubernetes_uid="uid",
        ).as_payload(),
        "rootfsPreflight": {"mode": "not-required"},
        "protectedStateSha256": cli.soperator_sha256({"mode": "not-required"}),
        "jobControl": {
            "policy": "wait-to-finish",
            "cancelJobIds": [],
            "requeueJobIds": [],
            "waitTimeoutSeconds": 0,
            "refreshIntervalSeconds": 30,
            "partitionSelection": "all-live-up",
            "newJobFallback": "wait-to-finish",
        },
        "slurmPreimage": cli.build_slurm_upgrade_preimage(
            partitions=(), jobs=(), reservations=()
        ).as_payload(),
        "loginContinuity": {"status": "unknown"},
    }
    admission_token = cli.hashlib.sha256(b"cluster-a").hexdigest()[:16]
    cli._write_owner_only_json(
        paths.reports_dir / f"soperator-upgrade-admission-{admission_token}.json",
        {
            **admission_material,
            "fingerprint": cli.soperator_sha256(admission_material),
            "createdAt": "2026-08-25T00:00:00Z",
        },
    )
    monkeypatch.setattr(cli, "_run_generated_bundle_validation", lambda *_args, **_kwargs: None)

    class _RecoveredTransaction:
        def __init__(self, _project_dir: Path) -> None:
            pass

        def current_generation_sha256(self) -> str:
            return "sha256:" + "8" * 64

        def commit(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("a recovered exact generation must not be recommitted")

    monkeypatch.setattr(cli, "ProjectBundleTransaction", _RecoveredTransaction)
    monkeypatch.setattr(
        cli,
        "_load_deploy_context",
        lambda _path: (generated_config, paths, manifest),
    )
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux_with_soperator_job_policy",
        lambda *_args, **kwargs: (
            applies.append(kwargs),
            SimpleNamespace(complete=lambda: None),
        )[1],
    )

    cli._run_common_soperator_release_upgrade(
        config_path=paths.config_path,
        source_payload=source_payload,
        target=target,
        ownership="managed",
        target_selector="latest",
        dry_run=False,
        job_policy="wait-to-finish",
        cancel_job_ids=(),
        requeue_job_ids=(),
        job_wait_timeout="0s",
        job_refresh_interval="30s",
    )

    assert applies[0]["operation_source_release"] == "4.1.6"
    assert applies[0]["source_capability_sha256"] == "sha256:" + "1" * 64


def test_slurm_restore_replays_only_checkpoint_owned_state_in_safe_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    authority_calls: list[str] = []
    node_state = {
        "n2": {
            "state": "DRAIN",
            "reason": "cxcli-soperator-upgrade:0123456789abcdef",
        }
    }
    partition_record = SimpleNamespace(partition="worker")
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_pause_records_from_payload",
        lambda _payload: (partition_record,),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_restore_slurm_nodes",
        lambda **kwargs: (
            calls.append(("nodes", tuple(kwargs["node_names"]))),
            node_state.update(
                {node: {"state": "IDLE", "reason": ""} for node in kwargs["node_names"]}
            ),
        ),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_restore_slurm_partitions",
        lambda **kwargs: calls.append(("partitions", kwargs["records"])),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_release_jobs",
        lambda namespace, job_ids, **_kwargs: calls.append(("jobs", (namespace, tuple(job_ids)))),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_delete_maintenance_reservation",
        lambda **kwargs: calls.append(("reservation", kwargs["reservation_name"])),
    )

    held_jobs = {
        "42": cli.AffectedSlurmJob(
            "42",
            "user",
            "PENDING",
            "worker",
            "",
            "",
            "",
            "JobHeldUser",
            "",
            "",
            "",
            "job-42",
            "recovery",
        ),
        "84": cli.AffectedSlurmJob(
            "84",
            "user",
            "PENDING",
            "worker",
            "",
            "",
            "",
            "JobHeldUser",
            "",
            "",
            "",
            "job-84",
            "recovery",
        ),
    }
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_jobs_by_id",
        lambda **kwargs: tuple(held_jobs[job_id] for job_id in kwargs["job_ids"]),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_node_recovery_snapshot",
        lambda **_kwargs: dict(node_state),
    )
    actions = [
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "nodes-drain-recorded",
            "actionId": "node-action",
            "node_names": ["n2"],
            "node_preimage": {"n2": {"state": "IDLE", "reason": ""}},
        },
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "nodes-drained",
            "actionId": "node-action",
            "node_names": ["n2"],
            "owned_node_names": ["n2"],
        },
        {
            "namespace": "soperator",
            "action": "scheduling-pause-recorded",
            "partitions": [{"partition": "worker"}],
        },
        {
            "namespace": "soperator",
            "action": "scheduling-pause-applied",
            "partitions": [{"partition": "worker"}],
        },
        {
            "namespace": "soperator",
            "action": "requeue-hold-selected",
            "job_ids": ["42"],
            "jobs": [{"job_id": "42", "state": "RUNNING"}],
        },
        {
            "namespace": "soperator",
            "action": "requeue-hold-selected-applied",
            "job_ids": ["42"],
            "jobs": [held_jobs["42"].__dict__],
        },
        {
            "namespace": "soperator",
            "action": "pending-hold-recorded",
            "job_ids": ["84"],
            "jobs": [{"job_id": "84", "state": "PENDING"}],
        },
        {
            "namespace": "soperator",
            "action": "pending-hold-applied",
            "job_ids": ["84"],
            "jobs": [held_jobs["84"].__dict__],
        },
        {
            "namespace": "soperator",
            "action": "maintenance-reservation-recorded",
            "reservation_name": "cxcli_0123456789abcdef",
            "preexisting_reservations": ["customer-maintenance"],
        },
        {
            "namespace": "soperator",
            "action": "maintenance-reservation-applied",
            "reservation_name": "cxcli_0123456789abcdef",
        },
        {"namespace": "soperator", "action": "cancel-selected", "job_ids": ["99"]},
    ]

    receipt = cli._restore_soperator_flux_apply_infrastructure(
        actions,
        extra_env={},
        assert_authority=lambda: authority_calls.append("held"),
    )
    requeued_receipt = cli._release_soperator_flux_apply_jobs(
        actions,
        group="requeued-running",
        extra_env={},
        assert_authority=lambda: authority_calls.append("held"),
    )
    held_receipt = cli._release_soperator_flux_apply_jobs(
        actions,
        group="other-held",
        extra_env={},
        assert_authority=lambda: authority_calls.append("held"),
    )

    assert [kind for kind, _value in calls] == [
        "nodes",
        "partitions",
        "reservation",
        "jobs",
        "jobs",
    ]
    assert calls[-2][1] == ("soperator", ("42",))
    assert calls[-1][1] == ("soperator", ("84",))
    assert authority_calls == ["held", "held", "held", "held", "held"]
    assert receipt == {
        "namespaces": ["soperator"],
        "restoredNodeCount": 1,
        "restoredPartitionCount": 1,
        "activeMaintenanceReservationCount": 1,
    }
    assert requeued_receipt["group"] == "requeued-running"
    assert requeued_receipt["releasedJobCount"] == 1
    assert held_receipt["group"] == "other-held"
    assert held_receipt["releasedJobCount"] == 1


def test_restored_infrastructure_verifier_uses_guarded_migration_partition_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partition_record = SimpleNamespace(
        partition="main",
        previous_record="PartitionName=main State=UP Nodes=worker-[0-1]",
        previous_record_fingerprint="saved-fingerprint",
    )
    live = SimpleNamespace(record="PartitionName=main State=UP Nodes=worker-0-[0-1]")
    monkeypatch.setattr(
        cli,
        "_soperator_flux_apply_owned_scheduling_state",
        lambda _actions: ({}, {"soperator": (partition_record,)}, {}, {}, {}),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_partition_state",
        lambda **_kwargs: live,
    )
    migration_comparisons: list[tuple[object, str, str]] = []

    def _migration_matches(
        observation: object,
        *,
        record: str,
        fingerprint: str,
    ) -> bool:
        migration_comparisons.append((observation, record, fingerprint))
        return True

    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_partition_migration_observation_matches",
        _migration_matches,
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_partition_observation_matches",
        lambda *_args, **_kwargs: pytest.fail(
            "cross-version restore verification used the strict pause comparator"
        ),
    )

    receipt = cli._verify_soperator_flux_apply_infrastructure_restored(
        [],
        extra_env={},
    )

    assert receipt == {"status": "restored", "namespaceCount": 1}
    assert migration_comparisons == [(live, partition_record.previous_record, "saved-fingerprint")]


def test_upgrade_job_gate_resolves_the_live_workload_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "items": [
            {
                "metadata": {
                    "namespace": "soperator",
                    "name": "soperator",
                }
            }
        ]
    }
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_kubectl_cluster",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    assert cli._soperator_upgrade_live_slurmcluster_namespaces(extra_env={}) == ("soperator",)

    payload["items"].append({"metadata": {"namespace": "other", "name": "other-cluster"}})
    with pytest.raises(RuntimeError, match="exactly one live SlurmCluster"):
        cli._soperator_upgrade_live_slurmcluster_namespaces(extra_env={})


def test_scheduling_operation_evidence_excludes_mutable_recovery_actions() -> None:
    journal: dict[str, object] = {
        "schema": cli.SOPERATOR_SLURM_RECOVERY_SCHEMA,
        "targetRef": "cluster-a",
        "command": "soperator upgrade",
        "startedAt": "2026-01-01T00:00:00Z",
        "policy": {"mode": "wait-to-finish"},
        "slurmPreimage": {"receipt_sha256": "sha256:" + "a" * 64},
        "actions": [],
    }
    before = cli._soperator_slurm_operation_evidence(journal)
    journal["actions"] = [
        {
            "action": "scheduling-pause-applied",
            "namespace": "soperator",
        }
    ]

    assert cli._soperator_slurm_operation_evidence(journal) == before
    assert before["actions"] == []


def test_common_gate_holds_only_new_pending_jobs_and_journals_exact_preimages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decisions: list[dict[str, Any]] = []
    held: list[tuple[str, ...]] = []
    reservations: set[str] = set()

    def _job(job_id: str, *, reason: str) -> cli.AffectedSlurmJob:
        return cli.AffectedSlurmJob(
            job_id=job_id,
            user="operator",
            state="PENDING",
            partition="worker",
            allocated_nodes="",
            requested_nodes="worker-0",
            scheduled_nodes="",
            reason=reason,
            elapsed="0:00",
            limit="1:00",
            remaining="1:00",
            name=f"job-{job_id}",
            impact_scope="pending",
        )

    monkeypatch.setattr(
        cli,
        "_soperator_release_refs_for_job_policy",
        lambda *_args, **_kwargs: (SimpleNamespace(namespace="soperator-system"),),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_live_slurmcluster_namespaces",
        lambda **_kwargs: ("soperator",),
    )
    monkeypatch.setattr(cli, "_soperator_upgrade_live_slurmcluster_exists", lambda **_kwargs: True)
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_worker_nodeset_pod_candidates",
        lambda **_kwargs: ("worker-0",),
    )
    monkeypatch.setattr(cli, "_soperator_upgrade_login_path_available", lambda **_kwargs: True)
    monkeypatch.setattr(
        cli, "_soperator_upgrade_slurm_node_filter", lambda **_kwargs: ("worker-0",)
    )
    monkeypatch.setattr(cli, "_handle_soperator_upgrade_running_jobs", lambda **_kwargs: ())
    monkeypatch.setattr(cli, "_soperator_upgrade_pause_slurm_partitions", lambda **_kwargs: ())
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_affected_jobs",
        lambda **_kwargs: (
            _job("10", reason="PartitionDown"),
            _job("11", reason="JobHeldUser"),
        ),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_hold_jobs",
        lambda _namespace, job_ids, **_kwargs: held.append(tuple(job_ids)),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_jobs_by_id",
        lambda **kwargs: tuple(_job(job_id, reason="JobHeldUser") for job_id in kwargs["job_ids"]),
    )
    monkeypatch.setattr(cli, "_soperator_upgrade_drain_slurm_nodes", lambda **_kwargs: ())
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_node_recovery_snapshot",
        lambda **_kwargs: {"worker-0": {"state": "IDLE", "reason": ""}},
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_reservation_names",
        lambda **_kwargs: tuple(sorted(reservations)),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_create_maintenance_reservation",
        lambda **kwargs: reservations.add(kwargs["reservation_name"]),
    )

    cli._soperator_flux_apply_slurm_job_gate(
        {},
        command_name="soperator upgrade",
        target_ref="cluster-a",
        extra_env={},
        job_policy="fail",
        cancel_job_ids=(),
        requeue_job_ids=(),
        job_wait_timeout_seconds=60,
        job_refresh_interval_seconds=5,
        decision_recorder=lambda event: decisions.append(dict(event)),
        mutation_guard=lambda: None,
    )

    assert held == [("10",)]
    assert {event["namespace"] for event in decisions} == {"soperator"}
    assert [event["action"] for event in decisions] == [
        "slurm-gate-started",
        "maintenance-reservation-recorded",
        "maintenance-reservation-applied",
        "pending-hold-recorded",
        "pending-hold-applied",
        "nodes-drain-recorded",
        "nodes-drain-not-required",
        "slurm-gate-complete",
    ]
    assert decisions[3]["jobs"][0]["reason"] == "PartitionDown"
    assert decisions[1]["preexisting_reservations"] == []


def test_resume_recovers_maintenance_reservation_receipt_without_recreating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = [
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "maintenance-reservation-recorded",
            "reservation_name": "cxcli_0123456789abcdef",
            "preexisting_reservations": ["customer-maintenance"],
        }
    ]
    recorded: list[dict[str, Any]] = []
    creates: list[str] = []
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_reservation_names",
        lambda **_kwargs: ("customer-maintenance", "cxcli_0123456789abcdef"),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_create_maintenance_reservation",
        lambda **kwargs: creates.append(kwargs["reservation_name"]),
    )

    cli._resume_soperator_flux_apply_maintenance_reservations(
        actions,
        extra_env={},
        decision_recorder=lambda event: recorded.append(dict(event)),
        assert_authority=lambda: None,
    )

    assert creates == []
    assert recorded[0]["action"] == "maintenance-reservation-applied"
    parsed = cli._soperator_flux_apply_owned_scheduling_state([*actions, *recorded])
    assert parsed[-1] == {"soperator": {"cxcli_0123456789abcdef"}}


def test_resume_recreates_missing_operation_owned_maintenance_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = [
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "maintenance-reservation-recorded",
            "reservation_name": "cxcli_0123456789abcdef",
            "preexisting_reservations": [],
        },
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "maintenance-reservation-applied",
            "reservation_name": "cxcli_0123456789abcdef",
        },
    ]
    live_reservations: set[str] = set()
    recorded: list[dict[str, Any]] = []
    authority_calls: list[str] = []
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_reservation_names",
        lambda **_kwargs: tuple(sorted(live_reservations)),
    )
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_create_maintenance_reservation",
        lambda **kwargs: live_reservations.add(kwargs["reservation_name"]),
    )

    cli._resume_soperator_flux_apply_maintenance_reservations(
        actions,
        extra_env={},
        decision_recorder=lambda event: recorded.append(dict(event)),
        assert_authority=lambda: authority_calls.append("held"),
    )

    assert authority_calls == ["held"]
    assert live_reservations == {"cxcli_0123456789abcdef"}
    assert recorded == [
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "at": recorded[0]["at"],
            "action": "maintenance-reservation-applied",
            "reservation_name": "cxcli_0123456789abcdef",
            "recoveryDisposition": "recovered-applied",
        }
    ]


def test_interrupted_slurm_gate_blocks_target_reconcile_before_mutation() -> None:
    with pytest.raises(RuntimeError, match="completion checkpoint"):
        cli._soperator_flux_apply_owned_scheduling_state(
            [
                {
                    "namespace": "soperator",
                    "checkpoint_id": "0123456789abcdef",
                    "action": "slurm-gate-started",
                    "node_names": ["worker-0"],
                }
            ]
        )


def test_missing_slurm_job_recovery_records_an_exact_external_tombstone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent = cli.normalize_slurm_recovery_event(
        {
            "namespace": "soperator",
            "checkpoint_id": "0123456789abcdef",
            "action": "pending-hold-recorded",
            "job_ids": ["42"],
            "jobs": [
                {
                    "job_id": "42",
                    "user": "alice",
                    "state": "PENDING",
                    "partition": "main",
                    "allocated_nodes": "",
                    "reason": "Resources",
                    "name": "train",
                }
            ],
        },
        fencing_epoch=7,
    )
    recorded: list[Mapping[str, Any]] = []
    monkeypatch.setattr(cli, "_soperator_upgrade_jobs_by_id", lambda **_kwargs: ())

    cli._recover_soperator_slurm_action_intents(
        [intent],
        extra_env={},
        decision_recorder=recorded.append,
        assert_authority=lambda: object(),
    )

    assert recorded[0]["satisfied_external_job_ids"] == ["42"]
    completion = cli.normalize_slurm_recovery_event(
        recorded[0],
        fencing_epoch=7,
        disposition=cli.SlurmRecoveryDisposition(str(recorded[0]["recoveryDisposition"])),
    )
    cli.validate_slurm_recovery_actions([intent, completion])


def test_failed_release_keeps_durable_slurm_recovery_journal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    authority, recovery_env = _slurm_recovery_test_context(monkeypatch)
    monkeypatch.setattr(
        cli, "_soperator_release_refs_for_job_policy", lambda *_args, **_kwargs: (object(),)
    )

    def _gate(*_args: object, **kwargs: object):
        recorder = kwargs["decision_recorder"]
        assert callable(recorder)
        recorder(
            {
                "namespace": "soperator",
                "checkpoint_id": "0123456789abcdef",
                "action": "requeue-hold-selected",
                "job_ids": ["42"],
                "jobs": [{"job_id": "42", "state": "RUNNING"}],
            }
        )
        recorder(
            {
                "namespace": "soperator",
                "checkpoint_id": "0123456789abcdef",
                "action": "requeue-hold-selected-applied",
                "job_ids": ["42"],
                "jobs": [{"job_id": "42"}],
            }
        )
        return ()

    monkeypatch.setattr(cli, "_soperator_flux_apply_slurm_job_gate", _gate)

    def _failed_apply(*_args: object, **kwargs: object) -> None:
        kwargs["before_reconcile_mutations"]()
        assert kwargs["read_controller_spool_migration"]() is None
        kwargs["write_controller_spool_migration"](
            {"schema": "test.controller-spool-migration.v1", "status": "intent"}
        )
        assert kwargs["read_controller_spool_migration"]()["status"] == "intent"
        raise RuntimeError("release failed")

    monkeypatch.setattr(cli, "_apply_rendered_flux", _failed_apply)

    with pytest.raises(RuntimeError, match="release failed"):
        cli._apply_rendered_flux_with_soperator_job_policy(
            {},
            paths,
            command_name="soperator upgrade",
            target_ref="cluster-a",
            extra_env=recovery_env,
            job_policy="requeue-hold-selected",
            cancel_job_ids=(),
            requeue_job_ids=("42",),
            job_wait_timeout_seconds=60,
            job_refresh_interval_seconds=5,
            strategy=cli.resolve_soperator_reconcile_strategy(
                current_release="4.1.6",
                target_release="4.1.7",
                source_contract="upstream-flux-v1",
                target_contract="upstream-flux-v1",
            ),
            infrastructure_plan_sha256="sha256:" + "1" * 64,
            slurm_preimage=cli.build_slurm_upgrade_preimage(
                partitions=(), jobs=(), reservations=()
            ),
            assert_authority=lambda: authority,
            operation_started_at=1.0,
        )

    journal_path = cli._soperator_slurm_action_journal_path(paths, "cluster-a")
    journal = cli.json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["status"] == "recovery-required"
    assert journal["actions"][0]["job_ids"] == ["42"]
    assert journal["controllerSpoolMigration"] == {
        "schema": "test.controller-spool-migration.v1",
        "status": "intent",
    }
    assert journal_path.stat().st_mode & 0o777 == 0o600


def test_failed_release_resumes_same_journal_without_premature_job_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    authority, recovery_env = _slurm_recovery_test_context(monkeypatch)
    gates: list[str] = []
    transitions: list[str] = []
    first_attempt = True
    monkeypatch.setattr(
        cli,
        "_soperator_release_refs_for_job_policy",
        lambda *_args, **_kwargs: (object(),),
    )

    def _gate(*_args: object, **kwargs: object) -> tuple[object, ...]:
        gates.append("gate")
        recorder = kwargs["decision_recorder"]
        recorder(
            {
                "namespace": "soperator",
                "checkpoint_id": "0123456789abcdef",
                "action": "slurm-gate-started",
                "node_names": ["worker-0"],
            }
        )
        recorder(
            {
                "namespace": "soperator",
                "checkpoint_id": "0123456789abcdef",
                "action": "requeue-hold-selected",
                "job_ids": ["42"],
                "jobs": [{"job_id": "42", "state": "RUNNING"}],
            }
        )
        recorder(
            {
                "namespace": "soperator",
                "checkpoint_id": "0123456789abcdef",
                "action": "requeue-hold-selected-applied",
                "job_ids": ["42"],
                "jobs": [{"job_id": "42"}],
            }
        )
        recorder(
            {
                "namespace": "soperator",
                "checkpoint_id": "0123456789abcdef",
                "action": "slurm-gate-complete",
                "node_names": ["worker-0"],
            }
        )
        return ()

    def _apply(*_args: object, **kwargs: object) -> None:
        nonlocal first_attempt
        kwargs["before_reconcile_mutations"]()
        kwargs["bind_operation_spec_sha256"]("sha256:" + "a" * 64)
        kwargs["restore_infrastructure"]()
        if first_attempt:
            first_attempt = False
            raise RuntimeError("interrupted before release")
        kwargs["release_requeued_jobs"]()
        kwargs["release_held_jobs"]()

    monkeypatch.setattr(cli, "_soperator_flux_apply_slurm_job_gate", _gate)
    monkeypatch.setattr(
        cli,
        "_restore_soperator_flux_apply_infrastructure",
        lambda *_args, **_kwargs: transitions.append("restore-infrastructure") or {},
    )
    monkeypatch.setattr(
        cli,
        "_release_soperator_flux_apply_jobs",
        lambda *_args, **kwargs: transitions.append(str(kwargs["group"])) or {},
    )
    monkeypatch.setattr(cli, "_apply_rendered_flux", _apply)

    call_kwargs = {
        "command_name": "soperator upgrade",
        "target_ref": "cluster-a",
        "extra_env": recovery_env,
        "job_policy": "requeue-hold-selected",
        "cancel_job_ids": (),
        "requeue_job_ids": ("42",),
        "job_wait_timeout_seconds": 60,
        "job_refresh_interval_seconds": 5,
        "strategy": cli.resolve_soperator_reconcile_strategy(
            current_release="4.1.6",
            target_release="4.1.7",
            source_contract="upstream-flux-v1",
            target_contract="upstream-flux-v1",
        ),
        "infrastructure_plan_sha256": "sha256:" + "1" * 64,
        "slurm_preimage": cli.build_slurm_upgrade_preimage(partitions=(), jobs=(), reservations=()),
        "assert_authority": lambda: authority,
        "operation_started_at": 1.0,
    }
    with pytest.raises(RuntimeError, match="interrupted before release"):
        cli._apply_rendered_flux_with_soperator_job_policy({}, paths, **call_kwargs)

    assert transitions == ["restore-infrastructure"]
    monkeypatch.setattr(
        cli,
        "_recover_soperator_slurm_action_intents",
        lambda *_args, **_kwargs: pytest.fail("completed gate replayed action intents"),
    )
    monkeypatch.setattr(
        cli,
        "_resume_soperator_flux_apply_maintenance_reservations",
        lambda *_args, **_kwargs: pytest.fail("completed gate replayed reservations"),
    )
    monkeypatch.setattr(
        cli,
        "_resume_interrupted_soperator_slurm_gates",
        lambda *_args, **_kwargs: pytest.fail("completed gate replayed live Slurm state"),
    )
    cli._apply_rendered_flux_with_soperator_job_policy({}, paths, **call_kwargs)

    assert gates == ["gate"]
    assert transitions == [
        "restore-infrastructure",
        "requeued-running",
        "other-held",
    ]
    journal = cli.json.loads(
        cli._soperator_slurm_action_journal_path(paths, "cluster-a").read_text(encoding="utf-8")
    )
    assert journal["status"] == "restored"


def test_interrupted_one_shot_callbacks_recover_then_verify_live_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    authority, recovery_env = _slurm_recovery_test_context(monkeypatch)
    recovered: list[str] = []
    verification_attempts = {
        "infrastructure": 0,
        "requeued-running": 0,
        "other-held": 0,
    }
    monkeypatch.setattr(
        cli,
        "_soperator_release_refs_for_job_policy",
        lambda *_args, **_kwargs: (object(),),
    )
    monkeypatch.setattr(cli, "_soperator_flux_apply_slurm_job_gate", lambda *_a, **_k: ())
    monkeypatch.setattr(
        cli,
        "_restore_soperator_flux_apply_infrastructure",
        lambda *_args, **_kwargs: recovered.append("infrastructure") or {},
    )
    monkeypatch.setattr(
        cli,
        "_release_soperator_flux_apply_jobs",
        lambda *_args, **kwargs: recovered.append(str(kwargs["group"])) or {},
    )

    def _verify_infrastructure(*_args: object, **_kwargs: object) -> dict[str, str]:
        verification_attempts["infrastructure"] += 1
        if verification_attempts["infrastructure"] == 1:
            raise RuntimeError("restore incomplete")
        return {"status": "restored"}

    def _verify_jobs(*_args: object, **kwargs: object) -> dict[str, str]:
        group = str(kwargs["group"])
        verification_attempts[group] += 1
        if verification_attempts[group] == 1:
            raise RuntimeError("release incomplete")
        return {"status": "released", "group": group}

    monkeypatch.setattr(
        cli,
        "_verify_soperator_flux_apply_infrastructure_restored",
        _verify_infrastructure,
    )
    monkeypatch.setattr(cli, "_verify_soperator_flux_apply_jobs_released", _verify_jobs)

    def _apply(*_args: object, **kwargs: object) -> None:
        kwargs["before_reconcile_mutations"]()
        kwargs["bind_operation_spec_sha256"]("sha256:" + "a" * 64)
        kwargs["recover_restored_infrastructure"]()
        kwargs["recover_requeued_jobs"]()
        kwargs["recover_held_jobs"]()

    monkeypatch.setattr(cli, "_apply_rendered_flux", _apply)

    cli._apply_rendered_flux_with_soperator_job_policy(
        {},
        paths,
        command_name="soperator upgrade",
        target_ref="cluster-a",
        extra_env=recovery_env,
        job_policy="fail",
        cancel_job_ids=(),
        requeue_job_ids=(),
        job_wait_timeout_seconds=60,
        job_refresh_interval_seconds=5,
        strategy=cli.resolve_soperator_reconcile_strategy(
            current_release="4.1.6",
            target_release="4.1.7",
            source_contract="upstream-flux-v1",
            target_contract="upstream-flux-v1",
        ),
        infrastructure_plan_sha256="sha256:" + "1" * 64,
        slurm_preimage=cli.build_slurm_upgrade_preimage(partitions=(), jobs=(), reservations=()),
        assert_authority=lambda: authority,
        operation_started_at=1.0,
    )

    assert recovered == ["infrastructure", "requeued-running", "other-held"]
    assert verification_attempts == {
        "infrastructure": 2,
        "requeued-running": 2,
        "other-held": 2,
    }
