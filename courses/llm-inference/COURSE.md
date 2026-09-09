# LLM Inference and GPU Optimization on NVIDIA H100

This course connects autoregressive generation mechanics to memory, scheduling, latency, throughput, quality, and serving-engine behavior. Every optimization keeps workload and decoding semantics explicit.

## 1. Model inference and artifact preparation

**Objective**

Explain how fixed model parameters generate output tokens and verify the model artifacts needed for a controlled inference run.

**How it works**

### What inference is

Inference means using an already-trained model to compute an output for a new input. The model's parameters are the learned numbers used in that computation. Ordinary inference reads those parameters; it does not run an optimizer to learn from the current request. Inference can mean classification, producing an embedding vector, or generating text. This course focuses on autoregressive decoder-only language models: models that generate later tokens using the input and previously generated tokens.

A token is a unit in a tokenizer's vocabulary, represented by an integer ID. It can be a word fragment, punctuation or part of a byte sequence, so tokens are not interchangeable with words or network chunks. The model processes IDs, not human-readable sentences directly. A tokenizer converts between text and IDs; a chat template formats roles and messages into the model's expected input. Weights, tokenizer and configuration must agree.

### Why inference needs its own course

Training pays to change model behavior. Inference repeatedly pays to use that behavior for actual requests. A usable application needs more than a plausible answer: it needs appropriate response time, output quality, memory capacity, throughput and safe input/output handling. A GPU that can hold a model's weights may still lack space for many long requests. A change that increases total throughput can make each user wait longer. These are distinct engineering decisions.

Generating text does not by itself update model weights or guarantee factual correctness. Adding retrieved documents changes the context available to the request, not the learned parameters. Applications may store conversations or separately train from approved data, but that is an additional workflow, not an automatic effect of inference. GPU Fundamentals and Optimizations provide the device and measurement prerequisites.

### How one request becomes generated text

A large language model (LLM) needs an artifact bundle: its weight files plus the configuration, tokenizer and other data required to reproduce execution. A revision identifies a particular version; a checksum or cryptographic hash is a content fingerprint used to detect changed bytes, not proof that the content is trustworthy. An adapter supplies additional learned parameters associated with a base model. Quantization metadata describes how compact stored values map to numerical weights. A safetensors shard is one tensor-data file in a split weight set. Remote model code is executable implementation code obtained with a model, so allowing it has a different security meaning from reading tensor data. The audit later in this lesson checks these distinct components.

First establish a trusted, compatible model artifact, then load its fixed weights and prepare the engine. For a request, the host formats and tokenizes the prompt, queues it, and supplies token IDs to the model. Embeddings turn IDs into vectors. Transformer layers combine information from allowed preceding positions and produce logits: scores for possible next tokens. Softmax and the chosen decoding rule turn those scores into a token choice. Greedy decoding chooses a highest-scoring token; sampling draws from a configured distribution.

Prefill processes the known prompt and stores reusable attention keys and values, called the key/value (KV) cache. The logits at the final prompt position supply the first output token. There is no compulsory extra decode pass before that first choice. If the request continues, the next forward processes the newly chosen token, reads earlier cached state and appends that input token's keys and values. It then supplies logits for another token. This feedback loop is decode.

Check end-of-sequence, stop rules and the output budget after token selection. Detokenize permitted output and return or stream it; release request state when the request finishes. The newest selected token only acquires KV entries if it is subsequently processed by a forward pass. While weights stay fixed, the token history, cache, random-number state and scheduler membership can change. CPU and GPU responsibilities depend on the engine: formatting and networking are usually host work, while model tensor computation is GPU work. Never assume every step or every sampling implementation runs on one processor.

### Execution and dependencies

A request will become token IDs, model computations and generated tokens. Before loading any model, establish what artifact supplies those computations and whether it is safe and compatible to load. No running engine is needed for this first metadata audit; GPU Fundamentals and Optimizations supply the device and measurement prerequisites.

Loading a model turns an artifact bundle into an executable computation. The configuration describes the architecture; the weight shards and index supply its tensors; the tokenizer and chat template determine the input IDs. Generation settings, adapters and quantization metadata can change how the same visible request is processed. Immutable revisions and checksums identify which bundle is being used, while expected tensor shapes, dtypes and special-token IDs check that its parts agree.

The loading path has several stages. Metadata is resolved first, files are downloaded or staged on the CPU if needed, GPU materialization places the weights in device memory, and libraries or kernels perform first-use setup. Readiness follows only after the intended request path can run. Mixing those stages into a steady-state benchmark makes loading and warm-up look like ordinary inference cost.

Custom model code adds an execution boundary: `trust_remote_code` permits repository code to run. It is not merely permission to read weight data. License metadata also needs review rather than automatic acceptance. The artifact record distinguishes local, fetched, transformed and compiled components while keeping credentials and private paths out of shared evidence.

During inference, an embedding table maps each input ID to a vector, and model operations read the loaded parameters. `model.eval()` selects evaluation behavior for modules such as dropout; it does not disable autograd. `torch.inference_mode()` avoids autograd recording for forward-only work. Neither mode makes parameters immutable. Keeping weights fixed is part of the inference workflow: no optimizer update is performed for the request.

A model name is not a complete artifact identity. Different tokenizer, chat template, adapter, quantization, generation, or remote-code revisions can change token counts, memory, outputs, and performance while appearing to be “the same model.”

### Try a small example

Imagine a four-token vocabulary: I, like, GPUs and an end marker. A fixed table of next-token scores can map I to like, like to GPUs, and GPUs to the end marker. Starting with I, three repeated choices yield like, GPUs and stop. The table never changes. A one-token output budget instead stops after like. This is a transparent autoregressive model, not a trained transformer: it has no attention, prefill optimization or KV cache.

**Practice labs**

- [Lab 35: Trace fixed-parameter autoregressive inference](reference/labs/35_inference_basics.md)
- [Lab 16: Audit model, tokenizer, configuration, and license identity](reference/labs/16_model_artifact_audit.md)

**Mental model**

Inference behavior depends on an artifact bundle: architecture config, tokenizer and template, weights, generation defaults, adapters, quantization scheme, revision, and optional remote code.

## 2. Autoregressive generation

**Objective**

Trace one request from text to tokens, prompt processing, iterative decode, detokenization, and stop conditions.

**How it works**

Tokenization converts text into token IDs using a vocabulary and its rules. Prefill processes the known prompt and produces attention state plus logits, the scores used to choose the first output token. Decode then processes newly selected tokens one step at a time using the saved state. Detokenization turns output IDs back into text, and stopping decides when generation ends. The first output does not require a separate decode pass after prefill.

Inside attention, a learned projection transforms token vectors into queries (Q), keys (K) and values (V). A query describes what a position seeks, keys provide matching information, and values carry the information mixed into the result. The key/value (KV) cache stores prior keys and values. In shape notation, B is batch size, S is sequence length and H is hidden-vector width. These components explain the request workflow before its tensor shapes and timing boundaries are examined.

The audit fixes the tokenizer and model. This lesson follows one request through those artifacts so later metrics can be attached to exact phases rather than to a single opaque “inference time.”

### Process the known prompt

The chat template formats the request, and the pinned tokenizer converts it into IDs `[B,S]`. Embedding lookup produces vectors `[B,S,H]`. Prefill processes those known prompt positions, forms attention queries, keys and values, and stores per-layer K/V state for the processed tokens. Causal masking prevents a position from using future prompt tokens even though many positions are computed in parallel.

The final prompt position's logits already describe the first output-token distribution. The decoding rule selects that token directly; no extra decode forward is required before the first choice. The stopping and buffering policy then decides whether to end generation and which content may be returned.

### Continue only while another token is needed

If generation continues, the sampled token becomes the input to the next forward pass. That pass reads prior K/V, appends K/V for its current input token and produces logits for the following token. Selection and stopping checks repeat. A token gains cache entries only when a forward pass processes it; the final sampled token may never enter KV if generation stops immediately afterward.

Hugging Face Transformers provides model/tokenizer classes and a DynamicCache that grows as new inputs are processed. Parameters stay fixed during this loop, while request history, cache blocks, random number generator (RNG) state and scheduler membership can change. Detokenization may buffer incomplete byte sequences, and a Hypertext Transfer Protocol (HTTP) chunk can carry zero, one or several model tokens.

### Match the attention mask to the phase

For one head, attention forms scores `Q @ K.T / sqrt(head_dim)`, applies the allowed-position mask, converts scores to weights with softmax and combines V using those weights. Square prompt attention has a lower-triangular allowed region. A one-token continuation instead needs access to all valid cached positions, including its current input. A square-prefill masking rule applied without checking absolute positions can hide required history in this nonsquare case.

PyTorch's scaled dot-product attention (SDPA) application programming interface (API), `scaled_dot_product_attention`, expresses the operation but does not by itself identify the chosen kernel. Correct shapes and allowed context come first; Lesson 11 develops backend optimization.

Prefill and decode execute different shapes and stress different resources. Model correctness, service correctness, and streaming transport correctness are also separate: valid logits do not guarantee correct stop handling or chunk/token accounting.

**Practice labs**

- [Lab 08: Compare cached attention with full recomputation](reference/labs/08_kv_cache.md)
- [Lab 09: Follow real-model prefill and continuation](reference/labs/09_hf_prefill_decode.md)

**Mental model**

Prefill computes prompt representations, initializes KV state, and supplies the final prompt position's logits for sampling the first output token. If generation continues, decode reads prior KV, processes the last sampled token and appends that input token's KV, then produces logits and samples the following token. Each sampled token passes through the same stopping checks.

## 3. Decoding policy and output quality

**Objective**

Separate deterministic decoding, stochastic workload control, and output-quality evaluation.

**How it works**

Sampling chooses a next token from a probability distribution derived from the model's logits, or raw scores. Greedy decoding chooses a highest-scoring token instead of drawing randomly. Temperature changes how concentrated the distribution is; top-k restricts choices to k leading tokens, while top-p keeps a leading set whose cumulative probability reaches a threshold. A seed initializes pseudorandom choices for reproducibility; it does not guarantee identical results across different implementations or hardware.

For probabilities [0.7, 0.2, 0.1], greedy decoding chooses the first token, but sampling can choose any allowed token. Changing the policy can change answer length, content and computational work. A quality metric evaluates whether outputs meet the task's needs, whereas a repeatability check asks whether a declared run can be reproduced. Holding sampling settings fixed is necessary for a useful comparison, but identical speed or identical text is not by itself a measure of answer quality.

The decode loop applies sampling and stopping after logits. Those policies determine both output semantics and how much work each request performs.

The model first produces logits for the next token. Greedy decoding selects a largest logit. Stochastic decoding instead constructs a probability distribution and samples from it, so several tokens can be valid outcomes of the same prompt.

Temperature T > 0 divides logits by T before softmax. A smaller T makes high-scoring tokens relatively more likely; a larger T flattens the probabilities. Top-k retains the k leading candidates. Top-p retains the smallest ranked prefix whose cumulative probability reaches p, then renormalizes the retained probabilities. Applying top-k and top-p together can give different results in different orders, so the engine's processor order and threshold convention are part of the policy.

Repetition controls, maximum tokens, stop IDs or strings and sampling choices affect both the output and the amount of work. A candidate that stops earlier can appear faster without executing equivalent generation. Prompt-token counts, output-length distributions, finish reasons and empty or error responses make that difference visible.

A deterministic regression profile, often greedy or fixed settings and seed, checks a bounded reproducible behavior. A fixed seed helps replay on the same qualified stack but does not promise identical samples across kernels or engines. A representative stochastic profile tests a declared distribution of requests and outputs instead. Exact token/text comparisons suit an exact-equality contract; task evaluators and confidence intervals suit stochastic quality claims. Generated length must be reported when it is both an outcome and a performance denominator.

A candidate can look faster only because it emits fewer tokens, stops earlier, uses a different seed, or changes randomness. Deterministic regression and representative stochastic service evaluation answer different questions.

**Practice labs**

- [Lab 17: Compare greedy and seeded stochastic generation](reference/labs/17_sampling_semantics.md)

**Mental model**

Decoding configuration changes token choices, output length, termination, and quality. Performance comparisons need a fixed profile or a declared stochastic distribution.

## 4. Attention-cache capacity

**Objective**

Derive KV bytes per token and predict concurrency limits.

**How it works**

The key-value (KV) cache stores attention information from tokens the model has already processed, avoiding repeated key/value computation during generation. Attention heads are parallel components that form queries, keys and values; head dimension is the width of each head's vectors. Multi-head attention (MHA) uses separate key/value heads for its query heads. Grouped-query attention (GQA) shares each key/value head among a group of query heads. Multi-query attention (MQA) shares one key/value head across all query heads.

These are model-architecture choices, not interchangeable cache switches for arbitrary weights. Cache dtype is the numerical representation used to store its values. Resident state is memory currently occupied on the device, and headroom is capacity left for growth or other allocations. KV size therefore depends on the model's actual layers, key/value heads, head dimension, stored-token count and representation—not simply its parameter count.

Generation and sampling now define what output is requested and when it stops. Cached key/value tensors retain attention state from processed tokens, so their size connects that fixed token contract to the maximum resident workload.

For each processed token, each attention layer retains a key vector and a value vector for every KV head. A head has `head_dim` elements, and each stored element uses a chosen number of bytes. Multiplying these dimensions gives ideal KV bytes per token: `layers × KV_heads × head_dim × 2(K and V) × bytes_per_element`. Multiply again by the stored token count across active requests to estimate their logical cache payload.

The factor of two counts keys and values. The head count is the number of KV heads, not automatically the number of query heads. MHA normally supplies one KV head per query head; GQA shares fewer KV heads among query groups; MQA shares one. These architectural choices can reduce cache bytes while retaining the query-head count. They cannot simply be switched on for arbitrary trained weights.

The ideal payload is only one part of peak device memory. Weights and quantization metadata coexist with the KV pool, while prefill activations, attention workspaces, graph pools and communication buffers can add phase-specific peaks. Loading and staging can create a different peak again. Allocator overhead, fragmentation and operational headroom need separate allowance without double-counting active bytes already included in reserved memory.

Actual allocated blocks can exceed the formula because of padding, alignment, page metadata, replication or engine-specific layouts. Hybrid attention layers can also require a layer-specific calculation. Comparing the logical formula with allocated blocks and measured peaks explains the difference between expected payload and usable admission capacity.

KV cache often limits active sequence concurrency, but the ideal formula is only one part of peak memory. Ignoring weights, workspaces, graph pools, communication buffers, fragmentation, and safety reserve creates unsafe admission limits.

For a hypothetical model with 32 layers, 8 key/value heads per layer, 128 values per head and 2 bytes per stored value, one cached token needs 2 × 32 × 8 × 128 × 2 = 131,072 bytes, or 128 KiB. The initial factor of two counts keys and values. At 2,048 cached tokens, one request needs 256 MiB; eight such requests need 2 GiB. This excludes block rounding, metadata and all other model memory. It is a capacity calculation, not a measured allocation or a promise that every architecture uses this layout.

**Practice labs**

- [Lab 08: Compare cached attention with full recomputation](reference/labs/08_kv_cache.md)
- [Lab 26: Calculate ideal KV storage for MHA, GQA, and MQA](reference/labs/26_kv_capacity.md)

**Mental model**

KV cache scales with layers, KV heads, head dimension, dtype bytes, tokens, and active sequences. GQA/MQA reduce KV heads relative to query heads.

## 5. Inference workload shape

**Objective**

Build a workload matrix that reveals phase and queueing behavior.

**How it works**

Input sequence length (ISL) counts prompt tokens and output sequence length (OSL) counts generated tokens. A requested output limit is a maximum; the observed output may be shorter because of stopping. Concurrency counts requests in progress together, while arrival rate counts new requests entering per unit time. These describe different aspects of a workload: ten active requests do not tell you whether they arrived together or gradually.

A workload distribution records the mix of lengths and request types. Burstiness describes arrivals concentrated into short intervals. A queue holds waiting work, and a scheduler chooses what runs next. A service-level objective (SLO) is a declared service target, such as a first-token latency threshold for a stated fraction of requests. Before tuning an engine, define this workload: a long-prompt, short-answer service stresses different phases from a short-prompt, long-answer service.

Prefill cost scales with input sequence length, decode lifetime with output length, and scheduler pressure with arrivals/concurrency. One average prompt hides these interacting axes.

A workload cell describes both how a request begins and how long it continues. ISL sets the amount of prompt processing and initial key/value (KV) state. Observed OSL sets the number of generated tokens, continuation passes and growth of history reads. A long prompt with a short answer therefore stresses different phases from a short prompt with a long answer.

Concurrency adds requests that compete or batch together. Arrival rate and burstiness determine how quickly new requests reach the queue; they are not implied by the number currently active. An ISL×OSL heat map should therefore include short/short, long/short, short/long and long/long cases, with the arrival process and concurrency defined alongside it. Weighted aggregate results are useful only after individual cells remain visible, especially those with strict service targets. Changing traffic patterns can justify versioned operational profiles with explicit conditions for switching or review.

Batch shape introduces a further cost. Padding lengths 3, 4 and 8 into one rectangular batch uses 24 positions for 15 real tokens. Bucketing `[3,4]` separately from `[8]` uses 16 positions for those same 15 tokens. It reduces padding but creates extra batches or scheduling constraints. Prompt identity, masks and position information must survive grouping and ungrouping; otherwise the apparent saving changes the computation.

Sequential dependence is another axis beyond size. In `state = tanh(state @ weight)`, each update needs the previous state before it can proceed. Tanh is an elementwise function bounded between -1 and 1. Such recurrent updates offer different parallelism from processing a wide known input, which is why total element count alone does not describe a workload.

Engine settings that win for short chat can fail for long-context summarization or long generation. Production traffic may also change by time or tenant, so one permanent configuration is not always best.

**Practice labs**

- [Lab 18: Reduce padded prefill work with length buckets](reference/labs/18_padding_bucketing.md)
- [Lab 25: Explore input length and recurrent operator work](reference/labs/25_workload_metrics.md)

**Mental model**

ISL drives prompt processing and initial KV; OSL drives decode iterations and KV growth; concurrency controls batching, queueing, and capacity pressure.

## 6. Model-serving architecture

**Objective**

Start a pinned serving stack, verify readiness, and benchmark through its public request boundary.

**How it works**

Model serving keeps a model available to answer requests from applications. An execution engine performs the model computation and manages resources such as batching and key/value (KV) state; a server receives requests, invokes execution and returns responses. vLLM provides a large language model (LLM) execution engine and a serving interface. NVIDIA TensorRT-LLM provides optimized LLM execution components and serving workflows. NVIDIA Triton Inference Server supplies a serving layer that can use a TensorRT-LLM backend. Triton Inference Server is a different project from the Triton GPU-programming language used in some kernel examples.

An endpoint is an addressable service entry point. Its application programming interface (API) schema defines request and response fields. Readiness means the service can accept the intended requests, not merely that a process exists; warm-up prepares first-use work before measurement. A container packages an execution environment, and its digest identifies exact image content. These roles must be understood before combining versions, launchers and deployment options.

Artifact identity, generation, sampling, KV capacity and the workload matrix are established. Start a controlled single-GPU engine so the next lesson has a real endpoint to measure. Its paging and scheduler internals are introduced only at a high level here and studied in Lessons 8–10.

### Separate request handling from model execution

A serving request reaches an API, is checked against its schema, and is passed to the model's execution path. vLLM supplies an engine and serving interface with continuous scheduling and paged KV management. TensorRT-LLM supplies NVIDIA-optimized model execution, while Triton Inference Server can manage the request boundary and model repository around a supported backend. Their components must be combined through a supported version contract.

Hypertext Transfer Protocol (HTTP) carries requests and responses; JavaScript Object Notation (JSON) represents structured request fields and results. A client supplies a model name, prompt and generation settings, and receives output plus available usage metadata. Offline generation calls the engine directly and omits that service boundary. OpenAI-compatible and Triton APIs can both use HTTP while requiring different payload schemas.

### Establish readiness before measuring service

A process starts before all its model state and kernels are necessarily ready. The controlled lifecycle loads the pinned model, establishes a real readiness condition, checks a deterministic request, warms intended shapes and then starts the load client. Container digest, engine/framework/CUDA versions, build flags, dtype/quantization and scheduler/cache settings identify that execution. Independent restarts separate policy A/B trials so retained state does not bias the comparison. The interface stays non-public; logs remain private and shared evidence contains only sanitized configuration and aggregates.

### Extend the request contract for adapters and media

Low-rank adaptation (LoRA) represents a task-specific weight update as the product of two small matrix factors, added to the transformation performed by a fixed base model. Multi-LoRA serving selects different adapters for different requests while sharing the base weights. This is an advanced, engine-specific extension: qualify each adapter against its exact base-model identity, isolate cache entries between adapters, and measure adapter-switch scheduling, memory pressure, and output quality under concurrent requests. An adapter fitting alone does not prove that many adapters can serve together safely.

Multimodal inference accepts more than one kind of input, such as text and images. A model-specific processor prepares each input type, often using an encoder to turn media into representations the language model can consume. Serving these requests therefore needs a model-specific workload contract. Record how authorized images or other media expand into model tokens, include encoder latency and encoder/cache state, and test scheduling and quality with the actual supported engine. Text-only input sequence length (ISL), cache, and throughput measurements do not automatically describe a multimodal request; avoid collecting or publishing private media.

A process can be alive before weights, engines, graphs, and caches are ready. Benchmarking an unpinned or warming server mixes startup failures with steady-state latency and makes results irreproducible.

**Practice labs**

- [Lab 10: Measure batched offline generation with vLLM](reference/labs/10_vllm_offline.md)
- [Lab 11: Measure concurrent completion requests](reference/labs/11_serving_client.md)
- [Lab 30: Probe an engine's readiness and request schema](reference/labs/30_engine_profile.md)

**Mental model**

The serving engine owns model loading, KV management, scheduling, kernels, and metrics; Triton can provide model lifecycle and request handling around TensorRT-LLM.

## 7. Serving latency and useful throughput

**Objective**

Use user-facing latency and server-capacity metrics with explicit boundaries.

**How it works**

Latency measures how long a request or stage takes. Time to first token (TTFT) measures from a declared request boundary to the first generated token. Inter-token latency (ITL) measures the gaps between successive output tokens; time per output token (TPOT) summarizes the post-first-token generation interval per subsequent token under the declared convention. Throughput measures completed work per unit time, while goodput counts only work meeting the specified service and validity criteria.

A percentile describes a position in the observed latency distribution: p95 is a boundary at or below which 95 percent of observations lie. Open-loop load schedules arrivals independently of response completion; closed-loop load allows completions to determine when more work is sent. AIPerf is a client-side tool for generating controlled inference requests and reporting service measurements. Network chunks may contain zero, one or several tokens, so chunk timing and true token timing are not automatically the same metric.

The workload matrix defines requests and the engine lifecycle provides a controlled endpoint. Metrics now define when clocks start, what each duration represents and how concurrent requests share capacity. Establish these boundaries before changing cache or scheduling policy.

### Place timestamps on the request path

TTFT starts at the declared request-submission boundary and ends when the first output token is observed there. It can include queueing, preprocessing, prefill, first-token selection/postprocessing and transport. Prefill already supplies the first-token logits; a separate mandatory decode pass is not part of that sequence.

After the first token, ITL measures each gap between successive token arrivals. For N > 1 output tokens, mean TPOT is `(last_token_time - first_token_time)/(N-1)`: N tokens contain N−1 post-first-token gaps. A single-token output has no such interval. Last-token latency ends at the final content token, while response-completion latency can extend to later protocol or finish metadata. Substituting completion time into TPOT includes that extra overhead unless the timestamps coincide.

### Distinguish tokens from transport events

Server-Sent Events (SSE) carries successive events on a Hypertext Transfer Protocol (HTTP) response. An event can contain metadata, several tokens or no content token, and transport chunks need not match event or token boundaries. First-content timestamps and inter-chunk gaps are therefore proxies unless the client has appropriate per-token accounting. Bundled tokens do not provide enough information to invent individual arrival times.

### Count completed work under a defined load

System output throughput divides completed output tokens by a shared measurement interval. A per-request generation rate has a different denominator and must be labeled. Goodput counts only work satisfying the declared latency, error and quality criteria, so high raw throughput can coexist with poor goodput.

Open-loop load schedules new arrivals independently of previous completions and can expose queue growth under overload. Closed-loop clients wait for completions before sending more work; their waiting can conceal a server's inability to handle an independent arrival stream. The load model, warm-up treatment, measurement window and latency distributions are part of the meaning of every reported rate.

Tools disagree about ITL/TPOT boundaries, empty chunks, warm-up, and throughput windows. Open-loop and closed-loop generators also produce different queueing behavior, so unlabeled numbers cannot be compared.

**Practice labs**

- [Lab 11: Measure concurrent completion requests](reference/labs/11_serving_client.md)
- [Lab 15: Observe first content and streaming gaps](reference/labs/15_streaming_client.md)
- [Lab 25: Explore input length and recurrent operator work](reference/labs/25_workload_metrics.md)

**Mental model**

TTFT spans request arrival to first token; inter-token latency measures output gaps; time per output token may use a different aggregation; throughput counts completed requests or tokens; goodput counts work meeting service objectives.

## 8. Attention-cache allocation and reclamation

**Objective**

Explain how block allocation reduces unusable reserved capacity and where fragmentation remains.

**How it works**

Paged key/value (KV) management divides available cache storage into fixed-size blocks instead of reserving one maximum-length contiguous region for every request. Each request maps logical token positions to physical cache blocks, obtains additional blocks as it grows, and returns blocks when they are no longer needed. Logical positions describe the sequence seen by the model; physical blocks describe where its cached values are stored. This is an allocation strategy, not a change to the attention equation.

A free list tracks reusable blocks. Fragmentation is storage wasted by an allocation layout, including unused slots at a block's end. Reference counts record shared ownership; copy-on-write creates a private copy before modifying shared state. Eviction removes cached state, preemption pauses work, and offload or swapping moves state to another storage tier. Their costs differ: freeing cache without preserving or recomputing required state would change the computation, not merely its memory use.

The KV formula predicts bytes. Paged allocation decides how those bytes are divided, mapped to requests, and reclaimed as variable-length sequences change.

A paged cache starts with a pool of fixed-size physical KV blocks. A request's logical block table maps its token positions to blocks in that pool. As processed tokens need more space, the allocator obtains another free block and extends the mapping. Consecutive logical positions can therefore live in noncontiguous physical blocks without changing attention's logical sequence.

The last block may be only partly filled. That unused tail is real capacity overhead even though the request no longer reserves its entire maximum sequence length. Block size trades this waste against mapping and allocation overhead.

Completion releases a request's ownership of its blocks. A block returns to the free list only when no active owner or retention policy still needs it. Shared prefixes may remain for other requests or later reuse, using reference counts to track ownership. Copy-on-write creates private storage before a request changes shared state. These rules prevent one completed request from freeing values another still needs.

When the pool is under pressure, an engine can reject new admission, preempt and later recompute a request, swap retained state to another tier, or evict reusable prefixes. Each choice preserves required state differently and has a different cost. Free and active blocks, churn, partial-block waste, preemption, recomputation, swapping and allocation failures show how the policy behaves over scheduler rounds.

Reserving one contiguous maximum-length buffer per request wastes capacity and makes growth difficult. Paged KV improves flexibility but still has partial-block waste, metadata, eviction, and admission limits.

Priority, eviction, host offload, and attention-window reclamation decide which blocks remain resident; every policy trades capacity, recomputation, transfer, and quality semantics.

Attention-window reclamation discards positions that the model's declared attention rule can no longer read. A sliding-window model can stop needing older positions; ordinary full-context attention cannot discard them merely because memory is tight without changing its outputs.

**Practice labs**

- [Lab 27: Follow paged-cache allocation, growth, and recycling](reference/labs/27_paged_kv.md)

**Mental model**

A request grows through mapped cache blocks. Finishing releases its ownership, but a block is reusable only when active requests and retention rules no longer need its contents.

## 9. Request scheduling and prompt chunking

**Objective**

Compare scheduling policies under mixed prompt and decode work.

**How it works**

Continuous batching updates the group of active requests between model iterations: completed requests leave and new requests can enter instead of waiting for an entire fixed batch to finish. Chunked prefill divides a long prompt into smaller pieces of prompt-processing work that can be scheduled between other work. The two ideas address different scheduling choices and can be combined. Neither means that one autoregressive request generates all of its dependent output tokens at once.

A token budget limits the token work admitted to a scheduling step. Admission decides which waiting work can start; backpressure slows or rejects new work when capacity is exhausted; fairness describes how capacity and waiting time are shared. A long prefill may delay other requests even if it is individually efficient. The lesson examines that trade-off while preserving total token work and separating an abstract scheduler model from a real engine.

Paged key/value (KV) makes dynamic admission possible. The scheduler decides which prefill chunks and decode tokens share each model iteration under a token and capacity budget.

At the end of a model iteration, the scheduler removes completed requests and decides which waiting requests can join the next active batch. It has a token-work budget and a finite amount of KV space. Continuous batching changes membership between iterations so a short request can leave without forcing every request in a fixed batch to finish first.

Prompt processing and decode compete within that budget. One long prefill can occupy the device while existing requests wait for their next token. Chunked prefill divides the prompt into smaller segments, allowing decode work to be scheduled between them. Smaller chunks can reduce waiting but add scheduling, launch and repeated-boundary costs. They do not remove the prompt tokens or let one request generate dependent output tokens simultaneously.

Admission also needs an overload policy. A bounded queue, rejection or backpressure limits what happens when arrivals exceed capacity. Queue arrival, admission, phase-specific scheduled tokens, active requests, free blocks, preemptions and completion connect that policy to observed waiting.

A scheduling comparison must preserve outputs as well as token counts. A deterministic policy-equivalence check can compare matching greedy responses or their exact text digests, hashes of the returned text bytes. A matching digest is a bounded equality check, not proof of stochastic-distribution or task-quality equivalence. Fresh server restarts and counterbalanced A/B order prevent cache, graph or allocator warmth from consistently favoring one configuration. The campaign guides apply those controls to real serving; a scheduler model alone does not establish engine performance.

Prefill-first scheduling can block decode behind one long prompt; decode-first scheduling can starve new requests. Chunking changes head-of-line delay but adds scheduler, launch, and repeated-boundary cost.

An overlap scheduler prepares upcoming work on the CPU while already-submitted GPU work runs. Piecewise compilation or graph capture divides model execution into eligible compiled or captured regions and other regions. This partitions the execution graph; chunked prefill partitions prompt-token work. The serving engine must qualify its CUDA Graph capture sizes and its compiled shape ranges or fallback paths separately.

**Practice labs**

- [Lab 18: Reduce padded prefill work with length buckets](reference/labs/18_padding_bucketing.md)
- [Lab 28: Compare full-prefill and chunked scheduling](reference/labs/28_continuous_batching.md)
- [Lab 34: Preserve outputs while changing a serving policy](reference/labs/34_policy_equivalence_client.md)

**Mental model**

The scheduler chooses which ready requests run in each iteration. Prompt chunks bound work per turn; admission, cache capacity and completion determine how much useful work can share the next batch.

## 10. Prefix reuse and cache retention

**Objective**

Identify reusable prefixes and measure hit benefits without changing request semantics.

**How it works**

Prefix caching reuses attention state for a beginning token sequence already processed under compatible model and execution settings. A cache hit finds reusable state; a miss requires computation. For example, two requests with identical initial token IDs may reuse part of prefill, but matching visible text alone is insufficient if tokenization or model settings differ. This is reuse of previously computed key/value (KV) state, not returning a previously generated answer.

Reuse identity is the information proving two cache entries mean the same computation. A hash is a compact content fingerprint used for indexing or identity checks. A radix tree organizes sequences by shared prefixes and can help find the longest reusable beginning. Eviction removes retained entries to recover capacity. Longer retention can save more prompt work or reduce room for active requests, so a successful cache policy must balance reuse with storage and isolation.

Cache tiering retains reusable KV state outside its fastest memory tier. High-bandwidth memory (HBM) is device memory used by attention; host dynamic random-access memory (DRAM) and storage can retain inactive state for later restoration. Eviction discards an entry, offload moves or copies it, and restoration brings compatible state back before reuse. A time-to-live (TTL) limits retention; least recently used (LRU) eviction selects the least recently accessed eligible entry under capacity pressure. A TTL is not a guarantee that capacity will retain an entry that long.

Paged KV blocks can outlive or be shared across request prefixes. Prefix caching avoids repeated prefill only when the model’s exact token-level computation is identical.

### Find an exact, compatible beginning

A new request first identifies its model/engine artifact, adapter, tokenized template result, position scheme, relevant multimodal inputs and other computation-affecting metadata. Cache lookup then seeks matching token blocks under that identity. Only the common complete prefix is reusable; a changed token invalidates the state derived from that token and its following context.

A match must still have valid retained storage. Shared immutable blocks need reference tracking so an entry is not reclaimed while a request uses it. A miss, expired entry or incompatible identity requires recomputing the affected prefix. Lookup outcome, matched tokens/blocks, miss reason and occupancy explain how much prefill can actually be saved.

### Locate the retained state

Prefix reuse needs an index from an exact token prefix and its model context to reusable KV state. A hash/block design identifies reusable complete token blocks; a radix tree shares a path of token prefixes and branches where requests diverge. SGLang calls its radix-tree-based reuse approach RadixAttention. These are alternative indexing organizations, not alternatives to computing attention itself. Both require valid ownership, eviction and isolation rules. A similar-looking prompt or a hash without the complete identity contract is insufficient. The supplied prefix experiments use the course's pinned serving path; an SGLang comparison is optional source study, not an installed or qualified engine lab.

### Compare restoration with recomputation

A valid device-resident hit can be used without a host or storage restore. A hit in a slower tier must be restored before attention consumes it. Cache value depends on whether a compatible prefix is still present when the next request arrives and whether restoration costs less than recomputing prefill. Account for bytes per token, reused length, transfer bandwidth and startup, cache lookup/reconstruction, write traffic, queueing and active-request headroom. Report device, host and storage hits separately: a slow-tier hit can still be slower than recomputation. Saving prefill does not directly remove decode steps. A larger working set or longer reuse interval may require more retention capacity, but more cache can also create churn, disk writes or resource contention.

Cold and warm trials must be independent, preserve output equivalence and expose eviction and admission effects. A warmer inherited cache cannot fairly demonstrate a better policy.

### Qualify any direct storage path separately

GPUDirect Storage (GDS) provides supported direct memory access (DMA) paths between storage and GPU memory that can avoid a CPU-memory bounce buffer. GPUDirect RDMA uses remote direct memory access for direct device-memory access by devices such as network adapters; network storage may use RDMA as part of a GDS path, but the two names are not interchangeable. CPU software still coordinates the control path. A cuFile call or a fast read does not itself prove the direct path: filesystem, driver, topology and operation support matter, and compatibility paths can stage through host memory. An optional qualified experiment must independently establish the selected path and compare actual restore latency, write cost and serving quality under the same workload.

Text that looks the same can tokenize differently, and one changed token invalidates all following KV state. Incorrect reuse is a correctness bug; aggressive retention also competes with active-request capacity.

**Practice labs**

- [Lab 20: Investigate reusable prompt prefixes in a live engine](reference/labs/20_prefix_cache_client.md)
- [Lab 34: Preserve outputs while changing a serving policy](reference/labs/34_policy_equivalence_client.md)
- [Lab 36: Model KV retention and restore decisions](reference/labs/36_kv_tiering.md)

**Mental model**

Prefix caching reuses KV for identical token prefixes under compatible model, adapter, position, and cache settings.

## 11. Efficient attention execution

**Objective**

Compare materialized attention and supported fused backends for prefill and decode shapes.

**How it works**

Scaled dot-product attention (SDPA) is an operation: compare query vectors with key vectors using dot products, scale the scores, apply the allowed-position mask and softmax, then use the resulting weights to mix value vectors. Softmax converts scores into nonnegative weights that sum to one across the considered keys. An attention backend is the concrete implementation that executes this operation. PyTorch's SDPA application programming interface (API) can dispatch—select at runtime—among supported backends based on inputs and configuration.

A straightforward implementation materializes the full score matrix in memory. Tiled or fused implementations process pieces and combine stages to reduce intermediate storage and traffic. FlashAttention is an algorithm family for such memory-efficient attention; SDPA does not mean that FlashAttention was necessarily selected. TensorRT-LLM's XQA names a specialized generation-attention path with its own support conditions. Prefill and decode have different query/cache shapes, so the chosen implementation must be observed for each phase.

The generation and key/value (KV) lessons explain Q queries and stored K/V state; batching and prefix reuse explain which requests share execution. Establish an uncompressed, fixed-dtype attention baseline now, before Lesson 12 changes numerical representation through quantization.

A straightforward attention implementation computes QK scores, writes the full score matrix, reads it for masking and softmax, then combines the resulting weights with V. That intermediate can be large even though it is not part of the final output.

A fused tiled implementation works on pieces of those scores and keeps intermediate fragments on chip. It combines the stages without materializing the entire matrix in high-bandwidth memory (HBM), while preserving the specified attention and causal semantics. The saving comes from intermediate storage and traffic; the model still performs attention over the allowed positions.

Prefill offers many query positions at once, enabling large matrix-like tiles. Decode often has one or a few queries against an increasingly long KV history, making memory traffic and latency more prominent. A backend efficient for one shape need not be efficient or eligible for the other. Dtype, head dimension, grouped-query attention (GQA) layout, mask and dropout/training settings also affect selection.

PyTorch SDPA controls or engine logs and profiler evidence identify which backend actually ran. Kernel names, score/intermediate allocations, shapes and output comparisons establish what changed. Holding tokenizer, scheduler and cache policy fixed keeps an attention improvement attributable to the attention implementation.

“Flash attention” or SDPA is not one universal kernel. Masks, dtype, head dimension, sequence, GQA layout, dropout/training mode, and software version can select different implementations or fall back.

TensorRT-LLM's XQA is a multi-query attention (MQA)/GQA generation optimization with a limited support matrix and heuristic dispatch, so it must be observed rather than assumed.

Tiling must preserve one softmax normalization across all allowed keys. For each query, maintain a running maximum score, a sum of exponentials relative to that maximum, and the correspondingly weighted value sum. If a new tile raises the maximum, rescale the old sums before adding the new tile's contributions. Divide the accumulated weighted values by the accumulated exponential sum at the end. Keeping these running quantities avoids storing the full score matrix. Independently normalizing each tile and averaging its output would generally give the wrong attention result.

**Practice labs**

- [Lab 24: Compare materialized attention with SDPA dispatch](reference/labs/24_sdpa_attention.md)

**Mental model**

Attention backends perform the same specified operation with different storage and execution strategies. Tiling preserves global normalization while avoiding a full score matrix; workload shape and supported inputs determine the suitable implementation.

## 12. Quantized inference representations

**Objective**

Compare memory, kernel support, latency, throughput, and output quality for supported quantization paths.

**How it works**

Quantization represents values using a smaller set of numerical levels, often reducing storage. A scale—and sometimes a zero point mapping real zero into that representation—relates stored values to approximate original values. Dequantization reconstructs those approximations for computation. Calibration uses representative data to choose useful ranges or scales; an outlier is a value unusually large or small relative to most others and can make a shared scale less accurate.

Weight-only quantization changes stored model weights; activation quantization changes intermediate computation values; key/value (KV) quantization changes cached attention state. Notation such as W4A16 means four-bit weights and sixteen-bit activations, while W8A8 means eight-bit weights and activations. Activation-aware Weight Quantization (AWQ) uses activation statistics from calibration inputs to choose weight scaling that protects important channels, meaning selected feature dimensions. GPTQ is a post-training weight-quantization method that uses calibration-derived sensitivity information to compensate for rounding error by adjusting weights not yet quantized. Both use model behavior to limit error, but their calibration and weight-adjustment procedures differ. Quantized storage alone does not prove native low-bit arithmetic: the execution path may unpack or dequantize first. Memory, latency and output quality therefore need separate checks.

The serving ledger separates weights, activations, and KV. Quantization can target each independently, with different scale metadata, kernels, and quality risks.

Quantization first maps values into a smaller set of representable levels. The stored codes need scale metadata and sometimes a zero point to reconstruct their approximate numerical values. The kernel must either consume that representation directly or unpack/dequantize it before computation. Smaller stored weights do not by themselves demonstrate faster arithmetic.

For symmetric per-tensor 8-bit integer (INT8), one positive scale serves the tensor. For nonzero values, choose `scale = max(abs(values))/127`, encode `clamp(round(values/scale), -127, 127)`, and reconstruct `integer*scale`. An all-zero tensor needs an explicit positive-scale convention to avoid division by zero. With an illustrative scale of 0.10, 0.26 rounds to code 3 and reconstructs as 0.30. That approximation and the extra scale storage are both part of the representation.

The state being quantized determines the consequences. Weight quantization changes model storage; per-channel, group or block scales can fit local ranges more closely than one tensor-wide scale. Activation quantization changes intermediate computation and may require calibration or dynamic scales. KV quantization changes cache bytes read and written for each token, adding conversion work and potentially altering long-context attention.

A complete recipe fixes calibration data, excluded layers, metadata, engine support and fallback behavior. Artifact/load size, resident high-bandwidth memory (HBM), peak memory, admitted concurrency, phase latency and task quality can move differently. The same prompts and stopping policy are needed to interpret those changes. The linked mechanics lab explains storage and reconstruction; native low-bit engine performance requires its own qualified path.

Smaller tensors do not guarantee faster requests. Dequantization, calibration, fallback kernels, irregular shapes, or memory-bound phase changes can erase gains; additional capacity may be the primary benefit even when single-request latency is unchanged.

NVIDIA Model Optimizer can produce engine artifacts for supported 8-bit floating-point (FP8), activation-aware weight quantization (AWQ), and GPTQ recipes, but format names alone do not prove H100 kernel dispatch or quality.

**Practice labs**

- [Lab 29: Inspect quantized weight and KV storage mechanics](reference/labs/29_quantization.md)

**Mental model**

Quantization replaces a numerical representation with limited codes plus scale information. Storage savings become a useful optimization only when supported computation and acceptable output quality preserve the full inference contract.

## 13. Speculative generation

**Objective**

Explain draft proposals, target verification, accepted prefixes, rejection recovery, and bonus tokens.

**How it works**

Speculative decoding tries to reduce sequential target-model calls by proposing several tokens cheaply and asking the target model to verify them together. The draft or proposal mechanism supplies candidate tokens; the target is the model whose intended output behavior must be preserved. An accepted prefix is the consecutive beginning of the proposal that passes verification. A rejection requires a defined recovery step before generation continues. Some schemes also obtain a bonus token from the target after all proposed tokens are accepted.

Acceptance rate is the fraction of proposed tokens accepted, but it does not include the cost of producing or checking them. Greedy verification can compare token choices directly; stochastic verification needs a correction rule that preserves the target probability distribution, not just plausible-looking text. Drafting can come from another model or another supported proposal mechanism. This is an execution technique, not permission to replace the target's semantics with a faster model's answers.

After prefill supplies the first output token, ordinary decode uses one target-model forward pass for each subsequent token. Speculation attempts to amortize target calls by verifying several cheaper draft proposals at once without changing the target distribution.

### Propose, verify and recover in order

The proposal mechanism drafts `k` candidate tokens from the current accepted history. The target evaluates their conditional probabilities in a verification pass. The acceptance rule checks a consecutive prefix; accepting a later token cannot repair an earlier rejected context.

At the first rejection, the algorithm emits a recovery token selected by the appropriate target or corrected sampling rule, discards later draft tokens and resumes from the accepted history. If every proposal is accepted, the algorithm may also supply a target bonus token, subject to the output budget and stopping rules. Greedy equality and stochastic distribution preservation need different acceptance arguments.

### Choose a proposal method compatible with the target

Proposal methods vary. A smaller draft model can generate candidate tokens; prompt lookup or n-gram methods reuse matching token sequences without running a separate neural draft. Medusa adds proposal heads to a model. EAGLE-family methods use model features in an auxiliary drafting process rather than simply attaching independent next-token classifiers. Multi-token prediction (MTP) uses model-specific heads or modules trained to propose future tokens. These labels do not establish identical algorithms, supported models or distribution guarantees. The target still needs the appropriate verification and acceptance/recovery procedure. Select a proposal mechanism supported by the pinned engine and model; compare acceptance, extra work, memory and output semantics before interpreting a speedup.

### Follow a small rejection example

For the greedy mechanics case, think of the target as a transition function: the current token determines its next-token scores. Check draft tokens in order against the target's greedy choices. For draft `[b,c,d]`, if b matches and c does not, emit b and the target's replacement for c, discard d, and restart drafting from the accepted history. If all draft tokens match and the output budget permits, append the available target bonus token. This preserves the target-only sequence for this illustrative first-order model. A full transformer must condition verification on the complete causal history; the toy transition property is not a general model assumption. Proposed, accepted, rejected, recovery and bonus counts explain the token path; final token IDs and finish reasons check its result. Draft time, verification time and target-call count expose its cost in each workload cell. Measure drafting, verification and recovery together rather than inferring speed from acceptance alone.

Draft generation and verification are not free. Low acceptance, large batches, mismatched tokenizers, or recovery mistakes can make speculation slower or incorrect.

For exact stochastic sampling, let p(x) be the target probability of a proposed token x and q(x) its draft probability at the same accepted history, after the declared sampling transformations. Accept x with probability min(1, p(x)/q(x)). A sampled draft token has q(x) > 0. If rejected, sample a replacement from probabilities proportional to max(p(x) − q(x), 0), then discard later proposals. This correction fills probability mass underrepresented by the draft. For two tokens with target probabilities [0.6, 0.4] and draft probabilities [0.8, 0.2], the first is accepted with probability 0.75 and the second with probability 1. Rejection mass is 0.2 and goes to the second token, recovering target probabilities [0.6, 0.4]. This teaches the exact sampling rule; the supplied greedy mechanics lab does not implement or verify it.

**Practice labs**

- [Lab 23: Trace speculative acceptance, rejection, and recovery](reference/labs/23_speculative_decoding.md)
- [Lab 33: Check greedy equivalence in a speculative engine campaign](reference/labs/33_speculative_engine_client.md)

**Mental model**

A draft proposes several tokens; the target scores them together; the algorithm accepts a prefix under its sampling rule and recovers correctly after rejection.

## 14. Distributed inference placement

**Objective**

Select placement from model fit, request rate, latency, and communication.

**How it works**

Serving data parallelism (DP) replicates a complete model and routes independent requests to replicas. Tensor parallelism (TP) divides a model operation's tensors across workers, pipeline parallelism (PP) divides layers into stages, and expert parallelism (EP) places different mixture-of-experts (MoE) components on different workers. In an MoE layer, a router chooses which expert networks process each token. These strategies change whether a request stays within one replica or requires several workers to cooperate.

A collective is group communication, and topology is the actual path connecting the workers' devices. Key/value (KV) ownership describes which worker holds the attention state needed to continue a request. Serving replicas do not average training gradients per request: their model weights are fixed during inference. Partitioning can help a model fit while adding communication on its token-generation path, so parallelism must be understood as a placement decision before it is treated as a performance feature.

Scheduling shares one engine instance. Parallelism decides whether to replicate that instance or partition its weights, layers, context, or experts across devices.

### Decide whether a request needs one replica or several workers

Serving DP creates complete model replicas and routes independent requests to them. When one model fits per GPU, extra replicas can increase aggregate capacity without splitting each request's model operations. Their weights remain fixed; they do not synchronize training gradients per request.

TP instead splits matrices within a layer. Ranks exchange the output pieces or partial sums needed to continue the same request, adding latency-sensitive collectives to its execution. PP divides layers into stages and transfers activations between them; fill/drain bubbles and the time to traverse the stages matter. EP places different MoE experts on ranks and sends tokens to their selected owners. Request ownership, KV placement and communication follow these assignments.

### Derive the tensor exchange from the calculation

For stored weights shaped `[output_features,input_features]`, a linear layer computes `Y = X @ W.T`. Column parallelism splits output features, producing different columns of Y that an all-gather concatenates in rank order. Row parallelism splits input features and the corresponding weight columns; each rank produces a partial dot product and all-reduce sums them. For `X=[2,3]` and one weight row `[5,7]`, the two row-partial results are 10 and 21 and their sum is 31.

### Return expert outputs to the original token order

All-to-all sends different token groups to their chosen expert owners. The receiving rank applies its local expert operation and sends results back according to the inverse routing, restoring each token to its original position. Keep split counts, order and reference values explicit. Correct reconstruction and slowest-rank timing are separate requirements: every token must return to its intended position, and the group result is not ready until its slowest required rank finishes. Parallel groups use the trusted private fabric and a controlled readiness endpoint; a loopback client boundary does not expose a public service.

Training parallelism names are reused in serving but the synchronization contract differs. Serving data parallel replicas do not all-reduce gradients per request, while TP communication lies on every decode token’s latency path.

Context-parallel serving partitions long prefill or decode context with different KV and communication effects. In context-parallel serving, workers own different portions of a request's stored keys and values. A decode query still needs information from every allowed portion, so workers exchange the query or cache blocks and combine properly normalized attention contributions. Splitting cache ownership can increase the context that fits, but adds communication on that request's path; it does not create independent replicas.

**Practice labs**

- [Lab 00: Verify the two-node inference mechanics platform](reference/labs/00_cluster_preflight.md)
- [Lab 12: Route inference tokens to expert owners](reference/labs/12_moe_expert_parallel.md)
- [Lab 19: Compare inference tensor-partition communication patterns](reference/labs/19_tensor_parallel_linear.md)

**Mental model**

Replicas serve independent requests; partitioned workers cooperate on one request. Choose placement from model and cache fit, communication dependencies, request load and latency.

## 15. Serving workloads and phase separation

**Objective**

Generate controlled load and explain when prefill/decode disaggregation or KV-aware routing might help.

**How it works**

AIPerf is a workload-generation and measurement client: it sends controlled requests to an inference endpoint and records service behavior. It is not the model execution engine. Disaggregated serving assigns prefill and decode to separate workers, transferring the request's key/value (KV) state between them. A worker pool is a set of instances assigned a role; a handoff transfers the state and responsibility needed for the next stage. KV-aware routing chooses a worker partly by considering reusable cached prefixes.

NVIDIA Dynamo supplies components for coordinating distributed inference workflows, including routing and disaggregated serving. Rate matching means keeping one stage's service capacity compatible with the work arriving from another. Remote direct memory access (RDMA) lets supported network hardware access registered remote memory without the remote CPU copying each transferred payload. Registration makes a memory region eligible for authorized transfers; setup, permissions and synchronization still matter. RDMA is not supplied automatically by an H100. Separating workers can improve specialization or create a larger transfer and queueing cost. This advanced architecture needs its own qualification beyond a local mechanics lab.

The first AIPerf profile and metric definitions were introduced in Lesson 7, then reused for controlled scheduling experiments. Now combine workload design, concurrency and parallel placement in a broader benchmark; optional disaggregation changes where requests and KV state travel.

### Connect client observations to server work

AIPerf sends requests according to a declared workload: endpoint/model identity, input/output distributions, concurrency or arrival rate, warm-up, duration/count, sampling and stops. Its time to first token (TTFT), inter-token latency (ITL)/time per output token (TPOT), completion latency, throughput and errors describe the client boundary. Server queues, scheduled tokens, KV use and preemptions explain which internal work could have produced those observations.

### Follow a disaggregated request through its handoff

A prefill worker processes the prompt and produces KV state. The request and required state then move to a decode worker, which cannot continue until transfer and reconstruction finish. The handoff adds work and waiting; phase specialization helps only if its benefit exceeds those costs. Capacity must be compared in prompt-token and output-token units because a rate of prompt completion alone does not describe the amount of decode work arriving.

KV-aware routing considers both reusable prefixes and active load. A cache-hot worker may still be a poor destination if its queue is long. The first growing queue identifies a rate mismatch worth investigating, whether at prefill, handoff or decode.

### Include later model turns in an application measurement

In an agentic tool loop, the model requests a permitted external operation and the application validates and executes it under its own authority. The returned data becomes untrusted input to a later model invocation. Schema and authority checks, tool latency, new tokenization/prefill work and task-success criteria extend the request path beyond one generation call.

### Keep benchmark conventions consistent

GenAI-Perf is another NVIDIA generative-model benchmarking client and appears in performance-engineering material. It does not define a separate model-execution phase. This course uses AIPerf as its canonical workload/reporting path; NVIDIA documents differences in input formats, metric names and CLI behavior between them. Do not mix files, percentile definitions or token/chunk boundaries across tools. MLPerf Inference provides standardized workload and quality scenarios for comparable benchmark submissions; an ad hoc course smoke run is not an MLPerf-compliant result. The goal is to borrow disciplined workload definitions without relabeling unqualified measurements.

A single successful request says nothing about capacity. Disaggregation and cache-aware routing can also add KV transfer, coordination, and queueing that exceed any phase specialization benefit.

Suppose a stable workload admits ten requests per second, each with 1,000 prompt tokens and 100 output tokens. Ignoring prefix reuse and retries, the prefill pool must sustain 10,000 prompt tokens per second and the service must deliver 1,000 output tokens per second. If prefill selects each request’s first output token, as in the workflow taught here, the decode pool must sustain ten times the remaining 99 positions, or 990 continuation positions per second. Equal request counts do not imply equal token-processing rates or equal worker counts. If either pool falls behind its required rate, its queue grows; cache transfer and latency targets add constraints beyond this simple average-rate calculation.

**Practice labs**

- [Lab 30: Probe an engine's readiness and request schema](reference/labs/30_engine_profile.md)

**Mental model**

AIPerf describes request distributions and reports client metrics. Disaggregation separates phase workers and introduces transfer, routing, and queueing; KV-aware routing tries to preserve useful cache locality.

## 16. Evidence-based inference optimization

**Objective**

Select one measured inference change and defend keep or reject across latency, throughput, capacity, and quality.

**How it works**

A causal inference report tests whether a specified implementation change caused an improvement under a fixed workload and output-quality contract. The baseline is the reference path and the candidate contains the change. An independent trial starts a fresh run; repeated samples inside one run describe a different level of variation. Counterbalancing alternates variant order to reduce systematic first-run or time-order bias. A rejection criterion states when correctness, comparability or performance evidence is insufficient to keep the change.

An attention microbenchmark measures one bounded operation with fixed tensors. A serving experiment includes the model, cache, scheduler, request arrivals and client-visible completion. A faster attention kernel can have a small or absent effect on service latency if another stage dominates. The required capstone report therefore names its measurement boundary before presenting a result; it does not convert an operator timing into time to first token (TTFT) or service throughput.

The course now supplies phase mechanics, workload distributions, metrics, cache/scheduler controls, quality gates, and live-engine boundaries. The required mechanics capstone connects one profiler observation to a controlled attention-kernel decision. A separate qualified live-engine campaign is needed for a serving decision.

A causal decision begins by naming the boundary being optimized. The required mechanics capstone compares attention implementations on fixed tensors. Its baseline, candidate and correctness reference must perform the same attention calculation. That evidence can support an operator-level decision; it cannot establish client TTFT or serving capacity.

A serving decision requires a separate qualified campaign. Artifact and engine/container identity, tokenizer/template, quantization, the input sequence length (ISL)/output sequence length (OSL) workload mix, sampling/stops, hardware allocation and metric definitions fix the comparison. At least three independent baseline campaigns reveal variation across runs, while throughput plotted against p50/p95 TTFT, inter-token latency (ITL) and completion latency shows the latency cost of increasing load.

A short profile connects an observed delay to one candidate change, such as chunking, cache policy, quantization or placement. Fresh restarts, counterbalanced order and a disconfirming control test whether that change caused the result. Output tokens, finish reasons, errors, queues, scheduled tokens, key/value (KV) occupancy, block churn, preemptions and memory reveal changes that a single throughput number would hide.

The final keep/reject decision must satisfy correctness, quality and capacity gates as well as performance goals. It includes the rejected hypothesis, uncertainty, the limits of the available cluster and the next experiment. A valid rejection or an inconclusive result is useful evidence; a speedup is not required to complete the investigation.

Maximum tokens/s can hide unacceptable TTFT/ITL tails, queue growth, quality drift, or fragile capacity. A causal report must include a rejected hypothesis and the limits of the two-node test.

**Practice labs**

- [Lab 32: Build a causal attention optimization report](reference/labs/32_inference_capstone.md)

**Mental model**

An inference decision connects artifact identity and decoding equivalence to ISL/OSL/concurrency, TTFT, ITL/TPOT, throughput, memory, failures, and quality.
