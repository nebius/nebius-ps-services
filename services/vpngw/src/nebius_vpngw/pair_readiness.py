"""Read-only regional readiness, sharing exact native and cloud route observers."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import shlex
import subprocess
from pathlib import Path

import yaml

from .deploy.apply_diagnostics import Failure


class ReadinessFailure(RuntimeError):
    def __init__(self, phase: str, predicate: str):
        self.failure = Failure(phase, predicate)
        super().__init__(f"Paired readiness failed: {phase}/{predicate}")


def checked(phase, predicate, call, *args, **kwargs):
    """Bound diagnostics to declared predicates, never raw cloud/SSH errors."""
    try:
        return call(*args, **kwargs)
    except ReadinessFailure:
        raise
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        StopIteration,
        subprocess.TimeoutExpired,
    ):
        raise ReadinessFailure(phase, predicate) from None


def native(generation: str, required: list[str]) -> dict:
    """Guest observation: no reconciliation, initiation, service or cloud effects."""
    from .agent import ordinary
    from .agent.ordinary_cloud_routes import encrypted_routes

    raw = ordinary.CONFIG.read_bytes()
    if hashlib.sha256(raw).hexdigest() != generation:
        raise RuntimeError("Paired native configuration generation changed")
    cfg = yaml.safe_load(raw)
    if Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() != "1":
        raise RuntimeError("Paired gateway forwarding remains fenced")
    if cfg.get("vm_ha") is None:
        ordinary.destination_protection(cfg).verify()
    encrypted_routes(cfg, required)
    if ordinary.CONFIG.read_bytes() != raw:
        raise RuntimeError("Paired native configuration changed during observation")
    return {
        "generation": generation,
        "required_prefixes": sorted(required),
        "encrypted_routes": "verified",
    }


def _native(context, instance, target, required):
    from . import cli

    expected = {
        "generation": hashlib.sha256(instance.config_yaml.encode()).hexdigest(),
        "required_prefixes": sorted(required),
        "encrypted_routes": "verified",
    }
    source = (
        "import json,sys;from nebius_vpngw.pair_readiness import native;"
        "request=json.loads(sys.stdin.readline(65537));"
        "print(json.dumps(native(request['generation'],request['required_prefixes']),sort_keys=True))"
    )
    argv = cli._status_ssh_target_command(context, hostname=instance.hostname, target=target)
    result = subprocess.run(
        [*argv, "sudo -n /usr/bin/python3 -B -c " + shlex.quote(source)],
        input=json.dumps(expected),
        text=True,
        capture_output=True,
        timeout=45,
    )
    if result.returncode or len(result.stdout) > 65536 or json.loads(result.stdout) != expected:
        raise RuntimeError("Paired encrypted route observation is incomplete")
    return expected


def cloud_coverage(observed: dict, required: list[str]) -> None:
    from .deploy.ordinary_route_ownership import allocation_id

    if not observed["active_tables"]:
        raise RuntimeError("Paired workload route targets are unavailable")
    allocation = observed["allocations"][0]
    for table in observed["active_tables"]:
        rows = observed["routes"][table]
        for prefix in required:
            exact = [route for route in rows if route.spec.destination.cidr == prefix]
            if len(exact) != 1 or allocation_id(exact[0]) != allocation:
                raise RuntimeError("Paired workload route is missing or has a different next hop")
            network = ipaddress.IPv4Network(prefix)
            for route in rows:
                destination = ipaddress.IPv4Network(route.spec.destination.cidr)
                if destination.subnet_of(network) and allocation_id(route) != allocation:
                    raise RuntimeError(
                        "Paired workload route has an overriding foreign destination"
                    )


def ha_cloud_coverage(state, observed: dict, required: list[str]) -> None:
    from . import cli

    if cli._vm_ha_cloud_authority(state, observed).condition != "exact":
        raise RuntimeError("Paired HA cloud authority is not exact")
    if not observed["route_targets"]:
        raise RuntimeError("Paired HA workload route targets are unavailable")
    for target in observed["route_targets"]:
        rows = [
            row for row in observed["routes"] if row["route_table_id"] == target["route_table_id"]
        ]
        for prefix in required:
            exact = [row for row in rows if row["prefix"] == prefix]
            if len(exact) != 1 or exact[0]["allocation_id"] != state.allocation_id:
                raise RuntimeError(
                    "Paired HA workload prefix lacks its exact shared-allocation route"
                )
            if any(
                ipaddress.IPv4Network(row["prefix"]).subnet_of(ipaddress.IPv4Network(prefix))
                and row["allocation_id"] != state.allocation_id
                for row in rows
            ):
                raise RuntimeError("Paired HA workload prefix has an overriding foreign route")


def _manager(local, plan):
    from . import cli

    token = cli._ensure_authentication(required=False, show_progress=False)
    return cli._own_vm_manager(
        cli.VMManager(
            project_id=local["project_id"],
            region=plan.gateway_group.region,
            auth_token=token,
            tenant_id=local.get("tenant_id"),
            region_id=plan.gateway_group.region,
        )
    ), token


def observe(path: Path) -> dict:
    from . import cli

    return checked(
        "pair-configuration",
        "configuration-unavailable",
        cli._with_vm_manager_lifetimes(_observe),
        path,
    )


def _observe(path: Path) -> dict:
    from . import cli
    from .config_loader import load_local_config, merge_with_peer_configs

    local = load_local_config(path)
    plan = merge_with_peer_configs(local, [])
    required = sorted(
        {
            prefix
            for connection in local["connections"]
            for prefix in connection.get("remote_prefixes", [])
        }
    )
    if not required:
        raise RuntimeError("Paired readiness requires reciprocal remote prefixes")
    manager, token = checked("pair-cloud", "cloud-client-unavailable", _manager, local, plan)
    if plan.vm_ha is not None:
        inspected = checked(
            "pair-owner",
            "ownership-observation-unavailable",
            cli._inspect_vm_ha_command_status,
            path,
            require_resolved_config=True,
        )
        snapshot = inspected.snapshot
        if snapshot.view.overall != "HEALTHY":
            raise ReadinessFailure("pair-owner", "regional-health-not-verified")
        name = snapshot.authority.owner_name
        if not name or not inspected.targets or snapshot.lifecycle_state is None:
            raise ReadinessFailure("pair-owner", "owner-identity-unavailable")
        checked(
            "pair-cloud",
            "required-route-coverage-unverified",
            ha_cloud_coverage,
            snapshot.lifecycle_state,
            checked(
                "pair-cloud",
                "cloud-observation-unavailable",
                manager.observe_vm_ha_migration_state,
                plan.gateway_group,
                local["gateway"]["local_prefixes"],
            ),
            required,
        )
        instance = next(item for item in plan.iter_instance_configs() if item.hostname == name)
        native_proof = checked(
            "pair-native",
            "encrypted-forwarding-unverified",
            _native,
            inspected.ssh_context,
            instance,
            inspected.targets[name],
            required,
        )
        again = checked(
            "pair-owner",
            "ownership-observation-unavailable",
            cli._inspect_vm_ha_command_status,
            path,
            require_resolved_config=True,
        )
        if (
            again.snapshot.view.overall != "HEALTHY"
            or again.snapshot.authority.owner_name != name
            or not snapshot.authority.ownership_digest
            or again.snapshot.authority.ownership_digest != snapshot.authority.ownership_digest
        ):
            raise ReadinessFailure("pair-owner", "owner-changed-during-verification")
        checked(
            "pair-cloud",
            "required-route-coverage-unverified",
            ha_cloud_coverage,
            snapshot.lifecycle_state,
            checked(
                "pair-cloud",
                "cloud-observation-unavailable",
                manager.observe_vm_ha_migration_state,
                plan.gateway_group,
                local["gateway"]["local_prefixes"],
            ),
            required,
        )
    else:
        from .deploy.ordinary_route_discovery import Discovery
        from .deploy.ordinary_route_ownership import RouteJournal
        from .deploy.ordinary_runtime_handoff import Handoff, ObservedJournal, same_completed_routes
        from .deploy.route_manager import RouteManager
        from .deploy.ssh_push import SSHPush

        project = local["project_id"]
        instances = tuple(plan.iter_instance_configs())
        if len(instances) != 1:
            raise RuntimeError("Paired ordinary readiness requires exactly one member")
        instance = instances[0]
        targets = manager.inspect_ordinary_targets([instance.hostname])
        context = cli._build_status_ssh_context(
            local, plan, {name: value[1] for name, value in targets.items()}, project_id=project
        )
        target = targets[instance.hostname][1]
        policy = context.policies[instance.hostname]
        link = Handoff(SSHPush(ssh_policy=policy), target, instance, local)
        before = checked("pair-writer", "runtime-writer-unavailable", link.inspect)
        authority = before["authority"]
        if authority is not None and authority["writer"] != "runtime":
            raise ReadinessFailure("pair-writer", "runtime-writer-reserved")
        ledger = (
            ObservedJournal(project, plan.gateway_group.name, before["journal"])
            if authority
            else RouteJournal(project, plan.gateway_group.name)
        )
        discovery = Discovery(
            manager, plan, local, ledger, RouteManager(project, auth_token=token, ssh_policy=policy)
        )
        observed = checked("pair-cloud", "cloud-observation-unavailable", discovery.observe)
        if authority and observed["infrastructure"] != authority["binding"]["infrastructure"]:
            raise ReadinessFailure("pair-writer", "runtime-binding-changed")
        checked(
            "pair-cloud", "required-route-coverage-unverified", cloud_coverage, observed, required
        )
        native_proof = checked(
            "pair-native",
            "encrypted-forwarding-unverified",
            _native,
            context,
            instance,
            target,
            required,
        )
        checked(
            "pair-cloud",
            "required-route-coverage-unverified",
            cloud_coverage,
            checked("pair-cloud", "cloud-observation-unavailable", discovery.observe),
            required,
        )
        if not same_completed_routes(
            before, checked("pair-writer", "runtime-writer-unavailable", link.inspect)
        ):
            raise ReadinessFailure("pair-writer", "runtime-writer-changed")
    return {
        "configuration": "verified",
        "connection": "ready",
        "cloud_routes": "verified",
        "native": native_proof,
        "connectivity": "unknown",
    }
