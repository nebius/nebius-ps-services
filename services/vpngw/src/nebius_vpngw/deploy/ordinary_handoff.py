"""Pinned SSH exclusion while the existing ordinary-to-HA workflow takes ownership.

The holder owns admission and approved package actions. Existing lifecycle,
credentials, apply locks and verified HA status retain migration and activation
authority.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import shlex
from collections.abc import Iterator
from typing import Any

from ..handoff_bootstrap import streamed_source
from ..ordinary_operations import digest
from .ssh_policy import configure_paramiko_host_verification

# Source is embedded so a 0.6.0 ordinary guest can participate before installation.
_REMOTE = "import _vpngw_handoff_remote as handoff; handoff.main()"


@contextlib.contextmanager
def _connection(ssh: Any, target: str, instance: Any, local: dict) -> Iterator[Any]:
    paramiko = ssh._ensure_paramiko()
    client = paramiko.SSHClient()
    try:
        configure_paramiko_host_verification(
            client,
            paramiko,
            policy=ssh._ssh_policy,
            hostname=instance.hostname,
            transport_host=target,
        )
        spec = (local.get("gateway_group") or {}).get("vm_spec") or {}
        ssh._connect_client(
            client,
            hostname=target,
            username=spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu"),
            vm_spec=spec,
        )
        yield client
    finally:
        client.close()


def _start(client: Any, request: dict) -> tuple[Any, Any]:
    stdin, stdout, _stderr = client.exec_command(
        "sudo -n /usr/bin/python3 -B -c "
        + shlex.quote(
            "import base64,sys; raw=sys.stdin.readline(4194305); "
            "assert len(raw)<=4194304 and raw.endswith('\\n'); "
            "exec(compile(base64.b64decode(raw.strip(),validate=True),'<ha-bootstrap>','exec'))"
        ),
        timeout=630,
    )
    source = base64.b64encode((streamed_source() + _REMOTE).encode()).decode()
    if len(source) > 4 * 1024 * 1024:
        raise RuntimeError("HA handoff source bundle exceeds limit")
    stdin.write(source + "\n")
    stdin.flush()
    _send(stdin, request)
    return stdin, stdout


def _send(stdin: Any, request: dict) -> None:
    payload = json.dumps(request, separators=(",", ":")) + "\n"
    if len(payload.encode()) > 128 * 1024 * 1024:
        raise RuntimeError("HA handoff request exceeds its limit")
    stdin.write(payload)
    stdin.flush()


def _receive(stdout: Any) -> dict:
    value = json.loads(stdout.readline(2 * 1024 * 1024 + 1))
    if not isinstance(value, dict):
        raise RuntimeError("Ordinary-to-HA admission response invalid")
    return value


def _ha_identity(instance: Any) -> dict:
    import yaml

    config = yaml.safe_load(instance.config_yaml)
    return dict(
        cluster_id=config["vm_ha"]["cluster_id"],
        node_id=instance.vm_ha_node.node_id,
        generation_id=instance.vm_ha_generation.generation_id,
    )


def inspect(ssh: Any, target: str, instance: Any, local: dict) -> dict:
    with _connection(ssh, target, instance, local) as client:
        _stdin, stdout = _start(client, {"action": "inspect", "ha": _ha_identity(instance)})
        value = _receive(stdout)
        if set(value) != {
            "journal",
            "pending",
            "environment",
            "mode",
            "lock",
            "boot_id",
            "config_sha256",
            "unstarted",
        } or value["mode"] not in {"ordinary", "ha"}:
            raise RuntimeError("Ordinary-to-HA admission observation invalid")
        return value


def inspect_repair(ssh: Any, target: str, instance: Any, local: dict, *, expected: dict) -> dict:
    with _connection(ssh, target, instance, local) as client:
        _stdin, stdout = _start(client, {"action": "repair-evidence", "repair": expected})
        value = _receive(stdout)
        if value.get("forwarding") is not False or any(
            value.get("lock", {}).get(k) != expected[k]
            for k in ("cluster_id", "node_id", "generation_id", "operation_id")
        ):
            raise RuntimeError("HA package repair peer admission is unverified")
        return value


class Handoff:
    def __init__(
        self,
        stdin: Any,
        stdout: Any,
        *,
        ha: dict,
        package: Any = None,
        peer_observer: Any = None,
        peer: dict | None = None,
    ):
        self.stdin, self.stdout, self.ha = stdin, stdout, ha
        self.finished = False
        self.package, self.peer_observer, self.peer = package, peer_observer, peer
        self.package_receipt: dict | None = None

    def _peer_request(self) -> dict:
        if self.peer_observer is None:
            return {}
        current = self.peer_observer()
        if current != self.peer:
            raise RuntimeError("HA repair peer admission changed after approval")
        return {"peer": current, "expected_peer": self.peer}

    def prepare_package(self, plan: Any) -> dict:
        if self.package is None or plan.digest != self.package.digest:
            raise RuntimeError("HA handoff package plan changed")
        wheel = plan.wheel.read_bytes()
        from .ordinary_apply import sha

        if sha(wheel) != plan.manifest["wheel_sha256"]:
            raise RuntimeError("HA package changed after approval")
        for path in plan.dependency_paths:
            if sha(path.read_bytes()) != plan.manifest["dependency_wheels"][path.name]:
                raise RuntimeError("HA dependency changed after approval")
        # Reservation already staged the bound wheels. Probe the peer only after
        # upload, immediately before this small named package-write request.
        _send(
            self.stdin,
            dict(action="prepare-package", plan_digest=plan.digest, **self._peer_request()),
        )
        receipt = _receive(self.stdout)
        if receipt.get("artifact_sha256") != plan.manifest["wheel_sha256"]:
            raise RuntimeError("HA handoff package receipt is unverified")
        self.package_receipt = receipt
        return receipt

    def finish_repair(self, *, operation_id: str) -> None:
        _send(self.stdin, dict(action="resume-repair", **self._peer_request()))
        status = _receive(self.stdout)
        self._peer_request()
        self.complete(status, operation_id=operation_id)

    def publish_activation(self, *, receipt: Any, operation_id: str) -> None:
        from dataclasses import asdict

        _send(
            self.stdin,
            dict(
                action="publish-activation",
                receipt=asdict(receipt),
                operation_id=operation_id,
                **self._peer_request(),
            ),
        )
        if _receive(self.stdout) != {"published": True}:
            raise RuntimeError("HA handoff activation publication is unverified")

    def complete(self, payload: dict, *, operation_id: str) -> None:
        if (
            any(payload.get(k) != v for k, v in self.ha.items())
            or payload.get("data_plane_mode") != "passive"
            or payload.get("apply_locked") is not True
            or payload.get("apply_operation_id") != operation_id
        ):
            raise RuntimeError("Ordinary-to-HA takeover status is not exact and locked-passive")
        self.stdin.write(json.dumps({"action": "complete", "operation": operation_id}) + "\n")
        self.stdin.flush()
        if _receive(self.stdout) != {"complete": True}:
            raise RuntimeError("Ordinary-to-HA takeover acknowledgement missing")
        self.finished = True


@contextlib.contextmanager
def reserve(
    ssh: Any,
    target: str,
    instance: Any,
    local: dict,
    *,
    observed: dict,
    approval: str,
    artifact: str,
    package: Any = None,
    peer_observer: Any = None,
    peer: dict | None = None,
) -> Iterator[Handoff]:
    if ssh._ssh_policy is None or instance.vm_ha_node is None or instance.vm_ha_generation is None:
        raise RuntimeError("Ordinary-to-HA admission requires the existing pinned migration plan")
    config = json.loads(json.dumps(__import__("yaml").safe_load(instance.config_yaml)))
    ha = dict(
        cluster_id=config["vm_ha"]["cluster_id"],
        node_id=instance.vm_ha_node.node_id,
        generation_id=instance.vm_ha_generation.generation_id,
    )
    binding = digest({"hostname": instance.hostname, "ha": ha})
    with _connection(ssh, target, instance, local) as client:
        stdin, stdout = _start(
            client,
            dict(
                action="reserve",
                observed=observed,
                binding=binding,
                approval=approval,
                artifact=artifact,
                ha=ha,
                package=None if package is None else package.envelope,
                repair=None
                if observed.get("mode") != "ha"
                else dict(
                    ha,
                    compute_id=package.target_identity,
                    operation_id=observed["lock"]["operation_id"],
                ),
                peer=peer,
                staged_package=None
                if package is None
                else {
                    "wheel": base64.b64encode(package.wheel.read_bytes()).decode(),
                    "dependency_wheels": {
                        path.name: base64.b64encode(path.read_bytes()).decode()
                        for path in package.dependency_paths
                    },
                },
            ),
        )
        if _receive(stdout) != {"ready": True}:
            raise RuntimeError("Ordinary-to-HA admission failed before migration effects")
        holder = Handoff(
            stdin, stdout, ha=ha, package=package, peer_observer=peer_observer, peer=peer
        )
        try:
            yield holder
        finally:
            if not holder.finished:
                with contextlib.suppress(Exception):
                    stdin.channel.shutdown_write()
