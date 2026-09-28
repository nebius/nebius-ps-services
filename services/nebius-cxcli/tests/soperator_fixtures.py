from __future__ import annotations

from collections.abc import Mapping

from nebius_cxcli.soperator_infrastructure_identity import (
    SOPERATOR_INFRASTRUCTURE_IDENTITY_SCHEMA,
    LoginAllocationIdentity,
    ProtectedStorageIdentity,
    SfsFilesystemIdentity,
    SfsProtectedStorageIdentity,
    SoperatorInfrastructureReceipt,
)
from nebius_cxcli.soperator_release import (
    SOPERATOR_MAIN_RELEASE_NAME,
    SOPERATOR_RELEASE_SNAPSHOT_SCHEMA,
    SOPERATOR_UPSTREAM_REPOSITORY,
    SoperatorChartSnapshot,
    SoperatorReleaseGraphNode,
    SoperatorReleaseSnapshot,
    SoperatorThirdPartyChartSnapshot,
    seal_soperator_release_snapshot,
)


def sample_snapshot(
    *,
    release: str = "4.1.7",
    target_ref: str = "soperator",
    release_names: tuple[str, ...] = (SOPERATOR_MAIN_RELEASE_NAME,),
    source_contract: str = "upstream-flux-v1",
    third_party_release_chart_keys: Mapping[str, str] | None = None,
) -> SoperatorReleaseSnapshot:
    digest = "sha256:" + "1" * 64
    chart = SoperatorChartSnapshot(
        name="helm-soperator-fluxcd",
        version=release,
        digest="sha256:" + "2" * 64,
        package_sha256="sha256:" + "3" * 64,
        source_path="helm/soperator-fluxcd",
        source_tree_sha256="sha256:" + "4" * 64,
    )
    third_party_versions = {
        "namespaceRaw": "2.0.0",
        "certManager": "v1.19.6",
        "kruise": "1.8.0",
        "mariadbOperator": "25.10.2",
        "securityProfilesOperator": "0.8.5-soperator",
    }
    third_party = {
        key: SoperatorThirdPartyChartSnapshot(
            chart=name,
            version=third_party_versions[key],
            repository="https://charts.example.invalid",
            package_sha256="sha256:" + token * 64,
        )
        for key, name, token in (
            ("namespaceRaw", "raw", "8"),
            ("certManager", "cert-manager", "9"),
            ("kruise", "kruise", "a"),
            ("mariadbOperator", "mariadb-operator", "b"),
            ("securityProfilesOperator", "security-profiles-operator", "c"),
        )
    }
    third_party_release_chart_keys = dict(third_party_release_chart_keys or {})
    return seal_soperator_release_snapshot(
        SoperatorReleaseSnapshot(
            schema=SOPERATOR_RELEASE_SNAPSHOT_SCHEMA,
            target_ref=target_ref,
            request_sha256="sha256:" + "e" * 64,
            stage_graphs={},
            auxiliary_artifacts={},
            post_render_patches=(),
            selector=release,
            release=release,
            repository=SOPERATOR_UPSTREAM_REPOSITORY,
            tag=release,
            commit="a" * 40,
            tree="b" * 40,
            archive_url=(f"{SOPERATOR_UPSTREAM_REPOSITORY}/archive/refs/tags/{release}.tar.gz"),
            archive_sha256=digest,
            archive_root=f"soperator-{release}",
            source_manifest_sha256="sha256:" + "5" * 64,
            registry="oci://cr.eu-north1.nebius.cloud/soperator",
            capability_contract=source_contract,
            capability_sha256="sha256:" + "6" * 64,
            charts={"umbrella": chart},
            third_party_charts=third_party,
            release_graph=tuple(
                SoperatorReleaseGraphNode(
                    release_name=name,
                    namespace="flux-system",
                    owner=("third-party" if name in third_party_release_chart_keys else "upstream"),
                    stage=index,
                    chart_key=third_party_release_chart_keys.get(name, "umbrella"),
                    dependencies=release_names[:index],
                    is_main=name == SOPERATOR_MAIN_RELEASE_NAME,
                )
                for index, name in enumerate(release_names)
            ),
            scripts_manifest_sha256="sha256:" + "7" * 64,
            image_references=("registry.example.invalid/soperator@" + digest,),
            mount_image="registry.example.invalid/mount@" + digest,
            adapter_state_schema="nebius-cxcli.soperator-adapter-state.v2",
            populate_jail_image="registry.example.invalid/jail@" + digest,
            jail_cuda_version="12.6",
            snapshot_sha256="",
        )
    )


def sample_infrastructure_receipt() -> SoperatorInfrastructureReceipt:
    return SoperatorInfrastructureReceipt(
        schema=SOPERATOR_INFRASTRUCTURE_IDENTITY_SCHEMA,
        project_id="project-a",
        nebius_cluster_id="mk8s-a",
        kubernetes_uid="kube-system-uid",
        storage=ProtectedStorageIdentity(
            kind="sfs",
            sfs=SfsProtectedStorageIdentity(
                filesystems=(
                    SfsFilesystemIdentity(
                        role="jail",
                        filesystem_id="filesystem-jail",
                        mount_tag="cluster-a-jail",
                        node_group_ids=("nodes-controller", "nodes-worker"),
                        pv_names=("pv-jail",),
                        pvc_names=("jail-rootfs",),
                    ),
                )
            ),
        ),
        login=LoginAllocationIdentity(
            namespace="soperator",
            service_name="soperator-login-svc",
            service_uid="login-service-uid",
            service_type="LoadBalancer",
            cluster_ips=("10.96.0.20",),
            ingress_addresses=("203.0.113.9",),
            allocation_ids=("login-allocation",),
            service_spec_sha256="sha256:" + "e" * 64,
            assignment_sha256="sha256:" + "f" * 64,
        ),
    )


def sample_jail_logs_binding():
    system = {"matchExpressions": [{"key": "node-group", "operator": "In", "values": ["system"]}]}
    storage = {"matchExpressions": [{"key": "jail", "operator": "In", "values": ["true"]}]}
    values = {
        "observability": {"enabled": True},
        "slurmCluster": {
            "namespace": "soperator",
            "overrideValues": {
                "clusterName": "soperator",
                "k8sNodeFilters": [
                    {
                        "name": "system",
                        "affinity": {
                            "nodeAffinity": {
                                "requiredDuringSchedulingIgnoredDuringExecution": {
                                    "nodeSelectorTerms": [system]
                                }
                            }
                        },
                    }
                ],
                "volumeSources": [
                    {"name": "jail", "persistentVolumeClaim": {"claimName": "active-jail"}}
                ],
            },
        },
    }
    documents = [
        {
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "active-jail", "namespace": "soperator"},
            "spec": {"volumeName": "active-jail-pv"},
        },
        {
            "kind": "PersistentVolume",
            "metadata": {
                "name": "active-jail-pv",
                "labels": {"soperator.nebius.ai/lifecycle": "protected"},
            },
            "spec": {
                "claimRef": {"name": "active-jail", "namespace": "soperator"},
                "local": {"path": "/mnt/jail-store/rootfs/slot-a"},
                "nodeAffinity": {"required": {"nodeSelectorTerms": [storage]}},
            },
        },
    ]
    return values, documents


def _enabled(value, *, default=False):
    return value is True if value is not None else default


def _nested(values, *path):
    current = values
    for key in path:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key)
    return current if isinstance(current, Mapping) else {}


def expected_soperator_release_names(values: Mapping[str, object]) -> frozenset[str]:
    names = {
        "soperator-fluxcd-ns",
        "soperator-fluxcd-kruise",
        "soperator-fluxcd-security-profiles-operator",
        "soperator-fluxcd-custom-configmaps",
        "soperator-fluxcd-soperator",
        "soperator-fluxcd-nodeconfigurator",
        "soperator-fluxcd-slurm-cluster",
    }
    if _enabled(_nested(values, "certManager").get("enabled"), default=True):
        names.add("soperator-fluxcd-cert-manager")
    if _enabled(_nested(values, "mariadbOperator").get("enabled"), default=True):
        names.update(
            {
                "soperator-fluxcd-mariadb-operator-crds",
                "soperator-fluxcd-mariadb-operator",
            }
        )
    if _enabled(_nested(values, "soperator", "soperatorChecks").get("enabled"), default=True):
        names.add("soperator-fluxcd-soperatorchecks")
    if _enabled(_nested(values, "nodesets").get("enabled")):
        names.add("soperator-fluxcd-nodesets")
    if _enabled(_nested(values, "soperatorActiveChecks").get("enabled"), default=True):
        names.add("soperator-fluxcd-soperator-activechecks")
    if _enabled(_nested(values, "storageClasses").get("enabled")):
        names.add("soperator-fluxcd-storageclasses")
    backup = _nested(values, "backup")
    if _enabled(backup.get("enabled")):
        names.add("soperator-fluxcd-k8up")
        if _enabled(_nested(backup, "config").get("enabled")):
            names.add("soperator-fluxcd-backup-config")
    observability = _nested(values, "observability")
    if _enabled(observability.get("enabled")):
        vm_stack = _nested(observability, "vmStack")
        vm_logs_enabled = _enabled(_nested(observability, "vmLogs").get("enabled"), default=True)
        vm_stack_enabled = _enabled(vm_stack.get("enabled"), default=True)
        if _enabled(_nested(observability, "prometheusOperator").get("enabled"), default=True):
            names.add("soperator-fluxcd-prometheus-operator-crds")
        if vm_logs_enabled:
            names.add("soperator-fluxcd-vm-logs")
        if vm_stack_enabled:
            names.update(
                {
                    "soperator-fluxcd-victoria-metrics-operator-crds",
                    "soperator-fluxcd-vm-stack",
                }
            )
            token_kind = str(observability.get("publicEndpointTokenKind") or "secret")
            writer = _nested(vm_stack, "tsaToken", "writer")
            if (
                _enabled(observability.get("publicEndpointEnabled"), default=True)
                and token_kind == "secret"
                and _enabled(writer.get("enabled"), default=True)
            ):
                names.add("soperator-fluxcd-tsa-token-writer")
        opentelemetry = _nested(observability, "opentelemetry")
        if _enabled(opentelemetry.get("enabled"), default=True):
            names.update(
                {
                    "soperator-fluxcd-opentelemetry-collector-events",
                    "soperator-fluxcd-opentelemetry-collector-logs",
                }
            )
            if _enabled(
                _nested(opentelemetry, "logs", "values", "jailLogs").get("enabled"),
                default=True,
            ):
                names.add("soperator-fluxcd-opentelemetry-collector-jail-logs")
        if _enabled(_nested(observability, "dcgmExporter").get("enabled"), default=True):
            names.add("soperator-fluxcd-dcgm-exporter")
        if _enabled(_nested(values, "notifier").get("enabled")) and _enabled(
            _nested(observability, "vmStack").get("enabled"), default=True
        ):
            names.add("soperator-fluxcd-soperator-notifier")
    if _enabled(_nested(values, "soperator", "monitoringDashboards").get("enabled")):
        names.add("soperator-fluxcd-monitoring-dashboards")
    return frozenset(names)


def sample_selected_graph(lock, values):
    names = expected_soperator_release_names(values)
    nodes = {node.release_name: node for node in lock.release_graph}
    if names - nodes.keys():
        raise ValueError("Soperator values enable an unverified release")
    return tuple(nodes[name] for name in sorted(names))
