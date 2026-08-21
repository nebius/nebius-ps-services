from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "misc" / "gcp-vpngw.sh"
HELPER_PATH = PROJECT_ROOT / "misc" / "gcp_vpngw_vm_ha.py"


def _load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("test_gcp_vpngw_vm_ha", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _args() -> list[str]:
    return [
        "--vm-ha-peer",
        "--connection-name",
        "site-ha",
        "--gcp-project-id",
        "fixture-project",
        "--region",
        "europe-west9",
        "--network",
        "fixture-network",
        "--vpn-gateway-name",
        "ha-gw-nebius",
        "--cloud-router-name",
        "cr-nebius-ha",
        "--cloud-router-asn",
        "65014",
        "--nebius-active-public-ip",
        "203.0.113.10",
        "--nebius-passive-public-ip",
        "203.0.113.20",
        "--nebius-asn",
        "65010",
    ]


def _plan(module: ModuleType) -> Any:
    return module.build_plan(module.parser().parse_args(_args()))


def _fake_gcloud(tmp_path: Path, resources: dict[str, dict[str, Any]]) -> tuple[Path, Path]:
    command_log = tmp_path / "gcloud-argv.jsonl"
    executable = tmp_path / "gcloud"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
with Path(os.environ["FAKE_GCLOUD_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(arguments) + "\\n")

if arguments[:2] == ["auth", "list"]:
    print(os.environ.get("FAKE_GCLOUD_ACCOUNT", ""))
    raise SystemExit(0)

if len(arguments) >= 4 and arguments[:2] == ["compute", arguments[1]] and arguments[2] == "describe":
    forced_error = os.environ.get("FAKE_GCLOUD_DESCRIBE_ERROR", "")
    if forced_error:
        print(forced_error, file=sys.stderr)
        raise SystemExit(1)
    key = f"{arguments[1]}/{arguments[3]}"
    resources = json.loads(os.environ.get("FAKE_GCLOUD_RESOURCES", "{}"))
    if key not in resources:
        print("ERROR: requested resource was not found", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(resources[key]))
    raise SystemExit(0)

raise SystemExit(2)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    resources_file = tmp_path / "resources.json"
    resources_file.write_text(json.dumps(resources), encoding="utf-8")
    return executable, command_log


def _subprocess_env(
    tmp_path: Path, command_log: Path, resources: dict[str, dict[str, Any]]
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "FAKE_GCLOUD_ACCOUNT": "tester@example.invalid",
            "FAKE_GCLOUD_LOG": str(command_log),
            "FAKE_GCLOUD_RESOURCES": json.dumps(resources),
            "PATH": f"{tmp_path}{os.pathsep}{env['PATH']}",
        }
    )
    return env


def _complete_resources(module: ModuleType) -> dict[str, dict[str, Any]]:
    plan = _plan(module)
    router_interfaces = [
        {
            "name": tunnel.router_interface,
            "ipRange": f"{tunnel.gcp_ip}/30",
            "linkedVpnTunnel": f"regions/europe-west9/vpnTunnels/{tunnel.name}",
        }
        for tunnel in plan.tunnels
    ]
    router_peers = [
        {
            "name": tunnel.bgp_peer,
            "interfaceName": tunnel.router_interface,
            "peerIpAddress": tunnel.nebius_ip,
            "peerAsn": plan.nebius_asn,
            "advertisedRoutePriority": tunnel.advertised_priority,
        }
        for tunnel in plan.tunnels
    ]
    resources: dict[str, dict[str, Any]] = {
        "vpn-gateways/ha-gw-nebius": {
            "network": "global/networks/fixture-network",
            "vpnInterfaces": [
                {"id": 0, "ipAddress": "198.51.100.10"},
                {"id": 1, "ipAddress": "198.51.100.20"},
            ],
        },
        "routers/cr-nebius-ha": {
            "network": "global/networks/fixture-network",
            "bgp": {"asn": 65014},
            "interfaces": router_interfaces,
            "bgpPeers": router_peers,
        },
        "external-vpn-gateways/site-ha-peer-a": {
            "redundancyType": "TWO_IPS_REDUNDANCY",
            "interfaces": [
                {"id": 0, "ipAddress": "203.0.113.10"},
                {"id": 1, "ipAddress": "203.0.113.20"},
            ],
        },
        "external-vpn-gateways/site-ha-peer-b": {
            "redundancyType": "TWO_IPS_REDUNDANCY",
            "interfaces": [
                {"id": 0, "ipAddress": "203.0.113.20"},
                {"id": 1, "ipAddress": "203.0.113.10"},
            ],
        },
    }
    for tunnel in plan.tunnels:
        resources[f"vpn-tunnels/{tunnel.name}"] = {
            "vpnGateway": "regions/europe-west9/vpnGateways/ha-gw-nebius",
            "peerExternalGateway": f"global/externalVpnGateways/{tunnel.external_gateway}",
            "router": "regions/europe-west9/routers/cr-nebius-ha",
            "ikeVersion": 2,
            "vpnGatewayInterface": tunnel.gcp_interface,
            "peerExternalGatewayInterface": tunnel.peer_interface,
            "status": "ESTABLISHED",
        }
    return resources


def test_vm_ha_plan_uses_mirrored_peer_mapping_and_member_priorities() -> None:
    module = _load_helper()
    plan = _plan(module)

    assert [tunnel.external_gateway for tunnel in plan.tunnels] == [
        "site-ha-peer-a",
        "site-ha-peer-a",
        "site-ha-peer-b",
        "site-ha-peer-b",
    ]
    assert [tunnel.vm_index for tunnel in plan.tunnels] == [0, 1, 1, 0]
    assert [tunnel.gcp_interface for tunnel in plan.tunnels] == [0, 1, 0, 1]
    assert [tunnel.advertised_priority for tunnel in plan.tunnels] == [0, 100, 100, 0]
    assert [(tunnel.vm_index, tunnel.ha_role) for tunnel in plan.tunnels] == [
        (0, "active"),
        (1, "passive"),
        (1, "active"),
        (0, "passive"),
    ]
    assert len({tunnel.cidr for tunnel in plan.tunnels}) == 4


def test_vm_ha_plan_rejects_invalid_psk_environment_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_helper()
    monkeypatch.setenv("PSK1_ENV_NAME", "invalid-yaml-name")

    with pytest.raises(module.HelperError, match="valid environment variable name"):
        _plan(module)


def test_vm_ha_plan_preserves_unique_suffixes_for_maximum_connection_name() -> None:
    module = _load_helper()
    arguments = _args()
    arguments[2] = "a" * 63

    plan = module.build_plan(module.parser().parse_args(arguments))

    generated_names = {
        plan.external_gateway_a,
        plan.external_gateway_b,
        *(tunnel.name for tunnel in plan.tunnels),
        *(tunnel.router_interface for tunnel in plan.tunnels),
        *(tunnel.bgp_peer for tunnel in plan.tunnels),
    }
    assert len(generated_names) == 14
    assert all(len(name) <= 63 for name in generated_names)


def test_vm_ha_status_uses_only_read_only_gcloud_commands(tmp_path: Path) -> None:
    module = _load_helper()
    resources = _complete_resources(module)
    _executable, command_log = _fake_gcloud(tmp_path, resources)

    result = subprocess.run(
        [str(SCRIPT), *_args(), "--status"],
        cwd=PROJECT_ROOT,
        env=_subprocess_env(tmp_path, command_log, resources),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert "Status inspection complete" in result.stdout
    commands = [json.loads(line) for line in command_log.read_text().splitlines()]
    assert commands
    assert all(command[:2] == ["auth", "list"] or "describe" in command for command in commands)
    assert all("login" not in command for command in commands)
    assert all(
        not any(token in command for token in ("create", "add-interface", "add-bgp-peer"))
        for command in commands
    )


def test_vm_ha_dry_run_is_four_tunnel_and_secret_free(tmp_path: Path) -> None:
    module = _load_helper()
    resources = {
        key: value
        for key, value in _complete_resources(module).items()
        if key in {"vpn-gateways/ha-gw-nebius", "routers/cr-nebius-ha"}
    }
    resources["routers/cr-nebius-ha"] = {
        "network": "global/networks/fixture-network",
        "bgp": {"asn": 65014},
        "interfaces": [],
        "bgpPeers": [],
    }
    _executable, command_log = _fake_gcloud(tmp_path, resources)
    env = _subprocess_env(tmp_path, command_log, resources)
    env["GCP_SITE_HA_TUNNEL_1_PSK"] = "must-not-appear"

    result = subprocess.run(
        [str(SCRIPT), *_args(), "--dry-run"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("DRY-RUN create tunnel:") == 4
    assert "gateway_instance_index: 0" in result.stdout
    assert "gateway_instance_index: 1" in result.stdout
    assert "GCP_SITE_HA_TUNNEL_1_PSK" in result.stdout
    assert "must-not-appear" not in result.stdout + result.stderr
    commands = [json.loads(line) for line in command_log.read_text().splitlines()]
    assert all("create" not in command for command in commands)


def test_legacy_status_never_starts_gcloud_login(tmp_path: Path) -> None:
    resources: dict[str, dict[str, Any]] = {}
    _executable, command_log = _fake_gcloud(tmp_path, resources)
    env = _subprocess_env(tmp_path, command_log, resources)
    env["FAKE_GCLOUD_ACCOUNT"] = ""

    result = subprocess.run(
        [
            str(SCRIPT),
            "--connection-name",
            "legacy",
            "--gcp-project-id",
            "fixture-project",
            "--region",
            "europe-west9",
            "--status",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.returncode != 0
    assert "Run 'gcloud auth login' explicitly" in result.stderr
    commands = [json.loads(line) for line in command_log.read_text().splitlines()]
    assert commands == [["auth", "list", "--filter=status:ACTIVE", "--format=value(account)"]]


def test_vm_ha_create_commands_bind_priorities_by_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_helper()
    plan = _plan(module)

    class FakeCloud:
        def __init__(self) -> None:
            self.commands: list[list[str]] = []

        def describe_json(self, kind: str, name: str, *, regional: bool) -> dict[str, Any] | None:
            del regional
            if kind == "vpn-gateways":
                return {"network": "global/networks/fixture-network", "vpnInterfaces": []}
            if kind == "routers":
                return {
                    "network": "global/networks/fixture-network",
                    "bgp": {"asn": 65014},
                    "interfaces": [],
                    "bgpPeers": [],
                }
            del name
            return None

        def mutate(
            self,
            arguments: list[str],
            *,
            label: str,
            secrets: tuple[str, ...] = (),
            secret_flags: dict[str, str] | None = None,
        ) -> None:
            del label, secrets, secret_flags
            self.commands.append(arguments)

    cloud = FakeCloud()
    psks: dict[str, str] = {}
    for tunnel in plan.tunnels:
        psks[tunnel.psk_env_name] = f"secret-{tunnel.name}"
        module._validate_tunnel(plan, cloud, tunnel, dry_run=False, status=False, psks=psks)
        module._validate_router_interface(plan, cloud, tunnel, dry_run=False, status=False)
        module._validate_bgp_peer(plan, cloud, tunnel, dry_run=False, status=False)

    peer_commands = [command for command in cloud.commands if "add-bgp-peer" in command]
    assert [
        next(value for value in command if value.startswith("--advertised-route-priority="))
        for command in peer_commands
    ] == [
        "--advertised-route-priority=0",
        "--advertised-route-priority=100",
        "--advertised-route-priority=100",
        "--advertised-route-priority=0",
    ]
    assert all(not any("secret-" in value for value in command) for command in peer_commands)


def test_vm_ha_private_config_psks_are_preflighted_without_output(
    tmp_path: Path,
) -> None:
    module = _load_helper()
    plan = _plan(module)
    config = tmp_path / "private.config.yaml"
    config.write_text(
        "gateway_group:\n"
        "  connections:\n"
        "    - name: unrelated-site\n"
        "      tunnels:\n"
        "        - name: unrelated-tunnel\n"
        "          psk: unrelated-secret\n"
        f"    - name: {plan.connection}\n"
        "      tunnels:\n"
        f"        - name: {plan.tunnels[3].name}\n"
        "          psk: secret-four\n"
        f"        - name: {plan.tunnels[0].name}\n"
        "          psk: secret-one\n"
        f"        - name: {plan.tunnels[2].name}\n"
        "          psk: secret-three\n"
        f"        - name: {plan.tunnels[1].name}\n"
        "          psk: secret-two\n",
        encoding="utf-8",
    )
    config.chmod(0o600)

    resolved = module._resolve_psks(plan, str(config))

    assert list(resolved) == [tunnel.psk_env_name for tunnel in plan.tunnels]
    assert list(resolved.values()) == [
        "secret-one",
        "secret-two",
        "secret-three",
        "secret-four",
    ]


def test_vm_ha_psk_source_config_rejects_unsafe_permissions(tmp_path: Path) -> None:
    module = _load_helper()
    plan = _plan(module)
    config = tmp_path / "public.config.yaml"
    config.write_text("connections: []\n", encoding="utf-8")
    config.chmod(0o644)

    with pytest.raises(module.HelperError, match="group or other users"):
        module._resolve_psks(plan, str(config))


def test_vm_ha_psk_source_config_rejects_environment_ambiguity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_helper()
    plan = _plan(module)
    config = tmp_path / "private.config.yaml"
    config.write_text("connections: []\n", encoding="utf-8")
    config.chmod(0o600)
    monkeypatch.setenv(plan.tunnels[0].psk_env_name, "environment-secret")

    with pytest.raises(module.HelperError, match="cannot be combined"):
        module._resolve_psks(plan, str(config))


def test_gcloud_secret_flag_uses_anonymous_flags_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_helper()
    plan = _plan(module)
    observed: dict[str, Any] = {}

    def fake_run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        secret = "process-list-secret"
        assert all(secret not in argument for argument in command)
        child_environment = options.get("env")
        assert isinstance(child_environment, dict)
        assert secret not in child_environment.values()
        assert not any(tunnel.psk_env_name in child_environment for tunnel in plan.tunnels)
        flags_argument = next(
            argument for argument in command if argument.startswith("--flags-file=")
        )
        flags_path = flags_argument.split("=", 1)[1]
        observed["payload"] = json.loads(Path(flags_path).read_text(encoding="utf-8"))
        observed["pass_fds"] = options.get("pass_fds")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    for tunnel in plan.tunnels:
        monkeypatch.setenv(tunnel.psk_env_name, f"secret-{tunnel.name}")
    monkeypatch.setenv("SOURCE_CONFIG_SECRET", "process-list-secret")
    cloud = module.GCloud(plan, secret_values=("process-list-secret",))
    cloud.mutate(
        ["compute", "vpn-tunnels", "create", "site-ha-tunnel-1"],
        label="create tunnel",
        secrets=("process-list-secret",),
        secret_flags={"--shared-secret": "process-list-secret"},
    )

    assert observed["payload"] == {"--shared-secret": "process-list-secret"}
    assert isinstance(observed["pass_fds"], tuple) and len(observed["pass_fds"]) == 1


def test_vm_ha_rejects_foreign_external_gateway_shape() -> None:
    module = _load_helper()
    plan = _plan(module)

    class FakeCloud:
        @staticmethod
        def describe_json(kind: str, name: str, *, regional: bool) -> dict[str, Any]:
            del kind, name, regional
            return {
                "redundancyType": "SINGLE_IP_INTERNALLY_REDUNDANT",
                "interfaces": [{"id": 0, "ipAddress": "203.0.113.10"}],
            }

    with pytest.raises(module.HelperError, match="foreign interface mapping"):
        module._validate_external_gateway(
            plan,
            FakeCloud(),
            plan.external_gateway_a,
            (plan.active_public_ip, plan.passive_public_ip),
            dry_run=False,
            status=False,
        )


def test_vm_ha_rejects_malformed_existing_ha_gateway_interfaces() -> None:
    module = _load_helper()
    plan = _plan(module)

    class FakeCloud:
        @staticmethod
        def describe_json(kind: str, name: str, *, regional: bool) -> dict[str, Any]:
            del kind, name, regional
            return {
                "network": "global/networks/fixture-network",
                "vpnInterfaces": [{"id": 0, "ipAddress": "198.51.100.10"}],
            }

    with pytest.raises(module.HelperError, match="interface mapping"):
        module._validate_gateway(plan, FakeCloud(), dry_run=False, status=False)


def test_vm_ha_rejects_foreign_tunnel_ike_version() -> None:
    module = _load_helper()
    plan = _plan(module)
    tunnel = plan.tunnels[0]

    class FakeCloud:
        @staticmethod
        def describe_json(kind: str, name: str, *, regional: bool) -> dict[str, Any]:
            del kind, name, regional
            return {
                "vpnGateway": "regions/europe-west9/vpnGateways/ha-gw-nebius",
                "peerExternalGateway": f"global/externalVpnGateways/{tunnel.external_gateway}",
                "router": "regions/europe-west9/routers/cr-nebius-ha",
                "ikeVersion": 1,
                "vpnGatewayInterface": tunnel.gcp_interface,
                "peerExternalGatewayInterface": tunnel.peer_interface,
            }

    with pytest.raises(module.HelperError, match="foreign binding"):
        module._validate_tunnel(plan, FakeCloud(), tunnel, dry_run=False, status=False)


def test_vm_ha_router_observation_rejects_malformed_asn() -> None:
    module = _load_helper()
    plan = _plan(module)

    class FakeCloud:
        @staticmethod
        def describe_json(kind: str, name: str, *, regional: bool) -> dict[str, Any]:
            del kind, name, regional
            return {
                "network": "global/networks/fixture-network",
                "bgp": {"asn": "not-an-integer"},
            }

    with pytest.raises(module.HelperError, match="invalid integer"):
        module._validate_router(plan, FakeCloud(), dry_run=False, status=False)


def test_vm_ha_describe_permission_error_fails_closed(tmp_path: Path) -> None:
    module = _load_helper()
    resources = _complete_resources(module)
    _executable, command_log = _fake_gcloud(tmp_path, resources)
    env = _subprocess_env(tmp_path, command_log, resources)
    env["FAKE_GCLOUD_DESCRIBE_ERROR"] = "ERROR: permission denied"

    result = subprocess.run(
        [str(SCRIPT), *_args(), "--status"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.returncode != 0
    assert "permission denied" in result.stderr
    assert "MISSING" not in result.stdout
