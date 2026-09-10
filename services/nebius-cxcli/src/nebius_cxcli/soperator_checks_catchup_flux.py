"""Use the staged product workflow to quiesce an admitted native check upgrade."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from .flux_ops import apply_staged_soperator_release
from .paths import ProjectPaths
from .soperator_checks import SoperatorChecksExecution
from .soperator_checks_auxiliary_recovery import AuxiliaryBindingRecovery
from .soperator_checks_binding import AUXILIARY_CRONJOB
from .soperator_checks_catchup import ChecksCatchupRecovery, expected_wait_hook
from .soperator_checks_phase import ChecksPhase
from .soperator_failures import SoperatorMainWorkloadIdentity
from .soperator_install_checks_repair import terminate_admitted_wait_hook


def recover_staged_checks(
    checks: SoperatorChecksExecution,
    *,
    paths: ProjectPaths,
    source_dir: Path,
    kube_context: str,
    extra_env: Mapping[str, str] | None,
    cache_dir: Path | None = None,
    freeze_main_workload_authority: Callable[
        [SoperatorMainWorkloadIdentity], SoperatorMainWorkloadIdentity
    ]
    | None = None,
    on_stage_progress: Callable[[int, int, tuple[str, ...]], None] | None = None,
) -> object:
    env = {**os.environ, **(extra_env or {})}

    def read_log(name: str, container: str) -> str:
        result = subprocess.run(
            [
                "kubectl",
                "--context",
                kube_context,
                "logs",
                "job/" + name,
                "-n",
                "soperator",
                "-c",
                container,
                "--limit-bytes=131072",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode:
            raise RuntimeError("native catch-up submission evidence is unavailable")
        return result.stdout

    def apply_quiet(proof: Mapping[str, Any], stop: Callable[[], None]) -> object:
        checks.authority()
        return apply_staged_soperator_release(
            paths,
            extra_env=env,
            cache_dir=cache_dir,
            checks_policy=checks.policy,
            checks_context=checks.lifecycle.context(ChecksPhase.ACCEPTANCE)
            if checks.lifecycle is not None
            else None,
            remediated_checks_release=proof["checksRelease"],
            freeze_main_workload_authority=freeze_main_workload_authority,
            recover_interrupted_checks=stop,
            on_stage_progress=on_stage_progress,
        )

    result = ChecksCatchupRecovery(
        checks,
        read_log=read_log,
        render_hook=lambda: expected_wait_hook(source_dir),
        apply_quiet=lambda proof: apply_quiet(
            proof,
            lambda: terminate_admitted_wait_hook(
                proof, env=env, kube_context=kube_context, assert_authority=checks.authority
            ),
        ),
    ).recover()

    def render_auxiliary(values: Mapping[str, Any]) -> Mapping[str, Any]:
        rendered = subprocess.run(
            [
                "helm",
                "template",
                "soperator-activechecks",
                str(source_dir / "helm/soperator-activechecks"),
                "--namespace",
                "soperator",
                "-f",
                "-",
            ],
            input=yaml.safe_dump(dict(values)),
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        crons = [
            row
            for row in yaml.safe_load_all(rendered.stdout)
            if isinstance(row, Mapping)
            and row.get("kind") == "CronJob"
            and row.get("metadata", {}).get("name") == AUXILIARY_CRONJOB
        ]
        if len(crons) != 1:
            raise RuntimeError("pinned upstream chart has no exact auxiliary CronJob")
        return crons[0]

    AuxiliaryBindingRecovery(checks, render=render_auxiliary, apply_quiet=apply_quiet).recover()
    return result
