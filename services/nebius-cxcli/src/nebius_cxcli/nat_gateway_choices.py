"""NAT presentation built on the canonical VPC identity resolver."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .nat_gateway import policy_snapshot
from .provider_options import OptionChoice
from .vpc_choices import parse_planned_vpc_ref, resolve_wizard_provider_choices


def read_field(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for key in path.split("."):
        value = value.get(key) if isinstance(value, Mapping) else None
    return value


def selected_token(row: Mapping[str, Any], field: str) -> str:
    binding = row.get("bindings", {}).get("inputs." + field)
    if not binding:
        return row.get("inputs", {}).get(field) or ""
    suffix = binding["source_output"]
    if binding.get("key") is not None:
        suffix += "." + binding["key"]
    if binding.get("attribute") is not None:
        suffix += "." + binding["attribute"]
    return f"planned:vpc@{binding['source_instance']}.{suffix}"


def select_binding(row: dict[str, Any], field: str, token: str) -> None:
    bindings = row.setdefault("bindings", {})
    if token.startswith("planned:"):
        ref = parse_planned_vpc_ref(token, option_name=field, field_name=field)
        binding = {
            "source_component": "vpc",
            "source_instance": ref.source_instance,
            "source_output": ref.source_output,
        }
        if ref.key is not None:
            binding["key"] = ref.key
        if ref.attribute is not None:
            binding["attribute"] = ref.attribute
        bindings["inputs." + field] = binding
        row["inputs"].pop(field, None)
    else:
        bindings.pop("inputs." + field, None)
        row["inputs"][field] = token


def resolve_choice(choices: list[OptionChoice], token: str) -> OptionChoice | None:
    """Resolve one exact token or proven live identity, never a name or CIDR."""
    matches = [
        choice
        for choice in choices
        if token and token in (choice.value, choice.metadata.get("live_id"))
    ]
    return matches[0] if len(matches) == 1 else None


class NatChoices:
    def __init__(
        self, payload: dict[str, Any], lookup: Any, sdk: Any, emit: Any, prefix: str, entry: Any
    ) -> None:
        self.payload, self.lookup, self.sdk = payload, lookup, sdk
        self.emit, self.prefix, self.entry = emit, prefix, entry
        self.project = payload["client_info"]["nebius"]["project_id"]
        self.vpcs = {
            r["instance_id"]: r
            for r in payload["infra"]["components"]
            if r.get("id") == "vpc" and r.get("enabled")
        }

    def provider(self, name: str, **args: Any) -> list[OptionChoice]:
        path = self.prefix + ".inputs." + name
        choices = resolve_wizard_provider_choices(
            read_field=read_field,
            payload=self.payload,
            entry=self.entry,
            full_path_label=path,
            provider=name,
            args={"project_id": self.project, **args},
            provider_lookup=self.lookup,
        )
        if self.lookup.last_error():
            raise RuntimeError(f"NAT live discovery is incomplete: {self.lookup.last_error()}")
        if (
            name in {"project_networks", "project_subnets"}
            and not self.lookup.vpc_discovery_complete()
        ):
            raise RuntimeError("NAT live discovery is incomplete; refresh the full VPC inventory.")
        for message in self.lookup.vpc_diagnostics(path):
            self.emit(message)
        return choices

    def network(self, choice: OptionChoice) -> tuple[str, list[str]]:
        live_id = choice.metadata.get("live_id") or (
            choice.value if not choice.value.startswith("planned:") else ""
        )
        if live_id:
            return live_id, self.sdk.network_cidrs(live_id)
        ref = parse_planned_vpc_ref(choice.value, option_name="network", field_name="network_id")
        network = self.vpcs[ref.source_instance]["inputs"]["network"]
        ranges = list(network.get("ipv4_private_cidrs", []))
        for pool in network.get("ipv4_private_pool_ids", []):
            ranges.extend(self.sdk.pool_cidrs(pool))
        if not ranges or any(value.startswith("/") for value in ranges):
            raise ValueError("NAT requires explicit network CIDRs or resolved private pools")
        return "", sorted(set(ranges))

    def subnets(self, network: OptionChoice) -> list[OptionChoice]:
        live_network, _ = self.network(network)
        result = []
        for choice in self.provider("project_subnets", network_id=network.value):
            metadata = dict(choice.metadata)
            live_id = metadata.get("live_id") or (
                choice.value if not choice.value.startswith("planned:") else ""
            )
            if live_id:
                info = self.sdk.subnet_info(live_id, live_network)
                metadata.update(info)
                metadata["live_id"] = live_id
            else:
                ref = parse_planned_vpc_ref(
                    choice.value, option_name="subnet", field_name="subnet_id"
                )
                subnet = self.vpcs[ref.source_instance]["inputs"]["subnets"][ref.key]
                table = subnet.get("route_table_id")
                if table:
                    if not live_network:
                        raise ValueError("An existing route table requires a resolved VPC network")
                    policy = self.sdk.route_policy(table, live_network)
                elif live_network:
                    policy = self.sdk.route_policy(
                        self.sdk.network(live_network).status.default_route_table_id, live_network
                    )
                else:
                    policy = policy_snapshot(
                        "", [{"destination_cidr": "0.0.0.0/0", "default_egress_gateway": True}]
                    )
                direct = any(
                    r["destination_cidr"] == "0.0.0.0/0" and r["default_egress_gateway"]
                    for r in policy["routes"]
                )
                # Public pool inheritance is optional in the VPC component.
                # Inherited/explicit pools still require realization checks at deploy.
                public_capable = subnet.get("use_network_public_pools", True) or bool(
                    subnet.get("ipv4_public_cidrs")
                )
                metadata.update(
                    policy=policy,
                    public_ip_capable=public_capable,
                    direct_egress=direct,
                    reason=(
                        "public IPs unavailable"
                        if not public_capable
                        else ""
                        if direct
                        else "no direct default Internet route"
                    ),
                )
            public = (
                "public IPs allowed" if metadata["public_ip_capable"] else "public IPs unavailable"
            )
            egress = (
                "direct Internet egress"
                if metadata["direct_egress"]
                else "custom/no default egress"
            )
            suffix = "; planned capability, checked at deploy" if metadata.get("planned") else ""
            result.append(
                OptionChoice(
                    choice.value,
                    f"{metadata.get('resource_label', choice.label)} | {public}; {egress}{suffix}",
                    choice.recommended,
                    metadata,
                )
            )
        return result

    @staticmethod
    def target(choice: OptionChoice) -> dict[str, Any]:
        if choice.value.startswith("planned:"):
            ref = parse_planned_vpc_ref(
                choice.value, option_name="workload subnet", field_name="subnet_id"
            )
            return {
                "owner": "terraform",
                "vpc_instance": ref.source_instance,
                "subnet_key": ref.key,
            }
        return {"owner": "external", "subnet_id": choice.value, "delegated": True}
