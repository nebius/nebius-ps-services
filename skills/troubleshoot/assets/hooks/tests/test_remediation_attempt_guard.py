#!/usr/bin/env python3
"""Disposable tests for the remediation-attempt Codex hook."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
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


def complete_general_report(
    classification: str = "VERIFIED_FIXED",
    *,
    current_impact: str = "The reported local failure no longer reproduces.",
    completion_verdict: str | None = None,
) -> str:
    del completion_verdict
    diagnosed_fixed = classification == "DIAGNOSED-FIXED"
    verified_fixed = classification == "VERIFIED_FIXED"
    changes = (
        "Repaired the canonical owner and added the focused regression oracle."
        if diagnosed_fixed or verified_fixed
        else "No owner-correct repair is claimed for this outcome."
    )
    verified = (
        "The original reproducer, focused regression, and source boundary passed."
        if diagnosed_fixed or verified_fixed
        else "The bounded investigation state and reported outcome were checked."
    )
    not_verified = (
        guard.VERIFIED_NO_GAP
        if verified_fixed
        else "Installed activation and live replay remain unverified."
    )
    return "\n".join(
        [
            "# Troubleshooting Report",
            "## Outcome",
            f"- Classification: {classification}",
            "- Confidence: High",
            "- Fixed scope: The canonical local report-hook source boundary.",
            f"- Current state: {current_impact}",
            "## Root Cause And Fix",
            "- Root cause: The report hook enforced a broader contract than its owner required.",
            f"- Changes made: {changes}",
            "## Verification",
            f"- Verified: {verified}",
            f"- Not verified: {not_verified}",
            "## Next Action",
            "- Owner: Runtime maintainer",
            "- Action: Install the reviewed hook and run a fresh disposable session.",
            "- Done when: Installed parity and the fresh-session report path both pass.",
        ]
    )


def complete_report(
    *,
    stop_trigger: str = "attempt_limit",
    attempt_count: int = guard.DEFAULT_ATTEMPT_LIMIT,
    legacy_evidence: bool = False,
) -> str:
    attempt_lines = [
        f"- attempt-{number} | Remediation: remediation {number} | "
        f"Verification: verification {number} | Result: failed_same_blocker"
        for number in range(1, attempt_count + 1)
    ]
    evidence = "Historical evidence summary unavailable." if legacy_evidence else None
    evidence_lines = [
        f"- attempt-{number} | Evidence: {evidence or f'new evidence {number}'}"
        for number in range(1, attempt_count + 1)
    ]
    if legacy_evidence:
        evidence_lines.append(f"- {guard.LEGACY_EVIDENCE_NOTE}")
    return "\n".join(
        [
            "# Troubleshooting Report",
            "## Outcome",
            "- Classification: UNRESOLVED",
            "- Confidence: Unknown",
            "- Fixed scope: No fixed scope was proven before budget exhaustion.",
            "- Current state: The bounded tranche is exhausted; further tools are denied.",
            f"- {guard.REPORT_MARKER}",
            f"- Stop trigger: {stop_trigger}",
            "## Root Cause And Fix",
            "- Root cause: The same bounded operation still fails.",
            "- Blocker key: component|operation|error-class|boundary",
            *attempt_lines,
            "- Changes made: Marker-recorded attempts did not remove the blocker.",
            "## Verification",
            "- Verified: The recorded attempts retained the same bounded blocker.",
            *evidence_lines,
            "- Not verified: A durable repair and end-to-end recovery remain unverified.",
            "## Next Action",
            "- Owner: User",
            "- Action: Review the evidence before authorizing another bounded tranche.",
            "- Done when: A new explicit instruction authorizes the next safe action.",
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
        output = guard.evaluate(self.payload)
        self.assertNotEqual(
            output.get("reason"),
            "Troubleshoot prompt context exceeds its safe bound.",
        )
        hook_output = output.get("hookSpecificOutput")
        if isinstance(hook_output, dict):
            context = hook_output.get("additionalContext")
            if isinstance(context, str):
                self.assertLessEqual(len(context), guard.MAX_ADDITIONAL_CONTEXT_CHARS)
        return output

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

    def test_explicit_invocation_without_marker_records_advisory_report_state(
        self,
    ) -> None:
        output = self.evaluate_prompt(
            "$troubleshoot diagnose the failure", turn_id="report-turn"
        )
        self.assertIn(
            "# Troubleshooting Report",
            output["hookSpecificOutput"]["additionalContext"],
        )
        for classification in guard.GENERAL_REPORT_OUTCOMES:
            self.assertIn(
                classification, output["hookSpecificOutput"]["additionalContext"]
            )
        self.assertIn(
            "`repo/path:line`",
            output["hookSpecificOutput"]["additionalContext"],
        )
        for field in (
            "Classification",
            "Confidence",
            "Fixed scope",
            "Current state",
            "Root cause",
            "Changes made",
            "Verified",
            "Not verified",
            "Owner",
            "Action",
            "Done when",
        ):
            self.assertIn(field, output["hookSpecificOutput"]["additionalContext"])
        obligation = self.report_obligation_data()
        self.assertEqual(obligation["schema"], guard.REPORT_OBLIGATION_SCHEMA)
        self.assertEqual(obligation["status"], "active")
        self.assertNotIn("corrections", obligation)
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
        self.assertTrue(incomplete["continue"])
        self.assertNotIn("decision", incomplete)
        self.assertNotIn("systemMessage", incomplete)
        self.assertEqual(self.report_obligation_data()["status"], "advisory_incomplete")
        self.assertNotIn("corrections", self.report_obligation_data())

        self.payload.update(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "run-check"},
            }
        )
        self.assertEqual(guard.evaluate(self.payload), {})

        self.evaluate_prompt("$troubleshoot", turn_id="valid-report-turn")
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "turn_id": "valid-report-turn",
                "stop_hook_active": False,
            }
        )
        self.payload["last_assistant_message"] = complete_general_report()
        complete = self.evaluate_stop_with_arbiter()
        self.assertTrue(complete["continue"])
        self.assertEqual(self.report_obligation_data()["status"], "delivered")

    def test_every_prompt_contract_combination_fits_the_host_context_limit(
        self,
    ) -> None:
        report_notice = guard.GENERAL_REPORT_NOTICE
        for value in (
            "Outcome",
            "Root Cause And Fix",
            "Verification",
            "Next Action",
            *guard.GENERAL_REPORT_OUTCOMES,
            "Classification",
            "Confidence",
            "Fixed scope",
            "Current state",
            "Root cause",
            "Changes made",
            "Verified",
            "Not verified",
            "Owner",
            "Action",
            "Done when",
        ):
            self.assertIn(value, report_notice)

        pending_profiles = (
            {
                "mode": "resize_active",
                "attempt_limit": guard.MAX_ATTEMPT_LIMIT,
                "time_limit_minutes": guard.MAX_TIME_LIMIT_MINUTES,
                "authorization_id": "a" * 32,
            },
            {
                "mode": "next_tranche",
                "attempt_limit": guard.MAX_ATTEMPT_LIMIT,
                "time_limit_minutes": guard.MAX_TIME_LIMIT_MINUTES,
                "authorization_id": "a" * 32,
            },
        )
        issues = (
            (None, None),
            ("the remediation marker is missing from current.md", "missing"),
            ("the remediation marker is invalid: missing: blocker_summary", "invalid"),
            (
                "a causally independent blocker requires tranche 1 and a null "
                "override_summary",
                "valid",
            ),
            ("x" * 160, "valid"),
        )
        contexts = [report_notice + "\n" + guard._profile_context(10, 180, "a" * 32)]
        for pending in pending_profiles:
            for issue, state_kind in issues:
                contexts.append(
                    report_notice
                    + "\n"
                    + guard._pending_prompt_context(pending, issue, state_kind)
                )
        for context in contexts:
            with self.subTest(length=len(context)):
                self.assertLessEqual(len(context), guard.MAX_ADDITIONAL_CONTEXT_CHARS)

    def test_invalid_report_obligation_state_remains_fail_closed(self) -> None:
        self.evaluate_prompt("$troubleshoot", turn_id="tampered-report-state")
        obligation_file = guard.report_obligation_file_for_payload(self.payload)
        assert obligation_file is not None
        data = json.loads(obligation_file.read_text(encoding="utf-8"))
        data["schema"] = guard.PREVIOUS_REPORT_OBLIGATION_SCHEMA
        data["corrections"] = 0
        obligation_file.write_text(json.dumps(data), encoding="utf-8")

        self.payload.update(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "run-check"},
            }
        )
        denied = guard.evaluate(self.payload)
        reason = denied["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("reporting state is invalid", reason)
        self.assertIn("fresh Codex session", reason)

        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": complete_general_report(),
                "stop_hook_active": False,
            }
        )
        blocked = self.evaluate_stop_with_arbiter()
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("private report obligation is invalid", blocked["reason"])
        self.assertIn("fresh Codex session", blocked["reason"])

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

    def test_diagnosed_fixed_accepts_source_proof_with_runtime_pending(self) -> None:
        report = complete_general_report("DIAGNOSED-FIXED")
        self.assertIn("- Confidence: High", report)
        self.assertIn("Installed activation and live replay remain unverified", report)
        self.assertEqual(guard._general_report_complete(report), (True, ""))

    def test_diagnosed_fixed_rejects_planned_but_unapplied_repair(self) -> None:
        report = complete_general_report("DIAGNOSED-FIXED").replace(
            "Repaired the canonical owner and added the focused regression oracle.",
            "The owner-correct repair is planned and awaiting implementation.",
        )
        complete, issue = guard._general_report_complete(report)
        self.assertFalse(complete)
        self.assertIn("requires an applied owner-correct repair", issue)

    def test_verified_fixed_requires_no_unverified_declared_scope(self) -> None:
        report = complete_general_report().replace(
            guard.VERIFIED_NO_GAP,
            "Installed activation remains unverified.",
        )
        complete, issue = guard._general_report_complete(report)
        self.assertFalse(complete)
        self.assertIn("VERIFIED_FIXED requires", issue)

    def test_concise_report_requires_each_core_field_once(self) -> None:
        valid = complete_general_report("DIAGNOSED-FIXED")
        for field in (
            "- Fixed scope:",
            "- Root cause:",
            "- Changes made:",
            "- Verified:",
            "- Not verified:",
            "- Owner:",
            "- Action:",
            "- Done when:",
        ):
            with self.subTest(field=field):
                report = "\n".join(
                    line for line in valid.splitlines() if not line.startswith(field)
                )
                self.assertFalse(guard._general_report_complete(report)[0])

    def test_evidence_appendix_is_optional_and_not_a_completion_gate(self) -> None:
        report = (
            complete_general_report("DIAGNOSED-FIXED")
            + "\n"
            + "\n".join(
                [
                    "## Evidence Appendix",
                    "- Design: PASS",
                    "- Infrastructure: UNKNOWN",
                    "- Configuration: FAIL",
                ]
            )
        )
        self.assertEqual(guard._general_report_complete(report), (True, ""))

    def test_concise_report_rejects_sensitive_content(self) -> None:
        report = complete_general_report("DIAGNOSED-FIXED").replace(
            "The canonical local report-hook source boundary.",
            "Target host: worker-one",
        )
        complete, issue = guard._general_report_complete(report)
        self.assertFalse(complete)
        self.assertEqual(issue, guard.REPORT_SAFETY_ISSUE)

    def test_concise_report_enforces_repo_contained_local_reference_grammar(
        self,
    ) -> None:
        fake_home = self.root / "home" / "tester"
        repo = fake_home / "repo"
        project_a = repo / "project-a"
        project_b = repo / "project-b"
        project_a.mkdir(parents=True)
        project_b.mkdir()
        evidence = project_b / "evidence.md"
        evidence.write_text("safe\n", encoding="utf-8")
        spaced_evidence = project_b / "evidence file.md"
        spaced_evidence.write_text("safe\n", encoding="utf-8")
        parenthesized_evidence = project_b / "evidence(foo).md"
        parenthesized_evidence.write_text("safe\n", encoding="utf-8")
        sensitive_names = (
            "token=fake-test-value.md",
            "credential=do-not-echo.md",
            "credential%3Ddo-not-echo.md",
            "AKIAABCDEFGHIJKLMNOP.md",
            "db.internal.local.md",
            "10.0.0.1",
        )
        for name in sensitive_names:
            (project_b / name).write_text("safe\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "inside-link").symlink_to(project_b, target_is_directory=True)
        outside = fake_home / "outside"
        outside.mkdir()
        outside_evidence = outside / "evidence.md"
        outside_evidence.write_text("unsafe\n", encoding="utf-8")
        (repo / "outside-link").symlink_to(outside, target_is_directory=True)
        base = complete_general_report("UNRESOLVED")
        root_cause = (
            "The report hook enforced a broader contract than its owner required."
        )

        accepted_references = (
            "`project-b/evidence.md:1`",
            "[relative](project-b/evidence.md)",
            f"[absolute]({evidence})",
            "[home](~/repo/project-b/evidence.md)",
            f"[file]({evidence.as_uri()})",
            f"[space](<{spaced_evidence}>)",
            "[balanced parens](project-b/evidence(foo).md)",
            r"[escaped parens](project-b/evidence\(foo\).md)",
            '[title](project-b/evidence.md "Public evidence")',
            "[parenthesized title](project-b/evidence.md (Public evidence))",
            "[inside symlink](inside-link/evidence.md)",
            "[public guide](https://example.com/guide)",
        )
        with patch.dict(os.environ, {"HOME": str(fake_home)}):
            for reference in accepted_references:
                with self.subTest(reference=reference):
                    report = base.replace(root_cause, f"Evidence: {reference}.")
                    self.assertEqual(
                        guard._general_report_complete(report, repo), (True, "")
                    )
            self.assertEqual(
                guard._normalize_safe_local_references(
                    f"[space](<{spaced_evidence}>)", repo
                ),
                "[space](<project-b/evidence file.md>)",
            )

        for prose in (
            "Node.js remains supported.",
            "Invoke /plan next.",
            "The unmethoded route /api/v1/jobs remains descriptive prose.",
            "The PASS/FAIL/UNKNOWN status vocabulary remains explicit.",
            "The failing route is GET /api/v1/jobs.",
        ):
            with self.subTest(prose=prose):
                report = base.replace(root_cause, prose)
                self.assertEqual(
                    guard._general_report_complete(report, repo), (True, "")
                )

        advisory_references = (
            "project-b/evidence.md",
            "[http](http://example.com/guide)",
            "`http://example.com/guide`",
            "http://example.com/guide",
            "[mail](mailto:hello@example.com)",
            "[dot](./project-b/evidence.md)",
            f"[root]({repo})",
        )
        for reference in advisory_references:
            with self.subTest(advisory=reference):
                advisory = base.replace(root_cause, f"Evidence: {reference}.")
                self.assertEqual(
                    guard._general_report_complete(advisory, repo),
                    (False, guard.REPORT_REFERENCE_FORMAT_ISSUE),
                )

        unsafe_references = (
            "`../outside/evidence.md`",
            f"[absolute]({outside_evidence})",
            "[home](~/outside/evidence.md)",
            f"[file]({outside_evidence.as_uri()})",
            "[authority](file://host/path)",
            f"[query]({evidence.as_uri()}?x=1)",
            f"[fragment]({evidence.as_uri()}#L1)",
            "[relative query](project-b/evidence.md?x=1)",
            "[relative fragment](project-b/evidence.md#L1)",
            "[other scheme](ssh://example.com/evidence.md)",
            "[javascript](javascript:alert(1))",
            "[javascript entity](javascript&#58;alert(1))",
            "[javascript encoded](javascript%3Aalert(1))",
            "[data](data:text/html,<script>alert(1)</script>)",
            '<a href="javascript&#58;alert(1)">unsafe HTML</a>',
            "[userinfo](https://user:pass@example.com/guide)",
            "[private URL](https://10.0.0.1/guide)",
            "[parenthesized escape](../outside(evidence).md)",
            "[entity escape](..&#47;outside(evidence).md)",
            "[entity secret](project-b/token&#61;fake-test-value.md)",
            "[ambiguous](project-b/evidence.md",
            '[bad title](project-b/evidence.md "title"',
            f"[encoded]({repo.as_uri()}/project-a/%2e%2e/%2e%2e/outside/evidence.md)",
            f"[double encoded]({repo.as_uri()}/project-a/%252e%252e/evidence.md)",
            f"[nul]({repo.as_uri()}/project-b/evidence.md%00)",
            f"[slash]({repo.as_uri()}/project-b%2fevidence.md)",
            f"[backslash]({repo.as_uri()}/project-b%5cevidence.md)",
            f"[malformed]({repo.as_uri()}/project-b/%ZZ)",
            "[escape](outside-link/evidence.md)",
            r"`C:\\Temp\\report.log`",
            r"`\\server\share\report.log`",
            r"`~\repo\project-b\evidence.md`",
            "[token=fake-test-value](project-b/evidence.md)",
            "[endpoint=worker.internal](project-b/evidence.md)",
            *(f"[sensitive target](project-b/{name})" for name in sensitive_names),
        )
        with patch.dict(os.environ, {"HOME": str(fake_home)}):
            for reference in unsafe_references:
                with self.subTest(reference=reference):
                    report = base.replace(root_cause, f"Evidence: {reference}.")
                    self.assertEqual(
                        guard._general_report_complete(report, repo),
                        (False, guard.REPORT_SAFETY_ISSUE),
                    )

        self.assertEqual(guard.resolve_git_root(str(project_a)), str(repo.resolve()))
        self.assertIsNone(guard.resolve_git_root(str(outside)))

        for reference in (
            f"`{evidence}`",
            "`~/repo/project-b/evidence.md`",
            f"`{evidence.as_uri()}`",
        ):
            with self.subTest(reference=reference):
                report = base.replace(root_cause, f"Evidence: {reference}.")
                self.assertEqual(
                    guard._general_report_complete(report, None),
                    (False, guard.REPORT_SAFETY_ISSUE),
                )

    def test_nested_cwd_uses_resolved_git_root_for_local_reference_stop_states(
        self,
    ) -> None:
        repo = self.root / "repo"
        project_a = repo / "project-a"
        project_b = repo / "project-b"
        nested = project_a / "src" / "nested"
        nested.mkdir(parents=True)
        project_b.mkdir(parents=True)
        evidence = project_b / "evidence.md"
        evidence.write_text("safe\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        outside = Path(self.tmp.name) / "nested-outside"
        outside.mkdir()
        (outside / "report.log").write_text("unsafe\n", encoding="utf-8")
        (repo / "linked").symlink_to(outside, target_is_directory=True)

        self.payload["cwd"] = str(nested)
        self.evaluate_prompt("$troubleshoot", turn_id="safe-sibling")
        safe = complete_general_report("UNRESOLVED").replace(
            "The report hook enforced a broader contract than its owner required.",
            f"Evidence: [sibling]({evidence}).",
        )
        self.payload.update(
            {
                "cwd": str(nested),
                "hook_event_name": "Stop",
                "turn_id": "safe-sibling",
                "last_assistant_message": safe,
                "stop_hook_active": False,
            }
        )
        delivered = self.evaluate_stop_with_arbiter()
        self.assertTrue(delivered["continue"])
        self.assertNotIn("systemMessage", delivered)
        self.assertEqual(self.report_obligation_data()["status"], "delivered")

        self.evaluate_prompt("$troubleshoot", turn_id="unsafe-escape")
        unsafe = complete_general_report("UNRESOLVED").replace(
            "The report hook enforced a broader contract than its owner required.",
            "Evidence: `linked/report.log`.",
        )
        self.payload.update(
            {
                "cwd": str(nested),
                "hook_event_name": "Stop",
                "turn_id": "unsafe-escape",
                "last_assistant_message": unsafe,
                "stop_hook_active": False,
            }
        )
        stopped = self.evaluate_stop_with_arbiter()
        self.assertFalse(stopped["continue"])
        self.assertIn("unsafe", stopped["stopReason"])
        self.assertNotIn(str(outside), stopped["systemMessage"])
        self.assertNotIn("linked/report.log", stopped["systemMessage"])
        self.assertEqual(self.report_obligation_data()["status"], "sensitive_detected")

        self.payload["stop_hook_active"] = True
        repeated = self.evaluate_stop_with_arbiter()
        self.assertTrue(repeated["continue"])
        self.assertNotIn("systemMessage", repeated)

    def test_exhaustion_marker_must_be_inside_outcome(self) -> None:
        report = complete_report().replace(
            f"- {guard.REPORT_MARKER}",
            "- Exhaustion marker is recorded under Root Cause And Fix.",
        )
        report = report.replace(
            "## Root Cause And Fix",
            f"## Root Cause And Fix\n- {guard.REPORT_MARKER}",
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

    def test_exhaustion_rejects_diagnosed_fixed_classification(self) -> None:
        report = complete_report().replace(
            "- Classification: UNRESOLVED",
            "- Classification: DIAGNOSED-FIXED",
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
        self.assertIn("supported unresolved classification", issue)

    def test_ordinary_incomplete_report_is_advisory_without_fallback(
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
        output = self.evaluate_stop_with_arbiter()
        self.assertTrue(output["continue"])
        self.assertNotIn("decision", output)
        self.assertNotIn("systemMessage", output)
        self.assertEqual(self.report_obligation_data()["status"], "advisory_incomplete")
        self.payload["stop_hook_active"] = True
        self.assertTrue(self.evaluate_stop_with_arbiter()["continue"])

    def test_sensitive_report_stops_once_without_automatic_replacement(
        self,
    ) -> None:
        self.evaluate_prompt("$troubleshoot", turn_id="sensitive-report-turn")
        report = complete_general_report("DIAGNOSED-FIXED").replace(
            "The canonical local report-hook source boundary.",
            "credential=do-not-echo",
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "turn_id": "sensitive-report-turn",
                "last_assistant_message": report,
                "stop_hook_active": False,
            }
        )
        stopped = self.evaluate_stop_with_arbiter()
        self.assertFalse(stopped["continue"])
        self.assertNotIn("decision", stopped)
        warning = stopped["systemMessage"]
        self.assertNotIn("do-not-echo", warning)
        self.assertNotIn("# Troubleshooting Report", warning)
        self.assertIn("not request an automatic rewrite", warning)
        self.assertEqual(self.report_obligation_data()["status"], "sensitive_detected")

        self.payload["stop_hook_active"] = True
        repeated = self.evaluate_stop_with_arbiter()
        self.assertTrue(repeated["continue"])
        self.assertNotIn("systemMessage", repeated)

        self.payload.update(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "run-check"},
            }
        )
        self.assertEqual(guard.evaluate(self.payload), {})

    def test_report_status_write_failure_stops_without_echo_or_retry(self) -> None:
        self.evaluate_prompt("$troubleshoot", turn_id="report-write-failure")
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "turn_id": "report-write-failure",
                "last_assistant_message": complete_general_report(),
                "stop_hook_active": False,
                guard.REPORT_FINALIZE_FIELD: True,
            }
        )
        with patch.object(
            guard,
            "_close_report_obligation",
            side_effect=guard.GuardStateError("private detail must not be echoed"),
        ):
            stopped = guard.evaluate(self.payload)
        self.assertFalse(stopped["continue"])
        self.assertNotIn("decision", stopped)
        self.assertNotIn("private detail", stopped["systemMessage"])
        self.assertNotIn("# Troubleshooting Report", stopped["systemMessage"])

    def test_sensitive_status_write_failure_uses_only_generic_terminal_warning(
        self,
    ) -> None:
        self.evaluate_prompt("$troubleshoot", turn_id="sensitive-write-failure")
        sensitive = complete_general_report("DIAGNOSED-FIXED").replace(
            "The canonical local report-hook source boundary.",
            "credential=never-reflect-this",
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "turn_id": "sensitive-write-failure",
                "last_assistant_message": sensitive,
                "stop_hook_active": False,
            }
        )
        with patch.object(
            guard,
            "_close_report_obligation",
            side_effect=guard.GuardStateError("private detail must not be echoed"),
        ):
            stopped = guard.evaluate(self.payload)
        self.assertFalse(stopped["continue"])
        self.assertNotIn("decision", stopped)
        self.assertNotIn("never-reflect-this", stopped["systemMessage"])
        self.assertNotIn("private detail", stopped["systemMessage"])
        self.assertEqual(self.report_obligation_data()["status"], "active")

    def test_interrupted_obligation_survives_later_turn_until_reported(
        self,
    ) -> None:
        self.evaluate_prompt("$troubleshoot", turn_id="interrupted-turn")
        resumed = self.evaluate_prompt("Resume safely.", turn_id="resumed-turn")
        self.assertIn(
            "earlier troubleshoot report remains undelivered",
            resumed["hookSpecificOutput"]["additionalContext"],
        )
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
            if path.name == "stop_sdlc_continue.py":
                return {
                    "decision": "block",
                    "reason": "SDLC continuation remains required.",
                }
            return {"continue": True}

        with patch.object(arbiter, "_run_delegate", side_effect=with_project_block):
            blocked = arbiter.evaluate(self.payload, hook_dir)
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("SDLC continuation", blocked["reason"])
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
        seen: list[str] = []

        def with_terminal_peer(
            path: Path,
            payload: dict[str, object],
            _deadline: float,
        ) -> dict[str, object]:
            seen.append(path.name)
            if path.name == "remediation_attempt_guard.py":
                return guard.evaluate(payload)
            if path.name == "stop_sdlc_continue.py":
                return terminal
            return {"continue": True}

        with patch.object(arbiter, "_run_delegate", side_effect=with_terminal_peer):
            result = arbiter.evaluate(self.payload, hook_dir)
        self.assertEqual(result, terminal)
        self.assertEqual(seen[: len(arbiter.DELEGATES)], list(arbiter.DELEGATES))
        self.assertEqual(seen[-1], "remediation_attempt_guard.py")
        self.assertEqual(self.report_obligation_data()["status"], "delivered")

    def test_terminal_peer_retains_precedence_and_surfaces_finalization_failure(
        self,
    ) -> None:
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": complete_general_report(),
                "stop_hook_active": False,
            }
        )
        hook_dir = Path(self.tmp.name) / "terminal-finalization-failure-hooks"
        hook_dir.mkdir()
        for delegate in arbiter.DELEGATES:
            (hook_dir / delegate).write_text("# test delegate\n", encoding="utf-8")
        terminal = {
            "continue": False,
            "stopReason": "A peer Stop policy ended the host turn.",
        }

        def finalization_fails(
            path: Path,
            payload: dict[str, object],
            _deadline: float,
        ) -> dict[str, object]:
            if path.name == "remediation_attempt_guard.py":
                if payload.get(arbiter.REPORT_FINALIZE_FIELD):
                    return {"continue": True}
                return {"continue": True, arbiter.REPORT_READY_FIELD: True}
            if path.name == "stop_sdlc_continue.py":
                return terminal
            return {"continue": True}

        with patch.object(arbiter, "_run_delegate", side_effect=finalization_fails):
            result = arbiter.evaluate(self.payload, hook_dir)
        self.assertEqual(result["stopReason"], terminal["stopReason"])
        self.assertIn("delivery could not be recorded", result["systemMessage"])

    def test_missing_finalization_ack_fails_closed_without_terminal_peer(
        self,
    ) -> None:
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": complete_general_report(),
                "stop_hook_active": False,
            }
        )
        hook_dir = Path(self.tmp.name) / "missing-finalization-ack-hooks"
        hook_dir.mkdir()
        for delegate in arbiter.DELEGATES:
            (hook_dir / delegate).write_text("# test delegate\n", encoding="utf-8")

        def missing_ack(
            path: Path,
            payload: dict[str, object],
            _deadline: float,
        ) -> dict[str, object]:
            if path.name == "remediation_attempt_guard.py":
                if payload.get(arbiter.REPORT_FINALIZE_FIELD):
                    return {"continue": True}
                return {"continue": True, arbiter.REPORT_READY_FIELD: True}
            return {"continue": True}

        with patch.object(arbiter, "_run_delegate", side_effect=missing_ack):
            result = arbiter.evaluate(self.payload, hook_dir)
        self.assertFalse(result["continue"])
        self.assertIn("delivery could not be recorded", result["systemMessage"])

    def test_first_terminal_result_keeps_precedence_while_all_peers_run(
        self,
    ) -> None:
        self.evaluate_prompt("$troubleshoot", turn_id="first-terminal")
        sensitive = complete_general_report("DIAGNOSED-FIXED").replace(
            "The canonical local report-hook source boundary.",
            "credential=never-echo-this",
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "turn_id": "first-terminal",
                "last_assistant_message": sensitive,
                "stop_hook_active": False,
            }
        )
        hook_dir = Path(self.tmp.name) / "all-delegate-hooks"
        hook_dir.mkdir()
        for delegate in arbiter.DELEGATES:
            (hook_dir / delegate).write_text("# test delegate\n", encoding="utf-8")
        seen: list[str] = []

        def terminal_then_peers(
            path: Path,
            payload: dict[str, object],
            _deadline: float,
        ) -> dict[str, object]:
            seen.append(path.name)
            if path.name == "remediation_attempt_guard.py":
                return guard.evaluate(payload)
            if path.name == "stop_sdlc_continue.py":
                return {
                    "continue": False,
                    "stopReason": "A later peer terminal must not replace the first.",
                }
            if path.name == "stop_sdlc_continue.py":
                return {"decision": "block", "reason": "Later continuation."}
            return {"continue": True}

        with patch.object(arbiter, "_run_delegate", side_effect=terminal_then_peers):
            result = arbiter.evaluate(self.payload, hook_dir)
        self.assertEqual(seen, list(arbiter.DELEGATES))
        self.assertFalse(result["continue"])
        self.assertIn("sensitive or unsafe", result["stopReason"])
        self.assertNotIn("later peer", result["stopReason"].casefold())
        self.assertEqual(self.report_obligation_data()["status"], "sensitive_detected")

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
        denied = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")
        reason = denied["permissionDecisionReason"]
        self.assertIn("*** Update File when it exists", reason)
        self.assertIn("*** Add File only when it is absent", reason)
        self.assertIn("Delete/Add replacement are denied", reason)
        state_file = guard.state_file_for_payload(self.payload)
        assert state_file is not None
        self.payload.update(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Add File: {state_file}",
                            "+replacement",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        denied = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")

        self.payload["tool_input"] = {
            "command": "\n".join(
                [
                    "*** Begin Patch",
                    f"*** Delete File: {state_file}",
                    f"*** Add File: {state_file}",
                    "+replacement",
                    "*** End Patch",
                ]
            )
        }
        denied = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")

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

    def test_resolved_marker_explicit_profile_change_preserves_resolution(
        self,
    ) -> None:
        recorded = [attempt(1, result="succeeded")]
        self.write_state(state_data(attempts=recorded, status="resolved"))

        output = self.evaluate_prompt(
            "$troubleshoot --attempt-limit=10 --time-limit-minutes=180",
            turn_id="resolved-resize",
        )
        self.assertIn(
            "preserve its blocker, tranche, attempt ledger, counters",
            output["hookSpecificOutput"]["additionalContext"],
        )
        pending = self.authorization_data()["pending"]
        assert isinstance(pending, dict)
        self.assertEqual(pending["mode"], "resize_active")

        state_file = guard.state_file_for_payload(self.payload)
        assert state_file is not None
        self.payload.update(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Add File: {state_file}",
                            "+replacement",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        denied = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")

        self.payload["tool_input"] = {
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
        }
        self.assertEqual(self.evaluate_pre_tool(), {})

        self.write_state(
            state_data(
                attempts=recorded,
                status="resolved",
                attempt_limit=10,
                time_limit_minutes=180,
                budget_authorization_id=pending["authorization_id"],
            )
        )
        self.payload.update(
            {"tool_name": "Bash", "tool_input": {"command": "run-check"}}
        )
        self.assertEqual(self.evaluate_pre_tool(), {})
        authorization = self.authorization_data()
        self.assertIsNone(authorization["pending"])
        self.assertEqual(
            authorization["current"]["authorization_id"],
            pending["authorization_id"],
        )

    def test_resolved_marker_new_investigation_does_not_preemptively_gate_tools(
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
        self.assertIn("Troubleshoot remediation profile", context)
        self.assertNotIn("prior terminal marker", context)
        self.assertNotIn("complete canonical fresh active marker", context)
        self.assertEqual(self.evaluate_pre_tool(), {})
        self.assertEqual(
            guard.load_authorization_state(self.payload).kind,
            "missing",
        )

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
        self.assertIn("## Next Action", fallback)
        self.assertNotIn("## Completion Gate", fallback)
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

    def test_exhausted_fallback_preserves_contained_and_redacts_escaping_references(
        self,
    ) -> None:
        inside = self.root / "evidence" / "inside.log"
        inside.parent.mkdir()
        inside.write_text("safe\n", encoding="utf-8")
        attempts = default_failed_attempts()
        attempts[0]["new_evidence"] = f"`{inside}`"
        self.write_state(
            state_data(
                attempts=attempts,
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        state = guard.load_guard_state(self.payload)
        fallback = guard._fallback_report(state, "incomplete", self.root)
        self.assertNotIn(str(inside), fallback)
        self.assertIn("`evidence/inside.log`", fallback)
        self.assertEqual(guard._report_complete(fallback, state, self.root), (True, ""))

        bounded_fallback = "Evidence summary unavailable."
        long_target = "evidence/" + ("nested-segment/" * 6) + "inside.log"
        for reference in (
            f"[long]({long_target})",
            f"[long angle](<{long_target}>)",
            f"`{long_target}`",
            f"`{self.root}`",
            "https://example.com/" + ("evidence-" * 12),
        ):
            with self.subTest(bounded_reference=reference):
                self.assertEqual(
                    guard._bounded_report_value(
                        reference,
                        bounded_fallback,
                        project_root=self.root,
                    ),
                    bounded_fallback,
                )

        attempts = default_failed_attempts()
        attempts[0]["new_evidence"] = f"[long]({long_target})"
        self.write_state(
            state_data(
                attempts=attempts,
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        state = guard.load_guard_state(self.payload)
        fallback = guard._fallback_report(state, "incomplete", self.root)
        self.assertNotIn("[long]", fallback)
        self.assertIn(bounded_fallback, fallback)
        self.assertEqual(guard._report_complete(fallback, state, self.root), (True, ""))

        outside = Path(self.tmp.name) / "exhausted-outside"
        outside.mkdir()
        (outside / "report.log").write_text("unsafe\n", encoding="utf-8")
        (self.root / "linked").symlink_to(outside, target_is_directory=True)
        attempts = default_failed_attempts()
        attempts[0]["new_evidence"] = "`linked/report.log`"
        self.write_state(
            state_data(
                attempts=attempts,
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        state = guard.load_guard_state(self.payload)
        fallback = guard._fallback_report(state, "incomplete", self.root)
        self.assertNotIn("linked/report.log", fallback)
        self.assertIn("Evidence summary unavailable.", fallback)
        self.assertEqual(
            guard._report_complete(fallback, state, self.root),
            (True, ""),
        )

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

    def test_exhausted_fallback_is_revalidated_before_emission(self) -> None:
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
        with patch.object(
            guard,
            "_fallback_report",
            return_value="credential=do-not-echo [broken](../outside(file).md)",
        ):
            fallback = guard._validated_fallback_report(state, "incomplete", self.root)
        self.assertNotIn("do-not-echo", fallback)
        self.assertNotIn("outside(file).md", fallback)
        self.assertEqual(guard._general_report_complete(fallback), (True, ""))

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

    def test_longest_exhaustion_correction_stays_below_preview_limit(
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
        self.assertIn("# Troubleshooting Report", correction["reason"])
        self.assertIn("- attempt-5 | Remediation:", correction["reason"])
        self.assertNotIn("## Completion Gate", correction["reason"])
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
                self.assertNotIn(sensitive_value, sensitive_result["reason"])
                self.assertIn(guard.REPORT_MARKER, sensitive_result["reason"])

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
            if line.startswith("- Root cause: ")
            else line
            for line in complete.splitlines()
        )
        self.assertEqual(
            guard._report_complete(missing, state)[1],
            "## Root Cause And Fix requires one substantive `- Root cause:` line",
        )

        paraphrased = "\n".join(
            "- Root cause: A different substantive summary of the same failure."
            if line.startswith("- Root cause: ")
            else line
            for line in complete.splitlines()
        )
        self.assertEqual(
            guard._report_complete(paraphrased, state)[1],
            "Root cause must exactly match the bounded marker-derived blocker",
        )

        missing_source = "\n".join(
            "The source section remains intentionally substantive."
            if line.startswith("- Blocker key: ")
            else line
            for line in complete.splitlines()
        )
        self.assertEqual(
            guard._report_complete(missing_source, state)[1],
            "Blocker key must exactly match the bounded marker-derived value",
        )

        paraphrased_source = "\n".join(
            "- Blocker key: another|substantive|source|boundary"
            if line.startswith("- Blocker key: ")
            else line
            for line in complete.splitlines()
        )
        self.assertEqual(
            guard._report_complete(paraphrased_source, state)[1],
            "Blocker key must exactly match the bounded marker-derived value",
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
            if path.name == "stop_sdlc_continue.py":
                return {
                    "decision": "block",
                    "reason": "SDLC continuation remains required.",
                }
            return {"continue": True}

        with patch.object(arbiter, "_run_delegate", side_effect=with_project_block):
            blocked = arbiter.evaluate(self.payload, hook_dir)
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("SDLC continuation", blocked["reason"])

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
            if path.name == "stop_sdlc_continue.py":
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
