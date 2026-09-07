# Where to Go Next

These optional directions extend the training
course without adding required experiments, changing its H100 environment or
implying production-scale qualification. They include both recent tooling
and established concepts with evolving implementations. Start with model
interoperability or numerical recipes; study the distributed topics after
you can explain the course's checkpoint and parallelism mechanics.

## Megatron Bridge and checkpoint interoperability

Megatron Bridge connects Hugging Face model representations with Megatron
Core's distributed training representations through checkpoint conversion
and training recipes. Moving a model involves more than copying tensor
files: configuration, parameter naming and partitioning must preserve the
same computation.
**Investigate**: Which configuration and numerical checks would demonstrate
that a conversion to Megatron and back preserved a model?
**Scope**: Evolving NVIDIA tooling; the documentation records a 0.5.0 release
on June 22, 2026. Conversion support and memory requirements are model-specific;
an available recipe need not fit one or two H100s.
[Explore Megatron Bridge](https://docs.nvidia.com/nemo/megatron-bridge/latest/).

## Block-scaled low-precision training

A low-precision recipe specifies scaling, rounding and the representations of
forward and backward tensors, not just a dtype. Block-scaled formats assign
scales to groups of values, introducing additional metadata and layout
decisions. This extends the FP8 lesson into the relationship between numerical
accuracy, transposition and buffer storage.
**Investigate**: Why can transposing a block-scaled tensor require
requantization, and which extra buffers belong in the memory budget?
**Scope**: Established FP8 foundation with newer formats. Ordinary FP8 paths
can target H100; the documented native MXFP8 and NVFP4 training paths target
Blackwell. Do not treat those formats as interchangeable H100 settings.
[Study Transformer Engine's FP8 and FP4 primer](https://docs.nvidia.com/deeplearning/transformer-engine/user-guide/examples/fp8_primer.html).

## Training resiliency and recovery boundaries

Resiliency combines detecting failed or stalled work, preserving recoverable
state and restarting from a valid point. It extends checkpoint correctness
into an operational question: who detects the failure, coordinates workers
and decides which state can be trusted?
**Investigate**: How would you distinguish a slow but healthy training step
from a hang without repeatedly discarding useful work?
**Scope**: Established problem with evolving recovery tooling. Megatron Bridge
documents Slurm-only fault-tolerance/preemption integrations and experimental
rerun or in-process-restart features. These require independent qualification,
not changes to the course's learner-managed scheduler.
[Read the training resiliency guide](https://docs.nvidia.com/nemo/megatron-bridge/latest/training/resiliency.html).

## DeepEP and specialized expert dispatch

DeepEP provides token-dispatch and combine communication for
mixture-of-experts (MoE) models. Replacing a general dispatcher can change
communication buffers, routing metadata and overlap while leaving the expert
computation conceptually unchanged. The engineering task is to preserve
token accounting and training semantics while measuring the complete step.
**Investigate**: Which output, gradient and routing checks must remain valid
when the dispatcher changes?
**Scope**: Evolving implementation. Current upstream requirements include
Hopper-compatible hardware and appropriate NVLink/RDMA connectivity. Do not
infer support from H100 ownership or a broader integration-page claim; check
the exact dispatcher revision and topology.
[Read DeepEP's requirements](https://github.com/deepseek-ai/DeepEP) and
[NVIDIA's expert-parallel integration guidance](https://docs.nvidia.com/nemo/megatron-bridge/latest/parallelisms.html#deepep-and-hybridep-optimizations).

## Hierarchical context parallelism

Hierarchical context parallelism partitions long-context work using different
communication patterns within and between GPU groups. It makes network
topology part of the execution plan: a fast local group and a slower
inter-group link need not use the same exchange strategy.
**Investigate**: When would local all-to-all exchange plus inter-group ring
communication be preferable to gathering every key/value tensor everywhere?
**Scope**: Advanced distributed study with evolving support. Group sizes,
sequence divisibility, key/value-head counts and Transformer Engine versions
constrain valid configurations. The course's two-rank mechanics lab does not
demonstrate production hierarchical scaling.
[Study hierarchical context parallelism](https://docs.nvidia.com/nemo/megatron-bridge/latest/training/hierarchical-context-parallel.html).

## Hybrid state-space and attention models

A state-space sequence layer summarizes earlier inputs in a state carried
from one sequence step to the next. Each new input updates that state and
produces an output from it. Mamba uses input-dependent rules to control which
information the state retains. Hybrid models combine such layers with
attention. Their computation and state requirements differ from those of an
all-attention transformer. Studying them
tests which activation-memory, recomputation and parallelism assumptions
generalize beyond the model used in the labs.
**Investigate**: Which stored activations and backward computations change
when an attention layer is replaced by a state-space layer?
**Scope**: Evolving model recipes, not an equivalent-computation optimization.
Changing architecture requires suitable weights or training, and model-specific
kernels and parallelism support. No single-H100 fit is implied.
[Explore NVIDIA's Nemotron 3 Nano hybrid-architecture guide](https://docs.nvidia.com/nemo/megatron-bridge/latest/models/nemotron/nemotron3-nano.html).
