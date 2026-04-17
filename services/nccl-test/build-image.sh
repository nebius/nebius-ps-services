#!/usr/bin/env bash
set -euo pipefail

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

show_usage() {
  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./build-image.sh${S_RESET} ${S_DIM}[options]${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}--tag${S_RESET} <name>             Local image tag. Default: ${DEFAULT_TAG}"
  printf '%b\n' "  ${S_YELLOW}--platform${S_RESET} <platform>   Build target platform. Default: ${DEFAULT_PLATFORM}"
  printf '%b\n' "  ${S_YELLOW}--cuda-image-tag${S_RESET} <tag>  CUDA base image tag. Default: ${DEFAULT_CUDA_IMAGE_TAG}"
  printf '%b\n' "  ${S_YELLOW}--nccl-tests-ref${S_RESET} <ref>  Upstream NVIDIA/nccl-tests ref. Default: ${DEFAULT_NCCL_TESTS_REF}"
  printf '%b\n' "  ${S_YELLOW}--nccl-package-version${S_RESET}  Optional libnccl package version override."
  printf '%b\n' "  ${S_YELLOW}--push${S_RESET}                  Push instead of loading into local Docker."
  printf '%b\n' "  ${S_YELLOW}--no-cache${S_RESET}              Disable Docker build cache."
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}               Show help."
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
  "${cmd[@]}"

  if [[ "${push_image}" -eq 1 ]]; then
    log_success "Image pushed: ${image_tag}"
  else
    log_success "Image loaded locally: ${image_tag}"
  fi
}

main "$@"
