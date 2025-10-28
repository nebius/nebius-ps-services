# Deployment of vLLM as a High-Performance Inference Engine with SkyPilot

Large language models (LLMs) have revolutionized natural language processing, but serving them efficiently at scale remains a technical challenge. **vLLM** is an open-source, high-throughput inference engine designed to maximize the performance of transformer-based LLMs, supporting advanced features like paged attention, tensor parallelism, and efficient memory management. vLLM is compatible with Hugging Face models and exposes an OpenAI-compatible API, making it a powerful drop-in solution for production and research environments.

This article provides a comprehensive, hands-on guide to deploying and operating vLLM as an inference engine, with a focus on real-world implementation using [SkyPilot](https://skypilot.readthedocs.io/) for orchestration on Kubernetes. While SkyPilot simplifies cloud resource management and deployment, the centerpiece of this guide is vLLM itself, its architecture, configuration, and operational best practices. All implementation steps and configuration examples referenced here are available in the repository: [nebius-ps-services/examples/skypilot/skypilot-inference](https://github.com/nebius/nebius-ps-services/tree/main/examples/skypilot/skypilot-inference).

---

## Preparing and Configuring Models for Inference

**Checklist before deployment**:
- Model and revision: select the correct model weights (files) and make sure the tokenizer files are from the same model version. This ensures text is split and mapped to token IDs exactly as during training.
- GPU memory (VRAM): must be sufficient for model weights, context window, and concurrency
- CUDA version: host driver CUDA must be >= container build CUDA
- Dtype: bfloat16 recommended on H100/H200; fp16 where appropriate
- Attention backend support: verify kernel compatibility with positional encoding (SDPA, FlashAttention, Triton)
- For BLOOM/ALiBi, prefer Torch SDPA or Triton attention; avoid FA3
- Parallelism: set `--tensor-parallel-size` to GPUs per node
- Limits: `--max-model-len`, `--max-num-seqs` to fit memory and target latency
- KV cache: plan memory footprint; consider KV quantization if supported
- Chat template: If the model or tokenizer does not provide a built-in chat template, you must supply one manually (e.g., via `--chat-template`).
- Quantization: Choose a quantization method (GPTQ, AWQ, INT8, INT4, FP8) to reduce memory usage and enable serving larger models on limited hardware. Quantization compresses model weights to lower precision, trading off some accuracy for speed and efficiency. Pick the method based on your hardware and model support:
  - INT8/INT4: best for aggressive memory savings, may reduce output quality
  - FP8: supported on latest GPUs (H100/H200), balances speed and accuracy
  - GPTQ/AWQ: advanced quantization for specific models, check compatibility
  - You must select quantization before starting the server; it cannot be changed dynamically during inference. Quantization can improve throughput and reduce latency, but may affect output quality.
- Eager vs graphs: start with eager mode (the default, where each operation is executed immediately and errors are easier to debug); enable CUDA graphs after validation for better performance. Eager mode is more robust and helps diagnose issues, while CUDA graphs can improve throughput once the setup is stable. You can enforce eager mode with the `--enforce-eager` flag if you encounter instability or crashes with CUDA graphs. To enable CUDA graphs in vLLM, use the `-O.use_cudagraph=true` flag when starting the server.
- By default, if you do not specify --enforce-eager or set -O.cudagraph_mode=NONE, vLLM will attempt to use CUDA Graphs for performance, provided the model and environment support it. CUDA Graph mode will be enabled by default unless you explicitly disable it.
- Note on Docker Images for vLLM: For most use cases, the official `vllm/vllm-openai` Docker image is recommended—it is maintained, tested, and suitable for standard LLM inference. Build a custom image only if you need extra dependencies, custom vLLM code, or must comply with enterprise security requirements. When customizing, start FROM the official image for best compatibility.
- Observability: enable metrics and set logging level appropriately

---

## Step-by-Step Implementation: Deploying vLLM with SkyPilot on Nebius Managed K8s Cluster

This section provides a step-by-step guide for deploying vLLM as a high-performance inference engine using SkyPilot on Kubernetes. Before proceeding, ensure you have a running Kubernetes cluster. If you need to create a Nebius Managed K8s cluster, refer to the official documentation: [Create a Nebius K8s cluster](https://docs.nebius.com/kubernetes/clusters/manage). The following instructions reference implementation details and scripts available in this repository: [nebius-ps-services/examples/skypilot/skypilot-inference](https://github.com/nebius/nebius-ps-services/tree/main/examples/skypilot/skypilot-inference)

### 1. Prerequisites

- **Install SkyPilot:**
  ```sh
  ./skypilot-install.sh
  ```
- **Set Up Credentials:**
  ```sh
  ./nebiaus-sa-setup.sh
  ```
  This creates a `.env` file for your project. Fill in the required values (see `.template.env` for reference).
- **Generate SkyPilot Config:**
  ```sh
  ./generate-sky-config.sh
  ```
  This renders `.sky.yaml` from the template. Edit `.sky.yaml` as needed for your environment.

### 2. Environment Preparation

- Fill in the required values in `.env` file.
- Ensure `.sky.yaml` is configured for your cluster and storage.

### 3. Create Shared Volume (Persistent Volume Claim)

If your Kubernetes cluster does not have a `ReadWriteMany` storage class, install it (see [Nebius docs](https://docs.nebius.com/kubernetes/storage/filesystem-over-csi)).

Create the volume:
```sh
sky volumes apply -y volume.sky.yaml
```

### 4. Launch vLLM Setup and Serve

Use the provided YAML (e.g., `qwen72b-inference-vllm.yaml`) to launch the setup and serving process:
```sh
sky launch -c serve -y qwen72b-inference-vllm.yaml
```
This will:
- Download model weights to the shared volume (PVC)
- Run preflight checks for GPU, CUDA, and Python
- Start the vLLM server with the specified configuration

**Note:** The first launch may take several minutes to download large model weights.

### 5. Test the Endpoint

Use the provided test script to validate the OpenAI-compatible endpoint:
```sh
./test-vllm.sh --port-forward --port <port> --pod <head-pod-name> -n <ns>
./test-vllm.sh --test all
```
**Note:** Port forwarding keeps running so for testing you will need to open up a second terminal.
This script supports health checks, completions, chat, and interactive modes. See the README for more usage examples.


### 6. Key vLLM CLI Flags

- `--tensor-parallel-size`: Number of GPUs per node to use for tensor parallelism. Enables distributed inference for large models.
- `--dtype`: Data type for model weights and computation (e.g., `bfloat16`, `float16`). Impacts memory usage and performance.
- `--download-dir`: Directory path where model weights and tokenizer files are cached/downloaded.
- `--trust-remote-code`: Allows loading custom model code from remote repositories. Required for some Hugging Face models.
- `--host`: IP address to bind the API server (typically `0.0.0.0` for all interfaces).
- `--port`: Port number for the API server to listen on.
- `--max-model-len`: Maximum total sequence length (in tokens) supported by the model (input + output tokens).
- `--max-num-seqs`: Maximum number of concurrent sequences (requests) the server can process in parallel.
- `--chat-template`: Path to a custom chat template file for chat-based models (required if not provided by the model).
- `--chat-template-content-format`: Format of the chat template content (e.g., `auto`, `jinja`).
- `--no-trust-request-chat-template`: Disables accepting chat templates from client requests for security.
- `-O.attention_backend`: Specifies the attention backend to use (e.g., `FLASH_ATTN`, `TORCH_SDPA`).
- `--enforce-eager`: Forces the server to run in eager mode (disables CUDA graphs for stability).
- `-O.cudagraph_mode=NONE`: Explicitly disables CUDA graph mode for debugging or compatibility.

All these are mapped as environment variables in the YAML and passed to the vLLM server at runtime.

---

## vLLM CLI Commands: Serve and Benchmarking

vLLM provides a suite of CLI tools for serving models and benchmarking inference performance. Understanding these commands is essential for both development and production deployments. Below are the most important commands, with usage examples and explanations (see [official vLLM CLI docs](https://docs.vllm.ai/en/stable/cli/index.html) for full details).

### `vllm serve`: Start the Inference Server

Launches the vLLM OpenAI-compatible API server for a specified model.

**Example:**
```sh
vllm serve Qwen/Qwen2.5-72B-Instruct \
  --tensor-parallel-size 8 \
  --dtype bfloat16 \
  --download-dir /model-weights/hf_home/hub \
  --host 0.0.0.0 \
  --port 8010 \
  --max-model-len 8192 \
  --max-num-seqs 256 \
  -O.attention_backend=FLASH_ATTN
```

---

## SkyPilot YAML Configuration

The `vllm serve` command is launched within the SkyPilot YAML using environment variables. Here’s how the arguments and configuration are mapped from the shell command to the YAML file(`qwen72b-inference-vllm.yaml`):

The YAML’s `run:` section assembles the variables and launches the command, ensuring all configuration is explicit and reproducible. This approach allows you to:
- Parameterize deployments for different models and hardware
- Enforce best practices (e.g., eager mode, safe attention backend)
- Cleanly separate secrets and runtime configuration

**Tip:** To change model, precision, or other settings, simply edit the corresponding environment variable in the YAML and relaunch.

---

## vLLM Benchmark `vllm bench`

Runs a suite of benchmarks to measure throughput and latency for a given model and configuration:

- `vllm bench latency`: Runs a latency benchmark by loading the model and measuring `per-request latency in seconds`. This command launches a new instance of the model for benchmarking and sends requests internally (no API server is started). It cannot be used if the model is already running in a separate server process.

**Example**
```sh
vllm bench latency \
    --model "$MODEL_ID" \
    --tensor-parallel-size "$NUM_SHARDS" \
    --dtype "$DTYPE" \
    --max-model-len "$VLLM_MAX_MODEL_LEN" \
    --max-num-seqs "$VLLM_MAX_NUM_SEQS" \
    --download-dir "$CACHE_DIR" \
    -O.attention_backend="$VLLM_ATTENTION_BACKEND"
```

- `vllm bench throughput`: Runs a throughput benchmark by loading the model and measuring maximum `tokens/sec`. Like the latency benchmark, this command starts a new model instance and sends requests internally (no API server is started). It cannot be used on a model that is already running.

**Example**
```sh
vllm bench throughput \
    --model "$MODEL_ID" \
    --tensor-parallel-size "$NUM_SHARDS" \
    --dtype "$DTYPE" \
    --max-model-len "$VLLM_MAX_MODEL_LEN" \
    --max-num-seqs "$VLLM_MAX_NUM_SEQS" \
    --input-len "$VLLM_INPUT_LEN" \
    --download-dir "$CACHE_DIR" \
    -O.attention_backend="$VLLM_ATTENTION_BACKEND"
```

- `vllm bench serve`: Benchmarks a model that is already running via `vllm serve` by connecting to its API endpoint. Use this command to measure the performance of a live, running server without reloading the model; requests are sent over HTTP to the existing API server. For accurate maximum performance metrics, ensure the vLLM server is idle (not serving other requests) during the benchmark.

**Example:**
To benchmark the vLLM server, first SSH into the head node of your SkyPilot cluster (replace `serve` with your cluster name if different):

```sh
ssh serve
```
Once connected, run the following command to start the benchmark:

```sh
vllm bench serve \
  --host 127.0.0.1 \
  --port 8010 \
  --model Qwen/Qwen2.5-72B-Instruct \
  --num-prompts 1000 \
  --random-input-len 4000\
  --random-output-len 4000\
  --max-concurrency 256
```

### See https://docs.vllm.ai/en/stable/cli/bench/serve.html for details.

**Flag Explanations:**
- `--num-prompts`: Total number of prompts (requests) to send during the benchmark. Higher values provide more stable and representative metrics.
- `--random-input-len`: Number of input tokens per synthetic prompt generated by the benchmark client; stresses long-context encoding and KV cache usage.
- `--random-output-len`: Target number of tokens to generate per request; acts as an upper bound (may end earlier due to EOS or server limits).
- `--max-concurrency`: Maximum concurrent in-flight requests from the benchmark client; tune to be ≤ server `--max-num-seqs` to avoid excessive queuing.

### Example benchmark results (serve)

Run context: 1000 prompts, `--random-input-len=4000`, `--random-output-len=4000`, `--max-concurrency=256`, model `Qwen/Qwen2.5-72B-Instruct`.

| Metric | Value | Meaning |
|:---|---:|:---|
| Successful requests | 1000 | Requests completed successfully (2xx) and included in metrics. |
| Maximum request concurrency | 256 | Configured cap on in-flight requests from the client. |
| Benchmark duration (s) | 728.33 | Total wall-clock time for the run. |
| Total input tokens | 3,994,587 | Sum of prompt tokens sent to the server. |
| Total generated tokens | 3,870,644 | Sum of tokens produced by the server. |
| Request throughput (req/s) | 1.37 | Average completed requests per second over the run. |
| Output token throughput (tok/s) | 5,314.42 | Average generated tokens per second (decoding throughput). |
| Peak output token throughput (tok/s) | 8,385.00 | Highest short-interval output token rate observed. |
| Peak concurrent requests | 264.00 | Highest observed in-flight requests during the run (may briefly exceed client cap due to scheduling/streaming). |
| Total token throughput (tok/s) | 10,799.02 | Input + output tokens per second; proxy for overall token processing rate. |
| Mean TTFT (ms) | 6,520.70 | Average time to first token; includes queueing and prefill work. |
| Median TTFT (ms) | 1,117.96 | Median time to first token (p50). |
| P99 TTFT (ms) | 43,002.75 | 99th percentile TTFT; tail-latency to first token. |
| Mean TPOT (ms) | 47.09 | Average time per output token after the first; decode-step latency. |
| Median TPOT (ms) | 46.73 | Median TPOT (p50). |
| P99 TPOT (ms) | 133.04 | 99th percentile TPOT; tail per-token latency. |
| Mean ITL (ms) | 44.62 | Average inter-token latency observed by the client. |
| Median ITL (ms) | 34.73 | Median ITL (p50). |
| P99 ITL (ms) | 373.22 | 99th percentile ITL; tail spacing between tokens. |


---

## Model Preparation and Configuration: Best Practices

Before deploying vLLM, ensure your model and environment are properly configured for optimal performance and stability. Use this checklist:

- **Model and revision:** Select the correct model weights and matching tokenizer files to ensure tokenization consistency.
- **GPU memory (VRAM):** Must be sufficient for model weights, context window, and concurrency.
- **CUDA version:** Host driver CUDA must be >= container build CUDA.
- **Dtype:** `bfloat16` recommended on H100/H200; `fp16` where appropriate.
- **Attention backend:** Verify kernel compatibility (SDPA, FlashAttention, Triton). For BLOOM/ALiBi, prefer Torch SDPA or Triton; avoid FA3.
- **Parallelism:** Set `--tensor-parallel-size` to GPUs per node.
- **Limits:** Tune `--max-model-len` and `--max-num-seqs` for memory and latency targets.
- **KV cache:** Plan memory footprint; consider KV quantization if supported.
- **Chat template:** If not provided by the model, supply via `--chat-template` (required for chat API).
- **Quantization:** Choose (GPTQ, AWQ, INT8, INT4, FP8) for memory savings. Must be set at server start; cannot change dynamically.
- **Eager vs graphs:** Start with eager mode (`--enforce-eager`); enable CUDA graphs (`-O.use_cudagraph=true`) after validation for performance.
- **Observability:** Enable metrics and set logging level as needed.

---

## Example: Using the vLLM OpenAI-Compatible API

Once deployed, vLLM exposes an OpenAI-compatible API for completions and chat. Here are example `curl` commands:

**Health check:**
```bash
curl -fsS http://127.0.0.1:8010/v1/models
```

**Completions (classic prompt):**
```bash
curl -s http://127.0.0.1:8010/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-72B-Instruct","prompt":"Write a short poem about the moon.","max_tokens":64}'
```

**Chat (role-structured messages):**
```bash
curl -s http://127.0.0.1:8010/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-72B-Instruct","messages":[{"role":"user","content":"Write a one-line haiku about GPUs."}],"max_tokens":64}'
```

---

## Operational Notes and Troubleshooting

**Common issue**: BLOOM + ALiBi with FlashAttention v3 (FA3)
- Symptom: first request crashes with `AssertionError: Alibi is not supported in FA3`
- Fix: force Torch SDPA (e.g.`-O.attention_backend=TORCH_SDPA`); keep eager mode if unstable
- Note: Some builds may still route to FA internally; verify backend in logs

**CUDA graphs stability**
- If warmup or capture crashes, disable graphs (enforce eager), align driver/toolkit to image, then re-enable progressively

**CUDA graphs and compile mode**
- Capturing CUDA graphs reduces launch overhead after warmup
- Some stacks are sensitive; eager mode can be used for the baseline
- You can disable graphs (e.g., enforce eager) and re-enable after validation

**Health checks**
- Probe `/v1/models`; only proceed when server is bound and healthy

**Performance tuning**
- Increase batch/concurrency for throughput; monitor latency and KV memory

**Security and production hygiene**
- Add TLS, authentication, rate limits; expose metrics; set resource limits; avoid exposing your model server to the public internet without authentication or access controls.
