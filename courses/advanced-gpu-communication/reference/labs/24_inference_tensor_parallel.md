# Lab 24: Compare inference tensor-partition communication patterns

Tensor parallelism can split a layer's weights across GPUs, but partial outputs must be assembled correctly. This lab demonstrates column- and row-parallel linear inference on two H100 ranks and compares both with a full-weight reference. You will connect conceptual weight-shard capacity with collective communication while keeping those operator mechanics separate from model-level serving claims.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Pass the two-node mechanics preflight and review matrix partitioning. Exactly two ranks are required; both profiles use an evenly divisible hidden width. The experiment retains reference tensors for verification, so it is not a pure memory-minimized deployment. Lab 18 adds backward and gradient checks; this lab isolates the forward-only partitioning and latency question.

This bounded operator lab uses one rank per node on the advanced cluster; it isolates cross-node mechanics. The full eight/sixteen-rank route measures intra-node and inter-node placement separately. If a model fits one H100, two replicas may outperform cross-node TP for aggregate throughput.

## Concepts and code path

Rank zero creates the input and full weight, then broadcasts both so every rank starts with the same values. With inputs shaped `[batch, width]` and PyTorch-style weights `[output_features, input_features]`, the reference is `inputs @ weight.T`. A column-parallel layer splits output features (dimension zero of this stored weight), computes distinct output columns, and concatenates an all-gather list in rank order. A row-parallel layer splits input features and their matching weight columns; all-reduce sums the partial dot products. A correct local slice alone does not prove that the assembled output is correct.

The code separates these two operation paths from measurement and numerical validation. Each path warms up, then records repeated synchronized wall-clock samples with a barrier before each trial. Each sample uses the slowest rank; reported time includes the local matrix operation, collective, and associated host/allocation overhead, but excludes setup broadcasts, the pre-trial barrier, and the final timing-value reduction. It is not isolated network latency. All ranks agree on numerical success before reporting, and process-group cleanup runs on exit. There are no tokenizer, KV-cache, request queue, or streaming-client components.

Given a model using 55 GiB that fits on each H100, route requests to two independent replicas. Change to two-way cross-node TP. Expected observation: TP halves ideal weight ownership but adds collectives to prefill and every decode step; keep it for fit or measured objectives, not because two GPUs exist.

Run the expert and tensor mechanics in Labs 23–24. Compare batch one with the default batch at the same width, validating column reconstruction and row-partial summation. Lab 25 extends the projection to all sixteen ranks. Labs 32–34 then run a real pinned model server and connect cache transfers, routing and client latency to useful serving capacity. Mechanics timing must not be reported as live serving TTFT.

## Practice

`labs/24_inference_tensor_parallel.py` runs forward-only column-sharded linear layers with all-gather and row-sharded layers with all-reduce. It checks both against full-weight outputs and writes shard sizes, payload size, and slowest-rank latency samples.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/24_inference_tensor_parallel/logs/%j.out" \
  --error="$PWD/results/24_inference_tensor_parallel/logs/%j.err" \
  slurm/24_inference_tensor_parallel.sbatch --workload small
```

## Check your results

Each new job owns `results/24_inference_tensor_parallel/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/24_inference_tensor_parallel/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/24_inference_tensor_parallel/logs/$LAB_JOB_ID.out"
cat "results/24_inference_tensor_parallel/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require finite reference/output values and both reference-agreement flags. Column reconstruction additionally uses `rtol=1e-2, atol=1e-2`; both paths must have relative L2 error below 0.02. The row path changes BF16 reduction order, so cancellation near zero makes a pointwise relative comparison misleading; its explicit recipe uses aggregate relative L2 instead. Inspect `maximum_relative_l2` and `maximum_absolute_error` together rather than treating either as proof of model quality.

Inspect per-rank shard bytes, logical collective bytes, the repeated sample lists, and the two path medians. Logical tensor bytes are not measured wire traffic. `model_fit_fraction_per_rank` is a logical weight fraction: this teaching process still holds full reference weights, both partition buffers, and outputs. It does not demonstrate reduced whole-process residency. A real engine also needs KV-cache and workspace memory.

Retain operator-lab weight fractions, communication bytes, and expert token loads separately from live-engine startup, TTFT, throughput, and exported metrics. Do not claim the live engine exposed per-expert load or exact resident model bytes unless those fields are present in its artifacts.

Use the simplest placement that meets capacity and service objectives on the actual topology.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Column parallel all gather median (seconds) | `column_parallel_all_gather_median_ms` | `s` |
| Row parallel all reduce median (seconds) | `row_parallel_all_reduce_median_ms` | `s` |
| Logical collective tensor bytes | `logical_collective_tensor_bytes` | `bytes` |
| Parameter shard bytes per rank | `parameter_shard_bytes_per_rank` | `bytes` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 24_inference_tensor_parallel \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the two partition patterns together using the course launcher. Profiles select width 2,048/batch 32 for small and width 8,192/batch 128 for H100. The optional positive `--batch-size` changes input rows while keeping the selected width fixed. Use batch one as a small-message comparison, not as a simulation of a complete autoregressive decode step. Repeat each comparison in at least three independent jobs with distinct output directories under `results/`, where the result inspector searches, and preserve the topology and workload settings.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/24_inference_tensor_parallel/logs/%j.out" \
  --error="$PWD/results/24_inference_tensor_parallel/logs/%j.err" slurm/24_inference_tensor_parallel.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/24_inference_tensor_parallel/logs/%j.out" \
  --error="$PWD/results/24_inference_tensor_parallel/logs/%j.err" slurm/24_inference_tensor_parallel.sbatch --workload large
sbatch --chdir="$PWD" \
  --output="$PWD/results/24_inference_tensor_parallel/logs/%j.out" \
  --error="$PWD/results/24_inference_tensor_parallel/logs/%j.err" slurm/24_inference_tensor_parallel.sbatch --workload small --batch-size 1 --output-dir results/tp-batch1-run1
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Why does one split concatenate output columns while the other sums partial outputs? Predict what reversing the gathered rank order would do: shape checks would pass, but reference agreement would fail. Which activation dimensions determine communicated bytes? Compare batch one with the default batch at the same width; use sample variability and three independent runs to explain the communication-to-compute ratio. Finally, list the validation-only tensors a deployment could remove and the KV-cache/workspace allocations it would need to add.

Replication uses more weight memory but avoids per-token model-parallel collectives. TP improves fit and sometimes compute scale but adds communication each layer. PP/EP add scheduling and imbalance complexity.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/24_inference_tensor_parallel/logs/%j.out" \
  --error="$PWD/results/24_inference_tensor_parallel/logs/%j.err" slurm/24_inference_tensor_parallel.nsys.sbatch --workload small
```

The native Systems command is in `slurm/24_inference_tensor_parallel.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Check exported statistics for every rank, then open representative reports from each worker in Systems. Load large reports in small groups and close them between comparisons. Expand NVTX, CUDA, and NCCL kernel rows. Align step/collective boundaries and compare each rank’s arrival, waiting, and compute intervals. A rank-local trace alone cannot establish communication overlap across the job. Compute replay is inapplicable to the live collective; isolate a local kernel before inspecting counters.

Guided comparison: Use `--batch-size` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `batch_size` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Wrong reconstruction order can yield correctly shaped but incorrect tensors. Check the common-state broadcasts, shard axes, and rank-ordered concatenation before interpreting timing. Every rank must enter matching collectives in the same order; rank mismatch or incompatible shapes can block even after basic preflight passes. Do not relax a failed tolerance merely to produce a timing result. If batch-one results are noisy, inspect the repeated samples and surrounding host/network activity before attributing the delay to a kernel.

Avoid using cross-node TP for a model that already fits and then generalizing the added latency.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Partitioning trades local weight ownership against communication. Use Labs 32–34 for actual model-server throughput and streaming evidence after qualifying their runtime. This operator lab does not measure time to first token or production scalability.

Prefer replication for throughput when fit allows; partition only to solve a measured fit or scale constraint.

Map the critical communication path for one decode token under TP and EP.
