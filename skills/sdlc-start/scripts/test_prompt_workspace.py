#!/usr/bin/env python3
"""Offline tests for the Agentic SDLC private prompt workspace."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("prompt_workspace.py")
SPEC = importlib.util.spec_from_file_location("sdlc_prompt_workspace", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
workspace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workspace)


def write_private(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    path.chmod(0o600)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PromptWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.codex_home = self.root / "codex-home"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialize(self) -> dict[str, object]:
        return workspace.initialize(self.project, self.codex_home, False, "code")

    def prompt_path(self) -> Path:
        path = Path(str(self.initialize()["starter_prompt"]))
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "<!-- Required: replace this comment with your Ask. -->",
            "Implement the requested behavior and verify its observable acceptance criteria.",
        )
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
        return path

    def intake(self, prompt: Path | str) -> dict[str, object]:
        return workspace.intake(str(prompt), self.project, self.codex_home)

    def set_run_status(self, result: dict[str, object], status: str) -> None:
        run_dir = Path(str(result["snapshot"])).parents[2]
        write_private(run_dir / "run.json", {"status": status})

    def edit_prompt(self, path: Path, marker: str) -> None:
        text = path.read_text(encoding="utf-8")
        if "## Steering\n" in text:
            text = text.replace("## Steering\n", f"## Steering\n\n{marker}\n", 1)
        else:
            text = text.rstrip() + f"\n\n## Steering\n\n{marker}\n"
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)

    def test_init_is_idempotent_and_survives_git_init(self) -> None:
        first = self.initialize()
        prompt = Path(str(first["starter_prompt"]))
        before = prompt.read_bytes()
        before_mtime = prompt.stat().st_mtime_ns
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        second = self.initialize()
        self.assertEqual(first["workspace"], second["workspace"])
        self.assertFalse(second["starter_created"])
        self.assertEqual(prompt.read_bytes(), before)
        self.assertEqual(prompt.stat().st_mtime_ns, before_mtime)
        self.assertEqual(list(self.project.iterdir()), [self.project / ".git"])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(prompt.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(prompt.parent.stat().st_mode), 0o700)

    def test_intake_bootstraps_remote_default_and_reuses_dirty_run_branch(self) -> None:
        origin = self.root / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
        subprocess.run(
            ["git", "init", "-q", "-b", "trunk", str(self.project)], check=True
        )
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.name", "SDLC Start"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.project),
                "config",
                "user.email",
                "sdlc-start@example.invalid",
            ],
            check=True,
        )
        tracked = self.project / "tracked.txt"
        tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.project), "add", "tracked.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "commit", "-qm", "initial"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "remote", "add", "origin", str(origin)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "push", "-qu", "origin", "trunk"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/trunk"],
            check=True,
        )

        prompt = self.prompt_path()
        first = self.intake(prompt)
        branch = subprocess.run(
            ["git", "-C", str(self.project), "branch", "--show-current"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        self.assertTrue(branch.startswith("feature/sdlc-"))
        self.assertEqual(first["promotion_branch"], branch)
        self.assertEqual(first["default_branch"], "trunk")

        tracked.write_text("unfinished\n", encoding="utf-8")
        resumed = self.intake(prompt)
        self.assertEqual(resumed["action"], "resume")
        self.assertEqual(resumed["promotion_branch"], branch)
        self.assertEqual(tracked.read_text(encoding="utf-8"), "unfinished\n")

    def test_concurrent_init_creates_one_starter_prompt(self) -> None:
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda _index: self.initialize(), range(6)))
        self.assertEqual(
            [result["starter_created"] for result in results].count(True), 1
        )
        self.assertEqual(len({str(result["starter_prompt"]) for result in results}), 1)
        prompt_root = Path(str(results[0]["starter_prompt"])).parent
        self.assertEqual(len(list(prompt_root.glob("*.md"))), 2)
        self.assertTrue((prompt_root / workspace.HUB_FILENAME).is_file())

    def test_untouched_starter_fails_before_creating_a_run(self) -> None:
        initialized = self.initialize()
        project_dir = Path(str(initialized["workspace"])).parent
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            self.intake(Path(str(initialized["starter_prompt"])))
        self.assertEqual(caught.exception.code, "PROMPT_INPUT_INVALID")
        self.assertFalse((project_dir / "active-run.json").exists())

    def test_new_resume_steering_resolve_and_completed_rerun(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt.name)
        self.assertEqual(first["action"], "new")
        self.assertEqual(first["revision"], "r0001")
        self.assertEqual(Path(str(first["snapshot"])).read_bytes(), prompt.read_bytes())
        resumed = self.intake(prompt)
        self.assertEqual(resumed["action"], "resume")
        self.assertEqual(resumed["run_id"], first["run_id"])
        self.edit_prompt(prompt, "Prefer the smallest vertical slice.")
        steered = self.intake(prompt.name)
        self.assertEqual(steered["action"], "steering")
        self.assertEqual(steered["revision"], "r0002")
        repeated = self.intake(prompt.name)
        self.assertEqual(repeated["revision"], "r0002")
        manifest = Path(str(first["snapshot"])).parents[2].parent / "workspace.json"
        with mock.patch.object(
            workspace,
            "verify_current_prompt_impact",
            return_value=(
                {"revision": "r0002", "effects": ["requirements"]},
                "d" * 64,
            ),
        ):
            resolved = workspace.steering_resolve(
                manifest, str(first["run_id"]), "r0002", "applied"
            )
        self.assertEqual(resolved["disposition"], "applied")
        self.assertEqual(self.intake(prompt.name)["action"], "resume")
        self.set_run_status(first, "complete")
        done = self.intake(prompt.name)
        self.assertEqual(done["outcome"], "ALREADY_COMPLETE")
        self.edit_prompt(prompt, "Add one follow-up requirement.")
        next_run = self.intake(prompt.name)
        self.assertEqual(next_run["action"], "new")
        self.assertNotEqual(next_run["run_id"], first["run_id"])
        next_binding = workspace.validate_binding(
            Path(str(next_run["snapshot"])).parents[2]
        )
        self.assertEqual(next_binding["revisions"][0]["kind"], "completed_follow_up")
        self.assertEqual(next_binding["predecessor"]["run_id"], first["run_id"])
        self.assertEqual(next_binding["lineage_root"], first["run_id"])

    def test_active_prompt_conflict_preserves_existing_run(self) -> None:
        first_prompt = self.prompt_path()
        first = self.intake(first_prompt)
        manifest = Path(str(self.initialize()["workspace"]))
        created = workspace.create_prompt(
            manifest,
            "Implement a distinct queued objective",
            id_factory=lambda: "1" * 32,
        )
        second_prompt = Path(str(created["path"]))
        queued = self.intake(second_prompt)
        self.assertEqual(queued["action"], "queued")
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["queue_position"], 1)
        self.assertEqual(self.intake(first_prompt)["run_id"], first["run_id"])

    def test_queued_prompt_drift_requires_explicit_rerun_before_activation(
        self,
    ) -> None:
        first_prompt = self.prompt_path()
        first = self.intake(first_prompt)
        manifest = Path(str(self.initialize()["workspace"]))
        created = workspace.create_prompt(
            manifest,
            "Implement a second queued objective",
            id_factory=lambda: "2" * 32,
        )
        second_prompt = Path(str(created["path"]))
        queued = self.intake(second_prompt)
        self.assertEqual(queued["action"], "queued")
        self.assertEqual(queued["queue_position"], 1)
        self.assertEqual(
            workspace.queue_rows(manifest)[0]["source_path"], second_prompt.name
        )

        second_prompt.write_text(
            second_prompt.read_text(encoding="utf-8").rstrip()
            + "\n\n## Context\n\nUse the updated queued context.\n",
            encoding="utf-8",
        )
        second_prompt.chmod(0o600)
        self.set_run_status(first, "complete")
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.activate_queue_head(manifest)
        self.assertEqual(caught.exception.code, "QUEUED_PROMPT_DRIFT")

        activated = self.intake(second_prompt)
        self.assertEqual(activated["action"], "new")
        self.assertEqual(activated["status"], "activated")
        self.assertEqual(workspace.queue_rows(manifest), [])

    def test_queued_prompt_raw_drift_requires_explicit_rerun(self) -> None:
        first_prompt = self.prompt_path()
        first = self.intake(first_prompt)
        manifest = Path(str(self.initialize()["workspace"]))
        created = workspace.create_prompt(
            manifest,
            "Implement a second queued objective",
            id_factory=lambda: "2" * 32,
        )
        second_prompt = Path(str(created["path"]))
        self.intake(second_prompt)

        second_prompt.write_text(
            second_prompt.read_text(encoding="utf-8").replace(
                "## Ask", "<!-- formatting after acceptance -->\n\n## Ask"
            ),
            encoding="utf-8",
        )
        second_prompt.chmod(0o600)
        self.set_run_status(first, "complete")
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.activate_queue_head(manifest)
        self.assertEqual(caught.exception.code, "QUEUED_PROMPT_DRIFT")

        activated = self.intake(second_prompt)
        self.assertEqual(activated["action"], "new")
        self.assertEqual(activated["status"], "activated")
        self.assertEqual(workspace.queue_rows(manifest), [])

    def test_completed_follow_up_queues_until_resources_release(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt)
        manifest = Path(str(self.initialize()["workspace"]))
        self.set_run_status(first, "complete")
        self.edit_prompt(prompt, "Add a follow-up after resource release.")

        with mock.patch.object(workspace, "run_resources_released", return_value=False):
            queued = self.intake(prompt)
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["queue_position"], 1)

        activated = workspace.activate_queue_head(manifest)
        self.assertEqual(activated["status"], "activated")
        binding = workspace.validate_binding(
            Path(str(activated["snapshot"])).parents[2]
        )
        self.assertEqual(binding["predecessor"]["run_id"], first["run_id"])
        self.assertEqual(binding["revisions"][0]["kind"], "completed_follow_up")

    def test_queue_activation_reads_authoritative_execution_resources(self) -> None:
        first_prompt = self.prompt_path()
        first = self.intake(first_prompt)
        manifest = Path(str(self.initialize()["workspace"]))
        created = workspace.create_prompt(
            manifest,
            "Implement another queued objective",
            id_factory=lambda: "3" * 32,
        )
        second_prompt = Path(str(created["path"]))
        self.intake(second_prompt)
        self.set_run_status(first, "complete")
        first_run_dir = Path(str(first["snapshot"])).parents[2]
        integration = first_run_dir / "worktrees" / "FEAT-001" / "integration"
        integration.mkdir(parents=True)
        write_private(
            first_run_dir / "execution" / "FEAT-001" / "coordinator.json",
            {
                "schema": "agentic-sdlc/execution-coordinator-v7",
                "run_id": first["run_id"],
                "status": "done",
                "active_wave": None,
                "cleanup_retained": [],
                "integration_worktree": str(integration),
            },
        )

        waiting = workspace.activate_queue_head(manifest)
        self.assertEqual(waiting["status"], "waiting_for_resource_release")
        integration.rmdir()
        activated = workspace.activate_queue_head(manifest)
        self.assertEqual(activated["status"], "activated")

    def test_queue_activation_recovers_after_interrupted_dequeue(self) -> None:
        first_prompt = self.prompt_path()
        first = self.intake(first_prompt)
        manifest = Path(str(self.initialize()["workspace"]))
        created = workspace.create_prompt(
            manifest,
            "Implement another queued objective",
            id_factory=lambda: "3" * 32,
        )
        second_prompt = Path(str(created["path"]))
        self.intake(second_prompt)
        self.set_run_status(first, "complete")

        with mock.patch.object(
            workspace,
            "resolve_queue_entry",
            side_effect=RuntimeError("injected dequeue interruption"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected dequeue"):
                workspace.activate_queue_head(manifest)

        recovered = workspace.activate_queue_head(manifest)
        self.assertEqual(recovered["status"], "activated")
        self.assertTrue(recovered["recovered"])
        self.assertEqual(recovered["prompt_id"], "prompt-" + "3" * 32)
        self.assertEqual(workspace.queue_rows(manifest), [])

    def test_queue_cancel_reports_already_committed_activation(self) -> None:
        first_prompt = self.prompt_path()
        first = self.intake(first_prompt)
        manifest = Path(str(self.initialize()["workspace"]))
        created = workspace.create_prompt(
            manifest,
            "Implement another queued objective",
            id_factory=lambda: "3" * 32,
        )
        second_prompt = Path(str(created["path"]))
        self.intake(second_prompt)
        self.set_run_status(first, "complete")
        with mock.patch.object(
            workspace,
            "resolve_queue_entry",
            side_effect=RuntimeError("injected dequeue interruption"),
        ):
            with self.assertRaises(RuntimeError):
                workspace.activate_queue_head(manifest)

        canceled = workspace.cancel_queued_prompt(manifest, second_prompt.name)
        self.assertEqual(canceled["action"], "queue_already_activated")
        self.assertEqual(canceled["prompt_id"], "prompt-" + "3" * 32)
        self.assertEqual(workspace.queue_rows(manifest), [])

    def test_formatting_only_edit_does_not_create_steering(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt)
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "## Ask", "<!-- formatting-only -->\n\n## Ask"
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        resumed = self.intake(prompt)
        self.assertEqual(resumed["action"], "resume")
        self.assertEqual(resumed["revision"], "r0001")
        self.assertEqual(resumed["run_id"], first["run_id"])
        self.assertEqual(
            resumed["sha256"],
            hashlib.sha256(Path(str(resumed["snapshot"])).read_bytes()).hexdigest(),
        )

    def test_fenced_code_indentation_is_semantic(self) -> None:
        prompt = self.prompt_path()
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "Implement the requested behavior and verify its observable acceptance criteria.",
                "Implement this YAML exactly:\n\n```yaml\nroot:\n  child: value\n```",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        first = self.intake(prompt)
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "  child: value", "    child: value"
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        changed = self.intake(prompt)
        self.assertEqual(changed["run_id"], first["run_id"])
        self.assertEqual(changed["revision"], "r0002")
        self.assertEqual(changed["action"], "steering")

    def test_fenced_markdown_headings_remain_inside_ask(self) -> None:
        prompt = self.prompt_path()
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "Implement the requested behavior and verify its observable acceptance criteria.",
                "Document these examples:\n\n"
                "```markdown\n## Ask\nNested backtick example\n```\n\n"
                "~~~markdown\n## Context\nNested tilde example\n~~~",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        document = workspace.parse_prompt(prompt)
        self.assertIn("## Ask", document["sections"]["Ask"])
        self.assertIn("## Context", document["sections"]["Ask"])
        self.assertNotIn("Context", document["sections"])

    def test_interrupted_refinement_reset_retries_from_binding_commit_point(
        self,
    ) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt)
        self.edit_prompt(prompt, "Apply the accepted revision atomically.")
        with mock.patch.object(
            workspace,
            "begin_requirements_refinement",
            side_effect=OSError("injected refinement write failure"),
        ):
            with self.assertRaises(OSError):
                self.intake(prompt)
        run_dir = Path(str(first["snapshot"])).parents[2]
        binding = json.loads((run_dir / "prompt.json").read_text(encoding="utf-8"))
        self.assertEqual(len(binding["revisions"]), 1)

        retried = self.intake(prompt)
        self.assertEqual(retried["revision"], "r0002")
        refinement = json.loads(
            (run_dir / "requirements-refinement.json").read_text(encoding="utf-8")
        )
        self.assertEqual(refinement["revision"], "r0002")

        self.edit_prompt(prompt, "Add one more accepted change.")
        original_write = workspace.write_atomic

        def fail_binding_commit(path: Path, content: bytes) -> None:
            if path.name == "prompt.json":
                raise OSError("injected binding commit failure")
            original_write(path, content)

        with mock.patch.object(
            workspace, "write_atomic", side_effect=fail_binding_commit
        ):
            with self.assertRaises(OSError):
                self.intake(prompt)
        self.assertEqual(
            len(
                json.loads((run_dir / "prompt.json").read_text(encoding="utf-8"))[
                    "revisions"
                ]
            ),
            2,
        )
        committed = self.intake(prompt)
        self.assertEqual(committed["revision"], "r0003")

    def test_binding_validation_binds_intent_and_follow_up_kind(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt)
        first_run_dir = Path(str(first["snapshot"])).parents[2]
        binding_path = first_run_dir / "prompt.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["revisions"][0]["intent_sha256"] = "0" * 64
        write_private(binding_path, binding)
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.validate_binding(first_run_dir)
        self.assertEqual(caught.exception.code, "RUN_STATE_INVALID")

        binding["revisions"][0]["intent_sha256"] = workspace.parse_prompt(prompt)[
            "intent_sha256"
        ]
        write_private(binding_path, binding)
        self.set_run_status(first, "complete")
        self.edit_prompt(prompt, "Add a completed follow-up objective.")
        follow_up = self.intake(prompt)
        follow_up_dir = Path(str(follow_up["snapshot"])).parents[2]
        follow_up_path = follow_up_dir / "prompt.json"
        follow_up_binding = json.loads(follow_up_path.read_text(encoding="utf-8"))
        follow_up_binding["revisions"][0]["kind"] = "initial"
        write_private(follow_up_path, follow_up_binding)
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.validate_binding(follow_up_dir)
        self.assertEqual(caught.exception.code, "RUN_STATE_INVALID")

    def test_requirements_refinement_blocks_ready_with_material_question(
        self,
    ) -> None:
        first = self.intake(self.prompt_path())
        run_dir = Path(str(first["snapshot"])).parents[2]
        state = workspace.load_requirements_refinement(run_dir, required=True)
        assert state is not None
        self.assertEqual(state["status"], "extracting")
        state["questions"] = [
            {
                "id": "Q-001",
                "question": "Which compatibility boundary is required?",
                "material": True,
                "status": "open",
                "answer": None,
                "source": None,
                "source_revision": None,
                "conflict": None,
            }
        ]
        state["compiled_requirements_sha256"] = "c" * 64
        state["status"] = "ready"
        refinement_path = run_dir / "requirements-refinement.json"
        valid_bytes = refinement_path.read_bytes()
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.save_requirements_refinement(run_dir, state)
        self.assertEqual(caught.exception.code, "RUN_STATE_INVALID")
        self.assertEqual(refinement_path.read_bytes(), valid_bytes)
        state["questions"][0].update(
            {
                "status": "answered",
                "answer": "Use only the prompt-v2 boundary.",
                "source": "prompt",
                "source_revision": "r0001",
            }
        )
        saved = workspace.save_requirements_refinement(run_dir, state)
        self.assertEqual(saved["status"], "ready")

    def test_requirements_refinement_contract_binds_latest_compiled_file(
        self,
    ) -> None:
        first = self.intake(self.prompt_path())
        run_dir = Path(str(first["snapshot"])).parents[2]
        manifest = run_dir.parent / "workspace.json"
        requirements = self.project / "docs" / "requirements.md"
        design = self.project / "docs" / "design.md"
        requirements.parent.mkdir()
        requirements.write_text(
            """<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v2 -->
# Requirements

<!-- REQUIREMENT: REQ-001 status=active priority=P0 type=feature -->
### REQ-001: Keep behavior current

#### User Story

As a user, I need observable accepted behavior.

#### Acceptance Criteria

- AC-001: The behavior is observable.

#### Negative Criteria

- NC-001: Stale intent does not progress.

#### Validation Method

Run canonical validation.

#### Test Method

Run focused tests.

#### Evaluation Method

Inspect the receipt.

<!-- /REQUIREMENT: REQ-001 -->
<!-- maintain-project-specs:requirements:end -->
""",
            encoding="utf-8",
        )
        design.write_text(
            """<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v2 -->
# Design

<!-- FEATURE: FEAT-001 reqs=REQ-001 status=ready delivery=unassessed priority=P0 version=1 -->
### FEAT-001: Keep one owner

#### Requirements Covered

- REQ-001

#### Context Evidence

Canonical owner tests.

#### Design Details

Validate impact coverage before progression.

#### Selected Option

Use one owner receipt.

#### Alternatives Considered

Separate validators create ambiguity.

#### Implementation Boundaries

The owner validates project contracts.

#### Test-First Success Criteria

- TDD-001: Stale coverage fails.

#### Validation Plan

Run validation.

#### Test Plan

Run focused tests.

#### Evaluation Plan

Inspect the outcome.

#### Rollout And Rollback

Fail closed on downgrade.

#### Done Definition

The prompt revision is traceable.

#### Implementation Evidence

The implementation predates schema v2 evidence tracking.

#### Verification Evidence

Independent verification predates schema v2 evidence tracking.

<!-- /FEATURE: FEAT-001 -->
<!-- maintain-project-specs:design:end -->
""",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.name", "SDLC Test"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.project),
                "config",
                "user.email",
                "sdlc-test@example.invalid",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "add", "docs"], check=True
        )
        subprocess.run(
            ["git", "-C", str(self.project), "commit", "-qm", "specs"],
            check=True,
        )
        bound_requirements = requirements.read_bytes()
        state = workspace.load_requirements_refinement(run_dir, required=True)
        assert state is not None
        state["status"] = "ready"
        state["extracted"]["constraints"] = [
            "Keep the accepted behavior current."
        ]
        state["compiled_requirements_sha256"] = hashlib.sha256(
            requirements.read_bytes()
        ).hexdigest()
        workspace.save_requirements_refinement(run_dir, state)
        write_private(
            run_dir / "prompt-impact-claim.json",
            {
                "schema": "agentic-sdlc/prompt-impact-claim-v1",
                "prompt_id": state["prompt_id"],
                "revision": state["revision"],
                "intent_sha256": state["intent_sha256"],
                "dispositions": [
                    {
                        "statement": "constraints:0001",
                        "disposition": "existing_contract",
                        "requirements": ["REQ-001"],
                        "design": ["FEAT-001"],
                        "effects": [],
                        "reason": None,
                    }
                ],
                "declared_effects": [],
                "declared_plan_action": "retain_plan",
            },
        )

        verified = workspace.verify_requirements_refinement_contract(
            manifest, str(first["run_id"])
        )
        self.assertEqual(verified["revision"], "r0001")
        self.assertEqual(verified["impact"]["classification"], "no_effect")
        row = next(
            item
            for item in workspace.prompt_rows(manifest, None, None)
            if item.get("prompt_ref") == first["prompt_ref"]
        )
        self.assertEqual(
            row["impact"],
            {
                "classification": "no_effect",
                "requirements": ["REQ-001"],
                "design": ["FEAT-001"],
                "reasons": [],
                "plan_action": "retain_plan",
            },
        )
        serialized_impact = json.dumps(row["impact"], sort_keys=True)
        for forbidden in ("sha256", "statement", "prompt-", str(run_dir)):
            self.assertNotIn(forbidden, serialized_impact)
        ledger = run_dir / "prompt-impact" / "ledger.json"
        ledger_backing = ledger.with_name("ledger.backing")
        ledger.rename(ledger_backing)
        os.link(ledger_backing, ledger)
        with self.assertRaises(workspace.PromptWorkspaceError) as hardlinked:
            workspace.verify_requirements_refinement_contract(
                manifest, str(first["run_id"])
            )
        self.assertEqual(hardlinked.exception.code, "RUN_STATE_INVALID")
        ledger.unlink()
        ledger_backing.rename(ledger)

        claim_path = run_dir / "prompt-impact-claim.json"
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        claim["dispositions"][0].update(
            {
                "disposition": "non_contract",
                "requirements": [],
                "design": [],
                "reason": "clarification_context",
            }
        )
        write_private(claim_path, claim)
        write_private(run_dir / "prompt-impact" / "attempt-0002.json", {"orphan": True})
        recovered = workspace.verify_requirements_refinement_contract(
            manifest, str(first["run_id"])
        )
        self.assertEqual(recovered["impact"]["classification"], "no_effect")
        ledger_value = json.loads(ledger.read_text(encoding="utf-8"))
        self.assertEqual(ledger_value["current"]["generation"], 3)
        with ThreadPoolExecutor(max_workers=4) as executor:
            concurrent = list(
                executor.map(
                    lambda _index: workspace.verify_requirements_refinement_contract(
                        manifest, str(first["run_id"])
                    )["impact_sha256"],
                    range(4),
                )
            )
        self.assertEqual(len(set(concurrent)), 1)
        self.assertEqual(
            sorted(path.name for path in ledger.parent.glob("attempt-*.json")),
            ["attempt-0001.json", "attempt-0002.json", "attempt-0003.json"],
        )
        impact_module = sys.modules[workspace.publish_prompt_impact.__module__]
        ledger_bytes = ledger.read_bytes()
        with self.assertRaises(impact_module.PromptImpactError) as stale:
            impact_module._publish_ledger_cas(
                run_dir, "0" * 64, ledger_value
            )
        self.assertEqual(stale.exception.code, "CONCURRENT_MODIFICATION")
        self.assertEqual(ledger.read_bytes(), ledger_bytes)
        requirements.write_text(
            requirements.read_text(encoding="utf-8") + "\nUnbound drift.\n",
            encoding="utf-8",
        )
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.verify_requirements_refinement_contract(
                manifest, str(first["run_id"])
            )
        self.assertEqual(caught.exception.code, "REQUIREMENTS_REFINEMENT_REQUIRED")

        requirements.unlink()
        design.unlink()
        requirements.parent.rmdir()
        foreign_docs = self.root / "foreign-docs"
        foreign_docs.mkdir()
        (foreign_docs / "requirements.md").write_bytes(bound_requirements)
        requirements.parent.symlink_to(foreign_docs, target_is_directory=True)
        with self.assertRaises(workspace.PromptWorkspaceError) as unsafe:
            workspace.verify_requirements_refinement_contract(
                manifest, str(first["run_id"])
            )
        self.assertEqual(unsafe.exception.code, "REQUIREMENTS_REFINEMENT_REQUIRED")

    def test_legacy_unfinished_run_fails_closed_and_completed_is_readable(self) -> None:
        prompt = self.prompt_path()
        initialized = self.initialize()
        project_dir = Path(str(initialized["workspace"])).parent
        run_id = "run-legacy"
        run_dir = project_dir / run_id
        run_dir.mkdir(mode=0o700)
        write_private(run_dir / "run.json", {"status": "running"})
        write_private(project_dir / "active-run.json", {"run_id": run_id})
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            self.intake(prompt)
        self.assertEqual(caught.exception.code, "WORKFLOW_UPGRADE_REQUIRED")
        write_private(run_dir / "run.json", {"status": "complete"})
        result = self.intake(prompt)
        self.assertEqual(result["action"], "new")
        self.assertTrue(run_dir.exists())

    def test_bound_pre_impact_status_is_pending_then_historical(self) -> None:
        first = self.intake(self.prompt_path())
        run_dir = Path(str(first["snapshot"])).parents[2]
        manifest = run_dir.parent / "workspace.json"
        pending = next(
            item
            for item in workspace.prompt_rows(manifest, None, None)
            if item.get("prompt_ref") == first["prompt_ref"]
        )
        self.assertEqual(pending["impact"]["classification"], "pending")
        self.assertEqual(
            pending["impact"]["plan_action"],
            "clarification_or_reconciliation_required",
        )

        self.set_run_status(first, "complete")
        historical = next(
            item
            for item in workspace.prompt_rows(manifest, None, None)
            if item.get("prompt_ref") == first["prompt_ref"]
        )
        self.assertEqual(
            historical["impact"]["classification"], "historical_no_receipt"
        )
        self.assertEqual(historical["impact"]["plan_action"], "none")

    def test_foreign_symlink_tamper_and_sensitive_input_are_rejected(self) -> None:
        prompt = self.prompt_path()
        foreign = self.root / "foreign.md"
        foreign.write_bytes(prompt.read_bytes())
        foreign.chmod(0o600)
        with self.assertRaises(workspace.PromptWorkspaceError):
            self.intake(foreign)
        link = prompt.with_name("linked.md")
        link.symlink_to(prompt)
        with self.assertRaises(workspace.PromptWorkspaceError):
            self.intake(link)
        with self.assertRaises(workspace.PromptWorkspaceError):
            self.intake(link.name)
        link.unlink()
        original = prompt.read_text(encoding="utf-8")
        assignments = (
            "GITHUB" + "_TOKEN = ghp_" + "x" * 24,
            "GITHUB" + "_TOKEN = ghp_" + "x" * 24 + " # example",
            "OPENAI" + "_API_KEY = sk-" + "x" * 24,
            "token = " + "x" * 16,
        )
        for sensitive_assignment in assignments:
            with self.subTest(secret_kind=sensitive_assignment.split(" ", 1)[0]):
                text = original.rstrip() + (
                    f"\n\n## Steering\n\n{sensitive_assignment}\n"
                )
                prompt.write_text(text, encoding="utf-8")
                prompt.chmod(0o600)
                with self.assertRaises(workspace.PromptWorkspaceError) as caught:
                    self.intake(prompt)
                self.assertEqual(caught.exception.code, "PROMPT_SENSITIVE_INPUT")

    def test_public_nebius_metadata_is_not_secret_input(self) -> None:
        prefix = "NEBIUS" + "_"
        assignments = (
            prefix + "PROFILE = codex-agent-project-1234567890",
            prefix + "PROJECT_ID = project-1234567890",
            prefix
            + "AUTH_CREDENTIALS_FILE = "
            + "/tmp/codex-agent-authkey.project-1234567890.json",
        )

        for assignment in assignments:
            with self.subTest(variable=assignment.split(" ", 1)[0]):
                self.assertFalse(workspace.contains_secret(assignment))

    def test_public_nebius_metadata_cannot_mask_secret_input(self) -> None:
        prefix = "NEBIUS" + "_"
        text = (
            prefix
            + "PROFILE = codex-agent-project-1234567890 "
            + prefix
            + "IAM_"
            + "TOKEN = "
            + "x" * 32
        )

        self.assertTrue(workspace.contains_secret(text))

    def test_snapshot_tamper_fails_closed(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt)
        snapshot = Path(str(first["snapshot"]))
        snapshot.write_text("tampered\n", encoding="utf-8")
        snapshot.chmod(0o600)
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            self.intake(prompt)
        self.assertEqual(caught.exception.code, "RUN_STATE_INVALID")

    def test_concurrent_unchanged_intake_creates_one_run_and_revision(self) -> None:
        prompt = self.prompt_path()
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(
                executor.map(lambda _index: self.intake(prompt.name), range(6))
            )
        self.assertEqual(len({str(result["run_id"]) for result in results}), 1)
        self.assertEqual([result["action"] for result in results].count("new"), 1)
        self.assertTrue(all(result["revision"] == "r0001" for result in results))

    def test_missing_active_pointer_is_repaired_from_one_bound_run(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt)
        project_dir = Path(str(first["snapshot"])).parents[3]
        (project_dir / "active-run.json").unlink()
        resumed = self.intake(prompt.name)
        self.assertEqual(resumed["action"], "resume")
        self.assertEqual(resumed["run_id"], first["run_id"])
        pointer = json.loads(
            (project_dir / "active-run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(pointer["run_id"], first["run_id"])

    def test_multiple_unfinished_runs_fail_closed(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt)
        project_dir = Path(str(first["snapshot"])).parents[3]
        other = project_dir / "run-conflict"
        other.mkdir(mode=0o700)
        write_private(other / "run.json", {"status": "running"})
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            self.intake(prompt.name)
        self.assertEqual(caught.exception.code, "RUN_STATE_INVALID")

    def test_interrupted_revision_snapshot_is_adopted_once(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt)
        self.edit_prompt(prompt, "Recover this interrupted steering revision.")
        run_dir = Path(str(first["snapshot"])).parents[2]
        revision_dir = run_dir / "inputs" / "r0002"
        revision_dir.mkdir(mode=0o700)
        snapshot = revision_dir / "prompt.md"
        snapshot.write_bytes(prompt.read_bytes())
        snapshot.chmod(0o600)
        recovered = self.intake(prompt.name)
        self.assertEqual(recovered["action"], "steering")
        self.assertEqual(recovered["revision"], "r0002")
        self.assertEqual(self.intake(prompt.name)["revision"], "r0002")

    def test_cli_intake_does_not_expose_internal_ids_or_snapshot_paths(self) -> None:
        prompt = self.prompt_path()
        result = subprocess.run(
            [
                "python3",
                str(MODULE_PATH),
                "intake",
                str(prompt),
                "--project-path",
                str(self.project),
                "--codex-home",
                str(self.codex_home),
                "--json",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        value = json.loads(result.stdout)
        self.assertNotIn("project_id", value)
        self.assertNotIn("run_id", value)
        self.assertNotIn("sha256", value)
        self.assertNotIn("snapshot", value)

    def test_malformed_oversize_and_unsafe_mode_fail_before_run_creation(self) -> None:
        prompt = self.prompt_path()
        project_dir = Path(str(self.initialize()["workspace"])).parent
        original = prompt.read_bytes()
        prompt.write_bytes(b"\xff\xfe")
        prompt.chmod(0o600)
        with self.assertRaises(workspace.PromptWorkspaceError) as invalid_utf8:
            self.intake(prompt)
        self.assertEqual(invalid_utf8.exception.code, "PROMPT_INPUT_INVALID")
        self.assertFalse((project_dir / "active-run.json").exists())
        prompt.write_bytes(original + b"x" * workspace.MAX_PROMPT_BYTES)
        prompt.chmod(0o600)
        with self.assertRaises(workspace.PromptWorkspaceError) as oversize:
            self.intake(prompt)
        self.assertEqual(oversize.exception.code, "PROMPT_INPUT_INVALID")
        self.assertFalse((project_dir / "active-run.json").exists())
        prompt.write_bytes(original)
        prompt.chmod(0o644)
        with self.assertRaises(workspace.PromptWorkspaceError) as permissions:
            self.intake(prompt)
        self.assertEqual(permissions.exception.code, "WORKSPACE_PERMISSION_INVALID")
        self.assertFalse((project_dir / "active-run.json").exists())

    def test_editor_workspace_new_prompt_history_and_metadata_listing(self) -> None:
        initialized = self.initialize()
        editor = json.loads(
            Path(str(initialized["editor_workspace"])).read_text(encoding="utf-8")
        )
        labels = [task["label"] for task in editor["tasks"]["tasks"]]
        self.assertEqual(
            labels,
            [
                "Agentic SDLC: New Prompt",
                "Agentic SDLC: Prompt History",
                "Agentic SDLC: Prompt Queue",
                "Agentic SDLC: Cancel Queued Prompt",
            ],
        )
        manifest = Path(str(initialized["workspace"]))
        created_at = datetime(2026, 7, 16, 12, 30, tzinfo=timezone.utc)
        first = workspace.create_prompt(
            manifest,
            "Add reports; $(touch nope) `unsafe`",
            clock=lambda: created_at,
            id_factory=lambda: "1" * 32,
        )
        second = workspace.create_prompt(
            manifest,
            "Add reports; $(touch nope) `unsafe`",
            clock=lambda: created_at,
            id_factory=lambda: "2" * 32,
        )
        self.assertNotEqual(first["path"], second["path"])
        self.assertTrue(Path(str(first["path"])).name.startswith("11111--"))
        self.assertTrue(Path(str(second["path"])).name.startswith("22222--"))
        self.assertFalse((self.project / "nope").exists())
        rows = workspace.prompt_rows(manifest, "reports", "2026-07-16")
        self.assertEqual(len(rows), 2)
        serialized = json.dumps(rows)
        self.assertNotIn("prompt-" + "1" * 32, serialized)
        self.assertNotIn("sha256", serialized)
        self.assertNotIn("Add reports; $(touch nope) `unsafe`\n\n##", serialized)
        self.assertTrue(all(row["status"] == "draft" for row in rows))

    def test_activity_is_monotonic_and_rejected_intake_does_not_reorder(self) -> None:
        prompt = self.prompt_path()
        result = self.intake(prompt)
        project_dir = Path(str(result["snapshot"])).parents[3]
        prompt_id = str(workspace.prompt_metadata(prompt)["prompt_id"])
        future = datetime(2030, 1, 1, tzinfo=timezone.utc)
        workspace.update_activity(project_dir, prompt_id, future)
        workspace.update_activity(
            project_dir, prompt_id, datetime(2020, 1, 1, tzinfo=timezone.utc)
        )
        before = (project_dir / "activity.json").read_bytes()
        safe = prompt.read_bytes()
        self.edit_prompt(prompt, "token = " + "x" * 16)
        with self.assertRaises(workspace.PromptWorkspaceError):
            self.intake(prompt)
        self.assertEqual((project_dir / "activity.json").read_bytes(), before)
        prompt.write_bytes(safe)
        prompt.chmod(0o600)
        rows = workspace.prompt_rows(project_dir / "workspace.json", None, None)
        row = next(item for item in rows if item["path"] == str(prompt.resolve()))
        self.assertEqual(row["last_invoked_at"], future.isoformat(timespec="seconds"))

    def test_exact_manual_rename_repairs_binding_and_run_mirror(self) -> None:
        prompt = self.prompt_path()
        result = self.intake(prompt)
        run_dir = Path(str(result["snapshot"])).parents[2]
        write_private(
            run_dir / "run.json",
            {
                "status": "running",
                "prompt": {"filename": prompt.name},
                "prompt_filename": prompt.name,
            },
        )
        prompt_ref = str(workspace.parse_prompt(prompt)["prompt_ref"])
        renamed = prompt.with_name(f"{prompt_ref}--renamed-product-prompt.md")
        prompt.rename(renamed)
        accepted = self.intake(renamed.name)
        self.assertTrue(accepted["renamed"])
        binding = json.loads((run_dir / "prompt.json").read_text(encoding="utf-8"))
        mirror = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["prompt_filename"], renamed.name)
        self.assertEqual(mirror["prompt_filename"], renamed.name)
        self.assertEqual(mirror["prompt"]["filename"], renamed.name)

    def test_rename_and_edit_or_stale_copy_fails_closed(self) -> None:
        prompt = self.prompt_path()
        result = self.intake(prompt)
        project_dir = Path(str(result["snapshot"])).parents[3]
        prompt_ref = str(workspace.parse_prompt(prompt)["prompt_ref"])
        renamed = prompt.with_name(f"{prompt_ref}--renamed-and-edited.md")
        prompt.rename(renamed)
        self.edit_prompt(renamed, "Change during rename.")
        before = (project_dir / "activity.json").read_bytes()
        with self.assertRaises(workspace.PromptWorkspaceError) as drift:
            self.intake(renamed.name)
        self.assertEqual(drift.exception.code, "PROMPT_DRIFT")
        self.assertEqual((project_dir / "activity.json").read_bytes(), before)
        renamed.write_bytes(Path(str(result["snapshot"])).read_bytes())
        renamed.chmod(0o600)
        stale = renamed.with_name(f"{prompt_ref}--stale-copy.md")
        stale.write_bytes(renamed.read_bytes())
        stale.chmod(0o600)
        with self.assertRaises(workspace.PromptWorkspaceError) as conflict:
            self.intake(renamed.name)
        self.assertEqual(conflict.exception.code, "PROMPT_CONFLICT")

    def test_v2_workspace_migrates_once_and_repairs_run_mirrors(self) -> None:
        prompt = self.prompt_path()
        result = self.intake(prompt)
        snapshot = Path(str(result["snapshot"]))
        run_dir = snapshot.parents[2]
        prompt_ref = str(workspace.parse_prompt(prompt)["prompt_ref"])
        legacy_name = prompt.name.removeprefix(f"{prompt_ref}--")
        legacy_path = prompt.with_name(legacy_name)
        v2 = prompt.read_text(encoding="utf-8").replace(
            "schema: agentic-sdlc/prompt-v3",
            "schema: agentic-sdlc/prompt-v2",
        ).replace(f"prompt_ref: {prompt_ref}\n", "") + (
            "\n## Context\n\n"
            "schema: body-schema-must-stay\n"
            "prompt_id: body-identity-must-stay\n"
        )
        prompt.unlink()
        legacy_path.write_text(v2, encoding="utf-8")
        legacy_path.chmod(0o600)
        binding = json.loads((run_dir / "prompt.json").read_text(encoding="utf-8"))
        binding["prompt_filename"] = legacy_name
        snapshot_bytes = snapshot.read_bytes().replace(
            b"schema: agentic-sdlc/prompt-v3",
            b"schema: agentic-sdlc/prompt-v2",
        ).replace(f"prompt_ref: {prompt_ref}\n".encode(), b"")
        snapshot.write_bytes(snapshot_bytes)
        snapshot.chmod(0o600)
        binding["revisions"][0]["sha256"] = hashlib.sha256(snapshot_bytes).hexdigest()
        binding["revisions"][0]["bytes"] = len(snapshot_bytes)
        write_private(run_dir / "prompt.json", binding)
        write_private(
            run_dir / "run.json",
            {"prompt": {"filename": legacy_name}, "prompt_filename": legacy_name},
        )

        migrated = self.initialize()
        target = Path(str(migrated["starter_prompt"]))
        document = workspace.parse_prompt(target)
        migrated_text = bytes(document["raw"]).decode("utf-8")
        self.assertEqual(document["prompt_ref"], prompt_ref)
        self.assertIn("schema: body-schema-must-stay", migrated_text)
        self.assertIn("prompt_id: body-identity-must-stay", migrated_text)
        self.assertEqual(migrated_text.count(f"prompt_ref: {prompt_ref}"), 1)
        self.assertEqual(snapshot.read_bytes(), snapshot_bytes)
        repaired = json.loads((run_dir / "prompt.json").read_text(encoding="utf-8"))
        mirror = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(repaired["prompt_filename"], target.name)
        self.assertEqual(mirror["prompt_filename"], target.name)
        self.assertEqual(mirror["prompt"]["filename"], target.name)
        self.assertEqual(self.initialize()["starter_prompt"], str(target))

    def test_prompt_refs_resolve_exactly_and_extend_on_collision(self) -> None:
        manifest = Path(str(self.initialize()["workspace"]))
        first = workspace.create_prompt(
            manifest,
            "First collision objective",
            id_factory=lambda: "abcde0" + "0" * 26,
        )
        second = workspace.create_prompt(
            manifest,
            "Second collision objective",
            id_factory=lambda: "abcde1" + "1" * 26,
        )
        self.assertEqual(first["prompt_ref"], "abcde")
        self.assertEqual(second["prompt_ref"], "abcde1")
        second_result = self.intake(Path(str(second["prompt_ref"])))
        self.assertEqual(second_result["prompt_ref"], "abcde1")

    def test_v2_migration_recovers_from_journaled_old_source(self) -> None:
        prompt = self.prompt_path()
        document = workspace.parse_prompt(prompt)
        prompt_ref = str(document["prompt_ref"])
        v2 = prompt.read_bytes().replace(
            b"schema: agentic-sdlc/prompt-v3",
            b"schema: agentic-sdlc/prompt-v2",
        ).replace(f"prompt_ref: {prompt_ref}\n".encode(), b"")
        legacy = prompt.with_name(prompt.name.removeprefix(f"{prompt_ref}--"))
        prompt.unlink()
        legacy.write_bytes(v2)
        legacy.chmod(0o600)
        migrated = workspace._render_migrated_prompt(v2, prompt_ref)
        item = {
            "prompt_id": str(document["prompt_id"]),
            "prompt_ref": prompt_ref,
            "old_name": legacy.name,
            "new_name": f"{prompt_ref}--{legacy.name}",
            "old_sha256": hashlib.sha256(v2).hexdigest(),
            "new_sha256": hashlib.sha256(migrated).hexdigest(),
        }
        marker = prompt.parent.parent / "prompt-v3-migration.json"
        workspace.write_atomic(
            marker,
            workspace.stable_json(
                {
                    "schema": workspace.PROMPT_V3_MIGRATION_SCHEMA,
                    "migrations": [item],
                }
            ),
        )
        self.assertEqual(workspace.migrate_prompt_files_v2(prompt.parent), [item])
        self.assertFalse(legacy.exists())
        self.assertEqual((prompt.parent / item["new_name"]).read_bytes(), migrated)

    def test_v3_queue_pointer_rewrite_is_replay_safe(self) -> None:
        manifest = Path(str(self.initialize()["workspace"]))
        created = workspace.create_prompt(manifest, "Queued migration objective")
        prompt = Path(str(created["path"]))
        document = workspace.parse_prompt(prompt)
        project_dir = manifest.parent
        workspace.enqueue_prompt(
            project_dir,
            document,
            datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        prompt_ref = str(document["prompt_ref"])
        v2 = prompt.read_bytes().replace(
            b"schema: agentic-sdlc/prompt-v3",
            b"schema: agentic-sdlc/prompt-v2",
        ).replace(f"prompt_ref: {prompt_ref}\n".encode(), b"")
        legacy = prompt.with_name(prompt.name.removeprefix(f"{prompt_ref}--"))
        prompt.unlink()
        legacy.write_bytes(v2)
        legacy.chmod(0o600)
        queue = workspace.load_prompt_queue(project_dir)
        queue["entries"][0]["source_path"] = legacy.name
        workspace.save_prompt_queue(project_dir, queue)

        migrations = workspace.migrate_prompt_files_v2(prompt.parent)
        workspace.rewrite_prompt_v3_references(project_dir, prompt.parent, migrations)
        workspace.rewrite_prompt_v3_references(project_dir, prompt.parent, migrations)
        repaired = workspace.load_prompt_queue(project_dir)["entries"][0]
        self.assertEqual(repaired["source_path"], migrations[0]["new_name"])
        self.assertEqual(
            (project_dir / str(repaired["snapshot"])).read_bytes(),
            (prompt.parent / migrations[0]["new_name"]).read_bytes(),
        )

    def test_session_projection_creates_lossless_prompt_and_rejects_stale_merge(self) -> None:
        manifest = Path(str(self.initialize()["workspace"]))
        refined = self.root / "refined.md"
        refinement = (
            "Implement automatic SDLC prompt intake.\n\n"
            "- Preserve exact session provenance.\n"
            "- Keep manual edits inert until explicit run.\n"
        )
        refined.write_text(refinement, encoding="utf-8")
        refined.chmod(0o600)
        created = workspace.merge_session_projection(
            manifest,
            refined,
            prompt_reference=None,
            expected_sha256=None,
            new_objective=True,
            operation_id="1" * 64,
            projection_sha256=file_sha256(refined),
            clock=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        prompt = Path(str(created["path"]))
        self.assertIn(refinement.strip(), prompt.read_text(encoding="utf-8"))
        base = workspace.parse_prompt(prompt)

        delta = self.root / "delta.md"
        delta.write_text("Also retain collision-safe prompt references.", encoding="utf-8")
        delta.chmod(0o600)
        merged = workspace.merge_session_projection(
            manifest,
            delta,
            prompt_reference=str(base["prompt_ref"]),
            expected_sha256=str(base["sha256"]),
            new_objective=False,
            operation_id="2" * 64,
            projection_sha256=file_sha256(delta),
            clock=lambda: datetime(2026, 8, 10, 0, 0, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(merged["prompt_id"], base["prompt_id"])
        self.assertIn("Also retain collision-safe", prompt.read_text(encoding="utf-8"))
        duplicate = workspace.merge_session_projection(
            manifest,
            delta,
            prompt_reference=str(base["prompt_ref"]),
            expected_sha256=str(base["sha256"]),
            new_objective=False,
            operation_id="3" * 64,
            projection_sha256=file_sha256(delta),
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["sha256"], merged["sha256"])
        other_delta = self.root / "other-delta.md"
        other_delta.write_text("Require a distinct second constraint.\n", encoding="utf-8")
        other_delta.chmod(0o600)
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.merge_session_projection(
                manifest,
                other_delta,
                prompt_reference=str(base["prompt_ref"]),
                expected_sha256=str(base["sha256"]),
                new_objective=False,
                operation_id="4" * 64,
                projection_sha256=file_sha256(other_delta),
            )
        self.assertEqual(caught.exception.code, "PROMPT_DRIFT")

        retried = workspace.merge_session_projection(
            manifest,
            delta,
            prompt_reference=str(base["prompt_ref"]),
            expected_sha256=str(base["sha256"]),
            new_objective=False,
            operation_id="2" * 64,
            projection_sha256=file_sha256(delta),
        )
        self.assertEqual(retried["sha256"], merged["sha256"])
        recreated = workspace.merge_session_projection(
            manifest,
            refined,
            prompt_reference=None,
            expected_sha256=None,
            new_objective=True,
            operation_id="1" * 64,
            projection_sha256=file_sha256(refined),
        )
        self.assertEqual(recreated["prompt_id"], created["prompt_id"])

    def test_session_projection_new_objective_recovers_after_create_interruption(
        self,
    ) -> None:
        manifest = Path(str(self.initialize()["workspace"]))
        prompt_root = manifest.parent / "prompts"
        refined = self.root / "interrupted-refinement.md"
        refined.write_text("Create exactly one crash-safe objective.\n", encoding="utf-8")
        refined.chmod(0o600)
        before = set(prompt_root.glob("*.md"))
        original_write = workspace.write_exclusive

        def interrupt_after_write(path: Path, data: bytes) -> None:
            original_write(path, data)
            raise KeyboardInterrupt("simulated process termination")

        with mock.patch.object(
            workspace,
            "write_exclusive",
            side_effect=interrupt_after_write,
        ):
            with self.assertRaises(KeyboardInterrupt):
                workspace.merge_session_projection(
                    manifest,
                    refined,
                    prompt_reference=None,
                    expected_sha256=None,
                    new_objective=True,
                    operation_id="4" * 64,
                    projection_sha256=file_sha256(refined),
                    clock=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
                )

        recovered = workspace.merge_session_projection(
            manifest,
            refined,
            prompt_reference=None,
            expected_sha256=None,
            new_objective=True,
            operation_id="4" * 64,
            projection_sha256=file_sha256(refined),
            clock=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        created_prompts = set(prompt_root.glob("*.md")) - before
        self.assertEqual(len(created_prompts), 1)
        self.assertEqual(
            Path(str(recovered["path"])).read_text(encoding="utf-8").count(
                "<!-- prompt-session-operation:v2:"
                f"{'4' * 64}:{file_sha256(refined)} -->"
            ),
            1,
        )

    def test_session_projection_rejects_reserved_operation_marker(self) -> None:
        manifest = Path(str(self.initialize()["workspace"]))
        refined = self.root / "reserved-marker.md"
        refined.write_text(
            f"Do not inject <!-- prompt-session-operation:{'5' * 64} -->.\n",
            encoding="utf-8",
        )
        refined.chmod(0o600)
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.merge_session_projection(
                manifest,
                refined,
                prompt_reference=None,
                expected_sha256=None,
                new_objective=True,
                operation_id="5" * 64,
                projection_sha256=file_sha256(refined),
            )
        self.assertEqual(caught.exception.code, "PROMPT_INPUT_INVALID")

    def test_session_projection_rejects_symlink_input(self) -> None:
        manifest = Path(str(self.initialize()["workspace"]))
        target = self.root / "projection-target.md"
        target.write_text("Require one durable constraint.\n", encoding="utf-8")
        target.chmod(0o600)
        projection = self.root / "projection-link.md"
        projection.symlink_to(target)

        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.merge_session_projection(
                manifest,
                projection,
                prompt_reference=None,
                expected_sha256=None,
                new_objective=True,
                operation_id="a" * 64,
                projection_sha256=file_sha256(target),
            )
        self.assertEqual(caught.exception.code, "PROMPT_PATH_INVALID")

    def test_session_projection_rejects_substitution_and_serializes_same_base(self) -> None:
        prompt = self.prompt_path()
        manifest = Path(str(self.initialize()["workspace"]))
        base = workspace.parse_prompt(prompt)
        first = self.root / "first-project-intent.md"
        second = self.root / "second-project-intent.md"
        first.write_text("Require the first durable constraint.\n", encoding="utf-8")
        second.write_text("Require the second durable constraint.\n", encoding="utf-8")
        first.chmod(0o600)
        second.chmod(0o600)
        before = prompt.read_bytes()
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.merge_session_projection(
                manifest,
                second,
                prompt_reference=str(base["prompt_ref"]),
                expected_sha256=str(base["sha256"]),
                new_objective=False,
                operation_id="7" * 64,
                projection_sha256=file_sha256(first),
            )
        self.assertEqual(caught.exception.code, "PROMPT_DRIFT")
        self.assertEqual(prompt.read_bytes(), before)

        def merge(path: Path, operation_id: str) -> tuple[str, object]:
            try:
                result = workspace.merge_session_projection(
                    manifest,
                    path,
                    prompt_reference=str(base["prompt_ref"]),
                    expected_sha256=str(base["sha256"]),
                    new_objective=False,
                    operation_id=operation_id,
                    projection_sha256=file_sha256(path),
                    clock=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
                )
                return "ok", result
            except workspace.PromptWorkspaceError as error:
                return error.code, error

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda item: merge(*item),
                    ((first, "8" * 64), (second, "9" * 64)),
                )
            )
        self.assertEqual(sorted(code for code, _ in results), ["PROMPT_DRIFT", "ok"])
        project_dir = manifest.parent
        self.assertFalse((project_dir / "active-run.json").exists())
        self.assertEqual(list(project_dir.glob("runs/*/run.json")), [])
        self.assertEqual(list(project_dir.glob("runs/*/execution/*/coordinator.json")), [])

    def test_verify_detects_active_binding_drift(self) -> None:
        prompt = self.prompt_path()
        result = self.intake(prompt)
        run_dir = Path(str(result["snapshot"])).parents[2]
        manifest = run_dir.parent / "workspace.json"
        verified = workspace.verify_command(manifest, prompt, run_dir.name)
        self.assertEqual(verified["action"], "verified")
        self.edit_prompt(prompt, "Unaccepted drift.")
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.verify_command(manifest, prompt, run_dir.name)
        self.assertEqual(caught.exception.code, "PROMPT_DRIFT")


if __name__ == "__main__":
    unittest.main()
