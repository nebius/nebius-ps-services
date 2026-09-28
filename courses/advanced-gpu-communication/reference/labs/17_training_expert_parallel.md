# Lab 17: Trace expert routing, gradients, and grouped work

Mixture-of-experts training distributes tokens to selected expert computations, then must return outputs and gradients to the correct owners. This lab implements a bounded two-rank example with explicit routing and reference checks. You will connect expert load imbalance to communication and grouped computation while distinguishing a teaching implementation from a production MoE runtime.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/17_training_expert_parallel.json).

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Pass the two-node preflight and review all-to-all exchange and gradient flow. Exactly two ranks are required. Keep the Training environment separate from the similarly named Inference mechanics lab.

The two-node course lab illustrates bounded TP/PP/CP/EP mechanics only. Production parallel groups generally require more GPUs and deliberate intra-/inter-node placement following the qualified Megatron Core stack.

## Concepts and code path

The source creates deterministic token assignments, exchanges tokens to expert owners, computes local expert outputs, and returns data to originating ranks. Its training path also returns token gradients, verifies expert weight gradients, and checks an optimizer update. A local comparison pads expert batches for grouped `bmm` and compares them with an expert loop; padded grouped work is not a production grouped-GEMM kernel.

Given eight experts on two ranks, suppose four heavily used experts currently reside on rank zero. Change physical placement by moving two of those existing experts to rank one while preserving each token's selected expert ID, routing weight, and expert parameters. Compare output and gradient equivalence, expert counts, transfer volume, and slowest-rank time. Expected observation: physical placement can rebalance work without changing the model. Changing router choices, dropping tokens, or adding an auxiliary balancing loss is instead a model/recipe change requiring quality validation; it is not an equivalent-computation optimization.

Run Labs 17, 18, and 20 and label every tensor shape and collective direction.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run the small case through the two-node launcher. Inspect all correctness gates before increasing workload size or using timing to reason about load balance.

```bash
umask 077
python3 tools/submit_lab.py --lab 17_training_expert_parallel slurm/training_two_rank.sbatch labs/17_training_expert_parallel.py --profile small
python3 tools/submit_lab.py --lab 17_training_expert_parallel slurm/training_two_rank.sbatch labs/17_training_expert_parallel.py --profile large
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 17_training_expert_parallel --job "$LAB_JOB_ID"
```

Require routed output, token-gradient, expert-gradient, optimizer-update, and grouped-output agreement with the supplied references. Inspect `global_expert_token_load`, max-to-mean load, grouped versus loop timing, communication phases, and slowest-rank step time.

Record state placement, input/output shapes, bytes communicated, step time, expert loads, and numerical equivalence.

The right strategy follows the state that does not fit and the communication the topology can support.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Loop expert gemm median (seconds) | `loop_expert_gemm_median_ms` | `s` |
| Padded grouped bmm median (seconds) | `padded_grouped_bmm_median_ms` | `s` |
| Training step slowest rank median (seconds) | `training_step_slowest_rank_median_ms` | `s` |
| Forward all to all median (seconds) | `forward_all_to_all_median_ms` | `s` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 17_training_expert_parallel \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Trace one token's outward and return routes. Why must its gradient follow the inverse mapping? Compare the busiest expert's token count with the mean across experts, and account for extra padding before interpreting grouped-operation performance.

TP adds latency-sensitive collectives per layer; PP adds bubbles and stage imbalance; CP adds attention communication; EP adds routing and variable expert load. Combining them can fit larger models but multiplies configuration and checkpoint complexity.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 17_training_expert_parallel --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/training_two_rank.sbatch labs/17_training_expert_parallel.py --profile small
```

Check exported statistics for every rank, then open representative reports from each worker in Systems. Load large reports in small groups and close them between comparisons. Expand NVTX, CUDA, and NCCL kernel rows. Align step/collective boundaries and compare each rank’s arrival, waiting, and compute intervals. A rank-local trace alone cannot establish communication overlap across the job. Compute replay is inapplicable to the live collective; isolate a local kernel before inspecting counters.

Guided comparison: Compare per-expert token load with the slowest-rank completion. Independently identify the most loaded expert and propose one routing/batching change while retaining token conservation and reference output.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Incorrect split counts or permutations can produce shape errors or silently misassigned tokens. Check the routing reference first. A waiting rank may be blocked by a mismatched collective sequence rather than slow expert arithmetic.

Avoid combining parallelism dimensions before measuring the simplest one that solves capacity.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

EP performance depends on routing, balance, communication, and expert kernels together. Preserve these correctness boundaries when extending the example; two participating ranks on the reserved sixteen-GPU cluster demonstrate mechanics, not production MoE scalability.

Start with data parallelism and add only the dimension required by fit, sequence, depth, or sparse experts.

Give one capacity problem solved by each parallelism family.
