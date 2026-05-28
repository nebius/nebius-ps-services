from __future__ import annotations

import json
import subprocess

import pytest

from nebius_cxcli.runtime_validation import validate_runtime_payload
from nebius_cxcli.soperator_onboarding import (
    ONBOARDING_ACTION_INSTALL_SOPERATOR,
    ONBOARDING_ACTION_UPGRADE_SOPERATOR,
    analyze_soperator_onboarding_snapshot,
    collect_kubectl_soperator_snapshot,
    soperator_onboarding_fingerprint,
    soperator_onboarding_is_accepted,
    validate_soperator_onboarding_acceptance,
    write_soperator_onboarding_reports,
)


def _snapshot(*, release: dict[str, object] | None = None) -> dict[str, object]:
    releases = [release] if release is not None else []
    return {
        "node_groups": {
            "cpu-a": {
                "gpu": False,
                "node_count": 2,
                "labels": {"nebius.com/node-group": "cpu-a"},
            },
            "h100": {
                "gpu": True,
                "node_count": 2,
                "labels": {
                    "nebius.com/node-group": "h100",
                    "topology.nebius.com/tier-0": "leaf-a",
                },
                "allocatable": {"nvidia.com/gpu": "2", "rdma/shared_device": "8"},
            },
        },
        "helm_releases": releases,
        "crds": [],
        "namespaces": ["default"],
        "storage": {},
    }


def test_soperator_onboarding_analyzer_detects_vanilla_cluster_actions() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(),
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert report.state == "vanilla-mk8s"
    assert ONBOARDING_ACTION_INSTALL_SOPERATOR in {action.id for action in report.actions}
    assert any(finding.layer == "storage-sfs" for finding in report.findings)
    assert any(finding.layer == "topology" and finding.status == "available" for finding in report.findings)


def test_soperator_onboarding_analyzer_ignores_noncanonical_slurm_nebius_crds() -> None:
    snapshot = _snapshot()
    snapshot["crds"] = ["slurmjobs.example.nebius-internal.io"]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert report.state == "vanilla-mk8s"
    assert ONBOARDING_ACTION_INSTALL_SOPERATOR in {action.id for action in report.actions}


def test_soperator_onboarding_analyzer_accepts_fallback_selector_labels() -> None:
    snapshot = _snapshot()
    node_groups = snapshot["node_groups"]  # type: ignore[index]
    node_groups["h100"]["labels"] = {"yandex.cloud/node-group-id": "mk8snodegroup-123"}  # type: ignore[index]

    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert not any(finding.layer == "role-mapping" and finding.status == "blocked" for finding in report.findings)


def test_soperator_onboarding_analyzer_offers_upgrade_for_older_release() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator",
                "namespace": "soperator",
                "chart": "soperator-0.24.0",
                "app_version": "0.24.0",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert report.state == "existing-soperator"
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR in {action.id for action in report.actions}
    assert any(finding.status == "upgrade-available" for finding in report.findings)


def test_soperator_onboarding_analyzer_does_not_downgrade_newer_release() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator",
                "namespace": "soperator",
                "chart": "soperator-0.26.0",
                "app_version": "0.26.0",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    action_ids = {action.id for action in report.actions}
    assert ONBOARDING_ACTION_UPGRADE_SOPERATOR not in action_ids
    assert any(finding.status == "newer-than-cxcli" for finding in report.findings)


def test_soperator_onboarding_analyzer_blocks_incompatible_release_identity() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "custom-slurm",
                "namespace": "slurm",
                "chart": "soperator-1.0.0",
                "app_version": "1.0.0",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert report.state == "partial-soperator"
    assert any(finding.status == "blocked" for finding in report.findings)


def test_soperator_onboarding_analyzer_ignores_soperator_sibling_charts() -> None:
    report = analyze_soperator_onboarding_snapshot(
        _snapshot(
            release={
                "name": "soperator-checks",
                "namespace": "soperator",
                "chart": "soperator-checks-1.0.0",
                "app_version": "1.0.0",
            }
        ),
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert report.state == "vanilla-mk8s"
    assert not any(finding.status == "blocked" for finding in report.findings)


def test_soperator_onboarding_analyzer_blocks_collection_errors() -> None:
    report = analyze_soperator_onboarding_snapshot(
        {
            "collection_errors": [
                {
                    "command": "kubectl --context missing get nodes -o json",
                    "message": "context missing",
                }
            ]
        },
        target_ref="cluster1",
        pinned_chart_version="0.25.0",
        pinned_app_version="0.25.0",
    )

    assert report.state == "analysis-blocked"
    assert any(finding.layer == "kubernetes" and finding.status == "blocked" for finding in report.findings)
    assert report.actions == ()


def test_collect_kubectl_soperator_snapshot_records_shellout_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_called_process_error(command, **kwargs):
        _ = kwargs
        raise subprocess.CalledProcessError(
            1,
            command,
            stderr="forbidden by current context",
        )

    monkeypatch.setattr("nebius_cxcli.soperator_onboarding.subprocess.run", _raise_called_process_error)

    snapshot = collect_kubectl_soperator_snapshot(kube_context="missing-context", timeout=1)

    assert snapshot["collection_errors"]
    report = analyze_soperator_onboarding_snapshot(snapshot, target_ref="cluster1")
    assert report.state == "analysis-blocked"


def test_collect_kubectl_soperator_snapshot_skips_soperator_resources_without_crds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def _run(command, **kwargs):
        _ = kwargs
        command_list = list(command)
        commands.append(command_list)
        assert "slurmclusters,nodeconfigurators,nodesets" not in command_list
        stdout = "[]" if command_list[0] == "helm" else json.dumps({"items": []})
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("nebius_cxcli.soperator_onboarding.subprocess.run", _run)

    snapshot = collect_kubectl_soperator_snapshot(kube_context="vanilla", timeout=1)

    assert snapshot["collection_errors"] == []
    assert snapshot["soperator_resources"] == []
    assert not any("slurmclusters,nodeconfigurators,nodesets" in command for command in commands)


def test_collect_kubectl_soperator_snapshot_records_node_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(command, **kwargs):
        _ = kwargs
        command_list = list(command)
        if command_list[0] == "helm":
            stdout = "[]"
        elif len(command_list) > 4 and command_list[4] == "nodes":
            stdout = json.dumps(
                {
                    "items": [
                        {
                            "metadata": {
                                "name": "computeinstance-a",
                                "labels": {
                                    "node.kubernetes.io/instance-type": "cpu-d3",
                                },
                            },
                            "status": {"allocatable": {"cpu": "3900m"}},
                        },
                        {
                            "metadata": {
                                "name": "computeinstance-b",
                                "labels": {
                                    "node.kubernetes.io/instance-type": "cpu-d3",
                                },
                            },
                            "status": {"allocatable": {"cpu": "3900m"}},
                        },
                    ]
                }
            )
        else:
            stdout = json.dumps({"items": []})
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("nebius_cxcli.soperator_onboarding.subprocess.run", _run)

    snapshot = collect_kubectl_soperator_snapshot(kube_context="vanilla", timeout=1)

    assert snapshot["node_groups"]["cpu-d3"]["nodes"] == [
        "computeinstance-a",
        "computeinstance-b",
    ]


def test_collect_kubectl_soperator_snapshot_queries_only_discovered_soperator_crds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def _run(command, **kwargs):
        _ = kwargs
        command_list = list(command)
        commands.append(command_list)
        if command_list[0] == "helm":
            stdout = "[]"
        elif len(command_list) > 4 and command_list[4] == "crd":
            stdout = json.dumps(
                {
                    "items": [
                        {
                            "metadata": {
                                "name": "slurmclusters.slurm.nebius.ai",
                            }
                        }
                    ]
                }
            )
        else:
            stdout = json.dumps({"items": []})
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("nebius_cxcli.soperator_onboarding.subprocess.run", _run)

    snapshot = collect_kubectl_soperator_snapshot(kube_context="partial", timeout=1)

    assert snapshot["collection_errors"] == []
    assert any(command[4] == "slurmclusters" for command in commands if len(command) > 4)
    assert not any(
        "nodeconfigurators" in command or "nodesets" in command
        for command in commands
    )


def _onboarding_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "deploy": {
            "targets": [
                {
                    "instance_id": "cluster1",
                    "kind": "external-mk8s",
                    "ownership": "external",
                    "access": "external",
                    "kube_context": "nebius-cluster1-mk8scluster-123-external",
                    "inventory": {
                        "node_groups": {
                            "cpu-a": {"gpu": False, "node_count": 2},
                            "h100": {"gpu": True, "node_count": 2},
                        }
                    },
                    "soperator_onboarding": {
                        "accepted": True,
                        "analysis_fingerprint": "",
                        "state": "vanilla-mk8s",
                        "storage_mode": "adopt-existing-storage",
                        "actions": [ONBOARDING_ACTION_INSTALL_SOPERATOR],
                    },
                }
            ]
        },
        "infra": {"components": []},
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "group": "slurm",
                    "enabled": True,
                    "install_mode": "onboard-existing-cluster",
                    "repo": "oci://example.invalid/charts",
                    "version": "0.25.0",
                    "namespace": "soperator",
                    "release-name": "soperator",
                    "values": {},
                }
            ]
        },
    }
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    target["soperator_onboarding"]["analysis_fingerprint"] = soperator_onboarding_fingerprint(  # type: ignore[index]
        payload,
        target_ref="cluster1",
    )
    return payload


def test_onboarding_acceptance_refuses_stale_analysis() -> None:
    payload = _onboarding_payload()
    validate_soperator_onboarding_acceptance(payload, target_ref="cluster1")

    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    target["inventory"]["node_groups"]["extra-cpu"] = {"gpu": False, "node_count": 1}  # type: ignore[index]

    with pytest.raises(ValueError, match="does not have a current accepted"):
        validate_soperator_onboarding_acceptance(payload, target_ref="cluster1")


def test_onboarding_acceptance_refuses_blocked_analysis_even_with_current_fingerprint() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["accepted"] = True
    onboarding["state"] = "analysis-blocked"
    onboarding["collection_errors"] = [
        {
            "command": "kubectl --context missing get nodes -o json",
            "message": "context missing",
        }
    ]
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(
        payload,
        target_ref="cluster1",
    )

    assert not soperator_onboarding_is_accepted(payload, target_ref="cluster1")
    with pytest.raises(ValueError, match="does not have a current accepted"):
        validate_soperator_onboarding_acceptance(payload, target_ref="cluster1")


def test_onboarding_acceptance_refuses_partial_analysis_even_with_current_fingerprint() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["accepted"] = True
    onboarding["state"] = "partial-soperator"
    onboarding["analysis_fingerprint"] = soperator_onboarding_fingerprint(
        payload,
        target_ref="cluster1",
    )

    assert not soperator_onboarding_is_accepted(payload, target_ref="cluster1")
    with pytest.raises(ValueError, match="does not have a current accepted"):
        validate_soperator_onboarding_acceptance(payload, target_ref="cluster1")


def test_onboarding_fingerprint_tracks_soperator_chart_pin() -> None:
    payload = _onboarding_payload()
    original = soperator_onboarding_fingerprint(payload, target_ref="cluster1")
    chart = payload["apps"]["charts"][0]  # type: ignore[index]
    chart["version"] = "0.26.0"

    assert soperator_onboarding_fingerprint(payload, target_ref="cluster1") != original


def test_onboarding_fingerprint_ignores_ephemeral_helm_release_metadata() -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    onboarding = target["soperator_onboarding"]  # type: ignore[index]
    onboarding["helm_releases"] = [
        {
            "name": "soperator",
            "namespace": "soperator",
            "chart": "soperator-0.25.0",
            "app_version": "0.25.0",
            "updated": "2026-05-20 10:00:00",
            "revision": "1",
        }
    ]
    original = soperator_onboarding_fingerprint(payload, target_ref="cluster1")
    onboarding["helm_releases"][0]["updated"] = "2026-05-20 10:05:00"  # type: ignore[index]
    onboarding["helm_releases"][0]["revision"] = "2"  # type: ignore[index]

    assert soperator_onboarding_fingerprint(payload, target_ref="cluster1") == original


def test_runtime_validation_accepts_external_soperator_target_without_mk8s_infra() -> None:
    validate_runtime_payload(_onboarding_payload())


def test_onboarding_report_writer_persists_target_report(tmp_path) -> None:
    payload = _onboarding_payload()

    written = write_soperator_onboarding_reports(payload, tmp_path / "generated")

    assert len(written) == 1
    report_path = written[0]
    assert report_path == tmp_path / "generated" / "reports" / "soperator-onboarding-cluster1.json"
    report = report_path.read_text(encoding="utf-8")
    assert '"target_ref": "cluster1"' in report
    assert soperator_onboarding_fingerprint(payload, target_ref="cluster1") in report


def test_onboarding_report_writer_preserves_collection_errors(tmp_path) -> None:
    payload = _onboarding_payload()
    target = payload["deploy"]["targets"][0]  # type: ignore[index]
    target["soperator_onboarding"]["collection_errors"] = [  # type: ignore[index]
        {
            "command": "kubectl --context missing get nodes -o json",
            "message": "context missing",
        }
    ]

    written = write_soperator_onboarding_reports(payload, tmp_path / "generated")

    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert report["state"] == "analysis-blocked"
    assert report["findings"][0]["evidence"]["errors"][0]["message"] == "context missing"
