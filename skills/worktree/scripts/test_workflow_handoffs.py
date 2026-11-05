"""Bounded dependency summaries retain exact evidence references at large fan-in."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import workflow_execution as execution
from test_workflow_program import task
from workflow_state import write


class HandoffTests(unittest.TestCase):
    def test_large_fan_in_is_indexed_and_tampering_is_not_repaired(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary).resolve()
            deps = tuple(f"TASK-{i:03d}" for i in range(1, 2101))
            selected = replace(task(2101), dependencies=deps)
            assignment = {"schema": execution.ASSIGNMENT_SCHEMA}
            assignment["assignment_digest"] = execution.sha256_json(assignment)
            result = {
                "schema": execution.RESULT_SCHEMA,
                "assignment_digest": assignment["assignment_digest"],
                "summary": "x" * 3000,
                "commit": "a" * 40,
            }
            result["result_digest"] = execution.sha256_json(result)
            original = execution.read_json

            def read(path):
                if "dependency-indexes" in path.parts:
                    return original(path)
                if "assignments" in path.parts:
                    return assignment
                if "results" in path.parts:
                    return result
                if "tasks" in path.parts:
                    return {
                        "status": "merged",
                        "result_digest": result["result_digest"],
                    }
                if "waves" in path.parts:
                    return {}
                raise AssertionError(path)

            with (
                mock.patch.object(
                    execution,
                    "_task_wave_index",
                    return_value={i: "WAVE-001" for i in deps},
                ),
                mock.patch.object(execution, "read_json", side_effect=read),
                mock.patch.object(execution, "_validate_assignment_record"),
                mock.patch.object(execution, "_validate_result_record"),
            ):

                def build(persist=False):
                    return execution._build_incoming_handoff(
                        run,
                        "FEAT-001",
                        {},
                        "WAVE-002",
                        selected,
                        "a" * 40,
                        "frozen",
                        persist_index=persist,
                    )

                handoff = build(True)
                self.assertLess(len(json.dumps(handoff).encode()), 64 * 1024)
                self.assertEqual(len(handoff["predecessors"]), 16)
                self.assertEqual(len(handoff["predecessors"][0]["summary"]), 512)
                self.assertEqual(handoff["dependency_index"]["count"], 2100)
                self.assertEqual(build(), handoff)
                index = run / handoff["dependency_index"]["path"]
                write(index, {"predecessors": []})
                with self.assertRaises(execution.ExecutionError):
                    build(True)
                index.unlink()
                with self.assertRaises(execution.ExecutionError):
                    build()


if __name__ == "__main__":
    unittest.main()
