"""Final delivery guidance for both owners, including promotion crash windows."""
import contextlib
import io
import json
import unittest
from unittest import mock

import test_workflow_automatic as fixtures
import workflow_automatic as automatic
import workflow_delivery_guidance as messages
import workflow_intake as intake
import workflow_projects as projects
import worktree_manager as manager


class DeliveryGuidanceTests(unittest.TestCase):
    workflow_owner = "implement"
    setUp = fixtures.AutomaticTests.setUp
    git = staticmethod(fixtures.AutomaticTests.git)
    freeze_basis = fixtures.AutomaticTests.freeze_basis
    prepare = fixtures.AutomaticTests.prepare
    record_gates = fixtures.AutomaticTests.record_gates
    validate_final = fixtures.AutomaticTests.validate_final

    def expected_command(self):
        return messages.continuation(self.workflow_owner, str(self.repo))

    def test_dirty_source_wait_has_exact_owner_command_and_preserves_files(self):
        self.freeze_basis()
        dirty = self.repo / "other-project.txt"
        dirty.write_text("work in progress\n")
        head = self.git(self.repo, "rev-parse", "HEAD")
        result = automatic.prepare_aggregation(self.run, "automatic-test")
        self.assertEqual(result["status"], "AGGREGATION_WAIT")
        self.assertEqual(result["next_action"], self.expected_command())
        self.assertEqual(result["delivery"]["delivery_stage"], "promotion_pending")
        self.assertIn("not yet been delivered", result["user_message"])
        self.assertIn("uncommitted", result["user_message"])
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(dirty.read_text(), "work in progress\n")
        self.assertTrue(self.outer.exists())
        before = {str(p): p.read_bytes() for p in self.run.rglob("*.json")}
        projected = messages.guidance(self.run)
        self.assertEqual(projected["next_action"], self.expected_command())
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.run.rglob("*.json")})

    def test_dirt_racing_preflight_returns_pending_guidance(self):
        self.freeze_basis()
        original = manager.integrate_worktree
        def race(**kwargs):
            (self.repo / "racing.txt").write_text("dirty\n")
            return original(**kwargs)
        with mock.patch.object(manager, "integrate_worktree", side_effect=race):
            result = automatic.prepare_aggregation(self.run, "automatic-test")
        self.assertEqual(result["status"], "AGGREGATION_WAIT")
        self.assertEqual(result["next_action"], self.expected_command())

    def test_crash_after_promotion_is_not_reported_as_unpromoted(self):
        self.prepare()
        self.validate_final()
        original = projects.update
        def crash(*args, **kwargs):
            if kwargs.get("state") == "delivered":
                raise RuntimeError("interrupted after Git promotion")
            return original(*args, **kwargs)
        with mock.patch.object(projects, "update", side_effect=crash), self.assertRaises(RuntimeError):
            automatic.finish_aggregation(self.run, "automatic-test")
        result = messages.guidance(self.run)
        self.assertEqual(result["delivery_stage"], "finalization_pending")
        self.assertNotIn("not yet been delivered", result["user_message"])
        self.assertEqual(result["next_action"], self.expected_command())
        completed = automatic.finish_aggregation(self.run, "automatic-test")
        self.assertIsNone(completed["next_action"])
        self.assertEqual(completed["delivery"]["delivery_stage"], "complete")

    def test_validated_cli_error_retains_guidance_and_original_failure(self):
        self.prepare()
        output = io.StringIO()
        with mock.patch.object(automatic, "finish_aggregation", side_effect=RuntimeError("original failure")), \
                contextlib.redirect_stdout(output):
            result = intake.main(["--owner", self.workflow_owner, "aggregate-finish", "--run-dir", str(self.run)])
        response = json.loads(output.getvalue())
        self.assertEqual(result, 2)
        self.assertEqual(response["error"], "original failure")
        self.assertEqual(response["next_action"], self.expected_command())

    def test_unverifiable_promotion_reports_recovery(self):
        self.prepare()
        with mock.patch.object(manager, "_git", side_effect=RuntimeError("unavailable")):
            result = messages.guidance(self.run)
        self.assertEqual(result["delivery_stage"], "recovery_pending")
        self.assertNotIn("not yet been delivered", result["user_message"])


class SdlcDeliveryGuidanceTests(DeliveryGuidanceTests):
    workflow_owner = "agentic-sdlc"


if __name__ == "__main__":
    unittest.main()
