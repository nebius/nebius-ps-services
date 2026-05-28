#!/usr/bin/env bash
#SBATCH --deadline="now+8hours"
#SBATCH --time=1:00:00
#SBATCH --exclusive
#SBATCH --mem=0

set -euo pipefail

printf 'Cleaning up job-scoped Enroot containers on node: %s\n' "$(hostname)"
# shellcheck disable=SC2016 # Expand containers inside the worker-side bash.
srun bash -c 'containers=$(enroot list | grep -E "^pyxis_([0-9]+([._].*)?|.*_[0-9]+([._].*)?)$" || true); if [[ -z "${containers}" ]]; then echo "No job-scoped Pyxis containers found"; exit 0; fi; printf "%s\n" "${containers}" | xargs -r -n1 -- sudo enroot remove --force'
printf 'Cleanup done.\n'
