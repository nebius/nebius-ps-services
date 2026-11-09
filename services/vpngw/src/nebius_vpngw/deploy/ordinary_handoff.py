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
import threading
import time
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

from paramiko import (  # type: ignore[import-untyped]
    AuthenticationException,
    BadHostKeyException,
    SSHException,
)

from .. import ingress_policy
from ..handoff_bootstrap import streamed_source
from ..ordinary_operations import digest
from . import ingress_ssh
from .ssh_policy import configure_paramiko_host_verification
from .vm_ha_package import VMHAAgentPackageError

# Source is embedded so a 0.6.0 ordinary guest can participate before installation.
_REMOTE = "import _vpngw_handoff_remote as handoff; handoff.main()"
DETACH: ContextVar[threading.Event | None] = ContextVar("ha_package_observer_detach", default=None)
REPAIR: ContextVar[bool] = ContextVar("ha_package_admitted_repair", default=False)


class PackageUnresolved(RuntimeError):
    """A settled guest needs owner diagnosis; transport failures are not retries."""

    def __init__(self, identity: str):
        super().__init__("HA package owner settled before verified completion")
        self.identity = identity


class PackageDetached(KeyboardInterrupt):
    """Only the observer is cancelled; the admitted guest phase keeps running."""


class PackageJob:
    def __init__(self, ssh: Any, target: str, instance: Any, local: dict):
        self.ssh, self.target, self.instance, self.local = ssh, target, instance, local
        self.identity: str | None = None
        self.request: dict | None = None
        self.submitted: dict[str, dict] = {}
        self.started = time.monotonic()
        self.reported = self.started

    def control(self, request: dict) -> dict:
        detached = DETACH.get()
        if detached is not None and detached.is_set():
            raise PackageDetached()
        from .apply_diagnostics import ObservationUnavailable
        from .ssh_commands import budget

        phase = "package-connection"
        try:
            with (
                budget(time.monotonic() + 30),
                _connection(self.ssh, self.target, self.instance, self.local) as client,
            ):
                phase = "package-request"
                _stdin, stdout = _start(client, request)
                phase = "package-response"
                result = _receive(stdout)
                phase = "package-completion"
                return result
        except TimeoutError as error:
            raise ObservationUnavailable(phase, "observation-deadline-expired") from error
        except (PermissionError, AuthenticationException, BadHostKeyException) as error:
            raise ObservationUnavailable(
                "package-observation", "ssh-access-or-identity-blocked", retryable=False
            ) from error
        except (OSError, EOFError, SSHException) as error:
            raise ObservationUnavailable(
                "package-observation", "ssh-observation-unavailable"
            ) from error
        except ValueError as error:
            raise ObservationUnavailable(
                "package-observation", "guest-response-malformed", retryable=False
            ) from error

    def bind(self, request: dict) -> dict:
        import hashlib

        from ..package_worker import identity
        from . import package_reservation

        source = (
            streamed_source()
            + "from nebius_vpngw import package_worker; package_worker.main(sys.argv[1], int(sys.argv[2]), sys.argv[3])\n"
        )
        saved = package_reservation.admit(request, hashlib.sha256(source.encode()).hexdigest())
        if saved is not None:
            # The original phase key is used by the guest on reattachment.
            request = dict(
                saved["request"],
                **(
                    {"staged_package": request["staged_package"]}
                    if "staged_package" in request
                    else {}
                ),
            )
        self.request = request
        self.identity = identity(request)
        self.reservation = saved
        return {"action": "worker-start", "request": request, "source": source}

    def start(self, request: dict) -> dict:
        admission = dict(self.bind(request), retry=REPAIR.get())
        try:
            result = self.control(admission)
        except (AuthenticationException, BadHostKeyException):
            raise
        except (OSError, EOFError, SSHException):
            # Readback resolves an uncertain ACK; it never grants overlapping work.
            result = {"state": "RUNNING_OWNED", "identity": self.identity, "result": None}
        result.pop("phase", None)
        return self.wait(None, result, admission=admission)

    def submit(self, request: dict) -> dict:
        from ..package_worker import digest as phase_digest

        if request.get("action") == "verify-prepared":
            request = dict(request, observation=os.urandom(16).hex())
        self.submitted[request["action"]] = request
        try:
            result = self.control(
                {"action": "worker-submit", "identity": self.identity, "payload": request}
            )
        except (AuthenticationException, BadHostKeyException):
            raise
        except (OSError, EOFError, SSHException):
            result = {"state": "RUNNING_OWNED", "identity": self.identity, "result": None}
        return self.wait(
            phase_digest(request),
            result,
            admission={"action": "worker-submit", "identity": self.identity, "payload": request},
        )

    def wait(self, phase: str | None, result: dict, *, admission: dict | None = None) -> dict:
        from .apply_diagnostics import ObservationUnavailable

        while True:
            if (
                set(result) != {"state", "identity", "result"}
                or result["identity"] != self.identity
            ):
                raise ObservationUnavailable(
                    "package-observation", "package-identity-conflict", retryable=False
                )
            if result["state"] == "PREPARED" and isinstance(result["result"], dict):
                return result["result"]
            if result["state"] == "READY_TO_CONTINUE":
                raise PackageUnresolved(str(self.identity))
            if result["state"] == "NOT_SUBMITTED" and admission is not None:
                try:
                    result = self.control(admission)
                    result.pop("phase", None)
                except (AuthenticationException, BadHostKeyException):
                    raise
                except (OSError, EOFError, SSHException):
                    result = {"state": "RUNNING_OWNED", "identity": self.identity, "result": None}
                continue
            if result["state"] not in {"RUNNING_OWNED", "WAIT_EXTERNAL_LOCK"}:
                raise ObservationUnavailable(
                    "package-observation", "package-authority-blocked", retryable=False
                )
            detached = DETACH.get()
            if detached is not None:
                if detached.wait(0.5):
                    raise PackageDetached()
            else:
                time.sleep(0.5)
            if time.monotonic() - self.reported >= 30:
                elapsed = int(time.monotonic() - self.started)
                print(
                    f"HA package operation continues ({elapsed}s); observing the same owned phase"
                )
                self.reported = time.monotonic()
            try:
                result = self.control(
                    {"action": "worker-status", "identity": self.identity, "phase": phase}
                )
            except (AuthenticationException, BadHostKeyException):
                raise
            except (OSError, EOFError, SSHException):
                # No new submission is permitted after an observation failure.
                continue


@contextlib.contextmanager
def _connection(ssh: Any, target: str, instance: Any, local: dict) -> Iterator[Any]:
    from .ssh_commands import DEADLINE, connection_deadline

    paramiko = ssh._ensure_paramiko()
    client = paramiko.SSHClient()
    try:
        deadline = DEADLINE.get()
        guard = (
            connection_deadline(client, deadline)
            if deadline is not None
            else contextlib.nullcontext()
        )
        with guard:
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
            ingress_ssh.preflight(client, instance.config_yaml)
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("package connection observation deadline expired")
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
        timeout=30,
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
    raw = stdout.readline(2 * 1024 * 1024 + 1)
    if not raw:
        raise EOFError("HA package observation connection closed")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Ordinary-to-HA admission response invalid")
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
            "route_runtime",
        } or value["mode"] not in {"ordinary", "ha"}:
            raise RuntimeError("Ordinary-to-HA admission observation invalid")
        return value


def inspect_reservation(
    ssh: Any, target: str, instance: Any, local: dict, reservation: dict
) -> dict:
    return PackageJob(ssh, target, instance, local).control(
        {"action": "worker-reservation", "reservation": reservation}
    )


def inspect_repair(
    ssh: Any,
    target: str,
    instance: Any,
    local: dict,
    *,
    expected: dict,
    activation: bool = False,
) -> dict:
    with _connection(ssh, target, instance, local) as client:
        _stdin, stdout = _start(
            client,
            {
                "action": "activation-evidence" if activation else "repair-evidence",
                "repair": expected,
            },
        )
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
        job: PackageJob,
        *,
        ha: dict,
        package: Any = None,
        peer_observer: Any = None,
        peer: dict | None = None,
        activation_peer_observer: Any = None,
    ):
        self.job, self.ha = job, ha
        self.finished = False
        self.package, self.peer_observer, self.peer = package, peer_observer, peer
        self.package_receipt: dict | None = None
        self.fresh_verifier: Any = None
        self.activated_config_sha256: str | None = None
        self.repair_verifier: Any = None
        self.activation_peer_observer = activation_peer_observer

    def exchange(self, request: dict) -> dict:
        return self.job.submit(request)

    def _peer_request(self) -> dict:
        if self.peer_observer is None:
            return {}
        current = self.peer_observer()
        from ..ha_repair import peer_checkpoint_transition

        artifact = self.package.manifest["wheel_sha256"] if self.package is not None else ""
        if not peer_checkpoint_transition(self.peer, current, artifact):
            raise RuntimeError("HA repair peer admission changed after approval")
        return {"peer": current, "expected_peer": self.peer}

    def prepare_package(self, plan: Any) -> dict:
        if REPAIR.get():
            if self.job.request is None:
                raise RuntimeError("HA package repair lost its reservation")
            self.job.start(self.job.request)
        if self.package is None or plan.digest != self.package.digest:
            raise VMHAAgentPackageError("HA handoff package plan changed")
        wheel = plan.wheel.read_bytes()
        from .ordinary_apply import sha

        if sha(wheel) != plan.manifest["wheel_sha256"]:
            raise VMHAAgentPackageError("HA package changed after approval")
        for path in plan.dependency_paths:
            if sha(path.read_bytes()) != plan.manifest["dependency_wheels"][path.name]:
                raise VMHAAgentPackageError("HA dependency changed after approval")
        # Reservation already staged the bound wheels. Probe the peer only after
        # upload, immediately before this small named package-write request.
        receipt = self.exchange(
            dict(action="prepare-package", plan_digest=plan.digest, **self._peer_request()),
        )
        if receipt.get("artifact_sha256") != plan.manifest["wheel_sha256"]:
            raise VMHAAgentPackageError("HA handoff package receipt is unverified")
        self.package_receipt = receipt
        return receipt

    def verify_prepared(self) -> dict:
        receipt = self.exchange(dict(action="verify-prepared", **self._peer_request()))
        if receipt != self.package_receipt:
            raise RuntimeError("HA prepared package identity changed")
        return receipt

    def checkpoint_repair(self) -> None:
        self.verify_prepared()
        self._peer_request()
        if self.repair_verifier is None:
            raise RuntimeError("HA package repair has no fresh checkpoint verifier")
        self.repair_verifier()
        # Keep the holder, cold guard and stopped writers until desired config is
        # published. The new agent must never run against the predecessor YAML.

    def publish_activation(self, *, receipt: Any, operation_id: str) -> None:
        from dataclasses import asdict

        result = self.exchange(
            dict(
                action="publish-activation",
                receipt=asdict(receipt),
                operation_id=operation_id,
                **(
                    {"activation_peer": self.activation_peer_observer()}
                    if self.activation_peer_observer is not None
                    else {}
                ),
            ),
        )
        self.activated_config_sha256 = receipt.staged_file_sha256
        if result != {"published": True}:
            raise RuntimeError("HA handoff activation publication is unverified")

    def complete(self, payload: dict, *, operation_id: str) -> None:
        if (
            any(payload.get(k) != v for k, v in self.ha.items())
            or payload.get("data_plane_mode") != "passive"
            or payload.get("apply_locked") is not True
            or payload.get("apply_operation_id") != operation_id
        ):
            raise RuntimeError("Ordinary-to-HA takeover status is not exact and locked-passive")
        if self.activated_config_sha256 is None or self.fresh_verifier is None:
            raise RuntimeError("HA handoff has no activated configuration or fresh SSH verifier")
        self.fresh_verifier()
        if self.exchange({"action": "complete", "operation": operation_id}) != {"complete": True}:
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
    activation_peer_observer: Any = None,
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
        job = PackageJob(ssh, target, instance, local)
        from .package_recovery import reserve as reserve_owned

        ready = reserve_owned(
            job,
            dict(
                action="reserve",
                ingress_policy=ingress_policy.from_config(config),
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
        if ready != {"ready": True}:
            raise RuntimeError("Ordinary-to-HA admission failed before migration effects")
        holder = Handoff(
            job,
            ha=ha,
            package=package,
            peer_observer=peer_observer,
            peer=peer,
            activation_peer_observer=activation_peer_observer,
        )
        receipts = job.control({"action": "worker-receipts", "identity": job.identity})
        if "state" in receipts:
            raise RuntimeError("HA package receipt authority is unavailable")
        if "prepare-package" in receipts:
            holder.package_receipt = receipts["prepare-package"]["result"]
            job.submitted["prepare-package"] = receipts["prepare-package"]["request"]
        if "publish-activation" in receipts:
            holder.activated_config_sha256 = receipts["publish-activation"]["request"]["receipt"][
                "staged_file_sha256"
            ]
        boot_id = ingress_ssh.preflight(client, instance.config_yaml)
        holder.repair_verifier = lambda: ingress_ssh.verify_repair_checkpoint(
            ssh,
            target,
            instance,
            local,
            boot_id=boot_id,
            config_sha256=observed["config_sha256"],
        )
        holder.fresh_verifier = lambda: ingress_ssh.verify_fresh(
            ssh,
            target,
            instance,
            local,
            boot_id=boot_id,
            config_sha256=holder.activated_config_sha256,
        )
        try:
            yield holder
        finally:
            # Closing the observer does not cancel the admitted phase or admit
            # another one. The exact Holder and startup inhibition remain owned.
            pass


def prepare_guarded(ssh: Any, plan: Any, local: dict, expected: dict | None) -> dict:
    job = PackageJob(ssh, plan.target, plan.instance, local)
    receipt = job.start(
        {"action": "prepare-guarded", "package": plan.envelope, "expected": expected}
    )
    if (
        receipt.get("schema") != "nebius-vpngw/vm-ha-package-v1"
        or receipt.get("artifact_sha256") != plan.manifest["wheel_sha256"]
    ):
        raise VMHAAgentPackageError("HA guarded package confirmation differs")
    return receipt


def observe_guarded(ssh: Any, plan: Any, local: dict, expected: dict | None) -> dict:
    return PackageJob(ssh, plan.target, plan.instance, local).control(
        {
            "action": "worker-find",
            "request": {
                "action": "prepare-guarded",
                "package": plan.envelope,
                "expected": expected,
            },
        }
    )


def corrective_action(ssh: Any, target: str, instance: Any, local: dict, request: dict) -> dict:
    if request["action"] not in {"corrective-inspect", "corrective-reserve"}:
        raise ValueError("unsupported corrective handoff action")
    with _connection(ssh, target, instance, local) as client:
        _stdin, stdout = _start(client, request)
        return _receive(stdout)
