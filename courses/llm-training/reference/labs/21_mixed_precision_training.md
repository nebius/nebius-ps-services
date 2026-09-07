# Lab 21: Validate precision changes across a full training update

A precision change affects more than forward logits: gradients and optimizer updates can also change. This lab compares FP32, BF16, and scaled FP16 tiny-transformer steps from identical initial state and data. You will use explicit numerical thresholds before comparing device time, end-to-end throughput, and memory, keeping numerical acceptance separate from warmed training trajectories.

## Before you start

**Theory preparation:** Read Lessons 4, 6 and 7 for the full update, memory lifetimes, autocast, FP16 scaling/unscaling, clipping, matched references and aggregate gradient/update error. Lesson 6 is a code-and-ledger preview; run the complete format comparison in Lesson 7.

Use one H100 in the Training environment. In Lesson 6, only inspect the memory-accounting code and prepare the ledger; this script has no memory-only mode. Run the complete comparison in Lesson 7 after reviewing autocast and dynamic gradient scaling. The FP16 path uses scaling; the BF16 path does not require the same scaling behavior by default.

Use the detected H100 memory capacity and preserve operational headroom; do not assume all H100 SKUs expose the same HBM. Full non-MIG allocation is the course contract.

Hopper supports FP8 Tensor Core paths and BF16/FP16/TF32. MXFP8 and NVFP4 examples that require Blackwell remain comparison-only; availability must be queried from the pinned Transformer Engine API.

## Concepts and code path

The program creates matched model/optimizer state and a fixed numerical-test batch. It compares pre-update loss, every collected parameter gradient, and parameter updates against FP32. Separate warmed runs then measure evolving training steps. Common helpers manage device checks and output; the tiny-model module owns architecture. Lab 22 uses a different model, so only within-lab state is matched.

An L2 norm measures overall magnitude as the square root of the sum of squared elements. Relative L2 error divides the norm of candidate-minus-reference by the reference norm. This lab sums over matching named gradient or update tensors as if their elements formed one vector; it does not concatenate them in memory. The denominator is at least `1e-12`, preserving the implementation's near-zero guard. The measure reports aggregate discrepancy, not the largest individual-element error. For reference `[3, 4]` and candidate `[3, 4.1]`, it is `0.1 / 5 = 0.02`. Scalar loss error instead uses the absolute loss difference divided by the larger of the absolute reference loss and `1e-12`.

## Practice

### After Lesson 6

Given 1 billion parameters with BF16 model weights and gradients plus two FP32 Adam moments and an FP32 master copy, those states alone approach 16 GB before activations and temporaries. Change to a sharded optimizer across two ranks. Expected observation: ideal persistent state per rank falls, but transient gathers, communication buffers, allocator reserve, and phase peaks remain in the measured ledger.

Inspect Lab 21's model dimensions, optimizer state and allocation/peak-reporting code to build the memory ledger before running a precision comparison. Predict which allocations must coexist and distinguish estimated live bytes from allocated/reserved peaks. The supplied lab runs complete precision variants, not a memory-only mode; execute it in Lesson 7 and return to this ledger to explain the observations.

### After Lesson 7

For the optional FP8 extension in [Lab 22](22_transformer_engine_fp8.md), consider the following comparison. The supplied Lab 21 script does not execute FP8.

Given a BF16 baseline with finite gradients, run a warmed FP8 `te.autocast` region on matched inputs while retaining FP32 optimizer state. Change only the recipe and supported modules. Expected observation: selected GEMMs may use FP8 and activation bytes may fall, but loss/gradient/update tolerances, amax history, and kernel evidence—not the context manager alone—decide acceptance.

Run Lab 21 and, after qualifying Transformer Engine, optional Lab 22. Each lab creates matched state and data within its own comparison. Lab 21 uses a tiny transformer while Lab 22 uses a Transformer Engine linear layer; their results are not a cross-lab matched-model comparison. Record each lab's numerical thresholds and timing boundary independently.

### Run the supplied experiment

Inspect the numerical-threshold options before running. Keep the defaults unless the experiment has a separately justified acceptance contract; do not loosen thresholds after seeing a failure.

```bash
umask 077
python labs/21_mixed_precision_training.py --help
sbatch slurm/single_gpu.sbatch labs/21_mixed_precision_training.py --profile smoke
```

## Check your results

Require finite values and updates plus the declared FP32 comparison gates. Defaults allow loss relative error 0.05 and gradient/update relative L2 error 0.2; these are recipe-specific training checks, not elementwise BF16 allclose. Inspect thresholds alongside each mode's numerical and warmed-timing records.

Retain a dtype-by-state ledger, phase peaks, allocator statistics, and shape contract.

Optimize the state that owns the actual peak rather than applying a generic memory technique.

Record dtype ledger, recipe, warm-up, selected kernels, loss, gradient/update error, time, and memory.

Accept precision only when the intended fast path runs and the training signal remains within declared tolerance.

## Investigate the behavior

Compare device-region time with end-to-end step time and tokens/s. Explain why gradient scaling can prevent FP16 underflow yet still require unscaled-gradient and update checks. Distinguish a one-step equivalence test from long-run quality.

Recomputation saves activations but spends FLOPs; sharding saves persistent per-rank state but adds gathers and reductions; offload spends PCIe/network bandwidth; reduced precision adds metadata and numerical risk.

Lower precision can reduce activation storage, computation time or communication volume, but adds cast/scaling work and quality risk. One-step closeness checks numerical mechanics; longer-run task evaluation is still required for a training-quality claim.

## If something goes wrong

Non-finite, skipped, or ineffective updates invalidate a candidate. Inspect gradient scale and error components before changing precision or input scale. An apparently faster mode with failed numerical gates is not an accepted optimization.

Adding parameter, gradient, optimizer, and activation maxima from different phases overestimates simultaneous live memory.

Avoid comparing precision modes from independently initialized models.

## Takeaways and next step

Precision choices require explicit error, state-update, and performance evidence. Extend to a meaningful held-out training task before claiming convergence parity, and qualify Transformer Engine separately before attempting FP8.

Build phase-aware lower and upper bounds and confirm them with device measurements.

Identify which memory terms DDP, FSDP2, checkpointing, and mixed precision change.

Clone initial state and input, warm recipes separately, then compare both numerical and performance evidence.

Explain storage, compute, accumulation, master-weight, and optimizer-state precision.
