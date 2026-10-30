"""Retirement preparation from exact state addresses and authenticated cloud reads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .nebius_api_helpers import bounded_nebius_request_kwargs, sdk_message_to_mapping
from .ssh_host_identity import recovered_identity
from .tailscale_config import members, validate
from .tailscale_enrollment import KeyStore, contract, receipt, resolve_key_id, revoke, verify_node
from .tailscale_ssh import Guest, asset


class Cloud:
    def __init__(self, sdk: Any) -> None:
        self.sdk = sdk

    def read(self, kind: str, identity: str) -> dict[str, Any] | None:
        from nebius.aio.service_error import RequestError
        from nebius.api.nebius.compute import v1 as compute
        from nebius.api.nebius.vpc import v1 as vpc

        namespace = compute if kind in {"Instance", "Disk"} else vpc
        client = getattr(namespace, kind + "ServiceClient")(self.sdk.sdk)
        try:
            obj = client.get(
                getattr(namespace, "Get" + kind + "Request")(id=identity),
                **bounded_nebius_request_kwargs(timeout_seconds=30),
            ).wait()
            result = dict(sdk_message_to_mapping(obj))
        except RequestError as error:
            if error.status.code.name == "NOT_FOUND":
                return None
            raise RuntimeError(
                "Tailscale retirement cloud read denied or unavailable; absence is unproven"
            ) from None
        except Exception:
            raise RuntimeError(
                "Tailscale retirement cloud read unavailable; absence is unproven"
            ) from None
        if result.get("metadata", {}).get("id") != identity:
            raise RuntimeError("Tailscale retirement cloud identity mismatch")
        return result


def addresses(module: str, row: dict[str, Any]) -> dict[str, tuple[str, str]]:
    base = "module." + module + "."
    result = {base + "nebius_vpc_v1_security_group.gateway": ("SecurityGroup", "")}
    for name in ("tailscale", "icmp", "egress"):
        result[base + "nebius_vpc_v1_security_rule." + name] = ("SecurityRule", "")
    for cidr in row["inputs"]["management_cidrs"]:
        result[base + "nebius_vpc_v1_security_rule.ssh[" + json.dumps(cidr) + "]"] = (
            "SecurityRule",
            "",
        )
    for member in members(row):
        prefix = base + 'module.member["' + member + '"].'
        result[prefix + "nebius_compute_v1_instance.vm"] = ("Instance", member)
        result[prefix + "nebius_compute_v1_disk.boot[0]"] = ("Disk", member)
        result[base + 'nebius_vpc_v1_allocation.public["' + member + '"]'] = ("Allocation", member)
    return result


FIELDS = {"Instance": "instance_id", "Disk": "boot_disk_id", "Allocation": "public_allocation_id"}


def _never_activated(record: dict[str, Any]) -> bool:
    """Require durable proof, never infer it from an absent current node."""
    return record.get("activation_started") is False and not any(
        own.get("enrollment")
        or own.get("node_id")
        or own.get("prior_enrollments")
        or own.get("replacement")
        for own in record["members"].values()
    )


def prepare(
    owner: Any, row: dict[str, Any], state: list[dict[str, Any]], sdk: Any
) -> dict[str, Any]:
    """No capacity, provisioning, enrollment, key creation, or mandatory outputs."""
    journal = owner.open()
    cfg, inputs = validate(row), row["inputs"]
    record = journal.value["gateways"].setdefault(
        row["instance_id"],
        {
            "tailnet": cfg["tailnet"],
            "members": {},
            "lifecycle": "retiring",
            "activation_started": True,
        },
    )
    if record["tailnet"] != cfg["tailnet"] and not _never_activated(record):
        raise RuntimeError("Retirement tailnet differs from its owner")
    module = asset(owner.executor.manifest, row["instance_id"])["module_name"]
    expected = addresses(module, row)
    entries = record.setdefault("resources", {})
    candidates = dict(entries)
    state_by_address = {}
    for resource in state:
        address = resource.get("address", "")
        if (
            not address.startswith("module." + module + ".")
            or resource.get("mode", "managed") != "managed"
        ):
            continue
        if resource.get("type") == "terraform_data":
            continue
        if address not in expected:
            raise RuntimeError(
                "Unexpected Tailscale resource address; retirement ownership is unresolved"
            )
        if address in state_by_address:
            raise RuntimeError("Duplicate Tailscale resource address")
        state_by_address[address] = resource
        identity = resource.get("values", {}).get("id")
        if not identity:
            raise RuntimeError("Tailscale state contains a resource without an identity")
        kind, member = expected[address]
        candidate = {"kind": kind, "id": identity, "member": member}
        if address in entries and entries[address] != candidate:
            raise RuntimeError("Tailscale state differs from recorded retirement ownership")
        candidates[address] = candidate
    # Journal IDs survive refreshed state after partial destruction.
    for address, (kind, member) in expected.items():
        identity = record["members"].get(member, {}).get(FIELDS.get(kind, ""))
        if identity:
            candidate = {"kind": kind, "id": identity, "member": member}
            if address in candidates and candidates[address] != candidate:
                raise RuntimeError("Tailscale member binding differs from Terraform state")
            candidates[address] = candidate
    cloud = Cloud(sdk)
    live = {}
    for address, entry in candidates.items():
        if address not in expected or (entry["kind"], entry["member"]) != expected[address]:
            raise RuntimeError("Unsupported Tailscale retirement resource binding")
        journal.assert_held()
        obj = cloud.read(entry["kind"], entry["id"])
        live[address] = obj
        if obj is None:
            continue
        metadata, spec = obj["metadata"], obj.get("spec", {})
        if entry["kind"] == "SecurityRule":
            group = candidates.get("module." + module + ".nebius_vpc_v1_security_group.gateway", {})
            if metadata.get("parent_id") != group.get("id") or not group.get("id"):
                raise RuntimeError("Tailscale security rule parent mismatch")
        else:
            labels = metadata.get("labels", {})
            if (
                metadata.get("parent_id") != inputs["parent_id"]
                or labels.get("cxcli-component") != "tailscale-gw"
            ):
                raise RuntimeError("Tailscale cloud project or component ownership mismatch")
            if entry["member"] and labels.get("cxcli-member") != entry["member"]:
                raise RuntimeError("Tailscale cloud member ownership mismatch")
        if entry["kind"] == "SecurityGroup" and spec.get("network_id") != inputs["network_id"]:
            raise RuntimeError("Tailscale security group VPC mismatch")
        if (
            entry["kind"] == "Allocation"
            and spec.get("ipv4_public", {}).get("subnet_id") != inputs["subnet_id"]
        ):
            raise RuntimeError("Tailscale allocation subnet mismatch")
    if any(live.values()):
        subnet = cloud.read("Subnet", inputs["subnet_id"])
        network = cloud.read("Network", inputs["network_id"])
        if (
            not subnet
            or not network
            or subnet["metadata"].get("parent_id") != inputs["parent_id"]
            or network["metadata"].get("parent_id") != inputs["parent_id"]
            or subnet.get("spec", {}).get("network_id") != inputs["network_id"]
        ):
            raise RuntimeError("Retirement project/network/subnet identity is unresolved")
    outputs = {}
    keys, bootstraps = {}, {}
    for member in members(row):
        known = record["members"].setdefault(member, {})
        matching = [
            (address, entry) for address, entry in candidates.items() if entry["member"] == member
        ]
        binding = {FIELDS[entry["kind"]]: entry["id"] for _, entry in matching}
        vm_entry = next(
            ((address, entry) for address, entry in matching if entry["kind"] == "Instance"), None
        )
        vm = live[vm_entry[0]] if vm_entry else None
        if vm and vm_entry is not None:
            spec = vm["spec"]
            interfaces = spec.get("network_interfaces", [])
            if (
                len(interfaces) != 1
                or interfaces[0].get("subnet_id") != inputs["subnet_id"]
                or spec.get("boot_disk", {}).get("existing_disk", {}).get("id")
                != binding.get("boot_disk_id")
                or interfaces[0].get("public_ip_address", {}).get("allocation_id")
                != binding.get("public_allocation_id")
            ):
                raise RuntimeError("Tailscale VM disk or network allocation binding mismatch")
            body = spec.get("cloud_init_user_data", "")
            identity = recovered_identity(
                SimpleNamespace(spec=SimpleNamespace(cloud_init_user_data=body))
            )
            sha = hashlib.sha256(body.encode()).hexdigest()
            prior = (
                state_by_address.get(vm_entry[0], {}).get("values", {}).get("cloud_init_user_data")
            )
            if (
                not identity
                or (prior is not None and prior != body)
                or known.get("bootstrap_sha256") not in {None, sha}
                or known.get("host_public_key") not in {None, identity["ed25519_public"]}
            ):
                raise RuntimeError("Tailscale retirement bootstrap or SSH identity mismatch")
            known.update(host_public_key=identity["ed25519_public"], bootstrap_sha256=sha)
            keys[member], bootstraps[member] = identity, body
            status_interfaces = vm.get("status", {}).get("network_interfaces", [])
            if len(status_interfaces) == 1:
                iface = status_interfaces[0]
                public = iface.get("public_ip_address", {})
                if public.get("allocation_id") == binding.get(
                    "public_allocation_id"
                ) and public.get("address"):
                    outputs[member] = {
                        **binding,
                        "public_ip": public["address"],
                        "private_ip": iface.get("ip_address", {}).get("address", ""),
                        "network_id": inputs["network_id"],
                        "subnet_id": inputs["subnet_id"],
                    }
        known.update(
            binding,
            project_id=inputs["parent_id"],
            network_id=inputs["network_id"],
            subnet_id=inputs["subnet_id"],
            member=member,
            vm_absent=vm is None,
        )
    if record.get("retired") and any(live.values()):
        raise RuntimeError("Retired Tailscale owner unexpectedly contains live resources")
    record.update(resources=candidates, retiring=True)
    journal.save()  # Ownership is recorded only after all authenticated checks succeeded.
    if keys:
        owner.executor.runtime_env[f"TF_VAR_{module}_ssh_host_keys"] = json.dumps(keys)
        owner.executor.runtime_env[f"TF_VAR_{module}_bootstrap_user_data"] = json.dumps(bootstraps)
    return outputs


def guest_for(owner: Any, row: dict[str, Any], output: dict[str, Any], sdk: Any) -> Guest:
    key = validate(row).get("auth", {}).get("ssh_private_key_file")
    private = Path(key).expanduser() if key else None
    if private and not private.is_absolute():
        private = owner.executor.report_paths.project_dir / private
    return Guest(sdk, owner.executor.paths, output, row["inputs"], private)


def retire_members(owner: Any, row: dict[str, Any], output: dict[str, Any], sdk: Any) -> None:
    journal = owner.open()
    cfg = validate(row)
    record = journal.value["gateways"][row["instance_id"]]
    if _never_activated(record):
        # capture() has independently checked exact Terraform/Compute ownership.
        # Never infer this proof merely from a missing node ID.
        for own in record["members"].values():
            own["phase"] = "RETIRING"
        record["lifecycle"] = "retiring"
        journal.save()
        return
    api, store = owner.api(row), KeyStore(owner.executor.report_paths.project_dir)
    for member, own in record["members"].items():
        journal.assert_held()
        guest = None
        observation = None
        if not own.get("vm_absent"):
            try:
                guest = guest_for(owner, row, output[member], sdk)
                guest.install(asset(owner.executor.manifest, row["instance_id"]))
                observation = guest.call("retire")  # Permanent guest fence, no enrollment.
            except (RuntimeError, OSError, KeyError):
                # Only an already proven identity permits retiring an unreachable guest.
                if not own.get("node_id"):
                    raise RuntimeError(
                        "Unknown guest enrollment identity; restore authenticated reachability before retirement"
                    ) from None
                guest = None
        op = own.get("enrollment")
        if op:
            original_phase = own.setdefault("enrollment_fence_phase", op["phase"])
            if op["mode"] == "oauth":
                resolve_key_id(journal, op, api, store)
            if guest:
                found = receipt(guest, op, fence=True)
                current, saved = found["guest"], found["receipt"]
                if current.get("node_id"):
                    if not saved or saved["phase"] not in {"started", "enrolled"}:
                        raise RuntimeError("Unknown device identity blocks retirement")
                    node = verify_node(current, op, None if own.get("node_id") else api)
                    if own.get("node_id") not in {None, node}:
                        raise RuntimeError("Retirement node differs from its owner")
                    own["node_id"] = node
                    journal.save()
                elif saved and saved["phase"] in {"started", "enrolled"} and not own.get("node_id"):
                    raise RuntimeError(
                        "Started enrollment outcome is uncertain; device reconciliation required before destruction"
                    )
            elif not own.get("node_id") and original_phase not in {
                "issuing",
                "rejected",
                "key-ready",
                "revoked",
                "retired",
            }:
                raise RuntimeError("Unknown started enrollment identity blocks destructive cleanup")
            revoke(journal, own, api, store)
            if guest:
                guest.call("enrollment-forget-key", enrollment=contract(op))
            op["phase"] = "revoked"
            journal.save()
        elif observation and observation.get("node_id") != own.get("node_id", ""):
            raise RuntimeError("Unknown device on guest blocks retirement")
        if own.get("node_id"):
            if api is None:
                raise RuntimeError(
                    "Device retirement requires scoped OAuth device-delete credentials"
                )
            if observation and observation.get("node_id") not in {"", own["node_id"]}:
                raise RuntimeError("Retirement guest node differs from owned identity")
            journal.assert_held()
            api.delete_owned(own["node_id"], cfg["tags"])
            own["device_retired"] = True
            journal.save()
        if own.get("replacement"):
            if api is None:
                raise RuntimeError("Retiring a previous device requires scoped API credentials")
            journal.assert_held()
            api.delete_owned(own["replacement"]["node_id"], cfg["tags"])
            own.pop("replacement")
            journal.save()
        for previous in own.get("prior_enrollments", []):
            retired_key = {"enrollment": previous}
            revoke(journal, retired_key, api, store)
        own["phase"] = "RETIRING"
        journal.save()


def confirm(owner: Any, row: dict[str, Any], remaining: list[dict[str, Any]], sdk: Any) -> None:
    journal = owner.open()
    record = journal.value["gateways"][row["instance_id"]]
    module = asset(owner.executor.manifest, row["instance_id"])["module_name"]
    if any(
        r.get("address", "").startswith("module." + module + ".")
        and r.get("mode", "managed") == "managed"
        and r.get("type") != "terraform_data"
        for r in remaining
    ):
        raise RuntimeError("Terraform retirement is not confirmed")
    cloud = Cloud(sdk)
    for entry in record.get("resources", {}).values():
        journal.assert_held()
        if cloud.read(entry["kind"], entry["id"]) is not None:
            raise RuntimeError("Cloud resource removal is not independently confirmed")
    for own in record["members"].values():
        if own.get("node_id") and not own.get("device_retired"):
            raise RuntimeError("Device retirement remains unresolved")
        if own.get("enrollment"):
            if not own.get("key_retired"):
                raise RuntimeError("Enrollment key retirement remains unresolved")
            own["enrollment"]["phase"] = "retired"
        own["phase"] = "RETIRED"
    record["retired"] = True
    record["lifecycle"] = "retired"
    journal.save()
