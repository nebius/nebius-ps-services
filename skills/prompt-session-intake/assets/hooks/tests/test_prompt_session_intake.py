#!/usr/bin/env python3
"""Focused tests for capture-only prompt-session hook state."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


HOOK_DIR = Path(__file__).resolve().parents[1]
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))
import prompt_session_storage as storage  # noqa: E402

STATE_PATH = HOOK_DIR / "prompt_session_state.py"
SPEC = importlib.util.spec_from_file_location("prompt_session_state", STATE_PATH)
assert SPEC and SPEC.loader
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)
ARBITER_SPEC = importlib.util.spec_from_file_location(
    "prompt_session_stop_arbiter", HOOK_DIR / "stop_lifecycle_arbiter.py"
)
assert ARBITER_SPEC and ARBITER_SPEC.loader
arbiter = importlib.util.module_from_spec(ARBITER_SPEC)
ARBITER_SPEC.loader.exec_module(arbiter)


class PromptSessionHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="prompt-session-hook-")
        self.root = Path(self.temporary.name)
        self.home = self.root / "codex"
        self.home.mkdir(mode=0o700)
        self.project = self.root / "project"
        self.project.mkdir(mode=0o700)
        self.base = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(self.project),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def bind(self, workflow: str = "task-implementer") -> None:
        command = (
            "$task-implementer workspace init"
            if workflow == "task-implementer"
            else "$sdlc-start workspace init"
        )
        result = state.evaluate_submit({**self.base, "prompt": command}, self.home)
        self.assertTrue(result["continue"])
        self.assertIn(
            "bound this session", result["hookSpecificOutput"]["additionalContext"]
        )

    def event_path(self, turn: str = "turn-1") -> Path:
        return state._event_path(state.state_root(self.home), "session-1", turn)

    def test_unbound_or_missing_identity_creates_no_event(self) -> None:
        self.assertEqual(
            state.evaluate_submit({**self.base, "prompt": "Implement it"}, self.home),
            {},
        )
        self.assertFalse(self.event_path().exists())
        missing = {**self.base, "prompt": "Implement it"}
        missing.pop("session_id")
        self.assertEqual(state.evaluate_submit(missing, self.home), {})

    def test_binding_is_session_project_and_workflow_scoped(self) -> None:
        self.bind()
        repeated = state.evaluate_submit(
            {**self.base, "prompt": "$task-implementer run abcde"}, self.home
        )
        self.assertTrue(repeated["continue"])
        with self.assertRaisesRegex(
            state.PromptSessionError, "different project or workflow"
        ):
            state.evaluate_submit(
                {**self.base, "prompt": "$sdlc-start workspace init"}, self.home
            )

    def test_incomplete_or_extended_run_does_not_bind(self) -> None:
        for prompt in ("$task-implementer run", "$sdlc-start run abcde extra"):
            self.assertEqual(
                state.evaluate_submit({**self.base, "prompt": prompt}, self.home),
                {},
            )
        self.assertIsNone(state.load_binding(state.state_root(self.home), "session-1"))

    def test_bound_turn_stages_once_with_redacted_receipt_and_private_modes(
        self,
    ) -> None:
        self.bind()
        payload = {
            **self.base,
            "turn_id": "direct",
            "prompt": "Add validation and preserve every constraint.",
        }
        first = state.evaluate_submit(payload, self.home)
        second = state.evaluate_submit(payload, self.home)
        self.assertEqual(first, second)
        event_path = self.event_path("direct")
        event = json.loads(event_path.read_text(encoding="utf-8"))
        context = first["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn(payload["prompt"], context)
        self.assertEqual(event["phase"], "staged")
        self.assertEqual(stat.S_IMODE(event_path.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE((event_path.parent / "raw.md").stat().st_mode), 0o600
        )

    def test_same_turn_changed_digest_conflicts_without_replacing_raw(self) -> None:
        self.bind()
        first = {**self.base, "turn_id": "same", "prompt": "First safe intent"}
        state.evaluate_submit(first, self.home)
        raw = self.event_path("same").parent / "raw.md"
        before = raw.read_bytes()
        with self.assertRaisesRegex(state.PromptSessionError, "reused"):
            state.evaluate_submit(
                {**first, "prompt": "Different safe intent"}, self.home
            )
        self.assertEqual(raw.read_bytes(), before)

    def test_secret_is_rejected_before_raw_or_event_write(self) -> None:
        self.bind()
        result = state.evaluate_submit(
            {
                **self.base,
                "turn_id": "secret",
                "prompt": "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuv",
            },
            self.home,
        )
        self.assertFalse(result["continue"])
        self.assertNotIn("sk-", result["stopReason"])
        self.assertFalse(self.event_path("secret").parent.exists())

    def test_stop_compaction_and_subagent_turns_do_not_stage(self) -> None:
        self.bind()
        for index, extra in enumerate(
            (
                {"stop_hook_active": True},
                {"prompt_source": "compaction"},
                {"is_subagent": True},
            )
        ):
            payload = {
                **self.base,
                "turn_id": f"excluded-{index}",
                "prompt": "Continue the workflow",
                **extra,
            }
            self.assertEqual(state.evaluate_submit(payload, self.home), {})
            self.assertFalse(self.event_path(f"excluded-{index}").exists())

    def test_exact_marked_stop_continuation_is_excluded_once(self) -> None:
        self.bind()
        reason = "Complete the current prompt-session transition."
        state.mark_stop_continuation(
            self.home,
            {
                "hook_event_name": "Stop",
                "session_id": "session-1",
                "turn_id": "source-turn",
            },
            reason,
        )
        excluded = state.evaluate_submit(
            {**self.base, "turn_id": "continued", "prompt": reason}, self.home
        )
        self.assertIn(
            "excluded this exact shared Stop continuation",
            excluded["hookSpecificOutput"]["additionalContext"],
        )
        self.assertFalse(self.event_path("continued").exists())
        staged = state.evaluate_submit(
            {**self.base, "turn_id": "real-user", "prompt": reason}, self.home
        )
        self.assertIn("staged", staged["hookSpecificOutput"]["additionalContext"])

    def test_shared_arbiter_marks_its_exact_continuation_prompt(self) -> None:
        self.bind()
        state.evaluate_submit(
            {**self.base, "turn_id": "pending", "prompt": "Apply one constraint."},
            self.home,
        )
        result = arbiter.evaluate(
            {
                "hook_event_name": "Stop",
                "session_id": "session-1",
                "turn_id": "pending",
                "codex_home": str(self.home),
            },
            HOOK_DIR,
        )
        self.assertEqual(result["decision"], "block")
        continued = state.evaluate_submit(
            {
                **self.base,
                "turn_id": "stop-continuation",
                "prompt": result["reason"],
            },
            self.home,
        )
        self.assertIn(
            "excluded this exact shared Stop continuation",
            continued["hookSpecificOutput"]["additionalContext"],
        )
        self.assertFalse(self.event_path("stop-continuation").exists())

    def test_concurrent_duplicate_delivery_creates_one_record(self) -> None:
        self.bind()
        payload = {
            **self.base,
            "turn_id": "concurrent",
            "prompt": "Apply one safe steering update.",
        }
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(
                executor.map(
                    lambda _: state.evaluate_submit(payload, self.home), range(6)
                )
            )
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(
            len(list(self.event_path("concurrent").parent.glob("event.json"))), 1
        )

    def test_concurrent_first_root_bootstrap_is_safe(self) -> None:
        payload = {**self.base, "turn_id": "bootstrap", "prompt": "Unbound prompt"}
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(
                executor.map(
                    lambda _: state.evaluate_submit(payload, self.home), range(6)
                )
            )
        self.assertEqual(results, [{}] * 6)
        marker = state.state_root(self.home) / ".root.json"
        self.assertEqual(
            json.loads(marker.read_text(encoding="utf-8"))["schema"],
            storage.ROOT_SCHEMA,
        )

    def test_hard_linked_state_lock_fails_closed(self) -> None:
        self.bind()
        lock = state.state_root(self.home) / ".state.lock"
        alias = self.root / "lock-alias"
        os.link(lock, alias)
        with self.assertRaisesRegex(state.PromptSessionError, "state lock is unsafe"):
            state.evaluate_submit(
                {
                    **self.base,
                    "turn_id": "unsafe-lock",
                    "prompt": "Add one constraint.",
                },
                self.home,
            )

    def test_bound_sdlc_capture_never_creates_sdlc_run_state(self) -> None:
        self.bind("agentic-sdlc")
        state.evaluate_submit(
            {
                **self.base,
                "turn_id": "sdlc-direct",
                "prompt": "Tighten the acceptance criteria.",
            },
            self.home,
        )
        self.assertTrue(self.event_path("sdlc-direct").exists())
        self.assertFalse((self.home / "sdlc-runs").exists())


if __name__ == "__main__":
    unittest.main()
