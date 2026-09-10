# Lab 33: Tune real DDP buckets and communication hooks

Gradient readiness only creates an opportunity for overlap; DDP must group gradients into buckets and communicate them in a valid order. This lab exposes that behavior and compares full-precision reduction with FP16, BF16 and PowerSGD communication hooks. You will observe actual bucket sizes, measure complete optimizer steps, and examine numerical drift against an independently evolving full-batch FP32 reference rather than assuming fewer bytes guarantee a better training run.

## Before you start

**Theory preparation:** Read Lessons 11–13 for DDP, gradient readiness, communication hooks, averaging and lossy compression. Reuse Lesson 7's relative L2 concept. Complete Lab 28 and network qualification before these trials.

Complete Lessons 11–13, Lab 28's readiness model and the Optimizations networking workshop. Use the existing two-node launcher with one full H100 per node. The PyTorch 2.14 manifest remains the target; clean target qualification is separate. BF16 communication requires a supported NCCL newer than 2.9.6 and is an experimental framework API. No datasets or model downloads are needed. A pair of one-GPU nodes cannot demonstrate intra-node NVLink behavior.

## Concepts and code path

The fixture has four FP32 Linear/Tanh stages, 256-wide for smoke and 1024-wide for h100. Tanh is the smooth hyperbolic tangent, bounded between -1 and 1. Each rank trains on an equal disjoint half of the same generated global batch. MSE uses a mean, so averaged rank gradients match the full-batch objective. Candidate and reference use plain SGD: `parameter -= learning_rate * gradient`. The independent reference evolves on the complete batch.

DDP receives `--bucket-cap-mb` and exactly one registered hook. The hook wrapper logs public GradBucket indices and uncompressed bytes before delegating to the framework's averaging, FP16, BF16 or PowerSGD implementation. Those bytes describe original gradients, not network traffic. Buckets can rebuild during startup. PowerSGD uses error feedback and warm start; `--power-start` must be at least two, and warm-up must include a compressed step. The default start of two is a short mechanics setting, not a recommendation for real training.

Reference checks follow timing. Both candidate and reference updates are checked against their actual gradients, including a nonzero-change requirement. The allreduce configuration also requires FP32 gradient/parameter agreement with the oracle. Lossy configurations report gradient and parameter trajectory relative L2 errors; those errors include accumulated state differences after earlier approximate steps. They are not isolated single-step compression error or convergence acceptance.

## Practice

Consider 64 MiB of FP32 gradients and an illustrative effective link rate of 8 GiB/s: payload/rate alone is 7.8125 ms, before collective topology, startup and contention. Casting the payload to FP16 halves its nominal bytes, but conversion and reduction still cost time. If communication already fits beneath independent backward work, reducing its duration may not reduce the joined step. A trace that shows less collective time but unchanged or worse step time is a valid reason to reject compression.

After Lab 28's synthetic readiness schedule, run Lab 33: Tune real DDP buckets and communication hooks. Compare two bucket caps with the allreduce hook first, using fresh jobs. Hold the selected cap fixed while comparing FP16, BF16 and PowerSGD separately. Do not interpret a cap sweep as transport qualification; reuse Optimizations' NCCL workshop before making topology claims.

Use fresh jobs for each configuration, preserving seed, workload, warm-up and iterations. First vary only bucket cap; 0.1 MB is intentionally below a smoke layer's matrix size, so actual bucket sizes may exceed the hint. Keep the cap fixed before changing the hook.

```bash
umask 077
sbatch slurm/two_node.sbatch labs/33_ddp_buckets.py --hook allreduce --bucket-cap-mb 1
sbatch slurm/two_node.sbatch labs/33_ddp_buckets.py --hook allreduce --bucket-cap-mb 0.1
sbatch slurm/two_node.sbatch labs/33_ddp_buckets.py --hook fp16 --bucket-cap-mb 0.1
sbatch slurm/two_node.sbatch labs/33_ddp_buckets.py --hook bf16 --bucket-cap-mb 0.1
sbatch slurm/two_node.sbatch labs/33_ddp_buckets.py --hook powersgd --bucket-cap-mb 0.1 --power-rank 1 --power-start 2 --warmup 4
```

Only rank zero writes the private JSON. Repeat candidate and baseline at least three times in independent jobs. Use the supplied two-node Nsight launcher for a separate short run. It requires the shared course/results filesystem and traces each local torchrun child into a separate report; do not run a distributed lab through a single-GPU profiler launcher. Label CUDA/NVTX ranges on every relevant rank, and add NCCL API tracing only when the installed Nsight version supports it. The standard CUDA trace already shows NCCL device kernels.

```bash
umask 077
sbatch slurm/nsys_ddp.sbatch --hook allreduce --bucket-cap-mb 0.1 --warmup 2 --iterations 2
```

Reports are retained under a unique private `results/nsys-ddp-*` directory, one report per node. Correlate the measured-step ranges across both reports. An unavailable profiler leaves only this diagnostic gate pending.

## Check your results

The bucket capacity is a hint. Inspect actual uncompressed bucket sizes after warm-up and rebuilding before interpreting communication timing.

Inspect `trajectory` for every startup and measured step: candidate/reference global mean loss, gradient/parameter trajectory relative L2 error and `observed_buckets`. Require finite values, nonempty bucket observations and verified SGD updates. The allreduce result additionally requires `rtol=1e-5, atol=1e-6` agreement. Compression deliberately has no fabricated universal quality tolerance: `quality_status` stays pending for task convergence.

`slowest_rank_step_samples_ms` includes forward, mean loss, backward, hook work and SGD. It excludes the barrier, timing aggregation and full-batch oracle. This is an isolated-step experiment with validation gaps, not continuous training-job throughput. `ddp_measured_step`, `ddp_bucket_submit` and `full_batch_reference` distinguish those regions in a trace.

Capture every relevant rank before claiming an exposed communication tail. Compare at least three independent unprofiled jobs and record the installed NCCL and PyTorch versions alongside the existing trajectory and timing records.

## Investigate the behavior

Do post-warm-up buckets differ from startup buckets? Does a smaller cap produce earlier communication or simply more collective overhead? Can conversion or low-rank factor work outweigh reduced transfer? PowerSGD may leave small tensors uncompressed; do not compute wire bandwidth from the raw bucket ledger. A faster configuration with growing numerical drift is a candidate for further evaluation, not an accepted optimization.

## If something goes wrong

Missing or non-finite gradients, or an optimizer step that changes no parameters, invalidate the run. Unequal work or rank-divergent collective order can hang; check data partition and launcher placement before tuning. Unsupported hooks must fail clearly, not silently switch algorithms. PowerSGD warm-up rejection means the requested measurement would not isolate its active mode. Tight FP32 failure requires diagnosing objective, reduction and precision differences before considering looser criteria.

## Takeaways and next step

Use both timeline dependencies and joined-step evidence to explain a bucket choice. Then define an application-specific validation metric, allowable degradation, training horizon and independent seeds before adopting compression in a real model. Compare convergence and full-job cost as well as local step latency; this short synthetic experiment cannot establish either production outcome.
