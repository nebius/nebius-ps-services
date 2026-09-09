"""Required upstream REST dependency for SConfig and native checks."""

from __future__ import annotations

from typing import Any


def materialize_soperator_rest(values: dict[str, Any]) -> None:
    nodes = values.setdefault("slurmNodes", {})
    if not isinstance(nodes, dict):
        raise ValueError("slurmNodes must be a mapping")
    rest = nodes.setdefault("rest", {})
    if not isinstance(rest, dict) or rest.get("enabled", True) is not True:
        raise ValueError("Slurm REST is required by upstream SConfig and native checks")
    rest["enabled"] = True
    size = rest.get("size", 2)
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ValueError("Required Slurm REST must have at least one replica")
    controller = nodes.setdefault("controller", {})
    if not isinstance(controller, dict):
        raise ValueError("Slurm controller must be a mapping")
    metrics = controller.setdefault("openMetrics", {})
    if not isinstance(metrics, dict):
        raise ValueError("Controller OpenMetrics must be a mapping")
    # Both pinned releases include slurm.conf from slurm_rest.conf. The REST
    # daemon rejects the controller-only MetricsType directive. The upstream
    # exporter remains independent of this controller-native endpoint.
    metrics["enabled"] = False
