"""NAT prepare, verify, activate and restore around the admitted Terraform lifecycle."""

from __future__ import annotations

import copy
import ipaddress
import json
import re
import subprocess
import uuid
from contextlib import ExitStack
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .component_sources import component_output_root_name
from .nat_gateway import ROUTE_OVERRIDES, fingerprint, nat_rows, target_key
from .nat_gateway_activation_inputs import activation_subnet_contracts
from .nat_gateway_probe_ssh import ProbeSSH
from .nat_gateway_sdk import NatGatewaySDK
from .nat_gateway_ssh import ssh_identity_options
from .nat_gateway_state import NatState, activation_pending, assert_activation_plan
from .object_storage_transport import object_storage_transport
from .runtime_config import to_plain_data
from .ssh_host_enrollment import prepare_ssh_host_trust
from .ssh_host_identity import prepare_host_identity
from .ssh_trust import resolve_ssh_known_hosts_file, ssh_trust_options
from .terraform_backend import backend_settings_from_config

CURRENT_NAT: ContextVar[Any] = ContextVar("cxcli_nat_deployment", default=None)


def guard_direct_mutation(
    cli: Any, config: Any, paths: Any, runtime_env: Any, *, initialize: bool = False
) -> None:
    from .wireguard_host_identity import CURRENT_WIREGUARD_IDENTITIES, wireguard_rows

    if wireguard_rows(config) and CURRENT_WIREGUARD_IDENTITIES.get() is None:
        raise RuntimeError(
            "WireGuard infrastructure must use render/deploy with protected host identities; "
            "direct Terraform mutation is disabled"
        )
    manager = CURRENT_NAT.get()
    if manager is not None:
        manager.assert_held()
        manager.assert_attachments()
        return
    if nat_rows(to_plain_data(config)):
        raise RuntimeError(
            "NAT infrastructure must use render/deploy with readiness and routing recovery; direct Terraform mutation is disabled"
        )
    state = cli.terraform_show_json(paths.infra_dir, extra_env=runtime_env, initialize=initialize)
    resources = cli._terraform_state_resources(state)
    if any(
        ((r.get("values") or {}).get("labels") or {}).get("cxcli-component") == "nat-gateway"
        for r in resources
    ):
        raise RuntimeError(
            "Terraform state still contains NAT resources; restore routing and retire the component through render/deploy"
        )


def module_outputs(outputs: dict[str, Any], instance: str) -> dict[str, Any]:
    prefix = component_output_root_name(instance, "")
    return {
        key[len(prefix) :]: value["value"]
        for key, value in outputs.items()
        if key.startswith(prefix)
    }


def verify_guest(
    outputs: dict[str, Any],
    inputs: dict[str, Any],
    *,
    known_hosts: Path,
    private_key: Path | None = None,
) -> dict[str, Any]:
    user = inputs["ssh_user_name"]
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
        raise ValueError("Invalid NAT SSH user")
    address = str(ipaddress.IPv4Interface(outputs["public_ip"]).ip)
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        *ssh_trust_options(known_hosts),
    ]
    command.extend(
        [
            f"{user}@{address}",
            "timeout 300 cloud-init status --wait >/dev/null && systemctl is-active --quiet nebius-nat.service && sudo -n /usr/local/sbin/nebius-nat status",
        ]
    )
    try:
        with ssh_identity_options(inputs["ssh_public_key"], private_key) as identity:
            result = subprocess.run(
                [command[0], *identity, *command[1:]],
                capture_output=True,
                text=True,
                timeout=330,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError(
            "NAT guest readiness could not be verified; routes remain at their last confirmed attachment"
        ) from None
    if result.returncode or len(result.stdout) > 65536:
        raise RuntimeError(
            "NAT trusted SSH/bootstrap readiness failed; verify host trust and SSH access, then resume deploy"
        )
    report = json.loads(result.stdout)
    if (
        report.get("schema") != 2
        or report.get("config_sha256") != outputs["guest_config_sha256"]
        or report.get("forwarding") is not True
        or report.get("rules_valid") is not True
        or report.get("ssh_policy_valid") is not True
        or report.get("ssh_listener_ready") is not True
        or report.get("ssh_ban_service_ready") is not True
    ):
        raise RuntimeError("NAT guest forwarding or SSH security differs from Terraform intent")
    return report


class NatDeployment:
    def __init__(self, executor: Any, *, settings: Any = None) -> None:
        self.executor = executor
        self.settings = settings
        self.stack = ExitStack()
        self.state: NatState | None = None
        self.prepared = False
        self.activating = False
        self.rows: list[dict[str, Any]] = []
        self.overrides: dict[str, Any] = {}
        self.retirement_required = False
        self._resources: list[dict[str, Any]] | None = None
        self._host_identity_env: dict[str, str] = {}
        self._host_identity_modules: dict[str, str] = {}

    def __enter__(self) -> NatDeployment:
        self.token = CURRENT_NAT.set(self)
        payload = to_plain_data(self.executor.config)
        if not any(row.get("enabled") for row in payload.get("infra", {}).get("components", [])):
            # The last NAT row may have been removed. Shared ownership survives config removal.
            try:
                state = self._open()
                self.retirement_required = any(
                    record.get("outputs") for record in state.value["gateways"].values()
                )
                if any(
                    record.get("phase") not in {"detached", "retired"}
                    for record in state.value["gateways"].values()
                ):
                    raise RuntimeError(
                        "Removed NAT has not restored routing; restore its configuration, disable routing and deploy before retirement"
                    )
            except BaseException:
                self.__exit__(None, None, None)
                raise
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self.state is not None:
                self.state.release()
        finally:
            self.stack.close()
            CURRENT_NAT.reset(self.token)

    def _open(self) -> NatState:
        if self.state is None:
            # The outer deployment has already admitted the protected regular backend.
            settings = self.settings or backend_settings_from_config(self.executor.config)
            transport = self.stack.enter_context(
                object_storage_transport(settings, extra_env=self.executor.runtime_env)
            )
            self.state = NatState(transport, settings.key, readonly=self.executor.options.dry_run)
            if nat_rows(to_plain_data(self.executor.config)) or self.state.value["gateways"]:
                self.state.acquire()
        return self.state

    def assert_held(self) -> None:
        self.executor.lease.assert_held()
        if self.state is not None and (self.state.lock_etag or self.retirement_required):
            self.state.assert_held()

    def outputs(self) -> dict[str, Any]:
        return self.executor.cli.terraform_output_json(
            self.executor.paths.infra_dir, extra_env=self.executor.runtime_env, initialize=False
        )

    def _inputs(self, row: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
        inputs = copy.deepcopy(row["inputs"])
        binding = row.get("bindings", {}).get("inputs.network_id")
        if binding:
            inputs["network_id"] = module_outputs(outputs, binding["source_instance"]).get(
                "network_id", ""
            )
        binding = row.get("bindings", {}).get("inputs.gateway_subnet_id")
        if binding:
            source = module_outputs(outputs, binding["source_instance"])
            value = source.get(binding["source_output"], {})
            if binding.get("key") is not None:
                value = value.get(binding["key"], {})
            if binding.get("attribute") is not None:
                value = value.get(binding["attribute"], "")
            inputs["gateway_subnet_id"] = value or self._subnet_id(
                {
                    "owner": "terraform",
                    "vpc_instance": binding["source_instance"],
                    "subnet_key": binding["key"],
                },
                outputs,
            )
        return inputs

    def _module_name(self, component: str, instance: str) -> str:
        matches = [
            item["module_name"]
            for item in self.executor.manifest.get("render", {}).get("module_sources", [])
            if item.get("component_id") == component and item.get("instance_id") == instance
        ]
        if len(matches) != 1 or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", matches[0]):
            raise RuntimeError(
                "NAT requires an unambiguous rendered module identity; rerender the project"
            )
        return matches[0]

    def _state_resources(self) -> list[dict[str, Any]]:
        if self._resources is None:
            raw = self.executor.cli.terraform_show_json(
                self.executor.paths.infra_dir, extra_env=self.executor.runtime_env, initialize=False
            )
            self._resources = self.executor.cli._terraform_state_resources(raw)
        return self._resources

    def _module_resources(self, component: str, instance: str) -> list[dict[str, Any]]:
        prefix = f"module.{self._module_name(component, instance)}."
        return [
            resource
            for resource in self._state_resources()
            if resource.get("address", "").startswith(prefix)
        ]

    def _network_id(self, row: dict[str, Any], outputs: dict[str, Any]) -> str:
        network = self._inputs(row, outputs).get("network_id", "")
        binding = row.get("bindings", {}).get("inputs.network_id")
        if not binding:
            return network
        identities = {network} if network else set()
        for resource in self._module_resources("vpc", binding["source_instance"]):
            values = resource.get("values") or {}
            kind = resource.get("type")
            identity = (
                values.get("id")
                if kind == "nebius_vpc_v1_network"
                else values.get("network_id")
                if kind == "nebius_vpc_v1_subnet"
                else None
            )
            if identity:
                identities.add(identity)
        if len(identities) > 1:
            raise RuntimeError("NAT VPC network identity is ambiguous in Terraform state")
        return next(iter(identities), "")

    def _subnet_id(self, target: dict[str, Any], outputs: dict[str, Any]) -> str:
        subnet = (
            module_outputs(outputs, target["vpc_instance"])
            .get("subnet_ids", {})
            .get(target["subnet_key"], "")
        )
        candidates = [
            resource.get("values", {}).get("id", "")
            for resource in self._module_resources("vpc", target["vpc_instance"])
            if resource.get("mode") == "managed"
            and resource.get("type") == "nebius_vpc_v1_subnet"
            and resource.get("index") == target["subnet_key"]
        ]
        if len(candidates) > 1 or (candidates and not candidates[0]):
            raise RuntimeError("NAT workload subnet identity is unresolved in Terraform state")
        if subnet and candidates and subnet != candidates[0]:
            raise RuntimeError(
                "NAT workload subnet identity disagrees between outputs and resource state"
            )
        return subnet or (candidates[0] if candidates else "")

    def _validate_provisioning(self, row: dict[str, Any], outputs: dict[str, Any]) -> None:
        inputs = self._inputs(row, outputs)
        network = self._network_id(row, outputs)
        own_subnet = module_outputs(outputs, row["instance_id"]).get("subnet_id", "")
        candidates = [
            resource.get("values", {}).get("id", "")
            for resource in self._module_resources("nat-gateway", row["instance_id"])
            if resource.get("mode") == "managed" and resource.get("type") == "nebius_vpc_v1_subnet"
        ]
        if len(candidates) > 1 or (own_subnet and candidates and own_subnet != candidates[0]):
            raise RuntimeError("NAT dedicated subnet identity is ambiguous")
        with NatGatewaySDK(inputs["parent_id"]) as sdk:
            if inputs.get("gateway_subnet_mode", "create") == "reuse" and inputs.get(
                "gateway_subnet_id"
            ):
                if inputs["gateway_subnet_id"] in candidates:
                    raise ValueError(
                        "Cannot reuse the subnet currently owned by this NAT module; keep create mode or select an independently owned subnet"
                    )
                sdk.reusable_subnet(inputs["gateway_subnet_id"], network)
                if not own_subnet:
                    sdk.require_private_capacity(inputs["gateway_subnet_id"], network)
            sdk.cpu_preset(inputs["platform"], inputs["preset"])
            image = sdk.image(
                inputs["source_image_id"], platform=inputs["platform"], preset=inputs["preset"]
            )
            if inputs["boot_disk_size_gib"] * 1024**3 < image.status.min_disk_size_bytes:
                raise ValueError("NAT disk is smaller than the live image minimum")
            if network and set(inputs["source_cidrs"]) != set(sdk.network_cidrs(network)):
                raise RuntimeError(
                    "NAT source ranges differ from the live network pools; review the network policy"
                )

    def prepare(self) -> None:
        if self.prepared:
            self.apply_runtime_inputs(self.executor)
            return
        payload = to_plain_data(self.executor.config)
        self.rows = nat_rows(payload)
        state = self._open()
        if any(activation_pending(record) for record in state.value["gateways"].values()):
            if self.executor.options.dry_run:
                raise RuntimeError(
                    "Unfinished NAT activation requires deploy recovery before preview"
                )
            self.restore_activations()
            raise RuntimeError(
                "Interrupted NAT activation restored and probes cleaned; rerun deploy from this checkpoint"
            )
        if not self.rows:
            if any(
                record["phase"] not in {"detached", "retired"}
                for record in state.value["gateways"].values()
            ):
                raise RuntimeError(
                    "Removed NAT has not restored routing; disable routing and deploy before retirement"
                )
            self.prepared = True
            return
        outputs = self.outputs()
        all_rows = {r["instance_id"]: r for r in payload["infra"]["components"]}
        preview_needed = False
        managed_subnets = {
            identifier
            for r in all_rows.values()
            if r.get("id") == "vpc"
            for identifier in module_outputs(outputs, r["instance_id"])
            .get("subnet_ids", {})
            .values()
        }
        managed_subnets.update(
            resource["values"]["id"]
            for resource in self._state_resources()
            if resource.get("mode") == "managed"
            and resource.get("type") == "nebius_vpc_v1_subnet"
            and (resource.get("values") or {}).get("id")
        )
        for row in self.rows:
            name = row["instance_id"]
            record = state.value["gateways"].get(name)
            if record and record.get("phase") == "retired":
                record = None
            intent = copy.deepcopy(row)
            intent["routing"].pop("enabled")
            intent["routing"].pop("verification", None)
            digest = fingerprint(intent)
            if record and record["intent"] != digest and record.get("phase") != "detached":
                raise RuntimeError(
                    f"NAT {name} has an active or unfinished intent; disable routing and deploy before changing topology or policy"
                )
            inputs = self._inputs(row, outputs)
            network = self._network_id(row, outputs)
            module_name = self._module_name("nat-gateway", name)
            if record and record["module_name"] != module_name:
                raise RuntimeError(
                    "NAT rendered module identity changed; retire the existing gateway before remapping it"
                )
            if row["routing"]["enabled"] or not record or record["intent"] != digest:
                self._validate_provisioning(row, outputs)
            with NatGatewaySDK(inputs["parent_id"]) as sdk:
                if row["routing"]["enabled"] and network:
                    for policy in row["routing"]["policies"].values():
                        sdk.check_source_policy(policy, network)
                if not record or record["intent"] != digest:
                    preview_needed = True
                    previous = record
                    if previous:
                        self._assert_no_consumers(previous)
                    record = {
                        "intent": digest,
                        "module_name": module_name,
                        "phase": "detached" if previous else "prepared",
                        "network_id": network,
                        "project_id": inputs["parent_id"],
                        "targets": {},
                        "outputs": previous["outputs"] if previous else {},
                    }
                    for target in row["routing"]["targets"]:
                        key = target_key(target)
                        if target["owner"] == "external":
                            if target["subnet_id"] in managed_subnets:
                                raise ValueError(
                                    "A Terraform-owned VPC subnet cannot delegate its route field to the SDK; select the deployment VPC in the wizard"
                                )
                            if not network:
                                raise ValueError("External NAT subnets require an existing network")
                            subnet = sdk.subnet(target["subnet_id"], network)
                            policy = row["routing"]["policies"][target["policy"]]
                            if subnet.status.route_table.id != policy["source_table_id"]:
                                raise RuntimeError(
                                    "Selected subnet source route table changed; refresh and review routing"
                                )
                            original = subnet.spec.route_table_id or ""
                            subnet_id = target["subnet_id"]
                        else:
                            original = (
                                all_rows[target["vpc_instance"]]["inputs"]["subnets"][
                                    target["subnet_key"]
                                ].get("route_table_id")
                                or ""
                            )
                            subnet_id = (
                                module_outputs(outputs, target["vpc_instance"])
                                .get("subnet_ids", {})
                                .get(target["subnet_key"], "")
                            )
                        record["targets"][key] = {
                            **target,
                            "original": original,
                            "subnet_id": subnet_id,
                            "installed": "",
                            "pending": None,
                        }
                    state.value["gateways"][name] = record
                    state.save()
                if network:
                    if record["network_id"] and record["network_id"] != network:
                        raise RuntimeError(
                            "NAT network identity changed outside its recorded intent"
                        )
                    record["network_id"] = network
                for target in record["targets"].values():
                    if target["owner"] == "terraform":
                        subnet = self._subnet_id(target, outputs)
                        if subnet:
                            if target["subnet_id"] and target["subnet_id"] != subnet:
                                raise RuntimeError(
                                    "NAT workload subnet identity changed outside its recorded intent"
                                )
                            target["subnet_id"] = subnet
                state.save()
                for key, target in record["targets"].items():
                    if target["owner"] == "terraform":
                        if not row["routing"]["enabled"]:
                            self.overrides[key] = target["original"] or None
                            continue
                        original = target["installed"] or target["original"] or None
                        pending = target.get("pending")
                        if pending and target["subnet_id"]:
                            current = sdk.subnet(target["subnet_id"], network).spec.route_table_id
                            if current == pending["desired"]:
                                original = current
                            elif current != pending["expected"]:
                                raise RuntimeError(
                                    "Unfinished NAT Terraform attachment has drifted"
                                )
                        self.overrides[key] = original
        self._prepare_host_identities(outputs)
        if preview_needed:
            from .nat_gateway_address_plan import assess_config_address_space

            report = assess_config_address_space(payload, sdk_factory=NatGatewaySDK)
            self.executor.cli.progress_console.print(
                report.message()
                + " Preparation preview assumes new reservations; the fresh plan determines retained demand.",
                markup=False,
            )
        self.prepared = True
        self.assert_attachments()
        self.apply_runtime_inputs(self.executor)

    def _prepare_host_identities(
        self, outputs: dict[str, Any], *, retained_only: bool = False
    ) -> None:
        self._host_identity_env = {}
        self._host_identity_modules = {}
        for row in self.rows:
            module_name = self._module_name("nat-gateway", row["instance_id"])
            self._host_identity_modules[row["instance_id"]] = module_name
            own = module_outputs(outputs, row["instance_id"])
            identities = {
                resource["values"]["id"]
                for resource in self._module_resources("nat-gateway", row["instance_id"])
                if resource.get("mode") == "managed"
                and resource.get("type") == "nebius_compute_v1_instance"
                and (resource.get("values") or {}).get("id")
            }
            if own.get("instance_id"):
                identities.add(own["instance_id"])
            if len(identities) > 1:
                raise RuntimeError("NAT host identity has conflicting Terraform instance IDs")
            if retained_only and not identities:
                continue
            with NatGatewaySDK(row["inputs"]["parent_id"]) as sdk:
                self._host_identity_env.update(
                    prepare_host_identity(
                        sdk,
                        namespace="nat-host-keys",
                        instance_id=next(iter(identities), ""),
                        paths=self.executor.report_paths,
                        infra_dir=self.executor.paths.infra_dir,
                        module_name=module_name,
                        preview=self.executor.options.dry_run,
                    )
                )

    def apply_runtime_inputs(self, executor: Any) -> None:
        """Bind prepared inputs to root or admission planners without persisting keys."""
        if not self.prepared:
            raise RuntimeError("NAT runtime inputs require a prepared deployment owner")
        if nat_rows(to_plain_data(executor.config)) != self.rows:
            raise RuntimeError("NAT runtime inputs differ from the prepared configuration")
        modules = [
            item
            for item in executor.manifest.get("render", {}).get("module_sources", [])
            if item.get("component_id") == "nat-gateway"
        ]
        actual = {item.get("instance_id"): item.get("module_name") for item in modules}
        if len(actual) != len(modules) or actual != self._host_identity_modules:
            raise RuntimeError("NAT runtime inputs differ from the prepared module identities")
        self._set_overrides(executor)
        executor.runtime_env.update(self._host_identity_env)

    def _set_overrides(self, executor: Any) -> None:
        if self.rows:
            expected = {
                target_key(target)
                for row in self.rows
                for target in row["routing"]["targets"]
                if target["owner"] == "terraform"
            }
            if not expected.issubset(self.overrides):
                raise RuntimeError(
                    "NAT route override map is incomplete; refusing uncontrolled routing"
                )
            executor.runtime_env["TF_VAR_" + ROUTE_OVERRIDES] = json.dumps(self.overrides)

    def assert_attachments(self) -> None:
        if self.state is None:
            return
        for row in self.rows:
            record = self.state.value["gateways"][row["instance_id"]]
            if not record.get("network_id"):
                continue
            with NatGatewaySDK(record["project_id"]) as sdk:
                for target in record["targets"].values():
                    if not target["subnet_id"]:
                        continue
                    current = sdk.subnet(target["subnet_id"], record["network_id"])
                    allowed = {target["installed"] or target["original"]}
                    if target.get("pending"):
                        allowed.add(target["pending"]["desired"])
                    if current.spec.route_table_id not in allowed:
                        raise RuntimeError(
                            "NAT workload attachment changed outside its journaled transition; refusing overwrite"
                        )

    def check_plan(self, plan: dict[str, Any]) -> None:
        if self.activating:
            return
        if self.rows:
            from .nat_gateway_quota import check_plan_quotas

            self.assert_attachments()
            outputs = self.outputs()
            for row in self.rows:
                prefix = f"module.{self._module_name('nat-gateway', row['instance_id'])}."
                if any(
                    resource.get("mode") != "data"
                    and resource.get("address", "").startswith(prefix)
                    and set(resource.get("change", {}).get("actions", [])) & {"create", "update"}
                    for resource in plan.get("resource_changes", [])
                ):
                    self._validate_provisioning(row, outputs)
            from .nat_gateway_address_plan import assess_plan_address_space

            modules = {
                (item["component_id"], item["instance_id"]): item["module_name"]
                for item in self.executor.manifest.get("render", {}).get("module_sources", [])
            }
            for row in self.rows:
                inputs = self._inputs(row, outputs)
                if inputs.get("gateway_subnet_mode") == "reuse" and inputs.get("gateway_subnet_id"):
                    allocation_address = f"module.{self._module_name('nat-gateway', row['instance_id'])}.nebius_vpc_v1_allocation.private"
                    if any(
                        r.get("address") == allocation_address
                        and "create" in r.get("change", {}).get("actions", [])
                        for r in plan.get("resource_changes", [])
                    ):
                        with NatGatewaySDK(inputs["parent_id"]) as sdk:
                            sdk.require_private_capacity(
                                inputs["gateway_subnet_id"], self._network_id(row, outputs)
                            )
            report = assess_plan_address_space(to_plain_data(self.executor.config), plan, modules)
            if report.capacities or report.status != "feasible":
                self.executor.cli.progress_console.print(report.message(), markup=False)
            report.require_feasible()
            check_plan_quotas(to_plain_data(self.executor.config), plan)
            assert self.state is not None
            for row in self.rows:
                if row["routing"]["enabled"]:
                    continue
                record = self.state.value["gateways"][row["instance_id"]]
                for target in record["targets"].values():
                    if target["owner"] == "terraform":
                        for resource in plan.get("resource_changes", []):
                            change = resource.get("change", {})
                            if (
                                resource.get("type") == "nebius_vpc_v1_subnet"
                                and (change.get("before") or {}).get("id") == target["subnet_id"]
                                and change.get("actions") not in (["no-op"], ["read"])
                            ):
                                after = change.get("after") or {}
                                if (
                                    change.get("actions") != ["update"]
                                    or (after.get("route_table_id") or "") != target["original"]
                                    or (change.get("after_unknown") or {}).get("route_table_id")
                                ):
                                    raise RuntimeError(
                                        "NAT detach plan must restore the recorded original route table"
                                    )
                        target["pending"] = {
                            "desired": target["original"],
                            "expected": target["installed"] or target["original"],
                            "operation_id": "terraform",
                        }
                self.state.save()
        # Detect removed components from provider labels, independent of the authored catalog.
        for resource in plan.get("resource_changes", []):
            before = resource.get("change", {}).get("before") or {}
            if (before.get("labels") or {}).get("cxcli-component") != "nat-gateway":
                continue
            if "delete" not in resource["change"]["actions"]:
                continue
            state = self._open()
            address = resource.get("address", "")
            records = [
                r
                for r in state.value["gateways"].values()
                if address.startswith(f"module.{r['module_name']}.")
            ]
            if any(activation_pending(record) for record in records):
                raise RuntimeError(
                    "NAT retirement blocked by unfinished activation or pending probe cleanup"
                )
            if not records or any(r["phase"] != "detached" for r in records):
                raise RuntimeError(
                    "Restore NAT routing before retirement: set routing.enabled=false, render and deploy; then remove the component and deploy again"
                )
            for record in records:
                self._assert_no_consumers(record)

    def _assert_no_consumers(self, record: dict[str, Any]) -> None:
        tables = set(record["outputs"].get("route_table_ids", {}).values())
        with NatGatewaySDK(record["project_id"]) as sdk:
            for subnet in sdk.subnets(record["network_id"]):
                if subnet.status.route_table.id in tables:
                    raise RuntimeError("A subnet still consumes a NAT table; retirement is blocked")

    def _attach(
        self, sdk: NatGatewaySDK, record: dict[str, Any], target: dict[str, Any], desired: str
    ) -> None:
        assert self.state is not None
        current = sdk.subnet(target["subnet_id"], record["network_id"])
        pending = target.get("pending")
        if pending:
            if pending["desired"] != desired:
                raise RuntimeError("Resolve the pending NAT attachment before changing its intent")
            if pending.get("operation_id"):
                sdk.wait_operation(pending["operation_id"])
                current = sdk.subnet(target["subnet_id"], record["network_id"])
            else:
                raise RuntimeError(
                    "NAT attachment has an unknown submission outcome; use nat reconcile only after confirming prior requests have settled"
                )
        expected = target["installed"] or target["original"]
        if current.spec.route_table_id == desired:
            if not pending and desired != expected:
                raise RuntimeError("NAT attachment changed without a journaled operation")
            target["installed"] = desired if desired != target["original"] else ""
            target["pending"] = None
            self.state.save()
            return
        if pending:
            raise RuntimeError("Completed NAT operation did not reach its desired attachment")
        if current.spec.route_table_id != expected:
            raise RuntimeError("NAT attachment drift; refusing to overwrite another owner")
        target["pending"] = {"desired": desired, "expected": expected, "operation_id": ""}
        self.state.save()  # Durable write-ahead intent precedes the SDK request.

        def operation(identifier: str) -> None:
            target["pending"]["operation_id"] = identifier
            assert self.state is not None
            self.state.save()

        sdk.attach(
            target["subnet_id"],
            record["network_id"],
            expected=expected,
            desired=desired,
            assert_held=self.assert_held,
            record_operation=operation,
        )
        target["installed"] = desired if desired != target["original"] else ""
        target["pending"] = None
        self.state.save()

    def complete(self) -> None:
        try:
            self._complete_activation()
        except Exception as error:
            if self.state is not None and any(
                record.get("activation", {}).get("phase") not in {None, "complete", "restored"}
                for record in self.state.value["gateways"].values()
            ):
                try:
                    self.restore_activations()
                except Exception as recovery_error:
                    raise RuntimeError(
                        "NAT activation failed; restoration or probe cleanup is pending. Inspect nat status and resume after resolving the reported drift or operation."
                    ) from recovery_error
            raise error

    def _apply_routes(self, desired: dict[str, str], purpose: str) -> None:
        assert self.state is not None
        if not self.prepared and self.rows:
            self._prepare_host_identities(self.outputs(), retained_only=True)
            self.executor.runtime_env.update(self._host_identity_env)
        self._set_overrides(self.executor)
        self.activating = True
        try:
            plan_file, _ = self.executor._terraform_plan(purpose=purpose)
            assert_activation_plan(
                self.executor.observation_plan,
                desired,
                subnet_contracts=activation_subnet_contracts(
                    self.executor.config, self.executor.manifest, self.state.value["gateways"]
                ),
            )
            self.state.save()
            self.assert_held()
            if any(
                r.get("change", {}).get("actions") not in (["no-op"], ["read"])
                for r in self.executor.observation_plan.get("resource_changes", [])
            ):
                self.executor.cli._run_terraform_apply_with_status(
                    self.executor.config,
                    self.executor.paths,
                    initialize=False,
                    run_mk8s_preflight=False,
                    plan_file=plan_file,
                    expected_plan_sha256=self.executor.cli._sha256_file(plan_file),
                    extra_env=self.executor.runtime_env,
                    assert_authority=self.assert_held,
                )
        finally:
            self.activating = False

    def restore_activations(self) -> None:
        from .nat_gateway_recovery import restore_activations

        restore_activations(self, NatGatewaySDK)

    def _complete_activation(self) -> None:
        if not self.rows:
            self.finalize_retirement()
            return
        assert self.state is not None
        outputs = self.outputs()
        from . import nat_gateway_probes

        prepared_probes: dict[str, Any] = {}
        desired: dict[str, str] = {}
        external_transitions: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        for row in self.rows:
            inputs = self._inputs(row, outputs)
            record = self.state.value["gateways"][row["instance_id"]]
            own = module_outputs(outputs, row["instance_id"])
            record["outputs"], record["network_id"] = own, inputs["network_id"]
            record["activation"] = {
                "id": uuid.uuid4().hex,
                "phase": "preflight",
                "previous_phase": record["phase"],
                "attachments": {},
                "probes": {},
            }
            self.state.save()
            with NatGatewaySDK(inputs["parent_id"]) as sdk:
                known_hosts = None
                if row["routing"]["enabled"]:
                    for key, policy in row["routing"]["policies"].items():
                        sdk.check_source_policy(policy, inputs["network_id"])
                        sdk.workload_policy(
                            own["route_table_ids"][key],
                            inputs["network_id"],
                            policy,
                            own["private_allocation_id"],
                        )
                    if set(inputs["source_cidrs"]) != set(sdk.network_cidrs(inputs["network_id"])):
                        raise RuntimeError(
                            "NAT network ranges differ from the reviewed source policy"
                        )
                    sdk.gateway(own, inputs)
                    known_hosts = prepare_ssh_host_trust(
                        sdk,
                        own,
                        self.executor.report_paths,
                        self.executor.options.ssh_known_hosts_file,
                    )
                    verify_guest(
                        own,
                        inputs,
                        known_hosts=known_hosts,
                        private_key=self.executor.options.ssh_private_key,
                    )
                for key, target in record["targets"].items():
                    if target["owner"] == "terraform":
                        target["subnet_id"] = module_outputs(outputs, target["vpc_instance"])[
                            "subnet_ids"
                        ][target["subnet_key"]]
                    if target["subnet_id"] == own["subnet_id"]:
                        raise RuntimeError("Gateway subnet cannot consume its own NAT route")
                    subnet = sdk.subnet(target["subnet_id"], record["network_id"])
                    route = (
                        own["route_table_ids"][target["policy"]]
                        if row["routing"]["enabled"]
                        else target["original"]
                    )
                    record["activation"]["attachments"][key] = {
                        "before": subnet.spec.route_table_id or "",
                        "effective_before": subnet.status.route_table.id,
                        "desired": route,
                    }
                    if target["owner"] == "terraform":
                        expected = target["installed"] or target["original"]
                        if row["routing"]["enabled"] and subnet.spec.route_table_id not in {
                            expected,
                            route,
                        }:
                            raise RuntimeError("Terraform-owned NAT subnet attachment drift")
                        desired[target["subnet_id"]] = route
                        target["pending"] = {
                            "desired": route,
                            "expected": expected if row["routing"]["enabled"] else route,
                            "operation_id": "terraform",
                        }
                        self.overrides[key] = route or None
                    else:
                        external_transitions.append((record, target, route))
                self.state.save()
                prepared_probes[row["instance_id"]] = nat_gateway_probes.preflight(
                    sdk, row, record, to_plain_data(self.executor.config)
                )
                prepared_probe = prepared_probes[row["instance_id"]]
                if prepared_probe is not None:
                    assert known_hosts is not None
                    access = self.stack.enter_context(
                        ProbeSSH(
                            own,
                            inputs,
                            known_hosts=known_hosts,
                            private_key=self.executor.options.ssh_private_key,
                        )
                    )
                    access.preflight(assert_held=self.assert_held)
                    prepared_probe["ssh"] = access
                record["activation"]["phase"] = "activating"
                self.state.save()
        self._apply_routes(desired, "Admit NAT route activation")
        for record, target, route in external_transitions:
            with NatGatewaySDK(record["project_id"]) as sdk:
                self._attach(sdk, record, target, route)
        self.verify()
        for row in self.rows:
            record = self.state.value["gateways"][row["instance_id"]]
            if prepared_probes[row["instance_id"]] is not None:
                record["activation"]["phase"] = "verifying"
                self.state.save()
                with NatGatewaySDK(record["project_id"]) as sdk:
                    nat_gateway_probes.run(
                        sdk,
                        record,
                        prepared_probes[row["instance_id"]],
                        save=self.state.save,
                        assert_held=self.assert_held,
                    )
        for row in self.rows:
            record = self.state.value["gateways"][row["instance_id"]]
            for target in record["targets"].values():
                target["installed"] = (
                    record["outputs"]["route_table_ids"][target["policy"]]
                    if row["routing"]["enabled"]
                    else ""
                )
                target["pending"] = None
            record["phase"] = "active" if row["routing"]["enabled"] else "detached"
            record["activation"]["phase"] = "complete"
            self.state.save()

        self.finalize_retirement()

    def finalize_retirement(self) -> None:
        if self.state is None:
            return
        current = {row["instance_id"] for row in self.rows}
        if any(
            activation_pending(record)
            for name, record in self.state.value["gateways"].items()
            if name not in current
        ):
            raise RuntimeError(
                "NAT retirement blocked by unfinished activation or pending probe cleanup"
            )
        retired = [
            name
            for name, record in self.state.value["gateways"].items()
            if name not in current and record["phase"] == "detached"
        ]
        if not retired:
            return
        raw = self.executor.cli.terraform_show_json(
            self.executor.paths.infra_dir, extra_env=self.executor.runtime_env, initialize=False
        )
        resources = self.executor.cli._terraform_state_resources(raw)
        for name in retired:
            prefix = f"module.{self.state.value['gateways'][name]['module_name']}."
            if any(resource.get("address", "").startswith(prefix) for resource in resources):
                raise RuntimeError("NAT retirement has not converged in Terraform state")
            record = self.state.value["gateways"][name]
            record["phase"], record["outputs"] = "retired", {}
            self.state.save()

    def verify(self) -> None:
        if not self.rows:
            return
        assert self.state is not None
        for row in self.rows:
            record = self.state.value["gateways"][row["instance_id"]]
            with NatGatewaySDK(record["project_id"]) as sdk:
                if row["routing"]["enabled"]:
                    own = record["outputs"]
                    inputs = {
                        **self._inputs(row, self.outputs()),
                        "network_id": record["network_id"],
                    }
                    sdk.gateway(own, inputs)
                    for key, policy in row["routing"]["policies"].items():
                        sdk.workload_policy(
                            own["route_table_ids"][key],
                            record["network_id"],
                            policy,
                            own["private_allocation_id"],
                        )
                    verify_guest(
                        own,
                        inputs,
                        known_hosts=resolve_ssh_known_hosts_file(
                            self.executor.report_paths, self.executor.options.ssh_known_hosts_file
                        ),
                        private_key=self.executor.options.ssh_private_key,
                    )
                for target in record["targets"].values():
                    expected = (
                        record["outputs"]["route_table_ids"][target["policy"]]
                        if row["routing"]["enabled"]
                        else target["original"]
                    )
                    observed = sdk.subnet(target["subnet_id"], record["network_id"])
                    if observed.spec.route_table_id != expected or (
                        expected and observed.status.route_table.id != expected
                    ):
                        raise RuntimeError("NAT route postconditions have not converged")
            if not row["routing"]["enabled"]:
                self._assert_no_consumers(record)
