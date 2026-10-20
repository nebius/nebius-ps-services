#!/usr/bin/env python3

import tempfile
import copy
import json
import runpy
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import collect_live_evidence as collector


class CollectorTests(unittest.TestCase):
    def test_readiness_budget_reaches_docker_subprocess(self) -> None:
        import implement_lifecycle

        with (
            patch.object(
                implement_lifecycle,
                "_compose_command",
                return_value=["docker", "compose", "ps"],
            ),
            patch.object(implement_lifecycle, "_run") as run,
        ):
            run.return_value.stdout = "[]"
            implement_lifecycle._compose_ps_active(
                {}, Path("unused"), Path("project"), timeout=3.25
            )
        run.assert_called_once_with(
            ["docker", "compose", "ps"], cwd=Path("project"), timeout=3.25
        )

    def test_cli_catches_collector_error_across_entrypoint_module_identity(
        self,
    ) -> None:
        import implement_lifecycle

        namespace = runpy.run_path(
            implement_lifecycle.__file__, run_name="isolated_lifecycle"
        )
        entrypoint = namespace["main"]
        output = StringIO()

        def fail(*args):
            raise collector.LifecycleError("bounded collector failure")

        with (
            patch.dict(entrypoint.__globals__, {"collect_application": fail}),
            patch.object(
                sys,
                "argv",
                [
                    "lifecycle",
                    "collect-application",
                    "--expected-generation",
                    str(uuid.uuid4()),
                ],
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(entrypoint(), 1)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"status": "FAIL", "error": "bounded collector failure"},
        )

    def test_services_without_configured_healthchecks_remain_valid(self) -> None:
        records = [
            {"Service": name, "State": "running", "Health": health}
            for name, health in (("frontend", ""), ("api", None), ("db", "healthy"))
        ]
        self.assertEqual(
            collector._healthy_services(
                Path("unused"), "generation", lambda *a, **kw: {"services": records}
            ),
            ["api", "db", "frontend"],
        )

    def test_late_healthy_inspection_cannot_pass_readiness(self) -> None:
        records = [
            {"Service": name, "State": "running", "Health": "healthy"}
            for name in ("frontend", "api", "db")
        ]
        with (
            patch.object(collector.time, "monotonic", side_effect=[0, 0, 61]),
            self.assertRaisesRegex(
                collector.LifecycleError, "health readiness timed out"
            ),
        ):
            collector._healthy_services(
                Path("unused"), "generation", lambda *a, **kw: {"services": records}
            )

    def test_waits_for_initial_and_post_restart_health_before_http(self) -> None:
        generation = str(uuid.uuid4())
        healthy = {
            "services": [
                {"Service": name, "State": "running", "Health": "healthy"}
                for name in ("frontend", "api", "db")
            ]
        }
        starting = copy.deepcopy(healthy)
        starting["services"][1]["Health"] = "starting"
        events = []
        snapshots = iter([starting, healthy, starting, healthy])
        task = {"id": 9, "title": "Verifier task", "completed": True}

        def inspect(*args, timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 60)
            snapshot = next(snapshots)
            events.append("starting" if snapshot is starting else "healthy")
            return snapshot

        with (
            patch.object(collector.time, "sleep"),
            patch.object(
                collector,
                "_frontend",
                side_effect=lambda *a: events.append("frontend") or "Task board",
            ),
            patch.object(
                collector,
                "_eventually",
                side_effect=lambda *a: events.append("http") or task,
            ),
            patch.object(collector, "_request", return_value=task),
        ):
            evidence = collector.collect(
                Path("unused"),
                generation,
                current={"generation_id": generation, "web_port": 49152},
                compose_ps_fn=inspect,
                database_probe_fn=lambda *a: {
                    "task_id": 9,
                    "title": "Verifier task",
                    "completed": True,
                },
                restart_api_fn=lambda *a: events.append("restart"),
            )
        self.assertEqual(
            events,
            [
                "starting",
                "healthy",
                "frontend",
                "http",
                "http",
                "restart",
                "starting",
                "healthy",
                "http",
            ],
        )
        self.assertTrue(evidence["persisted_after_restart"])

    def test_invalid_inventory_or_failed_health_fails_before_http(self) -> None:
        healthy = [
            {"Service": name, "State": "running", "Health": "healthy"}
            for name in ("frontend", "api", "db")
        ]
        invalid = [healthy[:2], healthy + [healthy[0]], healthy + [None]]
        for field, value in (
            ("Health", "unhealthy"),
            ("State", "exited"),
            ("Service", "unknown"),
        ):
            records = copy.deepcopy(healthy)
            records[0][field] = value
            invalid.append(records)
        for records in invalid:
            with (
                self.subTest(records=records),
                patch.object(collector, "_frontend") as frontend,
            ):
                with self.assertRaises(collector.LifecycleError):
                    collector.collect(
                        Path("unused"),
                        "generation",
                        current={"generation_id": "generation", "web_port": 49152},
                        compose_ps_fn=lambda *a, **kw: {"services": records},
                    )
                frontend.assert_not_called()

    def test_starting_health_has_bounded_deadline_before_http(self) -> None:
        records = [
            {"Service": name, "State": "running", "Health": "starting"}
            for name in ("frontend", "api", "db")
        ]
        with (
            patch.object(collector.time, "monotonic", side_effect=[0, 0, 0, 61]),
            patch.object(collector.time, "sleep") as sleep,
            patch.object(collector, "_frontend") as frontend,
            self.assertRaisesRegex(
                collector.LifecycleError, "health readiness timed out"
            ),
        ):
            collector.collect(
                Path("unused"),
                "generation",
                current={"generation_id": "generation", "web_port": 49152},
                compose_ps_fn=lambda *a, **kw: {"services": records},
            )
        frontend.assert_not_called()
        sleep.assert_called_once_with(1)

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
