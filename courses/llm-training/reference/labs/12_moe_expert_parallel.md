# Lab 12: Trace expert routing, gradients, and grouped work

Mixture-of-experts training distributes tokens to selected expert computations, then must return outputs and gradients to the correct owners. This lab implements a bounded two-rank example with explicit routing and reference checks. You will connect expert load imbalance to communication and grouped computation while distinguishing a teaching implementation from a production MoE runtime.

## Before you start

**Theory preparation:** Read Lessons 11–12 for rank ownership, all-to-all expert dispatch/return, explicit gradient transport, expert updates and grouped/batched matrix work. Use Lessons 4 and 7 for update and numerical checks; complete distributed preflight before tracing the two-rank route.

Pass the two-node preflight and review all-to-all exchange and gradient flow. Exactly two ranks are required. Keep the Training environment separate from the similarly named Inference mechanics lab.

The two-node course lab illustrates bounded TP/PP/CP/EP mechanics only. Production parallel groups generally require more GPUs and deliberate intra-/inter-node placement following the qualified Megatron Core stack.

## Concepts and code path

The source creates deterministic token assignments, exchanges tokens to expert owners, computes local expert outputs, and returns data to originating ranks. Its training path also returns token gradients, verifies expert weight gradients, and checks an optimizer update. A local comparison pads expert batches for grouped `bmm` and compares them with an expert loop; padded grouped work is not a production grouped-GEMM kernel.

## Practice

Given eight experts on two ranks, suppose four heavily used experts currently reside on rank zero. Change physical placement by moving two of those existing experts to rank one while preserving each token's selected expert ID, routing weight, and expert parameters. Compare output and gradient equivalence, expert counts, transfer volume, and slowest-rank time. Expected observation: physical placement can rebalance work without changing the model. Changing router choices, dropping tokens, or adding an auxiliary balancing loss is instead a model/recipe change requiring quality validation; it is not an equivalent-computation optimization.

Run Labs 12, 19, and 29 and label every tensor shape and collective direction.

Run the smoke case through the two-node launcher. Inspect all correctness gates before increasing workload size or using timing to reason about load balance.

```bash
umask 077
sbatch slurm/two_node.sbatch labs/12_moe_expert_parallel.py --profile smoke
sbatch slurm/two_node.sbatch labs/12_moe_expert_parallel.py --profile h100
```

## Check your results

Require routed output, token-gradient, expert-gradient, optimizer-update, and grouped-output agreement with the supplied references. Inspect `global_expert_token_load`, max-to-mean load, grouped versus loop timing, communication phases, and slowest-rank step time.

Record state placement, input/output shapes, bytes communicated, step time, expert loads, and numerical equivalence.

The right strategy follows the state that does not fit and the communication the topology can support.

## Investigate the behavior

Trace one token's outward and return routes. Why must its gradient follow the inverse mapping? Compare the busiest expert's token count with the mean across experts, and account for extra padding before interpreting grouped-operation performance.

TP adds latency-sensitive collectives per layer; PP adds bubbles and stage imbalance; CP adds attention communication; EP adds routing and variable expert load. Combining them can fit larger models but multiplies configuration and checkpoint complexity.

## If something goes wrong

Incorrect split counts or permutations can produce shape errors or silently misassigned tokens. Check the routing reference first. A waiting rank may be blocked by a mismatched collective sequence rather than slow expert arithmetic.

Avoid combining parallelism dimensions before measuring the simplest one that solves capacity.

## Takeaways and next step

EP performance depends on routing, balance, communication, and expert kernels together. Preserve these correctness boundaries when extending the example; two one-GPU nodes demonstrate mechanics, not production MoE scalability.

Start with data parallelism and add only the dimension required by fit, sequence, depth, or sparse experts.

Give one capacity problem solved by each parallelism family.
