#!/usr/bin/env bash
set -euo pipefail

if (($# < 2)); then
  printf 'Usage: %s OCI_IMAGE_DIGEST COMMAND [ARG ...]\n' "$0" >&2
  exit 2
fi
image=$1
shift
if [[ ! ${image} =~ ^(docker|oras)://.+@sha256:[0-9a-f]{64}$ ]]; then
  printf 'ERROR: use docker:// or oras:// with an exact sha256 digest.\n' >&2
  exit 2
fi
command -v apptainer >/dev/null 2>&1 || {
  printf 'ERROR: adapt this reviewed example to the site container runtime.\n' >&2
  exit 2
}
exec apptainer exec --nv "${image}" "$@"
