#!/usr/bin/env bash
set -euo pipefail
usage() {
    printf 'Usage: %s [--resume] CONFIG [SBATCH_OPTIONS...]\n' "${0##*/}"
    printf 'Example: %s config.yaml --nodes=1 --gpus-per-node=2 --time=00:10:00\n' "${0##*/}"
}
if [[ ${1:-} == --help || ${1:-} == -h ]]; then usage; exit 0; fi
resume=()
if [[ ${1:-} == --resume ]]; then resume=(--resume); shift; fi
if [[ $# -lt 1 || $1 == -* ]]; then usage >&2; exit 2; fi
config=$1
shift
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
python="$project_dir/.venv/bin/python"
[[ -x "$python" ]] || { printf 'ERROR: Run uv sync --locked --extra ml first.\n' >&2; exit 2; }
command -v sbatch >/dev/null || { printf 'ERROR: sbatch is unavailable.\n' >&2; exit 2; }
config=$("$python" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=True))' "$config")
"$python" -m nebius_train validate --config "$config"
logs=${SLURM_LOGS_DIR:-"$project_dir/logs"}
mkdir -p -- "$logs"
logs=$(cd -- "$logs" && pwd -P)
# Slurm can copy the job script to its spool, so pass the actual project path.
exec sbatch --parsable --export=ALL --chdir="$project_dir" \
    --output="$logs/%x-%j.out" --error="$logs/%x-%j.err" \
    "$@" "$project_dir/train.slurm" "$project_dir" "$config" "${resume[@]}"
