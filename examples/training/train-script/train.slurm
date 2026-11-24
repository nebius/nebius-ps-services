#!/usr/bin/env bash
#SBATCH --job-name=nebius-train
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:30:00
set -euo pipefail
[[ $# -ge 2 ]] || { printf 'ERROR: Use submit.sh CONFIG [SBATCH_OPTIONS...]\n' >&2; exit 2; }
project_dir=$1
config=$2
shift 2
: "${SLURM_JOB_NUM_NODES:?Missing Slurm allocation}"
: "${SLURM_GPUS_ON_NODE:?GPU allocation required}"
: "${SLURM_JOB_ID:?Missing Slurm job ID}"
: "${SLURM_NODELIST:?Missing Slurm nodes}"
[[ $SLURM_GPUS_ON_NODE =~ ^[1-9][0-9]*$ ]] || { printf 'ERROR: GPU count must be numeric.\n' >&2; exit 2; }
python="$project_dir/.venv/bin/python"
[[ -x "$python" ]] || { printf 'ERROR: Shared project environment missing.\n' >&2; exit 2; }
export OMP_NUM_THREADS=$(( ${SLURM_CPUS_PER_TASK:-4} / SLURM_GPUS_ON_NODE ))
(( OMP_NUM_THREADS > 0 )) || export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=$OMP_NUM_THREADS OPENBLAS_NUM_THREADS=$OMP_NUM_THREADS
export TOKENIZERS_PARALLELISM=false PYTHONFAULTHANDLER=1
export HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1
export TORCH_NCCL_ASYNC_ERROR_HANDLING=1
hosts=$(scontrol show hostnames "$SLURM_NODELIST")
rdzv_host=${hosts%%$'\n'*}
# Unique allocation-derived port; override when site policy allocates a port.
port=${MASTER_PORT:-$((20000 + SLURM_JOB_ID % 30000))}
[[ $port =~ ^[0-9]+$ ]] && (( port > 1023 && port < 65536 )) || exit 2
exec srun --kill-on-bad-exit=1 --ntasks-per-node=1 --cpu-bind=cores \
    "$python" -m torch.distributed.run \
    --nnodes="$SLURM_JOB_NUM_NODES" --nproc-per-node="$SLURM_GPUS_ON_NODE" \
    --rdzv-id="$SLURM_JOB_ID" --rdzv-backend=c10d --rdzv-endpoint="$rdzv_host:$port" \
    --max-restarts=0 --module nebius_train train --config "$config" "$@"
