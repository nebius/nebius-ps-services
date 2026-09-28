#!/usr/bin/env python3
"""Native package-only verification in disposable Docker fixtures, without a cluster/GPU."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import uuid
from pathlib import Path

from nebius_cxcli.nsight_installation import file_program
from nebius_cxcli.nsight_jail import encode_payload
from nebius_cxcli.nsight_profiling import (
    RUNTIME_IMAGE,
    activation_command,
    activation_source,
    installer_source,
    parse_result,
    runtime_mount_script,
)


def docker(*args, input_text=None, timeout=1800, expected_exit=0):
    result = subprocess.run(
        ["docker", *args], input=input_text, text=True, capture_output=True, timeout=timeout
    )
    if result.returncode != expected_exit:
        raise RuntimeError(
            f"Docker {args[0]} failed:\n{result.stderr[-4000:]}\n{result.stdout[-4000:]}"
        )
    return result.stdout.strip()


def run_native(ubuntu, arch):
    observed = docker("info", "--format", "{{.Architecture}}")
    if {"aarch64": "arm64", "x86_64": "amd64"}.get(observed, observed) != arch:
        raise RuntimeError(
            "Native validation requires the Docker host architecture; emulation is not accepted"
        )
    token = "cxcli-nsight-test-" + uuid.uuid4().hex[:12]
    containers, images = [], []
    source = installer_source()
    repair_source = file_program()
    try:
        print(f"Nsight native {ubuntu}/{arch}: resolving Ubuntu fixture", flush=True)
        docker("pull", "ubuntu:" + ubuntu)
        base = json.loads(docker("image", "inspect", "ubuntu:" + ubuntu))[0]["RepoDigests"][0]
        fixture = token + ":fixture"
        docker(
            "build",
            "-t",
            fixture,
            "-",
            input_text=f"""FROM {base} AS jail
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 ca-certificates util-linux && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /usr/local/cuda/bin && ln -s /bin/false /usr/local/cuda/bin/nsys && ln -s /bin/false /usr/local/cuda/bin/ncu && printf 'export PATH=/usr/local/cuda/bin:$PATH\\n' > /etc/profile.d/path_cuda.sh
FROM {RUNTIME_IMAGE}
COPY --from=jail / /mnt/jail/
RUN mkdir -p /mnt/nsight-runtime /mnt/jail/dev /mnt/jail/proc /mnt/jail/sys /mnt/jail/run /mnt/jail/tmp
""",
        )
        images.append(fixture)
        container = token + "-admission"
        security = (
            "--cap-add",
            "SYS_ADMIN",
            "--security-opt",
            "apparmor=unconfined",
            "--security-opt",
            "no-new-privileges",
        )
        docker("run", "-d", *security, "--name", container, fixture, "sleep", "infinity")
        containers.append(container)

        def execute(name, request, *, code=source, read_only=False, expected_exit=0):
            script = "set -eu\nmount -t tmpfs tmpfs /mnt/nsight-runtime\n"
            if read_only:
                script += "mount --bind /mnt/jail /mnt/jail\nmount -o remount,bind,ro /mnt/jail\n"
            script += runtime_mount_script(read_only=read_only)
            if read_only:
                script += (
                    "findmnt -n -o VFS-OPTIONS --target /mnt/jail | tr ',' '\\n' | grep -qx ro\n"
                )
            if request.get("action") in {"install", "verify"}:
                script += activation_command(request["action"])
            script += shlex.join(
                [
                    "setpriv",
                    "--bounding-set=-sys_admin",
                    "--inh-caps=-sys_admin",
                    "--ambient-caps=-sys_admin",
                    "--no-new-privs",
                    "chroot",
                    "/mnt/jail",
                    "/usr/bin/python3",
                    "-c",
                    code,
                    encode_payload({**request, "require_mount_capability_dropped": True}),
                ]
            )
            # Mounts stay in this exec's namespace; the fixture is not modified to satisfy checks.
            return docker(
                "exec", name, "unshare", "-m", "sh", "-c", script, expected_exit=expected_exit
            )

        print(f"Nsight native {ubuntu}/{arch}: signed online admission", flush=True)
        admission = parse_result(execute(container, {"action": "admit"}))
        admitted = token + ":admitted"
        docker("commit", container, admitted)
        images.append(admitted)
        results = {}
        for scenario in ("fresh", "unpacked", "activation-interrupted"):
            name = token + "-" + scenario
            docker(
                "run",
                "-d",
                *security,
                "--network",
                "none",
                "--name",
                name,
                admitted,
                "sleep",
                "infinity",
            )
            containers.append(name)
            print(f"Nsight native {ubuntu}/{arch}: network-off {scenario} install", flush=True)
            if scenario == "unpacked":
                # Fixture setup stops exactly after one admitted tool archive is unpacked.
                # Product install must authenticate and finish that frozen partial state.
                prefix = source.split('if __name__ == "__main__":')[0]
                code = (
                    prefix
                    + """
request = decode_payload(sys.argv[1])
admission = request["admission"]
validate_runtime_privileges()
atomic_json(INTENT, check_package_preconditions(admission))
artifact = next(a for a in admission["artifacts"] if a["package"].startswith("nsight-compute-"))
run(["dpkg", "--unpack", download(artifact, cached_only=True)])
"""
                )
                execute(name, {"admission": admission}, code=code)
            if scenario == "activation-interrupted":
                code = (
                    activation_source().split('if __name__ == "__main__":')[0]
                    + """
original_link = os.link
def crash_after_publication(source, target):
    original_link(source, target)
    os._exit(73)
os.link = crash_after_publication
activate("install")
"""
                )
                execute(name, {}, code=code, expected_exit=73)
            installed = parse_result(execute(name, {"action": "install", "admission": admission}))
            replayed = parse_result(execute(name, {"action": "install", "admission": admission}))
            verified = parse_result(
                execute(name, {"action": "verify", "admission": admission}, read_only=True)
            )
            if installed != replayed or installed != verified:
                raise RuntimeError("Native installation, replay and read-only verification differ")
            results[scenario] = {"install": True, "replay": True, "readOnlyVerify": True}
            if scenario == "fresh":
                # Fixture fault injection; only product code performs the repair.
                missing = (
                    installed["binaries"]["ncu"],
                    "/etc/profile.d/99-nsight.sh",
                    "/etc/profile.d/zz-nebius-nsight.sh",
                )
                setup = "import os,json,sys; [os.unlink(p) for p in json.loads(sys.argv[1])]"
                docker(
                    "exec", name, "chroot", "/mnt/jail", "python3", "-c", setup, json.dumps(missing)
                )
                request = {"action": "observe", "admission": admission, "verification": installed}
                observation = parse_result(
                    execute(name, request, code=repair_source, read_only=True)
                )
                if observation["state"] != "repairable" or {
                    r["path"] for r in observation["omissions"]
                } != set(missing):
                    raise RuntimeError(
                        "Native omission observation differs from the injected fault"
                    )
                execute(
                    name,
                    {**request, "action": "repair", "omissions": observation["omissions"]},
                    code=repair_source,
                )
                restored = parse_result(
                    execute(name, {"action": "verify", "admission": admission}, read_only=True)
                )
                if restored != installed:
                    raise RuntimeError(
                        "Native omission repair changed accepted installation identity"
                    )
                results["omission-repair"] = {
                    "observe": True,
                    "restore": True,
                    "readOnlyVerify": True,
                }

        return {
            "schema": "nebius-cxcli.nsight-native.v1",
            "ubuntu": ubuntu,
            "architecture": arch,
            "native": True,
            "baseImage": base,
            "runtimeImage": RUNTIME_IMAGE,
            "installerSha256": hashlib.sha256(source.encode()).hexdigest(),
            "repairProgramSha256": hashlib.sha256(repair_source.encode()).hexdigest(),
            "admissionSha256": hashlib.sha256(
                json.dumps(admission, sort_keys=True).encode()
            ).hexdigest(),
            "scenarios": results,
            "gpuOrClusterTest": False,
        }
    finally:
        for name in reversed(containers):
            docker("stop", "-t", "1", name)
            docker("rm", name)
        for image in reversed(images):
            docker("image", "rm", image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ubuntu", choices=("22.04", "24.04"), required=True)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_native(args.ubuntu, args.arch)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Passed native Ubuntu {args.ubuntu}/{args.arch}; receipt: {args.output}")


if __name__ == "__main__":
    main()
