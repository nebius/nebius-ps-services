#!/usr/bin/env bash
set -euo pipefail

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
  printf '%b\n' "  ${S_CYAN}publish-image-doer.sh${S_RESET} ${S_DIM}--mode prep --tag X.Y.Z --tag-prefix NAME [options]${S_RESET}"
  printf '%b\n' "  ${S_CYAN}publish-image-doer.sh${S_RESET} ${S_DIM}--mode publish --tag X.Y.Z --tag-prefix NAME [options]${S_RESET}"
  printf '%b\n' "  ${S_CYAN}publish-image-doer.sh${S_RESET} ${S_DIM}--mode verify --tag X.Y.Z --tag-prefix NAME --image-name IMAGE${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}--project-dir DIR${S_RESET}       Project directory, default current directory"
  printf '%b\n' "  ${S_YELLOW}--main-branch BRANCH${S_RESET}    Default branch, default repository default branch or main"
  printf '%b\n' "  ${S_YELLOW}--changelog FILE${S_RESET}        Changelog path relative to project dir, default CHANGELOG.md"
  printf '%b\n' "  ${S_YELLOW}--no-push${S_RESET}               Prep only: commit but do not push branch"
  printf '%b\n' "  ${S_YELLOW}--image-name IMAGE${S_RESET}      Verify mode: image repository without tag"
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}              Show help"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "Required command not found: ${cmd}"
    exit 1
  fi
}

repo_default_branch() {
  local branch=""
  branch="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  branch="${branch#origin/}"
  if [[ -n "${branch}" ]]; then
    printf '%s\n' "${branch}"
    return
  fi
  printf '%s\n' "main"
}

derive_tag_prefix() {
  local project_dir="$1"
  basename -- "${project_dir}"
}

normalize_tag() {
  local raw="$1"
  local tag_prefix="$2"
  local full_prefix="${tag_prefix}-v"
  local version_part=""
  if [[ "${raw}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf '%s\n' "${tag_prefix}-v${raw}"
    return
  fi
  if [[ "${raw:0:${#full_prefix}}" == "${full_prefix}" ]]; then
    version_part="${raw:${#full_prefix}}"
    if [[ "${version_part}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      printf '%s\n' "${raw}"
      return
    fi
  fi
  log_error "Version/tag must be X.Y.Z or ${tag_prefix}-vX.Y.Z"
  exit 1
}

tag_version() {
  local tag="$1"
  printf '%s\n' "${tag##*-v}"
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
    log_error "This operation must run from a local branch, not detached HEAD."
    exit 1
  fi
}

push_current_branch() {
  local branch="$1"
  ensure_named_branch "${branch}"
  git push --set-upstream origin "HEAD:${branch}"
}

release_source_required_note() {
  local main_branch="$1"
  log_note "First open and merge a PR for your current branch into ${main_branch}, then switch to ${main_branch}, fast-forward, and rerun."
}

ensure_release_source_ready() {
  local branch="$1"
  local main_branch="$2"
  local mode="$3"
  local status_output=""
  ensure_named_branch "${branch}"
  if [[ "${branch}" != "${main_branch}" ]]; then
    log_error "--mode ${mode} must run from ${main_branch}; current branch is ${branch}."
    release_source_required_note "${main_branch}"
    exit 1
  fi
  status_output="$(git status --short --untracked-files=all)"
  if [[ -n "${status_output}" ]]; then
    log_error "--mode ${mode} requires a clean working tree on ${main_branch}."
    printf '%s\n' "${status_output}" >&2
    release_source_required_note "${main_branch}"
    exit 1
  fi
  ensure_branch_synced "${main_branch}"
}

release_branch_name() {
  local tag="$1"
  printf 'release/%s\n' "${tag}"
}

ensure_release_branch_absent() {
  local branch="$1"
  local remote_status=0
  if git show-ref --verify --quiet "refs/heads/${branch}"; then
    log_error "Release branch already exists locally: ${branch}"
    exit 1
  fi
  git ls-remote --exit-code --heads origin "${branch}" >/dev/null 2>&1 || remote_status=$?
  if [[ "${remote_status}" -eq 0 ]]; then
    log_error "Release branch already exists on origin: ${branch}"
    exit 1
  fi
  if [[ "${remote_status}" -ne 2 ]]; then
    log_error "Unable to check origin for release branch: ${branch}"
    exit 1
  fi
}

ensure_branch_synced() {
  local branch="$1"
  local local_commit=""
  local remote_commit=""
  git fetch origin "${branch}"
  local_commit="$(git rev-parse HEAD)"
  remote_commit="$(git rev-parse "origin/${branch}")"
  if [[ "${local_commit}" != "${remote_commit}" ]]; then
    log_error "Local ${branch} is not at origin/${branch}."
    log_note "local : ${local_commit}"
    log_note "origin: ${remote_commit}"
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
  local changelog="$2"
  python3 - "${tag}" "${changelog}" <<'PY'
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
    raise SystemExit(f"Missing changelog section for {tag} in {changelog_path}")
content = []
for line in lines[release_idx + 1:]:
    if line.startswith("## "):
        break
    content.append(line)
if not "\n".join(content).strip():
    raise SystemExit(f"Release changelog section for {tag} in {changelog_path} is empty")
PY
}

update_changelog() {
  local tag="$1"
  local changelog="$2"
  local release_date=""
  release_date="$(date +%Y-%m-%d)"
  python3 - "${tag}" "${release_date}" "${changelog}" <<'PY'
import pathlib
import re
import sys

tag, date_str, changelog_path = sys.argv[1], sys.argv[2], sys.argv[3]
path = pathlib.Path(changelog_path)
text = path.read_text(encoding="utf-8")
if "## [Unreleased]" not in text:
    raise SystemExit("Unreleased section not found in CHANGELOG.md")
release_header = f"## [{tag}] - {date_str}"
headers = list(re.finditer(r"^## \[.+?\].*$", text, re.MULTILINE))
if not headers:
    raise SystemExit("No CHANGELOG headers found")
preamble = text[: headers[0].start()]
sections = []
for idx, header in enumerate(headers):
    end = headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
    sections.append((header.group(0), text[header.end():end]))

def normal(content: str) -> str:
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
    raise SystemExit("Unable to locate Unreleased heading")
unreleased_header, unreleased_content = sections[unreleased_idx]
payload = unreleased_content.strip("\n")
if tag_idx is None:
    new_sections = []
    for idx, (header, content) in enumerate(sections):
        if idx == unreleased_idx:
            new_sections.append((unreleased_header, "\n\n"))
            new_sections.append((release_header, normal(payload)))
        else:
            new_sections.append((header, content))
    sections = new_sections
else:
    if payload.strip():
        tag_header, tag_content = sections[tag_idx]
        sections[tag_idx] = (tag_header, merge_content(payload, tag_content))
    sections[unreleased_idx] = (unreleased_header, "\n\n")
path.write_text(preamble + "".join(f"{h}{c}" for h, c in sections), encoding="utf-8")
PY
}

prep_release() {
  local tag="$1"
  local changelog="$2"
  local do_push="$3"
  local branch="$4"
  local main_branch="$5"
  local release_branch=""
  ensure_tag_absent "${tag}"
  ensure_release_source_ready "${branch}" "${main_branch}" "prep"
  release_branch="$(release_branch_name "${tag}")"
  ensure_release_branch_absent "${release_branch}"
  git switch -c "${release_branch}"
  branch="${release_branch}"
  log_success "Created release branch ${release_branch} from ${main_branch}."
  update_changelog "${tag}" "${changelog}"
  git add "${changelog}"
  if git diff --cached --quiet -- "${changelog}"; then
    log_note "No changelog changes to commit."
  else
    git commit -m "Prepare image release ${tag}" -- "${changelog}"
    log_success "Committed ${changelog}."
  fi
  if [[ "${do_push}" -eq 1 ]]; then
    push_current_branch "${branch}"
    log_success "Branch pushed."
  fi
}

publish_tag() {
  local tag="$1"
  local changelog="$2"
  ensure_clean_worktree
  ensure_release_section_ready "${tag}" "${changelog}"
  ensure_tag_absent "${tag}"
  git tag -a "${tag}" -m "Release ${tag}"
  git push origin "refs/tags/${tag}"
  log_success "Tag pushed: ${tag}"
}

verify_image() {
  local image_name="$1"
  local version="$2"
  require_cmd "docker"
  if [[ -z "${image_name}" ]]; then
    log_error "--image-name is required for verify mode."
    exit 1
  fi
  docker buildx imagetools inspect "${image_name}:${version}" >/dev/null
  log_success "Verified image tag: ${image_name}:${version}"
}

main() {
  init_output_style
  local mode=""
  local raw_tag=""
  local tag_prefix=""
  local project_dir="${PWD}"
  local main_branch=""
  local changelog="CHANGELOG.md"
  local no_push=0
  local image_name=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --mode) mode="${2:-}"; shift 2 ;;
      --tag) raw_tag="${2:-}"; shift 2 ;;
      --tag-prefix) tag_prefix="${2:-}"; shift 2 ;;
      --project-dir) project_dir="${2:-}"; shift 2 ;;
      --main-branch) main_branch="${2:-}"; shift 2 ;;
      --changelog) changelog="${2:-}"; shift 2 ;;
      --no-push) no_push=1; shift ;;
      --allow-non-main) log_error "--allow-non-main is not supported; image publish must run from the clean synced default branch."; exit 1 ;;
      --image-name) image_name="${2:-}"; shift 2 ;;
      -h|--help) show_usage; exit 0 ;;
      *) log_error "Unknown argument: $1"; show_usage >&2; exit 1 ;;
    esac
  done

  [[ -n "${mode}" && -n "${raw_tag}" ]] || { show_usage >&2; exit 1; }
  require_cmd "git"
  require_cmd "python3"
  cd "${project_dir}"
  main_branch="${main_branch:-$(repo_default_branch)}"
  tag_prefix="${tag_prefix:-$(derive_tag_prefix "${PWD}")}"
  changelog="${changelog#./}"
  [[ -f "${changelog}" ]] || { log_error "Missing changelog: ${changelog}"; exit 1; }
  local tag=""
  local version=""
  local branch=""
  tag="$(normalize_tag "${raw_tag}" "${tag_prefix}")"
  version="$(tag_version "${tag}")"
  branch="$(git rev-parse --abbrev-ref HEAD)"

  case "${mode}" in
    prep)
      prep_release "${tag}" "${changelog}" "$((1 - no_push))" "${branch}" "${main_branch}"
      ;;
    publish)
      ensure_tag_absent "${tag}"
      ensure_release_source_ready "${branch}" "${main_branch}" "publish"
      publish_tag "${tag}" "${changelog}"
      ;;
    verify)
      verify_image "${image_name}" "${version}"
      ;;
    *)
      log_error "--mode must be prep, publish, or verify."
      exit 1
      ;;
  esac
}

main "$@"
