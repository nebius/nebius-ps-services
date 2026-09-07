# Lab 12: Route inference tokens to expert owners

Expert-parallel inference sends tokens to the ranks that own their selected experts and returns outputs to the original order. This lab implements that round trip on two H100 nodes and reports expert load balance. You will learn the data-movement mechanism without confusing a tiny routed operator with a complete MoE model or a serving benchmark.

## Before you start

**Theory preparation:** Read Lesson 14 for MoE expert ownership, all-to-all split counts, inverse routing and forward-only validation. Fundamentals Lesson 12 and Optimizations Lessons 11–12 supply process groups, network qualification and rank timing. Complete distributed preflight.

Pass the two-node mechanics preflight and review all-to-all routing. Exactly two ranks are required. This Inference lab has no backward or optimizer update; those responsibilities belong to the separate Training EP lab.

## Concepts and code path

The source builds deterministic token routing, exchanges tokens according to split counts, applies toy expert computations, and restores outputs to their origins. A known reference checks assignment and round-trip correctness. Timing covers the bounded routing computation; logical expert parameter fractions describe ownership, not complete measured resident model memory.

## Practice

Run the paired ranks through the supplied launcher. Record workload size and topology with each timing so a changed routing case is not mistaken for a fixed-work engine optimization.

```bash
umask 077
sbatch slurm/two_node.sbatch labs/12_moe_expert_parallel.py --profile smoke
sbatch slurm/two_node.sbatch labs/12_moe_expert_parallel.py --profile h100
```

## Check your results

Require every rank's output to match its expected experts. Inspect token send counts, `global_expert_token_load`, max-to-mean imbalance, the parameter-fraction scope, and `round_trip` timing. There are no TTFT or online throughput fields.

## Investigate the behavior

Trace one token through dispatch, expert execution, and return permutation. Which expert determines the longest local workload? Explain why balanced token counts can still hide unequal expert computation costs.

## If something goes wrong

Incorrect split counts or inverse permutations can misroute outputs. Diagnose those against the reference before examining latency. A collective timeout can result from disagreement about collective order across ranks rather than a slow network.

## Takeaways and next step

EP performance combines routing cost and expert balance. A real-engine extension needs a reviewed MoE artifact and the Inference two-node serving launcher; this mechanics result does not establish production model fit or service performance.
