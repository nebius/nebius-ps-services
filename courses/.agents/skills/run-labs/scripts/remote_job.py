"""Fixed SSH-side job protocol. Sent over stdin by transport; no shell recipes."""

import fcntl
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def call(argv):
    return subprocess.run(
        argv, check=True, text=True, capture_output=True, timeout=45
    ).stdout.strip()


def write_receipt(path, value):
    fd, name = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_receipt(path):
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        # A partial intent is uncertain; only scheduler reconciliation may recover it.
        return {}


def job_rows(name, job=None):
    argv = [
        "sacct",
        "--noheader",
        "--parsable2",
        "--starttime",
        "1970-01-01",
        "--format=JobIDRaw,JobName%100,User%100,State,ExitCode",
    ]
    argv += (
        ["--jobs", str(job)] if job else ["--name", name, "--user", getpass.getuser()]
    )
    rows = []
    for line in call(argv).splitlines():
        fields = line.split("|")
        if (
            len(fields) >= 5
            and fields[0].isdigit()
            and fields[1] == name
            and fields[2] == getpass.getuser()
        ):
            rows.append(
                {
                    "job": int(fields[0]),
                    "name": name,
                    "user": fields[2],
                    "state": fields[3].split()[0].rstrip("+"),
                    "exit_code": fields[4],
                }
            )
    return rows


def main(req):
    root = Path(req["root"])
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("Remote workspace must be absolute and not a symlink")
    owner = json.loads((root / ".run-labs-workspace.json").read_text())
    if owner != {"campaign": req["campaign"], "source_sha256": req["source_sha256"]}:
        raise ValueError("Remote workspace ownership differs")
    if req["action"] == "cleanup":
        for job in req["jobs"]:
            rows = job_rows(job["name"], job["job"])
            if (
                len(rows) != 1
                or rows[0]["state"] != "COMPLETED"
                or rows[0]["exit_code"] != "0:0"
            ):
                raise ValueError(
                    "Remote cleanup requires all owned jobs completed successfully"
                )
        results = root / "results"
        if results.is_symlink():
            raise ValueError("Remote results became a symlink")
        if results.exists():
            shutil.rmtree(results)
        return {"cleaned": True}
    name = req["name"]
    if not re.fullmatch(r"rl-[a-f0-9]{24}", name):
        raise ValueError("Invalid action name")
    receipt = root / ".run-labs-jobs" / name
    receipt.parent.mkdir(mode=0o700, exist_ok=True)
    with (receipt.parent / (name + ".lock")).open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        if req["action"] == "reconcile":
            if not receipt.exists():
                return {"not_dispatched": True}
            old = read_receipt(receipt)
            rows = job_rows(name, old.get("job"))
            if len(rows) != 1:
                raise ValueError("Uncertain dispatch remains unresolved")
            return rows[0]
        if req["action"] == "submit":
            if receipt.exists():
                old = read_receipt(receipt)
                if old.get("job"):
                    return old
                rows = job_rows(name)
                if len(rows) == 1:
                    write_receipt(receipt, rows[0])
                    return rows[0]
                raise ValueError(
                    "Uncertain dispatch: reconcile Slurm; never automatically resubmit"
                )
            # Persist uncertainty before sbatch. A failure can still mean accepted.
            with receipt.open("x") as out:
                json.dump({"intent": True, "name": name}, out)
                out.flush()
                os.fsync(out.fileno())
            directory_fd = os.open(receipt.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            argv = req["argv"]
            if argv[:2] != ["python3", "tools/submit_lab.py"]:
                raise ValueError("Dispatch requires the course submit helper")
            argv = argv[:4] + ["--parsable", "--job-name=" + name] + argv[4:]
            env = os.environ.copy()
            env.update(req["environment"])
            proc = subprocess.run(
                argv,
                cwd=root,
                env=env,
                check=True,
                text=True,
                capture_output=True,
                timeout=50,
            )
            value = proc.stdout.strip()
            if not re.fullmatch(r"[1-9][0-9]*(?:;[A-Za-z0-9_-]+)?", value):
                raise ValueError("Unrecognized sbatch receipt")
            result = {
                "job": int(value.split(";")[0]),
                "name": name,
                "user": getpass.getuser(),
            }
            write_receipt(receipt, result)
            return result
        rows = job_rows(name, req["job"])
        if len(rows) != 1:
            raise ValueError("Exact owned Slurm job not independently visible")
        row = rows[0]
        if req["action"] == "cancel" and row["state"] not in (
            "COMPLETED",
            "FAILED",
            "CANCELLED",
            "TIMEOUT",
            "OUT_OF_MEMORY",
            "NODE_FAIL",
            "PREEMPTED",
        ):
            call(["scancel", str(row["job"])])
            row["cancel_requested"] = True
        return row


if __name__ == "__main__":
    try:
        os.umask(0o077)
        print(json.dumps(main(json.load(sys.stdin))))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        # Never print captured worker output or prepared environment values.
        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": str(exc)
                    if isinstance(exc, ValueError)
                    else "Remote operation failed; inspect private receipts",
                }
            )
        )
        sys.exit(2)
