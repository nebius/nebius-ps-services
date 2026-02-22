# vLLM Inference with SkyPilot

This repo provides a reproducible setup for exploring vLLM inference on
Kubernetes using SkyPilot. It supports any Hugging Face-compatible model and
includes:

- Unified YAML for setup, model download, and serving (see example YAMLs)
- Portable test script for endpoint validation (`test-vllm.sh`)

## Prerequisites

Before using this project, ensure you have the following prerequisites set up:

1. **Install SkyPilot**
    - If SkyPilot is not installed, run the provided installation script:

       ```sh
       ./skypilot-install.sh
       ```

    - Activate the virtual environment before running `sky` commands:

      ```sh
      source ~/venvs/skypilot/bin/activate
      ```

    - Optional checks:

      ```sh
      sky check nebius
      sky check kubernetes
      ```

2. **Set Up Credentials**
    - Prepare `.env` (the script creates it from `.template.env` if missing),
      then replace placeholder values.
    - Ensure required CLIs are available and authenticated: `nebius` (logged
      in), `jq`, and `aws`.
    - Run the service account setup script:

       ```sh
       ./nebius-sa-setup.sh
       ```

    - This script creates the Nebius service account credentials and configures
      global AWS profiles for Nebius Object Storage.
3. **Generate sky.yaml**
    - Generate Sky config: `./generate-sky-config.sh` (it renders `.sky.yaml`
      from `template.sky.yaml`).
    - Edit `template.sky.yaml` and re-run the script when you need to change
      Sky config values.

You must complete these steps before proceeding with further setup or deployment.

## Quickstart

1. Prepare environment:
   - Ensure `.env` has real values (not placeholders). If using direnv, run
     `direnv allow`.
   - Make sure `.sky.yaml` is regenerated after any `template.sky.yaml` changes.
2. Create shared volume (PVC):
   - If your K8s cluster lacks the `ReadWriteMany` storage class, install it
     using the official helm chart (skip PVC/PV creation):
     <https://docs.nebius.com/kubernetes/storage/filesystem-over-csi>
   - Create the volume: `sky volumes apply -y volume.sky.yaml`
3. Launch setup + serve:
   - `sky launch -c serve -y <your-model-inference-vllm.yaml>`
   - First it downloads model weights to PVC; this may take several minutes to
     half an hour depending on model size.
4. Test endpoint:
   - Run `./test-vllm.sh` (see below for usage).

---

## Environment Variables for Inference Configuration

See the example YAMLs for required environment variables and runtime flags. Key
settings include:

- Model repo (e.g., `bigscience/bloom`, `Qwen/Qwen-72B`)
- Attention backend (e.g., `TRITON_ATTN`, `TORCH_SDPA`) for compatibility
- Context length, concurrency, and chat template settings
- Debug and runtime flags for stability

Secrets:

- `HF_TOKEN` (optional, via `secrets:`): Only needed for private models

---

## Testing with test-vllm.sh

Use `test-vllm.sh` to validate the OpenAI-compatible endpoint. It supports
health, completions, and chat tests with a simplified interface.

**Key modes:**

- Port-forward only: `./test-vllm.sh --port-forward --pod <pod> -n <ns> -p <port>`
  (keeps open until Ctrl+C)
- Port-forward and test: `./test-vllm.sh --port-forward --pod <pod> --test all`
  (port-forward stops automatically after tests)
- Interactive chat: `./test-vllm.sh --chat` (prompts with ">")
- Interactive completions: `./test-vllm.sh --prompt` (prompts with ">")
- Default tests: `./test-vllm.sh --test [health|chat|prompt|all]`
  (no value => health test)

**Examples:**

- Health check: `./test-vllm.sh --test` (defaults to health test)
- Chat (default message): `./test-vllm.sh --test chat`
- Completions (default prompt): `./test-vllm.sh --test prompt`

---

## Deployment Notes

- PVC is used for model cache; only rank 0 downloads weights
- Set `REDOWNLOAD=true` to force fresh download from scratch
- All secrets and runtime files are git-ignored; and only templates are committed
- Highly recommended: K8s node CUDA driver version should be greater than or
  equal to container CUDA runtime version.

---

## Benchmark the vLLM inference server

`vllm bench serve`: Benchmarks a model that is already running via
`vllm serve` by connecting to its API endpoint. Use this command to measure the
performance of a live, running server without reloading the model; requests are
sent over HTTP to the existing API server. For accurate maximum performance
metrics, ensure the vLLM server is idle (not serving other requests) during the
benchmark.

**Example:**
To benchmark the vLLM server, first SSH into the head node of your SkyPilot
cluster (replace `serve` with your cluster name if different):

```sh
sky ssh serve
```

Once connected, run the following command to start the benchmark:

```sh
vllm bench serve \
  --host 127.0.0.1 \
  --port 8010 \
  --model Qwen/Qwen2.5-72B-Instruct \
  --num-prompts 1000 \
  --random-input-len 4000 \
  --random-output-len 4000 \
  --max-concurrency 256
```

### See <https://docs.vllm.ai/en/stable/cli/bench/serve.html> for details

**Flag Explanations:**

- `--num-prompts`: Total number of prompts (requests) to send during the
  benchmark. Higher values provide more stable and representative metrics.
- `--random-input-len`: Number of input tokens per synthetic prompt generated
  by the benchmark client; stresses long-context encoding and KV cache usage.
- `--random-output-len`: Target number of tokens to generate per request; acts
  as an upper bound (may end earlier due to EOS or server limits).
- `--max-concurrency`: Maximum concurrent in-flight requests from the benchmark
  client; tune to be ≤ server `--max-num-seqs` to avoid excessive queuing.
