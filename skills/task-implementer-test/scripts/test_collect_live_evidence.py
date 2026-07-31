#!/usr/bin/env python3

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import collect_live_evidence as collector


class CollectorTests(unittest.TestCase):
    def test_correlates_frontend_api_database_and_restart(self) -> None:
        generation = str(uuid.uuid4())
        created = {"id": 9, "title": "Verifier task", "completed": False}
        updated = {"id": 9, "title": "Verifier task", "completed": True}
        ps = {
            "status": "LIVE_INSPECTED",
            "services": [
                {"Service": name, "State": "running", "Health": "healthy"}
                for name in ("frontend", "api", "db")
            ],
        }
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(
                collector,
                "status",
                return_value={"generation_id": generation, "web_port": 49152},
            ),
            patch.object(collector, "_frontend", return_value="<h1>Task board</h1>"),
            patch.object(
                collector,
                "_eventually",
                side_effect=[[], updated, updated],
            ),
            patch.object(collector, "_request", side_effect=[created, updated]),
            patch.object(
                collector,
                "database_probe",
                return_value={
                    "task_id": 9,
                    "title": "Verifier task",
                    "completed": True,
                },
            ),
            patch.object(collector, "compose_ps", return_value=ps),
            patch.object(collector, "restart_api", return_value={"status": "ok"}),
        ):
            evidence = collector.collect(Path(temp), generation)
        self.assertEqual(evidence["created_task"], evidence["database_task"])
        self.assertTrue(evidence["persisted_after_restart"])
        self.assertEqual(evidence["services"], ["api", "db", "frontend"])


if __name__ == "__main__":
    unittest.main()
