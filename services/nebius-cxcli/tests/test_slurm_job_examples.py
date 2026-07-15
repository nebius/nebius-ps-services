from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = REPO_ROOT / "examples" / "slurm-jobs"
SUBMITTER = EXAMPLE_DIR / "submit-job-test.sh"
CPU_BATCH = EXAMPLE_DIR / "cpu-job-test.sbatch"
GPU_BATCH = EXAMPLE_DIR / "gpu-job-test.sbatch"


PUBLIC_FLAGS = (
    "--part-type auto|cpu|gpu",
    "--partition <name>",
    "--count <n>",
    "--run-minutes <n>",
    "--heartbeat-seconds <n>",
    "--wall-minutes <n>",
    "--submit-mode loop|array",
    "--gpus-per-job <n>",
    "--nodes <n>",
    "--cpus-per-task <n>",
    "--exclusive",
    "--qos <name>",
    "--account <name>",
    "--requeue",
    "--output-dir <path>",
    "--login <login-external-ip>",
    "--login-shell",
    "--login-remote-dir <path>",
    "--watch-jobs",
    "--watch-once",
    "--watch-interval <seconds>",
    "--watch-duration <seconds>",
    "--watch-job-name <pattern>",
    "--watch-job-ids <ids>",
    "--dry-run",
    "-h, --help",
)


def run_submitter(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    return subprocess.run(
        ["bash", str(SUBMITTER), *args],
        check=False,
        cwd=EXAMPLE_DIR,
        env=env,
        text=True,
        capture_output=True,
    )


def sbatch_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("sbatch ")]


def write_fake_scontrol(bin_dir: Path, *, nodes: str = "worker-0") -> None:
    scontrol = bin_dir / "scontrol"
    scontrol.write_text(
        "#!/usr/bin/env bash\n"
        'job_id="${3:-}"\n'
        "printf '%s\\n' \"JobId=${job_id} JobName=sop-gpu-job-test-01 "
        "UserId=root(0) JobState=RUNNING Partition=main "
        f"NodeList={nodes} "
        "Priority=100 Reason=None Requeue=1 Restarts=0 TimeLimit=00:35:00 "
        'SubmitTime=2026-07-04T05:09:00 StartTime=2026-07-04T05:09:24"\n',
        encoding="utf-8",
    )
    scontrol.chmod(0o755)


def test_slurm_example_scripts_parse_as_bash() -> None:
    for script in (SUBMITTER, CPU_BATCH, GPU_BATCH):
        result = subprocess.run(
            ["bash", "-n", str(script)],
            check=False,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr


def test_submitter_help_documents_public_flags() -> None:
    result = run_submitter("--help")

    assert result.returncode == 0
    assert "submit-job-test.sh --login <login-external-ip>" in result.stdout
    assert "--login stages this directory" in result.stdout
    assert "unique private path" in result.stdout
    assert "long-lived interactive SSH session" in result.stdout
    assert "Default: auto" in result.stdout
    assert "Default: until jobs finish" in result.stdout
    assert "default: --no-requeue" in result.stdout
    assert "Examples:\n  ./submit-job-test.sh\n" in result.stdout
    for flag in PUBLIC_FLAGS:
        assert flag in result.stdout


def test_example_readme_documents_private_login_node_execution_flow() -> None:
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")

    assert "./examples/slurm-jobs/submit-job-test.sh --login <login-external-ip>" in readme
    assert "/root/testjobs-<UTC timestamp>-<process ID>" in readme
    assert "mode `0700`" in readme
    assert "--login-remote-dir /root/my-private-testjobs" in readme
    assert "--login-shell" in readme
    assert "arbitrary SSH options are not accepted" in readme
    assert "--heartbeat-seconds 2" in readme
    assert "explicitly passes `sbatch --no-requeue` by default" in readme
    assert "disposable action probes" in readme
    assert "`sbatch --requeue`" in readme
    assert "./submit-job-test.sh --watch-jobs" in readme
    assert "timestamped proof stream" in readme
    assert "scp -r examples/slurm-jobs" not in readme
    assert "cd /shared/slurm-jobs" not in readme


def test_example_readme_starts_submit_examples_with_bare_command() -> None:
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
    gpu_section = readme.split("## Submit GPU Jobs", 1)[1]
    first_example = gpu_section.split("```bash\n", 1)[1].split("\n```", 1)[0]

    assert first_example == "./submit-job-test.sh"


def test_login_dry_run_stages_privately_and_runs_remote_command() -> None:
    result = run_submitter(
        "--login",
        "203.0.113.10",
        "--login-remote-dir",
        "/root/private-job-test",
        "--count",
        "2",
        "--heartbeat-seconds",
        "2",
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 3
    stage_command = shlex.split(lines[0])
    assert stage_command == [
        "ssh",
        "root@203.0.113.10",
        (
            "umask 077; test ! -e /root/private-job-test "
            "&& install -d -m 0700 -- /root/private-job-test"
        ),
    ]
    assert lines[1].startswith("scp -r ")
    assert f"{EXAMPLE_DIR}/." in lines[1]
    assert "root@203.0.113.10:/root/private-job-test/" in lines[1]
    remote_command = shlex.split(lines[2])
    assert remote_command[:2] == ["ssh", "root@203.0.113.10"]
    assert "cd /root/private-job-test && ./submit-job-test.sh" in remote_command[2]
    assert "--count 2" in remote_command[2]
    assert "--heartbeat-seconds 2" in remote_command[2]
    assert "--requeue" not in remote_command[2]
    assert "--dry-run" not in remote_command[2]


def test_login_dry_run_forwards_explicit_requeue_opt_in() -> None:
    result = run_submitter(
        "--login",
        "login.example.test",
        "--login-remote-dir",
        "/root/private-requeue-test",
        "--requeue",
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    remote_command = shlex.split(result.stdout.splitlines()[2])
    assert "--requeue" in remote_command[2]


def test_login_dry_run_uses_one_unique_default_staging_path() -> None:
    result = run_submitter("--login", "login.example.test", "--dry-run")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 3
    stage_command = shlex.split(lines[0])
    remote_dir = stage_command[2].rsplit(" ", 1)[-1]
    assert re.fullmatch(r"/root/testjobs-\d{8}T\d{6}Z-\d+", remote_dir)
    assert f"root@login.example.test:{remote_dir}/" in lines[1]
    assert f"cd {remote_dir} && ./submit-job-test.sh" in shlex.split(lines[2])[2]


def test_login_shell_stages_without_submitting_jobs() -> None:
    result = run_submitter(
        "--login",
        "203.0.113.10",
        "--login-remote-dir",
        "/root/private-job-test",
        "--login-shell",
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 3
    shell_command = shlex.split(lines[2])
    assert shell_command[:3] == ["ssh", "-t", "root@203.0.113.10"]
    assert shell_command[3] == ('cd /root/private-job-test && exec "${SHELL:-/bin/bash}" -i')
    assert "./submit-job-test.sh" not in shell_command[3]


def test_heartbeat_interval_is_exported_to_the_batch_job() -> None:
    result = run_submitter("--dry-run", "--heartbeat-seconds", "2")

    assert result.returncode == 0, result.stderr
    assert "HEARTBEAT_SECONDS=2" in result.stdout


def test_login_only_options_require_login() -> None:
    shell_result = run_submitter("--login-shell", "--dry-run")
    remote_dir_result = run_submitter("--login-remote-dir", "/root/private-job-test", "--dry-run")

    assert shell_result.returncode != 0
    assert "--login-shell requires --login" in shell_result.stderr
    assert remote_dir_result.returncode != 0
    assert "--login-remote-dir requires --login" in remote_dir_result.stderr


def test_login_rejects_unsafe_remote_paths_and_targets() -> None:
    invalid_invocations = (
        ("--login", "host;touch-pwned", "--dry-run"),
        (
            "--login",
            "203.0.113.10",
            "--login-remote-dir",
            "relative/path",
            "--dry-run",
        ),
        (
            "--login",
            "203.0.113.10",
            "--login-remote-dir",
            "/root/../unsafe",
            "--dry-run",
        ),
        (
            "--login",
            "203.0.113.10",
            "--login-remote-dir",
            "/root/./unsafe",
            "--dry-run",
        ),
        (
            "--login",
            "203.0.113.10",
            "--login-remote-dir",
            "/",
            "--dry-run",
        ),
        (
            "--login",
            "203.0.113.10",
            "--login-remote-dir",
            "/etc/job-test",
            "--dry-run",
        ),
    )

    for invocation in invalid_invocations:
        result = run_submitter(*invocation)
        assert result.returncode != 0


def test_submitter_rejects_unknown_options() -> None:
    result = run_submitter("--unknown-option")

    assert result.returncode != 0
    assert "Unknown option: --unknown-option" in result.stderr


def test_submitter_rejects_old_check_watch_option_names() -> None:
    result = run_submitter("--check-jobs")

    assert result.returncode != 0
    assert "Unknown option: --check-jobs" in result.stderr


def test_submitter_rejects_old_login_subcommand() -> None:
    result = run_submitter("login", "203.0.113.10")

    assert result.returncode != 0
    assert "Unexpected argument: login" in result.stderr


def test_login_requires_explicit_ip_before_other_flags() -> None:
    result = run_submitter("--login", "--dry-run")

    assert result.returncode != 0
    assert "--login requires a value" in result.stderr


def test_submitter_rejects_removed_kind_option() -> None:
    result = run_submitter("--kind", "gpu")

    assert result.returncode != 0
    assert "Unknown option: --kind" in result.stderr


def test_submitter_rejects_redundant_no_requeue_option() -> None:
    result = run_submitter("--no-requeue")

    assert result.returncode != 0
    assert "Unknown option: --no-requeue" in result.stderr


def test_default_dry_run_uses_gpu_template_on_slurm_default_partition() -> None:
    result = run_submitter("--dry-run")

    assert result.returncode == 0, result.stderr
    lines = sbatch_lines(result.stdout)
    assert len(lines) == 1
    assert "--no-requeue" in lines[0]
    assert "--requeue" not in lines[0]
    assert "--partition" not in result.stdout
    assert "--gres=gpu:1" in result.stdout
    assert "sop-gpu-job-test-01" in result.stdout
    assert "gpu-job-test.sbatch" in result.stdout


def test_requeue_explicitly_opts_disposable_probe_job_in() -> None:
    result = run_submitter("--dry-run", "--requeue")

    assert result.returncode == 0, result.stderr
    lines = sbatch_lines(result.stdout)
    assert len(lines) == 1
    assert "--requeue" in lines[0]
    assert "--no-requeue" not in lines[0]


def test_main_partition_dry_run_defaults_to_gpu_template_without_part_type() -> None:
    result = run_submitter("--dry-run", "--partition", "main", "--count", "2")

    assert result.returncode == 0, result.stderr
    lines = sbatch_lines(result.stdout)
    assert len(lines) == 2
    assert result.stdout.count("--partition main") == 2
    assert result.stdout.count("--gres=gpu:1") == 2
    assert "sop-gpu-job-test-01" in result.stdout
    assert "sop-gpu-job-test-02" in result.stdout
    assert "gpu-job-test.sbatch" in result.stdout


def test_cpu_partition_dry_run_uses_cpu_template_without_gpu_gres() -> None:
    result = run_submitter("--dry-run", "--partition", "cpu")

    assert result.returncode == 0, result.stderr
    lines = sbatch_lines(result.stdout)
    assert len(lines) == 1
    assert "--partition cpu" in result.stdout
    assert "--gres" not in result.stdout
    assert "sop-cpu-job-test-01" in result.stdout
    assert "cpu-job-test.sbatch" in result.stdout


def test_cpu_dry_run_prints_one_sbatch_per_loop_job_without_gpu_gres() -> None:
    result = run_submitter("--dry-run", "--partition", "cpu", "--count", "3")

    assert result.returncode == 0, result.stderr
    lines = sbatch_lines(result.stdout)
    assert len(lines) == 3
    assert "--gres" not in result.stdout
    assert "sop-cpu-job-test-01" in result.stdout
    assert "sop-cpu-job-test-02" in result.stdout
    assert "sop-cpu-job-test-03" in result.stdout


def test_gpu_dry_run_prints_one_sbatch_per_loop_job_with_gpu_gres() -> None:
    result = run_submitter(
        "--dry-run",
        "--part-type",
        "gpu",
        "--partition",
        "gpu",
        "--count",
        "3",
        "--gpus-per-job",
        "1",
    )

    assert result.returncode == 0, result.stderr
    lines = sbatch_lines(result.stdout)
    assert len(lines) == 3
    assert result.stdout.count("--gres=gpu:1") == 3
    assert "sop-gpu-job-test-01" in result.stdout
    assert "sop-gpu-job-test-02" in result.stdout
    assert "sop-gpu-job-test-03" in result.stdout
    assert "gpu-job-test.sbatch" in result.stdout


def test_array_mode_dry_run_prints_one_sbatch_command() -> None:
    result = run_submitter("--dry-run", "--count", "3", "--submit-mode", "array")

    assert result.returncode == 0, result.stderr
    lines = sbatch_lines(result.stdout)
    assert len(lines) == 1
    assert "--array=0-2" in result.stdout


def test_watch_jobs_dry_run_prints_watch_command_without_submitting() -> None:
    result = run_submitter(
        "--watch-jobs",
        "--watch-job-ids",
        "12345,12346",
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    assert sbatch_lines(result.stdout) == []
    assert "squeue -h -o" in result.stdout
    assert "sacct -X -n -P -j 12345\\,12346" in result.stdout


def test_watch_jobs_default_runs_until_observed_jobs_clear(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    squeue = bin_dir / "squeue"
    sacct = bin_dir / "sacct"
    sleep = bin_dir / "sleep"
    squeue_count = bin_dir / "squeue.count"
    write_fake_scontrol(bin_dir)
    squeue.write_text(
        "#!/usr/bin/env bash\n"
        f"count_file={shlex.quote(str(squeue_count))}\n"
        'count="$(cat "$count_file" 2>/dev/null || printf \'0\')"\n'
        "count=$((count + 1))\n"
        'printf "%s\\n" "$count" >"$count_file"\n'
        "if ((count == 1)); then\n"
        "  printf '%s\\n' '60|RUNNING|00:10|20:00|main|worker-0|sop-gpu-job-test-01'\n"
        "fi\n",
        encoding="utf-8",
    )
    sacct.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *Elapsed* ]]; then\n'
        "  printf '%s\\n' '60|COMPLETED|0:0|00:30:04|2026-07-04T05:09:00|2026-07-04T05:09:24|2026-07-04T05:39:28|worker-0|0'\n"
        "else\n"
        "  printf '%s\\n' '60|COMPLETED|0:0|2026-07-04T05:09:00|2026-07-04T05:09:24|worker-0|0'\n"
        "fi\n",
        encoding="utf-8",
    )
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    squeue.chmod(0o755)
    sacct.chmod(0o755)
    sleep.chmod(0o755)
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        ["bash", str(SUBMITTER), "--watch-jobs", "--watch-job-ids", "60"],
        check=False,
        cwd=EXAMPLE_DIR,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Slurm job watch started: duration=until-clear interval=15s" in result.stdout
    assert "duration=1800s" not in result.stdout
    assert "Slurm job watch sample 2" in result.stdout
    assert (
        "All observed Slurm smoke jobs left squeue with COMPLETED accounting state."
        in result.stdout
    )
    assert "Slurm job watch result: PASS - observed 1 job id(s)" in result.stdout


def test_watch_jobs_accepts_transient_accounting_visibility_gap(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    squeue = bin_dir / "squeue"
    sacct = bin_dir / "sacct"
    sleep = bin_dir / "sleep"
    squeue_count = bin_dir / "squeue.count"
    sacct_count = bin_dir / "sacct.count"
    write_fake_scontrol(bin_dir)
    squeue.write_text(
        "#!/usr/bin/env bash\n"
        f"count_file={shlex.quote(str(squeue_count))}\n"
        'count="$(cat "$count_file" 2>/dev/null || printf \'0\')"\n'
        "count=$((count + 1))\n"
        'printf "%s\\n" "$count" >"$count_file"\n'
        "if ((count == 1)); then\n"
        "  printf '%s\\n' '60|RUNNING|00:10|20:00|main|worker-0|sop-gpu-job-test-01'\n"
        "fi\n",
        encoding="utf-8",
    )
    sacct.write_text(
        "#!/usr/bin/env bash\n"
        f"count_file={shlex.quote(str(sacct_count))}\n"
        'count="$(cat "$count_file" 2>/dev/null || printf \'0\')"\n'
        "count=$((count + 1))\n"
        'printf "%s\\n" "$count" >"$count_file"\n'
        "if ((count >= 3)); then\n"
        '  if [[ "$*" == *Elapsed* ]]; then\n'
        "    printf '%s\\n' '60|COMPLETED|0:0|00:30:04|2026-07-04T05:09:00|2026-07-04T05:09:24|2026-07-04T05:39:28|worker-0|0'\n"
        "  else\n"
        "    printf '%s\\n' '60|COMPLETED|0:0|2026-07-04T05:09:00|2026-07-04T05:09:24|worker-0|0'\n"
        "  fi\n"
        "fi\n",
        encoding="utf-8",
    )
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    squeue.chmod(0o755)
    sacct.chmod(0o755)
    sleep.chmod(0o755)
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        ["bash", str(SUBMITTER), "--watch-jobs", "--watch-job-ids", "60"],
        check=False,
        cwd=EXAMPLE_DIR,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "temporarily not visible in squeue and accounting is not terminal" in result.stderr
    assert "Slurm accounting visibility recovered after 1 transient gap sample(s)" in result.stdout
    assert (
        "All observed Slurm smoke jobs left squeue with COMPLETED accounting state" in result.stdout
    )
    assert "Slurm job watch result: PASS - observed 1 job id(s)" in result.stdout
    assert "Slurm job watch result: FAIL" not in result.stderr


def test_watch_jobs_tolerates_transient_controller_rpc_gap_after_baseline(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    squeue = bin_dir / "squeue"
    sacct = bin_dir / "sacct"
    sleep = bin_dir / "sleep"
    squeue_count = bin_dir / "squeue.count"
    write_fake_scontrol(bin_dir)
    squeue.write_text(
        "#!/usr/bin/env bash\n"
        f"count_file={shlex.quote(str(squeue_count))}\n"
        'count="$(cat "$count_file" 2>/dev/null || printf \'0\')"\n'
        "count=$((count + 1))\n"
        'printf "%s\\n" "$count" >"$count_file"\n'
        "if ((count == 1)); then\n"
        "  printf '%s\\n' '60|RUNNING|00:10|20:00|main|worker-0|sop-gpu-job-test-01'\n"
        "elif ((count == 2)); then\n"
        "  exit 1\n"
        "fi\n",
        encoding="utf-8",
    )
    sacct.write_text(
        "#!/usr/bin/env bash\n"
        f"count_file={shlex.quote(str(squeue_count))}\n"
        'count="$(cat "$count_file" 2>/dev/null || printf \'0\')"\n'
        "if ((count < 3)); then\n"
        "  printf '%s\\n' '60|RUNNING|0:0|2026-07-04T05:09:00|2026-07-04T05:09:24|worker-0|0'\n"
        'elif [[ "$*" == *Elapsed* ]]; then\n'
        "  printf '%s\\n' '60|COMPLETED|0:0|00:30:04|2026-07-04T05:09:00|2026-07-04T05:09:24|2026-07-04T05:39:28|worker-0|0'\n"
        "else\n"
        "  printf '%s\\n' '60|COMPLETED|0:0|2026-07-04T05:09:00|2026-07-04T05:09:24|worker-0|0'\n"
        "fi\n",
        encoding="utf-8",
    )
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for command in (squeue, sacct, sleep):
        command.chmod(0o755)
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        ["bash", str(SUBMITTER), "--watch-jobs", "--watch-job-ids", "60"],
        check=False,
        cwd=EXAMPLE_DIR,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "squeue is temporarily unavailable" in result.stderr
    assert "lineage_baseline" in result.stdout
    assert "Slurm job watch result: PASS" in result.stdout


def test_watch_jobs_accepts_unallocated_pending_job_identity(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    squeue = bin_dir / "squeue"
    scontrol = bin_dir / "scontrol"
    sacct = bin_dir / "sacct"
    squeue.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '60|PENDING|0:00|20:00|main||(Priority)|sop-gpu-job-test-01'\n",
        encoding="utf-8",
    )
    scontrol.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' 'JobId=60 JobName=sop-gpu-job-test-01 UserId=root(0) "
        "JobState=PENDING Partition=main NodeList=(null) Priority=100 Reason=Priority "
        "Requeue=1 Restarts=0 TimeLimit=00:20:00 SubmitTime=2026-07-14T12:59:30 "
        "StartTime=Unknown'\n",
        encoding="utf-8",
    )
    sacct.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *Elapsed* ]]; then\n'
        "  printf '%s\\n' '60|PENDING|0:0|00:00:00|2026-07-14T12:59:30|Unknown|Unknown|Unknown|0'\n"
        "else\n"
        "  printf '%s\\n' '60|PENDING|0:0|2026-07-14T12:59:30|Unknown|Unknown|0'\n"
        "fi\n",
        encoding="utf-8",
    )
    for command in (squeue, scontrol, sacct):
        command.chmod(0o755)
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "bash",
            str(SUBMITTER),
            "--watch-jobs",
            "--watch-once",
            "--watch-job-ids",
            "60",
        ],
        check=False,
        cwd=EXAMPLE_DIR,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "job_id=60 state=PENDING" in result.stdout
    assert (
        "job_id=60 lineage_pending submit=2026-07-14T12:59:30 allocation=unassigned restarts=0"
    ) in result.stdout
    assert "lacks a complete JobID/submit/start/allocation/Restarts" not in result.stderr
    assert "Slurm job watch result: PASS" in result.stdout


def test_watch_jobs_fails_when_visible_job_allocation_changes(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    squeue = bin_dir / "squeue"
    scontrol = bin_dir / "scontrol"
    sleep = bin_dir / "sleep"
    scontrol_count = bin_dir / "scontrol.count"
    squeue.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '60|RUNNING|00:10|20:00|main|worker-0|sop-gpu-job-test-01'\n",
        encoding="utf-8",
    )
    scontrol.write_text(
        "#!/usr/bin/env bash\n"
        f"count_file={shlex.quote(str(scontrol_count))}\n"
        'count="$(cat "$count_file" 2>/dev/null || printf \'0\')"\n'
        "count=$((count + 1))\n"
        'printf "%s\\n" "$count" >"$count_file"\n'
        'nodes="worker-0"\n'
        "if ((count > 1)); then nodes=worker-1; fi\n"
        "printf '%s\\n' \"JobId=60 JobName=sop-gpu-job-test-01 UserId=root(0) "
        "JobState=RUNNING Partition=main NodeList=${nodes} Priority=100 Reason=None "
        "Requeue=1 Restarts=0 TimeLimit=00:35:00 SubmitTime=2026-07-04T05:09:00 "
        'StartTime=2026-07-04T05:09:24"\n',
        encoding="utf-8",
    )
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for command in (squeue, scontrol, sleep):
        command.chmod(0o755)
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        ["bash", str(SUBMITTER), "--watch-jobs", "--watch-job-ids", "60"],
        check=False,
        cwd=EXAMPLE_DIR,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "changed submit/start/allocation/Restarts lineage" in result.stderr
    assert "Slurm job watch result: FAIL" in result.stderr


def test_watch_jobs_rejects_completed_explicit_job_without_preupgrade_baseline(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    squeue = bin_dir / "squeue"
    sacct = bin_dir / "sacct"
    write_fake_scontrol(bin_dir)
    squeue.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '60|RUNNING|00:10|20:00|main|worker-0|sop-gpu-job-test-01'\n",
        encoding="utf-8",
    )
    sacct.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '59|COMPLETED|0:0|2026-07-04T05:09:00|2026-07-04T05:09:24|worker-0|0'\n"
        "printf '%s\\n' '60|RUNNING|0:0|2026-07-04T05:09:00|2026-07-04T05:09:24|worker-0|0'\n",
        encoding="utf-8",
    )
    squeue.chmod(0o755)
    sacct.chmod(0o755)
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "bash",
            str(SUBMITTER),
            "--watch-jobs",
            "--watch-once",
            "--watch-job-ids",
            "59,60",
        ],
        check=False,
        cwd=EXAMPLE_DIR,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "\x1b[" not in result.stdout
    assert "job_id=60 state=RUNNING" in result.stdout
    assert "completed before the watcher captured a pre-upgrade lineage baseline" in result.stderr
    assert "Slurm job watch result: FAIL" in result.stderr


def test_watch_sample_header_uses_color_when_color_is_forced(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    squeue = bin_dir / "squeue"
    write_fake_scontrol(bin_dir)
    squeue.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '60|RUNNING|00:10|20:00|main|worker-0|sop-gpu-job-test-01'\n",
        encoding="utf-8",
    )
    squeue.chmod(0o755)
    env = os.environ.copy()
    env.pop("NO_COLOR", None)
    env["CLICOLOR_FORCE"] = "1"
    env["TERM"] = "xterm-256color"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "bash",
            str(SUBMITTER),
            "--watch-jobs",
            "--watch-once",
            "--watch-job-ids",
            "60",
        ],
        check=False,
        cwd=EXAMPLE_DIR,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "\x1b[1;36m[" in result.stdout
    assert "Slurm job watch sample 1\x1b[0m" in result.stdout


def test_exclusive_is_only_added_when_requested() -> None:
    default_result = run_submitter("--dry-run", "--partition", "cpu")
    exclusive_result = run_submitter("--dry-run", "--partition", "cpu", "--exclusive")

    assert default_result.returncode == 0, default_result.stderr
    assert exclusive_result.returncode == 0, exclusive_result.stderr
    assert "--exclusive" not in default_result.stdout
    assert "--exclusive" in exclusive_result.stdout
