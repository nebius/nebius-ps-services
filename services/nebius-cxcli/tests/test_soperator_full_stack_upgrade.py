from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_cxcli.mk8s_upgrade import CompatibilityChoice
from nebius_cxcli.soperator_failures import SoperatorSafetyPauseError
from nebius_cxcli.soperator_full_stack_upgrade import (
    CampaignControllerSpoolMigrationStore,
    CampaignSegmentResult,
    FrozenCompatibilityRow,
    FrozenNodeGroupTarget,
    _new_receipt,
    _receipt_from_payload,
    apply_frozen_node_group_rows,
    assert_campaign_node_group_inventory,
    assert_final_capacity_snapshot_unchanged,
    build_campaign_intent,
    campaign_intent_from_payload,
    campaign_receipt_path,
    final_node_group_capacity_snapshot,
    load_campaign_receipt,
    reachable_kubernetes_versions,
    resolve_kubernetes_upgrade_path,
    run_campaign,
    run_final_runtime_validation_boundary,
    validate_campaign_intent,
)


def _intent():
    return build_campaign_intent(
        target_ref="cluster-a",
        ownership="managed",
        backend="terraform",
        backend_authority_sha256="sha256:" + "b" * 64,
        provider_api_authorized=False,
        source_config_sha256="sha256:" + "a" * 64,
        source_project_snapshot_sha256="sha256:" + "c" * 64,
        cluster_id="mk8scluster-a",
        kubernetes_uid="uid-a",
        requested_release_selector="latest",
        source_release="4.1.7",
        target_release="4.2.0",
        target_jail_cuda_version="13.0",
        requested_kubernetes_selector="latest",
        source_kubernetes_version="1.33",
        supported_kubernetes_versions=("1.33.9", "1.34.5", "1.35.1"),
        target_os="ubuntu24.04",
        target_gpu_stack_preset="cuda13.0",
        node_group_strategy="zero-surge",
        strategy_max_surge_count=None,
        drain_timeout="30m",
        zero_size_gpu_validation="require-capacity",
        job_policy="wait-to-finish",
        cancel_job_ids=(),
        requeue_job_ids=(),
        job_wait_timeout="0s",
        job_refresh_interval="30s",
        node_groups=(
            FrozenNodeGroupTarget(
                key="worker",
                provider_name="worker",
                provider_id="mk8snodegroup-a",
                platform="gpu-l40s",
                source_version="1.33",
                source_os="ubuntu22.04",
                source_drivers_preset="cuda12.8",
                target_version="1.35",
                target_os="ubuntu24.04",
                target_drivers_preset="cuda13.0",
                gpu=True,
            ),
        ),
        compatibility_rows=(
            FrozenCompatibilityRow(
                group_key="worker",
                kubernetes_version="1.34",
                platform="gpu-l40s",
                os="ubuntu24.04",
                drivers_preset="cuda13.0",
            ),
            FrozenCompatibilityRow(
                group_key="worker",
                kubernetes_version="1.35",
                platform="gpu-l40s",
                os="ubuntu24.04",
                drivers_preset="cuda13.0",
            ),
        ),
    )


def _live_capacity_group(*, target: int, resource_version: int) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(
            id="mk8snodegroup-a",
            name="worker",
            resource_version=resource_version,
        ),
        spec=SimpleNamespace(
            version="1.35",
            template=SimpleNamespace(
                os="ubuntu24.04",
                gpu_settings=SimpleNamespace(drivers_preset="cuda13.0"),
            ),
        ),
        status=SimpleNamespace(
            version="1.35",
            target_node_count=target,
            ready_node_count=target,
            node_count=target,
            outdated_node_count=0,
            reconciling=False,
        ),
    )


@pytest.mark.parametrize(("before_target", "after_target"), ((0, 1), (1, 0)))
def test_final_capacity_transition_requires_fresh_runtime_validation(
    before_target: int,
    after_target: int,
) -> None:
    intent = _intent()
    before = final_node_group_capacity_snapshot(
        intent=intent,
        live_node_groups=(_live_capacity_group(target=before_target, resource_version=10),),
    )
    after = final_node_group_capacity_snapshot(
        intent=intent,
        live_node_groups=(_live_capacity_group(target=after_target, resource_version=11),),
    )

    with pytest.raises(RuntimeError, match="changed during Soperator final GPU validation"):
        assert_final_capacity_snapshot_unchanged(before, after)


def test_group_mutation_revalidates_each_tuple_against_changing_provider_api() -> None:
    first = _intent().node_groups[0]
    second = replace(first, key="worker-b", provider_name="worker-b", provider_id="group-b")
    rows = {
        group.key: FrozenCompatibilityRow(
            group_key=group.key,
            kubernetes_version="1.35",
            platform=group.platform,
            os="ubuntu24.04",
            drivers_preset="cuda13.0",
        )
        for group in (first, second)
    }
    supported = CompatibilityChoice(
        platform="gpu-l40s",
        os="ubuntu24.04",
        drivers_preset="cuda13.0",
    )
    applied: list[str] = []

    def _choices(_group, _row):  # type: ignore[no-untyped-def]
        return (supported,) if not applied else ()

    with pytest.raises(SoperatorSafetyPauseError, match="no longer contains"):
        apply_frozen_node_group_rows(
            node_groups=(first, second),
            rows=rows,
            compatibility_lookup=_choices,
            apply_group=lambda group, _row: applied.append(group.key),
        )

    assert applied == ["worker"]


def test_final_runtime_boundary_refreshes_before_validation_and_reproves_after() -> None:
    events: list[str] = []
    snapshot = {"worker": {"resourceVersion": "10", "targetNodeCount": 1}}

    source, runtime, final_snapshot = run_final_runtime_validation_boundary(
        refresh_sources=lambda: events.append("refresh-sources") or ("source",),
        prove_release_graph=lambda: events.append("prove-graph") or object(),
        freeze_capacity=lambda: events.append("freeze-capacity") or snapshot,
        validate_runtime=lambda observed: (
            events.append("validate-runtime") or {"snapshot": observed}
        ),
    )

    assert events == [
        "refresh-sources",
        "prove-graph",
        "freeze-capacity",
        "validate-runtime",
        "prove-graph",
        "freeze-capacity",
    ]
    assert source == ("source",)
    assert runtime == {"snapshot": snapshot}
    assert final_snapshot == snapshot


def test_final_runtime_boundary_rejects_post_validation_graph_failure() -> None:
    events: list[str] = []
    graph_results = iter((object(), None))

    with pytest.raises(RuntimeError, match="after runtime validation"):
        run_final_runtime_validation_boundary(
            refresh_sources=lambda: events.append("refresh-sources"),
            prove_release_graph=lambda: events.append("prove-graph") or next(graph_results),
            freeze_capacity=lambda: (
                events.append("freeze-capacity")
                or {"worker": {"resourceVersion": "10", "targetNodeCount": 1}}
            ),
            validate_runtime=lambda _snapshot: events.append("validate-runtime"),
        )

    assert events == [
        "refresh-sources",
        "prove-graph",
        "freeze-capacity",
        "validate-runtime",
        "prove-graph",
    ]


def test_latest_kubernetes_target_is_highest_contiguous_provider_endpoint() -> None:
    assert reachable_kubernetes_versions(
        current_version="1.33.4",
        supported_versions=("1.34.8", "1.35.2", "1.37.0"),
    ) == ("1.34", "1.35")
    assert resolve_kubernetes_upgrade_path(
        selector="latest",
        current_version="1.33",
        supported_versions=("1.33", "1.34", "1.35", "1.37"),
    ) == ("1.35", ("1.34", "1.35"))


def test_exact_kubernetes_target_rejects_provider_gap() -> None:
    with pytest.raises(ValueError, match="not a reachable provider-supported endpoint"):
        resolve_kubernetes_upgrade_path(
            selector="1.35",
            current_version="1.33",
            supported_versions=("1.33", "1.35"),
        )


@pytest.mark.parametrize(
    "observed",
    (
        (),
        ("mk8snodegroup-a", "mk8snodegroup-new"),
        ("mk8snodegroup-a", "mk8snodegroup-a"),
    ),
)
def test_campaign_rejects_live_node_group_inventory_drift(
    observed: tuple[str, ...],
) -> None:
    with pytest.raises(SoperatorSafetyPauseError, match="inventory differs"):
        assert_campaign_node_group_inventory(_intent(), observed)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"schema": "wrong"}, "unsupported schema"),
        ({"ownership": "foreign"}, "ownership is invalid"),
        ({"backend": "foreign"}, "backend is invalid"),
        ({"backend": "provider-api"}, "Terraform backend"),
        (
            {"ownership": "onboarded", "backend": "terraform"},
            "provider API backend",
        ),
        ({"provider_api_authorized": True}, "cannot carry provider API"),
        ({"backend_authority_sha256": "bad"}, "backend authority digest"),
        ({"source_config_sha256": "bad"}, "source config digest"),
        ({"source_project_snapshot_sha256": "bad"}, "project snapshot digest"),
        ({"target_ref": ""}, "requires target"),
        ({"node_group_strategy": "foreign"}, "strategy is invalid"),
        (
            {"node_group_strategy": "safe-surge", "strategy_max_surge_count": 0},
            "positive max-surge",
        ),
        ({"strategy_max_surge_count": 1}, "only safe-surge"),
        ({"job_policy": "foreign"}, "job policy is invalid"),
        ({"target_kubernetes_version": "1.34"}, "path is inconsistent"),
        ({"node_groups": ()}, "at least one node group"),
        ({"zero_size_gpu_validation": "foreign"}, "zero-size GPU policy"),
    ),
)
def test_campaign_intent_rejects_invalid_frozen_contracts(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_campaign_intent(replace(_intent(), **changes))


def test_campaign_intent_rejects_node_identity_version_and_compatibility_drift() -> None:
    intent = _intent()
    group = intent.node_groups[0]

    with pytest.raises(ValueError, match="repeats a node-group identity"):
        validate_campaign_intent(
            replace(
                intent,
                node_groups=(group, replace(group, key="second")),
                compatibility_rows=(*intent.compatibility_rows,),
            )
        )
    with pytest.raises(ValueError, match="endpoint is inconsistent"):
        validate_campaign_intent(
            replace(intent, node_groups=(replace(group, target_version="1.34"),))
        )
    with pytest.raises(ValueError, match="requires provider name"):
        validate_campaign_intent(replace(intent, node_groups=(replace(group, provider_name=""),)))
    with pytest.raises(ValueError, match="exactly one minor behind"):
        validate_campaign_intent(
            replace(intent, node_groups=(replace(group, source_version="1.31"),))
        )
    with pytest.raises(ValueError, match="compatibility rows are incomplete"):
        validate_campaign_intent(replace(intent, compatibility_rows=()))
    bad_final = replace(intent.compatibility_rows[-1], os="ubuntu22.04")
    with pytest.raises(ValueError, match="final compatibility row is inconsistent"):
        validate_campaign_intent(
            replace(intent, compatibility_rows=(*intent.compatibility_rows[:-1], bad_final))
        )


def _campaign_receipt_payload() -> dict[str, object]:
    return json.loads(json.dumps(asdict(_new_receipt(_intent()))))


def test_campaign_receipt_parser_rejects_non_object_and_missing_ledger() -> None:
    with pytest.raises(RuntimeError, match="must be an object"):
        _receipt_from_payload([])
    payload = _campaign_receipt_payload()
    payload["segments"] = "invalid"
    with pytest.raises(RuntimeError, match="has no segment ledger"):
        _receipt_from_payload(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda payload: payload.update(schema="wrong"), "unsupported schema"),
        (lambda payload: payload.update(status="wrong"), "invalid state"),
        (lambda payload: payload.update(intent_sha256="bad"), "invalid intent digest"),
        (
            lambda payload: payload["intent"].update(target_ref="foreign"),
            "intent digest does not match",
        ),
        (lambda payload: payload["segments"].append("invalid"), "segment ledger is invalid"),
        (
            lambda payload: payload["segments"][0].update(status="wrong"),
            "segment ledger is invalid",
        ),
        (
            lambda payload: payload["segments"][0].update(
                status="complete",
                evidence_sha256="",
            ),
            "has no evidence digest",
        ),
        (lambda payload: payload.update(supervisor={}), "supervisor state is invalid"),
    ),
)
def test_campaign_receipt_parser_rejects_corrupt_durable_evidence(
    mutate,
    message: str,
) -> None:
    payload = _campaign_receipt_payload()
    mutate(payload)

    with pytest.raises(RuntimeError, match=message):
        _receipt_from_payload(payload)


def test_campaign_receipt_parser_rejects_evidence_digest_mismatch() -> None:
    payload = _campaign_receipt_payload()
    payload["segments"][0].update(
        status="complete",
        evidence={"ready": True},
        evidence_sha256="sha256:" + "f" * 64,
    )

    with pytest.raises(RuntimeError, match="evidence digest does not match"):
        _receipt_from_payload(payload)


def _campaign_executors(intent=None):
    resolved = intent or _intent()
    return {
        name: (lambda name=name: CampaignSegmentResult(evidence={"segment": name}))
        for name in resolved.segments
    }


def test_campaign_runner_rejects_missing_executor_and_invalid_maintenance(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = campaign_receipt_path(tmp_path, target_ref=intent.target_ref)
    with pytest.raises(RuntimeError, match="has no executor"):
        run_campaign(
            path=path,
            intent=intent,
            segment_executors={},
            enter_maintenance=lambda _record, _existing: {},
            restore_maintenance=lambda _record, _evidence: {},
            assert_fence=lambda: None,
        )
    with pytest.raises(RuntimeError, match="returned invalid evidence"):
        run_campaign(
            path=path,
            intent=intent,
            segment_executors=_campaign_executors(intent),
            enter_maintenance=lambda _record, _existing: "invalid",
            restore_maintenance=lambda _record, _evidence: {},
            assert_fence=lambda: None,
        )


def test_campaign_runner_rejects_invalid_segment_and_restoration_evidence(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = campaign_receipt_path(tmp_path, target_ref=intent.target_ref)
    executors = _campaign_executors(intent)
    executors[intent.segments[0]] = lambda: "invalid"
    with pytest.raises(RuntimeError, match="returned invalid evidence"):
        run_campaign(
            path=path,
            intent=intent,
            segment_executors=executors,
            enter_maintenance=lambda _record, _existing: {},
            restore_maintenance=lambda _record, _evidence: {},
            assert_fence=lambda: None,
        )

    second_path = campaign_receipt_path(tmp_path / "second", target_ref=intent.target_ref)
    with pytest.raises(RuntimeError, match="restoration returned invalid evidence"):
        run_campaign(
            path=second_path,
            intent=intent,
            segment_executors=_campaign_executors(intent),
            enter_maintenance=lambda _record, _existing: {},
            restore_maintenance=lambda _record, _evidence: "invalid",
            assert_fence=lambda: None,
        )


def test_campaign_catches_up_one_minor_lagging_node_groups_before_control_plane_hops() -> None:
    base = _intent()
    group = replace(base.node_groups[0], source_version="1.32")
    intent = replace(
        base,
        node_groups=(group,),
        compatibility_rows=(
            FrozenCompatibilityRow(
                group_key="worker",
                kubernetes_version="1.33",
                platform="gpu-l40s",
                os="ubuntu24.04",
                drivers_preset="cuda13.0",
            ),
            *base.compatibility_rows,
        ),
    )

    validate_campaign_intent(intent)
    assert intent.compatibility_versions == ("1.33", "1.34", "1.35")
    assert intent.segments[:3] == (
        "soperator-release",
        "node-templates:1.33",
        "runtime-readiness:1.33",
    )


def test_campaign_resume_skips_completed_segment_and_keeps_maintenance(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = campaign_receipt_path(tmp_path, target_ref=intent.target_ref)
    attempts: dict[str, int] = {}
    maintenance = {"entered": 0, "restored": 0}
    fail_once = {"value": True}

    def _executor(name: str):
        def _run() -> CampaignSegmentResult:
            attempts[name] = attempts.get(name, 0) + 1
            if name == "mk8s-hop:1.34" and fail_once["value"]:
                fail_once["value"] = False
                raise RuntimeError("injected")
            return CampaignSegmentResult(
                evidence={"segment": name},
                irreversible_frontier=f"completed:{name}",
            )

        return _run

    executors = {name: _executor(name) for name in intent.segments}

    with pytest.raises(RuntimeError, match="injected"):
        run_campaign(
            path=path,
            intent=intent,
            segment_executors=executors,
            enter_maintenance=lambda _record, _existing: (
                maintenance.__setitem__("entered", maintenance["entered"] + 1)
                or {"state": "active"}
            ),
            restore_maintenance=lambda _record, _evidence: {"state": "restored"},
            assert_fence=lambda: None,
        )

    changed = replace(intent, job_refresh_interval="60s")
    with pytest.raises(RuntimeError, match="different frozen intent"):
        run_campaign(
            path=path,
            intent=changed,
            segment_executors={
                name: (lambda: CampaignSegmentResult(evidence={})) for name in changed.segments
            },
            enter_maintenance=lambda _record, _existing: {},
            restore_maintenance=lambda _record, _evidence: {},
            assert_fence=lambda: None,
        )

    failed = load_campaign_receipt(path)
    assert failed is not None
    assert failed.status == "active"
    assert failed.maintenance == "active"
    assert failed.segments[0].status == "complete"
    assert failed.segments[1].status == "failed"

    completed = run_campaign(
        path=path,
        intent=intent,
        segment_executors=executors,
        enter_maintenance=lambda _record, _existing: {"state": "must-not-run"},
        restore_maintenance=lambda _record, _evidence: (
            maintenance.__setitem__("restored", maintenance["restored"] + 1)
            or {"state": "restored"}
        ),
        assert_fence=lambda: None,
    )

    assert completed.status == "complete"
    assert completed.maintenance == "restored"
    assert completed.maintenance_evidence == {
        "entry": {"summary": {"state": "active"}, "events": []},
        "restoration": {"state": "restored"},
    }
    assert attempts["soperator-release"] == 1
    assert attempts["mk8s-hop:1.34"] == 2
    assert maintenance == {"entered": 1, "restored": 1}


def test_completed_campaign_reproves_final_segment_without_reopening_maintenance(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = campaign_receipt_path(tmp_path, target_ref=intent.target_ref)
    attempts = {name: 0 for name in intent.segments}

    def _executor(name: str):
        def _run() -> CampaignSegmentResult:
            attempts[name] += 1
            return CampaignSegmentResult(evidence={"segment": name})

        return _run

    executors = {name: _executor(name) for name in intent.segments}
    run_campaign(
        path=path,
        intent=intent,
        segment_executors=executors,
        enter_maintenance=lambda _record, _existing: {"state": "active"},
        restore_maintenance=lambda _record, _evidence: {"state": "restored"},
        assert_fence=lambda: None,
    )

    completed = run_campaign(
        path=path,
        intent=intent,
        segment_executors=executors,
        enter_maintenance=lambda _record, _existing: pytest.fail(
            "completed reproof must not reopen maintenance"
        ),
        restore_maintenance=lambda _record, _evidence: pytest.fail(
            "completed reproof must not restore maintenance twice"
        ),
        assert_fence=lambda: None,
    )

    assert completed.status == "complete"
    assert completed.maintenance == "restored"
    assert attempts == {name: (2 if name == intent.segments[-1] else 1) for name in intent.segments}
    assert completed.segments[-1].attempts == 2
    assert completed.segments[-1].evidence == {"segment": intent.segments[-1]}


def test_parent_campaign_persists_controller_spool_checkpoint_during_release(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = campaign_receipt_path(tmp_path, target_ref=intent.target_ref)
    store = CampaignControllerSpoolMigrationStore(path=path, intent=intent)
    checkpoint = {
        "schema": "nebius-cxcli.soperator-controller-spool-migration.v1",
        "status": "prepared",
        "operationId": "sha256:" + "d" * 64,
    }

    def _release() -> CampaignSegmentResult:
        assert store.read() is None
        store.write(checkpoint)
        assert store.read() == checkpoint
        raise RuntimeError("injected release interruption")

    executors = {
        name: (
            _release
            if name == "soperator-release"
            else lambda name=name: CampaignSegmentResult(evidence={"segment": name})
        )
        for name in intent.segments
    }
    with pytest.raises(RuntimeError, match="release interruption"):
        run_campaign(
            path=path,
            intent=intent,
            segment_executors=executors,
            enter_maintenance=lambda _record, _existing: {"state": "active"},
            restore_maintenance=lambda _record, _evidence: {"state": "restored"},
            assert_fence=lambda: None,
        )

    interrupted = load_campaign_receipt(path)
    assert interrupted is not None
    assert interrupted.maintenance == "active"
    assert interrupted.maintenance_evidence == {
        "summary": {"state": "active"},
        "events": [],
        "controllerSpoolMigration": checkpoint,
    }


def test_campaign_refuses_different_frozen_intent(tmp_path: Path) -> None:
    intent = _intent()
    path = campaign_receipt_path(tmp_path, target_ref=intent.target_ref)

    def _fail() -> CampaignSegmentResult:
        raise RuntimeError("injected")

    executors = {
        name: (
            _fail
            if name == "soperator-release"
            else lambda name=name: CampaignSegmentResult(evidence={"segment": name})
        )
        for name in intent.segments
    }
    with pytest.raises(RuntimeError, match="injected"):
        run_campaign(
            path=path,
            intent=intent,
            segment_executors=executors,
            enter_maintenance=lambda _record, _existing: {"state": "active"},
            restore_maintenance=lambda _record, _evidence: {"state": "restored"},
            assert_fence=lambda: None,
        )


def test_maintenance_entry_events_are_durable_before_entry_completes(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = campaign_receipt_path(tmp_path, target_ref=intent.target_ref)
    executors = {
        name: (lambda name=name: CampaignSegmentResult(evidence={"segment": name}))
        for name in intent.segments
    }
    fail_once = {"value": True}

    def _enter(record_event, existing_evidence):
        if fail_once["value"]:
            fail_once["value"] = False
            record_event({"action": "scheduling-pause-recorded", "partitions": []})
            raise RuntimeError("injected maintenance entry interruption")
        assert existing_evidence["events"] == [
            {"action": "scheduling-pause-recorded", "partitions": []}
        ]
        return {"state": "active"}

    with pytest.raises(RuntimeError, match="entry interruption"):
        run_campaign(
            path=path,
            intent=intent,
            segment_executors=executors,
            enter_maintenance=_enter,
            restore_maintenance=lambda _record, _evidence: {"state": "restored"},
            assert_fence=lambda: None,
        )

    interrupted = load_campaign_receipt(path)
    assert interrupted is not None
    assert interrupted.maintenance == "entering"
    assert interrupted.maintenance_evidence["events"] == [
        {"action": "scheduling-pause-recorded", "partitions": []}
    ]

    completed = run_campaign(
        path=path,
        intent=intent,
        segment_executors=executors,
        enter_maintenance=_enter,
        restore_maintenance=lambda _record, evidence: {
            "eventCount": len(evidence["events"]),
        },
        assert_fence=lambda: None,
    )

    assert completed.maintenance_evidence["restoration"] == {"eventCount": 1}


def test_campaign_retries_interrupted_maintenance_restoration_without_rerunning_segments(
    tmp_path: Path,
) -> None:
    intent = _intent()
    path = campaign_receipt_path(tmp_path, target_ref=intent.target_ref)
    segment_calls: list[str] = []
    restoration_calls = 0

    def _segment(name: str):
        def _run() -> CampaignSegmentResult:
            segment_calls.append(name)
            return CampaignSegmentResult(evidence={"segment": name})

        return _run

    def _restore(record, evidence):
        nonlocal restoration_calls
        restoration_calls += 1
        if restoration_calls == 1:
            record({"action": "maintenance-held-job-release-intent", "job_id": "41"})
            raise RuntimeError("injected maintenance restoration interruption")
        assert any(
            event.get("action") == "maintenance-held-job-release-intent"
            for event in evidence["events"]
        )
        record({"action": "maintenance-held-job-release-applied", "job_id": "41"})
        return {"state": "restored"}

    executors = {name: _segment(name) for name in intent.segments}
    with pytest.raises(RuntimeError, match="restoration interruption"):
        run_campaign(
            path=path,
            intent=intent,
            segment_executors=executors,
            enter_maintenance=lambda _record, _existing: {"state": "active"},
            restore_maintenance=_restore,
            assert_fence=lambda: None,
        )

    interrupted = load_campaign_receipt(path)
    assert interrupted is not None
    assert interrupted.status == "active"
    assert interrupted.maintenance == "restoring"
    assert all(segment.status == "complete" for segment in interrupted.segments)
    assert interrupted.maintenance_evidence["events"][-1] == {
        "action": "maintenance-held-job-release-intent",
        "job_id": "41",
    }

    completed = run_campaign(
        path=path,
        intent=intent,
        segment_executors=executors,
        enter_maintenance=lambda _record, _existing: pytest.fail(
            "maintenance entry must not repeat"
        ),
        restore_maintenance=_restore,
        assert_fence=lambda: None,
    )

    assert completed.status == "complete"
    assert completed.maintenance == "restored"
    assert restoration_calls == 2
    assert completed.maintenance_evidence["entry"]["events"][-1] == {
        "action": "maintenance-held-job-release-applied",
        "job_id": "41",
    }
    assert segment_calls == list(intent.segments)


def test_completed_campaign_is_archived_before_new_intent(tmp_path: Path) -> None:
    intent = _intent()
    path = campaign_receipt_path(tmp_path, target_ref=intent.target_ref)
    executors = {
        name: (lambda name=name: CampaignSegmentResult(evidence={"segment": name}))
        for name in intent.segments
    }
    run_campaign(
        path=path,
        intent=intent,
        segment_executors=executors,
        enter_maintenance=lambda _record, _existing: {"state": "active"},
        restore_maintenance=lambda _record, _evidence: {"state": "restored"},
        assert_fence=lambda: None,
    )

    changed = replace(intent, source_release=intent.target_release)
    run_campaign(
        path=path,
        intent=changed,
        segment_executors=executors,
        enter_maintenance=lambda _record, _existing: {"state": "active"},
        restore_maintenance=lambda _record, _evidence: {"state": "restored"},
        assert_fence=lambda: None,
    )

    assert list(path.parent.glob("campaign-*.json"))


def test_embedded_campaign_intent_rejects_scalar_sequence_fields() -> None:
    payload = _intent().__dict__.copy()
    payload["kubernetes_hops"] = "1.34"

    with pytest.raises(RuntimeError, match="embedded intent is invalid"):
        campaign_intent_from_payload(payload)
