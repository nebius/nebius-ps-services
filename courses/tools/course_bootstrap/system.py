"""Reconcile missing shared-jail packages without changing its platform stack."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


PROTECTED = re.compile(
    r"^(?:nvidia-|libnvidia-|cuda-|nsight-|slurm|soperator|grafana|kube|linux-(?:image|modules)|libnccl)"
)
BASE_PACKAGES = ("python3.12", "python3.12-venv", "ca-certificates")


def prerequisites(catalog, components):
    if not components:
        return []
    return sorted(
        {
            *BASE_PACKAGES,
            *(
                package
                for name in components
                for package in catalog["components"][name].get("system_packages", [])
            ),
        }
    )


def capture(argv):
    result = subprocess.run(
        argv, text=True, capture_output=True, timeout=30, check=False
    )
    if result.returncode:
        raise RuntimeError(
            f"{Path(argv[0]).name} failed while inspecting the installation"
        )
    return result.stdout


def ubuntu():
    values = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    if values.get("ID") != "ubuntu" or values.get("VERSION_ID") != "24.04":
        raise RuntimeError(
            "Automatic package installation supports the Ubuntu 24.04 shared jail"
        )
    if os.uname().machine != "x86_64":
        raise RuntimeError("The course runtime catalog supports Linux x86_64")
    return {"os": "ubuntu-24.04", "architecture": "x86_64"}


def installed(name):
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", name],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "install ok installed"


def check_transaction(output):
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] not in {"Inst", "Remv"}:
            continue
        name = parts[1].split(":", 1)[0]
        if parts[0] == "Remv" or PROTECTED.match(name):
            raise RuntimeError(
                f"Package transaction would change protected platform software: {name}"
            )


def install_packages(run, *, packages, apptainer=False):
    missing = [name for name in packages if not installed(name)]
    need_apptainer = apptainer and shutil.which("apptainer") is None
    if not missing and not need_apptainer:
        return
    privilege = (
        []
        if os.geteuid() == 0
        else ["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive"]
    )
    apt_command = ["apt-get", "-o", "DPkg::Lock::Timeout=120"]
    apt = [*privilege, *apt_command]
    # Bound the plan inside sudo: the unprivileged controller cannot safely
    # terminate root-owned descendants. Retain its lock until timeout exits.
    plan = [*privilege, "timeout", "--kill-after=5s", "30s", *apt_command]
    run([*apt, "update"], label="package-index", transaction=True)
    if need_apptainer:
        if not installed("software-properties-common"):
            missing.append("software-properties-common")
    if missing:
        proposal = run(
            [*plan, "--simulate", "install", "--no-install-recommends", *missing],
            label="system-packages-plan",
            capture_output=True,
            transaction=True,
        )
        check_transaction(proposal)
        run(
            [*apt, "install", "-y", "--no-install-recommends", *missing],
            label="system-packages",
            transaction=True,
        )
    if need_apptainer:
        run(
            [*privilege, "add-apt-repository", "-y", "ppa:apptainer/ppa"],
            label="apptainer-repository",
            transaction=True,
        )
        run([*apt, "update"], label="apptainer-index", transaction=True)
        check_transaction(
            run(
                [
                    *plan,
                    "--simulate",
                    "install",
                    "--no-install-recommends",
                    "apptainer",
                ],
                label="apptainer-plan",
                capture_output=True,
                transaction=True,
            )
        )
        run(
            [*apt, "install", "-y", "--no-install-recommends", "apptainer"],
            label="apptainer",
            transaction=True,
        )


def capacity():
    """Scheduler declarations are selection hints, not live GPU qualification."""
    payload = json.loads(capture(["scontrol", "--local", "--json", "show", "nodes"]))
    if not isinstance(payload.get("nodes"), list):
        raise RuntimeError("Slurm returned an invalid node inventory")
    rows = []
    for node in payload["nodes"]:
        gres = node.get("gres")
        if gres is None:
            rows.append({"gpus": None, "hopper": None})
            continue
        matches = re.findall(
            r"(?:^|,)gpu(?::([^,:()]+))?:(\d+)(?:\([^)]*\))?(?=,|$)", gres
        )
        # Untyped GRES gpu:COUNT is also supported.
        if not matches:
            simple = re.findall(r"(?:^|,)gpu:(\d+)(?:\([^)]*\))?(?=,|$)", gres)
            matches = [("", count) for count in simple]
        count = sum(int(number) for _, number in matches)
        hopper_count = 0
        unknown = False
        for kind, number in matches:
            if re.search(r"h(?:100|200)", kind, re.I):
                hopper_count += int(number)
            elif not re.search(
                r"(?:a100|a10|a30|a40|l4|l40|v100|t4|b100|b200|b300)", kind, re.I
            ):
                unknown = True
        rows.append(
            {
                "gpus": count,
                "hopper": None if unknown else bool(hopper_count),
                "hopper_gpus": None if unknown else hopper_count,
            }
        )
    return rows


def applicability(needs, nodes):
    if not needs.get("gpus", 0):
        return True, "CPU software"
    if not nodes:
        return None, "No configured worker inventory; applicability unverified"
    eligible = [
        node for node in nodes if node["gpus"] is None or node["gpus"] >= needs["gpus"]
    ]
    if len(eligible) < needs.get("nodes", 1):
        return False, "Configured workers do not meet the GPU/node requirement"
    if needs.get("hopper"):
        eligible = [
            node
            for node in eligible
            if (
                node.get("hopper_gpus") is not None
                and node["hopper_gpus"] >= needs["gpus"]
            )
            or (node.get("hopper_gpus") is None and node["hopper"] is not False)
        ]
        if len(eligible) < needs.get("nodes", 1):
            return False, "Configured GPU types do not support this Hopper runtime"
    if any(node["gpus"] is None or node["hopper"] is None for node in eligible):
        return None, "Hardware details unverified; software preparation only"
    return True, "Configured capacity matches; runtime qualification remains in the lab"
