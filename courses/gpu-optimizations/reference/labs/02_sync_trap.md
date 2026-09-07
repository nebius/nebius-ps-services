# Lab 02: Remove unnecessary per-step host synchronization

Fetching a CUDA scalar into Python can force the host to wait for the GPU. In a repeated loop, those waits can prevent efficient submission of later work. This lab compares retrieving each scalar immediately with accumulating on the device and retrieving once, while checking that the final sum still represents the same computation.

## Before you start

**Theory preparation:** Read Lesson 2 for scalar reductions, CUDA-to-host scalar reads, synchronization and reporting boundaries. Use Lesson 1’s correctness contract to explain why delaying a read must still preserve the required aggregate result.

Use one H100 and understand Lab 01's synchronized wall boundary. The experiment uses generated tensors, not a training dataset. `--steps` controls how often the scalar-producing work is repeated.

## Concepts and code path

A scalar is one number. Calling `.item()` converts a one-element tensor into a Python number. When the value is on the GPU, the host must wait until that value is available before returning it to Python. For example, logging every step can introduce repeated waits even when the GPU computation itself is unchanged. Keeping contributions in a device tensor postpones that observation, but cannot replace an immediate host-side decision that actually needs the value.

One path calls `.item()` at each step and accumulates on the host. The other keeps scalar contributions on the GPU and delays the host read until the end. Both must produce equivalent sums. The optimization is legal only if the application does not need each scalar immediately to decide the next operation.

## Practice

Start with the supplied loop, then vary only the step count. Keep the tensor profile and environment unchanged when investigating how repeated synchronization affects total duration.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/02_sync_trap.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/02_sync_trap.py --profile smoke --steps 100
```

## Check your results

Require `equivalent_sum`, then compare `per_step_item_median_ms` with `delayed_item_median_ms`. The result is a whole-loop comparison, not the isolated cost of a single `.item()` call.

Both sums must be finite. Infinity and NaN (not a number) invalidate the run before a successful result is written; a relative tolerance cannot establish equivalence for those values.

## Investigate the behavior

Locate each host wait in the source's two control flows. Would delaying logging preserve your application's semantics? Would delaying a convergence decision? Use a timeline to distinguish launch gaps from time spent executing useful device work.

## If something goes wrong

A sum mismatch can result from changed work or accumulation order and precision. Investigate it before interpreting timing. If no improvement appears, determine whether each step is already compute-heavy enough to hide submission overhead.

## Takeaways and next step

Batching observations can improve execution without changing the mathematical workload, but it changes when information becomes available. Apply this idea to logging only after defining how much observation delay the application can tolerate.
