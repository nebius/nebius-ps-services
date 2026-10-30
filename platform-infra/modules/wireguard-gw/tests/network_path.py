"""Root-only disposable Linux namespace proof of the canonical MTU/MSS repair.

Run inside an isolated container with NET_ADMIN/SYS_ADMIN and iproute2, iptables,
wireguard-tools, curl, openssl and Python. No host network or cloud credentials.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=15, **kwargs)


def main():
    os.umask(0o077)
    prefix = f"wg-test-{os.getpid()}"
    names = [prefix + suffix for suffix in ("-client", "-gateway", "-api")]
    client, gateway, api = names
    server = None
    with tempfile.TemporaryDirectory(prefix="wireguard-path-") as directory:
        root = Path(directory)
        try:
            for name in names:
                run(["ip", "netns", "add", name])
                run(["ip", "-n", name, "link", "set", "lo", "up"])
            for left, right, left_if, right_if, network in (
                (client, gateway, "eth0", "eth0", "192.0.2"),
                (gateway, api, "eth1", "eth0", "10.10.0"),
            ):
                run(["ip", "link", "add", "vleft", "type", "veth", "peer", "name", "vright"])
                run(["ip", "link", "set", "vleft", "netns", left])
                run(["ip", "link", "set", "vright", "netns", right])
                for name, old, device, suffix in (
                    (left, "vleft", left_if, "1"),
                    (right, "vright", right_if, "2"),
                ):
                    run(["ip", "-n", name, "link", "set", old, "name", device])
                    run(
                        [
                            "ip",
                            "-n",
                            name,
                            "address",
                            "add",
                            network + "." + suffix + "/24",
                            "dev",
                            device,
                        ]
                    )
                    run(["ip", "-n", name, "link", "set", device, "mtu", "1450", "up"])

            def ns(name, args, **kwargs):
                return run(["ip", "netns", "exec", name, *args], **kwargs)

            ns(gateway, ["sysctl", "-qw", "net.ipv4.ip_forward=1"])
            ns(api, ["sysctl", "-qw", "net.ipv4.tcp_mtu_probing=0"])
            run(["ip", "-n", gateway, "route", "add", "default", "via", "10.10.0.2"])
            run(["ip", "-n", api, "route", "add", "10.8.0.0/24", "via", "10.10.0.1"])
            keys = {}
            for name, address, mtu in (
                (client, "10.8.0.2/24", 1420),
                (gateway, "10.8.0.1/24", 1370),
            ):
                private = run(["wg", "genkey"]).stdout.strip()
                public = run(["wg", "pubkey"], input=private).stdout.strip()
                file = root / (name + ".key")
                file.write_text(private)
                keys[name] = (private, public, file)
                ns(name, ["ip", "link", "add", "wg0", "type", "wireguard"])
                ns(name, ["wg", "set", "wg0", "private-key", str(file), "listen-port", "51820"])
                ns(name, ["ip", "address", "add", address, "dev", "wg0"])
                ns(name, ["ip", "link", "set", "wg0", "mtu", str(mtu), "up"])
            ns(
                client,
                [
                    "wg",
                    "set",
                    "wg0",
                    "peer",
                    keys[gateway][1],
                    "endpoint",
                    "192.0.2.2:51820",
                    "allowed-ips",
                    "10.10.0.0/24",
                ],
            )
            ns(gateway, ["wg", "set", "wg0", "peer", keys[client][1], "allowed-ips", "10.8.0.2/32"])
            ns(client, ["ip", "route", "add", "10.10.0.0/24", "dev", "wg0"])
            # Reproduce the ineffective PMTU feedback condition without changing
            # any real host firewall or relying on provider behavior.
            ns(
                gateway,
                [
                    "iptables",
                    "-A",
                    "OUTPUT",
                    "-p",
                    "icmp",
                    "--icmp-type",
                    "fragmentation-needed",
                    "-j",
                    "DROP",
                ],
            )
            cert, key = root / "cert.pem", root / "key.pem"
            run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=fixture",
                    "-addext",
                    "subjectAltName=IP:10.10.0.2",
                ]
            )
            script = root / "https.py"
            script.write_text(
                "import http.server,ssl,sys\nclass H(http.server.BaseHTTPRequestHandler):\n def do_GET(self):\n  b=b'x'*65536;self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)\n def log_message(self,*a): pass\ns=http.server.HTTPServer(('10.10.0.2',8443),H);c=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);c.load_cert_chain(sys.argv[1],sys.argv[2]);s.socket=c.wrap_socket(s.socket,server_side=True);s.serve_forever()\n"
            )
            server = subprocess.Popen(
                ["ip", "netns", "exec", api, "python3", str(script), str(cert), str(key)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.3)
            command = [
                "ip",
                "netns",
                "exec",
                client,
                "curl",
                "--noproxy",
                "*",
                "--cacert",
                str(cert),
                "--max-time",
                "4",
                "-s",
                "https://10.10.0.2:8443",
            ]
            before = subprocess.run(command, capture_output=True, timeout=6)
            assert before.returncode != 0, "Negative control unexpectedly passed"
            source = Path(__file__).resolve().parents[1] / "wireguard_network.py"
            spec = importlib.util.spec_from_file_location("guest", source)
            guest = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(guest)
            guest.STATE_DIR = root / "state"
            guest.WG_DIR = root / "wg"
            guest.CLIENTS_DIR = root / "clients"
            for folder in (guest.STATE_DIR, guest.WG_DIR, guest.CLIENTS_DIR):
                folder.mkdir(mode=0o700)
            guest.CONFIG_PATH = guest.STATE_DIR / "config.json"
            guest.REGISTRY_PATH = guest.STATE_DIR / "clients.json"
            guest.LOCK_PATH = guest.STATE_DIR / "clients.lock"
            guest.CONFIG_PATH.write_text('{"wireguard_tunnel_cidr":"10.8.0.1/24"}')
            guest.REGISTRY_PATH.write_text(
                json.dumps({"clients": [{"public_key": keys[client][1]}]})
            )
            (guest.WG_DIR / "private.key").write_text(keys[gateway][0])
            (guest.WG_DIR / "public.key").write_text(keys[gateway][1])
            (guest.WG_DIR / "wg0.conf").write_text(
                f"[Interface]\nPrivateKey = {keys[gateway][0]}\nAddress = 10.8.0.1/24\n[Peer]\nPublicKey = {keys[client][1]}\nAllowedIPs = 10.8.0.2/32\n"
            )
            # Client credential consistency has separate unit coverage; this
            # fixture exercises kernel routing, WireGuard, TLS and the canonical rules.
            guest.validate_clients = lambda registry: []
            original_run = guest.run
            guest.run = lambda args, **kwargs: original_run(
                ["ip", "netns", "exec", gateway, *args], **kwargs
            )
            request = {
                "wireguard_mtu": None,
                "revision": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
            guest.reconcile_network(request)
            assert len(run(command).stdout) == 65536, "MSS repair did not restore TLS transfer"
            ns(client, ["ip", "link", "set", "wg0", "mtu", "1370"])
            guest.reconcile_network(request)
            assert len(run(command).stdout) == 65536
            assert (
                ns(gateway, ["wg", "show", "wg0", "public-key"]).stdout.strip() == keys[gateway][1]
            )
            assert ns(gateway, ["wg", "show", "wg0", "peers"]).stdout.strip() == keys[client][1]
            print(
                "PASS: negative control stalled; canonical repair restored verified 64KiB TLS; repeat preserved identity and peer"
            )
        finally:
            if server:
                server.terminate()
                server.wait(timeout=5)
            for name in reversed(names):
                subprocess.run(["ip", "netns", "delete", name], capture_output=True, timeout=5)


if __name__ == "__main__":
    main()
