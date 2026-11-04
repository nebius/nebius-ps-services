from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import traceback
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from nebius_cxcli import nat_gateway_probe_guest as guest
from nebius_cxcli import nat_gateway_probe_resources as resource_module
from nebius_cxcli import nat_gateway_probe_ssh as transport
from nebius_cxcli.nat_gateway_probe_resources import ProbeResources
from nebius_cxcli.nat_gateway_probe_result import accepted_report
from nebius_cxcli.nat_gateway_probes import cloud_init
from nebius_cxcli.ssh_host_identity import generate_host_identity
from test_nat_gateway_probes import CLIENT_PUBLIC_KEY, evidence, probe_fixture


@pytest.fixture
def access(tmp_path):
    root = tmp_path / "space and %percent"
    root.mkdir()
    pair = generate_host_identity()
    key = root / "client.key"
    key.write_text(pair["ed25519_private"])
    key.chmod(0o600)
    known = root / "known_hosts"
    known.write_text("203.0.113.9 " + pair["ed25519_public"] + "\n")
    with transport.ProbeSSH(
        {"public_ip": "203.0.113.9/32"},
        {"ssh_user_name": "ubuntu", "ssh_public_key": pair["ed25519_public"]},
        known_hosts=known,
        private_key=key,
    ) as ssh:
        yield ssh


@pytest.mark.parametrize(
    "forwarding,disabled,permit,ok",
    [
        ("yes", "no", "any", True),
        ("local", "no", "any", True),
        ("no", "no", "any", False),
        ("remote", "no", "any", False),
        ("yes", "yes", "any", False),
        ("yes", "no", "none", False),
    ],
)
def test_forwarding_preflight_checks_effective_policy(
    access, monkeypatch, forwarding, disabled, permit, ok
):
    def capture(args, **kwargs):
        assert args[3] == "nat-gateway" and "SSH_CONNECTION" in args[4]
        assert kwargs["timeout"] == 30
        return 0, json.dumps(
            {"allowtcpforwarding": forwarding, "disableforwarding": disabled, "permitopen": permit}
        ).encode()

    monkeypatch.setattr(transport, "capture", capture)
    if ok:
        access.preflight(assert_held=lambda: None)
    else:
        with pytest.raises(RuntimeError, match="before route activation"):
            access.preflight(assert_held=lambda: None)


@pytest.mark.integration
def test_real_openssh_configuration_pins_both_hops_and_selects_one_client(access, monkeypatch):
    probe = probe_fixture()
    probe["host_public_key"] = CLIENT_PUBLIC_KEY
    reads = []

    def address(**kwargs):
        reads.append(True)
        return "10.0.0.8"

    monkeypatch.setattr(transport, "capture", lambda *a, **kw: (0, evidence(probe)))
    assert access.wait(
        NS(private_address=address), probe, save=lambda: None, assert_held=lambda: None
    )["ok"]
    assert len(reads) == 2
    for host in ("nat-gateway", "nat-probe"):
        result = subprocess.run(
            ["ssh", "-G", "-F", str(access.config), host],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        settings = dict(line.split(" ", 1) for line in result.stdout.splitlines())
        assert settings["stricthostkeychecking"] == "true"
        assert settings["forwardagent"] == "no" and settings["identitiesonly"] == "yes"
        assert settings["identityagent"] == "none"
        assert settings["globalknownhostsfile"] == "/dev/null"
        assert settings["batchmode"] == "yes"
        if host == "nat-probe":
            assert settings["proxyjump"] == "nat-gateway"
            assert settings["hostname"] == "10.0.0.8"
            assert settings["hostkeyalias"] == "cxcli-probe-" + probe["config"]["run_id"]
        else:
            assert settings["hostkeyalias"] == "203.0.113.9"
            assert "proxyjump" not in settings
    assert access.config.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "change",
    [
        {"activation": "other"},
        {"run_id": "other"},
        {"config_sha256": "other"},
        {"program_sha256": "other"},
        {"endpoint_ip": "8.8.8.8"},
        {"boot_id": "invalid"},
        {"finished_at": 0},
        {"ok": "true"},
        {"observed_ip": "203.0.113.10"},
    ],
)
def test_result_cannot_claim_success_for_wrong_run_or_path(change):
    probe = probe_fixture()
    report = json.loads(evidence(probe)) | change
    with pytest.raises(RuntimeError):
        accepted_report(json.dumps(report).encode(), probe)


@pytest.mark.parametrize("raw", [b"", b"[]", b"null", b"not json", b"\xff", b"x" * 4097])
def test_invalid_result_is_not_success(raw):
    with pytest.raises(RuntimeError):
        accepted_report(raw, probe_fixture())


def test_trusted_negative_result_remains_a_failed_measurement():
    probe = probe_fixture()
    report = json.loads(evidence(probe)) | {"observed_ip": "203.0.113.10", "ok": False}
    assert accepted_report(json.dumps(report).encode(), probe)["ok"] is False


def test_probe_access_and_result_permissions_require_no_manual_setup(tmp_path):
    pair = generate_host_identity()
    data = yaml.safe_load(
        cloud_init(probe_fixture()["config"], public_key=CLIENT_PUBLIC_KEY, host_identity=pair)
    )
    assert data["ssh_keys"]["ed25519_public"] == pair["ed25519_public"]
    user = data["users"][0]
    assert user["name"] == transport.PROBE_USER and user["lock_passwd"] is True
    assert user["sudo"] is None and user["shell"] == "/bin/sh"
    assert user["ssh_authorized_keys"] == ["restrict " + CLIENT_PUBLIC_KEY]
    assert data["ssh_pwauth"] is False and data["disable_root"] is True
    path = tmp_path / "root-owned" / "result.json"
    guest.write_result({"ok": True}, path)
    assert json.loads(path.read_bytes()) == {"ok": True}
    assert path.stat().st_mode & 0o777 == 0o644
    assert path.parent.stat().st_mode & 0o777 == 0o755
    assert list(path.parent.iterdir()) == [path]
    assert "/dev/ttyS0" not in Path(guest.__file__).read_text()


def test_capture_returns_bounded_output_and_discards_diagnostics(tmp_path):
    code, raw = transport.capture(
        [sys.executable, "-c", "import sys; print('ok'); print('sensitive',file=sys.stderr)"],
        cwd=tmp_path,
        timeout=5,
        assert_held=lambda: None,
    )
    assert code == 0 and raw == b"ok\n"


@pytest.mark.parametrize("failure", ["timeout", "oversize", "cancel"])
def test_capture_stops_jump_descendants_before_returning(tmp_path, failure):
    marker = tmp_path / "child.pid"
    child = "import time; time.sleep(120)"
    script = (
        "import subprocess,sys,time,pathlib; "
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); "
        + ("print('x'*4097,flush=True); " if failure == "oversize" else "")
        + "time.sleep(120)"
    )

    def held():
        if failure == "cancel" and marker.exists():
            raise KeyboardInterrupt

    expected = (
        KeyboardInterrupt
        if failure == "cancel"
        else (TimeoutError if failure == "timeout" else RuntimeError)
    )
    try:
        with pytest.raises(expected):
            transport.capture(
                [sys.executable, "-c", script],
                cwd=tmp_path,
                timeout=1 if failure == "timeout" else 5,
                assert_held=held,
            )
        assert marker.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
    finally:
        if marker.exists():
            with suppress(ProcessLookupError):
                os.kill(int(marker.read_text()), signal.SIGKILL)


def test_inconclusive_ssh_retries_are_bounded(access, monkeypatch):
    probe = probe_fixture()
    probe["host_public_key"] = CLIENT_PUBLIC_KEY
    monkeypatch.setattr(transport, "RESULT_TIMEOUT", 0.02)

    def unavailable(*args, **kwargs):
        time.sleep(0.03)
        return 255, b""

    monkeypatch.setattr(transport, "capture", unavailable)
    with pytest.raises(RuntimeError, match="no trusted SSH result"):
        access.wait(
            NS(private_address=lambda **kw: "10.0.0.8"),
            probe,
            save=lambda: None,
            assert_held=lambda: None,
        )


def test_unresolved_ssh_shutdown_blocks_resource_cleanup(access, monkeypatch):
    probe = probe_fixture()
    probe["host_public_key"] = CLIENT_PUBLIC_KEY

    def unresolved(*args, **kwargs):
        raise transport.ProcessUnresolved("shutdown unresolved")

    monkeypatch.setattr(transport, "capture", unresolved)
    saved = []
    with pytest.raises(transport.ProcessUnresolved):
        access.wait(
            NS(private_address=lambda **kw: "10.0.0.8"),
            probe,
            save=lambda: saved.append(True),
            assert_held=lambda: None,
        )
    assert probe["ssh_cleanup_pending"] is True and saved
    with pytest.raises(RuntimeError, match="shutdown is unresolved"):
        ProbeResources(NS(), probe, lambda: None, lambda: None).cleanup()


def test_unconfirmed_reconciliation_preserves_unresolved_ssh():
    probe = probe_fixture()
    probe.update(ssh_cleanup_pending=True, resources={})
    resources = ProbeResources(NS(), probe, lambda: None, lambda: None)
    with pytest.raises(RuntimeError, match="SSH collectors"):
        resources.reconcile()
    assert probe["ssh_cleanup_pending"] is True
    with pytest.raises(RuntimeError, match="shutdown is unresolved"):
        resources.cleanup()
    resources.reconcile(ssh_collectors_stopped=True)
    resources.cleanup()
    assert probe["ssh_cleanup_pending"] is False and probe["cleanup"] == "complete"


def test_sdk_error_cannot_leak_cloud_init_key(monkeypatch):
    probe = probe_fixture()
    probe["resources"] = {}
    resources = ProbeResources(NS(project_id="project"), probe, lambda: None, lambda: None)
    sentinel = "TEST-ONLY-PRIVATE-KEY-SENTINEL"

    def fail(*args, **kwargs):
        raise ValueError(sentinel)

    monkeypatch.setattr(
        resources, "api", lambda _: (NS(CreateInstanceRequest=object), NS(create=fail))
    )
    monkeypatch.setattr(resource_module, "sdk_parse_message", lambda *a: object())
    with pytest.raises(RuntimeError) as error:
        resources.create("instance", {"cloud_init_user_data": sentinel})
    rendered = "".join(traceback.format_exception(error.value))
    assert sentinel not in rendered and sentinel not in json.dumps(probe)
    assert probe["resources"]["instance"]["phase"] == "submitted"


@pytest.mark.parametrize("remaining", [False, True])
def test_fixture_cleanup_attempts_all_resources_and_checks_absence(monkeypatch, remaining):
    from test_nat_gateway_probe_ssh_integration import cleanup_fixture

    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "stop":
            raise subprocess.TimeoutExpired(command, 30)
        if command[1] == "rm":
            return NS(returncode=1)
        if "ls" in command:
            return NS(returncode=0, stdout="retained" if remaining else "")
        return NS(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    errors = cleanup_fixture("fixture-exact", "fixture-exact:fixture")
    assert len(commands) == 5
    assert commands[2] == ["docker", "image", "rm", "fixture-exact:fixture"]
    assert bool(errors) is remaining


@pytest.mark.parametrize(
    "drift", [None, "instance", "subnet", "public", "disk", "service_account", "address", "stopped"]
)
def test_sdk_readback_binds_private_destination_to_owned_instance(monkeypatch, drift):
    from nebius.api.nebius.compute.v1 import Disk, Instance
    from nebius.api.nebius.vpc.v1 import Allocation

    from nebius_cxcli.nebius_api_helpers import sdk_parse_message

    probe = probe_fixture()
    for kind in ("instance", "disk", "allocation"):
        probe["resources"][kind] = {"id": kind + "-exact", "name": kind + "-name"}
    specs = {
        "instance": {
            "boot_disk": {"existing_disk": {"id": "disk-exact"}},
            "network_interfaces": [
                {
                    "name": "eth0",
                    "subnet_id": probe["subnet_id"],
                    "ip_address": {"allocation_id": "allocation-exact"},
                }
            ],
        },
        "disk": {"source_image_id": probe["shape"]["image"], "size_gibibytes": 20},
        "allocation": {"ipv4_private": {"subnet_id": probe["subnet_id"]}},
    }
    statuses = {
        "instance": {"state": "RUNNING"},
        "allocation": {
            "details": {"allocated_cidr": "10.0.0.8/32", "subnet_id": probe["subnet_id"]},
            "assignment": {"network_interface": {"instance_id": "instance-exact"}},
        },
        "disk": {},
    }
    if drift == "instance":
        statuses["allocation"]["assignment"]["network_interface"]["instance_id"] = "other"
    elif drift == "subnet":
        specs["instance"]["network_interfaces"][0]["subnet_id"] = "other"
    elif drift == "public":
        specs["instance"]["network_interfaces"][0]["public_ip_address"] = {
            "allocation_id": "public"
        }
    elif drift == "disk":
        specs["instance"]["boot_disk"]["existing_disk"]["id"] = "other"
    elif drift == "service_account":
        specs["instance"]["service_account_id"] = "account"
    elif drift == "address":
        statuses["allocation"]["details"]["allocated_cidr"] = "10.0.0.0/24"
    elif drift == "stopped":
        statuses["instance"]["state"] = "STOPPED"
    classes = {"instance": Instance, "disk": Disk, "allocation": Allocation}
    objects = {
        kind: sdk_parse_message(
            classes[kind],
            {
                "metadata": {
                    "id": entry["id"],
                    "name": entry["name"],
                    "parent_id": "project-test",
                    "labels": {"cxcli-nat-probe": probe["config"]["run_id"]},
                },
                "spec": specs[kind],
                "status": statuses[kind],
            },
        )
        for kind, entry in probe["resources"].items()
    }
    resources = ProbeResources(NS(project_id="project-test"), probe, lambda: None, lambda: None)
    monkeypatch.setattr(resources, "read", lambda kind, entry, **kw: objects[kind])
    if drift:
        with pytest.raises(RuntimeError, match="relationships|not bound"):
            resources.private_address(deadline=time.monotonic() + 30)
    else:
        assert resources.private_address(deadline=time.monotonic() + 30) == "10.0.0.8"
