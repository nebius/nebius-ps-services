from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import implement_catalog as catalog
from implement_errors import LifecycleError
from implement_lifecycle import _atomic_json


def successful_verification():
    return {"schema": "workflow/verification-observations-v1", "groups": [{
        "id": "fixture", "status": "PASS", "inventory": ["fixture.Case.test_ok"],
        "outcomes": {"fixture.Case.test_ok": "passed"},
        "observation": {"local_group_quiescent": True, "stdout": {"complete": True}, "stderr": {"complete": True}}}]}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / "catalog"
        package = self.source / "implement-test"
        package.mkdir(parents=True)
        self.file = package / "SKILL.md"
        self.file.write_text("frozen verifier\n")
        for patcher in (
            patch.object(catalog, "anchor", return_value=self.source),
            patch.object(catalog, "PACKAGES", ("implement-test",)),
            patch.object(catalog, "check_imports"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_changed_deleted_and_symlink_inputs_invalidate_next_boundary(self):
        frozen = catalog.snapshot()
        catalog.validate_record(frozen)
        self.file.write_text("changed\n")
        with self.assertRaises(LifecycleError):
            catalog.validate_record(frozen)
        self.file.unlink()
        with self.assertRaises(LifecycleError):
            catalog.validate_record(frozen)
        target = self.root / "other"
        target.write_text("frozen verifier\n")
        self.file.symlink_to(target)
        with self.assertRaises(LifecycleError):
            catalog.validate_record(frozen)

    def test_preflight_and_generation_receipt_are_required_and_bound(self):
        private = self.root / "owner"
        with self.assertRaises(LifecycleError):
            catalog.preflight(private)
        value = catalog.publish_preflight(
            private, catalog.snapshot(), {"deterministic": "PASS", "stages": [], "verification": successful_verification()}
        )
        run = private / "runs/generation"
        (run / "evidence").mkdir(parents=True)
        state = {}
        catalog.freeze(private, run, state, catalog.preflight(private))
        catalog.validate(state, run)
        original = (run / catalog.RELATIVE).read_bytes()
        changed = {**value, "status": "FAIL"}
        _atomic_json(run / catalog.RELATIVE, changed)
        with self.assertRaises(LifecycleError):
            catalog.validate(state, run)
        (run / catalog.RELATIVE).write_bytes(original)
        self.file.write_text("changed\n")
        with self.assertRaises(LifecycleError):
            catalog.validate(state, run)
        self.assertEqual((run / catalog.RELATIVE).read_bytes(), original)

    def test_failing_preflight_cannot_issue_binding(self):
        with self.assertRaises(LifecycleError):
            catalog.publish_preflight(
                self.root / "owner", catalog.snapshot(), {"deterministic": "FAIL"}
            )
        self.assertFalse((self.root / "owner/preflight.json").exists())


class RuntimeHookTests(unittest.TestCase):
    def test_runtime_bytes_registration_and_features_are_bound(self):
        import json

        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            files, hooks = catalog.hook_inputs(home, "codex")
            for name, data in files.items():
                target = home / "hooks" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            settings = home / "hooks.json"
            settings.write_text(json.dumps({"hooks": hooks}))
            config = home / "config.toml"
            config.write_text("[features]\nhooks = true\n")
            expected = catalog.runtime_hooks(home, "codex")
            settings.write_text(json.dumps({"hooks": {**hooks, "PostToolUse": []}}))
            self.assertEqual(catalog.runtime_hooks(home, "codex"), expected)
            settings.write_text(json.dumps({"hooks": {**hooks, "PostToolUse": {}}}))
            with self.assertRaises(LifecycleError):
                catalog.runtime_hooks(home, "codex")
            settings.write_text(json.dumps({"hooks": hooks}))

            shadow = home / "hooks/json.py"
            shadow.write_text("# unexpected import shadow\n")
            with self.assertRaises(LifecycleError):
                catalog.runtime_hooks(home, "codex")
            shadow.unlink()
            with self.assertRaisesRegex(LifecycleError, "binding is missing"):
                catalog.validate_runtime_hooks({}, home, required=True)
            (home / "evidence").mkdir()
            (home / "evidence/public-trial-reservation.json").write_text("{}")
            with self.assertRaisesRegex(LifecycleError, "binding is missing"):
                catalog.validate_runtime_hooks({}, home)

            target = home / "hooks/pre_tool_use_sdlc_policy.py"
            original = target.read_bytes()
            target.write_bytes(original + b"\n# drift\n")
            with self.assertRaises(LifecycleError):
                catalog.runtime_hooks(home, "codex")
            target.unlink()
            with self.assertRaises(OSError):
                catalog.runtime_hooks(home, "codex")
            other = home / "other.py"
            other.write_bytes(original)
            target.symlink_to(other)
            with self.assertRaises(LifecycleError):
                catalog.runtime_hooks(home, "codex")
            target.unlink()
            target.write_bytes(original)
            settings.write_text(json.dumps({"hooks": {}}))
            with self.assertRaises(LifecycleError):
                catalog.runtime_hooks(home, "codex")
            settings.write_text(json.dumps({"hooks": hooks}))
            config.write_text("[features]\nhooks = false\n")
            with self.assertRaises(LifecycleError):
                catalog.runtime_hooks(home, "codex")
            config.write_text("[features]\nhooks = true\n")
            self.assertEqual(catalog.runtime_hooks(home, "codex"), expected)


class ImportIdentityTests(unittest.TestCase):
    def test_evidence_cannot_select_foreign_executable_code(self):
        import sys

        with patch.dict(
            sys.modules,
            {
                "workflow_foreign": SimpleNamespace(
                    __file__="/foreign/workflow_foreign.py"
                )
            },
        ):
            with self.assertRaisesRegex(LifecycleError, "mixed verifier"):
                catalog.check_imports()


if __name__ == "__main__":
    unittest.main()
