# Lab 31: Place hierarchical context-parallel communication

Context parallelism partitions a long token sequence across ranks while preserving attention over the full context. Hierarchical context parallelism uses different communication within and between nodes. Compare a flat sixteen-rank point-to-point pattern with an eight-by-two hierarchy, placing the inner eight ranks inside each NVLink domain and the outer exchange across InfiniBand.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/31_context_parallel.json).

Use the dedicated two-worker, sixteen-H100 cluster prepared in shared environment setup. Verify local NVLink/NVSwitch and inter-node InfiniBand readiness. Keep driver, software, allocation and other workloads fixed; the two one-GPU TCP workers cannot establish this fabric's performance. The `small` and `large` names select workload sizes, not optimization or profiling modes.

Prepare the [joint vendor runtime](../../README.md) once, then source
`env/vendor-environment.sh` in this submission shell. The installer requires
the Bridge, NIXLBench and Dynamo prerequisites together; installation alone does
not qualify both workers for this experiment.

## Concepts and code path

Both configurations retain sixteen context ranks, the same tokens, model, global batch and optimizer. The hierarchical layout uses a2a+p2p with sizes [8,2]; the flat layout uses p2p. Eight query groups support the inner split, and sequence lengths divide the context partition. Keep torchrun node-contiguous rank order. Compare final parameters to the flat reference before accepting the measured step distribution.

The recipe explicitly selects Bridge's sequential `single` dataloader and
seeded synthetic tokens. Context ranks split each sequence; they do not select
independent data samples. Keep the loader and seed fixed across both layouts.
The attention backend supplies causal masking. The dataset skips both creating
and transferring dense attention masks, keeping unused quadratic tensors out
of DataLoader shared memory.

## Practice

Submit the two unprofiled jobs from the login node, one after the other after completion, and retain their printed job numbers.

Select the prepared Bridge runtime before submission and create one reference with the baseline control. Export `CONTEXT_REFERENCE` as the printed `final-weights.pt` path after that job completes. Keep that same file for both measured runs.

```bash
export COURSE_PYTHON="$COURSE_BRIDGE_PYTHON"
export COURSE_TORCHRUN="$COURSE_BRIDGE_TORCHRUN"
python3 tools/submit_lab.py --lab 31_context_parallel slurm/fabric.sbatch labs/31_context_parallel.py --profile small --reference-only
```

```bash
python3 tools/submit_lab.py --lab 31_context_parallel slurm/fabric.sbatch labs/31_context_parallel.py --profile small --layout flat --reference "$CONTEXT_REFERENCE"
python3 tools/submit_lab.py --lab 31_context_parallel slurm/fabric.sbatch labs/31_context_parallel.py --profile small --layout hierarchical --reference "$CONTEXT_REFERENCE"
```

Logs stay under `results/31_context_parallel/logs/`. A submission receipt is not a measurement; wait for successful completion before selecting artifacts.

## Check your results

Confirm both completed job states and inspect the actual JSON paths. Set `BASELINE_RESULT` and `CANDIDATE_RESULT` to those artifacts, never to stdout or profiler reports.

```bash
sacct -j "${LAB_JOB_ID:?job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 31_context_parallel --job "$LAB_JOB_ID"
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 31_context_parallel \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; reviewed current generation otherwise}"
```

| Dashboard panel | Measurement path | Display unit |
| --- | --- | --- |
| Median context-parallel step | `step_seconds` | s |
| Processed tokens per second | `tokens_per_second` | tokens/s |
| Maximum parameter error | `parameter_max_abs_error` | none |

Select workspace and profile in Grafana. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match confirmation. Set the time picker to **Experiment start** through **Experiment end** and select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers**. Sampled utilization is context, not a per-kernel explanation or proof of transport selection.

## Investigate the behavior

Capture flat and hierarchical layouts in separate diagnostic jobs, using the same reference for both. Wait for each capture to finish before submitting the next.

In Systems correlate attention and communication lanes across ranks 0–7 and 8–15. Verify rank placement rather than assuming [8,2] names a physical topology. Compare the exposed cross-node tail for the two layouts. Independently vary workload profile in a new matched campaign; explain why additional hierarchy overhead may hurt a short sequence.

```bash
python3 tools/submit_lab.py --lab 31_context_parallel --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/fabric.sbatch labs/31_context_parallel.py --profile small --layout flat --reference "$CONTEXT_REFERENCE"
python3 tools/submit_lab.py --lab 31_context_parallel --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/fabric.sbatch labs/31_context_parallel.py --profile small --layout hierarchical --reference "$CONTEXT_REFERENCE"
```

Keep diagnostic captures separate from acceptance timings. For distributed work, retain each rank's report and placement record; compare the same application phase across ranks. Nsight Compute replay is inappropriate for live collectives: investigate a separately isolated local kernel when kernel-level evidence is needed.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing worker, vendor field, transport, completed request or correctness check is missing evidence, never zero performance. Read the lab's private job logs and fix the failing prerequisite before another trial. Do not change drivers, network configuration or registration modules inside an experiment.

Publication failure is separate from benchmark failure. Retain successful JSON artifacts and republish with the reviewed generation after ingestion is repaired. Vendor versions are qualification candidates until the designated runtime and sixteen-GPU live checks pass.

## Takeaways and next step

Explain what changed, which observation supports the mechanism, and which alternative explanation remains. Repeat matched unprofiled runs and describe variation; retain a slower candidate when it disproves the initial hypothesis. Finish with the independent investigation above and a bounded decision for this workload and topology.
