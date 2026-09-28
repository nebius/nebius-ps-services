# Lab 29: Measure NIXL GPU transfer progress

NVIDIA Inference Xfer Library, or NIXL, moves data between memory regions using transport backends. A progress thread advances transfer work independently of the calling thread. Use NIXLBench to test whether that thread changes latency and throughput for a fixed GPU-to-GPU transfer across two workers. This isolates a transport mechanism used by disaggregated inference.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/29_nixl_transfer.json).

Use the dedicated two-worker, sixteen-H100 cluster prepared in shared environment setup. Verify local NVLink/NVSwitch and inter-node InfiniBand readiness. Keep driver, software, allocation and other workloads fixed; the two one-GPU TCP workers cannot establish this fabric's performance. The `small` and `large` names select workload sizes, not optimization or profiling modes.

Prepare the [joint vendor runtime](../../README.md) once, then source
`env/vendor-environment.sh` in this submission shell. The installer requires
the Bridge, NIXLBench and Dynamo prerequisites together; installation alone does
not qualify both workers for this experiment.

## Concepts and code path

The pairwise scatter-gather benchmark uses UCX, GPU memory at both endpoints, WRITE operations and consistency checks. One GPU on each worker participates; sixteen GPUs are reserved to isolate the trial. Only the initiating rank prints the aggregate table. Preparation, posting and transfer times describe different phases; amortized item latency must not be relabeled as application token latency.

## Practice

Submit the two unprofiled jobs from the login node, one after the other after completion, and retain their printed job numbers.

```bash
python3 tools/submit_lab.py --lab 29_nixl_transfer slurm/vendor_job.sbatch labs/29_nixl_transfer.py --profile small --progress-thread off
python3 tools/submit_lab.py --lab 29_nixl_transfer slurm/vendor_job.sbatch labs/29_nixl_transfer.py --profile small --progress-thread on
```

Logs stay under `results/29_nixl_transfer/logs/`. A submission receipt is not a measurement; wait for successful completion before selecting artifacts.

## Check your results

Confirm both completed job states and inspect the actual JSON paths. Set `BASELINE_RESULT` and `CANDIDATE_RESULT` to those artifacts, never to stdout or profiler reports.

The artifact's `launcher_sha256` identifies the file selected by
`COURSE_NIXLBENCH`. With the supplied environment, this is the library-path
wrapper in `env/nixlbench`. Retain the installed native executable's hash and
source revision separately in the qualified runtime inventory; the wrapper's
identity alone does not establish which native build ran.

```bash
sacct -j "${LAB_JOB_ID:?job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 29_nixl_transfer --job "$LAB_JOB_ID"
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 29_nixl_transfer \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; reviewed current generation otherwise}"
```

| Dashboard panel | Measurement path | Display unit |
| --- | --- | --- |
| NIXL payload rate | `GBps` | Bps |
| Mean transfer phase | `transfer_us` | s |
| P99 transfer phase | `transfer_p99_us` | s |

Select workspace and profile in Grafana. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match confirmation. Set the time picker to **Experiment start** through **Experiment end** and select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers**. Sampled utilization is context, not a per-kernel explanation or proof of transport selection.

## Investigate the behavior

Inspect preparation_us, post_us, transfer_us and transfer_p99_us in the authoritative artifact. A faster transfer phase can be offset by thread scheduling or registration costs. Repeat the fixed comparison with the large workload; never compare different payload sizes as an optimization. These vendor phases and GPU/node telemetry are the evidence; CUDA-kernel replay is inapplicable to this UCX transfer.

Keep diagnostic captures separate from acceptance timings. For distributed work, retain each rank's report and placement record; compare the same application phase across ranks. Nsight Compute replay is inappropriate for live collectives: investigate a separately isolated local kernel when kernel-level evidence is needed.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 29_nixl_transfer --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/vendor_job.sbatch labs/29_nixl_transfer.py --profile small --progress-thread off
```

**Nsight Systems evidence:** Capture the actual vendor executable inside each allocated worker; preserve raw numerical output separately. Open rank0.nsys-rep and rank1.nsys-rep beside the private vendor output. Inspect UCX, CUDA API and OS runtime rows around buffer preparation and progress. GPU payload DMA may have no CUDA kernel event; use clean NIXL transfer_us and bandwidth for the network result. No course NVTX range is emitted by the vendor executable. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing worker, vendor field, transport, completed request or correctness check is missing evidence, never zero performance. Read the lab's private job logs and fix the failing prerequisite before another trial. Do not change drivers, network configuration or registration modules inside an experiment.

Publication failure is separate from benchmark failure. Retain successful JSON artifacts and republish with the reviewed generation after ingestion is repaired. Vendor versions are qualification candidates until the designated runtime and sixteen-GPU live checks pass.

## Takeaways and next step

Explain what changed, which observation supports the mechanism, and which alternative explanation remains. Repeat matched unprofiled runs and describe variation; retain a slower candidate when it disproves the initial hypothesis. Finish with the independent investigation above and a bounded decision for this workload and topology.
