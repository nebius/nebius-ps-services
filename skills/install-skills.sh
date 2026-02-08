#!/usr/bin/env bash
set -euo pipefail

# Install Codex skills from a local folder or GitHub source into ~/.agents/skills.
#
# Usage:
#   ./install-skills.sh [options] [source] [destination_dir]
#
# Source options:
#   - Local directory path (default: script directory)
#   - GitHub URL:
#       https://github.com/<owner>/<repo>
#       https://github.com/<owner>/<repo>/tree/<ref>/<subpath>
#
# Destination (default):
#   ~/.agents/skills
#
# Options:
#   --pull          Refresh local source via git pull --ff-only (local source only)
#   -h, --help      Show this help
#
# Safety/idempotency:
#   - Re-runs converge to the same state.
#   - Skills are source-owned via marker files and are not overwritten by other sources.
#   - Cleanup removes stale skills only for the same source identity.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SRC_DIR="${SCRIPT_DIR}"

show_usage() {
  cat <<'EOF'
Usage:
  ./install-skills.sh [options] [source] [destination_dir]

Source:
  - Local directory path (default: script directory)
  - GitHub URL:
      https://github.com/<owner>/<repo>
      https://github.com/<owner>/<repo>/tree/<ref>/<subpath>

Destination (default):
  ~/.agents/skills

Options:
  --pull          Refresh local source via git pull --ff-only (local source only)
  -h, --help      Show this help

Examples:
  ./install-skills.sh
  ./install-skills.sh /Users/rezab/test
  ./install-skills.sh --pull /path/to/local/skills-repo
  ./install-skills.sh "https://github.com/openai/skills/tree/main/skills" "~/.agents/skills"

Notes:
  - --pull only works with local git working trees and fails on dirty/detached state.
  - If skills were installed/updated, extension-host restart is triggered automatically (best effort, non-fatal).
  - Existing unmanaged folders in destination are never overwritten.
  - If a skill exists but belongs to another source, it is skipped.
  - A valid skill folder must contain SKILL.md.
EOF
}

is_github_source() {
  local value="$1"
  [[ "${value}" =~ ^https://github\.com/[^/]+/[^/]+(/tree/[^/]+/.+)?/?$ ]]
}

hash_string() {
  local value="$1"

  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "${value}" | shasum -a 256 | awk '{print $1}'
    return
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "${value}" | sha256sum | awk '{print $1}'
    return
  fi

  # Fallback key if hashing tools are unavailable.
  printf '%s' "${value}" | tr '/ :@' '____'
}

pull_local_source() {
  local source_dir="$1"

  if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git is required for --pull." >&2
    exit 1
  fi
  if ! git -C "${source_dir}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "ERROR: --pull requested but source is not in a git work tree: ${source_dir}" >&2
    exit 1
  fi
  if [[ -n "$(git -C "${source_dir}" status --porcelain)" ]]; then
    echo "ERROR: --pull refused because source has uncommitted changes: ${source_dir}" >&2
    echo "Commit/stash changes, or run without --pull." >&2
    exit 1
  fi
  if [[ -z "$(git -C "${source_dir}" symbolic-ref -q HEAD || true)" ]]; then
    echo "ERROR: --pull refused on detached HEAD in: ${source_dir}" >&2
    exit 1
  fi

  echo "Refreshing source via git pull --ff-only: ${source_dir}"
  git -C "${source_dir}" pull --ff-only
}

restart_vscode_extension_host_background() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "Skip extension-host restart: supported on macOS only."
    return 0
  fi
  if ! command -v open >/dev/null 2>&1; then
    echo "Skip extension-host restart: open command not available."
    return 0
  fi
  if ! pgrep -x "Code" >/dev/null 2>&1; then
    echo "Skip extension-host restart: Visual Studio Code is not running."
    return 0
  fi

  (
    open -g "vscode://command/workbench.action.restartExtensionHost" >/dev/null 2>&1 || true
  ) >/dev/null 2>&1 &

  echo "Requested VS Code command: Developer: Restart Extension Host."
}

read_target_owner() {
  local target_dir="$1"
  local marker_new="${target_dir}/.install-source-id"
  local marker_legacy="${target_dir}/.install-source"
  local owner=""

  if [[ -f "${marker_new}" ]]; then
    owner="$(cat "${marker_new}")"
  elif [[ -f "${marker_legacy}" ]]; then
    owner="$(cat "${marker_legacy}")"
    # Legacy marker format: absolute local source path from previous script version.
    if [[ "${owner}" == "${SRC_DIR}" ]]; then
      owner="${SOURCE_ID}"
      printf '%s\n' "${owner}" > "${marker_new}"
      rm -f "${marker_legacy}"
    fi
  fi

  printf '%s' "${owner}"
}

DO_PULL=0
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pull)
      DO_PULL=1
      shift
      ;;
    -h|--help)
      show_usage
      exit 0
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        POSITIONAL+=("$1")
        shift
      done
      ;;
    -*)
      echo "ERROR: unknown option: $1" >&2
      show_usage >&2
      exit 1
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ "${#POSITIONAL[@]}" -gt 2 ]]; then
  echo "ERROR: too many positional arguments." >&2
  show_usage >&2
  exit 1
fi

SOURCE_SPEC="${POSITIONAL[0]:-${DEFAULT_SRC_DIR}}"
DEST_DIR="${POSITIONAL[1]:-${HOME}/.agents/skills}"

if ! command -v rsync >/dev/null 2>&1; then
  echo "ERROR: rsync is required but was not found in PATH." >&2
  exit 1
fi

TMP_MANIFEST=""
TMP_CLONE_ROOT=""
cleanup() {
  rm -f "${TMP_MANIFEST:-}" >/dev/null 2>&1 || true
  if [[ -n "${TMP_CLONE_ROOT}" && -d "${TMP_CLONE_ROOT}" ]]; then
    rm -rf "${TMP_CLONE_ROOT}"
  fi
}
trap cleanup EXIT

SOURCE_ID=""
if is_github_source "${SOURCE_SPEC}"; then
  if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git is required to use a remote GitHub source." >&2
    exit 1
  fi
  if [[ "${DO_PULL}" -eq 1 ]]; then
    echo "ERROR: --pull is only supported for local source directories." >&2
    exit 1
  fi

  source_no_trailing="${SOURCE_SPEC%/}"
  if [[ "${source_no_trailing}" =~ ^https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+)$ ]]; then
    owner="${BASH_REMATCH[1]}"
    repo="${BASH_REMATCH[2]}"
    ref="${BASH_REMATCH[3]}"
    subpath="${BASH_REMATCH[4]}"
  elif [[ "${source_no_trailing}" =~ ^https://github\.com/([^/]+)/([^/]+)$ ]]; then
    owner="${BASH_REMATCH[1]}"
    repo="${BASH_REMATCH[2]}"
    ref="HEAD"
    subpath="."
  else
    echo "ERROR: unsupported GitHub source format: ${SOURCE_SPEC}" >&2
    echo "Use https://github.com/<owner>/<repo> or /tree/<ref>/<subpath>" >&2
    exit 1
  fi

  repo="${repo%.git}"
  repo_url="https://github.com/${owner}/${repo}.git"
  TMP_CLONE_ROOT="$(mktemp -d)"
  clone_dir="${TMP_CLONE_ROOT}/repo"

  if [[ "${ref}" == "HEAD" ]]; then
    git clone --depth 1 "${repo_url}" "${clone_dir}" >/dev/null
  else
    git clone --depth 1 --branch "${ref}" "${repo_url}" "${clone_dir}" >/dev/null
  fi

  SRC_DIR="${clone_dir}/${subpath}"
  if [[ ! -d "${SRC_DIR}" ]]; then
    echo "ERROR: source subpath not found in remote repo: ${subpath}" >&2
    exit 1
  fi
  SRC_DIR="$(cd "${SRC_DIR}" && pwd -P)"
  SOURCE_ID="github:${owner}/${repo}@${ref}:${subpath}"
else
  if [[ ! -d "${SOURCE_SPEC}" ]]; then
    echo "ERROR: source directory does not exist: ${SOURCE_SPEC}" >&2
    exit 1
  fi

  SRC_DIR="$(cd "${SOURCE_SPEC}" && pwd -P)"
  SOURCE_ID="local:${SRC_DIR}"

  if [[ "${DO_PULL}" -eq 1 ]]; then
    pull_local_source "${SRC_DIR}"
  fi
fi

mkdir -p "${DEST_DIR}"
DEST_DIR="$(cd "${DEST_DIR}" && pwd -P)"

if [[ "${SRC_DIR}" == "${DEST_DIR}" ]]; then
  echo "ERROR: source and destination must be different directories." >&2
  exit 1
fi

SOURCE_KEY="$(hash_string "${SOURCE_ID}")"

STATE_DIR="${DEST_DIR}/.install-skills-state"
SOURCE_MANIFEST="${STATE_DIR}/${SOURCE_KEY}.skills"
TMP_MANIFEST="$(mktemp)"

installed=0
skipped=0
removed=0

shopt -s nullglob
for d in "${SRC_DIR}"/*; do
  [[ -d "${d}" ]] || continue

  name="$(basename "${d}")"
  if [[ ! -f "${d}/SKILL.md" ]]; then
    echo "Skip (no SKILL.md): ${name}"
    skipped=$((skipped + 1))
    continue
  fi

  target="${DEST_DIR}/${name}"
  marker="${target}/.install-source-id"
  if [[ -d "${target}" ]]; then
    target_owner="$(read_target_owner "${target}")"
    if [[ -n "${target_owner}" ]]; then
      if [[ "${target_owner}" != "${SOURCE_ID}" ]]; then
        echo "Skip (owned by different source): ${name}"
        skipped=$((skipped + 1))
        continue
      fi
    else
      echo "Skip (existing unmanaged directory): ${name}"
      skipped=$((skipped + 1))
      continue
    fi
  fi

  mkdir -p "${target}"

  # Keep installed skill folders in sync with source.
  rsync -a --delete --exclude ".DS_Store" --exclude ".install-source" --exclude ".install-source-id" "${d}/" "${target}/"
  printf '%s\n' "${SOURCE_ID}" > "${marker}"
  rm -f "${target}/.install-source"
  printf '%s\n' "${name}" >> "${TMP_MANIFEST}"

  echo "Installed/updated: ${name} -> ${target}"
  installed=$((installed + 1))
done
shopt -u nullglob

sort -u "${TMP_MANIFEST}" -o "${TMP_MANIFEST}"

# Remove skills previously installed from this source that no longer exist in source.
if [[ -f "${SOURCE_MANIFEST}" ]]; then
  while IFS= read -r old_name; do
    [[ -n "${old_name}" ]] || continue
    if ! grep -Fxq -- "${old_name}" "${TMP_MANIFEST}"; then
      old_target="${DEST_DIR}/${old_name}"
      if [[ -d "${old_target}" ]]; then
        old_owner="$(read_target_owner "${old_target}")"
        if [[ "${old_owner}" == "${SOURCE_ID}" ]]; then
          rm -rf "${old_target}"
          echo "Removed stale skill: ${old_name}"
          removed=$((removed + 1))
        fi
      fi
    fi
  done < "${SOURCE_MANIFEST}"
fi

mkdir -p "${STATE_DIR}"
cp "${TMP_MANIFEST}" "${SOURCE_MANIFEST}"

echo "Done. Installed/updated: ${installed}, skipped: ${skipped}, removed: ${removed}"
if [[ "${installed}" -eq 0 ]]; then
  echo "Warning: no skills were installed from ${SRC_DIR}" >&2
fi
if [[ "${installed}" -gt 0 ]]; then
  restart_vscode_extension_host_background
else
  echo "If Codex is running, restart it so it reloads skills."
fi
