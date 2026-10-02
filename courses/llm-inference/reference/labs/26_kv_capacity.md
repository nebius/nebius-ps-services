# Lab 26: Calculate ideal KV storage for MHA, GQA, and MQA

KV-cache capacity depends on stored key/value heads, not simply the number of query heads. This lab calculates ideal storage for multi-head, grouped-query, and multi-query attention and checks a small tensor allocation's byte arithmetic. You will learn to estimate whether a proposed workload is plausible before allocating it, while keeping logical tensor bytes distinct from full engine memory.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 and review the KV formula. The declared model has 32 layers, 32 query heads, head dimension 128, and two-byte elements. Sequence length and request concurrency are configurable positive values.

Use the actual H100 HBM capacity and selected KV dtype. FP8 KV support and kernels are engine/version-specific; do not infer support from hardware format capability alone.

## Concepts and code path

The script computes bytes per sequence position as `2 * layers * kv_heads * head_dim * dtype_bytes`, where the leading two accounts for K and V. It scales that value by sequence and concurrency for each head layout. A one-token GQA tensor probe checks logical byte accounting; it does not allocate every full configuration.

Given 32 layers, 8 KV heads, head dimension 128, BF16 K/V, ideal KV is `32×8×128×2×2 = 131,072` bytes or 128 KiB per token. At 8,192 tokens one request uses about 1 GiB ideal KV. Change from 32 KV heads to 8 with the same query heads. Expected observation: ideal KV drops fourfold, but measured free capacity is lower after weights, block rounding, workspaces, graph pools, and reserve.

## Practice

`labs/26_kv_capacity.py` calculates ideal KV-cache sizes for multi-head, grouped-query, and multi-query attention. A real CUDA BF16 tensor checks the byte formula; JSON reports capacity estimates and explicitly excluded runtime overhead.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/26_kv_capacity/logs/%j.out" \
  --error="$PWD/results/26_kv_capacity/logs/%j.err" \
  slurm/26_kv_capacity.sbatch --workload small --sequence 4096 --concurrency 8
```

## Check your results

Each new job owns `results/26_kv_capacity/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/26_kv_capacity/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/26_kv_capacity/logs/$LAB_JOB_ID.out"
cat "results/26_kv_capacity/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `probe_bytes_match`. Inspect each case's KV-head count, bytes per token, and ideal GiB, plus the list of excluded runtime overheads. Ideal capacity excludes weights, workspaces, allocator reservation, fragmentation, and engine metadata.

Retain formula inputs, bytes/token, sequence lengths, concurrency, block overhead, and observed memory.

Capacity planning must reserve weights, temporaries, graph pools, fragmentation, and runtime headroom in addition to ideal KV bytes.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Cases / mha / ideal gib | `cases.mha.ideal_gib` | `bytes` |
| Cases / gqa / ideal gib | `cases.gqa.ideal_gib` | `bytes` |
| Cases / mqa / ideal gib | `cases.mqa.ideal_gib` | `bytes` |
| Concurrency | `concurrency` | `none` |
| Sequence | `sequence` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 26_kv_capacity \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Compare the selected artifact fields and experiment timestamps. This dashboard omits GPU telemetry because this recipe cannot attribute device activity to its result.

## Investigate the behavior

### Workload variations

Vary sequence length or concurrency independently. These commands change arithmetic estimates, not the size of a fully materialized production cache for every case.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/26_kv_capacity/logs/%j.out" \
  --error="$PWD/results/26_kv_capacity/logs/%j.err" slurm/26_kv_capacity.sbatch --workload small --sequence 4096 --concurrency 8
sbatch --chdir="$PWD" \
  --output="$PWD/results/26_kv_capacity/logs/%j.out" \
  --error="$PWD/results/26_kv_capacity/logs/%j.err" slurm/26_kv_capacity.sbatch --workload small --sequence 8192 --concurrency 8
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Explain why doubling context doubles ideal KV bytes and why reducing KV heads can lower storage without reducing query-head count. Which terms must be adjusted for quantized KV and its scale metadata?

GQA/MQA and lower KV precision increase capacity but can change architecture or quality. Reserving a larger KV pool raises admission capacity while leaving less workspace/headroom and increasing the cost of cache pressure.

**Nsight Systems: not applicable.** This capacity worksheet calculates bytes and token limits; it does not allocate a GPU KV cache. Inspect the measured or modeled fields in this lab's dashboard; retain the artifact and its stated scope.

## If something goes wrong

An estimate larger than available memory is a reason to reduce the declared workload before allocation. Check GiB versus GB and the K/V factor before treating an unexpected number as an engine defect.

Avoid using query-head count instead of KV-head count for grouped-query models.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Capacity arithmetic is a planning tool, not a measured fit result. Extend with safe bounded allocations and baseline/peak allocator readings, then validate a real engine's reservation and admission behavior separately.

Compute ideal KV first, then add allocator and engine overhead from measurement.

Derive KV bytes/token for a model with 32 layers, 8 KV heads, head size 128, and BF16 storage.

The supplied experiment calculates ideal MHA/GQA/MQA storage and probes one-token GQA logical bytes; it does not measure full-cache allocator usage for every layout. Extension: allocate K/V tensors for each declared layer/head/sequence/concurrency configuration, synchronize and record allocated/reserved memory before and after, and separate tensor bytes from allocator and workspace overhead. Reduce the declared workload before allocation if its estimate exceeds available memory.
