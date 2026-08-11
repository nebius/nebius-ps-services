#!/usr/bin/env python3
"""Disposable tests for the remediation-attempt Codex hook."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


HOOK = Path(__file__).resolve().parents[1] / "remediation_attempt_guard.py"
SPEC = importlib.util.spec_from_file_location("remediation_attempt_guard", HOOK)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)

ARBITER = HOOK.with_name("stop_lifecycle_arbiter.py")
ARBITER_SPEC = importlib.util.spec_from_file_location("stop_lifecycle_arbiter", ARBITER)
assert ARBITER_SPEC and ARBITER_SPEC.loader
arbiter = importlib.util.module_from_spec(ARBITER_SPEC)
sys.modules[ARBITER_SPEC.name] = arbiter
ARBITER_SPEC.loader.exec_module(arbiter)

BLOCKER_KEY = "component|operation|error-class|boundary"
EXPECTED_LOG_LAYERS = (
    "Component",
    "Application or job",
    "Container or orchestrator",
    "Service manager",
    "OS and kernel",
    "Network and firewall",
    "Storage",
    "GPU or hardware",
)


def expected_log_rows(status: str = "examined") -> list[str]:
    return [
        f"| {layer} | bounded local evidence | fixture incident window | "
        f"scoped layer state was reviewed | {status} |"
        for layer in EXPECTED_LOG_LAYERS
    ]


def attempt(
    number: int,
    *,
    blocker_key: str = BLOCKER_KEY,
    distinct_key: str | None = None,
    hypothesis: str | None = None,
    new_evidence: str | None = None,
    result: str = "failed_same_blocker",
) -> dict[str, str]:
    return {
        "blocker_key": blocker_key,
        "distinct_key": distinct_key or f"hypothesis-{number}|variable-{number}|target",
        "hypothesis": hypothesis or f"hypothesis {number}",
        "new_evidence": new_evidence or f"new evidence {number}",
        "remediation": f"remediation {number}",
        "verification": f"verification {number}",
        "result": result,
    }


def default_failed_attempts() -> list[dict[str, str]]:
    return [attempt(number) for number in range(1, guard.DEFAULT_ATTEMPT_LIMIT + 1)]


def state_data(**updates: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema": guard.SCHEMA,
        "blocker_key": BLOCKER_KEY,
        "blocker_summary": "The same bounded operation still fails.",
        "tranche": 1,
        "started_at": "2026-01-01T00:30:00Z",
        "active_seconds": 0,
        "attempt_limit": guard.DEFAULT_ATTEMPT_LIMIT,
        "time_limit_minutes": guard.DEFAULT_TIME_LIMIT_MINUTES,
        "budget_authorization_id": None,
        "attempts": [],
        "status": "active",
        "stop_trigger": None,
        "override_summary": None,
    }
    data.update(updates)
    return data


def legacy_state_data(**updates: object) -> dict[str, object]:
    data = state_data(
        schema=guard.LEGACY_SCHEMA,
        attempt_limit=guard.HISTORICAL_MAX_ATTEMPT_LIMIT,
        time_limit_minutes=guard.HISTORICAL_DEFAULT_TIME_LIMIT_MINUTES,
    )
    data.update(updates)
    return data


def completion_gate_rows(verdict: str) -> list[str]:
    gap = (
        guard.PASS_NO_GAP
        if verdict == "PASS"
        else "Acquire criterion-specific evidence before closure."
    )
    return [
        "| Criterion | Verdict | Evidence | Gap or next action |",
        "| --- | --- | --- | --- |",
        *[
            f"| {criterion} | {verdict} | "
            f"{guard.PASS_EVIDENCE_BY_CRITERION[criterion] if verdict == 'PASS' else 'Focused criterion evidence was recorded.'} | "
            f"{gap} |"
            for criterion in guard.COMPLETION_CRITERIA
        ],
    ]


def complete_report(
    *,
    stop_trigger: str = "attempt_limit",
    attempt_count: int = guard.DEFAULT_ATTEMPT_LIMIT,
    legacy_evidence: bool = False,
) -> str:
    attempt_lines = [
        (
            f"- attempt-{number} | Remediation: remediation {number} | "
            f"Verification: verification {number} | Result: failed_same_blocker"
        )
        for number in range(1, attempt_count + 1)
    ] or ["No remediation attempts were counted before the active-time limit."]
    if legacy_evidence:
        evidence_lines = [
            *[
                f"- attempt-{number} | Evidence: "
                "Historical evidence summary unavailable."
                for number in range(1, attempt_count + 1)
            ],
            guard.LEGACY_EVIDENCE_NOTE,
        ]
    else:
        evidence_lines = [
            f"- attempt-{number} | Evidence: new evidence {number}"
            for number in range(1, attempt_count + 1)
        ] or ["The active-time ledger reached the configured limit."]
    return "\n".join(
        [
            "# Troubleshooting Report",
            "## Outcome",
            "- Classification: UNRESOLVED",
            "- Current workflow state: REPORTED",
            f"- {guard.REPORT_MARKER}",
            f"- Stop trigger: {stop_trigger}",
            "- Confidence: the blocker remains unresolved after bounded attempts.",
            "- Current impact: no further remediation is authorized in this tranche.",
            "- Stabilization status: no additional state change is authorized.",
            "## Failure Contract",
            "- Expected: the bounded operation completes without the recorded blocker.",
            "- Actual: the same bounded operation still fails after each attempt.",
            "- Scope and signature: the stable blocker and its private attempt ledger.",
            "- Reproduction or characterization: the recorded verification still fails.",
            "- Success criteria and constraints: remove the blocker without exceeding authority.",
            "- Target, environment, blast radius, and allowed mutations: the bounded target remains frozen.",
            "- Included system boundary: the stable blocker and recorded remediation ledger.",
            "- Excluded system boundary: unrecorded components and unexercised paths.",
            "- Exercised control and data paths: marker attempts and verifications only.",
            "- Incident-window start: the bounded tranche start from the marker.",
            "- Incident-window end: the marker exhaustion event for this report.",
            "## Architecture Verdict",
            "- Observed technologies, versions, and deployment model: evidence is incomplete at exhaustion.",
            "- Configuration authorities: the decisive configuration authority is not proven.",
            "- Components, dependencies, ports, protocols, and authentication: coverage is incomplete.",
            "- Control and data flows: the decisive failing flow remains unresolved.",
            "- Official vendor architecture comparison and verdict: comparison remains incomplete.",
            "## Component Verification Matrix",
            "| Component | Version and existence | Active configuration | Runtime health | Dependencies, authentication, and DNS | Resources and time sync | Restart history and recent changes | Evidence |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            "| unresolved component | version evidence unavailable | active configuration unproven | runtime health unproven | dependency, authentication, and DNS evidence incomplete | resource and clock evidence incomplete | restart and change evidence incomplete | bounded marker evidence only |",
            "## Incident Timeline",
            "| Time | Source and clock basis | Correlation identifier | Event | Evidence or inference |",
            "| --- | --- | --- | --- | --- |",
            "| bounded incident window | clock basis unavailable | blocker key correlation | attempts remained unsuccessful | marker-derived attempt order only |",
            "## Logs Examined",
            "| Layer | Source | Window and filters | Finding | Coverage status |",
            "| --- | --- | --- | --- | --- |",
            *expected_log_rows("unavailable"),
            "## Hypotheses And Experiments",
            "| Hypothesis | Prediction and falsifier | Bounded experiment | Observation | Decision |",
            "| --- | --- | --- | --- | --- |",
            "| recorded attempt hypotheses | marker prediction summaries only | bounded remediation verification | blocker remained present | unresolved at exhaustion |",
            "## Code Debugging",
            "- Reproduction and execution or data path: relevant path evidence is incomplete.",
            "- Stack trace, core dump, or equivalent runtime evidence: decisive artifact is unavailable.",
            "- Configuration, environment, and data inputs: input coverage remains incomplete.",
            "- Recent changes and affected or unaffected comparison: comparison is unavailable.",
            "- Focused tests, static or dynamic analysis, and instrumentation: coverage is incomplete.",
            "- Instrumentation cleanup and limitations: no cleanup state is inferred by this report.",
            "## Root Cause",
            "- Blocker: The same bounded operation still fails.",
            "- Blocker key: component|operation|error-class|boundary",
            "- Earliest divergence: decisive divergence remains unproven.",
            "- Causal chain: the marker proves persistence, not a complete causal chain.",
            "- Counterfactual and reintroduction: decisive counterfactual evidence is unavailable.",
            "- Alternatives eliminated: competing alternatives remain unresolved.",
            "- Confidence: unresolved after bounded remediation attempts.",
            "## Remediation",
            *attempt_lines,
            "- Design classification and handoff: no additional handoff is inferred.",
            "- Changes made: only the marker-recorded attempts are represented.",
            "- Authority and safety basis: another attempt requires a new instruction.",
            "- Rollback or recovery state: rollback state is unavailable to this report.",
            "## Post-Fix Validation",
            *evidence_lines,
            "- Original reproducer: the stable blocker still reproduces.",
            "- Regression oracle: the blocker verification remained unsuccessful.",
            "- Targeted and boundary checks: complete boundary evidence is unavailable.",
            "- Repeated or dynamic diagnostics: only marker-recorded evidence is represented.",
            "- Live trial status and claim scope: no verified live-fix claim is made.",
            "- Candidate, target, checkpoint, and replay range: exact lineage is unavailable.",
            "- Intervention ledger and first contaminated boundary: intervention state is unknown.",
            "- Product-owned transitions and independent postconditions: not proven at exhaustion.",
            "## Completion Gate",
            *completion_gate_rows("UNKNOWN"),
            "## Remaining Unknowns And Residual Risks",
            "- Unknowns and coverage gaps: decisive evidence is still missing for required criteria.",
            "- Residual risks: the stable blocker remains unresolved.",
            "- Exact next action: user review before a fresh bounded tranche.",
        ]
    )


def complete_general_report(
    classification: str = "VERIFIED_FIXED",
    *,
    current_impact: str = "The reported local failure no longer reproduces.",
    completion_verdict: str | None = None,
) -> str:
    verdict = completion_verdict or (
        "PASS" if classification == "VERIFIED_FIXED" else "UNKNOWN"
    )
    return "\n".join(
        [
            "# Troubleshooting Report",
            "## Outcome",
            f"- Classification: {classification}",
            "- Current workflow state: REPORTED",
            "- Confidence: high confidence from focused local verification.",
            f"- Current impact: {current_impact}",
            "- Stabilization status: no separate stabilization was required.",
            "## Failure Contract",
            "- Expected: the bounded operation completes without the reported error.",
            "- Actual: the original invocation failed at the recorded boundary.",
            "- Scope and signature: one local workflow and its stable error signature.",
            "- Reproduction or characterization: the focused oracle reproduced the denial.",
            "- Success criteria and constraints: restore the owner contract without policy drift.",
            "- Target, environment, blast radius, and allowed mutations: local fixture with bounded edits.",
            "- Included system boundary: local report hook and its arbiter dependency.",
            "- Excluded system boundary: installed runtime and external targets.",
            "- Exercised control and data paths: prompt through report validation.",
            "- Incident-window start: 2026-01-01T00:00:00Z.",
            "- Incident-window end: 2026-01-01T00:05:00Z.",
            "## Architecture Verdict",
            "- Observed technologies, versions, and deployment model: PASS: local Python hook fixture.",
            "- Configuration authorities: PASS: the canonical hook source owns the evaluated policy.",
            "- Components, dependencies, ports, protocols, and authentication: PASS: local dependencies verified.",
            "- Control and data flows: PASS: prompt, guard, arbiter, and report flow were traced.",
            "- Official vendor architecture comparison and verdict: PASS: observed flow matches the supported contract.",
            "## Component Verification Matrix",
            "| Component | Version and existence | Active configuration | Runtime health | Dependencies, authentication, and DNS | Resources and time sync | Restart history and recent changes | Evidence |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            "| report guard | PASS: source revision verified | PASS: canonical settings active | PASS: focused path passed | PASS: local arbiter dependency, authentication, and DNS passed | PASS: no pressure and synchronized fixture | PASS: restart and change history reviewed | PASS: focused unit evidence recorded |",
            "## Incident Timeline",
            "| Time | Source and clock basis | Correlation identifier | Event | Evidence or inference |",
            "| --- | --- | --- | --- | --- |",
            "| fixture incident window | synchronized local test clock | report-turn identifier | first denial localized | direct unit evidence recorded |",
            "## Logs Examined",
            "| Layer | Source | Window and filters | Finding | Coverage status |",
            "| --- | --- | --- | --- | --- |",
            *expected_log_rows(),
            "## Hypotheses And Experiments",
            "| Hypothesis | Prediction and falsifier | Bounded experiment | Observation | Decision |",
            "| --- | --- | --- | --- | --- |",
            "| owner policy mismatch | predicts denial until owner repair | focused before and after oracle | denial cleared after repair | supported and repaired |",
            "## Code Debugging",
            "- Reproduction and execution or data path: PASS: the exact guard path reproduced and was traced.",
            "- Stack trace, core dump, or equivalent runtime evidence: PASS: deterministic return evidence replaced crash data.",
            "- Configuration, environment, and data inputs: PASS: bounded fixture inputs and settings were compared.",
            "- Recent changes and affected or unaffected comparison: PASS: changed and protected cases were compared.",
            "- Focused tests, static or dynamic analysis, and instrumentation: PASS: unit and static checks passed.",
            "- Instrumentation cleanup and limitations: PASS: no temporary instrumentation remains.",
            "## Root Cause",
            "- Earliest divergence: the owner applied a broader policy than its contract.",
            "- Causal chain: broad classification denied the otherwise authorized operation.",
            "- Counterfactual and reintroduction: focused faulty and repaired states separated the cause.",
            "- Alternatives eliminated: startup and unrelated policy failures were eliminated.",
            "- Confidence: high confidence from the deterministic regression oracle.",
            "## Remediation",
            "- Design classification and handoff: localized invariant restoration required no design handoff.",
            "- Changes made: repaired the canonical owner and preserved unrelated policy.",
            "- Authority and safety basis: the user authorized the bounded local repair.",
            "- Rollback or recovery state: no residual runtime or repository recovery is required.",
            "## Post-Fix Validation",
            "- Original reproducer: PASS: the formerly denied operation passed the focused oracle.",
            "- Regression oracle: PASS: protected owner state remained denied in the companion case.",
            "- Targeted and boundary checks: PASS: guard and arbiter boundaries passed focused checks.",
            "- Repeated or dynamic diagnostics: PASS: deterministic repetitions produced the expected result.",
            "- Live trial status and claim scope: PASS: source-only local fixture validation completed.",
            "- Candidate, target, checkpoint, and replay range: PASS: exact local revision and fixture were used.",
            "- Intervention ledger and first contaminated boundary: PASS: the fixture had a clean evidence lineage.",
            "- Product-owned transitions and independent postconditions: PASS: independent unit assertions passed.",
            "## Completion Gate",
            *completion_gate_rows(verdict),
            "## Remaining Unknowns And Residual Risks",
            "- Unknowns and coverage gaps: no remaining gap within the scoped local fixture.",
            "- Residual risks: fresh installed-runtime activation remains a separate rollout claim.",
            "- Exact next action: use the existing regression oracle for future changes.",
        ]
    )


class RemediationAttemptGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "workspace"
        self.root.mkdir()
        self.home = Path(self.tmp.name) / "codex"
        self.home.mkdir()
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.home)})
        self.env.start()
        self.payload: dict[str, object] = {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(self.root),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "run-check"},
        }

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def write_state(self, data: object, *, raw: str | None = None) -> Path:
        state_file = guard.state_file_for_payload(self.payload)
        assert state_file is not None
        state_file.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(data, sort_keys=True) if raw is None else raw
        state_file.write_text(
            f"# Current state\n\n{guard.MARKER_START}{body}{guard.MARKER_END}\n",
            encoding="utf-8",
        )
        return state_file

    def evaluate_pre_tool(self) -> dict[str, object]:
        self.payload["hook_event_name"] = "PreToolUse"
        return guard.evaluate(self.payload)

    def evaluate_prompt(
        self, prompt: str, *, turn_id: str = "turn-prompt"
    ) -> dict[str, object]:
        self.payload.update(
            {
                "hook_event_name": "UserPromptSubmit",
                "turn_id": turn_id,
                "prompt": prompt,
            }
        )
        return guard.evaluate(self.payload)

    def authorization_data(self) -> dict[str, object]:
        authorization = guard.load_authorization_state(self.payload)
        self.assertEqual(authorization.kind, "valid")
        assert authorization.data is not None
        return authorization.data

    def report_obligation_data(self) -> dict[str, object]:
        obligation = guard.load_report_obligation_state(self.payload)
        self.assertEqual(obligation.kind, "valid")
        assert obligation.data is not None
        return obligation.data

    def evaluate_stop_with_arbiter(self) -> dict[str, object]:
        return arbiter.evaluate(self.payload, HOOK.parent)

    def test_explicit_invocation_without_marker_requires_and_accepts_report(
        self,
    ) -> None:
        output = self.evaluate_prompt(
            "$troubleshoot diagnose the failure", turn_id="report-turn"
        )
        self.assertIn(
            "structured terminal report",
            output["hookSpecificOutput"]["additionalContext"],
        )
        obligation = self.report_obligation_data()
        self.assertEqual(obligation["status"], "active")
        self.assertEqual(obligation["corrections"], 0)
        obligation_file = guard.report_obligation_file_for_payload(self.payload)
        assert obligation_file is not None
        self.assertEqual(obligation_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(obligation_file.parent.stat().st_mode & 0o777, 0o700)

        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "The operation is fixed.",
                "stop_hook_active": False,
            }
        )
        incomplete = self.evaluate_stop_with_arbiter()
        self.assertEqual(incomplete["decision"], "block")
        self.assertIn("structured troubleshooting report", incomplete["reason"])
        self.assertEqual(self.report_obligation_data()["corrections"], 1)

        self.payload["last_assistant_message"] = complete_general_report()
        complete = self.evaluate_stop_with_arbiter()
        self.assertTrue(complete["continue"])
        self.assertEqual(self.report_obligation_data()["status"], "delivered")

    def test_general_report_accepts_every_terminal_classification(self) -> None:
        for index, classification in enumerate(guard.GENERAL_REPORT_OUTCOMES):
            with self.subTest(classification=classification):
                turn_id = f"classification-{index}"
                self.evaluate_prompt("$troubleshoot", turn_id=turn_id)
                self.payload.update(
                    {
                        "hook_event_name": "Stop",
                        "turn_id": turn_id,
                        "last_assistant_message": complete_general_report(
                            classification,
                            current_impact=(
                                "The operation remains blocked after a tool error."
                                if classification == "UNRESOLVED"
                                else "The bounded investigation reached its terminal state."
                            ),
                        ),
                        "stop_hook_active": False,
                    }
                )
                output = self.evaluate_stop_with_arbiter()
                self.assertTrue(output["continue"])
                self.assertEqual(self.report_obligation_data()["status"], "delivered")

    def test_verified_fixed_requires_all_completion_criteria_to_pass(self) -> None:
        valid = complete_general_report()
        logs_row = next(
            line for line in valid.splitlines() if line.startswith("| Logs |")
        )
        report = valid.replace(
            logs_row,
            "| Logs | UNKNOWN | Incident logs remain unavailable. | "
            "Acquire the incident log window before closure. |",
        )
        complete, issue = guard._general_report_complete(report)
        self.assertFalse(complete)
        self.assertIn("VERIFIED_FIXED requires PASS for: Logs", issue)

    def test_pass_rows_reject_claimed_evidence_gaps(self) -> None:
        valid = complete_general_report()
        logs_row = next(
            line for line in valid.splitlines() if line.startswith("| Logs |")
        )
        cases = (
            "Verified: log evidence is unavailable for this incident.",
            "Verified: the relevant logs remain unproven.",
            "Verified: the required log check was not run.",
            "Verified: the required diagnostic remains unrun.",
            "Verified: no log evidence was available for this incident.",
            "Verified: logs were never collected for this incident.",
        )
        for evidence in cases:
            with self.subTest(evidence=evidence):
                report = valid.replace(
                    logs_row,
                    f"| Logs | PASS | {evidence} | {guard.PASS_NO_GAP} |",
                )
                complete, issue = guard._general_report_complete(report)
                self.assertFalse(complete)
                self.assertIn("must be `Verified: Logs Examined.`", issue)

    def test_pass_rows_require_structured_evidence_and_no_gap_sentinel(self) -> None:
        valid = complete_general_report()
        logs_row = next(
            line for line in valid.splitlines() if line.startswith("| Logs |")
        )
        cases = (
            valid.replace(
                logs_row,
                f"| Logs | PASS | Logs were examined for the incident. | "
                f"{guard.PASS_NO_GAP} |",
            ),
            valid.replace(
                guard.PASS_NO_GAP,
                "Acquire more evidence before closure.",
                1,
            ),
        )
        for report in cases:
            with self.subTest(report=report):
                self.assertFalse(guard._general_report_complete(report)[0])

    def test_pass_rows_cross_validate_referenced_report_evidence(self) -> None:
        valid = complete_general_report()
        valid_negative_finding = valid.replace(
            "scoped layer state was reviewed",
            "no evidence of the original failure remained in the incident window",
        )
        self.assertEqual(
            guard._general_report_complete(valid_negative_finding),
            (True, ""),
        )
        cases = {
            "design": valid.replace(
                "- Official vendor architecture comparison and verdict: PASS:",
                "- Official vendor architecture comparison and verdict: UNKNOWN:",
            ),
            "infrastructure": valid.replace(
                "PASS: source revision verified",
                "UNKNOWN: source revision unavailable",
            ),
            "connectivity": valid.replace(
                "PASS: local arbiter dependency, authentication, and DNS passed",
                "UNKNOWN: dependency, authentication, and DNS evidence unavailable",
            ),
            "configuration": valid.replace(
                "PASS: canonical settings active",
                "UNKNOWN: active settings unavailable",
            ),
            "runtime": valid.replace(
                "PASS: focused path passed",
                "UNKNOWN: runtime path unverified",
            ),
            "logs": valid.replace("| examined |", "| unavailable |"),
            "code": valid.replace(
                "- Reproduction and execution or data path: PASS:",
                "- Reproduction and execution or data path: UNKNOWN:",
            ),
            "architecture-contradiction": valid.replace(
                "PASS: observed flow matches the supported contract.",
                "PASS: official architecture was not checked.",
            ),
            "component-contradiction": valid.replace(
                "PASS: source revision verified",
                "PASS: version evidence unavailable",
            ),
            "component-evidence-contradiction": valid.replace(
                "PASS: focused unit evidence recorded",
                "PASS: component evidence is missing",
            ),
            "log-source-contradiction": valid.replace(
                "bounded local evidence",
                "log source unavailable",
                1,
            ),
            "log-finding-contradiction": valid.replace(
                "scoped layer state was reviewed",
                "log evidence was never collected",
                1,
            ),
            "code-contradiction": valid.replace(
                "PASS: deterministic return evidence replaced crash data.",
                "PASS: runtime evidence was not checked.",
            ),
            "post-fix-contradiction": valid.replace(
                "PASS: source-only local fixture validation completed.",
                "PASS: live trial evidence unavailable.",
            ),
        }
        for name, report in cases.items():
            with self.subTest(name=name):
                complete, issue = guard._general_report_complete(report)
                self.assertFalse(complete)
                self.assertTrue(issue)

    def test_general_report_accepts_public_vendor_and_artifact_identities(self) -> None:
        report = complete_general_report().replace(
            "- Official vendor architecture comparison and verdict: PASS: observed flow matches the supported contract.",
            "\n".join(
                [
                    "- Official vendor architecture comparison and verdict: PASS: observed flow matches the supported contract.",
                    "- Vendor docs: https://kubernetes.io/docs/concepts/architecture/",
                    "- Public host: kubernetes.io",
                    "- Candidate commit: 0123456789abcdef0123456789abcdef01234567",
                    "- Image digest: sha256:"
                    + "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                ]
            ),
        )
        self.assertEqual(guard._general_report_complete(report), (True, ""))

    def test_general_report_rejects_single_label_host_identity(self) -> None:
        report = complete_general_report().replace(
            "- Official vendor architecture comparison and verdict: PASS: observed flow matches the supported contract.",
            "\n".join(
                [
                    "- Official vendor architecture comparison and verdict: PASS: observed flow matches the supported contract.",
                    "- Target host: worker-one",
                ]
            ),
        )
        complete, issue = guard._general_report_complete(report)
        self.assertFalse(complete)
        self.assertIn("sensitive value", issue)

    def test_general_report_requires_structured_tables_and_labeled_fields(
        self,
    ) -> None:
        valid = complete_general_report()
        component_row = (
            "| report guard | PASS: source revision verified | PASS: canonical "
            "settings active | PASS: focused path passed | PASS: local arbiter dependency, "
            "authentication, and DNS passed | PASS: no pressure and synchronized fixture | "
            "PASS: restart and change history reviewed | PASS: focused unit evidence recorded |"
        )
        cases = {
            "missing-component-row": valid.replace(component_row, ""),
            "missing-code-debug-field": valid.replace(
                "- Reproduction and execution or data path:",
                "- Execution path summary:",
            ),
            "malformed-log-table": valid.replace(
                "| Layer | Source | Window and filters | Finding | Coverage status |",
                "| Layer | Finding |",
            ),
        }
        for name, report in cases.items():
            with self.subTest(name=name):
                complete, issue = guard._general_report_complete(report)
                self.assertFalse(complete)
                self.assertTrue(issue)

    def test_logs_examined_requires_each_canonical_layer_exactly_once(self) -> None:
        valid = complete_general_report("UNRESOLVED", completion_verdict="UNKNOWN")
        rows = expected_log_rows()
        self.assertEqual(guard._general_report_complete(valid), (True, ""))
        for layer, row in zip(EXPECTED_LOG_LAYERS, rows, strict=True):
            cases = {
                "missing": valid.replace(row, "", 1),
                "duplicate": valid.replace(row, f"{row}\n{row}", 1),
                "case-alias": valid.replace(
                    row, row.replace(f"| {layer} |", f"| {layer.casefold()} |"), 1
                ),
            }
            for name, report in cases.items():
                with self.subTest(layer=layer, case=name):
                    complete, issue = guard._general_report_complete(report)
                    self.assertFalse(complete)
                    self.assertTrue(issue)
        reordered = valid.replace(f"{rows[0]}\n{rows[1]}", f"{rows[1]}\n{rows[0]}")
        self.assertFalse(guard._general_report_complete(reordered)[0])
        unsupported = valid.replace(
            rows[0], rows[0].replace("| Component |", "| Component runtime |"), 1
        )
        self.assertFalse(guard._general_report_complete(unsupported)[0])

    def test_log_coverage_statuses_are_canonical(self) -> None:
        valid = complete_general_report("UNRESOLVED", completion_verdict="UNKNOWN")
        for status in ("unavailable", "unsafe", "not applicable"):
            with self.subTest(accepted=status):
                report = valid.replace("| examined |", f"| {status} |", 1)
                self.assertEqual(guard._general_report_complete(report), (True, ""))
        verified = complete_general_report()
        for status in ("unavailable", "unsafe"):
            with self.subTest(verified_fixed_rejected=status):
                report = verified.replace("| examined |", f"| {status} |", 1)
                self.assertFalse(guard._general_report_complete(report)[0])
        for status in ("UNKNOWN", "Examined", "not-applicable"):
            with self.subTest(rejected=status):
                report = valid.replace("| examined |", f"| {status} |", 1)
                self.assertFalse(guard._general_report_complete(report)[0])

    def test_report_requires_explicit_boundary_and_incident_window_fields(
        self,
    ) -> None:
        required_fields = (
            "- Included system boundary: local report hook and its arbiter dependency.",
            "- Excluded system boundary: installed runtime and external targets.",
            "- Exercised control and data paths: prompt through report validation.",
            "- Incident-window start: 2026-01-01T00:00:00Z.",
            "- Incident-window end: 2026-01-01T00:05:00Z.",
        )
        valid = complete_general_report()
        self.assertEqual(guard._general_report_complete(valid), (True, ""))
        for field in required_fields:
            with self.subTest(field=field):
                prefix = field.split(":", 1)[0] + ":"
                cases = (
                    valid.replace(f"{field}\n", ""),
                    valid.replace(field, f"{field}\n{field}"),
                    valid.replace(field, prefix),
                )
                for report in cases:
                    self.assertFalse(guard._general_report_complete(report)[0])

    def test_component_matrix_requires_dns_and_restart_history_headers(self) -> None:
        former_header = (
            "| Component | Version and existence | Active configuration | Runtime health | "
            "Dependencies and authentication | Resources and time sync | Recent changes | "
            "Evidence |"
        )
        expected_header = (
            "| Component | Version and existence | Active configuration | Runtime health | "
            "Dependencies, authentication, and DNS | Resources and time sync | "
            "Restart history and recent changes | Evidence |"
        )
        report = complete_general_report()
        self.assertEqual(guard._general_report_complete(report), (True, ""))
        self.assertFalse(
            guard._general_report_complete(
                report.replace(expected_header, former_header)
            )[0]
        )

    def test_evidence_tables_reject_placeholder_cells(self) -> None:
        unresolved = complete_general_report("UNRESOLVED", completion_verdict="UNKNOWN")
        cases = {
            "component-evidence": unresolved.replace(
                "PASS: source revision verified", "n", 1
            ),
            "log-source": unresolved.replace("bounded local evidence", "n", 1),
            "log-window": unresolved.replace(
                expected_log_rows()[0],
                expected_log_rows()[0].replace("fixture incident window", "n"),
                1,
            ),
            "log-finding": unresolved.replace(
                "scoped layer state was reviewed", "n", 1
            ),
            "pass-detail": complete_general_report().replace(
                "PASS: source revision verified", "PASS: n", 1
            ),
        }
        for name, report in cases.items():
            with self.subTest(name=name):
                self.assertFalse(guard._general_report_complete(report)[0])

    def test_component_identity_rejects_placeholders_but_allows_short_names(
        self,
    ) -> None:
        valid = complete_general_report()
        self.assertEqual(
            guard._general_report_complete(
                valid.replace("| report guard |", "| api |", 1)
            ),
            (True, ""),
        )
        for placeholder in ("n", "none", "placeholder", "-", "?", "..."):
            with self.subTest(placeholder=placeholder):
                report = valid.replace("| report guard |", f"| {placeholder} |", 1)
                self.assertFalse(guard._general_report_complete(report)[0])

    def test_completion_gate_rejects_missing_duplicate_and_invalid_rows(self) -> None:
        valid = complete_general_report("UNRESOLVED")
        logs_row = next(
            line for line in valid.splitlines() if line.startswith("| Logs |")
        )
        cases = {
            "missing": "\n".join(
                line for line in valid.splitlines() if line != logs_row
            ),
            "duplicate": valid.replace(logs_row, f"{logs_row}\n{logs_row}"),
            "invalid": valid.replace("| Logs | UNKNOWN |", "| Logs | MAYBE |"),
            "missing-header": valid.replace(
                "| Criterion | Verdict | Evidence | Gap or next action |\n", ""
            ),
            "duplicate-separator": valid.replace(
                "| Criterion | Verdict | Evidence | Gap or next action |\n"
                "| --- | --- | --- | --- |",
                "| Criterion | Verdict | Evidence | Gap or next action |\n"
                "| --- | --- | --- | --- |\n| --- | --- | --- | --- |",
            ),
            "extra-invalid": valid.replace(
                "## Remaining Unknowns And Residual Risks",
                "| Surprise | MAYBE | Extra evidence here. | Review it next. |\n"
                "## Remaining Unknowns And Residual Risks",
            ),
        }
        for name, report in cases.items():
            with self.subTest(name=name):
                self.assertFalse(guard._general_report_complete(report)[0])

    def test_exhaustion_marker_must_be_inside_outcome(self) -> None:
        report = complete_report().replace(
            f"- {guard.REPORT_MARKER}",
            "- Exhaustion marker is recorded in another section.",
        )
        report = report.replace(
            "## Failure Contract",
            f"## Failure Contract\n{guard.REPORT_MARKER}",
        )
        state = guard.GuardState(
            kind="valid",
            state_file=None,
            data=state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            ),
            exhausted=True,
            stop_trigger="attempt_limit",
        )
        complete, issue = guard._report_complete(report, state)
        self.assertFalse(complete)
        self.assertIn("Outcome requires exactly one", issue)

    def test_general_report_retry_is_bounded_and_emits_structured_fallback(
        self,
    ) -> None:
        self.evaluate_prompt("$troubleshoot", turn_id="fallback-turn")
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "No report was returned.",
                "stop_hook_active": False,
            }
        )
        self.assertEqual(self.evaluate_stop_with_arbiter()["decision"], "block")

        self.payload["stop_hook_active"] = True
        fallback_output = self.evaluate_stop_with_arbiter()
        self.assertFalse(fallback_output["continue"])
        fallback = fallback_output["systemMessage"]
        self.assertIn("# Troubleshooting Report", fallback)
        self.assertIn("- Classification: UNRESOLVED", fallback)
        self.assertEqual(guard._general_report_complete(fallback), (True, ""))
        self.assertEqual(self.report_obligation_data()["status"], "fallback")

    def test_interrupted_obligation_survives_later_turn_until_reported(
        self,
    ) -> None:
        self.evaluate_prompt("$troubleshoot", turn_id="interrupted-turn")
        self.payload.update(
            {
                "hook_event_name": "PreToolUse",
                "turn_id": "resumed-turn",
                "tool_name": "Bash",
                "tool_input": {"command": "run-check"},
            }
        )
        self.assertEqual(guard.evaluate(self.payload), {})

        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": complete_general_report(
                    "UNRESOLVED",
                    current_impact=(
                        "The earlier turn was interrupted before it could report completion."
                    ),
                ),
                "stop_hook_active": False,
            }
        )
        delivered = self.evaluate_stop_with_arbiter()
        self.assertTrue(delivered["continue"])
        self.assertEqual(self.report_obligation_data()["status"], "delivered")

    def test_report_delivery_waits_for_other_stop_policy_continuations(self) -> None:
        self.evaluate_prompt("$troubleshoot", turn_id="composed-stop")
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": complete_general_report(),
                "stop_hook_active": False,
            }
        )
        hook_dir = Path(self.tmp.name) / "hooks"
        hook_dir.mkdir()
        for delegate in arbiter.DELEGATES:
            (hook_dir / delegate).write_text("# test delegate\n", encoding="utf-8")

        def with_project_block(
            path: Path,
            payload: dict[str, object],
            _deadline: float,
        ) -> dict[str, object]:
            if path.name == "remediation_attempt_guard.py":
                return guard.evaluate(payload)
            if path.name == "project_specs_lifecycle.py":
                return {
                    "decision": "block",
                    "reason": "Project reconciliation remains required.",
                }
            return {"continue": True}

        with patch.object(arbiter, "_run_delegate", side_effect=with_project_block):
            blocked = arbiter.evaluate(self.payload, hook_dir)
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("Project reconciliation", blocked["reason"])
        self.assertEqual(self.report_obligation_data()["status"], "active")

        def without_block(
            path: Path,
            payload: dict[str, object],
            _deadline: float,
        ) -> dict[str, object]:
            if path.name == "remediation_attempt_guard.py":
                return guard.evaluate(payload)
            return {"continue": True}

        with patch.object(arbiter, "_run_delegate", side_effect=without_block):
            completed = arbiter.evaluate(self.payload, hook_dir)
        self.assertTrue(completed["continue"])
        self.assertEqual(self.report_obligation_data()["status"], "delivered")

    def test_report_delivery_finalizes_before_terminal_peer_result(self) -> None:
        self.evaluate_prompt("$troubleshoot", turn_id="terminal-peer")
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": complete_general_report(),
                "stop_hook_active": False,
            }
        )
        hook_dir = Path(self.tmp.name) / "terminal-peer-hooks"
        hook_dir.mkdir()
        for delegate in arbiter.DELEGATES:
            (hook_dir / delegate).write_text("# test delegate\n", encoding="utf-8")
        terminal = {
            "continue": False,
            "stopReason": "A peer Stop policy ended the host turn.",
        }

        def with_terminal_peer(
            path: Path,
            payload: dict[str, object],
            _deadline: float,
        ) -> dict[str, object]:
            if path.name == "remediation_attempt_guard.py":
                return guard.evaluate(payload)
            if path.name == "project_specs_lifecycle.py":
                return terminal
            return {"continue": True}

        with patch.object(arbiter, "_run_delegate", side_effect=with_terminal_peer):
            result = arbiter.evaluate(self.payload, hook_dir)
        self.assertEqual(result, terminal)
        self.assertEqual(self.report_obligation_data()["status"], "delivered")

    def test_missing_marker_and_missing_session_fail_open(self) -> None:
        self.assertEqual(self.evaluate_pre_tool(), {})
        self.payload.pop("session_id")
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_prompt_defaults_and_exact_override_are_session_bound(self) -> None:
        output = self.evaluate_prompt("$troubleshoot diagnose the failure")
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("attempt_limit=5", context)
        self.assertIn("time_limit_minutes=120", context)
        self.assertIn("budget_authorization_id=null", context)
        self.assertEqual(guard.load_authorization_state(self.payload).kind, "missing")

        output = self.evaluate_prompt(
            "$troubleshoot --attempt-limit=10 --time-limit-minutes=180 diagnose",
            turn_id="turn-override",
        )
        context = output["hookSpecificOutput"]["additionalContext"]
        authorization = self.authorization_data()
        current = authorization["current"]
        assert isinstance(current, dict)
        self.assertEqual(current["attempt_limit"], 10)
        self.assertEqual(current["time_limit_minutes"], 180)
        self.assertIn(current["authorization_id"], context)
        authorization_file = guard.authorization_file_for_payload(self.payload)
        assert authorization_file is not None
        self.assertEqual(authorization_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(authorization_file.parent.stat().st_mode & 0o777, 0o700)
        raw = authorization_file.read_text(encoding="utf-8")
        self.assertNotIn("diagnose", raw)
        self.assertNotIn("$troubleshoot", raw)

    def test_partial_override_preserves_saved_field_and_explicit_defaults_reset(
        self,
    ) -> None:
        self.evaluate_prompt("$troubleshoot --attempt-limit=10", turn_id="turn-a")
        first = self.authorization_data()["current"]
        assert isinstance(first, dict)
        self.assertEqual(
            (first["attempt_limit"], first["time_limit_minutes"]), (10, 120)
        )

        self.evaluate_prompt("$troubleshoot --time-limit-minutes=180", turn_id="turn-b")
        second = self.authorization_data()["current"]
        assert isinstance(second, dict)
        self.assertEqual(
            (second["attempt_limit"], second["time_limit_minutes"]), (10, 180)
        )
        second_id = second["authorization_id"]

        bare = self.evaluate_prompt("$troubleshoot continue", turn_id="turn-c")
        self.assertIn(
            "attempt_limit=10", bare["hookSpecificOutput"]["additionalContext"]
        )
        self.assertEqual(
            self.authorization_data()["current"]["authorization_id"], second_id
        )

        self.evaluate_prompt(
            "$troubleshoot --attempt-limit=5 --time-limit-minutes=120",
            turn_id="turn-reset",
        )
        reset = self.authorization_data()["current"]
        assert isinstance(reset, dict)
        self.assertEqual(
            (reset["attempt_limit"], reset["time_limit_minutes"]), (5, 120)
        )
        self.assertNotEqual(reset["authorization_id"], second_id)

    def test_prompt_parser_rejects_bad_flags_without_authorizing(self) -> None:
        invalid_prompts = (
            "$troubleshoot --attempt-limit=0",
            "$troubleshoot --attempt-limit=11",
            "$troubleshoot --time-limit-minutes=181",
            "$troubleshoot --attempt-limit=-1",
            "$troubleshoot --attempt-limit",
            "$troubleshoot --attempt-limit=3 --attempt-limit=4",
            "$troubleshoot --time-limit-minutes=120 --time-limit-minutes=180",
            "$troubleshoot --attempt-limit=" + "9" * 80,
        )
        for index, prompt in enumerate(invalid_prompts, start=1):
            with self.subTest(prompt=prompt):
                output = self.evaluate_prompt(prompt, turn_id=f"invalid-{index}")
                self.assertEqual(output["decision"], "block")
                self.assertEqual(
                    guard.load_authorization_state(self.payload).kind, "missing"
                )

        ignored_prompts = (
            "please use $troubleshoot --attempt-limit=10",
            "`$troubleshoot --attempt-limit=10`",
            '"$troubleshoot --attempt-limit=10"',
        )
        for index, prompt in enumerate(ignored_prompts, start=1):
            with self.subTest(prompt=prompt):
                self.assertEqual(
                    self.evaluate_prompt(prompt, turn_id=f"ignored-{index}"), {}
                )
        trailing = self.evaluate_prompt(
            "$troubleshoot investigate --attempt-limit=10", turn_id="trailing"
        )
        self.assertIn(
            "attempt_limit=5", trailing["hookSpecificOutput"]["additionalContext"]
        )
        self.assertEqual(guard.load_authorization_state(self.payload).kind, "missing")

    def test_active_resize_requires_exact_marker_handshake(self) -> None:
        recorded = [attempt(1), attempt(2)]
        self.write_state(state_data(attempts=recorded, active_seconds=300))
        output = self.evaluate_prompt(
            "$troubleshoot --attempt-limit=10 --time-limit-minutes=180",
            turn_id="resize",
        )
        self.assertIn(
            "preserve its blocker, tranche, attempt ledger, counters",
            output["hookSpecificOutput"]["additionalContext"],
        )
        pending = self.authorization_data()["pending"]
        assert isinstance(pending, dict)

        denied = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")
        self.assertIn("budget update is pending", denied["permissionDecisionReason"])

        state_file = guard.state_file_for_payload(self.payload)
        assert state_file is not None
        self.payload.update(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Update File: {state_file}",
                            "@@",
                            "-old",
                            "+new",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        self.assertEqual(guard.evaluate(self.payload), {})

        self.write_state(
            state_data(
                attempts=recorded,
                active_seconds=301,
                attempt_limit=10,
                time_limit_minutes=180,
                budget_authorization_id=pending["authorization_id"],
            )
        )
        self.payload.update(
            {"tool_name": "Bash", "tool_input": {"command": "run-check"}}
        )
        denied = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")
        self.assertIsNotNone(self.authorization_data()["pending"])

        self.write_state(
            state_data(
                attempts=recorded,
                active_seconds=300,
                attempt_limit=10,
                time_limit_minutes=180,
                budget_authorization_id=pending["authorization_id"],
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})
        authorization = self.authorization_data()
        self.assertIsNone(authorization["pending"])
        self.assertEqual(
            authorization["current"]["authorization_id"],
            pending["authorization_id"],
        )

    def test_active_resize_rejects_limits_at_or_below_consumption(self) -> None:
        self.evaluate_prompt(
            "$troubleshoot --attempt-limit=10 --time-limit-minutes=180",
            turn_id="large-active-profile",
        )
        current = self.authorization_data()["current"]
        assert isinstance(current, dict)
        self.write_state(
            state_data(
                attempts=[attempt(1), attempt(2), attempt(3)],
                active_seconds=120 * 60,
                attempt_limit=10,
                time_limit_minutes=180,
                budget_authorization_id=current["authorization_id"],
            )
        )
        attempts = self.evaluate_prompt(
            "$troubleshoot --attempt-limit=3", turn_id="reduce-attempts"
        )
        self.assertEqual(attempts["decision"], "block")
        self.assertIn("strictly greater than the 3", attempts["reason"])
        minutes = self.evaluate_prompt(
            "$troubleshoot --time-limit-minutes=120", turn_id="reduce-time"
        )
        self.assertEqual(minutes["decision"], "block")
        self.assertIn("strictly above the 7200", minutes["reason"])
        self.assertIsNone(self.authorization_data()["pending"])

    def test_stop_requests_one_pending_marker_update(self) -> None:
        self.write_state(state_data(attempts=[attempt(1)], active_seconds=60))
        self.evaluate_prompt("$troubleshoot --attempt-limit=10", turn_id="resize-stop")
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "The profile update is not recorded.",
                "stop_hook_active": False,
            }
        )
        first = guard.evaluate(self.payload)
        self.assertEqual(first["decision"], "block")
        self.assertIn("Before another tool call", first["reason"])

        self.payload["stop_hook_active"] = True
        second = guard.evaluate(self.payload)
        self.assertFalse(second["continue"])
        self.assertIn("remains pending", second["stopReason"])
        self.assertIn("Before another tool call", second["systemMessage"])

    def test_pending_resize_invalid_marker_gives_atomic_restore_guidance(self) -> None:
        recorded = [attempt(1)]
        self.write_state(state_data(attempts=recorded, active_seconds=60))
        self.evaluate_prompt("$troubleshoot --attempt-limit=10", turn_id="resize")
        pending = self.authorization_data()["pending"]
        assert isinstance(pending, dict)

        invalid = state_data(
            attempts=recorded,
            active_seconds=60,
            attempt_limit=10,
            budget_authorization_id=pending["authorization_id"],
        )
        invalid.pop("blocker_summary")
        self.write_state(invalid)

        prompt = self.evaluate_prompt(
            "Continue after repairing the resize.", turn_id="resize-invalid"
        )
        prompt_context = prompt["hookSpecificOutput"]["additionalContext"]
        self.assertIn("missing: blocker_summary", prompt_context)
        self.assertIn("Restore every non-profile field", prompt_context)
        self.assertIn("apply the authorized profile change atomically", prompt_context)

        denied = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")
        self.assertIn("missing: blocker_summary", denied["permissionDecisionReason"])
        self.assertIn(
            "do not reset or invent blocker state",
            denied["permissionDecisionReason"],
        )

        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "The resize marker still needs repair.",
                "stop_hook_active": False,
            }
        )
        first = guard.evaluate(self.payload)
        self.assertEqual(first["decision"], "block")
        self.assertIn("Restore every non-profile field", first["reason"])

        self.payload["stop_hook_active"] = True
        second = guard.evaluate(self.payload)
        self.assertFalse(second["continue"])
        self.assertIn("missing: blocker_summary", second["systemMessage"])

        self.write_state(
            state_data(
                attempts=recorded,
                active_seconds=60,
                attempt_limit=10,
                budget_authorization_id=pending["authorization_id"],
            )
        )
        self.payload.update(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "run-check"},
                "stop_hook_active": False,
            }
        )
        self.assertEqual(self.evaluate_pre_tool(), {})
        self.assertIsNone(self.authorization_data()["pending"])

    def test_pending_resize_missing_marker_reports_safe_recovery_boundary(self) -> None:
        state_file = self.write_state(state_data(attempts=[attempt(1)]))
        self.evaluate_prompt("$troubleshoot --attempt-limit=10", turn_id="resize")
        state_file.unlink()

        prompt = self.evaluate_prompt(
            "Continue after restoring the marker.", turn_id="resize-missing"
        )
        prompt_context = prompt["hookSpecificOutput"]["additionalContext"]
        self.assertIn("marker is missing from current.md", prompt_context)
        self.assertIn("cannot reconstruct a deleted marker", prompt_context)
        self.assertIn("fresh user-authorized troubleshoot session", prompt_context)

        denied = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")
        self.assertIn(
            "restore the exact pre-resize canonical marker",
            denied["permissionDecisionReason"],
        )

        self.payload.update(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Add File: {state_file}",
                            "+restored marker",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "The resize marker is missing.",
                "stop_hook_active": False,
            }
        )
        first = guard.evaluate(self.payload)
        self.assertEqual(first["decision"], "block")
        self.assertIn("cannot reconstruct a deleted marker", first["reason"])

        self.payload["stop_hook_active"] = True
        second = guard.evaluate(self.payload)
        self.assertFalse(second["continue"])
        self.assertIn(
            "fresh user-authorized troubleshoot session", second["systemMessage"]
        )

    def test_pending_invalid_marker_surfaces_validation_reason(self) -> None:
        self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.evaluate_prompt("Continue troubleshooting.", turn_id="next-tranche")
        pending = self.authorization_data()["pending"]
        assert isinstance(pending, dict)

        invalid = state_data(
            tranche=2,
            started_at="2026-01-02T00:00:00Z",
            budget_authorization_id=pending["authorization_id"],
            override_summary="The user requested another bounded tranche.",
        )
        invalid.pop("blocker_summary")
        state_file = self.write_state(invalid)

        prompt = self.evaluate_prompt(
            "Continue after the marker update.", turn_id="pending-invalid"
        )
        prompt_context = prompt["hookSpecificOutput"]["additionalContext"]
        self.assertIn("remediation marker is invalid", prompt_context)
        self.assertIn("missing: blocker_summary", prompt_context)

        denied = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")
        self.assertIn("missing: blocker_summary", denied["permissionDecisionReason"])
        self.assertIn("complete canonical", denied["permissionDecisionReason"])

        self.payload.update(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Update File: {state_file}",
                            "@@",
                            "-old",
                            "+new",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

        self.payload.update(
            {
                "hook_event_name": "Stop",
                "tool_name": "Bash",
                "tool_input": {"command": "run-check"},
                "last_assistant_message": "The marker still needs repair.",
                "stop_hook_active": False,
            }
        )
        first = guard.evaluate(self.payload)
        self.assertEqual(first["decision"], "block")
        self.assertIn("missing: blocker_summary", first["reason"])

        self.payload["stop_hook_active"] = True
        second = guard.evaluate(self.payload)
        self.assertFalse(second["continue"])
        self.assertIn("missing: blocker_summary", second["systemMessage"])

    def test_pending_invalid_transition_surfaces_transition_reason(self) -> None:
        self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.evaluate_prompt("Continue troubleshooting.", turn_id="next-tranche")
        pending = self.authorization_data()["pending"]
        assert isinstance(pending, dict)

        self.write_state(
            state_data(
                blocker_key="other-component|other-operation|other-error|other-boundary",
                blocker_summary="A causally independent operation now fails.",
                tranche=2,
                started_at="2026-01-02T00:00:00Z",
                budget_authorization_id=pending["authorization_id"],
                override_summary="The user requested another bounded tranche.",
            )
        )

        prompt = self.evaluate_prompt(
            "Continue after the marker update.", turn_id="pending-transition"
        )
        prompt_context = prompt["hookSpecificOutput"]["additionalContext"]
        self.assertIn("causally independent blocker requires", prompt_context)

        denied = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")
        self.assertIn(
            "causally independent blocker requires tranche 1",
            denied["permissionDecisionReason"],
        )

        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "The transition still needs repair.",
                "stop_hook_active": False,
            }
        )
        stopped = guard.evaluate(self.payload)
        self.assertEqual(stopped["decision"], "block")
        self.assertIn("causally independent blocker requires", stopped["reason"])

    def test_resize_boundaries_allow_four_of_five_and_nine_of_ten(self) -> None:
        self.write_state(
            state_data(
                attempts=[attempt(number) for number in range(1, 5)],
                active_seconds=120 * 60 - 1,
            )
        )
        output = self.evaluate_prompt(
            "$troubleshoot --attempt-limit=5 --time-limit-minutes=120",
            turn_id="default-boundary",
        )
        self.assertIn(
            "budget_authorization_id=",
            output["hookSpecificOutput"]["additionalContext"],
        )

        pending = self.authorization_data()["pending"]
        assert isinstance(pending, dict)
        self.write_state(
            state_data(
                attempts=[attempt(number) for number in range(1, 5)],
                active_seconds=120 * 60 - 1,
                budget_authorization_id=pending["authorization_id"],
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

        current = self.authorization_data()["current"]
        assert isinstance(current, dict)
        self.payload["turn_id"] = "large-profile"
        authorization = guard._save_current_profile(
            self.payload, guard.load_authorization_state(self.payload), 10, 180
        )
        current = authorization.data["current"]
        self.write_state(
            state_data(
                attempts=[attempt(number) for number in range(1, 10)],
                active_seconds=180 * 60 - 1,
                attempt_limit=10,
                time_limit_minutes=180,
                budget_authorization_id=current["authorization_id"],
            )
        )
        output = self.evaluate_prompt(
            "$troubleshoot --attempt-limit=10 --time-limit-minutes=180",
            turn_id="large-boundary",
        )
        self.assertIn(
            "attempt_limit=10", output["hookSpecificOutput"]["additionalContext"]
        )

    def test_nondefault_marker_requires_exact_private_authorization(self) -> None:
        missing_id = state_data()
        missing_id.pop("budget_authorization_id")
        self.write_state(missing_id)
        reason = self.evaluate_pre_tool()["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        self.assertIn("missing: budget_authorization_id", reason)

        self.write_state(
            state_data(
                attempt_limit=10,
                time_limit_minutes=180,
                budget_authorization_id="0" * 32,
                override_summary=None,
            )
        )
        reason = self.evaluate_pre_tool()["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        self.assertIn("require the private session authorization sidecar", reason)

        self.payload["hook_event_name"] = "UserPromptSubmit"
        self.evaluate_prompt(
            "$troubleshoot --attempt-limit=10 --time-limit-minutes=180",
            turn_id="authorized",
        )
        # Invalid marker state requests exact repair and does not launder values.
        self.assertEqual(guard.load_authorization_state(self.payload).kind, "missing")

    def test_marker_rejects_mismatched_authorization_id_or_values(self) -> None:
        self.evaluate_prompt(
            "$troubleshoot --attempt-limit=10 --time-limit-minutes=180",
            turn_id="exact-binding",
        )
        current = self.authorization_data()["current"]
        assert isinstance(current, dict)
        self.write_state(
            state_data(
                attempt_limit=9,
                time_limit_minutes=180,
                budget_authorization_id=current["authorization_id"],
            )
        )
        reason = self.evaluate_pre_tool()["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        self.assertIn("do not match the private session authorization", reason)

        self.write_state(
            state_data(
                attempt_limit=10,
                time_limit_minutes=180,
                budget_authorization_id="f" * 32,
            )
        )
        reason = self.evaluate_pre_tool()["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        self.assertIn("do not match the private session authorization", reason)

    def test_authorization_sidecar_rejects_binding_and_permission_tampering(
        self,
    ) -> None:
        self.evaluate_prompt("$troubleshoot --attempt-limit=10", turn_id="binding")
        authorization_file = guard.authorization_file_for_payload(self.payload)
        assert authorization_file is not None
        original = json.loads(authorization_file.read_text(encoding="utf-8"))

        tampered = json.loads(json.dumps(original))
        tampered["workspace_hash"] = "0" * 64
        authorization_file.write_text(json.dumps(tampered), encoding="utf-8")
        authorization_file.chmod(0o600)
        authorization = guard.load_authorization_state(self.payload)
        self.assertEqual(authorization.kind, "invalid")
        self.assertIn("workspace binding", authorization.reason)

        authorization_file.write_text(json.dumps(original), encoding="utf-8")
        authorization_file.chmod(0o644)
        authorization = guard.load_authorization_state(self.payload)
        self.assertEqual(authorization.kind, "invalid")
        self.assertIn("permissions must be 0600", authorization.reason)

    def test_authorization_sidecar_rejects_symlinks_and_oversized_content(
        self,
    ) -> None:
        self.evaluate_prompt("$troubleshoot --attempt-limit=10", turn_id="safety")
        authorization_file = guard.authorization_file_for_payload(self.payload)
        assert authorization_file is not None
        target = authorization_file.with_name("authorization-target.json")
        target.write_text(
            authorization_file.read_text(encoding="utf-8"), encoding="utf-8"
        )
        authorization_file.unlink()
        authorization_file.symlink_to(target)
        authorization = guard.load_authorization_state(self.payload)
        self.assertEqual(authorization.kind, "invalid")
        self.assertIn("symbolic link", authorization.reason)

        authorization_file.unlink()
        authorization_file.write_text(
            "{" + " " * guard.MAX_AUTHORIZATION_BYTES + "}", encoding="utf-8"
        )
        authorization_file.chmod(0o600)
        authorization = guard.load_authorization_state(self.payload)
        self.assertEqual(authorization.kind, "invalid")
        self.assertIn("exceeds 4096 bytes", authorization.reason)

        authorization_file.write_text(
            '{"schema":"first","schema":"second"}', encoding="utf-8"
        )
        authorization_file.chmod(0o600)
        authorization = guard.load_authorization_state(self.payload)
        self.assertEqual(authorization.kind, "invalid")
        self.assertIn("duplicate JSON keys", authorization.reason)

    def test_exhausted_marker_needs_a_new_prompt_and_fresh_tranche(self) -> None:
        self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.evaluate_pre_tool()
        terminal = self.authorization_data()["terminal"]
        self.assertIsInstance(terminal, dict)

        # Clearing the ledger without a new prompt cannot reopen tranche 1.
        self.write_state(state_data())
        reason = self.evaluate_pre_tool()["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        self.assertIn("cannot be reopened", reason)

        # Restore the terminal marker, then let the next user instruction mint a
        # fresh-tranche authorization.
        self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        output = self.evaluate_prompt("continue", turn_id="continue-turn")
        self.assertIn(
            "complete canonical fresh active marker",
            output["hookSpecificOutput"]["additionalContext"],
        )
        pending_context = output["hookSpecificOutput"]["additionalContext"]
        for field in (
            "blocker_key",
            "blocker_summary",
            "attempts",
            "active_seconds",
            "started_at",
            "status",
            "stop_trigger",
            "tranche",
            "override_summary",
        ):
            self.assertIn(field, pending_context)
        pending = self.authorization_data()["pending"]
        assert isinstance(pending, dict)
        self.assertEqual(pending["mode"], "next_tranche")
        state_file = guard.state_file_for_payload(self.payload)
        assert state_file is not None
        self.payload.update(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Update File: {state_file}",
                            "@@",
                            "-old",
                            "+new",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        self.assertEqual(self.evaluate_pre_tool(), {})
        self.write_state(
            state_data(
                tranche=2,
                started_at="2026-01-02T00:00:00Z",
                budget_authorization_id=pending["authorization_id"],
                override_summary="The user requested another bounded tranche.",
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})
        authorization = self.authorization_data()
        self.assertIsNone(authorization["pending"])
        self.assertIsNone(authorization["terminal"])

    def test_resolved_marker_new_independent_blocker_promotes_pending_authorization(
        self,
    ) -> None:
        self.write_state(
            state_data(
                attempts=[attempt(1, result="succeeded")],
                status="resolved",
            )
        )

        output = self.evaluate_prompt("$troubleshoot", turn_id="resolved-next")
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("prior terminal marker", context)
        self.assertNotIn("exhausted marker", context)

        denied = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")
        self.assertIn("prior terminal marker", denied["permissionDecisionReason"])

        pending = self.authorization_data()["pending"]
        assert isinstance(pending, dict)
        self.assertEqual(pending["mode"], "next_tranche")
        self.write_state(
            state_data(
                blocker_key=(
                    "other-component|other-operation|other-error|other-boundary"
                ),
                blocker_summary="A causally independent operation now fails.",
                started_at="2026-01-02T00:00:00Z",
                budget_authorization_id=pending["authorization_id"],
            )
        )

        self.assertEqual(self.evaluate_pre_tool(), {})
        authorization = self.authorization_data()
        self.assertIsNone(authorization["pending"])
        self.assertIsNone(authorization["terminal"])

    def test_ten_attempt_fallback_is_complete_and_self_validating(self) -> None:
        self.evaluate_prompt(
            "$troubleshoot --attempt-limit=10 --time-limit-minutes=180",
            turn_id="large-report-profile",
        )
        current = self.authorization_data()["current"]
        assert isinstance(current, dict)
        self.write_state(
            state_data(
                attempts=[attempt(number) for number in range(1, 11)],
                attempt_limit=10,
                time_limit_minutes=180,
                budget_authorization_id=current["authorization_id"],
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        state = guard.load_guard_state(self.payload)
        report = guard._fallback_report(state, "missing report")
        self.assertIn("attempt-10", report)
        self.assertLess(len(report), guard.MAX_FALLBACK_PREVIEW_CHARS)
        self.assertTrue(guard._report_complete(report, state)[0])

    def test_new_user_turn_releases_terminal_lock_after_task_state_is_removed(
        self,
    ) -> None:
        state_file = self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.evaluate_pre_tool()
        self.assertIsInstance(self.authorization_data()["terminal"], dict)
        state_file.unlink()

        output = self.evaluate_prompt("Start the next task.", turn_id="new-task")
        context = output["hookSpecificOutput"]["additionalContext"]
        authorization = self.authorization_data()
        self.assertIsNone(authorization["terminal"])
        current = authorization["current"]
        assert isinstance(current, dict)
        self.assertIn(current["authorization_id"], context)

    def test_terminal_lock_denies_tools_while_exhausted_marker_is_missing(
        self,
    ) -> None:
        state_file = self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.evaluate_pre_tool()
        self.assertIsInstance(self.authorization_data()["terminal"], dict)
        state_file.unlink()

        denied = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")
        self.assertIn("terminal lock", denied["permissionDecisionReason"])
        self.assertIn("new user instruction", denied["permissionDecisionReason"])

        self.payload.update(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Add File: {state_file}",
                            "+restored marker",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "The exhausted marker is missing.",
                "stop_hook_active": False,
            }
        )
        stop_retry = guard.evaluate(self.payload)
        self.assertEqual(stop_retry["decision"], "block")
        self.assertIn("terminal lock", stop_retry["reason"])

        self.payload["stop_hook_active"] = True
        stopped = guard.evaluate(self.payload)
        self.assertFalse(stopped["continue"])
        self.assertIn("terminal lock", stopped["systemMessage"])

    def test_five_unique_failed_attempts_deny_next_tool(self) -> None:
        self.write_state(state_data(attempts=default_failed_attempts()))
        output = self.evaluate_pre_tool()
        decision = output["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("budget exhausted", decision["permissionDecisionReason"])

    def test_four_unique_failed_attempts_still_allow_next_tool(self) -> None:
        self.write_state(state_data(attempts=default_failed_attempts()[:-1]))
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_duplicate_distinct_key_is_rejected(self) -> None:
        repeated = "same-hypothesis|same-variable|same-target"
        self.write_state(
            state_data(
                attempts=[
                    attempt(1, distinct_key=repeated),
                    attempt(2, distinct_key=repeated),
                ]
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("new distinct_key", output["permissionDecisionReason"])

    def test_retry_requires_a_new_hypothesis(self) -> None:
        self.write_state(
            state_data(
                attempts=[
                    attempt(1, hypothesis="The parser accepts a stale value."),
                    attempt(2, hypothesis="  the PARSER accepts a stale value.  "),
                ]
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "new evidence-derived hypothesis",
            output["permissionDecisionReason"],
        )

    def test_retry_requires_new_evidence(self) -> None:
        self.write_state(
            state_data(
                attempts=[
                    attempt(1, new_evidence="A stack trace ends in parser.load."),
                    attempt(2, new_evidence="  a STACK trace ends in parser.load. "),
                ]
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("new evidence from logs", output["permissionDecisionReason"])

        missing_evidence = attempt(1)
        missing_evidence.pop("new_evidence")
        self.write_state(state_data(attempts=[missing_evidence]))
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertIn(
            "attempt 1 is incomplete; missing canonical fields: new_evidence",
            output["permissionDecisionReason"],
        )

    def test_terminal_legacy_marker_may_omit_evidence_but_stays_exhausted(
        self,
    ) -> None:
        legacy_attempts = [attempt(1), attempt(2), attempt(3)]
        for item in legacy_attempts:
            item.pop("new_evidence")
            item["id"] = f"legacy-{item['distinct_key']}"
        self.write_state(
            legacy_state_data(
                attempts=legacy_attempts,
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("budget exhausted", output["permissionDecisionReason"])
        self.assertNotIn(
            "marker is present but invalid", output["permissionDecisionReason"]
        )

    def test_current_terminal_schema_still_requires_new_evidence(self) -> None:
        attempts = default_failed_attempts()
        attempts[-1].pop("new_evidence")
        self.write_state(
            state_data(
                attempts=attempts,
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertIn(
            "attempt 5 is incomplete; missing canonical fields: new_evidence",
            output["permissionDecisionReason"],
        )

    def test_legacy_schema_is_report_only(self) -> None:
        self.write_state(legacy_state_data())
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertIn(
            "markers are accepted only as exhausted report-only state",
            output["permissionDecisionReason"],
        )

    def test_legacy_attempt_limit_retains_original_ceiling(self) -> None:
        self.write_state(
            legacy_state_data(
                attempt_limit=guard.HISTORICAL_MAX_ATTEMPT_LIMIT + 1,
                override_summary="Historical state requested a larger limit.",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertIn(
            "codex/remediation-budget-v1 attempt_limit must be an integer from 1 to 3",
            output["permissionDecisionReason"],
        )

    def test_previous_v2_schema_requires_canonical_marker_repair(self) -> None:
        self.write_state(
            state_data(
                schema="codex/remediation-budget-v2",
                attempt_limit=guard.HISTORICAL_MAX_ATTEMPT_LIMIT,
                time_limit_minutes=guard.HISTORICAL_DEFAULT_TIME_LIMIT_MINUTES,
                attempts=[attempt(1), attempt(2)],
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "marker schema must be codex/remediation-budget-v4",
            output["permissionDecisionReason"],
        )

        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "The marker needs repair.",
                "stop_hook_active": False,
            }
        )
        stop_output = guard.evaluate(self.payload)
        self.assertEqual(stop_output["decision"], "block")
        self.assertIn(
            "Repair only the exact advertised current.md", stop_output["reason"]
        )
        self.assertNotIn(guard.REPORT_MARKER, stop_output["reason"])

    def test_time_limit_is_inclusive_and_uses_active_time(self) -> None:
        self.write_state(
            state_data(
                active_seconds=guard.DEFAULT_TIME_LIMIT_MINUTES * 60,
                attempt_limit=guard.DEFAULT_ATTEMPT_LIMIT,
                time_limit_minutes=guard.DEFAULT_TIME_LIMIT_MINUTES,
            )
        )
        output = self.evaluate_pre_tool()
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_idle_wall_clock_time_does_not_consume_active_time(self) -> None:
        self.write_state(
            state_data(
                started_at="2020-01-01T00:00:00Z",
                active_seconds=guard.DEFAULT_TIME_LIMIT_MINUTES * 60 - 1,
                attempt_limit=guard.DEFAULT_ATTEMPT_LIMIT,
                time_limit_minutes=guard.DEFAULT_TIME_LIMIT_MINUTES,
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_resolved_state_does_not_block(self) -> None:
        self.write_state(
            state_data(
                attempts=[
                    attempt(1),
                    attempt(2),
                    attempt(3, result="succeeded"),
                ],
                status="resolved",
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_attempt_limit_cannot_be_raised_or_disabled(self) -> None:
        self.write_state(
            state_data(
                attempt_limit=guard.MAX_ATTEMPT_LIMIT + 1,
                override_summary="The current user requested six attempts.",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "attempt_limit must be an integer from 1 to 10",
            output["permissionDecisionReason"],
        )

        self.write_state(
            state_data(
                attempt_limit=None,
                override_summary="The current user requested unlimited attempts.",
            )
        )
        output = self.evaluate_pre_tool()
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_v3_is_not_reinterpreted_even_with_free_text_override(
        self,
    ) -> None:
        self.write_state(
            state_data(
                schema=guard.PREVIOUS_SCHEMA,
                attempt_limit=guard.HISTORICAL_MAX_ATTEMPT_LIMIT,
                time_limit_minutes=guard.HISTORICAL_DEFAULT_TIME_LIMIT_MINUTES,
                override_summary=(
                    "Stale global policy called three attempts and sixty minutes "
                    "the default."
                ),
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "codex/remediation-budget-v3 markers are not reinterpreted",
            output["permissionDecisionReason"],
        )

        self.write_state(
            state_data(
                schema=guard.PREVIOUS_SCHEMA,
                attempt_limit=guard.DEFAULT_ATTEMPT_LIMIT,
                time_limit_minutes=None,
                override_summary="The current user requested an earlier stop.",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "replace the exact marker with codex/remediation-budget-v4",
            output["permissionDecisionReason"],
        )

        prompt_output = self.evaluate_prompt(
            "$troubleshoot --attempt-limit=10", turn_id="v3-repair"
        )
        self.assertIn(
            "Repair the exact advertised current.md marker",
            prompt_output["hookSpecificOutput"]["additionalContext"],
        )
        self.assertEqual(guard.load_authorization_state(self.payload).kind, "missing")

    def test_initial_v3_tranche_rejects_override_summary(self) -> None:
        self.write_state(
            state_data(
                override_summary="Stale policy should not be laundered as an override.",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "initial v4 tranche requires a null override_summary",
            output["permissionDecisionReason"],
        )

    def test_noncanonical_lower_limit_is_rejected_before_ledger_state(self) -> None:
        self.write_state(
            state_data(
                attempt_limit=1,
                attempts=[attempt(1), attempt(2)],
                status="exhausted",
                stop_trigger="attempt_limit",
                override_summary="The current user lowered the attempt limit.",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "require the private session authorization sidecar",
            output["permissionDecisionReason"],
        )

    def test_noncanonical_attempt_fails_with_repairable_reason(self) -> None:
        self.write_state(
            state_data(
                attempts=[
                    {
                        "blocker_key": BLOCKER_KEY,
                        "attempt": 1,
                        "finished_at": "2026-01-01T00:45:00Z",
                        "result": "failed",
                        "error": "same blocker",
                    }
                ]
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "attempt 1 is incomplete; missing canonical fields",
            output["permissionDecisionReason"],
        )
        self.assertIn(
            "remove it and keep planned or in-progress work in prose",
            output["permissionDecisionReason"],
        )
        self.assertIn(
            "does not consume an attempt or exhaust",
            output["permissionDecisionReason"],
        )

    def test_planned_attempt_reports_all_missing_fields_and_atomic_repair(self) -> None:
        planned_attempt = attempt(1)
        for field in ("remediation", "verification", "result"):
            planned_attempt.pop(field)
        planned_attempt["repair"] = "Human-readable planned repair."

        self.write_state(state_data(attempts=[planned_attempt]))

        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        reason = output["permissionDecisionReason"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "missing canonical fields: remediation, verification, result",
            reason,
        )
        self.assertIn("record completed remediation and verification only", reason)
        self.assertIn("remove it", reason)
        self.assertIn("repair every missing field atomically", reason)
        self.assertNotIn(planned_attempt["repair"], reason)

    def test_unsupported_result_names_the_canonical_repair(self) -> None:
        invalid_result = "different_blocker"
        self.write_state(state_data(attempts=[attempt(1, result=invalid_result)]))

        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        reason = output["permissionDecisionReason"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "attempt 1 result must be failed_same_blocker or succeeded",
            reason,
        )
        self.assertIn("remove it and keep unverified progress in prose", reason)
        self.assertIn("fresh empty attempt ledger", reason)
        self.assertNotIn(invalid_result, reason)

    def test_continuation_tranche_requires_user_override_summary(self) -> None:
        self.write_state(state_data(tranche=2))
        output = self.evaluate_pre_tool()
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_status_trigger_and_limit_state_must_be_consistent(self) -> None:
        self.write_state(state_data(stop_trigger="attempt_limit"))
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertIn(
            "active state requires a null stop_trigger",
            output["permissionDecisionReason"],
        )

        self.write_state(
            state_data(
                attempts=[attempt(1)],
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertIn(
            "failed-attempt limit to be reached",
            output["permissionDecisionReason"],
        )

        self.write_state(
            state_data(
                active_seconds=1,
                status="exhausted",
                stop_trigger="time_limit",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertIn(
            "active-time limit to be reached",
            output["permissionDecisionReason"],
        )

        self.write_state(
            state_data(
                attempts=[attempt(1), attempt(2), attempt(3)],
                status="resolved",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertIn(
            "resolved state requires a successful final attempt",
            output["permissionDecisionReason"],
        )

        self.write_state(
            state_data(
                attempts=[attempt(1, result="succeeded")],
                status="active",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertIn(
            "successful final attempt requires resolved state",
            output["permissionDecisionReason"],
        )

    def test_stop_does_not_exhaust_before_one_blocker_reaches_its_limit(
        self,
    ) -> None:
        for failed_attempts in range(guard.DEFAULT_ATTEMPT_LIMIT):
            with self.subTest(failed_attempts=failed_attempts):
                self.payload.update(
                    {
                        "hook_event_name": "Stop",
                        "last_assistant_message": complete_report(
                            attempt_count=failed_attempts
                        ),
                        "stop_hook_active": False,
                    }
                )
                self.write_state(
                    state_data(
                        attempts=[
                            attempt(number) for number in range(1, failed_attempts + 1)
                        ],
                        status="exhausted",
                        stop_trigger="attempt_limit",
                    )
                )

                output = guard.evaluate(self.payload)
                self.assertEqual(output["decision"], "block")
                self.assertIn("marker is invalid", output["reason"])
                self.assertNotIn(guard.REPORT_MARKER, output["reason"])

    def test_attempt_ledger_is_bounded_to_ten_entries(self) -> None:
        self.write_state(
            state_data(attempts=[attempt(number) for number in range(1, 12)])
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "attempts must be a list with at most 10 entries",
            output["permissionDecisionReason"],
        )

    def test_marker_repair_preserves_active_budget(self) -> None:
        state_file = self.write_state(
            state_data(
                attempts=[
                    {
                        "attempt": 1,
                        "result": "failed",
                    }
                ]
            )
        )
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

        self.payload.update(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Update File: {state_file}",
                            "@@",
                            "-old",
                            "+new",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

        self.write_state(state_data(attempts=[attempt(1), attempt(2)]))
        self.payload.update(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "run-check"},
            }
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "Marker repaired; work remains.",
                "stop_hook_active": False,
            }
        )
        self.assertEqual(guard.evaluate(self.payload), {"continue": True})

    def test_causally_new_blocker_starts_a_fresh_budget(self) -> None:
        self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

        self.evaluate_prompt(
            "Investigate the causally independent operation.",
            turn_id="independent-blocker",
        )
        pending = self.authorization_data()["pending"]
        assert isinstance(pending, dict)

        self.write_state(
            state_data(
                blocker_key="other-component|other-operation|other-error|other-boundary",
                blocker_summary="A causally independent operation now fails.",
                tranche=1,
                started_at="2026-01-01T01:00:00Z",
                active_seconds=0,
                attempts=[],
                status="active",
                stop_trigger=None,
                budget_authorization_id=pending["authorization_id"],
                override_summary=None,
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_attempts_must_be_bound_to_the_markers_single_blocker(self) -> None:
        other_blocker = "other-component|other-operation|other-error|other-boundary"
        self.write_state(
            state_data(
                blocker_key=other_blocker,
                blocker_summary="A causally independent operation now fails.",
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )

        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "must match the marker blocker_key",
            output["permissionDecisionReason"],
        )
        self.assertNotIn("budget exhausted", output["permissionDecisionReason"])

        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": complete_report(),
                "stop_hook_active": False,
            }
        )
        stop_output = guard.evaluate(self.payload)
        self.assertEqual(stop_output["decision"], "block")
        self.assertIn("marker is invalid", stop_output["reason"])
        self.assertNotIn(guard.REPORT_MARKER, stop_output["reason"])

    def test_attempt_blocker_binding_is_required(self) -> None:
        unbound = attempt(1)
        unbound.pop("blocker_key")
        self.write_state(state_data(attempts=[unbound]))

        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "attempt 1 is incomplete; missing canonical fields: blocker_key",
            output["permissionDecisionReason"],
        )

    def test_only_exact_current_state_patch_is_allowed_after_exhaustion(self) -> None:
        state_file = self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.payload.update(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Update File: {state_file}",
                            "@@",
                            "-old",
                            "+new",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        self.assertEqual(self.evaluate_pre_tool(), {})
        self.payload["tool_input"] = {
            "command": "\n".join(
                [
                    "*** Begin Patch",
                    f"*** Update File: {state_file}",
                    "@@",
                    "-old",
                    "+new",
                    f"*** Update File: {self.root / 'other.txt'}",
                    "@@",
                    "-old",
                    "+new",
                    "*** End Patch",
                ]
            )
        }
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_malformed_and_oversized_markers_fail_closed(self) -> None:
        self.write_state({}, raw="{")
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )
        self.write_state(
            {},
            raw=(
                '{"schema":"codex/remediation-budget-v4",'
                '"schema":"codex/remediation-budget-v4"}'
            ),
        )
        reason = self.evaluate_pre_tool()["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        self.assertIn("duplicate JSON keys", reason)

    def test_invalid_state_reason_does_not_reflect_paths_or_marker_content(
        self,
    ) -> None:
        state_file = self.write_state(
            {},
            raw='{"private_value": "/private/internal-host/current.md",',
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        reason = output["permissionDecisionReason"]
        self.assertIn("marker JSON is malformed", reason)
        self.assertNotIn("private_value", reason)
        self.assertNotIn("/private/internal-host/current.md", reason)

        self.write_state(state_data())
        with patch.object(
            Path,
            "open",
            side_effect=PermissionError(
                f"permission denied: {state_file.parent / 'private-current.md'}"
            ),
        ):
            output = self.evaluate_pre_tool()["hookSpecificOutput"]
        reason = output["permissionDecisionReason"]
        self.assertIn("task-state marker could not be read safely", reason)
        self.assertNotIn("private-current.md", reason)
        self.assertLessEqual(len(reason), 1024)

        state_file = guard.state_file_for_payload(self.payload)
        assert state_file is not None
        state_file.write_text(
            f"{guard.MARKER_START}{json.dumps(state_data())}"
            + (" " * guard.MAX_MARKER_PREFIX_BYTES)
            + guard.MARKER_END,
            encoding="utf-8",
        )
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_large_task_state_after_bounded_marker_is_allowed(self) -> None:
        state_file = self.write_state(state_data())
        with state_file.open("a", encoding="utf-8") as handle:
            handle.write("x" * guard.MAX_MARKER_PREFIX_BYTES)
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_marker_after_prefix_and_oversized_task_state_fail_closed(self) -> None:
        state_file = guard.state_file_for_payload(self.payload)
        assert state_file is not None
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            ("x" * guard.MAX_MARKER_PREFIX_BYTES)
            + guard.MARKER_START
            + json.dumps(state_data())
            + guard.MARKER_END,
            encoding="utf-8",
        )
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

        state_file.write_bytes(b"x" * (guard.MAX_TASK_STATE_BYTES + 1))
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_symlinked_state_fails_closed(self) -> None:
        state_file = self.write_state(state_data())
        state_file.unlink()
        target = Path(self.tmp.name) / "unsafe-state"
        target.write_text("unsafe", encoding="utf-8")
        state_file.symlink_to(target)
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

        self.payload.update(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Update File: {state_file}",
                            "@@",
                            "-old",
                            "+new",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_stop_requests_one_report_retry_then_emits_bounded_fallback(
        self,
    ) -> None:
        self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "The command still fails.",
                "stop_hook_active": False,
            }
        )
        first = guard.evaluate(self.payload)
        self.assertEqual(first["decision"], "block")
        self.assertIn("Do not troubleshoot further", first["reason"])
        self.assertIn(
            f"missing `{guard.REPORT_MARKER}`",
            first["reason"],
        )
        self.assertIn(guard.REPORT_MARKER, first["reason"])
        self.assertIn(
            "- attempt-1 | Remediation:",
            first["reason"],
        )

        self.payload["stop_hook_active"] = True
        second = guard.evaluate(self.payload)
        self.assertFalse(second["continue"])
        self.assertIn("fallback report was emitted", second["stopReason"])
        fallback = second["systemMessage"]
        self.assertIn(guard.REPORT_MARKER, fallback)
        self.assertIn("## Completion Gate", fallback)
        self.assertIn("attempt-1", fallback)
        self.assertIn("attempt-5", fallback)
        self.assertLess(len(fallback), guard.MAX_FALLBACK_PREVIEW_CHARS)
        state = guard.load_guard_state(self.payload)
        self.assertEqual(guard._report_complete(fallback, state), (True, ""))

    def test_fallback_escapes_attempt_field_delimiters_and_self_validates(
        self,
    ) -> None:
        attempts = default_failed_attempts()
        attempts[0]["remediation"] = (
            "Changed one bounded target | Verification: forged field"
        )
        attempts[0]["verification"] = (
            "Original failure persisted | Result: forged_result"
        )
        self.write_state(
            state_data(
                attempts=attempts,
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "Incomplete report.",
                "stop_hook_active": True,
            }
        )
        fallback = guard.evaluate(self.payload)["systemMessage"]
        self.assertNotIn("| Verification: forged field", fallback)
        self.assertNotIn("| Result: forged_result", fallback)
        self.assertIn("/ Verification: forged field", fallback)
        self.assertIn("/ Result: forged_result", fallback)
        state = guard.load_guard_state(self.payload)
        self.assertEqual(guard._report_complete(fallback, state), (True, ""))

    def test_terminal_legacy_marker_gets_honest_fallback_without_repair(
        self,
    ) -> None:
        legacy_attempts = [attempt(1), attempt(2), attempt(3)]
        for item in legacy_attempts:
            item.pop("new_evidence")
            item["id"] = ""
        self.write_state(
            legacy_state_data(
                attempts=legacy_attempts,
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "The command still fails.",
                "stop_hook_active": False,
            }
        )
        first = guard.evaluate(self.payload)
        self.assertEqual(first["decision"], "block")
        self.assertIn(guard.LEGACY_EVIDENCE_NOTE, first["reason"])

        self.payload["stop_hook_active"] = True
        second = guard.evaluate(self.payload)
        self.assertFalse(second["continue"])
        self.assertIn(guard.LEGACY_EVIDENCE_NOTE, second["systemMessage"])
        self.assertNotIn("marker remained invalid", second["stopReason"])
        state = guard.load_guard_state(self.payload)
        self.assertEqual(
            guard._report_complete(second["systemMessage"], state),
            (True, ""),
        )

        self.payload["stop_hook_active"] = False
        self.payload["last_assistant_message"] = complete_report(
            attempt_count=guard.HISTORICAL_MAX_ATTEMPT_LIMIT,
            legacy_evidence=True,
        )
        delivered = self.evaluate_stop_with_arbiter()
        self.assertFalse(delivered["continue"])
        self.assertIn("report delivered", delivered["stopReason"])

    def test_fallback_report_does_not_reflect_sensitive_marker_values(self) -> None:
        sensitive_attempt = attempt(1)
        sensitive_attempt["remediation"] = "token=do-not-echo"
        sensitive_attempt["verification"] = "Bearer private-value"
        sensitive_attempt["new_evidence"] = "https://private.example.invalid/log"
        self.write_state(
            state_data(
                blocker_key="password=do-not-echo",
                blocker_summary="/Users/private/path",
                attempts=[sensitive_attempt],
                active_seconds=guard.DEFAULT_TIME_LIMIT_MINUTES * 60,
                status="exhausted",
                stop_trigger="time_limit",
            )
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "Incomplete report.",
                "stop_hook_active": True,
            }
        )
        fallback = guard.evaluate(self.payload)["systemMessage"]
        for sensitive_value in (
            "do-not-echo",
            "private-value",
            "private.example.invalid",
            "/Users/private/path",
        ):
            self.assertNotIn(sensitive_value, fallback)

    def test_fallback_redacts_private_network_and_cloud_identifiers(self) -> None:
        for sensitive_value in (
            "172.16.2.3",
            "fd00::1",
            "db.internal.local",
            "internal.example.com",
            "localhost",
            "AKIA" + "ABCDEFGHIJKLMNOP",
            r"C:\Users\private\state.txt",
            "credential=do-not-echo",
        ):
            with self.subTest(sensitive_value=sensitive_value):
                self.assertEqual(
                    guard._bounded_report_value(
                        f"Observed {sensitive_value} during inspection.",
                        "Sensitive value redacted.",
                    ),
                    "Sensitive value redacted.",
                )

    def test_sensitive_detector_allows_reserved_words_in_ordinary_prose(self) -> None:
        for public_safe_text in (
            "The local test still fails.",
            "The internal state remains unchanged.",
            "The private data was not inspected.",
            "The corp policy check did not run.",
        ):
            with self.subTest(public_safe_text=public_safe_text):
                self.assertFalse(
                    guard._contains_sensitive_report_value(public_safe_text)
                )

    def test_fallback_report_stays_below_the_hook_output_preview_limit(self) -> None:
        attempts = default_failed_attempts()
        for index, item in enumerate(attempts, start=1):
            item["remediation"] = ("bounded remediation summary " * 30)[:512]
            item["verification"] = ("bounded verification summary " * 30)[:512]
            item["new_evidence"] = (
                f"attempt {index} " + ("bounded evidence summary " * 40)
            )[:768]
        self.write_state(
            state_data(
                blocker_summary=("bounded blocker summary " * 30)[:512],
                attempts=attempts,
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "Incomplete report.",
                "stop_hook_active": False,
            }
        )
        correction = guard.evaluate(self.payload)["reason"]
        self.assertLess(len(correction), guard.MAX_FALLBACK_PREVIEW_CHARS)
        self.assertIn("attempt-5", correction)

        self.payload["stop_hook_active"] = True
        fallback_output = guard.evaluate(self.payload)
        fallback = fallback_output["systemMessage"]
        self.assertLess(len(fallback), guard.MAX_FALLBACK_PREVIEW_CHARS)
        self.assertLess(
            len(json.dumps(fallback_output)), guard.MAX_FALLBACK_PREVIEW_CHARS
        )
        self.assertIn("attempt-5", fallback)

    def test_longest_report_issue_keeps_full_correction_below_preview_limit(
        self,
    ) -> None:
        attempts = default_failed_attempts()
        for index, item in enumerate(attempts, start=1):
            item["remediation"] = ("bounded remediation summary " * 30)[:512]
            item["verification"] = ("bounded verification summary " * 30)[:512]
            item["new_evidence"] = (
                f"attempt {index} " + ("bounded evidence summary " * 40)
            )[:768]
        self.write_state(
            state_data(
                blocker_summary=("bounded blocker summary " * 30)[:512],
                attempts=attempts,
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        state = guard.load_guard_state(self.payload)
        incomplete_report = "\n".join(
            line
            for line in guard._fallback_report(state, "seed").splitlines()
            if not line.startswith("- attempt-5 | Remediation:")
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": incomplete_report,
                "stop_hook_active": False,
            }
        )

        correction = guard.evaluate(self.payload)
        self.assertIn(
            "Remediation requires substantive Remediation, Verification, and Result "
            "fields for attempt-5",
            correction["reason"],
        )
        self.assertLess(len(correction["reason"]), guard.MAX_FALLBACK_PREVIEW_CHARS)
        self.assertLess(len(json.dumps(correction)), guard.MAX_FALLBACK_PREVIEW_CHARS)

    def test_stop_requests_marker_repair_without_exhaustion_report(self) -> None:
        self.write_state({}, raw="{")
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "The marker needs repair.",
                "stop_hook_active": False,
            }
        )
        output = guard.evaluate(self.payload)
        self.assertEqual(output["decision"], "block")
        self.assertIn("Repair only the exact advertised current.md", output["reason"])
        self.assertIn("do not exhaust", output["reason"])
        self.assertNotIn(guard.REPORT_MARKER, output["reason"])
        self.assertNotIn("## Outcome", output["reason"])

    def test_stop_rejects_inexact_heading_or_missing_stop_trigger(self) -> None:
        self.write_state(
            state_data(
                active_seconds=guard.DEFAULT_TIME_LIMIT_MINUTES * 60,
                status="exhausted",
                stop_trigger="time_limit",
            )
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": complete_report(
                    stop_trigger="time_limit", attempt_count=0
                ).replace("## Root Cause", "## Root Cause Details"),
                "stop_hook_active": False,
            }
        )
        self.assertEqual(guard.evaluate(self.payload)["decision"], "block")

        self.payload["last_assistant_message"] = complete_report(
            stop_trigger="attempt_limit", attempt_count=0
        )
        self.assertEqual(guard.evaluate(self.payload)["decision"], "block")

        self.payload["last_assistant_message"] = "\n".join(
            [
                guard.REPORT_MARKER,
                "Stop trigger: time_limit",
                *guard.REPORT_HEADINGS,
            ]
        )
        self.assertEqual(guard.evaluate(self.payload)["decision"], "block")

    def test_stop_rejects_placeholder_or_marker_incomplete_report(self) -> None:
        self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        placeholder_report = "\n".join(
            [
                guard.REPORT_MARKER,
                "Stop trigger: attempt_limit",
                *[
                    "\n".join([heading, "placeholder placeholder"])
                    for heading in guard.REPORT_HEADINGS
                ],
            ]
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": placeholder_report,
                "stop_hook_active": False,
            }
        )
        self.assertEqual(guard.evaluate(self.payload)["decision"], "block")

        incomplete = complete_report().replace("attempt-2 |", "second-attempt |")
        self.payload["last_assistant_message"] = incomplete
        self.assertEqual(guard.evaluate(self.payload)["decision"], "block")

        generic_attempts = complete_report().replace(
            "- attempt-2 | Remediation: remediation 2 | "
            "Verification: verification 2 | Result: failed_same_blocker",
            "- attempt-2 | Result: failed_same_blocker",
        )
        self.payload["last_assistant_message"] = generic_attempts
        self.assertEqual(guard.evaluate(self.payload)["decision"], "block")

        generic_evidence = complete_report().replace(
            "- attempt-2 | Evidence: new evidence 2",
            "- attempt-2 | Evidence: generic",
        )
        self.payload["last_assistant_message"] = generic_evidence
        self.assertEqual(guard.evaluate(self.payload)["decision"], "block")

        generic_fields = (
            complete_report()
            .replace(
                "- attempt-2 | Remediation: remediation 2 | "
                "Verification: verification 2 | Result: failed_same_blocker",
                "- attempt-2 | Remediation: generic observation | "
                "Verification: generic observation | Result: failed_same_blocker",
            )
            .replace(
                "- attempt-2 | Evidence: new evidence 2",
                "- attempt-2 | Evidence: generic observation",
            )
        )
        self.payload["last_assistant_message"] = generic_fields
        self.assertEqual(guard.evaluate(self.payload)["decision"], "block")

        for sensitive_value in (
            "credential=do-not-echo",
            '"token": "do-not-echo"',
            "Bearer do-not-echo",
            "https://private.example.invalid/log",
            "172.16.2.3",
            "internal.example.com",
            r"C:\Users\private\state.txt",
        ):
            with self.subTest(sensitive_report_value=sensitive_value):
                self.payload["last_assistant_message"] = complete_report().replace(
                    "remediation 2",
                    sensitive_value,
                )
                sensitive_result = guard.evaluate(self.payload)
                self.assertEqual(sensitive_result["decision"], "block")
                self.assertIn("sensitive value", sensitive_result["reason"])

        self.payload["stop_hook_active"] = True
        fallback = guard.evaluate(self.payload)
        self.assertFalse(fallback["continue"])
        self.assertIn("fallback report was emitted", fallback["stopReason"])

    def test_stop_distinguishes_missing_and_paraphrased_blocker_lines(self) -> None:
        self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        state = guard.load_guard_state(self.payload)
        complete = guard._fallback_report(state, "seed")

        missing = "\n".join(
            "The blocking section remains intentionally substantive."
            if line.startswith("- Blocker: ")
            else line
            for line in complete.splitlines()
        )
        self.assertEqual(
            guard._report_complete(missing, state)[1],
            "Root Cause requires one substantive `- Blocker:` line",
        )

        paraphrased = "\n".join(
            "- Blocker: A different substantive summary of the same failure."
            if line.startswith("- Blocker: ")
            else line
            for line in complete.splitlines()
        )
        self.assertEqual(
            guard._report_complete(paraphrased, state)[1],
            "Root Cause `- Blocker:` line must exactly match the bounded "
            "marker-derived value",
        )

        missing_source = "\n".join(
            "The source section remains intentionally substantive."
            if line.startswith("- Blocker key: ")
            else line
            for line in complete.splitlines()
        )
        self.assertEqual(
            guard._report_complete(missing_source, state)[1],
            "Root Cause requires one substantive `- Blocker key:` line",
        )

        paraphrased_source = "\n".join(
            "- Blocker key: another|substantive|source|boundary"
            if line.startswith("- Blocker key: ")
            else line
            for line in complete.splitlines()
        )
        self.assertEqual(
            guard._report_complete(paraphrased_source, state)[1],
            "Root Cause `- Blocker key:` line must exactly match the bounded "
            "marker-derived value",
        )

    def test_complete_exhausted_report_waits_for_other_stop_hooks(
        self,
    ) -> None:
        self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": complete_report(),
                "stop_hook_active": False,
            }
        )
        ready = guard.evaluate(self.payload)
        self.assertTrue(ready["continue"])
        self.assertTrue(ready[guard.REPORT_READY_FIELD])

        hook_dir = Path(self.tmp.name) / "exhausted-hooks"
        hook_dir.mkdir()
        for delegate in arbiter.DELEGATES:
            (hook_dir / delegate).write_text("# test delegate\n", encoding="utf-8")

        def with_project_block(
            path: Path,
            payload: dict[str, object],
            _deadline: float,
        ) -> dict[str, object]:
            if path.name == "remediation_attempt_guard.py":
                return guard.evaluate(payload)
            if path.name == "project_specs_lifecycle.py":
                return {
                    "decision": "block",
                    "reason": "Project reconciliation remains required.",
                }
            return {"continue": True}

        with patch.object(arbiter, "_run_delegate", side_effect=with_project_block):
            blocked = arbiter.evaluate(self.payload, hook_dir)
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("Project reconciliation", blocked["reason"])

        def without_block(
            path: Path,
            payload: dict[str, object],
            _deadline: float,
        ) -> dict[str, object]:
            if path.name == "remediation_attempt_guard.py":
                return guard.evaluate(payload)
            return {"continue": True}

        with patch.object(arbiter, "_run_delegate", side_effect=without_block):
            delivered = arbiter.evaluate(self.payload, hook_dir)
        self.assertFalse(delivered["continue"])
        self.assertIn("report delivered", delivered["stopReason"])

    def test_exhausted_report_finalizes_before_terminal_peer_result(self) -> None:
        self.evaluate_prompt("$troubleshoot", turn_id="exhausted-terminal-peer")
        self.write_state(
            state_data(
                attempts=default_failed_attempts(),
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": complete_report(),
                "stop_hook_active": False,
            }
        )
        hook_dir = Path(self.tmp.name) / "exhausted-terminal-peer-hooks"
        hook_dir.mkdir()
        for delegate in arbiter.DELEGATES:
            (hook_dir / delegate).write_text("# test delegate\n", encoding="utf-8")
        terminal = {
            "continue": False,
            "stopReason": "A peer Stop policy ended the host turn.",
        }

        def with_terminal_peer(
            path: Path,
            payload: dict[str, object],
            _deadline: float,
        ) -> dict[str, object]:
            if path.name == "remediation_attempt_guard.py":
                return guard.evaluate(payload)
            if path.name == "project_specs_lifecycle.py":
                return terminal
            return {"continue": True}

        with patch.object(arbiter, "_run_delegate", side_effect=with_terminal_peer):
            result = arbiter.evaluate(self.payload, hook_dir)
        self.assertEqual(result, terminal)
        self.assertEqual(self.report_obligation_data()["status"], "delivered")

    def test_fresh_continuation_tranche_is_allowed(self) -> None:
        self.write_state(
            state_data(
                tranche=2,
                started_at="2026-01-01T00:59:00Z",
                attempts=[],
                override_summary="The current user said continue for five more attempts.",
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})


if __name__ == "__main__":
    unittest.main()
