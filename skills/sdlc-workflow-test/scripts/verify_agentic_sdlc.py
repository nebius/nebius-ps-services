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



# BEGIN shared runtime bootstrap
def _load_skill_support(group, anchor_file, declared_path, *, source_only=False):
    import hashlib as _hashlib
    import os as _os
    from pathlib import Path as _Path
    import stat as _stat
    import sys as _sys
    from types import ModuleType as _ModuleType

    def read_source(path):
        path = _Path(_os.path.abspath(path))
        for part in (*reversed(path.parents), path):
            metadata = part.lstat()
            if _stat.S_ISLNK(metadata.st_mode):
                if metadata.st_uid != 0 or part == path:
                    raise ImportError("shared runtime path contains an unsafe symlink")
                metadata = part.stat()
            if part != path:
                sticky = metadata.st_uid == 0 and metadata.st_mode & _stat.S_ISVTX
                if (not _stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in {0, _os.getuid()}
                        or metadata.st_mode & 0o022 and not sticky):
                    raise ImportError("unsafe shared runtime ancestry")
        path = path.resolve(strict=True)
        descriptor = _os.open(path.anchor, _os.O_RDONLY | _os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                child = _os.open(part, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW, dir_fd=descriptor)
                _os.close(descriptor)
                descriptor = child
                metadata = _os.fstat(descriptor)
                sticky = metadata.st_uid == 0 and metadata.st_mode & _stat.S_ISVTX
                if metadata.st_uid not in {0, _os.getuid()} or metadata.st_mode & 0o022 and not sticky:
                    raise ImportError("unsafe shared runtime ancestry")
            child = _os.open(path.name, _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_NONBLOCK, dir_fd=descriptor)
            try:
                before = _os.fstat(child)
                if (not _stat.S_ISREG(before.st_mode) or before.st_uid != _os.getuid()
                        or before.st_mode & 0o022 or before.st_nlink != 1 or before.st_size > 1048576):
                    raise ImportError("unsafe shared runtime source")
                data = bytearray()
                while chunk := _os.read(child, min(65536, 1048577 - len(data))):
                    data.extend(chunk)
                    if len(data) > 1048576:
                        raise ImportError("shared runtime source exceeds size limit")
                after = _os.fstat(child)
                bound = _os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                def identity(value):
                    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                if identity(before) != identity(after) or identity(after) != identity(bound):
                    raise ImportError("shared runtime source changed while reading")
                return bytes(data), identity(after)
            finally:
                _os.close(child)
        finally:
            _os.close(descriptor)

    anchor = _Path(_os.path.abspath(anchor_file))
    declared_paths = (declared_path,) if isinstance(declared_path, str) else declared_path
    candidates = []
    for declared in declared_paths:
        relative = _Path(declared)
        if tuple(anchor.parts[-len(relative.parts):]) == relative.parts:
            catalog = anchor.parents[len(relative.parts) - 1]
            candidates.append(("catalog", catalog, catalog / "global-context-management/scripts"))
    if not source_only:
        flat = anchor.parent.parent if anchor.parent.name == "lib" else anchor.parent
        candidates.append(("flat", flat, flat))
        agent = "codex" if _os.environ.get("CODEX_THREAD_ID") else _os.environ.get("SKILLS_AGENT", "codex")
        if agent not in {"codex", "claude"}:
            raise ImportError("SKILLS_AGENT must be codex or claude")
        key, default = ("CODEX_HOME", ".codex") if agent == "codex" else ("CLAUDE_CONFIG_DIR", ".claude")
        home = _Path(_os.environ.get(key, str(_Path.home() / default))).expanduser()
        if not home.is_absolute():
            raise ImportError("shared runtime home must be absolute")
        candidates.append(("flat", home / "hooks", home / "hooks"))
    for kind, root, support in candidates:
        loader_path = support / "trusted_runtime.py"
        if not loader_path.exists() and not loader_path.is_symlink():
            if ((kind == "catalog" and (support.exists() or support.is_symlink()))
                    or any((support / name).exists() or (support / name).is_symlink()
                           for name in ("agent_runtime.py", "hook_runtime.py", "task_state_permissions.py"))):
                raise ImportError("incomplete shared runtime bundle; reinstall current support")
            continue
        data, identity = read_source(loader_path)
        digest = _hashlib.sha256(data).hexdigest()
        cache_name = "_skills_trusted_runtime"
        loader = _sys.modules.get(cache_name)
        provenance = (str(loader_path), identity, digest)
        if cache_name in _sys.modules:
            if (type(loader) is not _ModuleType or getattr(loader, "_bootstrap_provenance", None) != provenance):
                raise ImportError("conflicting shared runtime loader")
        else:
            loader = _ModuleType(cache_name)
            loader.__file__ = str(loader_path)
            exec(compile(data, str(loader_path), "exec"), loader.__dict__)
            loader._bootstrap_provenance = provenance
            loader._read_source = read_source
            _sys.modules[cache_name] = loader
        return loader.load_support(group, anchor=(kind, root), source_only=source_only)
    raise ImportError("Shared skill runtime unavailable; install the complete current skill support")
# END shared runtime bootstrap
_load_skill_support('runtime', __file__, 'sdlc-workflow-test/scripts/verify_agentic_sdlc.py')

from agent_runtime import agent_home, agent_name, installed_skills_dir, runtime_environment  # noqa: E402


# The verifier is also loaded by file location from outside its scripts directory.
# Resolve the semantic validator's sibling modules from this exact catalog copy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import verification_workspace as workspace  # noqa: E402
import verification_suites as suite_checks  # noqa: E402

SEMANTICS_PATH = Path(__file__).resolve().with_name("three_tier_semantics.py")
SEMANTICS_SPEC = importlib.util.spec_from_file_location(
    "sdlc_workflow_test_three_tier_semantics", SEMANTICS_PATH
)
if SEMANTICS_SPEC is None or SEMANTICS_SPEC.loader is None:
    raise RuntimeError("Could not load the three-tier semantic validator.")
three_tier_semantics = importlib.util.module_from_spec(SEMANTICS_SPEC)
SEMANTICS_SPEC.loader.exec_module(three_tier_semantics)

ORIGIN_SPEC = importlib.util.spec_from_file_location(
    "sdlc_workflow_test_owned_git_origin", Path(__file__).with_name("owned_git_origin.py")
)
if ORIGIN_SPEC is None or ORIGIN_SPEC.loader is None:
    raise RuntimeError("Could not load the owned Git origin validator.")
owned_git_origin = importlib.util.module_from_spec(ORIGIN_SPEC)
ORIGIN_SPEC.loader.exec_module(owned_git_origin)

REQUIRED_SDLC_SKILLS = (
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
    "align",
    "worktree",
    "nebius-grafana-query",
    "troubleshoot",
)
SOURCE_PARITY_SKILLS = (
    *REQUIRED_SDLC_SKILLS,
    *REQUIRED_RUNTIME_SUPPORT_SKILLS,
    # Shared semantic/advisory/delivery owners are installed dependencies, not
    # additional golden-path phases or project-lifecycle admission gates.
    "maintain-project-specs",
    "implement",
    "global-context-management",
    "design",
    "research",
    "code-review",
    "apply-security",
    "linter",
    "create-pr",
    "review-pr",
    "commit",
    "merge-pr",
    "sdlc-workflow-test",
)

DESCRIPTION_PREFIX = "Use only as part of the Agentic SDLC workflow;"
LEGACY_SPEC_OWNER_TERMS = (
    "Only `sdlc-create-requirements` writes",
    "Only `sdlc-create-design` writes",
    "ownership-receipted v2",
    "v2-managed selected-project",
    'created_by_skill: "sdlc-create-requirements"',
    'updated_by_skill: "sdlc-create-requirements"',
    'created_by_skill: "sdlc-create-design"',
    'updated_by_skill: "sdlc-create-design"',
    "Agentic SDLC-owned `docs/design.md`",
)
MARKDOWNLINT_TEMPLATE_ENVELOPE = "MD003 MD022 MD033 MD041"
DESIGN_REQUIRED_TERMS = (
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
    "$sdlc-start <objective>",
    "$sdlc-start continue [project-folder]",
    "workflow/accepted-objective-v2",
    "workflow/session-binding-v1",
    "immutable execution basis",
    "normalized objective",
    "ALREADY_COMPLETE",
    "allow_implicit_invocation: false",
    "~/.codex/sdlc-verification/report.md",
    "does not deny filesystem targets by path",
    "Ordinary outbound network commands",
    "secret-bearing",
    "MCP payloads",
    "guarded Git or GitHub actions",
    "source-installed parity",
    "immutable execution basis",
    "task-recover",
    "replan-future",
    "sequential fallback",
    "v4 outer",
    "verification-live-results-v3",
    "predefined runtime operational criterion",
    "non-Grafana provenance",
    "installed `nebius-grafana-query`",
    "missing, stale, contended, or invalid lifecycle evidence never blocks an SDLC phase",
    "publication-only mode",
    "findings-and-readiness-only mode",
    'phase: "create-pr"',
    'uat_status: "passed"',
)
DEFAULT_PROJECT_ID = "sdlc-verification-project"
DEFAULT_RUN_ID = "run-active"
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
    "sdlc-start",
    "sdlc-create-requirements",
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
        "align": "live",
        "sdlc-start": "deterministic",
        "sdlc-prepare-execution": "deterministic",
        "sdlc-implement-plan": "deterministic",
        "sdlc-merge-pr": "safety-only",
    }
)
REQUIRED_EVIDENCE_SKILLS = tuple(SKILL_EVIDENCE_BASES)
SKILL_REQUIRED_ASSERTIONS = {
    "align": [
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
    "sdlc-gui-test": ["headless_playwright_harness", "semantic_gui_assertions"],
    "sdlc-implement-plan": ["worker_contract_tests", "scoped_integration"],
    "sdlc-merge-pr": ["merge_guardrails_tested", "no_real_merge_performed"],
    "sdlc-prepare-execution": ["execution_scheduler_tests", "recovery_contract_tests"],
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
    "lightweight": "workflow/accepted-objective-v2",
    "three-tier": "agentic-sdlc/three-tier-results-v3",
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
        "objective.intake",
        "objective.revisions",
        "objective.binding",
        "objective.lifecycle",
        "spec.validation-receipt",
        "source.obligations",
        "program.source-admission",
    },
    "sdlc-prepare-execution": {
        "execution.scope",
        "execution.sessions-recovery",
        "execution.replan",
        "execution.secret-gate",
        "git.promotion-safety",
        "program.phase-slices",
        "program.completion-freshness",
        "execution.bounded-scale",
    },
    "sdlc-implement-plan": {
        "execution.sequential-fallback",
        "execution.worker-liveness",
        "interop.outer-lease-v4",
        "interop.shared-execution",
        "interop.implement-promotion",
        "execution.bounded-scale",
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
        "align": {"golden-path", "documentation-update"},
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
    "objectives",
    "bases",
    "bindings",
    "execution",
}
PRIVATE_REPOSITORY_FILES = {
    "STEERING.md",
    "active-run.json",
    "active.lock",
    "current-state.json",
    "feature-queue.json",
    "prompt.json",
    "objective.json",
    "host-capacity.json",
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
    host_home: Path
    verification_root: Path
    disposable_project: Path
    selected_project: Path
    fixture_host_home: Path
    live_evidence_path: Path
    agent: str = field(default_factory=agent_name)
    checks: list[Check] = field(default_factory=list)
    suite_results: list[suite_checks.SuiteResult] = field(default_factory=list)
    report_path: Path | None = None
    progress: bool = False
    interrupted: bool = False
    in_progress: bool = False

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
    source_identity: str,
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
            source_identity=source_identity,
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
    # The accepted objective and failure summaries do not independently prove all
    # seven live lanes. Stay fail-closed until the collector can derive exact
    # claims from checkpoint, Git, test, steering, and repaired-failure bytes.
    return None


def validated_three_tier_claims(
    ctx: Context,
    sources: list[Path],
    *,
    source_identity: str,
) -> set[str] | None:
    if re.fullmatch(r"[0-9a-f]{32}", source_identity) is None:
        return None
    root = ctx.verification_root
    run_root = root / "three-tier-live" / "runs" / source_identity
    canonical = run_root / "evidence" / "three-tier-results.json"
    active_state = root / "three-tier-live" / "active.json"
    if any(
        has_symlink_component(path, root)
        or not path.is_file()
        or path.stat().st_nlink != 1
        or path.stat().st_uid != os.getuid()
        or path.stat().st_mode & 0o077
        for path in (active_state, canonical)
    ):
        return None
    canonical_digest = file_sha256(canonical)
    if canonical_digest is None or len([
        path for path in sources if file_sha256(path) == canonical_digest
    ]) != 1:
        return None
    try:
        # Execute only the caller-configured installation, never a code path
        # supplied by evidence. A fresh process keeps source module caches out
        # of the exact installed execution-basis and semantic validation.
        catalog = ctx.global_skills_dir
        helper = catalog / "sdlc-workflow-test/scripts/three_tier_lifecycle.py"
        if catalog.resolve() != catalog or has_symlink_component(helper, catalog) or not helper.is_file():
            return None
        result = run(
            [sys.executable, "-B", str(helper), "--verification-root", str(root),
             "--expected-verification-id", source_identity, "validate-results"],
            env=runtime_environment(ctx.agent),
            timeout=suite_checks.CAPABILITY_SUITE_TIMEOUT_SECONDS["three-tier"],
        )
        if result.returncode != 0:
            return None
        admission = json.loads(result.stdout)
        if (
            not isinstance(admission, dict)
            or set(admission) != {"status", "verification_id", "canonical_sha256",
                                  "catalog_root", "baseline_sha", "promoted_sha"}
            or admission["status"] != "PASS"
            or admission["verification_id"] != source_identity
            or admission["catalog_root"] != str(catalog)
            or admission["canonical_sha256"] != canonical_digest
            or file_sha256(canonical) != canonical_digest
            or any(not isinstance(admission[key], str) or SHA_RE.fullmatch(admission[key]) is None
                   for key in ("baseline_sha", "promoted_sha"))
            or admission["baseline_sha"] == admission["promoted_sha"]
        ):
            return None
    except (
        AttributeError,
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
        owned_git_origin.OriginError,
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
    parser = argparse.ArgumentParser(
        description="Run safe Agentic SDLC static and hook preflight verification.",
        allow_abbrev=False,
    )
    parser.add_argument("--agent", choices=("codex", "claude"), default=agent_name())
    parser.add_argument("--skills-root", type=Path, default=skills_root)
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    parser.add_argument("--design", type=Path, default=default_design_path(skills_root))
    parser.add_argument(
        "--global-skills-dir", type=Path, default=None
    )
    parser.add_argument("--agent-home", dest="host_home", type=Path, default=None)
    parser.add_argument(
        "--verification-root", type=Path, default=None
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
        help="Private aggregate live-results manifest; owned Docker/browser results are resolved from its profile identity. Defaults to <verification-root>/live-results.json.",
    )
    args = parser.parse_args(argv)
    args.host_home = args.host_home or agent_home(args.agent)
    args.global_skills_dir = args.global_skills_dir or (args.host_home / "skills" if args.agent == "claude" else installed_skills_dir(args.agent))
    args.verification_root = args.verification_root or args.host_home / "sdlc-verification"
    return args


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


def git_output(project: Path, *args: str, bootstrap: bool = False) -> str | None:
    try:
        env = owned_git_origin.environment(project, bootstrap=bootstrap)
    except owned_git_origin.OriginError:
        return None
    result = run(["git", *args], cwd=project, timeout=15, env=env)
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
    fixture_host_home = verification_root / "fixture-agent-home"
    return Context(
        agent=ns.agent,
        skills_root=ns.skills_root.expanduser().resolve(strict=False),
        repo_root=ns.repo_root.expanduser().resolve(strict=False),
        design_path=ns.design.expanduser().resolve(strict=False),
        global_skills_dir=ns.global_skills_dir.expanduser().resolve(strict=False),
        host_home=ns.host_home.expanduser().resolve(strict=False),
        verification_root=verification_root,
        disposable_project=disposable_project,
        selected_project=selected_project,
        fixture_host_home=fixture_host_home,
        live_evidence_path=(
            ns.live_evidence.expanduser().absolute()
            if ns.live_evidence is not None
            else verification_root / "live-results.json"
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
    canonical_root = (ctx.host_home / "sdlc-verification").resolve(strict=False)
    broad_roots = {
        Path("/").resolve(strict=False),
        Path.home().resolve(strict=False),
        ctx.host_home.resolve(strict=False),
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
    try:
        workspace.admit_root(requested_root, create=True, source_root=ctx.repo_root, host_home=ctx.host_home)
    except (workspace.WorkspaceError, OSError) as error:
        return str(error)
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
    normalized_text = " ".join(text.split())
    missing = [
        term
        for term in DESIGN_REQUIRED_TERMS
        if " ".join(term.split()) not in normalized_text
    ]
    status = "PASS" if not missing else "FAIL"
    detail = "Design document contains core SDLC and verification contract terms."
    if missing:
        detail = "Missing expected design terms: " + ", ".join(missing)
    ctx.add("Environment checked", "Design contract", status, detail)


def check_source_phase_contract(ctx: Context) -> None:
    problems: list[str] = []
    for required in REQUIRED_SDLC_SKILLS:
        folder = ctx.skills_root / required
        skill_md = folder / "SKILL.md"
        meta = frontmatter(skill_md)
        if meta.get("name") != required:
            problems.append(f"{required}: invalid or missing source name")
        description = meta.get("description", "")
        if not description.startswith(DESCRIPTION_PREFIX):
            problems.append(f"{required}: source description prefix is invalid")
        policy = openai_invocation_policy(folder / "agents" / "openai.yaml")
        if policy != "false":
            problems.append(f"{required}: source invocation policy is not false")
    ctx.add(
        "Environment checked",
        "Source phase-skill contract",
        "PASS" if not problems else "FAIL",
        "All required source phase skills have canonical metadata and explicit-only policy."
        if not problems
        else "; ".join(problems),
        capability_id="source.phase-contract",
    )


def check_source_catalog_validation(ctx: Context) -> None:
    validator = (
        ctx.skills_root / "align-skill" / "scripts" / "validate-skill-structure.py"
    )
    if not validator.is_file():
        ctx.add(
            "Environment checked",
            "Source catalog structure",
            "FAIL",
            f"Shared source-catalog validator is missing: {validator}",
            capability_id="source.catalog-structure",
        )
        return
    result = run(
        [sys.executable, "-B", str(validator), str(ctx.skills_root)],
        cwd=ctx.skills_root,
        timeout=120,
    )
    output = (result.stdout + result.stderr).strip().splitlines()
    summary = output[-1] if output else "Validator returned no output."
    ctx.add(
        "Environment checked",
        "Source catalog structure",
        "PASS" if result.returncode == 0 else "FAIL",
        summary,
        capability_id="source.catalog-structure",
    )


def check_spec_ownership_contract(ctx: Context) -> None:
    problems: list[str] = []

    def normalized(path: Path) -> str:
        return " ".join(read_text(path).split())

    design = normalized(ctx.design_path)
    design_terms = (
        "`maintain-project-specs` is the single semantic, schema, template, validation, and receipt owner",
        "`sdlc-create-requirements` and `sdlc-create-design` are routed authoring adapters",
        "adapter invokes that shared validator without defining a second spec schema or receipt authority",
        "The canonical result is required before spec-dependent planning and dispatch",
        "historical private lifecycle-phase evidence remains advisory and never becomes an independent SDLC gate",
    )
    for term in design_terms:
        if term not in design:
            problems.append(f"design contract missing: {term}")

    adapters = {
        "sdlc-create-requirements": "This skill may change requirements only while routed as its Agentic SDLC authoring adapter",
        "sdlc-create-design": "This skill may change design only while routed as its Agentic SDLC authoring adapter",
    }
    for skill, term in adapters.items():
        text = normalized(ctx.skills_root / skill / "SKILL.md")
        if term not in text or "`maintain-project-specs` owns both canonical documents" not in text:
            problems.append(f"{skill}: shared-owner adapter invariant is missing")

    common_owner_term = (
        "`maintain-project-specs` is the sole semantic, schema, and validation "
        "owner of both canonical specs"
    )
    common_phases = set(REQUIRED_SDLC_SKILLS) - {
        "sdlc-create-requirements",
        "sdlc-create-design",
        "sdlc-start",
    }
    for skill in sorted(common_phases):
        text = normalized(ctx.skills_root / skill / "SKILL.md")
        if not (common_owner_term in text or
                "`maintain-project-specs` is the sole semantic, schema, paired-publication, and validation owner of both canonical specs" in text):
            problems.append(f"{skill}: positive shared-owner invariant is missing")

    start = normalized(ctx.skills_root / "sdlc-start" / "SKILL.md")
    if (
        "`maintain-project-specs` is the sole semantic, schema, paired-publication, and validation owner" not in start
        or "write only as its Agentic SDLC adapters" not in start
    ):
        problems.append("sdlc-start: shared-owner coordinator invariant is missing")

    owner = normalized(ctx.skills_root / "maintain-project-specs" / "SKILL.md")
    if (
        "This skill is the only semantic owner of project-spec schemas" not in owner
        or "stable IDs, traceability, paired publication, and strict validation" not in owner
    ):
        problems.append("maintain-project-specs: authoritative owner contract is missing")

    adapter = normalized(
        ctx.skills_root / "sdlc-start" / "scripts" / "validate_project_specs.py"
    )
    if (
        "Advisory Agentic SDLC adapter for project-spec inspection" not in adapter
        or '"maintain-project-specs" / "scripts"' not in adapter
        or "except ProjectSpecError as error" not in adapter
        or '"status": "advisory"' not in adapter
    ):
        problems.append("sdlc-start: shared project-spec validator adapter is invalid")

    workflow_test = normalized(ctx.skills_root / "sdlc-workflow-test" / "SKILL.md")
    workflow_sequence = "`sdlc-create-design`, `sdlc-auto-steering`"
    if workflow_sequence not in workflow_test:
        problems.append(
            "sdlc-workflow-test: auto-steering is missing after design"
        )

    readme = read_text(ctx.skills_root / "sdlc-create-requirements" / "README.md")
    boundary_match = re.search(
        r"^## Main Boundaries\s*$\n(?P<body>.*?)(?=^## |\Z)",
        readme,
        flags=re.MULTILINE | re.DOTALL,
    )
    boundary_lines = (
        [
            line.strip()
            for line in boundary_match.group("body").splitlines()
            if line.strip().startswith("-")
        ]
        if boundary_match
        else []
    )
    if not boundary_lines or any(
        not line.startswith("- Do not ") for line in boundary_lines
    ):
        problems.append("sdlc-create-requirements README boundaries are not prohibitions")

    template_contracts = {
        "requirements": ctx.skills_root
        / "sdlc-create-requirements"
        / "assets"
        / "templates"
        / "requirements.md.template",
        "design": ctx.skills_root
        / "sdlc-create-design"
        / "assets"
        / "templates"
        / "design.md.template",
    }
    for kind, path in template_contracts.items():
        text = read_text(path)
        lines = text.splitlines()
        expected_disable = (
            f"<!-- markdownlint-disable {MARKDOWNLINT_TEMPLATE_ENVELOPE} -->"
        )
        expected_enable = (
            f"<!-- markdownlint-enable {MARKDOWNLINT_TEMPLATE_ENVELOPE} -->"
        )
        required_terms = (
            f"<!-- maintain-project-specs:{kind}:start schema=maintain-project-specs/{kind}-v2 -->",
            f"schema: maintain-project-specs/{kind}-v2",
            f"<!-- maintain-project-specs:{kind}:end -->",
            'created_by_skill: "maintain-project-specs"',
            'updated_by_skill: "maintain-project-specs"',
        )
        if (
            not lines
            or lines[0] != expected_disable
            or lines[-1] != expected_enable
            or any(term not in text for term in required_terms)
        ):
            problems.append(f"{kind} template ownership or lint envelope is invalid")

    legacy_paths = [
        ctx.design_path,
        ctx.skills_root / "README.md",
        ctx.skills_root
        / "sdlc-start"
        / "references"
        / "objective-refinement.md",
        *(ctx.skills_root / skill / "SKILL.md" for skill in REQUIRED_SDLC_SKILLS),
    ]
    for path in legacy_paths:
        text = read_text(path)
        for term in LEGACY_SPEC_OWNER_TERMS:
            if term in text:
                problems.append(f"{path}: legacy ownership term remains: {term}")

    ctx.add(
        "Environment checked",
        "Canonical spec ownership",
        "PASS" if not problems else "FAIL",
        "Shared spec ownership, routed adapters, template envelopes, and workflow ordering are canonical."
        if not problems
        else "; ".join(problems),
        capability_id="spec.ownership-contract",
    )


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
                "# SLICE-<id> Plan v<N>",
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
                "against the locked plan and its End-To-End Slice",
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
                "When the locked plan defines an end-to-end slice",
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
        "align skill": (
            ctx.skills_root / "align" / "SKILL.md",
            [
                "Compare implementation against tests, CLI help, examples, workflows, and documentation",
                "Make the smallest effective change only when the issue is clear",
                "Run the mandatory changed-scope quality gate",
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
        normalized_text = " ".join(text.split())
        missing = [
            term for term in terms if " ".join(term.split()) not in normalized_text
        ]
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
                "Dispatch fresh task workers into ready slots",
                "unknown permits one total worker",
                "task-recover",
                "task-arm",
                "task-requeue",
                "task-heartbeat",
                "task-watch",
                "replan-future",
                "explicit merge commits",
                "fast-forward the unchanged clean",
                "workflow/execution-coordinator-v3",
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
                "Every task receives a fresh native agent",
                "wave-integrate",
                "wave-complete",
                "one direct-child commit",
                "task-admit",
                "wave-refill",
                "verified_no_change",
                "validate-target",
            ],
        ),
        "shared sequential native fallback": (
            ctx.skills_root / "worktree/scripts/workflow_dispatch.py",
            ["--ephemeral", "workspace-write", "--add-dir", "fresh_session=True"],
        ),
        "workflow state and immutable execution basis": (
            ctx.skills_root / "sdlc-start" / "references" / "state-schema.md",
            [
                '"state_version": 2',
                "workflow/execution-coordinator-v3",
                "sdlc-prepare-execution",
                "execution/FEAT-001/coordinator.json",
                "bindings/<native-session-sha256>.json",
                'phase: "create-pr"',
                "expected_head",
                "base_head",
                'uat_status: "passed"',
                "outer-integration-pending",
                "recorded primary path",
                "$worktree integrate",
                "fresh explicit user invocation",
                "from that primary checkout",
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
                "base_head: <recorded-default-head>",
                'uat_status: "passed"',
                "gh pr create --base <origin-default> --head <branch>",
                "Do not use a shell wrapper",
            ],
        ),
        "commit-push managed publication guard": (
            ctx.skills_root / "commit-push" / "SKILL.md",
            [
                "publication-guard --publication-action push",
                "before staging, committing, fetching, or pushing",
                "Publish only from the accumulated source branch",
            ],
        ),
        "create-pr managed publication guard": (
            ctx.skills_root / "create-pr" / "SKILL.md",
            [
                "publication-guard --publication-action create-pr",
                "Before staging, committing, fetching for publication",
                "Only a genuinely unmanaged manual worktree may pass as `unmanaged`",
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
        "active SDLC protected delivery mode": (
            ctx.skills_root / "sdlc-merge-pr" / "SKILL.md",
            ["accepted run-level opt-in", "attest-delivery", "submit-delivery",
             "reconcile-delivery", "merged-verification-failed", "Queue rules fail closed"],
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
                "github_delivery_effect",
                "protected delivery adapter",
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
                "`agentic-sdlc/execution-coordinator-v1` through `v7`",
                "workflow/execution-coordinator-v3",
                "sdlc-update-documents, then align",
            ],
        ),
    }
    for name, (path, terms) in checks.items():
        text = read_text(path)
        missing = [term for term in terms if " ".join(term.split()) not in " ".join(text.split())]
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
            ctx.skills_root / "sdlc-classify-failure" / "scripts" / "repair_control.py",
            [
                "agentic-sdlc/failure-event-v2",
                "agentic-sdlc/diagnosis-v3",
                "agentic-sdlc/design-approval-v2",
                "agentic-sdlc/repair-control-v2",
                "agentic-sdlc/revalidation-evidence-v2",
                "blocked_missing_evidence",
                "localized_limit",
                "design_limit",
                "feature_dispatch_limit",
                "blocked_semantic_cycle",
            ],
        ),
        "conditional troubleshooting handoff reference": (
            ctx.skills_root / "troubleshoot" / "SKILL.md",
            ["references/workflow-handoffs.md"],
        ),
        "conditional troubleshooting contract": (
            ctx.skills_root / "troubleshoot" / "references" / "workflow-handoffs.md",
            [
                "Agentic SDLC Diagnostic Mode",
                "Return one `diagnosis-v3` to `sdlc-classify-failure`",
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
                "repair-control-v2",
                "conditional diagnostic branch",
                "60 active",
                "four repair dispatches",
                "revalidation-cursor-v2",
            ],
        ),
        "corrective execution assignment": (
            ctx.skills_root
            / "worktree"
            / "scripts"
            / "workflow_execution.py",
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
        "align": (
            "General alignment runtime dependency",
            "runtime.align-dependency",
        ),
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
        if not (source / "SKILL.md").is_file() or not (installed / "SKILL.md").is_file():
            mismatches.append(name)
        elif source.resolve(strict=False) == installed.resolve(strict=False):
            continue
        elif source_digest != installed_digest:
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
    host_home: Path,
    agent: str = "codex",
) -> bool:
    expanded = command.replace("${CODEX_HOME:-$HOME/.codex}", str(host_home)).replace(
        "${CODEX_HOME}", str(host_home)
    )
    expanded = expanded.replace("$CODEX_HOME", str(host_home))
    if any(token in expanded for token in ("\n", "\r", "$(", "`")):
        return False
    try:
        words = shlex.split(expanded)
    except ValueError:
        return False
    shell_controls = {";", "&&", "||", "|", "&", ">", ">>", "<", "<<"}
    if not words or any(word in shell_controls for word in words):
        return False
    if words == ["python3", str(host_home / "hooks/hook_runtime.py"), "--agent", agent,
                 "--hook", expected_path.name]:
        return not has_symlink_component(host_home / "hooks/hook_runtime.py", host_home)
    if agent == "claude":
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


def hook_command_mentions_sdlc(command: str, host_home: Path) -> bool:
    """Keep routing names/arguments visible without treating the home as code."""
    try:
        words = shlex.split(command)
    except ValueError:
        return True
    prefix = str(host_home) + os.sep
    return any("sdlc" in (word[len(prefix):] if word.startswith(prefix) else word).lower()
               for word in words)


def check_hook_config(ctx: Context) -> None:
    hooks_sources: list[tuple[Path, dict[str, Any]]] = []
    malformed_sources: list[Path] = []
    hooks_json = ctx.host_home / ("hooks.json" if ctx.agent == "codex" else "settings.json")
    config_toml = ctx.host_home / "config.toml"
    if hooks_json.exists():
        parsed = load_hooks_json(hooks_json)
        hooks = parsed.get("hooks", {}) if parsed is not None else None
        if isinstance(hooks, dict):
            hooks_sources.append((hooks_json, hooks))
        else:
            malformed_sources.append(hooks_json)
    if ctx.agent == "codex" and config_toml.exists():
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
            f"Optional SDLC hook registration is not configured under {ctx.host_home}",
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
        expected = ctx.host_home / "hooks" / filename
        valid = [
            command
            for command in matching
            if hook_command_targets(command, expected, host_home=ctx.host_home, agent=ctx.agent)
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
        "Stop", "stop_lifecycle_arbiter.py", "Stop SDLC hook configured"
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
        hook_command_mentions_sdlc(command, ctx.host_home) for command in user_prompt_commands
    ):
        ctx.add(
            "Hook configuration results",
            "UserPromptSubmit SDLC routing",
            "FAIL",
            "UserPromptSubmit command tokens outside the owned home prefix mention SDLC routing.",
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
        installed_hook_root = ctx.host_home / "hooks"
        hook_files = [
            "lib/__init__.py",
            "lib/sdlc_policy.py",
            "lib/sdlc_state.py",
        ]
        if pre_found:
            hook_files.append("pre_tool_use_sdlc_policy.py")
        if stop_found:
            hook_files.extend(["stop_sdlc_continue.py", "stop_lifecycle_arbiter.py"])
        if any("hook_runtime.py" in command for _, command in all_commands):
            hook_files.extend(["agent_runtime.py", "hook_runtime.py", "trusted_runtime.py", "task_state_permissions.py", "git_environment.py"])
        mismatches: list[str] = []
        unsafe_symlinks: list[str] = []
        for relative in hook_files:
            source_root = (
                ctx.skills_root / "global-context-management/scripts"
                if relative in {"agent_runtime.py", "hook_runtime.py", "trusted_runtime.py", "task_state_permissions.py", "git_environment.py"}
                else source_hook_root
            )
            source_path = source_root / relative
            installed_path = installed_hook_root / relative
            if source_root.is_symlink() or has_symlink_component(
                source_path, source_root
            ):
                unsafe_symlinks.append(f"source:{relative}")
            if installed_hook_root.is_symlink() or has_symlink_component(
                installed_path, installed_hook_root
            ):
                unsafe_symlinks.append(f"installed:{relative}")
            source_digest = file_digest(source_path) if source_path.is_file() else "missing"
            installed_digest = file_digest(installed_path) if installed_path.is_file() else "missing"
            if (not re.fullmatch(r"[0-9a-f]{64}", source_digest)
                    or not re.fullmatch(r"[0-9a-f]{64}", installed_digest)
                    or source_digest != installed_digest):
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
    return run(["git", *args], cwd=project, timeout=15, env=owned_git_origin.environment(project, bootstrap=True))


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)


def expected_verification_context(ctx: Context, baseline_head: str) -> dict[str, Any]:
    return {
        "schema": VERIFICATION_CONTEXT_SCHEMA if ctx.agent == "codex" else "agentic-sdlc/verification-context-claude-v1",
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
        value.get("schema") != (VERIFICATION_CONTEXT_SCHEMA if ctx.agent == "codex" else "agentic-sdlc/verification-context-claude-v1")
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


@workspace.fixture_setup
def setup_disposable_project(ctx: Context) -> bool:
    first_check = len(ctx.checks)
    project = ctx.disposable_project
    if project.is_symlink() or (project.exists() and not project.is_dir()):
        ctx.add(
            "Disposable SDLC golden-path run results",
            "Disposable project",
            "FAIL",
            "Disposable project root is symlinked or is not a directory; no project files were changed.",
        )
        return False
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
            return False
    project.mkdir(parents=True, exist_ok=True)
    try:
        context = ctx.verification_root / owned_git_origin.git_profile.DIRECTORY / "context.json"
        if not context.exists() and not context.is_symlink():
            if git_path.exists() or any(project.iterdir()):
                raise owned_git_origin.OriginError("Existing fixture has no owned Git profile; use a fresh verification root.")
            owned_git_origin.prepare_configuration(project, ctx.verification_root, "lightweight", kind="lightweight")
        owned_git_origin.preflight(project)
    except owned_git_origin.OriginError as error:
        ctx.add("Disposable SDLC golden-path run results", "Git configuration", "FAIL", str(error))
        return False
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
    fixture_targets = [project / relative for relative in files]

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
        return False
    existing_repository = (project / ".git").exists()
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
            return False
        remotes = [line for line in remote_result.stdout.splitlines() if line]
    owned_fixture = (
        existing_repository
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
            return False
        if not owned_fixture:
            ctx.add(
                "Disposable SDLC golden-path run results",
                "Disposable project",
                "FAIL",
                "Existing clean Git repository is not a canonical verifier-owned fixture; no project files were changed.",
            )
            return False
        if remotes or any(
            (ctx.verification_root / name).exists()
            or (ctx.verification_root / name).is_symlink()
            for name in (owned_git_origin.ORIGIN, owned_git_origin.RECEIPT)
        ):
            try:
                owned_git_origin.validate(project, ctx.verification_root, "lightweight")
            except owned_git_origin.OriginError as error:
                ctx.add(
                    "Disposable SDLC golden-path run results", "Owned local Git origin",
                    "FAIL", str(error),
                )
                return False
    for rel, content in files.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    if not existing_repository:
        try:
            owned_git_origin.create_baseline(project)
        except owned_git_origin.OriginError as error:
            ctx.add("Disposable SDLC golden-path run results", "Git baseline", "FAIL", str(error))
            return False
        commit_message = "initial disposable verification project"
    else:
        git(project, "add", "-A")
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

    baseline_head = git_output(project, "rev-parse", "HEAD", bootstrap=True)
    if baseline_head and SHA_RE.fullmatch(baseline_head):
        try:
            origin = owned_git_origin.initialize(
                project, ctx.verification_root, "lightweight"
            )
        except owned_git_origin.OriginError as error:
            ctx.add(
                "Disposable SDLC golden-path run results", "Owned local Git origin",
                "FAIL", str(error),
            )
            return False
        ctx.add(
            "Disposable SDLC golden-path run results", "Owned local Git origin",
            "PASS", "Exactly one owned local origin retains its frozen baseline.",
        )
        fixture_baseline = origin["baseline_sha"]
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
            return False
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
                    context_path, expected_verification_context(ctx, fixture_baseline)
                )
    else:
        ctx.add(
            "Disposable SDLC golden-path run results",
            "Verification context",
            "FAIL",
            "Disposable baseline Git identity is unavailable.",
        )

    return not any(check.status == "FAIL" for check in ctx.checks[first_check:])


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


class FixtureSetupError(RuntimeError):
    """A bounded deterministic fixture failure that must reach the report."""


def setup_fixture_state(ctx: Context, *, record: bool = True) -> Path:
    run_dir = ctx.fixture_host_home / "sdlc-runs" / DEFAULT_PROJECT_ID / DEFAULT_RUN_ID
    # The verifier may be installed while --skills-root selects source. Keep
    # that catalog's fixture imports out of this process's trusted runtime.
    code = (
        "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "from test_workflow_fixture import seed; "
        "seed(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4],"
        "'verification-session',status='running')"
    )
    env = runtime_environment(ctx.agent, home=ctx.fixture_host_home, fresh_session=True)
    try:
        env = owned_git_origin.environment(ctx.selected_project, env=env)
    except owned_git_origin.OriginError as error:
        raise FixtureSetupError("Hook fixture Git environment is unavailable.") from error
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = run(
        [sys.executable, "-c", code, str(ctx.skills_root / "worktree/scripts"),
         str(run_dir), str(ctx.selected_project), DEFAULT_PROJECT_ID],
        cwd=ctx.selected_project, env=env, timeout=30,
    )
    if result.returncode != 0:
        raise FixtureSetupError(f"Hook fixture seed failed (exit {result.returncode}).")
    write_json(
        run_dir / "current-state.json",
        {
            "project_id": DEFAULT_PROJECT_ID,
            "run_id": DEFAULT_RUN_ID,
            "status": "running",
            "current_execution": "FEAT-001",
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


def check_direct_objectives(ctx: Context) -> None:
    paths = ['worktree/scripts/workflow_intake.py', 'worktree/scripts/workflow_objectives.py',
             'worktree/scripts/workflow_impact.py', 'worktree/references/workflow-contract.md',
             'worktree/scripts/test_workflow_objectives.py']
    missing = [path for path in paths if not (ctx.skills_root / path).is_file()]
    ctx.add('Idempotency results', 'Direct objective contracts', 'FAIL' if missing else 'PASS',
            'Missing: ' + ', '.join(missing) if missing else 'Shared direct intake, immutable basis and continuation contracts are present.')


def check_capability_regressions(ctx: Context) -> None:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHON_COLORS"] = "0"
    suites = suite_checks.suite_catalog(ctx.skills_root)
    capabilities = suite_checks.capability_catalog()
    if any(suite not in suites for _, requirements in capabilities.values() for suite, _ in requirements):
        ctx.add("Suite execution results", "Suite catalog", "FAIL", "Capability references an unknown suite.")
        return
    results = {}
    for index, (name, spec) in enumerate(suites.items(), 1):
        if ctx.progress:
            print(f"Suite {index}/{len(suites)}: {name} (limit {spec.timeout}s)", flush=True)
        result = suite_checks.execute(spec, env)
        results[name] = result
        ctx.suite_results.append(result)
        ctx.add("Suite execution results", name, "PASS" if result.returncode == 0 else "WARN" if result.returncode == 130 else "FAIL", result.detail)
        if ctx.progress:
            print(result.detail, flush=True)
        if ctx.report_path is not None:
            write_report(ctx, ctx.report_path)
        if result.returncode in {125, 130}:
            ctx.interrupted = result.returncode == 130
            break
    for capability_id, (name, requirements) in capabilities.items():
        failed_suites = sorted({suite for suite, _ in requirements
                                if suite in results and results[suite].returncode not in {0, 130}})
        incomplete = sorted({suite for suite, _ in requirements
                             if suite not in results or results[suite].returncode == 130})
        missing = [test for suite, test in requirements if suite in results
                   and test not in results[suite].passed and test not in results[suite].skipped]
        skipped = [test for suite, test in requirements if suite in results and test in results[suite].skipped]
        status = "FAIL" if failed_suites or missing and not incomplete else "WARN" if incomplete or skipped else "PASS"
        if failed_suites:
            detail = "Blocked by failed required suite(s): " + ", ".join(failed_suites) + ". See suite execution results."
        elif incomplete:
            detail = "Incomplete after interruption; required suites: " + ", ".join(incomplete)
        elif missing:
            detail = "Required regression tests were not executed: " + ", ".join(missing)
        elif skipped:
            detail = "Required platform-specific regression tests were skipped: " + ", ".join(skipped)
        else:
            detail = f"Executed {len(requirements)} required regression test(s)."
        ctx.add("Capability regression results", name, status, detail, capability_id=capability_id)


def run_hook(
    script: Path,
    payload: dict[str, Any],
    ctx: Context,
    *,
    host_home: Path | None = None,
) -> dict[str, Any]:
    env = runtime_environment(ctx.agent, home=host_home or ctx.fixture_host_home, fresh_session=True)
    try:
        env = owned_git_origin.environment(ctx.selected_project, env=env)
    except owned_git_origin.OriginError as error:
        return {"_error": str(error)}
    command = ["python3", str(script)]
    if ctx.agent == "claude":
        env["CLAUDE_PLUGIN_DATA"] = str(ctx.verification_root / "fixture-plugin-data")
        hook = "stop_lifecycle_arbiter.py" if script.name == "stop_sdlc_continue.py" else script.name
        command = ["python3", str(ctx.skills_root / "global-context-management/scripts/hook_runtime.py"),
                   "--agent", ctx.agent, "--hook", hook, "--plugin-root", str(ctx.skills_root)]
    result = run(command, input_text=json.dumps(payload), env=env, timeout=40)
    if result.returncode != 0:
        return {"_error": result.stderr.strip() or result.stdout.strip()}
    if not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"_error": result.stdout.strip()}


def pre_payload(ctx: Context, tool_name: str, command: str) -> dict[str, Any]:
    if ctx.agent == "claude" and tool_name == "apply_patch":
        matched = re.search(r"\*\*\* Update File: (.+)\n@@\n-(.+)\n\+(.+)\n", command)
        if matched is None:
            raise ValueError("unsupported native edit fixture")
        return {"hook_event_name": "PreToolUse", "cwd": str(ctx.selected_project),
                "prompt_id": "550e8400-e29b-41d4-a716-446655440000", "session_id": "verification-session",
                "tool_name": "Edit", "tool_use_id": "verification-tool",
                "tool_input": {"file_path": matched[1], "old_string": matched[2], "new_string": matched[3]}}
    return {
        "hook_event_name": "PreToolUse",
        "cwd": str(ctx.selected_project),
        ("turn_id" if ctx.agent == "codex" else "prompt_id"): "550e8400-e29b-41d4-a716-446655440000",
        "session_id": "verification-session",
        "tool_name": tool_name,
        "tool_use_id": "verification-tool",
        "tool_input": {"command": command},
    }


def stop_payload(ctx: Context, active: bool = False) -> dict[str, Any]:
    return {
        "hook_event_name": "Stop",
        "cwd": str(ctx.selected_project),
        ("turn_id" if ctx.agent == "codex" else "prompt_id"): "550e8400-e29b-41d4-a716-446655440000",
        "session_id": "verification-session",
        "stop_hook_active": active,
        "last_assistant_message": "verification",
    }


def denied(result: dict[str, Any]) -> str | None:
    output = result.get("hookSpecificOutput", {})
    if output.get("permissionDecision") == "deny":
        return str(output.get("permissionDecisionReason") or "")
    return None


def check_hooks_with_fixtures(ctx: Context) -> None:
    try:
        _check_hooks_with_fixtures(ctx)
    except FixtureSetupError as error:
        for section in ("PreToolUse safety test results", "Stop continuation test results"):
            ctx.add(section, "Hook fixture setup", "FAIL", str(error))


def _check_hooks_with_fixtures(ctx: Context) -> None:
    # Hook fixtures use a separate native home and explicit synthetic binding;
    # they must never resolve an operator's live run.
    ctx = replace(
        ctx,
        fixture_host_home=ctx.verification_root / "hook-fixture-agent-home",
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
            "current_execution": "FEAT-001",
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
                **json.loads((run_dir / 'run.json').read_text()),
                "status": run_status or status,
                "blocked_reason": blocked_reason,
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
        ctx.fixture_host_home
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

    empty_host_home = ctx.verification_root / "empty-fixture-agent-home"
    empty_host_home.mkdir(parents=True, exist_ok=True)
    no_active = run_hook(stop_hook, stop_payload(ctx), ctx, host_home=empty_host_home)
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
    bound_command = "Use $sdlc-start continue"
    ctx.add(
        "Stop continuation test results",
        "Continue through explicitly bound $sdlc-start",
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
    no_route = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add(
        "Stop continuation test results",
        "Committed features do not invent a UAT route",
        "PASS" if no_route == {"continue": True} else "FAIL",
        json.dumps(no_route, sort_keys=True),
    )

    reset_stop_state(
        next_skill="sdlc-uat-tests",
        feature_queue={"features": [
            {"id": "FEAT-001", "status": "committed"},
            {"id": "FEAT-002", "status": "planned"},
        ]},
    )
    uat = run_hook(stop_hook, stop_payload(ctx), ctx)
    uat_prompt = str(uat.get("reason") or "")
    ctx.add(
        "Stop continuation test results",
        "Continue to coordinator-selected slice UAT",
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

    reset_stop_state(next_skill="sdlc-validate-codes")
    binding = ctx.fixture_host_home / "sdlc-runs" / "bindings" / (
        hashlib.sha256(b"verification-session").hexdigest() + ".json"
    )
    binding.unlink()
    unbound = run_hook(stop_hook, stop_payload(ctx), ctx)
    ctx.add(
        "Stop continuation test results",
        "Unbound active run does not infer ownership",
        "PASS" if unbound == {"continue": True} else "FAIL",
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
            source_identity=result_value["source_identity"],
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
                    "Merge safety evidence lacks protected qualification, admission and recovery regressions."
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
            "resume the completed objective without duplicate state",
        ),
        "change-request": (
            "Change-request handling",
            "apply scoped steering to the accepted objective",
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
    if ctx.in_progress or ctx.interrupted or any(check.status == "WARN" for check in ctx.checks):
        return "PARTIAL"
    return "PASS"


def summarize_matrix(ctx: Context) -> list[tuple[str, str]]:
    capability_rows = [
        ("Source phase-skill contract", "source.phase-contract"),
        ("Source catalog structure", "source.catalog-structure"),
        ("Canonical spec ownership", "spec.ownership-contract"),
        (
            "Canonical project-spec validation receipt",
            "spec.validation-receipt",
        ),
        ("Installed worktree dependency", "runtime.worktree-dependency"),
        ("Source-installed parity", "runtime.skill-parity"),
        ("Document obligation coverage", "source.obligations"),
        ("Phase and slice planning", "program.phase-slices"),
        ("Portable source admission", "program.source-admission"),
        ("Current completion evidence", "program.completion-freshness"),
        ("Bounded task scaling", "execution.bounded-scale"),
        ("Public direct entry points", "public.interface"),
        ("Direct objective intake", "objective.intake"),
        ("Objective revisions", "objective.revisions"),
        ("Explicit session binding", "objective.binding"),
        ("Objective lifecycle", "objective.lifecycle"),
        ("Execution scope", "execution.scope"),
        ("Session recovery", "execution.sessions-recovery"),
        ("Future-wave replan", "execution.replan"),
        ("Secret persistence gate", "execution.secret-gate"),
        ("Sequential fallback", "execution.sequential-fallback"),
        ("Managed outer lease", "interop.outer-lease-v4"),
        ("Implement interoperability", "interop.shared-execution"),
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
        "- Coverage boundary: document packages, phased delivery, large task graphs and protected merge recovery have deterministic regression evidence. Current live profiles do not qualify native multi-slice document delivery or a real remote merge.",
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
        "Suite execution results",
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
            "- `python3 align-skill/scripts/validate-skill-structure.py .`",
            "- `python3 -m unittest -v worktree/scripts/test_workflow_objectives.py sdlc-start/scripts/test_sdlc_start_contract.py sdlc-start/scripts/test_validate_project_specs.py`",
            "- `python3 -m unittest discover -v -s worktree/scripts -p 'test_*.py'`",
            "- `python3 worktree/scripts/test_workflow_dispatch.py -v`",
            "- `python3 worktree/scripts/test-worktree-manager.py -v`",
            "- `python3 worktree/scripts/test-git-promotion.py -v`",
            "- `python3 implement/scripts/test-worktree-interoperability.py -v`",
            "- `python3 implement/scripts/test-task-waves.py -v`",
            "- `python3 sdlc-start/assets/hooks/tests/test_sdlc_hooks.py -v`",
            "- `python3 sdlc-evaluate/scripts/test_observability_contract.py -v`",
            "- `python3 -m unittest -v sdlc-workflow-test/scripts/test_verify_agentic_sdlc.py sdlc-workflow-test/scripts/test_collect_live_evidence.py`",
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


def archive_report(path: Path) -> None:
    """Preserve the prior report once before replacing the current run report."""
    if not path.exists():
        return
    content = workspace._read(path, private=True)
    archive = path.parent / (path.name + ".history")
    archive.mkdir(mode=0o700, exist_ok=True)
    workspace._canonical(archive)
    workspace._identity(archive)
    fd, name = tempfile.mkstemp(prefix="report-", suffix=".md", dir=archive)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


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
    try:
        workspace._report_allowed(ctx.verification_root, report_path)
    except workspace.WorkspaceError as error:
        print(str(error), file=sys.stderr)
        return 2
    root_problem = prepare_verification_root(ctx, requested_root)
    if root_problem:
        print(root_problem, file=sys.stderr)
        return 2
    try:
        with workspace.operation(ctx.verification_root):
            archive_report(report_path)
            ctx.in_progress = True
            ctx.report_path = report_path
            write_report(ctx, report_path)
            check_design(ctx)
            check_source_phase_contract(ctx)
            check_source_catalog_validation(ctx)
            check_spec_ownership_contract(ctx)
            check_vertical_slice_contract(ctx)
            check_execution_plane_contract(ctx)
            check_repair_loop_contract(ctx)
            check_skill_discovery(ctx)
            check_hook_config(ctx)
            ctx.report_path = report_path
            fixture_admitted = setup_disposable_project(ctx)
            check_direct_objectives(ctx)
            ctx.report_path = report_path
            ctx.progress = True
            check_capability_regressions(ctx)
            execution_stopped = ctx.interrupted or any(result.returncode == 125 for result in ctx.suite_results)
            if fixture_admitted and not execution_stopped:
                check_hooks_with_fixtures(ctx)
            elif not fixture_admitted:
                for section in ("PreToolUse safety test results", "Stop continuation test results"):
                    ctx.add(
                        section, "Hook fixture prerequisite", "FAIL",
                        "Disposable fixture admission failed; hook fixtures were not created or executed.",
                    )
            else:
                ctx.add("PreToolUse safety test results", "Hook fixture execution", "WARN",
                        "Suite execution stopped before hook fixtures; no hook result claimed.")
            add_agent_required_sections(ctx)
            ctx.in_progress = False
            write_report(ctx, report_path)
            print(f"Report path: {report_path}")
            print(f"Final readiness status: {final_status(ctx)}")
            failures = [check for check in ctx.checks if check.status == "FAIL"]
            warnings = [check for check in ctx.checks if check.status == "WARN"]
            print(f"Failures: {len(failures)}")
            print(f"Warnings: {len(warnings)}")
            return 130 if ctx.interrupted else 1 if failures else 0

    except KeyboardInterrupt:
        ctx.interrupted = True
        ctx.add("Suite execution results", "Verification interrupted", "WARN",
                "Run interrupted; completed observations are retained and readiness is partial.")
        with workspace.operation(ctx.verification_root):
            write_report(ctx, report_path)
        print(f"Interrupted; partial report: {report_path}", file=sys.stderr)
        return 130
    except (workspace.WorkspaceError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
