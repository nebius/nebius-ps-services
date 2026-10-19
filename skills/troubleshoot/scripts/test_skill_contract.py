#!/usr/bin/env python3
"""Cross-skill contract tests for troubleshooting boundaries."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TROUBLESHOOT = ROOT / "troubleshoot"
DESIGN = ROOT / "design"


class TroubleshootContractTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

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
        self.assertIn("if terminal is None", stop_arbiter)
        guard_source = self.read(
            TROUBLESHOOT / "assets" / "hooks" / "remediation_attempt_guard.py"
        )
        self.assertIn("codex/troubleshoot-report-obligation-v3", guard_source)
        self.assertIn('"sensitive_detected"', guard_source)
        self.assertNotIn("MAX_REPORT_CORRECTIONS", guard_source)
        for owner in ("maintain-project-specs", "prompt-session-intake", "sdlc-start"):
            candidate = ROOT / owner / "assets" / "hooks" / "stop_lifecycle_arbiter.py"
            if candidate.exists():
                with self.subTest(shared_arbiter_owner=owner):
                    self.assertEqual(stop_arbiter, self.read(candidate))
        prompt_hook = hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual(prompt_hook["additionalContextLimit"], 800)
        self.assertEqual(prompt_hook["timeout"], 30)

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
        evals = self.read(DESIGN / "evals" / "process-cases.md")
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

    def test_observability_evals_require_zero_call_signal_fit_gate(self) -> None:
        process_cases = self.read(TROUBLESHOOT / "evals" / "process-cases.md")
        rubric = self.read(TROUBLESHOOT / "evals" / "process-rubric.md")
        prompts = self.read(TROUBLESHOOT / "evals" / "trigger-prompts.csv")
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

    def test_core_routes_conditional_contracts_and_stays_compact(self):
        core = self.read(TROUBLESHOOT / "SKILL.md")
        self.assertLessEqual(len(core.splitlines()), 300)
        for name in (
            "workflow-handoffs",
            "lifecycle-contract",
            "report-delivery",
            "observability-evidence",
            "remediation-budget",
            "live-product-validation",
        ):
            self.assertIn(f"references/{name}.md", core)
            self.assertTrue((TROUBLESHOOT / "references" / f"{name}.md").is_file())
        for state in (
            "INTAKE",
            "DISCOVERY",
            "BASELINE",
            "MODEL",
            "HYPOTHESES",
            "EXPERIMENTS",
            "LOCALIZED",
            "PROVEN",
            "REMEDIATED",
            "VERIFIED",
            "REPORTED",
        ):
            self.assertIn(state, core)

    def test_adaptive_scope_and_evidence_invalidation_contract(self):
        protocol = " ".join(
            self.read(TROUBLESHOOT / "references/investigation-protocol.md")
            .lower()
            .split()
        )
        for invariant in (
            "immediate dependencies",
            "unexamined",
            "unknown",
            "contradict",
            "clock",
            "invalidate",
            "restart",
            "truncated",
            "three consecutive",
            "neither distinguish",
            "never reset",
            "independent blockers",
        ):
            self.assertIn(invariant, protocol)
        self.assertNotIn("exactly these eight rows", protocol)
        self.assertNotIn("three to seven", protocol)
        self.assertIn("no minimum", protocol)

    def test_report_classes_and_budget_interfaces_are_preserved(self):
        core = self.read(TROUBLESHOOT / "SKILL.md")
        report = self.read(TROUBLESHOOT / "references/report-delivery.md")
        for outcome in (
            "VERIFIED_FIXED",
            "DIAGNOSED-FIXED",
            "MITIGATED_NOT_PROVEN",
            "DIAGNOSED_NOT_FIXED",
            "BLOCKED_MISSING_EVIDENCE",
            "UNRESOLVED",
        ):
            self.assertIn(outcome, core)
            self.assertIn(outcome, report)
        for flag in ("--attempt-limit=N", "--time-limit-minutes=N"):
            self.assertIn(flag, core)
        lifecycle = self.read(TROUBLESHOOT / "references/lifecycle-contract.md")
        for field in (
            "blocker_key",
            "override_summary",
            "stop_trigger",
            "status: active",
        ):
            self.assertIn(field, lifecycle)
        self.assertIn("troubleshoot-report-obligation.json", report)
        self.assertIn("sensitive_detected", report)

    def test_sdlc_handoff_and_live_proof_stay_owned(self):
        handoff = " ".join(
            self.read(TROUBLESHOOT / "references/workflow-handoffs.md").split()
        )
        for field in (
            "diagnosis-v1",
            "sdlc-classify-failure",
            "next_recommended_skill",
            "Do not commit product fixes",
            "counterfactual",
            "constraints to preserve",
        ):
            self.assertIn(field, handoff)
        live = self.read(TROUBLESHOOT / "references/live-product-validation.md").lower()
        for concept in (
            "checkpoint",
            "quies",
            "independent",
            "interven",
            "authoritative",
        ):
            self.assertIn(concept, live)

    def test_domain_guides_are_decision_guides(self):
        for name in (
            "linux",
            "storage",
            "network",
            "code-debugging",
            "gpu",
            "kubernetes",
            "slurm",
            "soperator",
            "nebius",
        ):
            guide = self.read(TROUBLESHOOT / "references" / f"{name}.md")
            for heading in (
                "Competing explanations",
                "Cheapest distinguishing evidence",
                "Interpretation",
                "Next branch",
                "Escalation Conditions",
                "Verification",
            ):
                self.assertIn(heading, guide, name)

    def test_provider_gate_is_conditional_and_complete(self):
        core = self.read(TROUBLESHOOT / "SKILL.md")
        provider = self.read(TROUBLESHOOT / "references/observability-evidence.md")
        self.assertNotIn("six-query", core)
        for concept in (
            "signal_fit",
            "list_datasources",
            "unavailable",
            "selector",
            "window",
            "budget",
        ):
            self.assertIn(concept, provider)
        self.assertIn("Never invoke the Grafana installer", provider)

    def test_invocation_prompt_routes_to_adaptive_method(self):
        prompt = self.read(TROUBLESHOOT / "agents/openai.yaml")
        self.assertIn("$troubleshoot", prompt)
        self.assertIn("allow_implicit_invocation: true", prompt)
        self.assertNotIn("every component", prompt)
        self.assertIn("failing path", prompt)


if __name__ == "__main__":
    unittest.main()
