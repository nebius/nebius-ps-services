"""Project action-critical source inputs from refreshed Terraform and live values."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any

import yaml

from .config_model import to_dynamic_payload
from .deployment_plan import soperator_target


def _present(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _present(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_present(item) for item in value]
    return value


def observed_node_group(values: Mapping[str, Any], resources: Sequence[Mapping]) -> dict[str, Any]:
    """Reverse the canonical MK8s module's input projection, preserving zero capacity."""
    template = values.get("template") or {}
    shape = template.get("resources") or {}
    group = {
        key: _present(values[key])
        for key in ("name", "parent_id", "version", "labels", "auto_repair", "strategy")
        if values.get(key) is not None
    }
    for key in ("os", "boot_disk", "taints", "local_disks", "filesystems", "network_interfaces"):
        if template.get(key) is not None:
            group[key] = _present(template[key])
    if values.get("fixed_node_count") is not None:
        group["node_count"] = values["fixed_node_count"]
    elif values.get("autoscaling"):
        group["autoscaling"] = {"enabled": True, **_present(values["autoscaling"])}
    else:
        raise RuntimeError("Observed node group has no capacity configuration")
    for key in ("platform", "preset"):
        if not shape.get(key):
            raise RuntimeError("Observed node group has incomplete hardware configuration")
        group[key] = shape[key]
    group["preemptible"] = template.get("preemptible") is not None
    group["node_labels"] = (template.get("metadata") or {}).get("labels") or {}
    interfaces = template.get("network_interfaces") or []
    if interfaces:
        group["subnet_id"] = interfaces[0].get("subnet_id")
        group["public_ips"] = any(
            (interface.get("public_ip_address") is not None) for interface in interfaces
        )
    gpu = template.get("gpu_settings") or {}
    group["gpu"] = str(shape["platform"]).startswith("gpu-") or bool(
        gpu or template.get("gpu_cluster")
    )
    if group["gpu"]:
        group["gpu_stack_source"] = "nebius_image" if gpu else "operator_managed"
        if gpu.get("drivers_preset"):
            group["gpu_stack_preset"] = gpu["drivers_preset"]
    if template.get("reservation_policy"):
        group["reservation"] = _present(template["reservation_policy"])
    gpu_id = (template.get("gpu_cluster") or {}).get("id")
    if gpu_id:
        owned = next(
            (
                row
                for row in resources
                if row.get("type") == "nebius_compute_v1_gpu_cluster"
                and row.get("values", {}).get("id") == gpu_id
            ),
            None,
        )
        group["gpu_cluster_key" if owned else "gpu_cluster_id"] = (
            owned["index"] if owned else gpu_id
        )
    account = template.get("service_account_id")
    if account:
        owned = next(
            (
                row
                for row in resources
                if row.get("type") == "nebius_iam_v1_service_account"
                and row.get("values", {}).get("id") == account
            ),
            None,
        )
        group["service_account"] = (
            {
                key: value
                for key in ("name", "description", "labels")
                if (value := owned["values"].get(key)) is not None
            }
            if owned
            else {"id": account}
        )
    cloud_init = template.get("cloud_init_user_data")
    if cloud_init:
        try:
            data = yaml.safe_load(cloud_init)
        except yaml.YAMLError:
            raise RuntimeError("Observed cloud-init is invalid YAML") from None
        users = data.get("users") if isinstance(data, Mapping) else None
        if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], Mapping):
            raise RuntimeError("Observed cloud-init is not the canonical MK8s SSH configuration")
        user = users[0]
        if not user.get("name") or not isinstance(user.get("ssh_authorized_keys"), list):
            raise RuntimeError("Observed cloud-init SSH configuration is incomplete")
        if data != {
            "users": [
                {
                    "name": user["name"],
                    "ssh_authorized_keys": user["ssh_authorized_keys"],
                    "sudo": "ALL=(ALL) NOPASSWD:ALL",
                    "shell": "/bin/bash",
                }
            ]
        }:
            raise RuntimeError(
                "Observed cloud-init differs from the canonical MK8s SSH configuration"
            )
        group["ssh"] = {"username": user["name"], "public_keys": user["ssh_authorized_keys"]}
    return group


def project_observed_source(
    desired: Mapping[str, Any],
    *,
    target_ref: str,
    resources: Sequence[Mapping],
    owned_modules: set[str],
    live_release: str,
    live_values: Mapping[str, Any],
    terraform: Mapping[str, Any],
) -> dict[str, Any]:
    """Build source topology from actual resources, including removed desired keys."""
    source = copy.deepcopy(to_dynamic_payload(desired))
    selected = soperator_target(source)
    if selected is None or selected[0] != target_ref:
        raise RuntimeError("Observed Soperator target does not match current deployment")
    selected[1]["version"] = live_release
    components = [
        row
        for row in source.get("infra", {}).get("components", [])
        if row.get("id") == "mk8s" and row.get("instance_id") == target_ref
    ]
    if not components:
        return source
    owned = [
        row
        for row in resources
        if any(
            str(row.get("address", "")).startswith(f"module.{module}.") for module in owned_modules
        )
    ]
    clusters = [row["values"] for row in owned if row.get("type") == "nebius_mk8s_v1_cluster"]
    if len(clusters) != 1:
        raise RuntimeError("Observed Terraform cluster ownership is missing or ambiguous")
    cluster = clusters[0]
    control = cluster.get("control_plane") or {}
    networks = [
        row.get("values", {}).get("id")
        for row in owned
        if row.get("type") == "nebius_vpc_v1_network"
    ]
    networks += [
        row.get("values", {}).get("network_id")
        for row in owned
        if row.get("type") == "nebius_vpc_v1_subnet"
        and row.get("values", {}).get("id") == control.get("subnet_id")
    ]
    networks = list({value for value in networks if value})
    if len(networks) != 1:
        raise RuntimeError("Cannot observe the Terraform-owned cluster's network identity")
    inputs = components[0].setdefault("inputs", {})
    inputs["cluster"] = _present(
        {
            "parent_id": cluster.get("parent_id"),
            "cluster_name": cluster.get("name"),
            "network_id": networks[0],
            "subnet_id": control.get("subnet_id"),
            "k8s_version": control.get("version"),
            "labels": cluster.get("labels") or {},
            "public_endpoint": (control.get("endpoints") or {}).get("public_endpoint") is not None,
            "etcd_cluster_size": control.get("etcd_cluster_size"),
            "kube_network": cluster.get("kube_network"),
            "control_plane": control,
        }
    )
    groups = {}
    for row in owned:
        if row.get("type") != "nebius_mk8s_v1_node_group":
            continue
        key = row.get("index")
        if not isinstance(key, str) or not key or key in groups:
            raise RuntimeError("Observed Terraform node-group key is missing or ambiguous")
        groups[key] = observed_node_group(row["values"], owned)
    inputs["node_groups"] = groups
    inputs["gpu_clusters"] = {
        row["index"]: {
            key: value
            for key in ("parent_id", "name", "labels", "infiniband_fabric")
            if (value := row["values"].get(key)) is not None
        }
        for row in owned
        if row.get("type") == "nebius_compute_v1_gpu_cluster"
    }
    # State expands module defaults and aliases. Reuse authored spelling only
    # where the refreshed plan proves the before/after value is equivalent.
    desired_component = next(
        row
        for row in desired["infra"]["components"]
        if row.get("id") == "mk8s" and row.get("instance_id") == target_ref
    )
    desired_inputs = desired_component["inputs"]
    for resource in terraform.get("resource_changes", []):
        if not any(
            str(resource.get("address", "")).startswith(f"module.{module}.")
            for module in owned_modules
        ):
            continue
        change = resource.get("change", {})
        if resource.get("type") == "nebius_mk8s_v1_cluster" and change.get("actions") == ["no-op"]:
            inputs["cluster"] = copy.deepcopy(desired_inputs["cluster"])
        key = resource.get("index")
        if resource.get("type") != "nebius_mk8s_v1_node_group" or key not in groups:
            continue
        authored = desired_inputs.get("node_groups", {}).get(key)
        after = change.get("after")
        if authored is None or after is None:
            continue
        if change.get("actions") == ["no-op"]:
            groups[key] = copy.deepcopy(authored)
            continue
        projected_after = observed_node_group(after, owned)
        before = groups[key]
        for field in tuple(set(before) | set(authored)):
            if before.get(field) == projected_after.get(field):
                if field in authored:
                    before[field] = copy.deepcopy(authored[field])
                else:
                    before.pop(field, None)
    return source


def resolved_settings_differ(
    cli: Any,
    *,
    generation: Any,
    paths: Any,
    target_ref: str,
    live_values: Mapping[str, Any] | None,
) -> bool:
    """Compare effective settings only after proving unchanged release/topology."""
    from .deployment_observation import _canonical_soperator_values, target_documents
    from .deployment_resolution import resolved_application_generation

    if live_values is None:
        return True
    resolved = resolved_application_generation(
        cli, generation, paths, initialize_terraform=False, target_ref=target_ref
    )
    documents = target_documents(resolved, target_ref)
    candidates = [
        document
        for document in documents.values()
        if document.get("kind") == "ConfigMap"
        and document.get("metadata", {}).get("name") == "terraform-fluxcd-values"
        and document.get("metadata", {}).get("namespace") == "flux-system"
    ]
    if len(candidates) != 1:
        raise RuntimeError("Resolved Soperator values identity is missing or ambiguous")
    desired = yaml.safe_load(candidates[0].get("data", {}).get("values.yaml", ""))
    if not isinstance(desired, Mapping):
        raise RuntimeError("Resolved Soperator values are unavailable")
    return _canonical_soperator_values(live_values) != _canonical_soperator_values(desired)


def observe_upstream_values(
    cli: Any, *, flux_dir: Any, kube_env: Mapping[str, str]
) -> Mapping | None:
    """Read the resource-owned values ConfigMap; never read runtime Secret payloads."""
    documents = list(
        yaml.safe_load_all((flux_dir / "configmap-terraform-fluxcd-values.yaml").read_text())
    )
    if len(documents) != 1 or not isinstance(documents[0], Mapping):
        raise RuntimeError("Rendered upstream values identity is invalid")
    expected = documents[0]["metadata"]
    result = cli._run_soperator_upgrade_kubectl(
        expected.get("namespace", "flux-system"),
        ["get", "configmap", expected["name"], "-o", "json", "--ignore-not-found=true"],
        kube_context=kube_env.get(cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV),
        extra_env=kube_env,
        check=True,
    )
    if not result.stdout.strip():
        return None
    document = json.loads(result.stdout)
    metadata = document.get("metadata", {})
    if (
        not metadata.get("uid")
        or metadata.get("name") != expected["name"]
        or metadata.get("namespace") != expected.get("namespace", "flux-system")
    ):
        raise RuntimeError("Live upstream values identity is incomplete")
    for key, value in expected.get("labels", {}).items():
        if metadata.get("labels", {}).get(key) != value:
            raise RuntimeError("Live upstream values ownership differs from the selected target")
    values = yaml.safe_load(document.get("data", {}).get("values.yaml", ""))
    if not isinstance(values, Mapping):
        raise RuntimeError("Live upstream values are unavailable")
    return values


def observe_adapter(cli: Any, *, kube_env: Mapping[str, str]) -> Mapping:
    from .soperator_adapter import (
        SOPERATOR_ADAPTER_NAMESPACE,
        SOPERATOR_ADAPTER_STATE_CONFIGMAP,
        soperator_adapter_state_from_documents,
    )

    result = cli._run_soperator_upgrade_kubectl(
        SOPERATOR_ADAPTER_NAMESPACE,
        ["get", "configmap", SOPERATOR_ADAPTER_STATE_CONFIGMAP, "-o", "json"],
        kube_context=kube_env.get(cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV),
        extra_env=kube_env,
        check=True,
    )
    document = json.loads(result.stdout)
    if not document.get("metadata", {}).get("uid"):
        raise RuntimeError("Observed Soperator adapter identity is incomplete")
    return soperator_adapter_state_from_documents([document])


def verify_reconcile_storage(cli, *, generation, flux_dir, target_ref, kube_env):
    """A same-release repair cannot silently change existing physical bindings."""
    from .deployment_jail_state import jail_values, observe_jail_storage
    from .deployment_observation import verify_desired_target

    if observe_adapter(cli, kube_env=kube_env) != cli._rendered_soperator_adapter_state(flux_dir):
        raise RuntimeError(
            "Current artifacts change the live Soperator storage adapter; "
            "use the dedicated Soperator upgrade workflow for a physical transition"
        )
    verify_desired_target(
        cli,
        generation=generation,
        target_ref=target_ref,
        kube_env=kube_env,
        protected_only=True,
        resource_kinds=frozenset({"PersistentVolume", "PersistentVolumeClaim"}),
    )
    observe_jail_storage(
        cli,
        flux_dir=flux_dir,
        values=jail_values(generation.manifest["runtime_config"], target_ref),
        kube_context=kube_env[cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV],
        kube_env=kube_env,
    )
    verify_worker_storage(cli, flux_dir=flux_dir, kube_env=kube_env)


def verify_worker_storage(cli, *, flux_dir, kube_env):
    """A partially switched NodeSet must use its physical-transition recovery."""
    from .deployment_observation import _owned_matches

    upstream = cli._rendered_soperator_upstream_values(flux_dir)
    expected = {
        row["name"]: row.get("slurmd", {}).get("volumes", {})
        for row in upstream.get("nodesets", {}).get("overrideValues", {}).get("nodesets", [])
    }
    namespaces = cli._soperator_upgrade_live_slurmcluster_namespaces(extra_env=kube_env)
    if len(namespaces) != 1:
        raise RuntimeError("Worker storage observation requires one exact Soperator namespace")
    response = cli._run_soperator_upgrade_kubectl(
        namespaces[0],
        ["get", "nodesets.slurm.nebius.ai", "-o", "json"],
        kube_context=kube_env.get(cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV),
        extra_env=kube_env,
        check=True,
    )
    payload = json.loads(response.stdout)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("items"), list):
        raise RuntimeError("Live worker storage inventory is unavailable")
    for row in payload["items"]:
        metadata = row.get("metadata", {})
        name = metadata.get("name")
        if not metadata.get("uid") or metadata.get("namespace") != namespaces[0]:
            raise RuntimeError("Live worker storage identity is incomplete")
        volumes = row.get("spec", {}).get("slurmd", {}).get("volumes", {})
        if name not in expected or any(
            not _owned_matches(
                expected[name].get(key, [] if key == "jailSubMounts" else {}),
                volumes.get(key, [] if key == "jailSubMounts" else {}),
            )
            for key in ("jail", "jailSubMounts")
        ):
            raise RuntimeError(
                "Live NodeSet storage differs from current artifacts; "
                "resume its dedicated physical-transition workflow before reconciliation"
            )


def verify_source_projection(
    cli: Any,
    source: dict,
    *,
    paths: Any,
    target_ref: str,
    upstream: Mapping,
    adapter: Mapping,
    kube_env: Mapping,
) -> dict:
    """Require a full current-source render round trip before admitting any stage."""
    from .deployment_jail_state import observe_jail_storage
    from .deployment_live_values import authored_values, physical_values
    from .soperator_release import load_soperator_release_snapshot, soperator_release_snapshot_path

    selected = soperator_target(source)
    assert selected is not None
    selected[1]["values"] = physical_values(selected[1].get("values", {}), adapter)
    baseline = cli._render_soperator_upgrade_admission(
        source_payload=source,
        config_path=paths.config_path,
        paths=paths,
        require_soperator_flux=False,
        preserve_ordinary=False,
    )
    try:
        release = load_soperator_release_snapshot(
            soperator_release_snapshot_path(baseline.staged_paths.reports_dir, target_ref)
        )
        selected[1]["values"] = authored_values(
            selected[1]["values"], upstream, adapter, release=release
        )
    finally:
        baseline.cleanup()
    rendered = cli._render_soperator_upgrade_admission(
        source_payload=source,
        config_path=paths.config_path,
        paths=paths,
        require_soperator_flux=False,
        preserve_ordinary=False,
    )
    try:
        manifest = cli.load_generated_manifest(rendered.staged_paths.generated_dir)
        target = cli._resolve_selected_deploy_targets(
            manifest, requested_target_ref=target_ref, all_targets=False
        )[0]
        target_paths = cli._paths_for_target_flux_dir(rendered.staged_paths, target)
        if (
            cli._rendered_soperator_upstream_values(target_paths.flux_dir) != upstream
            or cli._rendered_soperator_adapter_state(target_paths.flux_dir) != adapter
        ):
            raise RuntimeError(
                "Current Soperator source cannot be reproduced from observed values and storage; no changes were applied"
            )
        from .deployment_observation import verify_desired_target
        from .deployment_state import DeploymentGeneration

        # The adapter state omits PV capacity and placement. Compare the complete
        # protected resource specifications as well, before source can execute.
        verify_desired_target(
            cli,
            generation=DeploymentGeneration.capture(rendered.staged_paths, manifest),
            target_ref=target_ref,
            kube_env=kube_env,
            protected_only=True,
        )
        observe_jail_storage(
            cli,
            flux_dir=target_paths.flux_dir,
            values=selected[1]["values"],
            kube_context=kube_env[cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV],
            kube_env=kube_env,
        )
    finally:
        rendered.cleanup()
    return source
