# Lab 04: Verify the two-node inference mechanics platform

Distributed inference requires correctly placed workers before model partitioning or serving behavior can be evaluated. This lab checks the two-node H100 allocation and a known NCCL collective without starting an inference engine. You will separate basic worker communication from model loading, endpoint readiness, request correctness, and service performance, each of which needs its own later evidence.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/04_inference_readiness.json).

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Use the Inference mechanics environment and the [cluster runbook](../cluster-smoke-test.md). Two distinct nodes must each expose one full H100. This lab is not the serving-container readiness check.

## Concepts and code path

The course launcher creates two ranks, and shared helpers validate the device and initialize NCCL. Placement information is exchanged and a predictable tensor reduction is checked. The example has no HTTP endpoint, tokenizer, model shards, request queue, or KV-cache manager.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Use the two-node launcher from this course directory. Keep placement diagnostics private and do not expose rendezvous or worker ports outside the cluster's approved private network.

```bash
umask 077
"$COURSE_PYTHON" labs/04_inference_readiness.py --help
python3 tools/submit_lab.py --lab 04_inference_readiness slurm/two_node.sbatch labs/04_inference_readiness.py --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 04_inference_readiness --job "$LAB_JOB_ID"
```

Require `two_nodes` and `nccl_all_reduce`, world size two, and two distinct hosts in private evidence. A passed collective does not prove a TensorRT-LLM or vLLM engine can load or serve the selected model.

Publication compares the recorded `world_size` and `distinct_host_count` in both runs. Keep both at two; a placement change invalidates the readiness comparison.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| World size | `world_size` | `none` |
| Distinct host count | `distinct_host_count` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 04_inference_readiness \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Draw the two worker/device pairs and explain why local rank is zero on each node. Name the additional initialization boundaries that a real distributed engine introduces beyond this communication group.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 04_inference_readiness --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/two_node.sbatch labs/04_inference_readiness.py --profile small
```

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Open both rank reports. Inspect lab_workload and the CUDA/NCCL all-reduce; this probe establishes collective readiness, not serving latency. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Incorrect placement, device visibility, or NCCL failures belong to the corresponding platform layer. Preserve the failing output and ask the cluster owner to resolve infrastructure issues instead of changing network or sharing settings.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Distributed readiness has multiple gates. Use this result for the TP and EP mechanics labs, then qualify the separate engine containers and their complete startup/request/shutdown workflows before claiming serving readiness.
