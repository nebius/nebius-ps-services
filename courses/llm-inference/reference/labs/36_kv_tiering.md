# Lab 36: Model KV retention and restore decisions

A reusable prefix is valuable only if compatible cached state survives until the next request and restoring it is worthwhile. This CPU lab models those decisions explicitly. You will vary capacity, expiry, reuse intervals and transfer cost, observe deterministic LRU movement across device/host/storage tiers, and distinguish modeled savings from real serving latency. It performs no CUDA, network, engine or filesystem data-transfer workload.

## Before you start

**Theory preparation:** Read Lessons 4, 8 and 10 for cache bytes, ownership, exact identity, TTL, LRU, tier promotion/demotion and restore-versus-recompute cost. Explain what a restart clears and why a model revision changes reuse identity before this CPU-only policy simulation.

Read Lessons 4, 8 and 10 for KV sizing, page ownership and exact prefix identity. Python and the supplied standard-library helpers are sufficient; a GPU and serving dependencies are unnecessary. The profile label is retained for the course result format and does not activate H100 execution. All prefixes are inactive, equally sized and completely reusable inside this model. Active-request pinning and partial-prefix matching are outside its scope.

## Concepts and code path

`Policy` stores capacities in whole prefixes, bytes per prefix, idle TTL, prefill cost and transfer assumptions. `TierCache` owns three exclusive ordered maps. Access expires entries whose deadlines have arrived, finds the exact namespace/prefix key, compares restore cost with recomputation, then promotes the entry. Capacity pressure demotes the oldest entry; the last tier discards it. Every operation checks unique ownership and capacity bounds.

A TTL refresh happens on access, while demotion preserves the existing deadline. A restart clears device and host maps only. A model revision changes the namespace, so old entries cannot satisfy new requests even if numeric prefix IDs match. These names stand for the complete semantic identity; the toy program does not calculate real token hashes or validate stored tensors.

The arrival schedule is fixed independently of work cost. Foreground and write-service costs are accumulated separately. Transfer uses decimal GB/s, while `--prefix-mib` uses binary MiB. Storage transfer time uses a declared effective end-to-end restore rate; the model does not add separate storage-to-host and host-to-device transfer times. The exclusive model records each modeled demotion write, including writes that real systems might filter or overlap.

## Practice

For a modeled 4-MiB reusable prefix, 2 GB/s of effective storage-to-device throughput and 0.2 ms startup imply 0.2 + 4,194,304 / 2,000,000,000 × 1,000 = 2.297152 ms to restore. Against an assumed 8-ms prefill, restoration has a 5.702848-ms foreground advantage before queueing and write costs. At 0.2 GB/s, restoration takes 21.17152 ms and recomputation is cheaper. These are declared model inputs, not measurements or claims that storage performs like DRAM.

Run Lab 36: Model KV retention and restore decisions on CPU before the live cache campaign. Vary one of capacity, TTL, reuse gap or storage rate. Then model a worker restart and a model-identity change separately. Keep Lab 20 for actual engine requests: the policy model is neither a vLLM/Dynamo implementation nor a serving benchmark.

Run from this course directory on CPU. Each command writes a new private result. Begin with no slower retention, then change only storage capacity; compare TTL, rate, restart and identity separately against that retained baseline.

```bash
umask 077
python labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 0
python labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 8
python labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 8 --ttl-ms 100
python labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 8 --storage-gbps 0.2
python labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 8 --restart-at 6
python labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 8 --revision-change-at 6
```

Use `--gap-ms` to vary reuse interval, keeping the cyclic prefix sequence unchanged. With six prefixes and a 50-ms gap, a prefix recurs every 300 ms. The supplied deterministic policy needs no repeated timing benchmark; independent runtime trials belong to a later real-engine experiment.

## Check your results

The report labels `evidence_kind` as `deterministic_policy_model`. Follow each request's `found_tier`, decision, occupancy, expiry and capacity-eviction counts. `reuse_fraction` counts actual modeled reuse decisions, so an expensive tier hit that chooses recomputation is not a reuse. Review read/write bytes and both modeled time totals. Their sum is service-work accounting, not measured elapsed time: write work may overlap, and queueing is absent.

Compare the reported restore decision with the calculation in Practice: the default rate should favor restoration, while the slower-rate control should favor recomputation.

For Lab 36 retain each tier hit, reuse/recompute decision, expiry/eviction count, occupancy, read/write bytes, and separate modeled foreground and write-service totals. Fixed arrivals are not delayed by the model's costs, so these totals must not be labeled TTFT, QPS or goodput. The model omits active-request pinning, partial-prefix reuse, queueing, chunking and decode. Transfer the question to a qualified engine only with those effects and task-quality checks restored.

## Investigate the behavior

Does TTL expire before the next reuse even when capacity is ample? Does increasing storage capacity help a low-reuse workload? Can a storage hit be rejected as too costly? After a modeled restart, only prefixes previously demoted to storage survive; exclusive tiers are not a persistent backup of every device entry. Explain why changing identity prevents reuse while a mere capacity change does not.

## If something goes wrong

Invalid capacities, non-finite costs and decreasing arrival times fail explicitly. A duplicate key across exclusive tiers or capacity overflow is a state bug. Do not relabel the model's rates as measurements to reconcile a surprising outcome. A real cuFile read might use a staged compatibility path; API success and cache reuse alone do not prove GDS, safe persistent recovery or correct attention output.

## Takeaways and next step

A useful cache policy balances valid reuse, capacity, transfer and write costs. State the model's assumptions before interpreting its result. For an optional qualified engine study, retain exact model/cache identity, verify cache counters and outputs, establish the actual direct or staged I/O path, and measure cold/warm/restart cohorts with TTFT, decode and queueing. No infrastructure installation or real GDS execution is supplied by this lab.
