# Lab 33: Test KV-cache-aware request routing

A key/value cache stores attention state for tokens already processed. When requests reuse prefixes, a router can send work to a worker holding useful cached blocks. Compare round-robin routing with Dynamo KV-aware routing on two aggregated TP8 workers. Keep the request corpus, prefix groups, concurrency, model and output lengths fixed so the routing decision is the only tuning control.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use the dedicated two-worker, sixteen-H100 cluster prepared in shared environment setup. Verify local NVLink/NVSwitch and inter-node InfiniBand readiness. Keep driver, software, allocation and other workloads fixed; the two one-GPU TCP workers cannot establish this fabric's performance. The `small` and `large` names select workload sizes, not optimization or profiling modes.

Reuse the joint vendor environment and pinned model prepared in
[Lab 32](32_dynamo_disaggregation.md). Source `env/vendor-environment.sh` in
this submission shell. Retain the complete model cache and repository metadata
for offline tokenizer lookup; do not substitute a weights-only copy.

## Concepts and code path

The serving runner enables prefix caching and publishes KV events from each worker. Four deterministic prefix families create reuse opportunities; each run warms its own fresh deployment before timing. KV-aware routing balances cache reuse and load, so lower TTFT is not guaranteed. Complete output and signature checks prevent a failed or changed response stream from becoming an apparent improvement.

Both workers enable `VLLM_BATCH_INVARIANT=1`, select FlashAttention 2 with `attention_config.flash_attn_version=2`, and set `custom_ops=["+rms_norm"]` and `pass_config.fuse_allreduce_rms=false` in the compilation configuration. Explicit RMSNorm selection preserves its batch-invariant CUDA implementation when compilation would otherwise select the native implementation. The pinned runtime can still select nondeterministic Hopper FlashAttention 3 and fused tensor-parallel all-reduce/RMSNorm paths under batch invariance. These explicit settings avoid those paths; compilation and CUDA graphs stay enabled. Keep these settings fixed across layouts, routing policies and concurrency levels, and require the output-equivalence check to pass before comparing performance. This reproducibility mode can change throughput relative to default vLLM execution. Seeded requests alone do not guarantee identical outputs across different batch shapes.

## Practice

`labs/33_dynamo_routing.py` runs repeated-prefix streaming requests against two TP8 replicas with round-robin or KV-aware routing. It checks complete streams and fixed token counts and writes client measurements, with server profiling separate from acceptance timing.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/33_dynamo_routing/logs/%j.out" \
  --error="$PWD/results/33_dynamo_routing/logs/%j.err" \
  slurm/vendor_job.sbatch \
  labs/33_dynamo_routing.py --profile small --model-dir "$MODEL_PATH" --router round-robin
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

For pair publication, confirm both completed job states and inspect the actual JSON paths. Set `BASELINE_RESULT` and `CANDIDATE_RESULT` to those artifacts, never to stdout or profiler reports.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/33_dynamo_routing/logs/$LAB_JOB_ID.out"
cat "results/33_dynamo_routing/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Require `COMPLETED` and exit code `0:0` for each job. Reading JSON is inspection,
not validation: check `lab_id`, `experiment.slurm_job_id`, correctness and
instrumentation fields. Retain every original/aggregate required by this lab.

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 33_dynamo_routing \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; reviewed current generation otherwise}"
```

| Dashboard panel | Measurement path | Display unit |
| --- | --- | --- |
| Median time to first content | `ttft_p50_ms` | s |
| P99 time to first content | `ttft_p99_ms` | s |
| Completed request rate | `requests_per_second` | reqps |

Select workspace and profile in Grafana. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match confirmation. Set the time picker to **Experiment start** through **Experiment end** and select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers**. Sampled utilization is context, not a per-kernel explanation or proof of transport selection.

## Investigate the behavior

### Workload variations

Submit the two unprofiled jobs from the login node, one after the other after completion, and retain their printed job numbers.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/33_dynamo_routing/logs/%j.out" \
  --error="$PWD/results/33_dynamo_routing/logs/%j.err" slurm/vendor_job.sbatch labs/33_dynamo_routing.py --profile small --model-dir "$MODEL_PATH" --router round-robin
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/33_dynamo_routing/logs/%j.out" \
  --error="$PWD/results/33_dynamo_routing/logs/%j.err" slurm/vendor_job.sbatch labs/33_dynamo_routing.py --profile small --model-dir "$MODEL_PATH" --router kv
```

Logs stay under `results/33_dynamo_routing/logs/`. A submission receipt is not a measurement; wait for successful completion before selecting artifacts.

Capture round-robin and KV-aware routing in separate diagnostic jobs. Wait for each capture to finish before submitting the next.

Compare client TTFT distributions with server prefill activity in a separate Systems capture. Inspect router and worker logs for event readiness; a selected router name is not evidence of cache hits. Independently increase concurrency in a new fixed pair and explain whether load balance begins to dominate prefix locality. Report both median and tail latency.

The private `requests.json` records `request_start_unix_ns` and
`request_end_unix_ns`; `measurement-window.json` bounds the measured cohort
after warmup. Check client/worker clock alignment before correlating these epoch
timestamps with server activity. Latency uses a monotonic clock. A request may
reach one replica, so do not require matching activity on both workers for every
individual request.

This coordinated diagnostic uses the native `sbatch` launcher to reserve both nodes and keep the coordinator on a worker. The lifecycle driver launches the visible `nsys profile` prefix on each GPU worker through `srun`; it also manages rendezvous, readiness and cleanup. `{report}` becomes a private per-rank path. Repeat with `--router kv` to capture the candidate. Put `--worker-prefix` last. Inspect the printed worker reports, then repeat the clean baseline for acceptance measurements.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=1 \
  --chdir="$PWD" --output="results/33_dynamo_routing/logs/capture-%j.out" \
  --error="results/33_dynamo_routing/logs/capture-%j.err" \
  slurm/vendor_job.sbatch labs/33_dynamo_routing.py --profile small --model-dir "$MODEL_PATH" --router round-robin --capture systems \
  --worker-prefix env -u DEBUGINFOD_URLS nsys profile \
  --trace=cuda,nvtx,osrt,nccl \
  --cuda-trace-scope=process-tree --sample=none \
  --discard-environment=true --force-overwrite=false --kill=none \
  --trace-fork-before-exec=true --cuda-graph-trace=node \
  --capture-range=cudaProfilerApi --capture-range-end=stop \
  --flush-on-cudaprofilerstop=false --wait=primary \
  '--output={report}'
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
