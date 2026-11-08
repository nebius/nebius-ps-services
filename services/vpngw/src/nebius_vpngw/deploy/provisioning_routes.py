"""Freeze ordinary route scope before provisioning, then bind exact creations."""

from __future__ import annotations

from copy import deepcopy

from ..apply_approval import digest
from .apply_command import CommandJournal
from .apply_plan import CommandPlan
from .ordinary_route_command import _observe_plan
from .ordinary_route_discovery import Discovery
from .ordinary_route_plan import intent
from .ordinary_route_policy import freeze
from .provisioning_journal import ProvisioningJournal, requests_from_plan
from .provisioning_receipts import completed


def prepare(discovery: Discovery, provisioning: dict) -> dict:
    """Project only reviewed network changes; never discover future destinations."""
    recipes = requests_from_plan(provisioning)
    observation = discovery.observe_infrastructure()
    infrastructure = observation["infrastructure"]
    if (
        provisioning["project_id"] != infrastructure["project"]
        or provisioning["gateway_name"] != discovery.journal.gateway
    ):
        raise RuntimeError("Provisioning routes belong to another deployment")
    guests = {recipe.guest: recipe for recipe in recipes if recipe.guest is not None}
    instances = tuple(discovery.deployment.iter_instance_configs())
    if not set(guests) <= {instance.hostname for instance in instances}:
        raise RuntimeError("Provisioning routes contain an unconfigured gateway")
    gateways, allocations = discovery.gateway_owners(
        observation, tuple(item for item in instances if item.hostname not in guests)
    )
    prefixes = set(observation["policy"].excluded_prefixes)
    for recipe in recipes:
        request = recipe.template()
        if recipe.type == "UpdatePoolRequest":
            prefixes.update(item.cidr for item in request.spec.cidrs)
        elif recipe.type == "CreateSubnetRequest":
            if request.spec.network_id != infrastructure["network_id"]:
                raise RuntimeError("Provisioning subnet crossed route VPC authority")
            prefixes.update(
                item.cidr for pool in request.spec.ipv4_private_pools.pools for item in pool.cidrs
            )
        elif recipe.type == "UpdateSubnetRequest":
            # The provisioning owner only attaches its exclusive gateway subnet.
            # Workload subnet conversion remains the route owner's separate effect.
            identifier = request.metadata.id
            if identifier in {item["subnet_id"] for item in infrastructure["targets"]}:
                raise RuntimeError("Provisioning cannot pre-satisfy a workload route change")
            if request.spec.network_id != infrastructure["network_id"]:
                raise RuntimeError("Provisioning subnet crossed route VPC authority")
            for table in infrastructure["tables"].values():
                table["subnets"] = [value for value in table["subnets"] if value != identifier]
    for instance in instances:
        guest_recipe = guests.get(instance.hostname)
        if guest_recipe is None:
            continue
        request = guest_recipe.template()
        if len(request.spec.network_interfaces) != 1:
            raise RuntimeError("Provisioning routes require one exact gateway NIC")
        nic = request.spec.network_interfaces[0]
        if not nic.name or not nic.subnet_id or not nic.ip_address.allocation_id:
            raise RuntimeError("Provisioning route next hop is incomplete")
        gateways[str(instance.instance_index)] = {
            "name": instance.hostname,
            "compute_id": "@" + guest_recipe.key,
            "nic": nic.name,
            "subnet_id": nic.subnet_id,
            "allocation_id": nic.ip_address.allocation_id,
        }
        allocations[instance.instance_index] = nic.ip_address.allocation_id
    policy = freeze(
        discovery.local, network_prefixes=tuple(sorted(prefixes)), summarize=discovery.summarize
    )
    infrastructure["gateways"] = gateways
    infrastructure["excluded_prefixes"] = list(policy.excluded_prefixes)
    observation["allocations"] = allocations
    observation["policy"] = policy
    selected, _ = _observe_plan(discovery, observation, defer_bgp=True)
    return {"provisioning": digest(provisioning), "routes": intent(selected)}


def bind(frozen: dict, child: ProvisioningJournal, command: CommandJournal) -> dict:
    """Resolve only gateway identity fields from this exact completed child."""
    state = completed(command, child)
    parent = command.state
    root = parent["transaction"]
    plan = CommandPlan.parse(root["plan"])
    route_effect = next((item for item in plan.effects if item.key == "routes"), None)
    if (
        route_effect is None
        or route_effect.owner != "ordinary-provisioned-routes"
        or route_effect.request_sha256 != digest(frozen)
        or parent["bindings"].get("routes") != frozen
        or "provisioning" not in route_effect.requires
        or frozen["provisioning"] != digest(state["transaction"]["plan"])
    ):
        raise RuntimeError("Derived routes differ from successful provisioning receipts")
    result = deepcopy(frozen["routes"])
    for index, gateway in result["infrastructure"]["gateways"].items():
        for field, kind in (
            ("compute_id", "compute"),
            ("subnet_id", "subnet"),
            ("allocation_id", "allocation"),
        ):
            value = gateway[field]
            if value.startswith("@"):
                binding = state["bindings"].get(value[1:])
                if binding is None or binding["kind"] != kind:
                    raise RuntimeError("Derived route identity has no exact creation receipt")
                gateway[field] = binding["resource_id"]
        result["allocations"][index] = gateway["allocation_id"]
    return result
