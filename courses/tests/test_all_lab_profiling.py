"""All-lab capture applicability, dashboard identity and worker-side evidence."""

import copy
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from native_job_fixtures import worker_prefix
from test_course_review_fixes import load_lab
from test_observability_integration import ROOT, load

RECIPES = [
    (path.parents[1].name, recipe)
    for path in sorted(ROOT.glob("*/reference/observability.json"))
    for recipe in json.loads(path.read_text())["labs"].values()
]


def evidence(course, recipe):
    base = ROOT / course
    return (
        (base / "reference/labs" / (recipe["lab"] + ".md")).read_text(),
        json.loads((base / recipe["dashboard"]).read_text()),
    )


@pytest.mark.parametrize(
    "course,recipe", RECIPES, ids=[c + "/" + r["lab"] for c, r in RECIPES]
)
def test_every_lab_has_truthful_capture_and_dashboard_contract(
    course, recipe, monkeypatch
):
    guide, dashboard = evidence(course, recipe)
    validator = load("validate_course_template")
    monkeypatch.setattr(validator, "ROOT", ROOT / course)
    validator.validate_lab_evidence(recipe, guide, dashboard, course)


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "wrong-dashboard",
        "wrong-query",
        "wrong-unit",
        "missing-command",
        "false-exception",
        "missing-view",
        "unrelated-gpu",
        "missing-setup",
        "duplicate-setup",
        "dashboard-prerequisite",
        "wrong-native-log",
        "wrong-native-launcher",
    ],
)
def test_evidence_validator_rejects_misleading_wiring(defect):
    course, original = next(
        (c, r) for c, r in RECIPES if r["lab"] == "01_cpu_gpu_crossover"
    )
    recipe = copy.deepcopy(original)
    guide, dashboard = evidence(course, recipe)
    if defect == "missing":
        del recipe["systems"]
    elif defect == "wrong-dashboard":
        dashboard["uid"] = "another-lab"
    elif defect == "wrong-query":
        dashboard["panels"][1]["targets"][0]["expr"] = (
            'course_lab_duration_seconds{lab="other"}'
        )
    elif defect == "wrong-unit":
        dashboard["panels"][1]["fieldConfig"]["defaults"]["unit"] = "bytes"
    elif defect == "missing-command":
        guide = guide.replace("01_cpu_gpu_crossover.nsys.sbatch", "01_cpu_gpu_crossover.sbatch")
    elif defect == "false-exception":
        recipe["systems"]["applicable"] = False
    elif defect == "missing-view":
        guide = guide.replace(recipe["systems"]["view"], "")
    elif defect == "missing-setup":
        guide = guide.replace("[Lab Guide]", "[Missing Guide]")
    elif defect == "duplicate-setup":
        link = "[Lab Guide](../../../lab-guide.html#lab-preparation-scripts)"
        guide = guide.replace(link, link + " " + link)
    elif defect == "dashboard-prerequisite":
        guide = guide.replace("## Before you start", "## Before you start\n\n[Dashboard](../grafana/assigned.json)")
    elif defect == "wrong-native-log":
        guide = guide.replace("logs/%j.out", "logs/wrong.out")
    elif defect == "wrong-native-launcher":
        guide = guide.replace("01_cpu_gpu_crossover.nsys.sbatch", "unknown.nsys.sbatch")
    else:
        recipe["gpu_telemetry"] = False
    with pytest.raises(SystemExit):
        load("validate_course_template").validate_lab_evidence(
            recipe, guide, dashboard, course
        )


@pytest.mark.parametrize("tool", ["none", "nsys"])
def test_vendor_output_is_not_contaminated_by_profiler_and_preserves_exit(
    tmp_path, monkeypatch, tool
):
    with load_lab("advanced-gpu-communication/labs/vendor_capture.py") as helper:
        monkeypatch.setenv("COURSE_PROFILE_TOOL", tool)
        monkeypatch.setenv("SLURM_JOB_ID", "123")
        monkeypatch.setenv("DEBUGINFOD_URLS", "https://symbols.example.test")
        binary = tmp_path / "nsys"
        binary.write_text(
            f"#!{sys.executable}\nimport os,subprocess,sys\n"
            "assert 'DEBUGINFOD_URLS' not in os.environ\n"
            "print('Profiler diagnostics are not vendor JSON',flush=True)\n"
            "args=sys.argv[1:]\ni=next(i for i,a in enumerate(args) if a.startswith('--output='));command=args[i+1:]\n"
            "raise SystemExit(subprocess.call(command))\n"
        )
        binary.chmod(0o700)
        monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
        output = tmp_path / "rank0.stdout"
        command = helper.worker_command(
            [sys.executable, "-c", "print('{\"ok\": true}');raise SystemExit(7)"],
            output,
            ucx=True,
            prefix=worker_prefix("29_nixl_transfer") if tool == "nsys" else None,
        )
        if tool == "nsys":
            assert "--trace=cuda,nvtx,osrt,ucx" in command
            assert "--cuda-trace-scope=process-tree" in command
            assert "--discard-environment=true" in command
        run = subprocess.run(command, capture_output=True, text=True, check=False)
        assert run.returncode == 7
        assert json.loads(output.read_text()) == {"ok": True}
        assert output.stat().st_mode & 0o077 == 0
        # A repeated capture cannot replace the first authoritative raw output.
        assert subprocess.run(command, capture_output=True, check=False).returncode != 0
        assert json.loads(output.read_text()) == {"ok": True}


def test_vendor_replay_and_out_of_allocation_capture_are_rejected(
    monkeypatch, tmp_path
):
    with load_lab("advanced-gpu-communication/labs/vendor_capture.py") as helper:
        monkeypatch.setenv("COURSE_PROFILE_TOOL", "ncu")
        with pytest.raises(ValueError, match="Systems only"):
            helper.worker_command(["vendor"], tmp_path / "report")
        monkeypatch.setenv("COURSE_PROFILE_TOOL", "nsys")
        monkeypatch.delenv("SLURM_JOB_ID", raising=False)
        with pytest.raises(ValueError, match="worker-prefix"):
            helper.worker_command(["vendor"], tmp_path / "report")


@pytest.mark.parametrize("capture", ["none", "systems"])
def test_goodput_capture_reaches_owned_server_and_is_not_published(
    monkeypatch, tmp_path, capture
):
    with load_lab("advanced-gpu-communication/labs/34_serving_goodput.py") as lab:
        seen, results = [], []
        model = tmp_path / "models--Qwen--Qwen3-8B" / "snapshots" / lab.MODEL_REVISION
        model.mkdir(parents=True)
        for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
            (model / name).write_text("{}")
        monkeypatch.setenv("COURSE_AIPERF", sys.executable)
        monkeypatch.setattr(
            sys, "argv", ["lab", "--model-dir", str(model), "--capture", capture]
        )
        monkeypatch.setattr(lab, "private_folder", lambda *a: tmp_path)
        monkeypatch.setattr(lab, "validate_common_args", lambda *a: None)
        monkeypatch.setattr(lab, "summarize", lambda *a, **kw: {"checked": True})
        monkeypatch.setattr(lab, "allocation_gpu_family", lambda: "NVIDIA H200")
        monkeypatch.setattr(lab, "write_result", lambda *a, **kw: results.append(kw))

        @contextmanager
        def server(args, folder):
            seen.append(args.capture)
            yield (
                "http://127.0.0.1:8000",
                SimpleNamespace(
                    start=lambda *a, **kw: SimpleNamespace(wait=lambda **kw: 0),
                    healthy=lambda: None,
                ),
            )

        monkeypatch.setattr(lab, "service", server)
        lab.main()
        assert seen == [capture]
        assert len(results) == (1 if capture == "none" else 0)


def test_moved_nccl_launcher_uses_existing_lab():
    launcher = (ROOT / "advanced-gpu-communication/slurm/10_nccl_tests_report.sbatch").read_text()
    assert "labs/18_nccl_tests_report.py" not in launcher
    assert "labs/10_nccl_tests_report.py" in launcher
    assert (ROOT / "advanced-gpu-communication/labs/10_nccl_tests_report.py").is_file()
