"""Run native rank/server launch boundaries locally, without GPU allocations."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from argparse import Namespace

import pytest
from native_job_fixtures import (
    ROOT,
    executable,
    local_commands,
    prepare_job,
    worker_prefix,
)


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
    prefix = worker_prefix(lab)
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
    "server,ambient,capture",
    [(False, "nsys", "none"), (True, "ncu", "systems"), (True, "none", "none")],
)
def test_explicit_worker_prefix_rejects_conflicting_modes(
    monkeypatch, server, ambient, capture
):
    monkeypatch.setenv("COURSE_PROFILE_TOOL", ambient)
    with pytest.raises(ValueError):
        vendor_capture().validate_worker_prefix(
            Namespace(
                worker_prefix=worker_prefix(
                    "32_dynamo_disaggregation" if server else "29_nixl_transfer"
                ),
                capture=capture,
            ),
            server=server,
        )


@pytest.mark.parametrize(
    "lab,ranks,reports",
    [
        ("02_collective_readiness", 1, 2),
        ("01_fabric_topology", 8, 16),
        ("14_distributed_profiling", 8, 16),
        ("01_fabric_topology", 8, 1),
    ],
)
def test_native_torchrun_places_profiler_at_each_rank_and_requires_all_reports(
    tmp_path, monkeypatch, lab, ranks, reports
):
    prepare_job(tmp_path, "advanced-gpu-communication", lab)
    env = {**os.environ, **local_commands(tmp_path)}
    executable(tmp_path / "bin/scontrol", 'print("node-a\\nnode-b")\n')
    executable(
        tmp_path / "bin/torchrun",
        """import json,os,sys
from pathlib import Path
Path('ranks.json').write_text(json.dumps(sys.argv[1:]))
for i in range(int(os.environ['REPORTS'])): (Path(os.environ['COURSE_PROFILES_DIR'])/f'rank-{i}.nsys-rep').write_text('report')
""",
    )
    env.update(
        SLURM_JOB_NODELIST="node-[a-b]",
        SLURM_NODEID="1",
        SLURM_JOB_NUM_NODES="2",
        COURSE_RUN_ID="a" * 12,
        REPORTS=str(reports),
    )
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "advanced-gpu-communication/slurm" / f"{lab}.nsys.sbatch"),
            "--workload",
            "large",
            "--seed",
            "7",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == (0 if reports == ranks * 2 else 2), result.stderr
    argv = json.loads((tmp_path / "ranks.json").read_text())
    assert f"--nproc-per-node={ranks}" in argv and "--node-rank=1" in argv
    assert argv[argv.index("--no-python") + 1 :][:5] == [
        "env",
        "-u",
        "DEBUGINFOD_URLS",
        "timeout",
        "--signal=TERM",
    ]
    assert "nsys" in argv and f"labs/{lab}.py" in argv
    if lab == "14_distributed_profiling":
        from test_course_review_fixes import load_lab

        class Parsed(Exception):
            pass

        def stop_before_gpu(args):
            assert args.seed == 7 and args.workload == "large"
            raise Parsed

        monkeypatch.setenv("COURSE_WORKLOAD", "large")
        monkeypatch.setenv("COURSE_PROFILE_TOOL", "nsys")
        arguments = argv[argv.index(f"labs/{lab}.py"):]
        monkeypatch.setattr("sys.argv", arguments)
        with load_lab(f"advanced-gpu-communication/labs/{lab}.py") as module:
            monkeypatch.setattr(module, "validate_common_args", stop_before_gpu)
            with pytest.raises(Parsed):
                module.main()
    assert argv[-2:] == ["--seed", "7"]


@pytest.mark.parametrize(
    "failure,expected",
    [("", 0), ("client", 7), ("control", 2), ("server", 2), ("missing-report", 2)],
)
def test_native_server_waits_for_delayed_export_and_preserves_failures(
    tmp_path, failure, expected
):
    lab = "11_serving_client"
    prepare_job(tmp_path, "llm-inference", lab)
    env = {**os.environ, **local_commands(tmp_path)}
    executable(
        tmp_path / "bin/setsid",
        "import os,sys\nos.setsid();os.execvpe(sys.argv[1],sys.argv[1:],os.environ)\n",
    )
    executable(
        tmp_path / "bin/runner",
        "import os,sys\nos.execvpe(sys.argv[2],sys.argv[2:],os.environ)\n",
    )
    executable(
        tmp_path / "bin/nsys",
        r"""import json,os,re,signal,sys,time
from pathlib import Path
args=sys.argv[1:];Path('server-argv.json').write_text(json.dumps(args))
prefix=args[args.index('--output')+1]
prefix=re.sub(r'%q\{([^}]+)\}',lambda m:os.environ[m[1]],prefix).replace('%p',str(os.getpid()))
def stop(*_):
    time.sleep(.25)
    if os.environ['FAILURE']!='missing-report':Path(prefix+'.nsys-rep').write_text('finished export')
    Path('server-stopped').write_text('yes')
    raise SystemExit(7 if os.environ['FAILURE']=='server' else 0)
signal.signal(signal.SIGINT,stop)
Path('server-ready').write_text(str(os.getpid()))
while True:time.sleep(.01)
""",
    )
    executable(
        tmp_path / "bin/curl",
        """import os,sys
from pathlib import Path
with Path('controls').open('a') as f:f.write(sys.argv[-1].rsplit('/',1)[1]+'\\n')
print('500' if os.environ['FAILURE']=='control' else '200',end='')
""",
    )
    executable(
        tmp_path / "bin/client",
        """import json,os,sys
from pathlib import Path
if sys.argv[1]=='-c':
    if '/health' in sys.argv[2]:raise SystemExit(0 if Path('server-ready').exists() else 1)
    print('synthetic_metric 1');raise SystemExit(0)
Path('client-argv.json').write_text(json.dumps(sys.argv[1:]))
assert os.environ['COURSE_CAPTURE']=='1'
raise SystemExit(7 if os.environ['FAILURE']=='client' else 0)
""",
    )
    env.update(
        COURSE_PYTHON=str(tmp_path / "bin/client"),
        COURSE_CONTAINER_RUNNER=str(tmp_path / "bin/runner"),
        VLLM_IMAGE_DIGEST="example.test/image@sha256:" + "a" * 64,
        COURSE_RUN_ID="a" * 12,
        FAILURE=failure,
    )
    result = subprocess.run(
        ["bash", str(ROOT / "llm-inference/slurm" / f"{lab}.nsys.sbatch")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == expected, result.stderr
    assert (tmp_path / "server-stopped").exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "server-ready").read_text()), 0)
    if not failure:
        argv = json.loads((tmp_path / "server-argv.json").read_text())
        assert "--capture-range=cudaProfilerApi" in argv
        assert "--profiler-config.profiler" in argv
        assert argv[argv.index("--host") + 1] == "127.0.0.1"
        assert (tmp_path / "controls").read_text().splitlines() == [
            "start_profile",
            "stop_profile",
        ]


def test_native_nccl_ranks_select_their_only_visible_gpu():
    command = (
        ROOT / "advanced-gpu-communication/slurm/10_nccl_tests_report.nsys.sbatch"
    ).read_text()
    assert "--worker-prefix env -u DEBUGINFOD_URLS timeout" in command
    from test_course_review_fixes import load_lab

    with load_lab("advanced-gpu-communication/labs/10_nccl_tests_report.py") as lab:
        argv = lab.launch_command(
            Path("/qualified/all_reduce_perf"), "pmix", [8, 16], 1, 2, 2
        )
    assert "--ntasks=16" in argv and "--gpus-per-task=1" in argv and "-T" in argv
    source = (
        ROOT / "advanced-gpu-communication/labs/10_nccl_tests_report.py"
    ).read_text()
    assert 'env["NCCL_TESTS_DEVICE"] = "0"' in source
