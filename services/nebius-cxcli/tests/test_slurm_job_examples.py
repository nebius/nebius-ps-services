from __future__ import annotations

import os
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
    "--check-jobs",
    "--check-once",
    "--check-interval <seconds>",
    "--check-duration <seconds>",
    "--check-job-name <pattern>",
    "--check-job-ids <ids>",
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
    assert "submit-job-test.sh login <login-external-ip>" in result.stdout
    assert "Copying is part of login mode" in result.stdout
    assert "Default: auto" in result.stdout
    assert "Examples:\n  ./submit-job-test.sh\n" in result.stdout
    for flag in PUBLIC_FLAGS:
        assert flag in result.stdout


def test_example_readme_documents_login_node_copy_flow() -> None:
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")

    assert "./examples/slurm-jobs/submit-job-test.sh login <login-external-ip>" in readme
    assert "login-node SSH session" in readme
    assert "/root/testjobs" in readme
    assert "./submit-job-test.sh --check-jobs --check-duration 900" in readme
    assert "timestamped proof stream" in readme
    assert "scp -r examples/slurm-jobs" not in readme
    assert "cd /shared/slurm-jobs" not in readme


def test_example_readme_starts_submit_examples_with_bare_command() -> None:
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
    gpu_section = readme.split("## Submit GPU Jobs", 1)[1]
    first_example = gpu_section.split("```bash\n", 1)[1].split("\n```", 1)[0]

    assert first_example == "./submit-job-test.sh"


def test_login_dry_run_prints_copy_and_remote_shell_commands() -> None:
    result = run_submitter("login", "203.0.113.10", "--dry-run")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("ssh root@203.0.113.10 ")
    assert "mkdir" in lines[0]
    assert "/root/testjobs" in lines[0]
    assert lines[1].startswith("scp -r ")
    assert f"{EXAMPLE_DIR}/." in lines[1]
    assert "root@203.0.113.10:/root/testjobs/" in lines[1]
    assert lines[2].startswith("ssh -t root@203.0.113.10 ")
    assert "cd" in lines[2]
    assert "/root/testjobs" in lines[2]


def test_submitter_rejects_unknown_options() -> None:
    result = run_submitter("--unknown-option")

    assert result.returncode != 0
    assert "Unknown option: --unknown-option" in result.stderr


def test_submitter_rejects_removed_kind_option() -> None:
    result = run_submitter("--kind", "gpu")

    assert result.returncode != 0
    assert "Unknown option: --kind" in result.stderr


def test_default_dry_run_uses_gpu_template_on_slurm_default_partition() -> None:
    result = run_submitter("--dry-run")

    assert result.returncode == 0, result.stderr
    lines = sbatch_lines(result.stdout)
    assert len(lines) == 1
    assert "--partition" not in result.stdout
    assert "--gres=gpu:1" in result.stdout
    assert "sop-gpu-job-test-01" in result.stdout
    assert "gpu-job-test.sbatch" in result.stdout


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


def test_check_jobs_dry_run_prints_monitor_command_without_submitting() -> None:
    result = run_submitter(
        "--check-jobs",
        "--check-job-ids",
        "12345,12346",
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    assert sbatch_lines(result.stdout) == []
    assert "squeue -h -o" in result.stdout
    assert "sacct -X -n -P -j 12345\\,12346" in result.stdout


def test_check_jobs_accepts_completed_explicit_job_from_sacct(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    squeue = bin_dir / "squeue"
    sacct = bin_dir / "sacct"
    squeue.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '60|RUNNING|00:10|20:00|main|worker-0|sop-gpu-job-test-01'\n",
        encoding="utf-8",
    )
    sacct.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '59|COMPLETED|0:0|00:30:04|2026-07-04T05:09:24|2026-07-04T05:39:28'\n"
        "printf '%s\\n' '60|RUNNING|0:0|00:10|2026-07-04T05:40:00|Unknown'\n",
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
            "--check-jobs",
            "--check-once",
            "--check-job-ids",
            "59,60",
        ],
        check=False,
        cwd=EXAMPLE_DIR,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "job_id=60 state=RUNNING" in result.stdout
    assert "job_id=59 state=COMPLETED source=sacct terminal=completed" in result.stdout
    assert "Slurm job monitor result: PASS - observed 2 job id(s)" in result.stdout


def test_exclusive_is_only_added_when_requested() -> None:
    default_result = run_submitter("--dry-run", "--partition", "cpu")
    exclusive_result = run_submitter("--dry-run", "--partition", "cpu", "--exclusive")

    assert default_result.returncode == 0, default_result.stderr
    assert exclusive_result.returncode == 0, exclusive_result.stderr
    assert "--exclusive" not in default_result.stdout
    assert "--exclusive" in exclusive_result.stdout
