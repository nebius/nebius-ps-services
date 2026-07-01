from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = REPO_ROOT / "examples" / "slurm-jobs"
SUBMITTER = EXAMPLE_DIR / "submit-soperator-smoke.sh"
CPU_BATCH = EXAMPLE_DIR / "cpu-drain-smoke.sbatch"
GPU_BATCH = EXAMPLE_DIR / "gpu-drain-smoke.sbatch"


PUBLIC_FLAGS = (
    "--kind cpu|gpu",
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
    for flag in PUBLIC_FLAGS:
        assert flag in result.stdout


def test_submitter_rejects_unknown_options() -> None:
    result = run_submitter("--unknown-option")

    assert result.returncode != 0
    assert "Unknown option: --unknown-option" in result.stderr


def test_cpu_dry_run_prints_one_sbatch_per_loop_job_without_gpu_gres() -> None:
    result = run_submitter("--dry-run", "--kind", "cpu", "--count", "3")

    assert result.returncode == 0, result.stderr
    lines = sbatch_lines(result.stdout)
    assert len(lines) == 3
    assert "--gres" not in result.stdout
    assert "sop-cpu-smoke-01" in result.stdout
    assert "sop-cpu-smoke-02" in result.stdout
    assert "sop-cpu-smoke-03" in result.stdout


def test_gpu_dry_run_prints_one_sbatch_per_loop_job_with_gpu_gres() -> None:
    result = run_submitter(
        "--dry-run",
        "--kind",
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
    assert "sop-gpu-smoke-01" in result.stdout
    assert "sop-gpu-smoke-02" in result.stdout
    assert "sop-gpu-smoke-03" in result.stdout


def test_array_mode_dry_run_prints_one_sbatch_command() -> None:
    result = run_submitter("--dry-run", "--count", "3", "--submit-mode", "array")

    assert result.returncode == 0, result.stderr
    lines = sbatch_lines(result.stdout)
    assert len(lines) == 1
    assert "--array=0-2" in result.stdout


def test_exclusive_is_only_added_when_requested() -> None:
    default_result = run_submitter("--dry-run", "--kind", "cpu")
    exclusive_result = run_submitter("--dry-run", "--kind", "cpu", "--exclusive")

    assert default_result.returncode == 0, default_result.stderr
    assert exclusive_result.returncode == 0, exclusive_result.stderr
    assert "--exclusive" not in default_result.stdout
    assert "--exclusive" in exclusive_result.stdout
