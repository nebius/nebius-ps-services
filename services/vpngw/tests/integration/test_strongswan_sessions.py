"""Real two-ended IKE lifecycle checks inside the disposable Linux fixture."""

from __future__ import annotations

import concurrent.futures
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

from nebius_vpngw.agent.strongswan_renderer import SWANCTL_CONF, StrongSwanRenderer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires the explicit isolated systemd fixture",
    ),
]


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=20)


@dataclass
class Endpoint:
    namespace: str
    process: subprocess.Popen
    config: Path
    address: str

    def command(self, *args: str) -> subprocess.CompletedProcess[str]:
        assert self.process.poll() is None, "fixture charon exited"
        return run("nsenter", "-t", str(self.process.pid), "-m", "-n", "--", *args)

    def sas(self) -> tuple[tuple[int, ...], int]:
        output = self.command("swanctl", "--list-sas").stdout
        ike_ids = tuple(
            sorted(
                int(value) for value in re.findall(r"^fixture: #(\d+), ESTABLISHED,", output, re.M)
            )
        )
        children = len(re.findall(r"^\s+fixture: #\d+, reqid \d+, INSTALLED,", output, re.M))
        return ike_ids, children


@pytest.fixture(
    params=[(2, False), (2, True), (1, False), (1, True)],
    ids=["ikev2", "ikev2-prior-peer", "ikev1", "ikev1-prior-peer"],
)
def endpoints(tmp_path, request):
    assert sys.platform == "linux" and os.geteuid() == 0
    assert Path("/.dockerenv").is_file()
    assert Path("/proc/1/comm").read_text().strip() == "systemd"
    for command in ("ip", "tc", "unshare", "nsenter", "mount", "swanctl"):
        assert shutil.which(command), f"required fixture command unavailable: {command}"
    charon = "/usr/lib/ipsec/charon"
    assert Path(charon).is_file()
    suffix = uuid.uuid4().hex[:7]
    namespaces = [f"ike-{suffix}-{index}" for index in (1, 2)]
    processes: list[subprocess.Popen] = []
    logs = []
    result = []
    psk = secrets.token_hex(32)
    ike_version, prior_peer = request.param
    try:
        for namespace in namespaces:
            run("ip", "netns", "add", namespace)
        left, right = "l" + suffix, "r" + suffix
        run("ip", "link", "add", left, "type", "veth", "peer", "name", right)
        for index, (namespace, link) in enumerate(zip(namespaces, (left, right), strict=True), 1):
            run("ip", "link", "set", link, "netns", namespace)
            prefix = ("ip", "netns", "exec", namespace)
            run(*prefix, "ip", "link", "set", link, "name", "eth0")
            run(*prefix, "ip", "link", "set", "lo", "up")
            run(*prefix, "ip", "address", "add", f"192.0.2.{index}/30", "dev", "eth0")
            run(*prefix, "ip", "link", "set", "eth0", "up")
            run(*prefix, "ip", "address", "add", f"10.200.{index}.1/32", "dev", "lo")
            run(
                *prefix, "ip", "link", "add", "xfrm0", "type", "xfrm", "dev", "eth0", "if_id", "100"
            )
            run(*prefix, "ip", "link", "set", "xfrm0", "up")
            run(
                *prefix,
                "ip",
                "route",
                "add",
                f"10.200.{3 - index}.1/32",
                "dev",
                "xfrm0",
                "src",
                f"10.200.{index}.1",
            )
            # Both native start actions must begin before either handshake can
            # finish. This delay belongs only to the isolated collision fixture.
            run(*prefix, "tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", "500ms")
            config = {
                "gateway": {
                    "ssh_allowed_cidrs": ["203.0.113.4/32"],
                    "local_prefixes": [f"10.200.{index}.1/32"],
                },
                "defaults": {
                    "allow_ikev1": ike_version == 1,
                    "crypto": {
                        "ike_proposals": ["aes256-sha256-modp2048"],
                        "esp_proposals": ["aes256-sha256-modp2048"],
                    },
                },
                "connections": [
                    {
                        "routing_mode": "static",
                        "tunnels": [
                            {
                                "name": "fixture",
                                "ike_version": ike_version,
                                "local_public_ip": f"192.0.2.{index}",
                                "remote_public_ip": f"192.0.2.{3 - index}",
                                "psk": psk,
                            }
                        ],
                    }
                ],
            }
            rendered = {}
            StrongSwanRenderer().render_and_apply(config, rendered_files=rendered)
            if prior_peer and index == 2:
                # The verified 0.6.0 renderer used these exact lifecycle
                # settings. This is session-policy interoperability coverage,
                # not a claim that the full historical release was upgraded.
                rendered[SWANCTL_CONF] = (
                    rendered[SWANCTL_CONF]
                    .replace("unique = no", "unique = replace")
                    .replace("close_action = trap", "close_action = restart")
                )
            swanctl = tmp_path / f"swanctl-{index}.conf"
            swanctl.write_text(rendered[SWANCTL_CONF])
            swanctl.chmod(0o600)
            settings = tmp_path / f"strongswan-{index}.conf"
            settings.write_text("""charon {
  load_modular = yes
  install_routes = no
  reuse_ikesa = no
  plugins {
    include /etc/strongswan.d/charon/*.conf
    ha {
      load = no
    }
  }
}
""")
            log = (tmp_path / f"charon-{index}.log").open("w")
            logs.append(log)
            # Each owned daemon receives a private /run, including its PID file
            # and VICI socket. No host namespace or credential is mounted.
            bootstrap = (
                "import os,subprocess; "
                "subprocess.run(['mount','-t','tmpfs','tmpfs','/run'],check=True); "
                f"os.execv({charon!r},[{charon!r}])"
            )
            process = subprocess.Popen(
                [
                    *prefix,
                    "unshare",
                    "--mount",
                    "--propagation",
                    "private",
                    sys.executable,
                    "-c",
                    bootstrap,
                ],
                env={**os.environ, "STRONGSWAN_CONF": str(settings)},
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            processes.append(process)
            endpoint = Endpoint(namespace, process, swanctl, f"10.200.{index}.1")
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None, "fixture charon failed to start"
                ready = run(
                    "nsenter",
                    "-t",
                    str(process.pid),
                    "-m",
                    "-n",
                    "--",
                    "swanctl",
                    "--stats",
                    check=False,
                )
                if ready.returncode == 0:
                    break
                assert time.monotonic() < deadline, "fixture VICI socket unavailable"
                time.sleep(0.1)
            result.append(endpoint)
        yield result, ike_version
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        for log in sorted(tmp_path.glob("charon-*.log")):
            # Only synthetic fixture identities exist in this namespace. Keep
            # the random PSK out of failure diagnostics even if daemon logging
            # changes in a later package version.
            lines = log.read_text().replace(psk, "[redacted]").splitlines()
            relevant = [
                line
                for line in lines
                if re.search(
                    r"failed|unable|not supported|not found|kernel|proposal|loaded plugins",
                    line,
                    re.I,
                )
            ]
            print(log.name, "\n".join(relevant[-40:]))
        for namespace in namespaces:
            run("ip", "netns", "delete", namespace, check=False)


def establish(endpoints: list[Endpoint], ike_version: int) -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda e: e.command("swanctl", "--load-all", "--file", str(e.config)), endpoints
            )
        )
    assert all(result.returncode == 0 for result in results)
    deadline = time.monotonic() + 20
    while not all(endpoint.sas()[1] for endpoint in endpoints):
        assert time.monotonic() < deadline, (
            "simultaneous startup did not establish CHILD SAs: "
            + repr([endpoint.command("swanctl", "--list-sas").stdout for endpoint in endpoints])
        )
        time.sleep(0.2)
    for endpoint in endpoints:
        endpoint.command("tc", "qdisc", "del", "dev", "eth0", "root")
    if ike_version == 1:
        # IKEv1 always reuses an existing IKE SA. Its separate coverage checks
        # simultaneous startup, encrypted traffic and peer-close recovery.
        return
    before = endpoints[1].sas()[0]
    # One fresh authenticated peer negotiation while an IKE SA already exists
    # reproduces replacement admission. reuse_ikesa=no is fixture-only and
    # makes this explicit VICI initiation independent of an existing IKE SA.
    endpoints[1].command("swanctl", "--initiate", "--child", "fixture", "--timeout", "10")
    assert set(endpoints[1].sas()[0]) - set(before), "duplicate negotiation was not injected"


def stable_sessions(endpoints: list[Endpoint], seconds: float = 22) -> None:
    deadline = time.monotonic() + 55
    stable_since = None
    previous = None
    history = []
    while time.monotonic() < deadline:
        current = tuple(endpoint.sas() for endpoint in endpoints)
        history.append(current)
        # Two startup initiations plus one explicit duplicate are the entire
        # input. Multiple authenticated SAs may coexist; autonomous replacement
        # after those inputs have ended is the regression under test.
        if all(1 <= len(ids) <= 3 and children == len(ids) for ids, children in current):
            if current != previous:
                stable_since = time.monotonic()
            if stable_since is not None and time.monotonic() - stable_since >= seconds:
                return
        else:
            stable_since = None
        previous = current
        time.sleep(0.5)
    pytest.fail(f"IKE/CHILD identities did not converge without replacement: {history[-12:]}")


def tcp179(server: Endpoint, client: Endpoint, *, recovering: bool = False) -> None:
    code = (
        "import socket; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
        "s.settimeout(10); s.bind(('0.0.0.0',179)); s.listen(1); "
        "print('ready',flush=True); c,_=s.accept(); c.settimeout(3); "
        "assert c.recv(4)==b'BGP!'; c.sendall(b'PASS'); c.close(); s.close()"
    )
    listener = subprocess.Popen(
        [
            "nsenter",
            "-t",
            str(server.process.pid),
            "-m",
            "-n",
            "--",
            sys.executable,
            "-u",
            "-c",
            code,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        import select

        assert select.select([listener.stdout], [], [], 5)[0], "TCP listener did not start"
        assert listener.stdout.readline().strip() == "ready"
        client.command(
            sys.executable,
            "-c",
            "import socket,sys,time\n"
            "deadline=time.monotonic()+7\n"
            "while True:\n"
            " try: s=socket.create_connection((sys.argv[1],179),1); break\n"
            " except OSError:\n"
            "  if sys.argv[2]!='recover' or time.monotonic()>=deadline: raise\n"
            "  time.sleep(.1)\n"
            "s.settimeout(3); s.sendall(b'BGP!'); assert s.recv(4)==b'PASS'; s.close()",
            server.address,
            "recover" if recovering else "steady",
        )
        assert listener.wait(timeout=8) == 0
    finally:
        if listener.poll() is None:
            listener.terminate()
            listener.wait(timeout=5)


def test_simultaneous_start_converges_and_peer_close_recovers(endpoints) -> None:
    endpoints, ike_version = endpoints
    establish(endpoints, ike_version)
    stable_sessions(endpoints)
    before = [endpoint.sas() for endpoint in endpoints]
    for server, client in (endpoints, reversed(endpoints)):
        tcp179(server, client)
    assert [endpoint.sas() for endpoint in endpoints] == before
    # A real peer DELETE must still recover when fresh encrypted traffic arrives.
    endpoints[0].command("swanctl", "--terminate", "--ike", "fixture", "--timeout", "5")
    assert not set(endpoints[0].sas()[0]) & set(before[0][0])
    # The close intentionally disrupts traffic. A fresh attempt may receive an
    # XFRM acquire error before negotiation completes; only this declared
    # recovery check permits bounded retries. Steady-state TCP never retries.
    tcp179(endpoints[0], endpoints[1], recovering=True)
    stable_sessions(endpoints, seconds=12)
    assert [endpoint.sas() for endpoint in endpoints] != before
    for endpoint in endpoints:
        policies = endpoint.command("ip", "xfrm", "policy").stdout
        assert "proto esp" in policies and "if_id 0x64" in policies
