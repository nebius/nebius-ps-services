#!/usr/bin/env python3
"""Focused tests for capture-only prompt-session hook state."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


HOOK_DIR = Path(__file__).resolve().parents[1]
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))
import prompt_session_storage as storage  # noqa: E402

STATE_PATH = HOOK_DIR / "prompt_session_state.py"
SPEC = importlib.util.spec_from_file_location("prompt_session_state", STATE_PATH)
assert SPEC and SPEC.loader
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)
SUBMIT_SPEC = importlib.util.spec_from_file_location(
    "prompt_session_intake_hook", HOOK_DIR / "prompt_session_intake.py"
)
assert SUBMIT_SPEC and SUBMIT_SPEC.loader
submit_hook = importlib.util.module_from_spec(SUBMIT_SPEC)
SUBMIT_SPEC.loader.exec_module(submit_hook)
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

    def run_submit_hook(self, payload: object) -> dict[str, object]:
        process = subprocess.run(
            [sys.executable, str(HOOK_DIR / "prompt_session_intake.py")],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(process.stderr, "")
        result = json.loads(process.stdout)
        self.assertIsInstance(result, dict)
        return result

    def run_stop_hook(self, payload: object) -> dict[str, object]:
        process = subprocess.run(
            [sys.executable, str(HOOK_DIR / "stop_prompt_session_intake.py")],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(process.stderr, "")
        result = json.loads(process.stdout)
        self.assertIsInstance(result, dict)
        return result

    def test_unbound_or_missing_identity_creates_no_event(self) -> None:
        self.assertEqual(
            state.evaluate_submit({**self.base, "prompt": "Implement it"}, self.home),
            {},
        )
        self.assertFalse(self.event_path().exists())
        secret_turn = {**self.base, "turn_id": "unbound-secret"}
        with mock.patch.object(
            state, "contains_secret", return_value=True
        ) as secret_detector:
            self.assertEqual(
                state.evaluate_submit(
                    {**secret_turn, "prompt": "Handle this direct request."},
                    self.home,
                ),
                {},
            )
        secret_detector.assert_not_called()
        self.assertFalse(self.event_path("unbound-secret").exists())
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

    def test_bound_turn_stages_once_with_metadata_only_private_receipt(
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
        self.assertEqual(event["schema"], "prompt-session-intake/event-v2")
        self.assertEqual(
            event["submitted_sha256"],
            state.sha256_bytes(payload["prompt"].encode("utf-8")),
        )
        self.assertNotIn(payload["prompt"], event_path.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(event_path.stat().st_mode), 0o600)
        self.assertFalse((event_path.parent / "raw.md").exists())

    def test_same_turn_changed_digest_conflicts_without_replacing_receipt(self) -> None:
        self.bind()
        first = {**self.base, "turn_id": "same", "prompt": "First safe intent"}
        state.evaluate_submit(first, self.home)
        event = self.event_path("same")
        before = event.read_bytes()
        with self.assertRaisesRegex(state.PromptSessionError, "reused"):
            state.evaluate_submit(
                {**first, "prompt": "Different safe intent"}, self.home
            )
        self.assertEqual(event.read_bytes(), before)
        self.assertFalse((event.parent / "raw.md").exists())

    def test_event_v1_namespace_remains_inert_when_v2_stages(self) -> None:
        self.bind()
        root = state.state_root(self.home)
        legacy = (
            root
            / "sessions"
            / storage.session_key("session-1")
            / "events"
            / storage.turn_key("legacy-turn")
        )
        legacy.mkdir(parents=True, mode=0o700)
        legacy_event = legacy / "event.json"
        legacy_raw = legacy / "raw.md"
        legacy_event.write_text('{"schema":"prompt-session-intake/event-v1"}\n', encoding="utf-8")
        legacy_raw.write_text("legacy body must remain untouched\n", encoding="utf-8")
        legacy_event.chmod(0o600)
        legacy_raw.chmod(0o600)
        before_event = legacy_event.read_bytes()
        before_raw = legacy_raw.read_bytes()

        result = state.evaluate_submit(
            {
                **self.base,
                "turn_id": "legacy-turn",
                "prompt": "Stage only the v2 metadata receipt.",
            },
            self.home,
        )

        self.assertTrue(result["continue"])
        self.assertTrue(self.event_path("legacy-turn").exists())
        self.assertEqual(legacy_event.read_bytes(), before_event)
        self.assertEqual(legacy_raw.read_bytes(), before_raw)

    def test_secret_skips_capture_but_direct_prompt_continues(self) -> None:
        self.bind()
        secret_prompts = (
            "OPENAI_API_KEY=" + "sk-" + ("a" * 24),
            "NEBIUS_IAM_TOKEN=" + ("n" * 24),
            "KUBECONFIG=/tmp/example certificate-authority-data: private-material",
        )
        for index, secret_prompt in enumerate(secret_prompts):
            with self.subTest(index=index):
                turn_id = f"secret-{index}"
                result = state.evaluate_submit(
                    {
                        **self.base,
                        "turn_id": turn_id,
                        "prompt": secret_prompt,
                    },
                    self.home,
                )
                self.assertTrue(result["continue"])
                serialized = json.dumps(result)
                self.assertNotIn(secret_prompt, serialized)
                self.assertFalse(self.event_path(turn_id).parent.exists())

    def test_submit_wrapper_never_blocks_structured_capture_failure(self) -> None:
        self.bind()
        before = state.load_binding(state.state_root(self.home), "session-1")
        result = self.run_submit_hook(
            {
                **self.base,
                "codex_home": str(self.home),
                "prompt": "$sdlc-start workspace init",
            }
        )
        self.assertTrue(result["continue"])
        self.assertNotIn("stopReason", result)
        self.assertIn("BINDING_CONFLICT", json.dumps(result))
        self.assertEqual(
            state.load_binding(state.state_root(self.home), "session-1"), before
        )

    def test_submit_wrapper_never_blocks_unexpected_capture_failure(self) -> None:
        private_detail = "internal-detail-must-not-leak"
        output_stream = io.StringIO()
        with mock.patch.object(
            submit_hook,
            "evaluate_submit",
            side_effect=RuntimeError(private_detail),
        ), mock.patch.object(sys, "stdin", io.StringIO(json.dumps(self.base))), mock.patch.object(
            sys, "stdout", output_stream
        ):
            self.assertEqual(submit_hook.main(), 0)
        serialized = output_stream.getvalue()
        output = json.loads(serialized)
        self.assertTrue(output["continue"])
        self.assertNotIn("stopReason", output)
        self.assertNotIn(private_detail, serialized)

    def test_submit_wrapper_passes_secret_without_persistence(self) -> None:
        self.bind()
        secret_prompt = "OPENAI_API_KEY=" + "sk-" + ("a" * 24)
        result = self.run_submit_hook(
            {
                **self.base,
                "codex_home": str(self.home),
                "turn_id": "wrapper-secret",
                "prompt": secret_prompt,
            }
        )
        self.assertTrue(result["continue"])
        self.assertNotIn("stopReason", result)
        self.assertNotIn(secret_prompt, json.dumps(result))
        self.assertFalse(self.event_path("wrapper-secret").parent.exists())

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

    def test_prompt_capture_delegate_never_blocks_stop(self) -> None:
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
        self.assertEqual(result, {"continue": True})
        self.assertEqual(state._load_json(self.event_path("pending"))["phase"], "staged")

    def test_stop_wrapper_never_blocks_invalid_capture_state(self) -> None:
        self.bind()
        root = state.state_root(self.home)
        registry = root / "registry.json"
        registry.write_text("not-json\n", encoding="utf-8")
        registry.chmod(0o600)
        result = self.run_stop_hook(
            {
                "hook_event_name": "Stop",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "codex_home": str(self.home),
            }
        )
        self.assertEqual(result, {"continue": True})

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

    def test_submit_wrapper_passes_unsafe_state_without_persistence(self) -> None:
        self.bind()
        lock = state.state_root(self.home) / ".state.lock"
        alias = self.root / "wrapper-lock-alias"
        os.link(lock, alias)
        prompt = "Add one constraint that must not be replayed."
        result = self.run_submit_hook(
            {
                **self.base,
                "codex_home": str(self.home),
                "turn_id": "unsafe-wrapper",
                "prompt": prompt,
            }
        )
        self.assertTrue(result["continue"])
        self.assertNotIn("stopReason", result)
        self.assertNotIn(prompt, json.dumps(result))
        self.assertFalse(self.event_path("unsafe-wrapper").parent.exists())

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
