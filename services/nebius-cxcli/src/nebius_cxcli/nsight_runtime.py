"""Bounded, read-only prerequisites and durable laptop access for Nsight viewers."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .grafana_runtime import GRAFANA_TARGET_KUBE_CONTEXT_ENV
from .nsight import REPORTS_MOUNT, VIEWER_IMAGES, configured_viewers


class NsightKubernetes:
    def __init__(self, extra_env: Mapping[str, str], *, runner=None):
        self.env = {**os.environ, **extra_env}
        self.context = str(extra_env.get(GRAFANA_TARGET_KUBE_CONTEXT_ENV, ""))
        if not self.context or not extra_env.get("KUBECONFIG"):
            raise ValueError(
                "Nsight requires an explicitly bound Kubernetes context and kubeconfig"
            )
        self.runner = runner or subprocess.run

    def run(self, args, *, input_text=None, timeout=60):
        result = self.runner(
            ["kubectl", "--context", self.context, *args],
            env=self.env,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            # kubectl errors can echo manifests, auth plugins or Secret contents.
            raise RuntimeError(
                f"Nsight Kubernetes operation failed ({args[0] if args else 'unknown'}); inspect the selected target"
            )
        return result.stdout

    def get(self, kind: str, name: str, namespace: str = "") -> dict[str, Any]:
        args = (["-n", namespace] if namespace else []) + ["get", kind, name, "-o", "json"]
        value = json.loads(self.run(args))
        if not isinstance(value, dict):
            raise RuntimeError("Nsight Kubernetes read returned a non-object response")
        return value


def validate_reports_pvc(kube: NsightKubernetes, row: Mapping[str, Any]) -> dict[str, Any]:
    namespace = row["namespace"]
    name = row["values"]["volumes"][0]["persistentVolumeClaim"]["claimName"]
    pvc = kube.get("pvc", name, namespace)
    if (
        pvc.get("metadata", {}).get("name") != name
        or pvc.get("metadata", {}).get("namespace") != namespace
        or not pvc.get("metadata", {}).get("uid")
        or pvc.get("status", {}).get("phase") != "Bound"
        or pvc.get("spec", {}).get("volumeMode", "Filesystem") != "Filesystem"
    ):
        raise RuntimeError(
            "Nsight reports PVC must be an existing Bound filesystem claim in the viewer namespace"
        )
    pv = kube.get("pv", pvc["spec"]["volumeName"])
    ref = pv.get("spec", {}).get("claimRef", {})
    if (
        ref.get("uid") != pvc["metadata"]["uid"]
        or ref.get("namespace") != namespace
        or ref.get("name") != name
    ):
        raise RuntimeError("Nsight reports PV does not bind the selected claim identity")
    if not pv.get("metadata", {}).get("uid"):
        raise RuntimeError("Nsight reports PV has no immutable identity")
    if "ReadWriteOncePod" in pvc.get("spec", {}).get("accessModes", []):
        raise RuntimeError(
            "ReadWriteOncePod storage cannot be shared by a profiler and its viewers"
        )
    return {
        "namespace": namespace,
        "pvcName": name,
        "pvcUid": pvc["metadata"]["uid"],
        "pvName": pv["metadata"]["name"],
        "pvUid": pv["metadata"]["uid"],
    }


def validate_login_secret(kube: NsightKubernetes, row: Mapping[str, Any]) -> None:
    for field in ("webUsername", "webPassword"):
        ref = row["values"][field]
        identity = f"{row['namespace']}/{ref['secretName']}"
        # Only the object name and nonempty key names leave kubectl. Suppress
        # NotFound alone so missing objects remain distinct from access errors.
        try:
            output = kube.run(
                [
                    "-n",
                    row["namespace"],
                    "get",
                    "secret",
                    ref["secretName"],
                    "--ignore-not-found=true",
                    "-o",
                    'go-template={{.metadata.name}}{{"\\n"}}'
                    '{{range $k,$v := .data}}{{if $v}}{{$k}}{{"\\n"}}{{end}}{{end}}',
                ]
            )
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            raise RuntimeError(
                f"Cannot read Nsight login Secret {identity}; check Kubernetes connectivity, "
                "access and Secret read permissions."
            ) from None
        lines = output.splitlines()
        if not lines:
            raise RuntimeError(
                f"Nsight login Secret {identity} does not exist; provide it through your "
                "Secret-management workflow before retrying."
            )
        if lines[0] != ref["secretName"]:
            raise RuntimeError(f"Nsight login Secret {identity} returned an unexpected identity")
        if ref["secretKey"] not in lines[1:]:
            raise RuntimeError(
                f"Nsight login Secret {identity} requires a non-empty '{ref['secretKey']}' key "
                f"for {field}; update it through your Secret-management workflow before retrying."
            )


def prepare_nsight_viewers(config: Any, *, extra_env, target_ref: str = "") -> tuple[dict, ...]:
    rows = configured_viewers(config, target_ref)
    if not rows:
        return ()
    kube = NsightKubernetes(extra_env or {})
    bindings = []
    for row in rows:
        validate_login_secret(kube, row)
        bindings.append(validate_reports_pvc(kube, row))
    return tuple(bindings)


def persistent_context(
    context: str, *, kubernetes_uid: str, runner=None, candidates: tuple[Path, ...] | None = None
) -> Path | None:
    """Prove a durable context independently, never return installation scratch paths."""
    if candidates is None:
        candidates = (Path.home() / ".kube" / "config",)
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            payload = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(payload, dict) or context not in {
            item.get("name") for item in payload.get("contexts", []) if isinstance(item, dict)
        }:
            continue
        kube = NsightKubernetes(
            {"KUBECONFIG": str(path), GRAFANA_TARGET_KUBE_CONTEXT_ENV: context}, runner=runner
        )
        try:
            if (
                kube.get("namespace", "kube-system").get("metadata", {}).get("uid")
                == kubernetes_uid
            ):
                return path.resolve()
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
            continue
    return None


def access_command(row: Mapping[str, Any], *, kubeconfig: Path, context: str) -> str:
    service = row["values"]["service"]
    return shlex.join(
        [
            "kubectl",
            "--kubeconfig",
            str(kubeconfig),
            "--context",
            context,
            "--namespace",
            str(row["namespace"]),
            "port-forward",
            "--address",
            "127.0.0.1",
            f"service/{row['release_name']}-service",
            f"{service['httpPort']}:{service['httpPort']}",
            f"{service['turnPort']}:{service['turnPort']}",
        ]
    )


def password_command(row: Mapping[str, Any], *, kubeconfig: Path, context: str) -> str:
    ref = row["values"]["webPassword"]
    # Secret keys permit dots; escape those for JSONPath field selection.
    key = ref["secretKey"].replace(".", "\\.")
    return (
        shlex.join(
            [
                "kubectl",
                "--kubeconfig",
                str(kubeconfig),
                "--context",
                context,
                "--namespace",
                str(row["namespace"]),
                "get",
                "secret",
                ref["secretName"],
                "-o",
                f"jsonpath={{.data.{key}}}",
            ]
        )
        + " | base64 --decode && printf '\\n'"
    )


def validate_viewer_deployment(deployment: Mapping[str, Any], row: Mapping[str, Any]) -> None:
    values = row["values"]
    spec = deployment.get("spec", {})
    pod = spec.get("template", {}).get("spec", {})
    containers = pod.get("containers", [])
    selected = [
        item for item in containers if item.get("name") == f"nsight-streamer-{values['tool']}"
    ]
    if (
        len(selected) != 1
        or spec.get("replicas") != 1
        or pod.get("automountServiceAccountToken") is not False
    ):
        raise RuntimeError("Nsight viewer Deployment differs from its single-viewer contract")
    container = selected[0]
    volumes = [item for item in pod.get("volumes", []) if "persistentVolumeClaim" in item]
    mounts = [item for item in container.get("volumeMounts", []) if item.get("name") == "reports"]
    if (
        container.get("image") != VIEWER_IMAGES[values["tool"]]
        or volumes != values["volumes"]
        or mounts != values["volumeMounts"]
    ):
        raise RuntimeError(
            "Nsight viewer image or read-only reports binding differs from its configuration"
        )
    env = {item["name"]: item for item in container.get("env", [])}
    for name, key in (("WEB_USERNAME", "webUsername"), ("WEB_PASSWORD", "webPassword")):
        ref = values[key]
        if env.get(name) != {
            "name": name,
            "valueFrom": {"secretKeyRef": {"name": ref["secretName"], "key": ref["secretKey"]}},
        }:
            raise RuntimeError("Nsight viewer login differs from its configured Secret reference")


def collect_nsight_status(
    config: Any,
    *,
    extra_env,
    target_ref: str = "",
    emit=None,
    runner=None,
    kubeconfig_candidates: tuple[Path, ...] | None = None,
) -> tuple[dict[str, Any], ...]:
    rows = configured_viewers(config, target_ref)
    if not rows:
        return ()
    kube = NsightKubernetes(extra_env or {}, runner=runner)
    uid = kube.get("namespace", "kube-system")["metadata"]["uid"]
    durable = persistent_context(
        kube.context, kubernetes_uid=uid, runner=runner, candidates=kubeconfig_candidates
    )
    statuses = []
    for row in rows:
        namespace, release = row["namespace"], row["release_name"]
        deployment = kube.get("deployment", release, namespace)
        validate_viewer_deployment(deployment, row)
        status = deployment.get("status", {})
        if (
            status.get("observedGeneration", 0)
            < deployment.get("metadata", {}).get("generation", 1)
            or status.get("availableReplicas", 0) < 1
            or status.get("updatedReplicas", 0) < 1
        ):
            raise RuntimeError(f"Nsight viewer {release} is not ready")
        service = kube.get("service", f"{release}-service", namespace)
        actual = {
            port.get("name"): (port.get("port"), port.get("protocol", "TCP"))
            for port in service.get("spec", {}).get("ports", [])
        }
        expected = row["values"]["service"]
        if service.get("spec", {}).get("type") != "ClusterIP" or actual != {
            "http": (expected["httpPort"], "TCP"),
            "turn": (expected["turnPort"], "TCP"),
        }:
            raise RuntimeError(
                f"Nsight viewer {release} Service differs from its configured TCP ports"
            )
        validate_login_secret(kube, row)
        binding = validate_reports_pvc(kube, row)
        kube.run(
            [
                "-n",
                namespace,
                "exec",
                f"deployment/{release}",
                "-c",
                f"nsight-streamer-{row['values']['tool']}",
                "--",
                "su",
                "-s",
                "/bin/sh",
                "nvidia",
                "-c",
                f"test -d {REPORTS_MOUNT} && test -r {REPORTS_MOUNT} && test -x {REPORTS_MOUNT}",
            ]
        )
        kube.run(
            [
                "-n",
                namespace,
                "exec",
                f"deployment/{release}",
                "-c",
                f"nsight-streamer-{row['values']['tool']}",
                "--",
                "python3",
                "-c",
                "import socket, time\n"
                "deadline = time.monotonic() + 120\n"
                "while True:\n"
                "    try:\n"
                f"        for port in ({expected['httpPort']}, {expected['turnPort']}):\n"
                "            socket.create_connection(('127.0.0.1', port), 3).close()\n"
                "        break\n"
                "    except OSError:\n"
                "        if time.monotonic() >= deadline: raise\n"
                "        time.sleep(2)\n",
            ],
            timeout=150,
        )
        item = {
            "target_ref": target_ref or row["instance_id"],
            "release_name": release,
            "namespace": namespace,
            "tool": row["values"]["tool"],
            "storage": binding,
            "status": "ready",
            "browser_verified": False,
            "url": f"http://localhost:{expected['httpPort']}",
            "secret_name": row["values"]["webPassword"]["secretName"],
            "access_status": "ready" if durable else "persistent-context-required",
        }
        if durable:
            item["port_forward_command"] = access_command(
                row, kubeconfig=durable, context=kube.context
            )
            item["password_command"] = password_command(
                row, kubeconfig=durable, context=kube.context
            )
        statuses.append(item)
        if emit:
            emit(
                f"Nsight {item['tool']}: installed; reports at {REPORTS_MOUNT}; browser login Secret {namespace}/{item['secret_name']}"
            )
            if durable:
                emit(item["port_forward_command"])
                emit(f"Open {item['url']} (keep this terminal running).")
            else:
                emit(
                    "Viewer installed; access setup incomplete. Persist the selected target's kubeconfig and repeat application apply or profiling install; no temporary-context command was emitted."
                )
    return tuple(statuses)
