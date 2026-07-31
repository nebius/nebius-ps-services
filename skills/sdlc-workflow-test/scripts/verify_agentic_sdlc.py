#!/usr/bin/env python3
"""Safe preflight verifier for the Agentic SDLC workflow.

The script writes only under ~/.codex/sdlc-verification by default. It inspects
installed global skills and hook config read-only, runs hook source fixtures
with disposable state, and writes a Markdown report.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

SEMANTICS_PATH = Path(__file__).resolve().with_name("three_tier_semantics.py")
SEMANTICS_SPEC = importlib.util.spec_from_file_location(
    "sdlc_workflow_test_three_tier_semantics", SEMANTICS_PATH
)
if SEMANTICS_SPEC is None or SEMANTICS_SPEC.loader is None:
    raise RuntimeError("Could not load the three-tier semantic validator.")
three_tier_semantics = importlib.util.module_from_spec(SEMANTICS_SPEC)
SEMANTICS_SPEC.loader.exec_module(three_tier_semantics)

REQUIRED_SDLC_SKILLS = (
    "sdlc-align-specs",
    "sdlc-auto-steering",
    "sdlc-classify-failure",
    "sdlc-commit",
    "sdlc-create-design",
    "sdlc-create-plan",
    "sdlc-create-requirements",
    "sdlc-evaluate",
    "sdlc-gather-context",
    "sdlc-gui-test",
    "sdlc-implement-plan",
    "sdlc-merge-pr",
    "sdlc-prepare-execution",
    "sdlc-start",
    "sdlc-tdd",
    "sdlc-tui-test",
    "sdlc-update-documents",
    "sdlc-uat-tests",
    "sdlc-unit-tests",
    "sdlc-validate-codes",
)
REQUIRED_RUNTIME_SUPPORT_SKILLS = (
    "worktree",
    "nebius-grafana-query",
    "project-agent-instructions",
    "troubleshoot",
)
SOURCE_PARITY_SKILLS = (
    *REQUIRED_SDLC_SKILLS,
    *REQUIRED_RUNTIME_SUPPORT_SKILLS,
    "sdlc-workflow-test",
)

DESCRIPTION_PREFIX = "Use only as part of the Agentic SDLC workflow;"
DEFAULT_PROJECT_ID = "sdlc-verification-project"
DEFAULT_RUN_ID = "active"
DESIGN_RELATIVE = Path("docs") / "agentic-sdlc-design.md"
LIVE_RESULTS_SCHEMA = "agentic-sdlc/verification-live-results-v3"
LIVE_LANE_RESULT_SCHEMA = "agentic-sdlc/live-lane-result-v3"
SKILL_RESULT_SCHEMA = "agentic-sdlc/skill-result-v3"
VERIFICATION_CONTEXT_SCHEMA = "agentic-sdlc/verification-context-v1"
VERIFICATION_ROOT_MARKER_SCHEMA = "agentic-sdlc/verification-root-v1"
VERIFICATION_ROOT_MARKER = ".sdlc-workflow-test-root.json"
DISPOSABLE_FIXTURE_MARKER = ".sdlc-workflow-test-fixture.json"
DISPOSABLE_FIXTURE_MARKER_CONTENT = '{"schema":"agentic-sdlc/disposable-fixture-v2"}\n'
SHA_RE = re.compile(r"[0-9a-f]{40,64}")
LIVE_LANES = (
    "golden-path",
    "idempotency",
    "change-request",
    "failure-routing",
    "auto-steering",
    "documentation-update",
    "steering-continuation",
)
GOLDEN_PHASE_SEQUENCE = (
    "sdlc-create-requirements",
    "sdlc-start",
    "sdlc-gather-context",
    "sdlc-create-design",
    "project-agent-instructions",
    "sdlc-auto-steering",
    "sdlc-create-plan",
    "sdlc-prepare-execution",
    "sdlc-tdd",
    "sdlc-implement-plan",
    "sdlc-validate-codes",
    "sdlc-unit-tests",
    "sdlc-evaluate",
    "sdlc-update-documents",
    "sdlc-align-specs",
    "sdlc-commit",
    "sdlc-uat-tests",
)
LIVE_LANE_REQUIRED_ASSERTIONS = {
    "golden-path": {
        "full_phase_sequence",
        "canonical_phase_skill_names",
        "selected_scope_commit",
        "clean_final_head",
        "private_state_excluded",
    },
    "idempotency": {
        "stable_prompt_and_feature_ids",
        "no_duplicate_revision",
        "no_duplicate_commit",
    },
    "change-request": {
        "stable_ids",
        "immutable_locked_plan",
        "scoped_change",
        "evidence_refreshed",
    },
    "failure-routing": {
        "validation_failure",
        "implementation_test_failure",
        "bad_test_failure",
        "design_failure",
        "specification_gap_failure",
        "environment_failure",
        "earliest_responsible_phase",
        "repair_rerun_passed",
        "known_cause_bypassed_troubleshooting",
        "ambiguous_failure_diagnosed_once",
        "diagnosis_returned_to_classifier",
        "corrective_plan_reentry",
        "unresolved_or_budget_stop",
    },
    "auto-steering": {
        "classification_recorded",
        "disposition_applied",
        "next_phase_routed",
    },
    "documentation-update": {
        "evaluation_preceded_docs",
        "documents_evidence_recorded",
        "final_docs_aligned",
    },
    "steering-continuation": {
        "pause_honored",
        "no_pr_honored",
        "prompt_bound_continuation",
        "no_private_identity_exposed",
    },
}
SKILL_EVIDENCE_BASES = {skill: "live" for skill in REQUIRED_SDLC_SKILLS}
SKILL_EVIDENCE_BASES.update(
    {
        "project-agent-instructions": "live",
        "sdlc-start": "deterministic",
        "sdlc-prepare-execution": "deterministic",
        "sdlc-implement-plan": "deterministic",
        "sdlc-merge-pr": "safety-only",
    }
)
REQUIRED_EVIDENCE_SKILLS = tuple(SKILL_EVIDENCE_BASES)
SKILL_REQUIRED_ASSERTIONS = {
    "sdlc-align-specs": [
        "requirements_design_traceability",
        "implemented_behavior_alignment",
    ],
    "sdlc-auto-steering": ["revision_classified", "disposition_routed"],
    "sdlc-classify-failure": [
        "controlled_failure_classified",
        "earliest_phase_selected",
    ],
    "sdlc-commit": [
        "clean_promoted_head",
        "canonical_uat_handoff",
        "private_state_excluded",
    ],
    "sdlc-create-design": ["stable_feature_ids", "vertical_slice_boundaries"],
    "sdlc-create-plan": ["immutable_locked_plan", "dependency_wave_scope"],
    "sdlc-create-requirements": [
        "stable_requirement_ids",
        "acceptance_methods_recorded",
    ],
    "sdlc-evaluate": ["cross_layer_evaluation", "gui_api_database_correlation"],
    "sdlc-gather-context": ["source_traceability", "boundary_context_recorded"],
    "sdlc-gui-test": ["computer_use_harness", "semantic_gui_assertions"],
    "sdlc-implement-plan": ["worker_contract_tests", "scoped_integration"],
    "sdlc-merge-pr": ["merge_guardrails_tested", "no_real_merge_performed"],
    "sdlc-prepare-execution": ["execution_scheduler_tests", "recovery_contract_tests"],
    "project-agent-instructions": [
        "conditional_decision_recorded",
        "instruction_ownership_preserved",
    ],
    "sdlc-start": ["two_command_surface_tests", "prompt_bound_state_tests"],
    "sdlc-tdd": ["tests_preceded_implementation", "slice_contract_covered"],
    "sdlc-tui-test": ["disposable_tui_smoke", "terminal_state_asserted"],
    "sdlc-uat-tests": ["acceptance_criteria_exercised", "restart_persistence_proved"],
    "sdlc-unit-tests": ["unit_api_database_suites", "negative_case_covered"],
    "sdlc-update-documents": [
        "evaluation_preceded_docs",
        "runtime_instructions_aligned",
    ],
    "sdlc-validate-codes": ["planned_scope_validated", "security_boundary_reviewed"],
}
EVIDENCE_PROFILES = {
    "deterministic",
    "lightweight",
    "three-tier",
    "safety",
}
PROFILE_SOURCE_SCHEMAS = {
    "lightweight": "agentic-sdlc/prompt-binding-v1",
    "three-tier": "agentic-sdlc/three-tier-results-v2",
}
SKILL_REQUIRED_PROFILES = {
    skill: [
        "deterministic"
        if basis == "deterministic"
        else "safety"
        if basis == "safety-only"
        else "lightweight"
    ]
    for skill, basis in SKILL_EVIDENCE_BASES.items()
}
SKILL_REQUIRED_PROFILES.update(
    {
        "sdlc-evaluate": ["lightweight", "three-tier"],
        "sdlc-gui-test": ["three-tier"],
        "sdlc-uat-tests": ["lightweight", "three-tier"],
        "sdlc-unit-tests": ["lightweight", "three-tier"],
    }
)
DETERMINISTIC_SKILL_CAPABILITIES = {
    "sdlc-start": {
        "public.interface",
        "prompt.workspace-init",
        "prompt.history",
        "prompt.rename",
        "prompt.lifecycle",
    },
    "sdlc-prepare-execution": {
        "execution.scope",
        "execution.sessions-recovery",
        "execution.replan",
        "execution.secret-gate",
    },
    "sdlc-implement-plan": {
        "execution.sequential-fallback",
        "interop.outer-lease-v2",
        "interop.task-implementer-compatibility",
    },
}
SKILL_LANE_REQUIREMENTS = {
    skill: {"golden-path"}
    for skill, basis in SKILL_EVIDENCE_BASES.items()
    if basis == "live"
}
SKILL_LANE_REQUIREMENTS.update(
    {
        "sdlc-auto-steering": {"auto-steering"},
        "sdlc-classify-failure": {"failure-routing"},
        "sdlc-update-documents": {"documentation-update"},
        "sdlc-align-specs": {"golden-path", "documentation-update"},
        "sdlc-tui-test": {"golden-path"},
    }
)


def lane_evidence_pattern(lane: str) -> str:
    """Return the canonical lane-local evidence-path pattern."""
    escaped = re.escape(lane)
    segment = r"(?!\.{1,2}(?:/|$))[^/]+"
    return rf"^evidence/{escaped}/{segment}(?:/{segment})*$"


def valid_lane_evidence_path(lane: str, relative: str) -> bool:
    return re.fullmatch(lane_evidence_pattern(lane), relative) is not None


def valid_assertion_artifact_path(owner_prefix: str, relative: str) -> bool:
    """Return whether an assertion artifact stays in its canonical owner folder."""
    prefix = f"{owner_prefix}/artifacts/"
    if not relative.startswith(prefix):
        return False
    suffix = relative.removeprefix(prefix)
    if not suffix:
        return False
    path = PurePosixPath(suffix)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def valid_profile_source_path(profile: str, relative: str) -> bool:
    """Return whether a profile source stays in its canonical private folder."""
    prefix = f"evidence/profiles/{profile}/sources/"
    if not relative.startswith(prefix):
        return False
    suffix = relative.removeprefix(prefix)
    path = PurePosixPath(suffix)
    return (
        bool(suffix)
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def file_sha256(path: Path) -> str | None:
    """Return a bounded artifact digest, or None for unreadable/oversized files."""
    try:
        if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def assertion_artifact_semantics_valid(assertion: str, artifact: Path) -> bool:
    """Validate assertion-specific evidence whose contract is machine-readable."""
    if assertion not in {"canonical_phase_skill_names", "canonical_uat_handoff"}:
        return False
    try:
        value = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if assertion == "canonical_phase_skill_names":
        return value == {
            "schema": "agentic-sdlc/canonical-phase-skill-names-v1",
            "sequence": list(GOLDEN_PHASE_SEQUENCE),
        }
    return value == {
        "schema": "agentic-sdlc/canonical-uat-handoff-v1",
        "from": "sdlc-commit",
        "to": "sdlc-uat-tests",
    }


PRIVATE_REPOSITORY_PARTS = {
    ".agents",
    ".codex",
    ".sdlc",
    "evidence",
    "plans",
    "screenshots",
    "steering",
    "transcripts",
}
PRIVATE_REPOSITORY_FILES = {
    "STEERING.md",
    "active-run.json",
    "active.lock",
    "current-state.json",
    "feature-queue.json",
    "prompt.json",
    "run.json",
}


@dataclass
class Check:
    name: str
    status: str
    detail: str
    section: str
    capability_id: str | None = None


@dataclass
class Context:
    skills_root: Path
    repo_root: Path
    design_path: Path
    global_skills_dir: Path
    codex_home: Path
    verification_root: Path
    disposable_project: Path
    selected_project: Path
    fixture_codex_home: Path
    live_evidence_path: Path
    three_tier_results_path: Path | None = None
    checks: list[Check] = field(default_factory=list)

    def add(
        self,
        section: str,
        name: str,
        status: str,
        detail: str,
        *,
        capability_id: str | None = None,
    ) -> None:
        self.checks.append(
            Check(
                name=name,
                status=status,
                detail=detail,
                section=section,
                capability_id=capability_id,
            )
        )


def validated_profile_claims(
    ctx: Context,
    profile: str,
    source_artifacts: list[Path],
    *,
    verification_id_value: str,
    baseline: str,
    final: str,
) -> set[str] | None:
    """Derive exact claims from a profile's canonical live source."""
    if profile == "lightweight":
        return validated_lightweight_claims(
            ctx,
            source_artifacts,
            verification_id_value=verification_id_value,
            final=final,
        )
    if profile == "three-tier":
        return validated_three_tier_claims(
            ctx,
            source_artifacts,
            verification_id_value=verification_id_value,
            baseline=baseline,
            final=final,
        )
    return None


def validated_lightweight_claims(
    ctx: Context,
    sources: list[Path],
    *,
    verification_id_value: str,
    final: str,
) -> set[str] | None:
    del ctx, sources, verification_id_value, final
    # The prompt binding and failure summaries do not independently prove all
    # seven live lanes. Stay fail-closed until the collector can derive exact
    # claims from checkpoint, Git, test, steering, and repaired-failure bytes.
    return None


def validated_three_tier_claims(
    ctx: Context,
    sources: list[Path],
    *,
    verification_id_value: str,
    baseline: str,
    final: str,
) -> set[str] | None:
    canonical = ctx.three_tier_results_path
    if (
        canonical is None
        or canonical.is_symlink()
        or not canonical.is_file()
        or canonical.stat().st_nlink != 1
        or (os.name == "posix" and canonical.stat().st_mode & 0o077)
    ):
        return None
    canonical_digest = file_sha256(canonical)
    if canonical_digest is None:
        return None
    matching = [path for path in sources if file_sha256(path) == canonical_digest]
    if len(matching) != 1:
        return None
    run_root = canonical.parent.parent
    active_state = run_root.parent.parent / "active.json"
    try:
        state = json.loads(active_state.read_text(encoding="utf-8"))
        if (
            state.get("run_root") != str(run_root)
            or state.get("verification_id") != verification_id_value
            or state.get("git") != {"baseline_sha": baseline, "promoted_sha": final}
        ):
            return None
        keep = state.get("cleanup", {}).get("status") == "KEPT"
        three_tier_semantics.validate_semantic_results(state, keep=keep)
    except (
        AttributeError,
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
        three_tier_semantics.SemanticEvidenceError,
    ):
        return None
    claims: set[str] = set()
    for skill, profiles in SKILL_REQUIRED_PROFILES.items():
        if "three-tier" in profiles:
            claims.update(SKILL_REQUIRED_ASSERTIONS[skill])
    return claims


def parse_args(argv: list[str]) -> argparse.Namespace:
    skill_dir = Path(__file__).resolve().parents[1]
    skills_root = skill_dir.parents[0]
    repo_root = skills_root.parent
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    parser = argparse.ArgumentParser(
        description="Run safe Agentic SDLC static and hook preflight verification.",
    )
    parser.add_argument("--skills-root", type=Path, default=skills_root)
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    parser.add_argument("--design", type=Path, default=default_design_path(skills_root))
    parser.add_argument(
        "--global-skills-dir", type=Path, default=Path.home() / ".agents" / "skills"
    )
    parser.add_argument("--codex-home", type=Path, default=codex_home)
    parser.add_argument(
        "--verification-root", type=Path, default=codex_home / "sdlc-verification"
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Report path under --verification-root; symlinked or outside paths are rejected.",
    )
    parser.add_argument(
        "--live-evidence",
        type=Path,
        default=None,
        help="Private full-run results manifest; defaults to <verification-root>/live-results.json.",
    )
    parser.add_argument(
        "--three-tier-results",
        type=Path,
        default=None,
        help="Canonical retained three-tier-results.json used to validate the copied three-tier profile source.",
    )
    return parser.parse_args(argv)


def default_design_path(skills_root: Path) -> Path:
    cwd = Path.cwd().resolve(strict=False)
    candidates = (
        cwd / DESIGN_RELATIVE,
        cwd / "skills" / DESIGN_RELATIVE,
        skills_root / DESIGN_RELATIVE,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return skills_root / DESIGN_RELATIVE


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 30,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            env=env,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=args,
            returncode=124,
            stdout="",
            stderr=f"command timed out after {timeout} seconds",
        )
    except OSError:
        return subprocess.CompletedProcess(
            args=args,
            returncode=127,
            stdout="",
            stderr="command could not be started",
        )


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def file_digest(path: Path) -> str:
    if path.is_symlink():
        return "unsafe-symlink"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if root.is_symlink():
        return "unsafe-symlink:."
    if not root.is_dir():
        return "missing"
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            return f"unsafe-symlink:{path.relative_to(root).as_posix()}"
        if (
            not path.is_file()
            or path.name == ".install-source-id"
            or "__pycache__" in path.parts
        ):
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            return "unreadable"
        digest.update(b"\0")
    return digest.hexdigest()


def inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def has_symlink_component(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def has_non_directory_parent_or_non_file_target(path: Path, root: Path) -> bool:
    """Reject path components that cannot safely address a regular file."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    if root.exists() and not root.is_dir():
        return True
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and not current.is_dir():
            return True
    return path.exists() and not path.is_file()


def git_output(project: Path, *args: str) -> str | None:
    result = run(["git", *args], cwd=project, timeout=15)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def verification_id(ctx: Context, baseline_head: str) -> str:
    digest = hashlib.sha256()
    values = [
        str(ctx.selected_project.resolve(strict=False)),
        baseline_head,
        file_digest(ctx.design_path),
    ]
    for name in SOURCE_PARITY_SKILLS:
        values.append(name)
        values.append(tree_digest(ctx.skills_root / name))
    digest.update("\n".join(values).encode("utf-8"))
    return digest.hexdigest()


def frontmatter(skill_md: Path) -> dict[str, str]:
    text = read_text(skill_md)
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line.startswith((" ", "\t")):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("\"'")
    return data


def openai_invocation_policy(metadata_path: Path) -> str | None:
    text = read_text(metadata_path)
    if not text:
        return None
    in_policy = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            in_policy = stripped.split(":", 1)[0] == "policy"
            continue
        if in_policy and stripped.startswith("allow_implicit_invocation:"):
            return stripped.split(":", 1)[1].strip().strip("\"'")
    return None


def setup_context(ns: argparse.Namespace) -> Context:
    verification_root = ns.verification_root.expanduser().resolve(strict=False)
    disposable_project = verification_root / "disposable-project"
    selected_project = disposable_project / "services" / "resource-validator"
    fixture_codex_home = verification_root / "fixture-codex-home"
    return Context(
        skills_root=ns.skills_root.expanduser().resolve(strict=False),
        repo_root=ns.repo_root.expanduser().resolve(strict=False),
        design_path=ns.design.expanduser().resolve(strict=False),
        global_skills_dir=ns.global_skills_dir.expanduser().resolve(strict=False),
        codex_home=ns.codex_home.expanduser().resolve(strict=False),
        verification_root=verification_root,
        disposable_project=disposable_project,
        selected_project=selected_project,
        fixture_codex_home=fixture_codex_home,
        live_evidence_path=(
            ns.live_evidence.expanduser().absolute()
            if ns.live_evidence is not None
            else verification_root / "live-results.json"
        ),
        three_tier_results_path=(
            ns.three_tier_results.expanduser().resolve(strict=False)
            if ns.three_tier_results is not None
            else None
        ),
    )


def private_output_path(
    path: Path,
    verification_root: Path,
    *,
    requested_root: Path | None = None,
) -> Path | None:
    lexical_root = (requested_root or verification_root).expanduser().absolute()
    candidate = path.expanduser().absolute()
    if (
        lexical_root.is_symlink()
        or lexical_root.resolve(strict=False) != lexical_root
        or has_symlink_component(candidate, lexical_root)
        or has_non_directory_parent_or_non_file_target(candidate, lexical_root)
    ):
        return None
    resolved = candidate.resolve(strict=False)
    return resolved if inside(resolved, verification_root) else None


def verification_root_problem(ctx: Context, requested_root: Path) -> str | None:
    lexical_root = requested_root.expanduser().absolute()
    resolved_root = lexical_root.resolve(strict=False)
    if lexical_root.is_symlink() or resolved_root != lexical_root:
        return "Verification root must not contain symlinked path components."
    canonical_root = (ctx.codex_home / "sdlc-verification").resolve(strict=False)
    broad_roots = {
        Path("/").resolve(strict=False),
        Path.home().resolve(strict=False),
        ctx.codex_home.resolve(strict=False),
        ctx.repo_root.resolve(strict=False),
        ctx.skills_root.resolve(strict=False),
        Path.cwd().resolve(strict=False),
    }
    if resolved_root in broad_roots or inside(resolved_root, ctx.repo_root):
        return "Verification root must be a dedicated directory outside the source repository."
    if resolved_root.exists() and not resolved_root.is_dir():
        return "Verification root exists but is not a directory."
    marker = resolved_root / VERIFICATION_ROOT_MARKER
    marker_value: Any = None
    if marker.exists() or marker.is_symlink():
        if (
            marker.is_symlink()
            or not marker.is_file()
            or (os.name == "posix" and marker.stat().st_mode & 0o077)
        ):
            return "Verification-root ownership marker is unsafe."
        try:
            marker_value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return "Verification-root ownership marker is invalid."
        if marker_value != {"schema": VERIFICATION_ROOT_MARKER_SCHEMA}:
            return "Verification-root ownership marker is invalid."
    if resolved_root.exists() and resolved_root != canonical_root:
        if marker_value is None:
            return (
                "Existing custom verification root is not owned by sdlc-workflow-test."
            )
    return None


def prepare_verification_root(ctx: Context, requested_root: Path) -> str | None:
    problem = verification_root_problem(ctx, requested_root)
    if problem:
        return problem
    ensure_private_directory(ctx.verification_root)
    marker = ctx.verification_root / VERIFICATION_ROOT_MARKER
    if marker.is_symlink():
        return "Verification-root ownership marker must not be a symlink."
    if not marker.exists():
        write_private_json(marker, {"schema": VERIFICATION_ROOT_MARKER_SCHEMA})
    return None


def check_design(ctx: Context) -> None:
    text = read_text(ctx.design_path)
    if not text:
        ctx.add(
            "Environment checked",
            "Design document",
            "FAIL",
            f"Missing or unreadable: {ctx.design_path}",
        )
        return
    required_terms = [
        "There is no workflow CLI",
        "sdlc-start",
        "sdlc-auto-steering",
        "sdlc-update-documents",
        "sdlc-prepare-execution",
        "Feature execution plane",
        "one direct-child commit",
        "WORKFLOW_UPGRADE_REQUIRED",
        "steering/auto-steering.json",
        "documents.md",
        "requirements-change",
        "design-change",
        "project-agent-instructions-change",
        "docs-update",
        "PreToolUse",
        "Stop",
        "Private local run state",
        "Resume And Idempotency",
        "Workflow Verification",
        "Quick preflight test",
        "Full workflow test",
        "Real three-tier application test",
        "$sdlc-workflow-test",
        "--resume",
        "$sdlc-start",
        "$sdlc-start workspace init [project-folder]",
        "$sdlc-start run <prompt-path-or-unique-filename>",
        "agentic-sdlc/prompt-v1",
        "ALREADY_COMPLETE",
        "allow_implicit_invocation: false",
        "~/.codex/sdlc-verification/report.md",
        "does not deny filesystem targets by path",
        "Ordinary outbound network commands",
        "secret-bearing",
        "MCP payloads",
        "guarded Git or GitHub actions",
        "source-installed parity",
        "exact manual",
        "task-recover",
        "replan-future",
        "sequential fallback",
        "v2 outer",
        "verification-live-results-v3",
        "predefined runtime operational criterion",
        "non-Grafana provenance",
        "installed `nebius-grafana-query`",
        "installed\n  `project-agent-instructions`",
        "publication-only mode",
        "findings-and-readiness-only mode",
        'phase: "create-pr"',
        'uat_status: "passed"',
    ]
    missing = [term for term in required_terms if term not in text]
    status = "PASS" if not missing else "FAIL"
    detail = "Design document contains core SDLC and verification contract terms."
    if missing:
        detail = "Missing expected design terms: " + ", ".join(missing)
    ctx.add("Environment checked", "Design contract", status, detail)


def check_vertical_slice_contract(ctx: Context) -> None:
    checks = {
        "sdlc-create-design template": (
            ctx.skills_root
            / "sdlc-create-design"
            / "assets"
            / "templates"
            / "design.md.template",
            [
                "### End-To-End Feature Flow",
                "### Layer Map",
                "#### Feature End-To-End Flow",
                "#### Feature Layer Map",
                "- Vertical slice:",
            ],
        ),
        "sdlc-create-plan skill": (
            ctx.skills_root / "sdlc-create-plan" / "SKILL.md",
            [
                "vertical end-to-end feature slices",
                "plan one end-to-end slice",
                "Plan identifies the end-to-end slice",
            ],
        ),
        "sdlc-create-plan template": (
            ctx.skills_root
            / "sdlc-create-plan"
            / "assets"
            / "templates"
            / "feature-plan.md.template",
            [
                "# FEAT-<id> Plan v<N>",
                "## End-To-End Slice",
                "Layer flow: <frontend -> API -> service -> database, or N/A>",
                "Cross-layer validation target: <expected observable result>",
            ],
        ),
        "sdlc-implement-plan skill": (
            ctx.skills_root / "sdlc-implement-plan" / "SKILL.md",
            [
                "vertical end-to-end slice",
                "without widening feature scope",
                "instead of broadening scope",
            ],
        ),
        "sdlc-gather-context skill": (
            ctx.skills_root / "sdlc-gather-context" / "SKILL.md",
            [
                "vertical slice",
                "layer owners",
                "boundary contracts",
            ],
        ),
        "sdlc-gather-context template": (
            ctx.skills_root
            / "sdlc-gather-context"
            / "assets"
            / "templates"
            / "context-pack.md.template",
            [
                "## Layer And Boundary Context",
                "Layer Or Boundary",
                "Contract Or Gap",
            ],
        ),
        "sdlc-tdd skill": (
            ctx.skills_root / "sdlc-tdd" / "SKILL.md",
            [
                "planned end-to-end slice",
                "layer contracts",
                "cross-layer validation target",
            ],
        ),
        "sdlc-validate-codes skill": (
            ctx.skills_root / "sdlc-validate-codes" / "SKILL.md",
            [
                "locked-slice boundary validation",
                "End-To-End Slice",
                "implementation stayed inside the planned layers",
            ],
        ),
        "sdlc-validate-codes template": (
            ctx.skills_root
            / "sdlc-validate-codes"
            / "assets"
            / "templates"
            / "validate.md.template",
            [
                "## Slice Boundary Check",
                "Locked End-To-End Slice",
                "Changed Files Within Planned Layers",
            ],
        ),
        "sdlc-unit-tests skill": (
            ctx.skills_root / "sdlc-unit-tests" / "SKILL.md",
            [
                "planned end-to-end slice",
                "cross-layer validation target",
                "Planned slice coverage passes",
            ],
        ),
        "sdlc-unit-tests template": (
            ctx.skills_root
            / "sdlc-unit-tests"
            / "assets"
            / "templates"
            / "tests.md.template",
            [
                "## End-To-End Slice Coverage",
                "Slice Element",
                "layer contract / boundary / validation target / N/A",
            ],
        ),
        "sdlc-evaluate skill": (
            ctx.skills_root / "sdlc-evaluate" / "SKILL.md",
            [
                "planned end-to-end slice",
                "Layer-isolated checks alone",
                "Planned end-to-end slice observation",
                "$nebius-grafana-query",
                "predefined runtime operational criterion",
                "query-admission record for one criterion",
                "non-Grafana provenance",
                "structured `criterion_fit`",
                "at most one data query per provider invocation",
                "Remaining query budget is a ceiling, not a target",
                "Passive read-only production telemetry",
                "`pass`, `fail`, or `inconclusive`",
            ],
        ),
        "sdlc-evaluate template": (
            ctx.skills_root
            / "sdlc-evaluate"
            / "assets"
            / "templates"
            / "evaluate.md.template",
            [
                "## End-To-End Slice Observation",
                "Layer Boundaries Exercised",
                "Cross-Layer Result",
                "## Observability Evidence",
                "Admitted Criterion ID",
                "Signal Family And Candidate",
                "Signal Provenance",
                "Candidate Selector Attribution",
                "Baseline Or Control Selector Attribution",
                "Pass Condition",
                "Fail Condition",
                "Inconclusive Conditions",
                "Grade-Change Relation",
                "Observed Comparison",
                "Criterion Interpretation",
                "Connectivity Checks",
                "Remaining Query Budget",
                "Remaining Fast Query Budget",
                "Remaining Deep Query Budget",
                "Provider Stages Used",
                "<pass, fail, inconclusive>",
            ],
        ),
        "sdlc-update-documents skill": (
            ctx.skills_root / "sdlc-update-documents" / "SKILL.md",
            [
                "evaluated end-to-end slice evidence",
                "multi-layer behavior",
                "route the gap back to `sdlc-evaluate`",
            ],
        ),
        "sdlc-update-documents template": (
            ctx.skills_root
            / "sdlc-update-documents"
            / "assets"
            / "templates"
            / "documents.md.template",
            [
                "End-to-end slice",
                "Evaluation:",
                "Source evidence",
            ],
        ),
        "sdlc-align-specs skill": (
            ctx.skills_root / "sdlc-align-specs" / "SKILL.md",
            [
                "end-to-end slice evidence",
                "Vertical flow, layer map, locked slice",
                "Slice mismatch maps to the earliest owner",
            ],
        ),
    }
    for name, (path, terms) in checks.items():
        text = read_text(path)
        if not text:
            ctx.add(
                "Environment checked", name, "FAIL", f"Missing or unreadable: {path}"
            )
            continue
        missing = [term for term in terms if term not in text]
        status = "PASS" if not missing else "FAIL"
        detail = f"Vertical slice contract terms present in {path}."
        if missing:
            detail = "Missing expected vertical slice terms: " + ", ".join(missing)
        ctx.add("Environment checked", name, status, detail)


def check_execution_plane_contract(ctx: Context) -> None:
    checks = {
        "execution preparation skill": (
            ctx.skills_root / "sdlc-prepare-execution" / "SKILL.md",
            [
                "integration branch/worktree",
                "deterministic task waves",
                "WORKFLOW_UPGRADE_REQUIRED",
            ],
        ),
        "execution plane reference": (
            ctx.skills_root
            / "sdlc-prepare-execution"
            / "references"
            / "execution-plane.md",
            [
                "one fresh worker agent per task",
                "sequential `codex exec` fallback",
                "task-recover",
                "replan-future",
                "git merge --no-ff --no-edit",
                "git merge --ff-only",
                "agentic-sdlc/execution-coordinator-v4",
            ],
        ),
        "locked plan task graph": (
            ctx.skills_root
            / "sdlc-create-plan"
            / "assets"
            / "templates"
            / "feature-plan.md.template",
            [
                "## Task Graph",
                "### TASK-001",
                "Write claims:",
                "Conflict domains:",
                "## Planned Dependency Waves",
            ],
        ),
        "implementation wave coordinator": (
            ctx.skills_root / "sdlc-implement-plan" / "SKILL.md",
            [
                "Every `TASK-*` must use its own fresh agent",
                "wave-integrate",
                "wave-complete",
                "one direct-child commit",
                "--ephemeral",
                "workspace-write",
            ],
        ),
        "workflow state v2 with execution coordinator v4": (
            ctx.skills_root / "sdlc-start" / "references" / "state-schema.md",
            [
                '"state_version": 2',
                "agentic-sdlc/execution-coordinator-v4",
                "sdlc-prepare-execution",
                "execution/FEAT-001/coordinator.json",
                "sessions/<session-hash>.json",
                'phase: "create-pr"',
                "expected_head",
                'uat_status: "passed"',
                "WORKFLOW_UPGRADE_REQUIRED",
            ],
        ),
        "active SDLC PR publication mode": (
            ctx.skills_root / "create-pr" / "SKILL.md",
            [
                "Active Agentic SDLC Publication Mode",
                "publication-only mode",
                "promoted_head",
                "PR_HEAD_DRIFT",
                "sdlc-classify-failure",
                "expected_head: <promoted_head>",
                'uat_status: "passed"',
                "gh pr create --head <branch>",
                "Do not use a shell wrapper",
            ],
        ),
        "active SDLC PR review mode": (
            ctx.skills_root / "review-pr" / "SKILL.md",
            [
                "Active Agentic SDLC Review Mode",
                "findings-and-readiness-only",
                "PR_HEAD_DRIFT",
                "Do not check out or switch branches",
                "sdlc-classify-failure",
                "sdlc-merge-pr",
            ],
        ),
        "active SDLC exact merge mode": (
            ctx.skills_root / "sdlc-merge-pr" / "SKILL.md",
            [
                "exact promoted and reviewed SHA",
                "--match-head-commit <promoted-sha>",
                "exact_command",
                "explicit_user_request: true",
                "explicit PR number or URL",
                "--admin",
                "--delete-branch",
                "compound merge command",
                "does not use a GitHub merge MCP call",
            ],
        ),
        "guarded PR creation actions": (
            ctx.skills_root
            / "sdlc-start"
            / "assets"
            / "hooks"
            / "lib"
            / "sdlc_policy.py",
            [
                "is_gh_pr_create",
                "is_github_pr_create_tool",
                "explicit_current_branch_refspec",
                "gh_pr_create_head_matches",
                "is_github_pr_merge_tool",
                "canonical_gh_pr_merge_target",
                "merge_auth_valid",
                "sensitive_command_wrapper_reason",
                "expected_checks_status",
                "expected_review_status",
                "pr_auth_valid",
                'expected_phase="create-pr"',
                'expected_uat_status="passed"',
            ],
        ),
        "failure taxonomy": (
            ctx.skills_root
            / "sdlc-classify-failure"
            / "references"
            / "failure-taxonomy.md",
            [
                "DOCUMENTATION_DRIFT",
                "PR_HEAD_DRIFT",
                "coordinator schema v1, v2, or v3",
                "sdlc-update-documents, then sdlc-align-specs",
            ],
        ),
    }
    for name, (path, terms) in checks.items():
        text = read_text(path)
        missing = [term for term in terms if term not in text]
        status = "PASS" if text and not missing else "FAIL"
        detail = f"Execution-plane contract terms present in {path}."
        if not text:
            detail = f"Missing or unreadable: {path}"
        elif missing:
            detail = "Missing expected execution-plane terms: " + ", ".join(missing)
        ctx.add("Environment checked", name, status, detail)


def check_repair_loop_contract(ctx: Context) -> None:
    checks = {
        "repair-control helper": (
            ctx.skills_root
            / "sdlc-classify-failure"
            / "scripts"
            / "repair_control.py",
            [
                "agentic-sdlc/failure-event-v1",
                "agentic-sdlc/diagnosis-v1",
                "agentic-sdlc/design-approval-v1",
                "agentic-sdlc/repair-control-v1",
                "agentic-sdlc/revalidation-evidence-v1",
                "blocked_missing_evidence",
                "localized_limit",
                "design_limit",
                "feature_dispatch_limit",
                "blocked_semantic_cycle",
            ],
        ),
        "conditional troubleshooting contract": (
            ctx.skills_root / "troubleshoot" / "SKILL.md",
            [
                "Agentic SDLC Diagnostic Mode",
                "Return one `diagnosis-v1` to `sdlc-classify-failure`",
                "Do not commit product fixes",
                "No implementation bug found",
            ],
        ),
        "corrective plan contract": (
            ctx.skills_root / "sdlc-create-plan" / "SKILL.md",
            [
                "corrective plan vN+1",
                "Preserve every existing task definition byte-for-byte",
                "Append contiguous corrective `TASK-*` IDs",
                "original regression oracle",
            ],
        ),
        "design admission contract": (
            ctx.skills_root / "sdlc-create-design" / "SKILL.md",
            [
                "validated design-admission record",
                "localized implementation",
                "durable human approval",
                "missing implementation evidence",
            ],
        ),
        "coordinator repair pointer": (
            ctx.skills_root / "sdlc-start" / "references" / "state-schema.md",
            [
                "agentic-sdlc/repair-state-pointer-v1",
                "repair-control-v1",
                "conditional diagnostic branch",
                "60 active",
                "four repair dispatches",
                "revalidation-cursor-v1",
            ],
        ),
        "corrective execution assignment": (
            ctx.skills_root
            / "sdlc-prepare-execution"
            / "scripts"
            / "sdlc_execution_core.py",
            [
                "diagnosis_id",
                "regression_oracle",
                "regression-oracle-evidence-v1",
                "_task_definition_digest",
                "replacement plan changes a completed or active task definition",
            ],
        ),
    }
    for name, (path, terms) in checks.items():
        content = read_text(path)
        missing = [term for term in terms if term not in content]
        status = "PASS" if content and not missing else "FAIL"
        detail = f"Repair-loop contract terms present in {path}."
        if not content:
            detail = f"Missing or unreadable: {path}"
        elif missing:
            detail = "Missing expected repair-loop terms: " + ", ".join(missing)
        ctx.add("Environment checked", name, status, detail)


def check_skill_discovery(ctx: Context) -> None:
    base = ctx.global_skills_dir
    ctx.add(
        "Skill discovery results",
        "Global skills directory",
        "PASS" if base.is_dir() else "FAIL",
        str(base),
    )
    names: dict[str, list[Path]] = {}
    for folder in sorted(base.iterdir()) if base.is_dir() else []:
        skill_md = folder / "SKILL.md"
        if not skill_md.is_file():
            continue
        meta = frontmatter(skill_md)
        name = meta.get("name", "")
        if name:
            names.setdefault(name, []).append(folder)

    for required in REQUIRED_SDLC_SKILLS:
        folder = base / required
        skill_md = folder / "SKILL.md"
        if not folder.is_dir():
            ctx.add(
                "Skill discovery results", required, "FAIL", f"Missing folder: {folder}"
            )
            continue
        if not skill_md.is_file():
            ctx.add(
                "Skill discovery results",
                required,
                "FAIL",
                f"Missing SKILL.md: {skill_md}",
            )
            continue
        meta = frontmatter(skill_md)
        name = meta.get("name", "")
        description = meta.get("description", "")
        problems: list[str] = []
        if name != required:
            problems.append(f"name is {name!r}, expected {required!r}")
        if not description:
            problems.append("missing description")
        elif not description.startswith(DESCRIPTION_PREFIX):
            problems.append("description does not start with SDLC-only prefix")
        policy = openai_invocation_policy(folder / "agents" / "openai.yaml")
        if policy != "false":
            problems.append(
                "agents/openai.yaml policy.allow_implicit_invocation is not false"
            )
        status = "PASS" if not problems else "FAIL"
        detail = (
            "SKILL.md name, SDLC trigger description, and explicit-only "
            "invocation policy are valid."
        )
        if problems:
            detail = "; ".join(problems)
        ctx.add("Skill discovery results", required, status, detail)

    duplicate_sdlc = {
        name: paths
        for name, paths in names.items()
        if name.startswith("sdlc-") and len(paths) > 1
    }
    if duplicate_sdlc:
        detail = "; ".join(
            f"{name}: {', '.join(str(p) for p in paths)}"
            for name, paths in duplicate_sdlc.items()
        )
        ctx.add("Skill discovery results", "Duplicate SDLC names", "FAIL", detail)
    else:
        ctx.add(
            "Skill discovery results",
            "Duplicate SDLC names",
            "PASS",
            "No duplicate sdlc-* names found.",
        )

    support_checks = {
        "worktree": (
            "Managed worktree runtime dependency",
            "runtime.worktree-dependency",
        ),
        "nebius-grafana-query": (
            "Observability evidence runtime dependency",
            "runtime.observability-dependency",
        ),
        "troubleshoot": (
            "Conditional diagnosis runtime dependency",
            "runtime.troubleshoot-dependency",
        ),
        "project-agent-instructions": (
            "Project agent instructions runtime dependency",
            "runtime.project-agent-instructions-dependency",
        ),
    }
    for support_name in REQUIRED_RUNTIME_SUPPORT_SKILLS:
        support = ctx.global_skills_dir / support_name
        check_name, capability_id = support_checks[support_name]
        available = (support / "SKILL.md").is_file()
        ctx.add(
            "Skill discovery results",
            check_name,
            "PASS" if available else "FAIL",
            f"Installed {support_name} support skill is available."
            if available
            else f"Missing runtime dependency: {support}",
            capability_id=capability_id,
        )

    mismatches: list[str] = []
    unsafe_symlinks: list[str] = []
    for name in SOURCE_PARITY_SKILLS:
        source = ctx.skills_root / name
        installed = ctx.global_skills_dir / name
        source_digest = tree_digest(source)
        installed_digest = tree_digest(installed)
        if source_digest.startswith("unsafe-symlink:"):
            unsafe_symlinks.append(f"{name} source")
        if installed_digest.startswith("unsafe-symlink:"):
            unsafe_symlinks.append(f"{name} installed")
        if source.resolve(strict=False) == installed.resolve(strict=False):
            continue
        if source_digest != installed_digest:
            mismatches.append(name)
    ctx.add(
        "Skill discovery results",
        "Source and installed runtime parity",
        "PASS" if not mismatches and not unsafe_symlinks else "FAIL",
        "Required source skills match installed runtime copies."
        if not mismatches and not unsafe_symlinks
        else "; ".join(
            detail
            for detail in (
                "Source and installed copies differ: " + ", ".join(mismatches)
                if mismatches
                else "",
                "Skill trees contain symlinks: " + ", ".join(unsafe_symlinks)
                if unsafe_symlinks
                else "",
            )
            if detail
        ),
        capability_id="runtime.skill-parity",
    )


def load_hooks_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_toml_hooks(path: Path) -> dict[str, Any] | None:
    try:
        import tomllib
    except ModuleNotFoundError:
        return None
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    hooks = value.get("hooks", {})
    if not isinstance(hooks, dict):
        return None
    return {event: groups for event, groups in hooks.items() if event != "state"}


def flatten_hook_commands(
    hooks: Any,
) -> tuple[list[tuple[str, str]], str | None]:
    commands: list[tuple[str, str]] = []
    if not isinstance(hooks, dict):
        return commands, "hooks must be an object"
    for event, groups in hooks.items():
        if not isinstance(event, str) or not event:
            return commands, "hook event names must be non-empty strings"
        groups_iter: list[Any]
        if isinstance(groups, dict):
            groups_iter = [groups]
        elif isinstance(groups, list):
            groups_iter = groups
        else:
            return commands, f"hook event {event!r} must contain a group or list"
        for group in groups_iter:
            if not isinstance(group, dict) or "hooks" not in group:
                return commands, f"hook event {event!r} contains an invalid group"
            entries = group["hooks"]
            if isinstance(entries, dict):
                entries = [entries]
            if not isinstance(entries, list):
                return commands, f"hook event {event!r} has a non-list hooks value"
            for entry in entries:
                if not isinstance(entry, dict):
                    return commands, f"hook event {event!r} contains an invalid entry"
                if entry.get("type") != "command":
                    return (
                        commands,
                        f"hook event {event!r} contains a non-command entry",
                    )
                command = entry.get("command")
                if not isinstance(command, str) or not command.strip():
                    return commands, f"hook event {event!r} has an invalid command"
                commands.append((event, command))
    return commands, None


def hook_command_targets(
    command: str,
    expected_path: Path,
    *,
    codex_home: Path,
) -> bool:
    expanded = command.replace("${CODEX_HOME:-$HOME/.codex}", str(codex_home)).replace(
        "${CODEX_HOME}", str(codex_home)
    )
    expanded = expanded.replace("$CODEX_HOME", str(codex_home))
    if any(token in expanded for token in ("\n", "\r", "$(", "`")):
        return False
    try:
        words = shlex.split(expanded)
    except ValueError:
        return False
    shell_controls = {";", "&&", "||", "|", "&", ">", ">>", "<", "<<"}
    if not words or any(word in shell_controls for word in words):
        return False
    expected_root = expected_path.parent
    if expected_root.is_symlink() or expected_path.is_symlink():
        return False
    expected = expected_path.resolve(strict=False)
    if (
        Path(words[0]).name != words[0]
        or re.fullmatch(r"python(?:3(?:\.\d+)?)?", words[0]) is None
    ):
        return False
    arguments = words[1:]
    option_values = {"-W", "-X"}
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in {"-c", "-m"}:
            return False
        if argument in option_values:
            index += 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        candidate = Path(argument).expanduser()
        return (
            index == len(arguments) - 1
            and candidate.is_absolute()
            and candidate.resolve(strict=False) == expected
            and not has_symlink_component(candidate, expected_root)
        )
    return False


def check_hook_config(ctx: Context) -> None:
    hooks_sources: list[tuple[Path, dict[str, Any]]] = []
    malformed_sources: list[Path] = []
    hooks_json = ctx.codex_home / "hooks.json"
    config_toml = ctx.codex_home / "config.toml"
    if hooks_json.exists():
        parsed = load_hooks_json(hooks_json)
        hooks = parsed.get("hooks", {}) if parsed is not None else None
        if isinstance(hooks, dict):
            hooks_sources.append((hooks_json, hooks))
        else:
            malformed_sources.append(hooks_json)
    if config_toml.exists():
        hooks = load_toml_hooks(config_toml)
        if hooks is None:
            malformed_sources.append(config_toml)
        else:
            hooks_sources.append((config_toml, hooks))
    for source in malformed_sources:
        ctx.add(
            "Hook configuration results",
            f"Hook source {source.name}",
            "FAIL",
            f"Hook configuration is malformed or unreadable: {source}",
            capability_id="hooks.registration",
        )
    if not hooks_sources and not malformed_sources:
        ctx.add(
            "Hook configuration results",
            "Hook config source",
            "WARN",
            f"Optional SDLC hook registration is not configured under {ctx.codex_home}",
            capability_id="hooks.registration",
        )
        return

    all_commands: list[tuple[str, str]] = []
    for source, hooks in hooks_sources:
        source_commands, problem = flatten_hook_commands(hooks)
        if problem is not None:
            ctx.add(
                "Hook configuration results",
                f"Hook source {source.name}",
                "FAIL",
                f"Hook configuration is malformed: {problem}",
                capability_id="hooks.registration",
            )
            continue
        all_commands.extend(source_commands)
        ctx.add(
            "Hook configuration results",
            f"Hook source {source.name}",
            "PASS",
            f"{len(source_commands)} command hook(s) discovered in {source}",
        )

    def registration(event: str, filename: str, label: str) -> tuple[bool, list[str]]:
        matching = [
            command
            for hook_event, command in all_commands
            if hook_event == event and filename in command
        ]
        expected = ctx.codex_home / "hooks" / filename
        valid = [
            command
            for command in matching
            if hook_command_targets(command, expected, codex_home=ctx.codex_home)
        ]
        invalid = [command for command in matching if command not in valid]
        if invalid:
            ctx.add(
                "Hook configuration results",
                label,
                "FAIL",
                f"{len(invalid)} configured entrypoint(s) do not target the canonical CODEX_HOME hook payload.",
                capability_id="hooks.registration",
            )
        elif valid:
            ctx.add(
                "Hook configuration results",
                label,
                "PASS",
                "Configured entrypoint targets the canonical CODEX_HOME hook payload.",
                capability_id="hooks.registration",
            )
        else:
            ctx.add(
                "Hook configuration results",
                label,
                "WARN",
                "Optional SDLC hook is not configured.",
                capability_id="hooks.registration",
            )
        return bool(valid), invalid

    pre_found, _ = registration(
        "PreToolUse",
        "pre_tool_use_sdlc_policy.py",
        "PreToolUse SDLC hook configured",
    )
    stop_found, _ = registration(
        "Stop", "stop_sdlc_continue.py", "Stop SDLC hook configured"
    )
    session_found = any(event == "SessionStart" for event, _ in all_commands)
    ctx.add(
        "Hook configuration results",
        "SessionStart preserved",
        "PASS" if session_found else "WARN",
        "SessionStart hook exists."
        if session_found
        else "No SessionStart hook command discovered.",
    )
    user_prompt_commands = [
        command for event, command in all_commands if event == "UserPromptSubmit"
    ]
    if user_prompt_commands and any(
        "sdlc" in command.lower() for command in user_prompt_commands
    ):
        ctx.add(
            "Hook configuration results",
            "UserPromptSubmit SDLC routing",
            "FAIL",
            "UserPromptSubmit command appears to mention SDLC routing.",
        )
    else:
        detail = "UserPromptSubmit hooks do not mention SDLC routing."
        if not user_prompt_commands:
            detail = "No UserPromptSubmit hook command discovered."
        ctx.add(
            "Hook configuration results",
            "UserPromptSubmit SDLC routing",
            "PASS",
            detail,
        )

    if pre_found or stop_found:
        source_hook_root = ctx.skills_root / "sdlc-start" / "assets" / "hooks"
        installed_hook_root = ctx.codex_home / "hooks"
        hook_files = [
            "lib/__init__.py",
            "lib/sdlc_policy.py",
            "lib/sdlc_state.py",
        ]
        if pre_found:
            hook_files.append("pre_tool_use_sdlc_policy.py")
        if stop_found:
            hook_files.append("stop_sdlc_continue.py")
        mismatches: list[str] = []
        unsafe_symlinks: list[str] = []
        for relative in hook_files:
            source_path = source_hook_root / relative
            installed_path = installed_hook_root / relative
            if source_hook_root.is_symlink() or has_symlink_component(
                source_path, source_hook_root
            ):
                unsafe_symlinks.append(f"source:{relative}")
            if installed_hook_root.is_symlink() or has_symlink_component(
                installed_path, installed_hook_root
            ):
                unsafe_symlinks.append(f"installed:{relative}")
            if file_digest(source_path) != file_digest(installed_path):
                mismatches.append(relative)
        ctx.add(
            "Hook configuration results",
            "Configured hook payload parity",
            "PASS" if not mismatches and not unsafe_symlinks else "FAIL",
            "Configured SDLC hook payload matches source."
            if not mismatches and not unsafe_symlinks
            else "; ".join(
                detail
                for detail in (
                    "Configured hook payload differs or is missing: "
                    + ", ".join(mismatches)
                    if mismatches
                    else "",
                    "Configured hook payload contains symlinks: "
                    + ", ".join(unsafe_symlinks)
                    if unsafe_symlinks
                    else "",
                )
                if detail
            ),
            capability_id="hooks.payload-parity",
        )


def git(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=project, timeout=15)


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)


def expected_verification_context(ctx: Context, baseline_head: str) -> dict[str, Any]:
    return {
        "schema": VERIFICATION_CONTEXT_SCHEMA,
        "verification_id": verification_id(ctx, baseline_head),
        "project_root": str(ctx.selected_project),
        "git_root": str(ctx.disposable_project),
        "baseline_head": baseline_head,
        "live_results": str(ctx.live_evidence_path),
    }


def valid_verification_context(ctx: Context, value: Any, *, current_head: str) -> bool:
    required = {
        "schema",
        "verification_id",
        "project_root",
        "git_root",
        "baseline_head",
        "live_results",
    }
    if not isinstance(value, dict) or set(value) != required:
        return False
    baseline = str(value.get("baseline_head") or "")
    if (
        value.get("schema") != VERIFICATION_CONTEXT_SCHEMA
        or value.get("project_root") != str(ctx.selected_project)
        or value.get("git_root") != str(ctx.disposable_project)
        or value.get("live_results") != str(ctx.live_evidence_path)
        or SHA_RE.fullmatch(baseline) is None
        or value.get("verification_id") != verification_id(ctx, baseline)
    ):
        return False
    ancestry = run(
        ["git", "merge-base", "--is-ancestor", baseline, current_head],
        cwd=ctx.disposable_project,
        timeout=15,
    )
    return ancestry.returncode == 0


def setup_disposable_project(ctx: Context) -> None:
    project = ctx.disposable_project
    if project.is_symlink() or (project.exists() and not project.is_dir()):
        ctx.add(
            "Disposable SDLC golden-path run results",
            "Disposable project",
            "FAIL",
            "Disposable project root is symlinked or is not a directory; no project files were changed.",
        )
        return
    git_path = project / ".git"
    if project.exists():
        try:
            non_empty = next(project.iterdir(), None) is not None
        except OSError:
            non_empty = True
        if non_empty and not git_path.is_dir():
            ctx.add(
                "Disposable SDLC golden-path run results",
                "Disposable project",
                "FAIL",
                "Existing non-Git disposable directory is not verifier-owned; no project files were changed.",
            )
            return
    project.mkdir(parents=True, exist_ok=True)
    files = {
        DISPOSABLE_FIXTURE_MARKER: DISPOSABLE_FIXTURE_MARKER_CONTENT,
        "README.md": "# Disposable SDLC Verification Project\n",
        "services/resource-validator/pyproject.toml": (
            '[project]\nname = "sdlc-verification-project"\nversion = "0.0.0"\n'
        ),
        "services/resource-validator/src/resource_name.py": (
            '"""Disposable verification module."""\n'
        ),
        "services/resource-validator/tests/test_resource_name.py": (
            "def test_placeholder():\n    assert True\n"
        ),
        "services/resource-validator/docs/.gitkeep": "",
        "services/unrelated/README.md": "# Unrelated sibling fixture\n",
    }
    old_files = {
        "pyproject.toml": (
            '[project]\nname = "sdlc-verification-project"\nversion = "0.0.0"\n'
        ),
        "src/resource_name.py": '"""Disposable verification module."""\n',
        "tests/test_resource_name.py": "def test_placeholder():\n    assert True\n",
        "docs/.gitkeep": "",
    }
    old_fixture_files = {
        "README.md": "# Disposable SDLC Verification Project\n",
        **old_files,
    }
    fixture_targets = [
        project / relative for relative in set(files).union(old_fixture_files)
    ]

    def unsafe_fixture_target(path: Path) -> bool:
        current = project
        relative = path.relative_to(project)
        for part in relative.parts[:-1]:
            current = current / part
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                return True
        return path.is_symlink() or (path.exists() and not path.is_file())

    unsafe_targets = [path for path in fixture_targets if unsafe_fixture_target(path)]
    if (
        git_path.is_symlink()
        or (git_path.exists() and not git_path.is_dir())
        or unsafe_targets
    ):
        ctx.add(
            "Disposable SDLC golden-path run results",
            "Disposable project",
            "FAIL",
            "Disposable fixture contains symlinked or non-canonical paths; no project files were changed.",
        )
        return
    existing_repository = (project / ".git").exists()
    tracked_files: set[str] = set()
    remotes: list[str] = []
    if existing_repository:
        tracked = git(project, "ls-files", "-z")
        remote_result = git(project, "remote")
        if tracked.returncode != 0 or remote_result.returncode != 0:
            ctx.add(
                "Disposable SDLC golden-path run results",
                "Disposable project",
                "FAIL",
                "Existing disposable project ownership could not be verified.",
            )
            return
        tracked_files = {path for path in tracked.stdout.split("\0") if path}
        remotes = [line for line in remote_result.stdout.splitlines() if line]
    old_fixture = (
        existing_repository
        and not remotes
        and tracked_files == set(old_fixture_files)
        and all(
            read_text(project / relative) == content
            for relative, content in old_fixture_files.items()
        )
    )
    owned_fixture = (
        existing_repository
        and not remotes
        and read_text(project / DISPOSABLE_FIXTURE_MARKER)
        == DISPOSABLE_FIXTURE_MARKER_CONTENT
    )
    if existing_repository:
        before = git(project, "status", "--porcelain", "--untracked-files=all")
        dirty_lines = [line for line in before.stdout.splitlines() if line]
        if before.returncode != 0 or dirty_lines:
            ctx.add(
                "Disposable SDLC golden-path run results",
                "Disposable project",
                "FAIL",
                "Existing disposable project is dirty; unknown changes were preserved.",
            )
            return
        if remotes or not (old_fixture or owned_fixture):
            ctx.add(
                "Disposable SDLC golden-path run results",
                "Disposable project",
                "FAIL",
                "Existing clean Git repository is not a canonical verifier-owned fixture; no project files were changed.",
            )
            return
    for rel, content in files.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    if old_fixture:
        for relative, content in old_files.items():
            path = project / relative
            if path.is_file() and read_text(path) == content:
                path.unlink()
        for relative in ("src", "tests", "docs"):
            try:
                (project / relative).rmdir()
            except OSError:
                pass
    if not existing_repository:
        init = git(project, "init", "-b", "main")
        if init.returncode != 0:
            git(project, "init")
            git(project, "branch", "-m", "main")
        git(project, "config", "user.email", "sdlc-verification@example.invalid")
        git(project, "config", "user.name", "SDLC Verification")
        git(project, "add", ".")
        commit_message = "initial disposable verification project"
    else:
        git(
            project,
            "add",
            "-A",
            "--",
            "README.md",
            DISPOSABLE_FIXTURE_MARKER,
            "pyproject.toml",
            "src",
            "tests",
            "docs",
            "services",
        )
        commit_message = "align disposable nested project fixture"
    staged = git(project, "diff", "--cached", "--quiet")
    if staged.returncode == 1:
        commit = git(project, "commit", "-m", commit_message)
        if commit.returncode != 0:
            ctx.add(
                "Disposable SDLC golden-path run results",
                "Disposable project commit",
                "FAIL",
                "Disposable fixture commit failed.",
            )
    status = git(project, "status", "--porcelain", "--untracked-files=all")
    clean = status.returncode == 0 and status.stdout == ""
    canonical_marker = (
        read_text(project / DISPOSABLE_FIXTURE_MARKER)
        == DISPOSABLE_FIXTURE_MARKER_CONTENT
    )
    ctx.add(
        "Disposable SDLC golden-path run results",
        "Disposable project",
        "PASS"
        if project.is_dir()
        and ctx.selected_project.is_dir()
        and canonical_marker
        and clean
        else "FAIL",
        (
            f"Git root exists at {project}; selected nested project is "
            f"{ctx.selected_project}; owned: {canonical_marker}; clean: {clean}"
        ),
    )

    baseline_head = git_output(project, "rev-parse", "HEAD")
    if baseline_head and SHA_RE.fullmatch(baseline_head):
        context_path = ctx.verification_root / "verification-context.json"
        try:
            existing_context: Any = json.loads(context_path.read_text(encoding="utf-8"))
        except UnicodeError:
            ctx.add(
                "Disposable SDLC golden-path run results",
                "Verification context",
                "FAIL",
                "Preserved verification context contains invalid UTF-8 and was not modified.",
            )
            return
        except (OSError, json.JSONDecodeError):
            existing_context = None
        if not valid_verification_context(
            ctx, existing_context, current_head=baseline_head
        ):
            if ctx.live_evidence_path.exists():
                ctx.add(
                    "Disposable SDLC golden-path run results",
                    "Verification context",
                    "FAIL",
                    "Live evidence exists but its preserved verification context is invalid.",
                )
            else:
                write_private_json(
                    context_path, expected_verification_context(ctx, baseline_head)
                )
    else:
        ctx.add(
            "Disposable SDLC golden-path run results",
            "Verification context",
            "FAIL",
            "Disposable baseline Git identity is unavailable.",
        )


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def setup_fixture_state(ctx: Context, *, record: bool = True) -> Path:
    run_dir = ctx.fixture_codex_home / "sdlc-runs" / DEFAULT_PROJECT_ID / DEFAULT_RUN_ID
    write_json(
        run_dir.parent / "active.lock",
        {
            "project_id": DEFAULT_PROJECT_ID,
            "project_root": str(ctx.selected_project),
            "run_id": DEFAULT_RUN_ID,
            "status": "running",
        },
    )
    write_json(run_dir.parent / "active-run.json", {"run_id": DEFAULT_RUN_ID})
    prompt_filename = "20260716T000000Z--verify-agentic-sdlc.md"
    write_json(
        run_dir / "run.json",
        {"status": "running", "prompt": {"filename": prompt_filename}},
    )
    write_json(
        run_dir / "prompt.json",
        {
            "schema": "agentic-sdlc/prompt-binding-v1",
            "run_id": DEFAULT_RUN_ID,
            "prompt_id": "prompt-" + "1" * 32,
            "prompt_filename": prompt_filename,
            "revisions": [
                {
                    "revision": "r0001",
                    "sha256": "a" * 64,
                    "snapshot": "inputs/r0001/prompt.md",
                    "steering_status": "initial",
                }
            ],
        },
    )
    write_json(
        run_dir / "current-state.json",
        {
            "project_id": DEFAULT_PROJECT_ID,
            "run_id": DEFAULT_RUN_ID,
            "status": "running",
            "current_feature": "FEAT-001",
            "current_phase": "implementation",
            "next_recommended_skill": "sdlc-validate-codes",
            "retry_counts": {"implementation": 0},
            "iteration_count": 1,
            "max_iterations": 200,
            "needs_human": False,
        },
    )
    write_json(
        run_dir / "feature-queue.json",
        {"features": [{"id": "FEAT-001", "status": "implementation"}]},
    )
    write_json(run_dir / "fingerprints.json", {})
    for rel in ("context", "plans", "evidence/FEAT-001", "history", "permissions"):
        (run_dir / rel).mkdir(parents=True, exist_ok=True)
    (run_dir / "steering").mkdir(parents=True, exist_ok=True)
    plan = run_dir / "plans" / "FEAT-001.plan.v1.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    (run_dir / "plans" / "FEAT-001.plan.v1.md.lock").write_text(
        "locked\n", encoding="utf-8"
    )
    (run_dir / "STEERING.md").write_text("", encoding="utf-8")
    for rel in ("history/continuation-state.json", "history/hook-events.jsonl"):
        try:
            (run_dir / rel).unlink()
        except FileNotFoundError:
            pass
    if record:
        ctx.add(
            "Environment checked",
            "Disposable SDLC state",
            "PASS",
            f"Fixture state created at {run_dir}",
        )
    return run_dir


def check_prompt_workspace(ctx: Context) -> None:
    helper = ctx.skills_root / "sdlc-start" / "scripts" / "prompt_workspace.py"
    tests = ctx.skills_root / "sdlc-start" / "scripts" / "test_prompt_workspace.py"
    reference = ctx.skills_root / "sdlc-start" / "references" / "prompt-workspace.md"
    template = ctx.skills_root / "sdlc-start" / "assets" / "prompt-template.md"
    missing = [
        path for path in (helper, tests, reference, template) if not path.is_file()
    ]
    if missing:
        ctx.add(
            "Idempotency results",
            "Prompt workspace source",
            "FAIL",
            "Missing: " + ", ".join(str(path) for path in missing),
        )
        return
    ctx.add(
        "Idempotency results",
        "Prompt workspace source",
        "PASS",
        "Prompt helper, regression tests, reference, and template are available.",
    )


def parse_unittest_results(output: str) -> tuple[set[str], set[str]]:
    passed: set[str] = set()
    skipped: set[str] = set()
    pattern = re.compile(r"^(test_[A-Za-z0-9_]+).*\.\.\.\s+(ok|skipped\b.*)$")
    ansi_escape = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    for line in output.splitlines():
        match = pattern.match(ansi_escape.sub("", line).strip())
        if not match:
            continue
        name, outcome = match.groups()
        if outcome == "ok":
            passed.add(name)
        else:
            skipped.add(name)
    return passed, skipped


def check_capability_regressions(ctx: Context) -> None:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHON_COLORS"] = "0"
    suites = {
        "prompt": (
            [
                sys.executable,
                "-m",
                "unittest",
                "-v",
                "sdlc-start/scripts/test_prompt_workspace.py",
                "sdlc-start/scripts/test_sdlc_start_contract.py",
            ],
            ctx.skills_root,
        ),
        "execution": (
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-v",
                "-s",
                str(ctx.skills_root / "sdlc-prepare-execution" / "scripts"),
                "-p",
                "test_*.py",
            ],
            ctx.skills_root,
        ),
        "dispatch": (
            [
                sys.executable,
                str(
                    ctx.skills_root
                    / "sdlc-implement-plan"
                    / "scripts"
                    / "test_worker_dispatch.py"
                ),
                "-v",
            ],
            ctx.skills_root,
        ),
        "worktree": (
            [sys.executable, "scripts/test-worktree-manager.py", "-v"],
            ctx.skills_root / "worktree",
        ),
        "task-implementer": (
            [sys.executable, "scripts/test-worktree-interoperability.py", "-v"],
            ctx.skills_root / "task-implementer",
        ),
        "hooks": (
            [
                sys.executable,
                str(
                    ctx.skills_root
                    / "sdlc-start"
                    / "assets"
                    / "hooks"
                    / "tests"
                    / "test_sdlc_hooks.py"
                ),
                "-v",
            ],
            ctx.skills_root,
        ),
        "observability": (
            [
                sys.executable,
                str(
                    ctx.skills_root
                    / "sdlc-evaluate"
                    / "scripts"
                    / "test_observability_contract.py"
                ),
                "-v",
            ],
            ctx.skills_root,
        ),
        "repair-control": (
            [
                sys.executable,
                str(
                    ctx.skills_root
                    / "sdlc-classify-failure"
                    / "scripts"
                    / "test_repair_control.py"
                ),
                "-v",
            ],
            ctx.skills_root,
        ),
        "evaluation-failure": (
            [
                sys.executable,
                str(
                    ctx.skills_root
                    / "sdlc-evaluate"
                    / "scripts"
                    / "test_failure_contract.py"
                ),
                "-v",
            ],
            ctx.skills_root,
        ),
        "corrective-plan": (
            [
                sys.executable,
                str(
                    ctx.skills_root
                    / "sdlc-create-plan"
                    / "scripts"
                    / "test_corrective_plan_contract.py"
                ),
                "-v",
            ],
            ctx.skills_root,
        ),
        "troubleshoot-contract": (
            [
                sys.executable,
                str(
                    ctx.skills_root
                    / "troubleshoot"
                    / "scripts"
                    / "test_skill_contract.py"
                ),
                "-v",
            ],
            ctx.skills_root,
        ),
        "verifier": (
            [
                sys.executable,
                str(
                    ctx.skills_root
                    / "sdlc-workflow-test"
                    / "scripts"
                    / "test_verify_agentic_sdlc.py"
                ),
                "-v",
            ],
            ctx.skills_root,
        ),
        "three-tier": (
            [
                sys.executable,
                "-m",
                "unittest",
                "-v",
                "sdlc-workflow-test/scripts/test_three_tier_prompt.py",
                "sdlc-workflow-test/scripts/test_three_tier_browser.py",
                "sdlc-workflow-test/scripts/test_three_tier_lifecycle.py",
            ],
            ctx.skills_root,
        ),
    }
    results: dict[str, tuple[subprocess.CompletedProcess[str], set[str], set[str]]] = {}
    for suite, (command, cwd) in suites.items():
        result = run(command, cwd=cwd, env=env, timeout=120)
        passed, skipped = parse_unittest_results(result.stderr + result.stdout)
        results[suite] = (result, passed, skipped)

    capabilities: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
        "three-tier.harness": (
            "Three-tier prompt and lifecycle harness",
            (
                (
                    "three-tier",
                    "test_rendered_starter_is_accepted_as_a_new_managed_run",
                ),
                ("three-tier", "test_renderer_rejects_compose_identity_drift"),
                (
                    "three-tier",
                    "test_prepare_public_images_uses_private_config_and_fixed_images",
                ),
                (
                    "three-tier",
                    "test_record_runtime_rejects_swapped_container_roles",
                ),
                (
                    "three-tier",
                    "test_uat_phase_failure_updates_computer_use_report_status",
                ),
                (
                    "three-tier",
                    "test_computer_use_jit_readiness_contract_is_mirrored",
                ),
                ("three-tier", "test_semantic_rejects_out_of_order_gui_steps"),
                (
                    "three-tier",
                    "test_semantic_rejects_duplicate_test_evidence_content",
                ),
            ),
        ),
        "evaluation.observability-contract": (
            "Bounded observability evaluation contract",
            (
                (
                    "observability",
                    "test_observability_is_limited_to_predefined_operational_criteria",
                ),
                (
                    "observability",
                    "test_production_read_and_experiment_boundaries_are_distinct",
                ),
                (
                    "observability",
                    "test_inconclusive_is_a_first_class_criterion_result",
                ),
                (
                    "observability",
                    "test_template_records_bounded_observability_provenance",
                ),
                (
                    "observability",
                    "test_eval_cases_require_zero_call_admission_and_sequential_queries",
                ),
            ),
        ),
        "repair.control-contract": (
            "Evidence-guided repair-control contract",
            (
                (
                    "repair-control",
                    "test_ambiguous_evaluation_routes_to_troubleshoot_exactly_once",
                ),
                (
                    "repair-control",
                    "test_failed_direct_repair_requires_troubleshooting_before_attempt_two",
                ),
                (
                    "repair-control",
                    "test_no_bug_found_remains_unresolved_never_design",
                ),
                (
                    "repair-control",
                    "test_design_requires_positive_system_contract_proof",
                ),
                (
                    "evaluation-failure",
                    "test_ambiguous_failure_requires_diagnosis",
                ),
                (
                    "corrective-plan",
                    "test_valid_corrective_plan_preserves_history_and_appends_task",
                ),
                (
                    "troubleshoot-contract",
                    "test_agentic_sdlc_diagnostic_mode_has_strict_handoff_boundary",
                ),
            ),
        ),
        "public.interface": (
            "Public two-command contract",
            (
                ("prompt", "test_public_interface_is_mirrored"),
                (
                    "prompt",
                    "test_private_completion_helpers_do_not_expand_public_surface",
                ),
            ),
        ),
        "prompt.workspace-init": (
            "Prompt workspace initialization",
            (
                ("prompt", "test_init_is_idempotent_and_survives_git_init"),
                ("prompt", "test_concurrent_init_creates_one_starter_prompt"),
            ),
        ),
        "prompt.history": (
            "Prompt metadata history",
            (
                (
                    "prompt",
                    "test_editor_workspace_new_prompt_history_and_metadata_listing",
                ),
                (
                    "prompt",
                    "test_activity_is_monotonic_and_rejected_intake_does_not_reorder",
                ),
            ),
        ),
        "prompt.rename": (
            "Prompt exact-rename safety",
            (
                ("prompt", "test_exact_manual_rename_repairs_binding_and_run_mirror"),
                ("prompt", "test_rename_and_edit_or_stale_copy_fails_closed"),
                ("hooks", "test_stop_uses_repaired_renamed_prompt_filename"),
            ),
        ),
        "prompt.lifecycle": (
            "Prompt run and steering lifecycle",
            (
                ("prompt", "test_new_resume_steering_resolve_and_completed_rerun"),
                (
                    "prompt",
                    "test_concurrent_unchanged_intake_creates_one_run_and_revision",
                ),
            ),
        ),
        "execution.scope": (
            "Exact initialized-folder scope",
            (
                ("execution", "test_nested_project_scope_is_persisted_and_enforced"),
                (
                    "execution",
                    "test_tdd_seal_rejects_changes_outside_nested_project_scope",
                ),
                (
                    "execution",
                    "test_claim_outside_nested_project_scope_fails_before_resources",
                ),
            ),
        ),
        "execution.sessions-recovery": (
            "Worker sessions and interrupted recovery",
            (
                (
                    "execution",
                    "test_interrupted_worker_recovery_transfers_claimed_dirty_state",
                ),
                ("execution", "test_interrupted_worker_recovery_accepts_clean_base"),
                (
                    "execution",
                    "test_interrupted_worker_recovery_accepts_one_clean_direct_child",
                ),
                ("execution", "test_one_worker_session_cannot_own_two_tasks"),
            ),
        ),
        "execution.replan": (
            "Resource-free future-wave replan",
            (
                (
                    "execution",
                    "test_future_replan_replaces_only_resource_free_planned_waves",
                ),
            ),
        ),
        "execution.secret-gate": (
            "Secret-safe result persistence",
            (
                (
                    "execution",
                    "test_finish_rejects_sensitive_staged_content_without_leaking",
                ),
            ),
        ),
        "execution.sequential-fallback": (
            "Sequential ephemeral Codex fallback",
            (
                ("dispatch", "test_dispatches_fresh_ephemeral_workers_sequentially"),
                (
                    "dispatch",
                    "test_first_failure_stops_later_dispatch_and_retains_concise_error",
                ),
                ("dispatch", "test_missing_codex_is_environment_blocker"),
            ),
        ),
        "interop.outer-lease-v2": (
            "Managed outer-worktree lease lifecycle",
            (
                (
                    "worktree",
                    "test_agentic_sdlc_owner_uses_v2_lease_and_releases_before_publication",
                ),
                ("worktree", "test_unfinished_v1_lease_requires_workflow_upgrade"),
                (
                    "execution",
                    "test_managed_outer_execution_releases_before_publication",
                ),
            ),
        ),
        "interop.task-implementer-compatibility": (
            "Task Implementer lease compatibility",
            (
                (
                    "task-implementer",
                    "test_workers_remain_internal_to_the_outer_worktree_branch",
                ),
                (
                    "task-implementer",
                    "test_managed_write_claim_cannot_escape_outer_scope",
                ),
            ),
        ),
        "steering.continuation": (
            "Prompt-bound steering continuation",
            (
                ("hooks", "test_stop_continues_running_next_skill"),
                ("hooks", "test_stop_continues_for_pause_steering"),
                ("hooks", "test_stop_uses_repaired_renamed_prompt_filename"),
            ),
        ),
        "publication.authorization": (
            "Exact Agentic SDLC publication authorization",
            (
                (
                    "hooks",
                    "test_pretool_allows_push_with_exact_create_pr_authorization",
                ),
                (
                    "hooks",
                    "test_pretool_denies_authorized_push_with_extra_refs_or_tags",
                ),
                ("hooks", "test_pretool_denies_wrapped_authorized_push"),
                ("hooks", "test_pretool_allows_gh_pr_create_with_exact_authorization"),
                (
                    "hooks",
                    "test_pretool_denies_gh_pr_create_without_explicit_head",
                ),
                ("hooks", "test_pretool_denies_compound_gh_pr_create"),
                ("hooks", "test_pretool_guards_only_github_pr_creation_writes"),
            ),
        ),
        "merge.authorization": (
            "Exact Agentic SDLC merge authorization",
            (
                ("hooks", "test_pretool_denies_gh_pr_merge_without_authorization"),
                ("hooks", "test_pretool_allows_exact_authorized_gh_pr_merge"),
                (
                    "hooks",
                    "test_pretool_allows_exact_authorized_merge_queue_command",
                ),
                ("hooks", "test_pretool_allows_exact_authorized_pr_url_merge"),
                ("hooks", "test_pretool_denies_merge_without_exact_head_guard"),
                (
                    "hooks",
                    "test_pretool_denies_merge_command_outside_exact_authorization",
                ),
                (
                    "hooks",
                    "test_pretool_denies_merge_without_explicit_pr_target",
                ),
                (
                    "hooks",
                    "test_pretool_denies_wrapped_or_prepended_gh_pr_merge",
                ),
                ("hooks", "test_pretool_denies_nested_shell_gh_pr_merge"),
                ("hooks", "test_pretool_denies_merge_admin_bypass"),
                ("hooks", "test_pretool_denies_merge_branch_deletion"),
                ("hooks", "test_pretool_denies_compound_merge_command"),
                ("hooks", "test_pretool_denies_merge_pr_scope_mismatch"),
                ("hooks", "test_pretool_denies_merge_without_passing_readiness"),
                ("hooks", "test_pretool_denies_github_merge_mcp_in_active_sdlc"),
            ),
        ),
        "verifier.self-tests": (
            "Verifier contract self-tests",
            (
                ("verifier", "test_any_deterministic_failure_forces_fail"),
                ("verifier", "test_missing_live_evidence_is_partial"),
                ("verifier", "test_valid_live_evidence_is_accepted"),
                (
                    "verifier",
                    "test_generic_digest_backed_evidence_cannot_reach_pass",
                ),
                ("verifier", "test_no_change_all_pass_manifest_fails_closed"),
                ("verifier", "test_stale_live_evidence_fails_closed"),
                ("verifier", "test_alternate_live_baseline_fails_closed"),
                ("verifier", "test_out_of_scope_committed_change_fails_closed"),
                ("verifier", "test_committed_private_state_fails_closed"),
                ("verifier", "test_deleted_out_of_scope_history_fails_closed"),
                ("verifier", "test_deleted_private_state_history_fails_closed"),
                ("verifier", "test_symlinked_live_manifest_fails_closed"),
                ("verifier", "test_invalid_utf8_live_manifest_fails_closed"),
                ("verifier", "test_invalid_utf8_context_fails_closed"),
                (
                    "verifier",
                    "test_invalid_utf8_context_during_setup_writes_fail_report",
                ),
                ("verifier", "test_capability_matrix_isolates_unrelated_warnings"),
                ("verifier", "test_report_is_private"),
                ("verifier", "test_verification_root_is_private"),
                ("verifier", "test_flat_fixture_migrates_cleanly_to_nested_scope"),
                ("verifier", "test_dirty_fixture_fails_without_mutation"),
                (
                    "verifier",
                    "test_symlinked_disposable_project_fails_without_target_mutation",
                ),
                (
                    "verifier",
                    "test_symlinked_services_component_fails_without_target_mutation",
                ),
                (
                    "verifier",
                    "test_regular_file_fixture_parent_fails_without_mutation",
                ),
                ("verifier", "test_run_timeout_becomes_failure_result"),
                ("verifier", "test_tree_digest_ignores_install_provenance"),
                ("verifier", "test_tree_digest_rejects_symlinks"),
                ("verifier", "test_tree_digest_rejects_symlinked_root"),
                (
                    "verifier",
                    "test_file_digest_rejects_symlinked_hook_payload",
                ),
                (
                    "verifier",
                    "test_report_path_must_stay_under_verification_root",
                ),
                ("verifier", "test_report_path_rejects_symlinked_parent"),
                ("verifier", "test_report_path_rejects_symlinked_file"),
            ),
        ),
    }
    for capability_id, (name, requirements) in capabilities.items():
        failed_suites = sorted(
            {suite for suite, _ in requirements if results[suite][0].returncode != 0}
        )
        missing = [
            test
            for suite, test in requirements
            if test not in results[suite][1] and test not in results[suite][2]
        ]
        skipped = [test for suite, test in requirements if test in results[suite][2]]
        status = "FAIL" if failed_suites or missing else "WARN" if skipped else "PASS"
        if failed_suites:
            detail = "Required regression suite failed: " + ", ".join(failed_suites)
        elif missing:
            detail = "Required regression tests were not executed: " + ", ".join(
                missing
            )
        elif skipped:
            detail = (
                "Required platform-specific regression tests were skipped: "
                + ", ".join(skipped)
            )
        else:
            detail = f"Executed {len(requirements)} required regression test(s)."
        ctx.add(
            "Capability regression results",
            name,
            status,
            detail,
            capability_id=capability_id,
        )


def run_hook(
    script: Path,
    payload: dict[str, Any],
    ctx: Context,
    *,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home or ctx.fixture_codex_home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = run(
        ["python3", str(script)], input_text=json.dumps(payload), env=env, timeout=10
    )
    if result.returncode != 0:
        return {"_error": result.stderr.strip() or result.stdout.strip()}
    if not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"_error": result.stdout.strip()}


def pre_payload(ctx: Context, tool_name: str, command: str) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "cwd": str(ctx.selected_project),
        "turn_id": "verification-turn",
        "tool_name": tool_name,
        "tool_use_id": "verification-tool",
        "tool_input": {"command": command},
    }


def stop_payload(ctx: Context, active: bool = False) -> dict[str, Any]:
    return {
        "hook_event_name": "Stop",
        "cwd": str(ctx.selected_project),
        "turn_id": "verification-turn",
        "stop_hook_active": active,
        "last_assistant_message": "verification",
    }


def denied(result: dict[str, Any]) -> str | None:
    output = result.get("hookSpecificOutput", {})
    if output.get("permissionDecision") == "deny":
        return str(output.get("permissionDecisionReason") or "")
    return None


def check_hooks_with_fixtures(ctx: Context) -> None:
    # Hook fixtures must never share the prompt-bound CODEX_HOME used by a live
    # run. Otherwise active-run discovery can resolve the operator's real run
    # instead of the deterministic fixture and mutate or misread its state.
    ctx = replace(
        ctx,
        fixture_codex_home=ctx.verification_root / "hook-fixture-codex-home",
    )
    hook_dir = ctx.skills_root / "sdlc-start" / "assets" / "hooks"
    pre_tool = hook_dir / "pre_tool_use_sdlc_policy.py"
    stop_hook = hook_dir / "stop_sdlc_continue.py"
    tests = hook_dir / "tests" / "test_sdlc_hooks.py"
    missing = [path for path in (pre_tool, stop_hook, tests) if not path.exists()]
    if missing:
        ctx.add(
            "PreToolUse safety test results",
            "Hook source files",
            "FAIL",
            "Missing: " + ", ".join(str(p) for p in missing),
        )
        return

    def reset_stop_state(
        *,
        status: str = "running",
        run_status: str | None = None,
        current_phase: str = "implementation",
        next_skill: str = "sdlc-validate-codes",
        retry_counts: dict[str, int] | None = None,
        iteration_count: int = 1,
        max_iterations: int = 200,
        max_retries: int = 3,
        needs_human: bool = False,
        blocked_reason: str = "",
        feature_queue: dict[str, Any] | None = None,
        steering: str = "",
    ) -> Path:
        run_dir = setup_fixture_state(ctx, record=False)
        current_state = {
            "project_id": DEFAULT_PROJECT_ID,
            "run_id": DEFAULT_RUN_ID,
            "status": status,
            "current_feature": "FEAT-001",
            "current_phase": current_phase,
            "next_recommended_skill": next_skill,
            "retry_counts": retry_counts or {current_phase: 0},
            "iteration_count": iteration_count,
            "max_iterations": max_iterations,
            "max_retries": max_retries,
            "needs_human": needs_human,
            "blocked_reason": blocked_reason,
        }
        write_json(
            run_dir / "run.json",
            {
                "status": run_status or status,
                "blocked_reason": blocked_reason,
                "prompt": {"filename": "20260716T000000Z--verify-agentic-sdlc.md"},
            },
        )
        write_json(run_dir / "current-state.json", current_state)
        write_json(
            run_dir / "feature-queue.json",
            feature_queue
            or {"features": [{"id": "FEAT-001", "status": current_phase}]},
        )
        (run_dir / "STEERING.md").write_text(steering, encoding="utf-8")
        return run_dir

    setup_fixture_state(ctx)
    allow_status = run_hook(
        pre_tool, pre_payload(ctx, "Bash", "git status --short"), ctx
    )
    ctx.add(
        "PreToolUse safety test results",
        "Allow read-only git status",
        "PASS" if allow_status == {} else "FAIL",
        json.dumps(allow_status, sort_keys=True),
    )
    delete_cmd = "rm " + "-rf " + "/"
    deny_delete = run_hook(pre_tool, pre_payload(ctx, "Bash", delete_cmd), ctx)
    reason = denied(deny_delete)
    ctx.add(
        "PreToolUse safety test results",
        "Deny destructive recursive delete",
        "PASS" if reason and "recursive removal" in reason else "FAIL",
        reason or json.dumps(deny_delete, sort_keys=True),
    )
    plan = (
        ctx.fixture_codex_home
        / "sdlc-runs"
        / DEFAULT_PROJECT_ID
        / DEFAULT_RUN_ID
        / "plans"
        / "FEAT-001.plan.v1.md"
    )
    patch = f"*** Begin Patch\n*** Update File: {plan}\n@@\n-# Plan\n+# Changed\n*** End Patch\n"
    allow_plan = run_hook(pre_tool, pre_payload(ctx, "apply_patch", patch), ctx)
    ctx.add(
        "PreToolUse safety test results",
        "Allow locked plan edit",
        "PASS" if allow_plan == {} else "FAIL",
        json.dumps(allow_plan, sort_keys=True),
    )

    empty_codex_home = ctx.verification_root / "empty-fixture-codex-home"
    empty_codex_home.mkdir(parents=True, exist_ok=True)
    no_active = run_hook(stop_hook, stop_payload(ctx), ctx, codex_home=empty_codex_home)
    ctx.add(
        "Stop continuation test results",
        "No active run",
        "PASS" if no_active == {"continue": True} else "FAIL",
        json.dumps(no_active, sort_keys=True),
    )

    reset_stop_state(status="complete")
    complete = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add(
        "Stop continuation test results",
        "Complete run stops",
        "PASS"
        if complete.get("continue") is False
        and "complete" in str(complete.get("stopReason"))
        else "FAIL",
        str(complete.get("stopReason") or complete),
    )

    reset_stop_state(status="paused")
    paused = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add(
        "Stop continuation test results",
        "Paused run stops",
        "PASS"
        if paused.get("continue") is False and "paused" in str(paused.get("stopReason"))
        else "FAIL",
        str(paused.get("stopReason") or paused),
    )

    reset_stop_state(status="blocked", blocked_reason="verification blocker")
    blocked = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add(
        "Stop continuation test results",
        "Blocked run stops",
        "PASS"
        if blocked.get("continue") is False
        and "verification blocker" in str(blocked.get("stopReason"))
        else "FAIL",
        str(blocked.get("stopReason") or blocked),
    )

    reset_stop_state(needs_human=True, blocked_reason="verification approval")
    human = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add(
        "Stop continuation test results",
        "Human input stops",
        "PASS"
        if human.get("continue") is False
        and "Human input required" in str(human.get("stopReason"))
        else "FAIL",
        str(human.get("stopReason") or human),
    )

    reset_stop_state(iteration_count=200, max_iterations=200)
    max_iter = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add(
        "Stop continuation test results",
        "Max iteration stops",
        "PASS"
        if max_iter.get("continue") is False
        and "Max SDLC iterations" in str(max_iter.get("stopReason"))
        else "FAIL",
        str(max_iter.get("stopReason") or max_iter),
    )

    reset_stop_state(retry_counts={"implementation": 3}, max_retries=3)
    retry = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add(
        "Stop continuation test results",
        "Retry budget stops",
        "PASS"
        if retry.get("continue") is False
        and "Retry budget exceeded" in str(retry.get("stopReason"))
        else "FAIL",
        str(retry.get("stopReason") or retry),
    )

    reset_stop_state()
    run_hook(stop_hook, stop_payload(ctx, active=True), ctx)
    run_hook(stop_hook, stop_payload(ctx, active=True), ctx)
    no_progress = run_hook(stop_hook, stop_payload(ctx, active=True), ctx)
    ctx.add(
        "Stop continuation test results",
        "No-progress guard stops",
        "PASS"
        if no_progress.get("continue") is False
        and "No progress" in str(no_progress.get("stopReason"))
        else "FAIL",
        str(no_progress.get("stopReason") or no_progress),
    )

    reset_stop_state(next_skill="sdlc-validate-codes")
    stop_continue = run_hook(stop_hook, stop_payload(ctx), ctx)
    prompt = str(stop_continue.get("reason") or "")
    bound_command = "Use $sdlc-start run 20260716T000000Z--verify-agentic-sdlc.md"
    ctx.add(
        "Stop continuation test results",
        "Continue through prompt-bound $sdlc-start",
        "PASS"
        if stop_continue.get("decision") == "block"
        and bound_command in prompt
        and "Use $sdlc-start." not in prompt
        else "FAIL",
        prompt.splitlines()[0] if prompt else json.dumps(stop_continue, sort_keys=True),
    )

    reset_stop_state(
        next_skill="",
        feature_queue={"features": [{"id": "FEAT-001", "status": "committed"}]},
    )
    uat = run_hook(stop_hook, stop_payload(ctx), ctx)
    uat_prompt = str(uat.get("reason") or "")
    ctx.add(
        "Stop continuation test results",
        "Continue to UAT",
        "PASS"
        if uat.get("decision") == "block" and "sdlc-uat-tests" in uat_prompt
        else "FAIL",
        uat_prompt.splitlines()[0] if uat_prompt else json.dumps(uat, sort_keys=True),
    )

    reset_stop_state(steering="Pause after the current feature. Do not create a PR.\n")
    steering = run_hook(stop_hook, stop_payload(ctx), ctx)
    steering_prompt = str(steering.get("reason") or "")
    ctx.add(
        "Steering behavior results",
        "Pause/no-PR steering continues through coordinator",
        "PASS"
        if steering.get("decision") == "block"
        and bound_command in steering_prompt
        and "STEERING.md" in steering_prompt
        else "FAIL",
        steering_prompt.splitlines()[0]
        if steering_prompt
        else json.dumps(steering, sort_keys=True),
    )

    unbound_dir = reset_stop_state(next_skill="sdlc-validate-codes")
    (unbound_dir / "prompt.json").unlink()
    write_json(unbound_dir / "run.json", {"status": "running"})
    unbound = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add(
        "Stop continuation test results",
        "Unbound active run fails closed",
        "PASS"
        if unbound.get("continue") is False
        and "WORKFLOW_UPGRADE_REQUIRED" in str(unbound.get("stopReason"))
        else "FAIL",
        str(unbound.get("stopReason") or unbound),
    )

    reset_stop_state(
        current_phase="review",
        next_skill="sdlc-merge-pr",
        retry_counts={"review": 0},
        feature_queue={
            "features": [{"id": "FEAT-001", "status": "committed"}],
            "uat": {"status": "passed"},
        },
    )
    stop_merge = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add(
        "Stop continuation test results",
        "Do not auto-continue merge",
        "PASS"
        if stop_merge.get("continue") is False
        and "explicit user request" in str(stop_merge.get("stopReason"))
        else "FAIL",
        str(stop_merge.get("stopReason") or stop_merge),
        capability_id="hook.merge-explicit-request",
    )


def committed_paths_between(
    ctx: Context, baseline: str, final: str
) -> list[str] | None:
    revisions = run(
        ["git", "rev-list", "--reverse", f"{baseline}..{final}"],
        cwd=ctx.disposable_project,
        timeout=15,
    )
    if revisions.returncode != 0:
        return None
    commits = [line for line in revisions.stdout.splitlines() if line]
    if len(commits) > 1_000 or any(
        SHA_RE.fullmatch(commit) is None for commit in commits
    ):
        return None
    paths: list[str] = []
    for commit in commits:
        changed = run(
            [
                "git",
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-m",
                "-z",
                commit,
                "--",
            ],
            cwd=ctx.disposable_project,
            timeout=15,
        )
        if changed.returncode != 0:
            return None
        paths.extend(path for path in changed.stdout.split("\0") if path)
        if len(paths) > 100_000:
            return None
    return paths


def load_live_results(ctx: Context) -> dict[str, dict[str, Any]] | None:
    manifest = ctx.live_evidence_path
    verification_root = ctx.verification_root.resolve(strict=False)

    def reject(detail: str) -> None:
        ctx.add(
            "Live workflow results",
            "Live evidence integrity",
            "FAIL",
            detail,
            capability_id="live.evidence-integrity",
        )

    if (
        has_symlink_component(manifest, verification_root)
        or has_non_directory_parent_or_non_file_target(manifest, verification_root)
        or not inside(manifest.resolve(strict=False), verification_root)
    ):
        reject(
            "Live evidence manifest must be a regular private file under the verification root."
        )
        return None
    if not manifest.exists():
        return None
    if (
        not manifest.is_file()
        or not inside(manifest.resolve(strict=False), verification_root)
        or manifest.stat().st_nlink != 1
    ):
        reject(
            "Live evidence manifest must be a regular private file under the verification root."
        )
        return None
    if manifest.stat().st_size > 1_000_000:
        reject("Live evidence manifest exceeds the one-megabyte safety limit.")
        return None
    if os.name == "posix" and manifest.stat().st_mode & 0o077:
        reject(
            "Live evidence manifest permissions must not grant group or other access."
        )
        return None
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        reject("Live evidence manifest is unreadable or invalid JSON.")
        return None
    required = {
        "schema",
        "verification_id",
        "project_root",
        "baseline_head",
        "final_head",
        "profiles",
        "lanes",
        "skills",
    }
    if not isinstance(value, dict) or set(value) != required:
        reject("Live evidence manifest fields do not match the v3 schema.")
        return None
    context_path = ctx.verification_root / "verification-context.json"
    if (
        not context_path.is_file()
        or has_symlink_component(context_path, verification_root)
        or context_path.stat().st_nlink != 1
        or (os.name == "posix" and context_path.stat().st_mode & 0o077)
    ):
        reject("Preserved verification context is missing or unsafe.")
        return None
    try:
        preserved_context: Any = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        reject("Preserved verification context is unreadable or invalid.")
        return None
    current = git_output(ctx.disposable_project, "rev-parse", "HEAD")
    if current is None or not valid_verification_context(
        ctx, preserved_context, current_head=current
    ):
        reject("Preserved verification context identity is stale or invalid.")
        return None
    baseline = str(preserved_context["baseline_head"])
    final = str(value.get("final_head") or "")
    profiles = value.get("profiles")
    lanes = value.get("lanes")
    skills = value.get("skills")
    if (
        value.get("schema") != LIVE_RESULTS_SCHEMA
        or value.get("project_root") != str(ctx.selected_project)
        or value.get("baseline_head") != baseline
        or SHA_RE.fullmatch(final) is None
        or value.get("verification_id") != preserved_context["verification_id"]
        or not isinstance(profiles, dict)
        or not set(profiles).issubset(PROFILE_SOURCE_SCHEMAS)
        or not isinstance(lanes, dict)
        or set(lanes) != set(LIVE_LANES)
        or not isinstance(skills, dict)
        or set(skills) != set(REQUIRED_EVIDENCE_SKILLS)
    ):
        reject("Live evidence identity or lane structure is stale or invalid.")
        return None

    passing_capabilities = {
        check.capability_id
        for check in ctx.checks
        if check.capability_id is not None and check.status == "PASS"
    }
    deterministic_capabilities = set().union(*DETERMINISTIC_SKILL_CAPABILITIES.values())
    validated_profiles: set[str] = set()
    profile_claims: dict[str, set[str]] = {}
    if deterministic_capabilities.issubset(passing_capabilities):
        validated_profiles.add("deterministic")
        profile_claims["deterministic"] = set().union(
            *(
                set(SKILL_REQUIRED_ASSERTIONS[skill])
                for skill in DETERMINISTIC_SKILL_CAPABILITIES
            )
        )
    if "merge.authorization" in passing_capabilities:
        validated_profiles.add("safety")
        profile_claims["safety"] = set(SKILL_REQUIRED_ASSERTIONS["sdlc-merge-pr"])
    registered_profiles: set[str] = set()
    used_profile_sources: dict[str, str] = {}
    for profile, entry in profiles.items():
        expected_path = f"evidence/profiles/{profile}/result.json"
        if (
            not isinstance(entry, dict)
            or set(entry) != {"status", "evidence"}
            or entry.get("status") not in {"PASS", "FAIL", "PARTIAL"}
            or entry.get("evidence") != [expected_path]
        ):
            reject(f"Evidence profile entry is invalid: {profile}.")
            return None
        unresolved = verification_root / expected_path
        artifact = unresolved.resolve(strict=False)
        if (
            not inside(artifact, verification_root)
            or has_symlink_component(unresolved, verification_root)
            or not artifact.is_file()
            or artifact.stat().st_nlink != 1
            or (os.name == "posix" and artifact.stat().st_mode & 0o077)
        ):
            reject(f"Evidence profile artifact is unavailable or unsafe: {profile}.")
            return None
        try:
            result_value = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            reject(f"Evidence profile artifact is invalid JSON: {profile}.")
            return None
        if (
            not isinstance(result_value, dict)
            or set(result_value)
            != {
                "schema",
                "profile",
                "verification_id",
                "status",
                "source_schema",
                "source_identity",
                "source_artifacts",
            }
            or result_value.get("schema") != "agentic-sdlc/evidence-profile-v3"
            or result_value.get("profile") != profile
            or result_value.get("verification_id") != value["verification_id"]
            or result_value.get("status") != entry["status"]
            or result_value.get("source_schema") != PROFILE_SOURCE_SCHEMAS[profile]
            or not isinstance(result_value.get("source_identity"), str)
            or not result_value["source_identity"]
            or not isinstance(result_value.get("source_artifacts"), list)
            or not 1 <= len(result_value["source_artifacts"]) <= 20
        ):
            reject(f"Evidence profile result is invalid: {profile}.")
            return None
        primary_source: Path | None = None
        profile_source_artifacts: list[Path] = []
        for source_ref in result_value["source_artifacts"]:
            if (
                not isinstance(source_ref, dict)
                or set(source_ref) != {"path", "sha256"}
                or not isinstance(source_ref.get("path"), str)
                or not isinstance(source_ref.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", source_ref["sha256"]) is None
                or not valid_profile_source_path(profile, source_ref["path"])
            ):
                reject(f"Evidence profile source reference is invalid: {profile}.")
                return None
            relative = source_ref["path"]
            unresolved_source = verification_root / Path(*PurePosixPath(relative).parts)
            source_artifact = unresolved_source.resolve(strict=False)
            if (
                not inside(source_artifact, verification_root)
                or has_symlink_component(unresolved_source, verification_root)
                or not source_artifact.is_file()
                or source_artifact.stat().st_nlink != 1
                or (os.name == "posix" and source_artifact.stat().st_mode & 0o077)
                or file_sha256(source_artifact) != source_ref["sha256"]
                or relative in used_profile_sources
            ):
                reject(f"Evidence profile source is unavailable or unsafe: {profile}.")
                return None
            if primary_source is None:
                primary_source = source_artifact
            profile_source_artifacts.append(source_artifact)
            used_profile_sources[relative] = profile
        try:
            source_header = json.loads(primary_source.read_text(encoding="utf-8"))
        except (AttributeError, OSError, UnicodeError, json.JSONDecodeError):
            reject(f"Evidence profile primary source is invalid JSON: {profile}.")
            return None
        identity_field = "verification_id" if profile == "three-tier" else "run_id"
        if (
            not isinstance(source_header, dict)
            or source_header.get("schema") != result_value["source_schema"]
            or source_header.get(identity_field) != result_value["source_identity"]
        ):
            reject(f"Evidence profile primary source identity is invalid: {profile}.")
            return None
        claims = validated_profile_claims(
            ctx,
            profile,
            profile_source_artifacts,
            verification_id_value=value["verification_id"],
            baseline=baseline,
            final=final,
        )
        if entry["status"] == "PASS":
            if claims is None:
                reject(
                    f"Evidence profile PASS failed source-specific semantic validation: {profile}."
                )
                return None
            validated_profiles.add(profile)
            profile_claims[profile] = claims
        registered_profiles.add(profile)
    clean_status = git_output(ctx.disposable_project, "status", "--porcelain")
    ancestry = run(
        ["git", "merge-base", "--is-ancestor", baseline, final],
        cwd=ctx.disposable_project,
        timeout=15,
    )
    if current != final or clean_status != "" or ancestry.returncode != 0:
        reject(
            "Live evidence Git identity is stale, dirty, or not descended from its baseline."
        )
        return None
    changed_paths = committed_paths_between(ctx, baseline, final)
    if changed_paths is None:
        reject("Live evidence committed-path history could not be verified.")
        return None
    selected_scope = ctx.selected_project.relative_to(ctx.disposable_project).as_posix()
    outside = [
        path
        for path in changed_paths
        if path != selected_scope and not path.startswith(selected_scope + "/")
    ]
    private = [
        path
        for path in changed_paths
        if PRIVATE_REPOSITORY_PARTS.intersection(PurePosixPath(path).parts)
        or PurePosixPath(path).name in PRIVATE_REPOSITORY_FILES
    ]
    if outside or private:
        reject(
            "Live evidence history includes paths outside the selected project or private SDLC state."
        )
        return None
    golden_path = lanes.get("golden-path")
    if (
        isinstance(golden_path, dict)
        and golden_path.get("status") == "PASS"
        and (final == baseline or not changed_paths)
    ):
        reject("Golden-path PASS requires a committed selected-scope change.")
        return None

    used_assertion_artifacts: dict[str, str] = {}

    def assertions_are_valid(
        assertions: Any,
        *,
        required_names: set[str],
        owner_prefix: str,
        owner: str,
        status: str,
        semantic_claims: set[str] | None = None,
    ) -> bool:
        if not isinstance(assertions, dict) or set(assertions) != required_names:
            return False
        passed: list[bool] = []
        for assertion_name, assertion in assertions.items():
            if (
                not isinstance(assertion, dict)
                or set(assertion) != {"passed", "evidence"}
                or not isinstance(assertion.get("passed"), bool)
                or not isinstance(assertion.get("evidence"), list)
                or not 1 <= len(assertion["evidence"]) <= 20
            ):
                return False
            passed.append(assertion["passed"])
            for evidence_ref in assertion["evidence"]:
                if (
                    not isinstance(evidence_ref, dict)
                    or set(evidence_ref) != {"path", "sha256"}
                    or not isinstance(evidence_ref.get("path"), str)
                    or not isinstance(evidence_ref.get("sha256"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", evidence_ref["sha256"]) is None
                    or not valid_assertion_artifact_path(
                        owner_prefix, evidence_ref["path"]
                    )
                ):
                    return False
                relative = evidence_ref["path"]
                unresolved = verification_root / Path(*PurePosixPath(relative).parts)
                artifact = unresolved.resolve(strict=False)
                if (
                    not inside(artifact, verification_root)
                    or has_symlink_component(unresolved, verification_root)
                    or not artifact.is_file()
                    or artifact.stat().st_nlink != 1
                    or (os.name == "posix" and artifact.stat().st_mode & 0o077)
                    or file_sha256(artifact) != evidence_ref["sha256"]
                    or (
                        status == "PASS"
                        and (
                            semantic_claims is None
                            or assertion_name not in semantic_claims
                        )
                        and not assertion_artifact_semantics_valid(
                            assertion_name, artifact
                        )
                    )
                ):
                    return False
                prior_owner = used_assertion_artifacts.get(relative)
                if prior_owner is not None and prior_owner != owner:
                    return False
                used_assertion_artifacts[relative] = owner
        return status != "PASS" or all(passed)

    normalized: dict[str, dict[str, Any]] = {}
    for lane, entry in lanes.items():
        if not isinstance(entry, dict) or set(entry) != {"status", "evidence"}:
            reject(f"Live lane {lane} does not match the v3 result shape.")
            return None
        status = entry.get("status")
        evidence = entry.get("evidence")
        if (
            status not in {"PASS", "FAIL", "PARTIAL"}
            or not isinstance(evidence, list)
            or len(evidence) > 100
            or len({item for item in evidence if isinstance(item, str)})
            != len(evidence)
        ):
            reject(f"Live lane {lane} has an invalid status or evidence list.")
            return None
        if len(evidence) != 1 or evidence[0] != f"evidence/{lane}/result.json":
            reject(f"Live lane {lane} requires its one canonical result.json artifact.")
            return None
        resolved: list[str] = []
        for relative in evidence:
            if not isinstance(relative, str) or not relative:
                reject(f"Live lane {lane} contains an invalid evidence path.")
                return None
            if not valid_lane_evidence_path(lane, relative):
                reject(
                    f"Live lane {lane} evidence must be a canonical path under evidence/{lane}/."
                )
                return None
            pure_path = PurePosixPath(relative)
            candidate_path = Path(*pure_path.parts)
            if candidate_path.is_absolute() or ".." in candidate_path.parts:
                reject(f"Live lane {lane} contains an unsafe evidence path.")
                return None
            unresolved = verification_root / candidate_path
            candidate = unresolved.resolve(strict=False)
            if (
                not inside(candidate, verification_root)
                or has_symlink_component(unresolved, verification_root)
                or not candidate.is_file()
                or candidate.stat().st_nlink != 1
                or (os.name == "posix" and candidate.stat().st_mode & 0o077)
            ):
                reject(f"Live lane {lane} references unavailable private evidence.")
                return None
            resolved.append(relative)
        try:
            result_value = json.loads(
                (verification_root / resolved[0]).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            reject(f"Live lane {lane} result is unreadable or invalid JSON.")
            return None
        expected_result_fields = {
            "schema",
            "lane",
            "profile",
            "verification_id",
            "baseline_head",
            "final_head",
            "status",
            "assertions",
        }
        assertions = (
            result_value.get("assertions") if isinstance(result_value, dict) else None
        )
        if (
            not isinstance(result_value, dict)
            or set(result_value) != expected_result_fields
            or result_value.get("schema") != LIVE_LANE_RESULT_SCHEMA
            or result_value.get("lane") != lane
            or result_value.get("profile") != "lightweight"
            or "lightweight" not in registered_profiles
            or (status == "PASS" and "lightweight" not in validated_profiles)
            or result_value.get("verification_id") != value["verification_id"]
            or result_value.get("baseline_head") != baseline
            or result_value.get("final_head") != final
            or result_value.get("status") != status
            or not assertions_are_valid(
                assertions,
                required_names=LIVE_LANE_REQUIRED_ASSERTIONS[lane],
                owner_prefix=f"evidence/{lane}",
                owner=f"lane:{lane}",
                status=status,
                semantic_claims=profile_claims.get("lightweight"),
            )
        ):
            reject(f"Live lane {lane} semantic assertions are invalid or incomplete.")
            return None
        normalized[lane] = {"status": status, "evidence": resolved}
    for skill, entry in skills.items():
        expected_path = f"evidence/skills/{skill}/result.json"
        if (
            not isinstance(entry, dict)
            or set(entry) != {"status", "basis", "evidence"}
            or entry.get("status") not in {"PASS", "FAIL", "PARTIAL"}
            or entry.get("basis") != SKILL_EVIDENCE_BASES[skill]
            or entry.get("evidence") != [expected_path]
        ):
            reject(f"Skill evidence entry is invalid: {skill}.")
            return None
        unresolved = verification_root / expected_path
        artifact = unresolved.resolve(strict=False)
        if (
            not inside(artifact, verification_root)
            or has_symlink_component(unresolved, verification_root)
            or not artifact.is_file()
            or artifact.stat().st_nlink != 1
            or (os.name == "posix" and artifact.stat().st_mode & 0o077)
        ):
            reject(f"Skill evidence artifact is unavailable or unsafe: {skill}.")
            return None
        try:
            result_value = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            reject(f"Skill evidence artifact is invalid JSON: {skill}.")
            return None
        if (
            not isinstance(result_value, dict)
            or set(result_value)
            != {
                "schema",
                "skill",
                "verification_id",
                "baseline_head",
                "final_head",
                "status",
                "basis",
                "profiles",
                "assertions",
            }
            or result_value.get("schema") != SKILL_RESULT_SCHEMA
            or result_value.get("skill") != skill
            or result_value.get("verification_id") != value["verification_id"]
            or result_value.get("baseline_head") != baseline
            or result_value.get("final_head") != final
            or result_value.get("status") != entry["status"]
            or result_value.get("basis") != entry["basis"]
            or not isinstance(result_value.get("profiles"), list)
            or result_value.get("profiles") != SKILL_REQUIRED_PROFILES[skill]
            or any(
                profile not in EVIDENCE_PROFILES
                for profile in result_value.get("profiles", [])
            )
            or (
                entry["status"] == "PASS"
                and not set(result_value["profiles"]).issubset(validated_profiles)
            )
            or not assertions_are_valid(
                result_value.get("assertions"),
                required_names=set(SKILL_REQUIRED_ASSERTIONS[skill]),
                owner_prefix=f"evidence/skills/{skill}",
                owner=f"skill:{skill}",
                status=entry["status"],
                semantic_claims=set().union(
                    *(
                        profile_claims.get(profile, set())
                        for profile in result_value.get("profiles", [])
                    )
                ),
            )
        ):
            reject(f"Skill semantic result is invalid: {skill}.")
            return None
        if entry["status"] == "PASS" and skill in DETERMINISTIC_SKILL_CAPABILITIES:
            missing_capabilities = [
                capability
                for capability in DETERMINISTIC_SKILL_CAPABILITIES[skill]
                if not any(
                    check.capability_id == capability and check.status == "PASS"
                    for check in ctx.checks
                )
            ]
            if missing_capabilities:
                reject(
                    f"Deterministic skill evidence is not backed by passing verifier capabilities: {skill}."
                )
                return None
        if entry["status"] == "PASS" and skill in SKILL_LANE_REQUIREMENTS:
            if any(
                normalized.get(lane, {}).get("status") != "PASS"
                for lane in SKILL_LANE_REQUIREMENTS[skill]
            ):
                reject(f"Skill PASS is not backed by its required live lane: {skill}.")
                return None
        if entry["status"] == "PASS" and skill == "sdlc-merge-pr":
            merge_guard = [
                check
                for check in ctx.checks
                if check.capability_id == "merge.authorization"
            ]
            if not merge_guard or merge_guard[-1].status != "PASS":
                reject(
                    "Merge safety evidence is not backed by the explicit-authorization hook test."
                )
                return None
        ctx.add(
            "Agentic SDLC skill results",
            skill,
            "WARN" if entry["status"] == "PARTIAL" else entry["status"],
            f"Validated {entry['basis']} evidence with {len(result_value['assertions'])} assertion(s).",
            capability_id=f"skill.{skill}",
        )
    ctx.add(
        "Live workflow results",
        "Live evidence integrity",
        "PASS",
        "Private live evidence manifest identity, Git state, and artifact paths are valid.",
        capability_id="live.evidence-integrity",
    )
    return normalized


def add_agent_required_sections(ctx: Context) -> None:
    lane_contract = {
        "golden-path": (
            "Golden-path agent execution",
            "complete one full disposable workflow",
        ),
        "idempotency": (
            "Idempotency rerun",
            "rerun the completed prompt without duplicate state",
        ),
        "change-request": (
            "Change-request handling",
            "apply a scoped prompt change request",
        ),
        "failure-routing": (
            "Failure-loop routing",
            "inject controlled failures and confirm routing",
        ),
        "auto-steering": (
            "Auto-steering classification",
            "record and route product-truth steering",
        ),
        "documentation-update": (
            "Documentation update phase",
            "record documentation evidence after evaluation",
        ),
        "steering-continuation": (
            "Steering and continuation",
            "verify pause, no-PR, and continuation guards",
        ),
    }
    live = load_live_results(ctx)
    integrity_failed = any(
        check.capability_id == "live.evidence-integrity" and check.status == "FAIL"
        for check in ctx.checks
    )
    for lane, (name, action) in lane_contract.items():
        entry = live.get(lane) if live is not None else None
        if entry is None:
            reason = "No live evidence manifest was supplied."
            if integrity_failed:
                reason = "The supplied live evidence manifest failed validation."
            ctx.add(
                "Live workflow results",
                name,
                "WARN",
                f"{reason} Run the {lane} lane to {action}.",
                capability_id=f"live.{lane}",
            )
            continue
        status = str(entry["status"])
        ctx.add(
            "Live workflow results",
            name,
            "WARN" if status == "PARTIAL" else status,
            f"Validated {len(entry['evidence'])} private evidence artifact(s).",
            capability_id=f"live.{lane}",
        )
    ctx.add(
        "Disposable SDLC golden-path run results",
        "Private state not committed",
        "PASS",
        "Preflight fixture keeps private state outside the disposable repo.",
    )


def final_status(ctx: Context) -> str:
    if any(check.status == "FAIL" for check in ctx.checks):
        return "FAIL"
    if any(check.status == "WARN" for check in ctx.checks):
        return "PARTIAL"
    return "PASS"


def summarize_matrix(ctx: Context) -> list[tuple[str, str]]:
    capability_rows = [
        ("Installed worktree dependency", "runtime.worktree-dependency"),
        ("Source-installed parity", "runtime.skill-parity"),
        ("Public two-command interface", "public.interface"),
        ("Prompt workspace init", "prompt.workspace-init"),
        ("Prompt history", "prompt.history"),
        ("Prompt rename safety", "prompt.rename"),
        ("Prompt lifecycle", "prompt.lifecycle"),
        ("Execution scope", "execution.scope"),
        ("Session recovery", "execution.sessions-recovery"),
        ("Future-wave replan", "execution.replan"),
        ("Secret persistence gate", "execution.secret-gate"),
        ("Sequential fallback", "execution.sequential-fallback"),
        ("Managed outer lease", "interop.outer-lease-v2"),
        ("Task Implementer interoperability", "interop.task-implementer-compatibility"),
        ("Steering continuation", "steering.continuation"),
        ("Verifier self-tests", "verifier.self-tests"),
        ("Three-tier harness contract", "three-tier.harness"),
        ("Hook registration", "hooks.registration"),
        ("Hook payload parity", "hooks.payload-parity"),
        ("Live evidence integrity", "live.evidence-integrity"),
        ("Golden-path live run", "live.golden-path"),
        ("Idempotency live run", "live.idempotency"),
        ("Change-request live run", "live.change-request"),
        ("Failure-routing live run", "live.failure-routing"),
        ("Auto-steering live run", "live.auto-steering"),
        ("Documentation live run", "live.documentation-update"),
        ("Continuation live run", "live.steering-continuation"),
    ]
    rows: list[tuple[str, str]] = []
    for label, capability_id in capability_rows:
        checks = [check for check in ctx.checks if check.capability_id == capability_id]
        if not checks:
            status = (
                "NOT APPLICABLE" if capability_id == "hooks.payload-parity" else "WARN"
            )
        elif any(check.status == "FAIL" for check in checks):
            status = "FAIL"
        elif any(check.status == "WARN" for check in checks):
            status = "PARTIAL"
        else:
            status = "PASS"
        rows.append((label, status))
    return rows


def summarize_skill_matrix(ctx: Context) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for skill in REQUIRED_EVIDENCE_SKILLS:
        checks = [
            check for check in ctx.checks if check.capability_id == f"skill.{skill}"
        ]
        if any(check.status == "FAIL" for check in checks):
            status = "FAIL"
        elif any(check.status == "WARN" for check in checks) or not checks:
            status = "PARTIAL"
        else:
            status = "PASS"
        rows.append((skill, SKILL_EVIDENCE_BASES[skill], status))
    return rows


def report(ctx: Context) -> str:
    status = final_status(ctx)
    now = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    by_section: dict[str, list[Check]] = {}
    for check in ctx.checks:
        by_section.setdefault(check.section, []).append(check)
    lines = [
        "# Agentic SDLC Verification Report",
        "",
        "## Summary",
        "",
        f"- Final readiness status: {status}",
        f"- Generated at: {now}",
        f"- Verification root: `{ctx.verification_root}`",
        f"- Disposable Git root: `{ctx.disposable_project}`",
        f"- Selected nested project: `{ctx.selected_project}`",
        f"- Live evidence manifest: `{ctx.live_evidence_path}`",
        "- Mode: deterministic preflight plus optional, machine-validated private live evidence.",
        "",
        "## Readiness Matrix",
        "",
        "| Area | Status |",
        "| --- | --- |",
    ]
    for label, value in summarize_matrix(ctx):
        lines.append(f"| {label} | {value} |")
    lines.extend(
        [
            "",
            "## Agentic SDLC Skill Matrix",
            "",
            "| Skill | Evidence basis | Status |",
            "| --- | --- | --- |",
        ]
    )
    for skill, basis, value in summarize_skill_matrix(ctx):
        lines.append(f"| {skill} | {basis} | {value} |")
    lines.append("")
    for section in [
        "Environment checked",
        "Capability regression results",
        "Skill discovery results",
        "Hook configuration results",
        "PreToolUse safety test results",
        "Stop continuation test results",
        "Disposable SDLC golden-path run results",
        "Idempotency results",
        "Failure-loop results",
        "Steering behavior results",
        "Live workflow results",
        "Agentic SDLC skill results",
    ]:
        lines.extend([f"## {section}", ""])
        for check in by_section.get(section, []):
            lines.append(f"- {check.status}: {check.name} - {check.detail}")
        if section not in by_section:
            lines.append("- WARN: Not run.")
        lines.append("")
    gaps = [check for check in ctx.checks if check.status in {"FAIL", "WARN"}]
    lines.extend(["## Gaps found", ""])
    if gaps:
        for check in gaps:
            lines.append(f"- {check.section}: {check.name} - {check.detail}")
    else:
        lines.append("- None found in preflight.")
    lines.extend(["", "## Validation commands", ""])
    lines.extend(
        [
            "- `python3 -m unittest -v sdlc-start/scripts/test_prompt_workspace.py sdlc-start/scripts/test_sdlc_start_contract.py`",
            "- `python3 -m unittest discover -v -s sdlc-prepare-execution/scripts -p 'test_*.py'`",
            "- `python3 sdlc-implement-plan/scripts/test_worker_dispatch.py -v`",
            "- `python3 worktree/scripts/test-worktree-manager.py -v`",
            "- `python3 task-implementer/scripts/test-worktree-interoperability.py -v`",
            "- `python3 sdlc-start/assets/hooks/tests/test_sdlc_hooks.py -v`",
            "- `python3 sdlc-evaluate/scripts/test_observability_contract.py -v`",
            "- `python3 sdlc-workflow-test/scripts/test_verify_agentic_sdlc.py -v`",
        ]
    )
    lines.extend(["", "## Skipped live or external checks", ""])
    missing_live = [
        lane
        for lane in LIVE_LANES
        if not any(
            check.capability_id == f"live.{lane}" and check.status != "WARN"
            for check in ctx.checks
        )
    ]
    if missing_live:
        lines.append(
            "- Live lanes without validated PASS or FAIL evidence: "
            + ", ".join(missing_live)
            + "."
        )
    else:
        lines.append("- None; all required live lanes supplied validated results.")
    lines.append(
        "- Real Codex session behavior is not synthesized by this verifier; only supplied private evidence is accepted."
    )
    lines.extend(["", "## Recommended fixes", ""])
    if gaps:
        for check in gaps[:10]:
            lines.append(
                f"- Address `{check.name}` in `{check.section}` and rerun `$sdlc-workflow-test`."
            )
    else:
        lines.append("- No fixes required.")
    lines.extend(["", "## Low-risk real repository recommendation", ""])
    if status == "PASS":
        lines.append(
            "- YES: deterministic capabilities and all required live lanes passed. Start with a low-risk repository and preserve the same private evidence boundary."
        )
    else:
        lines.append(
            "- NO: resolve every deterministic failure and provide all required live-lane evidence before using a real repository."
        )
    lines.extend(["", f"## Final readiness status: {status}", ""])
    return "\n".join(lines)


def write_report(ctx: Context, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = report(ctx)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.write("\n")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def main(argv: list[str]) -> int:
    ns = parse_args(argv)
    ctx = setup_context(ns)
    requested_root = ns.verification_root.expanduser().absolute()
    root_problem = verification_root_problem(ctx, requested_root)
    if root_problem:
        print(root_problem, file=sys.stderr)
        return 2
    requested_report = ns.report or (requested_root / "report.md")
    report_path = private_output_path(
        requested_report,
        ctx.verification_root,
        requested_root=requested_root,
    )
    if report_path is None:
        print(
            "Report path must be a non-symlinked file under the verification root.",
            file=sys.stderr,
        )
        return 2
    root_problem = prepare_verification_root(ctx, requested_root)
    if root_problem:
        print(root_problem, file=sys.stderr)
        return 2
    check_design(ctx)
    check_vertical_slice_contract(ctx)
    check_execution_plane_contract(ctx)
    check_repair_loop_contract(ctx)
    check_skill_discovery(ctx)
    check_hook_config(ctx)
    setup_disposable_project(ctx)
    check_prompt_workspace(ctx)
    check_capability_regressions(ctx)
    check_hooks_with_fixtures(ctx)
    add_agent_required_sections(ctx)
    write_report(ctx, report_path)
    print(f"Report path: {report_path}")
    print(f"Final readiness status: {final_status(ctx)}")
    failures = [check for check in ctx.checks if check.status == "FAIL"]
    warnings = [check for check in ctx.checks if check.status == "WARN"]
    print(f"Failures: {len(failures)}")
    print(f"Warnings: {len(warnings)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
