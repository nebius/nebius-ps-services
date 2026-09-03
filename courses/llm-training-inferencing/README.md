# LLM Training, Inference, and Performance Optimization

A self-contained course that connects decoder-only model mechanics to practical training and serving on a two-node NVIDIA H100 Slurm cluster.

## Start here

1. Read [MISSION.md](MISSION.md) for outcomes and limits.
2. Open [index.html](index.html), the complete portable course.
3. Follow [COURSE.md](COURSE.md) and [SYLLABUS.md](SYLLABUS.md) in order.
4. Create the pinned training and serving environments from [VERSIONS.md](VERSIONS.md).
5. Read [evidence security and safe sharing](reference/evidence-security.md), review [reference/source-coverage.md](reference/source-coverage.md), then run [reference/cluster-smoke-test.md](reference/cluster-smoke-test.md) after provisioning the cluster.

## Runtime contract

The target is a Slurm cluster with two worker nodes and one full non-MIG H100 per node. Local model-mechanism labs reserve one H100. Distributed PyTorch labs request both nodes and run one process per node with Slurm and `torchrun`. vLLM launchers use the engine's supported local or distributed serving path.

The course is ready for static validation now. Live H100, NCCL, model-download, framework, and serving evidence must be collected later on the user's cluster.

Keep training and serving packages isolated as required by
[VERSIONS.md](VERSIONS.md). From this directory, create and provision both
environments separately:

```bash
python3.12 -m venv .venv-training
.venv-training/bin/python -m pip install -r requirements-training.txt
.venv-training/bin/python -m pip check

python3.12 -m venv .venv-serving
.venv-serving/bin/python -m pip install -r requirements-serving.txt
.venv-serving/bin/python -m pip check
```

The serving environment must be provisioned on a platform supported by the
pinned vLLM build. These local environments do not prove CUDA, H100, NCCL,
Slurm, model-download, or serving behavior.

## What is included

- 24 foundations-first modules organized into LLM training, LLM inference, training performance optimization, and inference performance optimization.
- 24 runnable labs covering tiny and mixed-precision training, optional Transformer Engine FP8, loss masks, accumulation, checkpointing, DDP, FSDP2, LoRA SFT, GRPO, model-artifact auditing, KV cache, sampling, padding, prefill/decode, speculative-decoding acceptance, vLLM scheduling/cache metrics, tensor/expert parallelism, and streaming.
- One-node and two-node Slurm launchers, including explicit `torchrun` for distributed PyTorch.
- Single-node and two-node vLLM serving launchers, including a bounded prefix-cache A/B trial with metrics snapshots.
- Accessible embedded diagrams, complete lab source, exercises, answer keys, and review cues.

## Validate without a GPU

```bash
.venv-training/bin/python tools/validate_course.py
```

This checks course structure, embedded-source parity, Python syntax, static help contracts, and launcher declarations. It does not execute reviewed labs, load models, or run CUDA/NCCL.

From the parent `courses` directory, run the offline behavioral contracts with:

```bash
PYTHONDONTWRITEBYTECODE=1 llm-training-inferencing/.venv-training/bin/python -m pytest
```

Labs fail when a declared correctness gate is false. JSON results use the
canonical `gpu-course-result/v1` schema. JSON results, optional checkpoints,
trainer output, server logs, and metrics use one random course run ID rather
than a scheduler identity. Artifacts are created private and exclusively; vLLM
launchers also use bounded signal cleanup and refuse to clobber prior logs or
metrics. Share only a separately reviewed summary that follows [the
evidence-security guide](reference/evidence-security.md).

## Quality and performance are separate gates

A training optimization must preserve the declared loss, gradient, update, or evaluation criterion. An inference optimization must preserve output acceptance while meeting the relevant TTFT, steady-state latency, throughput, capacity, and memory goals. Tiny teaching runs demonstrate mechanisms, not production model quality.
