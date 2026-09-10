"""Validate native execution against the verified upstream Helm render."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .soperator_checks_allocation import projected_script
from .soperator_checks_policy import CHECKS_POLICY_ENV, checks_digest


def normalized(value: Any) -> Any:
    """Remove only semantically empty/default Kubernetes serialization fields."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if item is None or item == [] or item == {} or item == "":
                continue
            if (key, item) in (("apiVersion", "v1"), ("defaultMode", 420), ("readOnly", False)):
                continue
            result[key] = normalized(item)
        return result
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def execution_spec(spec: Mapping[str, Any], kind: str) -> dict[str, Any]:
    result = copy.deepcopy(dict(spec.get(kind + "Spec", {})))
    for key in ("jobContainer", "mungeContainer"):
        if key in result:
            result[key].setdefault("appArmorProfile", "unconfined")
            result[key]["env"] = [
                item for item in result[key].get("env", []) if item.get("name") != CHECKS_POLICY_ENV
            ]
    if kind == "slurmJob":
        result.setdefault("eachWorkerJobs", False)
        result.setdefault("maxNumberOfJobs", 0)
    return normalized(result)


def verify_check_spec(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> None:
    kind = expected.get("checkType")
    if kind not in {"k8sJob", "slurmJob"} or actual.get("podTemplateNameRef"):
        raise RuntimeError("unsupported upstream check execution authority")
    keys = (
        "name",
        "slurmClusterRefName",
        "checkType",
        "dependsOn",
        "nodeSelector",
        "affinity",
        "tolerations",
        "successReactions",
        "failureReactions",
    )
    if normalized({k: expected.get(k) for k in keys}) != normalized(
        {k: actual.get(k) for k in keys}
    ) or execution_spec(expected, kind) != execution_spec(actual, kind):
        raise RuntimeError("ActiveCheck execution differs from the verified upstream render")


def verify_native_template(
    expected: Mapping[str, Any], check: Mapping[str, Any], template: Mapping[str, Any]
) -> str | None:
    """Validate the upstream CR-to-Pod contract, returning the native script ConfigMap."""
    actual = check.get("spec", {})
    verify_check_spec(expected, actual)
    kind = expected["checkType"]
    selected = actual[kind + "Spec"]
    base = selected["jobContainer"]
    pod = template.get("spec", {})
    containers = pod.get("containers", [])
    if len(containers) != 1:
        raise RuntimeError("upstream check container topology changed")
    container = containers[0]
    env = copy.deepcopy(base.get("env", []))
    if kind == "slurmJob":
        env.append({"name": "ACTIVE_CHECK_NAME", "value": check["metadata"]["name"]})
        if selected.get("eachWorkerJobs"):
            env.append({"name": "EACH_WORKER_JOBS", "value": "true"})
        if "maxNumberOfJobs" in selected:
            env.append(
                {
                    "name": "ACTIVE_CHECK_MAX_NUMBER_OF_JOBS",
                    "value": str(selected["maxNumberOfJobs"]),
                }
            )
    executable = {
        "name": actual["name"],
        **{key: base.get(key) for key in ("image", "command", "args", "workingDir")},
        "env": env,
    }
    if (
        normalized(executable) != normalized({key: container.get(key) for key in executable})
        or container.get("envFrom")
        or container.get("lifecycle")
    ):
        raise RuntimeError("native check executable differs from its verified ActiveCheck")
    munge = selected.get("mungeContainer")
    init = pod.get("initContainers", [])
    if len(init) != (1 if munge else 0):
        raise RuntimeError("native check init container topology changed")
    if munge and (
        init[0].get("name") != "munge"
        or init[0].get("image") != munge.get("image")
        or normalized(init[0].get("command", [])) != normalized(munge.get("command", []))
        or init[0].get("restartPolicy") != "Always"
    ):
        raise RuntimeError("native check munge executable changed")
    if munge:
        if any(init[0].get(key) for key in ("args", "env", "envFrom", "lifecycle")):
            raise RuntimeError("native check munge execution environment changed")
        expected_mounts = [
            {"name": "munge-key", "mountPath": "/mnt/munge-key", "readOnly": True},
            {"name": "munge-socket", "mountPath": "/run/munge"},
        ]
        if normalized(init[0].get("volumeMounts", [])) != normalized(expected_mounts):
            raise RuntimeError("native check munge mounts changed")
    for key in ("affinity", "nodeSelector", "tolerations", "hostUsers"):
        if normalized(pod.get(key)) != normalized(actual.get(key)):
            raise RuntimeError("native check Pod scheduling contract changed")
    if pod.get("serviceAccountName") != actual["slurmClusterRefName"] + "-activecheck-sa":
        raise RuntimeError("native check service account changed")
    mounts = copy.deepcopy(base.get("volumeMounts", []))
    volumes = copy.deepcopy(base.get("volumes", []))
    slurm_mount = {"name": "slurm-configs", "mountPath": "/mnt/slurm-configs", "readOnly": True}
    socket_mount = {"name": "munge-socket", "mountPath": "/run/munge"}
    key_mount = {"name": "munge-key", "mountPath": "/mnt/munge-key", "readOnly": True}
    if munge or kind == "slurmJob":
        cluster = actual["slurmClusterRefName"]
        volumes.extend(
            [
                {"name": "slurm-configs", "configMap": {"name": cluster + "-slurm-configs"}},
                {
                    "name": "munge-key",
                    "secret": {
                        "secretName": cluster + "-munge",
                        "defaultMode": 272,
                        "items": [{"key": "munge.key", "path": "munge.key", "mode": 256}],
                    },
                },
                {"name": "munge-socket", "emptyDir": {}},
            ]
        )
    script_name = None
    if kind == "slurmJob":
        script_name = "sbatch-script-" + actual["name"]
        script_mount = {
            "name": "sbatch-volume",
            "mountPath": "/opt/bin/sbatch.sh",
            "subPath": "sbatch.sh",
            "readOnly": True,
        }
        mounts = [slurm_mount, key_mount, socket_mount, script_mount, *mounts]
        volumes.append(
            {
                "name": "sbatch-volume",
                "configMap": {
                    "name": script_name,
                    "items": [{"key": "sbatch.sh", "path": "sbatch.sh", "mode": 493}],
                },
            }
        )
    elif munge:
        mounts.extend([slurm_mount, socket_mount])
    if normalized(mounts) != normalized(container.get("volumeMounts", [])) or normalized(
        volumes
    ) != normalized(pod.get("volumes", [])):
        raise RuntimeError("native check script mounts or volume sources changed")
    return script_name


def job_execution_digest(job: Mapping[str, Any]) -> str:
    # A Job admission webhook may rewrite the template before storage. Job-level
    # selector/UID/default controller labels are excluded; the complete Pod spec is not.
    template = job.get("spec", {}).get("template", {})
    spec = normalized(template.get("spec", {}))
    script = projected_script(template)
    if script is not None:
        return checks_digest({"podSpec": spec, "projectedScript": script})
    return checks_digest(spec)
