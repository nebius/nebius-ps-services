#!/usr/bin/env bash
set -euo pipefail

S_RESET=""
S_BOLD=""
S_DIM=""
S_RED=""
S_AMBER=""
S_YELLOW=""
S_GREEN=""
S_CYAN=""
TMP_DIR=""

init_output_style() {
  if [[ ( -t 1 || -t 2 ) && "${TERM:-}" != "dumb" && -z "${NO_COLOR:-}" ]]; then
    S_RESET=$'\033[0m'
    S_BOLD=$'\033[1m'
    S_DIM=$'\033[2m'
    S_RED=$'\033[31m'
    S_AMBER=$'\033[38;5;214m'
    S_YELLOW=$'\033[33m'
    S_GREEN=$'\033[32m'
    S_CYAN=$'\033[36m'
  fi
}

log_error() {
  printf '%b\n' "${S_RED}${S_BOLD}ERROR:${S_RESET}${S_RED} ${1}${S_RESET}" >&2
}

log_warn() {
  printf '%b\n' "${S_AMBER}${1}${S_RESET}" >&2
}

log_note() {
  printf '%b\n' "${S_DIM}${1}${S_RESET}"
}

log_success() {
  printf '%b\n' "${S_GREEN}${1}${S_RESET}"
}

cleanup() {
  if [[ -n "${TMP_DIR:-}" ]]; then
    rm -rf "${TMP_DIR}"
  fi
}

show_usage() {
  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh${S_RESET} ${S_DIM}[options]${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}--lock-file PATH${S_RESET}    Lock file to read (default: chart upstream-soperator.lock.yaml)"
  printf '%b\n' "  ${S_YELLOW}--check-latest${S_RESET}      Compare the lock release with the highest GitHub SemVer release without writing"
  printf '%b\n' "  ${S_YELLOW}--sync${S_RESET}              Apply full upstream sync and validate it without staging changes"
  printf '%b\n' "  ${S_YELLOW}--latest${S_RESET}            With --sync, use the highest GitHub SemVer release and update the lock"
  printf '%b\n' "  ${S_YELLOW}--scope SCOPE${S_RESET}       Limit read-only checks to scripts, crds, images, or all (default: all)"
  printf '%b\n' "  ${S_YELLOW}--report${S_RESET}            Print detailed per-item status; with --sync, print a readable changed-file summary"
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}          Show help"
  printf '\n'
  printf '%b\n' "${S_BOLD}Examples from repository root:${S_RESET}"
  printf '%b\n' "  ${S_DIM}Verify all upstream tracking without writing:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --scope all --report${S_RESET}"
  printf '\n'
  printf '%b\n' "  ${S_DIM}Check whether the lock matches the highest GitHub SemVer release:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --check-latest${S_RESET}"
  printf '\n'
  printf '%b\n' "  ${S_DIM}Refresh the release already named in the lock and leave changes unstaged:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --sync --report${S_RESET}"
  printf '\n'
  printf '%b\n' "  ${S_DIM}Sync the highest GitHub SemVer release, create a branch when needed, validate, and leave changes unstaged:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --latest --sync --report${S_RESET}"
  printf '\n'
  printf '%b\n' "  ${S_DIM}Read-only scoped checks:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --scope scripts --report${S_RESET}"
  printf '%b\n' "  ${S_CYAN}helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --scope crds --report${S_RESET}"
  printf '%b\n' "  ${S_CYAN}helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --scope images --report${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_BOLD}Write-mode notes:${S_RESET}"
  printf '%b\n' "  ${S_DIM}--sync requires a clean working tree and always uses the full upstream release contract.${S_RESET}"
  printf '%b\n' "  ${S_DIM}When run from main, master, the default branch, or detached HEAD, it creates sync-soperator-<release>.${S_RESET}"
  printf '%b\n' "  ${S_DIM}Local --sync does not stage, commit, install tools, push, open a PR, or merge.${S_RESET}"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "Required command not found: ${cmd}"
    print_install_hint "${cmd}"
    exit 1
  fi
}

print_install_hint() {
  local cmd="$1"
  local os=""
  os="$(uname -s 2>/dev/null || true)"

  case "${os}" in
    Darwin)
      case "${cmd}" in
        helm) log_warn "Install on macOS: brew install helm" ;;
        yq) log_warn "Install on macOS: brew install yq" ;;
        git) log_warn "Install on macOS: brew install git" ;;
        curl) log_warn "Install on macOS: brew install curl" ;;
        ruby) log_warn "Install on macOS: brew install ruby" ;;
        awk|diff|mktemp|sed|tar) log_warn "Install macOS command line tools: xcode-select --install" ;;
        *) log_warn "Install '${cmd}' with Homebrew or macOS command line tools." ;;
      esac
      ;;
    Linux)
      case "${cmd}" in
        helm) log_warn "Install on Linux: https://helm.sh/docs/intro/install/" ;;
        yq) log_warn "Install on Linux: sudo sh -c 'curl -fsSL https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -o /usr/local/bin/yq && chmod +x /usr/local/bin/yq'" ;;
        awk) log_warn "Install on Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y gawk" ;;
        diff) log_warn "Install on Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y diffutils" ;;
        mktemp) log_warn "Install on Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y coreutils" ;;
        curl|git|ruby|sed|tar) log_warn "Install on Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y ${cmd}" ;;
        *) log_warn "Install '${cmd}' with your Linux package manager." ;;
      esac
      ;;
    *)
      log_warn "Install '${cmd}' for your operating system, then rerun this command."
      ;;
  esac
}

require_yq_v4() {
  require_cmd "yq"
  local version_output=""
  version_output="$(yq --version 2>/dev/null || true)"
  if [[ ! "${version_output}" =~ version[[:space:]]v?4\. ]]; then
    log_error "Required command 'yq' must be mikefarah yq v4; found: ${version_output:-unknown version}"
    print_install_hint "yq"
    exit 1
  fi
}

script_dir() {
  local source="${BASH_SOURCE[0]}"
  while [[ -L "${source}" ]]; do
    local dir
    dir="$(cd -P "$(dirname "${source}")" >/dev/null 2>&1 && pwd)"
    source="$(readlink "${source}")"
    [[ "${source}" != /* ]] && source="${dir}/${source}"
  done
  cd -P "$(dirname "${source}")" >/dev/null 2>&1 && pwd
}

repo_root() {
  local dir="$1"
  if git -C "${dir}" rev-parse --show-toplevel >/dev/null 2>&1; then
    git -C "${dir}" rev-parse --show-toplevel
  else
    cd "${dir}/../../.." >/dev/null 2>&1 && pwd
  fi
}

yaml_value() {
  local file="$1"
  local key="$2"
  ruby -ryaml -e 'data = YAML.safe_load(File.read(ARGV[0]), aliases: true, permitted_classes: [Symbol]); value = data.fetch(ARGV[1]); puts value' "${file}" "${key}"
}

json_string() {
  ruby -rjson -e 'print JSON.generate(ARGV.fetch(0))' "$1"
}

compare_releases() {
  ruby -e '
SEMVER = /\A(?<major>0|[1-9]\d*)\.(?<minor>0|[1-9]\d*)\.(?<patch>0|[1-9]\d*)(?:-(?<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\z/

def parse_version(value)
  version = value.to_s.sub(/\Av/, "")
  match = SEMVER.match(version)
  unless match
    warn "invalid release version #{value.inspect}; expected SemVer X.Y.Z"
    exit 2
  end

  prerelease = match[:prerelease]
  if prerelease
    prerelease.split(".").each do |identifier|
      if identifier.match?(/\A\d+\z/) && identifier.length > 1 && identifier.start_with?("0")
        warn "invalid release version #{value.inspect}; numeric prerelease identifiers must not have leading zeroes"
        exit 2
      end
    end
  end

  [[match[:major].to_i, match[:minor].to_i, match[:patch].to_i], prerelease]
end

def compare_prerelease(left, right)
  return 0 if left == right
  return 1 if left.nil?
  return -1 if right.nil?

  left_parts = left.split(".")
  right_parts = right.split(".")
  [left_parts.length, right_parts.length].max.times do |index|
    left_part = left_parts[index]
    right_part = right_parts[index]
    return -1 if left_part.nil?
    return 1 if right_part.nil?

    left_numeric = left_part.match?(/\A\d+\z/)
    right_numeric = right_part.match?(/\A\d+\z/)
    comparison =
      if left_numeric && right_numeric
        left_part.to_i <=> right_part.to_i
      elsif left_numeric
        -1
      elsif right_numeric
        1
      else
        left_part <=> right_part
      end
    return comparison unless comparison.zero?
  end
  0
end

left_core, left_pre = parse_version(ARGV.fetch(0))
right_core, right_pre = parse_version(ARGV.fetch(1))
core_comparison = left_core <=> right_core
puts(core_comparison.zero? ? compare_prerelease(left_pre, right_pre) : core_comparison)
' "$1" "$2"
}

default_git_branch() {
  local root="$1"
  local branch
  branch="$(git -C "${root}" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  branch="${branch#origin/}"
  printf '%s\n' "${branch:-main}"
}

safe_branch_fragment() {
  printf '%s\n' "$1" | sed -E 's/[^A-Za-z0-9._-]+/-/g; s/^-+//; s/-+$//'
}

branch_exists() {
  local root="$1"
  local branch="$2"
  git -C "${root}" show-ref --verify --quiet "refs/heads/${branch}"
}

next_available_sync_branch() {
  local root="$1"
  local release="$2"
  local base candidate index
  base="sync-soperator-$(safe_branch_fragment "${release}")"
  candidate="${base}"
  index=2
  while branch_exists "${root}" "${candidate}"; do
    candidate="${base}-${index}"
    index=$((index + 1))
  done
  printf '%s\n' "${candidate}"
}

ensure_sync_branch() {
  local root="$1"
  local release="$2"
  local current_branch default_branch
  current_branch="$(git -C "${root}" branch --show-current 2>/dev/null || true)"
  default_branch="$(default_git_branch "${root}")"

  if [[ -z "${current_branch}" ]]; then
    local sync_branch
    sync_branch="$(next_available_sync_branch "${root}" "${release}")"
    log_note "Creating feature branch '${sync_branch}' for upstream Soperator sync..."
    git -C "${root}" switch -c "${sync_branch}" >/dev/null
    return 0
  fi

  if [[ "${current_branch}" == "${default_branch}" || "${current_branch}" == "main" || "${current_branch}" == "master" ]]; then
    local sync_branch
    sync_branch="$(next_available_sync_branch "${root}" "${release}")"
    log_note "Creating feature branch '${sync_branch}' for upstream Soperator sync..."
    git -C "${root}" switch -c "${sync_branch}" >/dev/null
    return 0
  fi
}

ensure_clean_worktree() {
  local root="$1"
  local status_output=""
  status_output="$(git -C "${root}" status --short --untracked-files=all)"
  if [[ -n "${status_output}" ]]; then
    log_error "--sync requires a clean working tree before it mutates files."
    printf '%s\n' "${status_output}" >&2
    exit 1
  fi
}

update_lock_pin() {
  local lock_file="$1"
  local release="$2"
  local tag="$3"
  local commit="$4"
  local release_json tag_json commit_json
  release_json="$(json_string "${release}")"
  tag_json="$(json_string "${tag}")"
  commit_json="$(json_string "${commit}")"
  yq -i ".release = ${release_json} | .tag = ${tag_json} | .commit = ${commit_json}" "${lock_file}"
}

chart_metadata_paths() {
  printf '%s\0' \
    "helm-charts/soperator/Chart.yaml" \
    "helm-charts/soperator-activechecks/Chart.yaml" \
    "helm-charts/soperator-backup-config/Chart.yaml" \
    "helm-charts/soperator-checks/Chart.yaml" \
    "helm-charts/soperator-dcgm-exporter/Chart.yaml" \
    "helm-charts/soperator-notifier/Chart.yaml"
}

chart_metadata_changed() {
  local root="$1"
  local -a paths=()
  while IFS= read -r -d '' path; do
    paths+=("${path}")
  done < <(chart_metadata_paths)
  ! git -C "${root}" diff --quiet -- "${paths[@]}"
}

git_status_short() {
  local root="$1"
  git -C "${root}" status --short --untracked-files=all
}

git_short_status_label() {
  local short_status="$1"
  case "${short_status}" in
    "??") printf '%s\n' "new" ;;
    "!!") printf '%s\n' "ignored" ;;
    DD|AU|UD|UA|DU|AA|UU) printf '%s\n' "unmerged" ;;
    *R*) printf '%s\n' "renamed" ;;
    *C*) printf '%s\n' "copied" ;;
    *D*) printf '%s\n' "deleted" ;;
    *A*) printf '%s\n' "added" ;;
    *M*) printf '%s\n' "modified" ;;
    *T*) printf '%s\n' "typechange" ;;
    *) printf '%s\n' "changed" ;;
  esac
}

format_git_status_short() {
  local line short_status path label
  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    if [[ "${#line}" -lt 4 ]]; then
      printf '  %-10s %s\n' "changed" "${line}"
      continue
    fi
    short_status="${line:0:2}"
    path="${line:3}"
    label="$(git_short_status_label "${short_status}")"
    printf '  %-10s %s\n' "${label}" "${path}"
  done
}

report_changed_files() {
  local root="$1"
  local status_output=""
  status_output="$(git_status_short "${root}")"
  if [[ -z "${status_output}" ]]; then
    log_note "Changed files after sync: none."
    return 0
  fi

  printf '%b\n' "${S_BOLD}Changed files after sync:${S_RESET}"
  format_git_status_short <<<"${status_output}"
}

resolve_tag_commit() {
  local repo="$1"
  local tag="$2"
  local refs peeled direct

  refs="$(git ls-remote --tags "${repo}" "refs/tags/${tag}" "refs/tags/${tag}^{}")"
  peeled="$(printf '%s\n' "${refs}" | awk '$2 ~ /\^\{\}$/ { print $1; exit }')"
  direct="$(printf '%s\n' "${refs}" | awk '$2 !~ /\^\{\}$/ { print $1; exit }')"

  if [[ -n "${peeled}" ]]; then
    printf '%s\n' "${peeled}"
  elif [[ -n "${direct}" ]]; then
    printf '%s\n' "${direct}"
  else
    log_error "Could not resolve tag '${tag}' in ${repo}"
    exit 1
  fi
}

github_api_token() {
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    printf '%s\n' "${GITHUB_TOKEN}"
  elif [[ -n "${GH_TOKEN:-}" ]]; then
    printf '%s\n' "${GH_TOKEN}"
  fi
  return 0
}

curl_github_api() {
  local url="$1"
  shift

  local -a curl_args=(
    -fsSL
    -H "Accept: application/vnd.github+json"
    -H "X-GitHub-Api-Version: 2022-11-28"
  )
  local token
  token="$(github_api_token)"
  if [[ -n "${token}" ]]; then
    curl_args+=(-H "Authorization: Bearer ${token}")
  fi

  curl "${curl_args[@]}" "$@" "${url}"
}

release_tags_page() {
  local repo="$1"
  local page="$2"
  local api_repo
  api_repo="${repo#https://github.com/}"
  api_repo="${api_repo%.git}"
  curl_github_api "https://api.github.com/repos/${api_repo}/releases?per_page=100&page=${page}" \
    | ruby -rjson -e '
payload = JSON.parse(STDIN.read)
unless payload.is_a?(Array)
  warn "GitHub releases response is not an array"
  exit 1
end

if payload.empty?
  puts "__nebius_cxcli_empty_release_page__"
  exit
end

payload.each do |release|
  next if release["draft"] || release["prerelease"]
  tag = release["tag_name"].to_s.strip
  puts tag unless tag.empty?
end
'
}

highest_release_tag() {
  local repo="$1"
  local page=1
  local best=""
  local tags tag comparison page_empty

  while [[ "${page}" -le 20 ]]; do
    if ! tags="$(release_tags_page "${repo}" "${page}")"; then
      return 1
    fi
    page_empty=0

    while IFS= read -r tag; do
      [[ -n "${tag}" ]] || continue
      if [[ "${tag}" == "__nebius_cxcli_empty_release_page__" ]]; then
        page_empty=1
        continue
      fi
      if [[ -z "${best}" ]]; then
        if compare_releases "${tag}" "${tag}" >/dev/null 2>&1; then
          best="${tag}"
        fi
        continue
      fi
      if comparison="$(compare_releases "${tag}" "${best}" 2>/dev/null)" && [[ "${comparison}" -gt 0 ]]; then
        best="${tag}"
      fi
    done <<<"${tags}"

    [[ "${page_empty}" -eq 0 ]] || break
    page=$((page + 1))
  done

  printf '%s\n' "${best}"
}

fetch_release() {
  local repo="$1"
  local tag="$2"
  local destination="$3"
  local archive="${destination}/source.tar.gz"
  local url="${repo%/}/archive/refs/tags/${tag}.tar.gz"

  mkdir -p "${destination}/source"
  curl -fsSL "${url}" -o "${archive}"
  tar -xzf "${archive}" -C "${destination}/source" --strip-components=1
}

chart_dependency_remote_repositories() {
  local chart_path="$1"
  ruby -ryaml -e '
chart_file = File.join(ARGV.fetch(0), "Chart.yaml")
chart = YAML.safe_load(File.read(chart_file), aliases: true, permitted_classes: [Symbol])
seen_urls = {}
Array(chart["dependencies"]).each_with_index do |dependency, index|
  repository = dependency["repository"].to_s.strip
  next unless repository.start_with?("http://", "https://")
  next if seen_urls.key?(repository)

  name = dependency["name"].to_s.strip
  name = "repo-#{index + 1}" if name.empty?
  name = name.gsub(/[^A-Za-z0-9_.-]/, "-")
  name = "repo-#{index + 1}" if name.empty?
  seen_urls[repository] = name
end

seen_names = {}
seen_urls.each_with_index do |(repository, name), index|
  base = name.empty? ? "repo-#{index + 1}" : name
  candidate = base
  suffix = 2
  while seen_names.key?(candidate)
    candidate = "#{base}-#{suffix}"
    suffix += 1
  end
  seen_names[candidate] = true
  puts [candidate, repository].join("\t")
end
' "${chart_path}"
}

seed_chart_dependency_repositories() {
  local chart_path="$1"
  local repo_name repo_url seeded
  seeded=0

  while IFS=$'\t' read -r repo_name repo_url; do
    [[ -n "${repo_name}" && -n "${repo_url}" ]] || continue
    if [[ "${seeded}" -eq 0 ]]; then
      log_note "Preparing temporary Helm repository config for remote chart dependencies..."
      seeded=1
    fi
    helm repo add "${repo_name}" "${repo_url}" >/dev/null
  done < <(chart_dependency_remote_repositories "${chart_path}")
}

sync_chart_dependencies_and_validate() {
  local root="$1"
  local chart_dir="$2"
  local chart_rel="helm-charts/soperator"
  local chart_path="${root}/${chart_rel}"
  if [[ -z "${TMP_DIR:-}" ]]; then
    log_error "Internal error: temporary workspace is not initialized before Helm validation."
    exit 1
  fi

  local helm_state="${TMP_DIR}/helm-repositories"

  mkdir -p "${helm_state}/repository"

  (
    export HELM_REPOSITORY_CACHE="${helm_state}/repository"
    export HELM_REPOSITORY_CONFIG="${helm_state}/repositories.yaml"
    seed_chart_dependency_repositories "${chart_path}"

    if chart_metadata_changed "${root}"; then
      log_note "Regenerating Soperator Chart.lock from synced dependency metadata..."
      helm dependency update "${chart_path}" >/dev/null
    fi

    log_note "Validating synced Soperator chart..."
    helm dependency build "${chart_path}" >/dev/null
    helm lint --strict --with-subcharts "${chart_path}" >/dev/null
    helm template soperator-smoke "${chart_path}" --namespace soperator >/dev/null

    local example
    for example in "${chart_dir}"/examples/*-values.yaml; do
      [[ -f "${example}" ]] || continue
      helm template soperator-smoke "${chart_path}" --namespace soperator -f "${example}" >/dev/null
    done
  )
}

verify_imports() {
  ruby - "${@}" <<'RUBY'
require "digest"
require "fileutils"
require "json"
require "yaml"

lock_file, repo_root, upstream_root, scope, sync_flag, report_flag, release, commit = ARGV
sync = sync_flag == "1"
report = report_flag == "1"
def load_yaml_file(path)
  YAML.safe_load(File.read(path), aliases: true, permitted_classes: [Symbol])
end

lock = load_yaml_file(lock_file)

unless %w[scripts crds images all].include?(scope)
  warn "ERROR: --scope must be one of scripts, crds, images, or all."
  exit 2
end

def chart_yaml(path)
  load_yaml_file(path)
end

def package_version(release)
  "#{release}-ps.1"
end

def parent_package_version(release, chart)
  current_version = chart["version"].to_s
  if chart["appVersion"].to_s == release &&
      current_version.match?(/\A#{Regexp.escape(release)}-ps\.[1-9]\d*\z/)
    return current_version
  end

  package_version(release)
end

def normalize_rel(path)
  path = path.to_s.sub(%r{\A\./}, "").sub(%r{/\z}, "")
  raise "empty path is not allowed" if path.empty?
  raise "absolute path is not allowed: #{path}" if path.start_with?("/")
  parts = path.split("/")
  raise "parent directory traversal is not allowed: #{path}" if parts.include?("..")
  path
end

def overlap?(left, right)
  left = normalize_rel(left)
  right = normalize_rel(right)
  left == right || left.start_with?("#{right}/")
end

def safe_join(root, rel)
  File.expand_path(normalize_rel(rel), root)
end

def local_owned_paths(lock)
  Array(lock["local_owned_paths"]).map { |path| normalize_rel(path) }
end

def validate_exact_import_target!(lock, local_path, kind)
  target = normalize_rel(local_path)
  local_owned_paths(lock).each do |owned|
    raise "#{kind} sync target '#{target}' overlaps local-owned path '#{owned}'" if overlap?(target, owned)
  end
end

def validate_image_target!(lock, local_file)
  target = normalize_rel(local_file)
  owned = local_owned_paths(lock).any? { |path| overlap?(target, path) || overlap?(path, target) }
  return if owned

  raise "image sync target '#{target}' is not declared in local_owned_paths"
end

def selected?(entry, scope)
  scope == "all" || entry.fetch("scope") == scope
end

def digest_path(path)
  digest = Digest::SHA256.new
  if File.file?(path)
    digest.update(File.basename(path))
    digest.update("\0")
    digest.update(File.binread(path))
    return digest.hexdigest
  end
  raise "path not found: #{path}" unless File.directory?(path)
  Dir.glob(File.join(path, "**", "*"), File::FNM_DOTMATCH).select { |item| File.file?(item) }.sort.each do |file|
    relative = file.delete_prefix("#{path}/")
    digest.update(relative)
    digest.update("\0")
    digest.update(File.binread(file))
    digest.update("\0")
  end
  digest.hexdigest
end

def copy_exact_import(source, target)
  FileUtils.rm_rf(target)
  FileUtils.mkdir_p(target)
  if File.directory?(source)
    FileUtils.cp_r(Dir.glob(File.join(source, "*"), File::FNM_DOTMATCH).reject { |p| [".", ".."].include?(File.basename(p)) }, target)
  else
    FileUtils.mkdir_p(File.dirname(target))
    FileUtils.cp(source, target)
  end
end

def dig_path(data, path)
  Array(path).reduce(data) do |cursor, key|
    if cursor.is_a?(Array)
      cursor.fetch(Integer(key))
    elsif cursor.is_a?(Hash)
      cursor.fetch(key)
    else
      raise "cannot descend into #{cursor.class} with #{key.inspect}"
    end
  end
end

def verify_chart_versions!(repo_root, lock)
  chart = chart_yaml(File.join(repo_root, "helm-charts/soperator/Chart.yaml"))
  release = lock.fetch("release")
  app_version = chart.fetch("appVersion")
  chart_version = chart.fetch("version")

  raise "Chart.yaml appVersion '#{app_version}' does not match upstream release '#{release}'." unless app_version == release
  package_version_re = /\A#{Regexp.escape(release)}-ps\.[1-9]\d*\z/
  unless chart_version.match?(package_version_re)
    raise "Chart.yaml version '#{chart_version}' must match '#{release}-ps.N'."
  end
end

def verify_tracked_chart_app_version!(repo_root, upstream_root, entry, verify_chart_lock:)
  upstream_chart = chart_yaml(File.join(upstream_root, entry.fetch("upstream_file")))
  local_file = safe_join(repo_root, entry.fetch("local_file"))
  local_chart = chart_yaml(local_file)
  upstream_app_version = upstream_chart.fetch("appVersion")
  local_app_version = local_chart.fetch("appVersion")
  unless upstream_app_version == local_app_version
    raise "local appVersion #{local_app_version.inspect} does not match upstream #{upstream_app_version.inspect}"
  end

  if File.expand_path(local_file) == File.expand_path(parent_chart_file(repo_root))
    verify_parent_upstream_dependencies!(repo_root, upstream_chart, verify_chart_lock: verify_chart_lock)
    return
  end

  expected_package_version = "#{upstream_app_version}-ps.1"
  local_package_version = local_chart.fetch("version")
  unless local_package_version == expected_package_version
    raise "local chart version #{local_package_version.inspect} does not match expected package version #{expected_package_version.inspect}"
  end

  chart_name = local_chart.fetch("name")
  dependency = parent_dependency(repo_root, chart_name)
  raise "parent chart is missing dependency #{chart_name.inspect}" unless dependency
  unless dependency["version"] == expected_package_version
    raise "parent dependency #{chart_name} version #{dependency["version"].inspect} does not match expected #{expected_package_version.inspect}"
  end

  verify_chart_lock_dependency!(repo_root, chart_name, dependency) if verify_chart_lock
end

def report_line(status, kind, name, detail = nil)
  suffix = detail && !detail.empty? ? " - #{detail}" : ""
  puts format("%-7s %-7s %s%s", status, kind, name, suffix)
end

def yq_available?
  system("yq", "--version", out: File::NULL, err: File::NULL)
end

def yq_sync!(file, yq_path, value)
  raise "image sync requires yq v4 in PATH" unless yq_available?
  expression = "#{yq_path} = #{JSON.generate(value)}"
  ok = system("yq", "-i", expression, file)
  raise "failed to update #{file} at #{yq_path}" unless ok
end

def yq_update!(file, expression)
  raise "sync requires yq v4 in PATH" unless yq_available?
  ok = system("yq", "-i", expression, file)
  raise "failed to update #{file}" unless ok
end

def yq_set_string!(file, yq_path, value)
  yq_update!(file, "#{yq_path} = #{JSON.generate(value)}")
end

def parent_chart_file(repo_root)
  File.join(repo_root, "helm-charts/soperator/Chart.yaml")
end

def parent_chart_lock_file(repo_root)
  File.join(repo_root, "helm-charts/soperator/Chart.lock")
end

def sync_parent_chart_metadata!(repo_root, release, commit)
  file = parent_chart_file(repo_root)
  chart = chart_yaml(file)
  annotations = chart.fetch("annotations", {})
  expected_package_version = parent_package_version(release, chart)
  return if chart["appVersion"] == release &&
    chart["version"] == expected_package_version &&
    annotations["soperator.nebius.com/upstream-release"] == release &&
    annotations["soperator.nebius.com/upstream-commit"] == commit

  yq_update!(
    file,
    [
      ".version = #{JSON.generate(expected_package_version)}",
      ".appVersion = #{JSON.generate(release)}",
      ".annotations.\"soperator.nebius.com/upstream-release\" = #{JSON.generate(release)}",
      ".annotations.\"soperator.nebius.com/upstream-commit\" = #{JSON.generate(commit)}"
    ].join(" | ")
  )
end

def update_parent_dependency_version!(repo_root, dependency_name, dependency_version)
  yq_set_string!(parent_chart_file(repo_root), parent_dependency_field_path(dependency_name, "version"), dependency_version)
end

def upstream_parent_dependencies(upstream_chart)
  Array(upstream_chart["dependencies"]).reject { |dependency| dependency["repository"].to_s.start_with?("file://") }
end

def parent_dependency_field_path(dependency_name, field)
  "(.dependencies[] | select(.name == #{JSON.generate(dependency_name)}) | .#{field})"
end

def parent_dependency(repo_root, dependency_name)
  parent_chart = chart_yaml(parent_chart_file(repo_root))
  Array(parent_chart["dependencies"]).find { |item| item["name"] == dependency_name }
end

def parent_lock_dependency(repo_root, dependency_name)
  chart_lock = chart_yaml(parent_chart_lock_file(repo_root))
  Array(chart_lock["dependencies"]).find { |item| item["name"] == dependency_name }
end

def verify_chart_lock_dependency!(repo_root, dependency_name, dependency)
  lock_dependency = parent_lock_dependency(repo_root, dependency_name)
  raise "Chart.lock is missing dependency #{dependency_name.inspect}" unless lock_dependency

  %w[version repository].each do |field|
    next if lock_dependency[field] == dependency[field]

    raise "Chart.lock dependency #{dependency_name} #{field} #{lock_dependency[field].inspect} does not match Chart.yaml #{dependency[field].inspect}"
  end
end

def verify_parent_upstream_dependencies!(repo_root, upstream_chart, verify_chart_lock:)
  upstream_parent_dependencies(upstream_chart).each do |dependency|
    local_dependency = parent_dependency(repo_root, dependency.fetch("name"))
    raise "local parent chart is missing upstream dependency #{dependency.fetch("name")}" unless local_dependency

    %w[version repository].each do |field|
      next if local_dependency[field] == dependency[field]

      raise "local parent dependency #{dependency.fetch("name")} #{field} #{local_dependency[field].inspect} does not match upstream #{dependency[field].inspect}"
    end
    verify_chart_lock_dependency!(repo_root, dependency.fetch("name"), local_dependency) if verify_chart_lock
  end
end

def sync_parent_upstream_dependencies!(repo_root, upstream_chart)
  upstream_parent_dependencies(upstream_chart).each do |dependency|
    local_dependency = parent_dependency(repo_root, dependency.fetch("name"))
    raise "local parent chart is missing upstream dependency #{dependency.fetch("name")}" unless local_dependency

    updates = []
    unless local_dependency["version"] == dependency["version"]
      updates << "#{parent_dependency_field_path(dependency.fetch("name"), "version")} = #{JSON.generate(dependency["version"])}"
    end
    unless local_dependency["repository"] == dependency["repository"]
      updates << "#{parent_dependency_field_path(dependency.fetch("name"), "repository")} = #{JSON.generate(dependency["repository"])}"
    end
    next if updates.empty?

    yq_update!(parent_chart_file(repo_root), updates.join(" | "))
  end
end

def sync_tracked_chart_version!(repo_root, upstream_root, entry)
  upstream_chart = chart_yaml(File.join(upstream_root, entry.fetch("upstream_file")))
  local_file = safe_join(repo_root, entry.fetch("local_file"))
  local_chart = chart_yaml(local_file)
  upstream_app_version = upstream_chart.fetch("appVersion")
  annotations = local_chart.fetch("annotations", {})

  chart_updates = []
  chart_updates << ".appVersion = #{JSON.generate(upstream_app_version)}" unless local_chart["appVersion"] == upstream_app_version
  unless annotations["soperator.nebius.com/upstream-release"] == upstream_app_version
    chart_updates << ".annotations.\"soperator.nebius.com/upstream-release\" = #{JSON.generate(upstream_app_version)}"
  end
  yq_update!(local_file, chart_updates.join(" | ")) unless chart_updates.empty?

  if File.expand_path(local_file) == File.expand_path(parent_chart_file(repo_root))
    sync_parent_upstream_dependencies!(repo_root, upstream_chart)
    return
  end

  expected_package_version = package_version(upstream_app_version)
  chart_name = local_chart.fetch("name")
  yq_set_string!(local_file, ".version", expected_package_version) unless local_chart["version"] == expected_package_version

  parent_chart = chart_yaml(parent_chart_file(repo_root))
  dependency = Array(parent_chart["dependencies"]).find { |item| item["name"] == chart_name }
  update_parent_dependency_version!(repo_root, chart_name, expected_package_version) unless dependency && dependency["version"] == expected_package_version
end

failures = []

if sync && scope == "all"
  sync_parent_chart_metadata!(repo_root, release, commit)
end

begin
  verify_chart_versions!(repo_root, lock)
  report_line("ok", "lock", "chart package version and upstream appVersion") if report
rescue StandardError => e
  failures << e.message
  report_line("fail", "lock", "chart package version and upstream appVersion", e.message)
end

Array(lock.dig("imports", "chart_versions")).each do |entry|
  next unless scope == "all"
  name = entry.fetch("name")
  begin
    sync_tracked_chart_version!(repo_root, upstream_root, entry) if sync
    verify_tracked_chart_app_version!(repo_root, upstream_root, entry, verify_chart_lock: !sync)
    report_line("ok", "chart", name) if report
  rescue StandardError => e
    failures << "#{name}: #{e.message}"
    report_line("fail", "chart", name, e.message)
  end
end

Array(lock.dig("imports", "scripts")).each do |entry|
  next unless selected?(entry, scope)
  name = entry.fetch("name")
  begin
    raise "script import #{name} is not approved for --sync" if sync && !entry["sync"]
    validate_exact_import_target!(lock, entry.fetch("local_path"), "script")
    upstream_path = File.join(upstream_root, entry.fetch("upstream_path"))
    local_path = safe_join(repo_root, entry.fetch("local_path"))
    raise "upstream script import not found: #{entry.fetch("upstream_path")}" unless File.exist?(upstream_path)
    copy_exact_import(upstream_path, local_path) if sync
    upstream_digest = digest_path(upstream_path)
    local_digest = digest_path(local_path)
    if upstream_digest == local_digest
      report_line("ok", "script", name) if report
    else
      if report
        system("diff", "-ruN", "#{upstream_path}/", "#{local_path}/") if File.directory?(upstream_path) && File.directory?(local_path)
      end
      raise "local path #{entry.fetch("local_path")} does not match #{entry.fetch("upstream_path")}"
    end
  rescue StandardError => e
    failures << "#{name}: #{e.message}"
    report_line("fail", "script", name, e.message)
  end
end

Array(lock.dig("imports", "crds")).each do |entry|
  next unless selected?(entry, scope)
  name = entry.fetch("name")
  begin
    raise "CRD import #{name} is not approved for --sync" if sync && !entry["sync"]
    validate_exact_import_target!(lock, entry.fetch("local_path"), "CRD")
    upstream_path = File.join(upstream_root, entry.fetch("upstream_path"))
    local_path = safe_join(repo_root, entry.fetch("local_path"))
    raise "upstream CRD import not found: #{entry.fetch("upstream_path")}" unless File.exist?(upstream_path)
    copy_exact_import(upstream_path, local_path) if sync
    upstream_digest = digest_path(upstream_path)
    local_digest = digest_path(local_path)
    if upstream_digest == local_digest
      report_line("ok", "crd", name) if report
    else
      if report
        system("diff", "-ruN", "#{upstream_path}/", "#{local_path}/") if File.directory?(upstream_path) && File.directory?(local_path)
      end
      raise "local path #{entry.fetch("local_path")} does not match #{entry.fetch("upstream_path")}"
    end
  rescue StandardError => e
    failures << "#{name}: #{e.message}"
    report_line("fail", "crd", name, e.message)
  end
end

Array(lock.dig("imports", "images")).each do |entry|
  next unless selected?(entry, scope)
  name = entry.fetch("name")
  begin
    raise "image import #{name} is not approved for --sync" if sync && !entry["sync"]
    validate_image_target!(lock, entry.fetch("local_file"))
    upstream_file = File.join(upstream_root, entry.fetch("upstream_file"))
    local_file = safe_join(repo_root, entry.fetch("local_file"))
    upstream_value = dig_path(chart_yaml(upstream_file), entry.fetch("upstream_path"))
    local_value = dig_path(chart_yaml(local_file), entry.fetch("local_path"))
    if upstream_value != local_value && sync
      yq_sync!(local_file, entry.fetch("yq_path"), upstream_value)
      local_value = dig_path(chart_yaml(local_file), entry.fetch("local_path"))
    end
    if upstream_value == local_value
      report_line("ok", "image", name) if report
    else
      raise "local value #{local_value.inspect} does not match upstream #{upstream_value.inspect}"
    end
  rescue StandardError => e
    failures << "#{name}: #{e.message}"
    report_line("fail", "image", name, e.message)
  end
end

Array(lock.dig("imports", "review")).each_with_index do |entry, index|
  next unless scope == "all"
  name = entry.fetch("name")
  begin
    upstream_path = File.join(upstream_root, entry.fetch("upstream_path"))
    actual = digest_path(upstream_path)
    expected = entry.fetch("sha256")
    updated_hash = actual != expected && sync
    if updated_hash
      yq_set_string!(lock_file, ".imports.review[#{index}].sha256", actual)
      expected = actual
    end
    if actual == expected
      report_line("ok", "logic", name, updated_hash ? "updated review hash" : nil) if report
    else
      raise "tracked upstream logic changed; expected #{expected}, got #{actual}"
    end
  rescue StandardError => e
    failures << "#{name}: #{e.message}"
    report_line("fail", "logic", name, e.message)
  end
end

if failures.any?
  warn
  warn "Upstream import verification failed:"
  failures.each { |failure| warn "  - #{failure}" }
  exit 1
end

puts "Selected upstream imports are in sync."
RUBY
}

main() {
  init_output_style

  local check_latest=0
  local sync_latest=0
  local sync=0
  local report=0
  local scope="all"
  local chart_dir
  chart_dir="$(cd "$(script_dir)/.." >/dev/null 2>&1 && pwd)"
  local root
  root="$(repo_root "${chart_dir}")"
  local lock_file="${chart_dir}/upstream-soperator.lock.yaml"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --lock-file)
        if [[ $# -lt 2 ]]; then
          log_error "--lock-file requires a path."
          exit 1
        fi
        lock_file="$2"
        shift 2
        ;;
      --check-latest)
        check_latest=1
        shift
        ;;
      --sync)
        sync=1
        shift
        ;;
      --latest)
        sync_latest=1
        shift
        ;;
      --scope)
        if [[ $# -lt 2 ]]; then
          log_error "--scope requires scripts, crds, images, or all."
          exit 1
        fi
        scope="$2"
        shift 2
        ;;
      --scope=*)
        scope="${1#--scope=}"
        shift
        ;;
      --report)
        report=1
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
        log_error "Unexpected argument: $1"
        show_usage >&2
        exit 1
        ;;
    esac
  done

  case "${scope}" in
    scripts|crds|images|all) ;;
    *)
      log_error "--scope must be one of: scripts, crds, images, all"
      exit 1
      ;;
  esac

  if [[ "${sync_latest}" -eq 1 && "${sync}" -ne 1 ]]; then
    log_error "--latest updates the lock and must be used with --sync."
    exit 1
  fi

  if [[ "${check_latest}" -eq 1 && ( "${sync}" -eq 1 || "${sync_latest}" -eq 1 ) ]]; then
    log_error "--check-latest is read-only and cannot be combined with --sync or --latest."
    exit 1
  fi

  if [[ "${sync}" -eq 1 && "${scope}" != "all" ]]; then
    log_error "--sync must use --scope all under the single-release sync contract. Use --scope only for read-only verification."
    exit 1
  fi

  require_cmd "awk"
  require_cmd "curl"
  require_cmd "diff"
  require_cmd "git"
  require_cmd "mktemp"
  require_cmd "ruby"
  require_cmd "sed"
  require_cmd "tar"

  if [[ "${sync}" -eq 1 ]]; then
    require_cmd "helm"
    require_yq_v4
    ensure_clean_worktree "${root}"
  fi

  if [[ ! -f "${lock_file}" ]]; then
    log_error "Lock file not found: ${lock_file}"
    exit 1
  fi

  local repo release tag commit
  repo="$(yaml_value "${lock_file}" "repository")"
  release="$(yaml_value "${lock_file}" "release")"
  tag="$(yaml_value "${lock_file}" "tag")"
  commit="$(yaml_value "${lock_file}" "commit")"

  if [[ -z "${repo}" || -z "${release}" || -z "${tag}" || -z "${commit}" ]]; then
    log_error "Lock file is missing repository, release, tag, or commit."
    exit 1
  fi

  if [[ "${check_latest}" -eq 1 ]]; then
    local github_highest
    github_highest="$(highest_release_tag "${repo}")"
    if [[ -z "${github_highest}" ]]; then
      log_error "Could not determine highest GitHub SemVer release for ${repo}."
      exit 1
    fi
    local release_comparison
    if ! release_comparison="$(compare_releases "${release}" "${github_highest}")"; then
      log_error "Could not compare locked release '${release}' with highest GitHub SemVer release '${github_highest}'."
      exit 1
    fi
    if [[ "${release_comparison}" -lt 0 ]]; then
      log_error "Highest Soperator release is '${github_highest}', but lock is pinned to '${release}'. Run --latest --sync --report from a clean working tree."
      exit 1
    elif [[ "${release_comparison}" -gt 0 ]]; then
      log_error "Locked Soperator release '${release}' is newer than highest GitHub SemVer release '${github_highest}'. Check the lock for a typo or wait for GitHub release metadata before syncing."
      exit 1
    fi
    log_success "Pinned Soperator release '${release}' is the highest GitHub SemVer release."
    exit 0
  fi

  if [[ "${sync_latest}" -eq 1 ]]; then
    local github_highest
    github_highest="$(highest_release_tag "${repo}")"
    if [[ -z "${github_highest}" ]]; then
      log_error "Could not determine highest GitHub SemVer release for ${repo}."
      exit 1
    fi
    local release_comparison
    if ! release_comparison="$(compare_releases "${release}" "${github_highest}")"; then
      log_error "Could not compare locked release '${release}' with highest GitHub SemVer release '${github_highest}'."
      exit 1
    fi
    if [[ "${release_comparison}" -gt 0 ]]; then
      log_error "Locked Soperator release '${release}' is newer than highest GitHub SemVer release '${github_highest}'. Refusing --latest sync before mutating files; check the lock for a typo or wait for GitHub release metadata."
      exit 1
    fi
    release="${github_highest}"
    tag="${github_highest}"
  elif [[ "${sync}" -eq 1 ]]; then
    tag="${release}"
  fi

  if [[ "${sync}" -eq 1 ]]; then
    ensure_sync_branch "${root}" "${release}"
  fi

  local resolved_commit
  resolved_commit="$(resolve_tag_commit "${repo}" "${tag}")"
  if [[ "${sync}" -eq 1 ]]; then
    commit="${resolved_commit}"
    if [[ "$(yaml_value "${lock_file}" "release")" != "${release}" || "$(yaml_value "${lock_file}" "tag")" != "${tag}" || "$(yaml_value "${lock_file}" "commit")" != "${commit}" ]]; then
      update_lock_pin "${lock_file}" "${release}" "${tag}" "${commit}"
    fi
  elif [[ "${resolved_commit}" != "${commit}" ]]; then
    log_error "Tag '${tag}' resolves to '${resolved_commit}', but lock expects '${commit}'."
    exit 1
  fi

  TMP_DIR="$(mktemp -d)"
  trap cleanup EXIT

  log_note "Fetching ${repo} release ${tag}..."
  fetch_release "${repo}" "${tag}" "${TMP_DIR}"

  verify_imports "${lock_file}" "${root}" "${TMP_DIR}/source" "${scope}" "${sync}" "${report}" "${release}" "${commit}"
  if [[ "${sync}" -eq 1 ]]; then
    sync_chart_dependencies_and_validate "${root}" "${chart_dir}"
    if [[ "${report}" -eq 1 ]]; then
      report_changed_files "${root}"
    fi
  fi
  log_success "Upstream import verification completed for scope '${scope}'."
}

main "$@"
