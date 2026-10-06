#!/usr/bin/env bash
set -euo pipefail

if (($# < 2)); then
  printf 'Usage: %s INSTALLED_IMAGE_ID COMMAND [ARG ...]\n' "$0" >&2
  exit 2
fi
image=$1
shift
runner_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
course_dir="$(cd -- "$runner_dir/.." && pwd -P)"
image=$(python3.12 "$course_dir/tools/course_runtime.py" image --course-root "$course_dir" "$image")
command -v apptainer >/dev/null 2>&1 || {
  printf 'ERROR: adapt this reviewed example to the site container runtime.\n' >&2
  exit 2
}
runner_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
mount_file=$(mktemp)
trap 'rm -f -- "$mount_file"' EXIT
python3.12 "$runner_dir/../tools/managed_profilers.py" > "$mount_file"
runtime_root=${COURSE_RUNTIME_ROOT:?load the lab runtime}
[[ $runtime_root != *[:,]* ]] || { printf 'ERROR: unsupported bind path.\n' >&2; exit 2; }
bind_args=(--bind "$runtime_root:$runtime_root" --bind "$runtime_root/.runtime:$runtime_root/.runtime:ro")
[[ $course_dir != *[:,]* ]] || { printf 'ERROR: unsupported course bind path.\n' >&2; exit 2; }
bind_args+=(--bind "$course_dir:$course_dir")
if [[ -n ${HF_HUB_CACHE:-} ]]; then
  bind_args+=(--env "HF_HOME=$HF_HOME" --env "HF_HUB_CACHE=$HF_HUB_CACHE" --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1)
fi
if [[ -n ${COURSE_JOB_DIR:-} ]]; then
  bind_args+=(--env "HF_MODULES_CACHE=$COURSE_JOB_DIR/artifacts/hf-modules" --env "XDG_CACHE_HOME=$COURSE_JOB_DIR/artifacts/cache")
fi
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
exec env -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV apptainer exec --nv "${bind_args[@]}" "${image}" \
  bash -c 'set -e; source /etc/profile.d/99-nsight.sh; exec "$@"' bash "$@"
