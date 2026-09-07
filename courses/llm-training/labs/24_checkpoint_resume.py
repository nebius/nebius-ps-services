"""Prove that a complete checkpoint reproduces the next training update."""

from __future__ import annotations

import argparse
import io

from common import (
    add_common_args,
    load_torch,
    require_h100,
    seed_everything,
    validate_common_args,
    write_result,
)
from tiny_lm import build_tiny_lm, make_language_batch


def make_rng_language_batch(
    torch: object, *, batch_size: int, sequence: int, vocab_size: int
) -> tuple[object, object]:
    """Build a batch whose exact contents depend on both CPU and CUDA RNG state."""
    cpu_tokens = torch.randint(
        0, vocab_size, (batch_size, sequence), device="cpu", dtype=torch.long
    )
    cuda_tokens = torch.randint(
        0, vocab_size, (batch_size, sequence), device="cuda", dtype=torch.long
    )
    inputs = (cpu_tokens.to("cuda") + cuda_tokens).remainder(vocab_size)
    labels = torch.roll(inputs, shifts=-1, dims=1)
    return inputs, labels


def step(
    torch: object, model: object, optimizer: object, inputs: object, labels: object
) -> float:
    optimizer.zero_grad(set_to_none=True)
    logits = model(inputs)
    loss = torch.nn.functional.cross_entropy(
        logits.reshape(-1, logits.shape[-1]), labels.reshape(-1)
    )
    loss.backward()
    optimizer.step()
    return float(loss.detach())


def move_optimizer_state(torch: object, optimizer: object, device: str) -> None:
    """Move tensor-valued optimizer state after a CPU checkpoint load."""
    for state in optimizer.state.values():
        for name, value in state.items():
            if isinstance(value, torch.Tensor):
                state[name] = value.to(device)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    torch.use_deterministic_algorithms(True)
    torch.backends.cuda.matmul.allow_tf32 = False
    model = build_tiny_lm(
        torch, vocab_size=256, hidden_size=128, layers=2, heads=4, max_sequence=64
    ).to("cuda")
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    inputs, labels = make_language_batch(
        torch, batch_size=4, sequence=32, vocab_size=256, device="cuda"
    )
    first_loss = step(torch, model, optimizer, inputs, labels)
    checkpoint = io.BytesIO()
    torch.save(
        {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "cpu_rng": torch.get_rng_state(),
            "cuda_rng": torch.cuda.get_rng_state().cpu(),
        },
        checkpoint,
    )
    reference_inputs, reference_labels = make_rng_language_batch(
        torch, batch_size=4, sequence=32, vocab_size=256
    )
    reference_loss = step(torch, model, optimizer, reference_inputs, reference_labels)
    reference_state = {
        name: value.detach().clone() for name, value in model.state_dict().items()
    }
    without_restore_inputs, without_restore_labels = make_rng_language_batch(
        torch, batch_size=4, sequence=32, vocab_size=256
    )
    omitted_rng_restore_diverges = not bool(
        torch.equal(reference_inputs, without_restore_inputs)
        and torch.equal(reference_labels, without_restore_labels)
    )
    if not omitted_rng_restore_diverges:
        raise SystemExit(
            "Negative control failed: the advanced RNG state repeated a batch."
        )
    checkpoint.seek(0)
    saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
    resumed = build_tiny_lm(
        torch, vocab_size=256, hidden_size=128, layers=2, heads=4, max_sequence=64
    ).to("cuda")
    resumed_optimizer = torch.optim.AdamW(resumed.parameters(), lr=1e-3)
    resumed.load_state_dict(saved["model"])
    resumed_optimizer.load_state_dict(saved["optimizer"])
    move_optimizer_state(torch, resumed_optimizer, "cuda")
    torch.set_rng_state(saved["cpu_rng"].cpu())
    torch.cuda.set_rng_state(saved["cuda_rng"].cpu())
    resumed_inputs, resumed_labels = make_rng_language_batch(
        torch, batch_size=4, sequence=32, vocab_size=256
    )
    generated_batch_exact = bool(
        torch.equal(reference_inputs, resumed_inputs)
        and torch.equal(reference_labels, resumed_labels)
    )
    resumed_loss = step(
        torch, resumed, resumed_optimizer, resumed_inputs, resumed_labels
    )
    max_error = max(
        float((reference_state[name] - value).abs().max())
        for name, value in resumed.state_dict().items()
    )
    exact = (
        generated_batch_exact and reference_loss == resumed_loss and max_error == 0.0
    )
    if not exact:
        raise SystemExit(
            "Checkpoint resume did not reproduce the next loss and parameter update."
        )
    target = write_result(
        args,
        lab_id="24_checkpoint_resume",
        environment=environment,
        measurements={
            "first_loss": first_loss,
            "reference_next_loss": reference_loss,
            "resumed_next_loss": resumed_loss,
            "max_parameter_error": max_error,
            "post_checkpoint_batch_digest_inputs_equal": generated_batch_exact,
            "checkpoint_state": ["model", "optimizer", "cpu_rng", "cuda_rng"],
            "negative_control_omitted_rng_restore_diverged": (
                omitted_rng_restore_diverges
            ),
        },
        correctness={
            "rng_generated_next_batch_exact": generated_batch_exact,
            "omitted_rng_restore_diverges": omitted_rng_restore_diverges,
            "exact_next_update": exact,
        },
    )
    print(f"Wrote resume-equivalence evidence: {target}")


if __name__ == "__main__":
    main()
