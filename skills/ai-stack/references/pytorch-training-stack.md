# PyTorch Training Stack

Use this reference for PyTorch framework selection, Hugging Face integration,
distributed training, model sharding, precision, memory, checkpoints, and
training-to-serving export.

## Contents

- Recommendation
- Layered stack
- Why PyTorch is the default
- Lifecycle and customization boundaries
- Training-method selection
- Distributed parallelism decision tree
- DDP
- FSDP2
- Tensor and sequence parallelism
- Context, pipeline, and expert parallelism
- DeepSpeed, Megatron Core, and NeMo
- Precision and memory controls
- Compilation and kernels
- Data and input pipeline
- Checkpoint and serving export
- Scheduling and cluster integration
- Benchmark and acceptance protocol
- Local Apple Silicon use
- Anti-patterns

## Recommendation

Use this default for new Python-first model training and post-training work:

```text
PyTorch
  + Hugging Face Transformers
  + Hugging Face Accelerate
  + Transformers Trainer or a small custom loop
  + PEFT for LoRA or QLoRA
  + TRL for preference optimization or reinforcement-learning post-training
  + PyTorch DDP when the full training state fits per GPU
  + PyTorch FSDP2 when model-state sharding is required
```

Add DeepSpeed, Megatron Core, NeMo Framework, tensor parallelism, context
parallelism, pipeline parallelism, or expert parallelism only when the simpler
stack fails a measured memory, throughput, duration, recovery, or model-support
gate.

## Layered Stack

Keep each layer independently replaceable:

| Layer | Default | Responsibility |
| --- | --- | --- |
| Tensor and autograd framework | PyTorch | Modules, autograd, optimizers, AMP, compilation, distributed primitives |
| Model implementation | Transformers | Architectures, configurations, tokenizers, generation, Trainer integration |
| Launch and portability | Accelerate | Device placement, multi-process launch, DDP, FSDP, DeepSpeed integration, mixed precision |
| Standard supervised loop | Transformers Trainer | Training arguments, evaluation, callbacks, checkpoint integration |
| Custom research loop | Native PyTorch plus Accelerate | Non-standard losses, schedules, data flow, logging, or algorithm control |
| Parameter-efficient tuning | PEFT | LoRA-family adapters, adapter configuration, merge and load workflows |
| Post-training | TRL | Supervised fine-tuning, preference optimization, reward modeling, and supported RL methods |
| Native distributed sharding | FSDP2 and DTensor | Per-parameter sharding and composable device meshes |
| Large NVIDIA model framework | Megatron Core, optionally NeMo Framework | Mature multi-axis parallelism, model-specific kernels, large-scale checkpointing |
| Experiment and registry | MLflow or organizational standard | Runs, lineage, metrics, artifacts, registry, evaluation identity |

Do not treat Accelerate as a replacement for PyTorch. It configures and launches
PyTorch and supported distributed backends. Do not treat Trainer as the only
way to use Transformers. Use a custom loop when Trainer abstractions obscure a
material algorithmic requirement.

## Why PyTorch Is The Default

Use PyTorch by default for this skill because:

- Open-model ecosystems commonly publish PyTorch-compatible weights and
  Transformers implementations first.
- Transformers, Accelerate, PEFT, TRL, vLLM, SGLang, TensorRT-LLM integration,
  and most current LLM training examples center on PyTorch.
- PyTorch provides DDP, FSDP2, DTensor, tensor parallel APIs, distributed
  checkpointing, AMP, activation checkpointing, profiler, and compilation in
  one framework.
- The Python execution model is practical for research iteration and production
  engineering.
- NVIDIA CUDA and NCCL integration is strong, while other accelerator backends
  remain possible when officially supported.

This is a compatibility and ecosystem recommendation. Keep TensorFlow or JAX
when an existing platform, model implementation, accelerator, research method,
or team expertise makes them the lower-risk choice. Do not rewrite a stable
system solely for framework preference.

## Lifecycle And Customization Boundaries

Keep these capabilities distinct even when one product implements several:

1. Data rights, governance, immutable source snapshots, and deletion policy.
2. Parsing, filtering, deduplication, labeling, tokenization, and packing.
3. Model architecture and training or tuning code.
4. Distributed execution, accelerator kernels, and topology.
5. Job scheduling, placement, quotas, preemption, and infrastructure recovery.
6. Experiment tracking and reproducibility metadata.
7. Training checkpoints and artifact storage.
8. Offline quality, safety, compatibility, and performance evaluation.
9. Registry lineage, approval, promotion, rollback, and retirement.
10. Serving deployment, monitoring, feedback, retention, and incident recovery.

Do not select a complete ML platform when only one layer is missing. Do not
call a run reproducible unless code, environment, model, data, configuration,
topology, checkpoint, evaluation, and artifact identities can be reconstructed
together.

### Customization Ladder

Diagnose the baseline failure before training:

- Missing or changing private knowledge: prefer retrieval or tools.
- Output shape or workflow compliance: prefer structured output, prompting,
  tools, and constrained workflows.
- Stable domain vocabulary, style, or task behavior: evaluate PEFT.
- Broader behavior change with governed examples: evaluate full fine-tuning or
  preference optimization.
- Missing domain distribution from large unlabeled corpora: evaluate continued
  pretraining.
- Unmet ownership or architecture requirement: consider pretraining only with
  explicit data, compute, research, safety, and operations ownership.

Every escalation requires a failure taxonomy, representative baseline, expected
mechanism, legally usable data, hidden evaluation set, ablation against the
simpler rung, and rollback and retirement paths.

### Experiment And Registry Contract

Track:

- source revision and dirty state or immutable source bundle;
- dependencies, container, drivers, runtime, kernels, and hardware;
- base model, license, config, tokenizer, templates, adapters, and revisions;
- immutable data manifests, transformations, rights, and deletion obligations;
- hyperparameters, seeds, precision, topology, launcher, and scheduler;
- metrics, profiles, checkpoints, evaluations, failures, and cost;
- parent-child lineage for bases, adapters, merges, quantization, and exports.

Use MLflow when it is the selected experiment, artifact, trace, evaluation, and
registry authority; otherwise use the existing organizational authority. A
registry represents approval and lineage, not merely file storage. Bind mutable
aliases or stages to immutable versions and define approvers, promotion gates,
deployment references, rollback, retention, and retirement.

### Promotion Contract

Promotion binds the exact:

- model and adapter artifacts;
- tokenizer, chat template, generation config, prompts, tools, and policies;
- training, evaluation, and hidden-holdout datasets;
- evaluator code and thresholds;
- quantization, runtime, hardware, and region;
- quality, safety, compatibility, performance, recovery, and rollback results;
- model/data cards, license, intended use, limitations, and approvers.

Use release gates rather than selecting the best-looking run. Include slice
analysis, regression budget, contamination checks, unsafe behavior, serving
compatibility, checkpoint resume, rollback readiness, and retirement.

## Training-Method Selection

Use this ladder before selecting distributed technology:

1. Prompt, tool, or retrieval change: Use when the failure is knowledge,
   workflow, or output control rather than model behavior.
2. LoRA: Default tuning method for stable task, domain, style, or tool behavior
   when a small trainable delta is sufficient.
3. QLoRA: Use when base-model memory is the limiting factor. Load the frozen
   base in a supported 4-bit format, commonly NF4, use BF16 compute when
   supported, and train LoRA adapters.
4. Full fine-tuning: Use only when LoRA has a measured quality ceiling and the
   data, compute, risk, and artifact lifecycle justify updating all weights.
5. Continued pretraining: Use for a real domain-distribution gap supported by a
   large governed unlabeled corpus.
6. Pretraining from scratch: Use only with explicit research, data, compute,
   tokenizer, architecture, safety, and operations ownership.

For every escalation, require a labeled failure taxonomy, simpler baseline,
hidden evaluation set, ablation, and rollback path.

## Distributed Parallelism Decision Tree

Use the following order:

```text
Does the full training state fit on each target accelerator?
  yes -> single accelerator correctness and performance baseline
    duration or throughput target still fails -> DDP
    target passes -> keep the single-accelerator path
  no, replicated model state is the limit -> FSDP2
  individual layers or activation tensors still do not fit -> tensor parallel
  long sequence activations dominate -> context or sequence parallel
  MoE expert capacity or compute dominates -> expert parallel
  model depth or cross-node topology justifies stages -> pipeline parallel
  several limits apply at very large scale -> composed multi-axis parallelism
```

The phrase "full training state" includes parameters, gradients, optimizer
state, activations, temporary buffers, communication buffers, and framework
fragmentation. Estimate memory, then verify it with a real profile.

### Selection Table

| Situation | Preferred starting point | Root cause addressed | Main cost |
| --- | --- | --- | --- |
| Model and optimizer fit per GPU | DDP | Need more sample throughput | Replicated state and gradient all-reduce |
| Replicated state does not fit | FSDP2 | Parameter, gradient, and optimizer memory | Repeated all-gather and reduce-scatter |
| One layer or activation dimension does not fit | Tensor parallel | Intra-layer memory and compute | Frequent topology-sensitive collectives |
| Very long contexts dominate activation memory | Context or sequence parallel | Sequence-axis memory and compute | Attention communication and implementation constraints |
| MoE experts exceed local capacity | Expert parallel | Expert placement and sparse compute | All-to-all traffic and load imbalance |
| Deep model must span stages | Pipeline parallel | Layer partitioning across devices or nodes | Pipeline bubbles, micro-batches, partition complexity |
| Multiple constraints at frontier scale | Multi-axis mesh | Combined memory and duration limits | Highest configuration, checkpoint, and debugging complexity |

## DDP

Use `torch.nn.parallel.DistributedDataParallel` when:

- the entire model, gradients, optimizer state, and activations fit on every GPU;
- the main goal is sample or token throughput;
- the model is replicated and each rank processes different data;
- gradient all-reduce cost remains acceptable.

DDP is the preferred first distributed step because its mental model, failure
surface, checkpointing, and debugging are simpler than model sharding.

Require:

- one process per GPU for CUDA workloads;
- correct distributed sampler or sharded iterable dataset semantics;
- deterministic global batch calculation;
- explicit gradient accumulation and optimizer-step identity;
- NCCL and topology validation;
- rank-safe logging, checkpointing, evaluation, and data publication;
- no accidental duplicate samples or duplicate side effects.

Switch from DDP when replicated state exceeds memory, all-reduce dominates the
profile, or the required model cannot fit per GPU even after safe memory
controls.

## FSDP2

Prefer PyTorch FSDP2, `torch.distributed.fsdp.fully_shard`, for new native
PyTorch sharded-data-parallel designs when:

- parameter, gradient, or optimizer replication prevents model fit;
- the team wants a PyTorch-native, DTensor-based sharding path;
- model modules can be wrapped or grouped with a verified sharding plan;
- communication can be overlapped and the cluster interconnect is suitable.

FSDP2 uses per-parameter sharding and converts parameters to DTensor. Parameters
are all-gathered around forward and backward computation and resharded outside
those windows according to configuration.

Design:

- Use a `DeviceMesh` that represents physical topology and logical parallel
  dimensions.
- Choose wrap boundaries from module size, execution order, memory, and
  communication overlap, not arbitrary layer counts.
- Initialize large models without materializing full weights on every rank when
  supported.
- Use mixed precision deliberately for parameters, reductions, and buffers.
- Use activation checkpointing independently from parameter sharding.
- Save distributed state through PyTorch Distributed Checkpoint or the exact
  supported integration.
- Test resume with the target world size and any required resharding path.

Prefer FSDP2 over FSDP1 for greenfield work when the exact model and integrations
support it. Keep FSDP1 for stable existing systems until migration is justified
and verified.

Do not claim FSDP2 is always faster than DDP. It trades memory for additional
communication and parameter materialization. Benchmark throughput, memory,
startup, checkpoint, and recovery.

## Tensor And Sequence Parallelism

Use tensor parallelism when a layer, attention block, MLP, embedding, or output
projection must be partitioned across GPUs, or when FSDP2 alone cannot meet the
run-duration target.

PyTorch tensor parallel APIs are based on DTensor and currently expose styles
such as column-wise, row-wise, and sequence parallelism. Treat experimental API
status as a release risk and pin the PyTorch version.

Design rules:

- Keep the tensor-parallel group inside the fastest available interconnect,
  normally an NVLink or NVSwitch island.
- Partition paired operations coherently to avoid unnecessary redistributions.
- Record every input and output layout transition.
- Verify divisibility constraints for hidden dimensions, heads, vocabulary,
  experts, and sequence axes.
- Combine tensor parallelism with DDP or FSDP2 only through an explicit
  multidimensional device mesh.
- Benchmark communication, kernel efficiency, memory, and numerical behavior.

Sequence parallelism commonly partitions selected activation work along the
sequence dimension while compatible parameters remain replicated or follow the
chosen tensor layout. It is not a generic substitute for context parallelism.

## Context, Pipeline, And Expert Parallelism

### Context Parallelism

Use when long context makes attention activations or sequence compute the
limiting factor. Verify the attention implementation, position encoding,
masking, communication pattern, packed-sequence behavior, and model support.

### Pipeline Parallelism

Use when model stages must span devices or nodes and the compute per stage can
amortize pipeline bubbles and communication. Native PyTorch pipeline APIs are
currently alpha, so treat API and integration stability as a material risk.

Require:

- a balanced partition;
- a micro-batch and schedule study;
- clear loss and optimizer ownership;
- checkpoint identity across stages;
- failure and restart tests;
- composition tests with data or tensor parallel dimensions.

### Expert Parallelism

Use for mixture-of-experts models when experts must be distributed. Evaluate
all-to-all performance, router balance, token dropping or capacity behavior,
expert placement, fault domains, and checkpoint portability.

## DeepSpeed, Megatron Core, And NeMo

### DeepSpeed

Use DeepSpeed when:

- an existing supported stack already depends on ZeRO;
- CPU or NVMe offload is a required, measured bridge;
- its optimizer, communication, or training integration wins on the target
  workload;
- the team can own version compatibility across PyTorch, Transformers, kernels,
  checkpoints, and inference export.

Do not add DeepSpeed merely because Accelerate can launch it. For greenfield
native sharding, benchmark FSDP2 first.

### Megatron Core

Use Megatron Core when large NVIDIA transformer training requires mature
composition of data, tensor, pipeline, context, sequence, or expert
parallelism, specialized kernels, and large-scale distributed checkpointing.
It is a high-control, high-complexity choice.

### NeMo Framework

Use NeMo Framework when its integration around Megatron Core, recipes,
configuration, checkpoints, data tooling, and NVIDIA platform support reduces
total ownership. Keep the underlying Megatron and NeMo artifact contracts
visible so that serving export and recovery are not platform assumptions.

Before selecting either, prove that FSDP2 or a simpler stack cannot meet memory,
throughput, duration, recovery, and model-support gates.

## Precision And Memory Controls

Apply controls in this order and measure after each material change:

1. BF16 mixed precision: Default on supported Hopper, Blackwell, and other
   accelerators with reliable BF16 support.
2. Gradient accumulation: Increase effective batch without increasing
   per-step activation memory, while accounting for fewer optimizer steps and
   changed communication cadence.
3. Activation checkpointing: Recompute selected activations during backward to
   reduce memory. Measure the compute tax.
4. Efficient attention and supported fused kernels: Verify exact model,
   sequence shape, dtype, hardware, and backward support.
5. Optimizer-state reduction: Use sharding or a validated lower-memory optimizer.
6. FSDP2 or ZeRO: Shard model state.
7. Tensor, context, pipeline, or expert parallelism: Add only for the remaining
   root cause.
8. CPU or NVMe offload: Treat as a last bridge when accelerator memory is the
   blocker and transfer cost still satisfies duration.

Use FP8 training only when the exact hardware, model, kernels, scaling method,
and framework integration are supported and the quality and stability gates
pass. Treat lower precision as a quality and numerical decision, not only a
speed switch.

## Compilation And Kernels

Use eager PyTorch as the correctness baseline. Add `torch.compile` when:

- the graph is sufficiently stable;
- compile startup and cache behavior are acceptable;
- graph breaks are understood;
- distributed collectives and custom kernels are supported;
- the representative workload shows a material end-to-end gain.

Record PyTorch, compiler, Triton, CUDA, driver, NCCL, kernel, GPU, and model
versions. A microbenchmark gain is not a training-run gain.

Use custom CUDA or Triton kernels only when the profile identifies a stable
hotspot and the team can own correctness, backward behavior, numerical tests,
architecture support, compilation, packaging, security, and upgrades.

## Data And Input Pipeline

A fast distributed model loop can still be input-bound. Define:

- immutable data snapshots and transformation manifests;
- tokenization and chat-template identity;
- sample and token counts before and after filtering;
- packed sequence and loss-mask semantics;
- sharding by rank and worker without duplication or loss;
- deterministic or explicitly stochastic shuffle semantics;
- local, network, and object-storage cache behavior;
- prefetch, pinned memory, worker count, decode cost, and backpressure;
- restart position and duplicate tolerance for streaming data;
- data-loader state included in exact resume when required.

Profile GPU idle time and host-to-device transfer before adding GPUs.

## Checkpoint And Serving Export

Keep two artifact classes separate.

### Training Checkpoint

Capture the state required to continue the same optimization process:

- model state;
- optimizer state;
- scheduler state;
- gradient scaler when applicable;
- RNG state for every relevant source;
- data-loader or sampler state when exact continuation is required;
- global step, epoch, consumed samples or tokens, and accumulation position;
- parallelism mesh, framework version, and checkpoint schema;
- configuration, code, environment, and data identities.

Use atomic publication, completion markers, checksums, retention, encryption,
and an actual resume test. A checkpoint is not valid until a resumed run makes
correct forward progress.

### Serving Artifact

Export a separate immutable release bundle:

- model configuration;
- `safetensors` weights where supported;
- tokenizer files and special-token map;
- chat template;
- generation configuration;
- adapter files and PEFT configuration, or a declared merged derivative;
- quantization manifest and calibration identity;
- model license and source revision;
- expected runtime and hardware compatibility;
- evaluation report and content digests.

Never point production inference directly at a mutable training checkpoint
prefix. Validate conversion, numerical parity, tokenizer behavior, tool calls,
quality, and performance in the actual inference runtime.

## Scheduling And Cluster Integration

Keep training semantics separate from cluster scheduling.

- Slurm: Prefer when it already owns GPU placement, queues, priorities,
  accounting, topology, and batch operations.
- Kubernetes Jobs or JobSet: Use when Kubernetes is the established compute
  control plane and the team owns GPU scheduling, network, storage, and failure
  behavior.
- Kubeflow Trainer: Add when distributed-training lifecycle and policy justify
  a training-specific controller.
- Ray Train: Add when training belongs to a wider Ray-native data, tuning, or
  distributed Python workflow.
- Managed training: Use when reduced platform ownership outweighs portability,
  data, cost, and feature constraints.

Require gang scheduling or equivalent all-worker readiness where the framework
cannot make progress with partial allocation. Record placement topology and
failure-domain assumptions.

## Benchmark And Acceptance Protocol

For every distributed design:

1. Freeze model revision, data sample, tokenizer, sequence distribution,
   precision, optimizer, global batch, accumulation, and quality target.
2. Measure a single-GPU or smallest-fit baseline.
3. Record peak allocated and reserved memory by category when possible.
4. Measure tokens or samples per second, model FLOP utilization where reliable,
   step time, data wait, collective time, and straggler variance.
5. Sweep GPU count and parallelism without changing the learning objective.
6. Measure checkpoint write, load, and recovery time.
7. Run enough steps to include warmup, compilation, steady state, evaluation,
   and checkpoint behavior.
8. Verify loss trajectory and final quality, not throughput alone.
9. Report utilization and total run cost at the accepted quality.
10. Reject a more complex parallelism mode unless it passes a named memory,
    duration, reliability, or cost gate.

## Local Apple Silicon Use

On an Apple M4 Pro with 48 GB unified memory, use PyTorch with the MPS backend
for development and bounded experiments where supported:

- unit and integration tests;
- tokenizer and data-pipeline validation;
- small-model inference and training smoke tests;
- small LoRA experiments;
- checkpoint, configuration, and export logic;
- CPU or MPS correctness checks.

Do not use MPS measurements to estimate CUDA kernel performance, NCCL scaling,
Hopper or Blackwell precision behavior, multi-node communication, or production
serving capacity. Maintain a small CUDA validation environment before merging
GPU-specific changes.

## Anti-Patterns

- Selecting FSDP2 before proving DDP cannot fit or scale.
- Combining DDP, FSDP, tensor, pipeline, and context parallelism without a
  memory and communication model.
- Calling every distributed technique "model sharding".
- Treating Accelerate, Trainer, DeepSpeed, Slurm, and Kubernetes as competing
  products at the same layer.
- Saving only weights when exact training recovery is required.
- Serving a distributed training checkpoint without an explicit export.
- Using mutable model, tokenizer, dataset, or container aliases.
- Changing global batch, precision, sequence distribution, and GPU count in the
  same performance comparison.
- Reporting theoretical FLOPS or one-step throughput as end-to-end training
  performance.
- Adding CPU or NVMe offload before measuring transfer bottlenecks.
- Enabling `torch.compile`, fused kernels, FP8, or quantization without a
  correctness and quality baseline.
