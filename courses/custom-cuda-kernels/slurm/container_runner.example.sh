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
runner_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
mount_file=$(mktemp)
trap 'rm -f -- "$mount_file"' EXIT
python3 "$runner_dir/../tools/managed_profilers.py" > "$mount_file"
bind_args=()
while IFS= read -r -d '' mount; do
  bind_args+=(--bind "$mount")
done < "$mount_file"
rm -f -- "$mount_file"
trap - EXIT
if [[ -n ${COURSE_TOOLS:-} ]]; then
  [[ -d ${COURSE_TOOLS} && ${COURSE_TOOLS} != *[:,]* ]] || {
    printf 'ERROR: COURSE_TOOLS must be an existing directory without bind separators.\n' >&2
    exit 2
  }
  bind_args+=(--bind "${COURSE_TOOLS}:${COURSE_TOOLS}:ro")
fi
# Activate inside the container after mounting the complete package resources.
exec apptainer exec --nv "${bind_args[@]}" "${image}" \
  bash -c 'set -e; source /etc/profile.d/99-nsight.sh; exec "$@"' bash "$@"
