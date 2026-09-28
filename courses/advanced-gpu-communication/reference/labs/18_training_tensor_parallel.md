# Lab 18: Partition a linear layer and verify its backward pass

Tensor parallelism partitions the computation inside a layer rather than assigning independent examples to complete model replicas. This lab splits a linear operation across two H100 ranks and checks its forward output, gradients, and update against a replicated reference. You will learn which tensor dimensions are local and which results must be communicated to preserve the original computation.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/18_training_tensor_parallel.json).

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Pass distributed preflight and review matrix multiplication shapes. The hidden width must be divisible by the number of ranks. This is a small operator proof, not a full language-model tensor-parallel framework.

## Concepts and code path

The source creates deterministic weights and inputs, partitions a weight dimension for column-parallel work, and compares local results with reference slices. Backward forms local weight gradients and combines input-gradient contributions. It also demonstrates row-parallel forward reconstruction and checks a simple optimizer update. Reference tensors remain available for verification, so conceptual shard bytes are not the entire resident-memory footprint.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run both ranks through the course launcher. Keep the full result so the forward, backward, update, and communication checks remain associated with the same workload.

```bash
umask 077
python3 tools/submit_lab.py --lab 18_training_tensor_parallel slurm/training_two_rank.sbatch labs/18_training_tensor_parallel.py --profile small
python3 tools/submit_lab.py --lab 18_training_tensor_parallel slurm/training_two_rank.sbatch labs/18_training_tensor_parallel.py --profile large
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 18_training_tensor_parallel --job "$LAB_JOB_ID"
```

Require column/row forward, weight-gradient, input-gradient, and update agreement. Each compared tensor, norm and relative error must be finite, and every error must be below `0.02`. An invalid comparison becomes a failing infinite error so all ranks can reach the shared MIN verdict described in Lesson 12; no successful result is written if any rank fails. Inspect replicated versus per-rank shard bytes, input-gradient all-reduce bytes, collective time, and slowest-rank training-step time. Do not equate theoretical shard storage with measured whole-model fit.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Training step slowest rank median (seconds) | `training_step_slowest_rank_median_ms` | `s` |
| Input gradient all reduce median (seconds) | `input_gradient_all_reduce_median_ms` | `s` |
| Parameter shard bytes per rank | `parameter_shard_bytes_per_rank` | `bytes` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 18_training_tensor_parallel \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Write the shapes of X, each weight shard, local Y, and dX. Why are input-gradient contributions summed for a column split? Which communication moves from backward to forward when the partition orientation changes?

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 18_training_tensor_parallel --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/training_two_rank.sbatch labs/18_training_tensor_parallel.py --profile small
```

Check exported statistics for every rank, then open representative reports from each worker in Systems. Load large reports in small groups and close them between comparisons. Expand NVTX, CUDA, and NCCL kernel rows. Align step/collective boundaries and compare each rank’s arrival, waiting, and compute intervals. A rank-local trace alone cannot establish communication overlap across the job. Compute replay is inapplicable to the live collective; isolate a local kernel before inspecting counters.

Guided comparison: Compare the sharded linear result and input gradient with the replicated reference. Independently identify the required collective from the tensor shapes before choosing the partition orientation.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Shape mismatches usually indicate the wrong partition axis or reconstruction order. A correct forward result can still have an incorrect backward collective, so inspect every gradient gate independently.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

TP changes tensor ownership and communication dependencies inside a layer. Carry those invariants into a larger block only after its partitioned result and update match an unpartitioned reference; do not infer production scaling from this two-rank proof.
