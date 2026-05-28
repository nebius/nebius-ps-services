#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel)"
cd "${REPO_ROOT}"

TAG_PREFIX="soperator-chart"
MAIN_BRANCH="main"
CHART_DIR="helm-charts/soperator"
CHART_NAME="soperator"
CHART_FILE="${CHART_DIR}/Chart.yaml"
CHANGELOG_FILE="${CHART_DIR}/CHANGELOG.md"

S_RESET=""
S_BOLD=""
S_DIM=""
S_RED=""
S_YELLOW=""
S_GREEN=""
S_CYAN=""

init_output_style() {
  if [[ ( -t 1 || -t 2 ) && "${TERM:-}" != "dumb" && -z "${NO_COLOR:-}" ]]; then
    S_RESET=$'\033[0m'
    S_BOLD=$'\033[1m'
    S_DIM=$'\033[2m'
    S_RED=$'\033[31m'
    S_YELLOW=$'\033[33m'
    S_GREEN=$'\033[32m'
    S_CYAN=$'\033[36m'
  fi
}

log_error() {
  printf '%b\n' "${S_RED}${S_BOLD}ERROR:${S_RESET}${S_RED} ${1}${S_RESET}" >&2
}

log_note() {
  printf '%b\n' "${S_DIM}${1}${S_RESET}"
}

log_success() {
  printf '%b\n' "${S_GREEN}${1}${S_RESET}"
}

show_usage() {
  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./publish-helm.sh${S_RESET} ${S_DIM}--prep X.Y.Z [--no-push]${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./publish-helm.sh${S_RESET} ${S_DIM}--publish X.Y.Z [--allow-non-main]${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Modes:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}--prep${S_RESET}     Update ${CHANGELOG_FILE} and ${CHART_FILE}, validate the chart, commit the release prep, and push the current branch."
  printf '%b\n' "                 First push auto-sets origin/<current-branch> as upstream when needed."
  printf '%b\n' "                 Clean-worktree check is strict and includes untracked files."
  printf '%b\n' "                 Fails if tag ${TAG_PREFIX}-vX.Y.Z already exists locally or on origin."
  printf '%b\n' "  ${S_YELLOW}--publish${S_RESET}  Create and push tag ${TAG_PREFIX}-vX.Y.Z."
  printf '%b\n' "                 Fails if ${CHART_FILE} does not already declare version X.Y.Z."
  printf '%b\n' "                 Tag push triggers .github/workflows/helm-chart-publish.yml."
  printf '\n'

  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}--no-push${S_RESET}          For --prep only: do not push the branch."
  printf '%b\n' "  ${S_YELLOW}--allow-non-main${S_RESET}   Allow --publish from a non-${MAIN_BRANCH} branch when HEAD is already in origin/${MAIN_BRANCH} history."
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}         Show help."
  printf '\n'

  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./publish-helm.sh --prep 0.1.0${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./publish-helm.sh --publish 0.1.0${S_RESET}"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "Required command not found: ${cmd}"
    exit 1
  fi
}

ensure_repo_ready() {
  require_cmd "git"
  require_cmd "helm"
  require_cmd "python3"
  for required_file in "${CHART_FILE}" "${CHANGELOG_FILE}"; do
    if [[ ! -f "${required_file}" ]]; then
      log_error "Missing required file ${required_file}"
      exit 1
    fi
  done
}

normalize_tag() {
  local raw="$1"
  if [[ "${raw}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf '%s\n' "${TAG_PREFIX}-v${raw}"
    return 0
  fi
  if [[ "${raw}" =~ ^${TAG_PREFIX}-v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf '%s\n' "${raw}"
    return 0
  fi
  log_error "Version/tag must be X.Y.Z or ${TAG_PREFIX}-vX.Y.Z"
  exit 1
}

ensure_clean_worktree() {
  local status_output=""
  status_output="$(git status --short --untracked-files=all)"
  if [[ -n "${status_output}" ]]; then
    log_error "Working tree is not clean."
    printf '%s\n' "${status_output}" >&2
    exit 1
  fi
}

ensure_named_branch() {
  local branch="$1"
  if [[ -z "${branch}" || "${branch}" == "HEAD" ]]; then
    log_error "This operation must run from a local branch, not a detached HEAD."
    exit 1
  fi
}

branch_has_upstream() {
  local branch="$1"
  git rev-parse --abbrev-ref --symbolic-full-name "${branch}@{upstream}" >/dev/null 2>&1
}

push_current_branch() {
  local branch="$1"
  ensure_named_branch "${branch}"

  if branch_has_upstream "${branch}"; then
    git push
    return 0
  fi

  log_note "No upstream configured for ${branch}; pushing to origin and setting upstream..."
  git push --set-upstream origin "${branch}"
}

ensure_branch_synced() {
  local branch="$1"
  git fetch origin
  local local_commit=""
  local remote_commit=""
  local_commit="$(git rev-parse HEAD)"
  if ! remote_commit="$(git rev-parse "origin/${branch}" 2>/dev/null)"; then
    log_error "Remote branch origin/${branch} not found."
    exit 1
  fi
  if [[ "${local_commit}" != "${remote_commit}" ]]; then
    log_error "Local ${branch} is not at origin/${branch}."
    log_note "local : ${local_commit}"
    log_note "origin: ${remote_commit}"
    exit 1
  fi
}

ensure_head_in_branch_history() {
  local branch="$1"
  git fetch origin
  if ! git rev-parse "origin/${branch}" >/dev/null 2>&1; then
    log_error "Remote branch origin/${branch} not found."
    exit 1
  fi
  if ! git merge-base --is-ancestor HEAD "origin/${branch}"; then
    log_error "Current HEAD is not in origin/${branch} history."
    log_note "The tag-triggered publish workflow will reject tags outside origin/${branch} history."
    exit 1
  fi
}

ensure_tag_absent() {
  local tag="$1"
  if git rev-parse --verify --quiet "${tag}" >/dev/null 2>&1; then
    log_error "Local tag already exists: ${tag}"
    exit 1
  fi
  if git ls-remote --exit-code --tags origin "refs/tags/${tag}" >/dev/null 2>&1; then
    log_error "Remote tag already exists on origin: ${tag}"
    exit 1
  fi
}

ensure_release_section_ready() {
  local tag="$1"

  python3 - "${tag}" "${CHANGELOG_FILE}" <<'PY'
import pathlib
import re
import sys

tag, changelog_path = sys.argv[1], sys.argv[2]
version = tag.rsplit("-v", 1)[-1]
lines = pathlib.Path(changelog_path).read_text(encoding="utf-8").splitlines()
patterns = [
    re.compile(rf"^##\s+\[?{re.escape(tag)}\]?(?:\s+-.*)?$"),
    re.compile(rf"^##\s+\[?{re.escape(version)}\]?(?:\s+-.*)?$"),
]

release_idx = None
for idx, line in enumerate(lines):
    if any(pattern.match(line.strip()) for pattern in patterns):
        release_idx = idx
        break

if release_idx is None:
    print(f"Missing changelog section for {tag} in {changelog_path}", file=sys.stderr)
    sys.exit(1)

content_lines = []
for line in lines[release_idx + 1 :]:
    if line.startswith("## "):
        break
    content_lines.append(line)

if not "\n".join(content_lines).strip():
    print(f"Release changelog section for {tag} in {changelog_path} is empty", file=sys.stderr)
    sys.exit(1)
PY
}

update_changelog() {
  local tag="$1"
  local release_date=""
  release_date="$(date +%Y-%m-%d)"

  python3 - "${tag}" "${release_date}" "${CHANGELOG_FILE}" <<'PY'
import pathlib
import re
import sys

tag, date_str, changelog_path = sys.argv[1], sys.argv[2], sys.argv[3]
path = pathlib.Path(changelog_path)
text = path.read_text(encoding="utf-8")

if "## [Unreleased]" not in text:
    print("Unreleased section not found in CHANGELOG.md", file=sys.stderr)
    sys.exit(1)

release_header = f"## [{tag}] - {date_str}"

header_re = re.compile(r"^## \[.+?\].*$", re.MULTILINE)
headers = list(header_re.finditer(text))
if not headers:
    print("No CHANGELOG headers found", file=sys.stderr)
    sys.exit(1)

preamble = text[: headers[0].start()]
sections = []
for i, h in enumerate(headers):
    end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
    header = h.group(0)
    content = text[h.end() : end]
    sections.append((header, content))

def normalize_content(content: str) -> str:
    stripped = content.strip("\n")
    if not stripped.strip():
        return "\n\n"
    return "\n\n" + stripped + "\n\n"

def merge_content(new_part: str, existing: str) -> str:
    parts = []
    if new_part.strip():
        parts.append(new_part.strip("\n"))
    if existing.strip():
        parts.append(existing.strip("\n"))
    if not parts:
        return "\n\n"
    return "\n\n" + "\n\n".join(parts) + "\n\n"

unreleased_idx = None
tag_idx = None
for idx, (header, _) in enumerate(sections):
    if header.strip() == "## [Unreleased]":
        unreleased_idx = idx
    if header.startswith(f"## [{tag}] -"):
        tag_idx = idx

if unreleased_idx is None:
    print("Unable to locate Unreleased heading", file=sys.stderr)
    sys.exit(1)

unreleased_header, unreleased_content = sections[unreleased_idx]
unreleased_payload = unreleased_content.strip("\n")

if tag_idx is None:
    new_sections = []
    for idx, (header, content) in enumerate(sections):
        if idx == unreleased_idx:
            new_sections.append((header, "\n\n"))
            new_sections.append((release_header, normalize_content(unreleased_payload)))
        else:
            new_sections.append((header, content))
    sections = new_sections
else:
    if unreleased_payload.strip():
        tag_header, tag_content = sections[tag_idx]
        sections[tag_idx] = (tag_header, merge_content(unreleased_payload, tag_content))
    sections[unreleased_idx] = (unreleased_header, "\n\n")

new_text = preamble + "".join(f"{h}{c}" for h, c in sections)
path.write_text(new_text, encoding="utf-8")
PY
}

update_chart_version() {
  local version="$1"

  python3 - "${version}" "${CHART_FILE}" <<'PY'
import pathlib
import re
import sys

target_version, chart_path = sys.argv[1], sys.argv[2]
path = pathlib.Path(chart_path)
text = path.read_text(encoding="utf-8")

match = re.search(r"(?m)^version:\s*(.+?)\s*$", text)
if match is None:
    print(f"Unable to locate chart version in {chart_path}", file=sys.stderr)
    sys.exit(1)

current_version = match.group(1).strip().strip("'\"")
if current_version == target_version:
    sys.exit(0)

updated = re.sub(
    r"(?m)^version:\s*(.+?)\s*$",
    f"version: {target_version}",
    text,
    count=1,
)
path.write_text(updated, encoding="utf-8")
PY
}

get_chart_version() {
  python3 - "${CHART_FILE}" <<'PY'
import pathlib
import re
import sys

chart_path = sys.argv[1]
text = pathlib.Path(chart_path).read_text(encoding="utf-8")
match = re.search(r"(?m)^version:\s*(.+?)\s*$", text)
if match is None:
    raise SystemExit(f"Unable to locate chart version in {chart_path}")
print(match.group(1).strip().strip("'\""), end="")
PY
}

ensure_chart_version_matches_tag() {
  local tag="$1"
  local expected_version="${tag##*-v}"
  local chart_version=""
  chart_version="$(get_chart_version)"

  if [[ "${chart_version}" != "${expected_version}" ]]; then
    log_error "${CHART_FILE} version ${chart_version} does not match release ${expected_version}."
    exit 1
  fi
}

validate_chart() {
  if [[ -f "${CHART_DIR}/Chart.lock" ]]; then
    log_note "Building locked Helm dependencies for ${CHART_DIR}..."
    helm dependency build "${CHART_DIR}"
  else
    log_note "Updating Helm dependencies for ${CHART_DIR}..."
    helm dependency update "${CHART_DIR}"
  fi
  log_note "Running helm lint --strict --with-subcharts for ${CHART_DIR}..."
  helm lint --strict --with-subcharts "${CHART_DIR}"
  log_note "Rendering smoke template for ${CHART_DIR}..."
  helm template smoke "${CHART_DIR}" --namespace "${CHART_NAME}" >/dev/null
}

prep_release() {
  local tag="$1"
  local do_push="$2"
  local branch="$3"
  local version="${tag##*-v}"

  ensure_clean_worktree
  ensure_named_branch "${branch}"
  ensure_tag_absent "${tag}"

  log_note "Updating ${CHANGELOG_FILE} for ${tag}..."
  update_changelog "${tag}"
  log_note "Updating ${CHART_FILE} version to ${version}..."
  update_chart_version "${version}"
  ensure_chart_version_matches_tag "${tag}"
  validate_chart

  git add "${CHANGELOG_FILE}" "${CHART_FILE}"
  if git diff --cached --quiet -- "${CHANGELOG_FILE}" "${CHART_FILE}"; then
    log_note "No chart release prep changes to commit."
  else
    git commit -m "Prepare chart release ${tag}" -- "${CHANGELOG_FILE}" "${CHART_FILE}"
    log_success "Committed chart release prep for ${tag}."
  fi

  if [[ "${do_push}" -eq 1 ]]; then
    log_note "Pushing current branch..."
    push_current_branch "${branch}"
    log_success "Branch pushed."
  fi
}

create_and_push_tag() {
  local tag="$1"
  ensure_release_section_ready "${tag}"
  ensure_chart_version_matches_tag "${tag}"
  ensure_tag_absent "${tag}"
  git tag -a "${tag}" -m "Release ${tag}"
  git push origin "refs/tags/${tag}"
  log_success "Tag pushed: ${tag}"
  log_success "The chart publish workflow will run from this tag push."
}

main() {
  init_output_style
  ensure_repo_ready

  local mode=""
  local raw_version=""
  local allow_non_main=0
  local no_push=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --prep|--publish)
        if [[ -n "${mode}" ]]; then
          log_error "Choose only one mode: --prep or --publish."
          exit 1
        fi
        mode="${1#--}"
        shift
        ;;
      --allow-non-main)
        allow_non_main=1
        shift
        ;;
      --no-push)
        no_push=1
        shift
        ;;
      -h|--help)
        show_usage
        exit 0
        ;;
      --)
        shift
        break
        ;;
      -*)
        log_error "Unknown option: $1"
        show_usage >&2
        exit 1
        ;;
      *)
        if [[ -n "${raw_version}" ]]; then
          log_error "Unexpected extra argument: $1"
          show_usage >&2
          exit 1
        fi
        raw_version="$1"
        shift
        ;;
    esac
  done

  if [[ -z "${mode}" || -z "${raw_version}" ]]; then
    show_usage >&2
    exit 1
  fi

  if [[ "${mode}" != "prep" && "${no_push}" -eq 1 ]]; then
    log_error "--no-push is only valid with --prep."
    exit 1
  fi

  local tag=""
  tag="$(normalize_tag "${raw_version}")"

  local branch=""
  branch="$(git rev-parse --abbrev-ref HEAD)"

  case "${mode}" in
    prep)
      prep_release "${tag}" "$(( ! no_push ))" "${branch}"
      ;;
    publish)
      ensure_clean_worktree
      if [[ "${allow_non_main}" -eq 1 ]]; then
        ensure_head_in_branch_history "${MAIN_BRANCH}"
      else
        ensure_named_branch "${branch}"
        if [[ "${branch}" != "${MAIN_BRANCH}" ]]; then
          log_error "--publish must run from ${MAIN_BRANCH} unless --allow-non-main is set."
          exit 1
        fi
        ensure_branch_synced "${MAIN_BRANCH}"
      fi
      create_and_push_tag "${tag}"
      ;;
    *)
      log_error "Unsupported mode: ${mode}"
      exit 1
      ;;
  esac
}

main "$@"
