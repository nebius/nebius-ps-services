# Lab 11: Compare flat and hierarchical all-reduce

All-reduce returns the sum to every participant. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed. The goal is a defensible explanation backed by correctness checks and repeated observations; a candidate is allowed to be slower.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/11_collective_layout.json).

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Keep model, software, clocks and other workload activity fixed; preserve private artifacts for both runs.

## Concepts and code path

All-reduce returns the sum to every participant. The flat variant asks NCCL to choose an algorithm over all ranks. The explicit hierarchy reduces within each eight-GPU node, all-reduces between the two node leaders, then broadcasts within each node. All ranks create process groups in the same order. Both variants process identical float32 elements and must return the exact rank sum. NCCL may already choose a better hierarchy; fewer communicating leaders is a hypothesis, not a promised optimization. The source writes a completed result only after its correctness checks pass. Measurement and publication run separately, so exporting evidence cannot distort the timed operation.

## Practice

On the login node, submit the baseline and candidate below. Save both job numbers and printed result paths.

```bash
python3 tools/submit_lab.py --lab 11_collective_layout slurm/fabric.sbatch labs/11_collective_layout.py --profile small --layout flat
python3 tools/submit_lab.py --lab 11_collective_layout slurm/fabric.sbatch labs/11_collective_layout.py --profile small --layout hierarchical
```

Logs are created before submission under `results/11_collective_layout/logs/<job>.out` and `.err`. A submitted job is not a completed result.

## Check your results

Wait for both jobs to complete successfully. Inspect the measured fields and correctness status; a failed check must be resolved before comparing performance.

```bash
sacct -j "${LAB_JOB_ID:?job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 11_collective_layout --job "$LAB_JOB_ID"
```

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Collective duration | `collective.median_ms` | s |
| Useful payload rate | `useful_GBps` | GBs |

Select the two unprofiled result artifacts. The publisher checks equivalent parameters and allows only the named change. Repeated qualification uses no changed parameter.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 11_collective_layout \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Set the time picker from **Experiment start** to **Experiment end**, then select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers** for telemetry. Summary panels show the selected pair; telemetry describes its actual job window.

## Investigate the behavior

In Systems, align rank reports using the same collective sequence and the local_reduce, leader_all_reduce and local_broadcast NVTX ranges. Compare the inter-node tail and waiting nonleaders with flat_all_reduce. Grafana Collective duration is the median of each iteration’s slowest-rank time; Useful payload rate is bytes divided by that time, not NCCL normalized bus bandwidth. Independently run the same comparison with --nodes=1 to isolate local overhead, keeping those results in a separate eight-rank comparison.

Capture separately from timing. Check exported statistics for every rank, then open representative `.nsys-rep` reports from each worker in Systems, loading large reports in small groups. Rank filenames retain the Slurm job and global rank; correlate matching phases across reports. Use a local-kernel exercise for Compute: replaying distributed collectives can stall their peers.

```bash
python3 tools/submit_lab.py --lab 11_collective_layout --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/fabric.sbatch labs/11_collective_layout.py --profile small --layout flat
```

Repeat the unprofiled baseline and candidate after inspecting the trace. Instrumented artifacts are rejected by the comparison publisher.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing required full GPU (H100 or H200), peer path, InfiniBand port, rank or result is a failed prerequisite. Stop and inspect the per-lab job log. Do not force a transport or change cluster configuration to disguise a failed check. The owner must repair the supported runtime before another trial.

Publication failure is distinct from benchmark failure. Retain valid JSON and republish using the reviewed generation. Missing metrics remain unknown; failed ingestion does not mean the workload failed. A candidate need not be faster to teach a useful result.

## Takeaways and next step

Explain which measured observation supports your hypothesis, which alternative explanation remains, and whether the one-variable change should be kept. Repeat enough clean runs to expose variation, and report topology and workload limits with the conclusion.
