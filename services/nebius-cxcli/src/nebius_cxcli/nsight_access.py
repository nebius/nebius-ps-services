"""Fresh, credential-free laptop access to the two live Soperator viewers."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import kubernetes_process
from .grafana_database import OWNER_ANNOTATION, app_owner
from .grafana_runtime import GRAFANA_TARGET_CLUSTER_ID_ENV, GRAFANA_TARGET_KUBE_CONTEXT_ENV
from .nsight import CHART_NAME, kubernetes_name, namespace_name
from .nsight_runtime import access_command, password_command
from .paths import ProjectPaths
from .runtime_config import to_plain_data

_SECRET_PROJECTION = (
    'go-template={{.metadata.uid}}{{"\\n"}}{{.metadata.name}}{{"\\n"}}'
    '{{.metadata.namespace}}{{"\\n"}}{{.metadata.resourceVersion}}{{"\\n"}}'
    '{{if .metadata.deletionTimestamp}}deleting{{else}}active{{end}}{{"\\n"}}'
    '{{range $key,$value := .data}}{{if $value}}{{$key}}{{"\\n"}}{{end}}{{end}}'
)


class NsightAccessError(RuntimeError):
    """Live evidence cannot establish a complete, safe access handoff."""


@dataclass(frozen=True)
class ViewerAccess:
    tool: str
    namespace: str
    service_name: str
    http_port: int
    turn_port: int
    password_secret: str
    password_key: str
    username_secret: str
    username_key: str

    @property
    def browser_url(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"


@dataclass(frozen=True)
class NsightAccess:
    target_ref: str
    viewers: tuple[ViewerAccess, ViewerAccess]
    kubeconfig: Path
    kube_context: str

    def commands(self) -> tuple[str, str, str]:
        forwards = tuple(
            access_command(
                kubeconfig=self.kubeconfig,
                context=self.kube_context,
                namespace=viewer.namespace,
                service_name=viewer.service_name,
                http_port=viewer.http_port,
                turn_port=viewer.turn_port,
            )
            for viewer in self.viewers
        )
        shared = self.viewers[0]
        return (
            forwards[0],
            forwards[1],
            password_command(
                kubeconfig=self.kubeconfig,
                context=self.kube_context,
                namespace=shared.namespace,
                secret_name=shared.password_secret,
                secret_key=shared.password_key,
            ),
        )

    def terminal_lines(self) -> list[str]:
        commands = self.commands()
        lines = [f"# Soperator profiling {self.target_ref}"]
        for index, title in enumerate(("Nsight Systems", "Nsight Compute")):
            lines.extend(
                [
                    f"# {title}: open {self.viewers[index].browser_url} after starting forwarding.",
                    "# Keep this port-forward running in its own terminal (HTTP and TURN TCP).",
                    commands[index],
                ]
            )
        lines.extend(
            ["# Display the shared viewer password (run in another terminal)", commands[2]]
        )
        return lines


def _one[T](items: Sequence[T], description: str) -> T:
    if len(items) != 1:
        raise NsightAccessError(f"Expected exactly one {description}; found {len(items)}.")
    return items[0]


def _read(env: Mapping[str, str], resource: str, *, namespace: str = "", name: str = "") -> Any:
    """Bounded get only; Secret data is never returned to cxcli."""
    from . import cli

    output = _SECRET_PROJECTION if resource == "secret" else "json"
    if resource == "secret" and (not namespace or not name):
        raise NsightAccessError("Secret access checks require an exact namespace and name.")
    command = ["kubectl", "--context", env[GRAFANA_TARGET_KUBE_CONTEXT_ENV], "get", resource]
    command.extend(["--namespace", namespace] if namespace else ["--all-namespaces"])
    if name:
        command.append(name)
    command.extend(["--request-timeout=20s", "-o", output])
    try:
        result = kubernetes_process.run(
            command,
            env=cli._post_flux_subprocess_env(env),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise NsightAccessError(
            "Cannot inspect live profiling access; check connectivity."
        ) from None
    if result.returncode:
        raise NsightAccessError(
            f"Cannot inspect live profiling {resource}; check existence, connectivity and permissions."
        )
    if resource == "secret":
        return result.stdout.splitlines()
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise NsightAccessError("Invalid live profiling resource response.") from None
    if not isinstance(value, dict):
        raise NsightAccessError("Invalid live profiling resource response.")
    return value


def _helm_owned(resource: Mapping[str, Any], release: str, namespace: str) -> bool:
    meta = resource.get("metadata", {})
    annotations = meta.get("annotations", {})
    return bool(
        meta.get("uid")
        and not meta.get("deletionTimestamp")
        and meta.get("namespace") == namespace
        and annotations.get("meta.helm.sh/release-name") == release
        and annotations.get("meta.helm.sh/release-namespace") == namespace
    )


def _environment(container: Mapping[str, Any], variable: str) -> Mapping[str, Any]:
    return _one([e for e in container.get("env", []) if e.get("name") == variable], variable)


def _secret_ref(container: Mapping[str, Any], variable: str) -> tuple[str, str]:
    entry = _environment(container, variable)
    ref = entry.get("valueFrom", {}).get("secretKeyRef", {})
    if (
        "value" in entry
        or ref.get("optional")
        or not isinstance(ref.get("key"), str)
        or not re.fullmatch(r"[A-Za-z0-9._-]+", ref["key"])
    ):
        raise NsightAccessError(f"Nsight {variable} must reference a required Secret key.")
    return kubernetes_name(ref.get("name"), "Nsight login Secret"), ref["key"]


def _port(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise NsightAccessError("Nsight access contains an invalid port.")
    return value


def _service_port(service: Mapping[str, Any], container: Mapping[str, Any], role: str) -> int:
    port = _one(
        [p for p in service["spec"].get("ports", []) if p.get("name") == role],
        f"Nsight {role} Service port",
    )
    value = _port(port.get("port"))
    destination = port.get("targetPort", value)
    container_ports = container.get("ports", [])
    if isinstance(destination, str):
        destination = _one(
            [p.get("containerPort") for p in container_ports if p.get("name") == destination],
            f"Nsight {role} named container port",
        )
    destination = _port(destination)
    env = _environment(container, f"{role.upper()}_PORT")
    declared = env.get("value", "")
    if (
        port.get("protocol", "TCP") != "TCP"
        or "valueFrom" in env
        or not isinstance(declared, str)
        or not declared.isascii()
        or not declared.isdecimal()
        or int(declared) != destination
        or not any(
            p.get("containerPort") == destination and p.get("protocol", "TCP") == "TCP"
            for p in container_ports
        )
        or (role == "turn" and value != destination)
    ):
        raise NsightAccessError(f"Nsight {role} Service does not match its live TCP listener.")
    return value


def _snapshot(resource: Mapping[str, Any]) -> tuple[Any, ...]:
    meta, status = resource.get("metadata", {}), resource.get("status", {})
    return (
        *(
            meta.get(key)
            for key in ("uid", "generation", "deletionTimestamp", "annotations", "labels")
        ),
        resource.get("spec"),
        *(
            status.get(key)
            for key in (
                "conditions",
                "observedGeneration",
                "replicas",
                "updatedReplicas",
                "availableReplicas",
            )
        ),
    )


def _viewer(
    env: Mapping[str, str], release: Mapping[str, Any], tool: str
) -> tuple[ViewerAccess, list[tuple[str, Mapping[str, Any]]]]:
    meta, spec, status = release["metadata"], release["spec"], release.get("status", {})
    ready = _one(
        [c for c in status.get("conditions", []) if c.get("type") == "Ready"],
        f"{tool} HelmRelease readiness condition",
    )
    if (
        not meta.get("uid")
        or not meta.get("generation")
        or meta.get("deletionTimestamp")
        or spec.get("suspend")
        or spec.get("kubeConfig")
        or ready.get("status") != "True"
        or ready.get("observedGeneration", status.get("observedGeneration")) != meta["generation"]
    ):
        raise NsightAccessError(
            f"Nsight {tool} HelmRelease is not ready at its current generation."
        )
    namespace = namespace_name(spec.get("targetNamespace") or meta["namespace"])
    name = kubernetes_name(spec.get("releaseName") or meta["name"], "Nsight release")
    # The pinned chart uses app/release labels, not app.kubernetes.io/instance.
    workloads = _read(env, "deployments", namespace=namespace).get("items", [])
    workload = _one(
        [w for w in workloads if _helm_owned(w, name, namespace)], f"owned {tool} Deployment"
    )
    ws, live = workload["spec"], workload.get("status", {})
    replicas = ws.get("replicas", 1)
    if (
        type(replicas) is not int
        or replicas < 1
        or live.get("observedGeneration") != workload["metadata"].get("generation")
        or any(
            live.get(key, 0) != replicas
            for key in ("replicas", "updatedReplicas", "availableReplicas")
        )
    ):
        raise NsightAccessError(f"Nsight {tool} Deployment has not completed its rollout.")
    template = ws["template"]
    container = _one(
        [c for c in template["spec"]["containers"] if c.get("name") == f"nsight-streamer-{tool}"],
        f"{tool} viewer container",
    )
    secret, key = _secret_ref(container, "WEB_PASSWORD")
    user_secret, user_key = _secret_ref(container, "WEB_USERNAME")
    if (secret, key) == (user_secret, user_key):
        raise NsightAccessError("Nsight username and password must use distinct Secret keys.")
    services = _read(env, "services", namespace=namespace).get("items", [])
    labels = template.get("metadata", {}).get("labels", {})
    if labels.get("release") != name or labels.get("app") != f"nsight-streamer-{tool}":
        raise NsightAccessError("Nsight Deployment labels do not identify its viewer release.")
    service = _one(
        [
            s
            for s in services
            if _helm_owned(s, name, namespace)
            and s.get("spec", {}).get("type", "ClusterIP") == "ClusterIP"
            and s.get("spec", {}).get("clusterIP") != "None"
            and s.get("spec", {}).get("selector")
            and s["spec"]["selector"].get("release") == name
            and s["spec"]["selector"].get("app") == labels["app"]
            and all(labels.get(k) == v for k, v in s["spec"]["selector"].items())
        ],
        f"owned {tool} viewer Service",
    )
    result = ViewerAccess(
        tool,
        namespace,
        kubernetes_name(service["metadata"]["name"], "Nsight Service"),
        _service_port(service, container, "http"),
        _service_port(service, container, "turn"),
        secret,
        key,
        user_secret,
        user_key,
    )
    return result, [("helmrelease", release), ("deployment", workload), ("service", service)]


def inspect_live_nsight(
    config: Any, target_ref: str, env: Mapping[str, str]
) -> tuple[ViewerAccess, ViewerAccess]:
    """Observe both live viewers without installer probes or desired-value comparisons."""
    owner = app_owner(config, target_ref)
    releases = _read(env, "helmreleases.helm.toolkit.fluxcd.io").get("items", [])
    owned = [
        r
        for r in releases
        if r.get("metadata", {}).get("annotations", {}).get(OWNER_ANNOTATION) == owner
        and (
            r.get("spec", {}).get("chart", {}).get("spec", {}).get("chart") == CHART_NAME
            or (r.get("status", {}).get("history") or [{}])[0].get("chartName") == CHART_NAME
        )
    ]
    viewers, snapshots = [], []
    for tool in ("nsys", "ncu"):
        release = _one(
            [r for r in owned if r.get("spec", {}).get("values", {}).get("tool") == tool],
            f"owned {tool} HelmRelease",
        )
        viewer, resources = _viewer(env, release, tool)
        viewers.append(viewer)
        snapshots.extend(resources)
    if len({(v.namespace, v.password_secret, v.password_key) for v in viewers}) != 1:
        raise NsightAccessError("Both Nsight viewers must reference the same password Secret key.")
    ports = [port for v in viewers for port in (v.http_port, v.turn_port)]
    if len(set(ports)) != len(ports):
        raise NsightAccessError("Nsight viewers have conflicting local HTTP/TURN ports.")
    secrets: dict[tuple[str, str], set[str]] = {}
    for viewer in viewers:
        for secret, key in (
            (viewer.password_secret, viewer.password_key),
            (viewer.username_secret, viewer.username_key),
        ):
            secrets.setdefault((viewer.namespace, secret), set()).add(key)
    secret_snapshots = {}
    for (namespace, name), keys in secrets.items():
        lines = _read(env, "secret", namespace=namespace, name=name)
        if (
            len(lines) < 5
            or not lines[0]
            or lines[1:3] != [name, namespace]
            or not lines[3]
            or lines[4] != "active"
            or not keys <= set(lines[5:])
        ):
            raise NsightAccessError(
                "Nsight login Secret is missing required nonempty keys or identity."
            )
        secret_snapshots[(namespace, name)] = lines
    for kind, original in snapshots:
        meta = original["metadata"]
        current = _read(env, kind, namespace=meta["namespace"], name=meta["name"])
        if _snapshot(current) != _snapshot(original):
            raise NsightAccessError("Nsight resources changed during access discovery; retry.")
    for (namespace, name), original in secret_snapshots.items():
        if _read(env, "secret", namespace=namespace, name=name) != original:
            raise NsightAccessError("Nsight login Secret changed during access discovery; retry.")
    return viewers[0], viewers[1]


def discover_nsight_access(
    config: Any, paths: ProjectPaths, target_ref: str, identity: Mapping[str, str]
) -> NsightAccess:
    from . import cli
    from .kubeconfig_target import verify_context_cluster

    target = dict(
        _one(
            [
                t
                for t in to_plain_data(config).get("deploy", {}).get("targets", [])
                if t.get("instance_id") == target_ref
            ],
            "configured target",
        )
    )
    if not identity.get("cluster_id") or not identity.get("kubernetes_uid"):
        raise NsightAccessError("Profiling access requires a recorded immutable cluster identity.")
    target.pop("kube_context", None)
    target["cluster_id"] = identity["cluster_id"]
    with ExitStack() as stack:
        env = cli._prepare_cluster_handoff_kube_env(
            config,
            paths,
            stack=stack,
            target=target,
            persist_local_kubeconfig=False,
            set_current_context=False,
            allow_terraform_output=False,
            require_renewable_auth=True,
        )
        if (
            not env
            or env.get(GRAFANA_TARGET_CLUSTER_ID_ENV) != identity["cluster_id"]
            or cli._read_kube_system_namespace_uid(
                kube_context=env[GRAFANA_TARGET_KUBE_CONTEXT_ENV], extra_env=env
            )
            != identity["kubernetes_uid"]
        ):
            raise NsightAccessError("Profiling target immutable cluster identity differs.")
        viewers = inspect_live_nsight(config, target_ref, env)
        spec = cli._mk8s_cluster_handoff_spec(
            config,
            cluster_id=identity["cluster_id"],
            access=target.get("access") or "external",
            require_renewable_auth=True,
        )
        if spec.context_name != env[GRAFANA_TARGET_KUBE_CONTEXT_ENV]:
            raise NsightAccessError("Profiling target context changed during discovery.")
        persisted = cli._persist_cluster_handoff_kubeconfig(spec=spec, set_current_context=False)
        kubeconfig = persisted or Path.home() / ".kube" / "config"
        verify_context_cluster(
            kubeconfig, context=spec.context_name, server=spec.server, ca_pem=spec.ca_pem
        )
        if (
            cli._read_kube_system_namespace_uid(
                kube_context=spec.context_name, extra_env={**env, "KUBECONFIG": str(kubeconfig)}
            )
            != identity["kubernetes_uid"]
        ):
            raise NsightAccessError("Persistent profiling kubeconfig reaches another cluster.")
        return NsightAccess(target_ref, viewers, kubeconfig.resolve(), spec.context_name)


def show_nsight_access(config_path: Path, target_ref: str) -> NsightAccess:
    from . import cli
    from .deployment_applications import recorded_application_identity
    from .deployment_cli import read_local_deployment_record

    config, paths = cli._load_context_readonly(config_path)
    record = read_local_deployment_record(config_path)
    value = record.value if record else {}
    evidence = (
        (value.get("accepted") or {}).get("evidence", {}).get("targets", {}).get(target_ref, {})
    )
    if not evidence.get("jailState"):
        raise NsightAccessError("Profiling show requires a recorded accepted Soperator target.")
    identity = recorded_application_identity(value, paths=paths, target_ref=target_ref)
    return discover_nsight_access(config, paths, target_ref, identity or {})
