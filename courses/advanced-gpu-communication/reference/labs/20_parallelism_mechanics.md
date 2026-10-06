# Lab 20: Follow pipeline and context partitions through backward

Pipeline parallelism divides layers, while context parallelism divides sequence positions. This lab demonstrates their ownership and gradient mechanics on two ranks with deliberately small computations. You will trace forward activations, returning gradients, and global normalization without mistaking a one-microbatch pipeline or a partitioned statistic for a production transformer-parallel runtime.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Pass the two-node preflight and review matrix gradients and all-gather/all-reduce semantics. Tensor and expert parallelism have separate training labs; this one focuses on pipeline and context partitions.

## Concepts and code path

The pipeline example sends the first linear layer's activation to rank one, computes the second layer and loss, then returns the activation gradient to rank zero for backward. A full local reference validates gradients. The context example partitions sequence positions, reduces a square-sum statistic, gathers shards for reconstruction, and compares gradients under global normalization. It does not implement distributed attention.

## Practice

`labs/20_parallelism_mechanics.py` demonstrates two-stage activation/gradient exchange and sequence partitioning across two ranks. It checks losses, reconstruction, and gradients against unpartitioned references and writes tensor shapes, communication sizes, and correctness results.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/20_parallelism_mechanics/logs/%j.out" \
  --error="$PWD/results/20_parallelism_mechanics/logs/%j.err" \
  slurm/20_parallelism_mechanics.sbatch --workload small
```

## Check your results

Each new job owns `results/20_parallelism_mechanics/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/20_parallelism_mechanics/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/20_parallelism_mechanics/logs/$LAB_JOB_ID.out"
cat "results/20_parallelism_mechanics/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require pipeline forward/backward reference agreement and context partition/gradient agreement at FP32 `rtol=1e-5, atol=1e-6`. Inspect activation-send/gradient-return bytes, global/local shapes, one microbatch, and listed collectives.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Pipeline parallel / stages | `pipeline_parallel.stages` | `none` |
| Pipeline parallel / microbatches | `pipeline_parallel.microbatches` | `none` |
| Pipeline parallel / activation send bytes | `pipeline_parallel.activation_send_bytes` | `bytes` |
| Context parallel / sequence fraction per rank | `context_parallel.sequence_fraction_per_rank` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 20_parallelism_mechanics --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 20_parallelism_mechanics \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the paired mechanics through the two-node launcher. The output is primarily correctness and communication accounting; there is no multi-microbatch pipeline efficiency benchmark to tune here.

```bash
"$COURSE_PYTHON" labs/20_parallelism_mechanics.py --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/20_parallelism_mechanics/logs/%j.out" \
  --error="$PWD/results/20_parallelism_mechanics/logs/%j.err" slurm/20_parallelism_mechanics.sbatch --workload small
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Draw the forward and backward arrows between pipeline stages. Why must the second stage treat the received activation as differentiable? For context partitioning, explain why normalizing each local sum by local size would change the intended global gradient.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/20_parallelism_mechanics/logs/%j.out" \
  --error="$PWD/results/20_parallelism_mechanics/logs/%j.err" slurm/20_parallelism_mechanics.nsys.sbatch --workload small
```

The native Systems command is in `slurm/20_parallelism_mechanics.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Open both rank reports. Expand lab_workload, CUDA streams and NCCL send/receive/all-gather rows. Identify pipeline bubbles and context exchange, then check the numerical-equivalence panels before proposing an overlap change. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A send/receive shape or ordering mismatch can block both stages. Correct reconstruction with wrong gradients points toward normalization or partition indexing. Diagnose those independently rather than treating a successful gather as complete correctness.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Parallelism begins with explicit state and tensor ownership. Production pipeline schedules and context-parallel attention are extensions requiring new communication, masking, and reference tests; this lab does not establish their throughput.
