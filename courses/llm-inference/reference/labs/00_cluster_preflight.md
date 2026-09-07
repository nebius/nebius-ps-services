# Lab 00: Verify the two-node inference mechanics platform

Distributed inference requires correctly placed workers before model partitioning or serving behavior can be evaluated. This lab checks the two-node H100 allocation and a known NCCL collective without starting an inference engine. You will separate basic worker communication from model loading, endpoint readiness, request correctness, and service performance, each of which needs its own later evidence.

## Before you start

**Theory preparation:** Read Lesson 14 for distributed inference ownership, with Fundamentals Lesson 12’s process groups/known-value collectives and Optimizations Lesson 11’s network qualification. Run this before the two-node mechanics labs; it does not qualify an HTTP serving engine.

Use the Inference mechanics environment and the [cluster runbook](../cluster-smoke-test.md). Two distinct nodes must each expose one full H100. This lab is not the serving-container readiness check.

## Concepts and code path

The course launcher creates two ranks, and shared helpers validate the device and initialize NCCL. Placement information is exchanged and a predictable tensor reduction is checked. The example has no HTTP endpoint, tokenizer, model shards, request queue, or KV-cache manager.

## Practice

Use the two-node launcher from this course directory. Keep placement diagnostics private and do not expose rendezvous or worker ports outside the cluster's approved private network.

```bash
umask 077
python labs/00_cluster_preflight.py --help
sbatch slurm/two_node.sbatch labs/00_cluster_preflight.py --profile smoke
```

## Check your results

Require `two_nodes` and `nccl_all_reduce`, world size two, and two distinct hosts in private evidence. A passed collective does not prove a TensorRT-LLM or vLLM engine can load or serve the selected model.

## Investigate the behavior

Draw the two worker/device pairs and explain why local rank is zero on each node. Name the additional initialization boundaries that a real distributed engine introduces beyond this communication group.

## If something goes wrong

Incorrect placement, device visibility, or NCCL failures belong to the corresponding platform layer. Preserve the failing output and ask the cluster owner to resolve infrastructure issues instead of changing network or sharing settings.

## Takeaways and next step

Distributed readiness has multiple gates. Use this result for the TP and EP mechanics labs, then qualify the separate engine containers and their complete startup/request/shutdown workflows before claiming serving readiness.
