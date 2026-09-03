from __future__ import annotations

import io
import json
import logging
import os
import shlex
import subprocess
import typing as t
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import typer
import yaml
from click import unstyle
from click.core import Command, Context
from grpc import StatusCode
from nebius.aio.service_error import RequestError
from rich import print as rich_print
from rich.console import Console
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from nebius_vpngw import vpngw_sa
from nebius_vpngw.agent.vm_ha.progress import planned_request_fingerprint
from nebius_vpngw.cli import (
    _COMMAND_EXAMPLES,
    _NEBIUS_REGION_HELP,
    _ROOT_HELP_EXAMPLES,
    _active_vm_ha_lifecycle_state,
    _add_configured_no_active_tunnel_rows,
    _apply_impl,
    _apply_operator_auth_token,
    _build_status_ssh_context,
    _canonical_digest,
    _configured_gateway_vms_exist,
    _detect_connection_role_overrides,
    _detect_cross_connection_ecmp_warnings,
    _ensure_gateway_vms_exist,
    _existing_gateway_ssh_policy,
    _external_ips_assigned,
    _fetch_vm_ha_agent_status,
    _format_ecmp_warning_lines,
    _format_role_override_lines,
    _gateway_tunnel_uptime,
    _GatewayVMDiscoveryError,
    _ipsec_status_reports_no_active_tunnels,
    _list_status_routes,
    _mark_service_probe_recovered,
    _missing_standby_apply_approval_lines,
    _own_vm_manager,
    _parse_bgp_uptime,
    _plan_vm_ha_apply_convergence,
    _prepare_vm_ha_configured_passive_standby,
    _prepare_vm_ha_manual_failback_target,
    _prepare_vm_ha_planned_target,
    _read_vm_ha_planned_terminal_agent,
    _read_vm_ha_planned_terminal_cloud,
    _refresh_vm_ha_ssh_policy_after_compute,
    _registered_command_name,
    _render_vm_ha_status,
    _requested_apply_service_account_token,
    _require_vm_ha_manual_failover_target,
    _run_vm_ha_operator_command,
    _run_vm_ha_planned_transfer,
    _safe_vm_ha_reason,
    _select_carrying_tunnel_for_connection,
    _select_existing_public_allocations,
    _serialize_explicit_vm_ha_apply,
    _should_prompt_add_routes_after_apply,
    _status_ssh_target_command,
    _tunnel_probe_retry_has_established_sa,
    _update_external_ips_in_yaml,
    _validate_vm_ha_agent_status,
    _validate_vm_ha_display_status,
    _validate_vm_ha_lifecycle_credential_transition,
    _validate_vm_ha_peer_rotation_preparation,
    _validate_vm_ha_planned_status,
    _vm_ha_activation_blockers,
    _vm_ha_activation_recovery_approval_state,
    _vm_ha_apply_order,
    _vm_ha_apply_order_for_owner,
    _vm_ha_bound_owner_node_id,
    _vm_ha_cloud_authority,
    _vm_ha_desired_approval_state,
    _vm_ha_error_chain_has_sdk_code,
    _vm_ha_failed_passive_replacement_plan,
    _vm_ha_initial_resource_bindings,
    _vm_ha_local_config_command,
    _vm_ha_member_failure_condition,
    _vm_ha_migration_plan_digest,
    _vm_ha_missing_standby_owner_refresh_required,
    _vm_ha_missing_standby_replacement_plan,
    _vm_ha_observation_matches_bindings,
    _vm_ha_ordinary_migration_ssh_hosts,
    _vm_ha_ordinary_migration_ssh_import_hosts,
    _vm_ha_planned_progress_observation,
    _vm_ha_planned_reproof_converging,
    _vm_ha_planned_terminal_runtime_binding,
    _vm_ha_ssh_trust_scope,
    _vm_ha_status_runtime_binding,
    _vm_ha_status_view,
    _VMHAAgentStatusPermanent,
    _VMHAAgentStatusStale,
    _VMHAApplyPlanningFailed,
    _VMHACloudAuthority,
    _VMHAMemberEvidence,
    _VMHAMissingStandbyReplacementPlan,
    _VMHAPlannedCutoverVerificationIncomplete,
    _VMHAPlannedCutoverVerificationUnavailable,
    _VMHAPlannedRedundancyRestorationError,
    _VMHAPlannedRestorationVerificationUnavailable,
    _VMHAPlannedTerminalContext,
    _VMHAPlannedTerminalObservationUnavailable,
    _VMHARemoteAgentUnavailable,
    _VMHAStatusSSHUnavailable,
    _vpn_gateway_status_table,
    _wait_for_vm_ha_agent_status,
    _wait_for_vm_ha_planned_transfer,
    _with_vm_manager_lifetimes,
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
from nebius_vpngw.deploy.ssh_policy import (
    LegacyOrdinarySSHEnrollmentRequired,
    VMHAReplacementSSHIdentityProblem,
    VMHAReplacementSSHIdentityUnavailable,
    VMHASSHIdentityRotationIntent,
    VMHASSHTrustScope,
)
from nebius_vpngw.deploy.vm_diff import ChangeType, VMDiff
from nebius_vpngw.deploy.vm_ha_cloud import (
    AllocationOwner,
    AmbiguousHACloudError,
    InstanceCloudState,
    PermanentHACloudError,
    RetryableHACloudError,
)
from nebius_vpngw.deploy.vm_ha_identity import FormerVMHAProvenance
from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
    VMHAMigrationTransaction,
    vm_ha_missing_standby_disk_name,
    vm_ha_missing_standby_replacement_effect,
    vm_ha_passive_replacement_binding_key,
)
from nebius_vpngw.deploy.vm_manager import PublicAllocationCandidate
from nebius_vpngw.nebius_auth import NebiusCLIAuthenticationError
from nebius_vpngw.schema import HARole, RoutingMode, VMHARouteTarget
from nebius_vpngw.vm_ha_credentials import VMHACredentialIdentityError


class _ContextManagedFake:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        return None

    def prepare_ordinary_ssh_policy(self, *args, **kwargs):
        """Ordinary apply fakes opt out unless a test exercises the trust contract."""

        return None

    def vm_ha_ssh_trust_bindings(self, _spec, *, retained_hosts, **_kwargs):
        """Model the post-Compute exact-member trust interface by default."""

        return {hostname: (lambda: None) for hostname in retained_hosts}

    def ordinary_migration_ssh_imports(self, _spec, *, hostnames, **_kwargs):
        return {hostname: object() for hostname in hostnames}

    def set_ssh_policy(self, policy) -> None:
        self.ssh_policy = policy


class _ContextManagedNamespace(_ContextManagedFake, SimpleNamespace):
    pass


def test_nested_vm_manager_lifetime_remains_open_for_outer_command() -> None:
    events: list[str] = []

    class Manager:
        open = False

        def __enter__(self):
            self.open = True
            events.append("enter")
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
            self.open = False
            events.append("exit")

    @_with_vm_manager_lifetimes
    def prepare() -> t.Callable[[], bool]:
        manager = _own_vm_manager(Manager())
        return lambda: manager.open

    @_with_vm_manager_lifetimes
    def run() -> bool:
        reader = prepare()
        events.append("observe")
        return reader()

    assert run() is True
    assert events == ["enter", "observe", "exit"]
    assert hasattr(_run_vm_ha_planned_transfer, "__wrapped__")


HELP_ENV = {"COLUMNS": "120", "LINES": "200"}
EXPECTED_ROOT_COMMANDS = (
    "create-config",
    "vm-ha",
    "prep-network",
    "validate-config",
    "apply",
    "status",
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
    ("vm-ha",),
    ("prep-network",),
    ("validate-config",),
    ("apply",),
    ("status",),
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


@pytest.fixture(autouse=True)
def _isolate_vm_ha_apply_identity_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unrelated CLI orchestration tests below the online identity boundary."""

    credentials = SimpleNamespace(
        service_account_id="service-account-test",
        project_id="project-test",
        resource_bindings=lambda: {},
        approval_records=lambda: [],
        by_node=lambda: {},
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli.inspect_managed_vm_ha_credentials",
        lambda **_kwargs: SimpleNamespace(
            action="reuse",
            credentials=credentials,
            source_path=Path("/private/managed-vm-ha-credentials.json"),
            approval_record=lambda: {"action": "reuse"},
        ),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli.ensure_managed_vm_ha_credentials",
        lambda managed_plan, **_kwargs: SimpleNamespace(
            credentials=managed_plan.credentials,
            token_identity=SimpleNamespace(token="managed-token"),
        ),
    )
    artifact = SimpleNamespace(sha256="f" * 64)
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_agent_artifact",
        lambda _ssh_policy: artifact,
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


def _runtime_credential_bindings(
    *, service_account_id: str = "service-account-test", authorized_key_id: str = "key-a"
) -> dict[str, str]:
    return {
        "credential-authorized-key:node-active": authorized_key_id,
        "credential-authorized-key:node-passive": authorized_key_id,
        "credential-service-account:node-active": service_account_id,
        "credential-service-account:node-passive": service_account_id,
        "credential-sha256:node-active": "d" * 64,
        "credential-sha256:node-passive": "d" * 64,
    }


def _runtime_credentials(
    *, service_account_id: str = "service-account-test", authorized_key_id: str = "key-a"
) -> SimpleNamespace:
    bindings = _runtime_credential_bindings(
        service_account_id=service_account_id,
        authorized_key_id=authorized_key_id,
    )
    return SimpleNamespace(
        service_account_id=service_account_id,
        resource_bindings=lambda: bindings,
    )


def _lifecycle_with_credential_bindings(
    *,
    status: VMHALifecycleStatus,
    bindings: dict[str, str],
) -> VMHALifecycleState:
    state = _lifecycle_state(status=status)
    assert state.transaction is not None
    transaction = replace(
        state.transaction,
        resource_bindings=tuple(
            sorted(
                {
                    **dict(state.transaction.resource_bindings),
                    **bindings,
                }.items()
            )
        ),
    )
    return replace(state, transaction=transaction)


def test_active_vm_ha_lifecycle_rejects_missing_credential_bindings() -> None:
    with pytest.raises(ValueError, match="binding is incomplete"):
        _validate_vm_ha_lifecycle_credential_transition(
            _lifecycle_state(),
            _runtime_credentials(),
        )


def test_active_vm_ha_lifecycle_rejects_authorized_key_rotation() -> None:
    state = _lifecycle_with_credential_bindings(
        status=VMHALifecycleStatus.ACTIVE,
        bindings=_runtime_credential_bindings(authorized_key_id="key-old"),
    )

    with pytest.raises(ValueError, match="identity changed"):
        _validate_vm_ha_lifecycle_credential_transition(
            state,
            _runtime_credentials(authorized_key_id="key-new"),
        )


def test_active_vm_ha_lifecycle_rejects_service_account_rebinding() -> None:
    state = _lifecycle_with_credential_bindings(
        status=VMHALifecycleStatus.ACTIVE,
        bindings=_runtime_credential_bindings(service_account_id="service-account-old"),
    )
    credentials = _runtime_credentials(service_account_id="service-account-new")

    with pytest.raises(ValueError, match="identity changed"):
        _validate_vm_ha_lifecycle_credential_transition(
            state,
            credentials,
        )


@pytest.mark.parametrize(
    "status",
    (VMHALifecycleStatus.PROVISIONING, VMHALifecycleStatus.ACTIVATING),
)
def test_pending_vm_ha_lifecycle_requires_exact_credential_identity(
    status: VMHALifecycleStatus,
) -> None:
    credentials = _runtime_credentials()

    with pytest.raises(ValueError, match="binding is incomplete"):
        _validate_vm_ha_lifecycle_credential_transition(
            _lifecycle_state(status=status),
            credentials,
        )

    state = _lifecycle_with_credential_bindings(
        status=status,
        bindings=_runtime_credential_bindings(),
    )
    _validate_vm_ha_lifecycle_credential_transition(
        state,
        credentials,
    )


def test_vm_ha_lifecycle_rejects_partial_credential_binding() -> None:
    state = _lifecycle_with_credential_bindings(
        status=VMHALifecycleStatus.ACTIVE,
        bindings={"credential-service-account:node-active": "service-account-test"},
    )

    with pytest.raises(ValueError, match="binding is incomplete"):
        _validate_vm_ha_lifecycle_credential_transition(
            state,
            _runtime_credentials(),
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
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_update_external_ips_targets_only_gateway_group_and_replaces_atomically(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "targeted.config.yaml"
    original = """external_ips:
  - [\"198.51.100.5\"]
gateway_group:
  name: nebius-vpn-gw
  instance_count: 1
  external_ips: []
  subnet:
    name: vpngw-subnet
"""
    config_path.write_text(original, encoding="utf-8")

    _update_external_ips_in_yaml(config_path, [["203.0.113.10"]])

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["external_ips"] == [["198.51.100.5"]]
    assert payload["gateway_group"]["external_ips"] == [["203.0.113.10"]]

    preserved = config_path.read_text(encoding="utf-8")
    with (
        patch("nebius_vpngw.cli.os.replace", side_effect=OSError("replace failed")),
        pytest.raises(OSError, match="replace failed"),
    ):
        _update_external_ips_in_yaml(config_path, [["203.0.113.20"]])
    assert config_path.read_text(encoding="utf-8") == preserved
    assert list(tmp_path.iterdir()) == [config_path]


def test_update_external_ips_replaces_blank_and_comment_separated_sequence(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "formatted.config.yaml"
    config_path.write_text(
        """gateway_group:
  name: nebius-vpn-gw
  instance_count: 2
  external_ips:
    - [\"198.51.100.5\"]

    # The second VM was intentionally left empty.
    - []
  subnet:
    name: vpngw-subnet
""",
        encoding="utf-8",
    )

    _update_external_ips_in_yaml(
        config_path,
        [["203.0.113.10"], ["203.0.113.20"]],
    )

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["gateway_group"]["external_ips"] == [
        ["203.0.113.10"],
        ["203.0.113.20"],
    ]


def test_external_ips_assigned_ignores_placeholders() -> None:
    assert not _external_ips_assigned([["${VPNGW_IP}"], []])
    assert _external_ips_assigned([["203.0.113.10"], []])


def test_existing_public_ip_selector_defaults_to_distinct_ha_assignments() -> None:
    console = Console(record=True, width=140)
    candidates = [
        PublicAllocationCandidate(
            allocation_id="allocation-1",
            name="reserved-a",
            address="203.0.113.10",
            resource_version=1,
        ),
        PublicAllocationCandidate(
            allocation_id="allocation-2",
            name="reserved-b",
            address="203.0.113.20",
            resource_version=2,
        ),
    ]

    with (
        patch("nebius_vpngw.cli.typer.prompt", side_effect=["existing", "2"]) as prompt,
        patch("nebius_vpngw.cli.typer.confirm", return_value=True) as confirm,
    ):
        matrix, selected = _select_existing_public_allocations(
            console,
            gateway_group={"vm_ha": {"enabled": True}},
            desired_matrix=[[], []],
            candidates=candidates,
        )

    assert matrix == [["203.0.113.20"], ["203.0.113.10"]]
    assert selected[(0, 0)].allocation_id == "allocation-2"
    assert selected[(1, 0)].allocation_id == "allocation-1"
    assert "initial active" in prompt.call_args_list[1].args[0]
    assert "initial passive" in confirm.call_args.args[0]


def test_existing_public_ip_selector_can_leave_missing_slots_on_auto() -> None:
    candidate = PublicAllocationCandidate(
        allocation_id="allocation-1",
        name="reserved-a",
        address="203.0.113.10",
        resource_version=1,
    )

    with patch("nebius_vpngw.cli.typer.prompt", return_value="auto"):
        matrix, selected = _select_existing_public_allocations(
            Console(record=True),
            gateway_group={},
            desired_matrix=[[]],
            candidates=[candidate],
        )

    assert matrix == [[]]
    assert selected == {}


def test_external_ip_yaml_update_rejects_symlink_and_fingerprint_race(tmp_path: Path) -> None:
    target = tmp_path / "target.config.yaml"
    target.write_text("gateway_group:\n  external_ips: []\n", encoding="utf-8")
    link = tmp_path / "link.config.yaml"
    link.symlink_to(target)

    with pytest.raises(OSError):
        _update_external_ips_in_yaml(link, [["203.0.113.10"]])
    assert target.read_text(encoding="utf-8") == "gateway_group:\n  external_ips: []\n"

    from nebius_vpngw import cli as cli_module

    source_text = target.read_text(encoding="utf-8")
    fingerprint = cli_module._file_fingerprint(target)
    target.write_text('gateway_group:\n  external_ips: [["198.51.100.5"]]\n', encoding="utf-8")
    with pytest.raises(OSError, match="changed"):
        _update_external_ips_in_yaml(
            target,
            [["203.0.113.10"]],
            expected_fingerprint=fingerprint,
            source_text=source_text,
        )
    assert "198.51.100.5" in target.read_text(encoding="utf-8")


def test_prep_network_help_and_interactive_flags() -> None:
    help_result = CliRunner().invoke(app, ["prep-network", "--help"])
    help_output = unstyle(help_result.output)
    assert help_result.exit_code == 0
    assert "--interactive" in help_output
    assert "--no-interactive" in help_output

    conflict = CliRunner().invoke(
        app,
        ["prep-network", "--interactive", "--no-interactive"],
    )
    conflict_output = unstyle(conflict.output)
    assert conflict.exit_code == 2
    assert "cannot be used together" in conflict_output


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


def _gateway_discovery_error(code: StatusCode) -> RequestError:
    return RequestError(  # type: ignore[arg-type]
        SimpleNamespace(code=code)
    )


def _gateway_discovery_plan(
    *instance_names: str,
    vm_ha: object | None = None,
) -> SimpleNamespace:
    instances = tuple(SimpleNamespace(hostname=name, external_ip=None) for name in instance_names)
    return SimpleNamespace(
        gateway_group=SimpleNamespace(name="nebius-vpn-gw", region="eu-test1"),
        vm_ha=vm_ha,
        iter_instance_configs=lambda: instances,
    )


def _configured_gateway_response(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(
            id=f"compute-{name}",
            name=name,
            parent_id="project-test",
        )
    )


def test_status_route_inventory_reads_every_page_before_rendering() -> None:
    class Request:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    requests: list[Request] = []

    def list_page(request: Request) -> SimpleNamespace:
        requests.append(request)
        route = SimpleNamespace(metadata=SimpleNamespace(id=f"route-{len(requests)}"))
        return SimpleNamespace(
            items=[route],
            next_page_token="next" if not request.page_token else "",
        )

    routes = _list_status_routes(
        SimpleNamespace(list=list_page),
        Request,
        route_table_id="route-table-1",
    )

    assert [route.metadata.id for route in routes] == ["route-1", "route-2"]
    assert [request.page_token for request in requests] == ["", "next"]
    assert all(request.parent_id == "route-table-1" for request in requests)
    assert all(request.page_size == 1000 for request in requests)


def test_status_route_inventory_fails_as_unavailable_after_an_earlier_page() -> None:
    class Request:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.page_token = page_token

    def list_page(request: Request) -> SimpleNamespace:
        if not request.page_token:
            return SimpleNamespace(items=[object()], next_page_token="next")
        raise RuntimeError("provider detail")

    with pytest.raises(RuntimeError, match="^Status route inventory is unavailable$"):
        _list_status_routes(
            SimpleNamespace(list=list_page),
            Request,
            route_table_id="route-table-1",
        )


def test_configured_gateway_vms_exist_uses_exact_names_until_one_exists() -> None:
    service = Mock()
    service.get_by_name.side_effect = [
        SimpleNamespace(wait=Mock(side_effect=_gateway_discovery_error(StatusCode.NOT_FOUND))),
        SimpleNamespace(wait=Mock(return_value=_configured_gateway_response("nebius-vpn-gw-1"))),
    ]

    with patch(
        "nebius.api.nebius.compute.v1.InstanceServiceClient",
        return_value=service,
    ):
        assert _configured_gateway_vms_exist(
            object(),
            project_id="project-test",
            instance_names=("nebius-vpn-gw-0", "nebius-vpn-gw-1"),
        )

    assert [
        (call.args[0].parent_id, call.args[0].name) for call in service.get_by_name.call_args_list
    ] == [
        ("project-test", "nebius-vpn-gw-0"),
        ("project-test", "nebius-vpn-gw-1"),
    ]
    service.list.assert_not_called()


def test_configured_gateway_vms_exist_returns_false_only_when_all_are_absent() -> None:
    service = Mock()
    service.get_by_name.side_effect = [
        SimpleNamespace(wait=Mock(side_effect=_gateway_discovery_error(StatusCode.NOT_FOUND))),
        SimpleNamespace(wait=Mock(side_effect=_gateway_discovery_error(StatusCode.NOT_FOUND))),
    ]

    with patch(
        "nebius.api.nebius.compute.v1.InstanceServiceClient",
        return_value=service,
    ):
        assert not _configured_gateway_vms_exist(
            object(),
            project_id="project-test",
            instance_names=("nebius-vpn-gw-0", "nebius-vpn-gw-1"),
        )


def test_configured_gateway_vms_exist_accepts_an_immediate_exact_response() -> None:
    service = Mock()
    service.get_by_name.return_value = _configured_gateway_response("nebius-vpn-gw-0")

    with patch(
        "nebius.api.nebius.compute.v1.InstanceServiceClient",
        return_value=service,
    ):
        assert _configured_gateway_vms_exist(
            object(),
            project_id="project-test",
            instance_names=("nebius-vpn-gw-0",),
        )


@pytest.mark.parametrize(
    "provider_error",
    (
        _gateway_discovery_error(StatusCode.UNAUTHENTICATED),
        RuntimeError("NOT_FOUND from an untyped provider boundary"),
    ),
)
def test_configured_gateway_vms_exist_fails_closed_on_query_errors(
    provider_error: BaseException,
) -> None:
    service = Mock()
    service.get_by_name.return_value = SimpleNamespace(wait=Mock(side_effect=provider_error))

    with (
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=service,
        ),
        pytest.raises(_GatewayVMDiscoveryError) as raised,
    ):
        _configured_gateway_vms_exist(
            object(),
            project_id="project-test",
            instance_names=("nebius-vpn-gw-0",),
        )

    assert str(raised.value) == "Unable to query configured gateway VMs."
    assert raised.value.__cause__ is provider_error
    assert "untyped provider boundary" not in str(raised.value)


@pytest.mark.parametrize(
    "response",
    (
        None,
        SimpleNamespace(),
        SimpleNamespace(metadata=SimpleNamespace(name="")),
        SimpleNamespace(metadata=SimpleNamespace(name="stale-prefix-match-0")),
    ),
)
def test_configured_gateway_vms_exist_rejects_missing_or_inexact_identity(
    response: object,
) -> None:
    service = Mock()
    service.get_by_name.return_value = response

    with (
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=service,
        ),
        pytest.raises(
            _GatewayVMDiscoveryError,
            match=r"^Unable to query configured gateway VMs\.$",
        ),
    ):
        _configured_gateway_vms_exist(
            object(),
            project_id="project-test",
            instance_names=("nebius-vpn-gw-0",),
        )


def test_ensure_gateway_vms_exist_accepts_one_exact_configured_vm() -> None:
    service = Mock()
    service.get_by_name.return_value = _configured_gateway_response("nebius-vpn-gw-0")
    manager = _ContextManagedNamespace(_get_client=lambda: object())

    with (
        patch("nebius_vpngw.cli.VMManager", return_value=manager),
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=service,
        ),
    ):
        _ensure_gateway_vms_exist(
            _static_route_plan(),
            project_id="project-test",
            region="eu-test1",
            auth_token=None,
            tenant_id=None,
            action="list local routes",
        )


def test_ensure_gateway_vms_exist_preserves_absent_exit_contract(capsys) -> None:
    service = Mock()
    service.get_by_name.return_value = SimpleNamespace(
        wait=Mock(side_effect=_gateway_discovery_error(StatusCode.NOT_FOUND))
    )
    manager = _ContextManagedNamespace(_get_client=lambda: object())

    with (
        patch("nebius_vpngw.cli.VMManager", return_value=manager),
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=service,
        ),
        pytest.raises(typer.Exit) as raised,
    ):
        _ensure_gateway_vms_exist(
            _static_route_plan(),
            project_id="project-test",
            region="eu-test1",
            auth_token=None,
            tenant_id=None,
            action="list local routes",
        )

    output = capsys.readouterr().out
    assert raised.value.exit_code == 1
    assert "No configured gateway VMs found." in output
    assert "nebius-vpngw apply" in output


def test_ensure_gateway_vms_exist_sanitizes_query_failure(capsys) -> None:
    provider_error = RuntimeError("PRIVATE_PROVIDER_DETAIL")
    service = Mock()
    service.get_by_name.return_value = SimpleNamespace(wait=Mock(side_effect=provider_error))
    manager = _ContextManagedNamespace(_get_client=lambda: object())

    with (
        patch("nebius_vpngw.cli.VMManager", return_value=manager),
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=service,
        ),
        pytest.raises(typer.Exit) as raised,
    ):
        _ensure_gateway_vms_exist(
            _static_route_plan(),
            project_id="project-test",
            region="eu-test1",
            auth_token=None,
            tenant_id=None,
            action="list local routes",
        )

    output = capsys.readouterr().out
    assert raised.value.exit_code == 1
    assert "Unable to query configured gateway VMs." in output
    assert "PRIVATE_PROVIDER_DETAIL" not in output


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


def test_missing_standby_direct_apply_points_to_vm_ha_without_claiming_a_digest(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config dir" / "gateway's config.yaml"

    refusal, next_action = _missing_standby_apply_approval_lines(config_path)

    assert refusal == "Missing standby replacement must be approved through vm-ha."
    assert "digest" not in refusal
    assert next_action == (
        "Next: run nebius-vpngw vm-ha --local-config-file "
        f"{shlex.quote(str(config_path))} to create the missing non-owner VM."
    )


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
    manager_events: list[str] = []

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            manager_events.append("construct")

        def __enter__(self):
            manager_events.append("enter")
            return self

        def __exit__(self, *_args) -> None:
            manager_events.append("exit")

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
    assert manager_events == [
        "construct",
        "enter",
        "construct",
        "enter",
        "exit",
        "exit",
    ]


def test_ordinary_apply_publishes_prepinned_trust_before_compute_mutation(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ordinary.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {
            "vm_spec": {
                "ssh_username": "operator",
                "ssh_private_key_path": str(tmp_path / "id_ed25519"),
                "ssh_public_key": "ssh-ed25519 fixture",
            }
        },
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
    policy = SimpleNamespace(managed_action="create")
    trace: list[str] = []

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            self.policy = kwargs.get("ssh_policy")
            trace.append("construct-runtime" if self.policy is policy else "construct-discovery")

        def prepare_ordinary_ssh_policy(self, spec, instances, **kwargs):
            assert kwargs["trust_scope"].cluster_id == "ordinary-v1"
            assert kwargs["management_public_key"] == "ssh-ed25519 fixture"
            assert kwargs["username"] == "operator"
            trace.append("preflight")
            return policy

        def check_changes(self, spec):
            trace.append("check-changes")
            return changes

        def ensure_group(self, spec, recreate=False, local_prefixes=None):
            assert self.policy is policy
            trace.append("ensure-group")
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
        patch(
            "nebius_vpngw.cli.publish_vm_ha_ssh_trust",
            side_effect=lambda selected: trace.append(
                "publish" if selected is policy else "publish-wrong-policy"
            ),
        ),
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 0, result.stdout
    assert trace.index("preflight") < trace.index("check-changes")
    assert trace.index("check-changes") < trace.index("publish")
    assert trace.index("publish") < trace.index("construct-runtime")
    assert trace.index("construct-runtime") < trace.index("ensure-group")


def test_ordinary_dry_run_runs_trust_preflight_without_effects(tmp_path: Path) -> None:
    config_path = tmp_path / "ordinary.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {
            "vm_spec": {
                "ssh_public_key": "ssh-ed25519 fixture",
            }
        },
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    trace: list[str] = []
    policy = SimpleNamespace(managed_action=None)

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            trace.append("construct")

        def prepare_ordinary_ssh_policy(self, spec, instances, **kwargs):
            assert kwargs["dry_run"] is True
            trace.append("preflight")
            return policy

        def check_changes(self, spec):
            trace.append("check-changes")
            return []

        def ensure_group(self, *args, **kwargs):
            raise AssertionError("dry-run must not provision")

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=_static_route_plan()),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.publish_vm_ha_ssh_trust") as publish_trust,
        patch("nebius_vpngw.cli.SSHPush") as ssh_push,
    ):
        result = CliRunner().invoke(
            app,
            ["apply", "--local-config-file", str(config_path), "--dry-run"],
        )

    assert result.exit_code == 0, result.stdout
    assert trace == ["construct", "preflight", "check-changes"]
    assert "Dry-run complete" in result.stdout
    publish_trust.assert_not_called()
    ssh_push.assert_not_called()


def test_ordinary_dry_run_reports_required_enrollment_without_learning(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ordinary.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {"ssh_public_key": "ssh-ed25519 fixture"}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    enroll = Mock(side_effect=AssertionError("dry-run learned network trust"))

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def prepare_ordinary_ssh_policy(self, *args, **kwargs):
            raise LegacyOrdinarySSHEnrollmentRequired({"nebius-vpn-gw-0"})

        def check_changes(self, _spec):
            return []

        enroll_ordinary_ssh_host_keys = enroll

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=_static_route_plan()),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.publish_vm_ha_ssh_trust") as publish,
    ):
        result = CliRunner().invoke(
            app,
            ["apply", "--local-config-file", str(config_path), "--dry-run"],
        )

    assert result.exit_code == 1, result.stdout
    assert "Dry-run blocked" in result.stdout
    enroll.assert_not_called()
    publish.assert_not_called()


def test_ordinary_apply_enrolls_then_repreflights_before_publication(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ordinary.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {"ssh_public_key": "ssh-ed25519 fixture"}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    plan = _static_route_plan()
    policy = SimpleNamespace(managed_action="enroll")
    enrollment = {"nebius-vpn-gw-0": object()}
    trace: list[str] = []
    discovery_count = 0

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            self.runtime = kwargs.get("ssh_policy") is policy

        def prepare_ordinary_ssh_policy(self, *args, **kwargs):
            nonlocal discovery_count
            discovery_count += 1
            trace.append(f"preflight-{discovery_count}")
            if discovery_count == 1:
                raise LegacyOrdinarySSHEnrollmentRequired({"nebius-vpn-gw-0"})
            assert kwargs["legacy_host_key_enrollments"] is enrollment
            return policy

        def check_changes(self, _spec):
            trace.append("check-changes")
            return []

        def enroll_ordinary_ssh_host_keys(self, *args, **kwargs):
            trace.append("enroll")
            return enrollment

        def ensure_group(self, *args, **kwargs):
            assert self.runtime
            trace.append("ensure-group")
            return {}

    class FakeSSHPush:
        def deactivate_vm_ha(self, *args, **kwargs) -> bool:
            return False

        def push_config_and_reload(self, *args, **kwargs) -> None:
            return None

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
        patch(
            "nebius_vpngw.cli.publish_vm_ha_ssh_trust",
            side_effect=lambda selected: trace.append(
                "publish" if selected is policy else "publish-wrong"
            ),
        ),
    ):
        result = CliRunner().invoke(app, ["apply", "-c", str(config_path)])

    assert result.exit_code == 0, result.stdout
    assert trace == [
        "preflight-1",
        "check-changes",
        "enroll",
        "preflight-2",
        "check-changes",
        "publish",
        "ensure-group",
    ]


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
            "nebius_vpngw.deploy.vm_manager.VMManager._get_vm_by_name_for_ordinary_ssh_preflight",
            side_effect=lambda _, name: (trace.append(f"compute:{name}"), None)[1],
        ) as compute_read,
        patch(
            "nebius_vpngw.deploy.vm_manager.require_vm_ha_ssh_policy",
            return_value=SimpleNamespace(managed_action=None),
        ),
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
    compute_read.assert_called_once()
    assert requests == []
    assert sa_calls == ["test-sa"]
    assert trace == ["sa-token", "compute:nebius-vpn-gw-0"]
    assert "Analyzing configuration changes" in result.stdout
    assert "Using the short-lived token for the requested Nebius Service Account." in (
        result.stdout
    )
    assert "Using the explicitly supplied Nebius IAM token." not in result.stdout


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

    class FakeVMManager(_ContextManagedFake):
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
            "nebius_vpngw.cli._apply_operator_auth_token",
            side_effect=lambda: trace.append("operator-auth") or None,
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
        token = "sa-token" if sa_name is not None else None
        assert (
            trace.index(auth_event) < trace.index(f"manager:{token}") < trace.index("check-changes")
        )
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

    class FakeVMManager(_ContextManagedFake):
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

    class FakeVMManager(_ContextManagedFake):
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

    class FakeVMManager(_ContextManagedFake):
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

    class FakeVMManager(_ContextManagedFake):
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


@pytest.mark.parametrize("identity_failure", [False, True])
def test_apply_waits_for_readiness_but_not_identity_failure(
    tmp_path: Path,
    identity_failure: bool,
) -> None:
    config_path = tmp_path / "static.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {"ssh_username": "operator"}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    plan = _static_route_plan()
    pushed_targets: list[str] = []

    ssh_not_ready = {
        "reachable": False,
        "cloud_init_complete": False,
        "strongswan_installed": False,
        "frr_installed": False,
        "agent_installed": False,
        "esp4_ready": False,
        "esp4_reboot_pending": False,
        "message": "SSH not ready yet",
    }
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
    health_results = [ssh_not_ready, pending_health, pending_health, ready_health]

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def discover_former_vm_ha_candidate_members(
            self, spec, *, allow_unmarked_runtime_probe=False
        ):
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

        def check_vm_health(self, vm_name, vm_ip, *, username="ubuntu") -> dict[str, object]:
            assert username == "operator"
            if identity_failure:
                raise RuntimeError("SSH host identity verification failed for fixture")
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
        patch("nebius_vpngw.cli.print", wraps=rich_print) as print_mock,
        patch("time.sleep", return_value=None),
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    if identity_failure:
        assert result.exit_code == 1
        assert isinstance(result.exception, RuntimeError)
        assert pushed_targets == []
    else:
        assert result.exit_code == 0
        assert pushed_targets == ["203.0.113.10"]
        print_mock.assert_any_call("nebius-vpn-gw-0 (203.0.113.10): SSH not ready yet")
        assert "SSH not ready yet" in result.stdout
        assert "waiting for reboot" in result.stdout
        assert "Config push gate passed" in result.stdout


def test_prep_network_allows_missing_peer_psk_placeholders(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["region_id"] = "eu-east1"
    sample_config["gateway_group"]["region"] = "eu-west1"
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
    loader_calls: list[dict[str, t.Any]] = []

    def tracked_load(path: Path, **kwargs: t.Any) -> dict[str, t.Any]:
        loader_calls.append(kwargs)
        return load_local_config(path, **kwargs)

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            assert kwargs["region"] == "eu-north1"
            assert kwargs["region_id"] == "eu-north1"

        def prepare_network_foundation(self, spec: GatewayGroupSpec) -> str:
            assert spec.name == "nebius-vpn-gw"
            assert spec.instance_count == 1
            assert spec.region == "eu-north1"
            return "subnet-1"

        def prepare_public_allocations_in_subnet(
            self,
            spec: GatewayGroupSpec,
            *,
            subnet_id: str,
            desired_external_ips: list[list[str]] | None = None,
        ) -> list[list[str]]:
            assert spec.name == "nebius-vpn-gw"
            assert spec.instance_count == 1
            assert spec.region == "eu-north1"
            assert subnet_id == "subnet-1"
            assert desired_external_ips == [[]]
            return [["203.0.113.10"]]

    with (
        patch("nebius_vpngw.cli.load_local_config", side_effect=tracked_load),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli._update_external_ips_in_yaml", return_value=True),
    ):
        result = CliRunner().invoke(
            app,
            [
                "prep-network",
                "--local-config-file",
                str(config_path),
                "--region",
                "eu-north1",
            ],
        )

    assert result.exit_code == 0
    assert "Reserved public IPs:" in result.stdout
    assert "203.0.113.10" in result.stdout
    assert loader_calls == [
        {
            "allow_missing_placeholders": True,
            "validate_schema": False,
            "region_override": "eu-north1",
        }
    ]
    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert persisted["region_id"] == "eu-east1"
    assert persisted["gateway_group"]["region"] == "eu-west1"


def test_prep_network_reports_allocations_when_yaml_update_fails(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    config_path = tmp_path / "prep-network-update-failure.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def prepare_network_foundation(self, spec: GatewayGroupSpec) -> str:
            return "subnet-1"

        def prepare_public_allocations_in_subnet(
            self,
            spec: GatewayGroupSpec,
            *,
            subnet_id: str,
            desired_external_ips: list[list[str]] | None = None,
        ) -> list[list[str]]:
            assert subnet_id == "subnet-1"
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


def test_prep_network_completes_and_publishes_partial_external_ip_matrix(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["gateway_group"]["instance_count"] = 2
    sample_config["gateway_group"]["external_ips"] = [["203.0.113.10"], []]
    config_path = tmp_path / "partial-prep.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def prepare_network_foundation(self, spec: GatewayGroupSpec) -> str:
            assert spec.instance_count == 2
            return "subnet-1"

        def prepare_public_allocations_in_subnet(
            self,
            spec: GatewayGroupSpec,
            *,
            subnet_id: str,
            desired_external_ips: list[list[str]] | None,
        ) -> list[list[str]]:
            assert subnet_id == "subnet-1"
            assert desired_external_ips == [["203.0.113.10"], []]
            return [["203.0.113.10"], ["203.0.113.20"]]

    with (
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch(
            "nebius_vpngw.cli._update_external_ips_in_yaml",
            return_value=True,
        ) as update,
    ):
        result = CliRunner().invoke(
            app,
            [
                "prep-network",
                "--local-config-file",
                str(config_path),
                "--no-interactive",
            ],
        )

    assert result.exit_code == 0, result.output
    assert update.call_args.args == (
        config_path,
        [["203.0.113.10"], ["203.0.113.20"]],
    )
    assert update.call_args.kwargs["expected_fingerprint"] is not None


def test_prep_network_reverifies_selected_allocation_after_convergence(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    config_path = tmp_path / "selected-prep.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    candidate = PublicAllocationCandidate(
        allocation_id="allocation-1",
        name="reserved-a",
        address="203.0.113.10",
        resource_version=4,
    )

    class FakeVMManager(_ContextManagedFake):
        verification_calls: list[dict[tuple[int, int], PublicAllocationCandidate]] = []

        def __init__(self, *args, **kwargs) -> None:
            pass

        def prepare_network_foundation(self, spec: GatewayGroupSpec) -> str:
            return "subnet-1"

        def list_eligible_public_allocations(
            self,
            spec: GatewayGroupSpec,
            *,
            subnet_id: str,
        ) -> list[PublicAllocationCandidate]:
            return [candidate]

        def verify_selected_public_allocations(
            self,
            spec: GatewayGroupSpec,
            *,
            subnet_id: str,
            selections: dict[tuple[int, int], PublicAllocationCandidate],
        ) -> None:
            self.verification_calls.append(selections)

        def prepare_public_allocations_in_subnet(
            self,
            spec: GatewayGroupSpec,
            *,
            subnet_id: str,
            desired_external_ips: list[list[str]] | None,
        ) -> list[list[str]]:
            assert desired_external_ips == [[candidate.address]]
            return [[candidate.address]]

    FakeVMManager.verification_calls = []
    with (
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.typer.prompt", return_value="existing"),
        patch("nebius_vpngw.cli.typer.confirm", return_value=True),
        patch("nebius_vpngw.cli._update_external_ips_in_yaml", return_value=True),
    ):
        result = CliRunner().invoke(
            app,
            [
                "prep-network",
                "--local-config-file",
                str(config_path),
                "--interactive",
            ],
        )

    assert result.exit_code == 0, result.output
    assert FakeVMManager.verification_calls == [
        {(0, 0): candidate},
        {(0, 0): candidate},
    ]


@pytest.mark.parametrize(
    "mutation",
    ("too-many-instances", "flat-external-ips", "multiple-nics"),
)
def test_prep_network_rejects_malformed_gateway_shape_before_authentication(
    tmp_path: Path,
    sample_config: dict,
    mutation: str,
) -> None:
    if mutation == "too-many-instances":
        sample_config["gateway_group"]["instance_count"] = 11
    elif mutation == "flat-external-ips":
        sample_config["gateway_group"]["external_ips"] = ["203.0.113.10"]
    else:
        sample_config["gateway_group"]["vm_spec"]["num_nics"] = 2
    config_path = tmp_path / f"invalid-{mutation}.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    with (
        patch("nebius_vpngw.cli._ensure_authentication") as authenticate,
        patch("nebius_vpngw.cli.VMManager") as manager,
    ):
        result = CliRunner().invoke(
            app,
            [
                "prep-network",
                "--local-config-file",
                str(config_path),
                "--no-interactive",
            ],
        )

    assert result.exit_code == 1
    assert "Invalid gateway network-preparation configuration" in result.output
    authenticate.assert_not_called()
    manager.assert_not_called()


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


def test_prep_network_reports_region_failure_before_cloud_access(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config.pop("region_id", None)
    sample_config["gateway_group"].pop("region", None)
    config_path = tmp_path / "prep-network-missing-region.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    with patch(
        "nebius_vpngw.cli._ensure_authentication",
        side_effect=AssertionError("region failure must precede authentication"),
    ):
        result = CliRunner().invoke(
            app,
            ["prep-network", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 1
    assert "Failed to resolve Nebius region" in result.stdout
    assert "Nebius region authority region_id must resolve" in result.stdout


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


@pytest.mark.parametrize(
    ("plan_vm_ha", "local_config_was_explicit", "approval_flags_present", "message"),
    [
        (None, True, False, "gateway_group.vm_ha"),
        (object(), False, False, "--local-config-file"),
        (object(), True, True, "cannot be combined"),
    ],
)
def test_vm_ha_peer_rotation_preparation_rejects_unsupported_requests(
    plan_vm_ha: object | None,
    local_config_was_explicit: bool,
    approval_flags_present: bool,
    message: str,
) -> None:
    plan = SimpleNamespace(vm_ha=plan_vm_ha)

    with pytest.raises(typer.BadParameter, match=message):
        _validate_vm_ha_peer_rotation_preparation(
            plan,
            local_config_was_explicit=local_config_was_explicit,
            approval_flags_present=approval_flags_present,
        )


def test_vm_ha_peer_rotation_preparation_accepts_explicit_vm_ha_request() -> None:
    _validate_vm_ha_peer_rotation_preparation(
        SimpleNamespace(vm_ha=object()),
        local_config_was_explicit=True,
        approval_flags_present=False,
    )


@pytest.mark.parametrize(
    "approval_option",
    (
        "--approve-vm-ha-migration",
        "--recover-vm-ha-migration",
        "--replace-failed-vm-ha-passive",
    ),
)
def test_vm_ha_peer_rotation_preparation_rejects_each_approval_flag_before_effects(
    tmp_path: Path,
    approval_option: str,
) -> None:
    config_path = tmp_path / "vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    plan = SimpleNamespace(vm_ha=object(), validate=lambda: None)

    class ForbiddenVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("VMManager must not be constructed")

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value={}),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli.VMManager", ForbiddenVMManager),
    ):
        result = CliRunner().invoke(
            app,
            [
                "apply",
                "--local-config-file",
                str(config_path),
                "--prepare-vm-ha-peer-rotation",
                approval_option,
                "approval-digest",
            ],
        )

    assert result.exit_code != 0
    assert "cannot be combined" in result.output


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
        nebius_service_account_id="service-account-a",
        nebius_authorized_key_id="authorized-key-a",
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
                nebius_credentials_sha256="d" * 64,
            ),
            SimpleNamespace(
                node_id="node-passive",
                role=SimpleNamespace(value="passive"),
                compute_id="compute-1",
                network_interface_name="eth0",
                nebius_credentials_sha256="d" * 64,
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
        operation_id="e" * 64,
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

    with pytest.raises(ValueError, match="no durable bootstrap-timeout evidence"):
        _vm_ha_failed_passive_replacement_plan(plan, state, current)
    transaction = state.transaction
    assert transaction is not None
    state = replace(
        state,
        transaction=transaction.advance(
            predecessor_sha256=state.record_sha256,
            completed_effect="verify-1-bootstrap-timeout-gateway-1",
        ),
    )
    passive_name, digest = _vm_ha_failed_passive_replacement_plan(plan, state, current)

    assert passive_name == "gateway-1"
    assert len(digest) == 64
    changed = json.loads(json.dumps(current))
    changed["members"][0]["compute_revision"] = "changed"
    with pytest.raises(ValueError, match="unrelated cloud drift"):
        _vm_ha_failed_passive_replacement_plan(plan, state, changed)


def test_missing_standby_approval_binds_managed_ssh_rotation() -> None:
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
    desired = {"desired": "unchanged"}
    state = VMHALifecycleState.start_provisioning(
        project_id="project-test",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_name="gateway-cluster-shared-private-ip",
        members=members,
        operation_id="initial-operation",
        approval_kind="migration",
        approval_digest="a" * 64,
        desired_state_digest=_canonical_digest(desired),
        current_state_digest="c" * 64,
        initial_resource_bindings={
            "compute:gateway-0": "compute-0",
            "compute:gateway-1": "compute-1",
            "disk:gateway-0": "disk-0",
            "disk:gateway-1": "disk-1",
            "primary-allocation:gateway-0:eth0": "primary-0",
            "primary-allocation:gateway-1:eth0": "primary-1",
            "public-allocation:gateway-0:eth0": "public-0",
            "public-allocation:gateway-1:eth0": "public-1",
            "shared-allocation-id": "shared-1",
            "shared-allocation-owner-compute": "compute-0",
            "shared-allocation-owner-nic": "eth0",
        },
    )
    state = replace(
        state,
        status=VMHALifecycleStatus.ACTIVE,
        allocation_id="shared-1",
        route_runtime_id="route-runtime-1",
        route_targets=("route-table-1:10.0.0.0/8",),
    )
    observation = {
        "members": [
            {
                "aliases": ["shared-1"],
                "boot_disk_id": "disk-0",
                "compute_id": "compute-0",
                "compute_revision": "10",
                "instance_name": "gateway-0",
                "network_interface_name": "eth0",
                "present": True,
                "primary_allocation_id": "primary-0",
                "public_allocation_id": "public-0",
                "state": "running",
            },
            {"instance_name": "gateway-1", "present": False},
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "shared-1",
            "owner": {
                "compute_id": "compute-0",
                "network_interface_name": "eth0",
            },
            "present": True,
        },
    }
    rotation = VMHASSHIdentityRotationIntent(
        hostname="gateway-1",
        trust_scope_sha256="d" * 64,
        old_fingerprint="SHA256:old",
        predecessor_receipt_sha256="e" * 64,
        predecessor_projection_sha256="f" * 64,
    )
    plan = SimpleNamespace(vm_ha=object())

    with patch(
        "nebius_vpngw.cli._vm_ha_desired_approval_state",
        return_value=desired,
    ):
        without_rotation = _vm_ha_missing_standby_replacement_plan(
            plan,
            state,
            observation,
        )
        with_rotation = _vm_ha_missing_standby_replacement_plan(
            plan,
            state,
            observation,
            ssh_identity_rotation=rotation,
        )
        changed_predecessor = _vm_ha_missing_standby_replacement_plan(
            plan,
            state,
            observation,
            ssh_identity_rotation=replace(
                rotation,
                predecessor_receipt_sha256="1" * 64,
            ),
        )

    assert with_rotation.ssh_identity_rotation == rotation
    assert with_rotation.approval_digest != without_rotation.approval_digest
    assert with_rotation.operation_id != without_rotation.operation_id
    assert changed_predecessor.approval_digest != with_rotation.approval_digest


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


def test_vm_ha_apply_authenticates_runtime_credentials_before_effect_boundaries(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "vm-ha-credential-blocked.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    planned_instances = (
        SimpleNamespace(vm_ha_node=SimpleNamespace(node_id="node-active")),
        SimpleNamespace(vm_ha_node=SimpleNamespace(node_id="node-passive")),
    )
    plan = SimpleNamespace(
        vm_ha=object(),
        validate=lambda: None,
        iter_instance_configs=lambda: iter(planned_instances),
    )
    local_cfg = {
        "project_id": "project-test",
        "gateway_group": {"vm_spec": {}},
    }

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch(
            "nebius_vpngw.cli.inspect_managed_vm_ha_credentials",
            side_effect=VMHACredentialIdentityError("authentication-failed"),
        ) as preflight,
        patch("nebius_vpngw.cli._ensure_authentication") as authenticate,
        patch("nebius_vpngw.cli.VMHALifecycleStore") as lifecycle_store,
        patch("nebius_vpngw.cli.VMManager") as manager,
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 1
    assert "runtime credential inspection failed" in result.stdout
    assert "authentication-failed" in result.stdout
    preflight.assert_called_once()
    authenticate.assert_not_called()
    lifecycle_store.assert_not_called()
    manager.assert_not_called()


def test_vm_ha_apply_prepares_default_directory_but_not_a_retained_member_key(
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
    monkeypatch.delenv("VPNGW_SSH_HOST_KEYS_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    calls: list[str] = []

    class FakeVMManager(_ContextManagedFake):
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
    assert "original SSH private host key for retained member gateway-0" in result.stdout
    assert calls == ["construct", "discover"]
    scope = VMHASSHTrustScope(
        tenant_id="tenant-a",
        project_id="project-a",
        region_id="eu-west1",
        gateway_name="gateway",
        cluster_id="cluster-a",
    )
    default_directory = tmp_path / ".ssh" / "nebius-vpngw" / "host-keys" / "gateway" / scope.digest
    assert default_directory.is_dir()
    assert not tuple(default_directory.glob("*.key"))


def test_vm_ha_apply_routes_cli_auth_failure_around_ssh_preflight_to_renderer(
    tmp_path: Path,
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

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def discover_vm_ha_members(self, spec):
            raise NebiusCLIAuthenticationError("PRIVATE_CLI_AUTH_DETAIL")

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
    assert "VM-HA apply stopped: Nebius cloud authentication was rejected." in result.stdout
    assert "Refresh the Nebius CLI profile or replace NEBIUS_IAM_TOKEN" in result.stdout
    assert "SSH trust preflight failed" not in result.stdout
    assert "PRIVATE_CLI_AUTH_DETAIL" not in result.stdout
    assert "Traceback" not in result.stdout


@pytest.mark.parametrize("routing_mode", ("static", "bgp"))
def test_vm_ha_apply_ssh_recovery_is_shared_across_routing_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routing_mode: str,
) -> None:
    config_path = tmp_path / f"vm-ha-{routing_mode}.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    members = (
        SimpleNamespace(hostname="gateway-0", external_ip="203.0.113.10"),
        SimpleNamespace(hostname="gateway-1", external_ip="203.0.113.11"),
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
        gateway_group=SimpleNamespace(
            name="gateway",
            region="eu-west1",
            routing_mode=routing_mode,
        ),
        validate=lambda: None,
        iter_instance_configs=lambda: iter(members),
    )
    local_cfg = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "region_id": "eu-west1",
        "gateway_group": {"routing_mode": routing_mode, "vm_spec": {}},
    }
    monkeypatch.delenv("VPNGW_SSH_KNOWN_HOSTS_FILE", raising=False)
    monkeypatch.delenv("VPNGW_SSH_HOST_KEYS_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def discover_vm_ha_members(self, spec):
            return {member.hostname: member.external_ip for member in members}

        def vm_ha_ssh_trust_bindings(self, spec, **kwargs):
            assert kwargs["retained_hosts"] == {member.hostname for member in members}
            return {member.hostname: (lambda: None) for member in members}

        def recover_vm_ha_ssh_host_keys(self, hostnames, **kwargs):
            raise AssertionError("recovery must stay lazy inside the shared resolver")

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch(
            "nebius_vpngw.cli.require_vm_ha_ssh_policy",
            side_effect=RuntimeError("stop after shared SSH recovery wiring"),
        ) as require_policy,
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 1
    assert "SSH trust preflight failed before external mutation" in result.stdout
    assert require_policy.call_args.args[0] == (
        ("gateway-0", "203.0.113.10"),
        ("gateway-1", "203.0.113.11"),
    )
    assert require_policy.call_args.kwargs["retained_hosts"] == {
        "gateway-0",
        "gateway-1",
    }
    assert require_policy.call_args.kwargs["allow_default_known_hosts_import"] is True
    assert set(require_policy.call_args.kwargs["default_known_hosts_bindings"]) == {
        "gateway-0",
        "gateway-1",
    }
    assert callable(require_policy.call_args.kwargs["host_identity_recovery"])


def test_vm_ha_apply_treats_lifecycle_bound_missing_compute_as_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "vm-ha-replacement.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    lifecycle = _lifecycle_state(status=VMHALifecycleStatus.PROVISIONING)
    vm_ha_spec = SimpleNamespace(
        cluster_id=lifecycle.cluster_id,
        members=tuple(
            SimpleNamespace(
                instance_index=member.instance_index,
                node_id=member.node_id,
                role=SimpleNamespace(value=member.role),
            )
            for member in lifecycle.members
        ),
    )
    instances = tuple(
        SimpleNamespace(hostname=member.instance_name, external_ip=member.public_ip)
        for member in lifecycle.members
    )
    plan = SimpleNamespace(
        vm_ha=vm_ha_spec,
        gateway_group=SimpleNamespace(
            name=lifecycle.gateway_name,
            region="eu-west1",
            vm_ha=vm_ha_spec,
        ),
        validate=lambda: None,
        iter_instance_configs=lambda: iter(instances),
    )
    local_cfg = {
        "tenant_id": "tenant-a",
        "project_id": lifecycle.project_id,
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
    }
    existing = {instances[0].hostname: instances[0].external_ip}
    monkeypatch.delenv("VPNGW_SSH_KNOWN_HOSTS_FILE", raising=False)
    monkeypatch.delenv("VPNGW_SSH_HOST_KEYS_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def discover_vm_ha_members(self, spec):
            return existing

        def vm_ha_ssh_trust_bindings(self, spec, **kwargs):
            assert kwargs["retained_hosts"] == set(existing)
            return {instances[0].hostname: (lambda: None)}

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch("nebius_vpngw.cli.VMHALifecycleStore.read", return_value=lifecycle),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch(
            "nebius_vpngw.cli.require_vm_ha_ssh_policy",
            side_effect=RuntimeError("stop after lifecycle retention classification"),
        ) as require_policy,
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 1
    assert require_policy.call_args.kwargs["enrollment_hosts"] == {instances[1].hostname}
    assert require_policy.call_args.kwargs["retained_hosts"] == {
        instance.hostname for instance in instances
    }


@pytest.mark.parametrize(
    ("problem", "expected_next_action"),
    (
        (
            VMHAReplacementSSHIdentityProblem.OPERATOR_SOURCE_CONFLICT,
            "remove VPNGW_SSH_KNOWN_HOSTS_FILE and VPNGW_SSH_HOST_KEYS_DIR from the "
            "intended product-managed invocation, then rerun vm-ha to resume the "
            "checkpointed SSH identity rotation",
        ),
        (
            VMHAReplacementSSHIdentityProblem.MANAGED_PREDECESSOR_UNAVAILABLE,
            "restore the exact product-managed SSH trust predecessor bound to the "
            "checkpointed rotation, then rerun vm-ha",
        ),
    ),
)
def test_vm_ha_apply_planner_projects_persisted_rotation_problem(
    tmp_path: Path,
    problem: VMHAReplacementSSHIdentityProblem,
    expected_next_action: str,
) -> None:
    config_path = tmp_path / "vm-ha-replacement.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    lifecycle = _lifecycle_state(status=VMHALifecycleStatus.PROVISIONING)
    vm_ha_spec = SimpleNamespace(
        cluster_id=lifecycle.cluster_id,
        members=tuple(
            SimpleNamespace(
                instance_index=member.instance_index,
                node_id=member.node_id,
                role=SimpleNamespace(value=member.role),
            )
            for member in lifecycle.members
        ),
    )
    instances = tuple(
        SimpleNamespace(hostname=member.instance_name, external_ip=member.public_ip)
        for member in lifecycle.members
    )
    plan = SimpleNamespace(
        vm_ha=vm_ha_spec,
        gateway={},
        gateway_group=SimpleNamespace(
            name=lifecycle.gateway_name,
            region="eu-west1",
            vm_ha=vm_ha_spec,
        ),
        validate=lambda: None,
        iter_instance_configs=lambda: iter(instances),
    )
    local_cfg = {
        "tenant_id": "tenant-a",
        "project_id": lifecycle.project_id,
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
    }
    existing = {instances[0].hostname: instances[0].external_ip}
    rotation = VMHASSHIdentityRotationIntent(
        hostname=instances[1].hostname,
        trust_scope_sha256="d" * 64,
        old_fingerprint="SHA256:old",
        predecessor_receipt_sha256="e" * 64,
        predecessor_projection_sha256="f" * 64,
    )
    replacement = SimpleNamespace(
        target_instance_name=instances[1].hostname,
        authorization_persisted=True,
        ssh_identity_rotation=rotation,
    )

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def discover_vm_ha_members(self, spec):
            return existing

        def observe_vm_ha_migration_state(self, spec, local_prefixes=None):
            return {"observation": "owner-only"}

    def reject_checkpointed_rotation(*_args, **kwargs):
        assert kwargs["rotate_identity_hosts"] == (instances[1].hostname,)
        raise VMHAReplacementSSHIdentityUnavailable(
            "checkpointed rotation prerequisite is unavailable",
            problem=problem,
        )

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch("nebius_vpngw.cli.VMHALifecycleStore.read", return_value=lifecycle),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch(
            "nebius_vpngw.cli._vm_ha_missing_standby_replacement_plan",
            return_value=replacement,
        ),
        patch(
            "nebius_vpngw.cli.require_vm_ha_ssh_policy",
            side_effect=reject_checkpointed_rotation,
        ),
        pytest.raises(_VMHAApplyPlanningFailed) as raised,
    ):
        _plan_vm_ha_apply_convergence(config_path)

    assert raised.value.reason == "replacement-ssh-identity-unavailable"
    assert raised.value.next_action == expected_next_action


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
    manager_events: list[str] = []
    trust_policy = SimpleNamespace(managed_action="create")

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            manager_events.append("construct")

        def __enter__(self):
            manager_events.append("enter")
            return self

        def __exit__(self, *_args) -> None:
            manager_events.append("exit")

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
    assert manager_events == ["construct", "enter", "exit"]
    assert require_trust.call_args.args[0] == (
        ("nebius-vpn-gw-0", "203.0.113.99"),
        ("nebius-vpn-gw-1", "203.0.113.11"),
    )
    assert require_trust.call_args.kwargs["additional_aliases"] == {
        "nebius-vpn-gw-0": ("203.0.113.10",),
        "nebius-vpn-gw-1": (),
    }
    assert require_trust.call_args.kwargs["persist_default_host_keys"] is False
    assert "would create the per-deployment SSH trust store" in result.stdout
    publish_trust.assert_not_called()
    ssh_push.assert_not_called()
    observed = lifecycle_store.read(
        expected_project_id="project-test",
        expected_gateway_name="nebius-vpn-gw",
    )
    assert observed == (removed if with_removed_tombstone else None)


def test_vm_ha_checkpoint_retains_the_ordinary_active_ssh_provenance() -> None:
    active = SimpleNamespace(
        instance_index=0,
        instance_name="gateway-0",
        role="active",
        compute_id="compute-0",
    )
    passive = SimpleNamespace(
        instance_index=1,
        instance_name="gateway-1",
        role="passive",
        compute_id="compute-1",
    )
    plan = SimpleNamespace(
        gateway_group=SimpleNamespace(name="gateway"),
        vm_ha=SimpleNamespace(
            members=(
                SimpleNamespace(instance_index=0, role=SimpleNamespace(value="active")),
                SimpleNamespace(instance_index=1, role=SimpleNamespace(value="passive")),
            )
        ),
    )
    lifecycle = SimpleNamespace(
        members=(active, passive),
        transaction=SimpleNamespace(
            approval_kind="migration",
            completed_effects=("provision-gateway-1-compute",),
            resource_bindings=(
                ("compute:gateway-0", "compute-0"),
                ("compute:gateway-1", "compute-1"),
            ),
        ),
    )

    assert _vm_ha_ordinary_migration_ssh_hosts(plan, lifecycle, None) == {"gateway-0"}
    lifecycle.transaction.completed_effects = ()
    assert _vm_ha_ordinary_migration_ssh_hosts(plan, lifecycle, None) == set()
    lifecycle.transaction.completed_effects = (
        "provision-gateway-0-compute",
        "provision-gateway-1-compute",
    )
    assert _vm_ha_ordinary_migration_ssh_hosts(plan, lifecycle, None) == set()


def test_vm_ha_active_lifecycle_does_not_reread_ordinary_ssh_predecessor() -> None:
    lifecycle = SimpleNamespace(status=VMHALifecycleStatus.ACTIVE)
    assert (
        _vm_ha_ordinary_migration_ssh_import_hosts(
            lifecycle,
            None,
            {"gateway-0"},
        )
        == set()
    )
    lifecycle.status = VMHALifecycleStatus.ACTIVATING
    assert _vm_ha_ordinary_migration_ssh_import_hosts(
        lifecycle,
        None,
        {"gateway-0"},
    ) == {"gateway-0"}


def test_vm_ha_post_compute_refresh_rebinds_exact_current_members() -> None:
    instances = (
        SimpleNamespace(hostname="gateway-0", external_ip="203.0.113.10"),
        SimpleNamespace(hostname="gateway-1", external_ip="203.0.113.11"),
    )
    plan = SimpleNamespace(
        gateway_group=SimpleNamespace(name="gateway"),
        vm_ha=object(),
        iter_instance_configs=lambda: iter(instances),
    )

    def active_binding() -> None:
        return None

    def passive_binding() -> None:
        return None

    manager = SimpleNamespace(
        discover_vm_ha_members=lambda _spec: {
            "gateway-0": "203.0.113.10",
            "gateway-1": "203.0.113.11",
        },
        vm_ha_ssh_trust_bindings=Mock(
            return_value={
                "gateway-0": active_binding,
                "gateway-1": passive_binding,
            }
        ),
        set_ssh_policy=Mock(),
    )
    refreshed = object()

    with patch(
        "nebius_vpngw.cli.require_vm_ha_ssh_policy",
        return_value=refreshed,
    ) as require_policy:
        result = _refresh_vm_ha_ssh_policy_after_compute(
            plan=plan,
            vm_manager=manager,
            vm_ips={
                "gateway-0": "203.0.113.10",
                "gateway-1": "203.0.113.11",
            },
            trust_scope=VMHASSHTrustScope(
                tenant_id="tenant-test",
                project_id="project-test",
                region_id="eu-west1",
                gateway_name="gateway",
                cluster_id="cluster-test",
            ),
            management_key_path=Path("/tmp/management-key"),
            management_public_key="ssh-ed25519 fixture",
            ordinary_migration_hosts={"gateway-0"},
        )

    assert result is refreshed
    manager.vm_ha_ssh_trust_bindings.assert_called_once_with(
        plan.gateway_group,
        retained_hosts={"gateway-0", "gateway-1"},
        ordinary_migration_hosts={"gateway-0"},
    )
    assert require_policy.call_args.args[0] == (
        ("gateway-0", "203.0.113.10"),
        ("gateway-1", "203.0.113.11"),
    )
    assert require_policy.call_args.kwargs["enrollment_hosts"] == ()
    assert require_policy.call_args.kwargs["allow_managed_repair"] is False
    assert require_policy.call_args.kwargs["allow_default_known_hosts_import"] is False
    assert require_policy.call_args.kwargs["default_known_hosts_bindings"] == {
        "gateway-0": active_binding,
        "gateway-1": passive_binding,
    }
    manager.set_ssh_policy.assert_called_once_with(refreshed)


@pytest.mark.parametrize(
    ("failing_stage_role", "final_transition_fault", "peer_shapes", "start_destroyed"),
    [
        (None, None, (("gcp", "static"),), False),
        (None, "prepare", (("gcp", "static"),), False),
        (None, "prepare", (("aws", "bgp"),), False),
        (None, "prepare", (("azure", "static"), ("azure", "bgp")), False),
        (None, "prepare", (("cisco", "static"),), False),
        (None, "prepare", (("generic", "bgp"),), False),
        ("passive", None, (("gcp", "static"),), False),
        ("active", None, (("gcp", "static"),), False),
        (None, "before-write", (("gcp", "static"),), False),
        (None, "before-write-once", (("gcp", "static"),), False),
        (None, "before-write-once-v3", (("gcp", "static"),), False),
        (None, "after-write", (("gcp", "static"),), False),
        (None, "relock-failure", (("gcp", "static"),), False),
        (None, "package-failure", (("gcp", "static"),), False),
        (None, None, (("gcp", "bgp"),), True),
    ],
)
def test_vm_ha_apply_delivers_nebius_credentials_passive_first_and_never_activates_partial_stage(
    tmp_path: Path,
    failing_stage_role: str | None,
    final_transition_fault: str | None,
    peer_shapes: tuple[tuple[str, str], ...],
    start_destroyed: bool,
) -> None:
    config_path = tmp_path / "vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    original_config = config_path.read_bytes()
    binding = SimpleNamespace(
        cluster_id="cluster-a",
        shared_allocation_id="shared-private",
        route_runtime_id="route-runtime-a",
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
        nebius_project_id="project-test",
        nebius_service_account_id="service-account-test",
        nebius_authorized_key_id="authorized-key-test",
        nodes=(
            SimpleNamespace(
                node_id="node-a",
                role=SimpleNamespace(value="active"),
                compute_id="compute-0",
                network_interface_name="eth0",
                nebius_credentials_path="/installed/node-a.json",
                nebius_credentials_sha256="d" * 64,
            ),
            SimpleNamespace(
                node_id="node-b",
                role=SimpleNamespace(value="passive"),
                compute_id="compute-1",
                network_interface_name="eth0",
                nebius_credentials_path="/installed/node-b.json",
                nebius_credentials_sha256="d" * 64,
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
    local_cfg = {
        "project_id": "project-test",
        "gateway_group": {"vm_spec": {}},
        "connections": [
            {"name": f"peer-{index}", "vendor": vendor, "routing_mode": mode}
            for index, (vendor, mode) in enumerate(peer_shapes)
        ],
    }
    plan.gateway_group.vm_ha = plan.vm_ha
    observed: list[tuple[str, str, object]] = []
    source_bundles: list[object] = []
    ensure_calls = 0
    resume_calls = 0
    active_write_attempts = 0
    written_states: list[VMHALifecycleState] = []
    destroyed_sha256: str | None = None

    class BoundResult(dict):
        vm_ha_runtime_binding = binding

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def discover_vm_ha_members(self, spec):
            if ensure_calls:
                return {
                    "gateway-0": "203.0.113.10",
                    "gateway-1": "203.0.113.11",
                }
            return {}

        def verify_vm_ha_existing_identities(
            self, existing, *, policy=None, username="ubuntu"
        ) -> None:
            expected = (
                {
                    "gateway-0": "203.0.113.10",
                    "gateway-1": "203.0.113.11",
                }
                if ensure_calls
                else {}
            )
            assert existing == expected

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

        def check_vm_health(self, vm_name, vm_ip, *, username="ubuntu") -> dict[str, object]:
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

        def ensure_vm_ha_agent_package(self, target, inst_cfg, cfg, *, artifact=None):
            assert artifact is not None
            role = inst_cfg.vm_ha_node.role.value
            observed.append(("package", role, binding))
            if final_transition_fault == "package-failure" and role == "active":
                raise RuntimeError("injected active package failure")
            return {
                "schema": "nebius-vpngw/vm-ha-package-v1",
                "package_version": "test",
                "cryptography_version": "test",
                "cffi_version": "test",
            }

        def install_vm_ha_apply_lock(self, target, inst_cfg, cfg, *, runtime_binding, operation_id):
            assert [role for phase, role, _binding in observed if phase == "package"][-2:] == [
                "passive",
                "active",
            ]
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
            self,
            target,
            inst_cfg,
            cfg,
            *,
            staged_receipt,
            runtime_binding,
            agent_artifact,
        ) -> None:
            assert agent_artifact is not None
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
        written_states.append(state)
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
    continuation_result = None
    prepared_state = None
    credential_bindings = {
        "credential-service-account:node-a": "service-account-test",
        "credential-authorized-key:node-a": "authorized-key-test",
        "credential-sha256:node-a": "d" * 64,
        "credential-service-account:node-b": "service-account-test",
        "credential-authorized-key:node-b": "authorized-key-test",
        "credential-sha256:node-b": "d" * 64,
    }
    verified_credentials = SimpleNamespace(
        service_account_id="service-account-test",
        project_id="project-test",
        resource_bindings=lambda: credential_bindings,
        approval_records=lambda: [
            {"node_id": "node-a", "credential_sha256": "d" * 64},
            {"node_id": "node-b", "credential_sha256": "d" * 64},
        ],
    )
    if start_destroyed:
        active_members = (
            VMHALifecycleMember(
                0,
                "gateway-0",
                "node-a",
                "active",
                "deleted-compute-0",
                "eth0",
                "203.0.113.10",
                "11",
                "deleted-disk-0",
                "subnet-a",
                "deleted-primary-0",
                "retained-public-0",
                ("deleted-shared-private",),
            ),
            VMHALifecycleMember(
                1,
                "gateway-1",
                "node-b",
                "passive",
                "deleted-compute-1",
                "eth0",
                "203.0.113.11",
                "12",
                "deleted-disk-1",
                "subnet-a",
                "deleted-primary-1",
                "retained-public-1",
            ),
        )
        active_state = VMHALifecycleState(
            status=VMHALifecycleStatus.ACTIVE,
            project_id="project-test",
            gateway_name="gateway",
            cluster_id="cluster-a",
            allocation_id="deleted-shared-private",
            allocation_name="gateway-cluster-a-shared-private-ip",
            members=active_members,
            route_runtime_id="deleted-route-runtime",
            route_targets=(
                json.dumps(
                    VMHARouteTarget(
                        project_id="project-test",
                        network_id="network-a",
                        workload_subnet_id="subnet-a",
                        route_table_id="route-table-a",
                    ).model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
            transaction=VMHAMigrationTransaction(
                operation_id="operation-before-destroy",
                approval_kind="migration",
                approval_digest="9" * 64,
                desired_state_digest="8" * 64,
                current_state_digest="7" * 64,
                checkpoint="active",
                pending_effect=None,
                completed_effects=(),
                resource_bindings=tuple(
                    sorted(
                        {
                            **credential_bindings,
                            "compute:gateway-0": "deleted-compute-0",
                            "compute:gateway-1": "deleted-compute-1",
                            "disk:gateway-0": "deleted-disk-0",
                            "disk:gateway-1": "deleted-disk-1",
                            "primary-allocation:gateway-0:eth0": "deleted-primary-0",
                            "primary-allocation:gateway-1:eth0": "deleted-primary-1",
                            "public-allocation:gateway-0:eth0": "retained-public-0",
                            "public-allocation:gateway-1:eth0": "retained-public-1",
                            "route-runtime-id": "deleted-route-runtime",
                            "shared-allocation-id": "deleted-shared-private",
                        }.items()
                    )
                ),
                revision=1,
                predecessor_sha256=None,
            ),
        )
        destruction = VMHALifecycleState.start_destruction(
            active_state,
            operation_id="operation-destroy",
            approval_digest="6" * 64,
            desired_state_digest="5" * 64,
            current_state_digest="4" * 64,
            destroy_plan_json='{"schema":"test-destroy"}',
            current_observation={},
        )
        destroyed = destruction.with_status(
            VMHALifecycleStatus.DESTROYED,
            checkpoint="destruction-verified-absent",
        )
        store = VMHALifecycleStore(config_path)
        store.write_verified(active_state, predecessor_sha256=None)
        store.write_verified(destruction, predecessor_sha256=active_state.record_sha256)
        store.write_verified(destroyed, predecessor_sha256=destruction.record_sha256)
        destroyed_sha256 = destroyed.record_sha256

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch(
            "nebius_vpngw.cli.inspect_managed_vm_ha_credentials",
            return_value=SimpleNamespace(
                action="reuse",
                credentials=verified_credentials,
                source_path=Path("/private/managed-vm-ha-credentials.json"),
                approval_record=lambda: {
                    "action": "reuse",
                    "runtime_credentials": verified_credentials.approval_records(),
                },
            ),
        ),
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
        apply_args = ["apply", "--local-config-file", str(config_path)]
        if final_transition_fault == "prepare":
            apply_args.append("--prepare-vm-ha-peer-rotation")
        first_result = CliRunner().invoke(app, apply_args)
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
            and final_transition_fault in {None, "prepare"}
            and first_result.exit_code == 0
        ):
            idempotent_result = CliRunner().invoke(app, apply_args)
            if final_transition_fault == "prepare":
                prepared_state = VMHALifecycleStore(config_path).read(
                    expected_project_id="project-test", expected_gateway_name="gateway"
                )
                continuation_result = CliRunner().invoke(
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
    elif final_transition_fault == "prepare":
        assert result.exit_code == 0, result.stdout
        assert idempotent_result is not None
        assert idempotent_result.exit_code == 0, idempotent_result.stdout
        assert prepared_state is not None
        assert prepared_state.status is VMHALifecycleStatus.ACTIVATING
        assert continuation_result is not None
        assert continuation_result.exit_code == 0, continuation_result.stdout
        assert "peer-rotation preparation completed successfully" in result.stdout
        assert "Releasing the current-owner lock" not in result.stdout
        preparation_trace = [
            ("stage", "passive"),
            ("stage", "active"),
            ("package", "passive"),
            ("package", "active"),
            ("lock", "passive"),
            ("lock", "active"),
            ("adopt", "active"),
            ("activate", "passive"),
            ("activate", "active"),
        ]
        ordinary_trace = preparation_trace + [("clear", "active"), ("clear", "passive")]
        assert [(phase, role) for phase, role, _ in observed] == (
            preparation_trace * 2 + ordinary_trace
        )
        state = VMHALifecycleStore(config_path).read(
            expected_project_id="project-test", expected_gateway_name="gateway"
        )
        assert state is not None and state.status is VMHALifecycleStatus.ACTIVE
        assert ensure_calls == 1
        assert resume_calls == 2
    elif failing_stage_role is None and final_transition_fault in {None, "after-write"}:
        assert result.exit_code == 0, result.stdout
        expected_trace = [
            ("stage", "passive"),
            ("stage", "active"),
            ("package", "passive"),
            ("package", "active"),
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
        if start_destroyed:
            clean_successors = [
                item
                for item in written_states
                if item.status is VMHALifecycleStatus.PROVISIONING
                and item.transaction is not None
                and item.transaction.revision == 1
                and item.transaction.predecessor_sha256 == destroyed_sha256
            ]
            assert len(clean_successors) == 1
            bindings = dict(clean_successors[0].transaction.resource_bindings)  # type: ignore[union-attr]
            assert set(bindings) == {
                *credential_bindings,
                "public-allocation:gateway-0:eth0",
                "public-allocation:gateway-1:eth0",
                "route-targets-digest",
            }
            assert bindings["public-allocation:gateway-0:eth0"] == "retained-public-0"
            assert bindings["public-allocation:gateway-1:eth0"] == "retained-public-1"
            assert config_path.read_bytes() == original_config
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
        elif final_transition_fault == "package-failure":
            assert "no apply-lock installation was attempted" in result.stdout
            assert "pre-existing locks were preserved" in result.stdout
            assert [role for phase, role, _ in observed if phase == "package"] == [
                "passive",
                "active",
            ]
            assert all(
                phase not in {"lock", "adopt", "activate", "clear"} for phase, _, _ in observed
            )
        else:
            assert all(phase != "activate" for phase, _, _ in observed)
    managed_source = Path("/private/managed-vm-ha-credentials.json")
    expected_sources = [managed_source]
    if failing_stage_role != "passive":
        expected_sources.append(managed_source)
    if final_transition_fault == "prepare":
        expected_sources *= 3
    elif final_transition_fault in {"before-write-once", "before-write-once-v3"} or (
        failing_stage_role is None and final_transition_fault is None
    ):
        expected_sources *= 2
    assert source_bundles == expected_sources
    assert all(item is binding for _, _, item in observed)


@pytest.mark.parametrize(
    "remote_already_released",
    (False, True),
    ids=("before-remote-release", "after-remote-release-before-local-complete"),
)
def test_missing_standby_apply_resumes_pending_inhibition_release_crash_window(
    tmp_path: Path,
    remote_already_released: bool,
) -> None:
    config_path = tmp_path / "vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
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
        gateway={"local_prefixes": []},
        manage_routes=False,
        should_manage_routes=lambda: False,
        validate=lambda: None,
        iter_instance_configs=lambda: iter((active, passive)),
    )
    plan.gateway_group.vm_ha = plan.vm_ha
    local_cfg = {
        "project_id": "project-test",
        "gateway_group": {"vm_spec": {}},
        "connections": [],
    }
    credentials = {
        "credential-authorized-key:node-a": "authorized-key-test",
        "credential-authorized-key:node-b": "authorized-key-test",
        "credential-service-account:node-a": "service-account-test",
        "credential-service-account:node-b": "service-account-test",
        "credential-sha256:node-a": "d" * 64,
        "credential-sha256:node-b": "d" * 64,
    }
    active_members = (
        VMHALifecycleMember(
            0,
            "gateway-0",
            "node-a",
            "active",
            "compute-0",
            "eth0",
            "203.0.113.10",
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
            "compute-old",
            "eth0",
            "203.0.113.11",
            "12",
            "disk-old",
            "subnet-a",
            "primary-1",
            "public-1",
        ),
    )
    before_observation = {
        "members": [
            {
                "aliases": ["shared-private"],
                "boot_disk_id": "disk-0",
                "compute_id": "compute-0",
                "compute_revision": "11",
                "instance_name": "gateway-0",
                "network_interface_name": "eth0",
                "primary_allocation_id": "primary-0",
                "public_allocation_id": "public-0",
                "public_ip": "203.0.113.10",
                "present": True,
                "state": "running",
            },
            {
                "aliases": [],
                "boot_disk_id": "disk-old",
                "compute_id": "compute-old",
                "compute_revision": "12",
                "instance_name": "gateway-1",
                "network_interface_name": "eth0",
                "primary_allocation_id": "primary-1",
                "public_allocation_id": "public-1",
                "public_ip": "203.0.113.11",
                "present": True,
                "state": "running",
            },
        ],
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
    missing_observation = {
        **before_observation,
        "members": [
            before_observation["members"][0],
            {"instance_name": "gateway-1", "present": False},
        ],
    }
    terminal_observation = {
        **before_observation,
        "members": [
            before_observation["members"][0],
            {
                **before_observation["members"][1],
                "boot_disk_id": "disk-new",
                "compute_id": "compute-new",
                "compute_revision": "13",
            },
        ],
    }
    desired_digest = _canonical_digest(_vm_ha_desired_approval_state(plan))
    active_state = VMHALifecycleState.start_provisioning(
        project_id="project-test",
        gateway_name="gateway",
        cluster_id="cluster-a",
        allocation_name="gateway-cluster-a-shared-private-ip",
        members=active_members,
        operation_id="initial-operation",
        approval_kind="migration",
        approval_digest="d" * 64,
        desired_state_digest=desired_digest,
        current_state_digest="e" * 64,
        initial_resource_bindings=_vm_ha_initial_resource_bindings(
            before_observation,
            credential_bindings=credentials,
        ),
        current_observation=before_observation,
    )
    route_target = VMHARouteTarget(
        project_id="project-test",
        network_id="network-a",
        workload_subnet_id="subnet-a",
        route_table_id="route-table-a",
    )
    active_state = replace(
        active_state,
        status=VMHALifecycleStatus.ACTIVE,
        allocation_id="shared-private",
        route_runtime_id="route-runtime-a",
        route_targets=(
            json.dumps(
                route_target.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )
    replacement_disk_name = vm_ha_missing_standby_disk_name(
        gateway_name="gateway",
        instance_name="gateway-1",
        predecessor_sha256=active_state.record_sha256,
        cycle=1,
    )
    replacement_state = VMHALifecycleState.start_missing_standby_replacement(
        active_state,
        target_instance_name="gateway-1",
        replacement_cycle=1,
        replacement_disk_name=replacement_disk_name,
        operation_id="f" * 64,
        approval_digest="1" * 64,
        desired_state_digest=desired_digest,
        current_state_digest=_canonical_digest(missing_observation),
        current_observation=missing_observation,
    )
    assert replacement_state.transaction is not None
    disk_effect = vm_ha_missing_standby_replacement_effect("gateway-1", 1, "create-boot-disk")
    transaction = replacement_state.transaction.advance(
        predecessor_sha256=replacement_state.record_sha256,
        completed_effect=disk_effect,
        resource_updates={
            vm_ha_passive_replacement_binding_key("disk", "gateway-1", 1): "disk-new"
        },
    )
    replacement_state = replace(replacement_state, transaction=transaction)
    compute_effect = vm_ha_missing_standby_replacement_effect("gateway-1", 1, "create-compute")
    transaction = transaction.advance(
        predecessor_sha256=replacement_state.record_sha256,
        completed_effect=compute_effect,
        resource_updates={
            vm_ha_passive_replacement_binding_key("compute", "gateway-1", 1): "compute-new"
        },
        observation=tuple(sorted(replacement_state.transaction.observation)),
    )
    replacement_state = replace(replacement_state, transaction=transaction)
    terminal_members = (
        active_members[0],
        replace(
            active_members[1],
            compute_id="compute-new",
            compute_revision="13",
            disk_id="disk-new",
        ),
    )
    runtime_binding = SimpleNamespace(
        cluster_id="cluster-a",
        shared_allocation_id="shared-private",
        route_runtime_id="route-runtime-a",
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
        nebius_project_id="project-test",
        nebius_service_account_id="service-account-test",
        nebius_authorized_key_id="authorized-key-test",
        nodes=(
            SimpleNamespace(
                node_id="node-a",
                role=SimpleNamespace(value="active"),
                compute_id="compute-0",
                network_interface_name="eth0",
                nebius_credentials_path="/installed/node-a.json",
                nebius_credentials_sha256="d" * 64,
            ),
            SimpleNamespace(
                node_id="node-b",
                role=SimpleNamespace(value="passive"),
                compute_id="compute-new",
                network_interface_name="eth0",
                nebius_credentials_path="/installed/node-b.json",
                nebius_credentials_sha256="d" * 64,
            ),
        ),
        route_targets=(route_target,),
    )
    activating_state = _active_vm_ha_lifecycle_state(
        plan=plan,
        runtime_binding=runtime_binding,
        members=terminal_members,
        project_id="project-test",
        previous=replacement_state,
        status=VMHALifecycleStatus.ACTIVATING,
    )
    assert activating_state.transaction is not None
    inhibition_effect = "install-standby-replacement-inhibition-node-a"
    transaction = activating_state.transaction.advance(
        predecessor_sha256=activating_state.record_sha256,
        completed_effect=inhibition_effect,
    )
    activating_state = replace(activating_state, transaction=transaction)
    release_effect = "release-standby-replacement-inhibition-node-a"
    transaction = transaction.advance(
        predecessor_sha256=activating_state.record_sha256,
        checkpoint=f"before-{release_effect}",
        pending_effect=release_effect,
    )
    activating_state = replace(activating_state, transaction=transaction)
    lifecycle_store = VMHALifecycleStore(config_path)
    lifecycle_store.path.write_text(
        json.dumps(activating_state.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    lifecycle_store.path.chmod(0o600)

    class BoundResult(dict):
        vm_ha_runtime_binding = runtime_binding

    trace: list[str] = []

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def discover_vm_ha_members(self, spec):
            return {
                "gateway-0": "203.0.113.10",
                "gateway-1": "203.0.113.11",
            }

        def observe_vm_ha_migration_state(self, spec, local_prefixes=None):
            return terminal_observation

        def verify_vm_ha_existing_identities(self, existing, **kwargs) -> None:
            assert set(existing) == {"gateway-0"}

        def check_changes(self, spec):
            return []

        def set_vm_ha_lifecycle_journal(self, journal) -> None:
            self.journal = journal

        def replace_missing_vm_ha_standby(self, spec, local_prefixes, *, approval_digest):
            assert approval_digest == "1" * 64
            trace.append("resume-replacement-provisioning")
            return BoundResult(
                {
                    "gateway-0": "203.0.113.10",
                    "gateway-1": "203.0.113.11",
                }
            )

        def ensure_group(self, *args, **kwargs):
            raise AssertionError("release resume must not run ordinary reconciliation")

        def resume_vm_ha_activation(self, *args, **kwargs):
            raise AssertionError("replacement resume owns its exact provisioning result")

        def finalize_vm_ha_provisioning(self, *args, **kwargs):
            raise AssertionError("ACTIVATING resume must not rebind finalized members")

        def wait_for_vm_network(self, vm_name, vm_ip, timeout=180) -> bool:
            return True

        def check_vm_health(self, vm_name, vm_ip, *, username="ubuntu") -> dict[str, object]:
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

    remote_state = {"released": remote_already_released}

    class FakeSSHPush:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def release_vm_ha_standby_replacement_inhibition(
            self,
            target,
            hostname,
            cfg,
            *,
            inhibition,
        ) -> None:
            assert target == "203.0.113.10"
            assert hostname == "gateway-0"
            assert inhibition["operation_id"] == "f" * 64
            trace.append("release-idempotent" if remote_state["released"] else "release-first")
            remote_state["released"] = True

        def __getattr__(self, name: str):
            raise AssertionError(f"release resume must not call SSH method {name}")

    terminal_nodes: list[str] = []

    def terminal_status(*, predicate, inst_cfg, **_kwargs):
        node_id = inst_cfg.vm_ha_node.node_id
        owner = node_id == "node-a"
        payload = {
            "data_plane_mode": "active" if owner else "passive",
            "promotion_ready": owner,
            "observed_owner_node_id": "node-a",
            "ownership_epoch": "7",
            "pending_operation_id": None,
            "transfer_inhibition_operation_id": None,
            "mtls": {
                "state": "healthy",
                "operation_id": None,
                "inhibited": False,
            },
            "route_reconciliation": (
                {
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
                }
                if owner
                else None
            ),
        }
        assert predicate(payload)
        terminal_nodes.append(node_id)
        return payload

    verified_credentials = SimpleNamespace(
        service_account_id="service-account-test",
        project_id="project-test",
        resource_bindings=lambda: credentials,
        approval_records=lambda: [
            {"node_id": "node-a", "credential_sha256": "d" * 64},
            {"node_id": "node-b", "credential_sha256": "d" * 64},
        ],
    )
    ssh_policy = SimpleNamespace(managed_action=None)
    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch(
            "nebius_vpngw.cli.inspect_managed_vm_ha_credentials",
            return_value=SimpleNamespace(
                action="reuse",
                credentials=verified_credentials,
                source_path=Path("/private/managed-vm-ha-credentials.json"),
                approval_record=lambda: {
                    "action": "reuse",
                    "runtime_credentials": verified_credentials.approval_records(),
                },
            ),
        ),
        patch("nebius_vpngw.cli.require_vm_ha_ssh_policy", return_value=ssh_policy),
        patch(
            "nebius_vpngw.cli._vm_ha_missing_standby_owner_refresh_required",
            return_value=False,
        ),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", FakeSSHPush),
        patch(
            "nebius_vpngw.cli._wait_for_vm_ha_agent_status",
            side_effect=terminal_status,
        ),
    ):
        _apply_impl(
            local_config_file=config_path,
            recreate_gw=False,
            sa=None,
            project_id=None,
            region=None,
            dry_run=False,
            prepare_vm_ha_peer_rotation=False,
            approve_vm_ha_migration=None,
            recover_vm_ha_migration=None,
            replace_failed_vm_ha_passive=None,
            replace_missing_vm_ha_standby="1" * 64,
        )

    completed = lifecycle_store.read(
        expected_project_id="project-test",
        expected_gateway_name="gateway",
    )
    assert completed is not None
    assert completed.status is VMHALifecycleStatus.ACTIVE
    assert completed.transaction is not None
    assert completed.transaction.checkpoint == "missing-standby-replacement-complete"
    assert completed.transaction.pending_effect is None
    assert release_effect in completed.transaction.completed_effects
    assert remote_state["released"] is True
    assert trace == [
        "resume-replacement-provisioning",
        "release-idempotent" if remote_already_released else "release-first",
    ]
    assert set(terminal_nodes) == {"node-a", "node-b"}
    assert len(terminal_nodes) == 2


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
        "region_id": "eu-east1",
        "gateway_group": {"region": "eu-central1", "vm_spec": vm_spec},
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
    manager_regions: list[tuple[str | None, str | None]] = []
    manager_events: list[str] = []
    trust_order: list[str] = []

    class BoundResult(dict):
        vm_ha_runtime_binding = object()

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            self.index = len(manager_keys)
            manager_keys.append(kwargs.get("management_key_path"))
            manager_regions.append((kwargs.get("region"), kwargs.get("region_id")))

        def __enter__(self):
            manager_events.append(f"enter:{self.index}")
            return self

        def __exit__(self, *_args) -> None:
            manager_events.append(f"exit:{self.index}")

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
    assert manager_regions == [("eu-west1", "eu-west1"), ("eu-west1", "eu-west1")]
    assert manager_events == ["enter:0", "enter:1", "exit:1", "exit:0"]
    initial_policy_call = require_policy.call_args_list[0]
    assert initial_policy_call.kwargs["trust_scope"].region_id == "eu-west1"
    assert initial_policy_call.kwargs["management_key_path"] == expected_key
    assert initial_policy_call.kwargs["require_management_key"] is True
    assert initial_policy_call.kwargs["allow_managed_repair"] is True
    assert require_policy.call_args_list[-1].kwargs["allow_managed_repair"] is False
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


def test_vm_ha_apply_sanitizes_exhausted_sdk_deadline_and_restores_filter(
    monkeypatch,
    tmp_path,
    caplog,
    capsys,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    plan = SimpleNamespace(
        vm_ha=object(),
        gateway_group=SimpleNamespace(name="nebius-vpn-gw"),
    )

    class ApplyLock:
        def __init__(self, *, project_id: str, gateway_name: str) -> None:
            assert (project_id, gateway_name) == ("project-test", "nebius-vpn-gw")

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_local_config", lambda *_args, **_kwargs: config_path
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli.load_local_config",
        lambda _path: {"project_id": "project-test"},
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", ApplyLock)

    logger = logging.getLogger("nebius.aio.request")
    caplog.set_level(logging.ERROR, logger=logger.name)

    @_serialize_explicit_vm_ha_apply
    def command(
        local_config_file: Path | None = None,
        project_id: str | None = None,
        dry_run: bool = False,
    ) -> None:
        try:
            raise RuntimeError("PRIVATE_RETRY_DETAIL")
        except RuntimeError:
            logger.error(
                "request attempt 1 for Request failed but will be retried",
                exc_info=True,
            )
        provider_error = RequestError(  # type: ignore[arg-type]
            SimpleNamespace(code=StatusCode.DEADLINE_EXCEEDED)
        )
        raise RuntimeError("PRIVATE_PROVIDER_DETAIL") from provider_error

    with pytest.raises(typer.Exit) as raised:
        command(local_config_file=config_path)

    output = capsys.readouterr()
    assert raised.value.exit_code == 1
    assert output.out == (
        "VM-HA apply stopped: Nebius cloud request timed out after bounded retries.\n"
        "Run 'nebius-vpngw vm-ha --local-config-file <file>' to inspect and resume.\n"
    )
    assert "request attempt" not in caplog.text
    assert "PRIVATE_RETRY_DETAIL" not in caplog.text
    assert "PRIVATE_PROVIDER_DETAIL" not in output.out
    assert "Traceback" not in output.out

    logger.error("request attempt 2 for Request failed but will be retried")
    assert "request attempt 2" in caplog.text


def test_vm_ha_apply_sanitizes_unauthenticated_and_restores_filter(
    monkeypatch,
    tmp_path,
    caplog,
    capsys,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    plan = SimpleNamespace(
        vm_ha=object(),
        gateway_group=SimpleNamespace(name="nebius-vpn-gw"),
    )

    class ApplyLock:
        def __init__(self, *, project_id: str, gateway_name: str) -> None:
            assert (project_id, gateway_name) == ("project-test", "nebius-vpn-gw")

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_local_config", lambda *_args, **_kwargs: config_path
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli.load_local_config",
        lambda _path: {"project_id": "project-test"},
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", ApplyLock)

    logger = logging.getLogger("nebius.aio.request")
    caplog.set_level(logging.ERROR, logger=logger.name)

    @_serialize_explicit_vm_ha_apply
    def command(
        local_config_file: Path | None = None,
        project_id: str | None = None,
        dry_run: bool = False,
    ) -> None:
        try:
            raise RuntimeError("PRIVATE_RETRY_DETAIL")
        except RuntimeError:
            logger.error(
                "request attempt 1 for Request failed but will be retried",
                exc_info=True,
            )
        provider_error = RequestError(  # type: ignore[arg-type]
            SimpleNamespace(code=StatusCode.UNAUTHENTICATED)
        )
        raise RuntimeError("PRIVATE_PROVIDER_DETAIL") from provider_error

    with pytest.raises(typer.Exit) as raised:
        command(local_config_file=config_path)

    output = capsys.readouterr()
    assert raised.value.exit_code == 1
    assert output.out == (
        "VM-HA apply stopped: Nebius cloud authentication was rejected.\n"
        "Refresh the Nebius CLI profile or replace NEBIUS_IAM_TOKEN, then rerun apply.\n"
    )
    assert "request attempt" not in caplog.text
    assert "PRIVATE_RETRY_DETAIL" not in caplog.text
    assert "PRIVATE_PROVIDER_DETAIL" not in output.out
    assert "Traceback" not in output.out

    logger.error("request attempt 2 for Request failed but will be retried")
    assert "request attempt 2" in caplog.text


def test_ordinary_apply_preserves_exception_and_sdk_retry_diagnostic(
    monkeypatch,
    tmp_path,
    caplog,
) -> None:
    config_path = tmp_path / "gateway.config.yaml"
    plan = SimpleNamespace(
        vm_ha=None,
        gateway_group=SimpleNamespace(name="nebius-vpn-gw"),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_local_config", lambda *_args, **_kwargs: config_path
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli.load_local_config",
        lambda _path: {"project_id": "project-test"},
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)

    logger = logging.getLogger("nebius.aio.request")
    caplog.set_level(logging.ERROR, logger=logger.name)

    @_serialize_explicit_vm_ha_apply
    def command(
        local_config_file: Path | None = None,
        project_id: str | None = None,
        dry_run: bool = False,
    ) -> None:
        logger.error("request attempt 1 for Request failed but will be retried")
        raise RuntimeError("ordinary failure")

    with pytest.raises(RuntimeError, match="ordinary failure"):
        command(local_config_file=config_path, dry_run=True)

    assert "request attempt 1" in caplog.text


def test_vm_ha_sdk_code_matcher_ignores_implicit_exception_context() -> None:
    provider_error = RequestError(  # type: ignore[arg-type]
        SimpleNamespace(code=StatusCode.DEADLINE_EXCEEDED)
    )
    explicit_wrapper = RuntimeError("explicit wrapper")
    explicit_wrapper.__cause__ = provider_error

    assert _vm_ha_error_chain_has_sdk_code(explicit_wrapper, "DEADLINE_EXCEEDED")

    try:
        raise provider_error
    except RequestError:
        try:
            raise RuntimeError("unrelated cleanup failure")
        except RuntimeError as implicit_wrapper:
            assert implicit_wrapper.__cause__ is None
            assert not _vm_ha_error_chain_has_sdk_code(
                implicit_wrapper,
                "DEADLINE_EXCEEDED",
            )


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
        "controller_capabilities": ["vm-ha-standby-restoration-v2"],
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
    with pytest.raises(_VMHAAgentStatusStale, match="standby restoration capability"):
        _validate_vm_ha_agent_status(
            {**payload, "controller_capabilities": []},
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
        "controller_capabilities": ["vm-ha-standby-restoration-v2"],
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
        "controller_capabilities": ["vm-ha-standby-restoration-v2"],
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
        "auto_healing": {
            "state": "enabled",
            "peer_agrees": True,
            "accepted_start": False,
        },
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

    if state == "active":
        for committed_state in ("enabled", "disabled"):
            with pytest.raises(_VMHAAgentStatusPermanent, match="invalid display evidence"):
                _validate_vm_ha_display_status(
                    {
                        **payload,
                        "auto_healing": {
                            "state": committed_state,
                            "peer_agrees": False,
                            "accepted_start": False,
                        },
                    },
                    inst_cfg=inst_cfg,
                    runtime_binding=binding,
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
        "controller_capabilities": ["vm-ha-standby-restoration-v2"],
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
        "promotion_committed": False,
        "promotion_ready": False,
        "reasons": [],
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
    for controller_state in (
        "fencing",
        "ownership-transfer",
        "degraded-path",
        "repair-exhausted",
        "degraded",
    ):
        transitional = {
            **payload,
            "state": controller_state,
            "standby_ready": False,
            "standby_tunnel_state": "not-standby",
            "standby_readiness_reasons": ["controller-effect-pending"],
        }
        assert (
            _validate_vm_ha_planned_status(
                transitional,
                inst_cfg=inst_cfg,
                runtime_binding=binding,
            )
            is transitional
        )
    invalid_cases = (
        {**payload, "digests": {"configuration": "f" * 64}},
        {**payload, "allocation_id": "foreign"},
        {**payload, "promotion_committed": True},
        {**payload, "reasons": ["unsafe\nterminal-text"]},
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


def test_vm_ha_planned_terminal_runtime_binding_includes_exact_generation() -> None:
    lifecycle = _lifecycle_state()
    instance = SimpleNamespace(
        vm_ha_generation=SimpleNamespace(
            generation_id="a" * 64,
            digests=SimpleNamespace(
                configuration="a" * 64,
                static_routes="b" * 64,
                bgp_policy="c" * 64,
            ),
        )
    )

    binding = _vm_ha_planned_terminal_runtime_binding(lifecycle, instance)

    assert binding.cluster_id == lifecycle.cluster_id
    assert binding.shared_allocation_id == lifecycle.allocation_id
    assert binding.route_runtime_id == lifecycle.route_runtime_id
    assert binding.generation_id == "a" * 64
    assert binding.configuration_digest == "a" * 64
    assert binding.static_routes_digest == "b" * 64
    assert binding.bgp_policy_digest == "c" * 64


def test_vm_ha_planned_terminal_runtime_binding_accepts_exact_persisted_replacement() -> None:
    active = _lifecycle_state()
    owner, target = active.members
    before_observation = {
        "members": [
            {
                "aliases": list(owner.alias_allocation_ids),
                "boot_disk_id": owner.disk_id,
                "compute_id": owner.compute_id,
                "compute_revision": owner.compute_revision,
                "instance_name": owner.instance_name,
                "network_interface_name": owner.network_interface_name,
                "primary_allocation_id": owner.primary_allocation_id,
                "public_allocation_id": owner.public_allocation_id,
                "public_ip": owner.public_ip,
                "present": True,
                "state": "running",
            },
            {
                "aliases": list(target.alias_allocation_ids),
                "boot_disk_id": target.disk_id,
                "compute_id": target.compute_id,
                "compute_revision": target.compute_revision,
                "instance_name": target.instance_name,
                "network_interface_name": target.network_interface_name,
                "primary_allocation_id": target.primary_allocation_id,
                "public_allocation_id": target.public_allocation_id,
                "public_ip": target.public_ip,
                "present": True,
                "state": "running",
            },
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": active.allocation_id,
            "allocation_name": active.allocation_name,
            "owner": {
                "compute_id": owner.compute_id,
                "network_interface_name": owner.network_interface_name,
            },
            "present": True,
        },
    }
    missing_observation = {
        **before_observation,
        "members": [
            before_observation["members"][0],
            {"instance_name": target.instance_name, "present": False},
        ],
    }
    assert active.transaction is not None
    active = replace(
        active,
        transaction=replace(
            active.transaction,
            resource_bindings=tuple(
                sorted(_vm_ha_initial_resource_bindings(before_observation).items())
            ),
        ),
    )
    replacement_disk_name = vm_ha_missing_standby_disk_name(
        gateway_name=active.gateway_name,
        instance_name=target.instance_name,
        predecessor_sha256=active.record_sha256,
        cycle=1,
    )
    replacement_state = VMHALifecycleState.start_missing_standby_replacement(
        active,
        target_instance_name=target.instance_name,
        replacement_cycle=1,
        replacement_disk_name=replacement_disk_name,
        operation_id="e" * 64,
        approval_digest="d" * 64,
        desired_state_digest=active.transaction.desired_state_digest,
        current_state_digest=_canonical_digest(missing_observation),
        current_observation=missing_observation,
    )
    replacement = _VMHAMissingStandbyReplacementPlan(
        target_instance_name=target.instance_name,
        owner_instance_name=owner.instance_name,
        approval_digest="d" * 64,
        operation_id="e" * 64,
        replacement_cycle=1,
        replacement_disk_name=replacement_disk_name,
        retired_compute_id=target.compute_id,
        retired_disk_id=target.disk_id,
        primary_allocation_id=target.primary_allocation_id,
        public_allocation_id=target.public_allocation_id,
        authorization_persisted=True,
    )
    instance = SimpleNamespace(
        vm_ha_generation=SimpleNamespace(
            generation_id="a" * 64,
            digests=SimpleNamespace(
                configuration="a" * 64,
                static_routes="b" * 64,
                bgp_policy="c" * 64,
            ),
        )
    )

    binding = _vm_ha_planned_terminal_runtime_binding(
        replacement_state,
        instance,
        replacement=replacement,
    )

    assert binding.cluster_id == active.cluster_id
    assert binding.shared_allocation_id == active.allocation_id
    assert binding.route_runtime_id == active.route_runtime_id
    with pytest.raises(ValueError, match="persisted missing standby runtime authority"):
        _vm_ha_planned_terminal_runtime_binding(
            replacement_state,
            instance,
            replacement=replace(replacement, approval_digest="e" * 64),
        )


def test_missing_standby_owner_refresh_admits_only_exact_fail_closed_peer_loss() -> None:
    binding = SimpleNamespace(
        shared_allocation_id="shared-private",
        route_runtime_id="route-runtime",
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
    )
    owner = SimpleNamespace(
        hostname="gateway-0",
        vm_ha_node=SimpleNamespace(node_id="node-a"),
    )
    operation_id = "d" * 64
    approval_digest = "e" * 64
    replacement = SimpleNamespace(
        owner_instance_name=owner.hostname,
        target_instance_name="gateway-1",
        replacement_cycle=1,
        operation_id=operation_id,
        approval_digest=approval_digest,
        authorization_persisted=True,
    )
    transaction = SimpleNamespace(
        approval_kind="recovery",
        approval_digest=approval_digest,
        operation_id=operation_id,
        pending_effect=None,
        completed_effects=(),
        observation_guard=None,
        accepted_cloud_operation_effect=None,
        accepted_cloud_operation_id=None,
    )
    lifecycle_state = SimpleNamespace(
        record_version=4,
        status=VMHALifecycleStatus.PROVISIONING,
        transaction=transaction,
    )
    status = {
        "state": "blocked",
        "reasons": ["controller-step-failed"],
        "data_plane_mode": "blocked",
        "promotion_ready": False,
        "observed_owner_node_id": "node-a",
        "ownership_epoch": "7",
        "apply_locked": False,
        "pending_operation_id": None,
        "transfer_inhibition_operation_id": None,
        "controller_capabilities": [],
        "route_reconciliation": {
            "owner_node_id": "node-a",
            "allocation_id": binding.shared_allocation_id,
            "route_runtime_id": binding.route_runtime_id,
            "generation_id": binding.generation_id,
            "digests": {
                "configuration": binding.configuration_digest,
                "static_routes": binding.static_routes_digest,
                "bgp_policy": binding.bgp_policy_digest,
            },
            "operation_id": "route-operation-a",
            "ownership_epoch": "7",
            "ownership_incarnation": 0,
        },
    }

    with (
        patch(
            "nebius_vpngw.cli._vm_ha_planned_terminal_runtime_binding",
            return_value=binding,
        ),
        patch("nebius_vpngw.cli._fetch_vm_ha_agent_status", return_value=status),
    ):
        assert _vm_ha_missing_standby_owner_refresh_required(
            replacement=replacement,
            planned_instances=[owner],
            existing_members={owner.hostname: "203.0.113.10"},
            lifecycle_state=lifecycle_state,
            vm_spec={},
            management_key_path=None,
            ssh_policy=SimpleNamespace(),
        )

    for unsafe in (
        {**status, "reasons": ["route-ledger-identity-not-exact"]},
        {**status, "pending_operation_id": "controller-effect"},
        {**status, "route_reconciliation": None},
    ):
        with (
            patch(
                "nebius_vpngw.cli._vm_ha_planned_terminal_runtime_binding",
                return_value=binding,
            ),
            patch("nebius_vpngw.cli._fetch_vm_ha_agent_status", return_value=unsafe),
            pytest.raises(RuntimeError, match="not serving exactly"),
        ):
            _vm_ha_missing_standby_owner_refresh_required(
                replacement=replacement,
                planned_instances=[owner],
                existing_members={owner.hostname: "203.0.113.10"},
                lifecycle_state=lifecycle_state,
                vm_spec={},
                management_key_path=None,
                ssh_policy=SimpleNamespace(),
            )

    inhibition_effect = "install-standby-replacement-inhibition-node-a"
    inhibited_lifecycle = SimpleNamespace(
        record_version=4,
        status=VMHALifecycleStatus.PROVISIONING,
        transaction=SimpleNamespace(
            **{
                **vars(transaction),
                "pending_effect": inhibition_effect,
            }
        ),
    )
    guarded_after_refresh = {
        **status,
        "reasons": ["current-boot-guard-not-active"],
        "apply_operation_id": None,
        "controller_capabilities": [
            "vm-ha-live-peer-replacement-v4",
            "vm-ha-standby-replacement-inhibition-v1",
        ],
    }
    with (
        patch(
            "nebius_vpngw.cli._vm_ha_planned_terminal_runtime_binding",
            return_value=binding,
        ),
        patch(
            "nebius_vpngw.cli._fetch_vm_ha_agent_status",
            return_value=guarded_after_refresh,
        ),
    ):
        assert _vm_ha_missing_standby_owner_refresh_required(
            replacement=replacement,
            planned_instances=[owner],
            existing_members={owner.hostname: "203.0.113.10"},
            lifecycle_state=inhibited_lifecycle,
            vm_spec={},
            management_key_path=None,
            ssh_policy=SimpleNamespace(),
        )

    for unsafe in (
        {**guarded_after_refresh, "pending_operation_id": "controller-effect"},
        {**guarded_after_refresh, "route_reconciliation": None},
    ):
        with (
            patch(
                "nebius_vpngw.cli._vm_ha_planned_terminal_runtime_binding",
                return_value=binding,
            ),
            patch("nebius_vpngw.cli._fetch_vm_ha_agent_status", return_value=unsafe),
            pytest.raises(RuntimeError, match="not serving exactly"),
        ):
            _vm_ha_missing_standby_owner_refresh_required(
                replacement=replacement,
                planned_instances=[owner],
                existing_members={owner.hostname: "203.0.113.10"},
                lifecycle_state=inhibited_lifecycle,
                vm_spec={},
                management_key_path=None,
                ssh_policy=SimpleNamespace(),
            )

    inhibited = {
        **status,
        "state": "blocked",
        "reasons": ["checkpointed-action-prerequisites-changed"],
        "data_plane_mode": "passive",
        "apply_operation_id": None,
        "pending_operation_id": None,
        "transfer_inhibition_operation_id": operation_id,
        "transfer_inhibition_quiescent": True,
        "controller_capabilities": ["vm-ha-live-peer-replacement-v2"],
    }
    with (
        patch(
            "nebius_vpngw.cli._vm_ha_planned_terminal_runtime_binding",
            return_value=binding,
        ),
        patch("nebius_vpngw.cli._fetch_vm_ha_agent_status", return_value=inhibited),
    ):
        assert _vm_ha_missing_standby_owner_refresh_required(
            replacement=replacement,
            planned_instances=[owner],
            existing_members={owner.hostname: "203.0.113.10"},
            lifecycle_state=inhibited_lifecycle,
            vm_spec={},
            management_key_path=None,
            ssh_policy=SimpleNamespace(),
        )

    for reason in (
        "candidate-dataplane-requires-owner-only-preparation",
        "owner-routes-require-reconciliation",
        "exact-owner-ready-to-enable-forwarding",
        "replaying-checkpointed-action",
    ):
        promoting = {
            **inhibited,
            "state": "promoting",
            "reasons": [reason],
            "pending_operation_id": "controller-owner-local-effect",
            "transfer_inhibition_quiescent": False,
        }
        with (
            patch(
                "nebius_vpngw.cli._vm_ha_planned_terminal_runtime_binding",
                return_value=binding,
            ),
            patch("nebius_vpngw.cli._fetch_vm_ha_agent_status", return_value=promoting),
        ):
            assert _vm_ha_missing_standby_owner_refresh_required(
                replacement=replacement,
                planned_instances=[owner],
                existing_members={owner.hostname: "203.0.113.10"},
                lifecycle_state=inhibited_lifecycle,
                vm_spec={},
                management_key_path=None,
                ssh_policy=SimpleNamespace(),
            )

    replaying = {
        **promoting,
        "reasons": ["replaying-checkpointed-action"],
    }
    with (
        patch(
            "nebius_vpngw.cli._vm_ha_planned_terminal_runtime_binding",
            return_value=binding,
        ),
        patch("nebius_vpngw.cli._fetch_vm_ha_agent_status", return_value=replaying),
    ):
        assert _vm_ha_missing_standby_owner_refresh_required(
            replacement=replacement,
            planned_instances=[owner],
            existing_members={owner.hostname: "203.0.113.10"},
            lifecycle_state=inhibited_lifecycle,
            vm_spec={},
            management_key_path=None,
            ssh_policy=SimpleNamespace(),
        )

    current = {
        **inhibited,
        "controller_capabilities": [
            "vm-ha-live-peer-replacement-v4",
            "vm-ha-standby-replacement-inhibition-v2",
        ],
    }
    with (
        patch(
            "nebius_vpngw.cli._vm_ha_planned_terminal_runtime_binding",
            return_value=binding,
        ),
        patch("nebius_vpngw.cli._fetch_vm_ha_agent_status", return_value=current),
    ):
        assert not _vm_ha_missing_standby_owner_refresh_required(
            replacement=replacement,
            planned_instances=[owner],
            existing_members={owner.hostname: "203.0.113.10"},
            lifecycle_state=inhibited_lifecycle,
            vm_spec={},
            management_key_path=None,
            ssh_policy=SimpleNamespace(),
        )

    for unsafe in (
        {**inhibited, "transfer_inhibition_operation_id": "f" * 64},
        {**inhibited, "transfer_inhibition_quiescent": False},
        {**inhibited, "route_reconciliation": None},
    ):
        with (
            patch(
                "nebius_vpngw.cli._vm_ha_planned_terminal_runtime_binding",
                return_value=binding,
            ),
            patch("nebius_vpngw.cli._fetch_vm_ha_agent_status", return_value=unsafe),
            pytest.raises(RuntimeError, match="not serving exactly"),
        ):
            _vm_ha_missing_standby_owner_refresh_required(
                replacement=replacement,
                planned_instances=[owner],
                existing_members={owner.hostname: "203.0.113.10"},
                lifecycle_state=inhibited_lifecycle,
                vm_spec={},
                management_key_path=None,
                ssh_policy=SimpleNamespace(),
            )


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
                "state": "running",
            },
            {
                "aliases": [],
                "compute_id": "compute-1",
                "compute_revision": "22",
                "instance_name": "nebius-vpn-gw-1",
                "network_interface_name": "eth0",
                "present": True,
                "state": "running",
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

    canonical_absence = {
        **observation,
        "members": [
            observation["members"][0],
            {"instance_name": "nebius-vpn-gw-1", "present": False},
        ],
    }
    absent_authority = _vm_ha_cloud_authority(state, canonical_absence)
    assert absent_authority.unavailable_member_node_ids == ("node-passive",)

    omitted_authority = _vm_ha_cloud_authority(
        state,
        {**observation, "members": [observation["members"][0]]},
    )
    assert omitted_authority.unavailable_member_node_ids == ()
    assert "cloud-member-identity-conflict" in omitted_authority.reasons

    malformed_member_authority = _vm_ha_cloud_authority(
        state,
        {
            **observation,
            "members": [
                observation["members"][0],
                {"instance_name": "nebius-vpn-gw-1", "present": "false"},
            ],
        },
    )
    assert malformed_member_authority.unavailable_member_node_ids == ()
    assert "cloud-member-state-malformed" in malformed_member_authority.reasons

    revision_changed = {
        **observation,
        "members": [
            {**observation["members"][0], "compute_revision": "23"},
            observation["members"][1],
        ],
    }
    revised_authority = _vm_ha_cloud_authority(state, revision_changed)
    assert revised_authority.condition == "exact"
    assert revised_authority.observation_digest != authority.observation_digest

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
    transfer_inhibition_operation_id: str | None = None,
    transfer_inhibition_quiescent: bool = False,
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
        "controller_capabilities": ["vm-ha-standby-restoration-v2"],
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
        "transfer_inhibition_operation_id": transfer_inhibition_operation_id,
        "transfer_inhibition_quiescent": transfer_inhibition_quiescent,
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
        "auto_healing": {
            "state": "enabled",
            "peer_agrees": True,
            "accepted_start": False,
        },
        "runtime_identity": {
            "state": "verified",
            "reason": "identity-verified",
        },
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
        rearm_command="nebius-vpngw vm-ha --local-config-file fixture.yaml",
    )

    assert view.overall == "HEALTHY"
    assert len(view.member_rows) == 2
    assert tuple(row[1] for row in view.member_rows) == expected_roles
    assert next(row for row in view.summary_rows if row[0] == "Action")[1] == "none"
    assert next(row for row in view.summary_rows if row[0] == "Identity")[1] == "verified"
    assert next(row for row in view.summary_rows if row[0] == "Auto-healing")[1] == "enabled"
    rendered = repr((view.summary_rows, view.member_rows))
    assert "node-active" not in rendered
    assert "node-passive" not in rendered
    assert "shared-private" not in rendered


def test_vm_ha_status_view_reports_disabled_standby_restoration_as_maintenance() -> None:
    rearm_command = "nebius-vpngw vm-ha --local-config-file nebius-gcp-classic-vpn.config.yaml"
    members = list(_vm_ha_view_members())
    for index, member in enumerate(members):
        record = dict(member.record or {})
        record["auto_healing"] = {
            "state": "disabled",
            "peer_agrees": True,
            "accepted_start": False,
        }
        if member.node_id == "node-active":
            record["rearm_phase"] = "inhibited"
            record["rearm_reason"] = "standby-auto-healing-policy-disabled"
        members[index] = replace(member, record=record)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        t.cast(tuple[_VMHAMemberEvidence, _VMHAMemberEvidence], tuple(members)),
        rearm_command=rearm_command,
    )

    assert view.overall == "MAINTENANCE"
    assert next(row for row in view.summary_rows if row[0] == "Redundancy")[1] == "maintenance"
    assert next(row for row in view.summary_rows if row[0] == "Auto-healing")[1] == "disabled"
    assert next(row for row in view.summary_rows if row[0] == "Action")[1] == (
        "nebius-vpngw vm-ha --local-config-file "
        "nebius-gcp-classic-vpn.config.yaml --standby-auto-healing enabled"
    )


def test_vm_ha_status_view_keeps_maintenance_when_disabled_standby_is_stopped() -> None:
    members = list(_vm_ha_view_members())
    owner_record = dict(members[0].record or {})
    owner_record["auto_healing"] = {
        "state": "disabled",
        "peer_agrees": True,
        "accepted_start": False,
    }
    owner_record["rearm_phase"] = "inhibited"
    owner_record["rearm_reason"] = "standby-auto-healing-policy-disabled"
    members[0] = replace(members[0], record=owner_record)
    members[1] = replace(
        members[1],
        condition="unknown",
        reason="agent-status-unavailable",
        record=None,
    )

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        t.cast(tuple[_VMHAMemberEvidence, _VMHAMemberEvidence], tuple(members)),
        rearm_command="nebius-vpngw vm-ha --local-config-file fixture.yaml",
    )

    assert view.overall == "MAINTENANCE"
    assert view.reasons == ("standby-auto-healing-policy-disabled",)


def test_vm_ha_status_view_blocks_split_auto_healing_policy() -> None:
    members = list(_vm_ha_view_members())
    passive_record = dict(members[1].record or {})
    passive_record["auto_healing"] = {
        "state": "disabled",
        "peer_agrees": True,
        "accepted_start": False,
    }
    members[1] = replace(members[1], record=passive_record)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        t.cast(tuple[_VMHAMemberEvidence, _VMHAMemberEvidence], tuple(members)),
        rearm_command="unused",
    )

    assert view.overall == "BLOCKED"
    assert "standby-auto-healing-policy-invalid" in view.reasons
    assert next(row for row in view.summary_rows if row[0] == "Auto-healing")[1] == "blocked"


@pytest.mark.parametrize("committed_state", ("enabled", "disabled"))
def test_vm_ha_status_view_blocks_committed_policy_without_peer_agreement(
    committed_state: str,
) -> None:
    members = list(_vm_ha_view_members())
    for index, member in enumerate(members):
        record = dict(member.record or {})
        record["auto_healing"] = {
            "state": committed_state,
            "peer_agrees": False,
            "accepted_start": False,
        }
        members[index] = replace(member, record=record)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        t.cast(tuple[_VMHAMemberEvidence, _VMHAMemberEvidence], tuple(members)),
        rearm_command="unused",
    )

    assert view.overall == "BLOCKED"
    assert "standby-auto-healing-policy-invalid" in view.reasons
    assert next(row for row in view.summary_rows if row[0] == "Auto-healing")[1] == "blocked"
    assert "--standby-auto-healing enabled" not in view.action


@pytest.mark.parametrize(
    ("policy_state", "accepted_start"),
    (("transitioning", False), ("enabled", True)),
)
def test_vm_ha_status_view_reports_auto_healing_transition(
    policy_state: str,
    accepted_start: bool,
) -> None:
    members = list(_vm_ha_view_members())
    for index, member in enumerate(members):
        record = dict(member.record or {})
        record["auto_healing"] = {
            "state": policy_state,
            "peer_agrees": policy_state == "enabled",
            "accepted_start": accepted_start,
        }
        members[index] = replace(member, record=record)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        t.cast(tuple[_VMHAMemberEvidence, _VMHAMemberEvidence], tuple(members)),
        rearm_command="unused",
    )

    assert view.overall == "TRANSITIONING"
    assert next(row for row in view.summary_rows if row[0] == "Auto-healing")[1] == (
        "transitioning"
    )
    assert view.action == "wait"


def test_vm_ha_status_view_reports_unknown_auto_healing_without_member_evidence() -> None:
    members = tuple(
        replace(member, condition="unknown", reason="agent-status-unavailable", record=None)
        for member in _vm_ha_view_members()
    )

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        t.cast(tuple[_VMHAMemberEvidence, _VMHAMemberEvidence], members),
        rearm_command="unused",
    )

    assert view.overall == "UNKNOWN"
    assert next(row for row in view.summary_rows if row[0] == "Auto-healing")[1] == "unknown"
    assert "--standby-auto-healing enabled" not in view.action


def test_vm_ha_local_config_command_shell_quotes_special_paths() -> None:
    config_path = Path("config dir/gateway's[maintenance].yaml")

    command = _vm_ha_local_config_command("nebius-vpngw vm-ha", config_path)

    assert command == ("nebius-vpngw vm-ha --local-config-file " + shlex.quote(str(config_path)))


def _vm_ha_rotating_view_members(
    operation_id: str,
) -> tuple[_VMHAMemberEvidence, _VMHAMemberEvidence]:
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
                "apply_locked": False,
                "apply_operation_id": None,
                "transfer_inhibition_operation_id": operation_id,
                "transfer_inhibition_quiescent": True,
                "state": "blocked" if member.node_id == "node-passive" else "active",
                "reasons": (["mtls-rotation-active"] if member.node_id == "node-passive" else []),
                "standby_ready": False,
                "standby_readiness_reasons": (
                    ["mtls-rotation-active"] if member.node_id == "node-passive" else []
                ),
                "mtls": mtls,
            }
        )
        rotated.append(replace(member, record=record))
    return t.cast(tuple[_VMHAMemberEvidence, _VMHAMemberEvidence], tuple(rotated))


def test_vm_ha_status_view_reports_resumable_managed_mtls_rotation() -> None:
    operation_id = "9" * 64

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        _vm_ha_rotating_view_members(operation_id),
        rearm_command="nebius-vpngw vm-ha --local-config-file fixture.yaml",
        mtls_command="nebius-vpngw vm-ha --rotate-mtls --local-config-file fixture.yaml",
    )

    assert view.overall == "TRANSITIONING"
    assert next(row for row in view.summary_rows if row[0] == "mTLS")[1] == "rotating"
    assert next(row for row in view.summary_rows if row[0] == "Action")[1].startswith(
        "nebius-vpngw vm-ha --rotate-mtls"
    )


@pytest.mark.parametrize("passive_only", [True, False])
def test_vm_ha_status_view_reports_inhibition_only_rotation_recovery(
    passive_only: bool,
) -> None:
    operation_id = "9" * 64
    members = list(_vm_ha_rotating_view_members(operation_id))
    for index, member in enumerate(members):
        assert member.record is not None
        record = dict(member.record)
        mtls = dict(record["mtls"])
        if passive_only and index == 0:
            record.update(
                transfer_inhibition_operation_id=None,
                transfer_inhibition_quiescent=False,
            )
            mtls.update(
                state="healthy",
                operation_id=None,
                operation_kind=None,
                phase=None,
                inhibited=False,
                inhibition_operation_id=None,
            )
        else:
            mtls.update(
                state="healthy",
                operation_id=None,
                operation_kind=None,
                phase=None,
            )
        record["mtls"] = mtls
        members[index] = replace(member, record=record)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        t.cast(tuple[_VMHAMemberEvidence, _VMHAMemberEvidence], tuple(members)),
        rearm_command="nebius-vpngw vm-ha --local-config-file fixture.yaml",
        mtls_command="nebius-vpngw vm-ha --rotate-mtls --local-config-file fixture.yaml",
    )

    assert view.overall == "TRANSITIONING"
    assert next(row for row in view.summary_rows if row[0] == "mTLS")[1] == "rotating"
    assert next(row for row in view.summary_rows if row[0] == "Action")[1].startswith(
        "nebius-vpngw vm-ha --rotate-mtls"
    )


def test_vm_ha_status_view_blocks_rotation_and_apply_lock_coexistence() -> None:
    operation_id = "9" * 64
    members = list(_vm_ha_rotating_view_members(operation_id))
    owner = dict(members[0].record or {})
    owner.update(apply_locked=True, apply_operation_id=operation_id)
    members[0] = replace(members[0], record=owner)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        t.cast(tuple[_VMHAMemberEvidence, _VMHAMemberEvidence], tuple(members)),
        rearm_command="unused",
    )

    assert view.overall == "BLOCKED"
    assert "managed-mtls-transaction-conflict" in repr(view.summary_rows)


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


def test_vm_ha_status_view_observes_active_nonowner_safety_fencing() -> None:
    members = list(_vm_ha_view_members())
    standby = dict(members[1].record or {})
    standby.update(
        data_plane_mode="active",
        promotion_ready=False,
        standby_ready=False,
    )
    members[1] = replace(members[1], record=standby)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        (members[0], members[1]),
        rearm_command="unused",
    )

    assert view.overall == "TRANSITIONING"
    assert view.action == "wait"
    assert "nonowner-forwarding" in view.reasons


def test_vm_ha_status_view_accepts_exact_blocked_disable_active_action() -> None:
    members = list(_vm_ha_view_members())
    standby = dict(members[1].record or {})
    standby.update(
        state="blocked",
        data_plane_mode="active",
        promotion_ready=False,
        standby_ready=False,
        reasons=["active-node-lacks-exact-allocation-ownership"],
        pending_operation_id="boot-a:4:disable-active:node-passive",
    )
    members[1] = replace(members[1], record=standby)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        (members[0], members[1]),
        rearm_command="unused",
    )

    assert view.overall == "TRANSITIONING"
    assert "unexpected-controller-operation" not in view.reasons
    assert "active-node-lacks-exact-allocation-ownership" not in view.reasons


def test_vm_ha_status_view_rejects_wrong_member_disable_active_target() -> None:
    members = list(_vm_ha_view_members())
    standby = dict(members[1].record or {})
    standby.update(
        state="blocked",
        data_plane_mode="active",
        promotion_ready=False,
        standby_ready=False,
        reasons=["active-node-lacks-exact-allocation-ownership"],
        pending_operation_id="boot-a:4:disable-active:node-active",
    )
    members[1] = replace(members[1], record=standby)

    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-0", "node-active", None, ()),
        (members[0], members[1]),
        rearm_command="unused",
    )

    assert view.overall == "BLOCKED"
    assert "unexpected-controller-operation" in view.reasons


def test_vm_ha_status_renderer_emits_one_four_column_table() -> None:
    view = _vm_ha_status_view(
        _VMHACloudAuthority("active", "exact", "nebius-vpn-gw-1", "node-passive", None, ()),
        _vm_ha_view_members(owner_node_id="node-passive"),
        rearm_command="nebius-vpngw vm-ha --local-config-file fixture.yaml",
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
    summary_labels = ("Redundancy", "Identity", "Auto-healing", "Action")
    assert all(label in rendered for label in summary_labels)
    assert [rendered.index(label) for label in summary_labels] == sorted(
        rendered.index(label) for label in summary_labels
    )
    assert "Rearm" not in rendered
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

    maintenance_command = (
        _vm_ha_local_config_command(
            "nebius-vpngw vm-ha",
            Path("config dir/nebius-gcp-classic-vpn's[maintenance].yaml"),
        )
        + " --standby-auto-healing enabled"
    )
    sanitized = replace(
        view,
        overall="MAINTENANCE",
        summary_rows=(
            ("Redundancy", "not ready", "standby-not-ready"),
            ("Identity", "blocked", "runtime identity proof is blocked"),
            ("Auto-healing", "disabled", "automatic standby restoration is disabled"),
            (
                "Action",
                maintenance_command,
                "re-enable automatic standby restoration after maintenance",
            ),
        ),
    )
    sanitized_output = io.StringIO()
    _render_vm_ha_status(
        Console(file=sanitized_output, color_system=None, width=240),
        sanitized,
    )
    assert maintenance_command in sanitized_output.getvalue()

    narrow_output = io.StringIO()
    _render_vm_ha_status(
        Console(file=narrow_output, color_system=None, width=60),
        sanitized,
    )
    assert f"Action  {maintenance_command}" in narrow_output.getvalue()

    private_config_path = Path("config dir/customer's-private[transition].yaml")
    nonmaintenance_command = _vm_ha_local_config_command(
        "nebius-vpngw vm-ha --rotate-mtls",
        private_config_path,
    )
    nonmaintenance = replace(
        sanitized,
        overall="TRANSITIONING",
        summary_rows=tuple(
            (
                label,
                nonmaintenance_command if label == "Action" else value,
                detail,
            )
            for label, value, detail in sanitized.summary_rows
        ),
    )
    nonmaintenance_output = io.StringIO()
    _render_vm_ha_status(
        Console(file=nonmaintenance_output, color_system=None, width=240),
        nonmaintenance,
    )
    redacted_command = "nebius-vpngw vm-ha --rotate-mtls --local-config-file <file>"
    assert redacted_command in nonmaintenance_output.getvalue()
    assert str(private_config_path) not in nonmaintenance_output.getvalue()
    assert "customer" not in nonmaintenance_output.getvalue()

    narrow_nonmaintenance_output = io.StringIO()
    _render_vm_ha_status(
        Console(file=narrow_nonmaintenance_output, color_system=None, width=60),
        nonmaintenance,
    )
    assert f"Action  {redacted_command}" in narrow_nonmaintenance_output.getvalue()
    assert "customer" not in narrow_nonmaintenance_output.getvalue()


def test_vm_ha_status_renderer_colors_only_semantic_health_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    maintenance = replace(
        healthy,
        overall="MAINTENANCE",
        summary_rows=(
            (
                "Redundancy",
                "maintenance",
                "standby restoration is intentionally disabled",
            ),
            (
                "Identity",
                "verified",
                "both current-boot runtime identities verified",
            ),
            (
                "Auto-healing",
                "disabled",
                "automatic standby restoration is disabled for maintenance",
            ),
            ("Action", "none", ""),
        ),
    )
    rendered_tables: list[t.Any] = []
    capture_console = SimpleNamespace(print=lambda table, **_kwargs: rendered_tables.append(table))

    _render_vm_ha_status(capture_console, healthy)
    _render_vm_ha_status(capture_console, blocked)
    _render_vm_ha_status(capture_console, maintenance)

    (
        healthy_table,
        healthy_summary,
        _healthy_action,
        blocked_table,
        blocked_summary,
        _blocked_action,
        maintenance_table,
        maintenance_summary,
        _maintenance_action,
    ) = rendered_tables
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
    assert [column.header for column in healthy_summary.columns] == ["Field", "Value", "Details"]
    assert [column.header for column in blocked_summary.columns] == ["Field", "Value", "Details"]
    assert str(maintenance_table.title.spans[-1].style) == "bold yellow"
    assert [str(cell.style) for cell in maintenance_summary.columns[1]._cells] == [
        "red",
        "white",
        "red",
    ]

    monkeypatch.delenv("NO_COLOR", raising=False)
    color_output = io.StringIO()
    _render_vm_ha_status(
        Console(
            file=color_output,
            color_system="standard",
            force_terminal=True,
            width=240,
        ),
        maintenance,
    )
    rendered_color = color_output.getvalue()
    assert "\x1b[31mmaintenance\x1b[0m" in rendered_color
    assert "\x1b[31mdisabled" in rendered_color
    assert "\x1b[31mverified" not in rendered_color


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
        "Uptime",
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


def test_service_probe_recovery_suppresses_only_its_buffered_transient_detail() -> None:
    rows = {"gateway-1": {"frr": "[red]error[/red]"}}
    details = {("gateway-1", "frr"): "transient startup output"}

    assert not _mark_service_probe_recovered(
        service_rows_by_host=rows,
        failed_service_details=details,
        hostname="gateway-1",
        service_name="frr",
        returncode=1,
        stdout="inactive\n",
    )
    assert rows["gateway-1"]["frr"] == "[red]error[/red]"
    assert details == {("gateway-1", "frr"): "transient startup output"}

    assert _mark_service_probe_recovered(
        service_rows_by_host=rows,
        failed_service_details=details,
        hostname="gateway-1",
        service_name="frr",
        returncode=0,
        stdout="active\n",
    )
    assert rows["gateway-1"]["frr"] == "[green]active[/green]"
    assert details == {}


def test_gateway_uptime_is_mode_neutral_and_prefers_bgp_session_evidence() -> None:
    assert (
        _gateway_tunnel_uptime(
            bgp_peer_ip=None,
            bgp_uptime={},
            ipsec_uptime="0:05:19:40",
        )
        == "0:05:19:40"
    )
    assert (
        _gateway_tunnel_uptime(
            bgp_peer_ip="169.254.0.2",
            bgp_uptime={"169.254.0.2": "1d02h03m"},
            ipsec_uptime="0:05:19:40",
        )
        == "1d02h03m"
    )
    assert (
        _gateway_tunnel_uptime(
            bgp_peer_ip="169.254.0.2",
            bgp_uptime={},
            ipsec_uptime="0:05:19:40",
        )
        == "0:05:19:40"
    )


@pytest.mark.parametrize(
    "token",
    ["never", "n/a", "unknown", "idle", "malformed", "1hgarbage"],
)
def test_unavailable_bgp_uptime_preserves_ipsec_fallback(token: str) -> None:
    parsed = _parse_bgp_uptime(token)

    assert parsed is None
    assert (
        _gateway_tunnel_uptime(
            bgp_peer_ip="169.254.0.2",
            bgp_uptime={} if parsed is None else {"169.254.0.2": parsed},
            ipsec_uptime="0:05:19:40",
        )
        == "0:05:19:40"
    )


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("93784", "1:02:03:04"),
        ("26:03:04", "1:02:03:04"),
        ("1d2h3m4s", "1:02:03:04"),
    ],
)
def test_bgp_uptime_normalizes_supported_frr_formats(token: str, expected: str) -> None:
    assert _parse_bgp_uptime(token) == expected


def test_status_preflight_accepts_one_present_vm_ha_member(
    tmp_path: Path,
    capsys,
) -> None:
    class StatusCollectionReached(RuntimeError):
        pass

    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    plan = _gateway_discovery_plan(
        "nebius-vpn-gw-0",
        "nebius-vpn-gw-1",
        vm_ha=object(),
    )
    manager = _ContextManagedNamespace(
        _get_client=lambda: object(),
        get_vm_public_ip=Mock(side_effect=StatusCollectionReached),
    )
    service = Mock()
    service.get_by_name.side_effect = [
        SimpleNamespace(wait=Mock(side_effect=_gateway_discovery_error(StatusCode.NOT_FOUND))),
        _configured_gateway_response("nebius-vpn-gw-1"),
    ]

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch(
            "nebius_vpngw.cli._load_config_with_region_override",
            return_value={"project_id": "project-test"},
        ),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value=None),
        patch("nebius_vpngw.cli.VMManager", return_value=manager),
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=service,
        ),
        pytest.raises(StatusCollectionReached),
    ):
        status(local_config_file=config_path, project_id=None, region=None)

    output = capsys.readouterr().out
    assert "Collecting gateway VM status..." in output
    service.list.assert_not_called()


def test_status_preflight_preserves_all_absent_success_exit(
    tmp_path: Path,
    capsys,
) -> None:
    config_path = tmp_path / "gateway.config.yaml"
    plan = _gateway_discovery_plan("nebius-vpn-gw-0", "nebius-vpn-gw-1")
    manager = _ContextManagedNamespace(_get_client=lambda: object())
    service = Mock()
    service.get_by_name.side_effect = [
        SimpleNamespace(wait=Mock(side_effect=_gateway_discovery_error(StatusCode.NOT_FOUND))),
        SimpleNamespace(wait=Mock(side_effect=_gateway_discovery_error(StatusCode.NOT_FOUND))),
    ]

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch(
            "nebius_vpngw.cli._load_config_with_region_override",
            return_value={"project_id": "project-test"},
        ),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value=None),
        patch("nebius_vpngw.cli.VMManager", return_value=manager),
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=service,
        ),
        pytest.raises(typer.Exit) as raised,
    ):
        status(local_config_file=config_path, project_id=None, region=None)

    output = capsys.readouterr().out
    assert raised.value.exit_code == 0
    assert "No configured gateway VMs found." in output
    assert "nebius-vpngw apply" in output


def test_status_preflight_sanitizes_query_failure(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "gateway.config.yaml"
    plan = _gateway_discovery_plan("nebius-vpn-gw-0")
    manager = _ContextManagedNamespace(_get_client=lambda: object())
    service = Mock()
    service.get_by_name.return_value = SimpleNamespace(
        wait=Mock(side_effect=RuntimeError("PRIVATE_PROVIDER_DETAIL"))
    )

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch(
            "nebius_vpngw.cli._load_config_with_region_override",
            return_value={"project_id": "project-test"},
        ),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value=None),
        patch("nebius_vpngw.cli.VMManager", return_value=manager),
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=service,
        ),
        pytest.raises(typer.Exit) as raised,
    ):
        status(local_config_file=config_path, project_id=None, region=None)

    output = capsys.readouterr().out
    assert raised.value.exit_code == 1
    assert "Unable to query configured gateway VMs." in output
    assert "PRIVATE_PROVIDER_DETAIL" not in output
    assert "Traceback" not in output


def test_status_zero_sa_rows_preserve_all_configured_tunnel_identity() -> None:
    table = _vpn_gateway_status_table()

    _add_configured_no_active_tunnel_rows(
        table,
        "gateway-0",
        {
            "gateway-0": {
                "tunnel-active": "active",
                "tunnel-passive": "passive",
                "tunnel-disabled": "disable",
            }
        },
        {
            "gateway-0": {
                "tunnel-active": "192.0.2.10",
                "tunnel-passive": "192.0.2.11",
                "tunnel-disabled": "192.0.2.12",
            }
        },
    )

    rows = list(zip(*(column._cells for column in table.columns), strict=True))
    assert [[str(cell) for cell in row] for row in rows] == [
        [
            "tunnel-active",
            "[green]active[/green]",
            "gateway-0",
            "[yellow]NONE[/yellow]",
            "-",
            "192.0.2.10",
            "-",
            "-",
        ],
        [
            "tunnel-passive",
            "[yellow]passive[/yellow]",
            "gateway-0",
            "[yellow]NONE[/yellow]",
            "-",
            "192.0.2.11",
            "-",
            "-",
        ],
        [
            "tunnel-disabled",
            "[red]disabled[/red]",
            "gateway-0",
            "[yellow]NONE[/yellow]",
            "-",
            "192.0.2.12",
            "-",
            "-",
        ],
    ]


def test_status_zero_sa_rows_distinguish_a_vm_without_configured_tunnels() -> None:
    table = _vpn_gateway_status_table()

    _add_configured_no_active_tunnel_rows(table, "gateway-0", {}, {})

    rows = list(zip(*(column._cells for column in table.columns), strict=True))
    assert [[str(cell) for cell in row] for row in rows] == [
        [
            "No configured tunnels",
            "-",
            "gateway-0",
            "[yellow]NONE[/yellow]",
            "-",
            "-",
            "-",
            "-",
        ]
    ]


@pytest.mark.parametrize(
    ("remote_command", "output", "expected"),
    (
        ("sudo swanctl --list-sas", "", False),
        ("sudo swanctl --list-sas", "no active SAs\n", False),
        ("sudo swanctl --list-sas", "tunnel-a: malformed\n", False),
        (
            "sudo swanctl --list-sas",
            "tunnel-a: #1, CONNECTING, IKEv2\n",
            False,
        ),
        (
            "sudo swanctl --list-sas",
            "tunnel-a: #1, ESTABLISHED, IKEv2\n",
            True,
        ),
        (
            "sudo ipsec statusall",
            "tunnel-a[1]: ESTABLISHED 8 minutes ago, 10.0.0.1[10.0.0.1]...192.0.2.10[192.0.2.10]\n",
            True,
        ),
        ("sudo ipsec statusall", "Security Associations (0 up, 0 connecting):\nnone\n", False),
    ),
)
def test_status_retry_clears_stale_error_only_with_established_sa_evidence(
    remote_command: str,
    output: str,
    expected: bool,
) -> None:
    assert _tunnel_probe_retry_has_established_sa(["ssh", remote_command], output) is expected


@pytest.mark.parametrize("preferred_swanctl", (True, False))
def test_status_partial_runtime_observation_keeps_every_configured_tunnel(
    tmp_path: Path,
    preferred_swanctl: bool,
) -> None:
    config_path = tmp_path / "gateway.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    instance = SimpleNamespace(hostname="gateway-0", external_ip="192.0.2.100")
    plan = SimpleNamespace(
        gateway_group=SimpleNamespace(name="gateway", region="eu-test1"),
        vm_ha=None,
        iter_instance_configs=lambda: (instance,),
    )
    local_cfg = {
        "defaults": {"routing": {"mode": "static"}},
        "gateway_group": {"routing_mode": "static", "vm_spec": {}},
        "connections": [
            {
                "name": "peer-a",
                "routing_mode": "static",
                "tunnels": [
                    {
                        "name": "tunnel-active",
                        "gateway_instance_index": 0,
                        "remote_public_ip": "192.0.2.10",
                        "ha_role": "active",
                    },
                    {
                        "name": "tunnel-passive",
                        "gateway_instance_index": 0,
                        "remote_public_ip": "192.0.2.11",
                        "ha_role": "passive",
                    },
                ],
            }
        ],
    }
    table = _vpn_gateway_status_table()
    commands: list[str] = []

    def run_command(command: list[str], **_kwargs: t.Any) -> SimpleNamespace:
        remote_command = str(command[-1])
        commands.append(remote_command)
        if "swanctl --list-sas" in remote_command:
            if not preferred_swanctl:
                return SimpleNamespace(returncode=1, stdout="", stderr="unavailable")
            return SimpleNamespace(
                returncode=0,
                stdout="tunnel-active: #1, ESTABLISHED, IKEv2\nruntime-only: #2, CONNECTING, IKEv2\n",
                stderr="",
            )
        if "ipsec statusall" in remote_command:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "tunnel-active[1]: ESTABLISHED 8 minutes ago, "
                    "10.0.0.1[10.0.0.1]...192.0.2.10[192.0.2.10]\n"
                    "runtime-only[2]: CONNECTING 1 minute ago, "
                    "10.0.0.1[10.0.0.1]...192.0.2.99[192.0.2.99]\n"
                ),
                stderr="",
            )
        if "systemctl is-active" in remote_command or "pgrep -x charon" in remote_command:
            return SimpleNamespace(returncode=0, stdout="active\n", stderr="")
        if "table_220_rule" in remote_command:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "table_220": False,
                        "broad_apipa": False,
                        "orphaned_count": 0,
                        "status": "healthy",
                    }
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected command")

    manager = _ContextManagedNamespace(
        _get_client=lambda: None,
        get_vm_public_ip=lambda _hostname: "192.0.2.100",
    )
    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value=None),
        patch("nebius_vpngw.cli.VMManager", return_value=manager),
        patch("nebius_vpngw.cli._vpn_gateway_status_table", return_value=table),
        patch("subprocess.run", side_effect=run_command),
    ):
        status(local_config_file=config_path, project_id=None, region=None)

    rows = [
        [str(cell) for cell in row]
        for row in zip(*(column._cells for column in table.columns), strict=True)
    ]
    assert all(len(row) == 8 for row in rows)
    assert [row[0] for row in rows] == [
        "tunnel-active",
        "tunnel-passive",
        "runtime-only",
    ]
    assert rows[0][1:4] == [
        "[green]active[/green]",
        "gateway-0",
        "[green]Established[/green]",
    ]
    assert rows[1] == [
        "tunnel-passive",
        "[yellow]passive[/yellow]",
        "gateway-0",
        "[yellow]NONE[/yellow]",
        "-",
        "192.0.2.11",
        "-",
        "-",
    ]
    assert rows[2][1:4] == ["-", "gateway-0", "[yellow]Connecting[/yellow]"]
    assert ("sudo ipsec statusall" in commands) is not preferred_swanctl


def test_status_skips_all_vm_ha_work_for_non_ha_plan(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.config.yaml"
    plan = SimpleNamespace(
        gateway_group=SimpleNamespace(name="gateway", region="eu-test1"),
        vm_ha=None,
        iter_instance_configs=lambda: (),
    )
    manager = _ContextManagedNamespace(_get_client=lambda: None)

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
        status(local_config_file=config_path, project_id=None, region=None)

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
    manager = _ContextManagedNamespace(_get_client=lambda: None)
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
        status(local_config_file=config_path, project_id=None, region=None)

    assert loaded_configs[0]["connections"][0]["tunnels"][0]["psk"] == f"${{{variable}}}"


def test_status_passes_one_explicit_region_to_plan_and_manager(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["region_id"] = "eu-east1"
    sample_config["gateway_group"]["region"] = "eu-west1"
    config_path = tmp_path / "status-region.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    plan = SimpleNamespace(
        gateway_group=SimpleNamespace(name="gateway", region="eu-north1"),
        vm_ha=None,
        iter_instance_configs=lambda: (),
    )
    manager_kwargs: list[dict[str, t.Any]] = []

    def merge(config: dict[str, t.Any], peer_files: list[Path]) -> SimpleNamespace:
        assert peer_files == []
        assert config["region_id"] == "eu-north1"
        assert config["gateway_group"]["region"] == "eu-north1"
        return plan

    def manager_factory(*_args, **kwargs):
        manager_kwargs.append(kwargs)
        return _ContextManagedNamespace(_get_client=lambda: None)

    with (
        patch("nebius_vpngw.cli.merge_with_peer_configs", side_effect=merge),
        patch("nebius_vpngw.cli._ensure_authentication", return_value=None),
        patch("nebius_vpngw.cli.VMManager", side_effect=manager_factory),
    ):
        status(local_config_file=config_path, project_id=None, region="eu-north1")

    assert manager_kwargs[0]["region"] == "eu-north1"
    assert manager_kwargs[0]["region_id"] == "eu-north1"
    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert persisted["region_id"] == "eu-east1"
    assert persisted["gateway_group"]["region"] == "eu-west1"


def test_destroy_passes_one_explicit_region_without_persisting_it(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["region_id"] = "eu-east1"
    sample_config["gateway_group"]["region"] = "eu-west1"
    config_path = tmp_path / "destroy-region.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    manager_kwargs: list[dict[str, t.Any]] = []

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            manager_kwargs.append(kwargs)

    with (
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
    ):
        result = CliRunner().invoke(
            app,
            [
                "destroy",
                "--local-config-file",
                str(config_path),
                "--region",
                "eu-north1",
            ],
            input="n\n",
        )

    assert result.exit_code == 0, result.output
    assert manager_kwargs[0]["region"] == "eu-north1"
    assert manager_kwargs[0]["region_id"] == "eu-north1"
    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert persisted["region_id"] == "eu-east1"
    assert persisted["gateway_group"]["region"] == "eu-west1"


def test_destroy_yes_executes_verified_coordinator_without_prompt(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    config_path = tmp_path / "destroy-yes.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    with (
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch(
            "nebius_vpngw.cli.VMManager",
            side_effect=lambda *_args, **_kwargs: _ContextManagedFake(),
        ),
        patch(
            "nebius_vpngw.cli.execute_destroy",
            return_value=SimpleNamespace(
                already_absent=False,
                deleted_compute=1,
                deleted_disks=1,
                deleted_routes=1,
                deleted_allocations=1,
            ),
        ) as execute,
    ):
        result = CliRunner().invoke(
            app,
            [
                "destroy",
                "--local-config-file",
                str(config_path),
                "--yes",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "Proceed with destruction?" not in result.output
    assert "Destroy completed and verified successfully" in result.output
    execute.assert_called_once()


def test_destroy_sanitizes_unexpected_provider_failure(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    config_path = tmp_path / "destroy-failure.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    with (
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch(
            "nebius_vpngw.cli.VMManager",
            side_effect=lambda *_args, **_kwargs: _ContextManagedFake(),
        ),
        patch(
            "nebius_vpngw.cli.execute_destroy",
            side_effect=Exception("provider detail must not escape"),
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "destroy",
                "--local-config-file",
                str(config_path),
                "--yes",
            ],
        )

    assert result.exit_code == 1
    assert "Destroy failed safely" in result.output
    assert "Reason: destroy-operation-failed" in result.output
    assert "rerun this exact destroy command" in result.output
    assert "provider detail must not escape" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("missing_pin", "cloud_missing", "expected_overall", "expected_fetches"),
    (
        (None, False, "HEALTHY", 4),
        ("nebius-vpn-gw-1", False, "DEGRADED", 2),
        (None, True, "BLOCKED", 2),
    ),
)
def test_status_collects_read_only_vm_ha_evidence_and_renders_one_view(
    tmp_path: Path,
    missing_pin: str | None,
    cloud_missing: bool,
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
            external_ip="192.0.2.11" if cloud_missing else None,
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
                "state": "running",
            },
            {
                "instance_name": "nebius-vpn-gw-1",
                "present": not cloud_missing,
                **(
                    {}
                    if cloud_missing
                    else {
                        "aliases": [],
                        "compute_id": "compute-1",
                        "network_interface_name": "eth0",
                        "state": "running",
                    }
                ),
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

    class ReadOnlyManager(_ContextManagedFake):
        def _get_client(self) -> None:
            return None

        def get_vm_public_ip(self, hostname: str) -> str | None:
            if cloud_missing and hostname == "nebius-vpn-gw-1":
                return None
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
        patch.dict(
            os.environ,
            {"VPNGW_SSH_KNOWN_HOSTS_FILE": str(tmp_path / "known_hosts")},
        ),
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
        status(local_config_file=config_path, project_id=None, region=None)

    assert observation_calls == [
        (gateway_group, ["10.0.0.0/8"]),
        (gateway_group, ["10.0.0.0/8"]),
    ]
    assert require_policy.call_count == 2
    assert build_ssh.call_count > 0
    available_policies = {
        hostname: policy for hostname, policy in policies.items() if hostname != missing_pin
    }
    probed_policies = {
        hostname: policy
        for hostname, policy in available_policies.items()
        if not cloud_missing or hostname != "nebius-vpn-gw-1"
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
        for hostname, policy in probed_policies.items()
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
    if cloud_missing:
        missing_row = next(row for row in rendered[0].member_rows if row[0] == "nebius-vpn-gw-1")
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
        rearm_command="nebius-vpngw vm-ha --local-config-file fixture.yaml",
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


@pytest.mark.parametrize(
    "reason",
    (
        "automatic-retry-exhausted",
        "compute-start-permanent-failure",
        "compute-start-retry-scheduled",
        "standby-readiness-timeout",
        "standby-auto-healing-recovery-authority-stale-or-foreign",
        "standby-auto-healing-recovery-completion-failed",
        "standby-auto-healing-recovery-consume-failed",
        "standby-auto-healing-recovery-invalid",
        "standby-auto-healing-recovery-policy-changed",
        "standby-auto-healing-recovery-policy-unavailable",
        "standby-restoration-authorization-invalid",
        "standby-restoration-authority-stale-or-foreign",
        "standby-restoration-blocked",
        "standby-restoration-not-committed",
        "standby-restoration-policy-changed",
        "standby-restoration-policy-unavailable",
        "standby-restoration-start-identity-changed",
    ),
)
def test_vm_ha_status_preserves_closed_restoration_reason_codes(reason: str) -> None:
    assert _safe_vm_ha_reason(reason) == reason


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


def test_apply_authentication_preserves_explicit_ambient_token_provenance(
    monkeypatch,
) -> None:
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "explicit-operator-token")

    assert _apply_operator_auth_token() == "explicit-operator-token"
    assert os.environ["NEBIUS_IAM_TOKEN"] == "explicit-operator-token"


def test_apply_authentication_uses_renewable_cli_profile_without_exporting_token(
    monkeypatch,
) -> None:
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)

    assert _apply_operator_auth_token() is None
    assert "NEBIUS_IAM_TOKEN" not in os.environ


def test_requested_vm_ha_service_account_must_match_runtime_identity(monkeypatch) -> None:
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(
        vpngw_sa,
        "ensure_vm_ha_service_account_identity_and_token",
        lambda **_kwargs: vpngw_sa.ServiceAccountTokenIdentity(
            token="short-lived-token",
            service_account_id="service-account-other",
            service_account_name="gateway-runtime",
        ),
    )

    with pytest.raises(typer.Exit):
        _requested_apply_service_account_token(
            sa_name="gateway-runtime",
            tenant_id=None,
            project_id="project-test",
            region_id=None,
            vm_ha_enabled=True,
            expected_service_account_id="service-account-runtime",
        )


def test_apply_rejects_service_account_override_for_vm_ha(tmp_path: Path) -> None:
    config_path = tmp_path / "vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    plan = SimpleNamespace(
        vm_ha=object(),
        gateway_group=SimpleNamespace(name="gateway-a", region="eu-north1"),
        validate=lambda: None,
        iter_instance_configs=lambda: iter(()),
    )
    with (
        patch(
            "nebius_vpngw.cli.load_local_config",
            return_value={"project_id": "project-a", "gateway_group": {"vm_spec": {}}},
        ),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli.inspect_managed_vm_ha_credentials") as inspect_credentials,
    ):
        result = CliRunner().invoke(
            app,
            ["apply", "--local-config-file", str(config_path), "--sa", "custom-sa"],
        )

    assert result.exit_code == 1
    assert "--sa is supported only" in result.stdout
    assert "ordinary gateways" in result.stdout
    inspect_credentials.assert_not_called()


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


def test_vm_ha_operator_command_suppresses_remote_failure_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _manual_failback_plan()
    local_cfg = {"gateway_group": {"vm_spec": {}}}
    sensitive_marker = "remote-sensitive-payload-must-not-escape"
    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _: local_cfg)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", lambda *_, **__: None)
    monkeypatch.setattr(
        "nebius_vpngw.cli.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=f"Traceback: request failed with sensitive={sensitive_marker}",
        ),
    )

    with pytest.raises(RuntimeError) as failure:
        _run_vm_ha_operator_command(
            local_config_file=Path("config.yaml"),
            agent_flag="--vm-ha-status",
        )

    assert str(failure.value) == (
        "VM-HA remote agent action failed; run status and inspect VM-HA service journals"
    )
    assert sensitive_marker not in str(failure.value)
    assert "Traceback" not in str(failure.value)


def _manual_failback_plan() -> SimpleNamespace:
    generation = SimpleNamespace(
        generation_id="a" * 64,
        digests=SimpleNamespace(
            configuration="a" * 64,
            static_routes="b" * 64,
            bgp_policy="c" * 64,
        ),
    )
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
    *,
    state: InstanceCloudState,
    alias_present: bool,
    revision: str,
    allocation_id: str = "shared-private",
) -> SimpleNamespace:
    return SimpleNamespace(
        state=state,
        resource_version=revision,
        has_alias_allocation=lambda nic, allocation: (
            alias_present if (nic, allocation) == ("eth0", allocation_id) else False
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

    class FakeManager(_ContextManagedFake):
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

    class FakeManager(_ContextManagedFake):
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
                "node_id": "node-active",
                "state": "active",
                "promotion_ready": True,
                "redundancy_ready": True,
                "data_plane_mode": "active",
                "observed_owner_node_id": "node-active",
                "apply_locked": False,
                "pending_operation_id": None,
            },
            {
                "node_id": "node-passive",
                "state": "normal",
                "standby_ready": True,
                "standby_readiness_reasons": [],
                "data_plane_mode": "passive",
                "observed_owner_node_id": "node-active",
                "apply_locked": False,
                "pending_operation_id": None,
            },
        ]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_operator_command", operator)

    _prepare_vm_ha_manual_failback_target(local_config_file=Path("config.yaml"))

    assert starts == []
    assert waits == ["nebius-vpn-gw-0", "nebius-vpn-gw-1"]
    assert operator_calls == [("--vm-ha-status", None)]


def test_manual_failback_rearms_stopped_standby_when_active_already_owns(
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
    observations = iter((stopped, running, running, running))

    class FakeManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def _get_client(self) -> object:
            return object()

        def wait_for_vm_ha_member_ssh(self, name: str, *_args, **_kwargs) -> None:
            waits.append(name)

    cloud = SimpleNamespace(
        get_instance=lambda *_: None,
        stop_instance=lambda *_: None,
        get_allocation=lambda *_: None,
        set_alias_allocation=lambda *_: None,
    )
    waits: list[str] = []
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
    monkeypatch.setattr("nebius_vpngw.cli.NebiusSDKCloudClient", lambda _sdk, **_kw: cloud)
    monkeypatch.setattr(
        "nebius_vpngw.cli.VMHACloudAdapter",
        lambda **_kw: SimpleNamespace(observe_cluster=lambda **kwargs: next(observations)),
    )
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)
    operator_calls: list[tuple[str, str | None]] = []

    def operator(**kwargs):
        operator_calls.append((kwargs["agent_flag"], kwargs.get("configured_role")))
        if kwargs["agent_flag"] == "--vm-ha-rearm-request":
            return [{"schema": "nebius-vpngw/vm-ha-rearm-request-v1"}]
        standby = {
            "node_id": "node-passive",
            "state": "normal",
            "standby_ready": True,
            "standby_readiness_reasons": [],
            "data_plane_mode": "passive",
            "observed_owner_node_id": "node-active",
            "apply_locked": False,
            "pending_operation_id": None,
        }
        if kwargs.get("configured_role") == "passive":
            return [standby]
        return [
            {
                "node_id": "node-active",
                "state": "active",
                "promotion_ready": True,
                "data_plane_mode": "active",
                "observed_owner_node_id": "node-active",
                "apply_locked": False,
                "pending_operation_id": None,
            },
            standby,
        ]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_operator_command", operator)

    _prepare_vm_ha_manual_failback_target(local_config_file=Path("config.yaml"))

    assert waits == ["nebius-vpn-gw-1"]
    assert operator_calls == [
        ("--vm-ha-rearm-request", "active"),
        ("--vm-ha-status", "passive"),
        ("--vm-ha-status", "passive"),
        ("--vm-ha-status", None),
    ]


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

    class FakeManager(_ContextManagedFake):
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

    class FakeManager(_ContextManagedFake):
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

    class FakeManager(_ContextManagedFake):
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

    class FakeManager(_ContextManagedFake):
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

    class FakeManager(_ContextManagedFake):
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
    "progress_sequence",
    (
        (True,),
        (False, True),
        (False,),
    ),
)
def test_vm_ha_rearm_retry_race_resumes_only_with_exact_consumed_progress(
    monkeypatch: pytest.MonkeyPatch,
    progress_sequence: tuple[bool, ...],
) -> None:
    lifecycle = _lifecycle_state()
    plan = _manual_failback_plan()
    active_owner = AllocationOwner("compute-0", "eth0")

    def observation(state: InstanceCloudState, revision: str):
        return SimpleNamespace(
            allocation=SimpleNamespace(owner=active_owner),
            former=_manual_failback_compute(
                state=InstanceCloudState.RUNNING,
                alias_present=True,
                revision="21",
            ),
            candidate=_manual_failback_compute(
                state=state,
                alias_present=False,
                revision=revision,
            ),
        )

    observations = iter(
        (
            observation(InstanceCloudState.STOPPED, "22"),
            observation(InstanceCloudState.STOPPED, "22"),
            observation(InstanceCloudState.STOPPED, "22"),
            observation(InstanceCloudState.RUNNING, "23"),
            observation(InstanceCloudState.RUNNING, "23"),
        )
    )
    waits: list[str] = []

    class LifecycleStore:
        def __init__(self, _path: Path) -> None:
            pass

        def read(self, **_kwargs):
            return lifecycle

    class FakeManager(_ContextManagedFake):
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
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)
    operator_calls: list[tuple[str, str | None]] = []

    def operator(**kwargs):
        operator_calls.append((kwargs["agent_flag"], kwargs.get("configured_role")))
        if kwargs["agent_flag"] == "--vm-ha-rearm-request":
            raise RuntimeError("rearm writer busy")
        if kwargs.get("configured_role") == "active":
            return [
                {
                    "state": "active",
                    "promotion_ready": True,
                    "data_plane_mode": "active",
                    "observed_owner_node_id": "node-active",
                    "apply_locked": False,
                    "pending_operation_id": None,
                    "rearm_phase": "starting",
                    "rearm_reason": None,
                }
            ]
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
    progress_values = iter(progress_sequence)
    exact_progress = Mock(side_effect=lambda: next(progress_values, progress_sequence[-1]))

    if not any(progress_sequence):
        monotonic = Mock(side_effect=(0.0, 0.0, 300.0))
        monkeypatch.setattr("nebius_vpngw.cli.time.monotonic", monotonic)
        with pytest.raises(RuntimeError, match="rearm writer busy"):
            _prepare_vm_ha_planned_target(
                local_config_file=Path("config.yaml"),
                target_role=None,
                before_rearm_request=lambda *_args: None,
                rearm_request_progress_is_exact=exact_progress,
            )
        exact_progress.assert_called_once_with()
        assert operator_calls == [("--vm-ha-rearm-request", "active")]
        assert waits == []
        return

    prepared = _prepare_vm_ha_planned_target(
        local_config_file=Path("config.yaml"),
        target_role=None,
        before_rearm_request=lambda *_args: None,
        rearm_request_progress_is_exact=exact_progress,
    )

    assert prepared.outcome == "standby-ready"
    assert prepared.terminal_context is not None
    assert (
        prepared.terminal_context.request_timeout_seconds,
        prepared.terminal_context.cutover_timeout_seconds,
        prepared.terminal_context.restoration_timeout_seconds,
    ) == (300.0, 600.0, 300.0)
    assert exact_progress.call_count == (3 if progress_sequence[0] is False else 2)
    assert operator_calls == [
        ("--vm-ha-rearm-request", "active"),
        ("--vm-ha-status", "active"),
        ("--vm-ha-status", "active"),
        ("--vm-ha-status", "passive"),
        ("--vm-ha-status", "passive"),
    ]
    assert waits == ["nebius-vpn-gw-1"]


@pytest.mark.parametrize(
    ("command", "target_role"),
    (("failback", "active"), ("failover", "passive")),
)
@pytest.mark.parametrize("output_format", (None, "text", "json"))
def test_repeated_planned_transfer_is_a_request_free_noop_in_selected_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    target_role: str,
    output_format: str | None,
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

    args = [command, "vm", "--local-config-file", str(config_path)]
    if output_format is not None:
        args.extend(("--output-format", output_format))
    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.output
    operation_name = "Failback" if command == "failback" else "Failover"
    if output_format == "json":
        assert json.loads(unstyle(result.stdout)) == {
            "outcome": "already-owner",
            "request_submitted": False,
            "schema": "nebius-vpngw/vm-ha-planned-transfer-result-v1",
            "target_role": target_role,
        }
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert result.stderr == (
            f"{operation_name} not needed: the {target_role} VM already owns the gateway.\n"
        )
    help_result = CliRunner().invoke(app, [command, "vm", "--help"])
    help_output = unstyle(help_result.stdout)
    assert help_result.exit_code == 0
    assert "no-op" in help_output
    assert "--output-format" in help_output
    assert "text" in help_output
    assert "json" in help_output


@pytest.mark.parametrize(
    ("command", "target_role", "agent_flag", "configured_role"),
    (
        ("failback", "active", "--vm-ha-manual-failback", "active"),
        ("failover", "passive", "--vm-ha-manual-failover", "passive"),
    ),
)
@pytest.mark.parametrize("output_format", (None, "text", "json"))
def test_nested_vm_transfer_waits_and_uses_selected_output_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    target_role: str,
    agent_flag: str,
    configured_role: str,
    output_format: str | None,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("project_id: project-test\n", encoding="utf-8")
    preparation_calls: list[str] = []
    operator_calls: list[tuple[str, str | None]] = []
    wait_calls: list[tuple[str, str]] = []
    request_record = {
        "schema": f"nebius-vpngw/vm-ha-manual-{command}-v1",
        "cluster_id": "cluster-a",
        "node_id": f"node-{target_role}",
        "generation_id": "a" * 64,
        "requested_at": 10.0,
    }

    def prepare(**kwargs):
        preparation_calls.append(kwargs["target_role"])
        return SimpleNamespace(
            outcome="standby-ready",
            record={},
            terminal_context=SimpleNamespace(
                target_role=kwargs["target_role"],
                request_timeout_seconds=300.0,
            ),
        )

    def operator(**kwargs):
        operator_calls.append((kwargs["agent_flag"], kwargs.get("configured_role")))
        return [request_record]

    monkeypatch.setattr("nebius_vpngw.cli._prepare_vm_ha_planned_target", prepare)
    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_operator_command", operator)
    monkeypatch.setattr(
        "nebius_vpngw.cli._wait_for_vm_ha_planned_transfer",
        lambda **kwargs: (
            wait_calls.append((kwargs["operation_name"], kwargs["request_fingerprint"]))
            or SimpleNamespace(cutover_seconds=8.1, total_seconds=12.3)
        ),
    )

    args = [command, "vm", "--local-config-file", str(config_path)]
    if output_format is not None:
        args.extend(("--output-format", output_format))
    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.output
    assert preparation_calls == [target_role]
    assert operator_calls == [(agent_flag, configured_role)]
    assert wait_calls == [
        (
            "Failback" if command == "failback" else "Failover",
            planned_request_fingerprint(request_record),
        )
    ]
    if output_format == "json":
        assert json.loads(result.stdout) == request_record
    else:
        assert result.stdout == ""
    expected_stderr = (
        "Failing back to the active VM...\n"
        "Failback to the active VM is done successfully in 12.3s.\n"
        if command == "failback"
        else "Failing over to the passive VM...\n"
        "Failover to the passive VM is done successfully in 12.3s.\n"
    )
    assert result.stderr == expected_stderr


@pytest.mark.parametrize("output_format", ("text", "json"))
def test_planned_transfer_failure_uses_selected_output_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("project_id: project-test\n", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_vpngw.cli._prepare_vm_ha_planned_target",
        lambda **_kwargs: SimpleNamespace(
            outcome="standby-ready",
            record={},
            terminal_context=SimpleNamespace(
                target_role="active",
                request_timeout_seconds=300.0,
            ),
        ),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_operator_command",
        lambda **_kwargs: [
            {
                "schema": "nebius-vpngw/vm-ha-manual-failback-v1",
                "cluster_id": "cluster-a",
                "node_id": "node-active",
                "generation_id": "a" * 64,
                "requested_at": 10.0,
            }
        ],
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._wait_for_vm_ha_planned_transfer",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("terminal cloud ownership evidence drifted")
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "failback",
            "vm",
            "--local-config-file",
            str(config_path),
            "--output-format",
            output_format,
        ],
    )

    assert result.exit_code == 1
    if output_format == "json":
        assert json.loads(result.stdout) == {
            "schema": "nebius-vpngw/vm-ha-manual-failback-v1",
            "cluster_id": "cluster-a",
            "node_id": "node-active",
            "generation_id": "a" * 64,
            "requested_at": 10.0,
        }
    else:
        assert result.stdout == ""
    assert result.stderr == (
        "Failing back to the active VM...\n"
        "Failback failed: VM-HA operation failed safely; run status and inspect "
        "VM-HA service journals\n"
    )
    assert "successfully" not in result.stderr


@pytest.mark.parametrize("output_format", ("text", "json"))
@pytest.mark.parametrize(
    ("failure", "expected_failure"),
    (
        (
            _VMHAPlannedCutoverVerificationUnavailable(elapsed_seconds=300.0),
            "Failback outcome is not yet verified: terminal cutover observation remained "
            "unavailable. The VM-HA controller may still be completing the transfer; run "
            "'nebius-vpngw status --local-config-file <file>' before retrying and inspect "
            "VM-HA service journals only if status is not healthy.\n",
        ),
        (
            _VMHAPlannedCutoverVerificationIncomplete(
                elapsed_seconds=600.0,
                budget_seconds=600.0,
            ),
            "Failback cutover is not yet verified after 600.0s total: exact ownership "
            "reproof did not stabilize within its 600.0s cutover deadline. The VM-HA "
            "controller may still be completing the transfer; run 'nebius-vpngw status "
            "--local-config-file <file>' before retrying and inspect VM-HA service "
            "journals only if status is not healthy.\n",
        ),
        (
            _VMHAPlannedRestorationVerificationUnavailable(
                cutover_seconds=229.6,
                restoration_seconds=70.4,
                total_seconds=300.0,
            ),
            "Failback cutover succeeded in 229.6s, but standby restoration is not yet "
            "verified after 70.4s (300.0s total): terminal observation remained "
            "unavailable. Automatic background restoration may still be running; run "
            "'nebius-vpngw status --local-config-file <file>' and use 'nebius-vpngw "
            "vm-ha --local-config-file <file>' only if status reports a blocked recovery.\n",
        ),
    ),
)
def test_planned_transfer_observer_loss_reports_unverified_in_selected_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
    failure: RuntimeError,
    expected_failure: str,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("project_id: project-test\n", encoding="utf-8")
    request_record = {
        "schema": "nebius-vpngw/vm-ha-manual-failback-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-active",
        "generation_id": "a" * 64,
        "requested_at": 10.0,
    }
    monkeypatch.setattr(
        "nebius_vpngw.cli._prepare_vm_ha_planned_target",
        lambda **_kwargs: SimpleNamespace(
            outcome="standby-ready",
            record={},
            terminal_context=SimpleNamespace(
                target_role="active",
                request_timeout_seconds=300.0,
            ),
        ),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_operator_command",
        lambda **_kwargs: [request_record],
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._wait_for_vm_ha_planned_transfer",
        lambda **_kwargs: (_ for _ in ()).throw(failure),
    )

    result = CliRunner().invoke(
        app,
        [
            "failback",
            "vm",
            "--local-config-file",
            str(config_path),
            "--output-format",
            output_format,
        ],
    )

    assert result.exit_code == 1
    if output_format == "json":
        assert json.loads(result.stdout) == request_record
    else:
        assert result.stdout == ""
    assert result.stderr == "Failing back to the active VM...\n" + expected_failure
    assert "failed safely" not in result.stderr
    assert "done successfully" not in result.stderr


@pytest.mark.parametrize("output_format", ("text", "json"))
@pytest.mark.parametrize(
    "error_prefix",
    ("provider preparation failed", "Failback provider preparation failed"),
)
def test_planned_transfer_preparation_failure_is_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
    error_prefix: str,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("project_id: project-test\n", encoding="utf-8")
    sensitive_marker = "local-sensitive-payload-must-not-escape"

    def fail_preparation(**_kwargs: object) -> t.NoReturn:
        raise RuntimeError(f"{error_prefix} with sensitive={sensitive_marker}")

    monkeypatch.setattr(
        "nebius_vpngw.cli._prepare_vm_ha_planned_target",
        fail_preparation,
    )

    result = CliRunner().invoke(
        app,
        [
            "failback",
            "vm",
            "--local-config-file",
            str(config_path),
            "--output-format",
            output_format,
        ],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Failback failed: VM-HA operation failed safely; run status and inspect "
        "VM-HA service journals\n"
    )
    assert sensitive_marker not in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("command", ("failback", "failover"))
def test_planned_transfer_rejects_invalid_output_format_before_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("project_id: project-test\n", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_local_config",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid output format must be rejected before config resolution"
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            command,
            "vm",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "yaml",
        ],
    )

    stderr = unstyle(result.stderr)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Invalid value for '--output-format'" in stderr
    assert "text" in stderr
    assert "json" in stderr


def test_planned_transfer_reports_safe_partial_completion_when_rearm_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("project_id: project-test\n", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_vpngw.cli._prepare_vm_ha_planned_target",
        lambda **_kwargs: SimpleNamespace(
            outcome="standby-ready",
            record={},
            terminal_context=SimpleNamespace(
                target_role="passive",
                request_timeout_seconds=300.0,
            ),
        ),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_operator_command",
        lambda **_kwargs: [
            {
                "schema": "nebius-vpngw/vm-ha-manual-failover-v1",
                "cluster_id": "cluster-a",
                "node_id": "node-passive",
                "generation_id": "a" * 64,
                "requested_at": 10.0,
            }
        ],
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._wait_for_vm_ha_planned_transfer",
        lambda **_kwargs: (_ for _ in ()).throw(
            _VMHAPlannedRedundancyRestorationError(
                "compute-start-failed",
                cutover_seconds=41.2,
                restoration_seconds=32.2,
                total_seconds=73.4,
            )
        ),
    )

    result = CliRunner().invoke(
        app,
        ["failover", "vm", "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Failing over to the passive VM...\n"
        "Failover cutover succeeded in 41.2s, but standby restoration failed after "
        "32.2s (73.4s total): compute-start-failed. Run 'nebius-vpngw vm-ha "
        "--local-config-file <file>' to recover the blocked standby restoration.\n"
    )
    assert "done successfully" not in result.stderr


def _planned_transfer_observer_test_context(
    *,
    status_reader: t.Callable[[], dict[str, t.Any]],
    cloud_reader: t.Callable[[], t.Any],
    standby_status_reader: t.Callable[[], dict[str, t.Any]],
    timeout_seconds: float = 2.0,
) -> _VMHAPlannedTerminalContext:
    return _VMHAPlannedTerminalContext(
        target_role="active",
        former_role="passive",
        target_member=SimpleNamespace(node_id="node-active"),
        former_member=SimpleNamespace(node_id="node-passive"),
        target_owner=AllocationOwner("compute-active", "eth0"),
        allocation_id="allocation-a",
        runtime_binding=SimpleNamespace(),
        status_reader=status_reader,
        standby_status_reader=standby_status_reader,
        cloud_reader=cloud_reader,
        request_timeout_seconds=timeout_seconds,
        cutover_timeout_seconds=timeout_seconds,
        restoration_timeout_seconds=timeout_seconds,
    )


def _planned_reproof_test_entry(
    sequence: int,
    action: str,
    state: str,
    *,
    target_node_id: str = "node-passive",
) -> dict[str, t.Any]:
    return {
        "sequence": sequence,
        "action": action,
        "state": state,
        "operation_id": f"boot-a:{sequence}:{action}:{target_node_id}",
        "boot_id": "boot-a",
        "ownership_epoch": "7",
        "recorded_at": float(sequence),
        "error_type": None,
    }


def _planned_reproof_test_progress(
    fingerprint: str,
    history: list[dict[str, t.Any]],
    *,
    target_role: str = "passive",
) -> dict[str, t.Any]:
    former_role = "passive" if target_role == "active" else "active"
    return {
        "schema": "nebius-vpngw/vm-ha-transfer-progress-v1",
        "cluster_id": "cluster-a",
        "candidate_node_id": f"node-{target_role}",
        "former_owner_node_id": f"node-{former_role}",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "b" * 64,
            "static_routes": "c" * 64,
            "bgp_policy": "d" * 64,
        },
        "route_runtime_id": "route-runtime-a",
        "intent": "planned-failback" if target_role == "active" else "planned-failover",
        "request_fingerprint": fingerprint,
        "first_operation_id": f"boot-a:1:stop-former-owner:node-{former_role}",
        "ownership_incarnation": 1,
        "history": history,
    }


def _planned_reproof_test_context(
    *,
    status_reader: t.Callable[[], dict[str, t.Any]],
    cloud_reader: t.Callable[[], t.Any],
    standby_status_reader: t.Callable[[], dict[str, t.Any]],
    target_role: str = "passive",
    cutover_timeout_seconds: float = 600.0,
    restoration_timeout_seconds: float = 300.0,
) -> _VMHAPlannedTerminalContext:
    former_role = "passive" if target_role == "active" else "active"
    return _VMHAPlannedTerminalContext(
        target_role=target_role,
        former_role=former_role,
        target_member=SimpleNamespace(
            node_id=f"node-{target_role}",
            compute_id=f"compute-{target_role}",
            network_interface_name="eth0",
        ),
        former_member=SimpleNamespace(
            node_id=f"node-{former_role}",
            compute_id=f"compute-{former_role}",
            network_interface_name="eth0",
        ),
        target_owner=AllocationOwner(f"compute-{target_role}", "eth0"),
        allocation_id="allocation-a",
        runtime_binding=SimpleNamespace(
            shared_allocation_id="allocation-a",
            generation_id="a" * 64,
            configuration_digest="b" * 64,
            static_routes_digest="c" * 64,
            bgp_policy_digest="d" * 64,
            route_runtime_id="route-runtime-a",
        ),
        status_reader=status_reader,
        standby_status_reader=standby_status_reader,
        cloud_reader=cloud_reader,
        request_timeout_seconds=300.0,
        cutover_timeout_seconds=cutover_timeout_seconds,
        restoration_timeout_seconds=restoration_timeout_seconds,
    )


@pytest.mark.parametrize(
    "error",
    (
        OSError("sensitive local path"),
        subprocess.TimeoutExpired("sensitive command", 5.0),
        _VMHAAgentStatusStale("sensitive stale identity"),
        _VMHARemoteAgentUnavailable("sensitive remote output"),
    ),
)
def test_planned_terminal_agent_reader_closes_retry_safe_failures(
    error: BaseException,
) -> None:
    with pytest.raises(_VMHAPlannedTerminalObservationUnavailable) as failure:
        _read_vm_ha_planned_terminal_agent(
            lambda: (_ for _ in ()).throw(error),
            source="target-agent",
            mismatch_message="wrong cardinality",
        )

    assert failure.value.source == "target-agent"
    assert "sensitive" not in str(failure.value)


@pytest.mark.parametrize(
    "error",
    (
        TimeoutError("sensitive timeout"),
        ConnectionError("sensitive endpoint"),
        RetryableHACloudError("sensitive retry detail"),
        AmbiguousHACloudError("sensitive ambiguous detail"),
    ),
)
def test_planned_terminal_cloud_reader_closes_retry_safe_failures(
    error: BaseException,
) -> None:
    with pytest.raises(_VMHAPlannedTerminalObservationUnavailable) as failure:
        _read_vm_ha_planned_terminal_cloud(lambda: (_ for _ in ()).throw(error))

    assert failure.value.source == "cloud"
    assert "sensitive" not in str(failure.value)


def test_planned_terminal_readers_keep_permanent_and_malformed_failures_immediate() -> None:
    with pytest.raises(_VMHAAgentStatusPermanent, match="foreign identity"):
        _read_vm_ha_planned_terminal_agent(
            lambda: (_ for _ in ()).throw(_VMHAAgentStatusPermanent("foreign identity")),
            source="target-agent",
            mismatch_message="wrong cardinality",
        )
    with pytest.raises(RuntimeError, match="wrong cardinality"):
        _read_vm_ha_planned_terminal_agent(
            lambda: [],
            source="target-agent",
            mismatch_message="wrong cardinality",
        )
    with pytest.raises(PermanentHACloudError, match="invalid resource"):
        _read_vm_ha_planned_terminal_cloud(
            lambda: (_ for _ in ()).throw(PermanentHACloudError("invalid resource"))
        )


def test_planned_reproof_predicate_rejects_foreign_or_unsafe_evidence() -> None:
    request = {
        "schema": "nebius-vpngw/vm-ha-manual-failover-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-passive",
        "generation_id": "a" * 64,
        "requested_at": 10.0,
    }
    fingerprint = planned_request_fingerprint(request)
    initial_progress = _planned_reproof_test_progress(
        fingerprint,
        [_planned_reproof_test_entry(1, "enable-active", "completed")],
    )
    blocked = {
        "state": "blocked",
        "promotion_committed": False,
        "data_plane_mode": "active",
        "apply_locked": False,
        "reasons": ["local-ownership-lacks-establishment-proof"],
        "pending_operation_id": "boot-a:2:disable-active:node-passive",
        "transfer_progress": initial_progress,
    }
    context = _planned_reproof_test_context(
        status_reader=lambda: {},
        cloud_reader=lambda: None,
        standby_status_reader=lambda: {},
    )

    assert _vm_ha_planned_reproof_converging(
        blocked,
        context=context,
        request_fingerprint=fingerprint,
    )
    for rejected in (
        {**blocked, "apply_locked": True},
        {**blocked, "reasons": ["controller-step-failed"]},
        {**blocked, "pending_operation_id": "boot-a:2:disable-active:node-active"},
        {
            **blocked,
            "transfer_progress": {
                **initial_progress,
                "request_fingerprint": "f" * 64,
            },
        },
        {**blocked, "promotion_committed": True},
    ):
        assert not _vm_ha_planned_reproof_converging(
            rejected,
            context=context,
            request_fingerprint=fingerprint,
        )

    detach = _planned_reproof_test_entry(
        2,
        "detach-candidate-for-reproof",
        "attempting",
    )
    detaching = {
        "state": "ownership-transfer",
        "promotion_committed": False,
        "data_plane_mode": "passive",
        "apply_locked": False,
        "reasons": ["candidate-attachment-requires-reproof"],
        "pending_operation_id": detach["operation_id"],
        "transfer_progress": _planned_reproof_test_progress(
            fingerprint,
            [*initial_progress["history"], detach],
        ),
    }
    assert _vm_ha_planned_reproof_converging(
        detaching,
        context=context,
        request_fingerprint=fingerprint,
    )
    assert not _vm_ha_planned_reproof_converging(
        {**detaching, "data_plane_mode": "active"},
        context=context,
        request_fingerprint=fingerprint,
    )


@pytest.mark.parametrize("target_role", ("active", "passive"))
@pytest.mark.parametrize("race", ("cloud", "final-agent"))
def test_planned_transfer_observes_exact_reproof_before_terminal_cutover(
    capsys: pytest.CaptureFixture[str],
    race: str,
    target_role: str,
) -> None:
    operation = "failback" if target_role == "active" else "failover"
    target_node_id = f"node-{target_role}"
    request = {
        "schema": f"nebius-vpngw/vm-ha-manual-{operation}-v1",
        "cluster_id": "cluster-a",
        "node_id": target_node_id,
        "generation_id": "a" * 64,
        "requested_at": 10.0,
    }
    fingerprint = planned_request_fingerprint(request)
    initial_entry = _planned_reproof_test_entry(
        1,
        "enable-active",
        "completed",
        target_node_id=target_node_id,
    )
    initial_progress = _planned_reproof_test_progress(
        fingerprint,
        [initial_entry],
        target_role=target_role,
    )

    def bridge_record(
        *, state: str, data_plane_mode: str, reason: str, pending: str
    ) -> dict[str, t.Any]:
        return {
            "state": state,
            "promotion_committed": False,
            "data_plane_mode": data_plane_mode,
            "apply_locked": False,
            "reasons": [reason],
            "pending_operation_id": pending,
            "transfer_progress": initial_progress,
        }

    reproof_actions = (
        ("detach-candidate-for-reproof", "ownership-transfer"),
        ("attach-candidate", "ownership-transfer"),
        ("confirm-candidate-ownership", "promoting"),
        ("reconcile-routes", "promoting"),
        ("enable-active", "promoting"),
    )
    completed: list[dict[str, t.Any]] = [initial_entry]
    reproof_records: list[dict[str, t.Any]] = []
    for sequence, (action, state) in enumerate(reproof_actions, start=2):
        attempt = _planned_reproof_test_entry(
            sequence,
            action,
            "attempting",
            target_node_id=target_node_id,
        )
        reproof_records.append(
            {
                "state": state,
                "promotion_committed": False,
                "data_plane_mode": "passive",
                "apply_locked": False,
                "reasons": ["candidate-attachment-requires-reproof"],
                "pending_operation_id": attempt["operation_id"],
                "transfer_progress": _planned_reproof_test_progress(
                    fingerprint,
                    [*completed, attempt],
                    target_role=target_role,
                ),
            }
        )
        completed.append({**attempt, "state": "completed"})

    route_receipt = {
        "owner_node_id": target_node_id,
        "allocation_id": "allocation-a",
        "route_runtime_id": "route-runtime-a",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "b" * 64,
            "static_routes": "c" * 64,
            "bgp_policy": "d" * 64,
        },
        "operation_id": "route-operation",
        "ownership_epoch": "7",
        "ownership_incarnation": 1,
    }
    terminal = {
        "promotion_committed": True,
        "state": "active",
        "promotion_ready": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": target_node_id,
        "former_owner_compute_state": "stopped",
        "former_attachment_absent": True,
        "candidate_attachment_exact": True,
        "ownership_re_read_exact": True,
        "apply_locked": False,
        "pending_operation_id": None,
        "guard_boot_id": "boot-a",
        "controller_ready_boot_id": "boot-a",
        "ownership_epoch": "7",
        "route_reconciliation": route_receipt,
    }
    restored = {
        **terminal,
        "former_owner_compute_state": "running",
        "rearm_phase": "running",
        "redundancy_ready": True,
    }
    blocked = bridge_record(
        state="blocked",
        data_plane_mode="active",
        reason="local-ownership-lacks-establishment-proof",
        pending=f"boot-a:2:disable-active:{target_node_id}",
    )
    passive = bridge_record(
        state="normal",
        data_plane_mode="blocked",
        reason="non-owner-must-remain-passive",
        pending=f"boot-a:3:enter-passive:{target_node_id}",
    )
    status_records = iter(
        [
            terminal,
            blocked,
            passive,
            *reproof_records,
            terminal,
            terminal,
            restored,
            restored,
        ]
    )
    matching_cloud = SimpleNamespace(
        allocation=SimpleNamespace(owner=AllocationOwner(f"compute-{target_role}", "eth0")),
        former=_manual_failback_compute(
            state=(
                InstanceCloudState.RUNNING
                if target_role == "active"
                else InstanceCloudState.STOPPED
            ),
            alias_present=target_role == "active",
            revision="42",
            allocation_id="allocation-a",
        ),
        candidate=_manual_failback_compute(
            state=(
                InstanceCloudState.RUNNING
                if target_role == "passive"
                else InstanceCloudState.STOPPED
            ),
            alias_present=target_role == "passive",
            revision="42",
            allocation_id="allocation-a",
        ),
    )
    mismatching_cloud = SimpleNamespace(
        allocation=SimpleNamespace(owner=None),
        former=_manual_failback_compute(
            state=matching_cloud.former.state,
            alias_present=False,
            revision="42",
            allocation_id="allocation-a",
        ),
        candidate=_manual_failback_compute(
            state=matching_cloud.candidate.state,
            alias_present=False,
            revision="42",
            allocation_id="allocation-a",
        ),
    )
    restored_cloud = SimpleNamespace(
        allocation=matching_cloud.allocation,
        former=_manual_failback_compute(
            state=InstanceCloudState.RUNNING,
            alias_present=target_role == "active",
            revision="42",
            allocation_id="allocation-a",
        ),
        candidate=_manual_failback_compute(
            state=InstanceCloudState.RUNNING,
            alias_present=target_role == "passive",
            revision="42",
            allocation_id="allocation-a",
        ),
    )
    cloud_records = iter(
        [
            mismatching_cloud if race == "cloud" else matching_cloud,
            matching_cloud,
            restored_cloud,
            restored_cloud,
        ]
    )
    standby = {
        "state": "normal",
        "standby_ready": True,
        "standby_readiness_reasons": [],
        "data_plane_mode": "passive",
        "observed_owner_node_id": target_node_id,
        "apply_locked": False,
        "pending_operation_id": None,
    }
    context = _planned_reproof_test_context(
        status_reader=lambda: next(status_records),
        cloud_reader=lambda: next(cloud_records),
        standby_status_reader=lambda: standby,
        target_role=target_role,
    )
    now = [0.0]

    completion = _wait_for_vm_ha_planned_transfer(
        context=context,
        operation_name=operation.title(),
        started_at=-350.0,
        request_fingerprint=fingerprint,
        clock=lambda: now[0],
        sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    assert completion.cutover_seconds == 357.0
    assert completion.restoration_seconds == 1.0
    assert completion.total_seconds == 358.0
    progress_output = capsys.readouterr().err
    assert "unassigning shared IP for ownership reproof" in progress_output
    assert progress_output.count("cutover completed") == 1
    assert "failed safely" not in progress_output


def test_planned_transfer_reproof_timeout_is_fixed_and_cannot_admit_late_proof() -> None:
    request = {
        "schema": "nebius-vpngw/vm-ha-manual-failover-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-passive",
        "generation_id": "a" * 64,
        "requested_at": 10.0,
    }
    fingerprint = planned_request_fingerprint(request)
    progress = _planned_reproof_test_progress(
        fingerprint,
        [_planned_reproof_test_entry(1, "enable-active", "completed")],
    )
    now = [0.0]
    status_reads = 0

    def status_reader() -> dict[str, t.Any]:
        nonlocal status_reads
        status_reads += 1
        return {
            "state": "blocked",
            "promotion_committed": False,
            "data_plane_mode": "active",
            "apply_locked": False,
            "reasons": ["local-ownership-lacks-establishment-proof"],
            "pending_operation_id": "boot-a:2:disable-active:node-passive",
            "transfer_progress": progress,
        }

    context = _planned_reproof_test_context(
        status_reader=status_reader,
        cloud_reader=lambda: pytest.fail("deadline must precede terminal cloud proof"),
        standby_status_reader=lambda: pytest.fail("deadline must precede standby proof"),
        cutover_timeout_seconds=2.0,
    )

    with pytest.raises(_VMHAPlannedCutoverVerificationIncomplete) as failure:
        _wait_for_vm_ha_planned_transfer(
            context=context,
            operation_name="Failover",
            started_at=-58.0,
            request_fingerprint=fingerprint,
            clock=lambda: now[0],
            sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )

    assert failure.value.elapsed_seconds == 60.0
    assert failure.value.budget_seconds == 2.0
    assert status_reads == 2


@pytest.mark.parametrize(
    ("reader_name", "failure_call"),
    (
        ("status", 1),
        ("status", 2),
        ("status", 3),
        ("cloud", 1),
        ("cloud", 2),
        ("cloud", 3),
        ("standby", 1),
        ("standby", 2),
    ),
)
def test_planned_transfer_retries_each_terminal_observer_within_phase_deadline(
    monkeypatch: pytest.MonkeyPatch,
    reader_name: str,
    failure_call: int,
) -> None:
    now = [0.0]
    calls = {"status": 0, "cloud": 0, "standby": 0}

    def read(name: str) -> dict[str, t.Any]:
        calls[name] += 1
        if name == reader_name and calls[name] == failure_call:
            source = "cloud" if name == "cloud" else f"{name}-agent"
            if source == "status-agent":
                source = "target-agent"
            raise _VMHAPlannedTerminalObservationUnavailable(t.cast(t.Any, source))
        return {}

    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_cutover_status_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_cutover_cloud_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_owner_redundancy_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_restored_cloud_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_standby_matches",
        lambda *_args, **_kwargs: True,
    )
    context = _planned_transfer_observer_test_context(
        status_reader=lambda: read("status"),
        cloud_reader=lambda: read("cloud"),
        standby_status_reader=lambda: read("standby"),
    )

    completion = _wait_for_vm_ha_planned_transfer(
        context=context,
        operation_name="Failback",
        started_at=0.0,
        clock=lambda: now[0],
        sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
        poll_seconds=0.1,
    )

    assert completion.total_seconds < (
        context.cutover_timeout_seconds + context.restoration_timeout_seconds
    )
    assert calls[reader_name] > failure_call


@pytest.mark.parametrize(
    ("reader_name", "failure_call", "expected_error"),
    (
        ("status", 1, _VMHAPlannedCutoverVerificationUnavailable),
        ("status", 2, _VMHAPlannedCutoverVerificationUnavailable),
        ("cloud", 1, _VMHAPlannedCutoverVerificationUnavailable),
        ("status", 3, _VMHAPlannedRestorationVerificationUnavailable),
        ("cloud", 2, _VMHAPlannedRestorationVerificationUnavailable),
        ("cloud", 3, _VMHAPlannedRestorationVerificationUnavailable),
        ("standby", 1, _VMHAPlannedRestorationVerificationUnavailable),
        ("standby", 2, _VMHAPlannedRestorationVerificationUnavailable),
    ),
)
def test_planned_transfer_persistent_loss_at_each_terminal_read_is_unverified(
    monkeypatch: pytest.MonkeyPatch,
    reader_name: str,
    failure_call: int,
    expected_error: type[RuntimeError],
) -> None:
    now = [0.0]
    calls = {"status": 0, "cloud": 0, "standby": 0}

    def read(name: str) -> dict[str, t.Any]:
        calls[name] += 1
        if name == reader_name and calls[name] >= failure_call:
            source = "cloud" if name == "cloud" else f"{name}-agent"
            if source == "status-agent":
                source = "target-agent"
            raise _VMHAPlannedTerminalObservationUnavailable(t.cast(t.Any, source))
        return {}

    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_cutover_status_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_cutover_cloud_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_owner_redundancy_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_restored_cloud_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_standby_matches",
        lambda *_args, **_kwargs: True,
    )
    context = _planned_transfer_observer_test_context(
        timeout_seconds=1.0,
        status_reader=lambda: read("status"),
        cloud_reader=lambda: read("cloud"),
        standby_status_reader=lambda: read("standby"),
    )

    with pytest.raises(expected_error) as failure:
        _wait_for_vm_ha_planned_transfer(
            context=context,
            operation_name="Failback",
            started_at=0.0,
            clock=lambda: now[0],
            sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
            poll_seconds=0.25,
        )

    if isinstance(failure.value, _VMHAPlannedCutoverVerificationUnavailable):
        assert failure.value.elapsed_seconds == 1.0
    else:
        assert isinstance(
            failure.value,
            _VMHAPlannedRestorationVerificationUnavailable,
        )
        assert failure.value.cutover_seconds == 0.0
        assert failure.value.restoration_seconds == 1.0
        assert failure.value.total_seconds == 1.0


def test_planned_transfer_late_terminal_read_cannot_authorize_cutover(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = [0.0]
    status_calls = 0

    def status_reader() -> dict[str, t.Any]:
        nonlocal status_calls
        status_calls += 1
        if status_calls == 2:
            now[0] = 1.1
        return {}

    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_cutover_status_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_cutover_cloud_matches",
        lambda *_args, **_kwargs: True,
    )
    context = _planned_transfer_observer_test_context(
        timeout_seconds=1.0,
        status_reader=status_reader,
        cloud_reader=lambda: {},
        standby_status_reader=lambda: {},
    )

    with pytest.raises(RuntimeError, match="did not complete within 1.1s"):
        _wait_for_vm_ha_planned_transfer(
            context=context,
            operation_name="Failback",
            started_at=0.0,
            clock=lambda: now[0],
            sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )

    assert "cutover completed" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("target_role", "operation_name", "expected_progress"),
    (
        (
            "active",
            "Failback",
            "Failback in progress: 5.0s elapsed, cutting over...\n"
            "Failback cutover completed in 6.0s; restoring standby redundancy...\n"
            "Failback in progress: 8.0s elapsed, starting former owner as standby...\n"
            "Failback in progress: 12.0s elapsed, waiting for standby readiness...\n",
        ),
        (
            "passive",
            "Failover",
            "Failover in progress: 5.0s elapsed, cutting over...\n"
            "Failover cutover completed in 6.0s; restoring standby redundancy...\n"
            "Failover in progress: 8.0s elapsed, starting former owner as standby...\n"
            "Failover in progress: 12.0s elapsed, waiting for standby readiness...\n",
        ),
    ),
)
def test_planned_transfer_waits_for_committed_status_and_independent_cloud(
    capsys: pytest.CaptureFixture[str],
    target_role: str,
    operation_name: str,
    expected_progress: str,
) -> None:
    lifecycle = _lifecycle_state()
    members = {member.role: member for member in lifecycle.members}
    target = members[target_role]
    former_role = "passive" if target_role == "active" else "active"
    former = members[former_role]
    runtime_binding = SimpleNamespace(
        shared_allocation_id=lifecycle.allocation_id,
        route_runtime_id=lifecycle.route_runtime_id,
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
    )
    cutover_status = {
        "promotion_committed": True,
        "state": "active",
        "promotion_ready": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": target.node_id,
        "former_owner_compute_state": "stopped",
        "former_attachment_absent": True,
        "candidate_attachment_exact": True,
        "ownership_re_read_exact": True,
        "ownership_epoch": "42",
        "apply_locked": False,
        "pending_operation_id": None,
        "guard_boot_id": "boot-a",
        "controller_ready_boot_id": "boot-a",
        "route_reconciliation": {
            "owner_node_id": target.node_id,
            "allocation_id": lifecycle.allocation_id,
            "route_runtime_id": lifecycle.route_runtime_id,
            "generation_id": "a" * 64,
            "digests": {
                "configuration": "a" * 64,
                "static_routes": "b" * 64,
                "bgp_policy": "c" * 64,
            },
            "operation_id": "route-operation",
            "ownership_epoch": "42",
            "ownership_incarnation": 1,
        },
    }
    restored_status = {
        **cutover_status,
        "former_owner_compute_state": "running",
        "rearm_phase": "running",
        "redundancy_ready": True,
    }
    starting_status = {
        **cutover_status,
        "former_owner_compute_state": "transitional",
        "rearm_phase": "starting",
    }
    now = [0.0]

    def status_reader() -> dict[str, t.Any]:
        if now[0] < 6.0:
            return {"state": "promoting", "promotion_committed": False}
        if now[0] >= 12.0:
            return restored_status
        return starting_status if now[0] >= 8.0 else cutover_status

    def cloud_reader() -> t.Any:
        former_state = InstanceCloudState.RUNNING if now[0] >= 12.0 else InstanceCloudState.STOPPED
        active = _manual_failback_compute(
            state=InstanceCloudState.RUNNING if target_role == "active" else former_state,
            alias_present=target_role == "active",
            revision="42",
        )
        passive = _manual_failback_compute(
            state=InstanceCloudState.RUNNING if target_role == "passive" else former_state,
            alias_present=target_role == "passive",
            revision="42",
        )
        return SimpleNamespace(
            allocation=SimpleNamespace(
                owner=AllocationOwner(target.compute_id, target.network_interface_name)
            ),
            former=active,
            candidate=passive,
        )

    standby_status = {
        "state": "normal",
        "standby_ready": True,
        "standby_readiness_reasons": [],
        "data_plane_mode": "passive",
        "observed_owner_node_id": target.node_id,
        "apply_locked": False,
        "pending_operation_id": None,
    }
    context = _VMHAPlannedTerminalContext(
        target_role=target_role,
        former_role=former_role,
        target_member=target,
        former_member=former,
        target_owner=AllocationOwner(target.compute_id, target.network_interface_name),
        allocation_id=lifecycle.allocation_id,
        runtime_binding=runtime_binding,
        status_reader=status_reader,
        standby_status_reader=lambda: standby_status,
        cloud_reader=cloud_reader,
        request_timeout_seconds=20.0,
        cutover_timeout_seconds=20.0,
        restoration_timeout_seconds=20.0,
    )

    elapsed = _wait_for_vm_ha_planned_transfer(
        context=context,
        operation_name=operation_name,
        started_at=0.0,
        clock=lambda: now[0],
        sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    assert elapsed.cutover_seconds == 6.0
    assert elapsed.restoration_seconds == 6.0
    assert elapsed.total_seconds == 12.0
    assert capsys.readouterr().err == expected_progress


def test_planned_transfer_restoration_has_an_independent_deadline() -> None:
    lifecycle = _lifecycle_state()
    members = {member.role: member for member in lifecycle.members}
    target = members["active"]
    former = members["passive"]
    runtime_binding = SimpleNamespace(
        shared_allocation_id=lifecycle.allocation_id,
        route_runtime_id=lifecycle.route_runtime_id,
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
    )
    cutover_status = {
        "promotion_committed": True,
        "state": "active",
        "promotion_ready": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": target.node_id,
        "former_owner_compute_state": "stopped",
        "former_attachment_absent": True,
        "candidate_attachment_exact": True,
        "ownership_re_read_exact": True,
        "ownership_epoch": "42",
        "apply_locked": False,
        "pending_operation_id": None,
        "guard_boot_id": "boot-a",
        "controller_ready_boot_id": "boot-a",
        "route_reconciliation": {
            "owner_node_id": target.node_id,
            "allocation_id": lifecycle.allocation_id,
            "route_runtime_id": lifecycle.route_runtime_id,
            "generation_id": "a" * 64,
            "digests": {
                "configuration": "a" * 64,
                "static_routes": "b" * 64,
                "bgp_policy": "c" * 64,
            },
            "operation_id": "route-operation",
            "ownership_epoch": "42",
            "ownership_incarnation": 1,
        },
    }
    restored_status = {
        **cutover_status,
        "former_owner_compute_state": "running",
        "rearm_phase": "running",
        "redundancy_ready": True,
    }
    now = [0.0]

    def status_reader() -> dict[str, t.Any]:
        if now[0] < 1.0:
            return {"state": "promoting", "promotion_committed": False}
        return restored_status if now[0] >= 2.5 else cutover_status

    def cloud_reader() -> t.Any:
        former_state = InstanceCloudState.RUNNING if now[0] >= 2.5 else InstanceCloudState.STOPPED
        return SimpleNamespace(
            allocation=SimpleNamespace(
                owner=AllocationOwner(target.compute_id, target.network_interface_name)
            ),
            former=_manual_failback_compute(
                state=InstanceCloudState.RUNNING,
                alias_present=True,
                revision="42",
            ),
            candidate=_manual_failback_compute(
                state=former_state,
                alias_present=False,
                revision="42",
            ),
        )

    context = _VMHAPlannedTerminalContext(
        target_role="active",
        former_role="passive",
        target_member=target,
        former_member=former,
        target_owner=AllocationOwner(target.compute_id, target.network_interface_name),
        allocation_id=lifecycle.allocation_id,
        runtime_binding=runtime_binding,
        status_reader=status_reader,
        standby_status_reader=lambda: {
            "state": "normal",
            "standby_ready": True,
            "standby_readiness_reasons": [],
            "data_plane_mode": "passive",
            "observed_owner_node_id": target.node_id,
            "apply_locked": False,
            "pending_operation_id": None,
        },
        cloud_reader=cloud_reader,
        request_timeout_seconds=2.0,
        cutover_timeout_seconds=2.0,
        restoration_timeout_seconds=2.0,
    )

    completion = _wait_for_vm_ha_planned_transfer(
        context=context,
        operation_name="Failback",
        started_at=0.0,
        clock=lambda: now[0],
        sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
        poll_seconds=0.5,
    )

    assert completion.cutover_seconds == 1.0
    assert completion.restoration_seconds == 1.5
    assert completion.total_seconds == 2.5


def test_planned_transfer_reader_latency_reports_background_restoration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    reads = 0

    def status_reader() -> dict[str, t.Any]:
        nonlocal reads
        reads += 1
        if reads == 3:
            now[0] = 1.1
        return {}

    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_cutover_status_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_cutover_cloud_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_planned_owner_redundancy_matches",
        lambda *_args, **_kwargs: False,
    )
    context = _VMHAPlannedTerminalContext(
        target_role="active",
        former_role="passive",
        target_member=SimpleNamespace(node_id="node-active"),
        former_member=SimpleNamespace(node_id="node-passive"),
        target_owner=AllocationOwner("compute-active", "eth0"),
        allocation_id="allocation-a",
        runtime_binding=SimpleNamespace(),
        status_reader=status_reader,
        standby_status_reader=lambda: {},
        cloud_reader=lambda: SimpleNamespace(),
        request_timeout_seconds=1.0,
        cutover_timeout_seconds=1.0,
        restoration_timeout_seconds=1.0,
    )

    with pytest.raises(_VMHAPlannedRedundancyRestorationError) as timeout:
        _wait_for_vm_ha_planned_transfer(
            context=context,
            operation_name="Failback",
            started_at=0.0,
            clock=lambda: now[0],
            sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
            poll_seconds=0.5,
        )

    assert timeout.value.background_continues is True


def test_planned_transfer_waits_through_exact_controller_effect_retry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = {
        "schema": "nebius-vpngw/vm-ha-manual-failover-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-passive",
        "generation_id": "a" * 64,
        "requested_at": 10.0,
    }
    fingerprint = planned_request_fingerprint(request)
    lifecycle = _lifecycle_state()
    members = {member.role: member for member in lifecycle.members}
    target = members["passive"]
    former = members["active"]
    runtime_binding = SimpleNamespace(
        shared_allocation_id=lifecycle.allocation_id,
        generation_id="a" * 64,
        configuration_digest="b" * 64,
        static_routes_digest="c" * 64,
        bgp_policy_digest="d" * 64,
        route_runtime_id=lifecycle.route_runtime_id,
    )
    operation_id = "boot-a:1:stop-former-owner:node-active"
    base_progress = {
        "schema": "nebius-vpngw/vm-ha-transfer-progress-v1",
        "cluster_id": "cluster-a",
        "candidate_node_id": target.node_id,
        "former_owner_node_id": former.node_id,
        "allocation_id": lifecycle.allocation_id,
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "b" * 64,
            "static_routes": "c" * 64,
            "bgp_policy": "d" * 64,
        },
        "route_runtime_id": lifecycle.route_runtime_id,
        "intent": "planned-failover",
        "request_fingerprint": fingerprint,
        "first_operation_id": operation_id,
        "ownership_incarnation": 1,
    }
    attempt = {
        "sequence": 1,
        "action": "stop-former-owner",
        "state": "attempting",
        "operation_id": operation_id,
        "boot_id": "boot-a",
        "ownership_epoch": "7",
        "recorded_at": 10.0,
        "error_type": None,
    }
    failure = {
        **attempt,
        "sequence": 2,
        "state": "failed",
        "recorded_at": 11.0,
        "error_type": "effect-failed",
    }
    retry = {**attempt, "sequence": 3, "recorded_at": 12.0}
    cutover_status = {
        "promotion_committed": True,
        "state": "active",
        "promotion_ready": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": target.node_id,
        "former_owner_compute_state": "stopped",
        "former_attachment_absent": True,
        "candidate_attachment_exact": True,
        "ownership_re_read_exact": True,
        "ownership_epoch": "42",
        "apply_locked": False,
        "pending_operation_id": None,
        "guard_boot_id": "boot-a",
        "controller_ready_boot_id": "boot-a",
        "route_reconciliation": {
            "owner_node_id": target.node_id,
            "allocation_id": lifecycle.allocation_id,
            "route_runtime_id": lifecycle.route_runtime_id,
            "generation_id": "a" * 64,
            "digests": {
                "configuration": "b" * 64,
                "static_routes": "c" * 64,
                "bgp_policy": "d" * 64,
            },
            "operation_id": "route-operation",
            "ownership_epoch": "42",
            "ownership_incarnation": 1,
        },
    }
    restored_status = {
        **cutover_status,
        "former_owner_compute_state": "running",
        "rearm_phase": "running",
        "redundancy_ready": True,
    }
    now = [0.0]

    def status_reader() -> dict[str, t.Any]:
        if now[0] < 1.0:
            return {
                "state": "promoting",
                "promotion_committed": False,
                "pending_operation_id": operation_id,
                "transfer_progress": {**base_progress, "history": [attempt]},
            }
        if now[0] < 2.0:
            return {
                "state": "blocked",
                "reasons": ["controller-step-failed"],
                "pending_operation_id": operation_id,
                "transfer_progress": {
                    **base_progress,
                    "history": [attempt, failure],
                },
            }
        if now[0] < 3.0:
            return {
                "state": "promoting",
                "promotion_committed": False,
                "pending_operation_id": operation_id,
                "transfer_progress": {
                    **base_progress,
                    "history": [attempt, failure, retry],
                },
            }
        return restored_status if now[0] >= 4.0 else cutover_status

    def cloud_reader() -> t.Any:
        former_state = InstanceCloudState.RUNNING if now[0] >= 4.0 else InstanceCloudState.STOPPED
        return SimpleNamespace(
            allocation=SimpleNamespace(
                owner=AllocationOwner(target.compute_id, target.network_interface_name)
            ),
            former=_manual_failback_compute(
                state=former_state,
                alias_present=False,
                revision="42",
            ),
            candidate=_manual_failback_compute(
                state=InstanceCloudState.RUNNING,
                alias_present=True,
                revision="42",
            ),
        )

    context = _VMHAPlannedTerminalContext(
        target_role="passive",
        former_role="active",
        target_member=target,
        former_member=former,
        target_owner=AllocationOwner(target.compute_id, target.network_interface_name),
        allocation_id=lifecycle.allocation_id,
        runtime_binding=runtime_binding,
        status_reader=status_reader,
        standby_status_reader=lambda: {
            "state": "normal",
            "standby_ready": True,
            "standby_readiness_reasons": [],
            "data_plane_mode": "passive",
            "observed_owner_node_id": target.node_id,
            "apply_locked": False,
            "pending_operation_id": None,
        },
        cloud_reader=cloud_reader,
        request_timeout_seconds=10.0,
        cutover_timeout_seconds=10.0,
        restoration_timeout_seconds=10.0,
    )

    completion = _wait_for_vm_ha_planned_transfer(
        context=context,
        operation_name="Failover",
        started_at=0.0,
        request_fingerprint=fingerprint,
        clock=lambda: now[0],
        sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
        poll_seconds=0.5,
    )

    assert completion.cutover_seconds == 3.0
    assert completion.restoration_seconds == 1.0
    assert completion.total_seconds == 4.0
    assert capsys.readouterr().err == (
        "Failover in progress: 0.0s elapsed, stopping current owner...\n"
        "Failover in progress: 1.0s elapsed, stopping current owner failed; "
        "forwarding remains fenced while the controller retries...\n"
        "Failover in progress: 2.0s elapsed, stopping current owner...\n"
        "Failover cutover completed in 3.0s; restoring standby redundancy...\n"
        "Failover in progress: 4.0s elapsed, waiting for standby readiness...\n"
    )


def test_planned_transfer_requires_exact_owned_retry_evidence_and_stays_bounded() -> None:
    fingerprint = "e" * 64
    operation_id = "boot-a:1:stop-former-owner:node-active"
    failed_entry = {
        "sequence": 1,
        "action": "stop-former-owner",
        "state": "failed",
        "operation_id": operation_id,
        "boot_id": "boot-a",
        "ownership_epoch": "7",
        "recorded_at": 10.0,
        "error_type": "effect-failed",
    }
    transfer_progress = {
        "schema": "nebius-vpngw/vm-ha-transfer-progress-v1",
        "cluster_id": "cluster-a",
        "candidate_node_id": "node-passive",
        "former_owner_node_id": "node-active",
        "allocation_id": "shared-private",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "b" * 64,
            "static_routes": "c" * 64,
            "bgp_policy": "d" * 64,
        },
        "route_runtime_id": "route-runtime-a",
        "intent": "planned-failover",
        "request_fingerprint": fingerprint,
        "first_operation_id": operation_id,
        "ownership_incarnation": 1,
        "history": [failed_entry],
    }
    record = {
        "pending_operation_id": "boot-a:2:stop-former-owner:node-active",
        "transfer_progress": transfer_progress,
    }
    context = _VMHAPlannedTerminalContext(
        target_role="passive",
        former_role="active",
        target_member=SimpleNamespace(node_id="node-passive"),
        former_member=SimpleNamespace(node_id="node-active"),
        target_owner=AllocationOwner("compute-1", "eth0"),
        allocation_id="shared-private",
        runtime_binding=SimpleNamespace(
            generation_id="a" * 64,
            configuration_digest="b" * 64,
            static_routes_digest="c" * 64,
            bgp_policy_digest="d" * 64,
            route_runtime_id="route-runtime-a",
        ),
        status_reader=lambda: {},
        standby_status_reader=lambda: {},
        cloud_reader=lambda: None,
        request_timeout_seconds=10.0,
        cutover_timeout_seconds=10.0,
        restoration_timeout_seconds=10.0,
    )

    unowned = _vm_ha_planned_progress_observation(
        record,
        context=context,
        request_fingerprint=fingerprint,
        after_sequence=0,
    )
    foreign = _vm_ha_planned_progress_observation(
        record,
        context=context,
        request_fingerprint="f" * 64,
        after_sequence=0,
    )
    mislabeled_operation_id = "boot-a:1:attach-candidate:node-passive"
    mislabeled_record = {
        **record,
        "pending_operation_id": mislabeled_operation_id,
        "transfer_progress": {
            **transfer_progress,
            "history": [{**failed_entry, "operation_id": mislabeled_operation_id}],
        },
    }
    mislabeled = _vm_ha_planned_progress_observation(
        mislabeled_record,
        context=context,
        request_fingerprint=fingerprint,
        after_sequence=0,
    )
    owned_record = {
        **record,
        "state": "blocked",
        "reasons": ["controller-step-failed"],
        "pending_operation_id": operation_id,
    }
    malformed_record = {
        **owned_record,
        "transfer_progress": {**transfer_progress, "schema": "malformed"},
    }
    stale_boot_record = {
        **owned_record,
        "transfer_progress": {
            **transfer_progress,
            "history": [{**failed_entry, "boot_id": "boot-b"}],
        },
    }
    now = [0.0]

    assert unowned is not None
    assert unowned.retryable_failure is None
    assert foreign is None
    assert mislabeled is not None
    assert mislabeled.retryable_failure is None
    assert (
        _vm_ha_planned_progress_observation(
            malformed_record,
            context=context,
            request_fingerprint=fingerprint,
            after_sequence=0,
        )
        is None
    )
    stale_boot = _vm_ha_planned_progress_observation(
        stale_boot_record,
        context=context,
        request_fingerprint=fingerprint,
        after_sequence=0,
    )
    assert stale_boot is not None
    assert stale_boot.retryable_failure is None
    for rejected_record in (malformed_record, stale_boot_record):
        with pytest.raises(RuntimeError, match="controller-step-failed") as rejected:
            _wait_for_vm_ha_planned_transfer(
                context=replace(
                    context,
                    status_reader=lambda record=rejected_record: record,
                    standby_status_reader=lambda: pytest.fail(
                        "rejected retry evidence cannot reach standby proof"
                    ),
                    cloud_reader=lambda: pytest.fail(
                        "rejected retry evidence cannot reach cloud proof"
                    ),
                ),
                operation_name="Failover",
                started_at=0.0,
                request_fingerprint=fingerprint,
                clock=lambda: 0.0,
                sleeper=lambda _seconds: pytest.fail(
                    "rejected retry evidence must fail immediately"
                ),
            )
        assert "Forwarding remains fenced" in str(rejected.value)
    with pytest.raises(RuntimeError, match="still retrying stopping current owner") as timeout:
        _wait_for_vm_ha_planned_transfer(
            context=replace(
                context,
                cutover_timeout_seconds=1.0,
                status_reader=lambda: owned_record,
                standby_status_reader=lambda: pytest.fail(
                    "retry deadline must precede standby proof"
                ),
                cloud_reader=lambda: pytest.fail("retry deadline must precede cloud proof"),
            ),
            operation_name="Failover",
            started_at=0.0,
            request_fingerprint=fingerprint,
            clock=lambda: now[0],
            sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )
    assert "Forwarding remains fenced" in str(timeout.value)

    now[0] = 0.0

    def slow_status_reader() -> dict[str, t.Any]:
        now[0] = 1.1
        return owned_record

    with pytest.raises(RuntimeError, match="still retrying stopping current owner") as timeout:
        _wait_for_vm_ha_planned_transfer(
            context=replace(
                context,
                cutover_timeout_seconds=1.0,
                status_reader=slow_status_reader,
                standby_status_reader=lambda: pytest.fail(
                    "retry deadline must precede standby proof"
                ),
                cloud_reader=lambda: pytest.fail("retry deadline must precede cloud proof"),
            ),
            operation_name="Failover",
            started_at=0.0,
            request_fingerprint=fingerprint,
            clock=lambda: now[0],
            sleeper=lambda _seconds: pytest.fail(
                "a status read that reaches the deadline must not sleep"
            ),
        )
    assert "Forwarding remains fenced" in str(timeout.value)


def test_planned_transfer_reports_exact_lineage_bound_phase_changes_immediately(
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = {
        "schema": "nebius-vpngw/vm-ha-manual-failover-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-passive",
        "generation_id": "a" * 64,
        "requested_at": 10.0,
    }
    fingerprint = planned_request_fingerprint(request)
    target = SimpleNamespace(node_id="node-passive")
    former = SimpleNamespace(node_id="node-active")
    runtime_binding = SimpleNamespace(
        generation_id="a" * 64,
        configuration_digest="b" * 64,
        static_routes_digest="c" * 64,
        bgp_policy_digest="d" * 64,
        route_runtime_id="route-runtime-a",
    )
    base_progress = {
        "schema": "nebius-vpngw/vm-ha-transfer-progress-v1",
        "cluster_id": "cluster-a",
        "candidate_node_id": target.node_id,
        "former_owner_node_id": former.node_id,
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "b" * 64,
            "static_routes": "c" * 64,
            "bgp_policy": "d" * 64,
        },
        "route_runtime_id": "route-runtime-a",
        "intent": "planned-failover",
        "request_fingerprint": fingerprint,
        "first_operation_id": "boot-a:1:stop-former-owner:node-active",
        "ownership_incarnation": 1,
    }
    stop_attempt = {
        "sequence": 1,
        "action": "stop-former-owner",
        "state": "attempting",
        "operation_id": "boot-a:1:stop-former-owner:node-active",
        "boot_id": "boot-a",
        "ownership_epoch": "7",
        "recorded_at": 10.0,
        "error_type": None,
    }
    stop_complete = {**stop_attempt, "sequence": 2, "state": "completed", "recorded_at": 11.0}
    detach_attempt = {
        **stop_attempt,
        "sequence": 3,
        "action": "detach-former-attachment",
        "state": "attempting",
        "operation_id": "boot-a:2:detach-former-attachment:node-active",
        "recorded_at": 12.0,
    }
    now = [0.0]

    def status_reader() -> dict[str, t.Any]:
        if now[0] >= 2.0:
            return {"state": "blocked", "reasons": ["controller-step-failed"]}
        history = [stop_attempt] if now[0] < 1.0 else [stop_attempt, stop_complete, detach_attempt]
        return {
            "state": "promoting",
            "promotion_committed": False,
            "transfer_progress": {**base_progress, "history": history},
        }

    context = _VMHAPlannedTerminalContext(
        target_role="passive",
        former_role="active",
        target_member=target,
        former_member=former,
        target_owner=AllocationOwner("compute-passive", "eth0"),
        allocation_id="allocation-a",
        runtime_binding=runtime_binding,
        status_reader=status_reader,
        standby_status_reader=lambda: pytest.fail("blocked trial cannot reach standby"),
        cloud_reader=lambda: pytest.fail("blocked trial cannot reach terminal cloud proof"),
        request_timeout_seconds=10.0,
        cutover_timeout_seconds=10.0,
        restoration_timeout_seconds=10.0,
    )

    with pytest.raises(RuntimeError, match="controller-step-failed"):
        _wait_for_vm_ha_planned_transfer(
            context=context,
            operation_name="Failover",
            started_at=0.0,
            request_fingerprint=fingerprint,
            clock=lambda: now[0],
            sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )

    assert capsys.readouterr().err == (
        "Failover in progress: 0.0s elapsed, stopping current owner...\n"
        "Failover in progress: 1.0s elapsed, unassigning shared IP...\n"
    )


def test_planned_transfer_falls_back_after_fine_progress_disappears(
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = {
        "schema": "nebius-vpngw/vm-ha-manual-failover-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-passive",
        "generation_id": "a" * 64,
        "requested_at": 10.0,
    }
    fingerprint = planned_request_fingerprint(request)
    target = SimpleNamespace(node_id="node-passive")
    former = SimpleNamespace(node_id="node-active")
    runtime_binding = SimpleNamespace(
        generation_id="a" * 64,
        configuration_digest="b" * 64,
        static_routes_digest="c" * 64,
        bgp_policy_digest="d" * 64,
        route_runtime_id="route-runtime-a",
    )
    progress = {
        "schema": "nebius-vpngw/vm-ha-transfer-progress-v1",
        "cluster_id": "cluster-a",
        "candidate_node_id": target.node_id,
        "former_owner_node_id": former.node_id,
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "b" * 64,
            "static_routes": "c" * 64,
            "bgp_policy": "d" * 64,
        },
        "route_runtime_id": "route-runtime-a",
        "intent": "planned-failover",
        "request_fingerprint": fingerprint,
        "first_operation_id": "boot-a:1:stop-former-owner:node-active",
        "ownership_incarnation": 1,
        "history": [
            {
                "sequence": 1,
                "action": "stop-former-owner",
                "state": "attempting",
                "operation_id": "boot-a:1:stop-former-owner:node-active",
                "boot_id": "boot-a",
                "ownership_epoch": "7",
                "recorded_at": 10.0,
                "error_type": None,
            }
        ],
    }
    now = [0.0]

    def status_reader() -> dict[str, t.Any]:
        if now[0] >= 6.0:
            return {"state": "blocked", "reasons": ["controller-step-failed"]}
        record: dict[str, t.Any] = {
            "state": "promoting",
            "promotion_committed": False,
        }
        if now[0] < 1.0:
            record["transfer_progress"] = progress
        return record

    context = _VMHAPlannedTerminalContext(
        target_role="passive",
        former_role="active",
        target_member=target,
        former_member=former,
        target_owner=AllocationOwner("compute-passive", "eth0"),
        allocation_id="allocation-a",
        runtime_binding=runtime_binding,
        status_reader=status_reader,
        standby_status_reader=lambda: pytest.fail("blocked trial cannot reach standby"),
        cloud_reader=lambda: pytest.fail("blocked trial cannot reach terminal cloud proof"),
        request_timeout_seconds=10.0,
        cutover_timeout_seconds=10.0,
        restoration_timeout_seconds=10.0,
    )

    with pytest.raises(RuntimeError, match="controller-step-failed"):
        _wait_for_vm_ha_planned_transfer(
            context=context,
            operation_name="Failover",
            started_at=0.0,
            request_fingerprint=fingerprint,
            clock=lambda: now[0],
            sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )

    assert capsys.readouterr().err == (
        "Failover in progress: 0.0s elapsed, stopping current owner...\n"
        "Failover in progress: 5.0s elapsed, cutting over...\n"
    )


def test_planned_transfer_rejects_blocked_controller_without_success_wait() -> None:
    context = _VMHAPlannedTerminalContext(
        target_role="active",
        former_role="passive",
        target_member=SimpleNamespace(node_id="node-active"),
        former_member=SimpleNamespace(node_id="node-passive"),
        target_owner=AllocationOwner("compute-0", "eth0"),
        allocation_id="shared-private",
        runtime_binding=SimpleNamespace(),
        status_reader=lambda: {
            "state": "blocked",
            "reasons": ["controller-step-failed"],
        },
        standby_status_reader=lambda: pytest.fail(
            "blocked status must precede standby success proof"
        ),
        cloud_reader=lambda: pytest.fail("blocked status must precede cloud success proof"),
        request_timeout_seconds=20.0,
        cutover_timeout_seconds=20.0,
        restoration_timeout_seconds=20.0,
    )

    with pytest.raises(RuntimeError, match="controller-step-failed") as blocked:
        _wait_for_vm_ha_planned_transfer(
            context=context,
            operation_name="Failback",
            started_at=0.0,
            clock=lambda: 1.0,
            sleeper=lambda _seconds: None,
        )

    assert "Forwarding remains fenced" in str(blocked.value)
    assert "nebius-vpngw status --local-config-file <file>" in str(blocked.value)
    assert "journalctl -u nebius-vpngw-vm-ha.service" in str(blocked.value)


def test_planned_transfer_timeout_is_bounded() -> None:
    now = [0.0]
    context = _VMHAPlannedTerminalContext(
        target_role="active",
        former_role="passive",
        target_member=SimpleNamespace(node_id="node-active"),
        former_member=SimpleNamespace(node_id="node-passive"),
        target_owner=AllocationOwner("compute-0", "eth0"),
        allocation_id="shared-private",
        runtime_binding=SimpleNamespace(),
        status_reader=lambda: {"state": "promoting", "promotion_committed": False},
        standby_status_reader=lambda: pytest.fail("timeout must precede standby success proof"),
        cloud_reader=lambda: pytest.fail("timeout must precede cloud success proof"),
        request_timeout_seconds=2.0,
        cutover_timeout_seconds=2.0,
        restoration_timeout_seconds=2.0,
    )

    with pytest.raises(RuntimeError, match=r"within 2\.0s"):
        _wait_for_vm_ha_planned_transfer(
            context=context,
            operation_name="Failback",
            started_at=0.0,
            clock=lambda: now[0],
            sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )


@pytest.mark.parametrize(
    "arguments",
    (
        ("vm-ha-failover",),
        ("vm-ha-failback",),
        ("set-vm-ha-mtls",),
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

    class FakeManager(_ContextManagedFake):
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
    output = unstyle(result.output)
    normalized_output = " ".join(output.split())

    assert result.exit_code == 0
    assert "Usage:" in output
    assert "Examples:" in output
    assert "COMMAND --help" in output
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


@pytest.mark.parametrize("command_name", ("vm-ha", "apply", "prep-network", "status", "destroy"))
def test_region_commands_publish_one_precedence_contract(command_name: str) -> None:
    command = get_command(app).commands[command_name]
    region_options = [parameter for parameter in command.params if parameter.name == "region"]

    assert len(region_options) == 1
    assert region_options[0].help == _NEBIUS_REGION_HELP
    assert all(parameter.name != "zone" for parameter in command.params)


@pytest.mark.parametrize("command_path", tuple(_COMMAND_EXAMPLES))
def test_each_cli_command_help_renders_with_its_examples(
    command_path: tuple[str, ...],
) -> None:
    result = CliRunner().invoke(app, [*command_path, "--help"], env=HELP_ENV)
    output = unstyle(result.output)
    normalized_output = " ".join(output.split())

    assert result.exit_code == 0
    assert "Usage:" in output
    assert "Examples:" in output
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
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "--sa" in output
    assert "Service Account" in output
    assert "--prepare-vm-ha-peer-rotation" in output
    assert "VM-HA IPsec peer credential change" in " ".join(output.split())


def test_destroy_help_publishes_topology_and_retention_contract() -> None:
    result = CliRunner().invoke(app, ["destroy", "--help"], env=HELP_ENV)
    output = unstyle(result.stdout)
    normalized = " ".join(output.split())

    assert result.exit_code == 0
    assert "ordinary or VM-HA gateway compute" in normalized
    assert "default-No confirmation" in normalized
    assert "public IP allocations are retained" in normalized
    assert "--yes" in output


def test_route_and_operator_help_mentions_multi_connection_behavior() -> None:
    click_app = get_command(app)

    list_remote_help = click_app.commands["list-routes-remote"].help or ""
    add_routes_cmd = click_app.commands["add-routes-local"]
    add_routes_help = add_routes_cmd.help or ""
    failover_group = click_app.commands["failover"]
    assert isinstance(failover_group, TyperGroup)
    failover_command = failover_group.commands["tunnel"]
    failover_help = failover_command.params[0].help or ""
    failback_group = click_app.commands["failback"]
    assert isinstance(failback_group, TyperGroup)
    failback_command = failback_group.commands["tunnel"]
    restart_command = click_app.commands["restart-tunnel"]
    restart_help = restart_command.params[0].help or ""

    assert "owning gateway VM" in list_remote_help
    assert "selected connection" in list_remote_help

    assert "--swap-route-table" in add_routes_help
    assert "rollback command" in add_routes_help

    option_help_by_name = {param.name: param.help or "" for param in add_routes_cmd.params}
    assert "rollback command" in option_help_by_name["swap_route_table"]
    assert "Skip the confirmation prompt for" in option_help_by_name["yes"]
    assert "--swap-route-table" in option_help_by_name["yes"]

    assert "multi-connection topologies" in failover_help
    for command in (failover_command, failback_command):
        command_help = command.help or ""
        assert "supported only on regular gateways (non-HA)" in command_help
        assert "using BGP, not Static routing" in command_help
    assert "only the owning gateway VM" in restart_help
    normalized_restart_command_help = " ".join((restart_command.help or "").split())
    assert "supported only on regular gateways (non-HA)" in normalized_restart_command_help
    assert "unsupported for VM-HA-enabled gateways" in normalized_restart_command_help
    assert "controller owns data-plane repair" in normalized_restart_command_help


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


def test_status_ssh_context_isolates_one_missing_explicit_vm_ha_pin(
    tmp_path: Path, monkeypatch
) -> None:
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
    monkeypatch.setenv("VPNGW_SSH_KNOWN_HOSTS_FILE", str(tmp_path / "known_hosts"))

    def require_policy(host_pairs, *, enrollment_hosts, trust_scope):
        pairs = tuple(host_pairs)
        observed_pairs.append(pairs)
        assert enrollment_hosts == set()
        assert trust_scope.cluster_id == "cluster-a"
        if pairs[0][0] == "nebius-vpn-gw-1":
            raise ValueError("missing exact pin")
        return trusted_policy

    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", require_policy)
    exact_auth = object()
    monkeypatch.setattr(
        "nebius_vpngw.cli.resolve_ssh_client_auth",
        lambda *_args, **_kwargs: exact_auth,
    )
    built: list[tuple[Path | None, object | None, object | None, str | None]] = []

    def build_command(key_path, *, client_auth=None, ssh_policy=None, hostname=None):
        built.append((key_path, client_auth, ssh_policy, hostname))
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
                    "ssh_public_key": "ssh-ed25519 fixture configured identity",
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
    assert built == [(None, exact_auth, trusted_policy, "nebius-vpn-gw-0")]
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


def test_status_ssh_context_resolves_managed_vm_ha_trust_at_full_member_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    members = [
        SimpleNamespace(hostname="nebius-vpn-gw-0", external_ip="203.0.113.10"),
        SimpleNamespace(hostname="nebius-vpn-gw-1", external_ip="203.0.113.11"),
    ]
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
        gateway_group=SimpleNamespace(name="nebius-vpn-gw", region="eu-west1"),
        iter_instance_configs=lambda: iter(members),
    )
    managed_policy = object()
    observed: list[tuple[tuple[str, str], ...]] = []

    def existing_policy(_config, _plan, host_pairs, **_kwargs):
        observed.append(tuple(host_pairs))
        return managed_policy

    monkeypatch.delenv("VPNGW_SSH_KNOWN_HOSTS_FILE", raising=False)
    monkeypatch.setattr("nebius_vpngw.cli._existing_gateway_ssh_policy", existing_policy)

    context = _build_status_ssh_context(
        {
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "gateway_group": {"vm_spec": {}},
        },
        plan,
        {
            "nebius-vpn-gw-0": "198.51.100.10",
            "nebius-vpn-gw-1": "198.51.100.11",
        },
    )

    assert observed == [
        (
            ("nebius-vpn-gw-0", "198.51.100.10"),
            ("nebius-vpn-gw-1", "198.51.100.11"),
        )
    ]
    assert context.policies == {
        "nebius-vpn-gw-0": managed_policy,
        "nebius-vpn-gw-1": managed_policy,
    }
    assert context.unavailable_members == frozenset()


def test_status_ssh_context_rejects_managed_trust_for_an_incomplete_member_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    members = [
        SimpleNamespace(hostname="nebius-vpn-gw-0", external_ip="203.0.113.10"),
        SimpleNamespace(hostname="nebius-vpn-gw-1", external_ip="203.0.113.11"),
    ]
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
        gateway_group=SimpleNamespace(name="nebius-vpn-gw", region="eu-west1"),
        iter_instance_configs=lambda: iter(members),
    )
    observed: list[tuple[tuple[str, str], ...]] = []

    def existing_policy(_config, _plan, host_pairs, **_kwargs):
        observed.append(tuple(host_pairs))
        raise ValueError("managed receipt member set is incomplete")

    monkeypatch.delenv("VPNGW_SSH_KNOWN_HOSTS_FILE", raising=False)
    monkeypatch.setattr("nebius_vpngw.cli._existing_gateway_ssh_policy", existing_policy)

    context = _build_status_ssh_context(
        {
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "gateway_group": {"vm_spec": {}},
        },
        plan,
        {
            "nebius-vpn-gw-0": "198.51.100.10",
            "nebius-vpn-gw-1": "198.51.100.11",
        },
    )

    assert observed == [
        (
            ("nebius-vpn-gw-0", "198.51.100.10"),
            ("nebius-vpn-gw-1", "198.51.100.11"),
        )
    ]
    assert context.policies == {
        "nebius-vpn-gw-0": None,
        "nebius-vpn-gw-1": None,
    }
    assert context.unavailable_members == frozenset({"nebius-vpn-gw-0", "nebius-vpn-gw-1"})


def test_vm_ha_status_does_not_probe_without_exact_client_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nebius_vpngw import cli as cli_module

    members = tuple(
        SimpleNamespace(
            hostname=f"gateway-{index}",
            vm_ha_node=SimpleNamespace(
                node_id=f"node-{index}",
                role=SimpleNamespace(value="active" if index == 0 else "passive"),
            ),
        )
        for index in range(2)
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
        gateway_group=SimpleNamespace(name="gateway", region="eu-west1"),
        iter_instance_configs=lambda: iter(members),
    )
    context = cli_module._StatusSSHContext(
        username="operator",
        key_path=tmp_path / "mismatched-key",
        client_auth=None,
        client_auth_required=True,
        policies={member.hostname: object() for member in members},
        unavailable_members=frozenset(),
        exact_trust_required=True,
    )
    fetch = Mock(return_value={})
    monkeypatch.setattr("nebius_vpngw.cli._fetch_vm_ha_agent_status", fetch)
    monkeypatch.setattr(
        "nebius_vpngw.cli.VMHALifecycleStore",
        lambda _path: SimpleNamespace(read=lambda **_kwargs: None),
    )

    snapshot = cli_module._collect_vm_ha_status_snapshot(
        local_config_file=tmp_path / "gateway.yaml",
        local_cfg={},
        plan=plan,
        project_id="project-a",
        vm_manager=SimpleNamespace(),
        vm_ips={member.hostname: f"192.0.2.{index + 10}" for index, member in enumerate(members)},
        ssh_context=context,
        require_local_generation=True,
    )

    fetch.assert_not_called()
    assert {member.reason for member in snapshot.members} == {"ssh-trust-unavailable"}


def test_ordinary_existing_ssh_consumer_uses_published_managed_trust(monkeypatch) -> None:
    instance = SimpleNamespace(hostname="gateway-0", external_ip="203.0.113.10")
    plan = SimpleNamespace(
        vm_ha=None,
        gateway_group=SimpleNamespace(name="gateway", region="eu-west1"),
        iter_instance_configs=lambda: iter((instance,)),
    )
    local_cfg = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "region_id": "eu-west1",
    }
    policy = object()
    observed: dict[str, object] = {}
    monkeypatch.delenv("VPNGW_SSH_KNOWN_HOSTS_FILE", raising=False)
    monkeypatch.setattr("nebius_vpngw.cli.managed_ssh_trust_available", lambda scope: True)

    def require_policy(host_pairs, **kwargs):
        observed["host_pairs"] = tuple(host_pairs)
        observed.update(kwargs)
        return policy

    monkeypatch.setattr("nebius_vpngw.cli.require_vm_ha_ssh_policy", require_policy)

    resolved = _existing_gateway_ssh_policy(
        local_cfg,
        plan,
        ((instance.hostname, instance.external_ip),),
    )

    assert resolved is policy
    assert observed["host_pairs"] == (("gateway-0", "203.0.113.10"),)
    assert observed["enrollment_hosts"] == set()
    assert observed["trust_scope"].cluster_id == "ordinary-v1"


def test_vm_ha_ssh_trust_scope_uses_the_resolved_plan_region() -> None:
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
        gateway_group=SimpleNamespace(name="gateway-a", region="eu-north1"),
    )

    scope = _vm_ha_ssh_trust_scope(
        {
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "region_id": "eu-east1",
        },
        plan,
    )

    assert scope.region_id == "eu-north1"


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


def test_restart_tunnel_static_restarts_ipsec_without_bgp_commands(tmp_path: Path) -> None:
    config_path = tmp_path / "restart-static.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "defaults": {"routing": {"mode": RoutingMode.STATIC}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 1,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "static-site",
                "routing_mode": RoutingMode.STATIC,
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
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, input=None):
        recorded_cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

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

    assert result.exit_code == 0
    assert len(recorded_cmds) == 1
    assert recorded_cmds[0][-1] == "sudo /usr/bin/python3 - --restart-tunnel tunnel-1"
    assert all("vtysh" not in part for cmd in recorded_cmds for part in cmd)
    assert "Resetting matching BGP neighbor(s)" not in result.stdout
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
