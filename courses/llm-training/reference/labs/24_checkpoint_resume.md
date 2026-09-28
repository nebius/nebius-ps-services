# Lab 24: Reproduce the next update after checkpoint restoration

Saving weights alone does not guarantee that resumed training follows the same trajectory. Optimizer state and random-number generators also affect the next batch and update. This lab creates a complete in-memory checkpoint, restores it, and compares the next step with an uninterrupted reference, including a negative control that deliberately omits RNG restoration.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/24_checkpoint_resume.json).

Use one H100 and the Training environment. This deterministic mechanics exercise serializes state in memory; it is not a disk durability, process-crash recovery, or distributed checkpoint test.

Large checkpoints stress storage and can expose rank skew or partial writes. Two-node labs validate bounded state ownership and restart mechanics, not production checkpoint bandwidth.

## Concepts and code path

The program performs an initial update, serializes model, optimizer, CPU RNG, and CUDA RNG state, and advances the reference trajectory. A restored model/optimizer repeats the next randomly generated batch and step. A separate control skips RNG restoration to show why state completeness matters. Comparisons check both next-batch identity and parameter results.

The supplied example performs three updates: an initial update before the checkpoint, the reference next update, and the restored next update. Its in-memory checkpoint contains model parameters, AdamW state, and CPU/CUDA random-number generator state. Restoring all four reproduces the next batch, loss and parameters exactly. The omitted-RNG control checks that the next generated batch differs; it does not run another optimizer update or test scheduler and sampler restoration.

Use Lab 24 to compare uninterrupted and interrupted/resumed trajectories.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run the supplied deterministic case without adding asynchronous data loading or nondeterministic operations. Those additions require a broader checkpoint contract and new controls.

```bash
umask 077
"$COURSE_PYTHON" labs/24_checkpoint_resume.py --help
python3 tools/submit_lab.py --lab 24_checkpoint_resume slurm/single_gpu.sbatch labs/24_checkpoint_resume.py --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 24_checkpoint_resume --job "$LAB_JOB_ID"
```

Require exact next-batch generation, exact next loss and parameter update, and a different generated batch when RNG restoration is omitted. Inspect reference/resumed loss, `max_parameter_error`, and `checkpoint_state`. A successful serialization call alone does not prove any of these properties.

Retain the supplied checkpoint-state list, next-batch equality flag, next losses, maximum parameter error and omitted-RNG result. A persistent checkpoint manifest, state hashes, saved gradients and data cursor belong to a separately implemented recovery extension.

Resume equivalence is proven at the next update boundary, not by successful deserialization alone.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Reference next loss | `reference_next_loss` | `none` |
| Resumed next loss | `resumed_next_loss` | `none` |
| Max parameter error | `max_parameter_error` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 24_checkpoint_resume \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Name the state that determines the next operation in your own training loop: scheduler, scaler, sampler position, accumulation progress, and data cursor may matter. Which are absent because this lab intentionally uses a smaller system?

Frequent checkpoints reduce recovery loss but consume I/O, storage, and synchronization time. Asynchronous checkpointing shortens exposed time but adds consistency and lifetime complexity.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 24_checkpoint_resume --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/24_checkpoint_resume.py --profile small
```

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Expand lab_workload and CUDA API/GPU rows. Locate training updates and device-to-host/host-to-device checkpoint transfers. Distinguish host serialization from GPU work, and require reference/resumed losses and parameter error to match. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A different next batch points to RNG or data-position restoration. If inputs match but parameters differ, investigate optimizer/model state or nondeterministic execution. Diagnose the earliest divergence rather than loosening exact equality automatically.

Saving mid-accumulation without accumulated gradients silently changes the next optimizer step.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Checkpoint correctness is a reproduced state transition, not merely a saved file. Extend this proof to a private disk checkpoint and fresh process, then add distributed shards and data-loader position as separately verified responsibilities.

Checkpoint only at a declared boundary or persist every partial state needed for that boundary.

List the state required for exact data order and exact numerical continuation.
