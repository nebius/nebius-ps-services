# Lab 15: Distinguish task imbalance from partial grid waves

A long final task can delay completion even when most work has finished, while a partial grid wave can leave SM capacity unused. This lab explores those effects separately with concurrent GEMMs and a Triton wave probe. You will learn why approximate work matching and a scheduling model are useful for diagnosis but insufficient for a fixed-work optimization claim.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use one H100 with the course-qualified Triton compiler. Review task makespan—the time until all tasks finish—and the difference between task imbalance, warp divergence, and a grid tail.

Calculate waves from the measured SM90 resource-limited residency, not only total SM count. Persistent or library kernels can have scheduling behavior that a simple block-wave model does not fully describe.

## Concepts and code path

Eight streams execute balanced or skewed GEMM sizes. The script reports task durations, joined makespan, and modeled FLOPs. Work is matched within two percent, not exactly, and matrices differ. A separate Triton probe varies block counts around estimated resident capacity and validates every program's output. This repeats Fundamentals Lab 11's wave probe as an explicit control: its purpose here is to distinguish a uniform-grid tail from the new balanced-versus-skewed eight-stream workload. Reuse the earlier wave model rather than treating it as a new technique. Neither part is a direct divergent-branch experiment.

Given capacity for 240 concurrent blocks, 241 uniform blocks require a nearly empty second wave. Change the grid to 480 smaller blocks while preserving the result and the same total useful work, if the algorithm permits. Expected observation: under the same 240-block residency limit, the grid fills two waves; whether it finishes sooner still depends on per-block overhead and actual scheduling. A divergent-warp control would show different active-lane evidence.

Run Lab 15's balanced/skewed concurrent task survey and separate partial-wave probe. The task sets have modeled GEMM work matched within two percent, not identical matrices or exact work. The grid probe varies total blocks. Keep those limits with the timing and do not describe either comparison as a fixed-work regrouping speedup.

## Practice

`labs/15_tail_load_balance.py` compares balanced and skewed work across eight CUDA streams and measures separate Triton partial-wave cases. It checks approximately matched work and valid outputs, then reports timing distributions and estimated residency.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/15_tail_load_balance/logs/%j.out" \
  --error="$PWD/results/15_tail_load_balance/logs/%j.err" \
  slurm/15_tail_load_balance.sbatch --workload small
```

## Check your results

Each new job owns `results/15_tail_load_balance/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/15_tail_load_balance/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/15_tail_load_balance/logs/$LAB_JOB_ID.out"
cat "results/15_tail_load_balance/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require the work-ratio gate, finite task outputs, and reference-matching partial-wave outputs. Inspect `makespan_distribution`, `task_duration_distribution`, `skewed_to_balanced_modeled_work_ratio`, and `partial_wave_probe`. GEMM finiteness alone is not numerical reference equivalence.

Record per-unit work, completion distribution, wave count, p50/p90/p99, and total time. The script reports these percentiles from the retained summaries: with the default twenty makespan or probe samples, nearest-rank p99 equals the maximum. This is a small-sample diagnostic, not a stable tail-latency estimate; stronger tail claims require a separately designed collection with enough observations.

Fix the specific imbalance owner: data partition, task grouping, launch geometry, or synchronization.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Balanced / makespan / median (seconds) | `balanced.makespan.median_ms` | `s` |
| Skewed / makespan / median (seconds) | `skewed.makespan.median_ms` | `s` |
| Partial wave probe / cases / case / completion distribution / p50 (seconds) | `partial_wave_probe.cases.*.completion_distribution.p50_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 15_tail_load_balance --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 15_tail_load_balance \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run both task sets and the wave probe together. Keep the declared work ratio in your report rather than describing the candidate as identical useful work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/15_tail_load_balance/logs/%j.out" \
  --error="$PWD/results/15_tail_load_balance/logs/%j.err" slurm/15_tail_load_balance.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/15_tail_load_balance/logs/%j.out" \
  --error="$PWD/results/15_tail_load_balance/logs/%j.err" slurm/15_tail_load_balance.sbatch --workload large
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Compare the longest task with the median task and the joined completion time. Does the tail model predict the observed transition? Use a profiler to confirm actual concurrent execution and residency before interpreting the model as hardware fact.

Sorting/grouping work reduces divergence but adds preprocessing and can hurt locality. Splitting heavy tasks improves balance but adds launches/atomics. Padding a grid adds useless work. Dynamic scheduling improves balance with queue-management cost.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/15_tail_load_balance/logs/%j.out" \
  --error="$PWD/results/15_tail_load_balance/logs/%j.err" slurm/15_tail_load_balance.nsys.sbatch --workload small
```

The native Systems command is in `slurm/15_tail_load_balance.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For the partial-wave control, the Compute command selects `uniform_tail_probe` inside `tail_measure`. This opt-in range wraps each timed probe launch and excludes input initialization, warmup, the one-block compilation probe and sentinel validation. The launch-count limit selects the first measured grid, with `resident_block_slots` blocks. Compare its launch resources and occupancy limits with the source's residency estimate; one capture does not prove scheduling or counters for all four grids or the concurrent GEMM task sets. Inspect those task streams and the remaining grids in Systems.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/15_tail_load_balance/logs/%j.out" \
  --error="$PWD/results/15_tail_load_balance/logs/%j.err" slurm/15_tail_load_balance.ncu.sbatch --workload small
```

The native Compute command is in `slurm/15_tail_load_balance.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare balanced and skewed task sets, preserving the guide's approximate-work caveat. Independently select the bottleneck task and calculate the tail cost; confirm concurrency before attributing it to scheduling.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A single stream occupying most resources can make apparent concurrency ineffective. Missing resource metadata can leave an optimistic estimate: the code retains the thread-limit bound and omits unavailable register/shared-memory constraints. Independent confirmation is then required. Changing total blocks changes total probe work; do not normalize it away without explaining the model.

Increasing occupancy cannot fix one rank receiving twice the work.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

This is a controlled diagnostic survey, not proof of a causal regrouping speedup. An extension should preserve exact inputs and total useful work while changing only scheduling, then include any packing or reordering overhead.

Locate the level where work becomes uneven and rebalance before tuning lower-level resources.

Give separate remedies for warp divergence, a grid tail, and rank skew.
