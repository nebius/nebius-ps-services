# Lab 03: Measure pageable and pinned host transfers

Pinned memory can help the GPU transfer data directly, but allocating pinned buffers and requesting a nonblocking copy do not automatically overlap transfers with computation. This lab measures four host-to-device copy combinations using a CPU timer that includes waiting for each copy to finish. You will learn what these timings establish and what still requires a timeline experiment.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use one H100 and sufficient host memory for both pageable and pinned buffers. Pinning consumes a limited host resource. The default small transfer is 64 MiB; use the size override for a bounded comparison.

H100 can execute copies and compute concurrently under supported paths, but the CPU, PCIe/fabric topology, pinning limits, and framework stream semantics remain part of the experiment.

## Concepts and code path

The program creates one pageable input, copies its contents into a pinned allocation, and reuses a GPU destination. It compares blocking and nonblocking submission for each source type. Every measured copy is followed by device synchronization, so the host duration includes completion. The final exact-copy check validates the destination contents; the lab does not run a producer/consumer pipeline.

## Practice

`labs/03_transfer_and_pinning.py` measures host-to-device copies using pageable or pinned memory with blocking or nonblocking submission. It writes copy latency and effective bandwidth and checks the copied data exactly matches its source.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_transfer_and_pinning/logs/%j.out" \
  --error="$PWD/results/03_transfer_and_pinning/logs/%j.err" \
  slurm/03_transfer_and_pinning.sbatch --workload small
```

## Check your results

Each new job owns `results/03_transfer_and_pinning/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/03_transfer_and_pinning/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/03_transfer_and_pinning/logs/$LAB_JOB_ID.out"
cat "results/03_transfer_and_pinning/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `exact_copy` and inspect all four `modes`, their median milliseconds, and effective GiB/s. The rate counts payload bytes in one direction. It is not HBM bandwidth and does not establish simultaneous copy and compute.

For the later overlap extension, keep a timeline, CUDA-event measurements, end-to-end wall time, transfer sizes, and dependency description.

Overlap is proven when the timeline and end-to-end time show concurrent useful work, not when two API calls were issued on different streams.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Modes / case / median (seconds) | `modes.*.median_ms` | `s` |
| Modes / case / effective gib per s | `modes.*.effective_gib_per_s` | `Bps` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 03_transfer_and_pinning --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 03_transfer_and_pinning \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Compare two transfer sizes in separate jobs. Allocation and initial pinning are outside the copy timing, so record that exclusion when relating results to your application.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_transfer_and_pinning/logs/%j.out" \
  --error="$PWD/results/03_transfer_and_pinning/logs/%j.err" slurm/03_transfer_and_pinning.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_transfer_and_pinning/logs/%j.out" \
  --error="$PWD/results/03_transfer_and_pinning/logs/%j.err" slurm/03_transfer_and_pinning.sbatch --workload small --size-mib 128
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Which costs are excluded by reusing the buffers? Why can `non_blocking=True` have little effect when the host immediately waits? Compare the size trend before assuming a hardware link has reached its practical limit.

Pinned memory enables asynchronous host/device transfers and can improve bandwidth, but consumes a scarce OS resource and can hurt system behavior if overused. More streams increase possible overlap while making lifetime, ordering, and debugging more complex.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_transfer_and_pinning/logs/%j.out" \
  --error="$PWD/results/03_transfer_and_pinning/logs/%j.err" slurm/03_transfer_and_pinning.nsys.sbatch --workload small
```

The native Systems command is in `slurm/03_transfer_and_pinning.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

This is a copy-only experiment. Compute kernel counters are inapplicable; use Systems memcpy rows and the completed transfer timings.

Guided comparison: Use `--size-mib` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `size_mib` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Pinned-allocation failure may reflect host limits rather than insufficient GPU memory. Stop or reduce the declared allocation. If copies appear implausibly fast, confirm the timer includes synchronization and the intended number of bytes.

In a later overlap experiment, synchronizing after every operation would serialize the schedule. Retain the supplied per-copy waits here: they are required to measure completed transfers.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Pinning is an enabling mechanism, not an overlap guarantee. Next, use Lab 07 to reason about independent streams; a real input pipeline additionally needs safe buffer lifetime, a producer, and an explicit consumer dependency.

For the later end-to-end overlap experiment, place events around device work and use one final synchronization.

Identify every dependency that prevents two operations from overlapping.
