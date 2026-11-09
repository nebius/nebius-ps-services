import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from tests.unit.test_ordinary_route_runtime import bound
from tests.unit.test_vm_ha_managed_credentials import (
    _iam_inspector,
    _key_item_from_credential,
    _KeyService,
    _write_credential,
)

from nebius_vpngw import ordinary_route_binding as binding
from nebius_vpngw import vm_ha_managed_credentials as managed
from nebius_vpngw.deploy.credential_enrollment import EnrollmentJournal
from nebius_vpngw.vm_ha_credentials import (
    VMHACredentialIdentity,
    VMHACredentialSet,
    read_vm_ha_credential_source,
)
from nebius_vpngw.vpngw_sa import ServiceAccountTokenIdentity


def test_completed_first_enrollment_recovers_same_key_without_expanding_iam(recovery, tmp_path):
    s = recovery
    # Establish the local key, then enroll without needing recovery authority.
    s.ensure(s.inspect())
    initial = s.inspect(source=None)
    child = EnrollmentJournal("project-a", "gateway-a", root=tmp_path / "first-enrollment")
    with child:
        child.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        s.ensure(initial, enrollment=child)
    before = child.path.read_bytes()
    initial.source_path.unlink()
    observed = s.inspect(enrollment=child)
    with child:
        bound = child.bind_observation(
            observed, intent=initial.approval_record(), approval="a" * 64, execution_id="b" * 32
        )
        s.ensure(bound, enrollment=child)
    assert initial.source_path.read_bytes() == s.payload
    assert child.path.read_bytes() == before
    assert len(s.keys.items) == 1


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    secret = tmp_path / "guest-source.json"
    _write_credential(secret)
    payload = secret.read_bytes()
    value = bound()
    value.update(project="project-a", gateway="gateway-a", node="gateway-a-0")
    value["infrastructure"]["project"] = "project-a"
    value["infrastructure"]["gateways"]["0"]["name"] = "gateway-a-0"
    value["credentials"] = {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "service_account": "service-account-a",
        "authorized_key": "auth-public-key-a",
        "path": binding.credential_path(
            generation=value["generation"],
            node=value["node"],
            sha256=hashlib.sha256(payload).hexdigest(),
        ),
    }
    source = managed.OrdinaryCredentialRecovery(value, lambda: read_vm_ha_credential_source(secret))
    keys = _KeyService()
    item = _key_item_from_credential(secret)
    item.metadata.name = "gateway-a-route-runtime-key"
    item.metadata.labels = managed.vm_ha_ownership_labels(
        project_id="project-a", gateway_name="gateway-a", purpose="ordinary-route-writer"
    )
    item.spec.description = "Nebius VPNGW ordinary route runtime key"
    keys.items.append(item)
    monkeypatch.setattr("nebius.api.nebius.iam.v1.AuthPublicKeyServiceClient", lambda sdk: keys)

    def factory(*a):
        return NS(sync_close=lambda: None), True

    def preflight(node_ids, *, source_path, project_id, requested_service_account_name, purpose):
        raw = read_vm_ha_credential_source(source_path)
        subject = json.loads(raw)["subject-credentials"]
        return VMHACredentialSet(
            tuple(
                VMHACredentialIdentity(
                    node,
                    source_path,
                    hashlib.sha256(raw).hexdigest(),
                    subject["sub"],
                    subject["kid"],
                    project_id,
                    requested_service_account_name,
                )
                for node in node_ids
            ),
            purpose=purpose,
        )

    def inspect(**kwargs):
        return managed.inspect_managed_vm_ha_credentials(
            project_id="project-a",
            gateway_name="gateway-a",
            node_ids=("gateway-a-0",),
            purpose="ordinary-route-writer",
            home=tmp_path,
            iam_inspector=_iam_inspector,
            client_factory=factory,
            preflight=preflight,
            recovery=kwargs.pop("source", source),
            **kwargs,
        )

    def ensure(plan, **kwargs):
        def identity(name, tenant, project, region, **options):
            assert all(
                v["action"] == "reuse"
                for v in options["reconciliation_plan"].approval_record().values()
            )
            return ServiceAccountTokenIdentity("test-token", "service-account-a", name)

        return managed.ensure_managed_vm_ha_credentials(
            plan,
            node_ids=("gateway-a-0",),
            tenant_id=None,
            region_id=None,
            client_factory=factory,
            preflight=preflight,
            iam_inspector=_iam_inspector,
            identity_ensurer=identity,
            **kwargs,
        )

    return NS(
        inspect=inspect,
        ensure=ensure,
        source=source,
        value=value,
        payload=payload,
        secret=secret,
        keys=keys,
        home=tmp_path,
    )


def test_recovery_preview_is_read_only_and_publication_reuses_exact_iam(recovery):
    s = recovery
    plan = s.inspect()
    assert plan.action == "recover" and not plan.source_path.exists()
    assert "private-key" not in json.dumps(plan.approval_record())
    assert plan.approval_record()["recovery"] == s.value
    result = s.ensure(plan)
    assert plan.source_path.read_bytes() == s.payload
    assert result.credentials == plan.credentials
    assert s.keys.create_requests == []
    assert not s.inspect().source_path.is_symlink()


def test_lost_publication_reply_resumes_original_enrollment_without_iam_creation(
    recovery, monkeypatch
):
    s = recovery
    plan = s.inspect()
    journal = EnrollmentJournal("project-a", "gateway-a", root=s.home / "enrollment")
    publish = managed._atomic_private_write
    failed = False

    def lose(path, payload, **kwargs):
        nonlocal failed
        publish(path, payload, **kwargs)
        if not failed:
            failed = True
            raise OSError("lost publication reply")

    monkeypatch.setattr(managed, "_atomic_private_write", lose)
    with journal:
        journal.begin(plan.approval_record(), approval="a" * 64, execution_id="b" * 32)
        with pytest.raises(OSError, match="lost publication"):
            s.ensure(plan, enrollment=journal)
    assert plan.source_path.read_bytes() == s.payload
    observed = s.inspect(enrollment=journal)
    resumed = journal.bind_observation(
        observed, intent=plan.approval_record(), approval="a" * 64, execution_id="b" * 32
    )
    with journal:
        s.ensure(resumed, enrollment=journal)
    assert journal.snapshot()["transaction"]["phase"] == "complete"
    assert s.keys.create_requests == []


@pytest.mark.parametrize("fault", ["hash", "key", "project", "node", "unsafe", "iam", "pending"])
def test_recovery_rejects_untrusted_or_changed_evidence(recovery, fault):
    s = recovery
    value = deepcopy(s.value)
    if fault == "hash":
        s.secret.write_bytes(s.payload + b" ")
    elif fault == "key":
        value["credentials"]["authorized_key"] = "foreign-key"
    elif fault == "project":
        value["project"] = value["infrastructure"]["project"] = "foreign"
    elif fault == "node":
        value["node"] = value["infrastructure"]["gateways"]["0"]["name"] = "foreign-node"
        value["credentials"]["path"] = binding.credential_path(
            generation=value["generation"],
            node=value["node"],
            sha256=value["credentials"]["sha256"],
        )
    elif fault == "unsafe":
        s.secret.chmod(0o644)
    elif fault == "iam":
        s.keys.items[0].metadata.id = "foreign-key"
    elif fault == "pending":
        plan = s.inspect()
        managed._ensure_private_parent(
            plan.source_path, home=s.home, expected_uid=__import__("os").getuid()
        )
        managed._atomic_private_write(
            plan.source_path.parent / ".private-key.pending.pem", b"invalid", replace=False
        )
    with pytest.raises((RuntimeError, ValueError)):
        s.inspect(source=replace(s.source, binding=value))
    assert s.keys.create_requests == []


def test_existing_local_material_is_never_replaced(recovery):
    s = recovery
    plan = s.inspect()
    managed._ensure_private_parent(
        plan.source_path, home=s.home, expected_uid=__import__("os").getuid()
    )
    _write_credential(plan.source_path, key_id="different-key")
    original = plan.source_path.read_bytes()
    with pytest.raises(RuntimeError, match="conflict"):
        s.ensure(plan)
    assert plan.source_path.read_bytes() == original
    assert s.keys.create_requests == []


def test_iam_identity_change_after_preview_blocks_publication(recovery):
    s = recovery
    plan = s.inspect()
    s.keys.items[0].metadata.id = "changed-key"
    with pytest.raises(RuntimeError):
        s.ensure(plan)
    assert not plan.source_path.exists()


@pytest.mark.parametrize("same", [True, False])
def test_concurrent_publication_accepts_only_exact_recovered_bytes(recovery, monkeypatch, same):
    s = recovery
    plan = s.inspect()
    publish = managed._atomic_private_write
    concurrent = s.payload if same else s.payload + b" "

    def race(path, payload, **kwargs):
        publish(path, concurrent, **kwargs)
        raise managed.VMHAManagedCredentialError("publication would overwrite state")

    monkeypatch.setattr(managed, "_atomic_private_write", race)
    if same:
        assert s.ensure(plan).credentials == plan.credentials
    else:
        with pytest.raises(managed.VMHAManagedCredentialError, match="overwrite"):
            s.ensure(plan)
    assert plan.source_path.read_bytes() == concurrent
    assert s.keys.create_requests == []


def test_transport_uses_pinned_connection_and_never_copies_secret_into_request(
    recovery, monkeypatch
):
    from contextlib import contextmanager
    from io import StringIO

    from nebius_vpngw.deploy import ordinary_runtime_handoff as transport

    s = recovery
    captured = []
    incoming = StringIO()
    incoming.channel = NS(shutdown_write=lambda: None)
    output = NS(read=lambda size: s.payload, channel=NS(recv_exit_status=lambda: 0))

    def execute(command, **kwargs):
        captured.append(command)
        return incoming, output, None

    @contextmanager
    def pinned(ssh, target, instance, local):
        assert (ssh, target, instance.hostname, local) == ("pinned", "transport", "gateway-a-0", {})
        captured.append("pinned-connect")
        yield NS(exec_command=execute)

    monkeypatch.setattr(transport, "_connection", pinned)
    handoff = transport.Handoff("pinned", "transport", NS(hostname="gateway-a-0"), {})
    assert handoff.read_credential(s.value) == s.payload
    assert captured[0] == "pinned-connect"
    assert json.loads(incoming.getvalue()) == s.value
    assert "private-key" not in incoming.getvalue()
    output.channel.recv_exit_status = lambda: 1
    with pytest.raises(RuntimeError, match="unavailable"):
        handoff.read_credential(s.value)
