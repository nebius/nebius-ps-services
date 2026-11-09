"""Sequential, isolated authentic-release package trials owned by run.py."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

from package_repeat import verify_case_identity
from qualification import package_cases, topologies

SETUP = r"""
mkdir -p /qualification
/usr/bin/python3 -B /fixture/verify_predecessor.py --source-wheel "$1"
python3 -m venv /opt/qualification-controller
/opt/qualification-controller/bin/python -m pip install --no-cache-dir "$2"
/opt/qualification-controller/bin/python -m pip install --no-cache-dir uv==0.13.7
ln -s /opt/qualification-controller/bin/uv /usr/local/bin/uv
/usr/bin/python3 -B /fixture/verify_predecessor.py --source-wheel "$1"
cp /opt/vpngw-predecessor.json /qualification/predecessor-image.json
/usr/bin/python3 -B /fixture/package_baseline.py --mode "$3" --topology "$4" --source-wheel "$1"
"""

EVIDENCE = r"""
import json,shutil,subprocess
from pathlib import Path
output=Path('/qualification/runtime-observation');output.mkdir(parents=True,exist_ok=True)
root=Path('/var/lib/nebius-vpngw')
for name in ('ordinary/operation.json','runtime-migration/operation.json',
    'runtime-migration/inhibited','vm-ha/apply.lock','vm-ha/guard.json',
    'vm-ha/controller-checkpoint.json','vm-ha/checkpoint-conversion.json'):
    path=root/name
    if path.is_file() and path.stat().st_size <= 4*1024*1024:
        shutil.copyfile(path,output/name.replace('/','--'))
for label,args in (
    ('dpkg-audit',['dpkg','--audit']),
    ('dpkg-packages',['dpkg-query','-W']),
    ('systemd-jobs',['systemctl','list-jobs','--no-pager']),
    ('journal',['journalctl','-b','--no-pager','-n','200'])):
    result=subprocess.run(args,capture_output=True,text=True,timeout=15)
    (output/(label+'.json')).write_text(json.dumps({'exit':result.returncode,
        'stdout':result.stdout[-256000:],'stderr':result.stderr[-16000:]}))
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def remove_owned_container(name: str, owner: str) -> None:
    # A timed-out create may have succeeded. Discover by the preallocated name
    # and invocation label even when its reply was lost; never infer absence
    # from an unavailable Docker daemon.
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    if not result.stdout.strip():
        return
    if result.stdout.strip() != name:
        raise RuntimeError("Container cleanup discovery is ambiguous")
    result = subprocess.run(
        ["docker", "inspect", "--format", '{{index .Config.Labels "nebius-vpngw.owner"}}', name],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    if result.stdout.strip() != owner:
        raise RuntimeError("Container cleanup ownership changed")
    for command in (["docker", "stop", "--timeout", "10", name], ["docker", "rm", name]):
        subprocess.run(command, stdout=subprocess.DEVNULL, check=True, timeout=30)


def run_historical(args) -> None:
    os.umask(0o077)
    project = Path(__file__).resolve().parents[2]
    manifest_path = args.manifest or Path(__file__).with_name("historical-releases.json")
    inputs = json.loads(manifest_path.read_text())
    releases = inputs["releases"]
    if inputs["schema"] != "nebius-vpngw.package-qualification-inputs.v1" or len(releases) != 4:
        raise ValueError("Qualification requires the frozen four-release input manifest")
    if len({release["version"] for release in releases}) != 4:
        raise ValueError("Qualification release versions must be distinct")
    for release in releases:
        version = release["version"]
        if (
            re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None
            or re.fullmatch(r"[0-9a-f]{64}", release["sha256"]) is None
            or release["tag"] != "nebius-vpngw-v" + version
            or release["url"]
            != (
                "https://github.com/nebius/nebius-ps-services/releases/download/"
                f"nebius-vpngw-v{version}/nebius_vpngw-{version}-py3-none-any.whl"
            )
        ):
            raise ValueError("Qualification release provenance is not canonical")
    candidate = args.candidate_wheel.resolve(strict=True)
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x") as receipt:
        json.dump({"status": "preparing"}, receipt)
    evidence = report.parent / (report.stem + "-evidence")
    evidence.mkdir(mode=0o700, exist_ok=False)
    selected = [
        r
        for r in releases
        if args.historical_version is None or r["version"] == args.historical_version
    ]
    if not selected:
        raise ValueError("Selected version is not in the frozen release manifest")
    latest = max(releases, key=lambda r: tuple(int(p) for p in r["version"].split(".")))
    selected_topologies = topologies(args.topology)
    expected = package_cases([r["version"] for r in selected], latest["version"], args.topology)
    image = "vpngw-package-history-" + uuid.uuid4().hex[:12] + ":fixture"
    images = [image]
    summary = {
        "schema": "nebius-vpngw.package-qualification-matrix.v1",
        "candidate_sha256": sha(candidate),
        "inputs_sha256": sha(manifest_path),
        "platform": args.platform,
        "fixture_sha256": {
            name: sha(Path(__file__).with_name(name))
            for name in (
                "run.py",
                "Dockerfile.packages",
                "Dockerfile.predecessor",
                "historical_packages.py",
                "qualification.py",
                "package_baseline.py",
                "package_upgrade_driver.py",
                "install_predecessor.py",
                "verify_predecessor.py",
                "package_repeat.py",
            )
        },
        "topology": args.topology,
        "coverage": "complete" if len(selected) == 4 and args.topology == "all" else "partial",
        "expected_cases": len(expected),
        "expected_case_ids": expected,
        "expected_repeats": len(expected),
        "cases": [],
        "predecessor_images": [],
        "status": "running",
    }

    def save():
        report.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")

    def command(argv, log, timeout=600):
        return subprocess.run(
            argv, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=timeout
        )

    save()
    try:
        with tempfile.TemporaryDirectory(prefix="vpngw-package-history-") as temporary:
            root = Path(temporary)
            fixture = root / "fixture"
            fixture.mkdir()
            for name in (
                "package_baseline.py",
                "package_upgrade_driver.py",
                "package_repeat.py",
                "install_predecessor.py",
                "verify_predecessor.py",
            ):
                shutil.copyfile(Path(__file__).with_name(name), fixture / name)
            shutil.copyfile(
                project / "tests/fixtures/previous_release_0_6_0_ordinary.json",
                fixture / "previous_release_0_6_0_ordinary.json",
            )
            artifacts = root / "artifacts"
            artifacts.mkdir()
            shutil.copyfile(candidate, artifacts / candidate.name)
            if sha(artifacts / candidate.name) != summary["candidate_sha256"]:
                raise RuntimeError("Candidate changed before fixture publication")
            for release in selected:
                name = release["url"].rsplit("/", 1)[-1]
                target = artifacts / name
                if args.release_wheels:
                    shutil.copyfile(args.release_wheels / name, target)
                else:
                    with urlopen(release["url"], timeout=60) as response:
                        target.write_bytes(response.read())
                if sha(target) != release["sha256"]:
                    raise ValueError("Authentic release wheel digest mismatch")
            with (evidence / "image-build.log").open("w") as log:
                command(
                    [
                        "docker",
                        "build",
                        "--platform",
                        args.platform,
                        "-t",
                        image,
                        "-f",
                        str(Path(__file__).with_name("Dockerfile.packages")),
                        str(Path(__file__).parent),
                    ],
                    log,
                )
            for release in selected:
                # A minimal, public-only build context excludes the candidate,
                # credentials, checkout and scenario configuration. Docker's
                # content cache reuses the verified predecessor installation.
                baseline_context = root / ("baseline-" + release["version"])
                baseline_context.mkdir()
                source_name = release["url"].rsplit("/", 1)[-1]
                shutil.copyfile(artifacts / source_name, baseline_context / source_name)
                for script in ("install_predecessor.py", "verify_predecessor.py"):
                    shutil.copyfile(fixture / script, baseline_context / script)
                baseline_image = image + "-" + release["version"]
                images.append(baseline_image)
                with (evidence / (release["version"] + "-image-build.log")).open("w") as log:
                    command(
                        [
                            "docker",
                            "build",
                            "--platform",
                            args.platform,
                            "--build-arg",
                            "BASE_IMAGE=" + image,
                            "--label",
                            "nebius-vpngw.owner=" + image,
                            "--label",
                            "nebius-vpngw.predecessor=" + release["sha256"],
                            "-t",
                            baseline_image,
                            "-f",
                            str(Path(__file__).with_name("Dockerfile.predecessor")),
                            str(baseline_context),
                        ],
                        log,
                    )
                identity = json.loads(
                    subprocess.check_output(
                        [
                            "docker",
                            "image",
                            "inspect",
                            "--format",
                            "{{json .}}",
                            baseline_image,
                        ],
                        text=True,
                        timeout=30,
                    )
                )
                if identity["Architecture"] != args.platform.removeprefix("linux/"):
                    raise RuntimeError(
                        "Predecessor image architecture differs from selected platform"
                    )
                if identity["Config"]["Labels"]["nebius-vpngw.predecessor"] != release["sha256"]:
                    raise RuntimeError("Predecessor image provenance differs from selected release")
                baseline_id = identity["Id"]
                summary["predecessor_images"].append(
                    {
                        "version": release["version"],
                        "id": baseline_id,
                        "wheel_sha256": release["sha256"],
                        "architecture": identity["Architecture"],
                    }
                )
                save()
                for mode in ("static", "bgp"):
                    cases = [(topology, None) for topology in selected_topologies]
                    if release == latest:
                        fault = "native-unpack" if mode == "static" else "product-wheel"
                        cases.extend((topology, fault) for topology in selected_topologies)
                    for topology, interruption in cases:
                        case = f"{release['version']}-{mode}-{topology}"
                        if interruption:
                            case += "-interrupt-" + interruption
                        name = "vpngw-package-history-" + uuid.uuid4().hex[:12]
                        record = {
                            "case": case,
                            "predecessor_sha256": release["sha256"],
                            "predecessor_image": baseline_id,
                            "status": "running",
                        }
                        summary["cases"].append(record)
                        save()
                        print("Package history trial:", case, flush=True)
                        record["container"] = name
                        save()
                        try:
                            with (evidence / (case + ".log")).open("w") as log:
                                command(
                                    [
                                        "docker",
                                        "run",
                                        "-d",
                                        "--name",
                                        name,
                                        "--platform",
                                        args.platform,
                                        "--label",
                                        "nebius-vpngw.test=package-history",
                                        "--label",
                                        "nebius-vpngw.owner=" + image,
                                        "--privileged",
                                        "--cgroupns=private",
                                        "--memory",
                                        "3g",
                                        "--memory-swap",
                                        "3g",
                                        "--cpus",
                                        "2",
                                        "--pids-limit",
                                        "1024",
                                        "--log-opt",
                                        "max-size=10m",
                                        "--log-opt",
                                        "max-file=2",
                                        "--tmpfs",
                                        "/run",
                                        "--tmpfs",
                                        "/run/lock",
                                        "--tmpfs",
                                        "/tmp",
                                        "--mount",
                                        f"type=bind,src={fixture},dst=/fixture,readonly",
                                        "--mount",
                                        f"type=bind,src={artifacts},dst=/artifacts,readonly",
                                        baseline_id,
                                    ],
                                    log,
                                    60,
                                )
                                deadline = time.monotonic() + 60
                                while subprocess.run(
                                    ["docker", "exec", name, "busctl", "--system", "list"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    timeout=10,
                                ).returncode:
                                    if time.monotonic() >= deadline:
                                        raise RuntimeError(
                                            "Package fixture systemd did not become ready"
                                        )
                                    time.sleep(0.2)
                                source = "/artifacts/" + release["url"].rsplit("/", 1)[-1]
                                target = "/artifacts/" + candidate.name
                                command(
                                    [
                                        "docker",
                                        "exec",
                                        name,
                                        "bash",
                                        "-euc",
                                        SETUP,
                                        "fixture",
                                        source,
                                        target,
                                        mode,
                                        topology,
                                    ],
                                    log,
                                    600,
                                )
                                command(
                                    [
                                        "docker",
                                        "exec",
                                        name,
                                        "/opt/qualification-controller/bin/python",
                                        "-B",
                                        "/fixture/package_upgrade_driver.py",
                                        "--candidate",
                                        target,
                                        "--topology",
                                        topology,
                                        *(
                                            ["--interrupt-after", interruption]
                                            if interruption
                                            else []
                                        ),
                                    ],
                                    log,
                                    900,
                                )
                                command(
                                    [
                                        "docker",
                                        "cp",
                                        name + ":/qualification",
                                        str(evidence / case),
                                    ],
                                    log,
                                    30,
                                )
                            result = json.loads((evidence / case / "result.json").read_text())
                            verify_case_identity(case, result)
                            if result["candidate_sha256"] != summary["candidate_sha256"]:
                                raise RuntimeError("Trial candidate differs from frozen artifact")
                            if result.get("repeat", {}).get("status") != "passed":
                                raise RuntimeError("Package repeat convergence was not qualified")
                            if result["probe"]["architecture"] != args.platform.removeprefix(
                                "linux/"
                            ):
                                raise RuntimeError(
                                    "Trial architecture differs from selected platform"
                                )
                            baseline = json.loads(
                                (evidence / case / "predecessor-image.json").read_text()
                            )
                            if (
                                baseline["wheel_sha256"] != release["sha256"]
                                or baseline["version"] != release["version"]
                            ):
                                raise RuntimeError(
                                    "Installed cached predecessor differs from source release"
                                )
                            record.update(
                                status="passed",
                                boundary=result["boundary"],
                                repeat=result["repeat"]["kind"],
                            )
                        except Exception as error:
                            record.update(status="failed", error_type=type(error).__name__)
                            summary["status"] = "failed"
                            save()
                            raise
                        finally:
                            evidence_failure = False
                            try:
                                with (evidence / (case + "-evidence.log")).open("w") as log:
                                    command(
                                        [
                                            "docker",
                                            "exec",
                                            name,
                                            "/usr/bin/python3",
                                            "-B",
                                            "-c",
                                            EVIDENCE,
                                        ],
                                        log,
                                        90,
                                    )
                                    destination = evidence / case
                                    destination.mkdir(exist_ok=True)
                                    command(
                                        [
                                            "docker",
                                            "cp",
                                            name + ":/qualification/.",
                                            str(destination),
                                        ],
                                        log,
                                        30,
                                    )
                                record["evidence_capture"] = "complete"
                            except Exception as error:
                                record["evidence_capture"] = "failed"
                                record["evidence_error_type"] = type(error).__name__
                                # Preserve the original trial error. Successful
                                # cases still require complete retained evidence.
                                evidence_failure = record["status"] == "passed"
                                if evidence_failure:
                                    record["status"] = summary["status"] = "failed"
                            try:
                                remove_owned_container(name, image)
                                record["cleanup"] = "complete"
                            except Exception as error:
                                record["cleanup"] = "failed"
                                record["cleanup_error_type"] = type(error).__name__
                                summary["status"] = "failed"
                                save()
                                raise
                            save()
                            if evidence_failure:
                                raise RuntimeError("Package trial evidence capture failed")
                        save()
            if [row["case"] for row in summary["cases"]] != expected or any(
                row["status"] != "passed" or not row.get("repeat") for row in summary["cases"]
            ):
                raise RuntimeError("Package qualification case/repeat coverage is incomplete")
            summary["status"] = "passed"
            save()
    finally:
        cleanup = subprocess.run(
            ["docker", "image", "rm", *reversed(images)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
        summary["image_cleanup"] = "complete" if cleanup.returncode == 0 else "failed"
        if cleanup.returncode:
            summary["status"] = "failed"
        if summary["status"] == "running":
            summary["status"] = "incomplete"
        save()
        if cleanup.returncode:
            raise RuntimeError("Package fixture image cleanup did not complete")
