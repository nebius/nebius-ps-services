"""Live, credential-free Grafana access instructions for operators."""

from __future__ import annotations

import base64
import json
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import kubernetes_process
from .grafana_database import OWNER_ANNOTATION, app_owner
from .grafana_runtime import GRAFANA_TARGET_CLUSTER_ID_ENV, GRAFANA_TARGET_KUBE_CONTEXT_ENV
from .paths import ProjectPaths
from .runtime_config import to_plain_data

if TYPE_CHECKING:
    from . import cli


class GrafanaAccessError(RuntimeError):
    """The live target cannot supply verified operator access instructions."""


@dataclass(frozen=True)
class GrafanaAccess:
    target_ref: str
    namespace: str
    release_name: str
    service_name: str
    service_port: int
    admin_secret_name: str
    admin_password_key: str
    admin_user: str
    kube_context: str
    kubeconfig: Path

    @property
    def browser_url(self) -> str:
        return "http://127.0.0.1:3000"

    def commands(self, config_path: Path) -> tuple[tuple[str, str], ...]:
        kubectl = [
            "kubectl",
            "--kubeconfig",
            str(self.kubeconfig),
            "--context",
            self.kube_context,
            "--namespace",
            self.namespace,
        ]
        key = self.admin_password_key.replace(".", "\\.")
        return (
            (
                "Port forwarding (keep this running in its terminal)",
                shlex.join(
                    [
                        *kubectl,
                        "port-forward",
                        "--address",
                        "127.0.0.1",
                        f"service/{self.service_name}",
                        f"3000:{self.service_port}",
                    ]
                ),
            ),
            (
                "Display the Grafana password (run in another terminal)",
                shlex.join(
                    [
                        *kubectl,
                        "get",
                        "secret",
                        self.admin_secret_name,
                        "-o",
                        f"jsonpath={{.data.{key}}}",
                    ]
                )
                + " | base64 --decode && printf '\\n'",
            ),
            (
                "Show freshly verified access instructions again",
                shlex.join(
                    [
                        "nebius-cxcli",
                        "grafana",
                        "show",
                        "--config",
                        str(config_path),
                        "--target",
                        self.target_ref,
                    ]
                ),
            ),
        )

    def terminal_lines(self, config_path: Path) -> list[str]:
        lines = [
            f"# Grafana {self.target_ref}: user {self.admin_user}",
            f"# Open {self.browser_url} after starting port forwarding.",
        ]
        for label, command in self.commands(config_path):
            lines.extend([f"# {label}", command])
        return lines

    def markdown_lines(self, config_path: Path) -> list[str]:
        lines = [
            f"- Login username: `{self.admin_user}`",
            f"- Browser: [{self.browser_url}]({self.browser_url}) after starting port forwarding.",
            "",
        ]
        for label, command in self.commands(config_path):
            lines.extend([f"{label}:", "", "```bash", command, "```", ""])
        return lines


def _read(
    env: Mapping[str, str],
    resource: str,
    *,
    namespace: str = "",
    name: str = "",
    output: str = "json",
    selector: str = "",
) -> Any:
    """Only bounded kubectl get; Secret callers must project non-password output."""
    from . import cli

    if resource == "secret" and output == "json":
        raise GrafanaAccessError("Access discovery must not retrieve Secret bodies.")
    command = ["kubectl", "--context", env[GRAFANA_TARGET_KUBE_CONTEXT_ENV], "get", resource]
    if namespace:
        command.extend(["--namespace", namespace])
    else:
        command.append("--all-namespaces")
    if name:
        command.append(name)
    if selector:
        command.extend(["--selector", selector])
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
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GrafanaAccessError(
            f"Cannot inspect live Grafana {resource}; check cluster access."
        ) from exc
    if result.returncode:
        raise GrafanaAccessError(
            f"Cannot inspect live Grafana {resource}; check cluster access and permissions."
        )
    if output != "json":
        return result.stdout
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise GrafanaAccessError(f"Invalid response inspecting Grafana {resource}.") from exc


def _one(items: Sequence[Any], description: str) -> Any:
    if len(items) != 1:
        raise GrafanaAccessError(f"Expected exactly one {description}; found {len(items)}.")
    return items[0]


def _helm_owned(resource: Mapping[str, Any], release: str, namespace: str) -> bool:
    metadata = resource.get("metadata", {})
    annotations = metadata.get("annotations", {})
    return bool(
        metadata.get("uid")
        and not metadata.get("deletionTimestamp")
        and annotations.get("meta.helm.sh/release-name") == release
        and annotations.get("meta.helm.sh/release-namespace") == namespace
    )


def _secret_ref(container: Mapping[str, Any], variable: str) -> tuple[str, str]:
    entry = _one([e for e in container.get("env", []) if e.get("name") == variable], variable)
    ref = entry.get("valueFrom", {}).get("secretKeyRef", {})
    if "value" in entry or not ref.get("name") or not ref.get("key") or ref.get("optional"):
        raise GrafanaAccessError(
            f"Grafana {variable} must reference an existing required Secret key."
        )
    return str(ref["name"]), str(ref["key"])


def _require_unchanged(
    env: Mapping[str, str], resources: Sequence[tuple[str, Mapping[str, Any]]]
) -> None:
    for resource, original in resources:
        meta = original["metadata"]
        current = _read(env, resource, namespace=meta["namespace"], name=meta["name"])
        observed = current.get("metadata", {})
        readiness = (
            "conditions",
            "observedGeneration",
            "replicas",
            "updatedReplicas",
            "availableReplicas",
        )
        if (
            observed.get("uid") != meta["uid"]
            or observed.get("deletionTimestamp")
            or observed.get("annotations") != meta.get("annotations")
            or current.get("spec") != original.get("spec")
            or any(
                current.get("status", {}).get(key) != original.get("status", {}).get(key)
                for key in readiness
            )
        ):
            raise GrafanaAccessError("Grafana changed during access discovery; rerun grafana show.")


def _cluster_uid(env: Mapping[str, str]) -> str:
    from . import cli

    try:
        return cli._read_kube_system_namespace_uid(
            kube_context=env[GRAFANA_TARGET_KUBE_CONTEXT_ENV], extra_env=env
        )
    except subprocess.SubprocessError:
        raise GrafanaAccessError(
            "Unable to verify Grafana cluster identity; check connectivity and retry."
        ) from None


def inspect_live_grafana(config: Any, target_ref: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Read live owned workload bindings, never local desired Grafana values."""
    owner = app_owner(config, target_ref)
    releases = _read(env, "helmreleases.helm.toolkit.fluxcd.io").get("items", [])
    owned = [
        r
        for r in releases
        if r.get("metadata", {}).get("annotations", {}).get(OWNER_ANNOTATION) == owner
        and (
            (r.get("status", {}).get("history") or [{}])[0].get("chartName") == "grafana"
            or r.get("spec", {}).get("chart", {}).get("spec", {}).get("chart") == "grafana"
        )
    ]
    release = _one(owned, "owned Grafana HelmRelease")
    metadata, spec, status = release["metadata"], release["spec"], release.get("status", {})
    ready: Mapping[str, Any] = next(
        (c for c in status.get("conditions", []) if c.get("type") == "Ready"), {}
    )
    if (
        metadata.get("deletionTimestamp")
        or spec.get("suspend")
        or spec.get("kubeConfig")
        or not metadata.get("uid")
        or not metadata.get("generation")
        or ready.get("status") != "True"
        or ready.get("observedGeneration", status.get("observedGeneration"))
        != metadata["generation"]
    ):
        raise GrafanaAccessError("Grafana HelmRelease is not ready at its current generation.")
    namespace = spec.get("targetNamespace") or metadata["namespace"]
    release_name = spec.get("releaseName") or metadata["name"]
    selector = f"app.kubernetes.io/instance={release_name}"
    workloads = _read(env, "deployments", namespace=namespace, selector=selector).get("items", [])
    workload = _one(
        [
            w
            for w in workloads
            if _helm_owned(w, release_name, namespace)
            and any(
                c.get("name") == "grafana"
                for c in w.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            )
        ],
        "owned Grafana Deployment",
    )
    ws, live = workload["spec"], workload.get("status", {})
    replicas = ws.get("replicas", 1)
    if (
        not isinstance(replicas, int)
        or replicas < 1
        or live.get("observedGeneration") != workload["metadata"].get("generation")
        or any(
            live.get(key, 0) != replicas
            for key in ("updatedReplicas", "availableReplicas", "replicas")
        )
    ):
        raise GrafanaAccessError("Grafana Deployment has not completed its rollout.")
    template = ws["template"]
    container = _one(
        [c for c in template["spec"]["containers"] if c.get("name") == "grafana"],
        "Grafana container",
    )
    admin_secret, password_key = _secret_ref(container, "GF_SECURITY_ADMIN_PASSWORD")
    user_secret, user_key = _secret_ref(container, "GF_SECURITY_ADMIN_USER")
    if (user_secret, user_key) == (admin_secret, password_key):
        raise GrafanaAccessError("Grafana username and password must use distinct Secret keys.")
    # Project key names only; no password data enters this process.
    keys = _read(
        env,
        "secret",
        namespace=namespace,
        name=admin_secret,
        output='go-template={{range $key, $value := .data}}{{printf "%s\\n" $key}}{{end}}',
    ).splitlines()
    if password_key not in keys:
        raise GrafanaAccessError("Grafana admin password Secret key is missing.")
    key = user_key.replace(".", "\\.")
    encoded_user = _read(
        env, "secret", namespace=namespace, name=user_secret, output=f"jsonpath={{.data.{key}}}"
    )
    try:
        admin_user = base64.b64decode(encoded_user, validate=True).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise GrafanaAccessError("Grafana admin username is unavailable.") from exc
    if not admin_user or any(ord(c) < 32 or ord(c) == 127 for c in admin_user):
        raise GrafanaAccessError("Grafana admin username is empty or contains control characters.")
    http = container.get("readinessProbe", {}).get("httpGet", {})
    http_port = http.get("port")
    if isinstance(http_port, str):
        http_port = _one(
            [
                p.get("containerPort")
                for p in container.get("ports", [])
                if p.get("name") == http_port
            ],
            "Grafana HTTP container port",
        )
    if not isinstance(http_port, int) or http.get("scheme", "HTTP") != "HTTP":
        raise GrafanaAccessError("Grafana must expose an unambiguous HTTP readiness port.")
    services = _read(env, "services", namespace=namespace, selector=selector).get("items", [])
    candidates = []
    for service in services:
        ss = service.get("spec", {})
        labels = template.get("metadata", {}).get("labels", {})
        if (
            not _helm_owned(service, release_name, namespace)
            or ss.get("clusterIP") == "None"
            or not ss.get("selector")
            or any(labels.get(k) != v for k, v in ss["selector"].items())
        ):
            continue
        for port in ss.get("ports", []):
            destination = port.get("targetPort", port.get("port"))
            if isinstance(destination, str):
                matches = [
                    p.get("containerPort")
                    for p in container.get("ports", [])
                    if p.get("name") == destination
                ]
                destination = matches[0] if len(matches) == 1 else None
            if port.get("protocol", "TCP") == "TCP" and destination == http_port:
                candidates.append((service, port.get("port")))
    service, port = _one(candidates, "owned Grafana HTTP Service port")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise GrafanaAccessError("Grafana Service has an invalid HTTP port.")
    # Reject an intervening rollout rather than combining two generations.
    _require_unchanged(
        env, (("helmrelease", release), ("deployment", workload), ("service", service))
    )
    return dict(
        target_ref=target_ref,
        namespace=namespace,
        release_name=release_name,
        service_name=service["metadata"]["name"],
        service_port=port,
        admin_secret_name=admin_secret,
        admin_password_key=password_key,
        admin_user=admin_user,
    )


def discover_grafana_access(
    config: Any, paths: ProjectPaths, target_ref: str, identity: Mapping[str, str]
) -> GrafanaAccess:
    from . import cli
    from .kubeconfig_target import verify_context_cluster

    targets = to_plain_data(config).get("deploy", {}).get("targets", [])
    target = dict(
        _one([t for t in targets if t.get("instance_id") == target_ref], "configured target")
    )
    if not identity.get("cluster_id") or not identity.get("kubernetes_uid"):
        raise GrafanaAccessError(
            "Grafana access requires a recorded immutable cluster identity; deploy the target first."
        )
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
            or _cluster_uid(env) != identity["kubernetes_uid"]
        ):
            raise GrafanaAccessError("Grafana target immutable cluster identity differs.")
        details = inspect_live_grafana(config, target_ref, env)
        spec = cli._mk8s_cluster_handoff_spec(
            config,
            cluster_id=identity["cluster_id"],
            access=target.get("access") or "external",
            require_renewable_auth=True,
        )
        if spec.context_name != env[GRAFANA_TARGET_KUBE_CONTEXT_ENV]:
            raise GrafanaAccessError("Grafana target context changed during discovery.")
        persisted = cli._persist_cluster_handoff_kubeconfig(spec=spec, set_current_context=False)
        kubeconfig = persisted or Path.home() / ".kube" / "config"
        verify_context_cluster(
            kubeconfig, context=spec.context_name, server=spec.server, ca_pem=spec.ca_pem
        )
        durable_env = {**env, "KUBECONFIG": str(kubeconfig)}
        if _cluster_uid(durable_env) != identity["kubernetes_uid"]:
            raise GrafanaAccessError(
                "Persistent Grafana kubeconfig does not reach the verified cluster."
            )
        return GrafanaAccess(
            **details, kube_context=spec.context_name, kubeconfig=kubeconfig.resolve()
        )


def show_grafana_access(config_path: Path, target_ref: str) -> GrafanaAccess:
    from . import cli
    from .deployment_applications import recorded_application_identity
    from .deployment_cli import read_local_deployment_record

    config, paths = cli._load_context_readonly(config_path)
    record = read_local_deployment_record(config_path)
    identity = recorded_application_identity(
        record.value if record else {}, paths=paths, target_ref=target_ref
    )
    return discover_grafana_access(config, paths, target_ref, identity or {})


def complete_grafana_handoff(
    config: Any,
    source: ProjectPaths,
    destination: ProjectPaths,
    summary: cli.DeployRunSummary,
    selected_targets: Sequence[str],
    validations: Sequence[Mapping[str, Any]],
) -> cli.DeployRunSummary:
    """Post-acceptance presentation cannot undo successful deployment acceptance."""
    from dataclasses import replace

    from .deployment_reports import publish_deployment_reports
    from .grafana_runtime import grafana_enabled_for_target
    from .inventory_ops import write_inventory

    targets = [
        ref for ref in selected_targets if grafana_enabled_for_target(config, target_ref=ref)
    ]
    if not targets:
        return summary
    accesses, errors = [], {}
    for ref in targets:
        try:
            accesses.append(
                discover_grafana_access(
                    config, source, ref, (summary.cluster_identities or {}).get(ref, {})
                )
            )
        except Exception as exc:
            errors[ref] = (
                str(exc)
                if isinstance(exc, GrafanaAccessError)
                else "Unable to verify live access; rerun grafana show."
            )
    summary = replace(
        summary,
        grafana_access=tuple(accesses),
        grafana_access_errors=tuple(f"{ref}: {error}" for ref, error in errors.items()),
    )
    try:
        write_inventory(
            config,
            source,
            validations=validations,
            grafana_access=accesses,
            grafana_access_errors=errors,
            grafana_command_config_path=destination.config_path,
        )
        report = publish_deployment_reports(
            source, destination, validations, summary.validation_report
        )
        return replace(summary, validation_report=report)
    except Exception:
        return replace(
            summary,
            grafana_access_errors=(
                *summary.grafana_access_errors,
                "Could not publish the refreshed access report; rerun grafana show.",
            ),
        )
