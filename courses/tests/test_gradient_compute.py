"""Local Compute admission, actual launcher dispatch and diagnostic boundaries."""

from __future__ import annotations

from course_builder import metadata as cb_metadata
import copy
import json
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








@pytest.mark.parametrize("name", list(PROCESS_ENV))
@pytest.mark.parametrize("value", ("2", "invalid"))
def test_multirank_or_malformed_environment_rejected_in_both_entrypoints(
    monkeypatch, name, value
):
    monkeypatch.setenv(name, value)
    with load_lab(
        "advanced-gpu-communication/labs/gradient_overlap_compute.py"
    ) as companion:
        with pytest.raises(SystemExit, match="one local process"):
            companion.require_single_process()




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
