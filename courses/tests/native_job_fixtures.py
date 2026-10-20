"""Local scheduler/profiler doubles for the actual Linux batch jobs."""

import os
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def prepare_job(root, course, lab):
    # These tests isolate scheduler/profiler lifecycle. Runtime activation has its
    # own real-record tests in test_course_bootstrap; this explicit double leaves
    # the test-selected executable and container fixture in place.
    (root / "tools").mkdir(exist_ok=True)
    shutil.copyfile(
        ROOT / "tools/course_job_cache.sh", root / "tools/course_job_cache.sh"
    )
    for name in (
        "course_job.py",
        "course_viewer.py",
        "course_execution.py",
        "course_publication.py",
        "course_publication_state.py",
        "course_publication_evidence.py",
    ):
        shutil.copyfile(ROOT / "tools" / name, root / "tools" / name)
    viewer_fixture(root)
    (root / "reference").mkdir(exist_ok=True)
    capabilities = json.loads((ROOT / course / "reference/execution.json").read_text())
    capabilities["course"] = root.name
    (root / "reference/execution.json").write_text(json.dumps(capabilities))
    (root / "tools/course_env.sh").write_text(
        ": # isolated runtime selection fixture\n"
    )
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


def viewer_fixture(root):
    """Explicit synthetic installation; never write host /var/lib or fake live proof."""
    root = root.resolve()
    reports = root / "viewer-reports"
    reports.mkdir(exist_ok=True)
    reports.chmod(0o1777)
    metadata = reports / ".cxcli-nsight"
    metadata.mkdir(exist_ok=True)
    metadata.chmod(0o755)
    installed = root / "viewer-installation"
    installed.mkdir(exist_ok=True)
    installed.chmod(0o755)
    descriptor = installed / "nsight-reports.json"
    value = {
        "schema": "nebius-cxcli.nsight-reports/v1",
        "installation": {
            "generation": "fixture",
            "target": "fixture",
            "kubernetes_uid": "fixture",
        },
        "storage": {
            key: "fixture"
            for key in ("namespace", "pvcName", "pvcUid", "pvName", "pvUid")
        },
        "producer_path": str(reports),
        "viewer_path": "/mnt/reports",
        "mount_point": str(root),
        "filesystem_type": "fixture",
        "token": "1" * 64,
    }
    for path in (descriptor, metadata / "binding.json"):
        path.write_text(json.dumps(value))
        path.chmod(0o644)
    admission = metadata / "admission.json"
    admission.write_text(
        json.dumps(
            {
                "schema": value["schema"] + "/admission",
                "token": value["token"],
                "open": True,
            }
        )
    )
    admission.chmod(0o644)
    source = (ROOT / "tools/course_viewer.py").read_text()
    source = source.replace(
        'DESCRIPTOR = "/var/lib/nebius-cxcli/nsight-reports.json"',
        f"DESCRIPTOR = {str(descriptor)!r}",
    ).replace("ROOT_UID = 0", f"ROOT_UID = {os.getuid()}")
    fixture = f"""\ndef mount_identity(fd):
    return {{"id": "fixture", "device": str(os.fstat(fd).st_dev), "point": {str(root)!r}, "type": "fixture"}}
"""
    if sys.platform != "linux":
        fixture += """\ndef rename_noreplace(source_parent, source, destination_parent, destination):
    # Single-process orchestration double only. Linux tests use real renameat2.
    try:
        os.stat(destination, dir_fd=destination_parent, follow_symlinks=False)
    except FileNotFoundError:
        os.rename(source, destination, src_dir_fd=source_parent, dst_dir_fd=destination_parent)
    else:
        raise FileExistsError(destination)
"""
    source = source.replace(
        'if __name__ == "__main__":', fixture + '\nif __name__ == "__main__":'
    )
    (root / "tools/course_viewer.py").write_text(source)
    return descriptor


def executable(path, code):
    path.write_text(f"#!{sys.executable}\n" + code)
    path.chmod(0o700)


def local_commands(root):
    binaries = root / "bin"
    binaries.mkdir(exist_ok=True)
    executable(
        binaries / "scontrol",
        "print('JobId=123 TimeLimit=2-00:00:00')\n",
    )
    executable(
        binaries / "setsid",
        "import os, sys; os.setsid(); os.execvpe(sys.argv[1], sys.argv[1:], os.environ)\n",
    )
    for command in ("stat", "timeout", "bash"):
        real = shutil.which("g" + command) or shutil.which(command)
        (binaries / command).symlink_to(real)
    executable(
        binaries / "srun",
        """import json,os,sys
from pathlib import Path
args=sys.argv[1:]
Path('srun.json').write_text(json.dumps(args))
with Path('srun-history.jsonl').open('a') as output: output.write(json.dumps(args)+'\\n')
while args and args[0].startswith('--'): args.pop(0)
os.execvpe(args[0],args,os.environ)
""",
    )
    executable(
        binaries / "python-spy",
        """import json,os,sys
from pathlib import Path
Path('application.json').write_text(json.dumps({'argv':sys.argv[1:], 'capture':os.environ.get('COURSE_CAPTURE'), 'tool':os.environ.get('COURSE_PROFILE_TOOL'), 'workload':os.environ.get('COURSE_WORKLOAD'), 'results':os.environ.get('COURSE_RESULTS_DIR')}))
with Path('application-history.jsonl').open('a') as output: output.write(json.dumps({'argv':sys.argv[1:], 'tool':os.environ.get('COURSE_PROFILE_TOOL')})+'\\n')
Path(os.environ['COURSE_RESULTS_DIR'],'measurement.json').write_text(json.dumps({'schema':'gpu-course-result/v1','measurements':{'observed_value':17},'correctness':{'passed':True}}))
raise SystemExit(int(os.environ.get('WORKLOAD_STATUS','0')))
""",
    )
    for tool in ("nsys", "ncu"):
        executable(
            binaries / tool,
            r"""import argparse,json,os,re,subprocess,sys
from pathlib import Path
args=sys.argv[1:]
Path('profiler.json').write_text(json.dumps(args))
tool=Path(sys.argv[0]).name
key='--output' if tool=='nsys' else '--export'
index=args.index(key)
report=re.sub(r'%q\{([^}]+)\}',lambda m:os.environ.get(m[1],''),args[index+1]).replace('%p',str(os.getpid()))
if tool=='ncu':
    # NCU's overwrite option is a switch, unlike Nsight Systems' boolean.
    # Stop parsing at the application, as the real CLI does; a stray "false"
    # must execute false rather than silently skipping ahead to python-spy.
    parser=argparse.ArgumentParser(allow_abbrev=False)
    for option in ('--target-processes','--nvtx-include','--kernel-name-base',
                   '--rename-kernels','--kernel-name','--launch-count','--set',
                   '--section','--clock-control','--export','--config-file',
                   '--nvtx-push-pop-scope','--replay-mode','--kill'):
        parser.add_argument(option)
    parser.add_argument('--nvtx',action='store_true')
    parser.add_argument('--force-overwrite',action='store_true')
    parser.add_argument('application',nargs=argparse.REMAINDER)
    application=parser.parse_args(args).application
else:
    start=next(i for i,a in enumerate(args) if a.endswith('python-spy'))
    application=args[start:]
status=subprocess.call(application)
mode=os.environ.get('REPORT_MODE','present')
if mode!='missing': Path(report+('.nsys-rep' if tool=='nsys' else '.ncu-rep')).write_text('native report fixture' if mode=='present' else '')
raise SystemExit(status or int(os.environ.get('PROFILER_STATUS','0')))
""",
        )
    return {
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "COURSE_PYTHON": str(binaries / "python-spy"),
        "COURSE_TORCHRUN": str(binaries / "torchrun"),
        "COURSE_PROFILE_KERNEL": "measured_kernel",
        "SLURM_JOB_ID": "123",
        "SLURM_JOB_NUM_NODES": "1",
    }


def run_job(root, course, lab, mode="", arguments=(), environment=None):
    prepare_job(root, course, lab)
    env = {**os.environ, **local_commands(root), **(environment or {})}
    job = ROOT / course / "slurm" / (lab + ".sbatch")
    selection = (
        ["--profiler", mode]
        if mode in {"nsys", "ncu", "all"}
        else ["--device", "cuda"]
        if mode == "cuda"
        else ["--experiment", mode.replace("two_node", "two-node")]
        if mode
        else []
    )
    return subprocess.run(
        ["bash", str(job), *selection, *arguments],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def worker_prefix(lab="32_dynamo_disaggregation"):
    """Extract the literal native prefix students can inspect in their job."""
    import shlex

    source = (ROOT / "advanced-gpu-communication/slurm" / (lab + ".sbatch")).read_text()
    tail = source.split("--worker-prefix ", 1)[1].split("\n\n", 1)[0]
    return shlex.split(tail.replace("\\\n", " "))
