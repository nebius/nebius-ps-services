"""Transactional NAT wizard with local backtracking and fresh provider discovery."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from .nat_gateway import routing_preview, target_key, target_policy_key, validate_nat_config
from .nat_gateway_choices import NatChoices, resolve_choice, select_binding, selected_token
from .nat_gateway_sdk import NatGatewaySDK
from .provider_options import OptionChoice, ProviderOptionLookup
from .quota_checks import assess_live_quotas, format_quota_report_lines

_REFRESH = "__refresh__"
_CREATE_SUBNET = "__create_subnet__"


class WizardNavigation(Exception):
    def __init__(self, value: Any, stopped: bool) -> None:
        self.value, self.stopped = value, stopped


class RefreshChoices(Exception):
    pass


def configure_nat(
    payload: dict[str, Any],
    row: dict[str, Any],
    *,
    prefix: str,
    lookup: ProviderOptionLookup,
    prompt: Callable[..., tuple[Any, bool]],
    is_back: Callable[[Any], bool],
    emit: Callable[[str], None],
    entry: Any,
    sdk_factory: Callable[..., Any] = NatGatewaySDK,
) -> dict[str, Any]:
    result = copy.deepcopy(row)
    inputs = result.setdefault("inputs", {})
    scope = payload["client_info"]["nebius"]
    project, region = scope["project_id"], scope["region_id"]
    inputs["parent_id"] = project
    result.setdefault("routing", {"version": 1, "enabled": True, "targets": [], "policies": {}})
    # Keep snapshots outside the draft selection so backtracking never captures
    # our currently installed NAT policy as the target's original policy.
    policy_snapshots = {
        (selected_token(result, "network_id"), target_key(target)): copy.deepcopy(
            result["routing"]["policies"][target["policy"]]
        )
        for target in result["routing"]["targets"]
    }

    def ask(
        key: str,
        default: Any,
        choices: list[OptionChoice] | None = None,
        type_hint: str = "string",
        *,
        refresh: bool = False,
    ) -> Any:
        if choices is not None:
            allowed = {c.value for c in choices}
            if isinstance(default, list):
                default = [value for value in default if value in allowed]
            elif default and default not in allowed:
                emit(f"Preferred {key} value '{default}' is unavailable; select a current choice.")
                default = None
            if refresh:
                choices = [*choices, OptionChoice(_REFRESH, "Refresh live inventory")]
            elif not choices:
                raise RuntimeError(f"No live choices for {key}; refresh inventory or go back.")
        value, stop = prompt(
            f"{prefix}.{key}",
            default,
            choices=choices,
            type_hint=type_hint,
            # The controller validates workload cardinality. The shared prompt
            # otherwise preselects every choice, including the refresh action.
            required=not (choices is not None and type_hint == "list(string)"),
        )
        if stop or is_back(value):
            raise WizardNavigation(value, stop)
        if choices is not None:
            selected = value if isinstance(value, list) else [value]
            if _REFRESH in selected:
                raise RefreshChoices
            if any(v not in {c.value for c in choices} for v in selected):
                raise ValueError(f"Select {key} from the displayed choices")
        return value

    with sdk_factory(project) as sdk:
        inventory = NatChoices(payload, lookup, sdk, emit, prefix, entry)
        network: OptionChoice | None = None
        gateway_choice: OptionChoice | None = None
        subnets: list[OptionChoice] = []
        images: list[dict[str, Any]] = []
        steps = [
            "name",
            "network",
            "gateway",
            "gateway_cidr",
            "targets",
            "platform",
            "preset",
            "image",
            "disk_type",
            "disk_size",
            "ssh_user",
            "ssh_key",
            "test",
            "review",
        ]
        position = 0
        history: list[int] = []
        while position < len(steps):
            step = steps[position]
            if step == "gateway_cidr" and inputs["gateway_subnet_mode"] != "create":
                position += 1
                continue
            try:
                if step == "name":
                    emit(
                        "NAT gateway: network → gateway subnet → workload subnets → VM and access → verification → summary. q returns one step; qq discards this edit."
                    )
                    inputs["name"] = ask("inputs.name", inputs.get("name") or result["instance_id"])
                elif step == "network":
                    emit(
                        "Choose the parent VPC. Live resources and planned deployment resources are labelled separately."
                    )
                    choices = inventory.provider("project_networks")
                    old = selected_token(result, "network_id")
                    previous = resolve_choice(choices, old)
                    network_default = previous.value if previous is not None else old
                    selected = ask("inputs.network_id", network_default, choices, refresh=True)
                    network = next(c for c in choices if c.value == selected)
                    _, ranges = inventory.network(network)
                    if network_default != selected:
                        gateway_choice = None
                        inputs.pop("gateway_subnet_id", None)
                        result.get("bindings", {}).pop("inputs.gateway_subnet_id", None)
                        result["routing"]["targets"], result["routing"]["policies"] = [], {}
                    elif old != selected:
                        # A proven alias is the same VPC; retain its original route preimages.
                        for (scope_token, snapshot_target), snapshot in list(
                            policy_snapshots.items()
                        ):
                            if scope_token == old:
                                policy_snapshots.setdefault((selected, snapshot_target), snapshot)
                    select_binding(result, "network_id", selected)
                    inputs["source_cidrs"] = sorted(set(ranges))
                elif step == "gateway":
                    assert network is not None
                    emit(
                        "Choose a gateway subnet in the selected VPC, or create a dedicated one. Reuse requires public-IP capability and direct Internet egress; its addressing and routes remain unchanged."
                    )
                    subnets = inventory.subnets(network)
                    suitable = []
                    for choice in sorted(subnets, key=lambda c: bool(c.metadata.get("planned"))):
                        if choice.metadata.get("reason"):
                            emit(
                                f"Unavailable for gateway: {choice.label}: {choice.metadata['reason']}"
                            )
                        else:
                            suitable.append(
                                OptionChoice(
                                    choice.value,
                                    choice.label
                                    if choice.metadata.get("planned")
                                    else f"existing: {choice.label}",
                                    metadata=choice.metadata,
                                )
                            )
                    gateway_default = (
                        selected_token(result, "gateway_subnet_id")
                        if inputs.get("gateway_subnet_mode") == "reuse"
                        else ""
                    )
                    preferred_gateway = resolve_choice(suitable, gateway_default)
                    if gateway_default and preferred_gateway is None:
                        emit(
                            f"Saved gateway subnet '{gateway_default}' is unavailable; select a current subnet or create a dedicated one."
                        )
                    selected = ask(
                        "gateway_subnet",
                        preferred_gateway.value
                        if preferred_gateway is not None
                        else _CREATE_SUBNET,
                        [
                            *suitable,
                            OptionChoice(
                                _CREATE_SUBNET, "Create a dedicated public-facing gateway subnet"
                            ),
                        ],
                        refresh=True,
                    )
                    if selected == _CREATE_SUBNET:
                        gateway_choice = None
                        inputs["gateway_subnet_mode"] = "create"
                        inputs.pop("gateway_subnet_id", None)
                        result.get("bindings", {}).pop("inputs.gateway_subnet_id", None)
                    else:
                        gateway_choice = next(c for c in suitable if c.value == selected)
                        inputs["gateway_subnet_mode"] = "reuse"
                        select_binding(result, "gateway_subnet_id", selected)
                        inputs.pop("gateway_subnet_cidr", None)
                elif step == "gateway_cidr":
                    import ipaddress

                    cidr = ask(
                        "inputs.gateway_subnet_cidr", inputs.get("gateway_subnet_cidr", "/28")
                    )
                    ipaddress.IPv4Network("0.0.0.0" + cidr if cidr.startswith("/") else cidr)
                    inputs["gateway_subnet_cidr"] = cidr
                elif step == "targets":
                    assert network is not None
                    emit(
                        "Select private workload subnets whose Internet traffic should use this NAT gateway. Each receives its own table preserving non-default routes."
                    )
                    subnets = inventory.subnets(network)
                    gateway = (
                        selected_token(result, "gateway_subnet_id")
                        if inputs["gateway_subnet_mode"] == "reuse"
                        else ""
                    )
                    gateway_live_id = (
                        gateway_choice.metadata.get("live_id")
                        if gateway_choice is not None
                        else None
                    )
                    choices = [
                        c
                        for c in subnets
                        if c.value != gateway
                        and (not gateway_live_id or c.metadata.get("live_id") != gateway_live_id)
                    ]
                    prior = {
                        target_key(t): result["routing"]["policies"][t["policy"]]
                        for t in result["routing"]["targets"]
                    }
                    default = [c.value for c in choices if target_key(inventory.target(c)) in prior]
                    if any(inventory.target(c)["owner"] == "external" for c in choices):
                        emit(
                            "Selecting external workload subnets delegates their route-table attachment to cxcli. Remove competing attachment ownership before deployment."
                        )
                    selected = ask(
                        "routing.targets", default, choices, "list(string)", refresh=True
                    )
                    if not selected:
                        raise ValueError(
                            "Select at least one workload subnet to use the NAT gateway."
                        )
                    targets, policies = [], {}
                    for choice in choices:
                        if choice.value in selected:
                            target = inventory.target(choice)
                            policy = target_policy_key(target)
                            targets.append({**target, "policy": policy})
                            # Re-editing an active target must retain its original source
                            # policy, not snapshot cxcli's currently installed NAT table.
                            snapshot = policy_snapshots.setdefault(
                                (network.value, target_key(target)),
                                copy.deepcopy(choice.metadata["policy"]),
                            )
                            policies[policy] = copy.deepcopy(snapshot)
                    result["routing"].update(targets=targets, policies=policies)
                elif step == "platform":
                    value = ask(
                        "inputs.platform",
                        inputs.get("platform", "cpu-d3"),
                        inventory.provider("compute_platforms", cpu_only=True),
                        refresh=True,
                    )
                    if value != inputs.get("platform"):
                        inputs.pop("preset", None)
                        inputs.pop("source_image_id", None)
                    inputs["platform"] = value
                elif step == "preset":
                    value = ask(
                        "inputs.preset",
                        inputs.get("preset", "4vcpu-16gb"),
                        inventory.provider(
                            "compute_platform_presets", cpu_only=True, platform=inputs["platform"]
                        ),
                        refresh=True,
                    )
                    sdk.cpu_preset(inputs["platform"], value)
                    if value != inputs.get("preset"):
                        inputs.pop("source_image_id", None)
                    inputs["preset"] = value
                elif step == "image":
                    images = sdk.image_choices(region, inputs["platform"], inputs["preset"])
                    choices = [
                        OptionChoice(r["id"], r["name"], i == 0) for i, r in enumerate(images)
                    ]
                    value = ask(
                        "inputs.source_image_id",
                        inputs.get("source_image_id"),
                        choices,
                        refresh=True,
                    )
                    sdk.image(value, platform=inputs["platform"], preset=inputs["preset"])
                    inputs["source_image_id"] = value
                elif step == "disk_type":
                    inputs["boot_disk_type"] = ask(
                        "inputs.boot_disk_type",
                        inputs.get("boot_disk_type", "NETWORK_SSD"),
                        inventory.provider("compute_boot_disk_types"),
                        refresh=True,
                    )
                elif step == "disk_size":
                    minimum = next(
                        r["minimum_gib"] for r in images if r["id"] == inputs["source_image_id"]
                    )
                    value = ask(
                        "inputs.boot_disk_size_gib",
                        max(minimum, inputs.get("boot_disk_size_gib", 40)),
                        type_hint="number",
                    )
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or value < minimum
                        or value != int(value)
                    ):
                        raise ValueError(
                            f"Boot disk size must be a whole number of GiB, at least {minimum} for this image."
                        )
                    inputs["boot_disk_size_gib"] = value
                elif step == "ssh_user":
                    inputs["ssh_user_name"] = ask(
                        "inputs.ssh_user_name", inputs.get("ssh_user_name", "ubuntu")
                    )
                elif step == "ssh_key":
                    inputs["ssh_public_key"] = ask(
                        "inputs.ssh_public_key", inputs.get("ssh_public_key")
                    )
                elif step == "test":
                    emit(
                        "After route activation, automatic verification uses one temporary private VM per workload subnet, sequentially, then removes it. Failure or missing evidence restores this activation's attachments. SSH access through the gateway and temporary quota are checked before activation. Test access, result collection and cleanup are automatic; serial logs are not required."
                    )
                    enabled = result["routing"].get("verification", {}).get("enabled", True)
                    selected = ask(
                        "routing.verification.enabled",
                        "enabled" if enabled else "disabled",
                        [
                            OptionChoice(
                                "enabled", "Run automatic Internet routing test (recommended)"
                            ),
                            OptionChoice(
                                "disabled",
                                "Skip Internet test; still verify gateway and activate routes",
                            ),
                        ],
                    )
                    result["routing"]["verification"] = {"enabled": selected == "enabled"}
                elif step == "review":
                    result["routing"]["enabled"] = True
                    emit(routing_preview(result))
                    if any(t["owner"] == "external" for t in result["routing"]["targets"]):
                        emit(
                            "Selected external subnet route-table attachments are delegated to cxcli. Other subnet fields remain with their existing owner."
                        )
                    candidate = copy.deepcopy(payload)
                    candidate["infra"]["components"] = [
                        result if r.get("instance_id") == result["instance_id"] else r
                        for r in candidate["infra"]["components"]
                    ]
                    validate_nat_config(candidate)
                history.append(position)
                position += 1
            except RefreshChoices:
                lookup.refresh()
                emit("Live inventory will be read again.")
            except WizardNavigation as navigation:
                if navigation.stopped or not history:
                    raise
                position = history.pop()
            except (ValueError, RuntimeError) as error:
                emit(str(error))
                # Keep failed fields local. A retry also refreshes discovery; q goes back.
                try:
                    ask(
                        "retry",
                        "retry",
                        [OptionChoice("retry", "Retry this step / refresh inventory")],
                    )
                except WizardNavigation as navigation:
                    if navigation.stopped or not history:
                        raise
                    position = history.pop()
                lookup.refresh()
    from .nat_gateway_address_plan import assess_config_address_space

    capacity = assess_config_address_space(candidate, sdk_factory=sdk_factory)
    emit(capacity.message())
    emit(
        "This configuration can be saved, then validated and rendered. Deploy checks fresh address capacity and quotas before provisioning; routes activate only after gateway readiness checks."
    )
    report = assess_live_quotas(candidate, context="NAT wizard")
    for line in format_quota_report_lines(report, phase="wizard", markup=False):
        emit(line)
    return result
