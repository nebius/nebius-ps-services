#!/usr/bin/env bash
set -euo pipefail

umask 077

usage() {
  cat <<'EOF'
Usage: ensure-local-config.sh [--check | --apply] [options]

Check or apply the local Codex/Grafana MCP wiring for Nebius-managed Grafana.
The setup pins one validated human Nebius CLI profile and stores a private
profile-to-user binding. The default is --check and does not modify local
configuration.

Options:
  --check                 Inspect only. This is the default.
  --apply                 Create missing private identity/token state and a
                          missing Codex MCP registration; set its bounded
                          startup timeout.
  --replace-existing      With --apply, remove and re-add an existing mismatched
                          MCP server using the canonical human-profile wiring.
  --user-profile NAME     Human Nebius CLI profile to pin. Defaults to
                          NEBIUS_GRAFANA_USER_PROFILE, then `nebius profile active`.
  --mcp-server-name NAME  Codex MCP server name. Default: grafana-nebius.
  --grafana-url URL       Must be https://grafana.nebius.dev/.
  --state-root PATH       Private state root. Default:
                          $HOME/.config/grafana-mcp/nebius.
  --wrapper-path PATH     Wrapper script path. Default: this skill's wrapper.
  --skip-codex            Do not inspect or update Codex MCP config.
  -h, --help              Show this help.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '%s\n' "$*"
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'error: required command not found on PATH: %s\n' "$1" >&2
    exit 127
  fi
}

mode="check"
replace_existing="false"
skip_codex="false"
user_profile="${NEBIUS_GRAFANA_USER_PROFILE:-}"
mcp_server_name="${MCP_SERVER_NAME:-grafana-nebius}"
grafana_url="${GRAFANA_URL:-https://grafana.nebius.dev/}"
state_root="${NEBIUS_GRAFANA_STATE_ROOT:-${HOME}/.config/grafana-mcp/nebius}"
script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
wrapper_path="${WRAPPER_PATH:-${script_dir}/run-nebius-grafana-mcp.sh}"
codex_startup_timeout_seconds="300"
active_tmp_file=""
drift_found="false"

cleanup() {
  if [ -n "$active_tmp_file" ]; then
    rm -f -- "$active_tmp_file"
    active_tmp_file=""
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)
      mode="check"
      ;;
    --apply)
      mode="apply"
      ;;
    --replace-existing)
      replace_existing="true"
      ;;
    --user-profile)
      shift
      user_profile="${1:?missing value for --user-profile}"
      ;;
    --mcp-server-name)
      shift
      mcp_server_name="${1:?missing value for --mcp-server-name}"
      ;;
    --grafana-url)
      shift
      grafana_url="${1:?missing value for --grafana-url}"
      ;;
    --state-root)
      shift
      state_root="${1:?missing value for --state-root}"
      ;;
    --wrapper-path)
      shift
      wrapper_path="${1:?missing value for --wrapper-path}"
      ;;
    --skip-codex)
      skip_codex="true"
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      printf 'error: unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$replace_existing" = "true" ] && [ "$mode" != "apply" ]; then
  die "--replace-existing requires --apply"
fi

validate_nebius_grafana_url() {
  case "$grafana_url" in
    https://grafana.nebius.dev | https://grafana.nebius.dev/)
      return
      ;;
  esac

  die "this helper only supports GRAFANA_URL=https://grafana.nebius.dev/; configure external Grafana with a separate service-account-token setup"
}

validate_name() {
  local label="$1"
  local value="$2"

  if [[ ! "$value" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ ]]; then
    die "$label contains unsupported characters"
  fi
}

validate_profile_name() {
  validate_name "Nebius profile name" "$user_profile"
  case "$user_profile" in
    codex-agent-*)
      die "the selected Nebius profile is an agent profile; select a human user profile"
      ;;
  esac
}

validate_mcp_server_name() {
  if [[ ! "$mcp_server_name" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$ ]]; then
    die "MCP server name contains unsupported characters"
  fi
}

validate_normalized_absolute_path() {
  local label="$1"
  local path="$2"
  local normalized

  case "$path" in
    /*) ;;
    *) die "$label must be an absolute path" ;;
  esac
  if [[ "$path" =~ [[:cntrl:]] ]]; then
    die "$label contains control characters"
  fi
  normalized="$(
    python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$path"
  )"
  [ "$path" = "$normalized" ] \
    || die "$label must be lexically normalized without dot segments"
}

run_nebius_clean() {
  env \
    -u CODEX_NEBIUS_PROJECT_ID \
    -u CODEX_NEBIUS_TOKEN_HELPER \
    -u NEBIUS_AUTH_CREDENTIALS_FILE \
    -u NEBIUS_IAM_TOKEN \
    -u NEBIUS_IMPERSONATE_SERVICE_ACCOUNT_ID \
    -u NEBIUS_PROFILE \
    -u NEBIUS_PROJECT_ID \
    -u TOKEN \
    nebius "$@"
}

sanitize_browser_auth_stderr() {
  sed -E \
    's#https://auth[.]nebius[.]com/[^[:space:]]+#<browser-auth-url-redacted>#g'
}

run_nebius_browser_auth() {
  run_nebius_clean "$@" \
    2> >(sanitize_browser_auth_stderr >&2)
}

resolve_active_profile() {
  run_nebius_clean profile active 2>/dev/null \
    | awk 'NF { print; exit }'
}

resolve_profile_user_id() {
  local -a auth_args=(
    --no-browser
    --auth-timeout 10s
    --timeout 20s
    --retries 1
  )
  local identity_json
  local user_id

  if [ "$mode" = "apply" ]; then
    auth_args=(
      --auth-timeout 120s
      --timeout 20s
      --retries 1
    )
    if ! identity_json="$(
      run_nebius_browser_auth iam whoami \
        --profile "$user_profile" \
        --format json \
        "${auth_args[@]}" </dev/null
    )"; then
      die "Nebius profile '$user_profile' could not be validated after browser authentication"
    fi
  elif ! identity_json="$(
    run_nebius_clean iam whoami \
      --profile "$user_profile" \
      --format json \
      "${auth_args[@]}" </dev/null 2>/dev/null
  )"; then
    die "Nebius profile '$user_profile' could not be validated non-interactively"
  fi

  if ! user_id="$(
    printf '%s' "$identity_json" \
      | python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except (TypeError, ValueError):
    raise SystemExit(2)

user = payload.get("user_profile")
if not isinstance(user, dict):
    raise SystemExit(3)
if "service_account_profile" in payload or "anonymous_profile" in payload:
    raise SystemExit(4)
user_id = user.get("id")
if not isinstance(user_id, str) or not user_id:
    raise SystemExit(5)
print(user_id)
'
  )"; then
    die "Nebius profile '$user_profile' is not a validated human user profile"
  fi

  if [[ ! "$user_id" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$ ]]; then
    die "Nebius profile '$user_profile' returned an unsupported user ID"
  fi
  printf '%s\n' "$user_id"
}

file_mode() {
  stat -f '%OLp' "$1" 2>/dev/null || stat -c '%a' "$1"
}

file_uid() {
  stat -f '%u' "$1" 2>/dev/null || stat -c '%u' "$1"
}

validate_owned_directory() {
  local path="$1"

  [ -L "$path" ] && die "private state directory must not be a symlink: $path"
  [ -d "$path" ] || die "private state path is not a directory: $path"
  [ "$(file_uid "$path")" = "$(id -u)" ] \
    || die "private state directory is not owned by the current user: $path"
}

validate_private_state_dir() {
  local state_dir="$1"

  python3 - "$state_dir" "${HOME:?HOME is required}" <<'PY'
import os
import stat
import sys

state_dir = os.path.abspath(sys.argv[1])
home = os.path.abspath(sys.argv[2])
try:
    if os.path.commonpath((state_dir, home)) != home or state_dir == home:
        raise ValueError
except ValueError:
    raise SystemExit(2)

relative = os.path.relpath(state_dir, home)
current = home
for part in relative.split(os.sep):
    current = os.path.join(current, part)
    try:
        info = os.lstat(current)
    except OSError:
        raise SystemExit(6)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SystemExit(3)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise SystemExit(4)

try:
    leaf_mode = stat.S_IMODE(os.lstat(state_dir).st_mode)
except OSError:
    raise SystemExit(6)
if leaf_mode != 0o700:
    raise SystemExit(5)
PY
}

validate_existing_state_prefix() {
  local target="$1"

  python3 - "$target" "${HOME:?HOME is required}" <<'PY'
import os
import stat
import sys

target = sys.argv[1]
home = os.path.abspath(sys.argv[2])
relative = os.path.relpath(target, home)
current = home
for part in relative.split(os.sep):
    current = os.path.join(current, part)
    try:
        info = os.lstat(current)
    except FileNotFoundError:
        break
    except OSError:
        raise SystemExit(2)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SystemExit(3)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise SystemExit(4)
PY
}

prepare_private_state_dir() {
  local path

  case "$state_dir" in
    "${HOME}/"*) ;;
    *) die "private state must be an absolute path below HOME" ;;
  esac

  validate_existing_state_prefix "$state_dir" \
    || die "private state has an unsafe existing path component"

  if [ ! -e "$state_dir" ] && [ ! -L "$state_dir" ]; then
    if [ "$mode" = "check" ]; then
      info "identity: profile-scoped state is missing; --apply would create it"
      drift_found="true"
      return 1
    fi
    mkdir -p -- "$state_dir"
  fi

  for path in "$state_root" "${state_root}/${mcp_server_name}" "$state_dir"; do
    validate_owned_directory "$path"
    if [ "$mode" = "apply" ] && [ "$(file_mode "$path")" != "700" ]; then
      chmod 700 "$path" \
        || die "unable to enforce mode 0700 on private state directory: $path"
    fi
  done

  validate_private_state_dir "$state_dir" \
    || die "private state path is unsafe; require owned non-symlink directories without group/world write access and a mode-0700 leaf"
}

validate_private_file() {
  local path="$1"

  [ -L "$path" ] && die "identity binding must not be a symlink"
  [ -f "$path" ] || die "identity binding is not a regular file"
  [ "$(file_uid "$path")" = "$(id -u)" ] \
    || die "identity binding is not owned by the current user"
  [ "$(file_mode "$path")" = "600" ] \
    || die "identity binding must have mode 0600"
}

read_binding_field() {
  local name="$1"
  sed -n "s/^${name}=//p" "$identity_file"
}

binding_matches() {
  local stored_profile
  local stored_user_id
  local stored_version

  [ "$(awk 'END { print NR }' "$identity_file")" = "3" ] || return 1
  stored_version="$(read_binding_field version)"
  stored_profile="$(read_binding_field profile)"
  stored_user_id="$(read_binding_field user_id)"

  [ "$stored_version" = "1" ] \
    && [ "$stored_profile" = "$user_profile" ] \
    && [ "$stored_user_id" = "$profile_user_id" ]
}

write_identity_binding() {
  active_tmp_file="$(mktemp "${identity_file}.tmp.XXXXXX")"
  chmod 600 "$active_tmp_file" \
    || die "unable to enforce mode 0600 on temporary identity binding"
  {
    printf 'version=1\n'
    printf 'profile=%s\n' "$user_profile"
    printf 'user_id=%s\n' "$profile_user_id"
  } >"$active_tmp_file"
  mv -f -- "$active_tmp_file" "$identity_file"
  active_tmp_file=""
  chmod 600 "$identity_file" \
    || die "unable to enforce mode 0600 on identity binding"
  validate_private_file "$identity_file"
}

ensure_identity_binding() {
  if ! prepare_private_state_dir; then
    return
  fi

  if [ -e "$identity_file" ] || [ -L "$identity_file" ]; then
    validate_private_file "$identity_file"
    if ! binding_matches; then
      die "the pinned profile no longer matches its private identity binding; create a distinct human profile and rerun setup"
    fi
    info "identity: validated pinned human profile binding"
    return
  fi

  if [ "$mode" = "check" ]; then
    info "identity: binding is missing; --apply would create it"
    drift_found="true"
    return
  fi

  write_identity_binding
  info "identity: created private pinned human profile binding"
}

check_binary() {
  if command -v mcp-grafana >/dev/null 2>&1; then
    info "binary: mcp-grafana found at $(command -v mcp-grafana)"
  else
    if [ "$mode" = "apply" ]; then
      die "mcp-grafana is not on PATH; install and validate it before applying Codex MCP config"
    fi
    warn "binary: mcp-grafana not found on PATH; install it before starting Codex MCP"
    drift_found="true"
    return
  fi

  if ! command -v strings >/dev/null 2>&1 \
    || ! strings "$(command -v mcp-grafana)" 2>/dev/null \
      | grep -F "GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE" >/dev/null; then
    if [ "$mode" = "apply" ]; then
      die "installed mcp-grafana lacks required token-file authentication support; update it before applying Nebius-managed Grafana config"
    fi
    warn "binary: installed mcp-grafana lacks required token-file authentication support"
    drift_found="true"
  fi
}

token_file_is_fresh() {
  [ -f "$token_file" ] \
    && [ ! -L "$token_file" ] \
    && [ "$(file_uid "$token_file")" = "$(id -u)" ] \
    && [ "$(file_mode "$token_file")" = "600" ] \
    && python3 - "$token_file" <<'PY'
from pathlib import Path
import sys
import time

path = Path(sys.argv[1])
value = path.read_bytes()
lines = value.splitlines()
age = time.time() - path.stat().st_mtime
if (
    len(lines) != 1
    or not lines[0].strip()
    or b"\x00" in lines[0]
    or age < 0
    or age > 3600
):
    raise SystemExit(1)
PY
}

ensure_startup_token() {
  if token_file_is_fresh; then
    info "token: reusable pinned-human startup token is fresh"
    return
  fi
  if [ "$mode" = "check" ]; then
    info "token: fresh profile-scoped startup token is missing; --apply would mint it"
    drift_found="true"
    return
  fi

  GRAFANA_URL="$grafana_url" \
    GRAFANA_TOKEN_FILE="$token_file" \
    NEBIUS_GRAFANA_IDENTITY_FILE="$identity_file" \
    NEBIUS_GRAFANA_USER_PROFILE="$user_profile" \
    "$wrapper_path" --refresh-token-only >/dev/null
  token_file_is_fresh \
    || die "wrapper did not produce a valid fresh profile-scoped startup token"
  info "token: minted fresh pinned-human startup token"
}

trusted_wrapper_path() {
  local path="$1"

  python3 - "$path" "${HOME:?HOME is required}" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
home = os.path.abspath(sys.argv[2])
try:
    if os.path.commonpath((path, home)) != home or path == home:
        raise ValueError
except ValueError:
    raise SystemExit(2)

relative = os.path.relpath(path, home)
current = home
components = [home]
for part in relative.split(os.sep):
    current = os.path.join(current, part)
    components.append(current)

for index, component in enumerate(components):
    try:
        info = os.lstat(component)
    except OSError:
        raise SystemExit(3)
    if stat.S_ISLNK(info.st_mode):
        raise SystemExit(4)
    is_leaf = index == len(components) - 1
    if is_leaf:
        if not stat.S_ISREG(info.st_mode):
            raise SystemExit(5)
    elif not stat.S_ISDIR(info.st_mode):
        raise SystemExit(6)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise SystemExit(7)
PY
}

trusted_equivalent_wrapper_path() {
  local path="$1"
  local repo_root
  local relative_path

  case "$path" in
    "$HOME/.agents/skills/install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh" | \
      "$HOME/.codex/skills/install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh")
      trusted_wrapper_path "$path"
      ;;
    *)
      case "$path" in
        "$HOME"/*/skills/install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh)
          command -v git >/dev/null 2>&1 || return 1
          repo_root="$(git -C "$(dirname -- "$path")" rev-parse --show-toplevel 2>/dev/null)" \
            || return 1
          case "$repo_root" in
            "$HOME"/*)
              relative_path="${path#"${repo_root}/"}"
              [ "$relative_path" = "skills/install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh" ] \
                && trusted_wrapper_path "$path"
              ;;
            *)
              return 1
              ;;
          esac
          ;;
        *)
          return 1
          ;;
      esac
      ;;
  esac
}

codex_config_digest() {
  local codex_config="${CODEX_HOME:-${HOME}/.codex}/config.toml"

  python3 - "$codex_config" <<'PY'
import hashlib
import os
from pathlib import Path
import stat
import sys

config_path = Path(sys.argv[1])
if config_path.is_symlink():
    raise SystemExit("Codex config must not be a symbolic link")

flags = os.O_RDONLY
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
fd = os.open(config_path, flags)
try:
    config_stat = os.fstat(fd)
    if not stat.S_ISREG(config_stat.st_mode):
        raise SystemExit("Codex config is not a regular file")
    if config_stat.st_uid != os.getuid():
        raise SystemExit("Codex config is not owned by the current user")
    with os.fdopen(fd, "rb", closefd=False) as stream:
        config_bytes = stream.read()
finally:
    os.close(fd)

print(hashlib.sha256(config_bytes).hexdigest())
PY
}

set_codex_mcp_startup_timeout() {
  local expected_digest="$1"
  local codex_config="${CODEX_HOME:-${HOME}/.codex}/config.toml"

  python3 - \
    "$codex_config" \
    "$mcp_server_name" \
    "$codex_startup_timeout_seconds" \
    "$expected_digest" <<'PY'
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

config_path = Path(sys.argv[1])
server_name = sys.argv[2]
timeout = sys.argv[3]
expected_digest = sys.argv[4]

if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", server_name):
    raise SystemExit("invalid MCP server name")
if not timeout.isdigit() or int(timeout) <= 0:
    raise SystemExit("invalid MCP startup timeout")
if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
    raise SystemExit("invalid expected Codex config digest")
if config_path.is_symlink():
    raise SystemExit("Codex config must not be a symbolic link")

flags = os.O_RDONLY
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
fd = os.open(config_path, flags)
try:
    original_stat = os.fstat(fd)
    if not stat.S_ISREG(original_stat.st_mode):
        raise SystemExit("Codex config is not a regular file")
    if original_stat.st_uid != os.getuid():
        raise SystemExit("Codex config is not owned by the current user")
    with os.fdopen(fd, "rb", closefd=False) as stream:
        original = stream.read()
finally:
    os.close(fd)

if hashlib.sha256(original).hexdigest() != expected_digest:
    raise SystemExit("Codex config changed after structured inspection")

try:
    text = original.decode("utf-8")
except UnicodeDecodeError as exc:
    raise SystemExit("Codex config is not valid UTF-8") from exc

header_pattern = re.compile(
    rf"(?m)^[ \t]*\[mcp_servers\.{re.escape(server_name)}\][ \t]*(?:#.*)?(?:\n|$)"
)
headers = list(header_pattern.finditer(text))
if len(headers) != 1:
    raise SystemExit("expected exactly one canonical MCP server table")

header = headers[0]
next_table = re.search(r"(?m)^[ \t]*\[", text[header.end():])
section_end = (
    header.end() + next_table.start()
    if next_table
    else len(text)
)
section = text[header.end():section_end]
timeout_pattern = re.compile(
    r"(?m)^[ \t]*startup_timeout_sec[ \t]*=.*(?:\n|$)"
)
matches = list(timeout_pattern.finditer(section))
if len(matches) > 1:
    raise SystemExit("duplicate MCP startup timeout settings")

timeout_line = f"startup_timeout_sec = {timeout}.0\n"
if matches:
    match = matches[0]
    updated_section = (
        section[:match.start()] + timeout_line + section[match.end():]
    )
    updated = text[:header.end()] + updated_section + text[section_end:]
else:
    updated = text[:header.end()] + timeout_line + text[header.end():]

updated_bytes = updated.encode("utf-8")
if updated_bytes == original:
    raise SystemExit(0)

parent = config_path.parent
temp_fd, temp_name = tempfile.mkstemp(
    prefix=f".{config_path.name}.",
    dir=parent,
)
try:
    os.fchmod(temp_fd, stat.S_IMODE(original_stat.st_mode))
    with os.fdopen(temp_fd, "wb", closefd=False) as stream:
        stream.write(updated_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    os.close(temp_fd)
    temp_fd = -1

    current_fd = os.open(config_path, flags)
    try:
        current_stat = os.fstat(current_fd)
        if not stat.S_ISREG(current_stat.st_mode):
            raise SystemExit("Codex config is no longer a regular file")
        if current_stat.st_uid != os.getuid():
            raise SystemExit("Codex config ownership changed concurrently")
        with os.fdopen(current_fd, "rb", closefd=False) as stream:
            current = stream.read()
    finally:
        os.close(current_fd)
    if hashlib.sha256(current).hexdigest() != expected_digest:
        raise SystemExit("Codex config changed concurrently before replacement")

    os.replace(temp_name, config_path)
    temp_name = ""
    directory_fd = os.open(parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    if temp_fd >= 0:
        os.close(temp_fd)
    if temp_name:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
PY
}

add_codex_mcp() {
  local added_config
  local expected_digest

  if ! codex mcp add "$mcp_server_name" \
    --env "GRAFANA_URL=${grafana_url}" \
    --env "GRAFANA_TOKEN_FILE=${token_file}" \
    --env "NEBIUS_GRAFANA_IDENTITY_FILE=${identity_file}" \
    --env "NEBIUS_GRAFANA_USER_PROFILE=${user_profile}" \
    -- "$wrapper_path" --disable-write --max-loki-log-limit 20; then
    return 1
  fi
  expected_digest="$(codex_config_digest)" || {
    warn "codex: unable to bind the added registration to its config bytes; leaving the added entry intact for inspection"
    return 1
  }
  added_config="$(
    codex mcp get "$mcp_server_name" --json 2>/dev/null
  )" || {
    warn "codex: unable to verify the added MCP registration before setting its timeout; leaving the added entry intact for inspection"
    return 1
  }
  if ! printf '%s' "$added_config" \
    | registration_values_match "$wrapper_path" missing; then
    warn "codex: added MCP registration did not have the exact canonical pre-timeout values; leaving it intact for inspection"
    return 1
  fi
  if ! set_codex_mcp_startup_timeout "$expected_digest"; then
    warn "codex: unable to set the bounded MCP startup timeout; leaving the added entry intact for inspection"
    return 1
  fi
  if ! verify_codex_mcp_registration; then
    warn "codex: canonical MCP registration failed structured post-write verification; leaving the unverifiable entry intact to avoid deleting concurrent state"
    return 1
  fi
  info "codex: added canonical MCP server '$mcp_server_name'"
}

print_add_command() {
  printf '%q --apply --user-profile %q --mcp-server-name %q --grafana-url %q --state-root %q --wrapper-path %q\n' \
    "${BASH_SOURCE[0]}" \
    "$user_profile" \
    "$mcp_server_name" \
    "$grafana_url" \
    "$state_root" \
    "$wrapper_path"
}

registration_command() {
  python3 -c '
import json
import sys

payload = json.load(sys.stdin)
transport = payload.get("transport")
if not isinstance(transport, dict):
    raise SystemExit(2)
command = transport.get("command")
if not isinstance(command, str) or not command:
    raise SystemExit(3)
print(command)
'
}

registration_values_match() {
  local expected_command="${1:-}"
  local timeout_policy="${2:-canonical}"

  python3 -c '
import json
import sys

(
    expected_command,
    expected_url,
    expected_token,
    expected_identity,
    expected_profile,
    expected_timeout,
    timeout_policy,
) = sys.argv[1:]
payload = json.load(sys.stdin)
transport = payload.get("transport")
if not isinstance(transport, dict) or transport.get("type") != "stdio":
    raise SystemExit(2)
if payload.get("enabled") is not True:
    raise SystemExit(3)
if expected_command and transport.get("command") != expected_command:
    raise SystemExit(4)
for key in (
    "disabled_reason",
    "enabled_tools",
    "disabled_tools",
):
    if payload.get(key) is not None:
        raise SystemExit(5)
startup_timeout = payload.get("startup_timeout_sec")
if timeout_policy == "canonical":
    if startup_timeout != float(expected_timeout):
        raise SystemExit(6)
elif timeout_policy == "missing":
    if startup_timeout is not None:
        raise SystemExit(6)
else:
    raise SystemExit(11)
if payload.get("tool_timeout_sec") is not None:
    raise SystemExit(7)
if transport.get("cwd") is not None or transport.get("env_vars") not in (None, []):
    raise SystemExit(8)
if transport.get("args") != [
    "--disable-write",
    "--max-loki-log-limit",
    "20",
]:
    raise SystemExit(9)
expected_env = {
    "GRAFANA_URL": expected_url,
    "GRAFANA_TOKEN_FILE": expected_token,
    "NEBIUS_GRAFANA_IDENTITY_FILE": expected_identity,
    "NEBIUS_GRAFANA_USER_PROFILE": expected_profile,
}
if transport.get("env") != expected_env:
    raise SystemExit(10)
' \
    "$expected_command" \
    "$grafana_url" \
    "$token_file" \
    "$identity_file" \
    "$user_profile" \
    "$codex_startup_timeout_seconds" \
    "$timeout_policy"
}

verify_codex_mcp_registration() {
  local registered_config

  registered_config="$(
    codex mcp get "$mcp_server_name" --json 2>/dev/null
  )" || return 1
  printf '%s' "$registered_config" \
    | registration_values_match "$wrapper_path"
}

registration_replacement_shape_supported() {
  local expected_name="$1"

  python3 -c '
import json
import sys

expected_name = sys.argv[1]
payload = json.load(sys.stdin)
transport = payload.get("transport")
if not isinstance(transport, dict) or transport.get("type") != "stdio":
    raise SystemExit(2)
if set(payload) != {
    "name",
    "enabled",
    "disabled_reason",
    "transport",
    "enabled_tools",
    "disabled_tools",
    "startup_timeout_sec",
    "tool_timeout_sec",
}:
    raise SystemExit(3)
if set(transport) != {
    "type",
    "command",
    "args",
    "env",
    "env_vars",
    "cwd",
}:
    raise SystemExit(4)
if payload.get("name") != expected_name:
    raise SystemExit(5)
if payload.get("enabled") is not True:
    raise SystemExit(6)
for key in (
    "disabled_reason",
    "enabled_tools",
    "disabled_tools",
):
    if payload.get(key) is not None:
        raise SystemExit(7)
startup_timeout = payload.get("startup_timeout_sec")
if startup_timeout not in (None, float(sys.argv[2])):
    raise SystemExit(8)
if payload.get("tool_timeout_sec") is not None:
    raise SystemExit(8)
if transport.get("cwd") is not None or transport.get("env_vars") not in (None, []):
    raise SystemExit(9)
if not isinstance(transport.get("command"), str) or not transport["command"]:
    raise SystemExit(10)
args = transport.get("args")
if not isinstance(args, list) or not all(
    isinstance(value, str) for value in args
):
    raise SystemExit(11)
env = transport.get("env")
if not isinstance(env, dict) or not all(
    isinstance(key, str) and isinstance(value, str)
    for key, value in env.items()
):
    raise SystemExit(12)
' "$expected_name" "$codex_startup_timeout_seconds"
}

registration_json_matches() {
  local expected_json="$1"

  python3 -c '
import json
import os
import sys

with os.fdopen(3) as expected_stream:
    expected = json.load(expected_stream)
actual = json.load(sys.stdin)
if actual != expected:
    raise SystemExit(1)
' 3<<<"$expected_json"
}

ensure_codex_mcp() {
  local current_config
  local expected_digest
  local existing_command
  local existing_config
  local command_acceptable="true"
  local mismatch="false"

  if ! command -v codex >/dev/null 2>&1; then
    warn "codex: codex CLI not found on PATH; cannot inspect MCP config"
    drift_found="true"
    return
  fi

  if ! codex mcp get "$mcp_server_name" >/dev/null 2>&1; then
    if [ "$mode" = "check" ]; then
      info "codex: MCP server '$mcp_server_name' is missing; --apply would add it"
      print_add_command
      drift_found="true"
      return
    fi
    add_codex_mcp \
      || die "unable to add canonical MCP server '$mcp_server_name'"
    return
  fi

  if ! existing_config="$(
    codex mcp get "$mcp_server_name" --json 2>/dev/null
  )"; then
    die "Codex cannot expose structured MCP config for safe value comparison; update Codex before replacing this server"
  fi
  if ! existing_command="$(
    printf '%s' "$existing_config" | registration_command
  )"; then
    existing_command=""
  fi

  info "codex: MCP server '$mcp_server_name' already exists; comparing structured values without printing them"

  if [ "$existing_command" != "$wrapper_path" ]; then
    if [ -n "$existing_command" ] \
      && [ -f "$existing_command" ] \
      && [ -f "$wrapper_path" ] \
      && cmp -s "$existing_command" "$wrapper_path" \
      && trusted_equivalent_wrapper_path "$existing_command"; then
      info "codex: existing server uses a trusted byte-identical wrapper"
    else
      warn "codex: existing server does not use the trusted canonical wrapper"
      command_acceptable="false"
      mismatch="true"
    fi
  fi

  if ! printf '%s' "$existing_config" \
    | registration_values_match; then
    warn "codex: existing server does not match the exact read-only arguments and pinned non-secret binding values"
    mismatch="true"
  fi

  if [ "$mismatch" = "false" ]; then
    info "codex: MCP server '$mcp_server_name' matches the pinned-human setup"
    return
  fi

  if [ "$command_acceptable" = "true" ] \
    && printf '%s' "$existing_config" \
      | registration_values_match "" missing; then
    if [ "$mode" = "check" ]; then
      info "codex: MCP server '$mcp_server_name' needs the bounded startup timeout; --apply would set it without replacing the registration"
      drift_found="true"
      return
    fi
    expected_digest="$(codex_config_digest)" \
      || die "unable to bind the inspected MCP registration to the exact Codex config bytes"
    current_config="$(
      codex mcp get "$mcp_server_name" --json 2>/dev/null
    )" || die "Codex MCP registration disappeared before setting the startup timeout"
    printf '%s' "$current_config" \
      | registration_json_matches "$existing_config" \
      || die "Codex MCP registration changed concurrently before setting the startup timeout"
    set_codex_mcp_startup_timeout "$expected_digest" \
      || die "unable to set the bounded MCP startup timeout"
    current_config="$(
      codex mcp get "$mcp_server_name" --json 2>/dev/null
    )" || die "Codex MCP registration disappeared after setting the startup timeout"
    printf '%s' "$current_config" \
      | registration_values_match "$existing_command" \
      || die "Codex MCP registration failed verification after setting the startup timeout"
    info "codex: set the bounded startup timeout without replacing the MCP registration"
    return
  fi

  if [ "$mode" = "check" ]; then
    warn "codex: migration required; rerun --apply --replace-existing after reviewing the exact server name and paths"
    drift_found="true"
    return
  fi
  if [ "$replace_existing" != "true" ]; then
    die "existing MCP server requires replacement; rerun with --apply --replace-existing after explicit review"
  fi
  if ! printf '%s' "$existing_config" \
    | registration_replacement_shape_supported "$mcp_server_name"; then
    die "existing MCP server has non-default or malformed structured settings; replace it manually after backing up Codex config"
  fi
  if ! current_config="$(
    codex mcp get "$mcp_server_name" --json 2>/dev/null
  )"; then
    die "Codex MCP registration disappeared before replacement; inspect the named entry before retrying"
  fi
  if ! printf '%s' "$current_config" \
    | registration_json_matches "$existing_config"; then
    die "Codex MCP registration changed concurrently after inspection; refusing to remove or overwrite it"
  fi

  codex mcp remove "$mcp_server_name" \
    || die "unable to remove mismatched MCP server '$mcp_server_name'"
  info "codex: removed mismatched MCP server '$mcp_server_name'"
  if add_codex_mcp; then
    return
  fi
  die "canonical replacement did not verify after removing the mismatched entry; no prior values were replayed, so inspect the named entry and rerun --apply after correcting the add failure"
}

require_command nebius
require_command python3
validate_nebius_grafana_url
validate_mcp_server_name
validate_normalized_absolute_path "private state root" "$state_root"
validate_normalized_absolute_path "wrapper path" "$wrapper_path"

if [ -z "$user_profile" ]; then
  user_profile="$(resolve_active_profile || true)"
fi
[ -n "$user_profile" ] \
  || die "no active Nebius CLI profile was found; pass --user-profile with a human profile"
validate_profile_name
profile_user_id="$(resolve_profile_user_id)"

state_dir="${state_root}/${mcp_server_name}/${user_profile}"
identity_file="${state_dir}/identity"
token_file="${state_dir}/token"

[ -L "$wrapper_path" ] && die "wrapper path must not be a symlink"
[ -f "$wrapper_path" ] || die "wrapper path is not a regular file: $wrapper_path"
[ -x "$wrapper_path" ] || die "wrapper path is not executable: $wrapper_path"
trusted_wrapper_path "$wrapper_path" \
  || die "wrapper path must be below HOME with owned, non-symlinked, non-writable path components"

info "mode: $mode"
info "mcp server: $mcp_server_name"
info "grafana url: $grafana_url"
info "human profile: $user_profile"
info "identity binding: $identity_file"
info "token file: $token_file"
info "wrapper: $wrapper_path"

ensure_identity_binding
check_binary
ensure_startup_token

if [ "$skip_codex" = "false" ]; then
  ensure_codex_mcp
else
  info "codex: skipped"
fi

if [ "$drift_found" = "true" ]; then
  exit 1
fi
