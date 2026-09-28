#!/usr/bin/env bash
# Build all registered course publications, then verify their bytes.
set -euo pipefail
show_help() {
  cat <<'USAGE'
Usage: build-courses.sh [-h | --help]
Build every registered course and companion archive, then verify freshness.
Requires Bash and python3, plus the configured renderer's documented dependencies.
Git is required only when the adapter selects a Git publication inventory.
Run using the path to this script from any working directory.
  -h, --help  Show help without building or requiring Python
  --          End options; no positional arguments are accepted
Unchanged inputs produce identical output bytes. This command does not install
packages, run learner programs, synchronize remote files or publish a website.
USAGE
}
if [[ $# -eq 1 && ( $1 == -h || $1 == --help ) ]]; then show_help; exit 0; fi
if [[ $# -eq 1 && $1 == -- ]]; then shift; fi
if [[ $# -ne 0 ]]; then show_help >&2; exit 2; fi
if ! command -v python3 >/dev/null 2>&1; then
  printf 'ERROR: Required command not found: python3\n' >&2; exit 1
fi
script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
builder="$script_dir/tools/build_course_html.py"
if [[ ! -r $builder ]]; then printf 'ERROR: Missing course builder\n' >&2; exit 1; fi
if python3 -B "$builder"; then :; else status=$?; exit "$status"; fi
printf '\nChecking...\n'
if python3 -B "$builder" --check; then :; else status=$?; exit "$status"; fi
printf 'All course publications rebuilt and verified.\n'
