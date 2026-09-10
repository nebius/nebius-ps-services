# LLM Inference

## Course guide

[Read the complete course](index.html) ·
[Syllabus](SYLLABUS.md) ·
[Glossary](GLOSSARY.md) ·
[Versions and environment](VERSIONS.md) ·
[Cluster smoke runbook](reference/cluster-smoke-test.md) ·
[Benchmark worksheet](reference/benchmark-record.md) ·
[Lab mechanisms and evidence](reference/lab-mechanisms.md)

Estimated guided time: **47 hours**.

## Learning order

Audit artifacts before loading them, follow generation and fix sampling semantics, then calculate cache capacity and define the workload. Learn basic engine lifecycle before client metrics or live policy experiments. Establish scheduling and an uncompressed attention baseline before quantization, speculation, parallelism and advanced serving.

Use the [lesson-by-lesson syllabus](SYLLABUS.md) for the current activity and
readiness checkpoints. Lab numbers identify files; follow lesson order rather
than running every lab numerically. Previews and optional branches are labeled.

## Getting started

The HTML course keeps diagrams beside the relevant explanation in a wide,
responsive layout. Each diagram fits the page and retains a caption and
accessible SVG description. Each lab explains setup, concepts, code structure,
supported experiments, result checks, investigation, troubleshooting, and
takeaways, with links to its related lessons. Use the side-panel contents to
jump between topics.

Take GPU Fundamentals and GPU Performance Optimization first. Use a small
mechanics environment for PyTorch/Transformers labs and a separate lightweight
serving-client/test environment. vLLM, TensorRT-LLM, Triton, AIPerf, and Dynamo
run through immutable container digests and a reviewed site container runner.

The lessons follow text and token mechanics through production serving,
including phase-aware memory, queueing, caches, parallelism, and
agentic-serving behavior.

```bash
python3 -m venv .venv-mechanics
source .venv-mechanics/bin/activate
python -m pip install -r requirements-mechanics.txt
python tools/validate_course.py

deactivate
python3 -m venv .venv-serving
source .venv-serving/bin/activate
python -m pip install -r requirements-serving.txt
```

Example mechanics run:

The installed requirements above are candidates, not target qualification.
Before submitting any job, use the approved environment or engine image
identity described in [Versions and environment](VERSIONS.md) and complete the
relevant [cluster smoke gates](reference/cluster-smoke-test.md). Keep the
mechanics and serving-client environments separate. Set the submitting shell's
file mask before every submission session so early scheduler output stays private.

After Lesson 2 and [Lab 16’s artifact audit](reference/labs/16_model_artifact_audit.md), follow [Lab 09’s phase-measurement guide](reference/labs/09_hf_prefill_decode.md). Run from this course root on shared storage. Select the mechanics interpreter
explicitly so the active serving-client environment cannot leak into the job;
the interpreter path must exist on the allocated node.

```bash
umask 077
COURSE_PYTHON="$PWD/.venv-mechanics/bin/python" \
sbatch slurm/single_gpu.sbatch labs/09_hf_prefill_decode.py --profile smoke
```

Pinned engine profiles remain pending until their images, model revisions,
driver compatibility, readiness, and H100 behavior are verified.

After Lesson 6, follow [Lab 10’s offline generation guide](reference/labs/10_vllm_offline.md) and run through that same immutable boundary:

```bash
umask 077
VLLM_IMAGE_DIGEST='docker://registry/image@sha256:DIGEST' \
COURSE_CONTAINER_RUNNER=slurm/container_runner.example.sh \
sbatch slurm/vllm_offline.sbatch --profile smoke
```

After Lessons 6–9 and qualification of both vLLM and AIPerf images, follow
[Lab 34’s live-engine guide](reference/labs/34_policy_equivalence_client.md) for the core continuous-
batching/chunked-prefill comparison with three independent engine restarts per
policy and counterbalanced order:

```bash
umask 077
VLLM_IMAGE_DIGEST='docker://registry/vllm@sha256:DIGEST' \
AIPERF_IMAGE_DIGEST='docker://registry/aiperf@sha256:DIGEST' \
COURSE_CONTAINER_RUNNER=slurm/container_runner.example.sh \
sbatch slurm/vllm_chunked_prefill_ab.sbatch
```

After Lesson 13, follow [Lab 33’s speculative-decoding guide](reference/labs/33_speculative_engine_client.md) and qualify a compatible target/draft pair before the profile;
the launcher requires immutable model revisions and rejects paired greedy
outputs whose private digests differ:

```bash
umask 077
VLLM_IMAGE_DIGEST='docker://registry/vllm@sha256:DIGEST' \
AIPERF_IMAGE_DIGEST='docker://registry/aiperf@sha256:DIGEST' \
COURSE_CONTAINER_RUNNER=slurm/container_runner.example.sh \
sbatch slurm/vllm_speculative_ab.sbatch TARGET TARGET_REVISION DRAFT DRAFT_REVISION
```

The two-node launcher takes an explicit `pp`, `tp`, or `ep` mode. The `ep`
mode intentionally has no default model: supply a reviewed MoE artifact and
immutable revision. Every mode restarts its two-node engine for three bounded
throughput and streaming trials; two one-GPU nodes teach communication
mechanics, not production-scale parallel performance.

The final mechanics capstone uses `slurm/capstone_three_trials.sbatch`. It
launches three fresh processes, alternates variant order, validates correctness,
and writes one scoped aggregate keep/reject record; it makes no serving claim.

`slurm/container_runner.example.sh` shows the digest-to-runtime boundary with
Apptainer. Review it for the site, place it on a shared filesystem, and set
`COURSE_CONTAINER_RUNNER` plus the launcher-specific `*_IMAGE_DIGEST` variable.

Lab 08 performs its cache construction, correctness checks and measurements in
inference mode. Non-finite prompt or continuation comparisons reject the run
before timings or successful results are recorded.

## Begin with the concepts

Lab 35 is a download-free CPU introduction to fixed-parameter token generation; use `python3 labs/35_inference_basics.py --device cpu` in the mechanics environment. Its explicit `--device cuda` path is for an allocated H100. This is not an attention or serving benchmark.

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
