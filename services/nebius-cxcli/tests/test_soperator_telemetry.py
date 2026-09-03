from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from threading import Event
from time import monotonic

import pytest

import nebius_cxcli.soperator_telemetry as telemetry
from nebius_cxcli.soperator_telemetry import (
    SOPERATOR_OBSERVABILITY_RECEIPT_SCHEMA,
    SoperatorObservabilityFailure,
    SoperatorObservabilityScope,
    failed_soperator_observability_receipt,
    select_soperator_observability_slurm_cluster,
    select_soperator_observability_workload,
    soperator_observability_metric_query,
    verify_soperator_observability,
)


def _scope() -> SoperatorObservabilityScope:
    return SoperatorObservabilityScope(
        project_id="project-123",
        cluster_id="mk8scluster-123",
        namespace="soperator-system",
        slurm_cluster="soperator",
        release="4.1.7",
        target_ref="cluster-a",
        verification_started_at=1_300.0,
        metric_not_before=1_000.0,
        log_not_before=900.0,
        pod_name="soperator-controller-abc",
        pod_uid="pod-uid-abc",
        container_name="manager",
    )


def _query_success(
    url: str,
    *,
    token: str,
    params: Mapping[str, str],
    timeout_seconds: float,
) -> Mapping[str, object]:
    del timeout_seconds
    assert token == "operator-token"
    if "prometheus" in url:
        return {"status": "success", "data": {"result": [{"value": [1_250.0, "1"]}]}}
    assert params["start"] == "900000000000"
    return {
        "status": "success",
        "data": {
            "result": [
                {
                    "stream": {
                        "k8s_namespace_name": "soperator-system",
                        "k8s_pod_name": "soperator-controller-abc",
                        "k8s_pod_uid": "pod-uid-abc",
                        "k8s_container_name": "manager",
                    },
                    "values": [["1200000000000", "line"]],
                }
            ]
        },
    }


def test_explicit_observability_verifier_uses_current_metric_and_exact_pod_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[str, Mapping[str, str]]] = []
    monkeypatch.setattr(telemetry.time, "time", lambda: 1_300.0)

    def query(url: str, **kwargs: object) -> Mapping[str, object]:
        params = kwargs["params"]
        assert isinstance(params, Mapping)
        queries.append((url, params))
        return _query_success(url, **kwargs)  # type: ignore[arg-type]

    receipt = verify_soperator_observability(
        _scope(),
        verification_id="verification-1",
        token="operator-token",
        query=query,
    )

    assert receipt.schema == SOPERATOR_OBSERVABILITY_RECEIPT_SCHEMA
    assert receipt.status == "passed"
    assert receipt.verification_id == "verification-1"
    assert receipt.credential_source == "operator-nebius-cli"
    assert receipt.metric_series_count == 1
    assert receipt.product_log_stream_count == 1
    assert len(queries) == 2
    assert 'iam_project_id="project-123"' in queries[0][1]["query"]
    assert 'mk8s_cluster_id="mk8scluster-123"' in queries[0][1]["query"]
    assert 'k8s_pod_uid="pod-uid-abc"' in queries[1][1]["query"]
    persisted = str(asdict(receipt))
    assert "operator-token" not in persisted
    assert "project-123" not in persisted
    assert "mk8scluster-123" not in persisted


def test_observability_missing_token_runs_no_query() -> None:
    queries: list[object] = []

    with pytest.raises(SoperatorObservabilityFailure) as caught:
        verify_soperator_observability(
            _scope(),
            verification_id="verification-1",
            token="",
            query=lambda *_args, **_kwargs: queries.append(object()) or {},
        )

    assert caught.value.code == "authentication-unavailable"
    assert queries == []


@pytest.mark.parametrize(
    ("http_status", "failure_code"),
    [
        (401, "authentication-unavailable"),
        (403, "authorization-denied"),
        (503, "backend-unavailable"),
    ],
)
def test_http_query_failure_is_typed_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    http_status: int,
    failure_code: str,
) -> None:
    def reject(*_args: object, **_kwargs: object) -> None:
        raise telemetry.HTTPError(
            "https://private.example.invalid/query",
            http_status,
            "secret response detail",
            None,
            None,
        )

    monkeypatch.setattr(telemetry, "urlopen", reject)

    with pytest.raises(SoperatorObservabilityFailure) as caught:
        telemetry._http_json(
            "https://read.monitoring.api.nebius.cloud/projects/redacted/prometheus",
            token="secret-token",
            params={"query": "1"},
            timeout_seconds=1,
        )

    assert caught.value.code == failure_code
    assert caught.value.http_status == http_status
    assert "secret response detail" not in str(caught.value)
    assert "secret-token" not in str(caught.value)


def test_http_query_rejects_oversized_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            assert size == 5
            return b"12345"

    monkeypatch.setattr(telemetry, "_MAX_HTTP_RESPONSE_BYTES", 4)
    monkeypatch.setattr(telemetry, "urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(SoperatorObservabilityFailure) as caught:
        telemetry._http_json(
            "https://read.monitoring.api.nebius.cloud/projects/redacted/prometheus",
            token="secret-token",
            params={"query": "1"},
            timeout_seconds=1,
        )

    assert caught.value.code == "backend-unavailable"
    assert "secret-token" not in str(caught.value)


@pytest.mark.parametrize("missing_surface", ["metrics", "logs"])
def test_observability_rejects_missing_or_stale_evidence(
    monkeypatch: pytest.MonkeyPatch,
    missing_surface: str,
) -> None:
    monkeypatch.setattr(telemetry.time, "time", lambda: 1_300.0)
    clock = {"now": 0.0}
    monkeypatch.setattr(telemetry.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        telemetry.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )

    def query(url: str, **kwargs: object) -> Mapping[str, object]:
        result = _query_success(url, **kwargs)  # type: ignore[arg-type]
        if (missing_surface == "metrics" and "prometheus" in url) or (
            missing_surface == "logs" and "loki" in url
        ):
            return {"status": "success", "data": {"result": []}}
        return result

    with pytest.raises(SoperatorObservabilityFailure) as caught:
        verify_soperator_observability(
            _scope(),
            verification_id="verification-1",
            token="operator-token",
            timeout_seconds=1,
            query=query,
        )

    assert caught.value.code == "evidence-missing"
    assert caught.value.attempts == 1


def test_observability_caps_each_backend_call_to_the_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}
    timeouts: list[float] = []
    monkeypatch.setattr(telemetry.time, "time", lambda: 1_300.0)
    monkeypatch.setattr(telemetry.time, "monotonic", lambda: clock["now"])

    def query(
        url: str,
        *,
        token: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        del token, params
        timeouts.append(timeout_seconds)
        if "prometheus" in url:
            clock["now"] += 18.0
        else:
            clock["now"] += timeout_seconds
        return {"status": "success", "data": {"result": []}}

    with pytest.raises(SoperatorObservabilityFailure) as caught:
        verify_soperator_observability(
            _scope(),
            verification_id="verification-1",
            token="operator-token",
            timeout_seconds=25,
            query=query,
        )

    assert caught.value.code == "evidence-missing"
    assert caught.value.attempts == 1
    assert timeouts == [20.0, pytest.approx(7.0)]
    assert clock["now"] == pytest.approx(25.0)


def test_observability_deadline_does_not_wait_for_a_stuck_query() -> None:
    release = Event()

    def query(*_args: object, **_kwargs: object) -> Mapping[str, object]:
        release.wait()
        return {"status": "success", "data": {"result": []}}

    started_at = monotonic()
    try:
        with pytest.raises(telemetry._ObservabilityDeadlineExceeded):
            telemetry._query_once(
                _scope(),
                token="operator-token",
                query=query,
                deadline=started_at + 0.05,
            )
    finally:
        release.set()

    assert monotonic() - started_at < 0.5


def test_metric_freshness_window_advances_during_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr(telemetry.time, "time", lambda: 1_360.0)
    monkeypatch.setattr(telemetry.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        telemetry.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )

    def query(url: str, **_kwargs: object) -> Mapping[str, object]:
        if "prometheus" in url:
            return {
                "status": "success",
                "data": {"result": [{"value": [1_001.0, "1"]}]},
            }
        return {
            "status": "success",
            "data": {
                "result": [
                    {
                        "stream": {
                            "k8s_namespace_name": "soperator-system",
                            "k8s_pod_name": "soperator-controller-abc",
                            "k8s_pod_uid": "pod-uid-abc",
                            "k8s_container_name": "manager",
                        },
                        "values": [["1350000000000", "line"]],
                    }
                ]
            },
        }

    with pytest.raises(SoperatorObservabilityFailure) as caught:
        verify_soperator_observability(
            _scope(),
            verification_id="verification-1",
            token="operator-token",
            timeout_seconds=1,
            poll_interval_seconds=1,
            query=query,
        )

    assert caught.value.code == "evidence-missing"
    assert caught.value.attempts == 1


def test_failure_receipt_is_separate_redacted_and_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(telemetry.time, "time", lambda: 1_301.0)

    receipt = failed_soperator_observability_receipt(
        verification_id="verification-1",
        target_ref="cluster-a",
        release="4.1.7",
        verification_started_at=1_300.0,
        failure_code="authorization-denied",
        http_status=403,
        attempts=1,
        scope=_scope(),
    )

    assert receipt.status == "unavailable"
    assert receipt.failure_code == "authorization-denied"
    assert receipt.http_status == 403
    assert receipt.attempts == 1
    persisted = str(asdict(receipt))
    assert "project-123" not in persisted
    assert "mk8scluster-123" not in persisted
    assert "cluster-a" not in persisted


def test_metric_query_rejects_unsafe_label_values() -> None:
    scope = SoperatorObservabilityScope(**{**_scope().__dict__, "cluster_id": 'bad"selector'})

    with pytest.raises(ValueError, match="unsupported characters"):
        soperator_observability_metric_query(scope)


def test_query_plan_rejects_project_id_path_traversal() -> None:
    scope = SoperatorObservabilityScope(
        **{**_scope().__dict__, "project_id": "../project-other"}
    )

    with pytest.raises(ValueError, match="project_id"):
        telemetry._query_plan(scope)


def test_observability_rejects_invalid_project_id_before_query() -> None:
    scope = SoperatorObservabilityScope(
        **{**_scope().__dict__, "project_id": "../project-other"}
    )
    queries: list[object] = []

    with pytest.raises(SoperatorObservabilityFailure) as caught:
        verify_soperator_observability(
            scope,
            verification_id="verification-1",
            token="operator-token",
            timeout_seconds=1,
            query=lambda *_args, **_kwargs: queries.append(object()) or {},
        )

    assert caught.value.code == "evidence-missing"
    assert queries == []


def test_failure_receipt_survives_invalid_scope_identifiers() -> None:
    scope = SoperatorObservabilityScope(
        **{**_scope().__dict__, "project_id": "../project-other"}
    )

    receipt = failed_soperator_observability_receipt(
        verification_id="verification-1",
        target_ref="cluster-a",
        release="4.1.7",
        verification_started_at=1_300.0,
        failure_code="evidence-missing",
        scope=scope,
    )

    assert receipt.status == "failed"
    assert receipt.metrics_endpoint_sha256 == ""
    assert receipt.metric_query_sha256 == ""


def test_workload_selection_follows_exact_ownership_and_records_pod_start() -> None:
    payload = {
        "items": [
            {
                "kind": "Deployment",
                "metadata": {
                    "namespace": "soperator-system",
                    "name": "soperator-controller-manager",
                    "uid": "deployment-uid",
                    "labels": {"app.kubernetes.io/version": "4.1.7"},
                },
                "spec": {"replicas": 1},
                "status": {"readyReplicas": 1},
            },
            {
                "kind": "ReplicaSet",
                "metadata": {
                    "namespace": "soperator-system",
                    "name": "soperator-controller-manager-abc",
                    "uid": "replicaset-uid",
                    "ownerReferences": [
                        {"kind": "Deployment", "uid": "deployment-uid", "controller": True}
                    ],
                },
            },
            {
                "kind": "Pod",
                "metadata": {
                    "namespace": "soperator-system",
                    "name": "soperator-controller-manager-abc-123",
                    "uid": "pod-uid",
                    "ownerReferences": [
                        {"kind": "ReplicaSet", "uid": "replicaset-uid", "controller": True}
                    ],
                },
                "spec": {
                    "containers": [{"name": "manager", "image": "ghcr.io/nebius/soperator:v4.1.7"}]
                },
                "status": {
                    "startTime": "2026-08-30T12:00:00Z",
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "containerStatuses": [{"name": "manager", "ready": True}],
                },
            },
        ]
    }

    selected = select_soperator_observability_workload(payload, release="4.1.7")

    assert selected.pod_uid == "pod-uid"
    assert selected.container_name == "manager"
    assert selected.pod_started_at > 0


def test_slurm_cluster_selection_requires_one_exact_live_identity() -> None:
    snapshot = {
        "soperator_resources": [{"kind": "SlurmCluster", "metadata": {"name": "soperator"}}]
    }

    assert select_soperator_observability_slurm_cluster(snapshot) == "soperator"
    with pytest.raises(SoperatorObservabilityFailure, match="exactly one"):
        select_soperator_observability_slurm_cluster({"soperator_resources": []})


@pytest.mark.parametrize(
    "failure",
    [KeyboardInterrupt(), SystemExit(7)],
)
def test_observability_preserves_process_interrupts(failure: BaseException) -> None:
    def fail(*_args: object, **_kwargs: object) -> Mapping[str, object]:
        raise failure

    with pytest.raises(type(failure)):
        verify_soperator_observability(
            _scope(),
            verification_id="verification-1",
            token="operator-token",
            query=fail,
        )
