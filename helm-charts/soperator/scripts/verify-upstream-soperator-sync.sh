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
  printf '%b\n' "  ${S_YELLOW}--sync${S_RESET}              Validate a full sync in a temporary clone, then promote approved changes"
  printf '%b\n' "  ${S_YELLOW}--latest${S_RESET}            With --sync, use the highest GitHub SemVer release and update the lock"
  printf '%b\n' "  ${S_YELLOW}--accept-review-baseline${S_RESET}  With --sync, explicitly replace changed review-only hashes"
  printf '%b\n' "  ${S_YELLOW}--accept-image-baseline${S_RESET}   With --sync, explicitly accept reviewed image reference, index digest, and platform-digest changes"
  printf '%b\n' "  ${S_YELLOW}--live-upgrade-evidence PATH${S_RESET}  Attested disposable live-campaign JSON required for pin promotion or baseline acceptance"
  printf '%b\n' "  ${S_YELLOW}--ci-preview-no-branch${S_RESET}  With --sync, preview in disposable CI checkout without creating a local branch"
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
  printf '%b\n' "  ${S_DIM}After reviewing upstream logic and runtime images, sync the highest SemVer release and explicitly update both baselines:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --latest --sync --accept-review-baseline --accept-image-baseline --live-upgrade-evidence /path/to/soperator-disposable-upgrade-evidence.json --report${S_RESET}"
  printf '\n'
  printf '%b\n' "  ${S_DIM}Read-only scoped checks:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --scope scripts --report${S_RESET}"
  printf '%b\n' "  ${S_CYAN}helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --scope crds --report${S_RESET}"
  printf '%b\n' "  ${S_CYAN}helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --scope images --report${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_BOLD}Write-mode notes:${S_RESET}"
  printf '%b\n' "  ${S_DIM}--sync requires a clean working tree and always uses the full upstream release contract.${S_RESET}"
  printf '%b\n' "  ${S_DIM}When run from main, master, the default branch, or detached HEAD, it creates sync-soperator-<release>.${S_RESET}"
  printf '%b\n' "  ${S_DIM}--ci-preview-no-branch is only for disposable CI previews and keeps the current checkout branch unchanged.${S_RESET}"
  printf '%b\n' "  ${S_DIM}Review-only hashes never change unless --accept-review-baseline is present.${S_RESET}"
  printf '%b\n' "  ${S_DIM}Runtime image references, index digests, and platform digests never change unless --accept-image-baseline is present.${S_RESET}"
  printf '%b\n' "  ${S_DIM}A real pin promotion or either baseline-acceptance flag requires non-expired, attested evidence bound to the resolved commit and candidate manifest.${S_RESET}"
  printf '%b\n' "  ${S_DIM}A baseline-free --ci-preview-no-branch run may omit evidence because it cannot approve or persist a promotion.${S_RESET}"
  printf '%b\n' "  ${S_DIM}Local --sync validates in a temporary clone before promotion; it does not git-stage, commit, push, open a PR, or merge.${S_RESET}"
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
        docker) log_warn "Install Docker Desktop on macOS: https://docs.docker.com/desktop/setup/install/mac-install/" ;;
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
        docker) log_warn "Install Docker Engine with the Buildx plugin: https://docs.docker.com/engine/install/" ;;
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

require_docker_buildx() {
  require_cmd "docker"
  if ! docker buildx version >/dev/null 2>&1; then
    log_error "Required Docker Buildx plugin is unavailable; OCI digest and platform verification cannot run."
    print_install_hint "docker"
    exit 1
  fi
}

require_helm_unittest() {
  if ! helm plugin list 2>/dev/null | awk 'NR > 1 && $1 == "unittest" && $2 == "1.1.1" { found = 1 } END { exit(found ? 0 : 1) }'; then
    log_error "Required Helm unittest plugin v1.1.1 is unavailable."
    log_warn "Install: helm plugin install https://github.com/helm-unittest/helm-unittest --version v1.1.1"
    exit 1
  fi
}

validate_live_upgrade_evidence() {
  local evidence_path="$1"
  local target_release="$2"
  local target_chart_version="$3"
  local expected_repository="$4"
  local expected_workflow="$5"
  local repository_root="$6"
  local lock_file="$7"

  if [[ ! -f "${evidence_path}" || -L "${evidence_path}" ]]; then
    log_error "Disposable live upgrade evidence must be one regular, non-symlink JSON file: ${evidence_path}"
    return 1
  fi

  ruby -rjson -rtime -ryaml - "${evidence_path}" "${target_release}" "${target_chart_version}" "${expected_repository}" "${expected_workflow}" "${repository_root}" "${lock_file}" <<'RUBY'
path, target_release, target_chart_version, expected_repository, expected_workflow, repository_root, lock_file = ARGV

def mapping_at(value, label)
  raise "#{label} must be an object" unless value.is_a?(Hash)
  value
end

def require_exact(value, expected, label)
  raise "#{label} must be #{expected.inspect}" unless value == expected
end

def require_text(value, label)
  text = value.to_s
  raise "#{label} must be a non-empty string" if text.empty? || text != value
  text
end

def require_sha256(value, label)
  text = require_text(value, label)
  raise "#{label} must be one lowercase SHA-256 digest" unless text.match?(/\A[0-9a-f]{64}\z/)
end

def require_git_commit(value, label)
  text = require_text(value, label)
  unless text.match?(/\A(?:[0-9a-f]{40}|[0-9a-f]{64})\z/)
    raise "#{label} must be one full lowercase Git commit ID"
  end
end

begin
  raise "evidence file exceeds 1 MiB" if File.size(path) > 1_048_576
  document = JSON.parse(File.read(path))
  lock = YAML.safe_load(File.read(lock_file), aliases: true, permitted_classes: [Symbol])
  required_source = mapping_at(
    lock.dig("conformance", "live_upgrade_evidence", "required_source"),
    "lock conformance.live_upgrade_evidence.required_source"
  )
  root = mapping_at(document, "evidence")
  require_exact(
    root["schema"],
    "nebius-cxcli-soperator-disposable-upgrade-evidence/v1",
    "evidence.schema"
  )
  require_exact(root["status"], "passed", "evidence.status")
  require_exact(root["disposable"], true, "evidence.disposable")

  producer = mapping_at(root["producer"], "evidence.producer")
  repository = require_text(producer["repository"], "evidence.producer.repository")
  require_exact(repository, expected_repository, "evidence.producer.repository")
  producer_workflow = require_text(producer["workflow"], "evidence.producer.workflow")
  unless producer_workflow.match?(%r{\A\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\z})
    raise "evidence.producer.workflow must name one repository workflow file"
  end
  if producer_workflow == ".github/workflows/soperator-upstream-verifier.yml"
    raise "the upstream verifier workflow cannot produce its own upgrade evidence"
  end
  require_exact(producer_workflow, expected_workflow, "evidence.producer.workflow")
  unless File.file?(File.join(repository_root, producer_workflow))
    raise "evidence.producer.workflow does not exist in this repository"
  end
  run_id = producer["run_id"]
  raise "evidence.producer.run_id must be a positive integer" unless run_id.is_a?(Integer) && run_id.positive?
  run_attempt = producer["run_attempt"]
  unless run_attempt.is_a?(Integer) && run_attempt.positive?
    raise "evidence.producer.run_attempt must be a positive integer"
  end
  require_git_commit(producer["head_sha"], "evidence.producer.head_sha")

  source = mapping_at(root["source"], "evidence.source")
  require_exact(
    source["upstream_release"],
    required_source.fetch("upstream_release"),
    "evidence.source.upstream_release"
  )
  require_exact(
    source["chart_version"],
    required_source.fetch("chart_version"),
    "evidence.source.chart_version"
  )
  %w[tarball_sha256 contract_fingerprint runtime_images_sha256].each do |field|
    require_sha256(source[field], "evidence.source.#{field}")
    require_exact(
      source[field],
      required_source.fetch(field),
      "evidence.source.#{field}"
    )
  end
  target = mapping_at(root["target"], "evidence.target")
  require_exact(target["upstream_release"], target_release, "evidence.target.upstream_release")
  require_exact(target["chart_version"], target_chart_version, "evidence.target.chart_version")
  require_git_commit(target["upstream_commit"], "evidence.target.upstream_commit")
  require_sha256(
    target["candidate_manifest_sha256"],
    "evidence.target.candidate_manifest_sha256"
  )

  campaign = mapping_at(root["campaign"], "evidence.campaign")
  require_exact(
    campaign["schema"],
    "nebius-cxcli-ext-soperator-upgrade-campaign/v4",
    "evidence.campaign.schema"
  )
  require_exact(campaign["status"], "complete", "evidence.campaign.status")
  require_exact(campaign["pending_phase"], "none", "evidence.campaign.pending_phase")
  require_text(campaign["id"], "evidence.campaign.id")
  require_sha256(campaign["fingerprint"], "evidence.campaign.fingerprint")

  acceptance = mapping_at(root["acceptance"], "evidence.acceptance")
  running_jobs = mapping_at(acceptance["running_jobs"], "evidence.acceptance.running_jobs")
  %w[preserved same_job_ids same_allocations restarts_zero exit_code_zero].each do |key|
    require_exact(running_jobs[key], true, "evidence.acceptance.running_jobs.#{key}")
  end
  tui_actions = mapping_at(acceptance["tui_actions"], "evidence.acceptance.tui_actions")
  require_exact(tui_actions["all_terminal"], true, "evidence.acceptance.tui_actions.all_terminal")
  require_exact(tui_actions["indeterminate"], 0, "evidence.acceptance.tui_actions.indeterminate")
  ssh = mapping_at(acceptance["ssh"], "evidence.acceptance.ssh")
  require_exact(ssh["forced_disconnects"], 0, "evidence.acceptance.ssh.forced_disconnects")
  require_exact(ssh["login_endpoint_continuous"], true, "evidence.acceptance.ssh.login_endpoint_continuous")
  require_exact(ssh["voluntary_handoff"], true, "evidence.acceptance.ssh.voluntary_handoff")
  controller = mapping_at(acceptance["controller"], "evidence.acceptance.controller")
  require_exact(controller["slurmctld_processes"], 1, "evidence.acceptance.controller.slurmctld_processes")
  require_exact(controller["slurmctld_hosts"], 1, "evidence.acceptance.controller.slurmctld_hosts")
  require_exact(controller["controller_replicas"], 1, "evidence.acceptance.controller.controller_replicas")
  require_exact(controller["bridge_resources_absent"], true, "evidence.acceptance.controller.bridge_resources_absent")
  jail_gpu = mapping_at(acceptance["jail_gpu"], "evidence.acceptance.jail_gpu")
  require_exact(jail_gpu["passed"], true, "evidence.acceptance.jail_gpu.passed")

  artifacts = mapping_at(root["artifacts"], "evidence.artifacts")
  require_sha256(artifacts["report_sha256"], "evidence.artifacts.report_sha256")
  require_sha256(artifacts["checkpoint_sha256"], "evidence.artifacts.checkpoint_sha256")

  completed_at = Time.iso8601(require_text(root["completed_at"], "evidence.completed_at"))
  expires_at = Time.iso8601(require_text(root["expires_at"], "evidence.expires_at"))
  now = Time.now.utc
  raise "evidence.completed_at is in the future" if completed_at > now
  raise "evidence.expires_at must be later than completed_at" unless expires_at > completed_at
  raise "evidence has expired" unless expires_at > now
rescue JSON::ParserError => e
  warn "invalid JSON: #{e.message}"
  exit 1
rescue ArgumentError, KeyError, TypeError, RuntimeError => e
  warn e.message
  exit 1
end

puts "Disposable live upgrade evidence passed for upstream #{target_release}."
RUBY
}

verify_live_upgrade_evidence_commit() {
  local evidence_path="$1"
  local resolved_commit="$2"

  ruby -rjson - "${evidence_path}" "${resolved_commit}" <<'RUBY'
evidence_path, resolved_commit = ARGV
evidence = JSON.parse(File.read(evidence_path))
actual = evidence.fetch("target").fetch("upstream_commit")
unless actual == resolved_commit
  warn "evidence.target.upstream_commit #{actual.inspect} does not match resolved commit #{resolved_commit.inspect}"
  exit 1
end
RUBY
}

verify_live_upgrade_evidence_provenance() {
  local evidence_path="$1"
  local lock_file="$2"
  local producer_repository producer_workflow trusted_ref trusted_event
  local run_id run_attempt head_sha run_metadata signer_workflow
  producer_repository="$(yaml_path_value "${lock_file}" conformance live_upgrade_evidence producer_repository)"
  producer_workflow="$(yaml_path_value "${lock_file}" conformance live_upgrade_evidence producer_workflow)"
  trusted_ref="$(yaml_path_value "${lock_file}" conformance live_upgrade_evidence trusted_ref)"
  trusted_event="$(yaml_path_value "${lock_file}" conformance live_upgrade_evidence trusted_event)"
  run_id="$(ruby -rjson -e 'puts JSON.parse(File.read(ARGV[0])).fetch("producer").fetch("run_id")' "${evidence_path}")"
  run_attempt="$(ruby -rjson -e 'puts JSON.parse(File.read(ARGV[0])).fetch("producer").fetch("run_attempt")' "${evidence_path}")"
  head_sha="$(ruby -rjson -e 'puts JSON.parse(File.read(ARGV[0])).fetch("producer").fetch("head_sha")' "${evidence_path}")"
  signer_workflow="${producer_repository}/${producer_workflow}"

  require_cmd "gh"
  if ! gh attestation verify "${evidence_path}" \
    --repo "${producer_repository}" \
    --signer-workflow "${signer_workflow}" \
    --source-ref "${trusted_ref}" \
    --source-digest "${head_sha}" \
    --deny-self-hosted-runners >/dev/null; then
    log_error "Disposable live upgrade evidence has no valid trusted GitHub artifact attestation."
    return 1
  fi

  run_metadata="${TMP_DIR}/live-upgrade-evidence-run.json"
  if ! gh api "repos/${producer_repository}/actions/runs/${run_id}" >"${run_metadata}"; then
    log_error "Could not load the attested evidence producer workflow run."
    return 1
  fi
  ruby -rjson - \
    "${run_metadata}" \
    "${producer_repository}" \
    "${producer_workflow}" \
    "${trusted_ref}" \
    "${trusted_event}" \
    "${run_id}" \
    "${run_attempt}" \
    "${head_sha}" <<'RUBY'
metadata_file, repository, workflow, trusted_ref, trusted_event, run_id, run_attempt, head_sha = ARGV
run = JSON.parse(File.read(metadata_file))
expected_branch = trusted_ref.sub(%r{\Arefs/heads/}, "")
checks = {
  "conclusion" => [run["conclusion"], "success"],
  "repository" => [run.dig("head_repository", "full_name"), repository],
  "workflow" => [run["path"], workflow],
  "event" => [run["event"], trusted_event],
  "branch" => [run["head_branch"], expected_branch],
  "run_id" => [run["id"], Integer(run_id, 10)],
  "run_attempt" => [run["run_attempt"], Integer(run_attempt, 10)],
  "head_sha" => [run["head_sha"], head_sha],
}
failed = checks.select { |_, (actual, expected)| actual != expected }
unless failed.empty?
  warn "evidence producer workflow metadata does not match the locked attested provenance: #{failed.keys.join(", ")}"
  exit 1
end
RUBY
}

candidate_manifest_sha256() {
  local root="$1"
  local lock_file="$2"

  ruby -rdigest -rjson -rrubygems/package -ryaml -rzlib - "${root}" "${lock_file}" <<'RUBY'
root, lock_file = ARGV
root = File.expand_path(root)
lock_file = File.expand_path(lock_file)
lock = YAML.safe_load(File.read(lock_file), aliases: true, permitted_classes: [Symbol])
chart = YAML.safe_load(
  File.read(File.join(root, "helm-charts/soperator/Chart.yaml")),
  aliases: true,
  permitted_classes: [Symbol]
)
runtime_images = Array(lock.dig("imports", "runtime_images")).map do |entry|
  platform_digests = Hash[
    Hash(entry.fetch("platform_digests")).sort_by { |platform, _| platform.to_s }
  ]
  {
    "name" => entry.fetch("name"),
    "reference" => entry.fetch("reference"),
    "digest" => entry.fetch("digest"),
    "required_platforms" => Array(entry.fetch("required_platforms")).map(&:to_s).sort,
    "platform_digests" => platform_digests,
    "required_commands" => Array(entry.fetch("required_commands", [])).map(&:to_s).sort,
  }
end.sort_by { |entry| entry.fetch("name") }

def normalize_relative_path(root, path, label)
  expanded = File.expand_path(path, root)
  prefix = "#{root}/"
  unless expanded.start_with?(prefix)
    raise "#{label} escapes the candidate repository: #{path}"
  end
  relative = expanded.delete_prefix(prefix)
  if relative.empty? || relative == ".git" || relative.start_with?(".git/")
    raise "#{label} is not a valid candidate path: #{path}"
  end
  relative
end

def canonical_dependency_archive_sha256(path, relative)
  entries = {}
  Zlib::GzipReader.open(path) do |gzip|
    Gem::Package::TarReader.new(gzip) do |tar|
      tar.each do |entry|
        name = entry.full_name.to_s.sub(%r{\A\./}, "").sub(%r{/\z}, "")
        if name.empty? || name.start_with?("/") || name.split("/").include?("..")
          raise "dependency archive contains an unsafe path: #{relative}:#{entry.full_name}"
        end
        raise "dependency archive contains a duplicate path: #{relative}:#{name}" if entries.key?(name)

        if entry.file?
          entries[name] = {
            "type" => "file",
            "sha256" => Digest::SHA256.hexdigest(entry.read || ""),
            "executable" => (entry.header.mode & 0o111) != 0,
          }
        elsif entry.directory?
          entries[name] = {"type" => "directory"}
        else
          raise "dependency archive contains an unsupported entry type: #{relative}:#{name}"
        end
      end
    end
  end
  raise "dependency archive is empty: #{relative}" if entries.empty?

  Digest::SHA256.hexdigest(JSON.generate(entries.sort.to_h))
rescue Zlib::GzipFile::Error, Gem::Package::TarInvalidError => e
  raise "dependency archive is invalid: #{relative}: #{e.message}"
end

def canonical_json_value(value)
  case value
  when Hash
    value.keys.map(&:to_s).sort.to_h do |key|
      original_key = value.key?(key) ? key : value.keys.find { |candidate| candidate.to_s == key }
      [key, canonical_json_value(value.fetch(original_key))]
    end
  when Array
    value.map { |item| canonical_json_value(item) }
  else
    value
  end
end

def canonical_helm_lock_sha256(path)
  document = YAML.safe_load(File.read(path), aliases: true, permitted_classes: [Symbol])
  raise "Helm dependency lock must be an object" unless document.is_a?(Hash)
  document = document.dup
  document.delete("generated")
  document.delete(:generated)
  Digest::SHA256.hexdigest(JSON.generate(canonical_json_value(document)))
end

def candidate_file_record(path, relative)
  dependency_archive = relative.start_with?("helm-charts/soperator/charts/") && relative.end_with?(".tgz")
  if dependency_archive
    return {
      "kind" => "canonical-helm-dependency-archive-v1",
      "sha256" => canonical_dependency_archive_sha256(path, relative),
      "executable" => false,
    }
  end
  if relative == "helm-charts/soperator/Chart.lock"
    return {
      "kind" => "canonical-helm-dependency-lock-v1",
      "sha256" => canonical_helm_lock_sha256(path),
      "executable" => false,
    }
  end

  {
    "kind" => "file",
    "sha256" => Digest::SHA256.file(path).hexdigest,
    "executable" => (File.stat(path).mode & 0o111) != 0,
  }
end

candidate_roots = ["helm-charts/soperator"]
queue = candidate_roots.dup
visited_charts = {}
until queue.empty?
  chart_root = queue.shift
  next if visited_charts[chart_root]

  visited_charts[chart_root] = true
  chart_file = File.join(root, chart_root, "Chart.yaml")
  raise "candidate chart is missing Chart.yaml: #{chart_root}" unless File.file?(chart_file)
  chart_document = YAML.safe_load(File.read(chart_file), aliases: true, permitted_classes: [Symbol])
  Array(chart_document["dependencies"]).each do |dependency|
    repository = dependency["repository"].to_s
    next unless repository.start_with?("file://")

    dependency_root = normalize_relative_path(
      root,
      File.expand_path(repository.delete_prefix("file://"), File.join(root, chart_root)),
      "local chart dependency"
    )
    candidate_roots << dependency_root
    queue << dependency_root
  end
end

Array(lock["exact_sync_targets"]).each do |path|
  candidate_roots << normalize_relative_path(root, path.to_s, "exact sync target")
end

candidate_files = [normalize_relative_path(root, lock_file, "upstream lock")]
Array(lock.dig("imports", "chart_versions")).each do |entry|
  candidate_files << normalize_relative_path(root, entry.fetch("local_file"), "chart version target")
end
Array(lock.dig("imports", "images")).each do |entry|
  candidate_files << normalize_relative_path(root, entry.fetch("local_file"), "image target")
end

inventory = {}
candidate_roots.uniq.sort.each do |relative_root|
  path = File.join(root, relative_root)
  raise "candidate root does not exist: #{relative_root}" unless File.directory?(path)
  raise "candidate root must not be a symlink: #{relative_root}" if File.symlink?(path)

  Dir.glob(File.join(path, "**", "*"), File::FNM_DOTMATCH).sort.each do |item|
    next if [".", ".."].include?(File.basename(item))
    relative = normalize_relative_path(root, item, "candidate inventory")
    raise "candidate inventory contains a symlink: #{relative}" if File.symlink?(item)
    next unless File.file?(item)

    inventory[relative] = candidate_file_record(item, relative)
  end
end
candidate_files.uniq.sort.each do |relative|
  path = File.join(root, relative)
  raise "candidate file does not exist: #{relative}" unless File.file?(path)
  raise "candidate file must not be a symlink: #{relative}" if File.symlink?(path)
  inventory[relative] = candidate_file_record(path, relative)
end

manifest = {
  "schema" => "nebius-cxcli-soperator-candidate-manifest/v2",
  "upstream_release" => lock.fetch("release"),
  "upstream_commit" => lock.fetch("commit"),
  "chart_version" => chart.fetch("version"),
  "runtime_images" => runtime_images,
  "files" => inventory.sort.to_h,
}
puts Digest::SHA256.hexdigest(JSON.generate(manifest))
RUBY
}

verify_live_upgrade_candidate_manifest() {
  local evidence_path="$1"
  local root="$2"
  local lock_file="$3"
  local expected actual
  expected="$(ruby -rjson -e 'puts JSON.parse(File.read(ARGV[0])).fetch("target").fetch("candidate_manifest_sha256")' "${evidence_path}")"
  actual="$(candidate_manifest_sha256 "${root}" "${lock_file}")"
  if [[ "${actual}" != "${expected}" ]]; then
    log_error "Disposable live upgrade evidence candidate manifest does not match the post-validation chart/dependency/import tree."
    return 1
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

yaml_path_value() {
  local file="$1"
  shift
  ruby -ryaml -e '
value = YAML.safe_load(File.read(ARGV.shift), aliases: true, permitted_classes: [Symbol])
ARGV.each do |key|
  value = value.is_a?(Hash) ? value[key] : nil
end
puts value unless value.nil?
' "${file}" "$@"
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

repo_relative_file() {
  local root="$1"
  local file="$2"
  ruby -e '
root = File.expand_path(ARGV.fetch(0))
file = File.expand_path(ARGV.fetch(1))
prefix = "#{root}/"
unless file.start_with?(prefix)
  warn "sync lock must be inside the repository: #{file}"
  exit 1
end
relative = file.delete_prefix(prefix)
if relative.empty? || relative == ".git" || relative.start_with?(".git/")
  warn "invalid repository-relative sync lock path: #{relative.inspect}"
  exit 1
end
puts relative
' "${root}" "${file}"
}

create_staged_repo() {
  local root="$1"
  local destination="$2"
  log_note "Creating isolated sync staging checkout..."
  git clone --quiet --no-hardlinks "${root}" "${destination}"
  if [[ "$(git -C "${destination}" rev-parse HEAD)" != "$(git -C "${root}" rev-parse HEAD)" ]]; then
    log_error "Staged checkout does not match the current repository HEAD."
    exit 1
  fi
}

promote_staged_sync() {
  local staged_root="$1"
  local root="$2"
  local lock_rel="$3"
  ruby - "${staged_root}" "${root}" "${lock_rel}" <<'RUBY'
require "fileutils"
require "open3"
require "yaml"

staged_root, repo_root, lock_rel = ARGV

def load_yaml(path)
  YAML.safe_load(File.read(path), aliases: true, permitted_classes: [Symbol])
end

def normalize_rel(path)
  value = path.to_s.sub(%r{\A\./}, "").sub(%r{/\z}, "")
  raise "empty promotion path is not allowed" if value.empty?
  raise "absolute promotion path is not allowed: #{value}" if value.start_with?("/")
  parts = value.split("/")
  raise "parent directory traversal is not allowed: #{value}" if parts.include?("..")
  raise "repository metadata cannot be promoted: #{value}" if value == ".git" || value.start_with?(".git/")
  value
end

def command_paths(root, *arguments)
  output, error, status = Open3.capture3("git", "-C", root, *arguments)
  raise "git promotion inventory failed: #{error.strip}" unless status.success?
  output.split("\0").reject(&:empty?).map { |path| normalize_rel(path) }
end

def path_within?(path, root)
  path == root || path.start_with?("#{root}/")
end

def reject_symlink_components!(root, rel, allow_missing_leaf:)
  cursor = File.expand_path(root)
  parts = normalize_rel(rel).split("/")
  parts.each_with_index do |part, index|
    cursor = File.join(cursor, part)
    next unless File.exist?(cursor) || File.symlink?(cursor)
    if File.symlink?(cursor)
      raise "promotion path contains a symlink: #{rel}"
    end
    if index == parts.length - 1 && !allow_missing_leaf && !File.exist?(cursor)
      raise "promotion source does not exist: #{rel}"
    end
  end
end

def atomic_copy_file(source, target)
  FileUtils.mkdir_p(File.dirname(target))
  temporary = File.join(File.dirname(target), ".#{File.basename(target)}.upstream-sync-#{Process.pid}")
  FileUtils.rm_f(temporary)
  FileUtils.cp(source, temporary, preserve: true)
  File.rename(temporary, target)
ensure
  FileUtils.rm_f(temporary) if defined?(temporary) && temporary
end

def atomic_replace_tree(source, target)
  parent = File.dirname(target)
  basename = File.basename(target)
  temporary = File.join(parent, ".#{basename}.upstream-sync-new-#{Process.pid}")
  backup = File.join(parent, ".#{basename}.upstream-sync-old-#{Process.pid}")
  FileUtils.rm_rf(temporary)
  FileUtils.rm_rf(backup)
  FileUtils.mkdir_p(parent)
  FileUtils.cp_r(source, temporary, preserve: true)
  File.rename(target, backup) if File.exist?(target)
  begin
    File.rename(temporary, target)
  rescue StandardError
    File.rename(backup, target) if File.exist?(backup) && !File.exist?(target)
    raise
  end
  FileUtils.rm_rf(backup)
ensure
  FileUtils.rm_rf(temporary) if defined?(temporary) && temporary
end

lock = load_yaml(File.join(staged_root, normalize_rel(lock_rel)))
exact_roots = Array(lock["exact_sync_targets"]).map { |path| normalize_rel(path) }
file_targets = [normalize_rel(lock_rel), "helm-charts/soperator/Chart.lock"]
Array(lock.dig("imports", "chart_versions")).each do |entry|
  file_targets << normalize_rel(entry.fetch("local_file"))
end
Array(lock.dig("imports", "images")).each do |entry|
  file_targets << normalize_rel(entry.fetch("local_file"))
end
file_targets.uniq!

changed = command_paths(staged_root, "diff", "--name-only", "-z", "--no-renames", "HEAD", "--")
changed.concat(command_paths(staged_root, "ls-files", "--others", "--exclude-standard", "-z"))
changed.uniq!

changed.each do |path|
  allowed = file_targets.include?(path) || exact_roots.any? { |root_path| path_within?(path, root_path) }
  raise "staged sync changed non-promotable path '#{path}'" unless allowed
end

changed_exact_roots = exact_roots.select do |root_path|
  changed.any? { |path| path_within?(path, root_path) }
end
changed_files = changed.select do |path|
  file_targets.include?(path) && changed_exact_roots.none? { |root_path| path_within?(path, root_path) }
end

# Validate the complete promotion inventory before the first real-worktree mutation.
changed_exact_roots.each do |path|
  reject_symlink_components!(staged_root, path, allow_missing_leaf: false)
  reject_symlink_components!(repo_root, path, allow_missing_leaf: true)
  raise "staged exact-sync tree is missing: #{path}" unless File.directory?(File.join(staged_root, path))
end
changed_files.each do |path|
  reject_symlink_components!(staged_root, path, allow_missing_leaf: true)
  reject_symlink_components!(repo_root, path, allow_missing_leaf: true)
end

changed_exact_roots.each do |path|
  atomic_replace_tree(File.join(staged_root, path), File.join(repo_root, path))
end
changed_files.each do |path|
  source = File.join(staged_root, path)
  target = File.join(repo_root, path)
  if File.file?(source)
    atomic_copy_file(source, target)
  elsif !File.exist?(source)
    FileUtils.rm_f(target)
  else
    raise "promotable file path is not a regular file: #{path}"
  end
end

puts "Promoted #{changed_exact_roots.length} exact tree(s) and #{changed_files.length} file(s) from validated staging."
RUBY
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
  local commit="$2"
  local destination="$3"
  local archive="${destination}/source.tar.gz"
  local url="${repo%/}/archive/${commit}.tar.gz"

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
  local update_dependencies="${3:-0}"
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

    if [[ "${update_dependencies}" -eq 1 ]] && chart_metadata_changed "${root}"; then
      log_note "Regenerating Soperator Chart.lock from synced dependency metadata..."
      helm dependency update "${chart_path}" >/dev/null
    fi

    log_note "Validating synced Soperator chart..."
    helm dependency build "${chart_path}" >/dev/null
    helm lint --strict --with-subcharts "${chart_path}" >/dev/null
    helm template soperator-smoke "${chart_path}" --namespace soperator >/dev/null
    verify_cxcli_rendered_contract "${root}" "${chart_path}"
    local unittest_log="${TMP_DIR}/helm-unittest.log"
    if ! helm unittest --strict --with-subchart=false "${chart_path}" >"${unittest_log}" 2>&1; then
      cat "${unittest_log}" >&2
      log_error "Soperator Helm unit tests failed."
      exit 1
    fi

    local example
    for example in "${chart_dir}"/examples/*-values.yaml; do
      [[ -f "${example}" ]] || continue
      helm template soperator-smoke "${chart_path}" --namespace soperator -f "${example}" >/dev/null
    done
  )
}

create_chart_validation_workspace() {
  local root="$1"
  local destination="$2"

  mkdir -p "${destination}"
  tar -C "${root}" -cf - helm-charts | tar -C "${destination}" -xf -
}

verify_cxcli_rendered_contract() {
  local root="$1"
  local chart_path="$2"
  local lock_file="${root}/helm-charts/soperator/upstream-soperator.lock.yaml"
  local render_template rendered_file
  render_template="$(ruby -ryaml -e '
lock = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: true, permitted_classes: [Symbol])
template = lock.dig("conformance", "cxcli_values", "render_template").to_s
raise "cxcli render_template must be a non-empty relative path" if template.empty? || template.start_with?("/") || template.split("/").include?("..")
puts template
' "${lock_file}")"
  rendered_file="${TMP_DIR}/cxcli-values-contract-rendered.yaml"

  helm template cxcli-contract "${chart_path}" \
    --namespace soperator \
    --show-only "${render_template}" >"${rendered_file}"

  ruby -ryaml - "${lock_file}" "${rendered_file}" <<'RUBY'
lock_file, rendered_file = ARGV
lock = YAML.safe_load(File.read(lock_file), aliases: true, permitted_classes: [Symbol])
contract = lock.fetch("conformance").fetch("cxcli_values")
documents = File.read(rendered_file).split(/^---[ \t]*\r?$\n?/).each_with_object([]) do |document, result|
  next if document.strip.empty?
  value = YAML.safe_load(document, aliases: true, permitted_classes: [Symbol])
  result << value unless value.nil?
end
clusters = documents.select { |document| document.is_a?(Hash) && document["kind"] == "SlurmCluster" }
raise "cxcli contract render must contain exactly one SlurmCluster" unless clusters.length == 1
cluster = clusters.fetch(0)

def value_at_path(root, path)
  path.reduce(root) do |cursor, key|
    unless cursor.is_a?(Hash) && cursor.key?(key.to_s)
      raise "rendered cxcli contract is missing #{Array(path).join(".")}"
    end
    cursor.fetch(key.to_s)
  end
end

paths = Array(contract.fetch("rendered_required_paths"))
raise "cxcli rendered_required_paths must not be empty" if paths.empty?
labels = paths.map { |path| Array(path).map(&:to_s).join(".") }
raise "cxcli rendered conformance contains duplicate paths" unless labels.uniq.length == labels.length
paths.each { |path| value_at_path(cluster, path) }

controller = value_at_path(cluster, %w[spec slurmNodes controller])
raise "rendered cxcli controller must be an object" unless controller.is_a?(Hash)
forbidden = Array(contract.fetch("forbidden_controller_fields")).map(&:to_s)
present = forbidden.select { |field| controller.key?(field) }
unless present.empty?
  raise "rendered cxcli controller exposes unsupported HA/replica fields: #{present.join(", ")}"
end

puts "Rendered cxcli singleton controller contract passed."
RUBY
}

verify_imports() {
  ruby - "${@}" <<'RUBY'
require "digest"
require "fileutils"
require "json"
require "open3"
require "tmpdir"
require "yaml"

lock_file, repo_root, upstream_root, scope, sync_flag, report_flag, release, commit,
  accept_review_baseline_flag, accept_image_baseline_flag = ARGV
sync = sync_flag == "1"
report = report_flag == "1"
accept_review_baseline = accept_review_baseline_flag == "1"
accept_image_baseline = accept_image_baseline_flag == "1"
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

def contains_path?(parent, child)
  parent = normalize_rel(parent)
  child = normalize_rel(child)
  parent == child || child.start_with?("#{parent}/")
end

def overlap?(left, right)
  left = normalize_rel(left)
  right = normalize_rel(right)
  contains_path?(left, right) || contains_path?(right, left)
end

def safe_join(root, rel)
  expanded_root = File.expand_path(root)
  expanded = File.expand_path(normalize_rel(rel), expanded_root)
  prefix = "#{expanded_root}/"
  raise "path escapes root: #{rel}" unless expanded.start_with?(prefix)
  expanded
end

def local_owned_paths(lock)
  Array(lock["local_owned_paths"]).map { |path| normalize_rel(path) }
end

def exact_sync_targets(lock)
  Array(lock["exact_sync_targets"]).map { |path| normalize_rel(path) }
end

def validate_exact_import_target!(lock, local_path, kind)
  target = normalize_rel(local_path)
  unless exact_sync_targets(lock).include?(target)
    raise "#{kind} sync target '#{target}' is not an exact_sync_targets allowlist entry"
  end
  local_owned_paths(lock).each do |owned|
    raise "#{kind} sync target '#{target}' overlaps local-owned path '#{owned}'" if overlap?(target, owned)
  end
end

def validate_image_target!(lock, local_file)
  target = normalize_rel(local_file)
  owned = local_owned_paths(lock).include?(target)
  return if owned

  raise "image sync target '#{target}' is not an exact local_owned_paths entry"
end

def validate_lock_target_contract!(lock)
  targets = exact_sync_targets(lock)
  raise "exact_sync_targets must not be empty" if targets.empty?
  raise "exact_sync_targets contains duplicate entries" unless targets.uniq.length == targets.length

  targets.combination(2).each do |left, right|
    raise "exact sync targets overlap: '#{left}' and '#{right}'" if overlap?(left, right)
  end

  imported_targets = []
  %w[scripts crds].each do |kind|
    Array(lock.dig("imports", kind)).each do |entry|
      target = normalize_rel(entry.fetch("local_path"))
      validate_exact_import_target!(lock, target, kind)
      imported_targets << target
    end
  end
  unless imported_targets.sort == targets.sort
    raise "exact_sync_targets must exactly equal the script and CRD import targets"
  end

  Array(lock.dig("imports", "images")).each do |entry|
    validate_image_target!(lock, entry.fetch("local_file"))
  end
end

def reject_symlink_components!(root, rel)
  cursor = File.expand_path(root)
  normalize_rel(rel).split("/").each do |part|
    cursor = File.join(cursor, part)
    next unless File.exist?(cursor) || File.symlink?(cursor)
    raise "exact sync target contains a symlink: #{rel}" if File.symlink?(cursor)
  end
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

def copy_exact_import(source, target, lock:, target_rel:, kind:, repo_root:)
  validate_exact_import_target!(lock, target_rel, kind)
  reject_symlink_components!(repo_root, target_rel)
  raise "exact import source is not a directory: #{source}" unless File.directory?(source)
  FileUtils.rm_rf(target)
  FileUtils.mkdir_p(target)
  FileUtils.cp_r(Dir.glob(File.join(source, "*"), File::FNM_DOTMATCH).reject { |p| [".", ".."].include?(File.basename(p)) }, target)
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

def path_label(path)
  Array(path).map(&:to_s).join(".")
end

def schema_value_at_path(schema, path)
  Array(path).reduce(schema) do |cursor, key|
    properties = cursor.is_a?(Hash) ? cursor["properties"] : nil
    raise "schema has no explicit properties at #{path_label(path)}" unless properties.is_a?(Hash)
    properties.fetch(key.to_s)
  end
end

def verify_cxcli_values_surface!(repo_root, lock)
  contract = lock.fetch("conformance").fetch("cxcli_values")
  values_file = safe_join(repo_root, contract.fetch("values_file"))
  schema_file = safe_join(repo_root, contract.fetch("schema_file"))
  values = chart_yaml(values_file)
  schema = JSON.parse(File.read(schema_file))
  paths = Array(contract.fetch("required_paths"))
  raise "cxcli values conformance required_paths must not be empty" if paths.empty?
  labels = paths.map { |path| path_label(path) }
  raise "cxcli values conformance contains duplicate paths" unless labels.uniq.length == labels.length

  paths.each do |path|
    dig_path(values, path)
    schema_value_at_path(schema, path)
  end

  render_template = contract.fetch("render_template").to_s
  if render_template.empty? || render_template.start_with?("/") || render_template.split("/").include?("..")
    raise "cxcli render_template must be a non-empty relative path"
  end
  render_file = safe_join(repo_root, File.join("helm-charts/soperator", render_template))
  raise "cxcli render_template does not exist: #{render_template}" unless File.file?(render_file)

  rendered_paths = Array(contract.fetch("rendered_required_paths"))
  raise "cxcli rendered_required_paths must not be empty" if rendered_paths.empty?
  rendered_labels = rendered_paths.map { |path| path_label(path) }
  unless rendered_labels.uniq.length == rendered_labels.length
    raise "cxcli rendered conformance contains duplicate paths"
  end

  forbidden = Array(contract.fetch("forbidden_controller_fields")).map(&:to_s)
  raise "cxcli forbidden_controller_fields must not be empty" if forbidden.empty?
  unless forbidden.uniq.length == forbidden.length
    raise "cxcli forbidden_controller_fields contains duplicates"
  end
  values_controller = dig_path(values, %w[slurmNodes controller])
  schema_controller = schema_value_at_path(schema, %w[slurmNodes controller])
  schema_properties = schema_controller.fetch("properties", {})
  forbidden.each do |field|
    raise "cxcli values expose unsupported controller field #{field}" if values_controller.key?(field)
    if schema_properties.is_a?(Hash) && schema_properties.key?(field)
      raise "cxcli values schema exposes unsupported controller field #{field}"
    end
  end
end

def verify_live_upgrade_evidence_contract!(repo_root, lock)
  contract = lock.fetch("conformance").fetch("live_upgrade_evidence")
  repository = contract.fetch("producer_repository").to_s
  unless repository.match?(%r{\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\z})
    raise "live evidence producer_repository must be owner/repository"
  end
  workflow = contract["producer_workflow"]
  unless workflow.nil?
    unless workflow.is_a?(String) && workflow.match?(%r{\A\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\z})
      raise "live evidence producer_workflow must be null or one repository workflow path"
    end
    if workflow == ".github/workflows/soperator-upstream-verifier.yml"
      raise "the upstream verifier cannot be its own live evidence producer"
    end
    raise "configured live evidence producer workflow does not exist" unless File.file?(safe_join(repo_root, workflow))
  end
  unless contract.fetch("trusted_ref") == "refs/heads/main"
    raise "live evidence trusted_ref must remain refs/heads/main"
  end
  unless contract.fetch("trusted_event") == "workflow_dispatch"
    raise "live evidence trusted_event must remain workflow_dispatch"
  end
  unless contract.fetch("artifact_name") == "soperator-disposable-upgrade-evidence"
    raise "live evidence artifact_name must remain soperator-disposable-upgrade-evidence"
  end

  required_source = contract.fetch("required_source")
  source_release = required_source.fetch("upstream_release").to_s
  source_chart_version = required_source.fetch("chart_version").to_s
  raise "live evidence required source release must not be empty" if source_release.empty?
  raise "live evidence required source chart version must not be empty" if source_chart_version.empty?
  %w[tarball_sha256 contract_fingerprint runtime_images_sha256].each do |field|
    value = required_source.fetch(field).to_s
    unless value.match?(/\A[0-9a-f]{64}\z/)
      raise "live evidence required source #{field} must be one lowercase SHA-256 digest"
    end
  end

  profile_file = safe_join(repo_root, required_source.fetch("profile_file"))
  raise "live evidence required source profile does not exist" unless File.file?(profile_file)
  profile_document = load_yaml_file(profile_file)
  source_profile = Array(profile_document.fetch("releases")).find do |entry|
    entry.fetch("version").to_s == source_release
  end
  raise "live evidence required source release is absent from the cxcli migration profiles" unless source_profile

  source_checks = {
    "upstream release" => [source_profile.fetch("upstream_tag").to_s, source_release],
    "chart version" => [source_profile.fetch("chart_version").to_s, source_chart_version],
    "tarball digest" => [source_profile.fetch("source_tarball_sha256").to_s, required_source.fetch("tarball_sha256").to_s],
    "contract fingerprint" => [source_profile.fetch("contract_fingerprint").to_s, required_source.fetch("contract_fingerprint").to_s],
  }
  source_runtime = Array(source_profile.fetch("component_contracts")).find do |entry|
    entry.fetch("id").to_s == "slurm-cluster"
  end
  raise "live evidence required source has no slurm-cluster runtime profile" unless source_runtime
  source_checks["runtime image digest"] = [
    source_runtime.fetch("images").fetch("sha256").to_s,
    required_source.fetch("runtime_images_sha256").to_s,
  ]
  mismatches = source_checks.select { |_, (actual, expected)| actual != expected }
  unless mismatches.empty?
    raise "live evidence required source does not match the cxcli migration profile: #{mismatches.keys.join(", ")}"
  end
end

def files_below(path)
  Dir.glob(File.join(path, "**", "*"), File::FNM_DOTMATCH)
    .select { |item| File.file?(item) }
    .map { |item| item.delete_prefix("#{path}/") }
    .sort
end

def verify_slurm_script_consumption!(repo_root, entry)
  scripts_path = safe_join(repo_root, entry.fetch("local_path"))
  values = chart_yaml(safe_join(repo_root, entry.fetch("consumer_values_file")))
  configmap_file = safe_join(repo_root, entry.fetch("consumer_template_file"))
  mount_files = Array(entry.fetch("consumer_mount_files")).map { |path| safe_join(repo_root, path) }
  configmap = File.read(configmap_file)
  base_match = configmap.match(/range\s+\$name\s*:=\s*list\s+([^\n]+)/)
  raise "Slurm script ConfigMap has no explicit base script list" unless base_match
  base_names = base_match[1].scan(/"([^"]+)"/).flatten
  built_in = values.fetch("slurmScripts").fetch("builtIn")
  built_in_names = built_in.keys.map(&:to_s)
  files = files_below(scripts_path)
  executables = files.reject { |path| path.end_with?(".json") }
  configs = files.select { |path| path.end_with?(".json") }

  unregistered = executables.reject { |path| base_names.include?(path) || built_in_names.include?(path) }
  raise "imported Slurm scripts are not renderable: #{unregistered.join(", ")}" unless unregistered.empty?
  orphan_configs = configs.reject { |path| built_in_names.include?(path.delete_suffix(".json")) }
  raise "imported Slurm script configs are not renderable: #{orphan_configs.join(", ")}" unless orphan_configs.empty?
  raise "Slurm script ConfigMap no longer reads packaged slurm_scripts" unless configmap.include?("slurm_scripts/%s")
  mount_text = mount_files.map { |path| File.read(path) }.join("\n")
  unless mount_text.include?("/opt/slurm_scripts/") && mount_text.include?("slurm-scripts")
    raise "rendered Slurm scripts are no longer mounted at /opt/slurm_scripts/"
  end
end

def verify_activechecks_script_consumption!(repo_root, entry)
  scripts_path = safe_join(repo_root, entry.fetch("local_path"))
  consumer_files = Array(entry.fetch("consumer_files")).flat_map do |path|
    expanded = safe_join(repo_root, path)
    File.directory?(expanded) ? files_below(expanded).map { |relative| File.join(expanded, relative) } : [expanded]
  end
  consumer_text = consumer_files.select { |path| File.file?(path) }.map { |path| File.read(path) }.join("\n")
  missing = files_below(scripts_path).reject do |relative|
    consumer_text.include?("scripts/#{relative}")
  end
  raise "imported ActiveChecks scripts are not rendered: #{missing.join(", ")}" unless missing.empty?
end

def verify_script_consumption!(repo_root, entry)
  case entry.fetch("consumer")
  when "slurm-configmap"
    verify_slurm_script_consumption!(repo_root, entry)
  when "activechecks-render"
    verify_activechecks_script_consumption!(repo_root, entry)
  else
    raise "unknown script consumer #{entry["consumer"].inspect}"
  end
end

def normalized_runtime_reference(value)
  reference = value.to_s.strip
  raise "runtime image reference is empty" if reference.empty?
  raise "runtime image reference contains whitespace: #{reference.inspect}" if reference.match?(/\s/)
  return reference if reference.include?("@")
  final_segment = reference.split("/").last.to_s
  final_segment.include?(":") ? reference : "#{reference}:latest"
end

def runtime_image_reference(values, entry)
  if entry["image_path"]
    return normalized_runtime_reference(dig_path(values, entry.fetch("image_path")))
  end

  repository = dig_path(values, entry.fetch("repository_path")).to_s
  repository = repository.tr("#", "/") if entry["repository_format"] == "pyxis"
  tag = dig_path(values, entry.fetch("tag_path")).to_s
  if entry["tag_suffix_value_path"]
    tag = "#{tag}#{entry.fetch("tag_suffix_prefix")}#{dig_path(values, entry.fetch("tag_suffix_value_path"))}"
  end
  normalized_runtime_reference("#{repository}:#{tag}")
end

def runtime_image_source_paths(entry)
  paths = []
  paths << entry["image_path"] if entry["image_path"]
  paths << entry["repository_path"] if entry["repository_path"]
  paths << entry["tag_path"] if entry["tag_path"]
  paths.compact
end

LOGIN_SSH_RUNTIME_IMAGE_NAME = "Slurm login SSH"
LOGIN_SSH_REQUIRED_COMMANDS = %w[
  awk
  cat
  readlink
  sha256sum
  sort
  ssh-keygen
  ssh-keyscan
  sshd
  tr
].freeze
RUNTIME_COMMAND_TIMEOUT_SECONDS = 120
RUNTIME_COMMAND_CLEANUP_TIMEOUT_SECONDS = 10
RUNTIME_COMMAND_CAPTURE_LIMIT_BYTES = 65_536
class BoundedCommandTimeout < StandardError; end

def terminate_process_group(pid, signal)
  Process.kill(signal, -pid)
rescue Errno::ESRCH
  nil
end

def read_stream_bounded(stream, limit_bytes)
  captured = String.new(encoding: Encoding::BINARY)
  truncated = false
  loop do
    chunk = stream.readpartial(16_384)
    remaining = limit_bytes - captured.bytesize
    if remaining > 0
      captured << chunk.byteslice(0, remaining)
    end
    truncated = true if chunk.bytesize > remaining
  end
rescue EOFError, IOError
  [captured, truncated]
end

def capture3_bounded(*command, timeout_seconds:)
  stdin = stdout = stderr = wait_thread = nil
  stdout_reader = stderr_reader = nil
  stdin, stdout, stderr, wait_thread = Open3.popen3(*command, pgroup: true)
  stdin.close
  stdout_reader = Thread.new do
    read_stream_bounded(stdout, RUNTIME_COMMAND_CAPTURE_LIMIT_BYTES)
  end
  stderr_reader = Thread.new do
    read_stream_bounded(stderr, RUNTIME_COMMAND_CAPTURE_LIMIT_BYTES)
  end
  if wait_thread.join(timeout_seconds).nil?
    terminate_process_group(wait_thread.pid, "TERM")
    if wait_thread.join(1).nil?
      terminate_process_group(wait_thread.pid, "KILL")
      wait_thread.join(1)
    end
    raise BoundedCommandTimeout, "bounded command timed out"
  end

  stdout_output, stdout_truncated = stdout_reader.value
  stderr_output, stderr_truncated = stderr_reader.value
  [
    stdout_output,
    stderr_output,
    wait_thread.value,
    stdout_truncated,
    stderr_truncated,
  ]
ensure
  [stdin, stdout, stderr].compact.each do |stream|
    stream.close unless stream.closed?
  rescue IOError
    nil
  end
  [stdout_reader, stderr_reader].compact.each do |reader|
    reader.kill if reader.alive?
  end
end

def runtime_required_commands(entry)
  raw_commands = entry["required_commands"]
  return [] if raw_commands.nil?
  raise "required_commands must be an array" unless raw_commands.is_a?(Array)

  commands = raw_commands.map do |command|
    unless command.is_a?(String) && command.match?(/\A[A-Za-z0-9][A-Za-z0-9._+-]*\z/)
      raise "required_commands contains invalid command #{command.inspect}"
    end
    command
  end
  raise "required_commands must not be empty" if commands.empty?
  raise "required_commands contains duplicate commands" unless commands.uniq.length == commands.length
  raise "required_commands must use canonical sorted order" unless commands == commands.sort
  commands
end

def verify_runtime_command_contract!(lock)
  runtime_images = Array(lock.dig("imports", "runtime_images"))
  login_images = runtime_images.select do |entry|
    entry.fetch("name").to_s == LOGIN_SSH_RUNTIME_IMAGE_NAME
  end
  unless login_images.length == 1
    raise "runtime image lock must contain exactly one #{LOGIN_SSH_RUNTIME_IMAGE_NAME.inspect} entry"
  end

  runtime_images.each { |entry| runtime_required_commands(entry) }
  actual = runtime_required_commands(login_images.fetch(0))
  return if actual == LOGIN_SSH_REQUIRED_COMMANDS

  raise "#{LOGIN_SSH_RUNTIME_IMAGE_NAME} required_commands must exactly match the cxcli login-session probe contract"
end

def immutable_platform_reference(reference, platform_digest)
  repository = reference.split("@", 2).first.to_s
  final_slash = repository.rindex("/")
  final_colon = repository.rindex(":")
  if final_colon && (final_slash.nil? || final_colon > final_slash)
    repository = repository[0...final_colon]
  end
  raise "runtime image repository is empty for #{reference.inspect}" if repository.empty?
  unless platform_digest.match?(/\Asha256:[0-9a-f]{64}\z/)
    raise "runtime image platform digest is invalid for #{reference.inspect}"
  end
  "#{repository}@#{platform_digest}"
end

def force_remove_runtime_container(cidfile)
  return true unless File.file?(cidfile)

  container_id = File.read(cidfile).strip
  return false unless container_id.match?(/\A[0-9a-f]{12,64}\z/)

  _, _, status, _, _ = capture3_bounded(
    "docker", "rm", "--force", container_id,
    timeout_seconds: RUNTIME_COMMAND_CLEANUP_TIMEOUT_SECONDS
  )
  status.success?
rescue BoundedCommandTimeout, Errno::ENOENT, IOError
  false
end

def verify_runtime_commands!(reference, entry, platform_digests)
  commands = runtime_required_commands(entry)
  return if commands.empty?

  command_probe = <<~'SH'
    for required_command do
      command -v "$required_command" >/dev/null 2>&1 || {
        printf 'missing required command: %s\n' "$required_command" >&2
        exit 42
      }
    done
  SH
  platform_digests.sort.each do |platform, platform_digest|
    immutable_reference = immutable_platform_reference(reference, platform_digest)
    runtime_dir = Dir.mktmpdir("soperator-runtime-command-")
    cidfile = File.join(runtime_dir, "container.cid")
    begin
      _, error, status, _, error_truncated = capture3_bounded(
        "docker", "run", "--rm", "--pull=always",
        "--cidfile", cidfile,
        "--platform", platform,
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "64",
        "--memory", "128m",
        "--cpus", "1",
        "--entrypoint", "/bin/sh",
        immutable_reference,
        "-ceu", command_probe, "runtime-command-contract", *commands,
        timeout_seconds: RUNTIME_COMMAND_TIMEOUT_SECONDS
      )
    rescue BoundedCommandTimeout
      cleanup_confirmed = force_remove_runtime_container(cidfile)
      cleanup_detail = cleanup_confirmed ? "completed" : "could not be confirmed"
      raise "immutable runtime command check timed out for #{entry.fetch("name")} #{platform} after #{RUNTIME_COMMAND_TIMEOUT_SECONDS} seconds; forced container cleanup #{cleanup_detail}; command output redacted"
    ensure
      FileUtils.rm_rf(runtime_dir)
    end
    next if status.success?

    normalized_error = error.to_s.encode(
      "UTF-8", invalid: :replace, undef: :replace, replace: ""
    )
    missing_command = normalized_error.lines.map do |line|
      match = line.strip.match(/\Amissing required command: ([A-Za-z0-9][A-Za-z0-9._+-]*)\z/)
      match && match[1]
    end.compact.uniq
    detail = if !error_truncated && status.exitstatus == 42 && missing_command.length == 1 && commands.include?(missing_command[0])
      "missing required command: #{missing_command[0]}"
    else
      "probe exited #{status.exitstatus}; command output redacted"
    end
    raise "immutable runtime command check failed for #{entry.fetch("name")} #{platform}: #{detail}"
  end
end

def inspect_runtime_image(reference)
  manifest_output, manifest_error, manifest_status = Open3.capture3(
    "docker", "buildx", "imagetools", "inspect", reference,
    "--format", "{{json .Manifest}}"
  )
  unless manifest_status.success?
    raise "OCI manifest lookup failed for #{reference}: #{manifest_error.strip}"
  end
  manifest = JSON.parse(manifest_output)
  digest = manifest.fetch("digest").to_s
  unless digest.match?(/\Asha256:[0-9a-f]{64}\z/)
    raise "OCI manifest lookup returned invalid digest #{digest.inspect} for #{reference}"
  end

  platform_digests = {}
  platforms = Array(manifest["manifests"]).map do |descriptor|
    platform = descriptor["platform"] || {}
    os = platform["os"].to_s
    architecture = platform["architecture"].to_s
    next if os.empty? || architecture.empty? || os == "unknown" || architecture == "unknown"
    platform_name = [os, architecture, platform["variant"]].compact.join("/")
    platform_digest = descriptor["digest"].to_s
    unless platform_digest.match?(/\Asha256:[0-9a-f]{64}\z/)
      raise "OCI platform #{platform_name} returned invalid digest #{platform_digest.inspect} for #{reference}"
    end
    if platform_digests.key?(platform_name)
      raise "OCI image exposes duplicate concrete platform #{platform_name}: #{reference}"
    end
    platform_digests[platform_name] = platform_digest
    platform_name
  end.compact

  if platforms.empty?
    image_output, image_error, image_status = Open3.capture3(
      "docker", "buildx", "imagetools", "inspect", reference,
      "--format", "{{json .Image}}"
    )
    unless image_status.success?
      raise "OCI image-config lookup failed for #{reference}: #{image_error.strip}"
    end
    image = JSON.parse(image_output)
    configs = image.is_a?(Hash) && image.key?("os") ? [image] : image.values
    platforms = configs.map do |config|
      next unless config.is_a?(Hash) && config["os"] && config["architecture"]
      [config["os"], config["architecture"], config["variant"]].compact.join("/")
    end.compact
    platforms.each { |platform_name| platform_digests[platform_name] = digest }
  end

  raise "OCI image exposes no concrete runtime platforms: #{reference}" if platforms.empty?
  {
    "digest" => digest,
    "platforms" => platforms.uniq.sort,
    "platform_digests" => platform_digests.sort.to_h,
  }
rescue JSON::ParserError => e
  raise "OCI inspection returned invalid JSON for #{reference}: #{e.message}"
end

def verify_runtime_image_coverage!(lock)
  imported = Array(lock.dig("imports", "images")).map do |entry|
    [normalize_rel(entry.fetch("local_file")), JSON.generate(entry.fetch("local_path"))]
  end.uniq
  covered = Array(lock.dig("imports", "runtime_images")).flat_map do |entry|
    runtime_image_source_paths(entry).map do |path|
      [normalize_rel(entry.fetch("local_file")), JSON.generate(path)]
    end
  end.uniq
  missing = imported - covered
  return if missing.empty?

  labels = missing.map { |file, path| "#{file}:#{path}" }
  raise "tracked upstream image values lack runtime OCI coverage: #{labels.join(", ")}"
end

failures = []

begin
  validate_lock_target_contract!(lock)
  report_line("ok", "lock", "exact sync target allowlist") if report
rescue StandardError => e
  warn "ERROR: invalid upstream sync target contract: #{e.message}"
  exit 1
end

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
    if sync
      copy_exact_import(
        upstream_path,
        local_path,
        lock: lock,
        target_rel: entry.fetch("local_path"),
        kind: "script",
        repo_root: repo_root
      )
    end
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
    verify_script_consumption!(repo_root, entry)
    report_line("ok", "render", name, "all imported scripts have a chart consumer") if report
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
    if sync
      copy_exact_import(
        upstream_path,
        local_path,
        lock: lock,
        target_rel: entry.fetch("local_path"),
        kind: "CRD",
        repo_root: repo_root
      )
    end
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

if scope == "all" || scope == "images"
  begin
    verify_runtime_command_contract!(lock)
    report_line("ok", "lock", "runtime command contract") if report
  rescue StandardError => e
    failures << "runtime command contract: #{e.message}"
    report_line("fail", "lock", "runtime command contract", e.message)
  end

  begin
    verify_runtime_image_coverage!(lock)
    report_line("ok", "lock", "runtime image coverage") if report
  rescue StandardError => e
    failures << "runtime image coverage: #{e.message}"
    report_line("fail", "lock", "runtime image coverage", e.message)
  end

  inspection_cache = {}
  Array(lock.dig("imports", "runtime_images")).each_with_index do |entry, index|
    name = entry.fetch("name")
    begin
      local_file = safe_join(repo_root, entry.fetch("local_file"))
      values = chart_yaml(local_file)
      actual_reference = runtime_image_reference(values, entry)
      locked_reference = normalized_runtime_reference(entry.fetch("reference"))
      inspection = inspection_cache[actual_reference] ||= inspect_runtime_image(actual_reference)
      actual_digest = inspection.fetch("digest")
      expected_digest = entry.fetch("digest").to_s
      reference_changed = actual_reference != locked_reference
      digest_changed = actual_digest != expected_digest

      required_platforms = Array(entry.fetch("required_platforms")).map(&:to_s)
      raise "required_platforms must not be empty" if required_platforms.empty?
      missing_platforms = required_platforms - inspection.fetch("platforms")
      unless missing_platforms.empty?
        raise "#{actual_reference} lacks required OCI platform(s): #{missing_platforms.join(", ")}"
      end
      actual_platform_digests = inspection.fetch("platform_digests").slice(*required_platforms)
      expected_platform_digests = entry.fetch("platform_digests", {}).transform_keys(&:to_s)
      platform_digests_changed = actual_platform_digests != expected_platform_digests
      baseline_changed = reference_changed || digest_changed || platform_digests_changed

      if baseline_changed && sync && accept_image_baseline
        yq_set_string!(lock_file, ".imports.runtime_images[#{index}].reference", actual_reference)
        yq_set_string!(lock_file, ".imports.runtime_images[#{index}].digest", actual_digest)
        yq_update!(
          lock_file,
          ".imports.runtime_images[#{index}].platform_digests = #{JSON.generate(actual_platform_digests)}"
        )
        locked_reference = actual_reference
        expected_digest = actual_digest
        expected_platform_digests = actual_platform_digests
      elsif baseline_changed
        changes = []
        changes << "reference #{locked_reference.inspect} -> #{actual_reference.inspect}" if reference_changed
        changes << "index digest #{expected_digest} -> #{actual_digest}" if digest_changed
        if platform_digests_changed
          changes << "platform digests #{expected_platform_digests.inspect} -> #{actual_platform_digests.inspect}"
        end
        raise "runtime image baseline changed (#{changes.join("; ")}); review the registry change and rerun --sync --accept-image-baseline --live-upgrade-evidence PATH"
      end
      unless actual_reference == locked_reference && actual_digest == expected_digest &&
             actual_platform_digests == expected_platform_digests
        raise "runtime image lock update did not converge for #{actual_reference}"
      end
      verify_runtime_commands!(actual_reference, entry, actual_platform_digests)
      locked_platforms = required_platforms.map do |platform_name|
        "#{platform_name}=#{actual_platform_digests.fetch(platform_name)}"
      end
      detail = "#{actual_digest}, #{locked_platforms.join(", ")}"
      verified_commands = runtime_required_commands(entry)
      detail = "#{detail}, commands=#{verified_commands.join(",")}" unless verified_commands.empty?
      report_line("ok", "oci", name, detail) if report
    rescue StandardError => e
      failures << "#{name}: #{e.message}"
      report_line("fail", "oci", name, e.message)
    end
  end
end

if scope == "all"
  begin
    verify_live_upgrade_evidence_contract!(repo_root, lock)
    report_line("ok", "evidence", "trusted disposable campaign producer contract") if report
  rescue StandardError => e
    failures << "trusted disposable campaign producer contract: #{e.message}"
    report_line("fail", "evidence", "trusted disposable campaign producer contract", e.message)
  end

  begin
    verify_cxcli_values_surface!(repo_root, lock)
    report_line("ok", "values", "stable cxcli chart surface") if report
  rescue StandardError => e
    failures << "stable cxcli chart surface: #{e.message}"
    report_line("fail", "values", "stable cxcli chart surface", e.message)
  end
end

Array(lock.dig("imports", "review")).each_with_index do |entry, index|
  next unless scope == "all"
  name = entry.fetch("name")
  begin
    upstream_path = File.join(upstream_root, entry.fetch("upstream_path"))
    actual = digest_path(upstream_path)
    expected = entry.fetch("sha256")
    updated_hash = actual != expected && sync && accept_review_baseline
    if updated_hash
      yq_set_string!(lock_file, ".imports.review[#{index}].sha256", actual)
      expected = actual
    end
    if actual == expected
      report_line("ok", "logic", name, updated_hash ? "accepted reviewed baseline" : nil) if report
    else
      raise "tracked upstream logic changed; expected #{expected}, got #{actual}; review the upstream diff and rerun --sync --accept-review-baseline --live-upgrade-evidence PATH"
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
  local accept_review_baseline=0
  local accept_image_baseline=0
  local live_upgrade_evidence=""
  local ci_preview_no_branch=0
  local report=0
  local scope="all"
  local validation_root=""
  local validation_chart_dir=""
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
      --accept-review-baseline)
        accept_review_baseline=1
        shift
        ;;
      --accept-image-baseline)
        accept_image_baseline=1
        shift
        ;;
      --live-upgrade-evidence)
        if [[ $# -lt 2 || -z "$2" ]]; then
          log_error "--live-upgrade-evidence requires a path."
          exit 1
        fi
        live_upgrade_evidence="$2"
        shift 2
        ;;
      --ci-preview-no-branch)
        ci_preview_no_branch=1
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

  if [[ "${accept_review_baseline}" -eq 1 && "${sync}" -ne 1 ]]; then
    log_error "--accept-review-baseline is a write operation and must be used with --sync."
    exit 1
  fi

  if [[ "${accept_image_baseline}" -eq 1 && "${sync}" -ne 1 ]]; then
    log_error "--accept-image-baseline is a write operation and must be used with --sync."
    exit 1
  fi

  if [[ -n "${live_upgrade_evidence}" && "${sync}" -ne 1 ]]; then
    log_error "--live-upgrade-evidence can only be used with --sync."
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

  if [[ "${ci_preview_no_branch}" -eq 1 && "${sync}" -ne 1 ]]; then
    log_error "--ci-preview-no-branch can only be used with --sync in disposable CI previews."
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

  if [[ ! -f "${lock_file}" ]]; then
    log_error "Lock file not found: ${lock_file}"
    exit 1
  fi

  local repo release tag commit locked_release
  repo="$(yaml_value "${lock_file}" "repository")"
  release="$(yaml_value "${lock_file}" "release")"
  tag="$(yaml_value "${lock_file}" "tag")"
  commit="$(yaml_value "${lock_file}" "commit")"
  locked_release="${release}"

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
      log_error "Highest Soperator release is '${github_highest}', but lock is pinned to '${release}'. Complete the disposable upgrade campaign, then run --latest --sync --accept-review-baseline --accept-image-baseline --live-upgrade-evidence PATH --report from a clean working tree."
      exit 1
    elif [[ "${release_comparison}" -gt 0 ]]; then
      log_error "Locked Soperator release '${release}' is newer than highest GitHub SemVer release '${github_highest}'. Check the lock for a typo or wait for GitHub release metadata before syncing."
      exit 1
    fi
    log_success "Pinned Soperator release '${release}' is the highest GitHub SemVer release."
    exit 0
  fi

  local release_comparison=0
  if [[ "${sync_latest}" -eq 1 ]]; then
    local github_highest
    github_highest="$(highest_release_tag "${repo}")"
    if [[ -z "${github_highest}" ]]; then
      log_error "Could not determine highest GitHub SemVer release for ${repo}."
      exit 1
    fi
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

  local evidence_required=0
  if [[ "${accept_review_baseline}" -eq 1 || "${accept_image_baseline}" -eq 1 ]]; then
    evidence_required=1
  fi
  if [[ "${sync_latest}" -eq 1 && "${release_comparison}" -lt 0 && "${ci_preview_no_branch}" -ne 1 ]]; then
    evidence_required=1
  fi
  if [[ "${evidence_required}" -eq 1 && -z "${live_upgrade_evidence}" ]]; then
    log_error "Upstream pin promotion and baseline acceptance require --live-upgrade-evidence PATH from a successful, non-expired disposable upgrade campaign for target '${release}'."
    exit 1
  fi
  if [[ -n "${live_upgrade_evidence}" ]]; then
    local evidence_target_chart_version evidence_producer_repository evidence_producer_workflow
    evidence_producer_repository="$(yaml_path_value "${lock_file}" conformance live_upgrade_evidence producer_repository)"
    evidence_producer_workflow="$(yaml_path_value "${lock_file}" conformance live_upgrade_evidence producer_workflow)"
    if [[ -z "${evidence_producer_repository}" || -z "${evidence_producer_workflow}" ]]; then
      log_error "No trusted disposable-campaign evidence producer is configured in the lock; pin promotion and baseline acceptance remain unavailable."
      exit 1
    fi
    if [[ -z "${TMP_DIR:-}" ]]; then
      TMP_DIR="$(mktemp -d)"
      trap cleanup EXIT
    fi
    if [[ "${release}" == "${locked_release}" ]]; then
      evidence_target_chart_version="$(yaml_value "${chart_dir}/Chart.yaml" "version")"
    else
      evidence_target_chart_version="${release}-ps.1"
    fi
    if ! validate_live_upgrade_evidence \
      "${live_upgrade_evidence}" \
      "${release}" \
      "${evidence_target_chart_version}" \
      "${evidence_producer_repository}" \
      "${evidence_producer_workflow}" \
      "${root}" \
      "${lock_file}"; then
      log_error "Invalid disposable live upgrade evidence for target '${release}'."
      exit 1
    fi
    if ! verify_live_upgrade_evidence_provenance "${live_upgrade_evidence}" "${lock_file}"; then
      exit 1
    fi
  fi

  if [[ "${scope}" == "all" || "${sync}" -eq 1 ]]; then
    require_cmd "helm"
    require_helm_unittest
  fi
  if [[ "${sync}" -eq 1 ]]; then
    require_yq_v4
    ensure_clean_worktree "${root}"
  fi
  if [[ "${scope}" == "all" || "${scope}" == "images" ]]; then
    require_docker_buildx
  fi

  if [[ "${sync}" -eq 1 && "${ci_preview_no_branch}" -ne 1 ]]; then
    ensure_sync_branch "${root}" "${release}"
  fi

  local resolved_commit
  resolved_commit="$(resolve_tag_commit "${repo}" "${tag}")"
  if [[ -n "${live_upgrade_evidence}" ]]; then
    if ! verify_live_upgrade_evidence_commit "${live_upgrade_evidence}" "${resolved_commit}"; then
      log_error "Disposable live upgrade evidence is not bound to the resolved upstream commit."
      exit 1
    fi
  fi
  if [[ "${sync}" -ne 1 && "${resolved_commit}" != "${commit}" ]]; then
    log_error "Tag '${tag}' resolves to '${resolved_commit}', but lock expects '${commit}'."
    exit 1
  fi

  if [[ -z "${TMP_DIR:-}" ]]; then
    TMP_DIR="$(mktemp -d)"
    trap cleanup EXIT
  fi

  if [[ "${sync}" -eq 1 ]]; then
    commit="${resolved_commit}"
    local lock_rel staged_root staged_lock_file staged_chart_dir
    lock_rel="$(repo_relative_file "${root}" "${lock_file}")"
    staged_root="${TMP_DIR}/staged-repo"
    create_staged_repo "${root}" "${staged_root}"
    staged_lock_file="${staged_root}/${lock_rel}"
    staged_chart_dir="${staged_root}/helm-charts/soperator"
    if [[ "$(yaml_value "${staged_lock_file}" "release")" != "${release}" || "$(yaml_value "${staged_lock_file}" "tag")" != "${tag}" || "$(yaml_value "${staged_lock_file}" "commit")" != "${commit}" ]]; then
      update_lock_pin "${staged_lock_file}" "${release}" "${tag}" "${commit}"
    fi

    log_note "Fetching ${repo} commit ${commit} resolved from tag ${tag}..."
    fetch_release "${repo}" "${commit}" "${TMP_DIR}"
    verify_imports \
      "${staged_lock_file}" \
      "${staged_root}" \
      "${TMP_DIR}/source" \
      "${scope}" \
      "1" \
      "${report}" \
      "${release}" \
      "${commit}" \
      "${accept_review_baseline}" \
      "${accept_image_baseline}"
    sync_chart_dependencies_and_validate "${staged_root}" "${staged_chart_dir}" "1"
    if [[ -n "${live_upgrade_evidence}" ]]; then
      verify_live_upgrade_candidate_manifest \
        "${live_upgrade_evidence}" \
        "${staged_root}" \
        "${staged_lock_file}"
    fi
    if [[ "${ci_preview_no_branch}" -eq 1 ]]; then
      log_note "Disposable CI preview validated in isolated staging; no candidate files are promoted to the checkout."
      if [[ "${report}" -eq 1 ]]; then
        report_changed_files "${staged_root}"
      fi
    else
      promote_staged_sync "${staged_root}" "${root}" "${lock_rel}"
      if [[ "${report}" -eq 1 ]]; then
        report_changed_files "${root}"
      fi
    fi
  else
    log_note "Fetching ${repo} commit ${commit} resolved from tag ${tag}..."
    fetch_release "${repo}" "${commit}" "${TMP_DIR}"
    verify_imports \
      "${lock_file}" \
      "${root}" \
      "${TMP_DIR}/source" \
      "${scope}" \
      "0" \
      "${report}" \
      "${release}" \
      "${commit}" \
      "0" \
      "0"
    if [[ "${scope}" == "all" ]]; then
      validation_root="${TMP_DIR}/chart-validation"
      create_chart_validation_workspace "${root}" "${validation_root}"
      validation_chart_dir="${validation_root}/helm-charts/soperator"
      sync_chart_dependencies_and_validate "${validation_root}" "${validation_chart_dir}" "0"
    fi
  fi
  log_success "Upstream import verification completed for scope '${scope}'."
}

main "$@"
