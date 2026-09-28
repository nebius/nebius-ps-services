"""Sync and freeze task-owned remote workspaces without installing infrastructure."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from catalog import expand
from run_labs_common import canonical
from transport import ssh_argv

REMOTE = r"""
import hashlib,json,os,shutil,sys
from pathlib import Path
q=json.load(sys.stdin);os.umask(0o077)
source=Path.home()/q['destination']
for name,expected in q['source'].items():
 p=source/name
 if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
  raise ValueError('Remote synchronized source differs: '+name)
base=source/'.run-labs-work';base.mkdir(mode=0o700,exist_ok=True)
result=[]
for row in q['units']:
 target=base/row['course']/row['lab']/row['profile']
 owner={'campaign':q['campaign'],'source_sha256':q['source_sha256']}
 if target.exists():
  if json.loads((target/'.run-labs-workspace.json').read_text())!=owner:
   raise ValueError('Remote workspace belongs to another source or campaign')
 else:
  target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
  incoming=target.with_name('.'+target.name+'.preparing')
  marker=target.with_name('.'+target.name+'.preparing.json')
  if incoming.exists():
   if not marker.is_file() or json.loads(marker.read_text())!=owner:
    raise ValueError('Partial workspace is not owned by this campaign')
   shutil.rmtree(incoming)
  marker.write_text(json.dumps(owner))
  with marker.open('rb') as stream: os.fsync(stream.fileno())
  shutil.copytree(source/row['course'],incoming,ignore=shutil.ignore_patterns('results','lab-results','*.zip','__pycache__','.venv','.pytest_cache'))
  incoming.chmod(0o700)
  (incoming/'.run-labs-workspace.json').write_text(json.dumps(owner))
  os.replace(incoming,target)
  marker.unlink()
 result.append({'key':row['key'],'profile':row['profile'],'remote_root':str(target)})
print(json.dumps({'source_sha256':q['source_sha256'],'units':result}))
"""


def sync(state, campaign):
    env = state["environment"]
    ssh = env["ssh"]
    destination = "run-labs-" + state["id"]
    receipt = campaign / "sync.json"
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
    if not receipt.exists():
        argv += ["--receipt", str(receipt)]
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
        "units": [
            {k: u[k] for k in ("key", "course", "lab", "profile")}
            for u in state["plan"]["units"]
        ],
    }
    proc = subprocess.run(
        [*ssh_argv(env), shlex.join(["python3", "-c", REMOTE])],
        input=json.dumps(query),
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if proc.returncode:
        raise ValueError(
            "Remote workspace preparation failed; retain existing campaign and inspect its private sync log"
        )
    result = json.loads(proc.stdout)
    if result["source_sha256"] != state["plan"]["source_sha256"]:
        raise ValueError("Remote source proof differs")
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
