# Lab 14: Correlate PyTorch and Systems traces across ranks

Independent compute can overlap an asynchronous all-reduce only until a dependent consumer needs its result. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed. The goal is a defensible explanation backed by correctness checks and repeated observations; a candidate is allowed to be slower.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/14_distributed_profiling.json).

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Keep model, software, clocks and other workload activity fixed; preserve private artifacts for both runs.

## Concepts and code path

Independent compute can overlap an asynchronous all-reduce only until a dependent consumer needs its result. The off variant waits before matrix multiplication; the on variant launches the collective, computes an independent product and waits before reuse. The experiment checks every collective value and compares the product with a reference. Each timing sample uses the maximum elapsed time across ranks, measured after a synchronized start and completion. The source writes a completed result only after its correctness checks pass. Measurement and publication run separately, so exporting evidence cannot distort the timed operation.

## Practice

On the login node, submit the baseline and candidate below. Save both job numbers and printed result paths.

```bash
python3 tools/submit_lab.py --lab 14_distributed_profiling slurm/fabric.sbatch labs/14_distributed_profiling.py --profile small --overlap off
python3 tools/submit_lab.py --lab 14_distributed_profiling slurm/fabric.sbatch labs/14_distributed_profiling.py --profile small --overlap on
```

Logs are created before submission under `results/14_distributed_profiling/logs/<job>.out` and `.err`. A submitted job is not a completed result.

## Check your results

Wait for both jobs to complete successfully. Inspect the measured fields and correctness status; a failed check must be resolved before comparing performance.

```bash
sacct -j "${LAB_JOB_ID:?job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 14_distributed_profiling --job "$LAB_JOB_ID"
```

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Slowest-rank step duration | `step.median_ms` | s |
| Worst sampled step duration | `step.max_ms` | s |

Select the two unprofiled result artifacts. The publisher checks equivalent parameters and allows only the named change. Repeated qualification uses no changed parameter.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 14_distributed_profiling \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Set the time picker from **Experiment start** to **Experiment end**, then select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers** for telemetry. Summary panels show the selected pair; telemetry describes its actual job window.

## Investigate the behavior

In Systems, select communication, independent_compute and wait_for_collective ranges and inspect CUDA/NCCL lanes on every rank. Is reduced waiting real overlap or a slower GEMM sharing resources? For framework attribution, make a separate --torch-trace run: open each rank JSON in a local Chrome trace viewer and inspect training_like_step, aten::mm and its input shapes, c10d collectives and CUDA activity. The workload reuses tensors allocated before recording, so allocation events may be absent even with memory profiling enabled. An empty allocation view does not mean the workload uses no GPU memory. Profiling adds overhead; do not compare those timings with clean runs or capture PyTorch and Systems simultaneously. Independently try the same fixed-shape comparison on one node; do not mix eight- and sixteen-rank slots.

Capture separately from timing. Check exported statistics for every rank, then open representative `.nsys-rep` reports from each worker in Systems, loading large reports in small groups. Rank filenames retain the Slurm job and global rank; correlate matching phases across reports. Use a local-kernel exercise for Compute: replaying distributed collectives can stall their peers.

```bash
python3 tools/submit_lab.py --lab 14_distributed_profiling --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/fabric.sbatch labs/14_distributed_profiling.py --profile small --overlap off
```

Repeat the unprofiled baseline and candidate after inspecting the trace. Instrumented artifacts are rejected by the comparison publisher.

```bash
python3 tools/submit_lab.py --lab 14_distributed_profiling slurm/fabric.sbatch labs/14_distributed_profiling.py --profile small --overlap off --torch-trace
```

Each trace is under `results/14_distributed_profiling/profiles/torch-<run-id>/rank-<rank>.json`; preserve the rank-to-host record privately.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing required full GPU (H100 or H200), peer path, InfiniBand port, rank or result is a failed prerequisite. Stop and inspect the per-lab job log. Do not force a transport or change cluster configuration to disguise a failed check. The owner must repair the supported runtime before another trial.

Publication failure is distinct from benchmark failure. Retain valid JSON and republish using the reviewed generation. Missing metrics remain unknown; failed ingestion does not mean the workload failed. A candidate need not be faster to teach a useful result.

## Takeaways and next step

Explain which measured observation supports your hypothesis, which alternative explanation remains, and whether the one-variable change should be kept. Repeat enough clean runs to expose variation, and report topology and workload limits with the conclusion.
