#!/usr/bin/env bash
set -euo pipefail

# Builds the nccl-test image from a selected NVIDIA CUDA tag and NVIDIA/nccl-tests ref, then loads it into local Docker by default or pushes it to the remote image name you pass in --tag when you add --push, authenticating to Nebius registries first.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

DEFAULT_TAG="nccl-test:dev"
DEFAULT_PLATFORM="linux/amd64"
DEFAULT_CUDA_IMAGE_TAG="13.2.0"
DEFAULT_NCCL_TESTS_REF="v2.18.3"

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

print_usage_option() {
  local label="$1"
  local description="$2"
  local label_width=24
  local padding_width=$((label_width - ${#label}))
  local padding=""

  if (( padding_width < 1 )); then
    padding_width=1
  fi
  printf -v padding '%*s' "${padding_width}" ''

  printf '%b%b%b\n' "  ${S_YELLOW}${label}${S_RESET}" "${padding}" "${description}"
}

extract_registry_host() {
  local image_ref="${1%%@*}"
  local first_component=""

  if [[ "${image_ref}" != */* ]]; then
    return 1
  fi

  first_component="${image_ref%%/*}"
  if [[ "${first_component}" == "localhost" || "${first_component}" == *.* || "${first_component}" == *:* ]]; then
    printf '%s\n' "${first_component}"
    return 0
  fi

  return 1
}

is_nebius_registry_host() {
  local registry_host="$1"
  [[ "${registry_host}" == cr.*.nebius.cloud || "${registry_host}" == cr.nebius.cloud ]]
}

login_nebius_registry_if_needed() {
  local image_tag="$1"
  local registry_host=""
  local nebius_iam_token="${NEBIUS_IAM_TOKEN:-}"

  if ! registry_host="$(extract_registry_host "${image_tag}")"; then
    return 0
  fi

  if ! is_nebius_registry_host "${registry_host}"; then
    return 0
  fi

  require_cmd nebius

  if [[ -z "${nebius_iam_token}" ]]; then
    log_note "Fetching Nebius IAM token for ${registry_host}..."
    nebius_iam_token="$(nebius iam get-access-token)"
  else
    log_note "Using NEBIUS_IAM_TOKEN for ${registry_host}..."
  fi

  if [[ -z "${nebius_iam_token}" ]]; then
    log_error "Nebius IAM token is empty. Set NEBIUS_IAM_TOKEN or make sure 'nebius iam get-access-token' works."
    exit 1
  fi

  log_note "Logging in to Nebius Container Registry ${registry_host}..."
  printf '%s\n' "${nebius_iam_token}" | docker login "${registry_host}" --username iam --password-stdin >/dev/null
}

show_usage() {
  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./build-image.sh${S_RESET} ${S_DIM}[options]${S_RESET}"
  printf '\n'
  printf '%b\n' "${S_DIM}Build from a chosen NVIDIA CUDA base tag and NVIDIA/nccl-tests ref, then load locally by default or push to the remote image name in --tag when --push is set.${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  print_usage_option "--tag <name>" "Image tag to load locally or push remotely. Default: ${DEFAULT_TAG}"
  print_usage_option "--platform <platform>" "Build target platform. Default: ${DEFAULT_PLATFORM}"
  print_usage_option "--cuda-image-tag <tag>" "CUDA base image tag. Default: ${DEFAULT_CUDA_IMAGE_TAG}"
  print_usage_option "--nccl-tests-ref <ref>" "Upstream NVIDIA/nccl-tests ref. Default: ${DEFAULT_NCCL_TESTS_REF}"
  print_usage_option "--nccl-package-version" "Optional libnccl package version override."
  print_usage_option "--push" "Push to the remote image name in --tag instead of loading locally."
  print_usage_option "--no-cache" "Disable Docker build cache."
  print_usage_option "-h, --help" "Show help."
  printf '\n'

  printf '%b\n' "${S_BOLD}Notes:${S_RESET}"
  printf '%b\n' "  ${S_DIM}- Without --push, the image is loaded into your local Docker cache under --tag.${S_RESET}"
  printf '%b\n' "  ${S_DIM}- With --push, set --tag to the full remote image name you want to publish, for example cr.eu-north1.nebius.cloud/<short-id>/images/nccl-test:dev.${S_RESET}"
  printf '%b\n' "  ${S_DIM}- For Nebius registry pushes, the script uses NEBIUS_IAM_TOKEN when set or fetches one with 'nebius iam get-access-token', then runs docker login before pushing.${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./build-image.sh${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./build-image.sh --tag nccl-test:cuda13.2.0-v2.18.3${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./build-image.sh --cuda-image-tag 13.2.0 --nccl-tests-ref v2.18.3${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./build-image.sh --tag cr.eu-north1.nebius.cloud/<short-id>/images/nccl-test:dev --push${S_RESET}"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "Required command not found: ${cmd}"
    exit 1
  fi
}

main() {
  init_output_style
  require_cmd docker

  local image_tag="${DEFAULT_TAG}"
  local platform="${DEFAULT_PLATFORM}"
  local cuda_image_tag="${DEFAULT_CUDA_IMAGE_TAG}"
  local nccl_tests_ref="${DEFAULT_NCCL_TESTS_REF}"
  local nccl_package_version=""
  local push_image=0
  local no_cache=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tag)
        shift
        [[ $# -gt 0 ]] || { log_error "--tag requires a value"; exit 1; }
        image_tag="$1"
        ;;
      --platform)
        shift
        [[ $# -gt 0 ]] || { log_error "--platform requires a value"; exit 1; }
        platform="$1"
        ;;
      --cuda-image-tag)
        shift
        [[ $# -gt 0 ]] || { log_error "--cuda-image-tag requires a value"; exit 1; }
        cuda_image_tag="$1"
        ;;
      --nccl-tests-ref)
        shift
        [[ $# -gt 0 ]] || { log_error "--nccl-tests-ref requires a value"; exit 1; }
        nccl_tests_ref="$1"
        ;;
      --nccl-package-version)
        shift
        [[ $# -gt 0 ]] || { log_error "--nccl-package-version requires a value"; exit 1; }
        nccl_package_version="$1"
        ;;
      --push)
        push_image=1
        ;;
      --no-cache)
        no_cache=1
        ;;
      -h|--help)
        show_usage
        exit 0
        ;;
      *)
        log_error "Unknown argument: $1"
        show_usage >&2
        exit 1
        ;;
    esac
    shift
  done

  if [[ "${push_image}" -eq 0 ]] && [[ "${platform}" == *,* ]]; then
    log_error "A multi-platform build must use --push; local --load supports only a single platform."
    exit 1
  fi

  if ! docker buildx version >/dev/null 2>&1; then
    log_error "docker buildx is required."
    exit 1
  fi

  local output_flag="--load"
  if [[ "${push_image}" -eq 1 ]]; then
    output_flag="--push"
  fi

  local cmd=(
    docker buildx build
    --file "${SCRIPT_DIR}/Dockerfile"
    --platform "${platform}"
    --pull
    --provenance=false
    --sbom=false
    --tag "${image_tag}"
    --build-arg "CUDA_IMAGE_TAG=${cuda_image_tag}"
    --build-arg "NCCL_TESTS_REF=${nccl_tests_ref}"
    "${output_flag}"
    "${SCRIPT_DIR}"
  )

  if [[ -n "${nccl_package_version}" ]]; then
    cmd+=(--build-arg "NCCL_PACKAGE_VERSION=${nccl_package_version}")
  fi
  if [[ "${no_cache}" -eq 1 ]]; then
    cmd+=(--no-cache)
  fi

  log_note "Building ${image_tag}"
  log_note "CUDA base tag : ${cuda_image_tag}"
  log_note "NCCL tests ref: ${nccl_tests_ref}"
  log_note "Platform      : ${platform}"
  log_note "Attestations  : disabled to match CI registry output"
  if [[ "${push_image}" -eq 1 ]]; then
    login_nebius_registry_if_needed "${image_tag}"
  fi
  "${cmd[@]}"

  if [[ "${push_image}" -eq 1 ]]; then
    log_success "Image pushed: ${image_tag}"
  else
    log_success "Image loaded locally: ${image_tag}"
  fi
}

main "$@"
