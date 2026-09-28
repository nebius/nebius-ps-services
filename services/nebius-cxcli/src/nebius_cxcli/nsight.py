"""Nsight catalog values and storage contracts shared by both installation paths."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .component_instances import component_instance_id, component_type_id
from .runtime_config import to_plain_data

CHART_VERSION = "2026.4.1"
CHART_NAME = "nsight-streamer"
CHART_REPOSITORY = "https://helm.ngc.nvidia.com/nvidia/devtools"
REPORTS_PATH = "/data/nsight-reports"
REPORTS_MOUNT = "/mnt/reports"
APP_TOOLS = {"nsight-streamer": "nsys", "nsight-streamer-ncu": "ncu"}
TOOL_VERSIONS = {"nsys": "2026.4.1", "ncu": "2026.2.1"}
TOOL_PORTS = {"nsys": (30080, 30478), "ncu": (30081, 30479)}
VIEWER_RESOURCES = {
    "requests": {"cpu": "1", "memory": "2Gi"},
    "limits": {"cpu": "4", "memory": "8Gi"},
}
VIEWER_IMAGES = {
    "nsys": "nvcr.io/nvidia/devtools/nsight-streamer-nsys:2026.4.1@sha256:146f078413d6826409b9bb40a181fdcce73158751a5d387a535c71a099f8a170",
    "ncu": "nvcr.io/nvidia/devtools/nsight-streamer-ncu:2026.2.1@sha256:fc9d7b8133c920b4809dbdb6182a4be45764c35a076e87d563f777cbaff34141",
}
_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_DNS = re.compile(rf"{_LABEL}(?:\.{_LABEL})*\Z")


def kubernetes_name(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not _DNS.fullmatch(value)
        or len(value) > 253
        or any(len(part) > 63 for part in value.split("."))
    ):
        raise ValueError(f"{label} must be a non-empty Kubernetes name")
    return value


def namespace_name(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 63 or not re.fullmatch(_LABEL, value):
        raise ValueError("Namespace must be a DNS label of at most 63 characters")
    return value


def release_name(value: Any) -> str:
    # This chart derives a Service name directly from the release name.
    if (
        not isinstance(value, str)
        or len(value) > 53
        or not re.fullmatch(r"[a-z](?:[a-z0-9-]*[a-z0-9])?", value)
    ):
        raise ValueError(
            "Nsight Helm release must be a Service-safe label of at most 53 characters"
        )
    return value


def report_subpath(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Reports subdirectory must be a string")
    if value and (
        value.startswith("/")
        or str(PurePosixPath(value)) != value
        or any(part in {".", ".."} for part in value.split("/"))
        or not re.fullmatch(r"[A-Za-z0-9._/-]+", value)
    ):
        raise ValueError("Reports subdirectory must be normalized and relative without '..'")
    return value


def viewer_values(
    tool: str, *, claim: str, subpath: str = "", secret: str = "nsight-streamer-auth"
) -> dict[str, Any]:
    http, turn = TOOL_PORTS[tool]
    mount: dict[str, Any] = {"name": "reports", "mountPath": REPORTS_MOUNT, "readOnly": True}
    if report_subpath(subpath):
        mount["subPath"] = subpath
    return {
        "tool": tool,
        "service": {"type": "ClusterIP", "httpPort": http, "turnPort": turn},
        "enableResize": True,
        "maxResolution": "1920x1080",
        "preserveConfig": False,
        "resources": copy.deepcopy(VIEWER_RESOURCES),
        "webUsername": {"secretName": secret, "secretKey": "username"},
        "webPassword": {"secretName": secret, "secretKey": "password"},
        "volumes": [{"name": "reports", "persistentVolumeClaim": {"claimName": claim}}],
        "volumeMounts": [mount],
    }


@dataclass(frozen=True)
class ReportBinding:
    namespace: str
    claim: str
    subpath: str
    jail_path: str


def soperator_report_binding(
    values: Mapping[str, Any], *, namespace: str, reports_path: str = REPORTS_PATH
) -> ReportBinding:
    """Resolve the longest persistent mount; never fall back to the rootfs PVC."""
    from .soperator_adapter import soperator_persistent_mount_bindings

    path = PurePosixPath(reports_path)
    if not path.is_absolute() or str(path) != reports_path or ".." in path.parts:
        raise ValueError("Reports path must be a normalized absolute jail path")
    matches = [
        item
        for item in soperator_persistent_mount_bindings(values)
        if path.is_relative_to(item.mount_path)
    ]
    if not matches:
        raise ValueError("Reports path must be inside an accepted persistent jail submount")
    binding = max(matches, key=lambda item: len(PurePosixPath(item.mount_path).parts))
    relative = path.relative_to(binding.mount_path).as_posix()
    if relative == ".":
        raise ValueError("Choose a reports subdirectory, not an entire shared jail mount")
    return ReportBinding(namespace, binding.pvc_name, report_subpath(relative), reports_path)


def validate_viewer_values(app_id: str, values: Mapping[str, Any]) -> None:
    if not isinstance(values, Mapping) or set(values) - {
        "tool",
        "service",
        "enableResize",
        "maxResolution",
        "preserveConfig",
        "webUsername",
        "webPassword",
        "volumes",
        "volumeMounts",
        "resources",
        "nodeSelector",
        "affinity",
        "tolerations",
        "imagePullSecrets",
        "storage",
    }:
        raise ValueError(
            "Unsupported Nsight viewer values; image, environment and runtime overrides are not supported"
        )
    tool = APP_TOOLS[app_id]
    for field in ("resources", "nodeSelector", "affinity"):
        if field in values and not isinstance(values[field], Mapping):
            raise ValueError(f"Nsight {field} must be a mapping")
    resources = values.get("resources", {})
    if set(resources) - {"requests", "limits"}:
        raise ValueError("Nsight resources supports only requests and limits")
    for field, quantities in resources.items():
        if not isinstance(quantities, Mapping) or any(
            not isinstance(key, str)
            or isinstance(value, bool)
            or not isinstance(value, (str, int, float))
            or not re.fullmatch(
                r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+|[EPTGMKk]i?|[mun])?", str(value)
            )
            for key, value in quantities.items()
        ):
            raise ValueError(f"Nsight resources.{field} requires nonnegative Kubernetes quantities")
    if any(not isinstance(value, str) for value in values.get("nodeSelector", {}).values()):
        raise ValueError("Nsight nodeSelector values must be strings")
    for field in ("tolerations", "imagePullSecrets"):
        if field in values and (
            not isinstance(values[field], list)
            or any(not isinstance(item, Mapping) for item in values[field])
        ):
            raise ValueError(f"Nsight {field} must be a list of mappings")
    if type(values.get("enableResize")) is not bool or not re.fullmatch(
        r"[1-9][0-9]*x[1-9][0-9]*", str(values.get("maxResolution", ""))
    ):
        raise ValueError("Nsight requires a boolean enableResize and WIDTHxHEIGHT resolution")
    if values.get("tool") != tool:
        raise ValueError(
            f"{app_id} requires tool={tool}; select the other viewer App to change tools"
        )
    service = values.get("service", {})
    if (
        not isinstance(service, Mapping)
        or service.get("type") != "ClusterIP"
        or values.get("preserveConfig") is not False
    ):
        raise ValueError("Nsight viewers require ClusterIP and preserveConfig=false")
    ports = [service.get(key) for key in ("httpPort", "turnPort")]
    if (
        any(type(port) is not int or not 1024 <= port <= 65535 for port in ports)
        or ports[0] == ports[1]
    ):
        raise ValueError("Nsight HTTP and TURN ports must be distinct integers from 1024 to 65535")
    for name in ("webUsername", "webPassword"):
        ref = values.get(name)
        if not isinstance(ref, Mapping) or set(ref) != {"secretName", "secretKey"}:
            raise ValueError(f"Nsight {name} requires a Secret reference, never a credential value")
        kubernetes_name(ref["secretName"], f"{name}.secretName")
        if not isinstance(ref["secretKey"], str) or not re.fullmatch(
            r"[A-Za-z0-9._-]+", ref["secretKey"]
        ):
            raise ValueError(f"Invalid {name}.secretKey")
    volumes, mounts = values.get("volumes"), values.get("volumeMounts")
    if (
        not isinstance(volumes, list)
        or len(volumes) != 1
        or not isinstance(mounts, list)
        or len(mounts) != 1
    ):
        raise ValueError("Nsight requires exactly one existing reports PVC mount")
    volume, mount = volumes[0], mounts[0]
    if (
        not isinstance(volume, Mapping)
        or set(volume) != {"name", "persistentVolumeClaim"}
        or volume.get("name") != "reports"
        or not isinstance(volume.get("persistentVolumeClaim"), Mapping)
        or set(volume["persistentVolumeClaim"]) != {"claimName"}
        or not isinstance(mount, Mapping)
        or set(mount) - {"name", "mountPath", "readOnly", "subPath"}
        or mount.get("name") != "reports"
        or mount.get("mountPath") != REPORTS_MOUNT
        or mount.get("readOnly") is not True
    ):
        raise ValueError("Nsight must mount only its reports PVC read-only at /mnt/reports")
    kubernetes_name(volume["persistentVolumeClaim"]["claimName"], "Reports PVC")
    report_subpath(mount.get("subPath", ""))
    storage = values.get("storage", {})
    if (
        not isinstance(storage, Mapping)
        or storage.get("additionalVolumes")
        or storage.get("cloudStorage", {})
    ):
        raise ValueError("Nsight catalog viewers use only the explicit reports PVC")
    if values.get("env"):
        raise ValueError("Nsight catalog viewers do not accept environment overrides")
    if values.get("tools") or values.get("image"):
        raise ValueError("Nsight viewer images are selected by the pinned official chart")


def viewer_patches(release_name: str, *, tool: str) -> list[dict[str, Any]]:
    """The standalone viewer needs no Kubernetes API or namespace-wide Role."""
    return [
        {
            "target": {"kind": "Deployment", "name": re.escape(release_name)},
            "patch": "- op: add\n  path: /spec/template/spec/automountServiceAccountToken\n  value: false\n"
            "- op: replace\n  path: /spec/template/spec/containers/0/image\n  value: "
            + VIEWER_IMAGES[tool]
            + "\n",
        },
        {
            "target": {"kind": "Role", "name": re.escape(f"{release_name}-role")},
            "patch": "- op: replace\n  path: /rules\n  value: []\n",
        },
    ]


def configured_viewers(payload: Any, target_ref: str = "") -> list[dict[str, Any]]:
    from .component_defaults import resolve_component_defaults
    from .components import component_entries

    data = to_plain_data(payload)
    entries = {entry.id: entry for entry in component_entries("apps")}
    result = []
    for original in data.get("apps", {}).get("charts", []):
        app_id = component_type_id(original)
        if app_id not in APP_TOOLS or not original.get("enabled", False):
            continue
        if target_ref and component_instance_id(original) != target_ref:
            continue
        row = resolve_component_defaults(
            payload=data,
            component_node=copy.deepcopy(original),
            entry=entries[app_id],
            preserve_existing_literal=True,
            preserve_existing_shared=False,
            include_shared=False,
        )
        row.setdefault("namespace", entries[app_id].default_namespace or "soperator")
        row["release_name"] = (
            row.get("release-name") or entries[app_id].default_release_name or app_id
        )
        namespace_name(row["namespace"])
        release_name(row["release_name"])
        if row.get("version", CHART_VERSION) != CHART_VERSION:
            raise ValueError(f"Nsight viewers require the reviewed chart {CHART_VERSION}")
        validate_viewer_values(app_id, row.get("values", {}))
        result.append(row)
    ports_by_target: dict[str, set[int]] = {}
    for row in result:
        occupied = ports_by_target.setdefault(component_instance_id(row), set())
        ports = {row["values"]["service"][key] for key in ("httpPort", "turnPort")}
        if occupied & ports:
            raise ValueError(
                "Nsight viewers on one target require distinct laptop HTTP and TURN ports"
            )
        occupied.update(ports)
    return result


def configure_viewer(row: dict[str, Any], *, prompt: Callable[[str, Any], Any]) -> dict[str, Any]:
    """Small guided editor; caller owns cancellation/back and source publication."""
    result = copy.deepcopy(row)
    values = result.setdefault("values", {})
    tool = APP_TOOLS[result["id"]]
    volumes = values.get("volumes") or [{}]
    mounts = values.get("volumeMounts") or [{}]
    namespace = prompt(
        "Namespace (the reports PVC and login Secret must exist here)",
        result.get("namespace", "soperator"),
    )
    claim = prompt(
        "Existing reports PVC", volumes[0].get("persistentVolumeClaim", {}).get("claimName", "")
    )
    subpath = prompt(
        "Reports subdirectory inside the PVC (empty uses the whole reports PVC)",
        mounts[0].get("subPath", ""),
    )
    secret = prompt(
        "Existing browser login Secret",
        values.get("webUsername", {}).get("secretName", "nsight-streamer-auth"),
    )
    updated = viewer_values(tool, claim=claim, subpath=subpath, secret=secret)
    for key in ("webUsername", "webPassword"):
        updated[key]["secretKey"] = prompt(
            f"Secret key for browser {'username' if key == 'webUsername' else 'password'}",
            values.get(key, {}).get("secretKey", updated[key]["secretKey"]),
        )
    for key in ("resources", "nodeSelector", "affinity", "tolerations", "imagePullSecrets"):
        if key in values:
            updated[key] = copy.deepcopy(values[key])
    for key in ("service", "enableResize", "maxResolution"):
        if key in values:
            updated[key] = copy.deepcopy(values[key])
    result["namespace"] = namespace_name(namespace)
    updated["resources"] = prompt(
        "Viewer resource requests and limits", values.get("resources", {})
    )
    result["values"] = updated
    validate_viewer_values(result["id"], updated)
    return result


def run_viewer_wizard(row, *, prompt_scalar, is_back):
    class Navigation(Exception):
        pass

    def ask(label, current):
        value, stop = prompt_scalar(label, current)
        if stop:
            raise Navigation("quit")
        if is_back(value):
            raise Navigation("back")
        return value

    try:
        return configure_viewer(row, prompt=ask), "continue"
    except Navigation as exc:
        return row, str(exc)
