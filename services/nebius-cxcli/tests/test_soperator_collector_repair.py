import copy
import json
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import soperator_install_checks_repair as repair
from nebius_cxcli.soperator_install_render_repair import (
    COLLECTOR_REPAIR_REASON,
    OUTER_FILE,
    VALUES_FILE,
)
from nebius_cxcli.soperator_jail_logs_binding import JAIL_LOGS_RELEASE
from soperator_fixtures import sample_jail_logs_binding


@pytest.mark.parametrize(
    "mutation",
    [None, "source-artifact", "source-reference", "accepted", "gate-actions", "successful-child"],
)
def test_collector_admission_seals_only_exact_preacceptance_delta(tmp_path, monkeypatch, mutation):
    values, adapter = sample_jail_logs_binding()
    paths = SimpleNamespace(
        project_dir=tmp_path,
        flux_dir=tmp_path / "generated/flux",
        reports_dir=tmp_path / "generated/reports",
    )
    paths.flux_dir.mkdir(parents=True)
    paths.reports_dir.mkdir(parents=True)
    outer = {
        "spec": {
            "values": values,
            "postRenderers": [
                {"kustomize": {"patches": [{"target": {"name": JAIL_LOGS_RELEASE}, "patch": "[]"}]}}
            ],
        }
    }
    original = {
        VALUES_FILE: yaml.safe_dump({"data": {"values.yaml": yaml.safe_dump(values)}}).encode(),
        OUTER_FILE: yaml.safe_dump(outer).encode(),
        "soperator-nebius-adapter.yaml": yaml.safe_dump_all(adapter).encode(),
    }
    for name, content in original.items():
        (paths.flux_dir / name).write_bytes(content)
    hashes = repair._file_hashes(original)
    spec = {
        "strategy": "install",
        "current_release": "",
        "target_release": "4.1.5",
        "target_ref": "lab",
        "desired_values_sha256": hashes[VALUES_FILE],
        "adapter_sha256": hashes["soperator-nebius-adapter.yaml"],
        "intervention_generation": 0,
    }
    operation_sha = repair._digest(spec)
    predecessor = {
        "status": "running",
        "operation": {"spec": spec},
        "transitions": [
            {"phase": "resolve-immutable-sources", "status": "complete"},
            {"phase": "establish-boot-storage-barrier", "status": "complete"},
            {"id": "apply", "phase": "apply-declarative-release", "status": "running"},
        ],
        "irreversibleIntent": {
            "transitionId": "apply",
            "phase": "apply-declarative-release",
            "disposition": "pending-forward-only",
        },
    }
    check_state = {"phase": "planned", "jobs": {}}
    if mutation == "accepted":
        check_state["phase"] = "accepted"
    for name, payload in [
        ("soperator-release-reconcile-test.json", predecessor),
        (f"soperator-checks-{operation_sha.removeprefix('sha256:')[:24]}.json", check_state),
    ]:
        p = paths.reports_dir / name
        p.write_text(json.dumps(payload))
        p.chmod(0o600)
    snapshot = SimpleNamespace(
        release="4.1.5",
        charts={"activeChecks": SimpleNamespace(digest="sha256:checks")},
        third_party_charts={
            "opentelemetryCollector": SimpleNamespace(
                chart="collector", version="1.2.3", package_sha256="a" * 64
            )
        },
        release_graph=[
            SimpleNamespace(
                release_name=JAIL_LOGS_RELEASE,
                owner="third-party",
                chart_key="opentelemetryCollector",
            )
        ],
    )
    monkeypatch.setattr(repair, "load_soperator_release_snapshot", lambda *a: snapshot)
    monkeypatch.setattr(repair, "frozen_soperator_release_from_snapshot", lambda *a: None)
    child = {
        "metadata": {
            "uid": "collector-uid",
            "labels": {
                "app.kubernetes.io/version": "4.1.5",
                "soperator.nebius.ai/release-graph": "nebius-cxcli",
            },
        },
        "spec": {
            "chartRef": {
                "kind": "HelmChart",
                "name": "soperator-third-party-opentelemetrycollector",
                "namespace": "flux-system",
            }
        },
        "status": {
            "lastAttemptedRevision": "1.2.3",
            "lastAttemptedConfigDigest": "sha256:" + "b" * 64,
        },
    }
    source = {
        "metadata": {"uid": "source-uid"},
        "spec": {"chart": "collector", "version": "1.2.3"},
        "status": {"artifact": {"digest": "sha256:" + "a" * 64}},
    }
    if mutation == "source-artifact":
        source["status"]["artifact"]["digest"] = "foreign"
    elif mutation == "source-reference":
        child["spec"]["chartRef"]["name"] = "foreign"
    elif mutation == "successful-child":
        child["status"]["history"] = [{"status": "deployed"}]

    def read(args, **kwargs):
        if args[0] == "helmrelease":
            return child
        if args[0] == "helmchart":
            return source
        if args[0] == "slurmcluster":
            return {
                "spec": {"volumeSources": values["slurmCluster"]["overrideValues"]["volumeSources"]}
            }
        raise AssertionError("collector repair must not inspect or terminate native check Jobs")

    monkeypatch.setattr(repair, "_kube_get", read)
    seals = []
    monkeypatch.setattr(
        repair, "_bind_repair_admission", lambda payload, **kwargs: seals.append(payload)
    )
    journal = {"operationSpecSha256": operation_sha, "lastCompletedStage": "gated", "actions": []}
    if mutation == "gate-actions":
        journal["actions"] = [{"performed": "other"}]
    kwargs = dict(
        paths=paths,
        target_ref="lab",
        scheduling_journal=journal,
        local_scheduling_journal=copy.deepcopy(journal),
        env={},
        kube_context="test",
        assert_authority=lambda: None,
        reason=COLLECTOR_REPAIR_REASON,
    )
    if mutation is not None:
        with pytest.raises(RuntimeError):
            repair._prepare_install_binding_repair(**kwargs)
        assert seals == []
        assert repair._files(paths.flux_dir) == original
        return
    result = repair._prepare_install_binding_repair(**kwargs)
    assert len(seals) == 1 and seals[0] == result
    assert result["collectorRelease"]["sourceUid"] == "source-uid"
    assert "checksRelease" not in result
    assert result["waitHook"] is None
    changed = repair._files(paths.flux_dir)
    assert {name for name in original if original[name] != changed[name]} == {OUTER_FILE}
    assert result["interventionGeneration"] == 1
