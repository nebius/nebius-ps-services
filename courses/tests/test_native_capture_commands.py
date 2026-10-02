"""Run learner argv against process spies; never allocate GPUs or contact services."""

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def recipes(course):
    return json.loads((ROOT / course / "reference/observability.json").read_text())[
        "labs"
    ]


def executable(path, body):
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)
    return str(path)


@pytest.mark.parametrize("kind,tool", [("systems", "nsys"), ("compute", "ncu")])
def test_native_capture_uses_worker_identity_and_marks_diagnostic(tmp_path, kind, tool):
    command = recipes("gpu-fundamentals")["01_cpu_gpu_crossover"][
        f"learner_{kind}_command"
    ]
    executable(
        tmp_path / "srun",
        "import os,sys\n"
        "args=sys.argv[1:]\n"
        'i=args.index("env")\n'
        'os.environ.update(SLURM_JOB_ID="824", SLURM_STEP_ID="0", SLURM_PROCID="0")\n'
        "os.execvpe(args[i],args[i:],os.environ)\n",
    )
    executable(
        tmp_path / tool,
        "import os,sys,json\nfrom pathlib import Path\n"
        'Path("capture.json").write_text(json.dumps({"argv":sys.argv[1:],'
        '"capture":os.environ["COURSE_CAPTURE"],"tool":os.environ["COURSE_PROFILE_TOOL"],'
        '"debug":os.environ.get("DEBUGINFOD_URLS"),"job":os.environ["SLURM_JOB_ID"]}))\n',
    )
    result = subprocess.run(
        check=False,
        args=["bash", "-c", command],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.defpath,
            "COURSE_PYTHON": "/qualified/python",
            "COURSE_PROFILE_KERNEL": "measured_kernel",
            "COURSE_CAPTURE": "0",
            "COURSE_PROFILE_TOOL": "none",
            "DEBUGINFOD_URLS": "https://example.test/symbols",
        },
    )
    assert result.returncode == 0, result.stderr
    captured = json.loads((tmp_path / "capture.json").read_text())
    assert captured["capture"] == "1" and captured["tool"] == tool
    assert captured["job"] == "824" and captured["debug"] is None
    assert captured["argv"][-4:] == [
        "/qualified/python",
        "labs/01_cpu_gpu_crossover.py",
        "--profile",
        "small",
    ]
    prefix = captured["argv"][
        captured["argv"].index("--output" if tool == "nsys" else "--export") + 1
    ]
    assert (
        "%q{SLURM_JOB_ID}" in prefix
        and "%q{SLURM_STEP_ID}" in prefix
        and "%p" in prefix
    )
    assert not any("profile_lab.py" in arg for arg in captured["argv"])
    if tool == "ncu":
        assert (
            captured["argv"][captured["argv"].index("--kernel-name") + 1]
            == "regex:measured_kernel"
        )


def test_cpu_submission_has_no_gpu_and_rejects_capture(tmp_path):
    launcher = ROOT / "llm-inference/slurm/cpu.sbatch"
    assert not any(
        "gpu" in line
        for line in launcher.read_text().splitlines()
        if line.startswith("#SBATCH")
    )
    (tmp_path / "model.py").write_text("")
    executable(
        tmp_path / "srun",
        'import sys,json\nfrom pathlib import Path\nPath("argv.json").write_text(json.dumps(sys.argv[1:]))\n',
    )
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.defpath,
        "COURSE_PYTHON": sys.executable,
        "SLURM_JOB_ID": "123",
        "COURSE_CAPTURE": "0",
        "COURSE_PROFILE_TOOL": "none",
    }
    result = subprocess.run(
        check=False,
        args=["bash", str(launcher), "model.py", "--cache-bytes", "256"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
    )
    assert result.returncode == 0
    assert json.loads((tmp_path / "argv.json").read_text()) == [
        "--ntasks=1",
        sys.executable,
        "model.py",
        "--cache-bytes",
        "256",
    ]
    (tmp_path / "argv.json").unlink()
    result = subprocess.run(
        check=False,
        args=["bash", str(launcher), "model.py"],
        cwd=tmp_path,
        env={**env, "COURSE_PROFILE_TOOL": "nsys"},
        capture_output=True,
    )
    assert result.returncode == 2
    assert not (tmp_path / "argv.json").exists()


@pytest.mark.parametrize("ranks", ["1", "8"])
def test_rank_launcher_uses_allocated_node_rank_and_native_argv(tmp_path, ranks):
    executable(tmp_path / "scontrol", 'print("node-a\\nnode-b")\n')
    python = executable(
        tmp_path / "qualified-python",
        'import sys\nassert sys.argv[1:] == ["tools/fabric_guard.py"]\n',
    )
    torchrun = executable(
        tmp_path / "torchrun",
        "import sys,os,json\nfrom pathlib import Path\n"
        'Path("ranks.json").write_text(json.dumps({"argv":sys.argv[1:],"run":os.environ["COURSE_RUN_ID"],'
        '"world":os.environ["COURSE_WORLD_SIZE"]}))\n',
    )
    result = subprocess.run(
        check=False,
        args=[
            "bash",
            str(ROOT / "advanced-gpu-communication/slurm/capture_ranks.sh"),
            ranks,
            "env",
            "COURSE_CAPTURE=1",
            "nsys",
            "profile",
            "python",
            "lab.py",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "COURSE_PYTHON": python,
            "COURSE_TORCHRUN": torchrun,
            "SLURM_JOB_ID": "123",
            "SLURM_NODEID": "1",
            "SLURM_NTASKS": "2",
            "SLURM_STEP_ID": "2",
            "SLURM_JOB_NODELIST": "node-[a-b]",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    row = json.loads((tmp_path / "ranks.json").read_text())
    assert "--node-rank=1" in row["argv"] and "--master-addr=node-a" in row["argv"]
    assert f"--nproc-per-node={ranks}" in row["argv"] and row["world"] == str(
        2 * int(ranks)
    )
    assert row["argv"][-6:] == [
        "env",
        "COURSE_CAPTURE=1",
        "nsys",
        "profile",
        "python",
        "lab.py",
    ]
    assert row["run"] == "0000007b0002"


def vendor_capture():
    spec = importlib.util.spec_from_file_location(
        "native_vendor", ROOT / "advanced-gpu-communication/labs/vendor_capture.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "lab",
    [
        "29_nixl_transfer",
        "32_dynamo_disaggregation",
        "33_dynamo_routing",
        "34_serving_goodput",
    ],
)
def test_native_coordinator_prefix_targets_private_rank_report(tmp_path, lab):
    command = recipes("advanced-gpu-communication")[lab]["learner_systems_command"]
    prefix = shlex.split(command.replace("\\\n", " ").split("--worker-prefix ", 1)[1])
    capture = vendor_capture()
    server = not lab.startswith("29")
    report = tmp_path / "private rank report"
    argv = capture.native_systems_prefix(prefix, report, server=server, ucx=not server)
    assert argv[-1] == f"--output={report}"
    for defect in [
        "--force-overwrite=true",
        "--trace=cuda",
        "--output=/tmp/report",
        "--sample=cpu",
    ]:
        with pytest.raises(ValueError):
            capture.native_systems_prefix(
                [*prefix, defect], report, server=server, ucx=not server
            )


@pytest.mark.parametrize(
    "lab,failure",
    [
        ("11_serving_client", ""),
        ("15_streaming_client", ""),
        ("20_prefix_cache_client", ""),
        ("33_speculative_engine_client", ""),
        ("34_policy_equivalence_client", ""),
        ("11_serving_client", "client"),
        ("11_serving_client", "control"),
        ("11_serving_client", "server"),
        ("11_serving_client", "metrics-link"),
        ("11_serving_client", "missing-report"),
    ],
)
def test_server_native_argv_controls_cleanup_and_failures(tmp_path, lab, failure):
    import shutil

    (tmp_path / "slurm").mkdir()
    shutil.copy2(
        ROOT / "llm-inference/slurm/capture_server.sh",
        tmp_path / "slurm/capture_server.sh",
    )
    for leaf in ["logs", "profiles"]:
        (tmp_path / "results" / lab / leaf).mkdir(parents=True, mode=0o700)
    executable(
        tmp_path / "srun",
        'import sys,os\na=sys.argv[1:]\ni=a.index("bash")\nos.execvpe(a[i],a[i:],os.environ)\n',
    )
    executable(
        tmp_path / "setsid",
        "import sys,os\nos.setsid()\nos.execvpe(sys.argv[1],sys.argv[1:],os.environ)\n",
    )
    executable(
        tmp_path / "timeout",
        "import sys,os\na=sys.argv[4:]\nos.execvpe(a[0],a,os.environ)\n",
    )
    runner = executable(
        tmp_path / "runner",
        "import sys,os\na=sys.argv[2:]\nos.execvpe(a[0],a,os.environ)\n",
    )
    executable(
        tmp_path / "nsys",
        """import json,os,signal,sys,time
from pathlib import Path
args=sys.argv[1:]
prefix=args[args.index('--output')+1].replace('%q{COURSE_CAPTURE_ID}',os.environ['COURSE_CAPTURE_ID'])
Path('server-argv.json').write_text(json.dumps(args))
def stop(*_):
    if os.environ['FAILURE'] != 'missing-report':
        Path(prefix+'.nsys-rep').write_text('fixture report')
    Path('server-stopped').write_text('yes')
    sys.exit(7 if os.environ['FAILURE']=='server' else 0)
signal.signal(signal.SIGINT,stop)
Path('server-ready').write_text(str(os.getpid()))
while True: time.sleep(.01)
""",
    )
    executable(
        tmp_path / "curl",
        """import os,sys
from pathlib import Path
url=sys.argv[-1]
if url.endswith('/health'):
    sys.exit(0 if Path('server-ready').exists() else 1)
if url.endswith('_profile'):
    with Path('controls').open('a') as f:f.write(url.rsplit('/',1)[1]+'\\n')
    print('500' if os.environ['FAILURE']=='control' and url.endswith('/start_profile') else '200',end='')
else: print('synthetic_metric 1')
""",
    )
    client = executable(
        tmp_path / "client",
        """import os,sys,json
from pathlib import Path
Path('client-argv.json').write_text(json.dumps(sys.argv[1:]))
assert os.environ['COURSE_CAPTURE']=='1'
if os.environ['FAILURE']=='metrics-link':
    target=Path('untouched');target.write_text('keep')
    Path('results/'+os.environ['LAB']+'/profiles/metrics-'+os.environ['COURSE_CAPTURE_ID']+'.prom').symlink_to(target.resolve())
sys.exit(7 if os.environ['FAILURE']=='client' else 0)
""",
    )
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "COURSE_PYTHON": client,
        "COURSE_CONTAINER_RUNNER": runner,
        "VLLM_IMAGE_DIGEST": "example.test/image@sha256:" + "a" * 64,
        "SLURM_JOB_ID": "127",
        "SLURM_STEP_ID": "0",
        "FAILURE": failure,
        "LAB": lab,
        "CLUSTER_TARGET_MODEL": "public/example",
        "CLUSTER_TARGET_REVISION": "a" * 40,
        "COURSE_DRAFT_MODEL": "public/draft",
        "COURSE_DRAFT_REVISION": "b" * 40,
    }
    result = subprocess.run(
        check=False,
        args=["bash", "-c", recipes("llm-inference")[lab]["learner_systems_command"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == (
        0
        if not failure
        else 7
        if failure == "client"
        else 1
        if failure == "metrics-link"
        else 2
    ), result.stderr
    assert (tmp_path / "server-stopped").exists()
    controls = (tmp_path / "controls").read_text().splitlines()
    assert controls[0] == "start_profile" and controls[-1] == "stop_profile"
    pid = int((tmp_path / "server-ready").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    if failure == "metrics-link":
        assert (tmp_path / "untouched").read_text() == "keep"
    if not failure:
        client_argv = json.loads((tmp_path / "client-argv.json").read_text())
        server_argv = json.loads((tmp_path / "server-argv.json").read_text())
        assert not any(
            arg in {"@URL@", "@PORT@", "@OUTPUT@"} for arg in client_argv + server_argv
        )
        assert "http://127.0.0.1:20127" in client_argv
        assert server_argv[server_argv.index("--host") + 1] == "127.0.0.1"
        assert server_argv[server_argv.index("--port") + 1] == "20127"


@pytest.mark.parametrize(
    "server,ambient,capture",
    [(False, "nsys", "none"), (True, "ncu", "systems"), (True, "none", "none")],
)
def test_explicit_worker_prefix_rejects_conflicting_modes(
    monkeypatch, server, ambient, capture
):
    from argparse import Namespace

    lab = "32_dynamo_disaggregation" if server else "29_nixl_transfer"
    command = recipes("advanced-gpu-communication")[lab]["learner_systems_command"]
    prefix = shlex.split(command.replace("\\\n", " ").split("--worker-prefix ", 1)[1])
    monkeypatch.setenv("COURSE_PROFILE_TOOL", ambient)
    with pytest.raises(ValueError):
        vendor_capture().validate_worker_prefix(
            Namespace(worker_prefix=prefix, capture=capture), server=server
        )


def test_native_nccl_ranks_select_their_only_visible_gpu():
    command = recipes("advanced-gpu-communication")["10_nccl_tests_report"][
        "learner_systems_command"
    ]
    assert "--ntasks=16" in command and "--gpus-per-task=1" in command
    assert "NCCL_TESTS_DEVICE=0" in command and "-T 120" in command
