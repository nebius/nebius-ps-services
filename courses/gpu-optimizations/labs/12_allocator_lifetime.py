"""Observe allocated, reserved, reusable, and released CUDA allocator bytes."""

from __future__ import annotations

import argparse
import gc
from typing import Any

from common import (
    add_common_args,
    load_torch,
    require_h100,
    seed_everything,
    validate_common_args,
    write_result,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)

    def snapshot() -> dict[str, Any]:
        stats = torch.cuda.memory_stats()
        return {
            "allocated_bytes": int(stats["allocated_bytes.all.current"]),
            "reserved_bytes": int(stats["reserved_bytes.all.current"]),
            "inactive_split_bytes": int(stats["inactive_split_bytes.all.current"]),
            "allocation_retries": int(stats["num_alloc_retries"]),
            "out_of_memory_events": int(stats["num_ooms"]),
        }

    torch.cuda.empty_cache()
    baseline = snapshot()
    scale = 1 if args.profile == "smoke" else 4

    def allocate(mebibytes: int) -> Any:
        tensor = torch.empty(
            mebibytes * scale * 2**20,
            dtype=torch.uint8,
            device="cuda",
        )
        tensor.fill_(1)
        return tensor

    first = allocate(64)
    middle = allocate(32)
    last = allocate(64)
    after_three_allocations = snapshot()
    del middle
    gc.collect()
    torch.cuda.synchronize()
    after_middle_delete = snapshot()
    replacement = allocate(24)
    after_reuse_candidate = snapshot()
    del last, replacement
    gc.collect()
    torch.cuda.synchronize()
    before_empty_cache_with_first_live = snapshot()
    torch.cuda.empty_cache()
    after_empty_cache_with_first_live = snapshot()
    del first
    gc.collect()
    torch.cuda.synchronize()
    after_all_delete = snapshot()
    torch.cuda.empty_cache()
    after_final_empty_cache = snapshot()

    allocated_dropped = (
        after_middle_delete["allocated_bytes"]
        < after_three_allocations["allocated_bytes"]
    )
    reservation_survived_middle_delete = (
        after_middle_delete["reserved_bytes"]
        >= after_three_allocations["reserved_bytes"]
    )
    first_tensor_was_live = (
        before_empty_cache_with_first_live["allocated_bytes"]
        > baseline["allocated_bytes"]
    )
    empty_cache_preserved_live_allocation = (
        after_empty_cache_with_first_live["allocated_bytes"]
        == before_empty_cache_with_first_live["allocated_bytes"]
    )
    all_lab_allocations_released = (
        after_all_delete["allocated_bytes"]
        < before_empty_cache_with_first_live["allocated_bytes"]
    )
    final_empty_cache_did_not_increase_reservation = (
        after_final_empty_cache["reserved_bytes"] <= after_all_delete["reserved_bytes"]
    )
    if not all(
        (
            allocated_dropped,
            reservation_survived_middle_delete,
            first_tensor_was_live,
            empty_cache_preserved_live_allocation,
            all_lab_allocations_released,
            final_empty_cache_did_not_increase_reservation,
        )
    ):
        raise SystemExit("CUDA allocator lifetime invariants did not hold.")
    target = write_result(
        args,
        lab_id="12_allocator_lifetime",
        environment=environment,
        measurements={
            "allocation_scale": scale,
            "baseline": baseline,
            "after_three_allocations": after_three_allocations,
            "after_middle_delete": after_middle_delete,
            "after_reuse_candidate": after_reuse_candidate,
            "before_empty_cache_with_first_live": before_empty_cache_with_first_live,
            "after_empty_cache_with_first_live": after_empty_cache_with_first_live,
            "after_all_delete": after_all_delete,
            "after_final_empty_cache": after_final_empty_cache,
        },
        correctness={
            "allocated_bytes_drop_after_delete": allocated_dropped,
            "reservation_survived_middle_delete": reservation_survived_middle_delete,
            "first_tensor_live_during_empty_cache": first_tensor_was_live,
            "empty_cache_preserved_live_allocation": (
                empty_cache_preserved_live_allocation
            ),
            "all_lab_allocations_released": all_lab_allocations_released,
            "final_empty_cache_did_not_increase_reservation": (
                final_empty_cache_did_not_increase_reservation
            ),
        },
    )
    print(f"Completed allocator-lifetime experiment: {target}")


if __name__ == "__main__":
    main()
