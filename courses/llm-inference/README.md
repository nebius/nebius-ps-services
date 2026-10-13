# LLM Inference

[Read the course](index.html) · Estimated guided time: **52 hours**.

Follow a request from model artifacts and tokens to generation, cache use and
serving. Take GPU Fundamentals and GPU Performance Optimization first. Use
[GPU Performance Tools](../gpu-performance-tools/index.html) for profiling and
[PyTorch for GPU Performance Engineering](../pytorch-gpu-performance-engineering/index.html)
for a tensor-reading refresher.

## Learning route

Audit artifacts, follow generation and sampling, estimate cache capacity,
then define the workload. Learn engine readiness and client metrics before
comparing scheduling, quantization or speculation.

- Begin with Lab 35's download-free CPU generation exercise:
  `"$COURSE_PYTHON" labs/35_inference_basics.py --device cpu` in the mechanics runtime.
- After Lesson 5, fix the model, sampling contract, cache estimate and workload.
- After Lesson 7, distinguish client latency from model-forward time; try the
  introductory [AIPerf profile](reference/cluster-smoke-test.md) through Lab 15’s `--experiment aiperf` route before a policy comparison.
- Change scheduling, backend or precision one at a time. CPU cache models and
  mechanics experiments do not establish live serving performance.

## Before the first lab

Complete the [Lab Guide](../lab-guide.html#lab-preparation-scripts) and check
[Versions and environment](VERSIONS.md). Use the approved runtime on one full
H100; a qualified advanced cluster can also supply one-GPU allocations.

Example mechanics run: after Lesson 2 and
[Lab 16's artifact audit](reference/labs/16_model_artifact_audit.md), follow
[Lab 09](reference/labs/09_hf_prefill_decode.md) from this course directory:

```bash
sbatch slurm/09_hf_prefill_decode.sbatch --workload small
```

Lab 16 needs online Hub metadata access even when artifacts are cached.
Keep that exception separate from the other offline runs.

## Serving runtime preparation

Mechanics uses `requirements-mechanics.txt`; lightweight clients use
`requirements-serving.txt`. Native vLLM and AIPerf keep separate prepared
environments and vendor-matched dependencies. Use the Lab Guide's course/lab
lookup; only Lab 33 adds the larger target model. Jobs restore their saved
runtime and private caches automatically.

TensorRT-LLM/Triton and Dynamo container exercises are optional. Their prepared
artifacts need their own readiness, response-correctness and GPU checks;
preparation does not build a GPU engine or qualify serving performance.

## Completion

Keep mechanics and serving evidence separate. Use Lab 32's trials experiment
for at least three equivalent-work, unprofiled runs and a quality/latency/memory
decision. Qualify real engines independently. Distributed serving belongs to
[Advanced Labs](../advanced-gpu-communication/index.html); optional Dynamo
experiments are not a core completion gate.

Use [Where to Go Next](NEXT-STEPS.md) for optional reading after the core route.

## Reference guides

- [Versions and environment](VERSIONS.md): runtime identities and qualification limits.
- [Readiness checks](reference/cluster-smoke-test.md): checks before interpreting results.
- [Lab mechanisms and evidence](reference/lab-mechanisms.md): worked interpretation and scope.
- [Benchmark worksheet](reference/benchmark-record.md): compare equivalent work.
- [Evidence and privacy](reference/evidence-security.md): retain raw results privately and share sanitized summaries.
- [Glossary](GLOSSARY.md): terms used throughout the course.
