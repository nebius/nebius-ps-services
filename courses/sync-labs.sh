#!/usr/bin/env bash
# Push the local course working tree to a login node using Git, SSH and rsync.
set -euo pipefail

S_RESET='' S_BOLD='' S_DIM='' S_RED='' S_AMBER='' S_GREEN='' S_CYAN=''
work_dir='' active_pid=''

init_output_style() {
  if [[ ( -t 1 || -t 2 ) && ${TERM:-} != dumb && ! ${NO_COLOR+x} ]]; then
    S_RESET=$'\033[0m' S_BOLD=$'\033[1m' S_DIM=$'\033[2m'
    S_RED=$'\033[31m' S_AMBER=$'\033[33m'
    S_GREEN=$'\033[32m' S_CYAN=$'\033[36m'
  fi
}

log_error() { printf '%s%sERROR:%s %s\n' "$S_RED" "$S_BOLD" "$S_RESET" "$*" >&2; }
log_warn() { printf '%s%s%s\n' "$S_AMBER" "$*" "$S_RESET" >&2; }
log_note() { printf '%s%s%s\n' "$S_DIM" "$*" "$S_RESET"; }
log_success() { printf '%s%s%s\n' "$S_GREEN" "$*" "$S_RESET"; }
die() { log_error "$*"; exit 1; }

show_usage() {
  printf '%sUsage%s\n' "$S_BOLD" "$S_RESET"
  printf '  sync-labs.sh [options] [user@]TARGET\n\n'
  printf 'Run locally from your Git clone. TARGET is a DNS name, IPv4/IPv6 address,\n'
  printf 'or SSH alias. Bare targets use root; user@TARGET selects another account.\n'
  printf 'SSH supplies name resolution and key defaults.\n\n'
  printf '%sOptions%s\n' "$S_BOLD" "$S_RESET"
  printf '  --dry-run         Preview without changing the remote destination\n'
  printf '  --dest NAME       Direct subfolder of remote home (default: courses)\n'
  printf '  --port PORT       SSH port, from 1 through 65535\n'
  printf '  --identity FILE   SSH private-key file\n'
  printf '  -h, --help        Show this help\n'
  printf '  --                End option parsing\n\n'
  printf '%sExamples%s\n' "$S_BOLD" "$S_RESET"
  printf '  %s./sync-labs.sh login.example.com%s\n' "$S_CYAN" "$S_RESET"
  printf '  %s./sync-labs.sh 192.0.2.10%s\n' "$S_CYAN" "$S_RESET"
  printf '  %s./sync-labs.sh --dry-run user@slurm-login%s\n' "$S_CYAN" "$S_RESET"
  printf '  %s./sync-labs.sh --port 2222 --identity ~/.ssh/id_ed25519 user@192.0.2.10%s\n\n' "$S_CYAN" "$S_RESET"
  printf 'Matching remote source files are updated; remote-only files are kept.\n'
  printf 'Git ignore rules exclude untracked environments, builds and results.\n'
  printf 'The selected account overrides SSH config User; use user@TARGET for non-root.\n'
  printf 'Sync before submitting jobs. Run existing sbatch commands from a course root.\n'
}

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }

cleanup() {
  # Only the exact private directory allocated by this invocation is eligible.
  if [[ -n $work_dir && $work_dir == /tmp/course-sync.* && -d $work_dir && ! -L $work_dir ]]; then
    find "$work_dir" -depth -delete
  fi
}

# Waiting on an asynchronous child lets Bash run signal traps immediately.
# Keep stdin available for SSH authentication and host-key confirmation.
run_remote() {
  local status
  "$@" <&0 &
  active_pid=$!
  if wait "$active_pid"; then status=0; else status=$?; fi
  active_pid=''
  return "$status"
}

cancel_sync() {
  local status=$1 transport_pid=''
  trap '' INT TERM HUP
  if [[ -n $active_pid ]]; then
    # rsync owns a separate SSH child; stop it as well as the direct child.
    if [[ -f $work_dir/ssh.pid ]]; then
      read -r transport_pid < "$work_dir/ssh.pid" || :
      if [[ $transport_pid =~ ^[1-9][0-9]*$ ]]; then
        kill -TERM "$transport_pid" 2>/dev/null || :
      fi
    fi
    kill -TERM "$active_pid" 2>/dev/null || :
    wait "$active_pid" 2>/dev/null || :
  fi
  log_warn 'Sync cancelled. Rerun the command before submitting jobs.'
  exit "$status"
}

# Quote one word for the POSIX shell on the receiving host.
shell_quote() { printf "'%s'" "${1//\'/\'\\\'\'}"; }

parse_target() {
  local target=$1 host user=root
  host=$target
  if [[ $target == *@* ]]; then
    user=${target%%@*}
    host=${target#*@}
    [[ $user =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]] || die 'Invalid SSH username.'
  fi
  if [[ $host == \[*\] ]]; then
    host=${host#\[}
    host=${host%\]}
    [[ $host == *:* ]] || die 'Brackets are only supported for IPv6 addresses.'
  fi
  if [[ $host == *:* ]]; then
    # SSH validates the address itself; reject shell syntax and host:port inputs.
    [[ $host == *:*:* && $host =~ ^[a-zA-Z0-9:.%_-]+$ ]] || die 'Invalid IPv6 target; use --port for the SSH port.'
  else
    [[ $host =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]] || die 'TARGET must be a DNS name, IP address or SSH alias, optionally user@target.'
  fi
  ssh_target="${user}@${host}"
}

build_manifest() {
  local candidate name relative parent
  local -a pathspecs=()
  courses=()
  for candidate in "$source_root"/*; do
    [[ -f $candidate/reference/course.json && -d $candidate/labs ]] || continue
    [[ ! -L $candidate && ! -L $candidate/labs && ! -L $candidate/reference ]] || die 'Course directories must not be symlinks.'
    name=${candidate##*/}
    courses+=("$name")
    pathspecs+=(":(literal)$name/")
  done
  [[ ${#courses[@]} -gt 0 ]] || die 'No courses found beside this script (expected reference/course.json and labs/).'
  [[ -f $source_root/index.html && ! -L $source_root/index.html ]] || die 'Missing regular catalog file: index.html'
  git -C "$source_root" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die 'The script must be located in the courses directory of a Git clone.'
  # Check Git before consuming the list; process substitution would hide failures.
  git -C "$source_root" ls-files --cached --others --exclude-standard -z -- \
    "${pathspecs[@]}" ':(literal)index.html' > "$work_dir/git-files" || die 'Unable to enumerate course files with Git.'
  : > "$work_dir/files"
  while IFS= read -r -d '' relative; do
    [[ -e $source_root/$relative || -L $source_root/$relative ]] || continue
    [[ -f $source_root/$relative || -L $source_root/$relative ]] || die "Unsupported source type: $relative"
    parent=$relative
    while [[ $parent == */* ]]; do
      parent=${parent%/*}
      [[ ! -L $source_root/$parent ]] || die "Source directory is a symlink: $parent"
    done
    printf '%s\0' "$relative" >> "$work_dir/files"
  done < "$work_dir/git-files"
  [[ -s $work_dir/files ]] || die 'No course files selected for transfer.'
}

main() {
  init_output_style
  local dry_run=0 destination=courses port='' identity='' target='' options=1
  local source_root ssh_target remote_code remote_command status
  local -a courses=() ssh_args=(-T -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
  local -a rsync_args=(-rlptz --safe-links --from0 --itemize-changes --stats)

  while [[ $# -gt 0 ]]; do
    if [[ $options == 1 ]]; then
      case $1 in
        -h|--help) show_usage; return 0 ;;
        --dry-run) dry_run=1; shift; continue ;;
        --dest|--port|--identity)
          [[ $# -ge 2 && -n $2 && $2 != --* ]] || die "Missing value for $1"
          case $1 in
            --dest) destination=$2 ;;
            --port) port=$2 ;;
            --identity) identity=$2 ;;
          esac
          shift 2; continue ;;
        --) options=0; shift; continue ;;
        -*) die "Unknown option: $1 (see --help)" ;;
      esac
    fi
    [[ -z $target ]] || die 'Expected exactly one SSH target.'
    target=$1
    shift
  done
  [[ -n $target ]] || die 'Missing SSH target (see --help).'
  [[ $destination =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || die '--dest must be a single folder name beginning with a letter or digit.'
  parse_target "$target"
  if [[ -n $port ]]; then
    [[ $port =~ ^[0-9]{1,5}$ ]] || die '--port must be an integer from 1 through 65535.'
    port=$((10#$port))
    [[ $port -ge 1 && $port -le 65535 ]] || die '--port must be an integer from 1 through 65535.'
    ssh_args+=(-p "$port")
  fi
  if [[ -n $identity ]]; then
    [[ -f $identity && -r $identity ]] || die '--identity must name a readable key file.'
    ssh_args+=(-i "$identity")
  fi
  require_cmd git
  require_cmd ssh
  require_cmd rsync
  require_cmd mktemp
  require_cmd find
  source_root=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  work_dir=$(mktemp -d /tmp/course-sync.XXXXXXXX)
  trap cleanup EXIT
  trap 'cancel_sync 130' INT
  trap 'cancel_sync 143' TERM
  trap 'cancel_sync 129' HUP
  build_manifest

  # This guard is read-only and runs again immediately before the receiver.
  # rsync creates a missing destination itself; its dry-run creates nothing.
  # shellcheck disable=SC2016 # Expand HOME and receiver arguments on the remote host.
  remote_code='set -eu
destination=$1
shift
command -v rsync >/dev/null 2>&1 || { printf "%s\n" "ERROR: rsync is required on the login node." >&2; exit 1; }
[ -n "${HOME:-}" ] && cd "$HOME" || { printf "%s\n" "ERROR: Cannot access remote home." >&2; exit 1; }
if [ -L "$destination" ]; then
  printf "%s\n" "ERROR: Remote destination must not be a symlink." >&2; exit 1
elif [ -e "$destination" ]; then
  [ -d "$destination" ] && [ -w "$destination" ] && [ -x "$destination" ] || { printf "%s\n" "ERROR: Remote destination must be a writable directory." >&2; exit 1; }
else
  [ -w . ] || { printf "%s\n" "ERROR: Remote home is not writable." >&2; exit 1; }
fi
if [ "$#" -gt 0 ]; then exec rsync "$@"; fi'
  remote_command="sh -c $(shell_quote "$remote_code") sync-labs $(shell_quote "$destination")"

  # SSH owns the real hostname/IP and remote command. A fixed rsync endpoint
  # keeps its host and command tokenization out of both user-controlled values.
  # Quote each receiver argument for the remote POSIX shell before invoking SSH.
  {
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    printf 'ssh_args=('
    printf ' %q' "${ssh_args[@]}"
    printf ' )\nssh_target=%q\nremote_command=%q\n' "$ssh_target" "$remote_command"
    printf 'printf "%%s\\n" "$$" > %q\n' "$work_dir/ssh.pid"
    declare -f shell_quote
    cat <<'TRANSPORT'
[[ $# -ge 2 && $1 == sync-target && $2 == rsync ]] || exit 2
shift 2
for argument in "$@"; do
  remote_command+=" $(shell_quote "$argument")"
done
exec ssh "${ssh_args[@]}" "$ssh_target" "$remote_command"
TRANSPORT
  } > "$work_dir/ssh"
  chmod 700 "$work_dir/ssh"

  log_note "Checking SSH access and destination: $ssh_target:~/$destination/"
  # shellcheck disable=SC2029 # The command is constructed from POSIX-quoted words.
  if run_remote ssh "${ssh_args[@]}" "$ssh_target" "$remote_command"; then
    :
  else
    status=$?
    log_error 'SSH preflight failed. Check the target, account, key, port and remote rsync.'
    if [[ $target != *@* ]]; then
      log_warn 'The default SSH user is root.'
      log_warn "For another cluster account, use user@${ssh_target#*@}."
    fi
    return "$status"
  fi
  if [[ $dry_run == 1 ]]; then
    rsync_args+=(--dry-run)
    log_note "Previewing ${#courses[@]} courses; the remote destination will not change."
  else
    log_note "Syncing ${#courses[@]} courses; local source wins and remote-only files are kept."
  fi
  if run_remote rsync "${rsync_args[@]}" --files-from="$work_dir/files" \
    -e "$work_dir/ssh" -- \
    "$source_root/" "sync-target:$destination/"; then
    if [[ $dry_run == 1 ]]; then
      log_success 'Preview complete. No destination files changed.'
    else
      log_success "Synced ${#courses[@]} courses to $ssh_target:~/$destination/"
      log_note 'On the login node, open a course and inspect its labs:'
      printf '  cd ~/%s/%s\n  ls labs/\n' "$destination" "$(shell_quote "${courses[0]}")"
    fi
  else
    status=$?
    log_warn 'Sync incomplete. Rerun the command before submitting jobs; remote-only files are kept.'
    return "$status"
  fi
}

main "$@"
