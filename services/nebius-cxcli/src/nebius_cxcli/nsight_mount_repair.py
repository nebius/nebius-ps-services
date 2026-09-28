"""Exact cause-bound runtime corrections, never arbitrary manifest patches."""

from __future__ import annotations

import copy
import json

from .deployment_state import digest
from .nsight_profiling import RUNTIME_IMAGE, activation_command

REPAIR = "runtime-mounts"
IMAGE_REPAIR = "runtime-image"
PROFILE_REPAIR = "profile-order"
PROFILE_SIGNATURE = (
    "Traceback (most recent call last):\n"
    '  File "<string>", line 705, in <module>\n'
    '  File "<string>", line 696, in main\n'
    '  File "<string>", line 645, in install\n'
    '  File "<string>", line 583, in verify\n'
    "RuntimeError: Login PATH selects another Nsight installation\n"
) * 3


def repair_stage(kind):
    if kind in {REPAIR, IMAGE_REPAIR}:
        return "nsight-admit"
    if kind == PROFILE_REPAIR:
        return "nsight-install"
    raise ValueError("Unsupported Nsight runtime repair")


INCOMPATIBLE_IMAGE = "cr.eu-north1.nebius.cloud/soperator/populate_jail@sha256:1e2743b3767432b97cf0885d0fbf7baa88d1c6d42ff3b7924c3f4685fd6437a6"
SIGNATURE = "mount: /: Permission denied\n"
IMAGE_SIGNATURE = "readlink: unrecognized option: m\nBusyBox v1.37.0 (2025-01-17 18:12:01 UTC) multi-call binary.\n\nUsage: readlink [-fnv] FILE\n\nDisplay the value of a symlink\n\n\t-n\tDon't add newline\n\t-f\tCanonicalize by following all symlinks\n\t-v\tVerbose\n"


def corrected_manifest(base, kind=REPAIR):
    result = copy.deepcopy(base)
    pod = result["spec"]["template"]["spec"]
    containers = pod["containers"]
    if len(containers) != 1 or containers[0]["name"] != "rootfs":
        raise RuntimeError("Runtime-mounts repair requires the original rootfs container")
    if kind == PROFILE_REPAIR:
        from .soperator_protected_data_plane import protected_workload_identity

        marker = "attempt=0\nuntil setpriv "
        script = containers[0]["command"][2]
        if (
            protected_workload_identity(base).purpose != "profiling-install"
            or script.count(marker) != 1
            or activation_command("install") in script
        ):
            raise RuntimeError("Profile-order repair requires the original install wrapper")
        containers[0]["command"][2] = script.replace(
            marker, activation_command("install") + marker, 1
        )
        return result
    context = containers[0]["securityContext"]
    if (
        kind not in {REPAIR, IMAGE_REPAIR}
        or "appArmorProfile" in pod.get("securityContext", {})
        or any(
            "apparmor" in k.lower()
            for k in result["spec"]["template"]["metadata"].get("annotations", {})
        )
        or context.get("capabilities") != {"add": ["SYS_ADMIN"]}
        or context.get("allowPrivilegeEscalation") is not False
        or context.get("readOnlyRootFilesystem") is not True
        or not containers[0]["command"][2].startswith(
            "set -eu\ncommand -v setpriv >/dev/null\nmount --make-rprivate /\n"
        )
    ):
        raise RuntimeError("Runtime-mounts repair requires the exact omitted AppArmor policy")
    if kind == IMAGE_REPAIR:
        if containers[0]["image"] != INCOMPATIBLE_IMAGE or context.get("appArmorProfile") not in (
            None,
            {"type": "Unconfined"},
        ):
            raise RuntimeError(
                "Runtime-image repair requires the exact incompatible population image"
            )
        containers[0]["image"] = RUNTIME_IMAGE
    elif "appArmorProfile" in context:
        raise RuntimeError("Runtime-mounts repair requires the exact omitted AppArmor policy")
    context["appArmorProfile"] = {"type": "Unconfined"}
    return result


def repair_record(base, kind=REPAIR):
    return {
        "kind": kind,
        "beforeSha256": digest(base),
        "afterSha256": digest(corrected_manifest(base, kind)),
    }


def failure_signature(base, kind):
    if kind == PROFILE_REPAIR:
        return PROFILE_SIGNATURE, "login-profile-order"
    context = base["spec"]["template"]["spec"]["containers"][0]["securityContext"]
    if kind == IMAGE_REPAIR and context.get("appArmorProfile") == {"type": "Unconfined"}:
        return IMAGE_SIGNATURE, "initial-path-check"
    return SIGNATURE, "initial-private-mount"


def add_failure_proof(kube, attempt, proof, kind=REPAIR):
    """Authenticate the exact retained Pod and known cause-specific failure log."""
    if proof["terminated"].get("rootfs", {}).get("exitCode") != 1 or any(
        row.get("exitCode") != 0 for name, row in proof["terminated"].items() if name != "rootfs"
    ):
        raise RuntimeError("Runtime-mounts repair requires successful init gates and rootfs exit 1")
    # terminal_proof already authenticated this Pod against the frozen Job.
    namespace = attempt["manifest"]["metadata"]["namespace"]
    pods = json.loads(
        kube.run(
            [
                "-n",
                namespace,
                "get",
                "pods",
                "-l",
                f"batch.kubernetes.io/controller-uid={attempt['jobUid']}",
                "-o",
                "json",
            ]
        )
    ).get("items", [])
    if len(pods) != 1 or pods[0].get("metadata", {}).get("uid") != proof["podUid"]:
        raise RuntimeError("Runtime-mounts predecessor Pod changed")
    name = pods[0]["metadata"]["name"]
    output = kube.run(
        [
            "-n",
            namespace,
            "logs",
            name,
            "-c",
            "rootfs",
            "--limit-bytes=4096",
        ]
    )
    signature, boundary = failure_signature(attempt["manifest"], kind)
    if output != signature:
        raise RuntimeError("Nsight runtime repair requires the exact initial setup failure log")
    body = {k: v for k, v in proof.items() if k != "proofSha256"}
    body["mountFailure"] = {
        "signatureSha256": digest(signature),
        "boundary": boundary,
    }
    return {**body, "proofSha256": digest(body)}


def has_failure_proof(proof, base=None, kind=REPAIR):
    signature, boundary = (
        failure_signature(base, kind) if base is not None else (SIGNATURE, "initial-private-mount")
    )
    return (
        proof.get("mountFailure")
        == {
            "signatureSha256": digest(signature),
            "boundary": boundary,
        }
        and proof.get("terminated", {}).get("rootfs", {}).get("exitCode") == 1
        and all(
            row.get("exitCode") == 0
            for name, row in proof.get("terminated", {}).items()
            if name != "rootfs"
        )
    )
