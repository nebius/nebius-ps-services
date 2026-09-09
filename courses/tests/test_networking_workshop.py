"""Networking lesson order, bounded experiments and upstream result parsing."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from test_course_content_contract import lab_section, ROOT, load_builder
from test_course_review_fixes import load_lab


def test_networking_precedes_application_scaling():
    lessons = load_builder().parse_course(ROOT / "gpu-optimizations/COURSE.md")[2]
    assert lessons[10]["title"] == "GPU communication paths and performance"
    assert lessons[11]["title"] == "Distributed scaling and communication overlap"
    assert "Lab 17" in lab_section("gpu-optimizations", 17, "Practice")
    assert "Lab 18" in lab_section("gpu-optimizations", 17, "Practice")


def test_networking_profiles_are_one_factor_and_fail_on_conflicts():
    with load_lab("gpu-optimizations/labs/17_nccl_transport_sweep.py") as lab:
        assert lab.profile_settings("default", {}) == {}
        assert lab.profile_settings("socket", {}) == {"NCCL_NET": "Socket"}
        assert lab.profile_settings("gdr-off", {}) == {"NCCL_NET_GDR_LEVEL": "LOC"}
        assert lab.profile_settings("ring", {}) == {"NCCL_ALGO": "Ring"}
        assert lab.profile_settings("qp4", {}) == {"NCCL_IB_QPS_PER_CONNECTION": "4"}
        with pytest.raises(ValueError, match="inherited"):
            lab.profile_settings("default", {"NCCL_ALGO": "Tree"})
        with pytest.raises(ValueError, match="profile"):
            lab.profile_settings("invalid", {})
        assert lab.message_sizes(8, 32) == [8, 16, 32]
        for bounds in ((0, 32), (16, 8), (8, 2**31), (12, 32)):
            with pytest.raises(ValueError):
                lab.message_sizes(*bounds)


def test_nccl_launcher_help_and_allocation_guard():
    script = ROOT / "gpu-optimizations/slurm/nccl_tests.sbatch"
    help_run = subprocess.run(
        ["bash", str(script), "--help"], capture_output=True, text=True
    )
    assert help_run.returncode == 0
    assert "MPI" in help_run.stdout
    env = {k: v for k, v in os.environ.items() if not k.startswith("SLURM_")}
    run = subprocess.run(
        ["bash", str(script), "default"], env=env, capture_output=True, text=True
    )
    assert run.returncode != 0
    assert "Slurm" in run.stderr


def test_no_new_network_admin_commands():
    for path in (
        ROOT / "gpu-optimizations/slurm/nccl_tests.sbatch",
        ROOT / "gpu-optimizations/labs/17_nccl_transport_sweep.py",
        ROOT / "gpu-optimizations/labs/18_nccl_tests_report.py",
    ):
        text = path.read_text()
        for command in ("sudo ", "modprobe ", "setpci ", "sysctl -w", "systemctl "):
            assert command not in text


def nccl_output():
    return """# nccl-tests version 2.20.0 (b4d5bee) nccl-headers=23102 nccl-library=23102
# Collective test starting: all_reduce_perf
# nThread 1 nGpus 1 minBytes 8 maxBytes 16 step: 2(factor) warmup iters: 5 iters: 20 agg iters: 1 validation: 1 graph: 0 unalign: 0
# Using devices
# Rank 0 Group 0 Pid 10 on node-a device 0 [0000:01:00] NVIDIA H100
# Rank 1 Group 0 Pid 11 on node-b device 0 [0000:01:00] NVIDIA H100
# out-of-place in-place
# size count type redop root time algbw busbw #wrong time algbw busbw #wrong
# (B) (elements) (us) (GB/s) (GB/s) (us) (GB/s) (GB/s)
8 2 float sum -1 1.0e1 0.00 0.00 0 11 0.00 0.00 0
16 4 float sum -1 10 0.00 0.00 0 11 0.00 0.00 0
# Out of bounds values : 0 OK
# Avg bus bandwidth : 0.00
# Collective test concluded: all_reduce_perf
"""


def test_nccl_report_extracts_metrics_not_identifiers():
    with load_lab("gpu-optimizations/labs/18_nccl_tests_report.py") as lab:
        report = lab.parse_output(nccl_output(), [8, 16], 2)
        assert report["nccl_library"] == 23102
        assert report["rows"][0]["out_of_place"]["time_us"] == 10
        assert report["rows"][1]["in_place"]["wrong"] == 0
        assert "node-a" not in str(report)
        assert "Pid" not in str(report)


@pytest.mark.parametrize(
    "old,new",
    [
        ("validation: 1", "validation: 0"),
        ("0.00 0.00 0 11", "0.00 0.00 1 11"),
        ("0.00 0.00 0 11", "0.00 0.00 N/A 11"),
        ("1.0e1", "nan"),
        ("# Collective test concluded: all_reduce_perf", ""),
        ("# Out of bounds values : 0 OK", "# Out of bounds values : 1 FAILED"),
        ("# Rank 1 Group 0", "# Rank 0 Group 0"),
        ("node-b", "node-a"),
        ("device 0", "device 1"),
        ("# (B) (elements) (us)", "# (B) (elements) (ms)"),
        ("16 4 float sum -1 10 0.00 0.00 0 11 0.00 0.00 0\n", ""),
    ],
)
def test_nccl_report_rejects_unproven_output(old, new):
    with load_lab("gpu-optimizations/labs/18_nccl_tests_report.py") as lab:
        with pytest.raises(ValueError):
            lab.parse_output(nccl_output().replace(old, new), [8, 16], 2)


def test_nccl_report_rejects_duplicate_size():
    with load_lab("gpu-optimizations/labs/18_nccl_tests_report.py") as lab:
        with pytest.raises(ValueError):
            lab.parse_output(
                nccl_output() + "8 2 float sum -1 10 0 0 0 11 0 0 0\n", [8, 16], 2
            )


def test_bandwidth_uses_the_true_even_sample_median():
    with load_lab("gpu-optimizations/labs/17_nccl_transport_sweep.py") as lab:
        row = lab.summarize_curve(6000000, 2, [1, 2, 10, 11], [2, 3, 11, 12])
        assert row["median_ms"] == 6
        assert row["algbw_GBps_at_median"] == 1


def test_offline_or_diagnostic_timings_cannot_be_promoted():
    with load_lab("gpu-optimizations/labs/18_nccl_tests_report.py") as lab:
        assert lab.acceptance_timing("run", False, nccl_output())
        assert not lab.acceptance_timing("parse", False, nccl_output())
        assert not lab.acceptance_timing("run", True, nccl_output())
        assert not lab.acceptance_timing(
            "run", False, nccl_output() + "NCCL INFO diagnostic\n"
        )


@pytest.mark.parametrize("exit_code", [0, 7])
def test_mocked_mpi_launch_checks_exit_and_redacts_report(
    tmp_path, monkeypatch, exit_code
):
    binary = tmp_path / "all_reduce_perf"
    binary.write_text("non-executed fixture")
    binary.chmod(0o700)
    output = tmp_path / "results"
    for name in tuple(os.environ):
        if name.startswith(("NCCL_", "COURSE_RUN_ID")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setenv("SLURM_JOB_NUM_NODES", "2")
    monkeypatch.setenv("SLURM_NTASKS", "2")
    monkeypatch.setenv("COURSE_RUN_ID", "aabbccddeeff")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "lab",
            "--mode",
            "run",
            "--binary",
            str(binary),
            "--mpi",
            "pmix",
            "--max-bytes",
            "16",
            "--variant",
            "ring",
            "--output-dir",
            str(output),
        ],
    )
    with load_lab("gpu-optimizations/labs/18_nccl_tests_report.py") as lab:
        monkeypatch.setattr(lab.shutil, "which", lambda name: "/usr/bin/srun")

        def fake_run(command, **kwargs):
            assert "--ntasks=2" in command and "--gpus-per-task=1" in command
            assert command[command.index("-g") + 1] == "1"
            assert "-J" not in command
            assert kwargs["env"]["NCCL_ALGO"] == "Ring"
            assert kwargs["timeout"] == 900
            assert "shell" not in kwargs
            kwargs["stdout"].write(nccl_output())
            return SimpleNamespace(returncode=exit_code)

        monkeypatch.setattr(lab.subprocess, "run", fake_run)
        if exit_code:
            with pytest.raises(SystemExit):
                lab.main()
            assert not list(output.glob("*.json"))
        else:
            lab.main()
            result = next(output.glob("*.json"))
            report = json.loads(result.read_text())
            assert report["measurements"]["acceptance_timing"] is True
            assert report["measurements"]["job_local_overrides"] == {
                "NCCL_ALGO": "Ring"
            }
            assert "node-a" not in result.read_text()
            assert result.stat().st_mode & 0o777 == 0o600
        assert len(list(output.glob("*.log"))) == 1


def test_mpi_mode_is_not_interpreted_as_shell_input():
    with load_lab("gpu-optimizations/labs/18_nccl_tests_report.py") as lab:
        with pytest.raises(ValueError, match="MPI|mode"):
            lab.launch_command(
                "/path/to/all_reduce_perf", "pmix; invalid", [8, 16], 5, 20
            )


def test_network_diagram_labels_fit_cards_and_avoid_link_lines():
    paths = list((ROOT / "gpu-fundamentals/reference/diagrams").glob("*scale-out.svg"))
    paths += [
        ROOT / "gpu-fundamentals/reference/diagrams" / (stem + ".svg")
        for stem in ("gpu-networking-layers", "host-staging-versus-gpudirect-rdma")
    ]
    paths += [
        ROOT / "gpu-optimizations/reference/diagrams" / (stem + ".svg")
        for stem in ("network-evidence-sequence", "all-reduce-result-units")
    ]
    assert len(paths) == 5
    for path in paths:
        svg = ET.fromstring(path.read_text())
        assert svg.find("title").text and svg.find("desc").text
        boxes = [
            tuple(float(rect.get(key)) for key in ("x", "y", "width", "height"))
            for rect in svg.findall(".//rect")
        ]
        for text in svg.findall(".//text"):
            if text.get("text-anchor") != "middle":
                continue
            x, y = float(text.get("x")), float(text.get("y"))
            containers = [
                (w, h) for bx, by, w, h in boxes if bx < x < bx + w and by < y < by + h
            ]
            assert containers
            width, _height = min(containers)
            # Conservative layout budget, not a browser/font-rendering claim.
            assert len("".join(text.itertext())) * 19 * 0.56 <= width - 16
    diagram = ET.fromstring(paths[0].read_text())
    links = [text for text in diagram.findall(".//text") if text.text == "NVLink"]
    assert [float(text.get("x")) for text in links] == [180, 400]
