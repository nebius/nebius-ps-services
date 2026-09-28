# Lab 22: Tune microbatching at fixed global batch

Data parallelism replicates the model and partitions examples. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed. The goal is a defensible explanation backed by correctness checks and repeated observations; a candidate is allowed to be slower.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/22_fabric_training.json).

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Keep model, software, clocks and other workload activity fixed; preserve private artifacts for both runs.

## Concepts and code path

Data parallelism replicates the model and partitions examples. Global batch equals ranks × microbatch × accumulation steps. With 128 examples on 16 ranks, microbatch 1 needs 8 accumulation steps; microbatch 4 needs 2. DDP no_sync suppresses reductions on intermediate microbatches, and the final backward reduces the accumulated gradients. Loss is divided by the number of accumulation steps. A full-batch reference checks the resulting parameter update, not just whether loss is finite. The source writes a completed result only after its correctness checks pass. Measurement and publication run separately, so exporting evidence cannot distort the timed operation.

## Practice

On the login node, submit the baseline and candidate below. Save both job numbers and printed result paths.

```bash
python3 tools/submit_lab.py --lab 22_fabric_training slurm/fabric.sbatch labs/22_fabric_training.py --profile small --microbatch 1
python3 tools/submit_lab.py --lab 22_fabric_training slurm/fabric.sbatch labs/22_fabric_training.py --profile small --microbatch 4
```

Logs are created before submission under `results/22_fabric_training/logs/<job>.out` and `.err`. A submitted job is not a completed result.

## Check your results

Wait for both jobs to complete successfully. Inspect the measured fields and correctness status; a failed check must be resolved before comparing performance.

```bash
sacct -j "${LAB_JOB_ID:?job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 22_fabric_training --job "$LAB_JOB_ID"
```

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Training step duration | `step.median_ms` | s |
| Useful samples per second | `samples_per_second` | samples/s |
| Maximum rank allocated memory | `peak_allocated_bytes` | bytes |
| Global mean loss | `loss` | none |

Select the two unprofiled result artifacts. The publisher checks equivalent parameters and allows only the named change. Repeated qualification uses no changed parameter.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 22_fabric_training \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Set the time picker from **Experiment start** to **Experiment end**, then select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers** for telemetry. Summary panels show the selected pair; telemetry describes its actual job window.

## Investigate the behavior

Use Systems forward, backward and optimizer ranges to count launch gaps and locate the final gradient synchronization. Grafana compares useful samples/s, slowest-rank step time, peak allocated memory and global mean loss. The tiny MLP isolates update and communication mechanics; it is not a production LLM throughput estimate. Each timed replay includes model reset, identically in both variants. Peak memory includes reference buffers. Independently test microbatch 2, then explain whether larger kernels repaid memory cost. A separate --nodes=1 campaign tests strong scaling with the same global batch; never change node count inside a microbatch comparison.

Capture separately from timing. Check exported statistics for every rank, then open representative `.nsys-rep` reports from each worker in Systems, loading large reports in small groups. Rank filenames retain the Slurm job and global rank; correlate matching phases across reports. Use a local-kernel exercise for Compute: replaying distributed collectives can stall their peers.

```bash
python3 tools/submit_lab.py --lab 22_fabric_training --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/fabric.sbatch labs/22_fabric_training.py --profile small --microbatch 1
```

Repeat the unprofiled baseline and candidate after inspecting the trace. Instrumented artifacts are rejected by the comparison publisher.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing required full GPU (H100 or H200), peer path, InfiniBand port, rank or result is a failed prerequisite. Stop and inspect the per-lab job log. Do not force a transport or change cluster configuration to disguise a failed check. The owner must repair the supported runtime before another trial.

Publication failure is distinct from benchmark failure. Retain valid JSON and republish using the reviewed generation. Missing metrics remain unknown; failed ingestion does not mean the workload failed. A candidate need not be faster to teach a useful result.

## Takeaways and next step

Explain which measured observation supports your hypothesis, which alternative explanation remains, and whether the one-variable change should be kept. Repeat enough clean runs to expose variation, and report topology and workload limits with the conclusion.
