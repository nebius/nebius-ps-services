# Lab 28: Test NCCL network-adapter selection

A host channel adapter, or HCA, connects a worker to InfiniBand. NCCL selects usable adapters and communication paths from topology. This experiment compares automatic adapter eligibility with one explicitly selected HCA on each node. Both runs retain sixteen ranks and identical payloads, so the experiment tests network selection rather than adding compute resources.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/28_nic_selection.json).

Use the dedicated two-worker, sixteen-H100 cluster prepared in shared environment setup. Verify local NVLink/NVSwitch and inter-node InfiniBand readiness. Keep driver, software, allocation and other workloads fixed; the two one-GPU TCP workers cannot establish this fabric's performance. The `small` and `large` names select workload sizes, not optimization or profiling modes.

## Concepts and code path

The single policy sets an exact NCCL_IB_HCA match for each node. The all policy leaves adapter selection automatic; it does not assert that every adapter is active. NCCL INIT, NET and GRAPH logs provide selected-path evidence. A faster run or an environment variable alone cannot prove GPUDirect RDMA. Correlate logs with the topology and completed collective results.

## Practice

Submit the two unprofiled jobs from the login node, one after the other after completion, and retain their printed job numbers.

```bash
python3 tools/submit_lab.py --lab 28_nic_selection slurm/fabric.sbatch labs/28_nic_selection.py --profile small --server-hca "$SERVER_HCA" --client-hca "$CLIENT_HCA" --hca-policy all
python3 tools/submit_lab.py --lab 28_nic_selection slurm/fabric.sbatch labs/28_nic_selection.py --profile small --server-hca "$SERVER_HCA" --client-hca "$CLIENT_HCA" --hca-policy single
```

Logs stay under `results/28_nic_selection/logs/`. A submission receipt is not a measurement; wait for successful completion before selecting artifacts.

## Check your results

Confirm both completed job states and inspect the actual JSON paths. Set `BASELINE_RESULT` and `CANDIDATE_RESULT` to those artifacts, never to stdout or profiler reports.

```bash
sacct -j "${LAB_JOB_ID:?job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 28_nic_selection --job "$LAB_JOB_ID"
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

Match each global rank to its worker and inspect NET/IB transport, selected HCA and GDR evidence in the logs. In Systems inspect the network_all_reduce range and stragglers. Try another observed HCA in a separate comparison; keep both baseline and candidate HCA arguments identical within that pair. Explain GPU-to-NIC locality before interpreting a throughput difference.

```bash
python3 tools/submit_lab.py --lab 28_nic_selection --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/fabric.sbatch labs/28_nic_selection.py --profile small --server-hca "$SERVER_HCA" --client-hca "$CLIENT_HCA" --hca-policy all
```

Keep diagnostic captures separate from acceptance timings. For distributed work, retain each rank's report and placement record; compare the same application phase across ranks. Nsight Compute replay is inappropriate for live collectives: investigate a separately isolated local kernel when kernel-level evidence is needed.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing worker, vendor field, transport, completed request or correctness check is missing evidence, never zero performance. Read the lab's private job logs and fix the failing prerequisite before another trial. Do not change drivers, network configuration or registration modules inside an experiment.

Publication failure is separate from benchmark failure. Retain successful JSON artifacts and republish with the reviewed generation after ingestion is repaired. Vendor versions are qualification candidates until the designated runtime and sixteen-GPU live checks pass.

## Takeaways and next step

Explain what changed, which observation supports the mechanism, and which alternative explanation remains. Repeat matched unprofiled runs and describe variation; retain a slower candidate when it disproves the initial hypothesis. Finish with the independent investigation above and a bounded decision for this workload and topology.
