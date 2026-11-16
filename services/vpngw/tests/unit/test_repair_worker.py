import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from nebius_vpngw import package_worker as worker
from nebius_vpngw import repair_bootstrap, repair_state
from nebius_vpngw.deploy import repair_remote


def test_repair_bundle_imports_without_site_packages(tmp_path):
    script = tmp_path / "bootstrap.py"
    script.write_text(
        repair_bootstrap.source()
        + "assert not any(x.startswith('nebius_vpngw') for x in sys.modules)\nassert _vpngw_worker.BACKEND=='repair'\n"
        + "assert sys.modules['_vpngw_prior_package_worker'].BACKEND=='handoff'\n"
        + "assert sys.modules['_vpngw_prior_package_worker'].ROOT != _vpngw_worker.ROOT\n"
    )
    result = subprocess.run([sys.executable, "-I", "-S", str(script)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    assert b"nebius-vpngw-repair" in script.read_bytes() or repair_bootstrap.source_frame()


@pytest.fixture
def repair_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "BACKEND", "repair")
    monkeypatch.setattr(worker, "ROOT", tmp_path / "workers")
    monkeypatch.setattr(worker, "RUN", tmp_path / "run")
    monkeypatch.setattr(worker, "BOOT", tmp_path / "boot")
    worker.BOOT.write_text("boot-a")
    request = {"action": "repair-install", "operation": "a" * 32, "intent": "b" * 64, "package": {}}
    key = worker.identity(request)
    worker.directory(worker.ROOT / key)
    worker.publish(worker.ROOT / key / "request.json", request)
    worker.publish(
        worker.ROOT / key / "transport.json",
        {
            "schema": worker.SCHEMA,
            "identity": key,
            "boot": "boot-a",
            "source": "c" * 64,
            "generation": 1,
            "unit": worker.unit(key, 1),
            "requests": {},
            "terminal": None,
            "waiting": False,
        },
    )
    return key, request


def test_repair_worker_settlement_does_not_parse_retired_product_journals(
    repair_worker, monkeypatch
):
    key, request = repair_worker
    admitted = []
    monkeypatch.setitem(
        sys.modules, "_vpngw_repair_remote", SimpleNamespace(recovery_admission=admitted.append)
    )
    monkeypatch.setattr(worker, "unit_running", lambda _: False)
    result = worker.status(key)
    assert result["state"] == "READY_TO_CONTINUE"
    assert admitted == [request]


def test_repair_receipt_waits_for_entire_worker_cgroup(repair_worker, monkeypatch):
    key, request = repair_worker
    path, value = worker.record(key)
    value["requests"][worker.digest(request)] = {
        "request": request,
        "state": "complete",
        "result": {"phase": "installed"},
    }
    worker.publish(path, value)
    monkeypatch.setattr(worker, "unit_running", lambda _: True)
    assert worker.status(key)["state"] == "RUNNING_OWNED"
    monkeypatch.setattr(worker, "unit_running", lambda _: False)
    assert worker.status(key)["state"] == "PREPARED"


def test_repair_backend_does_not_accept_normal_package_authority(repair_worker):
    key, _request = repair_worker
    assert worker.unit(key, 1).startswith("nebius-vpngw-repair-package-")
    with pytest.raises(ValueError, match="action is invalid"):
        worker.identity({"action": "prepare-guarded"})


@pytest.mark.parametrize(
    "phase,rendered",
    [
        ("installed", set()),
        ("enrolled", {"/etc/frr/frr.conf"}),
        ("prepared", {"/etc/frr/frr.conf"}),
    ],
)
def test_package_recheck_after_interrupted_render_keeps_binary_proof(
    monkeypatch, tmp_path, phase, rendered
):
    request = {"package": {"native": {"archives": {}}, "wheels": {}}}
    inventory = {
        "archives": repair_state.digest({}),
        "conffiles": ["/etc/frr/frr.conf", "/etc/unrelated.conf"],
    }
    repair_state.publish(tmp_path / "native-inventory.json", inventory)
    monkeypatch.setattr(
        repair_remote,
        "admitted",
        lambda *_a, **_k: (None, {"id": "a" * 32, "phase": phase}, tmp_path),
    )
    monkeypatch.setattr(repair_remote.guest, "verify_guard", lambda _: None)
    monkeypatch.setattr(repair_remote, "verify_assets", lambda *_args: None)
    monkeypatch.setattr(repair_remote.runtime.migration, "verify_packages", lambda _: None)
    monkeypatch.setattr(repair_remote.guest, "BOOT", tmp_path / "boot")
    (tmp_path / "boot").write_text("boot-a")
    checked = []
    monkeypatch.setattr(
        repair_remote.runtime,
        "verify_inventory",
        lambda value, **kwargs: checked.append((value, kwargs)),
    )
    repair_remote.verify_installation(request)
    assert checked == [(inventory, {"rendered": rendered})]


def test_new_confirmed_repair_retires_only_completed_repair_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(repair_remote.guest, "ROOT", tmp_path / "repair")
    intent = {
        "project": "fixture-project",
        "gateway": "fixture-gateway",
        "configuration": "a" * 64,
        "artifact": "b" * 64,
        "ha": False,
        "targets": [
            {
                "name": "fixture",
                "compute": "vm-a",
                "disk": "disk-a",
                "transport": "192.0.2.1",
                "binding": "c" * 64,
            }
        ],
    }
    first, second = "d" * 32, "e" * 32
    repair_remote.admit({"operation": first, "intent": intent})
    repair_state.publish(
        repair_remote.guest.ROOT / "package-policy.json", {"operation": first, "original": None}
    )
    with pytest.raises(RuntimeError, match="must settle"):
        repair_remote.admit({"operation": second, "intent": intent})
    journal = repair_state.Journal(repair_remote.guest.ROOT)
    for phase in repair_state.PHASES[1:]:
        journal.advance(phase)
    repair_remote.admit({"operation": second, "intent": intent})
    assert not (repair_remote.guest.ROOT / "package-policy.json").exists()
    old = repair_remote.guest.ROOT / "archives" / first / "repair-package-policy.json"
    assert json.loads(old.read_bytes())["operation"] == first
    assert journal.load()["id"] == second
