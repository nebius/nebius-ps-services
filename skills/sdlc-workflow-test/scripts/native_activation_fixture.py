"""Synthetic native records for unit tests only; never use in a live trial."""

import hashlib
import json
from pathlib import Path
import three_tier_native as native

SESSION = "native-fixture-session"


def seed(state, startup, skills_root):
    boundary = Path(state["run_root"])
    home = Path(state["private_root"]) / "codex-home"

    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(content)
        path.chmod(0o600)

    records = [
        {
            "type": "session_meta",
            "payload": {"id": SESSION, "source": "cli", "cli_version": "0.166.0"},
        },
        {
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "native-turn"},
        },
        {
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "outer-model-call", "input": "private contents discarded"},
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "thread_id": SESSION,
                "turn_id": "native-turn",
                "item": {
                    "type": "CommandExecution", "id": "native-call", "status": "completed",
                    "source": "unified_exec_startup", "exit_code": 0,
                    "command": "private contents discarded", "aggregated_output": "private contents discarded",
                },
            },
        },
    ]
    write(
        home / "sessions/rollout-fixture.jsonl",
        b"".join((json.dumps(r) + "\n").encode() for r in records),
    )
    event = {
        "event": "PreToolUse",
        "project_id": startup["project_id"],
        "run_id": startup["run_id"],
        "turn_id": "native-turn",
        "tool_use_id": "native-call",
        "decision": "allow",
    }
    write(
        home
        / "sdlc-runs"
        / startup["project_id"]
        / startup["run_id"]
        / "history/hook-events.jsonl",
        (json.dumps(event) + "\n").encode(),
    )
    write(
        home / "hooks/pre_tool_use_sdlc_policy.py",
        (
            skills_root / "sdlc-start/assets/hooks/pre_tool_use_sdlc_policy.py"
        ).read_bytes(),
    )
    for name in ("trusted_runtime.py", "git_environment.py", "agent_runtime.py"):
        write(home / "hooks" / name, (
            skills_root / "global-context-management/scripts" / name
        ).read_bytes())
    for name in ("__init__.py", "sdlc_state.py", "sdlc_policy.py"):
        write(home / "hooks/lib" / name, (
            skills_root / "sdlc-start/assets/hooks/lib" / name
        ).read_bytes())
    value = native.observe(state, skills_root=skills_root)
    write(boundary / native.RELATIVE, (json.dumps(value) + "\n").encode())
    state["native_activation"] = {
        "path": native.RELATIVE,
        "sha256": hashlib.sha256((boundary / native.RELATIVE).read_bytes()).hexdigest(),
    }
    return value
