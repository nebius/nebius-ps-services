#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
helper="${script_dir}/gcp_vpngw_vm_ha.py"

if [[ ! -r "${helper}" ]]; then
  printf 'ERROR: VM-HA GCP helper is missing: %s\n' "${helper}" >&2
  exit 1
fi

exec python3 "${helper}" "$@"
