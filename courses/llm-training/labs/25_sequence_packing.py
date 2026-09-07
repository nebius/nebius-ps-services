"""Compare padded and boundary-safe packed causal-language-model batches."""

from __future__ import annotations

import argparse

from common import (
    add_common_args,
    load_torch,
    require_h100,
    validate_common_args,
    write_result,
)


def first_fit_bins(lengths: list[int], capacity: int) -> list[list[int]]:
    """Pack sequence lengths without splitting an example."""
    bins: list[list[int]] = []
    used: list[int] = []
    for length in sorted(lengths, reverse=True):
        for index, consumed in enumerate(used):
            if consumed + length <= capacity:
                bins[index].append(length)
                used[index] += length
                break
        else:
            bins.append([length])
            used.append(length)
    return bins


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    parser.add_argument("--capacity", type=int, default=256)
    args = parser.parse_args()
    validate_common_args(args)
    if args.capacity < 32:
        raise SystemExit("--capacity must be at least 32")
    torch = load_torch()
    environment = require_h100(torch)
    lengths = [31, 48, 63, 77, 91, 113]
    if max(lengths) > args.capacity:
        raise SystemExit("capacity is smaller than an example")
    bins = first_fit_bins(lengths, args.capacity)
    padded_tokens = len(lengths) * max(lengths)
    packed_tokens = len(bins) * args.capacity
    valid_tokens = sum(lengths)
    boundary_mask = torch.zeros(
        (packed_tokens, packed_tokens), device="cuda", dtype=torch.bool
    )
    cursor = 0
    spans: list[tuple[int, int, int]] = []
    for bin_id, group in enumerate(bins):
        for length in group:
            start = cursor
            boundary_mask[cursor : cursor + length, cursor : cursor + length] = (
                torch.ones((length, length), device="cuda", dtype=torch.bool).tril()
            )
            cursor += length
            spans.append((bin_id, start, cursor))
        cursor = ((cursor + args.capacity - 1) // args.capacity) * args.capacity
    backward_boundary_checks = [
        not bool(boundary_mask[start, prior_end - 1].item())
        for (prior_bin, _prior_start, prior_end), (bin_id, start, _end) in zip(
            spans, spans[1:]
        )
        if bin_id == prior_bin and start == prior_end
    ]
    within_example_controls = [
        bool(boundary_mask[end - 1, start].item())
        and bool(boundary_mask[start, start].item())
        and bool(boundary_mask[end - 1, end - 1].item())
        for _bin_id, start, end in spans
    ]
    no_cross_example_attention = bool(backward_boundary_checks) and all(
        backward_boundary_checks
    )
    valid_within_example_attention = all(within_example_controls)
    target = write_result(
        args,
        lab_id="25_sequence_packing",
        environment=environment,
        measurements={
            "lengths": lengths,
            "bins": bins,
            "valid_tokens": valid_tokens,
            "padded_tokens": padded_tokens,
            "packed_capacity_tokens": packed_tokens,
            "padding_efficiency": round(valid_tokens / padded_tokens, 4),
            "packing_efficiency": round(valid_tokens / packed_tokens, 4),
        },
        correctness={
            "every_adjacent_packed_boundary_checked": len(backward_boundary_checks)
            == sum(max(0, len(group) - 1) for group in bins),
            "no_cross_example_attention": no_cross_example_attention,
            "within_example_causal_controls_pass": valid_within_example_attention,
            "all_examples_assigned": sum(map(len, bins)) == len(lengths),
        },
    )
    print(f"Wrote packing evidence: {target}")


if __name__ == "__main__":
    main()
