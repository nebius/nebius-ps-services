"""Single-writer ownership handoff for protected Soperator release upgrades."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Protocol
from urllib.parse import quote

from .soperator_release import SOPERATOR_UPSTREAM_UMBRELLA_CHART

SOPERATOR_RELEASE_OWNERSHIP_SCHEMA = "nebius-cxcli.soperator-release-ownership.v2"
SOPERATOR_SOURCE_PARENT_WRITER_SCHEMA = (
    "nebius-cxcli.soperator-source-parent-writer.v1"
)
_CANONICAL_CONTROLLER_NAMESPACE = "soperator-system"
_CANONICAL_CONTROLLER_NAME = "soperator-controller-manager"
_PROTECTED_DATA_PLANE_SOURCE_CONTRACT = "protected-data-plane-v1"
_UPSTREAM_FLUX_SOURCE_CONTRACT = "upstream-flux-v1"


class OwnershipCommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


OwnershipCommandRunner = Callable[..., OwnershipCommandResult]


@dataclass(frozen=True)
class SourceReleaseOwner:
    kind: str
    namespace: str
    name: str
    uid: str
    resource_version: str
    original_suspend: bool | None = None
    original_replicas: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"HelmRelease", "Deployment"}:
            raise ValueError("Soperator source owner kind is invalid")
        for value in (self.namespace, self.name, self.uid, self.resource_version):
            if not str(value or "").strip() or "\n" in str(value) or "\r" in str(value):
                raise ValueError("Soperator source owner identity is incomplete")


@dataclass(frozen=True)
class SourceReleaseOwnership:
    schema: str
    owners: tuple[SourceReleaseOwner, ...]

    @property
    def receipt_sha256(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def as_payload(self) -> dict[str, object]:
        return {**asdict(self), "receipt_sha256": self.receipt_sha256}


@dataclass(frozen=True)
class SourceParentWriter:
    """Exact Flux Kustomization that continuously writes the legacy umbrella."""

    schema: str
    namespace: str
    name: str
    uid: str
    managed_helmrelease_uid: str
    contract_sha256: str
    original_suspend: bool | None

    def __post_init__(self) -> None:
        if self.schema != SOPERATOR_SOURCE_PARENT_WRITER_SCHEMA:
            raise ValueError("Soperator source parent writer schema is invalid")
        for value in (
            self.namespace,
            self.name,
            self.uid,
            self.managed_helmrelease_uid,
        ):
            if not str(value or "").strip() or "\n" in str(value) or "\r" in str(value):
                raise ValueError("Soperator source parent writer identity is incomplete")
        if not str(self.contract_sha256).startswith("sha256:") or len(
            self.contract_sha256
        ) != len("sha256:") + 64:
            raise ValueError("Soperator source parent writer contract is invalid")

    @property
    def receipt_sha256(self) -> str:
        return _sha256(asdict(self))

    def as_payload(self) -> dict[str, object]:
        return {**asdict(self), "receipt_sha256": self.receipt_sha256}


def source_parent_writer_from_payload(
    payload: Mapping[str, object],
) -> SourceParentWriter:
    writer = SourceParentWriter(
        schema=str(payload.get("schema") or ""),
        namespace=str(payload.get("namespace") or ""),
        name=str(payload.get("name") or ""),
        uid=str(payload.get("uid") or ""),
        managed_helmrelease_uid=str(payload.get("managed_helmrelease_uid") or ""),
        contract_sha256=str(payload.get("contract_sha256") or ""),
        original_suspend=(
            payload.get("original_suspend")
            if isinstance(payload.get("original_suspend"), bool)
            else None
        ),
    )
    if payload.get("receipt_sha256") != writer.receipt_sha256:
        raise RuntimeError("Soperator source parent writer receipt was modified")
    return writer


def source_release_ownership_from_payload(
    payload: Mapping[str, object],
) -> SourceReleaseOwnership:
    if payload.get("schema") != SOPERATOR_RELEASE_OWNERSHIP_SCHEMA:
        raise RuntimeError("Soperator source ownership receipt has an unsupported schema")
    raw_owners = payload.get("owners")
    if not isinstance(raw_owners, list):
        raise RuntimeError("Soperator source ownership receipt has no owner list")
    owners: list[SourceReleaseOwner] = []
    for item in raw_owners:
        if not isinstance(item, Mapping):
            raise RuntimeError("Soperator source ownership receipt has a malformed owner")
        owners.append(
            SourceReleaseOwner(
                kind=str(item.get("kind") or ""),
                namespace=str(item.get("namespace") or ""),
                name=str(item.get("name") or ""),
                uid=str(item.get("uid") or ""),
                resource_version=str(item.get("resource_version") or ""),
                original_suspend=(
                    item.get("original_suspend")
                    if isinstance(item.get("original_suspend"), bool)
                    else None
                ),
                original_replicas=(
                    item.get("original_replicas")
                    if isinstance(item.get("original_replicas"), int)
                    else None
                ),
            )
        )
    receipt = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=tuple(owners),
    )
    if payload.get("receipt_sha256") != receipt.receipt_sha256:
        raise RuntimeError("Soperator source ownership receipt was modified")
    return receipt


def capture_source_release_ownership(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    graph_contract: Mapping[str, object],
    source_release: str,
    source_contract: str = _UPSTREAM_FLUX_SOURCE_CONTRACT,
    source_release_identities: Sequence[tuple[str, str]] | None = None,
) -> SourceReleaseOwnership:
    """Capture the exact writers admitted by the frozen source capability contract."""

    normalized_source_release = str(source_release or "").strip().removeprefix("v")
    if not normalized_source_release:
        raise RuntimeError("protected Soperator source release identity is missing")
    normalized_source_contract = str(source_contract or "").strip()
    if normalized_source_contract == _PROTECTED_DATA_PLANE_SOURCE_CONTRACT:
        if source_release_identities is not None:
            raise RuntimeError(
                "protected data-plane source ownership does not accept target graph identities"
            )
        owners = _capture_protected_data_plane_source_helmreleases(
            runner,
            kube_context=kube_context,
            source_release=normalized_source_release,
        )
    elif normalized_source_contract == _UPSTREAM_FLUX_SOURCE_CONTRACT:
        owners = _capture_upstream_flux_source_helmreleases(
            runner,
            kube_context=kube_context,
            graph_contract=graph_contract,
            source_release=normalized_source_release,
            source_release_identities=source_release_identities,
        )
    else:
        raise RuntimeError(
            "protected Soperator source ownership has no reviewed capability contract"
        )
    owners.append(
        _capture_source_controller(
            runner,
            kube_context=kube_context,
            source_release=normalized_source_release,
        )
    )
    helm_owners = [owner for owner in owners if owner.kind == "HelmRelease"]
    suspended = [
        f"{owner.namespace}/{owner.name}" for owner in helm_owners if owner.original_suspend is True
    ]
    if suspended:
        raise RuntimeError(
            "protected Soperator ownership cannot safely roll back while source "
            "HelmReleases are already suspended: " + ", ".join(sorted(suspended))
        )
    return SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=tuple(sorted(owners, key=lambda item: (item.kind, item.namespace, item.name))),
    )


def _capture_upstream_flux_source_helmreleases(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    graph_contract: Mapping[str, object],
    source_release: str,
    source_release_identities: Sequence[tuple[str, str]] | None,
) -> list[SourceReleaseOwner]:
    """Capture only source objects named by a frozen direct-upstream graph."""

    rows = _frozen_graph_release_rows(graph_contract)
    if source_release_identities is not None:
        expected_identities = {
            (str(namespace or "").strip(), str(name or "").strip())
            for namespace, name in source_release_identities
        }
        if not expected_identities or any(not all(identity) for identity in expected_identities):
            raise RuntimeError("protected Soperator source release graph identity is incomplete")
        rows_by_identity = {
            (str(row.get("namespace") or ""), str(row.get("releaseName") or "")): row
            for row in rows
        }
        missing = sorted(expected_identities - rows_by_identity.keys())
        if missing:
            raise RuntimeError(
                "protected Soperator target graph omits source release owners: "
                + ", ".join(f"{namespace}/{name}" for namespace, name in missing)
            )
        rows = tuple(rows_by_identity[identity] for identity in sorted(expected_identities))
        if sum(row.get("isMain") is True for row in rows) != 1:
            raise RuntimeError(
                "protected Soperator source release graph has no unique main workload"
            )
    owners: list[SourceReleaseOwner] = []
    for row in rows:
        namespace = str(row.get("namespace") or "")
        name = str(row.get("releaseName") or "")
        item = _json_command(
            runner,
            _kubectl(kube_context, "-n", namespace, "get", "helmrelease", name, "-o", "json"),
        )
        metadata = _mapping(item.get("metadata"))
        labels = _mapping(metadata.get("labels"))
        spec = _mapping(item.get("spec"))
        expected_ref = {
            "kind": str(row.get("sourceKind") or ""),
            "name": str(row.get("sourceName") or ""),
            "namespace": "flux-system",
        }
        live_release = str(labels.get("app.kubernetes.io/version") or "").removeprefix("v")
        if (
            labels.get("soperator.nebius.ai/release-graph") != "nebius-cxcli"
            or live_release != source_release
            or spec.get("chartRef") != expected_ref
            or "chart" in spec
        ):
            raise RuntimeError(
                f"protected Soperator source HelmRelease {namespace}/{name} differs from "
                "the frozen direct-upstream graph"
            )
        owners.append(
            _owner(
                item,
                kind="HelmRelease",
                original_suspend=(bool(spec.get("suspend")) if "suspend" in spec else None),
            )
        )
    return owners


def _capture_protected_data_plane_source_helmreleases(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    source_release: str,
) -> list[SourceReleaseOwner]:
    """Capture one legacy umbrella and every exact Helm-owned child writer."""

    inventory = _json_command(
        runner,
        _kubectl(
            kube_context,
            "get",
            "helmreleases.helm.toolkit.fluxcd.io",
            "-A",
            "-o",
            "json",
        ),
    )
    release_items = _items(inventory)
    umbrella_items: list[Mapping[str, object]] = []
    for item in release_items:
        metadata = _mapping(item.get("metadata"))
        spec = _mapping(item.get("spec"))
        chart = _mapping(spec.get("chart"))
        chart_spec = _mapping(chart.get("spec"))
        if (
            str(metadata.get("namespace") or "") == "flux-system"
            and str(chart_spec.get("chart") or "") == SOPERATOR_UPSTREAM_UMBRELLA_CHART
        ):
            umbrella_items.append(item)
    if len(umbrella_items) != 1:
        raise RuntimeError(
            "protected data-plane source ownership requires exactly one legacy umbrella "
            "HelmRelease"
        )
    umbrella = umbrella_items[0]
    umbrella_metadata = _mapping(umbrella.get("metadata"))
    umbrella_spec = _mapping(umbrella.get("spec"))
    umbrella_chart = _mapping(_mapping(umbrella_spec.get("chart")).get("spec"))
    umbrella_source_ref = _mapping(umbrella_chart.get("sourceRef"))
    umbrella_version = str(umbrella_chart.get("version") or "").removeprefix("v")
    umbrella_name = str(umbrella_metadata.get("name") or "").strip()
    umbrella_namespace = str(umbrella_metadata.get("namespace") or "").strip()
    target_namespace = str(
        umbrella_spec.get("targetNamespace") or umbrella_namespace
    ).strip()
    if (
        not all((umbrella_name, umbrella_namespace, target_namespace))
        or umbrella_version != source_release
        or umbrella_source_ref.get("kind") != "HelmRepository"
        or not str(umbrella_source_ref.get("name") or "").strip()
        or "chartRef" in umbrella_spec
    ):
        raise RuntimeError(
            "protected data-plane source umbrella differs from the frozen source identity"
        )

    child_items: list[Mapping[str, object]] = []
    for item in release_items:
        metadata = _mapping(item.get("metadata"))
        labels = _mapping(metadata.get("labels"))
        if (
            labels.get("helm.toolkit.fluxcd.io/name") == umbrella_name
            and labels.get("helm.toolkit.fluxcd.io/namespace") == umbrella_namespace
        ):
            child_items.append(item)
    if not child_items:
        raise RuntimeError("protected data-plane source umbrella has no owned HelmReleases")

    release_names = {
        str(_mapping(_mapping(item.get("metadata")).get("annotations")).get(
            "meta.helm.sh/release-name"
        )
        or "").strip()
        for item in child_items
    }
    if len(release_names) != 1 or not next(iter(release_names)):
        raise RuntimeError(
            "protected data-plane source children have ambiguous Helm release ownership"
        )
    helm_release_name = next(iter(release_names))
    child_identities = {
        (
            str(_mapping(item.get("metadata")).get("namespace") or ""),
            str(_mapping(item.get("metadata")).get("name") or ""),
        )
        for item in child_items
    }
    for item in release_items:
        metadata = _mapping(item.get("metadata"))
        labels = _mapping(metadata.get("labels"))
        annotations = _mapping(metadata.get("annotations"))
        identity = (
            str(metadata.get("namespace") or ""),
            str(metadata.get("name") or ""),
        )
        claims_umbrella = (
            labels.get("app.kubernetes.io/instance") == helm_release_name
            or (
                annotations.get("meta.helm.sh/release-name") == helm_release_name
                and annotations.get("meta.helm.sh/release-namespace") == target_namespace
            )
        )
        if claims_umbrella and identity not in child_identities:
            raise RuntimeError(
                "protected data-plane source contains a partially identified umbrella child"
            )

    owners: list[SourceReleaseOwner] = []
    core_charts: dict[str, int] = {
        "helm-soperator": 0,
        "helm-slurm-cluster": 0,
        "helm-slurm-cluster-storage": 0,
    }
    for item in child_items:
        metadata = _mapping(item.get("metadata"))
        labels = _mapping(metadata.get("labels"))
        annotations = _mapping(metadata.get("annotations"))
        spec = _mapping(item.get("spec"))
        chart_spec = _mapping(_mapping(spec.get("chart")).get("spec"))
        source_ref = _mapping(chart_spec.get("sourceRef"))
        namespace = str(metadata.get("namespace") or "")
        name = str(metadata.get("name") or "")
        chart_name = str(chart_spec.get("chart") or "")
        chart_version = str(chart_spec.get("version") or "").removeprefix("v")
        live_release = str(labels.get("app.kubernetes.io/version") or "").removeprefix("v")
        if (
            namespace != target_namespace
            or not name.startswith(f"{helm_release_name}-")
            or labels.get("app.kubernetes.io/instance") != helm_release_name
            or labels.get("app.kubernetes.io/managed-by") != "Helm"
            or labels.get("app.kubernetes.io/name") != SOPERATOR_UPSTREAM_UMBRELLA_CHART
            or labels.get("helm.sh/chart") != f"helm-soperator-fluxcd-{source_release}"
            or live_release != source_release
            or annotations.get("meta.helm.sh/release-name") != helm_release_name
            or annotations.get("meta.helm.sh/release-namespace") != target_namespace
            or not chart_name
            or not chart_version
            or source_ref.get("kind") != "HelmRepository"
            or not str(source_ref.get("name") or "").strip()
            or "chartRef" in spec
        ):
            raise RuntimeError(
                f"protected data-plane source HelmRelease {namespace}/{name} differs from "
                "the admitted umbrella ownership contract"
            )
        if chart_name in core_charts:
            if chart_version != source_release:
                raise RuntimeError(
                    "protected data-plane source core HelmRelease differs from the frozen "
                    "source release"
                )
            core_charts[chart_name] += 1
        owners.append(
            _owner(
                item,
                kind="HelmRelease",
                original_suspend=(bool(spec.get("suspend")) if "suspend" in spec else None),
            )
        )
    if any(count != 1 for count in core_charts.values()):
        raise RuntimeError(
            "protected data-plane source umbrella lacks a unique operator, storage, or "
            "SlurmCluster writer"
        )
    owners.append(
        _owner(
            umbrella,
            kind="HelmRelease",
            original_suspend=(
                bool(umbrella_spec.get("suspend")) if "suspend" in umbrella_spec else None
            ),
        )
    )
    return owners


def _capture_source_controller(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    source_release: str,
) -> SourceReleaseOwner:
    controller = _json_command(
        runner,
        _kubectl(
            kube_context,
            "-n",
            _CANONICAL_CONTROLLER_NAMESPACE,
            "get",
            "deployment",
            _CANONICAL_CONTROLLER_NAME,
            "-o",
            "json",
        )
    )
    controller_metadata = _mapping(controller.get("metadata"))
    controller_labels = _mapping(controller_metadata.get("labels"))
    controller_release = str(
        controller_labels.get("app.kubernetes.io/version") or ""
    ).removeprefix("v")
    if controller_release != source_release:
        raise RuntimeError(
            "protected Soperator controller release differs from the frozen source identity"
        )
    controller_spec = _mapping(controller.get("spec"))
    raw_replicas = controller_spec.get("replicas", 1)
    return _owner(
        controller,
        kind="Deployment",
        original_replicas=int(raw_replicas) if isinstance(raw_replicas, int) else 1,
    )


def _source_parent_contract_sha256(resource: Mapping[str, object]) -> str:
    spec = dict(_mapping(resource.get("spec")))
    spec.pop("suspend", None)
    source_ref = _mapping(spec.get("sourceRef"))
    if (
        source_ref.get("kind") not in {"GitRepository", "OCIRepository", "Bucket"}
        or not str(source_ref.get("name") or "").strip()
        or not isinstance(spec.get("prune"), bool)
    ):
        raise RuntimeError("legacy Soperator source parent contract is incomplete")
    return _sha256(spec)


def _source_parent_inventory_contains_umbrella(
    resource: Mapping[str, object],
    *,
    namespace: str,
    name: str,
) -> bool:
    status = _mapping(resource.get("status"))
    inventory = _mapping(status.get("inventory"))
    entries = inventory.get("entries")
    expected_id = f"{namespace}_{name}_helm.toolkit.fluxcd.io_HelmRelease"
    return isinstance(entries, list) and any(
        isinstance(item, Mapping)
        and item.get("id") == expected_id
        and str(item.get("v") or "").startswith("v2")
        for item in entries
    )


def capture_source_parent_writer(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    ownership: SourceReleaseOwnership,
) -> SourceParentWriter | None:
    """Resolve the exact Flux parent that writes one captured legacy umbrella."""

    candidates: list[tuple[SourceReleaseOwner, Mapping[str, object], str, str]] = []
    for owner in ownership.owners:
        if owner.kind != "HelmRelease":
            continue
        current = _json_command(
            runner,
            _kubectl(
                kube_context,
                "-n",
                owner.namespace,
                "get",
                "helmrelease",
                owner.name,
                "-o",
                "json",
            ),
        )
        metadata = _mapping(current.get("metadata"))
        if str(metadata.get("uid") or "") != owner.uid:
            raise RuntimeError("legacy Soperator umbrella identity changed before parent capture")
        labels = _mapping(metadata.get("labels"))
        parent_name = str(labels.get("kustomize.toolkit.fluxcd.io/name") or "").strip()
        parent_namespace = str(
            labels.get("kustomize.toolkit.fluxcd.io/namespace") or ""
        ).strip()
        if parent_name or parent_namespace:
            if not parent_name or not parent_namespace:
                raise RuntimeError("legacy Soperator umbrella has partial Flux parent identity")
            candidates.append((owner, current, parent_namespace, parent_name))
    if not candidates:
        return None
    parent_identities = {(namespace, name) for _, _, namespace, name in candidates}
    if len(candidates) != 1 or len(parent_identities) != 1:
        raise RuntimeError("legacy Soperator umbrella has ambiguous Flux parent ownership")
    owner, _umbrella, parent_namespace, parent_name = candidates[0]
    parent = _json_command(
        runner,
        _kubectl(
            kube_context,
            "-n",
            parent_namespace,
            "get",
            "kustomization.kustomize.toolkit.fluxcd.io",
            parent_name,
            "-o",
            "json",
        ),
    )
    metadata = _mapping(parent.get("metadata"))
    parent_uid = str(metadata.get("uid") or "").strip()
    if (
        not parent_uid
        or not _source_parent_inventory_contains_umbrella(
            parent,
            namespace=owner.namespace,
            name=owner.name,
        )
    ):
        raise RuntimeError(
            "legacy Soperator Flux parent does not inventory the captured umbrella"
        )
    return SourceParentWriter(
        schema=SOPERATOR_SOURCE_PARENT_WRITER_SCHEMA,
        namespace=parent_namespace,
        name=parent_name,
        uid=parent_uid,
        managed_helmrelease_uid=owner.uid,
        contract_sha256=_source_parent_contract_sha256(parent),
        # The legacy bootstrap declares no suspend field. Removing our field is
        # the canonical rollback even if this recovery observes it after a
        # previous interrupted quiescence already set it to true.
        original_suspend=None,
    )


def _source_parent_umbrella_owner(
    ownership: SourceReleaseOwnership,
    writer: SourceParentWriter,
) -> SourceReleaseOwner:
    matches = [
        owner
        for owner in ownership.owners
        if owner.kind == "HelmRelease" and owner.uid == writer.managed_helmrelease_uid
    ]
    if len(matches) != 1:
        raise RuntimeError("legacy Soperator Flux parent has no unique captured umbrella")
    return matches[0]


def _read_source_parent_writer(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    ownership: SourceReleaseOwnership,
    writer: SourceParentWriter,
) -> Mapping[str, object]:
    parent = _json_command(
        runner,
        _kubectl(
            kube_context,
            "-n",
            writer.namespace,
            "get",
            "kustomization.kustomize.toolkit.fluxcd.io",
            writer.name,
            "-o",
            "json",
        ),
    )
    metadata = _mapping(parent.get("metadata"))
    umbrella = _source_parent_umbrella_owner(ownership, writer)
    if (
        str(metadata.get("uid") or "") != writer.uid
        or _source_parent_contract_sha256(parent) != writer.contract_sha256
        or not _source_parent_inventory_contains_umbrella(
            parent,
            namespace=umbrella.namespace,
            name=umbrella.name,
        )
    ):
        raise RuntimeError("legacy Soperator Flux parent authority changed")
    return parent


def _source_parent_is_quiesced(resource: Mapping[str, object]) -> bool:
    if _mapping(resource.get("spec")).get("suspend") is not True:
        return False
    conditions = _mapping(resource.get("status")).get("conditions")
    return not (
        isinstance(conditions, list)
        and any(
            isinstance(item, Mapping)
            and item.get("type") == "Reconciling"
            and str(item.get("status") or "").lower() == "true"
            for item in conditions
        )
    )


def _quiesce_source_parent_writer(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    ownership: SourceReleaseOwnership,
    writer: SourceParentWriter,
    assert_authority: Callable[[], object],
) -> None:
    current = _read_source_parent_writer(
        runner,
        kube_context=kube_context,
        ownership=ownership,
        writer=writer,
    )
    if _mapping(current.get("spec")).get("suspend") is not True:
        metadata = _mapping(current.get("metadata"))
        resource_version = str(metadata.get("resourceVersion") or "").strip()
        if not resource_version:
            raise RuntimeError("legacy Soperator Flux parent has no resourceVersion")
        assert_authority()
        _cas_merge_patch(
            runner,
            kube_context=kube_context,
            namespace=writer.namespace,
            resource="kustomization.kustomize.toolkit.fluxcd.io",
            name=writer.name,
            uid=writer.uid,
            resource_version=resource_version,
            spec_patch={"suspend": True},
        )
    for attempt in range(121):
        observed = _read_source_parent_writer(
            runner,
            kube_context=kube_context,
            ownership=ownership,
            writer=writer,
        )
        if _source_parent_is_quiesced(observed):
            return
        if attempt == 120:
            raise RuntimeError("legacy Soperator Flux parent did not become quiescent")
        assert_authority()
        time.sleep(1)


def _verify_source_parent_writer_quiesced(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    ownership: SourceReleaseOwnership,
    writer: SourceParentWriter,
) -> None:
    current = _read_source_parent_writer(
        runner,
        kube_context=kube_context,
        ownership=ownership,
        writer=writer,
    )
    if not _source_parent_is_quiesced(current):
        raise RuntimeError("legacy Soperator Flux parent writer is not quiesced")


def _restore_source_parent_writer(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    ownership: SourceReleaseOwnership,
    writer: SourceParentWriter,
    assert_authority: Callable[[], object],
) -> None:
    current = _read_source_parent_writer(
        runner,
        kube_context=kube_context,
        ownership=ownership,
        writer=writer,
    )
    metadata = _mapping(current.get("metadata"))
    spec = _mapping(current.get("spec"))
    if writer.original_suspend is None and "suspend" not in spec:
        return
    if writer.original_suspend is not None and spec.get("suspend") is writer.original_suspend:
        return
    resource_version = str(metadata.get("resourceVersion") or "").strip()
    if not resource_version:
        raise RuntimeError("legacy Soperator Flux parent has no resourceVersion")
    assert_authority()
    _cas_merge_patch(
        runner,
        kube_context=kube_context,
        namespace=writer.namespace,
        resource="kustomization.kustomize.toolkit.fluxcd.io",
        name=writer.name,
        uid=writer.uid,
        resource_version=resource_version,
        spec_patch={"suspend": writer.original_suspend},
    )


def quiesce_source_release_ownership(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    ownership: SourceReleaseOwnership,
    assert_authority: Callable[[], object],
    parent_writer: SourceParentWriter | None = None,
) -> dict[str, object]:
    """Suspend legacy reconcilers and stop every captured controller writer."""

    resolved_parent = parent_writer or capture_source_parent_writer(
        runner,
        kube_context=kube_context,
        ownership=ownership,
    )
    if resolved_parent is not None:
        _quiesce_source_parent_writer(
            runner,
            kube_context=kube_context,
            ownership=ownership,
            writer=resolved_parent,
            assert_authority=assert_authority,
        )
    for owner in ownership.owners:
        assert_authority()
        resource = "helmrelease" if owner.kind == "HelmRelease" else "deployment"
        current = _json_command(
            runner,
            _kubectl(
                kube_context,
                "-n",
                owner.namespace,
                "get",
                resource,
                owner.name,
                "-o",
                "json",
            ),
        )
        metadata = _mapping(current.get("metadata"))
        spec = _mapping(current.get("spec"))
        if str(metadata.get("uid") or "") != owner.uid:
            raise RuntimeError("legacy Soperator owner UID changed before quiescence")
        resource_version = str(metadata.get("resourceVersion") or "")
        if not resource_version:
            raise RuntimeError("legacy Soperator owner has no current resourceVersion")
        if owner.kind == "HelmRelease":
            if spec.get("suspend") is True:
                continue
            _cas_merge_patch(
                runner,
                kube_context=kube_context,
                namespace=owner.namespace,
                resource="helmrelease",
                name=owner.name,
                uid=owner.uid,
                resource_version=resource_version,
                spec_patch={"suspend": True},
            )
        elif owner.kind == "Deployment":
            if int(spec.get("replicas") or 0) == 0:
                continue
            _cas_merge_patch(
                runner,
                kube_context=kube_context,
                namespace=owner.namespace,
                resource="deployment",
                name=owner.name,
                uid=owner.uid,
                resource_version=resource_version,
                spec_patch={"replicas": 0},
            )
            _checked(
                runner,
                _kubectl(
                    kube_context,
                    "-n",
                    owner.namespace,
                    "rollout",
                    "status",
                    f"deployment/{owner.name}",
                    "--timeout=10m",
                ),
                timeout_seconds=660,
            )
    _verify_source_quiesced(
        runner,
        kube_context=kube_context,
        ownership=ownership,
    )
    return {
        "status": "quiesced",
        "ownershipReceiptSha256": ownership.receipt_sha256,
        "helmReleaseCount": sum(owner.kind == "HelmRelease" for owner in ownership.owners),
        "controllerCount": sum(owner.kind == "Deployment" for owner in ownership.owners),
        "parentWriterCount": int(resolved_parent is not None),
        **(
            {"parentWriter": resolved_parent.as_payload()}
            if resolved_parent is not None
            else {}
        ),
    }


def verify_target_single_writer(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    target_release: str,
) -> dict[str, object]:
    """Prove the one canonical direct-upstream controller is the Ready target writer."""

    item = _json_command(
        runner,
        _kubectl(
            kube_context,
            "-n",
            _CANONICAL_CONTROLLER_NAMESPACE,
            "get",
            "deployment",
            _CANONICAL_CONTROLLER_NAME,
            "-o",
            "json",
        ),
    )
    metadata = _mapping(item.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    spec = _mapping(item.get("spec"))
    status = _mapping(item.get("status"))
    identity = (
        str(metadata.get("namespace") or ""),
        str(metadata.get("name") or ""),
        str(metadata.get("uid") or ""),
    )
    replicas = int(spec.get("replicas") or 0)
    ready = int(status.get("readyReplicas") or 0)
    version = str(labels.get("app.kubernetes.io/version") or "").removeprefix("v")
    if identity[:2] != (_CANONICAL_CONTROLLER_NAMESPACE, _CANONICAL_CONTROLLER_NAME) or not identity[2]:
        raise RuntimeError("target Soperator controller identity is incomplete")
    if replicas <= 0 or version != target_release or ready != replicas:
        raise RuntimeError(
            "target Soperator controller is active without the exact target release "
            "and full readiness"
        )
    return {
        "status": "exclusive",
        "namespace": identity[0],
        "name": identity[1],
        "uid": identity[2],
        "release": target_release,
    }


def retire_source_release_ownership(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    ownership: SourceReleaseOwnership,
    assert_authority: Callable[[], object],
    parent_writer: SourceParentWriter | None = None,
) -> dict[str, object]:
    """Retire exact source HelmReleases and retain their parent suspended."""

    retired: list[str] = []
    adopted: list[str] = []
    for owner in ownership.owners:
        if owner.kind != "HelmRelease":
            continue
        current = _json_command_optional(
            runner,
            _kubectl(
                kube_context,
                "-n",
                owner.namespace,
                "get",
                "helmrelease",
                owner.name,
                "-o",
                "json",
            ),
        )
        if current is None:
            retired.append(f"{owner.namespace}/{owner.name}")
            continue
        metadata = _mapping(current.get("metadata"))
        spec = _mapping(current.get("spec"))
        labels = _mapping(metadata.get("labels"))
        if str(metadata.get("uid") or "") != owner.uid:
            raise RuntimeError("legacy Soperator HelmRelease identity changed before retirement")
        if labels.get("soperator.nebius.ai/release-graph") == "nebius-cxcli":
            adopted.append(f"{owner.namespace}/{owner.name}")
            continue
        if spec.get("suspend") is not True:
            raise RuntimeError("legacy Soperator HelmRelease resumed before retirement")
        _orphan_helmrelease(
            runner,
            kube_context=kube_context,
            resource=current,
            assert_authority=assert_authority,
        )
        retired.append(f"{owner.namespace}/{owner.name}")
    if parent_writer is not None:
        _verify_source_parent_writer_quiesced(
            runner,
            kube_context=kube_context,
            ownership=ownership,
            writer=parent_writer,
        )
    return {
        "status": "retired",
        "ownershipReceiptSha256": ownership.receipt_sha256,
        "retiredCount": len(retired),
        "adoptedCount": len(adopted),
        "retiredOwnersSha256": _sha256(retired),
        "adoptedOwnersSha256": _sha256(adopted),
        "retiredParentWriterCount": int(parent_writer is not None),
        **(
            {"parentWriterReceiptSha256": parent_writer.receipt_sha256}
            if parent_writer is not None
            else {}
        ),
    }


def restore_source_release_ownership(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    ownership: SourceReleaseOwnership,
    source_release: str,
    assert_authority: Callable[[], object],
    parent_writer: SourceParentWriter | None = None,
) -> dict[str, object]:
    """Resume source Flux only while no target release owner is visible."""

    restored: list[str] = []
    target_inventory = _json_command(
        runner,
        _kubectl(
            kube_context,
            "get",
            "helmreleases.helm.toolkit.fluxcd.io",
            "-A",
            "-o",
            "json",
        ),
    )
    target_owners: list[str] = []
    for item in _items(target_inventory):
        metadata = _mapping(item.get("metadata"))
        labels = _mapping(metadata.get("labels"))
        if labels.get("soperator.nebius.ai/release-graph") != "nebius-cxcli":
            continue
        namespace = str(metadata.get("namespace") or "")
        name = str(metadata.get("name") or "")
        uid = str(metadata.get("uid") or "")
        if not all((namespace, name, uid)):
            raise RuntimeError("target Soperator HelmRelease identity is incomplete")
        target_owners.append(f"{namespace}/{name}")
    if target_owners:
        raise RuntimeError(
            "target Soperator release ownership is visible; automatic source rollback "
            "is unsafe and the protected upgrade must resume forward"
        )
    for owner in ownership.owners:
        if owner.kind != "HelmRelease":
            continue
        resource = "helmrelease" if owner.kind == "HelmRelease" else "deployment"
        current = _json_command(
            runner,
            _kubectl(
                kube_context,
                "-n",
                owner.namespace,
                "get",
                resource,
                owner.name,
                "-o",
                "json",
            ),
        )
        metadata = _mapping(current.get("metadata"))
        if str(metadata.get("uid") or "") != owner.uid:
            raise RuntimeError("source owner UID changed; automatic rollback is unsafe")
        spec = _mapping(current.get("spec"))
        resource_version = str(metadata.get("resourceVersion") or "")
        if owner.original_suspend is None and "suspend" not in spec:
            continue
        assert_authority()
        _cas_merge_patch(
            runner,
            kube_context=kube_context,
            namespace=owner.namespace,
            resource=resource,
            name=owner.name,
            uid=owner.uid,
            resource_version=resource_version,
            spec_patch={"suspend": owner.original_suspend},
        )
        restored.append(f"{owner.kind}:{owner.namespace}/{owner.name}")
    if parent_writer is not None:
        _restore_source_parent_writer(
            runner,
            kube_context=kube_context,
            ownership=ownership,
            writer=parent_writer,
            assert_authority=assert_authority,
        )
        restored.append(
            f"Kustomization:{parent_writer.namespace}/{parent_writer.name}"
        )
    _wait_source_controllers_restored(
        runner,
        kube_context=kube_context,
        ownership=ownership,
        source_release=source_release,
        assert_authority=assert_authority,
    )
    return {
        "status": "restored",
        "ownershipReceiptSha256": ownership.receipt_sha256,
        "restoredCount": len(restored),
        "restoredOwnersSha256": _sha256(restored),
    }


def _wait_source_controllers_restored(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    ownership: SourceReleaseOwnership,
    source_release: str,
    assert_authority: Callable[[], object],
) -> None:
    controllers = [owner for owner in ownership.owners if owner.kind == "Deployment"]
    for attempt in range(121):
        pending: list[str] = []
        for owner in controllers:
            current = _json_command(
                runner,
                _kubectl(
                    kube_context,
                    "-n",
                    owner.namespace,
                    "get",
                    "deployment",
                    owner.name,
                    "-o",
                    "json",
                ),
            )
            metadata = _mapping(current.get("metadata"))
            labels = _mapping(metadata.get("labels"))
            spec = _mapping(current.get("spec"))
            status = _mapping(current.get("status"))
            desired = int(owner.original_replicas or 0)
            if (
                str(metadata.get("uid") or "") != owner.uid
                or str(labels.get("app.kubernetes.io/version") or "").removeprefix("v")
                != source_release
                or int(spec.get("replicas") or 0) != desired
                or int(status.get("readyReplicas") or 0) != desired
            ):
                pending.append(f"{owner.namespace}/{owner.name}")
        if not pending:
            return
        if attempt == 120:
            raise RuntimeError(
                "source Soperator controllers did not regain exact release ownership: "
                + ", ".join(pending)
            )
        assert_authority()
        time.sleep(5)


def _verify_source_quiesced(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    ownership: SourceReleaseOwnership,
    allow_retired_helmreleases: bool = False,
    parent_writer: SourceParentWriter | None = None,
    adopted_target_release: str = "",
) -> None:
    if parent_writer is not None:
        _verify_source_parent_writer_quiesced(
            runner,
            kube_context=kube_context,
            ownership=ownership,
            writer=parent_writer,
        )
    for owner in ownership.owners:
        command = _kubectl(
            kube_context,
            "-n",
            owner.namespace,
            "get",
            "helmrelease" if owner.kind == "HelmRelease" else "deployment",
            owner.name,
            "-o",
            "json",
        )
        payload = (
            _json_command_optional(runner, command)
            if owner.kind == "HelmRelease" and allow_retired_helmreleases
            else _json_command(runner, command)
        )
        if payload is None:
            continue
        metadata = _mapping(payload.get("metadata"))
        if str(metadata.get("uid") or "") != owner.uid:
            labels = _mapping(metadata.get("labels"))
            if (
                owner.kind == "HelmRelease"
                and allow_retired_helmreleases
                and labels.get("soperator.nebius.ai/release-graph") == "nebius-cxcli"
            ):
                continue
            raise RuntimeError("legacy Soperator owner UID changed during quiescence")
        if owner.kind == "HelmRelease":
            labels = _mapping(metadata.get("labels"))
            if (
                allow_retired_helmreleases
                and labels.get("soperator.nebius.ai/release-graph") == "nebius-cxcli"
            ):
                continue
            if _mapping(payload.get("spec")).get("suspend") is not True:
                raise RuntimeError("legacy Soperator HelmRelease is not suspended")
        else:
            spec = _mapping(payload.get("spec"))
            status = _mapping(payload.get("status"))
            if int(spec.get("replicas") or 0) != 0 or int(status.get("readyReplicas") or 0) != 0:
                if adopted_target_release and _source_controller_is_adopted_by_target(
                    runner,
                    kube_context=kube_context,
                    controller=payload,
                    target_release=adopted_target_release,
                ):
                    continue
                raise RuntimeError("legacy Soperator controller writer is not quiesced")


def _source_controller_is_adopted_by_target(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    controller: Mapping[str, object],
    target_release: str,
) -> bool:
    """Prove an in-place controller UID now belongs to the exact target release."""

    normalized_release = str(target_release or "").strip().removeprefix("v")
    metadata = _mapping(controller.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    annotations = _mapping(metadata.get("annotations"))
    controller_namespace = str(metadata.get("namespace") or "").strip()
    target_owner_name = str(labels.get("helm.toolkit.fluxcd.io/name") or "").strip()
    target_owner_namespace = str(
        labels.get("helm.toolkit.fluxcd.io/namespace") or ""
    ).strip()
    helm_release_name = str(annotations.get("meta.helm.sh/release-name") or "").strip()
    helm_release_namespace = str(
        annotations.get("meta.helm.sh/release-namespace") or ""
    ).strip()
    if (
        not normalized_release
        or controller_namespace != _CANONICAL_CONTROLLER_NAMESPACE
        or str(metadata.get("name") or "") != _CANONICAL_CONTROLLER_NAME
        or labels.get("app.kubernetes.io/managed-by") != "Helm"
        or str(labels.get("app.kubernetes.io/version") or "").removeprefix("v")
        != normalized_release
        or not str(labels.get("helm.sh/chart") or "").startswith(
            f"helm-soperator-{normalized_release}"
        )
        or not all(
            (
                target_owner_name,
                target_owner_namespace,
                helm_release_name,
                helm_release_namespace,
            )
        )
        or helm_release_namespace != controller_namespace
    ):
        return False
    target_owner = _json_command_optional(
        runner,
        _kubectl(
            kube_context,
            "-n",
            target_owner_namespace,
            "get",
            "helmrelease",
            target_owner_name,
            "-o",
            "json",
        ),
    )
    if target_owner is None:
        return False
    owner_metadata = _mapping(target_owner.get("metadata"))
    owner_labels = _mapping(owner_metadata.get("labels"))
    owner_annotations = _mapping(owner_metadata.get("annotations"))
    owner_spec = _mapping(target_owner.get("spec"))
    chart_ref = _mapping(owner_spec.get("chartRef"))
    return (
        str(owner_metadata.get("namespace") or "") == target_owner_namespace
        and str(owner_metadata.get("name") or "") == target_owner_name
        and bool(str(owner_metadata.get("uid") or "").strip())
        and owner_labels.get("soperator.nebius.ai/release-graph") == "nebius-cxcli"
        and str(owner_labels.get("app.kubernetes.io/version") or "").removeprefix("v")
        == normalized_release
        and owner_annotations.get("meta.helm.sh/release-name") == helm_release_name
        and owner_annotations.get("meta.helm.sh/release-namespace")
        == helm_release_namespace
        and owner_spec.get("targetNamespace") == controller_namespace
        and owner_spec.get("releaseName") == helm_release_name
        and chart_ref.get("kind") == "OCIRepository"
        and chart_ref.get("namespace") == target_owner_namespace
        and bool(str(chart_ref.get("name") or "").strip())
    )


def verify_source_release_quiesced(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    ownership: SourceReleaseOwnership,
    parent_writer: SourceParentWriter | None = None,
    adopted_target_release: str = "",
) -> dict[str, object]:
    """Read-only postcondition for a completed source-owner quiescence."""

    _verify_source_quiesced(
        runner,
        kube_context=kube_context,
        ownership=ownership,
        allow_retired_helmreleases=True,
        parent_writer=parent_writer,
        adopted_target_release=adopted_target_release,
    )
    return {
        "status": "quiesced",
        "ownershipReceiptSha256": ownership.receipt_sha256,
        "parentWriterCount": int(parent_writer is not None),
    }


def verify_source_release_retired(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    ownership: SourceReleaseOwnership,
    parent_writer: SourceParentWriter | None = None,
    adopted_target_release: str = "",
) -> dict[str, object]:
    """Read-only postcondition for exact legacy HelmRelease retirement."""

    for owner in ownership.owners:
        if owner.kind == "Deployment":
            continue
        current = _json_command_optional(
            runner,
            _kubectl(
                kube_context,
                "-n",
                owner.namespace,
                "get",
                "helmrelease",
                owner.name,
                "-o",
                "json",
            ),
        )
        if current is None:
            continue
        metadata = _mapping(current.get("metadata"))
        labels = _mapping(metadata.get("labels"))
        if labels.get("soperator.nebius.ai/release-graph") != "nebius-cxcli":
            raise RuntimeError("legacy Soperator HelmRelease was not retired or adopted")
    _verify_source_quiesced(
        runner,
        kube_context=kube_context,
        ownership=ownership,
        allow_retired_helmreleases=True,
        parent_writer=parent_writer,
        adopted_target_release=adopted_target_release,
    )
    return {
        "status": "retired",
        "ownershipReceiptSha256": ownership.receipt_sha256,
        "retiredParentWriterCount": int(parent_writer is not None),
    }


def _frozen_graph_release_rows(
    graph_contract: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    if graph_contract.get("schema") != "nebius-cxcli.soperator-release-graph.v2":
        raise RuntimeError("protected Soperator ownership requires the current frozen graph")
    raw_rows = graph_contract.get("releases")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise RuntimeError("protected Soperator frozen graph has no release identities")
    rows: list[Mapping[str, object]] = []
    identities: set[tuple[str, str]] = set()
    for raw_row in raw_rows:
        if not isinstance(raw_row, Mapping):
            raise RuntimeError("protected Soperator frozen graph has a malformed release")
        required = (
            str(raw_row.get("namespace") or ""),
            str(raw_row.get("releaseName") or ""),
            str(raw_row.get("sourceKind") or ""),
            str(raw_row.get("sourceName") or ""),
        )
        if not all(required) or required[2] not in {"OCIRepository", "HelmChart"}:
            raise RuntimeError("protected Soperator frozen graph has an incomplete release")
        identity = required[:2]
        if identity in identities:
            raise RuntimeError("protected Soperator frozen graph has duplicate releases")
        identities.add(identity)
        rows.append(raw_row)
    if sum(row.get("isMain") is True for row in rows) != 1:
        raise RuntimeError("protected Soperator frozen graph has no unique main workload")
    return tuple(sorted(rows, key=lambda row: (str(row["namespace"]), str(row["releaseName"]))))


def _owner(
    item: Mapping[str, object],
    *,
    kind: str,
    original_suspend: bool | None = None,
    original_replicas: int | None = None,
) -> SourceReleaseOwner:
    metadata = _mapping(item.get("metadata"))
    required = {
        "namespace": str(metadata.get("namespace") or "").strip(),
        "name": str(metadata.get("name") or "").strip(),
        "uid": str(metadata.get("uid") or "").strip(),
        "resourceVersion": str(metadata.get("resourceVersion") or "").strip(),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"legacy Soperator {kind} lacks {', '.join(missing)}")
    return SourceReleaseOwner(
        kind=kind,
        namespace=required["namespace"],
        name=required["name"],
        uid=required["uid"],
        resource_version=required["resourceVersion"],
        original_suspend=original_suspend,
        original_replicas=original_replicas,
    )


def _kubectl(kube_context: str, *args: str) -> tuple[str, ...]:
    return ("kubectl", "--context", kube_context, *args)


def _checked(
    runner: OwnershipCommandRunner,
    args: Sequence[str],
    *,
    timeout_seconds: int = 120,
    input_text: str | None = None,
) -> OwnershipCommandResult:
    result = runner(
        args,
        timeout_seconds=timeout_seconds,
        check=False,
        **({"input_text": input_text} if input_text is not None else {}),
    )
    if result.returncode != 0:
        detail = str(result.stderr or result.stdout or "").strip().splitlines()
        raise RuntimeError(
            "Soperator ownership command failed" + (f": {detail[0]}" if detail else "")
        )
    return result


def _cas_merge_patch(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    namespace: str,
    resource: str,
    name: str,
    uid: str,
    resource_version: str,
    spec_patch: Mapping[str, object] | None = None,
    metadata_patch: Mapping[str, object] | None = None,
) -> None:
    """Mutate one exact owner through Kubernetes UID/resourceVersion CAS."""

    if not uid or not resource_version:
        raise RuntimeError("Soperator ownership CAS identity is incomplete")
    if metadata_patch and {"uid", "resourceVersion"}.intersection(metadata_patch):
        raise RuntimeError("Soperator ownership CAS metadata patch overrides identity")
    metadata: dict[str, object] = {
        "uid": uid,
        "resourceVersion": resource_version,
        **dict(metadata_patch or {}),
    }
    payload: dict[str, object] = {"metadata": metadata}
    if spec_patch is not None:
        payload["spec"] = dict(spec_patch)
    _checked(
        runner,
        _kubectl(
            kube_context,
            "-n",
            namespace,
            "patch",
            resource,
            name,
            "--type=merge",
            "-p",
            json.dumps(payload, separators=(",", ":")),
        ),
    )


def _orphan_helmrelease(
    runner: OwnershipCommandRunner,
    *,
    kube_context: str,
    resource: Mapping[str, object],
    assert_authority: Callable[[], object],
) -> None:
    metadata = _mapping(resource.get("metadata"))
    namespace = str(metadata.get("namespace") or "")
    name = str(metadata.get("name") or "")
    uid = str(metadata.get("uid") or "")
    resource_version = str(metadata.get("resourceVersion") or "")
    api_version = str(resource.get("apiVersion") or "")
    if not all((namespace, name, uid, resource_version)) or "/" not in api_version:
        raise RuntimeError("Soperator HelmRelease orphan identity is incomplete")
    finalizers = metadata.get("finalizers")
    if isinstance(finalizers, list) and finalizers:
        assert_authority()
        _cas_merge_patch(
            runner,
            kube_context=kube_context,
            namespace=namespace,
            resource="helmrelease",
            name=name,
            uid=uid,
            resource_version=resource_version,
            metadata_patch={"finalizers": []},
        )
    group, version = api_version.split("/", 1)
    raw_path = (
        f"/apis/{quote(group, safe='')}/{quote(version, safe='')}/namespaces/"
        f"{quote(namespace, safe='')}/helmreleases/{quote(name, safe='')}"
    )
    assert_authority()
    _checked(
        runner,
        _kubectl(kube_context, "delete", "--raw", raw_path, "-f", "-"),
        input_text=json.dumps(
            {
                "apiVersion": "meta.k8s.io/v1",
                "kind": "DeleteOptions",
                "propagationPolicy": "Orphan",
                "preconditions": {"uid": uid},
            },
            separators=(",", ":"),
        ),
    )


def _json_command(
    runner: OwnershipCommandRunner,
    args: Sequence[str],
) -> Mapping[str, object]:
    result = _checked(runner, args)
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Soperator ownership command returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("Soperator ownership command returned a non-object")
    return payload


def _json_command_optional(
    runner: OwnershipCommandRunner,
    args: Sequence[str],
) -> Mapping[str, object] | None:
    # kubectl's default human-readable NotFound error is not a stable machine
    # contract. Ask it to suppress absence explicitly, then treat its empty
    # successful response as the optional object not existing.
    result = runner(
        (*args, "--ignore-not-found=true"),
        timeout_seconds=120,
        check=False,
    )
    if result.returncode != 0:
        for raw_payload in (result.stdout, result.stderr):
            try:
                failure = json.loads(str(raw_payload or "").strip())
            except json.JSONDecodeError:
                continue
            if (
                isinstance(failure, Mapping)
                and failure.get("kind") == "Status"
                and failure.get("reason") == "NotFound"
                and failure.get("code") == 404
            ):
                return None
        detail = str(result.stderr or result.stdout or "").strip().splitlines()
        raise RuntimeError(
            "Soperator ownership command failed" + (f": {detail[0]}" if detail else "")
        )
    raw_payload = str(result.stdout or "").strip()
    if not raw_payload:
        return None
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Soperator ownership command returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("Soperator ownership command returned a non-object")
    return payload


def _items(value: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    items = value.get("items")
    if not isinstance(items, list):
        raise RuntimeError("Soperator ownership inventory has no item list")
    return tuple(item for item in items if isinstance(item, Mapping))


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = [
    "SOPERATOR_RELEASE_OWNERSHIP_SCHEMA",
    "SOPERATOR_SOURCE_PARENT_WRITER_SCHEMA",
    "SourceParentWriter",
    "SourceReleaseOwner",
    "SourceReleaseOwnership",
    "capture_source_parent_writer",
    "capture_source_release_ownership",
    "quiesce_source_release_ownership",
    "retire_source_release_ownership",
    "restore_source_release_ownership",
    "source_parent_writer_from_payload",
    "source_release_ownership_from_payload",
    "verify_target_single_writer",
    "verify_source_release_quiesced",
    "verify_source_release_retired",
]
