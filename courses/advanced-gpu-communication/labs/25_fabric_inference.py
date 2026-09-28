"""Fixed-request projection decoding: replicated requests versus tensor-sharded weights."""

import argparse

from course_evidence import evidence_phase
from fabric_common import all_correct, initialize, measure
from inference_common import (
    add_common_args,
    close_distributed,
    validate_common_args,
    write_result,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--placement", choices=("replicated", "tensor"), default="replicated"
    )
    args = parser.parse_args()
    validate_common_args(args)
    torch, rank, world, _local, env = initialize(args.seed)
    try:
        requests, width, tokens = (
            (32, 512, 8) if args.profile == "small" else (128, 4096, 32)
        )
        # Educational recurrent projection, not a complete language model or service.
        prompts = torch.randn(requests, width, device="cuda") * 0.01
        weight = torch.randn(width, width, device="cuda") / width**0.5
        reference = prompts.clone()
        for _ in range(tokens):
            reference = torch.tanh(reference @ weight)
        if args.placement == "replicated":
            local_prompts = prompts.chunk(world, dim=0)[rank].contiguous()
            local_weight = weight
            expected = reference.chunk(world, dim=0)[rank]
        else:
            local_prompts = prompts
            local_weight = weight.chunk(world, dim=0)[rank].clone()
            expected = reference
        # Remove the reference weights from measured memory; reference output remains for validation.
        del weight, prompts
        output = None

        def operation():
            nonlocal output
            state = local_prompts.clone()
            for _ in range(tokens):
                with evidence_phase("decode_projection"):
                    if args.placement == "replicated":
                        state = state @ local_weight
                    else:
                        part = state.chunk(world, dim=1)[rank].contiguous()
                        state = part @ local_weight
                if args.placement == "tensor":
                    with evidence_phase("decode_all_reduce"):
                        torch.distributed.all_reduce(state)
                state = torch.tanh(state)
            output = state

        torch.cuda.reset_peak_memory_stats()
        times = measure(torch, operation, args.warmup, args.iterations)
        all_correct(
            torch,
            bool(torch.allclose(output, expected, rtol=3e-3, atol=3e-5)),
            "Sharded decoding differs from full-weight reference",
        )
        peak = torch.tensor(
            torch.cuda.max_memory_allocated(), device="cuda", dtype=torch.int64
        )
        torch.distributed.all_reduce(peak, op=torch.distributed.ReduceOp.MAX)
        if rank == 0:
            print(
                write_result(
                    args,
                    lab_id="25_fabric_inference",
                    environment=env,
                    measurements={
                        "requests": requests,
                        "width": width,
                        "generated_tokens_per_request": tokens,
                        "ranks": world,
                        "batch": times,
                        "tokens_per_second": requests
                        * tokens
                        / times["median_ms"]
                        * 1000,
                        "peak_allocated_bytes": peak.item(),
                    },
                    correctness={"projection_decode_matches_reference": True},
                )
            )
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
