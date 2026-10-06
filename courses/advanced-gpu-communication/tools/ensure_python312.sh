#!/usr/bin/env bash
# Normal sync/SSH handoff only. The main installer owns the remaining packages.
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf 'Usage: %s\nEnsure Python 3.12 and venv support in the Ubuntu 24.04 shared jail.\n' "$0"
  exit 0
fi
[[ $# == 0 ]] || { printf 'ERROR: no arguments are accepted.\n' >&2; exit 2; }
if command -v python3.12 >/dev/null && python3.12 -c 'import sys, venv, ensurepip; assert sys.version_info[:2] == (3, 12)' 2>/dev/null; then
  exit 0
fi
# shellcheck source=/dev/null
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || {
  printf 'ERROR: automatic Python bootstrap requires Ubuntu 24.04.\n' >&2; exit 1;
}
privilege=()
if [[ $(id -u) != 0 ]]; then privilege=(sudo -n); fi
packages=()
for package in python3.12 python3.12-venv; do
  if [[ $(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true) != 'install ok installed' ]]; then
    packages+=("$package")
  fi
done
[[ ${#packages[@]} -gt 0 ]] || {
  printf 'ERROR: installed Python 3.12 is incomplete; repair the interpreter before setup.\n' >&2; exit 1;
}
printf 'Preparing Python 3.12 for the course setup command.\n'
"${privilege[@]}" apt-get -o DPkg::Lock::Timeout=120 update
proposal=$("${privilege[@]}" apt-get -o DPkg::Lock::Timeout=120 --simulate install --no-install-recommends "${packages[@]}")
if ! awk '
  $1 == "Remv" { exit 1 }
  $1 == "Inst" && $2 ~ /^(nvidia-|libnvidia-|cuda-|nsight-|slurm|soperator|grafana|kube|linux-(image|modules)|libnccl)/ { exit 1 }
' <<< "$proposal"; then
  printf 'ERROR: Python installation would change protected platform packages.\n' >&2
  exit 1
fi
"${privilege[@]}" env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends "${packages[@]}"
python3.12 -c 'import sys, venv, ensurepip; assert sys.version_info[:2] == (3, 12)'
