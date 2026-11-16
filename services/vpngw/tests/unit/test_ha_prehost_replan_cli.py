"""Exercise prehost retirement through the public command and real child owners."""

from dataclasses import dataclass, field
from types import SimpleNamespace as NS

import pytest
from test_ha_prehost_replan import completed_credential_context
from test_ha_prehost_replan import context as context
from typer.testing import CliRunner

from nebius_vpngw import cli
from nebius_vpngw.deploy import apply_command, ha_command, security_group_apply
from nebius_vpngw.deploy.provisioning_trust import TrustJournal
from nebius_vpngw.deploy.recovery import error_chain
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStore


@pytest.mark.parametrize("decision", ["dry-run", "decline", "settle"])
def test_real_apply_retires_prehost_with_completed_iam_and_ssh(
    context, monkeypatch, tmp_path, decision
):
    journal, predecessor, options, enrollment = completed_credential_context(
        context, tmp_path, True, include_ssh=True
    )
    state = options["lifecycle"]
    path = tmp_path / "gateway.yaml"
    path.write_text("version: 1\n")
    store = VMHALifecycleStore(path)
    store.write_verified(state)
    trust = TrustJournal(state.project_id, state.gateway_name)
    local = dict(
        project_id=state.project_id,
        region_id="region",
        gateway_group=dict(name=state.gateway_name, vm_spec={}),
    )
    generation = NS(
        generation_id="9" * 64,
        digests=NS(configuration="8" * 64, static_routes="b" * 64, bgp_policy="c" * 64),
    )
    nodes = tuple(
        NS(instance_index=m.instance_index, node_id=m.node_id, role=NS(value=m.role))
        for m in state.members
    )
    instances = tuple(
        NS(
            hostname=m.instance_name,
            external_ip=m.public_ip,
            vm_ha_node=node,
            vm_ha_generation=generation,
        )
        for m, node in zip(state.members, nodes, strict=True)
    )
    ha = NS(cluster_id=state.cluster_id, members=nodes, generation=generation)
    plan = NS(
        vm_ha=ha,
        gateway_group=NS(name=state.gateway_name, region="region", vm_ha=ha, security_group_ids=()),
        gateway={},
        validate=lambda: None,
        iter_instance_configs=lambda: iter(instances),
    )

    @dataclass
    class Credentials:
        credentials: object = field(default_factory=lambda: options["credential_plan"].credentials)
        command_intent: object = None
        action: str = "reuse"
        command_action: str = "reuse"
        public_key_sha256: str = options["credential_plan"].public_key_sha256

        def approval_record(self):
            return self.command_intent or self.observation_record()

        def observation_record(self):
            return options["credential_plan"].observation_record()

    class Manager:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def discover_vm_ha_members(self, _):
            return {m.instance_name: m.public_ip for m in state.members}

        def observe_vm_ha_migration_state(self, *_):
            return options["cloud"]

        def verify_vm_ha_existing_identities(self, *_a, **_kw):
            pass

        def check_changes(self, _):
            return []

    reads = []

    def inspect_host(target, name, _local):
        assert target == next(m.public_ip for m in state.members if m.instance_name == name)
        reads.append(name)
        return options["hosts"][name]

    artifact = NS(sha256="2" * 64, dependency_plans=(), capabilities=())
    monkeypatch.setattr(cli, "load_local_config", lambda _: local)
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr(cli, "VMHALifecycleStore", lambda *_: store)
    monkeypatch.setattr(apply_command, "CommandJournal", lambda *_a, **_k: journal)
    monkeypatch.setattr(cli, "inspect_managed_vm_ha_credentials", lambda **_: Credentials())
    monkeypatch.setattr(cli, "_validate_vm_ha_lifecycle_credential_transition", lambda *_: None)
    monkeypatch.setattr(cli, "_apply_operator_auth_token", lambda: None)
    monkeypatch.setattr(cli, "VMManager", Manager)
    monkeypatch.setattr(cli, "require_vm_ha_ssh_policy", lambda *_a, **_k: options["ssh_policy"])
    monkeypatch.setattr(cli, "_vm_ha_missing_standby_replacement_plan", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "validate_retained_route_scope", lambda *_: None)
    monkeypatch.setattr(
        security_group_apply, "inspect_plan", lambda *_: {"desired_ids": [], "members": {}}
    )
    monkeypatch.setattr(cli, "_vm_ha_desired_approval_state", lambda _: {"desired": "changed"})
    monkeypatch.setattr(cli, "_resolve_vm_ha_agent_artifact", lambda _: artifact)
    monkeypatch.setattr(cli, "_plan_vm_ha_package_dependencies", lambda artifact, **_: artifact)
    monkeypatch.setattr(cli, "_vm_ha_observation_matches_bindings", lambda *_a, **_k: True)
    monkeypatch.setattr(cli, "_vm_ha_provisioning_members", lambda *_: state.members)
    monkeypatch.setattr(cli, "_vm_ha_apply_order_for_owner", lambda *_: instances)
    monkeypatch.setattr(cli, "SSHPush", lambda **_: NS(inspect_vm_ha_prehost=inspect_host))
    # Transports supply observations; the command, lifecycle validation, child
    # receipts, retirement and approval flow remain the production implementation.
    approvals, failures, successors = [], [], []
    approve = cli._approve_disruption

    def review(value, **kwargs):
        approvals.append((value, kwargs))
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
        return approve(value, **kwargs)

    monkeypatch.setattr(cli, "_approve_disruption", review)
    monkeypatch.setattr(
        cli.typer, "confirm", lambda *_a, **_k: decision == "settle" and len(approvals) == 1
    )
    failed = cli.ApplyReport.failed

    def record_failure(report, error):
        failures.extend((type(item).__name__, str(item)) for item in error_chain(error))
        return failed(report, error)

    monkeypatch.setattr(cli.ApplyReport, "failed", record_failure)
    create = ha_command.create

    def successor(*args, **kwargs):
        candidate = create(*args, **kwargs)
        successors.append(candidate)
        return candidate

    monkeypatch.setattr(ha_command, "create", successor)
    paths = (journal.path, enrollment.path, trust.path, store.path)
    before = {p: p.read_bytes() for p in paths}
    result = CliRunner().invoke(
        cli.app,
        ["apply", "--local-config-file", str(path)]
        + (["--dry-run"] if decision == "dry-run" else []),
    )
    assert result.exit_code == 0, failures
    assert all(p.read_bytes() == before[p] for p in paths[1:])
    if decision == "settle":
        assert len(approvals) == 2 and approvals[0][0] != approvals[1][0]
        assert approvals[1][1]["supplied"] is None
        assert journal.pending() is None
        assert any(p.read_bytes() == before[journal.path] for p in journal.root.rglob("*.json"))
        assert len(successors) == 1
        assert "credentials" not in successors[0].bindings
        assert "ssh" not in successors[0].bindings
        assert successors[0].intent["packages"]["checkpoint"] == state.record_sha256
        assert successors[0].plan.approval != predecessor.plan.approval
        assert reads.count("gateway-0") >= 3 and reads.count("gateway-1") >= 3
    else:
        assert len(approvals) == 1 and not successors
        assert journal.path.read_bytes() == before[journal.path]
        assert not (journal.root / "history").exists()
