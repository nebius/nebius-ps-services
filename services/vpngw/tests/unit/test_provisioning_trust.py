from types import SimpleNamespace

import pytest
from test_ssh_policy import _default_host_key_directory, _trust_scope

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
