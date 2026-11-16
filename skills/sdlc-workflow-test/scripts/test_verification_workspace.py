"""Real owned-Git fixture teardown, replay and exclusion boundaries."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import verify_agentic_sdlc as verifier
import verification_workspace as workspace
import three_tier_lifecycle as live
from git_fixture_environment import isolated_git_configuration


class WorkspaceTests(unittest.TestCase):
    def test_standalone_cli_uses_one_reentrant_workspace_owner(self):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(Path(workspace.__file__)),
                "--verification-root",
                str(self.root),
                "destroy",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "DESTROYED")
        self.assertFalse((self.root / "disposable-project").exists())

    def setUp(self):
        self.enterContext(isolated_git_configuration())
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp).resolve() / "verification"
        self.ctx = verifier.setup_context(
            verifier.parse_args(["--verification-root", str(self.root)])
        )
        self.assertIsNone(verifier.prepare_verification_root(self.ctx, self.root))
        self.assertTrue(verifier.setup_disposable_project(self.ctx), self.ctx.checks)
        self.report = self.root / "report.md"
        verifier.write_report(self.ctx, self.report)

    def test_lightweight_destroy_preserves_report_and_is_idempotent(self):
        before = self.report.read_bytes()
        self.assertEqual(workspace.destroy(self.root)["status"], "DESTROYED")
        self.assertEqual(self.report.read_bytes(), before)
        self.assertFalse((self.root / "disposable-project").exists())
        self.assertFalse((self.root / "origin.git").exists())
        self.assertEqual(workspace.destroy(self.root)["status"], "ALREADY_DESTROYED")
        self.assertTrue((self.root / workspace.ARCHIVE).is_dir())

    def test_missing_profile_is_preserved(self):
        profile = self.root / ".sdlc-git-environment/context.json"
        profile.unlink()
        with self.assertRaises((workspace.WorkspaceError, ValueError)):
            workspace.destroy(self.root)
        self.assertTrue((self.root / "disposable-project/.git").is_dir())

    def test_unreceipted_remnants_are_not_adopted(self):
        (self.root / workspace.RECEIPT).unlink()
        with self.assertRaisesRegex(workspace.WorkspaceError, "remnants"):
            workspace.destroy(self.root)
        self.assertTrue((self.root / "origin.git").is_dir())

    def test_dirty_project_is_preserved(self):
        (self.root / "disposable-project/foreign.txt").write_text("preserve")
        with self.assertRaises(ValueError):
            workspace.destroy(self.root)
        self.assertTrue((self.root / "disposable-project/foreign.txt").exists())

    def test_replaced_auxiliary_directory_is_rejected(self):
        path = self.root / "fixture-agent-home"
        path.rename(self.root / "former-home")
        path.mkdir()
        with self.assertRaisesRegex(workspace.WorkspaceError, "replaced"):
            workspace.destroy(self.root)
        self.assertTrue((self.root / "disposable-project").exists())

    def test_changed_catalog_preserves_owned_context_and_cleanup(self):
        context = self.root / "verification-context.json"
        before = context.read_bytes(), workspace._identity(context)
        with mock.patch.object(verifier, "verification_id", return_value="f" * 64):
            self.assertFalse(verifier.setup_disposable_project(self.ctx))
        self.assertEqual((context.read_bytes(), workspace._identity(context)), before)
        self.assertEqual(workspace.destroy(self.root)["status"], "DESTROYED")

    def _replaced_context(self):
        context = self.root / "verification-context.json"
        archive = self.root / workspace.ARCHIVE
        archive.mkdir(mode=0o700, exist_ok=True)
        original = archive / "original-context.json"
        original.write_bytes(context.read_bytes())
        original.chmod(0o600)
        changed = workspace._json(context)
        changed["verification_id"] = "f" * 64
        verifier.write_private_json(context, changed)
        return original

    def test_restore_context_requires_original_hash_and_all_other_identities(self):
        original = self._replaced_context()
        with self.assertRaisesRegex(workspace.WorkspaceError, "replaced"):
            workspace.destroy(self.root)
        before_report = self.report.read_bytes()
        result = workspace.restore_context(self.root, original)
        self.assertEqual(result["status"], "RESTORED")
        self.assertEqual((self.root / "verification-context.json").read_bytes(), original.read_bytes())
        self.assertEqual(workspace.restore_context(self.root, original)["status"], "ALREADY_RESTORED")
        self.assertEqual(workspace.destroy(self.root)["status"], "DESTROYED")
        self.assertEqual(self.report.read_bytes(), before_report)
        self.assertTrue(original.is_file())

    def test_restore_context_rejects_wrong_original_and_foreign_path(self):
        original = self._replaced_context()
        before = (self.root / "verification-context.json").read_bytes()
        original.write_text("{}\n")
        with self.assertRaises(workspace.WorkspaceError):
            workspace.restore_context(self.root, original)
        self.assertEqual((self.root / "verification-context.json").read_bytes(), before)
        with self.assertRaises(workspace.WorkspaceError):
            workspace.restore_context(self.root, Path(self.temp) / "foreign.json")

    def test_restore_context_rejects_replaced_directory(self):
        original = self._replaced_context()
        path = self.root / "fixture-agent-home"
        path.rename(self.root / "old-home")
        path.mkdir()
        with self.assertRaisesRegex(workspace.WorkspaceError, "replaced"):
            workspace.restore_context(self.root, original)

    def test_restore_context_rejects_changed_scope_or_live_evidence(self):
        original = self._replaced_context()
        path = self.root / "verification-context.json"
        changed = workspace._json(path)
        changed["project_root"] = str(self.root / "foreign")
        verifier.write_private_json(path, changed)
        with self.assertRaises(workspace.WorkspaceError):
            workspace.restore_context(self.root, original)
        changed = workspace._json(original)
        changed["verification_id"] = "f" * 64
        verifier.write_private_json(path, changed)
        (self.root / "live-results.json").write_text("{}\n")
        with self.assertRaises(workspace.WorkspaceError):
            workspace.restore_context(self.root, original)

    def test_restore_context_replays_interrupted_receipt_update(self):
        original = self._replaced_context()
        write = workspace._write

        def interrupt(path, value, **kwargs):
            if path == self.root / workspace.RECEIPT:
                raise OSError("injected receipt interruption")
            return write(path, value, **kwargs)

        with mock.patch.object(workspace, "_write", side_effect=interrupt):
            with self.assertRaisesRegex(OSError, "injected"):
                workspace.restore_context(self.root, original)
        self.assertEqual(workspace.restore_context(self.root, original)["status"], "RESTORED")
        self.assertEqual(workspace.destroy(self.root)["status"], "DESTROYED")

    def test_restore_context_rejects_live_fixture_and_dirty_project(self):
        original = self._replaced_context()
        active = self.root / "three-tier-live/active.json"
        active.parent.mkdir()
        active.write_text("{}\n")
        with self.assertRaisesRegex(workspace.WorkspaceError, "no live fixture"):
            workspace.restore_context(self.root, original)
        active.unlink()
        (self.root / "disposable-project/unknown.txt").write_text("preserve\n")
        with self.assertRaises(ValueError):
            workspace.restore_context(self.root, original)

    def test_restore_context_cli_uses_the_owned_archive(self):
        original = self._replaced_context()
        result = subprocess.run(
            [sys.executable, "-B", str(Path(workspace.__file__)), "--verification-root",
             str(self.root), "restore-context", "--original-context", str(original)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "RESTORED")
        self.assertEqual(workspace.destroy(self.root)["status"], "DESTROYED")

    def test_restore_context_rejects_symlinked_live_boundary(self):
        original = self._replaced_context()
        other = Path(self.temp) / "foreign-live"
        other.mkdir()
        (other / "runs").mkdir()
        (self.root / "three-tier-live").symlink_to(other, target_is_directory=True)
        before = (self.root / "verification-context.json").read_bytes()
        with self.assertRaises(workspace.WorkspaceError):
            workspace.restore_context(self.root, original)
        self.assertEqual((self.root / "verification-context.json").read_bytes(), before)

    def test_symlink_is_rejected_without_following_it(self):
        other = Path(self.temp) / "unrelated"
        other.write_text("preserve")
        (self.root / "evidence/redirect").symlink_to(other)
        with self.assertRaises(workspace.WorkspaceError):
            workspace.destroy(self.root)
        self.assertEqual(other.read_text(), "preserve")

    def test_interrupted_deletion_resumes_from_checkpoint(self):
        original = workspace._delete_checked
        count = 0

        def interrupt(*args):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError("injected interruption")
            return original(*args)

        with (
            mock.patch.object(workspace, "_delete_checked", side_effect=interrupt),
            self.assertRaises(OSError),
        ):
            workspace.destroy(self.root)
        self.assertEqual(workspace.destroy(self.root)["status"], "DESTROYED")
        journal = json.loads((self.root / workspace.JOURNAL).read_text())
        self.assertEqual(journal["status"], "DESTROYED")
        self.assertEqual(len(journal["removed"]), len(set(journal["removed"])))

    def test_changed_tree_after_checkpoint_is_preserved(self):
        with (
            mock.patch.object(
                workspace, "_delete_checked", side_effect=OSError("interrupt")
            ),
            self.assertRaises(OSError),
        ):
            workspace.destroy(self.root)
        (self.root / "evidence/new.json").write_text("changed")
        with self.assertRaisesRegex(workspace.WorkspaceError, "changed"):
            workspace.destroy(self.root)
        self.assertTrue((self.root / "evidence/new.json").exists())

    def test_orphaned_live_run_prevents_false_absence(self):
        orphan = self.root / "three-tier-live/runs/orphan"
        orphan.mkdir(parents=True)
        with self.assertRaisesRegex(workspace.WorkspaceError, "Orphaned"):
            workspace.destroy(self.root)

    def test_live_failure_does_not_claim_aggregate_cleanup(self):
        with (
            mock.patch.object(
                live, "destroy", side_effect=live.LifecycleError("blocked")
            ),
            self.assertRaises(live.LifecycleError),
        ):
            workspace.destroy(self.root)
        self.assertTrue((self.root / "disposable-project").is_dir())
        self.assertNotEqual(
            json.loads((self.root / workspace.JOURNAL).read_text())["status"],
            "DESTROYED",
        )

    def test_live_only_destroy_delegates_and_combined_status_is_explicit(self):
        workspace.destroy(self.root)
        with mock.patch.object(
            live, "destroy", return_value=("DESTROYED", {})
        ) as owner:
            result = workspace.destroy(self.root)
        owner.assert_called_once_with(self.root)
        self.assertEqual(
            result,
            {"status": "DESTROYED", "lightweight": "ABSENT", "live": "DESTROYED"},
        )

    def test_report_cannot_overlap_owned_raw_state(self):
        with self.assertRaisesRegex(workspace.WorkspaceError, "overlaps"):
            workspace.register_fixture(
                self.root, fresh=False, report=self.root / "evidence/report.md"
            )

    def test_native_launcher_process_blocks_cleanup(self):
        workspace.register_native(self.root)
        with self.assertRaisesRegex(workspace.WorkspaceError, "native coordinator"):
            workspace.destroy(self.root)

    def test_other_process_cannot_mutate_during_workspace_operation(self):
        script = "import sys; from pathlib import Path; import verification_workspace as w; w.destroy(Path(sys.argv[1]))"
        with workspace.operation(self.root):
            result = subprocess.run(
                [sys.executable, "-B", "-c", script, str(self.root)],
                cwd=Path(__file__).parent,
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("WORKSPACE_BUSY", result.stderr)
        self.assertTrue((self.root / "disposable-project").exists())

    def test_main_rejects_report_over_ownership_before_any_suite(self):
        marker = self.root / workspace.ROOT_MARKER
        before = marker.read_bytes()
        with mock.patch.object(verifier, "check_capability_regressions") as suites:
            status = verifier.main(
                ["--verification-root", str(self.root), "--report", str(marker)]
            )
        self.assertEqual(status, 2)
        suites.assert_not_called()
        self.assertEqual(marker.read_bytes(), before)

    def test_unbound_checkpoint_is_reconciled_only_when_exact(self):
        original = workspace._write

        def fail_journal(path, *args, **kwargs):
            if path.name == workspace.JOURNAL:
                # Force stale Git stat data without changing owned file bytes.
                tracked = self.ctx.disposable_project / workspace.FIXTURE_MARKER
                info = tracked.stat()
                os.utime(tracked, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
                raise OSError("injected snapshot-to-journal interruption")
            return original(path, *args, **kwargs)

        with (
            mock.patch.object(workspace, "_write", side_effect=fail_journal),
            self.assertRaises(OSError),
        ):
            workspace.destroy(self.root)
        self.assertEqual(workspace.destroy(self.root)["status"], "DESTROYED")

    def test_resigned_checkpoint_cannot_delete_retained_report(self):
        with (
            mock.patch.object(
                workspace, "_delete_checked", side_effect=OSError("interrupt")
            ),
            self.assertRaises(OSError),
        ):
            workspace.destroy(self.root)
        journal = workspace._json(self.root / workspace.JOURNAL)
        checkpoint = self.root / workspace.ARCHIVE / journal["checkpoint"]
        snapshot = workspace._json(checkpoint)
        snapshot["targets"].append("report.md")
        snapshot["entries"].update(workspace._tree(self.root, ["report.md"]))
        workspace._write(checkpoint, snapshot)
        journal["sha256"] = workspace._digest(snapshot)
        workspace._write(self.root / workspace.JOURNAL, journal)
        with self.assertRaisesRegex(workspace.WorkspaceError, "scope"):
            workspace.destroy(self.root)
        self.assertTrue(self.report.is_file())

    def test_custom_context_bound_manifest_is_removed(self):
        root = Path(self.temp).resolve() / "custom"
        ctx = verifier.setup_context(
            verifier.parse_args(
                [
                    "--verification-root",
                    str(root),
                    "--live-evidence",
                    str(root / "custom.json"),
                ]
            )
        )
        self.assertIsNone(verifier.prepare_verification_root(ctx, root))
        self.assertTrue(verifier.setup_disposable_project(ctx), ctx.checks)
        context = workspace._json(root / "verification-context.json")
        workspace._write(
            root / "custom.json",
            {
                "schema": "agentic-sdlc/verification-live-results-v3",
                "verification_id": context["verification_id"],
            },
        )
        self.assertEqual(workspace.destroy(root)["status"], "DESTROYED")
        self.assertFalse((root / "custom.json").exists())

    def test_nested_manifest_preserves_parent_and_unrelated_sibling(self):
        root = Path(self.temp).resolve() / "custom"
        ctx = verifier.setup_context(
            verifier.parse_args(
                [
                    "--verification-root",
                    str(root),
                    "--live-evidence",
                    str(root / "results/custom.json"),
                ]
            )
        )
        self.assertIsNone(verifier.prepare_verification_root(ctx, root))
        self.assertTrue(verifier.setup_disposable_project(ctx), ctx.checks)
        context = workspace._json(root / "verification-context.json")
        (root / "results").mkdir(mode=0o700)
        (root / "results/keep.txt").write_text("unrelated")
        workspace._write(
            root / "results/custom.json",
            {
                "schema": "agentic-sdlc/verification-live-results-v3",
                "verification_id": context["verification_id"],
            },
        )
        self.assertEqual(workspace.destroy(root)["status"], "DESTROYED")
        self.assertFalse((root / "results/custom.json").exists())
        self.assertEqual((root / "results/keep.txt").read_text(), "unrelated")

    def test_file_changed_during_live_cleanup_is_preserved(self):
        target = self.root / "evidence/observation.json"
        target.write_text("original")
        target.chmod(0o600)

        def mutate(_root):
            target.write_text("changed")
            return "ALREADY_DESTROYED", None

        with (
            mock.patch.object(live, "destroy", side_effect=mutate),
            self.assertRaisesRegex(workspace.WorkspaceError, "changed"),
        ):
            workspace.destroy(self.root)
        self.assertEqual(target.read_text(), "changed")

    def test_missing_native_launcher_proof_blocks_started_product(self):
        state = {
            "private_root": str(self.root / "private"),
            "evidence_root": str(self.root / "evidence"),
            "workflow_startup": {"path": "started"},
        }
        active = live.paths(self.root)["active"]
        active.parent.mkdir(exist_ok=True)
        active.write_text("placeholder")
        with (
            mock.patch.object(live, "load_active", return_value=(self.root, state)),
            self.assertRaisesRegex(workspace.WorkspaceError, "launcher ownership"),
        ):
            with workspace.product_quiescence(self.root, live):
                self.fail("must not admit cleanup")


class CompanionBoundaryTests(unittest.TestCase):
    def test_cleanup_checkpoint_survives_partial_companion_evidence_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.json"
            active.touch()
            companion = root / "evidence/companion"
            companion.mkdir(parents=True)
            (companion / "launch.json").write_text("{}")
            state = {"filesystem_cleanup": {}, "private_root": str(root / "private"),
                     "evidence_root": str(root / "evidence")}
            with mock.patch.object(live, "paths", return_value={"active": active}), mock.patch.object(
                live, "load_active", return_value=(root, state)
            ), mock.patch.object(live, "validate_filesystem_cleanup") as validate, mock.patch.object(
                workspace, "require_native_absent"
            ) as absent:
                with workspace.product_quiescence(root, live):
                    pass
            validate.assert_called_once_with(root, state)
            absent.assert_called_once_with(root)

    def test_native_inventory_rejects_legacy_duplicate_and_unbounded_rows(self):
        row = {"pid": 42, "started": "fixed start"}
        valid = {"schema": "agentic-sdlc/workspace-native-v2", "processes": [row, {**row, "pid": 43}]}
        self.assertEqual(workspace._native_records(valid), valid["processes"])
        for value in ({"schema": "agentic-sdlc/workspace-native-v1", **row},
                      {**valid, "processes": [row, row]},
                      {**valid, "processes": [{**row, "pid": i + 2} for i in range(65)]}):
            with self.assertRaises(workspace.WorkspaceError):
                workspace._native_records(value)

    def test_failed_companion_can_record_quiescent_terminal_proof(self):
        import companion_trial
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {"verification_id": "fixture"}
            with mock.patch.object(workspace, "operation"), mock.patch.object(
                live, "load_active", return_value=(root, state)
            ), mock.patch.object(companion_trial.os, "killpg", side_effect=ProcessLookupError):
                companion_trial.record_terminal(root, state, root, {"status": "FAIL"}, [42])
            terminal = json.loads((root / "finished.json").read_text())
            self.assertEqual(terminal["status"], "FAIL")
            self.assertTrue(terminal["cleanup_verified"])
            (root / "finished.json").unlink()
            with mock.patch.object(companion_trial.os, "killpg"), self.assertRaisesRegex(ValueError, "remains active"):
                companion_trial.record_terminal(root, state, root, {"status": "FAIL"}, [42])
            self.assertFalse((root / "finished.json").exists())

    def test_aggregate_record_replay_cannot_replace_frozen_proof(self):
        state = {"git": {}, "multi_project": {"aggregate_sha": "a" * 40, "proof": "first"}}
        with mock.patch.object(live, "verification_root"), mock.patch.object(
            live, "load_active", return_value=(None, state)
        ), mock.patch.object(live, "observe_aggregate", return_value={**state["multi_project"], "proof": "changed"}), mock.patch.object(
            live, "update_state"
        ) as update, self.assertRaisesRegex(live.LifecycleError, "evidence changed"):
            live.record_aggregate(Path("unused"))
        update.assert_not_called()
        self.assertEqual(state["multi_project"]["proof"], "first")


class NativeProcessHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp).resolve() / "verification"
        workspace.admit_root(self.root, create=True)

    def terminated_ledger(self, count=64):
        rows = [{"pid": 900000 + index, "started": "old process identity"}
                for index in range(count)]
        value = {"schema": "agentic-sdlc/workspace-native-v2", "processes": rows}
        workspace._write(self.root / workspace.NATIVE, value)
        original = subprocess.run
        dead = {str(row["pid"]) for row in rows}

        def observe(command, *args, **kwargs):
            if command[:3] == ["ps", "-o", "lstart="] and command[-1] in dead:
                return subprocess.CompletedProcess(command, 1, "", "")
            return original(command, *args, **kwargs)

        self.enterContext(mock.patch.object(workspace.subprocess, "run", side_effect=observe))
        return value

    def test_completed_cleanup_retires_full_native_ledger_before_next_launch(self):
        previous = self.terminated_ledger()
        result = workspace.destroy(self.root)
        repeated = workspace.destroy(self.root)
        workspace.register_native(self.root)
        self.assertEqual(result["status"], "DESTROYED")
        self.assertEqual(repeated["status"], "ALREADY_DESTROYED")
        current = workspace._json(self.root / workspace.NATIVE)
        self.assertEqual([row["pid"] for row in current["processes"]], [os.getpid()])
        archives = list((self.root / workspace.ARCHIVE).glob("native-*.json"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(workspace._json(archives[0])["ledger"], previous)

    def test_live_native_process_preserves_ledger_until_exit(self):
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", "import sys; sys.stdin.buffer.read()"],
            stdin=subprocess.PIPE, start_new_session=True,
        )
        try:
            workspace.register_native(self.root, pid=child.pid)
            path = self.root / workspace.NATIVE
            before = path.read_bytes()
            with self.assertRaisesRegex(workspace.WorkspaceError, "still active"):
                workspace.destroy(self.root)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((self.root / workspace.ARCHIVE).exists())
        finally:
            child.stdin.close()
            child.wait(timeout=5)
        self.assertEqual(workspace.destroy(self.root)["status"], "DESTROYED")
        self.assertFalse(path.exists())
        saved = next((self.root / workspace.ARCHIVE).glob("native-*.json"))
        self.assertEqual(workspace._json(saved)["ledger"], json.loads(before))

    def test_native_history_publication_failure_preserves_active_ledger(self):
        self.terminated_ledger(1)
        path = self.root / workspace.NATIVE
        before = path.read_bytes()
        with mock.patch.object(workspace, "_write", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                workspace.destroy(self.root)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list((self.root / workspace.ARCHIVE).glob("native-*.json")), [])
        self.assertEqual(workspace.destroy(self.root)["status"], "DESTROYED")

    def test_native_history_retirement_replays_after_publication(self):
        previous = self.terminated_ledger(1)
        path = self.root / workspace.NATIVE
        before = path.read_bytes()
        with mock.patch.object(workspace, "_delete_checked", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                workspace.destroy(self.root)
        self.assertEqual(path.read_bytes(), before)
        archives = list((self.root / workspace.ARCHIVE).glob("native-*.json"))
        self.assertEqual(len(archives), 1)
        archived = archives[0].read_bytes()
        self.assertEqual(workspace._json(archives[0])["ledger"], previous)
        self.assertEqual(workspace.destroy(self.root)["status"], "DESTROYED")
        self.assertFalse(path.exists())
        self.assertEqual(archives[0].read_bytes(), archived)
        self.assertEqual(list((self.root / workspace.ARCHIVE).glob("native-*.json")), archives)

    def test_native_history_publication_is_verified_before_retirement(self):
        self.terminated_ledger(1)
        path = self.root / workspace.NATIVE
        before = path.read_bytes()
        original = workspace._write

        def corrupt(saved, value, **kwargs):
            original(saved, {"changed": True}, **kwargs)

        with mock.patch.object(workspace, "_write", side_effect=corrupt):
            with self.assertRaisesRegex(workspace.WorkspaceError, "archive differs"):
                workspace.destroy(self.root)
        self.assertEqual(path.read_bytes(), before)

    def test_native_history_drift_before_unlink_is_preserved(self):
        previous = self.terminated_ledger(1)
        path = self.root / workspace.NATIVE
        changed = {**previous, "processes": [*previous["processes"],
                   {"pid": 900001, "started": "another identity"}]}
        original = workspace._delete_checked

        def replace(*args, **kwargs):
            workspace._write(path, changed)
            return original(*args, **kwargs)

        with mock.patch.object(workspace, "_delete_checked", side_effect=replace):
            with self.assertRaisesRegex(workspace.WorkspaceError, "replaced|changed"):
                workspace.destroy(self.root)
        self.assertEqual(workspace._json(path), changed)
        saved = next((self.root / workspace.ARCHIVE).glob("native-*.json"))
        self.assertEqual(workspace._json(saved)["ledger"], previous)

    def test_native_history_archive_conflict_preserves_both_records(self):
        self.terminated_ledger(1)
        path = self.root / workspace.NATIVE
        before = path.read_bytes()
        with mock.patch.object(workspace, "_delete_checked", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                workspace.destroy(self.root)
        saved = next((self.root / workspace.ARCHIVE).glob("native-*.json"))
        workspace._write(saved, {"changed": True})
        with self.assertRaisesRegex(workspace.WorkspaceError, "archive differs"):
            workspace.destroy(self.root)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(workspace._json(saved), {"changed": True})

    def test_unknown_native_absence_does_not_retire_history(self):
        self.terminated_ledger(1)
        path = self.root / workspace.NATIVE
        before = path.read_bytes()
        unknown = subprocess.CompletedProcess([], 2, "", "unavailable")
        with mock.patch.object(workspace.subprocess, "run", return_value=unknown):
            with self.assertRaisesRegex(workspace.WorkspaceError, "cannot be measured"):
                workspace.destroy(self.root)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.root / workspace.ARCHIVE).exists())

    def test_native_history_archive_symlink_is_rejected(self):
        self.terminated_ledger(1)
        path = self.root / workspace.NATIVE
        before = path.read_bytes()
        foreign = Path(self.temp) / "foreign"
        foreign.mkdir()
        (self.root / workspace.ARCHIVE).symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(workspace.WorkspaceError, "symlink"):
            workspace.destroy(self.root)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(foreign.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
