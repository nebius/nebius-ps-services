"""Native jobs preserve arguments, failure propagation and isolated evidence."""

import json
from pathlib import Path

import pytest
from native_job_fixtures import ROOT, prepare_job, run_job

LAB = "01_cpu_gpu_crossover"


@pytest.mark.parametrize("mode", ["", "nsys", "ncu"])
@pytest.mark.parametrize("workload", ["small", "large"])
def test_native_job_preserves_workload_argv_and_private_outputs(
    tmp_path, mode, workload
):
    result = run_job(
        tmp_path,
        "gpu-fundamentals",
        LAB,
        mode,
        ["--workload", workload, "--seed", "19", "--note", "two words"],
        {"COURSE_PROFILE_TOOL": "ncu", "COURSE_CAPTURE": "1"},
    )
    assert result.returncode == 0, result.stderr
    app = json.loads((tmp_path / "application.json").read_text())
    assert app["argv"] == [
        f"labs/{LAB}.py",
        "--workload",
        workload,
        "--seed",
        "19",
        "--note",
        "two words",
    ]
    assert (app["tool"], app["capture"]) == (mode or "none", "1" if mode else "0")
    assert app["workload"] == workload
    job = tmp_path / "results" / LAB / "jobs/123"
    assert Path(app["results"]) == job / "results"
    assert sorted(p.name for p in job.iterdir()) == [
        "artifacts",
        "logs",
        "profiles",
        "results",
    ]
    assert all(p.stat().st_mode & 0o777 == 0o700 for p in [job, *job.iterdir()])
    if mode:
        flags = json.loads((tmp_path / "profiler.json").read_text())
        assert "python-spy" in " ".join(flags)
        if mode == "nsys":
            assert (
                "--discard-environment=true" in flags
                and "--force-overwrite=false" in flags
            )
        else:
            assert flags[flags.index("--nvtx-include") + 1] == "course_measure/"
            assert flags[flags.index("--launch-count") + 1] == "1"


@pytest.mark.parametrize("mode", ["nsys", "ncu"])
@pytest.mark.parametrize(
    "environment,expected",
    [
        ({"WORKLOAD_STATUS": "7"}, 7),
        ({"PROFILER_STATUS": "9"}, 9),
        ({"REPORT_MODE": "missing"}, 2),
        ({"REPORT_MODE": "empty"}, 2),
    ],
)
def test_native_capture_requires_success_and_a_nonempty_report(
    tmp_path, mode, environment, expected
):
    result = run_job(tmp_path, "gpu-fundamentals", LAB, mode, environment=environment)
    assert result.returncode == expected, result.stderr


@pytest.mark.parametrize(
    "arguments,environment",
    [
        (["--profile", "small"], {}),
        (["--workload", "huge"], {}),
        (["--workload"], {}),
        ([], {"SLURM_RESTART_COUNT": "1"}),
        ([], {"SLURM_JOB_ID": "bad"}),
    ],
)
def test_invalid_or_restarted_job_never_executes(tmp_path, arguments, environment):
    result = run_job(
        tmp_path, "gpu-fundamentals", LAB, arguments=arguments, environment=environment
    )
    assert result.returncode == 2
    assert not (tmp_path / "application.json").exists()


@pytest.mark.parametrize("unsafe", ["duplicate", "symlink", "permissions"])
def test_native_job_refuses_unsafe_or_existing_output(tmp_path, unsafe):
    prepare_job(tmp_path, "gpu-fundamentals", LAB)
    parent = tmp_path / "results" / LAB / "jobs"
    if unsafe == "duplicate":
        (parent / "123").mkdir()
        (parent / "123/original").write_text("keep")
    elif unsafe == "symlink":
        parent.rmdir()
        parent.symlink_to(tmp_path)
    else:
        parent.chmod(0o755)
    result = run_job(tmp_path, "gpu-fundamentals", LAB)
    assert result.returncode != 0
    assert not (tmp_path / "application.json").exists()
    if unsafe == "duplicate":
        assert (parent / "123/original").read_text() == "keep"


@pytest.mark.parametrize(
    "course,lab,mode,required",
    [
        ("llm-training", "32_learning_basics", "cuda", "--device"),
        ("llm-inference", "35_inference_basics", "nsys", "--device"),
        ("gpu-fundamentals", "08_operator_to_kernels", "nsys", "--external-only"),
        ("gpu-optimizations", "07_profile_workload", "nsys", "--external-only"),
        ("llm-training", "30_training_profiler", "ncu", "--external-only"),
    ],
)
def test_diagnostic_specializations_are_owned_by_the_job(
    tmp_path, course, lab, mode, required
):
    result = run_job(tmp_path, course, lab, mode)
    assert result.returncode == 0, result.stderr
    argv = json.loads((tmp_path / "application.json").read_text())["argv"]
    assert required in argv
    if required == "--device":
        assert argv[argv.index(required) + 1] == "cuda"


def test_all_published_job_references_are_native_and_belong_to_the_lab():
    total = 0
    for path in ROOT.glob("*/reference/observability.json"):
        for lab, row in json.loads(path.read_text())["labs"].items():
            total += 1
            for mode, relative in row["jobs"].items():
                job = path.parents[1] / relative
                assert job.is_file() and job.name.startswith(lab + ".")
                source = job.read_text()
                assert "#SBATCH --no-requeue" in source
                assert all(
                    old not in source
                    for old in ("profile_lab.py", "submit_lab.py", "server_capture.py")
                )
                assert f"results/{lab}/jobs/$SLURM_JOB_ID" in source
                assert (
                    f"labs/{lab}.py" in source
                    or path.parents[1].name == "custom-cuda-kernels"
                    or "compute_companion" in row
                )
    assert total == 110


def test_no_python_execution_wrapper_remains():
    for root in [ROOT / "tools", *ROOT.glob("*/tools")]:
        assert not any(
            (root / name).exists()
            for name in ("profile_lab.py", "submit_lab.py", "server_capture.py")
        )
