# Cluster smoke-test runbook

**Hardware scope:** local GPU checks use one full, non-MIG H100. A qualified advanced cluster can supply one-GPU allocations. Distributed work follows the advanced course's separate fabric qualification.

This is a target-qualification checklist, not the lesson execution order.
Follow the [course](../index.html) for the learning route and complete each
exercise's relevant safety/setup gate before running it. Distributed and optional
checks qualify those paths; they are not prerequisites for earlier single-GPU
lessons that do not use them.

Run from this course root after the [Lab Guide](../../lab-guide.html#lab-preparation)
has prepared the available lab software. Check its completion summary for failures
or hardware skips affecting the selected lab. Launchers restore the saved runtime
automatically; [VERSIONS.md](../VERSIONS.md) describes its scope.
Preparation creates private log directories before Slurm opens its output.
For these read-only local dependency checks, select the ordinary lab runtime:

```bash
source tools/course_env.sh 35_inference_basics --lab
"$COURSE_PYTHON" -m pip check
python3 tools/validate_course.py
```

Raw model data, prompts, outputs, Slurm logs, server logs, metrics, and profiler
reports remain private. Fill in [benchmark-record.md](benchmark-record.md) and
follow [evidence-security.md](evidence-security.md) before sharing aggregates.

## Allocation and artifact identity

```bash
sbatch slurm/16_model_artifact_audit.sbatch --workload small
```

Require a full non-MIG H100, compute capability 9.0, immutable model/tokenizer
identity, compatible configuration and weights, and the stated remote-code
policy. Separate artifact resolution, CPU staging, GPU loading, warm-up, and
readiness. Reaching readiness does not establish request correctness.
Lab 16 checks the local H100 and needs online Hub metadata access even when
model artifacts are cached.

## Generation and cache semantics

```bash
sbatch slurm/35_inference_basics.sbatch --device cuda
sbatch slurm/08_kv_cache.sbatch --workload small
sbatch slurm/09_hf_prefill_decode.sbatch --workload small
sbatch slurm/17_sampling_semantics.sbatch --workload small
sbatch slurm/18_padding_bucketing.sbatch --workload small
sbatch slurm/23_speculative_decoding.sbatch --workload small
sbatch slurm/24_sdpa_attention.sbatch --workload small
```

Lab 35 can first be studied with `--device cpu` without downloads or an engine.
Require the declared token sequence, EOS/length stopping and unchanged
parameters. Its fixed table contains no attention or KV cache and its CPU
result does not qualify the H100 path or serving performance.

Require cache/no-cache agreement and fixed decoding semantics. Lab 09 checks
the requested new-token count, attention mask, cache position, and advancing
cache length. The synthetic speculative lab must match the target-greedy
sequence and exercise partial rejection, recovery, and full-acceptance bonus
paths. High acceptance must reduce target forward calls, while the
low-acceptance control must demonstrate why proposal overhead can remove
that benefit. It is not real-engine speedup evidence. SDPA comparisons hold mask,
shape, dtype, and correctness tolerances fixed.

## Workload, capacity, and scheduling mechanics

```bash
sbatch slurm/25_workload_metrics.sbatch --workload small
sbatch slurm/26_kv_capacity.sbatch --workload small
sbatch slurm/27_paged_kv.sbatch --workload small
sbatch slurm/28_continuous_batching.sbatch --workload small
sbatch slurm/29_quantization.sbatch --workload small
```

Define ISL/OSL, arrival pattern, concurrency, TTFT/ITL timestamp endpoints and AIPerf aggregation conventions, and output
tokens for the later live campaign. Lab 25 itself measures synthetic projection
and recurrent operator work, with batch rows rather than service requests and
no generated tokens or TTFT/ITL. Check MHA/GQA/MQA arithmetic, allocation/recycling invariants, and
scheduler semantics before live-engine comparisons. Lab 29 must pass its
declared tensor-error gates; inspect its logical byte counts and timing
observations separately. Actual resident-memory savings, service latency and
model quality require a qualified engine experiment; smaller weights alone do
not establish a faster service.

Lab 36 is a separate local policy gate with no GPU or serving engine requirement:

```bash
"$COURSE_PYTHON" labs/36_kv_tiering.py --storage-gbps 2 --output-dir results/tiering-fast
"$COURSE_PYTHON" labs/36_kv_tiering.py --storage-gbps 0.2 --output-dir results/tiering-slow
```

Hold the arrival sequence and capacities fixed. Verify TTL expiry, LRU
demotion, namespace isolation and restore-versus-recompute decisions using the
complete guide's restart and revision controls. Record modeled foreground and
write costs separately. Neither modeled hits nor assumed bandwidth prove real
KV persistence, GPUDirect Storage activation, TTFT or storage performance.

## One-GPU serving and streaming

```bash
sbatch slurm/10_vllm_offline.sbatch --workload small
sbatch slurm/11_serving_client.sbatch
sbatch slurm/15_streaming_client.sbatch
sbatch slurm/20_prefix_cache_client.sbatch
```

Use the launchers' documented model and immutable revision inputs. Keep cold
startup separate from steady state. Offline vLLM must aggregate all measured
requests, prompt and output tokens, and the full measured-window throughput.
Serving launchers retain Prometheus snapshots and request failures. For prefix
A/B, compare prompt-token cohorts within the lab's declared 0.8–1.2 median-count
ratio and report the actual ratio. Inspect cache queries/hits rather than
assuming visible repeated text guarantees reuse. Streaming chunks do not
necessarily correspond one-to-one with model tokens.

## Engine qualification and causal report

Inspect the specialized launcher selections, submit the introductory AIPerf
comparison, and run the local capstone trials:

```bash
bash slurm/30_engine_profile.sbatch --experiment trtllm --help
sbatch slurm/15_streaming_client.sbatch --experiment aiperf --workload small
bash slurm/dynamo_disaggregated_preflight.sbatch --help
bash slurm/33_speculative_engine_client.sbatch --help
sbatch slurm/32_inference_capstone.sbatch --experiment trials --workload small
```

Lab 30 owns TensorRT-LLM/Triton profiling; Lab 15’s `--experiment aiperf`
route owns the introductory AIPerf comparison from Lesson 7. The submission
above starts the pinned default model on loopback, runs a bounded streaming
profile, and stops the server. It needs the prepared serving runtime. The default
Lab 15 submission uses the supplied streaming client. The separate Dynamo preflight checks conditional qualification. Real-engine speculative decoding is optional
and requires compatible target/draft artifacts. Dynamo disaggregation remains
an advanced exercise with capacity, transport, and routing prerequisites.

Keep three independent attention-capstone records with matching shapes,
numerical checks, timing distributions, memory observations and a
profiler-backed explanation. For a separately qualified serving campaign, also
retain throughput/latency curves, queue/KV/preemption evidence, errors and
quality checks. Preserve the rejected hypothesis and counterbalance
baseline/candidate trial order. Record
unsupported engine or topology gates as pending rather than substituting
simulation or CPU mechanics for live serving proof.

## Distributed practice

Use [Advanced Labs](../../advanced-gpu-communication/index.html) for placement,
transport, collective and application checks on its qualified two-worker,
sixteen-H100 fabric. Follow its lab-specific acceptance criteria; two one-GPU
TCP workers do not establish fabric performance. These checks are not gates
for the local experiments above.
