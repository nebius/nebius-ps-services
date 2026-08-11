#!/usr/bin/env python3
"""Capture-only UserPromptSubmit hook for explicitly bound workflow sessions."""

from __future__ import annotations

import json
import sys
from typing import Any

from prompt_session_state import PromptSessionError, evaluate_submit


def main() -> int:
    try:
        payload: Any = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise PromptSessionError(
                "PAYLOAD_INVALID", "hook payload must be an object"
            )
        output = evaluate_submit(payload)
    except PromptSessionError as error:
        output = {
            "continue": False,
            "stopReason": f"Prompt-session intake blocked ({error.code}): {error.message}",
        }
    except Exception:
        output = {
            "continue": False,
            "stopReason": "Prompt-session intake failed closed without persisting prompt content.",
        }
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
