#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LEASE_HELPER="$SCRIPT_DIR/agent-nebius-auth-repair-lease.py"
SHARED_HELPER="$SCRIPT_DIR/../assets/hooks/nebius_auth_shared.py"
SA_NAME="codex-agent-sa"
PROJECT_ROLE="admin"
TENANT_ROLE="viewer"
DRY_RUN="false"
LEASE_FILE=""
LEASE_TTL_SECONDS="43200"
MAX_LEASE_TTL_SECONDS="86400"
TENANT_ARG_SET="false"
PROJECT_ARG_SET="false"
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
PLAN_DIGEST=""
PLAN_NOTES=()
OBSERVED_SERVICE_ACCOUNT_ID=""
OBSERVED_GROUP_ID=""
OBSERVED_PERMITS_SHA256=""
OBSERVED_CREDENTIAL_SHA256=""
OBSERVED_CREDENTIAL_SERVICE_ACCOUNT_ID=""
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
    [--dry-run]

  bash scripts/agent-nebius-auth-setup.sh repair-lease \
    --project-id <project_id> \
    [--tenant-id <expected_tenant_id>] \
    [--ttl-seconds <seconds>] \
    [--dry-run]

  bash scripts/agent-nebius-auth-setup.sh repair-local \
    --lease-file <path>

Verifies, creates, or repairs local Codex Agent Nebius service-account auth.
The verify command is read-only and never requires a human/admin profile.
Runtime renewable context is handled by the installed PreToolUse hook, not by
this script.
Setup is explicit-only. Invoking it authorizes one bounded convergence for the
resolved project: one tenant-parented custom group, exactly project 'admin'
plus tenant 'viewer', one service-account membership, the canonical credential,
and its CLI profile. An optional --dry-run previews the same target without
mutation. If the canonical credential cannot mint a token, ensure backs it up
and replaces it at most once.

If the existing canonical credential references a service account that the
validated human profile proves is no longer present, ensure creates or reuses
the fixed service account with that human profile, reconciles its exact IAM
shape, generates one new authorized-key credential, backs up the stale file,
and rebinds the agent profile. Authentication, authorization, transient, and
unclassified identity-lookup failures still stop without replacement.

An explicitly requested repair lease lasts 12 hours by default (24 hours maximum) and can
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
  [[ -n "$COMMAND" ]] || die "A command is required: verify, ensure, repair-lease, or repair-local."
  if [[ "$COMMAND" == "repair-local" ]]; then
    [[ -n "$LEASE_FILE" ]] || die "repair-local requires --lease-file <path>."
    [[ "$TENANT_ARG_SET" == "false" && "$PROJECT_ARG_SET" == "false" && "$TTL_ARG_SET" == "false" ]] \
      || die "repair-local derives its exact target from the lease; pass only --lease-file."
    [[ "$DRY_RUN" == "false" ]] || die "repair-local does not accept --dry-run."
    return 0
  fi

  [[ "$COMMAND" == "verify" || "$COMMAND" == "ensure" || "$COMMAND" == "repair-lease" ]] || die "Unsupported command: $COMMAND"
  [[ -n "$PROJECT_ID" ]] || die "--project-id is required. Discover it from the current session before setup."
  [[ "$PROJECT_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]*$ ]] || die "--project-id contains unsupported characters."
  [[ -z "$TENANT_ID" || "$TENANT_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]*$ ]] || die "--tenant-id contains unsupported characters."
  if [[ "$COMMAND" == "verify" ]]; then
    [[ "$TENANT_ARG_SET" == "false" && "$TTL_ARG_SET" == "false" ]] \
      || die "verify accepts only --project-id."
    [[ "$DRY_RUN" == "false" && -z "$LEASE_FILE" ]] \
      || die "verify accepts no mutation, lease, dry-run, or repair options."
    return 0
  fi
  [[ -z "$LEASE_FILE" ]] || die "--lease-file is valid only with repair-local."
  if [[ "$COMMAND" == "ensure" ]]; then
    [[ "$TTL_ARG_SET" == "false" ]] || die "$COMMAND does not accept --ttl-seconds."
  fi
  if [[ "$COMMAND" == "repair-lease" ]]; then
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

  if [[ -n "${GROUP_NAME:-}" ]]; then
    return 0
  fi

  project_hash="$(
    python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:20])' \
      "$PROJECT_ID"
  )"
  GROUP_NAME="codex-agent-${project_hash}"
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
  local group_name="$1"
  local parent_id="$2"
  local scope_label="$3"
  local create_output=""
  local group_id=""

  group_id="$(lookup_group_id_by_name "$group_name" "$parent_id")"

  if [[ -n "$group_id" ]]; then
    printf '%s\n' "$group_id"
    return 0
  fi

  log "Creating $scope_label group: $group_name"
  if create_output="$(nebius iam group create \
      --parent-id "$parent_id" \
      --name "$group_name" \
      --no-browser \
      --profile "$HUMAN_PROFILE" \
      --format json)"; then
    group_id="$(printf '%s' "$create_output" | json_get_id)"
  else
    group_id="$(lookup_group_id_by_name "$group_name" "$parent_id")"
    [[ -n "$group_id" ]] \
      || die "Failed to create group and no converged group exists."
    log "Group converged after a create conflict."
  fi
  [[ -n "$group_id" ]] || die "Failed to create group."
  printf '%s\n' "$group_id"
}

lookup_group_id() {
  lookup_group_id_by_name "$GROUP_NAME" "$TENANT_ID"
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

list_access_permits_canonical() {
  local group_id="$1"
  local response=""

  response="$(nebius iam access-permit list \
    --parent-id "$group_id" \
    --all \
    --no-browser \
    --profile "$HUMAN_PROFILE" \
    --format json)" \
    || die "Access-permit lookup failed; refusing to treat unknown state as absent."
  printf '%s' "$response" | jq -ce '
        def rows:
          if type == "array" then .
          elif (.items? | type) == "array" then .items
          elif type == "object" and length == 0 then []
          else error("unsupported access-permit response") end;
        [rows[] | {
          resource_id: (.spec.resource_id // .resource_id // ""),
          role: (.spec.role // .role // "")
        }] | sort_by(.resource_id, .role)
      ' || die "Access-permit lookup returned malformed JSON."
}

validated_agent_permits_canonical() {
  local group_id="$1"
  local permits=""

  permits="$(list_access_permits_canonical "$group_id")"
  printf '%s' "$permits" | jq -e \
    --arg project "$PROJECT_ID" \
    --arg project_role "$PROJECT_ROLE" \
    --arg tenant "$TENANT_ID" \
    --arg tenant_role "$TENANT_ROLE" '
      all(.[];
        ((.resource_id == $project) and (.role == $project_role))
        or ((.resource_id == $tenant) and (.role == $tenant_role)))
      and (([.[] | [.resource_id, .role]] | unique | length) == length)
    ' >/dev/null \
    || die "Agent group '$GROUP_NAME' must contain only project '$PROJECT_ROLE' and tenant '$TENANT_ROLE' permits, without duplicates; refusing mutation."
  printf '%s\n' "$permits"
}

create_access_permit() {
  local group_id="$1"
  local resource_id="$2"
  local role="$3"
  local scope_label="$4"
  local output_file=""
  local permits=""

  if is_dry_run; then
    log "Would add role '$role' on $scope_label '$resource_id' to group '$GROUP_NAME'."
    return 0
  fi

  output_file="$(mktemp)"
  if nebius iam access-permit create \
    --parent-id "$group_id" \
    --resource-id "$resource_id" \
    --no-browser \
    --profile "$HUMAN_PROFILE" \
    --role "$role" >"$output_file" 2>&1; then
    cleanup_file "$output_file"
    return 0
  fi

  permits="$(validated_agent_permits_canonical "$group_id")"
  if printf '%s' "$permits" | jq -e \
      --arg resource "$resource_id" \
      --arg role "$role" '
        any(.[]; (.resource_id == $resource) and (.role == $role))
      ' >/dev/null; then
    cleanup_file "$output_file"
    log "Access permit converged after a create conflict."
    return 0
  fi

  cat "$output_file" >&2
  cleanup_file "$output_file"
  die "Failed to create access permit."
}

ensure_exact_agent_group_permits() {
  local group_id="$1"
  local permits=""
  local project_missing="false"
  local tenant_missing="false"

  permits="$(validated_agent_permits_canonical "$group_id")"
  if ! printf '%s' "$permits" | jq -e \
      --arg resource "$PROJECT_ID" \
      --arg role "$PROJECT_ROLE" '
        any(.[]; (.resource_id == $resource) and (.role == $role))
      ' >/dev/null; then
    project_missing="true"
  fi
  if ! printf '%s' "$permits" | jq -e \
      --arg resource "$TENANT_ID" \
      --arg role "$TENANT_ROLE" '
        any(.[]; (.resource_id == $resource) and (.role == $role))
      ' >/dev/null; then
    tenant_missing="true"
  fi

  if [[ "$project_missing" == "true" ]]; then
    create_access_permit "$group_id" "$PROJECT_ID" "$PROJECT_ROLE" "project"
  fi
  if [[ "$tenant_missing" == "true" ]]; then
    create_access_permit "$group_id" "$TENANT_ID" "$TENANT_ROLE" "tenant"
  fi

  if [[ "$project_missing" == "true" || "$tenant_missing" == "true" ]]; then
    permits="$(validated_agent_permits_canonical "$group_id")"
  fi
  printf '%s' "$permits" | jq -e \
    --arg project "$PROJECT_ID" \
    --arg project_role "$PROJECT_ROLE" \
    --arg tenant "$TENANT_ID" \
    --arg tenant_role "$TENANT_ROLE" '
      length == 2
      and any(.[]; (.resource_id == $project) and (.role == $project_role))
      and any(.[]; (.resource_id == $tenant) and (.role == $tenant_role))
    ' >/dev/null \
    || die "Agent group '$GROUP_NAME' did not converge to exactly project '$PROJECT_ROLE' and tenant '$TENANT_ROLE'."
}

managed_group_membership_exact() {
  local group_id="$1"
  local sa_id="$2"
  local members='[]'
  local next_page_token=""
  local page_count=0
  local page_members=""
  local page_token=""
  local page_args=()
  local response=""
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
      and (
        length == 0
        or (
          (.memberships | type == "array")
          and ((.next_page_token // "") | type == "string")
        )
      )
    ' >/dev/null \
      || die "Group-membership lookup returned malformed JSON."
    page_members="$(printf '%s' "$response" | jq -ce '
      [(.memberships // [])[] |
        (.spec.member_id // .member_id // .metadata.id // .id // "")]
    ')" || die "Group-membership lookup returned malformed JSON."
    printf '%s' "$page_members" | jq -e '
      all(.[]; (type == "string") and (length > 0))
    ' >/dev/null \
      || die "Group-membership lookup returned an empty or malformed member ID."
    members="$(jq -cn --argjson prior "$members" --argjson page "$page_members" \
      '$prior + $page')"
    next_page_token="$(printf '%s' "$response" | jq -r '.next_page_token // empty')"
    [[ -n "$next_page_token" ]] || break
    for seen_token in "${seen_page_tokens[@]}"; do
      [[ "$seen_token" != "$next_page_token" ]] \
        || die "Group-membership lookup repeated a pagination token."
    done
    seen_page_tokens+=("$next_page_token")
    page_token="$next_page_token"
  done

  if printf '%s' "$members" | jq -e --arg member "$sa_id" \
      'length == 1 and .[0] == $member' >/dev/null; then
    return 0
  fi
  if printf '%s' "$members" | jq -e 'length == 0' >/dev/null; then
    return 1
  fi
  die "Agent group '$GROUP_NAME' must contain only one membership for service account '$sa_id'; refusing mutation."
}

ensure_group_membership() {
  local group_id="$1"
  local sa_id="$2"
  local output_file=""

  if is_dry_run; then
    log "Would ensure service account '$sa_id' is a member of group '$group_id'."
    return 0
  fi

  if managed_group_membership_exact "$group_id" "$sa_id"; then
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
    managed_group_membership_exact "$group_id" "$sa_id" \
      || die "Group membership create succeeded, but exact membership could not be verified."
    return 0
  fi

  if managed_group_membership_exact "$group_id" "$sa_id"; then
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

  group_id="$(get_or_create_group "$GROUP_NAME" "$TENANT_ID" "agent authorization")"
  ensure_exact_agent_group_permits "$group_id"
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

service_account_get_error_is_not_found() {
  local status="$1"
  local error_file="$2"
  local rpc_code=""

  [[ "$status" -ne 0 ]] || return 1
  rpc_code="$(python3 -c '
import pathlib
import re
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
codes = re.findall(r"rpc error:\s*code\s*=\s*([a-z][a-z0-9_]*)", text, re.I)
print(codes[0].lower() if len(codes) == 1 else "")
' "$error_file")" || return 1
  [[ "$rpc_code" == "notfound" ]]
}

credential_service_account_is_current() {
  local sa_id=""
  local error_file=""
  local metadata=""
  local output_file=""
  local resolved_name=""
  local resolved_parent_id=""
  local status=0

  sa_id="$(credential_service_account_id "$CREDENTIAL_FILE" || true)"
  [[ -n "$sa_id" ]] || die "Existing credential does not contain a service-account ID; refusing mutation."

  output_file="$(mktemp)"
  error_file="$(mktemp)"
  if nebius iam service-account get \
      --id "$sa_id" \
      --no-browser \
      --retries 1 \
      --profile "$HUMAN_PROFILE" \
      --format json >"$output_file" 2>"$error_file"; then
    metadata="$(<"$output_file")"
  else
    status=$?
    if service_account_get_error_is_not_found "$status" "$error_file"; then
      cleanup_file "$output_file"
      cleanup_file "$error_file"
      return 1
    fi
    cat "$error_file" >&2
    cleanup_file "$output_file"
    cleanup_file "$error_file"
    die "Failed to resolve service account '$sa_id' from the existing credential; refusing mutation."
  fi
  cleanup_file "$output_file"
  cleanup_file "$error_file"
  resolved_name="$(printf '%s' "$metadata" | json_get_name)"
  resolved_parent_id="$(printf '%s' "$metadata" | json_get_parent_id)"
  [[ "$resolved_name" == "$SA_NAME" ]] || die "Existing credential belongs to service account '$resolved_name', not '$SA_NAME'; refusing mutation."
  [[ "$resolved_parent_id" == "$PROJECT_ID" ]] || die "Existing credential service account belongs to project '$resolved_parent_id', not '$PROJECT_ID'; refusing mutation."
}

verify_credential_identity() {
  credential_service_account_is_current \
    || die "Existing credential references a service account that is no longer present; a repair lease cannot bootstrap a replacement identity."
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
  log "  credential service-account ID: ${OBSERVED_CREDENTIAL_SERVICE_ACCOUNT_ID:-absent}"
  log "  authorization group: $GROUP_NAME"
  log "  group parent: $TENANT_ID"
  log "  observed group ID: ${OBSERVED_GROUP_ID:-absent}"
  log "  required permits: '$PROJECT_ROLE' on project '$PROJECT_ID'; '$TENANT_ROLE' on tenant '$TENANT_ID'"
  log "  observed permits SHA-256: ${OBSERVED_PERMITS_SHA256:-absent}"
  log "  credential file: $CREDENTIAL_FILE"
  log "  observed credential SHA-256: ${OBSERVED_CREDENTIAL_SHA256:-absent}"
  log "  CLI profile: $PROFILE"
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
    normalized="$(printf '%s' "$line" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/[[:space:]]+\[(active|default)\]$//')"
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

profile_service_account_id() {
  nebius iam whoami \
    --no-browser \
    --profile "$PROFILE" \
    --format json 2>/dev/null \
    | jq -er '.service_account_profile.info.metadata.id // empty'
}

profile_matches_service_account() {
  local expected_service_account_id="$1"
  local actual_service_account_id=""

  actual_service_account_id="$(profile_service_account_id)" \
    || die "CLI profile '$PROFILE' can mint a token but its service-account identity cannot be resolved."
  [[ "$actual_service_account_id" == "$expected_service_account_id" ]]
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
  profile_matches_service_account "$service_account_id" \
    || die "Agent runtime profile service-account identity does not match the canonical credential."
  profile_has_project_access "$service_account_id" \
    || die "Agent runtime profile minted a token but does not resolve the canonical service account with project access."
  resolve_agent_profile_tenant
  profile_has_tenant_quota_access \
    || die "Agent runtime profile minted a token and can reach the project but lacks authoritative tenant quota-allowance read access. Invoke agent-nebius-auth-setup explicitly to reconcile the fixed tenant viewer grant."
  log "Agent runtime auth is ready for project '$PROJECT_ID' and tenant quota reads."
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

ensure_profile_binding() {
  local sa_id="$1"
  local force_rebind="${2:-false}"

  if is_dry_run; then
    log "Would create or update profile '$PROFILE' from '$CREDENTIAL_FILE'."
    return 0
  fi

  [[ -f "$CREDENTIAL_FILE" ]] || return 1

  if profile_exists; then
    if [[ "$force_rebind" != "true" ]] \
        && profile_can_mint_token \
        && profile_matches_service_account "$sa_id"; then
      log "CLI profile already exists and can mint a token: $PROFILE"
      return 0
    fi
    log "Updating profile: $PROFILE"
    run_preserving_active_profile nebius profile update "$PROFILE" \
      --endpoint "$ENDPOINT" \
      --tenant-id "$TENANT_ID" \
      --parent-id "$PROJECT_ID" \
      --service-account-file "$CREDENTIAL_FILE" >/dev/null \
      || die "Failed to update CLI profile '$PROFILE'; no additional credential replacement was attempted."
  else
    log "Creating profile: $PROFILE"
    run_preserving_active_profile nebius profile create "$PROFILE" \
      --endpoint "$ENDPOINT" \
      --tenant-id "$TENANT_ID" \
      --parent-id "$PROJECT_ID" \
      --service-account-file "$CREDENTIAL_FILE" >/dev/null \
      || die "Failed to create CLI profile '$PROFILE'; no additional credential replacement was attempted."
  fi

  profile_can_mint_token && profile_matches_service_account "$sa_id"
}

credential_auth_failure_is_proven() {
  local error_file=""
  local normalized_error=""

  error_file="$(mktemp)"
  if nebius iam get-access-token \
      --no-browser \
      --auth-timeout 10s \
      --timeout 20s \
      --retries 1 \
      --profile "$PROFILE" </dev/null >/dev/null 2>"$error_file"; then
    cleanup_file "$error_file"
    return 1
  fi
  normalized_error="$(tr '[:upper:]' '[:lower:]' <"$error_file")"
  cleanup_file "$error_file"
  case "$normalized_error" in
    *unauthenticated*|*invalid*credential*|*authentication*failed*|*signature*failed*|*public*key*not*found*)
      return 0
      ;;
    *)
      die "Token minting failed without a classified credential-authentication error; no credential replacement was attempted."
      ;;
  esac
}

profile_has_project_access() {
  local expected_service_account_id="$1"
  local metadata=""
  local resolved_service_account_id=""

  metadata="$(
    nebius iam service-account get-by-name \
      --name "$SA_NAME" \
      --parent-id "$PROJECT_ID" \
      --no-browser \
      --profile "$PROFILE" \
      --format json 2>/dev/null
  )" || return 1
  resolved_service_account_id="$(printf '%s' "$metadata" | json_get_id)"
  [[ -n "$resolved_service_account_id" && "$resolved_service_account_id" == "$expected_service_account_id" ]]
}

resolve_agent_profile_tenant() {
  local expected_tenant_id="$TENANT_ID"
  local metadata=""
  local resolved_id=""
  local resolved_tenant_id=""

  metadata="$(
    nebius iam project get \
      --id "$PROJECT_ID" \
      --no-browser \
      --profile "$PROFILE" \
      --format json
  )" || die "Agent runtime profile minted a token but cannot read project metadata."
  resolved_id="$(printf '%s' "$metadata" | json_get_id)"
  resolved_tenant_id="$(printf '%s' "$metadata" | json_get_parent_id)"
  [[ "$resolved_id" == "$PROJECT_ID" ]] \
    || die "Agent project metadata ID '$resolved_id' does not match requested project '$PROJECT_ID'."
  [[ -n "$resolved_tenant_id" ]] \
    || die "Agent project metadata for '$PROJECT_ID' is missing metadata.parent_id."
  [[ "$resolved_tenant_id" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]*$ ]] \
    || die "Agent project metadata contains an unsupported tenant ID."
  if [[ -n "$expected_tenant_id" && "$expected_tenant_id" != "$resolved_tenant_id" ]]; then
    die "Agent project metadata tenant '$resolved_tenant_id' does not match the reviewed tenant '$expected_tenant_id'."
  fi
  TENANT_ID="$resolved_tenant_id"
}

profile_has_tenant_quota_access() {
  [[ -n "$TENANT_ID" ]] || return 1
  nebius quotas quota-allowance list \
    --parent-id "$TENANT_ID" \
    --page-size 1 \
    --no-browser \
    --profile "$PROFILE" \
    --format json >/dev/null 2>&1
}

verify_project_access() {
  local expected_service_account_id="$1"

  if is_dry_run; then
    log "Would verify basic project access through profile '$PROFILE'."
    return 0
  fi

  profile_has_project_access "$expected_service_account_id"
}

verify_tenant_quota_access() {
  if is_dry_run; then
    log "Would verify read-only tenant quota-allowance access through profile '$PROFILE'."
    return 0
  fi

  resolve_agent_profile_tenant
  profile_has_tenant_quota_access
}

ensure_existing_credential_flow() {
  local sa_id=""

  safe_chmod_credential "$CREDENTIAL_FILE"
  sa_id="$(credential_service_account_id "$CREDENTIAL_FILE")"
  ensure_iam_shape_for_service_account "$sa_id"
  ensure_working_profile_with_one_replacement "$sa_id"
  verify_project_access "$sa_id" || die "Basic project access or canonical service-account identity still fails after IAM reconciliation."
  verify_tenant_quota_access \
    || die "The agent profile lacks authoritative tenant quota-allowance read access after IAM reconciliation."
}

ensure_missing_credential_flow() {
  local sa_id=""

  sa_id="$(get_or_create_service_account)"
  ensure_iam_shape_for_service_account "$sa_id"
  ensure_credential_file "$sa_id"
  ensure_working_profile_with_one_replacement "$sa_id" "true"
  verify_project_access "$sa_id" || die "Token minted, but basic project access or canonical service-account identity verification failed."
  verify_tenant_quota_access \
    || die "Token minted, but authoritative tenant quota-allowance read access failed."
}

ensure_deleted_service_account_credential_flow() {
  local replacement_sa_id=""
  local stale_sa_id=""

  stale_sa_id="$(credential_service_account_id "$CREDENTIAL_FILE")"
  log "The canonical credential references a service account that is no longer present; bootstrapping the fixed account with the validated human profile."
  replacement_sa_id="$(get_or_create_service_account)"
  [[ -n "$replacement_sa_id" && "$replacement_sa_id" != "$stale_sa_id" ]] \
    || die "The canonical service-account lookup did not prove a distinct replacement for the deleted credential identity; refusing mutation."
  ensure_iam_shape_for_service_account "$replacement_sa_id"
  replace_credential_file "$replacement_sa_id"
  if ! ensure_profile_binding "$replacement_sa_id" "true"; then
    if credential_auth_failure_is_proven; then
      die "The replacement credential cannot mint a token; the stale credential was replaced once and no second credential was generated."
    fi
    die "The rebuilt profile does not match the replacement credential; no second credential was generated."
  fi
  verify_project_access "$replacement_sa_id" \
    || die "Token minted, but basic project access or replacement service-account identity verification failed."
  verify_tenant_quota_access \
    || die "Token minted, but authoritative tenant quota-allowance read access failed."
}

ensure_working_profile_with_one_replacement() {
  local sa_id="$1"
  local force_rebind="${2:-false}"

  if ensure_profile_binding "$sa_id" "$force_rebind"; then
    return 0
  fi

  if ! credential_auth_failure_is_proven; then
    profile_matches_service_account "$sa_id" \
      || die "CLI profile service-account identity does not match the canonical credential."
    return 0
  fi
  log "The canonical credential cannot mint a token; replacing it once within this explicit setup invocation."
  replace_credential_file "$sa_id"
  ensure_profile_binding "$sa_id" \
    || die "Profile still cannot mint a token after one bounded credential replacement; no second replacement was attempted: $PROFILE"
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
    || die "A repair lease requires an existing working credential. Complete setup, then request the lease separately."
  verify_credential_local_safety "$CREDENTIAL_FILE"
  verify_credential_identity
  [[ "$(credential_mode "$CREDENTIAL_FILE")" == "600" ]] \
    || die "A repair lease requires the canonical credential at mode 0600. Complete setup or repair first."
  LEASE_SERVICE_ACCOUNT_ID="$(credential_service_account_id "$CREDENTIAL_FILE")"
  credential_hash="$(credential_sha256 "$CREDENTIAL_FILE")"
  [[ -n "$credential_hash" ]] || die "Failed to fingerprint the canonical credential."
  LEASE_CREDENTIAL_SHA256="$credential_hash"
  profile_exists || die "A repair lease requires the matching CLI profile. Complete setup first."
  profile_can_mint_token || die "The matching profile cannot mint a token. Complete repair before issuing a lease."
  profile_matches_service_account "$LEASE_SERVICE_ACCOUNT_ID" \
    || die "The matching profile identity differs from the canonical credential. Complete repair before issuing a lease."
  profile_has_project_access "$LEASE_SERVICE_ACCOUNT_ID" \
    || die "The matching profile can mint a token but does not resolve the canonical service account with project access. Complete IAM repair before issuing a lease."
  profile_has_tenant_quota_access \
    || die "The matching profile lacks authoritative tenant quota-allowance read access. Complete IAM repair before issuing a lease."
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
  log "Lease integrity digest: $PLAN_DIGEST"
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
  local lease_service_account_name=""

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
  lease_service_account_name="$(printf '%s' "$lease_json" | jq -r '.service_account_name')"
  [[ "$lease_service_account_name" == "$SA_NAME" ]] \
    || die "Repair lease service-account name is not the canonical '$SA_NAME'."
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
    log "Correcting the exact lease-bound credential to mode 0600. Because it was previously broader, review whether credential rotation is required."
    safe_chmod_credential "$CREDENTIAL_FILE"
  fi
  ensure_profile_binding "$LEASE_SERVICE_ACCOUNT_ID" \
    || die "Local profile repair completed, but token minting or service-account identity verification still fails. Confirm persistent credential or IAM repair; the lease cannot rotate credentials or change IAM."
  verify_project_access "$LEASE_SERVICE_ACCOUNT_ID" || die "Token minting succeeded, but canonical service-account identity or basic project access still fails. Confirm IAM diagnosis and repair; the lease cannot change IAM."
  profile_has_tenant_quota_access || die "Token minting and project access succeeded, but tenant quota-allowance read access still fails. Confirm IAM diagnosis and repair; the lease cannot change IAM."
  log "Lease-authorized local repair completed. No IAM, credential generation or rotation, identity, or hook mutation was attempted."
}

build_setup_plan() {
  local credential_missing="false"
  local group_id=""
  local mode=""
  local permits=""
  local profile_needs_rebind="false"
  local sa_id=""

  PLANNED_MUTATIONS=()
  PLAN_NOTES=()
  OBSERVED_SERVICE_ACCOUNT_ID=""
  OBSERVED_GROUP_ID=""
  OBSERVED_PERMITS_SHA256=""
  OBSERVED_CREDENTIAL_SHA256=""
  OBSERVED_CREDENTIAL_SERVICE_ACCOUNT_ID=""
  plan_nebius_dir
  ensure_group_name

  if [[ -e "$CREDENTIAL_FILE" || -L "$CREDENTIAL_FILE" ]]; then
    verify_credential_local_safety "$CREDENTIAL_FILE"
    OBSERVED_CREDENTIAL_SERVICE_ACCOUNT_ID="$(credential_service_account_id "$CREDENTIAL_FILE")"
    OBSERVED_CREDENTIAL_SHA256="$(credential_sha256 "$CREDENTIAL_FILE")"
    if credential_service_account_is_current; then
      sa_id="$OBSERVED_CREDENTIAL_SERVICE_ACCOUNT_ID"
      mode="$(credential_mode "$CREDENTIAL_FILE")"
      if [[ "$mode" != "600" ]]; then
        PLANNED_MUTATIONS+=("set credential permissions to 0600")
      fi
    else
      credential_missing="true"
      sa_id="$(lookup_service_account_id)"
      [[ -z "$sa_id" || "$sa_id" != "$OBSERVED_CREDENTIAL_SERVICE_ACCOUNT_ID" ]] \
        || die "The canonical service-account lookup did not prove a distinct replacement for the deleted credential identity; refusing mutation."
      if [[ -z "$sa_id" ]]; then
        PLANNED_MUTATIONS+=("create service account '$SA_NAME' in project '$PROJECT_ID' with the validated human profile")
      fi
      PLANNED_MUTATIONS+=("after exact IAM convergence, generate one replacement authorized-key credential, back up the stale credential at mode 0600, and atomically install the replacement")
    fi
  else
    credential_missing="true"
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
  if [[ -z "$group_id" ]]; then
    PLANNED_MUTATIONS+=("create group '$GROUP_NAME' under tenant '$TENANT_ID'")
    PLANNED_MUTATIONS+=("add role '$PROJECT_ROLE' on project '$PROJECT_ID' to the new group")
    PLANNED_MUTATIONS+=("add role '$TENANT_ROLE' on tenant '$TENANT_ID' to the new group")
    PLANNED_MUTATIONS+=("add service account '$SA_NAME' to the new group")
  else
    permits="$(validated_agent_permits_canonical "$group_id")"
    OBSERVED_PERMITS_SHA256="$(printf '%s' "$permits" | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
    if ! printf '%s' "$permits" | jq -e --arg resource "$PROJECT_ID" --arg role "$PROJECT_ROLE" \
        'any(.[]; (.resource_id == $resource) and (.role == $role))' >/dev/null; then
      PLANNED_MUTATIONS+=("add role '$PROJECT_ROLE' on project '$PROJECT_ID' to group '$GROUP_NAME'")
    fi
    if ! printf '%s' "$permits" | jq -e --arg resource "$TENANT_ID" --arg role "$TENANT_ROLE" \
        'any(.[]; (.resource_id == $resource) and (.role == $role))' >/dev/null; then
      PLANNED_MUTATIONS+=("add role '$TENANT_ROLE' on tenant '$TENANT_ID' to group '$GROUP_NAME'")
    fi
    if [[ -z "$sa_id" ]]; then
      managed_group_membership_exact "$group_id" "" || true
      PLANNED_MUTATIONS+=("add service account '$SA_NAME' to group '$GROUP_NAME'")
    elif ! managed_group_membership_exact "$group_id" "$sa_id"; then
      PLANNED_MUTATIONS+=("add service account '$SA_NAME' to group '$GROUP_NAME'")
    fi
  fi

  if profile_exists; then
    if [[ "$credential_missing" == "true" ]]; then
      profile_needs_rebind="true"
    elif [[ -z "$sa_id" ]]; then
      profile_needs_rebind="true"
    elif ! profile_can_mint_token; then
      profile_needs_rebind="true"
    elif ! profile_matches_service_account "$sa_id"; then
      profile_needs_rebind="true"
    fi
    if [[ "$profile_needs_rebind" == "true" ]]; then
      PLANNED_MUTATIONS+=("update CLI profile '$PROFILE' from the canonical credential")
    fi
  else
    profile_needs_rebind="true"
    PLANNED_MUTATIONS+=("create CLI profile '$PROFILE' from the canonical credential")
  fi
  if [[ "$profile_needs_rebind" == "true" && "$credential_missing" != "true" && -n "$OBSERVED_CREDENTIAL_SHA256" ]]; then
    PLANNED_MUTATIONS+=("if the rebuilt profile still has a classified credential-authentication failure, back up and replace the canonical credential once")
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      verify|ensure|repair-lease|repair-local)
        [[ -z "$COMMAND" ]] || die "Pass exactly one command: verify, ensure, repair-lease, or repair-local."
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
  need_cmd tr
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

  PROFILE="codex-agent-${PROJECT_ID}"
  CREDENTIAL_FILE="$HOME/.nebius/codex-agent-authkey.${PROJECT_ID}.json"
  auth_lock_dir="$HOME/.agent-nebius-auth-setup.${PROJECT_ID}.lock"
  profile_lock_dir="$HOME/.agent-nebius-auth-setup.profile.lock"

  if ! is_dry_run; then
    acquire_lock "$profile_lock_dir"
    acquire_lock "$auth_lock_dir"
  fi
  resolve_project_metadata
  ensure_group_name

  if [[ "$COMMAND" == "repair-lease" ]]; then
    build_repair_lease_plan
    PLAN_DIGEST="$(compute_repair_lease_plan_digest)"
    print_repair_lease_plan
    if is_dry_run; then
      log "Dry run complete. No filesystem, profile, credential, or IAM mutations were performed."
      return 0
    fi
    issue_repair_lease
    return 0
  fi

  if is_dry_run; then
    build_setup_plan
    print_plan
    log "Dry run complete. No filesystem, profile, credential, or IAM mutations were performed."
    return 0
  fi

  ensure_nebius_dir

  if [[ -e "$CREDENTIAL_FILE" || -L "$CREDENTIAL_FILE" ]]; then
    log "Credential file exists: $CREDENTIAL_FILE"
    verify_credential_local_safety "$CREDENTIAL_FILE"
    if credential_service_account_is_current; then
      ensure_existing_credential_flow
    else
      ensure_deleted_service_account_credential_flow
    fi
  else
    ensure_missing_credential_flow
  fi
  log "Done."
  log "Credential file: $CREDENTIAL_FILE"
  log "Profile: $PROFILE"
  log "Token test: CODEX_NEBIUS_PROJECT_ID=$PROJECT_ID nebius iam get-access-token --no-browser --profile $PROFILE >/dev/null"
}

main "$@"
