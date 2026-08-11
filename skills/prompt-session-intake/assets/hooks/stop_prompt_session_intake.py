#!/usr/bin/env python3
"""Shared-arbiter delegate for incomplete prompt-session transitions."""

from __future__ import annotations

import json
import sys
from typing import Any

from prompt_session_state import (
    PromptSessionError,
    codex_home,
    evaluate_stop,
    mark_stop_continuation,
)


MARK_CONTINUATION_FIELD = "_promptSessionMarkStopContinuation"


def main() -> int:
    try:
        payload: Any = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise PromptSessionError(
                "PAYLOAD_INVALID", "hook payload must be an object"
            )
        reason = payload.get(MARK_CONTINUATION_FIELD)
        if reason is not None:
            if not isinstance(reason, str) or not reason:
                raise PromptSessionError(
                    "CONTINUATION_INVALID", "Stop continuation reason is invalid"
                )
            mark_stop_continuation(codex_home(payload), payload, reason)
            output = {"continue": True}
        else:
            output = evaluate_stop(payload)
    except PromptSessionError as error:
        output = {
            "continue": False,
            "stopReason": f"Prompt-session Stop delegate blocked ({error.code}): {error.message}",
        }
    except Exception:
        output = {
            "continue": False,
            "stopReason": "Prompt-session Stop delegate failed closed.",
        }
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
