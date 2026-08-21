from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = PROJECT_ROOT / "misc" / "gcp_vpngw_classic_vm_ha.py"
ENTRYPOINT_PATH = PROJECT_ROOT / "misc" / "gcp-vpngw.sh"


def _load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gcp_vpngw_classic_vm_ha", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _arguments(**overrides: Any) -> Namespace:
    values: dict[str, Any] = {
        "connection_name": "site-static-ha",
        "gcp_project_id": "gcp-test-project",
        "region": "us-test1",
        "network": "static-test-vpc",
        "nebius_active_public_ip": "203.0.113.10",
        "nebius_passive_public_ip": "203.0.113.11",
        "gcp_prefix": ["10.40.0.0/24"],
        "nebius_prefix": ["10.50.0.0/24"],
        "inner_cidr_a": "169.254.240.0/30",
        "inner_cidr_b": "169.254.240.4/30",
        "active_priority": "1000",
        "passive_priority": "2000",
    }
    values.update(overrides)
    return Namespace(**values)


def _plan(module: ModuleType, **overrides: Any) -> Any:
    return module.build_plan(_arguments(**overrides))


def _empty_resources(plan: Any) -> dict[tuple[str, str], None]:
    resources: dict[tuple[str, str], None] = {}
    for path in plan.paths:
        resources[("addresses", path.address)] = None
        resources[("target-vpn-gateways", path.gateway)] = None
        resources[("forwarding-rules", path.forwarding_esp)] = None
        resources[("forwarding-rules", path.forwarding_udp500)] = None
        resources[("forwarding-rules", path.forwarding_udp4500)] = None
        resources[("vpn-tunnels", path.tunnel)] = None
        for route in path.routes:
            resources[("routes", route.name)] = None
    return resources


def _resource_key(arguments: list[str]) -> tuple[str, str]:
    assert arguments[0] == "compute"
    assert arguments[2] == "create"
    return arguments[1], arguments[3]


def test_plan_is_two_isolated_classic_paths_without_router_resources() -> None:
    module = _load_helper()
    plan = _plan(module)

    assert len(plan.paths) == 2
    assert {path.vm_index for path in plan.paths} == {0, 1}
    assert len({path.gateway for path in plan.paths}) == 2
    assert len({path.tunnel for path in plan.paths}) == 2
    assert len({path.peer_public_ip for path in plan.paths}) == 2
    assert [path.routes[0].priority for path in plan.paths] == [1000, 2000]
    assert "router" not in repr(plan).lower()
    assert "bgp" not in repr(plan).lower()


def test_single_port_range_normalization_is_exact() -> None:
    module = _load_helper()

    assert module._ports(None, "500-500") == {"500"}
    assert module._ports(None, "4500-4500") == {"4500"}
    assert module._ports(None, "500-501") == set()


def test_prefix_order_is_stable_for_retained_route_names() -> None:
    module = _load_helper()

    assert module._prefixes(
        ["172.16.31.0/28", "10.96.0.41/32", "172.16.31.0/28"],
        "Nebius local prefix",
    ) == ("172.16.31.0/28", "10.96.0.41/32")


def test_create_builds_two_one_to_one_classic_graphs() -> None:
    module = _load_helper()
    plan = _plan(
        module,
        nebius_prefix=["10.50.0.0/24", "10.51.0.0/24"],
    )
    resources = _empty_resources(plan)
    secret_values = {path.psk_env_name: f"secret-{path.label}" for path in plan.paths}

    class RecordingCloud:
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], dict[str, Any]]] = []

        def mutate(self, arguments: list[str], **options: Any) -> None:
            self.calls.append((arguments, options))

    cloud = RecordingCloud()
    module._create(plan, cloud, resources, secret_values)

    flattened = [argument for arguments, _ in cloud.calls for argument in arguments]
    assert "routers" not in flattened
    assert "router-interfaces" not in flattened
    assert "router-peers" not in flattened
    assert (
        sum(arguments[:3] == ["compute", "addresses", "create"] for arguments, _ in cloud.calls)
        == 2
    )
    assert (
        sum(
            arguments[:3] == ["compute", "target-vpn-gateways", "create"]
            for arguments, _ in cloud.calls
        )
        == 2
    )
    forwarding_calls = [
        call for call in cloud.calls if call[0][:3] == ["compute", "forwarding-rules", "create"]
    ]
    assert len(forwarding_calls) == 6
    assert all(
        not any(argument.startswith("--network=") for argument in arguments)
        for arguments, _ in forwarding_calls
    )
    assert all("--network-tier=PREMIUM" in arguments for arguments, _ in forwarding_calls)
    assert all("--load-balancing-scheme=EXTERNAL" in arguments for arguments, _ in forwarding_calls)
    tunnel_calls = [
        call for call in cloud.calls if call[0][:3] == ["compute", "vpn-tunnels", "create"]
    ]
    route_calls = [call for call in cloud.calls if call[0][:3] == ["compute", "routes", "create"]]
    assert len(tunnel_calls) == 2
    assert len(route_calls) == 4
    infrastructure_call_indexes = [
        index for index, (arguments, _) in enumerate(cloud.calls) if arguments[1] != "routes"
    ]
    route_call_indexes = [
        index for index, (arguments, _) in enumerate(cloud.calls) if arguments[1] == "routes"
    ]
    assert max(infrastructure_call_indexes) < min(route_call_indexes)
    for arguments, options in tunnel_calls:
        assert all("secret-" not in argument for argument in arguments)
        assert set(options["secret_flags"]) == {"--shared-secret"}
    expected_routes = [route for path in plan.paths for route in path.routes]
    assert [arguments[3] for arguments, _ in route_calls] == [
        route.name for route in expected_routes
    ]
    for path in plan.paths:
        path_route_calls = route_calls[
            path.vm_index * len(path.routes) : (path.vm_index + 1) * len(path.routes)
        ]
        for route, (arguments, _) in zip(path.routes, path_route_calls, strict=True):
            assert f"--destination-range={route.prefix}" in arguments
            assert f"--priority={route.priority}" in arguments
            assert f"--next-hop-vpn-tunnel={path.tunnel}" in arguments


def test_create_path_b_tunnel_failure_creates_no_routes() -> None:
    module = _load_helper()
    plan = _plan(module)
    resources = _empty_resources(plan)
    secret_values = {path.psk_env_name: f"secret-{path.label}" for path in plan.paths}

    class FailingCloud:
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], dict[str, Any]]] = []

        def mutate(self, arguments: list[str], **options: Any) -> None:
            self.calls.append((arguments, options))
            if arguments[:4] == [
                "compute",
                "vpn-tunnels",
                "create",
                plan.paths[1].tunnel,
            ]:
                raise module.HelperError("injected path B tunnel failure")

    cloud = FailingCloud()
    with pytest.raises(module.HelperError, match="path B tunnel failure"):
        module._create(plan, cloud, resources, secret_values)

    tunnel_calls = [
        call for call in cloud.calls if call[0][:3] == ["compute", "vpn-tunnels", "create"]
    ]
    assert [arguments[3] for arguments, _ in tunnel_calls] == [
        plan.paths[0].tunnel,
        plan.paths[1].tunnel,
    ]
    assert not any(arguments[1] == "routes" for arguments, _ in cloud.calls)
    for arguments, options in tunnel_calls:
        assert all("secret-" not in argument for argument in arguments)
        assert set(options["secret_flags"]) == {"--shared-secret"}


@pytest.mark.parametrize(
    "scenario",
    ("path-a-infrastructure", "mixed-infrastructure", "partial-routes"),
)
def test_create_resumes_compatible_partial_graphs_in_two_phases(scenario: str) -> None:
    module = _load_helper()
    plan = _plan(
        module,
        nebius_prefix=["10.50.0.0/24", "10.51.0.0/24"],
    )
    complete = _complete_graph(plan)
    resources: dict[tuple[str, str], dict[str, Any] | None] = {key: None for key in complete}
    route_keys = {("routes", route.name) for path in plan.paths for route in path.routes}
    infrastructure_keys = set(resources) - route_keys

    if scenario == "path-a-infrastructure":
        for key in infrastructure_keys:
            if key[1] in {
                plan.paths[0].address,
                plan.paths[0].gateway,
                plan.paths[0].forwarding_esp,
                plan.paths[0].forwarding_udp500,
                plan.paths[0].forwarding_udp4500,
                plan.paths[0].tunnel,
            }:
                resources[key] = complete[key]
    elif scenario == "mixed-infrastructure":
        present = {
            ("addresses", plan.paths[0].address),
            ("target-vpn-gateways", plan.paths[0].gateway),
            ("forwarding-rules", plan.paths[0].forwarding_esp),
            ("forwarding-rules", plan.paths[0].forwarding_udp500),
            ("addresses", plan.paths[1].address),
            ("target-vpn-gateways", plan.paths[1].gateway),
        }
        for key in present:
            resources[key] = complete[key]
    else:
        for key in infrastructure_keys:
            resources[key] = complete[key]
        for path in plan.paths:
            resources[("routes", path.routes[0].name)] = complete[("routes", path.routes[0].name)]

    class RecordingCloud:
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], dict[str, Any]]] = []

        def mutate(self, arguments: list[str], **options: Any) -> None:
            self.calls.append((arguments, options))

    cloud = RecordingCloud()
    secrets = {path.psk_env_name: f"secret-{path.label}" for path in plan.paths}
    module._create(plan, cloud, resources, secrets)

    expected_missing = {key for key, value in resources.items() if value is None}
    actual_mutations = [_resource_key(arguments) for arguments, _ in cloud.calls]
    assert len(actual_mutations) == len(set(actual_mutations))
    assert set(actual_mutations) == expected_missing
    infrastructure_indexes = [
        index for index, key in enumerate(actual_mutations) if key[0] != "routes"
    ]
    route_indexes = [index for index, key in enumerate(actual_mutations) if key[0] == "routes"]
    if infrastructure_indexes and route_indexes:
        assert max(infrastructure_indexes) < min(route_indexes)
    assert [key for key in actual_mutations if key[0] == "routes"] == [
        ("routes", route.name)
        for path in plan.paths
        for route in path.routes
        if resources[("routes", route.name)] is None
    ]


def test_complete_graph_is_idempotent() -> None:
    module = _load_helper()
    plan = _plan(module)
    resources = {key: {"present": True} for key in _empty_resources(plan)}

    class RejectMutationCloud:
        @staticmethod
        def mutate(arguments: list[str], **options: Any) -> None:
            del arguments, options
            raise AssertionError("complete graph must not be changed")

    module._create(plan, RejectMutationCloud(), resources, {})


def test_missing_secrets_are_rejected_before_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    plan = _plan(module)
    resources = _empty_resources(plan)
    for path in plan.paths:
        monkeypatch.delenv(path.psk_env_name, raising=False)

    with pytest.raises(module.HelperError, match="missing secret input"):
        module._resolve_psks(plan, resources)


def test_secret_flag_uses_anonymous_descriptor_and_scrubbed_environment(
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
        assert not any(path.psk_env_name in child_environment for path in plan.paths)
        flags_argument = next(
            argument for argument in command if argument.startswith("--flags-file=")
        )
        observed["payload"] = json.loads(
            Path(flags_argument.split("=", 1)[1]).read_text(encoding="utf-8")
        )
        observed["pass_fds"] = options.get("pass_fds")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    for path in plan.paths:
        monkeypatch.setenv(path.psk_env_name, f"secret-{path.label}")
    monkeypatch.setenv("UNRELATED_SECRET_SOURCE", "process-list-secret")
    cloud = module.GCloud(plan, secret_values=("process-list-secret",))
    cloud.mutate(
        ["compute", "vpn-tunnels", "create", plan.paths[0].tunnel],
        label="create tunnel",
        secret_flags={"--shared-secret": "process-list-secret"},
    )

    assert observed["payload"] == {"--shared-secret": "process-list-secret"}
    assert isinstance(observed["pass_fds"], tuple) and len(observed["pass_fds"]) == 1


def test_foreign_existing_route_is_rejected() -> None:
    module = _load_helper()
    plan = _plan(module)

    class FakeCloud:
        @staticmethod
        def describe(kind: str, name: str, *, regional: bool) -> dict[str, Any] | None:
            del regional
            if kind == "routes":
                return {
                    "network": f"global/networks/{plan.network}",
                    "destRange": "192.0.2.0/24",
                    "nextHopVpnTunnel": f"regions/{plan.region}/vpnTunnels/{plan.paths[0].tunnel}",
                    "priority": 1000,
                }
            return None

    with pytest.raises(module.HelperError, match="foreign binding"):
        module._inspect(plan, FakeCloud())


def _complete_graph(plan: Any) -> dict[tuple[str, str], dict[str, Any]]:
    resources: dict[tuple[str, str], dict[str, Any]] = {}
    for index, path in enumerate(plan.paths, start=10):
        address_value = f"192.0.2.{index}"
        resources[("addresses", path.address)] = {
            "address": address_value,
            "addressType": "EXTERNAL",
            "networkTier": "PREMIUM",
            "region": f"regions/{plan.region}",
        }
        resources[("target-vpn-gateways", path.gateway)] = {
            "network": f"global/networks/{plan.network}"
        }
        forwarding = (
            (path.forwarding_esp, "ESP", None),
            (path.forwarding_udp500, "UDP", "500-500"),
            (path.forwarding_udp4500, "UDP", "4500-4500"),
        )
        for name, protocol, port_range in forwarding:
            rule = {
                "IPAddress": address_value,
                "IPProtocol": protocol,
                "loadBalancingScheme": "EXTERNAL",
                "networkTier": "PREMIUM",
                "target": f"regions/{plan.region}/targetVpnGateways/{path.gateway}",
            }
            if port_range is not None:
                rule["portRange"] = port_range
            resources[("forwarding-rules", name)] = rule
        resources[("vpn-tunnels", path.tunnel)] = {
            "ikeVersion": 2,
            "localTrafficSelector": ["0.0.0.0/0"],
            "peerIp": path.peer_public_ip,
            "remoteTrafficSelector": ["0.0.0.0/0"],
            "targetVpnGateway": f"regions/{plan.region}/targetVpnGateways/{path.gateway}",
        }
        for route in path.routes:
            resources[("routes", route.name)] = {
                "destRange": route.prefix,
                "network": f"global/networks/{plan.network}",
                "nextHopVpnTunnel": f"regions/{plan.region}/vpnTunnels/{path.tunnel}",
                "nextHopVpnTunnelRegion": f"regions/{plan.region}",
                "priority": route.priority,
            }
    return resources


def test_complete_premium_graph_inspection_is_idempotent() -> None:
    module = _load_helper()
    plan = _plan(module)
    resources = _complete_graph(plan)

    class Cloud:
        @staticmethod
        def describe(kind: str, name: str, *, regional: bool) -> dict[str, Any]:
            del regional
            return resources[(kind, name)]

        @staticmethod
        def mutate(arguments: list[str], **options: Any) -> None:
            del arguments, options
            raise AssertionError("complete Premium graph must not be changed")

    inspected = module._inspect(plan, Cloud())
    module._create(plan, Cloud(), inspected, {})


@pytest.mark.parametrize(
    ("kind", "field", "value"),
    (
        ("addresses", "networkTier", None),
        ("addresses", "networkTier", "STANDARD"),
        ("forwarding-rules", "networkTier", None),
        ("forwarding-rules", "networkTier", "STANDARD"),
        ("forwarding-rules", "loadBalancingScheme", None),
        ("forwarding-rules", "loadBalancingScheme", "INTERNAL"),
    ),
)
def test_foreign_classic_tier_or_scheme_fails_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    field: str,
    value: str | None,
) -> None:
    module = _load_helper()
    mutation_calls: list[list[str]] = []

    class Cloud:
        def __init__(self, plan: Any, *, secret_values: tuple[str, ...] = ()) -> None:
            del secret_values
            self.plan = plan
            self.resources = _complete_graph(plan)
            target_name = (
                plan.paths[0].address if kind == "addresses" else plan.paths[0].forwarding_esp
            )
            target = self.resources[(kind, target_name)]
            if value is None:
                target.pop(field)
            else:
                target[field] = value

        @staticmethod
        def require_auth() -> None:
            return None

        def describe(self, resource_kind: str, name: str, *, regional: bool) -> dict[str, Any]:
            del regional
            return self.resources[(resource_kind, name)]

        @staticmethod
        def mutate(arguments: list[str], **options: Any) -> None:
            del options
            mutation_calls.append(arguments)

    monkeypatch.setattr(module, "GCloud", Cloud)
    result = module.main(
        [
            "--connection-name=site-static-ha",
            "--gcp-project-id=gcp-test-project",
            "--region=us-test1",
            "--network=static-test-vpc",
            "--nebius-active-public-ip=203.0.113.10",
            "--nebius-passive-public-ip=203.0.113.11",
            "--gcp-prefix=10.40.0.0/24",
            "--nebius-prefix=10.50.0.0/24",
            "--yes",
        ]
    )

    assert result == 1
    assert mutation_calls == []


@pytest.mark.parametrize("mode", ("--status", "--dry-run"))
def test_main_read_only_modes_never_mutate_missing_graph(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    module = _load_helper()

    class Cloud:
        def __init__(self, plan: Any, *, secret_values: tuple[str, ...] = ()) -> None:
            del plan, secret_values

        @staticmethod
        def require_auth() -> None:
            return None

        @staticmethod
        def describe(kind: str, name: str, *, regional: bool) -> None:
            del kind, name, regional
            return None

        @staticmethod
        def mutate(arguments: list[str], **options: Any) -> None:
            del arguments, options
            raise AssertionError("read-only Classic mode must not mutate")

    monkeypatch.setattr(module, "GCloud", Cloud)
    result = module.main(
        [
            "--connection-name=site-static-ha",
            "--gcp-project-id=gcp-test-project",
            "--region=us-test1",
            "--network=static-test-vpc",
            "--nebius-active-public-ip=203.0.113.10",
            "--nebius-passive-public-ip=203.0.113.11",
            "--gcp-prefix=10.40.0.0/24",
            "--nebius-prefix=10.50.0.0/24",
            mode,
        ]
    )

    assert result == 0


def test_main_entrypoint_delegates_classic_help() -> None:
    result = subprocess.run(
        [str(ENTRYPOINT_PATH), "--classic-vm-ha-peer", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "NO_COLOR": "1"},
    )

    assert result.returncode == 0
    assert "two isolated one-to-one GCP Classic VPN paths" in result.stdout
