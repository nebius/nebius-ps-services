"""Explicit read-only Nebius Observability verification for Soperator."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SOPERATOR_OBSERVABILITY_RECEIPT_SCHEMA = "nebius-cxcli.soperator-observability-verification.v1"
SOPERATOR_OBSERVABILITY_CREDENTIAL_SOURCE = "operator-nebius-cli"
_SAFE_LABEL_VALUE = re.compile(r"^[A-Za-z0-9_.:/-]+$")
_SAFE_PROJECT_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_HTTP_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class SoperatorObservabilityScope:
    project_id: str
    cluster_id: str
    namespace: str
    slurm_cluster: str
    release: str
    target_ref: str
    verification_started_at: float
    metric_not_before: float
    log_not_before: float
    pod_name: str
    pod_uid: str
    container_name: str


@dataclass(frozen=True)
class SoperatorObservabilityReceipt:
    schema: str
    verification_id: str
    status: str
    failure_code: str
    http_status: int | None
    target_ref_sha256: str
    release: str
    credential_source: str
    metrics_endpoint_sha256: str
    logs_endpoint_sha256: str
    metric_query_sha256: str
    product_log_query_sha256: str
    workload_identity_sha256: str
    verification_started_at: str
    metric_not_before: str
    log_not_before: str
    observed_at: str
    attempts: int
    metric_series_count: int
    metric_newest_sample_at: str
    product_log_stream_count: int
    product_log_newest_sample_at: str


@dataclass(frozen=True)
class SoperatorObservabilityWorkload:
    namespace: str
    pod_name: str
    pod_uid: str
    container_name: str
    pod_started_at: float


class SoperatorObservabilityFailure(RuntimeError):
    """Sanitized verifier failure suitable for a non-secret receipt."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.attempts = attempts


class _ObservabilityDeadlineExceeded(RuntimeError):
    """Internal signal that no further backend request may start."""


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _timestamp_value(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def select_soperator_observability_workload(
    payload: Mapping[str, Any],
    *,
    release: str,
) -> SoperatorObservabilityWorkload:
    """Select the newest Ready Pod owned by the exact target Deployment."""

    items = payload.get("items")
    if not isinstance(items, list):
        raise SoperatorObservabilityFailure(
            "evidence-missing",
            "Soperator observability workload inventory has no items",
        )

    def metadata(item: Mapping[str, Any]) -> Mapping[str, Any]:
        return _mapping(item.get("metadata"))

    def owner_uid(item: Mapping[str, Any], kind: str) -> str:
        owners = metadata(item).get("ownerReferences")
        matches = [
            str(owner.get("uid") or "")
            for owner in _sequence(owners)
            if isinstance(owner, Mapping)
            and owner.get("kind") == kind
            and owner.get("controller") is True
        ]
        return matches[0] if len(matches) == 1 else ""

    deployments: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping) or item.get("kind") != "Deployment":
            continue
        meta = metadata(item)
        labels = _mapping(meta.get("labels"))
        spec = _mapping(item.get("spec"))
        status = _mapping(item.get("status"))
        name = str(meta.get("name") or "").lower()
        version = str(labels.get("app.kubernetes.io/version") or "").removeprefix("v")
        desired = int(spec.get("replicas") or 0)
        ready = int(status.get("readyReplicas") or 0)
        if (
            str(meta.get("namespace") or "") in {"soperator", "soperator-system"}
            and "soperator" in name
            and version == release
            and desired > 0
            and ready == desired
            and str(meta.get("uid") or "")
        ):
            deployments.append(item)
    if len(deployments) != 1:
        raise SoperatorObservabilityFailure(
            "evidence-missing",
            "Soperator observability requires exactly one Ready target controller Deployment",
        )
    deployment_uid = str(metadata(deployments[0]).get("uid") or "")
    replica_set_uids = {
        str(metadata(item).get("uid") or "")
        for item in items
        if isinstance(item, Mapping)
        and item.get("kind") == "ReplicaSet"
        and owner_uid(item, "Deployment") == deployment_uid
    }
    candidates: list[SoperatorObservabilityWorkload] = []
    for item in items:
        if (
            not isinstance(item, Mapping)
            or item.get("kind") != "Pod"
            or owner_uid(item, "ReplicaSet") not in replica_set_uids
        ):
            continue
        meta = metadata(item)
        if meta.get("deletionTimestamp"):
            continue
        status = _mapping(item.get("status"))
        if not any(
            isinstance(condition, Mapping)
            and condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in _sequence(status.get("conditions"))
        ):
            continue
        ready_containers = {
            str(container.get("name") or "")
            for container in _sequence(status.get("containerStatuses"))
            if isinstance(container, Mapping) and container.get("ready") is True
        }
        containers = _sequence(_mapping(item.get("spec")).get("containers"))
        target_containers = sorted(
            str(container.get("name") or "")
            for container in containers
            if isinstance(container, Mapping)
            and str(container.get("name") or "") in ready_containers
            and "soperator" in str(container.get("image") or "").lower()
        )
        namespace = str(meta.get("namespace") or "")
        pod_name = str(meta.get("name") or "")
        pod_uid = str(meta.get("uid") or "")
        pod_started_at = _timestamp_value(status.get("startTime"))
        if (
            len(target_containers) == 1
            and all((namespace, pod_name, pod_uid))
            and pod_started_at > 0
        ):
            candidates.append(
                SoperatorObservabilityWorkload(
                    namespace=namespace,
                    pod_name=pod_name,
                    pod_uid=pod_uid,
                    container_name=target_containers[0],
                    pod_started_at=pod_started_at,
                )
            )
    if not candidates:
        raise SoperatorObservabilityFailure(
            "evidence-missing",
            "Soperator observability has no exact Ready target-owned Pod/container",
        )
    return sorted(candidates, key=lambda item: (item.pod_started_at, item.pod_name))[-1]


def select_soperator_observability_slurm_cluster(snapshot: Mapping[str, Any]) -> str:
    """Return the unique live SlurmCluster name from the validated snapshot."""

    candidates = []
    for item in _sequence(snapshot.get("soperator_resources")):
        if not isinstance(item, Mapping) or item.get("kind") != "SlurmCluster":
            continue
        name = str(_mapping(item.get("metadata")).get("name") or "").strip()
        if name:
            candidates.append(name)
    if len(candidates) != 1:
        raise SoperatorObservabilityFailure(
            "evidence-missing",
            "Soperator observability requires exactly one live SlurmCluster identity",
        )
    return candidates[0]


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _timestamp(value: float) -> str:
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")


def _required(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"Soperator observability scope requires {label}")
    return normalized


def _promql_value(value: str) -> str:
    normalized = _required(value, "a non-empty selector value")
    if not _SAFE_LABEL_VALUE.fullmatch(normalized):
        raise ValueError("Soperator observability selector contains unsupported characters")
    return normalized.replace("\\", "\\\\").replace('"', '\\"')


def _project_id_value(value: str) -> str:
    normalized = _required(value, "project_id")
    if not _SAFE_PROJECT_ID.fullmatch(normalized):
        raise ValueError("Soperator observability project_id is not a safe URL path segment")
    return normalized


def _http_json(
    url: str,
    *,
    token: str,
    params: Mapping[str, str],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    query = urlencode({key: value for key, value in params.items() if value})
    request = Request(
        f"{url.rstrip('/')}?{query}",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body_bytes = response.read(_MAX_HTTP_RESPONSE_BYTES + 1)
            if len(body_bytes) > _MAX_HTTP_RESPONSE_BYTES:
                raise SoperatorObservabilityFailure(
                    "backend-unavailable",
                    "Nebius Observability query returned an oversized response",
                )
            body = body_bytes.decode("utf-8")
    except HTTPError as exc:
        surface = "Prometheus" if "read.monitoring.api.nebius.cloud" in url else "Loki"
        if exc.code == 401:
            code = "authentication-unavailable"
        elif exc.code == 403:
            code = "authorization-denied"
        else:
            code = "backend-unavailable"
        raise SoperatorObservabilityFailure(
            code,
            f"Nebius Observability {surface} query returned HTTP {exc.code}",
            http_status=exc.code,
        ) from exc
    except (TimeoutError, URLError) as exc:
        raise SoperatorObservabilityFailure(
            "backend-unavailable",
            "Nebius Observability query failed",
        ) from exc
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise SoperatorObservabilityFailure(
            "backend-unavailable",
            "Nebius Observability query returned invalid JSON",
        ) from exc
    if not isinstance(payload, Mapping):
        raise SoperatorObservabilityFailure(
            "backend-unavailable",
            "Nebius Observability query returned a non-object payload",
        )
    if str(payload.get("status") or "success").lower() != "success":
        raise SoperatorObservabilityFailure(
            "backend-unavailable",
            "Nebius Observability query returned a failed status",
        )
    return payload


def _prometheus_evidence(payload: Mapping[str, Any], *, not_before: float) -> tuple[int, float]:
    result = _mapping(payload.get("data")).get("result")
    if not isinstance(result, list) or not result:
        return 0, 0.0
    newest = 0.0
    count = 0
    for item in result:
        sample = item.get("value") if isinstance(item, Mapping) else None
        if not isinstance(sample, list) or len(sample) < 2:
            continue
        try:
            observed = float(sample[0])
        except (TypeError, ValueError):
            continue
        if observed >= not_before:
            count += 1
            newest = max(newest, observed)
    return count, newest


def _loki_evidence(
    payload: Mapping[str, Any],
    *,
    not_before: float,
    expected_stream: Mapping[str, str],
) -> tuple[int, float]:
    result = _mapping(payload.get("data")).get("result")
    if not isinstance(result, list) or not result:
        return 0, 0.0
    newest = 0.0
    streams = 0
    for item in result:
        values = item.get("values") if isinstance(item, Mapping) else None
        stream = item.get("stream") if isinstance(item, Mapping) else None
        if not isinstance(stream, Mapping) or any(
            str(stream.get(key) or "") != value for key, value in expected_stream.items()
        ):
            continue
        if not isinstance(values, list):
            continue
        stream_fresh = False
        for sample in values:
            if not isinstance(sample, list) or not sample:
                continue
            try:
                observed = int(str(sample[0])) / 1_000_000_000
            except (TypeError, ValueError):
                continue
            if observed >= not_before:
                stream_fresh = True
                newest = max(newest, observed)
        if stream_fresh:
            streams += 1
    return streams, newest


def _query_plan(scope: SoperatorObservabilityScope) -> tuple[str, str, str, str]:
    project_id = _project_id_value(scope.project_id)
    namespace = _promql_value(scope.namespace)
    pod_name = _promql_value(scope.pod_name)
    pod_uid = _promql_value(scope.pod_uid)
    container_name = _promql_value(scope.container_name)
    metrics_url = (
        f"https://read.monitoring.api.nebius.cloud/projects/{project_id}/prometheus/api/v1/query"
    )
    logs_url = (
        f"https://read.logging.api.nebius.cloud/projects/{project_id}/loki/api/v1/query_range"
    )
    metric_query = soperator_observability_metric_query(scope)
    product_log_query = (
        '{__bucket__="default",k8s_namespace_name="'
        + namespace
        + '",k8s_pod_name="'
        + pod_name
        + '",k8s_pod_uid="'
        + pod_uid
        + '",k8s_container_name="'
        + container_name
        + '"}'
    )
    return metrics_url, logs_url, metric_query, product_log_query


def _query_once(
    scope: SoperatorObservabilityScope,
    *,
    token: str,
    query: Callable[..., Mapping[str, Any]],
    deadline: float,
) -> tuple[tuple[int, float], tuple[int, float]]:
    def remaining_timeout() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _ObservabilityDeadlineExceeded
        return min(20.0, remaining)

    def require_remaining_time() -> None:
        if time.monotonic() >= deadline:
            raise _ObservabilityDeadlineExceeded

    def bounded_query(
        url: str,
        *,
        params: Mapping[str, str],
    ) -> Mapping[str, Any]:
        timeout_seconds = remaining_timeout()
        completed = threading.Event()
        results: list[Mapping[str, Any]] = []
        errors: list[BaseException] = []

        def invoke() -> None:
            try:
                results.append(
                    query(
                        url,
                        token=token,
                        params=params,
                        timeout_seconds=timeout_seconds,
                    )
                )
            except BaseException as exc:
                errors.append(exc)
            finally:
                completed.set()

        threading.Thread(
            target=invoke,
            name="soperator-observability-query",
            daemon=True,
        ).start()
        if not completed.wait(timeout_seconds):
            raise _ObservabilityDeadlineExceeded
        require_remaining_time()
        if errors:
            raise errors[0]
        if not results:
            raise SoperatorObservabilityFailure(
                "backend-unavailable",
                "Nebius Observability query failed",
            )
        return results[0]

    metrics_url, logs_url, metric_query, product_log_query = _query_plan(scope)
    metric_query_at = time.time()
    metric_payload = bounded_query(
        metrics_url,
        params={"query": metric_query, "time": str(int(metric_query_at))},
    )
    log_query_at = time.time()
    product_payload = bounded_query(
        logs_url,
        params={
            "start": str(int(scope.log_not_before * 1_000_000_000)),
            "end": str(int(log_query_at * 1_000_000_000)),
            "limit": "20",
            "direction": "backward",
            "query": product_log_query,
        },
    )
    evidence_checked_at = time.time()
    expected_stream = {
        "k8s_namespace_name": scope.namespace,
        "k8s_pod_name": scope.pod_name,
        "k8s_pod_uid": scope.pod_uid,
        "k8s_container_name": scope.container_name,
    }
    return (
        _prometheus_evidence(
            metric_payload,
            not_before=max(scope.metric_not_before, evidence_checked_at - 300.0),
        ),
        _loki_evidence(
            product_payload,
            not_before=scope.log_not_before,
            expected_stream=expected_stream,
        ),
    )


def soperator_observability_metric_query(scope: SoperatorObservabilityScope) -> str:
    """Return the exact release- and cluster-scoped Prometheus query."""

    return (
        'kube_customresource_slurmcluster_info{iam_project_id="'
        + _promql_value(scope.project_id)
        + '",mk8s_cluster_id="'
        + _promql_value(scope.cluster_id)
        + '",soperator_release="'
        + _promql_value(scope.release)
        + '",name="'
        + _promql_value(scope.slurm_cluster)
        + '"}'
    )


def _receipt_identity(scope: SoperatorObservabilityScope) -> dict[str, str]:
    metrics_url, logs_url, metric_query, product_log_query = _query_plan(scope)
    workload_identity = {
        "clusterId": scope.cluster_id,
        "namespace": scope.namespace,
        "podName": scope.pod_name,
        "podUid": scope.pod_uid,
        "containerName": scope.container_name,
    }
    return {
        "target_ref_sha256": _sha256_text(scope.target_ref),
        "metrics_endpoint_sha256": _sha256_text(metrics_url),
        "logs_endpoint_sha256": _sha256_text(logs_url),
        "metric_query_sha256": _sha256_text(metric_query),
        "product_log_query_sha256": _sha256_text(product_log_query),
        "workload_identity_sha256": _sha256_text(
            json.dumps(workload_identity, sort_keys=True, separators=(",", ":"))
        ),
    }


def failed_soperator_observability_receipt(
    *,
    verification_id: str,
    target_ref: str,
    release: str,
    verification_started_at: float,
    failure_code: str,
    http_status: int | None = None,
    attempts: int = 0,
    scope: SoperatorObservabilityScope | None = None,
) -> SoperatorObservabilityReceipt:
    """Build a sanitized terminal failure receipt without raw provider detail."""

    try:
        identity = _receipt_identity(scope) if scope is not None else {}
    except ValueError:
        identity = {}
    return SoperatorObservabilityReceipt(
        schema=SOPERATOR_OBSERVABILITY_RECEIPT_SCHEMA,
        verification_id=verification_id,
        status="failed" if failure_code == "evidence-missing" else "unavailable",
        failure_code=failure_code,
        http_status=http_status,
        target_ref_sha256=identity.get("target_ref_sha256", _sha256_text(target_ref)),
        release=release,
        credential_source=SOPERATOR_OBSERVABILITY_CREDENTIAL_SOURCE,
        metrics_endpoint_sha256=identity.get("metrics_endpoint_sha256", ""),
        logs_endpoint_sha256=identity.get("logs_endpoint_sha256", ""),
        metric_query_sha256=identity.get("metric_query_sha256", ""),
        product_log_query_sha256=identity.get("product_log_query_sha256", ""),
        workload_identity_sha256=identity.get("workload_identity_sha256", ""),
        verification_started_at=_timestamp(verification_started_at),
        metric_not_before=_timestamp(scope.metric_not_before) if scope is not None else "",
        log_not_before=_timestamp(scope.log_not_before) if scope is not None else "",
        observed_at=_timestamp(time.time()),
        attempts=attempts,
        metric_series_count=0,
        metric_newest_sample_at="",
        product_log_stream_count=0,
        product_log_newest_sample_at="",
    )


def verify_soperator_observability(
    scope: SoperatorObservabilityScope,
    *,
    verification_id: str,
    token: str,
    timeout_seconds: int = 60,
    poll_interval_seconds: float = 10.0,
    query: Callable[..., Mapping[str, Any]] = _http_json,
    emit: Callable[[str], None] | None = None,
) -> SoperatorObservabilityReceipt:
    """Require a current metric and a log from the exact current Pod UID."""

    _required(scope.project_id, "project_id")
    _required(scope.cluster_id, "cluster_id")
    _required(scope.namespace, "namespace")
    _required(scope.slurm_cluster, "slurm_cluster")
    _required(scope.release, "release")
    _required(scope.target_ref, "target_ref")
    _required(scope.pod_name, "pod_name")
    _required(scope.pod_uid, "pod_uid")
    _required(scope.container_name, "container_name")
    try:
        _query_plan(scope)
    except ValueError as exc:
        raise SoperatorObservabilityFailure(
            "evidence-missing",
            "Soperator observability scope contains an invalid identity",
        ) from exc
    if (
        min(
            scope.verification_started_at,
            scope.metric_not_before,
            scope.log_not_before,
        )
        <= 0
    ):
        raise ValueError("Soperator observability scope requires positive evidence timestamps")
    if not str(token or "").strip():
        raise SoperatorObservabilityFailure(
            "authentication-unavailable",
            "Soperator observability requires an operator IAM access token",
        )
    attempts = 0
    deadline = time.monotonic() + max(timeout_seconds, 1)
    latest = ((0, 0.0), (0, 0.0))
    while True:
        if time.monotonic() >= deadline:
            raise SoperatorObservabilityFailure(
                "evidence-missing",
                "Soperator observability evidence did not become current within "
                f"{timeout_seconds} seconds",
                attempts=attempts,
            )
        attempts += 1
        try:
            latest = _query_once(scope, token=token, query=query, deadline=deadline)
        except _ObservabilityDeadlineExceeded as exc:
            raise SoperatorObservabilityFailure(
                "evidence-missing",
                "Soperator observability evidence did not become current within "
                f"{timeout_seconds} seconds",
                attempts=attempts,
            ) from exc
        except SoperatorObservabilityFailure as exc:
            raise SoperatorObservabilityFailure(
                exc.code,
                str(exc),
                http_status=exc.http_status,
                attempts=attempts,
            ) from exc
        except Exception as exc:
            raise SoperatorObservabilityFailure(
                "backend-unavailable",
                "Nebius Observability query failed",
                attempts=attempts,
            ) from exc
        if latest[0][0] > 0 and latest[1][0] > 0:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SoperatorObservabilityFailure(
                "evidence-missing",
                "Soperator observability evidence did not become current within "
                f"{timeout_seconds} seconds",
                attempts=attempts,
            )
        if emit:
            emit(
                "Soperator observability pending in Nebius backends: "
                f"metrics={latest[0][0]}, productLogs={latest[1][0]}"
            )
        time.sleep(min(max(0.1, poll_interval_seconds), remaining))
    observed_at = time.time()
    identity = _receipt_identity(scope)
    return SoperatorObservabilityReceipt(
        schema=SOPERATOR_OBSERVABILITY_RECEIPT_SCHEMA,
        verification_id=verification_id,
        status="passed",
        failure_code="",
        http_status=None,
        target_ref_sha256=identity["target_ref_sha256"],
        release=scope.release,
        credential_source=SOPERATOR_OBSERVABILITY_CREDENTIAL_SOURCE,
        metrics_endpoint_sha256=identity["metrics_endpoint_sha256"],
        logs_endpoint_sha256=identity["logs_endpoint_sha256"],
        metric_query_sha256=identity["metric_query_sha256"],
        product_log_query_sha256=identity["product_log_query_sha256"],
        workload_identity_sha256=identity["workload_identity_sha256"],
        verification_started_at=_timestamp(scope.verification_started_at),
        metric_not_before=_timestamp(scope.metric_not_before),
        log_not_before=_timestamp(scope.log_not_before),
        observed_at=_timestamp(observed_at),
        attempts=attempts,
        metric_series_count=latest[0][0],
        metric_newest_sample_at=_timestamp(latest[0][1]),
        product_log_stream_count=latest[1][0],
        product_log_newest_sample_at=_timestamp(latest[1][1]),
    )


__all__ = [
    "SOPERATOR_OBSERVABILITY_RECEIPT_SCHEMA",
    "SoperatorObservabilityFailure",
    "SoperatorObservabilityReceipt",
    "SoperatorObservabilityScope",
    "SoperatorObservabilityWorkload",
    "failed_soperator_observability_receipt",
    "select_soperator_observability_slurm_cluster",
    "select_soperator_observability_workload",
    "soperator_observability_metric_query",
    "verify_soperator_observability",
]
