import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_cxcli import soperator_passive_probe as module
from nebius_cxcli.soperator_passive_checks import _PROBE


def gpu_output(*, tests=None):
    report = {
        "status": "PASS",
        "tests": tests
        if tests is not None
        else [
            {
                "name": "module",
                "enable": True,
                "state": {"code": 0, "error": ""},
                "checks": [{"enable": True, "state": {"status": "PASS", "error": ""}}],
            }
        ],
    }
    return (
        "All checks passed\nHealth checker exit code: 0\nHealth checker stdout:\n"
        + json.dumps(report)
        + "\nHealth checker stderr:\n\n"
    )


def test_remote_probe_embeds_shared_validator_without_installed_package():
    script = (
        "namespace = {'__name__': 'observer_test'}\n"
        f"exec({_PROBE!r}, namespace)\n"
        f"result = namespace['native_probe']['child_verdict']('gpu_health_check.py', {gpu_output()!r})\n"
        "assert result['status'] == 'PASS' and result['checks'] == 1\n"
    )
    subprocess.run(
        [sys.executable, "-I", "-c", script], check=True, timeout=10, capture_output=True
    )


@pytest.fixture
def native(monkeypatch):
    expected = {
        "hashes": {"check_runner.py": "sha"},
        "config": [],
        "resources": {"gpus": 8},
        "reservation": "reserve",
        "baseline": {"observedAt": 1},
        "diagnostics": [
            {
                "name": "health",
                "command": "./health",
                "log": "slurm_scripts/$worker.$name.$context.out",
            }
        ],
        "diagnosticScripts": {"./health": "gpu_health_check.py"},
        "proofRoles": {"gpu_health_check.py": "required-measurement"},
    }
    expected["config"] = expected["diagnostics"]
    facts = {
        "state": ["IDLE", "RESERVED"],
        "reservation": "reserve",
        "realMemory": 1000,
        "gpuNames": ["H200"] * 8,
    }
    output = {
        "text": "[2026-01-01 00:00:00.100 UTC] INFO: Started\nRunning check health (./health), logging to x\nCheck health: OK\nFinished in 1.0 seconds",
        "child": gpu_output(),
    }
    monkeypatch.setattr(module.logging, "disable", lambda level: None)
    real_path = Path
    monkeypatch.setattr(
        module,
        "Path",
        lambda path: (
            SimpleNamespace(read_text=lambda: "boot")
            if path == "/proc/sys/kernel/random/boot_id"
            else real_path(path)
        ),
    )
    monkeypatch.setenv("SLURMD_NODENAME", "worker-0")
    monkeypatch.setattr(module, "mounted", lambda *args: (expected["hashes"], expected["config"]))
    monkeypatch.setattr(module, "running", lambda: [])
    monkeypatch.setattr(module, "node_facts", lambda _: facts)
    monkeypatch.setattr(
        module,
        "log_snapshot",
        lambda *args: (
            {"mtime": 9999999999999999999},
            output["child"] if len(args) == 3 else output["text"],
        ),
    )
    runner = {
        "Check": lambda **kwargs: SimpleNamespace(**kwargs),
        "get_node_info": lambda: SimpleNamespace(
            state_flags=facts["state"],
            reservation=facts["reservation"],
            real_memory_bytes=1000 * 1024 * 1024,
        ),
        "get_platform_tags": lambda: ["8xGPU", "8xH200"],
        "filter_applicable_checks": lambda checks: checks,
    }
    monkeypatch.setattr(module.runpy, "run_path", lambda *args, **kwargs: runner)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("observer must not launch diagnostics"),
    )
    return expected, facts, output, runner


def test_complete_native_execution_is_observed_without_launching_checks(native):
    expected, _, _, _ = native
    result = module.probe(expected, "accept")
    assert result["verdicts"] == [
        {
            "name": "health",
            "command": "./health",
            "status": "PASS",
            "script": "gpu_health_check.py",
            "proofRole": "required-measurement",
            "kind": "native-health",
            "reports": 1,
            "checks": 1,
            "log": {"mtime": 9999999999999999999},
        }
    ]
    assert result["coverage"] == {
        "requiredMeasurements": ["gpu_health_check.py"],
        "supportingOnly": [],
    }


def test_run_started_before_restoration_cannot_supply_fresh_evidence(native):
    expected, _, _, _ = native
    expected["baseline"]["observedAt"] = 9999999999999999998
    assert module.probe(expected, "accept")["pending"]


@pytest.mark.parametrize("incomplete", [True, False])
def test_normal_in_progress_periodic_execution_waits(native, monkeypatch, incomplete):
    expected, _, output, _ = native
    if incomplete:
        output["text"] = output["text"].replace("Finished in 1.0 seconds", "")
    else:
        monkeypatch.setattr(module, "running", lambda: ["17"])
    assert module.probe(expected, "accept")["pending"]


@pytest.mark.parametrize("selection", ["ALLOC", "MIXED", "ALLOC,MIXED,CYCLE"])
def test_idle_worker_does_not_wait_for_an_inapplicable_periodic_schedule(native, selection):
    expected, _, output, _ = native
    expected["scheduler"] = {"HealthCheckNodeState": selection}
    output["text"] = ""
    result = module.probe(expected, "accept")
    assert not result.get("pending"), "Slurm will not produce a periodic log on this idle node"
    assert result["periodicApplicable"] is False
    assert not result.get("verdicts"), "inapplicability must not invent a PASS"


@pytest.mark.parametrize(
    "selection,states,applicable",
    [
        ("ANY", ["IDLE", "RESERVED"], True),
        ("CYCLE", ["IDLE"], True),
        ("IDLE,CYCLE", ["IDLE", "RESERVED"], True),
        ("ALLOC", ["ALLOCATED"], True),
        ("MIXED", ["MIXED"], True),
        ("IDLE", ["MIXED"], False),
        ("NONDRAINED_IDLE", ["IDLE"], True),
        ("NONDRAINED_IDLE", ["IDLE", "DRAIN"], False),
        ("START_ONLY", ["IDLE"], False),
        ("REBOOT_ONLY", ["IDLE"], False),
    ],
)
def test_periodic_applicability_preserves_required_runs_and_explicit_state_filters(
    native, selection, states, applicable
):
    expected, facts, output, _ = native
    expected["scheduler"] = {"HealthCheckNodeState": selection}
    facts["state"] = states
    output["text"] = ""
    result = module.probe(expected, "accept")
    assert result["periodicApplicable"] is applicable
    assert bool(result.get("pending")) is applicable


@pytest.mark.parametrize("selection", ["UNKNOWN", "START_ONLY,IDLE", "REBOOT_ONLY,CYCLE"])
def test_unsupported_scheduler_selector_cannot_waive_periodic_evidence(native, selection):
    expected, _, _, _ = native
    expected["scheduler"] = {"HealthCheckNodeState": selection}
    with pytest.raises(RuntimeError, match="scheduler"):
        module.probe(expected, "accept")


def test_periodic_inapplicability_requires_stable_node_state(native, monkeypatch):
    expected, facts, _, _ = native
    expected["scheduler"] = {"HealthCheckNodeState": "ALLOC"}
    reads = 0

    def changing(_):
        nonlocal reads
        reads += 1
        return {**facts, "state": ["IDLE"] if reads == 1 else ["ALLOCATED"]}

    monkeypatch.setattr(module, "node_facts", changing)
    with pytest.raises(RuntimeError, match="changed during observation"):
        module.probe(expected, "accept")


@pytest.mark.parametrize(
    "failure", ["Check health: FAIL (disk)", "Failed to detect GPU platform", ""]
)
def test_exit_or_completion_marker_alone_cannot_prove_pass(native, failure):
    expected, _, output, _ = native
    output["text"] = output["text"].replace("Check health: OK", failure)
    with pytest.raises(RuntimeError, match="successfully|PASS evidence"):
        module.probe(expected, "accept")


def test_native_platform_detection_cannot_silently_fall_back_to_cpu(native):
    expected, _, _, runner = native
    runner["get_platform_tags"] = lambda: ["CPU"]
    with pytest.raises(RuntimeError, match="GPU facts"):
        module.probe(expected, "accept")


@pytest.mark.parametrize("reservation", ["", "foreign", "reserve-extra"])
def test_paused_evidence_requires_exact_owned_reservation(native, reservation):
    expected, facts, _, _ = native
    facts["reservation"] = reservation
    with pytest.raises(RuntimeError, match="reservation"):
        module.probe(expected, "paused")


def test_mounted_exclusion_alone_does_not_prove_effective_suppression(native):
    expected, _, _, _ = native
    with pytest.raises(RuntimeError, match="suppression is ineffective"):
        module.probe(expected, "paused")


def test_job_hook_logs_are_bound_to_exact_attempt(native):
    expected, _, output, _ = native
    expected.update(context="prolog", job="17", attempt="1")
    output["text"] += '\nEnvironment SLURM_JOB_ID="17"\nEnvironment SLURM_RESTART_COUNT="0"'
    with pytest.raises(RuntimeError, match="another Slurm attempt"):
        module.probe(expected, "job")


@pytest.mark.parametrize(
    "script,child_output",
    [
        ("boot_disk_full.sh", "Could not determine boot disk usage\n"),
        ("gpu_health_check.py", "Error when running checks\nHealth checker exit code: 0\n"),
        ("alloc_mem_used.drain.sh", "No info about the memory allocated for the job, exiting\n"),
        ("alloc_mem_used.undrain.sh", "No info about the node real memory, exiting\n"),
    ],
)
def test_native_ok_without_child_measurement_cannot_prove_pass(
    native, monkeypatch, script, child_output
):
    expected, _, output, _ = native
    expected["diagnosticScripts"] = {"./health": script}
    expected["proofRoles"] = {script: "required-measurement"}
    monkeypatch.setattr(
        module,
        "log_snapshot",
        lambda *args: (
            {"mtime": 9999999999999999999},
            child_output if len(args) == 3 else output["text"],
        ),
    )
    with pytest.raises(RuntimeError, match="measurement|health result"):
        module.probe(expected, "accept")


@pytest.mark.parametrize(
    "script,output",
    [
        ("boot_disk_full.sh", "Node boot disk is 12% full (threshold 80%)\n"),
        (
            "alloc_mem_used.drain.sh",
            "System available memory: 2048\nJob allocated memory: 1024\nEnough available memory on the node\n",
        ),
        (
            "alloc_mem_used.undrain.sh",
            "System available memory: 2048\nNode real memory: 1024\nEnough available memory on the node\n",
        ),
    ],
)
def test_reviewed_child_health_results_require_positive_measurement(script, output):
    assert module.child_verdict(script, output) == {"status": "PASS"}


@pytest.mark.parametrize("defect", ["summary-only", "empty-tests", "missing-checks", "skipped"])
def test_gpu_summary_pass_requires_actual_enabled_test_and_subcheck_results(native, defect):
    expected, _, output, _ = native
    if defect == "summary-only":
        output["child"] = "All checks passed\nHealth checker exit code: 0\n"
    elif defect == "empty-tests":
        output["child"] = gpu_output(tests=[])
    else:
        test = {
            "enable": True,
            "state": {"code": 0, "error": ""},
            "checks": [],
        }
        if defect == "skipped":
            test["checks"] = [{"enable": True, "state": {"status": "SKIP", "error": ""}}]
        output["child"] = gpu_output(tests=[test])
    with pytest.raises(RuntimeError, match="health.*(report|coverage|non-PASS)"):
        module.probe(expected, "accept")


@pytest.mark.parametrize(
    "script,output",
    [
        ("alloc_gpus_busy.drain.sh", "No GPU devices are requested by user\n"),
        ("nvme_raid_health.sh", "No NVMe disks detected, skipping\n"),
        ("nvme_raid_health.sh", "No NVMe-backed RAID arrays detected, skipping\n"),
    ],
)
def test_native_hardware_or_allocation_skips_are_not_pass(script, output):
    verdict = module.child_verdict(script, output)
    assert verdict["status"] == "NATIVE_SKIP" and verdict["limitation"]


@pytest.mark.parametrize(
    "script,output",
    [
        ("alloc_gpus_busy.drain.sh", ""),
        ("alloc_gpus_busy.undrain.sh", "No processes running on GPUs\n"),
        ("nvme_raid_health.sh", "NVMe RAID health check passed\n"),
        (
            "nvme_raid_health.sh",
            "Could not read dmesg for NVMe RAID check, skipping dmesg probe\nNVMe RAID health check passed\n",
        ),
    ],
)
def test_native_completion_does_not_invent_an_unreported_measurement(script, output):
    verdict = module.child_verdict(script, output)
    assert verdict["status"] == "NATIVE_OK" and verdict["limitation"]


def test_supporting_evidence_is_separate_from_required_measurement_coverage(native):
    expected, _, output, _ = native
    expected["diagnosticScripts"] = {"./health": "nvme_raid_health.sh"}
    expected["proofRoles"] = {"nvme_raid_health.sh": "supporting-only"}
    output["child"] = "No NVMe disks detected, skipping\n"
    result = module.probe(expected, "accept")
    assert result["verdicts"][0]["status"] == "NATIVE_SKIP"
    assert result["notApplicable"] == []
    assert result["coverage"] == {
        "requiredMeasurements": [],
        "supportingOnly": ["nvme_raid_health.sh"],
    }


def test_supporting_completion_cannot_satisfy_a_required_proof_role(native):
    expected, _, output, _ = native
    expected["diagnosticScripts"] = {"./health": "nvme_raid_health.sh"}
    expected["proofRoles"] = {"nvme_raid_health.sh": "required-measurement"}
    output["child"] = "NVMe RAID health check passed\n"
    with pytest.raises(RuntimeError, match="frozen proof role"):
        module.probe(expected, "accept")


@pytest.mark.parametrize("mtime", [1, 10000000000000000000])
def test_child_evidence_outside_runner_interval_is_pending(native, monkeypatch, mtime):
    expected, _, output, _ = native
    monkeypatch.setattr(
        module,
        "log_snapshot",
        lambda *args: (
            {"mtime": mtime if len(args) == 3 else 9999999999999999999},
            output["child"] if len(args) == 3 else output["text"],
        ),
    )
    assert module.probe(expected, "accept")["pending"]


def test_rewritten_child_log_during_observation_is_pending(native, monkeypatch):
    expected, _, output, _ = native
    reads = 0

    def snapshot(*args):
        nonlocal reads
        if len(args) == 3:
            reads += 1
        return {"mtime": 9999999999999999999, "sha256": str(reads)}, output["child"] if len(
            args
        ) == 3 else output["text"]

    monkeypatch.setattr(module, "log_snapshot", snapshot)
    assert module.probe(expected, "accept")["pending"]


def test_native_output_reader_is_bounded_and_rejects_symlinks(tmp_path, monkeypatch):
    path = tmp_path / "worker-0.health.prolog.out"
    path.write_text("result\n")
    monkeypatch.setattr(module, "Path", lambda _: tmp_path)
    evidence, text = module.log_snapshot("prolog", "worker-0", "health")
    assert text == "result\n" and evidence["sha256"]
    path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(RuntimeError, match="bounded reader"):
        module.log_snapshot("prolog", "worker-0", "health")
    link = tmp_path / "worker-0.link.prolog.out"
    link.symlink_to(path)
    with pytest.raises(OSError):
        module.log_snapshot("prolog", "worker-0", "link")


def test_native_output_reader_detects_concurrent_write(tmp_path, monkeypatch):
    path = tmp_path / "worker-0.health.prolog.out"
    path.write_text("old result\n")
    monkeypatch.setattr(module, "Path", lambda _: tmp_path)
    fstat = module.os.fstat
    reads = 0

    def changed(fd):
        nonlocal reads
        reads += 1
        if reads == 2:
            path.write_text("new different result\n")
        return fstat(fd)

    monkeypatch.setattr(module.os, "fstat", changed)
    assert module.log_snapshot("prolog", "worker-0", "health") == ({}, "")
