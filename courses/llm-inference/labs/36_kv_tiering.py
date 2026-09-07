"""Model KV-prefix retention, expiry and restore cost without storage I/O or an engine."""

from __future__ import annotations

import argparse
from collections import OrderedDict
from dataclasses import dataclass
import math
from typing import Any

from common import add_common_args, validate_common_args, write_result


@dataclass(frozen=True)
class Policy:
    capacities: tuple[int, int, int] = (2, 2, 8)
    ttl_ms: float = 500.0
    prefix_bytes: int = 4 * 2**20
    prefill_ms: float = 8.0
    host_gbps: float = 20.0
    storage_gbps: float = 2.0
    latency_ms: float = 0.2

    def __post_init__(self) -> None:
        if len(self.capacities) != 3 or any(
            type(c) is not int or not 0 <= c <= 1024 for c in self.capacities
        ):
            raise ValueError("Provide three capacities from 0 through 1024")
        if type(self.prefix_bytes) is not int or not 1 <= self.prefix_bytes <= 2**40:
            raise ValueError("Prefix bytes must be an integer from 1 through 2**40")
        for value in (self.ttl_ms, self.prefill_ms, self.host_gbps, self.storage_gbps):
            if not math.isfinite(value) or not 1e-6 <= value <= 1e9:
                raise ValueError(
                    "TTL, prefill and bandwidth must be finite values in 1e-6..1e9"
                )
        if not math.isfinite(self.latency_ms) or not 0 <= self.latency_ms <= 1e9:
            raise ValueError("Latency must be finite and nonnegative")

    def transfer_ms(self, tier: int) -> float:
        if tier == 0:
            return 0.0
        rate = self.host_gbps if tier == 1 else self.storage_gbps
        # GB/s is decimal, bytes are bytes; result is milliseconds.
        return self.latency_ms + self.prefix_bytes / (rate * 1e9) * 1000


class TierCache:
    """Exclusive whole-prefix LRU tiers; only inactive, reusable entries exist."""

    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.tiers: list[OrderedDict[tuple[str, int], float]] = [
            OrderedDict() for _ in range(3)
        ]
        self.last_time = -1.0

    def assert_invariants(self) -> None:
        keys = [key for tier in self.tiers for key in tier]
        if len(set(keys)) != len(keys):
            raise RuntimeError("A prefix is owned by multiple exclusive tiers")
        if any(
            len(tier) > capacity
            for tier, capacity in zip(self.tiers, self.policy.capacities, strict=True)
        ):
            raise RuntimeError("Cache capacity exceeded")

    def restart_worker(self) -> None:
        self.tiers[0].clear()
        self.tiers[1].clear()

    def insert(
        self, key: tuple[str, int], expires: float, tier_index: int = 0
    ) -> tuple[int, float, int]:
        if tier_index == 3:
            return 0, 0.0, 1
        if self.policy.capacities[tier_index] == 0:
            return self.insert(key, expires, tier_index + 1)
        tier = self.tiers[tier_index]
        bytes_written = self.policy.prefix_bytes if tier_index > 0 else 0
        write_ms = self.policy.transfer_ms(tier_index)
        evictions = 0
        if len(tier) == self.policy.capacities[tier_index]:
            evicted, deadline = tier.popitem(last=False)
            extra_bytes, extra_ms, evictions = self.insert(
                evicted, deadline, tier_index + 1
            )
            bytes_written += extra_bytes
            write_ms += extra_ms
        tier[key] = expires
        return bytes_written, write_ms, evictions

    def access(self, namespace: str, prefix: int, now_ms: float) -> dict[str, Any]:
        if not namespace or type(prefix) is not int or prefix < 0:
            raise ValueError(
                "A nonempty cache identity and nonnegative prefix ID are required"
            )
        if not math.isfinite(now_ms) or now_ms < 0 or now_ms < self.last_time:
            raise ValueError(
                "Arrival time must be finite, nonnegative and nondecreasing"
            )
        self.last_time = now_ms
        expired = 0
        for tier in self.tiers:
            for key in list(tier):
                if tier[key] <= now_ms:
                    del tier[key]
                    expired += 1
        key = (namespace, prefix)
        found = next((i for i, tier in enumerate(self.tiers) if key in tier), None)
        if found is not None:
            del self.tiers[found][key]
        restore_ms = self.policy.transfer_ms(found) if found is not None else None
        reuse = restore_ms is not None and restore_ms < self.policy.prefill_ms
        cost = restore_ms if reuse else self.policy.prefill_ms
        read_bytes = self.policy.prefix_bytes if reuse and found != 0 else 0
        write_bytes, write_ms, evictions = self.insert(key, now_ms + self.policy.ttl_ms)
        self.assert_invariants()
        return {
            "prefix": prefix,
            "namespace": namespace,
            "arrival_ms": now_ms,
            "found_tier": ("device", "host", "storage")[found]
            if found is not None
            else "miss",
            "decision": "reuse" if reuse else "recompute",
            "modeled_foreground_ms": cost,
            "modeled_write_service_ms": write_ms,
            "read_bytes": read_bytes,
            "write_bytes": write_bytes,
            "expired_prefixes": expired,
            "capacity_evictions": evictions,
            "tier_occupancy_prefixes": [len(tier) for tier in self.tiers],
        }


def simulate(
    policy: Policy,
    *,
    prefixes: int,
    rounds: int,
    gap_ms: float,
    restart_at: int = -1,
    revision_change_at: int = -1,
) -> dict[str, Any]:
    if not 1 <= prefixes <= 128 or not 1 <= rounds <= 20:
        raise ValueError("Use 1..128 prefixes and 1..20 rounds")
    if not math.isfinite(gap_ms) or not 0 <= gap_ms <= 1e9:
        raise ValueError("Gap must be finite and nonnegative <= 1e9")
    count = prefixes * rounds
    if any(
        value != -1 and not 0 <= value < count
        for value in (restart_at, revision_change_at)
    ):
        raise ValueError(
            "Restart/revision index must be -1 or an existing request index"
        )
    cache = TierCache(policy)
    records = []
    for index in range(count):
        if index == restart_at:
            cache.restart_worker()
        namespace = (
            "model-B"
            if revision_change_at >= 0 and index >= revision_change_at
            else "model-A"
        )
        row = cache.access(namespace, index % prefixes, index * gap_ms)
        row["worker_restart"] = index == restart_at
        records.append(row)
    return {
        "evidence_kind": "deterministic_policy_model",
        "requests": records,
        "reuse_fraction": sum(row["decision"] == "reuse" for row in records) / count,
        "modeled_foreground_total_ms": sum(
            row["modeled_foreground_ms"] for row in records
        ),
        "modeled_write_service_total_ms": sum(
            row["modeled_write_service_ms"] for row in records
        ),
        "no_cache_prefill_total_ms": count * policy.prefill_ms,
        "read_bytes": sum(row["read_bytes"] for row in records),
        "write_bytes": sum(row["write_bytes"] for row in records),
        "boundary": "whole inactive prefixes, exclusive tiers, fixed arrival times and declared costs; no queueing, decode, live TTFT or GDS measurement",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    parser.add_argument("--device-prefixes", type=int, default=2)
    parser.add_argument("--host-prefixes", type=int, default=2)
    parser.add_argument("--storage-prefixes", type=int, default=8)
    parser.add_argument("--prefixes", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--ttl-ms", type=float, default=500)
    parser.add_argument("--gap-ms", type=float, default=50)
    parser.add_argument("--prefix-mib", type=int, default=4)
    parser.add_argument("--prefill-ms", type=float, default=8)
    parser.add_argument("--host-gbps", type=float, default=20)
    parser.add_argument("--storage-gbps", type=float, default=2)
    parser.add_argument("--latency-ms", type=float, default=0.2)
    parser.add_argument("--restart-at", type=int, default=-1)
    parser.add_argument("--revision-change-at", type=int, default=-1)
    args = parser.parse_args()
    validate_common_args(args)
    try:
        policy = Policy(
            capacities=(
                args.device_prefixes,
                args.host_prefixes,
                args.storage_prefixes,
            ),
            ttl_ms=args.ttl_ms,
            prefix_bytes=args.prefix_mib * 2**20,
            prefill_ms=args.prefill_ms,
            host_gbps=args.host_gbps,
            storage_gbps=args.storage_gbps,
            latency_ms=args.latency_ms,
        )
        measurements = simulate(
            policy,
            prefixes=args.prefixes,
            rounds=args.rounds,
            gap_ms=args.gap_ms,
            restart_at=args.restart_at,
            revision_change_at=args.revision_change_at,
        )
    except ValueError as exc:
        parser.error(str(exc))
    measurements["policy"] = vars(policy)
    target = write_result(
        args,
        lab_id="36_kv_tiering",
        environment={"execution": "CPU model; no CUDA, engine or storage access"},
        measurements=measurements,
        correctness={"exclusive_ownership_and_capacity": True},
    )
    print(f"Completed modeled KV policy: {target}")


if __name__ == "__main__":
    main()
