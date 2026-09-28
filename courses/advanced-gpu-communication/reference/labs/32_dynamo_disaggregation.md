# Lab 32: Compare aggregated and disaggregated Dynamo serving

Prefill processes input tokens and creates the key/value cache; decode repeatedly generates new tokens using that cache. NVIDIA Dynamo can place these phases on different workers and transfer cache data through NIXL. Compare two aggregated eight-GPU replicas against one eight-GPU prefill worker plus one eight-GPU decode worker, holding the total allocation and request corpus fixed.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/32_dynamo_disaggregation.json).

Use the dedicated two-worker, sixteen-H100 cluster prepared in shared environment setup. Verify local NVLink/NVSwitch and inter-node InfiniBand readiness. Keep driver, software, allocation and other workloads fixed; the two one-GPU TCP workers cannot establish this fabric's performance. The `small` and `large` names select workload sizes, not optimization or profiling modes.

Prepare the course's joint vendor runtime once using the
[advanced course runtime instructions](../../README.md). Dynamo requires CUDA 13
compiler tools and a CUDA-aware UCX/RDMA stack selected by `DYNAMO_UCX_PREFIX`;
its joint NIXL/NIXL-EP build must use that same stack. Keep it separate from
standalone NIXLBench's UCX installation.

The job owns a private etcd process on the first worker. Worker hostnames must
resolve to reachable IPv4 addresses; etcd binds that address and advertises the
hostname. Readiness failure stops the experiment before request traffic.

**Login node, from `~/courses/advanced-gpu-communication`:** prepare the pinned
model once for Labs 32–34, then retain the exported path:

```bash
source env/vendor-environment.sh
export MODEL_PATH="$("$COURSE_DYNAMO_PYTHON" -c 'from huggingface_hub import snapshot_download; print(snapshot_download("Qwen/Qwen3-8B", revision="b968826d9c46dd6066d109eabc6255188de91218"))')"
declare -p MODEL_PATH >> "$HOME/courses/.runtime/$COURSE.sh"
```

Keep the complete Hugging Face cache snapshot, including repository metadata.
Lab 34 uses the pinned repository ID and revision for offline tokenizer lookup;
copying weights and tokenizer files alone is insufficient.

## Concepts and code path

The runner owns private job-scoped discovery, two vLLM workers and a loopback frontend. Both layouts use the same pinned Qwen3-8B BF16 weights, TP8 per worker and deterministic requests. The disaggregated path uses NixlConnector. Fixed output counts and complete streams establish delivery validity; identical output signatures are required for comparison. This is not a model-quality evaluation. TTFT includes queueing and transfer effects, not only GPU prefill.

Both workers enable `VLLM_BATCH_INVARIANT=1`, select FlashAttention 2 with `attention_config.flash_attn_version=2`, and set `custom_ops=["+rms_norm"]` and `pass_config.fuse_allreduce_rms=false` in the compilation configuration. Explicit RMSNorm selection preserves its batch-invariant CUDA implementation when compilation would otherwise select the native implementation. The pinned runtime can still select nondeterministic Hopper FlashAttention 3 and fused tensor-parallel all-reduce/RMSNorm paths under batch invariance. These explicit settings avoid those paths; compilation and CUDA graphs stay enabled. Keep these settings fixed across layouts, routing policies and concurrency levels, and require the output-equivalence check to pass before comparing performance. This reproducibility mode can change throughput relative to default vLLM execution. Seeded requests alone do not guarantee identical outputs across different batch shapes.

## Practice

Submit the two unprofiled jobs from the login node, one after the other after completion, and retain their printed job numbers.

```bash
python3 tools/submit_lab.py --lab 32_dynamo_disaggregation slurm/vendor_job.sbatch labs/32_dynamo_disaggregation.py --profile small --model-dir "$MODEL_PATH" --layout aggregated
python3 tools/submit_lab.py --lab 32_dynamo_disaggregation slurm/vendor_job.sbatch labs/32_dynamo_disaggregation.py --profile small --model-dir "$MODEL_PATH" --layout disaggregated
```

Logs stay under `results/32_dynamo_disaggregation/logs/`. A submission receipt is not a measurement; wait for successful completion before selecting artifacts.

## Check your results

Confirm both completed job states and inspect the actual JSON paths. Set `BASELINE_RESULT` and `CANDIDATE_RESULT` to those artifacts, never to stdout or profiler reports.

```bash
sacct -j "${LAB_JOB_ID:?job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 32_dynamo_disaggregation --job "$LAB_JOB_ID"
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 32_dynamo_disaggregation \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; reviewed current generation otherwise}"
```

| Dashboard panel | Measurement path | Display unit |
| --- | --- | --- |
| P99 time to first content | `ttft_p99_ms` | s |
| Generated tokens per second | `tokens_per_second` | tokens/s |
| P99 streamed chunk gap | `chunk_gap_p99_ms` | s |

Select workspace and profile in Grafana. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match confirmation. Set the time picker to **Experiment start** through **Experiment end** and select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers**. Sampled utilization is context, not a per-kernel explanation or proof of transport selection.

## Investigate the behavior

Capture both aggregated and disaggregated layouts in separate diagnostic jobs. Inspect NIXL cache-transfer activity in the disaggregated capture; aggregated replicas do not perform that cross-worker cache transfer. Wait for each capture to finish before submitting the next.

Capture the actual GPU servers with --capture systems in a separate run. Inspect prefill, decode, NCCL and transfer activity in the two server reports, alongside client request timestamps. A chunk can contain several tokens; chunk-gap p99 is not token-gap p99. Independently repeat the fixed-layout comparison at another concurrency in a new matched campaign; explain the cache-transfer break-even point.

Use the private `requests.json` fields `request_start_unix_ns` and
`request_end_unix_ns`, and `measurement-window.json` for the measured cohort's
bounds after warmup. Check clock alignment between the client and both workers
before comparing epoch timestamps with the server traces. Client latency uses
a monotonic clock; a clock offset must not be interpreted as a transfer delay.

```bash
python3 tools/submit_lab.py --lab 32_dynamo_disaggregation slurm/vendor_job.sbatch labs/32_dynamo_disaggregation.py --profile small --model-dir "$MODEL_PATH" --layout aggregated --capture systems
python3 tools/submit_lab.py --lab 32_dynamo_disaggregation slurm/vendor_job.sbatch labs/32_dynamo_disaggregation.py --profile small --model-dir "$MODEL_PATH" --layout disaggregated --capture systems
```

Keep diagnostic captures separate from acceptance timings. For distributed work, retain each rank's report and placement record; compare the same application phase across ranks. Nsight Compute replay is inappropriate for live collectives: investigate a separately isolated local kernel when kernel-level evidence is needed.

**Nsight Systems evidence:** Capture the owned GPU server and its worker descendants while the client supplies requests. Open the server .nsys-rep and expand the GPU worker process tree, CUDA streams and runtime NVTX rows. Inspect the request interval after readiness; client-only activity or startup-only kernels are not sufficient. Compare launch gaps and prefill/decode activity against the clean client latency panels. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

The lab starts CUDA collection through both workers after readiness and stops
collection before shutting down the engines. Both control responses must confirm
success. It waits up to 180 seconds for both reports before stopping the servers.
Missing or empty reports fail the job; inspect request-phase GPU activity and
profiler warnings in both reports before accepting the capture.

## If something goes wrong

A missing worker, vendor field, transport, completed request or correctness check is missing evidence, never zero performance. Read the lab's private job logs and fix the failing prerequisite before another trial. Do not change drivers, network configuration or registration modules inside an experiment.

Publication failure is separate from benchmark failure. Retain successful JSON artifacts and republish with the reviewed generation after ingestion is repaired. Vendor versions are qualification candidates until the designated runtime and sixteen-GPU live checks pass.

## Takeaways and next step

Explain what changed, which observation supports the mechanism, and which alternative explanation remains. Repeat matched unprofiled runs and describe variation; retain a slower candidate when it disproves the initial hypothesis. Finish with the independent investigation above and a bounded decision for this workload and topology.
