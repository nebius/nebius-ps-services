"""All-lab capture applicability, dashboard identity and worker-side evidence."""

import copy
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
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
        guide = guide.replace(recipe["systems_command"], "")
    elif defect == "false-exception":
        recipe["systems"]["applicable"] = False
    elif defect == "missing-view":
        guide = guide.replace(recipe["systems"]["view"], "")
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
            "args=sys.argv[1:]\ncommand=args[args.index('--output')+2:]\n"
            "raise SystemExit(subprocess.call(command))\n"
        )
        binary.chmod(0o700)
        monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
        output = tmp_path / "rank0.stdout"
        command = helper.worker_command(
            [sys.executable, "-c", "print('{\"ok\": true}');raise SystemExit(7)"],
            output,
            ucx=True,
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
        with pytest.raises(ValueError, match="allocation"):
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
    launcher = (ROOT / "advanced-gpu-communication/slurm/nccl_tests.sbatch").read_text()
    assert "labs/18_nccl_tests_report.py" not in launcher
    assert "labs/10_nccl_tests_report.py" in launcher
    assert (ROOT / "advanced-gpu-communication/labs/10_nccl_tests_report.py").is_file()


@pytest.mark.parametrize(
    "lab,extra,expected",
    [
        ("24_checkpoint_resume", [], 0),
        ("01_fabric_topology", [], 0),
        ("10_hopper_cluster", [], 0),
        ("32_learning_basics", ["--device", "cuda"], 0),
        ("32_learning_basics", ["--device=cuda"], 0),
        ("32_learning_basics", ["--device", "cpu"], 2),
        ("27_paged_kv", [], 2),
    ],
)
def test_profile_entrypoint_admits_gpu_work_and_rejects_cpu_exceptions(
    monkeypatch, tmp_path, lab, extra, expected
):
    profiler = load("profile_lab")
    recipe = next(r for _, r in RECIPES if r["lab"] == lab)
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference/observability.json").write_text(
        json.dumps({"labs": {lab: recipe}})
    )
    monkeypatch.setattr(profiler, "ROOT", tmp_path)
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setenv("RANK", "1")
    monkeypatch.setenv("DEBUGINFOD_URLS", "https://symbols.example.test")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "profile_lab",
            "--lab",
            lab,
            "--tool",
            "nsys",
            "--",
            "python",
            "lab.py",
            *extra,
        ],
    )
    monkeypatch.setattr(profiler.shutil, "which", lambda x: x)
    seen = []

    def start(command, **kwargs):
        seen.append((command, kwargs))
        Path(command[command.index("--output") + 1]).with_suffix(
            ".nsys-rep"
        ).write_bytes(b"fixture report")
        return SimpleNamespace(wait=lambda **kw: 0, poll=lambda: 0)

    monkeypatch.setattr(profiler.subprocess, "Popen", start)
    # Keep the fixture from changing the test process's umask.
    monkeypatch.setattr(profiler.os, "umask", lambda _: None)
    with pytest.raises(SystemExit) as ended:
        profiler.main()
    assert ended.value.code == expected
    if expected == 0:
        command, options = seen[0]
        assert options["env"]["COURSE_CAPTURE"] == "1"
        assert "DEBUGINFOD_URLS" not in options["env"]
        assert profiler.os.environ["DEBUGINFOD_URLS"] == "https://symbols.example.test"
        assert "--cuda-trace-scope=process-tree" in command
        if recipe["systems"]["target"] == "rank":
            assert "--trace=cuda,nvtx,osrt,nccl" in command
        receipt = list((tmp_path / "results" / lab / "profiles").glob("*.json"))
        assert len(receipt) == 1
        assert json.loads(receipt[0].read_text())["acceptance_timing"] is False
    else:
        assert seen == []


@pytest.mark.parametrize("tool", ["nsys", "ncu"])
@pytest.mark.parametrize("report_state", ["missing", "empty", "nonempty"])
@pytest.mark.parametrize("profiler_status", [0, 7])
def test_success_requires_report_and_preserves_profiler_failure(
    monkeypatch, tmp_path, capsys, tool, report_state, profiler_status
):
    profiler = load("profile_lab")
    lab = "01_cpu_gpu_crossover"
    recipe = next(r for _, r in RECIPES if r["lab"] == lab)
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference/observability.json").write_text(
        json.dumps({"labs": {lab: recipe}})
    )
    monkeypatch.setattr(profiler, "ROOT", tmp_path)
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setenv("RANK", "0")
    monkeypatch.setattr(
        sys,
        "argv",
        ["profile_lab", "--lab", lab, "--tool", tool, "--", "python", "lab.py"],
    )
    monkeypatch.setattr(profiler.shutil, "which", lambda x: x)
    monkeypatch.setattr(profiler.os, "umask", lambda _: None)

    def start(command, **kwargs):
        if report_state != "missing":
            option = "--export" if tool == "ncu" else "--output"
            extension = ".ncu-rep" if tool == "ncu" else ".nsys-rep"
            Path(command[command.index(option) + 1]).with_suffix(extension).write_bytes(
                b"fixture report" if report_state == "nonempty" else b""
            )
        return SimpleNamespace(
            wait=lambda **kw: profiler_status, poll=lambda: profiler_status
        )

    monkeypatch.setattr(profiler.subprocess, "Popen", start)
    with pytest.raises(SystemExit) as ended:
        profiler.main()
    expected = profiler_status or (0 if report_state == "nonempty" else 2)
    assert ended.value.code == expected
    receipts = list((tmp_path / "results" / lab / "profiles").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["exit_code"] == expected
    assert receipt["acceptance_timing"] is False
    assert ("capture failed" in capsys.readouterr().err) == (
        profiler_status == 0 and report_state != "nonempty"
    )
