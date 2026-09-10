"""Enroot permission belongs to the existing upstream profile operator."""

import copy
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import flux_ops
from nebius_cxcli import soperator_enroot as enroot
from nebius_cxcli import soperator_install_userns_recovery as recovery
from nebius_cxcli.soperator_checks_contract import job_execution_digest
from nebius_cxcli.soperator_enroot import (
    ENROOT_PROFILE_NAME,
    enroot_profile_document,
    enroot_profile_nodes_ready,
    split_enroot_profile_documents,
)
from nebius_cxcli.soperator_install_userns_recovery import userns_denial
from nebius_cxcli.soperator_install_userns_repair import userns_repair_candidate
from test_soperator_upstream_adapter import _values, render_soperator_adapter_documents


def profile():
    return enroot_profile_document(
        {
            "soperator.nebius.ai/managed-by": "nebius-cxcli-adapter",
            "soperator.nebius.ai/lifecycle": "recreatable",
        }
    )


def test_adapter_declares_application_specific_userns_permission():
    documents, _ = render_soperator_adapter_documents(_values())
    profiles = [d for d in documents if d["kind"] == "AppArmorProfile"]
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile["apiVersion"] == "security-profiles-operator.x-k8s.io/v1alpha1"
    assert profile["metadata"]["name"] == "cxcli-soperator-enroot-v1"
    assert profile["metadata"]["labels"]["soperator.nebius.ai/lifecycle"] == "recreatable"
    policy = profile["spec"]["policy"]
    assert " /usr/bin/enroot-nsenter flags=(unconfined)" in policy
    assert "userns," in policy
    assert "**" not in policy
    assert "sysctl" not in policy


@pytest.mark.parametrize("mutation", ["name", "namespace", "policy", "lifecycle", "duplicate"])
def test_profile_does_not_adopt_arbitrary_configuration(mutation):
    p = profile()
    if mutation in {"name", "namespace"}:
        p["metadata"][mutation] = "foreign"
    elif mutation == "policy":
        p["spec"]["policy"] += "file,"
    elif mutation == "lifecycle":
        p["metadata"]["labels"]["soperator.nebius.ai/lifecycle"] = "protected"
    with pytest.raises(ValueError):
        split_enroot_profile_documents([p, p] if mutation == "duplicate" else [p])


@pytest.mark.parametrize(
    "mutation", [None, "missing", "pending", "old_uid", "wrong_kind", "duplicate", "mixed"]
)
def test_profile_gate_requires_owner_bound_installation_on_both_nodes(mutation):
    p = profile()
    p["metadata"]["uid"] = "current-profile"
    nodes = {
        "items": [
            {
                "metadata": {"name": n, "uid": n},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
            for n in ["n0", "n1"]
        ]
    }
    statuses = {
        "items": [
            {
                "metadata": {
                    "uid": n,
                    "namespace": "soperator",
                    "ownerReferences": [
                        {
                            "uid": "current-profile",
                            "name": ENROOT_PROFILE_NAME,
                            "kind": "AppArmorProfile",
                            "controller": True,
                        }
                    ],
                },
                "nodeName": n,
                "status": "Installed",
            }
            for n in ["n0", "n1"]
        ]
    }
    if mutation == "missing":
        statuses["items"].pop()
    if mutation == "pending":
        statuses["items"][1]["status"] = "Pending"
    if mutation == "old_uid":
        statuses["items"][1]["metadata"]["ownerReferences"][0]["uid"] = "old"
    if mutation == "wrong_kind":
        statuses["items"][1]["metadata"]["ownerReferences"][0]["kind"] = "ConfigMap"
    if mutation in {"duplicate", "mixed"}:
        statuses["items"].append(copy.deepcopy(statuses["items"][0]))
    if mutation == "mixed":
        statuses["items"][-1]["status"] = "Pending"
    if mutation in {"wrong_kind", "duplicate", "mixed"}:
        with pytest.raises(RuntimeError):
            enroot_profile_nodes_ready(p, nodes, statuses)
    else:
        assert enroot_profile_nodes_ready(p, nodes, statuses) is (mutation is None)


@pytest.mark.parametrize("mutation", [None, "namespace", "name", "policy", "owner", "deleting"])
def test_profile_apply_rejects_kernel_policy_collision_before_mutation(
    monkeypatch, tmp_path, mutation
):
    existing = profile()
    if mutation in {"namespace", "name"}:
        existing["metadata"][mutation] = "foreign"
    elif mutation == "policy":
        existing["spec"]["policy"] += "file,"
    elif mutation == "owner":
        existing["metadata"]["labels"]["soperator.nebius.ai/managed-by"] = "foreign"
    elif mutation == "deleting":
        existing["metadata"]["deletionTimestamp"] = "now"
    applied = []

    def run(args, **kwargs):
        if "apply" in args:
            applied.append(kwargs["input"])
            return SimpleNamespace(stdout="")
        assert "get" in args
        if "-A" in args:
            return SimpleNamespace(stdout=json.dumps({"items": [existing]}))
        return SimpleNamespace(stdout="{}")

    monkeypatch.setattr(enroot.subprocess, "run", run)
    monkeypatch.setattr(enroot, "enroot_profile_nodes_ready", lambda *a: True)
    args = dict(env={}, cache_dir=tmp_path, timeout_seconds=0, poll_interval_seconds=0)
    if mutation:
        with pytest.raises(RuntimeError, match="collides"):
            enroot.apply_enroot_profile([profile()], **args)
        assert applied == []
    else:
        enroot.apply_enroot_profile([profile()], **args)
        assert yaml.safe_load(applied[0]) == profile()


def test_profile_gate_timeout_does_not_claim_installation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        enroot.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout='{"items": []}')
    )
    monkeypatch.setattr(enroot, "enroot_profile_nodes_ready", lambda *a: False)
    with pytest.raises(RuntimeError, match="every ready node"):
        enroot.apply_enroot_profile(
            [profile()], env={}, cache_dir=tmp_path, timeout_seconds=0, poll_interval_seconds=0
        )


@pytest.mark.parametrize(
    "mutation", [None, "job_uid", "execution", "accounting", "active_job", "reservation"]
)
def test_recovery_closes_only_proven_reservation_after_terminal_native_failure(
    monkeypatch, mutation
):
    job = {
        "metadata": {"uid": "native-uid"},
        "spec": {"template": {"spec": {"containers": [{"name": "native"}]}}},
        "status": {"conditions": [{"type": "Complete", "status": "True"}]},
    }
    row = {"JobId": "17", "JobState": "FAILED", "ExitCode": "1:0"}
    reservation = {"name": "owned", "fingerprint": "same", "users": ["root", "soperatorchecks"]}
    proof = {
        "job": {
            "name": "native",
            "uid": "native-uid",
            "entry": {"execution": job_execution_digest(job)},
        },
        "slurm": copy.deepcopy(row),
        "reservation": copy.deepcopy(reservation),
    }
    if mutation == "job_uid":
        job["metadata"]["uid"] = "replaced"
    if mutation == "execution":
        job["spec"]["template"]["spec"]["containers"][0]["name"] = "changed"
    if mutation == "accounting":
        row["JobState"] = "COMPLETED"
    if mutation == "reservation":
        reservation["fingerprint"] = "changed"
    writes = []

    def slurm(command):
        assert writes[-1] == "authority"
        assert command == "scontrol update ReservationName=owned Users=root"
        writes.append(command)
        reservation["users"] = ["root"]

    runner = SimpleNamespace(
        state={},
        _get=lambda *a: job,
        _reservation=lambda *a: copy.deepcopy(reservation),
        slurm=slurm,
        authority=lambda: writes.append("authority"),
        _save=lambda: writes.append("saved"),
    )
    monkeypatch.setattr(recovery, "_accounted", lambda *a: row)
    monkeypatch.setattr(
        recovery,
        "slurm_jobs",
        lambda *a: [{"JobState": "RUNNING"}] if mutation == "active_job" else [],
    )
    if mutation:
        with pytest.raises(RuntimeError):
            recovery.close_userns_repair_reservation(runner, {"usernsFailure": proof})
        assert writes == []
        assert runner.state == {}
    else:
        recovery.close_userns_repair_reservation(runner, {"usernsFailure": proof})
        assert reservation["users"] == ["root"]
        assert runner.state["usernsRepairQuiescence"]["status"] == "complete"
        assert writes == ["authority", "scontrol update ReservationName=owned Users=root", "saved"]


def test_recovery_delta_preserves_every_previous_adapter_byte():
    before = {
        "soperator-nebius-adapter.yaml": yaml.safe_dump(
            {"kind": "ConfigMap", "metadata": {"labels": profile()["metadata"]["labels"]}},
            sort_keys=False,
        ).encode(),
        "untouched": b"fixed",
    }
    after = userns_repair_candidate(before)
    assert after["soperator-nebius-adapter.yaml"].startswith(
        before["soperator-nebius-adapter.yaml"]
    )
    assert after["untouched"] == b"fixed"
    assert userns_repair_candidate(after, inverse=True) == before
    assert userns_repair_candidate(after) == after
    bad = {
        **after,
        "soperator-nebius-adapter.yaml": after["soperator-nebius-adapter.yaml"].replace(
            b"userns,", b"file,"
        ),
    }
    with pytest.raises(RuntimeError):
        userns_repair_candidate(bad, inverse=True)


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "other_boot",
        "userspace",
        "different_pid",
        "different_comm",
        "different_cap",
        "late",
        "missing",
    ],
)
def test_denial_requires_same_kernel_process_and_native_failure_window(mutation):
    moment = 1788896446600000
    rows = [
        {
            "_BOOT_ID": "a" * 32,
            "_TRANSPORT": "kernel",
            "__REALTIME_TIMESTAMP": str(moment),
            "MESSAGE": m,
        }
        for m in [
            'apparmor="AUDIT" operation="userns_create" profile="unconfined" pid=42 comm="enroot-nsenter" target="unprivileged_userns"',
            'apparmor="DENIED" operation="capable" profile="unprivileged_userns" pid=42 comm="enroot-nsenter" capname="sys_admin"',
        ]
    ]
    if mutation == "other_boot":
        rows[1]["_BOOT_ID"] = "b" * 32
    if mutation == "userspace":
        rows[1]["_TRANSPORT"] = "stdout"
    if mutation == "different_pid":
        rows[1]["MESSAGE"] = rows[1]["MESSAGE"].replace("pid=42", "pid=43")
    if mutation == "different_comm":
        rows[1]["MESSAGE"] = rows[1]["MESSAGE"].replace("enroot-nsenter", "other")
    if mutation == "different_cap":
        rows[1]["MESSAGE"] = rows[1]["MESSAGE"].replace("sys_admin", "net_admin")
    if mutation == "late":
        rows[1]["__REALTIME_TIMESTAMP"] = str(moment + 30000000)
    if mutation == "missing":
        rows.pop()
    args = dict(
        boot="a" * 32,
        started=datetime(2026, 9, 8, 19, 40, 46),
        ended=datetime(2026, 9, 8, 19, 40, 47),
    )
    if mutation:
        with pytest.raises(RuntimeError):
            userns_denial(rows, **args)
    else:
        assert userns_denial(rows, **args)["pid"] == "42"


@pytest.mark.parametrize("owner", [False, True])
def test_staged_profile_is_applied_only_after_upstream_owner_before_main(
    tmp_path, monkeypatch, owner
):
    from test_soperator_flux_sources import _outer_bundle, _paths, _staged_contract

    contract = _staged_contract()
    first = contract["releases"][0]
    if owner:
        first["upstreamReleaseName"] = "soperator-fluxcd-security-profiles-operator"
    bundle = yaml.safe_load(_outer_bundle())
    bundle["spec"]["postRenderers"][0]["kustomize"]["patches"][0]["target"]["name"] = first[
        "upstreamReleaseName"
    ]
    events = []

    def run(args, **kwargs):
        if "kustomize" in args:
            return SimpleNamespace(
                returncode=0,
                stdout=yaml.safe_dump(bundle) + "---\n" + yaml.safe_dump(profile()),
                stderr="",
            )
        if "apply" in args:
            assert "AppArmorProfile" not in kwargs["input"]
            events.append("apply-bundle")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(flux_ops.subprocess, "run", run)
    monkeypatch.setattr(flux_ops, "_rendered_soperator_graph_contract", lambda *a: contract)
    monkeypatch.setattr(flux_ops, "_run_kubectl_json_process", lambda *a, **k: {"items": []})
    for name in [
        "_patch_helmrelease_suspend",
        "_remove_helmrelease_suspend",
        "_wait_for_helmrelease_quiescence",
        "_wait_for_suspended_child_inventory",
        "_wait_for_soperator_dependency_health",
    ]:
        monkeypatch.setattr(flux_ops, name, lambda *a, **k: None)
    monkeypatch.setattr(flux_ops, "prepare_soperator_release_sources", lambda *a, **k: ())
    monkeypatch.setattr(
        flux_ops, "_wait_for_soperator_release_stage", lambda c, s, **k: events.append(f"stage-{s}")
    )
    monkeypatch.setattr(
        flux_ops, "apply_enroot_profile", lambda *a, **k: events.append("profile-ready")
    )
    if not owner:
        with pytest.raises(ValueError, match="one upstream profile operator"):
            flux_ops.apply_staged_soperator_release(_paths(tmp_path), cache_dir=tmp_path / "cache")
        assert events == []
    else:
        flux_ops.apply_staged_soperator_release(_paths(tmp_path), cache_dir=tmp_path / "cache")
        assert events.index("stage-0") < events.index("profile-ready") < events.index("stage-1")
