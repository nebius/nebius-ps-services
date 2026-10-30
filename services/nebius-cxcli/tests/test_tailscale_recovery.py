"""Interruption oracles exercise the real lifecycle and guest protocol without live effects."""

from __future__ import annotations

import copy
import json
import os
import subprocess
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import tailscale_enrollment as enrollment
from nebius_cxcli import tailscale_retirement as retirement
from nebius_cxcli.tailscale_api import ApiError, TailscaleAPI
from nebius_cxcli.tailscale_config import desired
from test_tailscale import guest as guest  # noqa: F401
from test_tailscale import healthy, row


class SavedJournal:
    def __init__(self):
        self.value = {"schema": 3, "gateways": {}}
        self.saved = None
        self.fail = None
        self.held = True

    def assert_held(self):
        if not self.held:
            raise RuntimeError("stale writer")

    def save(self):
        self.assert_held()
        if self.fail:
            self.fail()
        self.saved = copy.deepcopy(self.value)


def test_old_member_retirement_summary_cannot_skip_current_key(enrollment_case):
    c = enrollment_case
    c.own["key_retired"] = True  # Summary retained from a replaced VM's operation.
    c.remote.fault = lambda operation, kwargs: (
        (_ for _ in ()).throw(RuntimeError("before delivery")) if operation == "reconcile" else None
    )
    with pytest.raises(RuntimeError, match="before delivery"):
        c.resume()
    enrollment.revoke(c.journal, c.own, c.api, c.store)
    assert c.deleted == ["key-id"]
    assert c.own["enrollment"]["key_retired"]


@pytest.fixture
def enrollment_case(tmp_path, monkeypatch, guest):
    value = row()
    own = {
        "project_id": "project-test",
        "instance_id": "vm-a",
        "boot_disk_id": "disk-a",
        "public_allocation_id": "ip-a",
        "network_id": "network-test",
        "subnet_id": "subnet-test",
        "host_public_key": "ssh-ed25519 fixture",
        "bootstrap_sha256": "bootstrap",
        "member": "a",
    }
    member = tmp_path / "member.json"
    host = tmp_path / "host.pub"
    guest.write(member, {"project": "project-test", "member": "a"})
    host.write_text(own["host_public_key"])
    monkeypatch.setattr(guest, "MEMBER", member)
    monkeypatch.setattr(guest, "HOST_PUBLIC", host)
    state = {"node_id": "", "backend": "NeedsLogin"}
    monkeypatch.setattr(guest, "observation", lambda: copy.deepcopy(state))
    calls, issued, deleted = [], [], []

    def run(args, **_):
        calls.append(args)
        if "up" in args:
            assert any(arg.startswith("--auth-key=file:") for arg in args)
            assert all("tskey-auth-" not in arg for arg in args)
            state.update(healthy(), backend="Running")
        return ""

    monkeypatch.setattr(guest, "run", run)

    class Remote:
        fault = None

        def call(self, operation, **kwargs):
            if self.fault:
                self.fault(operation, kwargs)
            if operation == "status":
                return guest.observation()
            if operation == "enrollment-status":
                return guest.enrollment_status(kwargs)
            if operation == "enrollment-fence-unused":
                return guest.enrollment_status(kwargs, unused=True)
            if operation == "enrollment-fence":
                return guest.enrollment_status(kwargs, fence=True)
            if operation == "enrollment-forget-key":
                return guest.forget_key(kwargs)
            if operation == "retire":
                guest.write(guest.STATE / "retired.json", {"schema": 1})
                return guest.observation()
            assert operation == "reconcile"
            return guest.reconcile(kwargs)

        def install(self, _):
            pass

    remote = Remote()
    api = NS(
        credential_binding=lambda: {
            "source": "reference",
            "client_id": "test",
            "client_secret_ref": "env:TEST_TAILSCALE_SECRET",
        },
        bind_credential=lambda pin: None,
        key=lambda op: issued.append(op) or ("key-id", "tskey-auth-original"),
        device=lambda node: {"nodeId": node, "tags": value["tailscale"]["tags"]},
        revoke_key=lambda key: deleted.append(key),
    )
    journal = SavedJournal()
    journal.value["gateways"] = {
        "ts": {"tailnet": value["tailscale"]["tailnet"], "members": {"a": own}}
    }
    store = enrollment.KeyStore(tmp_path)
    case = NS(
        row=value,
        own=own,
        guest=guest,
        remote=remote,
        state=state,
        calls=calls,
        api=api,
        issued=issued,
        deleted=deleted,
        journal=journal,
        store=store,
    )
    case.resume = lambda: enrollment.resume(
        journal, own, remote, value["tailscale"], desired(value, "a"), api, store
    )
    return case


def test_interrupt_before_delivery_reuses_original_key(enrollment_case):
    c = enrollment_case

    def before(operation, _):
        if operation == "reconcile":
            raise RuntimeError("before delivery")

    c.remote.fault = before
    with pytest.raises(RuntimeError, match="before delivery"):
        c.resume()
    op = copy.deepcopy(c.own["enrollment"])
    assert c.store.read(op)["key"] == "tskey-auth-original"
    path = c.store.base / ".nebius-cxcli/tailscale-enrollment" / (op["id"] + ".json")
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert "tskey-auth-" not in json.dumps(c.journal.saved)
    c.remote.fault = None
    assert c.resume()["node_id"] == "node-a"
    assert c.issued == [op["id"]]
    assert c.store.read(op) is None
    assert not list(c.guest.STATE.glob("*.key"))
    c.resume()
    assert sum("up" in args for args in c.calls) == 1


@pytest.mark.parametrize("change", ["routes", "stage"])
def test_recovery_requires_current_approved_activation(enrollment_case, monkeypatch, change):
    from nebius_cxcli import tailscale_cli

    c = enrollment_case
    c.remote.fault = lambda operation, kwargs: (
        (_ for _ in ()).throw(RuntimeError("before delivery")) if operation == "reconcile" else None
    )
    with pytest.raises(RuntimeError, match="before delivery"):
        c.resume()
    assert not c.state.get("routes")
    if change == "routes":
        c.row["tailscale"]["routes"] = ["10.21.0.0/24"]
        c.row["tailscale"]["policy"]["rules"][0]["destinations"] = ["10.21.0.0/24"]
    else:
        c.row["tailscale"]["stage"] = "infrastructure"
    monkeypatch.setattr(tailscale_cli, "load", lambda *a: (NS(), {}, NS(), c.row))
    monkeypatch.setattr(
        "nebius_cxcli.terraform_backend.backend_settings_from_config",
        lambda *a: pytest.fail("recovery reached cloud boundary"),
    )
    with pytest.raises(ValueError, match="stale|active"):
        tailscale_cli.recover.__wrapped__(
            c.store.base / "config.yaml",
            member="a",
            operation_id=c.own["enrollment"]["id"],
            writer_stopped=True,
        )


def test_routes_approval_requires_current_handoff(tmp_path, monkeypatch):
    from nebius_cxcli import tailscale_cli

    value = row()
    value["tailscale"]["onboarding"]["purpose"] = "Changed intent"
    monkeypatch.setattr(tailscale_cli, "load", lambda *a: (NS(), {}, NS(), value))
    monkeypatch.setattr(
        "nebius_cxcli.deployment_cli.deployment_execution",
        lambda **kw: pytest.fail("route mutation reached cloud boundary"),
    )
    with pytest.raises(ValueError, match="stale"):
        tailscale_cli.routes_approve.__wrapped__(tmp_path / "config.yaml")


@pytest.mark.parametrize("cleanup", ["lock", "unused-key"])
def test_stale_handoff_does_not_block_safe_recovery_cleanup(tmp_path, monkeypatch, cleanup):
    from nebius_cxcli import tailscale_cli

    value = row()
    value["tailscale"]["stage"] = "infrastructure"
    value["tailscale"]["onboarding"]["purpose"] = "Changed intent"
    monkeypatch.setattr(tailscale_cli, "load", lambda *a: (NS(), {}, NS(), value))

    def reached(config):
        raise RuntimeError("safe cleanup admitted")

    monkeypatch.setattr("nebius_cxcli.terraform_backend.backend_settings_from_config", reached)
    kwargs = (
        {"lock_owner": "stopped-owner"}
        if cleanup == "lock"
        else {
            "operation_id": "selected-operation",
            "member": "a",
            "replace_unused_key": True,
        }
    )
    with pytest.raises(RuntimeError, match="safe cleanup admitted"):
        tailscale_cli.recover.__wrapped__(tmp_path / "config.yaml", writer_stopped=True, **kwargs)


@pytest.mark.parametrize("boundary", ["started", "enrolled", "owner", "ack"])
def test_guest_interruption_recovers_same_node(enrollment_case, monkeypatch, boundary):
    c = enrollment_case
    original = c.guest.write
    interrupted = False

    def write(path, value):
        nonlocal interrupted
        original(path, value)
        if interrupted:
            return
        if (
            (
                boundary in {"started", "enrolled"}
                and isinstance(value, dict)
                and value.get("phase") == boundary
            )
            or boundary == "owner"
            and path.name == "owner.json"
        ):
            interrupted = True
            raise RuntimeError("interrupted")

    monkeypatch.setattr(c.guest, "write", write)
    real_call = c.remote.call

    def call(operation, **kwargs):
        result = real_call(operation, **kwargs)
        if boundary == "ack" and operation == "reconcile" and not getattr(c, "lost", False):
            c.lost = True
            raise RuntimeError("interrupted")
        return result

    c.remote.call = call
    with pytest.raises(RuntimeError, match="interrupted"):
        c.resume()
    assert c.resume()["node_id"] == "node-a"
    assert len(c.issued) == 1
    assert sum("up" in args for args in c.calls) == 1
    assert c.own["enrollment"]["phase"] == "enrolled"


def test_ambiguous_key_creation_never_retries(enrollment_case):
    c = enrollment_case

    def issue(op):
        c.issued.append(op)
        raise RuntimeError("lost key response")

    c.api.key = issue
    with pytest.raises(RuntimeError, match="lost key response"):
        c.resume()
    with pytest.raises(RuntimeError, match="creation outcome is uncertain"):
        c.resume()
    assert len(c.issued) == 1
    assert c.own["enrollment"]["phase"] == "issuing"
    assert not c.calls


def test_local_key_receipt_survives_lost_journal_write(enrollment_case):
    c = enrollment_case

    def lost():
        if c.own.get("enrollment", {}).get("phase") == "key-ready":
            raise RuntimeError("journal unavailable")

    c.journal.fail = lost
    with pytest.raises(RuntimeError, match="journal unavailable"):
        c.resume()
    c.own["enrollment"]["phase"] = "issuing"  # Reload the last successfully saved public journal.
    c.journal.fail = None
    c.resume()
    assert len(c.issued) == 1


@pytest.mark.parametrize(
    "fault", ["foreign-node", "missing-receipt", "changed-binding", "changed-tags"]
)
def test_foreign_enrollment_cannot_be_adopted(enrollment_case, fault):
    c = enrollment_case
    c.remote.fault = lambda operation, _: (
        (_ for _ in ()).throw(RuntimeError("pause")) if operation == "reconcile" else None
    )
    with pytest.raises(RuntimeError, match="pause"):
        c.resume()
    c.remote.fault = None
    if fault in {"foreign-node", "missing-receipt"}:
        c.state.update(healthy(), backend="Running")
        if fault == "foreign-node":
            c.state["node_id"] = "foreign"
    elif fault == "changed-binding":
        c.own["boot_disk_id"] = "foreign-disk"
    else:
        c.row["tailscale"]["tags"] = ["tag:foreign"]
    with pytest.raises(RuntimeError, match="receipt|binding"):
        c.resume()
    assert len(c.issued) == 1
    assert not c.calls


def test_stale_writer_cannot_issue_or_deliver(enrollment_case):
    c = enrollment_case
    c.journal.held = False
    with pytest.raises(RuntimeError, match="stale writer"):
        c.resume()
    assert not c.issued
    assert not c.calls


def test_unused_key_replacement_fences_old_guest_request(enrollment_case):
    c = enrollment_case
    c.remote.fault = lambda operation, _: (
        (_ for _ in ()).throw(RuntimeError("pause")) if operation == "reconcile" else None
    )
    with pytest.raises(RuntimeError, match="pause"):
        c.resume()
    c.remote.fault = None
    old = copy.deepcopy(c.own["enrollment"])
    enrollment.recover_unused(c.journal, c.own, c.remote, c.api, c.store)
    assert c.deleted == ["key-id"]
    with pytest.raises(RuntimeError, match="fenced"):
        c.guest.reconcile(
            {
                "desired": desired(c.row, "a"),
                "enrollment": enrollment.contract(old),
                "auth_key": "tskey-auth-original",
            }
        )
    c.resume()
    assert len(c.issued) == 2
    assert c.own["enrollment"]["id"] != old["id"]


def test_started_unknown_enrollment_cannot_replace_key(enrollment_case, monkeypatch):
    c = enrollment_case
    monkeypatch.setattr(
        c.guest, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("expired key"))
    )
    with pytest.raises(RuntimeError, match="expired key"):
        c.resume()
    with pytest.raises(RuntimeError, match="already started"):
        enrollment.recover_unused(c.journal, c.own, c.remote, c.api, c.store)
    assert not c.deleted
    assert len(c.issued) == 1


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "permissions"])
def test_runtime_key_storage_rejects_unsafe_files(enrollment_case, unsafe):
    c = enrollment_case
    c.remote.fault = lambda operation, _: (
        (_ for _ in ()).throw(RuntimeError("pause")) if operation == "reconcile" else None
    )
    with pytest.raises(RuntimeError, match="pause"):
        c.resume()
    op = c.own["enrollment"]
    path = c.store.base / ".nebius-cxcli/tailscale-enrollment" / (op["id"] + ".json")
    if unsafe == "permissions":
        path.chmod(0o644)
    else:
        other = path.with_suffix(".original")
        path.rename(other)
        if unsafe == "symlink":
            path.symlink_to(other)
        else:
            os.link(other, path)
    with pytest.raises((RuntimeError, OSError)):
        c.store.read(op)


def test_guest_subprocess_errors_do_not_disclose_secrets(guest, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            args[0], 1, output="tskey-auth-sensitive", stderr="tskey-auth-sensitive"
        )

    monkeypatch.setattr(guest.subprocess, "run", fail)
    with pytest.raises(RuntimeError) as error:
        guest.run(["tailscale", "up"])
    assert "sensitive" not in str(error.value)
    assert error.value.__suppress_context__


def test_key_revocation_lost_ack_and_denial(tmp_path):
    api = TailscaleAPI(row()["tailscale"], base_dir=tmp_path)
    present = True

    def request(method, *_args, **_kwargs):
        nonlocal present
        if method == "DELETE":
            present = False
            raise RuntimeError("lost ack")
        if not present:
            raise ApiError(404)
        return {"id": "key-id"}, ""

    api.request = request
    api.revoke_key("key-id")
    api.revoke_key("key-id")
    api.request = lambda *_a, **_k: (_ for _ in ()).throw(ApiError(403))
    with pytest.raises(ApiError) as error:
        api.revoke_key("key-id")
    assert error.value.status == 403


def test_recovery_cannot_rebind_known_node_before_guest_rejection(enrollment_case):
    c = enrollment_case
    c.resume()
    c.state["node_id"] = "foreign-node"
    with pytest.raises(RuntimeError, match="identity|receipt"):
        c.resume()
    assert c.own["node_id"] == "node-a"
    assert c.journal.saved["gateways"]["ts"]["members"]["a"]["node_id"] == "node-a"


def test_retirement_retry_after_device_delete_accepts_api_absence(enrollment_case, monkeypatch):
    c = enrollment_case
    c.resume()
    c.own["device_retired"] = True
    c.api.device = lambda *_: (_ for _ in ()).throw(ApiError(404))
    removed = []
    c.api.delete_owned = lambda node, tags: removed.append(node)
    owner = NS(
        open=lambda: c.journal,
        api=lambda _: c.api,
        executor=NS(manifest={}, report_paths=NS(project_dir=c.store.base)),
    )
    monkeypatch.setattr(retirement, "guest_for", lambda *_: c.remote)
    monkeypatch.setattr(retirement, "asset", lambda *_: {})
    retirement.retire_members(owner, c.row, {"a": {}}, NS())
    assert removed == ["node-a"]
    assert c.own["key_retired"]


@pytest.fixture
def retirement_case(tmp_path, monkeypatch):
    value = row()
    value["inputs"]["replicas"] = 1
    journal = SavedJournal()
    own = {"host_public_key": "ssh-ed25519 fixture", "bootstrap_sha256": ""}
    journal.value["gateways"] = {
        "ts": {"tailnet": value["tailscale"]["tailnet"], "members": {"a": own}}
    }
    own.pop("bootstrap_sha256")
    live, state = {}, []
    expected = retirement.addresses("ts", value)
    ids = {"Instance": "vm-a", "Disk": "disk-a", "Allocation": "ip-a", "SecurityGroup": "sg-a"}
    for address, (kind, member) in expected.items():
        if kind == "SecurityRule":
            continue
        identity = ids[kind]
        obj = {
            "metadata": {
                "id": identity,
                "parent_id": "project-test",
                "labels": {
                    "cxcli-component": "tailscale-gw",
                    **({"cxcli-member": member} if member else {}),
                },
            },
            "spec": {},
        }
        if kind == "Instance":
            obj["spec"] = {
                "cloud_init_user_data": "immutable",
                "boot_disk": {"existing_disk": {"id": "disk-a"}},
                "network_interfaces": [
                    {"subnet_id": "subnet-test", "public_ip_address": {"allocation_id": "ip-a"}}
                ],
            }
            obj["status"] = {
                "network_interfaces": [
                    {"public_ip_address": {"allocation_id": "ip-a", "address": "192.0.2.1/32"}}
                ]
            }
        elif kind == "Allocation":
            obj["spec"] = {"ipv4_public": {"subnet_id": "subnet-test"}}
        elif kind == "SecurityGroup":
            obj["spec"] = {"network_id": "network-test"}
        live[identity] = obj
        state.append(
            {
                "address": address,
                "mode": "managed",
                "type": "nebius_compute_v1_instance" if kind == "Instance" else "fixture",
                "values": {
                    "id": identity,
                    **({"cloud_init_user_data": "immutable"} if kind == "Instance" else {}),
                },
            }
        )
    live["subnet-test"] = {
        "metadata": {"id": "subnet-test", "parent_id": "project-test"},
        "spec": {"network_id": "network-test"},
    }
    live["network-test"] = {
        "metadata": {"id": "network-test", "parent_id": "project-test"},
        "spec": {},
    }
    reads = []

    def read(_cloud, kind, identity):
        reads.append((kind, identity))
        return copy.deepcopy(live.get(identity))

    monkeypatch.setattr(retirement.Cloud, "read", read)
    monkeypatch.setattr(
        retirement,
        "recovered_identity",
        lambda _: {"ed25519_public": "ssh-ed25519 fixture", "ed25519_private": "private-fixture"},
    )
    monkeypatch.setattr(retirement, "asset", lambda *_: {"module_name": "ts"})
    calls = []
    remote = NS(
        install=lambda _: calls.append("install"),
        call=lambda op, **_: calls.append(op) or {"node_id": ""},
    )
    monkeypatch.setattr(retirement, "guest_for", lambda *_: remote)
    owner = NS(
        open=lambda: journal,
        api=lambda _: None,
        executor=NS(manifest={}, report_paths=NS(project_dir=tmp_path), runtime_env={}),
    )
    return NS(
        row=value,
        journal=journal,
        own=own,
        live=live,
        state=state,
        reads=reads,
        calls=calls,
        owner=owner,
        sdk=NS(project_id="project-test"),
    )


def test_never_activated_retirement_allows_approved_tailnet_change(retirement_case):
    c = retirement_case
    record = c.journal.value["gateways"]["ts"]
    record.update(tailnet="", activation_started=False, lifecycle="infrastructure-ready")
    c.owner.api = lambda _: pytest.fail("never-activated retirement read credentials")
    output = retirement.prepare(c.owner, c.row, c.state, c.sdk)
    retirement.retire_members(c.owner, c.row, output, c.sdk)
    assert record["lifecycle"] == "retiring"
    assert not c.calls


@pytest.mark.parametrize(
    "proof", [{}, {"activation_started": True}, {"activation_started": False, "node": True}]
)
def test_retirement_tailnet_change_requires_never_activated_proof(retirement_case, proof):
    c = retirement_case
    record = c.journal.value["gateways"]["ts"]
    record["tailnet"] = "other.ts.net"
    if "activation_started" in proof:
        record["activation_started"] = proof["activation_started"]
    if proof.get("node"):
        c.own["node_id"] = "node-a"
    with pytest.raises(RuntimeError, match="tailnet differs"):
        retirement.prepare(c.owner, c.row, c.state, c.sdk)


def test_retirement_reconstructs_missing_outputs_without_provisioning(retirement_case):
    c = retirement_case
    output = retirement.prepare(c.owner, c.row, c.state, c.sdk)
    assert c.own["boot_disk_id"] == "disk-a"
    assert c.own["public_allocation_id"] == "ip-a"
    assert output["a"]["instance_id"] == "vm-a"
    retirement.retire_members(c.owner, c.row, output, c.sdk)
    assert c.calls == ["install", "retire"]
    assert "private-fixture" not in json.dumps(c.journal.saved)
    assert c.journal.saved["gateways"]["ts"]["retiring"]


def test_disk_only_partial_creation_needs_no_ssh_or_enrollment(retirement_case):
    c = retirement_case
    c.state = [r for r in c.state if r["values"]["id"] == "disk-a"]
    c.live.pop("vm-a")
    output = retirement.prepare(c.owner, c.row, c.state, c.sdk)
    assert output == {}
    retirement.retire_members(c.owner, c.row, output, c.sdk)
    assert not c.calls
    assert c.own["vm_absent"]
    assert c.own["boot_disk_id"] == "disk-a"


@pytest.mark.parametrize("field", ["parent", "network", "disk", "allocation", "ssh", "bootstrap"])
def test_retirement_rejects_foreign_cloud_bindings(retirement_case, monkeypatch, field):
    c = retirement_case
    if field == "parent":
        c.live["disk-a"]["metadata"]["parent_id"] = "foreign"
    elif field == "network":
        c.live["subnet-test"]["spec"]["network_id"] = "foreign"
    elif field == "disk":
        c.live["vm-a"]["spec"]["boot_disk"]["existing_disk"]["id"] = "foreign"
    elif field == "allocation":
        c.live["vm-a"]["spec"]["network_interfaces"][0]["public_ip_address"]["allocation_id"] = (
            "foreign"
        )
    elif field == "ssh":
        c.own["host_public_key"] = "foreign"
    else:
        c.live["vm-a"]["spec"]["cloud_init_user_data"] = "changed"
    with pytest.raises(RuntimeError, match="mismatch|unresolved"):
        retirement.prepare(c.owner, c.row, c.state, c.sdk)
    assert c.journal.saved is None
    assert not c.calls


def test_interrupted_cloud_destruction_continues_and_checks_all_resources(retirement_case):
    c = retirement_case
    output = retirement.prepare(c.owner, c.row, c.state, c.sdk)
    retirement.retire_members(c.owner, c.row, output, c.sdk)
    c.live.pop("vm-a")
    c.state = [r for r in c.state if r["values"]["id"] != "vm-a"]
    output = retirement.prepare(c.owner, c.row, c.state, c.sdk)
    assert c.own["vm_absent"]
    retirement.retire_members(c.owner, c.row, output, c.sdk)
    with pytest.raises(RuntimeError, match="not independently confirmed"):
        retirement.confirm(c.owner, c.row, [], c.sdk)
    for identity in ("disk-a", "ip-a", "sg-a"):
        c.live.pop(identity)
    retirement.confirm(c.owner, c.row, [], c.sdk)
    assert c.journal.value["gateways"]["ts"]["retired"]
    assert {kind for kind, _ in c.reads} >= {"Instance", "Disk", "Allocation", "SecurityGroup"}


def test_unknown_unreachable_guest_blocks_destroy(retirement_case, monkeypatch):
    c = retirement_case
    output = retirement.prepare(c.owner, c.row, c.state, c.sdk)
    monkeypatch.setattr(
        retirement, "guest_for", lambda *_: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    with pytest.raises(RuntimeError, match="Unknown guest enrollment"):
        retirement.retire_members(c.owner, c.row, output, c.sdk)
    assert not c.journal.value["gateways"]["ts"].get("retired")


def test_proven_node_can_retire_when_guest_unreachable(retirement_case, monkeypatch):
    c = retirement_case
    output = retirement.prepare(c.owner, c.row, c.state, c.sdk)
    c.own["node_id"] = "node-a"
    deleted = []
    c.owner.api = lambda _: NS(delete_owned=lambda node, _: deleted.append(node))
    monkeypatch.setattr(
        retirement, "guest_for", lambda *_: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    retirement.retire_members(c.owner, c.row, output, c.sdk)
    assert deleted == ["node-a"]
    assert c.own["device_retired"]


@pytest.mark.parametrize(
    "code,absent", [("NOT_FOUND", True), ("PERMISSION_DENIED", False), ("UNAVAILABLE", False)]
)
def test_cloud_read_distinguishes_absence_from_uncertainty(monkeypatch, code, absent):
    from nebius.aio import service_error
    from nebius.api.nebius.compute import v1

    class Error(Exception):
        status = NS(code=NS(name=code))

    monkeypatch.setattr(service_error, "RequestError", Error)
    monkeypatch.setattr(
        v1, "DiskServiceClient", lambda _: NS(get=lambda *_a, **_kw: (_ for _ in ()).throw(Error()))
    )
    cloud = retirement.Cloud(NS(sdk=None))
    if absent:
        assert cloud.read("Disk", "disk-a") is None
    else:
        with pytest.raises(RuntimeError, match="absence is unproven"):
            cloud.read("Disk", "disk-a")


def test_rejected_key_replacement_preserves_started_operation(enrollment_case, monkeypatch):
    c = enrollment_case
    original = c.guest.write

    def interrupt(path, value):
        original(path, value)
        if isinstance(value, dict) and value.get("phase") == "started":
            raise RuntimeError("interrupted before up")

    monkeypatch.setattr(c.guest, "write", interrupt)
    with pytest.raises(RuntimeError, match="interrupted"):
        c.resume()
    monkeypatch.setattr(c.guest, "write", original)
    with pytest.raises(RuntimeError, match="already started"):
        enrollment.recover_unused(c.journal, c.own, c.remote, c.api, c.store)
    assert c.resume()["node_id"] == "node-a"
    assert len(c.issued) == 1
    assert c.deleted == ["key-id"]  # Enrollment retires its one-use key after proof.


def test_torn_receipt_recovery_uses_verified_id_without_reusing_key(enrollment_case):
    c = enrollment_case
    c.remote.fault = lambda operation, _: (
        (_ for _ in ()).throw(RuntimeError("pause")) if operation == "reconcile" else None
    )
    with pytest.raises(RuntimeError, match="pause"):
        c.resume()
    c.remote.fault = None
    op = c.own["enrollment"]
    op.update(phase="issuing", key_id="")
    path = c.store.base / ".nebius-cxcli/tailscale-enrollment" / (op["id"] + ".json")
    path.write_text('{"schema":')
    c.api.key_info = lambda key: {
        "description": "cxcli enrollment " + op["id"],
        "capabilities": {
            "devices": {
                "create": {
                    "reusable": False,
                    "ephemeral": False,
                    "tags": op["tags"],
                }
            }
        },
    }
    enrollment.recover_unused(c.journal, c.own, c.remote, c.api, c.store, key_id="key-id")
    assert c.deleted == ["key-id"]
    assert not path.exists()
    assert "enrollment" not in c.own
    assert not c.calls


@pytest.mark.parametrize("enrolled", [False, True])
def test_admin_key_identity_survives_delivery_for_retirement(
    enrollment_case, monkeypatch, enrolled
):
    c = enrollment_case
    c.row["tailscale"]["auth"] = {
        "mode": "auth-key",
        "member_key_refs": {"a": "env:TEST_ADMIN_KEY", "b": "env:TEST_ADMIN_KEY_B"},
        "member_key_ids": {"a": "admin-key-id", "b": "admin-key-b"},
    }
    monkeypatch.setattr(enrollment, "secret", lambda *_a, **_kw: "tskey-auth-original")
    c.api.key_info = lambda key: {
        "capabilities": {
            "devices": {
                "create": {
                    "reusable": False,
                    "ephemeral": False,
                    "tags": c.row["tailscale"]["tags"],
                }
            }
        },
    }
    if not enrolled:
        c.remote.fault = lambda operation, _: (
            (_ for _ in ()).throw(RuntimeError("pause")) if operation == "reconcile" else None
        )
        with pytest.raises(RuntimeError, match="pause"):
            c.resume()
    else:
        c.resume()
    assert c.own["enrollment"]["key_id"] == "admin-key-id"
    enrollment.revoke(c.journal, c.own, c.api, c.store)
    assert c.deleted == ["admin-key-id"]
    assert c.own["key_retired"]


@pytest.mark.parametrize("remaining", [False, True])
def test_destroy_rejects_omitted_gateway_even_with_only_disk_state(
    tmp_path, monkeypatch, remaining
):
    from nebius_cxcli import cli, generated_manifest, tailscale_deploy

    state = {
        "values": {
            "root_module": {
                "resources": [
                    {
                        "address": 'module.omitted.module.member["a"].nebius_compute_v1_disk.boot[0]',
                        "type": "nebius_compute_v1_disk",
                        "values": {
                            "id": "disk-omitted",
                            "labels": {"cxcli-component": "tailscale-gw"},
                        },
                    }
                ]
            }
        }
    }
    monkeypatch.setattr(tailscale_deploy, "rows", lambda _: [row()] if remaining else [])
    monkeypatch.setattr(cli, "terraform_show_json", lambda *_a, **_kw: state)
    monkeypatch.setattr(
        generated_manifest,
        "load_generated_manifest",
        lambda _: {
            "render": {
                "module_sources": [
                    {"component_id": "tailscale-gw", "instance_id": name, "module_name": name}
                    for name in ("ts", "omitted")
                ]
            },
        },
    )
    with (
        pytest.raises(RuntimeError, match="Retain Tailscale component configuration"),
        tailscale_deploy.retire_for_destroy(
            NS(), NS(infra_dir=tmp_path, generated_dir=tmp_path), {}
        ),
    ):
        pytest.fail("Terraform destroy must not be admitted")


def test_replacing_an_enrolled_key_is_rejected_without_fencing(enrollment_case):
    c = enrollment_case
    c.resume()
    before = copy.deepcopy(c.own)
    saved = c.remote.call("enrollment-status", enrollment=enrollment.contract(c.own["enrollment"]))
    with pytest.raises(RuntimeError, match="already started"):
        enrollment.recover_unused(c.journal, c.own, c.remote, c.api, c.store)
    assert c.own == before
    assert (
        c.remote.call("enrollment-status", enrollment=enrollment.contract(c.own["enrollment"]))
        == saved
    )
    assert c.resume()["node_id"] == "node-a"


def test_guest_unused_fence_atomically_refuses_started_operation(enrollment_case):
    c = enrollment_case
    c.resume()
    op = c.own["enrollment"]
    with pytest.raises(RuntimeError, match="already started"):
        c.remote.call("enrollment-fence-unused", enrollment=enrollment.contract(op))
    assert not c.remote.call("enrollment-status", enrollment=enrollment.contract(op))["receipt"][
        "fenced"
    ]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_definitively_rejected_issuance_can_retry_same_operation(enrollment_case, status):
    c = enrollment_case
    create = c.api.key
    c.api.key = lambda _: (_ for _ in ()).throw(ApiError(status))
    with pytest.raises(ApiError):
        c.resume()
    op = c.own["enrollment"]["id"]
    assert c.own["enrollment"]["phase"] == "rejected"
    c.api.key = create
    c.resume()
    assert c.issued == [op]


def test_complete_pending_local_key_survives_publish_interruption(enrollment_case, monkeypatch):
    c = enrollment_case
    with monkeypatch.context() as patch:
        patch.setattr(
            enrollment.os,
            "rename",
            lambda *_a, **_kw: (_ for _ in ()).throw(OSError("local publication interrupted")),
        )
        with pytest.raises(OSError, match="publication interrupted"):
            c.resume()
    assert c.own["enrollment"]["phase"] == "issuing"
    c.resume()
    assert len(c.issued) == 1
    assert not list((c.store.base / ".nebius-cxcli/tailscale-enrollment").iterdir())


def test_consumed_administrator_key_retirement(enrollment_case, monkeypatch):
    c = enrollment_case
    c.row["inputs"]["replicas"] = 1
    c.row["tailscale"]["auth"] = {
        "mode": "auth-key",
        "member_key_refs": {"a": "env:TEST_RECOVERY_KEY"},
        "member_key_ids": {"a": "admin-key-id"},
    }
    c.api.key_info = lambda _: {
        "capabilities": {
            "devices": {
                "create": {
                    "reusable": False,
                    "ephemeral": False,
                    "tags": c.row["tailscale"]["tags"],
                }
            }
        }
    }
    monkeypatch.setenv("TEST_RECOVERY_KEY", "tskey-auth-admin")
    c.resume()
    assert not c.issued
    deleted = []
    c.api.delete_owned = lambda node, _: deleted.append(node)
    owner = NS(
        open=lambda: c.journal,
        api=lambda _: c.api,
        executor=NS(manifest={}, report_paths=NS(project_dir=c.store.base)),
    )
    monkeypatch.setattr(retirement, "guest_for", lambda *_: c.remote)
    monkeypatch.setattr(retirement, "asset", lambda *_: {})
    retirement.retire_members(owner, c.row, {"a": {}}, NS())
    assert c.own["key_retired"] and c.own["device_retired"]
    assert deleted == ["node-a"]
    assert c.deleted == ["admin-key-id"]  # API confirms absence for a consumed key.


def test_ambiguous_issuance_can_recover_after_retirement_attempt(enrollment_case, monkeypatch):
    c = enrollment_case
    c.api.key = lambda _: (_ for _ in ()).throw(RuntimeError("lost response"))
    with pytest.raises(RuntimeError, match="lost response"):
        c.resume()
    owner = NS(
        open=lambda: c.journal,
        api=lambda _: c.api,
        executor=NS(manifest={}, report_paths=NS(project_dir=c.store.base)),
    )
    monkeypatch.setattr(retirement, "guest_for", lambda *_: c.remote)
    monkeypatch.setattr(retirement, "asset", lambda *_: {})
    c.journal.value["gateways"]["ts"]["retiring"] = True
    with pytest.raises(RuntimeError, match="key identity is unresolved"):
        retirement.retire_members(owner, c.row, {"a": {}}, NS())
    c.api.key_info = lambda _: {
        "description": "cxcli enrollment " + c.own["enrollment"]["id"],
        "capabilities": {
            "devices": {
                "create": {
                    "reusable": False,
                    "ephemeral": False,
                    "tags": c.row["tailscale"]["tags"],
                }
            }
        },
    }
    enrollment.recover_unused(c.journal, c.own, c.remote, c.api, c.store, key_id="key-id")
    retirement.retire_members(owner, c.row, {"a": {}}, NS())
    assert "enrollment" not in c.own
    assert c.deleted
    assert not c.calls


def test_ordinary_destroy_uses_dedicated_retirement_preparation(retirement_case, monkeypatch):
    from contextlib import nullcontext

    from nebius_cxcli import cli, deployment_cli, generated_manifest, tailscale_deploy

    c = retirement_case
    config = {"infra": {"components": [c.row]}}
    paths = NS(
        project_dir=c.owner.executor.report_paths.project_dir,
        generated_dir="generated",
        infra_dir="infra",
    )
    monkeypatch.setattr(
        deployment_cli,
        "deployment_execution",
        lambda **_: nullcontext(NS(assert_held=lambda: None)),
    )
    monkeypatch.setattr(
        generated_manifest,
        "load_generated_manifest",
        lambda _: {
            "render": {
                "module_sources": [
                    {"component_id": "tailscale-gw", "instance_id": "ts", "module_name": "ts"}
                ]
            }
        },
    )
    monkeypatch.setattr(
        cli,
        "terraform_show_json",
        lambda *_a, **_kw: {"values": {"root_module": {"resources": c.state}}},
    )
    monkeypatch.setattr(
        cli,
        "terraform_output_json",
        lambda *_a, **_kw: pytest.fail("retirement must not require outputs"),
    )
    monkeypatch.setattr(tailscale_deploy, "ComputeHostIdentityClient", lambda _: nullcontext(c.sdk))
    monkeypatch.setattr(tailscale_deploy.TailscaleDeployment, "open", lambda _: c.journal)
    monkeypatch.setattr(tailscale_deploy.TailscaleDeployment, "api", lambda *_: None)
    monkeypatch.setattr(
        tailscale_deploy.TailscaleDeployment,
        "prepare",
        lambda *_: pytest.fail("provisioning during destroy"),
    )
    with tailscale_deploy.retire_for_destroy(config, paths, {}):
        assert c.journal.value["gateways"]["ts"]["retiring"]
        c.state.clear()
        c.live.clear()
    assert c.journal.value["gateways"]["ts"]["retired"]


@pytest.mark.parametrize("change", ["tags", "binding", "mode"])
def test_rejected_issuance_validates_binding_before_retry(enrollment_case, change):
    c = enrollment_case
    c.api.key = lambda _: (_ for _ in ()).throw(ApiError(403))
    with pytest.raises(ApiError):
        c.resume()
    if change == "tags":
        c.row["tailscale"]["tags"] = ["tag:other"]
    elif change == "binding":
        c.own["instance_id"] = "foreign"
    else:
        c.row["tailscale"]["auth"]["mode"] = "auth-key"
    c.api.key = lambda _: pytest.fail("changed operation must not issue another key")
    with pytest.raises(RuntimeError, match="binding changed"):
        c.resume()


@pytest.mark.parametrize(
    "info", [None, {"reusable": True}, {"ephemeral": True}, {"tags": ["tag:foreign"]}]
)
def test_admin_key_capabilities_fail_before_delivery(enrollment_case, monkeypatch, info):
    c = enrollment_case
    c.row["inputs"]["replicas"] = 1
    c.row["tailscale"]["auth"] = {
        "mode": "auth-key",
        "member_key_refs": {"a": "env:TEST_ADMIN_KEY"},
        "member_key_ids": {"a": "admin-key-id"},
    }
    monkeypatch.setattr(enrollment, "secret", lambda *_a, **_kw: "tskey-auth-original")
    c.api.key_info = lambda _: (
        None
        if info is None
        else {
            "capabilities": {
                "devices": {
                    "create": {
                        "reusable": False,
                        "ephemeral": False,
                        "tags": ["tag:router"],
                        **info,
                    }
                }
            }
        }
    )
    with pytest.raises(RuntimeError, match="capabilities"):
        c.resume()
    assert not c.calls and not c.issued and not c.deleted


def test_active_omitted_journal_owner_blocks_destroy_without_cloud_state():
    from nebius_cxcli.tailscale_deploy import assert_retirement_coverage

    with pytest.raises(RuntimeError, match="Retain Tailscale"):
        assert_retirement_coverage([row()], [], {}, {"omitted": {"members": {}}})
    assert_retirement_coverage([row()], [], {}, {"omitted": {"retired": True}})


def test_rejected_key_recovery_archive_does_not_block_later_retirement(
    enrollment_case, monkeypatch
):
    c = enrollment_case
    create = c.api.key
    c.api.key = lambda _: (_ for _ in ()).throw(ApiError(403))
    with pytest.raises(ApiError):
        c.resume()
    enrollment.recover_unused(c.journal, c.own, c.remote, c.api, c.store)
    c.api.key = create
    c.resume()
    c.api.delete_owned = lambda *_: None
    owner = NS(
        open=lambda: c.journal,
        api=lambda _: c.api,
        executor=NS(manifest={}, report_paths=NS(project_dir=c.store.base)),
    )
    monkeypatch.setattr(retirement, "guest_for", lambda *_: c.remote)
    monkeypatch.setattr(retirement, "asset", lambda *_: {})
    retirement.retire_members(owner, c.row, {"a": {}}, NS())
    assert c.own["device_retired"] and c.own["key_retired"]
    assert c.deleted == ["key-id"]


def test_empty_config_destroy_checks_existing_remote_owner(tmp_path, monkeypatch):
    from contextlib import nullcontext

    from nebius_cxcli import cli, deployment_cli, generated_manifest, tailscale_deploy

    monkeypatch.setattr(cli, "terraform_show_json", lambda *_a, **_kw: {})
    monkeypatch.setattr(generated_manifest, "load_generated_manifest", lambda _: {})
    monkeypatch.setattr(
        deployment_cli,
        "deployment_execution",
        lambda **_: nullcontext(NS(assert_held=lambda: None)),
    )
    journal = SavedJournal()
    journal.value["gateways"]["omitted"] = {"members": {"a": {"node_id": "node-a"}}}
    monkeypatch.setattr(tailscale_deploy.TailscaleDeployment, "open", lambda _: journal)
    with (
        pytest.raises(RuntimeError, match="Retain Tailscale"),
        tailscale_deploy.retire_for_destroy(
            {"infra": {"components": []}}, NS(infra_dir=tmp_path, generated_dir=tmp_path), {}
        ),
    ):
        pytest.fail("Existing remote owner must block destruction")


def test_revocation_checkpoint_survives_interruption_before_archive(enrollment_case):
    c = enrollment_case
    c.api.key = lambda _: (_ for _ in ()).throw(ApiError(403))
    with pytest.raises(ApiError):
        c.resume()
    save = c.journal.save

    def interrupt():
        save()
        if c.own.get("enrollment", {}).get("phase") == "revoked":
            raise RuntimeError("revoked checkpoint saved")

    c.journal.save = interrupt
    with pytest.raises(RuntimeError, match="checkpoint saved"):
        enrollment.recover_unused(c.journal, c.own, c.remote, c.api, c.store)
    c.journal.save = save
    assert c.journal.saved["gateways"]["ts"]["members"]["a"]["enrollment"]["phase"] == "revoked"
    enrollment.recover_unused(c.journal, c.own, c.remote, c.api, c.store)
    assert "enrollment" not in c.own
    assert not c.deleted
