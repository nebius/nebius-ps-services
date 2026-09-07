# LLM Inference and GPU Optimization on NVIDIA H100

This course connects autoregressive generation mechanics to memory, scheduling, latency, throughput, quality, and serving-engine behavior. Every optimization keeps workload and decoding semantics explicit.

## 1. Understand inference and prepare a model safely

**Start here**

### What inference is

Inference means using an already-trained model to compute an output for a new input. The model's parameters are the learned numbers used in that computation. Ordinary inference reads those parameters; it does not run an optimizer to learn from the current request. Inference can mean classification, producing an embedding vector, or generating text. This course focuses on autoregressive decoder-only language models: models that generate later tokens using the input and previously generated tokens.

A token is a unit in a tokenizer's vocabulary, represented by an integer ID. It can be a word fragment, punctuation or part of a byte sequence, so tokens are not interchangeable with words or network chunks. The model processes IDs, not human-readable sentences directly. A tokenizer converts between text and IDs; a chat template formats roles and messages into the model's expected input. Weights, tokenizer and configuration must agree.

### Why inference needs its own course

Training pays to change model behavior. Inference repeatedly pays to use that behavior for actual requests. A usable application needs more than a plausible answer: it needs appropriate response time, output quality, memory capacity, throughput and safe input/output handling. A GPU that can hold a model's weights may still lack space for many long requests. A change that increases total throughput can make each user wait longer. These are distinct engineering decisions.

Generating text does not by itself update model weights or guarantee factual correctness. Adding retrieved documents changes the context available to the request, not the learned parameters. Applications may store conversations or separately train from approved data, but that is an additional workflow, not an automatic effect of inference. Training expertise is not required here; GPU Fundamentals and Optimizations provide the device and measurement prerequisites.

### How one request becomes generated text

A large language model (LLM) needs an artifact bundle: its weight files plus the configuration, tokenizer and other data required to reproduce execution. A revision identifies a particular version; a checksum or cryptographic hash is a content fingerprint used to detect changed bytes, not proof that the content is trustworthy. An adapter supplies additional learned parameters associated with a base model. Quantization metadata describes how compact stored values map to numerical weights. A safetensors shard is one tensor-data file in a split weight set. Remote model code is executable implementation code obtained with a model, so allowing it has a different security meaning from reading tensor data. The audit later in this lesson checks these distinct components.

First establish a trusted, compatible model artifact, then load its fixed weights and prepare the engine. For a request, the host formats and tokenizes the prompt, queues it, and supplies token IDs to the model. Embeddings turn IDs into vectors. Transformer layers combine information from allowed preceding positions and produce logits: scores for possible next tokens. Softmax and the chosen decoding rule turn those scores into a token choice. Greedy decoding chooses a highest-scoring token; sampling draws from a configured distribution.

Prefill processes the known prompt and stores reusable attention keys and values, called the KV cache. The logits at the final prompt position supply the first output token. There is no compulsory extra decode pass before that first choice. If the request continues, the next forward processes the newly chosen token, reads earlier cached state and appends that input token's keys and values. It then supplies logits for another token. This feedback loop is decode.

Check end-of-sequence, stop rules and the output budget after token selection. Detokenize permitted output and return or stream it; release request state when the request finishes. The newest selected token only acquires KV entries if it is subsequently processed by a forward pass. While weights stay fixed, the token history, cache, random-number state and scheduler membership can change. CPU and GPU responsibilities depend on the engine: formatting and networking are usually host work, while model tensor computation is GPU work. Never assume every step or every sampling implementation runs on one processor.

### Try a small example and choose your route

Imagine a four-token vocabulary: I, like, GPUs and an end marker. A fixed table of next-token scores can map I to like, like to GPUs, and GPUs to the end marker. Starting with I, three repeated choices yield like, GPUs and stop. The table never changes. A one-token output budget instead stops after like. This is a transparent autoregressive model, not a trained transformer: it has no attention, prefill optimization or KV cache.

Lab 35 runs that example on CPU or an explicitly selected H100 and records scores, probabilities, token choices, stop reason and unchanged parameters. Use it to understand the loop without downloading a model or starting a server. Next, the artifact audit below teaches how to load real models safely; Lesson 2 develops the transformer prefill/decode path. Experienced engineers can proceed after explaining the difference between fixed weights and growing request state, and between a model token and a transport chunk.

**Objective** Verify configuration, tokenizer, weights, adapters, quantization metadata, license, revision, and code-execution requirements.

**Prerequisite bridge** A request will become token IDs, model computations and generated tokens. Before loading any model, establish what artifact supplies those computations and whether it is safe and compatible to load. No running engine is needed for this first metadata audit; GPU Fundamentals and Optimizations supply the device and measurement prerequisites.

**Why it matters** A model name is not a complete artifact identity. Different tokenizer, chat template, adapter, quantization, generation, or remote-code revisions can change token counts, memory, outputs, and performance while appearing to be “the same model.”

**Mechanism** Pin immutable revisions for configuration, tokenizer assets, chat template, safetensors shards and index, adapter plus base model, quantization metadata, generation configuration, and any custom code. Treat `trust_remote_code` as authority to execute repository code, not a convenience flag. A license tag is evidence to review, not a complete legal decision. Separate metadata resolution, CPU download/staging, GPU materialization, kernel/library warm-up, and readiness. Verify checksums or repository revisions, expected tensor shapes/dtypes, tokenizer special IDs, generation defaults, adapter lineage, and runtime compatibility before accepting traffic. Record what is local, fetched, transformed, or compiled without exposing credentials or private paths.
An embedding is a table whose integer index selects a vector. In Lab 35, that vector directly supplies next-token scores, making the fixed transition rule visible without a transformer. Evaluation mode (`model.eval()`) selects evaluation behavior for modules such as dropout; it does not disable autograd by itself. Autograd records operations for later differentiation. `torch.inference_mode()` avoids that recording for the forward-only loop, while `torch.no_grad()` suppresses gradient recording during the explicit table initialization. Neither prevents code from overwriting weights. Save a parameter copy and check equality after generation to establish that this particular run kept them fixed.

**Recall** Why are a model name and visible prompt insufficient to identify a benchmark workload?

**Mental model** Inference behavior depends on an artifact bundle: architecture config, tokenizer and template, weights, generation defaults, adapters, quantization scheme, revision, and optional remote code.

**Practice labs**

- [Lab 35: Trace fixed-parameter autoregressive inference](reference/labs/35_inference_basics.md)
- [Lab 16: Audit model, tokenizer, configuration, and license identity](reference/labs/16_model_artifact_audit.md)

## 2. Follow tokenization, prefill, decode, and stopping

**What it is** Tokenization converts text into token IDs using a vocabulary and its rules. Prefill processes the known prompt and produces attention state plus logits, the scores used to choose the first output token. Decode then processes newly selected tokens one step at a time using the saved state. Detokenization turns output IDs back into text, and stopping decides when generation ends. The first output does not require a separate decode pass after prefill.

Inside attention, a learned projection transforms token vectors into queries (Q), keys (K) and values (V). A query describes what a position seeks, keys provide matching information, and values carry the information mixed into the result. The KV cache stores prior keys and values. In shape notation, B is batch size, S is sequence length and H is hidden-vector width. These components explain the request workflow before its tensor shapes and timing boundaries are examined.

**Objective** Trace one request from text to tokens, prompt processing, iterative decode, detokenization, and stop conditions.

**Prerequisite bridge** The audit fixes the tokenizer and model. This lesson follows one request through those artifacts so later metrics can be attached to exact phases rather than to a single opaque “inference time.”

**Why it matters** Prefill and decode execute different shapes and stress different resources. Model correctness, service correctness, and streaming transport correctness are also separate: valid logits do not guarantee correct stop handling or chunk/token accounting.

**Mechanism** Text passes through the pinned chat template and tokenizer into token IDs `[B,S]`, then embeddings `[B,S,H]`. Q, K, and V projections form attention state; prefill processes the complete prompt in parallel across positions and writes one K/V entry per layer and token. The logits at the final prompt position already provide the distribution for the first output token; sampling it does not require an extra decode forward. Apply the stopping/buffering policy and stream permitted content. If generation continues, feed that sampled token to the next decode forward, which reads prior K/V and appends K/V for its input token, then produces logits for the following output. Repeat sampling and stopping checks. The newest sampled token does not enter KV until it is subsequently processed by a forward pass. Parameters remain fixed, while request state, KV blocks, RNG state, and scheduler membership evolve. Detokenization can buffer partial byte sequences, and one HTTP chunk may contain zero, one, or several model tokens.
For one attention head, scaled dot-product attention compares Q with K through `Q @ K.T / sqrt(head_dim)`, converts allowed scores to weights with softmax, then forms their weighted sum of V. A causal mask excludes future positions. In full-prompt attention that gives a lower-triangular allowed region. A one-token continuation may read all valid cached positions, including its current input, so reusing a square-prefill mask rule without checking absolute positions can hide required history. PyTorch's SDPA API (`scaled_dot_product_attention`) selects an implementation of this operation; choosing the API alone does not identify the selected kernel. These semantics support the first cache labs; Lesson 11 owns backend optimization.

Hugging Face Transformers supplies model and tokenizer classes. A DynamicCache retains per-layer K/V state and grows as new inputs are processed. Pass only the next required input token after prefill, update the attention mask and cache position consistently, and verify cache length against the number of inputs actually forwarded. Greedy selection and stopping do not themselves append K/V. Keep fixed parameters under inference mode, pin the artifacts from Lesson 1 and treat the prefill/continuation schedule as part of correctness.

**Recall** Which phase processes all prompt tokens together and which produces one new position per sequence?

**Mental model** Prefill computes prompt representations, initializes KV state, and supplies the final prompt position's logits for sampling the first output token. If generation continues, decode reads prior KV, processes the last sampled token and appends that input token's KV, then produces logits and samples the following token. Each sampled token passes through the same stopping checks.

**Practice labs**

- [Lab 08: Compare cached attention with full recomputation](reference/labs/08_kv_cache.md)
- [Lab 09: Follow real-model prefill and continuation](reference/labs/09_hf_prefill_decode.md)

## 3. Keep sampling and quality semantics fixed

**What it is** Sampling chooses a next token from a probability distribution derived from the model's logits, or raw scores. Greedy decoding chooses a highest-scoring token instead of drawing randomly. Temperature changes how concentrated the distribution is; top-k restricts choices to k leading tokens, while top-p keeps a leading set whose cumulative probability reaches a threshold. A seed initializes pseudorandom choices for reproducibility; it does not guarantee identical results across different implementations or hardware.

For probabilities [0.7, 0.2, 0.1], greedy decoding chooses the first token, but sampling can choose any allowed token. Changing the policy can change answer length, content and computational work. A quality metric evaluates whether outputs meet the task's needs, whereas a repeatability check asks whether a declared run can be reproduced. Holding sampling settings fixed is necessary for a useful comparison, but identical speed or identical text is not by itself a measure of answer quality.

**Objective** Separate deterministic decoding, stochastic workload control, and output-quality evaluation.

**Prerequisite bridge** The decode loop applies sampling and stopping after logits. Those policies determine both output semantics and how much work each request performs.

**Why it matters** A candidate can look faster only because it emits fewer tokens, stops earlier, uses a different seed, or changes randomness. Deterministic regression and representative stochastic service evaluation answer different questions.

**Mechanism** Define a deterministic profile—often greedy or fixed seed/settings—for exact or tolerance-based engine regression. Define a representative stochastic profile with pinned temperature, top-p/top-k, repetition controls, seed policy, maximum tokens, stop IDs/strings, and number of samples for workload performance. Record prompt tokens, output tokens, finish reason, empty/error responses, and normalized quality metrics. Compare text or token IDs where exact equivalence is required; use task-appropriate evaluators and confidence intervals where stochastic equivalence is expected. Avoid using generated length as both an uncontrolled outcome and a denominator without reporting its distribution. Greedy decoding selects the largest logit. Temperature T > 0 divides logits by T before softmax: smaller T sharpens relative probabilities and larger T flattens them. Top-k keeps the k highest-scoring candidates; top-p keeps the smallest ranked prefix whose cumulative probability reaches p, then renormalizes. If combined, processor order matters: declare the engine's order and threshold convention. A fixed seed aids replay on the same qualified stack, but does not guarantee identical samples across kernels or engines.

**Recall** How do temperature, top-k, top-p, seed, and stop tokens alter generated work?

**Mental model** Decoding configuration changes token choices, output length, termination, and quality. Performance comparisons need a fixed profile or a declared stochastic distribution.

**Practice labs**

- [Lab 17: Compare greedy and seeded stochastic generation](reference/labs/17_sampling_semantics.md)

## 4. Calculate KV-cache capacity for MHA, GQA, and MQA

**What it is** The key-value (KV) cache stores attention information from tokens the model has already processed, avoiding repeated key/value computation during generation. Attention heads are parallel components that form queries, keys and values; head dimension is the width of each head's vectors. Multi-head attention (MHA) uses separate key/value heads for its query heads. Grouped-query attention (GQA) shares each key/value head among a group of query heads. Multi-query attention (MQA) shares one key/value head across all query heads.

These are model-architecture choices, not interchangeable cache switches for arbitrary weights. Cache dtype is the numerical representation used to store its values. Resident state is memory currently occupied on the device, and headroom is capacity left for growth or other allocations. KV size therefore depends on the model's actual layers, key/value heads, head dimension, stored-token count and representation—not simply its parameter count.

**Objective** Derive KV bytes per token and predict concurrency limits.

**Prerequisite bridge** Generation and sampling now define what output is requested and when it stops. Cached key/value tensors retain attention state from processed tokens, so their size connects that fixed token contract to the maximum resident workload.

**Why it matters** KV cache often limits active sequence concurrency, but the ideal formula is only one part of peak memory. Ignoring weights, workspaces, graph pools, communication buffers, fragmentation, and safety reserve creates unsafe admission limits.

**Mechanism** Ideal KV bytes per token are `layers × KV_heads × head_dim × 2(K and V) × bytes_per_element`, multiplied by active tokens across requests. MHA normally has one KV head per query head; GQA shares fewer KV heads across query groups; MQA uses one KV head. Query-head count can stay fixed while KV bytes fall. Then build a phase-aware ledger: resident weights and quantization metadata; load/staging artifacts; prefill activations and attention workspaces; persistent KV block pool; graph-private pools; communication buffers; allocator reserve/fragmentation; and operational headroom. Reconcile formula with measured blocks and peak memory, noting padding, alignment, page metadata, hybrid attention layers, replicas, and engine-specific layouts.

**Recall** Which dimensions determine cached keys and values per layer?

**Mental model** KV cache scales with layers, KV heads, head dimension, dtype bytes, tokens, and active sequences. GQA/MQA reduce KV heads relative to query heads.

**Practice labs**

- [Lab 08: Compare cached attention with full recomputation](reference/labs/08_kv_cache.md)
- [Lab 26: Calculate ideal KV storage for MHA, GQA, and MQA](reference/labs/26_kv_capacity.md)

## 5. Define ISL × OSL × concurrency workloads

**What it is** Input sequence length (ISL) counts prompt tokens and output sequence length (OSL) counts generated tokens. A requested output limit is a maximum; the observed output may be shorter because of stopping. Concurrency counts requests in progress together, while arrival rate counts new requests entering per unit time. These describe different aspects of a workload: ten active requests do not tell you whether they arrived together or gradually.

A workload distribution records the mix of lengths and request types. Burstiness describes arrivals concentrated into short intervals. A queue holds waiting work, and a scheduler chooses what runs next. A service-level objective (SLO) is a declared service target, such as a first-token latency threshold for a stated fraction of requests. Before tuning an engine, define this workload: a long-prompt, short-answer service stresses different phases from a short-prompt, long-answer service.

**Objective** Build a workload matrix that reveals phase and queueing behavior.

**Prerequisite bridge** Prefill cost scales with input sequence length, decode lifetime with output length, and scheduler pressure with arrivals/concurrency. One average prompt hides these interacting axes.

**Why it matters** Engine settings that win for short chat can fail for long-context summarization or long generation. Production traffic may also change by time or tenant, so one permanent configuration is not always best.

**Mechanism** Build an ISL×OSL heat map with weighted cells from observed or declared workload distributions. Include at least short/short, long/short, short/long, and long/long cases, then add arrival process, concurrency, request rate, and burstiness. ISL determines prefill matrices and initial KV; OSL determines decode iterations and growing history reads; concurrency determines batching, queueing, and shared KV pressure. Weight results by expected traffic only after reporting each cell so rare SLO-critical cases remain visible. Treat “seasonal” profiles as versioned operational configurations with explicit switch/revisit conditions rather than auto-tuning folklore.
Padding fills unused positions so different prompt lengths fit a rectangular batch. An attention mask marks which positions are real context. Length bucketing groups similarly sized prompts before padding, reducing unused positions at the cost of extra batches or scheduling constraints. For lengths 3, 4 and 8, one batch occupies 24 positions; separating `[3,4]` from `[8]` occupies 16, while all 15 real tokens remain. Preserve prompt identity, masks and positions, restore the original ordering, and compare each prompt's final real-position logits. Before aggregating logit errors, require each compared reference and candidate vector, norm calculation and resulting relative error to be finite. NaN (not a number) can evade a numerical threshold or be hidden by a maximum over several prompts; infinity is invalid too. A small aggregate value is meaningful only after every prompt passes its own check. Lab 18 uses right-padded prefill and selects `length-1`; that index rule cannot be copied unchanged to a left-padded generation batch.

A recurrence repeatedly computes the next state from the previous one. Lab 25 first projects all input vectors with a matrix, selects the final projected vector, then repeats `state = tanh(state @ weight)`. Tanh is the hyperbolic tangent, a smooth elementwise transformation bounded between -1 and 1. Each iteration depends on the previous state, making it useful for contrasting wide input work with sequential work. Time projection, one update, the repeated updates and the joined path separately. Its rows are tensor batch rows and its iterations are state updates; there is no vocabulary sampling or generated-token count. Use this operator model to learn measurement boundaries before service metrics in Lesson 7.

**Recall** What do input sequence length and output sequence length change independently?

**Mental model** ISL drives prompt processing and initial KV; OSL drives decode iterations and KV growth; concurrency controls batching, queueing, and capacity pressure.

**Practice labs**

- [Lab 18: Reduce padded prefill work with length buckets](reference/labs/18_padding_bucketing.md)
- [Lab 25: Explore input length and recurrent operator work](reference/labs/25_workload_metrics.md)

## 6. Operate vLLM and TensorRT-LLM with Triton

**What it is** Model serving keeps a model available to answer requests from applications. An execution engine performs the model computation and manages resources such as batching and KV state; a server receives requests, invokes execution and returns responses. vLLM provides an LLM execution engine and a serving interface. NVIDIA TensorRT-LLM provides optimized LLM execution components and serving workflows. NVIDIA Triton Inference Server supplies a serving layer that can use a TensorRT-LLM backend. Triton Inference Server is a different project from the Triton GPU-programming language used in some kernel examples.

An endpoint is an addressable service entry point. Its API schema defines request and response fields. Readiness means the service can accept the intended requests, not merely that a process exists; warm-up prepares first-use work before measurement. A container packages an execution environment, and its digest identifies exact image content. These roles must be understood before combining versions, launchers and deployment options.

**Objective** Start a pinned serving stack, verify readiness, and benchmark through its public request boundary.

**Prerequisite bridge** Artifact identity, generation, sampling, KV capacity and the workload matrix are established. Start a controlled single-GPU engine so the next lesson has a real endpoint to measure. Its paging and scheduler internals are introduced only at a high level here and studied in Lessons 8–10.

**Why it matters** A process can be alive before weights, engines, graphs, and caches are ready. Benchmarking an unpinned or warming server mixes startup failures with steady-state latency and makes results irreproducible.

**Mechanism** vLLM provides a serving engine with continuous scheduling and paged KV mechanisms; TensorRT-LLM builds/uses NVIDIA-optimized model engines; Triton can own the inference server boundary and model repository around a supported backend. Pin container digest, engine/framework/CUDA versions, model revision, build flags, dtype/quantization, scheduler/cache settings, and endpoint schema. Launch on a non-public controlled interface, wait for a real readiness condition, send a deterministic correctness probe, warm intended shapes, then run the load client. Preserve server logs and metrics privately; publish sanitized configuration and aggregate evidence. Restart between policy A/B trials.

Low-rank adaptation (LoRA) represents a task-specific weight update as the product of two small matrix factors, added to the transformation performed by a fixed base model. Multi-LoRA serving selects different adapters for different requests while sharing the base weights. This is an advanced, engine-specific extension: qualify each adapter against its exact base-model identity, isolate cache entries between adapters, and measure adapter-switch scheduling, memory pressure, and output quality under concurrent requests. An adapter fitting alone does not prove that many adapters can serve together safely.

Multimodal inference accepts more than one kind of input, such as text and images. A model-specific processor prepares each input type, often using an encoder to turn media into representations the language model can consume. Serving these requests therefore needs a model-specific workload contract. Record how authorized images or other media expand into model tokens, include encoder latency and encoder/cache state, and test scheduling and quality with the actual supported engine. Text-only ISL, cache, and throughput measurements do not automatically describe a multimodal request; avoid collecting or publishing private media.
HTTP is a request/response protocol, and JSON is the structured text format used for these request fields and results. A client sends a model name, prompt and generation settings to the declared endpoint; the server validates them and returns output plus available usage metadata. A thread pool runs a bounded number of client tasks concurrently so blocking network waits need not serialize all requests. Set explicit timeouts, count errors and validate the response fields before including a request in throughput. Offline generation calls the engine directly without this service boundary. Readiness probes, model-name discovery and schema checks must match the chosen OpenAI-compatible or Triton route; sharing HTTP does not make their payloads interchangeable.

**Recall** Why must client, engine, model, and container versions be recorded together?

**Mental model** The serving engine owns model loading, KV management, scheduling, kernels, and metrics; Triton can provide model lifecycle and request handling around TensorRT-LLM.

**Practice labs**

- [Lab 10: Measure batched offline generation with vLLM](reference/labs/10_vllm_offline.md)
- [Lab 11: Measure concurrent completion requests](reference/labs/11_serving_client.md)
- [Lab 30: Probe an engine's readiness and request schema](reference/labs/30_engine_profile.md)

## 7. Measure TTFT, ITL, TPOT, throughput, and goodput

**What it is** Latency measures how long a request or stage takes. Time to first token (TTFT) measures from a declared request boundary to the first generated token. Inter-token latency (ITL) measures the gaps between successive output tokens; time per output token (TPOT) summarizes the post-first-token generation interval per subsequent token under the declared convention. Throughput measures completed work per unit time, while goodput counts only work meeting the specified service and validity criteria.

A percentile describes a position in the observed latency distribution: p95 is a boundary at or below which 95 percent of observations lie. Open-loop load schedules arrivals independently of response completion; closed-loop load allows completions to determine when more work is sent. AIPerf is a client-side tool for generating controlled inference requests and reporting service measurements. Network chunks may contain zero, one or several tokens, so chunk timing and true token timing are not automatically the same metric.

**Objective** Use user-facing latency and server-capacity metrics with explicit boundaries.

**Prerequisite bridge** The workload matrix defines requests and the engine lifecycle provides a controlled endpoint. Metrics now define when clocks start, what each duration represents and how concurrent requests share capacity. Establish these boundaries before changing cache or scheduling policy.

**Why it matters** Tools disagree about ITL/TPOT boundaries, empty chunks, warm-up, and throughput windows. Open-loop and closed-loop generators also produce different queueing behavior, so unlabeled numbers cannot be compared.

**Mechanism** TTFT runs from request submission to the first observed non-empty output token at a declared client boundary. It includes queueing, preprocessing, prefill, first-token sampling/postprocessing, and transport; prefill already computes the first-token logits. Per-gap ITL measures successive token-arrival intervals. With N > 1 output tokens, mean TPOT = (last_token_time - first_token_time)/(N-1). Last-token latency ends at the final content token; response-completion latency ends at the protocol completion event and may include later finish metadata. Therefore (completion_latency - TTFT)/(N-1) includes finish overhead unless completion and last-token timestamps coincide. If streamed chunks bundle tokens, report a chunk-gap proxy or use documented per-token accounting rather than inventing token timestamps. System output throughput is completed output tokens divided by a shared measurement interval. Report per-request generation rate and its denominator explicitly. Goodput counts only work meeting stated latency, error, and quality SLOs. Open-loop arrivals are independent of completion and expose overload; closed-loop clients wait and can conceal saturation. For the first token-aware AIPerf experiment, use the supplied slurm/aiperf.sbatch profile after its image/model prerequisites are qualified. It owns a bounded loopback server, readiness, streaming workload and cleanup. Begin with its fixed workload, identify requested versus observed token counts and the metric denominators, and retain errors as well as successful requests. Lesson 15 expands workload design and routing; it is not a prerequisite for this initial metric-reading exercise.
Server-Sent Events (SSE) is a streaming HTTP format carrying successive text events on one response connection. The supplied client parses `data:` events, ignores events without generated content and timestamps the first nonempty content plus later chunks. One event can contain several tokens or only metadata. This explains why the client reports first-content and inter-chunk timing while a qualified token-aware benchmark is needed for ITL/TPOT. Run the nonstreaming and streaming clients against the same declared workload, retain completion/error counts, and use the whole campaign interval for aggregate throughput.

**Recall** Which metric includes queueing and prefill before the first token arrives?

**Mental model** TTFT spans request arrival to first token; inter-token latency measures output gaps; time per output token may use a different aggregation; throughput counts completed requests or tokens; goodput counts work meeting service objectives.

**Practice labs**

- [Lab 11: Measure concurrent completion requests](reference/labs/11_serving_client.md)
- [Lab 15: Observe first content and streaming gaps](reference/labs/15_streaming_client.md)
- [Lab 25: Explore input length and recurrent operator work](reference/labs/25_workload_metrics.md)

## 8. Allocate and recycle paged KV blocks

**What it is** Paged KV management divides available cache storage into fixed-size blocks instead of reserving one maximum-length contiguous region for every request. Each request maps logical token positions to physical cache blocks, obtains additional blocks as it grows, and returns blocks when they are no longer needed. Logical positions describe the sequence seen by the model; physical blocks describe where its cached values are stored. This is an allocation strategy, not a change to the attention equation.

A free list tracks reusable blocks. Fragmentation is storage wasted by an allocation layout, including unused slots at a block's end. Reference counts record shared ownership; copy-on-write creates a private copy before modifying shared state. Eviction removes cached state, preemption pauses work, and offload or swapping moves state to another storage tier. Their costs differ: freeing cache without preserving or recomputing required state would change the computation, not merely its memory use.

**Objective** Explain how block allocation reduces unusable reserved capacity and where fragmentation remains.

**Prerequisite bridge** The KV formula predicts bytes. Paged allocation decides how those bytes are divided, mapped to requests, and reclaimed as variable-length sequences change.

**Why it matters** Reserving one contiguous maximum-length buffer per request wastes capacity and makes growth difficult. Paged KV improves flexibility but still has partial-block waste, metadata, eviction, and admission limits.

**Mechanism** A global pool contains fixed-size KV blocks. Each request owns a logical block table mapping sequence positions to physical blocks; blocks are allocated as tokens arrive, can be noncontiguous, and are returned when the request finishes. The final block is partly unused unless exactly full. Shared prefix blocks may use reference counts or copy-on-write semantics. Under pressure, the engine may reject admission, preempt and recompute, swap to another tier, or evict reusable prefixes according to policy. Track free blocks, active blocks, block churn, partial-block waste, preemption/recompute/swap, and allocation failures by scheduler round.

**Recall** Why does reserving maximum sequence length per request waste memory?

**Mental model** Paged KV allocates fixed-size blocks as sequences grow and returns them when sequences finish. Block size, free-list policy, and partial final blocks determine overhead. Priority, eviction, host offload, and attention-window reclamation decide which blocks remain resident; every policy trades capacity, recomputation, transfer, and quality semantics.

**Practice labs**

- [Lab 27: Follow paged-cache allocation, growth, and recycling](reference/labs/27_paged_kv.md)

## 9. Use continuous batching and chunked prefill

**What it is** Continuous batching updates the group of active requests between model iterations: completed requests leave and new requests can enter instead of waiting for an entire fixed batch to finish. Chunked prefill divides a long prompt into smaller pieces of prompt-processing work that can be scheduled between other work. The two ideas address different scheduling choices and can be combined. Neither means that one autoregressive request generates all of its dependent output tokens at once.

A token budget limits the token work admitted to a scheduling step. Admission decides which waiting work can start; backpressure slows or rejects new work when capacity is exhausted; fairness describes how capacity and waiting time are shared. A long prefill may delay other requests even if it is individually efficient. The lesson examines that trade-off while preserving total token work and separating an abstract scheduler model from a real engine.

**Objective** Compare scheduling policies under mixed prompt and decode work.

**Prerequisite bridge** Paged KV makes dynamic admission possible. The scheduler decides which prefill chunks and decode tokens share each model iteration under a token and capacity budget.

**Why it matters** Prefill-first scheduling can block decode behind one long prompt; decode-first scheduling can starve new requests. Chunking changes head-of-line delay but adds scheduler, launch, and repeated-boundary cost.

**Mechanism** Continuous batching rebuilds the active batch between iterations, admitting new work and removing finished requests. Each scheduler round chooses a budget of prompt and decode tokens subject to KV availability and engine constraints. Chunked prefill splits long prompts so decode tokens can interleave with smaller prompt segments. Trace queue arrival, admission, tokens scheduled by phase, chunk size, active requests, free blocks, preemptions, and completion. Run A/B trials with independent server restarts and counterbalanced order because cache, graph, and allocator state can carry across configurations. Define overload behavior—bounded queue, rejection, or backpressure—rather than letting latency grow silently.
A deterministic policy-equivalence check compares corresponding outputs before accepting a scheduling change. A response digest hashes the exact returned text bytes into a fixed-size fingerprint, using the content-identity idea from Lesson 1. The client records digests; the owning campaign pairs records with the same prompts, sampling settings and artifact identity and rejects mismatches. Restart each engine trial and alternate policy order so warmed state does not consistently favor one variant. Equal greedy digests establish the bounded fixture's equality, not general stochastic-distribution or task-quality equivalence.

**Recall** Why can a large prefill delay decode tokens for existing requests?

**Mental model** Continuous batching admits and retires sequences between iterations. Chunked prefill limits prompt work per scheduling round so decode can retain latency service. An overlap scheduler prepares upcoming work on the CPU while already-submitted GPU work runs. Piecewise compilation or graph capture divides model execution into eligible compiled or captured regions and other regions. This partitions the execution graph; chunked prefill partitions prompt-token work. The serving engine must qualify its CUDA Graph capture sizes and its compiled shape ranges or fallback paths separately.

**Practice labs**

- [Lab 18: Reduce padded prefill work with length buckets](reference/labs/18_padding_bucketing.md)
- [Lab 28: Compare full-prefill and chunked scheduling](reference/labs/28_continuous_batching.md)
- [Lab 34: Preserve outputs while changing a serving policy](reference/labs/34_policy_equivalence_client.md)

## 10. Reuse prefix KV safely

**What it is** Prefix caching reuses attention state for a beginning token sequence already processed under compatible model and execution settings. A cache hit finds reusable state; a miss requires computation. For example, two requests with identical initial token IDs may reuse part of prefill, but matching visible text alone is insufficient if tokenization or model settings differ. This is reuse of previously computed KV state, not returning a previously generated answer.

Reuse identity is the information proving two cache entries mean the same computation. A hash is a compact content fingerprint used for indexing or identity checks. A radix tree organizes sequences by shared prefixes and can help find the longest reusable beginning. Eviction removes retained entries to recover capacity. Longer retention can save more prompt work or reduce room for active requests, so a successful cache policy must balance reuse with storage and isolation.

Cache tiering retains reusable KV state outside its fastest memory tier. HBM is device memory used by attention; host DRAM and storage can retain inactive state for later restoration. Eviction discards an entry, offload moves or copies it, and restoration brings compatible state back before reuse. A time-to-live (TTL) limits retention; least recently used (LRU) eviction selects the least recently accessed eligible entry under capacity pressure. A TTL is not a guarantee that capacity will retain an entry that long.

**Objective** Identify reusable prefixes and measure hit benefits without changing request semantics.

**Prerequisite bridge** Paged KV blocks can outlive or be shared across request prefixes. Prefix caching avoids repeated prefill only when the model’s exact token-level computation is identical.

**Why it matters** Text that looks the same can tokenize differently, and one changed token invalidates all following KV state. Incorrect reuse is a correctness bug; aggressive retention also competes with active-request capacity.

**Mechanism** Cache identity includes model/engine artifact, adapter, tokenizer/template result, token IDs, positional scheme, relevant multimodal inputs, and any computation-affecting metadata. Matching occurs over exact token blocks; a near match reuses only the common complete prefix. Immutable shared blocks require reference tracking and safe reclamation. Record lookup, matched tokens/blocks, miss reason, prefill tokens saved, TTFT, cache occupancy, eviction, and effect on admission. Use independent cold and warm trials, verify output equivalence, and prevent one candidate from inheriting warmer cache state.

Prefix reuse needs an index from an exact token prefix and its model context to reusable KV state. A hash/block design identifies reusable complete token blocks; a radix tree shares a path of token prefixes and branches where requests diverge. SGLang calls its radix-tree-based reuse approach RadixAttention. These are alternative indexing organizations, not alternatives to computing attention itself. Both require valid ownership, eviction and isolation rules. A similar-looking prompt or a hash without the complete identity contract is insufficient. The supplied prefix experiments use the course's pinned serving path; an SGLang comparison is optional source study, not an installed or qualified engine lab.

Cache value depends on whether a compatible prefix is still present when the next request arrives and whether restoration costs less than recomputing prefill. Account for bytes per token, reused length, transfer bandwidth and startup, cache lookup/reconstruction, write traffic, queueing and active-request headroom. Report device, host and storage hits separately: a slow-tier hit can still be slower than recomputation. Saving prefill does not directly remove decode steps. A larger working set or longer reuse interval may require more retention capacity, but more cache can also create churn, disk writes or resource contention.

Lab 36 models equal-sized inactive whole prefixes in three exclusive LRU tiers. Eviction from device demotes to host, then storage; the last tier discards its oldest entry when full. Idle TTL is refreshed on access and preserved on demotion; an entry expires at its deadline even if no capacity eviction occurred. The explicit namespace stands for the complete model/token/adapter/position/layout identity introduced above. A revision change selects a new namespace. A modeled worker restart clears device and host caches while leaving stored entries eligible until expiry. Real recovery additionally requires valid persistent metadata, supported cache formats and complete identities; persistence alone does not prove safe reuse.

GPUDirect Storage (GDS) provides supported direct DMA paths between storage and GPU memory that can avoid a CPU-memory bounce buffer. GPUDirect RDMA concerns direct device-memory access by devices such as network adapters; network storage may use RDMA as part of a GDS path, but the two names are not interchangeable. CPU software still coordinates the control path. A cuFile call or a fast read does not itself prove the direct path: filesystem, driver, topology and operation support matter, and compatibility paths can stage through host memory. Lab 36 performs no real storage I/O or GDS operation. An optional qualified experiment must independently establish the selected path and compare actual restore latency, write cost and serving quality under the same workload.

**Recall** Which prompt tokens must match for their cached KV to be reusable?

**Mental model** Prefix caching reuses KV for identical token prefixes under compatible model, adapter, position, and cache settings.

**Practice labs**

- [Lab 20: Investigate reusable prompt prefixes in a live engine](reference/labs/20_prefix_cache_client.md)
- [Lab 34: Preserve outputs while changing a serving policy](reference/labs/34_policy_equivalence_client.md)
- [Lab 36: Model KV retention and restore decisions](reference/labs/36_kv_tiering.md)

## 11. Select SDPA and attention backends by phase

**What it is** Scaled dot-product attention (SDPA) is an operation: compare query vectors with key vectors using dot products, scale the scores, apply the allowed-position mask and softmax, then use the resulting weights to mix value vectors. Softmax converts scores into nonnegative weights that sum to one across the considered keys. An attention backend is the concrete implementation that executes this operation. PyTorch's SDPA API can dispatch—select at runtime—among supported backends based on inputs and configuration.

A straightforward implementation materializes the full score matrix in memory. Tiled or fused implementations process pieces and combine stages to reduce intermediate storage and traffic. FlashAttention is an algorithm family for such memory-efficient attention; SDPA does not mean that FlashAttention was necessarily selected. TensorRT-LLM's XQA names a specialized generation-attention path with its own support conditions. Prefill and decode have different query/cache shapes, so the chosen implementation must be observed for each phase.

**Objective** Compare materialized attention and supported fused backends for prefill and decode shapes.

**Prerequisite bridge** The generation and KV lessons explain Q queries and stored K/V state; batching and prefix reuse explain which requests share execution. Establish an uncompressed, fixed-dtype attention baseline now, before Lesson 12 changes numerical representation through quantization.

**Why it matters** “Flash attention” or SDPA is not one universal kernel. Masks, dtype, head dimension, sequence, GQA layout, dropout/training mode, and software version can select different implementations or fall back.

**Mechanism** A materialized path computes and stores an attention score matrix before softmax and V aggregation. Fused tiled backends keep score fragments on chip and avoid full HBM materialization while preserving causal semantics. Prefill has many query positions and large GEMM-like tiles; decode has one or few queries attending over growing KV, often becoming bandwidth- and latency-sensitive. Use PyTorch SDPA controls or engine logs/profilers to record the actual backend, kernel names, score/intermediate allocation, dtype/shape, and output equivalence. Separate attention improvement from tokenizer, scheduler, and cache changes.

**Recall** Why is a square prompt attention matrix different from single-position decode?

**Mental model** Prefill exposes long query and key dimensions; decode has a small query against a growing KV history. Backend eligibility and efficiency vary with dtype, head size, mask, and phase. TensorRT-LLM's XQA is an MQA/GQA generation optimization with a limited support matrix and heuristic dispatch, so it must be observed rather than assumed.

**Practice labs**

- [Lab 24: Compare materialized attention with SDPA dispatch](reference/labs/24_sdpa_attention.md)

## 12. Quantize weights and KV with quality gates

**What it is** Quantization represents values using a smaller set of numerical levels, often reducing storage. A scale—and sometimes a zero point mapping real zero into that representation—relates stored values to approximate original values. Dequantization reconstructs those approximations for computation. Calibration uses representative data to choose useful ranges or scales; an outlier is a value unusually large or small relative to most others and can make a shared scale less accurate.

Weight-only quantization changes stored model weights; activation quantization changes intermediate computation values; KV quantization changes cached attention state. Notation such as W4A16 means four-bit weights and sixteen-bit activations, while W8A8 means eight-bit weights and activations. Activation-aware Weight Quantization (AWQ) uses activation statistics from calibration inputs to choose weight scaling that protects important channels, meaning selected feature dimensions. GPTQ is a post-training weight-quantization method that uses calibration-derived sensitivity information to compensate for rounding error by adjusting weights not yet quantized. Both use model behavior to limit error, but their calibration and weight-adjustment procedures differ. Quantized storage alone does not prove native low-bit arithmetic: the execution path may unpack or dequantize first. Memory, latency and output quality therefore need separate checks.

**Objective** Compare memory, kernel support, latency, throughput, and output quality for supported quantization paths.

**Prerequisite bridge** The serving ledger separates weights, activations, and KV. Quantization can target each independently, with different scale metadata, kernels, and quality risks.

**Why it matters** Smaller tensors do not guarantee faster requests. Dequantization, calibration, fallback kernels, irregular shapes, or memory-bound phase changes can erase gains; additional capacity may be the primary benefit even when single-request latency is unchanged.

**Mechanism** Weight quantization stores model matrices in lower-bit formats with per-tensor, per-channel, group, or block scales and possibly zero points; kernels must consume the representation efficiently. Activation quantization changes intermediate paths and often needs calibration or dynamic scales. KV quantization reduces bytes written/read per token but adds encode/decode and may affect long-context attention. Pin the quantization recipe, calibration data, excluded layers, metadata, engine kernel support, and fallback behavior. Compare artifact/load size, resident HBM, peak memory, admitted concurrency, phase latency, throughput, and task-specific quality using the same prompts and stop policy.
The supplied symmetric INT8 recipe uses one positive scale per tensor: choose `scale = max(abs(values))/127`, with the implementation's positive guard for a zero maximum, then encode `clamp(round(values/scale), -127, 127)` and reconstruct `integer*scale`. A simple illustrative scale of 0.10 maps 0.26 to integer 3 and back to 0.30. The scale is extra stored metadata. Lab 29 dequantizes weights before BF16 matrix computation and measures KV reconstruction separately; compressed storage does not make either a native INT8 attention or GEMM benchmark.

Validate the weight operation and reconstructed KV against their own references. Relative L2 is `norm(candidate-reference)/norm(reference)`, the aggregate measure introduced in Fundamentals Lesson 9. This lab uses nonzero random references without an epsilon in that denominator; a zero-reference extension must first declare an absolute or guarded-error policy. Report logical bytes, conversion cost and numerical error separately, then use a task-quality evaluation before adopting a real model's quantization.

**Recall** Why can smaller weights fail to speed up an operation?

**Mental model** Quantization changes storage, scale metadata, conversion, kernels, and sometimes accumulation. KV quantization targets a different capacity term from weight quantization. NVIDIA Model Optimizer can produce engine artifacts for supported FP8, AWQ, and GPTQ recipes, but format names alone do not prove H100 kernel dispatch or quality.

**Practice labs**

- [Lab 29: Inspect quantized weight and KV storage mechanics](reference/labs/29_quantization.md)

## 13. Verify speculative decoding acceptance and recovery

**What it is** Speculative decoding tries to reduce sequential target-model calls by proposing several tokens cheaply and asking the target model to verify them together. The draft or proposal mechanism supplies candidate tokens; the target is the model whose intended output behavior must be preserved. An accepted prefix is the consecutive beginning of the proposal that passes verification. A rejection requires a defined recovery step before generation continues. Some schemes also obtain a bonus token from the target after all proposed tokens are accepted.

Acceptance rate is the fraction of proposed tokens accepted, but it does not include the cost of producing or checking them. Greedy verification can compare token choices directly; stochastic verification needs a correction rule that preserves the target probability distribution, not just plausible-looking text. Drafting can come from another model or another supported proposal mechanism. This is an execution technique, not permission to replace the target's semantics with a faster model's answers.

**Objective** Explain draft proposals, target verification, accepted prefixes, rejection recovery, and bonus tokens.

**Prerequisite bridge** After prefill supplies the first output token, ordinary decode uses one target-model forward pass for each subsequent token. Speculation attempts to amortize target calls by verifying several cheaper draft proposals at once without changing the target distribution.

**Why it matters** Draft generation and verification are not free. Low acceptance, large batches, mismatched tokenizers, or recovery mistakes can make speculation slower or incorrect.

**Mechanism** The draft proposes `k` tokens. The target evaluates their conditional probabilities in a verification pass. A compatible acceptance rule retains the longest accepted prefix; at the first rejection it samples or selects a recovery token from the corrected target distribution, discards later draft tokens, and resumes. If all proposals are accepted, an additional target “bonus” token may be available depending on the algorithm. Greedy and stochastic modes require different equivalence reasoning. Record proposed, accepted, rejected, recovery and bonus counts; draft time; verification time; target-call count; final token IDs/finish reason; and acceptance by workload cell.

Proposal methods vary. A smaller draft model can generate candidate tokens; prompt lookup or n-gram methods reuse matching token sequences without running a separate neural draft. Medusa adds proposal heads to a model. EAGLE-family methods use model features in an auxiliary drafting process rather than simply attaching independent next-token classifiers. Multi-token prediction (MTP) uses model-specific heads or modules trained to propose future tokens. These labels do not establish identical algorithms, supported models or distribution guarantees. The target still needs the appropriate verification and acceptance/recovery procedure. Select a proposal mechanism supported by the pinned engine and model; compare acceptance, extra work, memory and output semantics before interpreting a speedup.
For the greedy mechanics case, think of the target as a transition function: the current token determines its next-token scores. Check draft tokens in order against the target's greedy choices. For draft `[b,c,d]`, if b matches and c does not, emit b and the target's replacement for c, discard d, and restart drafting from the accepted history. If all draft tokens match and the output budget permits, append the available target bonus token. This preserves the target-only sequence in Lab 23's first-order model. Its embedding, linear projection and GELU activation use the operator definitions from Fundamentals Lesson 2. A full transformer must condition verification on the complete causal history; the toy transition property is not a general model assumption. Measure drafting, verification and recovery together rather than inferring speed from acceptance alone.

**Recall** What must the target model still verify for exact speculative sampling?

**Mental model** A draft proposes several tokens; the target scores them together; the algorithm accepts a prefix under its sampling rule and recovers correctly after rejection.

**Practice labs**

- [Lab 23: Trace speculative acceptance, rejection, and recovery](reference/labs/23_speculative_decoding.md)
- [Lab 33: Check greedy equivalence in a speculative engine campaign](reference/labs/33_speculative_engine_client.md)

## 14. Choose inference DP, TP, PP, and EP

**What it is** Serving data parallelism (DP) replicates a complete model and routes independent requests to replicas. Tensor parallelism (TP) divides a model operation's tensors across workers, pipeline parallelism (PP) divides layers into stages, and expert parallelism (EP) places different mixture-of-experts (MoE) components on different workers. In an MoE layer, a router chooses which expert networks process each token. These strategies change whether a request stays within one replica or requires several workers to cooperate.

A collective is group communication, and topology is the actual path connecting the workers' devices. KV ownership describes which worker holds the attention state needed to continue a request. Serving replicas do not average training gradients per request: their model weights are fixed during inference. Partitioning can help a model fit while adding communication on its token-generation path, so parallelism must be understood as a placement decision before it is treated as a performance feature.

**Objective** Select placement from model fit, request rate, latency, and communication.

**Prerequisite bridge** Scheduling shares one engine instance. Parallelism decides whether to replicate that instance or partition its weights, layers, context, or experts across devices.

**Why it matters** Training parallelism names are reused in serving but the synchronization contract differs. Serving data parallel replicas do not all-reduce gradients per request, while TP communication lies on every decode token’s latency path.

**Mechanism** Serving DP replicates the model and routes independent requests to replicas, increasing aggregate throughput if one model fits per GPU. TP shards layer matrices and exchanges partial activations each layer, enabling model fit at the cost of latency-sensitive collectives. PP splits layers and transfers activations through stages; bubbles and per-request latency matter. EP shards MoE experts and routes tokens with all-to-all; batch composition and expert balance determine utilization. Map state, request ownership, communication direction, and API entry point. Bind parallel groups to trusted private fabric and verify readiness through a controlled endpoint; a loopback client boundary is not public exposure.
For stored weights shaped `[output_features,input_features]`, a linear layer computes `Y = X @ W.T`. Column parallelism splits output features, producing different columns of Y that an all-gather concatenates in rank order. Row parallelism splits input features and the corresponding weight columns; each rank produces a partial dot product and all-reduce sums them. For `X=[2,3]` and one weight row `[5,7]`, the two row-partial results are 10 and 21 and their sum is 31. Broadcast establishes identical reference inputs/weights before the experiment; it is setup, not part of the measured operator.

All-to-all sends different token groups to their chosen expert owners. The receiving rank applies its local expert operation and sends results back according to the inverse routing, restoring each token to its original position. Keep split counts, order and reference values explicit. Lab 12's deterministic expert transform tests this round trip without backward; Lab 19 tests the matrix reconstruction. Both use the process-group and slowest-rank timing principles from Fundamentals Lesson 12 and Optimizations Lessons 11–12 before attempting a live parallel engine.

**Recall** Which strategy replicates a model for independent requests and which splits one operator?

**Mental model** Data parallel serving increases replicas; TP splits model tensors; PP splits layers; EP places experts and routes tokens. Context-parallel serving partitions long prefill or decode context with different KV and communication effects. Each changes fit, communication, and scheduling.

**Practice labs**

- [Lab 00: Verify the two-node inference mechanics platform](reference/labs/00_cluster_preflight.md)
- [Lab 12: Route inference tokens to expert owners](reference/labs/12_moe_expert_parallel.md)
- [Lab 19: Compare inference tensor-partition communication patterns](reference/labs/19_tensor_parallel_linear.md)

## 15. Benchmark with AIPerf and bound disaggregation claims

**What it is** AIPerf is a workload-generation and measurement client: it sends controlled requests to an inference endpoint and records service behavior. It is not the model execution engine. Disaggregated serving assigns prefill and decode to separate workers, transferring the request's KV state between them. A worker pool is a set of instances assigned a role; a handoff transfers the state and responsibility needed for the next stage. KV-aware routing chooses a worker partly by considering reusable cached prefixes.

NVIDIA Dynamo supplies components for coordinating distributed inference workflows, including routing and disaggregated serving. Rate matching means keeping one stage's service capacity compatible with the work arriving from another. Remote direct memory access (RDMA) lets supported network hardware access registered remote memory without the remote CPU copying each transferred payload. Registration makes a memory region eligible for authorized transfers; setup, permissions and synchronization still matter. RDMA is not supplied automatically by an H100. Separating workers can improve specialization or create a larger transfer and queueing cost. This advanced architecture needs its own qualification beyond a local mechanics lab.

**Objective** Generate controlled load and explain when prefill/decode disaggregation or KV-aware routing might help.

**Prerequisite bridge** The first AIPerf profile and metric definitions were introduced in Lesson 7, then reused for controlled scheduling experiments. Now combine workload design, concurrency and parallel placement in a broader benchmark; optional disaggregation changes where requests and KV state travel.

**Why it matters** A single successful request says nothing about capacity. Disaggregation and cache-aware routing can also add KV transfer, coordination, and queueing that exceed any phase specialization benefit.

**Mechanism** Configure AIPerf with pinned endpoint/model identity, input/output distributions, concurrency or request rate, warm-up, duration/count, sampling/stops, and metric definitions. Correlate its TTFT, ITL/TPOT, completion latency, throughput, and errors with server queue, scheduled tokens, KV use, and preemptions. In disaggregated serving, prefill workers produce KV and hand requests plus cache state to decode workers; capacity must be rate-matched in prompt-token and output-token units, and transfer/reconstruction lies on the handoff path. KV-aware routing combines reusable-prefix overlap with active load; a cache-hot worker can still be the wrong target when overloaded. Analyze the first growing queue. An agentic tool loop lets a model request a permitted external operation, receive its result and use that result in a later model turn. The application executes the tool and controls its authority; the returned result becomes input to a subsequent model invocation. Such loops add schema and authority validation, untrusted tool output, new tokenization/prefill episodes and task-success metrics.

GenAI-Perf is another NVIDIA generative-model benchmarking client and appears in performance-engineering material. It does not define a separate model-execution phase. This course uses AIPerf as its canonical workload/reporting path; NVIDIA documents differences in input formats, metric names and CLI behavior between them. Do not mix files, percentile definitions or token/chunk boundaries across tools. MLPerf Inference provides standardized workload and quality scenarios for comparable benchmark submissions; an ad hoc course smoke run is not an MLPerf-compliant result. The goal is to borrow disciplined workload definitions without relabeling unqualified measurements.

**Recall** What rate must a disaggregated prefill tier match with a decode tier?

**Mental model** AIPerf describes request distributions and reports client metrics. Disaggregation separates phase workers and introduces transfer, routing, and queueing; KV-aware routing tries to preserve useful cache locality.

**Practice labs**

- [Lab 30: Probe an engine's readiness and request schema](reference/labs/30_engine_profile.md)

## 16. Deliver a causal inference optimization report

**What it is** A causal inference report tests whether a specified implementation change caused an improvement under a fixed workload and output-quality contract. The baseline is the reference path and the candidate contains the change. An independent trial starts a fresh run; repeated samples inside one run describe a different level of variation. Counterbalancing alternates variant order to reduce systematic first-run or time-order bias. A rejection criterion states when correctness, comparability or performance evidence is insufficient to keep the change.

An attention microbenchmark measures one bounded operation with fixed tensors. A serving experiment includes the model, cache, scheduler, request arrivals and client-visible completion. A faster attention kernel can have a small or absent effect on service latency if another stage dominates. The required capstone report therefore names its measurement boundary before presenting a result; it does not convert an operator timing into TTFT or service throughput.

**Objective** Select one measured inference change and defend keep or reject across latency, throughput, capacity, and quality.

**Prerequisite bridge** The course now supplies phase mechanics, workload distributions, metrics, cache/scheduler controls, quality gates, and live-engine boundaries. The required mechanics capstone connects one profiler observation to a controlled attention-kernel decision. A separate qualified live-engine campaign is needed for a serving decision.

**Why it matters** Maximum tokens/s can hide unacceptable TTFT/ITL tails, queue growth, quality drift, or fragile capacity. A causal report must include a rejected hypothesis and the limits of the two-node test.

**Mechanism** Freeze artifact, engine/container, tokenizer/template, quantization, workload heat map, sampling/stops, hardware allocation, and metric definitions. Run at least three independent baseline campaigns and plot throughput against p50/p95 TTFT, ITL, and completion latency across load. Preserve output tokens, finish reasons, errors, server queue, scheduled tokens, KV occupancy, block churn/preemptions, and memory. Use a short engine/system profile to select one mechanism-matched change such as chunking, cache policy, quantization, or parallel placement. Restart, counterbalance trial order, run a disconfirming control, and apply correctness/quality plus capacity gates. State the keep/reject decision, rejected hypothesis, cluster limitation, and next experiment.

**Recall** Which workload dimensions and metric definitions must accompany the result?

**Mental model** An inference decision connects artifact identity and decoding equivalence to ISL/OSL/concurrency, TTFT, ITL/TPOT, throughput, memory, failures, and quality.

**Practice labs**

- [Lab 32: Build a causal attention optimization report](reference/labs/32_inference_capstone.md)
