#!/usr/bin/env python3
"""Contract tests for embedded observability evidence mode."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
QUERY = ROOT / "nebius-grafana-query"
TROUBLESHOOT = ROOT / "troubleshoot"
TRACE_FIXTURE = QUERY / "evals" / "evidence-provider-tool-traces.json"
READ_ONLY_TRACE_TOOLS = {
    "list_datasources",
    "query_loki_stats",
    "query_prometheus",
    "query_prometheus_1",
    "query_tempo",
    "query_tempo_1",
    "query_version_dimension_1",
}


class EvidenceProviderContractTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_provider_has_one_lazy_readiness_check_per_workflow(self) -> None:
        provider = self.read(QUERY / "references" / "evidence-provider.md")
        normalized = " ".join(provider.split())

        self.assertIn("unknown | available | unavailable", provider)
        self.assertIn("one bounded `list_datasources` call", normalized)
        self.assertIn(
            "single call is the connectivity, endpoint, authentication, MCP "
            "readiness, and datasource-discovery check",
            normalized,
        )
        self.assertIn("Do not query data, retry readiness", normalized)
        self.assertIn("run a separate DNS or internet probe", normalized)
        self.assertIn("invoke the installer", normalized)
        self.assertIn("recheck later in that run", normalized)
        self.assertIn("timeout, `429`, or backend `5xx` failure", normalized)
        self.assertIn(
            "without fallback-path switching, decomposition, or retry",
            normalized,
        )

    def test_provider_is_decision_gated_and_bounded(self) -> None:
        provider = self.read(QUERY / "references" / "evidence-provider.md")
        normalized = " ".join(provider.split())

        self.assertIn(
            "Zero queries is the correct outcome when runtime evidence cannot "
            "change the current decision",
            normalized,
        )
        self.assertIn(
            "remaining fast-stage budget, initially at most six data queries",
            normalized,
        )
        self.assertIn(
            "remaining deep-stage budget, initially at most four additional "
            "data queries",
            normalized,
        )
        self.assertIn(
            "left at least two named decision-relevant alternatives indistinguishable",
            normalized,
        )
        self.assertIn(
            "next bounded query must be able to change the caller's decision",
            normalized,
        )
        self.assertIn("selected stage has no remaining budget", normalized)
        self.assertIn(
            "Do not issue a fixed metrics-plus-logs-plus-traces bundle", normalized
        )
        self.assertIn(
            "For either embedded consumer, attempt at most one pre-admitted data "
            "query from one signal family per provider invocation",
            normalized,
        )
        self.assertIn(
            "Do not batch independent criteria, hypotheses, or signal families "
            "merely because budget remains",
            normalized,
        )
        self.assertIn('"signal_fit": {', provider)
        self.assertIn('"criterion_fit": {', provider)
        self.assertIn('"signals": ["<one-signal-family>"]', provider)
        self.assertNotIn("<ordered-signal-family>", provider)
        self.assertIn(
            "`signal_fit` is required for both embedded consumers",
            normalized,
        )
        self.assertIn(
            "`criterion_fit` is also required for `consumer: sdlc-evaluate`",
            normalized,
        )
        self.assertIn(
            "for `consumer: sdlc-evaluate`, the baseline/control window is absent "
            "or not absolute and bounded",
            normalized,
        )
        self.assertIn(
            "do not place raw telemetry, secret values, private endpoints, "
            "customer payloads, or raw query strings in the request",
            normalized,
        )
        self.assertIn(
            "`signals` does not contain exactly one family, `signal_fit` is absent "
            "or malformed",
            normalized,
        )
        self.assertIn(
            "use `irrelevant_evidence` for an invalid `signal_fit` or `criterion_fit`",
            normalized,
        )
        self.assertIn("at most five affected dimensions", normalized)
        self.assertIn("three sanitized representative events", normalized)

    def test_provider_returns_facts_without_diagnosis_or_grading(self) -> None:
        provider = self.read(QUERY / "references" / "evidence-provider.md")
        normalized = " ".join(provider.split())

        for field in (
            '"status": "complete | partial | unavailable | rejected"',
            '"rejection_reason": null',
            '"workflow_state": {',
            '"connectivity": "unknown | available | unavailable"',
            '"remaining_query_budget": 0',
            '"remaining_fast_query_budget": 0',
            '"remaining_deep_query_budget": 0',
            '"scope": {}',
            '"time_windows": {}',
            '"anomalies": []',
            '"error_fingerprints": []',
            '"affected_dimensions": []',
            '"recent_changes": []',
            '"correlations": []',
            '"representative_events": []',
            '"data_gaps": []',
            '"connectivity_checks": 0',
            '"queries": 0',
            '"series_returned": 0',
            '"rows_returned": 0',
        ):
            self.assertIn(field, provider)

        self.assertIn("must never claim a root cause", normalized)
        self.assertIn("return an acceptance grade", normalized)
        self.assertIn("never causation", normalized)
        self.assertIn("untrusted data", normalized)
        self.assertIn("each invocation is auditable", normalized)
        self.assertIn("without retaining raw query strings", normalized)

    def test_structured_tool_traces_enforce_state_and_budgets(self) -> None:
        fixture = json.loads(self.read(TRACE_FIXTURE))
        failure_results = {
            "authorization_denied",
            "timeout",
            "rate_limited",
            "backend_error",
        }
        rejection_reasons = {
            "missing_authority",
            "unresolved_selector",
            "invalid_window",
            "irrelevant_evidence",
            "invalid_budget",
        }
        required_output_keys = {
            "status",
            "rejection_reason",
            "workflow_state",
            "scope",
            "time_windows",
            "anomalies",
            "error_fingerprints",
            "affected_dimensions",
            "recent_changes",
            "correlations",
            "representative_events",
            "data_gaps",
            "query_cost",
        }
        required_list_fields = {
            "anomalies",
            "error_fingerprints",
            "affected_dimensions",
            "recent_changes",
            "correlations",
            "representative_events",
            "data_gaps",
        }

        for workflow in fixture["workflows"]:
            with self.subTest(workflow=workflow["name"]):
                connectivity = workflow["initial_connectivity"]
                remaining_budget = workflow["initial_query_budget"]
                remaining_fast_budget = workflow["initial_fast_query_budget"]
                remaining_deep_budget = workflow["initial_deep_query_budget"]
                readiness_checks = 0

                for request in workflow["requests"]:
                    calls = request["calls"]
                    if not request["provider_invoked"]:
                        self.assertEqual([], calls)
                        continue

                    consumer = request["consumer"]
                    signals = request["signals"]
                    self.assertIn(consumer, {"troubleshoot", "sdlc-evaluate"})
                    self.assertIsInstance(signals, list)
                    self.assertTrue(signals)
                    self.assertTrue(request["evidence_question"])
                    self.assertIsInstance(request["decision_ids"], list)
                    self.assertTrue(request["decision_ids"])
                    self.assertTrue(request["selectors"])
                    self.assertEqual(
                        {"start", "end"},
                        set(request["candidate_window"]),
                    )
                    self.assertTrue(request["candidate_window"]["start"])
                    self.assertTrue(request["candidate_window"]["end"])
                    output = request["output"]
                    missing_authority = (
                        output["rejection_reason"] == "missing_authority"
                    )
                    if missing_authority:
                        self.assertNotIn("authority_scope", request)
                        self.assertNotIn("authority_provenance", request)
                    else:
                        self.assertTrue(request["authority_scope"])
                        self.assertTrue(request["authority_provenance"])
                    self.assertEqual(connectivity, request["connectivity_state"])
                    self.assertEqual(
                        remaining_budget, request["remaining_query_budget"]
                    )
                    self.assertEqual(
                        remaining_fast_budget,
                        request["remaining_fast_query_budget"],
                    )
                    self.assertEqual(
                        remaining_deep_budget,
                        request["remaining_deep_query_budget"],
                    )
                    for stale_name in (
                        "input_connectivity",
                        "input_query_budget",
                        "input_fast_query_budget",
                        "input_deep_query_budget",
                    ):
                        self.assertNotIn(stale_name, request)
                    self.assertEqual(required_output_keys, set(output))
                    self.assertIsInstance(output["scope"], dict)
                    self.assertEqual(consumer, output["scope"]["consumer"])
                    signal_fit = request.get("signal_fit")
                    if signal_fit is not None:
                        self.assertEqual(1, len(signals))
                        self.assertEqual(
                            {
                                "family",
                                "candidate",
                                "provenance_kind",
                                "decision_relation",
                            },
                            set(signal_fit),
                        )
                        self.assertEqual(signals[0], signal_fit["family"])
                        self.assertTrue(signal_fit["candidate"])
                        self.assertIn(
                            signal_fit["provenance_kind"],
                            {
                                "user-signal",
                                "instrumentation-pipeline",
                                "dashboard-rule",
                                "catalog-runbook",
                                "grafana-backed-feed",
                            },
                        )
                        self.assertTrue(signal_fit["decision_relation"])
                    else:
                        self.assertEqual("rejected", output["status"])
                        self.assertEqual(
                            "irrelevant_evidence",
                            output["rejection_reason"],
                        )
                        self.assertEqual([], calls)
                    criterion_fit = request.get("criterion_fit")
                    if consumer == "sdlc-evaluate" and criterion_fit is not None:
                        self.assertEqual(
                            {
                                "criterion_id",
                                "signal_provenance_ref",
                                "measurement",
                                "threshold",
                                "candidate_attribution",
                                "baseline_or_control_attribution",
                                "required_coverage",
                                "pass_condition",
                                "fail_condition",
                                "inconclusive_conditions",
                                "grade_relation",
                            },
                            set(criterion_fit),
                        )
                        for field in (
                            "criterion_id",
                            "signal_provenance_ref",
                            "measurement",
                            "threshold",
                            "candidate_attribution",
                            "baseline_or_control_attribution",
                            "required_coverage",
                            "pass_condition",
                            "fail_condition",
                            "grade_relation",
                        ):
                            self.assertTrue(criterion_fit[field])
                        self.assertIsInstance(
                            criterion_fit["inconclusive_conditions"],
                            list,
                        )
                        self.assertTrue(criterion_fit["inconclusive_conditions"])
                        self.assertEqual(
                            [criterion_fit["criterion_id"]],
                            request["decision_ids"],
                        )
                        if output["status"] != "rejected":
                            self.assertTrue(request["evidence_question"])
                            self.assertTrue(request["authority_scope"])
                            self.assertEqual(
                                "recorded-sdlc-requirement",
                                request["authority_provenance"],
                            )
                            self.assertTrue(request["selectors"])
                            for window_name in (
                                "candidate_window",
                                "baseline_window",
                            ):
                                window = request[window_name]
                                self.assertEqual({"start", "end"}, set(window))
                                self.assertTrue(window["start"])
                                self.assertTrue(window["end"])
                    elif consumer == "sdlc-evaluate":
                        self.assertEqual("rejected", output["status"])
                        self.assertEqual(
                            "irrelevant_evidence",
                            output["rejection_reason"],
                        )
                        self.assertEqual([], calls)
                    else:
                        self.assertNotIn("criterion_fit", request)
                    self.assertIsInstance(output["time_windows"], dict)
                    for field in required_list_fields:
                        self.assertIsInstance(output[field], list)
                    workflow_state = output["workflow_state"]
                    self.assertEqual(
                        {
                            "connectivity",
                            "remaining_query_budget",
                            "remaining_fast_query_budget",
                            "remaining_deep_query_budget",
                        },
                        set(workflow_state),
                    )
                    query_cost = output["query_cost"]
                    self.assertEqual(
                        {
                            "connectivity_checks",
                            "queries",
                            "series_returned",
                            "rows_returned",
                        },
                        set(query_cost),
                    )
                    readiness = [
                        call for call in calls if call["tool"] == "list_datasources"
                    ]
                    data_calls = [
                        call for call in calls if call["tool"] != "list_datasources"
                    ]
                    for call in calls:
                        self.assertIn(call["tool"], READ_ONLY_TRACE_TOOLS)

                    if output["status"] == "rejected":
                        self.assertEqual([], calls)
                        self.assertIn(output["rejection_reason"], rejection_reasons)
                        self.assertEqual(connectivity, workflow_state["connectivity"])
                        self.assertEqual(
                            remaining_budget,
                            workflow_state["remaining_query_budget"],
                        )
                        self.assertEqual(
                            remaining_fast_budget,
                            workflow_state["remaining_fast_query_budget"],
                        )
                        self.assertEqual(
                            remaining_deep_budget,
                            workflow_state["remaining_deep_query_budget"],
                        )
                    else:
                        self.assertIsNone(output["rejection_reason"])
                        if connectivity == "unavailable":
                            self.assertEqual([], calls)
                            self.assertEqual("unavailable", output["status"])
                            self.assertEqual(
                                "unavailable",
                                workflow_state["connectivity"],
                            )
                        elif connectivity == "unknown":
                            self.assertEqual(1, len(readiness))
                        else:
                            self.assertEqual(0, len(readiness))

                    readiness_checks += len(readiness)
                    stage_budget = (
                        remaining_fast_budget
                        if request["stage"] == "fast"
                        else remaining_deep_budget
                    )
                    self.assertLessEqual(len(data_calls), stage_budget)
                    self.assertLessEqual(len(data_calls), remaining_budget)
                    self.assertLessEqual(
                        len(data_calls),
                        1,
                        "embedded consumers may attempt only one data query per "
                        "provider invocation",
                    )
                    self.assertEqual(
                        remaining_budget - len(data_calls),
                        workflow_state["remaining_query_budget"],
                    )
                    expected_fast_budget = remaining_fast_budget
                    expected_deep_budget = remaining_deep_budget
                    if request["stage"] == "fast":
                        expected_fast_budget -= len(data_calls)
                    else:
                        expected_deep_budget -= len(data_calls)
                    self.assertEqual(
                        expected_fast_budget,
                        workflow_state["remaining_fast_query_budget"],
                    )
                    self.assertEqual(
                        expected_deep_budget,
                        workflow_state["remaining_deep_query_budget"],
                    )
                    self.assertEqual(
                        workflow_state["remaining_query_budget"],
                        workflow_state["remaining_fast_query_budget"]
                        + workflow_state["remaining_deep_query_budget"],
                    )
                    self.assertGreaterEqual(
                        workflow_state["remaining_query_budget"],
                        0,
                    )
                    self.assertGreaterEqual(
                        workflow_state["remaining_fast_query_budget"],
                        0,
                    )
                    self.assertGreaterEqual(
                        workflow_state["remaining_deep_query_budget"],
                        0,
                    )
                    self.assertEqual(len(readiness), query_cost["connectivity_checks"])
                    self.assertEqual(len(data_calls), query_cost["queries"])
                    for call in data_calls:
                        self.assertIn("series_returned", call)
                        self.assertIn("rows_returned", call)
                        self.assertGreaterEqual(call["series_returned"], 0)
                        self.assertGreaterEqual(call["rows_returned"], 0)
                    self.assertEqual(
                        sum(call["series_returned"] for call in data_calls),
                        query_cost["series_returned"],
                    )
                    self.assertEqual(
                        sum(call["rows_returned"] for call in data_calls),
                        query_cost["rows_returned"],
                    )

                    failures = [
                        index
                        for index, call in enumerate(calls)
                        if call["result"] in failure_results
                    ]
                    if failures:
                        self.assertEqual(len(calls) - 1, failures[0])
                        self.assertEqual("unavailable", output["status"])
                        self.assertEqual("unavailable", workflow_state["connectivity"])
                    if readiness and readiness[0]["result"] in failure_results:
                        self.assertEqual(readiness, calls)
                    if connectivity != "unavailable" and not failures:
                        expected_connectivity = (
                            "available"
                            if connectivity == "unknown"
                            and output["status"] != "rejected"
                            else connectivity
                        )
                        self.assertEqual(
                            expected_connectivity,
                            workflow_state["connectivity"],
                        )

                    connectivity = workflow_state["connectivity"]
                    remaining_budget = workflow_state["remaining_query_budget"]
                    remaining_fast_budget = workflow_state[
                        "remaining_fast_query_budget"
                    ]
                    remaining_deep_budget = workflow_state[
                        "remaining_deep_query_budget"
                    ]

                self.assertLessEqual(readiness_checks, 1)

    def test_unproven_signal_fit_has_structured_zero_call_traces(self) -> None:
        fixture = json.loads(self.read(TRACE_FIXTURE))
        workflows = {workflow["name"]: workflow for workflow in fixture["workflows"]}
        expected = {
            "unproven_signal_fit_skips_provider": "unproven_signal_fit",
            "unproven_verification_signal_fit_skips_provider": (
                "unproven_verification_signal_fit"
            ),
        }

        for name, reason in expected.items():
            with self.subTest(workflow=name):
                request = workflows[name]["requests"][0]
                self.assertFalse(request["provider_invoked"])
                self.assertEqual("troubleshoot", request["consumer"])
                self.assertEqual(["metrics"], request["signals"])
                self.assertNotIn("signal_fit", request)
                self.assertEqual(reason, request["eligibility"])
                self.assertEqual([], request["calls"])

    def test_malformed_signal_fit_is_rejected_before_readiness(self) -> None:
        fixture = json.loads(self.read(TRACE_FIXTURE))
        workflows = {workflow["name"]: workflow for workflow in fixture["workflows"]}
        workflow = workflows["malformed_signal_fit_rejected_before_readiness"]
        request = workflow["requests"][0]

        self.assertTrue(request["provider_invoked"])
        self.assertEqual("troubleshoot", request["consumer"])
        self.assertEqual(["metrics"], request["signals"])
        self.assertNotIn("signal_fit", request)
        self.assertEqual([], request["calls"])
        self.assertEqual("rejected", request["output"]["status"])
        self.assertEqual(
            "irrelevant_evidence",
            request["output"]["rejection_reason"],
        )
        self.assertEqual(
            {
                "connectivity": workflow["initial_connectivity"],
                "remaining_query_budget": workflow["initial_query_budget"],
                "remaining_fast_query_budget": workflow["initial_fast_query_budget"],
                "remaining_deep_query_budget": workflow["initial_deep_query_budget"],
            },
            request["output"]["workflow_state"],
        )

    def test_malformed_evaluation_fit_is_rejected_before_readiness(self) -> None:
        fixture = json.loads(self.read(TRACE_FIXTURE))
        workflows = {workflow["name"]: workflow for workflow in fixture["workflows"]}
        workflow = workflows["malformed_criterion_fit_rejected_before_readiness"]
        request = workflow["requests"][0]

        self.assertTrue(request["provider_invoked"])
        self.assertEqual("sdlc-evaluate", request["consumer"])
        self.assertEqual(["metrics"], request["signals"])
        self.assertIn("signal_fit", request)
        self.assertNotIn("criterion_fit", request)
        self.assertEqual([], request["calls"])
        self.assertEqual("rejected", request["output"]["status"])
        self.assertEqual(
            "irrelevant_evidence",
            request["output"]["rejection_reason"],
        )
        self.assertEqual(
            {
                "connectivity": workflow["initial_connectivity"],
                "remaining_query_budget": workflow["initial_query_budget"],
                "remaining_fast_query_budget": workflow["initial_fast_query_budget"],
                "remaining_deep_query_budget": workflow["initial_deep_query_budget"],
            },
            request["output"]["workflow_state"],
        )

    def test_incomplete_evaluation_admission_skips_provider(self) -> None:
        fixture = json.loads(self.read(TRACE_FIXTURE))
        workflows = {workflow["name"]: workflow for workflow in fixture["workflows"]}
        request = workflows["incomplete_evaluation_admission_skips_provider"][
            "requests"
        ][0]

        self.assertFalse(request["provider_invoked"])
        self.assertEqual("sdlc-evaluate", request["consumer"])
        self.assertEqual("incomplete_criterion_fit", request["eligibility"])
        self.assertEqual([], request["calls"])

    def test_missing_evaluation_baseline_is_rejected_before_readiness(self) -> None:
        fixture = json.loads(self.read(TRACE_FIXTURE))
        workflows = {workflow["name"]: workflow for workflow in fixture["workflows"]}
        workflow = workflows["missing_evaluation_baseline_rejected_before_readiness"]
        request = workflow["requests"][0]

        self.assertTrue(request["provider_invoked"])
        self.assertNotIn("baseline_window", request)
        self.assertEqual([], request["calls"])
        self.assertEqual("rejected", request["output"]["status"])
        self.assertEqual("invalid_window", request["output"]["rejection_reason"])
        self.assertEqual(
            {
                "connectivity": workflow["initial_connectivity"],
                "remaining_query_budget": workflow["initial_query_budget"],
                "remaining_fast_query_budget": workflow["initial_fast_query_budget"],
                "remaining_deep_query_budget": workflow["initial_deep_query_budget"],
            },
            request["output"]["workflow_state"],
        )

    def test_trace_tool_allowlist_rejects_mutating_calls(self) -> None:
        for tool in (
            "create_dashboard",
            "update_alert",
            "delete_annotation",
            "api_post",
            "datasource_proxy_post",
        ):
            with self.subTest(tool=tool):
                self.assertNotIn(tool, READ_ONLY_TRACE_TOOLS)

    def test_mocked_events_are_redacted_structured_data(self) -> None:
        fixture = json.loads(self.read(TRACE_FIXTURE))
        events = [
            event
            for workflow in fixture["workflows"]
            for request in workflow["requests"]
            for event in (request.get("output") or {}).get("representative_events", [])
        ]

        self.assertTrue(events)
        self.assertTrue(all(event["message"] == "[REDACTED]" for event in events))

    def test_untrusted_values_are_absent_from_every_output_field(self) -> None:
        fixture = json.loads(self.read(TRACE_FIXTURE))
        requests = [
            request
            for workflow in fixture["workflows"]
            for request in workflow["requests"]
            if "mock_untrusted_input" in request
        ]

        self.assertTrue(requests)
        for request in requests:
            serialized_output = json.dumps(
                request["output"],
                sort_keys=True,
            ).lower()
            for raw_value in request["mock_untrusted_input"].values():
                self.assertNotIn(raw_value.lower(), serialized_output)
            for forbidden in (
                "bearer ",
                "<untrusted_token>",
                "<private_endpoint>",
                "<customer_data>",
                "ignore prior instructions",
                "invoke a write tool",
            ):
                self.assertNotIn(forbidden, serialized_output)
            self.assertEqual(
                "[REDACTED]",
                request["output"]["scope"]["attribution"],
            )
            self.assertIn("[redacted]", serialized_output)

    def test_authority_is_explicit_and_metadata_only_narrows(self) -> None:
        provider = self.read(QUERY / "references" / "evidence-provider.md")
        normalized = " ".join(provider.split())

        self.assertIn("user-or-recorded-sdlc-requirement", provider)
        self.assertIn("may narrow an already-authorized", normalized)
        self.assertIn("They do not grant authority", normalized)
        self.assertIn("never default to all production", normalized)

    def test_direct_report_and_embedded_envelope_are_separate(self) -> None:
        skill = self.read(QUERY / "SKILL.md")
        evals = self.read(QUERY / "evals" / "process-cases.md")
        normalized = " ".join(skill.split())
        normalized_evals = " ".join(evals.split())

        self.assertIn("Direct report mode", skill)
        self.assertIn("Evidence-provider mode", skill)
        self.assertIn("references/evidence-provider.md", skill)
        self.assertIn(
            "Direct report mode retains the existing installer handoff",
            normalized,
        )
        self.assertIn(
            "Evidence-provider mode returns `unavailable` to the caller",
            normalized,
        )
        self.assertIn(
            "Evidence-provider readiness or later "
            "transport/authentication/endpoint failure",
            normalized,
        )
        self.assertIn("including `401`/`403`", normalized)
        self.assertIn("Direct report mode authorization denied", normalized)
        self.assertIn("Direct report mode timeout", normalized)
        self.assertIn(
            "In direct report mode, Grafana tools are missing or authentication fails",
            normalized_evals,
        )
        self.assertIn(
            "Evidence-provider mode instead returns `unavailable` once",
            normalized_evals,
        )

    def test_troubleshoot_owns_interpretation(self) -> None:
        reference = self.read(TROUBLESHOOT / "references" / "observability-evidence.md")
        normalized = " ".join(reference.split())

        self.assertIn(
            "`troubleshoot` retains responsibility for interpretation and causal proof",
            normalized,
        )
        self.assertIn("BLOCKED_MISSING_EVIDENCE", reference)
        self.assertIn("never authorizes a production remediation", normalized)

    def test_root_docs_preserve_sequential_query_admission(self) -> None:
        readme = " ".join(self.read(ROOT / "README.md").split())
        design = " ".join(self.read(ROOT / "docs" / "agentic-sdlc-design.md").split())

        self.assertIn(
            "one matching signal has non-Grafana provenance",
            readme,
        )
        self.assertIn(
            "admits one cheapest decision-changing query per provider call",
            readme,
        )
        self.assertIn(
            "Each embedded provider invocation attempts at most one pre-admitted query",
            design,
        )


if __name__ == "__main__":
    unittest.main()
