"""Semantic evidence contract for the Agentic SDLC three-tier scenario."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCENARIO = "three-tier-task-board-v1"
RESULTS_SCHEMA = "agentic-sdlc/three-tier-results-v2"
REQUIRED_SDLC_PHASES = (
    "sdlc-create-requirements",
    "sdlc-start",
    "sdlc-gather-context",
    "sdlc-create-design",
    "sdlc-auto-steering",
    "sdlc-create-plan",
    "sdlc-prepare-execution",
    "sdlc-tdd",
    "sdlc-implement-plan",
    "sdlc-validate-codes",
    "sdlc-unit-tests",
    "sdlc-evaluate",
    "sdlc-update-documents",
    "align",
    "sdlc-commit",
    "local-ship",
    "sdlc-uat-tests",
    "post-uat-documents",
)
REQUIRED_GUI_STEPS = (
    "open-loopback-url",
    "observe-empty-state",
    "submit-blank-title",
    "verify-no-database-row",
    "create-unique-task",
    "observe-created-task",
    "correlate-api-database",
    "refresh-and-observe-persistence",
    "complete-task",
    "correlate-completed-state",
    "filter-active",
    "filter-completed",
    "restart-services-keep-volume",
    "observe-post-restart-persistence",
    "close-test-tab",
)
KEEP_GUI_STEP = "retain-test-tab"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"


class SemanticEvidenceError(RuntimeError):
    """Semantic evidence is incomplete, stale, or unsafe."""


def reject_symlink_components(path: Path, boundary: Path) -> None:
    current = path.absolute()
    while True:
        if current.is_symlink():
            raise SemanticEvidenceError(
                f"Semantic evidence path is symlinked: {current}"
            )
        if current == boundary:
            return
        if current.parent == current:
            raise SemanticEvidenceError("Semantic evidence escaped the owned run.")
        current = current.parent


def artifact_path(run_root: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise SemanticEvidenceError(f"Semantic evidence path is unsafe: {relative}")
    candidate = run_root / raw
    reject_symlink_components(candidate, run_root)
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(run_root) or not resolved.is_file():
        raise SemanticEvidenceError(
            f"Required evidence artifact is missing: {relative}"
        )
    return resolved


def read_results(path: Path, run_root: Path) -> dict[str, Any]:
    reject_symlink_components(path, run_root)
    if path.is_symlink() or not path.is_file():
        raise SemanticEvidenceError("Semantic three-tier results are missing.")
    if path.stat().st_size > 1024 * 1024:
        raise SemanticEvidenceError("Semantic three-tier results exceed 1 MiB.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SemanticEvidenceError(
            f"Semantic three-tier results are invalid: {error}"
        ) from error
    if not isinstance(value, dict):
        raise SemanticEvidenceError("Semantic three-tier results must be an object.")
    return value


def required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SemanticEvidenceError(f"Semantic evidence field is invalid: {label}")
    return value


def summarize_semantic_results(state: dict[str, Any]) -> dict[str, Any] | None:
    """Return bounded partial results for reporting without granting PASS."""

    run_root = Path(state["run_root"])
    try:
        value = read_results(run_root / "evidence" / "three-tier-results.json", run_root)
    except SemanticEvidenceError:
        return None
    if (
        value.get("schema") != RESULTS_SCHEMA
        or value.get("scenario") != SCENARIO
        or value.get("verification_id") != state["verification_id"]
    ):
        return None
    layers = value.get("layers")
    tests = value.get("tests")
    gui = value.get("gui_uat")
    allowed = {"PASS", "PARTIAL", "FAIL", "NOT_RUN"}
    if (
        set(value)
        != {
            "schema",
            "scenario",
            "verification_id",
            "git",
            "layers",
            "tests",
            "sdlc_phases",
            "gui_uat",
        }
        or not isinstance(value.get("git"), dict)
        or set(value["git"]) != {"baseline_sha", "promoted_sha", "clean"}
        or value["git"].get("baseline_sha") != state["git"].get("baseline_sha")
        or value["git"].get("promoted_sha") != state["git"].get("promoted_sha")
        or any(
            sha is not None and (not isinstance(sha, str) or len(sha) < 40)
            for sha in (value["git"]["baseline_sha"], value["git"]["promoted_sha"])
        )
        or not isinstance(value["git"]["clean"], bool)
        or not isinstance(layers, dict)
        or set(layers) != {"frontend", "web", "database"}
        or any(status not in allowed for status in layers.values())
        or not isinstance(tests, dict)
        or set(tests) != {"unit", "api", "database", "migration", "vertical", "gui"}
        or not isinstance(gui, dict)
        or set(value.get("sdlc_phases", {})) != set(REQUIRED_SDLC_PHASES)
        or any(status not in allowed for status in value["sdlc_phases"].values())
    ):
        return None
    normalized_tests: dict[str, dict[str, Any]] = {}
    seen_test_evidence: set[str] = set()
    seen_test_digests: set[str] = set()
    for name, result in tests.items():
        if not isinstance(result, dict) or result.get("status") not in allowed:
            return None
        assertions = result.get("assertions", 0)
        evidence = result.get("evidence", [])
        if (
            not isinstance(assertions, int)
            or isinstance(assertions, bool)
            or assertions < 0
            or not isinstance(evidence, list)
            or any(not isinstance(item, str) or not item for item in evidence)
        ):
            return None
        if result["status"] == "PASS":
            if assertions < 1 or not evidence:
                return None
            try:
                for relative in evidence:
                    if not relative.startswith("evidence/tests/"):
                        return None
                    artifact = artifact_path(run_root, relative)
                    if (
                        relative in seen_test_evidence
                        or artifact.stat().st_size == 0
                        or artifact.stat().st_size > 10 * 1024 * 1024
                    ):
                        return None
                    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
                    if digest in seen_test_digests:
                        return None
                    seen_test_evidence.add(relative)
                    seen_test_digests.add(digest)
            except (OSError, SemanticEvidenceError):
                return None
        normalized_tests[name] = {
            "status": result["status"],
            "assertions": assertions,
            "evidence_count": len(evidence),
        }
    steps = gui.get("steps", [])
    screenshots = gui.get("screenshots", [])
    if (
        set(gui)
        != {
            "harness",
            "browser",
            "steps",
            "api_db_correlated",
            "restart_persistence",
            "screenshots",
        }
        or gui.get("harness") != "computer-use"
        or gui.get("browser") != "chrome"
        or not isinstance(steps, list)
        or not isinstance(screenshots, list)
        or any(not isinstance(item, str) or not item for item in steps + screenshots)
    ):
        return None
    return {
        "layers": dict(layers),
        "tests": normalized_tests,
        "gui_uat": {
            "status": normalized_tests["gui"]["status"],
            "harness": gui.get("harness", "computer-use"),
            "browser": gui.get("browser", state["environment"]["browser"]),
            "step_count": len(steps),
            "screenshot_count": len(screenshots),
            "api_db_correlated": gui.get("api_db_correlated") is True,
            "restart_persistence": gui.get("restart_persistence") is True,
        },
    }


def validate_semantic_results(state: dict[str, Any], *, keep: bool) -> dict[str, Any]:
    run_root = Path(state["run_root"])
    value = read_results(run_root / "evidence" / "three-tier-results.json", run_root)
    if set(value) != {
        "schema",
        "scenario",
        "verification_id",
        "git",
        "layers",
        "tests",
        "sdlc_phases",
        "gui_uat",
    }:
        raise SemanticEvidenceError("Semantic three-tier results fields are invalid.")
    if value.get("schema") != RESULTS_SCHEMA or value.get("scenario") != SCENARIO:
        raise SemanticEvidenceError(
            "Semantic three-tier results schema/profile is invalid."
        )
    if value.get("verification_id") != state["verification_id"]:
        raise SemanticEvidenceError(
            "Semantic results do not match the active verification ID."
        )
    if value.get("git") != {
        "baseline_sha": state["git"].get("baseline_sha"),
        "promoted_sha": state["git"].get("promoted_sha"),
        "clean": True,
    }:
        raise SemanticEvidenceError(
            "Semantic results do not match the clean promoted Git identity."
        )
    if value.get("layers") != {
        "frontend": "PASS",
        "web": "PASS",
        "database": "PASS",
    }:
        raise SemanticEvidenceError(
            "All three application layers require semantic PASS evidence."
        )
    tests = value.get("tests")
    if not isinstance(tests, dict) or set(tests) != {
        "unit",
        "api",
        "database",
        "migration",
        "vertical",
        "gui",
    }:
        raise SemanticEvidenceError(
            "Semantic results must cover every required test class."
        )
    seen_test_evidence: set[str] = set()
    seen_test_digests: dict[str, str] = {}
    for test_name, test_value in tests.items():
        if (
            not isinstance(test_value, dict)
            or set(test_value) != {"status", "assertions", "evidence"}
            or test_value.get("status") != "PASS"
        ):
            raise SemanticEvidenceError(
                f"Required {test_name} test evidence did not pass."
            )
        assertions = test_value.get("assertions")
        evidence = test_value.get("evidence")
        if (
            not isinstance(assertions, int)
            or isinstance(assertions, bool)
            or assertions < 1
        ):
            raise SemanticEvidenceError(f"Required {test_name} assertions are missing.")
        if not isinstance(evidence, list) or not evidence:
            raise SemanticEvidenceError(f"Required {test_name} evidence is missing.")
        for item in evidence:
            relative = required_string(item, f"tests.{test_name}.evidence")
            if not relative.startswith("evidence/tests/"):
                raise SemanticEvidenceError(
                    "Test evidence must stay under evidence/tests/."
                )
            if relative in seen_test_evidence:
                raise SemanticEvidenceError(
                    "One generic artifact cannot satisfy multiple test classes."
                )
            artifact = artifact_path(run_root, relative)
            if artifact.stat().st_size == 0 or artifact.stat().st_size > 10 * 1024 * 1024:
                raise SemanticEvidenceError(
                    f"Test evidence size is invalid: {relative}"
                )
            try:
                digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            except OSError as error:
                raise SemanticEvidenceError(
                    f"Could not read test evidence: {relative}"
                ) from error
            prior_test = seen_test_digests.get(digest)
            if prior_test is not None and prior_test != test_name:
                raise SemanticEvidenceError(
                    "Identical generic evidence cannot satisfy multiple test classes."
                )
            seen_test_digests[digest] = test_name
            seen_test_evidence.add(relative)
    phases = value.get("sdlc_phases")
    if (
        not isinstance(phases, dict)
        or set(phases) != set(REQUIRED_SDLC_PHASES)
        or any(phases.get(phase) != "PASS" for phase in REQUIRED_SDLC_PHASES)
    ):
        raise SemanticEvidenceError(
            "Semantic results are missing a required passing SDLC phase."
        )
    gui = value.get("gui_uat")
    if (
        not isinstance(gui, dict)
        or set(gui)
        != {
            "harness",
            "browser",
            "steps",
            "api_db_correlated",
            "restart_persistence",
            "screenshots",
        }
        or gui.get("harness") != "computer-use"
    ):
        raise SemanticEvidenceError("GUI UAT requires the computer-use harness.")
    if gui.get("browser") != state["environment"]["browser"]:
        raise SemanticEvidenceError("GUI UAT browser identity is invalid.")
    steps = gui.get("steps")
    required_steps = (
        REQUIRED_GUI_STEPS[:-1] + (KEEP_GUI_STEP,) if keep else REQUIRED_GUI_STEPS
    )
    if not isinstance(steps, list) or tuple(steps) != required_steps:
        raise SemanticEvidenceError(
            "GUI UAT actions must match the complete required order."
        )
    if keep and "close-test-tab" in steps:
        raise SemanticEvidenceError(
            "Keep-mode GUI evidence cannot claim the tab was closed."
        )
    if not keep and KEEP_GUI_STEP in steps:
        raise SemanticEvidenceError(
            "Default create evidence cannot claim the tab was retained."
        )
    if (
        gui.get("api_db_correlated") is not True
        or gui.get("restart_persistence") is not True
    ):
        raise SemanticEvidenceError(
            "GUI UAT requires API/database correlation and restart persistence."
        )
    screenshots = gui.get("screenshots")
    if not isinstance(screenshots, list) or len(set(screenshots)) < 5:
        raise SemanticEvidenceError("GUI UAT requires five distinct screenshots.")
    screenshot_digests: set[str] = set()
    for item in screenshots:
        relative = required_string(item, "gui_uat.screenshots")
        if not relative.startswith("evidence/gui-uat/"):
            raise SemanticEvidenceError(
                "GUI screenshot evidence must stay under evidence/gui-uat/."
            )
        artifact = artifact_path(run_root, relative)
        if artifact.stat().st_size > 25 * 1024 * 1024:
            raise SemanticEvidenceError(
                f"GUI screenshot artifact exceeds 25 MiB: {relative}"
            )
        try:
            content = artifact.read_bytes()
        except OSError as error:
            raise SemanticEvidenceError(
                f"Could not read GUI screenshot: {relative}"
            ) from error
        suffix = artifact.suffix
        recognized_image = (
            suffix == ".png"
            and len(content) >= 24
            and content.startswith(PNG_SIGNATURE)
            and content[12:16] == b"IHDR"
        ) or (
            suffix in {".jpg", ".jpeg"}
            and len(content) >= 4
            and content.startswith(JPEG_SIGNATURE)
            and content.endswith(b"\xff\xd9")
        )
        if not recognized_image:
            raise SemanticEvidenceError(
                f"GUI screenshot is not a recognized PNG or JPEG image: {relative}"
            )
        screenshot_digests.add(hashlib.sha256(content).hexdigest())
    if len(screenshot_digests) < 5:
        raise SemanticEvidenceError(
            "GUI UAT screenshots must contain five distinct images."
        )
    return {
        "layers": dict(value["layers"]),
        "tests": {
            name: {
                "status": result["status"],
                "assertions": result["assertions"],
                "evidence_count": len(result["evidence"]),
            }
            for name, result in tests.items()
        },
        "gui_uat": {
            "status": "PASS",
            "harness": gui["harness"],
            "browser": gui["browser"],
            "step_count": len(gui["steps"]),
            "screenshot_count": len(gui["screenshots"]),
            "api_db_correlated": gui["api_db_correlated"],
            "restart_persistence": gui["restart_persistence"],
        },
    }
