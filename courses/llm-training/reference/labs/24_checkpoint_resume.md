# Lab 24: Reproduce the next update after checkpoint restoration

Saving weights alone does not guarantee that resumed training follows the same trajectory. Optimizer state and random-number generators also affect the next batch and update. This lab creates a complete in-memory checkpoint, restores it, and compares the next step with an uninterrupted reference, including a negative control that deliberately omits RNG restoration.

## Before you start

**Theory preparation:** Read Lesson 5 for model/optimizer/RNG state, deterministic next-step replay and checkpoint completeness, after Lessons 3–4 establish the update. Distinguish this in-memory state-restoration experiment from durable files, crash recovery and distributed checkpointing.

Use one H100 and the Training environment. This deterministic mechanics exercise serializes state in memory; it is not a disk durability, process-crash recovery, or distributed checkpoint test.

Large checkpoints stress storage and can expose rank skew or partial writes. Two-node labs validate bounded state ownership and restart mechanics, not production checkpoint bandwidth.

## Concepts and code path

The program performs an initial update, serializes model, optimizer, CPU RNG, and CUDA RNG state, and advances the reference trajectory. A restored model/optimizer repeats the next randomly generated batch and step. A separate control skips RNG restoration to show why state completeness matters. Comparisons check both next-batch identity and parameter results.

## Practice

Given an uninterrupted six-step run, save after step three including RNG, optimizer, scheduler, and sampler cursor. Change to restoring weights alone and repeat steps four to six. Expected observation: losses or parameters diverge because Adam moments and random/data state changed; a complete restore matches the declared exact or tolerance-based criterion.

Use Lab 24 to compare uninterrupted and interrupted/resumed trajectories.

Run the supplied deterministic case without adding asynchronous data loading or nondeterministic operations. Those additions require a broader checkpoint contract and new controls.

```bash
umask 077
python labs/24_checkpoint_resume.py --help
sbatch slurm/single_gpu.sbatch labs/24_checkpoint_resume.py --profile smoke
```

## Check your results

Require exact next-batch generation, exact next update, and divergence when RNG restoration is omitted. Inspect reference/resumed loss, `max_parameter_error`, and `checkpoint_state`. A successful serialization call alone does not prove any of these properties.

Record checkpoint manifest, step, state digests, next-batch identity, next loss, gradients, and parameters.

Resume equivalence is proven at the next update boundary, not by successful deserialization alone.

## Investigate the behavior

Name the state that determines the next operation in your own training loop: scheduler, scaler, sampler position, accumulation progress, and data cursor may matter. Which are absent because this lab intentionally uses a smaller system?

Frequent checkpoints reduce recovery loss but consume I/O, storage, and synchronization time. Asynchronous checkpointing shortens exposed time but adds consistency and lifetime complexity.

## If something goes wrong

A different next batch points to RNG or data-position restoration. If inputs match but parameters differ, investigate optimizer/model state or nondeterministic execution. Diagnose the earliest divergence rather than loosening exact equality automatically.

Saving mid-accumulation without accumulated gradients silently changes the next optimizer step.

## Takeaways and next step

Checkpoint correctness is a reproduced state transition, not merely a saved file. Extend this proof to a private disk checkpoint and fresh process, then add distributed shards and data-loader position as separately verified responsibilities.

Checkpoint only at a declared boundary or persist every partial state needed for that boundary.

List the state required for exact data order and exact numerical continuation.
