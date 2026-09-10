"""Closed upstream dependency repairs for a fenced, interrupted first install."""

from __future__ import annotations

import copy
import json
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from .paths import ProjectPaths
from .project_bundle_transaction import ProjectBundleTransaction
from .soperator_adapter import _MOUNT_GATE_SCRIPT, _REST_JWT_CONFIG_GATE_SCRIPT
from .soperator_checks_binding import CHECKS_RELEASE, bind_checks_jail, checks_post_renderers
from .soperator_checks_login import bind_checks_login
from .soperator_flux_graph import _source_name
from .soperator_install_render_repair import (
    CHECKS_REPAIR_REASON,
    COLLECTOR_REPAIR_REASON,
    LOGIN_REPAIR_REASON,
    OUTER_FILE,
    REST_REPAIR_REASON,
    VALUES_FILE,
    _bind_repair_admission,
    _digest,
    _documents,
    _file_hashes,
    _files,
    _kube_get,
    prepare_install_dashboard_repair,
)
from .soperator_jail_logs_binding import JAIL_LOGS_RELEASE, jail_logs_binding_operations
from .soperator_receipt_io import read_owner_only_json
from .soperator_release import load_soperator_release_snapshot, soperator_release_snapshot_path
from .soperator_release_resolver import frozen_soperator_release_from_snapshot
from .soperator_rest_contract import materialize_soperator_rest


def checks_repair_candidate(previous: Mapping[str, bytes]) -> dict[str, bytes]:
    """Only bind checks to the jail already approved for the main workload."""
    cm = _documents(previous[VALUES_FILE])
    outer = _documents(previous[OUTER_FILE])
    if len(cm) != 1 or len(outer) != 1:
        raise RuntimeError("Checks repair requires unambiguous compiled inputs")
    values = yaml.safe_load(cm[0]["data"]["values.yaml"])
    if outer[0]["spec"]["values"] != values:
        raise RuntimeError("Checks repair requires matching umbrella values")
    sources = values["slurmCluster"]["overrideValues"]["volumeSources"]
    jail = [row for row in sources if row.get("name") == "jail"]
    if len(jail) != 1:
        raise RuntimeError("Checks repair requires one approved main jail claim")
    pvc = str(jail[0]["persistentVolumeClaim"]["claimName"])
    active = values["soperatorActiveChecks"]
    if active.get("enabled") is not True:
        raise RuntimeError("Checks repair cannot change disabled check policy")
    old = active.get("overrideValues") or {}
    if "jobContainer" in old:
        raise RuntimeError("Checks repair requires the proven missing global jail binding")
    active["overrideValues"] = bind_checks_jail(old, pvc)
    patches = outer[0]["spec"]["postRenderers"][0]["kustomize"]["patches"]
    rows = [row for row in patches if row.get("target", {}).get("name") == CHECKS_RELEASE]
    if len(rows) != 1:
        raise RuntimeError("Checks repair requires one upstream checks child")
    operations = yaml.safe_load(rows[0]["patch"])
    if any(op.get("path") == "/spec/postRenderers" for op in operations):
        raise RuntimeError("Checks repair cannot overwrite an existing child postrenderer")
    operations.append(
        {"op": "add", "path": "/spec/postRenderers", "value": checks_post_renderers(pvc)}
    )
    rows[0]["patch"] = yaml.safe_dump(operations, sort_keys=False)
    outer[0]["spec"]["values"] = copy.deepcopy(values)
    cm[0]["data"]["values.yaml"] = yaml.safe_dump(values, sort_keys=False)
    candidate = dict(previous)
    candidate[VALUES_FILE] = yaml.safe_dump_all(cm, sort_keys=False).encode()
    candidate[OUTER_FILE] = yaml.safe_dump_all(outer, sort_keys=False).encode()
    return candidate


def rest_repair_candidate(previous: Mapping[str, bytes]) -> dict[str, bytes]:
    """Enable only the omitted REST dependency and its established JWT gate."""
    cm = _documents(previous[VALUES_FILE])
    outer = _documents(previous[OUTER_FILE])
    if len(cm) != 1 or len(outer) != 1:
        raise RuntimeError("REST repair requires unambiguous compiled inputs")
    values = yaml.safe_load(cm[0]["data"]["values.yaml"])
    if outer[0]["spec"]["values"] != values:
        raise RuntimeError("REST repair requires matching umbrella values")
    cluster = values["slurmCluster"]["overrideValues"]
    nodes = cluster["slurmNodes"]
    if "enabled" in nodes.get("rest", {}):
        raise RuntimeError("REST repair requires the proven omitted dependency flag")
    gates = [
        row
        for row in nodes["controller"]["customInitContainers"]
        if row.get("name") == "mount-gate-controller-jail"
    ]
    if len(gates) != 1 or gates[0].get("command") != ["/bin/sh", "-ec", _MOUNT_GATE_SCRIPT]:
        raise RuntimeError("REST repair requires the exact existing controller mount gate")
    materialize_soperator_rest(cluster)
    gates[0]["command"][-1] += _REST_JWT_CONFIG_GATE_SCRIPT
    outer[0]["spec"]["values"] = copy.deepcopy(values)
    cm[0]["data"]["values.yaml"] = yaml.safe_dump(values, sort_keys=False)
    candidate = dict(previous)
    candidate[VALUES_FILE] = yaml.safe_dump_all(cm, sort_keys=False).encode()
    candidate[OUTER_FILE] = yaml.safe_dump_all(outer, sort_keys=False).encode()
    return candidate


def login_repair_candidate(previous: Mapping[str, bytes], source_dir: Path) -> dict[str, bytes]:
    """Bind only native login commands, retaining every other compiled input."""
    cm = _documents(previous[VALUES_FILE])
    outer = _documents(previous[OUTER_FILE])
    if len(cm) != 1 or len(outer) != 1:
        raise RuntimeError("Login repair requires unambiguous compiled inputs")
    values = yaml.safe_load(cm[0]["data"]["values.yaml"])
    if outer[0]["spec"]["values"] != values:
        raise RuntimeError("Login repair requires matching umbrella values")
    bound = bind_checks_login(values, source_dir)
    if bound == values:
        raise RuntimeError("Login repair requires an absent native hostname binding")
    outer[0]["spec"]["values"] = bound
    cm[0]["data"]["values.yaml"] = yaml.safe_dump(bound, sort_keys=False)
    return {
        **previous,
        VALUES_FILE: yaml.safe_dump_all(cm, sort_keys=False).encode(),
        OUTER_FILE: yaml.safe_dump_all(outer, sort_keys=False).encode(),
    }


def collector_repair_candidate(previous: Mapping[str, bytes]) -> dict[str, bytes]:
    """Bind only the existing jail collector child; keep all compiled inputs."""
    outer = _documents(previous[OUTER_FILE])
    cm = _documents(previous[VALUES_FILE])
    if len(outer) != 1 or len(cm) != 1:
        raise RuntimeError("Collector repair requires unambiguous compiled inputs")
    values = yaml.safe_load(cm[0]["data"]["values.yaml"])
    if outer[0]["spec"]["values"] != values:
        raise RuntimeError("Collector repair requires matching umbrella values")
    patches = outer[0]["spec"]["postRenderers"][0]["kustomize"]["patches"]
    targets = [x for x in patches if x.get("target", {}).get("name") == JAIL_LOGS_RELEASE]
    if not targets:
        return dict(previous)
    if len(targets) != 1:
        raise RuntimeError("Collector repair requires one exact jail collector patch")
    operations = jail_logs_binding_operations(
        values, _documents(previous["soperator-nebius-adapter.yaml"])
    )
    existing = yaml.safe_load(targets[0]["patch"])
    if existing[-len(operations) :] == operations:
        return dict(previous)
    if any(
        str(x.get("path", "")).startswith(
            (
                "/spec/values/affinity",
                "/spec/values/extraVolumes",
                "/spec/values/tolerations",
                "/spec/values/nodeSelector",
            )
        )
        for x in existing
    ):
        raise RuntimeError("Collector repair cannot replace a custom collector binding")
    targets[0]["patch"] = yaml.safe_dump([*existing, *operations], sort_keys=False)
    return {**previous, OUTER_FILE: yaml.safe_dump_all(outer, sort_keys=False).encode()}


def prepare_install_input_repair(
    *, slurm: Callable[[str], str] | None = None, **kwargs: Any
) -> Mapping[str, Any] | None:
    from .soperator_install_cpu_mask_repair import prepare_install_cpu_mask_repair
    from .soperator_install_docker_repair import prepare_install_docker_repair
    from .soperator_install_gpu_repair import prepare_install_gpu_maintenance_repair
    from .soperator_install_runtime_repair import prepare_install_runtime_repair
    from .soperator_install_storage_repair import prepare_install_storage_repair
    from .soperator_install_topology_repair import prepare_install_topology_repair
    from .soperator_install_userns_repair import prepare_install_userns_repair

    paths = kwargs["paths"]
    target_ref = kwargs["target_ref"]
    if (paths.reports_dir / f"soperator-install-cpu-mask-repair-{target_ref}.json").exists():
        return prepare_install_cpu_mask_repair(**kwargs, slurm=slurm)
    if (paths.reports_dir / f"soperator-install-topology-repair-{target_ref}.json").exists():
        ancestor = prepare_install_topology_repair(**kwargs, slurm=slurm)
        return prepare_install_cpu_mask_repair(**kwargs, slurm=slurm, ancestor=ancestor)
    if (paths.reports_dir / f"soperator-install-docker-repair-{target_ref}.json").exists():
        ancestor = prepare_install_docker_repair(**kwargs, slurm=slurm)
        return prepare_install_topology_repair(**kwargs, slurm=slurm, ancestor=ancestor)
    if (paths.reports_dir / f"soperator-install-userns-repair-{target_ref}.json").exists():
        ancestor = prepare_install_userns_repair(**kwargs, slurm=slurm)
        return prepare_install_docker_repair(**kwargs, slurm=slurm, ancestor=ancestor)
    if (paths.reports_dir / f"soperator-install-storage-repair-{target_ref}.json").exists():
        ancestor = prepare_install_storage_repair(**kwargs, slurm=slurm)
        return prepare_install_userns_repair(**kwargs, slurm=slurm, ancestor=ancestor)
    if (paths.reports_dir / f"soperator-install-runtime-repair-{target_ref}.json").exists():
        ancestor = prepare_install_runtime_repair(**kwargs, slurm=slurm)
        return prepare_install_storage_repair(**kwargs, slurm=slurm, ancestor=ancestor)
    gpu_path = paths.reports_dir / f"soperator-install-gpu-maintenance-repair-{target_ref}.json"
    if gpu_path.exists():
        ancestor = prepare_install_gpu_maintenance_repair(**kwargs, slurm=slurm)
        return prepare_install_runtime_repair(**kwargs, slurm=slurm, ancestor=ancestor)
    if (paths.reports_dir / f"soperator-install-collector-repair-{target_ref}.json").exists():
        ancestor = _prepare_install_binding_repair(**kwargs, reason=COLLECTOR_REPAIR_REASON)
        return prepare_install_gpu_maintenance_repair(**kwargs, slurm=slurm, ancestor=ancestor)
    ancestor = _prepare_checks_input_repair(**kwargs)
    if ancestor is not None and ancestor.get("previousOperationSpecSha256") == kwargs[
        "scheduling_journal"
    ].get("operationSpecSha256"):
        return ancestor
    return _prepare_install_binding_repair(
        **kwargs, reason=COLLECTOR_REPAIR_REASON, ancestor=ancestor
    )


def _prepare_checks_input_repair(**kwargs: Any) -> Mapping[str, Any] | None:
    """Resume the latest sealed input repair or admit the next closed delta."""
    paths = kwargs["paths"]
    target_ref = kwargs["target_ref"]
    login_path = paths.reports_dir / f"soperator-install-login-repair-{target_ref}.json"
    if login_path.exists():
        return _prepare_install_binding_repair(**kwargs, reason=LOGIN_REPAIR_REASON)
    rest_path = paths.reports_dir / f"soperator-install-rest-repair-{target_ref}.json"
    if rest_path.exists():
        ancestor = _prepare_install_binding_repair(**kwargs, reason=REST_REPAIR_REASON)
    else:
        ancestor = _prepare_install_binding_repair(**kwargs)
        if ancestor is not None and ancestor.get("previousOperationSpecSha256") == kwargs[
            "scheduling_journal"
        ].get("operationSpecSha256"):
            return ancestor
        ancestor = _prepare_install_binding_repair(
            **kwargs, reason=REST_REPAIR_REASON, ancestor=ancestor
        )
    if ancestor is not None and ancestor.get("previousOperationSpecSha256") == kwargs[
        "scheduling_journal"
    ].get("operationSpecSha256"):
        return ancestor
    return _prepare_install_binding_repair(**kwargs, reason=LOGIN_REPAIR_REASON, ancestor=ancestor)


def _verify_ancestor_seals(
    repair: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    kube_context: str,
    assert_authority: Callable[[], object],
) -> None:
    ancestor = repair.get("ancestorRepair")
    if ancestor is not None:
        if not isinstance(ancestor, Mapping) or ancestor.get("replacementFiles") != repair.get(
            "previousFiles"
        ):
            raise RuntimeError("Install input repair lost its exact ancestry")
        _verify_ancestor_seals(
            ancestor, env=env, kube_context=kube_context, assert_authority=assert_authority
        )
        _bind_repair_admission(
            ancestor,
            env=env,
            kube_context=kube_context,
            assert_authority=assert_authority,
            create=False,
        )


def _hook_contract(job: Mapping[str, Any]) -> dict[str, Any]:
    spec = job["spec"]["template"]["spec"]
    if spec.get("initContainers") or len(spec.get("containers", [])) != 1:
        raise RuntimeError("Checks wait hook has an unexpected executable container")
    container = spec["containers"][0]
    return {
        "serviceAccountName": spec.get("serviceAccountName"),
        "restartPolicy": spec.get("restartPolicy"),
        "container": {
            key: container.get(key)
            for key in ("name", "image", "command", "args", "env", "envFrom")
        },
    }


def validate_wait_hook(live: Mapping[str, Any], expected: Mapping[str, Any]) -> dict[str, Any]:
    metadata = live.get("metadata", {})
    if (
        metadata.get("name") != "wait-for-active-checks"
        or metadata.get("namespace") != "soperator"
        or not metadata.get("uid")
        or metadata.get("deletionTimestamp")
        or metadata.get("annotations", {}).get("helm.sh/hook") != "post-install,post-upgrade"
        or _hook_contract(live) != _hook_contract(expected)
        or live.get("status", {}).get("succeeded")
    ):
        raise RuntimeError("Checks repair cannot prove the exact upstream read-only wait hook")
    return {
        "name": metadata["name"],
        "namespace": metadata["namespace"],
        "uid": metadata["uid"],
        "contractSha256": _digest(_hook_contract(live)),
    }


def _validate_install_apply_frontier(predecessor: Mapping[str, Any]) -> None:
    """Admit only the interrupted or failed form of the same initial apply."""
    transitions = predecessor.get("transitions")
    final_status = {"running": "running", "recovery-required": "failed"}.get(
        predecessor.get("status", "")
    )
    if (
        final_status is None
        or not isinstance(transitions, list)
        or len(transitions) != 3
        or any(not isinstance(row, Mapping) for row in transitions)
        or [(row.get("phase"), row.get("status")) for row in transitions]
        != [
            ("resolve-immutable-sources", "complete"),
            ("establish-boot-storage-barrier", "complete"),
            ("apply-declarative-release", final_status),
        ]
    ):
        raise RuntimeError("Checks repair is outside the interrupted install apply frontier")
    final = transitions[-1]
    if (
        not final.get("id")
        or final.get("receiptSha256") is not None
        or predecessor.get("irreversibleFrontier") is not None
        or predecessor.get("irreversibleIntent")
        != {
            "transitionId": final["id"],
            "phase": "apply-declarative-release",
            "disposition": "pending-forward-only",
        }
        or (
            final_status == "failed"
            and (
                final.get("failureType") != "operation-error"
                or type(final.get("failureAttempts")) is not int
                or final["failureAttempts"] < 1
            )
        )
    ):
        raise RuntimeError("Checks repair lost its incomplete install apply evidence")


def _prepare_install_binding_repair(
    *,
    paths: ProjectPaths,
    target_ref: str,
    scheduling_journal: Mapping[str, Any],
    local_scheduling_journal: Mapping[str, Any] | None,
    env: Mapping[str, str],
    kube_context: str,
    assert_authority: Callable[[], object],
    reason: str = CHECKS_REPAIR_REASON,
    ancestor: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    """Authenticate a repair chain and atomically publish only its closed delta."""
    kind = {
        CHECKS_REPAIR_REASON: "checks",
        REST_REPAIR_REASON: "rest",
        LOGIN_REPAIR_REASON: "login",
        COLLECTOR_REPAIR_REASON: "collector",
    }[reason]
    repair_path = paths.reports_dir / f"soperator-install-{kind}-repair-{target_ref}.json"
    existing = _files(paths.flux_dir)
    if repair_path.exists():
        saved = read_owner_only_json(repair_path, label="Soperator checks install repair")
        if (
            not isinstance(saved, Mapping)
            or saved.get("schema") != reason
            or saved.get("targetRef") != target_ref
            or saved.get("replacementFiles") != _file_hashes(existing)
        ):
            raise RuntimeError("Saved checks repair lost its exact generated authority")
        _verify_ancestor_seals(
            saved, env=env, kube_context=kube_context, assert_authority=assert_authority
        )
        _bind_repair_admission(
            saved,
            env=env,
            kube_context=kube_context,
            assert_authority=assert_authority,
            create=False,
        )
        return saved
    if reason == CHECKS_REPAIR_REASON:
        ancestor = prepare_install_dashboard_repair(
            paths=paths,
            target_ref=target_ref,
            scheduling_journal=scheduling_journal,
            local_scheduling_journal=local_scheduling_journal,
            env=env,
            kube_context=kube_context,
            assert_authority=assert_authority,
        )
    if ancestor is not None and ancestor.get(
        "previousOperationSpecSha256"
    ) == scheduling_journal.get("operationSpecSha256"):
        # Finish an admitted pre-main repair before admitting another generation.
        return ancestor
    existing = _files(paths.flux_dir)
    values = yaml.safe_load(_documents(existing[VALUES_FILE])[0]["data"]["values.yaml"])
    if reason == CHECKS_REPAIR_REASON:
        if "jobContainer" in (values.get("soperatorActiveChecks", {}).get("overrideValues") or {}):
            return ancestor
    elif reason == REST_REPAIR_REASON and (
        values["slurmCluster"]["overrideValues"]["slurmNodes"].get("rest", {}).get("enabled")
        is True
    ):
        return ancestor
    elif reason == LOGIN_REPAIR_REASON:
        login_snapshot = load_soperator_release_snapshot(
            soperator_release_snapshot_path(paths.reports_dir, target_ref)
        )
        login_source = frozen_soperator_release_from_snapshot(login_snapshot)
        if bind_checks_login(values, Path(login_source.source.source_dir)) == values:
            return ancestor
    elif reason == COLLECTOR_REPAIR_REASON and collector_repair_candidate(existing) == existing:
        return ancestor
    if (
        scheduling_journal != local_scheduling_journal
        or scheduling_journal.get("lastCompletedStage") != "gated"
        or scheduling_journal.get("actions") != []
    ):
        raise RuntimeError("Checks repair requires the unchanged scheduling gate")
    previous_sha = str(scheduling_journal.get("operationSpecSha256") or "")
    raw_predecessors = [
        read_owner_only_json(path, label="Soperator checks predecessor")
        for path in paths.reports_dir.glob("soperator-release-reconcile-*.json")
    ]
    predecessors: list[Mapping[str, Any]] = [
        row
        for row in raw_predecessors
        if isinstance(row, Mapping)
        and _digest(row.get("operation", {}).get("spec")) == previous_sha
    ]
    if len(predecessors) != 1:
        raise RuntimeError("Checks repair requires one exact reconcile predecessor")
    predecessor = predecessors[0]
    _validate_install_apply_frontier(predecessor)
    spec = predecessor["operation"]["spec"]
    snapshot = load_soperator_release_snapshot(
        soperator_release_snapshot_path(paths.reports_dir, target_ref)
    )
    if (
        spec.get("strategy") != "install"
        or spec.get("current_release") != ""
        or spec.get("target_release") != snapshot.release
        or spec.get("target_ref") != target_ref
        or spec.get("desired_values_sha256") != _file_hashes(existing)[VALUES_FILE]
        or spec.get("adapter_sha256") != _file_hashes(existing)["soperator-nebius-adapter.yaml"]
    ):
        raise RuntimeError("Checks repair is outside the interrupted install apply frontier")
    checks_receipt = (
        paths.reports_dir / f"soperator-checks-{previous_sha.removeprefix('sha256:')[:24]}.json"
    )
    check_state = read_owner_only_json(checks_receipt, label="Soperator checks predecessor state")
    if (
        not isinstance(check_state, Mapping)
        or check_state.get("phase") != "planned"
        or check_state.get("jobs") != {}
        or check_state.get("acceptance")
        or check_state.get("reservation")
    ):
        raise RuntimeError("Checks repair cannot run after operation acceptance started")
    child_name = JAIL_LOGS_RELEASE if reason == COLLECTOR_REPAIR_REASON else CHECKS_RELEASE
    child = _kube_get(
        ["helmrelease", "cxcli-" + child_name, "-n", "flux-system"],
        env=env,
        kube_context=kube_context,
    )
    chart = snapshot.charts["activeChecks"]
    if (
        child is None
        or child.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/version")
        != snapshot.release
        or child.get("metadata", {}).get("labels", {}).get("soperator.nebius.ai/release-graph")
        != "nebius-cxcli"
        or (
            reason != COLLECTOR_REPAIR_REASON
            and (
                child.get("spec", {}).get("chartRef", {}).get("name")
                != "soperator-upstream-activechecks"
                or child.get("status", {}).get("lastAttemptedRevisionDigest") != chart.digest
            )
        )
        or any(
            row.get("status") in {"deployed", "superseded"}
            for row in child.get("status", {}).get("history") or []
        )
    ):
        raise RuntimeError("Input repair requires the exact never-successful child installation")
    child_evidence = {
        "namespace": "flux-system",
        "name": "cxcli-" + child_name,
        "uid": child["metadata"]["uid"],
        "sourceDigest": chart.digest,
    }
    if reason == COLLECTOR_REPAIR_REASON:
        graph = next(x for x in snapshot.release_graph if x.release_name == JAIL_LOGS_RELEASE)
        locked = snapshot.third_party_charts[graph.chart_key]
        source_name = _source_name(graph)
        reference = {"kind": "HelmChart", "name": source_name, "namespace": "flux-system"}
        source = _kube_get(
            ["helmchart", source_name, "-n", "flux-system"], env=env, kube_context=kube_context
        )
        digest = "sha256:" + locked.package_sha256.removeprefix("sha256:")
        config_digest = child.get("status", {}).get("lastAttemptedConfigDigest")
        if (
            child["spec"].get("chartRef") != reference
            or child.get("status", {}).get("lastAttemptedRevision") != locked.version
            or not isinstance(config_digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", config_digest) is None
            or source is None
            or source.get("spec", {}).get("chart") != locked.chart
            or source.get("spec", {}).get("version") != locked.version
            or source.get("status", {}).get("artifact", {}).get("digest") != digest
            or not source.get("metadata", {}).get("uid")
        ):
            raise RuntimeError("Collector repair lost the exact frozen HelmChart artifact")
        child_evidence.update(
            {
                "sourceKind": "HelmChart",
                "sourceName": source_name,
                "sourceNamespace": "flux-system",
                "sourceUid": source["metadata"]["uid"],
                "sourceVersion": locked.version,
                "sourceChart": locked.chart,
                "sourceDigest": digest,
                "sourceConfigDigest": config_digest,
            }
        )
    cluster_name = values["slurmCluster"]["overrideValues"]["clusterName"]
    cluster = _kube_get(
        ["slurmcluster", cluster_name, "-n", "soperator"], env=env, kube_context=kube_context
    )
    desired_jail = [
        row["persistentVolumeClaim"]
        for row in values["slurmCluster"]["overrideValues"]["volumeSources"]
        if row.get("name") == "jail"
    ]
    live_jail = [
        row.get("persistentVolumeClaim")
        for row in (cluster or {}).get("spec", {}).get("volumeSources", [])
        if row.get("name") == "jail"
    ]
    if cluster is None or len(desired_jail) != 1 or live_jail != desired_jail:
        raise RuntimeError("Checks repair lost the approved live jail storage binding")
    if reason == REST_REPAIR_REASON:
        if (
            cluster.get("spec", {}).get("slurmNodes", {}).get("rest", {}).get("enabled")
            is not False
        ):
            raise RuntimeError("REST repair requires the proven disabled live dependency")
        service = _kube_get(
            ["service", cluster_name + "-rest-svc", "-n", "soperator"],
            env=env,
            kube_context=kube_context,
            optional=True,
        )
        if service is not None:
            raise RuntimeError("REST repair cannot replace an existing service")
    frozen = frozen_soperator_release_from_snapshot(snapshot)
    if reason == COLLECTOR_REPAIR_REASON:
        candidate = collector_repair_candidate(existing)
        return _publish_binding_repair(
            paths=paths,
            target_ref=target_ref,
            scheduling_journal=scheduling_journal,
            existing=existing,
            candidate=candidate,
            predecessor=predecessor,
            ancestor=ancestor,
            reason=reason,
            repair_path=repair_path,
            child_key="collectorRelease",
            child_evidence=child_evidence,
            hook_evidence=None,
            env=env,
            kube_context=kube_context,
            assert_authority=assert_authority,
        )
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "soperator-activechecks",
            str(Path(frozen.source.source_dir) / "helm/soperator-activechecks"),
            "--namespace",
            "soperator",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    expected = [
        row
        for row in yaml.safe_load_all(rendered.stdout)
        if isinstance(row, Mapping)
        and row.get("kind") == "Job"
        and row.get("metadata", {}).get("name") == "wait-for-active-checks"
    ]
    if len(expected) != 1:
        raise RuntimeError("Checks repair has no verified upstream wait hook")
    hook = _kube_get(
        ["job", "wait-for-active-checks", "-n", "soperator"],
        env=env,
        kube_context=kube_context,
        optional=True,
    )
    hook_evidence = validate_wait_hook(hook, expected[0]) if hook is not None else None
    if reason == LOGIN_REPAIR_REASON:
        candidate = login_repair_candidate(existing, Path(frozen.source.source_dir))
    else:
        candidate = (
            checks_repair_candidate if reason == CHECKS_REPAIR_REASON else rest_repair_candidate
        )(existing)
    return _publish_binding_repair(
        paths=paths,
        target_ref=target_ref,
        scheduling_journal=scheduling_journal,
        existing=existing,
        candidate=candidate,
        predecessor=predecessor,
        ancestor=ancestor,
        reason=reason,
        repair_path=repair_path,
        child_key="checksRelease",
        child_evidence=child_evidence,
        hook_evidence=hook_evidence,
        env=env,
        kube_context=kube_context,
        assert_authority=assert_authority,
    )


def _publish_binding_repair(
    *,
    paths: ProjectPaths,
    target_ref: str,
    scheduling_journal: Mapping[str, Any],
    existing: Mapping[str, bytes],
    candidate: Mapping[str, bytes],
    predecessor: Mapping[str, Any],
    ancestor: Mapping[str, Any] | None,
    reason: str,
    repair_path: Path,
    child_key: str,
    child_evidence: Mapping[str, Any],
    hook_evidence: Mapping[str, Any] | None,
    env: Mapping[str, str],
    kube_context: str,
    assert_authority: Callable[[], object],
    additional_evidence: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    spec = predecessor["operation"]["spec"]
    repair = {
        "schema": reason,
        "targetRef": target_ref,
        "previousOperationSpecSha256": scheduling_journal["operationSpecSha256"],
        "interventionGeneration": spec["intervention_generation"] + 1,
        "predecessorReceipt": predecessor,
        "predecessorReceiptSha256": _digest(predecessor),
        "ancestorRepair": ancestor,
        "previousFiles": _file_hashes(existing),
        "replacementFiles": _file_hashes(candidate),
        child_key: dict(child_evidence),
        "waitHook": hook_evidence,
    }
    if additional_evidence:
        if repair.keys() & additional_evidence.keys():
            raise RuntimeError("Install repair evidence cannot replace its identity")
        repair.update(additional_evidence)
    transaction = ProjectBundleTransaction(paths.project_dir)
    updates = {paths.flux_dir / name: content for name, content in candidate.items()}
    updates[repair_path] = (json.dumps(repair, sort_keys=True, indent=2) + "\n").encode()
    preimages = transaction.snapshot_preimages(updates)
    if preimages[repair_path].content is not None or any(
        preimages[paths.flux_dir / name].content != content for name, content in existing.items()
    ):
        raise RuntimeError("Checks repair inputs changed during admission")
    _bind_repair_admission(
        repair, env=env, kube_context=kube_context, assert_authority=assert_authority, create=True
    )
    assert_authority()
    transaction.commit(
        updates, expected_preimages={path: image.sha256 for path, image in preimages.items()}
    )
    return repair


def terminate_admitted_wait_hook(
    repair: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    kube_context: str,
    assert_authority: Callable[[], object],
) -> None:
    """Fail only the authenticated old read-only hook after its writers suspend."""
    evidence = repair.get("waitHook")
    if not isinstance(evidence, Mapping):
        return
    child = repair["checksRelease"]
    release = _kube_get(
        ["helmrelease", child["name"], "-n", child["namespace"]], env=env, kube_context=kube_context
    )
    if (
        release is None
        or release["metadata"].get("uid") != child["uid"]
        or release.get("spec", {}).get("suspend") is not True
        or release.get("status", {}).get("lastAttemptedRevisionDigest") != child["sourceDigest"]
    ):
        raise RuntimeError("Checks hook recovery lost its suspended release writer")
    job = _kube_get(
        ["job", evidence["name"], "-n", evidence["namespace"]],
        env=env,
        kube_context=kube_context,
        optional=True,
    )
    if job is None:
        return
    if job["metadata"].get("uid") != evidence["uid"]:
        # A later native retry owns its own hook; never terminate it using old evidence.
        return
    if _digest(_hook_contract(job)) != evidence["contractSha256"]:
        raise RuntimeError("Admitted checks wait hook execution changed")
    if (
        any(
            row.get("type") in {"Complete", "Failed"} and row.get("status") == "True"
            for row in job.get("status", {}).get("conditions", [])
        )
        or job.get("spec", {}).get("activeDeadlineSeconds") == 1
    ):
        return
    patch = [
        {"op": "test", "path": "/metadata/uid", "value": evidence["uid"]},
        {
            "op": "test",
            "path": "/metadata/resourceVersion",
            "value": job["metadata"]["resourceVersion"],
        },
        {"op": "add", "path": "/spec/activeDeadlineSeconds", "value": 1},
    ]
    assert_authority()
    result = subprocess.run(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            evidence["namespace"],
            "patch",
            "job",
            evidence["name"],
            "--type=json",
            "-p",
            json.dumps(patch),
        ],
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode:
        raise RuntimeError("Could not end the admitted failed-install wait hook")
    confirmed = _kube_get(
        ["job", evidence["name"], "-n", evidence["namespace"]],
        env=env,
        kube_context=kube_context,
        optional=True,
    )
    if (
        confirmed is not None
        and confirmed["metadata"].get("uid") == evidence["uid"]
        and confirmed.get("spec", {}).get("activeDeadlineSeconds") != 1
    ):
        raise RuntimeError("Admitted wait hook deadline did not persist")
