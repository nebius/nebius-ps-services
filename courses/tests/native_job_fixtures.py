"""Local scheduler/profiler doubles for the actual Linux batch jobs."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def prepare_job(root, course, lab):
    for relative in (
        "results",
        f"results/{lab}",
        f"results/{lab}/logs",
        f"results/{lab}/jobs",
    ):
        (root / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
    (root / "labs").mkdir(exist_ok=True)
    source = root / "labs" / (lab + ".py")
    if not source.exists():
        source.touch()
    return ROOT / course / "slurm"


def executable(path, code):
    path.write_text(f"#!{sys.executable}\n" + code)
    path.chmod(0o700)


def local_commands(root):
    binaries = root / "bin"
    binaries.mkdir(exist_ok=True)
    for command in ("stat", "timeout", "bash"):
        real = shutil.which("g" + command) or shutil.which(command)
        (binaries / command).symlink_to(real)
    executable(
        binaries / "srun",
        """import json,os,sys
from pathlib import Path
args=sys.argv[1:]
Path('srun.json').write_text(json.dumps(args))
while args and args[0].startswith('--'): args.pop(0)
os.execvpe(args[0],args,os.environ)
""",
    )
    executable(
        binaries / "python-spy",
        """import json,os,sys
from pathlib import Path
Path('application.json').write_text(json.dumps({'argv':sys.argv[1:], 'capture':os.environ.get('COURSE_CAPTURE'), 'tool':os.environ.get('COURSE_PROFILE_TOOL'), 'workload':os.environ.get('COURSE_WORKLOAD'), 'results':os.environ.get('COURSE_RESULTS_DIR')}))
raise SystemExit(int(os.environ.get('WORKLOAD_STATUS','0')))
""",
    )
    for tool in ("nsys", "ncu"):
        executable(
            binaries / tool,
            r"""import json,os,re,subprocess,sys
from pathlib import Path
args=sys.argv[1:]
Path('profiler.json').write_text(json.dumps(args))
tool=Path(sys.argv[0]).name
key='--output' if tool=='nsys' else '--export'
index=args.index(key)
report=re.sub(r'%q\{([^}]+)\}',lambda m:os.environ.get(m[1],''),args[index+1]).replace('%p',str(os.getpid()))
start=next(i for i,a in enumerate(args) if a.endswith('python-spy'))
status=subprocess.call(args[start:])
mode=os.environ.get('REPORT_MODE','present')
if mode!='missing': Path(report+('.nsys-rep' if tool=='nsys' else '.ncu-rep')).write_text('native report fixture' if mode=='present' else '')
raise SystemExit(status or int(os.environ.get('PROFILER_STATUS','0')))
""",
        )
    return {
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "COURSE_PYTHON": str(binaries / "python-spy"),
        "COURSE_PROFILE_KERNEL": "measured_kernel",
        "SLURM_JOB_ID": "123",
        "SLURM_JOB_NUM_NODES": "1",
    }


def run_job(root, course, lab, mode="", arguments=(), environment=None):
    prepare_job(root, course, lab)
    env = {**os.environ, **local_commands(root), **(environment or {})}
    job = ROOT / course / "slurm" / (lab + ("." + mode if mode else "") + ".sbatch")
    return subprocess.run(
        ["bash", str(job), *arguments],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def worker_prefix(lab="32_dynamo_disaggregation"):
    """Extract the literal native prefix students can inspect in their job."""
    import shlex

    source = (
        ROOT / "advanced-gpu-communication/slurm" / (lab + ".nsys.sbatch")
    ).read_text()
    tail = source.split("--worker-prefix ", 1)[1].split("\n\n", 1)[0]
    return shlex.split(tail.replace("\\\n", " "))
