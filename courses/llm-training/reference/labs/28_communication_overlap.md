# Lab 28: Reduce gradients when they become ready

Backward produces parameter gradients at different times, creating opportunities to communicate an earlier gradient while later computation continues. This lab compares serialized reduction with gradient-ready asynchronous reductions across two H100 nodes. You will examine bucket construction order, observed readiness order, and the final wait rather than assuming that asynchronous submission guarantees useful overlap.

## Before you start

**Theory preparation:** Read Lessons 11–13 for collectives, completed-gradient readiness, post-accumulate hooks, asynchronous Work joins and exposed communication. Use Lessons 4 and 7 for gradient semantics and numerical checks; complete distributed preflight and preserve the collective order.

Pass distributed preflight and understand backward dependencies. The bucket option requires at least two positive sizes. This is a bounded synthetic gradient schedule, not a replacement DDP implementation for arbitrary models.

On two one-GPU nodes, network and host placement dominate the measured collective path. Keep conclusions bounded and do not infer NVLink/NVSwitch behavior.

## Concepts and code path

The source constructs parameter work for ascending and descending bucket-size schedules. Post-accumulate hooks observe completed gradients and launch asynchronous all-reduces; the serialized reference waits until backward completes before reducing. Both paths join required communication before the step boundary ends. Reference checks compare reduced gradients, and timing uses the slowest rank.

## Practice

Given 30 milliseconds of backward work and an 8-millisecond final gradient reduction, all 8 milliseconds are exposed. Change to three buckets whose reductions start during backward and leave a 2-millisecond final wait. Expected observation: step time improves even if summed collective duration exceeds 8 milliseconds; rank timelines must show independent overlap and unchanged gradients.

Run Lab 28 with several bucket schedules and draw the critical path.

Begin with the default schedules, then inspect the option syntax before designing a different size sequence. Keep global work fixed when comparing schedule alternatives within a run.

```bash
umask 077
python labs/28_communication_overlap.py --help
sbatch slurm/two_node.sbatch labs/28_communication_overlap.py --profile smoke
```

## Check your results

Require serialized/overlapped gradient agreement, finite gradients, and finite ratios. Inspect `observed_gradient_ready_bucket_indices`, step medians, and serialized collective timing. A shorter joined step is compatible with overlap but needs a timeline for the causal claim.

Retain gradient-ready times, collective ranges, overlap, exposed communication, bucket bytes, and step time.

Keep the schedule that shortens the step, not the one with the most apparent overlap.

## Investigate the behavior

Why may gradient readiness reverse forward construction order? Draw the first communication that can begin and the final exposed tail. Explain how bucket size trades collective startup overhead against earlier readiness.

Overlap can slow both operations through contention even while hiding wall time. Extra streams, buffers, and bucket policies increase memory and complexity. Low-precision collectives add error and cast/scaling work.

## If something goes wrong

Inconsistent collective order across ranks can hang even if every bucket is valid locally. Buffer reuse before completion can corrupt gradients. Confirm waits and rank-consistent scheduling before investigating network performance.

A forced synchronization inserted for timing removes the overlap being measured.

## Takeaways and next step

Useful overlap is governed by dependencies and the critical path. Extend to a real model only with complete gradient/update references and a profiler-confirmed schedule; keep two-node mechanics distinct from production scaling.

Measure with non-intervening timeline ranges and synchronize only at the step boundary.

Explain the latency-versus-start-time tradeoff in bucket sizing.
