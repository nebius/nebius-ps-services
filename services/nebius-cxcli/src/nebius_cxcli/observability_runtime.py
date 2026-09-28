"""Runtime Secret delivery and read-only verification of resolved connections."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from .observability_backends import WRITE_SECRET
from .observability_routing import (
    SIGNALS,
    app_row,
    collector_owners,
    connections,
    native_values,
    target_settings,
)
from .runtime_config import to_plain_data


def write_secret_namespaces(config: Any, target: str) -> tuple[str, ...]:
    payload = to_plain_data(config)
    routing = target_settings(payload, target)
    if not routing:
        return ()
    owners = collector_owners(payload, target)
    namespaces = set()
    for signal in routing.get("signals", SIGNALS):
        route = routing[signal]
        if (
            route["storage"] == "local"
            or route["remote"].get("auth") != "nebius"
            or route["remote"].get("auth_secret")
        ):
            continue
        owner = owners[signal]
        if owner == "nebius-observability-agent":
            continue  # Agent owns IAM refresh via the cluster runtime identity.
        if owner == "soperator":
            native = native_values(payload, target)
            namespace = native.get("vmStack" if signal == "metrics" else "opentelemetry", {}).get(
                "namespace", "monitoring-system" if signal == "metrics" else "logs-system"
            )
        else:
            row = app_row(payload, target, owner)
            if row is None:
                raise ValueError(f"Missing owned {signal} collector on target {target}")
            namespace = row.get("namespace", "observability")
        namespaces.add(namespace)
    return tuple(sorted(namespaces))


def runtime_secret_documents(config: Any, target: str) -> list[dict[str, Any]]:
    return [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"namespace": namespace, "name": WRITE_SECRET["name"]},
        }
        for namespace in write_secret_namespaces(config, target)
    ]


def ensure_write_secrets(
    config: Any, *, target_ref: str, extra_env: Mapping[str, str] | None, emit: Any
) -> None:
    from .grafana_runtime import (
        _ensure_namespace,
        _grafana_secret_delivery_adapter,
        _secret_has_keys,
    )
    from .iam_bootstrap import issue_observability_static_key

    payload = to_plain_data(config)
    for namespace in write_secret_namespaces(payload, target_ref):
        _ensure_namespace(namespace, extra_env=extra_env)
        if _secret_has_keys(
            namespace=namespace,
            name=WRITE_SECRET["name"],
            keys=(WRITE_SECRET["key"],),
            extra_env=extra_env,
        ):
            continue
        identity = payload["client_info"]["nebius"]
        # Keep writer and Grafana reader credentials independently delivered. No
        # token is stored in authored/rendered values or returned to the caller.
        import hashlib

        suffix = hashlib.sha256(target_ref.encode()).hexdigest()[:10]
        name = "cxcli-" + target_ref[:28].rstrip("-") + "-o11y-write-" + suffix
        issue_observability_static_key(
            project_id=identity["project_id"],
            service_account_name=name,
            service_account_description="cxcli target observability ingestion",
            key_name=(name[:40] + "-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S")),
            role_ids=["editor"],
            profile=None,
            endpoint=None,
            config_file=None,
            compensation_scope=f"observability-write:{target_ref}:{namespace}",
            delivery=_grafana_secret_delivery_adapter(
                namespace=namespace,
                name=WRITE_SECRET["name"],
                key=WRITE_SECRET["key"],
                extra_env=extra_env,
            ),
        )
        emit(f"Created observability write Secret {namespace}/{WRITE_SECRET['name']}")


def verify_connections(
    config: Any, *, target_ref: str, extra_env: Mapping[str, str] | None
) -> None:
    import time

    desired = connections(config, target_ref)
    if not desired:
        return
    from .app_mutation import current_app_mutation_authority
    from .grafana_cluster import release_client

    # Capture the owning fence before release_client installs its nested scope.
    # Installing the dispatcher itself would recursively dispatch back to itself.
    fence = current_app_mutation_authority() or (lambda: None)
    with release_client(config, target=target_ref, env=extra_env or {}, fence=fence) as client:
        actual = {item["uid"]: item for item in client.datasources()}
        for connection in desired:
            observed = actual.get(connection["uid"], {})
            if any(
                observed.get(key) != connection[key] for key in ("name", "type", "url", "isDefault")
            ):
                raise RuntimeError(
                    f"Grafana datasource {connection['name']} does not match saved configuration"
                )
            response = client.request(
                "GET", f"api/datasources/uid/{quote(connection['uid'], safe='')}/health"
            )
            if str(response.get("status", "")).upper() != "OK":
                raise RuntimeError(
                    f"Grafana datasource {connection['name']} failed its backend health check"
                )
            if connection["type"] == "prometheus" and connection["name"] in {
                "metrics-local",
                "metrics-remote",
            }:
                response = client.request(
                    "GET", f"api/datasources/proxy/uid/{connection['uid']}/api/v1/query?query=up"
                )
                samples = response.get("data", {}).get("result", [])
                if not any(
                    float(sample.get("value", [0])[0]) >= time.time() - 300 for sample in samples
                ):
                    raise RuntimeError(
                        f"Grafana datasource {connection['name']} has no recent scrape samples"
                    )


def verify_native_telemetry(
    config: Any,
    scope: Any,
    *,
    verification_id: str,
    extra_env: Mapping[str, str],
    emit: Any,
    verifier: Any,
) -> Any:
    """Verify every active metrics/log destination against the same current Pod.

    Grafana's authenticated datasource proxy reaches private stores without
    exposing them or copying backend credentials into the CLI process.
    """
    import hashlib
    import json
    from dataclasses import replace
    from urllib.parse import urlencode

    from .grafana_cluster import release_client
    from .soperator_telemetry import _timestamp_value

    desired = connections(config, scope.target_ref)
    routing = target_settings(config, scope.target_ref)
    selected = {
        signal: [
            item
            for item in desired
            if item["name"]
            in (
                [f"{signal}-local"]
                if routing[signal]["storage"] == "local"
                else [f"{signal}-remote"]
                if routing[signal]["storage"] == "remote"
                else [f"{signal}-local", f"{signal}-remote"]
            )
        ]
        for signal in ("metrics", "logs")
    }
    receipts = []
    log_queries = set()
    with release_client(
        config, target=scope.target_ref, env=extra_env, fence=lambda: None
    ) as client:
        for metrics in selected["metrics"]:
            for logs in selected["logs"]:

                def query(
                    url: str,
                    *,
                    params: Mapping[str, str],
                    metric_source=metrics,
                    log_source=logs,
                    timeout_seconds: float = 20,
                    **_kwargs: Any,
                ) -> Any:
                    metric = "/prometheus/" in url
                    source = metric_source if metric else log_source
                    prefix = f"api/datasources/proxy/uid/{source['uid']}/"
                    if metric:
                        return client.request(
                            "GET",
                            prefix + "api/v1/query?" + urlencode(params),
                            timeout_seconds=timeout_seconds,
                        )
                    if source["type"] == "loki":
                        log_queries.add(params["query"])
                        return client.request(
                            "GET",
                            prefix + "loki/api/v1/query_range?" + urlencode(params),
                            timeout_seconds=timeout_seconds,
                        )
                    if source["type"] != "victoriametrics-logs-datasource":
                        raise RuntimeError("Native log verification requires Loki or VictoriaLogs")
                    labels = {
                        "k8s.namespace.name": scope.namespace,
                        "k8s.pod.name": scope.pod_name,
                        "k8s.pod.uid": scope.pod_uid,
                        "k8s.container.name": scope.container_name,
                    }
                    selector = " AND ".join(
                        f"{key}:={json.dumps(value)}" for key, value in labels.items()
                    )
                    selector += f" AND _time:[{scope.log_not_before}, {float(params['end']) / 1e9}] | limit 1"
                    log_queries.add(selector.split(" AND _time:")[0])
                    rows = client.request(
                        "GET",
                        prefix
                        + "select/logsql/query?"
                        + urlencode({"query": selector, "limit": "1"}),
                        ndjson=True,
                        timeout_seconds=timeout_seconds,
                    )
                    if not rows:
                        return {
                            "status": "success",
                            "data": {"resultType": "streams", "result": []},
                        }
                    row = rows[0]
                    if any(row.get(key) != value for key, value in labels.items()):
                        raise RuntimeError(
                            "VictoriaLogs evidence does not match the current Pod UID"
                        )
                    timestamp = _timestamp_value(row.get("_time"))
                    return {
                        "status": "success",
                        "data": {
                            "resultType": "streams",
                            "result": [
                                {
                                    "stream": {
                                        key.replace(".", "_"): value
                                        for key, value in labels.items()
                                    },
                                    "values": [
                                        [str(int(timestamp * 1e9)), str(row.get("_msg", ""))]
                                    ],
                                }
                            ],
                        },
                    }

                receipt = verifier(
                    scope,
                    verification_id=verification_id,
                    token="",
                    require_token=False,
                    query=query,
                    emit=emit,
                )
                receipts.append(receipt)

    def fingerprint(signal: str) -> str:
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    [(item["type"], item["url"]) for item in selected[signal]], sort_keys=True
                ).encode()
            ).hexdigest()
        )

    return replace(
        receipts[-1],
        credential_source="grafana-datasource-secrets",
        metrics_endpoint_sha256=fingerprint("metrics"),
        logs_endpoint_sha256=fingerprint("logs"),
        product_log_query_sha256="sha256:"
        + hashlib.sha256(json.dumps(sorted(log_queries)).encode()).hexdigest(),
        metric_series_count=min(item.metric_series_count for item in receipts),
        product_log_stream_count=min(item.product_log_stream_count for item in receipts),
    )


def admit_vmagent_transition(
    config: Any, *, target_ref: str, extra_env: Mapping[str, str] | None
) -> None:
    """Read the exact owned VMAgent before changing destination identities."""
    from .grafana_runtime import _kubectl_json
    from .observability_backends import metrics_destinations
    from .vmagent_routing import assert_queue_identity_preserved

    payload = to_plain_data(config)
    identity = _vmagent_identity(payload, target_ref)
    if identity is None:
        return
    namespace, name = identity
    crd = _kubectl_json(
        ["get", "crd", "vmagents.operator.victoriametrics.com", "--ignore-not-found", "-o", "json"],
        extra_env=extra_env,
    )
    if not crd:
        return
    inventory = _kubectl_json(
        ["-n", namespace, "get", "vmagents.operator.victoriametrics.com", "-o", "json"],
        extra_env=extra_env,
    )
    live = next(
        (
            item
            for item in inventory.get("items", [])
            if item.get("metadata", {}).get("name") == name
        ),
        None,
    )
    if not live:
        return
    previous = [item["url"] for item in live.get("spec", {}).get("remoteWrite", [])]
    desired = [item["url"] for item in metrics_destinations(payload, target_ref)]
    assert_queue_identity_preserved(previous, desired)


def _vmagent_identity(payload: dict[str, Any], target_ref: str) -> tuple[str, str] | None:
    from urllib.parse import urlsplit

    from .observability_routing import STORE_APPS, helm_fullname, local_url

    if not target_settings(payload, target_ref):
        return None
    owner = collector_owners(payload, target_ref).get("metrics")
    if owner not in {"soperator", STORE_APPS["metrics"]}:
        return None
    if owner == "soperator":
        url = urlsplit(local_url(payload, target_ref, "metrics"))
        if not url.hostname or len(url.hostname.split(".")) < 2:
            raise ValueError("Native metrics endpoint must identify its Service and namespace")
        service, namespace, *_ = url.hostname.split(".")
        name = service.removeprefix("vmsingle-")
    else:
        row = app_row(payload, target_ref, STORE_APPS["metrics"])
        if row is None:
            raise ValueError(f"Missing owned metrics collector on target {target_ref}")
        namespace = row.get("namespace", "observability")
        name = row.get("values", {}).get("fullnameOverride") or helm_fullname(
            row["release-name"], STORE_APPS["metrics"]
        )
    return namespace, name


def verify_vmagent_routes(
    config: Any, *, target_ref: str, extra_env: Mapping[str, str] | None
) -> None:
    """Read back operator-generated arguments, not just authored Helm values."""
    from .grafana_runtime import _kubectl_json
    from .observability_backends import metrics_destinations

    payload = to_plain_data(config)
    identity = _vmagent_identity(payload, target_ref)
    if identity is None:
        return
    namespace, name = identity
    live = _kubectl_json(
        ["-n", namespace, "get", "vmagent", name, "-o", "json"], extra_env=extra_env
    )
    desired = [item["url"] for item in metrics_destinations(payload, target_ref)]
    if [item["url"] for item in live.get("spec", {}).get("remoteWrite", [])] != desired:
        raise RuntimeError("Live VMAgent destinations differ from the saved routing")
    kind = "statefulset" if live.get("spec", {}).get("statefulMode") else "deployment"
    deployment = _kubectl_json(
        ["-n", namespace, "get", kind, "vmagent-" + name, "-o", "json"], extra_env=extra_env
    )
    if not any(
        ref.get("uid") == live.get("metadata", {}).get("uid")
        for ref in deployment.get("metadata", {}).get("ownerReferences", [])
    ):
        raise RuntimeError("VMAgent Deployment is not owned by the selected VMAgent")
    status = deployment.get("status", {})
    replicas = deployment.get("spec", {}).get("replicas", 1)
    if (
        not replicas
        or status.get("observedGeneration") != deployment.get("metadata", {}).get("generation")
        or status.get("updatedReplicas") != replicas
        or status.get("readyReplicas") != replicas
    ):
        raise RuntimeError("VMAgent destination rollout is not ready at the current generation")
    args = [
        arg
        for container in deployment.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
        if container.get("name") == "vmagent"
        for arg in container.get("args", [])
    ]
    urls = [
        url
        for arg in args
        if arg.lstrip("-").startswith("remoteWrite.url=")
        for url in arg.split("=", 1)[1].split(",")
    ]
    if urls != desired:
        raise RuntimeError("Running VMAgent arguments contain unexpected remoteWrite URLs")
