# Lab 28: Test NCCL network-adapter selection

A host channel adapter, or HCA, connects a worker to InfiniBand. NCCL selects usable adapters and communication paths from topology. This experiment compares automatic adapter eligibility with one explicitly selected HCA on each node. Both runs retain sixteen ranks and identical payloads, so the experiment tests network selection rather than adding compute resources.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use the dedicated two-worker, sixteen-H100 cluster prepared in shared environment setup. Verify local NVLink/NVSwitch and inter-node InfiniBand readiness. Keep driver, software, allocation and other workloads fixed; the two one-GPU TCP workers cannot establish this fabric's performance. The `small` and `large` names select workload sizes, not optimization or profiling modes.

## Concepts and code path

The single policy sets an exact NCCL_IB_HCA match for each node. The all policy leaves adapter selection automatic; it does not assert that every adapter is active. NCCL INIT, NET and GRAPH logs provide selected-path evidence. A faster run or an environment variable alone cannot prove GPUDirect RDMA. Correlate logs with the topology and completed collective results.

## Practice

`labs/28_nic_selection.py` runs sixteen-rank MAX all-reduce with automatic NIC selection or one selected NIC per node. It checks exact output and writes latency samples and useful payload rate while retaining automatic algorithm selection.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/28_nic_selection/logs/%j.out" \
  --error="$PWD/results/28_nic_selection/logs/%j.err" \
  slurm/28_nic_selection.sbatch --workload small --server-hca "$SERVER_HCA" --client-hca "$CLIENT_HCA" --hca-policy all
```

## Check your results

Each new job owns `results/28_nic_selection/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/28_nic_selection/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

For pair publication, confirm both completed job states and inspect the actual JSON paths. Set `BASELINE_RESULT` and `CANDIDATE_RESULT` to those artifacts, never to stdout or profiler reports.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/28_nic_selection/logs/$LAB_JOB_ID.out"
cat "results/28_nic_selection/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Require `COMPLETED` and exit code `0:0` for each job. Reading JSON is inspection,
not validation: check `lab_id`, `experiment.slurm_job_id`, correctness and
instrumentation fields. Retain every original/aggregate required by this lab.

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it.

```bash
source tools/course_env.sh 28_nic_selection --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 28_nic_selection \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; reviewed current generation otherwise}"
```

| Dashboard panel | Measurement path | Display unit |
| --- | --- | --- |
| Collective median duration | `cases.*.median_ms` | s |
| Useful payload rate | `cases.*.useful_GBps` | Bps |

Select workspace and profile in Grafana. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match confirmation. Set the time picker to **Experiment start** through **Experiment end** and select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers**. Sampled utilization is context, not a per-kernel explanation or proof of transport selection.

## Investigate the behavior

### Workload variations

Submit the two unprofiled jobs from the login node, one after the other after completion, and retain their printed job numbers.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/28_nic_selection/logs/%j.out" \
  --error="$PWD/results/28_nic_selection/logs/%j.err" slurm/28_nic_selection.sbatch --workload small --server-hca "$SERVER_HCA" --client-hca "$CLIENT_HCA" --hca-policy all
sbatch --chdir="$PWD" \
  --output="$PWD/results/28_nic_selection/logs/%j.out" \
  --error="$PWD/results/28_nic_selection/logs/%j.err" slurm/28_nic_selection.sbatch --workload small --server-hca "$SERVER_HCA" --client-hca "$CLIENT_HCA" --hca-policy single
```

Logs stay under `results/28_nic_selection/logs/`. A submission receipt is not a measurement; wait for successful completion before selecting artifacts.

Match each global rank to its worker and inspect NET/IB transport, selected HCA and GDR evidence in the logs. In Systems inspect the network_all_reduce range and stragglers. Try another observed HCA in a separate comparison; keep both baseline and candidate HCA arguments identical within that pair. Explain GPU-to-NIC locality before interpreting a throughput difference.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/28_nic_selection/logs/%j.out" \
  --error="$PWD/results/28_nic_selection/logs/%j.err" slurm/28_nic_selection.nsys.sbatch --workload small --server-hca "$SERVER_HCA" --client-hca "$CLIENT_HCA" --hca-policy all
```

The native Systems command is in `slurm/28_nic_selection.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Keep diagnostic captures separate from acceptance timings. For distributed work, retain each rank's report and placement record; compare the same application phase across ranks. Nsight Compute replay is inappropriate for live collectives: investigate a separately isolated local kernel when kernel-level evidence is needed.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing worker, vendor field, transport, completed request or correctness check is missing evidence, never zero performance. Read the lab's private job logs and fix the failing prerequisite before another trial. Do not change drivers, network configuration or registration modules inside an experiment.

Publication failure is separate from benchmark failure. Retain successful JSON artifacts and republish with the reviewed generation after ingestion is repaired. Vendor versions are qualification candidates until the designated runtime and sixteen-GPU live checks pass.

## Takeaways and next step

Explain what changed, which observation supports the mechanism, and which alternative explanation remains. Repeat matched unprofiled runs and describe variation; retain a slower candidate when it disproves the initial hypothesis. Finish with the independent investigation above and a bounded decision for this workload and topology.
