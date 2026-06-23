#!/usr/bin/env bash
set -euo pipefail

SA_NAME="codex-agent-sa"
ROLE="editor"
INSTALL_HOOK="false"
REPAIR="false"
DRY_RUN="false"
ENDPOINT="api.nebius.cloud"

TENANT_ID=""
PROJECT_ID=""
PROJECT_NAME=""
COMMAND=""
LOCK_DIRS=()

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf 'agent-nebius-auth: %s\n' "$*" >&2
}

usage() {
  cat >&2 <<'EOF'
Usage:
  bash scripts/agent-nebius-auth.sh ensure \
    --tenant-id <tenant_id> \
    --project-id <project_id> \
    --project-name <project_name> \
    [--service-account-name <name>] \
    [--role <role>] \
    [--repair] \
    [--install-hook] \
    [--dry-run]

Creates or repairs local Codex Agent Nebius service-account auth. Runtime token
injection is handled by the installed PreToolUse hook, not by this script.
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

is_dry_run() {
  [[ "$DRY_RUN" == "true" ]]
}

run() {
  if is_dry_run; then
    printf 'DRY RUN:' >&2
    printf ' %q' "$@" >&2
    printf '\n' >&2
    return 0
  fi
  "$@"
}

cleanup_file() {
  local file="$1"
  [[ -n "$file" && -f "$file" ]] || return 0
  unlink "$file"
}

cleanup_locks() {
  local lock_dir=""
  for lock_dir in "${LOCK_DIRS[@]}"; do
    rmdir "$lock_dir" 2>/dev/null || true
  done
}

acquire_lock() {
  local lock_dir="$1"
  local attempt=0

  if is_dry_run; then
    log "Would acquire lock: $lock_dir"
    return 0
  fi

  while [[ "$attempt" -lt 150 ]]; do
    if mkdir "$lock_dir" 2>/dev/null; then
      LOCK_DIRS+=("$lock_dir")
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 0.2
  done

  die "Timed out waiting for lock: $lock_dir"
}

slugify() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//'
}

json_get_id() {
  jq -r '.metadata.id // .id // .items[0].metadata.id // empty'
}

validate_inputs() {
  [[ "$COMMAND" == "ensure" ]] || die "Only the 'ensure' command is supported."
  [[ -n "$TENANT_ID" ]] || die "--tenant-id is required."
  [[ -n "$PROJECT_ID" ]] || die "--project-id is required."
  [[ -n "$PROJECT_NAME" ]] || die "--project-name is required."
  [[ "$TENANT_ID" =~ ^[A-Za-z0-9._:-]+$ ]] || die "--tenant-id contains unsupported characters."
  [[ "$PROJECT_ID" =~ ^[A-Za-z0-9._:-]+$ ]] || die "--project-id contains unsupported characters."
  [[ "$SA_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$ ]] || die "--service-account-name is not a safe Nebius resource name."
  [[ "$ROLE" =~ ^[A-Za-z0-9_.-]+$ ]] || die "--role contains unsupported characters."
}

safe_chmod_credential() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  run chmod 600 "$file"
}

ensure_nebius_dir() {
  run mkdir -p "$HOME/.nebius"
  run chmod 700 "$HOME/.nebius"
}

human_session_available() {
  if is_dry_run; then
    return 1
  fi
  nebius iam get-access-token >/dev/null 2>&1
}

ensure_human_session() {
  if is_dry_run; then
    log "Would verify the current human/default Nebius session."
    return 0
  fi
  nebius iam get-access-token >/dev/null \
    || die "Current Nebius human/default session is not valid. Re-authenticate, then rerun."
}

get_or_create_service_account() {
  local sa_id=""

  if is_dry_run; then
    log "Would get or create service account '$SA_NAME' in project '$PROJECT_ID'."
    printf '%s\n' "dry-run-service-account-id"
    return 0
  fi

  sa_id="$(
    nebius iam service-account get-by-name \
      --name "$SA_NAME" \
      --parent-id "$PROJECT_ID" \
      --format json 2>/dev/null | json_get_id || true
  )"

  if [[ -n "$sa_id" ]]; then
    printf '%s\n' "$sa_id"
    return 0
  fi

  log "Creating service account: $SA_NAME"
  sa_id="$(
    nebius iam service-account create \
      --name "$SA_NAME" \
      --parent-id "$PROJECT_ID" \
      --format json | json_get_id
  )"
  [[ -n "$sa_id" ]] || die "Failed to create service account."
  printf '%s\n' "$sa_id"
}

get_or_create_group() {
  local group_id=""

  if is_dry_run; then
    log "Would get or create group '$GROUP_NAME' under tenant '$TENANT_ID'."
    printf '%s\n' "dry-run-group-id"
    return 0
  fi

  group_id="$(
    nebius iam group get-by-name \
      --name "$GROUP_NAME" \
      --parent-id "$TENANT_ID" \
      --format json 2>/dev/null | json_get_id || true
  )"

  if [[ -n "$group_id" ]]; then
    printf '%s\n' "$group_id"
    return 0
  fi

  log "Creating group: $GROUP_NAME"
  group_id="$(
    nebius iam group create \
      --parent-id "$TENANT_ID" \
      --name "$GROUP_NAME" \
      --format json | json_get_id
  )"
  [[ -n "$group_id" ]] || die "Failed to create group."
  printf '%s\n' "$group_id"
}

access_permit_exists() {
  local group_id="$1"

  nebius iam access-permit list \
    --parent-id "$group_id" \
    --all \
    --format json 2>/dev/null \
    | jq -e --arg resource "$PROJECT_ID" --arg role "$ROLE" '
        def rows:
          if type == "array" then .
          elif (.items? | type) == "array" then .items
          else [] end;
        any(rows[]; ((.spec.resource_id // .resource_id // "") == $resource)
          and ((.spec.role // .role // "") == $role))
      ' >/dev/null
}

ensure_access_permit() {
  local group_id="$1"
  local output_file=""

  if is_dry_run; then
    log "Would ensure group '$group_id' has role '$ROLE' on project '$PROJECT_ID'."
    return 0
  fi

  if access_permit_exists "$group_id"; then
    log "Access permit already exists."
    return 0
  fi

  output_file="$(mktemp)"
  if nebius iam access-permit create \
    --parent-id "$group_id" \
    --resource-id "$PROJECT_ID" \
    --role "$ROLE" >"$output_file" 2>&1; then
    cleanup_file "$output_file"
    return 0
  fi

  if grep -Eiq 'already|exist|duplicate' "$output_file"; then
    cleanup_file "$output_file"
    log "Access permit already exists."
    return 0
  fi

  cat "$output_file" >&2
  cleanup_file "$output_file"
  die "Failed to ensure access permit."
}

membership_exists() {
  local group_id="$1"
  local sa_id="$2"

  nebius iam group-membership list-members \
    --parent-id "$group_id" \
    --page-size 1000 \
    --format json 2>/dev/null \
    | jq -e --arg member "$sa_id" '
        def rows:
          if type == "array" then .
          elif (.items? | type) == "array" then .items
          else [] end;
        any(rows[]; (.spec.member_id // .member_id // .metadata.id // .id // "") == $member)
      ' >/dev/null
}

ensure_group_membership() {
  local group_id="$1"
  local sa_id="$2"
  local output_file=""

  if is_dry_run; then
    log "Would ensure service account '$sa_id' is a member of group '$group_id'."
    return 0
  fi

  if membership_exists "$group_id" "$sa_id"; then
    log "Group membership already exists."
    return 0
  fi

  output_file="$(mktemp)"
  if nebius iam group-membership create \
    --parent-id "$group_id" \
    --member-id "$sa_id" >"$output_file" 2>&1; then
    cleanup_file "$output_file"
    return 0
  fi

  if grep -Eiq 'already|exist|duplicate' "$output_file"; then
    cleanup_file "$output_file"
    log "Group membership already exists."
    return 0
  fi

  cat "$output_file" >&2
  cleanup_file "$output_file"
  die "Failed to ensure group membership."
}

ensure_iam_shape_for_service_account() {
  local sa_id="$1"
  local group_id=""

  group_id="$(get_or_create_group)"
  ensure_access_permit "$group_id"
  ensure_group_membership "$group_id" "$sa_id"
}

credential_service_account_id() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  jq -r '
    .service_account_id
    // .serviceAccountId
    // .account_id
    // .accountId
    // .subject.service_account.id
    // empty
  ' "$file"
}

ensure_credential_file() {
  local sa_id="$1"

  ensure_nebius_dir

  if [[ -f "$CREDENTIAL_FILE" ]]; then
    log "Credential file exists: $CREDENTIAL_FILE"
    safe_chmod_credential "$CREDENTIAL_FILE"
    return 0
  fi

  log "Generating authorized-key credential file: $CREDENTIAL_FILE"
  run nebius iam auth-public-key generate \
    --service-account-id "$sa_id" \
    --output "$CREDENTIAL_FILE"
  safe_chmod_credential "$CREDENTIAL_FILE"
}

replace_credential_file() {
  local sa_id="$1"
  local backup_file=""
  local temp_file=""

  [[ "$REPAIR" == "true" ]] || die "Credential/profile verification failed. Rerun with --repair after confirming the current human/admin Nebius session can regenerate the key."
  ensure_human_session
  ensure_nebius_dir

  backup_file="$(mktemp "${CREDENTIAL_FILE}.bak.XXXXXX")"
  cleanup_file "$backup_file"
  temp_file="${CREDENTIAL_FILE}.tmp.$$"

  log "Generating replacement credential file before moving the existing file."
  run nebius iam auth-public-key generate \
    --service-account-id "$sa_id" \
    --output "$temp_file"
  run chmod 600 "$temp_file"

  if [[ -f "$CREDENTIAL_FILE" ]]; then
    log "Backing up existing credential file to: $backup_file"
    run mv "$CREDENTIAL_FILE" "$backup_file"
  fi
  run mv "$temp_file" "$CREDENTIAL_FILE"
  safe_chmod_credential "$CREDENTIAL_FILE"
}

profile_exists() {
  nebius profile list --format json 2>/dev/null \
    | jq -e --arg profile "$PROFILE" '
        def rows:
          if type == "array" then .
          elif (.items? | type) == "array" then .items
          elif (.profiles? | type) == "array" then .profiles
          elif type == "object" then
            [to_entries[] | {name: .key} + (if (.value | type) == "object" then .value else {} end)]
          else [] end;
        any(rows[]; (.name // .metadata.name // .profile // .id // "") == $profile)
      ' >/dev/null
}

profile_can_mint_token() {
  if is_dry_run; then
    return 1
  fi
  nebius iam get-access-token --profile "$PROFILE" >/dev/null 2>&1
}

ensure_profile() {
  if profile_can_mint_token; then
    log "Profile works: $PROFILE"
    return 0
  fi

  if is_dry_run; then
    log "Would create or update profile '$PROFILE' from '$CREDENTIAL_FILE'."
    return 0
  fi

  [[ -f "$CREDENTIAL_FILE" ]] || return 1

  if profile_exists; then
    log "Updating profile: $PROFILE"
    nebius profile update "$PROFILE" \
      --endpoint "$ENDPOINT" \
      --tenant-id "$TENANT_ID" \
      --parent-id "$PROJECT_ID" \
      --service-account-file "$CREDENTIAL_FILE" >/dev/null
  else
    log "Creating profile: $PROFILE"
    nebius profile create "$PROFILE" \
      --endpoint "$ENDPOINT" \
      --tenant-id "$TENANT_ID" \
      --parent-id "$PROJECT_ID" \
      --service-account-file "$CREDENTIAL_FILE" >/dev/null
  fi

  profile_can_mint_token
}

verify_project_access() {
  if is_dry_run; then
    log "Would verify basic project access through profile '$PROFILE'."
    return 0
  fi

  nebius iam service-account get-by-name \
    --name "$SA_NAME" \
    --parent-id "$PROJECT_ID" \
    --profile "$PROFILE" \
    --format json >/dev/null 2>&1
}

shell_quote() {
  printf '%q' "$1"
}

toml_literal_safe() {
  [[ "$1" != *"'"* ]]
}

install_hook() {
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local config_file="${codex_home}/config.toml"
  local hook_file="${SKILL_DIR}/hooks/pre_tool_use_nebius_auth.py"
  local begin_marker="# agent-nebius-auth managed block begin"
  local end_marker="# agent-nebius-auth managed block end"
  local begin_count=""
  local end_count=""
  local temp_file=""
  local hook_command=""
  local lock_dir="${codex_home}/agent-nebius-auth.config.lock"

  [[ -f "$hook_file" ]] || die "Hook file is missing: $hook_file"
  need_cmd python3

  hook_command="CODEX_NEBIUS_PROJECT_ID=$(shell_quote "$PROJECT_ID") python3 $(shell_quote "$hook_file")"
  toml_literal_safe "$hook_command" || die "Hook command contains a single quote and cannot be written safely as a TOML literal."

  if is_dry_run; then
    log "Would add or update the agent-nebius-auth PreToolUse hook block in $config_file."
    return 0
  fi

  mkdir -p "$codex_home"
  acquire_lock "$lock_dir"
  touch "$config_file"

  begin_count="$(grep -cF "$begin_marker" "$config_file" || true)"
  end_count="$(grep -cF "$end_marker" "$config_file" || true)"
  [[ "$begin_count" == "$end_count" ]] || die "Existing agent-nebius-auth managed hook block is malformed in $config_file."

  temp_file="$(mktemp)"
  awk -v begin="$begin_marker" -v end="$end_marker" '
    $0 == begin { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
  ' "$config_file" >"$temp_file"

  cat >>"$temp_file" <<EOF

$begin_marker
[[hooks.PreToolUse]]
matcher = "^Bash$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = '$hook_command'
timeout = 30
statusMessage = "Preparing Nebius auth"
$end_marker
EOF

  mv "$temp_file" "$config_file"

  log "Installed Codex PreToolUse hook block in $config_file"
  log "Restart Codex and review/trust the hook with /hooks before relying on it."
}

ensure_existing_credential_flow() {
  local sa_id=""

  safe_chmod_credential "$CREDENTIAL_FILE"

  if ! ensure_profile; then
    log "Profile cannot mint a token from the existing credential file."
    [[ "$REPAIR" == "true" ]] || die "Credential/profile verification failed. Rerun with --repair after confirming the current human/admin Nebius session can regenerate the key."
    ensure_human_session
    sa_id="$(get_or_create_service_account)"
    replace_credential_file "$sa_id"
    ensure_profile || die "Profile still cannot mint a token after credential repair."
  fi

  if ! verify_project_access; then
    if human_session_available; then
      log "Service-account token minted, but project access verification failed. Repairing IAM shape with the current human session."
      sa_id="$(credential_service_account_id "$CREDENTIAL_FILE" || true)"
      [[ -n "$sa_id" ]] || sa_id="$(get_or_create_service_account)"
      ensure_iam_shape_for_service_account "$sa_id"
      verify_project_access || die "Project access still fails after IAM repair."
    elif is_dry_run; then
      log "Would verify and repair IAM shape if a human/admin session is available."
    else
      die "Service-account token minted, but basic project access failed and no human/admin session is available to repair IAM drift."
    fi
  fi

  if human_session_available; then
    log "Human session available, verifying IAM shape."
    sa_id="$(credential_service_account_id "$CREDENTIAL_FILE" || true)"
    [[ -n "$sa_id" ]] || sa_id="$(get_or_create_service_account)"
    ensure_iam_shape_for_service_account "$sa_id"
  elif is_dry_run; then
    log "Would verify IAM shape if a human/admin session is available."
  else
    log "Human session unavailable, skipping IAM drift repair. Service-account auth works."
  fi
}

ensure_missing_credential_flow() {
  local sa_id=""

  ensure_human_session
  sa_id="$(get_or_create_service_account)"
  ensure_iam_shape_for_service_account "$sa_id"
  ensure_credential_file "$sa_id"
  ensure_profile || die "Profile was created but token minting failed: $PROFILE"
  verify_project_access || die "Token minted, but basic project access verification failed."
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      ensure)
        COMMAND="ensure"
        shift
        ;;
      --tenant-id)
        [[ $# -ge 2 ]] || die "--tenant-id requires a value."
        TENANT_ID="$2"
        shift 2
        ;;
      --project-id)
        [[ $# -ge 2 ]] || die "--project-id requires a value."
        PROJECT_ID="$2"
        shift 2
        ;;
      --project-name)
        [[ $# -ge 2 ]] || die "--project-name requires a value."
        PROJECT_NAME="$2"
        shift 2
        ;;
      --service-account-name)
        [[ $# -ge 2 ]] || die "--service-account-name requires a value."
        SA_NAME="$2"
        shift 2
        ;;
      --role)
        [[ $# -ge 2 ]] || die "--role requires a value."
        ROLE="$2"
        shift 2
        ;;
      --install-hook)
        INSTALL_HOOK="true"
        shift
        ;;
      --repair)
        REPAIR="true"
        shift
        ;;
      --dry-run)
        DRY_RUN="true"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        usage
        die "Unknown argument: $1"
        ;;
    esac
  done
}

main() {
  local project_slug=""
  local auth_lock_dir=""

  trap cleanup_locks EXIT
  trap 'cleanup_locks; exit 130' INT TERM
  parse_args "$@"
  validate_inputs

  need_cmd nebius
  need_cmd jq
  need_cmd sed
  need_cmd awk
  need_cmd mktemp
  need_cmd unlink

  project_slug="$(slugify "$PROJECT_NAME")"
  [[ -n "$project_slug" ]] || die "--project-name must contain at least one alphanumeric character."

  GROUP_NAME="codex-agent-${project_slug}"
  PROFILE="codex-agent-${PROJECT_ID}"
  CREDENTIAL_FILE="$HOME/.nebius/codex-agent-authkey.${PROJECT_ID}.json"
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
  auth_lock_dir="$HOME/.nebius/agent-nebius-auth.${PROJECT_ID}.lock"

  ensure_nebius_dir
  acquire_lock "$auth_lock_dir"

  if [[ -f "$CREDENTIAL_FILE" ]]; then
    log "Credential file exists: $CREDENTIAL_FILE"
    ensure_existing_credential_flow
  else
    ensure_missing_credential_flow
  fi

  if [[ "$INSTALL_HOOK" == "true" ]]; then
    install_hook
  fi

  log "Done."
  log "Credential file: $CREDENTIAL_FILE"
  log "Profile: $PROFILE"
  log "Token test: nebius iam get-access-token --profile $PROFILE >/dev/null"
}

main "$@"
