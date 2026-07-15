#!/usr/bin/env bash
set -euo pipefail

SA_NAME="codex-agent-sa"
ROLE="admin"
REPAIR="false"
DRY_RUN="false"
ENDPOINT="api.nebius.cloud"
AGENT_PROFILE_PREFIX="codex-agent-"

TENANT_ID=""
PROJECT_ID=""
PROJECT_NAME=""
COMMAND=""
LOCK_DIRS=()
PROFILE_RESTORE_TARGET=""
DEFAULT_PROJECT_FILE=""

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
    (--project-id <project_id> | --project-name <project_name>) \
    [--service-account-name <name>] \
    [--role <role>] \
    [--repair] \
    [--dry-run]

Creates or repairs local Codex Agent Nebius service-account auth. Runtime token
injection is handled by the installed PreToolUse hook, not by this script.
Defaults: service account 'codex-agent-sa', project role 'admin'.
Install or refresh that hook from the skills repo root with:

  ./install-skills.sh --install-hooks agent-nebius-auth/assets/hooks --register-hooks
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

cleanup() {
  restore_pending_active_profile
  cleanup_locks
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

json_get_name() {
  jq -r '.metadata.name // .name // .items[0].metadata.name // empty'
}

validate_inputs() {
  [[ "$COMMAND" == "ensure" ]] || die "Only the 'ensure' command is supported."
  [[ -n "$TENANT_ID" ]] || die "--tenant-id is required."
  [[ -n "$PROJECT_ID" || -n "$PROJECT_NAME" ]] || die "Exactly one of --project-id or --project-name is required."
  [[ -z "$PROJECT_ID" || -z "$PROJECT_NAME" ]] || die "Pass only one of --project-id or --project-name."
  [[ "$TENANT_ID" =~ ^[A-Za-z0-9._:-]+$ ]] || die "--tenant-id contains unsupported characters."
  [[ -z "$PROJECT_ID" || "$PROJECT_ID" =~ ^[A-Za-z0-9._:-]+$ ]] || die "--project-id contains unsupported characters."
  [[ "$PROJECT_NAME" != *$'\n'* && "$PROJECT_NAME" != *$'\r'* ]] || die "--project-name must be a single line."
  [[ "$SA_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$ ]] || die "--service-account-name is not a safe Nebius resource name."
  [[ "$ROLE" =~ ^[A-Za-z0-9_.-]+$ ]] || die "--role contains unsupported characters."
}

validate_resolved_project_id() {
  [[ -n "$PROJECT_ID" ]] || die "Failed to resolve a project ID."
  [[ "$PROJECT_ID" =~ ^[A-Za-z0-9._:-]+$ ]] || die "Resolved project ID contains unsupported characters."
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

write_default_project_id() {
  local temp_file=""

  if is_dry_run; then
    log "Would set the default agent Nebius project to '$PROJECT_ID'."
    return 0
  fi

  temp_file="${DEFAULT_PROJECT_FILE}.tmp.$$"
  printf '%s\n' "$PROJECT_ID" >"$temp_file"
  chmod 600 "$temp_file"
  mv "$temp_file" "$DEFAULT_PROJECT_FILE"
}

active_profile() {
  nebius profile active 2>/dev/null | awk 'NF { print; exit }'
}

current_profile() {
  nebius profile current 2>/dev/null | awk 'NF { print; exit }'
}

profile_is_agent() {
  local profile="$1"
  [[ "$profile" == "${AGENT_PROFILE_PREFIX}"* ]]
}

current_human_profile() {
  local profile=""

  profile="$(current_profile || true)"
  [[ -n "$profile" ]] || return 1
  profile_is_agent "$profile" && return 1
  printf '%s\n' "$profile"
}

require_human_profile() {
  local profile=""

  if is_dry_run; then
    printf '%s\n' "dry-run-human-profile"
    return 0
  fi

  profile="$(current_human_profile || true)"
  [[ -n "$profile" ]] || die "Current Nebius active profile is missing or is an agent service-account profile. Activate a human/admin profile, then rerun."
  nebius iam get-access-token --profile "$profile" >/dev/null \
    || die "Current Nebius human/default session '$profile' is not valid. Re-authenticate, then rerun."
  printf '%s\n' "$profile"
}

profile_restore_target() {
  local profile=""

  profile="$(active_profile || true)"
  if [[ -n "$profile" ]]; then
    printf '%s\n' "$profile"
    return 0
  fi

  current_human_profile || true
}

human_session_available() {
  local profile=""

  if is_dry_run; then
    return 1
  fi

  profile="$(current_human_profile || true)"
  [[ -n "$profile" ]] || return 1
  nebius iam get-access-token --profile "$profile" >/dev/null 2>&1
}

ensure_human_session() {
  if is_dry_run; then
    log "Would verify the current human/default Nebius session."
    return 0
  fi

  require_human_profile >/dev/null
}

resolve_project_id_from_name() {
  local profile=""
  local project_id=""

  if is_dry_run; then
    log "Would resolve project ID for project '$PROJECT_NAME' under tenant '$TENANT_ID'."
    printf '%s\n' "dry-run-project-id"
    return 0
  fi

  profile="$(require_human_profile)"
  project_id="$(
    nebius iam project get-by-name \
      --name "$PROJECT_NAME" \
      --parent-id "$TENANT_ID" \
      --profile "$profile" \
      --format json | json_get_id
  )"
  [[ -n "$project_id" ]] || die "Failed to resolve project ID for project name '$PROJECT_NAME'."
  printf '%s\n' "$project_id"
}

resolve_project_name_from_id() {
  local profile=""
  local project_name=""

  if [[ -n "$PROJECT_NAME" ]]; then
    printf '%s\n' "$PROJECT_NAME"
    return 0
  fi

  if is_dry_run; then
    log "Would resolve project name for project ID '$PROJECT_ID'."
    printf '%s\n' "dry-run-project"
    return 0
  fi

  profile="$(require_human_profile)"
  project_name="$(
    nebius iam project get \
      --id "$PROJECT_ID" \
      --profile "$profile" \
      --format json | json_get_name
  )"
  [[ -n "$project_name" ]] || die "Failed to resolve project name for project ID '$PROJECT_ID'."
  printf '%s\n' "$project_name"
}

resolve_project_selector() {
  if [[ -z "$PROJECT_ID" ]]; then
    PROJECT_ID="$(resolve_project_id_from_name)"
  fi
  validate_resolved_project_id
}

ensure_group_name() {
  local project_slug=""

  if [[ -n "${GROUP_NAME:-}" ]]; then
    return 0
  fi

  PROJECT_NAME="$(resolve_project_name_from_id)"
  project_slug="$(slugify "$PROJECT_NAME")"
  [[ -n "$project_slug" ]] || die "Project name must contain at least one alphanumeric character."
  GROUP_NAME="codex-agent-${project_slug}"
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

  ensure_group_name

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
      ' >/dev/null 2>/dev/null
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
      ' >/dev/null 2>/dev/null
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
      ' >/dev/null 2>/dev/null
}

profile_can_mint_token() {
  if is_dry_run; then
    return 1
  fi
  nebius iam get-access-token --profile "$PROFILE" >/dev/null 2>&1
}

restore_active_profile() {
  local previous_profile="$1"
  local current_profile=""

  [[ -n "$previous_profile" ]] || return 0
  current_profile="$(active_profile || true)"
  [[ "$current_profile" == "$previous_profile" ]] && return 0

  log "Restoring active Nebius profile: $previous_profile"
  nebius profile activate "$previous_profile" >/dev/null
}

restore_pending_active_profile() {
  local profile="$PROFILE_RESTORE_TARGET"

  [[ -n "$profile" ]] || return 0
  PROFILE_RESTORE_TARGET=""
  restore_active_profile "$profile" || true
}

run_preserving_active_profile() {
  local restore_target=""
  local status=0
  local restore_status=0

  if is_dry_run; then
    run "$@"
    return 0
  fi

  restore_target="$(profile_restore_target || true)"
  PROFILE_RESTORE_TARGET="$restore_target"
  set +e
  "$@"
  status=$?
  set -e

  PROFILE_RESTORE_TARGET=""
  restore_active_profile "$restore_target" || restore_status=$?
  [[ "$restore_status" -eq 0 ]] || return "$restore_status"
  return "$status"
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
    run_preserving_active_profile nebius profile update "$PROFILE" \
      --endpoint "$ENDPOINT" \
      --tenant-id "$TENANT_ID" \
      --parent-id "$PROJECT_ID" \
      --service-account-file "$CREDENTIAL_FILE" >/dev/null
  else
    log "Creating profile: $PROFILE"
    run_preserving_active_profile nebius profile create "$PROFILE" \
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
        die "--install-hook is no longer supported. Install or refresh the runtime hook from the skills repo root with: ./install-skills.sh --install-hooks agent-nebius-auth/assets/hooks --register-hooks"
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
  local auth_lock_dir=""
  local profile_lock_dir=""

  trap cleanup EXIT
  trap 'cleanup; exit 130' INT TERM
  parse_args "$@"
  validate_inputs

  need_cmd nebius
  need_cmd jq
  need_cmd sed
  need_cmd awk
  need_cmd mktemp
  need_cmd unlink

  resolve_project_selector

  PROFILE="codex-agent-${PROJECT_ID}"
  CREDENTIAL_FILE="$HOME/.nebius/codex-agent-authkey.${PROJECT_ID}.json"
  DEFAULT_PROJECT_FILE="$HOME/.nebius/codex-agent-default-project-id"
  auth_lock_dir="$HOME/.nebius/agent-nebius-auth.${PROJECT_ID}.lock"
  profile_lock_dir="$HOME/.nebius/agent-nebius-auth.profile.lock"

  ensure_nebius_dir
  acquire_lock "$profile_lock_dir"
  acquire_lock "$auth_lock_dir"

  if [[ -f "$CREDENTIAL_FILE" ]]; then
    log "Credential file exists: $CREDENTIAL_FILE"
    ensure_existing_credential_flow
  else
    ensure_missing_credential_flow
  fi
  write_default_project_id

  log "Done."
  log "Credential file: $CREDENTIAL_FILE"
  log "Profile: $PROFILE"
  log "Token test: nebius iam get-access-token --profile $PROFILE >/dev/null"
}

main "$@"
