"""Keep managed H200 hardware CPU IDs independent of the container CPU-time quota."""

from __future__ import annotations

import re
from typing import Any

H200_PLATFORM = "gpu-h200-sxm"
H200_PRESET = "8gpu-128vcpu-1600gb"
H200_PHYSICAL = "Boards=1 SocketsPerBoard=2 CoresPerSocket=32 ThreadsPerCore=2"
H200_QUOTA_DEFAULT = "Boards=1 SocketsPerBoard=1 CoresPerSocket=32 ThreadsPerCore=1"


def h200_static(cpu_millicores: int, *, gres: str = "gpu:8") -> str:
    # Immutable version-one topology repair recipe, retained for sealed
    # predecessor verification. Ordinary materialization must not use this mask.
    # CpuSpecList uses Slurm's abstract socket/core/thread numbering. Preserve
    # whole cores and the same number of usable threads on each physical socket.
    if cpu_millicores <= 0 or cpu_millicores > 128000 or cpu_millicores % 4000:
        raise ValueError("H200 CPU topology requires a whole-core budget divisible by four vCPUs")
    per_socket = cpu_millicores // 2000
    specialization = (
        f" CpuSpecList={per_socket}-63,{64 + per_socket}-127" if per_socket < 64 else ""
    )
    if not re.fullmatch(r"gpu(?::[A-Za-z0-9_-]+)?:8", gres):
        raise ValueError("H200 CPU topology requires all eight GPUs")
    return f"{H200_PHYSICAL}{specialization} Gres={gres}"


def materialize_h200_topology(nodeset: dict[str, Any], *, cpu_millicores: int | None) -> None:
    config = nodeset.setdefault("nodeConfig", {})
    if not isinstance(config, dict) or cpu_millicores is None:
        raise ValueError("H200 CPU topology requires explicit worker CPU resources")
    static = str(config.get("static") or "").strip()
    matches = re.findall(r"(?:^|\s)Gres=(\S+)", static)
    gres = matches[0] if len(matches) == 1 else "gpu:8"
    if type(cpu_millicores) is not int or not 0 < cpu_millicores <= 128000:
        raise ValueError("H200 CPU topology requires a CPU quota within the physical capacity")
    if not re.fullmatch(r"gpu(?::[A-Za-z0-9_-]+)?:8", gres):
        raise ValueError("H200 CPU topology requires all eight GPUs")
    candidate = f"{H200_PHYSICAL} Gres={gres}"
    if static not in {"", H200_QUOTA_DEFAULT, f"{H200_QUOTA_DEFAULT} Gres={gres}", candidate}:
        raise ValueError("H200 worker CPU topology conflicts with the verified platform and budget")
    config["static"] = candidate
