#!/usr/bin/env bash

set -euo pipefail

printf '[%s] Cleanup leftover Enroot containers for this job\n' "$(date)"

if [[ -z "${SLURM_JOB_ID:-}" ]]; then
    printf 'Slurm job ID is not known\n' >&2
    exit 0
fi

containers="$(
    enroot list \
        | grep -E "(^pyxis_${SLURM_JOB_ID}([._]|$)|^pyxis_.+_${SLURM_JOB_ID}([._]|$))" \
        || true
)"

if [[ -z "${containers}" ]]; then
    printf 'No job-scoped Pyxis containers found for Slurm job %s\n' "${SLURM_JOB_ID}"
    exit 0
fi

while IFS= read -r container; do
    [[ -z "${container}" ]] && continue
    enroot remove -f "${container}" || true
done <<< "${containers}"
