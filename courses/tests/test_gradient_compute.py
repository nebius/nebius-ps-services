"""Local Compute admission, actual launcher dispatch and diagnostic boundaries."""

from __future__ import annotations

from course_builder import metadata as cb_metadata
import copy
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import torch
from test_course_review_fixes import load_lab
from test_gradient_overlap import seeds
from test_observability_integration import ROOT, load

COURSE = ROOT / "advanced-gpu-communication"
LAB = "19_gradient_overlap"
PROCESS_ENV = {
    "WORLD_SIZE": "1",
    "LOCAL_WORLD_SIZE": "1",
    "SLURM_NTASKS": "1",
    "SLURM_JOB_NUM_NODES": "1",
    "SLURM_NNODES": "1",
    "RANK": "0",
    "LOCAL_RANK": "0",
    "SLURM_PROCID": "0",
    "SLURM_LOCALID": "0",
}


@pytest.fixture
def compute_fixture(tmp_path, monkeypatch):
    for folder in ("reference", "labs", "tools", "slurm", "bin"):
        (tmp_path / folder).mkdir()
    recipe = json.loads((COURSE / "reference/observability.json").read_text())["labs"][
        LAB
    ]
    (tmp_path / "reference/observability.json").write_text(
        json.dumps({"labs": {LAB: recipe}})
    )
    (tmp_path / recipe["compute_companion"]["path"]).write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path('invocation.json').write_text(json.dumps({'argv':sys.argv,'capture':os.environ['COURSE_CAPTURE']}))\n"
        "raise SystemExit(int(os.environ.get('WORKLOAD_STATUS','0')))\n"
    )
    shutil.copy2(ROOT / "tools/profile_lab.py", tmp_path / "tools/profile_lab.py")
    shutil.copy2(
        COURSE / "slurm/ncu_single_gpu.sbatch", tmp_path / "slurm/ncu_single_gpu.sbatch"
    )
    ncu = tmp_path / "bin/ncu"
    ncu.write_text(
        f"#!{sys.executable}\nimport json,os,subprocess,sys\nfrom pathlib import Path\n"
        "args=sys.argv[1:]\nPath('ncu-args.json').write_text(json.dumps(args))\n"
        "i=args.index('--export')\nstatus=subprocess.call(args[i+2:])\n"
        "mode=os.environ.get('REPORT_MODE','present')\n"
        "if mode!='missing': Path(args[i+1]+'.ncu-rep').write_text('stub report' if mode=='present' else '')\n"
        "raise SystemExit(status)\n"
    )
    ncu.chmod(0o700)
    srun = tmp_path / "bin/srun"
    srun.write_text(
        f"#!{sys.executable}\nimport json,os,sys\nfrom pathlib import Path\n"
        "args=sys.argv[1:]\nPath('srun-args.json').write_text(json.dumps(args))\n"
        "while args[0].startswith('--'): args.pop(0)\n"
        "os.execv(args[0],args)\n"
    )
    srun.chmod(0o700)
    monkeypatch.chdir(tmp_path)
    for name, value in PROCESS_ENV.items():
        monkeypatch.setenv(name, value)
    for name in (
        "COURSE_PROFILE_RANGE",
        "COURSE_PROFILE_KERNEL",
        "NCU_SET",
        "NCU_SECTIONS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setenv("COURSE_PYTHON", sys.executable)
    monkeypatch.setenv("PATH", str(tmp_path / "bin") + os.pathsep + os.environ["PATH"])
    helper = load("profile_lab")
    monkeypatch.setattr(helper, "ROOT", tmp_path)
    return tmp_path, helper, recipe


@pytest.mark.parametrize(
    "mode,status,expected",
    (
        ("present", 0, 0),
        ("present", 7, 7),
        ("empty", 0, 2),
        ("missing", 0, 2),
    ),
)
def test_actual_compute_launcher_uses_fixed_companion_and_private_reports(
    compute_fixture, monkeypatch, mode, status, expected
):
    root, _helper, _recipe = compute_fixture
    monkeypatch.setenv("REPORT_MODE", mode)
    monkeypatch.setenv("WORKLOAD_STATUS", str(status))
    run = subprocess.run(
        [
            "bash",
            "slurm/ncu_single_gpu.sbatch",
            "--profile",
            "small",
            "--bucket-mib",
            "8",
            "24",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == expected, run.stderr
    invocation = json.loads((root / "invocation.json").read_text())
    assert invocation == {
        "capture": "1",
        "argv": [
            "labs/gradient_overlap_compute.py",
            "--profile",
            "small",
            "--bucket-mib",
            "8",
            "24",
        ],
    }
    arguments = json.loads((root / "ncu-args.json").read_text())
    for key, value in (
        ("--nvtx-include", "gradient_backward/"),
        ("--nvtx-push-pop-scope", "process"),
        ("--replay-mode", "kernel"),
        ("--kill", "no"),
        ("--launch-count", "1"),
        ("--set", "basic"),
    ):
        assert arguments[arguments.index(key) + 1] == value
    assert "--force-overwrite" not in arguments
    assert json.loads((root / "srun-args.json").read_text())[:3] == [
        "--nodes=1",
        "--ntasks=1",
        "--gpus-per-task=1",
    ]
    receipts = list((root / "results" / LAB / "profiles").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["exit_code"] == expected and receipt["acceptance_timing"] is False
    assert receipts[0].stat().st_mode & 0o077 == 0
    assert receipts[0].parent.stat().st_mode & 0o077 == 0
    assert not list((root / "results").glob("*.json"))


@pytest.mark.parametrize(
    "defect",
    (
        "shell",
        "env",
        "torchrun",
        "code",
        "module",
        "other-script",
        "path-in-tail",
        "distributed-script",
        "server",
        "range",
        "foreign-path",
        "bad-metadata",
        "server-kind",
    ),
)
def test_companion_admission_rejects_bypasses_before_side_effects(
    compute_fixture, monkeypatch, defect
):
    root, helper, recipe = compute_fixture
    command = [sys.executable, recipe["compute_companion"]["path"]]
    options = []
    if defect in ("shell", "env", "torchrun"):
        command.insert(
            0, {"shell": "bash", "env": "env", "torchrun": "torchrun"}[defect]
        )
    elif defect in ("code", "module"):
        command.insert(1, "-c" if defect == "code" else "-m")
    elif defect in ("other-script", "distributed-script"):
        command[1] = (
            "labs/other.py"
            if defect == "other-script"
            else "labs/19_gradient_overlap.py"
        )
    elif defect == "path-in-tail":
        command.insert(1, "labs/other.py")
    elif defect == "server":
        options = ["--server"]
    elif defect == "range":
        options = ["--range", "lab_workload"]
    elif defect == "foreign-path":
        recipe["compute_companion"]["path"] = "../outside.py"
    elif defect == "bad-metadata":
        recipe["compute_companion"] = True
    elif defect == "server-kind":
        recipe["kind"] = "server-client"
        options = ["--server"]
    (root / "reference/observability.json").write_text(
        json.dumps({"labs": {LAB: recipe}})
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["profile", "--lab", LAB, "--tool", "ncu", *options, "--", *command],
    )
    with pytest.raises(SystemExit) as error:
        helper.main()
    assert error.value.code == 2
    assert not (root / "results").exists()
    assert not (root / "ncu-args.json").exists()


@pytest.mark.parametrize("name", list(PROCESS_ENV))
@pytest.mark.parametrize("value", ("2", "invalid"))
def test_multirank_or_malformed_environment_rejected_in_both_entrypoints(
    compute_fixture, monkeypatch, name, value
):
    root, helper, recipe = compute_fixture
    monkeypatch.setenv(name, value)
    command = [sys.executable, recipe["compute_companion"]["path"]]
    with pytest.raises(ValueError, match="one local process"):
        helper.admit_compute_companion(recipe, command, None, server=False)
    with load_lab(
        "advanced-gpu-communication/labs/gradient_overlap_compute.py"
    ) as companion:
        with pytest.raises(SystemExit, match="one local process"):
            companion.require_single_process()
    assert not (root / "results").exists()


@pytest.mark.parametrize("name", ("NCU_SET", "NCU_SECTIONS"))
def test_launcher_rejects_obsolete_overrides(compute_fixture, monkeypatch, name):
    root, _helper, _recipe = compute_fixture
    monkeypatch.setenv(name, "")
    run = subprocess.run(
        ["bash", "slurm/ncu_single_gpu.sbatch"], capture_output=True, text=True
    )
    assert run.returncode == 2 and "unsupported" in run.stderr
    assert not (root / "srun-args.json").exists()


@pytest.mark.parametrize("corrupt", (False, True))
def test_companion_captures_only_final_backward_and_never_writes_acceptance(
    tmp_path, monkeypatch, capsys, corrupt
):
    events, active = [], []

    @contextmanager
    def region(name):
        assert name == "gradient_backward"
        active.append(name)
        yield
        active.pop()

    class CpuTorch:
        cuda = SimpleNamespace(
            device_count=lambda: 1,
            synchronize=lambda: None,
            nvtx=SimpleNamespace(range=region),
        )

        def __getattr__(self, name):
            return getattr(torch, name)

    original_backward = torch.Tensor.backward

    def backward(self, *args, **kwargs):
        events.append(("backward", bool(active)))
        return original_backward(self, *args, **kwargs)

    monkeypatch.setattr(torch.Tensor, "backward", backward)
    monkeypatch.chdir(tmp_path)
    for name, value in PROCESS_ENV.items():
        monkeypatch.setenv(name, value)
    with load_lab(
        "advanced-gpu-communication/labs/gradient_overlap_compute.py"
    ) as module:
        monkeypatch.setattr(module, "load_torch", CpuTorch)
        monkeypatch.setattr(module, "require_course_gpu", lambda _torch: {})
        monkeypatch.setattr(module, "seed_everything", lambda *_args: None)
        monkeypatch.setattr(
            module, "make_bucket_seeds", lambda *_args, **_kwargs: seeds()
        )
        original_loss, original_reference = (
            module.bucket_loss,
            module.local_gradient_reference,
        )

        def loss(*args):
            assert not active
            return original_loss(*args)

        def reference(*args):
            assert not active
            values = original_reference(*args)
            return [torch.zeros_like(value) for value in values] if corrupt else values

        monkeypatch.setattr(module, "bucket_loss", loss)
        monkeypatch.setattr(module, "local_gradient_reference", reference)
        monkeypatch.setattr(sys, "argv", ["companion", "--warmup", "2"])
        if corrupt:
            with pytest.raises(SystemExit, match="independent gradient"):
                module.main()
        else:
            module.main()
    assert events == [("backward", False), ("backward", False), ("backward", True)]
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["acceptance_timing"] is False
    assert diagnostic["gradient_validation"]["passed"] is not corrupt
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("defect", ("command", "path", "range", "kind"))
def test_validator_rejects_broken_companion_contract(monkeypatch, defect):
    recipe = copy.deepcopy(
        json.loads((COURSE / "reference/observability.json").read_text())["labs"][LAB]
    )
    guide = (COURSE / "reference/labs" / (LAB + ".md")).read_text()
    dashboard = json.loads((COURSE / recipe["dashboard"]).read_text())
    if defect == "command":
        recipe["learner_compute_command"] = "absent from guide"
    elif defect == "path":
        recipe["compute_companion"]["path"] = "labs/missing.py"
    elif defect == "range":
        recipe["compute_companion"]["nvtx_range"] = "missing_range"
    else:
        recipe["kind"] = "server-client"
    validator = load("validate_course_template")
    monkeypatch.setattr(validator, "ROOT", COURSE)
    message = "native Compute command" if defect == "command" else "Compute companion"
    with pytest.raises(SystemExit, match=message):
        validator.validate_lab_evidence(recipe, guide, dashboard, COURSE.name)


def test_source_companion_is_standalone_without_a_new_lab_identity(tmp_path):
    metadata = cb_metadata.course_metadata(COURSE)
    assert len(metadata["labs"]) == 34
    assert "labs/gradient_overlap_compute.py" not in {
        row["path"] for row in metadata["labs"]
    }
    for name in (
        "gradient_overlap_compute.py",
        "gradient_overlap_common.py",
        "training_common.py",
        "course_evidence.py",
    ):
        shutil.copy2(COURSE / "labs" / name, tmp_path / name)
    run = subprocess.run(
        [sys.executable, str(tmp_path / "gradient_overlap_compute.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert "--bucket-mib" in run.stdout and "--output-dir" not in run.stdout
