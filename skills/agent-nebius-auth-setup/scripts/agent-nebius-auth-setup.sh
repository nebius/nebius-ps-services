#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LEASE_HELPER="$SCRIPT_DIR/agent-nebius-auth-repair-lease.py"
SHARED_HELPER="$SCRIPT_DIR/../assets/hooks/nebius_auth_shared.py"
SA_NAME="codex-agent-sa"
ROLE="admin"
DRY_RUN="false"
CONFIRM_DIGEST=""
LEASE_FILE=""
LEASE_TTL_SECONDS="43200"
MAX_LEASE_TTL_SECONDS="86400"
TENANT_ARG_SET="false"
PROJECT_ARG_SET="false"
SA_ARG_SET="false"
ROLE_ARG_SET="false"
TTL_ARG_SET="false"
ENDPOINT="api.nebius.cloud"
AGENT_PROFILE_PREFIX="codex-agent-"

TENANT_ID=""
PROJECT_ID=""
PROJECT_NAME=""
HUMAN_PROFILE=""
COMMAND=""
LOCK_DIRS=()
PROFILE_RESTORE_TARGET=""
PLANNED_MUTATIONS=()
AUTHORIZED_CONVERGENCE_ACTIONS=()
PLAN_DIGEST=""
PLAN_NOTES=()
OBSERVED_SERVICE_ACCOUNT_ID=""
OBSERVED_GROUP_ID=""
OBSERVED_CREDENTIAL_SHA256=""
LEASE_SERVICE_ACCOUNT_ID=""
LEASE_CREDENTIAL_SHA256=""
LEASE_DIR="$HOME/.nebius/codex-agent-repair-leases"
PENDING_CREDENTIAL_TEMP_DIR=""

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf 'agent-nebius-auth-setup: %s\n' "$*" >&2
}

usage() {
  cat >&2 <<'EOF'
Usage:
  bash scripts/agent-nebius-auth-setup.sh verify \
    --project-id <project_id>

  bash scripts/agent-nebius-auth-setup.sh ensure \
    --project-id <project_id> \
    [--tenant-id <expected_tenant_id>] \
    [--service-account-name <name>] \
    [--role <role>] \
    (--dry-run | --confirm <plan_digest>)

  bash scripts/agent-nebius-auth-setup.sh replace-credential \
    --project-id <project_id> \
    [--tenant-id <expected_tenant_id>] \
    [--service-account-name <name>] \
    [--role <role>] \
    (--dry-run | --confirm <plan_digest>)

  bash scripts/agent-nebius-auth-setup.sh repair-lease \
    --project-id <project_id> \
    [--tenant-id <expected_tenant_id>] \
    [--service-account-name <name>] \
    [--ttl-seconds <seconds>] \
    (--dry-run | --confirm <plan_digest>)

  bash scripts/agent-nebius-auth-setup.sh repair-local \
    --lease-file <path>

Verifies, creates, or repairs local Codex Agent Nebius service-account auth.
The verify command is read-only and never requires a human/admin profile.
Runtime renewable context is handled by the installed PreToolUse hook, not by
this script.
Defaults: service account 'codex-agent-sa', project role 'admin'.

For first-time bootstrap, review one dry-run plan and confirm service-account
creation once. That confirmation covers bounded convergence of the same
tenant/project/account/group/role/paths, including one backup-and-replacement
attempt when the newly generated credential cannot mint a token.

A confirmed repair lease lasts 12 hours by default (24 hours maximum) and can
authorize only mode-0600 correction and rebuilding the exact bound CLI profile.
It never authorizes credential generation or rotation, IAM, identity changes,
or hook installation.
Install or refresh that hook from the skills repo root with:

  ./install-skills.sh --install-hooks agent-nebius-auth-setup/assets/hooks --register-hooks
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
  cleanup_credential_temp
  restore_pending_active_profile
  cleanup_locks
}

cleanup_credential_temp() {
  local temp_dir="$PENDING_CREDENTIAL_TEMP_DIR"
  [[ -n "$temp_dir" ]] || return 0
  PENDING_CREDENTIAL_TEMP_DIR=""
  if [[ -f "$temp_dir/credential.json" ]]; then
    unlink "$temp_dir/credential.json"
  fi
  rmdir "$temp_dir" 2>/dev/null || true
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

json_get_parent_id() {
  jq -r '.metadata.parent_id // .parent_id // .items[0].metadata.parent_id // empty'
}

validate_inputs() {
  [[ -n "$COMMAND" ]] || die "A command is required: verify, ensure, replace-credential, repair-lease, or repair-local."
  if [[ "$COMMAND" == "repair-local" ]]; then
    [[ -n "$LEASE_FILE" ]] || die "repair-local requires --lease-file <path>."
    [[ "$TENANT_ARG_SET" == "false" && "$PROJECT_ARG_SET" == "false" && "$SA_ARG_SET" == "false" && "$ROLE_ARG_SET" == "false" && "$TTL_ARG_SET" == "false" ]] \
      || die "repair-local derives its exact target from the lease; pass only --lease-file."
    [[ "$DRY_RUN" == "false" && -z "$CONFIRM_DIGEST" ]] || die "repair-local accepts neither --dry-run nor --confirm."
    return 0
  fi

  [[ "$COMMAND" == "verify" || "$COMMAND" == "ensure" || "$COMMAND" == "replace-credential" || "$COMMAND" == "repair-lease" ]] || die "Unsupported command: $COMMAND"
  [[ -n "$PROJECT_ID" ]] || die "--project-id is required. Discover it from the current session before setup."
  [[ "$PROJECT_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]*$ ]] || die "--project-id contains unsupported characters."
  [[ -z "$TENANT_ID" || "$TENANT_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]*$ ]] || die "--tenant-id contains unsupported characters."
  [[ "$SA_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$ ]] || die "--service-account-name is not a safe Nebius resource name."
  [[ "$ROLE" =~ ^[A-Za-z0-9_.-]+$ ]] || die "--role contains unsupported characters."
  if [[ "$COMMAND" == "verify" ]]; then
    [[ "$TENANT_ARG_SET" == "false" && "$SA_ARG_SET" == "false" && "$ROLE_ARG_SET" == "false" && "$TTL_ARG_SET" == "false" ]] \
      || die "verify accepts only --project-id."
    [[ "$DRY_RUN" == "false" && -z "$CONFIRM_DIGEST" && -z "$LEASE_FILE" ]] \
      || die "verify accepts no mutation, lease, dry-run, repair, or confirmation options."
    return 0
  fi
  [[ "$DRY_RUN" == "true" || -n "$CONFIRM_DIGEST" ]] || die "Review the plan with --dry-run, then rerun with --confirm <plan_digest> under the workflow's one-time bootstrap approval or a separately approved standalone repair."
  [[ "$DRY_RUN" != "true" || -z "$CONFIRM_DIGEST" ]] || die "Pass only one of --dry-run or --confirm."
  [[ -z "$CONFIRM_DIGEST" || "$CONFIRM_DIGEST" =~ ^[a-f0-9]{64}$ ]] || die "--confirm requires the 64-character digest from the reviewed dry-run plan."
  [[ -z "$LEASE_FILE" ]] || die "--lease-file is valid only with repair-local."
  if [[ "$COMMAND" == "ensure" || "$COMMAND" == "replace-credential" ]]; then
    [[ "$TTL_ARG_SET" == "false" ]] || die "$COMMAND does not accept --ttl-seconds."
  fi
  if [[ "$COMMAND" == "repair-lease" ]]; then
    [[ "$ROLE_ARG_SET" == "false" ]] || die "repair-lease does not accept --role."
    [[ "$LEASE_TTL_SECONDS" =~ ^[0-9]+$ ]] || die "--ttl-seconds must be an integer."
    (( LEASE_TTL_SECONDS > 0 && LEASE_TTL_SECONDS <= MAX_LEASE_TTL_SECONDS )) \
      || die "--ttl-seconds must be between 1 and $MAX_LEASE_TTL_SECONDS."
  fi
}

validate_resolved_project_id() {
  [[ -n "$PROJECT_ID" ]] || die "Failed to resolve a project ID."
  [[ "$PROJECT_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]*$ ]] || die "Resolved project ID contains unsupported characters."
}

safe_chmod_credential() {
  local file="$1"
  local helper_args=(secure-chmod --path "$file" --uid "$(id -u)")
  [[ -f "$file" ]] || return 0
  verify_credential_local_safety "$file"
  if is_dry_run; then
    run chmod 600 "$file"
    return 0
  fi
  if [[ -n "$LEASE_CREDENTIAL_SHA256" ]]; then
    helper_args+=(--expected-sha256 "$LEASE_CREDENTIAL_SHA256")
  fi
  python3 "$LEASE_HELPER" "${helper_args[@]}"
}

ensure_nebius_dir() {
  if [[ -e "$HOME/.nebius" || -L "$HOME/.nebius" ]]; then
    [[ ! -L "$HOME/.nebius" && -d "$HOME/.nebius" ]] \
      || die "$HOME/.nebius must be a non-symlink directory."
    local owner_uid=""
    owner_uid="$(stat -f '%u' "$HOME/.nebius" 2>/dev/null || stat -c '%u' "$HOME/.nebius" 2>/dev/null || true)"
    [[ "$owner_uid" == "$(id -u)" ]] || die "$HOME/.nebius must be owned by the current user."
  fi
  run mkdir -p "$HOME/.nebius"
  run chmod 700 "$HOME/.nebius"
}

plan_nebius_dir() {
  local mode=""
  if [[ ! -e "$HOME/.nebius" && ! -L "$HOME/.nebius" ]]; then
    PLANNED_MUTATIONS+=("create owned local Nebius directory '$HOME/.nebius' at mode 0700")
    return 0
  fi
  [[ ! -L "$HOME/.nebius" && -d "$HOME/.nebius" ]] \
    || die "$HOME/.nebius must be a non-symlink directory."
  local owner_uid=""
  owner_uid="$(stat -f '%u' "$HOME/.nebius" 2>/dev/null || stat -c '%u' "$HOME/.nebius" 2>/dev/null || true)"
  [[ "$owner_uid" == "$(id -u)" ]] \
    || die "$HOME/.nebius must be owned by the current user."
  mode="$(stat -f '%Lp' "$HOME/.nebius" 2>/dev/null || stat -c '%a' "$HOME/.nebius" 2>/dev/null || true)"
  [[ "$mode" == "700" ]] \
    || PLANNED_MUTATIONS+=("set local Nebius directory permissions to 0700")
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

  profile="$(current_human_profile || true)"
  [[ -n "$profile" ]] || die "blocked-admin-auth: current Nebius profile is missing or is an agent profile; an exact IAM plan requires a non-agent administrative profile."
  nebius iam get-access-token --no-browser --auth-timeout 10s --timeout 20s --profile "$profile" </dev/null >/dev/null \
    || die "blocked-admin-auth: non-agent profile '$profile' is not valid non-interactively. Re-authenticate it, then rerun the IAM plan."
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

  profile="$(current_human_profile || true)"
  [[ -n "$profile" ]] || return 1
  nebius iam get-access-token --no-browser --auth-timeout 10s --timeout 20s --profile "$profile" </dev/null >/dev/null 2>&1
}

ensure_human_session() {
  require_human_profile >/dev/null
}

resolve_project_metadata() {
  local metadata=""
  local resolved_id=""
  local resolved_name=""
  local resolved_tenant_id=""
  local expected_tenant_id="$TENANT_ID"

  HUMAN_PROFILE="$(require_human_profile)"
  metadata="$(
    nebius iam project get \
      --id "$PROJECT_ID" \
      --no-browser \
      --profile "$HUMAN_PROFILE" \
      --format json
  )" || die "Failed to resolve authoritative metadata for project '$PROJECT_ID'."
  resolved_id="$(printf '%s' "$metadata" | json_get_id)"
  resolved_name="$(printf '%s' "$metadata" | json_get_name)"
  resolved_tenant_id="$(printf '%s' "$metadata" | json_get_parent_id)"

  [[ "$resolved_id" == "$PROJECT_ID" ]] || die "Project metadata ID '$resolved_id' does not match requested project '$PROJECT_ID'."
  [[ -n "$resolved_name" ]] || die "Project metadata for '$PROJECT_ID' is missing metadata.name."
  [[ -n "$resolved_tenant_id" ]] || die "Project metadata for '$PROJECT_ID' is missing metadata.parent_id."
  [[ "$resolved_tenant_id" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]*$ ]] || die "Resolved tenant ID contains unsupported characters."
  if [[ -n "$expected_tenant_id" && "$expected_tenant_id" != "$resolved_tenant_id" ]]; then
    die "Project '$PROJECT_ID' belongs to tenant '$resolved_tenant_id', not asserted tenant '$expected_tenant_id'."
  fi

  PROJECT_NAME="$resolved_name"
  TENANT_ID="$resolved_tenant_id"
  validate_resolved_project_id
}

ensure_group_name() {
  local project_hash=""
  local project_slug=""

  if [[ -n "${GROUP_NAME:-}" ]]; then
    return 0
  fi

  project_slug="$(slugify "$PROJECT_NAME")"
  [[ -n "$project_slug" ]] || die "Project name must contain at least one alphanumeric character."
  project_hash="$(
    python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:20])' \
      "$PROJECT_ID"
  )"
  GROUP_NAME="codex-agent-${project_slug:0:28}-${project_hash}"
}

get_or_create_service_account() {
  local create_output=""
  local sa_id=""

  sa_id="$(lookup_service_account_id)"

  if [[ -n "$sa_id" ]]; then
    printf '%s\n' "$sa_id"
    return 0
  fi

  log "Creating service account: $SA_NAME"
  if create_output="$(nebius iam service-account create \
      --name "$SA_NAME" \
      --parent-id "$PROJECT_ID" \
      --no-browser \
      --profile "$HUMAN_PROFILE" \
      --format json)"; then
    sa_id="$(printf '%s' "$create_output" | json_get_id)"
  else
    sa_id="$(lookup_service_account_id)"
    [[ -n "$sa_id" ]] \
      || die "Failed to create service account and no converged account exists."
    log "Service account converged after a create conflict."
  fi
  [[ -n "$sa_id" ]] || die "Failed to create service account."
  printf '%s\n' "$sa_id"
}

lookup_service_account_id() {
  local error_file=""
  local output_file=""
  local result=""
  local status=0

  output_file="$(mktemp)"
  error_file="$(mktemp)"
  if nebius iam service-account get-by-name \
    --name "$SA_NAME" \
    --parent-id "$PROJECT_ID" \
    --no-browser \
    --profile "$HUMAN_PROFILE" \
    --format json >"$output_file" 2>"$error_file"; then
    result="$(json_get_id <"$output_file")"
    cleanup_file "$output_file"
    cleanup_file "$error_file"
    [[ -n "$result" ]] \
      || die "Service-account lookup returned no ID for '$SA_NAME'."
    printf '%s\n' "$result"
    return 0
  else
    status=$?
  fi
  if [[ "$status" -eq 13 || "$status" -eq 53 ]]; then
    cleanup_file "$output_file"
    cleanup_file "$error_file"
    return 0
  fi
  cat "$error_file" >&2
  cleanup_file "$output_file"
  cleanup_file "$error_file"
  die "Service-account lookup failed; refusing to treat unknown state as absent."
}

get_or_create_group() {
  local create_output=""
  local group_id=""

  ensure_group_name

  group_id="$(lookup_group_id)"

  if [[ -n "$group_id" ]]; then
    printf '%s\n' "$group_id"
    return 0
  fi

  log "Creating group: $GROUP_NAME"
  if create_output="$(nebius iam group create \
      --parent-id "$PROJECT_ID" \
      --name "$GROUP_NAME" \
      --no-browser \
      --profile "$HUMAN_PROFILE" \
      --format json)"; then
    group_id="$(printf '%s' "$create_output" | json_get_id)"
  else
    group_id="$(lookup_group_id)"
    [[ -n "$group_id" ]] \
      || die "Failed to create group and no converged group exists."
    log "Group converged after a create conflict."
  fi
  [[ -n "$group_id" ]] || die "Failed to create group."
  printf '%s\n' "$group_id"
}

lookup_group_id() {
  lookup_group_id_by_name "$GROUP_NAME" "$PROJECT_ID"
}

lookup_group_id_by_name() {
  local group_name="$1"
  local parent_id="$2"
  local error_file=""
  local output_file=""
  local result=""
  local status=0

  output_file="$(mktemp)"
  error_file="$(mktemp)"
  if nebius iam group get-by-name \
    --name "$group_name" \
    --parent-id "$parent_id" \
    --no-browser \
    --profile "$HUMAN_PROFILE" \
    --format json >"$output_file" 2>"$error_file"; then
    result="$(json_get_id <"$output_file")"
    cleanup_file "$output_file"
    cleanup_file "$error_file"
    [[ -n "$result" ]] || die "Group lookup returned no ID for '$group_name'."
    printf '%s\n' "$result"
    return 0
  else
    status=$?
  fi
  if [[ "$status" -eq 13 || "$status" -eq 53 ]]; then
    cleanup_file "$output_file"
    cleanup_file "$error_file"
    return 0
  fi
  cat "$error_file" >&2
  cleanup_file "$output_file"
  cleanup_file "$error_file"
  die "Group lookup failed; refusing to treat unknown state as absent."
}

access_permit_exists() {
  local group_id="$1"
  local response=""
  local status=0

  response="$(nebius iam access-permit list \
    --parent-id "$group_id" \
    --all \
    --no-browser \
    --profile "$HUMAN_PROFILE" \
    --format json)" \
    || die "Access-permit lookup failed; refusing to treat unknown state as absent."
  if printf '%s' "$response" | jq -e --arg resource "$PROJECT_ID" --arg role "$ROLE" '
        def rows:
          if type == "array" then .
          elif (.items? | type) == "array" then .items
          else [] end;
        any(rows[]; ((.spec.resource_id // .resource_id // "") == $resource)
          and ((.spec.role // .role // "") == $role))
      ' >/dev/null; then
    return 0
  else
    status=$?
  fi
  [[ "$status" -eq 1 ]] || die "Access-permit lookup returned malformed JSON."
  return 1
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
    --no-browser \
    --profile "$HUMAN_PROFILE" \
    --role "$ROLE" >"$output_file" 2>&1; then
    cleanup_file "$output_file"
    return 0
  fi

  if access_permit_exists "$group_id"; then
    cleanup_file "$output_file"
    log "Access permit converged after a create conflict."
    return 0
  fi

  cat "$output_file" >&2
  cleanup_file "$output_file"
  die "Failed to ensure access permit."
}

membership_exists() {
  local group_id="$1"
  local sa_id="$2"
  local next_page_token=""
  local page_count=0
  local page_token=""
  local page_args=()
  local response=""
  local status=0
  local seen_page_tokens=()
  local seen_token=""

  while true; do
    page_count=$((page_count + 1))
    (( page_count <= 1000 )) \
      || die "Group-membership lookup exceeded the pagination safety bound."
    page_args=(
      --parent-id "$group_id"
      --page-size 1000
      --no-browser
      --profile "$HUMAN_PROFILE"
      --format json
    )
    if [[ -n "$page_token" ]]; then
      page_args+=(--page-token "$page_token")
    fi
    response="$(nebius iam group-membership list-members "${page_args[@]}")" \
      || die "Group-membership lookup failed; refusing to treat unknown state as absent."
    printf '%s' "$response" | jq -e '
      type == "object"
      and (.memberships | type == "array")
      and ((.next_page_token // "") | type == "string")
    ' >/dev/null \
      || die "Group-membership lookup returned malformed JSON."
    if printf '%s' "$response" | jq -e --arg member "$sa_id" '
          any(.memberships[];
            (.spec.member_id // .member_id // .metadata.id // .id // "") == $member)
        ' >/dev/null; then
      return 0
    else
      status=$?
    fi
    [[ "$status" -eq 1 ]] \
      || die "Group-membership lookup returned malformed JSON."
    next_page_token="$(printf '%s' "$response" | jq -r '.next_page_token // empty')"
    [[ -n "$next_page_token" ]] || return 1
    for seen_token in "${seen_page_tokens[@]}"; do
      [[ "$seen_token" != "$next_page_token" ]] \
        || die "Group-membership lookup repeated a pagination token."
    done
    seen_page_tokens+=("$next_page_token")
    page_token="$next_page_token"
  done
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
    --no-browser \
    --profile "$HUMAN_PROFILE" \
    --member-id "$sa_id" >"$output_file" 2>&1; then
    cleanup_file "$output_file"
    return 0
  fi

  if membership_exists "$group_id" "$sa_id"; then
    cleanup_file "$output_file"
    log "Group membership converged after a create conflict."
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
  python3 "$SHARED_HELPER" credential-id \
    --path "$file" \
    --uid "$(id -u)" \
    --allow-non-0600
}

verify_credential_identity() {
  local sa_id=""
  local metadata=""
  local resolved_name=""
  local resolved_parent_id=""

  sa_id="$(credential_service_account_id "$CREDENTIAL_FILE" || true)"
  [[ -n "$sa_id" ]] || die "Existing credential does not contain a service-account ID; refusing mutation."
  metadata="$(
    nebius iam service-account get \
      --id "$sa_id" \
      --no-browser \
      --profile "$HUMAN_PROFILE" \
      --format json
  )" || die "Failed to resolve service account '$sa_id' from the existing credential; refusing mutation."
  resolved_name="$(printf '%s' "$metadata" | json_get_name)"
  resolved_parent_id="$(printf '%s' "$metadata" | json_get_parent_id)"
  [[ "$resolved_name" == "$SA_NAME" ]] || die "Existing credential belongs to service account '$resolved_name', not '$SA_NAME'; refusing mutation."
  [[ "$resolved_parent_id" == "$PROJECT_ID" ]] || die "Existing credential service account belongs to project '$resolved_parent_id', not '$PROJECT_ID'; refusing mutation."
}

verify_credential_local_safety() {
  local file="$1"
  local owner_uid=""

  [[ -e "$file" || -L "$file" ]] || return 0
  [[ ! -L "$file" && -f "$file" ]] || die "Credential path must be a regular non-symlink file; refusing mutation."
  owner_uid="$(stat -f '%u' "$file" 2>/dev/null || stat -c '%u' "$file" 2>/dev/null || true)"
  [[ -n "$owner_uid" && "$owner_uid" == "$(id -u)" ]] || die "Credential file is not owned by the current user; refusing mutation."
}

print_plan() {
  local mutation=""
  local note=""

  log "Resolved authorization target:"
  log "  tenant ID: $TENANT_ID"
  log "  project ID: $PROJECT_ID"
  log "  project name: $PROJECT_NAME"
  log "  service account: $SA_NAME"
  log "  observed service-account ID: ${OBSERVED_SERVICE_ACCOUNT_ID:-absent}"
  log "  group: $GROUP_NAME"
  log "  group parent: $PROJECT_ID (project-scoped)"
  log "  observed group ID: ${OBSERVED_GROUP_ID:-absent}"
  log "  additive project role: $ROLE (existing roles on this project are preserved)"
  log "  credential file: $CREDENTIAL_FILE"
  log "  observed credential SHA-256: ${OBSERVED_CREDENTIAL_SHA256:-absent}"
  log "  CLI profile: $PROFILE"
  log "Authorized convergence actions:"
  for mutation in "${AUTHORIZED_CONVERGENCE_ACTIONS[@]}"; do
    log "  - $mutation"
  done
  log "Currently required actions:"
  if [[ "${#PLANNED_MUTATIONS[@]}" -eq 0 ]]; then
    log "  none (read-only authentication and access verification only)"
  else
    for mutation in "${PLANNED_MUTATIONS[@]}"; do
      log "  - $mutation"
    done
  fi
  if [[ "${#PLAN_NOTES[@]}" -gt 0 ]]; then
    log "Operator follow-up:"
    for note in "${PLAN_NOTES[@]}"; do
      log "  - $note"
    done
  fi
  log "Plan digest: $PLAN_DIGEST"
  log "No global default-project selector will be read or written."
}

ensure_credential_file() {
  local sa_id="$1"
  local generated_id=""
  local temp_file=""

  ensure_nebius_dir

  if [[ -f "$CREDENTIAL_FILE" ]]; then
    log "Credential file exists: $CREDENTIAL_FILE"
    safe_chmod_credential "$CREDENTIAL_FILE"
    return 0
  fi

  log "Generating authorized-key credential file: $CREDENTIAL_FILE"
  PENDING_CREDENTIAL_TEMP_DIR="$(mktemp -d "${CREDENTIAL_FILE}.tmp.XXXXXX")"
  temp_file="$PENDING_CREDENTIAL_TEMP_DIR/credential.json"
  if ! nebius iam auth-public-key generate \
    --service-account-id "$sa_id" \
    --no-browser \
    --profile "$HUMAN_PROFILE" \
    --output "$temp_file"; then
    die "Credential generation failed. A newly authorized public key may require operator review; no automatic retry or revocation was attempted."
  fi
  chmod 600 "$temp_file"
  generated_id="$(credential_service_account_id "$temp_file")" \
    || die "Generated credential identity is invalid; the canonical file was not changed."
  [[ "$generated_id" == "$sa_id" ]] \
    || die "Generated credential identity does not match the requested service account; the canonical file was not changed."
  fsync_file "$temp_file"
  mv "$temp_file" "$CREDENTIAL_FILE"
  cleanup_credential_temp
  safe_chmod_credential "$CREDENTIAL_FILE"
}

replace_credential_file() {
  local sa_id="$1"
  local backup_file=""
  local generated_id=""
  local temp_file=""

  ensure_human_session
  ensure_nebius_dir

  PENDING_CREDENTIAL_TEMP_DIR="$(mktemp -d "${CREDENTIAL_FILE}.tmp.XXXXXX")"
  temp_file="$PENDING_CREDENTIAL_TEMP_DIR/credential.json"

  log "Generating replacement credential file before moving the existing file."
  if ! nebius iam auth-public-key generate \
    --service-account-id "$sa_id" \
    --no-browser \
    --profile "$HUMAN_PROFILE" \
    --output "$temp_file"; then
    die "Replacement credential generation failed. A newly authorized public key may require operator review; no automatic retry or revocation was attempted."
  fi
  chmod 600 "$temp_file"
  generated_id="$(credential_service_account_id "$temp_file")" \
    || die "Replacement credential identity is invalid; the canonical file was not changed."
  [[ "$generated_id" == "$sa_id" ]] \
    || die "Replacement credential identity does not match the requested service account; the canonical file was not changed."
  fsync_file "$temp_file"

  if [[ -f "$CREDENTIAL_FILE" ]]; then
    backup_file="$(mktemp "${CREDENTIAL_FILE}.bak.XXXXXX")"
    log "Backing up existing credential file to: $backup_file"
    cp -p "$CREDENTIAL_FILE" "$backup_file"
    chmod 600 "$backup_file"
  fi
  mv "$temp_file" "$CREDENTIAL_FILE"
  cleanup_credential_temp
  safe_chmod_credential "$CREDENTIAL_FILE"
}

profile_exists() {
  local line=""
  local normalized=""
  local output=""

  output="$(nebius profile list 2>/dev/null)" \
    || die "CLI profile lookup failed; refusing to treat unknown state as absent."
  while IFS= read -r line || [[ -n "$line" ]]; do
    normalized="$(printf '%s' "$line" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/[[:space:]]+\[active\]$//')"
    [[ -n "$normalized" ]] || continue
    [[ "$normalized" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]*$ ]] \
      || die "CLI profile lookup returned unsupported output."
    [[ "$normalized" == "$PROFILE" ]] && return 0
  done <<<"$output"
  return 1
}

profile_can_mint_token() {
  nebius iam get-access-token --no-browser --auth-timeout 10s --timeout 20s --profile "$PROFILE" </dev/null >/dev/null 2>&1
}

verify_agent_runtime() {
  local mode=""
  local service_account_id=""

  [[ -e "$CREDENTIAL_FILE" || -L "$CREDENTIAL_FILE" ]] \
    || die "Agent runtime credential is missing for project '$PROJECT_ID'."
  verify_credential_local_safety "$CREDENTIAL_FILE"
  mode="$(credential_mode "$CREDENTIAL_FILE")"
  [[ "$mode" == "600" ]] \
    || die "Agent runtime credential must have mode 0600; no repair was attempted."
  service_account_id="$(credential_service_account_id "$CREDENTIAL_FILE")" \
    || die "Agent runtime credential identity is invalid or unsupported."
  [[ -n "$service_account_id" ]] \
    || die "Agent runtime credential identity is missing."
  profile_exists || die "Agent runtime profile is missing: $PROFILE"
  profile_can_mint_token \
    || die "Agent runtime profile cannot mint a token non-interactively."
  profile_has_project_access \
    || die "Agent runtime profile minted a token but lacks basic project access."
  log "Agent runtime auth is ready for project '$PROJECT_ID'."
  log "Human/admin IAM reconciliation was not checked."
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
  if is_dry_run; then
    log "Would create or update profile '$PROFILE' from '$CREDENTIAL_FILE'."
    return 0
  fi

  [[ -f "$CREDENTIAL_FILE" ]] || return 1

  if profile_exists; then
    if profile_can_mint_token; then
      log "CLI profile already exists and can mint a token: $PROFILE"
      return 0
    fi
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

profile_has_project_access() {
  nebius iam service-account get-by-name \
    --name "$SA_NAME" \
    --parent-id "$PROJECT_ID" \
    --no-browser \
    --profile "$PROFILE" \
    --format json >/dev/null 2>&1
}

verify_project_access() {
  if is_dry_run; then
    log "Would verify basic project access through profile '$PROFILE'."
    return 0
  fi

  profile_has_project_access
}

ensure_existing_credential_flow() {
  local sa_id=""

  safe_chmod_credential "$CREDENTIAL_FILE"

  if ! ensure_profile; then
    die "credential-replacement-required: the matching credential cannot mint a token. Before any replacement, mark the one replacement attempt in private task state, run replace-credential --dry-run, validate its state-bound target against the recorded bootstrap authorization, and invoke its confirmed phase without another user prompt."
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
  if ! ensure_profile; then
    die "credential-replacement-required: the newly generated credential cannot mint a token. Before any replacement, mark the one replacement attempt in private task state, run replace-credential --dry-run, validate its state-bound target against the recorded bootstrap authorization, and invoke its confirmed phase without another user prompt."
  fi
  verify_project_access || die "Token minted, but basic project access verification failed."
}

build_credential_replacement_plan() {
  local group_id=""
  local sa_id=""

  PLANNED_MUTATIONS=()
  PLAN_NOTES=()
  OBSERVED_SERVICE_ACCOUNT_ID=""
  OBSERVED_GROUP_ID=""
  OBSERVED_CREDENTIAL_SHA256=""
  plan_nebius_dir
  ensure_group_name
  AUTHORIZED_CONVERGENCE_ACTIONS=(
    "back up matching credential '$CREDENTIAL_FILE' with mode 0600"
    "replace the canonical credential once for service account '$SA_NAME'"
    "create or update CLI profile '$PROFILE' from the replacement credential"
    "verify token mint and basic project access"
  )

  [[ -e "$CREDENTIAL_FILE" || -L "$CREDENTIAL_FILE" ]] \
    || die "Credential replacement requires an existing canonical credential."
  verify_credential_local_safety "$CREDENTIAL_FILE"
  verify_credential_identity
  sa_id="$(credential_service_account_id "$CREDENTIAL_FILE")"
  OBSERVED_SERVICE_ACCOUNT_ID="$sa_id"
  OBSERVED_CREDENTIAL_SHA256="$(credential_sha256 "$CREDENTIAL_FILE")"
  group_id="$(lookup_group_id)"
  OBSERVED_GROUP_ID="$group_id"
  profile_can_mint_token \
    && die "Credential replacement is not required because the matching profile can mint a token."

  PLANNED_MUTATIONS+=("back up and replace credential '$CREDENTIAL_FILE' once")
  PLANNED_MUTATIONS+=("create or update CLI profile '$PROFILE' from the replacement credential")
  PLANNED_MUTATIONS+=("verify token mint and basic project access")
}

run_credential_replacement() {
  local sa_id="$OBSERVED_SERVICE_ACCOUNT_ID"

  [[ -n "$sa_id" ]] || die "Credential replacement requires a resolved service-account identity."
  replace_credential_file "$sa_id"
  ensure_profile \
    || die "Profile still cannot mint a token after the confirmed bootstrap's one bounded credential replacement: $PROFILE"
  verify_project_access \
    || die "The replacement credential can mint a token, but basic project access still fails. Return to a fresh state-bound ensure plan without replacing another key."
  log "The one bounded credential replacement completed."
}

credential_mode() {
  local file="$1"
  stat -f '%Lp' "$file" 2>/dev/null || stat -c '%a' "$file" 2>/dev/null || true
}

credential_sha256() {
  local file="$1"
  python3 "$LEASE_HELPER" credential-sha256 \
    --path "$file" \
    --uid "$(id -u)"
}

fsync_file() {
  python3 -c 'import os, sys; descriptor = os.open(sys.argv[1], os.O_RDONLY); os.fsync(descriptor); os.close(descriptor)' "$1"
}

verify_repair_lease_directory() {
  local owner_uid=""
  local mode=""

  [[ ! -L "$HOME/.nebius" && -d "$HOME/.nebius" ]] \
    || die "$HOME/.nebius must be an existing non-symlink directory before repair-lease use."
  owner_uid="$(stat -f '%u' "$HOME/.nebius" 2>/dev/null || stat -c '%u' "$HOME/.nebius" 2>/dev/null || true)"
  mode="$(credential_mode "$HOME/.nebius")"
  [[ "$owner_uid" == "$(id -u)" && "$mode" == "700" ]] \
    || die "$HOME/.nebius must be owned by the current user at mode 0700."

  [[ ! -L "$LEASE_DIR" && -d "$LEASE_DIR" ]] \
    || die "Repair-lease directory must be a non-symlink directory: $LEASE_DIR"
  owner_uid="$(stat -f '%u' "$LEASE_DIR" 2>/dev/null || stat -c '%u' "$LEASE_DIR" 2>/dev/null || true)"
  mode="$(credential_mode "$LEASE_DIR")"
  [[ "$owner_uid" == "$(id -u)" && "$mode" == "700" ]] \
    || die "Repair-lease directory must be owned by the current user at mode 0700: $LEASE_DIR"
}

ensure_repair_lease_directory() {
  ensure_nebius_dir
  run mkdir -p "$LEASE_DIR"
  run chmod 700 "$LEASE_DIR"
  is_dry_run || verify_repair_lease_directory
}

build_repair_lease_plan() {
  local credential_hash=""
  local nebius_mode=""
  local owner_uid=""

  PLANNED_MUTATIONS=()
  PLAN_NOTES=()
  [[ -e "$CREDENTIAL_FILE" || -L "$CREDENTIAL_FILE" ]] \
    || die "A repair lease requires an existing confirmed credential. Complete first-time setup, then request the lease separately."
  verify_credential_local_safety "$CREDENTIAL_FILE"
  verify_credential_identity
  [[ "$(credential_mode "$CREDENTIAL_FILE")" == "600" ]] \
    || die "A repair lease requires the canonical credential at mode 0600. Complete confirmed setup or repair first."
  LEASE_SERVICE_ACCOUNT_ID="$(credential_service_account_id "$CREDENTIAL_FILE")"
  credential_hash="$(credential_sha256 "$CREDENTIAL_FILE")"
  [[ -n "$credential_hash" ]] || die "Failed to fingerprint the canonical credential."
  LEASE_CREDENTIAL_SHA256="$credential_hash"
  profile_exists || die "A repair lease requires the matching CLI profile. Complete confirmed setup first."
  profile_can_mint_token || die "The matching profile cannot mint a token. Complete confirmed repair before issuing a lease."
  profile_has_project_access \
    || die "The matching profile can mint a token but lacks basic project access. Complete confirmed IAM repair before issuing a lease."
  [[ ! -L "$HOME/.nebius" && -d "$HOME/.nebius" ]] \
    || die "$HOME/.nebius must be an owned non-symlink directory before lease issuance."
  owner_uid="$(stat -f '%u' "$HOME/.nebius" 2>/dev/null || stat -c '%u' "$HOME/.nebius" 2>/dev/null || true)"
  [[ "$owner_uid" == "$(id -u)" ]] || die "$HOME/.nebius must be owned by the current user."
  nebius_mode="$(credential_mode "$HOME/.nebius")"
  if [[ "$nebius_mode" != "700" ]]; then
    PLANNED_MUTATIONS+=("set '$HOME/.nebius' permissions to 0700")
  fi
  if [[ -e "$LEASE_DIR" || -L "$LEASE_DIR" ]]; then
    [[ ! -L "$LEASE_DIR" && -d "$LEASE_DIR" ]] \
      || die "Repair-lease path must be a non-symlink directory: $LEASE_DIR"
    owner_uid="$(stat -f '%u' "$LEASE_DIR" 2>/dev/null || stat -c '%u' "$LEASE_DIR" 2>/dev/null || true)"
    [[ "$owner_uid" == "$(id -u)" ]] || die "Repair-lease directory must be owned by the current user."
    if [[ "$(credential_mode "$LEASE_DIR")" != "700" ]]; then
      PLANNED_MUTATIONS+=("set repair-lease directory '$LEASE_DIR' permissions to 0700")
    fi
  else
    PLANNED_MUTATIONS+=("create repair-lease directory '$LEASE_DIR' at mode 0700")
  fi
  PLANNED_MUTATIONS+=("create a workflow repair preauthorization record for project '$PROJECT_ID'")
  PLAN_NOTES+=("valid for $LEASE_TTL_SECONDS seconds; maximum $MAX_LEASE_TTL_SECONDS seconds")
  PLAN_NOTES+=("allows only credential mode 0600 correction and exact profile rebuild")
  PLAN_NOTES+=("does not authorize IAM, credential generation or rotation, identity changes, or hook changes")
}

compute_repair_lease_plan_digest() {
  {
    printf '%s\0' "repair-lease-v1" "$TENANT_ID" "$PROJECT_ID" "$PROJECT_NAME" "$SA_NAME" "$LEASE_SERVICE_ACCOUNT_ID" "$CREDENTIAL_FILE" "$LEASE_CREDENTIAL_SHA256" "$PROFILE" "$ENDPOINT" "$LEASE_TTL_SECONDS"
    printf '%s\0' "chmod-credential-0600" "rebuild-profile"
    printf '%s\0' "${PLANNED_MUTATIONS[@]}"
    printf '%s\0' "${PLAN_NOTES[@]}"
  } | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
}

print_repair_lease_plan() {
  log "Resolved repair-lease target:"
  log "  tenant ID: $TENANT_ID"
  log "  project ID: $PROJECT_ID"
  log "  project name: $PROJECT_NAME"
  log "  service account: $SA_NAME ($LEASE_SERVICE_ACCOUNT_ID)"
  log "  credential file: $CREDENTIAL_FILE"
  log "  CLI profile: $PROFILE"
  log "  lease directory: $LEASE_DIR"
  log "  lifetime: $LEASE_TTL_SECONDS seconds"
  log "Allowed unattended mutations:"
  log "  - set the exact bound credential to mode 0600"
  log "  - create or update the exact bound CLI profile from that credential"
  log "Explicitly excluded: IAM, credential generation or rotation, identity changes, and hooks."
  log "Plan digest: $PLAN_DIGEST"
}

issue_repair_lease() {
  local issued_at=""
  local expires_at=""
  local lease_path=""

  ensure_repair_lease_directory
  issued_at="$(date +%s)"
  expires_at="$((issued_at + LEASE_TTL_SECONDS))"
  lease_path="$(
    python3 "$LEASE_HELPER" issue \
      --lease-dir "$LEASE_DIR" \
      --project-id "$PROJECT_ID" \
      --tenant-id "$TENANT_ID" \
      --service-account-id "$LEASE_SERVICE_ACCOUNT_ID" \
      --service-account-name "$SA_NAME" \
      --profile "$PROFILE" \
      --credential-file "$CREDENTIAL_FILE" \
      --credential-sha256 "$LEASE_CREDENTIAL_SHA256" \
      --endpoint "$ENDPOINT" \
      --issued-at "$issued_at" \
      --expires-at "$expires_at" \
      --plan-digest "$PLAN_DIGEST" \
      --uid "$(id -u)"
  )" || die "Failed to create the repair lease."
  log "Repair lease created: $lease_path"
  log "Treat the lease path as private workflow state. It expires automatically and is reusable only for its two idempotent local actions."
}

validate_repair_lease() {
  local lease_json=""

  lease_json="$(
    python3 "$LEASE_HELPER" validate \
      --lease-file "$LEASE_FILE" \
      --home "$HOME" \
      --uid "$(id -u)" \
      --max-ttl "$MAX_LEASE_TTL_SECONDS" \
      --endpoint "$ENDPOINT"
  )"

  PROJECT_ID="$(printf '%s' "$lease_json" | jq -r '.project_id')"
  TENANT_ID="$(printf '%s' "$lease_json" | jq -r '.tenant_id')"
  SA_NAME="$(printf '%s' "$lease_json" | jq -r '.service_account_name')"
  LEASE_SERVICE_ACCOUNT_ID="$(printf '%s' "$lease_json" | jq -r '.service_account_id')"
  PROFILE="$(printf '%s' "$lease_json" | jq -r '.profile')"
  CREDENTIAL_FILE="$(printf '%s' "$lease_json" | jq -r '.credential_file')"
  LEASE_CREDENTIAL_SHA256="$(printf '%s' "$lease_json" | jq -r '.credential_sha256')"
}

run_local_repair() {
  local mode=""

  validate_repair_lease
  mode="$(credential_mode "$CREDENTIAL_FILE")"
  if [[ "$mode" != "600" ]]; then
    log "Correcting the exact lease-bound credential to mode 0600. Because it was previously broader, review whether confirmed credential rotation is required."
    safe_chmod_credential "$CREDENTIAL_FILE"
  fi
  ensure_profile || die "Local profile repair completed, but token minting still fails. Confirm persistent credential or IAM repair; the lease cannot rotate credentials or change IAM."
  verify_project_access || die "Token minting succeeded, but basic project access still fails. Confirm IAM diagnosis and repair; the lease cannot change IAM."
  log "Lease-authorized local repair completed. No IAM, credential generation or rotation, identity, or hook mutation was attempted."
}

build_setup_plan() {
  local group_id=""
  local mode=""
  local legacy_group_id=""
  local legacy_group_name=""
  local sa_id=""

  PLANNED_MUTATIONS=()
  PLAN_NOTES=()
  OBSERVED_SERVICE_ACCOUNT_ID=""
  OBSERVED_GROUP_ID=""
  OBSERVED_CREDENTIAL_SHA256=""
  plan_nebius_dir
  ensure_group_name
  AUTHORIZED_CONVERGENCE_ACTIONS=(
    "create service account '$SA_NAME' in project '$PROJECT_ID' if absent"
    "create group '$GROUP_NAME' under project '$PROJECT_ID' if absent"
    "ensure role '$ROLE' on project '$PROJECT_ID' for group '$GROUP_NAME'"
    "ensure service account '$SA_NAME' belongs to group '$GROUP_NAME'"
    "generate canonical credential '$CREDENTIAL_FILE' if absent"
    "set the canonical credential to mode 0600"
    "create or update CLI profile '$PROFILE' from the canonical credential"
    "if the credential cannot mint a token, back it up and replace it once"
    "verify basic project access and reconcile the same group permit and membership"
  )
  legacy_group_name="codex-agent-$(slugify "$PROJECT_NAME")"

  if [[ -e "$CREDENTIAL_FILE" || -L "$CREDENTIAL_FILE" ]]; then
    verify_credential_local_safety "$CREDENTIAL_FILE"
    verify_credential_identity
    sa_id="$(credential_service_account_id "$CREDENTIAL_FILE")"
    OBSERVED_CREDENTIAL_SHA256="$(credential_sha256 "$CREDENTIAL_FILE")"
    mode="$(credential_mode "$CREDENTIAL_FILE")"
    if [[ "$mode" != "600" ]]; then
      PLANNED_MUTATIONS+=("set credential permissions to 0600")
    fi
  else
    sa_id="$(lookup_service_account_id)"
    if [[ -z "$sa_id" ]]; then
      PLANNED_MUTATIONS+=("create service account '$SA_NAME' in project '$PROJECT_ID'")
    fi
    PLANNED_MUTATIONS+=("generate credential '$CREDENTIAL_FILE'")
    PLANNED_MUTATIONS+=("if the newly generated credential cannot mint a token, back it up and replace it once")
  fi
  OBSERVED_SERVICE_ACCOUNT_ID="$sa_id"
  group_id="$(lookup_group_id)"
  OBSERVED_GROUP_ID="$group_id"
  if [[ "$legacy_group_name" != "$GROUP_NAME" ]]; then
    legacy_group_id="$(lookup_group_id_by_name "$legacy_group_name" "$PROJECT_ID")"
    if [[ -n "$legacy_group_id" ]]; then
      PLAN_NOTES+=("legacy group '$legacy_group_name' remains unchanged; review its permits and cleanup separately")
    fi
  fi
  if [[ -z "$group_id" ]]; then
    PLANNED_MUTATIONS+=("create group '$GROUP_NAME' under project '$PROJECT_ID'")
    PLANNED_MUTATIONS+=("add role '$ROLE' on project '$PROJECT_ID' to the new group")
    PLANNED_MUTATIONS+=("add service account '$SA_NAME' to the new group")
  else
    if ! access_permit_exists "$group_id"; then
      PLANNED_MUTATIONS+=("add role '$ROLE' on project '$PROJECT_ID' to group '$GROUP_NAME'")
    fi
    if [[ -z "$sa_id" ]] || ! membership_exists "$group_id" "$sa_id"; then
      PLANNED_MUTATIONS+=("add service account '$SA_NAME' to group '$GROUP_NAME'")
    fi
  fi

  if profile_exists; then
    if ! profile_can_mint_token; then
      PLANNED_MUTATIONS+=("update CLI profile '$PROFILE' from the canonical credential")
    fi
  else
    PLANNED_MUTATIONS+=("create CLI profile '$PROFILE' from the canonical credential")
  fi
}

compute_plan_digest() {
  {
    printf '%s\0' "setup-confirmation-v4" "$TENANT_ID" "$PROJECT_ID" "$PROJECT_NAME" "$SA_NAME" "$GROUP_NAME" "$PROJECT_ID" "$ROLE" "$CREDENTIAL_FILE" "$PROFILE" "$HUMAN_PROFILE" "$ENDPOINT"
    printf '%s\0' "$OBSERVED_SERVICE_ACCOUNT_ID" "$OBSERVED_GROUP_ID" "$OBSERVED_CREDENTIAL_SHA256"
    printf '%s\0' "${AUTHORIZED_CONVERGENCE_ACTIONS[@]}"
    printf '%s\0' "${PLANNED_MUTATIONS[@]}"
    printf '%s\0' "${PLAN_NOTES[@]}"
  } | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      verify|ensure|replace-credential|repair-lease|repair-local)
        [[ -z "$COMMAND" ]] || die "Pass exactly one command: verify, ensure, replace-credential, repair-lease, or repair-local."
        COMMAND="$1"
        shift
        ;;
      --tenant-id)
        [[ $# -ge 2 ]] || die "--tenant-id requires a value."
        TENANT_ID="$2"
        TENANT_ARG_SET="true"
        shift 2
        ;;
      --project-id)
        [[ $# -ge 2 ]] || die "--project-id requires a value."
        PROJECT_ID="$2"
        PROJECT_ARG_SET="true"
        shift 2
        ;;
      --service-account-name)
        [[ $# -ge 2 ]] || die "--service-account-name requires a value."
        SA_NAME="$2"
        SA_ARG_SET="true"
        shift 2
        ;;
      --role)
        [[ $# -ge 2 ]] || die "--role requires a value."
        ROLE="$2"
        ROLE_ARG_SET="true"
        shift 2
        ;;
      --ttl-seconds)
        [[ $# -ge 2 ]] || die "--ttl-seconds requires a value."
        LEASE_TTL_SECONDS="$2"
        TTL_ARG_SET="true"
        shift 2
        ;;
      --lease-file)
        [[ $# -ge 2 ]] || die "--lease-file requires a value."
        LEASE_FILE="$2"
        shift 2
        ;;
      --install-hook)
        die "--install-hook is no longer supported. Install or refresh the runtime hook from the skills repo root with: ./install-skills.sh --install-hooks agent-nebius-auth-setup/assets/hooks --register-hooks"
        ;;
      --dry-run)
        DRY_RUN="true"
        shift
        ;;
      --confirm)
        [[ $# -ge 2 ]] || die "--confirm requires the digest from the reviewed dry-run plan."
        CONFIRM_DIGEST="$2"
        shift 2
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

  unset TOKEN NEBIUS_IAM_TOKEN NEBIUS_AUTH_CREDENTIALS_FILE NEBIUS_PROJECT_ID

  need_cmd nebius
  need_cmd python3
  need_cmd jq
  need_cmd id
  need_cmd sed
  need_cmd awk
  need_cmd mktemp
  need_cmd stat
  need_cmd unlink
  need_cmd date
  need_cmd cp

  if [[ "$COMMAND" == "repair-local" ]]; then
    validate_repair_lease
    auth_lock_dir="$HOME/.agent-nebius-auth-setup.${PROJECT_ID}.lock"
    profile_lock_dir="$HOME/.agent-nebius-auth-setup.profile.lock"
    acquire_lock "$profile_lock_dir"
    acquire_lock "$auth_lock_dir"
    validate_repair_lease
    run_local_repair
    return 0
  fi

  if [[ "$COMMAND" == "verify" ]]; then
    PROFILE="codex-agent-${PROJECT_ID}"
    CREDENTIAL_FILE="$HOME/.nebius/codex-agent-authkey.${PROJECT_ID}.json"
    verify_agent_runtime
    return 0
  fi

  resolve_project_metadata

  PROFILE="codex-agent-${PROJECT_ID}"
  CREDENTIAL_FILE="$HOME/.nebius/codex-agent-authkey.${PROJECT_ID}.json"
  auth_lock_dir="$HOME/.agent-nebius-auth-setup.${PROJECT_ID}.lock"
  profile_lock_dir="$HOME/.agent-nebius-auth-setup.profile.lock"

  if [[ "$COMMAND" == "repair-lease" ]]; then
    build_repair_lease_plan
    PLAN_DIGEST="$(compute_repair_lease_plan_digest)"
    print_repair_lease_plan
    if is_dry_run; then
      log "Dry run complete. No filesystem, profile, credential, or IAM mutations were performed."
      return 0
    fi
    [[ "$CONFIRM_DIGEST" == "$PLAN_DIGEST" ]] || die "The current repair-lease plan does not match the confirmed dry-run digest. Review a new --dry-run plan before mutation."

    acquire_lock "$profile_lock_dir"
    acquire_lock "$auth_lock_dir"
    build_repair_lease_plan
    PLAN_DIGEST="$(compute_repair_lease_plan_digest)"
    [[ "$CONFIRM_DIGEST" == "$PLAN_DIGEST" ]] || die "The repair-lease plan changed while acquiring the lock. Review a new --dry-run plan before mutation."
    issue_repair_lease
    return 0
  fi

  if [[ "$COMMAND" == "replace-credential" ]]; then
    build_credential_replacement_plan
    PLAN_DIGEST="$(compute_plan_digest)"
    print_plan
    if is_dry_run; then
      log "Dry run complete. No filesystem, profile, credential, or IAM mutations were performed."
      return 0
    fi
    [[ "$CONFIRM_DIGEST" == "$PLAN_DIGEST" ]] || die "The current credential-replacement plan does not match the confirmed dry-run digest. Review a new --dry-run plan before mutation."

    acquire_lock "$profile_lock_dir"
    acquire_lock "$auth_lock_dir"
    build_credential_replacement_plan
    PLAN_DIGEST="$(compute_plan_digest)"
    [[ "$CONFIRM_DIGEST" == "$PLAN_DIGEST" ]] || die "The credential-replacement plan changed while acquiring the lock. Review a new --dry-run plan before mutation."
    run_credential_replacement
    return 0
  fi

  build_setup_plan
  PLAN_DIGEST="$(compute_plan_digest)"
  print_plan
  if is_dry_run; then
    log "Dry run complete. No filesystem, profile, credential, or IAM mutations were performed."
    return 0
  fi
  [[ "$CONFIRM_DIGEST" == "$PLAN_DIGEST" ]] || die "The current plan does not match the confirmed dry-run digest. Review a new --dry-run plan before mutation."

  acquire_lock "$profile_lock_dir"
  acquire_lock "$auth_lock_dir"

  build_setup_plan
  PLAN_DIGEST="$(compute_plan_digest)"
  [[ "$CONFIRM_DIGEST" == "$PLAN_DIGEST" ]] || die "The setup plan changed while acquiring the lock. Review a new --dry-run plan before mutation."

  ensure_nebius_dir

  if [[ -e "$CREDENTIAL_FILE" || -L "$CREDENTIAL_FILE" ]]; then
    log "Credential file exists: $CREDENTIAL_FILE"
    verify_credential_local_safety "$CREDENTIAL_FILE"
    verify_credential_identity
    ensure_existing_credential_flow
  else
    ensure_missing_credential_flow
  fi
  log "Done."
  log "Credential file: $CREDENTIAL_FILE"
  log "Profile: $PROFILE"
  log "Token test: nebius iam get-access-token --no-browser --profile $PROFILE >/dev/null"
}

main "$@"
