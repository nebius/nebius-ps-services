# Lab 26: Separate collective latency from payload throughput

An all-reduce combines values from every rank and returns the result to every participant. For a four-byte payload, launch, protocol and synchronization costs dominate. A larger payload amortizes those costs. You will compare NCCL automatic selection with a bounded Tree experiment at three fixed message sizes, without forcing a protocol.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use the dedicated two-worker, sixteen-H100 cluster prepared in shared environment setup. Verify local NVLink/NVSwitch and inter-node InfiniBand readiness. Keep driver, software, allocation and other workloads fixed; the two one-GPU TCP workers cannot establish this fabric's performance. The `small` and `large` names select workload sizes, not optimization or profiling modes.

## Concepts and code path

The source initializes sixteen ranks, performs an exact MAX reduction, and records the slowest completed rank for every sample. Cases contain 4, 4096 and 1048576 bytes. Useful GB/s divides payload bytes by elapsed seconds; it is not a physical InfiniBand link counter or NCCL bus bandwidth. Auto can already select Tree, so equal results are meaningful.

## Practice

`labs/26_collective_latency.py` measures sixteen-rank MAX all-reduce for small and larger messages under automatic, Ring, or Tree algorithm selection. It checks exact output and writes per-size latency samples and useful payload rates.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/26_collective_latency/logs/%j.out" \
  --error="$PWD/results/26_collective_latency/logs/%j.err" \
  slurm/26_collective_latency.sbatch --workload small --algorithm auto
```

## Check your results

Each new job owns `results/26_collective_latency/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/26_collective_latency/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

For pair publication, confirm both completed job states and inspect the actual JSON paths. Set `BASELINE_RESULT` and `CANDIDATE_RESULT` to those artifacts, never to stdout or profiler reports.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/26_collective_latency/logs/$LAB_JOB_ID.out"
cat "results/26_collective_latency/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Require `COMPLETED` and exit code `0:0` for each job. Reading JSON is inspection,
not validation: check `lab_id`, `experiment.slurm_job_id`, correctness and
instrumentation fields. Retain every original/aggregate required by this lab.

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it.

```bash
source tools/course_env.sh 26_collective_latency --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 26_collective_latency \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; reviewed current generation otherwise}"
```

| Dashboard panel | Measurement path | Display unit |
| --- | --- | --- |
| Slowest-rank median | `cases.*.median_ms` | s |
| Worst sampled duration | `cases.*.max_ms` | s |
| Useful payload rate | `cases.*.useful_GBps` | Bps |

Select workspace and profile in Grafana. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match confirmation. Set the time picker to **Experiment start** through **Experiment end** and select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers**. Sampled utilization is context, not a per-kernel explanation or proof of transport selection.

## Investigate the behavior

### Workload variations

Submit the two unprofiled jobs from the login node, one after the other after completion, and retain their printed job numbers.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/26_collective_latency/logs/%j.out" \
  --error="$PWD/results/26_collective_latency/logs/%j.err" slurm/26_collective_latency.sbatch --workload small --algorithm auto
sbatch --chdir="$PWD" \
  --output="$PWD/results/26_collective_latency/logs/%j.out" \
  --error="$PWD/results/26_collective_latency/logs/%j.err" slurm/26_collective_latency.sbatch --workload small --algorithm Tree
```

Logs stay under `results/26_collective_latency/logs/`. A submission receipt is not a measurement; wait for successful completion before selecting artifacts.

Select network_all_reduce NVTX ranges and NCCL/CUDA lanes on all sixteen Systems reports. Separate a launch gap from a long collective. Repeat with Ring at the same sizes; explain whether the candidate changes startup cost or the large-message slope. Never conclude that forcing one algorithm is universally better.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/26_collective_latency/logs/%j.out" \
  --error="$PWD/results/26_collective_latency/logs/%j.err" slurm/26_collective_latency.nsys.sbatch --workload small --algorithm auto
```

The native Systems command is in `slurm/26_collective_latency.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Keep diagnostic captures separate from acceptance timings. For distributed work, retain each rank's report and placement record; compare the same application phase across ranks. Nsight Compute replay is inappropriate for live collectives: investigate a separately isolated local kernel when kernel-level evidence is needed.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing worker, vendor field, transport, completed request or correctness check is missing evidence, never zero performance. Read the lab's private job logs and fix the failing prerequisite before another trial. Do not change drivers, network configuration or registration modules inside an experiment.

Publication failure is separate from benchmark failure. Retain successful JSON artifacts and republish with the reviewed generation after ingestion is repaired. Vendor versions are qualification candidates until the designated runtime and sixteen-GPU live checks pass.

## Takeaways and next step

Explain what changed, which observation supports the mechanism, and which alternative explanation remains. Repeat matched unprofiled runs and describe variation; retain a slower candidate when it disproves the initial hypothesis. Finish with the independent investigation above and a bounded decision for this workload and topology.
