"""Cloud provenance and reference checks for destroy-owned disks and GPU clusters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .destroy_cloud import DestroyCloud

CSI_CLUSTER_LABEL = "compute.csi.nebius.com/cluster-id"


def references(value: Any, identifiers: set[str]) -> set[str]:
    if isinstance(value, Mapping):
        return set().union(*(references(v, identifiers) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(references(v, identifiers) for v in value))
    return {value} if isinstance(value, str) and value in identifiers else set()


def gpu_id(spec: Mapping[str, Any]) -> str | None:
    value = spec.get("gpu_cluster")
    if value is None or (isinstance(value, Mapping) and not value):
        return None
    if not isinstance(value, Mapping) or not isinstance(value.get("id"), str) or not value["id"]:
        raise RuntimeError("Destroy GPU cluster reference is incomplete")
    return value["id"]


def identity(resource: Mapping[str, Any], identifier: str, project_id: str) -> None:
    meta = resource.get("metadata", {})
    if meta.get("id") != identifier or meta.get("parent_id") != project_id:
        raise RuntimeError("Destroy dependent resource identity or project differs")
    if not isinstance(resource.get("spec"), Mapping) or not isinstance(
        resource.get("status"), Mapping
    ):
        raise RuntimeError("Destroy dependent resource inventory is incomplete")


def pvc_identity(disk: Mapping[str, Any], project_id: str, cluster_id: str) -> dict[str, Any]:
    meta = disk.get("metadata", {})
    identifier = meta.get("id")
    identity(disk, identifier, project_id)
    labels = meta.get("labels", {})
    fields = {
        "cluster_id": CSI_CLUSTER_LABEL,
        "csi_volume_name": "CSIVolumeName",
        "pv_name": "kubernetes.io/created-for/pv/name",
        "pvc_name": "kubernetes.io/created-for/pvc/name",
        "pvc_namespace": "kubernetes.io/created-for/pvc/namespace",
    }
    result = {key: labels.get(label) for key, label in fields.items()}
    if (
        any(not isinstance(v, str) or not v for v in result.values())
        or result["cluster_id"] != cluster_id
        or result["pv_name"] != result["csi_volume_name"]
    ):
        raise RuntimeError(f"PVC disk {identifier} has ambiguous cluster/PVC ownership")
    size = disk["status"].get("size_bytes") or disk["spec"].get("size_bytes")
    if not size:
        size = int(disk["spec"].get("size_gibibytes", 0)) * 1024**3
    if isinstance(size, bool) or not isinstance(size, (int, str)) or int(size) <= 0:
        raise RuntimeError(f"PVC disk {identifier} size is unavailable")
    return {"id": identifier, "project_id": project_id, **result, "size_bytes": int(size)}


def attachment_ids(disk: Mapping[str, Any]) -> set[str]:
    status = disk["status"]
    rw, ro = status.get("read_write_attachment", ""), status.get("read_only_attachments", [])
    if (
        not isinstance(rw, str)
        or not isinstance(ro, list)
        or any(not isinstance(v, str) or not v for v in ro)
    ):
        raise RuntimeError("Destroy disk attachment evidence is malformed")
    return set(ro) | ({rw} if rw else set())


def check_disk(
    cloud: DestroyCloud,
    row: Mapping[str, Any],
    *,
    delete: bool,
    workers: set[str],
    resource: Mapping[str, Any] | None = None,
) -> None:
    disk = resource if resource is not None else cloud.get("disk", row["id"])
    assert disk is not None
    if pvc_identity(disk, cloud.project_id, row["cluster_id"]) != row:
        raise RuntimeError(f"PVC disk {row['id']} ownership changed after approval")
    if disk["status"].get("managed_by"):
        raise RuntimeError("Instance-managed disks cannot be approved as PVC disks")
    if delete:
        if disk["spec"].get("forbid_deletion", False):
            raise RuntimeError(f"PVC disk {row['id']} has deletion protection")
        locks = disk["status"].get("lock_state", {})
        if not isinstance(locks, Mapping) or any(locks.values()):
            raise RuntimeError(f"PVC disk {row['id']} has an active image or snapshot lock")
        if attachment_ids(disk) - workers:
            raise RuntimeError(f"PVC disk {row['id']} has outside or remaining attachments")


def reference_index(
    cloud: DestroyCloud,
    identifiers: set[str],
    *,
    instances: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {identifier: set() for identifier in identifiers}
    if not identifiers:
        return result
    for instance in (
        instances if instances is not None else cloud.list("instance", cloud.project_id)
    ):
        if not isinstance(instance.get("spec"), Mapping):
            raise RuntimeError("Destroy instance reference inventory is incomplete")
        for identifier in references(instance, identifiers):
            result[identifier].add(instance["metadata"]["id"])
    return result


def check_other_templates(cloud: DestroyCloud, identifiers: set[str], cluster_id: str) -> None:
    if not identifiers:
        return
    for cluster in cloud.list("cluster", cloud.project_id):
        if cluster["metadata"]["id"] == cluster_id:
            continue
        for group in cloud.list("node_group", cluster["metadata"]["id"]):
            template = group.get("spec", {}).get("template")
            if not isinstance(template, Mapping):
                raise RuntimeError("Destroy other node-group template inventory is incomplete")
            if references(template, identifiers):
                raise RuntimeError("Destroy resource is referenced by another cluster template")


def check_gpu(cloud: DestroyCloud, identifier: str, workers: set[str]) -> None:
    gpu = cloud.get("gpu_cluster", identifier)
    assert gpu is not None
    identity(gpu, identifier, cloud.project_id)
    members = gpu["status"].get("instances", [])
    if not isinstance(members, list) or any(not isinstance(v, str) or not v for v in members):
        raise RuntimeError("Destroy GPU cluster membership is incomplete")
    if set(members) - workers or (not workers and gpu["status"].get("reconciling", False)):
        raise RuntimeError(f"GPU cluster {identifier} has outside members or is still reconciling")


def discover(
    cloud: DestroyCloud,
    cluster_id: str,
    groups: Sequence[Mapping[str, Any]],
    instances: Sequence[Mapping[str, Any]],
    workers: set[str],
    *,
    preserve_pvc_disks: bool,
    managed_gpu_ids: Sequence[str],
) -> dict[str, Any]:
    group_gpus = {g["metadata"]["id"]: gpu_id(g["spec"]["template"]) for g in groups}
    gpu_ids = set(managed_gpu_ids) | {v for v in group_gpus.values() if v}
    boot_ids: set[str] = set()
    for instance in instances:
        if instance["metadata"]["id"] in workers:
            gpu = gpu_id(instance["spec"])
            if gpu:
                gpu_ids.add(gpu)
        boot = instance.get("spec", {}).get("boot_disk", {}).get("existing_disk", {}).get("id")
        if boot:
            boot_ids.add(boot)
    disks = cloud.list("disk", cloud.project_id)
    disk_ids = {d["metadata"]["id"] for d in disks}
    refs = reference_index(cloud, disk_ids | gpu_ids, instances=instances)
    pvc, unclassified, excluded = {}, [], []
    for disk in disks:
        identifier = disk["metadata"]["id"]
        labels = disk["metadata"].get("labels", {})
        if not isinstance(labels, Mapping):
            raise RuntimeError("Destroy disk ownership labels are malformed")
        if identifier in boot_ids or disk.get("status", {}).get("managed_by"):
            if labels.get(CSI_CLUSTER_LABEL) == cluster_id:
                excluded.append(identifier)
            continue
        if labels.get(CSI_CLUSTER_LABEL) != cluster_id:
            if (refs[identifier] | attachment_ids(disk)) & workers:
                if not preserve_pvc_disks:
                    raise RuntimeError(f"Attached disk {identifier} has unproven PVC ownership")
                unclassified.append(identifier)
            continue
        row = pvc_identity(disk, cloud.project_id, cluster_id)
        check_disk(cloud, row, delete=not preserve_pvc_disks, workers=workers, resource=disk)
        if not preserve_pvc_disks and refs[identifier] - workers:
            raise RuntimeError(f"PVC disk {identifier} is referenced by an outside instance")
        pvc[identifier] = row
    for identifier in sorted(gpu_ids):
        check_gpu(cloud, identifier, workers)
        if refs[identifier] - workers:
            raise RuntimeError(f"GPU cluster {identifier} is referenced by an outside instance")
    candidates = gpu_ids | (set(pvc) if not preserve_pvc_disks else set())
    check_other_templates(cloud, candidates, cluster_id)
    return {
        "gpu_cluster_ids": sorted(gpu_ids),
        "group_gpu_clusters": group_gpus,
        "managed_gpu_cluster_ids": sorted(set(managed_gpu_ids)),
        "pvc_disk_ids": sorted(pvc),
        "pvc_disks": pvc,
        "unclassified_disk_ids": sorted(unclassified),
        "excluded_disk_ids": sorted(excluded),
    }


def check_remaining_references(
    value: Any, inventory: Mapping[str, Any], preserve_pvc_disks: bool
) -> None:
    identifiers = {
        inventory["cluster_id"],
        *inventory["node_group_ids"],
        *inventory["gpu_cluster_ids"],
    }
    if not preserve_pvc_disks:
        identifiers.update(inventory["pvc_disk_ids"])
    if references(value, identifiers):
        raise RuntimeError(
            "Remaining configuration references an approved cluster, node group, GPU cluster or PVC disk"
        )


def verify_pvc_scope(
    cloud: DestroyCloud,
    frozen: Mapping[str, Any],
    current: Mapping[str, Any],
    preserve: bool,
) -> None:
    if any(
        row != frozen["pvc_disks"].get(identifier)
        for identifier, row in current["pvc_disks"].items()
    ):
        raise RuntimeError("Destroy cloud inventory changed; approved PVC scope cannot expand")
    if preserve and current["pvc_disk_ids"] != frozen["pvc_disk_ids"]:
        raise RuntimeError("A preserved PVC disk disappeared after planning")
    for identifier in set(frozen["pvc_disk_ids"]) - set(current["pvc_disk_ids"]):
        if cloud.get("disk", identifier, absent_ok=True) is not None:
            raise RuntimeError("An approved PVC disk changed classification after planning")


def detached_references(
    cloud: DestroyCloud, inventory: Mapping[str, Any], preserve_pvc_disks: bool
) -> None:
    identifiers = set(inventory["gpu_cluster_ids"])
    if not preserve_pvc_disks:
        identifiers.update(inventory["pvc_disk_ids"])
    if any(reference_index(cloud, identifiers).values()):
        raise RuntimeError("Approved GPU/PVC resources still have instance references")
    check_other_templates(cloud, identifiers, inventory["cluster_id"])


def verify_pvc_postconditions(
    cloud: DestroyCloud, inventory: Mapping[str, Any], preserve: bool
) -> None:
    for identifier in inventory["pvc_disk_ids"]:
        disk = cloud.get("disk", identifier, absent_ok=not preserve)
        if preserve:
            check_disk(
                cloud,
                inventory["pvc_disks"][identifier],
                delete=False,
                workers=set(),
                resource=disk,
            )
        elif disk is not None:
            raise RuntimeError(f"Approved PVC disk {identifier} is not absent")
    if not preserve:
        unexpected = sorted(
            disk["metadata"]["id"]
            for disk in cloud.list("disk", cloud.project_id)
            if disk["metadata"].get("labels", {}).get(CSI_CLUSTER_LABEL) == inventory["cluster_id"]
            and disk["metadata"]["id"] not in inventory["pvc_disk_ids"]
            and disk["metadata"]["id"] not in inventory["excluded_disk_ids"]
        )
        if unexpected:
            raise RuntimeError(
                "Unapproved PVC disks appeared; separate cleanup is required before resuming: "
                + ", ".join(unexpected)
            )
