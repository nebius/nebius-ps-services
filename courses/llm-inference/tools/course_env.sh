#!/usr/bin/env bash
# Source inside a native job/diagnostic shell. This never installs software.
course_load_runtime() {
  local selection=$1 kind=${2:---launcher} exports
  if ! exports=$(python3.12 -B -E -s "$PWD/tools/course_runtime.py" shell --course-root "$PWD" "$kind" "$selection"); then
    return 1
  fi
  # The reader validates keys and quotes every value; it emits export/unset only.
  eval "$exports"
  : "${COURSE_PYTHON:?runtime record did not select a Python interpreter}"
}
course_load_runtime "$@" || { unset -f course_load_runtime; return 1; }
unset -f course_load_runtime
