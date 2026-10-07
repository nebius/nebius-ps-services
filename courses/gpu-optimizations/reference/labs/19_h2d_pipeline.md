# Lab 19: Overlap H2D copies with a bounded input ring

A nonblocking copy can free the CPU while the GPU still executes copies and kernels sequentially. This lab separates those two effects. It compares a single-stream input path with a copy/compute pipeline using the same pinned buffers, deterministic batches and dense matrix multiplications. You will establish the dependencies that make reuse safe, measure the complete loop, and use a trace to determine whether independent work actually overlaps.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use one full H100 with the course's qualified PyTorch environment. No datasets, model downloads or storage access are required. The small profile uses 512-square FP32 matrices; large uses 2048-square matrices. The defaults use two slots and sixteen batches. Keep CPU and pinned-memory consumption within your allocation; eight slots is an explicit upper bound, not a recommended setting.

## Concepts and code path

`run_pipeline` allocates one pinned host matrix, device input and output per slot, plus a shared dense weight matrix. Each batch has a different exactly representable value. A matrix filled with 1/width preserves that value after multiplication because the width is a power of two. Every output element must match, catching stale or incompletely transferred data. This is a deliberately controlled dense-compute workload, not a representative neural network.

Before filling a used slot, the host waits for its previous compute-complete event. H2D records readiness; the compute stream waits for it, performs the GEMMs and exact check, then records completion. Serial mode also copies on the compute stream; pipeline mode changes only copy-stream placement. Initial setup is synchronized before timing. The forward-only loop uses `torch.inference_mode()` to avoid autograd recording; explicit events and output checks still establish completion and correctness. PyTorch `record_stream()` protects allocator reuse, not application overwrites or producer-consumer ordering.

As a schematic example, assume each batch needs 3 ms of H2D and 5 ms of compute, with no shared-resource contention. Four serialized batches take 32 ms. With two safe slots, copy batch 1 while computing batch 0, and continue until the fourth compute finishes: ideal elapsed time is 3 + 4 × 5 = 23 ms, including fill and drain. These are illustrative assumptions, not H100 measurements. If the CPU takes 10 ms to prepare every batch, the transfer schedule cannot remove that producer limit.

Run the bounded input-ring experiment. It reports a joined whole-loop duration rather than separate serialized phases. Compare serial and pipeline modes with two slots, then use one slot as an overlap-negative control. The data is generated in memory; no storage or DataLoader worker performance is claimed.

## Practice

`labs/19_h2d_pipeline.py` runs a serial or bounded pipelined host-to-device workload containing copies and matrix multiplications. It checks every batch against its exact reference and writes whole-loop timings, pool size, and pipeline configuration.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/19_h2d_pipeline/logs/%j.out" \
  --error="$PWD/results/19_h2d_pipeline/logs/%j.err" \
  slurm/19_h2d_pipeline.sbatch --mode serial --slots 2
```

## Check your results

Each new job owns `results/19_h2d_pipeline/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/19_h2d_pipeline/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/19_h2d_pipeline/logs/$LAB_JOB_ID.out"
cat "results/19_h2d_pipeline/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `every_batch_matches_exact_reference` and `pipeline_drained`. `whole_loop_samples_ms` and the min/median/p90 summary include host fills, copies, GEMMs, device checks, slot waits and final drain. They exclude initial allocations. `pinned_pool_bytes` is the explicit input pool, not total host memory or allocator reserve. With small and two slots, it is 2 × 512 × 512 × 4 = 2,097,152 bytes; this is allocation arithmetic, not measured RSS.

Retain the batch count and GEMMs per batch alongside the reported samples and pool size. Inspect `slot_reuse_wait` and `pipeline_drain` for ownership stalls; use the copy/compute correlation described below to assess overlap.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Whole loop / median (seconds) | `whole_loop.median_ms` | `s` |
| Pinned pool bytes | `pinned_pool_bytes` | `bytes` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 19_h2d_pipeline --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 19_h2d_pipeline \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

From this course directory, compare the same workload in fresh jobs. The JSON result appears in the results directory with a unique run identifier. Repeat each configuration at least three times in independent jobs for acceptance; the iteration samples within one process are not independent trials.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/19_h2d_pipeline/logs/%j.out" \
  --error="$PWD/results/19_h2d_pipeline/logs/%j.err" slurm/19_h2d_pipeline.sbatch --mode serial --slots 2
sbatch --chdir="$PWD" \
  --output="$PWD/results/19_h2d_pipeline/logs/%j.out" \
  --error="$PWD/results/19_h2d_pipeline/logs/%j.err" slurm/19_h2d_pipeline.sbatch --mode pipeline --slots 2
sbatch --chdir="$PWD" \
  --output="$PWD/results/19_h2d_pipeline/logs/%j.out" \
  --error="$PWD/results/19_h2d_pipeline/logs/%j.err" slurm/19_h2d_pipeline.sbatch --mode pipeline --slots 1
```

After unprofiled timing, capture short diagnostic runs separately. The existing launcher produces private Nsight reports and summary text. Inspect the `h2d_loop` interval, excluding allocation and initialization before it.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/19_h2d_pipeline/logs/%j.out" \
  --error="$PWD/results/19_h2d_pipeline/logs/%j.err" slurm/19_h2d_pipeline.nsys.sbatch --mode pipeline --slots 2 --warmup 1 --iterations 2
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Follow `h2d_submit` and `consume_batch` to their corresponding device rows. The copy for a later batch may intersect a current batch's GEMM; the kernel consuming a batch must follow its own copy. One slot deliberately removes this runway. Increase only `--work` to change compute/transfer balance, then only `--slots` to examine buffering costs. More streams or a continuous kernel row does not establish full SM utilization.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/19_h2d_pipeline/logs/%j.out" \
  --error="$PWD/results/19_h2d_pipeline/logs/%j.err" slurm/19_h2d_pipeline.nsys.sbatch --mode serial --slots 2
```

The native Systems command is in `slurm/19_h2d_pipeline.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. Select `consume_batch` to skip weight initialization and capture the first batch GEMM. The launch-count limit applies after the range and kernel-name filters. With the default warmup, this GEMM belongs to the first warmup pipeline call. Verify its kernel name and range in the report; these counters describe one diagnostic GEMM, not a measured whole-loop sample or proof of copy/compute overlap. Use Systems to inspect the full pipeline.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/19_h2d_pipeline/logs/%j.out" \
  --error="$PWD/results/19_h2d_pipeline/logs/%j.err" slurm/19_h2d_pipeline.ncu.sbatch --mode serial --slots 2
```

The native Compute command is in `slurm/19_h2d_pipeline.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Use `--mode`, `--slots`, `--batches`, `--work` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `batches` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A failed reference is a data-integrity failure, even when the run appears faster. Check both ready and reuse dependencies before timing again. Allocation failure requires smaller bounded resources, not unlimited pinning. A pipeline slower than serial is a valid result for small work or contention. CPU fill may become the bottleneck; this lab does not measure loader workers or disk latency.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Host asynchrony, producer prefetch and device overlap are separate mechanisms. A complete answer identifies the dependency that protects each buffer, the overlap visible in a trace, and the joined-loop result. Transfer this design to a real loader only after preserving batch IDs and pinning lifetime. Then study Lab 20's reverse direction, where the CPU must wait before reading a transferred output.
