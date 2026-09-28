"""Typed observation and journaled repair of accepted installation omissions."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from .deployment_state import digest
from .nsight_jail import encode_payload


@dataclass(frozen=True)
class InstallationObservation:
    state: str
    omissions: tuple[dict, ...] = ()


def file_program():
    from .nsight_profiling import activation_source, installer_source

    return (
        "package={'__name__':'cxcli_package'}\nexec(" + repr(installer_source()) + ",package)\n"
        "activation={'__name__':'cxcli_activation'}\nexec("
        + repr(activation_source())
        + ",activation)\n"
        + Path(__file__).with_name("nsight_installation_files.py").read_text()
        + "\nmain(package, activation)\n"
    )


def repair_command(repair):
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
                file_program(),
                encode_payload({"action": "repair", **repair}),
            ]
        )
        + "\n"
    )


def observe_installation(env, receipt, verify):
    from .nsight_profiling import parse_result, validate_customization
    from .nsight_runtime import NsightKubernetes

    body = {key: value for key, value in receipt.items() if key != "receiptSha256"}
    if receipt.get("receiptSha256") != digest(body):
        raise RuntimeError("Accepted installation receipt changed")
    validate_customization(receipt["verification"], receipt["admission"])
    try:
        verify(env, receipt)
        return InstallationObservation("healthy")
    except RuntimeError as original:
        # A transport error is never classified as missing files. A separate
        # successful owner-aware observation must prove every omission.
        result = parse_result(
            NsightKubernetes(env).run(
                [
                    "-n",
                    "soperator",
                    "exec",
                    "login-0",
                    "--",
                    "chroot",
                    "/mnt/jail",
                    "/usr/bin/python3",
                    "-c",
                    file_program(),
                    encode_payload(
                        {
                            "action": "observe",
                            "admission": receipt["admission"],
                            "verification": receipt["verification"],
                        }
                    ),
                ],
                timeout=900,
            )
        )
        omissions = result.get("omissions")
        if result.get("state") != "repairable" or not isinstance(omissions, list) or not omissions:
            raise original
        if len(omissions) > 1024 or any(
            not isinstance(row, dict) or set(row) != {"path", "sha256", "mode"} for row in omissions
        ):
            raise RuntimeError("Installation omission observation is incomplete") from original
        return InstallationObservation("repairable", tuple(omissions))


def restore_installation(cli, journal, *, plan, adapter, storage, env, fence, epoch):
    from .nsight_profiling import customization_job
    from .nsight_recovery import run_attempt, validate_chain
    from .nsight_runtime import NsightKubernetes
    from .soperator_protected_data_plane import bind_protected_job_authority

    repair = plan["repair"]
    previous = repair["receipt"]
    binding = storage["volumes"][storage["activePvcName"]]
    repair_id = digest(repair)
    request = customization_job(
        name="cxcli-nsight-" + repair_id.removeprefix("sha256:")[:16] + "-install",
        image=adapter["targetImage"],
        pvc=storage["activePvcName"],
        filesystem_id=adapter["filesystemId"],
        request={"action": "install", "admission": previous["admission"]},
        installation_repair={
            "admission": previous["admission"],
            "verification": previous["verification"],
            "omissions": repair["omissions"],
        },
    )
    job = bind_protected_job_authority(
        request, operation_id=repair_id, fence_epoch=epoch, pvc_uid=binding["pvcUid"]
    )
    result = run_attempt(
        cli,
        journal,
        stage="nsight-install",
        manifest=job,
        fence=fence,
        context=NsightKubernetes(env).context,
        env=env,
        retry_epoch=epoch,
    )
    if result != previous["verification"]:
        raise RuntimeError("Repaired installation differs from its accepted tool receipt")
    validate_chain(journal.stage("nsight-install"), completed=True)
    body = {k: v for k, v in previous.items() if k != "receiptSha256"}
    body["installationRepairs"] = [
        *body.get("installationRepairs", []),
        {
            "repairId": repair_id,
            "attemptSha256": digest(journal.stage("nsight-install")),
        },
    ]
    return {**body, "receiptSha256": digest(body)}
