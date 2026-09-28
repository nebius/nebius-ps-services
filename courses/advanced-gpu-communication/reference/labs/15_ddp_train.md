# Lab 15: Train replicated models with DDP

DistributedDataParallel lets each GPU process different data while keeping replicated model updates coordinated. This lab trains the tiny language model with one rank on each H100 node. You will trace how local forward/backward work relates to gradient communication, global token accounting, and the slowest-rank completion time of the distributed workload.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/15_ddp_train.json).

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Use two verified one-GPU nodes with matching Training environments. Each rank runs a complete model replica; the supplied fixed-shape batches do not exercise unequal valid-token counts across ranks.

Two one-H100 nodes validate API and state/collective mechanics over the measured network. They cannot prove dense NVLink/NVSwitch placement or production scaling guidance.

## Concepts and code path

The launcher starts two workers; shared helpers initialize NCCL and bind each process to its GPU. Each rank builds its tiny model, wraps it in DDP, then constructs AdamW. Backward synchronizes gradients while parameters remain replicated. The program reports aggregated loss, maximum memory and elapsed time.

Given a model whose replicated training state uses 55 GiB per rank and fits one full H100, compare DDP and FSDP2 at equal global valid tokens. Change to a model whose replicated state exceeds device capacity. Expected observation: DDP can win the first case, while FSDP2 becomes a feasibility requirement in the second; transient gather peaks and checkpoint restart still need measurement.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Use the two-node launcher and retain the declared global tokens with each timing. A larger profile changes the workload and should be recorded as a new experiment.

```bash
umask 077
python3 tools/submit_lab.py --lab 15_ddp_train slurm/training_two_rank.sbatch labs/15_ddp_train.py --profile small
python3 tools/submit_lab.py --lab 15_ddp_train slurm/training_two_rank.sbatch labs/15_ddp_train.py --profile large
```

For the guided candidate, run:

```bash
python3 tools/submit_lab.py --lab 15_ddp_train slurm/training_two_rank.sbatch labs/15_ddp_train.py --profile small --zero-grad-fill
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 15_ddp_train --job "$LAB_JOB_ID"
```

Inspect `world_size`, `global_tokens`, `slowest_rank_elapsed_ms`, `final_mean_loss`, and maximum-rank peak allocation. Confirm the finite mean-loss gate. The supplied check does not establish every-gradient or one-update equivalence against a concatenated single-process reference.

Retain per-rank memory, step time, collective volume, exposed communication, loss, and parameter agreement.

Two nodes prove mechanics and bounded tradeoffs, not dense-node or large-scale efficiency.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Slowest rank elapsed (seconds) | `slowest_rank_elapsed_ms` | `s` |
| Final mean loss | `final_mean_loss` | `none` |
| Maximum rank peak allocated memory | `max_rank_peak_allocated_mib` | `bytes` |
| Global tokens | `global_tokens` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 15_ddp_train \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Map local examples to the global objective. For the default averaged-gradient behavior, explain why each rank must scale a summed loss by world size divided by global valid-token count when valid counts differ.

Sharding increases capacity but adds communication, materialization, and checkpoint complexity. DDP is simpler and often faster when the model fits. CPU offload extends capacity at the cost of transfer time and host memory.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 15_ddp_train --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/training_two_rank.sbatch labs/15_ddp_train.py --profile small
```

Check exported statistics for every rank, then open representative reports from each worker in Systems. Load large reports in small groups and close them between comparisons. Expand NVTX, CUDA, and NCCL kernel rows. Align step/collective boundaries and compare each rank’s arrival, waiting, and compute intervals. A rank-local trace alone cannot establish communication overlap across the job. Compute replay is inapplicable to the live collective; isolate a local kernel before inspecting counters.

Guided comparison: Compare the default gradient release with --zero-grad-fill at fixed model, data, seed and update count. Inspect the zero_grad range and complete-step time, require the same finite-loss/update checks, and compare final loss within numerical tolerance. Independently inspect whether clearing traffic or buffer allocation dominates before choosing a policy.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Collective-order mismatches can hang backward even after preflight succeeds. Missing ranks and divergent control flow require private-log inspection. Do not interpret work completed by only one rank as global training progress.

Avoid comparing per-rank batch sizes without holding the global batch fixed.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

DDP coordinates replicas; it does not reduce persistent model-state ownership per rank. Add the course's 100/300-valid-token reference extension before claiming unequal-token correctness, then compare state placement with FSDP2.

Choose DDP when replication fits and simplicity wins; shard when capacity requires it and communication remains acceptable.

Trace one parameter, gradient, and optimizer state through each strategy.

Extension: use 100 and 300 valid labels on the two ranks, all-reduce the count, and multiply each rank's summed loss by world size divided by the global valid-token count. Compare every parameter gradient and one update with a single-process concatenated reference. The supplied fixed-shape baseline does not perform this test.
