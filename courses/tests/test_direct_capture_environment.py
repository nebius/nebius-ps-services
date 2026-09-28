"""Direct launchers retain runtime inputs without collecting environment metadata."""

import json
import os
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from test_course_review_fixes import ROOT, load_lab


@pytest.mark.parametrize(
    "course", ["gpu-optimizations", "advanced-gpu-communication", "custom-cuda-kernels"]
)
def test_direct_shell_capture_preserves_arguments_and_environment(tmp_path, course):
    binary = tmp_path / "bin"
    binary.mkdir()
    fixtures = {
        "srun": "import os,sys\na=sys.argv[1:]\nwhile a[0].startswith('--'): a.pop(0)\nos.execvpe(a[0],a,os.environ)\n",
        "container": "import os,sys\na=sys.argv[2:]\nos.execvpe(a[0],a,os.environ)\n",
        "nsys": "import json,os,pathlib,sys\nif sys.argv[1]=='profile': pathlib.Path(os.environ['CAPTURE_FILE']).write_text(json.dumps({'argv':sys.argv[1:],'sentinel':os.environ['COURSE_TEST_INPUT'],'remote_symbols':'DEBUGINFOD_URLS' in os.environ}))\n",
    }
    for name, body in fixtures.items():
        target = binary / name
        target.write_text(f"#!{sys.executable}\n" + body)
        target.chmod(0o700)
    lab = tmp_path / "lab with spaces.py"
    lab.write_text("raise SystemExit('The fixture must not execute a GPU workload')\n")
    literal = "literal spaces;$(no-execution)"
    arguments = [str(lab), literal]
    if course == "custom-cuda-kernels":
        arguments.insert(0, "results/report")
    environment = dict(
        os.environ,
        PATH=str(binary) + os.pathsep + os.environ["PATH"],
        SLURM_JOB_ID="123",
        COURSE_PYTHON=sys.executable,
        COURSE_CONTAINER_RUNNER=str(binary / "container"),
        CUDA_IMAGE_DIGEST="example.test/cuda@sha256:" + "a" * 64,
        CAPTURE_FILE=str(tmp_path / "capture.json"),
        COURSE_TEST_INPUT="non-secret runtime input",
        DEBUGINFOD_URLS="https://symbols.example.test",
    )
    run = subprocess.run(
        ["bash", str(ROOT / course / "slurm/nsys_single_gpu.sbatch"), *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    captured = json.loads((tmp_path / "capture.json").read_text())
    assert "--discard-environment=true" in captured["argv"]
    assert "--inherit-environment=false" not in captured["argv"]
    assert captured["argv"][-2:] == [str(lab), literal]
    assert captured["sentinel"] == environment["COURSE_TEST_INPUT"]
    assert not captured["remote_symbols"]


@pytest.mark.parametrize("capture", ["none", "systems"])
def test_dynamo_capture_preserves_worker_configuration(monkeypatch, tmp_path, capture):
    with load_lab("advanced-gpu-communication/labs/dynamo_experiments.py") as lab:
        model = tmp_path / lab.MODEL_REVISION
        model.mkdir()
        (model / "config.json").write_text("{}")
        commands = {}
        processes = SimpleNamespace(
            start=lambda name, command, **kw: commands.update({name: command})
        )
        monkeypatch.setenv("COURSE_PROFILE_TOOL", "none")
        monkeypatch.setenv("COURSE_DYNAMO_PYTHON", sys.executable)
        monkeypatch.setattr(lab, "allocated_nodes", lambda: ["worker-a", "worker-b"])
        monkeypatch.setattr(
            lab.socket,
            "gethostbyname",
            lambda node: "192.168.1.1" if node == "worker-a" else "192.168.1.2",
        )
        monkeypatch.setattr(lab, "Processes", lambda _: nullcontext(processes))
        monkeypatch.setattr(lab, "start_etcd", lambda *a: "fixture-etcd")
        monkeypatch.setattr(lab, "step", lambda node, command, **kwargs: command)
        monkeypatch.setattr(lab.shutil, "which", lambda name: "/fixture/nsys")
        monkeypatch.setattr(
            lab,
            "wait_http",
            lambda url, _, timeout=600: (
                {"status": "ready"}
                if url.endswith("/health")
                else {"data": [{"id": "course-model"}]}
            ),
        )
        monkeypatch.setattr(lab, "request", lambda *a: None)
        monkeypatch.setattr(lab, "profile_control", lambda *a: None)
        args = SimpleNamespace(
            model_dir=model,
            run_id="a" * 12,
            layout="disaggregated",
            capture=capture,
            router="round-robin",
        )
        with lab.service(args, tmp_path):
            for rank, role in enumerate(("prefill", "decode")):
                command = commands[f"worker{rank}"]
                assert "ETCD_ENDPOINTS=fixture-etcd" in command
                assert "HF_HUB_OFFLINE=1" in command
                assert command[command.index("--disaggregation-mode") + 1] == role
                assert ("--discard-environment=true" in command) == (
                    capture == "systems"
                )
                assert "--inherit-environment=false" not in command
                if capture == "systems":
                    (tmp_path / f"server-rank{rank}.nsys-rep").write_bytes(
                        b"fixture report"
                    )
                    index = command.index("/fixture/nsys")
                    assert command[index - 3 : index] == [
                        "env",
                        "-u",
                        "DEBUGINFOD_URLS",
                    ]
