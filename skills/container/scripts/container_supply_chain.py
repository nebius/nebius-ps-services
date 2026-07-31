#!/usr/bin/env python3
"""Repository-selected local supply-chain evidence collection."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from container_audit_types import Finding
from container_runtime_common import run_command


def run_supply_chain(root: Path, image: str) -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    evidence: dict[str, Any] = {"requested": True, "tools": {}}
    selections: list[str] = []
    sbom_generated = False
    vulnerability_assessed = False
    if any((root / name).is_file() for name in (".trivy.yaml", "trivy.yaml")):
        selections.append("trivy")
    if (root / ".syft.yaml").exists():
        selections.append("syft")
    if not selections:
        findings.append(
            Finding(
                "supply-chain.unconfigured",
                "warning",
                "No repository-selected Trivy or Syft configuration was detected.",
            )
        )
        return findings, evidence

    for tool in selections:
        if shutil.which(tool) is None:
            findings.append(
                Finding(
                    f"supply-chain.{tool}-missing",
                    "warning",
                    f"Repository-selected tool is not installed: {tool}.",
                )
            )
            continue
        if tool == "trivy":
            command = [
                "trivy",
                "image",
                "--skip-db-update",
                "--offline-scan",
                "--format",
                "json",
                image,
            ]
        else:
            command = ["syft", f"docker:{image}", "--output", "json"]
        result = run_command(command, timeout=600, cwd=root)
        tool_evidence: dict[str, Any] = {
            "returncode": result.returncode,
            "output_truncated": result.stdout_truncated or result.stderr_truncated,
        }
        if result.returncode != 0:
            findings.append(
                Finding(
                    f"supply-chain.{tool}-failed",
                    "error",
                    f"{tool} did not produce valid offline local-image evidence.",
                )
            )
        else:
            try:
                payload: Any = json.loads(result.stdout)
            except json.JSONDecodeError:
                payload = None
                findings.append(
                    Finding(
                        f"supply-chain.{tool}-invalid",
                        "error",
                        f"{tool} returned invalid or oversized JSON.",
                    )
                )
            if payload is not None and not isinstance(payload, dict):
                payload = None
                findings.append(
                    Finding(
                        f"supply-chain.{tool}-invalid",
                        "error",
                        f"{tool} returned an unexpected JSON document shape.",
                    )
                )
            if tool == "syft" and payload is not None:
                artifacts = payload.get("artifacts")
                if not isinstance(artifacts, list):
                    findings.append(
                        Finding(
                            "supply-chain.syft-invalid",
                            "error",
                            "Syft returned an unexpected artifacts shape.",
                        )
                    )
                else:
                    tool_evidence["artifact_count"] = len(artifacts or [])
                    sbom_generated = True
            elif tool == "trivy" and payload is not None:
                severities: dict[str, int] = {}
                targets = payload.get("Results")
                results_valid = isinstance(targets, list)
                if not results_valid:
                    findings.append(
                        Finding(
                            "supply-chain.trivy-invalid",
                            "error",
                            "Trivy returned an unexpected results shape.",
                        )
                    )
                    targets = []
                for target in targets or []:
                    if not isinstance(target, dict):
                        results_valid = False
                        findings.append(
                            Finding(
                                "supply-chain.trivy-invalid",
                                "error",
                                "Trivy returned an unexpected target shape.",
                            )
                        )
                        continue
                    vulnerabilities = target.get("Vulnerabilities")
                    if vulnerabilities is not None and not isinstance(
                        vulnerabilities, list
                    ):
                        results_valid = False
                        findings.append(
                            Finding(
                                "supply-chain.trivy-invalid",
                                "error",
                                "Trivy returned an unexpected vulnerability shape.",
                            )
                        )
                        continue
                    for vulnerability in vulnerabilities or []:
                        if not isinstance(vulnerability, dict):
                            results_valid = False
                            findings.append(
                                Finding(
                                    "supply-chain.trivy-invalid",
                                    "error",
                                    "Trivy returned an unexpected vulnerability entry.",
                                )
                            )
                            continue
                        severity = str(vulnerability.get("Severity") or "UNKNOWN")
                        severities[severity] = severities.get(severity, 0) + 1
                if results_valid:
                    tool_evidence["vulnerability_counts"] = dict(
                        sorted(severities.items())
                    )
                    vulnerability_assessed = True
                    findings.append(
                        Finding(
                            "supply-chain.vulnerability-policy-unverified",
                            "warning",
                            "Scanner counts do not independently prove an enforced repository severity and exit policy.",
                        )
                    )
        evidence["tools"][tool] = tool_evidence
    evidence["sbom_generated"] = sbom_generated
    evidence["vulnerability_assessed"] = vulnerability_assessed
    if not sbom_generated:
        findings.append(
            Finding(
                "supply-chain.sbom-unverified",
                "warning",
                "No valid repository-selected SBOM evidence was generated.",
            )
        )
    if not vulnerability_assessed:
        findings.append(
            Finding(
                "supply-chain.vulnerability-assessment-unverified",
                "warning",
                "No valid repository-selected vulnerability assessment was produced.",
            )
        )
    evidence["provenance_verified"] = False
    evidence["signature_verified"] = False
    findings.extend(
        (
            Finding(
                "supply-chain.provenance-unverified",
                "warning",
                "Local scanner/SBOM execution does not verify release provenance.",
            ),
            Finding(
                "supply-chain.signature-unverified",
                "warning",
                "Signature verification requires an explicit trusted issuer and network policy.",
            ),
        )
    )
    return findings, evidence
