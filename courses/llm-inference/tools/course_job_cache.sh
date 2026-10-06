#!/usr/bin/env bash
# Source only after a native job creates its private, non-reused job directory.
: "${COURSE_JOB_DIR:?a fresh native job directory is required}"
mkdir -m 700 -- "$COURSE_JOB_DIR/cache"
export XDG_CACHE_HOME="$COURSE_JOB_DIR/cache"
export XDG_CONFIG_HOME="$XDG_CACHE_HOME/config"
export HF_HOME="$XDG_CACHE_HOME/huggingface"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"
export HF_ASSETS_CACHE="$HF_HOME/assets" HF_XET_CACHE="$HF_HOME/xet"
export TRITON_CACHE_DIR="$XDG_CACHE_HOME/triton"
export VLLM_CACHE_ROOT="$XDG_CACHE_HOME/vllm"
export VLLM_CONFIG_ROOT="$XDG_CONFIG_HOME/vllm"
# vLLM appends a UUID; full job paths can exceed the Unix socket path limit.
# Its node-local sockets retain the job's umask; writable caches stay per-job.
export VLLM_RPC_BASE_PATH=/tmp
export FLASHINFER_WORKSPACE_BASE="$XDG_CACHE_HOME/flashinfer"
export TORCHINDUCTOR_CACHE_DIR="$XDG_CACHE_HOME/torchinductor"
export CUDA_CACHE_PATH="$XDG_CACHE_HOME/cuda"
export TMPDIR="$XDG_CACHE_HOME/tmp"
mkdir -m 700 -- "$TMPDIR"
