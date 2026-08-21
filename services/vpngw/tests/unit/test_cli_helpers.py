from __future__ import annotations

import io
import json
import os
import shlex
import typing as t
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer
import yaml
from click.core import Command, Context
from rich.console import Console
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from nebius_vpngw import vpngw_sa
from nebius_vpngw.cli import (
    _COMMAND_EXAMPLES,
    _ROOT_HELP_EXAMPLES,
    _active_vm_ha_lifecycle_state,
    _build_status_ssh_context,
    _canonical_digest,
    _detect_connection_role_overrides,
    _detect_cross_connection_ecmp_warnings,
    _external_ips_assigned,
    _fetch_vm_ha_agent_status,
    _format_ecmp_warning_lines,
    _format_role_override_lines,
    _ipsec_status_reports_no_active_tunnels,
    _prepare_vm_ha_configured_passive_standby,
    _prepare_vm_ha_manual_failback_target,
    _registered_command_name,
    _render_vm_ha_status,
    _requested_apply_service_account_token,
    _require_vm_ha_manual_failover_target,
    _run_vm_ha_operator_command,
    _select_carrying_tunnel_for_connection,
    _serialize_explicit_vm_ha_apply,
    _should_prompt_add_routes_after_apply,
    _status_ssh_target_command,
    _update_external_ips_in_yaml,
    _validate_vm_ha_agent_status,
    _validate_vm_ha_display_status,
    _validate_vm_ha_planned_status,
    _vm_ha_activation_blockers,
    _vm_ha_activation_recovery_approval_state,
    _vm_ha_apply_order,
    _vm_ha_apply_order_for_owner,
    _vm_ha_bound_owner_node_id,
    _vm_ha_cloud_authority,
    _vm_ha_desired_approval_state,
    _vm_ha_failed_passive_replacement_plan,
    _vm_ha_member_failure_condition,
    _vm_ha_migration_plan_digest,
    _vm_ha_observation_matches_bindings,
    _vm_ha_status_runtime_binding,
    _vm_ha_status_view,
    _VMHAAgentStatusPermanent,
    _VMHAAgentStatusStale,
    _VMHACloudAuthority,
    _VMHAMemberEvidence,
    _VMHAStatusSSHUnavailable,
    _vpn_gateway_status_table,
    _wait_for_vm_ha_agent_status,
    app,
    status,
)
from nebius_vpngw.config_loader import (
    GatewayGroupSpec,
    InstanceResolvedConfig,
    ResolvedDeploymentPlan,
    load_local_config,
)
from nebius_vpngw.deploy.route_manager import NebiusSDKRouteBackend
from nebius_vpngw.deploy.vm_diff import ChangeType, VMDiff
from nebius_vpngw.deploy.vm_ha_cloud import AllocationOwner, InstanceCloudState
from nebius_vpngw.deploy.vm_ha_identity import FormerVMHAProvenance
from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
    VMHAMigrationTransaction,
)
from nebius_vpngw.schema import HARole, RoutingMode, VMHARouteTarget

HELP_ENV = {"COLUMNS": "120"}
EXPECTED_ROOT_COMMANDS = (
    "create-config",
    "configure-vm-ha",
    "prep-network",
    "validate-config",
    "apply",
    "status",
    "set-vm-ha-mtls",
    "vm-ha-rearm",
    "failover",
    "failback",
    "add-routes-local",
    "list-routes-local",
    "list-routes-remote",
    "restart-tunnel",
    "create-from-peer-config",
    "destroy",
)
EXPECTED_PUBLIC_COMMAND_PATHS = (
    ("create-config",),
    ("configure-vm-ha",),
    ("prep-network",),
    ("validate-config",),
    ("apply",),
    ("status",),
    ("set-vm-ha-mtls",),
    ("vm-ha-rearm",),
    ("failover",),
    ("failover", "vm"),
    ("failover", "tunnel"),
    ("failback",),
    ("failback", "vm"),
    ("failback", "tunnel"),
    ("add-routes-local",),
    ("list-routes-local",),
    ("list-routes-remote",),
    ("restart-tunnel",),
    ("create-from-peer-config",),
    ("destroy",),
)


def _public_command_paths(
    command: Command,
    prefix: tuple[str, ...] = (),
) -> list[tuple[str, ...]]:
    if not isinstance(command, TyperGroup):
        return []
    ctx = Context(command)
    paths: list[tuple[str, ...]] = []
    for name in command.list_commands(ctx):
        child = command.get_command(ctx, name)
        assert child is not None
        if child.hidden:
            continue
        path = (*prefix, name)
        paths.append(path)
        paths.extend(_public_command_paths(child, path))
    return paths


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Security Associations (0 up, 0 connecting):\n  none\n", True),
        ("no active connections\n", True),
        ("peer[1]: ESTABLISHED 1 minute ago\n", False),
        ("Security Associations (0 up, 0 connecting):\n", False),
    ],
)
def test_ipsec_status_distinguishes_cold_standby_from_parse_failure(
    output: str, expected: bool
) -> None:
    assert _ipsec_status_reports_no_active_tunnels(output) is expected


def test_vm_ha_binding_preflight_defers_standalone_resource_to_exact_sdk_read() -> None:
    observation = {"members": [], "route_targets": []}

    assert _vm_ha_observation_matches_bindings(
        observation,
        {"disk:nebius-vpn-gw-1": "disk-1"},
    )
    assert not _vm_ha_observation_matches_bindings(
        observation,
        {"compute:nebius-vpn-gw-1": "compute-1"},
    )


def _lifecycle_state(
    *, status: VMHALifecycleStatus = VMHALifecycleStatus.ACTIVE
) -> VMHALifecycleState:
    transaction = VMHAMigrationTransaction(
        operation_id="test-operation",
        approval_kind="migration",
        approval_digest="a" * 64,
        desired_state_digest="b" * 64,
        current_state_digest="c" * 64,
        checkpoint="fixture",
        pending_effect=None,
        completed_effects=(),
        resource_bindings=(("shared-allocation-id", "shared-private"),),
        revision=1,
        predecessor_sha256=None,
    )
    return VMHALifecycleState(
        status=status,
        project_id="project-test",
        gateway_name="nebius-vpn-gw",
        cluster_id="cluster",
        allocation_id="shared-private",
        allocation_name="nebius-vpn-gw-cluster-shared-private-ip",
        members=(
            VMHALifecycleMember(
                instance_index=0,
                instance_name="nebius-vpn-gw-0",
                node_id="node-active",
                role="active",
                compute_id="compute-0",
                network_interface_name="eth0",
                public_ip="203.0.113.10",
                compute_revision="11",
                disk_id="disk-0",
                network_interface_subnet_id="subnet-1",
                primary_allocation_id="primary-0",
                public_allocation_id="public-0",
                alias_allocation_ids=("shared-private",),
            ),
            VMHALifecycleMember(
                instance_index=1,
                instance_name="nebius-vpn-gw-1",
                node_id="node-passive",
                role="passive",
                compute_id="compute-1",
                network_interface_name="eth0",
                public_ip="203.0.113.11",
                compute_revision="12",
                disk_id="disk-1",
                network_interface_subnet_id="subnet-1",
                primary_allocation_id="primary-1",
                public_allocation_id="public-1",
            ),
        ),
        route_runtime_id="route-runtime",
        route_targets=("route-table-1:10.0.0.0/8",),
        transaction=transaction,
    )


def test_load_local_config_drops_unset_gateway_group_network_id_placeholder(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["gateway_group"]["network_id"] = "${NETWORK_ID}"
    config_path = tmp_path / "placeholder.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    loaded = load_local_config(config_path)

    assert loaded["gateway_group"]["network_id"] is None


def test_update_external_ips_in_yaml_is_idempotent(tmp_path: Path) -> None:
    config_path = tmp_path / "rewrite.config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "gateway_group:",
                '  name: "nebius-vpn-gw"',
                "  instance_count: 1",
                "  external_ips: []",
                '  # network_id: "vpcnetwork-abc123def456"',
                "  subnet:",
                '    name: "vpngw-subnet"',
                "    cidr: null",
                "    prefix_length: 24",
                "",
            ]
        ),
        encoding="utf-8",
    )

    external_ips = [["203.0.113.10"]]

    _update_external_ips_in_yaml(config_path, external_ips)
    first_render = config_path.read_text(encoding="utf-8")

    _update_external_ips_in_yaml(config_path, external_ips)
    second_render = config_path.read_text(encoding="utf-8")

    assert second_render == first_render
    assert '  external_ips:\n    - ["203.0.113.10"]\n' in first_render
    assert '  # network_id: "vpcnetwork-abc123def456"\n  subnet:\n' in first_render


def test_external_ips_assigned_ignores_placeholders() -> None:
    assert not _external_ips_assigned([["${VPNGW_IP}"], []])
    assert _external_ips_assigned([["203.0.113.10"], []])


def _static_route_plan() -> ResolvedDeploymentPlan:
    return ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        gateway={"local_prefixes": ["10.0.0.0/16"]},
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            )
        ],
        manage_routes=True,
    )


def test_should_prompt_add_routes_after_initial_static_creation() -> None:
    plan = _static_route_plan()
    changes = [
        (
            "nebius-vpn-gw-0",
            VMDiff(
                change_type=ChangeType.SAFE,
                differences=["VM does not exist (will create)"],
                destructive_fields=[],
            ),
        )
    ]

    assert _should_prompt_add_routes_after_apply(plan, changes, recreate_gw=False)
    assert not _should_prompt_add_routes_after_apply(plan, changes, recreate_gw=True)


def test_apply_prints_add_routes_hint_after_initial_static_creation(tmp_path: Path) -> None:
    config_path = tmp_path / "static.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    plan = _static_route_plan()
    changes = [
        (
            "nebius-vpn-gw-0",
            VMDiff(
                change_type=ChangeType.SAFE,
                differences=["VM does not exist (will create)"],
                destructive_fields=[],
            ),
        )
    ]

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def discover_former_vm_ha_candidate_members(
            self, spec, *, allow_unmarked_runtime_probe=False
        ):
            return {}

        def check_changes(self, spec) -> list[tuple[str, VMDiff]]:
            return changes

        def ensure_group(self, spec, recreate=False, local_prefixes=None) -> dict[str, str]:
            return {}

    class FakeSSHPush:
        def deactivate_vm_ha(self, target, cfg) -> bool:
            return False

        def push_config_and_reload(self, target, inst_cfg, cfg, *, fail_closed=False) -> None:
            return None

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 0
    assert "Apply completed successfully." in result.stdout
    assert "IMPORTANT: For static routing, run:" in result.stdout
    assert "add-routes-local --local-config-file" in result.stdout
    assert "<your-config.yaml>" not in result.stdout


def test_never_ha_sa_apply_selects_sa_before_compute_and_needs_no_operator_or_vpc_read(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ordinary.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    requests: list[object] = []
    sa_calls: list[str] = []
    trace: list[str] = []

    class AllocationService:
        def __init__(self, client) -> None:
            assert client is not None

        def list(self, request):
            requests.append(request)
            raise PermissionError("allocation list denied")

    class FakeSSHPush:
        def deactivate_vm_ha(self, target, cfg) -> bool:
            return False

        def push_config_and_reload(self, *args, **kwargs) -> None:
            return None

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=_static_route_plan()),
        patch(
            "nebius_vpngw.cli._ensure_authentication",
            side_effect=PermissionError("operator authentication is unavailable"),
        ) as operator_auth,
        patch(
            "nebius.api.nebius.vpc.v1.AllocationServiceClient",
            AllocationService,
        ),
        patch(
            "nebius_vpngw.deploy.vm_manager.VMManager._build_sdk_client",
            return_value=object(),
        ),
        patch(
            "nebius_vpngw.deploy.vm_manager.VMManager._get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: trace.append(f"compute:{name}"),
        ) as compute_read,
        patch(
            "nebius_vpngw.deploy.vm_manager.VMManager.check_changes",
            return_value=[],
        ) as check_changes,
        patch(
            "nebius_vpngw.deploy.vm_manager.VMManager.ensure_group",
            return_value={},
        ),
        patch(
            "nebius_vpngw.vpngw_sa.ensure_service_account_and_token",
            side_effect=lambda **kwargs: (
                sa_calls.append(kwargs["sa_name"]),
                trace.append("sa-token"),
                "sa-token",
            )[-1],
        ),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
    ):
        result = CliRunner().invoke(
            app,
            ["apply", "--local-config-file", str(config_path), "--sa", "test-sa"],
        )

    assert result.exit_code == 0, result.stdout
    check_changes.assert_called_once()
    operator_auth.assert_not_called()
    compute_read.assert_not_called()
    assert requests == []
    assert sa_calls == ["test-sa"]
    assert trace == ["sa-token"]
    assert "Analyzing configuration changes" in result.stdout


@pytest.mark.parametrize("sa_name", [None, "test-sa"], ids=("operator", "service-account"))
def test_unmarked_resources_do_not_trigger_implicit_ha_discovery_or_teardown(
    tmp_path: Path,
    sa_name: str | None,
) -> None:
    config_path = tmp_path / "ordinary.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    former = {
        "nebius-vpn-gw-0": "203.0.113.10",
        "nebius-vpn-gw-1": "203.0.113.11",
    }
    identities = {name: f"identity-{name}" for name in former}
    trace: list[str] = []

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            trace.append(f"manager:{kwargs.get('auth_token')}")
            self.provenance = None

        def discover_former_vm_ha_candidate_members(
            self, spec, *, allow_unmarked_runtime_probe=False, lifecycle_state=None
        ):
            if lifecycle_state is None:
                assert allow_unmarked_runtime_probe
                trace.append("candidate:unmarked")
            else:
                assert lifecycle_state.status is VMHALifecycleStatus.ACTIVE
                self.provenance = FormerVMHAProvenance.LIFECYCLE_STATE
                trace.append("candidate:persisted-active")
            return former

        @property
        def former_vm_ha_candidate_provenance(self):
            return self.provenance

        def verify_vm_ha_existing_identities(self, existing, **kwargs) -> None:
            assert existing == former
            trace.append("ssh-pins")

        def discover_former_vm_ha_members(
            self, spec, *, legacy_identities=None, lifecycle_state=None
        ):
            assert legacy_identities == identities
            trace.append(
                "classify:persisted" if lifecycle_state is not None else "classify:runtime"
            )
            return former

        def former_vm_ha_lifecycle_state(self, spec):
            trace.append("adopt")
            return _lifecycle_state()

        def verify_former_vm_ha_member_snapshot(self, spec, expected, **kwargs) -> None:
            assert expected == former
            assert kwargs.get("lifecycle_state") is not None
            trace.append(f"recheck:{kwargs['lifecycle_state'].status.value}")

        def check_changes(self, spec):
            trace.append("check-changes")
            return []

        def ensure_group(self, spec, recreate=False, local_prefixes=None):
            trace.append("ensure-group")
            return {"nebius-vpn-gw-0": former["nebius-vpn-gw-0"]}

        def wait_for_vm_network(self, *args, **kwargs) -> bool:
            return False

    class FakeSSHPush:
        def inspect_legacy_vm_ha_identity(self, target, name, cfg):
            trace.append(f"runtime:{name}")
            return identities[name]

        def deactivate_vm_ha(self, target, cfg, *, instance_name=None, retire_member=False) -> bool:
            trace.append(f"deactivate:{target}")
            return True

        def verify_vm_ha_deactivated(
            self, target, cfg, *, instance_name=None, retire_member=False
        ) -> None:
            trace.append(f"terminal:{target}")

        def push_config_and_reload(self, *args, **kwargs) -> None:
            return None

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=_static_route_plan()),
        patch(
            "nebius_vpngw.cli._ensure_authentication",
            side_effect=lambda **kwargs: trace.append("operator-auth") or "operator-token",
        ) as operator_auth,
        patch(
            "nebius_vpngw.vpngw_sa.ensure_service_account_and_token",
            side_effect=lambda **kwargs: trace.append("sa-auth") or "sa-token",
        ),
        patch("nebius_vpngw.cli.require_vm_ha_ssh_policy", return_value=object()),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
    ):
        args = ["apply", "--local-config-file", str(config_path)]
        if sa_name is not None:
            args.extend(["--sa", sa_name])
        result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.stdout
    auth_event = "sa-auth" if sa_name is not None else "operator-auth"
    token = "sa-token" if sa_name is not None else "operator-token"
    assert trace.index(auth_event) < trace.index(f"manager:{token}") < trace.index("check-changes")
    if sa_name is not None:
        operator_auth.assert_not_called()
    assert "candidate:unmarked" not in trace
    assert "ssh-pins" not in trace
    assert "classify:runtime" not in trace
    assert "adopt" not in trace
    assert not any(event.startswith("deactivate:") for event in trace)
    assert not any(event.startswith("terminal:") for event in trace)
    assert not VMHALifecycleStore(config_path).path.exists()


@pytest.mark.parametrize(
    ("lifecycle_record_version", "resume_after_writer_stop"),
    [(4, False), (3, False), (4, True)],
    ids=("v4", "v3-successor", "v4-resume-after-writer-stop"),
)
def test_ha_to_non_ha_deactivates_and_verifies_every_former_member_before_ensure_group(
    tmp_path: Path,
    lifecycle_record_version: int,
    resume_after_writer_stop: bool,
) -> None:
    config_path = tmp_path / "ordinary.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    lifecycle_store = VMHALifecycleStore(config_path)
    initial_lifecycle = replace(_lifecycle_state(), record_version=lifecycle_record_version)
    if resume_after_writer_stop:
        active_lifecycle = initial_lifecycle
        lifecycle_store.write_verified(active_lifecycle)
        initial_lifecycle = active_lifecycle.with_status(
            VMHALifecycleStatus.REMOVAL_IN_PROGRESS,
            checkpoint="removal-mutation-services-stopped",
        )
        lifecycle_store.write_verified(
            initial_lifecycle,
            predecessor_sha256=active_lifecycle.record_sha256,
        )
    if lifecycle_record_version == 3:
        lifecycle_store.path.write_text(
            json.dumps(initial_lifecycle.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
    elif not resume_after_writer_stop:
        lifecycle_store.write_verified(initial_lifecycle)
    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    plan = _static_route_plan()
    trace: list[tuple[object, ...]] = []
    former = {
        "nebius-vpn-gw-0": "203.0.113.10",
        "nebius-vpn-gw-1": "203.0.113.11",
    }
    manager_tokens: list[str | None] = []

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            manager_tokens.append(kwargs.get("auth_token"))

        def discover_former_vm_ha_candidate_members(self, spec, *, lifecycle_state=None):
            assert lifecycle_state == initial_lifecycle
            trace.append(("discover", spec.instance_count))
            return former

        @property
        def former_vm_ha_candidate_provenance(self):
            return FormerVMHAProvenance.LIFECYCLE_STATE

        def discover_former_vm_ha_members(
            self, spec, *, legacy_identities=None, lifecycle_state=None
        ):
            assert lifecycle_state == initial_lifecycle
            if resume_after_writer_stop:
                assert legacy_identities is None
            else:
                assert legacy_identities == {
                    "nebius-vpn-gw-0": "identity-nebius-vpn-gw-0",
                    "nebius-vpn-gw-1": "identity-nebius-vpn-gw-1",
                }
            return former

        def verify_vm_ha_existing_identities(self, existing, **kwargs) -> None:
            trace.append(("authenticate", tuple(existing)))

        def verify_former_vm_ha_member_snapshot(
            self, spec, expected, *, legacy_identities=None, lifecycle_state=None
        ) -> None:
            assert lifecycle_state is not None
            assert lifecycle_state.has_same_identity(_lifecycle_state())
            if lifecycle_state.status is VMHALifecycleStatus.ACTIVE:
                assert legacy_identities == {
                    "nebius-vpn-gw-0": "identity-nebius-vpn-gw-0",
                    "nebius-vpn-gw-1": "identity-nebius-vpn-gw-1",
                }
            trace.append(("recheck", tuple(expected)))

        def check_changes(self, spec):
            trace.append(("check_changes",))
            return []

        def ensure_group(self, spec, recreate=False, local_prefixes=None):
            trace.append(("ensure_group", spec.instance_count))
            return {"nebius-vpn-gw-0": former["nebius-vpn-gw-0"]}

        def wait_for_vm_network(self, *args, **kwargs) -> bool:
            return False

    class FakeSSHPush:
        def inspect_legacy_vm_ha_identity(self, target, name, cfg):
            trace.append(("inspect", name, target))
            return f"identity-{name}"

        def inhibit_vm_ha_removal(self, target, name, cfg, *, node_id, operation_id):
            assert not any(event[0] == "deactivate" for event in trace)
            trace.append(("inhibit", name, target))
            return {
                "schema": "nebius-vpngw/vm-ha-removal-inhibition-v1",
                "cluster_id": "cluster",
                "node_id": node_id,
                "generation_id": "a" * 64,
                "operation_id": operation_id,
            }

        def verify_vm_ha_removal_quiescent(self, target, name, cfg, *, inhibition) -> None:
            assert len([event for event in trace if event[0] == "inhibit"]) == 2
            trace.append(("quiescent", name, target))

        def stop_vm_ha_mutation_services(self, target, name, cfg) -> None:
            assert len([event for event in trace if event[0] == "quiescent"]) == 2
            trace.append(("stop-mutation", name, target))

        def deactivate_vm_ha(self, target, cfg, *, instance_name=None, retire_member=False) -> bool:
            assert instance_name in former
            assert former[instance_name] == target
            trace.append(("deactivate", target, retire_member))
            return True

        def verify_vm_ha_deactivated(
            self, target, cfg, *, instance_name=None, retire_member=False
        ) -> None:
            assert instance_name in former
            assert former[instance_name] == target
            trace.append(("verify", target, retire_member))

        def push_config_and_reload(self, *args, **kwargs) -> None:
            trace.append(("ordinary-push", args[0]))

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch(
            "nebius_vpngw.cli._ensure_authentication",
            side_effect=AssertionError(
                "operator authentication must not be used for service-account removal"
            ),
        ) as operator_auth,
        patch(
            "nebius_vpngw.vpngw_sa.ensure_service_account_and_token",
            side_effect=lambda **kwargs: trace.append(("sa-auth",)) or "sa-token",
        ),
        patch("nebius_vpngw.cli.require_vm_ha_ssh_policy", return_value=object()),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
    ):
        result = CliRunner().invoke(
            app, ["apply", "--local-config-file", str(config_path), "--sa", "test-sa"]
        )

    assert result.exit_code == 0, result.stdout
    ensure_index = trace.index(("ensure_group", 1))
    before_removal = [
        ("sa-auth",),
        ("discover", 1),
        ("authenticate", ("nebius-vpn-gw-0", "nebius-vpn-gw-1")),
    ]
    if not resume_after_writer_stop:
        before_removal.extend(
            [
                ("inspect", "nebius-vpn-gw-0", "203.0.113.10"),
                ("inspect", "nebius-vpn-gw-1", "203.0.113.11"),
            ]
        )
    before_removal.append(("check_changes",))
    if not resume_after_writer_stop:
        before_removal.extend(
            [
                ("inspect", "nebius-vpn-gw-0", "203.0.113.10"),
                ("inspect", "nebius-vpn-gw-1", "203.0.113.11"),
            ]
        )
    removal_trace = [
        ("recheck", ("nebius-vpn-gw-0", "nebius-vpn-gw-1")),
    ]
    if not resume_after_writer_stop:
        removal_trace.extend(
            [
                ("inhibit", "nebius-vpn-gw-0", "203.0.113.10"),
                ("inhibit", "nebius-vpn-gw-1", "203.0.113.11"),
                ("quiescent", "nebius-vpn-gw-0", "203.0.113.10"),
                ("quiescent", "nebius-vpn-gw-1", "203.0.113.11"),
                ("stop-mutation", "nebius-vpn-gw-0", "203.0.113.10"),
                ("stop-mutation", "nebius-vpn-gw-1", "203.0.113.11"),
            ]
        )
    removal_trace.extend(
        [
            ("deactivate", "203.0.113.10", False),
            ("deactivate", "203.0.113.11", True),
            ("verify", "203.0.113.10", False),
            ("verify", "203.0.113.11", True),
            ("recheck", ("nebius-vpn-gw-0", "nebius-vpn-gw-1")),
        ]
    )
    assert trace[:ensure_index] == before_removal + removal_trace
    operator_auth.assert_not_called()
    assert manager_tokens == ["sa-token", "sa-token"]
    terminal = lifecycle_store.read(
        expected_project_id="project-test",
        expected_gateway_name="nebius-vpn-gw",
    )
    assert terminal is not None
    assert terminal.record_version == 4
    assert terminal.status is VMHALifecycleStatus.REMOVED


@pytest.mark.parametrize("service_account_error", [None, RuntimeError("setup failed")])
def test_ha_to_non_ha_requested_sa_fails_closed_before_discovery(
    tmp_path: Path,
    service_account_error: RuntimeError | None,
) -> None:
    config_path = tmp_path / "ordinary.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    VMHALifecycleStore(config_path).write_verified(_lifecycle_state())
    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
    }

    def requested_service_account(**kwargs):
        if service_account_error is not None:
            raise service_account_error
        return None

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=_static_route_plan()),
        patch(
            "nebius_vpngw.cli._ensure_authentication",
            side_effect=AssertionError(
                "operator authentication must not be used for service-account removal"
            ),
        ) as operator_auth,
        patch(
            "nebius_vpngw.vpngw_sa.ensure_service_account_and_token",
            side_effect=requested_service_account,
        ),
        patch("nebius_vpngw.cli.VMManager") as vm_manager,
    ):
        result = CliRunner().invoke(
            app, ["apply", "--local-config-file", str(config_path), "--sa", "test-sa"]
        )

    assert result.exit_code == 1
    assert "ambient credential fallback is disabled" in result.stdout
    operator_auth.assert_not_called()
    vm_manager.assert_not_called()


def test_removed_tombstone_makes_consecutive_ordinary_sa_apply_teardown_free(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ordinary.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    VMHALifecycleStore(config_path).write_verified(_lifecycle_state())
    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    plan = _static_route_plan()
    former = {
        "nebius-vpn-gw-0": "203.0.113.10",
        "nebius-vpn-gw-1": "203.0.113.11",
    }
    trace: list[str] = []

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            trace.append(f"manager:{kwargs.get('auth_token')}")

        def discover_former_vm_ha_candidate_members(self, spec, *, lifecycle_state=None):
            trace.append("discover")
            return former

        @property
        def former_vm_ha_candidate_provenance(self):
            return FormerVMHAProvenance.LIFECYCLE_STATE

        def discover_former_vm_ha_members(
            self, spec, *, legacy_identities=None, lifecycle_state=None
        ):
            return former

        def verify_vm_ha_existing_identities(self, existing, **kwargs) -> None:
            return None

        def verify_former_vm_ha_member_snapshot(self, spec, expected, **kwargs) -> None:
            trace.append("recheck")

        def check_changes(self, spec):
            trace.append("analyze")
            return []

        def ensure_group(self, spec, recreate=False, local_prefixes=None):
            return {"nebius-vpn-gw-0": "203.0.113.10"}

        def wait_for_vm_network(self, *args, **kwargs) -> bool:
            return False

    class FakeSSHPush:
        def inspect_legacy_vm_ha_identity(self, target, name, cfg):
            return f"identity-{name}"

        def inhibit_vm_ha_removal(self, target, name, cfg, *, node_id, operation_id):
            trace.append(f"inhibit:{target}")
            return {
                "schema": "nebius-vpngw/vm-ha-removal-inhibition-v1",
                "cluster_id": "cluster",
                "node_id": node_id,
                "generation_id": "a" * 64,
                "operation_id": operation_id,
            }

        def verify_vm_ha_removal_quiescent(self, target, name, cfg, *, inhibition) -> None:
            trace.append(f"quiescent:{target}")

        def stop_vm_ha_mutation_services(self, target, name, cfg) -> None:
            trace.append(f"stop-mutation:{target}")

        def deactivate_vm_ha(self, target, cfg, *, instance_name=None, retire_member=False) -> bool:
            trace.append(f"deactivate:{target}")
            return True

        def verify_vm_ha_deactivated(
            self, target, cfg, *, instance_name=None, retire_member=False
        ) -> None:
            trace.append(f"verify:{target}")

        def push_config_and_reload(self, *args, **kwargs) -> None:
            trace.append("ordinary-push")

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch(
            "nebius_vpngw.cli._ensure_authentication",
            side_effect=lambda **kwargs: trace.append("operator-auth") or "operator-token",
        ) as operator_auth,
        patch(
            "nebius_vpngw.vpngw_sa.ensure_service_account_and_token",
            side_effect=lambda **kwargs: trace.append("sa-auth") or "sa-token",
        ),
        patch("nebius_vpngw.cli.require_vm_ha_ssh_policy", return_value=object()),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
    ):
        first = CliRunner().invoke(
            app, ["apply", "--local-config-file", str(config_path), "--sa", "test-sa"]
        )
        second = CliRunner().invoke(
            app, ["apply", "--local-config-file", str(config_path), "--sa", "test-sa"]
        )

    assert first.exit_code == second.exit_code == 0
    operator_auth.assert_not_called()
    assert trace.count("sa-auth") == 2
    assert trace.count("discover") == 1
    assert trace.count("analyze") == 2
    assert [item for item in trace if item.startswith("deactivate:")] == [
        "deactivate:203.0.113.10",
        "deactivate:203.0.113.11",
    ]
    assert trace.count("ordinary-push") == 2
    removed = VMHALifecycleStore(config_path).read(
        expected_project_id="project-test", expected_gateway_name="nebius-vpn-gw"
    )
    assert removed is not None and removed.status is VMHALifecycleStatus.REMOVED


@pytest.mark.parametrize("failure_stage", ["recheck", "deactivate", "verify"])
def test_ha_to_non_ha_failure_blocks_all_ordinary_provisioning(
    tmp_path: Path, failure_stage: str
) -> None:
    config_path = tmp_path / "ordinary.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    VMHALifecycleStore(config_path).write_verified(_lifecycle_state())
    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    plan = _static_route_plan()
    ordinary_effects: list[str] = []
    former = {
        "nebius-vpn-gw-0": "203.0.113.10",
        "nebius-vpn-gw-1": "203.0.113.11",
    }

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            self.provenance = FormerVMHAProvenance.CURRENT_MARKER

        def discover_former_vm_ha_candidate_members(
            self, spec, *, allow_unmarked_runtime_probe=False, lifecycle_state=None
        ):
            if lifecycle_state is not None:
                self.provenance = FormerVMHAProvenance.LIFECYCLE_STATE
            return former

        @property
        def former_vm_ha_candidate_provenance(self):
            return self.provenance

        def discover_former_vm_ha_members(
            self, spec, *, legacy_identities=None, lifecycle_state=None
        ):
            return former

        def former_vm_ha_lifecycle_state(self, spec):
            return _lifecycle_state()

        def verify_vm_ha_existing_identities(self, existing, **kwargs) -> None:
            return None

        def verify_former_vm_ha_member_snapshot(
            self, spec, expected, *, legacy_identities=None, lifecycle_state=None
        ) -> None:
            if failure_stage == "recheck":
                raise RuntimeError("identity changed")

        def check_changes(self, spec):
            return []

        def ensure_group(self, spec, recreate=False, local_prefixes=None):
            ordinary_effects.append("ensure_group")
            return {}

    class FakeSSHPush:
        def inspect_legacy_vm_ha_identity(self, target, name, cfg):
            return f"identity-{name}"

        def inhibit_vm_ha_removal(self, target, name, cfg, *, node_id, operation_id):
            return {
                "schema": "nebius-vpngw/vm-ha-removal-inhibition-v1",
                "cluster_id": "cluster",
                "node_id": node_id,
                "generation_id": "a" * 64,
                "operation_id": operation_id,
            }

        def verify_vm_ha_removal_quiescent(self, target, name, cfg, *, inhibition) -> None:
            return None

        def stop_vm_ha_mutation_services(self, target, name, cfg) -> None:
            return None

        def deactivate_vm_ha(self, target, cfg, *, instance_name=None, retire_member=False) -> bool:
            if failure_stage == "deactivate":
                raise RuntimeError("remote command failed")
            return True

        def verify_vm_ha_deactivated(
            self, target, cfg, *, instance_name=None, retire_member=False
        ) -> None:
            if failure_stage == "verify":
                raise RuntimeError("teardown incomplete")

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="discovery-token"),
        patch("nebius_vpngw.cli.require_vm_ha_ssh_policy", return_value=object()),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 1
    assert "Former VM-HA teardown failed before ordinary provisioning" in result.stdout
    assert ordinary_effects == []


@pytest.mark.parametrize("recreate_gw", [False, True])
def test_ha_to_non_ha_destructive_abort_happens_before_teardown(
    tmp_path: Path, recreate_gw: bool
) -> None:
    config_path = tmp_path / "ordinary.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    plan = _static_route_plan()
    teardown: list[str] = []
    former = {
        "nebius-vpn-gw-0": "203.0.113.10",
        "nebius-vpn-gw-1": "203.0.113.11",
    }
    destructive = VMDiff(
        change_type=ChangeType.DESTRUCTIVE,
        differences=["boot disk shape changed"],
        destructive_fields=["boot_disk"],
    )

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            self.provenance = FormerVMHAProvenance.CURRENT_MARKER

        def discover_former_vm_ha_candidate_members(
            self, spec, *, allow_unmarked_runtime_probe=False, lifecycle_state=None
        ):
            if lifecycle_state is not None:
                self.provenance = FormerVMHAProvenance.LIFECYCLE_STATE
            return former

        @property
        def former_vm_ha_candidate_provenance(self):
            return self.provenance

        def discover_former_vm_ha_members(
            self, spec, *, legacy_identities=None, lifecycle_state=None
        ):
            return former

        def former_vm_ha_lifecycle_state(self, spec):
            return _lifecycle_state()

        def verify_vm_ha_existing_identities(self, existing, **kwargs) -> None:
            return None

        def check_changes(self, spec):
            return [("nebius-vpn-gw-0", destructive)]

    class FakeSSHPush:
        def inspect_legacy_vm_ha_identity(self, target, name, cfg):
            return f"identity-{name}"

        def deactivate_vm_ha(self, *args, **kwargs) -> bool:
            teardown.append("deactivate")
            return True

    args = ["apply", "--local-config-file", str(config_path)]
    if recreate_gw:
        args.append("--recreate-gw")
    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli.require_vm_ha_ssh_policy", return_value=object()),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
    ):
        result = CliRunner().invoke(app, args, input="n\n")

    assert result.exit_code == (0 if recreate_gw else 1)
    assert teardown == []


def test_apply_waits_for_esp4_ready_before_config_push(tmp_path: Path) -> None:
    config_path = tmp_path / "static.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    plan = _static_route_plan()
    pushed_targets: list[str] = []

    pending_health = {
        "reachable": True,
        "cloud_init_complete": True,
        "strongswan_installed": True,
        "frr_installed": True,
        "agent_installed": False,
        "esp4_ready": False,
        "esp4_reboot_pending": True,
        "message": "ESP4/kernel update prepared; waiting for gateway reboot",
    }
    ready_health = {
        "reachable": True,
        "cloud_init_complete": True,
        "strongswan_installed": True,
        "frr_installed": True,
        "agent_installed": True,
        "esp4_ready": True,
        "esp4_reboot_pending": False,
        "message": "VM ready",
    }
    health_results = [pending_health, pending_health, ready_health]

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def discover_former_vm_ha_candidate_members(
            self, spec, *, allow_unmarked_runtime_probe=False
        ):
            return {}

        def discover_vm_ha_members(self, spec):
            return {}

        def verify_vm_ha_existing_identities(
            self, existing, *, policy=None, username="ubuntu"
        ) -> None:
            assert existing == {}

        def check_changes(self, spec) -> list[tuple[str, VMDiff]]:
            return []

        def ensure_group(self, spec, recreate=False, local_prefixes=None) -> dict[str, str]:
            return {"nebius-vpn-gw-0": "203.0.113.10"}

        def wait_for_vm_network(self, vm_name, vm_ip, timeout=180) -> bool:
            return True

        def check_vm_health(self, vm_name, vm_ip) -> dict[str, object]:
            return health_results.pop(0)

    class FakeSSHPush:
        def deactivate_vm_ha(self, target, cfg) -> bool:
            return False

        def push_config_and_reload(self, target, inst_cfg, cfg, *, fail_closed=False) -> None:
            pushed_targets.append(target)

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
        patch("time.sleep", return_value=None),
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 0
    assert pushed_targets == ["203.0.113.10"]
    assert "waiting for reboot" in result.stdout
    assert "Config push gate passed" in result.stdout


def test_prep_network_allows_missing_peer_psk_placeholders(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["connections"][0]["tunnels"][0]["psk"] = "${GCP_TUNNEL_1_PSK}"
    sample_config["connections"][0]["tunnels"].append(
        {
            "name": "tunnel-2",
            "gateway_instance_index": 0,
            "ha_role": "passive",
            "remote_public_ip": "198.51.100.11",
            "psk": "${GCP_TUNNEL_2_PSK}",
            "inner_cidr": "169.254.19.0/30",
            "inner_local_ip": "169.254.19.1",
            "inner_remote_ip": "169.254.19.2",
        }
    )
    config_path = tmp_path / "prep-network.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def prepare_network(
            self,
            spec: GatewayGroupSpec,
            *,
            allocate_ips: bool = True,
            desired_external_ips: list[list[str]] | None = None,
        ) -> list[list[str]]:
            assert spec.name == "nebius-vpn-gw"
            assert spec.instance_count == 1
            assert allocate_ips is True
            assert desired_external_ips == []
            return [["203.0.113.10"]]

    with (
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli._update_external_ips_in_yaml", return_value=None),
    ):
        result = CliRunner().invoke(app, ["prep-network", "--local-config-file", str(config_path)])

    assert result.exit_code == 0
    assert "Reserved public IPs:" in result.stdout
    assert "203.0.113.10" in result.stdout


def test_prep_network_reports_allocations_when_yaml_update_fails(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    config_path = tmp_path / "prep-network-update-failure.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def prepare_network(
            self,
            spec: GatewayGroupSpec,
            *,
            allocate_ips: bool = True,
            desired_external_ips: list[list[str]] | None = None,
        ) -> list[list[str]]:
            return [["203.0.113.20"]]

    with (
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch(
            "nebius_vpngw.cli._update_external_ips_in_yaml",
            side_effect=OSError("read-only config"),
        ),
    ):
        result = CliRunner().invoke(
            app,
            ["prep-network", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 1
    assert "Reserved public IPs:" in result.stdout
    assert "203.0.113.20" in result.stdout
    assert "Failed to update YAML with allocated IPs" in result.stdout
    assert "read-only config" in result.stdout


def test_prep_network_rejects_missing_project_before_authentication(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["project_id"] = "${PROJECT_ID}"
    config_path = tmp_path / "prep-network-missing-project.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    with patch("nebius_vpngw.cli._ensure_authentication") as authenticate:
        result = CliRunner().invoke(
            app,
            ["prep-network", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 1
    authenticate.assert_not_called()
    assert "project_id is required for prep-network" in result.stdout


def test_select_carrying_tunnel_is_scoped_per_connection() -> None:
    tunnel_bgp_map = {
        "nebius-vpn-gw-0": {
            "conn1-active": "169.254.10.2",
            "conn1-passive": "169.254.11.2",
            "conn2-active": "169.254.12.2",
            "conn2-passive": "169.254.13.2",
        }
    }
    tunnel_role_map = {
        "nebius-vpn-gw-0": {
            "conn1-active": "active",
            "conn1-passive": "passive",
            "conn2-active": "active",
            "conn2-passive": "passive",
        }
    }
    tunnel_connection_map = {
        "nebius-vpn-gw-0": {
            "conn1-active": "conn1",
            "conn1-passive": "conn1",
            "conn2-active": "conn2",
            "conn2-passive": "conn2",
        }
    }
    tunnel_statuses = {
        "conn1-active": "ESTABLISHED",
        "conn1-passive": "ESTABLISHED",
        "conn2-active": "ESTABLISHED",
        "conn2-passive": "ESTABLISHED",
    }
    bgp_states = {
        "169.254.10.2": "Established",
        "169.254.11.2": "Established",
        "169.254.12.2": "Established",
        "169.254.13.2": "Established",
    }
    tunnel_names = list(tunnel_statuses.keys())

    assert (
        _select_carrying_tunnel_for_connection(
            "nebius-vpn-gw-0",
            "conn1",
            tunnel_names,
            tunnel_statuses,
            bgp_states,
            tunnel_bgp_map,
            tunnel_role_map,
            tunnel_connection_map,
        )
        == "conn1-active"
    )
    assert (
        _select_carrying_tunnel_for_connection(
            "nebius-vpn-gw-0",
            "conn2",
            tunnel_names,
            tunnel_statuses,
            bgp_states,
            tunnel_bgp_map,
            tunnel_role_map,
            tunnel_connection_map,
        )
        == "conn2-active"
    )


def test_detect_connection_role_overrides_reports_manual_failover() -> None:
    tunnel_bgp_map = {
        "nebius-vpn-gw-0": {
            "conn-active": "169.254.10.2",
            "conn-passive": "169.254.11.2",
        }
    }
    tunnel_role_map = {
        "nebius-vpn-gw-0": {
            "conn-active": "active",
            "conn-passive": "passive",
        }
    }
    tunnel_connection_map = {
        "nebius-vpn-gw-0": {
            "conn-active": "gcp-ha-vpn",
            "conn-passive": "gcp-ha-vpn",
        }
    }
    tunnel_statuses = {
        "conn-active": "ESTABLISHED",
        "conn-passive": "ESTABLISHED",
    }
    bgp_states = {
        "169.254.10.2": "Idle (Admin)",
        "169.254.11.2": "Established",
    }

    overrides = _detect_connection_role_overrides(
        "nebius-vpn-gw-0",
        ["conn-active", "conn-passive"],
        tunnel_statuses,
        bgp_states,
        tunnel_bgp_map,
        tunnel_role_map,
        tunnel_connection_map,
    )

    assert overrides == [
        {
            "connection": "gcp-ha-vpn",
            "configured_active_tunnel": "conn-active",
            "selected_tunnel": "conn-passive",
            "reason": "manual failover",
            "detail": "configured active tunnel BGP is administratively down",
        }
    ]

    warning_lines = _format_role_override_lines({"nebius-vpn-gw-0": overrides})
    assert "Configured roles remain unchanged by design." in warning_lines[1]
    assert "Current traffic path: conn-passive" in "\n".join(warning_lines)


def test_detect_cross_connection_ecmp_warnings_for_active_paths() -> None:
    routes = {
        "10.10.0.0/24": [
            {"peerId": "169.254.10.2", "multipath": True},
            {"peerId": "169.254.12.2", "multipath": True},
            {"peerId": "169.254.11.2", "multipath": False},
            {"peerId": "169.254.13.2", "multipath": False},
        ]
    }
    peer_connection_map = {
        "169.254.10.2": "gcp-ha-vpn",
        "169.254.11.2": "gcp-ha-vpn",
        "169.254.12.2": "gcp-ha-vpn2",
        "169.254.13.2": "gcp-ha-vpn2",
    }
    peer_tunnel_map = {
        "169.254.10.2": "nebius-204-12-170-147-tunnel-1",
        "169.254.11.2": "nebius-204-12-170-147-tunnel-2",
        "169.254.12.2": "gcp-ha-vpn2-tunnel-1",
        "169.254.13.2": "gcp-ha-vpn2-tunnel-2",
    }
    peer_role_map = {
        "169.254.10.2": "active",
        "169.254.11.2": "passive",
        "169.254.12.2": "active",
        "169.254.13.2": "passive",
    }

    warnings = _detect_cross_connection_ecmp_warnings(
        routes,
        peer_connection_map,
        peer_tunnel_map,
        peer_role_map,
    )

    assert len(warnings) == 1
    assert warnings[0]["prefix"] == "10.10.0.0/24"
    assert warnings[0]["connections"] == ["gcp-ha-vpn", "gcp-ha-vpn2"]


def test_format_ecmp_warning_lines_groups_prefix_and_tunnels() -> None:
    warning_lines = _format_ecmp_warning_lines(
        {
            "nebius-vpn-gw-0": [
                {
                    "prefix": "10.10.0.0/24",
                    "entries": [
                        {
                            "connection": "gcp-ha-vpn",
                            "tunnel": "nebius-204-12-170-147-tunnel-1",
                            "peer_ip": "169.254.10.2",
                        },
                        {
                            "connection": "gcp-ha-vpn2",
                            "tunnel": "gcp-ha-vpn2-tunnel-1",
                            "peer_ip": "169.254.12.2",
                        },
                    ],
                }
            ]
        }
    )

    assert "Gateway VM: nebius-vpn-gw-0" in warning_lines
    assert "  Overlapping prefix: 10.10.0.0/24" in warning_lines
    assert "  Active tunnels carrying this prefix:" in warning_lines
    assert "    - nebius-204-12-170-147-tunnel-1 (connection: gcp-ha-vpn)" in warning_lines
    assert "    - gcp-ha-vpn2-tunnel-1 (connection: gcp-ha-vpn2)" in warning_lines


def test_cli_help_command_order() -> None:
    click_app = get_command(app)
    command_names = click_app.list_commands(Context(click_app))

    assert command_names == list(EXPECTED_ROOT_COMMANDS)
    assert [_registered_command_name(command) for command in app.registered_commands] == [
        name for name in EXPECTED_ROOT_COMMANDS if name not in {"failover", "failback"}
    ]
    for group_name in ("failover", "failback"):
        group = click_app.commands[group_name]
        assert isinstance(group, TyperGroup)
        assert group.list_commands(Context(group)) == ["vm", "tunnel"]


def test_vm_ha_apply_order_is_passive_first_and_non_ha_order_is_unchanged() -> None:
    active = SimpleNamespace(vm_ha_node=SimpleNamespace(role=SimpleNamespace(value="active")))
    passive = SimpleNamespace(vm_ha_node=SimpleNamespace(role=SimpleNamespace(value="passive")))
    ha_plan = SimpleNamespace(vm_ha=object(), iter_instance_configs=lambda: iter([active, passive]))
    ordinary_plan = SimpleNamespace(
        vm_ha=None, iter_instance_configs=lambda: iter([active, passive])
    )

    assert _vm_ha_apply_order(ha_plan) == [passive, active]
    assert _vm_ha_apply_order(ordinary_plan) == [active, passive]


def test_vm_ha_managed_apply_orders_exact_non_owner_before_promoted_owner() -> None:
    active = SimpleNamespace(
        vm_ha_node=SimpleNamespace(
            node_id="node-active",
            role=SimpleNamespace(value="active"),
        )
    )
    passive = SimpleNamespace(
        vm_ha_node=SimpleNamespace(
            node_id="node-passive",
            role=SimpleNamespace(value="passive"),
        )
    )
    plan = SimpleNamespace(
        vm_ha=object(),
        iter_instance_configs=lambda: iter([active, passive]),
    )
    lifecycle = _lifecycle_state(status=VMHALifecycleStatus.ACTIVATING)
    assert lifecycle.transaction is not None
    lifecycle = replace(
        lifecycle,
        transaction=replace(
            lifecycle.transaction,
            resource_bindings=(
                ("shared-allocation-id", "shared-private"),
                ("shared-allocation-owner-compute", "compute-1"),
                ("shared-allocation-owner-nic", "eth0"),
            ),
        ),
    )
    binding = SimpleNamespace(
        nodes=(
            SimpleNamespace(
                node_id="node-active",
                compute_id="compute-0",
                network_interface_name="eth0",
            ),
            SimpleNamespace(
                node_id="node-passive",
                compute_id="compute-1",
                network_interface_name="eth0",
            ),
        )
    )

    owner = _vm_ha_bound_owner_node_id(binding, lifecycle)

    assert owner == "node-passive"
    assert _vm_ha_apply_order_for_owner(plan, owner) == [active, passive]


def test_vm_ha_authoritative_binding_materializes_exact_current_owner() -> None:
    previous = _lifecycle_state(status=VMHALifecycleStatus.PROVISIONING)
    members = (
        replace(previous.members[0], alias_allocation_ids=()),
        replace(previous.members[1], alias_allocation_ids=("shared-private",)),
    )
    binding = SimpleNamespace(
        cluster_id="cluster",
        shared_allocation_id="shared-private",
        route_runtime_id="route-runtime",
        route_targets=(
            VMHARouteTarget(
                project_id="project-test",
                network_id="network-a",
                workload_subnet_id="subnet-a",
                route_table_id="route-table-a",
            ),
        ),
        nodes=(
            SimpleNamespace(
                node_id="node-active",
                role=SimpleNamespace(value="active"),
                compute_id="compute-0",
                network_interface_name="eth0",
            ),
            SimpleNamespace(
                node_id="node-passive",
                role=SimpleNamespace(value="passive"),
                compute_id="compute-1",
                network_interface_name="eth0",
            ),
        ),
    )
    plan = SimpleNamespace(gateway_group=SimpleNamespace(name="nebius-vpn-gw"))

    state = _active_vm_ha_lifecycle_state(
        plan=plan,
        runtime_binding=binding,
        members=members,
        project_id="project-test",
        previous=previous,
        status=VMHALifecycleStatus.ACTIVATING,
    )

    assert state.transaction is not None
    bindings = dict(state.transaction.resource_bindings)
    assert bindings["shared-allocation-owner-compute"] == "compute-1"
    assert bindings["shared-allocation-owner-nic"] == "eth0"

    with pytest.raises(RuntimeError, match="no exact shared-alias owner"):
        _active_vm_ha_lifecycle_state(
            plan=plan,
            runtime_binding=binding,
            members=tuple(replace(member, alias_allocation_ids=()) for member in members),
            project_id="project-test",
            previous=previous,
            status=VMHALifecycleStatus.ACTIVATING,
        )


def test_vm_ha_approval_digest_binds_current_revisions_and_recovery_domain() -> None:
    members = (
        SimpleNamespace(
            instance_index=0,
            node_id="node-a",
            role=SimpleNamespace(value="active"),
        ),
        SimpleNamespace(
            instance_index=1,
            node_id="node-b",
            role=SimpleNamespace(value="passive"),
        ),
    )
    generation = SimpleNamespace(
        generation_id="a" * 64,
        digests=SimpleNamespace(
            configuration="a" * 64,
            static_routes="b" * 64,
            bgp_policy="c" * 64,
        ),
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(
            cluster_id="cluster-a",
            members=members,
            generation=generation,
        ),
        gateway_group=SimpleNamespace(name="gateway"),
    )
    observation = {
        "members": [
            {
                "compute_id": "compute-a",
                "compute_revision": "11",
                "instance_name": "gateway-0",
                "present": True,
            },
            {"instance_name": "gateway-1", "present": False},
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {"present": False},
    }

    migration = _vm_ha_migration_plan_digest(
        plan,
        observation,
        approval_kind="migration",
    )
    recovery = _vm_ha_migration_plan_digest(
        plan,
        observation,
        approval_kind="recovery",
    )
    changed = json.loads(json.dumps(observation))
    changed["members"][0]["compute_revision"] = "12"

    assert migration != recovery
    assert migration != _vm_ha_migration_plan_digest(
        plan,
        changed,
        approval_kind="migration",
    )


def test_activation_recovery_approval_requires_exact_configured_active_reset() -> None:
    plan_members = (
        SimpleNamespace(
            instance_index=0,
            node_id="node-active",
            role=SimpleNamespace(value="active"),
        ),
        SimpleNamespace(
            instance_index=1,
            node_id="node-passive",
            role=SimpleNamespace(value="passive"),
        ),
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(
            cluster_id="cluster",
            members=plan_members,
            generation=SimpleNamespace(
                generation_id="a" * 64,
                digests=SimpleNamespace(
                    configuration="a" * 64,
                    static_routes="b" * 64,
                    bgp_policy="c" * 64,
                ),
            ),
        ),
        gateway_group=SimpleNamespace(name="nebius-vpn-gw"),
    )
    lifecycle = _lifecycle_state(status=VMHALifecycleStatus.ACTIVATING)
    assert lifecycle.transaction is not None
    lifecycle = replace(
        lifecycle,
        members=(
            replace(lifecycle.members[0], alias_allocation_ids=()),
            replace(lifecycle.members[1], alias_allocation_ids=("shared-private",)),
        ),
        transaction=replace(
            lifecycle.transaction,
            desired_state_digest=_canonical_digest(_vm_ha_desired_approval_state(plan)),
            checkpoint="before-activate-node-passive",
            pending_effect="activate-node-passive",
            resource_bindings=(
                ("shared-allocation-id", "shared-private"),
                ("shared-allocation-owner-compute", "compute-1"),
                ("shared-allocation-owner-nic", "eth0"),
            ),
        ),
    )
    observation = {
        "members": [
            {
                "aliases": ["shared-private"],
                "boot_disk_id": "disk-0",
                "compute_id": "compute-0",
                "compute_revision": "13",
                "instance_name": "nebius-vpn-gw-0",
                "network_interface_name": "eth0",
                "primary_allocation_id": "primary-0",
                "public_allocation_id": "public-0",
                "public_ip": "203.0.113.10",
                "subnet_id": "subnet-1",
                "present": True,
            },
            {
                "aliases": [],
                "boot_disk_id": "disk-1",
                "compute_id": "compute-1",
                "compute_revision": "14",
                "instance_name": "nebius-vpn-gw-1",
                "network_interface_name": "eth0",
                "primary_allocation_id": "primary-1",
                "public_allocation_id": "public-1",
                "public_ip": "203.0.113.11",
                "subnet_id": "subnet-1",
                "present": True,
            },
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "shared-private",
            "owner": {
                "compute_id": "compute-0",
                "network_interface_name": "eth0",
            },
            "present": True,
        },
    }

    approval = _vm_ha_activation_recovery_approval_state(
        plan,
        lifecycle,
        observation,
    )

    assert approval["lifecycle_record_sha256"] == lifecycle.record_sha256
    assert approval["recovery_mode"] == "configured-active-fenced-reset"

    foreign = json.loads(json.dumps(observation))
    foreign["members"][0]["boot_disk_id"] = "foreign-disk"
    with pytest.raises(ValueError, match="member identity changed"):
        _vm_ha_activation_recovery_approval_state(plan, lifecycle, foreign)

    stale_owner = json.loads(json.dumps(observation))
    stale_owner["shared_allocation"]["owner"]["compute_id"] = "compute-1"
    with pytest.raises(ValueError, match="configured-active cloud owner"):
        _vm_ha_activation_recovery_approval_state(plan, lifecycle, stale_owner)


def test_failed_passive_replacement_digest_binds_checkpoint_and_exact_observation() -> None:
    members = (
        VMHALifecycleMember(
            instance_index=0,
            instance_name="gateway-0",
            node_id="node-active",
            role="active",
            compute_id="compute-0",
            network_interface_name="eth0",
            public_ip="203.0.113.10",
            compute_revision="10",
            disk_id="disk-0",
            network_interface_subnet_id="subnet-1",
            primary_allocation_id="primary-0",
            public_allocation_id="public-0",
            alias_allocation_ids=("shared-1",),
        ),
        VMHALifecycleMember(
            instance_index=1,
            instance_name="gateway-1",
            node_id="node-passive",
            role="passive",
            compute_id="compute-1",
            network_interface_name="eth0",
            public_ip="203.0.113.11",
            compute_revision="11",
            disk_id="disk-1",
            network_interface_subnet_id="subnet-1",
            primary_allocation_id="primary-1",
            public_allocation_id="public-1",
        ),
    )
    original = {
        "members": [
            {
                "aliases": ["shared-1"],
                "boot_disk_id": "disk-0",
                "compute_id": "compute-0",
                "compute_revision": "10",
                "instance_name": "gateway-0",
                "present": True,
            },
            {
                "aliases": [],
                "boot_disk_id": "disk-1",
                "compute_id": "compute-1",
                "compute_revision": "11",
                "instance_name": "gateway-1",
                "present": True,
            },
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "shared-1",
            "owner": {"compute_id": "compute-0", "network_interface_name": "eth0"},
            "present": True,
        },
    }
    state = VMHALifecycleState.start_provisioning(
        project_id="project-test",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_name="gateway-cluster-shared-private-ip",
        members=members,
        operation_id="replacement-operation",
        approval_kind="migration",
        approval_digest="a" * 64,
        desired_state_digest="b" * 64,
        current_state_digest="c" * 64,
        initial_resource_bindings={
            "compute:gateway-0": "compute-0",
            "compute:gateway-1": "compute-1",
            "disk:gateway-0": "disk-0",
            "disk:gateway-1": "disk-1",
            "primary-allocation:gateway-1:eth0": "primary-1",
            "public-allocation:gateway-1:eth0": "public-1",
        },
        current_observation=original,
    )
    assert state.transaction is not None
    transaction = state.transaction.advance(
        predecessor_sha256=state.record_sha256,
        completed_effect="provision-gateway-1-boot-disk",
    )
    state = replace(state, transaction=transaction)
    state = replace(
        state,
        transaction=transaction.advance(
            predecessor_sha256=state.record_sha256,
            completed_effect="provision-gateway-1-compute",
        ),
    )
    plan = SimpleNamespace(vm_ha=object())
    current = json.loads(json.dumps(original))
    current["members"][1]["compute_revision"] = "12"

    passive_name, digest = _vm_ha_failed_passive_replacement_plan(plan, state, current)

    assert passive_name == "gateway-1"
    assert len(digest) == 64
    changed = json.loads(json.dumps(current))
    changed["members"][0]["compute_revision"] = "changed"
    with pytest.raises(ValueError, match="unrelated cloud drift"):
        _vm_ha_failed_passive_replacement_plan(plan, state, changed)


def test_vm_ha_apply_stops_before_external_mutation_when_runtime_is_blocked(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "vm-ha-blocked.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    plan = SimpleNamespace(
        vm_ha=object(), validate=lambda: None, iter_instance_configs=lambda: iter(())
    )

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value={}),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch(
            "nebius_vpngw.cli._vm_ha_activation_blockers",
            return_value=("authoritative-runtime-unavailable",),
        ),
        patch("nebius_vpngw.cli.VMManager") as manager,
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 1
    assert "BLOCKED before external mutation" in result.stdout
    manager.assert_not_called()


def test_vm_ha_apply_rejects_missing_host_trust_after_read_only_discovery(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    members = (
        SimpleNamespace(hostname="gateway-0", external_ip="203.0.113.10"),
        SimpleNamespace(hostname="gateway-1", external_ip="203.0.113.11"),
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
        gateway_group=SimpleNamespace(name="gateway", region="eu-west1"),
        validate=lambda: None,
        iter_instance_configs=lambda: iter(members),
    )
    monkeypatch.delenv("VPNGW_SSH_KNOWN_HOSTS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    calls: list[str] = []

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            calls.append("construct")

        def discover_vm_ha_members(self, spec):
            calls.append("discover")
            return {member.hostname: member.external_ip for member in members}

    with (
        patch(
            "nebius_vpngw.cli.load_local_config",
            return_value={
                "tenant_id": "tenant-a",
                "project_id": "project-a",
                "region_id": "eu-west1",
            },
        ),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 1
    assert "SSH trust preflight failed before external mutation" in result.stdout
    assert "VPNGW_SSH_HOST_KEYS_DIR is required for VM-HA managed trust repair" in result.stdout
    assert calls == ["construct", "discover"]


def test_vm_ha_nebius_credential_failure_precedes_auth_manager_and_cloud(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "vm-ha-invalid-credentials.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    with (
        patch(
            "nebius_vpngw.cli.load_local_config",
            side_effect=ValueError("VM-HA Nebius credentials for node-a are unavailable"),
        ),
        patch("nebius_vpngw.cli._ensure_authentication") as authenticate,
        patch("nebius_vpngw.cli.VMManager") as manager,
        patch("nebius_vpngw.cli.VMHACloudAdapter") as cloud,
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 1
    assert isinstance(result.exception, ValueError)
    assert "Nebius credentials for node-a are unavailable" in str(result.exception)
    authenticate.assert_not_called()
    manager.assert_not_called()
    cloud.assert_not_called()


@pytest.mark.parametrize("with_removed_tombstone", [False, True])
def test_vm_ha_migration_dry_run_previews_without_mutation(
    tmp_path: Path,
    with_removed_tombstone: bool,
) -> None:
    config_path = tmp_path / "vm-ha-migration.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    lifecycle_store = VMHALifecycleStore(config_path)
    removed = _lifecycle_state(status=VMHALifecycleStatus.REMOVED)
    if with_removed_tombstone:
        lifecycle_store.write_verified(removed)

    generation = SimpleNamespace(
        generation_id="a" * 64,
        digests=SimpleNamespace(
            configuration="a" * 64,
            static_routes="b" * 64,
            bgp_policy="c" * 64,
        ),
    )
    members = (
        SimpleNamespace(
            instance_index=0,
            node_id="node-active",
            role=SimpleNamespace(value="active"),
        ),
        SimpleNamespace(
            instance_index=1,
            node_id="node-passive",
            role=SimpleNamespace(value="passive"),
        ),
    )
    instances = tuple(
        SimpleNamespace(
            instance_index=member.instance_index,
            hostname=f"nebius-vpn-gw-{member.instance_index}",
            external_ip=f"203.0.113.{10 + member.instance_index}",
            vm_ha_node=member,
        )
        for member in members
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(
            cluster_id="cluster",
            generation=generation,
            members=members,
        ),
        gateway_group=SimpleNamespace(name="nebius-vpn-gw", region="eu-west1"),
        gateway={"local_prefixes": ["10.0.0.0/8"]},
        validate=lambda: None,
        iter_instance_configs=lambda: iter(instances),
    )
    plan.gateway_group.vm_ha = plan.vm_ha
    local_cfg = {
        "project_id": "project-test",
        "gateway_group": {"vm_spec": {}},
    }
    calls: list[str] = []
    trust_policy = SimpleNamespace(managed_action="create")

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def discover_vm_ha_members(self, spec):
            calls.append("discover")
            return {"nebius-vpn-gw-0": "203.0.113.99"}

        def verify_vm_ha_existing_identities(self, existing, **kwargs) -> None:
            calls.append("verify-existing")

        def check_changes(self, spec):
            calls.append("check-changes")
            return []

        def ensure_group(self, *args, **kwargs):
            raise AssertionError("dry-run must not provision")

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch(
            "nebius_vpngw.cli.require_vm_ha_ssh_policy",
            return_value=trust_policy,
        ) as require_trust,
        patch("nebius_vpngw.cli.publish_vm_ha_ssh_trust") as publish_trust,
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush") as ssh_push,
    ):
        result = CliRunner().invoke(
            app,
            ["apply", "--local-config-file", str(config_path), "--dry-run"],
        )

    assert result.exit_code == 0, result.stdout
    assert "Ordinary gateway to VM-HA migration plan" in result.stdout
    assert (
        "Dry-run complete; no lifecycle, cloud, route, or host state was changed" in result.stdout
    )
    assert calls == ["discover", "verify-existing", "check-changes"]
    assert require_trust.call_args.args[0] == (
        ("nebius-vpn-gw-0", "203.0.113.99"),
        ("nebius-vpn-gw-1", "203.0.113.11"),
    )
    assert require_trust.call_args.kwargs["additional_aliases"] == {
        "nebius-vpn-gw-0": ("203.0.113.10",),
        "nebius-vpn-gw-1": (),
    }
    assert "would create the per-deployment VM-HA SSH trust store" in result.stdout
    publish_trust.assert_not_called()
    ssh_push.assert_not_called()
    observed = lifecycle_store.read(
        expected_project_id="project-test",
        expected_gateway_name="nebius-vpn-gw",
    )
    assert observed == (removed if with_removed_tombstone else None)


@pytest.mark.parametrize(
    ("failing_stage_role", "final_transition_fault"),
    [
        (None, None),
        ("passive", None),
        ("active", None),
        (None, "before-write"),
        (None, "before-write-once"),
        (None, "before-write-once-v3"),
        (None, "after-write"),
        (None, "relock-failure"),
    ],
)
def test_vm_ha_apply_delivers_nebius_credentials_passive_first_and_never_activates_partial_stage(
    tmp_path: Path,
    failing_stage_role: str | None,
    final_transition_fault: str | None,
) -> None:
    config_path = tmp_path / "vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    binding = SimpleNamespace(
        cluster_id="cluster-a",
        shared_allocation_id="shared-private",
        route_runtime_id="route-runtime-a",
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
        nodes=(
            SimpleNamespace(
                node_id="node-a",
                role=SimpleNamespace(value="active"),
                compute_id="compute-0",
                network_interface_name="eth0",
            ),
            SimpleNamespace(
                node_id="node-b",
                role=SimpleNamespace(value="passive"),
                compute_id="compute-1",
                network_interface_name="eth0",
            ),
        ),
        route_targets=(
            VMHARouteTarget(
                project_id="project-test",
                network_id="network-a",
                workload_subnet_id="subnet-a",
                route_table_id="route-table-a",
            ),
        ),
    )
    generation = SimpleNamespace(
        generation_id="a" * 64,
        digests=SimpleNamespace(
            configuration="a" * 64,
            static_routes="b" * 64,
            bgp_policy="c" * 64,
        ),
    )
    active = SimpleNamespace(
        instance_index=0,
        hostname="gateway-0",
        external_ip="",
        vm_ha_node=SimpleNamespace(
            instance_index=0,
            node_id="node-a",
            role=SimpleNamespace(value="active"),
            nebius_credentials_path="/operator/node-a-nebius.json",
        ),
        vm_ha_generation=generation,
    )
    passive = SimpleNamespace(
        instance_index=1,
        hostname="gateway-1",
        external_ip="",
        vm_ha_node=SimpleNamespace(
            instance_index=1,
            node_id="node-b",
            role=SimpleNamespace(value="passive"),
            nebius_credentials_path="/operator/node-b-nebius.json",
        ),
        vm_ha_generation=generation,
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(
            cluster_id="cluster-a",
            generation=generation,
            members=(active.vm_ha_node, passive.vm_ha_node),
        ),
        gateway_group=SimpleNamespace(name="gateway", region="eu-west1"),
        gateway={},
        manage_routes=False,
        should_manage_routes=lambda: False,
        validate=lambda: None,
        iter_instance_configs=lambda: iter([active, passive]),
    )
    local_cfg = {"project_id": "project-test", "gateway_group": {"vm_spec": {}}}
    plan.gateway_group.vm_ha = plan.vm_ha
    observed: list[tuple[str, str, object]] = []
    source_bundles: list[object] = []
    ensure_calls = 0
    resume_calls = 0
    active_write_attempts = 0

    class BoundResult(dict):
        vm_ha_runtime_binding = binding

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def discover_vm_ha_members(self, spec):
            return {}

        def verify_vm_ha_existing_identities(
            self, existing, *, policy=None, username="ubuntu"
        ) -> None:
            assert existing == {}

        def check_changes(self, spec) -> list[tuple[str, VMDiff]]:
            return []

        def observe_vm_ha_migration_state(self, spec, local_prefixes=None):
            if ensure_calls == 0:
                return {
                    "members": [],
                    "project_id": "project-test",
                    "route_targets": [],
                    "routes": [],
                    "shared_allocation": {
                        "allocation_name": "gateway-cluster-a-shared-private-ip",
                        "present": False,
                    },
                }
            return {
                "members": [],
                "project_id": "project-test",
                "route_targets": [],
                "routes": [],
                "shared_allocation": {
                    "allocation_id": "shared-private",
                    "allocation_name": "gateway-cluster-a-shared-private-ip",
                    "owner": {
                        "compute_id": "compute-0",
                        "network_interface_name": "eth0",
                    },
                    "present": True,
                },
            }

        def set_vm_ha_lifecycle_journal(self, journal) -> None:
            self.journal = journal

        def finalize_vm_ha_provisioning(self, spec, local_prefixes, public_targets):
            return (
                VMHALifecycleMember(
                    0,
                    "gateway-0",
                    "node-a",
                    "active",
                    "compute-0",
                    "eth0",
                    public_targets["gateway-0"],
                    "11",
                    "disk-0",
                    "subnet-a",
                    "primary-0",
                    "public-0",
                    ("shared-private",),
                ),
                VMHALifecycleMember(
                    1,
                    "gateway-1",
                    "node-b",
                    "passive",
                    "compute-1",
                    "eth0",
                    public_targets["gateway-1"],
                    "12",
                    "disk-1",
                    "subnet-a",
                    "primary-1",
                    "public-1",
                ),
            )

        def ensure_group(self, spec, recreate=False, local_prefixes=None):
            nonlocal ensure_calls
            ensure_calls += 1
            return BoundResult({"gateway-0": "203.0.113.10", "gateway-1": "203.0.113.11"})

        def resume_vm_ha_activation(self, spec, local_prefixes=None):
            nonlocal resume_calls
            resume_calls += 1
            return BoundResult({"gateway-0": "203.0.113.10", "gateway-1": "203.0.113.11"})

        def wait_for_vm_network(self, vm_name, vm_ip, timeout=180) -> bool:
            return True

        def check_vm_health(self, vm_name, vm_ip) -> dict[str, object]:
            return {
                "reachable": True,
                "cloud_init_complete": True,
                "strongswan_installed": True,
                "frr_installed": True,
                "agent_installed": True,
                "esp4_ready": True,
                "esp4_reboot_pending": False,
                "message": "ready",
            }

    class FakeSSHPush:
        def stage_vm_ha_config(
            self,
            target,
            inst_cfg,
            cfg,
            *,
            runtime_binding,
            nebius_credentials_path,
        ):
            observed.append(("stage", inst_cfg.vm_ha_node.role.value, runtime_binding))
            source_bundles.append(nebius_credentials_path)
            if inst_cfg.vm_ha_node.role.value == failing_stage_role:
                raise RuntimeError("injected credential staging failure")
            return SimpleNamespace(
                node_id=inst_cfg.vm_ha_node.node_id,
                generation_id="a" * 64,
                configuration_digest="a" * 64,
                static_routes_digest="b" * 64,
                bgp_policy_digest="c" * 64,
            )

        def install_vm_ha_apply_lock(self, target, inst_cfg, cfg, *, runtime_binding, operation_id):
            observed.append(("lock", inst_cfg.vm_ha_node.role.value, runtime_binding))
            if (
                final_transition_fault == "relock-failure"
                and inst_cfg.vm_ha_node.role.value == "passive"
                and sum(phase == "lock" for phase, _role, _binding in observed) > 2
            ):
                raise RuntimeError("injected passive relock failure")
            return SimpleNamespace(
                node_id=inst_cfg.vm_ha_node.node_id,
                generation_id=inst_cfg.vm_ha_generation.generation_id,
                operation_id=operation_id,
            )

        def install_vm_ha_apply_owner_adoption(
            self,
            target,
            inst_cfg,
            cfg,
            *,
            runtime_binding,
            lock_receipt,
        ):
            observed.append(("adopt", inst_cfg.vm_ha_node.role.value, runtime_binding))
            assert lock_receipt.node_id == inst_cfg.vm_ha_node.node_id
            return SimpleNamespace(node_id=lock_receipt.node_id)

        def push_config_and_reload(
            self, target, inst_cfg, cfg, *, staged_receipt, runtime_binding
        ) -> None:
            observed.append(("activate", inst_cfg.vm_ha_node.role.value, runtime_binding))

        def clear_vm_ha_apply_lock(self, target, inst_cfg, cfg, *, receipt) -> None:
            observed.append(("clear", inst_cfg.vm_ha_node.role.value, binding))

    original_write_verified = VMHALifecycleStore.write_verified

    def write_verified_with_final_fault(
        store,
        state,
        *,
        predecessor_sha256=None,
    ) -> None:
        nonlocal active_write_attempts
        if state.status is VMHALifecycleStatus.ACTIVE:
            active_write_attempts += 1
        if state.status is VMHALifecycleStatus.ACTIVE and final_transition_fault in {
            "before-write",
            "relock-failure",
        }:
            raise RuntimeError("injected final ACTIVE persistence failure")
        if (
            state.status is VMHALifecycleStatus.ACTIVE
            and final_transition_fault in {"before-write-once", "before-write-once-v3"}
            and active_write_attempts == 1
        ):
            raise RuntimeError("injected one-time final ACTIVE persistence failure")
        original_write_verified(
            store,
            state,
            predecessor_sha256=predecessor_sha256,
        )
        if state.status is VMHALifecycleStatus.ACTIVE and final_transition_fault == "after-write":
            raise RuntimeError("injected post-write verification failure")

    def wait_with_controller_shaped_status(*, predicate, inst_cfg, **_kwargs):
        base = {
            "observed_owner_node_id": "node-a",
            "ownership_epoch": "7",
            "pending_operation_id": None,
            "promotion_ready": False,
            "route_reconciliation": {
                "allocation_id": "shared-private",
                "digests": {
                    "configuration": "a" * 64,
                    "static_routes": "b" * 64,
                    "bgp_policy": "c" * 64,
                },
                "generation_id": "a" * 64,
                "operation_id": "route-operation-a",
                "owner_node_id": "node-a",
                "ownership_epoch": "7",
                "ownership_incarnation": 0,
                "route_runtime_id": "route-runtime-a",
            },
        }
        role = inst_cfg.vm_ha_node.role.value
        # Controller-owned materialization has already established the
        # current-boot passive guard before the exact apply lock is checked.
        candidates = [{**base, "data_plane_mode": "passive"}]
        if role == "passive":
            pass
        else:
            later_valid_lineage = {
                **base,
                "data_plane_mode": "active",
                "promotion_ready": True,
                "route_reconciliation": {
                    **base["route_reconciliation"],
                    "ownership_incarnation": 1,
                },
            }
            invalid_lineage = {
                **base,
                "data_plane_mode": "active",
                "promotion_ready": True,
                "route_reconciliation": {
                    **base["route_reconciliation"],
                    "ownership_incarnation": -1,
                },
            }
            wrong_revision = {
                **base,
                "data_plane_mode": "active",
                "promotion_ready": True,
                "route_reconciliation": {
                    **base["route_reconciliation"],
                    "ownership_epoch": "8",
                },
            }
            if predicate(later_valid_lineage):
                assert not predicate(invalid_lineage)
                assert not predicate(wrong_revision)
                return later_valid_lineage
            candidates.append({**base, "data_plane_mode": "active", "promotion_ready": True})
        for payload in candidates:
            if predicate(payload):
                return payload
        raise AssertionError(f"status predicate rejected controller-shaped {role} states")

    idempotent_result = None
    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch(
            "nebius_vpngw.cli.require_vm_ha_ssh_policy",
            return_value=object(),
        ),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
        patch(
            "nebius_vpngw.cli._prepare_vm_ha_managed_mtls",
            return_value=SimpleNamespace(changed=False, operation_id=None),
        ),
        patch(
            "nebius_vpngw.cli._wait_for_vm_ha_agent_status",
            side_effect=wait_with_controller_shaped_status,
        ),
        patch.object(
            VMHALifecycleStore,
            "write_verified",
            write_verified_with_final_fault,
        ),
    ):
        first_result = CliRunner().invoke(
            app,
            ["apply", "--local-config-file", str(config_path)],
        )
        if final_transition_fault == "before-write-once-v3":
            interrupted = VMHALifecycleStore(config_path).read(
                expected_project_id="project-test", expected_gateway_name="gateway"
            )
            assert interrupted is not None
            assert interrupted.status is VMHALifecycleStatus.ACTIVATING
            legacy = replace(interrupted, record_version=3)
            VMHALifecycleStore(config_path).path.write_text(
                json.dumps(legacy.to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
        result = (
            CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])
            if final_transition_fault in {"before-write-once", "before-write-once-v3"}
            else first_result
        )
        if (
            failing_stage_role is None
            and final_transition_fault is None
            and first_result.exit_code == 0
        ):
            idempotent_result = CliRunner().invoke(
                app, ["apply", "--local-config-file", str(config_path)]
            )

    if final_transition_fault in {"before-write-once", "before-write-once-v3"}:
        assert first_result.exit_code == 1
        assert "exact-operation apply locks were restored and verified" in first_result.stdout
        assert result.exit_code == 0, result.stdout
        assert ensure_calls == 1
        assert resume_calls == 1
        state = VMHALifecycleStore(config_path).read(
            expected_project_id="project-test", expected_gateway_name="gateway"
        )
        assert state is not None and state.status is VMHALifecycleStatus.ACTIVE
        assert state.record_version == 4
    elif failing_stage_role is None and final_transition_fault in {None, "after-write"}:
        assert result.exit_code == 0, result.stdout
        expected_trace = [
            ("stage", "passive"),
            ("stage", "active"),
            ("lock", "passive"),
            ("lock", "active"),
            ("adopt", "active"),
            ("activate", "passive"),
            ("activate", "active"),
            ("clear", "active"),
            ("clear", "passive"),
        ]
        if final_transition_fault is None:
            assert idempotent_result is not None
            assert idempotent_result.exit_code == 0, idempotent_result.stdout
            expected_trace *= 2
        assert [(phase, role) for phase, role, _ in observed] == expected_trace
        state = VMHALifecycleStore(config_path).read(
            expected_project_id="project-test", expected_gateway_name="gateway"
        )
        assert state is not None
        assert state.status is VMHALifecycleStatus.ACTIVE
        assert [member.compute_id for member in state.members] == ["compute-0", "compute-1"]
    else:
        assert result.exit_code == 1
        state = VMHALifecycleStore(config_path).read(
            expected_project_id="project-test", expected_gateway_name="gateway"
        )
        assert state is not None and state.status is VMHALifecycleStatus.ACTIVATING
        if final_transition_fault == "before-write":
            assert [(phase, role) for phase, role, _ in observed[-2:]] == [
                ("lock", "passive"),
                ("lock", "active"),
            ]
            assert "exact-operation apply locks were restored and verified" in result.stdout
            assert "Apply completed successfully" not in result.stdout
        elif final_transition_fault == "relock-failure":
            assert "activation recovery is unsafe" in result.stdout
            assert "passive relock failure" in result.stdout
            assert [role for phase, role, _ in observed if phase == "lock"] == [
                "passive",
                "active",
                "passive",
            ]
            assert "Apply completed successfully" not in result.stdout
        else:
            assert all(phase != "activate" for phase, _, _ in observed)
    expected_sources = [passive.vm_ha_node.nebius_credentials_path]
    if failing_stage_role != "passive":
        expected_sources.append(active.vm_ha_node.nebius_credentials_path)
    if final_transition_fault in {"before-write-once", "before-write-once-v3"} or (
        failing_stage_role is None and final_transition_fault is None
    ):
        expected_sources *= 2
    assert source_bundles == expected_sources
    assert all(item is binding for _, _, item in observed)


@pytest.mark.parametrize(
    ("configured_key", "environment_key", "expected_key"),
    [
        ("~/configured-key", "/tmp/environment-key", Path("~/configured-key").expanduser()),
        (None, "~/environment-key", Path("~/environment-key").expanduser()),
        (None, None, None),
    ],
)
def test_vm_ha_apply_passes_one_resolved_management_key_to_both_managers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_key: str | None,
    environment_key: str | None,
    expected_key: Path | None,
) -> None:
    config_path = tmp_path / "vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    vm_spec = {"ssh_private_key_path": configured_key} if configured_key else {}
    local_cfg = {
        "project_id": "project-test",
        "gateway_group": {"vm_spec": vm_spec},
    }
    generation = SimpleNamespace(
        generation_id="a" * 64,
        digests=SimpleNamespace(
            configuration="a" * 64,
            static_routes="b" * 64,
            bgp_policy="c" * 64,
        ),
    )
    members = (
        SimpleNamespace(
            instance_index=0,
            node_id="node-a",
            role=SimpleNamespace(value="active"),
        ),
        SimpleNamespace(
            instance_index=1,
            node_id="node-b",
            role=SimpleNamespace(value="passive"),
        ),
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(
            cluster_id="cluster-a",
            generation=generation,
            members=members,
        ),
        gateway_group=SimpleNamespace(name="gateway", region="eu-west1"),
        gateway={},
        manage_routes=False,
        should_manage_routes=lambda: False,
        validate=lambda: None,
        iter_instance_configs=lambda: iter(()),
    )
    manager_keys: list[Path | None] = []
    trust_order: list[str] = []

    class BoundResult(dict):
        vm_ha_runtime_binding = object()

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            manager_keys.append(kwargs.get("management_key_path"))

        def discover_vm_ha_members(self, spec):
            trust_order.append("discover")
            return {}

        def verify_vm_ha_existing_identities(self, existing, **kwargs) -> None:
            trust_order.append("verify")
            return None

        def check_changes(self, spec):
            trust_order.append("check")
            return []

        def set_vm_ha_lifecycle_journal(self, journal) -> None:
            return None

        def ensure_group(self, spec, recreate=False, local_prefixes=None):
            trust_order.append("ensure")
            return BoundResult()

    plan.gateway_group.vm_ha = plan.vm_ha

    if environment_key is None:
        monkeypatch.delenv("VPNGW_SSH_KEY", raising=False)
    else:
        monkeypatch.setenv("VPNGW_SSH_KEY", environment_key)
    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch(
            "nebius_vpngw.cli.require_vm_ha_ssh_policy",
            return_value=SimpleNamespace(managed_action="repair"),
        ) as require_policy,
        patch(
            "nebius_vpngw.cli.publish_vm_ha_ssh_trust",
            side_effect=lambda _policy: trust_order.append("publish") or True,
        ),
        patch("nebius_vpngw.cli._vm_ha_apply_order", return_value=[]),
        patch("nebius_vpngw.cli._vm_ha_bound_owner_node_id", return_value="node-a"),
        patch("nebius_vpngw.cli._vm_ha_apply_order_for_owner", return_value=[]),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush"),
        patch(
            "nebius_vpngw.cli._prepare_vm_ha_managed_mtls",
            return_value=SimpleNamespace(changed=False, operation_id=None),
        ),
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 1
    assert "staged acknowledgements do not have exact generation parity" in result.stdout
    assert manager_keys == [expected_key, expected_key]
    assert require_policy.call_args.kwargs["management_key_path"] == expected_key
    assert require_policy.call_args.kwargs["require_management_key"] is True
    assert require_policy.call_args.kwargs["allow_managed_repair"] is True
    assert trust_order.index("verify") < trust_order.index("publish") < trust_order.index("ensure")


def test_vm_ha_activation_has_no_static_runtime_blockers_after_complete_wiring() -> None:
    assert _vm_ha_activation_blockers() == ()


def test_vm_ha_removal_holds_the_canonical_apply_lock(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "gateway.config.yaml"
    plan = SimpleNamespace(
        vm_ha=None,
        gateway_group=SimpleNamespace(name="nebius-vpn-gw"),
    )
    lock_events: list[tuple[str, str, str]] = []

    class LifecycleStore:
        def __init__(self, path: Path) -> None:
            assert path == config_path

        def read(self, **kwargs):
            assert kwargs == {
                "expected_project_id": "project-test",
                "expected_gateway_name": "nebius-vpn-gw",
            }
            return _lifecycle_state(status=VMHALifecycleStatus.ACTIVE)

    class ApplyLock:
        def __init__(self, *, project_id: str, gateway_name: str) -> None:
            lock_events.append(("init", project_id, gateway_name))

        def __enter__(self):
            lock_events.append(("enter", "project-test", "nebius-vpn-gw"))
            return self

        def __exit__(self, *_args) -> None:
            lock_events.append(("exit", "project-test", "nebius-vpn-gw"))

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_local_config", lambda *_args, **_kwargs: config_path
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli.load_local_config",
        lambda _path: {"project_id": "project-test"},
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)
    monkeypatch.setattr("nebius_vpngw.cli.VMHALifecycleStore", LifecycleStore)
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", ApplyLock)

    @_serialize_explicit_vm_ha_apply
    def command(
        local_config_file: Path | None = None,
        project_id: str | None = None,
        dry_run: bool = False,
    ) -> str:
        return "called"

    assert command(local_config_file=config_path, project_id=None, dry_run=False) == "called"
    assert lock_events == [
        ("init", "project-test", "nebius-vpn-gw"),
        ("enter", "project-test", "nebius-vpn-gw"),
        ("exit", "project-test", "nebius-vpn-gw"),
    ]


def test_vm_ha_status_validation_binds_generation_runtime_and_apply_lock() -> None:
    digests = SimpleNamespace(
        configuration="a" * 64,
        static_routes="b" * 64,
        bgp_policy="c" * 64,
    )
    inst_cfg = SimpleNamespace(
        config_yaml=yaml.safe_dump({"vm_ha": {"cluster_id": "cluster-a"}}),
        vm_ha_node=SimpleNamespace(node_id="node-a", role=SimpleNamespace(value="active")),
        vm_ha_generation=SimpleNamespace(generation_id="a" * 64, digests=digests),
    )
    binding = SimpleNamespace(
        cluster_id="cluster-a",
        shared_allocation_id="allocation-a",
        route_runtime_id="route-runtime-a",
    )
    payload = {
        "schema": "nebius-vpngw/vm-ha-status-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "configured_role": "active",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "allocation_id": "allocation-a",
        "route_runtime_id": "route-runtime-a",
        "apply_locked": True,
        "apply_operation_id": "d" * 64,
    }

    assert (
        _validate_vm_ha_agent_status(
            payload,
            inst_cfg=inst_cfg,
            runtime_binding=binding,
            expected_apply_locked=True,
            expected_operation_id="d" * 64,
        )
        is payload
    )
    repair = {
        "deadline_at": 15.0,
        "failure_fingerprint": ["bgp-not-ready"],
        "healthy_observations": 0,
        "operation_id": "repair-operation-a",
        "owner_node_id": "node-a",
        "ownership_epoch": "7",
        "remaining_seconds": 3.5,
    }
    assert (
        _validate_vm_ha_agent_status(
            {**payload, "repair": repair},
            inst_cfg=inst_cfg,
            runtime_binding=binding,
            expected_apply_locked=True,
            expected_operation_id="d" * 64,
        )["repair"]
        == repair
    )
    with pytest.raises(ValueError, match="invalid repair record"):
        _validate_vm_ha_agent_status(
            {**payload, "repair": {**repair, "remaining_seconds": float("nan")}},
            inst_cfg=inst_cfg,
            runtime_binding=binding,
        )
    with pytest.raises(ValueError, match="invalid repair record"):
        _validate_vm_ha_agent_status(
            {**payload, "repair": {**repair, "healthy_observations": 3}},
            inst_cfg=inst_cfg,
            runtime_binding=binding,
        )
    with pytest.raises(ValueError, match="wrong apply-lock operation"):
        _validate_vm_ha_agent_status(
            {**payload, "apply_operation_id": "e" * 64},
            inst_cfg=inst_cfg,
            runtime_binding=binding,
            expected_apply_locked=True,
            expected_operation_id="d" * 64,
        )
    with pytest.raises(ValueError, match="runtime binding"):
        _validate_vm_ha_agent_status(
            {**payload, "allocation_id": "foreign"},
            inst_cfg=inst_cfg,
            runtime_binding=binding,
        )
    with pytest.raises(_VMHAAgentStatusStale, match="expected generation"):
        _validate_vm_ha_agent_status(
            {**payload, "generation_id": "e" * 64},
            inst_cfg=inst_cfg,
            runtime_binding=binding,
        )
    with pytest.raises(_VMHAAgentStatusStale, match="apply-lock state"):
        _validate_vm_ha_agent_status(
            {**payload, "apply_locked": False, "apply_operation_id": None},
            inst_cfg=inst_cfg,
            runtime_binding=binding,
            expected_apply_locked=True,
            expected_operation_id="d" * 64,
        )
    with pytest.raises(_VMHAAgentStatusPermanent, match="foreign cluster"):
        _validate_vm_ha_agent_status(
            {**payload, "cluster_id": "foreign"},
            inst_cfg=inst_cfg,
            runtime_binding=binding,
        )


def test_vm_ha_status_validation_uses_runtime_generation_when_psk_is_unresolved() -> None:
    digests = SimpleNamespace(
        configuration="a" * 64,
        static_routes="b" * 64,
        bgp_policy="c" * 64,
    )
    inst_cfg = SimpleNamespace(
        config_yaml=yaml.safe_dump({"vm_ha": {"cluster_id": "cluster-a"}}),
        vm_ha_node=SimpleNamespace(node_id="node-a", role=SimpleNamespace(value="active")),
        vm_ha_generation=SimpleNamespace(generation_id="a" * 64, digests=digests),
    )
    payload = {
        "schema": "nebius-vpngw/vm-ha-status-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "configured_role": "active",
        "generation_id": "d" * 64,
        "digests": {
            "configuration": "d" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "apply_locked": False,
        "apply_operation_id": None,
    }

    assert (
        _validate_vm_ha_agent_status(
            payload,
            inst_cfg=inst_cfg,
            require_local_generation=False,
        )
        is payload
    )
    with pytest.raises(_VMHAAgentStatusPermanent, match="generation record"):
        _validate_vm_ha_agent_status(
            {
                **payload,
                "digests": {**payload["digests"], "configuration": "e" * 64},
            },
            inst_cfg=inst_cfg,
            require_local_generation=False,
        )
    with pytest.raises(_VMHAAgentStatusStale, match="policy digests"):
        _validate_vm_ha_agent_status(
            {
                **payload,
                "digests": {**payload["digests"], "static_routes": "e" * 64},
            },
            inst_cfg=inst_cfg,
            require_local_generation=False,
        )


@pytest.mark.parametrize(
    ("state", "rearm_phase"),
    (
        ("normal", "not-owner"),
        ("suspect", "idle"),
        ("fencing", "inhibited"),
        ("ownership-transfer", "blocked"),
        ("promoting", "starting"),
        ("active", "running"),
        ("degraded-path", "not-owner"),
        ("repairing", "idle"),
        ("repair-exhausted", "inhibited"),
        ("degraded", "blocked"),
        ("blocked", "starting"),
    ),
)
def test_vm_ha_display_status_validates_every_controller_and_rearm_state(
    state: str,
    rearm_phase: str,
) -> None:
    digests = SimpleNamespace(
        configuration="a" * 64,
        static_routes="b" * 64,
        bgp_policy="c" * 64,
    )
    inst_cfg = SimpleNamespace(
        config_yaml=yaml.safe_dump({"vm_ha": {"cluster_id": "cluster-a"}}),
        vm_ha_node=SimpleNamespace(node_id="node-b", role=SimpleNamespace(value="passive")),
        vm_ha_generation=SimpleNamespace(generation_id="a" * 64, digests=digests),
    )
    binding = SimpleNamespace(
        cluster_id="cluster-a",
        shared_allocation_id="allocation-a",
        route_runtime_id="route-runtime-a",
    )
    is_owner = rearm_phase != "not-owner"
    payload = {
        "schema": "nebius-vpngw/vm-ha-status-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-b",
        "configured_role": "passive",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "allocation_id": "allocation-a",
        "route_runtime_id": "route-runtime-a",
        "apply_locked": False,
        "apply_operation_id": None,
        "state": state,
        "reasons": [],
        "data_plane_mode": "active" if is_owner else "passive",
        "observed_owner_node_id": "node-b" if is_owner else "node-a",
        "promotion_ready": is_owner,
        "standby_ready": not is_owner,
        "standby_tunnel_state": "not-standby" if is_owner else "warm",
        "standby_readiness_reasons": [],
        "pending_operation_id": None,
        "rearm_phase": rearm_phase,
        "rearm_reason": None,
        "phase_durations_seconds": {
            "preparation": None,
            "detection_repair": None,
            "common_cutover": None,
            "redundancy_restoration": None,
        },
        "repair": None,
        "mtls": {
            "state": "healthy",
            "cluster_id": "cluster-a",
            "node_id": "node-b",
            "compute_id": "compute-b",
            "epoch": 1,
            "certificate_fingerprint": "d" * 64,
            "spki_fingerprint": "e" * 64,
            "peer_fingerprints": ["f" * 64],
            "operation_id": None,
            "operation_kind": None,
            "target_epoch": None,
            "peer_target_epoch": None,
            "preserve_local": None,
            "inhibited": False,
            "inhibition_operation_id": None,
            "phase": None,
            "recovery": None,
            "peer": {
                "node_id": "node-a",
                "boot_id": "boot-a",
                "sequence": 1,
                "epoch": 1,
                "certificate_fingerprint": "f" * 64,
                "fresh": True,
            },
        },
    }

    assert (
        _validate_vm_ha_display_status(
            payload,
            inst_cfg=inst_cfg,
            runtime_binding=binding,
        )
        is payload
    )

    invalid_durations = dict(payload["phase_durations_seconds"])
    invalid_durations["preparation"] = float("nan")
    with pytest.raises(_VMHAAgentStatusPermanent, match="invalid display evidence"):
        _validate_vm_ha_display_status(
            {**payload, "phase_durations_seconds": invalid_durations},
            inst_cfg=inst_cfg,
            runtime_binding=binding,
        )

    if state == "repairing":
        repair_operation = "repair-operation-a"
        repairing_owner = {
            **payload,
            "data_plane_mode": "active",
            "observed_owner_node_id": "node-b",
            "promotion_ready": False,
            "standby_ready": False,
            "standby_tunnel_state": "not-standby",
            "pending_operation_id": repair_operation,
            "repair": {
                "deadline_at": 15.0,
                "failure_fingerprint": ["bgp-not-ready"],
                "healthy_observations": 0,
                "operation_id": repair_operation,
                "owner_node_id": "node-b",
                "ownership_epoch": "7",
                "remaining_seconds": 3.5,
            },
        }
        assert (
            _validate_vm_ha_display_status(
                repairing_owner,
                inst_cfg=inst_cfg,
                runtime_binding=binding,
            )
            is repairing_owner
        )


def test_vm_ha_planned_status_requires_exact_runtime_and_standby_evidence() -> None:
    digests = SimpleNamespace(
        configuration="a" * 64,
        static_routes="b" * 64,
        bgp_policy="c" * 64,
    )
    inst_cfg = SimpleNamespace(
        config_yaml=yaml.safe_dump({"vm_ha": {"cluster_id": "cluster-a"}}),
        vm_ha_node=SimpleNamespace(node_id="node-b", role=SimpleNamespace(value="passive")),
        vm_ha_generation=SimpleNamespace(generation_id="a" * 64, digests=digests),
    )
    binding = SimpleNamespace(
        cluster_id="cluster-a",
        shared_allocation_id="allocation-a",
        route_runtime_id="route-runtime-a",
    )
    payload = {
        "schema": "nebius-vpngw/vm-ha-status-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-b",
        "configured_role": "passive",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "allocation_id": "allocation-a",
        "route_runtime_id": "route-runtime-a",
        "apply_locked": False,
        "apply_operation_id": None,
        "controller_ready_boot_id": None,
        "data_plane_mode": "passive",
        "guard_boot_id": "boot-b",
        "observed_owner_node_id": "node-a",
        "pending_operation_id": None,
        "promotion_ready": False,
        "route_reconciliation": None,
        "standby_readiness_reasons": [],
        "standby_ready": True,
        "state": "normal",
    }

    assert (
        _validate_vm_ha_planned_status(
            payload,
            inst_cfg=inst_cfg,
            runtime_binding=binding,
        )
        is payload
    )
    stale_projection = {
        **payload,
        "state": "blocked",
        "reasons": ["current-boot-status-stale"],
        "controller_ready_boot_id": None,
        "data_plane_mode": "blocked",
        "observed_owner_node_id": None,
        "promotion_ready": False,
        "standby_ready": False,
        "standby_tunnel_state": "not-standby",
        "standby_readiness_reasons": ["current-boot-status-stale"],
    }
    assert (
        _validate_vm_ha_planned_status(
            stale_projection,
            inst_cfg=inst_cfg,
            runtime_binding=binding,
        )
        is stale_projection
    )
    invalid_cases = (
        {**payload, "digests": {"configuration": "f" * 64}},
        {**payload, "allocation_id": "foreign"},
        {key: value for key, value in payload.items() if key != "standby_ready"},
        {**payload, "guard_boot_id": ""},
        {**payload, "controller_ready_boot_id": "boot-b"},
    )
    for invalid in invalid_cases:
        with pytest.raises((_VMHAAgentStatusPermanent, _VMHAAgentStatusStale)):
            _validate_vm_ha_planned_status(
                invalid,
                inst_cfg=inst_cfg,
                runtime_binding=binding,
            )


def test_vm_ha_status_runtime_binding_requires_authoritative_lifecycle() -> None:
    binding = _vm_ha_status_runtime_binding(_lifecycle_state())

    assert binding.cluster_id == "cluster"
    assert binding.shared_allocation_id == "shared-private"
    assert binding.route_runtime_id == "route-runtime"
    with pytest.raises(ValueError, match="authoritative runtime binding"):
        _vm_ha_status_runtime_binding(_lifecycle_state(status=VMHALifecycleStatus.PROVISIONING))


def test_vm_ha_cloud_authority_binds_owner_members_alias_and_routes() -> None:
    target = {
        "network_id": "network-1",
        "project_id": "project-test",
        "route_table_id": "route-table-1",
        "workload_subnet_id": "subnet-workload",
    }
    target_identity = json.dumps(target, sort_keys=True, separators=(",", ":"))
    state = replace(_lifecycle_state(), route_targets=(target_identity,))
    labels = NebiusSDKRouteBackend._authority_labels(
        cluster_id=state.cluster_id,
        allocation_id=state.allocation_id,
        route_target=VMHARouteTarget.model_validate(target),
        route_kind="bgp",
    )
    observation = {
        "members": [
            {
                "aliases": ["shared-private"],
                "compute_id": "compute-0",
                "compute_revision": "21",
                "instance_name": "nebius-vpn-gw-0",
                "network_interface_name": "eth0",
                "present": True,
            },
            {
                "aliases": [],
                "compute_id": "compute-1",
                "compute_revision": "22",
                "instance_name": "nebius-vpn-gw-1",
                "network_interface_name": "eth0",
                "present": True,
            },
        ],
        "route_targets": [target],
        "routes": [
            {
                "allocation_id": "shared-private",
                "authority_labels": labels,
                "name": "vpngw-ha-fixture",
                "prefix": "10.0.0.0/8",
                "route_table_id": "route-table-1",
            }
        ],
        "shared_allocation": {
            "allocation_id": "shared-private",
            "owner": {
                "compute_id": "compute-0",
                "network_interface_name": "eth0",
            },
            "present": True,
        },
    }

    authority = _vm_ha_cloud_authority(state, observation)

    assert authority.lifecycle == "active"
    assert authority.condition == "exact"
    assert authority.owner_name == "nebius-vpn-gw-0"
    assert authority.owner_node_id == "node-active"
    assert authority.reasons == ()

    foreign = {
        **observation,
        "shared_allocation": {
            "allocation_id": "shared-private",
            "owner": {
                "compute_id": "foreign-compute",
                "network_interface_name": "eth0",
            },
            "present": True,
        },
    }
    foreign_authority = _vm_ha_cloud_authority(state, foreign)
    assert foreign_authority.condition == "blocked"
    assert "shared-allocation-owner-foreign" in foreign_authority.reasons

    no_alias_route_authority = _vm_ha_cloud_authority(
        state,
        {**observation, "routes": []},
    )
    assert no_alias_route_authority.condition == "blocked"
    assert no_alias_route_authority.reasons == ("route-prefixes-not-exact",)

    missing_target_authority = _vm_ha_cloud_authority(
        state,
        {**observation, "route_targets": [], "routes": []},
    )
    assert missing_target_authority.condition == "blocked"
    assert missing_target_authority.reasons == ("route-targets-not-exact",)

    duplicate_route_authority = _vm_ha_cloud_authority(
        state,
        {**observation, "routes": [*observation["routes"], *observation["routes"]]},
    )
    assert duplicate_route_authority.condition == "blocked"
    assert duplicate_route_authority.reasons == ("route-records-not-exact",)

    malformed_presence_authority = _vm_ha_cloud_authority(
        state,
        {
            **observation,
            "shared_allocation": {
                **observation["shared_allocation"],
                "present": "yes",
            },
        },
    )
    assert malformed_presence_authority.condition == "blocked"
    assert "shared-allocation-not-exact" in malformed_presence_authority.reasons

    second_target = {
        **target,
        "route_table_id": "route-table-2",
        "workload_subnet_id": "subnet-workload-2",
    }
    two_target_state = replace(
        state,
        route_targets=(
            target_identity,
            json.dumps(second_target, sort_keys=True, separators=(",", ":")),
        ),
    )
    incomplete_routes = {
        **observation,
        "route_targets": [target, second_target],
    }
    incomplete_authority = _vm_ha_cloud_authority(two_target_state, incomplete_routes)
    assert incomplete_authority.condition == "blocked"
    assert incomplete_authority.reasons == ("route-prefixes-not-exact",)

    complete_routes = {
        **incomplete_routes,
        "routes": [
            *observation["routes"],
            {
                "allocation_id": "shared-private",
                "authority_labels": NebiusSDKRouteBackend._authority_labels(
                    cluster_id=state.cluster_id,
                    allocation_id=state.allocation_id,
                    route_target=VMHARouteTarget.model_validate(second_target),
                    route_kind="bgp",
                ),
                "name": "vpngw-ha-fixture-2",
                "prefix": "10.0.0.0/8",
                "route_table_id": "route-table-2",
            },
        ],
    }
    assert _vm_ha_cloud_authority(two_target_state, complete_routes).condition == "exact"

    wrong_next_hop = {
        **complete_routes,
        "routes": [dict(route) for route in complete_routes["routes"]],
    }
    wrong_next_hop["routes"][1]["allocation_id"] = "foreign-allocation"
    wrong_next_hop_authority = _vm_ha_cloud_authority(two_target_state, wrong_next_hop)
    assert wrong_next_hop_authority.condition == "blocked"
    assert wrong_next_hop_authority.reasons == ("route-next-hop-not-exact",)

    foreign_route = {
        "allocation_id": "foreign-allocation",
        "authority_labels": NebiusSDKRouteBackend._authority_labels(
            cluster_id="foreign-cluster",
            allocation_id="foreign-allocation",
            route_target=VMHARouteTarget.model_validate(target),
            route_kind="static",
        ),
        "name": "vpngw-foreign-cluster",
        "prefix": "192.0.2.1/32",
        "route_table_id": "route-table-1",
    }
    isolated = _vm_ha_cloud_authority(
        state,
        {**observation, "routes": [*observation["routes"], foreign_route]},
    )
    assert isolated.condition == "exact"
    assert isolated.reasons == ()


def _vm_ha_view_record(
    node_id: str,
    owner_node_id: str,
    *,
    owner: bool,
    standby_ready: bool = True,
    state: str = "normal",
    reasons: list[str] | None = None,
    pending_operation_id: str | None = None,
    apply_locked: bool = False,
    apply_operation_id: str | None = None,
    promotion_ready: bool | None = None,
    rearm_phase: str | None = None,
    rearm_reason: str | None = None,
    repair: dict[str, object] | None = None,
) -> dict[str, object]:
    local_fingerprint = ("d" if node_id == "node-active" else "e") * 64
    peer_fingerprint = ("e" if node_id == "node-active" else "d") * 64
    peer_node_id = "node-passive" if node_id == "node-active" else "node-active"
    return {
        "node_id": node_id,
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "state": state,
        "reasons": list(reasons or ()),
        "data_plane_mode": "active" if owner else "passive",
        "observed_owner_node_id": owner_node_id,
        "promotion_ready": owner if promotion_ready is None else promotion_ready,
        "standby_ready": False if owner else standby_ready,
        "standby_tunnel_state": "not-standby" if owner else "warm",
        "standby_readiness_reasons": [] if standby_ready or owner else list(reasons or ()),
        "apply_locked": apply_locked,
        "apply_operation_id": apply_operation_id,
        "pending_operation_id": pending_operation_id,
        "rearm_phase": rearm_phase or ("idle" if owner else "not-owner"),
        "rearm_reason": rearm_reason,
        "phase_durations_seconds": {
            "preparation": 1.0 if owner else None,
            "detection_repair": None,
            "common_cutover": 2.0 if owner else None,
            "redundancy_restoration": 3.0 if owner else None,
        },
        "repair": repair,
        "mtls": {
            "state": "healthy",
            "cluster_id": "cluster",
            "node_id": node_id,
            "compute_id": f"compute-{node_id}",
            "epoch": 1,
            "certificate_fingerprint": local_fingerprint,
            "spki_fingerprint": ("f" if node_id == "node-active" else "c") * 64,
            "peer_fingerprints": [peer_fingerprint],
            "operation_id": None,
            "operation_kind": None,
            "target_epoch": None,
            "peer_target_epoch": None,
            "preserve_local": None,
            "inhibited": False,
            "inhibition_operation_id": None,
            "phase": None,
            "recovery": None,
            "peer": {
                "node_id": peer_node_id,
                "boot_id": f"boot-{peer_node_id}",
                "sequence": 1,
                "epoch": 1,
                "certificate_fingerprint": peer_fingerprint,
                "fresh": True,
            },
        },
    }


def _vm_ha_view_members(
    *,
    owner_node_id: str = "node-active",
    standby_ready: bool = True,
    standby_state: str = "normal",
    standby_reasons: list[str] | None = None,
) -> tuple[_VMHAMemberEvidence, _VMHAMemberEvidence]:
    records = {
        "node-active": _vm_ha_view_record(
            "node-active", owner_node_id, owner=owner_node_id == "node-active"
        ),
        "node-passive": _vm_ha_view_record(
            "node-passive",
            owner_node_id,
            owner=owner_node_id == "node-passive",
            standby_ready=standby_ready,
            state=standby_state,
            reasons=standby_reasons,
        ),
    }
    if owner_node_id == "node-passive":
        records["node-active"] = _vm_ha_view_record(
            "node-active",
            owner_node_id,
            owner=False,
            standby_ready=standby_ready,
            state=standby_state,
            reasons=standby_reasons,
        )
    return (
        _VMHAMemberEvidence(
            "nebius-vpn-gw-0", "active", "node-active", "exact", "", records["node-active"]
        ),
        _VMHAMemberEvidence(
            "nebius-vpn-gw-1",
            "passive",
            "node-passive",
            "exact",
            "",
            records["node-passive"],
        ),
    )


@pytest.mark.parametrize(
    ("owner_name", "owner_node_id", "expected_roles"),
    (
        ("nebius-vpn-gw-0", "node-active", ("active", "standby")),
        ("nebius-vpn-gw-1", "node-passive", ("standby", "active")),
    ),
)
def test_vm_ha_status_view_is_healthy_for_either_authoritative_owner(
    owner_name: str,
    owner_node_id: str,
    expected_roles: tuple[str, str],
) -> None:
    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", owner_name, owner_node_id, None, ()),
        _vm_ha_view_members(owner_node_id=owner_node_id),
        rearm_command="nebius-vpngw vm-ha-rearm --local-config-file fixture.yaml",
    )

    assert view.overall == "HEALTHY"
    assert len(view.member_rows) == 2
    assert tuple(row[1] for row in view.member_rows) == expected_roles
    assert next(row for row in view.summary_rows if row[0] == "Action")[1] == "none"
    rendered = repr((view.summary_rows, view.member_rows))
    assert "node-active" not in rendered
    assert "node-passive" not in rendered
    assert "shared-private" not in rendered


def test_vm_ha_status_view_reports_resumable_managed_mtls_rotation() -> None:
    operation_id = "9" * 64
    members = list(_vm_ha_view_members())
    rotated: list[_VMHAMemberEvidence] = []
    for member in members:
        assert member.record is not None
        record = dict(member.record)
        mtls = dict(record["mtls"])
        mtls.update(
            {
                "state": "transitioning",
                "operation_id": operation_id,
                "operation_kind": "rotation",
                "target_epoch": 2,
                "peer_target_epoch": 2,
                "inhibited": True,
                "inhibition_operation_id": operation_id,
                "phase": "trust-expanded",
                "recovery": "rollback-or-resume",
            }
        )
        record.update(
            {
                "apply_locked": True,
                "apply_operation_id": operation_id,
                "mtls": mtls,
            }
        )
        rotated.append(replace(member, record=record))

    view = _vm_ha_status_view(
        _VMHACloudAuthority(
            "active", "exact", "nebius-vpn-gw-0", "node-active", None, ()
        ),
        t.cast(tuple[_VMHAMemberEvidence, _VMHAMemberEvidence], tuple(rotated)),
        rearm_command="nebius-vpngw vm-ha-rearm --local-config-file fixture.yaml",
        mtls_command="nebius-vpngw set-vm-ha-mtls --local-config-file fixture.yaml",
    )

    assert view.overall == "TRANSITIONING"
    assert next(row for row in view.summary_rows if row[0] == "mTLS")[1] == "rotating"
    assert next(row for row in view.summary_rows if row[0] == "Action")[1].startswith(
        "nebius-vpngw set-vm-ha-mtls"
    )


def test_vm_ha_status_view_blocks_member_generation_disagreement() -> None:
    members = list(_vm_ha_view_members())
    standby = dict(members[1].record or {})
    standby["generation_id"] = "d" * 64
    standby["digests"] = {
        **t.cast(dict[str, str], standby["digests"]),
        "configuration": "d" * 64,
    }
    members[1] = replace(members[1], record=standby)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        (members[0], members[1]),
        rearm_command="unused",
    )

    assert view.overall == "BLOCKED"
    assert "agent-status-conflict" in repr(view.summary_rows)


def test_vm_ha_status_view_gives_route_next_hop_repair_action() -> None:
    view = _vm_ha_status_view(
        _VMHACloudAuthority(
            "active",
            "blocked",
            "nebius-vpn-gw-0",
            "node-active",
            None,
            ("route-next-hop-not-exact",),
        ),
        _vm_ha_view_members(),
        rearm_command="unused",
    )

    assert view.overall == "BLOCKED"
    action = next(row for row in view.summary_rows if row[0] == "Action")
    assert action == (
        "Action",
        "repair-route-authority",
        "reconcile managed route next hops through the supported apply workflow, then rerun status",
    )


def test_vm_ha_status_view_treats_terminal_rearm_running_as_healthy() -> None:
    members = list(_vm_ha_view_members())
    owner = dict(members[0].record or {})
    owner["rearm_phase"] = "running"
    members[0] = replace(members[0], record=owner)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        (members[0], members[1]),
        rearm_command="unused",
    )

    assert view.overall == "HEALTHY"


def test_vm_ha_status_view_treats_exact_repair_action_as_transitioning() -> None:
    members = list(_vm_ha_view_members())
    operation_id = "boot-a:4:repair-local-dataplane:node-active"
    owner = dict(members[0].record or {})
    owner.update(
        state="repairing",
        promotion_ready=False,
        pending_operation_id=operation_id,
        repair={"operation_id": operation_id, "failure_fingerprint": ["bgp-not-ready"]},
    )
    members[0] = replace(members[0], record=owner)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        (members[0], members[1]),
        rearm_command="unused",
    )

    assert view.overall == "TRANSITIONING"
    assert "unexpected-controller-operation" not in repr(view.summary_rows)


@pytest.mark.parametrize("state", ("fencing", "ownership-transfer", "promoting"))
def test_vm_ha_status_view_treats_controller_transfer_actions_as_transitioning(
    state: str,
) -> None:
    members = list(_vm_ha_view_members())
    standby = dict(members[1].record or {})
    action_kind = {
        "fencing": "stop-former-owner",
        "ownership-transfer": "attach-candidate",
        "promoting": "confirm-candidate-ownership",
    }[state]
    target_node = "node-active" if state == "fencing" else "node-passive"
    standby.update(
        state=state,
        pending_operation_id=f"boot-a:4:{action_kind}:{target_node}",
    )
    members[1] = replace(members[1], record=standby)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        (members[0], members[1]),
        rearm_command="unused",
    )

    assert view.overall == "TRANSITIONING"
    assert "unexpected-controller-operation" not in repr(view.summary_rows)


def test_vm_ha_status_view_allows_post_effect_cloud_truth_to_lead_pending_promotion() -> None:
    members = list(_vm_ha_view_members(owner_node_id="node-passive"))
    promoted = dict(members[1].record or {})
    promoted.update(
        state="promoting",
        data_plane_mode="passive",
        observed_owner_node_id="node-active",
        promotion_ready=False,
        standby_ready=True,
        standby_tunnel_state="warm",
        pending_operation_id="boot-a:4:confirm-candidate-ownership:node-passive",
    )
    members[1] = replace(members[1], record=promoted)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-1", "node-passive", None, ()),
        (members[0], members[1]),
        rearm_command="unused",
    )

    assert view.overall == "TRANSITIONING"
    assert "cloud-controller-owner-conflict" not in repr(view.summary_rows)


def test_vm_ha_status_view_treats_owner_enter_passive_action_as_transitioning() -> None:
    members = list(_vm_ha_view_members())
    owner = dict(members[0].record or {})
    owner.update(
        state="normal",
        data_plane_mode="blocked",
        promotion_ready=False,
        pending_operation_id="boot-a:4:enter-passive:node-active",
    )
    members[0] = replace(members[0], record=owner)
    authority = _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ())

    view = _vm_ha_status_view(
        authority,
        (members[0], members[1]),
        rearm_command="unused",
    )
    assert view.overall == "TRANSITIONING"
    assert "authoritative-owner-not-serving" not in repr(view.summary_rows)

    owner["pending_operation_id"] = "arbitrary-operation"
    members[0] = replace(members[0], record=owner)
    invalid = _vm_ha_status_view(
        authority,
        (members[0], members[1]),
        rearm_command="unused",
    )
    assert invalid.overall == "BLOCKED"


def test_vm_ha_status_renderer_emits_one_four_column_table() -> None:
    view = _vm_ha_status_view(
        _VMHACloudAuthority(
            "active", "exact", "nebius-vpn-gw-1", "node-passive", None, ()
        ),
        _vm_ha_view_members(owner_node_id="node-passive"),
        rearm_command="nebius-vpngw vm-ha-rearm --local-config-file fixture.yaml",
    )
    output = io.StringIO()

    _render_vm_ha_status(
        Console(file=output, color_system=None, width=240),
        view,
    )

    rendered = output.getvalue()
    assert rendered.count("VM-HA Status — HEALTHY") == 1
    assert all(header in rendered for header in ("Gateway", "Role", "mTLS", "Ready"))
    assert all(
        header not in rendered
        for header in ("Field", "Value", "Details", "Controller", "Data Plane")
    )
    assert rendered.count("nebius-vpn-gw-0") == 1
    assert rendered.count("nebius-vpn-gw-1") == 1
    assert "│ nebius-vpn-gw-0 │ standby" in rendered
    assert "│ nebius-vpn-gw-1 │ active" in rendered
    assert all(
        misleading not in rendered
        for misleading in ("owner (", "standby (", "candidate (", "(active)", "(passive)")
    )
    assert "\x1b" not in rendered
    assert "node-active" not in rendered
    assert "shared-private" not in rendered


def test_vm_ha_status_renderer_colors_only_semantic_health_cells() -> None:
    healthy = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        _vm_ha_view_members(),
        rearm_command="unused",
    )
    members = list(_vm_ha_view_members())
    blocked_record = dict(members[0].record or {})
    blocked_mtls = dict(t.cast(dict[str, object], blocked_record["mtls"]))
    blocked_mtls["state"] = "invalid"
    blocked_record["mtls"] = blocked_mtls
    members[0] = replace(members[0], record=blocked_record)
    blocked = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        (members[0], members[1]),
        rearm_command="unused",
    )
    rendered_tables: list[t.Any] = []
    capture_console = SimpleNamespace(print=lambda table: rendered_tables.append(table))

    _render_vm_ha_status(capture_console, healthy)
    _render_vm_ha_status(capture_console, blocked)

    healthy_table, blocked_table = rendered_tables
    assert [column.header for column in healthy_table.columns] == [
        "Gateway",
        "Role",
        "mTLS",
        "Ready",
    ]
    assert str(healthy_table.title.spans[-1].style) == "bold green"
    assert [str(cell.style) for cell in healthy_table.columns[2]._cells] == ["green", "green"]
    assert [str(cell.style) for cell in healthy_table.columns[3]._cells] == ["green", "green"]
    assert str(blocked_table.title.spans[-1].style) == "bold red"
    assert [str(cell.style) for cell in blocked_table.columns[2]._cells] == ["red", "green"]
    assert [str(cell.style) for cell in blocked_table.columns[3]._cells] == ["red", "red"]


def test_vpn_gateway_status_table_has_exact_columns_and_folds_complete_tunnel_name() -> None:
    table = _vpn_gateway_status_table()
    assert [column.header for column in table.columns] == [
        "Tunnel",
        "Configured Role",
        "Gateway VM",
        "IPsec",
        "BGP",
        "Peer IP",
        "Encryption",
        "BGP Uptime",
    ]
    assert table.columns[0].overflow == "fold"

    tunnel_name = "tunnel-" + "x" * 57
    table.add_row(
        tunnel_name,
        "active",
        "gateway-0",
        "Established",
        "Established",
        "192.0.2.1",
        "AES_GCM",
        "1m",
    )
    output = io.StringIO()
    Console(file=output, color_system=None, width=100).print(table)
    first_column_fragments = [
        line.split("│")[1].strip()
        for line in output.getvalue().splitlines()
        if line.startswith("│") and line.split("│")[1].strip() != "Tunnel"
    ]
    assert "…" not in "".join(first_column_fragments)
    assert "".join(first_column_fragments) == tunnel_name


def test_status_skips_all_vm_ha_work_for_non_ha_plan(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.config.yaml"
    plan = SimpleNamespace(
        gateway_group=SimpleNamespace(name="gateway", region="eu-test1"),
        vm_ha=None,
        iter_instance_configs=lambda: (),
    )
    manager = SimpleNamespace(_get_client=lambda: None)

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch(
            "nebius_vpngw.cli.load_local_config",
            return_value={"gateway": {}, "gateway_group": {}},
        ),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value=None),
        patch("nebius_vpngw.cli.VMManager", return_value=manager),
        patch("nebius_vpngw.cli.VMHALifecycleStore") as lifecycle_store,
        patch("nebius_vpngw.cli._fetch_vm_ha_agent_status") as fetch_status,
        patch("nebius_vpngw.cli._render_vm_ha_status") as render_status,
    ):
        status(local_config_file=config_path, project_id=None, zone=None)

    lifecycle_store.assert_not_called()
    fetch_status.assert_not_called()
    render_status.assert_not_called()


def test_status_loads_real_config_without_resolving_unused_psk(
    tmp_path: Path,
    sample_config: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variable = "STATUS_COMMAND_UNUSED_PSK"
    monkeypatch.delenv(variable, raising=False)
    sample_config["connections"][0]["tunnels"][0]["psk"] = f"${{{variable}}}"
    config_path = tmp_path / "gateway.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    plan = SimpleNamespace(
        gateway_group=SimpleNamespace(name="gateway", region="eu-test1"),
        vm_ha=None,
        iter_instance_configs=lambda: (),
    )
    manager = SimpleNamespace(_get_client=lambda: None)
    loaded_configs: list[dict[str, t.Any]] = []

    def merge(config: dict[str, t.Any], peer_files: list[Path]) -> SimpleNamespace:
        assert peer_files == []
        loaded_configs.append(config)
        return plan

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.merge_with_peer_configs", side_effect=merge),
        patch("nebius_vpngw.cli._ensure_authentication", return_value=None),
        patch("nebius_vpngw.cli.VMManager", return_value=manager),
    ):
        status(local_config_file=config_path, project_id=None, zone=None)

    assert loaded_configs[0]["connections"][0]["tunnels"][0]["psk"] == f"${{{variable}}}"


@pytest.mark.parametrize(
    ("missing_pin", "expected_overall", "expected_fetches"),
    (
        (None, "HEALTHY", 2),
        ("nebius-vpn-gw-1", "DEGRADED", 1),
    ),
)
def test_status_collects_read_only_vm_ha_evidence_and_renders_one_view(
    tmp_path: Path,
    missing_pin: str | None,
    expected_overall: str,
    expected_fetches: int,
) -> None:
    config_path = tmp_path / "gateway.config.yaml"
    target = {
        "network_id": "network-1",
        "project_id": "project-test",
        "route_table_id": "route-table-1",
        "workload_subnet_id": "subnet-workload",
    }
    lifecycle = replace(
        _lifecycle_state(),
        route_targets=(json.dumps(target, sort_keys=True, separators=(",", ":")),),
    )
    digests = SimpleNamespace(
        configuration="a" * 64,
        static_routes="b" * 64,
        bgp_policy="c" * 64,
    )
    configs = (
        SimpleNamespace(
            hostname="nebius-vpn-gw-0",
            external_ip=None,
            config_yaml=yaml.safe_dump({"vm_ha": {"cluster_id": "cluster"}}),
            vm_ha_node=SimpleNamespace(node_id="node-active", role=SimpleNamespace(value="active")),
            vm_ha_generation=SimpleNamespace(generation_id="a" * 64, digests=digests),
        ),
        SimpleNamespace(
            hostname="nebius-vpn-gw-1",
            external_ip=None,
            config_yaml=yaml.safe_dump({"vm_ha": {"cluster_id": "cluster"}}),
            vm_ha_node=SimpleNamespace(
                node_id="node-passive", role=SimpleNamespace(value="passive")
            ),
            vm_ha_generation=SimpleNamespace(generation_id="a" * 64, digests=digests),
        ),
    )
    gateway_group = SimpleNamespace(name="nebius-vpn-gw", region="eu-test1", vm_ha=object())
    plan = SimpleNamespace(
        gateway_group=gateway_group,
        vm_ha=object(),
        iter_instance_configs=lambda: configs,
    )
    observation = {
        "members": [
            {
                "aliases": ["shared-private"],
                "compute_id": "compute-0",
                "instance_name": "nebius-vpn-gw-0",
                "network_interface_name": "eth0",
                "present": True,
            },
            {
                "aliases": [],
                "compute_id": "compute-1",
                "instance_name": "nebius-vpn-gw-1",
                "network_interface_name": "eth0",
                "present": True,
            },
        ],
        "route_targets": [target],
        "routes": [
            {
                "allocation_id": "shared-private",
                "authority_labels": NebiusSDKRouteBackend._authority_labels(
                    cluster_id=lifecycle.cluster_id,
                    allocation_id=lifecycle.allocation_id,
                    route_target=VMHARouteTarget.model_validate(target),
                    route_kind="bgp",
                ),
                "name": "vpngw-ha-fixture",
                "prefix": "10.0.0.0/8",
                "route_table_id": "route-table-1",
            }
        ],
        "shared_allocation": {
            "allocation_id": "shared-private",
            "owner": {
                "compute_id": "compute-0",
                "network_interface_name": "eth0",
            },
            "present": True,
        },
    }
    observation_calls: list[tuple[object, list[str]]] = []

    class ReadOnlyManager:
        def _get_client(self) -> None:
            return None

        def get_vm_public_ip(self, hostname: str) -> str:
            return {
                "nebius-vpn-gw-0": "192.0.2.10",
                "nebius-vpn-gw-1": "192.0.2.11",
            }[hostname]

        def observe_vm_ha_migration_state(
            self, spec: object, prefixes: list[str]
        ) -> dict[str, object]:
            observation_calls.append((spec, prefixes))
            return observation

    records = {
        "nebius-vpn-gw-0": {
            **_vm_ha_view_record(
                "node-active",
                "node-active",
                owner=True,
                rearm_phase="running",
            ),
            "schema": "nebius-vpngw/vm-ha-status-v1",
            "cluster_id": "cluster",
            "configured_role": "active",
            "generation_id": "a" * 64,
            "digests": {
                "configuration": "a" * 64,
                "static_routes": "b" * 64,
                "bgp_policy": "c" * 64,
            },
            "allocation_id": "shared-private",
            "route_runtime_id": "route-runtime",
        },
        "nebius-vpn-gw-1": {
            **_vm_ha_view_record("node-passive", "node-active", owner=False, standby_ready=True),
            "schema": "nebius-vpngw/vm-ha-status-v1",
            "cluster_id": "cluster",
            "configured_role": "passive",
            "generation_id": "a" * 64,
            "digests": {
                "configuration": "a" * 64,
                "static_routes": "b" * 64,
                "bgp_policy": "c" * 64,
            },
            "allocation_id": "shared-private",
            "route_runtime_id": "route-runtime",
        },
    }
    rendered: list[t.Any] = []
    policies = {
        "nebius-vpn-gw-0": object(),
        "nebius-vpn-gw-1": object(),
    }

    def require_member_policy(host_pairs, *, enrollment_hosts, trust_scope):
        pairs = tuple(host_pairs)
        assert len(pairs) == 1
        assert enrollment_hosts == set()
        assert trust_scope.project_id == "project-test"
        if pairs[0][0] == missing_pin:
            raise ValueError("missing exact pin")
        return policies[pairs[0][0]]

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch(
            "nebius_vpngw.cli.load_local_config",
            return_value={
                "project_id": "project-test",
                "gateway": {"local_prefixes": ["10.0.0.0/8"]},
                "gateway_group": {
                    "vm_spec": {
                        "ssh_username": "operator",
                        "ssh_private_key_path": str(tmp_path / "id_ed25519"),
                    }
                },
                "connections": [],
            },
        ),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value=None),
        patch("nebius_vpngw.cli.VMManager", return_value=ReadOnlyManager()),
        patch(
            "nebius_vpngw.cli.VMHALifecycleStore",
            return_value=SimpleNamespace(read=lambda **_kwargs: lifecycle),
        ),
        patch(
            "nebius_vpngw.cli.require_vm_ha_ssh_policy",
            side_effect=require_member_policy,
        ) as require_policy,
        patch("nebius_vpngw.cli._build_ssh_base_cmd", return_value=["ssh"]) as build_ssh,
        patch(
            "nebius_vpngw.cli._fetch_vm_ha_agent_status",
            side_effect=lambda **kwargs: records[kwargs["hostname"]],
        ) as fetch_status,
        patch(
            "nebius_vpngw.cli._render_vm_ha_status",
            side_effect=lambda _console, view: rendered.append(view),
        ),
        patch(
            "subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr=""),
        ) as run_command,
    ):
        status(local_config_file=config_path, project_id=None, zone=None)

    assert observation_calls == [(gateway_group, ["10.0.0.0/8"])]
    assert require_policy.call_count == 2
    assert build_ssh.call_count > 0
    available_policies = {
        hostname: policy for hostname, policy in policies.items() if hostname != missing_pin
    }
    assert {
        (call.kwargs["hostname"], call.kwargs["ssh_policy"]) for call in build_ssh.call_args_list
    } == set(available_policies.items())
    assert fetch_status.call_count == expected_fetches
    assert {
        (
            call.kwargs["hostname"],
            call.kwargs["username"],
            call.kwargs["key_path"],
            call.kwargs["ssh_policy"],
        )
        for call in fetch_status.call_args_list
    } == {
        (
            hostname,
            "operator",
            tmp_path / "id_ed25519",
            policy,
        )
        for hostname, policy in available_policies.items()
    }
    assert len(rendered) == 1
    assert rendered[0].overall == expected_overall
    assert len(rendered[0].member_rows) == 2
    assert any(
        call.args
        and isinstance(call.args[0], list)
        and isinstance(call.args[0][-1], str)
        and "table_220_rule" in call.args[0][-1]
        and "'table', 'all'" in call.args[0][-1]
        and "table_220_routes" in call.args[0][-1]
        and "('220', 'ipsec')" in call.args[0][-1]
        and "'table', '220'" not in call.args[0][-1]
        and "tokens[index + 1] == '220'" in call.args[0][-1]
        and "startswith('220:')" not in call.args[0][-1]
        and "table_220_error" in call.args[0][-1]
        and "broad_apipa_error" in call.args[0][-1]
        for call in run_command.call_args_list
    )
    if missing_pin is not None:
        missing_row = next(row for row in rendered[0].member_rows if row[0] == missing_pin)
        assert missing_row[2:] == ("unknown", "unknown")


def test_vm_ha_status_view_degrades_hygiene_and_recommends_periodic_repair() -> None:
    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        _vm_ha_view_members(
            standby_ready=False,
            standby_state="degraded",
            standby_reasons=[
                "routing-hygiene-not-ready",
                "routing-hygiene-not-ready",
            ],
        ),
        rearm_command="nebius-vpngw vm-ha-rearm --local-config-file fixture.yaml",
    )

    assert view.overall == "DEGRADED"
    overall = next(row for row in view.summary_rows if row[0] == "Overall")
    action = next(row for row in view.summary_rows if row[0] == "Action")
    assert overall[2].count("routing-hygiene-not-ready") == 1
    assert action[1] == "wait"
    assert "five-minute routing-maintenance cycle" in action[2]
    assert "supported apply workflow" in action[2]


def test_vm_ha_status_view_distinguishes_transition_block_and_unknown() -> None:
    transitioning_members = list(_vm_ha_view_members())
    owner = dict(transitioning_members[0].record or {})
    owner.update(
        state="ownership-transfer",
        data_plane_mode="blocked",
        promotion_ready=False,
        observed_owner_node_id=None,
        apply_locked=True,
        apply_operation_id="operation-a",
    )
    transitioning_members[0] = replace(transitioning_members[0], record=owner)
    transitioning = _vm_ha_status_view(
        _VMHACloudAuthority(
            "activating",
            "transitioning",
            "nebius-vpn-gw-0",
            "node-active",
            "operation-a",
            ("lifecycle-activating",),
        ),
        (transitioning_members[0], transitioning_members[1]),
        rearm_command="unused",
    )
    assert transitioning.overall == "TRANSITIONING"

    foreign_lock_members = list(_vm_ha_view_members())
    foreign_lock_owner = dict(foreign_lock_members[0].record or {})
    foreign_lock_owner.update(
        apply_locked=True,
        apply_operation_id="foreign-operation",
    )
    foreign_lock_members[0] = replace(foreign_lock_members[0], record=foreign_lock_owner)
    foreign_lock = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        (foreign_lock_members[0], foreign_lock_members[1]),
        rearm_command="unused",
    )
    assert foreign_lock.overall == "BLOCKED"

    conflicting_members = list(_vm_ha_view_members())
    conflicting = dict(conflicting_members[1].record or {})
    conflicting.update(
        data_plane_mode="active",
        promotion_ready=True,
        observed_owner_node_id="node-passive",
    )
    conflicting_members[1] = replace(conflicting_members[1], record=conflicting)
    blocked = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        (conflicting_members[0], conflicting_members[1]),
        rearm_command="unused",
    )
    assert blocked.overall == "BLOCKED"

    unknown_members = tuple(
        replace(member, condition="unknown", reason="agent-status-unavailable", record=None)
        for member in _vm_ha_view_members()
    )
    unknown = _vm_ha_status_view(
        _VMHACloudAuthority(
            "unknown", "unknown", None, None, None, ("cloud-observation-unavailable",)
        ),
        t.cast(tuple[_VMHAMemberEvidence, _VMHAMemberEvidence], unknown_members),
        rearm_command="unused",
    )
    assert unknown.overall == "UNKNOWN"
    assert tuple(row[1] for row in unknown.member_rows) == ("unknown", "unknown")


def test_vm_ha_status_view_missing_standby_is_degraded_without_raw_error() -> None:
    members = list(_vm_ha_view_members())
    members[1] = replace(
        members[1],
        condition="unknown",
        reason="agent-status-unavailable",
        record=None,
    )
    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        (members[0], members[1]),
        rearm_command="unused",
    )

    assert view.overall == "DEGRADED"
    rendered = repr((view.summary_rows, view.member_rows))
    assert "Traceback" not in rendered
    assert "standby-status-unavailable" in rendered
    assert view.member_rows[1] == ("nebius-vpn-gw-1", "standby", "unknown", "unknown")


def test_vm_ha_status_view_redacts_unknown_remote_reason_values() -> None:
    sentinels = (
        "allocation-abc123",
        "node-deadbeef",
        "operation-feedface",
        "/private/status/path",
        "RuntimeError: private endpoint",
    )
    members = list(_vm_ha_view_members())
    standby = dict(members[1].record or {})
    standby.update(
        state="degraded",
        standby_ready=False,
        reasons=[sentinels[0]],
        standby_readiness_reasons=[sentinels[1]],
        rearm_reason=sentinels[2],
        repair={"failure_fingerprint": [sentinels[3], sentinels[4]]},
    )
    members[1] = replace(members[1], record=standby)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        (members[0], members[1]),
        rearm_command="unused",
    )
    rendered = repr((view.summary_rows, view.member_rows))

    assert view.overall == "DEGRADED"
    assert "controller-reported-condition" in rendered
    assert all(sentinel not in rendered for sentinel in sentinels)


def test_vm_ha_status_wait_fails_immediately_on_identity_mismatch() -> None:
    with (
        patch(
            "nebius_vpngw.cli._fetch_vm_ha_agent_status",
            side_effect=_VMHAAgentStatusPermanent("foreign node"),
        ),
        patch("nebius_vpngw.cli.time.sleep") as sleep,
        pytest.raises(ValueError, match="foreign node"),
    ):
        _wait_for_vm_ha_agent_status(predicate=lambda _payload: True, target="fixture")

    sleep.assert_not_called()


def test_vm_ha_status_fetch_rejects_malformed_json_without_retry() -> None:
    with (
        patch("nebius_vpngw.cli._build_ssh_base_cmd", return_value=[]),
        patch(
            "nebius_vpngw.cli.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="{", stderr=""),
        ),
        pytest.raises(_VMHAAgentStatusPermanent, match="malformed status JSON"),
    ):
        _fetch_vm_ha_agent_status(
            target="192.0.2.1",
            hostname="gateway-0",
            username="ubuntu",
            key_path=None,
            ssh_policy=object(),
            inst_cfg=object(),
        )


def test_vm_ha_status_wait_retries_same_node_staleness_then_succeeds() -> None:
    expected = {"data_plane_mode": "blocked"}
    with (
        patch(
            "nebius_vpngw.cli._fetch_vm_ha_agent_status",
            side_effect=[
                _VMHAAgentStatusStale("previous generation"),
                expected,
            ],
        ) as fetch,
        patch("nebius_vpngw.cli.time.sleep") as sleep,
    ):
        assert (
            _wait_for_vm_ha_agent_status(
                predicate=lambda payload: payload is expected,
                target="fixture",
            )
            is expected
        )

    assert fetch.call_count == 2
    sleep.assert_called_once()


def test_vm_ha_status_wait_timeout_retains_last_stale_diagnostic() -> None:
    with (
        patch(
            "nebius_vpngw.cli._fetch_vm_ha_agent_status",
            side_effect=_VMHAAgentStatusStale("expected apply lock is not visible"),
        ),
        patch("nebius_vpngw.cli.time.monotonic", side_effect=[0.0, 0.0, 2.0]),
        patch("nebius_vpngw.cli.time.sleep"),
        pytest.raises(
            RuntimeError,
            match="expected apply lock is not visible",
        ),
    ):
        _wait_for_vm_ha_agent_status(
            predicate=lambda _payload: True,
            timeout_seconds=1.0,
            target="fixture",
        )


def test_vm_ha_service_account_requires_reviewed_current_project_role(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_ensure(*args, **kwargs):
        observed["roles"] = kwargs["role_ids"]
        observed["strict"] = kwargs["strict_role_grants"]
        return "token"

    monkeypatch.setattr(vpngw_sa, "ensure_service_account_and_token", fake_ensure)

    assert (
        vpngw_sa.ensure_vm_ha_service_account_and_token(
            "gateway-ha",
            "tenant",
            "project",
            "region",
            verified_role_ids=("editor",),
        )
        == "token"
    )
    assert observed["roles"] == ("editor",)
    assert observed["strict"] is True

    with pytest.raises(ValueError, match="reviewed allowlist"):
        vpngw_sa.ensure_vm_ha_service_account_and_token(
            "gateway-ha",
            "tenant",
            "project",
            "region",
            verified_role_ids=("roles/editor",),
        )

    with pytest.raises(ValueError, match="reviewed allowlist"):
        vpngw_sa.ensure_vm_ha_service_account_and_token(
            "gateway-ha",
            "tenant",
            "project",
            "region",
            verified_role_ids=("compute.editor", "vpc.editor"),
        )


def test_requested_service_account_never_falls_back_to_ambient_credentials(
    monkeypatch,
) -> None:
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(vpngw_sa, "ensure_service_account_and_token", lambda **kwargs: None)

    with pytest.raises(typer.Exit):
        _requested_apply_service_account_token(
            sa_name="gateway-runtime",
            tenant_id=None,
            project_id="project-test",
            region_id=None,
            vm_ha_enabled=False,
        )

    assert "NEBIUS_IAM_TOKEN" not in os.environ


def test_vm_ha_operator_command_rejects_stale_agent_identity(monkeypatch) -> None:
    generation = SimpleNamespace(generation_id="a" * 64)
    active = SimpleNamespace(
        hostname="gateway-0",
        external_ip="203.0.113.10",
        vm_ha_node=SimpleNamespace(node_id="node-a", role=SimpleNamespace(value="active")),
        vm_ha_generation=generation,
    )
    passive = SimpleNamespace(
        hostname="gateway-1",
        external_ip="203.0.113.11",
        vm_ha_node=SimpleNamespace(node_id="node-b", role=SimpleNamespace(value="passive")),
        vm_ha_generation=generation,
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
        iter_instance_configs=lambda: iter([active, passive]),
    )
    local_cfg = {"gateway_group": {"vm_spec": {}}}
    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _: local_cfg)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", lambda *_, **__: None)
    observed_timeout: list[float] = []

    def run(*args, **kwargs):
        observed_timeout.append(kwargs["timeout"])
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "schema": "nebius-vpngw/vm-ha-status-v1",
                    "cluster_id": "cluster-a",
                    "node_id": "another-node",
                    "configured_role": "passive",
                    "generation_id": "a" * 64,
                }
            ),
        )

    monkeypatch.setattr("nebius_vpngw.cli.subprocess.run", run)

    with pytest.raises(ValueError, match="stale node identity"):
        _run_vm_ha_operator_command(
            local_config_file=Path("config.yaml"),
            agent_flag="--vm-ha-status",
            timeout_seconds=1.25,
        )
    assert observed_timeout == [1.25]


def _manual_failback_plan() -> SimpleNamespace:
    generation = SimpleNamespace(generation_id="a" * 64)
    active = SimpleNamespace(
        hostname="nebius-vpn-gw-0",
        external_ip="203.0.113.10",
        vm_ha_node=SimpleNamespace(node_id="node-active", role=SimpleNamespace(value="active")),
        vm_ha_generation=generation,
    )
    passive = SimpleNamespace(
        hostname="nebius-vpn-gw-1",
        external_ip="203.0.113.11",
        vm_ha_node=SimpleNamespace(node_id="node-passive", role=SimpleNamespace(value="passive")),
        vm_ha_generation=generation,
    )
    return SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster"),
        gateway_group=SimpleNamespace(name="nebius-vpn-gw", region="eu-west1"),
        iter_instance_configs=lambda: iter((active, passive)),
    )


def _manual_failback_compute(
    *, state: InstanceCloudState, alias_present: bool, revision: str
) -> SimpleNamespace:
    return SimpleNamespace(
        state=state,
        resource_version=revision,
        has_alias_allocation=lambda nic, allocation: (
            alias_present if (nic, allocation) == ("eth0", "shared-private") else False
        ),
    )


def test_manual_failback_starts_only_exact_stopped_configured_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = _lifecycle_state()
    plan = _manual_failback_plan()
    passive_owner = AllocationOwner("compute-1", "eth0")
    observations = iter(
        (
            SimpleNamespace(
                allocation=SimpleNamespace(owner=passive_owner),
                former=_manual_failback_compute(
                    state=InstanceCloudState.STOPPED, alias_present=False, revision="22"
                ),
                candidate=_manual_failback_compute(
                    state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
                ),
            ),
            SimpleNamespace(
                allocation=SimpleNamespace(owner=passive_owner),
                former=_manual_failback_compute(
                    state=InstanceCloudState.RUNNING, alias_present=False, revision="23"
                ),
                candidate=_manual_failback_compute(
                    state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
                ),
            ),
            SimpleNamespace(
                allocation=SimpleNamespace(owner=passive_owner),
                former=_manual_failback_compute(
                    state=InstanceCloudState.RUNNING, alias_present=False, revision="23"
                ),
                candidate=_manual_failback_compute(
                    state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
                ),
            ),
        )
    )
    adapter = SimpleNamespace(observe_cluster=lambda **kwargs: next(observations))
    starts: list[tuple[str, str]] = []
    cloud = SimpleNamespace(
        get_instance=lambda *_: None,
        stop_instance=lambda *_: None,
        get_allocation=lambda *_: None,
        set_alias_allocation=lambda *_: None,
        start_instance=lambda instance_id, operation_id: starts.append((instance_id, operation_id)),
    )
    waits: list[tuple[str, str, str, float]] = []

    class FakeManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def _get_client(self) -> object:
            return object()

        def wait_for_vm_ha_member_ssh(
            self, name: str, public_ip: str, *, username: str, timeout: float
        ) -> None:
            waits.append((name, public_ip, username, timeout))

    monkeypatch.setattr(
        "nebius_vpngw.cli.load_local_config",
        lambda _: {
            "project_id": "project-test",
            "tenant_id": "tenant-test",
            "region_id": "region-test",
            "gateway_group": {"vm_spec": {"ssh_username": "ubuntu"}},
        },
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli.VMHALifecycleStore",
        lambda _: SimpleNamespace(read=lambda **kwargs: lifecycle),
    )
    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", lambda *_args, **_kw: object())
    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", lambda **_kw: "token")
    monkeypatch.setattr("nebius_vpngw.cli.VMManager", FakeManager)
    monkeypatch.setattr("nebius_vpngw.cli.NebiusSDKCloudClient", lambda _sdk, **_kwargs: cloud)
    monkeypatch.setattr("nebius_vpngw.cli.VMHACloudAdapter", lambda **_kw: adapter)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)
    operator_calls: list[tuple[str, str | None]] = []

    def operator(**kwargs):
        operator_calls.append((kwargs["agent_flag"], kwargs.get("configured_role")))
        if kwargs["agent_flag"] == "--vm-ha-rearm-request":
            return [{"schema": "nebius-vpngw/vm-ha-rearm-request-v1"}]
        return [
            {
                "standby_ready": True,
                "standby_readiness_reasons": [],
                "data_plane_mode": "passive",
                "observed_owner_node_id": "node-passive",
                "apply_locked": False,
                "pending_operation_id": None,
            }
        ]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_operator_command", operator)

    _prepare_vm_ha_manual_failback_target(local_config_file=Path("config.yaml"))

    assert starts == []
    assert operator_calls == [
        ("--vm-ha-rearm-request", "passive"),
        ("--vm-ha-status", "active"),
        ("--vm-ha-status", "active"),
    ]
    assert len(waits) == 1
    assert waits[0][:3] == ("nebius-vpn-gw-0", "203.0.113.10", "ubuntu")
    assert 0 < waits[0][3] <= 300


def test_manual_failback_does_not_start_an_already_active_exact_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = _lifecycle_state()
    plan = _manual_failback_plan()
    active_owner = AllocationOwner("compute-0", "eth0")
    observation = SimpleNamespace(
        allocation=SimpleNamespace(owner=active_owner),
        former=_manual_failback_compute(
            state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
        ),
        candidate=_manual_failback_compute(
            state=InstanceCloudState.RUNNING, alias_present=False, revision="22"
        ),
    )
    adapter = SimpleNamespace(observe_cluster=lambda **kwargs: observation)
    starts: list[tuple[str, str]] = []
    waits: list[str] = []
    cloud = SimpleNamespace(
        get_instance=lambda *_: None,
        stop_instance=lambda *_: None,
        get_allocation=lambda *_: None,
        set_alias_allocation=lambda *_: None,
        start_instance=lambda instance_id, operation_id: starts.append((instance_id, operation_id)),
    )

    class FakeManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def _get_client(self) -> object:
            return object()

        def wait_for_vm_ha_member_ssh(self, name: str, *_args, **_kwargs) -> None:
            waits.append(name)

    monkeypatch.setattr(
        "nebius_vpngw.cli.load_local_config",
        lambda _: {
            "project_id": "project-test",
            "gateway_group": {"vm_spec": {}},
        },
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli.VMHALifecycleStore",
        lambda _: SimpleNamespace(read=lambda **kwargs: lifecycle),
    )
    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", lambda *_args, **_kw: object())
    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", lambda **_kw: "token")
    monkeypatch.setattr("nebius_vpngw.cli.VMManager", FakeManager)
    monkeypatch.setattr("nebius_vpngw.cli.NebiusSDKCloudClient", lambda _sdk, **_kwargs: cloud)
    monkeypatch.setattr("nebius_vpngw.cli.VMHACloudAdapter", lambda **_kw: adapter)
    operator_calls: list[tuple[str, str | None]] = []

    def operator(**kwargs):
        operator_calls.append((kwargs["agent_flag"], kwargs.get("configured_role")))
        return [
            {
                "state": "active",
                "promotion_ready": True,
                "data_plane_mode": "active",
                "observed_owner_node_id": "node-active",
                "apply_locked": False,
                "pending_operation_id": None,
            }
        ]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_operator_command", operator)

    _prepare_vm_ha_manual_failback_target(local_config_file=Path("config.yaml"))

    assert starts == []
    assert waits == ["nebius-vpn-gw-0"]
    assert operator_calls == [("--vm-ha-status", "active")]


def test_manual_failback_reproves_promoted_owner_after_pinned_ssh_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = _lifecycle_state()
    plan = _manual_failback_plan()
    passive_owner = AllocationOwner("compute-1", "eth0")
    foreign_owner = AllocationOwner("foreign", "eth0")
    observations = iter(
        (
            SimpleNamespace(
                allocation=SimpleNamespace(owner=passive_owner),
                former=_manual_failback_compute(
                    state=InstanceCloudState.STOPPED, alias_present=False, revision="22"
                ),
                candidate=_manual_failback_compute(
                    state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
                ),
            ),
            SimpleNamespace(
                allocation=SimpleNamespace(owner=passive_owner),
                former=_manual_failback_compute(
                    state=InstanceCloudState.RUNNING, alias_present=False, revision="23"
                ),
                candidate=_manual_failback_compute(
                    state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
                ),
            ),
            SimpleNamespace(
                allocation=SimpleNamespace(owner=foreign_owner),
                former=_manual_failback_compute(
                    state=InstanceCloudState.RUNNING, alias_present=False, revision="23"
                ),
                candidate=_manual_failback_compute(
                    state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
                ),
            ),
        )
    )
    cloud = SimpleNamespace(
        get_instance=lambda *_: None,
        stop_instance=lambda *_: None,
        get_allocation=lambda *_: None,
        set_alias_allocation=lambda *_: None,
        start_instance=lambda *_args: None,
    )

    class FakeManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def _get_client(self) -> object:
            return object()

        def wait_for_vm_ha_member_ssh(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr(
        "nebius_vpngw.cli.load_local_config",
        lambda _: {"project_id": "project-test", "gateway_group": {"vm_spec": {}}},
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli.VMHALifecycleStore",
        lambda _: SimpleNamespace(read=lambda **kwargs: lifecycle),
    )
    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", lambda *_args, **_kw: object())
    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", lambda **_kw: "token")
    monkeypatch.setattr("nebius_vpngw.cli.VMManager", FakeManager)
    monkeypatch.setattr("nebius_vpngw.cli.NebiusSDKCloudClient", lambda _sdk, **_kwargs: cloud)
    monkeypatch.setattr(
        "nebius_vpngw.cli.VMHACloudAdapter",
        lambda **_kw: SimpleNamespace(observe_cluster=lambda **kwargs: next(observations)),
    )
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_operator_command",
        lambda **kwargs: (
            [{"schema": "nebius-vpngw/vm-ha-rearm-request-v1"}]
            if kwargs["agent_flag"] == "--vm-ha-rearm-request"
            else [
                {
                    "standby_ready": True,
                    "standby_readiness_reasons": [],
                    "data_plane_mode": "passive",
                    "observed_owner_node_id": "node-passive",
                    "apply_locked": False,
                    "pending_operation_id": None,
                }
            ]
        ),
    )

    with pytest.raises(RuntimeError, match="owner or target evidence drifted"):
        _prepare_vm_ha_manual_failback_target(local_config_file=Path("config.yaml"))


def test_manual_failback_rejects_foreign_owner_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = _lifecycle_state()
    plan = _manual_failback_plan()
    observation = SimpleNamespace(
        allocation=SimpleNamespace(owner=AllocationOwner("foreign", "eth0")),
        former=_manual_failback_compute(
            state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
        ),
        candidate=_manual_failback_compute(
            state=InstanceCloudState.STOPPED, alias_present=False, revision="22"
        ),
    )
    cloud = SimpleNamespace(
        get_instance=lambda *_: None,
        stop_instance=lambda *_: None,
        get_allocation=lambda *_: None,
        set_alias_allocation=lambda *_: None,
        start_instance=lambda *_args: pytest.fail("foreign owner must not start a Compute"),
    )

    class FakeManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def _get_client(self) -> object:
            return object()

    monkeypatch.setattr(
        "nebius_vpngw.cli.load_local_config",
        lambda _: {"project_id": "project-test", "gateway_group": {"vm_spec": {}}},
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli.VMHALifecycleStore",
        lambda _: SimpleNamespace(read=lambda **kwargs: lifecycle),
    )
    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", lambda *_args, **_kw: object())
    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", lambda **_kw: "token")
    monkeypatch.setattr("nebius_vpngw.cli.VMManager", FakeManager)
    monkeypatch.setattr("nebius_vpngw.cli.NebiusSDKCloudClient", lambda _sdk, **_kwargs: cloud)
    monkeypatch.setattr(
        "nebius_vpngw.cli.VMHACloudAdapter",
        lambda **_kw: SimpleNamespace(observe_cluster=lambda **kwargs: observation),
    )

    with pytest.raises(RuntimeError, match="no exact current owner"):
        _prepare_vm_ha_manual_failback_target(local_config_file=Path("config.yaml"))


@pytest.mark.parametrize(
    ("first_status", "unsafe", "expected_calls"),
    (
        pytest.param(
            {
                "standby_ready": False,
                "standby_readiness_reasons": ["current-boot-status-stale"],
                "data_plane_mode": "blocked",
                "observed_owner_node_id": None,
                "apply_locked": False,
                "pending_operation_id": None,
            },
            False,
            3,
            id="retry-stale-current-boot-projection",
        ),
        pytest.param(
            {
                "standby_ready": False,
                "standby_readiness_reasons": [],
                "data_plane_mode": "active",
                "observed_owner_node_id": "node-active",
                "apply_locked": False,
                "pending_operation_id": None,
            },
            True,
            1,
            id="reject-current-active",
        ),
        pytest.param(
            {
                "standby_ready": False,
                "standby_readiness_reasons": [],
                "data_plane_mode": "passive",
                "observed_owner_node_id": "foreign-node",
                "apply_locked": False,
                "pending_operation_id": None,
            },
            True,
            1,
            id="reject-foreign-owner",
        ),
    ),
)
def test_manual_failover_preflight_accepts_only_exact_running_active_owner(
    monkeypatch: pytest.MonkeyPatch,
    first_status: dict[str, object],
    unsafe: bool,
    expected_calls: int,
) -> None:
    lifecycle = _lifecycle_state()
    plan = _manual_failback_plan()
    active_owner = AllocationOwner("compute-0", "eth0")
    observation = SimpleNamespace(
        allocation=SimpleNamespace(owner=active_owner),
        former=_manual_failback_compute(
            state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
        ),
        candidate=_manual_failback_compute(
            state=InstanceCloudState.RUNNING, alias_present=False, revision="22"
        ),
    )

    class LifecycleStore:
        def __init__(self, _path: Path) -> None:
            pass

        def read(self, **_kwargs):
            return lifecycle

    class FakeManager:
        def __init__(self, **_kwargs) -> None:
            pass

        def _get_client(self):
            return object()

        def wait_for_vm_ha_member_ssh(self, *_args, **_kwargs) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli.load_local_config",
        lambda _path: {
            "project_id": "project-test",
            "gateway_group": {"vm_spec": {}},
        },
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr("nebius_vpngw.cli.VMHALifecycleStore", LifecycleStore)
    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", lambda *_, **__: None)
    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", lambda **_: "token")
    monkeypatch.setattr("nebius_vpngw.cli.VMManager", FakeManager)
    monkeypatch.setattr(
        "nebius_vpngw.cli.NebiusSDKCloudClient",
        lambda _sdk, **_kwargs: SimpleNamespace(
            get_instance=lambda *_: None,
            stop_instance=lambda *_: None,
            get_allocation=lambda *_: None,
            set_alias_allocation=lambda *_: None,
        ),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli.VMHACloudAdapter",
        lambda **_kwargs: SimpleNamespace(observe_cluster=lambda **_observe_kwargs: observation),
    )
    status_records = iter(
        (
            first_status,
            {
                "standby_ready": True,
                "standby_readiness_reasons": [],
                "data_plane_mode": "passive",
                "observed_owner_node_id": "node-active",
                "apply_locked": False,
                "pending_operation_id": None,
            },
            {
                "standby_ready": True,
                "standby_readiness_reasons": [],
                "data_plane_mode": "passive",
                "observed_owner_node_id": "node-active",
                "apply_locked": False,
                "pending_operation_id": None,
            },
        )
    )
    status_calls: list[str] = []
    status_timeouts: list[float] = []
    clock = [100.0]

    def operator(**kwargs):
        status_calls.append(kwargs["agent_flag"])
        status_timeouts.append(kwargs["timeout_seconds"])
        assert 0 < kwargs["timeout_seconds"] <= 300
        return [next(status_records)]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_operator_command", operator)
    monkeypatch.setattr("nebius_vpngw.cli.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "nebius_vpngw.cli.time.sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    if unsafe:
        with pytest.raises(RuntimeError, match="unsafe standby evidence"):
            _require_vm_ha_manual_failover_target(local_config_file=Path("config.yaml"))
    else:
        _require_vm_ha_manual_failover_target(local_config_file=Path("config.yaml"))
    assert status_calls == ["--vm-ha-status"] * expected_calls
    assert status_timeouts == pytest.approx([300.0] if unsafe else [300.0, 299.0, 299.0])


def test_manual_failover_preflight_rearms_stopped_passive_through_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = _lifecycle_state()
    plan = _manual_failback_plan()
    stopped = SimpleNamespace(
        allocation=SimpleNamespace(owner=AllocationOwner("compute-0", "eth0")),
        former=_manual_failback_compute(
            state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
        ),
        candidate=_manual_failback_compute(
            state=InstanceCloudState.STOPPED, alias_present=False, revision="22"
        ),
    )
    running = SimpleNamespace(
        allocation=SimpleNamespace(owner=AllocationOwner("compute-0", "eth0")),
        former=_manual_failback_compute(
            state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
        ),
        candidate=_manual_failback_compute(
            state=InstanceCloudState.RUNNING, alias_present=False, revision="23"
        ),
    )
    observations = iter((stopped, running, running))

    class LifecycleStore:
        def __init__(self, _path: Path) -> None:
            pass

        def read(self, **_kwargs):
            return lifecycle

    class FakeManager:
        def __init__(self, **_kwargs) -> None:
            pass

        def _get_client(self):
            return object()

        def wait_for_vm_ha_member_ssh(self, *_args, **_kwargs) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli.load_local_config",
        lambda _path: {
            "project_id": "project-test",
            "gateway_group": {"vm_spec": {}},
        },
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr("nebius_vpngw.cli.VMHALifecycleStore", LifecycleStore)
    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", lambda *_, **__: None)
    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", lambda **_: "token")
    monkeypatch.setattr("nebius_vpngw.cli.VMManager", FakeManager)
    monkeypatch.setattr(
        "nebius_vpngw.cli.NebiusSDKCloudClient",
        lambda _sdk, **_kwargs: SimpleNamespace(
            get_instance=lambda *_: None,
            stop_instance=lambda *_: None,
            get_allocation=lambda *_: None,
            set_alias_allocation=lambda *_: None,
        ),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli.VMHACloudAdapter",
        lambda **_kwargs: SimpleNamespace(
            observe_cluster=lambda **_observe_kwargs: next(observations)
        ),
    )
    calls: list[tuple[str, str | None]] = []

    def operator(**kwargs):
        calls.append((kwargs["agent_flag"], kwargs.get("configured_role")))
        assert 0 < kwargs["timeout_seconds"] <= 300
        if kwargs["agent_flag"] == "--vm-ha-rearm-request":
            return [{"schema": "nebius-vpngw/vm-ha-rearm-request-v1"}]
        return [
            {
                "standby_ready": True,
                "standby_readiness_reasons": [],
                "data_plane_mode": "passive",
                "observed_owner_node_id": "node-active",
                "apply_locked": False,
                "pending_operation_id": None,
            }
        ]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_operator_command", operator)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    _require_vm_ha_manual_failover_target(local_config_file=Path("config.yaml"))
    assert calls == [
        ("--vm-ha-rearm-request", "active"),
        ("--vm-ha-status", "passive"),
        ("--vm-ha-status", "passive"),
    ]


def test_vm_ha_rearm_starts_only_exact_stopped_alias_free_passive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = _lifecycle_state()
    plan = _manual_failback_plan()
    active_owner = AllocationOwner("compute-0", "eth0")
    stopped = SimpleNamespace(
        allocation=SimpleNamespace(owner=active_owner),
        former=_manual_failback_compute(
            state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
        ),
        candidate=_manual_failback_compute(
            state=InstanceCloudState.STOPPED, alias_present=False, revision="22"
        ),
    )
    running = SimpleNamespace(
        allocation=SimpleNamespace(owner=active_owner),
        former=_manual_failback_compute(
            state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
        ),
        candidate=_manual_failback_compute(
            state=InstanceCloudState.RUNNING, alias_present=False, revision="23"
        ),
    )
    observations = iter((stopped, running, running))
    starts: list[tuple[str, str]] = []
    waits: list[str] = []

    class LifecycleStore:
        def __init__(self, _path: Path) -> None:
            pass

        def read(self, **_kwargs):
            return lifecycle

    cloud = SimpleNamespace(
        get_instance=lambda *_: None,
        stop_instance=lambda *_: None,
        get_allocation=lambda *_: None,
        set_alias_allocation=lambda *_: None,
        start_instance=lambda compute_id, operation_id: starts.append((compute_id, operation_id)),
    )

    class FakeManager:
        def __init__(self, **_kwargs) -> None:
            pass

        def _get_client(self):
            return object()

        def wait_for_vm_ha_member_ssh(self, hostname: str, *_args, **_kwargs) -> None:
            waits.append(hostname)

    monkeypatch.setattr(
        "nebius_vpngw.cli.load_local_config",
        lambda _path: {
            "project_id": "project-test",
            "gateway_group": {"vm_spec": {}},
        },
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr("nebius_vpngw.cli.VMHALifecycleStore", LifecycleStore)
    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", lambda *_, **__: None)
    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", lambda **_: "token")
    monkeypatch.setattr("nebius_vpngw.cli.VMManager", FakeManager)
    monkeypatch.setattr("nebius_vpngw.cli.NebiusSDKCloudClient", lambda _sdk, **_kwargs: cloud)
    monkeypatch.setattr(
        "nebius_vpngw.cli.VMHACloudAdapter",
        lambda **_kwargs: SimpleNamespace(
            observe_cluster=lambda **_observe_kwargs: next(observations)
        ),
    )
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)
    operator_calls: list[tuple[str, str | None]] = []

    def operator(**kwargs):
        operator_calls.append((kwargs["agent_flag"], kwargs.get("configured_role")))
        if kwargs["agent_flag"] == "--vm-ha-rearm-request":
            return [{"schema": "nebius-vpngw/vm-ha-rearm-request-v1"}]
        return [
            {
                "standby_ready": True,
                "standby_readiness_reasons": [],
                "data_plane_mode": "passive",
                "observed_owner_node_id": "node-active",
                "apply_locked": False,
                "pending_operation_id": None,
            }
        ]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_operator_command", operator)

    _prepare_vm_ha_configured_passive_standby(local_config_file=Path("config.yaml"))

    assert starts == []
    assert operator_calls == [
        ("--vm-ha-rearm-request", "active"),
        ("--vm-ha-status", "passive"),
        ("--vm-ha-status", "passive"),
    ]
    assert waits == ["nebius-vpn-gw-1"]


@pytest.mark.parametrize(
    ("command", "target_role"),
    (("failback", "active"), ("failover", "passive")),
)
def test_repeated_planned_transfer_is_a_request_free_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    target_role: str,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("project_id: project-test\n", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_vpngw.cli._prepare_vm_ha_planned_target",
        lambda **_kwargs: SimpleNamespace(outcome="already-owner", record={}),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_operator_command",
        lambda **_kwargs: pytest.fail("same-owner command must not submit a transfer request"),
    )

    result = CliRunner().invoke(
        app,
        [command, "vm", "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "schema": "nebius-vpngw/vm-ha-planned-transfer-result-v1",
        "outcome": "already-owner",
        "target_role": target_role,
        "request_submitted": False,
    }
    help_result = CliRunner().invoke(app, [command, "vm", "--help"])
    assert help_result.exit_code == 0
    assert "no-op" in help_result.stdout


@pytest.mark.parametrize(
    ("command", "target_role", "agent_flag", "configured_role"),
    (
        ("failback", "active", "--vm-ha-manual-failback", "active"),
        ("failover", "passive", "--vm-ha-manual-failover", "passive"),
    ),
)
def test_nested_vm_transfer_routes_to_existing_operator_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    target_role: str,
    agent_flag: str,
    configured_role: str,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("project_id: project-test\n", encoding="utf-8")
    preparation_calls: list[str] = []
    operator_calls: list[tuple[str, str | None]] = []

    def prepare(**kwargs):
        preparation_calls.append(kwargs["target_role"])
        return SimpleNamespace(outcome="standby-ready", record={})

    def operator(**kwargs):
        operator_calls.append((kwargs["agent_flag"], kwargs.get("configured_role")))
        return [{"outcome": "submitted"}]

    monkeypatch.setattr("nebius_vpngw.cli._prepare_vm_ha_planned_target", prepare)
    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_operator_command", operator)

    result = CliRunner().invoke(
        app,
        [command, "vm", "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert preparation_calls == [target_role]
    assert operator_calls == [(agent_flag, configured_role)]


@pytest.mark.parametrize(
    "arguments",
    (
        ("vm-ha-failover",),
        ("vm-ha-failback",),
        ("failover", "legacy-tunnel"),
        ("failback", "legacy-tunnel"),
        ("failover",),
        ("failback",),
    ),
)
def test_removed_or_incomplete_transfer_paths_fail_before_effects(
    monkeypatch: pytest.MonkeyPatch,
    arguments: tuple[str, ...],
) -> None:
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_local_config",
        lambda *_args, **_kwargs: pytest.fail("parser rejection must precede config access"),
    )

    result = CliRunner().invoke(app, list(arguments))

    assert result.exit_code != 0
    if len(arguments) == 1 and arguments[0] in {"failover", "failback"}:
        assert "Usage:" in result.output
        assert "vm" in result.output
        assert "tunnel" in result.output


def test_vm_ha_rearm_rejects_foreign_owner_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = _lifecycle_state()
    plan = _manual_failback_plan()
    observation = SimpleNamespace(
        allocation=SimpleNamespace(owner=AllocationOwner("foreign", "eth0")),
        former=_manual_failback_compute(
            state=InstanceCloudState.RUNNING, alias_present=True, revision="21"
        ),
        candidate=_manual_failback_compute(
            state=InstanceCloudState.STOPPED, alias_present=False, revision="22"
        ),
    )
    starts: list[tuple[str, str]] = []

    class LifecycleStore:
        def __init__(self, _path: Path) -> None:
            pass

        def read(self, **_kwargs):
            return lifecycle

    cloud = SimpleNamespace(
        get_instance=lambda *_: None,
        stop_instance=lambda *_: None,
        get_allocation=lambda *_: None,
        set_alias_allocation=lambda *_: None,
        start_instance=lambda compute_id, operation_id: starts.append((compute_id, operation_id)),
    )

    class FakeManager:
        def __init__(self, **_kwargs) -> None:
            pass

        def _get_client(self):
            return object()

    monkeypatch.setattr(
        "nebius_vpngw.cli.load_local_config",
        lambda _path: {
            "project_id": "project-test",
            "gateway_group": {"vm_spec": {}},
        },
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr("nebius_vpngw.cli.VMHALifecycleStore", LifecycleStore)
    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", lambda *_, **__: None)
    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", lambda **_: "token")
    monkeypatch.setattr("nebius_vpngw.cli.VMManager", FakeManager)
    monkeypatch.setattr("nebius_vpngw.cli.NebiusSDKCloudClient", lambda _sdk, **_kwargs: cloud)
    monkeypatch.setattr(
        "nebius_vpngw.cli.VMHACloudAdapter",
        lambda **_kwargs: SimpleNamespace(observe_cluster=lambda **_observe_kwargs: observation),
    )

    with pytest.raises(RuntimeError, match="no exact current owner"):
        _prepare_vm_ha_configured_passive_standby(local_config_file=Path("config.yaml"))

    assert starts == []


def test_cli_help_examples_cover_every_public_command() -> None:
    click_app = get_command(app)

    assert tuple(_public_command_paths(click_app)) == EXPECTED_PUBLIC_COMMAND_PATHS
    assert set(_COMMAND_EXAMPLES) == set(EXPECTED_PUBLIC_COMMAND_PATHS)


def test_top_level_help_includes_quick_start_examples() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"], env=HELP_ENV)
    normalized_output = " ".join(result.output.split())

    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "Examples:" in result.output
    assert "COMMAND --help" in result.output
    for example in _ROOT_HELP_EXAMPLES:
        assert " ".join(example.split()) in normalized_output


def test_removed_vm_ha_status_surfaces_are_absent_from_help_and_parser() -> None:
    runner = CliRunner()
    root_help = runner.invoke(app, ["--help"], env=HELP_ENV)
    status_help = runner.invoke(app, ["status", "--help"], env=HELP_ENV)

    assert root_help.exit_code == 0
    assert status_help.exit_code == 0
    assert "vm-ha-recover" not in root_help.output
    assert "vm-ha-status" not in root_help.output
    assert "vm-ha-state" not in root_help.output
    assert "--vm-ha-only" not in status_help.output


@pytest.mark.parametrize("command_path", tuple(_COMMAND_EXAMPLES))
def test_each_cli_command_help_renders_with_its_examples(
    command_path: tuple[str, ...],
) -> None:
    result = CliRunner().invoke(app, [*command_path, "--help"], env=HELP_ENV)
    normalized_output = " ".join(result.output.split())

    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "Examples:" in result.output
    for example in _COMMAND_EXAMPLES[command_path]:
        assert " ".join(example.split()) in normalized_output


@pytest.mark.parametrize("command_path", EXPECTED_PUBLIC_COMMAND_PATHS)
def test_each_cli_command_example_uses_supported_syntax(
    command_path: tuple[str, ...], tmp_path: Path, monkeypatch
) -> None:
    for filename in (
        "nebius-vpngw.config.yaml",
        "gateway.config.yaml",
        "peer-vpn.txt",
    ):
        (tmp_path / filename).touch()
    monkeypatch.chdir(tmp_path)

    command: Command = get_command(app)
    for segment in command_path:
        assert isinstance(command, TyperGroup)
        command = command.commands[segment]
    for example in _COMMAND_EXAMPLES[command_path]:
        words = shlex.split(example)
        assert words[0] == "nebius-vpngw"
        assert tuple(words[1 : 1 + len(command_path)]) == command_path
        with command.make_context(command_path[-1], words[1 + len(command_path) :]):
            pass


def test_apply_help_exposes_the_supported_service_account_option() -> None:
    result = CliRunner().invoke(app, ["apply", "--help"], env=HELP_ENV)

    assert result.exit_code == 0
    assert "--sa" in result.stdout
    assert "Service Account" in result.stdout


def test_route_and_operator_help_mentions_multi_connection_behavior() -> None:
    click_app = get_command(app)

    list_remote_help = click_app.commands["list-routes-remote"].help or ""
    add_routes_cmd = click_app.commands["add-routes-local"]
    add_routes_help = add_routes_cmd.help or ""
    failover_group = click_app.commands["failover"]
    assert isinstance(failover_group, TyperGroup)
    failover_help = failover_group.commands["tunnel"].params[0].help or ""
    restart_help = click_app.commands["restart-tunnel"].params[0].help or ""

    assert "owning gateway VM" in list_remote_help
    assert "selected connection" in list_remote_help

    assert "--swap-route-table" in add_routes_help
    assert "rollback command" in add_routes_help

    option_help_by_name = {param.name: param.help or "" for param in add_routes_cmd.params}
    assert "rollback command" in option_help_by_name["swap_route_table"]
    assert "Skip the confirmation prompt for" in option_help_by_name["yes"]
    assert "--swap-route-table" in option_help_by_name["yes"]

    assert "multi-connection topologies" in failover_help
    assert "only the owning gateway VM" in restart_help


def test_add_routes_local_swap_route_table_requires_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    runner = CliRunner()
    config_path = tmp_path / "swap.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "project_id": "project-test",
        "defaults": {"routing": {"mode": "static"}},
    }
    plan = _static_route_plan()
    auth_calls = {"count": 0}

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda path: local_cfg)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda cfg, peers: plan)

    def fake_authentication(*, required: bool, show_progress: bool) -> str:
        auth_calls["count"] += 1
        return "token"

    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", fake_authentication)

    result = runner.invoke(
        app,
        [
            "add-routes-local",
            "--local-config-file",
            str(config_path),
            "--swap-route-table",
        ],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "--swap-route-table performs a blue/green subnet route-table cutover" in result.stdout
    assert "Proceed with route-table swap? [y/N]:" in result.stdout
    assert "Aborted. No changes made." in result.stdout
    assert auth_calls["count"] == 0


def test_add_routes_local_swap_route_table_passes_mode_and_rollback_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    config_path = tmp_path / "swap.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "project_id": "project-test",
        "defaults": {"routing": {"mode": "static"}},
    }
    plan = _static_route_plan()
    captured: dict[str, object] = {}

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda path: local_cfg)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda cfg, peers: plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli._ensure_authentication",
        lambda *, required, show_progress: "token",
    )

    class FakeRouteManager:
        def __init__(self, project_id, auth_token=None, *, ssh_policy=None):
            captured["project_id"] = project_id
            captured["auth_token"] = auth_token
            captured["ssh_policy"] = ssh_policy

        def add_routes(
            self,
            plan_obj,
            local_cfg_obj,
            *,
            summarize: bool = False,
            swap_route_table: bool = False,
            rollback_dir=None,
        ) -> None:
            captured["plan"] = plan_obj
            captured["local_cfg"] = local_cfg_obj
            captured["summarize"] = summarize
            captured["swap_route_table"] = swap_route_table
            captured["rollback_dir"] = rollback_dir

        def ensure_bgp_advertisements_current(
            self, plan_obj, local_cfg_obj, *, vm_ha_lifecycle_guard=None
        ) -> None:
            captured["ensured_bgp"] = (plan_obj, local_cfg_obj)
            captured["lifecycle_guard"] = vm_ha_lifecycle_guard

    monkeypatch.setattr("nebius_vpngw.cli.RouteManager", FakeRouteManager)

    result = runner.invoke(
        app,
        [
            "add-routes-local",
            "--local-config-file",
            str(config_path),
            "--swap-route-table",
        ],
        input="yes\n",
    )

    assert result.exit_code == 0
    assert captured["project_id"] == "project-test"
    assert captured["auth_token"] == "token"
    assert captured["ssh_policy"] is None
    assert captured["plan"] is plan
    assert captured["local_cfg"] is local_cfg
    assert captured["summarize"] is False
    assert captured["swap_route_table"] is True
    assert captured["rollback_dir"] == config_path.parent / ".nebius-vpngw-rollbacks"
    assert "ensured_bgp" not in captured
    assert "lifecycle_guard" not in captured


def test_build_ssh_base_cmd_enforces_host_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nebius_vpngw.cli import _build_ssh_base_cmd

    monkeypatch.delenv("VPNGW_SSH_KNOWN_HOSTS_FILE", raising=False)
    ssh_cmd = _build_ssh_base_cmd(tmp_path / "id_ed25519")

    assert "LogLevel=ERROR" in ssh_cmd
    assert "StrictHostKeyChecking=yes" in ssh_cmd
    assert "StrictHostKeyChecking=no" not in ssh_cmd
    assert not any(option.startswith("UserKnownHostsFile=") for option in ssh_cmd)


def test_build_ssh_base_cmd_uses_exact_explicit_known_hosts(tmp_path: Path, monkeypatch) -> None:
    from nebius_vpngw.cli import _build_ssh_base_cmd

    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("host ssh-ed25519 AAAAfixture\n", encoding="utf-8")
    monkeypatch.setenv("VPNGW_SSH_KNOWN_HOSTS_FILE", str(known_hosts))

    ssh_cmd = _build_ssh_base_cmd(None)

    assert f"UserKnownHostsFile={known_hosts}" in ssh_cmd
    assert "GlobalKnownHostsFile=none" in ssh_cmd
    assert "KnownHostsCommand=none" in ssh_cmd


def test_build_ssh_base_cmd_rejects_missing_explicit_known_hosts(
    tmp_path: Path, monkeypatch
) -> None:
    from nebius_vpngw.cli import _build_ssh_base_cmd

    monkeypatch.setenv("VPNGW_SSH_KNOWN_HOSTS_FILE", str(tmp_path / "missing"))

    with pytest.raises(ValueError, match="non-empty readable regular file"):
        _build_ssh_base_cmd(None)


def test_status_ssh_context_isolates_one_missing_vm_ha_pin(tmp_path: Path, monkeypatch) -> None:
    members = [
        SimpleNamespace(hostname="nebius-vpn-gw-0"),
        SimpleNamespace(hostname="nebius-vpn-gw-1"),
    ]
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
        gateway_group=SimpleNamespace(name="nebius-vpn-gw", region="eu-west1"),
        iter_instance_configs=lambda: iter(members),
    )
    vm_ips = {
        "nebius-vpn-gw-0": "203.0.113.10",
        "nebius-vpn-gw-1": "203.0.113.11",
    }
    trusted_policy = object()
    observed_pairs: list[tuple[tuple[str, str], ...]] = []

    def require_policy(host_pairs, *, enrollment_hosts, trust_scope):
        pairs = tuple(host_pairs)
        observed_pairs.append(pairs)
        assert enrollment_hosts == set()
        assert trust_scope.cluster_id == "cluster-a"
        if pairs[0][0] == "nebius-vpn-gw-1":
            raise ValueError("missing exact pin")
        return trusted_policy

    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", require_policy)
    built: list[tuple[Path | None, object | None, str | None]] = []

    def build_command(key_path, *, ssh_policy=None, hostname=None):
        built.append((key_path, ssh_policy, hostname))
        return ["ssh"]

    monkeypatch.setattr("nebius_vpngw.cli._build_ssh_base_cmd", build_command)
    key_path = tmp_path / "id_ed25519"
    context = _build_status_ssh_context(
        {
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "region_id": "eu-west1",
            "gateway_group": {
                "vm_spec": {
                    "ssh_username": "operator",
                    "ssh_private_key_path": str(key_path),
                }
            },
        },
        plan,
        vm_ips,
    )

    assert observed_pairs == [
        (("nebius-vpn-gw-0", "203.0.113.10"),),
        (("nebius-vpn-gw-1", "203.0.113.11"),),
    ]
    assert context.unavailable_members == frozenset({"nebius-vpn-gw-1"})
    assert _status_ssh_target_command(
        context,
        hostname="nebius-vpn-gw-0",
        target="203.0.113.10",
    ) == ["ssh", "operator@203.0.113.10"]
    assert built == [(key_path, trusted_policy, "nebius-vpn-gw-0")]
    with pytest.raises(_VMHAStatusSSHUnavailable, match="exact SSH trust"):
        _status_ssh_target_command(
            context,
            hostname="nebius-vpn-gw-1",
            target="203.0.113.11",
        )
    assert _vm_ha_member_failure_condition(_VMHAStatusSSHUnavailable("raw trust detail")) == (
        "unknown",
        "ssh-trust-unavailable",
    )


def test_ssh_policy_honors_an_explicit_empty_environment(tmp_path: Path, monkeypatch) -> None:
    from nebius_vpngw.deploy.ssh_policy import build_openssh_base_command

    monkeypatch.setenv("VPNGW_SSH_KNOWN_HOSTS_FILE", str(tmp_path / "missing"))

    ssh_cmd = build_openssh_base_command(environment={})

    assert not any(option.startswith("UserKnownHostsFile=") for option in ssh_cmd)


def test_restart_tunnel_uses_inline_remote_python_and_shows_real_failure_output(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "restart.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway_group": {
            "instance_count": 1,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "site-a",
                "tunnels": [
                    {
                        "name": "tunnel-1",
                        "gateway_instance_index": 0,
                    }
                ],
            }
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            )
        ],
    )
    recorded_cmd: list[str] = []
    recorded_input = ""

    def fake_run(cmd, capture_output, text, timeout, input=None):
        recorded_cmd[:] = cmd
        nonlocal recorded_input
        recorded_input = input or ""
        return SimpleNamespace(
            returncode=1,
            stdout="[TunnelMonitor] ❌ Failed to restart tunnel tunnel-1",
            stderr="Warning: Permanently added '203.0.113.10' (ED25519) to the list of known hosts.",
        )

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(
            app,
            ["restart-tunnel", "tunnel-1", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 1
    assert recorded_cmd[-1] == "sudo /usr/bin/python3 - --restart-tunnel tunnel-1"
    assert "def _restart_tunnel(tunnel_name: str) -> bool:" in recorded_input
    assert 'print(f"[TunnelMonitor] Failed to restart tunnel {tunnel_name}")' in recorded_input
    assert "\nConnecting to nebius-vpn-gw-0 (203.0.113.10)..." in result.stdout
    assert "\\nConnecting to nebius-vpn-gw-0" not in result.stdout
    assert "[TunnelMonitor] ❌ Failed to restart tunnel tunnel-1" in result.stdout


def test_restart_tunnel_full_reset_also_bounces_matching_bgp_neighbor(tmp_path: Path) -> None:
    config_path = tmp_path / "restart.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway": {"local_asn": 65010},
        "defaults": {"routing": {"mode": RoutingMode.BGP}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 1,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "gcp-ha-vpn",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "tunnel-1",
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    }
                ],
            }
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            )
        ],
    )
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, input=None):
        recorded_cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
        patch("nebius_vpngw.cli.time.sleep", return_value=None),
    ):
        result = CliRunner().invoke(
            app,
            ["restart-tunnel", "tunnel-1", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 0
    assert recorded_cmds[0][-1] == "sudo /usr/bin/python3 - --restart-tunnel tunnel-1"
    assert recorded_cmds[1][-1] == (
        "sudo vtysh -c 'configure terminal' -c 'router bgp 65010' "
        "-c 'neighbor 169.254.10.2 shutdown'"
    )
    assert recorded_cmds[2][-1] == (
        "sudo vtysh -c 'configure terminal' -c 'router bgp 65010' "
        "-c 'no neighbor 169.254.10.2 shutdown'"
    )
    assert "Resetting matching BGP neighbor(s)" in result.stdout
    assert "Successfully reset tunnel 'tunnel-1'" in result.stdout


def test_restart_tunnel_targets_only_owning_gateway_instance(tmp_path: Path) -> None:
    config_path = tmp_path / "restart-multivm.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway": {"local_asn": 65010},
        "defaults": {"routing": {"mode": RoutingMode.BGP}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 2,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "site-a",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-a-active",
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    }
                ],
            },
            {
                "name": "site-b",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-b-active",
                        "gateway_instance_index": 1,
                        "inner_remote_ip": "169.254.20.2",
                    }
                ],
            },
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=2,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            ),
            InstanceResolvedConfig(
                instance_index=1,
                hostname="nebius-vpn-gw-1",
                external_ip="203.0.113.20",
                config_yaml="gateway: {}\n",
            ),
        ],
    )
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, input=None):
        recorded_cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
        patch("nebius_vpngw.cli.time.sleep", return_value=None),
    ):
        result = CliRunner().invoke(
            app,
            ["restart-tunnel", "site-b-active", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 0
    assert all("203.0.113.20" in cmd[-2] for cmd in recorded_cmds)
    assert all("203.0.113.10" not in part for cmd in recorded_cmds for part in cmd)
    assert "Connecting to nebius-vpn-gw-1 (203.0.113.20)" in result.stdout
    assert "Connecting to nebius-vpn-gw-0" not in result.stdout


def test_restart_tunnel_fails_fast_when_tunnel_name_is_unknown(tmp_path: Path) -> None:
    config_path = tmp_path / "restart-unknown.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 1,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "site-a",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-a-active",
                        "gateway_instance_index": 0,
                    }
                ],
            }
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            )
        ],
    )

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run") as run_mock,
    ):
        result = CliRunner().invoke(
            app,
            ["restart-tunnel", "missing-tunnel", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 1
    assert "Tunnel 'missing-tunnel' not found." in result.stdout
    run_mock.assert_not_called()


def test_failover_accepts_enum_routing_modes(tmp_path: Path) -> None:
    config_path = tmp_path / "failover.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway": {"local_asn": 65010},
        "defaults": {"routing": {"mode": RoutingMode.BGP}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 1,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "gcp-ha-vpn",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "tunnel-active",
                        "ha_role": HARole.ACTIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                        "inner_local_ip": "169.254.10.1",
                    },
                    {
                        "name": "tunnel-passive",
                        "ha_role": HARole.PASSIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.11.2",
                        "inner_local_ip": "169.254.11.1",
                    },
                ],
            }
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            )
        ],
    )
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout):
        recorded_cmds.append(cmd)
        if "show bgp ipv4 unicast summary json" in cmd[-1]:
            return SimpleNamespace(
                returncode=0,
                stdout='{"ipv4Unicast":{"peers":{"169.254.10.2":{"state":"Idle"},"169.254.11.2":{"state":"Established"}}}}',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(
            app,
            ["failover", "tunnel", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 0
    assert recorded_cmds[0][-1] == (
        "sudo vtysh -c 'configure terminal' -c 'router bgp 65010' "
        "-c 'neighbor 169.254.10.2 shutdown'"
    )
    assert "Failover confirmed" in result.stdout


def test_failover_targets_selected_passive_tunnel_instance_in_multivm_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "failover-multivm.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway": {"local_asn": 65010},
        "defaults": {"routing": {"mode": RoutingMode.BGP}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 2,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "site-a",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-a-active",
                        "ha_role": HARole.ACTIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    },
                    {
                        "name": "site-a-passive",
                        "ha_role": HARole.PASSIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.11.2",
                    },
                ],
            },
            {
                "name": "site-b",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-b-active",
                        "ha_role": HARole.ACTIVE,
                        "gateway_instance_index": 1,
                        "inner_remote_ip": "169.254.20.2",
                    },
                    {
                        "name": "site-b-passive",
                        "ha_role": HARole.PASSIVE,
                        "gateway_instance_index": 1,
                        "inner_remote_ip": "169.254.21.2",
                    },
                ],
            },
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=2,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            ),
            InstanceResolvedConfig(
                instance_index=1,
                hostname="nebius-vpn-gw-1",
                external_ip="203.0.113.20",
                config_yaml="gateway: {}\n",
            ),
        ],
    )
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout):
        recorded_cmds.append(cmd)
        if "show bgp ipv4 unicast summary json" in cmd[-1]:
            return SimpleNamespace(
                returncode=0,
                stdout='{"ipv4Unicast":{"peers":{"169.254.20.2":{"state":"Idle"},"169.254.21.2":{"state":"Established"}}}}',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(
            app,
            [
                "failover",
                "tunnel",
                "site-b-passive",
                "--local-config-file",
                str(config_path),
            ],
        )

    assert result.exit_code == 0
    assert "Failing over connection 'site-b'" in result.stdout
    assert "site-b-active" in result.stdout
    assert "site-b-passive" in result.stdout
    assert all("203.0.113.20" in cmd[-2] for cmd in recorded_cmds)
    assert recorded_cmds[0][-1] == (
        "sudo vtysh -c 'configure terminal' -c 'router bgp 65010' "
        "-c 'neighbor 169.254.20.2 shutdown'"
    )


def test_failback_accepts_enum_routing_modes(tmp_path: Path) -> None:
    config_path = tmp_path / "failback.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway": {"local_asn": 65010},
        "defaults": {"routing": {"mode": RoutingMode.BGP}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 1,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "gcp-ha-vpn",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "tunnel-active",
                        "ha_role": HARole.ACTIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    },
                    {
                        "name": "tunnel-passive",
                        "ha_role": HARole.PASSIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.11.2",
                    },
                ],
            }
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            )
        ],
    )
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout):
        recorded_cmds.append(cmd)
        if "show bgp ipv4 unicast summary json" in cmd[-1]:
            return SimpleNamespace(
                returncode=0,
                stdout='{"ipv4Unicast":{"peers":{"169.254.10.2":{"state":"Established"},"169.254.11.2":{"state":"Idle"}}}}',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(
            app,
            ["failback", "tunnel", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 0
    assert recorded_cmds[0][-1] == (
        "sudo vtysh -c 'configure terminal' -c 'router bgp 65010' "
        "-c 'no neighbor 169.254.10.2 shutdown'"
    )
    assert "Failback confirmed" in result.stdout


def test_failback_targets_selected_active_tunnel_instance_in_multivm_config(tmp_path: Path) -> None:
    config_path = tmp_path / "failback-multivm.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway": {"local_asn": 65010},
        "defaults": {"routing": {"mode": RoutingMode.BGP}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 2,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "site-a",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-a-active",
                        "ha_role": HARole.ACTIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    },
                    {
                        "name": "site-a-passive",
                        "ha_role": HARole.PASSIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.11.2",
                    },
                ],
            },
            {
                "name": "site-b",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-b-active",
                        "ha_role": HARole.ACTIVE,
                        "gateway_instance_index": 1,
                        "inner_remote_ip": "169.254.20.2",
                    },
                    {
                        "name": "site-b-passive",
                        "ha_role": HARole.PASSIVE,
                        "gateway_instance_index": 1,
                        "inner_remote_ip": "169.254.21.2",
                    },
                ],
            },
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=2,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            ),
            InstanceResolvedConfig(
                instance_index=1,
                hostname="nebius-vpn-gw-1",
                external_ip="203.0.113.20",
                config_yaml="gateway: {}\n",
            ),
        ],
    )
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout):
        recorded_cmds.append(cmd)
        if "show bgp ipv4 unicast summary json" in cmd[-1]:
            return SimpleNamespace(
                returncode=0,
                stdout='{"ipv4Unicast":{"peers":{"169.254.20.2":{"state":"Established"},"169.254.21.2":{"state":"Idle"}}}}',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(
            app,
            [
                "failback",
                "tunnel",
                "site-b-active",
                "--local-config-file",
                str(config_path),
            ],
        )

    assert result.exit_code == 0
    assert "restore site-b-active" in result.stdout
    assert all("203.0.113.20" in cmd[-2] for cmd in recorded_cmds)
    assert recorded_cmds[0][-1] == (
        "sudo vtysh -c 'configure terminal' -c 'router bgp 65010' "
        "-c 'no neighbor 169.254.20.2 shutdown'"
    )
