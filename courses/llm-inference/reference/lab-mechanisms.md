# Lab mechanisms and evidence

## Lab 27: watch physical KV blocks change ownership

Objective: explain allocation, growth, completion and recycling using stable
block identities. The [paged-KV lab](../labs/27_paged_kv.py) models a pool of
physical blocks and a logical block table per request. It does not allocate
real engine KV tensors, share prefixes, move data between devices, or benchmark
an attention kernel. This small model makes ownership visible before a live
engine hides it behind optimized metadata.

### Worked lifecycle

Run the smoke lab with `--block-tokens 16 --total-blocks 4`. Each block stores
16 token positions for one request's modeled KV allocation. A 17-token request
needs two blocks: 32 reserved positions, 17 useful positions, 15 unused tail
positions. Only the final partial block creates internal waste in this model.

| Event | Request A | Request B | Request C | Free physical IDs |
| --- | --- | --- | --- | --- |
| Empty pool | — | — | — | 0,1,2,3 |
| Admit A with 16 tokens | [0] | — | — | 1,2,3 |
| Admit B with 17 tokens | [0] | [1,2] | — | 3 |
| Grow A to 17 tokens | [0,3] | [1,2] | — | None |
| Reject B growth to 65 tokens | [0,3] | [1,2] | — | None |
| A completes | — | [1,2] | — | 0,3 |
| Admit C with 17 tokens | — | [1,2] | [0,3] | None |

A's first physical block remains 0 when it grows. Its second logical block
maps to physical block 3; physical adjacency is unnecessary. On completion,
both blocks return to the free list. C subsequently receives those exact IDs,
while B's mapping remains unchanged. This proves recycling more strongly than
comparing two unrelated totals of free capacity.

1. Predict each row before running the lab through the
   [single-GPU smoke procedure](cluster-smoke-test.md).
2. Follow `lifecycle_events` in the result. Check ownership uniqueness and
   that used IDs plus free IDs always partition the configured pool.
3. Inspect the deliberately rejected growth. The length, existing table and
   free list must all remain unchanged; partial mutation would leak capacity
   or leave a request claiming tokens it cannot store.
4. Repeat with another positive block size and at least four total blocks.
   Explain how rounding waste changes. The demand worksheet for lengths
   31,65,127,9 remains a separate arithmetic exercise, not a lifecycle trace.
5. Inspect live engine memory/cache metrics later; do not treat these Python
   object counts as measured H100 bytes, allocation overhead, or throughput.

Common failure: confusing a request's logical token offset with a physical
block ID, or allowing two unrelated requests to own one block. Production
prefix sharing needs reference counts and identity rules not implemented here.
Review answer: growth appends only needed pages, release returns owned pages,
and failed reservation is atomic. Paging reduces fixed reservation waste but
does not remove KV capacity limits.

## Lab 28: compare a real policy difference, not two mislabeled chunk sizes

Objective: show why non-preemptible prompt work can delay later requests and
how chunking creates admission opportunities. The
[scheduler lab](../labs/28_continuous_batching.py) runs three controlled policies:
full prefill, 64-token chunks, and a 256-token chunk-size control. Arrivals,
prompts and outputs are identical. Each dispatch prioritizes ready decode
tokens before admitting prefill work.

### Understand the model clock

One service quantum supplies 256 abstract token-work units. Prefill and decode
tokens each cost one unit here. That equality is a teaching simplification:
real prefill and decode have different shape, memory and compute costs.
Ordinary chunked dispatches have at most 256 units and last one quantum.
A full prompt larger than that is admitted alone and runs without interruption
for `ceil(prompt_tokens / 256)` quanta. Arrivals during any dispatch wait until
it ends. Tokens become visible at the end, not the beginning, of a dispatch.

Given request A with 2048 prompt tokens arriving at time zero and request B
with one prompt token arriving at time one, full prefill occupies time 0–8.
B cannot be admitted while it runs. Under 64-token chunking, A processes its
first chunk during 0–1; B can join at time one and start producing tokens shortly
after its own prefill. Change only the policy. Expected observation: B's first
token arrives sooner in modeled time, while A may take more dispatches.
This does not predict a numerical H100 TTFT improvement.

The 256-token control still splits a 2048-token prompt into eight chunks. It
is therefore chunked, not unchunked. Likewise, counting a full 2048-token
prefill as one unit of elapsed time would give it eight times the service
capacity for free. The lab charges its occupied quanta explicitly.

1. Run Lab 28 and inspect each policy's `trace`: start/end, work units, prefill
   by request, decode requests, and active request IDs.
2. Sum scheduled work and verify `sum(prompts) + sum(outputs)` and complete
   request counts for all policies. A policy cannot win by dropping work.
3. Compare arrival-relative first-token latencies, not just absolute timestamps.
   A late arrival naturally has a later completion time even without queueing.
4. Explain the maximum contiguous scheduling interval and the chunk-size
   control. Request-order prefill selection is simple, not a production fairness
   algorithm; short requests can wait behind earlier requests.
5. Run `slurm/vllm_chunked_prefill_ab.sbatch` only in the qualified serving
   environment to measure actual TTFT, ITL, throughput, quality and memory.
   Its independent server restarts and at least three trials per policy are
   separate evidence from these deterministic mechanics.

See the official [vLLM optimization guide](https://docs.vllm.ai/en/v0.28.0/configuration/optimization/)
for the pinned engine's scheduling controls. This simulator is not a replica
of vLLM's scheduler or a replacement for that live A/B experiment.

Review answer: conserve work and offered arrivals, charge occupied service,
and separate policy mechanics from measured latency. Smaller chunks create more
decision points but can sacrifice batching efficiency and add real launch and
scheduler overhead, neither of which is priced by this model.
