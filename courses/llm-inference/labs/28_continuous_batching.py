"""Simulate continuous batching and chunked-prefill scheduling tradeoffs."""

from __future__ import annotations

import argparse
import math
import statistics

from common import (
    add_common_args,
    load_torch,
    require_h100,
    validate_common_args,
    write_result,
)


def simulate(
    prompts: list[int],
    outputs: list[int],
    chunk: int | None,
    token_budget: int,
    arrivals: list[int] | None = None,
) -> dict[str, object]:
    """Compare full prefill (chunk=None) with bounded decode-first chunks.

    One service quantum supplies token_budget abstract work units. Each prompt
    or decode token costs one unit, deliberately NOT an H100 latency model.
    A dispatch lasts ceil(work/budget) quanta; arrivals during it cannot join.
    Full prefill may exceed the budget only as a solitary, non-preemptible job.
    """
    if (
        len(prompts) != len(outputs)
        or (chunk is not None and chunk < 1)
        or token_budget < 1
    ):
        raise ValueError("prompts/outputs must align and budgets must be positive")
    arrivals = [0] * len(prompts) if arrivals is None else arrivals
    if len(arrivals) != len(prompts) or any(value < 0 for value in arrivals):
        raise ValueError("nonnegative arrivals must align with prompts")
    if any(value < 0 for value in prompts) or any(value < 1 for value in outputs):
        raise ValueError("prompts must be nonnegative; outputs must be positive")
    remaining_prefill = prompts[:]
    remaining_decode = outputs[:]
    first_token_times = [-1] * len(prompts)
    completion_times = [-1] * len(prompts)
    clock = 0
    scheduled_tokens = 0
    maximum_active = 0
    trace = []
    while any(remaining_prefill) or any(remaining_decode):
        budget = token_budget
        active = [
            index
            for index in range(len(prompts))
            if arrivals[index] <= clock
            and (remaining_prefill[index] or remaining_decode[index])
        ]
        if not active:
            clock = min(
                arrivals[index]
                for index in range(len(prompts))
                if remaining_prefill[index] or remaining_decode[index]
            )
            continue
        maximum_active = max(maximum_active, len(active))
        prefill = {}
        decode = []
        # Keep one decode token per ready request moving before spending the
        # remaining round budget on prompt chunks.
        for index in active:
            if remaining_prefill[index] == 0 and remaining_decode[index] > 0 and budget:
                remaining_decode[index] -= 1
                decode.append(index)
                budget -= 1
        for index in active:
            if remaining_prefill[index] > 0 and budget:
                if chunk is None:
                    amount = remaining_prefill[index]
                    if amount > budget and budget != token_budget:
                        # Do not enlarge a batch that already contains work.
                        continue
                else:
                    amount = min(chunk, remaining_prefill[index], budget)
                remaining_prefill[index] -= amount
                prefill[str(index)] = amount
                budget = max(0, budget - amount)
        work = sum(prefill.values()) + len(decode)
        duration = math.ceil(work / token_budget)
        end = clock + duration
        for index in decode:
            if first_token_times[index] < 0:
                first_token_times[index] = end
            if remaining_decode[index] == 0:
                completion_times[index] = end
        trace.append(
            {
                "start": clock,
                "end": end,
                "prefill": prefill,
                "decode": decode,
                "work": work,
                "active_requests": active,
            }
        )
        scheduled_tokens += work
        clock = end
    latencies = [
        time - arrival
        for time, arrival in zip(first_token_times, arrivals, strict=True)
    ]
    return {
        "policy": "nonpreemptible-full-prefill"
        if chunk is None
        else f"chunked-{chunk}",
        "scheduler_dispatches": len(trace),
        "elapsed_service_quanta": clock,
        "scheduled_tokens": scheduled_tokens,
        "first_token_times": first_token_times,
        "first_token_latencies": latencies,
        "median_first_token_latency_quanta": statistics.median(latencies)
        if latencies
        else None,
        "completion_times": completion_times,
        "completed_requests": sum(time >= 0 for time in completion_times),
        "maximum_active_requests": maximum_active,
        "work_units_per_service_quantum": token_budget,
        "trace": trace,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    prompts = [64, 2048, 128, 1024]
    outputs = [128, 32, 256, 64]
    arrivals = [0, 0, 1, 3]
    token_budget = 256
    unchunked = simulate(prompts, outputs, None, token_budget, arrivals)
    chunked = simulate(prompts, outputs, 64, token_budget, arrivals)
    control = simulate(prompts, outputs, 256, token_budget, arrivals)
    checks = {
        "equivalent_tokens": all(
            result["scheduled_tokens"] == sum(prompts) + sum(outputs)
            for result in (unchunked, chunked, control)
        ),
        "all_requests_complete": all(
            result["completed_requests"] == len(prompts)
            for result in (unchunked, chunked, control)
        ),
    }
    if not all(checks.values()):
        raise SystemExit("Scheduling comparison lost work or request completion")
    target = write_result(
        args,
        lab_id="28_continuous_batching",
        environment=environment,
        measurements={
            "prompts": prompts,
            "outputs": outputs,
            "arrival_service_quanta": arrivals,
            "work_units_per_service_quantum": token_budget,
            "nonpreemptible_full_prefill": unchunked,
            "chunked_64": chunked,
            "chunked_256_control": control,
            "note": (
                "abstract scheduler mechanics, not milliseconds or vLLM emulation; "
                "prefill/decode tokens have equal modeled cost; full prefills exceeding "
                "budget occupy multiple service quanta; arrivals wait until dispatch ends; "
                "a pinned live engine A/B remains a separate gate"
            ),
        },
        correctness=checks,
    )
    print(f"Wrote scheduling evidence: {target}")


if __name__ == "__main__":
    main()
