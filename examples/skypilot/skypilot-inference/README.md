# vLLM Inference with SkyPilot

This repo provides a reproducible setup for exploring vLLM inference on Kubernetes using SkyPilot. It supports any Hugging Face-compatible model and includes:
- Unified YAML for setup, model download, and serving (see example YAMLs)
- Portable test script for endpoint validation (`test-vllm.sh`)

## Prerequisite
Before using this project, ensure you have the following prerequisites set up:

1. **Install SkyPilot**
    - If SkyPilot is not installed, run the provided installation script:
       ```sh
       ./skypilot-install.sh
       ```

2. **Set Up Credentials**
    - To configure credentials for SkyPilot, run the service account setup script:
       ```sh
       ./nebiaus-sa-setup.sh
       ```
    - This script will create `.env` file in your project directory, ready to be configured for your specific project.
3. **Generate sky.yaml**  
    - Generate Sky config: `./generate-sky-config.sh` (It renders `.sky.yaml`), ater the .sky.yaml is generated, go ahead and configure your SkyPilot config for this specific project. 

You must complete these steps before proceeding with further setup or deployment.

## Quickstart
1. Prepare environment:
   - If you don't see the .env file (please note that it's a hidden file), then manually copy `.template.env` to `.env` and fill values. If using direnv, run `direnv allow`. 
   - Make sure the `.sky.yaml` file has been configured properly.
2. Create shared volume (PVC):
   - If your K8s cluster lacks the `ReadWriteMany` storage class, install it using the official helm chart (skip PVC/PV creation):
     https://docs.nebius.com/kubernetes/storage/filesystem-over-csi
   - Create the volume: `sky volumes apply -y volume.sky.yaml`
3. Launch setup + serve:
   - `sky launch -c serve -y <your-model-inference-vllm.yaml>`
   - First it will download the model weights to PVC; It takes several minutes to half an hour depends on the size of the model weights.
4. Test endpoint:
   - Run `./test-vllm.sh` (see below for usage).
---

## Environment Variables for Inferencing Configurations
See the example YAMLs for required environment variables and runtime flags. Key settings include:
- Model repo (e.g., `bigscience/bloom`, `Qwen/Qwen-72B`)
- Attention backend (e.g., `TRITON_ATTN`, `TORCH_SDPA`) for compatibility
- Context length, concurrency, and chat template settings
- Debug and runtime flags for stability

Secrets:
- `HF_TOKEN` (optional, via `secrets:`): Only needed for private models

---

## Testing with test-vllm.sh
Use `test-vllm.sh` to validate the OpenAI-compatible endpoint. It supports health, completions, and chat tests with a simplified interface.

**Key modes:**
- Port-forward only: `./test-vllm.sh --port-forward --port <port> --pod <head-pod-name> -n <ns>` (keeps open until Ctrl+C)
- Port-forward and test: `./test-vllm.sh --port-forward --port <port> --pod <head-pod-name> -n <ns> --test all` (portforward will be deleted)
- Interactive chat: `./test-vllm.sh --chat` (prompts with ">")
- Interactive completions: `./test-vllm.sh --prompt` (prompts with ">")
- Default tests: `./test-vllm.sh --test [health|chat|prompt|all]` (no value => health test)

**Examples:**
- Health check: `./test-vllm.sh --test` (defaults to health test)
- Chat (default message): `./test-vllm.sh --test chat`
- Completions (default prompt): `./test-vllm.sh --test prompt` 

---

## Deployment Notes
- PVC is used for model cache; only rank 0 downloads weights
- Set `REDOWNLOAD=true` to force fresh download from scrach
- All secrets and runtime files are git-ignored; and only templates are committed
- Highly recomended the K8s node CUDA driver version is greater/equal than container CUDA version (Cuda runtime)
