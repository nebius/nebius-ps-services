"""Generation-bound Nsight customization shared by install and jail replacement."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from .deployment_state import digest
from .nsight import REPORTS_PATH, TOOL_VERSIONS, kubernetes_name, soperator_report_binding
from .nsight_jail import encode_payload
from .runtime_config import to_plain_data
from .soperator_adapter import mount_gate_init_container
from .soperator_protected_data_plane import _rootfs_job_manifest

STAGES = ("nsight-admit", "nsight-install", "nsight-verify")
RUNTIME_IMAGE = "docker.io/library/ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3"


def installer_source() -> str:
    return Path(__file__).with_name("nsight_jail.py").read_text()


def activation_source() -> str:
    return Path(__file__).with_name("nsight_profile_activation.py").read_text()


def activation_command(action: str) -> str:
    return (
        shlex.join(
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
                activation_source(),
                action,
            ]
        )
        + "\n"
    )


def active_verify_arguments(admission):
    return [
        "chroot",
        "/mnt/jail",
        "/bin/sh",
        "-c",
        shlex.join(["/usr/bin/python3", "-c", activation_source(), "verify"])
        + " && "
        + shlex.join(
            [
                "/usr/bin/python3",
                "-c",
                installer_source(),
                encode_payload({"action": "verify", "admission": admission}),
            ]
        ),
    ]


def default_settings(
    *, secret_name: str = "nsight-streamer-auth", reports_path: str = REPORTS_PATH
) -> dict[str, Any]:
    return {
        "tools": dict(TOOL_VERSIONS),
        "reports_path": reports_path,
        "secret_name": kubernetes_name(secret_name, "Nsight login Secret"),
        "installer_sha256": "sha256:" + hashlib.sha256(installer_source().encode()).hexdigest(),
    }


def validate_settings(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping) or set(value) != {
        "tools",
        "reports_path",
        "secret_name",
        "installer_sha256",
    }:
        raise ValueError("profiling requires tools, reports_path, secret_name and installer_sha256")
    if value["tools"] != TOOL_VERSIONS:
        raise ValueError("Profiling requires nsys 2026.4.1 and ncu 2026.2.1")
    kubernetes_name(value["secret_name"], "Nsight login Secret")
    if not isinstance(value["reports_path"], str) or not re.fullmatch(
        r"/[A-Za-z0-9_./-]+", value["reports_path"]
    ):
        raise ValueError("Profiling reports_path must be a normalized absolute path")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(value["installer_sha256"])):
        raise ValueError("Profiling installer_sha256 must bind its installer program")


def profiling_settings(config: Any, target_ref: str) -> dict[str, Any] | None:
    rows = [
        row
        for row in (to_plain_data(config) or {}).get("deploy", {}).get("targets", [])
        if row.get("instance_id") == target_ref
    ]
    value = rows[0].get("profiling") if len(rows) == 1 else None
    validate_settings(value)
    if value is not None and value["installer_sha256"] != default_settings()["installer_sha256"]:
        raise RuntimeError(
            "The frozen Nsight installer differs from this CLI; use the CLI version that owns this profiling configuration"
        )
    return dict(value) if value is not None else None


def profiling_fingerprint(config: Any, target_ref: str) -> str:
    settings = profiling_settings(config, target_ref)
    return digest(settings) if settings is not None else ""


def soperator_viewer_gate(config, *, target_ref, release):
    from .deployment_plan import soperator_target
    from .nsight import VIEWER_IMAGES
    from .soperator_adapter import _filesystem_id

    settings = profiling_settings(config, target_ref)
    if settings is None:
        return []
    selected = soperator_target(config)
    if selected is None or selected[0] != target_ref:
        raise ValueError("Profiling configuration requires a Soperator target")
    values = selected[1]["values"]
    binding = soperator_report_binding(
        values, namespace="soperator", reports_path=settings["reports_path"]
    )
    viewer = release["values"]
    if (
        release["namespace"] != "soperator"
        or viewer["volumes"][0]["persistentVolumeClaim"]["claimName"] != binding.claim
        or viewer["volumeMounts"][0].get("subPath") != binding.subpath
    ):
        raise ValueError("Soperator viewer storage differs from the persistent reports binding")
    gate = mount_gate_init_container(
        name="nsight-reports",
        volume_name="reports",
        mount_id="jail",
        filesystem_id=_filesystem_id(values, "jail"),
        image=VIEWER_IMAGES[viewer["tool"]],
    )
    return [
        {
            "target": {"kind": "Deployment", "name": re.escape(release["release_name"])},
            "patch": yaml.safe_dump(
                [{"op": "add", "path": "/spec/template/spec/initContainers", "value": [gate]}]
            ),
        }
    ]


def sealed_customization(
    stages, *, settings_sha256: str, generation: str, pvc_uid: str, pv_uid: str
) -> dict[str, Any] | None:
    names = {*("rootfs-" + stage for stage in STAGES), "rootfs-nsight-customization"}
    present = names & set(stages)
    if not settings_sha256:
        if present:
            raise RuntimeError("Unexpected Nsight customization in this rootfs generation")
        return None
    if present != names or any(stages[name].get("status") != "complete" for name in names):
        raise RuntimeError("Nsight customization is incomplete; jail promotion is blocked")
    receipt = stages["rootfs-nsight-customization"].get("evidence", {})
    body = {key: value for key, value in receipt.items() if key != "receiptSha256"}
    if (
        receipt.get("schema") != "nebius-cxcli.nsight-generation.v1"
        or receipt.get("generation") != generation
        or receipt.get("pvcUid") != pvc_uid
        or receipt.get("pvUid") != pv_uid
        or receipt.get("settingsSha256") != settings_sha256
        or receipt.get("receiptSha256") != digest(body)
    ):
        raise RuntimeError("Nsight customization belongs to another rootfs generation")
    from .nsight_recovery import validate_chain

    lineage = {}
    for name in STAGES:
        chain = stages["rootfs-" + name].get("nsight")
        final = validate_chain(chain, completed=True)
        if final["result"] != stages["rootfs-" + name]["evidence"].get("result"):
            raise RuntimeError("Nsight stage result differs from its selected attempt")
        lineage[name] = digest(chain)
    if receipt.get("attemptLineage") != lineage:
        raise RuntimeError("Nsight customization attempt lineage changed")
    admission = stages["rootfs-nsight-admit"]["evidence"].get("result")
    installed = stages["rootfs-nsight-install"]["evidence"].get("result")
    verified = stages["rootfs-nsight-verify"]["evidence"].get("result")
    if (
        admission != receipt.get("admission")
        or installed != verified
        or verified != receipt.get("verification")
    ):
        raise RuntimeError("Nsight customization Job evidence differs from its receipt")
    validate_customization(verified, admission)
    return dict(receipt)


def customization_job(
    *,
    name: str,
    image: str,
    pvc: str,
    filesystem_id: str,
    request: Mapping[str, Any],
    reports_claim: str = "",
    reports_subpath: str = "",
    reports_mount: str = "",
    installation_repair: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    action = request["action"]
    request = dict(request)
    if installation_repair is not None:
        if action != "install":
            raise ValueError("Omission repair belongs only to standalone installation")
        # Restore only the frozen omissions, then verify without republishing
        # intact profiles, package intent or receipts through ordinary install.
        request["action"] = "verify"
    request["require_mount_capability_dropped"] = True
    if reports_claim:
        request["reports"] = {"mount": reports_mount, "subpath": reports_subpath}
    command = shlex.join(
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
            installer_source(),
            encode_payload(request),
        ]
    )
    if request["action"] != "verify":
        # Keep a single Pod identity, including bounded transient retries. A
        # failed install rechecks the remaining frozen package transaction.
        command = (
            "attempt=0\nuntil "
            + command
            + '; do\n  attempt=$((attempt + 1))\n  [ "$attempt" -lt 3 ] || exit 1\n  sleep 5\ndone\n'
        )
    if action in {"install", "verify"}:
        command = activation_command(request["action"]) + command
    if installation_repair is not None:
        from .nsight_installation import repair_command

        command = repair_command(installation_repair) + command
    manifest = _rootfs_job_manifest(
        namespace="soperator",
        name=name,
        image=RUNTIME_IMAGE,
        pvc_name=pvc,
        script=runtime_mount_script(read_only=action == "verify") + command,
        read_only=action == "verify",
        purpose=f"profiling-{action}",
    )
    pod: Any = manifest["spec"]
    pod = pod["template"]["spec"]
    if len(pod["containers"][0]["command"][2].encode()) > 120 * 1024:
        raise RuntimeError("Nsight execution exceeds the portable Linux argument budget")
    pod["volumes"].append({"name": "runtime", "emptyDir": {"sizeLimit": "1Gi"}})
    pod["containers"][0]["volumeMounts"].append(
        {"name": "runtime", "mountPath": "/mnt/nsight-runtime"}
    )
    # Mount setup is local to this Pod. setpriv drops SYS_ADMIN before running
    # any jail executable or downloaded package maintainer script.
    pod["containers"][0]["securityContext"]["capabilities"] = {"add": ["SYS_ADMIN"]}
    # The runtime default AppArmor policy denies mount even with SYS_ADMIN.
    # Match upstream jail population; jail executables still lose SYS_ADMIN.
    pod["containers"][0]["securityContext"]["appArmorProfile"] = {"type": "Unconfined"}
    pod["initContainers"] = [
        mount_gate_init_container(
            name="nsight-jail",
            volume_name="rootfs",
            mount_id="jail",
            filesystem_id=filesystem_id,
            image=image,
        )
    ]
    if reports_claim:
        from .nsight import report_subpath

        report_subpath(reports_subpath)
        pod["volumes"].append(
            {"name": "reports", "persistentVolumeClaim": {"claimName": reports_claim}}
        )
        pod["initContainers"].append(
            mount_gate_init_container(
                name="nsight-reports",
                volume_name="reports",
                mount_id="jail",
                filesystem_id=filesystem_id,
                image=image,
            )
        )
        pod["containers"][0]["volumeMounts"].append(
            {"name": "reports", "mountPath": "/mnt/jail" + reports_mount, "readOnly": False}
        )
    return manifest


def runtime_mount_script(*, read_only: bool) -> str:
    create = "false" if read_only else "true"
    return f"""set -eu
command -v setpriv >/dev/null
mount --make-rprivate /
scratch=/mnt/nsight-runtime
for directory in dev proc sys run tmp; do
  target=/mnt/jail/$directory
  [ "$(readlink -m "$target")" = "$target" ] || exit 1
  if [ ! -d "$target" ]; then {create} || exit 1; mkdir "$target"; fi
done
for file in resolv.conf hosts; do
  target=/mnt/jail/etc/$file
  [ "$(readlink -m "$target")" = "$target" ] || exit 1
  if [ ! -f "$target" ]; then {create} || exit 1; : > "$target"; fi
done
mkdir -p "$scratch/dev" "$scratch/run" "$scratch/tmp"
chmod 1777 "$scratch/tmp"
for device in null zero random urandom; do
  : > "$scratch/dev/$device"
  mount --bind /dev/$device "$scratch/dev/$device"
done
ln -s /proc/self/fd "$scratch/dev/fd"
ln -s /proc/self/fd/0 "$scratch/dev/stdin"
ln -s /proc/self/fd/1 "$scratch/dev/stdout"
ln -s /proc/self/fd/2 "$scratch/dev/stderr"
mount --rbind "$scratch/dev" /mnt/jail/dev
for directory in proc sys; do
  mount --rbind /$directory /mnt/jail/$directory
  mount -o remount,bind,ro,nosuid,nodev,noexec /mnt/jail/$directory
done
for directory in run tmp; do
  mount --bind "$scratch/$directory" /mnt/jail/$directory
done
for file in resolv.conf hosts; do
  mount --bind /etc/$file /mnt/jail/etc/$file
  mount -o remount,bind,ro,nosuid,nodev,noexec /mnt/jail/etc/$file
done
"""


def parse_result(output: str) -> dict[str, Any]:
    lines = [
        line[len("CXCLI_NSIGHT=") :]
        for line in output.splitlines()
        if line.startswith("CXCLI_NSIGHT=")
    ]
    if len(lines) != 1:
        raise RuntimeError("Nsight customization must return one exact receipt")
    result = json.loads(lines[0])
    if not isinstance(result, dict):
        raise RuntimeError("Nsight customization receipt must be an object")
    return result


def validate_customization(receipt: Mapping[str, Any], admission: Mapping[str, Any]) -> None:
    if (
        receipt.get("schema") != "nebius-cxcli.nsight-customization.v1"
        or receipt.get("admissionSha256") != digest(admission)
        or set(receipt.get("binaries", {})) != set(TOOL_VERSIONS)
        or set(receipt.get("binarySha256", {})) != set(TOOL_VERSIONS)
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", str(value))
            for value in [receipt.get("profileSha256"), *receipt.get("binarySha256", {}).values()]
        )
        or any(not str(path).startswith("/opt/") for path in receipt.get("binaries", {}).values())
    ):
        raise RuntimeError("Nsight customization receipt differs from its package admission")


def customize_generation(
    *,
    config: Any,
    target_ref: str,
    image: str,
    pvc: str,
    pvc_uid: str,
    pv_uid: str,
    filesystem_id: str,
    generation: str,
    run_job: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
    reports_values: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    settings = profiling_settings(config, target_ref)
    if settings is None:
        return None
    token = digest([generation, pvc_uid, settings]).removeprefix("sha256:")[:16]

    def execute(action: str, admission=None):
        request = {"action": action, **({"admission": admission} if admission is not None else {})}
        reports = {}
        if action == "install" and reports_values is not None:
            binding = soperator_report_binding(
                reports_values, namespace="soperator", reports_path=settings["reports_path"]
            )
            reports = {
                "reports_claim": binding.claim,
                "reports_subpath": binding.subpath,
                "reports_mount": binding.jail_path[: -(len(binding.subpath) + 1)],
            }
        return run_job(
            "nsight-" + action,
            customization_job(
                name=f"cxcli-nsight-{token}-{action}",
                image=image,
                pvc=pvc,
                filesystem_id=filesystem_id,
                request=request,
                **reports,
            ),
        )

    admission = dict(execute("admit"))
    if admission.get("schema") != "nebius-cxcli.nsight-packages.v2":
        raise RuntimeError("Nsight package admission is incomplete")
    installed = dict(execute("install", admission))
    validate_customization(installed, admission)
    verified = dict(execute("verify", admission))
    validate_customization(verified, admission)
    if verified != installed:
        raise RuntimeError("Nsight customized files changed before independent verification")
    body = {
        "schema": "nebius-cxcli.nsight-generation.v1",
        "generation": generation,
        "pvcName": pvc,
        "pvcUid": pvc_uid,
        "pvUid": pv_uid,
        "settingsSha256": digest(settings),
        "admission": admission,
        "verification": verified,
    }
    return {**body, "receiptSha256": digest(body)}
