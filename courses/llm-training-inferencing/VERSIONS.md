# Pinned dependency window

Statically reviewed on 2026-09-01 against current official documentation.

Pinned training environment: Python 3.12, PyTorch 2.13.0, Transformers 5.16.1, PEFT 0.20.0, TRL 1.12.0, Datasets 5.0.1, Accelerate 1.14.0, and pytest 9.1.1.

Optional FP8 Lab 22 uses the Transformer Engine 2.18 API shape documented for
`te.autocast` and `DelayedScaling`. Transformer Engine is deliberately not in
the base training requirements because its wheel/build must match the site's
PyTorch, CUDA, driver, and H100 environment. The cluster owner must provide an
approved compatible build and record its resolved version; the lab exits with
a clear blocker when it is absent.

Pinned serving environment: Python 3.12, vLLM 0.28.0, and pytest 9.1.1 in a fresh environment. The vLLM binary wheel includes tightly coupled PyTorch/CUDA components; do not merge it into the training environment merely because package resolution succeeds.

Public practice model: `Qwen/Qwen2.5-0.5B-Instruct` at immutable revision `7ae557604adf67be50417f59c2c2f167def9a775`. Labs pass the same revision to model and tokenizer loading. Review and update the revision deliberately; do not silently follow the repository's moving default branch.

All labs target a full NVIDIA H100 (SM90). H100 supports FP8 Tensor Core execution; NVIDIA's NVFP4 path is a Blackwell capability and is not presented as an H100 feature. Cluster driver, CUDA, NCCL, Slurm, and network-plugin versions must be recorded at runtime.
