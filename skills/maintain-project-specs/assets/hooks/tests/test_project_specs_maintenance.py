#!/usr/bin/env python3
"""Focused retention, pressure, and filesystem-safety tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "project_specs_maintenance.py"
SPEC = importlib.util.spec_from_file_location(
    "project_specs_maintenance_test", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintenance)


class ProjectSpecsMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "project-specs" / "workspace-abc"
        self.workspace.mkdir(parents=True, mode=0o700)
        for path in (self.root, self.root / "project-specs", self.workspace):
            os.chmod(path, 0o700)
        self.current = "current-session"
        self.current_state = self.workspace / self.current / "lifecycle.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_json(self, path: Path, value: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def session(
        self,
        name: str,
        phase: str,
        *,
        age_ns: int = 0,
        now_ns: int = 10_000_000_000_000_000,
    ) -> Path:
        root = self.workspace / name
        state: dict[str, object] = {
            "schema": maintenance.LIFECYCLE_SCHEMA,
            "project_scope": ".",
            "git_head_at_prompt": "a" * 40,
            "turn_sha256": "b" * 64,
            "phase": phase,
            "receipt_sha256": None,
            "requirements_sha256": None,
            "design_sha256": None,
            "rules_path": None,
            "rules_sha256": None,
            "project_instructions_state_sha256": None,
            "project_instructions_reload_required": None,
            "write_epoch": 0,
            "planned_write_epoch": None,
            "waiver": None,
        }
        if phase in {"planned", "implementation-open", "seal-armed", "sealed"}:
            state.update(
                {
                    "receipt_sha256": "c" * 64,
                    "rules_path": maintenance.LIFECYCLE_RULES_PATH,
                    "rules_sha256": "d" * 64,
                    "planned_write_epoch": 0,
                }
            )
        if phase == "sealed":
            state.update(
                {
                    "requirements_sha256": "e" * 64,
                    "design_sha256": "f" * 64,
                    "project_instructions_state_sha256": "1" * 64,
                    "project_instructions_reload_required": False,
                }
            )
        if phase == "waived":
            state["waiver"] = "read-only"
        self.write_json(
            root / "lifecycle.json",
            state,
        )
        self.write_json(
            root / "activity.json",
            {
                "schema": maintenance.ACTIVITY_SCHEMA,
                "last_seen_ns": now_ns - age_ns,
            },
        )
        return root

    @staticmethod
    def deletable(
        _session: Path, _workspace: Path, _remaining_ns: int
    ) -> tuple[str, int | None, str | None]:
        return "deletable", None, None

    def run_maintenance(self, *, now_ns: int) -> dict[str, object]:
        return maintenance.maintain_workspace(
            self.current_state,
            current_session=self.current,
            ownership_classifier=self.deletable,
            now_ns=now_ns,
        )

    def write_registry(self, generation: int) -> str:
        registry_root = self.workspace / "project-agent-ownership"
        registry_root.mkdir(mode=0o700, exist_ok=True)
        subject = {
            "project_root": "/project",
            "git_root": "/project",
            "project_scope": ".",
            "target_path": "/project/AGENTS.md",
        }
        key = hashlib.sha256(
            json.dumps(subject, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.write_json(
            registry_root / "registry.json",
            {
                "schema": maintenance.OWNERSHIP_REGISTRY_SCHEMA,
                "generation": generation,
                "entries": {
                    key: {
                        "generation": generation,
                        **subject,
                        "status": "blocked",
                        "target_sha256": "b" * 64,
                        "ownership": None,
                        "source_state_sha256": None,
                    }
                },
            },
        )
        snapshot = maintenance._ownership_registry_snapshot(self.workspace)
        assert snapshot is not None
        self.assertEqual(snapshot[0], generation)
        return snapshot[1]

    def test_more_than_128_sessions_uses_cursor_without_count_failure(self) -> None:
        now = 10_000_000_000_000_000
        for index in range(300):
            self.session(f"s-{index:03d}", "sealed", now_ns=now)
        first = self.run_maintenance(now_ns=now)
        self.assertIsNotNone(first["cursor_after"])
        second = self.run_maintenance(now_ns=now)
        self.assertIsNone(second["cursor_after"])
        self.assertEqual(second["last_error"], None)
        sessions = [
            path for path in self.workspace.iterdir() if path.name.startswith("s-")
        ]
        self.assertEqual(len(sessions), 300)

    def test_runtime_cutoff_resumes_after_last_visited_name(self) -> None:
        now = 10_000_000_000_000_000
        for name in ("a", "b", "c"):
            self.session(name, "sealed", now_ns=now)
        calls = 0

        def clock() -> int:
            nonlocal calls
            calls += 1
            return 0 if calls <= 7 else maintenance.MAX_RUNTIME_NS + 1

        with (
            mock.patch.object(maintenance.time, "monotonic_ns", side_effect=clock),
            mock.patch.object(maintenance, "_allocated_bytes", return_value=(1, True)),
        ):
            first = self.run_maintenance(now_ns=now)
        self.assertEqual(first["cursor_after"], "a")
        second = self.run_maintenance(now_ns=now)
        self.assertIsNone(second["cursor_after"])
        self.assertIsNotNone(second["last_completed_scan_ns"])

    def test_terminal_and_abandoned_planned_retention_delete_without_archive(
        self,
    ) -> None:
        now = 10_000_000_000_000_000
        terminal = self.session(
            "terminal", "sealed", age_ns=maintenance.TERMINAL_RETENTION_NS, now_ns=now
        )
        planned = self.session(
            "planned", "planned", age_ns=maintenance.UNFINISHED_RETENTION_NS, now_ns=now
        )
        self.run_maintenance(now_ns=now)
        self.assertFalse(terminal.exists())
        self.assertFalse(planned.exists())
        self.run_maintenance(now_ns=now)
        self.assertEqual(list((self.workspace / ".maintenance/staging").iterdir()), [])
        self.assertFalse((self.workspace / "archive").exists())

    def test_staged_bundle_is_counted_once_in_completed_scan(self) -> None:
        now = 10_000_000_000_000_000
        self.run_maintenance(now_ns=now)
        current = self.session(
            self.current,
            "sealed",
            age_ns=maintenance.TERMINAL_RETENTION_NS * 2,
            now_ns=now,
        )
        (current / "current-payload.bin").write_bytes(b"c" * 12288)
        candidate = self.session(
            "terminal",
            "sealed",
            age_ns=maintenance.TERMINAL_RETENTION_NS,
            now_ns=now,
        )
        (candidate / "payload.bin").write_bytes(b"x" * 8192)

        status = self.run_maintenance(now_ns=now)
        actual, safe = maintenance._allocated_bytes(self.workspace)

        self.assertTrue(safe)
        self.assertEqual(int(status["allocated_bytes"]), actual)
        self.assertTrue(current.exists())
        self.assertEqual(
            status["protected"].get("current-session"),
            1,
        )

    def test_missing_activity_current_and_seal_armed_are_protected(self) -> None:
        now = 10_000_000_000_000_000
        missing = self.session(
            "missing-activity",
            "waived",
            age_ns=maintenance.TERMINAL_RETENTION_NS,
            now_ns=now,
        )
        (missing / "activity.json").unlink()
        armed = self.session(
            "armed",
            "seal-armed",
            age_ns=maintenance.UNFINISHED_RETENTION_NS * 2,
            now_ns=now,
        )
        current = self.session(
            self.current,
            "sealed",
            age_ns=maintenance.TERMINAL_RETENTION_NS * 2,
            now_ns=now,
        )
        self.run_maintenance(now_ns=now)
        self.assertTrue(missing.exists())
        self.assertTrue((missing / "activity.json").is_file())
        self.assertTrue(armed.exists())
        self.assertTrue(current.exists())

    def test_future_and_permission_unsafe_activity_are_protected(self) -> None:
        now = 10_000_000_000_000_000
        future = self.session("future", "sealed", now_ns=now)
        self.write_json(
            future / "activity.json",
            {
                "schema": maintenance.ACTIVITY_SCHEMA,
                "last_seen_ns": now + maintenance.FUTURE_TOLERANCE_NS + 1,
            },
        )
        unsafe = self.session(
            "unsafe-activity",
            "sealed",
            age_ns=maintenance.TERMINAL_RETENTION_NS,
            now_ns=now,
        )
        (unsafe / "activity.json").chmod(0o644)
        self.run_maintenance(now_ns=now)
        self.assertTrue(future.exists())
        self.assertTrue(unsafe.exists())

    def test_incomplete_lifecycle_record_is_protected(self) -> None:
        now = 10_000_000_000_000_000
        candidate = self.session(
            "incomplete-lifecycle",
            "sealed",
            age_ns=maintenance.TERMINAL_RETENTION_NS,
            now_ns=now,
        )
        self.write_json(
            candidate / "lifecycle.json",
            {"schema": maintenance.LIFECYCLE_SCHEMA, "phase": "sealed"},
        )
        status = self.run_maintenance(now_ns=now)
        self.assertTrue(candidate.exists())
        self.assertGreaterEqual(dict(status["protected"]).get("unsafe-lifecycle", 0), 1)

    def test_pressure_uses_hysteresis_for_fresh_terminal_bundle(self) -> None:
        now = 10_000_000_000_000_000
        candidate = self.session("fresh-terminal", "sealed", now_ns=now)
        (candidate / "payload.bin").write_bytes(b"x" * 8192)
        with (
            mock.patch.object(maintenance, "HIGH_WATER_BYTES", 1),
            mock.patch.object(maintenance, "LOW_WATER_BYTES", 0),
        ):
            first = self.run_maintenance(now_ns=now)
            self.assertTrue(first["pressure"])
            self.assertTrue(candidate.exists())
            self.run_maintenance(now_ns=now)
            self.assertFalse(candidate.exists())

    def test_malformed_status_cannot_authorize_pressure_cleanup(self) -> None:
        now = 10_000_000_000_000_000
        self.run_maintenance(now_ns=now)
        candidate = self.session("fresh-terminal", "sealed", now_ns=now)
        status = maintenance._default_status()
        status["pressure"] = "yes"
        status["aggregate_allocated_bytes"] = maintenance.HIGH_WATER_BYTES * 2
        self.write_json(self.workspace / ".maintenance/status.json", status)

        observed = self.run_maintenance(now_ns=now)

        self.assertTrue(candidate.exists())
        self.assertIs(observed["pressure"], False)
        self.assertIsNone(observed["last_error"])

    def test_malformed_aggregate_cannot_reuse_prior_pressure(self) -> None:
        now = 10_000_000_000_000_000
        candidate = self.session("fresh-terminal", "sealed", now_ns=now)
        with (
            mock.patch.object(maintenance, "HIGH_WATER_BYTES", 1),
            mock.patch.object(maintenance, "LOW_WATER_BYTES", 0),
        ):
            first = self.run_maintenance(now_ns=now)
            self.assertTrue(first["pressure"])
            self.write_json(
                self.workspace.parent / ".maintenance/usage.json",
                {"schema": maintenance.USAGE_SCHEMA, "workspaces": "invalid"},
            )
            self.run_maintenance(now_ns=now)

        self.assertTrue(candidate.exists())

    def test_pressure_orders_terminal_before_expired_unfinished(self) -> None:
        now = 10_000_000_000_000_000
        self.session(
            "a-unfinished",
            "planned",
            now_ns=now,
        )
        self.session("z-terminal", "sealed", now_ns=now)
        calls: list[str] = []

        def classify(
            session: Path, _workspace: Path, _remaining_ns: int
        ) -> tuple[str, int | None, str | None]:
            calls.append(session.name)
            return "deletable", None, None

        with (
            mock.patch.object(maintenance, "HIGH_WATER_BYTES", 1),
            mock.patch.object(maintenance, "LOW_WATER_BYTES", 0),
        ):
            maintenance.maintain_workspace(
                self.current_state,
                current_session=self.current,
                ownership_classifier=classify,
                now_ns=now,
            )
            self.write_json(
                self.workspace / "a-unfinished/activity.json",
                {
                    "schema": maintenance.ACTIVITY_SCHEMA,
                    "last_seen_ns": now - maintenance.UNFINISHED_RETENTION_NS,
                },
            )
            calls.clear()
            maintenance.maintain_workspace(
                self.current_state,
                current_session=self.current,
                ownership_classifier=classify,
                now_ns=now,
            )
        self.assertEqual(calls[:2], ["z-terminal", "a-unfinished"])

    def test_pressure_orders_oldest_across_more_than_one_slice(self) -> None:
        now = 10_000_000_000_000_000
        for index in range(maintenance.MAX_VISITS + 1):
            self.session(f"a-fresh-{index:03d}", "sealed", now_ns=now)
        oldest = self.session(
            "z-oldest",
            "sealed",
            age_ns=maintenance.TERMINAL_RETENTION_NS - 1,
            now_ns=now,
        )

        with (
            mock.patch.object(maintenance, "HIGH_WATER_BYTES", 1),
            mock.patch.object(maintenance, "LOW_WATER_BYTES", 0),
        ):
            self.run_maintenance(now_ns=now)
            self.run_maintenance(now_ns=now)
            self.run_maintenance(now_ns=now)

        self.assertFalse(oldest.exists())

    def test_aggregate_usage_sums_completed_workspace_scans(self) -> None:
        now = 10_000_000_000_000_000
        self.session("one", "sealed", now_ns=now)
        first = self.run_maintenance(now_ns=now)
        other_workspace = self.workspace.parent / "workspace-other"
        other_state = other_workspace / "current-other" / "lifecycle.json"
        self.write_json(
            other_workspace / "one" / "lifecycle.json",
            {
                **json.loads((self.workspace / "one/lifecycle.json").read_text()),
            },
        )
        self.write_json(
            other_workspace / "one" / "activity.json",
            {"schema": maintenance.ACTIVITY_SCHEMA, "last_seen_ns": now},
        )
        os.chmod(other_workspace, 0o700)
        second = maintenance.maintain_workspace(
            other_state,
            current_session="current-other",
            ownership_classifier=self.deletable,
            now_ns=now,
        )
        self.assertGreaterEqual(
            int(second["aggregate_allocated_bytes"]),
            int(first["allocated_bytes"]) + int(second["allocated_bytes"]),
        )
        self.assertTrue(second["aggregate_complete"])

    def test_partial_root_inventory_cannot_enable_pressure(self) -> None:
        now = 10_000_000_000_000_000
        candidate = self.session("fresh-terminal", "sealed", now_ns=now)
        other_workspace = self.workspace.parent / "workspace-dormant"
        other_workspace.mkdir(mode=0o700)
        (other_workspace / "payload.bin").write_bytes(b"x" * 8192)

        with mock.patch.object(maintenance, "HIGH_WATER_BYTES", 1):
            status = self.run_maintenance(now_ns=now)

        self.assertTrue(candidate.exists())
        self.assertFalse(status["aggregate_complete"])
        self.assertFalse(status["pressure"])

    def test_root_cursor_eventually_maintains_dormant_workspace(self) -> None:
        now = 10_000_000_000_000_000
        other_workspace = self.workspace.parent / "workspace-dormant"
        other_workspace.mkdir(mode=0o700)
        original_workspace = self.workspace
        try:
            self.workspace = other_workspace
            candidate = self.session(
                "old-terminal",
                "sealed",
                age_ns=maintenance.TERMINAL_RETENTION_NS,
                now_ns=now,
            )
        finally:
            self.workspace = original_workspace

        maintenance.maintain_project_specs_root(
            self.current_state,
            current_session=self.current,
            ownership_classifier=self.deletable,
        )
        maintenance.maintain_project_specs_root(
            self.current_state,
            current_session=self.current,
            ownership_classifier=self.deletable,
        )

        self.assertFalse(candidate.exists())

    def test_hard_linked_authoritative_file_is_never_staged(self) -> None:
        now = 10_000_000_000_000_000
        candidate = self.session(
            "hard-linked",
            "sealed",
            age_ns=maintenance.TERMINAL_RETENTION_NS,
            now_ns=now,
        )
        os.link(candidate / "lifecycle.json", candidate / "copy.json")
        status = self.run_maintenance(now_ns=now)
        self.assertTrue(candidate.exists())
        self.assertGreaterEqual(
            dict(status["protected"]).get("unsafe-filesystem", 0), 1
        )

    def test_workspace_writer_lock_makes_maintenance_non_blocking(self) -> None:
        self.session("retained", "sealed")
        entered = threading.Event()
        release = threading.Event()

        def writer() -> None:
            with maintenance.session_locks(self.current_state):
                entered.set()
                release.wait(timeout=5)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(writer)
            self.assertTrue(entered.wait(timeout=5))
            status = self.run_maintenance(now_ns=10_000_000_000_000_000)
            self.assertTrue(status["lock_contended"])
            release.set()
            future.result(timeout=5)

    def test_inherited_session_lock_lease_avoids_child_self_deadlock(self) -> None:
        private_root = self.current_state.parent / "project-instructions"
        private_root.mkdir(parents=True, mode=0o700)
        child = """
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("maintenance_child", sys.argv[1])
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
keywords = {}
if len(sys.argv) == 5:
    keywords = {
        "workspace_lock_fd": int(sys.argv[3]),
        "session_lock_fd": int(sys.argv[4]),
    }
with module.workspace_operation_lock(Path(sys.argv[2]), **keywords):
    pass
"""
        command = [sys.executable, "-c", child, str(MODULE_PATH), str(private_root)]
        with maintenance.session_locks(self.current_state) as lock_fds:
            with self.assertRaises(subprocess.TimeoutExpired):
                subprocess.run(
                    command,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=0.5,
                )
            completed = subprocess.run(
                [*command, *(str(descriptor) for descriptor in lock_fds)],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                pass_fds=lock_fds,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        subprocess.run(command, check=True, timeout=5)

    def test_malformed_journal_cannot_escape_staging(self) -> None:
        now = 10_000_000_000_000_000
        self.run_maintenance(now_ns=now)
        victim = self.workspace / "victim"
        victim.mkdir(mode=0o700)
        self.write_json(victim / "evidence.json", {"keep": True})
        metadata = victim.lstat()
        journal = self.workspace / ".maintenance/journals/forged.json"
        self.write_json(
            journal,
            {
                "schema": maintenance.JOURNAL_SCHEMA,
                "session": "victim",
                "stage": "../../victim",
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "allocated_bytes": 1,
                "disposition": "deletable",
                "registry_generation": None,
                "registry_sha256": None,
            },
        )
        self.run_maintenance(now_ns=now)
        self.assertTrue((victim / "evidence.json").is_file())
        self.assertTrue(journal.is_file())

    def test_malformed_journals_cannot_starve_later_recovery(self) -> None:
        now = 10_000_000_000_000_000
        self.run_maintenance(now_ns=now)
        journals = self.workspace / ".maintenance/journals"
        for index in range(maintenance.MAX_VISITS):
            self.write_json(journals / f"000-{index:03d}.json", {"schema": "invalid"})
        candidate = self.session(
            "starved",
            "sealed",
            age_ns=maintenance.TERMINAL_RETENTION_NS,
            now_ns=now,
        )

        self.run_maintenance(now_ns=now)
        self.assertFalse(candidate.exists())
        self.assertEqual(
            len(list((self.workspace / ".maintenance/staging").iterdir())), 1
        )

        self.run_maintenance(now_ns=now)

        self.assertEqual(list((self.workspace / ".maintenance/staging").iterdir()), [])
        self.assertEqual(len(list(journals.iterdir())), maintenance.MAX_VISITS)

    def test_registry_generation_drift_restores_staged_bundle(self) -> None:
        now = 10_000_000_000_000_000
        candidate = self.session(
            "registry-drift",
            "sealed",
            age_ns=maintenance.TERMINAL_RETENTION_NS,
            now_ns=now,
        )
        registry_sha256 = self.write_registry(7)
        maintenance.maintain_workspace(
            self.current_state,
            current_session=self.current,
            ownership_classifier=lambda _session, _workspace, _remaining_ns: (
                "deletable",
                7,
                registry_sha256,
            ),
            now_ns=now,
        )
        self.assertFalse(candidate.exists())
        self.write_registry(8)

        status = maintenance.maintain_workspace(
            self.current_state,
            current_session=self.current,
            ownership_classifier=lambda _session, _workspace, _remaining_ns: (
                "unsafe",
                8,
                None,
            ),
            now_ns=now,
        )

        self.assertTrue(candidate.exists())
        self.assertEqual(list((self.workspace / ".maintenance/staging").iterdir()), [])
        self.assertEqual(list((self.workspace / ".maintenance/journals").iterdir()), [])
        self.assertGreaterEqual(dict(status["protected"]).get("unsafe", 0), 1)

    def test_matching_registry_generation_resumes_deletion(self) -> None:
        now = 10_000_000_000_000_000
        candidate = self.session(
            "registry-stable",
            "sealed",
            age_ns=maintenance.TERMINAL_RETENTION_NS,
            now_ns=now,
        )
        registry_sha256 = self.write_registry(7)
        maintenance.maintain_workspace(
            self.current_state,
            current_session=self.current,
            ownership_classifier=lambda _session, _workspace, _remaining_ns: (
                "deletable",
                7,
                registry_sha256,
            ),
            now_ns=now,
        )
        maintenance.maintain_workspace(
            self.current_state,
            current_session=self.current,
            ownership_classifier=lambda _session, _workspace, _remaining_ns: (
                "unsafe",
                7,
                None,
            ),
            now_ns=now,
        )
        self.assertFalse(candidate.exists())
        self.assertEqual(list((self.workspace / ".maintenance/staging").iterdir()), [])
        self.assertEqual(list((self.workspace / ".maintenance/journals").iterdir()), [])

    def test_same_generation_registry_change_restores_staged_bundle(self) -> None:
        now = 10_000_000_000_000_000
        candidate = self.session(
            "registry-changed",
            "sealed",
            age_ns=maintenance.TERMINAL_RETENTION_NS,
            now_ns=now,
        )
        registry_sha256 = self.write_registry(7)
        maintenance.maintain_workspace(
            self.current_state,
            current_session=self.current,
            ownership_classifier=lambda _session, _workspace, _remaining_ns: (
                "deletable",
                7,
                registry_sha256,
            ),
            now_ns=now,
        )
        registry_path = self.workspace / "project-agent-ownership/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        next(iter(registry["entries"].values()))["target_sha256"] = None
        self.write_json(registry_path, registry)

        maintenance.maintain_workspace(
            self.current_state,
            current_session=self.current,
            ownership_classifier=lambda _session, _workspace, _remaining_ns: (
                "unsafe",
                7,
                None,
            ),
            now_ns=now,
        )

        self.assertTrue(candidate.exists())
        self.assertEqual(list((self.workspace / ".maintenance/staging").iterdir()), [])
        self.assertEqual(list((self.workspace / ".maintenance/journals").iterdir()), [])

    def test_lock_setup_never_tightens_an_untrusted_ancestor(self) -> None:
        unsafe_home = self.root / "unsafe-home"
        unsafe_home.mkdir(mode=0o755)
        state = unsafe_home / "project-specs/workspace/session/lifecycle.json"
        with maintenance.session_locks(state):
            pass
        self.assertEqual(unsafe_home.stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
