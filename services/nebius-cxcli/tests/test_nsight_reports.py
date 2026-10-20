"""Installer/consumer storage contract; fixtures are not live storage evidence."""

import base64
import copy
import json
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from nebius_cxcli import nsight_report_destination, nsight_reports
from nebius_cxcli.deployment_jail_state import jail_values
from nebius_cxcli.nsight import soperator_report_binding
from test_nsight_profiling import profiling_config


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    reports = root / "reports"
    reports.mkdir()
    reports.chmod(0o1777)
    parent = root / "installation"
    parent.mkdir()
    parent.chmod(0o755)
    descriptor = parent / "nsight-reports.json"
    verification = {"schema": "fixture-package", "digest": "fixture"}
    receipt = parent / "nsight.json"
    receipt.write_text(json.dumps(verification))
    receipt.chmod(0o644)
    monkeypatch.setattr(nsight_reports, "OWNER_UID", os.getuid())
    monkeypatch.setattr(nsight_reports, "DESCRIPTOR", str(descriptor))
    monkeypatch.setattr(
        nsight_reports,
        "mount_identity",
        lambda fd: {
            "id": "fixture",
            "device": str(os.fstat(fd).st_dev),
            "point": str(root),
            "type": "fixture",
        },
    )
    monkeypatch.setattr(
        nsight_reports.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="")
    )
    request = {
        "verification": verification,
        "installation": {
            "generation": "generation-1",
            "target": "fixture",
            "kubernetes_uid": "fixture",
        },
        "storage": {
            key: "fixture" for key in ("namespace", "pvcName", "pvcUid", "pvName", "pvUid")
        },
        "producer_path": str(reports),
        "mount_point": str(root),
    }
    return request, descriptor, reports / ".cxcli-nsight/binding.json"


def test_reconciliation_is_byte_and_identity_preserving(installed):
    request, descriptor, marker = installed
    first = nsight_reports.reconcile(request)
    snapshots = [
        (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (descriptor, marker)
    ]
    assert nsight_reports.reconcile(request) == first
    assert snapshots == [
        (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (descriptor, marker)
    ]
    assert descriptor.read_bytes() == marker.read_bytes()


@pytest.mark.parametrize("missing", ["descriptor", "marker"])
def test_missing_contract_file_reuses_intact_binding_without_package_changes(installed, missing):
    request, descriptor, marker = installed
    first = nsight_reports.reconcile(request)
    receipt = descriptor.with_name("nsight.json").read_bytes()
    (descriptor if missing == "descriptor" else marker).unlink()
    assert nsight_reports.reconcile(request) == first
    assert descriptor.with_name("nsight.json").read_bytes() == receipt


def test_unsafe_existing_report_root_is_never_silently_migrated(installed):
    request, descriptor, marker = installed
    reports = Path(request["producer_path"])
    reports.chmod(0o777)
    with pytest.raises(ValueError, match="1777"):
        nsight_reports.reconcile(request)
    assert reports.stat().st_mode & 0o7777 == 0o777
    assert not descriptor.exists() and not marker.exists()


def test_binding_change_preserves_admitted_jobs_until_quiescence(installed, monkeypatch):
    request, descriptor, marker = installed
    first = nsight_reports.reconcile(request)
    previous = descriptor.read_bytes()
    request["installation"]["generation"] = "generation-2"
    monkeypatch.setattr(
        nsight_reports.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="123 RUNNING")
    )
    with pytest.raises(ValueError, match="quiescent"):
        nsight_reports.reconcile(request)
    assert descriptor.read_bytes() == marker.read_bytes() == previous
    assert json.loads(marker.with_name("admission.json").read_text())["open"] is True
    monkeypatch.setattr(
        nsight_reports.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="")
    )
    second = nsight_reports.reconcile(request)
    assert second["token"] != first["token"]
    assert descriptor.read_bytes() == marker.read_bytes()


def test_interrupted_invalidation_resumes_exact_inactive_marker(installed, monkeypatch):
    request, descriptor, marker = installed
    nsight_reports.reconcile(request)
    request["installation"]["generation"] = "generation-2"
    original = nsight_reports.write_json

    def interrupted(parent, name, value):
        original(parent, name, value)
        if value.get("inactive"):
            raise OSError("fixture interruption after inactive marker")

    monkeypatch.setattr(nsight_reports, "write_json", interrupted)
    with pytest.raises(OSError):
        nsight_reports.reconcile(request)
    assert descriptor.exists() and json.loads(marker.read_text())["inactive"]
    monkeypatch.setattr(nsight_reports, "write_json", original)
    nsight_reports.reconcile(request)
    assert descriptor.read_bytes() == marker.read_bytes()
    assert json.loads(descriptor.read_text())["installation"]["generation"] == "generation-2"


@pytest.mark.parametrize("boundary", ["marker", "descriptor", "admission"])
def test_transition_recovers_after_each_publication_boundary(installed, monkeypatch, boundary):
    request, descriptor, marker = installed
    nsight_reports.reconcile(request)
    request["installation"]["generation"] = "generation-2"
    original = nsight_reports.write_json

    def interrupted(parent, name, value):
        original(parent, name, value)
        trigger = (
            boundary == "marker"
            and name == "binding.json"
            and value.get("installation", {}).get("generation") == "generation-2"
            or boundary == "descriptor"
            and name == descriptor.name
            or boundary == "admission"
            and name == "admission.json"
            and value["open"] is True
        )
        if trigger:
            raise OSError("fixture interrupted transition")

    monkeypatch.setattr(nsight_reports, "write_json", interrupted)
    with pytest.raises(OSError):
        nsight_reports.reconcile(request)
    monkeypatch.setattr(nsight_reports, "write_json", original)
    nsight_reports.reconcile(request)
    assert descriptor.read_bytes() == marker.read_bytes()
    assert json.loads(marker.read_text())["installation"]["generation"] == "generation-2"


def test_actual_course_reader_observes_installer_admission_boundary(installed, monkeypatch):
    request, descriptor, marker = installed
    nsight_reports.reconcile(request)
    source = Path(__file__).resolve().parents[3] / "courses/tools/course_viewer.py"
    reader = ModuleType("fixture_course_viewer")
    exec(
        compile(
            source.read_text().replace("ROOT_UID = 0", f"ROOT_UID = {os.getuid()}"),
            str(source),
            "exec",
        ),
        reader.__dict__,
    )
    reader.DESCRIPTOR = str(descriptor)
    reader.mount_identity = nsight_reports.mount_identity
    admitted, fd = reader.binding()
    os.close(fd)
    request["installation"]["generation"] = "generation-2"

    def concurrent_admission(*args, **kwargs):
        with pytest.raises(ValueError, match="admission is closed"):
            reader.binding()
        still_admitted, fd = reader.binding(admitted)
        os.close(fd)
        assert still_admitted == admitted
        return SimpleNamespace(stdout="123 RUNNING")

    monkeypatch.setattr(nsight_reports.subprocess, "run", concurrent_admission)
    with pytest.raises(ValueError, match="quiescent"):
        nsight_reports.reconcile(request)
    current, fd = reader.binding()
    os.close(fd)
    assert current == admitted


def test_descriptor_is_published_last_and_retry_recovers(installed, monkeypatch):
    request, descriptor, marker = installed
    original = nsight_reports.write_json

    def interrupted(parent, name, value):
        if name == descriptor.name:
            raise OSError("fixture interruption")
        return original(parent, name, value)

    monkeypatch.setattr(nsight_reports, "write_json", interrupted)
    with pytest.raises(OSError):
        nsight_reports.reconcile(request)
    assert marker.exists() and not descriptor.exists()
    token = json.loads(marker.read_text())["token"]
    monkeypatch.setattr(nsight_reports, "write_json", original)
    assert nsight_reports.reconcile(request)["token"] == token


@pytest.mark.parametrize(
    "fault", ["wrong-mount", "package-drift", "marker-symlink", "foreign-marker", "token-mismatch"]
)
def test_reconciliation_rejects_unproven_bindings(installed, monkeypatch, fault):
    request, descriptor, marker = installed
    nsight_reports.reconcile(request)
    before = descriptor.read_bytes()
    if fault == "wrong-mount":
        monkeypatch.setattr(
            nsight_reports,
            "mount_identity",
            lambda fd: {"id": "root", "device": "root", "point": "/", "type": "fixture"},
        )
    elif fault == "package-drift":
        request["verification"] = {"changed": True}
    elif fault == "marker-symlink":
        marker.unlink()
        marker.symlink_to(descriptor)
    else:
        value = json.loads(marker.read_text())
        if fault == "foreign-marker":
            value = {"schema": nsight_reports.SCHEMA}
        else:
            value["token"] = "9" * 64
        marker.write_text(json.dumps(value))
    with pytest.raises((ValueError, OSError)):
        nsight_reports.reconcile(request)
    assert descriptor.read_bytes() == before


def test_install_reobserves_both_viewers_and_fences_remote_contract(monkeypatch):
    config = profiling_config()
    binding = soperator_report_binding(jail_values(config, "cluster"), namespace="soperator")
    storage = {
        "namespace": binding.namespace,
        "pvcName": binding.claim,
        "pvcUid": "pvc-uid",
        "pvName": "reports-pv",
        "pvUid": "pv-uid",
    }
    events, requests = [], []
    monkeypatch.setattr(
        nsight_report_destination,
        "prepare_nsight_viewers",
        lambda *a, **kw: events.append("storage") or (storage, dict(storage)),
    )

    class Kube:
        def __init__(self, env):
            pass

        def run(self, argv, **kw):
            events.append("remote")
            requests.append(json.loads(base64.b64decode(argv[-1])))
            assert "nsight_reports" not in argv  # self-contained source, no installed import
            return json.dumps(
                {"schema": nsight_reports.SCHEMA, "status": "ready", "token": "1" * 64}
            )

    monkeypatch.setattr(nsight_report_destination, "NsightKubernetes", Kube)
    kwargs = {
        "env": {},
        "config": config,
        "target_ref": "cluster",
        "generation": "accepted",
        "identity": {"kubernetes_uid": "cluster-uid"},
        "receipt": {"verification": {"accepted": True}},
        "fence": lambda: events.append("fence"),
    }
    nsight_report_destination.reconcile_report_destination(**kwargs)
    assert events == ["fence", "storage", "fence", "remote", "fence"]
    assert requests[0]["mount_point"] == "/data"
    assert requests[0]["storage"] == storage
    changed = copy.deepcopy(storage)
    changed["pvUid"] = "replacement"
    monkeypatch.setattr(
        nsight_report_destination, "prepare_nsight_viewers", lambda *a, **kw: (storage, changed)
    )
    with pytest.raises(RuntimeError, match="share one"):
        nsight_report_destination.reconcile_report_destination(**kwargs)
    assert len(requests) == 1
