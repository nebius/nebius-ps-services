# Lab 07: Validate host and GPU memory over InfiniBand

RDMA lets the NIC access registered memory without the CPU copying each payload. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed. The goal is a defensible explanation backed by correctness checks and repeated observations; a candidate is allowed to be slower.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/07_rdma_bandwidth.json).

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Set SERVER_HCA and CLIENT_HCA to the reviewed mlx5 devices nearest GPU 0. If the owner qualified nvidia-peermem instead of DMA-BUF, use --memory cuda-peermem in the candidate command. Keep model, software, clocks and other workload activity fixed; preserve private artifacts for both runs.

Source `$COURSE_TOOLS/fabric/environment.sh` as described in shared environment setup before submission. CUDA buffer validation needs the installed `libperftest_kernels.so`; a plugin-loading error is a setup failure, not a passing bandwidth result.

## Concepts and code path

RDMA lets the NIC access registered memory without the CPU copying each payload. GPUDirect RDMA registers GPU memory for that path. The test runs one owned ib_write_bw endpoint on each worker, with reliable-connected InfiniBand, equal sizes, equal iteration counts, equal queue depths and bidirectional data validation. CUDA DMA-BUF and nvidia-peermem are alternative registration mechanisms; use only the mechanism qualified for the installed driver and kernel. An IP address bootstraps the connection; it does not identify the payload transport. The source writes a completed result only after its correctness checks pass. Measurement and publication run separately, so exporting evidence cannot distort the timed operation.

## Practice

On the login node, submit the baseline and candidate below. Save both job numbers and printed result paths.

```bash
python3 tools/submit_lab.py --lab 07_rdma_bandwidth slurm/fabric_tools.sbatch labs/07_rdma_bandwidth.py --profile small --memory host --server-device "${SERVER_HCA:?qualified mlx5 device}" --client-device "${CLIENT_HCA:?qualified mlx5 device}"
python3 tools/submit_lab.py --lab 07_rdma_bandwidth slurm/fabric_tools.sbatch labs/07_rdma_bandwidth.py --profile small --memory cuda-dmabuf --server-device "$SERVER_HCA" --client-device "$CLIENT_HCA"
```

Logs are created before submission under `results/07_rdma_bandwidth/logs/<job>.out` and `.err`. A submitted job is not a completed result.

## Check your results

Wait for both jobs to complete successfully. Inspect the measured fields and correctness status; a failed check must be resolved before comparing performance.

```bash
sacct -j "${LAB_JOB_ID:?job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 07_rdma_bandwidth --job "$LAB_JOB_ID"
```

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Validated bidirectional bandwidth | `bidirectional_Gbps` | Gbits |
| Minimum validated endpoint bytes | `validated_bytes_min` | bytes |

Select the two unprofiled result artifacts. The publisher checks equivalent parameters and allows only the named change. Repeated qualification uses no changed parameter.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 07_rdma_bandwidth \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Set the time picker from **Experiment start** to **Experiment end**, then select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers** for telemetry. Summary panels show the selected pair; telemetry describes its actual job window.

## Investigate the behavior

Require both endpoint logs to say VALIDATION: PASSED with nonzero chunks and bytes, and both JSON reports to identify IB/RC and CUDA device 0 for the candidate. Bidirectional Gbit/s is aggregate traffic in both directions, not one-way GB/s. After the host/GPU comparison, hold GPU memory fixed and independently change --tx-depth from 128 to 64; publish the two printed result paths; the publisher detects the single changed parameter. Match NIC/GPU locality from nvidia-smi topo -m and ibdev2netdev before testing. NET/IB alone is insufficient evidence of GPUDirect. Registration failure is a failed prerequisite, not evidence that host memory is faster.

**Nsight Systems: not applicable.** This verbs benchmark measures NIC DMA into host or GPU memory. CUDA tracing cannot establish RDMA wire bandwidth; use validated perftest throughput and topology evidence. Inspect the measured or modeled fields in this lab's dashboard; retain the artifact and its stated scope.

## If something goes wrong

A missing required full GPU (H100 or H200), peer path, InfiniBand port, rank or result is a failed prerequisite. Stop and inspect the per-lab job log. Do not force a transport or change cluster configuration to disguise a failed check. The owner must repair the supported runtime before another trial.

Publication failure is distinct from benchmark failure. Retain valid JSON and republish using the reviewed generation. Missing metrics remain unknown; failed ingestion does not mean the workload failed. A candidate need not be faster to teach a useful result.

## Takeaways and next step

Explain which measured observation supports your hypothesis, which alternative explanation remains, and whether the one-variable change should be kept. Repeat enough clean runs to expose variation, and report topology and workload limits with the conclusion.
