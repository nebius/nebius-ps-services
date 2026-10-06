# Lab 10: Qualify an optional thread-block-cluster launch

Hopper thread-block clusters introduce an additional execution grouping beyond independent blocks. This optional lab checks that the declared cluster launch can run and produce the expected block IDs. You will learn the toolchain and launch boundary without confusing a successful probe with an implementation of TMA, distributed shared-memory reuse, or a demonstrated performance optimization.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

CUDA preparation compiles an isolated SM90a target and records its CUDA toolkit
and build path. This optional lab qualifies the bounded cluster launch from
that isolated build. Its native launcher loads the prepared target automatically; no
interactive allocation or additional configure/build/export block is required.

## Concepts and code path

The required build targets SM90. Enabling `COURSE_ENABLE_SM90A=ON` adds the optional cluster probe in its own SM90a target while required binaries remain SM90. This is a packaging policy; the probe does not demonstrate an architecture-accelerated instruction.

The host requests four blocks grouped into two-block clusters using an extended launch configuration. Thread zero of each block writes that block's index to its output slot. The host checks all four values and records repeated probe timing. No cross-block shared-memory operation or TMA transfer is implemented. The course's SM90a gate is a packaging choice, not a claim that every cluster API intrinsically requires SM90a.

Given four blocks that independently load one shared input tile, a cluster design may load once and share through distributed shared memory. Change to the cluster path only after supported placement and synchronization are proven. Expected observation: HBM traffic may fall while active-cluster count, remote-shared latency, descriptor overhead, and `sm_90a` portability can offset the benefit.

Enable Lab 10 only with the documented toolkit and isolated SM90a build profile. The supplied program is a cluster-launch probe: four blocks in two-block clusters write and validate block indices. It does not implement TMA, distributed shared-memory reuse, or a portable timing baseline. For an advanced extension, first add a correct ordinary-block reference, then one cluster-shared or TMA operation with its required synchronization, correctness checks, resource report and timing. The course's SM90a gate is a packaging policy for optional code, not a claim that every cluster-launch API intrinsically requires architecture-accelerated instructions.

## Practice

`labs/10_hopper_cluster.cu` launches four CUDA blocks in two-block clusters, checks each block writes its expected identity, and records launch timing and architecture details. It demonstrates cluster launch, not distributed shared-memory communication. The baseline launcher records its output as course result JSON.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/10_hopper_cluster/logs/%j.out" \
  --error="$PWD/results/10_hopper_cluster/logs/%j.err" \
  slurm/10_hopper_cluster.sbatch --workload small
```

## Check your results

Each new job owns `results/10_hopper_cluster/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/10_hopper_cluster/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/10_hopper_cluster/logs/$LAB_JOB_ID.out"
cat "results/10_hopper_cluster/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require exact block-index outputs, the two-block cluster declaration, and the optional architecture identity. A successful launch validates only this probe. There is no ordinary-block baseline or useful-work speedup comparison.

Retain compiler/toolkit identity, architecture flags, launch attributes, cluster shape, correctness, resources, and time.

Passing this lab confirms the tested cluster launch and block-index outputs only; it does not establish TMA behavior, a speedup or a portable default.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Kernel median (seconds) | `kernel_median_ms` | `s` |
| Cluster blocks | `cluster_blocks` | `none` |
| Elements | `elements` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 10_hopper_cluster --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 10_hopper_cluster \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

After setup compiles the isolated SM90a target, run the probe and memory check with the same qualified toolkit and build directory.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/10_hopper_cluster/logs/%j.out" \
  --error="$PWD/results/10_hopper_cluster/logs/%j.err" slurm/10_hopper_cluster.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/10_hopper_cluster/logs/%j.out" \
  --error="$PWD/results/10_hopper_cluster/logs/%j.err" slurm/10_hopper_cluster.sanitizer.sbatch memcheck --workload small
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Explain how block grouping differs from a two-node distributed job. Which synchronization and ownership rules would be needed before one block reads another's shared data? Which would be needed for an asynchronous TMA transfer?

Offloaded copies save SM instructions/register address work but add descriptor and barrier complexity. Clusters enlarge cooperation scope while reducing scheduling flexibility and possibly occupancy. L2 may be simpler and faster.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/10_hopper_cluster/logs/%j.out" \
  --error="$PWD/results/10_hopper_cluster/logs/%j.err" slurm/10_hopper_cluster.nsys.sbatch --workload small
```

The native Systems command is in `slurm/10_hopper_cluster.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker; submission and result publication remain outside capture. Expand course_measure and CUDA GPU kernel rows. Locate the cluster kernel and inspect launch gaps and its device duration. Check cluster capability and numerical correctness first; a capture does not prove a cluster-launch speedup. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Unsupported launch attributes or toolchain mismatches leave the optional exercise pending. Do not silently remove cluster attributes to make an ordinary launch pass under the same label.

Avoid shipping an `sm_90a`-dependent binary as though it were forward-compatible.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Advanced hardware features need separately scoped activation and correctness evidence. Add one real cluster-shared or TMA operation only after defining a portable reference, synchronization contract, resource report, and the same timed operations.

Keep the advanced target isolated and provide a correct portable path.

State the compile-, launch-, and runtime gates for the optional lab.
