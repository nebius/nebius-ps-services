#!/usr/bin/env python3
"""Non-blocking capture sidecar for direct workflow-session prompts."""

from __future__ import annotations

import json
import sys
from typing import Any

from prompt_session_state import PromptSessionError, evaluate_submit


def _capture_skipped(code: str) -> dict[str, Any]:
    safe_code = code if code.replace("_", "").isalnum() else "CAPTURE_ERROR"
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"Prompt-session capture was skipped ({safe_code}). Continue "
                "handling the direct user prompt normally; do not retry or replay "
                "it solely for capture."
            ),
        },
    }


def main() -> int:
    try:
        payload: Any = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise PromptSessionError(
                "PAYLOAD_INVALID", "hook payload must be an object"
            )
        output = evaluate_submit(payload)
    except PromptSessionError as error:
        output = _capture_skipped(error.code)
    except Exception:
        output = _capture_skipped("CAPTURE_ERROR")
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
