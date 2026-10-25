"""Transactional NAT wizard; provider choices are live, defaults are preferences."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from .nat_gateway import policy_snapshot, routing_preview, validate_nat_config
from .nat_gateway_sdk import NatGatewaySDK
from .provider_options import OptionChoice, ProviderOptionLookup
from .quota_checks import assess_live_quotas, format_quota_report_lines


class WizardNavigation(Exception):
    def __init__(self, value: Any, stopped: bool) -> None:
        self.value, self.stopped = value, stopped


def configure_nat(
    payload: dict[str, Any],
    row: dict[str, Any],
    *,
    prefix: str,
    lookup: ProviderOptionLookup,
    prompt: Callable[..., tuple[Any, bool]],
    is_back: Callable[[Any], bool],
    emit: Callable[[str], None],
    sdk_factory: Callable[..., Any] = NatGatewaySDK,
) -> dict[str, Any]:
    result = copy.deepcopy(row)
    inputs = result.setdefault("inputs", {})
    scope = payload["client_info"]["nebius"]
    project, region = scope["project_id"], scope["region_id"]
    inputs["parent_id"] = project

    def ask(
        key: str, default: Any, choices: list[OptionChoice] | None = None, type_hint: str = "string"
    ) -> Any:
        if choices == []:
            raise RuntimeError(
                f"No live choices for NAT {key}; {lookup.last_error() or 'inventory is empty'}"
            )
        if (
            choices is not None
            and default
            and not isinstance(default, list)
            and default not in {choice.value for choice in choices}
        ):
            emit(f"Preferred {key} value '{default}' is unavailable; select a live choice.")
        value, stop = prompt(
            f"{prefix}.{key}", default, choices=choices, type_hint=type_hint, required=True
        )
        if stop or is_back(value):
            raise WizardNavigation(value, stop)
        if choices is not None:
            selected = value if isinstance(value, list) else [value]
            if any(item not in {c.value for c in choices} for item in selected):
                raise ValueError(f"NAT {key} must come from the live choices")
        return value

    def provider(name: str, **args: Any) -> list[OptionChoice]:
        return lookup.resolve(
            provider=name,
            args={"project_id": project, **args},
            payload=payload,
            field_path=f"{prefix}.inputs.{name}",
        )

    inputs["name"] = ask("inputs.name", inputs.get("name") or result["instance_id"])
    platforms = provider("compute_platforms", cpu_only=True)
    inputs["platform"] = ask("inputs.platform", inputs.get("platform", "cpu-d3"), platforms)
    presets = provider("compute_platform_presets", cpu_only=True, platform=inputs["platform"])
    inputs["preset"] = ask("inputs.preset", inputs.get("preset", "4vcpu-16gb"), presets)
    with sdk_factory(project) as sdk:
        sdk.cpu_preset(inputs["platform"], inputs["preset"])
        images = sdk.image_choices(region, inputs["platform"], inputs["preset"])
        choices = [OptionChoice(r["id"], r["name"], index == 0) for index, r in enumerate(images)]
        inputs["source_image_id"] = ask(
            "inputs.source_image_id", inputs.get("source_image_id"), choices
        )
        sdk.image(inputs["source_image_id"], platform=inputs["platform"], preset=inputs["preset"])
        minimum = next(r["minimum_gib"] for r in images if r["id"] == inputs["source_image_id"])
        inputs["boot_disk_type"] = ask(
            "inputs.boot_disk_type",
            inputs.get("boot_disk_type", "NETWORK_SSD"),
            provider("compute_boot_disk_types"),
        )
        inputs["boot_disk_size_gib"] = ask(
            "inputs.boot_disk_size_gib",
            max(minimum, inputs.get("boot_disk_size_gib", 20)),
            type_hint="number",
        )
        if inputs["boot_disk_size_gib"] < minimum:
            raise ValueError(f"The selected image requires at least {minimum} GiB")
        vpcs = {
            r["instance_id"]: r
            for r in payload.get("infra", {}).get("components", [])
            if r.get("id") == "vpc" and r.get("enabled")
        }
        live_networks = provider("project_networks")
        managed_live_ids = {
            choice.value
            for choice in live_networks
            if any(
                choice.value == v["inputs"]["network"].get("existing_id")
                or (
                    v["inputs"]["network"].get("name")
                    and choice.metadata.get("name") == v["inputs"]["network"].get("name")
                )
                for v in vpcs.values()
            )
        }
        networks = [choice for choice in live_networks if choice.value not in managed_live_ids]
        networks += [OptionChoice(f"planned:{key}", f"Deployment VPC: {key}") for key in vpcs]
        binding = result.get("bindings", {}).get("inputs.network_id", {})
        current = f"planned:{binding['source_instance']}" if binding else inputs.get("network_id")
        selected = ask("inputs.network_id", current, networks)
        planned = (
            vpcs.get(selected.removeprefix("planned:")) if selected.startswith("planned:") else None
        )
        network_id = (
            selected if planned is None else planned["inputs"]["network"].get("existing_id", "")
        )
        if planned is not None and not network_id:
            candidates = [
                choice.value
                for choice in live_networks
                if choice.metadata.get("name") == planned["inputs"]["network"].get("name")
            ]
            if len(candidates) > 1:
                raise RuntimeError("Deployment network name is ambiguous in live inventory")
            network_id = candidates[0] if candidates else ""
        if planned is not None:
            result.setdefault("bindings", {})["inputs.network_id"] = {
                "source_component": "vpc",
                "source_instance": planned["instance_id"],
                "source_output": "network_id",
            }
            inputs.pop("network_id", None)
        else:
            result.get("bindings", {}).pop("inputs.network_id", None)
            inputs["network_id"] = network_id
        if network_id:
            ranges = sdk.network_cidrs(network_id)
        else:
            assert planned is not None
            network = planned["inputs"]["network"]
            ranges = list(network.get("ipv4_private_cidrs", []))
            for pool_id in network.get("ipv4_private_pool_ids", []):
                ranges.extend(sdk.pool_cidrs(pool_id))
            if not ranges or any(c.startswith("/") for c in ranges):
                raise ValueError("NAT requires explicit network CIDRs or resolved private pools")
        inputs["source_cidrs"] = sorted(set(ranges))
        emit(
            "NAT source ranges follow this network's private pools. Workload subnets share network address space."
        )
        inputs["gateway_subnet_cidr"] = ask(
            "inputs.gateway_subnet_cidr", inputs.get("gateway_subnet_cidr", "/28")
        )
        if network_id:
            sdk.validate_subnet_cidr(network_id, inputs["gateway_subnet_cidr"])
        inputs["ssh_user_name"] = ask("inputs.ssh_user_name", inputs.get("ssh_user_name", "ubuntu"))
        inputs["ssh_public_key"] = ask("inputs.ssh_public_key", inputs.get("ssh_public_key"))
        inputs["admin_cidrs"] = ask(
            "inputs.admin_cidrs", inputs.get("admin_cidrs", []), type_hint="list(string)"
        )
        targets: dict[str, dict[str, Any]] = {}
        snapshots: dict[str, dict[str, Any]] = {}
        # Terraform ownership is selected explicitly. Never delegate the same subnet to the SDK.
        if planned is not None:
            for key, subnet in planned["inputs"].get("subnets", {}).items():
                identifier = f"{planned['instance_id']}:{key}"
                source_table = subnet.get("route_table_id") or ""
                if source_table:
                    snapshot = sdk.route_policy(source_table, network_id)
                elif network_id:
                    snapshot = sdk.route_policy(
                        sdk.network(network_id).status.default_route_table_id, network_id
                    )
                else:
                    snapshot = policy_snapshot(
                        "", [{"destination_cidr": "0.0.0.0/0", "default_egress_gateway": True}]
                    )
                targets[identifier] = {
                    "owner": "terraform",
                    "vpc_instance": planned["instance_id"],
                    "subnet_key": key,
                }
                snapshots[identifier] = snapshot
        elif network_id:
            for subnet in sdk.subnets(network_id):
                if subnet.metadata.labels.get("cxcli-component") == "nat-gateway":
                    continue
                identifier = subnet.metadata.id
                targets[identifier] = {
                    "owner": "external",
                    "subnet_id": identifier,
                    "delegated": True,
                }
                snapshots[identifier] = sdk.route_policy(subnet.status.route_table.id, network_id)
        selected_targets = ask(
            "routing.targets", [], [OptionChoice(k, k) for k in targets], type_hint="list(string)"
        )
        policies, resolved_targets = {}, []
        for identifier in selected_targets:
            snapshot = snapshots[identifier]
            # Different original table identities retain separate policies even with equal routes.
            from .nat_gateway import fingerprint

            policy = "policy-" + fingerprint(snapshot)[:16]
            policies[policy] = snapshot
            resolved_targets.append({**targets[identifier], "policy": policy})
        result["routing"] = {
            "version": 1,
            "enabled": True,
            "targets": resolved_targets,
            "policies": policies,
        }
        emit(routing_preview(result))
        if any(t["owner"] == "external" for t in resolved_targets):
            emit(
                "These subnets are externally managed. Delegate only their route_table_id field to cxcli; remove competing ownership first."
            )
        accepted = ask(
            "routing.review",
            None,
            [
                OptionChoice("accept", "Accept these routes and ownership"),
                OptionChoice("draft", "Save prepared gateway without activating routes"),
            ],
        )
        result["routing"]["enabled"] = accepted == "accept"
    candidate = copy.deepcopy(payload)
    candidate["infra"]["components"] = [
        result if r.get("instance_id") == result["instance_id"] else r
        for r in candidate["infra"]["components"]
    ]
    validate_nat_config(candidate)
    report = assess_live_quotas(candidate, context="NAT wizard")
    for line in format_quota_report_lines(report, phase="wizard", markup=False):
        emit(line)
    emit(
        "Unknown or insufficient required quotas block deployment; this configuration can still be saved."
    )
    return result
