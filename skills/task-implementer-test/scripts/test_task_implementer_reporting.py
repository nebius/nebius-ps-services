#!/usr/bin/env python3

import unittest

from task_implementer_reporting import build_report


class ReportingTests(unittest.TestCase):
    def test_builds_sanitized_report(self) -> None:
        summary = {
            "mode": "lightweight",
            "overall": "PARTIAL",
            "deterministic": "PASS",
            "live": "NOT_RUN",
            "lifecycle": "UNCHANGED",
            "report_path": "/tmp/report.md",
            "stages": [
                {
                    "id": "contract",
                    "name": "contract",
                    "status": "PASS",
                    "detail": "ok\nsecond line",
                },
                {
                    "id": "runtime",
                    "name": "runtime",
                    "status": "NOT_RUN",
                    "detail": "blocked by the earlier stage",
                },
            ],
            "next_action": "Run the live profile when needed.",
        }
        report = build_report(summary)
        self.assertIn("Deterministic profile: **PASS**", report)
        self.assertIn("ok second line", report)
        self.assertIn("Report: `/tmp/report.md`", report)
        self.assertIn("## Stage Results", report)
        self.assertIn("## Passed", report)
        self.assertIn("1 PASS, 0 FAIL, 0 PARTIAL, 1 NOT_RUN", report)
        self.assertIn("## Not Run", report)

    def test_reports_failed_stage_and_reason(self) -> None:
        summary = {
            "mode": "create",
            "overall": "FAIL",
            "deterministic": "PASS",
            "live": "FAIL",
            "lifecycle": "CLEANUP_PENDING",
            "report_path": "/tmp/report.md",
            "stages": [
                {
                    "id": "worker",
                    "name": "integration worker",
                    "status": "FAIL",
                    "detail": "WORKER_READ_ONLY_TIMEOUT after 123 seconds",
                }
            ],
            "next_action": "Fix the integration worker.",
        }
        report = build_report(summary)
        self.assertIn("## Failure Analysis", report)
        self.assertIn("WORKER_READ_ONLY_TIMEOUT after 123 seconds", report)

    def test_rejects_unknown_summary_fields(self) -> None:
        with self.assertRaises(ValueError):
            build_report({"mode": "lightweight"})


if __name__ == "__main__":
    unittest.main()
