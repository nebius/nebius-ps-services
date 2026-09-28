"""Publish a bounded deployment summary outside the disposable execution cache."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .deploy_validation_report import DeployValidationReport, _validation_report_path
from .deployment_state import _read_regular
from .paths import ProjectPaths
from .soperator_checks import SoperatorAcceptanceJobError


def publish_deployment_reports(
    source: ProjectPaths,
    destination: ProjectPaths,
    validations: Sequence[Mapping[str, Any]],
    report: DeployValidationReport | None,
    *,
    failure: BaseException | None = None,
) -> DeployValidationReport | None:
    if source.reports_dir == destination.reports_dir:
        return report
    detail_names = {_validation_report_path(v, reports_dir=Path()).as_posix() for v in validations}
    if any(Path(name).name != name or name in {"", ".", ".."} for name in detail_names):
        raise RuntimeError("Deployment report name must be a local filename")
    if any(
        p.is_symlink()
        for p in (
            destination.reports_dir,
            destination.generated_dir,
            destination.project_dir,
            source.reports_dir,
        )
    ):
        raise RuntimeError("Deployment report directories must not be symlinked")
    destination.reports_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    markdown = source.reports_dir / "deploy-report.md"
    if markdown.exists():
        content = _read_regular(markdown).decode()
        content = content.replace(str(source.project_dir), str(destination.project_dir))
        for name in detail_names:
            content = content.replace(name, "deploy-report.json")
        if failure is not None:
            message = (
                str(failure)
                if isinstance(failure, SoperatorAcceptanceJobError)
                else "Deployment execution failed; see the terminal error."
            )
            content += f"\n## Execution failure\n\n{message}\n"
        outputs["deploy-report.md"] = content
    if report is not None:
        # Deliberately project the already displayed results. Raw validation
        # payloads can include subprocess logs and are not publication inputs.
        outputs["deploy-report.json"] = (
            json.dumps(
                {
                    "schema": "nebius-cxcli/deploy-summary/v1",
                    "status": "failed" if failure is not None else report.overall_status,
                    "total": report.total_count,
                    "completed": report.completed_count,
                    "execution_failure": (
                        failure.evidence
                        if isinstance(failure, SoperatorAcceptanceJobError)
                        else {"failed": True}
                        if failure is not None
                        else None
                    ),
                    "results": [
                        {
                            key: value
                            for key, value in asdict(item).items()
                            if key not in {"report_path", "footer_summary"}
                        }
                        for item in report.results
                    ],
                },
                indent=2,
            )
            + "\n"
        )
    for name, content in outputs.items():
        output = destination.reports_dir / name
        with tempfile.NamedTemporaryFile(
            dir=destination.reports_dir, prefix=".deploy-report-", delete=False
        ) as stream:
            staging = Path(stream.name)
            try:
                stream.write(content.encode())
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(staging, output)
            finally:
                staging.unlink(missing_ok=True)
    if report is None:
        return None
    return replace(
        report,
        markdown_path=destination.reports_dir / report.markdown_path.name,
        results=tuple(
            replace(item, report_path=destination.reports_dir / "deploy-report.json")
            for item in report.results
        ),
    )
