#!/usr/bin/env bash
set -euo pipefail

IMAGE="ubuntu:24.04"
WORKDIR="/workdir"
HOSTDIR="$(pwd -P)"
CODE_USER_DIR="${HOME}/Library/Application Support/Code/User"
OPEN_VSCODE=1
CONFIGURE_VSCODE=1
BOOTSTRAP=1
DRY_RUN=0
STOP_ONLY=0
REMOVE_ONLY=0
CONTAINER_NAME=""
PREFERRED_PYTHON=""
GIT_METADATA_SOURCE=""
PROJECT_SUBPATH=""
CONTAINER_REVISION="2"

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

log_warn() {
  printf '%b\n' "${S_YELLOW}${1}${S_RESET}" >&2
}

log_note() {
  printf '%b\n' "${S_DIM}${1}${S_RESET}"
}

log_success() {
  printf '%b\n' "${S_GREEN}${1}${S_RESET}"
}

show_usage() {
  printf '%b\n' "${S_BOLD}Usage:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./attach-ubuntu.sh${S_RESET} ${S_DIM}[options]${S_RESET}"
  printf '\n'

  printf '%b\n' "${S_BOLD}Options:${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}-h, --help${S_RESET}             Show help"
  printf '%b\n' "  ${S_YELLOW}--image <ref>${S_RESET}         Container image ${S_DIM}(default: ${IMAGE})${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}--name <name>${S_RESET}         Override the derived container name"
  printf '%b\n' "  ${S_YELLOW}--workdir <path>${S_RESET}      Container mount and working directory ${S_DIM}(default: ${WORKDIR})${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}--code-user-dir <dir>${S_RESET} Override VS Code user data root ${S_DIM}(default: ${CODE_USER_DIR})${S_RESET}"
  printf '%b\n' "  ${S_YELLOW}--no-open${S_RESET}             Prepare the container without opening VS Code"
  printf '%b\n' "  ${S_YELLOW}--no-bootstrap${S_RESET}        Skip Ubuntu toolchain and project dependency bootstrap"
  printf '%b\n' "  ${S_YELLOW}--no-vscode-config${S_RESET}    Skip attached-container config updates"
  printf '%b\n' "  ${S_YELLOW}--stop${S_RESET}                Stop the derived container and exit"
  printf '%b\n' "  ${S_YELLOW}--remove${S_RESET}              Remove the derived container and exit"
  printf '%b\n' "  ${S_YELLOW}--dry-run${S_RESET}             Print actions without changing Docker or VS Code"
  printf '\n'

  printf '%b\n' "${S_BOLD}Examples:${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./attach-ubuntu.sh${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./attach-ubuntu.sh --no-open${S_RESET}"
  printf '%b\n' "  ${S_CYAN}./attach-ubuntu.sh --remove${S_RESET}"
}

require_argument() {
  local flag="$1"
  local value="${2:-}"

  if [[ -z "${value}" ]]; then
    log_error "Missing value for ${flag}."
    show_usage >&2
    exit 1
  fi
}

require_command() {
  local cmd="$1"
  local reason="$2"

  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "'${cmd}' is required ${reason}."
    exit 1
  fi
}

format_command() {
  local quoted=""
  printf -v quoted '%q ' "$@"
  printf '%s' "${quoted% }"
}

urlencode() {
  local input="$1"
  local output=""
  local i=0
  local ch=""

  while [[ ${i} -lt ${#input} ]]; do
    ch="${input:${i}:1}"
    case "${ch}" in
      [a-zA-Z0-9.~_-])
        output+="${ch}"
        ;;
      *)
        printf -v ch '%%%02x' "'${ch}"
        output+="${ch}"
        ;;
    esac
    i=$((i + 1))
  done

  printf '%s' "${output}"
}

urlencode_path() {
  local input="$1"
  local output=""
  local i=0
  local ch=""

  while [[ ${i} -lt ${#input} ]]; do
    ch="${input:${i}:1}"
    case "${ch}" in
      /|[a-zA-Z0-9.~_-])
        output+="${ch}"
        ;;
      *)
        printf -v ch '%%%02x' "'${ch}"
        output+="${ch}"
        ;;
    esac
    i=$((i + 1))
  done

  printf '%s' "${output}"
}

hex_encode() {
  LC_ALL=C printf '%s' "$1" | od -An -tx1 | tr -d ' \n'
}

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf '%s' "${value}"
}

project_container_name() {
  local project_name=""
  project_name="$(basename "${HOSTDIR}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
  project_name="${project_name#-}"
  project_name="${project_name%-}"

  if [[ -z "${project_name}" ]]; then
    project_name="workspace"
  fi

  printf 'attach-ubuntu-%s\n' "${project_name}"
}

validate_container_name() {
  local value="$1"

  if [[ ! "${value}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
    log_error "Container name '${value}' is invalid. Use Docker-safe characters only."
    exit 1
  fi
}

container_exists() {
  docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"
}

container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}" 2>/dev/null || printf 'false')" == "true" ]]
}

mounted_source() {
  docker inspect -f '{{range .Mounts}}{{if eq .Destination "'"${WORKDIR}"'"}}{{.Source}}{{end}}{{end}}' "${CONTAINER_NAME}" 2>/dev/null || true
}

container_image() {
  docker inspect -f '{{.Config.Image}}' "${CONTAINER_NAME}" 2>/dev/null || true
}

container_workdir() {
  docker inspect -f '{{.Config.WorkingDir}}' "${CONTAINER_NAME}" 2>/dev/null || true
}

container_label() {
  local label_name="$1"
  docker inspect -f "{{ index .Config.Labels \"${label_name}\" }}" "${CONTAINER_NAME}" 2>/dev/null || true
}

git_metadata_mount_source() {
  docker inspect -f '{{range .Mounts}}{{if eq .Destination "/opt/attach-ubuntu/repo-root"}}{{.Source}}{{end}}{{end}}' "${CONTAINER_NAME}" 2>/dev/null || true
}

detect_git_metadata_source() {
  local probe="${HOSTDIR}"
  local git_root=""

  if ! command -v git >/dev/null 2>&1; then
    printf ''
    return 0
  fi

  while true; do
    if git_root="$(git -C "${probe}" rev-parse --show-toplevel 2>/dev/null)"; then
      if [[ "${git_root}" == "${HOSTDIR}" ]]; then
        printf ''
      else
        printf '%s\n' "${git_root}"
      fi
      return 0
    fi

    if [[ "${probe}" == "/" ]]; then
      break
    fi

    probe="$(dirname "${probe}")"
  done

  printf ''
}

project_subpath_from_repo_root() {
  local repo_root="$1"
  local normalized_repo_root="${repo_root%/}/"
  local relpath="${HOSTDIR#"${normalized_repo_root}"}"

  if [[ -z "${repo_root}" ]] || [[ "${relpath}" == "${HOSTDIR}" ]] || [[ -z "${relpath}" ]]; then
    log_error "Unable to derive the project subpath for ${HOSTDIR} inside ${repo_root}."
    exit 1
  fi

  printf '%s\n' "${relpath}"
}

validate_local_git_state() {
  if [[ ! -e "${HOSTDIR}/.git" ]] || ! command -v git >/dev/null 2>&1; then
    return 0
  fi

  if git -C "${HOSTDIR}" rev-parse --git-dir >/dev/null 2>&1; then
    return 0
  fi

  log_error "Found an invalid ${HOSTDIR}/.git. Remove that leftover directory or file and rerun attach-ubuntu."
  exit 1
}

detect_requested_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf ''
    return 0
  fi

  python3 - "${HOSTDIR}" <<'PY'
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

workspace = Path(sys.argv[1])
candidates = ["3.9", "3.10", "3.11", "3.12", "3.13"]


def normalize(raw: str) -> str:
    match = re.search(r"(\d+)\.(\d+)", raw)
    if not match:
        return ""
    return f"{match.group(1)}.{match.group(2)}"


def version_tuple(raw: str) -> tuple[int, int, int]:
    parts = [int(part) for part in raw.split(".") if part.isdigit()]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def satisfies(spec: str, candidate: str) -> bool:
    candidate_tuple = version_tuple(candidate)
    for token in [part.strip() for part in spec.split(",") if part.strip()]:
        match = re.match(r"^(~=|==|!=|<=|>=|<|>)(.+)$", token)
        if not match:
            return False
        operator, raw_version = match.groups()
        raw_version = raw_version.strip()
        wildcard = raw_version.endswith(".*")
        normalized = normalize(raw_version)
        if not normalized:
            return False
        target_tuple = version_tuple(normalized)
        if operator == "==":
            if wildcard:
                if not candidate.startswith(normalized):
                    return False
            elif candidate_tuple != target_tuple:
                return False
        elif operator == "!=":
            if wildcard:
                if candidate.startswith(normalized):
                    return False
            elif candidate_tuple == target_tuple:
                return False
        elif operator == ">":
            if not candidate_tuple > target_tuple:
                return False
        elif operator == ">=":
            if not candidate_tuple >= target_tuple:
                return False
        elif operator == "<":
            if not candidate_tuple < target_tuple:
                return False
        elif operator == "<=":
            if not candidate_tuple <= target_tuple:
                return False
        elif operator == "~=":
            lower_ok = candidate_tuple >= target_tuple
            upper_major = target_tuple[0]
            upper_minor = target_tuple[1] + 1
            upper_bound = (upper_major, upper_minor, 0)
            if not (lower_ok and candidate_tuple < upper_bound):
                return False
    return True


def version_from_classifier(classifiers: list[str]) -> str:
    versions = []
    for classifier in classifiers:
        match = re.search(r"Programming Language :: Python :: (\d+\.\d+)$", classifier)
        if match:
            versions.append(match.group(1))
    if not versions:
        return ""
    versions.sort(key=version_tuple)
    return versions[-1]


def version_from_target_version(raw: str) -> str:
    match = re.fullmatch(r"py(\d)(\d{2})", raw.strip())
    if not match:
        return ""
    return f"{match.group(1)}.{int(match.group(2))}"


python_version_file = workspace / ".python-version"
if python_version_file.is_file():
    detected = normalize(python_version_file.read_text(encoding="utf-8").strip())
    if detected:
        print(detected)
        raise SystemExit(0)

runtime_file = workspace / "runtime.txt"
if runtime_file.is_file():
    detected = normalize(runtime_file.read_text(encoding="utf-8").strip())
    if detected:
        print(detected)
        raise SystemExit(0)

pyproject_file = workspace / "pyproject.toml"
if tomllib is not None and pyproject_file.is_file():
    data = tomllib.loads(pyproject_file.read_text(encoding="utf-8"))
    requires_python = ""
    if isinstance(data, dict):
        project = data.get("project")
        if isinstance(project, dict):
            requires_python = project.get("requires-python", "") or ""
            classifiers = project.get("classifiers", []) or []
            if isinstance(classifiers, list):
                detected = version_from_classifier([str(item) for item in classifiers])
                if detected:
                    print(detected)
                    raise SystemExit(0)
        tool = data.get("tool")
        if isinstance(tool, dict):
            ruff = tool.get("ruff")
            if isinstance(ruff, dict):
                detected = version_from_target_version(str(ruff.get("target-version", "") or ""))
                if detected:
                    print(detected)
                    raise SystemExit(0)
    if requires_python:
        for candidate in candidates:
            if satisfies(requires_python, candidate):
                print(candidate)
                raise SystemExit(0)
        detected = normalize(requires_python)
        if detected:
            print(detected)
            raise SystemExit(0)

print("")
PY
}

bootstrap_container() {
  local marker_value=""
  local preferred_python="${PREFERRED_PYTHON}"

  [[ "${BOOTSTRAP}" -eq 1 ]] || return 0

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log_note "Dry-run: would bootstrap Ubuntu toolchain inside ${CONTAINER_NAME}"
    if [[ -n "${preferred_python}" ]]; then
      log_note "Dry-run: preferred project Python version: ${preferred_python}"
    fi
    if [[ -f "${HOSTDIR}/pyproject.toml" ]]; then
      if [[ -f "${HOSTDIR}/Makefile" ]]; then
        log_note "Dry-run: would use the project Makefile to provision dependencies when possible"
      else
        log_note "Dry-run: would create /opt/attach-ubuntu/venv and install the project in editable mode"
      fi
    fi
    return 0
  fi

  marker_value="toolchain-v1:${preferred_python:-system}"

  log_note "Bootstrapping Ubuntu toolchain in ${CONTAINER_NAME}"
  if [[ -n "${preferred_python}" ]]; then
    log_note "Detected preferred project Python: ${preferred_python}"
  fi

  ATTACH_UBUNTU_WORKDIR="${WORKDIR}" \
  ATTACH_UBUNTU_PREFERRED_PYTHON="${preferred_python}" \
  ATTACH_UBUNTU_TOOLCHAIN_MARKER="${marker_value}" \
  docker exec -i -e ATTACH_UBUNTU_WORKDIR -e ATTACH_UBUNTU_PREFERRED_PYTHON -e ATTACH_UBUNTU_TOOLCHAIN_MARKER \
    "${CONTAINER_NAME}" /usr/bin/env bash -s <<'EOF'
set -euo pipefail

WORKDIR="${ATTACH_UBUNTU_WORKDIR}"
PREFERRED_PYTHON="${ATTACH_UBUNTU_PREFERRED_PYTHON}"
TOOLCHAIN_MARKER="${ATTACH_UBUNTU_TOOLCHAIN_MARKER}"
STATE_DIR="/var/lib/attach-ubuntu"
STATE_FILE="${STATE_DIR}/toolchain.marker"
BASHRC="/root/.bashrc"
PROJECT_VENV_PATH="/opt/attach-ubuntu/venv"
PROJECT_VENV_MARKER="${PROJECT_VENV_PATH}/.attach-ubuntu-installed"

export DEBIAN_FRONTEND=noninteractive

mkdir -p "${STATE_DIR}"

have_required_tools() {
  command -v make >/dev/null 2>&1 &&
  command -v git >/dev/null 2>&1 &&
  command -v curl >/dev/null 2>&1 &&
  command -v python3 >/dev/null 2>&1
}

configure_python_shims() {
  local current_python="/usr/bin/python3"
  if [[ -n "${PREFERRED_PYTHON}" ]] && command -v "python${PREFERRED_PYTHON}" >/dev/null 2>&1; then
    current_python="$(command -v "python${PREFERRED_PYTHON}")"
  fi

  ln -sf "${current_python}" /usr/local/bin/python3
  ln -sf "${current_python}" /usr/local/bin/python
}

ensure_auto_venv_activation() {
  if grep -Fq '# attach-ubuntu-auto-venv' "${BASHRC}" 2>/dev/null; then
    return 0
  fi

  cat >>"${BASHRC}" <<'RC'
# attach-ubuntu-auto-venv
_attach_ubuntu_auto_venv() {
  local workdir="/workdir"
  local venv="/opt/attach-ubuntu/venv"
  if [[ ! -f "${venv}/bin/activate" ]]; then
    if [[ "${VIRTUAL_ENV:-}" == "${venv}" ]] && type deactivate >/dev/null 2>&1; then
      deactivate >/dev/null 2>&1 || true
    fi
    if [[ "${VENV:-}" == "${venv}" ]]; then
      unset VENV
    fi
    return 0
  fi
  case "${PWD}" in
    "${workdir}"|${workdir}/*)
      export VENV="${venv}"
      if [[ "${VIRTUAL_ENV:-}" != "${venv}" ]]; then
        . "${venv}/bin/activate"
      fi
      ;;
    *)
      if [[ "${VIRTUAL_ENV:-}" == "${venv}" ]] && type deactivate >/dev/null 2>&1; then
        deactivate >/dev/null 2>&1 || true
      fi
      if [[ "${VENV:-}" == "${venv}" ]]; then
        unset VENV
      fi
      ;;
  esac
}

case ";${PROMPT_COMMAND:-};" in
  *";_attach_ubuntu_auto_venv;"*) ;;
  *)
    PROMPT_COMMAND="_attach_ubuntu_auto_venv${PROMPT_COMMAND:+;${PROMPT_COMMAND}}"
    ;;
esac

_attach_ubuntu_auto_venv
RC
}

needs_toolchain_install=1
if [[ -f "${STATE_FILE}" ]] && [[ "$(cat "${STATE_FILE}")" == "${TOOLCHAIN_MARKER}" ]] && have_required_tools; then
  needs_toolchain_install=0
fi

if [[ "${needs_toolchain_install}" -eq 1 ]]; then
  apt-get update
  apt-get install -y \
    bash \
    build-essential \
    ca-certificates \
    curl \
    git \
    libffi-dev \
    libssl-dev \
    make \
    pkg-config \
    python-is-python3 \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv

  if [[ -n "${PREFERRED_PYTHON}" ]] && [[ "${PREFERRED_PYTHON}" != "3.12" ]]; then
    if ! apt-get install -y \
      "python${PREFERRED_PYTHON}" \
      "python${PREFERRED_PYTHON}-dev" \
      "python${PREFERRED_PYTHON}-venv"; then
      printf '%s\n' "attach-ubuntu: unable to install python${PREFERRED_PYTHON}; falling back to the default python3." >&2
    fi
  fi

  configure_python_shims
  printf '%s\n' "${TOOLCHAIN_MARKER}" > "${STATE_FILE}"
else
  configure_python_shims
fi

ensure_auto_venv_activation

cd "${WORKDIR}"

if [[ ! -f pyproject.toml ]]; then
  exit 0
fi

if [[ -f Makefile ]] && grep -Eq '^env:' Makefile; then
  make "VENV=${PROJECT_VENV_PATH}" env
  exit 0
fi

if [[ -f Makefile ]] && grep -Eq '^install:' Makefile; then
  make "VENV=${PROJECT_VENV_PATH}" install
  exit 0
fi

needs_project_install=0
if [[ ! -f "${PROJECT_VENV_MARKER}" ]]; then
  needs_project_install=1
fi

for dependency_file in pyproject.toml requirements.txt requirements-dev.txt uv.lock poetry.lock; do
  if [[ -f "${dependency_file}" ]] && [[ "${needs_project_install}" -eq 0 ]] && [[ "${dependency_file}" -nt "${PROJECT_VENV_MARKER}" ]]; then
    needs_project_install=1
  fi
done

if [[ "${needs_project_install}" -eq 0 ]]; then
  exit 0
fi

mkdir -p "$(dirname "${PROJECT_VENV_PATH}")"
python3 -m venv "${PROJECT_VENV_PATH}"
. "${PROJECT_VENV_PATH}/bin/activate"
python -m pip install --upgrade pip setuptools wheel

if python - <<'PY'
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    sys.exit(1)

data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
project = data.get("project", {})
optional = project.get("optional-dependencies", {})
sys.exit(0 if "dev" in optional else 1)
PY
then
  python -m pip install -e ".[dev]"
else
  python -m pip install -e .
fi

touch "${PROJECT_VENV_MARKER}"
EOF

  log_success "Ubuntu toolchain bootstrap completed in ${CONTAINER_NAME}"
}

ensure_vscode_attach_config() {
  local config_dir=""
  local config_file=""
  local written_path=""

  [[ "${CONFIGURE_VSCODE}" -eq 1 ]] || return 0

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    config_dir="${CODE_USER_DIR}/globalStorage/ms-vscode-remote.remote-containers/nameConfigs"
    config_file="${config_dir}/$(urlencode "${CONTAINER_NAME}").json"
    log_note "Dry-run: would update VS Code attached-container config at ${config_file}"
    return 0
  fi

  if [[ ! -d "${CODE_USER_DIR}" ]]; then
    log_warn "VS Code user directory was not found at ${CODE_USER_DIR}; skipping attached-container config update."
    return 0
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    log_warn "python3 was not found; skipping attached-container config update."
    return 0
  fi

  config_dir="${CODE_USER_DIR}/globalStorage/ms-vscode-remote.remote-containers/nameConfigs"
  config_file="${config_dir}/$(urlencode "${CONTAINER_NAME}").json"
  mkdir -p "${config_dir}"

  if ! written_path="$(
    python3 - "${config_file}" "${WORKDIR}" <<'PY'
import json
import shutil
import sys
import time
from pathlib import Path

config_path = Path(sys.argv[1])
workspace_folder = sys.argv[2]
required_extension = "openai.chatgpt"

data = {}
if config_path.exists():
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        backup_path = config_path.with_name(f"{config_path.name}.bak.{int(time.time())}")
        shutil.copy2(config_path, backup_path)
        data = {}

extensions = data.get("extensions")
if not isinstance(extensions, list):
    extensions = []
if required_extension not in extensions:
    extensions.append(required_extension)
data["extensions"] = extensions

settings = data.get("settings")
if not isinstance(settings, dict):
    settings = {}
settings.setdefault("terminal.integrated.defaultProfile.linux", "bash")
data["settings"] = settings
data["workspaceFolder"] = workspace_folder

config_path.write_text(json.dumps(data, indent="\t") + "\n", encoding="utf-8")
print(config_path)
PY
  )"; then
    log_warn "Failed to update the VS Code attached-container config for ${CONTAINER_NAME}."
    return 0
  fi

  log_note "Updated VS Code attached-container defaults at ${written_path}"
}

create_container() {
  local docker_command=(
    docker run -d
    --name "${CONTAINER_NAME}"
    --label "ai.attach-ubuntu.managed=true"
    --label "ai.attach-ubuntu.hostdir=${HOSTDIR}"
    --label "ai.attach-ubuntu.revision=${CONTAINER_REVISION}"
    -e VENV=/opt/attach-ubuntu/venv
  )

  if [[ -n "${GIT_METADATA_SOURCE}" ]]; then
    # shellcheck disable=SC2016
    docker_command+=(
      --label "ai.attach-ubuntu.repo-root=${GIT_METADATA_SOURCE}"
      --label "ai.attach-ubuntu.project-subpath=${PROJECT_SUBPATH}"
      --mount "type=bind,source=${GIT_METADATA_SOURCE},target=/opt/attach-ubuntu/repo-root"
      -w "/opt/attach-ubuntu/repo-root/${PROJECT_SUBPATH}"
      "${IMAGE}"
      /usr/bin/env bash -lc 'ln -sfn -- "$1" "$2" && exec sleep infinity' bash "/opt/attach-ubuntu/repo-root/${PROJECT_SUBPATH}" "${WORKDIR}"
    )
  else
    docker_command+=(
      --mount "type=bind,source=${HOSTDIR},target=${WORKDIR}"
      -w "${WORKDIR}"
      "${IMAGE}"
      sleep infinity
    )
  fi

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log_note "Dry-run: $(format_command "${docker_command[@]}")"
    return 0
  fi

  "${docker_command[@]}" >/dev/null
}

start_container() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log_note "Dry-run: $(format_command docker start "${CONTAINER_NAME}")"
    return 0
  fi

  docker start "${CONTAINER_NAME}" >/dev/null
}

stop_container() {
  if ! container_exists; then
    log_note "Container not found: ${CONTAINER_NAME}"
    return 0
  fi

  if ! container_running; then
    log_note "Container already stopped: ${CONTAINER_NAME}"
    return 0
  fi

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log_note "Dry-run: $(format_command docker stop "${CONTAINER_NAME}")"
    return 0
  fi

  docker stop "${CONTAINER_NAME}" >/dev/null
  log_success "Stopped ${CONTAINER_NAME}"
}

remove_container() {
  if ! container_exists; then
    log_note "Container not found: ${CONTAINER_NAME}"
    return 0
  fi

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log_note "Dry-run: $(format_command docker rm -f "${CONTAINER_NAME}")"
    return 0
  fi

  docker rm -f "${CONTAINER_NAME}" >/dev/null
  log_success "Removed ${CONTAINER_NAME}"
}

recreate_container() {
  if container_exists; then
    if [[ "${DRY_RUN}" -eq 1 ]]; then
      log_note "Dry-run: $(format_command docker rm -f "${CONTAINER_NAME}")"
    else
      docker rm -f "${CONTAINER_NAME}" >/dev/null
    fi
  fi

  create_container
}

ensure_container_ready() {
  local expected_mount_source="${HOSTDIR}"
  local expected_workdir="${WORKDIR}"

  if [[ -n "${GIT_METADATA_SOURCE}" ]]; then
    expected_mount_source=""
    expected_workdir="/opt/attach-ubuntu/repo-root/${PROJECT_SUBPATH}"
  fi

  if container_exists; then
    if [[ "$(mounted_source)" != "${expected_mount_source}" ]] || [[ "$(container_image)" != "${IMAGE}" ]] || [[ "$(container_workdir)" != "${expected_workdir}" ]] || [[ "$(git_metadata_mount_source)" != "${GIT_METADATA_SOURCE}" ]] || [[ "$(container_label "ai.attach-ubuntu.revision")" != "${CONTAINER_REVISION}" ]]; then
      log_note "Recreating ${CONTAINER_NAME} because its image, mount, or workdir no longer matches."
      recreate_container
      return 0
    fi

    if ! container_running; then
      log_note "Starting existing container ${CONTAINER_NAME}"
      start_container
      return 0
    fi

    log_note "Reusing running container ${CONTAINER_NAME}"
    return 0
  fi

  log_note "Creating ${CONTAINER_NAME}"
  create_container
}

build_attach_uri() {
  local payload=""
  local authority=""

  payload="$(printf '{"containerName":"%s","settings":{"context":"%s"}}' \
    "$(json_escape "/${CONTAINER_NAME}")" \
    "$(json_escape "${DOCKER_CONTEXT}")")"
  authority="$(hex_encode "${payload}")"

  printf 'vscode-remote://attached-container+%s%s\n' "${authority}" "$(urlencode_path "${WORKDIR}")"
}

print_manual_attach() {
  log_note "VS Code fallback:"
  log_note "  Dev Containers: Attach to Running Container..."
  log_note "  Choose: ${CONTAINER_NAME}"
}

request_vscode_attach() {
  local attach_uri="$1"

  [[ "${OPEN_VSCODE}" -eq 1 ]] || return 0

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log_note "Dry-run: $(format_command code --new-window --folder-uri "${attach_uri}")"
    return 0
  fi

  if code --new-window --folder-uri "${attach_uri}" >/dev/null 2>&1; then
    log_success "Requested a new VS Code Dev Containers window for ${CONTAINER_NAME}"
    return 0
  fi

  log_warn "VS Code auto-attach did not complete."
  print_manual_attach
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        show_usage
        exit 0
        ;;
      --image)
        require_argument "$1" "${2:-}"
        IMAGE="$2"
        shift 2
        ;;
      --name)
        require_argument "$1" "${2:-}"
        CONTAINER_NAME="$2"
        shift 2
        ;;
      --workdir)
        require_argument "$1" "${2:-}"
        WORKDIR="$2"
        shift 2
        ;;
      --code-user-dir)
        require_argument "$1" "${2:-}"
        CODE_USER_DIR="$2"
        shift 2
        ;;
      --no-open)
        OPEN_VSCODE=0
        shift
        ;;
      --no-bootstrap)
        BOOTSTRAP=0
        shift
        ;;
      --no-vscode-config)
        CONFIGURE_VSCODE=0
        shift
        ;;
      --stop)
        STOP_ONLY=1
        shift
        ;;
      --remove)
        REMOVE_ONLY=1
        shift
        ;;
      --dry-run)
        DRY_RUN=1
        shift
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
}

main() {
  init_output_style
  parse_args "$@"

  if [[ -z "${CONTAINER_NAME}" ]]; then
    CONTAINER_NAME="$(project_container_name)"
  fi
  validate_container_name "${CONTAINER_NAME}"

  if [[ "${STOP_ONLY}" -eq 1 && "${REMOVE_ONLY}" -eq 1 ]]; then
    log_error "Use either --stop or --remove, not both."
    exit 1
  fi

  require_command docker "to manage the Ubuntu test container"
  if [[ "${OPEN_VSCODE}" -eq 1 ]]; then
    require_command code "to request a Dev Containers window from VS Code"
  fi

  validate_local_git_state
  DOCKER_CONTEXT="$(docker context show)"
  GIT_METADATA_SOURCE="$(detect_git_metadata_source)"
  if [[ -n "${GIT_METADATA_SOURCE}" ]]; then
    PROJECT_SUBPATH="$(project_subpath_from_repo_root "${GIT_METADATA_SOURCE}")"
  fi

  if [[ "${STOP_ONLY}" -eq 1 ]]; then
    stop_container
    exit 0
  fi

  if [[ "${REMOVE_ONLY}" -eq 1 ]]; then
    remove_container
    exit 0
  fi

  PREFERRED_PYTHON="$(detect_requested_python)"
  ensure_vscode_attach_config
  ensure_container_ready
  bootstrap_container

  log_success "Container ready: ${CONTAINER_NAME}"
  log_note "Host folder: ${HOSTDIR}"
  log_note "Container folder: ${WORKDIR}"
  log_note "Docker context: ${DOCKER_CONTEXT}"
  if [[ -n "${GIT_METADATA_SOURCE}" ]]; then
    log_note "Mounted repo root: ${GIT_METADATA_SOURCE} -> /opt/attach-ubuntu/repo-root"
    log_note "Workspace link: ${WORKDIR} -> /opt/attach-ubuntu/repo-root/${PROJECT_SUBPATH}"
  fi
  if [[ -n "${PREFERRED_PYTHON}" ]]; then
    log_note "Preferred project Python: ${PREFERRED_PYTHON}"
  fi

  if [[ "${OPEN_VSCODE}" -eq 1 ]]; then
    request_vscode_attach "$(build_attach_uri)"
    log_note "If the new window does not connect, use the manual attach fallback."
    print_manual_attach
  else
    print_manual_attach
  fi
}

main "$@"
