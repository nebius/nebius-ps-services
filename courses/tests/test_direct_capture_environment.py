"""Direct launchers retain runtime inputs without collecting environment metadata."""

import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from native_job_fixtures import worker_prefix
from test_course_review_fixes import load_lab




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
            worker_prefix=worker_prefix() if capture == "systems" else None,
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
                    index = command.index("nsys")
                    assert command[index - 3 : index] == [
                        "env",
                        "-u",
                        "DEBUGINFOD_URLS",
                    ]
