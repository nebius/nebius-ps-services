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
login_ip=""
login_remote_dir="/root/testjobs"

show_usage() {
  cat <<'EOF'
Submit public Soperator Slurm smoke jobs for upgrade-policy testing.

Usage:
  submit-job-test.sh [submit options]
  submit-job-test.sh login <login-external-ip> [--dry-run]

Options:
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
  --dry-run                  Print sbatch commands without submitting.
  -h, --help                 Show this help text.

Login mode:
  Copies this directory to root@<login-external-ip>:/root/testjobs, then opens
  an SSH session in /root/testjobs. Copying is part of login mode.

Examples:
  ./submit-job-test.sh
  ./submit-job-test.sh login <login-external-ip>
  ./submit-job-test.sh --count 10
  ./submit-job-test.sh --partition main --count 10 --gpus-per-job 1
  ./submit-job-test.sh --part-type cpu --partition cpu --count 10
  ./submit-job-test.sh --partition main --count 10 --submit-mode array --dry-run
EOF
}

log_error() {
  printf 'ERROR: %s\n' "$*" >&2
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
        if [[ -n "$login_ip" ]]; then
          die "Unexpected login argument: $1"
        fi
        login_ip="$1"
        shift
        ;;
    esac
  done

  if [[ -z "$login_ip" ]]; then
    die "login requires <login-external-ip>"
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

main() {
  if [[ "${1:-}" == "login" ]]; then
    shift
    parse_login_args "$@"
    stage_examples_and_login
    return 0
  fi

  parse_args "$@"
  validate_args

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
