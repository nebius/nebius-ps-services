"""Expose accepted-prefix mechanics and regression conditions in greedy speculation."""

from __future__ import annotations

import argparse
import statistics
import time
from collections import Counter
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
    parser.add_argument("--draft-length", type=int, default=4)
    parser.add_argument("--max-new-tokens", type=int, default=32)
    args = parser.parse_args()
    validate_common_args(args)
    if (
        args.draft_length < 1
        or args.max_new_tokens < 2
        or args.max_new_tokens <= args.draft_length
    ):
        raise SystemExit(
            "--draft-length must be positive and --max-new-tokens must be greater "
            "than --draft-length so the full-acceptance path has room for a target "
            "bonus token."
        )
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)

    vocab_size, hidden = (2_048, 512) if args.profile == "smoke" else (8_192, 2_048)

    class TargetTransition(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.embedding = torch.nn.Embedding(vocab_size, hidden)
            self.projection = torch.nn.Linear(hidden, vocab_size, bias=False)

        def forward(self, token_ids: Any) -> Any:
            return self.projection(torch.nn.functional.gelu(self.embedding(token_ids)))

    target_model = TargetTransition().to("cuda").eval()
    with torch.inference_mode():
        all_tokens = torch.arange(vocab_size, device="cuda")
        ideal_draft = target_model(all_tokens).argmax(dim=-1).cpu().tolist()
    poor_draft = [int((token + 1) % vocab_size) for token in ideal_draft]
    start_token = args.seed % vocab_size

    @torch.inference_mode()
    def target_only() -> tuple[list[int], int]:
        current = start_token
        generated: list[int] = []
        calls = 0
        while len(generated) < args.max_new_tokens:
            token = int(
                target_model(torch.tensor([current], device="cuda"))
                .argmax(dim=-1)
                .item()
            )
            generated.append(token)
            current = token
            calls += 1
        return generated, calls

    @torch.inference_mode()
    def speculative(
        draft_table: list[int],
    ) -> tuple[list[int], int, list[int], int, int, int]:
        current = start_token
        generated: list[int] = []
        target_calls = 0
        accepted_lengths: list[int] = []
        proposal_count = 0
        bonus_count = 0
        recovered_count = 0
        while len(generated) < args.max_new_tokens:
            limit = min(args.draft_length, args.max_new_tokens - len(generated))
            proposals: list[int] = []
            draft_current = current
            for _ in range(limit):
                draft_current = draft_table[draft_current]
                proposals.append(draft_current)
            proposal_count += len(proposals)
            verification_inputs = [current, *proposals]
            verified = (
                target_model(torch.tensor(verification_inputs, device="cuda"))
                .argmax(dim=-1)
                .cpu()
                .tolist()
            )
            target_calls += 1
            accepted = 0
            for proposed, target_token in zip(proposals, verified[:-1], strict=True):
                if proposed == target_token:
                    generated.append(proposed)
                    current = proposed
                    accepted += 1
                else:
                    generated.append(target_token)
                    current = target_token
                    recovered_count += 1
                    break
            else:
                if len(generated) < args.max_new_tokens:
                    bonus_token = verified[-1]
                    generated.append(bonus_token)
                    current = bonus_token
                    bonus_count += 1
            accepted_lengths.append(accepted)
        return (
            generated,
            target_calls,
            accepted_lengths,
            proposal_count,
            bonus_count,
            recovered_count,
        )

    def time_case(operation: Any) -> tuple[list[float], Any, float]:
        for _ in range(args.warmup):
            operation()
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        samples: list[float] = []
        last_result: Any = None
        for _ in range(args.iterations):
            torch.cuda.synchronize()
            started = time.perf_counter()
            last_result = operation()
            torch.cuda.synchronize()
            samples.append((time.perf_counter() - started) * 1_000)
        peak_mib = torch.cuda.max_memory_allocated() / 2**20
        return samples, last_result, peak_mib

    baseline_samples, baseline_result, baseline_peak = time_case(target_only)
    high_samples, high_result, high_peak = time_case(lambda: speculative(ideal_draft))
    low_samples, low_result, low_peak = time_case(lambda: speculative(poor_draft))
    baseline_tokens, baseline_calls = baseline_result
    (
        high_tokens,
        high_calls,
        high_accepts,
        high_proposals,
        high_bonuses,
        high_recovered,
    ) = high_result
    (
        low_tokens,
        low_calls,
        low_accepts,
        low_proposals,
        low_bonuses,
        low_recovered,
    ) = low_result
    exact = baseline_tokens == high_tokens == low_tokens
    high_reduces_calls = high_calls < baseline_calls
    low_preserves_calls = low_calls == baseline_calls
    high_emits_bonus = high_bonuses > 0
    low_emits_recovery = low_recovered > 0
    if (
        not exact
        or baseline_calls != args.max_new_tokens
        or not high_reduces_calls
        or not low_preserves_calls
        or not high_emits_bonus
        or not low_emits_recovery
    ):
        raise SystemExit(
            "Speculative output or target-call acceptance gates did not pass."
        )

    def case_record(
        samples: list[float],
        calls: int,
        accepted: list[int],
        proposals: int,
        bonus_tokens: int,
        recovered_tokens: int,
    ) -> dict[str, Any]:
        histogram = Counter(accepted)
        return {
            "median_e2e_ms": round(statistics.median(samples), 4),
            "target_forward_calls": calls,
            "draft_tokens_proposed": proposals,
            "accepted_draft_tokens": sum(accepted),
            "target_bonus_tokens": bonus_tokens,
            "target_recovered_tokens": recovered_tokens,
            "mean_accepted_prefix": round(statistics.mean(accepted), 3),
            "accepted_prefix_histogram": {
                str(length): count for length, count in sorted(histogram.items())
            },
        }

    target = write_result(
        args,
        lab_id="23_speculative_decoding",
        environment=environment,
        measurements={
            "mechanism_scope": "greedy first-order synthetic target",
            "vocab_size": vocab_size,
            "hidden_size": hidden,
            "draft_length": args.draft_length,
            "generated_tokens": args.max_new_tokens,
            "target_only": {
                "median_e2e_ms": round(statistics.median(baseline_samples), 4),
                "target_forward_calls": baseline_calls,
                "peak_allocated_mib": round(baseline_peak, 1),
            },
            "high_acceptance": {
                **case_record(
                    high_samples,
                    high_calls,
                    high_accepts,
                    high_proposals,
                    high_bonuses,
                    high_recovered,
                ),
                "peak_allocated_mib": round(high_peak, 1),
            },
            "low_acceptance": {
                **case_record(
                    low_samples,
                    low_calls,
                    low_accepts,
                    low_proposals,
                    low_bonuses,
                    low_recovered,
                ),
                "peak_allocated_mib": round(low_peak, 1),
            },
        },
        correctness={
            "both_speculative_paths_match_target_only_greedy_output": exact,
            "high_acceptance_reduces_target_calls": high_reduces_calls,
            "low_acceptance_does_not_reduce_target_calls": low_preserves_calls,
            "full_acceptance_emits_target_bonus_tokens": high_emits_bonus,
            "first_rejection_emits_target_recovery_tokens": low_emits_recovery,
        },
    )
    print(f"Completed speculative-decoding mechanics comparison: {target}")


if __name__ == "__main__":
    main()
