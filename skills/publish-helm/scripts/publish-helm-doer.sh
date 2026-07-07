#!/usr/bin/env bash
set -euo pipefail

S_RESET=""
S_BOLD=""
S_DIM=""
S_RED=""
S_YELLOW=""
S_GREEN=""
S_CYAN=""
VERIFY_PULL_DIR=""

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

cleanup_verify_pull_dir() {
  if [[ -n "${VERIFY_PULL_DIR:-}" ]]; then
    rm -rf "${VERIFY_PULL_DIR}"
  fi
}

show_usage() {
  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}publish-helm-doer.sh${S_RESET} ${S_DIM}--mode prep --tag X.Y.Z --chart-dir DIR --chart-name NAME [options]${S_RESET}"
  printf '%b\n' "  ${S_CYAN}publish-helm-doer.sh${S_RESET} ${S_DIM}--mode publish --tag X.Y.Z --chart-dir DIR --chart-name NAME [options]${S_RESET}"
  printf '%b\n' "  ${S_CYAN}publish-helm-doer.sh${S_RESET} ${S_DIM}--mode verify --tag X.Y.Z --chart-name NAME --oci-repository OCI_BASE${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}--project-dir DIR${S_RESET}       Project directory, default current directory"
  printf '%b\n' "  ${S_YELLOW}--tag-prefix PREFIX${S_RESET}     Release tag prefix, default <chart-name>-chart"
  printf '%b\n' "  ${S_YELLOW}--main-branch BRANCH${S_RESET}    Default branch, default repository default branch or main"
  printf '%b\n' "  ${S_YELLOW}--changelog FILE${S_RESET}        Changelog path, default <chart-dir>/CHANGELOG.md"
  printf '%b\n' "  ${S_YELLOW}--oci-repository OCI${S_RESET}    Verify mode: OCI repository base without chart name or version"
  printf '%b\n' "  ${S_YELLOW}--public-verify${S_RESET}         Verify mode hint: use the current unauthenticated/authenticated Helm session as-is"
  printf '%b\n' "  ${S_YELLOW}--no-push${S_RESET}               Prep only: commit but do not push branch"
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
  [[ -n "${branch}" ]] && printf '%s\n' "${branch}" || printf '%s\n' "main"
}

normalize_tag() {
  local raw="$1"
  local tag_prefix="$2"
  local semver_re='[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?'
  local full_prefix="${tag_prefix}-v"
  local version_part=""
  if [[ "${raw}" =~ ^${semver_re}$ ]]; then
    printf '%s\n' "${tag_prefix}-v${raw}"
    return
  fi
  if [[ "${raw:0:${#full_prefix}}" == "${full_prefix}" ]]; then
    version_part="${raw:${#full_prefix}}"
    if [[ "${version_part}" =~ ^${semver_re}$ ]]; then
      printf '%s\n' "${raw}"
      return
    fi
  fi
  log_error "Version/tag must be X.Y.Z[-prerelease] or ${tag_prefix}-vX.Y.Z[-prerelease]"
  exit 1
}

tag_version() {
  printf '%s\n' "${1##*-v}"
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
  git fetch origin "${branch}"
  local local_commit remote_commit
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
release_idx = next((i for i, line in enumerate(lines) if any(p.match(line.strip()) for p in patterns)), None)
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

ensure_unreleased_changelog_note() {
  local version="$1"
  local chart_name="$2"
  local changelog="$3"
  python3 - "${version}" "${chart_name}" "${changelog}" <<'PY'
import pathlib
import re
import sys

version, chart_name, changelog_path = sys.argv[1], sys.argv[2], sys.argv[3]
path = pathlib.Path(changelog_path)
text = path.read_text(encoding="utf-8")
headers = list(re.finditer(r"^## \[.+?\].*$", text, re.MULTILINE))
if not headers:
    raise SystemExit("No CHANGELOG headers found")
unreleased_idx = next((idx for idx, h in enumerate(headers) if h.group(0).strip() == "## [Unreleased]"), None)
if unreleased_idx is None:
    raise SystemExit("Unable to locate Unreleased heading")
header = headers[unreleased_idx]
section_end = headers[unreleased_idx + 1].start() if unreleased_idx + 1 < len(headers) else len(text)
if text[header.end():section_end].strip():
    raise SystemExit(0)
note = f"\n\n- Bumped {chart_name} Helm chart to {version}.\n\n"
path.write_text(text[:header.end()] + note + text[section_end:], encoding="utf-8")
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

update_chart_version() {
  local chart_file="$1"
  local version="$2"
  python3 - "${chart_file}" "${version}" <<'PY'
import pathlib
import re
import sys

chart_file, version = sys.argv[1], sys.argv[2]
path = pathlib.Path(chart_file)
text = path.read_text(encoding="utf-8")
new_text, count = re.subn(r"(?m)^version:\s*.*$", f"version: {version}", text, count=1)
if count != 1:
    raise SystemExit(f"Unable to locate chart version in {chart_file}")
path.write_text(new_text, encoding="utf-8")
PY
}

read_chart_version() {
  local chart_file="$1"
  python3 - "${chart_file}" <<'PY'
import pathlib
import re
import sys

match = re.search(r"(?m)^version:\s*(.+?)\s*$", pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if not match:
    raise SystemExit("Unable to locate chart version")
print(match.group(1).strip().strip("'\""), end="")
PY
}

chart_dependency_remote_repositories() {
  local chart_path="$1"
  helm dependency list "${chart_path}" | awk '
    NR == 1 { next }
    $3 ~ /^https?:\/\// {
      name = $1
      gsub(/[^A-Za-z0-9_.-]/, "-", name)
      if (name == "") { name = "repo-" NR }
      if (!seen_url[$3]++) {
        base = name
        candidate = base
        suffix = 2
        while (seen_name[candidate]) {
          candidate = base "-" suffix
          suffix += 1
        }
        seen_name[candidate] = 1
        print candidate "\t" $3
      }
    }
  '
}

seed_chart_dependency_repositories() {
  local chart_path="$1"
  local repo_name repo_url
  while IFS=$'\t' read -r repo_name repo_url; do
    [[ -n "${repo_name}" && -n "${repo_url}" ]] || continue
    helm repo add "${repo_name}" "${repo_url}" >/dev/null
  done < <(chart_dependency_remote_repositories "${chart_path}")
}

validate_chart() {
  local chart_dir="$1"
  local chart_name="$2"
  local state_dir=""
  require_cmd "helm"
  state_dir="$(mktemp -d)"
  (
    trap 'rm -rf "${state_dir}"' EXIT
    mkdir -p "${state_dir}/repository"
    export HELM_REPOSITORY_CACHE="${state_dir}/repository"
    export HELM_REPOSITORY_CONFIG="${state_dir}/repositories.yaml"
    if grep -q '^dependencies:' "${chart_dir}/Chart.yaml"; then
      seed_chart_dependency_repositories "${chart_dir}"
      if [[ -f "${chart_dir}/Chart.lock" ]]; then
        helm dependency build "${chart_dir}"
      else
        helm dependency update "${chart_dir}"
      fi
    fi
  )
  helm lint --strict --with-subcharts "${chart_dir}"
  helm template smoke "${chart_dir}" --namespace "${chart_name}" >/dev/null
}

ensure_chart_version_matches() {
  local chart_file="$1"
  local expected="$2"
  local actual=""
  actual="$(read_chart_version "${chart_file}")"
  if [[ "${actual}" != "${expected}" ]]; then
    log_error "${chart_file} version ${actual} does not match release ${expected}."
    log_note "Run --mode prep for ${expected} and merge that Chart.yaml change before publishing."
    exit 1
  fi
}

prep_release() {
  local tag="$1"
  local version="$2"
  local chart_dir="$3"
  local chart_name="$4"
  local changelog="$5"
  local do_push="$6"
  local branch="$7"
  local main_branch="$8"
  local chart_file="${chart_dir}/Chart.yaml"
  local charts_path="${chart_dir}/charts"
  local charts_staged=""
  local release_branch=""
  local staged_paths=("${changelog}" "${chart_file}")
  ensure_tag_absent "${tag}"
  ensure_release_source_ready "${branch}" "${main_branch}" "prep"
  release_branch="$(release_branch_name "${tag}")"
  ensure_release_branch_absent "${release_branch}"
  git switch -c "${release_branch}"
  branch="${release_branch}"
  log_success "Created release branch ${release_branch} from ${main_branch}."
  ensure_unreleased_changelog_note "${version}" "${chart_name}" "${changelog}"
  update_changelog "${tag}" "${changelog}"
  update_chart_version "${chart_file}" "${version}"
  validate_chart "${chart_dir}" "${chart_name}"
  git add "${changelog}" "${chart_file}"
  if [[ -f "${chart_dir}/Chart.lock" ]]; then
    git add "${chart_dir}/Chart.lock"
    staged_paths+=("${chart_dir}/Chart.lock")
  fi
  if [[ -d "${charts_path}" ]]; then
    git add -A "${charts_path}"
    charts_staged="$(git diff --cached --name-only -- "${charts_path}")"
    if [[ -n "${charts_staged}" ]]; then
      staged_paths+=("${charts_path}")
    fi
  fi
  if git diff --cached --quiet -- "${staged_paths[@]}"; then
    log_note "No chart release changes to commit."
  else
    git commit -m "Prepare chart release ${tag}" -- "${staged_paths[@]}"
    log_success "Committed chart release prep."
  fi
  if [[ "${do_push}" -eq 1 ]]; then
    push_current_branch "${branch}"
    log_success "Branch pushed."
  fi
}

publish_tag() {
  local tag="$1"
  local version="$2"
  local chart_file="$3"
  local changelog="$4"
  ensure_clean_worktree
  ensure_release_section_ready "${tag}" "${changelog}"
  ensure_chart_version_matches "${chart_file}" "${version}"
  ensure_tag_absent "${tag}"
  git tag -a "${tag}" -m "Release ${tag}"
  git push origin "refs/tags/${tag}"
  log_success "Tag pushed: ${tag}"
}

validate_oci_repository_base() {
  local oci_repository="$1"
  local chart_name="$2"
  [[ -n "${oci_repository}" ]] || { log_error "--oci-repository is required for verify mode."; exit 1; }
  [[ "${oci_repository}" == oci://* ]] || { log_error "--oci-repository must start with oci://"; exit 1; }
  if [[ "${oci_repository%/}" == */"${chart_name}" ]]; then
    log_error "--oci-repository must be the repository base and must not include chart name '${chart_name}'."
    printf '%b\n' "${S_DIM}Pass the base repository, for example oci://registry.example.com/org/charts, then verification will append /${chart_name}.${S_RESET}" >&2
    exit 1
  fi
}

verify_chart() {
  local oci_repository="$1"
  local chart_name="$2"
  local version="$3"
  validate_oci_repository_base "${oci_repository}" "${chart_name}"
  require_cmd "helm"
  VERIFY_PULL_DIR="$(mktemp -d)"
  trap cleanup_verify_pull_dir EXIT
  helm pull "${oci_repository%/}/${chart_name}" --version "${version}" -d "${VERIFY_PULL_DIR}"
  log_success "Verified OCI chart pull: ${oci_repository%/}/${chart_name} --version ${version}"
}

main() {
  init_output_style
  local mode=""
  local raw_tag=""
  local tag_prefix=""
  local project_dir="${PWD}"
  local main_branch=""
  local chart_dir=""
  local chart_name=""
  local changelog=""
  local oci_repository=""
  local no_push=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --mode) mode="${2:-}"; shift 2 ;;
      --tag) raw_tag="${2:-}"; shift 2 ;;
      --tag-prefix) tag_prefix="${2:-}"; shift 2 ;;
      --project-dir) project_dir="${2:-}"; shift 2 ;;
      --main-branch) main_branch="${2:-}"; shift 2 ;;
      --chart-dir) chart_dir="${2:-}"; shift 2 ;;
      --chart-name) chart_name="${2:-}"; shift 2 ;;
      --changelog) changelog="${2:-}"; shift 2 ;;
      --oci-repository) oci_repository="${2:-}"; shift 2 ;;
      --public-verify) shift ;;
      --no-push) no_push=1; shift ;;
      --allow-non-main) log_error "--allow-non-main is not supported; chart publish must run from the clean synced default branch."; exit 1 ;;
      -h|--help) show_usage; exit 0 ;;
      *) log_error "Unknown argument: $1"; show_usage >&2; exit 1 ;;
    esac
  done

  local missing_required=()
  [[ -n "${mode}" ]] || missing_required+=("--mode")
  [[ -n "${raw_tag}" ]] || missing_required+=("--tag")
  [[ -n "${chart_name}" ]] || missing_required+=("--chart-name")
  if ((${#missing_required[@]} > 0)); then
    log_error "Missing required option(s): ${missing_required[*]}"
    if [[ " ${missing_required[*]} " == *" --tag "* ]]; then
      printf '%b\n' "${S_DIM}Provide an explicit release version/tag; do not infer it from Chart.yaml, latest tags, branch names, or changelog text.${S_RESET}" >&2
    fi
    show_usage >&2
    exit 1
  fi

  case "${mode}" in
    prep|publish|verify) ;;
    *)
      log_error "--mode must be prep, publish, or verify."
      show_usage >&2
      exit 1
      ;;
  esac

  tag_prefix="${tag_prefix:-${chart_name}-chart}"
  local tag version
  tag="$(normalize_tag "${raw_tag}" "${tag_prefix}")"
  version="$(tag_version "${tag}")"

  if [[ "${mode}" == "verify" ]]; then
    verify_chart "${oci_repository}" "${chart_name}" "${version}"
    return 0
  fi

  [[ -n "${chart_dir}" ]] || { log_error "Missing required option(s): --chart-dir"; show_usage >&2; exit 1; }
  require_cmd "git"
  require_cmd "python3"
  cd "${project_dir}"
  main_branch="${main_branch:-$(repo_default_branch)}"
  changelog="${changelog:-${chart_dir}/CHANGELOG.md}"
  [[ -f "${chart_dir}/Chart.yaml" ]] || { log_error "Missing chart file: ${chart_dir}/Chart.yaml"; exit 1; }
  [[ -f "${changelog}" ]] || { log_error "Missing changelog: ${changelog}"; exit 1; }
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD)"

  case "${mode}" in
    prep)
      prep_release "${tag}" "${version}" "${chart_dir}" "${chart_name}" "${changelog}" "$((1 - no_push))" "${branch}" "${main_branch}"
      ;;
    publish)
      ensure_tag_absent "${tag}"
      ensure_release_source_ready "${branch}" "${main_branch}" "publish"
      publish_tag "${tag}" "${version}" "${chart_dir}/Chart.yaml" "${changelog}"
      ;;
    verify) ;;
  esac
}

main "$@"
