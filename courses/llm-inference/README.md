# LLM Inference

## Hardware routes

The **base route** uses two workers with one H100 each. Its TCP/IP inter-node path is not representative of GPU-fabric optimization; run single-GPU exercises there.

Distributed practical work now belongs to [Advanced Labs: Multi-GPUs Multi-Nodes communication optimization](../advanced-gpu-communication/index.html). That course requires a qualified two-worker, sixteen-H100 cluster, which can also run the local labs with one-GPU allocations. The conceptual lessons here remain useful prerequisites.

Each native submission block prepares private log directories before calling `sbatch`; Slurm writes `results/<lab>/logs/<job>.out` and `.err`. Result JSON remains the authoritative experiment record. `small` and `large` select workload presets, independently of the baseline/candidate choice. Qualification, modeling and fixed server experiments can use identical effective parameters in both profiles; read the lab guide and result configuration before comparing them.

The optional AIPerf campaign referenced by Lab 30 belongs to Lab 15; submit
`slurm/15_streaming_client.aiperf.sbatch` using Lab 15's native submission block and private log
directory so its logs and serving artifacts retain the same lab identity.
AIPerf writes native benchmark exports; the Lab 15 dashboard and course
publisher use the separate streaming client's JSON results.
The streaming client uses finite closed-loop concurrency and reports median/p90
latency and requests/s. Its results do not establish open-loop overload, token
throughput or SLO goodput.
Labs 33 and 34 publish paired greedy-equivalence evidence to their dashboards;
read their latency and throughput measurements from the native AIPerf exports.
Lab 34's launcher shares one run ID across its campaign directory, server
processes and both policies' digest records.
Lab 20 fixes batch-invariant execution and the `TRITON_ATTN` attention backend
for both cache policies and all correctness and timing phases. Its exact
greedy-output gate remains required before performance measurements.

Start with [shared environment setup](../README.md#how-to-set-up-the-lab) to prepare the cluster, course runtime, Nsight tools, private Grafana, and readiness checks.

Read [Using GPU performance tools](../gpu-performance-tools/index.html) before the first experiment. Every lab includes its own Grafana dashboard, local capture commands, a correctness gate, and a selected-result comparison. Install the shared tools once in shared environment setup and keep `small` and `large` as separate workload campaigns.

## Serving runtime preparation

On the login node, after the shared Python setup, select
`COURSE=llm-inference` and work from `~/courses/llm-inference`.
The mechanics environment uses `requirements-mechanics.txt`.

**Inference serving:** additionally set `VLLM_IMAGE_DIGEST` and `AIPERF_IMAGE_DIGEST`
to qualified OCI digests and `COURSE_CONTAINER_RUNNER` to the absolute shared
`slurm/container_runner.example.sh` path. Require Apptainer on both workers.
The runner mounts managed Nsight packages and activation; qualify captures inside
each image. Prepare the separate lightweight serving client and save its settings:

```bash
python3.12 -m venv "$HOME/courses/.venvs/llm-inference-serving"
export COURSE_SERVING_PYTHON="$HOME/courses/.venvs/llm-inference-serving/bin/python"
"$COURSE_SERVING_PYTHON" -m pip install -r requirements-serving.txt
declare -p COURSE_SERVING_PYTHON VLLM_IMAGE_DIGEST AIPERF_IMAGE_DIGEST COURSE_CONTAINER_RUNNER \
  >> "$HOME/courses/.runtime/$COURSE.sh"
```

Before serving-client jobs, use `export COURSE_PYTHON="$COURSE_SERVING_PYTHON"`.
Restore `source "$HOME/courses/.runtime/$COURSE.sh"` before returning to mechanics labs.

## Course guide

[Read the complete course](index.html) ·
[Glossary](GLOSSARY.md) ·
[Versions and environment](VERSIONS.md) ·
[Cluster small runbook](reference/cluster-smoke-test.md) ·
[Benchmark worksheet](reference/benchmark-record.md) ·
[Lab mechanisms and evidence](reference/lab-mechanisms.md)

Estimated guided time: **52 hours**, for the conceptual and local practical route; provisioning and queue time are excluded.

## Learning order

Audit artifacts before loading them, follow generation and fix sampling semantics, then calculate cache capacity and define the workload. Learn basic engine lifecycle before client metrics or live policy experiments. Establish scheduling and an uncompressed attention baseline before quantization, speculation, parallelism and advanced serving.

Use each lesson’s Objective and Practice for the current activity; the
readiness checkpoints below help you decide when to continue. Lab numbers identify files; follow lesson order rather
than running every lab numerically. Previews and optional branches are labeled.

## Getting started

The HTML course keeps diagrams beside the relevant explanation in a wide,
responsive layout. Each diagram fits the page and retains a caption and
accessible SVG description. Each lab starts with a concise purpose and explains the needed concepts,
code structure, supported experiments, result checks, investigation,
troubleshooting and takeaways locally. Use the side-panel contents to
jump between topics.

Take GPU Fundamentals and GPU Performance Optimization first. Use a small
mechanics environment for PyTorch/Transformers labs and a separate lightweight
serving-client/test environment. vLLM, TensorRT-LLM, Triton, AIPerf, and Dynamo
run through immutable container digests and a reviewed site container runner.

The lessons follow text and token mechanics through production serving,
including phase-aware memory, queueing, caches, parallelism, and
agentic-serving behavior.

Prepare and restore the runtime using the [shared guide](../README.md#how-to-run-the-labs).

Example mechanics run:

Dependency installation alone does not establish target qualification.
Before submitting any job, use the approved environment or engine image
identity described in [Versions and environment](VERSIONS.md) and complete the
relevant [cluster small gates](reference/cluster-smoke-test.md). Keep the
mechanics and serving-client environments separate. Set the submitting shell's
file mask before every submission session so early scheduler output stays private.

After Lesson 2 and [Lab 16’s artifact audit](reference/labs/16_model_artifact_audit.md), follow [Lab 09’s phase-measurement guide](reference/labs/09_hf_prefill_decode.md). Run from this course root on shared storage. Select the mechanics interpreter
explicitly so the active serving-client environment cannot leak into the job;
the interpreter path must exist on the allocated node.

```bash
COURSE_PYTHON="$HOME/courses/.venvs/llm-inference/bin/python" \
sbatch --chdir="$PWD" \
  --output="$PWD/results/09_hf_prefill_decode/logs/%j.out" \
  --error="$PWD/results/09_hf_prefill_decode/logs/%j.err" slurm/09_hf_prefill_decode.sbatch --workload small
```

Pinned engine profiles remain pending until their images, model revisions,
driver compatibility, readiness, and H100 behavior are verified.
Lab 30 also requires a nonempty generated-text string in the declared response
field; a healthy endpoint or a non-text value is insufficient.
Its OpenAI and Triton launchers each own server startup, the bounded client
probe, and cleanup inside one Slurm allocation. The OpenAI launcher uses the
prepared vLLM image and cached pinned model; it does not download model assets.
The Triton launcher initializes one MPI rank with the prepared image's
`mpirun` before starting `tritonserver`, within the same allocated GPU task.
MPI's `--oversubscribe` permits the backend's spawned worker within that existing
allocation when the single launcher rank has occupied its advertised task slot.
Both executables and the backend's Python dependencies must be available in
that image; the launcher does not install them.
The prepared runner must also provide writable, job-isolated module and compiler
caches. When model assets are mounted read-only, set `HF_MODULES_CACHE` to a
separate writable directory so TensorRT-LLM can acquire its configuration lock;
keep the model assets and offline settings unchanged.
It disables configuration auto-completion, so the prepared repository must
declare its complete batch and transaction-policy settings explicitly, matching
the reviewed model YAML. See [Lab 30](reference/labs/30_engine_profile.md).

After Lesson 6, follow [Lab 10’s offline generation guide](reference/labs/10_vllm_offline.md) and run through that same immutable boundary:

```bash
VLLM_IMAGE_DIGEST='docker://registry/image@sha256:DIGEST' \
COURSE_CONTAINER_RUNNER=slurm/container_runner.example.sh \
sbatch --chdir="$PWD" \
  --output="$PWD/results/10_vllm_offline/logs/%j.out" \
  --error="$PWD/results/10_vllm_offline/logs/%j.err" slurm/10_vllm_offline.sbatch --workload small
```

After Lessons 6–9 and qualification of both vLLM and AIPerf images, follow
[Lab 34’s live-engine guide](reference/labs/34_policy_equivalence_client.md) for the core continuous-
batching/chunked-prefill comparison with three fresh-server correctness probes
and three fresh-server AIPerf trials per policy, each in counterbalanced order:

The launcher holds batch-invariant execution, `TRITON_ATTN`, and eager mode
fixed across both policies and phases. Keep the exact output gate; these
controlled scheduling measurements do not qualify compiled serving performance.

```bash
VLLM_IMAGE_DIGEST='docker://registry/vllm@sha256:DIGEST' \
AIPERF_IMAGE_DIGEST='docker://registry/aiperf@sha256:DIGEST' \
COURSE_CONTAINER_RUNNER=slurm/container_runner.example.sh \
sbatch --chdir="$PWD" \
  --output="$PWD/results/34_policy_equivalence_client/logs/%j.out" \
  --error="$PWD/results/34_policy_equivalence_client/logs/%j.err" slurm/34_policy_equivalence_client.sbatch
```

After Lesson 13, follow [Lab 33’s speculative-decoding guide](reference/labs/33_speculative_engine_client.md) and qualify a compatible target/draft pair before the profile;
the launcher requires immutable model revisions and rejects paired greedy
outputs whose private digests differ:

```bash
VLLM_IMAGE_DIGEST='docker://registry/vllm@sha256:DIGEST' \
AIPERF_IMAGE_DIGEST='docker://registry/aiperf@sha256:DIGEST' \
COURSE_CONTAINER_RUNNER=slurm/container_runner.example.sh \
sbatch --chdir="$PWD" \
  --output="$PWD/results/33_speculative_engine_client/logs/%j.out" \
  --error="$PWD/results/33_speculative_engine_client/logs/%j.err" slurm/33_speculative_engine_client.sbatch TARGET TARGET_REVISION DRAFT DRAFT_REVISION
```

Actual distributed serving practice is in the advanced course, where Dynamo experiments own their workers, discovery, model revision, client measurements and server captures.

The final mechanics capstone uses `slurm/32_inference_capstone.trials.sbatch`. It
launches three fresh processes, alternates variant order, validates correctness,
and writes one scoped aggregate keep/reject record; it makes no serving claim.
Aggregation retains the observed GPU family in its claim and rejects trials
with different hardware or software environments.

Capstone aggregation requires `experiment.instrumented` to be exactly `false`
in every input and rejects explicit diagnostic timing. Missing or malformed
provenance requires a fresh unprofiled trial; no aggregate is written from
rejected inputs.

`slurm/container_runner.example.sh` shows the digest-to-runtime boundary with
Apptainer. Review it for the site, place it on a shared filesystem, and set
`COURSE_CONTAINER_RUNNER` plus the launcher-specific `*_IMAGE_DIGEST` variable.

Lab 08 performs its cache construction, correctness checks and measurements in
inference mode. Non-finite prompt or continuation comparisons reject the run
before timings or successful results are recorded.

## Begin with the concepts

Lab 35 is a download-free CPU introduction to fixed-parameter token generation; use `"$COURSE_PYTHON" labs/35_inference_basics.py --device cpu` in the mechanics environment. Its explicit `--device cuda` path is for an allocated H100. This is not an attention or serving benchmark.

## Continue learning

After the core course, use [Where to Go Next](NEXT-STEPS.md) for optional
reading on current technologies and advanced concepts. Each entry
includes a study question, official sources and hardware or maturity limits;
these directions do not change the required labs or environment.

## Cache retention practice

[Lab 36](reference/labs/36_kv_tiering.md) teaches tier capacity, idle TTL, LRU
eviction, restoration versus recomputation, state lost on restart and
cache invalidation after identity changes. It
runs on CPU without an engine or storage access. Its cost model prepares the
questions for a real cache campaign; it does not measure TTFT or qualify GDS.

Compute captures for Labs 09, 17 and 23 select an annotated model or generation operation and a matrix kernel, excluding input/model initialization. Each guide states the first-invocation scope; these single-kernel diagnostics do not supply complete decode or speculative-path counters. Lab 16 requires online Hub metadata access even when artifacts are cached; use its lab-specific environment rather than changing other offline runs.

Nsight Systems capture commands and report views are assigned per lab in `reference/observability.json` and repeated in each lab guide. Shared setup imports each course dashboard directory once; CPU-only and protocol-only results omit unrelated GPU telemetry. Explicit profiling exceptions explain which evidence to use instead. Keep captures separate from the unprofiled result pair.

The chunked-prefill campaign captures each correctness probe and each AIPerf
trial separately. Each server starts capture after readiness and stops it after
that trial's requests. Check both policy variants for request activity in their
reports before interpreting the diagnostic campaign.

Offline vLLM profiler commands use `--in-process` so the `vllm_generate` NVTX range contains the single-GPU engine launches. This diagnostic mode excludes construction and warmup from the selected range; use the default engine process mode for clean throughput comparisons. See [Lab 10](reference/labs/10_vllm_offline.md).

Lab 10's Compute command requests 256 GiB of host RAM with
`SBATCH_MEM_PER_NODE=262144`, because kernel replay can back up the engine's
device allocations in system memory. The GPU count and workload stay fixed.

## Readiness checkpoints

After Lesson 5, freeze a model, sampling contract, KV estimate and workload matrix. After Lesson 7, distinguish client latency from model-forward timing and run the introductory AIPerf profile before policy A/B campaigns. After Lesson 12, compare scheduling/backend/quantization changes one at a time. Dynamo disaggregation is an optional advanced branch, never a core completion gate.

Every lesson starts with its **Objective**, teaches definitions and mechanisms
in **How it works**, links its **Practice**, summarizes the teaching in **Mental model**, then provides **Where to Go Next** and a **Glossary**. The linked guides integrate
worked examples in **Concepts and code path** and concise commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Keep mechanics and serving evidence separate. The capstone requires at least three equivalent-work trials and an honest quality/latency/memory decision; advanced engines require their own target qualification.
