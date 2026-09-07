"""Pure, bounded job-local NCCL experiment settings; no cluster configuration."""

from __future__ import annotations

from collections.abc import Mapping

PROFILES = {
    "default": {},
    "socket": {"NCCL_NET": "Socket"},
    "gdr-off": {"NCCL_NET_GDR_LEVEL": "LOC"},
    "ring": {"NCCL_ALGO": "Ring"},
    "tree": {"NCCL_ALGO": "Tree"},
    "qp1": {"NCCL_IB_QPS_PER_CONNECTION": "1"},
    "qp4": {"NCCL_IB_QPS_PER_CONNECTION": "4"},
}
EXPERIMENT_KEYS = (
    "NCCL_NET",
    "NCCL_IB_DISABLE",
    "NCCL_NET_GDR_LEVEL",
    "NCCL_ALGO",
    "NCCL_PROTO",
    "NCCL_IB_QPS_PER_CONNECTION",
    "NCCL_IB_SPLIT_DATA_ON_QPS",
    "NCCL_IB_GID_INDEX",
    "NCCL_TESTS_SPLIT",
    "NCCL_TESTS_SPLIT_MASK",
    "NCCL_TESTS_DEVICE",
)


def profile_settings(name: str, environment: Mapping[str, str]) -> dict[str, str]:
    if name not in PROFILES:
        raise ValueError("Unknown experiment profile")
    conflicts = [key for key in EXPERIMENT_KEYS if key in environment]
    if conflicts:
        raise ValueError(
            "Conflicting inherited NCCL experiment settings: "
            + ", ".join(conflicts)
            + ". Start a clean job shell; ask the owner before changing required site settings."
        )
    return PROFILES[name].copy()


def message_sizes(minimum: int, maximum: int) -> list[int]:
    if not (8 <= minimum <= maximum <= 256 * 2**20):
        raise ValueError("Message range must be between 8 bytes and 256 MiB")
    if minimum & (minimum - 1) or maximum & (maximum - 1):
        raise ValueError("Message bounds must be powers of two")
    sizes = []
    size = minimum
    while size <= maximum:
        sizes.append(size)
        size *= 2
    return sizes
