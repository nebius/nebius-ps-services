# Lab 23: Route inference tokens to expert owners

Expert-parallel inference sends tokens to the ranks that own their selected experts and returns outputs to the original order. This lab implements that round trip on two H100 nodes and reports expert load balance. You will learn the data-movement mechanism without confusing a tiny routed operator with a complete MoE model or a serving benchmark.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/23_inference_expert_parallel.json).

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Pass the two-node mechanics preflight and review all-to-all routing. Exactly two ranks are required. This Inference lab has no backward or optimizer update; those responsibilities belong to the separate Training EP lab.

## Concepts and code path

The source builds deterministic token routing, exchanges tokens according to split counts, applies toy expert computations, and restores outputs to their origins. A known reference checks assignment and round-trip correctness. Timing covers the bounded routing computation; logical expert parameter fractions describe ownership, not complete measured resident model memory.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run the paired ranks through the supplied launcher. Record workload size and topology with each timing so a changed routing case is not mistaken for a fixed-work engine optimization.

```bash
umask 077
python3 tools/submit_lab.py --lab 23_inference_expert_parallel slurm/two_node.sbatch labs/23_inference_expert_parallel.py --profile small
python3 tools/submit_lab.py --lab 23_inference_expert_parallel slurm/two_node.sbatch labs/23_inference_expert_parallel.py --profile large
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 23_inference_expert_parallel --job "$LAB_JOB_ID"
```

Require every rank's output to match its expected experts. Inspect token send counts, `global_expert_token_load`, max-to-mean imbalance, the parameter-fraction scope, and `round_trip` timing. There are no TTFT or online throughput fields.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Round trip / median (seconds) | `round_trip.median_ms` | `s` |
| Expert load max to mean | `expert_load_max_to_mean` | `none` |
| Toy expert parameter fraction per rank | `toy_expert_parameter_fraction_per_rank` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 23_inference_expert_parallel \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Trace one token through dispatch, expert execution, and return permutation. Which expert determines the longest local workload? Explain why balanced token counts can still hide unequal expert computation costs.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 23_inference_expert_parallel --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/two_node.sbatch labs/23_inference_expert_parallel.py --profile small
```

Check exported statistics for every rank, then open representative reports from each worker in Systems. Load large reports in small groups and close them between comparisons. Expand NVTX, CUDA, and NCCL kernel rows. Align step/collective boundaries and compare each rank’s arrival, waiting, and compute intervals. A rank-local trace alone cannot establish communication overlap across the job. Compute replay is inapplicable to the live collective; isolate a local kernel before inspecting counters.

Guided comparison: Follow dispatch, expert computation, and return permutation on both ranks. Independently identify load imbalance from the busiest expert while requiring output equivalence and token conservation.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Incorrect split counts or inverse permutations can misroute outputs. Diagnose those against the reference before examining latency. A collective timeout can result from disagreement about collective order across ranks rather than a slow network.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

EP performance combines routing cost and expert balance. A real-engine extension needs a separately qualified MoE model, backend and distributed launcher; the dense-model Dynamo labs do not qualify expert-parallel serving, and this mechanics result does not establish production model fit or service performance.
