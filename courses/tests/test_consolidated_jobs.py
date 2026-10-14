"""Exercise real batch branches using explicit local scheduler/tool doubles."""

import json

import pytest

from native_job_fixtures import run_job

LAB = "01_cpu_gpu_crossover"


def result(root, lab=LAB):
    return json.loads((root / "results" / lab / "jobs/123/result.json").read_text())


def test_all_runs_only_ordered_diagnostics_and_preserves_actual_data(tmp_path):
    process = run_job(
        tmp_path,
        "gpu-fundamentals",
        LAB,
        "all",
        ["--workload", "large", "--", "--seed", "37"],
    )
    assert process.returncode == 0, process.stderr
    history = [
        json.loads(line)
        for line in (tmp_path / "application-history.jsonl").read_text().splitlines()
    ]
    assert [row["tool"] for row in history] == ["nsys", "ncu"]
    assert all(row["argv"][-2:] == ["--seed", "37"] for row in history)
    document = result(tmp_path)
    assert document["schema"] == "course-job-result/v1"
    assert document["status"] == "completed"
    assert [row["status"] for row in document["stages"]] == ["completed", "completed"]
    for row in document["stages"]:
        assert row["measurements"][0]["data"]["measurements"]["observed_value"] == 17
        assert row["measurements"][0]["measurement_candidate"] is False
        assert len(row["native_reports"]) == 1


@pytest.mark.parametrize(
    "environment",
    [
        {"REPORT_MODE": "missing"},
        {"REPORT_MODE": "empty"},
        {"WORKLOAD_STATUS": "7"},
        {"PROFILER_STATUS": "9"},
    ],
)
def test_failure_stops_all_and_retains_unstarted_stage(tmp_path, environment):
    process = run_job(tmp_path, "gpu-fundamentals", LAB, "all", environment=environment)
    assert process.returncode != 0
    document = result(tmp_path)
    assert document["status"] == "failed"
    assert [row["status"] for row in document["stages"]] == ["failed", "unstarted"]
    assert len((tmp_path / "application-history.jsonl").read_text().splitlines()) == 1
    assert document["stages"][0]["measurements"]


@pytest.mark.parametrize(
    "lab,expected",
    [
        ("03_transfer_and_pinning", ["completed", "skipped"]),
        ("10_compatibility_stack", ["skipped", "skipped"]),
    ],
)
def test_all_skips_only_unsupported_stages(tmp_path, lab, expected):
    process = run_job(tmp_path, "gpu-fundamentals", lab, "all")
    assert process.returncode == 0, process.stderr
    document = result(tmp_path, lab)
    assert [row["status"] for row in document["stages"]] == expected
    if expected == ["skipped", "skipped"]:
        assert document["status"] == "skipped"
        assert not (tmp_path / "application.json").exists()


def test_explicit_unsupported_capture_fails_without_running(tmp_path):
    process = run_job(tmp_path, "gpu-fundamentals", "10_compatibility_stack", "ncu")
    assert process.returncode == 2
    assert "unsupported" in process.stderr
    assert result(tmp_path, "10_compatibility_stack")["status"] == "failed"
    assert not (tmp_path / "application.json").exists()


def test_invalid_selector_records_failed_job_before_activation(tmp_path):
    process = run_job(tmp_path, "gpu-fundamentals", LAB, "invalid")
    assert process.returncode == 2
    document = result(tmp_path)
    assert document["status"] == "failed"
    assert "invalid choice" in document["error"]
    assert document["stages"] == []
    assert not (tmp_path / "application.json").exists()


def test_every_normal_and_supported_diagnostic_selection_matches_its_native_branch():
    from native_job_fixtures import ROOT
    from course_execution import capabilities, select

    count = 0
    for path in ROOT.glob("*/reference/execution.json"):
        course = path.parents[1]
        for lab, spec in capabilities(course).items():
            count += 1
            source = (course / "slurm" / (lab + ".sbatch")).read_text()
            normal = select(course, lab, {"profiler": "none"})
            assert len(normal) == 1 and normal[0]["experiment"] == "default"
            assert normal[0]["status"] == "unstarted"
            for declared in spec["variants"]:
                requested = {
                    name: declared[name] for name in ("experiment", "profiler")
                }
                if spec["device_option"]:
                    requested["device"] = declared["device"]
                if declared["experiment"] == "sanitizer":
                    requested["sanitizer"] = "memcheck"
                stages = select(course, lab, requested)
                assert len(stages) == 1 and stages[0]["key"] == declared["key"]
                assert f"{declared['key']})" in source
                assert (
                    declared["timeout_seconds"] > 0
                    and declared["teardown_seconds"] >= 30
                )
            stages = select(course, lab, {"profiler": "all"})
            assert [row["id"] for row in stages] == ["nsys", "ncu"]
            assert all(row.get("profiler") != "none" for row in stages)
    assert count == 110


def test_explicit_devices_topologies_and_instrumentation_conflicts_are_rejected():
    from native_job_fixtures import ROOT
    from course_execution import select

    course = ROOT / "llm-training"
    cpu = select(course, "32_learning_basics", {"device": "cpu", "profiler": "all"})
    assert [row["status"] for row in cpu] == ["skipped", "skipped"]
    with pytest.raises(ValueError, match="unsupported"):
        select(course, "32_learning_basics", {"device": "cpu", "profiler": "nsys"})
    with pytest.raises(ValueError, match="Conflicting"):
        select(course, "32_learning_basics", {"device": "cpu"}, ["--device", "cuda"])
    with pytest.raises(ValueError, match="conflicts"):
        select(
            ROOT / "advanced-gpu-communication",
            "12_distributed_scaling",
            {"profiler": "nsys"},
            ["--nnodes", "1"],
        )
    with pytest.raises(ValueError, match="cannot be combined"):
        select(
            ROOT / "custom-cuda-kernels",
            "12_capstone",
            {"experiment": "sanitizer", "sanitizer": "memcheck", "profiler": "all"},
        )


def lifecycle_fixture(root, body, *, timeout=1):
    """Real lifecycle with a bounded child; no scheduler or workload behavior hidden."""
    import os
    import subprocess
    import sys
    from native_job_fixtures import prepare_job, local_commands

    prepare_job(root, "gpu-fundamentals", LAB)
    metadata = root / "reference/execution.json"
    data = json.loads(metadata.read_text())
    for row in data["labs"][LAB]["variants"]:
        row["timeout_seconds"] = timeout
        row["teardown_seconds"] = 1
    metadata.write_text(json.dumps(data))
    launcher = root / "job.sbatch"
    launcher.write_text("course_stage() {\n" + body + "\n}\n")
    env = {**os.environ, **local_commands(root)}
    return subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(root / "tools/course_job.py"),
            "--lab",
            LAB,
            "--launcher",
            str(launcher),
            "--",
            "--profiler",
            "all",
        ],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.parametrize("mode", ["timeout", "interrupt", "orphan", "activation"])
def test_failed_lifecycle_preserves_partial_evidence_and_stops_children(tmp_path, mode):
    import signal
    import time

    body = 'printf "partial=17\\n"; '
    if mode == "activation":
        body += "return 9"
    elif mode == "orphan":
        body += "sleep 30 & echo $! > child.pid; sleep .4; return 0"
    else:
        body += "sleep 30 & echo $! > child.pid; wait"
    child = lifecycle_fixture(tmp_path, body, timeout=10 if mode == "interrupt" else 1)
    if mode == "interrupt":
        deadline = time.monotonic() + 5
        while not (tmp_path / "child.pid").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child.send_signal(signal.SIGTERM)
    stdout, stderr = child.communicate(timeout=10)
    assert child.returncode != 0, (stdout, stderr)
    document = result(tmp_path)
    assert document["status"] == "failed"
    assert [row["status"] for row in document["stages"]] == ["failed", "unstarted"]
    assert document["stages"][0]["text_results"][0]["fields"]["partial"] == 17
    if mode == "orphan":
        assert "cleanup_error" in document["stages"][0]
    if (tmp_path / "child.pid").exists():
        pid = int((tmp_path / "child.pid").read_text())
        # A reparented zombie is dead even when init has not reaped it yet.
        import subprocess

        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()
        assert not state or state.startswith("Z")


def test_short_scheduler_limit_fails_before_runtime_activation(tmp_path):
    from native_job_fixtures import prepare_job, local_commands, executable, ROOT
    import os
    import subprocess

    prepare_job(tmp_path, "gpu-fundamentals", LAB)
    env = {**os.environ, **local_commands(tmp_path)}
    executable(tmp_path / "bin/scontrol", "print('JobId=123 TimeLimit=00:00:30')\n")
    process = subprocess.run(
        ["bash", str(ROOT / "gpu-fundamentals/slurm" / (LAB + ".sbatch"))],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert process.returncode != 0
    assert not (tmp_path / "application.json").exists()
    assert result(tmp_path)["stages"][0]["status"] == "unstarted"
    assert "Scheduler time limit" in result(tmp_path)["error"]


def test_aggregate_keeps_native_adapters_and_excludes_instrumented_originals(tmp_path):
    from course_job import collect

    stage = tmp_path / "stages/normal"
    stage.mkdir(parents=True)
    (stage / "native.txt").write_text(
        "gpu_name=NVIDIA H100\nbaseline_median_ms=2\ncourse_checks=passed\n"
    )
    (stage / "original.json").write_text(
        json.dumps(
            {
                "schema": "gpu-course-result/v1",
                "experiment": {"instrumented": True},
                "measurements": {"value": 17},
                "correctness": {"passed": True},
            }
        )
    )
    row = {"id": "normal", "profiler": "none", "experiment": "default"}
    data = collect(stage, row, 0)
    assert data["measurements"][0]["measurement_candidate"] is False
    assert data["text_results"][0]["fields"]["baseline_median_ms"] == 2
    assert any(
        item["correctness"] == {"course_checks": "passed"}
        for item in data["correctness"]
    )


@pytest.mark.parametrize("leader_observed", [False, True])
def test_detached_group_ownership_survives_leader_exit(monkeypatch, leader_observed):
    import course_job
    import signal

    # The job subreaper adopts a worker whose detached group leader has exited.
    processes = {100: (1, 100, "job"), 200: (100, 200, "leader"),
                 201: (200, 200, "worker")}
    monkeypatch.setattr(course_job, "process_tree", lambda: processes.copy())
    monkeypatch.setattr(course_job.os, "getpgrp", lambda: 100)
    observed = {}
    if leader_observed:
        course_job.owned_groups(100, observed)
    del processes[200]
    processes[201] = (100, 200, "worker")
    course_job.owned_groups(100, observed)
    assert course_job.groups_remain(150, observed)
    signals = []
    monkeypatch.setattr(course_job.os, "killpg", lambda group, sig: signals.append((group, sig)))
    course_job.signal_owned_groups(150, observed, signal.SIGTERM)
    assert (200, signal.SIGTERM) in signals

    # The historical PGID and member PID cannot authorize a recycled process.
    processes[201] = (1, 200, "replacement")
    assert not course_job.groups_remain(150, observed)
    signals.clear()
    course_job.signal_owned_groups(150, observed, signal.SIGTERM)
    assert (200, signal.SIGTERM) not in signals


def test_process_tree_ignores_dead_processes_but_keeps_live_workers(monkeypatch):
    import course_job

    snapshot = """100 1 100 Ss Mon Oct 12 12:00:00 2026
200 100 200 Z Mon Oct 12 12:00:01 2026
201 100 200 S Mon Oct 12 12:00:02 2026
202 100 202 X Mon Oct 12 12:00:03 2026
"""
    monkeypatch.setattr(course_job.subprocess, "check_output", lambda *args, **kwargs: snapshot)
    assert course_job.process_tree() == {
        100: (1, 100, "Mon Oct 12 12:00:00 2026"),
        201: (100, 200, "Mon Oct 12 12:00:02 2026"),
    }


@pytest.mark.parametrize("leader_delay", [0, 1.2])
def test_linux_cleans_detached_worker_after_group_leader_exit(tmp_path, leader_delay):
    import shlex
    import subprocess
    import sys

    if sys.platform != "linux":
        pytest.skip("Requires Linux child adoption")
    program = (
        "import os,time; from pathlib import Path; os.setsid(); child=os.fork(); "
        "Path('child.pid').write_text(str(child)) if child else None; "
        f"time.sleep({leader_delay} if child else 30)"
    )
    child = lifecycle_fixture(
        tmp_path,
        f"{shlex.quote(sys.executable)} -c {shlex.quote(program)} & wait; return 0",
        timeout=5,
    )
    stdout, stderr = child.communicate(timeout=12)
    assert child.returncode != 0, (stdout, stderr)
    document = result(tmp_path)
    assert [row["status"] for row in document["stages"]] == ["failed", "unstarted"]
    assert "cleanup_error" in document["stages"][0]
    pid = (tmp_path / "child.pid").read_text().strip()
    assert not subprocess.run(
        ["ps", "-o", "stat=", "-p", pid], capture_output=True, text=True
    ).stdout.strip()


def test_linux_adopts_detached_server_before_early_stage_exit(tmp_path):
    import sys
    import subprocess

    if sys.platform != "linux":
        pytest.skip("Linux process adoption is verified in the Linux fixture container")
    child = lifecycle_fixture(
        tmp_path,
        "sleep .2; setsid sleep 30 & echo $! > child.pid; sleep .05; return 0",
        timeout=3,
    )
    stdout, stderr = child.communicate(timeout=10)
    assert child.returncode != 0, (stdout, stderr)
    document = result(tmp_path)
    assert [row["status"] for row in document["stages"]] == ["failed", "unstarted"]
    assert "cleanup_error" in document["stages"][0]
    pid = (tmp_path / "child.pid").read_text().strip()
    assert not subprocess.run(
        ["ps", "-o", "stat=", "-p", pid], capture_output=True, text=True
    ).stdout.strip()


def test_linux_completed_adopted_child_does_not_fail_successful_stage(tmp_path):
    import shlex
    import sys

    if sys.platform != "linux":
        pytest.skip("Requires Linux child adoption")
    program = """
import os, time
from pathlib import Path
pid = os.fork()
if pid == 0:
    os._exit(0)
deadline = time.monotonic() + 5
while Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1][0] != 'Z':
    if time.monotonic() >= deadline:
        raise RuntimeError('Child did not terminate')
    time.sleep(.01)
# Leave an already-dead child for the job subreaper to collect.
os._exit(0)
"""
    child = lifecycle_fixture(
        tmp_path,
        f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}; return 0",
        timeout=10,
    )
    stdout, stderr = child.communicate(timeout=15)
    assert child.returncode == 0, (stdout, stderr, result(tmp_path))
    document = result(tmp_path)
    assert document["status"] == "completed"
    assert [row["status"] for row in document["stages"]] == ["completed", "completed"]
    assert all("cleanup_error" not in row for row in document["stages"])


@pytest.mark.parametrize(
    "original",
    [
        '{"value": NaN}',
        '{"value": 1e999}',
        '{"schema":"gpu-course-result/v1","experiment":[],"measurements":{}}',
        '{"schema":"gpu-course-result/v1","experiment":null,"measurements":[]}',
        '{"value":',
    ],
)
def test_invalid_result_data_fails_honestly_and_preserves_original(tmp_path, original):
    import shlex

    child = lifecycle_fixture(
        tmp_path,
        f'printf %s {shlex.quote(original)} > "$COURSE_RESULTS_DIR/original.json"',
        timeout=10,
    )
    stdout, stderr = child.communicate(timeout=10)
    assert child.returncode != 0, (stdout, stderr)
    document = result(tmp_path)
    assert document["status"] == "failed"
    assert [row["status"] for row in document["stages"]] == ["failed", "unstarted"]
    assert document["stages"][0]["data_errors"]
    path = tmp_path / "results" / LAB / "jobs/123/stages/nsys/results/original.json"
    assert path.read_text() == original


def test_oversized_json_is_a_visible_data_error(tmp_path, monkeypatch):
    import course_job

    stage = tmp_path / "stages/normal"
    stage.mkdir(parents=True)
    path = stage / "measurement.json"
    original = json.dumps({"measurements": {"value": "x" * 100}})
    path.write_text(original)
    monkeypatch.setattr(course_job, "MAX_JSON_BYTES", 64)
    data = course_job.collect(stage, {"profiler": "none", "experiment": "default"}, 0)
    assert data["data_errors"] == [
        {
            "path": "stages/normal/measurement.json",
            "reason": "JSON exceeds the 64-byte reader limit",
        }
    ]
    assert path.read_text() == original


def test_fixed_step_scope_does_not_expand_with_its_allocation():
    from course_execution import select
    from course_job import runtime_scope
    from native_job_fixtures import ROOT

    stage = select(ROOT / "advanced-gpu-communication", "12_distributed_scaling", {})[0]
    scope = runtime_scope(stage["scope"], {"SLURM_JOB_NUM_NODES": "2"})
    assert scope["nodes"] == 1 and scope["tasks"] == 1
    assert scope == stage["scope"]


@pytest.mark.parametrize("nodes", [None, "unknown", "0", "3"])
def test_fabric_scope_rejects_unestablished_or_unsupported_allocations(nodes):
    from course_execution import select
    from course_job import runtime_scope
    from native_job_fixtures import ROOT

    stage = select(ROOT / "advanced-gpu-communication", "01_fabric_topology", {})[0]
    with pytest.raises(ValueError, match="allocation"):
        runtime_scope(stage["scope"], {"SLURM_JOB_NUM_NODES": nodes})
