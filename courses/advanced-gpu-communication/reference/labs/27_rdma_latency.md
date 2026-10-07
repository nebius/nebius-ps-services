# Lab 27: Measure GPU-memory RDMA read completion latency

Remote direct memory access, or RDMA, lets a network adapter access registered memory without copying every message through the remote CPU. This experiment measures completion latency for reliable-connected RDMA reads. Compare host memory with GPU memory at one fixed payload size; small transfers expose costs that a bulk bandwidth result hides.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use the dedicated two-worker, sixteen-H100 cluster prepared in shared environment setup. Verify local NVLink/NVSwitch and inter-node InfiniBand readiness. Keep driver, software, allocation and other workloads fixed; the two one-GPU TCP workers cannot establish this fabric's performance. The `small` and `large` names select workload sizes, not optimization or profiling modes.

## Concepts and code path

The coordinator starts one owned ib_read_lat endpoint on each worker. The client reports median, mean and p99 completed read latency in microseconds. RC means reliable connected transport. DMA-BUF and nvidia-peermem are alternative GPU-memory registration paths; choose only a path qualified by the owner. Successful completions and requested-memory validation do not establish application numerical correctness. Lab 07 separately checks bandwidth payload integrity.

## Practice

`labs/27_rdma_latency.py` runs completed InfiniBand RDMA READ latency tests with host or GPU memory. It checks memory and transport configuration and writes latency statistics and payload size; it does not claim payload-content validation.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/27_rdma_latency/logs/%j.out" \
  --error="$PWD/results/27_rdma_latency/logs/%j.err" \
  slurm/27_rdma_latency.sbatch --workload small --server-device "$SERVER_HCA" --client-device "$CLIENT_HCA" --memory host
```

## Check your results

Each new job owns `results/27_rdma_latency/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/27_rdma_latency/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

For pair publication, confirm both completed job states and inspect the actual JSON paths. Set `BASELINE_RESULT` and `CANDIDATE_RESULT` to those artifacts, never to stdout or profiler reports.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/27_rdma_latency/logs/$LAB_JOB_ID.out"
cat "results/27_rdma_latency/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Require `COMPLETED` and exit code `0:0` for each job. Reading JSON is inspection,
not validation: check `lab_id`, `experiment.slurm_job_id`, correctness and
instrumentation fields. Retain every original/aggregate required by this lab.

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it.

```bash
source tools/course_env.sh 27_rdma_latency --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 27_rdma_latency \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; reviewed current generation otherwise}"
```

| Dashboard panel | Measurement path | Display unit |
| --- | --- | --- |
| Median RDMA read latency | `median_us` | s |
| P99 RDMA read latency | `p99_us` | s |

Select workspace and profile in Grafana. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match confirmation. Set the time picker to **Experiment start** through **Experiment end** and select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers**. Sampled utilization is context, not a per-kernel explanation or proof of transport selection.

## Investigate the behavior

### Workload variations

Submit the two unprofiled jobs from the login node, one after the other after completion, and retain their printed job numbers.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/27_rdma_latency/logs/%j.out" \
  --error="$PWD/results/27_rdma_latency/logs/%j.err" slurm/27_rdma_latency.sbatch --workload small --server-device "$SERVER_HCA" --client-device "$CLIENT_HCA" --memory host
sbatch --chdir="$PWD" \
  --output="$PWD/results/27_rdma_latency/logs/%j.out" \
  --error="$PWD/results/27_rdma_latency/logs/%j.err" slurm/27_rdma_latency.sbatch --workload small --server-device "$SERVER_HCA" --client-device "$CLIENT_HCA" --memory cuda-dmabuf
```

Logs stay under `results/27_rdma_latency/logs/`. A submission receipt is not a measurement; wait for successful completion before selecting artifacts.

Read both private endpoint logs and the vendor JSON transport fields. Compare the p99 tail with the median: a lower median can coexist with worse variability. Repeat the same GPU-memory comparison using cuda-peermem only when available. CUDA kernel replay is inapplicable to this network completion measurement; use vendor evidence and job-window Grafana context.

Keep diagnostic captures separate from acceptance timings. For distributed work, retain each rank's report and placement record; compare the same application phase across ranks. Nsight Compute replay is inappropriate for live collectives: investigate a separately isolated local kernel when kernel-level evidence is needed.

**Nsight Systems: not applicable.** This verbs benchmark measures NIC completion latency. It is not a CUDA-kernel workload; use perftest latency and the checked host/GPU registration mode. Inspect the measured or modeled fields in this lab's dashboard; retain the artifact and its stated scope.

## If something goes wrong

A missing worker, vendor field, transport, completed request or correctness check is missing evidence, never zero performance. Read the lab's private job logs and fix the failing prerequisite before another trial. Do not change drivers, network configuration or registration modules inside an experiment.

Publication failure is separate from benchmark failure. Retain successful JSON artifacts and republish with the reviewed generation after ingestion is repaired. Vendor versions are qualification candidates until the designated runtime and sixteen-GPU live checks pass.

## Takeaways and next step

Explain what changed, which observation supports the mechanism, and which alternative explanation remains. Repeat matched unprofiled runs and describe variation; retain a slower candidate when it disproves the initial hypothesis. Finish with the independent investigation above and a bounded decision for this workload and topology.
