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


if __name__ == "__main__":
    unittest.main()
