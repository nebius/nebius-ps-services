#!/usr/bin/env bash
# Rebuild course pages and results ZIPs, then verify source parity.
set -euo pipefail

error_style='' error_reset='' success_style='' success_reset='' checking_style=''
if [[ ${TERM:-} != dumb && ! ${NO_COLOR+x} ]]; then
  if [[ -t 2 ]]; then error_style=$'\033[1;31m' error_reset=$'\033[0m'; fi
  if [[ -t 1 ]]; then
    success_style=$'\033[32m' success_reset=$'\033[0m' checking_style=$'\033[1;36m'
  fi
fi

log_error() { printf '%sERROR:%s %s\n' "$error_style" "$error_reset" "$*" >&2; }

show_usage() {
  cat <<'USAGE'
Usage: build-courses.sh [-h | --help]

Rebuild the shared lab guide, course catalog, all registered course pages and
one combined dashboards-and-results ZIP per practical course, then check HTML
and ZIPs against their sources. Requires python3 and Git for rebuilding and publication-size checks.

Options:
  -h, --help  Show this help without building or requiring Python
  --          End options (no positional arguments are accepted)

Examples:
  ./build-courses.sh          From the courses directory
  ./courses/build-courses.sh  From the repository root

Run from any working directory using the path to this script. After editing
course sources, rebuild once and refresh your browser. Repeated runs produce
identical HTML and ZIP content when inputs are unchanged; timestamps may change.
Lab scripts are supplied by sync-labs.sh; lab-kit ZIPs are not generated.
Rebuilding does not synchronize wording between source documents or publish
the website. Full course validation is a separate maintainer step.
Terminal output separates checking in cyan, current outputs in green and failed
checks in red. Redirected output, TERM=dumb and any NO_COLOR use plain text.
USAGE
}

if [[ $# -eq 1 && ( $1 == -h || $1 == --help ) ]]; then
  show_usage
  exit 0
fi
if [[ $# -eq 1 && $1 == -- ]]; then shift; fi
if [[ $# -ne 0 ]]; then
  log_error "Unexpected argument: $1"
  show_usage >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  log_error 'Required command not found: python3'
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  log_error 'Required command not found: git'
  exit 1
fi
script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
builder="$script_dir/tools/build_course_html.py"
if [[ ! -f $builder || ! -r $builder ]]; then
  log_error "Course builder is missing or unreadable: $builder"
  exit 1
fi

if python3 -B "$builder"; then
  :
else
  status=$?
  log_error 'Build failed. Earlier outputs may have been refreshed; fix the reported error and rerun.'
  exit "$status"
fi
printf '\n%sChecking...%s\n' "$checking_style" "$success_reset"
if python3 -B "$builder" --check; then
  :
else
  status=$?
  log_error 'Generated HTML/ZIP verification failed; fix the reported error and rerun.'
  exit "$status"
fi
printf '%sAll course pages and results ZIPs rebuilt and verified. Refresh your browser.%s\n' "$success_style" "$success_reset"
