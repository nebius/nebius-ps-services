from __future__ import annotations

import copy
from types import SimpleNamespace

from nebius_cxcli.deployment_state import ObjectVersion
from nebius_cxcli.destroy import build_destroy_receipt
from nebius_cxcli.destroy_state import DestroyState

SETTINGS = SimpleNamespace(
    project_id="project-a",
    bucket="bucket-a",
    key="state-a",
    endpoint="https://storage.example.invalid",
)


class Store:
    def __init__(self):
        self.values = {}
        self.writes = []

    def read(self, key):
        return copy.deepcopy(self.values.get(key))

    def write(self, key, value, *, etag):
        previous = self.values.get(key)
        if etag != (previous.etag if previous else None):
            raise RuntimeError("lost CAS")
        version = str(len(self.writes) + 1)
        self.values[key] = ObjectVersion(copy.deepcopy(value), version)
        self.writes.append((key, copy.deepcopy(value)))
        return version


def receipt(*, delete_sfs=False, preserve_pvc_disks=False, ownership="managed", **overrides):
    inventory = {
        "project_id": "project-a",
        "cluster_id": "mk8scluster-a",
        "node_group_ids": ["group-a"],
        "filesystem_ids": ["filesystem-a"],
        "worker_ids": ["worker-a"],
        "group_filesystems": {"group-a": ["filesystem-a"]},
        "vm_nfs": None,
        "gpu_cluster_ids": [],
        "managed_gpu_cluster_ids": [],
        "group_gpu_clusters": {"group-a": None},
        "pvc_disk_ids": [],
        "pvc_disks": {},
        "unclassified_disk_ids": [],
        "excluded_disk_ids": [],
    }
    data = dict(
        target_ref="cluster-a",
        ownership=ownership,
        project_id="project-a",
        cluster_id="mk8scluster-a",
        delete_sfs=delete_sfs,
        preserve_pvc_disks=preserve_pvc_disks,
        inventory=inventory,
        config_sha256="sha256:" + "a" * 64,
        post_cleanup_config_sha256="sha256:" + "b" * 64,
        backend=DestroyState(Store(), SETTINGS).backend,
        generation={},
        terraform={},
        destroy_inventory=["mk8s:mk8scluster-a"],
        preserve_inventory=["sfs:filesystem-a"],
    )
    data.update(overrides)
    return build_destroy_receipt(**data)


class Cloud:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.present = {"mk8scluster-a", "filesystem-a"}
        self.fail_submit = False
        self.fail_poll = False
        self.fail_scope = False
        self.calls = []
        self.project_id = "project-a"
        self.data = {}
        self.instances = []
        self.assert_held = lambda: None

    def get(self, kind, identifier, *, absent_ok=False):
        if identifier in self.present:
            return copy.deepcopy(self.data.get(identifier, {"metadata": {"id": identifier}}))
        if absent_ok:
            return None
        raise RuntimeError("not found")

    def list(self, kind, parent):
        if kind == "instance":
            return copy.deepcopy(self.instances)
        return [
            copy.deepcopy(v)
            for (k, v) in self.data.items()
            if k in self.present and k.startswith(kind + "-")
        ]

    def poll_delete_once(self, kind, identifier, operation):
        self.poll_delete(kind, identifier, operation)
        return True

    def verify_scope(self, inventory, **kwargs):
        self.events.append("scope")
        if self.fail_scope:
            raise RuntimeError("scope changed")

    def inventory(self, identifier, **kwargs):
        return copy.deepcopy(receipt().approved["inventory"])

    def submit_delete(self, kind, identifier, key):
        self.events.append("submit:" + kind)
        self.calls.append((kind, identifier, key))
        if self.fail_submit:
            raise TimeoutError("lost response")
        return "operation-" + kind

    def recover_operation(self, kind, identifier, key):
        self.events.append("recover:" + kind)
        return "operation-" + kind

    def poll_delete(self, kind, identifier, operation):
        self.events.append("poll:" + kind)
        if self.fail_poll:
            raise TimeoutError("poll timeout")
        self.present.discard(identifier)

    def verify_cluster_absent(self, inventory):
        self.events.append("cluster-absence")
        if inventory["cluster_id"] in self.present:
            raise RuntimeError("cluster exists")

    def check_exclusive(self, ids, cluster, workers):
        self.events.append("exclusive")

    def check_filesystem(self, identifier, *, delete, detached=False):
        self.events.append("filesystem-check")
        if identifier not in self.present:
            raise RuntimeError("filesystem absent")
