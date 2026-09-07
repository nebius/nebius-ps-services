"""Simulate paged KV allocation, growth, recycling, and internal waste."""

from __future__ import annotations

import argparse

from common import (
    add_common_args,
    load_torch,
    require_h100,
    validate_common_args,
    write_result,
)


def allocate(
    lengths: list[int], block_tokens: int, total_blocks: int
) -> dict[str, int | bool]:
    """Static demand worksheet, independently cross-checked against live state."""
    if block_tokens < 1 or total_blocks < 1 or any(length < 0 for length in lengths):
        raise ValueError("positive capacity and nonnegative lengths required")
    required = sum((length + block_tokens - 1) // block_tokens for length in lengths)
    useful = sum(lengths)
    return {
        "required_blocks": required,
        "free_blocks": max(0, total_blocks - required),
        "shortfall_blocks": max(0, required - total_blocks),
        "useful_tokens": useful,
        "allocated_tokens": required * block_tokens,
        "internal_waste_tokens": required * block_tokens - useful,
        "fits": required <= total_blocks,
    }


class PagedKVPool:
    """Small deterministic allocator: logical request pages map to physical IDs.

    It models exclusive ownership only: no prefix sharing, swapping, tensors,
    preemption or engine timing. Capacity failures must leave all state intact.
    """

    def __init__(self, block_tokens: int, total_blocks: int):
        allocate([], block_tokens, total_blocks)
        self.block_tokens = block_tokens
        self.total_blocks = total_blocks
        self._free = list(range(total_blocks))
        self._tables: dict[str, list[int]] = {}
        self._lengths: dict[str, int] = {}

    def _reserve(self, blocks: int) -> list[int]:
        if blocks > len(self._free):
            raise MemoryError("KV capacity exhausted; no blocks were changed")
        chosen = self._free[:blocks]
        del self._free[:blocks]
        return chosen

    def admit(self, request: str, tokens: int) -> None:
        if not request or request in self._tables or tokens < 0:
            raise ValueError("unique nonempty request and nonnegative tokens required")
        blocks = (tokens + self.block_tokens - 1) // self.block_tokens
        reserved = self._reserve(blocks)
        self._tables[request] = reserved
        self._lengths[request] = tokens
        self.check_invariants()

    def grow(self, request: str, tokens: int) -> None:
        if tokens < self._lengths[request]:
            raise ValueError("growth cannot shrink a request")
        blocks = (tokens + self.block_tokens - 1) // self.block_tokens
        # Reserve before changing either the logical length or the block table.
        extra = self._reserve(blocks - len(self._tables[request]))
        self._tables[request].extend(extra)
        self._lengths[request] = tokens
        self.check_invariants()

    def release(self, request: str) -> None:
        released = self._tables.pop(request)
        del self._lengths[request]
        self._free = sorted(self._free + released)
        self.check_invariants()

    def check_invariants(self) -> None:
        used = [block for table in self._tables.values() for block in table]
        all_blocks = used + self._free
        if sorted(all_blocks) != list(range(self.total_blocks)):
            raise RuntimeError("physical block is lost, duplicated, or out of range")
        for request, table in self._tables.items():
            needed = (
                self._lengths[request] + self.block_tokens - 1
            ) // self.block_tokens
            if len(table) != needed:
                raise RuntimeError("logical block table does not match token length")

    def snapshot(self) -> dict[str, object]:
        self.check_invariants()
        return {
            "block_tables": {key: value[:] for key, value in self._tables.items()},
            "lengths": self._lengths.copy(),
            "free_block_ids": self._free[:],
            "accounting": allocate(
                list(self._lengths.values()), self.block_tokens, self.total_blocks
            ),
        }


def lifecycle_demo(block_tokens: int, total_blocks: int) -> dict[str, object]:
    if total_blocks < 4:
        raise ValueError("the lifecycle demonstration requires at least four blocks")
    pool = PagedKVPool(block_tokens, total_blocks)
    events = []

    def record(event: str) -> None:
        events.append({"event": event, "state": pool.snapshot()})

    record("empty pool")
    pool.admit("a", block_tokens)
    record("admit a: one block")
    pool.admit("b", block_tokens + 1)
    record("admit b: two blocks")
    pool.grow("a", block_tokens + 1)
    record("grow a across page boundary: preserve block 0, append block 3")
    before = pool.snapshot()
    rejected = False
    try:
        pool.grow("b", block_tokens * total_blocks + 1)
    except MemoryError:
        rejected = True
    unchanged = before == pool.snapshot()
    record("reject oversized growth without partial mutation")
    pool.release("a")
    record("complete a: return physical IDs 0 and 3")
    pool.admit("c", block_tokens + 1)
    record("admit c: reuse physical IDs 0 and 3")
    after = pool.snapshot()
    return {
        "events": events,
        "checks": {
            "growth_preserves_mapping": before["block_tables"]["a"] == [0, 3],
            "capacity_failure_detected": rejected,
            "capacity_failure_is_atomic": unchanged,
            "completion_recycles_physical_blocks": after["block_tables"]["c"] == [0, 3],
            "unrelated_request_mapping_preserved": before["block_tables"]["b"]
            == after["block_tables"]["b"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    parser.add_argument("--block-tokens", type=int, default=16)
    parser.add_argument("--total-blocks", type=int, default=128)
    args = parser.parse_args()
    validate_common_args(args)
    if args.block_tokens < 1 or args.total_blocks < 4:
        raise SystemExit(
            "--block-tokens must be positive; --total-blocks must be at least 4"
        )
    torch = load_torch()
    environment = require_h100(torch)
    demonstration = lifecycle_demo(args.block_tokens, args.total_blocks)
    if not all(demonstration["checks"].values()):
        raise SystemExit("Paged-KV lifecycle correctness failed")
    target = write_result(
        args,
        lab_id="27_paged_kv",
        environment=environment,
        measurements={
            "block_tokens": args.block_tokens,
            "total_blocks": args.total_blocks,
            "lifecycle_events": demonstration["events"],
            "demand_worksheet": allocate(
                [31, 65, 127, 9], args.block_tokens, args.total_blocks
            ),
            "scope": "CPU allocator mechanics, not live KV tensors or engine performance",
        },
        correctness=demonstration["checks"],
    )
    print(f"Wrote paged-KV evidence: {target}")


if __name__ == "__main__":
    main()
