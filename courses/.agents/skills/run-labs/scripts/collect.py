"""Copy task-owned raw results with independent remote checksums; never model caches."""

from __future__ import annotations

import json
import shlex
import re
from pathlib import PurePosixPath
import subprocess

from evidence import verify_inventory
from run_labs_common import directory, write
from transport import ssh_argv

INVENTORY = r"""
import hashlib,json,sys,re
from pathlib import Path
q=json.load(sys.stdin);root=Path(q['root'])
if json.loads((root/'.run-labs-workspace.json').read_text())!={'campaign':q['campaign'],'source_sha256':q['source_sha256']}:
 raise ValueError('Workspace owner differs')
if not re.fullmatch(r'[0-9]{2}_[a-z0-9_]+',q['lab']):raise ValueError('Invalid lab identity')
rows=[]
paths=[]
for job in q['jobs']:
 if type(job) is not int or job < 1:raise ValueError('Invalid producing job')
 base=root/'results'/q['lab']/'jobs'/str(job)
 if not base.is_dir() or any(p.is_symlink() for p in (base,*base.parents)):raise ValueError('Missing or unsafe job directory')
 paths.extend(base.rglob('*'))
 paths.extend(root/'results'/q['lab']/'logs'/(str(job)+suffix) for suffix in ('.out','.err'))
for p in sorted(set(paths)):
 if p.is_symlink():raise ValueError('Raw result symlink is not collectible')
 if p.is_file():
  with p.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
  rows.append({'path':str(p.relative_to(root)),'sha256':sha,'size':p.stat().st_size})
print(json.dumps({'schema':'run-labs-inventory/v1','files':rows}))
"""


def copy_timeout(inventory):
    """Budget larger copies at 5 MiB/s, bounded between 30 minutes and 4 hours."""
    total = 0
    for row in inventory["files"]:
        size = row["size"]
        if type(size) is not int or size < 0:
            raise ValueError("Inventory file size must be a non-negative integer")
        total += size
    bytes_per_second = 5 * 1024 * 1024
    seconds = (total + bytes_per_second - 1) // bytes_per_second
    return min(14400, max(1800, seconds))


def collect(state, unit, raw):
    env = state["environment"]
    directory(raw)
    query = {
        "root": unit["remote_root"],
        "campaign": state["id"],
        "source_sha256": state["plan"]["source_sha256"],
        "lab": unit["lab"],
        "jobs": sorted(
            {
                stage["dispatch"]["job"]
                for stage in unit["stages"]
                if stage.get("dispatch", {}).get("job")
            }
        ),
    }
    command = shlex.join(["python3", "-c", INVENTORY])
    proc = subprocess.run(
        [*ssh_argv(env), command],
        input=json.dumps(query),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if proc.returncode:
        raise ValueError("Remote checksum inventory failed")
    inventory = json.loads(proc.stdout)
    if inventory.get("schema") != "run-labs-inventory/v1" or not isinstance(
        inventory.get("files"), list
    ):
        raise ValueError("Invalid remote inventory")
    lab, jobs = query["lab"], {str(job) for job in query["jobs"]}
    if not re.fullmatch(r"[0-9]{2}_[a-z0-9_]+", lab):
        raise ValueError("Invalid lab identity")
    seen = set()
    for row in inventory["files"]:
        relative = PurePosixPath(row["path"])
        parts = relative.parts
        valid = (
            len(parts) >= 5
            and parts[:3] == ("results", lab, "jobs")
            and parts[3] in jobs
        )
        valid = valid or (
            len(parts) == 4
            and parts[:3] == ("results", lab, "logs")
            and relative.suffix in (".out", ".err")
            and relative.stem in jobs
        )
        if (
            not valid
            or "\0" in row["path"]
            or ".." in parts
            or relative.is_absolute()
            or str(relative) != row["path"]
            or row["path"] in seen
        ):
            raise ValueError("Inventory path is outside the selected producing jobs")
        if not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
            raise ValueError("Invalid inventory checksum")
        seen.add(row["path"])
    # Quote the remote operand for rsync's SSH command. This uses the same
    # portable rsync interface as sync-labs.sh, including macOS openrsync.
    destination = raw
    transport = shlex.join(ssh_argv(env)[:-1])
    proc = subprocess.run(
        [
            "rsync",
            "-rlt",
            "--safe-links",
            "--files-from=-",
            "--from0",
            "-e",
            transport,
            env["ssh"]["target"] + ":" + shlex.quote(unit["remote_root"] + "/"),
            str(destination) + "/",
        ],
        input="".join(row["path"] + "\0" for row in inventory["files"]).encode(),
        capture_output=True,
        check=False,
        timeout=copy_timeout(inventory),
    )
    if proc.returncode:
        raise ValueError(
            "Raw result copy failed; rerun collection without resubmitting jobs"
        )
    verify_inventory(raw, inventory)
    write(raw / "inventory.json", inventory)
    return inventory
