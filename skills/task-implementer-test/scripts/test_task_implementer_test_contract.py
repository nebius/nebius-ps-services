#!/usr/bin/env python3

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_modes_and_explicit_policy(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        metadata = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        for invocation in (
            "$task-implementer-test",
            "$task-implementer-test --create",
            "$task-implementer-test --create --keep",
            "$task-implementer-test --destroy",
        ):
            self.assertIn(invocation, skill)
        self.assertNotIn("$task-implementer-test --resume", skill)
        self.assertIn("allow_implicit_invocation: false", metadata)
        self.assertIn("embedded helper/workspace paths", skill)
        self.assertIn("digest unchanged", skill)
        self.assertIn("JSON recomputation is forbidden", skill)
        self.assertIn("container-port-80 contract", skill)

    def test_referenced_files_exist(self) -> None:
        for relative in (
            "references/verification-checklist.md",
            "references/live-app-test.md",
            "assets/app-prompt.md.template",
            "assets/live-results.schema.json",
            "scripts/verify_task_implementer.py",
            "scripts/task_implementer_lifecycle.py",
            "scripts/collect_live_evidence.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_app_prompt_keeps_required_managed_sections(self) -> None:
        prompt = (ROOT / "assets" / "app-prompt.md.template").read_text(
            encoding="utf-8"
        )
        for heading in (
            "## Ask",
            "## Outcome",
            "## Acceptance criteria",
            "## Verification",
        ):
            self.assertIn(heading, prompt)
        for contract in (
            "Docker assigns the",
            "database, user, and disposable local password `task_test`",
            "`tasks` table",
            "exactly `postgres:16-alpine`",
            "Do not set `container_name`",
        ):
            self.assertIn(contract, prompt)

    def test_live_reference_defines_self_contained_task_contracts(self) -> None:
        live = (ROOT / "references" / "live-app-test.md").read_text(encoding="utf-8")
        for contract in (
            "serve and expose container port 80",
            "PostgreSQL service `db`",
            "without relying on a bind mount",
            "`postgres:16-alpine`",
            "one shared labelled network",
            "long-form object-map syntax",
            "list syntax is not",
            "no API/database host ports",
            "Reject planning before dispatch",
        ):
            self.assertIn(contract, live)


if __name__ == "__main__":
    unittest.main()
