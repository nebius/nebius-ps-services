#!/usr/bin/env python3
"""Cross-skill contract tests for troubleshooting boundaries."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
TROUBLESHOOT = ROOT / "troubleshoot"
DESIGN = ROOT / "design"


class TroubleshootContractTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_troubleshoot_routes_only_system_contract_changes_after_proof(
        self,
    ) -> None:
        skill = self.read(TROUBLESHOOT / "SKILL.md")
        normalized = " ".join(skill.split())

        self.assertIn(
            "use the installed `design` skill before implementation only when",
            normalized,
        )
        self.assertIn(
            "Keep repairs inside an existing private boundary in `troubleshoot`",
            normalized,
        )
        self.assertIn(
            "A design-scale remedy changes at least one system contract",
            normalized,
        )
        self.assertIn(
            "Implementation size, algorithmic complexity, concurrency difficulty, "
            "or a large rewrite inside one existing private boundary does not make "
            "a repair design-scale",
            normalized,
        )
        self.assertNotIn("another non-trivial or hard-to-reverse", normalized)
        self.assertIn(
            "use `design` after causal proof and before implementation",
            normalized,
        )
        self.assertIn(
            "it must not reopen diagnosis or implement the change",
            normalized,
        )
        handoff = skill.index("use `design` after causal proof")
        self.assertLess(skill.index("7. **PROVEN**"), handoff)
        self.assertLess(handoff, skill.index("8. **REMEDIATED**"))
        self.assertIn(
            "If a design-scale remediation is required but `design` is unavailable",
            normalized,
        )

    def test_agentic_sdlc_routes_through_classifier_and_coordinator(self) -> None:
        skill = self.read(TROUBLESHOOT / "SKILL.md")
        normalized = " ".join(skill.split())

        self.assertIn(
            "send the proven causal handoff to `sdlc-classify-failure`",
            normalized,
        )
        self.assertIn(
            "instead of calling general `design` or a design phase directly",
            normalized,
        )
        self.assertIn("set `next_recommended_skill`", normalized)
        self.assertIn("the SDLC coordinator then routes", normalized)

    def test_agentic_sdlc_diagnostic_mode_has_strict_handoff_boundary(self) -> None:
        skill = self.read(TROUBLESHOOT / "SKILL.md")
        readme = self.read(TROUBLESHOOT / "README.md")
        normalized = " ".join(skill.split())
        normalized_readme = " ".join(readme.split())

        self.assertIn("## Agentic SDLC Diagnostic Mode", skill)
        self.assertIn(
            "conditional diagnostic branch, not a mandatory workflow phase",
            normalized,
        )
        self.assertIn(
            "Temporary diagnostic instrumentation must be explicitly scoped, "
            "reversible, uncommitted, removed before handoff",
            normalized,
        )
        self.assertIn("Do not commit product fixes", normalized)
        self.assertIn("Return one `diagnosis-v1`", normalized)
        self.assertIn(
            '"No implementation bug found" is missing or unresolved evidence',
            normalized,
        )
        self.assertIn(
            "stop at the causal handoff. Emit `diagnosis-v1`",
            normalized,
        )
        for field in (
            "expected and observed behavior",
            "stable blocker and exact regression oracle",
            "earliest divergent component",
            "violated invariant and causal chain",
            "affected files and bounded repair target",
            "counterfactual",
            "alternatives eliminated",
            "required regression test",
            "evidence references",
            "constraints to preserve",
        ):
            self.assertIn(field, normalized)
        self.assertIn("absent from the happy path", normalized_readme)
        self.assertIn(
            "It does not commit a product fix or call design, planning, or "
            "implementation",
            normalized_readme,
        )

    def test_remediation_attempts_are_bound_to_one_blocker(self) -> None:
        skill = " ".join(self.read(TROUBLESHOOT / "SKILL.md").split())
        reference = " ".join(
            self.read(TROUBLESHOOT / "references" / "remediation-budget.md").split()
        )
        hook_readme = " ".join(
            self.read(TROUBLESHOOT / "assets" / "hooks" / "README.md").split()
        )
        process_cases = " ".join(
            self.read(TROUBLESHOOT / "evals" / "process-cases.md").split()
        )

        self.assertIn(
            "Bind every completed attempt to the exact top-level marker `blocker_key`",
            skill,
        )
        self.assertIn(
            "only after that remediation executes and verification completes does "
            "it become attempt 1",
            skill,
        )
        self.assertIn(
            "Every canonical attempt's `blocker_key` must exactly match the marker's "
            "top-level `blocker_key`",
            reference,
        )
        self.assertIn(
            "Do not write a planned or in-progress attempt object",
            reference,
        )
        self.assertIn(
            "a ledger copied onto a causally independent blocker enters marker "
            "repair rather than exhaustion",
            hook_readme,
        )
        self.assertIn(
            "the denial lists all missing canonical fields together",
            hook_readme,
        )
        self.assertIn(
            "Expect the hook to reject that mixed state as invalid and request "
            "marker repair",
            process_cases,
        )

    def test_remediation_limits_and_terminal_report_have_one_canonical_path(
        self,
    ) -> None:
        skill = " ".join(self.read(TROUBLESHOOT / "SKILL.md").split())
        reference = " ".join(
            self.read(TROUBLESHOOT / "references" / "remediation-budget.md").split()
        )
        hook_readme = " ".join(
            self.read(TROUBLESHOOT / "assets" / "hooks" / "README.md").split()
        )

        self.assertIn(
            "$troubleshoot --attempt-limit=N --time-limit-minutes=N <problem>", skill
        )
        self.assertIn("hard maxima of 10 attempts and 180 minutes", skill)
        self.assertIn("return that report verbatim", skill)
        self.assertIn("A bare `$troubleshoot` keeps it", reference)
        self.assertIn("One flag changes only that field", reference)
        self.assertIn("explicit reset to 5/120", reference)
        self.assertIn("records same-blocker continuation only", reference)
        self.assertIn("return it verbatim as the whole assistant response", reference)
        self.assertIn(
            "A lower workflow stop leaves `status: active` and `stop_trigger: null`",
            reference,
        )
        self.assertIn("hard 10/180 maxima", hook_readme)
        self.assertIn("terminal lock", hook_readme)
        self.assertIn("Deleting `current.md` remains fail-closed", hook_readme)
        self.assertIn(
            "After exhaustion, the next user instruction is required whether",
            reference,
        )

    def test_remediation_hook_registers_prompt_tool_and_stop_boundaries(
        self,
    ) -> None:
        hooks = json.loads(self.read(TROUBLESHOOT / "assets" / "hooks.json.template"))
        remediation_events = [
            event
            for event, groups in hooks["hooks"].items()
            for group in groups
            for hook in group["hooks"]
            if "remediation_attempt_guard.py" in hook["command"]
        ]
        self.assertEqual(len(remediation_events), 3)
        self.assertEqual(
            set(remediation_events), {"UserPromptSubmit", "PreToolUse", "Stop"}
        )
        prompt_hook = hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual(prompt_hook["additionalContextLimit"], 800)
        self.assertEqual(prompt_hook["timeout"], 30)

    def test_live_product_validation_requires_owner_correct_clean_replay(
        self,
    ) -> None:
        skill = " ".join(self.read(TROUBLESHOOT / "SKILL.md").split())
        reference = " ".join(
            self.read(
                TROUBLESHOOT / "references" / "live-product-validation.md"
            ).split()
        )
        readme = " ".join(self.read(TROUBLESHOOT / "README.md").split())
        process_cases = " ".join(
            self.read(TROUBLESHOOT / "evals" / "process-cases.md").split()
        )

        self.assertIn(
            "whenever a live target is used to verify product behavior", skill
        )
        self.assertIn("Causal owner", reference)
        self.assertIn("Target state", reference)
        self.assertIn("Evidence lineage", reference)
        self.assertIn(
            "An unhealthy target caused by the product is still a product defect",
            reference,
        )
        self.assertIn("Product ownership is criterion-relative", reference)
        self.assertIn(
            "Any later change starts a new trial and a new evidence lineage",
            reference,
        )
        self.assertIn(
            "Observation is non-intervening only when it cannot alter "
            "criterion-relevant state or execution",
            reference,
        )
        self.assertIn(
            "Authorization to recover never makes the resulting evidence clean",
            reference,
        )
        self.assertIn(
            "A design change counts only after it is implemented and deployed",
            reference,
        )
        self.assertIn(
            "action-specific approval in every environment", reference
        )
        self.assertIn(
            "must precede the earliest product divergence or the first "
            "contaminated boundary, whichever came first",
            reference,
        )
        self.assertIn(
            "prove earlier writers, controllers, background jobs, and operators are "
            "quiescent",
            reference,
        )
        self.assertIn(
            "When the criterion is idempotent reconciliation", reference
        )
        self.assertIn(
            "successful exit, healthy final state, or idempotent no-op after manual "
            "pre-satisfaction does not prove the product fixed",
            reference,
        )
        self.assertIn(
            "Claim end-to-end workflow success only after an end-to-end run",
            reference,
        )
        self.assertIn("out-of-band mutation", readme)
        self.assertIn("directly pre-satisfy the desired state", process_cases)
        self.assertIn("amend the declared workflow", process_cases)
        self.assertIn(
            "advance its checkpoint after a partial product mutation",
            process_cases,
        )
        self.assertIn(
            "inspection endpoint that lazily initializes",
            process_cases,
        )
        self.assertIn("stale writer", process_cases)
        self.assertIn("harness-owned connectivity defect", process_cases)
        self.assertIn("cached telemetry", process_cases)

    def test_design_accepts_proven_handoff_without_reopening_diagnosis(self) -> None:
        skill = self.read(DESIGN / "SKILL.md")
        evals = self.read(DESIGN / "evals" / "trigger-prompts.md")
        normalized = " ".join(skill.split())
        normalized_evals = " ".join(evals.split())

        self.assertIn(
            "after `troubleshoot` has already proven the causal mechanism",
            normalized,
        )
        self.assertIn(
            "Do not diagnose an unknown or disputed failure mechanism",
            normalized,
        )
        self.assertIn(
            "treat the proven causal chain, violated invariant, and regression oracle "
            "as design inputs rather than reopening diagnosis",
            normalized,
        )
        self.assertIn(
            "Do not take over a complex or large repair that stays inside one "
            "existing private boundary",
            normalized,
        )
        self.assertIn(
            "implementation difficulty without a system-contract change must not "
            "trigger `design`",
            normalized_evals,
        )

    def test_metadata_docs_and_evals_preserve_the_boundary(self) -> None:
        metadata = self.read(TROUBLESHOOT / "agents" / "openai.yaml")
        readme = self.read(TROUBLESHOOT / "README.md")
        process_cases = self.read(TROUBLESHOOT / "evals" / "process-cases.md")
        trigger_prompts = self.read(TROUBLESHOOT / "evals" / "trigger-prompts.md")
        normalized_readme = " ".join(readme.split())
        normalized_prompts = " ".join(trigger_prompts.split())

        self.assertIn(
            "route system-contract-changing remediation through design only after "
            "causal proof",
            metadata,
        )
        self.assertIn("does not invoke `design`", normalized_readme)
        self.assertIn("Local Repair And Design-Scale Remediation", process_cases)
        self.assertIn("implementation difficulty alone", process_cases)
        self.assertIn(
            "A combined solve request stays in `troubleshoot` through `PROVEN`",
            normalized_prompts,
        )
        self.assertIn(
            "goes first to `sdlc-classify-failure`",
            normalized_readme,
        )
        self.assertIn(
            "coordinator then routes to the recorded",
            normalized_readme,
        )

    def test_observability_is_gated_and_reuses_one_readiness_state(self) -> None:
        skill = self.read(TROUBLESHOOT / "SKILL.md")
        reference = self.read(TROUBLESHOOT / "references" / "observability-evidence.md")
        normalized_skill = " ".join(skill.split())
        normalized_reference = " ".join(reference.split())

        self.assertIn(
            "observability result can distinguish a named hypothesis", normalized_skill
        )
        self.assertIn(
            "absolute bounded window before any Grafana call", normalized_skill
        )
        self.assertIn(
            "record non-Grafana evidence that it is expected to exist",
            normalized_skill,
        )
        self.assertIn("datasource discovery is not signal provenance", normalized_skill)
        self.assertIn("signal fit is unproven", normalized_skill)
        self.assertIn("$nebius-grafana-query", skill)
        self.assertIn("unknown | available | unavailable", skill)
        self.assertIn(
            "one connectivity/readiness check for this investigation", normalized_skill
        )
        self.assertIn("skip all later observability without retry", normalized_skill)
        self.assertIn(
            "it must not be used to fish for a relevant signal", normalized_skill
        )
        self.assertIn(
            "Treat the six-query fast allowance as a cumulative ceiling, not a target",
            normalized_skill,
        )
        self.assertIn(
            "Do not fan out to other telemetry families merely because the expected "
            "signal is absent",
            normalized_skill,
        )
        self.assertIn(
            "leaves at least two named hypotheses indistinguishable",
            normalized_skill,
        )
        self.assertIn(
            "next bounded hypothesis-specific query can change the decision",
            normalized_skill,
        )
        self.assertIn(
            "returned total, fast, and deep remaining query budgets",
            normalized_skill,
        )
        self.assertIn(
            "Do not invoke observability for deterministic syntax", normalized_reference
        )
        self.assertIn(
            "Repository metadata never grants production authority",
            normalized_reference,
        )
        self.assertIn("skipped: unproven_signal_fit", normalized_reference)
        self.assertIn(
            "Do not call `list_datasources` to discover whether any useful telemetry "
            "might exist",
            normalized_reference,
        )
        self.assertIn(
            "Pass the query-admission entry as the provider request's structured "
            "`signal_fit`",
            normalized_reference,
        )
        self.assertIn(
            "A new verification signal needs its own non-Grafana provenance",
            normalized_reference,
        )

    def test_observability_evals_require_zero_call_signal_fit_gate(self) -> None:
        process_cases = self.read(TROUBLESHOOT / "evals" / "process-cases.md")
        rubric = self.read(TROUBLESHOOT / "evals" / "process-rubric.md")
        prompts = self.read(TROUBLESHOOT / "evals" / "trigger-prompts.md")
        normalized_cases = " ".join(process_cases.split())
        normalized_rubric = " ".join(rubric.split())
        normalized_prompts = " ".join(prompts.split())

        self.assertIn(
            "no non-Grafana evidence that a matching telemetry signal exists",
            normalized_cases,
        )
        self.assertIn(
            "zero Grafana calls in fixtures one through three and six",
            normalized_cases,
        )
        self.assertIn(
            "readiness used for speculative signal discovery", normalized_cases
        )
        self.assertIn(
            "non-Grafana matching-signal provenance before readiness",
            normalized_rubric,
        )
        self.assertIn(
            "Grafana readiness must not be used to discover whether useful telemetry "
            "might exist",
            normalized_prompts,
        )

    def test_observability_preserves_causal_and_production_boundaries(self) -> None:
        skill = self.read(TROUBLESHOOT / "SKILL.md")
        reference = self.read(TROUBLESHOOT / "references" / "observability-evidence.md")
        normalized_skill = " ".join(skill.split())
        normalized_reference = " ".join(reference.split())

        self.assertIn(
            "Passive production telemetry remains read-only evidence", normalized_skill
        )
        self.assertIn("does not authorize remediation", normalized_skill)
        self.assertIn(
            "Correlation never becomes root cause by itself",
            " ".join(self.read(TROUBLESHOOT / "README.md").split()),
        )
        self.assertIn(
            "retains responsibility for interpretation and causal proof",
            normalized_reference,
        )
        self.assertIn("BLOCKED_MISSING_EVIDENCE", reference)
        self.assertIn("Never invoke the Grafana installer", normalized_reference)


if __name__ == "__main__":
    unittest.main()
