"""Exercise the chunked-prefill launcher boundary without starting a GPU server."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from native_job_fixtures import prepare_job, local_commands

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mismatch", [False, True])
@pytest.mark.parametrize(
    "startup_delay", [0, 0.2], ids=["normal-start", "delayed-start"]
)
def test_chunked_execution_is_fixed_and_quality_gates_measurement(
    tmp_path, mismatch, startup_delay
):
    course = tmp_path / "course with spaces"
    (course / "labs").mkdir(parents=True)
    (course / "labs/34_policy_equivalence_client.py").touch()
    prepare_job(course, "llm-inference", "34_policy_equivalence_client")
    commands = local_commands(course)
    record = course / "server-arguments.jsonl"
    clients = course / "client-arguments.jsonl"
    runner = course / "container-runner"
    runner.write_text(
        f"#!{sys.executable}\n"
        "import json,os,signal,sys,time\n"
        "if os.path.basename(sys.argv[0]) == 'aiperf': sys.argv.insert(1, 'aiperf')\n"
        "else: assert os.environ['VLLM_BATCH_INVARIANT'] == '1'\n"
        "if 'aiperf' not in sys.argv: time.sleep(float(os.environ['SERVER_STARTUP_DELAY']))\n"
        "with open(os.environ['SERVER_ARGUMENTS'], 'a') as f:\n"
        " f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "if 'aiperf' in sys.argv: sys.exit(0)\n"
        "signal.signal(signal.SIGTERM,lambda *a: sys.exit(0))\n"
        "os.write(int(os.environ['SERVER_READY_WRITE_FD']),b'1')\n"
        "while True: time.sleep(1)\n"
    )
    python = course / "client-python"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json,os,select,sys\n"
        "from pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "if args[0]=='-c':\n"
        " if 'urllib.request' in args[1]:\n"
        "  fd=int(os.environ['SERVER_READY_READ_FD'])\n"
        "  readable,_,_=select.select([fd],[],[],2)\n"
        "  sys.exit(0 if readable and os.read(fd,1)==b'1' else 1)\n"
        " else:\n"
        "  sys.argv=['-c',*args[2:]]\n"
        "  exec(compile(args[1],'<launcher-check>','exec'))\n"
        "elif args[0].endswith('server_capture.py'): pass\n"
        "else:\n"
        " with open(os.environ['CLIENT_ARGUMENTS'],'a') as f:\n"
        "  f.write(json.dumps(args)+'\\n')\n"
        " variant=args[args.index('--variant')+1]\n"
        " changed=os.environ['MISMATCH']=='1' and variant=='enabled'\n"
        " output=Path(args[args.index('--output')+1])\n"
        " output.write_text(json.dumps({'measurements':{'response_digests':"
        "['b' if changed else 'a']*4}}))\n"
    )
    for path in (runner, python):
        path.chmod(0o755)
    (course / "aiperf").symlink_to(runner)
    environment = {
        **os.environ,
        "PATH": commands["PATH"],
        "SLURM_JOB_ID": "41",
        "COURSE_RUN_ID": "0123456789ab",
        "COURSE_PYTHON": str(python),
        "COURSE_VLLM": str(runner),
        "COURSE_AIPERF": str(course / "aiperf"),
        "SERVER_ARGUMENTS": str(record),
        "CLIENT_ARGUMENTS": str(clients),
        "MISMATCH": "1" if mismatch else "0",
        "SERVER_STARTUP_DELAY": str(startup_delay),
    }
    environment.pop("COURSE_PROFILE_TOOL", None)
    # Consume one startup signal per server; elapsed time cannot prove readiness.
    ready_read, ready_write = os.pipe()
    environment["SERVER_READY_READ_FD"] = str(ready_read)
    environment["SERVER_READY_WRITE_FD"] = str(ready_write)
    try:
        completed = subprocess.run(
            [
                "bash",
                str(ROOT / "llm-inference/slurm/34_policy_equivalence_client.sbatch"),
            ],
            cwd=course,
            env=environment,
            pass_fds=(ready_read, ready_write),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    finally:
        os.close(ready_read)
        os.close(ready_write)
    assert completed.returncode == (1 if mismatch else 0), completed.stderr
    commands = [json.loads(line) for line in record.read_text().splitlines()]
    servers = [x for x in commands if "aiperf" not in x]
    measured = [x for x in commands if "aiperf" in x]
    calls = [json.loads(line) for line in clients.read_text().splitlines()]
    assert len(servers) == (2 if mismatch else 12)
    assert len(calls) == (2 if mismatch else 6)
    for command in servers:
        assert command[0] == "serve"
        offset = command.index("--attention-config")
        assert json.loads(command[offset + 1]) == {"backend": "TRITON_ATTN"}
        assert command[command.index("--dtype") + 1] == "bfloat16"
        assert "--enforce-eager" in command
    policies = [
        "enabled" if "--enable-chunked-prefill" in command else "disabled"
        for command in servers
    ]
    assert policies == (
        ["disabled", "enabled"]
        if mismatch
        else ["disabled", "enabled", "enabled", "disabled", "disabled", "enabled"] * 2
    )
    assert len(measured) == (0 if mismatch else 6)
    if mismatch:
        assert "chunked-prefill policies changed greedy outputs" in completed.stderr
