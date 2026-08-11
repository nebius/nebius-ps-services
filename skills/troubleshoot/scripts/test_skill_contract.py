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
        self.assertLess(skill.index("8. **PROVEN**"), handoff)
        self.assertLess(handoff, skill.index("9. **REMEDIATED**"))
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
            "including its new `blocker_key` and a public-safe `blocker_summary`",
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
            "Pending feedback reports a precise bounded missing-marker, "
            "invalid-marker, or invalid-transition reason",
            hook_readme,
        )
        self.assertIn(
            "A deleted resize marker remains fail-closed",
            hook_readme,
        )
        self.assertIn(
            "Expect the hook to reject that mixed state as invalid and request "
            "marker repair",
            process_cases,
        )
        self.assertIn(
            "UserPromptSubmit, PreToolUse, and Stop to report the exact bounded "
            "validation or transition reason",
            process_cases,
        )
        self.assertIn(
            "bounded sidecar metadata cannot reconstruct the prior marker",
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
        self.assertIn("prior terminal marker", hook_readme)
        self.assertIn("Deleting `current.md` remains fail-closed", hook_readme)
        self.assertIn(
            "After exhaustion, the next user instruction is required whether",
            reference,
        )

    def test_every_explicit_invocation_has_a_terminal_report_obligation(
        self,
    ) -> None:
        surfaces = {
            "SKILL.md": self.read(TROUBLESHOOT / "SKILL.md"),
            "README.md": self.read(TROUBLESHOOT / "README.md"),
            "hook README": self.read(TROUBLESHOOT / "assets" / "hooks" / "README.md"),
            "reporting reference": self.read(
                TROUBLESHOOT / "references" / "verification-and-reporting.md"
            ),
            "budget reference": self.read(
                TROUBLESHOOT / "references" / "remediation-budget.md"
            ),
            "process cases": self.read(TROUBLESHOOT / "evals" / "process-cases.md"),
        }
        for name, value in surfaces.items():
            with self.subTest(surface=name):
                normalized = " ".join(value.split()).casefold()
                self.assertIn("every explicit `$troubleshoot` invocation", normalized)
                self.assertIn("troubleshoot-report-obligation.json", normalized)
                self.assertIn("current workflow state: reported", normalized)
                self.assertIn("bounded ui fallback", normalized)
                self.assertIn("same session", normalized)

    def test_remediation_hook_registers_prompt_tool_and_arbitrated_stop_boundaries(
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
        self.assertEqual(len(remediation_events), 2)
        self.assertEqual(set(remediation_events), {"UserPromptSubmit", "PreToolUse"})
        stop_hook = hooks["hooks"]["Stop"][0]["hooks"][0]
        self.assertIn("stop_lifecycle_arbiter.py", stop_hook["command"])
        stop_arbiter = self.read(
            TROUBLESHOOT / "assets" / "hooks" / "stop_lifecycle_arbiter.py"
        )
        self.assertIn('"remediation_attempt_guard.py"', stop_arbiter)
        for owner in ("maintain-project-specs", "prompt-session-intake", "sdlc-start"):
            candidate = ROOT / owner / "assets" / "hooks" / "stop_lifecycle_arbiter.py"
            if candidate.exists():
                with self.subTest(shared_arbiter_owner=owner):
                    self.assertEqual(stop_arbiter, self.read(candidate))
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
        self.assertIn("action-specific approval in every environment", reference)
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
        self.assertIn("When the criterion is idempotent reconciliation", reference)
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

    def test_stack_discovery_component_logs_and_completion_are_mandatory(
        self,
    ) -> None:
        skill = " ".join(self.read(TROUBLESHOOT / "SKILL.md").split())
        protocol = " ".join(
            self.read(TROUBLESHOOT / "references" / "investigation-protocol.md").split()
        )
        reporting = " ".join(
            self.read(
                TROUBLESHOOT / "references" / "verification-and-reporting.md"
            ).split()
        )

        self.assertIn("INTAKE -> DISCOVERY -> BASELINE", skill)
        self.assertIn("technologies, exact versions, deployment model", skill)
        self.assertIn("official vendor architecture and configuration", skill)
        self.assertIn("component verification matrix", skill)
        self.assertIn("service manager, OS and kernel, network and firewall", skill)
        self.assertIn("expected supporting and falsifying evidence", skill)
        self.assertIn("indefinite `tail -f`", skill)
        self.assertIn("## Stack And Architecture Inventory", protocol)
        self.assertIn("## Layered Log-Coverage Ledger", protocol)
        self.assertIn("Timeout or output bound", protocol)
        self.assertIn("restart history and recent changes", protocol.casefold())
        self.assertIn("authentication, and DNS", protocol)
        self.assertIn("exactly these eight rows", protocol)
        self.assertIn("not subject to Grafana provider-admission gates", protocol)
        for heading in (
            "## Architecture Verdict",
            "## Component Verification Matrix",
            "## Incident Timeline",
            "## Logs Examined",
            "## Code Debugging",
            "## Completion Gate",
            "## Remaining Unknowns And Residual Risks",
        ):
            self.assertIn(heading, reporting)
        for field in (
            "- Included system boundary:",
            "- Excluded system boundary:",
            "- Exercised control and data paths:",
            "- Incident-window start:",
            "- Incident-window end:",
        ):
            self.assertIn(field, reporting)
        for layer in (
            "Component",
            "Application or job",
            "Container or orchestrator",
            "Service manager",
            "OS and kernel",
            "Network and firewall",
            "Storage",
            "GPU or hardware",
        ):
            self.assertIn(f"| {layer} |", reporting)
        self.assertIn("each of the eight canonical log layers exactly once", reporting)
        self.assertIn("Dependencies, authentication, and DNS", reporting)
        self.assertIn("Restart history and recent changes", reporting)
        for criterion in (
            "Design",
            "Infrastructure",
            "Connectivity",
            "Configuration",
            "Runtime health",
            "Logs",
            "Relevant code paths",
        ):
            self.assertIn(f"| {criterion} | PASS / FAIL / UNKNOWN |", reporting)
        self.assertIn("`VERIFIED_FIXED` is invalid unless all seven", reporting)

    def test_technology_playbooks_are_present_and_vendor_anchored(self) -> None:
        expected = {
            "slurm.md": ("SlurmctldLogFile", "SlurmdLogFile", "slurmdbd", "MUNGE"),
            "soperator.md": ("ActiveCheck", "CronJob", "slurm_jobs/", "task_prolog/"),
            "kubernetes.md": ("previous container", "kubelet", "CNI", "CSI"),
            "nebius.md": ("Managed Kubernetes", "VPC", "quota", "IAM"),
            "linux.md": ("systemd", "kernel", "cgroup", "clock sync"),
            "network.md": ("DNS", "firewall", "Packet capture", "InfiniBand"),
            "storage.md": ("CSI", "filesystem", "object", "corruption"),
            "gpu.md": ("Xid", "DCGM", "NCCL", "RDMA"),
            "code-debugging.md": (
                "stack traces",
                "core dumps",
                "focused",
                "instrumentation",
            ),
        }
        for name, terms in expected.items():
            with self.subTest(playbook=name):
                value = self.read(TROUBLESHOOT / "references" / name)
                self.assertIn("## Official Sources", value)
                for term in terms:
                    self.assertIn(term, value)

    def test_soperator_distinguishes_dedicated_and_workload_coupled_checks(
        self,
    ) -> None:
        value = " ".join(
            self.read(TROUBLESHOOT / "references" / "soperator.md").split()
        )
        self.assertIn("ActiveChecks are dedicated diagnostic jobs", value)
        self.assertIn("complete or exclusive GPU allocation", value)
        self.assertIn(
            "Do not describe them as safely running inside a customer's existing "
            "training allocation",
            value,
        )
        self.assertIn("workload-coupled or passive Soperator checks", value)
        self.assertIn("prolog, epilog, task hooks, or `HealthCheckProgram`", value)
        self.assertIn("collector shipping and centralized retention", value)

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

        self.assertIn('default_prompt: "$troubleshoot ', metadata)
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
