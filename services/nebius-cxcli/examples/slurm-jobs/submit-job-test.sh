#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

part_type="auto"
partition=""
count="1"
run_minutes="30"
wall_minutes="35"
submit_mode="loop"
gpus_per_job="1"
nodes="1"
cpus_per_task="1"
exclusive=0
qos=""
account=""
requeue=0
output_dir="slurm-smoke-logs"
dry_run=0
heartbeat_seconds="30"
watch_jobs=0
watch_once=0
watch_interval_seconds="15"
watch_duration_seconds=""
watch_job_name_pattern="sop-*-job-test*"
watch_job_ids=""
login_ip=""
login_remote_dir="/root/testjobs"

show_usage() {
  cat <<'EOF'
Submit public Soperator Slurm smoke jobs for upgrade-policy testing.

Usage:
  submit-job-test.sh [submit options]
  submit-job-test.sh --login <login-external-ip> [--dry-run]

Options:
  --login <login-external-ip> Copy examples to the Slurm login node and SSH there.
  --part-type auto|cpu|gpu   CPU/GPU job template to submit. Default: auto.
  --partition <name>         Slurm partition. Defaults to Slurm's default partition.
  --count <n>                Number of jobs to submit. Default: 1.
  --run-minutes <n>          In-job heartbeat duration. Default: 30.
  --wall-minutes <n>         Slurm wall time request. Default: 35.
  --submit-mode loop|array   Submit repeated sbatch jobs or one array. Default: loop.
  --gpus-per-job <n>         GPUs per GPU job. Default: 1. Used with the GPU template.
  --nodes <n>                Nodes per job. Default: 1.
  --cpus-per-task <n>        CPUs per task. Default: 1.
  --exclusive                Request exclusive node allocation where policy permits.
  --qos <name>               Pass a Slurm QOS to sbatch.
  --account <name>           Pass a Slurm account to sbatch.
  --requeue                  Submit jobs as requeueable.
  --output-dir <path>        Directory for Slurm stdout files. Default: slurm-smoke-logs.
  --watch-jobs               Watch submitted smoke jobs instead of submitting new jobs.
  --watch-once               Print one watch snapshot and exit. Used with --watch-jobs.
  --watch-interval <seconds> Poll interval for --watch-jobs. Default: 15.
  --watch-duration <seconds> Optional max duration for --watch-jobs. Default: until jobs finish.
  --watch-job-name <pattern> Shell pattern for matching smoke job names. Default: sop-*-job-test*.
  --watch-job-ids <ids>      Comma-separated Slurm job IDs to watch instead of name matching.
  --dry-run                  Print sbatch commands without submitting.
  -h, --help                 Show this help text.

Login mode:
  --login copies this directory to root@<login-external-ip>:/root/testjobs,
  then opens an SSH session in /root/testjobs.

Examples:
  ./submit-job-test.sh
  ./submit-job-test.sh --login <login-external-ip>
  ./submit-job-test.sh --count 10
  ./submit-job-test.sh --partition main --count 10 --gpus-per-job 1
  ./submit-job-test.sh --part-type cpu --partition cpu --count 10
  ./submit-job-test.sh --partition main --count 10 --submit-mode array --dry-run
  ./submit-job-test.sh --watch-jobs
  ./submit-job-test.sh --watch-jobs --watch-job-ids 12345,12346
EOF
}

log_error() {
  printf 'ERROR: %s\n' "$*" >&2
}

color_enabled() {
  if [[ -n "${NO_COLOR:-}" || "${TERM:-}" == "dumb" ]]; then
    return 1
  fi

  [[ -t 1 || "${CLICOLOR_FORCE:-0}" != "0" ]]
}

print_watch_sample_header() {
  local sample="$1"
  local timestamp
  local color_start=""
  local color_end=""

  timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  if color_enabled; then
    color_start=$'\033[1;36m'
    color_end=$'\033[0m'
  fi

  printf '%s[%s] Slurm job watch sample %s%s\n' "$color_start" "$timestamp" "$sample" "$color_end"
}

die() {
  log_error "$*"
  printf '\n' >&2
  show_usage >&2
  exit 2
}

require_value() {
  local option="$1"
  local value="${2:-}"

  if [[ -z "$value" ]]; then
    die "${option} requires a value"
  fi
}

is_positive_int() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

validate_positive_int() {
  local option="$1"
  local value="$2"

  if ! is_positive_int "$value"; then
    die "${option} must be a positive integer"
  fi
}

format_minutes() {
  local minutes="$1"
  local hours=$((minutes / 60))
  local mins=$((minutes % 60))

  printf '%02d:%02d:00' "$hours" "$mins"
}

print_command() {
  local arg
  local quoted_arg
  local -a quoted=()

  for arg in "$@"; do
    printf -v quoted_arg '%q' "$arg"
    quoted+=("$quoted_arg")
  done

  printf '%s\n' "${quoted[*]}"
}

require_command() {
  local command_name="$1"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    die "Required command not found: ${command_name}"
  fi
}

submit_command() {
  if ((dry_run)); then
    print_command "$@"
    return 0
  fi

  "$@"
}

run_or_print_command() {
  if ((dry_run)); then
    print_command "$@"
    return 0
  fi

  "$@"
}

stage_examples_and_login() {
  local login_target="root@${login_ip}"

  if ((!dry_run)); then
    require_command ssh
    require_command scp
  fi

  run_or_print_command ssh "$login_target" "mkdir -p ${login_remote_dir}"
  run_or_print_command scp -r "${SCRIPT_DIR}/." "${login_target}:${login_remote_dir}/"
  run_or_print_command ssh -t "$login_target" "cd ${login_remote_dir} && exec \"\${SHELL:-/bin/bash}\" -i"
}

parse_login_args() {
  while (($# > 0)); do
    case "$1" in
      --login)
        require_value "$1" "${2:-}"
        if [[ "$2" == -* ]]; then
          die "--login requires <login-external-ip>"
        fi
        if [[ -n "$login_ip" ]]; then
          die "--login may only be specified once"
        fi
        login_ip="$2"
        shift 2
        ;;
      --dry-run)
        dry_run=1
        shift
        ;;
      -h | --help)
        show_usage
        exit 0
        ;;
      --*)
        die "Unknown login option: $1"
        ;;
      *)
        die "Unexpected login argument: $1"
        ;;
    esac
  done

  if [[ -z "$login_ip" ]]; then
    die "--login requires <login-external-ip>"
  fi
}

parse_args() {
  while (($# > 0)); do
    case "$1" in
      --part-type)
        require_value "$1" "${2:-}"
        part_type="$2"
        shift 2
        ;;
      --partition)
        require_value "$1" "${2:-}"
        partition="$2"
        shift 2
        ;;
      --count)
        require_value "$1" "${2:-}"
        count="$2"
        shift 2
        ;;
      --run-minutes)
        require_value "$1" "${2:-}"
        run_minutes="$2"
        shift 2
        ;;
      --wall-minutes)
        require_value "$1" "${2:-}"
        wall_minutes="$2"
        shift 2
        ;;
      --submit-mode)
        require_value "$1" "${2:-}"
        submit_mode="$2"
        shift 2
        ;;
      --gpus-per-job)
        require_value "$1" "${2:-}"
        gpus_per_job="$2"
        shift 2
        ;;
      --nodes)
        require_value "$1" "${2:-}"
        nodes="$2"
        shift 2
        ;;
      --cpus-per-task)
        require_value "$1" "${2:-}"
        cpus_per_task="$2"
        shift 2
        ;;
      --exclusive)
        exclusive=1
        shift
        ;;
      --qos)
        require_value "$1" "${2:-}"
        qos="$2"
        shift 2
        ;;
      --account)
        require_value "$1" "${2:-}"
        account="$2"
        shift 2
        ;;
      --requeue)
        requeue=1
        shift
        ;;
      --output-dir)
        require_value "$1" "${2:-}"
        output_dir="$2"
        shift 2
        ;;
      --watch-jobs)
        watch_jobs=1
        shift
        ;;
      --watch-once)
        watch_once=1
        shift
        ;;
      --watch-interval)
        require_value "$1" "${2:-}"
        watch_interval_seconds="$2"
        shift 2
        ;;
      --watch-duration)
        require_value "$1" "${2:-}"
        watch_duration_seconds="$2"
        shift 2
        ;;
      --watch-job-name)
        require_value "$1" "${2:-}"
        watch_job_name_pattern="$2"
        shift 2
        ;;
      --watch-job-ids)
        require_value "$1" "${2:-}"
        watch_job_ids="$2"
        shift 2
        ;;
      --dry-run)
        dry_run=1
        shift
        ;;
      -h | --help)
        show_usage
        exit 0
        ;;
      --)
        shift
        break
        ;;
      --*)
        die "Unknown option: $1"
        ;;
      *)
        die "Unexpected argument: $1"
        ;;
    esac
  done

  if (($# > 0)); then
    die "Unexpected argument: $1"
  fi
}

validate_args() {
  case "$part_type" in
    auto | cpu | gpu) ;;
    *) die "--part-type must be auto, cpu, or gpu" ;;
  esac

  case "$submit_mode" in
    loop | array) ;;
    *) die "--submit-mode must be loop or array" ;;
  esac

  validate_positive_int "--count" "$count"
  validate_positive_int "--run-minutes" "$run_minutes"
  validate_positive_int "--wall-minutes" "$wall_minutes"
  validate_positive_int "--gpus-per-job" "$gpus_per_job"
  validate_positive_int "--nodes" "$nodes"
  validate_positive_int "--cpus-per-task" "$cpus_per_task"
  validate_positive_int "--watch-interval" "$watch_interval_seconds"

  if [[ -n "$watch_duration_seconds" ]]; then
    validate_positive_int "--watch-duration" "$watch_duration_seconds"
  fi
  watch_job_ids="$(normalize_csv "$watch_job_ids")"

  if ((wall_minutes < run_minutes)); then
    die "--wall-minutes must be greater than or equal to --run-minutes"
  fi

  if [[ "$part_type" == "auto" ]]; then
    case "$partition" in
      cpu) part_type="cpu" ;;
      *) part_type="gpu" ;;
    esac
  fi
}

batch_script_path() {
  case "$part_type" in
    cpu) printf '%s\n' "${SCRIPT_DIR}/cpu-job-test.sbatch" ;;
    gpu) printf '%s\n' "${SCRIPT_DIR}/gpu-job-test.sbatch" ;;
  esac
}

build_sbatch_command() {
  local job_name="$1"
  local array_spec="$2"
  local batch_script="$3"
  local run_seconds=$((run_minutes * 60))
  local output_pattern="${output_dir}/%x-%j.out"
  local wall_time
  wall_time="$(format_minutes "$wall_minutes")"

  if [[ -n "$array_spec" ]]; then
    output_pattern="${output_dir}/%x-%A_%a.out"
  fi

  local -a cmd=(
    sbatch
    --nodes "$nodes"
    --ntasks "1"
    --cpus-per-task "$cpus_per_task"
    --time "$wall_time"
    --job-name "$job_name"
    --output "$output_pattern"
    --export "ALL,RUN_SECONDS=${run_seconds},HEARTBEAT_SECONDS=${heartbeat_seconds}"
  )

  if [[ -n "$partition" ]]; then
    cmd+=(--partition "$partition")
  fi

  if [[ -n "$array_spec" ]]; then
    cmd+=("--array=${array_spec}")
  fi

  if [[ "$part_type" == "gpu" ]]; then
    cmd+=("--gres=gpu:${gpus_per_job}")
  fi

  if ((exclusive)); then
    cmd+=(--exclusive)
  fi

  if [[ -n "$qos" ]]; then
    cmd+=(--qos "$qos")
  fi

  if [[ -n "$account" ]]; then
    cmd+=(--account "$account")
  fi

  if ((requeue)); then
    cmd+=(--requeue)
  fi

  cmd+=("$batch_script")
  submit_command "${cmd[@]}"
}

submit_loop_jobs() {
  local batch_script="$1"
  local width=${#count}
  local i
  local index_label
  local job_name

  if ((width < 2)); then
    width=2
  fi

  for ((i = 1; i <= count; i += 1)); do
    printf -v index_label "%0${width}d" "$i"
    job_name="sop-${part_type}-job-test-${index_label}"
    build_sbatch_command "$job_name" "" "$batch_script"
  done
}

submit_array_job() {
  local batch_script="$1"
  local array_max=$((count - 1))
  local job_name="sop-${part_type}-job-test"

  build_sbatch_command "$job_name" "0-${array_max}" "$batch_script"
}

csv_contains() {
  local csv="$1"
  local needle="$2"
  local item
  local -a items=()

  IFS=',' read -r -a items <<<"$csv"
  for item in "${items[@]}"; do
    item="${item//[[:space:]]/}"
    if [[ "$item" == "$needle" || "$item" == "${needle}_"* || "$needle" == "${item}_"* ]]; then
      return 0
    fi
  done
  return 1
}

normalize_csv() {
  local csv="$1"
  local item
  local -a items=()
  local out=""

  IFS=',' read -r -a items <<<"$csv"
  for item in "${items[@]}"; do
    item="${item//[[:space:]]/}"
    if [[ -z "$item" ]]; then
      continue
    fi
    if [[ -n "$out" ]]; then
      out+=","
    fi
    out+="$item"
  done
  printf '%s\n' "$out"
}

job_matches_watch_filter() {
  local job_id="$1"
  local job_name="$2"

  if [[ -n "$watch_job_ids" ]]; then
    csv_contains "$watch_job_ids" "$job_id"
    return
  fi

  # shellcheck disable=SC2053 # --watch-job-name is an intentional shell pattern.
  [[ "$job_name" == $watch_job_name_pattern ]]
}

append_seen_job_id() {
  local job_id="$1"

  if [[ -z "$job_id" ]]; then
    return 0
  fi
  case ",${seen_job_ids}," in
    *",${job_id},"*) ;;
    *)
      if [[ -n "$seen_job_ids" ]]; then
        seen_job_ids+=","
      fi
      seen_job_ids+="$job_id"
      ;;
  esac
}

csv_remove() {
  local csv="$1"
  local needle="$2"
  local item
  local -a items=()
  local out=""

  IFS=',' read -r -a items <<<"$csv"
  for item in "${items[@]}"; do
    item="${item//[[:space:]]/}"
    if [[ -z "$item" || "$item" == "$needle" ]]; then
      continue
    fi
    if [[ -n "$out" ]]; then
      out+=","
    fi
    out+="$item"
  done
  printf '%s\n' "$out"
}

count_csv_items() {
  local csv="$1"
  local item
  local -a items=()
  local count=0

  if [[ -z "$csv" ]]; then
    printf '0\n'
    return 0
  fi

  IFS=',' read -r -a items <<<"$csv"
  for item in "${items[@]}"; do
    item="${item//[[:space:]]/}"
    if [[ -n "$item" ]]; then
      count=$((count + 1))
    fi
  done
  printf '%s\n' "$count"
}

state_is_interrupted() {
  case "$1" in
    BOOT_FAIL* | CANCELLED* | DEADLINE* | FAILED* | NODE_FAIL* | OUT_OF_MEMORY* | PREEMPTED* | REVOKED* | SPECIAL_EXIT* | TIMEOUT*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

state_is_completed() {
  case "$1" in
    COMPLETED*) return 0 ;;
    *) return 1 ;;
  esac
}

observed_jobs_completed_in_sacct() {
  local remaining_ids="$seen_job_ids"
  local job_id
  local state
  local rest

  if [[ -z "$remaining_ids" ]] || ! command -v sacct >/dev/null 2>&1; then
    return 1
  fi

  while IFS='|' read -r job_id state rest; do
    if [[ -z "$job_id" ]] || ! csv_contains "$seen_job_ids" "$job_id"; then
      continue
    fi
    if state_is_interrupted "$state"; then
      return 2
    fi
    if state_is_completed "$state"; then
      remaining_ids="$(csv_remove "$remaining_ids" "$job_id")"
    fi
  done < <(
    sacct -X -n -P -j "$seen_job_ids" --format=JobIDRaw,State 2>/dev/null || true
  )

  [[ -z "$remaining_ids" ]]
}

sacct_state_for_job() {
  local lookup_job_id="$1"
  local job_id
  local state
  local rest

  if ! command -v sacct >/dev/null 2>&1; then
    return 1
  fi

  while IFS='|' read -r job_id state rest; do
    if [[ "$job_id" == "$lookup_job_id" ]]; then
      printf '%s\n' "$state"
      return 0
    fi
  done < <(
    sacct -X -n -P -j "$lookup_job_id" --format=JobIDRaw,State 2>/dev/null || true
  )

  return 1
}

verify_explicit_missing_jobs() {
  local visible_ids="$1"
  local job_id
  local state
  local -a expected_ids=()

  if [[ -z "$watch_job_ids" ]]; then
    return 0
  fi

  IFS=',' read -r -a expected_ids <<<"$watch_job_ids"
  for job_id in "${expected_ids[@]}"; do
    job_id="${job_id//[[:space:]]/}"
    if [[ -z "$job_id" ]] || csv_contains "$visible_ids" "$job_id"; then
      continue
    fi

    state="$(sacct_state_for_job "$job_id" || true)"
    if state_is_completed "$state"; then
      printf 'job_id=%s state=%s source=sacct terminal=completed\n' "$job_id" "$state"
      continue
    fi
    if state_is_interrupted "$state"; then
      log_error "Explicit Slurm job id ${job_id} left squeue with interrupted accounting state: ${state}"
    else
      log_error "Explicit Slurm job id ${job_id} is not visible in squeue and accounting is not complete."
    fi
    watch_failures=$((watch_failures + 1))
  done
}

print_watch_sacct_evidence() {
  local job_id
  local state
  local exit_code
  local elapsed
  local start_time
  local end_time

  if [[ -z "$seen_job_ids" ]] || ! command -v sacct >/dev/null 2>&1; then
    return 0
  fi

  printf 'Accounting evidence from sacct for observed jobs:\n'
  while IFS='|' read -r job_id state exit_code elapsed start_time end_time; do
    if [[ -z "$job_id" ]]; then
      continue
    fi
    printf 'job_id=%s state=%s exit_code=%s elapsed=%s start=%s end=%s\n' \
      "$job_id" "$state" "$exit_code" "$elapsed" "$start_time" "$end_time"
    if state_is_interrupted "$state"; then
      watch_failures=$((watch_failures + 1))
    fi
  done < <(
    sacct -X -n -P -j "$seen_job_ids" \
      --format=JobIDRaw,State,ExitCode,Elapsed,Start,End 2>/dev/null || true
  )
}

watch_slurm_jobs() {
  local started_at
  local deadline
  local sample=0
  local matching_count
  local squeue_output
  local now
  local job_id
  local state
  local elapsed
  local remaining
  local partition_name
  local nodes
  local job_name
  local visible_job_ids
  local sleep_seconds
  local remaining_seconds
  local duration_label

  if ((dry_run)); then
    print_command squeue -h -o "%i|%T|%M|%L|%P|%N|%j"
    if [[ -n "$watch_job_ids" ]]; then
      print_command sacct -X -n -P -j "$watch_job_ids" \
        --format=JobIDRaw,State,ExitCode,Elapsed,Start,End
    fi
    return 0
  fi

  require_command squeue
  started_at="$(date +%s)"
  if [[ -n "$watch_duration_seconds" ]]; then
    deadline=$((started_at + watch_duration_seconds))
    duration_label="${watch_duration_seconds}s"
  else
    deadline=0
    duration_label="until-clear"
  fi
  seen_job_ids="$(normalize_csv "$watch_job_ids")"
  watch_failures=0

  printf 'Slurm job watch started: duration=%s interval=%ss name_pattern=%s job_ids=%s\n' \
    "$duration_label" "$watch_interval_seconds" "$watch_job_name_pattern" \
    "${watch_job_ids:-name-filter}"

  while true; do
    sample=$((sample + 1))
    now="$(date +%s)"
    matching_count=0
    print_watch_sample_header "$sample"
    if ! squeue_output="$(squeue -h -o "%i|%T|%M|%L|%P|%N|%j")"; then
      log_error "squeue failed while watching Slurm jobs"
      watch_failures=$((watch_failures + 1))
      break
    fi
    visible_job_ids=""
    while IFS='|' read -r job_id state elapsed remaining partition_name nodes job_name; do
      if [[ -z "$job_id" ]]; then
        continue
      fi
      if ! job_matches_watch_filter "$job_id" "$job_name"; then
        continue
      fi
      matching_count=$((matching_count + 1))
      append_seen_job_id "$job_id"
      if [[ -n "$visible_job_ids" ]]; then
        visible_job_ids+=","
      fi
      visible_job_ids+="$job_id"
      printf 'job_id=%s state=%s elapsed=%s remaining=%s partition=%s nodes=%s name=%s\n' \
        "$job_id" "$state" "$elapsed" "$remaining" "$partition_name" "$nodes" "$job_name"
      if state_is_interrupted "$state"; then
        watch_failures=$((watch_failures + 1))
      fi
    done <<<"$squeue_output"
    verify_explicit_missing_jobs "$visible_job_ids"
    if ((matching_count == 0)); then
      if observed_jobs_completed_in_sacct; then
        printf 'All observed Slurm smoke jobs left squeue with COMPLETED accounting state.\n'
        break
      fi
      log_error "No matching Slurm smoke jobs are visible in squeue and accounting is not complete."
      watch_failures=$((watch_failures + 1))
    fi
    if ((watch_once)); then
      break
    fi
    now="$(date +%s)"
    sleep_seconds="$watch_interval_seconds"
    if [[ -n "$watch_duration_seconds" ]]; then
      if ((now >= deadline)); then
        break
      fi
      remaining_seconds=$((deadline - now))
      if ((remaining_seconds < sleep_seconds)); then
        sleep_seconds="$remaining_seconds"
      fi
    fi
    sleep "$sleep_seconds"
  done

  print_watch_sacct_evidence
  if ((watch_failures > 0)); then
    log_error "Slurm job watch result: FAIL - interruption or visibility gap detected."
    return 1
  fi
  printf 'Slurm job watch result: PASS - observed %s job id(s) across %s sample(s) with no interruption signal.\n' \
    "$(count_csv_items "$seen_job_ids")" "$sample"
}

main() {
  local arg

  for arg in "$@"; do
    if [[ "$arg" == "--login" ]]; then
      parse_login_args "$@"
      stage_examples_and_login
      return 0
    fi
  done

  parse_args "$@"
  validate_args

  if ((watch_jobs)); then
    watch_slurm_jobs
    return $?
  fi

  local batch_script
  batch_script="$(batch_script_path)"

  if [[ ! -f "$batch_script" ]]; then
    die "Batch script not found: ${batch_script}"
  fi

  if ((!dry_run)); then
    require_command sbatch
    mkdir -p -- "$output_dir"
  fi

  case "$submit_mode" in
    loop) submit_loop_jobs "$batch_script" ;;
    array) submit_array_job "$batch_script" ;;
  esac
}

main "$@"
