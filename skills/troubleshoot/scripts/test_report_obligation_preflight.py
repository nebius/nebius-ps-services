from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

import preflight_report_obligations as preflight


class ReportObligationPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "task-state"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_sidecar(
        self,
        schema: str,
        status: str,
        *,
        session: str = "session",
    ) -> Path:
        session_dir = self.root / session
        session_dir.mkdir()
        path = session_dir / preflight.FILE_NAME
        path.write_text(
            json.dumps({"schema": schema, "status": status}) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def run_main(self) -> tuple[int, dict[str, object]]:
        output = StringIO()
        with redirect_stdout(output):
            result = preflight.main(["--state-root", str(self.root)])
        return result, json.loads(output.getvalue())

    def test_missing_root_is_clear(self) -> None:
        self.root.rmdir()
        result, summary = self.run_main()
        self.assertEqual(result, 0)
        self.assertEqual(
            summary,
            {"invalid": 0, "other": {}, "v1": {}, "v2": {}, "v3": {}},
        )

    def test_active_v2_aborts_without_printing_sidecar_content(self) -> None:
        path = self.write_sidecar(preflight.V2_SCHEMA, "active")
        path.write_text(
            json.dumps(
                {
                    "schema": preflight.V2_SCHEMA,
                    "status": "active",
                    "private_detail": "must-not-appear",
                }
            ),
            encoding="utf-8",
        )
        result, summary = self.run_main()
        self.assertEqual(result, 2)
        self.assertEqual(summary["v2"], {"active": 1})
        self.assertNotIn("must-not-appear", json.dumps(summary))
        self.assertNotIn(str(path), json.dumps(summary))

    def test_terminal_v2_and_current_v3_are_allowed(self) -> None:
        self.write_sidecar(preflight.V2_SCHEMA, "delivered", session="old")
        self.write_sidecar(preflight.V3_SCHEMA, "active", session="current")
        result, summary = self.run_main()
        self.assertEqual(result, 0)
        self.assertEqual(summary["v2"], {"delivered": 1})
        self.assertEqual(summary["v3"], {"active": 1})

    def test_invalid_sidecar_fails_closed(self) -> None:
        session_dir = self.root / "invalid"
        session_dir.mkdir()
        (session_dir / preflight.FILE_NAME).write_text("not-json\n", encoding="utf-8")
        result, summary = self.run_main()
        self.assertEqual(result, 3)
        self.assertEqual(summary["invalid"], 1)

    def test_unsupported_schema_is_counted_without_echoing_its_value(self) -> None:
        path = self.write_sidecar("private-schema-value", "active")
        result, summary = self.run_main()
        self.assertEqual(result, 3)
        self.assertEqual(summary["invalid"], 1)
        self.assertEqual(summary["other"], {"other": 1})
        self.assertNotIn("private-schema-value", json.dumps(summary))
        self.assertNotIn(str(path), json.dumps(summary))

    def test_known_v1_terminal_schema_is_preserved_and_allowed(self) -> None:
        self.write_sidecar(preflight.V1_SCHEMA, "fallback")
        result, summary = self.run_main()
        self.assertEqual(result, 0)
        self.assertEqual(summary["v1"], {"fallback": 1})

    def test_v2_rejects_v3_only_sensitive_status(self) -> None:
        self.write_sidecar(preflight.V2_SCHEMA, "sensitive_detected")
        result, summary = self.run_main()
        self.assertEqual(result, 3)
        self.assertEqual(summary["invalid"], 1)
        self.assertEqual(summary["v2"], {"other": 1})

    def test_unrelated_symlinked_directory_is_not_a_sidecar_error(self) -> None:
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (self.root / "unrelated-link").symlink_to(outside, target_is_directory=True)
        result, summary = self.run_main()
        self.assertEqual(result, 0)
        self.assertEqual(summary["invalid"], 0)


if __name__ == "__main__":
    unittest.main()
