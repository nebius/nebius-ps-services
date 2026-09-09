"""Deployment smoke validation against the authoritative upstream release graph."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .flux_ops import (
    _flux_wait_targets,
    _rendered_soperator_graph_contract,
    _soperator_product_readiness,
)
from .soperator_validation import (
    SOPERATOR_CLUSTER_VALIDATION_KIND,
    SOPERATOR_CLUSTER_VALIDATION_SCHEMA,
    _resolve_validation_report_file,
    _validation_scope,
    _validation_test_purpose,
)


def run_soperator_deploy_validations(
    validations: list[dict[str, Any]],
    *,
    reports_dir: Path,
    extra_env: Mapping[str, str] | None = None,
    emit: Callable[[str], None] | None = None,
) -> list[Path]:
    written = []
    env = {**os.environ, **(extra_env or {})}
    for spec in validations:
        if spec.get("kind") != SOPERATOR_CLUSTER_VALIDATION_KIND:
            raise ValueError("upstream deployment validation requires a Soperator target")
        target = str(spec.get("target_ref", ""))
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", target):
            raise ValueError("upstream deployment validation target is invalid")
        report_file = _resolve_validation_report_file(spec, mode="deploy")
        flux_dir = reports_dir.parent / "flux" / "targets" / target
        contract = _rendered_soperator_graph_contract(flux_dir)
        if contract is None or contract.get("clusterName") != spec.get("cluster_name", target):
            raise ValueError("upstream deployment validation has no matching release graph")
        # Validate one declared main workload before any Kubernetes subprocess.
        if len([row for row in _flux_wait_targets(flux_dir) if row.is_soperator_main]) != 1:
            raise ValueError("upstream deployment validation requires one exact main workload")
        graph_path = flux_dir / "soperator-release-graph.yaml"
        graph_sha = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        context = str(spec.get("kube_context", ""))
        if context:
            current = subprocess.run(
                ["kubectl", "config", "current-context"],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            ).stdout.strip()
            if current != context:
                raise RuntimeError("upstream deployment validation Kubernetes context differs")
        if emit:
            emit(f"Validating the complete upstream Soperator release graph ({target})")
        timeout = float(spec.get("readiness_timeout_seconds", 1200))
        deadline = time.monotonic() + max(0, timeout)
        last_detail = ""
        while True:
            ready, detail = _soperator_product_readiness(contract, env=env)
            if hashlib.sha256(graph_path.read_bytes()).hexdigest() != graph_sha:
                raise RuntimeError("upstream deployment validation release graph changed")
            if ready or time.monotonic() >= deadline:
                break
            if emit and detail != last_detail:
                emit("Soperator deployment readiness pending: " + detail)
                last_detail = detail
            time.sleep(min(10, max(0, deadline - time.monotonic())))
        report = {
            "schema": SOPERATOR_CLUSTER_VALIDATION_SCHEMA,
            "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
            "mode": "deploy",
            "test_purpose": _validation_test_purpose("deploy"),
            "scope": _validation_scope("deploy"),
            "target_ref": target,
            "name": spec.get("name", "Soperator cluster smoke test"),
            "validation": "Soperator upstream deployment test",
            "status": "passed" if ready else "failed",
            "passed": ready,
            "summary": detail,
            "release_graph_sha256": graph_sha,
            "checks": [
                {
                    "name": "Complete upstream release graph",
                    "status": "passed" if ready else "failed",
                    "passed": ready,
                    "summary": detail,
                }
            ],
        }
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / report_file
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        written.append(path)
        if not ready:
            raise RuntimeError("Soperator upstream deployment validation failed: " + detail)
        if emit:
            emit(detail)
    return written
