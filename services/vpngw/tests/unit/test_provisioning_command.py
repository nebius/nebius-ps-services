from contextlib import ExitStack
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from test_provisioning_execute import cloud_fixture
from test_provisioning_journal import plan as resource_plan

from nebius_vpngw import cli
from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import provisioning_command as owner
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.ssh_policy import VMHASSHTrustScope, require_vm_ha_ssh_policy
from nebius_vpngw.deploy.vm_manager import VMManager


@pytest.fixture
def scope(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("VPNGW_SSH_KNOWN_HOSTS_FILE", "VPNGW_SSH_HOST_KEYS_DIR"):
        monkeypatch.delenv(name, raising=False)
    trust = VMHASSHTrustScope("tenant", "project-a", "region", "gateway", "ordinary-v1")
    policy = require_vm_ha_ssh_policy(
        (("gateway-0", "gateway-0"),),
        enrollment_hosts={"gateway-0"},
        trust_scope=trust,
        allow_managed_repair=True,
        persist_default_host_keys=True,
    )
    instance = NS(hostname="gateway-0", instance_index=0, config_yaml="gateway: {}\n")
    deployment = NS(
        vm_ha=None,
        gateway_group=NS(name="gateway", vm_spec={}, ingress_policies={0: {}}),
        gateway={"local_prefixes": ["192.0.2.0/24"]},
        iter_instance_configs=lambda: iter([instance]),
    )
    cloud, submissions = cloud_fixture(monkeypatch)
    manager = NS(
        project_id="project-a",
        auth_token=None,
        _management_key_path=None,
        _get_client=lambda: object(),
        _build_cloud_init=lambda **kw: "#cloud-config\nwrite_files:\n            Port 22\n",
        _render_gateway_ssh_enrollment_cloud_init=VMManager._render_gateway_ssh_enrollment_cloud_init,
        _vm_public_ip_from_object=lambda _: "203.0.113.10",
        inspect_ordinary_targets=lambda names: {},
    )
    # These tests exercise the composing owner and real creation/trust journals;
    # resource/route discovery have their own SDK emulator tests.
    discoveries = []

    def resources(_manager, _sdk, _spec, indices, **kwargs):
        discoveries.append(indices)
        result = resource_plan()
        result.update(kwargs)
        return result

    monkeypatch.setattr(owner.provisioning_discovery, "resources", resources)
    monkeypatch.setattr(
        owner.provisioning_routes, "prepare", lambda d, p: {"provisioning": digest(p), "routes": {}}
    )
    journal = CommandJournal("project-a", "gateway", root=tmp_path / "command")
    local = {"tenant_id": "tenant", "project_id": "project-a", "region_id": "region"}
    return NS(
        manager=manager,
        deployment=deployment,
        instance=instance,
        policy=policy,
        trust=trust,
        journal=journal,
        local=local,
        discoveries=discoveries,
        cloud=cloud,
        submissions=submissions,
    )


def inspect(scope, **kwargs):
    args = dict(
        manager=scope.manager,
        deployment=scope.deployment,
        local=scope.local,
        targets={},
        plans={},
        artifact="b" * 64,
        ssh_policy=scope.policy,
        trust_scope=scope.trust,
        attachments=None,
        attachment_pending=None,
    )
    args.update(kwargs)
    return owner.inspect(scope.journal, **args)


def test_fresh_cli_plan_freezes_every_owner_before_approval_and_does_not_publish(
    scope, monkeypatch
):
    monkeypatch.setattr(cli, "_ordinary_ssh_trust_scope", lambda *a, **kw: scope.trust)
    seen = []
    monkeypatch.setattr(
        cli, "_approve_disruption", lambda approval, **kw: seen.append(approval) or False
    )
    with ExitStack() as stack:
        token = cli._VM_MANAGER_LIFETIMES.set(stack)
        try:
            plans, targets, approved, command = cli._prepare_ordinary_apply(
                scope.manager,
                NS(_ssh_policy=scope.policy),
                (scope.instance,),
                scope.local,
                supplied=None,
                dry_run=True,
                recreate=False,
                command_journal=scope.journal,
                artifact_sha256="b" * 64,
                deployment=scope.deployment,
            )
        finally:
            cli._VM_MANAGER_LIFETIMES.reset(token)
    assert not approved and not plans and not targets
    assert [e.owner for e in command.plan.effects] == [
        "ssh-bootstrap",
        "ordinary-provisioning",
        "ordinary-provisioned-runtime",
        "ordinary-provisioned-routes",
    ]
    assert seen == [command.approval]
    assert scope.discoveries == [(0,)] and scope.submissions == []
    assert scope.journal.snapshot() is None
    assert not list(scope.journal.root.parent.rglob("*.key"))


def test_fresh_command_resumes_published_keys_without_discovering_a_new_image(scope, monkeypatch):
    first = inspect(scope)
    with scope.journal:
        first.begin()
        policy = first.publish_trust()
        private = policy.identity_for("gateway-0").private_key
        original = scope.journal.path.read_bytes()
    # Resume after SSH publication, before resource submission. Key generation
    # was admitted once and the frozen creation image may no longer be latest.
    monkeypatch.setattr(
        owner.provisioning_discovery,
        "resources",
        lambda *a, **kw: pytest.fail("replanned cloud resources"),
    )
    next_policy = owner.policy(scope.manager, scope.deployment, scope.trust, first.intents["ssh"])
    resumed = inspect(scope, ssh_policy=next_policy)
    assert resumed.resumed and resumed.approval == first.approval
    assert scope.journal.path.read_bytes() == original
    with scope.journal:
        resumed.begin()
        published = resumed.publish_trust()
        assert published.identity_for("gateway-0").private_key == private
        assert scope.journal.path.read_bytes() == original
    assert scope.discoveries == [(0,)] and not scope.submissions


@pytest.mark.parametrize("drift", ["configuration", "artifact", "retained-target"])
def test_resume_cannot_expand_fresh_command_scope(scope, drift):
    first = inspect(scope)
    with scope.journal:
        first.begin()
    before = scope.journal.path.read_bytes()
    args = {}
    if drift == "configuration":
        args["local"] = {**scope.local, "changed": True}
    elif drift == "artifact":
        args["artifact"] = "f" * 64
    else:
        args["targets"] = {"unplanned": ("compute-foreign", "203.0.113.20")}
    with pytest.raises(RuntimeError):
        inspect(scope, **args)
    assert scope.journal.path.read_bytes() == before
    assert not scope.submissions


def test_retained_member_is_bound_even_when_initially_unchanged(scope):
    retained = NS(hostname="gateway-1", instance_index=1, config_yaml="gateway: {}\n")
    scope.deployment.iter_instance_configs = lambda: iter([scope.instance, retained])
    policy = require_vm_ha_ssh_policy(
        (("gateway-0", "gateway-0"), ("gateway-1", "203.0.113.11")),
        enrollment_hosts={"gateway-0", "gateway-1"},
        trust_scope=scope.trust,
        allow_managed_repair=True,
        persist_default_host_keys=True,
    )
    prepared = NS(manifest={"wheel_sha256": "b" * 64}, observation={"healthy": True}, noop=True)
    args = dict(
        targets={"gateway-1": ("retained-id", "203.0.113.11")},
        plans={"gateway-1": prepared},
        ssh_policy=policy,
    )
    first = inspect(scope, **args)
    with scope.journal:
        first.begin()
    assert scope.journal.state["transaction"]["progress"]["guest:gateway-1"] == "planned"
    assert first.intents["guest:gateway-1"] == prepared.manifest
    resumed = inspect(scope, **args)
    assert resumed.approval == first.approval
    changed = deepcopy(args)
    changed["targets"]["gateway-1"] = ("same-name-replacement", "203.0.113.11")
    with pytest.raises(RuntimeError, match="retained target changed"):
        inspect(scope, **changed)


def test_parent_resumes_after_compute_creation_without_recreating_resources_or_keys(scope):
    first = inspect(scope)
    with scope.journal:
        first.begin()
        policy = first.publish_trust()
        targets = first.provision(policy)
        assert targets == {"gateway-0": "203.0.113.10"}
        scope.manager.inspect_ordinary_targets = lambda names: {
            "gateway-0": ("compute-created", "203.0.113.10")
        }
    # Matching Compute names do not change the original fresh scope on resume.
    saved = owner.pending(scope.journal, scope.local, "b" * 64)
    assert owner.original_guests(saved) == {"gateway-0"}
    next_policy = owner.policy(
        scope.manager, scope.deployment, scope.trust, saved["bindings"]["ssh"]
    )
    resumed = inspect(
        scope, targets={"gateway-0": ("compute-created", "203.0.113.10")}, ssh_policy=next_policy
    )
    before = (scope.journal.path.read_bytes(), resumed.resources().path.read_bytes())
    with scope.journal:
        resumed.begin()
        assert resumed.provision(resumed.publish_trust()) == targets
    assert before == (scope.journal.path.read_bytes(), resumed.resources().path.read_bytes())
    assert [item[0] for item in scope.submissions] == ["disk", "compute"]
    assert "PRIVATE KEY" not in scope.journal.path.read_text()


def test_public_apply_runs_fresh_owners_in_order_without_legacy_ensure(
    scope, monkeypatch, tmp_path
):

    from nebius.api.nebius.compute import v1 as compute
    from test_cli_helpers import _ContextManagedFake
    from typer.testing import CliRunner

    from nebius_vpngw.config_loader import (
        GatewayGroupSpec,
        InstanceResolvedConfig,
        ResolvedDeploymentPlan,
    )
    from nebius_vpngw.deploy import ordinary_apply
    from nebius_vpngw.deploy.vm_diff import ChangeType, VMDiff

    events = []
    instance = InstanceResolvedConfig(
        instance_index=0, hostname="gateway-0", external_ip=None, config_yaml="gateway: {}\n"
    )
    deployment = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="gateway",
            instance_count=1,
            region="region",
            external_ips=[],
            vm_spec={},
            ingress_policies={0: {}},
        ),
        gateway={"local_prefixes": ["192.0.2.0/24"]},
        per_instance=[instance],
        manage_routes=True,
    )
    local = {
        **scope.local,
        "gateway_group": {"vm_spec": {}},
        "gateway": deployment.gateway,
        "defaults": {"routing": {"mode": "static"}},
    }
    wheel = tmp_path / "fixture.whl"
    wheel.write_bytes(b"exact selected fixture")
    artifact = ordinary_apply.sha(wheel.read_bytes())
    original_discover = owner.provisioning_discovery.resources

    def discover(*args, **kwargs):
        events.append("plan-resources")
        return original_discover(*args, **kwargs)

    monkeypatch.setattr(owner.provisioning_discovery, "resources", discover)

    class Manager(_ContextManagedFake):
        def __init__(self, **kwargs):
            self.project_id = "project-a"
            self.auth_token = None
            self._management_key_path = None

        _get_client = staticmethod(scope.manager._get_client)
        _build_cloud_init = staticmethod(scope.manager._build_cloud_init)
        _render_gateway_ssh_enrollment_cloud_init = staticmethod(
            scope.manager._render_gateway_ssh_enrollment_cloud_init
        )

        def prepare_ordinary_ssh_policy(self, *args, **kwargs):
            return scope.policy

        def inspect_ordinary_targets(self, names):
            return {
                name: ("compute-created", "203.0.113.10")
                for name in names
                if "compute-created" in scope.cloud
            }

        def check_changes(self, spec):
            return [
                (
                    "gateway-0",
                    VMDiff(
                        change_type=ChangeType.SAFE,
                        differences=["VM does not exist"],
                        destructive_fields=[],
                    ),
                )
            ]

        def _vm_public_ip_from_object(self, resource):
            resource.status = compute.InstanceStatus(state="RUNNING")
            return "203.0.113.10"

        def ensure_group(self, *args, **kwargs):
            pytest.fail("fresh apply bypassed its approved provisioning child")

    monkeypatch.setattr(cli, "VMManager", Manager)
    monkeypatch.setattr(cli, "SSHPush", lambda **kwargs: NS(_ssh_policy=kwargs.get("ssh_policy")))
    monkeypatch.setattr(cli, "load_local_config", lambda *a, **kw: local)
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *a, **kw: deployment)
    monkeypatch.setattr(cli, "_ensure_authentication", lambda *a, **kw: "test-token")
    monkeypatch.setattr(ordinary_apply, "pin_agent_artifact", lambda *a, **kw: wheel)

    def approve(*args, **kw):
        assert not scope.submissions
        events.append("approve")
        return True

    monkeypatch.setattr(cli, "_approve_disruption", approve)
    original_publish = owner.ProvisioningCommand.publish_trust

    def publish(command):
        events.append("publish-ssh")
        return original_publish(command)

    monkeypatch.setattr(owner.ProvisioningCommand, "publish_trust", publish)

    def routes(command, policy):
        assert [x[0] for x in scope.submissions] == ["disk", "compute"]
        events.append("bind-routes")
        command.routes = NS(required=True, connectivity_unknown=False)

    def finish(command):
        events.append("routes")
        command.journal.started("routes")
        command.journal.verified("routes", {"fixture": "route-owner-receipt"})
        return {"connectivity": "unknown"}

    monkeypatch.setattr(owner.ProvisioningCommand, "bind_routes", routes)
    monkeypatch.setattr(owner.ProvisioningCommand, "finish_routes", finish)

    def prepare(command, ssh, instance):
        assert "bind-routes" in events
        command.journal.started("guest:gateway-0")
        events.append("inspect-created-guest")
        return NS(
            target="203.0.113.10",
            target_identity="compute-created",
            instance=instance,
            noop=False,
            close=lambda: None,
        )

    monkeypatch.setattr(owner.ProvisioningCommand, "prepare_guest", prepare)
    monkeypatch.setattr(
        owner.ProvisioningCommand,
        "complete_guest",
        lambda *a: {"fixture": "exact-guest-owner-receipt"},
    )

    def stage(*args, approved, **kwargs):
        assert approved and len(scope.submissions) == 2
        events.append("stage")

    monkeypatch.setattr(ordinary_apply, "stage_plan", stage)
    monkeypatch.setattr(
        ordinary_apply, "execute_plan", lambda *a, **kw: events.append("activate") or {}
    )
    config = tmp_path / "config.yaml"
    config.write_text("version: 1\n")
    result = CliRunner().invoke(cli.app, ["apply", "-c", str(config)])
    assert result.exit_code == 0, (result.output, result.exception)
    assert events == [
        "plan-resources",
        "approve",
        "publish-ssh",
        "bind-routes",
        "inspect-created-guest",
        "stage",
        "activate",
        "routes",
    ]
    assert "add-routes-local" not in result.output
    state = CommandJournal("project-a", "gateway").snapshot()
    assert state["transaction"]["phase"] == "complete"
    assert state["transaction"]["plan"]["artifact_sha256"] == artifact
    assert set(state["transaction"]["receipts"]) == {
        "ssh",
        "provisioning",
        "guest:gateway-0",
        "routes",
    }
