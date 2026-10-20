#!/usr/bin/env python3
"""Verify faulty/corrected fixtures and isolation in disposable Linux containers."""

from __future__ import annotations

import argparse
import json
import tempfile
import uuid
from pathlib import Path

from cases import CASES, REPLAYS
from fixtures import build
from oracle import program
from runner import base_command, bounded, docker

REPAIRS = {
    "code-precedence": 'p=work/\'app.py\'; s=p.read_text(); p.write_text(s.replace(\'value = settings.get("mode", value)\', \'value = os.environ.get("APP_MODE", settings.get("mode", "safe"))\'))',
    "code-concurrency": 'p=work/\'app.py\'; s=p.read_text(); s=s.replace(\'barrier = threading.Barrier(workers)\', \'barrier = threading.Barrier(workers)\\nlock = threading.Lock()\'); s=s.replace("    old = value\\n    if schedule == \'overlap\': barrier.wait(timeout=2)\\n    value = old + 1", "    if schedule == \'overlap\': barrier.wait(timeout=2)\\n    with lock:\\n        value += 1"); p.write_text(s)',
    "storage-permission": "(work/'data').chmod(0o700)",
    "network-tls": "p=work/'settings.json'; data=json.loads(p.read_text()); data['tls']=False; p.write_text(json.dumps(data))",
}


def run(image: str) -> list[dict]:
    results = []
    with tempfile.TemporaryDirectory(prefix="diagnostic-fixtures-") as temp:
        root = Path(temp).resolve()
        for case_id in [r[0] for r in CASES] + REPLAYS:
            fixture = root / case_id
            build(case_id, fixture)
            for fixed in (False, True) if case_id in REPAIRS else (False,):
                identity = "troubleshoot-smoke-" + uuid.uuid4().hex
                volume = identity + "-work"
                docker("volume", "create", volume)
                try:
                    command = base_command(image, identity, volume)
                    command[command.index("--user=1000:1000")] = "--user=0:0"
                    i = command.index(image)
                    command[i:i] = [
                        "--mount",
                        f"type=bind,src={fixture},dst=/input,readonly",
                        "--cap-add=CHOWN",
                        "--cap-add=DAC_OVERRIDE",
                        "--cap-add=SETUID",
                        "--cap-add=SETGID",
                        "--rm",
                    ]
                    init = (
                        "import pathlib,shutil,os,json; work=pathlib.Path('/work'); "
                        "shutil.copytree('/input','/work',dirs_exist_ok=True); "
                        "[os.chown(p,1000,1000) for d,ds,fs in os.walk('/work') for p in [d]+[os.path.join(d,n) for n in fs]]; "
                    )
                    init += "os.setgroups([]); os.setgid(1000); os.setuid(1000); "
                    if fixed:
                        init += REPAIRS[case_id] + "; "
                    # Actual denied reads, not just absence of a mount argument.
                    init += "\nfor forbidden in ('/evaluator/answers.json','/var/run/docker.sock','/host'):\n try: open(forbidden,'rb'); raise AssertionError('oracle access succeeded')\n except (FileNotFoundError, PermissionError, IsADirectoryError): pass\n"
                    if case_id in REPAIRS:
                        init += program(case_id)
                    elif (fixture / "app.py").exists():
                        init += "\nimport subprocess; p=subprocess.run(['python3','-I','app.py'],cwd=work,env={**os.environ,'APP_MODE':'safe'},capture_output=True,text=True,timeout=5); print(json.dumps({'fixture_exit':p.returncode,'output':p.stdout[:4096]}))"
                    else:
                        init += "\nprint(json.dumps({'replay':json.loads((work/'observations.json').read_text())}))"
                    observed = bounded(command + ["python3", "-I", "-c", init], 30)
                    if observed["status"] != "ok":
                        results.append(
                            {
                                "case": case_id,
                                "fixed": fixed,
                                "pass": False,
                                "reason": "container fixture failed",
                            }
                        )
                        continue
                    data = json.loads(observed["stdout"])
                    passed = (
                        data.get("regression_pass") == fixed
                        if case_id in REPAIRS
                        else True
                    )
                    if case_id == "compute-quota":
                        passed = "25000 100000" in data["output"]
                    if case_id == "compute-wait":
                        passed = (
                            "wall_seconds" in data["output"]
                            and "cpu_seconds" in data["output"]
                        )
                    if case_id == "network-dns":
                        passed = "gaierror" in data["output"]
                    results.append(
                        {
                            "case": case_id,
                            "fixed": fixed,
                            "pass": passed,
                            "isolation_denied_reads": True,
                            "regression_pass": data.get("regression_pass"),
                        }
                    )
                finally:
                    bounded(["docker", "rm", "-f", identity], 15)
                    docker("volume", "rm", volume)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = run(args.image)
    args.output.write_text(
        json.dumps({"image": args.image, "results": results}, indent=2) + "\n"
    )
    print(
        json.dumps({"checks": len(results), "passed": sum(r["pass"] for r in results)})
    )
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
