# Lab 10: Qualify an optional thread-block-cluster launch

Hopper thread-block clusters introduce an additional execution grouping beyond independent blocks. This optional lab checks that the declared cluster launch can run and produce the expected block IDs. You will learn the toolchain and launch boundary without confusing a successful probe with an implementation of TMA, distributed shared-memory reuse, or a demonstrated performance optimization.

## Before you start

**Theory preparation:** Read Lesson 15 for thread-block clusters, launch attributes and the distinction from TMA/distributed shared-memory operations. Lesson 2 supplies the separately qualified architecture/image contract. This optional probe follows core acceptance and implements only cluster launch/index checks.

Follow the [optional cluster configure/build/test procedure](../cluster-smoke-test.md) in an allocated H100 job using the reviewed CUDA 13.3 image. Its SM90a binary is not forward-compatible. The required course remains SM90; keep the optional build directory separate and bind later launchers to the same qualified image through `CUDA_IMAGE_DIGEST`.

TMA and thread-block clusters are Hopper features. Portable cluster size is bounded; larger H100-specific opt-in sizes and architecture-accelerated instructions reduce portability and can reduce active clusters.

## Concepts and code path

The host requests four blocks grouped into two-block clusters using an extended launch configuration. Thread zero of each block writes that block's index to its output slot. The host checks all four values and records repeated probe timing. No cross-block shared-memory operation or TMA transfer is implemented. The course's SM90a gate is a packaging choice, not a claim that every cluster API intrinsically requires SM90a.

## Practice

Given four blocks that independently load one shared input tile, a cluster design may load once and share through distributed shared memory. Change to the cluster path only after supported placement and synchronization are proven. Expected observation: HBM traffic may fall while active-cluster count, remote-shared latency, descriptor overhead, and `sm_90a` portability can offset the benefit.

Enable Lab 10 only with the documented toolkit and isolated SM90a build profile. The supplied program is a cluster-launch probe: four blocks in two-block clusters write and validate block indices. It does not implement TMA, distributed shared-memory reuse, or a portable timing baseline. For an advanced extension, first add a correct ordinary-block reference, then one cluster-shared or TMA operation with its required synchronization, correctness checks, resource report and timing. The course's SM90a gate is a packaging policy for optional code, not a claim that every cluster-launch API intrinsically requires architecture-accelerated instructions.

Set `COURSE_CLUSTER_BUILD_DIR` to the separately qualified optional build and export the same qualified CUDA 13.3 digest as `CUDA_IMAGE_DIGEST`. The directory variable does not select the runtime image. Do not run these commands until configuration, the build and the targeted CTest check pass in that image.

```bash
umask 077
sbatch slurm/single_gpu.sbatch "${COURSE_CLUSTER_BUILD_DIR:?set the qualified optional build directory}/10_hopper_cluster" --smoke
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_CLUSTER_BUILD_DIR}/10_hopper_cluster" --smoke
```

## Check your results

Require exact block-index outputs, the two-block cluster declaration, and the optional architecture identity. A successful launch validates only this probe. There is no ordinary-block baseline or useful-work speedup comparison.

Retain compiler/image digest, architecture flags, launch attributes, cluster shape, correctness, resources, and time.

Passing this lab confirms the tested cluster launch and block-index outputs only; it does not establish TMA behavior, a speedup or a portable default.

## Investigate the behavior

Explain how block grouping differs from a two-node distributed job. Which synchronization and ownership rules would be needed before one block reads another's shared data? Which would be needed for an asynchronous TMA transfer?

Offloaded copies save SM instructions/register address work but add descriptor and barrier complexity. Clusters enlarge cooperation scope while reducing scheduling flexibility and possibly occupancy. L2 may be simpler and faster.

## If something goes wrong

Unsupported launch attributes or toolchain mismatches leave the optional exercise pending. Do not silently remove cluster attributes to make an ordinary launch pass under the same label.

Avoid shipping an `sm_90a`-dependent binary as though it were forward-compatible.

## Takeaways and next step

Advanced hardware features need separately scoped activation and correctness evidence. Add one real cluster-shared or TMA operation only after defining a portable reference, synchronization contract, resource report, and the same timed operations.

Keep the advanced target isolated and provide a correct portable path.

State the compile-, launch-, and runtime gates for the optional lab.
