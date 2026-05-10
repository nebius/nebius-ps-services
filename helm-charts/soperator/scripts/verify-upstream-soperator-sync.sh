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
  printf '%b\n' "  ${S_CYAN}scripts/verify-upstream-soperator-sync.sh${S_RESET} ${S_DIM}[options]${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}--lock-file PATH${S_RESET}    Lock file to read"
  printf '%b\n' "  ${S_YELLOW}--check-latest${S_RESET}      Fail if GitHub latest release is newer than the lock"
  printf '%b\n' "  ${S_YELLOW}--sync${S_RESET}              Apply approved exact file and image-value imports"
  printf '%b\n' "  ${S_YELLOW}--scope SCOPE${S_RESET}       Limit to scripts, images, or all (default: all)"
  printf '%b\n' "  ${S_YELLOW}--report${S_RESET}            Print import status for every selected item"
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}          Show help"
  printf '\n'
  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}scripts/verify-upstream-soperator-sync.sh --report${S_RESET}"
  printf '%b\n' "  ${S_CYAN}scripts/verify-upstream-soperator-sync.sh --scope images --report${S_RESET}"
  printf '%b\n' "  ${S_CYAN}scripts/verify-upstream-soperator-sync.sh --scope scripts --sync${S_RESET}"
  printf '%b\n' "  ${S_CYAN}scripts/verify-upstream-soperator-sync.sh --check-latest --report${S_RESET}"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "Required command not found: ${cmd}"
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
  ruby -ryaml -e 'data = YAML.load(File.read(ARGV[0])); value = data.fetch(ARGV[1]); puts value' "${file}" "${key}"
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

latest_release_tag() {
  local repo="$1"
  local api_repo
  api_repo="${repo#https://github.com/}"
  api_repo="${api_repo%.git}"
  curl -fsSL "https://api.github.com/repos/${api_repo}/releases/latest" \
    | sed -n 's/^[[:space:]]*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' \
    | head -n 1
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

verify_imports() {
  ruby - "${@}" <<'RUBY'
require "digest"
require "fileutils"
require "json"
require "yaml"

lock_file, repo_root, upstream_root, scope, sync_flag, report_flag = ARGV
sync = sync_flag == "1"
report = report_flag == "1"
lock = YAML.load(File.read(lock_file))

unless %w[scripts images all].include?(scope)
  warn "ERROR: --scope must be one of scripts, images, or all."
  exit 2
end

def chart_yaml(path)
  YAML.load(File.read(path))
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

def validate_exact_target!(lock, local_path)
  target = normalize_rel(local_path)
  local_owned_paths(lock).each do |owned|
    raise "sync target '#{target}' overlaps local-owned path '#{owned}'" if overlap?(target, owned)
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

def copy_exact(source, target)
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
  unless chart_version.match?(/\A\d+\.\d+\.\d+\z/)
    raise "Chart.yaml version '#{chart_version}' must be the chart package SemVer X.Y.Z. The upstream Soperator release belongs in appVersion."
  end
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

failures = []

begin
  verify_chart_versions!(repo_root, lock)
  report_line("ok", "lock", "chart SemVer and upstream appVersion") if report
rescue StandardError => e
  failures << e.message
  report_line("fail", "lock", "chart SemVer and upstream appVersion", e.message)
end

Array(lock.dig("imports", "exact")).each do |entry|
  next unless selected?(entry, scope)
  name = entry.fetch("name")
  begin
    raise "exact import #{name} is not approved for --sync" if sync && !entry["sync"]
    validate_exact_target!(lock, entry.fetch("local_path"))
    upstream_path = File.join(upstream_root, entry.fetch("upstream_path"))
    local_path = safe_join(repo_root, entry.fetch("local_path"))
    raise "upstream exact import not found: #{entry.fetch("upstream_path")}" unless File.exist?(upstream_path)
    copy_exact(upstream_path, local_path) if sync
    upstream_digest = digest_path(upstream_path)
    local_digest = digest_path(local_path)
    if upstream_digest == local_digest
      report_line("ok", "exact", name) if report
    else
      if report
        system("diff", "-ruN", "#{upstream_path}/", "#{local_path}/") if File.directory?(upstream_path) && File.directory?(local_path)
      end
      raise "local path #{entry.fetch("local_path")} does not match #{entry.fetch("upstream_path")}"
    end
  rescue StandardError => e
    failures << "#{name}: #{e.message}"
    report_line("fail", "exact", name, e.message)
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

Array(lock.dig("imports", "review")).each do |entry|
  next unless scope == "all"
  name = entry.fetch("name")
  begin
    upstream_path = File.join(upstream_root, entry.fetch("upstream_path"))
    actual = digest_path(upstream_path)
    expected = entry.fetch("sha256")
    if actual == expected
      report_line("ok", "logic", name) if report
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
      --scope)
        if [[ $# -lt 2 ]]; then
          log_error "--scope requires scripts, images, or all."
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
    scripts|images|all) ;;
    *)
      log_error "--scope must be one of: scripts, images, all"
      exit 1
      ;;
  esac

  require_cmd "awk"
  require_cmd "curl"
  require_cmd "diff"
  require_cmd "git"
  require_cmd "mktemp"
  require_cmd "ruby"
  require_cmd "tar"

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

  local resolved_commit
  resolved_commit="$(resolve_tag_commit "${repo}" "${tag}")"
  if [[ "${resolved_commit}" != "${commit}" ]]; then
    log_error "Tag '${tag}' resolves to '${resolved_commit}', but lock expects '${commit}'."
    exit 1
  fi

  if [[ "${check_latest}" -eq 1 ]]; then
    local latest
    latest="$(latest_release_tag "${repo}")"
    if [[ -z "${latest}" ]]; then
      log_error "Could not determine latest GitHub release for ${repo}."
      exit 1
    fi
    if [[ "${latest}" != "${release}" ]]; then
      log_error "Latest Soperator release is '${latest}', but lock is pinned to '${release}'. Create a feature branch, update the lock, run this verifier with --sync for approved imports, test, and open a PR."
      exit 1
    fi
    log_success "Pinned Soperator release '${release}' is the latest GitHub release."
  fi

  TMP_DIR="$(mktemp -d)"
  trap cleanup EXIT

  log_note "Fetching ${repo} release ${tag}..."
  fetch_release "${repo}" "${tag}" "${TMP_DIR}"

  verify_imports "${lock_file}" "${root}" "${TMP_DIR}/source" "${scope}" "${sync}" "${report}"
  log_success "Upstream import verification completed for scope '${scope}'."
}

main "$@"
