"""Automatic, strictly pinned SSH collection through the existing NAT gateway."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import selectors
import shlex
import subprocess
import tempfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from .lease_clock import elapsed
from .nat_gateway_probe_result import RESULT_LIMIT, accepted_report
from .nat_gateway_ssh import ssh_identity_options
from .owned_process import ExecutionScope
from .ssh_trust import ssh_trust_options

SSH_TIMEOUT = 30.0
RESULT_TIMEOUT = 300.0
PROBE_USER = "cxcli-probe"
RESULT_PATH = "/var/lib/cxcli-nat-probe/result.json"


class ProcessUnresolved(RuntimeError):
    """The supervisor could not confirm that every SSH child stopped."""


def capture(
    command: list[str], *, cwd: Path, timeout: float, assert_held: Any
) -> tuple[int, bytes]:
    """Bound output, time and all descendants, including after parent death."""
    assert_held()
    scope = ExecutionScope(uuid.uuid4().hex, elapsed() + timeout)
    process = None
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        process = scope.launch(
            command,
            {
                "cwd": cwd,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.DEVNULL,
            },
        )
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                assert_held()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("NAT probe SSH attempt timed out")
                for key, _ in selector.select(min(0.1, remaining)):
                    chunk = os.read(key.fd, RESULT_LIMIT + 1 - len(output))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > RESULT_LIMIT:
                            raise RuntimeError("NAT probe SSH output exceeds its bound")
        assert process.returncode is not None
        return process.returncode, bytes(output)
    except TimeoutError:
        raise
    except OSError:
        raise RuntimeError("NAT probe SSH transport could not start or read its result") from None
    finally:
        clean = scope.close(elapsed() + 11)
        if process is not None and process.stdout is not None:
            process.stdout.close()
        if not clean:
            raise ProcessUnresolved("NAT probe SSH shutdown is unresolved; cleanup is pending")


def _quoted(value: str) -> str:
    if any(character in value for character in "\r\n\0"):
        raise ValueError("Invalid SSH configuration value")
    return '"' + value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'


class ProbeSSH:
    def __init__(
        self,
        outputs: dict[str, Any],
        inputs: dict[str, Any],
        *,
        known_hosts: Path,
        private_key: Path | None = None,
    ) -> None:
        self.gateway = str(ipaddress.IPv4Interface(outputs["public_ip"]).ip)
        self.user = inputs["ssh_user_name"]
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", self.user):
            raise ValueError("Invalid NAT SSH user")
        self.public_key = inputs["ssh_public_key"]
        self.private_key, self.known_hosts = private_key, known_hosts
        self.stack = ExitStack()

    def __enter__(self) -> ProbeSSH:
        try:
            # Validate both trust and the exact client identity before route changes.
            ssh_trust_options(self.known_hosts)
            self.gateway_trust = str(self.known_hosts.expanduser().resolve(strict=True))
            identity = self.stack.enter_context(
                ssh_identity_options(self.public_key, self.private_key)
            )
            self.root = Path(
                self.stack.enter_context(tempfile.TemporaryDirectory(prefix="cxcli-nat-probe-ssh-"))
            )
            lines = [
                "Host *",
                "  StrictHostKeyChecking yes",
                "  GlobalKnownHostsFile /dev/null",
                "  ForwardAgent no",
                "  RequestTTY no",
                "  ConnectTimeout 15",
                "  ConnectionAttempts 1",
                "  ControlMaster no",
                "  ControlPath none",
                "  LogLevel ERROR",
            ]
            for flag, value in zip(identity[::2], identity[1::2], strict=True):
                if flag == "-i":
                    lines.append("  IdentityFile " + _quoted(value))
                elif flag == "-o":
                    name, setting = value.split("=", 1)
                    lines.append(f"  {name} {setting}")
            lines.extend(
                [
                    "Host nat-gateway",
                    f"  HostName {self.gateway}",
                    f"  User {self.user}",
                    f"  HostKeyAlias {self.gateway}",
                    "  UserKnownHostsFile " + _quoted(self.gateway_trust),
                ]
            )
            self.base_config = "\n".join(lines) + "\n"
            self.config = self.root / "config"
            self._write(self.config, self.base_config)
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args: Any) -> None:
        self.stack.close()

    @staticmethod
    def _write(path: Path, content: str) -> None:
        with open(path, "w", opener=lambda p, flags: os.open(p, flags, 0o600)) as output:
            output.write(content)

    def preflight(self, *, assert_held: Any) -> None:
        # Evaluate sshd Match rules using the real connection, without changing the host.
        script = (
            "import json,os,subprocess; c=os.environ['SSH_CONNECTION'].split(); "
            "context='user='+os.environ['USER']+',addr='+c[0]+',host='+c[0]+',laddr='+c[2]+',lport='+c[3]; "
            "r=subprocess.run(['sudo','-n','/usr/sbin/sshd','-T','-C',context],"
            "capture_output=True,text=True,timeout=10,check=True); "
            "wanted={'allowtcpforwarding','disableforwarding','permitopen'}; "
            "print(json.dumps({p[0]:p[1] for line in r.stdout.splitlines() "
            "if len(p:=line.split(None,1))==2 and p[0] in wanted}))"
        )
        code, raw = capture(
            ["ssh", "-F", str(self.config), "nat-gateway", "python3 -c " + shlex.quote(script)],
            cwd=self.root,
            timeout=SSH_TIMEOUT,
            assert_held=assert_held,
        )
        try:
            policy = json.loads(raw)
            allowed = (
                not code
                and policy.get("allowtcpforwarding") in {"yes", "all", "local"}
                and policy.get("disableforwarding") == "no"
                and policy.get("permitopen") == "any"
            )
        except (ValueError, TypeError, AttributeError):
            allowed = False
        if not allowed:
            raise RuntimeError(
                "NAT automatic testing requires trusted gateway SSH with local TCP forwarding; "
                "effective forwarding could not be confirmed before route activation"
            )

    def wait(self, resources: Any, probe: dict[str, Any], *, save: Any, assert_held: Any) -> dict:
        deadline = time.monotonic() + RESULT_TIMEOUT
        address = resources.private_address(deadline=deadline)
        alias = "cxcli-probe-" + probe["config"]["run_id"]
        pin = self.root / "probe_known_hosts"
        self._write(pin, alias + " " + probe["host_public_key"] + "\n")
        self._write(
            self.config,
            self.base_config
            + "\n".join(
                [
                    "Host nat-probe",
                    f"  HostName {address}",
                    f"  User {PROBE_USER}",
                    f"  HostKeyAlias {alias}",
                    "  HostKeyAlgorithms ssh-ed25519",
                    "  UserKnownHostsFile " + _quoted(str(pin)),
                    "  ProxyJump nat-gateway",
                    "",
                ]
            ),
        )
        while time.monotonic() < deadline:
            assert_held()
            try:
                code, raw = capture(
                    ["ssh", "-F", str(self.config), "nat-probe", "cat " + RESULT_PATH],
                    cwd=self.root,
                    timeout=min(SSH_TIMEOUT, max(0, deadline - time.monotonic())),
                    assert_held=assert_held,
                )
            except ProcessUnresolved:
                probe["ssh_cleanup_pending"] = True
                save()
                raise
            except TimeoutError:
                code, raw = 255, b""
            if code == 0:
                report = accepted_report(raw, probe)
                assert_held()
                if resources.private_address(deadline=deadline) != address:
                    raise RuntimeError("NAT probe private address changed during verification")
                return report
            if code not in {1, 255}:
                raise RuntimeError("NAT probe SSH result command failed")
            time.sleep(min(3, max(0, deadline - time.monotonic())))
        raise RuntimeError("NAT probe is inconclusive: no trusted SSH result before deadline")
