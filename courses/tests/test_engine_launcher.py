"""Exercise the owned engine lifecycle with a local HTTP engine fixture."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from native_job_fixtures import prepare_job, local_commands

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "mode",
    ["success", "surviving-child", "bad-response", "startup-failure", "foreign-server"],
)
def test_openai_launcher_owns_server_and_preserves_probe(mode, tmp_path):
    course = tmp_path / "course with spaces"
    course.mkdir()
    (course / "labs").symlink_to(ROOT / "llm-inference/labs", target_is_directory=True)
    runner = tmp_path / "engine-fixture"
    runner.write_text(
        f"#!{sys.executable}\n"
        """import json, os, signal, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
base = Path(os.environ["FIXTURE_DIR"])
(base / "argv.json").write_text(json.dumps(sys.argv[1:]))
if os.environ["FIXTURE_MODE"] == "startup-failure":
    raise SystemExit(7)
assert os.environ["HF_HUB_OFFLINE"] == "1"
assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
if os.environ["FIXTURE_MODE"] == "surviving-child":
    child = os.fork()
    if child == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        (base / "child.pid").write_text(str(os.getpid()))
        time.sleep(12)
        os._exit(0)
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        self.send_response(200); self.end_headers()
        alias = "foreign" if os.environ["FIXTURE_MODE"] == "foreign-server" else "course-lab30-123456789abc"
        self.wfile.write(json.dumps({"data": [{"id": "test/model"}, {"id": alias}]}).encode())
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        (base / "request.json").write_text(json.dumps(request))
        self.send_response(200); self.end_headers()
        text = "fixture generation" if os.environ["FIXTURE_MODE"] in {"success", "surviving-child"} else ""
        self.wfile.write(json.dumps({"choices": [{"text": text}]}).encode())
def stop(*args):
    (base / "stopped").write_text("terminated")
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
port = int(sys.argv[sys.argv.index("--port") + 1])
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""
    )
    runner.chmod(0o700)
    for port in range(29000, 30000):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            break
    else:
        pytest.fail("No local port available for engine fixture")
    prepare_job(course, "llm-inference", "30_engine_profile")
    commands = local_commands(course)
    env = {
        "PATH": commands["PATH"],
        "SLURM_JOB_ID": str(port - 20000),
        "COURSE_RUN_ID": "123456789abc",
        "COURSE_WORKLOAD": "large",
        "COURSE_PYTHON": sys.executable,
        "COURSE_VLLM": str(runner),
        "FIXTURE_DIR": str(tmp_path),
        "FIXTURE_MODE": mode,
    }
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "llm-inference/slurm/30_engine_profile.sbatch"),
            "test/model",
            "b" * 40,
        ],
        cwd=course,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert argv[:2] == ["serve", "test/model"]
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    for flag in ["--revision", "--tokenizer-revision"]:
        assert argv[argv.index(flag) + 1] == "b" * 40
    outputs = list((course / "results").rglob("*.json"))
    if mode == "startup-failure":
        assert result.returncode != 0
        assert "exited before becoming ready" in result.stderr
        assert not (tmp_path / "request.json").exists()
    elif mode == "foreign-server":
        assert result.returncode != 0
        assert "does not match this run's model identity" in result.stderr
        assert (tmp_path / "stopped").is_file()
        assert not (tmp_path / "request.json").exists()
    else:
        assert (tmp_path / "stopped").read_text() == "terminated"
        request = json.loads((tmp_path / "request.json").read_text())
        assert request["model"] == "test/model"
        assert request["max_tokens"] == 16
        if mode in {"success", "surviving-child"}:
            assert result.returncode == 0, result.stderr
            assert len(outputs) == 1
            evidence = json.loads(outputs[0].read_text())
            assert evidence["run_id"] == "123456789abc"
            assert evidence["correctness"]["response_has_generated_text"] is True
            assert "fixture generation" not in outputs[0].read_text()
        else:
            assert result.returncode != 0
            assert "did not contain generated text" in result.stderr
    if mode == "surviving-child":
        child = (tmp_path / "child.pid").read_text()
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", child], capture_output=True, text=True
        ).stdout.strip()
        assert not state or state.startswith("Z"), (
            "server worker survived process-group cleanup"
        )
    if mode not in {"success", "surviving-child"}:
        assert not outputs


def test_engine_recipe_uses_owned_launcher():
    recipes = json.loads((ROOT / "skills/run-labs/references/recipes.json").read_text())
    recipe = recipes["labs"]["llm-inference:30_engine_profile"]
    variants = {variant["id"]: variant["argv"] for variant in recipe["variants"]}
    assert set(variants) == {"openai", "openai-repeat", "triton", "triton-repeat"}
    assert variants["openai"] == variants["openai-repeat"]
    assert variants["triton"] == variants["triton-repeat"]
    assert variants["openai"][1] == "slurm/30_engine_profile.sbatch"
    assert variants["triton"][1] == "slurm/30_engine_profile.trtllm.sbatch"
    assert recipe["comparisons"] == [["openai", "openai-repeat"], ["triton", "triton-repeat"]]


@pytest.mark.parametrize("mode", ["success", "bad-response", "startup-failure"])
def test_triton_launcher_initializes_mpi_and_owns_server(mode, tmp_path):
    course = tmp_path / "course with spaces"
    course.mkdir()
    (course / "labs").symlink_to(ROOT / "llm-inference/labs", target_is_directory=True)
    repository = tmp_path / "model repository" / "tensorrt_llm"
    repository.mkdir(parents=True)
    (repository / "config.pbtxt").write_text('name: "tensorrt_llm"\n')
    prepare_job(course, "llm-inference", "30_engine_profile")
    commands = local_commands(course)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    srun = bin_dir / "srun"
    srun.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "assert sys.argv[1:3] == ['--ntasks=1', '--gpus-per-task=1']\n"
        "os.execv(sys.argv[3], sys.argv[3:])\n"
    )
    srun.chmod(0o700)
    runner = tmp_path / "triton-fixture"
    runner.write_text(
        f"#!{sys.executable}\n"
        """import json, os, signal, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
base = Path(os.environ["FIXTURE_DIR"])
(base / "argv.json").write_text(json.dumps(sys.argv[1:]))
assert sys.argv[2:8] == ["mpirun", "--allow-run-as-root", "--oversubscribe", "-n", "1", "tritonserver"]
print("fixture MPI startup", flush=True)
if os.environ["FIXTURE_MODE"] == "startup-failure":
    raise SystemExit(7)
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        assert self.path in ["/v2/health/ready", "/v2/models/tensorrt_llm/ready"]
        self.send_response(200); self.end_headers()
        self.wfile.write(b"{}")
    def do_POST(self):
        assert self.path == "/v2/models/tensorrt_llm/generate"
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        (base / "request.json").write_text(json.dumps(request))
        self.send_response(200); self.end_headers()
        text = "fixture generation" if os.environ["FIXTURE_MODE"] == "success" else ""
        self.wfile.write(json.dumps({"text_output": text}).encode())
def stop(*args):
    (base / "stopped").write_text("terminated")
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
port = int(next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--http-port=")))
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""
    )
    runner.chmod(0o700)
    for port in range(28000, 29000):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            break
    else:
        pytest.fail("No local port available for Triton fixture")
    result = subprocess.run(
        ["bash", str(ROOT / "llm-inference/slurm/30_engine_profile.trtllm.sbatch")],
        cwd=course,
        env={
            "PATH": str(bin_dir) + os.pathsep + commands["PATH"],
            "SLURM_JOB_ID": str(port - 20000),
            "COURSE_RUN_ID": "123456789abc",
            "COURSE_WORKLOAD": "large",
            "COURSE_PYTHON": sys.executable,
            "COURSE_CONTAINER_RUNNER": str(runner),
            "COURSE_VLLM": str(runner),
            "TRTLLM_IMAGE_DIGEST": "docker://example.invalid/triton@sha256:" + "a" * 64,
            "MODEL_REPOSITORY": str(repository.parent),
            "TRITON_REPOSITORY_PROFILE": "llmapi",
            "TRITON_MODEL_NAME": "tensorrt_llm",
            "TRITON_MAX_TOKEN_FIELD": "sampling_param_max_tokens",
            "FIXTURE_DIR": str(tmp_path),
            "FIXTURE_MODE": mode,
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert argv[1:7] == [
        "mpirun",
        "--allow-run-as-root",
        "--oversubscribe",
        "-n",
        "1",
        "tritonserver",
    ]
    assert "--disable-auto-complete-config" in argv
    assert "--model-repository=" + str(repository.parent) in argv
    for service in ["http", "grpc", "metrics"]:
        assert "--" + service + "-address=127.0.0.1" in argv
    log = (
        course
        / f"results/30_engine_profile/jobs/{port - 20000}/logs/trtllm-triton-run-123456789abc.log"
    )
    assert "fixture MPI startup" in log.read_text()
    outputs = list((course / "results").rglob("*.json"))
    if mode == "startup-failure":
        assert result.returncode != 0
        assert "exited before readiness" in result.stderr
        assert not (tmp_path / "request.json").exists()
    else:
        assert (tmp_path / "stopped").read_text() == "terminated"
        request = json.loads((tmp_path / "request.json").read_text())
        assert request["sampling_param_max_tokens"] == 16
        if mode == "success":
            assert result.returncode == 0, result.stderr
            assert len(outputs) == 1
            evidence = json.loads(outputs[0].read_text())
            assert evidence["run_id"] == "123456789abc"
            assert evidence["correctness"]["response_has_generated_text"] is True
            assert "fixture generation" not in outputs[0].read_text()
        else:
            assert result.returncode != 0
            assert "did not contain generated text" in result.stderr
    if mode != "success":
        assert not outputs


def test_openai_launcher_rejects_occupied_port_before_startup(tmp_path):
    commands = local_commands(tmp_path)
    (tmp_path / "labs").symlink_to(
        ROOT / "llm-inference/labs", target_is_directory=True
    )
    prepare_job(tmp_path, "llm-inference", "30_engine_profile")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        if not 20000 <= port < 40000:
            # The production formula covers these ports, so select one explicitly.
            listener.close()
            listener = socket.socket()
            for port in range(30000, 40000):
                try:
                    listener.bind(("127.0.0.1", port))
                    break
                except OSError:
                    continue
            else:
                pytest.fail("No fixture port available")
        try:
            result = subprocess.run(
                ["bash", str(ROOT / "llm-inference/slurm/30_engine_profile.sbatch")],
                cwd=tmp_path,
                env={
                    "PATH": commands["PATH"],
                    "SLURM_JOB_ID": str(port - 20000),
                    "COURSE_PYTHON": sys.executable,
                    "COURSE_RUN_ID": "123456789abc",
                    "COURSE_CONTAINER_RUNNER": "/usr/bin/false",
                    "VLLM_IMAGE_DIGEST": "docker://example.invalid/vllm@sha256:"
                    + "a" * 64,
                },
                capture_output=True,
                text=True,
                timeout=5,
            )
        finally:
            listener.close()
    assert result.returncode != 0
    assert "already occupied" in result.stderr
