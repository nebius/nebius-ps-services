"""Compare serial and worker-backed DataLoader pipelines feeding an H100."""

from __future__ import annotations

import argparse
import time

from common import (
    add_common_args,
    load_torch,
    require_h100,
    resolve_int_override,
    seed_everything,
    summarize_ms,
    validate_common_args,
    write_result,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--batches", type=int, default=None)
    args = parser.parse_args()
    validate_common_args(args)
    batches = resolve_int_override(
        args.batches,
        20 if args.profile == "smoke" else 100,
        option="--batches",
    )
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    feature_width = 4_096

    class SyntheticDataset(torch.utils.data.Dataset):
        def __len__(self) -> int:
            return (batches + args.warmup) * 8

        def __getitem__(self, index: int) -> object:
            generator = torch.Generator().manual_seed(index)
            sample = torch.randn(feature_width, generator=generator)
            return sample.sin().cos()

    dataset = SyntheticDataset()

    def run_loader(workers: int) -> tuple[dict[str, object], float]:
        loader = torch.utils.data.DataLoader(
            dataset,
            batch_size=8,
            num_workers=workers,
            pin_memory=True,
            persistent_workers=workers > 0,
            prefetch_factor=2 if workers > 0 else None,
        )
        phase_samples: dict[str, list[float]] = {
            "batch_ready_gap": [],
            "h2d": [],
            "device_consumption": [],
            "end_to_end": [],
        }
        checksum = torch.zeros((), device="cuda")
        iterator = iter(loader)
        for _ in range(args.warmup):
            warmup_batch = next(iterator).to("cuda", non_blocking=True)
            warmup_batch.square().mean()
        torch.cuda.synchronize()
        host_batch_bytes = None
        all_batches_pinned = True
        for _ in range(batches):
            step_started = time.perf_counter()
            host_batch = next(iterator)
            batch_ready = time.perf_counter()
            current_bytes = host_batch.numel() * host_batch.element_size()
            if host_batch_bytes is None:
                host_batch_bytes = current_bytes
            elif current_bytes != host_batch_bytes:
                raise SystemExit("Input batches changed byte size during measurement.")
            all_batches_pinned = all_batches_pinned and bool(host_batch.is_pinned())
            h2d_start = torch.cuda.Event(enable_timing=True)
            h2d_end = torch.cuda.Event(enable_timing=True)
            compute_start = torch.cuda.Event(enable_timing=True)
            compute_end = torch.cuda.Event(enable_timing=True)
            h2d_start.record()
            batch_value = host_batch.to("cuda", non_blocking=True)
            h2d_end.record()
            compute_start.record()
            checksum = checksum + batch_value.square().mean()
            compute_end.record()
            compute_end.synchronize()
            step_finished = time.perf_counter()
            phase_samples["batch_ready_gap"].append(
                (batch_ready - step_started) * 1_000
            )
            phase_samples["h2d"].append(float(h2d_start.elapsed_time(h2d_end)))
            phase_samples["device_consumption"].append(
                float(compute_start.elapsed_time(compute_end))
            )
            phase_samples["end_to_end"].append((step_finished - step_started) * 1_000)
        assert host_batch_bytes is not None
        return (
            {
                "num_workers": workers,
                "prefetch_factor": 2 if workers > 0 else None,
                "pin_memory": True,
                "non_blocking": True,
                "host_batch_bytes": host_batch_bytes,
                "all_batches_pinned": all_batches_pinned,
                "phases": {
                    name: {
                        **summarize_ms(samples),
                        "sample_count": len(samples),
                    }
                    for name, samples in phase_samples.items()
                },
            },
            float(checksum.item()),
        )

    serial, serial_sum = run_loader(0)
    worker, worker_sum = run_loader(4)
    correct = abs(serial_sum - worker_sum) <= max(1e-4, abs(serial_sum) * 1e-5)
    if not correct:
        raise SystemExit("Pipeline variants produced different checksums.")
    if not serial["all_batches_pinned"] or not worker["all_batches_pinned"]:
        raise SystemExit("DataLoader returned an unpinned timed batch.")
    target = write_result(
        args,
        lab_id="05_input_pipeline",
        environment=environment,
        measurements={
            "batches": batches,
            "timing_boundary": "single-stream serialized component evidence",
            "variants": {"num_workers_0": serial, "num_workers_4": worker},
        },
        correctness={"checksum": True, "all_batches_pinned": True},
    )
    print(f"Completed input pipeline experiment: {target}")


if __name__ == "__main__":
    main()
