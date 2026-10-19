#!/usr/bin/env bash
# Source inside a native job/diagnostic shell. This never installs software.
course_load_runtime() {
  local selection=$1 kind=${2:---launcher} exports
  local profiler_profile=/etc/profile.d/99-nsight.sh
  case ${COURSE_PROFILE_TOOL:-none} in
    nsys|ncu)
      if [[ ! -f $profiler_profile || -L $profiler_profile || ! -r $profiler_profile ]]; then
        printf '%s\n' 'ERROR: Managed profiler environment is unavailable; complete the shared profiling installation.' >&2
        return 1
      fi
      # Batch stages do not read login profiles. Activate the installer-owned tools
      # before the runtime reader adds the prepared Python and native toolchain.
      # shellcheck disable=SC1090
      source "$profiler_profile" || {
        printf '%s\n' 'ERROR: Managed profiler environment activation failed.' >&2
        return 1
      }
      ;;
  esac
  if ! exports=$(python3.12 -B -E -s "$PWD/tools/course_runtime.py" shell --course-root "$PWD" "$kind" "$selection"); then
    return 1
  fi
  # The reader validates keys and quotes every value; it emits export/unset only.
  eval "$exports"
  : "${COURSE_PYTHON:?runtime record did not select a Python interpreter}"
}
course_load_runtime "$@" || { unset -f course_load_runtime; return 1; }
unset -f course_load_runtime
