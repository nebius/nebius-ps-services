#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 "${script_dir}/gcp_vpngw_classic_vm_ha.py" "$@"
