"""Closed adapter transferring only the native ActiveChecks waiter to cxcli."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from . import kubernetes_process

# Official package/source equality establishes artifact trust before this check.
# Registry placement is release data; every other byte remains contract-bound.
WAITER_TEMPLATE_CONTRACT_SHA256 = "70d0b72aa67d094d45245c884da4536d86636c13c2627472ce4868a954b099ab"
_WAITER_IMAGE = re.compile(
    rb"(?m)^(          image: )[a-z0-9][a-z0-9.-]*"
    rb"(/soperator-proxy-docker-io/alpine/k8s:1\.31\.11)$"
)
ACTIVE_CHECKS_RELEASE = "soperator-fluxcd-soperator-activechecks"
HOOK_CONTROL = {"adapter": "cxcli-activechecks-waiter/v1", "install": True, "upgrade": True}


def verify_waiter_inventory(
    files: Mapping[str, bytes], rendered: Sequence[Mapping[str, Any]]
) -> None:
    template = files.get("templates/wait-for-checks-job.yaml", b"")
    contract, image_count = _WAITER_IMAGE.subn(rb"\1<release-registry>\2", template)
    if image_count != 1 or hashlib.sha256(contract).hexdigest() != WAITER_TEMPLATE_CONTRACT_SHA256:
        raise ValueError("ActiveChecks waiter has no reviewed acceptance adapter")
    hooks = [
        doc
        for doc in rendered
        if doc.get("metadata", {}).get("annotations", {}).get("helm.sh/hook")
    ]
    events = []
    for doc in hooks:
        raw = doc["metadata"]["annotations"]["helm.sh/hook"]
        if not isinstance(raw, str):
            raise ValueError("Invalid ActiveChecks hook annotation")
        names = [name.strip() for name in raw.split(",")]
        if len(names) != len(set(names)) or not set(names) <= {
            "pre-install",
            "post-install",
            "pre-upgrade",
            "post-upgrade",
            "pre-delete",
            "post-delete",
            "pre-rollback",
            "post-rollback",
            "test",
        }:
            raise ValueError("Unknown or malformed ActiveChecks hook event")
        events.append(set(names))
    affected = [
        (doc, names)
        for doc, names in zip(hooks, events, strict=True)
        if names & {"pre-install", "post-install", "pre-upgrade", "post-upgrade"}
    ]
    if len(affected) != 1 or (
        affected[0][0].get("kind") != "Job"
        or affected[0][0].get("metadata", {}).get("name") != "wait-for-active-checks"
        or affected[0][1] != {"post-install", "post-upgrade"}
    ):
        raise ValueError("ActiveChecks hook inventory differs from the reviewed sole waiter")


def verify_policy_hooks(chart: Path, projections: Sequence[Mapping[str, Any]]) -> None:
    """Close both action inventories for every distinct effective values projection.

    Maintenance/acceptance share the quiet check projection. Schedules,
    admission and ready share the desired check projection. Slurm partition
    changes belong to another chart and cannot affect these hooks.
    """
    files = {
        "templates/wait-for-checks-job.yaml": (
            chart / "templates/wait-for-checks-job.yaml"
        ).read_bytes()
    }
    for values in projections:
        for action in ("install", "upgrade"):
            command = [
                "helm",
                "template",
                "soperator-activechecks",
                str(chart),
                "--namespace",
                "soperator",
                "--values",
                "-",
            ]
            if action == "upgrade":
                command.append("--is-upgrade")
            rendered = kubernetes_process.run(
                command,
                input=yaml.safe_dump(dict(values)),
                text=True,
                capture_output=True,
                timeout=120,
            )
            if rendered.returncode:
                raise ValueError("Could not validate ActiveChecks phase hook inventory")
            verify_waiter_inventory(
                files,
                [doc for doc in yaml.safe_load_all(rendered.stdout) if isinstance(doc, Mapping)],
            )
