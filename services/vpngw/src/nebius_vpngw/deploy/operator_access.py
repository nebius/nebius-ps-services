"""Automated enrollment over existing pinned SSH operator access."""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import operator_guest
from .operator_keys import OperatorKey, safe_name
from .ssh_client_auth import SSHClientAuth
from .ssh_policy import SSHTrustPolicy, configure_paramiko_host_verification


@dataclass(frozen=True)
class OperatorTarget:
    hostname: str
    address: str
    compute_id: str
    username: str
    assert_current: Callable[[], None]


@dataclass
class EnrollmentStatus:
    hostname: str
    installed: bool = False
    new_login_verified: bool = False
    actor_login_verified: bool = False
    error: str | None = None

    def complete(self, *, generated: bool) -> bool:
        return (
            self.installed
            and self.actor_login_verified
            and (self.new_login_verified or not generated)
        )


class OperatorEnrollment:
    def __init__(
        self,
        targets: tuple[OperatorTarget, ...],
        actor: SSHClientAuth,
        policy: SSHTrustPolicy | None,
        *,
        paramiko_module: Any = None,
    ) -> None:
        if not targets or len({target.hostname for target in targets}) != len(targets):
            raise ValueError("Operator enrollment requires every distinct gateway member")
        if paramiko_module is None:
            import paramiko  # type: ignore[import-untyped]

            paramiko_module = paramiko
        self.targets, self.actor, self.policy, self.paramiko = (
            targets,
            actor,
            policy,
            paramiko_module,
        )

    def connect(self, target: OperatorTarget, *, new_key: OperatorKey | None = None) -> Any:
        target.assert_current()
        client = self.paramiko.SSHClient()
        try:
            configure_paramiko_host_verification(
                client,
                self.paramiko,
                policy=self.policy,
                hostname=target.hostname,
                transport_host=target.address,
            )
            authentication = (
                dict(self.actor.paramiko_connect_kwargs())
                if new_key is None
                else {
                    "pkey": new_key.pkey,
                    "allow_agent": False,
                    "look_for_keys": False,
                    "password": None,
                }
            )
            client.connect(
                hostname=target.address,
                username=target.username,
                timeout=15,
                auth_timeout=15,
                banner_timeout=15,
                **authentication,
            )
            return client
        except Exception:
            client.close()
            raise

    @staticmethod
    def _read_json(
        client: Any, command: str, request: dict | None = None, *, timeout: int = 30
    ) -> dict:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        if request is not None:
            stdin.write(json.dumps(request))
            stdin.flush()
            stdin.channel.shutdown_write()
        raw = stdout.read(2 * operator_guest.LIMIT + 1)
        error = stderr.read(4096)
        if stdout.channel.recv_exit_status() or len(raw) > 2 * operator_guest.LIMIT:
            # Remote output can contain customer key options. Keep it out of CLI logs.
            del error
            raise RuntimeError(
                "Guest access inspection or publication failed; retain the same key and retry"
            )
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise RuntimeError("Guest access response is invalid")
        return result

    def inspect(self, target: OperatorTarget, *, operation: str | None = None) -> dict:
        client = self.connect(target)
        try:
            context = self._read_json(
                client,
                "/usr/bin/python3 -c "
                + shlex.quote(
                    "import json,os;from pathlib import Path;print(json.dumps({'connection':os.environ.get('SSH_CONNECTION',''),'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))"
                ),
            )
            if not isinstance(context.get("boot"), str) or not context["boot"]:
                raise RuntimeError("Cannot establish the authenticated gateway boot")
            result = self.request(
                client,
                target,
                "inspect-replacement" if operation else "inspect",
                context["connection"],
                **({"operation": operation} if operation else {}),
            )
            projection = result.get("projection", {})
            if projection.get("compute_id") != target.compute_id or result.get(
                "digest"
            ) != operator_guest.digest(projection):
                raise RuntimeError("Guest authorization projection does not match the target")
            result["context"] = context
            return result
        finally:
            client.close()

    def request(
        self, client: Any, target: OperatorTarget, action: str, connection: str, **values: Any
    ) -> dict:
        source = Path(operator_guest.__file__).with_suffix(".py").read_text(encoding="utf-8")
        return self._read_json(
            client,
            "sudo -n /usr/bin/python3 -c " + shlex.quote(source),
            {
                "action": action,
                "username": target.username,
                "compute_id": target.compute_id,
                "connection": connection,
                **values,
            },
        )

    def run(
        self,
        name: str,
        key_factory: Callable[[], OperatorKey],
        *,
        on_key: Callable[[OperatorKey], None] = lambda _key: None,
    ) -> tuple[OperatorKey, tuple[EnrollmentStatus, ...]]:
        safe_name(name)
        # Every target authenticates and resolves actual sshd policy before creating
        # a credential or granting access anywhere. Never start a stopped HA node.
        snapshots = {target.hostname: self.inspect(target) for target in self.targets}
        rosters = []
        for snapshot in snapshots.values():
            roster = dict(snapshot["projection"]["operators"])
            roster.pop(name, None)  # Permit an interrupted grant of this same operator.
            rosters.append(roster)
        if any(roster != rosters[0] for roster in rosters):
            raise RuntimeError("Managed operator rosters differ across gateway members")
        key = key_factory()
        on_key(key)
        for snapshot in snapshots.values():
            existing = snapshot["projection"]["operators"].get(name)
            if existing is not None and existing != {
                "public_key": key.public_key,
                "fingerprint": key.fingerprint,
            }:
                raise RuntimeError(
                    "Operator name already belongs to another key; generated files were retained"
                )
            pending = snapshot.get("pending")
            if pending is not None and pending != {"name": name, "fingerprint": key.fingerprint}:
                raise RuntimeError("Another enrollment is pending; generated files were retained")
        statuses = []
        for target in self.targets:
            status = EnrollmentStatus(target.hostname)
            statuses.append(status)
            snapshot = snapshots[target.hostname]
            client = None
            try:
                client = self.connect(target)
                result = self.request(
                    client,
                    target,
                    "enroll",
                    snapshot["context"]["connection"],
                    name=name,
                    public_key=key.public_key,
                    expected_digest=snapshot["digest"],
                )
                status.installed = result.get("projection", {}).get("operators", {}).get(name) == {
                    "public_key": key.public_key,
                    "fingerprint": key.fingerprint,
                }
                if not status.installed:
                    raise RuntimeError("Public key installation did not verify")
                client.close()
                client = None
                if key.pkey is not None:
                    verification = self.connect(target, new_key=key)
                    try:
                        boot = self._read_json(
                            verification,
                            "/usr/bin/python3 -c "
                            + shlex.quote(
                                "import json;from pathlib import Path;print(json.dumps({'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))"
                            ),
                        )
                        if boot != {"boot": snapshot["context"]["boot"]}:
                            raise RuntimeError("Gateway rebooted during new-key verification")
                        status.new_login_verified = True
                    finally:
                        verification.close()
                verified = self.inspect(target)
                if verified["context"]["boot"] != snapshot["context"]["boot"] or verified[
                    "digest"
                ] != result.get("digest"):
                    raise RuntimeError(
                        "Gateway authorization changed during fresh operator verification"
                    )
                status.actor_login_verified = True
            except Exception as error:
                status.error = type(error).__name__
            finally:
                if client is not None:
                    client.close()
        return key, tuple(statuses)
