from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_ssh_policy import _default_host_key_directory, _ed25519_public_key, _trust_scope

from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import provisioning_trust, ssh_policy
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect


@pytest.mark.parametrize("boundary", ["keys", "selection", "receipt", "complete", None])
def test_ssh_prelude_resumes_every_publication_frontier_without_replacing_keys(
    tmp_path, monkeypatch, boundary
):
    monkeypatch.setenv("HOME", str(tmp_path))
    scope = _trust_scope(cluster_id="ordinary-v1")

    def observe(*, allow_repair=True):
        return ssh_policy.require_vm_ha_ssh_policy(
            (("gateway-0", "gateway-0"),),
            {},
            enrollment_hosts={"gateway-0"},
            trust_scope=scope,
            allow_managed_repair=allow_repair,
            persist_default_host_keys=True,
            managed_root=tmp_path / "managed",
        )

    desired = provisioning_trust.intent(observe(), ("gateway-0",))
    envelope = CommandPlan(
        "project",
        "gateway",
        "a" * 64,
        "b" * 64,
        (
            Effect(
                "ssh", "ssh-bootstrap", "publish", ("gateway-0",), digest(desired), "c" * 64, False
            ),
        ),
    )
    child = provisioning_trust.TrustJournal("project", "gateway", root=tmp_path / "child")
    if boundary == "keys":
        owner, name = provisioning_trust, "persist_planned_host_keys"
    elif boundary == "selection":
        owner, name = child, "select"
    elif boundary == "receipt":
        owner, name = ssh_policy, "_write_atomic"
    else:
        owner, name = child, "complete"
    original = getattr(owner, name)
    failed = []

    def interrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        if not failed and (boundary != "receipt" or args[0].name == "trust.json"):
            failed.append(True)
            raise TimeoutError("publication interrupted")
        return result

    with CommandJournal("project", "gateway", root=tmp_path / "parent") as command:
        command.begin(envelope, intents={"ssh": desired})
        command.started("ssh")
        if boundary is not None:
            monkeypatch.setattr(owner, name, interrupt)
            with pytest.raises(TimeoutError):
                provisioning_trust.publish(command, child, desired, observe)
            monkeypatch.setattr(owner, name, original)
        key = _default_host_key_directory(tmp_path, scope) / "gateway-0.key"
        before_key = key.read_bytes() if key.exists() else None
        receipt = provisioning_trust.publish(command, child, desired, observe)
        if before_key is not None:
            assert key.read_bytes() == before_key
        command.verified("ssh", receipt)
        command.complete()
        before = (command.path.read_bytes(), child.path.read_bytes(), key.read_bytes())
        assert provisioning_trust.publish(command, child, desired, observe) == receipt
        assert before == (command.path.read_bytes(), child.path.read_bytes(), key.read_bytes())
        assert "PRIVATE KEY" not in child.path.read_text()
        published = observe(allow_repair=False)
        assert published.managed_action is None
        assert published.identity_for("gateway-0").private_key == key.read_bytes()
        assert before == (command.path.read_bytes(), child.path.read_bytes(), key.read_bytes())


def test_ssh_prelude_rejects_unapproved_scope_before_private_publication(tmp_path):
    child = provisioning_trust.TrustJournal("project", "gateway", root=tmp_path / "child")
    command = CommandJournal("project", "gateway", root=tmp_path / "parent")
    with pytest.raises(RuntimeError, match="active exact command"):
        provisioning_trust.publish(command, child, {}, lambda: SimpleNamespace())
    assert not child.root.exists()


@pytest.mark.parametrize("verified", [False, True])
@pytest.mark.parametrize("current_binding_changed", [False, True])
def test_migration_resume_preserves_published_provenance_and_rechecks_current_compute(
    tmp_path, monkeypatch, verified, current_binding_changed
):
    from nebius_vpngw.cli import _vm_ha_ordinary_migration_ssh_import_hosts
    from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus

    monkeypatch.setenv("HOME", str(tmp_path))
    key_type, key_data = _ed25519_public_key(tmp_path)
    checks = []
    changed = False

    def current_binding():
        checks.append(True)
        if changed:
            raise RuntimeError("current Compute identity changed")

    imported = ssh_policy.TrustedSSHMemberImport(
        hostname="gateway-0",
        pins={key_type: key_data},
        predecessor_receipt_sha256="a" * 64,
        compute_binding_sha256="b" * 64,
        assert_current=current_binding,
    )
    lifecycle = SimpleNamespace(status=VMHALifecycleStatus.ACTIVATING)
    published = False

    def observe():
        imports = _vm_ha_ordinary_migration_ssh_import_hosts(
            lifecycle, None, {"gateway-0"}, ssh_published=published
        )
        return ssh_policy.require_vm_ha_ssh_policy(
            (("gateway-0", "203.0.113.10"), ("gateway-1", "203.0.113.11")),
            {},
            enrollment_hosts={"gateway-1"},
            retained_hosts={"gateway-0"},
            trust_scope=_trust_scope(),
            allow_managed_repair=True,
            persist_default_host_keys=True,
            default_known_hosts_bindings={"gateway-0": current_binding},
            trusted_member_imports={name: imported for name in imports},
        )

    desired = provisioning_trust.intent(observe(), ("gateway-1",))
    envelope = CommandPlan(
        "project", "gateway", "a" * 64, "b" * 64,
        (Effect("ssh", "ssh-bootstrap", "publish", ("gateway-0", "gateway-1"),
                digest(desired), "c" * 64, False),),
    )
    child = provisioning_trust.TrustJournal("project", "gateway", root=tmp_path / "child")
    with CommandJournal("project", "gateway", root=tmp_path / "parent") as command:
        command.begin(envelope, intents={"ssh": desired})
        command.started("ssh")
        receipt = provisioning_trust.publish(command, child, desired, observe)
        if verified:
            command.verified("ssh", receipt)
            published = True
        before = (command.path.read_bytes(), child.path.read_bytes())
        selected = child.snapshot()["transaction"]["selected"]
        # A later approved Compute transition changes observation provenance,
        # but neither the published pins nor the selected standby private key.
        imported = replace(imported, compute_binding_sha256="c" * 64)
        changed = current_binding_changed
        checks.clear()
        if current_binding_changed or not verified:
            reason = "current Compute identity" if current_binding_changed else "Selected SSH"
            with pytest.raises(RuntimeError, match=reason):
                provisioning_trust.publish(command, child, desired, observe)
        else:
            assert provisioning_trust.publish(command, child, desired, observe) == receipt
        assert checks
        assert before == (command.path.read_bytes(), child.path.read_bytes())
        assert child.snapshot()["transaction"]["selected"] == selected


@pytest.mark.parametrize("worker_state", ["RUNNING_OWNED", "COMPLETED", "BLOCKED"])
def test_verified_ssh_resume_keeps_original_package_handoff(
    tmp_path, monkeypatch, worker_state
):
    """Exercise the CLI observation block with a real immutable reservation."""
    import ast
    from pathlib import Path

    from nebius_vpngw import cli
    from nebius_vpngw.deploy import ordinary_handoff, package_reservation
    from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus

    host = "gateway-0"
    instance = SimpleNamespace(hostname=host)
    request = {
        "action": "reserve",
        "package": {"host": host},
        "observed": {"mode": "ordinary"},
        "approval": "a" * 64,
    }
    scope = (tmp_path / "command", "b" * 32, "c" * 64)
    token = package_reservation.SCOPE.set(scope)
    try:
        job = ordinary_handoff.PackageJob(None, "203.0.113.10", instance, {})
        original = job.bind(request)
        saved = package_reservation.read(*scope, host)
        path = package_reservation.path_for(scope[0], scope[1], host)
        before = path.read_bytes()
        lifecycle = SimpleNamespace(status=VMHALifecycleStatus.ACTIVATING)
        imports = cli._vm_ha_ordinary_migration_ssh_import_hosts(
            lifecycle, None, {host}, ssh_published=True
        )
        handoffs = cli._vm_ha_ordinary_migration_ssh_import_hosts(
            lifecycle, None, {host}
        )
        assert imports == set() and handoffs == {host}
        observations = []

        def inspect_reservation(ssh, target, selected, local, reservation):
            assert selected is instance and reservation == saved
            observations.append(target)
            return {"state": worker_state}

        monkeypatch.setattr(ordinary_handoff, "inspect_reservation", inspect_reservation)
        monkeypatch.setattr(
            ordinary_handoff, "inspect",
            lambda *args: pytest.fail("An admitted worker must retain its original request"),
        )
        # Run the actual CLI boundary without authentication or cloud effects.
        # The surrounding command supplies a validated reservation and trust.
        tree = ast.parse(Path(cli.__file__).read_text())
        function = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_apply_impl"
        )
        start = next(
            i for i, n in enumerate(function.body)
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            and n.target.id == "ordinary_handoff_observations"
        )
        block = ast.Module(body=function.body[start:start + 4], type_ignores=[])
        namespace = {
            "__name__": cli.__name__, "__package__": cli.__package__,
            "plan": SimpleNamespace(vm_ha=object()), "planned_instances": (instance,),
            "ordinary_migration_import_hosts": imports,
            "ordinary_migration_handoff_hosts": handoffs,
            "package_reservations": {host: saved}, "ssh_policy": object(),
            "SSHPush": lambda **kwargs: object(),
            "vm_ha_existing_members": {host: "203.0.113.10"}, "local_cfg": {},
            "t": cli.t,
        }
        if worker_state == "BLOCKED":
            with pytest.raises(RuntimeError, match="reservation is blocked"):
                exec(compile(block, cli.__file__, "exec"), namespace)
        else:
            exec(compile(block, cli.__file__, "exec"), namespace)
            assert namespace["ordinary_handoff_observations"] == {host: request["observed"]}
            replay = ordinary_handoff.PackageJob(None, "203.0.113.10", instance, {})
            assert replay.bind(request) == original
            assert replay.identity == saved["identity"]
            with pytest.raises(RuntimeError, match="reservation differs"):
                replay.bind({"action": "prepare-guarded", "package": request["package"]})
        assert observations == ["203.0.113.10"]
        assert path.read_bytes() == before
    finally:
        package_reservation.SCOPE.reset(token)
