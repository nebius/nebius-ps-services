# Visual plan

The table defines this course's overview diagrams. The
[visual manifest](visual-manifest.json) links additional detailed diagrams to
their conceptual lessons. Each figure appears after the specified section in its declared lesson or lab home,
with accessible labels, captions, and fit-to-width sizing.

The autoregressive lifecycle follows **Start here** in Lesson 1 and remains
linked from Lesson 2. Its first token comes from prefill; subsequent decode
processes the selected token only when generation continues. Token choice,
stopping, buffering and completion are explicit parts of the same workflow.

| Title | First stage | Second stage | Third stage | Explanation | Lesson | After | Layout | Home |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Artifact loading | Immutable bundle | Readiness and warm-up | Accepted request path | Model, tokenizer, adapters, quantization, and engine form one identity. | 1 | Mental model | flow | lesson |
| Prefill and decode | Prefill builds prompt KV | First token from prefill logits | Decode subsequent tokens | Prefill computes the first output distribution; each continuing decode consumes a generated token, appends its KV, and produces the next distribution. | 2 | Mental model | timeline | lesson |
| KV capacity | Layers and KV heads | Bytes per token | Sequence concurrency | GQA and MQA reduce cached heads but runtime overhead still needs measurement. | 4 | Mechanism | flow | lesson |
| ISL and OSL matrix | Short ISL | Long ISL | Fixed concurrency per panel; repeat the matrix at other loads | Rows vary input length and columns vary output length. All four combinations matter; concurrency is a separate sweep, not a quadrant. | 5 | Mechanism | matrix | lesson |
| Service metrics | Queue and prefill | First token | Output-token gaps | TTFT and ITL use different boundaries and must accompany throughput. | 7 | Mechanism | timeline | lesson |
| Paged KV | Free block pool | Sequence block tables | Recycle on completion | Blocks reduce fixed reservation while introducing rounding and metadata. | 8 | Mechanism | cycle | lesson |
| Continuous batching | Admit requests | Mix prefill and decode | Retire and refill | Scheduling trades efficiency, TTFT, ITL, and queue growth. | 9 | Mechanism | cycle | lesson |
| Prefix reuse | Exact token prefix | Cached KV blocks | Remaining prefill | Reuse depends on every model and request input that affects KV. | 10 | Mechanism | flow | lesson |
| Speculative decoding | Draft proposals | Target verification | Accept or recover | End-to-end value depends on acceptance and draft overhead. | 13 | Mechanism | flow | lesson |
| Inference placement | Replicas or partitions | Communication path | Latency and capacity | DP, TP, PP, and EP solve different fit and throughput constraints. | 14 | Mechanism | flow | lesson |
| Serving stack | Client and metrics | Engine scheduler | H100 kernels and KV | Compare engines only through equivalent artifacts and workloads. | 6 | Mechanism | flow | lesson |
| Disaggregation | Prefill workers | KV handoff | Decode workers | The router assigns requests to workers; KV handoff moves state from prefill to decode, while coordination may be bidirectional. Rate matching and transport determine whether separation helps. | 15 | Mechanism | topology | lesson |
