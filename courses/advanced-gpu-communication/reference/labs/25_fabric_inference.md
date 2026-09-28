# Lab 25: Compare request replicas with tensor-parallel decoding

Request replication assigns different requests to independent full-weight replicas. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed. The goal is a defensible explanation backed by correctness checks and repeated observations; a candidate is allowed to be slower.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/25_fabric_inference.json).

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Keep model, software, clocks and other workload activity fixed; preserve private artifacts for both runs.

## Concepts and code path

Request replication assigns different requests to independent full-weight replicas. Tensor parallelism splits each projection’s input dimension across GPUs; every decode step sums partial products before continuing. The global request count, width and generated length are identical. Both placements are compared with the same full-weight recurrent projection. This educational decode loop makes synchronization visible, but contains no tokenizer, attention KV cache or HTTP server, so its timings are not serving TTFT or end-user inter-token latency. The source writes a completed result only after its correctness checks pass. Measurement and publication run separately, so exporting evidence cannot distort the timed operation.

## Practice

On the login node, submit the baseline and candidate below. Save both job numbers and printed result paths.

```bash
python3 tools/submit_lab.py --lab 25_fabric_inference slurm/fabric.sbatch labs/25_fabric_inference.py --profile small --placement replicated
python3 tools/submit_lab.py --lab 25_fabric_inference slurm/fabric.sbatch labs/25_fabric_inference.py --profile small --placement tensor
```

Logs are created before submission under `results/25_fabric_inference/logs/<job>.out` and `.err`. A submitted job is not a completed result.

## Check your results

Wait for both jobs to complete successfully. Inspect the measured fields and correctness status; a failed check must be resolved before comparing performance.

```bash
sacct -j "${LAB_JOB_ID:?job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 25_fabric_inference --job "$LAB_JOB_ID"
```

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Fixed-request decode duration | `batch.median_ms` | s |
| Useful generated tokens per second | `tokens_per_second` | tokens/s |
| Maximum rank allocated memory | `peak_allocated_bytes` | bytes |

Select the two unprofiled result artifacts. The publisher checks equivalent parameters and allows only the named change. Repeated qualification uses no changed parameter.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 25_fabric_inference \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Set the time picker from **Experiment start** to **Experiment end**, then select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers** for telemetry. Summary panels show the selected pair; telemetry describes its actual job window.

## Investigate the behavior

In Systems, compare decode_projection with decode_all_reduce across ranks. Explain why tensor parallelism can lose on a small model even while splitting its weights. Grafana’s token rate counts each global request once, not once per rank. Peak allocated memory includes correctness reference buffers and is not a model-capacity bound. Independently repeat with the large workload profile as a separate comparison. Continue with Labs 32–34 for measured client latency and real server traces.

Capture separately from timing. Check exported statistics for every rank, then open representative `.nsys-rep` reports from each worker in Systems, loading large reports in small groups. Rank filenames retain the Slurm job and global rank; correlate matching phases across reports. Use a local-kernel exercise for Compute: replaying distributed collectives can stall their peers.

```bash
python3 tools/submit_lab.py --lab 25_fabric_inference --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/fabric.sbatch labs/25_fabric_inference.py --profile small --placement replicated
```

Repeat the unprofiled baseline and candidate after inspecting the trace. Instrumented artifacts are rejected by the comparison publisher.

For an actual model server, continue with Labs 32–34 in this course. Those experiments own server processes, client measurements, cache-transfer settings and serving dashboards. This projection exercise measures communication mechanics and must not be presented as production serving latency.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing required full GPU (H100 or H200), peer path, InfiniBand port, rank or result is a failed prerequisite. Stop and inspect the per-lab job log. Do not force a transport or change cluster configuration to disguise a failed check. The owner must repair the supported runtime before another trial.

Publication failure is distinct from benchmark failure. Retain valid JSON and republish using the reviewed generation. Missing metrics remain unknown; failed ingestion does not mean the workload failed. A candidate need not be faster to teach a useful result.

## Takeaways and next step

Explain which measured observation supports your hypothesis, which alternative explanation remains, and whether the one-variable change should be kept. Repeat enough clean runs to expose variation, and report topology and workload limits with the conclusion.
