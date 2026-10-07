# Lab 14: Correlate PyTorch and Systems traces across ranks

Independent compute can overlap an asynchronous all-reduce only until a dependent consumer needs its result. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed. The goal is a defensible explanation backed by correctness checks and repeated observations; a candidate is allowed to be slower.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Keep model, software, clocks and other workload activity fixed; preserve private artifacts for both runs.

## Concepts and code path

Independent compute can overlap an asynchronous all-reduce only until a dependent consumer needs its result. The off variant waits before matrix multiplication; the on variant launches the collective, computes an independent product and waits before reuse. The experiment checks every collective value and compares the product with a reference. Each timing sample uses the maximum elapsed time across ranks, measured after a synchronized start and completion. The source writes a completed result only after its correctness checks pass. Measurement and publication run separately, so exporting evidence cannot distort the timed operation.

## Practice

`labs/14_distributed_profiling.py` runs independent matrix multiplication and all-reduce with overlap disabled or enabled. It checks reference-equivalent outputs and writes step timings; a separate optional PyTorch capture exports one trace per rank.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/14_distributed_profiling/logs/%j.out" \
  --error="$PWD/results/14_distributed_profiling/logs/%j.err" \
  slurm/14_distributed_profiling.sbatch --workload small --overlap off
```

## Check your results

Each new job owns `results/14_distributed_profiling/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/14_distributed_profiling/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Wait for both jobs to complete successfully. Inspect the measured fields and correctness status; a failed check must be resolved before comparing performance.

Record each successful submission's job number. For each job, require `COMPLETED` and exit code `0:0`, then open its own logs and printed result path:

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/14_distributed_profiling/logs/$LAB_JOB_ID.out"
cat "results/14_distributed_profiling/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Slowest-rank step duration | `step.median_ms` | s |
| Worst sampled step duration | `step.max_ms` | s |

Select the two unprofiled result artifacts. The publisher checks equivalent parameters and allows only the named change. Repeated qualification uses no changed parameter.

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it.

```bash
source tools/course_env.sh 14_distributed_profiling --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 14_distributed_profiling \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Set the time picker from **Experiment start** to **Experiment end**, then select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers** for telemetry. Summary panels show the selected pair; telemetry describes its actual job window.

## Investigate the behavior

### Workload variations

On the login node, submit the baseline and candidate below. Save both job numbers and printed result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/14_distributed_profiling/logs/%j.out" \
  --error="$PWD/results/14_distributed_profiling/logs/%j.err" slurm/14_distributed_profiling.sbatch --workload small --overlap off
sbatch --chdir="$PWD" \
  --output="$PWD/results/14_distributed_profiling/logs/%j.out" \
  --error="$PWD/results/14_distributed_profiling/logs/%j.err" slurm/14_distributed_profiling.sbatch --workload small --overlap on
```

Slurm writes job logs under `results/14_distributed_profiling/logs/<job>.out` and `.err`. A submitted job is not a completed result.

In Systems, select communication, independent_compute and wait_for_collective ranges and inspect CUDA/NCCL lanes on every rank. Is reduced waiting real overlap or a slower GEMM sharing resources? For framework attribution, make a separate --torch-trace run: open each rank JSON in a local Chrome trace viewer and inspect training_like_step, aten::mm and its input shapes, c10d collectives and CUDA activity. The workload reuses tensors allocated before recording, so allocation events may be absent even with memory profiling enabled. An empty allocation view does not mean the workload uses no GPU memory. Profiling adds overhead; do not compare those timings with clean runs or capture PyTorch and Systems simultaneously. Independently try the same fixed-shape comparison on one node; do not mix eight- and sixteen-rank slots.

Capture separately from timing. Check exported statistics for every rank, then open representative `.nsys-rep` reports from each worker in Systems, loading large reports in small groups. Report filenames contain the Slurm job, step, task ID and process ID. A torchrun task launches several workers, so its Slurm task ID is not a worker's global rank. Use the rank-to-host record and each report's process identity to correlate matching phases across workers. Use a local-kernel exercise for Compute: replaying distributed collectives can stall their peers.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/14_distributed_profiling/logs/%j.out" \
  --error="$PWD/results/14_distributed_profiling/logs/%j.err" slurm/14_distributed_profiling.nsys.sbatch --workload small --overlap off
```

The native Systems command is in `slurm/14_distributed_profiling.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Repeat the unprofiled baseline and candidate after inspecting the trace. Instrumented artifacts are rejected by the comparison publisher.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/14_distributed_profiling/logs/%j.out" \
  --error="$PWD/results/14_distributed_profiling/logs/%j.err" slurm/14_distributed_profiling.sbatch --workload small --overlap off --torch-trace
```

Each trace is under `results/14_distributed_profiling/jobs/$JOB_ID/results/14_distributed_profiling/profiles/torch-<run-id>/rank-<rank>.json`; preserve the rank-to-host record privately.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing required full GPU (H100 or H200), peer path, InfiniBand port, rank or result is a failed prerequisite. Stop and inspect the per-lab job log. Do not force a transport or change cluster configuration to disguise a failed check. The owner must repair the supported runtime before another trial.

Publication failure is distinct from benchmark failure. Retain valid JSON and republish using the reviewed generation. Missing metrics remain unknown; failed ingestion does not mean the workload failed. A candidate need not be faster to teach a useful result.

## Takeaways and next step

Explain which measured observation supports your hypothesis, which alternative explanation remains, and whether the one-variable change should be kept. Repeat enough clean runs to expose variation, and report topology and workload limits with the conclusion.
