"""Isolated bounded cloud/SSH readers. Only projected NDJSON leaves this process."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from .probe import read_regular
from .projection import MAX_BYTES, project, safe_identifier


def scope(path: Path, project_id: str | None = None, region: str | None = None) -> dict[str, Any]:
    try:
        config = yaml.safe_load(read_regular(path))
    except yaml.YAMLError:
        raise ValueError("invalid-data") from None
    if not isinstance(config, dict) or not isinstance(config.get("gateway_group"), dict):
        raise ValueError("invalid-data")
    group = config["gateway_group"]

    def expand(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda match: os.environ.get(match[1], match[0]),
            value,
        )

    for key in ("tenant_id", "project_id", "region_id"):
        if key in config:
            config[key] = expand(config[key])
    for key in ("name", "region"):
        if key in group:
            group[key] = expand(group[key])
    count_value = expand(group.get("instance_count"))
    if isinstance(count_value, str) and count_value.isdecimal():
        group["instance_count"] = int(count_value)
    if isinstance(group.get("external_ips"), list):
        group["external_ips"] = [
            [expand(address) for address in row] if isinstance(row, list) else row
            for row in group["external_ips"]
        ]
    vm = group.get("vm_spec")
    if isinstance(vm, dict):
        for key in ("ssh_username", "ssh_public_key", "ssh_private_key_path"):
            if key in vm:
                vm[key] = expand(vm[key])
    count = group.get("instance_count")
    if type(count) is not int or not 1 <= count <= 10 or not safe_identifier(group.get("name")):
        raise ValueError("invalid-data")
    # Avoid path traversal in scoped local state, without expanding secret placeholders.
    for value in (group["name"], project_id or config.get("project_id", "")):
        if not safe_identifier(value) or any(part in value for part in ("/", "..", ":")):
            raise ValueError("invalid-data")
    config["project_id"] = project_id or config.get("project_id")
    group["region"] = region or group.get("region") or config.get("region_id")
    return config


def emit(name: str, value: Any, outcome: str = "ok") -> None:
    omissions = [0]
    limits = [0]
    data = project(value, omitted=omissions, limited=limits)
    if outcome == "ok" and limits[0]:
        outcome = "source-limit"
    elif outcome == "ok" and value and not data and omissions[0]:
        outcome = "partial"
    print(
        json.dumps(
            {
                "probe": name,
                "data": data,
                "outcome": outcome,
                "observed_at": time.time(),
                "omitted_fields": omissions[0],
                "truncated_fields": limits[0],
            }
        ),
        flush=True,
    )


def cloud(
    config: dict[str, Any],
    operations: list[str] | None = None,
    resources: dict[str, Any] | None = None,
) -> None:
    from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]
    from nebius.api.nebius.common.v1 import GetByNameRequest, GetOperationRequest
    from nebius.api.nebius.compute.v1 import InstanceServiceClient
    from nebius.api.nebius.vpc.v1 import (
        AllocationServiceClient,
        GetAllocationRequest,
        GetNetworkRequest,
        GetRouteTableRequest,
        GetSecurityGroupRequest,
        GetSubnetRequest,
        ListRoutesRequest,
        ListSecurityRulesRequest,
        NetworkServiceClient,
        RouteServiceClient,
        RouteTableServiceClient,
        SecurityGroupServiceClient,
        SecurityRuleServiceClient,
        SubnetServiceClient,
    )

    from ..deploy.security_groups import capture
    from ..deploy.vm_ha_cloud import read_vm_ha_cloud, vm_ha_observation_budget
    from ..nebius_auth import build_operator_sdk_client

    deadline = time.monotonic() + 27
    sdk = build_operator_sdk_client(explicit_token=os.environ.get("NEBIUS_IAM_TOKEN"))
    seen: set[tuple[str, str]] = set()
    probe_counts: dict[str, int] = {}

    def next_probe(kind: str) -> str:
        index = probe_counts.get(kind, 0)
        probe_counts[kind] = index + 1
        return f"cloud/{kind}/{index}"

    def read(service: Any, method: str, request: Any, *, seconds: float = 27) -> Any:
        request_deadline = min(deadline, time.monotonic() + seconds)
        return read_vm_ha_cloud(
            lambda kwargs: getattr(service, method)(request, **kwargs).wait(),
            timeout_provider=lambda: request_deadline - time.monotonic(),
        )

    def export(name: str, value: Any) -> dict[str, Any]:
        raw = MessageToDict(value.__pb2_message__, preserving_proto_field_name=True)
        if name.startswith("cloud/allocation/"):
            status = raw.get("status", {})
            details = status.pop("details", {})
            status["allocation_details"] = {
                key: details[key]
                for key in ("allocated_cidr", "pool_id", "subnet_id")
                if key in details
            }
        emit(name, raw)
        return raw

    def related(kind: str, identifier: str, service: Any, request_type: Any) -> Any:
        if not identifier or (kind, identifier) in seen:
            return None
        seen.add((kind, identifier))
        try:
            result = read(service(sdk), "get", request_type(id=identifier))
            if (
                result.metadata.id != identifier
                or result.metadata.parent_id != config["project_id"]
            ):
                raise ValueError("invalid-data")
            export("cloud/" + kind + "/" + str(len(seen)), result)
            return result
        except Exception:
            emit("cloud/" + kind + "/" + str(len(seen)), {}, "unavailable")
            return None

    try:
        instances = []
        group = config["gateway_group"]
        for index in range(group["instance_count"]):
            name = f"{group['name']}-{index}"
            try:
                instance = read(
                    InstanceServiceClient(sdk),
                    "get_by_name",
                    GetByNameRequest(parent_id=config["project_id"], name=name),
                    seconds=5,
                )
                if (
                    instance.metadata.parent_id != config["project_id"]
                    or instance.metadata.name != name
                ):
                    raise ValueError("invalid-data")
                export(f"cloud/instance/{index}", instance)
                addresses = [
                    nic.public_ip_address.address
                    for nic in instance.status.network_interfaces
                    if nic.public_ip_address.address
                ]
                if addresses:
                    emit(
                        f"target/{index}",
                        {"hostname": name, "address": str(ipaddress.ip_interface(addresses[0]).ip)},
                    )
                instances.append((index, instance))
            except Exception:
                emit(f"cloud/instance/{index}", {}, "unavailable")
        recorded = resources or {}
        related(
            "allocation",
            recorded.get("allocation_id", ""),
            AllocationServiceClient,
            GetAllocationRequest,
        )
        for index, target in enumerate(recorded.get("route_targets", [])[:32]):
            subnet = related(
                "subnet", target["workload_subnet_id"], SubnetServiceClient, GetSubnetRequest
            )
            table = related(
                "route-table",
                target["route_table_id"],
                RouteTableServiceClient,
                GetRouteTableRequest,
            )
            if table is None or subnet is None:
                continue
            if (
                table.metadata.parent_id != config["project_id"]
                or table.spec.network_id != target["network_id"]
                or subnet.spec.network_id != target["network_id"]
            ):
                emit(f"cloud/workload-routes/{index}", {}, "invalid-data")
                continue
            effective_table = subnet.status.route_table.id or subnet.spec.route_table_id
            if effective_table != target["route_table_id"]:
                emit(
                    f"cloud/workload-routes/{index}",
                    {
                        "route_table_id": target["route_table_id"],
                        "observed_route_table_id": effective_table,
                    },
                    "changed",
                )
                continue
            try:
                page = read(
                    RouteServiceClient(sdk),
                    "list",
                    ListRoutesRequest(parent_id=target["route_table_id"], page_size=1000),
                )
                export(f"cloud/workload-routes/{index}", page)
                if page.next_page_token:
                    emit(next_probe("routes-limit"), {}, "source-limit")
            except Exception:
                emit(f"cloud/workload-routes/{index}", {}, "unavailable")
        for index, identifier in enumerate((operations or [])[:16]):
            observed = False
            for service in (InstanceServiceClient, AllocationServiceClient):
                try:
                    operation = read(
                        service(sdk).operation_service(), "get", GetOperationRequest(id=identifier)
                    )
                    if operation.id != identifier:
                        raise ValueError("invalid-data")
                    status = operation.status()
                    emit(
                        f"cloud/operation/{index}",
                        {
                            "operation_id": identifier,
                            "done": operation.done(),
                            "failure_code": status.code.value[0] if status else None,
                        },
                    )
                    observed = True
                    break
                except Exception:
                    pass
            if not observed:
                emit(f"cloud/operation/{index}", {"operation_id": identifier}, "unavailable")
        for index, instance in instances:
            try:
                for nic in instance.spec.network_interfaces:
                    subnet = related("subnet", nic.subnet_id, SubnetServiceClient, GetSubnetRequest)
                    if subnet:
                        related(
                            "network",
                            subnet.spec.network_id,
                            NetworkServiceClient,
                            GetNetworkRequest,
                        )
                        table = subnet.status.route_table.id or subnet.spec.route_table_id
                        if table:
                            probe_name = next_probe("routes")
                            try:
                                page = read(
                                    RouteServiceClient(sdk),
                                    "list",
                                    ListRoutesRequest(parent_id=table, page_size=1000),
                                )
                                export(probe_name, page)
                                if page.next_page_token:
                                    emit(next_probe("routes-limit"), {}, "source-limit")
                            except Exception:
                                emit(probe_name, {}, "unavailable")
                for nic in instance.status.network_interfaces:
                    for allocation in (nic.public_ip_address, nic.ip_address):
                        related(
                            "allocation",
                            allocation.allocation_id,
                            AllocationServiceClient,
                            GetAllocationRequest,
                        )
                try:
                    with vm_ha_observation_budget(seconds=max(0.01, deadline - time.monotonic())):
                        attachments = capture(sdk, instance)
                    emit(f"cloud/attachments/{index}", [asdict(item) for item in attachments])
                    for attachment in attachments:
                        for identifier in attachment.effective_ids:
                            related(
                                "security-group",
                                identifier,
                                SecurityGroupServiceClient,
                                GetSecurityGroupRequest,
                            )
                            rules = read(
                                SecurityRuleServiceClient(sdk),
                                "list",
                                ListSecurityRulesRequest(parent_id=identifier, page_size=1000),
                            )
                            export(next_probe("security-rules"), rules)
                            if rules.next_page_token:
                                emit(next_probe("security-rules-limit"), {}, "source-limit")
                except Exception:
                    emit(next_probe("auxiliary"), {}, "unavailable")
            except Exception:
                emit(next_probe("auxiliary"), {}, "unavailable")
    finally:
        sdk.sync_close()


def remote(config: dict[str, Any], request: dict[str, Any]) -> None:
    import paramiko  # type: ignore[import-untyped]

    from ..deploy.ssh_client_auth import resolve_ssh_client_auth, select_operator_identity
    from ..deploy.ssh_policy import (
        KNOWN_HOSTS_ENV,
        VMHASSHTrustScope,
        configure_paramiko_host_verification,
        managed_ssh_trust_available,
        require_vm_ha_ssh_policy,
    )

    group = config["gateway_group"]
    ha = group.get("vm_ha") or {}
    trust = VMHASSHTrustScope(
        tenant_id=str(config.get("tenant_id") or ""),
        project_id=config["project_id"],
        region_id=str(group.get("region") or ""),
        gateway_name=group["name"],
        cluster_id=str(ha.get("cluster_id") or "") if ha.get("enabled") is True else "ordinary-v1",
    )
    targets = request["targets"]
    hostname = request["hostname"]
    target = str(ipaddress.ip_address(targets[hostname]))
    policy = None
    if (
        ha.get("enabled") is True
        or KNOWN_HOSTS_ENV in os.environ
        or managed_ssh_trust_available(trust)
    ):
        pairs = (
            tuple(
                (
                    f"{group['name']}-{i}",
                    targets.get(f"{group['name']}-{i}", f"{group['name']}-{i}"),
                )
                for i in range(group["instance_count"])
            )
            if KNOWN_HOSTS_ENV not in os.environ
            else ((hostname, target),)
        )
        policy = require_vm_ha_ssh_policy(pairs, enrollment_hosts=set(), trust_scope=trust)
    vm = group.get("vm_spec") or {}
    key = vm.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
    auth = (
        select_operator_identity(Path(request["identity"]))
        if request.get("identity")
        else resolve_ssh_client_auth(
            vm.get("ssh_public_key"), explicit_private_key=Path(key).expanduser() if key else None
        )
    )
    client = paramiko.SSHClient()
    configure_paramiko_host_verification(
        client, paramiko, policy=policy, hostname=hostname, transport_host=target
    )
    try:
        client.connect(
            hostname=target,
            username=vm.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu"),
            timeout=15,
            auth_timeout=15,
            banner_timeout=15,
            **auth.paramiko_connect_kwargs(),
        )
        folder = Path(__file__).parent
        projection = (folder / "projection.py").read_text()
        probe = (
            (folder / "probe.py")
            .read_text()
            .replace("from __future__ import annotations", "")
            .replace("from .projection import ENUMS, MAX_BYTES, project", "")
        )
        payload = {"since": request["since"], "until": request["until"], "budget": 180}
        script = (
            projection
            + "\n"
            + probe
            + "\ncollect_probe("
            + repr(payload)
            + ", emit=lambda record: print(json.dumps(record), flush=True))\n"
        )
        transport = client.get_transport()
        if transport is None:
            raise RuntimeError("ssh-transport")
        channel = transport.open_session(timeout=15)
        channel.settimeout(15)
        channel.exec_command("sudo -n /usr/bin/python3 -I -")
        channel.sendall(script.encode())
        channel.shutdown_write()
        deadline = time.monotonic() + 190
        buffer = bytearray()
        received = 0
        while not channel.exit_status_ready() or channel.recv_ready():
            if time.monotonic() >= deadline:
                raise TimeoutError("timeout")
            if channel.recv_ready():
                chunk = channel.recv(65536)
                received += len(chunk)
                buffer.extend(chunk)
                if len(buffer) > MAX_BYTES or received > 32 * MAX_BYTES:
                    raise ValueError("source-limit")
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    record = json.loads(line)
                    # Parent validates the envelope and re-projects every returned field.
                    print(json.dumps(record), flush=True)
            if channel.recv_stderr_ready():
                channel.recv_stderr(65536)  # Never print or persist arbitrary stderr.
            time.sleep(0.01)
        if channel.recv_exit_status() != 0:
            raise RuntimeError("ssh-transport")
        if buffer:
            raise ValueError("invalid-data")
    finally:
        client.close()


def main() -> None:
    request = json.loads(sys.stdin.buffer.read(MAX_BYTES))
    logging.disable(logging.CRITICAL)
    try:
        config = scope(Path(request["config"]), request.get("project"), request.get("region"))
        with open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):
            if request["action"] == "cloud":
                cloud(config, request.get("operations"), request.get("resources"))
            elif request["action"] == "remote":
                remote(config, request)
            else:
                raise ValueError("invalid-data")
    except Exception as error:
        from paramiko import (  # type: ignore[import-untyped]
            AuthenticationException,
            BadHostKeyException,
        )

        reason = "unavailable"
        if isinstance(error, TimeoutError):
            reason = "timeout"
        elif isinstance(error, AuthenticationException):
            reason = "authentication"
        elif isinstance(error, BadHostKeyException):
            reason = "ssh-transport"
        elif isinstance(error, PermissionError):
            reason = "permission"
        emit("worker", {"reason": reason}, "timeout" if reason == "timeout" else "unavailable")


if __name__ == "__main__":
    main()
