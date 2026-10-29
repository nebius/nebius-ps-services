"""Standalone standard-library probe copied into a temporary VM by cloud-init."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import socket
import ssl
import tempfile
import time
from pathlib import Path

RESULT_PATH = Path("/var/lib/cxcli-nat-probe/result.json")


def measure(config: dict) -> dict:
    report = {
        "schema": 1,
        "program_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "run_id": config["run_id"],
        "activation": config["activation"],
        "config_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "boot_time": next(
            int(line.split()[1])
            for line in Path("/proc/stat").read_text().splitlines()
            if line.startswith("btime ")
        ),
        "endpoint_ip": config["endpoint_ip"],
        "observed_ip": "",
        "ok": False,
    }
    for _ in range(5):
        connection = http.client.HTTPSConnection("api.ipify.org", timeout=15)
        try:
            # Pin a reviewed global IPv4 address, bypass DNS, proxies and redirects.
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(15)
            try:
                sock.connect((config["endpoint_ip"], 443))
                connection.sock = ssl.create_default_context().wrap_socket(
                    sock, server_hostname="api.ipify.org"
                )
            except BaseException:
                sock.close()
                raise
            connection.request(
                "GET",
                "/?format=json",
                headers={
                    "Host": "api.ipify.org",
                    "Connection": "close",
                    "Cache-Control": "no-cache",
                },
            )
            response = connection.getresponse()
            body = response.read(1025)
            if response.status != 200 or len(body) > 1024:
                raise ValueError("Invalid endpoint response")
            report["observed_ip"] = str(ipaddress.IPv4Address(json.loads(body)["ip"]))
            report["ok"] = report["observed_ip"] == config["expected_ip"]
            break
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            time.sleep(2)
        finally:
            connection.close()
    report["finished_at"] = time.time()
    return report


def write_result(report: dict, path: Path = RESULT_PATH) -> None:
    # Cloud-init runs as root. The SSH user can read, but cannot replace the
    # directory, result or program. Publish only a complete JSON document.
    path.parent.mkdir(mode=0o755, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".result-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            os.fchmod(output.fileno(), 0o644)
            json.dump(report, output, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    config = json.loads(Path("/var/lib/cxcli-nat-probe.json").read_text())
    write_result(measure(config))


if __name__ == "__main__":
    main()
