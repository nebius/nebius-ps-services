# Lab 18: Partition a linear layer and verify its backward pass

Tensor parallelism partitions the computation inside a layer rather than assigning independent examples to complete model replicas. This lab splits a linear operation across two H100 ranks and checks its forward output, gradients, and update against a replicated reference. You will learn which tensor dimensions are local and which results must be communicated to preserve the original computation.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Pass distributed preflight and review matrix multiplication shapes. The hidden width must be divisible by the number of ranks. This is a small operator proof, not a full language-model tensor-parallel framework.

## Concepts and code path

The source creates deterministic weights and inputs, partitions a weight dimension for column-parallel work, and compares local results with reference slices. Backward forms local weight gradients and combines input-gradient contributions. It also demonstrates row-parallel forward reconstruction and checks a simple optimizer update. Reference tensors remain available for verification, so conceptual shard bytes are not the entire resident-memory footprint.

## Practice

`labs/18_training_tensor_parallel.py` partitions linear-layer weights across ranks and checks forward outputs, gradients, and updates against a full-weight reference. It writes parameter-shard sizes, collective payload size, and training/communication timings.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/18_training_tensor_parallel/logs/%j.out" \
  --error="$PWD/results/18_training_tensor_parallel/logs/%j.err" \
  slurm/18_training_tensor_parallel.sbatch --workload small
```

## Check your results

Each new job owns `results/18_training_tensor_parallel/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/18_training_tensor_parallel/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/18_training_tensor_parallel/logs/$LAB_JOB_ID.out"
cat "results/18_training_tensor_parallel/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require column/row forward, weight-gradient, input-gradient, and update agreement. Each compared tensor, norm and relative error must be finite, and every error must be below `0.02`. An invalid comparison becomes a failing infinite error so all ranks can reach the shared MIN verdict described in Lesson 12; no successful result is written if any rank fails. Inspect replicated versus per-rank shard bytes, input-gradient all-reduce bytes, collective time, and slowest-rank training-step time. Do not equate theoretical shard storage with measured whole-model fit.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Training step slowest rank median (seconds) | `training_step_slowest_rank_median_ms` | `s` |
| Input gradient all reduce median (seconds) | `input_gradient_all_reduce_median_ms` | `s` |
| Parameter shard bytes per rank | `parameter_shard_bytes_per_rank` | `bytes` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 18_training_tensor_parallel --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 18_training_tensor_parallel \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run both ranks through the course launcher. Keep the full result so the forward, backward, update, and communication checks remain associated with the same workload.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/18_training_tensor_parallel/logs/%j.out" \
  --error="$PWD/results/18_training_tensor_parallel/logs/%j.err" slurm/18_training_tensor_parallel.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/18_training_tensor_parallel/logs/%j.out" \
  --error="$PWD/results/18_training_tensor_parallel/logs/%j.err" slurm/18_training_tensor_parallel.sbatch --workload large
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Write the shapes of X, each weight shard, local Y, and dX. Why are input-gradient contributions summed for a column split? Which communication moves from backward to forward when the partition orientation changes?

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/18_training_tensor_parallel/logs/%j.out" \
  --error="$PWD/results/18_training_tensor_parallel/logs/%j.err" slurm/18_training_tensor_parallel.nsys.sbatch --workload small
```

The native Systems command is in `slurm/18_training_tensor_parallel.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Check exported statistics for every rank, then open representative reports from each worker in Systems. Load large reports in small groups and close them between comparisons. Expand NVTX, CUDA, and NCCL kernel rows. Align step/collective boundaries and compare each rank’s arrival, waiting, and compute intervals. A rank-local trace alone cannot establish communication overlap across the job. Compute replay is inapplicable to the live collective; isolate a local kernel before inspecting counters.

Guided comparison: Compare the sharded linear result and input gradient with the replicated reference. Independently identify the required collective from the tensor shapes before choosing the partition orientation.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Shape mismatches usually indicate the wrong partition axis or reconstruction order. A correct forward result can still have an incorrect backward collective, so inspect every gradient gate independently.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

TP changes tensor ownership and communication dependencies inside a layer. Carry those invariants into a larger block only after its partitioned result and update match an unpartitioned reference; do not infer production scaling from this two-rank proof.
