# Lab 19: Compare inference tensor-partition communication patterns

Tensor parallelism can split a layer's weights across GPUs, but partial outputs must be assembled correctly. This lab demonstrates column- and row-parallel linear inference on two H100 ranks and compares both with a full-weight reference. You will connect conceptual weight-shard capacity with collective communication while keeping those operator mechanics separate from model-level serving claims.

## Before you start

**Theory preparation:** Read Lesson 14 for stored weight layout, column/row partitions, broadcast, all-gather concatenation and all-reduce summation. Use Fundamentals Lesson 9’s reference/error principles and Optimizations Lessons 11–12’s network and slowest-rank timing. Complete distributed preflight.

Pass the two-node mechanics preflight and review matrix partitioning. Exactly two ranks are required; both profiles use an evenly divisible hidden width. The experiment retains reference tensors for verification, so it is not a pure memory-minimized deployment. Training's tensor-parallel lab adds backward and gradient checks; this course owns the forward-only capacity and latency question.

Two one-H100 nodes can demonstrate cross-node mechanics but not intra-node NVLink/NVSwitch TP. If a model fits one H100, two replicas may outperform cross-node TP for aggregate throughput.

## Concepts and code path

Rank zero creates the input and full weight, then broadcasts both so every rank starts with the same values. With inputs shaped `[batch, width]` and PyTorch-style weights `[output_features, input_features]`, the reference is `inputs @ weight.T`. A column-parallel layer splits output features (dimension zero of this stored weight), computes distinct output columns, and concatenates an all-gather list in rank order. A row-parallel layer splits input features and their matching weight columns; all-reduce sums the partial dot products. A correct local slice alone does not prove that the assembled output is correct.

The code separates these two operation paths from measurement and numerical validation. Each path warms up, then records repeated synchronized wall-clock samples with a barrier before each trial. Each sample uses the slowest rank; reported time includes the local matrix operation, collective, and associated host/allocation overhead, but excludes setup broadcasts, the pre-trial barrier, and the final timing-value reduction. It is not isolated network latency. All ranks agree on numerical success before reporting, and process-group cleanup runs on exit. There are no tokenizer, KV-cache, request queue, or streaming-client components.

## Practice

Given a model using 55 GiB that fits on each H100, route requests to two independent replicas. Change to two-way cross-node TP. Expected observation: TP halves ideal weight ownership but adds collectives to prefill and every decode step; keep it for fit or measured objectives, not because two GPUs exist.

Run Labs 12 and 19 for model-fit, communication, and expert-load mechanics. In Lab 19, compare batch one with the default batch at the same width; validate both rank-ordered column reconstruction and row-partial summation. Then run `slurm/vllm_two_node.sbatch` in `tp` mode and, with a reviewed MoE artifact, `ep` mode. Its conditional advanced `pp` mode also runs a real two-node engine when the pinned engine/model supports pipeline parallelism. Each live mode restarts the engine for three independent bounded trials and records request throughput, streaming TTFT/inter-chunk gaps, and engine metrics. These two one-GPU nodes demonstrate bounded behavior, not production-scale parallel efficiency. Use AIPerf—not HTTP chunks—for token-aware ITL/TPOT.

Run the two partition patterns together using the course launcher. Profiles select width 2,048/batch 32 for smoke and width 8,192/batch 128 for H100. The optional positive `--batch-size` changes input rows while keeping the selected width fixed. Use batch one as a small-message comparison, not as a simulation of a complete autoregressive decode step. Repeat each comparison in at least three independent jobs with distinct output directories and preserve the topology and workload settings.

```bash
umask 077
sbatch slurm/two_node.sbatch labs/19_tensor_parallel_linear.py --profile smoke
sbatch slurm/two_node.sbatch labs/19_tensor_parallel_linear.py --profile h100
sbatch slurm/two_node.sbatch labs/19_tensor_parallel_linear.py --profile smoke --batch-size 1 --output-dir outputs/tp-batch1-run1
```

## Check your results

Require finite reference/output values and both reference-agreement flags. Column reconstruction additionally uses `rtol=1e-2, atol=1e-2`; both paths must have relative L2 error below 0.02. The row path changes BF16 reduction order, so cancellation near zero makes a pointwise relative comparison misleading; its explicit recipe uses aggregate relative L2 instead. Inspect `maximum_relative_l2` and `maximum_absolute_error` together rather than treating either as proof of model quality.

Inspect per-rank shard bytes, logical collective bytes, the repeated sample lists, and the two path medians. Logical tensor bytes are not measured wire traffic. `model_fit_fraction_per_rank` is a logical weight fraction: this teaching process still holds full reference weights, both partition buffers, and outputs. It does not demonstrate reduced whole-process residency. A real engine also needs KV-cache and workspace memory.

Retain operator-lab weight fractions, communication bytes, and expert token loads separately from live-engine startup, TTFT, throughput, and exported metrics. Do not claim the live engine exposed per-expert load or exact resident model bytes unless those fields are present in its artifacts.

Use the simplest placement that meets capacity and service objectives on the actual topology.

## Investigate the behavior

Why does one split concatenate output columns while the other sums partial outputs? Predict what reversing the gathered rank order would do: shape checks would pass, but reference agreement would fail. Which activation dimensions determine communicated bytes? Compare batch one with the default batch at the same width; use sample variability and three independent runs to explain the communication-to-compute ratio. Finally, list the validation-only tensors a deployment could remove and the KV-cache/workspace allocations it would need to add.

Replication uses more weight memory but avoids per-token model-parallel collectives. TP improves fit and sometimes compute scale but adds communication each layer. PP/EP add scheduling and imbalance complexity.

## If something goes wrong

Wrong reconstruction order can yield correctly shaped but incorrect tensors. Check the common-state broadcasts, shard axes, and rank-ordered concatenation before interpreting timing. Every rank must enter matching collectives in the same order; rank mismatch or incompatible shapes can block even after basic preflight passes. Do not relax a failed tolerance merely to produce a timing result. If batch-one results are noisy, inspect the repeated samples and surrounding host/network activity before attributing the delay to a kernel.

Avoid using cross-node TP for a model that already fits and then generalizing the added latency.

## Takeaways and next step

Partitioning trades local weight ownership against communication. Use the qualified two-node vLLM workflow for actual model-fit, request-throughput, and streaming evidence; this operator lab does not measure TTFT or production scalability.

Prefer replication for throughput when fit allows; partition only to solve a measured fit or scale constraint.

Map the critical communication path for one decode token under TP and EP.
