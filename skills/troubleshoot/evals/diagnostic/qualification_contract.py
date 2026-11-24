"""Frozen execution conditions and independent-review identity for diagnostics."""

from __future__ import annotations

import hashlib
import json
import re

SCHEMA = "diagnostic-benchmark/v2"
CONDITION_FIELDS = (
    "schema",
    "suite",
    "agent",
    "model",
    "image",
    "deadline_seconds",
    "repetitions",
    "execution_contract",
    "payload_hashes",
    "parallel_eligible_cases",
    "accounting_contract",
)


def validate_runtime(image: str, network: str) -> None:
    if not isinstance(image, str) or not re.fullmatch(
        r"[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64}", image
    ):
        raise ValueError("image must be pinned by digest")
    if (
        not isinstance(network, str)
        or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", network)
        or network in {"host", "container", "bridge", "default"}
    ):
        raise ValueError("use none or a dedicated inference-proxy network")


def execution_contract(network: str) -> dict:
    return {
        "network": network,
        "cpu_limit": 0.25,
        "memory_mib": 512,
        "pids": 128,
        "readonly_inputs": True,
        "no_host_mounts": True,
    }


def valid_conditions(report: dict) -> bool:
    contract = report.get("execution_contract")
    if not isinstance(contract, dict):
        return False
    try:
        validate_runtime(report.get("image"), contract.get("network"))
    except ValueError:
        return False
    expected = execution_contract(contract["network"])
    return (
        report.get("schema") == SCHEMA
        and report.get("suite") == "qualification"
        and report.get("deadline_seconds") == 600
        and report.get("repetitions") == 3
        and isinstance(report.get("model"), str)
        and bool(report["model"].strip())
        and contract == expected
        and all(type(contract[key]) is type(value) for key, value in expected.items())
    )


def evidence_digest(report: dict, trial: dict) -> str:
    """Bind the review to conditions, fixture, identity and all measured evidence."""
    evidence = {
        "conditions": {key: report.get(key) for key in CONDITION_FIELDS},
        "trial": {
            key: value for key, value in trial.items() if key != "evidence_sha256"
        },
    }
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
