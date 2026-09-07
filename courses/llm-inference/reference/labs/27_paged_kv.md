# Lab 27: Follow paged-cache allocation, growth, and recycling

Paged KV storage gives each request a logical block table that points to physical cache pages. This lab implements that ownership lifecycle in a deterministic Python model, including failed growth and page reuse. You will learn why capacity failure must leave state unchanged and why counting pages alone cannot demonstrate a correct allocator.

## Before you start

**Theory preparation:** Read Lesson 8 for logical/physical pages, free lists, ownership, capacity checks, growth and release, after Lesson 4’s cache sizing. Follow its failure-without-mutation example before running the CPU lifecycle model; no attention kernel executes here.

The course wrapper checks the H100 environment, but allocator operations run on the CPU and create no live KV tensors. Read the [lifecycle walkthrough](../lab-mechanisms.md). Use at least four physical blocks for the fixture.

Block size interacts with attention kernels, graph buckets, allocator behavior, and HBM capacity. Use engine-supported values rather than treating block size as a generic PyTorch allocation knob.

## Concepts and code path

`PagedKVPool` owns a free-page list, request tables, and logical lengths. Admission reserves pages; growth reserves any extra pages before changing state; completion returns pages. Invariant checks detect duplicated, lost, or out-of-range physical IDs. A separate static demand worksheet checks capacity arithmetic without replacing the stateful lifecycle proof.

## Practice

Given block size 16 and active sequence lengths 17, 31, and 48, requests allocate 2, 2, and 3 blocks. Change the 31-token request to finish. Expected observation: its two physical blocks return immediately and can serve a new request, while the 17-token request still wastes 15 token slots in its final block.

Run Lab 27 with 16-token blocks and at least four blocks. Follow A's table from [0] to [0,3] as it crosses a page boundary; B retains [1,2]. Reject oversized growth without changing any table or token length. Complete A and observe C reuse the exact physical IDs [0,3]. The [lifecycle walkthrough](../lab-mechanisms.md) explains every event and retains the original static demand worksheet as a separate accounting exercise.

Run a small four-block pool so the physical IDs are easy to trace. The example deliberately attempts oversized growth and checks rejection without partial mutation.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/27_paged_kv.py --profile smoke --block-tokens 16 --total-blocks 4
```

## Check your results

Follow A from `[0]` to `[0,3]`, while B retains `[1,2]`. After A completes, C must reuse `[0,3]`. Require atomic capacity failure, mapping preservation, recycling, and all pool invariants. Inspect `lifecycle_events` alongside the demand worksheet.

Record block size, allocated/useful tokens, internal waste, free physical IDs, logical block tables, rejected growth, and concurrency after every event. Verify that used and free IDs partition the pool and that a failed reservation leaves state unchanged. The model runs in Python and does not measure engine allocation latency, actual HBM consumption, prefix sharing, or eviction performance.

Paging improves flexible utilization but does not eliminate block rounding or total capacity limits.

## Investigate the behavior

Why can a request's logical pages map to nonadjacent physical IDs? Calculate unused slots in the final page. Explain why growth must reserve capacity before updating the request length.

Smaller blocks reduce internal fragmentation but increase metadata and scheduling overhead; larger blocks do the opposite. A large cache pool supports concurrency while reducing workspace and graph headroom.

## If something goes wrong

A duplicate physical ID is an ownership error, not harmless fragmentation. A failed growth that changes any table invalidates the allocator. Do not infer live engine memory behavior from this CPU model's execution time.

Avoid treating a logical block table as proof that physical memory use matches ideal KV bytes.

## Takeaways and next step

Correct paging requires lifecycle invariants as well as capacity math. Prefix sharing, eviction, swapping, and tensor storage are explicit extensions; each needs additional ownership and correctness rules before performance can be evaluated.

Measure block and allocator overhead and keep headroom for engine temporaries.

Show how block size changes metadata, waste, and allocation frequency.
