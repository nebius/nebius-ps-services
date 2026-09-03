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
    SOPERATOR_UPSTREAM_REGISTRY,
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
            registry=SOPERATOR_UPSTREAM_REGISTRY,
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
