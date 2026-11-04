"""Exact-run telemetry proof through collectors, storage and Grafana queries."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from . import kubernetes_process, observability_probe
from .app_mutation import assert_app_mutation_authority, guarded_app_manifest
from .grafana_api import GrafanaAuth, GrafanaClient, basic_auth
from .grafana_database import app_owner
from .grafana_runtime import (
    GrafanaReleaseSpec,
    _grafana_admin_credentials,
    grafana_api_endpoint,
    grafana_release_specs,
)
from .observability_intent import effective_write_destinations, trace_endpoint
from .observability_routing import connections, effective_signals, target_settings

PROBE_IMAGE = "python:3.13.12-alpine3.23@sha256:bb1f2fdb1065c85468775c9d680dcd344f6442a2d1181ef7916b60a623f11d40"
MANAGED_GRAFANA = "https://grafana.nebius.dev/"
PROBE_NAMESPACE = "observability"
MAX_WAIT_SECONDS = 300


def freeze_validation(payload: dict[str, Any], target: str) -> dict[str, Any]:
    routing = target_settings(payload, target)
    signals = effective_signals(payload, target)
    identity = payload.get("client_info", {}).get("nebius", {})
    if not all(identity.get(key) for key in ("project_id", "region_id")):
        raise ValueError(
            "Observability delivery verification requires the target Nebius project and region"
        )
    required = {
        f"{signal}-{location}"
        for signal in signals
        for location in ("local", "remote")
        if routing[signal]["storage"] in {location, "both"}
    }
    return {
        "project_id": identity["project_id"],
        "region_id": identity["region_id"],
        "target_ref": target,
        "signals": signals,
        "app_owner": app_owner(payload, target),
        "configuration_sha256": hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest(),
        "connections": [item for item in connections(payload, target) if item["name"] in required],
        "grafana": [asdict(spec) for spec in grafana_release_specs(payload, target_ref=target)],
        "write_destinations": effective_write_destinations(payload, target),
        "trace_endpoint": trace_endpoint(),
        "probe_image": PROBE_IMAGE,
        "probe_sha256": hashlib.sha256(Path(observability_probe.__file__).read_bytes()).hexdigest(),
    }


def kubectl(args: list[str], env: Mapping[str, str] | None, body: Any = None) -> dict[str, Any]:
    assert_app_mutation_authority()
    result = kubernetes_process.run(
        ["kubectl", *args],
        env={**os.environ, **(env or {})},
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        # Neither Secret values nor arbitrary API response bodies enter reports.
        raise RuntimeError(f"Observability Kubernetes {args[0]} failed (exit {result.returncode})")
    value = json.loads(result.stdout or "{}")
    if not isinstance(value, dict):
        raise RuntimeError("Observability Kubernetes API returned an invalid object")
    return value


def probe_document(spec: Mapping[str, Any], run_id: str, trace_id: str) -> dict[str, Any]:
    source = Path(observability_probe.__file__).read_bytes()
    if (
        spec["probe_image"] != PROBE_IMAGE
        or spec["probe_sha256"] != hashlib.sha256(source).hexdigest()
    ):
        raise RuntimeError(
            "Frozen observability probe differs from the installed runtime; rerender the bundle"
        )
    identity = {
        "cxcli_run": run_id,
        "cxcli_target": spec["target_ref"],
        "cxcli_project": spec["project_id"],
        "cxcli_generation": spec["configuration_sha256"],
    }
    settings = {
        "identity": identity,
        "trace_id": trace_id,
        "signals": spec["signals"],
        "trace_endpoint": spec["trace_endpoint"],
    }
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"cxcli-observability-{run_id}",
            "namespace": PROBE_NAMESPACE,
            "labels": {"app.kubernetes.io/name": "cxcli-observability-probe", "cxcli-run": run_id},
            "annotations": {
                "prometheus.io/scrape": "true",
                "prometheus.io/port": "8080",
                "cxcli.nebius.com/app-owner": spec["app_owner"],
                "prometheus.io/path": "/metrics",
            },
        },
        "spec": {
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "affinity": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "nebius.com/gpu",
                                        "operator": "NotIn",
                                        "values": ["true"],
                                    }
                                ]
                            }
                        ]
                    }
                }
            },
            "activeDeadlineSeconds": 600,
            "terminationGracePeriodSeconds": 5,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "probe",
                    "image": spec["probe_image"],
                    "command": ["python3", "-B", "-c", source.decode(), json.dumps(settings)],
                    "ports": [{"name": "metrics", "containerPort": 8080}],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {"cpu": "10m", "memory": "32Mi"},
                        "limits": {"cpu": "100m", "memory": "64Mi"},
                    },
                }
            ],
        },
    }


def query_path(
    kind: str, identity: Mapping[str, str], trace_id: str, started: float
) -> tuple[str, bool]:
    if kind == "prometheus":
        selector = ",".join(f"{k}={json.dumps(v)}" for k, v in identity.items())
        # timestamp() checks the actual stored sample, not the instant-query evaluation time.
        return "api/v1/query?" + urlencode(
            {"query": f"timestamp(cxcli_observability_probe{{{selector}}})"}
        ), False
    if kind == "loki":
        query = '{__bucket__="default"}' + "".join(
            f" |= {json.dumps(v)}" for v in identity.values()
        )
        return "loki/api/v1/query_range?" + urlencode(
            {"query": query, "start": int(started * 1e9), "limit": 20}
        ), False
    if kind == "victoriametrics-logs-datasource":
        query = " AND ".join(json.dumps(v) for v in identity.values())
        return "select/logsql/query?" + urlencode(
            {"query": query, "start": datetime.fromtimestamp(started, UTC).isoformat(), "limit": 20}
        ), True
    if kind in {"jaeger", "tempo"}:
        return "api/traces/" + trace_id, False
    raise RuntimeError(f"Unsupported observability query type: {kind}")


def _trace_attributes(value: Any) -> dict[str, str]:
    """Read OTLP resource attributes and Jaeger process/span tags by key."""
    result: dict[str, str] = {}
    if isinstance(value, dict):
        if isinstance(value.get("key"), str):
            item = value.get("value")
            if isinstance(item, str):
                result[value["key"]] = item
            elif isinstance(item, dict) and isinstance(item.get("stringValue"), str):
                result[value["key"]] = item["stringValue"]
        for item in value.values():
            result.update(_trace_attributes(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_trace_attributes(item))
    return result


def matches(
    kind: str, result: Any, identity: Mapping[str, str], trace_id: str, started: float
) -> bool:
    if kind == "prometheus":
        if not isinstance(result, dict) or result.get("status") != "success":
            return False
        for row in result.get("data", {}).get("result", []):
            try:
                if (
                    all(row.get("metric", {}).get(k) == v for k, v in identity.items())
                    and started <= float(row["value"][1]) <= time.time() + 30
                ):
                    return True
            except (KeyError, ValueError, TypeError, IndexError):
                continue
        return False
    if kind in {"loki", "victoriametrics-logs-datasource"}:
        lines: list[str] = []
        if kind == "loki" and isinstance(result, dict) and result.get("status") == "success":
            for stream in result.get("data", {}).get("result", []):
                lines.extend(value[1] for value in stream.get("values", []) if len(value) >= 2)
        elif isinstance(result, list):
            lines = [row.get("_msg", "") for row in result if isinstance(row, dict)]
        for line in lines:
            try:
                # Container parsers can preserve an envelope around the JSON log.
                message = json.loads(str(line)[str(line).index("{") :])
                if (
                    all(message.get(k) == v for k, v in identity.items())
                    and started <= float(message["timestamp"]) <= time.time() + 30
                ):
                    return True
            except (ValueError, TypeError, KeyError):
                continue
        return False
    attributes = _trace_attributes(result)
    if not all(attributes.get(key) == value for key, value in identity.items()):
        return False
    trace_forms = {trace_id, base64.b64encode(bytes.fromhex(trace_id)).decode()}

    # Require the matching span itself to be fresh, not an unrelated span.
    def fresh(value):
        if isinstance(value, dict):
            if value.get("traceID", value.get("traceId")) in trace_forms:
                for key, scale in (("startTime", 1e6), ("startTimeUnixNano", 1e9)):
                    try:
                        stamp = float(value[key]) / scale
                        if started <= stamp <= time.time() + 30:
                            return True
                    except (KeyError, TypeError, ValueError):
                        pass
            return any(fresh(item) for item in value.values())
        return isinstance(value, list) and any(fresh(item) for item in value)

    return fresh(result)


def _managed_client() -> GrafanaClient:
    from .sdk_auth import acquire_operator_access_token

    profile = os.environ.get("NEBIUS_PROFILE", "").strip()
    if not profile or profile.startswith("codex-agent-"):
        raise RuntimeError(
            "Managed Grafana verification requires NEBIUS_PROFILE naming a human Nebius CLI profile"
        )
    token = acquire_operator_access_token(
        profile=profile, interactive=False, context="managed observability verification"
    )
    return GrafanaClient(
        MANAGED_GRAFANA,
        GrafanaAuth("Bearer " + token),
        assert_authority=assert_app_mutation_authority,
    )


def _managed_datasource(
    client: GrafanaClient, connection: Mapping[str, Any]
) -> tuple[GrafanaClient, str]:
    found = []
    orgs = client.request("GET", "api/user/orgs")
    if not isinstance(orgs, list) or not orgs or len(orgs) > 100:
        raise RuntimeError(
            "Managed Grafana organization discovery did not return a bounded organization list"
        )
    for org in orgs:
        selected = GrafanaClient(
            client.url,
            client.auth,
            org_id=int(org["orgId"]),
            assert_authority=assert_app_mutation_authority,
        )
        for source in selected.request("GET", "api/datasources"):
            if source.get("type") == connection["type"] and source.get("url", "").rstrip(
                "/"
            ) == connection["url"].rstrip("/"):
                found.append((selected, source["uid"]))
    if len(found) != 1:
        raise RuntimeError(
            f"Managed Grafana requires one unambiguous project datasource for {connection['name']}; found {len(found)}"
        )
    return found[0]


def preflight(validations: list[dict[str, Any]], *, reports_dir: Path | None = None) -> None:
    """Prove managed portal read access before deployment mutates infrastructure."""
    required = [
        connection
        for spec in validations
        for connection in spec.get("e2e", {}).get("connections", [])
        if connection.get("auth") == "nebius"
    ]
    try:
        for spec in validations:
            contract = spec.get("e2e", {})
            if (
                contract
                and not contract.get("grafana")
                and any(
                    connection.get("auth") != "nebius" for connection in contract["connections"]
                )
            ):
                raise RuntimeError(
                    "Local or custom observability delivery requires a configured Grafana query path; use grafana install to configure the in-cluster workflow"
                )
        if not required:
            return
        client = _managed_client()
        for connection in {item["url"]: item for item in required}.values():
            _managed_datasource(client, connection)
    except Exception as exc:
        if reports_dir is not None:
            reports_dir.mkdir(parents=True, exist_ok=True)
            for spec in validations:
                if spec.get("e2e"):
                    (reports_dir / spec["report_file"]).write_text(
                        json.dumps(
                            {
                                "validation": spec["name"],
                                "kind": spec["kind"],
                                "target_ref": spec["target_ref"],
                                "passed": False,
                                "checked_at": datetime.now(UTC).isoformat(),
                                "checks": [],
                                "error": "Observability preflight failed: " + str(exc),
                            },
                            indent=2,
                        )
                        + "\n"
                    )
        raise


def _backend_client(
    stack: ExitStack, connection: Mapping[str, Any], managed: GrafanaClient | None, env
) -> GrafanaClient:
    url = connection["url"]
    parsed = urlsplit(url)
    auth = GrafanaAuth("")
    if connection.get("auth_secret"):
        secret = connection["auth_secret"]
        data = kubectl(
            [
                "-n",
                connection.get("namespace", PROBE_NAMESPACE),
                "get",
                "secret",
                secret["name"],
                "-o",
                "json",
            ],
            env,
        )
        auth = GrafanaAuth(
            "Bearer " + base64.b64decode(data["data"][secret["key"]], validate=True).decode()
        )
    elif connection.get("auth") == "nebius":
        if managed is None:
            raise RuntimeError("Managed backend read identity is unavailable")
        auth = managed.auth
    if parsed.hostname and (
        parsed.hostname.endswith(".svc") or parsed.hostname.endswith(".svc.cluster.local")
    ):
        service, namespace, *_ = parsed.hostname.split(".")
        spec = GrafanaReleaseSpec("", namespace, service, service, "", "", "", "", "", "")
        base = stack.enter_context(grafana_api_endpoint(spec, extra_env=env, loopback_only=True))
        url = base + parsed.path.lstrip("/")
    return GrafanaClient(url, auth, assert_authority=assert_app_mutation_authority)


def readers(
    spec: Mapping[str, Any], stack: ExitStack, env
) -> list[tuple[str, dict, GrafanaClient, str]]:
    managed = (
        _managed_client()
        if any(item.get("auth") == "nebius" for item in spec["connections"])
        else None
    )
    local_clients = []
    for raw in spec["grafana"]:
        release = GrafanaReleaseSpec(**raw)
        credentials = _grafana_admin_credentials(release, extra_env=env)
        if not credentials:
            raise RuntimeError(
                "Local Grafana administrator Secret is unavailable for query verification"
            )
        url = stack.enter_context(grafana_api_endpoint(release, extra_env=env, loopback_only=True))
        client = GrafanaClient(
            url, basic_auth(*credentials), assert_authority=assert_app_mutation_authority
        )
        local_clients.append((client, client.request("GET", "api/datasources")))
    result = []
    for connection in spec["connections"]:
        result.append(
            (
                "Backend " + connection["name"],
                connection,
                _backend_client(stack, connection, managed, env),
                "",
            )
        )
        grafana_count = 0
        if connection.get("auth") == "nebius" and managed is not None:
            client, uid = _managed_datasource(managed, connection)
            result.append(
                (
                    "Nebius Grafana " + connection["name"],
                    connection,
                    client,
                    f"api/datasources/proxy/uid/{quote(uid, safe='')}/",
                )
            )
            grafana_count += 1
        for client, sources in local_clients:
            matching = [
                source
                for source in sources
                if source.get("uid") == connection["uid"]
                and source.get("type") == connection["type"]
                and source.get("url", "").rstrip("/") == connection["url"].rstrip("/")
            ]
            if len(matching) != 1:
                raise RuntimeError(f"Local Grafana datasource differs from {connection['name']}")
            result.append(
                (
                    "Local Grafana " + connection["name"],
                    connection,
                    client,
                    f"api/datasources/proxy/uid/{quote(connection['uid'], safe='')}/",
                )
            )
            grafana_count += 1
        if not grafana_count:
            raise RuntimeError(f"No Grafana query path is configured for {connection['name']}")
    return result


def run_validation(
    spec: Mapping[str, Any],
    *,
    reports_dir: Path,
    extra_env,
    emit: Callable[[str], None] | None = None,
) -> Path:
    contract = spec["e2e"]
    run_id, trace_id = uuid.uuid4().hex, uuid.uuid4().hex
    started = time.time()
    identity = {
        "cxcli_run": run_id,
        "cxcli_target": contract["target_ref"],
        "cxcli_project": contract["project_id"],
        "cxcli_generation": contract["configuration_sha256"],
    }
    report = {
        "validation": spec["name"],
        "kind": spec["kind"],
        "target_ref": spec["target_ref"],
        "run_id": run_id,
        "trace_id": trace_id,
        "project_id": contract["project_id"],
        "configuration_sha256": contract["configuration_sha256"],
        "contract_sha256": hashlib.sha256(
            json.dumps(contract, sort_keys=True).encode()
        ).hexdigest(),
        "checked_at": datetime.now(UTC).isoformat(),
        "passed": False,
        "checks": [],
        "write_destinations": contract["write_destinations"],
    }
    path = reports_dir / spec["report_file"]
    pod = None
    error = None
    try:
        with ExitStack() as stack:
            queries = readers(contract, stack, extra_env)
            if not queries:
                raise RuntimeError("Observability verification has no required query paths")
            document = guarded_app_manifest(probe_document(contract, run_id, trace_id))
            if document is None:
                raise RuntimeError("Observability probe ownership was not admitted")
            try:
                pod = kubectl(["create", "-f", "-", "-o", "json"], extra_env, document)
            except Exception:
                # A timeout can follow successful creation. Reconcile only this
                # unpredictable name and owner before attempting UID-bound cleanup.
                found = kubectl(
                    [
                        "-n",
                        PROBE_NAMESPACE,
                        "get",
                        "pod",
                        document["metadata"]["name"],
                        "--ignore-not-found",
                        "-o",
                        "json",
                    ],
                    extra_env,
                )
                metadata = found.get("metadata", {})
                if (
                    metadata.get("labels", {}).get("cxcli-run") == run_id
                    and metadata.get("annotations", {}).get("cxcli.nebius.com/app-owner")
                    == contract["app_owner"]
                ):
                    pod = found
                raise
            pending = list(queries)
            deadline = time.monotonic() + MAX_WAIT_SECONDS
            while pending and time.monotonic() < deadline:
                for item in list(pending):
                    name, connection, client, prefix = item
                    query, ndjson = query_path(connection["type"], identity, trace_id, started)
                    try:
                        result = client.request(
                            "GET",
                            prefix + query,
                            ndjson=ndjson,
                            timeout_seconds=min(10, max(0.1, deadline - time.monotonic())),
                        )
                        passed = matches(connection["type"], result, identity, trace_id, started)
                    except RuntimeError:
                        passed = False
                    if passed:
                        pending.remove(item)
                        report["checks"].append(
                            {
                                "name": name,
                                "passed": True,
                                "summary": "Current run, target and project evidence found",
                            }
                        )
                        if emit:
                            emit(f"OK: {name} received this run's telemetry.")
                if pending:
                    time.sleep(min(5, max(0, deadline - time.monotonic())))
            for name, *_ in pending:
                report["checks"].append(
                    {
                        "name": name,
                        "passed": False,
                        "summary": "Required current-run telemetry was not queryable before the deadline",
                    }
                )
            if pending:
                raise RuntimeError(
                    "Observability delivery verification failed; inspect the per-signal report"
                )
            report["passed"] = True
    except Exception as exc:
        error = exc
        report["error"] = str(exc)
    finally:
        if pod:
            try:
                metadata = pod["metadata"]
                kubectl(
                    [
                        "delete",
                        "--raw",
                        f"/api/v1/namespaces/{PROBE_NAMESPACE}/pods/{metadata['name']}",
                        "-f",
                        "-",
                    ],
                    extra_env,
                    {
                        "apiVersion": "v1",
                        "kind": "DeleteOptions",
                        "preconditions": {"uid": metadata["uid"]},
                        "gracePeriodSeconds": 0,
                    },
                )
                report["cleanup"] = "passed"
            except Exception:
                report["cleanup"] = "failed"
                report["passed"] = False
                error = error or RuntimeError(
                    "Observability probe cleanup failed; Pod has a bounded active deadline"
                )
        if error:
            report["error"] = str(error)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n")
    if error:
        raise RuntimeError(str(error)) from error
    return path
