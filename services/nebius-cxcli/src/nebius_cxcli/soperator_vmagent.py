"""Size upstream write queues on rendered values, without acquiring authored ownership."""

from collections.abc import Mapping
from typing import Any


def materialize_vmagent_queues(values: dict[str, Any]) -> None:
    observability = values.get("observability")
    if not isinstance(observability, dict) or observability.get("enabled") is False:
        return
    capacity = 0
    nodesets = values.get("nodesets", [])
    if not isinstance(nodesets, list):
        raise ValueError("vmagent sizing requires a NodeSet list")
    for node in nodesets:
        if not isinstance(node, Mapping):
            raise ValueError("vmagent sizing requires valid worker NodeSets")
        # NodeSets are exclusively workers; services have separate replica settings.
        replicas = node.get("replicas", 0)
        if type(replicas) is not int or replicas < 0:
            raise ValueError("vmagent sizing requires nonnegative integral worker capacity")
        capacity += replicas
    spec = observability
    for name in ("vmStack", "values", "vmagent", "spec", "extraArgs"):
        child = spec.setdefault(name, {})
        if not isinstance(child, dict):
            raise ValueError(f"Soperator vmagent {name} must be a mapping")
        spec = child
    if "remoteWrite.queues" not in spec:
        spec["remoteWrite.queues"] = str(2 + capacity // 60)
