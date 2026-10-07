"""Sync and freeze task-owned remote workspaces without installing infrastructure."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

from catalog import expand
from run_labs_common import canonical
from transport import ssh_argv

REMOTE = r"""
import hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
q=json.load(sys.stdin);os.umask(0o077)
source=Path.home()/q['destination']
prepared=Path(q.get('prepared_root') or Path.home()/'courses')
def safe(path):
 if any(p.is_symlink() for p in (path,*path.parents)):
  raise ValueError('Workspace and source paths must not be symlinks')
def private(path):
 safe(path);path.mkdir(mode=0o700,exist_ok=True)
 if path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o777!=0o700:
  raise ValueError('Campaign workspace must be owned and private')
def verify(path,expected):
 safe(path)
 if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
  raise ValueError('Remote synchronized source differs: '+path.name)
for name,expected in q['source'].items():
 verify(source/name,expected)
base=source/'.run-labs-work';private(base)
sys.path.insert(0,str(source/'tools'))
from course_bootstrap import runtime
from course_bootstrap.state import atomic_json,read_json
result=[]
for row in q['units']:
 parent=base
 for name in (row['course'],row['lab'],row['profile']):
  parent=parent/name;private(parent)
 target=parent/row['course'];safe(target)
 owner={'campaign':q['campaign'],'source_sha256':q['source_sha256']}
 binding={'schema':'course-runtime-source/v1','root':str(prepared)}
 if target.exists():
  private(target)
  if read_json(target/'.run-labs-workspace.json')!=owner:
   raise ValueError('Remote workspace belongs to another source or campaign')
  if read_json(target/'.course-runtime-source.json')!=binding:
   raise ValueError('Prepared runtime binding changed; preserve this campaign')
 else:
  incoming=target.with_name('.'+target.name+'.preparing')
  marker=target.with_name('.'+target.name+'.preparing.json')
  safe(incoming);safe(marker)
  if incoming.exists():
   if not marker.is_file() or read_json(marker)!=owner:
    raise ValueError('Partial workspace is not owned by this campaign')
   shutil.rmtree(incoming)
  atomic_json(marker,owner)
  shutil.copytree(source/row['course'],incoming,ignore=shutil.ignore_patterns('results','lab-results','*.zip','__pycache__','.venv','.pytest_cache','.runtime','.models','.cache','build','.course-runtime-source.json'))
  incoming.chmod(0o700)
  atomic_json(incoming/'.run-labs-workspace.json',owner)
  atomic_json(incoming/'.course-runtime-source.json',binding)
  os.replace(incoming,target)
  marker.unlink()
 for name,expected in q['source'].items():
  relative=Path(name)
  if relative.parts[0]==row['course']:
   verify(target/Path(*relative.parts[1:]),expected)
 proofs=[]
 for selection in row['preparation']:
  try:
   activation=subprocess.run([sys.executable,'-B','-E','-s',str(target/'tools/course_runtime.py'),'shell','--course-root',str(target),'--launcher',selection['launcher']],capture_output=True,text=True,timeout=30)
   if activation.returncode:
    raise ValueError(activation.stderr.strip() or 'Native runtime activation failed')
   bound,record=runtime.load(target,selection['launcher'],launcher=True)
   if record['id']!=selection['runtime']:
    raise ValueError('Prepared runtime differs from the frozen selection')
  except (ValueError,FileNotFoundError) as exc:
   print(json.dumps({'error':str(exc)}));sys.exit(1)
  proofs.append({'launcher':selection['launcher'],'runtime':record['id'],'fingerprint':record['fingerprint']})
 result.append({'key':row['key'],'profile':row['profile'],'remote_root':str(target),'prepared_root':str(prepared),'runtimes':proofs})
print(json.dumps({'source_sha256':q['source_sha256'],'units':result}))
"""


def validate_proof(plan, proof):
    """A boolean preflight claim cannot replace selected managed-runtime proof."""
    if proof.get("source_sha256") != plan["source_sha256"]:
        raise ValueError("Remote source proof differs")
    expected = {(u["key"], u["profile"]): u for u in plan["units"]}
    rows = proof.get("units", [])
    if len(rows) != len(expected) or {(r["key"], r["profile"]) for r in rows} != set(expected):
        raise ValueError("Prepared runtime proof does not cover every campaign unit")
    for row in rows:
        unit = expected[row["key"], row["profile"]]
        required = {(r["launcher"], r["runtime"]) for r in unit["preparation"]}
        observed = row.get("runtimes", [])
        if (
            len(observed) != len(required)
            or {(r["launcher"], r["runtime"]) for r in observed} != required
            or any(not isinstance(r.get("fingerprint"), str) or not re.fullmatch(r"[a-f0-9]{64}", r["fingerprint"]) for r in observed)
            or not Path(row.get("prepared_root", "")).is_absolute()
            or Path(row["remote_root"]).name != unit["course"]
        ):
            raise ValueError("Managed runtime proof is incomplete; run sync before preflight")


def sync(state, campaign):
    env = state["environment"]
    ssh = env["ssh"]
    destination = "run-labs-" + state["id"]
    argv = [
        str(Path(state["courses_root"]) / "sync-labs.sh"),
        "--sync-only",
        "--dest",
        destination,
        "--port",
        str(ssh.get("port", 22)),
    ]
    if ssh.get("identity_file"):
        argv += ["--identity", ssh["identity_file"]]
    argv.append(ssh["target"])
    # A stable private log is overwritten on retry; no source values are printed.
    with (campaign / "sync.log").open("w") as stream:
        subprocess.run(
            argv, check=True, stdout=stream, stderr=subprocess.STDOUT, timeout=600
        )
    query = {
        "destination": destination,
        "campaign": state["id"],
        "source": state["plan"]["source"],
        "source_sha256": state["plan"]["source_sha256"],
        "prepared_root": env.get("prepared_root"),
        "units": [
            {k: u[k] for k in ("key", "course", "lab", "profile", "preparation")}
            for u in state["plan"]["units"]
        ],
    }
    proc = subprocess.run(
        [*ssh_argv(env), shlex.join(["python3.12", "-B", "-E", "-s", "-c", REMOTE])],
        input=json.dumps(query),
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if proc.returncode:
        try:
            error = json.loads(proc.stdout).get("error")
        except (ValueError, AttributeError):
            error = None
        raise ValueError(
            error or "Remote workspace preparation failed; retain existing campaign and inspect its private sync log"
        )
    result = json.loads(proc.stdout)
    validate_proof(state["plan"], result)
    for unit in state["plan"]["units"]:
        unit["remote_root"] = next(
            r["remote_root"]
            for r in result["units"]
            if r["key"] == unit["key"] and r["profile"] == unit["profile"]
        )
        for stage in unit["stages"]:
            if "argv" in stage:
                stage["argv"] = expand(
                    stage["argv"],
                    unit["profile"],
                    {"REMOTE_WORKSPACE": unit["remote_root"]},
                    unresolved=True,
                )
            stage["environment"] = {
                k: expand(
                    [v],
                    unit["profile"],
                    {"REMOTE_WORKSPACE": unit["remote_root"]},
                    unresolved=True,
                )[0]
                for k, v in stage.get("environment", {}).items()
            }
    return {
        "schema": "run-labs-sync/v1",
        "environment_sha256": canonical(env),
        **result,
    }
