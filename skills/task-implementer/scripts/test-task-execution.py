#!/usr/bin/env python3
"""Focused tests for dependency-wave scheduling and v1 rejection."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from prompt_workspace_core import PromptWorkspaceError
from prompt_workspace_execution import (
    TaskPlan,
    WriteClaim,
    assert_no_unfinished_v1,
    batches_for_wave,
    build_dependency_waves,
    load_coordinator_state,
    parse_task_plans,
    tasks_conflict,
)


def task(
    number: int,
    *,
    dependencies: tuple[str, ...] = (),
    path: str | None = None,
    domain: str | None = None,
    known: bool = True,
) -> TaskPlan:
    return TaskPlan(
        task_id=f"task-{number}",
        position=number - 1,
        dependencies=dependencies,
        write_claims=(WriteClaim("exact", path or f"src/task-{number}.py"),)
        if known
        else (),
        conflict_domains=(domain or f"files:task-{number}",) if known else (),
        requirement_ids=f"TI-REQ-{number:03d}",
        design_id=f"TI-DES-{number:03d}",
        goal=f"Implement task {number}",
        plan=f"Change task {number}",
        implementation_steps=f"Update only task {number} files",
        validation="run focused tests",
        end_to_end_validation="verify the task outcome",
        done_criteria="tests pass",
        rollback_notes="revert the task commit",
        stop_conditions="stop on validation failure",
        ownership_known=known,
    )


def handoff(tasks: list[str]) -> str:
    return "# Handoff\n\n## Task Queue\n\n" + "\n".join(tasks)


def task_markdown(
    number: int,
    *,
    dependencies: str = "none",
    claims: str | None = None,
    domains: str | None = None,
) -> str:
    return f"""### task-{number}

- Status: pending
- Depends on: {dependencies}
- Write claims: {claims if claims is not None else f"exact: src/task-{number}.py"}
- Conflict domains: {domains if domains is not None else f"files:task-{number}"}
- Implementation steps: update the claimed task file
- Validation: run tests
- End-to-end validation: verify the task result
- Done criteria: tests pass
"""


class DependencyWaveTest(unittest.TestCase):
    def test_ten_task_fixture_and_capacity_batches(self) -> None:
        tasks = [task(number) for number in range(1, 6)]
        tasks.extend(
            task(number, dependencies=tuple(f"task-{item}" for item in range(1, 6)))
            for number in (6, 7)
        )
        tasks.extend(
            (
                task(8, dependencies=("task-6", "task-7")),
                task(9, dependencies=("task-8",)),
                task(10, dependencies=("task-9",)),
            )
        )
        waves = build_dependency_waves(tasks)
        self.assertEqual(
            [[item.task_id for item in wave] for wave in waves],
            [
                ["task-1", "task-2", "task-3", "task-4", "task-5"],
                ["task-6", "task-7"],
                ["task-8"],
                ["task-9"],
                ["task-10"],
            ],
        )
        self.assertEqual(
            [
                [item.task_id for item in batch]
                for batch in batches_for_wave(waves[0], 2)
            ],
            [["task-1", "task-2"], ["task-3", "task-4"], ["task-5"]],
        )

    def test_overlapping_exact_and_prefix_claims_serialize(self) -> None:
        cases = (
            (WriteClaim("exact", "src/a.py"), WriteClaim("exact", "src/a.py")),
            (WriteClaim("exact", "src/node"), WriteClaim("exact", "src/node/a.py")),
            (WriteClaim("prefix", "src"), WriteClaim("exact", "src/a.py")),
            (WriteClaim("prefix", "src"), WriteClaim("prefix", "src/pkg")),
        )
        for left, right in cases:
            with self.subTest(left=left, right=right):
                first = task(1)
                second = task(2)
                first = TaskPlan(**{**first.__dict__, "write_claims": (left,)})
                second = TaskPlan(**{**second.__dict__, "write_claims": (right,)})
                self.assertTrue(tasks_conflict(first, second))
                self.assertEqual(len(build_dependency_waves([first, second])), 2)

    def test_conflict_domain_classes_serialize(self) -> None:
        domains = (
            "core-api:catalog",
            "database-schema:main",
            "migration-chain:202607",
            "dependency-manifest:python",
            "shared-abstraction:runner",
            "kubernetes-resource:apps/deployment/example",
            "terraform-resource:module.example",
            "exclusive-test:cluster-a",
            "external-mutation:provider-a",
            "architecture-decision:storage",
        )
        for domain in domains:
            with self.subTest(domain=domain):
                self.assertTrue(
                    tasks_conflict(task(1, domain=domain), task(2, domain=domain))
                )

    def test_external_mutation_domains_are_singleton_even_with_distinct_keys(
        self,
    ) -> None:
        for domain_class in (
            "external-database",
            "external-kubernetes",
            "external-terraform",
            "migration-execution",
            "publication",
        ):
            with self.subTest(domain_class=domain_class):
                waves = build_dependency_waves(
                    [
                        task(1, domain=f"{domain_class}:one"),
                        task(2, domain=f"{domain_class}:two"),
                    ]
                )
                self.assertEqual(len(waves), 2)

    def test_unknown_ownership_forces_singleton(self) -> None:
        waves = build_dependency_waves([task(1, known=False), task(2), task(3)])
        self.assertEqual(
            [[item.task_id for item in wave] for wave in waves],
            [["task-1"], ["task-2", "task-3"]],
        )

    def test_cycle_fails_closed(self) -> None:
        with self.assertRaisesRegex(PromptWorkspaceError, "cycle") as raised:
            build_dependency_waves(
                [task(1, dependencies=("task-2",)), task(2, dependencies=("task-1",))]
            )
        self.assertEqual(raised.exception.code, "DEPENDENCY_CYCLE")

    def test_parser_requires_explicit_claim_types_and_stable_domains(self) -> None:
        parsed = parse_task_plans(
            handoff(
                [
                    task_markdown(
                        1, claims="prefix: services/example", domains="core-api:public"
                    )
                ]
            )
        )
        self.assertEqual(
            parsed[0].write_claims, (WriteClaim("prefix", "services/example"),)
        )
        for claims, domains in (
            ("services/example", "files:one"),
            ("exact: src/a.py", "unknown value"),
        ):
            with self.subTest(claims=claims, domains=domains):
                with self.assertRaises(PromptWorkspaceError):
                    parse_task_plans(
                        handoff([task_markdown(1, claims=claims, domains=domains)])
                    )

    def test_parser_requires_self_contained_worker_contract(self) -> None:
        complete = task_markdown(1)
        for field in (
            "Implementation steps",
            "Validation",
            "End-to-end validation",
            "Done criteria",
        ):
            with self.subTest(field=field):
                incomplete = "\n".join(
                    line
                    for line in complete.splitlines()
                    if not line.startswith(f"- {field}:")
                )
                with self.assertRaises(PromptWorkspaceError) as raised:
                    parse_task_plans(handoff([incomplete]))
                self.assertEqual(raised.exception.code, "EXECUTION_STATE_INVALID")

    def test_execution_plane_v1_is_always_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            execution = run_dir / "execution"
            execution.mkdir()
            artifact = execution / "task-1.json"
            artifact.write_text(
                '{"schema":"task-implementer/execution-plane-v1","phase":"running"}\n',
                encoding="utf-8",
            )
            artifact.chmod(0o600)
            handoff_path = run_dir / "handoff.md"
            handoff_path.write_text(
                "## Run\n\n- Overall status: running\n", encoding="utf-8"
            )
            handoff_path.chmod(0o600)
            with self.assertRaises(PromptWorkspaceError) as raised:
                assert_no_unfinished_v1(run_dir)
            self.assertEqual(raised.exception.code, "WORKFLOW_UPGRADE_REQUIRED")
            handoff_path.write_text(
                "## Run\n\n- Current task: none\n- Overall status: done\n",
                encoding="utf-8",
            )
            handoff_path.chmod(0o600)
            artifact.write_text(
                '{"schema":"task-implementer/execution-plane-v1","phase":"stopped"}\n',
                encoding="utf-8",
            )
            artifact.chmod(0o600)
            with self.assertRaises(PromptWorkspaceError) as completed:
                assert_no_unfinished_v1(run_dir)
            self.assertEqual(completed.exception.code, "WORKFLOW_UPGRADE_REQUIRED")

    def test_legacy_coordinators_always_require_new_v7_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            orchestration = run_dir / "orchestration"
            orchestration.mkdir()
            coordinator = orchestration / "coordinator.json"
            for version in (1, 2, 3, 4, 5, 6):
                for status in ("running", "done"):
                    with self.subTest(version=version, status=status):
                        coordinator.write_text(
                            json.dumps(
                                {
                                    "schema": f"task-implementer/coordinator-v{version}",
                                    "status": status,
                                }
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                        coordinator.chmod(0o600)
                        with self.assertRaises(PromptWorkspaceError) as raised:
                            load_coordinator_state(run_dir)
                        self.assertEqual(
                            raised.exception.code, "WORKFLOW_UPGRADE_REQUIRED"
                        )


if __name__ == "__main__":
    unittest.main()
