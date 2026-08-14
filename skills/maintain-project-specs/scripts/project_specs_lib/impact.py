"""Validate complete prompt-revision impact coverage against canonical specs."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .contracts import (
    ProjectSpecError,
    _managed_region,
    _parse_design,
    _parse_requirements,
    _read_file,
    canonical_digest,
    validate_project,
)


CLAIM_SCHEMA = "maintain-project-specs.prompt-impact-claim.v1"
RECEIPT_SCHEMA = "maintain-project-specs.prompt-impact-receipt.v1"
WORKFLOWS = {"task-implementer", "agentic-sdlc"}
DISPOSITIONS = {
    "changed_contract",
    "existing_contract",
    "execution_only",
    "non_contract",
}
NON_CONTRACT_REASONS = {
    "workflow_directive",
    "duplicate",
    "clarification_context",
}
CONTRACT_EFFECTS = {"requirements", "design"}
ALL_EFFECTS = CONTRACT_EFFECTS | {"execution"}
PLAN_ACTIONS = {"retain_plan", "replan_required"}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
PROMPT_ID_RE = re.compile(r"prompt-[0-9a-f]{32}\Z")
REVISION_RE = re.compile(r"r[0-9]{4}\Z")
CATEGORY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def _fail(message: str) -> None:
    raise ProjectSpecError("PROMPT_IMPACT_INVALID", message)


def _sha256(value: object, label: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _fail(f"{label} is invalid")
    return value


def statement_inventory(extracted: object) -> list[dict[str, object]]:
    """Return a stable complete occurrence inventory without retaining prose."""

    if not isinstance(extracted, dict) or not extracted:
        _fail("refinement extraction is missing")
    inventory: list[dict[str, object]] = []
    for category in sorted(extracted):
        statements = extracted[category]
        if (
            not isinstance(category, str)
            or CATEGORY_RE.fullmatch(category) is None
            or not isinstance(statements, list)
        ):
            _fail("refinement extraction categories are invalid")
        for index, statement in enumerate(statements, start=1):
            if not isinstance(statement, str) or not statement.strip():
                _fail("refinement extraction statements are invalid")
            inventory.append(
                {
                    "key": f"{category}:{index:04d}",
                    "category": category,
                    "statement_sha256": canonical_digest(
                        {"category": category, "statement": statement.strip()}
                    ),
                }
            )
    if not inventory:
        _fail("refinement extraction has no statements")
    return inventory


def _spec_context(project_root: Path) -> dict[str, object]:
    receipt = validate_project(project_root)
    project = Path(str(receipt["project_root"]))
    parsed: dict[str, list[dict[str, Any]]] = {}
    for kind in ("requirements", "design"):
        _raw, text = _read_file(project / "docs" / f"{kind}.md", kind)
        _prefix, body, _suffix = _managed_region(text, kind)
        parsed[kind] = (
            _parse_requirements(body) if kind == "requirements" else _parse_design(body)
        )
    requirements = {
        str(record["id"]): record
        for record in parsed["requirements"]
        if record["status"] != "superseded"
    }
    designs = {
        str(record["id"]): record
        for record in parsed["design"]
        if (
            (str(record["id"]).startswith("TI-DES-") and record["status"] != "superseded")
            or (str(record["id"]).startswith("FEAT-") and record["status"] == "ready")
        )
    }
    return {
        "receipt": receipt,
        "requirements": requirements,
        "design": designs,
    }


def _sorted_unique_ids(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) for item in value)
        or value != sorted(set(value))
    ):
        _fail(f"{label} must be a sorted unique list")
    return list(value)


def validate_prompt_impact(
    project_root: Path,
    *,
    workflow: str,
    prompt_id: str,
    revision: str,
    prompt_sha256: str,
    intent_sha256: str,
    refinement: dict[str, object],
    claim: dict[str, object],
    prior_impact_sha256: str | None,
    prior_spec_receipt_sha256: str | None,
    generation: int,
) -> dict[str, object]:
    """Derive an immutable impact receipt from one complete coverage claim."""

    if workflow not in WORKFLOWS:
        _fail("workflow is invalid")
    if PROMPT_ID_RE.fullmatch(prompt_id) is None:
        _fail("prompt identity is invalid")
    if REVISION_RE.fullmatch(revision) is None:
        _fail("prompt revision is invalid")
    _sha256(prompt_sha256, "prompt digest")
    _sha256(intent_sha256, "prompt intent digest")
    _sha256(prior_impact_sha256, "prior impact digest", optional=True)
    _sha256(prior_spec_receipt_sha256, "prior spec receipt digest", optional=True)
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        _fail("impact generation is invalid")
    if refinement.get("status") != "ready":
        _fail("requirements refinement is not ready")
    if (
        refinement.get("prompt_id") != prompt_id
        or refinement.get("revision") != revision
        or refinement.get("intent_sha256") != intent_sha256
    ):
        _fail("requirements refinement is bound to another prompt revision")

    required_claim = {
        "schema",
        "prompt_id",
        "revision",
        "intent_sha256",
        "dispositions",
        "declared_effects",
        "declared_plan_action",
    }
    if (
        not isinstance(claim, dict)
        or set(claim) != required_claim
        or claim.get("schema") != CLAIM_SCHEMA
        or claim.get("prompt_id") != prompt_id
        or claim.get("revision") != revision
        or claim.get("intent_sha256") != intent_sha256
    ):
        _fail("impact claim identity is invalid")

    inventory = statement_inventory(refinement.get("extracted"))
    expected = {str(item["key"]): item for item in inventory}
    dispositions = claim.get("dispositions")
    if not isinstance(dispositions, list):
        _fail("impact dispositions are invalid")
    by_key: dict[str, dict[str, object]] = {}
    for disposition in dispositions:
        if not isinstance(disposition, dict) or set(disposition) != {
            "statement",
            "disposition",
            "requirements",
            "design",
            "effects",
            "reason",
        }:
            _fail("impact disposition shape is invalid")
        key = disposition.get("statement")
        if not isinstance(key, str) or key not in expected or key in by_key:
            _fail("impact statement coverage is missing, duplicate, or unknown")
        by_key[key] = disposition
    if set(by_key) != set(expected):
        _fail("every extracted statement occurrence requires one disposition")

    specs = _spec_context(project_root)
    current_requirements = dict(specs["requirements"])
    current_designs = dict(specs["design"])
    effects: set[str] = set()
    coverage: list[dict[str, object]] = []
    for item in inventory:
        key = str(item["key"])
        disposition = by_key[key]
        kind = disposition.get("disposition")
        if kind not in DISPOSITIONS:
            _fail("impact disposition is invalid")
        requirements = _sorted_unique_ids(
            disposition.get("requirements"), "requirement mappings"
        )
        designs = _sorted_unique_ids(disposition.get("design"), "design mappings")
        statement_effects = _sorted_unique_ids(
            disposition.get("effects"), "statement effects"
        )
        reason = disposition.get("reason")

        if kind in {"changed_contract", "existing_contract"}:
            if (
                not requirements
                or not designs
                or any(identifier not in current_requirements for identifier in requirements)
                or any(identifier not in current_designs for identifier in designs)
                or reason is not None
            ):
                _fail("contract coverage must map current requirement and design IDs")
            mapped_requirements = {
                str(requirement)
                for design_id in designs
                for requirement in current_designs[design_id]["requirements"]
            }
            if not set(requirements).issubset(mapped_requirements):
                _fail("design mappings do not cover the mapped requirements")
            if kind == "existing_contract":
                if statement_effects:
                    _fail("existing contract coverage cannot declare a new effect")
            elif (
                not statement_effects
                or any(effect not in CONTRACT_EFFECTS for effect in statement_effects)
            ):
                _fail("changed contract coverage must declare contract effects")
        elif kind == "execution_only":
            if requirements or designs or statement_effects != ["execution"] or reason is not None:
                _fail("execution-only coverage has incompatible fields")
        else:
            if (
                requirements
                or designs
                or statement_effects
                or reason not in NON_CONTRACT_REASONS
            ):
                _fail("non-contract coverage requires one bounded reason")

        effects.update(statement_effects)
        coverage.append(
            {
                "statement": key,
                "category": item["category"],
                "statement_sha256": item["statement_sha256"],
                "disposition": kind,
                "requirements": requirements,
                "design": designs,
                "effects": statement_effects,
                "reason": reason,
            }
        )

    derived_effects = sorted(effects)
    if any(effect not in ALL_EFFECTS for effect in derived_effects):
        _fail("derived impact effects are invalid")
    action = "retain_plan" if not derived_effects else "replan_required"
    declared_effects = _sorted_unique_ids(claim.get("declared_effects"), "declared effects")
    if declared_effects != derived_effects:
        _fail("declared impact effects do not match statement coverage")
    if claim.get("declared_plan_action") not in PLAN_ACTIONS or claim.get(
        "declared_plan_action"
    ) != action:
        _fail("declared plan action does not match statement coverage")

    receipt = dict(specs["receipt"])
    spec_receipt_sha256 = canonical_digest(receipt)
    transition = (
        {
            "prior_spec_receipt_sha256": prior_spec_receipt_sha256,
            "next_spec_receipt_sha256": spec_receipt_sha256,
            "prior_impact_sha256": prior_impact_sha256,
            "reason": "owner_reconciliation",
        }
        if prior_spec_receipt_sha256 is not None
        and prior_spec_receipt_sha256 != spec_receipt_sha256
        else None
    )
    return {
        "schema": RECEIPT_SCHEMA,
        "workflow": workflow,
        "prompt_id": prompt_id,
        "revision": revision,
        "prompt_sha256": prompt_sha256,
        "intent_sha256": intent_sha256,
        "refinement_sha256": canonical_digest(refinement),
        "statement_set_sha256": canonical_digest(inventory),
        "spec_receipt_sha256": spec_receipt_sha256,
        "spec_transition": transition,
        "spec_transition_sha256": (
            canonical_digest(transition) if transition is not None else None
        ),
        "requirements_sha256": receipt["requirements"]["sha256"],
        "design_sha256": receipt["design"]["sha256"],
        "prior_impact_sha256": prior_impact_sha256,
        "generation": generation,
        "coverage": coverage,
        "effects": derived_effects,
        "plan_action": action,
    }


def public_impact_status(receipt: dict[str, object]) -> dict[str, object]:
    """Project an impact receipt without content-derived or private identities."""

    effects = list(receipt.get("effects") or [])
    if not effects:
        classification = "no_effect"
    elif effects == ["execution"]:
        classification = "execution_change"
    elif "execution" in effects:
        classification = "contract_and_execution_change"
    else:
        classification = "contract_change"
    requirements = sorted(
        {
            str(identifier)
            for item in list(receipt.get("coverage") or [])
            for identifier in list(item.get("requirements") or [])
        }
    )
    designs = sorted(
        {
            str(identifier)
            for item in list(receipt.get("coverage") or [])
            for identifier in list(item.get("design") or [])
        }
    )
    reasons = sorted(
        {
            str(item["reason"])
            for item in list(receipt.get("coverage") or [])
            if item.get("reason") is not None
        }
    )
    return {
        "classification": classification,
        "requirements": requirements,
        "design": designs,
        "reasons": reasons,
        "plan_action": receipt.get("plan_action"),
    }
