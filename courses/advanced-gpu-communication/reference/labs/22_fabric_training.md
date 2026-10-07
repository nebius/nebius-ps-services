# Lab 22: Tune microbatching at fixed global batch

Data parallelism replicates the model and partitions examples. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed. The goal is a defensible explanation backed by correctness checks and repeated observations; a candidate is allowed to be slower.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Keep model, software, clocks and other workload activity fixed; preserve private artifacts for both runs.

## Concepts and code path

Data parallelism replicates the model and partitions examples. Global batch equals ranks × microbatch × accumulation steps. With 128 examples on 16 ranks, microbatch 1 needs 8 accumulation steps; microbatch 4 needs 2. DDP no_sync suppresses reductions on intermediate microbatches, and the final backward reduces the accumulated gradients. Loss is divided by the number of accumulation steps. A full-batch reference checks the resulting parameter update, not just whether loss is finite. The source writes a completed result only after its correctness checks pass. Measurement and publication run separately, so exporting evidence cannot distort the timed operation.

## Practice

`labs/22_fabric_training.py` changes microbatch size while keeping the global batch fixed across eight or sixteen DDP ranks. It checks accumulated loss and updates against a full-batch reference and writes timing, throughput, accumulation count, and peak memory.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/22_fabric_training/logs/%j.out" \
  --error="$PWD/results/22_fabric_training/logs/%j.err" \
  slurm/22_fabric_training.sbatch --workload small --microbatch 1
```

## Check your results

Each new job owns `results/22_fabric_training/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/22_fabric_training/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Wait for both jobs to complete successfully. Inspect the measured fields and correctness status; a failed check must be resolved before comparing performance.

Record each successful submission's job number. For each job, require `COMPLETED` and exit code `0:0`, then open its own logs and printed result path:

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/22_fabric_training/logs/$LAB_JOB_ID.out"
cat "results/22_fabric_training/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Training step duration | `step.median_ms` | s |
| Useful samples per second | `samples_per_second` | samples/s |
| Maximum rank allocated memory | `peak_allocated_bytes` | bytes |
| Global mean loss | `loss` | none |

Select the two unprofiled result artifacts. The publisher checks equivalent parameters and allows only the named change. Repeated qualification uses no changed parameter.

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it.

```bash
source tools/course_env.sh 22_fabric_training --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 22_fabric_training \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Set the time picker from **Experiment start** to **Experiment end**, then select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers** for telemetry. Summary panels show the selected pair; telemetry describes its actual job window.

## Investigate the behavior

### Workload variations

On the login node, submit the baseline and candidate below. Save both job numbers and printed result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/22_fabric_training/logs/%j.out" \
  --error="$PWD/results/22_fabric_training/logs/%j.err" slurm/22_fabric_training.sbatch --workload small --microbatch 1
sbatch --chdir="$PWD" \
  --output="$PWD/results/22_fabric_training/logs/%j.out" \
  --error="$PWD/results/22_fabric_training/logs/%j.err" slurm/22_fabric_training.sbatch --workload small --microbatch 4
```

Slurm writes job logs under `results/22_fabric_training/logs/<job>.out` and `.err`. A submitted job is not a completed result.

Use Systems forward, backward and optimizer ranges to count launch gaps and locate the final gradient synchronization. Grafana compares useful samples/s, slowest-rank step time, peak allocated memory and global mean loss. The tiny MLP isolates update and communication mechanics; it is not a production LLM throughput estimate. Each timed replay includes model reset, identically in both variants. Peak memory includes reference buffers. Independently test microbatch 2, then explain whether larger kernels repaid memory cost. A separate --nodes=1 campaign tests strong scaling with the same global batch; never change node count inside a microbatch comparison.

Capture separately from timing. Check exported statistics for every rank, then open representative `.nsys-rep` reports from each worker in Systems, loading large reports in small groups. Report filenames contain the Slurm job, step, task ID and process ID. A torchrun task launches several workers, so its Slurm task ID is not a worker's global rank. Use the rank-to-host record and each report's process identity to correlate matching phases across workers. Use a local-kernel exercise for Compute: replaying distributed collectives can stall their peers.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/22_fabric_training/logs/%j.out" \
  --error="$PWD/results/22_fabric_training/logs/%j.err" slurm/22_fabric_training.nsys.sbatch --workload small --microbatch 1
```

The native Systems command is in `slurm/22_fabric_training.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Repeat the unprofiled baseline and candidate after inspecting the trace. Instrumented artifacts are rejected by the comparison publisher.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing required full GPU (H100 or H200), peer path, InfiniBand port, rank or result is a failed prerequisite. Stop and inspect the per-lab job log. Do not force a transport or change cluster configuration to disguise a failed check. The owner must repair the supported runtime before another trial.

Publication failure is distinct from benchmark failure. Retain valid JSON and republish using the reviewed generation. Missing metrics remain unknown; failed ingestion does not mean the workload failed. A candidate need not be faster to teach a useful result.

## Takeaways and next step

Explain which measured observation supports your hypothesis, which alternative explanation remains, and whether the one-variable change should be kept. Repeat enough clean runs to expose variation, and report topology and workload limits with the conclusion.
