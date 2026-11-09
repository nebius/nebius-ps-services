"""Periodic ordinary route reconciliation inside the existing gateway agent."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from .. import ordinary_operations as operations
from .. import ordinary_route_binding as binding
from ..apply_approval import digest
from ..deploy.operator_guest import read_file
from ..deploy.operator_keys import protected_directory
from ..deploy.ordinary_route_discovery import Discovery
from ..deploy.ordinary_route_execute import execute
from ..deploy.ordinary_route_plan import inspect
from ..deploy.ordinary_route_policy import AbsenceObservations, Policy
from ..deploy.route_manager import RouteManager
from ..deploy.vm_ha_cloud import vm_ha_observation_budget, vm_ha_request_kwargs
from ..ha_tunnel_policy import connection_policies
from ..vm_ha_credentials import credential_ids_from_payload, read_vm_ha_credential_source
from . import ordinary
from .local_commands import CURRENT, CommandBudget, run
from .ordinary_route_authority import ROOT, RouteAuthority
from .ordinary_selection import require_quiescent
from .vm_ha.runtime import (
    RenewableNebiusSDK,
    _configured_bgp_summary_sessions,
    _confirmed_down_bgp_sessions,
    _established_bgp_sessions,
)
from .vm_ha.store import atomic_write_json
from .vm_ha.tunnel_observation import NativeTunnelObserver, route_destination
from .vm_ha.tunnels import Health

PERIOD_SECONDS = 15.0
TICK_BUDGET_SECONDS = 4.0
STATUS_SCHEMA = "nebius-vpngw.ordinary-route-status.v1"


def status(
    *, config_path: Path = ordinary.CONFIG, binding_path: Path = binding.PATH, root: Path = ROOT
) -> dict:
    """Read-only, generation-bound observation; never infer workload reachability."""
    result: dict = {
        "schema": STATUS_SCHEMA,
        "cloud_routes": "unknown",
        "connectivity": "unknown",
        "reason": "unavailable",
    }
    try:
        value = binding.read(binding_path)
        if value is None:
            return {**result, "reason": "runtime-not-configured"}
        boot = operations.BOOT.read_text().strip()
        owner = RouteAuthority(root).read()
        if owner is None or owner["binding"] != value:
            return {**result, "reason": "binding-unverified"}
        if owner["writer"] != "runtime":
            return {
                **result,
                "reason": "deployment-owned"
                if owner["writer"] == "deployment"
                else "workstation-reserved",
            }
        binding.authority(value, config_path.read_bytes(), boot)
        record = read_file(root / "status.json")
        if record is None or record["mode"] != 0o600 or record["uid"] != os.geteuid():
            return result
        observed = json.loads(record["content"])
        age = time.time() - observed["observed_at"]
        if (
            observed.get("generation") != value["generation"]
            or observed.get("boot_id") != boot
            or not 0 <= age <= PERIOD_SECONDS * 2 + TICK_BUDGET_SECONDS
        ):
            return {**result, "reason": "stale-observation"}
        if observed["cloud_routes"] != "verified":
            return {**result, "reason": "observation-incomplete"}
        return {
            **result,
            "cloud_routes": "verified",
            "reason": "current-observation",
            "generation": value["generation"],
            "routes": observed["routes"],
            "observed_at": observed["observed_at"],
        }
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return result


def _output(argv) -> str:
    result = run(argv, capture_output=True, text=True, timeout=TICK_BUDGET_SECONDS)
    if result.returncode or len(result.stdout.encode()) > 1024 * 1024:
        raise RuntimeError("Ordinary route native observation is unavailable")
    return result.stdout


class _BoundManager:
    """Discovery adapter reads exact generated Compute IDs, never names alone."""

    def __init__(self, sdk: Any, value: dict) -> None:
        self.sdk, self.value, self.project_id = sdk, value, value["project"]

    def _get_client(self):
        return self.sdk

    def get_ha_instance(self, identifier):
        from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceServiceClient

        return (
            InstanceServiceClient(self.sdk)
            .get(GetInstanceRequest(id=identifier), **vm_ha_request_kwargs())
            .wait()
        )

    def inspect_ordinary_targets(self, names):
        guest = self.value["infrastructure"]["gateways"]["0"]
        if tuple(names) != (guest["name"],):
            raise RuntimeError("Ordinary runtime route node changed")
        compute = self.get_ha_instance(guest["compute_id"])
        if compute.metadata.name != guest["name"]:
            raise RuntimeError("Ordinary runtime route Compute identity changed")
        return {guest["name"]: (guest["compute_id"], "")}


class RuntimeDiscovery(Discovery):
    def __init__(self, sdk: Any, value: dict, cfg: dict, journal) -> None:
        local = dict(cfg)
        local["gateway_group"] = {
            **cfg["gateway_group"],
            "network_id": value["infrastructure"]["network_id"],
            "subnet": {"name": value["gateway_subnet_name"]},
        }
        instance = SimpleNamespace(instance_index=0, hostname=value["node"])
        deployment = SimpleNamespace(iter_instance_configs=lambda: (instance,))
        super().__init__(
            _BoundManager(sdk, value),
            deployment,
            local,
            journal,
            RouteManager(value["project"]),
            summarize=value["policy"]["summarize"],
        )
        self.value, self.cfg = value, cfg

    def observe(self, *, completed=False) -> dict:
        result = super().observe(completed=completed)
        if (
            result["infrastructure"] != self.value["infrastructure"]
            or digest(result["policy"].mapping()) != digest(self.value["policy"])
            or result["conversions"]
        ):
            raise RuntimeError("Ordinary runtime route infrastructure or policy changed")
        return result

    def learned(self, policy: Policy) -> dict[str, tuple[str, ...]]:
        summary = json.loads(_output(["vtysh", "-c", "show bgp summary json"]))
        expected = {
            peer
            for connection in policy.connections
            if connection.mode == "bgp"
            for peer in connection.peers
        }
        configured = _configured_bgp_summary_sessions(summary)
        known = _established_bgp_sessions(summary) | _confirmed_down_bgp_sessions(summary)
        if configured is None or not expected <= configured or not expected <= known:
            raise RuntimeError("Ordinary BGP peer evidence is incomplete")
        rib = json.loads(_output(["vtysh", "-c", "show bgp ipv4 unicast json"]))
        return {
            connection.name: self.routes.selected_peer_routes(rib, peers=set(connection.peers))
            for connection in policy.connections
            if connection.mode == "bgp"
        }

    def encrypted(self, selected: dict[str, int]) -> None:
        encrypted_routes(self.cfg, selected)


def encrypted_routes(cfg: dict, selected, *, output=None) -> None:
    """Shared read-only exact CHILD/XFRM, BGP session and kernel route proof."""
    output = output or _output
    routes = json.loads(output(["ip", "-j", "-4", "route", "show", "table", "main"]))
    observer = NativeTunnelObserver(output)
    policies = connection_policies(cfg)
    for prefix in selected:
        entries = [r for r in routes if route_destination(r) == prefix]
        if len(entries) != 1 or entries[0].get("nexthops"):
            raise RuntimeError("Destination kernel route is ambiguous")
        entry = entries[0]
        choices = [
            (p, t)
            for p in policies
            for t in p.paths
            if t.interface == entry.get("dev")
            and (
                p.mode == "static"
                or entry.get("protocol") == "bgp"
                and entry.get("gateway") == t.peer
            )
        ]
        if len(choices) != 1:
            raise RuntimeError("Destination has no exact encrypted path")
        policy, path = choices[0]
        if (
            observer.observe(
                replace(policy, destinations=frozenset({prefix})), path.name, selected=True
            )
            is not Health.HEALTHY
        ):
            raise RuntimeError("Destination encryption is unproven")


class OrdinaryCloudRoutes:
    def __init__(
        self,
        *,
        config_path: Path = ordinary.CONFIG,
        binding_path: Path = binding.PATH,
        root: Path = ROOT,
        sdk_factory=RenewableNebiusSDK,
        discovery_factory=RuntimeDiscovery,
    ) -> None:
        self.config_path, self.binding_path, self.root = config_path, binding_path, root
        self.sdk_factory, self.discovery_factory = sdk_factory, discovery_factory
        self.sdk: Any = None
        self.sdk_identity: str | None = None
        self.absence = AbsenceObservations()

    def close(self) -> None:
        if self.sdk is not None:
            self.sdk.close()
            self.sdk, self.sdk_identity = None, None
        self.absence.unknown()

    @staticmethod
    def _credentials(value: dict) -> None:
        credentials = value["credentials"]
        payload = read_vm_ha_credential_source(Path(credentials["path"]), expected_uid=0)
        if hashlib.sha256(payload).hexdigest() != credentials[
            "sha256"
        ] or credential_ids_from_payload(payload) != (
            credentials["service_account"],
            credentials["authorized_key"],
        ):
            raise RuntimeError("Ordinary runtime route credential identity changed")

    def step(self) -> dict | None:
        try:
            return self._step()
        except Exception:
            self.absence.unknown()
            if self.root.exists():
                try:
                    protected_directory(self.root)
                    atomic_write_json(
                        self.root / "status.json",
                        {"observed_at": time.time(), "cloud_routes": "unknown"},
                    )
                except (OSError, ValueError):
                    logging.getLogger(__name__).error(
                        "Unknown route observation could not be persisted"
                    )
            raise

    def _step(self) -> dict | None:
        value = binding.read(self.binding_path)
        if value is None:
            self.close()
            return None
        with operations.mutation_lock():
            operations.require_idle()
            owner = RouteAuthority(self.root)
            current = owner.read()
            if current and current["binding"] == value and current["writer"] == "deployment":
                self.close()
                return None
            raw = self.config_path.read_bytes()
            cfg = yaml.safe_load(raw)
            if not isinstance(cfg, dict) or cfg.get("vm_ha") is not None:
                raise RuntimeError("Ordinary runtime route authority cannot operate HA")
            require_quiescent(cfg)
            boot = operations.BOOT.read_text().strip()
            identity = binding.authority(value, raw, boot)
            owner.require_runtime(value)
            self._credentials(value)
            if self.sdk_identity != identity:
                self.close()
                self.sdk = self.sdk_factory(
                    value["credentials"]["path"],
                    expected_service_account_id=value["credentials"]["service_account"],
                    expected_project_id=value["project"],
                )
                self.sdk_identity = identity
            journal = owner.journal(value)
            discovery = self.discovery_factory(self.sdk.client, value, cfg, journal)
            token = CURRENT.set(CommandBudget(time.monotonic() + TICK_BUDGET_SECONDS))
            try:
                with vm_ha_observation_budget(seconds=TICK_BUDGET_SECONDS):
                    return self._reconcile(
                        value, raw, boot, identity, cfg, owner, journal, discovery
                    )
            except Exception:
                self.absence.unknown()
                raise
            finally:
                CURRENT.reset(token)

    def _reconcile(self, value, raw, boot, identity, cfg, owner, journal, discovery) -> dict:
        def observe():
            operations.require_idle()
            owner.require_runtime(value)
            if (
                binding.read(self.binding_path) != value
                or self.config_path.read_bytes() != raw
                or operations.BOOT.read_text().strip() != boot
            ):
                raise RuntimeError("Ordinary runtime route authority changed")
            self._credentials(value)
            protection.verify()
            return discovery.observe()

        protection = ordinary.destination_protection(cfg)
        protection.verify()
        observed = observe()
        policy = observed["policy"]
        learned = discovery.learned(policy)
        # Cloud summaries need not exist in the kernel. Prove their constituent
        # destinations and admit only BGP destinations to the dynamic ledger;
        # configured Static destinations already have their own protection.
        native_policy = replace(policy, summarize=False)
        discovery.encrypted(native_policy.select(learned))
        dynamic_policy = replace(
            native_policy, connections=tuple(c for c in policy.connections if c.mode == "bgp")
        )
        protection.admit(frozenset(dynamic_policy.select(learned)))
        admitted = frozenset(
            record["snapshot"]["destination"] for record in journal.inventory().values()
        )
        absence = self.absence.observe(identity, policy, learned, admitted)
        pending = journal.transaction()
        if pending and pending["phase"] == "pending":
            plan = pending["plan"]
            if plan is None:
                raise RuntimeError("Runtime route selection is incomplete")
            # A saved cloud mutation is resumed only within its exact binding.
            if plan["options"].get("runtime_binding") != digest(value):
                raise RuntimeError("Pending runtime route effect belongs to another binding")
            approval = pending["approval"]
        else:
            plan = inspect(
                project=value["project"],
                gateway=value["gateway"],
                policy=policy,
                learned=learned,
                allocations=observed["allocations"],
                active_tables=observed["active_tables"],
                routes=observed["routes"],
                ownership=journal.inventory(),
                infrastructure=observed["infrastructure"],
                absence=absence,
            )
            plan["options"]["runtime_binding"] = digest(value)
            approval = digest([identity, plan])
        if any(
            item["type"] not in {"CreateRouteRequest", "DeleteRouteRequest"}
            or item["type"] == "DeleteRouteRequest"
            and item["reason"] != "bgp-withdrawn"
            for item in plan["requests"].values()
        ):
            raise RuntimeError("Runtime route effects exceed generated destination authority")

        def admit_request(request):
            observe()
            if type(request).__name__ == "DeleteRouteRequest":
                destination = next(
                    item["before"]["destination"]
                    for item in plan["requests"].values()
                    if item["type"] == "DeleteRouteRequest" and item["request"]["id"] == request.id
                )
                try:
                    current = discovery.learned(policy)
                    if not absence.admits(policy, current, destination):
                        raise RuntimeError(
                            "Runtime withdrawal no longer has three complete absence observations"
                        )
                except Exception:
                    self.absence.unknown()
                    raise

        execute(
            self.sdk.client,
            journal,
            plan,
            approval=approval,
            observe=observe,
            admit_request=admit_request,
        )
        status = {
            "generation": value["generation"],
            "boot_id": boot,
            "observed_at": time.time(),
            "routes": len(journal.inventory()),
            "connectivity": "unknown",
            "cloud_routes": "verified",
        }
        atomic_write_json(self.root / "status.json", status)
        return status
