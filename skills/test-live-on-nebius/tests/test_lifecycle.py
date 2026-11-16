"""Fault injection for ownership and cleanup; no credentials or network."""

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from admission import bind_dependencies, verify_dependencies
from live_run import execute, load_adapter, main, state_root, validate_plan
from live_state import Journal, StateError


def scenario(adapter="/unused/adapter.py"):
    return {
        "tenant": "tenant-example",
        "project": "project-example",
        "region": "region-example",
        "engine": "sdk",
        "source": "/source",
        "source_identity": {"revision": "frozen-test-fixture"},
        "objective": "verify guest checksum",
        "acceptance": ["checksum matches"],
        "resources": [{"kind": "vm", "count": 1, "owner": "sdk"}],
        "adapter": adapter,
        "adapter_sha256": hashlib.sha256(b"").hexdigest(),
    }


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        )
        self.addCleanup(self.temp.cleanup)
        self.j = Journal.create(self.temp.name, scenario())

    def test_intent_survives_crash_and_cannot_be_reissued(self):
        with self.j.lock():
            key = self.j.intent("vm", "hash")
        recovered = Journal(self.j.directory)
        with recovered.lock():
            self.assertEqual(recovered.data["operations"]["vm"]["idempotency_key"], key)
            with self.assertRaisesRegex(StateError, "RECONCILE"):
                recovered.intent("vm", "hash")
            with self.assertRaisesRegex(StateError, "OPERATIONS_UNRESOLVED"):
                recovered.finish_cleanup(writers_quiescent=True)

    def test_concurrent_resume_cleanup_excluded(self):
        other = Journal(self.j.directory)
        with (
            self.j.lock(),
            self.assertRaisesRegex(StateError, "RUN_BUSY"),
            other.lock(),
        ):
            pass

    def test_scope_owner_and_dependency_boundaries(self):
        with self.j.lock():
            with self.assertRaisesRegex(StateError, "RESOURCE_SCOPE"):
                self.j.own(
                    "foreign",
                    resource_id="x",
                    kind="vm",
                    parent="project-other",
                    owner="sdk",
                )
            self.j.own(
                "disk",
                resource_id="d",
                kind="disk",
                parent="project-example",
                owner="sdk",
            )
            self.j.own(
                "vm",
                resource_id="v",
                kind="vm",
                parent="project-example",
                owner="cxcli",
                dependencies=["disk"],
            )
            with self.assertRaisesRegex(StateError, "LIVE_DEPENDENTS"):
                self.j.deletion_target(
                    "disk",
                    observed_id="d",
                    observed_parent="project-example",
                    observed_run=self.j.data["run_id"],
                    owner="sdk",
                )
            with self.assertRaisesRegex(StateError, "FOREIGN_RESOURCE"):
                self.j.deletion_target(
                    "vm",
                    observed_id="v",
                    observed_parent="project-example",
                    observed_run=self.j.data["run_id"],
                    owner="sdk",
                )

    def test_cleanup_seals_provisioning_and_requires_absence(self):
        with self.j.lock():
            self.j.own(
                "vm", resource_id="v", kind="vm", parent="project-example", owner="sdk"
            )
            self.j.phase("cleanup")
            with self.assertRaisesRegex(StateError, "PROVISIONING_CLOSED"):
                self.j.intent("new", "hash")
            with self.assertRaisesRegex(StateError, "CLEANUP_INCOMPLETE"):
                self.j.finish_cleanup(writers_quiescent=True)
            self.j.absent("vm")
            with self.assertRaisesRegex(StateError, "CLEANUP_INCOMPLETE"):
                self.j.finish_cleanup(writers_quiescent=False)
            self.j.finish_cleanup(writers_quiescent=True)

    def test_quota_receipt_loss_requires_reconciliation(self):
        with self.j.lock():
            key = self.j.quota_intent("tenant/region", {"cpu": 4})
            self.assertEqual(
                self.j.data["quota_requests"][key]["status"], "submission_pending"
            )
            with self.assertRaisesRegex(StateError, "RECONCILE_QUOTA"):
                self.j.quota_intent("tenant/region", {"cpu": 4})

    def test_plan_tampering_and_symlink_rejected(self):
        self.j.data["plan"]["project"] = "project-other"
        with self.j.lock():
            self.j.data["plan"]["project"] = "project-other"
            with self.assertRaisesRegex(StateError, "PLAN_CHANGED"):
                self.j.save()
        link = Path(self.temp.name) / "link"
        link.symlink_to(self.j.directory, target_is_directory=True)
        with self.assertRaisesRegex(StateError, "SYMLINK"):
            Journal(link)

    def test_failed_test_still_cleans(self):
        adapter = NS(
            reconcile=Mock(),
            preflight=Mock(return_value=True),
            provision=Mock(),
            verify=Mock(side_effect=RuntimeError("secret sentinel")),
            cleanup=Mock(return_value=True),
        )
        self.assertEqual(execute(self.j, adapter), 1)
        self.assertTrue(self.j.data["cleanup_complete"])
        self.assertNotIn("secret sentinel", self.j.path.read_text())
        adapter.cleanup.assert_called_once()

    def test_cleanup_failure_is_not_success(self):
        adapter = NS(
            reconcile=Mock(),
            preflight=Mock(return_value=True),
            provision=Mock(),
            verify=Mock(return_value=True),
            cleanup=Mock(side_effect=TimeoutError()),
        )
        self.assertEqual(execute(self.j, adapter), 2)
        self.assertEqual(self.j.data["phase"], "cleanup_pending")

    def test_waiting_quota_creates_nothing_and_is_resumable(self):
        adapter = NS(
            reconcile=Mock(),
            preflight=Mock(return_value="awaiting_quota"),
            provision=Mock(),
            verify=Mock(),
            cleanup=Mock(),
        )
        self.assertEqual(execute(self.j, adapter), 3)
        adapter.provision.assert_not_called()
        adapter.cleanup.assert_not_called()
        adapter.preflight.return_value = True
        adapter.verify.return_value = True
        adapter.cleanup.return_value = True
        self.assertEqual(execute(self.j, adapter), 0)

    def test_cleanup_never_provisions(self):
        adapter = NS(
            reconcile=Mock(),
            preflight=Mock(),
            provision=Mock(),
            verify=Mock(),
            cleanup=Mock(return_value=True),
        )
        self.assertEqual(execute(self.j, adapter, cleanup_only=True), 1)
        adapter.preflight.assert_not_called()
        adapter.provision.assert_not_called()

    def test_invalid_resource_count_and_missing_acceptance(self):
        p = scenario()
        p["resources"][0]["count"] = -1
        with self.assertRaises(StateError):
            validate_plan(p)
        p = scenario()
        p["acceptance"] = []
        with self.assertRaises(StateError):
            validate_plan(p)

    def test_source_identity_is_required(self):
        for identity in ({}, None, "unbound"):
            p = scenario()
            p["source_identity"] = identity
            with (
                self.subTest(identity=identity),
                self.assertRaisesRegex(StateError, "SOURCE_IDENTITY_REQUIRED"),
            ):
                validate_plan(p)

    def test_recovery_rejects_all_supplied_options_even_defaults(self):
        run_id = "00000000-0000-4000-8000-000000000000"
        options = [
            ("--engine", "auto"),
            ("--engine", "sdk"),
            ("--timeout", "60m"),
            ("--timeout", "1s"),
            ("--quota-mode", "fail"),
            ("--quota-mode", "request"),
        ]
        for action in ("resume", "cleanup"):
            for flag, value in options:
                with (
                    self.subTest(action=action, flag=flag, value=value),
                    patch("live_run.Journal") as journal,
                    self.assertRaisesRegex(StateError, "RESUME_SCOPE_IMMUTABLE"),
                ):
                    main([action, run_id, flag, value])
                journal.assert_not_called()

    def test_native_host_resolution_uses_canonical_precedence_and_validation(self):
        with patch.dict(
            os.environ,
            {
                "CODEX_THREAD_ID": "native-codex",
                "SKILLS_AGENT": "claude",
                "CODEX_HOME": "/codex-example",
                "CLAUDE_CONFIG_DIR": "/claude-example",
            },
            clear=True,
        ):
            self.assertEqual(state_root(), Path("/codex-example/nebius-live-runs"))
        with patch.dict(
            os.environ,
            {"SKILLS_AGENT": "claude", "CLAUDE_CONFIG_DIR": "/claude-example"},
            clear=True,
        ):
            self.assertEqual(state_root(), Path("/claude-example/nebius-live-runs"))
        for env in ({"SKILLS_AGENT": "invalid"}, {"CODEX_HOME": "relative"}):
            with patch.dict(os.environ, env, clear=True), self.assertRaises(ValueError):
                state_root()

    def test_plan_does_not_import_adapter_or_write_run(self):
        file = Path(self.temp.name) / "plan.json"
        file.write_text(json.dumps(scenario()))
        before = set(Path(self.temp.name).iterdir())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "plan",
                        "--tenant",
                        "tenant-example",
                        "--project",
                        "project-example",
                        "--scenario",
                        str(file),
                    ]
                ),
                0,
            )
        self.assertEqual(before, set(Path(self.temp.name).iterdir()))

    def test_unresolved_create_cannot_wait_for_quota_or_provision(self):
        with self.j.lock():
            self.j.intent("vm", "hash")
        adapter = NS(
            reconcile=Mock(),
            preflight=Mock(return_value="awaiting_quota"),
            provision=Mock(),
            verify=Mock(),
            cleanup=Mock(return_value=True),
        )
        self.assertEqual(execute(self.j, adapter), 2)
        self.assertEqual(self.j.data["phase"], "cleanup_pending")
        adapter.preflight.assert_not_called()
        adapter.cleanup.assert_not_called()

    def test_deadline_prevents_following_stage(self):
        clock = [0]

        def slow(*_):
            clock[0] = 4000
            return True

        adapter = NS(
            reconcile=Mock(),
            preflight=slow,
            provision=Mock(),
            verify=Mock(),
            cleanup=Mock(return_value=True),
        )
        with patch("live_run.time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(execute(self.j, adapter), 1)
        adapter.provision.assert_not_called()
        adapter.cleanup.assert_called_once()

    def test_completed_and_locked_runs_never_import(self):
        with self.j.lock():
            with (
                patch("live_run.load_adapter") as load,
                self.assertRaisesRegex(StateError, "RUN_BUSY"),
            ):
                execute(Journal(self.j.directory))
            load.assert_not_called()
            self.j.finish_cleanup(writers_quiescent=True)
        with patch("live_run.load_adapter") as load:
            self.assertEqual(execute(self.j), 1)
        load.assert_not_called()

    def test_executes_exact_admitted_bytes_not_cached_bytecode(self):
        import os
        import py_compile

        path = Path(self.temp.name) / "adapter.py"
        suffix = "\n".join(
            "def " + name + "(*a): return True"
            for name in ("preflight", "provision", "verify", "reconcile", "cleanup")
        )
        path.write_text("value=1\n" + suffix)
        timestamp = path.stat().st_mtime
        py_compile.compile(str(path))
        path.write_text("value=2\n" + suffix)
        os.utime(path, (timestamp, timestamp))
        p = scenario(str(path))
        p["adapter_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        bind_dependencies(p)
        self.assertEqual(load_adapter(p).value, 2)

    def test_dependency_drift_blocks_before_adapter_or_stage_effects(self):
        path = Path(self.temp.name) / "adapter.py"
        dependency = Path(self.temp.name) / "helper.py"
        path.write_text("raise AssertionError('must not execute')")
        dependency.write_text("value=1")
        p = scenario(str(path))
        p["dependency_sha256"] = {
            str(dependency): hashlib.sha256(dependency.read_bytes()).hexdigest()
        }
        bind_dependencies(p)
        verify_dependencies(p)
        dependency.write_text("value=2")
        with self.assertRaisesRegex(StateError, "DEPENDENCY_CHANGED"):
            load_adapter(p)

    def test_help_without_sdk_or_credentials(self):
        script = Path(__file__).resolve().parents[1] / "scripts/live_run.py"
        result = subprocess.run(
            [sys.executable, "-B", str(script), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--quota-mode", result.stdout)


if __name__ == "__main__":
    unittest.main()
