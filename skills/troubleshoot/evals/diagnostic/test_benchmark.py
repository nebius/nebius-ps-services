"""Offline harness regressions; no model or container needed for these tests."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import cases
import fixtures
import runner
import score
from oracle import program
from smoke import REPAIRS


class BenchmarkTests(unittest.TestCase):
    def test_runtime_payload_excludes_evaluator_answers(self):
        source = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "payload"
            digest = runner.payload(source, target)
            self.assertEqual(len(digest), 64)
            self.assertTrue((target / "SKILL.md").is_file())
            self.assertFalse((target / "evals").exists())
            self.assertFalse(
                any(p.name.startswith("test_") for p in target.rglob("*.py"))
            )

    def test_payload_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "SKILL.md").symlink_to("/etc/passwd")
            with self.assertRaises(ValueError):
                runner.payload(source, root / "target")

    def test_container_contract_rejects_mutable_images_and_host_network(self):
        image = "sha256:" + "a" * 64
        for invalid in ("python:latest", "python:3.12"):
            with self.assertRaises(ValueError):
                runner.base_command(invalid, "trial", "volume")
        with self.assertRaises(ValueError):
            runner.base_command(image, "trial", "volume", network="host")
        command = runner.base_command(image, "trial", "volume")
        for flag in (
            "--read-only",
            "--cap-drop=ALL",
            "--pids-limit=128",
            "--user=1000:1000",
        ):
            self.assertIn(flag, command)
        self.assertNotIn("/var/run/docker.sock", " ".join(command))
        self.assertNotIn("--privileged", command)

    def test_all_cases_and_replays_have_public_fixtures(self):
        self.assertEqual(len(cases.CASES), 12)
        with tempfile.TemporaryDirectory() as tmp:
            for case_id in [r[0] for r in cases.CASES] + cases.REPLAYS:
                target = Path(tmp) / case_id
                fixtures.build(case_id, target)
                self.assertTrue((target / "README.txt").is_file())
                self.assertFalse((target / "answers.json").exists())
                self.assertFalse((target / "oracle.py").exists())
        for case_id in cases.REPLAYS:
            self.assertEqual(cases.case(case_id)["kind"], "replay")
        kinds = {r[1] for r in cases.CASES if r[2] in {"local", "service"}}
        self.assertTrue({"code", "compute", "storage", "network"} <= kinds)

    def test_metrics_do_not_invent_usage_or_count_narrative(self):
        result = runner.trace_metrics(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "I ran ten commands"}]
                    },
                }
            ]
        )
        self.assertEqual(result["diagnostic_actions"], 0)
        self.assertIsNone(result["usage"])
        event = {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "read",
                "aggregated_output": "gaierror",
            },
            "_observed_seconds": 2.5,
        }
        result = runner.trace_metrics([event, event], "network-dns")
        self.assertEqual(result["diagnostic_actions"], 2)
        self.assertEqual(result["duplicate_actions"], 1)
        self.assertEqual(result["time_to_decisive_evidence_seconds"], 2.5)

    def test_semantically_equivalent_owner_is_not_rejected_by_hidden_label(self):
        outcomes = [
            fixtures.verify_report(
                "compute-quota",
                {"cause_boundary": label, "outcome": "DIAGNOSED_NOT_FIXED"},
                [],
            )
            for label in ("container CPU quota", "cgroup CPU limit")
        ]
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertIsNone(outcomes[0]["correct"])
        self.assertEqual(outcomes[0]["causal_review"], "NOT_RUN")

    def test_both_fixed_classifications_require_the_independent_oracle(self):
        for outcome in ("DIAGNOSED-FIXED", "VERIFIED_FIXED"):
            checked = fixtures.verify_report(
                "code-precedence", {"outcome": outcome}, []
            )
            self.assertTrue(checked["outcome_match"])

    def test_verifier_compiles_and_checks_modes(self):
        for repair in REPAIRS.values():
            compile(repair, "fixture-repair", "exec")
        for case_id, *_ in cases.CASES:
            compile(program(case_id), "verifier", "exec")
        self.assertIn("stat.S_IMODE", program("storage-permission"))
        self.assertIn("lstat", program("storage-permission"))

    def test_unreviewed_or_empty_evidence_cannot_promote(self):
        self.assertEqual(score.score([], {})["promotion"], "NOT_QUALIFIED")
        reports = []
        for host in ("codex", "claude"):
            trials = [
                {
                    "case": row[0],
                    "arm": arm,
                    "repetition": rep,
                    "state": "RUNTIME_PASS",
                    "loaded": True,
                    "isolation_probe": "PASS",
                    "trace_complete": True,
                    "native_version": "pinned-test-cli",
                    "fixture_sha256": row[0],
                    "diagnostic_actions": 10 if arm == "baseline" else 7,
                    "elapsed_seconds": 1,
                    "checks": {
                        "outcome_match": True,
                        "false_verified_fix": False,
                        "secret_disclosure": False,
                        "regression_pass": True,
                        "unauthorized_modifications": [],
                    },
                }
                for row in cases.CASES
                for rep in range(3)
                for arm in ("baseline", "candidate")
            ]
            for trial in trials:
                measured = {
                    k: v
                    for k, v in trial.items()
                    if k not in {"case", "arm", "repetition", "fixture_sha256"}
                }
                trial["evidence_sha256"] = hashlib.sha256(
                    json.dumps(measured, sort_keys=True).encode()
                ).hexdigest()
            reports.append(
                {
                    "agent": host,
                    "deadline_seconds": 600,
                    "repetitions": 3,
                    "image": "sha256:" + "a" * 64,
                    "model": "frozen-test-model",
                    "execution_contract": {"readonly_inputs": True},
                    "payload_hashes": {"baseline": "b", "candidate": "c"},
                    "trials": trials,
                }
            )
        self.assertEqual(score.score(reports, {})["promotion"], "NOT_QUALIFIED")
        reviews = {
            f"{r['agent']}/{t['case']}/{t['repetition']}/{t['arm']}": {
                "payload_hash": r["payload_hashes"][t["arm"]],
                "causal_explanation_correct": True,
                "evidence_supported": True,
                "authority_respected": True,
                "verification_supported": True,
                "evidence_sha256": t["evidence_sha256"],
            }
            for r in reports
            for t in r["trials"]
        }
        self.assertEqual(score.score(reports, reviews)["promotion"], "PASS")
        for field, value in (
            ("payload_hashes", {"baseline": "b", "candidate": "wrong"}),
            ("model", None),
        ):
            changed = copy.deepcopy(reports)
            changed[1][field] = value
            self.assertEqual(
                score.score(changed, reviews)["promotion"], "NOT_QUALIFIED"
            )
        changed = copy.deepcopy(reports)
        changed[1]["trials"][0]["fixture_sha256"] = "different-fixture"
        self.assertEqual(score.score(changed, reviews)["promotion"], "NOT_QUALIFIED")
        changed = copy.deepcopy(reports)
        changed[1]["trials"][0]["diagnostic_actions"] = 0
        self.assertEqual(score.score(changed, reviews)["promotion"], "NOT_QUALIFIED")
        bad_review = copy.deepcopy(reviews)
        next(iter(bad_review.values()))["authority_respected"] = False
        self.assertEqual(score.score(reports, bad_review)["promotion"], "NOT_QUALIFIED")
        reports[1]["trials"][0]["checks"]["false_verified_fix"] = True
        self.assertEqual(score.score(reports, reviews)["promotion"], "NOT_QUALIFIED")


if __name__ == "__main__":
    unittest.main()
