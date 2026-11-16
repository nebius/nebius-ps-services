"""Real two-ended IKE lifecycle checks inside the disposable Linux fixture."""

from __future__ import annotations

import concurrent.futures
import json
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
from datetime import datetime
from pathlib import Path

import pytest

from nebius_vpngw.agent.strongswan_renderer import (
    STRONGSWAN_CONF_DIR,
    SWANCTL_CONF,
    StrongSwanRenderer,
)

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
    log: Path

    def command(self, *args: str) -> subprocess.CompletedProcess[str]:
        assert self.process.poll() is None, "fixture charon exited"
        result = run("nsenter", "-t", str(self.process.pid), "-m", "-n", "--", *args, check=False)
        assert result.returncode == 0, result.stderr[:4000]
        return result

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
    yield from _endpoints(tmp_path, *request.param, duplicate_negotiation=True)


@pytest.fixture(params=[2, 1], ids=["ikev2", "ikev1"])
def restart_endpoints(tmp_path, request):
    yield from _endpoints(tmp_path, request.param, False, duplicate_negotiation=False)


def _endpoints(tmp_path, ike_version, prior_peer, *, duplicate_negotiation):
    assert sys.platform == "linux" and os.geteuid() == 0
    assert Path("/.dockerenv").is_file()
    assert Path("/proc/1/comm").read_text().strip() == "systemd"
    for command in ("ip", "tc", "unshare", "nsenter", "mount", "swanctl"):
        assert shutil.which(command), f"required fixture command unavailable: {command}"
    charon = "/usr/sbin/charon-systemd"
    assert Path(charon).is_file()
    suffix = uuid.uuid4().hex[:7]
    namespaces = [f"ike-{suffix}-{index}" for index in (1, 2)]
    processes: list[subprocess.Popen] = []
    logs = []
    result = []
    psk = secrets.token_hex(32)
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
            # Hold initial packets until both peer configurations are loaded.
            # A delay alone permits a fast starter to receive a permanent
            # NO_PROPOSAL_CHOSEN from an unconfigured peer, which is a different
            # case from simultaneous configured-peer initiation.
            run(
                *prefix,
                "iptables",
                "-I",
                "OUTPUT",
                "-p",
                "udp",
                "-m",
                "multiport",
                "--dports",
                "500,4500",
                "-j",
                "DROP",
            )
            run(*prefix, "tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", "500ms")
            config = {
                "gateway": {
                    "local_prefixes": (
                        [f"10.200.{index}.1/32"]
                        if duplicate_negotiation
                        else [f"10.200.{index}.0/24", "169.254.210.0/30"]
                    ),
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
                        "name": "fixture",
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
            settings.write_text("""charon-systemd {
  filelog {
    stdout {
      default = 1
      flush_line = yes
      time_format = %Y-%m-%dT%H:%M:%S
      time_add_ms = yes
    }
  }
}
charon {
  load_modular = yes
  install_routes = no
  plugins {
    include /etc/strongswan.d/charon/*.conf
    ha {
      load = no
    }
  }
}
""")
            # Exercise the product policy, including its retransmission sequence,
            # rather than inheriting upstream defaults in the native fixture.
            with settings.open("a") as stream:
                stream.write(rendered[STRONGSWAN_CONF_DIR.parent / "nebius-vpngw.conf"])
                if duplicate_negotiation:
                    stream.write("\ncharon { reuse_ikesa = no }\n")
            log_path = tmp_path / f"charon-{index}.log"
            log = log_path.open("w")
            logs.append(log)
            # Each owned daemon receives a private /run, including its PID file
            # and VICI socket. No host namespace or credential is mounted.
            bootstrap = (
                "import os,subprocess; "
                "subprocess.run(['mount','-t','tmpfs','tmpfs','/run'],check=True); "
                f"subprocess.run(['mount','--bind',{str(swanctl)!r},"
                "'/etc/swanctl/swanctl.conf'],check=True); "
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
            endpoint = Endpoint(namespace, process, swanctl, f"10.200.{index}.1", log_path)
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None, "fixture charon failed to start"
                # Popen initially names ip/unshare/Python, before the private /run
                # mount exists. Its inherited socket may belong to the container's
                # system daemon. That reply cannot establish endpoint readiness.
                executable = Path(f"/proc/{process.pid}/exe").resolve()
                if executable != Path(charon).resolve():
                    assert time.monotonic() < deadline, "fixture namespace bootstrap stalled"
                    time.sleep(0.05)
                    continue
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
                    r"failed|unable|not supported|not found|kernel|proposal|loaded plugins|initiating|retransmit|giving up",
                    line,
                    re.I,
                )
            ]
            print(log.name, "\n".join(relevant[-40:]))
        for namespace in namespaces:
            run("ip", "netns", "delete", namespace, check=False)


def establish(
    endpoints: list[Endpoint], ike_version: int, *, duplicate_negotiation: bool = True
) -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda e: e.command("swanctl", "--load-all", "--file", str(e.config)), endpoints
            )
        )
    assert all(result.returncode == 0 for result in results)
    for endpoint in endpoints:
        assert "fixture:" in endpoint.command("swanctl", "--list-conns").stdout
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda e: e.command(
                    "iptables",
                    "-D",
                    "OUTPUT",
                    "-p",
                    "udp",
                    "-m",
                    "multiport",
                    "--dports",
                    "500,4500",
                    "-j",
                    "DROP",
                ),
                endpoints,
            )
        )
    deadline = time.monotonic() + 20
    while not all(endpoint.sas()[1] for endpoint in endpoints):
        assert time.monotonic() < deadline, (
            "simultaneous startup did not establish CHILD SAs: "
            + repr([endpoint.command("swanctl", "--list-sas").stdout for endpoint in endpoints])
        )
        time.sleep(0.2)
    for endpoint in endpoints:
        endpoint.command("tc", "qdisc", "del", "dev", "eth0", "root")
    if ike_version == 1 or not duplicate_negotiation:
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


def policy_identity(endpoint: Endpoint) -> tuple[set[int], set[int], int]:
    sas = endpoint.command("swanctl", "--list-sas").stdout
    policies = endpoint.command("ip", "xfrm", "policy").stdout
    stats = endpoint.command("cat", "/proc/net/xfrm_stat").stdout
    children = {int(n) for n in re.findall(r"fixture: #\d+, reqid (\d+), INSTALLED,", sas)}
    templates = {
        int(n) for n in re.findall(r"proto esp(?: spi 0x[0-9a-f]+)? reqid (\d+)", policies)
    }
    mismatches = re.search(r"^XfrmInTmplMismatch\s+(\d+)$", stats, re.M)
    assert mismatches, "kernel template mismatch counter unavailable"
    return children, templates, int(mismatches[1])


def ping_packets(endpoint: Endpoint, target: str, count: int = 30) -> tuple[int, int]:
    result = run(
        "nsenter",
        "-t",
        str(endpoint.process.pid),
        "-m",
        "-n",
        "--",
        "ping",
        "-n",
        "-c",
        str(count),
        "-i",
        ".2",
        "-W",
        "1",
        target,
        check=False,
    )
    match = re.search(r"(\d+) packets transmitted, (\d+) received", result.stdout)
    assert match and result.returncode in (0, 1), "native traffic observation unavailable"
    return int(match[1]), int(match[2])


def restart_ipsec(endpoint: Endpoint) -> None:
    result = subprocess.run(
        [
            "nsenter",
            "-t",
            str(endpoint.process.pid),
            "-m",
            "-n",
            "--",
            sys.executable,
            "-B",
            "-c",
            "import time; from nebius_vpngw.tunnel_restart import Restart; "
            "assert Restart(time.monotonic()+132).restart_ipsec({'name':'fixture','if_id':100})",
        ],
        capture_output=True,
        text=True,
        timeout=145,
    )
    assert result.returncode == 0, "product tunnel restart did not complete"


def session_identities(endpoint: Endpoint, *, child: bool) -> set[str]:
    """Return only authenticated sessions with the fixture's installed ESP CHILD."""
    code = (
        "import json; from nebius_vpngw.ipsec_observation import snapshot; "
        "inventory=snapshot(timeout=5).require_complete(); "
        "ready=[sa for sa in inventory.sas if sa.name=='fixture' "
        "and sa.state=='ESTABLISHED' "
        "and any(c.ready('fixture',100) for c in sa.children)]; print(json.dumps("
        + (
            "[c.fields.get('spi-out') for sa in ready for c in sa.children "
            "if c.ready('fixture',100)]"
            if child
            else "[sa.unique_id for sa in ready]"
        )
        + "))"
    )
    values = json.loads(endpoint.command(sys.executable, "-B", "-c", code).stdout)
    assert all(isinstance(value, str) and value for value in values)
    return set(values)


def test_simultaneous_restart_preserves_narrowed_policy_identity(restart_endpoints) -> None:
    endpoints, ike_version = restart_endpoints
    establish(endpoints, ike_version, duplicate_negotiation=False)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        # Warmup is separate from every strict steady-state assertion. Initial
        # simultaneous negotiation can still be settling after its first CHILD.
        list(pool.map(lambda i: ping_packets(endpoints[i], endpoints[1 - i].address, 10), (0, 1)))
        assert (
            list(pool.map(lambda i: ping_packets(endpoints[i], endpoints[1 - i].address), (0, 1)))
            == [(30, 30)] * 2
        )
        for _ in range(6):
            before = list(pool.map(policy_identity, endpoints))
            probes = []
            try:
                for index, endpoint in enumerate(endpoints):
                    probes.append(
                        subprocess.Popen(
                            [
                                "nsenter",
                                "-t",
                                str(endpoint.process.pid),
                                "-m",
                                "-n",
                                "--",
                                "ping",
                                "-n",
                                "-i",
                                ".2",
                                "-w",
                                "160",
                                endpoints[1 - index].address,
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    )
                time.sleep(2)
                list(pool.map(restart_ipsec, endpoints))
                time.sleep(5)
                traffic = list(
                    pool.map(lambda i: ping_packets(endpoints[i], endpoints[1 - i].address), (0, 1))
                )
                after = list(pool.map(policy_identity, endpoints))
                assert traffic == [(30, 30)] * 2, "successful restart lost encrypted traffic"
                assert all(children == templates == {100} for children, templates, _ in after)
                assert [row[2] for row in after] == [row[2] for row in before]
            finally:
                for process in probes:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=5)
    if ike_version == 1:
        return
    # Rekey and reauthentication must create genuinely new SA identities while
    # preserving the policy identity through authenticated overlap.
    for phase in ("child-rekey", "ike-rekey", "ike-reauth"):
        before = [policy_identity(endpoint) for endpoint in endpoints]
        child = phase == "child-rekey"
        identities = [session_identities(endpoint, child=child) for endpoint in endpoints]
        assert all(identities), "baseline authenticated ESP sessions are not ready"
        args = ["swanctl", "--rekey"]
        if phase == "child-rekey":
            args.extend(["--child", "fixture"])
        else:
            args.extend(["--ike-id", str(endpoints[0].sas()[0][0])])
        if phase == "ike-reauth":
            args.append("--reauth")
        endpoints[0].command(*args)
        deadline = time.monotonic() + 30
        # A new IKE ID can be CONNECTING before authentication and CHILD install.
        # Keep that transition outside the strict steady-state traffic sample.
        while not all(
            session_identities(endpoint, child=child) - previous
            for endpoint, previous in zip(endpoints, identities, strict=True)
        ):
            assert time.monotonic() < deadline, "native replacement identity was not observed"
            time.sleep(0.2)
        assert time.monotonic() < deadline, "authenticated replacement exceeded its deadline"
        for index, endpoint in enumerate(endpoints):
            assert ping_packets(endpoint, endpoints[1 - index].address) == (30, 30)
        after = [policy_identity(endpoint) for endpoint in endpoints]
        assert all(children == templates == {100} for children, templates, _ in after)
        assert [row[2] for row in after] == [row[2] for row in before]


@pytest.mark.parametrize("restart_endpoints", [2], indirect=True, ids=["ikev2"])
def test_running_daemon_separates_fixed_and_dynamic_reqids(restart_endpoints) -> None:
    endpoints, ike_version = restart_endpoints
    establish(endpoints, ike_version, duplicate_negotiation=False)
    for index, endpoint in enumerate(endpoints):
        ping_packets(endpoint, endpoints[1 - index].address, 10)
        assert ping_packets(endpoint, endpoints[1 - index].address) == (30, 30)
    for index, endpoint in enumerate(endpoints, 1):
        # A deliberately unmanaged CHILD exercises the real daemon allocator,
        # independently of assertions about the rendered reqid_base setting.
        dynamic = endpoint.config.with_name(f"dynamic-{index}.conf")
        original = endpoint.config.read_text()
        child = re.search(r"(?ms)^      fixture \{\n.*?^      \}\n", original)
        assert child is not None
        text = child[0].replace("fixture {", "dynamic-fixture {", 1)
        text = re.sub(r"^\s+reqid = 100\n", "\n", text, flags=re.M)
        text = text.replace("if_id_in = 100", "if_id_in = 101").replace(
            "if_id_out = 100", "if_id_out = 101"
        )
        text = text.replace("start_action = trap|start", "start_action = none")
        text = text.replace(f"10.200.{index}.0/24,169.254.210.0/30", f"10.201.{index}.0/24")
        # Distinct selectors on both sides force the responder to choose the
        # unmanaged CHILD instead of narrowing its broad request to fixture.
        text = text.replace("remote_ts = 0.0.0.0/0", f"remote_ts = 10.201.{3 - index}.0/24")
        assert "reqid = " not in text and f"10.201.{index}.0/24" in text
        assert f"remote_ts = 10.201.{3 - index}.0/24" in text
        # Updating this peer's children also updates its existing IKE sessions.
        # A separate equivalent peer config would reuse an IKE session whose
        # responder still has only the original CHILD configuration.
        dynamic.write_text(original[: child.end()] + text + original[child.end() :])
        dynamic.chmod(0o600)
        loaded = endpoint.command("swanctl", "--load-conns", "--file", str(dynamic))
        assert "1 connections, 0 unloaded" in loaded.stdout
        names = endpoint.command("swanctl", "--list-conns").stdout
        assert re.search(r"^fixture:", names, re.M)
        assert re.search(r"^\s+fixture:", names, re.M)
        assert re.search(r"^\s+dynamic-fixture:", names, re.M)
    endpoints[0].command("swanctl", "--initiate", "--child", "dynamic-fixture", "--timeout", "10")
    for index, endpoint in enumerate(endpoints):
        sas = endpoint.command("swanctl", "--list-sas").stdout
        dynamic_ids = [
            int(n) for n in re.findall(r"dynamic-fixture: #\d+, reqid (\d+), INSTALLED,", sas)
        ]
        managed_ids = [
            int(n) for n in re.findall(r"^\s+fixture: #\d+, reqid (\d+), INSTALLED,", sas, re.M)
        ]
        assert dynamic_ids and min(dynamic_ids) >= 65536
        assert managed_ids and set(managed_ids) == {100}
        ping_packets(endpoint, endpoints[1 - index].address, 10)
        assert ping_packets(endpoint, endpoints[1 - index].address) == (30, 30)


def test_simultaneous_start_converges_and_peer_close_recovers(endpoints) -> None:
    endpoints, ike_version = endpoints
    establish(endpoints, ike_version)
    stable_sessions(endpoints)
    # Loading current configuration to restore another missing connection must
    # leave established healthy connections forwarding with the same identities.
    before_reload = [endpoint.sas() for endpoint in endpoints]
    endpoints[0].command("swanctl", "--load-all", "--file", str(endpoints[0].config))
    time.sleep(0.2)
    assert [endpoint.sas() for endpoint in endpoints] == before_reload
    from nebius_vpngw.agent.vm_ha.tunnel_observation import NativeTunnelObserver
    from nebius_vpngw.agent.vm_ha.tunnels import Health
    from nebius_vpngw.ha_tunnel_policy import ConnectionPolicy, TunnelPath

    for index, endpoint in enumerate(endpoints, 1):
        destinations = frozenset({f"10.200.{3 - index}.1/32"})
        path = TunnelPath(
            "fixture",
            "xfrm0",
            100,
            True,
            destinations,
            "",
            (f"192.0.2.{index}", f"192.0.2.{3 - index}"),
            frozenset({f"10.200.{index}.1/32"}),
        )
        policy = ConnectionPolicy("fixture", "static", (path,), destinations)
        observer = NativeTunnelObserver(
            lambda args, endpoint=endpoint: endpoint.command(*args).stdout
        )
        # Never put kernel XFRM state (which contains key material) in assertions.
        health = observer.observe(policy, "fixture", selected=True)
        assert health is Health.HEALTHY
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


@pytest.mark.parametrize(
    "endpoints", [(2, False), (1, False)], indirect=True, ids=["ikev2", "ikev1"]
)
def test_initial_negotiation_uses_bounded_native_retry_sequence(endpoints) -> None:
    endpoints, _ = endpoints
    endpoint = endpoints[0]
    # The fixture's packet barrier remains in place. Only the initiating peer
    # is configured, so no remote response can end the retransmission sequence.
    endpoint.command("tc", "qdisc", "del", "dev", "eth0", "root")
    endpoint.command("swanctl", "--load-all", "--file", str(endpoint.config))
    deadline = time.monotonic() + 27
    while True:
        lines = endpoint.log.read_text().splitlines()
        giving_up = [line for line in lines if "giving up after 5 retransmits" in line]
        if giving_up:
            break
        assert time.monotonic() < deadline, "native initial negotiation did not finish its sequence"
        time.sleep(0.1)
    starts = [line for line in lines if "initiating " in line and "IKE_SA fixture[" in line]
    assert starts, "initial negotiation start was not observed"
    started = datetime.fromisoformat(starts[0].split()[0])
    ended = datetime.fromisoformat(giving_up[0].split()[0])
    duration = (ended - started).total_seconds()
    # Six waits sum to 20.78125s before jitter, at least 18.703125s with
    # 10% subtractive jitter. Allow small scheduling/clock granularity overhead.
    assert 18.5 <= duration <= 22, f"native retransmission sequence took {duration:.3f}s"
    assert [int(n) for n in re.findall(r"retransmit (\d+) of request", "\n".join(lines))][:5] == [
        1,
        2,
        3,
        4,
        5,
    ]
    while True:
        log = endpoint.log.read_text()
        if (
            "keying attempt 2" in log
            or sum("initiating " in line and "IKE_SA fixture[" in line for line in log.splitlines())
            >= 2
        ):
            break
        assert time.monotonic() < deadline, "persistent connection did not restart initiation"
        time.sleep(0.1)
    assert endpoint.process.poll() is None
