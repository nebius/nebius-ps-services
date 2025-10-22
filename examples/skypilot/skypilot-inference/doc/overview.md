

# Serving LLMs with vLLM: A Practical Inference Guide

This guide teaches the essentials of serving large language models (LLMs) with vLLM. It builds from foundational neural network concepts, through transformers and attention, to practical inference workflows, vLLM features, and operational guidance.

---

## 1. Neural Network Foundations

### What is a Neural Network?
A neural network is a computer program made up of layers of simple units called neurons. Each layer processes information, building up understanding step by step. Early layers find simple features (like word patterns), while deeper layers combine these to understand more abstract ideas (like the meaning of a sentence).

In reality, a "neuron" is just a mathematical function with some numbers (called weights) that it uses to process input. All the neurons and their weights are stored as arrays of numbers in memory (RAM or GPU memory). When you load a neural network onto a GPU, you are copying all these weights and the code for the layers onto the GPU so it can do the calculations quickly.

When a model is very large, it may not fit on a single GPU. In that case, the model is split across multiple GPUs or even multiple computers (nodes). The system divides the layers or parts of the layers between devices. Neurons on different GPUs communicate by sending their outputs (arrays of numbers) over high-speed connections like NVLink or PCIe. This is managed by the deep learning framework (like PyTorch), which handles all the details.

When you use a neural network for inference (getting answers from a trained model), the data flows through the layers in one direction—this is called a "forward pass." During training, the network learns by comparing its output to the correct answer and adjusting its internal settings (using a "backward pass"). Inference only needs the forward pass, which is much faster and uses less memory than training.

### Embeddings, Weights, and Quantization

**Embeddings:**
- When text is tokenized, each token is mapped to a numeric vector called an embedding.
- Embedding values are typically small real numbers (often between -1 and 1), initialized and learned during training.
- Only the embedding weights (the embedding matrix) are saved in the model; prompt-specific embeddings are computed fresh for each request.

**Weights:**
- Weights are the learned parameters of the neural network, stored as arrays of numbers (usually float32 or float16 by default).
- Only the weights are saved after training; no prompt-specific tokens or embeddings are stored.

**Quantization:**
- Quantization reduces the precision of weights (e.g., from float32 to INT8, INT4, or FP8), saving memory and speeding up inference.
- This allows larger models to fit on limited hardware, but may slightly reduce output quality. vLLM supports quantized weights (INT8, INT4, FP8, GPTQ, AWQ, etc.).
- Quantization is a trade-off: lower precision means more efficiency, but potentially less accuracy.

---

## 2. Transformers and Attention

### What is a Transformer?
A Transformer is a type of neural network designed to understand and generate sequences of text, like sentences or conversations. It is especially good at handling context—meaning it can "pay attention" to all the words in your prompt, not just the most recent ones.

**Why is this powerful?**
- The Transformer can generate coherent, context-aware responses because it can relate every word in the prompt to every other word, no matter how far apart they are.
- This is what allows it to answer questions, continue stories, or hold conversations in a way that feels natural.

**Self-Attention:**
- For each token, the model decides how much to "pay attention" to every other token. This helps it understand the meaning of the whole sentence, not just each word in isolation.

**Positional Encoding:**
- Since neural networks don’t naturally understand sequence order, positional encoding adds extra information so the model knows which token comes first, second, third, and so on. Different models use different methods for positional encoding, such as ALiBi (used by BLOOM) or RoPE (used by Qwen and LLaMA).

**Attention, at a glance:**
- Attention lets a token “look back” at the prompt and prior tokens to decide what to generate next. In Transformer LLMs, self‑attention computes, for each token, weighted combinations of all earlier token representations. This enables long‑range dependencies and contextual reasoning that n‑gram or fixed‑window models cannot capture.
- You cannot change a model’s attention mechanism at serve time; you only choose the implementation kernel (backend) compatible with it. See “Attention backends: how to choose”.

**Does attention mean the AI memorizes context?**
- Not exactly. Attention lets the model dynamically focus on relevant parts of the context window (prompt, history, instructions) for each output token. It does not store or recall information like human memory, but it can “integrate” previous context by weighting and combining it at each step. The KV cache is a technical optimization that lets the model reuse these computed weights efficiently, so it can generate long outputs without reprocessing the entire prompt every time.

---

## 3. LLM Inference Workflow

### Step-by-Step Workflow

1. **Input Preparation:** You provide a prompt (text or chat history) to the model.
2. **Tokenization:** The model uses its tokenizer to split your text into tokens (words, subwords, or special symbols), and maps each token to a unique token ID (an integer).
3. **Embedding Lookup:** Each token ID is used to look up a learned embedding vector from the model's embedding matrix. These vectors represent the tokens in a way the neural network can process.
4. **Prefill Phase:** The model processes all input embeddings to set up its internal state (memory for context), using its neural network layers (including attention).
5. **Decoding Phase:** The model generates output tokens one by one. For each step, it uses the current context to predict the next token.
6. **Sampling:** At each decoding step, the model assigns probability scores to possible next tokens. It uses sampling parameters (like `temperature`, `top_k`, `top_p`) to select one token from the most likely candidates. This controls how creative or focused the output is.
7. **Detokenization:** The output token IDs are converted back into readable text using the tokenizer's vocabulary.
8. **Output:** The final text (completion or chat reply) is returned to you.

The workflow repeats steps 5–7 until the desired number of tokens is generated or a stop condition is met.

**Key benchmarking metrics for LLM inference:**
- **Latency**: How quickly the model responds (time to first output token, time to full response)
- **Throughput**: How much work the system can handle (requests per second, tokens per second)
- **Concurrency**: Number of requests or users served at the same time
- **Memory usage**: Amount of GPU/CPU memory consumed during inference

---

## 4. Example: Prompting with "how are you?" (with KV Cache in action)

Suppose you prompt the model with "how are you?" and want it to reply "I am fine". Here’s what the Transformer does, with the KV cache explained at each step:

1. **Tokenization:** The prompt is split into tokens ("how", "are", "you", "?").
2. **Embedding:** Each token is mapped to a numeric vector (embedding). The tokenizer converts each token to a token ID (an integer). The embedding layer uses this token ID as an index to look up a row in the embedding matrix (a table of learned vectors). The result is the embedding vector for that token.
3. **Layer Processing:** The embedding vectors are then passed into the next layer(s) of the neural network (such as self-attention or a hidden layer). In these layers, the embedding vectors are multiplied by weights (and combined with biases and activation functions) to produce new representations.
4. **Self-Attention and KV Cache (Prefill Phase):**
   - The Transformer looks at all tokens in the prompt at once. For each token, it decides how much to "pay attention" to every other token.
   - As it processes the prompt ["how", "are", "you", "?"], the model computes key and value tensors for each token in the attention layers and stores them in the KV cache. This cache now holds the context for the entire prompt.
5. **Layer Processing:** The model passes these representations through many layers, each refining its understanding of the prompt and building up context.
6. **Decoding and KV Cache (Token Generation Loop):**
   - The model predicts the next token ("I") by considering the entire prompt and what it has learned about language. It uses the KV cache to efficiently access the context for ["how", "are", "you", "?"].
   - The new token ("I") is appended to the sequence, and its key and value tensors are added to the KV cache.
   - To predict the next token ("am"), the model only needs to process the new token ("I") and can reuse the cached keys/values for the previous tokens. This is much faster than recomputing everything.
   - This process repeats: each new token ("am", then "fine", then <eos>) is generated by looking at the cached context plus the new token, updating the KV cache at each step.

**Summary:**
- The KV cache allows the model to avoid recomputing attention for the entire sequence at every step. Instead, it only processes the new token and reuses all previous computations, making generation fast and efficient.
- If you add new input (e.g., extend the conversation), the model processes the new tokens, updates the KV cache, and continues generating efficiently.

---

## 5. Additional Concepts

### Context Window
- The context window is the span of tokens the model can consider at once. It includes your prompt, system instructions, chat history, and any assistant output fed back for continuity. If total tokens exceed the model’s maximum context length, the earliest tokens must be truncated or summarized. Larger context supports richer tasks (RAG, long chats) but consumes more memory and increases prefill latency.

### Vocabulary (Vocab)
- “Vocab” is the set of tokens the tokenizer can emit. Larger vocabularies (e.g., ~150k in Qwen) can encode some languages/scripts more efficiently, potentially reducing token counts for the same text. Different tokenizers (BPE, SentencePiece, tiktoken‑derived) segment text differently; this affects token counts, latency, and cost. Always use the tokenizer intended for the model and be cautious when switching variants.


### Positional Encoding Defined

**Positional encoding** is how a model keeps track of the order of words or tokens in your input. Since neural networks don’t naturally understand sequence order, positional encoding adds extra information so the model knows which token comes first, second, third, and so on. This helps the model make sense of sentences and conversations, not just the words themselves.


Different models use different methods for positional encoding, such as ALiBi (used by BLOOM) or RoPE (used by Qwen and LLaMA). You don’t need to set this yourself—the model is trained with a specific method, and it affects which attention backend you can use for serving.

---

## Example: Step-by-Step Workflow from Prompt to Answer

Let's walk through how a neural network (NN) predicts an answer, using the prompt "how are you?" and the expected answer "I am fine". We'll first explain the layers in a simple NN, then show a concrete example.

### Neural Network Layers (Simple Example)

A basic neural network has:

- **Input layer:** Receives the input data (e.g., token embeddings for each word).
- **Hidden layer(s):** Transforms the input using learned weights and activation functions. Can be one or more layers.
- **Output layer:** Produces the final prediction (e.g., the next token or word).

Suppose we have:
- 4 input neurons (for 4 input features/tokens)
- 4 hidden neurons
- 1 output neuron (for simplicity)

![Simple Neural Network](../image/simple_nn.png)

### Step-by-Step Example

1. **Tokenization:**
  - The prompt "how are you?" is split into tokens: ["how", "are", "you", "?"]
  - Each token is converted to a numeric vector (embedding), e.g., [0.1, 0.2, 0.3, 0.4]

2. **Input Layer:**
  - Each input neuron receives one value from the token embeddings. For our example, let's use 4 values: [0.1, 0.2, 0.3, 0.4]

3. **Hidden Layer:**
  - Each hidden neuron computes a weighted sum of all input neurons, adds a bias, and applies an activation function (like ReLU or tanh).
  - For example, Hidden Neuron 1: `h1 = activation(w1_1*0.1 + w1_2*0.2 + w1_3*0.3 + w1_4*0.4 + b1)`
  - This is done for all 4 hidden neurons, each with its own set of weights and bias.

4. **Output Layer:**
  - The output neuron takes the outputs from all hidden neurons, computes a weighted sum, adds a bias, and applies an activation function.
  - For example: `output = activation(v1*h1 + v2*h2 + v3*h3 + v4*h4 + b_out)`
  - The output is a score for the next token (e.g., the probability of "I").

5. **Prediction:**
  - The model selects the token with the highest score as the next word (e.g., "I").it

6. **Repeat for Next Token:**
  - The new input is now ["how", "are", "you", "?", "I"]. The process repeats: the model encodes the new sequence, passes it through the network, and predicts the next token (e.g., "am").
  - This continues until the model outputs "fine" and then a stop token.

### Summary Table (Toy Example)

| Step | Input Tokens                 | Input Values      | Output Token |
|------|------------------------------|-------------------|--------------|
| 1    | how, are, you, ?             | 0.1, 0.2, 0.3, 0.4| I            |
| 2    | how, are, you, ?, I          | ...               | am           |
| 3    | how, are, you, ?, I, am      | ...               | fine         |
| 4    | how, are, you, ?, I, am, fine| ...               | <eos>        |

*Note: In real LLMs, the network is much deeper and more complex, and the input values are high-dimensional embeddings, not just single numbers. But the principle is the same: each layer transforms the input, and the output layer predicts the next token based on all previous tokens.*

---

### Attention defined

**Attention explained (human analogy):**
Imagine reading a book and trying to answer a question about the story. Your brain doesn’t just focus on the last sentence—you recall relevant details from earlier pages, weighing which memories matter most for your answer. In AI, attention is the mechanism that lets a model do something similar: for each new word it generates, it looks back at all previous words (context), deciding which parts are most important for the next step. This is not true memorization, but dynamic focus—like how you might remember a plot twist or a character’s name when needed, but not every word you’ve read.

**Does attention mean the AI memorizes context?**
Not exactly. Attention lets the model dynamically focus on relevant parts of the context window (prompt, history, instructions) for each output token. It does not store or recall information like human memory, but it can “integrate” previous context by weighting and combining it at each step. The KV cache is a technical optimization that lets the model reuse these computed weights efficiently, so it can generate long outputs without reprocessing the entire prompt every time.

**Attention, at a glance**
- What it is and why it matters: Attention lets a token “look back” at the prompt and prior tokens to decide what to generate next. In Transformer LLMs, self‑attention computes, for each token, weighted combinations of all earlier token representations. This enables long‑range dependencies and contextual reasoning that n‑gram or fixed‑window models cannot capture.
- Positional encodings: models bake this in during training and it constrains backend choice:
  - ALiBi: additive linear bias by distance; used by BLOOM-176B.
  - RoPE: rotary embeddings; common in LLaMA/Qwen; sometimes extended for long context.
- Important: you cannot change a model’s attention mechanism at serve time; you only choose the implementation kernel (backend) compatible with it. See “Attention backends: how to choose”.

---

## 3) Models: Architecture and Artifacts

When you download a model (e.g., from Hugging Face), you get more than weights:
- Weights (safetensors shards + index): the learned parameters (dominant size)
- Config (config.json): architecture hyperparameters and positional strategy
- Tokenizer assets: tokenizer.json, tokenizer_config.json, vocab/merges, specials
- Generation defaults (optional): generation_config.json
- Adapters (optional): LoRA/PEFT weights for fine-tuned variants
- Custom code (rare): trust_remote_code

Tokenizer pipeline (why it matters):
1) Normalize text → 2) Pre-tokenize → 3) Subword segmentation (BPE/SP/WordPiece)
4) Post-process (BOS/EOS) → 5) Map to IDs; reverse for detokenization

Minimal example (Hugging Face Transformers):
```python
from transformers import AutoTokenizer
model_id = "bigscience/bloom"
tok = AutoTokenizer.from_pretrained(model_id)
ids = tok.encode("Write a short poem about the moon.", add_special_tokens=True)
print(ids)
print(tok.decode(ids))
```

Operational implications:
- Token counts drive latency and cost; tokenizers differ across models
- Ensure tokenizer and weights are from the same repo/revision

### Licenses: what to check
- License type: fully open source, research‑only, or restricted/commercial. Examples here: BLOOM RAIL (open with use constraints), Tongyi Qianwen license (commercial allowed with terms).
- Commercial use: verify if allowed and under what conditions; some require registration or approval for commercial deployments.
- Redistribution and derivatives: check whether you can redistribute weights, fine‑tuned variants, or quantized artifacts.
- Attribution and restrictions: some licenses include RAIL‑style acceptable‑use clauses or attribution requirements.
- Practical guidance: read the model card’s license section and linked license text; follow any registration steps if required by the provider. When in doubt, consult your legal/compliance team.

### Model profiles and selection: BLOOM-176B vs Qwen-72B
Use official model cards for authoritative specs; below are practitioner notes with links.

- BLOOM-176B (bigscience/bloom)
  - Size and memory: 176B parameters; BF16/FP16 weights alone are ~352 GB. Expect multi-node tensor parallelism and/or quantization for serving.
  - Context and positions: trained with ~2k context and ALiBi positional bias. ALiBi impacts backend choice (avoid FA3; prefer Torch SDPA or Triton).
  - Tokenizer and prompts: HF fast tokenizer; no built-in chat template—provide one for chat-style prompts.
  - Languages: multilingual; check card for coverage. License: BigScience BLOOM RAIL 1.0.
  - Card: https://huggingface.co/bigscience/bloom

- Qwen-72B (Qwen/Qwen-72B)
  - Size and memory: 72B parameters. Authors note BF16/FP16 chat requires on the order of ~144 GB total GPU memory (e.g., 2×A100-80G or 5×V100-32G); INT4 variants can fit ≈48 GB. Plan TP accordingly.
  - Context and positions: supports 32k context via extended RoPE; backend kernels like FlashAttention v2/v3 are typically supported; SDPA is a safe fallback.
  - Tokenizer and prompts: tiktoken-derived large vocab (~152k). Some Transformers flows require trust_remote_code; ensure your runtime matches the model version. Chat variants may provide templates.
  - License: Tongyi Qianwen license; review for commercial use terms.
  - Card: https://huggingface.co/Qwen/Qwen-72B (newer: Qwen1.5-72B)

Choosing between them for an inference task
- Hardware fit: check total VRAM and interconnect; BLOOM-176B generally needs multi-node TP or heavy quantization. Qwen-72B is easier to deploy on fewer high-memory GPUs or with INT4.
- Context needs: if you require >8k context, Qwen-72B’s 32k support is advantageous. BLOOM typically serves around 2k unless specialized. See context-defined.
- Language/compatibility: ensure tokenizer and chat templates align with your inputs; Qwen’s large vocab helps multilingual inputs; BLOOM is broadly multilingual too.
- Backend compatibility: BLOOM’s ALiBi favors Torch SDPA/Triton; Qwen with RoPE can leverage FlashAttention for best throughput when available.
- License and ecosystem: verify your use case aligns with each model’s license; consider community adapters and quantized checkpoints.

---

## 4) vLLM: Core Concepts and Features

vLLM is an inference engine optimized for high throughput and efficiency:
- PagedAttention (paged KV cache) and prefix caching
- Continuous batching of incoming requests
- Optimized attention backends (FlashAttention/FlashInfer/SDPA/TRITON_ATTN)
- Speculative decoding, chunked/disaggregated prefill
- Quantization support (GPTQ, AWQ, INT4/INT8/FP8; KV quant when available)
- Multi-LoRA, multimodal support
- OpenAI-compatible API server (completions/chat/models)
- Metrics and logging for production ops

Supported hardware: NVIDIA (CUDA), AMD (HIP/ROCm), Intel, PowerPC, TPU; pick images/builds compatible with your accelerator.

### Attention backends: how to choose
You select an implementation kernel compatible with the model’s attention and your hardware:
- Torch SDPA (baseline): robust and widely compatible (ALiBi, RoPE). Use when unsure or if other kernels are unstable.
- FlashAttention v2/v3: fastest on NVIDIA when supported; requires compatible head dims and positional encodings (not ALiBi). Great fit for RoPE models like Qwen.
- Triton attention: good alternative on NVIDIA for ALiBi models when you want more speed than SDPA.
- FlashInfer and other backends: specialized high-performance options depending on build. Verify support matrix for your device.

Decision guide
- If model uses ALiBi (e.g., BLOOM-176B): pick Torch SDPA or Triton; avoid FA3. Confirm in logs the kernel actually selected.
- If model uses RoPE and your build includes FlashAttention: pick flash-attn; fall back to SDPA if unsupported.
- On instability during warmup or capture: force SDPA and eager mode first; introduce faster kernels incrementally.

Quick mapping (positional encoding → backends)
- ALiBi: Torch SDPA (safe), Triton (often faster than SDPA), avoid FA3.
- RoPE (standard dims): FlashAttention v2/v3 (fastest on NVIDIA), else SDPA.
- RoPE (nonstandard head dims/build limits): SDPA fallback.
- Unknown/experimental: start with SDPA; verify logs before switching.

CUDA graphs and compile mode:
- Capturing CUDA graphs reduces launch overhead after warmup
- Some stacks are sensitive; eager mode is the robust baseline
- You can disable graphs (e.g., enforce eager) and re-enable after validation

---

## 5) How vLLM Works (Request Flow and API)

High-level flow:
1) Load tokenizer/assets and weights; initialize tensor-parallel ranks
2) Start OpenAI-compatible server on the configured host/port
3) For each request: tokenize → schedule/batch → prefill/decode → detokenize
4) Stream or return final text; update/reuse KV blocks for subsequent tokens

Completions (classic prompt → continuation):
```json
{
  "model": "bigscience/bloom",
  "prompt": "Complete: The benefits of tensor parallelism are",
  "max_tokens": 64,
  "temperature": 0.7,
  "stream": true
}
```

Chat completions (role-structured messages → assistant reply):
```json
{
  "model": "bigscience/bloom",
  "messages": [
    {"role": "user", "content": "Write a one-line haiku about GPUs."}
  ],
  "max_tokens": 64,
  "temperature": 0.7,
  "stream": true
}
```

Chat templates (Transformers ≥ 4.44):
- If a tokenizer lacks a built-in chat template (e.g., BLOOM), you must provide one
- Use a simple Jinja template that formats messages into a single prompt

Health check and examples:
```
curl -fsS http://<HOST>:8000/v1/models
curl -s http://<HOST>:8000/v1/completions -H 'Content-Type: application/json' -d '{"model":"bigscience/bloom","prompt":"Write a short poem about the moon.","max_tokens":64}'
```

---

## 6) Preparing and Configuring Models for Inference


Make these choices before going live:
- Model and revision: pick weights; ensure tokenizer matches
- Dtype: bfloat16 recommended on H100/H200; fp16 where appropriate
- Attention backend: pick a kernel compatible with positional encoding
- For BLOOM/ALiBi, prefer Torch SDPA or Triton attention; avoid FA3
- Parallelism: set `--tensor-parallel-size` to GPUs per node
- Limits: `--max-model-len`, `--max-num-seqs` to fit memory and target latency
- KV cache: plan memory footprint; consider KV quantization if supported
- Chat template: required for chat if tokenizer has none; pass via `--chat-template`
- Quantization: Choose a quantization method (GPTQ, AWQ, INT8, INT4, FP8) to reduce memory usage and enable serving larger models on limited hardware. Quantization compresses model weights to lower precision, trading off some accuracy for speed and efficiency. Pick the method based on your hardware and model support:
  - INT8/INT4: best for aggressive memory savings, may reduce output quality
  - FP8: supported on latest GPUs (H100/H200), balances speed and accuracy
  - GPTQ/AWQ: advanced quantization for specific models, check compatibility
  - You must select quantization before starting the server; it cannot be changed dynamically during inference. Quantization can improve throughput and reduce latency, but may affect output quality.
- Eager vs graphs: start with eager; enable CUDA graphs after validation
- Observability: enable metrics and set logging level appropriately

**GPU Hardware Checklist for Model Serving:**
- GPU memory (VRAM): must be sufficient for model weights, context window, and concurrency
- Supported dtype: check if your GPU supports bfloat16 (H100/H200), fp16, or INT8/FP8 for quantization
- CUDA version: host driver CUDA must be >= container build CUDA
- Accelerator type: ensure compatibility with inference engine (NVIDIA CUDA, AMD ROCm, etc.)
- Interconnect: for multi-GPU, high-bandwidth NVLink or PCIe is recommended
- Attention backend support: verify kernel compatibility (SDPA, FlashAttention, Triton)


Minimal readiness checklist:
- Weights/tokenizer verified; dtype set (bf16 on Hopper)
- Attention backend confirmed in logs (no FA3 for ALiBi models)
- TP size equals number of local GPUs; health probe returns models
- Max context and concurrency tuned; no OOMs during warmup/tests
- Chat template provided when required; 200 responses for both endpoints
- Quantization method selected and compatible with model/hardware
- GPU hardware meets memory, dtype, and backend requirements

Driver/Runtime compatibility (CUDA):
- Host driver CUDA version must be >= container’s build CUDA version
- Validate with `nvidia-smi` (host) and `torch.version.cuda` (container)

---

## 7) Operational Notes and Troubleshooting

Common issue: BLOOM + ALiBi with FlashAttention v3 (FA3)
- Symptom: first request crashes with `AssertionError: Alibi is not supported in FA3`
- Fix: force Torch SDPA (e.g., `--attention-backend torch-sdpa` or `-O.attention_backend=TORCH_SDPA`); keep eager mode if unstable
- Note: Some builds may still route to FA internally; verify backend in logs

CUDA graphs stability
- If warmup or capture crashes, disable graphs (enforce eager), align driver/toolkit to image, then re-enable progressively

Health checks
- Probe `/v1/models`; only proceed when server is bound and healthy

Performance tuning
- Increase batch/concurrency for throughput; monitor latency and KV memory
- Stream responses to improve perceived latency

Security and production hygiene
- Add TLS, auth, rate limits; expose metrics; set resource limits; avoid anonymous public endpoints

---

Appendix: Quick Reference
- Key flags: `--tensor-parallel-size`, `--dtype`, `--download-dir`, `--max-model-len`, `--max-num-seqs`, `--chat-template`
- Where to look in logs: selected attention backend, CUDA graphs capture, health/ready
- Helpful envs (varies by build): `VLLM_ATTENTION_BACKEND`, `VLLM_DISABLE_CUDA_GRAPHS`, `VLLM_COMPILE_MODE`
