#!/usr/bin/env python3
"""Tests for troubleshoot deterministic helpers."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
COLLECT = SCRIPTS / "collect_evidence.py"
REPEAT = SCRIPTS / "repeat_command.py"
COMPARE = SCRIPTS / "compare_evidence.py"
REDACTION = SCRIPTS / "evidence_redaction.py"


def load_script_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load test module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_script(
    script: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(script), *args),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


class EvidenceRedactionTests(unittest.TestCase):
    def test_sensitive_identifier_matching_avoids_substring_false_positives(
        self,
    ) -> None:
        module = load_script_module(REDACTION)
        for name in ("security", "ghost_count", "ghostscript"):
            self.assertFalse(module.is_sensitive_name(name), name)
        for name in ("DBPassword", "database_password", "x-api-key", "access_token"):
            self.assertTrue(module.is_sensitive_name(name), name)

    def test_redacts_private_ipv6_and_preserves_unrelated_diagnostics(self) -> None:
        module = load_script_module(REDACTION)
        unique_local = "fd12" + "::1"
        link_local = "fe80" + "::1234"
        loopback = "::" + "1"
        text = " ".join(
            (
                "security=enabled",
                "ghost_count=2",
                unique_local,
                link_local,
                loopback,
            )
        )
        redacted = module.redact_text(text)
        self.assertIn("security=enabled", redacted)
        self.assertIn("ghost_count=2", redacted)
        for private_address in (unique_local, link_local, loopback):
            self.assertNotIn(private_address, redacted)
        self.assertGreaterEqual(redacted.count("[ENDPOINT:"), 3)

    def test_private_ipv4_link_local_endpoint_is_redacted(self) -> None:
        module = load_script_module(REDACTION)
        link_local = ".".join(("169", "254", "169", "254"))
        redacted = module.redact_text(f"endpoint={link_local}")
        self.assertNotIn(link_local, redacted)

    def test_public_ipv4_address_is_preserved(self) -> None:
        module = load_script_module(REDACTION)
        public_address = ".".join(("8", "8", "8", "8"))
        self.assertEqual(module.redact_text(public_address), public_address)


class CollectEvidenceTests(unittest.TestCase):
    def test_collects_schema_without_environment_values_or_repo_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(("git", "init", "-q"), cwd=root, check=True)
            tracked = root / "tracked.txt"
            tracked.write_text("seed\n", encoding="utf-8")
            subprocess.run(("git", "add", "tracked.txt"), cwd=root, check=True)
            subprocess.run(
                (
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "seed",
                ),
                cwd=root,
                check=True,
            )
            tracked.write_text("changed\n", encoding="utf-8")
            (root / "untracked private name.txt").write_text("x\n", encoding="utf-8")
            before = subprocess.run(
                ("git", "status", "--porcelain=v1", "--untracked-files=all"),
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            marker = "synthetic-" + ("z" * 48)
            env = os.environ.copy()
            env["".join(("CANARY_", "VALUE"))] = marker
            result = subprocess.run(
                (sys.executable, str(COLLECT), "--root", str(root)),
                cwd=root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], 1)
            self.assertTrue(payload["repository"]["git"]["dirty"])
            self.assertEqual(
                payload["filesystem"]["probe_scope"],
                "system_temporary_directory",
            )
            self.assertNotIn(marker, result.stdout)
            self.assertNotIn("untracked private name.txt", result.stdout)
            after = subprocess.run(
                ("git", "status", "--porcelain=v1", "--untracked-files=all"),
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(before, after)

    def test_refuses_symlink_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target.json"
            target.write_text("unchanged\n", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            result = run_script(COLLECT, "--root", str(root), "--output", str(link))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged\n")

    def test_writes_private_atomic_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "evidence.json"
            result = run_script(COLLECT, "--root", str(root), "--output", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text())["schema_version"], 1)
            if os.name == "posix":
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_git_status_failure_is_unknown_not_clean(self) -> None:
        module = load_script_module(COLLECT)
        with (
            mock.patch.object(
                module, "detect_git_work_tree", return_value=(True, None)
            ),
            mock.patch.object(module, "run_bounded", return_value="abc123"),
            mock.patch.object(
                module,
                "collect_git_status",
                return_value=(None, "git_status_failed"),
            ),
        ):
            payload = module.collect_git(Path.cwd())
        self.assertIsNone(payload["dirty"])
        self.assertFalse(payload["status_available"])
        self.assertEqual(payload["status_error"], "git_status_failed")

    def test_git_status_timeout_is_unknown_not_clean(self) -> None:
        module = load_script_module(COLLECT)
        with (
            mock.patch.object(
                module, "detect_git_work_tree", return_value=(True, None)
            ),
            mock.patch.object(module, "run_bounded", return_value="abc123"),
            mock.patch.object(
                module,
                "collect_git_status",
                return_value=(None, "git_status_timeout"),
            ),
        ):
            payload = module.collect_git(Path.cwd())
        self.assertIsNone(payload["dirty"])
        self.assertFalse(payload["status_available"])
        self.assertEqual(payload["status_error"], "git_status_timeout")

    def test_git_detection_failure_is_unknown_not_non_repository(self) -> None:
        module = load_script_module(COLLECT)
        with mock.patch.object(
            module,
            "detect_git_work_tree",
            return_value=(None, "git_work_tree_detection_unavailable"),
        ):
            payload = module.collect_git(Path.cwd())
        self.assertIsNone(payload["is_work_tree"])
        self.assertFalse(payload["detection_available"])
        self.assertIsNone(payload["dirty"])

    def test_git_status_stream_counts_with_record_ceiling(self) -> None:
        module = load_script_module(COLLECT)
        state = {
            "records_seen": 0,
            "records_counted": 0,
            "staged": 0,
            "unstaged": 0,
            "untracked": 0,
            "truncated": False,
            "stream_error": False,
        }
        stream = io.BytesIO(b"M  one\n M two\n?? three\n?? four\n")
        with mock.patch.object(module, "MAX_GIT_STATUS_RECORDS", 2):
            module.drain_git_status(stream, state)
        self.assertEqual(state["records_seen"], 4)
        self.assertEqual(state["records_counted"], 2)
        self.assertTrue(state["truncated"])
        self.assertEqual(state["staged"], 1)
        self.assertEqual(state["unstaged"], 1)


class RepeatCommandTests(unittest.TestCase):
    def test_redacts_inline_sensitive_argv_without_hiding_positional_values(
        self,
    ) -> None:
        module = load_script_module(REPEAT)
        short_secret = "short-value"
        argv = [
            f"--token={short_secret}",
            f"DB_PASSWORD={short_secret}",
            "security",
            "public-positional-value",
        ]
        redacted = module.redact_argv(argv)
        self.assertNotIn(short_secret, json.dumps(redacted))
        self.assertEqual(redacted[0], "--token=[REDACTED]")
        self.assertEqual(redacted[1], "DB_PASSWORD=[REDACTED]")
        self.assertEqual(redacted[2:], argv[2:])

    def test_clusters_pass_and_failure_signatures(self) -> None:
        marker = "synthetic-" + ("q" * 48)
        label = "".join(("TOK", "EN="))
        code = (
            "import sys; print(sys.argv[1] + sys.argv[2]); sys.exit(int(sys.argv[3]))"
        )
        pass_result = run_script(
            REPEAT,
            "--runs",
            "2",
            "--timeout",
            "2",
            "--",
            sys.executable,
            "-c",
            code,
            label,
            marker,
            "0",
        )
        self.assertEqual(pass_result.returncode, 0, pass_result.stderr)
        payload = json.loads(pass_result.stdout)
        self.assertEqual(payload["pass_count"], 2)
        self.assertEqual(payload["failure_count"], 0)
        self.assertNotIn(marker, pass_result.stdout)

        sensitive_key = "".join(("pass", "word"))
        json_result = run_script(
            REPEAT,
            "--runs",
            "1",
            "--timeout",
            "2",
            "--",
            sys.executable,
            "-c",
            "import json,sys; print(json.dumps({sys.argv[1]: sys.argv[2]}))",
            sensitive_key,
            "tiny-value",
        )
        self.assertNotIn("tiny-value", json_result.stdout)

        fail_result = run_script(
            REPEAT,
            "--runs",
            "2",
            "--timeout",
            "2",
            "--",
            sys.executable,
            "-c",
            "import sys; print('failed'); sys.exit(3)",
        )
        self.assertEqual(fail_result.returncode, 0, fail_result.stderr)
        failed = json.loads(fail_result.stdout)
        self.assertEqual(failed["failure_count"], 2)
        self.assertEqual(len(failed["signature_clusters"]), 1)

    def test_uses_literal_argv_without_shell_interpretation(self) -> None:
        literal = "$(touch SHOULD_NOT_EXIST); spaced value"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = run_script(
                REPEAT,
                "--runs",
                "1",
                "--timeout",
                "2",
                "--",
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1])",
                literal,
                cwd=root,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn(literal, payload["runs"][0]["stdout_tail"])
            self.assertFalse((root / "SHOULD_NOT_EXIST").exists())

    @unittest.skipUnless(os.name == "posix", "process-group assertion requires POSIX")
    def test_timeout_terminates_descendant_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "child-finished"
            child_code = (
                "import pathlib,time,sys; time.sleep(1.2); "
                "pathlib.Path(sys.argv[1]).write_text('done')"
            )
            parent_code = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); "
                "time.sleep(10)"
            )
            result = run_script(
                REPEAT,
                "--runs",
                "1",
                "--timeout",
                "0.2",
                "--",
                sys.executable,
                "-c",
                parent_code,
                child_code,
                str(marker),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["timeout_count"], 1)
            time.sleep(1.4)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "posix", "process-group assertion requires POSIX")
    def test_timeout_kills_descendant_when_child_ignores_term(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "stubborn-child-finished"
            child_code = (
                "import pathlib,signal,sys,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(1.2); pathlib.Path(sys.argv[1]).write_text('done')"
            )
            parent_code = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); "
                "time.sleep(10)"
            )
            result = run_script(
                REPEAT,
                "--runs",
                "1",
                "--timeout",
                "0.2",
                "--",
                sys.executable,
                "-c",
                parent_code,
                child_code,
                str(marker),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["timeout_count"], 1)
            time.sleep(1.4)
            self.assertFalse(marker.exists())

    def test_bounds_large_output_and_preserves_launch_error_artifact(self) -> None:
        large = run_script(
            REPEAT,
            "--runs",
            "1",
            "--timeout",
            "2",
            "--max-tail-bytes",
            "128",
            "--",
            sys.executable,
            "-c",
            "print('x' * 20000)",
        )
        self.assertEqual(large.returncode, 0, large.stderr)
        payload = json.loads(large.stdout)
        self.assertLessEqual(len(payload["runs"][0]["stdout_tail"].encode()), 128)
        self.assertGreater(payload["runs"][0]["stdout_bytes_seen"], 128)
        self.assertTrue(payload["runs"][0]["stdout_truncated"])

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "launch.json"
            missing = "definitely-missing-troubleshoot-command"
            result = run_script(
                REPEAT,
                "--runs",
                "5",
                "--out",
                str(output),
                "--",
                missing,
            )
            self.assertEqual(result.returncode, 2)
            failed = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(failed["requested_runs"], 5)
            self.assertEqual(failed["completed_runs"], 1)
            self.assertEqual(failed["launch_error_count"], 1)

    def test_redacts_sensitive_argv_output_and_private_endpoints(self) -> None:
        short_secret = "tiny-value"
        access_key = "".join(("AK", "IA", "A" * 16))
        endpoint = "http" + "://service.internal.invalid/path"
        address = ".".join(("10", "23", "45", "67"))
        pem = "\n".join(
            (
                "-----BEGIN " + "PRIVATE KEY-----",
                "short-body",
                "-----END " + "PRIVATE KEY-----",
            )
        )
        sensitive_key = "".join(("pass", "word"))
        code = "import json,sys; print(json.dumps(dict(zip(sys.argv[1::2],sys.argv[2::2]))))"
        result = run_script(
            REPEAT,
            "--runs",
            "1",
            "--timeout",
            "2",
            "--",
            sys.executable,
            "-c",
            code,
            sensitive_key,
            short_secret,
            "endpoint",
            endpoint,
            "address",
            address,
            "access_key",
            access_key,
            "certificate",
            pem,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for private_value in (short_secret, endpoint, address, access_key, pem):
            self.assertNotIn(private_value, result.stdout)
        self.assertIn("[REDACTED]", result.stdout)
        self.assertIn("[ENDPOINT:", result.stdout)

        prefixed_secret = "prefixed-short-value"
        prefixed = run_script(
            REPEAT,
            "--runs",
            "1",
            "--timeout",
            "2",
            "--",
            sys.executable,
            "-c",
            "import json,sys; print(json.dumps({sys.argv[1]: sys.argv[2]}))",
            "database_password",
            prefixed_secret,
        )
        self.assertNotIn(prefixed_secret, prefixed.stdout)

        quoted_secret = " ".join(("quoted", "short", "value"))
        escaped_secret = "escaped" + '"' + "short"
        quoted = run_script(
            REPEAT,
            "--runs",
            "1",
            "--timeout",
            "2",
            "--",
            sys.executable,
            "-c",
            (
                "import json,sys; "
                "print(sys.argv[1] + '=\"' + sys.argv[2] + '\"'); "
                "print(json.dumps({sys.argv[3]: sys.argv[4]}))"
            ),
            "database_password",
            quoted_secret,
            "refresh_token",
            escaped_secret,
        )
        self.assertNotIn(quoted_secret, quoted.stdout)
        self.assertNotIn(escaped_secret, quoted.stdout)
        self.assertNotIn("escaped", quoted.stdout)

    def test_rejects_unbounded_aggregate_tail_request(self) -> None:
        result = run_script(
            REPEAT,
            "--runs",
            "10000",
            "--max-tail-bytes",
            "65536",
            "--",
            sys.executable,
            "-c",
            "print('should not run')",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("retained stream tails", result.stderr)

    def test_rejects_non_finite_timeout(self) -> None:
        result = run_script(
            REPEAT,
            "--runs",
            "1",
            "--timeout",
            "nan",
            "--",
            sys.executable,
            "-c",
            "print('should not run')",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("finite and positive", result.stderr)

    def test_refuses_symlink_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target.json"
            target.write_text("unchanged\n", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            result = run_script(
                REPEAT,
                "--runs",
                "1",
                "--out",
                str(link),
                "--",
                sys.executable,
                "-c",
                "print('ok')",
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged\n")


class CompareEvidenceTests(unittest.TestCase):
    def test_compares_deterministically_and_ignores_named_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good = root / "good.json"
            bad = root / "bad.json"
            good.write_text(
                json.dumps(
                    {"collected_at": "one", "service": {"port": 80, "state": "up"}}
                ),
                encoding="utf-8",
            )
            bad.write_text(
                json.dumps(
                    {"collected_at": "two", "service": {"port": 81, "state": "up"}}
                ),
                encoding="utf-8",
            )
            result = run_script(COMPARE, str(good), str(bad))
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["difference_count"], 1)
            self.assertEqual(payload["differences"][0]["path"], "/service/port")

            ignored = run_script(COMPARE, str(good), str(bad), "--ignore", "/service")
            self.assertEqual(json.loads(ignored.stdout)["difference_count"], 0)

    def test_preserves_json_structure_without_flattening_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good = root / "good.json"
            bad = root / "bad.json"
            good.write_text(json.dumps({"a.b": 1}), encoding="utf-8")
            bad.write_text(json.dumps({"a": {"b": 1}}), encoding="utf-8")
            dotted = json.loads(run_script(COMPARE, str(good), str(bad)).stdout)
            self.assertEqual(dotted["difference_count"], 2)
            self.assertEqual(
                {item["path"] for item in dotted["differences"]}, {"/a.b", "/a"}
            )

            good.write_text(json.dumps(["value"]), encoding="utf-8")
            bad.write_text(json.dumps({"0": "value"}), encoding="utf-8")
            typed = json.loads(run_script(COMPARE, str(good), str(bad)).stdout)
            self.assertEqual(typed["difference_count"], 1)
            self.assertEqual(typed["differences"][0]["path"], "")

            root_ignored = run_script(
                COMPARE,
                str(good),
                str(bad),
                "--ignore",
                "",
            )
            self.assertEqual(json.loads(root_ignored.stdout)["difference_count"], 0)

            good.write_text(json.dumps({"": 1}), encoding="utf-8")
            bad.write_text(json.dumps({"": 2}), encoding="utf-8")
            empty_key = json.loads(run_script(COMPARE, str(good), str(bad)).stdout)
            self.assertEqual(empty_key["differences"][0]["path"], "/")
            empty_key_ignored = run_script(
                COMPARE,
                str(good),
                str(bad),
                "--ignore",
                "/",
            )
            self.assertEqual(
                json.loads(empty_key_ignored.stdout)["difference_count"], 0
            )

    def test_bounds_reported_differences_but_counts_all(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good = root / "good.json"
            bad = root / "bad.json"
            good.write_text(json.dumps([0, 0, 0, 0]), encoding="utf-8")
            bad.write_text(json.dumps([1, 1, 1, 1]), encoding="utf-8")
            result = run_script(
                COMPARE,
                str(good),
                str(bad),
                "--max-differences",
                "2",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["difference_count"], 4)
            self.assertEqual(payload["reported_difference_count"], 2)
            self.assertTrue(payload["differences_truncated"])
            self.assertEqual(len(payload["differences"]), 2)

    def test_redacts_sensitive_values_and_refuses_symlink_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            marker = "synthetic-" + ("v" * 48)
            label = "".join(("TOK", "EN="))
            good = root / "good.json"
            bad = root / "bad.json"
            good.write_text(json.dumps({"value": "safe"}), encoding="utf-8")
            bad.write_text(json.dumps({"value": label + marker}), encoding="utf-8")
            result = run_script(COMPARE, str(good), str(bad))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(marker, result.stdout)
            self.assertIn("[REDACTED]", result.stdout)

            sensitive_key = "".join(("pass", "word"))
            good.write_text(json.dumps({sensitive_key: "old"}), encoding="utf-8")
            bad.write_text(json.dumps({sensitive_key: "new"}), encoding="utf-8")
            keyed = run_script(COMPARE, str(good), str(bad))
            self.assertNotIn('"old"', keyed.stdout)
            self.assertNotIn('"new"', keyed.stdout)

            endpoint = "https" + "://database.private.invalid/query"
            address = ".".join(("192", "168", "4", "12"))
            good.write_text(
                json.dumps({"endpoint": endpoint, "address": address}),
                encoding="utf-8",
            )
            bad.write_text(
                json.dumps({"endpoint": endpoint + "2", "address": address + "0"}),
                encoding="utf-8",
            )
            endpoint_result = run_script(COMPARE, str(good), str(bad))
            self.assertNotIn(endpoint, endpoint_result.stdout)
            self.assertNotIn(address, endpoint_result.stdout)
            self.assertIn("[ENDPOINT:", endpoint_result.stdout)

            short_fixture = "short-value"
            redacted_key = "database_password"
            good.write_text("{}", encoding="utf-8")
            bad.write_text(
                json.dumps({"nested": {redacted_key: short_fixture}}),
                encoding="utf-8",
            )
            nested = run_script(COMPARE, str(good), str(bad))
            self.assertNotIn(short_fixture, nested.stdout)
            self.assertIn("[REDACTED]", nested.stdout)

            quoted_fixture = " ".join(("quoted", "short", "value"))
            good.write_text(json.dumps({"value": "safe"}), encoding="utf-8")
            bad.write_text(
                json.dumps({"value": f'{redacted_key}="{quoted_fixture}"'}),
                encoding="utf-8",
            )
            quoted = run_script(COMPARE, str(good), str(bad))
            self.assertNotIn(quoted_fixture, quoted.stdout)

            private_key_name = "https" + "://service.internal.invalid/path"
            good.write_text(json.dumps({private_key_name: "one"}), encoding="utf-8")
            bad.write_text(json.dumps({private_key_name: "two"}), encoding="utf-8")
            private_path = run_script(COMPARE, str(good), str(bad))
            self.assertNotIn(private_key_name, private_path.stdout)
            self.assertIn("[ENDPOINT:", private_path.stdout)

            link = root / "linked.json"
            link.symlink_to(good)
            linked = run_script(COMPARE, str(link), str(bad))
            self.assertEqual(linked.returncode, 2)

    def test_rejects_malformed_json_and_symlink_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good = root / "good.json"
            bad = root / "bad.json"
            good.write_text("{not-json", encoding="utf-8")
            bad.write_text("{}", encoding="utf-8")
            malformed = run_script(COMPARE, str(good), str(bad))
            self.assertEqual(malformed.returncode, 2)

            good.write_text("{}", encoding="utf-8")
            target = root / "target.json"
            target.write_text("unchanged\n", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            result = run_script(COMPARE, str(good), str(bad), "--out", str(link))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged\n")

    def test_rejects_oversized_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good = root / "good.json"
            bad = root / "bad.json"
            good.write_text(json.dumps({"value": "x" * (11 * 1024 * 1024)}))
            bad.write_text("{}", encoding="utf-8")
            result = run_script(COMPARE, str(good), str(bad))
            self.assertEqual(result.returncode, 2)
            self.assertIn("input exceeds", result.stderr)

    def test_rejects_excessively_nested_input_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good = root / "good.json"
            bad = root / "bad.json"
            good.write_text("[" * 1500 + "0" + "]" * 1500, encoding="utf-8")
            bad.write_text("[]", encoding="utf-8")
            result = run_script(COMPARE, str(good), str(bad))
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
