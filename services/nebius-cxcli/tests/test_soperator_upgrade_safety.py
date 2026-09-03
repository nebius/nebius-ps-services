from __future__ import annotations

from types import SimpleNamespace

from nebius_cxcli import soperator_upgrade_safety


def test_protected_state_slurm_capture_enters_the_mounted_jail() -> None:
    calls: list[tuple[str, ...]] = []

    def _runner(args, **_kwargs):
        command = tuple(str(item) for item in args)
        calls.append(command)
        return SimpleNamespace(args=command, returncode=0, stdout="ok\n", stderr="")

    audit: list[dict[str, object]] = []
    warnings: list[str] = []
    result = soperator_upgrade_safety._capture_slurm_runtime(
        command_runner=_runner,
        namespace="soperator",
        kube_context="context-a",
        pods={
            "items": [
                {
                    "name": "login-0",
                    "phase": "Running",
                    "labels": {"app.kubernetes.io/component": "login"},
                }
            ]
        },
        timeout_seconds=30,
        audit=audit,
        warnings=warnings,
    )

    assert result["available"] is True
    assert warnings == []
    assert len(calls) == 6
    assert all(
        command[command.index("--") + 1 : command.index("--") + 6]
        == ("chroot", "/mnt/jail", "bash", "-lc", command[-1])
        for command in calls
    )
